"""Mouse/keyboard and reopening workflows in a disposable native Models tool."""
import importlib
import importlib.util
import json
from pathlib import Path
import runpy
import sys

import numpy as np
from Qt.QtCore import QPoint, Qt
from Qt.QtWidgets import QStyle, QStyleOptionViewItem
from PyQt6.QtTest import QTest
from chimerax.atomic import AtomicStructure, check_for_changes
from chimerax.core.commands import run
from unittest.mock import patch

root = Path(__file__).resolve().parents[1]
app = runpy.run_path(str(root / "scripts/headless_ui_fixture.py"))["install"](session)
spec = importlib.util.spec_from_file_location("chimerax.codex_bridge", root / "src/__init__.py",
    submodule_search_locations=[str(root / "src")])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
from chimerax.codex_bridge.ui_theme import style_model_panel
import chimerax.codex_bridge.model_chain_controls as chain_ui


def settle():
    check_for_changes(session)
    for _ in range(8):
        app.processEvents()


def add_residue(model, name, chain, number, definitions, x, y):
    residue = model.new_residue(name, chain, number)
    atoms = []
    for i, (name, element) in enumerate(definitions):
        atom = model.new_atom(name, element)
        residue.add_atom(atom)
        atom.coord = (x + i * 1.4, y, 0)
        atoms.append(atom)
    for a, b in zip(atoms, atoms[1:]):
        model.new_bond(a, b)
    return residue


model = AtomicStructure(session, name="Workflow complex")
for chain, y in (("A", 0), ("B", 20)):
    previous = None
    for number in range(1, 3):
        residue = add_residue(model, "ALA", chain, number,
            [("N", "N"), ("CA", "C"), ("C", "C"), ("O", "O")], number * 6, y)
        if previous is not None:
            model.new_bond(previous.find_atom("C"), residue.find_atom("N"))
        previous = residue
# Real PDB files often assign a ligand the same author chain ID as a protein.
ligand = add_residue(model, "ADP", "A", 201, [("C1", "C"), ("C2", "C"), ("O1", "O")], 30, 0)
ion = add_residue(model, "ZN", "A", 202, [("ZN", "Zn")], 34, 0)
model.ss_assigned = True
session.models.add([model])
model.atoms.displays = True
model.residues.ribbon_displays = False
coordination = model.pseudobond_group(model.PBG_METAL_COORDINATION)
metal_link = coordination.new_pseudobond(ion.atoms[0], ligand.atoms[-1])
measurement = model.pseudobond_group("user distances").new_pseudobond(ion.atoms[0], ligand.atoms[0])

from chimerax.model_panel.tool import model_panel
panel = model_panel(session, "Model Panel")
panel.countdown = 0
panel._fill_tree(always_rebuild=True)
style_model_panel(panel)
host = panel.tool_window.ui_area
host.setParent(None)
host.resize(400, 720)
host.show()
settle()
controls = panel._codex_chain_controls
issues = []


def atoms(chain):
    return model.residues.filter(model.residues.chain_ids == chain).atoms


def click_check(item, column):
    controls.tree.scrollToItem(item)
    settle()
    option = QStyleOptionViewItem()
    option.initFrom(controls.tree)
    option.rect = controls.tree.visualRect(controls.tree.indexFromItem(item, column))
    option.features |= QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator
    option.checkState = item.checkState(column)
    rect = controls.tree.style().subElementRect(QStyle.SubElement.SE_ItemViewItemCheckIndicator,
                                               option, controls.tree)
    assert not rect.isEmpty()
    QTest.mouseClick(controls.tree.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    settle()


row_b = controls.rows[(model, "B")]
click_check(row_b, 1)
assert controls.controller.state(atoms("B")) == "hidden", "Real mouse Show checkbox failed"
click_check(row_b, 1)
assert controls.controller.state(atoms("B")) == "shown"
click_check(row_b, 2)
assert atoms("B").selected.all() and not atoms("A").selected.any()
click_check(row_b, 2)
assert not atoms("B").selected.any()
row_a = controls.rows[(model, "A")]
click_check(row_a, 2)
assert metal_link.selected and not measurement.selected, "Row Select disagreed with molecule connection selection"
click_check(row_a, 2)
assert not metal_link.selected
assert controls.compounds.select_button.text() == "Select only"

# The entire cell is clickable, not only the small indicator at its left edge.
for column in (1, 2):
    for offset in (4, 25, 40):
        rect = controls.tree.visualRect(controls.tree.indexFromItem(row_b, column))
        before = row_b.checkState(column)
        QTest.mouseClick(controls.tree.viewport(), Qt.MouseButton.LeftButton,
            pos=QPoint(rect.left() + offset, rect.center().y()))
        settle()
        assert row_b.checkState(column) != before, (column, offset, "Cell did not toggle exactly once")
    # Reset using the same user input path.
    if row_b.checkState(column) != (Qt.CheckState.Checked if column == 1 else Qt.CheckState.Unchecked):
        click_check(row_b, column)

# A motion-only update must not rescan surface visibility. The positive display
# control ensures the spy observes real updates instead of a disconnected UI.
settle()
from chimerax.atomic import get_triggers
observed_reasons = []
probe_handler = get_triggers().add_handler("changes", lambda _name, changes: observed_reasons.append(
    sorted(set().union(changes.atom_reasons(), changes.residue_reasons(), changes.structure_reasons()))))
with patch.object(controls.controller, "state", wraps=controls.controller.state) as inspect_visibility:
    for _ in range(10):
        run(session, f"turn y 3 models #{model.id_string}")
        settle()
    model.atoms.coords = model.atoms.coords + (0.1, 0, 0)
    settle()
    probe_handler.remove()
    assert inspect_visibility.call_count == 0, ("Coordinate-only input rescanned every surface", observed_reasons)
    for residue in atoms("B").unique_residues:
        run(session, "select " + residue.atomspec)
        settle()
    assert inspect_visibility.call_count == 0, "Selection-only input rescanned every surface"
    assert row_b.checkState(2) == Qt.CheckState.PartiallyChecked
    appearance_reasons = []
    appearance_probe = get_triggers().add_handler("changes", lambda _name, changes: appearance_reasons.append(
        sorted(set().union(changes.atom_reasons(), changes.residue_reasons(), changes.structure_reasons()))))
    for command in (f"color #{model.id_string}/A red", f"transparency #{model.id_string} 20 target a",
                    "lighting soft", f"style #{model.id_string} sphere",
                    f"cartoon style #{model.id_string} width 2.5"):
        run(session, command)
        settle()
    appearance_probe.remove()
    assert inspect_visibility.call_count == 0, ("Appearance-only commands rescanned every surface", appearance_reasons)
    run(session, f"hide #{model.id_string}/B target acs")
    settle()
    assert inspect_visibility.call_count > 0 and row_b.checkState(1) == Qt.CheckState.Unchecked
run(session, f"show #{model.id_string}/B atoms")
run(session, "select clear")
settle()

controls.search.setText("ADP")
settle()
if controls.rows[(model, "A")].isHidden():
    issues.append("Ligand search misses ADP sharing a protein chain ID")
else:
    assert "ADP" in controls.rows[(model, "A")].text(0), "The matching ligand is not identified in the row"
controls.search.clear()
settle()

controls.tree.setCurrentItem(row_b, 0)
controls.tree.setFocus()
QTest.keyClick(controls.tree, Qt.Key.Key_Space)
settle()
if controls.controller.state(atoms("B")) != "hidden":
    issues.append("Space on the highlighted chain name does not toggle visibility")
else:
    QTest.keyClick(controls.tree, Qt.Key.Key_Space)
    settle()
QTest.keyClick(controls.tree, Qt.Key.Key_Space, Qt.KeyboardModifier.ShiftModifier)
settle()
assert atoms("B").selected.all() and controls.controller.state(atoms("B")) == "shown"
controls.tree.setCurrentItem(row_b, 2)
QTest.keyClick(controls.tree, Qt.Key.Key_Space)
settle()
assert not atoms("B").selected.any() and controls.controller.state(atoms("B")) == "shown"

controls.tree.setCurrentItem(row_b, 0)
controls.rows[(model, None)].setExpanded(False)
settle()
if controls.tree.currentItem() is row_b and controls.only_button.isEnabled():
    issues.append("Only this remains enabled for a highlighted chain hidden under a collapsed model")
controls.rows[(model, None)].setExpanded(True)

# A plugin reload must not forget the representation saved by a chain hide.
target = atoms("A")
pattern = np.zeros(len(target), dtype=bool)
pattern[1::4] = True
target.displays = pattern
target.unique_residues.ribbon_displays = False
controls.controller.set_visible(target, False)
settle()
saved_controller = controls.controller
import chimerax.codex_bridge.chain_visibility as visibility
importlib.reload(visibility)
chain_ui = importlib.reload(chain_ui)
replacement = chain_ui.install_model_chain_controls(panel)
assert replacement is not controls
assert replacement.controller is saved_controller, "Reload changed the controller referenced by Undo history"
assert replacement.tree.currentItem()._chain_key == (model, "B")
session.undo.undo()
settle()
assert np.array_equal(target.displays, pattern)
session.undo.redo()
settle()
assert replacement.controller.state(target) == "hidden"
replacement.controller.set_visible(target, True)
settle()
if not np.array_equal(target.displays, pattern) or target.unique_residues.ribbon_displays.any():
    issues.append("Reloading the panel discards the hidden chain's saved atom/cartoon style")

print("USER_WORKFLOW_FINDINGS " + json.dumps(issues))
replacement.cleanup()
host.close()
assert not issues, "; ".join(issues)
print("USER_WORKFLOWS_OK")
