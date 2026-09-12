"""Upgrade the previous installed panels in a disposable native GUI session."""
import json
from pathlib import Path
import runpy

from Qt.QtCore import QPoint, Qt
from Qt.QtWidgets import (QApplication, QComboBox, QGridLayout, QLineEdit,
                          QPlainTextEdit, QPushButton, QScrollArea, QWidget)
from PyQt6.QtTest import QTest
from chimerax.atomic import AtomicStructure, check_for_changes
from chimerax.model_panel.tool import ModelPanel, model_panel

ROOT = Path(__file__).resolve().parents[1]
app = QApplication.instance()
QTest.qWait(5500)

# Deliberately use the installed version so this tests an upgrade, not another
# construction of the freshly edited source panel implementations.
from chimerax.codex_bridge.action_pad import CodexActionPad
from chimerax.codex_bridge.camera_bookmarks import CameraBookmarks
from chimerax.codex_bridge.caver import CodexCaverTool
from chimerax.codex_bridge.cavity_browser import CodexCavityBrowser
from chimerax.codex_bridge.display_controls import CodexDisplayControls
from chimerax.codex_bridge.quick_results import QuickResults
from chimerax.codex_bridge.tool import CodexAssistant
import chimerax.codex_bridge as package

assert "site-packages" in package.__file__, package.__file__
package._schedule_helper_dock_layout = lambda *args, **kwargs: None
model = AtomicStructure(session, name="Reload preservation fixture")
residue = model.new_residue("ALA", "A", 1)
atom = model.new_atom("CA", "C")
residue.add_atom(atom)
atom.coord = (4, 3, 2)
session.models.add([model])
atom.selected = True
model.atoms.colors = (53, 117, 131, 255)
model.ss_assigned = True
check_for_changes(session)

assistant = CodexAssistant.get_singleton(session)
display = CodexDisplayControls.get_singleton(session)
action = CodexActionPad.get_singleton(session)
bookmarks = CameraBookmarks.get_singleton(session)
quick = QuickResults.get_singleton(session)
caver = CodexCaverTool.get_singleton(session)
cavity = CodexCavityBrowser.get_singleton(session)
models = ModelPanel.get_singleton(session) or model_panel(session, "Model Panel")
models.countdown = 1
models._fill_tree(always_rebuild=True)
models.tree.setCurrentItem(models._items[0])
assistant._show_assistant_tab()
assistant.prompt_edit.setPlainText("Preserve this multiline draft.\nKeep the chosen target.")
assistant.terminal_edit.setPlainText("Earlier transcript\nUser and assistant context")
caver.caver_home_field.setText("/tmp/my pending caver directory")
caver.output.setPlainText("\n".join(f"Saved tunnel result {i}" for i in range(80)))
tools = [assistant, display, action, bookmarks, quick, caver, cavity, models]


def scene():
    return (model.id, model.display, model.atoms.coords.tobytes(),
            model.atoms.colors.tobytes(), model.atoms.displays.tobytes(),
            model.atoms.selected.tobytes(), model.position.matrix.tobytes(),
            model.residues.ribbon_colors.tobytes(),
            session.main_view.camera.position.matrix.tobytes(),
            tuple(session.main_view.background_color))


def state_widgets():
    fields = []
    for tool in tools:
        host = tool.tool_window.ui_area
        for widget in host.findChildren(QWidget):
            if isinstance(widget, QLineEdit):
                fields.append((widget, "text", widget.text()))
            elif isinstance(widget, QPlainTextEdit):
                fields.append((widget, "toPlainText", widget.toPlainText()))
            elif isinstance(widget, QComboBox):
                fields.append((widget, "currentIndex", widget.currentIndex()))
    return fields


def outer_scroll(host):
    for scroll in host.findChildren(QScrollArea):
        if not scroll.isVisibleTo(host):
            continue
        parent = scroll.parentWidget()
        while parent is not None and not isinstance(parent, QScrollArea):
            parent = parent.parentWidget()
        if parent is None:
            return scroll
    raise AssertionError("No whole panel scroll")


QTest.qWait(300)
before = scene()
fields = state_widgets()
identities = {tool: [child for child in tool.tool_window.ui_area.findChildren(QWidget)
                    if isinstance(child, (QPushButton, QLineEdit, QPlainTextEdit, QComboBox))]
              for tool in tools}
old_scrollers = {tool: outer_scroll(tool.tool_window.ui_area)
                 for tool in (assistant, display, bookmarks, quick)}
reload_script = ROOT / "scripts/reload_right_panel_scrolling.py"
scope = runpy.run_path(str(reload_script))
report = scope["reload_right_panel_scrolling"](session, report_path="/tmp/right-panel-reload-first.json")
QTest.qWait(100)
assert report["ok"], report
first_scrollers = {tool: outer_scroll(tool.tool_window.ui_area) for tool in tools}
for tool, old in old_scrollers.items():
    assert first_scrollers[tool] is not old, tool.tool_name
for tool, children in identities.items():
    current = tool.tool_window.ui_area.findChildren(QWidget)
    assert all(widget in current for widget in children), (tool.tool_name, "Control replaced")
differences = [(type(widget).__name__, widget.objectName(), method, value, getattr(widget, method)())
               for widget, method, value in fields if getattr(widget, method)() != value]
assert not differences, differences
assert scene() == before and models.tree.selectedItems() == [models._items[0]]
assert assistant.prompt_panel.parentWidget() is assistant.scroll_area.widget()
assert display.widget.scope_label.isAncestorOf(display.widget.scroll_area) is False
assert display.widget.scroll_area.widget().isAncestorOf(display.widget.scope_label)

for tool in tools:
    host = tool.tool_window.ui_area
    host.setParent(None)
    host.resize(360, 150)
    host.show()
    if tool is assistant:
        assistant._apply_responsive_layout(360)
    QTest.qWait(40)
    scroll = first_scrollers[tool]
    assert host.height() == 150, (tool.tool_name, host.height())
    assert scroll.verticalScrollBar().maximum() > 0, tool.tool_name
    scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
    QTest.qWait(10)
    bottom = scroll.widget().mapTo(scroll.viewport(), QPoint(0, scroll.widget().height())).y()
    assert bottom <= scroll.viewport().height() + 2, (tool.tool_name, bottom)

# Confirm the legacy three-column grid has become two actual equal columns.
for grid in caver.tool_window.ui_area.findChildren(QGridLayout):
    buttons = [grid.itemAt(i).widget() for i in range(grid.count())]
    if len(buttons) == 10 and all(isinstance(button, QPushButton) for button in buttons):
        assert grid.columnStretch(2) == 0, "Old third column retains blank space"
        for button in buttons:
            assert button.width() >= button.fontMetrics().horizontalAdvance(button.text()) + 14, button.text()

# Re-executing the actual script should leave all controls and scroll objects
# intact.  Let queued QObject deletion and child filter callbacks settle too.
scope = runpy.run_path(str(reload_script))
second = scope["reload_right_panel_scrolling"](session, report_path="/tmp/right-panel-reload-second.json")
QTest.qWait(300)
assert second["ok"] and scene() == before
assert all(outer_scroll(tool.tool_window.ui_area) is first_scrollers[tool] for tool in tools)
assert all(getattr(widget, method)() == value for widget, method, value in fields)
result = {"ok": True, "panels": [tool.tool_name for tool in tools],
          "previous_installed_layouts": True, "same_widget_identities": True,
          "drafts_selection_scene_preserved": True, "repeat_idempotent": True}
Path("/tmp/right-panel-reload-report.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print("RIGHT_PANEL_RELOAD_OK", json.dumps(result))
