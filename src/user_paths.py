"""Per-user data locations that follow ChimeraX's own platform conventions."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def chimerax_user_data_dir():
    """Return ChimeraX's per-user data directory for the running version.

    Inside ChimeraX this is ``chimerax.app_dirs.user_data_dir`` (for example
    ``~/Library/Application Support/ChimeraX/1.10`` on macOS). Outside ChimeraX,
    or before app_dirs exists, fall back to the same layout per platform.
    """
    try:
        import chimerax

        app_dirs = getattr(chimerax, "app_dirs", None)
        user_data_dir = getattr(app_dirs, "user_data_dir", None)
        if user_data_dir:
            return Path(user_data_dir)
    except Exception:
        pass

    version = os.environ.get("CODEX_BRIDGE_CHIMERAX_VERSION", "1.10")
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "ChimeraX" / version
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        return base / "UCSF" / "ChimeraX" / version
    base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    return base / "ChimeraX" / version


def codex_bridge_user_dir():
    """Directory for this bundle's persistent per-user files (aliases, events)."""
    override = os.environ.get("CODEX_BRIDGE_USER_DIR")
    if override:
        return Path(override)
    return chimerax_user_data_dir() / "codex_bridge_user"
