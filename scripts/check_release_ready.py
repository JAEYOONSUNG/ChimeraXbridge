#!/usr/bin/env python3
"""Check whether the repository can reproduce the local ChimeraXbridge build."""

from __future__ import annotations

import py_compile
import re
import subprocess
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_PREFIX = "chimerax/codex_bridge/"
IGNORED_UNTRACKED = {
    ".codex_runtime_verify.json",
    "whole_clustering.csv",
}
RELEASE_ROOT_FILES = {
    ".gitignore",
    "README.md",
    "INSTALL_REPRODUCIBLE.md",
    "RAPIDOCK_SETUP.md",
    "bundle_info.xml",
    "license.txt",
    "package.json",
    "package-lock.json",
}
RELEASE_DIRS = {"src", "icons", "docs", "dist"}
RELEASE_SCRIPT_FILES = {
    "scripts/check_release_ready.py",
    "scripts/reload_codex_ui.py",
}
SOURCE_SUFFIXES = {".py", ".mjs", ".html", ".svg", ".png"}


def _run_git(args: list[str]) -> list[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.splitlines()


def _status_entries() -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for line in _run_git(["status", "--porcelain"]):
        if not line:
            continue
        status = line[:2]
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        entries.append((status, path))
    return entries


def _is_release_path(path_text: str) -> bool:
    path = Path(path_text)
    if path_text in IGNORED_UNTRACKED:
        return False
    if path.match("scripts/*.cxc"):
        return False
    if path.parts and path.parts[0] == "scripts":
        return path_text in RELEASE_SCRIPT_FILES
    if len(path.parts) == 1:
        return path_text in RELEASE_ROOT_FILES
    return path.parts[0] in RELEASE_DIRS


def _check_git_state(errors: list[str], warnings: list[str]) -> None:
    entries = _status_entries()
    release_dirty = [(status, path) for status, path in entries if _is_release_path(path)]
    if release_dirty:
        errors.append("release files are not committed")
        for status, path in release_dirty[:40]:
            warnings.append(f"  {status} {path}")
        if len(release_dirty) > 40:
            warnings.append(f"  ... {len(release_dirty) - 40} more")

    try:
        remote = _run_git(["remote", "get-url", "origin"])
    except RuntimeError as err:
        errors.append(f"origin remote is not configured: {err}")
    else:
        if not remote:
            errors.append("origin remote is not configured")
        else:
            warnings.append(f"origin: {remote[0]}")


def _check_bundle_xml(errors: list[str], warnings: list[str]) -> None:
    bundle_path = ROOT / "bundle_info.xml"
    try:
        text = bundle_path.read_text(encoding="utf-8")
        ElementTree.fromstring(text)
    except Exception as err:
        errors.append(f"bundle_info.xml is not valid XML: {err}")
        return

    icon_names = sorted(set(re.findall(r":: icon:([^\s:]+)", text)))
    missing = [name for name in icon_names if not (ROOT / "src" / "icons" / name).exists()]
    if missing:
        errors.append("toolbar icons referenced by bundle_info.xml are missing under src/icons")
        for name in missing:
            warnings.append(f"  missing icon: src/icons/{name}")
    else:
        warnings.append(f"toolbar icons present: {len(icon_names)}")


def _source_payload_files() -> list[Path]:
    files: list[Path] = []
    for path in (ROOT / "src").rglob("*"):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts:
            continue
        if path.suffix.lower() in SOURCE_SUFFIXES:
            files.append(path)
    return sorted(files)


def _check_python_syntax(errors: list[str], warnings: list[str]) -> None:
    checked = 0
    for base in (ROOT / "src", ROOT / "scripts"):
        for path in base.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            checked += 1
            try:
                py_compile.compile(str(path), doraise=True)
            except py_compile.PyCompileError as err:
                rel = path.relative_to(ROOT)
                errors.append(f"Python syntax error in {rel}: {err.msg}")
    warnings.append(f"Python syntax checked: {checked} files")


def _check_wheel(errors: list[str], warnings: list[str]) -> None:
    wheels = sorted((ROOT / "dist").glob("chimerax_codexbridge-*.whl"))
    if not wheels:
        warnings.append("no wheel under dist; devel install is required")
        return

    wheel = wheels[-1]
    try:
        with zipfile.ZipFile(wheel) as zf:
            names = set(zf.namelist())
    except Exception as err:
        errors.append(f"cannot inspect wheel {wheel.relative_to(ROOT)}: {err}")
        return

    missing: list[str] = []
    for path in _source_payload_files():
        rel = path.relative_to(ROOT / "src").as_posix()
        expected = PACKAGE_PREFIX + rel
        if expected not in names:
            missing.append(expected)

    if missing:
        errors.append(f"wheel is missing {len(missing)} current src payload file(s)")
        for name in missing[:40]:
            warnings.append(f"  wheel missing: {name}")
        if len(missing) > 40:
            warnings.append(f"  ... {len(missing) - 40} more")
    else:
        warnings.append(f"wheel payload matches current src files: {wheel.relative_to(ROOT)}")


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []

    for check in (_check_git_state, _check_bundle_xml, _check_python_syntax, _check_wheel):
        check(errors, warnings)

    for line in warnings:
        print(line)

    if errors:
        print("\nRelease readiness: FAIL")
        for err in errors:
            print(f"- {err}")
        return 1

    print("\nRelease readiness: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
