"""Real Qt check: ChimeraX --nogui --notools --exit --script <this file>.

The real panel and bookmark actions run against a disposable ChimeraX session;
only the dock host is replaced, so exact panel sizes can be exercised offscreen.
No preferences are written and no existing user process is controlled.
"""
import ctypes
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from Qt.QtCore import QPoint, QPointF, Qt
from Qt.QtGui import QWheelEvent
from Qt.QtWidgets import QApplication, QWidget, QPushButton, QSpinBox, QFileDialog
from chimerax.atomic import AtomicStructure
from chimerax.core.configfile import ConfigFile
from chimerax.geometry import translation
from chimerax.std_commands.view import NamedView, _named_views

ConfigFile.save = lambda *args, **kwargs: None
root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "chimerax.codex_bridge", root / "src/__init__.py",
    submodule_search_locations=[str(root / "src")])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
package._schedule_helper_dock_layout = lambda *args, **kwargs: None
from chimerax.codex_bridge.camera_bookmarks import CameraBookmarks
from chimerax.codex_bridge.camera_bookmark_state import DEFAULT_OPTIONS, bookmark_options

app = QApplication.instance() or QApplication([])


class DisposableToolWindow:
    def __init__(self, tool):
        self.ui_area = QWidget()
        self.shown = True

    def manage(self, **kwargs):
        pass


import chimerax.ui
chimerax.ui.MainToolWindow = DisposableToolWindow
session._codex_bookmark_settings = SimpleNamespace(**DEFAULT_OPTIONS)
session._codex_image_export_settings = SimpleNamespace(
    format="PNG", width=0, height=0, dpi=300, lock_ratio=True,
    transparent=False, directory="")
session.main_view.window_size = (1600, 1200)

model = AtomicStructure(session, name="bookmark UI fixture")
residue = model.new_residue("ALA", "A", 1)
atom = model.new_atom("CA", "C")
atom.coord = (2, 3, 4)
residue.add_atom(atom)
session.models.add([model])
model.atoms.colors = (41, 82, 123, 180)
model.atoms.selected = True
model.position = translation((5, 6, 7))
session.main_view.camera.position = translation((0, 0, 25))
session.main_view.background_color = (0.15, 0.2, 0.3, 1)
native = NamedView(session.main_view, (0, 0, 0), session.models.list())
_named_views(session).views["Existing native view"] = native


def scene():
    return (model.atoms.colors.tobytes(), model.atoms.selected.tobytes(),
            model.atoms.displays.tobytes(), model.position.matrix.tobytes(),
            session.main_view.camera.position.matrix.tobytes(),
            tuple(session.main_view.background_color),
            session.main_view.lighting.ambient_light_intensity)


before_construction = scene()
panel = CameraBookmarks(session, "Camera Bookmarks")
host = panel.tool_window.ui_area
assert _named_views(session).views["Existing native view"] is native
assert panel.list_widget.count() == 1
assert scene() == before_construction, "Opening the panel modified the scene"
assert panel._save_options() == DEFAULT_OPTIONS
assert panel.preset_combo.currentData() == "appearance"
assert panel.export_format.currentText() == "PNG"
assert (panel.export_width.value(), panel.export_height.value()) == (1600, 1200)
assert panel.export_dpi.value() == 300 and panel.export_lock_ratio.isChecked()


def settle():
    for _ in range(4):
        app.processEvents()


# QApplication.sendEvent deliberately does not bubble non-spontaneous wheel
# events. Use Qt's exported native dispatcher to exercise actual QWindow
# hit-testing and parent propagation, without controlling the user's mouse.
from PyQt6 import sip
from PyQt6.QtCore import QLibraryInfo
qt_library_dir = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.LibrariesPath))
qtcore = ctypes.CDLL(str(qt_library_dir / "QtCore.framework/Versions/A/QtCore"))
native_send = getattr(qtcore, "_ZN16QCoreApplication20sendSpontaneousEventEP7QObjectP6QEvent")
native_send.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
native_send.restype = ctypes.c_bool


def wheel(target, *, pixel=False, direct=False, direction=-1):
    pos = QPointF(3, 3) if target is panel.content else QPointF(target.rect().center())
    event = QWheelEvent(pos, QPointF(target.mapToGlobal(pos.toPoint())),
                        QPoint(0, direction * 60) if pixel else QPoint(),
                        QPoint() if pixel else QPoint(0, direction * 120),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    if direct:
        app.sendEvent(target, event)
    else:
        routed = QWheelEvent(QPointF(target.mapTo(host, pos.toPoint())),
                             QPointF(target.mapToGlobal(pos.toPoint())),
                             event.pixelDelta(), event.angleDelta(),
                             Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                             Qt.ScrollPhase.NoScrollPhase, False)
        native_send(sip.unwrapinstance(host.windowHandle()), sip.unwrapinstance(routed))
    settle()


def panel_point(widget):
    return widget.mapTo(panel.content, QPoint())


# Image dimensions and print resolution are independent. A locked ratio follows
# edits in either direction, and Current restores the graphics viewport size.
export_scene = scene()
views_before_export = tuple(_named_views(session).views.items())
panel.export_width.setValue(2400)
assert panel.export_height.value() == 1800
panel.export_height.setValue(900)
assert panel.export_width.value() == 1200
panel.export_lock_ratio.setChecked(False)
panel.export_width.setValue(1000)
assert panel.export_height.value() == 900
panel.export_height.setValue(500)
panel.export_lock_ratio.setChecked(True)
panel.export_width.setValue(1800)
assert panel.export_height.value() == 900
panel.export_dpi.setValue(600)
assert (panel.export_width.value(), panel.export_height.value()) == (1800, 900)
assert "600 DPI" in panel.export_size_hint.toolTip()
assert panel._export_settings.dpi == 600
panel.export_current_button.click()
assert (panel.export_width.value(), panel.export_height.value()) == (1600, 1200)
panel.export_width.setValue(800)
assert panel.export_height.value() == 600
assert scene() == export_scene

# The real export button must forward precisely the chosen output settings.
# Rendering/file metadata are covered by check_image_export.py; only this UI
# test's destination chooser and renderer are replaced, avoiding real dialogs.
from chimerax.codex_bridge import image_export as export_module
export_calls, dialog_calls = [], []
original_export = export_module.export_image
original_dialog = QFileDialog.getSaveFileName
destination = [""]


def choose_export(*args, **kwargs):
    dialog_calls.append((args, kwargs))
    return destination[0], ""


def record_export(ses, path, **options):
    assert ses is session
    assert not panel.export_button.isEnabled()
    assert QApplication.overrideCursor() is not None
    export_calls.append({"path": path, **options})
    return export_calls[-1]


try:
    QFileDialog.getSaveFileName = choose_export
    export_module.export_image = record_export
    status_before_cancel = panel.status.toolTip()
    directory_before_cancel = panel._export_settings.directory
    panel.export_button.click()
    assert not export_calls, "Cancel called the export backend"
    assert panel.status.toolTip() == status_before_cancel
    assert panel._export_settings.directory == directory_before_cancel
    assert panel.export_button.isEnabled() and QApplication.overrideCursor() is None
    panel.name_input.setText("figure:test/path")
    panel.export_transparent.setChecked(True)
    for format_name, suffix in (("PNG", ".png"), ("JPEG", ".jpg"), ("TIFF", ".tif")):
        panel.export_format.setCurrentText(format_name)
        assert panel._export_settings.format == format_name
        assert panel.export_transparent.isEnabled() == (format_name != "JPEG")
        assert format_name in panel.export_button.text()
        destination[0] = "/tmp/bookmark-ui-export" + suffix
        panel.export_button.click()
        assert export_calls[-1] == {
            "path": destination[0], "format": format_name, "width": 800,
            "height": 600, "dpi": 600, "transparent": format_name != "JPEG"}
        assert dialog_calls[-1][0][2].endswith("figure-test-path" + suffix)
        assert panel._export_settings.directory == "/tmp"
        assert panel.status.toolTip().startswith("Exported ")
        assert panel.export_button.isEnabled() and QApplication.overrideCursor() is None
        assert scene() == export_scene
        assert tuple(_named_views(session).views.items()) == views_before_export

    def fail_export(*args, **kwargs):
        raise RuntimeError("temporary export failure")

    export_module.export_image = fail_export
    panel.export_button.click()
    assert panel.status.toolTip() == "Export failed: temporary export failure"
    assert panel.export_button.isEnabled() and QApplication.overrideCursor() is None
    assert scene() == export_scene
finally:
    export_module.export_image = original_export
    QFileDialog.getSaveFileName = original_dialog

# Reopening the panel retains the user's format, pixels, DPI, ratio and alpha
# preferences without changing a model or creating a bookmark.
reopened = CameraBookmarks(session, "Camera Bookmarks preferences check")
assert reopened.export_format.currentText() == "TIFF"
assert (reopened.export_width.value(), reopened.export_height.value(), reopened.export_dpi.value()) == (800, 600, 600)
assert reopened.export_lock_ratio.isChecked() and reopened.export_transparent.isChecked()
assert scene() == export_scene
assert tuple(_named_views(session).views.items()) == views_before_export
reopened.tool_window.ui_area.close()
reopened.delete()
panel.export_format.setCurrentText("PNG")
panel.export_dpi.setValue(300)
panel.export_transparent.setChecked(False)
panel.export_current_button.click()
panel.name_input.clear()
panel._set_status("")


geometry = []
for width, height in ((380, 480), (560, 480), (360, 280)):
    host.resize(width, height)
    host.show()
    settle()
    scroll = panel.scroll_area
    vertical = scroll.verticalScrollBar()
    horizontal = scroll.horizontalScrollBar()
    vertical.setValue(0)
    settle()
    capture = f"/tmp/camera-bookmarks-{width}x{height}-top.png"
    assert host.grab().save(capture)
    assert host.width() == width and host.height() == height, (
        "Panel imposed a larger dock size", width, height, host.size())
    assert horizontal.maximum() == 0, ("Horizontal overflow", width, horizontal.maximum())
    assert panel.content.width() == scroll.viewport().width(), (
        "Content wider than the viewport", width, panel.content.width(), scroll.viewport().width())
    assert all(button.width() == 36 for button in panel.rotation_buttons)
    rotation_right = max(panel_point(button).x() + button.width() for button in panel.rotation_buttons)
    assert panel_point(panel.export_group).x() > rotation_right
    controls = [*panel.rotation_buttons, panel.focus_btn,
                panel.reset_btn, panel.save_btn, panel.refresh_btn, panel.rename_btn,
                panel.delete_btn, panel.legend_button, panel.more_button,
                panel.name_input, panel.cofr_combo, panel.step_combo,
                panel.export_format, panel.export_dpi, panel.export_width, panel.export_height,
                panel.export_lock_ratio, panel.export_current_button, panel.export_transparent,
                panel.export_button, panel.bookmark_options_button]
    for control in controls:
        start = panel_point(control)
        assert start.x() >= 0 and start.x() + control.width() <= panel.content.width(), (
            width, type(control).__name__, start.x(), control.width(), panel.content.width())
        if isinstance(control, QPushButton):
            assert control.fontMetrics().horizontalAdvance(control.text()) + 12 <= control.width(), (
                "Button text clipped", width, control.text(), control.width())
        if isinstance(control, QSpinBox):
            assert control.fontMetrics().horizontalAdvance(str(control.maximum())) + 4 <= control.lineEdit().width(), (
                "Image dimension text clipped", width, control.toolTip(), control.width(), control.lineEdit().width())
    if height >= 480:
        assert vertical.maximum() == 0, ("Unexpected scrolling in tall panel", width, vertical.maximum())
    else:
        assert vertical.maximum() > 0 and vertical.isVisible(), "Short panel has no vertical scroll"
        vertical.setValue(vertical.maximum())
        settle()
        for control in (panel.refresh_btn, panel.rename_btn, panel.delete_btn,
                        panel.legend_button, panel.more_button, panel.status):
            start = control.mapTo(scroll.viewport(), QPoint())
            assert 0 <= start.y() and start.y() + control.height() <= scroll.viewport().height(), (
                "Footer is unreachable", type(control).__name__, start.y(), control.height())
        assert host.grab().save(f"/tmp/camera-bookmarks-{width}x{height}-bottom.png")
        for direct in (False, True):
            for pixel in (False, True):
                for target in (panel.content, panel.cofr_combo, panel.step_combo,
                               panel.export_format, panel.export_dpi, panel.export_width,
                               panel.export_height, panel.export_transparent, panel.export_lock_ratio):
                    vertical.setValue(0)
                    index = target.currentIndex() if hasattr(target, "currentIndex") else None
                    options_before = panel._save_options()
                    export_before = vars(panel._export_settings).copy()
                    wheel(target, pixel=pixel, direct=direct)
                    assert vertical.value() > 0, (
                        "Wheel did not reach panel", type(target).__name__, direct, pixel)
                    if index is not None:
                        assert target.currentIndex() == index, "Wheel changed the selected condition"
                    assert panel._save_options() == options_before, "Wheel changed condition toggles"
                    assert vars(panel._export_settings) == export_before, "Wheel changed image settings"
    geometry.append({"size": [width, height], "content": [panel.content.width(), panel.content.height()],
                     "vertical_maximum": vertical.maximum(), "horizontal_maximum": horizontal.maximum(),
                     "screenshot": capture})

# Exercise actual signals and actual condition-aware saves/restores.
panel.scroll_area.ensureWidgetVisible(panel.bookmark_options_button)
options_menu = panel.bookmark_options_button.menu()
options_menu.popup(panel.bookmark_options_button.mapToGlobal(QPoint(0, panel.bookmark_options_button.height())))
settle()
assert panel.conditions_group.isVisible(), "Bookmark condition popup did not open"
for button in panel.include_buttons.values():
    point = button.mapTo(panel.conditions_group, QPoint())
    assert point.x() >= 0 and point.x() + button.width() <= panel.conditions_group.width()
assert panel.conditions_group.grab().save("/tmp/camera-bookmarks-options-popup.png")
panel.preset_combo.setCurrentIndex(panel.preset_combo.findData("camera"))
assert panel._save_options() == {key: key == "camera" for key in DEFAULT_OPTIONS}
panel.include_buttons["colors"].click()
assert panel.preset_combo.currentData() == "custom"
assert panel._save_options()["camera"] and panel._save_options()["colors"]
panel.preset_combo.setCurrentIndex(panel.preset_combo.findData("appearance"))
assert panel._save_options() == DEFAULT_OPTIONS
panel.include_buttons["selection"].click()
assert all(panel._save_options().values())
options_menu.hide()
saved_scene = scene()
name = '조건 "A"; bookmark with spaces!'
panel.name_input.setText(name)
panel.save_btn.click()
assert panel.name_input.text() == ""
assert panel.list_widget.currentItem().text() == name
assert bookmark_options(session, name) == dict(DEFAULT_OPTIONS, selection=True)
assert scene() == saved_scene, "Saving changed the scene"
model.atoms.colors = (240, 1, 2, 255)
model.atoms.selected = False
model.atoms.displays = False
model.position = translation((15, 16, 17))
session.main_view.camera.position = translation((0, 0, 50))
session.main_view.background_color = (1, 1, 1, 1)
panel.list_widget.itemClicked.emit(panel.list_widget.currentItem())
assert scene() == saved_scene, panel.status.toolTip()
assert panel.status.toolTip().startswith("Restored:")
assert _named_views(session).views["Existing native view"] is native

# Empty conditions disable the save button and are correctly classified custom.
for button in panel.include_buttons.values():
    button.setChecked(False)
assert not panel.save_btn.isEnabled()
assert panel.preset_combo.currentData() == "custom"
panel.include_buttons["colors"].click()
assert panel.save_btn.isEnabled()
panel.name_input.setText("")
panel.name_input.returnPressed.emit()
assert "View 1" in _named_views(session).views
assert bookmark_options(session, "View 1") == {key: key == "colors" for key in DEFAULT_OPTIONS}

# Nested bookmark lists scroll their own entries, then release the panel at an
# edge so the footer remains accessible even when the pointer is over the list.
for index in range(20):
    _named_views(session).views[f"Fixture bookmark {index:02d}"] = native
panel._refresh_list()
settle()
outer_bar = panel.scroll_area.verticalScrollBar()
inner_bar = panel.list_widget.verticalScrollBar()
assert inner_bar.maximum() > 0
for direct in (False, True):
    for pixel in (False, True):
        outer_bar.setValue(0)
        panel.scroll_area.ensureWidgetVisible(panel.list_widget, 0, 0)
        settle()
        outer_before = outer_bar.value()
        inner_bar.setValue(0)
        wheel(panel.list_widget.viewport(), pixel=pixel, direct=direct)
        assert inner_bar.value() > 0, ("Bookmark entries did not scroll", direct, pixel)
        assert outer_bar.value() == outer_before, "Panel scrolled before list entries"
        inner_bar.setValue(inner_bar.maximum())
        wheel(panel.list_widget.viewport(), pixel=pixel, direct=direct)
        assert outer_bar.value() > outer_before, ("List edge did not scroll panel", direct, pixel)

# Long names/errors remain available in the tooltip without growing the layout.
host.resize(360, 280)
panel._set_status("Save failed: " + "long condition description " * 20, err=True)
settle()
assert host.width() == 360 and panel.content.width() == panel.scroll_area.viewport().width()
assert "long condition" in panel.status.toolTip() and len(panel.status.text()) < len(panel.status.toolTip())

report = {"ok": True, "geometry": geometry, "wheel_and_trackpad": True,
          "nested_list_scroll": True,
          "image_export_settings": True, "export_ratio_and_dimensions": True,
          "export_ui_calls": export_calls, "export_cancel_and_failure": True,
          "export_settings_preserved_on_reopen": True,
          "preset_conditions": True, "actual_save_and_restore": True,
          "existing_bookmarks_preserved": True, "construction_preserves_scene": True}
Path("/tmp/camera-bookmark-ui-check.json").write_text(json.dumps(report, indent=2))
print("CAMERA_BOOKMARK_UI_OK", json.dumps(report))
host.close()
panel.delete()
