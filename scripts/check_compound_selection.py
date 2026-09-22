"""Real atomic selection and offscreen Qt checks for the Compounds section."""

import importlib.util
from pathlib import Path
import sys
from unittest.mock import patch

from Qt.QtCore import QPoint, QPointF, Qt
from Qt.QtGui import QWheelEvent
from Qt.QtWidgets import QApplication, QStyle, QStyleOptionButton, QStyleOptionComboBox
from PyQt6.QtTest import QTest
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
assert controls.select_button.text() == "Deselect"
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
choose(protein[0].atomspec)
before = selection()
with patch.object(controls, "_targets", side_effect=RuntimeError("discovery failed")):
    controls.select_button.click()
    assert selection() == before
    assert not controls.select_button.isEnabled() and not controls.select_button.isChecked()
    assert controls.error_label.isVisible() and "discovery failed" in controls.error_label.text()
controls.refresh()
assert controls.select_button.isEnabled()
with patch("chimerax.std_commands.select.select", side_effect=RuntimeError("selection failed")):
    controls.select_button.click()
    settle()
    assert selection() == before
    assert not controls.select_button.isChecked()
    assert "selection failed" in controls.error_label.text()
    widget.refresh()
    assert "selection failed" in controls.error_label.text(), "Refresh erased the action failure"
controls.select_button.click()
settle()
assert selection() == default and not controls.error_label.isVisible()
print("COMPOUND_ROUND_01_OK")

choose(protein[0].atomspec)
def nested_selection():
    controls._toggle_selection(True)
controls.selectionAboutToChange.connect(nested_selection)
from chimerax.std_commands.select import select as native_select
with patch("chimerax.std_commands.select.select", wraps=native_select) as select_spy:
    controls.select_button.click()
    assert select_spy.call_count == 1, "Reentrant callbacks repeated the native operation"
controls.selectionAboutToChange.disconnect(nested_selection)
settle()
assert selection() == default and not controls._selection_in_progress
ephemeral = AtomicStructure(session, name="Closing during selection")
residue(ephemeral, "LIG", "X", 1, [("C1", "C"), ("C2", "C")], 130)
ephemeral.ss_assigned = True
session.models.add([ephemeral])
choose(protein[0].atomspec)
def close_during_selection():
    session.models.close([ephemeral])
controls.selectionAboutToChange.connect(close_during_selection)
controls.select_button.click()
controls.selectionAboutToChange.disconnect(close_during_selection)
settle()
assert selection() == default and not controls.error_label.isVisible()
print("COMPOUND_ROUND_02_OK")

coordination = model.pseudobond_group(model.PBG_METAL_COORDINATION)
internal_pb = coordination.new_pseudobond(ion.atoms[0], ligand.atoms[0])
boundary_pb = coordination.new_pseudobond(ion.atoms[0], protein[0].atoms[0])
unrelated_pb = coordination.new_pseudobond(protein[0].atoms[0], protein[1].atoms[0])
choose(protein[0].atomspec)
controls.select_button.click()
settle()
assert internal_pb.selected, "Compound coordination was omitted from the selected target"
assert not boundary_pb.selected and not unrelated_pb.selected
session.undo.undo()
settle()
assert not internal_pb.selected and selection() == prior
session.undo.redo()
settle()
assert internal_pb.selected and selection() == default
unrelated_pb.selected = True
controls.select_button.click()
settle()
assert not internal_pb.selected and unrelated_pb.selected
print("COMPOUND_ROUND_03_OK")

choose("clear")
controls.mode_combo.setCurrentIndex(1)
settle()
assert controls.select_button.text() == "Select"
assert controls.select_button.accessibleName() == "Select nonprotein atoms"
assert "including hidden" in controls.scope_label.text()
assert "2 models" in controls.count_label.text()
assert standalone.name in controls.count_label.toolTip()
controls.select_button.click()
settle()
assert controls.select_button.text() == "Deselect"
assert controls.select_button.accessibleName() == "Deselect nonprotein atoms"
assert "atoms selected" in controls.select_button.accessibleDescription()
controls.mode_combo.setCurrentIndex(0)
choose("clear")
assert controls.select_button.accessibleName() == "Select compounds"
print("COMPOUND_ROUND_04_OK")

annotations = model.pseudobond_group("user distances")
measurement_pb = annotations.new_pseudobond(ligand.atoms[0], ion.atoms[0])
missing = model.pseudobond_group(model.PBG_MISSING_STRUCTURE)
missing_pb = missing.new_pseudobond(ligand.atoms[0], glycan.atoms[0])
measurement_pb.selected = True
controls.select_button.click()
settle()
assert internal_pb.selected and missing_pb.selected
assert not measurement_pb.selected, "A distance annotation was included as a molecular connection"
assert not boundary_pb.selected and not unrelated_pb.selected
session.undo.undo()
settle()
assert measurement_pb.selected and not missing_pb.selected
choose("clear")
print("COMPOUND_ROUND_05_OK")

def target_model(m):
    controls.model_combo.setCurrentIndex(next(
        i for i in range(controls.model_combo.count()) if controls.model_combo.itemData(i) is m))
    settle()

choose(protein[0].atomspec)
target_model(standalone)
assert selection() == prior, "Changing model scope modified the scene selection"
assert pointers(compound_atoms(session, model=standalone)) == pointers(small.atoms)
assert standalone.name in controls.model_combo.currentText()
assert "1 model" in controls.count_label.text()
controls.select_button.click()
settle()
assert selection() == pointers(small.atoms)
assert not model.atoms.selected.any()
target_model(model)
controls.mode_combo.setCurrentIndex(1)
controls.select_button.click()
settle()
assert selection() == everything_else - pointers(small.atoms)
target_model(None)
controls.mode_combo.setCurrentIndex(0)
choose("clear")
print("COMPOUND_ROUND_06_OK")

target_model(standalone)
standalone.name = "Renamed <compound> & control"
settle()
assert standalone.name in controls.model_combo.currentText()
assert controls.model_combo.currentData() is standalone
assert f"model #{standalone.id_string}" in controls.select_button.toolTip()
transient = AtomicStructure(session, name="Scope lifecycle")
residue(transient, "LIG", "T", 1, [("C1", "C"), ("C2", "C")], 150)
transient.ss_assigned = True
session.models.add([transient])
settle()
target_model(transient)
old_id = transient.id
choose(protein[0].atomspec)
session.models.close([transient])
settle()
assert not controls.select_button.isEnabled()
assert controls.model_combo.currentData() is transient
assert "no longer open" in controls.count_label.text()
replacement = AtomicStructure(session, name="Reused model number")
residue(replacement, "LIG", "T", 1, [("C1", "C"), ("C2", "C")], 160)
replacement.ss_assigned = True
replacement.id = old_id
session.models.add([replacement])
settle()
assert replacement.id == old_id
assert not controls.select_button.isEnabled(), "Model ID reuse silently retargeted the selection"
controls._toggle_selection(True)
assert selection() == prior
target_model(replacement)
controls.select_button.click()
settle()
assert selection() == pointers(replacement.atoms)
session.models.close([replacement])
target_model(None)
choose("clear")
print("COMPOUND_ROUND_07_OK")

from chimerax.codex_bridge.compound_selection import CompoundSelectionWidget
controls.mode_combo.setCurrentIndex(1)
target_model(standalone)
controls.expand_button.setChecked(False)
settle()
saved_selection = selection()
recreated = CompoundSelectionWidget(session)
recreated.show()
settle()
assert recreated.mode_combo.currentData() is True
assert recreated.model_combo.currentData() is standalone
assert not recreated.expand_button.isChecked() and not recreated.body.isVisible()
assert selection() == saved_selection
assert controls._settings.all_nonprotein and not controls._settings.expanded
recreated.close()
recreated.deleteLater()
controls.mode_combo.setCurrentIndex(0)
target_model(None)
controls.expand_button.setChecked(True)
settle()
assert not controls._settings.all_nonprotein and controls._settings.expanded
print("COMPOUND_ROUND_08_OK")

controls.expand_button.setChecked(False)
settle()
with patch.object(controls, "_targets", wraps=controls._targets) as discovery:
    for _ in range(10):
        controls.refresh()
    assert discovery.call_count == 0, "Collapsed controls rescanned all molecular atoms"
    choose(ligand.atomspec)
    controls.expand_button.setChecked(True)
    assert discovery.call_count == 1, "Expanding did not refresh the current selection"
assert "Partly selected" in controls.count_label.text()
settle()
with patch.object(controls, "_targets", wraps=controls._targets) as discovery:
    controls.select_button.click()
    assert discovery.call_count == 2, "Immediate feedback repeated discovery after resolving the live target"
settle()

benchmark = AtomicStructure(session, name="Residue-first performance fixture")
previous = None
for i in range(3000):
    r = residue(benchmark, "MSE" if i == 1 else "ALA", "P", i + 1,
        [("N", "N"), ("CA", "C"), ("C", "C"), ("O", "O")], i * 6)
    if previous is not None:
        benchmark.new_bond(previous.find_atom("C"), r.find_atom("N"))
    previous = r
free_amino = residue(benchmark, "ALA", "F", 1,
    [("N", "N"), ("CA", "C"), ("C", "C"), ("O", "O")], 19000)
heavy_water = residue(benchmark, "DOD", "W", 1, [("O", "O")], 20000)
benchmark.ss_assigned = True
session.models.add([benchmark])
settle()
assert benchmark.residues[1].polymer_type == Residue.PT_PROTEIN
assert free_amino.polymer_type == Residue.PT_NONE
assert set(heavy_water.atoms.structure_categories) == {"solvent"}
def atom_first_reference():
    atoms = benchmark.atoms
    return atoms.filter((atoms.residues.polymer_types == Residue.PT_NONE)
                        & (atoms.structure_categories != "solvent"))
assert pointers(compound_atoms(session, model=benchmark)) == pointers(atom_first_reference()) == pointers(free_amino.atoms)
from statistics import median
from time import perf_counter
def elapsed(action):
    timings = []
    for _ in range(15):
        start = perf_counter()
        action()
        timings.append(perf_counter() - start)
    return median(timings)
old_time = elapsed(atom_first_reference)
new_time = elapsed(lambda: compound_atoms(session, model=benchmark))
print(f"RESIDUE_FIRST_BENCH atoms={len(benchmark.atoms)} old_ms={old_time * 1000:.4f} new_ms={new_time * 1000:.4f}")
session.models.close([benchmark])
target_model(None)
choose("clear")
print("COMPOUND_ROUND_09_OK")

target_model(standalone)
choose(protein[0].atomspec)
def change_scope_during_selection():
    controls.model_combo.setCurrentIndex(0)
controls.selectionAboutToChange.connect(change_scope_during_selection)
controls.select_button.click()
controls.selectionAboutToChange.disconnect(change_scope_during_selection)
settle()
assert selection() == prior, "A callback broadened an in-flight per-model selection"
assert "Target changed" in controls.error_label.text()
controls.select_button.click()
settle()
assert selection() == default and not controls.error_label.isVisible()

# Real keyboard activation and larger fonts must work at the supported minimum.
choose("clear")
widget.resize(280, 330)
assert widget.width() == 300, "The panel allowed its controls to shrink below a readable width"
old_font = controls.font()
old_style = controls.styleSheet()
normal_height = controls.select_button.fontMetrics().height()
large_font = controls.font()
large_font.setPointSizeF(max(14.0, large_font.pointSizeF() * 1.5))
controls.setFont(large_font)
controls.setStyleSheet(old_style + "QComboBox, QPushButton { font-size: 14pt; }")
controls.mode_combo.setCurrentIndex(1)
settle()
assert controls.select_button.fontMetrics().height() > normal_height
for control in (controls.mode_combo, controls.select_button):
    control.ensurePolished()
    label_width = control.fontMetrics().horizontalAdvance(
        control.currentText() if control is controls.mode_combo else control.text())
    if control is controls.mode_combo:
        option = QStyleOptionComboBox()
        control.initStyleOption(option)
        text_rect = control.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox, option, QStyle.SubControl.SC_ComboBoxEditField, control)
    else:
        option = QStyleOptionButton()
        control.initStyleOption(option)
        text_rect = control.style().subElementRect(QStyle.SubElement.SE_PushButtonContents, option, control)
    assert text_rect.width() >= label_width, (text_rect.width(), label_width)
    assert control.height() >= control.fontMetrics().height(), (control.height(), control.fontMetrics().height())
    right = control.mapTo(widget.scroll_area.viewport(), QPoint(control.width(), 0)).x()
    assert right <= widget.scroll_area.viewport().width(), (right, widget.scroll_area.viewport().width())
widget.scroll_area.ensureWidgetVisible(controls.select_button)
controls.select_button.setFocus()
QTest.keyClick(controls.select_button, Qt.Key.Key_Space)
settle()
assert selection() == everything_else
QTest.keyClick(controls.select_button, Qt.Key.Key_Space)
settle()
assert not selection()
assert widget.grab().save("/tmp/compound-selection-large-font.png")
controls.setFont(old_font)
controls.setStyleSheet(old_style)
controls.mode_combo.setCurrentIndex(0)
widget.resize(320, 400)
bar.setValue(0)
settle()
assert widget.grab().save("/tmp/compound-selection-improved.png")
print("COMPOUND_ROUND_10_OK")

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
