"""Real Qt regression: ChimeraX --nogui --notools --exit --script <this file>.

Uses disposable structures and injected settings; it never saves user preferences.
"""
import importlib.util
import itertools
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from Qt.QtCore import QRectF
from Qt.QtGui import QColor, QPalette, QTextCursor
from Qt.QtWidgets import QApplication, QMainWindow, QWidget
from chimerax.atomic import AtomicStructure, check_for_changes, selected_atoms
from chimerax.build_structure.start import place_nucleic_acid, place_peptide
from chimerax.core.commands import run

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "chimerax.codex_bridge", root / "src/__init__.py",
    submodule_search_locations=[str(root / "src")])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
from chimerax.codex_bridge import sequence_bar as sequence_module
from chimerax.codex_bridge.sequence_bar import (
    ClickableAlignmentText, ClickableChainOverviewText, ClickableSequenceText,
    CodexSequenceBar, _residue_display_style, _sequence_text_style,
)
from chimerax.codex_bridge.sequence_colors import BASE_PALETTE_LABELS, base_legend_html

app = QApplication.instance() or QApplication([])

# Live reload migrates an old real Settings schema and persists presets safely.
# Redirect only this isolated check to a temporary config directory.
import chimerax
from chimerax.core.settings import Settings


class LegacySequenceColorSettings(Settings):
    AUTO_SAVE = {"aa_charge": True, "nucleotides": True}


original_dirs = chimerax.app_dirs_unversioned
original_color_settings = getattr(session, "_codex_sequence_color_settings", None)
try:
    with tempfile.TemporaryDirectory(prefix="sequence-palette-settings-") as config_dir:
        chimerax.app_dirs_unversioned = SimpleNamespace(user_config_dir=config_dir)
        legacy = LegacySequenceColorSettings(session, "Codex Sequence Colors")
        legacy.aa_charge = False
        legacy.nucleotides = False
        session._codex_sequence_color_settings = legacy
        migrated = sequence_module._sequence_color_settings(session)
        assert migrated is not legacy and migrated is session._codex_sequence_color_settings
        assert not migrated.aa_charge and not migrated.nucleotides
        assert migrated.base_palette == "muted"
        migrated.base_palette = "monochrome"
        restored = sequence_module.SequenceColorSettings(session, "Codex Sequence Colors")
        assert restored.base_palette == "monochrome"
        assert not restored.aa_charge and not restored.nucleotides
finally:
    chimerax.app_dirs_unversioned = original_dirs
    if original_color_settings is None:
        del session._codex_sequence_color_settings
    else:
        session._codex_sequence_color_settings = original_color_settings

MODES = list(itertools.product((False, True), repeat=2))
SOURCE = (45, 128, 88, 255)
NAMES = {"K": "LYS", "R": "ARG", "D": "ASP", "E": "GLU", "H": "HIS", "A": "ALA", "G": "GLY"}


def residue_style(letter, kind="protein", rgba=SOURCE):
    return {
        "rgba": rgba, "source": "cartoon", "atoms_displayed": False,
        "stick_displayed": False, "letter": letter, "polymer_kind": kind,
        "residue_name": NAMES.get(letter, letter) if kind == "protein" else letter,
    }


def background(style, charge, bases, view=None, base_palette="muted"):
    base = view.palette().color(QPalette.ColorRole.Base) if view is not None else QColor("#f0f0f0")
    return _sequence_text_style(style, base, charge=charge, bases=bases, base_palette=base_palette)["background"]


def fill_at(view, position):
    """Return the final ExtraSelection fill at a document character."""
    found = None
    for selection in view.extraSelections():
        if selection.cursor.selectionStart() <= position < selection.cursor.selectionEnd():
            found = selection.format.background().color()
    assert found is not None, (type(view).__name__, position, "missing fill")
    return found


def doc_position(view, line, column=0):
    return view.document().findBlockByNumber(line).position() + column


def cursor_rect(view, position):
    cursor = QTextCursor(view.document())
    cursor.setPosition(position)
    return view.cursorRect(cursor)


def entry(spec, letters, styles):
    return {"spec": spec, "sequence": letters, "length": len(letters),
            "residue_styles": styles, "tooltips": [f"{spec}:{i + 1}" for i in range(len(letters))]}


def set_theme(widget, dark):
    palette = QPalette(widget.palette())
    colors = {
        QPalette.ColorRole.Window: "#353535" if dark else "#eeeeee",
        QPalette.ColorRole.Base: "#252525" if dark else "#ffffff",
        QPalette.ColorRole.Text: "#eeeeee" if dark else "#202020",
        QPalette.ColorRole.WindowText: "#eeeeee" if dark else "#202020",
        QPalette.ColorRole.Button: "#454545" if dark else "#e8e8e8",
        QPalette.ColorRole.ButtonText: "#eeeeee" if dark else "#202020",
    }
    for role, value in colors.items():
        palette.setColor(role, QColor(value))
    app.setPalette(palette)
    widget.setPalette(palette)
    if widget.styleSheet():
        widget.setStyleSheet(widget.styleSheet())
    app.processEvents()


# Chemistry classification and the independence of the two switches.
charge_colors = {letter: background(residue_style(letter), True, False) for letter in "KRDEH"}
assert charge_colors["K"] == charge_colors["R"]
assert charge_colors["D"] == charge_colors["E"]
assert charge_colors["K"].blue() > charge_colors["K"].red(), "K/R must be blue"
assert charge_colors["D"].red() > charge_colors["D"].blue(), "D/E must be rose"
histidine = charge_colors["H"]
assert histidine.red() > histidine.green() > histidine.blue(), "H must be amber"
assert len({color.rgb() for color in charge_colors.values()}) == 3
base_colors = {letter: background(residue_style(letter, "nucleic"), False, True) for letter in "ACGTU"}
assert len({color.rgb() for color in base_colors.values()}) == 5, "A/C/G/T/U must be distinct"
for charge, bases in MODES:
    for letter in "KRDEH":
        style = residue_style(letter)
        assert background(style, charge, bases) == background(style, charge, False)
    for letter in "ACGTU":
        style = residue_style(letter, "nucleic")
        assert background(style, charge, bases) == background(style, False, bases)
assert background(residue_style("K"), False, False) != charge_colors["K"]
assert background(residue_style("A", "nucleic"), False, False) != base_colors["A"]


# The same live view API must recolor cached text in single, alignment and all-chain views.
styles = [residue_style(letter) for letter in "KRDEH"]
single = ClickableSequenceText(lambda *args: None, lambda *args: None)
single.set_sequence_text(["12345", "KRDEH"], 5, [""] * 5, styles)
single.highlight_ranges([(1, 1)])
single.set_search_ranges([(3, 3)], 0)
aligned = ClickableAlignmentText(lambda *args: None)
aligned.set_alignment_text(
    "Ref K-RDEH", 6, {"reference": [styles[0], None] + styles[1:]},
    {"offset": 4, "row_lines": {"reference": 0}, "sequence_rows": ["reference"]})
aligned.highlight_columns({"reference": [(2, 2)]})
aligned.set_search_ranges([{"row": "reference", "start": 4, "end": 4}], 0)
overview = ClickableChainOverviewText(lambda *args: None, lambda *args: None, lambda *args: None)
overview.set_chain_rows([entry("#99/A", "KRDEH", styles)])
overview.highlight_columns({"#99/A": [(1, 1)]})
overview.set_search_ranges([{"row": "#99/A", "start": 3, "end": 3}], 0)
view_cases = [
    (single, 1, 0, {"K": 0, "D": 2, "H": 4}, 1, 3),
    (aligned, 0, 4, {"K": 0, "D": 3, "H": 5}, 2, 4),
    (overview, overview._alignment_row_lines["#99/A"], overview._alignment_offset, {"K": 0, "D": 2, "H": 4}, 1, 3),
]
for view, line, offset, residues, selection_index, search_index in view_cases:
    view.resize(380, 120)
    view.show()
    app.processEvents()
    original_text = view.toPlainText()
    for charge, bases in MODES + [(False, False), (True, True)]:
        view.set_residue_coloring(charge=charge, bases=bases)
        app.processEvents()
        start = doc_position(view, line, offset)
        for letter, column in residues.items():
            assert fill_at(view, start + column) == background(residue_style(letter), charge, bases, view), (
                type(view).__name__, charge, bases, letter)
        assert fill_at(view, start + selection_index).name() == "#005ce6", "Selection overlay changed"
        assert fill_at(view, start + search_index).name() == "#f5c042", "Active search overlay changed"
        assert view.toPlainText() == original_text
        borders = view._residue_border_rects()
        assert bool(borders) == charge, (type(view).__name__, charge, bases, borders)
        assert all(color.getRgb()[:3] == SOURCE[:3] for rect, color in borders)
    if view is aligned:
        assert len(view._residue_border_rects()) == 2, "Alignment gaps must split source-color borders"


# Presets must invalidate cached fills in all view types, including hidden views.
nucleic_styles = [residue_style(letter, "nucleic") for letter in "ACGTUN"]
nucleic_single = ClickableSequenceText(lambda *args: None, lambda *args: None)
nucleic_single.set_sequence_text(["123456", "ACGTUN"], 6, [""] * 6, nucleic_styles)
nucleic_single.highlight_ranges([(1, 1)])
nucleic_single.set_search_ranges([(3, 3)], 0)
nucleic_aligned = ClickableAlignmentText(lambda *args: None)
nucleic_aligned.set_alignment_text(
    "DNA A-CGTUN", 7, {"reference": [nucleic_styles[0], None] + nucleic_styles[1:]},
    {"offset": 4, "row_lines": {"reference": 0}, "sequence_rows": ["reference"]})
nucleic_aligned.highlight_columns({"reference": [(2, 2)]})
nucleic_aligned.set_search_ranges([{"row": "reference", "start": 4, "end": 4}], 0)
nucleic_overview = ClickableChainOverviewText(lambda *args: None, lambda *args: None, lambda *args: None)
nucleic_overview.set_chain_rows([entry("#98/A", "ACGTUN", nucleic_styles)])
nucleic_overview.highlight_columns({"#98/A": [(1, 1)]})
nucleic_overview.set_search_ranges([{"row": "#98/A", "start": 3, "end": 3}], 0)
nucleic_cases = [
    (nucleic_single, 1, 0, {"A": 0, "G": 2, "U": 4, "N": 5}, 1, 3),
    (nucleic_aligned, 0, 4, {"A": 0, "G": 3, "U": 5, "N": 6}, 2, 4),
    (nucleic_overview, nucleic_overview._alignment_row_lines["#98/A"],
     nucleic_overview._alignment_offset, {"A": 0, "G": 2, "U": 4, "N": 5}, 1, 3),
]
for view, line, offset, residues, selection_index, search_index in nucleic_cases:
    original_text = view.toPlainText()
    for preset in (*BASE_PALETTE_LABELS, "muted"):
        view.set_residue_coloring(charge=False, bases=True, base_palette=preset)
        start = doc_position(view, line, offset)
        for letter, column in residues.items():
            assert fill_at(view, start + column) == background(
                residue_style(letter, "nucleic"), False, True, view, preset), (type(view).__name__, preset, letter)
        assert fill_at(view, start + selection_index).name() == "#005ce6"
        assert fill_at(view, start + search_index).name() == "#f5c042"
        assert view.toPlainText() == original_text
        if preset == "monochrome":
            assert len({fill_at(view, start + column).rgba() for column in residues.values()}) == 1


# Borders group original source-color runs, stop at short rows, and follow scrolling.
BLUE_SOURCE = (65, 105, 180, 255)
SHORT_SOURCE = (180, 103, 48, 255)
BASE_SOURCE = (135, 70, 165, 255)
long_letters = ("KRDEHAG" * 143)[:1000]
long_styles = [residue_style(letter, rgba=BLUE_SOURCE if 8 <= i < 16 else SOURCE)
               for i, letter in enumerate(long_letters)]
geometry = ClickableChainOverviewText(lambda *args: None, lambda *args: None, lambda *args: None)
geometry.set_chain_rows([
    entry("#90/A", long_letters, long_styles),
    entry("#90/B", "RDE", [residue_style(letter, rgba=SHORT_SOURCE) for letter in "RDE"]),
    entry("#90/C", "ACGTU", [residue_style(letter, "nucleic", BASE_SOURCE) for letter in "ACGTU"]),
])
geometry.resize(320, 130)
geometry.show()
geometry.set_residue_coloring(charge=True, bases=True)
app.processEvents()


def source_rect(rgba):
    matches = [rect for rect, color in geometry._residue_border_rects() if color.getRgb()[:3] == rgba[:3]]
    assert len(matches) == 1, (rgba, matches)
    return matches[0]


def assert_run_geometry(rect, line, start, end):
    offset = geometry._alignment_offset
    first = cursor_rect(geometry, doc_position(geometry, line, offset + start))
    last = cursor_rect(geometry, doc_position(geometry, line, offset + end))
    assert abs(rect.left() - first.left()) <= 2, (rect, first)
    assert abs(rect.right() - last.left()) <= 2, (rect, last)
    assert abs(rect.center().y() - first.center().y()) <= 2, (rect, first)


assert_run_geometry(source_rect(SHORT_SOURCE), geometry._alignment_row_lines["#90/B"], 0, 3)
assert_run_geometry(source_rect(BLUE_SOURCE), geometry._alignment_row_lines["#90/A"], 8, 16)
assert len(geometry._residue_border_rects()) == 5, "Contiguous source colors must form runs, not per-letter boxes"
blue_before = source_rect(BLUE_SOURCE)
scrollbar = geometry.horizontalScrollBar()
assert scrollbar.maximum() > 20
scrollbar.setValue(20)
app.processEvents()
blue_after = source_rect(BLUE_SOURCE)
assert abs((blue_before.left() - blue_after.left()) - scrollbar.value()) <= 1
assert_run_geometry(blue_after, geometry._alignment_row_lines["#90/A"], 8, 16)
scrollbar.setValue(scrollbar.maximum())
app.processEvents()
visible = QRectF(geometry.viewport().rect())
for rect, color in geometry._residue_border_rects():
    assert rect.intersects(visible) and rect.width() > 0 and rect.height() > 0, (rect, visible)
    assert color.getRgb()[:3] not in (SHORT_SOURCE[:3], BASE_SOURCE[:3]), "Off-screen short row still has a border"
for dark in (False, True):
    set_theme(geometry, dark)
    geometry.resize(900, 140)
    assert (geometry.palette().color(QPalette.ColorRole.Base).lightnessF() <= 0.5) == dark
    neutral = _sequence_text_style(residue_style("A"), geometry.palette().color(QPalette.ColorRole.Base), charge=True)
    charged = _sequence_text_style(residue_style("K"), geometry.palette().color(QPalette.ColorRole.Base), charge=True)
    assert neutral["background"].alpha() < charged["background"].alpha(), "Neutral residues should remain subdued"
    neutral_position = doc_position(geometry, geometry._alignment_row_lines["#90/A"], geometry._alignment_offset + 404)
    assert long_letters[404] == "A" and fill_at(geometry, neutral_position) == neutral["background"]
    scrollbar.setValue(2800)
    app.processEvents()
    assert len(geometry._residue_border_rects()) <= 3, "Border work should be limited to visible runs"
    assert geometry.grab().save(f"/tmp/sequence-coloring-long-{'dark' if dark else 'light'}.png")
geometry.set_residue_coloring(charge=False, bases=False)
assert not geometry._residue_border_rects()
scrollbar.setValue(0)
geometry.set_residue_coloring(charge=False, bases=True)
app.processEvents()
assert [color.getRgb()[:3] for rect, color in geometry._residue_border_rects()] == [BASE_SOURCE[:3]]


# Real models: buttons change only sequence rendering and the injected settings.
models = []
protein = AtomicStructure(session, name="Charge-color fixture")
session.models.add([protein])
models.append(protein)
place_peptide(protein, "KRDEH", [(-60, -45)] * 5, chain_id="A", position=(0, 0, 0))
place_peptide(protein, "KK", [(-60, -45)] * 2, chain_id="B", position=(0, 20, 0))
for kind, letters in (("dna", "ATGC"), ("rna", "ACGU")):
    model = AtomicStructure(session, name=f"{kind.upper()} color fixture")
    session.models.add([model])
    models.append(model)
    place_nucleic_acid(model, letters, type=kind, form="A" if kind == "rna" else "B",
                       position=(0, 40 + len(models) * 20, 0))
for model in models:
    model.atoms.colors = (58, 138, 184, 255)
    model.residues.ribbon_colors = (168, 108, 68, 255)
check_for_changes(session)


def scene_state():
    return tuple((model.atoms.colors.tobytes(), model.residues.ribbon_colors.tobytes(),
                  model.atoms.coords.tobytes(), model.atoms.displays.tobytes()) for model in models)


def selection_state():
    return tuple(sorted(atom.atomspec for atom in selected_atoms(session)))


settings_before = getattr(session, "_codex_sequence_color_settings", None)
injected_settings = SimpleNamespace(aa_charge=True, nucleotides=False)
session._codex_sequence_color_settings = injected_settings
window = QMainWindow()
window.main_view = QWidget(window)
session.ui.main_window = window
bar = CodexSequenceBar(session, "Sequence Bar")
bar.bar_widget.setParent(None)
bar.bar_widget.resize(1100, 280)
bar.bar_widget.show()
app.processEvents()
assert bar._color_settings is injected_settings
assert bar.charge_colors_button.isChecked() and not bar.base_colors_button.isChecked()
assert bar._base_palette == "muted", "Legacy settings must use the muted default"
protein_row = f"#{protein.id_string}/A"
style = _residue_display_style(protein.chains[0].existing_residues[0])
assert style["residue_name"] == "LYS" and style["letter"] == "K" and style["polymer_kind"] == "protein"
run(session, f"select {protein_row}:2")
check_for_changes(session)
bar._refresh_selection_state()
bar.search_edit.setText("DE")
app.processEvents()
scene_before = scene_state()
selection_before = selection_state()
search_before = (list(bar._search_matches), bar._search_active_index)
for charge, bases in MODES:
    bar.charge_colors_button.setChecked(charge)
    bar.base_colors_button.setChecked(bases)
    app.processEvents()
    assert scene_state() == scene_before, "Sequence toggles changed 3D colors, positions, or display"
    assert selection_state() == selection_before, "Sequence toggles changed actual selection"
    assert (list(bar._search_matches), bar._search_active_index) == search_before
    assert injected_settings.aa_charge == charge and injected_settings.nucleotides == bases
    text = bar.all_chains_text
    start = doc_position(text, text._alignment_row_lines[protein_row], text._alignment_offset)
    assert fill_at(text, start + 1).name() == "#005ce6"
    assert fill_at(text, start + 2).name() == "#f5c042"
    source_style = text.entries_by_row[protein_row]["residue_styles"][0]
    assert fill_at(text, start) == background(source_style, charge, bases, text)

for preset in (*BASE_PALETTE_LABELS, "muted"):
    bar.base_colors_button.setChecked(False)
    saved_charge = bar.charge_colors_button.isChecked()
    bar._base_palette_actions[preset].trigger()
    app.processEvents()
    assert bar.base_colors_button.isChecked(), "Choosing a preset must enable nucleotide fills"
    assert bar.charge_colors_button.isChecked() == saved_charge
    assert injected_settings.base_palette == preset and injected_settings.nucleotides
    assert [key for key, action in bar._base_palette_actions.items() if action.isChecked()] == [preset]
    assert BASE_PALETTE_LABELS[preset] in bar._color_key_label.text()
    assert base_legend_html(preset) in bar._color_key_label.text()
    assert all(view._base_palette == preset for view in (bar.sequence_text, bar.alignment_text, bar.all_chains_text))
    assert scene_state() == scene_before, "Preset selection changed scene colors, positions, or display"
    assert selection_state() == selection_before, "Preset selection changed actual selection"
    assert (list(bar._search_matches), bar._search_active_index) == search_before
    start = doc_position(text, text._alignment_row_lines[protein_row], text._alignment_offset)
    assert fill_at(text, start + 1).name() == "#005ce6"
    assert fill_at(text, start + 2).name() == "#f5c042"

for dark in (False, True):
    set_theme(bar.bar_widget, dark)
    text = bar.all_chains_text
    assert (text.palette().color(QPalette.ColorRole.Base).lightnessF() <= 0.5) == dark
    start = doc_position(text, text._alignment_row_lines[protein_row], text._alignment_offset)
    source_style = text.entries_by_row[protein_row]["residue_styles"][0]
    assert fill_at(text, start) == background(source_style, True, True, text), "Palette change left cached fills"
    assert scene_state() == scene_before
    assert bar.bar_widget.grab().save(f"/tmp/sequence-coloring-overview-{'dark' if dark else 'light'}.png")

# Hidden single/alignment views must receive the current settings before being revealed.
lookup = sequence_module._alignment_payload_for_entry
bar.search_edit.clear()
run(session, "select clear")
check_for_changes(session)
try:
    sequence_module._alignment_payload_for_entry = lambda *args: None
    bar.all_chains_button.setChecked(False)
    assert bar.sequence_text.isVisible() and not bar.alignment_text.isVisible()
    for charge in (False, True):
        bar.charge_colors_button.setChecked(charge)
        app.processEvents()
        assert bool(bar.sequence_text._residue_border_rects()) == charge
    entries = {item["chain_id"]: item for item in bar._entries if item["model_spec"] == f"#{protein.id_string}"}
    reference = entries["A"]
    moving = entries["B"]
    payload = {
        "length": 5, "identity": 0.2,
        "reference": {"spec": reference["spec"], "display": reference["spec"], "aligned": "KRDEH",
                      "styles": reference["residue_styles"],
                      "column_map": [r["spec"] for r in reference["residues"]]},
        "moving": {"spec": moving["spec"], "display": moving["spec"], "aligned": "KK---",
                   "styles": moving["residue_styles"] + [None] * 3,
                   "column_map": [r["spec"] for r in moving["residues"]] + [None] * 3},
    }
    sequence_module._alignment_payload_for_entry = lambda *args: payload
    bar._render_sequence()
    app.processEvents()
    assert bar.alignment_text.isVisible() and not bar.sequence_text.isVisible()
    for charge in (False, True):
        bar.charge_colors_button.setChecked(charge)
        app.processEvents()
        assert bool(bar.alignment_text._residue_border_rects()) == charge
    assert scene_state() == scene_before
finally:
    sequence_module._alignment_payload_for_entry = lookup
    bar.delete()
    session.models.close(models)
    if settings_before is None:
        del session._codex_sequence_color_settings
    else:
        session._codex_sequence_color_settings = settings_before
    for view in (single, aligned, overview, geometry, nucleic_single, nucleic_aligned, nucleic_overview):
        view.close()

report = {"ok": True, "toggle_combinations": 4, "view_types": ["single", "alignment", "all chains"],
          "base_presets": list(BASE_PALETTE_LABELS), "legacy_settings_migration_and_persistence": True,
          "source_color_borders": True, "short_rows_and_scroll": True,
          "selection_and_search_preserved": True, "scene_colors_unchanged": True}
Path("/tmp/sequence-coloring-check.json").write_text(json.dumps(report, indent=2))
print("SEQUENCE_COLORING_OK", json.dumps(report))
