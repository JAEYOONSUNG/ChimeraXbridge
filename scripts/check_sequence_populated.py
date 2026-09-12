"""Populated native sequence UI oracle, strictly headless/offscreen."""
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

assert os.environ.get("QT_QPA_PLATFORM") == "offscreen"
assert not session.ui.is_gui

from Qt.QtCore import QPoint
from Qt.QtGui import QTextCursor
from Qt.QtWidgets import QApplication, QMainWindow, QWidget
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
model = AtomicStructure(session, name="Populated sequence layout fixture with a long model name")
letters = "AKRDEHGT" * 30
for chain_number in range(18):
    previous = None
    chain_id = chr(65 + chain_number)
    for number, letter in enumerate(letters, 1):
        residue = model.new_residue(next(name for name, code in module.AA3_TO_1.items()
                                        if code == letter), chain_id, number)
        backbone = []
        for index, (name, element) in enumerate((("N", "N"), ("CA", "C"), ("C", "C"))):
            atom = model.new_atom(name, element)
            residue.add_atom(atom)
            atom.coord = (number * 3.8 + index, chain_number * 20, 0)
            backbone.append(atom)
        model.new_bond(backbone[0], backbone[1])
        model.new_bond(backbone[1], backbone[2])
        if previous is not None:
            model.new_bond(previous, backbone[0])
        previous = backbone[2]
session.models.add([model])
check_for_changes(session)
assert len(model.chains) == 18 and all(len(chain.existing_residues) == 240 for chain in model.chains)
model.residues[19].atoms.selected = True
check_for_changes(session)

old_window = getattr(session.ui, "main_window", None)
old_settings = getattr(session, "_codex_sequence_color_settings", None)
session._codex_sequence_color_settings = SimpleNamespace(
    aa_charge=True, nucleotides=True, base_palette="purine_pyrimidine")
window = QMainWindow()
window.main_view = QWidget(window)
session.ui.main_window = window
lookup = module._alignment_payload_for_entry
module._alignment_payload_for_entry = lambda *args: None
bar = module.CodexSequenceBar(session, "Sequence Bar")
widget = bar.bar_widget
widget.setParent(None)
widget.resize(900, 320)
widget.show()
app.processEvents()
assert len(bar._entries) == 18
bar._set_base_palette("monochrome")
bar.search_edit.setText("KRDE")
bar._refresh_selection_state()
selection_before = tuple(sorted(atom.atomspec for atom in selected_atoms(session)))
scene_before = (model.atoms.coords.tobytes(), model.atoms.colors.tobytes(),
                model.residues.ribbon_colors.tobytes(), model.atoms.displays.tobytes())

rows = [{"row_id": "reference" if index == 0 else f"row{index}",
         "spec": entry["spec"], "display": entry["display"],
         "aligned": entry["sequence"], "styles": entry["residue_styles"],
         "column_map": [residue["spec"] for residue in entry["residues"]]}
        for index, entry in enumerate(bar._entries)]
payload = {"length": len(letters), "rows": rows, "multi_alignment": True,
           "reference": rows[0], "moving": rows[1]}
controls = (bar.chain_combo, bar.selection_button, bar.search_edit,
            bar.search_prev_button, bar.search_next_button, bar.refresh_button,
            bar.similar_button, bar.all_chains_button, bar.charge_colors_button,
            bar.base_colors_button, bar.color_key_button, bar.panel_layout_button)
findings = []


def contained(child):
    point = child.mapTo(widget, QPoint(0, 0))
    return (point.x() >= 0 and point.y() >= 0
            and point.x() + child.width() <= widget.width()
            and point.y() + child.height() <= widget.height())


def fail(condition, message, context):
    if not condition:
        findings.append((context, message))


def check_view(mode, width, repeat):
    widget.resize(width, 320)
    app.processEvents()
    widget.layout().activate()
    context = f"{mode}/{width}/pass{repeat}"
    view = {"single": bar.sequence_text, "overview": bar.all_chains_text,
            "alignment": bar.alignment_text}[mode]
    fail(widget.width() == width and widget.height() <= 320, "Panel dimensions changed", context)
    fail(view.isVisible() and contained(view), "Sequence viewport is clipped", context)
    fail(all(control.isVisible() and contained(control) for control in controls),
         "Header controls are clipped or hidden", context)
    fail({control.height() for control in controls} == {30}, "Control heights differ", context)
    fail({control.font().pixelSize() for control in controls} == {13}, "Header fonts differ", context)
    fail(bar.search_edit.text() == "KRDE" and bool(bar._search_matches), "Search state lost", context)
    fail(all(text._base_palette == "monochrome" for text in (
        bar.sequence_text, bar.alignment_text, bar.all_chains_text)), "Palette lost", context)
    fail(tuple(sorted(atom.atomspec for atom in selected_atoms(session))) == selection_before,
         "Native selection changed", context)
    fail((model.atoms.coords.tobytes(), model.atoms.colors.tobytes(),
          model.residues.ribbon_colors.tobytes(), model.atoms.displays.tobytes()) == scene_before,
         "Structure changed", context)
    horizontal = view.horizontalScrollBar()
    fail(horizontal.maximum() > 0, "Long residues have no scroll range", context)
    if mode == "single":
        fail(bar.sequence_scrollbar.isVisible() and contained(bar.sequence_scrollbar),
             "Single-sequence scrollbar missing", context)
        bar.sequence_scrollbar.setValue(bar.sequence_scrollbar.maximum())
        fail(horizontal.value() == horizontal.maximum(), "External scrollbar not synchronized", context)
        line, column = 1, len(letters) - 1
    else:
        fail(not bar.sequence_scrollbar.isVisible(), "Hidden single view has a stray scrollbar", context)
        fail(horizontal.isVisible(), "Internal horizontal scrollbar missing", context)
        vertical = view.verticalScrollBar()
        fail(vertical.isVisible() and vertical.maximum() > 0, "Rows have no vertical scroll", context)
        horizontal.setValue(horizontal.maximum())
        vertical.setValue(vertical.maximum())
        row = list(view._alignment_row_lines)[-1]
        if mode == "alignment":
            row = view._alignment_sequence_rows[-1]
        line, column = view._alignment_row_lines[row], view._alignment_offset + len(letters) - 1
    app.processEvents()
    block = view.document().findBlockByNumber(line)
    cursor = QTextCursor(view.document())
    cursor.setPosition(block.position() + column)
    rect = view.cursorRect(cursor)
    fail(view.viewport().rect().contains(rect), f"Last residue clipped: {rect.getRect()}", context)
    if repeat == 0 and width in (600, 900):
        assert widget.grab().save(f"/tmp/populated-sequence-{mode}-{width}.png")


try:
    # Four full passes expose state-dependent rangeChanged and resize behavior.
    for repeat in range(4):
        for mode in ("overview", "single", "alignment", "single", "overview"):
            module._alignment_payload_for_entry = (lambda *args: payload) if mode == "alignment" else (lambda *args: None)
            bar.all_chains_button.setChecked(mode == "overview")
            bar._render_sequence()
            for width in (900, 600, 700, 800, 900, 600, 900):
                check_view(mode, width, repeat)
    assert not findings, json.dumps(findings, indent=2)
finally:
    module._alignment_payload_for_entry = lookup
    bar.delete()
    session.ui.main_window = old_window
    session._codex_sequence_color_settings = old_settings
    session.models.close([model])
    window.close()

print("SEQUENCE_POPULATED_OK", json.dumps({"ok": True, "chains": 18, "residues_per_chain": 240,
    "passes": 4, "modes": ["overview", "single", "alignment"], "widths": [600, 700, 800, 900],
    "platform": app.platformName(), "scene_preserved": True, "search_and_selection_preserved": True}))
