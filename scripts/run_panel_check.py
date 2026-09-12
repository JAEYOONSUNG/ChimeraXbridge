"""Run panel scrolling checks in an isolated ChimeraX process."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    checks = {
        "shared": ("check_panel_scroll.py", "PANEL_SCROLL_OK", False),
        "models": ("check_models_caver_scroll.py", "MODELS_CAVER_SCROLL_OK", False),
        "all": ("check_right_panel_scroll.py", "RIGHT_PANEL_SCROLL_OK", True),
        "reload": ("check_right_panel_reload.py", "RIGHT_PANEL_RELOAD_OK", True),
    }
    label = sys.argv[1] if len(sys.argv) == 2 else ""
    if label not in checks:
        raise SystemExit("Choose shared, models, all, or reload")
    filename, marker, gui = checks[label]
    if gui and os.environ.get("CODEX_ALLOW_VISIBLE_GUI_TESTS") != "1":
        raise SystemExit("This check opens visible ChimeraX windows. Use shared/models for "
                         "headless checks. Only after explicit user authorization, set "
                         "CODEX_ALLOW_VISIBLE_GUI_TESTS=1 to run a visible check.")
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / filename
    binary = os.environ.get("CHIMERAX_BIN", "/Applications/ChimeraX-1.10.1.app/Contents/bin/ChimeraX")
    with tempfile.TemporaryDirectory(prefix="right-panel-") as directory:
        driver = Path(directory) / "check.py"
        setup = ("from chimerax.toolbar.tool import get_toolbar_singleton\n"
                 "get_toolbar_singleton(session, create=True)\n") if gui else ""
        driver.write_text("import runpy\nfrom chimerax.core.configfile import ConfigFile\n"
                          "ConfigFile.save = lambda *args, **kwargs: None\n" + setup +
                          f"runpy.run_path({str(script)!r}, init_globals={{'session': session}})\n",
                          encoding="utf-8")
        command = [binary, "--notools", "--exit", "--script", str(driver)]
        if not gui:
            command.insert(1, "--nogui")
        environment = dict(os.environ, CODEX_BRIDGE_AUTORELOAD="0")
        process = subprocess.run(command, cwd=root, env=environment, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True, timeout=240)
    log = Path(tempfile.gettempdir()) / f"right-panel-{label}-check.log"
    log.write_text(process.stdout, encoding="utf-8")
    if process.returncode or marker not in process.stdout:
        print(process.stdout[-24000:])
        raise SystemExit(process.returncode or 1)
    print(process.stdout[-12000:])


if __name__ == "__main__":
    main()
