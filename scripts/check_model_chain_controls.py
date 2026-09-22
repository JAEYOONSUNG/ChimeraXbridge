"""Native Models/chain integration in an isolated offscreen ChimeraX session."""

import importlib.util
import json
from pathlib import Path
import runpy
import sys
from unittest.mock import patch

from Qt.QtCore import QPoint, QPointF, Qt
from Qt.QtGui import QWheelEvent
from Qt.QtWidgets import QApplication, QMenu, QPushButton
from PyQt6 import sip
from chimerax.atomic import AtomicStructure, MolecularSurface, Residue, check_for_changes, selected_atoms
from chimerax.core.commands import run
from chimerax.core.models import Model

root = Path(__file__).resolve().parents[1]
app = runpy.run_path(str(root / "scripts/headless_ui_fixture.py"))["install"](session)
callback_errors = []
original_excepthook = sys.excepthook


def record_callback_error(kind, error, traceback):
    callback_errors.append((kind.__name__, str(error)))
    original_excepthook(kind, error, traceback)


sys.excepthook = record_callback_error
spec = importlib.util.spec_from_file_location("chimerax.codex_bridge", root / "src/__init__.py",
    submodule_search_locations=[str(root / "src")])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
package._schedule_helper_dock_layout = lambda *args, **kwargs: None
from chimerax.codex_bridge.compound_selection import _settings
from chimerax.codex_bridge.display_controls import DisplayControlsWidget
from chimerax.codex_bridge.runtime_patches import _patch_model_panel_id_reorder, _patch_model_panel_ui
from chimerax.codex_bridge.ui_theme import style_model_panel


def settle():
    check_for_changes(session)
    for _ in range(8):
        app.processEvents()


def command(text):
    run(session, text)
    settle()


def pointers(atoms):
    return set(atoms.pointers)


def selection():
    return pointers(selected_atoms(session))


def add_residue(model, name, chain, number, definitions, origin):
    residue = model.new_residue(name, chain, number)
    atoms = []
    for index, (name, element) in enumerate(definitions):
        atom = model.new_atom(name, element)
        residue.add_atom(atom)
        atom.coord = (origin[0] + index * 1.35, origin[1], origin[2])
        atoms.append(atom)
    for first, second in zip(atoms, atoms[1:]):
        model.new_bond(first, second)
    return residue


def chain_atoms(chain_id, structure=None):
    structure = model if structure is None else structure
    return structure.residues.filter(structure.residues.chain_ids == chain_id).atoms


model = AtomicStructure(session, name="Protein complex with ADP")
for chain_index, chain in enumerate(("A", "B")):
    previous = None
    for number in range(1, 4):
        residue = add_residue(model, "ALA", chain, number,
            [("N", "N"), ("CA", "C"), ("C", "C"), ("O", "O")],
            (number * 5.5, chain_index * 18, 0))
        if previous is not None:
            model.new_bond(previous.find_atom("C"), residue.find_atom("N"))
        previous = residue
previous = None
for number in range(1, 3):
    residue = add_residue(model, "DA", "D", number,
        [("P", "P"), ("O5'", "O"), ("C5'", "C"), ("C4'", "C"), ("C3'", "C"), ("O3'", "O")],
        (number * 9, 36, 0))
    if previous is not None:
        model.new_bond(previous.find_atom("O3'"), residue.find_atom("P"))
    previous = residue
ligand = add_residue(model, "ADP", "L", 201, [("C1", "C"), ("C2", "C"), ("O1", "O")], (0, 54, 0))
ion = add_residue(model, "ZN", "Z", 301, [("ZN", "Zn")], (0, 65, 0))
water = add_residue(model, "HOH", "W", 401, [("O", "O")], (0, 76, 0))
blank = add_residue(model, "ATP", "", 501, [("C1", "C"), ("C2", "C"), ("O1", "O")], (0, 87, 0))
model.ss_assigned = True
session.models.add([model])
other = AtomicStructure(session, name="Independent comparison")
add_residue(other, "LIG", "Q", 1, [("C1", "C"), ("C2", "C")], (50, 0, 0))
other.ss_assigned = True
session.models.add([other])
annotation = Model("Unrelated annotation", session)
session.models.add([annotation])
model.atoms.displays = True
model.residues.ribbon_displays = False
chain_atoms("A").displays = False
chain_atoms("A").unique_residues.ribbon_displays = True
other.atoms.displays = True
model.atoms.colors = (70, 120, 175, 255)
other.atoms.colors = (155, 85, 55, 255)
command(f"surface #{model.id_string}/A")
surfaces = session.models.list(type=MolecularSurface)
assert surfaces and all(surface.atoms.intersects(chain_atoms("A")) for surface in surfaces)
assert all(r.polymer_type == Residue.PT_PROTEIN for r in chain_atoms("A").unique_residues)
assert all(r.polymer_type == Residue.PT_NUCLEIC for r in chain_atoms("D").unique_residues)
assert set(water.atoms.structure_categories) == {"solvent"}
assert "solvent" not in set(blank.atoms.structure_categories)


def appearance():
    return (tuple((m.display, m.atoms.displays.tobytes(), m.residues.ribbon_displays.tobytes())
                  for m in (model, other)),
            tuple((s.display, frozenset(s.show_atoms.pointers),
                   None if s.triangle_mask is None else s.triangle_mask.tobytes()) for s in surfaces))


def immutable_scene():
    return (tuple((m.atoms.coords.tobytes(), m.atoms.colors.tobytes(),
                   m.residues.ribbon_colors.tobytes(), m.position.matrix.tobytes()) for m in (model, other)),
            session.main_view.camera.position.matrix.tobytes(), annotation.display)


# Models preferences must not inherit the Display Controls collapsed/broad mode.
_settings(session).expanded = False
_settings(session).all_nonprotein = True
_settings(session, "models").expanded = True
_settings(session, "models").all_nonprotein = False
display = DisplayControlsWidget(session)
display.resize(340, 450)
display.show()
from chimerax.model_panel.tool import model_panel
panel = model_panel(session, "Model Panel")
panel.countdown = 0
panel._fill_tree(always_rebuild=True)
panel.tree.setCurrentItem(next(item for item in panel._items if item._model is model))
native_buttons = list(panel.tool_window.ui_area.findChildren(QPushButton))
original_tree = panel.tree
before = appearance(), immutable_scene(), selection()
_patch_model_panel_id_reorder(session)
_patch_model_panel_ui(session)
host = panel.tool_window.ui_area
host.setParent(None)
host.resize(350, 520)
host.show()
settle()
controls = panel._codex_chain_controls
tabs = panel._codex_model_views
scroll = panel._codex_models_scroll
assert tabs.count() == 2 and tabs.currentIndex() == 0
assert tabs.tabText(0).replace("&&", "&") == "Chains & molecules" and tabs.tabText(1) == "Advanced models"
assert controls.isVisible() and controls.compounds.body.isVisible()
assert "non-protein" in controls.compounds.expand_button.text()
assert controls.compounds.mode_combo.currentData() is False
assert display.compound_selection.mode_combo.currentData() is True
assert not display.compound_selection.expand_button.isChecked()
assert (appearance(), immutable_scene(), selection()) == before
assert panel.tree is original_tree and all(button in host.findChildren(QPushButton) for button in native_buttons)
assert {item._model for item in panel.tree.selectedItems()} == {model}
for _ in range(3):
    style_model_panel(panel)
assert panel._codex_model_views is tabs and panel._codex_models_scroll is scroll
assert tabs.count() == 2 and tabs.currentIndex() == 0
assert {item._model for item in panel.tree.selectedItems()} == {model}
assert all((model, chain) in controls.rows for chain in (None, "A", "B", "D", "L", "Z", "W", blank.chain_id)), list(controls.rows)
assert "Protein" in controls.rows[(model, "A")].text(0)
assert "DNA/RNA" in controls.rows[(model, "D")].text(0)
assert "ADP" in controls.rows[(model, "L")].text(0)
assert "(blank)" in controls.rows[(model, blank.chain_id)].text(0)
assert (annotation, None) not in controls.rows
baseline = immutable_scene()
print("MODEL_CHAINS_NATIVE_INTEGRATION_OK")

# A transient native state read failure is visible, bounded and recoverable.
before_failure = appearance(), immutable_scene(), selection()
with patch.object(controls.controller, "state", side_effect=RuntimeError("temporary state read failed")):
    with patch.object(session.logger, "warning", wraps=session.logger.warning) as warning:
        controls._queue_refresh()
        settle()
        assert "temporary state read failed" in controls.status.text()
        controls.refresh()
        assert warning.call_count == 1, "Repeated refreshes flooded the log with the same failure"
    assert not controls.tree.signalsBlocked(), "Failed refresh left chain interactions blocked"
assert (appearance(), immutable_scene(), selection()) == before_failure
controls.refresh()
assert not controls.status.text(), "Recovered state retained a stale failure message"
assert (appearance(), immutable_scene(), selection()) == before_failure
print("MODEL_CHAINS_REFRESH_RECOVERY_OK")


def check_state(chain, expected, column=1, structure=None):
    structure = model if structure is None else structure
    actual = controls.rows[(structure, chain)].checkState(column)
    assert actual == expected, (chain, column, actual, expected)


# Toggle all representations of a chain, preserve siblings, and undo exactly.
original = appearance()
controls.rows[(model, "A")].setCheckState(1, Qt.CheckState.Unchecked)
settle()
check_state("A", Qt.CheckState.Unchecked)
assert not chain_atoms("A").displays.any() and not chain_atoms("A").unique_residues.ribbon_displays.any()
assert all(not len(s.show_atoms & chain_atoms("A")) or not s.display for s in surfaces)
check_state("B", Qt.CheckState.Checked)
assert immutable_scene() == baseline
session.undo.undo()
settle()
assert appearance() == original
check_state("A", Qt.CheckState.Checked)
session.undo.redo()
settle()
check_state("A", Qt.CheckState.Unchecked)
controls.rows[(model, "A")].setCheckState(1, Qt.CheckState.Checked)
settle()
assert appearance() == original, ("Hide/show changed the chain's chosen representation", appearance(), original)

# External commands and raw atomic/model changes update without refresh().
command(f"hide #{model.id_string}/B atoms")
check_state("B", Qt.CheckState.Unchecked)
command(f"cartoon #{model.id_string}/B")
check_state("B", Qt.CheckState.Checked)
command(f"~cartoon #{model.id_string}/B")
chain_atoms("B").unique_residues[0].atoms.displays = True
settle()
check_state("B", Qt.CheckState.PartiallyChecked)
command(f"show #{model.id_string}/B atoms")
check_state("B", Qt.CheckState.Checked)
command(f"hide #{model.id_string}/A target acs")
check_state("A", Qt.CheckState.Unchecked)
command(f"show #{model.id_string}/A surfaces")
check_state("A", Qt.CheckState.Checked)
model.display = False
settle()
check_state(None, Qt.CheckState.Unchecked)
model.display = True
settle()
check_state("A", Qt.CheckState.Checked)
assert immutable_scene() == baseline
print("MODEL_CHAINS_VISIBILITY_LIVE_OK")

# Native Add/Subtract semantics retain unrelated objects and support undo.
command("select clear")
annotation.selected = True
settle()
display.atoms_transparency.set_value(47)
assert display._pending and display._apply_timer.isActive()
controls.rows[(model, "A")].setCheckState(2, Qt.CheckState.Checked)
assert not display._pending and not display._apply_timer.isActive(), "Chain selection retained a queued appearance edit"
settle()
assert selection() == pointers(chain_atoms("A")) and annotation.selected
check_state("A", Qt.CheckState.Checked, 2)
controls.rows[(model, "B")].setCheckState(2, Qt.CheckState.Checked)
settle()
assert selection() == pointers(chain_atoms("A")) | pointers(chain_atoms("B"))
controls.rows[(model, "A")].setCheckState(2, Qt.CheckState.Unchecked)
settle()
assert selection() == pointers(chain_atoms("B")) and annotation.selected
session.undo.undo()
settle()
assert selection() == pointers(chain_atoms("A")) | pointers(chain_atoms("B"))
command(f"select {chain_atoms('A').unique_residues[0].atomspec}")
check_state("A", Qt.CheckState.PartiallyChecked, 2)
check_state("B", Qt.CheckState.Unchecked, 2)

# The embedded nonprotein action is immediately discoverable and cancels edits.
expected_compounds = pointers(ligand.atoms) | pointers(ion.atoms) | pointers(blank.atoms) | pointers(other.atoms)
display.atoms_transparency.set_value(61)
assert display._pending
controls.compounds.select_button.click()
assert not display._pending and not display._apply_timer.isActive(), "Molecule selection retained a queued appearance edit"
settle()
assert selection() == expected_compounds
assert not annotation.selected
assert not pointers(water.atoms) & selection()
controls.compounds.mode_combo.setCurrentIndex(1)
settle()
assert selection() == expected_compounds, "Changing molecule scope edited selection"
assert display.compound_selection.mode_combo.currentData() is True
controls.compounds.select_button.click()
settle()
assert selection() == expected_compounds | pointers(water.atoms) | pointers(chain_atoms("D"))
controls.compounds.mode_combo.setCurrentIndex(0)
assert display.compound_selection.mode_combo.currentData() is True
assert immutable_scene() == baseline
print("MODEL_CHAINS_SELECTION_OK")

# Preserve search, current row and expansion across automatic topology rebuilds.
controls.search.setText("A")
settle()
assert not controls.rows[(model, "A")].isHidden()
assert controls.rows[(model, "B")].isHidden() and controls.rows[(model, "L")].isHidden()
controls.search.setText("ADP")
settle()
assert not controls.rows[(model, "L")].isHidden()
controls.search.setText("DNA/RNA")
settle()
assert not controls.rows[(model, "D")].isHidden()
assert controls.rows[(model, "A")].isHidden()
controls.search.clear()
controls.tree.setCurrentItem(controls.rows[(model, "B")])
controls.rows[(other, None)].setExpanded(False)
controls.search.setText("Protein")
extra = AtomicStructure(session, name="Live addition")
for index in range(24):
    add_residue(extra, "LIG", f"X{index:02d}", index + 1, [("C1", "C")], (index * 4, 100, 0))
extra.ss_assigned = True
session.models.add([extra])
settle()
assert controls.search.text() == "Protein"
assert controls.tree.currentItem()._chain_key == (model, "B")
assert not controls.rows[(other, None)].isExpanded()
assert (extra, "X23") in controls.rows
extra.name = "Renamed live fixture"
settle()
assert "Renamed live fixture" in controls.rows[(extra, None)].text(0)
controls.search.clear()
settle()
bar = controls.tree.verticalScrollBar()
assert bar.maximum() > 0
bar.setValue(min(5, bar.maximum()))
position = bar.value()
command(f"select #{model.id_string}/A")
assert bar.value() == position and controls.tree.currentItem()._chain_key == (model, "B")
controls.search.setText("no-matching-chain")
settle()
assert not controls.only_button.isEnabled(), "Filtered-out current row remained actionable"
controls.search.clear()
controls.tree.setCurrentItem(controls.rows[(model, "B")])
settle()
before_isolate = appearance()
controls.only_button.click()
settle()
check_state("B", Qt.CheckState.Checked)
check_state("A", Qt.CheckState.Unchecked)
check_state("D", Qt.CheckState.Unchecked)
assert annotation.display and immutable_scene() == baseline
session.undo.undo()
settle()
assert appearance() == before_isolate
controls.search.setText("Protein")
controls.show_all_button.click()
settle()
assert controls.controller.state(extra.atoms) == "shown", "Show all incorrectly followed the text filter"
controls.search.clear()
session.models.close([extra])
settle()
assert not any(key[0] is extra for key in controls.rows)
print("MODEL_CHAINS_FILTER_AND_ACTIONS_OK")

# Context menu routing and repeated patching preserve Advanced native controls.
menu = QMenu()
panel.tool_window.fill_context_menu(menu, 0, 0)
assert {action.text() for action in menu.actions()} == {"Show all chains", "Toggle molecule selection"}
tabs.setCurrentIndex(1)
settle()
menu = QMenu()
panel.tool_window.fill_context_menu(menu, 0, 0)
assert "Show Sequential Display Controls" in {action.text() for action in menu.actions()}
assert "Show all chains" not in {action.text() for action in menu.actions()}
_patch_model_panel_ui(session)
assert tabs.currentIndex() == 1 and panel._codex_chain_controls is controls
panel.countdown = 0
panel._fill_tree(always_rebuild=True)
assert {item._model for item in panel.tree.selectedItems()} == {model}
buttons = {button.text(): button for button in native_buttons}
buttons["Hide"].click()
assert not model.display and other.display
buttons["Show"].click()
assert model.display
tabs.setCurrentIndex(0)
settle()
check_state("B", Qt.CheckState.Checked)


def wheel(target):
    position = target.rect().center()
    event = QWheelEvent(QPointF(position), QPointF(target.mapToGlobal(position)),
        QPoint(0, -40), QPoint(), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(target, event)
    settle()


# Both tabs remain reachable in a short dock; wheel never changes molecule mode.
host.resize(320, 180)
settle()
assert host.height() == 180, (host.height(), host.minimumHeight())
outer = scroll.verticalScrollBar()
assert outer.maximum() > 0
for control in (controls.compounds.select_button, controls.search, controls.only_button, controls.show_all_button):
    # Qt uses a QLineEdit's inset focus rectangle; include its border too.
    scroll.ensureWidgetVisible(control, 8, 8)
    settle()
    location = control.mapTo(scroll.viewport(), QPoint())
    host.grab().save("/tmp/model-chain-controls-short.png")
    assert location.y() >= 0 and location.y() + control.height() <= scroll.viewport().height() + 1, (
        type(control).__name__, location.y(), control.height(), scroll.viewport().height(), outer.value(), outer.maximum())
    assert location.x() >= 0 and location.x() + control.width() <= scroll.viewport().width() + 1, control
outer.setValue(0)
mode = controls.compounds.mode_combo.currentIndex()
wheel(controls.compounds.mode_combo)
assert controls.compounds.mode_combo.currentIndex() == mode and outer.value() > 0
tabs.setCurrentIndex(1)
settle()
scroll.ensureWidgetVisible(buttons["Close"], 0, 0)
settle()
location = buttons["Close"].mapTo(scroll.viewport(), QPoint())
assert location.y() >= 0 and location.y() + buttons["Close"].height() <= scroll.viewport().height() + 1
tabs.setCurrentIndex(0)
host.resize(350, 650)
outer.setValue(0)
settle()
assert host.grab().save("/tmp/model-chain-controls.png")
print("MODEL_CHAINS_SCROLL_OK")

# Hidden tabs catch up on show; actual destruction removes all new handlers.
tabs.setCurrentIndex(1)
command(f"hide #{model.id_string}/B target acs")
tabs.setCurrentIndex(0)
settle()
check_state("B", Qt.CheckState.Unchecked)
assert controls._handlers
controls._queue_refresh()
sip.delete(host)
assert controls._closed and not controls._handlers
settle()
display.cleanup()
display.close()
assert not callback_errors, callback_errors
sys.excepthook = original_excepthook
report = {"ok": True, "native_tabs": True, "live_visibility": True,
          "native_undo": True, "nonprotein_selection": True, "pending_edits_cancelled": True,
          "filter_and_highlight_retained": True, "short_dock_height": 180,
          "teardown": True, "visible_windows": False}
Path("/tmp/model-chain-controls-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print("MODEL_CHAIN_CONTROLS_OK", json.dumps(report))
