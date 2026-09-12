"""Agent-confirmed export usability: presets, aspect and clear limit recovery."""
import importlib.util
import json
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace

from Qt.QtCore import QPoint
from Qt.QtWidgets import QToolButton
from chimerax.atomic import AtomicStructure

root=Path(__file__).resolve().parents[1]
app=runpy.run_path(str(root/'scripts/headless_ui_fixture.py'))['install'](session)
spec=importlib.util.spec_from_file_location('chimerax.codex_bridge',root/'src/__init__.py',submodule_search_locations=[str(root/'src')])
package=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=package
spec.loader.exec_module(package)
package._schedule_helper_dock_layout=lambda *args,**kwargs:None
from chimerax.codex_bridge.camera_bookmarks import CameraBookmarks
from chimerax.codex_bridge.image_export import MAX_PIXELS,MAX_DIMENSION
session._codex_image_export_settings=SimpleNamespace(format='PNG',width=1200,height=800,dpi=600,
    lock_ratio=False,transparent=True,directory='')
session._codex_bookmark_settings=SimpleNamespace(camera=True,visibility=True,colors=True,lighting=True,selection=False)
session.main_view.window_size=(1600,900)
model=AtomicStructure(session,name='Unchanged structure')
r=model.new_residue('ALA','A',1)
a=model.new_atom('CA','C');r.add_atom(a);a.coord=(0,0,0);a.selected=True
session.models.add([model])
panel=CameraBookmarks(session,'Camera Bookmarks')
host=panel.tool_window.ui_area
host.setParent(None);host.show();host.resize(360,280)
for _ in range(8):app.processEvents()


def image_state():
    return (panel.export_dpi.value(),panel.export_format.currentText(),panel.export_transparent.isChecked(),panel.export_lock_ratio.isChecked())


def scene():
    return (model.atoms.coords.tobytes(),model.atoms.colors.tobytes(),model.atoms.selected.tobytes(),
            model.position.matrix.tobytes(),session.main_view.camera.position.matrix.tobytes())


def size():return panel.export_width.value(),panel.export_height.value()


def preset(key):
    panel._refresh_export_size_menu()
    panel._export_size_actions[key].trigger()


def set_size(width,height):
    panel.export_lock_ratio.setChecked(False)
    panel.export_width.setValue(width);panel.export_height.setValue(height)


initial=image_state();original_scene=scene()
assert isinstance(panel.export_current_button,QToolButton)
assert panel.export_current_button.popupMode()==QToolButton.ToolButtonPopupMode.MenuButtonPopup
preset(2400)
assert size()==(2400,1600) and image_state()==initial
preset(3840)
assert size()==(3840,2560) and image_state()==initial
preset(1600)
assert size()==(1600,1067) and image_state()==initial
panel.export_current_button.click()
assert size()==(1600,900), 'Main Current click lost its original behavior'
preset('view2x')
assert size()==(3200,1800) and image_state()==initial

# Portrait presets preserve image ratio even when free width/height editing is on.
set_size(800,1200)
preset(2400)
assert size()==(2400,3600)
set_size(1200,800)
panel.export_lock_ratio.setChecked(True)
preset(2400)
assert size()==(2400,1600) and panel.export_lock_ratio.isChecked()
panel.export_width.setValue(3000)
assert size()==(3000,2000), 'Preset failed to update subsequent locked-ratio editing'

# Oversized requests offer explicit repair; menu inspection alone never edits.
for width,height in ((MAX_DIMENSION,MAX_DIMENSION),(12000,4000),(4000,12000),(8001,4000)):
    set_size(width,height)
    panel._refresh_export_size_menu()
    assert size()==(width,height) and not panel.export_button.isEnabled()
    assert 'Fit to limit' in panel.export_size_hint.toolTip()
    assert panel._export_size_actions['fit'].isEnabled()
    preset('fit')
    w,h=size()
    assert w*h<=MAX_PIXELS and w<=width and h<=height
    assert abs(w/width-h/height)<=1/min(width,height), 'Repair distorted image shape'
    assert panel.export_button.isEnabled()
    panel._refresh_export_size_menu()
    assert not panel._export_size_actions['fit'].isEnabled(), 'Repair would unnecessarily upscale'
    assert panel.export_dpi.value()==600 and panel.export_transparent.isChecked()

# Impossible narrow/tall shape must not silently clamp the requested height.
set_size(16,MAX_DIMENSION)
panel._refresh_export_size_menu()
for key in (1600,2400,3840):
    action=panel._export_size_actions[key]
    assert not action.isEnabled() and 'exceeds' in action.toolTip()
    panel._apply_export_size_preset(key)
    assert size()==(16,MAX_DIMENSION)
session.main_view.window_size=(6000,4000)
panel._refresh_export_size_menu()
assert not panel._export_size_actions['view2x'].isEnabled()
session.main_view.window_size=(1600,900)
panel.export_current_button.click()

# A pending file operation cannot have its request resized by a queued preset.
before=size();panel._export_busy=True
panel._apply_export_size_preset(3840)
assert size()==before
panel._export_busy=False;panel._export_options_changed()
assert scene()==original_scene

for width,height in ((360,180),(360,280),(500,280)):
    host.resize(width,height)
    for _ in range(6):app.processEvents()
    panel.show_image_export()
    for _ in range(6):app.processEvents()
    assert (host.width(),host.height())==(width,height)
    assert not panel.scroll_area.horizontalScrollBar().maximum()
    for control in (panel.export_current_button,panel.export_lock_ratio,panel.export_width,panel.export_height,panel.export_dpi):
        p=control.mapTo(panel.content,QPoint())
        assert p.x()>=0 and p.x()+control.width()<=panel.content.width()
    if width==360 and height==280:
        assert host.grab().save('/tmp/usability-export-presets.png')
assert panel.export_current_button.fontMetrics().horizontalAdvance(panel.export_current_button.text())+18<=panel.export_current_button.width()
print('USABILITY_EXPORT_OK',json.dumps({'ok':True,'presets':6,'aspect_preserved':True,'explicit_limit_recovery':True,'settings_and_scene_preserved':True,'same_row_geometry':True}))
