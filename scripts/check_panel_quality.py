"""Compact AI/Action/Cavity panel integration without a visible desktop window."""
import importlib.util
from pathlib import Path
import runpy
import sys

from Qt.QtCore import QPoint, QPointF, Qt
from Qt.QtGui import QWheelEvent
from Qt.QtWidgets import QAbstractButton, QWidget

root = Path(__file__).resolve().parents[1]
fixture = runpy.run_path(str(root / 'scripts/headless_ui_fixture.py'))
app = fixture['install'](session)
spec = importlib.util.spec_from_file_location('chimerax.codex_bridge', root / 'src/__init__.py',
    submodule_search_locations=[str(root / 'src')])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
package._schedule_helper_dock_layout = lambda *args, **kwargs: None
from chimerax.codex_bridge.tool import CodexAssistant
from chimerax.codex_bridge.action_pad import CodexActionPad
from chimerax.codex_bridge.cavity_browser import CodexCavityBrowser

assistant = CodexAssistant(session, 'AI Assistant')
action = CodexActionPad(session, 'Action Pad')
cavity = CodexCavityBrowser(session, 'Cavity Browser')
assistant.prompt_edit.setPlainText('Keep this draft while scrolling.\nSelected chain only.')
draft = assistant.prompt_edit.toPlainText()
for _ in range(10):
    app.processEvents()
for tool in (assistant, action, cavity):
    host = tool.tool_window.ui_area
    host.setParent(None)
    host.show()
    for width, height in ((360, 150), (360, 640), (500, 220)):
        host.resize(width, height)
        for _ in range(6):
            app.processEvents()
        assert host.size().width() == width and host.size().height() == height
        scroll = tool.scroll_area
        assert scroll.horizontalScrollBar().maximum() == 0
        bar = scroll.verticalScrollBar()
        if bar.maximum():
            assert bar.width() == 6, (tool.tool_name, bar.width())
        # Wheel on the full form reaches its final actions.
        for _ in range(100):
            event = QWheelEvent(QPointF(2, 2), QPointF(2, 2), QPoint(0, -80), QPoint(),
                Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                Qt.ScrollPhase.NoScrollPhase, False)
            app.sendEvent(scroll.widget(), event)
        assert bar.value() == bar.maximum()
        buttons = [b for b in host.findChildren(QAbstractButton) if b.isVisibleTo(host)
                   and not b.isWindow() and b.window() is host.window()]
        bottom = max(buttons, key=lambda b: b.mapTo(scroll.widget(), QPoint()).y() + b.height())
        point = bottom.mapTo(scroll.viewport(), QPoint())
        assert point.y() >= 0 and point.y() + bottom.height() <= scroll.viewport().height(), tool.tool_name
        if width == 360 and height == 150:
            host.grab().save('/tmp/quality-' + tool.tool_name.replace(' ', '-').lower() + '.png')
assert assistant.prompt_edit.toPlainText() == draft
assert not session.models.list(), 'Inspecting forms unexpectedly created a model'
print('PANEL_QUALITY_OK: AI/Action/Cavity complete scrolling, 6px gutter, draft retained')
