"""Completion checks that never create a visible window or activate an app.

Only explicit headless checks are allowlisted. Real GUI/OpenGL checks belong
in the opt-in runners and are deliberately absent here.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
CHECK_TIMEOUT = float(os.environ.get("CODEX_BRIDGE_CHECK_TIMEOUT", "180"))


def _write_text(path, text):
    try:
        path.write_text(text, encoding="utf-8")
    except OSError as error:  # a full disk must not hide the real result
        print(f"warning: could not write {path}: {error}", flush=True)

# (script basename, required success marker, plain Python instead of ChimeraX)
SUITES = {
    "user_workflows": [("check_user_workflows.py", "USER_WORKFLOWS_OK", False)],
    "model_chains": [("check_model_chain_controls.py", "MODEL_CHAIN_CONTROLS_OK", False)],
    "compound_selection": [("check_compound_selection.py", "COMPOUND_SELECTION_OK", False)],
    "ai_request_lifecycle": [("check_ai_request_lifecycle.py", "AI_REQUEST_LIFECYCLE_OK", False),
                             ("check_command_safety.py", "COMMAND_SAFETY_OK", True)],
    "ui_zoom": [("check_ui_zoom.py", "UI_ZOOM_OK", False)],
    "color_palette": [("check_color_palette.py", "COLOR_PALETTE_OK", False)],
    "display_palette": [("check_display_palette.py", "DISPLAY_PALETTE_OK", False)],
    "install_defaults": [("check_install_defaults.py", "INSTALL_DEFAULTS_OK", False)],
    "ai_connection": [("check_ai_connection.py", "AI_CONNECTION_OK", False)],
    "shared_profile": [("check_shared_profile.py", "SHARED_PROFILE_OK", False)],
    "usability_export": [("check_usability_export.py", "USABILITY_EXPORT_OK", False)],
    "usability_sequence": [("check_usability_sequence.py", "USABILITY_SEQUENCE_OK", False)],
    "usability_results": [("check_usability_results.py", "USABILITY_RESULTS_OK", False)],
    "usability_controls": [("check_usability_controls.py", "USABILITY_CONTROLS_OK", False)],
    "populated_panels": [("check_panel_populated.py", "PANEL_POPULATED_OK", False)],
    "populated_sequence": [("check_sequence_populated.py", "SEQUENCE_POPULATED_OK", False)],
    "lifecycle": [("check_quick_lifecycle.py", "QUICK_LIFECYCLE_OK", False)],
    "scroll": [("check_panel_scroll.py", "PANEL_SCROLL_OK", False)],
    "panels": [("check_display_controls.py", "PASS: selection identity", False),
               ("check_models_caver_scroll.py", "MODELS_CAVER_SCROLL_OK", False),
               ("check_panel_quality.py", "PANEL_QUALITY_OK", False)],
    "export": [("check_export_quality.py", "EXPORT_QUALITY_OK", False),
               ("check_camera_bookmark_ui.py", "CAMERA_BOOKMARK_UI_OK", False),
               ("check_camera_bookmark_state.py", "CAMERA_BOOKMARK_STATE_OK", False)],
    "sequence": [("check_sequence_startup.py", "OK", True),
                 ("check_sequence_colors.py", "OK", True),
                 ("check_sequence_guides.py", "SEQUENCE_GUIDES_OK", False),
                 ("check_sequence_coloring.py", "SEQUENCE_COLORING_OK", False),
                 ("check_sequence_quality.py", "SEQUENCE_QUALITY_OK", False)],
    "quick": [("check_quick_analyze.py", "QUICK_ANALYZE_OK", False),
              ("check_quick_pockets.py", "QUICK_POCKETS_OK", False),
              ("check_quick_views.py", "QUICK_VIEWS_OK", False),
              ("check_quick_reports.py", "QUICK_REPORTS_OK", False),
              ("check_quick_headless_ui.py", "QUICK_HEADLESS_UI_OK", False)],
}


def run_check(filename, marker, plain):
    script = ROOT / "scripts" / filename
    if not script.is_file():
        raise RuntimeError(f"Missing check: {filename}")
    environment = dict(os.environ, QT_QPA_PLATFORM="offscreen", CODEX_BRIDGE_AUTORELOAD="0")
    environment.pop("CODEX_ALLOW_VISIBLE_GUI_TESTS", None)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="quality-headless-") as folder:
        if plain:
            command = [sys.executable, str(script)]
        else:
            driver = Path(folder) / "check.py"
            config_guard = ("" if filename in ("check_sequence_coloring.py", "check_shared_profile.py", "check_install_defaults.py") else
                "from chimerax.core.configfile import ConfigFile\n"
                "ConfigFile.save = lambda *args, **kwargs: None\n")
            source_isolation = (
                "import sys, chimerax\n"
                "for name in list(sys.modules):\n"
                "    if name == 'chimerax.codex_bridge' or name.startswith('chimerax.codex_bridge.'):\n"
                "        del sys.modules[name]\n"
                "if hasattr(chimerax, 'codex_bridge'):\n"
                "    del chimerax.codex_bridge\n")
            driver.write_text("import runpy\nassert not session.ui.is_gui\n" + config_guard + source_isolation +
                f"runpy.run_path({str(script)!r}, init_globals={{'session': session}})\n",
                encoding="utf-8")
            binary = environment.get("CHIMERAX_BIN", "/Applications/ChimeraX-1.10.1.app/Contents/bin/ChimeraX")
            command = [binary, "--nogui", "--notools", "--exit", "--script", str(driver)]
        try:
            process = subprocess.run(command, cwd=ROOT, env=environment,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                encoding="utf-8", errors="replace", timeout=CHECK_TIMEOUT)
        except subprocess.TimeoutExpired as error:
            output = error.stdout or b""
            if isinstance(output, bytes):
                output = output.decode("utf-8", errors="replace")
            _write_text(Path(tempfile.gettempdir()) / f"quality-{script.stem}.log", output)
            raise RuntimeError(f"{filename}: exceeded {CHECK_TIMEOUT} seconds") from error
    log = Path(tempfile.gettempdir()) / f"quality-{script.stem}.log"
    _write_text(log, process.stdout)
    if process.returncode or marker not in process.stdout:
        raise RuntimeError(f"{filename}: exit={process.returncode}, marker={marker!r}\n"
                           + process.stdout[-12000:])
    return {"script": filename, "seconds": round(time.monotonic() - started, 2),
            "log": str(log), "mode": "plain" if plain else "headless/offscreen"}


def _suite_labels(argument):
    if argument == "all":
        return list(SUITES)
    if argument in SUITES:
        return [argument]
    matches = [label for label in SUITES if argument and argument in label]
    if not matches:
        raise SystemExit("Choose all, --list, or one of: " + ", ".join(SUITES))
    return matches


def main():
    arguments = sys.argv[1:]
    if arguments in ([], ["-h"], ["--help"]):
        raise SystemExit(
            "usage: run_quality_check.py <suite>|all|--list\n"
            "Suites: " + ", ".join(SUITES))
    if arguments == ["--list"]:
        for label, checks in SUITES.items():
            print(f"{label}: " + ", ".join(check[0] for check in checks))
        return
    if len(arguments) != 1:
        raise SystemExit("Pass exactly one suite label, 'all', or --list.")
    label = arguments[0]
    labels = _suite_labels(label)
    started = time.monotonic()
    suite_reports = []
    failures = []
    for suite in labels:
        results = []
        for check in SUITES[suite]:
            try:
                result = run_check(*check)
            except Exception as error:
                failures.append((suite, check[0], str(error)))
                print(f"FAIL {check[0]} ({suite})", flush=True)
                print(str(error)[-4000:], flush=True)
                continue
            results.append(result)
            print(f"PASS {result['script']} ({result['seconds']} s)", flush=True)
        ok = not any(suite == failed for failed, _, _ in failures)
        report = {"ok": ok, "suite": suite, "checks": results, "visible_windows": False,
                  "failures": [name for failed, name, _ in failures if failed == suite]}
        path = Path(tempfile.gettempdir()) / f"quality-{suite}-report.json"
        _write_json(path, report)
        suite_reports.append(report)
        if ok:
            print(f"QUALITY_{suite.upper()}_OK {len(results)} checks; report={path}", flush=True)
        else:
            print(f"QUALITY_{suite.upper()}_FAILED {len(report['failures'])} of "
                  f"{len(SUITES[suite])} checks; report={path}", flush=True)

    if len(labels) > 1:
        summary = {
            "ok": not failures,
            "git": _git_head(),
            "seconds": round(time.monotonic() - started, 2),
            "suites": suite_reports,
        }
        path = Path(tempfile.gettempdir()) / "quality-all-report.json"
        _write_json(path, summary)
        passed = sum(len(report["checks"]) for report in suite_reports)
        print(f"QUALITY_SUMMARY passed={passed} failed={len(failures)} "
              f"suites={len(labels)} seconds={summary['seconds']} report={path}")
        for suite, name, _ in failures:
            print(f"  failed: {suite}/{name}")
        if not failures:
            print("QUALITY_ALL_OK")
    if failures:
        raise SystemExit(1)


def _write_json(path, payload):
    try:
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError as error:
        print(f"warning: could not write {path}: {error}", flush=True)


def _git_head():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                              text=True, timeout=10).stdout.strip()
    except Exception:
        return ""


if __name__ == "__main__":
    main()
