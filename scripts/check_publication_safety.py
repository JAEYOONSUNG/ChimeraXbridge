#!/usr/bin/env python3
"""Audit public candidates without displaying credential values or file contents.

Uses Git's tracked + nonignored file set, so local ignored development scripts
are never read or accidentally promoted into publication candidates. This is a
deterministic guard for known credentials/configuration artifacts, not a claim
that arbitrary image pixels or every possible secret format can be recognized.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re
import subprocess
import zipfile


ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {".gitignore", "README.md", "INSTALL_REPRODUCIBLE.md", "RAPIDOCK_SETUP.md",
              "bundle_info.xml", "license.txt", "package.json", "package-lock.json"}
SOURCE_SUFFIXES = {".py", ".mjs", ".html", ".svg", ".png", ".json"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico"}
FORBIDDEN_PARTS = {".git", ".unlazy", ".codex", ".claude", ".gemini", ".ssh", ".aws",
                   ".config", "node_modules", "build", "__pycache__", ".venv", "venv"}
FORBIDDEN_NAMES = {"auth.json", "credentials.json", "credentials", "secrets.json", "secrets.yml",
                   "secrets.yaml", "id_rsa", "id_ed25519", ".codex_runtime_verify.json"}
RUNTIME_SUFFIXES = {".cxs", ".pdb", ".cif", ".mmcif", ".csv", ".tsv", ".log", ".tmp",
                    ".bak", ".pyc", ".pyo", ".sqlite", ".db", ".cxc", ".pem", ".key"}
PATTERNS = {
    "private-key": re.compile(r"-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----"),
    "credential-token": re.compile(
        r"\b(?:sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}"
        r"|github_pat_[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16}|AIza[A-Za-z0-9_-]{35}"
        r"|xox[baprs]-[A-Za-z0-9-]{20,})\b"),
    "personal-path": re.compile(r"/(?:Users|home)/[^/\s'\"<>]+|/var/" r"folders/[^\s'\"<>]+|[A-Z]:\\Users\\"),
    "credential-url": re.compile(r"https?://[^\s/@:]+:[^\s/@]+@"),
}
SECRET_ASSIGNMENT = re.compile(
    r'''(?i)\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password|authorization)\b["']?\s*[:=]\s*(["'])([^"'\n]{8,})\1''')
PLACEHOLDER = re.compile(
    r"(?ix)^(?:your[_ -].*|example.*|placeholder.*|dummy.*|fake.*|test[_ -].*|"
    r"replace[_ -].*|change[_ -]?me.*|<[^>]+>|\$\{[^}]+\}|\{[^}]+\}|\.\.\.)$")


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    reason: str


def git_paths(root=ROOT, *, tracked_only=False):
    args = ["git", "ls-files", "--cached"]
    if not tracked_only:
        args += ["--others", "--exclude-standard"]
    result = subprocess.run([*args, "-z"], cwd=root, capture_output=True, check=True)
    return sorted(set(result.stdout.decode("utf-8", errors="surrogateescape").split("\0")) - {""})


def forbidden_path(name):
    path = PurePosixPath(name)
    parts = {part.casefold() for part in path.parts}
    base = path.name.casefold()
    return (path.is_absolute() or ".." in path.parts or "\\" in name
            or bool(parts & FORBIDDEN_PARTS) or base in FORBIDDEN_NAMES
            or base == ".env" or base.startswith(".env.")
            or path.suffix.casefold() in RUNTIME_SUFFIXES)


def allowed_candidate(name):
    if forbidden_path(name):
        return False
    if name in ROOT_FILES:
        return True
    path = PurePosixPath(name)
    if len(path.parts) < 2:
        return False
    first, suffix = path.parts[0], path.suffix.casefold()
    if first == "src":
        return suffix in SOURCE_SUFFIXES
    if first == "icons":
        return suffix in {".svg", ".png", ".json"}
    if first == "assets":
        return suffix in IMAGE_SUFFIXES | {".svg"}
    if first == "docs":
        return suffix in IMAGE_SUFFIXES | {".svg", ".md", ".html"}
    if first == "dist":
        return len(path.parts) == 2 and suffix == ".whl"
    if first == "scripts":
        return len(path.parts) == 2 and suffix == ".py" and (
            path.name.startswith(("check_", "reload_", "run_"))
            or path.name in {"headless_ui_fixture.py", "nl_eval.py"})
    return (path.parts[:2] == (".github", "workflows") and len(path.parts) == 3
            and suffix in {".yml", ".yaml"})


def text_findings(name, data):
    findings = []
    # UTF-8 source/doc files and readable image metadata are both inspected;
    # invalid binary sequences cannot form these ASCII credential signatures.
    for line_number, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
        for reason, pattern in PATTERNS.items():
            if pattern.search(line):
                findings.append(Finding(name, line_number, reason))
        for match in SECRET_ASSIGNMENT.finditer(line):
            if not PLACEHOLDER.fullmatch(match.group(2).strip()):
                findings.append(Finding(name, line_number, "credential-literal"))
    return findings


def audit(root=ROOT):
    findings, checked = [], 0
    for name in git_paths(root):
        path = root / name
        if path.is_symlink():
            findings.append(Finding(name, 0, "nonregular-public-file"))
            continue
        if not path.exists():  # A staged/working-tree deletion publishes no bytes.
            continue
        if not allowed_candidate(name):
            findings.append(Finding(name, 0, "outside-public-allowlist"))
            continue
        if path.is_symlink() or not path.is_file():
            findings.append(Finding(name, 0, "nonregular-public-file"))
            continue
        checked += 1
        if path.suffix.casefold() == ".whl":
            try:
                with zipfile.ZipFile(path) as archive:
                    for info in archive.infolist():
                        member = info.filename
                        if info.is_dir():
                            continue
                        if forbidden_path(member) or (info.external_attr >> 16) & 0o170000 == 0o120000:
                            findings.append(Finding(name + "!" + member, 0, "forbidden-wheel-member"))
                        else:
                            findings.extend(text_findings(name + "!" + member, archive.read(info)))
            except (OSError, zipfile.BadZipFile):
                findings.append(Finding(name, 0, "unreadable-wheel"))
        else:
            findings.extend(text_findings(name, path.read_bytes()))
    return checked, findings


def self_test():
    # Synthetic signatures are assembled so the scanner does not flag its own
    # test fixtures, and no authentic credential ever appears in the output.
    samples = {
        "credential-token": "gh" + "p_" + "A" * 30,
        "private-key": "-----BEGIN " + "PRIVATE KEY-----",
        "personal-path": "/" + "Users/fixture/private.txt",
        "credential-url": "https://" + "person:password@example.invalid",
        "credential-literal": 'api_' + 'key = "' + "unmistakably-private-value" + '"',
    }
    for expected, value in samples.items():
        assert expected in {item.reason for item in text_findings("fixture.txt", value.encode())}
    assert not text_findings("fixture.py", b'key = os.environ.get("OPENAI_API_KEY")')
    assert not text_findings("fixture.json", b'{"api_key": "YOUR_API_KEY"}')
    for name in (".env", "src/auth.json", ".unlazy/report.json", "src/session.cxs",
                 "scripts/test_private.py", "src/../private.py"):
        assert not allowed_candidate(name)
    for name in ("src/shared_profile.py", "src/profiles/jaeyoon.json",
                 "scripts/check_sequence_colors.py", ".github/workflows/ci.yml"):
        assert allowed_candidate(name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="also exercise the scanner's detection rules")
    args = parser.parse_args()
    if args.self_test:
        self_test()
    checked, findings = audit()
    for finding in sorted(set(findings), key=lambda item: (item.path, item.line, item.reason)):
        # Intentionally no matched text, values, source excerpts or exception bodies.
        print(f"{finding.path}:{finding.line}: {finding.reason}")
    if findings:
        print(f"PUBLICATION_SAFETY_FAIL files={checked} findings={len(findings)}")
        return 1
    print(f"PUBLICATION_SAFETY_OK files={checked} findings=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
