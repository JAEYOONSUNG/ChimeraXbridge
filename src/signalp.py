"""SignalP sequence analysis and 3D annotation helpers.

The local SignalP CLI is preferred when available.  Without it, the workflow
opens the official DTU SignalP page with the current chain FASTA and applies a
clearly labeled local N-terminal preview so the user still gets model context.
"""

import json
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import webbrowser
from pathlib import Path


SIGNALP_WEB_URL = "https://services.healthtech.dtu.dk/services/SignalP-6.0/"
SIGNALP_GROUP_PREFIX = "signalp_"

SIGNALP_COLORS = {
    "signal_peptide": "#ffb000",
    "n_region": "#42b883",
    "h_region": "#f6c453",
    "c_region": "#ef6f6c",
    "cleavage": "#ffffff",
    "mature": "#8ecae6",
    "prodomain": "#a855f7",
    "fallback": "#c4b5fd",
}

HYDROPHOBIC = set("AILMFWVYC")
POSITIVE = set("KR")
SMALL = set("ASGCSTV")
PRODOMAIN_RICH = set("DESTQPGKR")


def run_signalp_command_text(session, request="", *, executor=None):
    """Entry point used by the ChimeraX command and slash-command bridge."""
    tokens = _split_tokens(request)
    action = "run"
    if tokens and tokens[0].lower() in {"run", "analyze", "analysis", "web", "apply", "load", "clear", "prodomain"}:
        action = tokens.pop(0).lower()
    options = _parse_options(tokens)

    model_hint = options.get("model") or options.get("target")
    if action == "clear":
        return clear_signalp_view(session, executor=executor)
    if action == "web":
        return launch_signalp_web(
            session,
            model_hint=model_hint,
            organism=options.get("organism") or options.get("org") or "other",
            mode=options.get("mode") or "fast",
        )
    if action in {"apply", "load"}:
        path = options.get("path") or options.get("_arg")
        if not path:
            return "SignalP apply/load needs a result file or folder path."
        return apply_signalp_results(session, path, model_hint=model_hint, executor=executor)
    if action == "prodomain":
        return run_prodomain_scan(session, model_hint=model_hint, executor=executor)

    return run_signalp_analysis(
        session,
        model_hint=model_hint,
        organism=options.get("organism") or options.get("org") or "other",
        mode=options.get("mode") or "fast",
        output_dir=options.get("out") or options.get("output") or options.get("output_dir"),
        auto_web=not _truthy_false(options.get("web")),
        executor=executor,
    )


def run_signalp_analysis(
    session,
    *,
    model_hint=None,
    organism="other",
    mode="fast",
    output_dir=None,
    auto_web=True,
    executor=None,
):
    entries = _signalp_chain_entries(session, model_hint=model_hint)
    if not entries:
        return "No protein chain sequence was resolved for SignalP."

    signalp_exe = _find_signalp_executable()
    run_dir = Path(output_dir).expanduser() if output_dir else _default_run_dir()
    run_dir.mkdir(parents=True, exist_ok=True)
    fasta_path = run_dir / "chimerax_signalp_input.fasta"
    fasta_path.write_text(_signalp_fasta(entries), encoding="utf-8")

    used_cli = False
    cli_message = ""
    predictions = []
    if signalp_exe:
        ok, cli_message = _run_signalp_cli(
            signalp_exe,
            fasta_path,
            run_dir,
            organism=organism,
            mode=mode,
        )
        if ok:
            used_cli = True
            predictions = parse_signalp_result_path(run_dir)
        else:
            predictions = []
    if not predictions:
        predictions = _fallback_signalp_predictions(entries)

    prodomain_candidates = find_prodomain_candidates(session, entries, predictions, model_hint=model_hint)
    visual_message = visualize_signalp_annotations(
        session,
        entries,
        predictions,
        prodomain_candidates=prodomain_candidates,
        executor=executor,
        clear_existing=True,
    )

    lines = ["SignalP analysis"]
    lines.append(f"- target chains: {', '.join(entry['spec'] for entry in entries)}")
    if used_cli:
        lines.append(f"- local SignalP: {signalp_exe}")
        lines.append(f"- output folder: {run_dir}")
    elif signalp_exe:
        lines.append(f"- local SignalP failed: {cli_message}")
    else:
        lines.append("- local SignalP CLI not found; applied a local N-terminal preview.")
    lines.extend(_prediction_report_lines(entries, predictions))
    lines.extend(_prodomain_report_lines(prodomain_candidates))
    if visual_message:
        lines.append("- 3D view: " + visual_message)
    if not used_cli and auto_web:
        web_message = launch_signalp_web(session, entries=entries, organism=organism, mode=mode)
        lines.append("- web: " + web_message)
    return "\n".join(lines)


def apply_signalp_results(session, path, *, model_hint=None, executor=None):
    entries = _signalp_chain_entries(session, model_hint=model_hint)
    if not entries:
        return "No protein chain sequence was resolved for SignalP result mapping."
    predictions = parse_signalp_result_path(path)
    if not predictions:
        return f"No SignalP predictions were parsed from {path}."
    prodomain_candidates = find_prodomain_candidates(session, entries, predictions, model_hint=model_hint)
    visual_message = visualize_signalp_annotations(
        session,
        entries,
        predictions,
        prodomain_candidates=prodomain_candidates,
        executor=executor,
        clear_existing=True,
    )
    lines = [f"Applied SignalP results from {Path(path).expanduser()}."]
    lines.extend(_prediction_report_lines(entries, predictions))
    lines.extend(_prodomain_report_lines(prodomain_candidates))
    if visual_message:
        lines.append("- 3D view: " + visual_message)
    return "\n".join(lines)


def run_prodomain_scan(session, *, model_hint=None, executor=None):
    entries = _signalp_chain_entries(session, model_hint=model_hint)
    if not entries:
        return "No protein chain sequence was resolved for prodomain scan."
    predictions = _fallback_signalp_predictions(entries)
    candidates = find_prodomain_candidates(session, entries, predictions, model_hint=model_hint)
    visual_message = visualize_signalp_annotations(
        session,
        entries,
        [],
        prodomain_candidates=candidates,
        executor=executor,
        clear_existing=True,
    )
    lines = ["Prodomain-like N-terminal scan"]
    lines.extend(_prodomain_report_lines(candidates))
    if visual_message:
        lines.append("- 3D view: " + visual_message)
    return "\n".join(lines)


def launch_signalp_web(session, *, entries=None, model_hint=None, organism="other", mode="fast"):
    if entries is None:
        entries = _signalp_chain_entries(session, model_hint=model_hint)
    if not entries:
        return "No protein chain sequence was resolved for SignalP web submission."
    fasta = _signalp_fasta(entries)
    _copy_text_to_clipboard(fasta)
    helper_error = _launch_with_browser_helper(
        ["signalp"],
        fasta,
        signalpOrganism=str(organism or "other"),
        signalpMode=str(mode or "fast"),
    )
    if helper_error is None:
        return f"Opened SignalP 6.0 and filled {len(entries)} sequence(s) in Chrome."
    js_code = _generic_sequence_fill_js(fasta)
    ok, detail = _run_safari_prefill(SIGNALP_WEB_URL, js_code)
    if ok:
        return f"Opened SignalP 6.0 and copied {len(entries)} FASTA sequence(s) to the clipboard."
    webbrowser.open(SIGNALP_WEB_URL)
    return (
        f"Opened SignalP 6.0 and copied FASTA to the clipboard; automatic filling failed: "
        f"{detail or helper_error}"
    )


def clear_signalp_view(session, *, executor=None):
    from .named_selection import list_groups, remove_group

    old_specs = list(getattr(session, "_codex_signalp_managed_specs", []) or [])
    for spec in old_specs:
        for command in (f"~label {spec}",):
            try:
                _run_chimerax(session, command, executor=executor)
            except Exception:
                pass
    removed = 0
    for group_name in list(list_groups(session)):
        if str(group_name).startswith(SIGNALP_GROUP_PREFIX):
            try:
                if remove_group(session, group_name):
                    removed += 1
            except Exception:
                pass
    session._codex_signalp_managed_specs = []
    return f"Cleared {removed} SignalP annotation group(s)."


def visualize_signalp_annotations(
    session,
    entries,
    predictions,
    *,
    prodomain_candidates=None,
    executor=None,
    clear_existing=True,
):
    from .named_selection import add_group

    if clear_existing:
        clear_signalp_view(session, executor=executor)

    entries_by_id = _entries_by_prediction_id(entries)
    predictions_by_id = _predictions_by_id(predictions)
    group_count = 0
    managed_specs = []
    commands = []

    for entry in entries:
        prediction = predictions_by_id.get(entry["signalp_id"])
        if not prediction or not _prediction_has_signal_peptide(prediction):
            continue
        sp_start = int(prediction.get("signal_start") or 1)
        sp_end = int(prediction.get("signal_end") or 0)
        if sp_end <= 0:
            continue
        chain_spec = entry["spec"]
        token = _group_token(entry)
        whole = _range_spec(entry, sp_start, sp_end)
        n_region, h_region, c_region = _signal_regions(sp_start, sp_end)
        region_specs = [
            ("signal_peptide", "Signal peptide", whole, SIGNALP_COLORS["signal_peptide"], "c"),
            ("n_region", "SignalP N-region", _range_spec(entry, *n_region), SIGNALP_COLORS["n_region"], "c"),
            ("h_region", "SignalP H-region", _range_spec(entry, *h_region), SIGNALP_COLORS["h_region"], "c"),
            ("c_region", "SignalP C-region", _range_spec(entry, *c_region), SIGNALP_COLORS["c_region"], "c"),
        ]
        for suffix, label, spec, color, target in region_specs:
            if not spec:
                continue
            name = f"{SIGNALP_GROUP_PREFIX}{token}_{suffix}"
            add_group(session, name, spec, color=color)
            group_count += 1
            commands.extend([
                f"show {chain_spec} cartoons",
                f"color {spec} {color} target {target}",
            ])
            managed_specs.append(spec)

        cleavage_start = max(sp_start, sp_end - 1)
        cleavage_spec = _range_spec(entry, cleavage_start, sp_end)
        if cleavage_spec:
            add_group(session, f"{SIGNALP_GROUP_PREFIX}{token}_cleavage", cleavage_spec, color=SIGNALP_COLORS["cleavage"])
            group_count += 1
            commands.extend([
                f"show {cleavage_spec} atoms",
                f"style {cleavage_spec} stick",
                f"color {cleavage_spec} {SIGNALP_COLORS['cleavage']} target ab",
                f"label {cleavage_spec} residues",
            ])
            managed_specs.append(cleavage_spec)

        mature_start = int(prediction.get("mature_start") or (sp_end + 1))
        if mature_start <= len(entry["sequence"]):
            mature_end = min(len(entry["sequence"]), mature_start + 24)
            mature_spec = _range_spec(entry, mature_start, mature_end)
            if mature_spec:
                add_group(session, f"{SIGNALP_GROUP_PREFIX}{token}_mature_nterm", mature_spec, color=SIGNALP_COLORS["mature"])
                group_count += 1
                commands.append(f"color {mature_spec} {SIGNALP_COLORS['mature']} target c")
                managed_specs.append(mature_spec)

    for candidate in prodomain_candidates or []:
        entry = entries_by_id.get(candidate.get("signalp_id"))
        if not entry:
            continue
        spec = candidate.get("spec") or _range_spec(entry, candidate["start"], candidate["end"])
        if not spec:
            continue
        name = f"{SIGNALP_GROUP_PREFIX}{_group_token(entry)}_prodomain"
        add_group(session, name, spec, color=SIGNALP_COLORS["prodomain"])
        group_count += 1
        commands.extend([
            f"show {entry['spec']} cartoons",
            f"color {spec} {SIGNALP_COLORS['prodomain']} target c",
            f"label {_range_spec(entry, candidate['start'], candidate['start'])} residues",
            f"label {_range_spec(entry, candidate['end'], candidate['end'])} residues",
        ])
        managed_specs.append(spec)

    if not commands:
        return "no SignalP/prodomain regions to draw."

    for command in commands:
        if not command or " None" in command:
            continue
        try:
            _run_chimerax(session, command, executor=executor)
        except Exception:
            pass
    try:
        _run_chimerax(session, "view " + " ".join(sorted(set(managed_specs))[:20]), executor=executor)
    except Exception:
        pass
    session._codex_signalp_managed_specs = sorted(set(managed_specs))
    return f"created {group_count} named model-panel group(s)."


def parse_signalp_result_path(path):
    root = Path(path).expanduser()
    files = []
    if root.is_file():
        files = [root]
    elif root.is_dir():
        preferred = (
            "prediction_results.txt",
            "output.gff3",
            "processed_entries.gff3",
            "prediction_results.json",
        )
        for name in preferred:
            candidate = root / name
            if candidate.exists():
                files.append(candidate)
        files.extend(sorted(root.glob("*.json")))
        files.extend(sorted(root.glob("*.gff3")))
        files.extend(sorted(root.glob("*.gff")))
        files.extend(sorted(root.glob("*.txt")))
    predictions = {}
    for file_path in files:
        suffix = file_path.suffix.lower()
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        parsed = []
        if suffix == ".json":
            parsed = parse_signalp_json(text)
        elif suffix in {".gff", ".gff3"}:
            parsed = parse_signalp_gff(text)
        else:
            parsed = parse_signalp_summary(text)
        for item in parsed:
            seq_id = str(item.get("id") or "").strip()
            if not seq_id:
                continue
            prior = predictions.get(seq_id, {})
            predictions[seq_id] = _merge_prediction(prior, item)
    return list(predictions.values())


def parse_signalp_summary(text):
    predictions = []
    header = None
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            candidate = line.lstrip("#").strip()
            if _looks_like_signalp_header(candidate):
                header = _split_table_line(candidate)
            continue
        columns = _split_table_line(line)
        if len(columns) < 2:
            continue
        if header and len(header) == len(columns):
            row = dict(zip(header, columns))
        elif header and len(columns) >= len(header):
            row = dict(zip(header[:-1], columns[: len(header) - 1]))
            row[header[-1]] = " ".join(columns[len(header) - 1 :])
        else:
            row = {"ID": columns[0], "Prediction": columns[1], "CS Position": " ".join(columns[2:])}
        prediction = _prediction_from_row(row)
        if prediction:
            predictions.append(prediction)
    return predictions


def parse_signalp_gff(text):
    predictions = {}
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 9:
            continue
        seq_id, source, feature_type, start, end, score, strand, phase, attrs = parts[:9]
        try:
            start_i = int(start)
            end_i = int(end)
        except Exception:
            continue
        item = predictions.setdefault(
            seq_id,
            {
                "id": seq_id,
                "prediction": "SP",
                "signal_start": None,
                "signal_end": None,
                "mature_start": None,
                "source": source or "SignalP GFF3",
                "probabilities": {},
            },
        )
        normalized = feature_type.lower().replace("-", "_")
        if "signal" in normalized and "peptide" in normalized:
            item["signal_start"] = start_i
            item["signal_end"] = end_i
            item["mature_start"] = end_i + 1
            attrs_map = _parse_gff_attrs(attrs)
            note = attrs_map.get("Note") or attrs_map.get("note") or attrs_map.get("Name") or ""
            if note:
                item["prediction"] = note
        elif "mature" in normalized:
            item["mature_start"] = start_i
        elif "propeptide" in normalized or "prodomain" in normalized:
            item.setdefault("features", []).append({"type": "propeptide", "start": start_i, "end": end_i})
    return list(predictions.values())


def parse_signalp_json(text):
    try:
        payload = json.loads(text)
    except Exception:
        return []
    records = []
    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, dict):
        for key in ("results", "predictions", "data"):
            if isinstance(payload.get(key), list):
                records = payload[key]
                break
        if not records:
            records = [{"id": key, **value} for key, value in payload.items() if isinstance(value, dict)]
    predictions = []
    for record in records:
        if not isinstance(record, dict):
            continue
        row = {}
        for key, value in record.items():
            norm = str(key).strip().lower().replace("_", " ")
            if norm in {"id", "name", "sequence id", "seqid"}:
                row["ID"] = str(value)
            elif norm in {"prediction", "type", "predicted class"}:
                row["Prediction"] = str(value)
            elif "cs" in norm or "cleavage" in norm:
                row["CS Position"] = str(value)
            else:
                row[str(key)] = value
        prediction = _prediction_from_row(row)
        if prediction:
            predictions.append(prediction)
    return predictions


def find_prodomain_candidates(session, entries, predictions, *, model_hint=None):
    candidates = []
    seen = set()
    by_id = _predictions_by_id(predictions)

    for feature in _uniprot_propeptide_features(session, model_hint=model_hint):
        signalp_id = _match_feature_to_entry(feature, entries)
        if not signalp_id:
            continue
        key = (signalp_id, int(feature["start"]), int(feature["end"]))
        if key in seen:
            continue
        seen.add(key)
        candidates.append(
            {
                "signalp_id": signalp_id,
                "start": int(feature["start"]),
                "end": int(feature["end"]),
                "spec": feature.get("spec"),
                "confidence": "high",
                "score": 8,
                "reason": f"UniProt {feature.get('feature_type', 'Propeptide')}: {feature.get('label', '')}".strip(),
            }
        )

    for entry in entries:
        prediction = by_id.get(entry["signalp_id"], {})
        candidate = _heuristic_prodomain_candidate(entry, prediction)
        if not candidate:
            continue
        key = (candidate["signalp_id"], candidate["start"], candidate["end"])
        if key in seen:
            continue
        seen.add(key)
        candidates.append(candidate)

    candidates.sort(key=lambda item: (-int(item.get("score", 0)), item["signalp_id"], item["start"]))
    return candidates


def _heuristic_prodomain_candidate(entry, prediction):
    seq = entry["sequence"]
    if len(seq) < 90:
        return None
    has_sp = _prediction_has_signal_peptide(prediction)
    signal_end = int(prediction.get("signal_end") or 0) if prediction else 0
    start = signal_end + 1 if signal_end else 1
    if start > 45:
        return None
    search_stop = min(len(seq), start + 180, max(start + 45, len(seq) // 3))
    window = seq[start - 1 : search_stop]
    if len(window) < 35:
        return None

    motif_end = _first_protease_motif_end(window, min_offset=30)
    if motif_end is not None:
        end = start + motif_end - 1
    else:
        end = min(len(seq), start + min(85, max(44, len(window))) - 1)
    if end - start + 1 < 35:
        return None

    segment = seq[start - 1 : end]
    hydrophobic_fraction = _fraction(segment, HYDROPHOBIC)
    rich_fraction = _fraction(segment, PRODOMAIN_RICH)
    complexity = _shannon_entropy(segment)
    charged_fraction = _fraction(segment, set("DEKR"))

    score = 0
    reasons = []
    if has_sp:
        score += 2
        reasons.append("SignalP/signal-peptide N-terminus")
    if motif_end is not None:
        score += 2
        reasons.append("basic convertase-like cleavage motif")
    if rich_fraction >= 0.45:
        score += 1
        reasons.append("prodomain-like polar/charged enrichment")
    if charged_fraction >= 0.18:
        score += 1
        reasons.append("charged N-terminal extension")
    if hydrophobic_fraction <= 0.32:
        score += 1
        reasons.append("not a second hydrophobic signal/TM segment")
    if complexity <= 3.45:
        score += 1
        reasons.append("low-complexity/disordered composition")
    if 40 <= len(segment) <= 180:
        score += 1
        reasons.append("propeptide-sized segment")

    if score < 4:
        return None
    confidence = "high" if score >= 7 else "medium" if score >= 5 else "low"
    return {
        "signalp_id": entry["signalp_id"],
        "start": start,
        "end": end,
        "spec": _range_spec(entry, start, end),
        "confidence": confidence,
        "score": score,
        "reason": "; ".join(reasons),
    }


def _fallback_signalp_predictions(entries):
    predictions = []
    for entry in entries:
        pred = _heuristic_signal_peptide(entry["sequence"])
        if not pred:
            predictions.append(
                {
                    "id": entry["signalp_id"],
                    "prediction": "OTHER",
                    "source": "local heuristic",
                    "probabilities": {},
                }
            )
            continue
        pred.update({"id": entry["signalp_id"], "source": "local heuristic", "fallback": True})
        predictions.append(pred)
    return predictions


def _heuristic_signal_peptide(sequence):
    seq = str(sequence or "").upper()
    if len(seq) < 30:
        return None
    nterm = seq[:70]
    best = None
    for start in range(2, 12):
        for end in range(start + 7, min(start + 19, len(nterm) - 4)):
            segment = nterm[start - 1 : end]
            hyd = _fraction(segment, HYDROPHOBIC)
            if hyd < 0.58:
                continue
            for cleavage in range(end + 3, min(end + 11, len(nterm))):
                minus3 = nterm[cleavage - 3] if cleavage - 3 >= 0 else ""
                minus1 = nterm[cleavage - 1] if cleavage - 1 >= 0 else ""
                small_bonus = 1 if minus3 in SMALL and minus1 in SMALL else 0
                positive_bonus = 1 if any(aa in POSITIVE for aa in nterm[: max(2, start)]) else 0
                score = hyd + (0.2 * small_bonus) + (0.12 * positive_bonus)
                if best is None or score > best[0]:
                    best = (score, cleavage)
    if not best or best[0] < 0.68:
        return None
    cleavage = int(best[1])
    return {
        "prediction": "SP-like local preview",
        "signal_start": 1,
        "signal_end": cleavage,
        "mature_start": cleavage + 1,
        "cs_probability": round(min(0.95, max(0.35, best[0] - 0.2)), 3),
        "probabilities": {"SP-like": round(best[0], 3)},
    }


def _signalp_chain_entries(session, model_hint=None):
    from .toolbar_actions import _protein_chain_entries

    entries = []
    for index, entry in enumerate(_protein_chain_entries(session, model_hint=model_hint), start=1):
        seq = re.sub(r"[^A-Za-z]", "X", str(entry.get("sequence") or "")).upper()
        if len(seq) < 10:
            continue
        item = dict(entry)
        item["sequence"] = seq
        item["signalp_id"] = _safe_signalp_id(entry, index)
        item["residue_numbers"] = _chain_residue_numbers(session, entry.get("spec"), len(seq))
        entries.append(item)
    return entries


def _safe_signalp_id(entry, index):
    base = f"{entry.get('structure_name', 'model')}_{entry.get('chain_id', 'chain')}_{entry.get('spec', index)}"
    token = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(base)).strip("._")
    return token or f"chain_{index}"


def _signalp_fasta(entries):
    blocks = []
    for entry in entries:
        blocks.append(f">{entry['signalp_id']} {entry['spec']}\n{entry['sequence']}")
    return "\n".join(blocks).strip() + ("\n" if blocks else "")


def _chain_residue_numbers(session, chain_spec, sequence_length):
    numbers = {}
    if not chain_spec:
        return numbers
    try:
        from chimerax.atomic import ChainArg

        chain = ChainArg.parse(str(chain_spec), session)[0]
        residues = list(getattr(chain, "residues", []) or [])
    except Exception:
        residues = []
    if len(residues) == sequence_length:
        for idx, residue in enumerate(residues, start=1):
            try:
                numbers[idx] = int(getattr(residue, "number"))
            except Exception:
                numbers[idx] = idx
    return numbers


def _range_spec(entry, start, end):
    try:
        start = int(start)
        end = int(end)
    except Exception:
        return None
    if start <= 0 or end <= 0 or start > end:
        return None
    length = len(entry.get("sequence") or "")
    if length:
        start = max(1, min(start, length))
        end = max(1, min(end, length))
    numbers = entry.get("residue_numbers") or {}
    start_num = numbers.get(start, start)
    end_num = numbers.get(end, end)
    chain_spec = entry["spec"]
    if start_num == end_num:
        return f"{chain_spec}:{start_num}"
    return f"{chain_spec}:{start_num}-{end_num}"


def _signal_regions(start, end):
    length = max(1, end - start + 1)
    if length <= 10:
        return (start, min(end, start + 2)), (min(end, start + 3), max(start, end - 3)), (max(start, end - 2), end)
    c_start = max(start, end - min(5, max(3, length // 4)) + 1)
    n_end = min(c_start - 1, start + min(5, max(3, length // 5)) - 1)
    h_start = min(end, n_end + 1)
    h_end = max(h_start, c_start - 1)
    return (start, n_end), (h_start, h_end), (c_start, end)


def _prediction_from_row(row):
    seq_id = row.get("ID") or row.get("id") or row.get("Name") or row.get("name")
    if not seq_id:
        return None
    prediction_text = str(row.get("Prediction") or row.get("prediction") or "").strip()
    cs_text = str(
        row.get("CS Position")
        or row.get("CS position")
        or row.get("CS")
        or row.get("cleavage")
        or ""
    )
    signal_end, mature_start, cs_probability = _parse_cs_position(cs_text)
    probabilities = {}
    for key, value in row.items():
        if key in {"ID", "id", "Name", "name", "Prediction", "prediction", "CS Position", "CS position", "CS"}:
            continue
        try:
            probabilities[str(key)] = float(value)
        except Exception:
            continue
    if not prediction_text and probabilities:
        prediction_text = max(probabilities.items(), key=lambda item: item[1])[0]
    item = {
        "id": str(seq_id).strip(),
        "prediction": prediction_text or "UNKNOWN",
        "signal_start": 1 if signal_end else None,
        "signal_end": signal_end,
        "mature_start": mature_start,
        "cs_probability": cs_probability,
        "probabilities": probabilities,
        "source": "SignalP",
    }
    return item


def _parse_cs_position(text):
    source = str(text or "")
    if not source or source.strip().upper() in {"NO_SP", "N/A", "NA", "NONE", "-"}:
        return None, None, None
    match = re.search(r"CS\s*pos(?:ition)?\s*:\s*(\d+)\s*-\s*(\d+)", source, re.IGNORECASE)
    if not match:
        match = re.search(r"\b(\d+)\s*-\s*(\d+)\b", source)
    if not match:
        return None, None, None
    signal_end = int(match.group(1))
    mature_start = int(match.group(2))
    prob = None
    p_match = re.search(r"\bPr\s*:\s*([0-9.]+)", source, re.IGNORECASE)
    if p_match:
        try:
            prob = float(p_match.group(1))
        except Exception:
            prob = None
    return signal_end, mature_start, prob


def _merge_prediction(prior, item):
    if not prior:
        return dict(item)
    merged = dict(prior)
    for key, value in item.items():
        if key == "probabilities":
            probs = dict(merged.get("probabilities") or {})
            probs.update(value or {})
            merged["probabilities"] = probs
        elif value not in (None, "", []):
            merged[key] = value
    return merged


def _looks_like_signalp_header(text):
    lowered = text.lower()
    return "id" in lowered and ("prediction" in lowered or "cs" in lowered or "other" in lowered)


def _split_table_line(line):
    if "\t" in line:
        return [part.strip() for part in line.split("\t")]
    if re.search(r"\s{2,}", line.strip()):
        return re.split(r"\s{2,}", line.strip())
    parts = line.strip().split()
    merged = []
    idx = 0
    while idx < len(parts):
        if idx + 1 < len(parts) and parts[idx].lower() == "cs" and parts[idx + 1].lower().startswith("position"):
            merged.append("CS Position")
            idx += 2
            continue
        merged.append(parts[idx])
        idx += 1
    return merged


def _prediction_has_signal_peptide(prediction):
    if not prediction:
        return False
    text = str(prediction.get("prediction") or "").upper()
    if "NO_SP" in text or "NO SIGNAL" in text:
        return False
    if "OTHER" in text and "SP" not in text:
        return False
    if int(prediction.get("signal_end") or 0) > 0:
        return True
    return any(token in text for token in ("SP", "TAT", "LIPO", "PILIN", "SIGNAL"))


def _predictions_by_id(predictions):
    return {str(item.get("id") or "").strip(): item for item in predictions or [] if item.get("id")}


def _entries_by_prediction_id(entries):
    return {entry["signalp_id"]: entry for entry in entries}


def _prediction_report_lines(entries, predictions):
    by_id = _predictions_by_id(predictions)
    lines = ["- predictions:"]
    for entry in entries:
        prediction = by_id.get(entry["signalp_id"])
        if not prediction:
            lines.append(f"  - {entry['spec']}: no parsed SignalP row")
            continue
        label = prediction.get("prediction", "UNKNOWN")
        signal_end = prediction.get("signal_end")
        source = prediction.get("source", "SignalP")
        prob = prediction.get("cs_probability")
        suffix = f", CS Pr {prob:.3f}" if isinstance(prob, (int, float)) else ""
        if signal_end:
            lines.append(f"  - {entry['spec']}: {label}, signal peptide 1-{signal_end}{suffix} [{source}]")
        else:
            lines.append(f"  - {entry['spec']}: {label} [{source}]")
    return lines


def _prodomain_report_lines(candidates):
    lines = ["- prodomain-like candidates:"]
    if not candidates:
        lines.append("  - none above the local evidence threshold")
        return lines
    for cand in candidates:
        lines.append(
            f"  - {cand.get('signalp_id')}: {cand['start']}-{cand['end']} "
            f"{cand.get('confidence', 'candidate')} score {cand.get('score', 0)}; {cand.get('reason', '')}"
        )
    return lines


def _uniprot_propeptide_features(session, *, model_hint=None):
    try:
        from .semantic import get_uniprot_feature_entries

        entries = get_uniprot_feature_entries(session, model_hint=model_hint) or []
    except Exception:
        return []
    hits = []
    for entry in entries:
        text = " ".join(
            str(entry.get(key, "") or "")
            for key in ("feature_type", "label", "description")
        ).lower()
        if any(token in text for token in ("propeptide", "prodomain", "activation peptide", "pro protein")):
            hits.append(entry)
    return hits


def _match_feature_to_entry(feature, entries):
    model_spec = feature.get("model_spec")
    chain_id = str(feature.get("chain_id") or "").strip()
    for entry in entries:
        if not entry.get("spec", "").startswith(str(model_spec) + "/"):
            continue
        if str(entry.get("chain_id") or "").strip() == chain_id:
            return entry["signalp_id"]
    return None


def _find_signalp_executable():
    for env_name in ("SIGNALP_EXE", "SIGNALP6_EXE"):
        value = os.environ.get(env_name)
        if value and Path(value).expanduser().exists():
            return str(Path(value).expanduser())
    for name in ("signalp6", "signalp"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _default_run_dir():
    root = Path.home() / "Downloads" / "ChimeraX" / "SignalP"
    root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="signalp_", dir=str(root)))


def _run_signalp_cli(executable, fasta_path, output_dir, *, organism="other", mode="fast"):
    organism = _normalize_organism(organism)
    mode = _normalize_mode(mode)
    command = [
        str(executable),
        "--fastafile",
        str(fasta_path),
        "--organism",
        organism,
        "--output_dir",
        str(output_dir),
        "--format",
        "txt",
        "--mode",
        mode,
    ]
    model_dir = os.environ.get("SIGNALP_MODEL_DIR")
    if model_dir:
        command.extend(["--model_dir", model_dir])
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=1800,
            env=_clean_subprocess_env(),
        )
    except Exception as err:
        return False, str(err)
    if result.returncode == 0:
        return True, " ".join(shlex.quote(part) for part in command)
    detail = (result.stderr or result.stdout or "").strip()
    return False, detail or f"SignalP exited with code {result.returncode}"


def _normalize_organism(value):
    text = str(value or "other").strip().lower()
    if text in {"euk", "eukaryote", "eukaryotes", "eukarya"}:
        return "eukarya"
    return "other"


def _normalize_mode(value):
    text = str(value or "fast").strip().lower()
    if text in {"slow", "slow-sequential", "slow_sequential"}:
        return "slow-sequential"
    return "fast"


def _clean_subprocess_env():
    env = dict(os.environ)
    for key in (
        "PYTHONHOME",
        "PYTHONPATH",
        "PYTHONNOUSERSITE",
        "PYTHONSTARTUP",
        "PYTHONEXECUTABLE",
        "PYTHONUSERBASE",
    ):
        env.pop(key, None)
    return env


def _copy_text_to_clipboard(text):
    if sys.platform == "darwin":
        try:
            subprocess.run(["pbcopy"], input=str(text).encode("utf-8"), check=False, timeout=5)
        except Exception:
            pass


def _launch_with_browser_helper(sites, fasta="", **extra):
    try:
        from .toolbar_actions import _launch_with_browser_helper as launch

        return launch(sites, fasta, **extra)
    except Exception as err:
        return str(err)


def _generic_sequence_fill_js(sequence_text):
    value = json.dumps(sequence_text)
    return f"""
(() => {{
  const value = {value};
  const fields = Array.from(document.querySelectorAll('textarea,input[type="text"],input:not([type]),[contenteditable="true"]'));
  const visible = fields.filter(el => {{
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }});
  const target = visible.find(el => (el.name || el.id || el.placeholder || '').toLowerCase().includes('seq')) || visible[0];
  if (!target) return 'no-field';
  target.scrollIntoView({{block: 'center'}});
  target.focus();
  if (target.isContentEditable) target.textContent = value;
  else target.value = value;
  target.dispatchEvent(new Event('input', {{bubbles: true}}));
  target.dispatchEvent(new Event('change', {{bubbles: true}}));
  return 'filled';
}})();
""".strip()


def _run_safari_prefill(url, js_code):
    try:
        from .toolbar_actions import _run_safari_prefill as safari

        return safari(url, js_code)
    except Exception as err:
        webbrowser.open(url)
        return False, str(err)


def _run_chimerax(session, command, executor=None):
    if executor is not None:
        return executor(command)
    from chimerax.core.commands import run

    return run(session, command)


def _parse_gff_attrs(attrs):
    result = {}
    for part in str(attrs or "").split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        result[key.strip()] = value.replace("%20", " ").strip()
    return result


def _split_tokens(text):
    try:
        return shlex.split(str(text or ""))
    except Exception:
        return str(text or "").split()


def _parse_options(tokens):
    options = {}
    positional = []
    for token in tokens:
        if "=" in token:
            key, value = token.split("=", 1)
            options[key.strip().lstrip("-").lower()] = value.strip()
        else:
            positional.append(token)
    if positional:
        options["_arg"] = " ".join(positional)
        if len(positional) == 1 and (Path(positional[0]).exists() or "/" in positional[0]):
            options["path"] = positional[0]
    return options


def _truthy_false(value):
    return str(value or "").strip().lower() in {"0", "false", "no", "off", "none"}


def _first_protease_motif_end(sequence, *, min_offset=30):
    seq = str(sequence or "").upper()
    patterns = (
        r"R..R",
        r"[KR][^P][KR]R",
        r"[KR]R",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, seq):
            if match.end() >= min_offset:
                return match.end()
    return None


def _fraction(sequence, alphabet):
    seq = str(sequence or "").upper()
    if not seq:
        return 0.0
    return sum(1 for aa in seq if aa in alphabet) / float(len(seq))


def _shannon_entropy(sequence):
    seq = str(sequence or "")
    if not seq:
        return 0.0
    counts = {}
    for aa in seq:
        counts[aa] = counts.get(aa, 0) + 1
    total = float(len(seq))
    entropy = 0.0
    for count in counts.values():
        p = count / total
        entropy -= p * math.log(p, 2)
    return entropy


def _group_token(entry):
    return re.sub(r"[^A-Za-z0-9_]+", "_", entry["signalp_id"]).strip("_").lower()
