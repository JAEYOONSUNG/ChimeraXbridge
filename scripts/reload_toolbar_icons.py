"""Reload toolbar artwork and its style toggle, keeping open tools and data."""
import importlib
from Qt.QtCore import QTimer


def reload_toolbar():
    from chimerax.codex_bridge import runtime_patches, icon_theme
    from Qt.QtGui import QPixmapCache
    importlib.invalidate_caches()
    importlib.reload(runtime_patches)
    importlib.reload(icon_theme)
    QPixmapCache.clear()
    runtime_patches._patch_tabbedtoolbar_section()
    runtime_patches.style_ai_toolbar(session)
    session.logger.status("Original logos and detailed SVG icons updated; toolbar labels aligned.")


# Return from a native file-open event before updating existing actions.
QTimer.singleShot(0, reload_toolbar)
