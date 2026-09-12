"""Headless real-Qt check: ChimeraX --nogui --notools --exit --script <file>."""
import importlib.util
import json
import os
import sys
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from Qt.QtWidgets import QApplication,QMainWindow,QWidget
from Qt.QtCore import Qt,QPoint
from Qt.QtGui import QTextCursor
from PyQt6.QtTest import QTest
from chimerax.atomic import AtomicStructure,selected_residues,selected_atoms,check_for_changes
from chimerax.build_structure.start import place_peptide,place_nucleic_acid
from chimerax.core.commands import run

root=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('chimerax.codex_bridge',root/'src/__init__.py',submodule_search_locations=[str(root/'src')])
package=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=package
spec.loader.exec_module(package)
from chimerax.codex_bridge.sequence_bar import CodexSequenceBar
from chimerax.codex_bridge.backends import get_backend_defaults
from chimerax.codex_bridge.panel_layout import PanelLayoutSettings

app=QApplication.instance() or QApplication([])
protein=AtomicStructure(session,name='Two protein chains')
session.models.add([protein])
place_peptide(protein,'AGSGKST',[(-60,-45)]*7,chain_id='A',position=(0,0,0))
place_peptide(protein,'AAA',[(-60,-45)]*3,chain_id='B',position=(0,20,0))
dna=AtomicStructure(session,name='DNA')
session.models.add([dna])
place_nucleic_acid(dna,'ATGC',type='dna',position=(0,40,0))
rna=AtomicStructure(session,name='RNA')
session.models.add([rna])
place_nucleic_acid(rna,'ACGU',type='rna',form='A',position=(0,60,0))
dna.display=False
check_for_changes(session)
window=QMainWindow()
window.main_view=QWidget(window)
session.ui.main_window=window
bar=CodexSequenceBar(session,'Sequence Bar')
bar.bar_widget.setParent(None)
bar.bar_widget.resize(1200,280)
bar.bar_widget.show()
app.processEvents()
text=bar.all_chains_text
assert bar.all_chains_button.isChecked() and bar._all_chains_enabled
assert len(text.entries_by_row)==6, list(text.entries_by_row)
assert text.isVisible() and not bar.sequence_text.isVisible()
assert not bar._alignment_payload
assert 'identity' not in bar.status_label.toolTip().lower()
for entry in text.entries_by_row.values():
    assert entry['sequence'] in text.toPlainText()
    assert entry['spec'] in text.toPlainText()
assert text.entries_by_row[f'#{dna.id_string}/A']['sequence']=='ATGC'
assert text.entries_by_row[f'#{rna.id_string}/A']['sequence']=='ACGU'

# Pixel hit testing must use each row's own length and map to that chain.
def point(row,index):
    cursor=QTextCursor(text.document().findBlockByNumber(text._alignment_row_lines[row]))
    cursor.setPosition(cursor.position()+text._alignment_offset+index)
    return text.cursorRect(cursor).center()

def clear():
    run(session,'select clear')
    check_for_changes(session)
    bar._refresh_selection_state()

row=f'#{protein.id_string}/B'
bar._selection_click_mode='residue'
clear()
QTest.mouseClick(text.viewport(),Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,point(row,1))
check_for_changes(session)
assert len(selected_residues(session))==1
res=selected_residues(session)[0]
assert res.chain_id=='B' and res.number==2
assert bar._all_chains_enabled
clear()
end=point(row,2)
assert text._alignment_column_and_row_at(QPoint(end.x()+80,end.y()))==(None,None)

# Each row has the correct context, even when a different chain was active.
seen=[]
original_menu=bar._show_residue_context_menu
bar._show_residue_context_menu=lambda index,pos: seen.append((bar._current_entry['spec'],index))
bar._show_all_chains_context_menu(1,f'#{dna.id_string}/A',QPoint())
assert seen==[(f'#{dna.id_string}/A',1)]
bar._show_residue_context_menu=original_menu
clear()
bar._selection_click_mode='atom'
bar._select_all_chains_range(0,0,f'#{dna.id_string}/A')
assert len(selected_atoms(session))==1
assert selected_atoms(session)[0].structure is dna
clear()
bar._selection_click_mode='chain'
bar._select_all_chains_range(0,0,row)
assert len(selected_residues(session))==3
assert all(r.chain_id=='B' for r in selected_residues(session))

bar.search_edit.setText('ATGC')
bar._on_search_next()
assert any(m['row']==f'#{dna.id_string}/A' for m in bar._search_matches)
bar.all_chains_button.setChecked(False)
assert not bar._all_chains_enabled and not text.isVisible()
bar.all_chains_button.setChecked(True)
assert len(text.entries_by_row)==6 and text.isVisible()
for backend in ('codex','openai'):
    for fast in (True,False):assert get_backend_defaults(backend,fast)[0]=='gpt-6-astra'
assert PanelLayoutSettings.AUTO_SAVE['mode']=='tabs'
bar.bar_widget.grab().save('/tmp/all-chains-overview.png')
Path('/tmp/chain-overview-check.json').write_text(json.dumps({'ok':True,'chains':6,'protein_dna_rna':True,'hidden_models':True,'pixel_pick':True,'context':True,'search':True,'version':bar.UI_LAYOUT_VERSION}))
bar.delete()
QTest.qWait(200)
print('CHAIN_OVERVIEW_OK')
