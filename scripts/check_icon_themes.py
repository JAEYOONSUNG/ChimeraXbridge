"""ChimeraX --notools --exit --script <this file>: real QAction theme toggle test."""
import ast
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

from Qt.QtWidgets import QApplication, QWidget, QToolButton, QToolBar
from Qt.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PyQt6 import sip
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
REPORT=Path('/tmp/icon-theme-test.json')
REPORT.unlink(missing_ok=True)
manifest=json.loads((ROOT/'src/icons/original/manifest.json').read_text())
for name,record in manifest['files'].items():
    source=(ROOT/'src/icons/original'/name).read_bytes()
    assert source==(ROOT/'icons/original'/name).read_bytes(), name
    assert hashlib.sha256(source).hexdigest()==record['sha256'],name
    expected=subprocess.check_output(['git','show',f"{manifest['commit']}:{record['path']}"],cwd=ROOT)
    assert source==expected,name

for name in list(sys.modules):
    if name=='chimerax.codex_bridge' or name.startswith('chimerax.codex_bridge.'):
        del sys.modules[name]
spec=importlib.util.spec_from_file_location('chimerax.codex_bridge',ROOT/'src/__init__.py',submodule_search_locations=[str(ROOT/'src')])
package=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=package
spec.loader.exec_module(package)
from chimerax.codex_bridge.runtime_patches import apply_runtime_patches
from chimerax.codex_bridge.icon_theme import icon_theme, set_icon_theme, ToolbarIconSettings, ICON_PAIRS, icon_path
from chimerax.toolbar.tool import get_toolbar_singleton
apply_runtime_patches(session)
package._install_runtime_toolbar_buttons(session,force_rebuild=True)
tool=get_toolbar_singleton(session,create=True)
package._install_runtime_toolbar_buttons(session,force_rebuild=True)
ttb=tool.ttb
ttb.show_tab('AI')
app=QApplication.instance()
app.processEvents()
# Let ChimeraX's scheduled startup toolbar rebuilds finish before checking
# that the user-triggered switch itself leaves widget identities unchanged.
from PyQt6.QtTest import QTest
QTest.qWait(6000)
ttb=tool.ttb
ttb.show_tab('AI')
section=ttb._buttons['AI']['Quick']
assert any(info.title=='Icons' for info in section._buttons)

def action(title):
    return next(iter(section._actions[title].values()))

def fingerprint(icon):
    image=icon.pixmap(28,28).toImage()
    return hashlib.sha256(image.constBits().asstring(image.sizeInBytes())).hexdigest()

def persistent_widgets():
    # QWidgetAction also owns transient overflow-menu copies, which Qt may
    # replace during relayout. The actual toolbar widgets must stay in place.
    return tuple(widget for tab in ttb._buttons.values() for section_info in tab.values()
                 if hasattr(section_info,'createdWidgets') for widget in section_info.createdWidgets()
                 if isinstance(widget.parentWidget(), QToolBar))

def live_widgets():
    return tuple((int(sip.unwrapinstance(widget)),
                  tuple((int(sip.unwrapinstance(button)), int(sip.unwrapinstance(button.defaultAction()))
                         if button.defaultAction() is not None else None)
                        for button in widget.findChildren(QToolButton)))
                 for widget in persistent_widgets())

def rendered_label_geometry():
    buttons=[button for widget in persistent_widgets() for button in widget.findChildren(QToolButton)]
    probe=QPixmap(28,28)
    probe.fill(QColor('#FF00FF'))
    result=[]
    for title in ('Analyze','AF Complex'):
        button=next(button for button in buttons if button.accessibleName()==title)
        icon, stylesheet=button.icon(),button.styleSheet()
        try:
            button.setIcon(QIcon(probe))
            button.setStyleSheet(stylesheet+'QToolButton { color: #00FF40; background: #111111; }')
            app.processEvents()
            image=QImage(button.size(),QImage.Format.Format_RGBA8888)
            image.fill(0)
            painter=QPainter(image)
            button.render(painter)
            painter.end()
            raw=image.constBits().asstring(image.sizeInBytes())
            rgba=np.frombuffer(raw,dtype=np.uint8).reshape(image.height(),image.bytesPerLine())[:, :image.width()*4].reshape(image.height(),image.width(),4)
            icon_pixels=(rgba[:,:,0]>230)&(rgba[:,:,1]<25)&(rgba[:,:,2]>230)
            text_pixels=(rgba[:,:,0]<75)&(rgba[:,:,1]>150)&(rgba[:,:,2]<120)
            icon_rows=np.nonzero(icon_pixels)[0]
            text_rows=np.nonzero(text_pixels)[0]
            assert len(icon_rows) and len(text_rows),(title,'missing rendered icon/text')
            result.append(dict(title=title,height=button.height(),icon_y=[int(icon_rows.min()),int(icon_rows.max())],first_label_y=int(text_rows.min())))
            assert button.accessibleName()==title
        finally:
            button.setIcon(icon)
            button.setStyleSheet(stylesheet)
    assert result[0]['height']==result[1]['height'],result
    assert result[0]['icon_y']==result[1]['icon_y'],result
    assert result[0]['first_label_y']==result[1]['first_label_y'],result
    return result

def widget_diagnostics():
    from PyQt6 import sip
    return [dict(tab=tab_name, section=section_name, python_id=id(widget),
                 native_id=int(sip.unwrapinstance(widget)),
                 titles=[button.text() for button in widget.findChildren(QToolButton)])
            for tab_name, tab in ttb._buttons.items() for section_name, section_info in tab.items()
            if hasattr(section_info,'createdWidgets') for widget in section_info.createdWidgets()]

saved=icon_theme(session)
try:
    set_icon_theme(session,'original')
    app.processEvents()
    original=fingerprint(action('Analyze').icon())
    assert action('Icons').text()=='Original\nicons'
    assert not action('Icons').isChecked()
    ttb.grab().save('/tmp/toolbar-icons-original.png')
    toolbar_refs=persistent_widgets()
    assert toolbar_refs, "no persistent toolbar widgets found"
    before=live_widgets()
    before_diagnostics=widget_diagnostics()
    callbacks=tuple(info.callback for info in section._buttons)
    current_tab=ttb.currentIndex()
    action('Icons').trigger()
    app.processEvents()
    assert icon_theme(session)=='modern'
    assert ToolbarIconSettings(session,'Codex Toolbar Icons').style=='modern'
    modern=fingerprint(action('Analyze').icon())
    assert original!=modern
    assert action('Icons').isChecked()
    assert action('Icons').text()=='New SVG\nicons'
    after=live_widgets()
    if before!=after:
        print('ICON_THEME_WIDGET_DIAGNOSTICS ' + json.dumps({'before':before_diagnostics,'after':widget_diagnostics()}))
    assert before==after, 'toolbar was rebuilt during toggle'
    assert current_tab==ttb.currentIndex(), 'active tab changed'
    assert callbacks==tuple(info.callback for info in section._buttons)
    label_alignment=rendered_label_geometry()
    ttb.grab().save('/tmp/toolbar-icons-modern.png')
    # Overflow widgets must inherit the current artwork and toggle state.
    parent=QWidget()
    popup=section.createWidget(parent)
    popup_actions=[b.defaultAction() for b in popup.findChildren(QToolButton)]
    assert any(a and a.property('codexIconThemeToggle') and a.isChecked() for a in popup_actions)
    for a in popup_actions:
        if a and a.text()=='Analyze':assert fingerprint(a.icon())==modern
    action('Icons').trigger()
    app.processEvents()
    assert icon_theme(session)=='original'
    assert ToolbarIconSettings(session,'Codex Toolbar Icons').style=='original'
    assert fingerprint(action('Analyze').icon())==original
    assert all(not a.isChecked() for a in popup_actions if a and a.property('codexIconThemeToggle'))
    assert label_alignment==rendered_label_geometry(), 'label geometry changed when returning to Original'
    for key in ICON_PAIRS:
        for theme in ('original','modern'):
            assert icon_path(key,theme).is_file(),(key,theme)
            assert not QIcon(str(icon_path(key,theme))).isNull(),(key,theme)
    report={'ok':True,'original_assets':len(manifest['files']),'mapped_buttons':len(ICON_PAIRS),'in_place':True,'saved_preference':True,'overflow':True,'label_alignment':label_alignment}
    Path('/tmp/icon-theme-test.json').write_text(json.dumps(report))
    print('ICON_THEMES_OK',json.dumps(report))
finally:
    set_icon_theme(session,saved)
