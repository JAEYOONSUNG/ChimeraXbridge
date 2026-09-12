"""Run with ChimeraX --nogui --notools --exit --script <this file>.

Uses a disposable session with real Qt widgets, atomic structures and surfaces.
"""
import importlib.util
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from Qt.QtWidgets import QApplication, QScrollArea, QWidget
from chimerax.atomic import AtomicStructure, Element
from chimerax.core.commands import run
from chimerax.core.models import Model

app = QApplication.instance() or QApplication([])
source = Path(__file__).resolve().parents[1] / "src" / "display_controls.py"
package_spec = importlib.util.spec_from_file_location(
    "chimerax.codex_bridge", source.with_name("__init__.py"),
    submodule_search_locations=[str(source.parent)])
package = importlib.util.module_from_spec(package_spec)
sys.modules[package_spec.name] = package
package_spec.loader.exec_module(package)
spec = importlib.util.spec_from_file_location("chimerax.codex_bridge.display_controls", source)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

models = []
for name, rgba in (("first", (255, 0, 0, 51)), ("second", (0, 0, 255, 255))):
    model = AtomicStructure(session, name=name)
    for chain in ("A", "B"):
        residue = model.new_residue("ALA", chain, 1)
        atom = model.new_atom("CA", Element.get_element("C"))
        residue.add_atom(atom)
        atom.coord = (0 if chain == "A" else 15, 0, 0)
        atom.color = rgba
        atom.display = True
        residue.ribbon_color = rgba
    session.models.add([model])
    model.ss_assigned = True
    model.residues.is_helix = True
    model.atoms.colors = rgba
    model.atoms.displays = True
    model.residues.ribbon_colors = rgba
    models.append(model)

widget = module.DisplayControlsWidget(session)
widget.show()

def select(spec):
    run(session, "select " + spec)
    from chimerax.atomic import check_for_changes
    check_for_changes(session)
    session.triggers.activate_trigger("selection changed", None)
    app.processEvents()

def assert_percent(block, value):
    assert block.value() == value, (block.value(), value)
    assert block.value_label.value() == value, (block.value_label.value(), value)

def scene_state():
    """Catch accidental scene edits from inspecting or refreshing controls."""
    return tuple(
        (
            model.atoms.colors.tobytes(),
            model.atoms.displays.tobytes(),
            model.residues.ribbon_colors.tobytes(),
            model.residues.ribbon_displays.tobytes(),
            tuple(model.ribbon_xs_mgr.scale_helix),
            tuple(model.ribbon_xs_mgr.scale_sheet),
        )
        for model in models
    )

select("#1/A")
assert_percent(widget.atoms_transparency, 80)
assert_percent(widget.selection_transparency, 80)
assert widget.color_hex_edit.text() == "#ff0000"
first_heading = widget.scope_label.text()
first_count = len(widget._scope_atoms())
assert "#1" in first_heading and "/ A" in first_heading, first_heading
select("#2/A")
assert_percent(widget.atoms_transparency, 0)
assert widget.color_hex_edit.text() == "#0000ff"
assert len(widget._scope_atoms()) == first_count == 1
assert widget.scope_label.text() != first_heading
assert "#2" in widget.scope_label.text(), widget.scope_label.text()
assert not widget._pending, widget._pending

# A delayed edit must not spill onto a newly picked chain.
widget.atoms_transparency.set_value(47)
assert widget._pending
select("#1/A")
widget._apply_pending()
assert models[0].atoms[0].color[3] == 51
assert_percent(widget.atoms_transparency, 80)

# External commands refresh both the slider and its independent numeric editor.
run(session, "transparency #1/A 40 target a")
app.processEvents()
assert_percent(widget.atoms_transparency, 40)
assert not widget._pending

# Surface patches from another chain in the same structure must not leak.
run(session, "surface #1")
run(session, "transparency #1/A 70 target s")
run(session, "transparency #1/B 20 target s")
select("#1/A")
assert_percent(widget.surface_transparency, 70)
select("#1/B")
assert_percent(widget.surface_transparency, 20)
select("#2/A")
assert_percent(widget.surface_transparency, 0)
assert not widget.surface_visible_check.isChecked()

# Hidden panels refresh when brought back, including changes from model controls.
widget.hide()
models[1].atoms.colors = (0, 255, 0, 102)
from chimerax.atomic import check_for_changes
check_for_changes(session)
app.processEvents()
widget.show()
app.processEvents()
assert_percent(widget.atoms_transparency, 60)
assert widget.color_hex_edit.text() == "#00ff00"

# Selection-independent rendering settings must never enqueue edits on refresh.
widget.refresh()
assert not widget._pending
before = tuple(models[1].ribbon_xs_mgr.scale_helix)
select("#1/A")
widget.helix_width.set_value(35)
widget._apply_pending()
assert tuple(models[1].ribbon_xs_mgr.scale_helix) == before
assert widget.helix_width.value_label.value() == 3.5

# Equal-size selections within one model still have distinct live values.
models[1].atoms[1].color = (145, 185, 246, 255)
widget.color_target_combo.setCurrentIndex(widget.color_target_combo.findData("ab"))
select("#2/A")
chain_a_heading = widget.scope_label.text()
assert len(widget._scope_atoms()) == 1
assert_percent(widget.atoms_transparency, 60)
assert widget.color_hex_edit.text() == "#00ff00"
select("#2/B")
assert len(widget._scope_atoms()) == 1
assert_percent(widget.atoms_transparency, 0)
assert widget.color_hex_edit.text() == "#91b9f6"
assert widget.scope_label.text() != chain_a_heading
assert "/ B" in widget.scope_label.text(), widget.scope_label.text()
assert not widget._pending, widget._pending

# Mixed readouts must describe the whole target, including hidden atoms.
models[1].atoms.displays = (True, False)
models[1].residues.ribbon_displays = (True, False)
models[1].residues.ribbon_colors = ((0, 255, 0, 102), (145, 185, 246, 255))
select("#2")
assert widget.color_hex_edit.text() == ""
assert widget.color_hex_edit.placeholderText() == "Mixed"
assert "2 colors" in widget.color_name_label.text(), widget.color_name_label.text()
assert_percent(widget.atoms_transparency, 30)
assert_percent(widget.cartoon_transparency, 30)
for key in ("atoms", "cartoon"):
    toggle = getattr(widget, key + "_visible_check")
    assert toggle.isChecked()
    assert toggle.text() == "Mixed", (key, toggle.text())
    label = getattr(widget, key + "_state_label").text()
    assert "Mixed transparency" in label and "average" in label, (key, label)
assert not widget._pending, widget._pending

# A mixed visibility button hides its entire target, then shows it again.
widget.atoms_visible_check.click()
check_for_changes(session)
app.processEvents()
assert not models[1].atoms.displays.any()
assert widget.atoms_visible_check.text() == "Hidden"
widget.atoms_visible_check.click()
check_for_changes(session)
app.processEvents()
assert models[1].atoms.displays.all()
assert widget.atoms_visible_check.text() == "Shown"

# Leaving a mixed target must replace both its color and visibility labels.
select("#2/A")
assert widget.color_hex_edit.text() == "#00ff00"
assert widget.color_hex_edit.placeholderText() != "Mixed"
assert widget.color_name_label.text() == "#00FF00"
assert widget.atoms_visible_check.text() == "Shown"
assert "Mixed" not in widget.atoms_state_label.text()

# Multi-model aggregation and empty selection reset.
select("clear")
assert len(widget._scope_atoms()) == 4
assert_percent(widget.selection_transparency, 0)
assert not widget.selection_transparency.isEnabled()
assert widget.scope_mode_label.text() == "SCENE"

# ChimeraX's assistant dock releases explicit child widths when resizing.
# Exercise the compact layout under the same sizing conditions.
for child in widget.findChildren(QWidget):
    child.setMinimumWidth(0)
    child.setMaximumWidth(16777215)

# Refreshing and resizing the compact inspector must leave the scene untouched.
models[1].atoms.displays = (True, False)
select("#2")
before = scene_state()
widget.refresh_button.click()
app.processEvents()
assert not widget._pending, widget._pending
assert scene_state() == before, "Refresh button changed the scene"
widget.refresh()
app.processEvents()
assert not widget._pending, widget._pending
assert scene_state() == before, "Programmatic refresh changed the scene"
for width in (380, 520):
    widget.resize(width, 640)
    app.processEvents()
    assert widget.width() == width, (widget.width(), width)
    assert widget.height() == 640, widget.height()
    assert not widget._pending, (width, widget._pending)
    assert scene_state() == before, (width, "Resizing changed the scene")
    image_path = f"/tmp/display-controls-compact-{width}.png"
    assert widget.grab().save(image_path), image_path

# Short docks must preserve a readable selection header and scrollable body.
widget.resize(380, 360)
app.processEvents()
assert widget.scope_label.height() >= widget.scope_label.fontMetrics().height()
assert isinstance(widget.scroll_area, QScrollArea)
assert widget.scroll_area.widget() is not None
assert widget.scroll_area.viewport() is not None
assert widget.scroll_area.viewport().height() >= 80, widget.scroll_area.viewport().height()
assert not widget._pending, widget._pending
assert scene_state() == before, "Short dock resizing changed the scene"
widget.resize(430, 640)
app.processEvents()
assert widget.grab().save("/tmp/display-controls-test.png")

# A selected non-atomic model must not reuse the preceding molecular readouts.
non_atomic = Model("non-atomic selection test", session)
session.models.add([non_atomic])
select("#" + non_atomic.id_string)
assert module._has_selection(session)
scoped_atoms = widget._scope_atoms()
assert scoped_atoms is None or len(scoped_atoms) == 0
assert_percent(widget.selection_transparency, 0)
assert not widget.selection_transparency.isEnabled()
assert not widget.clear_transparency_button.isEnabled()
for key in ("atoms", "cartoon", "surface"):
    assert not getattr(widget, key + "_transparency").isEnabled(), key
    assert not getattr(widget, key + "_visible_check").isEnabled(), key
assert widget.color_hex_edit.text() == ""
assert widget.color_hex_edit.placeholderText() == "No color"
assert not widget._pending, widget._pending
session.models.close([non_atomic])

# Closing the final structures must clear readouts and disable atom-dependent edits.
session.models.close(models)
select("clear")
assert widget._scope_atoms() is None
assert widget.scope_label.text() == "Nothing open yet"
assert_percent(widget.selection_transparency, 0)
assert not widget.selection_transparency.isEnabled()
assert not widget.clear_transparency_button.isEnabled()
for key in ("atoms", "cartoon", "surface"):
    slider = getattr(widget, key + "_transparency")
    toggle = getattr(widget, key + "_visible_check")
    assert_percent(slider, 0)
    assert not slider.isEnabled(), key
    assert not toggle.isEnabled(), key
    assert not toggle.isChecked(), key
    assert "No atoms" in getattr(widget, key + "_state_label").text(), key
for key in ("protein", "helix", "strand"):
    assert not getattr(widget, key + "_width").isEnabled(), key
    assert not getattr(widget, key + "_thickness").isEnabled(), key
assert widget.color_hex_edit.text() == ""
assert widget.color_hex_edit.placeholderText() == "Open model"
assert not widget.apply_color_button.isEnabled()
assert not widget.pick_color_button.isEnabled()
assert not widget.rainbow_ramp.isEnabled()
assert not widget.color_transparency_slider.isEnabled()
assert not widget.saturation_slider.isEnabled()
assert not widget._pending, widget._pending
widget.cleanup()
assert not widget._handlers and not widget._apply_timer.isActive()
print("PASS: selection identity, numeric sync, colors, surface patches, stale edits, scoped style, "
      "mixed states, visibility actions, read-only refresh, compact screenshots, short dock scrolling, "
      "non-atomic selection, empty scene, cleanup")
