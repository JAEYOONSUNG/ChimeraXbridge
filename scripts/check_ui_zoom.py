"""Headless UI zoom check: synthetic widgets plus the real Display Controls panel."""
import importlib.util
import json
from pathlib import Path
import runpy
import sys
import time

from Qt.QtCore import QSize, Qt
from Qt.QtGui import QFont
from Qt.QtWidgets import QDockWidget, QLabel, QLineEdit, QMainWindow, QPushButton, QToolButton, QVBoxLayout, QWidget
from PyQt6.QtTest import QTest

root = Path(__file__).resolve().parents[1]
app = runpy.run_path(str(root / "scripts/headless_ui_fixture.py"))["install"](session)
spec = importlib.util.spec_from_file_location("chimerax.codex_bridge", root / "src/__init__.py",
                                              submodule_search_locations=[str(root / "src")])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
package._schedule_helper_dock_layout = lambda *a, **k: None
from chimerax.codex_bridge import ui_zoom
from chimerax.codex_bridge.display_controls import DisplayControlsWidget

ui_zoom.UiZoomSettings.save = lambda *args, **kwargs: None  # never touch real preferences


def settle():
    for _ in range(6):
        app.processEvents()


assert ui_zoom.scale_stylesheet("font-size: 11px; border: 1px solid; padding: 2px 6px; margin: 0",
                                1.25) == "font-size: 14px; border: 1px solid; padding: 2px 8px; margin: 0"
assert ui_zoom.scale_stylesheet("QWidget#Box12px { font: 9pt; }", 2.0) == "QWidget#Box12px { font: 18pt; }"

host = QWidget()
layout = QVBoxLayout(host)
label = QLabel("styled")
label.setStyleSheet("font-size: 11px; padding: 2px 6px; border: 1px solid gray;")
button = QToolButton()
button.setFixedSize(48, 60)
button.setIconSize(QSize(28, 28))
explicit = QPushButton("explicit font")
font = explicit.font()
font.setPixelSize(11)
explicit.setFont(font)
for widget in (label, button, explicit):
    layout.addWidget(widget)
host.resize(300, 200)
host.show()
panel = DisplayControlsWidget(session)
panel.resize(360, 540)
panel.show()
settle()

base_app_point = app.font().pointSizeF()
base_panel_sheets = {id(w): w.styleSheet() for w in panel.findChildren(QWidget) if w.styleSheet()}
zoom = ui_zoom.UiZoom(session)

started = time.monotonic()
zoom.set_factor(1.25, announce=False)
zoom_seconds = time.monotonic() - started
settle()
assert "font-size: 14px" in label.styleSheet() and "1px solid" in label.styleSheet(), label.styleSheet()
assert (button.minimumWidth(), button.maximumHeight()) == (60, 75), (button.minimumSize(), button.maximumSize())
assert button.iconSize() == QSize(35, 35), button.iconSize()
assert explicit.font().pixelSize() == 14
assert abs(app.font().pointSizeF() - base_app_point * 1.25) < 0.01
scaled_panel = sum(1 for w in panel.findChildren(QWidget)
                   if w.styleSheet() and w.styleSheet() != base_panel_sheets.get(id(w)))
assert scaled_panel > 0, "Display Controls style sheets were not zoomed"

# Repeated zoom always scales from the base, never compounds.
zoom.set_factor(1.5, announce=False)
zoom.set_factor(1.25, announce=False)
assert "font-size: 14px" in label.styleSheet() and button.iconSize() == QSize(35, 35)

# Code that restyles or creates widgets after zooming is caught by the filter.
label.setStyleSheet("font-size: 10px;")
late = QPushButton("late")
late.setFixedHeight(20)
layout.addWidget(late)
late.show()
settle()
assert label.styleSheet() == "font-size: 12px;", label.styleSheet()
assert late.minimumHeight() == 25, late.minimumHeight()

# Step levels and exact restoration at 100 %.
zoom.reset()
assert zoom.factor == 1.0
zoom.zoom_in(); assert zoom.factor == 1.1
zoom.zoom_in(); assert zoom.factor == 1.25
zoom.zoom_out(); zoom.zoom_out(); zoom.zoom_out(); assert zoom.factor == 0.9
zoom.reset()
settle()
assert label.styleSheet() == "font-size: 10px;"
assert (button.minimumWidth(), button.maximumHeight()) == (48, 60) and button.iconSize() == QSize(28, 28)
assert explicit.font().pixelSize() == 11 and late.minimumHeight() == 20
assert abs(app.font().pointSizeF() - base_app_point) < 0.01
restored = {id(w): w.styleSheet() for w in panel.findChildren(QWidget) if w.styleSheet()}
assert restored == base_panel_sheets, "Display Controls style sheets not restored at 100 %"

# Keys work in the main window, docked and floating tool windows (incl. while a
# competing QAction binds the same key), but leave the Help viewer its own zoom.
window = QMainWindow()
window.setCentralWidget(QLabel("graphics"))
window.show()
docked_edit = QLineEdit()
dock = QDockWidget("tool", window)
dock.setWidget(docked_edit)
window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
from Qt.QtGui import QAction, QKeySequence
rival = QAction("Help zoom", dock)
rival.setShortcut(QKeySequence("Ctrl+="))
rival_hits = []
rival.triggered.connect(lambda: rival_hits.append(1))
dock.addAction(rival)


class HelpPage(QLineEdit):
    pass


HelpPage.__module__ = "chimerax.help_viewer.tool"
help_page = HelpPage()
help_dock = QDockWidget("Help", window)
help_dock.setWidget(help_page)
window.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, help_dock)
settle()
zoom.enable_keys()


def press(widget, key):
    widget.window().activateWindow()
    widget.setFocus()
    settle()
    QTest.keyClick(widget, key, Qt.KeyboardModifier.ControlModifier)
    settle()
    return zoom.factor


keys = {}
assert press(docked_edit, Qt.Key.Key_Equal) == 1.1
keys["docked"] = zoom.factor
assert press(docked_edit, Qt.Key.Key_Minus) == 1.0
dock.setFloating(True)
dock.show()
settle()
assert press(docked_edit, Qt.Key.Key_Equal) == 1.1
keys["floating"] = zoom.factor
assert press(docked_edit, Qt.Key.Key_0) == 1.0
assert not rival_hits, "zoom key leaked to a competing QAction"
assert press(help_page, Qt.Key.Key_Equal) == 1.0, "Help viewer must keep its own zoom keys"
keys["help_untouched"] = zoom.factor
keys = sorted(keys)

print("UI_ZOOM_OK " + json.dumps({"widgets": len(app.allWidgets()), "zoom_seconds": round(zoom_seconds, 3),
                                  "panel_sheets_scaled": scaled_panel, "keys": keys}))
