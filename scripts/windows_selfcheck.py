"""Windows self-check for the installed Codex Bridge (run inside the ChimeraX GUI).

    runscript "C:\\...\\chimerax_codex_bridge\\scripts\\windows_selfcheck.py"
    runscript "...\\windows_selfcheck.py" --no-suites     (skip headless suites)
    runscript "...\\windows_selfcheck.py" --no-fetch      (no PDB download)
    runscript "...\\windows_selfcheck.py" --suites=panels,sequence  (subset)

Everything lands in <repo>/windows_reports/<host>-<timestamp>/ next to this
script. The repo lives in Dropbox, so the report syncs back to the dev Mac:
report.json (environment, fonts, DPI, icon diagnostics, errors), screenshots of
the main window, every toolbar tab and each Codex Bridge panel, the ChimeraX
log, and (in the background) the headless quality suites run against this
machine's ChimeraX. The GUI phase takes ~1 minute; suites continue afterwards
and finish with WINDOWS_SELFCHECK_SUITES_DONE in the Log.
"""
import json
import locale
import os
import platform
import runpy
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARGS = set(sys.argv[1:])
OUT = ROOT / "windows_reports" / f"{platform.node() or 'host'}-{time.strftime('%Y%m%d-%H%M%S')}"
OUT.mkdir(parents=True, exist_ok=True)
PANELS = ("AI Assistant", "Action Pad", "Sequence Bar", "Display Controls",
          "CAVER", "Camera Bookmarks", "Quick Results")
from Qt.QtWidgets import QApplication
_APP = [] if QApplication.instance() else [QApplication(sys.argv)]  # --nogui dry runs
report = {"root": str(ROOT), "out": str(OUT), "argv": sys.argv[1:], "errors": {}, "screenshots": []}


def save_report():
    (OUT / "report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")


def step(name):
    def wrap(function):
        try:
            report[name] = function()
        except Exception:
            report["errors"][name] = traceback.format_exc()
        save_report()
        return function
    return wrap


def settle(seconds=0.4):
    from Qt.QtWidgets import QApplication
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.02)


def grab(widget, name):
    settle()
    path = OUT / f"{name}.png"
    if widget is not None and widget.grab().save(str(path)):
        report["screenshots"].append(path.name)
        return path.name
    return None


def run(command):
    from chimerax.core.commands import run as chimerax_run
    return chimerax_run(session, command, log=True)


@step("environment")
def _environment():
    from Qt.QtCore import QLibraryInfo
    from Qt.QtGui import QFontDatabase, QGuiApplication
    from chimerax.core import version as chimerax_version
    bundle = session.toolshed.find_bundle("ChimeraX-CodexBridge", session.logger, installed=True)
    families = set(QFontDatabase.families())
    app_font = QGuiApplication.font()
    return {
        "platform": platform.platform(),
        "python": sys.version,
        "executable": sys.executable,
        "chimerax": chimerax_version,
        "bundle": None if bundle is None else f"{bundle.version} @ {Path(bundle.get_path('icons')).parent}",
        "qt": QLibraryInfo.version().toString(),
        "preferred_encoding": locale.getpreferredencoding(False),
        "utf8_mode": sys.flags.utf8_mode,
        "screens": [{"name": s.name(), "dpr": s.devicePixelRatio(), "logical_dpi": s.logicalDotsPerInch(),
                     "size": [s.size().width(), s.size().height()]} for s in QGuiApplication.screens()],
        "app_font": [app_font.family(), app_font.pointSizeF(), app_font.pixelSize()],
        # Stylesheets name Mac fonts first; record which fallbacks exist here.
        "fonts": {f: f in families for f in ("Menlo", "Consolas", "Segoe UI", "SF Pro Text",
                                             "Helvetica Neue", "Malgun Gothic", "Arial")},
    }


@step("icons")
def _icons():
    return runpy.run_path(str(ROOT / "scripts" / "diagnose_toolbar_icons.py"))["collect"](session)


@step("toolbar")
def _toolbar():
    from chimerax.toolbar.tool import get_toolbar_singleton
    main = session.ui.main_window
    main.showMaximized()
    settle(1.0)
    grab(main, "main-window")
    ttb = get_toolbar_singleton(session, create=True).ttb
    tabs = []
    if hasattr(ttb, "count") and hasattr(ttb, "setCurrentIndex"):
        for index in range(ttb.count()):
            title = ttb.tabText(index)
            if title not in ("AI", "Molecule Display", "Nucleotides", "Home"):
                continue
            ttb.setCurrentIndex(index)
            tabs.append(grab(ttb, "toolbar-" + title.replace(" ", "_")))
    return {"tabs": tabs}


@step("structure")
def _structure():
    if "--no-fetch" in ARGS:
        return "skipped"
    run("open 3fx2")
    run("wait 10")
    settle(1.0)
    return [m.id_string for m in session.models.list()]


@step("panels")
def _panels():
    results = {}
    for name in PANELS:
        try:
            run(f'ui tool show "{name}"')
            settle(1.5)
            tools = [t for t in session.tools.list() if getattr(t, "tool_name", "") == name]
            window = getattr(tools[0], "tool_window", None) if tools else None
            area = getattr(window, "ui_area", None)
            results[name] = {
                "found": bool(tools),
                "size": None if area is None else [area.width(), area.height()],
                "screenshot": grab(area, "panel-" + name.replace(" ", "_")),
            }
        except Exception:
            results[name] = {"error": traceback.format_exc()}
    grab(session.ui.main_window, "main-window-panels")
    return results


@step("log")
def _log():
    path = OUT / "chimerax-log.html"
    run(f'log save "{path.as_posix()}"')
    text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    return {"file": path.name, "traceback_count": text.count("Traceback"),
            "error_count": text.lower().count("error")}


def _chimerax_binaries():
    bin_dir = Path(sys.executable).resolve().parent
    python = next((p for p in (bin_dir / "python.exe", bin_dir / "python3.11", bin_dir / "python3")
                   if p.exists()), Path(sys.executable))
    chimerax = next((p for p in (bin_dir / "ChimeraX-console.exe", bin_dir / "ChimeraX.exe",
                                 bin_dir / "ChimeraX") if p.exists()), Path(sys.executable))
    return python, chimerax


def _run_suites():
    runner = runpy.run_path(str(ROOT / "scripts" / "run_quality_check.py"), run_name="suites")
    python, chimerax = _chimerax_binaries()
    temp = OUT / "suite-tmp"
    temp.mkdir(exist_ok=True)
    # Checks write fixtures to "/tmp/..."; on Windows that is <drive>:\tmp.
    Path(os.path.abspath("/tmp")).mkdir(exist_ok=True)
    env = dict(os.environ, CHIMERAX_BIN=str(chimerax), TEMP=str(temp), TMP=str(temp), TMPDIR=str(temp),
               PYTHONIOENCODING="utf-8")
    results = {"python": str(python), "chimerax": str(chimerax), "suites": {}}
    chosen = next((a.split("=", 1)[1].split(",") for a in ARGS if a.startswith("--suites=")), None)
    for suite in chosen or runner["SUITES"]:
        started = time.monotonic()
        try:
            process = subprocess.run([str(python), str(ROOT / "scripts" / "run_quality_check.py"), suite],
                                     cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, encoding="utf-8", errors="replace", timeout=1800)
            ok = process.returncode == 0
            output = process.stdout
        except Exception:
            ok, output = False, traceback.format_exc()
        (OUT / f"suite-{suite}.log").write_text(output, encoding="utf-8")
        results["suites"][suite] = {"ok": ok, "seconds": round(time.monotonic() - started, 1)}
        (OUT / "suites.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    passed = sum(r["ok"] for r in results["suites"].values())
    done = session.ui.thread_safe if session.ui.is_gui else (lambda f, *a: f(*a))
    done(session.logger.info,
        f"WINDOWS_SELFCHECK_SUITES_DONE {passed}/{len(results['suites'])} passed -> {OUT}")


save_report()
if "--no-suites" in ARGS:
    pass
elif not session.ui.is_gui:  # --nogui dry run: ChimeraX exits when the script ends
    _run_suites()
else:
    threading.Thread(target=_run_suites, name="codex-windows-suites", daemon=True).start()
    report["suites"] = "running in background -> suites.json"
    save_report()
session.logger.info(f"WINDOWS_SELFCHECK_DONE errors={len(report['errors'])} -> {OUT}")
