"""Preview/filter clarity and comparison continuity without a visible window."""
import hashlib
import importlib.util
import json
from pathlib import Path
import runpy
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from Qt.QtCore import QPoint, Qt
from Qt.QtWidgets import QFileDialog
from PyQt6 import sip
from PyQt6.QtTest import QTest
from chimerax.atomic import AtomicStructure

root = Path(__file__).resolve().parents[1]
app = runpy.run_path(str(root / "scripts/headless_ui_fixture.py"))["install"](session)
spec = importlib.util.spec_from_file_location("chimerax.codex_bridge", root / "src/__init__.py",
    submodule_search_locations=[str(root / "src")])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
from chimerax.codex_bridge.quick_context import capture_context
from chimerax.codex_bridge.quick_results import QuickResults

exceptions = []
original_hook = sys.excepthook
sys.excepthook = lambda kind, value, tb: exceptions.append(f"{kind.__name__}: {value}")


def make_model(name):
    model = AtomicStructure(session, name=name)
    for number in range(1, 4):
        residue = model.new_residue("ALA", "A", number)
        atom = model.new_atom("CA", "C")
        residue.add_atom(atom)
        atom.coord = (3.8 * number, 0, 0)
    session.models.add([model])
    return model


def make_job(model, action="cavity", *, candidate=0, status="done"):
    result = {"title": "Comparison fixture", "action": action, "metrics": [],
        "summary": ["Original calculation"], "details": ["Computed evidence, not affinity."],
        "candidates": [
            {"id": "first-original-id", "label": "ATP contact", "kind": "geometry",
             "model_spec": model.atomspec, "volume": 12.123456789, "evidence": ["LYS55"]},
            {"id": "second-original-id", "label": "ADP contact", "kind": "geometry",
             "model_spec": model.atomspec, "volume": 400.5, "evidence": ["ARG33"]},
            {"id": "third-original-id", "label": "Water cavity", "kind": "geometry",
             "model_spec": model.atomspec, "volume": None, "evidence": ["No lining residues"]}]}
    return SimpleNamespace(action=action, context=capture_context(session, model.atomspec),
        result=result, status=status, candidate=candidate, elapsed=.25, cached=False)


class Controller:
    def __init__(self):
        self.previews = []
        self.starts = []
        self.undo_states = []
        self.overlay_visible = True
        self.panel = None

    def apply_candidate(self, index):
        self.previews.append(index)
        self.panel.job.candidate = index
        self.panel.show_candidate_evidence(self.panel.job.result, index)

    def start(self, *args, **kwargs):
        self.starts.append((args, kwargs))

    def has_preview_overlays(self):
        return False

    def cancel_active(self):
        pass


def open_panel():
    panel = QuickResults(session, "Quick Results")
    panel.controller = control
    control.panel = panel
    return panel


def destroy(panel):
    dock = panel.tool_window._dock_widget
    panel.delete()
    sip.delete(dock)


def order(table):
    return [table.row(table.topLevelItem(i)) for i in range(table.count())]


def scene(model):
    digest = hashlib.sha256()
    for data in (model.atoms.coords, model.atoms.colors, model.atoms.selected,
                 model.atoms.displays, model.position.matrix, session.main_view.camera.position.matrix):
        digest.update(data.tobytes())
    return digest.hexdigest()


model = make_model("Comparison target")
other = make_model("Other target")
control = Controller()
panel = open_panel()
clipboard = app.clipboard()
clipboard.setText("Existing clipboard content")
assert not panel.copy_button.isEnabled() and not panel.rerun_button.isEnabled()
panel.copy_button.click()
panel._copy_report()
panel.rerun_button.click()
assert clipboard.text() == "Existing clipboard content" and not control.starts
panel.show_error("No structure is open.")
assert not panel.rerun_button.isEnabled()
job = make_job(model)
before = scene(model)
panel.show_result(job)
assert panel.copy_button.isEnabled() and panel.rerun_button.isEnabled()
panel.copy_button.click()
assert "Comparison fixture" in clipboard.text() and "Water cavity" in clipboard.text()
assert not control.previews and not control.starts
panel.candidates.sortItems(3, Qt.SortOrder.DescendingOrder)
assert order(panel.candidates) == [1, 0, 2]
panel.candidate_filter.setText("ARG33")
assert [panel.candidates.row(item) for item in panel.candidates.visible_items()] == [1]
assert "1/3" in panel.candidate_label.toolTip()
assert "preview #1 filtered out" in panel.candidate_label.toolTip()
assert not control.previews, "Filtering changed the preview"
assert panel.next_button.isEnabled() and panel.previous_button.isEnabled()
panel.next_button.click()
assert control.previews == [1] and job.candidate == 1
assert "preview #2" in panel.candidate_label.toolTip()
assert "filtered out" not in panel.candidate_label.toolTip()
assert not panel.next_button.isEnabled(), "The only visible candidate is already the preview"

panel.candidate_filter.setText("NO MATCH")
assert "No matches" in panel.candidate_label.toolTip()
assert "preview #2 filtered out" in panel.candidate_label.toolTip()
assert not panel.next_button.isEnabled() and not panel.previous_button.isEnabled()
assert panel.candidate_filter.isClearButtonEnabled()
panel.candidate_filter.clear()
assert "3/3" in panel.candidate_label.toolTip()
assert control.previews == [1]

# Undo can leave no active preview. A single filtered candidate remains reachable.
job.candidate = -1
panel.candidates.setCurrentRow(-1)
panel.show_candidate_evidence(job.result, -1)
panel.candidate_filter.setText("LYS55")
assert "no preview" in panel.candidate_label.toolTip()
assert panel.next_button.isEnabled()
panel.previous_button.click()
assert control.previews == [1, 0]
assert "preview #1" in panel.candidate_label.toolTip()

# Running again preserves comparison choices but honors the new calculation's preview.
panel.candidate_filter.setText("ARG33")
rerun = make_job(model, candidate=0, status="running")
panel.show_running(rerun)
clipboard_before_running = clipboard.text()
panel._copy_report()
assert clipboard.text() == clipboard_before_running and not panel.rerun_button.isEnabled()
assert panel.candidate_filter.text() == "ARG33"
rerun.status = "done"
panel.show_result(rerun)
assert panel.candidate_filter.text() == "ARG33" and order(panel.candidates) == [1, 0, 2]
assert rerun.candidate == 0 and panel.candidates.currentRow() == 0
assert "preview #1 filtered out" in panel.candidate_label.toolTip()
assert control.previews == [1, 0], "Restoring comparison state applied a preview"

# Reports explicitly describe their all-candidate scope and retain original IDs.
assert "all candidates" in panel.copy_button.toolTip()
assert "all candidates" in panel.report_button.toolTip()
with tempfile.TemporaryDirectory(prefix="usability-results-") as temporary:
    path = Path(temporary) / "comparison.json"
    with patch.object(QFileDialog, "getSaveFileName", return_value=(str(path), "")):
        panel._save_report("json")
    report = json.loads(path.read_text(encoding="utf-8"))
    assert len(report["candidates"]) == 3
    assert report["preview"]["original_index"] == 0
    assert [item["data"]["id"] for item in report["candidates"]] == [
        "first-original-id", "second-original-id", "third-original-id"]
    assert report["candidates"][0]["data"]["volume"] == 12.123456789

# Closing and reopening retains UI choices without retaining live widget objects.
destroy(panel)
panel = open_panel()
panel.show_result(rerun)
assert panel.candidate_filter.text() == "ARG33"
assert order(panel.candidates) == [1, 0, 2] and panel.candidates.currentRow() == 0
assert control.previews == [1, 0] and not control.starts

# A fresh result with only one candidate must still expose its retained filter's clear button.
single = make_job(model)
single.result["candidates"] = single.result["candidates"][:1]
panel.show_result(single)
assert not panel.candidate_controls.isHidden()
assert "No matches" in panel.candidate_label.toolTip()
panel.candidate_filter.clear()
assert len(panel.candidates.visible_items()) == 1
panel.candidate_filter.setText("ARG33")
empty = make_job(model)
empty.result["candidates"] = []
panel.show_result(empty)
assert not panel.candidate_label.isHidden() and not panel.candidate_controls.isHidden()
assert "No candidates" in panel.candidate_label.toolTip()
assert not panel.next_button.isEnabled()

# Different actions and actual targets reset the last comparison to rank order.
for changed in (make_job(other), make_job(other, action="pocket")):
    panel.candidate_filter.setText("ARG33")
    panel.candidates.sortItems(3, Qt.SortOrder.DescendingOrder)
    panel.show_result(changed)
    assert panel.candidate_filter.text() == "" and order(panel.candidates) == [0, 1, 2]

panel.show_result(make_job(model))
panel.candidate_filter.setText("ARG33")
panel.candidates.sortItems(3, Qt.SortOrder.DescendingOrder)
assert scene(model) == before, "Comparison controls changed scene/model/selection data"
original_id = model.id
session.models.close([model])
replacement = make_model("Comparison target")
assert replacement.id == original_id, "Fixture did not exercise native model ID reuse"
panel.show_result(make_job(replacement))
assert panel.candidate_filter.text() == "" and order(panel.candidates) == [0, 1, 2]

# The existing header row and footer remain reachable at narrow, short sizes.
panel.candidate_filter.setText("ARG33")
host = panel.tool_window.ui_area
host.setParent(None)
host.show()
screens = []
for width, height in ((360, 150), (360, 280), (440, 500)):
    host.resize(width, height)
    QTest.qWait(30)
    assert (host.width(), host.height()) == (width, height)
    assert panel.scroll.horizontalScrollBar().maximum() == 0
    assert panel.candidates.horizontalScrollBar().maximum() == 0
    assert panel.candidate_label.width() > 0
    panel.scroll.ensureWidgetVisible(panel.candidate_label)
    path = f"/tmp/usability-results-{width}x{height}.png"
    host.grab().save(path)
    screens.append(path)
    panel.scroll.verticalScrollBar().setValue(panel.scroll.verticalScrollBar().maximum())
    QTest.qWait(10)
    for button in (panel.copy_button, panel.report_button, panel.export_button):
        position = button.mapTo(panel.scroll.viewport(), QPoint())
        assert 0 <= position.y() and position.y() + button.height() <= panel.scroll.viewport().height()

assert control.previews == [1, 0] and not control.starts
assert not exceptions, exceptions
sys.excepthook = original_hook
output = {"ok": True, "filter_keeps_preview": True, "single_match_navigation": True,
    "explicit_empty_and_undo_states": True, "same_target_comparison_continuity": True,
    "new_result_preview_authoritative": True, "replacement_model_resets": True,
    "full_report_scope": True, "compact_scroll": True, "visible_windows": False,
    "screenshots": screens}
Path("/tmp/usability-results-report.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
print("USABILITY_RESULTS_OK", json.dumps(output))
