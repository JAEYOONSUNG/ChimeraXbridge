"""Palette integration with real molecular targets; no desktop sampling or UI."""
import importlib.util
import json
from pathlib import Path
import runpy
import sys
from unittest.mock import patch

from Qt.QtCore import QPoint, Qt
from Qt.QtGui import QColor
from Qt.QtWidgets import QApplication
from PyQt6 import sip
from PyQt6.QtTest import QTest
from chimerax.atomic import AtomicStructure, check_for_changes
from chimerax.core.commands import run

root=Path(__file__).resolve().parents[1]
app=runpy.run_path(str(root/'scripts/headless_ui_fixture.py'))['install'](session)
spec=importlib.util.spec_from_file_location('chimerax.codex_bridge',root/'src/__init__.py',submodule_search_locations=[str(root/'src')])
package=importlib.util.module_from_spec(spec);sys.modules[spec.name]=package;spec.loader.exec_module(package)
package._schedule_helper_dock_layout=lambda *a,**k:None
from chimerax.codex_bridge.display_controls import DisplayControlsWidget
# Replace any startup hook from the installed bundle with this checkout's hook.
old_hook=getattr(session,'_codex_bridge_chem_restore_handler',None)
if old_hook is not None:
    old_hook.remove()
    delattr(session,'_codex_bridge_chem_restore_handler')
from chimerax.codex_bridge.display_color import install_auto_chemistry_restore
install_auto_chemistry_restore(session)
widget=DisplayControlsWidget(session)
widget.resize(360,540);widget.show()
clipboard=QApplication.clipboard();clipboard.setText('isolated palette clipboard')


def settle():
    for _ in range(6):app.processEvents()


def select(spec):
    run(session,'select '+spec)
    check_for_changes(session);settle();widget.refresh()


def scene():
    return tuple((m.atoms.colors.tobytes(),m.atoms.displays.tobytes(),m.atoms.selected.tobytes(),
                  m.atoms.coords.tobytes(),m.residues.ribbon_colors.tobytes(),m.position.matrix.tobytes())
                 for m in models),session.main_view.camera.position.matrix.tobytes()


settle()
assert widget.pick_color_button.text()=='Palette…'
assert widget.apply_color_button.text()=='Apply color'
assert widget.pick_color_button.isEnabled() and not widget.apply_color_button.isEnabled()
widget.pick_color_button.click();settle()
palette=widget._palette_dialog
assert palette is not None and palette.copy_button.isEnabled() and not palette.apply_button.isEnabled()
assert 'No target' in palette.target_label.text()
palette.setCurrentColor(QColor('#47A6CD'));settle()
assert clipboard.text()=='isolated palette clipboard'
palette.copy_button.click()
assert clipboard.text()=='#47A6CD' and not session.models.list()
# Reopening the button keeps one live palette and does not discard its choice.
widget.pick_color_button.click()
assert widget._palette_dialog is palette and palette.current_hex()=='#47A6CD'
palette.close_button.click();settle()
assert widget._palette_dialog is None

models=[]
for name,base in (('Reference',(25,70,140)),('Comparison',(180,60,20))):
    model=AtomicStructure(session,name=name)
    for index,alpha in enumerate((51,153)):
        residue=model.new_residue('ALA','A',index+1)
        atom=model.new_atom('CA','C');residue.add_atom(atom);atom.coord=(index*3.8,0,len(models)*9)
    model.ss_assigned=True
    session.models.add([model]);models.append(model)
    # Adding a model applies ChimeraX default styling; set test RGBA afterwards.
    for atom,residue,alpha in zip(model.atoms,model.residues,(51,153)):
        atom.color=(*base,alpha)
        residue.ribbon_color=(*base,alpha)
select('#1/A:1')
widget.color_target_combo.setCurrentIndex(widget.color_target_combo.findData('ab'));settle()
initial=scene()
widget.pick_color_button.click();settle();palette=widget._palette_dialog
assert palette.apply_button.isEnabled() and 'Selection' in palette.target_label.text()
assert '#1' in palette.target_label.text() and 'Atoms & bonds' in palette.target_label.text()
palette.setCurrentColor(QColor('#47A6CD'));settle()
palette.copy_button.click()
assert scene()==initial, 'Palette browsing/copy changed the scene'
palette.apply_button.click();settle()
assert tuple(models[0].atoms[0].color)==(71,166,205,51), tuple(models[0].atoms[0].color)
assert tuple(models[0].atoms[1].color)==(25,70,140,153)
assert tuple(models[1].atoms[0].color)==(180,60,20,51)
assert models[0].atoms[0].selected
assert palette.isVisible() and palette.status_label.text()=='Applied #47A6CD'

# A delayed native selection change must not apply against the old target caption.
with patch.object(widget,'_queue_refresh'):
    run(session,'select #2/A:1');check_for_changes(session)
    before=scene()
    palette.setCurrentColor(QColor('#C855A1'))
    palette.apply_button.click()
    assert scene()==before and 'Target changed' in palette.status_label.text()
    assert '#2' in palette.target_label.text()
    palette.apply_button.click()
assert tuple(models[1].atoms[0].color)==(200,85,161,51)

# Selecting a target after opening in copy-only mode enables explicit Apply.
select('clear');assert not palette.apply_button.isEnabled()
widget.color_scope_combo.setCurrentIndex(widget.color_scope_combo.findData('all'));settle()
assert palette.apply_button.isEnabled() and 'All models' in palette.target_label.text()
palette.setCurrentColor(QColor('#2D9974'));palette.apply_button.click();settle()
for m in models:
    assert list(m.atoms.colors[:,3])==[51,153]
    assert all(tuple(c[:3])==(45,153,116) for c in m.atoms.colors)
# Main representation switches also change the explicit popup target.
widget.color_target_combo.setCurrentIndex(widget.color_target_combo.findData('c'));settle()
assert 'Cartoon' in palette.target_label.text()
atom_colors=[m.atoms.colors.tobytes() for m in models]
palette.setCurrentColor(QColor('#AA5522'));palette.apply_button.click();settle()
assert atom_colors==[m.atoms.colors.tobytes() for m in models]
assert all(all(tuple(c[:3])==(170,85,34) for c in m.residues.ribbon_colors) for m in models)
assert all(list(m.residues.ribbon_colors[:,3])==[51,153] for m in models)

for width in (360,520):
    widget.resize(width,460);settle()
    assert widget.width()==width and not widget.scroll_area.horizontalScrollBar().maximum()
    for control in (widget.color_hex_edit,widget.pick_color_button,widget.apply_color_button):
        point=control.mapTo(widget.scroll_area.widget(),QPoint())
        assert point.x()>=0 and point.x()+control.width()<=widget.scroll_area.widget().width()
        if control is not widget.color_hex_edit:
            assert control.fontMetrics().horizontalAdvance(control.text())+10<=control.width()
    if width==360:
        widget.scroll_area.verticalScrollBar().setValue(0);settle()
        assert widget.grab().save('/tmp/display-palette-controls-360.png')
assert palette.grab().save('/tmp/display-color-palette.png')

# Explicit representations must isolate chemistry coloring, and palette RGB
# must retain transparent bonds with one undo/redo action.
model=models[0]
hetero=model.new_atom('N','N');model.residues[0].add_atom(hetero);hetero.coord=(1.3,0,0)
hetero.color=(11,22,33,77)
bond=model.new_bond(model.atoms[0],hetero);bond.color=(30,40,50,91)
select('#1/A:1')
widget.color_scope_combo.setCurrentIndex(widget.color_scope_combo.findData('sel'));settle()
widget.color_target_combo.setCurrentIndex(widget.color_target_combo.findData('c'));settle()
before_atoms=model.atoms.colors.tobytes()
palette.setCurrentColor(QColor('#883344'));palette.apply_button.click();settle()
assert model.atoms.colors.tobytes()==before_atoms, 'Cartoon coloring altered atom colors'
widget.color_target_combo.setCurrentIndex(widget.color_target_combo.findData('ab'));settle()
before_atoms=model.atoms.colors.tobytes();before_bond=tuple(bond.color)
palette.setCurrentColor(QColor('#2288BB'));palette.apply_button.click();settle()
assert tuple(bond.color)==(34,136,187,91), tuple(bond.color)
assert hetero.color[3]==77 and model.atoms[0].color[3]==51
from chimerax.atomic.colors import element_colors
assert tuple(hetero.color[:3])==tuple(element_colors(model.atoms.element_numbers)[-1,:3])
applied_atoms=model.atoms.colors.tobytes();applied_bond=tuple(bond.color)
session.undo.undo();settle()
assert model.atoms.colors.tobytes()==before_atoms and tuple(bond.color)==before_bond
session.undo.redo();settle()
assert model.atoms.colors.tobytes()==applied_atoms and tuple(bond.color)==applied_bond

group=model.pseudobond_group('Palette connector')
pseudobond=group.new_pseudobond(model.atoms[0],hetero)
pseudobond.color=(80,90,100,117)
widget.color_scope_combo.setCurrentIndex(widget.color_scope_combo.findData('all'))
widget.color_target_combo.setCurrentIndex(widget.color_target_combo.findData('abcsp'));settle()
before_bond=tuple(bond.color);before_pseudobond=tuple(pseudobond.color)
palette.setCurrentColor(QColor('#55AA77'));palette.apply_button.click();settle()
assert tuple(bond.color)==(85,170,119,91)
assert tuple(pseudobond.color)==(85,170,119,117), tuple(pseudobond.color)
session.undo.undo();settle()
assert tuple(bond.color)==before_bond and tuple(pseudobond.color)==before_pseudobond
session.undo.redo();settle()
assert tuple(bond.color)==(85,170,119,91) and tuple(pseudobond.color)==(85,170,119,117)

# Every RGB input shares the palette's alpha policy, including independent
# alpha values within a bond collection and one-step undo/redo of the scene.
second_bond=model.new_bond(model.atoms[1],hetero)
second_bond.color=(20,30,40,39)
second_pseudobond=group.new_pseudobond(model.atoms[1],hetero)
second_pseudobond.color=(50,60,70,201)
settle()


def color_state():
    return (scene(), model.bonds.colors.tobytes(), group.pseudobonds.colors.tobytes())


def assert_one_color_undo(before):
    after=color_state()
    assert after!=before, 'The color action had no effect'
    session.undo.undo();settle()
    assert color_state()==before, 'One undo did not restore all color/alpha values'
    session.undo.redo();settle()
    assert color_state()==after, 'One redo did not restore the applied color/alpha values'


def assert_rgb_alphas():
    assert [int(bond.color[3]),int(second_bond.color[3])]==[91,39]
    assert [int(pseudobond.color[3]),int(second_pseudobond.color[3])]==[117,201]
    assert [m.atoms.colors[:,3].tobytes() for m in models]==atom_alphas


atom_alphas=[m.atoms.colors[:,3].tobytes() for m in models]
before=color_state()
widget.color_hex_edit.setText('#446688');widget.apply_color_button.click();settle()
assert tuple(bond.color[:3])==(68,102,136)
assert tuple(pseudobond.color[:3])==(68,102,136)
assert_rgb_alphas();assert_one_color_undo(before)

before=color_state()
QTest.mouseClick(widget.rainbow_ramp,Qt.MouseButton.LeftButton,
                pos=widget.rainbow_ramp.rect().center());settle()
assert tuple(bond.color[:3])!= (68,102,136)
assert tuple(bond.color[:3])==tuple(pseudobond.color[:3])==tuple(model.atoms[0].color[:3])
assert_rgb_alphas();assert_one_color_undo(before)

before=color_state()
widget.saturation_value_label.setValue(30)
widget.saturation_value_label.editingFinished.emit();settle()
assert_rgb_alphas();assert_one_color_undo(before)

# RGBA explicitly replaces alpha. A transparency control overrides even that
# suffix, and must set bonds/pseudobonds to the same opacity as atoms/cartoon.
before=color_state()
widget.color_hex_edit.setText('#12345680');widget.apply_color_button.click();settle()
assert all(all(int(alpha)==128 for alpha in m.atoms.colors[:,3]) for m in models)
assert all(int(alpha)==128 for alpha in model.bonds.colors[:,3])
assert all(int(alpha)==128 for alpha in group.pseudobonds.colors[:,3])
assert_one_color_undo(before)
for percent,expected_alpha in ((25,192),(0,255),(100,0)):
    before=color_state()
    widget.color_hex_edit.setText('#65432180')
    widget.color_transparency_value_label.setValue(percent)
    widget.color_transparency_value_label.editingFinished.emit();settle()
    assert all(all(int(alpha)==expected_alpha for alpha in m.atoms.colors[:,3]) for m in models)
    assert all(int(alpha)==expected_alpha for alpha in model.bonds.colors[:,3])
    assert all(int(alpha)==expected_alpha for alpha in group.pseudobonds.colors[:,3])
    assert_one_color_undo(before)

# The native model-color representation deliberately covers whole models.
# Make that expansion and its unified transparency explicit before the user applies.
select('#1/A:1')
widget.color_scope_combo.setCurrentIndex(widget.color_scope_combo.findData('sel'))
widget.color_target_combo.setCurrentIndex(widget.color_target_combo.findData('m'));settle()
assert 'Whole selected models' in palette.target_label.text()
assert 'one transparency per model' in palette.target_label.text()
model_alpha=int(model.overall_color[3])
other_atoms=models[1].atoms.colors.tobytes()
palette.setCurrentColor(QColor('#113355'));palette.apply_button.click();settle()
assert all(tuple(c)==(17,51,85,model_alpha) for c in model.atoms.colors)
assert models[1].atoms.colors.tobytes()==other_atoms

# Palette actions queued after owner cleanup are inert.
before=scene();clipboard.setText('keep clipboard on close')
widget.cleanup();settle()
assert widget._palette_dialog is None
widget._apply_palette_color(palette,'#FFFFFF')
assert scene()==before and clipboard.text()=='keep clipboard on close'
sip.delete(widget);settle()
print('DISPLAY_PALETTE_OK',json.dumps({'ok':True,'browse_without_target':True,'copy_without_apply':True,
    'target_and_representation_explicit':True,'stale_target_rejected':True,'alpha_preserved':True,
    'all_rgb_routes_undo_redo':True,'explicit_rgba_and_transparency':True,
    'compact_controls':True,'desktop_sampling_exercised':False}))
