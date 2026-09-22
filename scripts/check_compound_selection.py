"""Real atomic selection and offscreen Qt checks for the Compounds section."""

import importlib.util
from pathlib import Path
import sys

from Qt.QtCore import QPoint, QPointF, Qt
from Qt.QtGui import QWheelEvent
from Qt.QtWidgets import QApplication
from chimerax.atomic import AtomicStructure, Residue, check_for_changes, selected_atoms
from chimerax.core.commands import run
from chimerax.core.models import Model

root = Path(__file__).resolve().parents[1]
app = QApplication.instance() or QApplication([])
spec = importlib.util.spec_from_file_location(
    "chimerax.codex_bridge", root / "src/__init__.py",
    submodule_search_locations=[str(root / "src")])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
from chimerax.codex_bridge.compound_selection import compound_atoms
from chimerax.codex_bridge.display_controls import DisplayControlsWidget

widget = DisplayControlsWidget(session)
widget.resize(320, 330)
widget.show()
controls = widget.compound_selection


def settle():
    check_for_changes(session)
    for _ in range(5):
        app.processEvents()


def residue(model, name, chain, number, atom_defs, offset):
    r = model.new_residue(name, chain, number)
    atoms = []
    for i, (atom_name, element) in enumerate(atom_defs):
        a = model.new_atom(atom_name, element)
        r.add_atom(a)
        a.coord = (offset + i * 1.4, 0, 0)
        atoms.append(a)
    for a, b in zip(atoms, atoms[1:]):
        model.new_bond(a, b)
    return r


def pointers(atoms):
    return set(atoms.pointers)


def selection():
    return pointers(selected_atoms(session))


def choose(command):
    run(session, "select " + command)
    settle()


settle()
assert not controls.select_button.isEnabled()
assert "No matching" in controls.count_label.text()

model = AtomicStructure(session, name="Protein, DNA, ligand, ion, water and glycan")
protein = []
for i in range(2):
    protein.append(residue(model, "ALA", "A", i + 1,
        [("N", "N"), ("CA", "C"), ("C", "C"), ("O", "O")], i * 6))
model.new_bond(protein[0].find_atom("C"), protein[1].find_atom("N"))
nucleic = []
for i in range(2):
    nucleic.append(residue(model, "DA", "D", i + 1,
        [("P", "P"), ("O5'", "O"), ("C5'", "C"), ("C4'", "C"), ("C3'", "C"), ("O3'", "O")], 30 + i * 9))
model.new_bond(nucleic[0].find_atom("O3'"), nucleic[1].find_atom("P"))
ligand = residue(model, "ATP", "L", 201, [("C1", "C"), ("C2", "C"), ("O1", "O")], 60)
ion = residue(model, "ZN", "Z", 301, [("ZN", "Zn")], 70)
water = residue(model, "HOH", "W", 401, [("O", "O")], 80)
glycan = residue(model, "NAG", "A", 99, [("C1", "C"), ("O1", "O"), ("C2", "C")], 90)
model.new_bond(protein[0].find_atom("CA"), glycan.find_atom("C1"))
model.ss_assigned = True
session.models.add([model])

standalone = AtomicStructure(session, name="Standalone compound")
small = residue(standalone, "ATP", "", 201, [("C1", "C"), ("C2", "C"), ("O1", "O")], 110)
standalone.ss_assigned = True
session.models.add([standalone])
non_atomic = Model("Unrelated map", session)
session.models.add([non_atomic])
settle()

# These independent positive controls make exclusion assertions meaningful.
assert all(r.polymer_type == Residue.PT_PROTEIN for r in protein)
assert all(r.polymer_type == Residue.PT_NUCLEIC for r in nucleic)
assert set(water.atoms.structure_categories) == {"solvent"}
assert set(small.atoms.structure_categories) == {"main"}
default = set().union(*(pointers(r.atoms) for r in (ligand, ion, glycan, small)))
everything_else = default | pointers(water.atoms) | set().union(*(pointers(r.atoms) for r in nucleic))
assert pointers(compound_atoms(session)) == default
assert pointers(compound_atoms(session, all_nonprotein=True)) == everything_else
assert controls.select_button.isEnabled(), "Model opening did not update the section"

# Selection replaces a prior protein target and includes hidden compounds.
choose(protein[0].atomspec)
prior = selection()
ion.atoms.displays = False
standalone.display = False
appearance = tuple((m.atoms.displays.tobytes(), m.atoms.colors.tobytes(),
                    m.atoms.coords.tobytes(), m.display) for m in (model, standalone))
widget.atoms_transparency.set_value(47)
assert widget._pending
controls.select_button.click()
# Selection triggers arrive on the next frame; pending edits must already be
# canceled synchronously, before anything can apply them to the new compounds.
assert not widget._pending and not widget._apply_timer.isActive()
widget._apply_pending()
settle()
assert selection() == default
assert controls.select_button.isChecked()
assert controls.select_button.text() == "Deselect compounds"
assert not non_atomic.selected
assert all(b.selected for r in (ligand, glycan, small) for b in r.atoms.intra_bonds)
assert appearance == tuple((m.atoms.displays.tobytes(), m.atoms.colors.tobytes(),
                           m.atoms.coords.tobytes(), m.display) for m in (model, standalone))
session.undo.undo()
settle()
assert selection() == prior
assert not controls.select_button.isChecked()
session.undo.redo()
settle()
assert selection() == default and controls.select_button.isChecked()

# Turning off removes only compound atoms, including their selected boundary
# bonds, and retains a protein/map selection that was added afterward.
choose("add " + protein[0].atomspec)
choose("add #" + non_atomic.id_string)
assert controls.select_button.isChecked()
controls.select_button.click()
settle()
assert selection() == prior and non_atomic.selected
assert not controls.select_button.isChecked()
assert not any(b.selected for b in glycan.atoms.bonds)
session.undo.undo()
settle()
assert selection() == default | prior and non_atomic.selected

# External partial/clear selections update the toggle without applying changes.
choose(ligand.atomspec)
assert not controls.select_button.isChecked()
assert "Partly selected" in controls.count_label.text()
assert f"{len(ligand.atoms)} / {len(default)} atoms" in controls.count_label.text()
before = selection()
widget.refresh()
settle()
assert selection() == before and not widget._pending
choose("clear")
assert not controls.select_button.isChecked()
assert "0 /" in controls.count_label.text()

# An external selection can change before the next frame refreshes the button.
# Use live atom state when clicked, even while the button still looks checked.
controls.select_button.click()
settle()
assert controls.select_button.isChecked()
run(session, "select clear")
assert controls.select_button.isChecked()
controls.select_button.click()
settle()
assert selection() == default and controls.select_button.isChecked()

# Broader scope is explicit and changing its mode alone never edits selection.
choose(protein[0].atomspec)
controls.mode_combo.setCurrentIndex(1)
settle()
assert selection() == prior
controls.select_button.click()
settle()
assert selection() == everything_else
assert not non_atomic.selected
controls.select_button.click()
settle()
assert not selection()
controls.mode_combo.setCurrentIndex(0)
settle()

# Wheel gestures scroll the existing tab and never switch selection mode.
bar = widget.scroll_area.verticalScrollBar()
assert bar.maximum() > 0 and widget.height() == 330
bar.setValue(0)
position = controls.mode_combo.rect().center()
event = QWheelEvent(QPointF(position), QPointF(controls.mode_combo.mapToGlobal(position)),
    QPoint(), QPoint(0, -120), Qt.MouseButton.NoButton,
    Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
QApplication.sendEvent(controls.mode_combo, event)
settle()
assert controls.mode_combo.currentIndex() == 0 and bar.value() > 0
assert not selection()
bar.setValue(bar.maximum())
before_scroll = bar.value()
widget.refresh()
settle()
assert bar.value() == before_scroll
widget.scroll_area.ensureWidgetVisible(widget.silhouette_check)
settle()
last = widget.silhouette_check.mapTo(widget.scroll_area.viewport(), QPoint(0, widget.silhouette_check.height()))
assert 0 < last.y() <= widget.scroll_area.viewport().height()

# Collapse state is stable through selection refreshes and tab visibility.
controls.expand_button.click()
settle()
assert not controls.body.isVisible()
collapsed_maximum = bar.maximum()
choose(ligand.atomspec)
widget.hide()
widget.show()
settle()
assert not controls.expand_button.isChecked() and not controls.body.isVisible()
controls.expand_button.click()
settle()
assert controls.body.isVisible() and bar.maximum() > collapsed_maximum
bar.setValue(0)
settle()
assert widget.grab().save("/tmp/compound-selection-controls.png")

# Atom deletion and model closing cannot retain stale targets or counts.
ligand.atoms[0].delete()
settle()
default = set().union(*(pointers(r.atoms) for r in (ligand, ion, glycan, small)))
assert pointers(compound_atoms(session)) == default
assert f"/ {len(default)} atoms" in controls.count_label.text()
session.models.close([standalone])
settle()
assert pointers(compound_atoms(session)) == set().union(*(pointers(r.atoms) for r in (ligand, ion, glycan)))
session.models.close([model])
settle()
assert not controls.select_button.isEnabled() and not controls.select_button.isChecked()
assert "No matching" in controls.count_label.text()
choose("#" + non_atomic.id_string)
controls._toggle_selection(True)
assert non_atomic.selected, "An empty target cleared unrelated selection"
session.models.close([non_atomic])
widget.cleanup()
assert not widget._handlers
widget.close()
settle()
print("COMPOUND_SELECTION_OK")
