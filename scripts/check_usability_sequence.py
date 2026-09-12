"""Sequence search usability and scientific-state checks, strictly offscreen."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

assert os.environ.get("QT_QPA_PLATFORM") == "offscreen"
assert not session.ui.is_gui

from Qt.QtCore import QPoint, Qt
from Qt.QtGui import QColor
from PyQt6.QtTest import QTest
from Qt.QtWidgets import QApplication, QMainWindow, QToolButton, QWidget
from chimerax.atomic import AtomicStructure, check_for_changes, selected_atoms

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "chimerax.codex_bridge", root / "src/__init__.py",
    submodule_search_locations=[str(root / "src")])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
from chimerax.codex_bridge import sequence_bar as module

app = QApplication.instance() or QApplication([])
assert app.platformName() == "offscreen"
model = AtomicStructure(session, name="Sequence search usability fixture")
for chain_id, sequence in (("A", "AAAAGGGG"), ("B", "AAAAAGG")):
    previous = None
    for number, letter in enumerate(sequence, 101):
        residue = model.new_residue("ALA" if letter == "A" else "GLY", chain_id, number)
        backbone = []
        for index, (name, element) in enumerate((("N", "N"), ("CA", "C"), ("C", "C"))):
            atom = model.new_atom(name, element)
            residue.add_atom(atom)
            atom.coord = (number * 3.8 + index, 0 if chain_id == "A" else 20, 0)
            backbone.append(atom)
        model.new_bond(backbone[0], backbone[1])
        model.new_bond(backbone[1], backbone[2])
        if previous is not None:
            model.new_bond(previous, backbone[0])
        previous = backbone[2]
session.models.add([model])
check_for_changes(session)
model.residues[7].atoms.selected = True
check_for_changes(session)

old_window = getattr(session.ui, "main_window", None)
old_settings = getattr(session, "_codex_sequence_color_settings", None)
old_lookup = module._alignment_payload_for_entry
session._codex_sequence_color_settings = SimpleNamespace(
    aa_charge=True, nucleotides=True, base_palette="monochrome")
window = QMainWindow()
window.main_view = QWidget(window)
session.ui.main_window = window
module._alignment_payload_for_entry = lambda *args: None
bar = module.CodexSequenceBar(session, "Sequence Bar")
widget = bar.bar_widget
widget.setParent(None)
widget.resize(600, 320)
widget.show()
app.processEvents()
selection_before = tuple(sorted(atom.atomspec for atom in selected_atoms(session)))
scene_before = (model.atoms.coords.tobytes(), model.atoms.colors.tobytes(),
                model.residues.ribbon_colors.tobytes(), model.atoms.displays.tobytes())


def feedback(text, previous, following):
    assert bar.search_edit.match_label.text() == text, bar.search_edit.match_label.text()
    assert bar.search_prev_button.isEnabled() == previous
    assert bar.search_next_button.isEnabled() == following


def active_span(view):
    hits = [item for item in view.extraSelections()
            if item.format.background().color() in (QColor("#3d3417"), QColor("#f5c042"))]
    assert hits, "Search has no visible hit highlight"
    assert hits[-1].format.background().color() == QColor("#f5c042"), "Active hit was covered"
    cursor = hits[-1].cursor
    return cursor.selectionStart(), cursor.selectionEnd()


try:
    assert not bar.search_prev_button.isEnabled() and not bar.search_next_button.isEnabled()
    assert bar.search_edit.match_label.isHidden()
    bar.search_edit.setText("AAA")
    assert len(bar._search_matches) == 5
    feedback("1/5", True, True)
    assert "all chains" in bar.search_edit.toolTip()
    bar.search_next_button.click()
    feedback("2/5", True, True)
    bar._show_all_chains_hover(0, bar._entries[0]["spec"])
    feedback("2/5", True, True)
    bar._show_all_chains_hover(None, None)
    bar._refresh_selection_state()
    bar.refresh()
    feedback("2/5", True, True)
    assert bar.search_edit.match_label.isVisible(), "Hover/selection removed persistent count"
    assert len(bar.all_chains_text._search_ranges) == 5
    active_span(bar.all_chains_text)

    bar.all_chains_button.setChecked(False)
    assert bar._search_matches == [(0, 2), (1, 3)]
    assert bar.sequence_text._search_ranges == [(0, 2), (1, 3)]
    assert bar._current_entry["spec"] in bar.search_edit.toolTip()
    feedback("1/2", True, True)
    origin = bar.sequence_text._line_starts[1]
    assert active_span(bar.sequence_text) == (origin, origin + 3)
    bar.search_edit.setFocus()
    QTest.keyClick(bar.search_edit, Qt.Key.Key_Return)
    feedback("2/2", True, True)
    assert active_span(bar.sequence_text) == (origin + 1, origin + 4)
    QTest.keyClick(bar.search_edit, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    feedback("1/2", True, True)
    bar.search_prev_button.click()
    feedback("2/2", True, True)
    bar.search_next_button.click()
    feedback("1/2", True, True)

    # Existing letters/whitespace/digit cleaning and X wildcard remain supported.
    bar.search_edit.setText(" a1x-a ")
    assert bar._search_matches == [(0, 2), (1, 3)]
    bar.search_edit.setText("AAAA")
    feedback("1/1", False, False)
    bar.search_edit.setText("CCCC")
    feedback("0/0", False, False)
    assert "no matches" in bar.status_label._full_text
    QTest.keyClick(bar.search_edit, Qt.Key.Key_Escape)
    assert bar.search_edit.text() == "" and bar.search_edit.match_label.isHidden()
    assert "Find" not in bar.status_label._full_text and "no matches" not in bar.status_label._full_text
    assert not bar.sequence_text._search_ranges
    bar.search_edit.setText("AAA")
    bar.sequence_text.setFocus()
    QTest.keyClick(bar.sequence_text, Qt.Key.Key_Escape)
    assert bar.search_edit.text() == "AAA", "Escape outside search changed the query"
    assert bar._find_shortcut.parent() is widget
    assert bar._find_shortcut.context() == Qt.ShortcutContext.WidgetWithChildrenShortcut
    QTest.keySequence(bar.sequence_text, bar._find_shortcut.key())
    assert bar.search_edit.hasFocus() and bar.search_edit.selectedText() == "AAA"

    # A gapped alignment must report and highlight the actual alignment columns.
    entries = bar._entries
    rows = [{"row_id": "reference" if index == 0 else "moving", "spec": entry["spec"],
             "display": entry["display"], "aligned": "A-AA-A" if index == 0 else "AA-A-AA",
             "column_map": None}
            for index, entry in enumerate(entries)]
    payload = {"length": 7, "rows": rows, "reference": rows[0], "moving": rows[1]}
    module._alignment_payload_for_entry = lambda *args: payload
    bar._render_sequence()
    assert "alignment" in bar.search_edit.toolTip()
    matches = bar._search_matches
    assert [(item["start"], item["end"]) for item in matches] == [(0, 3), (2, 5), (0, 3), (1, 5), (3, 6)]
    feedback("1/5", True, True)
    assert len(bar.alignment_text._search_ranges) == 5
    active_span(bar.alignment_text)
    bar.search_next_button.click()
    feedback("2/5", True, True)
    active_span(bar.alignment_text)
    bar.search_edit.clear()
    assert bar.search_edit.match_label.isHidden() and "Find" not in bar.status_label._full_text

    # The badge shares the original 220 px input; it must not cover typing or clear.
    bar.search_edit.setText("AAA")
    for width in (600, 800, 900, 1400):
        widget.resize(width, 320)
        app.processEvents()
        label = bar.search_edit.match_label
        assert label.isVisible() and bar.search_edit.rect().contains(label.geometry())
        assert bar.search_edit.width() >= 220 and bar.search_edit.height() == 30
        assert bar.search_edit.font().pixelSize() == 13 and label.font().pixelSize() == 13
        assert bar.search_edit.width() - bar.search_edit.textMargins().right() > 130
        for button in bar.search_edit.findChildren(QToolButton):
            if not button.isHidden():
                assert not label.geometry().intersects(button.geometry()), (label.geometry(), button.geometry())
        for control in (bar.chain_combo, bar.search_edit, bar.search_prev_button, bar.search_next_button,
                        bar.refresh_button, bar.similar_button):
            corner = control.mapTo(widget, QPoint(control.width() - 1, control.height() - 1))
            assert widget.rect().contains(corner), (width, control.objectName(), corner)
        if width == 600:
            assert widget.grab().save("/tmp/usability-sequence-search-600.png")

    module._alignment_payload_for_entry = lambda *args: None
    bar._render_sequence()
    saved_entry = bar._current_entry
    bar._current_entry = {"sequence": "A" * 20000}
    started = time.perf_counter()
    large_matches = bar._compute_search_matches("AAA")
    search_seconds = time.perf_counter() - started
    assert len(large_matches) == 19998 and large_matches[-1] == (19997, 19999)
    bar._current_entry = saved_entry
    assert tuple(sorted(atom.atomspec for atom in selected_atoms(session))) == selection_before
    assert (model.atoms.coords.tobytes(), model.atoms.colors.tobytes(),
            model.residues.ribbon_colors.tobytes(), model.atoms.displays.tobytes()) == scene_before
    assert all(view._base_palette == "monochrome" for view in (
        bar.sequence_text, bar.all_chains_text, bar.alignment_text))
finally:
    module._alignment_payload_for_entry = old_lookup
    bar.delete()
    session.ui.main_window = old_window
    session._codex_sequence_color_settings = old_settings
    session.models.close([model])
    window.close()

print("USABILITY_SEQUENCE_OK", json.dumps({"ok": True, "platform": app.platformName(),
    "scopes": ["all chains", "single chain", "gapped alignment"],
    "overlapping_and_adjacent_hits": True, "persistent_count": True,
    "keyboard_scope": True, "widths": [600, 800, 900, 1400], "scene_preserved": True,
    "overlap_search_20000_residues_seconds": round(search_seconds, 4)}))
