"""Offscreen sequence quality checks; never create a native ChimeraX window."""
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

assert os.environ.get("QT_QPA_PLATFORM") == "offscreen"
assert not session.ui.is_gui

from Qt.QtWidgets import QApplication, QMainWindow, QWidget

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "chimerax.codex_bridge", root / "src/__init__.py",
    submodule_search_locations=[str(root / "src")])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
from chimerax.codex_bridge.sequence_bar import (
    ClickableSequenceText, ClickableChainOverviewText, CodexSequenceBar,
)

app = QApplication.instance() or QApplication([])
assert app.platformName() == "offscreen"
style = {"rgba": (120, 140, 160, 255), "letter": "A",
         "polymer_kind": "nucleic", "residue_name": "A"}


class IndexedOnlyStyles:
    """Model a large sequence without permitting an all-residue paint copy."""
    def __init__(self, length):
        self.length = length
        self.reads = []

    def __len__(self):
        return self.length

    def __getitem__(self, index):
        assert isinstance(index, int), "Painting copied the entire sequence style list"
        assert 0 <= index < self.length
        self.reads.append(index)
        return style


single = ClickableSequenceText(lambda *args: None, lambda *args: None)
single.set_sequence_text(["", "A" * 10000 + "   Zn:1"], 10000, [], [])
single.resize(450, 100)
single.show()
single.set_residue_coloring(bases=True)
app.processEvents()
indexed = IndexedOnlyStyles(10000)
single._residue_styles = indexed
for target in (0, single.horizontalScrollBar().maximum() // 2,
               single.horizontalScrollBar().maximum()):
    single.horizontalScrollBar().setValue(target)
    # Call the actual paint geometry directly so no queued paint can hide errors.
    indexed.reads.clear()
    single._residue_border_rects()
    assert len(indexed.reads) < 100, len(indexed.reads)
    assert indexed.reads and max(indexed.reads) < 10000, "Metal suffix acquired a base border"
single._residue_styles = []


class CountedMap(dict):
    def __init__(self, source):
        super().__init__(source)
        self.lookups = 0

    def get(self, key, default=None):
        self.lookups += 1
        return super().get(key, default)


overview = ClickableChainOverviewText(lambda *args: None, lambda *args: None, lambda *args: None)
overview.set_chain_rows([
    {"spec": f"#99/{index}", "sequence": "A" * 100, "length": 100,
     "residue_styles": [style] * 100}
    for index in range(250)
])
overview.resize(450, 130)
overview.show()
overview.set_residue_coloring(bases=True)
app.processEvents()
rows = CountedMap(overview._alignment_row_lines)
overview._alignment_row_lines = rows
styles = CountedMap(overview._alignment_styles)
overview._alignment_styles = styles
for target in (0, overview.verticalScrollBar().maximum() // 2,
               overview.verticalScrollBar().maximum()):
    overview.verticalScrollBar().setValue(target)
    rows.lookups = styles.lookups = 0
    assert overview._residue_guide_lines()
    assert overview._residue_border_rects()
    assert rows.lookups < 20, (target, rows.lookups, "Painting inspected offscreen rows")
    assert styles.lookups < 20, (target, styles.lookups)

# Check the actual header widgets at common graphics-view widths, without a
# native MainToolWindow, toolbar, graphics canvas, or saved user settings.
old_settings = getattr(session, "_codex_sequence_color_settings", None)
session._codex_sequence_color_settings = SimpleNamespace(
    aa_charge=True, nucleotides=True, base_palette="muted")
old_window = getattr(session.ui, "main_window", None)
window = QMainWindow()
window.main_view = QWidget(window)
session.ui.main_window = window
bar = CodexSequenceBar(session, "Sequence Bar")
widget = bar.bar_widget
widget.setParent(None)
widget.show()
controls = (bar.chain_combo, bar.selection_button, bar.search_edit,
            bar.search_prev_button, bar.search_next_button,
            bar.refresh_button, bar.similar_button)
all_controls = controls + (bar.all_chains_button, bar.charge_colors_button,
                           bar.base_colors_button, bar.color_key_button,
                           bar.panel_layout_button)
widths = []
for width in (900, 1400, 2048):
    widget.resize(width, widget.sizeHint().height())
    app.processEvents()
    message = "Current sequence status with a long model name and selected range · " * 20
    bar.status_label.setText(message)
    widget.layout().activate()
    assert widget.width() == width, (width, widget.width())
    assert all(left.geometry().right() < right.x() for left, right in zip(controls, controls[1:]))
    assert bar.similar_button.geometry().right() < width
    assert bar.panel_layout_button.geometry().right() < width
    assert {control.height() for control in all_controls} == {30}
    assert {control.font().pixelSize() for control in all_controls} == {13}
    assert len({control.font().weight() for control in all_controls}) == 1
    assert all(control.fontMetrics().height() <= control.contentsRect().height()
               for control in all_controls)
    assert bar.status_label.toolTip() == message and bar.status_label.text() != message
    assert bar.chain_combo.width() >= 210 and bar.search_edit.width() >= 220
    widths.append((bar.chain_combo.width(), bar.search_edit.width()))
assert all(right[0] > left[0] and right[1] > left[1]
           for left, right in zip(widths, widths[1:]))

bar.search_edit.setText("ACGT")
for width in (600, 700, 800, 900, 600, 2048):
    widget.resize(width, 320)
    app.processEvents()
    widget.layout().activate()
    assert widget.width() == width, (width, widget.width(), "Header forced the view wider")
    assert all(control.isVisible() for control in all_controls)
    assert all(0 <= control.mapTo(widget, control.rect().topLeft()).x()
               and control.mapTo(widget, control.rect().bottomRight()).x() < width
               and control.mapTo(widget, control.rect().bottomRight()).y() < widget.height()
               for control in all_controls), width
    assert {control.height() for control in all_controls} == {30}
    assert {control.font().pixelSize() for control in all_controls} == {13}
    assert bar.search_edit.text() == "ACGT" and bar.base_colors_button.isChecked()
    chain_y = bar.chain_combo.mapTo(widget, bar.chain_combo.rect().topLeft()).y()
    search_y = bar.search_edit.mapTo(widget, bar.search_edit.rect().topLeft()).y()
    assert (search_y > chain_y) == (width < 840)
    # A tall sequence stays inside the original cap when the header wraps.
    previous_entries = bar._entries
    bar._entries = [None] * 30
    bar._fit_sequence_view_heights()
    widget.layout().activate()
    view = bar.all_chains_text
    assert view.mapTo(widget, view.rect().bottomRight()).y() < widget.height(), width
    if width in (600, 800, 900):
        assert widget.grab().save(f"/tmp/quality-sequence-header-{width}.png")
    bar._entries = previous_entries
    bar._fit_sequence_view_heights()

bar.delete()
session.ui.main_window = old_window
if old_settings is None:
    del session._codex_sequence_color_settings
else:
    session._codex_sequence_color_settings = old_settings
single.close()
overview.close()
window.close()
report = {"ok": True, "platform": app.platformName(), "header_widths": [600, 700, 800, 900, 1400, 2048],
          "viewport_bounded_style_access": True, "offscreen_rows_skipped": True,
          "uniform_control_height": 30, "uniform_header_font": 13}
print("SEQUENCE_QUALITY_OK", json.dumps(report))
