#!/usr/bin/env python3
"""Validate a portable public bundle with plain Python and no ChimeraX runtime.

Default mode requires all public files to be committed. --allow-dirty only
relaxes that preflight condition, never payload, metadata or safety checks.
"""
from __future__ import annotations

import argparse
import ast
import base64
import csv
from email.parser import Parser
import hashlib
import io
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
from xml.etree import ElementTree
import zipfile

from check_publication_safety import audit, git_paths, SOURCE_SUFFIXES


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_PREFIX = "chimerax/codex_bridge/"


def normalized_name(value):
    return re.sub(r"[-_.]+", "-", value).casefold()


def discover_wheel(root, distribution, version):
    """Use normalized filenames rather than a case-sensitive glob."""
    matches = []
    directory = root / "dist"
    if directory.is_dir():
        for path in directory.iterdir():
            parts = path.name[:-4].split("-")
            if (path.is_file() and path.suffix.casefold() == ".whl" and len(parts) >= 5
                    and normalized_name(parts[0]) == normalized_name(distribution)
                    and parts[1] == version):
                matches.append(path)
    if len(matches) != 1:
        raise ValueError(f"expected one current wheel, found {len(matches)}")
    return matches[0]


def check_git_state(errors, notes, *, allow_dirty):
    result = subprocess.run(["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=ROOT, capture_output=True, check=True)
    records = result.stdout.decode("utf-8", errors="surrogateescape").split("\0")
    entries, index = [], 0
    while index < len(records):
        entry = records[index]
        index += 1
        if not entry:
            continue
        status, name = entry[:2], entry[3:]
        entries.append((status, name))
        if "R" in status or "C" in status:
            index += 1
    if entries:
        notes.append(f"Uncommitted public candidates: {len(entries)}")
        if not allow_dirty:
            errors.append("public files are not committed (use --allow-dirty only for preflight)")
            notes.extend(f"  {status} {name}" for status, name in entries[:30])
    else:
        notes.append("Git worktree: clean")
    candidates = {name for name in git_paths(ROOT) if (ROOT / name).is_file()}
    untracked = candidates - set(git_paths(ROOT, tracked_only=True))
    if untracked and not allow_dirty:
        errors.append(f"{len(untracked)} public candidate files are untracked")
    return candidates


def check_sources(candidates, errors, notes):
    payload, folded, checked = {}, {}, 0
    for name in sorted(candidates):
        folded_name = name.casefold()
        if folded_name in folded:
            errors.append(f"case-insensitive filename collision: {folded[folded_name]}, {name}")
        folded[folded_name] = name
        path = ROOT / name
        if path.suffix == ".py":
            checked += 1
            try:
                ast.parse(path.read_text(encoding="utf-8"), filename=name)
            except SyntaxError as error:
                errors.append(f"invalid Python syntax: {name}:{error.lineno}")
            except UnicodeError:
                errors.append(f"Python source is not UTF-8: {name}")
        if name.startswith("src/") and path.suffix.casefold() in SOURCE_SUFFIXES:
            payload[name[4:]] = path.read_bytes()
    if "__init__.py" not in payload:
        errors.append("source package initializer is missing from public candidates")
    notes.append(f"Python syntax: {checked} public files; source payload: {len(payload)} files")
    return payload


def check_xml(payload, errors, notes):
    try:
        tree = ElementTree.parse(ROOT / "bundle_info.xml")
    except (OSError, ElementTree.ParseError):
        errors.append("bundle_info.xml is missing or invalid XML")
        return None
    bundle = tree.getroot()
    name, version = bundle.get("name", ""), bundle.get("version", "")
    if not name.startswith("ChimeraX-") or not re.fullmatch(r"\d+\.\d+\.\d+(?:[a-zA-Z0-9.+-]*)?", version):
        errors.append("bundle name or version is invalid")
    if bundle.get("package") != "chimerax.codex_bridge":
        errors.append("bundle package does not match the public Python package")
    files = {node.text.strip() for node in bundle.findall("./DataFiles/DataFile") if node.text}
    directories = {node.text.strip().rstrip("/") for node in bundle.findall("./DataFiles/DataDir") if node.text}
    for filename in sorted(files):
        if filename not in payload:
            errors.append(f"declared DataFile is missing (exact case required): src/{filename}")
    for directory in sorted(directories):
        if not any(filename.startswith(directory + "/") for filename in payload):
            errors.append(f"declared DataDir is empty/missing: src/{directory}")
    for filename in payload:
        if (not filename.endswith(".py") and filename not in files
                and not any(filename.startswith(directory + "/") for directory in directories)):
            errors.append(f"resource is not declared in DataFiles: src/{filename}")
    text = ElementTree.tostring(bundle, encoding="unicode")
    icons = set(re.findall(r":: icon:([^\s:]+)", text))
    icons.update(node.get("icon") for node in bundle.iter() if node.get("icon"))
    for icon in sorted(icons):
        if "icons/" + icon not in payload:
            errors.append(f"toolbar icon is missing (exact case required): src/icons/{icon}")
    notes.append(f"Bundle: {name} {version}; toolbar icons: {len(icons)}")
    return name, version


def check_record(archive, names, record_name, errors):
    try:
        rows = list(csv.reader(io.StringIO(archive.read(record_name).decode("utf-8"))))
    except (KeyError, UnicodeError, csv.Error):
        errors.append("wheel RECORD is missing or invalid")
        return
    seen = set()
    for row in rows:
        if len(row) != 3 or row[0] in seen:
            errors.append("wheel RECORD has malformed or duplicate entries")
            continue
        name, digest, size = row
        seen.add(name)
        if name not in names:
            errors.append(f"wheel RECORD references missing member: {name}")
            continue
        if name == record_name:
            if digest or size:
                errors.append("wheel RECORD must leave its own hash and size empty")
            continue
        data = archive.read(name)
        expected = "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("=")
        if digest != expected or size != str(len(data)):
            errors.append(f"wheel RECORD hash/size differs: {name}")
    for name in sorted(names - seen):
        errors.append(f"wheel member is absent from RECORD: {name}")


def check_wheel(bundle, payload, errors, notes):
    if bundle is None:
        return
    distribution, version = bundle
    try:
        wheel = discover_wheel(ROOT, distribution, version)
        with zipfile.ZipFile(wheel) as archive:
            members = [info.filename for info in archive.infolist() if not info.is_dir()]
            names = set(members)
            if len(members) != len(names) or len({name.casefold() for name in members}) != len(members):
                errors.append("wheel contains duplicate or case-colliding members")
            if any(PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts or "\\" in name
                   for name in members):
                errors.append("wheel contains unsafe member paths")
            metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
            if len(metadata_names) != 1:
                errors.append("wheel must contain exactly one package METADATA")
            else:
                metadata_name = metadata_names[0]
                metadata = Parser().parsestr(archive.read(metadata_name).decode("utf-8"))
                if (normalized_name(metadata.get("Name", "")) != normalized_name(distribution)
                        or metadata.get("Version") != version):
                    errors.append("wheel METADATA name/version differs from bundle_info.xml")
                metadata_prefix = metadata_name.rsplit("/", 1)[0] + "/"
                expected_prefix = distribution.replace("-", "_").casefold() + "-" + version + ".dist-info/"
                if metadata_prefix.casefold() != expected_prefix:
                    errors.append("wheel dist-info directory name/version differs from bundle_info.xml")
                if any(not name.startswith((PACKAGE_PREFIX, metadata_prefix)) for name in names):
                    errors.append("wheel includes files outside its package and dist-info directories")
                check_record(archive, names, metadata_prefix + "RECORD", errors)
            expected_members = {PACKAGE_PREFIX + name for name in payload}
            for member in sorted(expected_members - names):
                errors.append(f"wheel is missing current source: {member}")
            for member in sorted(name for name in names if name.startswith(PACKAGE_PREFIX) and name not in expected_members):
                errors.append(f"wheel contains obsolete/unpublished source: {member}")
            for name, data in payload.items():
                member = PACKAGE_PREFIX + name
                if member in names and archive.read(member) != data:
                    errors.append(f"wheel bytes differ from current source: src/{name}")
        notes.append(f"Wheel: {wheel.relative_to(ROOT).as_posix()}")
        notes.append("Wheel SHA-256: " + hashlib.sha256(wheel.read_bytes()).hexdigest())
    except (OSError, ValueError, KeyError, UnicodeError, zipfile.BadZipFile):
        errors.append("current wheel is missing, ambiguous, unreadable or has invalid metadata")


def self_test():
    """Reject stale payloads, metadata/RECORD corruption and missing wheels."""
    global ROOT
    original_root = ROOT
    payload = {"__init__.py": b"value = 1\n", "profiles/fixture.json": b'{"version":1}\n'}
    prefix = "chimerax_codexbridge-1.2.3.dist-info/"
    try:
        with tempfile.TemporaryDirectory(prefix="release-check-") as folder:
            ROOT = Path(folder)
            (ROOT / "dist").mkdir()
            wheel = ROOT / "dist/ChimeraX_CodexBridge-1.2.3-py3-none-any.whl"

            def write_fixture(*, version="1.2.3", omit=None, bad_record=False):
                contents = {PACKAGE_PREFIX + name: data for name, data in payload.items() if name != omit}
                contents[prefix + "METADATA"] = f"Name: ChimeraX-CodexBridge\nVersion: {version}\n".encode()
                contents[prefix + "WHEEL"] = b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
                rows = []
                for name, data in contents.items():
                    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("=")
                    rows.append((name, "sha256=" + ("wrong" if bad_record else digest), str(len(data))))
                rows.append((prefix + "RECORD", "", ""))
                record = io.StringIO()
                csv.writer(record).writerows(rows)
                contents[prefix + "RECORD"] = record.getvalue().encode()
                with zipfile.ZipFile(wheel, "w") as archive:
                    for name, data in contents.items():
                        archive.writestr(name, data)

            def verify(expected_payload=payload):
                failures = []
                check_wheel(("ChimeraX-CodexBridge", "1.2.3"), expected_payload, failures, [])
                return failures

            write_fixture()
            assert discover_wheel(ROOT, "chimerax-codexbridge", "1.2.3") == wheel
            assert not verify(), "Valid mixed-case wheel was rejected"
            assert any("bytes differ" in item for item in verify({**payload, "__init__.py": b"changed"}))
            write_fixture(omit="profiles/fixture.json")
            assert any("missing current source" in item for item in verify())
            write_fixture(version="9.9.9")
            assert any("METADATA name/version" in item for item in verify())
            write_fixture(bad_record=True)
            assert any("RECORD hash/size" in item for item in verify())
            wheel.unlink()
            assert verify(), "Missing current wheel was accepted"
    finally:
        ROOT = original_root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-dirty", action="store_true", help="preflight uncommitted public candidates")
    parser.add_argument("--self-test", action="store_true", help="also reject synthetic broken release fixtures")
    args = parser.parse_args()
    if args.self_test:
        self_test()
    errors, notes = [], []
    candidates = check_git_state(errors, notes, allow_dirty=args.allow_dirty)
    checked, findings = audit(ROOT)
    if findings:
        errors.extend(f"publication safety: {item.path}:{item.line}: {item.reason}" for item in findings)
    notes.append(f"Publication scan: {checked} candidates; {len(findings)} findings")
    payload = check_sources(candidates, errors, notes)
    bundle = check_xml(payload, errors, notes)
    check_wheel(bundle, payload, errors, notes)
    for note in notes:
        print(note)
    if errors:
        for error in errors:
            print("FAIL: " + error)
        print(f"RELEASE_READY_FAIL errors={len(errors)}")
        return 1
    print("RELEASE_READY_OK" + (" preflight (--allow-dirty)" if args.allow_dirty else " committed checkout"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
