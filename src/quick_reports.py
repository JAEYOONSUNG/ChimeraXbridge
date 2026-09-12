"""Portable scientific reports for quick actions; no ChimeraX or Qt dependency.

Candidate values retain computation precision. Display summaries are preserved
as supplied, and are never parsed back into measurements. CSV has one row per
candidate (one metadata row for candidate-free actions); nested values are JSON
cells so residue-level evidence and future result fields remain recoverable.
"""
from collections.abc import Mapping
from datetime import datetime, timezone
import csv
import io
import json
import math
from numbers import Integral, Real
import os
from pathlib import Path
import tempfile


_ACTIONS = frozenset(("analyze", "pocket", "cavity", "zoom", "view", "figure"))
_GEOMETRY_KEYS = frozenset(("mesh", "vertices", "triangles", "normals", "voxel_grid", "grid_matrix"))
_ARRAY_LIMIT = 10000
_UNITS = {
    "volume": "Å³", "area": "Å²", "max_depth": "Å", "avg_depth": "Å",
    "grid_step": "Å", "nearest_distance": "Å", "distance": "Å", "cutoff": "Å",
    "center": "Å (scene coordinates)", "bounds": "Å (scene coordinates)",
    "contact_count": "residues", "contact_residues": "residues",
    "residue_count": "residues", "atom_count": "atoms", "heavy_atoms": "atoms",
    "ligand_heavy_atoms": "atoms", "ligand_atom_count": "atoms", "grid_cells": "cells",
    "rank_score": "heuristic score; not affinity or druggability",
}


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _copy_plain(value, path, notes, active=None):
    """Copy known data types without retaining or stringifying live objects."""
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, Real):
        number = float(value)
        if math.isfinite(number):
            return number
        notes.append({"path": path, "reason": "non-finite number replaced with null"})
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if active is None:
        active = set()
    identity = id(value)
    if identity in active:
        notes.append({"path": path, "reason": "cyclic reference omitted"})
        return None
    active.add(identity)
    try:
        if isinstance(value, Mapping):
            copied = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise TypeError("Report field names must be strings at " + path)
                child_path = path + "/" + key.replace("~", "~0").replace("/", "~1")
                if key in _GEOMETRY_KEYS:
                    notes.append({"path": child_path, "reason": "display geometry omitted"})
                else:
                    copied[key] = _copy_plain(item, child_path, notes, active)
            return copied
        if isinstance(value, (list, tuple)):
            return [_copy_plain(item, path + "/" + str(index), notes, active)
                    for index, item in enumerate(value)]
        # NumPy is optional. Inspect only NumPy-owned types, not arbitrary live
        # objects advertising .tolist(), which may trigger scene operations.
        if type(value).__module__.split(".", 1)[0] == "numpy":
            if int(value.size) > _ARRAY_LIMIT:
                notes.append({"path": path, "reason": "large array omitted",
                              "shape": list(value.shape), "dtype": str(value.dtype)})
                return None
            return _copy_plain(value.tolist(), path, notes, active)
        notes.append({"path": path, "reason": "unsupported live or non-data object omitted",
                      "type": type(value).__name__})
        return None
    finally:
        active.remove(identity)


def _candidate_source(candidate):
    if candidate.get("source") is not None:
        return candidate["source"]
    kind = candidate.get("kind")
    if kind == "geometry":
        return "KVFinder geometry prediction"
    if kind in ("observed_ligand", "ligand", "ion", "metal"):
        return "observed coordinates"
    if kind == "selection":
        return "current selection"
    if kind == "neighborhood":
        return "navigation fallback; not a detected cavity"
    return None


def build_report(result, *, action, target, candidate_index=0, elapsed=None,
                 cached=False, signature=None, captured_at=None, stale=False):
    """Return a detached, JSON-safe report of the original computation.

    ``candidate_index`` is the original computation index, never a sorted table
    row. Use ``None`` after Undo when no candidate is currently previewed.
    Missing capture times remain null; exporting now is not evidence that the
    structure was captured now. All exclusions are described in notes.
    """
    if action not in _ACTIONS:
        raise ValueError("Unsupported quick report action: " + str(action))
    if not isinstance(result, Mapping):
        raise TypeError("A completed quick result is required for export")
    if result.get("action", action) != action:
        raise ValueError("Report action does not match the computed result")
    candidates = result.get("candidates", ())
    if not isinstance(candidates, (list, tuple)):
        raise TypeError("Quick result candidates must be a list or tuple")
    if candidate_index is not None:
        if isinstance(candidate_index, bool) or not isinstance(candidate_index, Integral):
            raise ValueError("Preview candidate index must be an integer or None")
        if (candidates and not 0 <= candidate_index < len(candidates)) or (not candidates and candidate_index != 0):
            raise ValueError("Preview candidate is not present in this result")
    notes = []
    copied = _copy_plain(result, "/result", notes)
    copied_candidates = copied.pop("candidates", [])
    exported_candidates = []
    for index, candidate in enumerate(copied_candidates):
        if not isinstance(candidate, dict):
            raise TypeError("Every quick candidate must be a mapping")
        # Original candidate fields live under data, so future fields cannot
        # collide with provenance, rank or the current-preview annotations.
        exported_candidates.append({
            "original_index": index, "original_rank": index + 1,
            "is_current_preview": index == candidate_index,
            "source": _candidate_source(candidate),
            "units": {key: unit for key, unit in _UNITS.items() if key in candidate},
            "data": candidate,
        })
    preview = None
    if exported_candidates and candidate_index is not None:
        chosen = exported_candidates[int(candidate_index)]
        preview = {"original_index": int(candidate_index), "original_rank": int(candidate_index) + 1,
                   "id": chosen["data"].get("id"), "label": chosen["data"].get("label")}
    metadata = _copy_plain({"action": action, "target": target,
                           "input_signature": signature, "captured_at": captured_at,
                           "elapsed_seconds": elapsed}, "/metadata", notes)
    return {
        "schema_version": 1, "action": metadata["action"], "target": metadata["target"],
        "title": copied.get("title"),
        "provenance": {"source": "ChimeraX Codex Bridge local quick actions",
                       "input_signature": metadata["input_signature"],
                       "captured_at": metadata["captured_at"], "exported_at": _utc_now(),
                       "elapsed_seconds": metadata["elapsed_seconds"],
                       "cached": bool(cached), "stale": bool(stale)},
        "preview": preview, "units": dict(_UNITS), "candidates": exported_candidates,
        "result_data": copied, "export_notes": notes,
    }


def _json(value, *, pretty=False):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2 if pretty else None)


def _flatten(value, prefix=""):
    """Flatten mappings, escaping dots/backslashes so column names are unique."""
    if isinstance(value, dict) and value:
        rows = {}
        for key, item in value.items():
            escaped = key.replace("\\", "\\\\").replace(".", "\\.")
            name = prefix + "." + escaped if prefix else escaped
            rows.update(_flatten(item, name))
        return rows
    return {prefix: value}


def _csv_cell(value):
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        value = _json(value)
    if isinstance(value, str):
        # Prefix only text. Real negative numbers stay numeric for spreadsheet
        # sorting/calculation. JSON/Markdown preserve all original text exactly.
        offset = 0
        while offset < len(value) and (value[offset].isspace() or value[offset] == "\ufeff"):
            offset += 1
        probe = value[offset:]
        if (value and ord(value[0]) < 32) or probe.startswith(("=", "+", "-", "@")):
            return "'" + value
    return value


def _csv(report):
    common = _flatten({key: value for key, value in report.items() if key != "candidates"})
    common["csv_text_safety"] = ("Formula-like text is prefixed with an apostrophe for spreadsheet safety; "
                                 "numeric measurements retain their values. Nested records use JSON.")
    rows = []
    for candidate in report.get("candidates", ()):
        rows.append({**common, **_flatten(candidate, "candidate")})
    if not rows:
        rows = [common]
    columns = list(dict.fromkeys(key for row in rows for key in row))
    # Put candidate identities and actual measurements before shared context.
    columns.sort(key=lambda key: (not key.startswith("candidate."), key))
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow([_csv_cell(key) for key in columns])
    for row in rows:
        writer.writerow([_csv_cell(row.get(key)) for key in columns])
    return output.getvalue()


def _md_text(value):
    if value is None:
        return "Unavailable"
    if isinstance(value, (list, dict)):
        value = _json(value)
    return (str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace("\\", "\\\\").replace("|", "\\|").replace("`", "\\`")
            .replace("*", "\\*").replace("_", "\\_").replace("[", "\\[")
            .replace("]", "\\]").replace("\r\n", "<br>").replace("\n", "<br>").replace("\r", "<br>"))


def _markdown(report):
    lines = ["# " + _md_text(report.get("title") or "Quick action report"), "",
             "Target: " + _md_text(report.get("target")), "",
             "Action: " + _md_text(report.get("action")), ""]
    provenance = report.get("provenance", {})
    lines += ["Captured: " + _md_text(provenance.get("captured_at")), "",
              "Input signature: " + _md_text(provenance.get("input_signature")), "",
              "Computation time: " + _md_text(provenance.get("elapsed_seconds")) + " s; cached: " +
              ("yes" if provenance.get("cached") else "no") + ".", ""]
    preview = report.get("preview")
    if preview:
        lines += ["Current preview: original rank " + str(preview["original_rank"]) + " — " +
                  _md_text(preview.get("label")), ""]
    if provenance.get("stale"):
        lines += ["**Historical result: structure inputs have changed since this calculation.**", ""]
    lines += ["## Original computation summary", ""]
    for line in report.get("result_data", {}).get("summary", ()):
        lines += ["- " + _md_text(line)]
    candidates = report.get("candidates", ())
    if candidates:
        lines += ["", "## Candidates", "",
                  "Rank | Preview | Candidate | Source | Measurements", "--- | --- | --- | --- | ---"]
        for candidate in candidates:
            data = candidate["data"]
            measurements = [f"{_md_text(key)}: {_md_text(data[key])} {_md_text(unit)}"
                            for key, unit in candidate["units"].items()
                            if key not in ("center", "bounds")]
            lines.append(" | ".join((str(candidate["original_rank"]),
                                     "Current" if candidate["is_current_preview"] else "",
                                     _md_text(data.get("label")), _md_text(candidate.get("source")),
                                     "; ".join(measurements) or "Unavailable")))
    lines += ["", "## Evidence and limits", ""]
    for item in report.get("result_data", {}).get("details", ()):
        lines.append("- " + _md_text(item))
    for candidate in candidates:
        evidence = candidate["data"].get("evidence", ())
        if evidence:
            lines += ["", "### " + _md_text(candidate["data"].get("label")), ""]
            lines.extend("- " + _md_text(item) for item in evidence)
    # The complete JSON appendix preserves every retained scientific field,
    # including future additions not yet in the human-readable summary table.
    payload = _json(report, pretty=True)
    fence = "```"
    while fence in payload:
        fence += "`"
    lines += ["", "## Complete data and provenance", "", fence + "json", payload, fence, ""]
    return "\n".join(lines)


def save_report(path, report, format):
    """Atomically save CSV, JSON or Markdown and return the final ``Path``.

    A missing suffix is appended. An incompatible suffix raises before any
    file is created or replaced. Serialization failures leave existing files
    untouched; the temporary file is always removed if replacement fails.
    """
    selected = str(format).lower().strip()
    if selected == "md":
        selected = "markdown"
    suffixes = {"csv": (".csv",), "json": (".json",), "markdown": (".md", ".markdown")}
    if selected not in suffixes:
        raise ValueError("Report format must be CSV, JSON or Markdown")
    destination = Path(path).expanduser()
    if not destination.suffix:
        destination = destination.with_suffix(suffixes[selected][0])
    elif destination.suffix.lower() not in suffixes[selected]:
        raise ValueError(f"File extension {destination.suffix!r} does not match {selected}; "
                         f"use {' or '.join(suffixes[selected])}")
    if not isinstance(report, dict):
        raise TypeError("Report must be a dictionary produced by build_report")
    # Validate the entire payload, even when exporting CSV/Markdown. In
    # particular, do not hide nonfinite values in nested stringified fields.
    serialized = _json(report, pretty=True)
    content = serialized + "\n" if selected == "json" else _csv(report) if selected == "csv" else _markdown(report)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="",
                                         dir=destination.parent, prefix="." + destination.name + ".",
                                         suffix=".tmp", delete=False) as handle:
            temporary_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, destination)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return destination
