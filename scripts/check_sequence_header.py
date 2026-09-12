"""ChimeraX --notools --exit --script <this file>; disposable GUI session only."""
import importlib
import json
import math
import runpy
import tempfile
from pathlib import Path

from Qt.QtCore import Qt
from Qt.QtGui import QTextCursor
from Qt.QtWidgets import QApplication, QWidget
from PyQt6.QtTest import QTest
from chimerax.atomic import selected_residues, check_for_changes
from chimerax.core.commands import run
from chimerax.codex_bridge.sequence_bar import CodexSequenceBar
from chimerax.codex_bridge.icon_theme import icon_theme, set_icon_theme
from chimerax.codex_bridge.runtime_patches import style_ai_toolbar
from chimerax.toolbar.tool import get_toolbar_singleton

assert session.ui.is_gui
REPORT = Path('/tmp/sequence-header-check.json')
REPORT.unlink(missing_ok=True)
app = QApplication.instance()
# Create the toolbar before waiting: --notools deliberately omits it, while
# the bridge has startup retries waiting for a toolbar to become available.
tool = get_toolbar_singleton(session, create=True)
from chimerax.codex_bridge import _install_runtime_toolbar_buttons
_install_runtime_toolbar_buttons(session, force_rebuild=True)
QTest.qWait(6000)  # Finish startup toolbar/dock callbacks before measuring.

# A local, artificial backbone supplies real chains/residue picking without fetching data.
def open_chain(chain, name):
    residues = ('ALA', 'GLY', 'SER', 'GLY', 'LYS', 'SER', 'THR') * 12
    lines=[]
    serial=1
    for number, residue in enumerate(residues,1):
        x=(number-1)*3.8
        for atom, dx, y, element in (('N',0.,0.,'N'), ('CA',1.4,0.4,'C'), ('C',2.8,0.,'C'), ('O',3.0,1.2,'O')):
            lines.append(f'ATOM  {serial:5d} {atom:^4s} {residue} {chain}{number:4d}    {x+dx:8.3f}{y+0.5*math.sin(number*0.7):8.3f}{0.3*math.cos(number*0.7):8.3f}{1.:6.2f}{20.:6.2f}          {element:>2s}')
            serial+=1
    with tempfile.NamedTemporaryFile(mode='w',suffix='.pdb',delete=False) as out:
        out.write('\n'.join(lines)+'\nTER\nEND\n')
        path=Path(out.name)
    before=set(session.models.list())
    run(session,f'open "{path}"')
    path.unlink()
    model=next(m for m in session.models.list() if m not in before and hasattr(m,'atoms'))
    model.name=name
    check_for_changes(session)
    return model

name='D1: R2 + two DNA-bound M2 · long descriptive model name ' * 3
model=open_chain('A',name)
bar=CodexSequenceBar.get_singleton(session)
bar.refresh()
assert bar._current_entry and bar._current_entry['length']==84
root=bar.bar_widget
root.setParent(None)
root.show()
app.processEvents()

full_status='Actual state · 84 residues · selected range A:12–28 · ' + 'Walker A P-loop; motif details. ' * 60
screens=[]
control_widths=[]
for width in (900,1400,2048):
    root.resize(width,root.sizeHint().height())
    QTest.qWait(60)
    # Set the synthetic long message after scene-refresh timers have run;
    # real selection updates are correctly allowed to replace status text.
    bar.status_label.setText(full_status)
    root.layout().activate()
    combo=bar.chain_combo
    mode=bar.selection_button
    status=bar.status_label
    assert combo.width()>=210,(width,combo.width())
    assert mode.width()>=142,(width,mode.width())
    assert status.y()>=combo.y()+combo.height(),(status.geometry(),combo.geometry())
    assert status.toolTip()==full_status
    assert len(status.text())<len(full_status)
    assert combo.font().pixelSize()==13
    assert mode.font().pixelSize()==13
    assert bar.sequence_text.font().pixelSize()==12
    assert combo.toolTip().endswith(name)
    assert bar.search_edit.width()>=220
    assert bar.similar_button.geometry().right() < width,(width,root.width(),bar.similar_button.geometry())
    # Wide windows spend their extra room on readable names and queries.
    assert width-bar.similar_button.geometry().right()<=10
    assert width-bar.panel_layout_button.geometry().right()<=10
    assert bar.panel_layout_button.y()>combo.y()
    controls=(combo,mode,bar.search_edit,bar.search_prev_button,
              bar.search_next_button,bar.refresh_button,bar.similar_button)
    assert all(left.geometry().right()<right.x()
               for left,right in zip(controls,controls[1:]))
    control_widths.append((combo.width(),bar.search_edit.width()))
    all_controls=controls+(bar.all_chains_button,bar.charge_colors_button,
                           bar.base_colors_button,bar.color_key_button,bar.panel_layout_button)
    assert {w.height() for w in all_controls}=={30},[(w.objectName(),w.height(),w.minimumHeight(),w.maximumHeight()) for w in all_controls]
    assert {w.font().pixelSize() for w in all_controls}=={13}
    assert len({w.font().weight() for w in all_controls})==1
    assert all(w.fontMetrics().height()<=w.contentsRect().height() for w in all_controls)
    path=f'/tmp/sequence-header-{width}.png'
    root.grab().save(path)
    screens.append(path)

assert all(new[0]>old[0] and new[1]>old[1]
           for old,new in zip(control_widths,control_widths[1:])),control_widths
assert control_widths[-1][0]>700 and control_widths[-1][1]>450,control_widths

# Text expansion cannot steal width from the chain or mode selectors.
before=(bar.chain_combo.width(),bar.selection_button.width())
bar.status_label.setText('Selected A:4')
app.processEvents()
assert before==(bar.chain_combo.width(),bar.selection_button.width())

# The mode button and sequence hit mapping must still work with the new font.
bar.all_chains_button.setChecked(False)
bar._set_selection_click_mode('residue')
bar.selection_button.click()
assert bar._selection_click_mode=='chain'
bar._set_selection_click_mode('residue')
run(session,'select clear')
check_for_changes(session)
bar._refresh_selection_state()
app.processEvents()
text=bar.sequence_text
cursor=QTextCursor(text.document().findBlockByNumber(1))
cursor.setPosition(cursor.position()+3)
point=text.cursorRect(cursor).center()
QTest.mouseClick(text.viewport(),Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,point)
check_for_changes(session)
assert len(selected_residues(session))==1
assert int(selected_residues(session).numbers[0])==4
bar.search_edit.setText('AGSGKST')
bar._on_search_next()
assert bar._search_matches

# Real chain switching and alignment statuses occupy the same second row.
run(session,'select clear')
other=open_chain('B','Comparison M2 complex')
bar.refresh()
index=bar.chain_combo.findData(f'#{other.id_string}/B')
assert index>=0
bar.chain_combo.setCurrentIndex(index)
assert bar._current_entry['spec']==f'#{other.id_string}/B'
bar.status_label.setText('Aligned pair · 100.0% identity · reference chain A vs comparison chain B')
root.resize(1400,root.sizeHint().height())
app.processEvents()
assert bar.status_label.y()>=bar.chain_combo.y()+bar.chain_combo.height()
assert bar.alignment_text.isVisible(), 'expected local automatic pairwise alignment'
root.grab().save('/tmp/sequence-header-alignment.png')
screens.append('/tmp/sequence-header-alignment.png')

# Both icon themes and future overflow widgets retain balanced section margins.
tool.ttb.show_tab('AI')
saved=icon_theme(session)
try:
    for theme in ('original','modern'):
        set_icon_theme(session,theme,save=False)
        style_ai_toolbar(session)
        app.processEvents()
        for title in ('Quick','Sequence','Modeling','Sites','Channels','Structure'):
            section=tool.ttb._buttons['AI'][title]
            for widget in section.createdWidgets():
                margins=widget.layout().contentsMargins()
                assert (margins.top(),margins.bottom())==(6,3),(title,margins)
            # Overflow sections are hidden and retain Qt's placeholder height.
            if widget.isVisible():
                assert widget.height()>=87,(title,widget.height())
        popup_parent=QWidget()
        popup=tool.ttb._buttons['AI']['Quick'].createWidget(popup_parent)
        margins=popup.layout().contentsMargins()
        assert (margins.top(),margins.bottom())==(6,3)
        path=f'/tmp/toolbar-spacing-{theme}.png'
        tool.ttb.grab().save(path)
        screens.append(path)
finally:
    set_icon_theme(session,saved,save=False)
report={'ok':True,'sequence_version':bar.UI_LAYOUT_VERSION,'control_rows':2,'sequence_font_px':12,'header_font_px':13,'widths':[900,1400,2048],'control_widths':control_widths,'toolbar_margins':[6,3],'screenshots':screens}
# Targeted reload retains the active chain, query and existing 3D mouse binding.
bar._set_selection_click_mode('chain')
old_spec=bar.chain_combo.currentData()
old_query=bar.search_edit.text()
binding=session.ui.mouse_modes.mode('left',[],exact=True)
before_models=list(session.models.list())
runpy.run_path(str(Path(__file__).with_name('reload_sequence_ui.py')),init_globals={'session':session})
QTest.qWait(250)
from chimerax.codex_bridge.sequence_bar import CodexSequenceBar as ReloadedSequenceBar
bar=ReloadedSequenceBar.get_singleton(session)
assert bar.chain_combo.currentData()==old_spec
assert bar.search_edit.text()==old_query
assert bar._selection_click_mode=='chain'
assert session.ui.mouse_modes.mode('left',[],exact=True) is binding
assert list(session.models.list())==before_models
bar._queue_refresh()
bar._queue_selection_refresh()
bar.delete()
QTest.qWait(220)
assert bar._closed and bar.bar_widget is None
REPORT.write_text(json.dumps(report,indent=2))
print('SEQUENCE_HEADER_OK',json.dumps(report))
