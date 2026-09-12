"""Populated side panels and the image-export entry point, entirely offscreen."""
import hashlib
import importlib.util
import json
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace
from unittest.mock import patch
from PyQt6.QtTest import QTest

from Qt.QtCore import QPoint, QPointF, Qt
from Qt.QtGui import QWheelEvent
from Qt.QtWidgets import QAbstractButton, QAbstractScrollArea, QComboBox, QDoubleSpinBox, QSpinBox, QSlider, QFileDialog
from chimerax.atomic import AtomicStructure, check_for_changes
from chimerax.std_commands.view import NamedView, _named_views

root = Path(__file__).resolve().parents[1]
fixture = runpy.run_path(str(root/'scripts/headless_ui_fixture.py'))
app = fixture['install'](session)
spec = importlib.util.spec_from_file_location('chimerax.codex_bridge', root/'src/__init__.py',
    submodule_search_locations=[str(root/'src')])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
package._schedule_helper_dock_layout = lambda *args, **kwargs: None
from chimerax.codex_bridge.action_pad import CodexActionPad
from chimerax.codex_bridge.display_controls import CodexDisplayControls
from chimerax.codex_bridge.camera_bookmarks import CameraBookmarks
from chimerax.codex_bridge.cavity_browser import CodexCavityBrowser, set_cavity_candidates
from chimerax.codex_bridge.tool import CodexAssistant
from chimerax.codex_bridge.quick_results import QuickResults

models = []
for i in range(24):
    model = AtomicStructure(session, name=f'Comparison {i+1}: 긴 구조 이름 · chain interaction and ligand neighborhood')
    for chain in ('A', 'B'):
        for number in range(1, 7):
            residue = model.new_residue('ALA', chain, number)
            atom = model.new_atom('CA', 'C')
            residue.add_atom(atom)
            atom.coord = (number*3.8, i*12, 0 if chain == 'A' else 8)
    session.models.add([model])
    model.ss_assigned = True
    model.atoms.colors = (85, 122, 145, 180)
    models.append(model)
models[0].atoms[0].selected = True
check_for_changes(session)
for i in range(40):
    _named_views(session).views[f'Condition {i+1}: 긴 북마크 이름 · {"reference view " * 4}'] = NamedView(
        session.main_view, (0,0,0), session.models.list())
set_cavity_candidates(session, [{'rank': i+1, 'spec': models[i].atomspec, 'volume': i+123.45,
    'label': f'Candidate {i+1}: descriptive cavity result'} for i in range(24)])

assistant = CodexAssistant(session, 'AI Assistant')
action = CodexActionPad(session, 'Action Pad')
display = CodexDisplayControls(session, 'Display Controls')
bookmarks = CameraBookmarks(session, 'Camera Bookmarks')
cavity = CodexCavityBrowser(session, 'Cavity Browser')
quick = QuickResults(session, 'Quick Results')
assistant.prompt_edit.setPlainText('Keep selection #1/A:1 and these settings.\nDo not recolor other chains.')
bookmarks.name_input.setText('Publication figure draft')
bookmarks.export_lock_ratio.setChecked(False)
bookmarks.export_width.setValue(4800)
bookmarks.export_height.setValue(2400)
bookmarks.export_dpi.setValue(600)
bookmarks.export_format.setCurrentText('TIFF')
action.widget.refresh()
display.widget.refresh()
for _ in range(10):app.processEvents()


def scene():
    digest = hashlib.sha256()
    for m in models:
        digest.update(m.atoms.coords.tobytes())
        digest.update(m.atoms.colors.tobytes())
        digest.update(m.atoms.selected.tobytes())
        digest.update(m.atoms.displays.tobytes())
        digest.update(m.position.matrix.tobytes())
        digest.update(bytes([m.display]))
    digest.update(session.main_view.camera.position.matrix.tobytes())
    return digest.hexdigest()


def wheel(widget):
    event = QWheelEvent(QPointF(2,2), QPointF(2,2), QPoint(0,-73), QPoint(),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    app.sendEvent(widget, event)


before = scene()
draft = assistant.prompt_edit.toPlainText()
settings = (bookmarks.export_width.value(), bookmarks.export_height.value(), bookmarks.export_dpi.value(),
            bookmarks.export_format.currentText(), bookmarks.name_input.text())
results=[]
for tool in (assistant, action, display, bookmarks, cavity):
    host = tool.tool_window.ui_area
    host.setParent(None)
    host.show()
    scroll = tool.widget.scroll_area if tool is display else tool.scroll_area
    for width,height in ((360,180),(440,260),(600,460)):
        host.resize(width,height)
        for _ in range(8):app.processEvents()
        assert (host.width(),host.height()) == (width,height), (tool.tool_name,host.size())
        assert not scroll.horizontalScrollBar().maximum(), tool.tool_name
        assert scroll.widget().width() <= scroll.viewport().width(), tool.tool_name
        for _ in range(100):wheel(scroll.widget())
        bar=scroll.verticalScrollBar()
        assert bar.value()==bar.maximum(), tool.tool_name
        assert scroll.widget().mapTo(scroll.viewport(),QPoint(0,scroll.widget().height())).y() <= scroll.viewport().height()+1
        for control in scroll.widget().findChildren(QAbstractButton):
            if not control.isVisibleTo(host) or control.window() is not host.window():continue
            ancestor=control.parentWidget()
            while ancestor is not scroll.widget() and not isinstance(ancestor,QAbstractScrollArea):
                ancestor=ancestor.parentWidget()
            if ancestor is not scroll.widget():continue
            pos=control.mapTo(scroll.widget(),QPoint())
            assert pos.x()>=0 and pos.x()+control.width()<=scroll.widget().width()+1, (tool.tool_name,control.text())
            text=control.text().replace('&','')
            if text:
                assert max(control.fontMetrics().horizontalAdvance(line) for line in text.splitlines()) <= control.width()-8, (tool.tool_name,text,control.width())
        if width==360:
            assert host.grab().save('/tmp/populated-'+tool.tool_name.replace(' ','-').lower()+'.png')
        results.append((tool.tool_name,width,height))
    for control in scroll.widget().findChildren(QComboBox)+scroll.widget().findChildren(QSpinBox)+scroll.widget().findChildren(QDoubleSpinBox)+scroll.widget().findChildren(QSlider):
        if control.window() is not host.window():continue
        value=control.currentIndex() if isinstance(control,QComboBox) else control.value()
        wheel(control)
        assert value==(control.currentIndex() if isinstance(control,QComboBox) else control.value())

# Prior scrolling must not hide the destination of the Export image shortcut.
host=bookmarks.tool_window.ui_area
host.resize(360,280)
for _ in range(6):app.processEvents()
bookmarks.scroll_area.verticalScrollBar().setValue(bookmarks.scroll_area.verticalScrollBar().maximum())
assert bookmarks.export_format.mapTo(bookmarks.scroll_area.viewport(),QPoint()).y()<0
quick._export()
for _ in range(6):app.processEvents()
p=bookmarks.export_button.mapTo(bookmarks.scroll_area.viewport(),QPoint())
assert 0<=p.y() and p.y()+bookmarks.export_button.height()<=bookmarks.scroll_area.viewport().height(), 'Export shortcut left its action offscreen'
p=bookmarks.export_format.mapTo(bookmarks.scroll_area.viewport(),QPoint())
assert p.y()>=0, 'Export format is hidden after its shortcut'
assert host.grab().save('/tmp/populated-image-export-revealed.png')
# Validation happens before a file dialog, and requested settings survive its
# nested event loop. The supplied writer records options without invoking GL.
bookmarks.export_width.setValue(8000)
bookmarks.export_height.setValue(4000)
assert bookmarks.export_button.isEnabled()
bookmarks.export_width.setValue(8001)
assert not bookmarks.export_button.isEnabled()
assert 'maximum 32 MP' in bookmarks.export_size_hint.toolTip()
with patch.object(QFileDialog, 'getSaveFileName', side_effect=AssertionError('Invalid size opened dialog')):
    bookmarks._export_image()
bookmarks.export_width.setValue(4800)
bookmarks.export_height.setValue(2400)
recorded=[]
from chimerax.codex_bridge import image_export

def choose_file(*args, **kwargs):
    assert not bookmarks.export_button.isEnabled()
    bookmarks.export_width.setValue(3200)
    bookmarks.export_height.setValue(1800)
    bookmarks.export_dpi.setValue(300)
    bookmarks._export_image()  # A second queued click must not open another dialog.
    return '/tmp/populated-requested-figure.tif', ''

def supplied_export(session, path, **options):
    recorded.append(options)
    return dict(path=path, **options)

with patch.object(QFileDialog, 'getSaveFileName', side_effect=choose_file) as dialog, \
        patch.object(image_export, 'export_image', side_effect=supplied_export):
    bookmarks._export_image()
    assert dialog.call_count==1 and len(recorded)==1
assert (recorded[0]['width'],recorded[0]['height'],recorded[0]['dpi'],recorded[0]['format'])==(4800,2400,600,'TIFF')
assert bookmarks.export_button.isEnabled()
bookmarks.export_width.setValue(4800)
bookmarks.export_height.setValue(2400)
bookmarks.export_dpi.setValue(600)
# Panels no longer undo the user's subsequent main-window size change.
window=session.ui.main_window
window.resize(1350,900)
QTest.qWait(350)
assert (window.width(),window.height())==(1350,900)
assert settings==(bookmarks.export_width.value(),bookmarks.export_height.value(),bookmarks.export_dpi.value(),bookmarks.export_format.currentText(),bookmarks.name_input.text())
assert assistant.prompt_edit.toPlainText()==draft and scene()==before
assert bookmarks.list_widget.count()==40 and cavity.list_widget.count()==24
# Native parent teardown may happen inside the save dialog's event loop.
# Preserve the accepted file request without updating destroyed controls.
from PyQt6 import sip
old = bookmarks
# Use the real registry-retirement implementation with the offscreen host.
from chimerax.ui.gui import MainWindow
from types import MethodType
old.tool_window.tool_instance = old
window.tool_instance_to_windows = {old: [old.tool_window]}
window._hide_tools_shown_states = {old.tool_window: True}
window._hide_floating_tools_shown_states = {old.tool_window: False}
window._tool_window_destroyed = MethodType(MainWindow._tool_window_destroyed, window)
recorded.clear()
def close_export_parent(*args, **kwargs):
    sip.delete(old.tool_window.ui_area)
    return '/tmp/populated-closed-parent.tif', ''
with patch.object(QFileDialog, 'getSaveFileName', side_effect=close_export_parent), \
        patch.object(image_export, 'export_image', side_effect=supplied_export):
    old._export_image()
assert old._disposed and not old._export_busy and len(recorded)==1
reopened=CameraBookmarks.get_singleton(session)
assert reopened is not old and not reopened._disposed
assert old not in window.tool_instance_to_windows
assert old.tool_window not in window._hide_tools_shown_states
assert old.tool_window not in window._hide_floating_tools_shown_states
assert reopened.list_widget.count()==40
assert (reopened.export_width.value(),reopened.export_height.value(),reopened.export_dpi.value())==(4800,2400,600)
reopened.show_image_export()
reopened.delete()
sip.delete(reopened.tool_window.ui_area)
QTest.qWait(10)
assert scene()==before
print('PANEL_POPULATED_OK',json.dumps({'ok':True,'models':24,'bookmarks':40,'candidates':24,'geometries':len(results),'export_revealed':True,'state_preserved':True}))
