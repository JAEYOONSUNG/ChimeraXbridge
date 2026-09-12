"""Native Qt integration for candidate comparison, report export and preview controls."""
import csv
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from Qt.QtCore import Qt, QPoint, QPointF
from Qt.QtGui import QWheelEvent
from Qt.QtWidgets import QApplication, QFileDialog
from PyQt6.QtTest import QTest
from chimerax.atomic import AtomicStructure

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("chimerax.codex_bridge", root / "src/__init__.py",
    submodule_search_locations=[str(root / "src")])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
from chimerax.codex_bridge.quick_results import QuickResults
from chimerax.codex_bridge.quick_context import capture_context
from chimerax.codex_bridge import quick_reports

app = QApplication.instance()
QTest.qWait(5500)
model = AtomicStructure(session, name="Comparison fixture")
for number in range(1, 4):
    residue = model.new_residue("ALA", "A", number)
    atom = model.new_atom("CA", "C")
    residue.add_atom(atom)
    atom.coord = (number * 3.8, 0, 0)
session.models.add([model])
model.atoms[0].selected = True
context = capture_context(session, model.atomspec)
result = {"title": "Cavity comparison", "action": "cavity",
          "summary": ["Original computation"], "details": ["Geometric candidates, not affinity."] * 50,
          "metrics": [{"label": "Candidates", "value": "5"}, {"label": "Volume", "value": "2 Å³"},
                      {"label": "Max depth", "value": "1 Å"}], "candidates": []}
for index, (volume, contacts, depth) in enumerate(((2.123456789, 2, 1.2), (10.987654321, 10, 2.1),
                                                (1.5, 1, 0), (None, None, None), (0, 0, 0))):
    result["candidates"].append({"id": f"{model.atomspec}:cavity:K{index}", "model_spec": model.atomspec,
        "label": f"Candidate {index} <native>", "kind": "geometry", "volume": volume,
        "contact_residues": contacts, "max_depth": depth, "specs": [model.residues[index % 3].atomspec],
        "evidence": ["LYS55 contact" if index == 2 else f"Measured fixture {index}"]})
job = SimpleNamespace(action="cavity", context=context, result=result, status="done", candidate=0,
                      elapsed=.25, cached=False)
panel = QuickResults.get_singleton(session)
panel.job = job


class Controller:
    def __init__(self):
        self.starts = []
        self.previews = []
        self.undo_states = [object()]
        self.overlay_visible = True
        self.latest = job

    def start(self, action, model_hint=None, *, force=False):
        self.starts.append((action, model_hint, force))

    def apply_candidate(self, index):
        self.previews.append(index)
        job.candidate = index
        panel.show_candidate_evidence(result, index)

    def set_overlay_visibility(self, visible):
        self.overlay_visible = bool(visible)

    def has_preview_overlays(self):
        return job.candidate >= 0

    def cancel_active(self):
        pass


control = Controller()
panel.controller = control
panel.show_result(job)
table = panel.candidates
assert table.count() == 5 and table.currentRow() == 0
assert table.item(3).text(3) == "—" and table.item(4).text(3) == "0"
assert not control.previews, "Initial display changed the scene again"


def order():
    return [table.row(table.topLevelItem(i)) for i in range(table.count())]


table.sortItems(3, Qt.SortOrder.AscendingOrder)
assert order() == [4, 2, 0, 1, 3], order()
table.sortItems(3, Qt.SortOrder.DescendingOrder)
assert order() == [1, 0, 2, 4, 3], order()
table.setCurrentItem(table.item(1))
assert control.previews[-1] == 1 and job.candidate == 1
assert "11 Å³" in panel.summary.toPlainText()
assert "Candidate 1 <native>" in panel.summary.toPlainText()
before_filter = list(control.previews)
panel.candidate_filter.setText("lys55")
assert [table.row(item) for item in table.visible_items()] == [2]
assert "1/5" in panel.candidate_label.text()
assert control.previews == before_filter, "Filtering silently applied a candidate"
panel.candidate_filter.clear()
panel.next_button.click()
assert control.previews[-1] == 0, "Next used original rank rather than displayed sort order"
panel.previous_button.click()
assert control.previews[-1] == 1
table.setCurrentRow(3)
assert control.previews[-1] == 1, "Programmatic Undo selection triggered another apply"
panel.next_button.click()
assert control.previews[-1] == 1, "Next did not wrap over visible sorted candidates"
assert control.starts == [], "Candidate comparison recomputed data"

# Explicit targets remain explicit; fresh calculations bypass the cache.
panel.rerun_button.click()
assert control.starts[-1] == ("cavity", model.atomspec, False)
panel.recalculate_action.trigger()
assert control.starts[-1] == ("cavity", model.atomspec, True)
auto_index = panel.target.findData(None)
assert auto_index >= 0
panel.target.setCurrentIndex(auto_index)
assert control.starts[-1] == ("cavity", None, False)
panel.overlay_check.setChecked(False)
assert control.overlay_visible is False
panel.overlay_check.setChecked(True)
assert control.overlay_visible is True

# Saving uses original candidate IDs/measurements, not filtered/sorted row order.
job.candidate = 1
panel.candidate_filter.setText("lys55")
with tempfile.TemporaryDirectory(prefix="quick-report-ui-") as temporary:
    folder = Path(temporary)
    for fmt, suffix in (("csv", ".csv"), ("json", ".json"), ("markdown", ".md")):
        path = folder / ("측정 결과" + suffix)
        with patch.object(QFileDialog, "getSaveFileName", return_value=(str(path), "")):
            panel._save_report(fmt)
        assert path.exists(), panel.status.toolTip()
        if fmt == "json":
            saved = json.loads(path.read_text())
            assert len(saved["candidates"]) == 5
            assert saved["preview"]["original_index"] == 1
            assert saved["candidates"][1]["data"]["volume"] == 10.987654321
            assert saved["candidates"][3]["data"]["volume"] is None
            assert saved["candidates"][4]["data"]["volume"] == 0
            assert saved["provenance"]["input_signature"] == context["snapshot"]["signature"]
        elif fmt == "csv":
            with path.open(newline="") as handle:
                assert len(list(csv.DictReader(handle))) == 5
        else:
            assert "10.987654321" in path.read_text()
    job.candidate = -1
    with patch.object(QFileDialog, "getSaveFileName", return_value=(str(folder / "no-preview.json"), "")):
        panel._save_report("json")
    undone = json.loads((folder / "no-preview.json").read_text())
    assert undone["preview"] is None and not any(c["is_current_preview"] for c in undone["candidates"])
    job.candidate = 1
    # A dialog's nested event loop may deliver another completed action. Save
    # the result/preview the user requested, not a newly displayed target.
    def result_arrives_during_dialog(*args, **kwargs):
        panel.job = SimpleNamespace(action="view", result={"title": "Other result", "candidates": []},
                                    context=context, candidate=0, elapsed=.1, cached=False, status="done")
        job.candidate = 2
        return str(folder / "captured-intent.json"), ""
    with patch.object(QFileDialog, "getSaveFileName", side_effect=result_arrives_during_dialog):
        panel._save_report("json")
    intent = json.loads((folder / "captured-intent.json").read_text())
    assert intent["action"] == "cavity" and intent["preview"]["original_index"] == 1
    panel.job = job
    job.candidate = 1
    with patch.object(QFileDialog, "getSaveFileName", return_value=("", "")), \
            patch.object(quick_reports, "save_report", side_effect=AssertionError("Cancel wrote data")):
        panel._save_report("json")
    with patch.object(QFileDialog, "getSaveFileName", return_value=(str(folder / "failure.json"), "")), \
            patch.object(quick_reports, "save_report", side_effect=OSError("fixture write failure")):
        panel._save_report("json")
    assert "fixture write failure" in panel.status.toolTip() and panel.report_button.isEnabled()
    job.status = "stale"
    panel.show_stale("Structure changed; recalculate.")
    assert not table.isEnabled() and not panel.overlay_check.isEnabled()
    assert panel.report_button.isEnabled()
    with patch.object(QFileDialog, "getSaveFileName", return_value=(str(folder / "stale.json"), "")):
        panel._save_report("json")
    assert json.loads((folder / "stale.json").read_text())["provenance"]["stale"] is True

job.status = "done"
panel.show_result(job)
host = panel.tool_window.ui_area
old_parent = host.parentWidget()
host.setParent(None)
host.show()
screens = []
for width, height in ((380, 500), (540, 500), (360, 280)):
    host.resize(width, height)
    QTest.qWait(30)
    assert host.width() == width and host.height() == height
    assert panel.scroll.horizontalScrollBar().maximum() == 0
    assert table.horizontalScrollBar().maximum() == 0
    panel.scroll.verticalScrollBar().setValue(0)
    path = f"/tmp/quick-refinement-{width}x{height}.png"
    host.grab().save(path)
    screens.append(path)
    panel.scroll.verticalScrollBar().setValue(panel.scroll.verticalScrollBar().maximum())
    QTest.qWait(20)
    for button in (panel.copy_button, panel.report_button, panel.export_button):
        point = button.mapTo(panel.scroll.viewport(), QPoint())
        assert 0 <= point.y() and point.y() + button.height() <= panel.scroll.viewport().height()

# Trackpads can scroll long evidence without being trapped by the outer panel.
panel.details_toggle.setChecked(True)
panel.scroll.ensureWidgetVisible(panel.details)
QTest.qWait(20)
inner = panel.details.verticalScrollBar()
assert inner.maximum() > 0
inner.setValue(0)
receiver = panel.details.viewport()
position = QPointF(receiver.rect().center())
event = QWheelEvent(position, QPointF(receiver.mapToGlobal(position.toPoint())), QPoint(0, -40), QPoint(),
                    Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
app.sendEvent(receiver, event)
assert inner.value() > 0, "Evidence text ignored pixel scrolling"
host.setParent(old_parent)
old_parent.layout().insertWidget(0, host)
host.show()

report = {"ok": True, "numeric_sort_missing_last": True, "filter_without_recompute": True,
          "original_candidate_indices": True, "explicit_target_and_force": True,
          "csv_json_markdown": True, "full_precision_and_provenance": True,
          "stale_export_marked": True, "compact_layout": True, "nested_evidence_scroll": True,
          "screenshots": screens}
Path("/tmp/quick-refinement-report.json").write_text(json.dumps(report, indent=2))
print("QUICK_REFINEMENT_OK", json.dumps(report))
