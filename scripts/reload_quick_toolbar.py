"""Reload only the six quick actions and their results pane, retaining the scene."""
import importlib
from pathlib import Path
import shutil
from Qt.QtCore import QTimer


def reload_quick_toolbar():
    source = Path(__file__).resolve().parents[1] / "src"
    filenames = ("__init__.py", "toolbar_actions.py", "quick_context.py", "quick_actions.py",
                 "quick_results.py", "quick_analyze.py", "quick_pockets.py", "quick_views.py",
                 "quick_cache.py", "quick_reports.py", "panel_scroll.py", "ui_theme.py")
    targets = list((Path.home() / "Library/Application Support/ChimeraX").glob(
        "*/lib/python/site-packages/chimerax/codex_bridge"))
    build = source.parent / "build/lib/chimerax/codex_bridge"
    if build.exists():
        targets.append(build)
    for target in targets:
        for name in filenames:
            if (target / name).resolve() != (source / name).resolve():
                shutil.copy2(source / name, target / name)
    # Old panels can already have lost their native dock during a reload.
    # Make the cleanup helper available before deleting any of those panels.
    importlib.reload(importlib.import_module("chimerax.codex_bridge.ui_theme"))
    previous = getattr(session, "_codex_quick_controller", None)
    if previous is not None:
        previous.close()
        # Controllers from the first implementation kept a quit handler after
        # close. Remove that old-version reference during this targeted reload.
        quit_handler = getattr(previous, "_quit_handler", None)
        if quit_handler is not None:
            quit_handler.remove()
            previous._quit_handler = None
    for tool in list(session.tools.list()):
        if tool.tool_name == "Quick Results":
            tool.delete()
    import chimerax.codex_bridge as package
    importlib.invalidate_caches()
    importlib.reload(package)
    for name in ("quick_cache", "quick_reports", "quick_context", "quick_analyze", "quick_pockets", "quick_views",
                 "quick_results", "quick_actions", "toolbar_actions"):
        module = importlib.import_module("chimerax.codex_bridge." + name)
        importlib.reload(module)
    package._install_runtime_toolbar_buttons(session, force_rebuild=True)
    from chimerax.toolbar.tool import get_toolbar_singleton
    toolbar = get_toolbar_singleton(session, create=True)
    toolbar.ttb.show_tab("AI")
    session.logger.status("Quick actions updated: candidate comparison, report export, bounded cache and stale-result handling.")


QTimer.singleShot(0, reload_quick_toolbar)
