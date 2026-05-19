"""Source-watcher that re-runs reload_codex_ui.py whenever the repo source changes.

When the user edits the package source in a writable checkout, this watcher
detects the change via a periodic mtime poll and triggers the existing
`scripts/reload_codex_ui.py` reload pipeline -- so the running ChimeraX session
picks up the change without the user having to invoke the reload script
themselves.

Behaviour:
  - Install once per session (`session._codex_bridge_auto_reload_installed`).
  - Polls every ``_POLL_INTERVAL_MS`` ms and only fires after the watched
    files have been stable for ``_DEBOUNCE_MS`` ms, so a half-saved edit
    doesn't trigger a partial reload.
  - Skips the reload if ChimeraX is currently running a foreground task (the
    user is mid-action).
  - Honour ``CODEX_BRIDGE_AUTORELOAD=0`` to opt out, and
    ``CODEX_BRIDGE_REPO_SRC=<path>`` to override the source path lookup.
"""
from __future__ import annotations

import os
import time
from pathlib import Path


_POLL_INTERVAL_MS = 1500
_DEBOUNCE_MS = 1500

# Patterns (glob, relative to the repo src dir) the watcher monitors. We
# include icons/*.svg|*.png too because SVG/icon edits change the rendered
# toolbar and should trigger a rebuild just like Python edits do.
_WATCHED_GLOBS = (
    "*.py",
    "icons/*.svg",
    "icons/*.png",
)


def install_auto_reload_watcher(session):
    """Idempotent: install the source-mtime watcher once per session."""
    if os.environ.get("CODEX_BRIDGE_AUTORELOAD", "1") == "0":
        return
    if getattr(session, "_codex_bridge_auto_reload_installed", False):
        return
    ui = getattr(session, "ui", None)
    if ui is None or not getattr(ui, "is_gui", False):
        return
    try:
        from Qt.QtCore import QTimer
    except Exception:
        return

    repo_src = _find_repo_source_dir()
    if repo_src is None:
        # Bundle was installed from an unknown location; don't keep polling.
        try:
            session.logger.info(
                "Codex Bridge auto-reload disabled: repo source not found "
                "(set CODEX_BRIDGE_REPO_SRC to enable)."
            )
        except Exception:
            pass
        return

    script_path = repo_src.parent / "scripts" / "reload_codex_ui.py"
    if not script_path.exists():
        try:
            session.logger.info(
                f"Codex Bridge auto-reload disabled: missing {script_path}"
            )
        except Exception:
            pass
        return

    state = {
        "last_mtime": _max_mtime(repo_src),
        "stable_since": None,
        "reload_in_flight": False,
    }

    def _poll(_ses=session, _src=repo_src, _script=script_path, _state=state):
        try:
            if _state["reload_in_flight"]:
                return
            current = _max_mtime(_src)
            now = time.monotonic()
            if current > _state["last_mtime"]:
                _state["last_mtime"] = current
                _state["stable_since"] = now
                return
            stable_since = _state["stable_since"]
            if stable_since is None:
                return
            if (now - stable_since) * 1000.0 < _DEBOUNCE_MS:
                return
            _state["stable_since"] = None
            _state["reload_in_flight"] = True
            try:
                _trigger_reload(_ses, _script)
            finally:
                # Re-enable after a beat so the post-reload mtime touches from
                # the sync_repo_source step don't immediately re-fire.
                QTimer.singleShot(
                    3000,
                    lambda s=_state: s.__setitem__("reload_in_flight", False),
                )
        except Exception:
            pass

    timer = QTimer()
    timer.setInterval(_POLL_INTERVAL_MS)
    timer.timeout.connect(_poll)
    timer.start()
    session._codex_bridge_auto_reload_timer = timer
    session._codex_bridge_auto_reload_installed = True
    try:
        session.logger.info(
            f"Codex Bridge auto-reload armed (watching {repo_src})."
        )
    except Exception:
        pass


def _max_mtime(repo_src: Path) -> float:
    latest = 0.0
    for pattern in _WATCHED_GLOBS:
        for path in repo_src.glob(pattern):
            try:
                t = path.stat().st_mtime
            except OSError:
                continue
            if t > latest:
                latest = t
    return latest


def _trigger_reload(session, script_path: Path) -> None:
    try:
        session.logger.info(
            f"Codex Bridge auto-reload: source changed, running {script_path.name}"
        )
    except Exception:
        pass
    try:
        from chimerax.core.commands import run as cmd_run

        # Quote the path so checkouts inside directories with spaces work.
        cmd_run(session, f'runscript "{script_path}"')
    except Exception as err:
        try:
            session.logger.warning(f"Codex Bridge auto-reload failed: {err}")
        except Exception:
            pass


def _find_repo_source_dir() -> "Path | None":
    """Locate a writable repo source tree, not just an installed copy.

    Strategy:
      1. Honour ``CODEX_BRIDGE_REPO_SRC`` env var.
      2. Use the current package directory when it is inside a source checkout.
      3. Check a few generic checkout locations under the user's home folder.
    """
    env_override = os.environ.get("CODEX_BRIDGE_REPO_SRC")
    if env_override:
        candidate = Path(env_override).expanduser()
        if (candidate / "__init__.py").exists():
            return candidate

    home = Path.home()
    package_src = Path(__file__).resolve().parent
    well_known = [
        package_src,
        home / "ChimeraXbridge" / "src",
        home / "Projects" / "ChimeraXbridge" / "src",
        home / "Code" / "ChimeraXbridge" / "src",
    ]
    for c in well_known:
        if (c / "__init__.py").exists() and (c.parent / "scripts" / "reload_codex_ui.py").exists():
            return c
    return None
