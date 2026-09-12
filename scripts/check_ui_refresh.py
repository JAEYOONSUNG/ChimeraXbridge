"""GUI smoke test: ChimeraX --notools --exit --script <this file>.

Run only in a disposable ChimeraX session. Does not invoke AI or science jobs.
"""
import ast
import importlib.util
import json
import os
import sys
from pathlib import Path

from Qt.QtCore import Qt
from Qt.QtGui import QIcon, QFontMetrics
from Qt.QtWidgets import QApplication, QPushButton, QToolButton, QHeaderView

ROOT = Path(__file__).resolve().parents[1]
REPORT = Path('/tmp/chimerax-ui-refresh-report.json')
REPORT.unlink(missing_ok=True)
assert session.ui.is_gui, 'Use a disposable GUI ChimeraX session'
for name in list(sys.modules):
    if name == 'chimerax.codex_bridge' or name.startswith('chimerax.codex_bridge.'):
        del sys.modules[name]
spec = importlib.util.spec_from_file_location('chimerax.codex_bridge', ROOT/'src/__init__.py', submodule_search_locations=[str(ROOT/'src')])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)

from chimerax.codex_bridge.runtime_patches import apply_runtime_patches, _style_ai_button_widget
from chimerax.codex_bridge.tool import CodexAssistant
from chimerax.codex_bridge.action_pad import CodexActionPad
from chimerax.codex_bridge.display_controls import CodexDisplayControls
from chimerax.atomic import AtomicStructure, Element, check_for_changes
from chimerax.core.commands import run
from chimerax.model_panel.tool import ModelPanel, model_panel

apply_runtime_patches(session)
models = []
for name, rgba in (('Reference complex · protein chains A and B', (167,143,204,255)),
                   ('Comparison structure with a long descriptive model name', (104,172,180,255))):
    model = AtomicStructure(session, name=name)
    for chain in ('A','B'):
        for i in range(1,4):
            residue = model.new_residue('ALA',chain,i)
            atom = model.new_atom('CA',Element.get_element('C'))
            residue.add_atom(atom)
            atom.coord=(i*3, 0 if chain=='A' else 12,0)
    session.models.add([model])
    model.atoms.colors=rgba
    model.ss_assigned=True
    model.residues.is_helix=True
    model.residues.ribbon_colors=rgba
    models.append(model)

panel = ModelPanel.get_singleton(session) or model_panel(session,'Model Panel')
assistant = CodexAssistant.get_singleton(session)
action = CodexActionPad.get_singleton(session)
display = CodexDisplayControls.get_singleton(session)
app = QApplication.instance()
check_for_changes(session)
app.processEvents()
panel._fill_tree(always_rebuild=True)
action.widget.refresh()
run(session,'select #1/A')
check_for_changes(session)
app.processEvents()
display.widget.refresh()
action.widget.refresh()
assert '#1' in display.widget.scope_label.text()
assert panel.tree.header().sectionResizeMode(0) == QHeaderView.ResizeMode.Stretch

# Existing native Model Panel actions still apply to the highlighted model only.
item = next(item for model,item in zip(panel.models,panel._items) if model is models[0])
panel.tree.setCurrentItem(item)
button = next(b for b in panel.tool_window.ui_area.findChildren(QPushButton) if b.text()=='Hide')
button.click()
assert not models[0].display and models[1].display
next(b for b in panel.tool_window.ui_area.findChildren(QPushButton) if b.text()=='Show').click()
assert models[0].display and models[1].display

# Tree action cells must actually exist after insertion in the Qt tree.
cells=[]
def walk(item):
    for col in range(1,6):
        widget=action.widget.tree.itemWidget(item,col)
        if widget is not None:
            button=widget if isinstance(widget,QToolButton) else widget.findChild(QToolButton)
            if button is not None:
                assert button.menu() is not None
                cells.append(button)
    for i in range(item.childCount()):walk(item.child(i))
for i in range(action.widget.tree.topLevelItemCount()):walk(action.widget.tree.topLevelItem(i))
assert len(cells)>=10, len(cells)

assistant._show_assistant_tab()
assistant.prompt_edit.setPlainText('Color the selected chain blue.\nKeep the other chains unchanged.')
assert assistant.prompt_edit.toPlainText().startswith('Color the selected')
assistant._append_system('Ready. Select a chain, then describe the change you want.')
old=assistant.content_tabs.currentIndex()
assistant.toggle_workspace_button.click()
assert assistant.content_tabs.currentIndex()!=old
assistant.toggle_workspace_button.click()
assert assistant.content_tabs.currentIndex()==old
assistant.toggle_terminal_button.click()
assert assistant._command_terminal_visible
assistant.toggle_terminal_button.click()
assert not assistant._command_terminal_visible

# Reload helpers must retain user drafts and transcript text.
reload_tree=ast.parse((ROOT/'scripts/reload_codex_ui.py').read_text())
functions=[node for node in reload_tree.body if isinstance(node,ast.FunctionDef) and node.name in ('_capture_assistant_text','_restore_assistant_text')]
namespace={}
exec(compile(ast.Module(body=functions,type_ignores=[]),'reload text helpers','exec'),namespace)
state=namespace['_capture_assistant_text'](session)
original_prompt=assistant.prompt_edit.toPlainText()
original_history=assistant.terminal_edit.toPlainText()
assistant.prompt_edit.clear()
assistant.terminal_edit.clear()
namespace['_restore_assistant_text'](assistant,state)
assert assistant.prompt_edit.toPlainText()==original_prompt
assert assistant.terminal_edit.toPlainText()==original_history

# Source icon registrations must be vector files, and long labels must fit.
providers=package._toolbar_provider_specs() if hasattr(package,'_toolbar_provider_specs') else []
# Read actual provider calls through AST; the runtime list can have a versioned name.
import ast
source=ast.parse((ROOT/'src/__init__.py').read_text())
buttons=[]
for node in ast.walk(source):
    if not isinstance(node,ast.Call) or not isinstance(node.func,ast.Name) or node.func.id!='provider':continue
    kwargs={k.arg:k.value.value for k in node.keywords if isinstance(k.value,ast.Constant)}
    icon=kwargs.get('icon')
    if not icon:continue
    assert icon.endswith('.svg'),icon
    button=QToolButton()
    button.setText(kwargs['display_name'])
    button.setIcon(QIcon(str(ROOT/'src/icons'/icon)))
    assert not button.icon().isNull(),icon
    _style_ai_button_widget(button)
    assert button.iconSize().width()==28
    assert all(QFontMetrics(button.font()).horizontalAdvance(line)+10<=button.width() for line in button.text().splitlines()),button.text()
    buttons.append(button)
assert len(buttons)>=35,len(buttons)

screens=[]
for name, root in (('models',panel.tool_window.ui_area), ('assistant',assistant.tool_window.ui_area),
                   ('display',display.widget), ('action',action.widget)):
    root.setParent(None)
    root.show()
    for width in (380,520):
        root.resize(width,640)
        app.processEvents()
        if name=='assistant':
            assistant._apply_responsive_layout(width)
            app.processEvents()
        assert root.width()==width,(name,width,root.width())
        if name == 'assistant':
            point=assistant.prompt_edit.mapTo(root, assistant.prompt_edit.rect().topLeft())
            assert point.y()+assistant.prompt_edit.height() <= root.height(), ('prompt below viewport',point.y(),assistant.prompt_edit.height(),root.height())
            assert assistant.prompt_edit.isVisible()
        if name == 'action':
            for label in (action.widget.pick_mode_label, action.widget.status_label):
                assert label.height() >= 16, ('caption collapsed',label.text(),label.height(),label.sizePolicy().verticalPolicy())
        if name == 'models':
            label=panel._codex_model_count
            assert label.height() >= 16, ('models heading collapsed',label.height(),label.sizePolicy().verticalPolicy())
        path=f'/tmp/ui-refresh-{name}-{width}.png'
        assert root.grab().save(path)
        screens.append(path)
    root.hide()
report={'ok':True,'versions':{'assistant':assistant.UI_LAYOUT_VERSION,'action':action.UI_LAYOUT_VERSION,'display':display.UI_LAYOUT_VERSION},'row_menu_cells':len(cells),'toolbar_buttons':len(buttons),'screenshots':screens}
REPORT.write_text(json.dumps(report,indent=2))
print('UI_REFRESH_OK',json.dumps(report))
