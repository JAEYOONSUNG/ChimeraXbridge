"""Refresh workspace chrome and restore the tabbed sidebar without rebuilding tools."""
import importlib
import runpy
from pathlib import Path
from Qt.QtCore import QTimer


def reload_workspace_layout():
    import chimerax.codex_bridge as package
    from chimerax.codex_bridge import panel_layout, runtime_patches
    importlib.invalidate_caches()
    importlib.reload(package)
    importlib.reload(panel_layout)
    importlib.reload(runtime_patches)
    runtime_patches.apply_runtime_patches(session)
    # The sequence reloader preserves the chain, query and mouse binding.
    runpy.run_path(str(Path(__file__).with_name("reload_sequence_ui.py")),
                   init_globals={"session": session})

    def arrange():
        panel_layout.set_panel_layout(session, "tabs")
        session.logger.status("Panels restored to the right sidebar with bottom tabs.")
    QTimer.singleShot(150, arrange)


QTimer.singleShot(0, reload_workspace_layout)
