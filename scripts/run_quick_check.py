"""Run a quick-toolbar check in an isolated ChimeraX process."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    label = sys.argv[1] if len(sys.argv) == 2 else ""
    if label not in ("analyze", "pockets", "views", "runtime", "reports", "refinement", "integration"):
        raise SystemExit("Choose analyze, pockets, views, runtime, reports, refinement or integration")
    if (label in ("runtime", "refinement", "integration")
            and os.environ.get("CODEX_ALLOW_VISIBLE_GUI_TESTS") != "1"):
        raise SystemExit("This check opens visible ChimeraX windows. Use analyze/pockets/views/reports "
                         "for headless checks. Only after explicit user authorization, set "
                         "CODEX_ALLOW_VISIBLE_GUI_TESTS=1 to run a visible check.")
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / f"check_quick_{label}.py"
    binary = os.environ.get("CHIMERAX_BIN", "/Applications/ChimeraX-1.10.1.app/Contents/bin/ChimeraX")
    marker = f"QUICK_{label.upper()}_OK"
    with tempfile.TemporaryDirectory(prefix="quick-toolbar-") as directory:
        driver = Path(directory) / "check.py"
        setup = ("from chimerax.toolbar.tool import get_toolbar_singleton\n"
                 "get_toolbar_singleton(session, create=True)\n") if label in ("runtime", "refinement", "integration") else ""
        driver.write_text("import runpy\nfrom chimerax.core.configfile import ConfigFile\n"
                          "ConfigFile.save = lambda *args, **kwargs: None\n" + setup +
                          f"runpy.run_path({str(script)!r}, init_globals={{'session': session}})\n")
        command = [binary, "--notools", "--exit", "--script", str(driver)]
        if label not in ("runtime", "refinement", "integration"):
            command.insert(1, "--nogui")
        environment = dict(os.environ, CODEX_BRIDGE_AUTORELOAD="0")
        process = subprocess.run(command, cwd=root, env=environment, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True, timeout=240)
    log = Path(tempfile.gettempdir()) / f"quick-{label}-check.log"
    log.write_text(process.stdout)
    if process.returncode or marker not in process.stdout:
        print(process.stdout[-18000:])
        raise SystemExit(process.returncode or 1)
    print(process.stdout[-9000:])


if __name__ == "__main__":
    main()
