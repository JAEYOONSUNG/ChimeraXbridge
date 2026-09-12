"""Run in a disposable GUI: ChimeraX --notools --exit --script <this file>."""
import json
from pathlib import Path
import numpy as np
from Qt.QtCore import Qt
from Qt.QtWidgets import QApplication
from PyQt6.QtTest import QTest
from chimerax.toolbar.tool import get_toolbar_singleton
from chimerax.codex_bridge import _install_runtime_toolbar_buttons, _schedule_helper_dock_layout
from chimerax.codex_bridge.panel_layout import set_panel_layout, panel_layout_mode, PanelLayoutSettings, _docks
from chimerax.codex_bridge.sequence_bar import CodexSequenceBar
from chimerax.codex_bridge.tool import CodexAssistant
from chimerax.codex_bridge.display_controls import CodexDisplayControls
from chimerax.atomic import AtomicStructure, Element, selected_atoms, check_for_changes
from chimerax.core.commands import run
from chimerax.cmd_line.tool import CommandLine
from chimerax.model_panel.tool import ModelPanel, model_panel

def stage(value):
    Path('/tmp/panel-layout-stage.txt').write_text(value)

stage('starting')
app=QApplication.instance()
window=session.ui.main_window
window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
window.showNormal()
window.resize(1600,1000)
session.tools.start_tools(["Log"])
CommandLine.get_singleton(session,create=True)
get_toolbar_singleton(session,create=True)
_install_runtime_toolbar_buttons(session,force_rebuild=True)
saved=panel_layout_mode(session)
REPORT=Path('/tmp/panel-layout-check.json')
REPORT.unlink(missing_ok=True)
set_panel_layout(session,'all',save=False)
assistant=CodexAssistant.get_singleton(session)
assistant.prompt_edit.setReadOnly(True)
assistant.prompt_run_button.setEnabled(False)
stage("startup wait")
QTest.qWait(6000)
stage("fixtures")
bar=CodexSequenceBar.get_singleton(session)
stage("bar obtained")
assistant=CodexAssistant.get_singleton(session)
display=CodexDisplayControls.get_singleton(session)
stage("display obtained")
model=AtomicStructure(session,name='Workspace layout validation')
res=model.new_residue('ALA','A',1)
for name,xyz in (('N',(0,0,0)),('CA',(1.4,0.3,0)),('C',(2.8,0,0))):
    atom=model.new_atom(name,Element.get_element('N' if name=='N' else 'C'))
    res.add_atom(atom)
    atom.coord=xyz
stage("adding model")
session.models.add([model])
stage("model added")
model.ss_assigned=True
model.atoms.colors=(167,143,204,255)
run(session,f'select #{model.id_string}')
check_for_changes(session)
assistant.prompt_edit.setPlainText('Keep this draft while arranging panels.')
QTest.qWait(300)
stage("refresh models")
models_tool=model_panel(session,"Model Panel")
pending=models_tool._frame_drawn_handler
if pending is not None:
    session.triggers.remove_handler(pending)
    models_tool._frame_drawn_handler=None
models_tool.countdown=1
model_panel(session,"Model Panel")._fill_tree(always_rebuild=True)
stage("refresh assistant")
models_tool=model_panel(session,"Model Panel")
models_tool.tree.setCurrentItem(models_tool._items[0])
assistant._refresh_workspace()
assert len(models_tool.tree.selectedItems())==1, "initial row was not selected"
models_before=list(session.models.list())
colors=model.atoms.colors.copy()
selection=selected_atoms(session).pointers.copy()
camera=session.main_view.camera.position.matrix.copy()
background=np.array(session.main_view.background_color).copy()
screens=[]

def scene_unchanged():
    assert list(session.models.list())==models_before
    assert np.array_equal(colors,model.atoms.colors)
    assert np.array_equal(selection,selected_atoms(session).pointers)
    assert np.array_equal(camera,session.main_view.camera.position.matrix)
    assert np.array_equal(background,session.main_view.background_color)
    assert assistant.prompt_edit.toPlainText()=='Keep this draft while arranging panels.'

try:
    for width,height in ((1600,1000),(2200,1250)):
        stage(f'all layout {width}')
        window.resize(width,height)
        set_panel_layout(session,'all',save=False)
        QTest.qWait(300)
        docks,_=_docks(session)
        primary=[docks[key] for key in ('models','ai assistant','display controls','action pad')]
        for dock in primary:
            assert window.dockWidgetArea(dock)==Qt.DockWidgetArea.BottomDockWidgetArea
            assert dock.isVisible() and not dock.visibleRegion().isEmpty(),dock.windowTitle()
            assert dock.width()>=280,(dock.windowTitle(),dock.width())
            assert not window.tabifiedDockWidgets(dock),(dock.windowTitle(),'still tabbed')
        command=CommandLine.get_singleton(session,create=False).tool_window._dock_widget
        assert command.height()<=50, ('command line expanded',command.height())
        assert command.width()>=window.width()-20, ('command line not full width',command.width(),window.width())
        assert command.y()>=max(d.geometry().bottom() for d in primary)
        assert models_tool.tree.topLevelItemCount()>=1, 'Models is empty after rearranging'
        assert len(models_tool.tree.selectedItems())==1, 'highlighted model row lost'
        rects=[dock.geometry() for dock in primary]
        assert max(r.top() for r in rects)-min(r.top() for r in rects)<=2,rects
        assert all(not a.intersects(b) for i,a in enumerate(rects) for b in rects[i+1:]),rects
        assert bar.search_edit.x()-bar.selection_button.geometry().right()<=8
        assert '#15181b' not in bar.bar_widget.styleSheet()
        a=bar.bar_widget.grab().toImage().pixelColor(2,2)
        b=docks['models'].widget().grab().toImage().pixelColor(2,2)
        assert a==b,('panel background mismatch',a.name(),b.name())
        origin=assistant.prompt_edit.mapTo(assistant.tool_window.ui_area,assistant.prompt_edit.rect().topLeft())
        assert origin.y()+assistant.prompt_edit.height()<=assistant.tool_window.ui_area.height()
        scene_unchanged()
        path=f'/tmp/workspace-all-{width}.png'
        window.grab().save(path)
        screens.append(path)

    # Delayed constructor/layout retries may raise a pane but must keep manual sizes.
    first=primary[0]
    window.resizeDocks([first],[first.width()+60],Qt.Orientation.Horizontal)
    QTest.qWait(150)
    resized=[d.geometry().getRect() for d in primary]
    _schedule_helper_dock_layout(session,raise_tool='display controls')
    QTest.qWait(1700)
    assert all(max(abs(a-b) for a,b in zip(old,d.geometry().getRect()))<=2 for old,d in zip(resized,primary))
    scene_unchanged()

    # The actual Panels menu switches layouts and persists the chosen mode.
    stage('tabs')
    bar.panel_layout_button._layout_actions['tabs'].trigger()
    QTest.qWait(250)
    assert panel_layout_mode(session)=='tabs'
    assert PanelLayoutSettings(session,'Codex Panel Layout').mode=='tabs'
    docks,_=_docks(session)
    anchor=docks['models']
    grouped=set(window.tabifiedDockWidgets(anchor))
    assert all(docks[key] in grouped for key in ('ai assistant','display controls','action pad'))
    assert window.dockWidgetArea(anchor)==Qt.DockWidgetArea.RightDockWidgetArea
    assert bar.panel_layout_button.text()=='Panels: Tabs'
    scene_unchanged()
    path='/tmp/workspace-tabs.png'
    window.grab().save(path)
    screens.append(path)
    bar.panel_layout_button._layout_actions['all'].trigger()
    QTest.qWait(250)
    assert panel_layout_mode(session)=='all'
    assert PanelLayoutSettings(session,'Codex Panel Layout').mode=='all'
    assert bar.panel_layout_button.text()=='Panels: All'
    scene_unchanged()
    report={'ok':True,'sequence_version':bar.UI_LAYOUT_VERSION,'layouts':['all','tabs'],'panels':4,'widths':[1600,2200],'state_preserved':True,'resize_preserved':True,'saved_preference':True,'screenshots':screens}
    stage('complete')
    REPORT.write_text(json.dumps(report,indent=2))
    print('PANEL_LAYOUT_OK',json.dumps(report))
finally:
    set_panel_layout(session,saved)
