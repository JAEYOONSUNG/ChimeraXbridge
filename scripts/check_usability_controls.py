"""Exercise control recovery and persistent navigation with offscreen Qt only."""
import importlib.util
from pathlib import Path
import runpy
import sys

from Qt.QtCore import QPoint, Qt
from chimerax.atomic import AtomicStructure, Element
from chimerax.core.commands import run

root = Path(__file__).resolve().parents[1]
fixture = runpy.run_path(str(root / "scripts/headless_ui_fixture.py"))
app = fixture["install"](session)
spec = importlib.util.spec_from_file_location("chimerax.codex_bridge", root / "src/__init__.py",
    submodule_search_locations=[str(root / "src")])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
package._schedule_helper_dock_layout = lambda *args, **kwargs: None
from chimerax.codex_bridge import action_pad, cavity_browser, display_controls


def settle():
    for _ in range(6):
        app.processEvents()


def snapshot():
    return tuple((m.id_string, m.atoms.colors.tobytes(), m.atoms.displays.tobytes(),
                  m.atoms.selected.tobytes(), m.residues.ribbon_colors.tobytes(),
                  m.residues.ribbon_displays.tobytes(), m.scene_position.matrix.tobytes())
                 for m in models)


models = []
for index in range(6):
    model = AtomicStructure(session, name="Navigation model " + str(index + 1))
    for chain in "ABCDEFGHIJKLMNOPQRST":
        previous = None
        for number in range(1, 4):
            residue = model.new_residue("ALA", chain, number)
            backbone = []
            for name, element, offset in (("N", "N", 0), ("CA", "C", 1.4), ("C", "C", 2.4)):
                atom = model.new_atom(name, Element.get_element(element))
                residue.add_atom(atom)
                atom.coord = ((number - 1) * 3.8 + offset, index * 20, (ord(chain) - ord("A")) * 6)
                atom.color = (50, 90, 140, 255)
                atom.display = True
                backbone.append(atom)
            model.new_bond(backbone[0], backbone[1])
            model.new_bond(backbone[1], backbone[2])
            if previous is not None:
                model.new_bond(previous, backbone[0])
            previous = backbone[2]
    session.models.add([model])
    model.ss_assigned = True
    models.append(model)

# Refreshing a long tree preserves deliberate collapsed branches and position.
action = action_pad.CodexActionPad(session, "Action Pad")
host = action.tool_window.ui_area
host.setParent(None)
host.resize(360, 420)
host.show()
action.widget.refresh()
settle()
tree = action.widget.tree
model_section = tree.topLevelItem(1)
model_section.child(0).setExpanded(False)
model_section.child(3).setExpanded(False)
tree.verticalScrollBar().setValue(24)
settle()
position = tree.verticalScrollBar().value()
assert position > 0
state = snapshot()
action.widget.refresh()
settle()
assert not tree.topLevelItem(1).child(0).isExpanded()
assert not tree.topLevelItem(1).child(3).isExpanded()
assert tree.verticalScrollBar().value() == position, (position, tree.verticalScrollBar().value())
assert snapshot() == state, "Refreshing navigation changed the scene"
tree.topLevelItem(1).setExpanded(False)
action.widget.refresh()
assert not tree.topLevelItem(1).isExpanded(), "Section collapse was lost"

# A new scene selection remains the action target, even with Models collapsed.
run(session, "select #2/B:1")
action.widget.refresh()
assert action.widget._current_spec() == "#2/B:1", action.widget._current_spec()
assert not tree.topLevelItem(1).isExpanded()

# No target is explained beside Color, without an enabled no-op action.
run(session, "select clear")
display = display_controls.DisplayControlsWidget(session)
display.resize(360, 600)
display.show()
settle()
color_buttons = (display.pick_color_button, display.apply_color_button,
                 display.by_chain_button, display.by_element_button, display.by_model_button)
assert all(not b.isEnabled() for b in color_buttons)
assert "Select" in display.color_hex_edit.placeholderText()
assert "All models" in display.color_hex_edit.toolTip()
state = snapshot()
display.color_scope_combo.setCurrentIndex(display.color_scope_combo.findData("all"))
settle()
assert all(b.isEnabled() for b in color_buttons)
assert snapshot() == state, "Changing the inspection scope recolored the scene"

# Hidden representations can still be colored; missing preview is not missing target.
for model in models:
    model.residues.ribbon_displays = False
display.color_target_combo.setCurrentIndex(display.color_target_combo.findData("c"))
display.refresh()
assert display.color_hex_edit.isEnabled()
assert all(b.isEnabled() for b in color_buttons)
assert display.color_hex_edit.placeholderText() == "No color"
assert "No displayed color" in display.color_hex_edit.toolTip()

# Selecting a non-atomic model is still a valid native color scope.
from chimerax.core.models import Model
non_atomic = Model("Non-atomic color target", session)
session.models.add([non_atomic])
run(session, "select " + non_atomic.atomspec)
display.color_scope_combo.setCurrentIndex(display.color_scope_combo.findData("sel"))
display.color_target_combo.setCurrentIndex(display.color_target_combo.findData("m"))
display.refresh()
assert display.color_hex_edit.isEnabled() and all(b.isEnabled() for b in color_buttons)
assert display.color_hex_edit.placeholderText() == "No color"

# An explicit empty cavity selection and opaque output survive open/refresh.
candidates = [{"rank": 1, "spec": "#1/A:1", "volume": 120},
              {"rank": 2, "spec": "#1/B:1", "volume": 80}]
commands = []
cavity_browser._run = lambda _session, command: commands.append(command)
cavity_browser.set_cavity_candidates(session, candidates, selected_ranks=[], transparency=0)
cavity = cavity_browser.CodexCavityBrowser(session, "Cavity Browser")
assert cavity._selected_ranks() == []
assert cavity.transparency_spin.value() == 0
assert not cavity.select_lining_button.isEnabled()
assert "All hidden" in cavity.summary_label.text()
cavity.reload_from_session()
assert cavity._selected_ranks() == [] and not commands
cavity.show_all_button.click()
assert cavity._selected_ranks() == [1, 2]
assert cavity.select_lining_button.isEnabled()
assert any("transparency #1/A:1 0" in command for command in commands)
cavity.hide_all_button.click()
assert cavity._selected_ranks() == []
commands.clear()
cavity.reload_from_session()
assert cavity._selected_ranks() == []
assert not cavity.select_lining_button.isEnabled()
reopened = cavity_browser.CodexCavityBrowser(session, "Cavity Browser check reopen")
assert reopened._selected_ranks() == []
assert reopened.transparency_spin.value() == 0
assert not commands, "Inspecting hidden cavities changed the scene"

# First-run defaults stay useful, and empty data offers an actionable explanation.
cavity_browser.set_cavity_candidates(session, candidates)
cavity.reload_from_session()
assert cavity._selected_ranks() == [1]
cavity_browser.set_cavity_candidates(session, [], selected_ranks=[])
cavity.reload_from_session()
assert "Run the Cavity action" in cavity.summary_label.text()
assert not cavity.show_all_button.isEnabled()
assert not cavity.hide_all_button.isEnabled()
assert not cavity.select_lining_button.isEnabled()

# The more explicit transparency label still fits the narrow scrolling panel.
cavity_browser.set_cavity_candidates(session, candidates, selected_ranks=[], transparency=0)
cavity.reload_from_session()
host = cavity.tool_window.ui_area
host.setParent(None)
host.resize(360, 220)
host.show()
settle()
assert host.width() == 360
assert cavity.scroll_area.horizontalScrollBar().maximum() == 0
content = cavity.scroll_area.widget()
for control in (cavity.transparency_spin, cavity.show_all_button, cavity.hide_all_button):
    left = control.mapTo(content, QPoint()).x()
    assert left >= 0 and left + control.width() <= content.width(), (control, left, content.width())
host.grab().save("/tmp/usability-cavity-controls.png")
display.grab().save("/tmp/usability-display-controls.png")
action.widget.cleanup()
session.models.close([non_atomic] + models)
display.refresh()
assert display.color_hex_edit.placeholderText() == "Open model"
assert all(not b.isEnabled() for b in color_buttons)
assert display.color_scope_combo.isEnabled()
display.cleanup()
print("USABILITY_CONTROLS_OK: tree position/collapse; explicit color scope; hidden cavities/opaque persistence; narrow controls")
