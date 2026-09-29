"""ChimeraX: runscript <this file> -- report why Codex Bridge toolbar icons are blank.

Run inside the installed ChimeraX GUI (e.g. on Windows) and paste the
TOOLBAR_ICON_DIAG line from the Log.
"""
import json
import os
import sys

from Qt.QtCore import QLibraryInfo, QSize
from Qt.QtGui import QIcon, QImageReader
from Qt.QtWidgets import QApplication

_APP = []  # keeps a --nogui QApplication alive

def collect(session):
    """Return the icon diagnostic dict (also used by windows_selfcheck.py)."""
    if QApplication.instance() is None:
        _APP.append(QApplication(sys.argv))
    report = {"platform": sys.platform}
    bi = session.toolshed.find_bundle("ChimeraX-CodexBridge", session.logger, installed=True)
    report["bundle"] = None if bi is None else f"{bi.name} {bi.version}"
    icons_dir = None if bi is None else bi.get_path("icons")
    report["icons_dir"] = icons_dir
    if icons_dir and os.path.isdir(icons_dir):
        names = os.listdir(icons_dir)
        report["svg_files"] = sum(n.endswith(".svg") for n in names)
        report["png_files"] = sum(n.endswith(".png") for n in names)
        redrawn = os.path.join(icons_dir, "original", "redrawn")
        report["redrawn_files"] = len(os.listdir(redrawn)) if os.path.isdir(redrawn) else 0
        for name in ("ai-analyze.svg", "blast-logo.png"):
            path = os.path.join(icons_dir, name)
            icon = QIcon(path)
            report[f"qicon_{name}"] = "ok" if not icon.pixmap(QSize(28, 28)).isNull() else "NULL"

    report["image_formats"] = sorted(bytes(f).decode() for f in QImageReader.supportedImageFormats())
    report["svg_supported"] = "svg" in report["image_formats"]
    report["qt_plugins_path"] = QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath)
    for sub in ("iconengines", "imageformats"):
        folder = os.path.join(report["qt_plugins_path"], sub)
        report[sub] = sorted(os.listdir(folder)) if os.path.isdir(folder) else "missing"

    try:
        from chimerax.codex_bridge import icon_theme
        report["icon_theme"] = icon_theme.icon_theme(session)
        key = ("AI", "Quick", "Analyze")
        path = icon_theme.icon_path(key, report["icon_theme"])
        report["theme_path"] = str(path)
        report["theme_path_exists"] = path.exists()
        icon = icon_theme._toolbar_icon(path)
        report["theme_icon"] = "ok" if not icon.pixmap(QSize(28, 28)).isNull() else "NULL"
    except Exception as error:  # report, don't abort
        report["icon_theme_error"] = repr(error)

    try:
        from chimerax.toolbar.tool import get_toolbar_singleton
        from Qt.QtWidgets import QToolButton
        ttb = get_toolbar_singleton(session, create=False).ttb
        blank = []
        total = 0
        for button in ttb.findChildren(QToolButton):
            action = button.defaultAction()
            if action is None or not action.property("codexToolbarArtwork"):
                continue
            total += 1
            if action.icon().pixmap(QSize(28, 28)).isNull():
                blank.append(" ".join(button.text().split()))
        report["toolbar_buttons"] = total
        report["toolbar_blank"] = blank[:20]
    except Exception as error:
        report["toolbar_error"] = repr(error)

    return report


if "session" in globals():  # runscript; windows_selfcheck imports collect()
    session.logger.info("TOOLBAR_ICON_DIAG " + json.dumps(collect(session)))
