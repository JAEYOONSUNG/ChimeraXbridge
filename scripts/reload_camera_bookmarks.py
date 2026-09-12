"""Reload only Camera Bookmarks, preserving saved conditions and the live scene."""
import importlib
from pathlib import Path
import shutil

from Qt.QtCore import QTimer


def reload_camera_bookmarks():
    source = Path(__file__).resolve().parents[1] / "src"
    targets = list((Path.home() / "Library/Application Support/ChimeraX").glob(
        "*/lib/python/site-packages/chimerax/codex_bridge"))
    build = source.parent / "build/lib/chimerax/codex_bridge"
    if build.exists():
        targets.append(build)
    for target in targets:
        for filename in ("__init__.py", "camera_bookmarks.py", "camera_bookmark_state.py", "image_export.py", "panel_scroll.py", "ui_theme.py"):
            if (target / filename).resolve() != (source / filename).resolve():
                shutil.copy2(source / filename, target / filename)

    importlib.reload(importlib.import_module("chimerax.codex_bridge.ui_theme"))
    old_tools = [tool for tool in session.tools.list() if tool.tool_name == "Camera Bookmarks"]
    old = next((tool for tool in old_tools if tool.displayed()), old_tools[0] if old_tools else None)
    draft = old.name_input.text() if old is not None else ""
    step = old.step_combo.currentText() if old is not None else "90°"
    pivot = old.cofr_combo.currentIndex() if old is not None else 0
    selected = old.list_widget.currentItem() if old is not None else None
    selected_name = selected.text() if selected is not None else None
    for tool in old_tools:
        tool.delete()

    import chimerax.codex_bridge as package
    importlib.invalidate_caches()
    importlib.reload(package)
    from chimerax.codex_bridge import camera_bookmarks, camera_bookmark_state, image_export
    importlib.reload(camera_bookmark_state)
    manager = getattr(session, "_codex_camera_bookmark_state", None)
    if manager is not None:
        # Session serialization requires the instance's class to match the
        # bundle's current get_class result after a source reload.
        manager.__class__ = camera_bookmark_state.CameraBookmarkState
    importlib.reload(image_export)
    importlib.reload(camera_bookmarks)
    tool = camera_bookmarks.CameraBookmarks.get_singleton(session)
    tool.name_input.setText(draft)
    tool.step_combo.setCurrentText(step)
    tool.cofr_combo.setCurrentIndex(pivot)
    for index in range(tool.list_widget.count()):
        item = tool.list_widget.item(index)
        if item.text() == selected_name:
            tool.list_widget.setCurrentItem(item)
            break
    tool.display(True)

    def fit_window():
        # Old fixed minimum heights can leave a window taller than its screen.
        window = session.ui.main_window
        screen = window.screen()
        if screen is not None:
            available = screen.availableGeometry()
            if window.frameGeometry().height() > available.height():
                window.resize(min(window.width(), available.width()),
                              max(300, available.height() - 40))
        tool.scroll_area.verticalScrollBar().setValue(0)

    QTimer.singleShot(200, fit_window)
    session.logger.status("Bookmarks updated: PNG/JPEG/TIFF export with pixel size, DPI and transparency.")


QTimer.singleShot(0, reload_camera_bookmarks)
