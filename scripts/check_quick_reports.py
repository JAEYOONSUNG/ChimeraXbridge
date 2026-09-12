"""Scientific report oracle: measured fixtures and real file round trips."""
import csv
import importlib.util
import json
import math
from pathlib import Path
import tempfile
from unittest.mock import patch

import numpy as np


ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name + "_report_check", ROOT / "src" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


reports = load("quick_reports")
analyze, pockets, views = [load("quick_" + name) for name in ("analyze", "pockets", "views")]
residues = [
    {"name": "ALA", "chain": "A", "number": 1, "spec": "#1/A:1", "polymer": "protein", "category": "main"},
    {"name": "GLY", "chain": "A", "number": 2, "spec": "#1/A:2", "polymer": "protein", "category": "main"},
    {"name": "ATP", "chain": "L", "number": 5, "spec": "#1/L:5", "polymer": "other", "category": "ligand"},
]
snapshot = {"target_label": '실험 α, "A"\nTarget', "signature": "scientific-fixture-signature", "selection_specs": (),
            "models": [{"spec": "#1", "name": "실험", "residues": residues,
                        "coords": np.array([[0, 0, 0], [0, 3, 0], [1.23456789012345, 0, 0],
                                            [2, 0, 0], [2.5, 0, 0], [3, 0, 0]]),
                        "elements": np.full(6, 6), "residue_index": np.array([0, 1, 2, 2, 2, 2]),
                        "bfactors": np.array([10, 20, 30, 30, 30, 30]), "occupancies": np.ones(6),
                        "atom_names": ("CA", "CA", "C1", "C2", "C3", "C4")} ]}


def report_for(result, action, **extra):
    return reports.build_report(result, action=action, target=snapshot["target_label"],
                                signature=snapshot["signature"], captured_at="2026-09-12T05:48:21+09:00",
                                elapsed=0.0123456789012345, **extra)


def strict_json(text):
    def reject(token):
        raise AssertionError("Nonstandard JSON constant: " + token)
    return json.loads(text, parse_constant=reject)


def expect_failure(callback, expected=(ValueError, TypeError, OSError)):
    try:
        callback()
    except expected:
        return
    raise AssertionError("Expected operation to fail")


analysis = analyze.compute(snapshot)
actual_distance = math.dist(snapshot["models"][0]["coords"][0], snapshot["models"][0]["coords"][2])
assert analysis["candidates"][0]["nearest_distance"] == actual_distance
fixture_results = {"analyze": analysis, "pocket": pockets.compute(snapshot, "pocket")}
fixture_results.update({action: views.compute(snapshot, action) for action in ("view", "figure", "zoom")})
assert fixture_results["pocket"]["candidates"][0]["kind"] == "observed_ligand"

# Independent voxel arithmetic deliberately differs from the rounded UI label.
grid_step = 0.65
volume = 41 * grid_step ** 3
depth = math.sqrt(2) * grid_step
geometry = {"id": "#1:cavity:KAA", "label": f'공동 | "KAA" · {volume:.0f} Å³\nUnrounded below',
            "model_spec": "#1", "kind": "geometry", "volume": np.float64(volume),
            "area": np.float64(19 * grid_step ** 2), "max_depth": depth, "avg_depth": None,
            "grid_step": grid_step, "grid_cells": np.int64(7 * 11 * 13),
            "contact_residues": np.int32(3), "rank_score": -1.234567890123,
            "center": np.array([-1.125, 2, 3.75]), "lining_specs": ["#1/A:1", "#1/A:2"],
            "evidence": ['Evidence, "quoted"\n두 번째 줄', "Geometric prediction; binding unassessed."],
            "mesh": {"vertices": np.broadcast_to(np.zeros(3), (1000000, 3)),
                     "triangles": np.broadcast_to(np.zeros(3, dtype=np.int32), (1000000, 3))},
            "future_measurement": {"value": -7.123456789012345, "unit": "kJ/mol", "note": "test field only"}}
neighborhood = {"id": "#1:neighborhood", "label": "=HYPERLINK(\"malicious\")", "kind": "neighborhood",
                "contact_residues": 0, "max_depth": float("nan"), "avg_depth": float("inf"),
                "evidence": ["Cavity measurement unavailable; this is only a navigation fallback."]}
fixture_results["cavity"] = {"title": "Cavity 결과", "action": "cavity", "summary": ["Rounded display only"],
                             "details": ["Ranking is not affinity.", "도킹·실험 검증 없음"],
                             "metrics": [{"label": "Volume", "value": f"{volume:.0f} Å³"}],
                             "candidates": [geometry, neighborhood],
                             "future_result_field": {"negative": -9.25, "note": "@TEXT()"}}

with tempfile.TemporaryDirectory(prefix="quick-reports-oracle-") as directory:
    directory = Path(directory)
    for action, result in fixture_results.items():
        payload = report_for(result, action, candidate_index=1 if action == "cavity" else 0, cached=True, stale=True)
        encoded = strict_json(json.dumps(payload, allow_nan=False, ensure_ascii=False))
        assert encoded["action"] == action
        assert encoded["target"] == snapshot["target_label"]
        assert encoded["provenance"]["input_signature"] == snapshot["signature"]
        assert encoded["provenance"]["captured_at"] == "2026-09-12T05:48:21+09:00"
        assert encoded["provenance"]["cached"] and encoded["provenance"]["stale"]
        assert encoded["result_data"]["details"] == result["details"]
        if result["candidates"]:
            assert [c["original_rank"] for c in encoded["candidates"]] == list(range(1, len(result["candidates"]) + 1))
            assert sum(c["is_current_preview"] for c in encoded["candidates"]) == 1
        else:
            assert encoded["preview"] is None
        for format, suffix in (("csv", ".csv"), ("json", ".json"), ("markdown", ".md")):
            destination = reports.save_report(directory / (action + "-" + format), payload, format)
            assert destination.suffix == suffix and destination.is_file()
            if format == "json":
                assert strict_json(destination.read_text()) == encoded
            elif format == "markdown":
                text = destination.read_text()
                assert "Historical result:" in text and "Complete data and provenance" in text
                # The appendix is complete, including fields the summary does not know.
                appendix = text.split("```json\n", 1)[1].rsplit("\n```", 1)[0]
                assert strict_json(appendix) == encoded
            else:
                with destination.open(newline="", encoding="utf-8") as handle:
                    rows = list(csv.DictReader(handle))
                assert len(rows) == max(1, len(result["candidates"]))
                assert rows[0]["target"] == snapshot["target_label"]
                assert float(rows[0]["provenance.elapsed_seconds"]) == 0.0123456789012345
                if action == "analyze":
                    assert float(rows[0]["candidate.data.nearest_distance"]) == actual_distance
                    neighbors = strict_json(rows[0]["candidate.data.neighbors"])
                    assert neighbors[0]["distance"] == actual_distance
                    assert int(rows[0]["result_data.counts.protein"]) == 2
                    assert rows[0]["units.distance"] == "Å"
                if action == "cavity":
                    assert float(rows[0]["candidate.data.volume"]) == volume
                    assert float(rows[0]["candidate.data.max_depth"]) == depth
                    assert float(rows[0]["candidate.data.rank_score"]) == geometry["rank_score"]
                    assert float(rows[0]["candidate.data.future_measurement.value"]) == geometry["future_measurement"]["value"]
                    assert rows[0]["candidate.data.label"] == geometry["label"]
                    assert strict_json(rows[0]["candidate.data.evidence"]) == geometry["evidence"]
                    assert rows[0]["candidate.units.volume"] == "Å³"
                    assert rows[1]["candidate.data.volume"] == ""
                    assert rows[1]["candidate.data.max_depth"] == ""
                    assert rows[1]["candidate.data.avg_depth"] == ""
                    assert int(rows[1]["candidate.data.contact_residues"]) == 0
                    assert rows[1]["candidate.original_rank"] == "2"
                    assert rows[1]["candidate.is_current_preview"] == "True"
                    assert rows[1]["candidate.data.label"].startswith("'=HYPERLINK")
                    assert rows[0]["result_data.future_result_field.note"] == "'@TEXT()"
    print("Measured result fixtures and CSV/JSON/Markdown round trips: six actions verified")

    payload = report_for(fixture_results["cavity"], "cavity", candidate_index=1)
    assert payload["preview"]["original_rank"] == 2
    assert payload["candidates"][0]["data"]["volume"] == volume
    assert "mesh" not in payload["candidates"][0]["data"]
    assert any(note["reason"] == "display geometry omitted" for note in payload["export_notes"])
    assert payload["candidates"][1]["data"]["max_depth"] is None
    assert len(json.dumps(payload)) < 20000
    payload["candidates"][0]["data"]["evidence"].append("Change exported copy")
    payload["candidates"][0]["data"]["center"][0] = 999
    assert "Change exported copy" not in geometry["evidence"] and geometry["center"][0] == -1.125
    assert math.isnan(neighborhood["max_depth"]) and "mesh" in geometry

    class LiveObject:
        def __str__(self):
            raise AssertionError("A live object must never be stringified")

    cycle = {}
    cycle["self"] = cycle
    odd = {"candidates": [], "live": LiveObject(), "array": np.zeros(10001), "cycle": cycle,
           "flag": np.bool_(True), "empty": {}, "dot.key": 2, "dot": {"key": 3}}
    odd_report = reports.build_report(odd, action="analyze", target="Copied data")
    assert odd_report["result_data"]["live"] is None and odd_report["result_data"]["array"] is None
    assert odd_report["result_data"]["flag"] is True
    assert odd_report["provenance"]["captured_at"] is None
    assert odd_report["provenance"]["elapsed_seconds"] is None
    assert odd_report["result_data"]["cycle"]["self"] is None
    assert len(odd_report["export_notes"]) == 3
    assert any(note["shape"] == [10001] for note in odd_report["export_notes"] if "shape" in note)
    odd_path = reports.save_report(directory / "odd.csv", odd_report, "csv")
    with odd_path.open(newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["result_data.dot\\.key"] == "2" and row["result_data.dot.key"] == "3"
    assert row["result_data.empty"] == "{}"

    # Undo removes only the preview; the original scientific report survives.
    for action in ("cavity", "view", "figure"):
        undone = report_for(fixture_results[action], action, candidate_index=None)
        assert undone["preview"] is None
        assert not any(candidate["is_current_preview"] for candidate in undone["candidates"])
        assert len(undone["candidates"]) == len(fixture_results[action]["candidates"])
        if action == "cavity":
            assert undone["candidates"][0]["data"]["volume"] == volume
            assert [candidate["original_rank"] for candidate in undone["candidates"]] == [1, 2]
        saved = reports.save_report(directory / ("undone-" + action + ".json"), undone, "json")
        assert strict_json(saved.read_text())["preview"] is None
        csv_path = reports.save_report(directory / ("undone-" + action + ".csv"), undone, "csv")
        with csv_path.open(newline="") as handle:
            rows = list(csv.DictReader(handle))
        assert all(row["preview"] == "" for row in rows)
        assert all(row.get("candidate.is_current_preview", "False") == "False" for row in rows)
        md_path = reports.save_report(directory / ("undone-" + action + ".md"), undone, "md")
        assert "Current preview: original rank" not in md_path.read_text()

    # Negative-control assertions prove the JSON oracle rejects nonfinite values.
    expect_failure(lambda: strict_json('{"value": NaN}'), (AssertionError,))
    for action, index in (("unknown", 0), ("cavity", 2), ("cavity", -1), ("cavity", 0.5), ("cavity", True)):
        expect_failure(lambda: report_for(fixture_results["cavity"], action, candidate_index=index))
    expect_failure(lambda: reports.build_report({}, action="view", target="x", candidate_index=1))
    expect_failure(lambda: reports.build_report({"action": "figure"}, action="view", target="x"))
    expect_failure(lambda: reports.build_report({5: "ambiguous key"}, action="analyze", target="x"))

    for text in ('=1+2', ' +SUM(A1)', '-formula', '@TEXT()', '\t=1', '\r=1', '\n=1', '\ufeff=1',
                 '\v=1', '\u2003=1', '\ufeff \ufeff=1'):
        assert reports._csv_cell(text) == "'" + text
    for number in (-4, -1.234567890123, 0, 4.75):
        assert reports._csv_cell(number) == number

    # Failure at serialization and atomic replacement must not truncate an existing file.
    for format, suffix in (("csv", ".csv"), ("json", ".json"), ("md", ".md")):
        existing = directory / ("existing" + suffix)
        existing.write_bytes(b"ORIGINAL FILE\n")
        for bad in ({"bad": float("nan")}, {"bad": LiveObject()}):
            expect_failure(lambda: reports.save_report(existing, bad, format))
            assert existing.read_bytes() == b"ORIGINAL FILE\n"
        with patch.object(reports.os, "replace", side_effect=OSError("simulated disk failure")):
            expect_failure(lambda: reports.save_report(existing, payload, format))
        assert existing.read_bytes() == b"ORIGINAL FILE\n"
        assert not list(directory.glob("." + existing.name + ".*.tmp"))
        with patch.object(reports.os, "fsync", side_effect=OSError("simulated flush failure")):
            expect_failure(lambda: reports.save_report(existing, payload, format))
        assert existing.read_bytes() == b"ORIGINAL FILE\n"
        assert not list(directory.glob("." + existing.name + ".*.tmp"))
        reports.save_report(existing, payload, format)
        assert existing.read_bytes() != b"ORIGINAL FILE\n"
    wrong = directory / "wrong.png"
    wrong.write_bytes(b"original image")
    expect_failure(lambda: reports.save_report(wrong, payload, "json"))
    assert wrong.read_bytes() == b"original image"
    expect_failure(lambda: reports.save_report(directory / "bad", payload, "png"))
    assert not (directory / "bad").exists()
    assert reports.save_report(directory / "UPPER.JSON", payload, "JSON").name == "UPPER.JSON"
    assert reports.save_report(directory / "long.markdown", payload, "md").suffix == ".markdown"
    print("Missing values, source/rank provenance, exclusion notes, text safety and atomic failures verified")

print("QUICK_REPORTS_OK")
