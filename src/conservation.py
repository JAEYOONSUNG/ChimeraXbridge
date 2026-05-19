import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from difflib import SequenceMatcher
from pathlib import Path


def format_conservation_report(session, model_hint=None, query_text=None, display_limit=10):
    profile = estimate_conservation_profile(session, model_hint=model_hint, query_text=query_text)
    session._codex_bridge_last_conservation_profile = None if profile.get("error") else profile
    if profile.get("error"):
        return f"- {profile['error']}"

    try:
        cap = max(1, min(50, int(display_limit)))
    except Exception:
        cap = 10
    lines = [
        "- ConSurf-lite local conservation analysis",
        f"  - target: {profile['target_chain_spec']} ({profile['target_model_name']})",
        f"  - method: {profile['method']}",
        f"  - aligned sequences: {profile['sequence_count']}",
        f"  - alignment length: {profile['alignment_length']}",
    ]
    accessions = profile.get("accessions", [])
    if accessions:
        lines.append("  - UniProt accessions: " + ", ".join(accessions[:8]))

    top_conserved = profile.get("top_conserved", [])
    if top_conserved:
        lines.append("  - top conserved residues:")
        for item in top_conserved[:cap]:
            lines.append(
                f"    - {item['residue_spec']} {item['aa']} grade {item['grade']} "
                f"(identity {item['identity_fraction']:.2f}, consensus {item['consensus_aa']})"
            )
    else:
        lines.append("  - top conserved residues: none")

    variable = profile.get("top_variable", [])
    if variable:
        lines.append("  - most variable residues:")
        for item in variable[:cap]:
            lines.append(
                f"    - {item['residue_spec']} {item['aa']} grade {item['grade']} "
                f"(identity {item['identity_fraction']:.2f}, consensus {item['consensus_aa']})"
            )
    else:
        lines.append("  - most variable residues: none")

    overlaps = profile.get("feature_overlaps", [])
    if overlaps:
        lines.append("  - conserved residue overlaps with motifs/features:")
        for item in overlaps[:cap]:
            lines.append(
                f"    - {item['residue_spec']} overlaps {item['label']} ({item['kind']})"
            )
    else:
        lines.append("  - conserved residue overlaps with motifs/features: none detected")

    return "\n".join(lines)


def apply_conservation_view(session, model_hint=None, query_text=None, executor=None, *, top_n=18):
    """top_n: how many top conserved + top variable residues to highlight (default 18 each)."""
    from .builtin_actions import _clear_selection, _publication_base_commands, _run, _selection_stick_style_commands

    profile = estimate_conservation_profile(session, model_hint=model_hint, query_text=query_text)
    session._codex_bridge_last_conservation_profile = None if profile.get("error") else profile
    if profile.get("error"):
        return profile["error"]

    target_model_spec = profile["target_model_spec"]
    target_chain_spec = profile["target_chain_spec"]
    try:
        n = max(1, min(50, int(top_n)))
    except Exception:
        n = 18
    high_specs = [item["residue_spec"] for item in profile.get("top_conserved", [])[:n]]
    variable_specs = [item["residue_spec"] for item in profile.get("top_variable", [])[:n]]

    commands = [*_publication_base_commands(target_model_spec), f"view {target_chain_spec}"]
    executed = []
    for command in commands:
        _run(session, command, executor=executor)
        executed.append(command)

    if high_specs:
        for command in (
            "select " + " ".join(high_specs),
            "name frozen conservation_high sel",
            *_selection_stick_style_commands("#355cde"),
            "label sel residues",
        ):
            _run(session, command, executor=executor)
            executed.append(command)
    if variable_specs:
        for command in (
            "select " + " ".join(variable_specs),
            "name frozen conservation_variable sel",
            *_selection_stick_style_commands("#f28f3b"),
        ):
            _run(session, command, executor=executor)
            executed.append(command)

    _clear_selection(session)
    return "\n".join(
        [
            "ConSurf-lite conservation view applied.",
            "Executed ChimeraX commands:",
            *[f"- {command}" for command in executed],
        ]
    )


def estimate_conservation_profile(session, model_hint=None, query_text=None):
    from chimerax.atomic import AtomicStructure
    from chimerax.atomic.structure import uniprot_ids

    target_info = _target_chain_info(session, model_hint=model_hint, query_text=query_text)
    if target_info is None:
        return {"error": "No protein chain with sequence was found for conservation analysis."}

    mafft_path = shutil.which("mafft") or "/usr/local/bin/mafft"
    if not Path(mafft_path).exists():
        return {"error": "MAFFT was not found. Install `mafft` to enable local conservation analysis."}

    target_seq = target_info["sequence"]
    candidates = []
    seen_sequences = set()
    accessions = set(target_info.get("accessions", []))

    for model in session.models.list(type=AtomicStructure):
        model_spec = f"#{getattr(model, 'id_string', '?')}"
        model_name = getattr(model, "name", "structure")
        chain_accessions = {
            str(getattr(useq, "chain_id", "")).strip(): str(getattr(useq, "uniprot_id", "")).strip()
            for useq in uniprot_ids(model)
            if getattr(useq, "uniprot_id", None) and getattr(useq, "chain_id", None)
        }
        for chain in model.chains:
            residues = chain.existing_residues
            numbers = [int(number) for number in residues.numbers]
            sequence_text = (getattr(chain, "characters", "") or "").upper()
            usable_len = min(len(sequence_text), len(numbers))
            if usable_len < 20:
                continue
            sequence_text = sequence_text[:usable_len]
            numbers = numbers[:usable_len]
            if not sequence_text:
                continue
            chain_id = str(chain.chain_id).strip() or "?"
            chain_spec = f"{model_spec}/{chain_id}"
            accession = chain_accessions.get(chain_id)
            if accession:
                accessions.add(accession)

            seq_key = sequence_text
            if seq_key in seen_sequences and chain_spec != target_info["chain_spec"]:
                continue

            similarity = SequenceMatcher(None, target_seq, sequence_text).ratio()
            length_ratio = min(len(sequence_text), len(target_seq)) / max(len(sequence_text), len(target_seq))
            same_accession = bool(accession and accession in target_info.get("accessions", []))
            if chain_spec != target_info["chain_spec"] and not same_accession:
                if length_ratio < 0.55 or similarity < 0.22:
                    continue

            seen_sequences.add(seq_key)
            candidates.append(
                {
                    "id": chain_spec,
                    "model_spec": model_spec,
                    "model_name": model_name,
                    "chain_id": chain_id,
                    "chain_spec": chain_spec,
                    "sequence": sequence_text,
                    "numbers": numbers,
                    "accession": accession,
                    "similarity": similarity,
                }
            )

    candidates.sort(key=lambda item: (0 if item["chain_spec"] == target_info["chain_spec"] else 1, -item["similarity"], item["chain_spec"]))
    if len(candidates) < 2:
        return {"error": "Not enough homolog-like protein chains are available locally. Open related structures/chains or provide more homologs."}

    cache_key = _conservation_cache_key(target_info, candidates)
    cache = getattr(session, "_codex_bridge_conservation_cache", None)
    if cache is None:
        session._codex_bridge_conservation_cache = cache = {}
    if cache_key in cache:
        return dict(cache[cache_key])

    alignment = _run_mafft_alignment(candidates, mafft_path)
    if not alignment:
        return {"error": "MAFFT alignment failed for the selected protein chains."}

    target_alignment = alignment[target_info["chain_spec"]]
    residues = _target_residue_conservation(target_info, target_alignment, alignment)
    if not residues:
        return {"error": "Conservation profile could not be mapped back onto the target chain."}

    from .semantic import get_motif_hits, get_uniprot_feature_entries

    feature_entries = get_uniprot_feature_entries(session, model_hint=target_info["chain_spec"])
    motif_hits = get_motif_hits(session, model_hint=target_info["chain_spec"])
    feature_overlaps = _conservation_feature_overlaps(residues, feature_entries, motif_hits)

    top_conserved = [item for item in residues if item["grade"] >= 8][:18]
    top_variable = sorted(residues, key=lambda item: (item["grade"], item["nongap_count"], item["number"]))[:18]

    profile = {
        "target_model_spec": target_info["model_spec"],
        "target_model_name": target_info["model_name"],
        "target_chain_spec": target_info["chain_spec"],
        "target_chain_id": target_info["chain_id"],
        "sequence_count": len(alignment),
        "alignment_length": len(next(iter(alignment.values()))) if alignment else 0,
        "method": "MAFFT alignment of locally available homolog-like chains; ConSurf-like 1-9 identity grades",
        "residues": residues,
        "top_conserved": top_conserved,
        "top_variable": top_variable,
        "feature_overlaps": feature_overlaps,
        "accessions": sorted(accessions),
    }
    cache[cache_key] = dict(profile)
    if len(cache) > 24:
        oldest_keys = list(cache.keys())[:-24]
        for key in oldest_keys:
            cache.pop(key, None)
    return profile


def _target_chain_info(session, model_hint=None, query_text=None):
    from .semantic import extract_chain_specs_from_text, get_session_semantics, resolve_default_model_spec, resolve_model_spec
    from chimerax.atomic import AtomicStructure
    from chimerax.atomic.structure import uniprot_ids

    explicit_chain_specs = extract_chain_specs_from_text(session, query_text or model_hint or "", model_hint=model_hint)
    target_chain_spec = explicit_chain_specs[0] if explicit_chain_specs else None
    target_model_spec = None
    target_chain_id = None
    if target_chain_spec:
        target_model_spec, _, target_chain_id = target_chain_spec.partition("/")

    semantics = get_session_semantics(session)
    if target_chain_spec is None:
        selection_ranges = list(semantics.get("selection", {}).get("ranges", []))
        if selection_ranges and "/" in selection_ranges[0]:
            target_chain_spec = selection_ranges[0].split(":", 1)[0]
            target_model_spec, _, target_chain_id = target_chain_spec.partition("/")

    if target_model_spec is None:
        target_model_spec = resolve_model_spec(session, model_hint) if model_hint else None
    target_model_spec = target_model_spec or resolve_default_model_spec(session)

    for model in session.models.list(type=AtomicStructure):
        model_spec = f"#{getattr(model, 'id_string', '?')}"
        if target_model_spec and model_spec != target_model_spec:
            continue
        chain_accessions = {
            str(getattr(useq, "chain_id", "")).strip(): str(getattr(useq, "uniprot_id", "")).strip()
            for useq in uniprot_ids(model)
            if getattr(useq, "uniprot_id", None) and getattr(useq, "chain_id", None)
        }
        for chain in model.chains:
            chain_id = str(chain.chain_id).strip() or "?"
            if target_chain_id and chain_id != target_chain_id:
                continue
            residues = chain.existing_residues
            numbers = [int(number) for number in residues.numbers]
            sequence_text = (getattr(chain, "characters", "") or "").upper()
            usable_len = min(len(sequence_text), len(numbers))
            if usable_len < 20:
                continue
            return {
                "model_spec": model_spec,
                "model_name": getattr(model, "name", "structure"),
                "chain_id": chain_id,
                "chain_spec": f"{model_spec}/{chain_id}",
                "sequence": sequence_text[:usable_len],
                "numbers": numbers[:usable_len],
                "accessions": [chain_accessions[chain_id]] if chain_id in chain_accessions else [],
            }
    return None


def _run_mafft_alignment(entries, mafft_path):
    fasta_lines = []
    for entry in entries:
        fasta_lines.append(f">{entry['chain_spec']}")
        fasta_lines.append(entry["sequence"])
    fd, fasta_path = tempfile.mkstemp(prefix="codex-consurf-lite-", suffix=".fa")
    os.close(fd)
    Path(fasta_path).write_text("\n".join(fasta_lines) + "\n", encoding="utf-8")
    try:
        completed = subprocess.run(
            [mafft_path, "--quiet", "--auto", fasta_path],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        if completed.returncode != 0:
            return None
        return _parse_fasta_alignment(completed.stdout)
    finally:
        try:
            Path(fasta_path).unlink()
        except OSError:
            pass


def _parse_fasta_alignment(text):
    alignment = {}
    current_name = None
    chunks = []
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(">"):
            if current_name is not None:
                alignment[current_name] = "".join(chunks)
            current_name = stripped[1:].strip()
            chunks = []
            continue
        chunks.append(stripped)
    if current_name is not None:
        alignment[current_name] = "".join(chunks)
    lengths = {len(value) for value in alignment.values()}
    if not alignment or len(lengths) != 1:
        return None
    return alignment


def _conservation_cache_key(target_info, candidates):
    digest = hashlib.sha1()
    digest.update(str(target_info["chain_spec"]).encode("utf-8"))
    for item in candidates:
        digest.update(str(item["chain_spec"]).encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(item["sequence"]).encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _target_residue_conservation(target_info, target_alignment, alignment):
    residues = []
    residue_numbers = list(target_info["numbers"])
    residue_index = 0
    for column_index, aa in enumerate(target_alignment):
        if aa == "-":
            continue
        if residue_index >= len(residue_numbers):
            break
        column = [seq[column_index] for seq in alignment.values()]
        nongap = [token for token in column if token != "-"]
        if not nongap:
            continue
        same = sum(1 for token in nongap if token == aa)
        identity_fraction = same / max(len(nongap), 1)
        grade = max(1, min(9, int(round(identity_fraction * 8)) + 1))
        consensus_aa = max(set(nongap), key=nongap.count)
        number = residue_numbers[residue_index]
        residues.append(
            {
                "residue_spec": f"{target_info['chain_spec']}:{number}",
                "number": number,
                "aa": aa,
                "identity_fraction": identity_fraction,
                "grade": grade,
                "consensus_aa": consensus_aa,
                "nongap_count": len(nongap),
            }
        )
        residue_index += 1
    residues.sort(key=lambda item: (-item["grade"], -item["identity_fraction"], item["number"]))
    return residues


def _conservation_feature_overlaps(residues, feature_entries, motif_hits):
    top_specs = {item["residue_spec"]: item for item in residues if item["grade"] >= 8}
    overlaps = []
    for entry in feature_entries:
        for residue in residues:
            if residue["grade"] < 8:
                continue
            token = residue["residue_spec"]
            prefix, _, residue_number = token.rpartition(":")
            if prefix != f"{entry['model_spec']}/{entry['chain_id']}":
                continue
            number = int(residue_number)
            if entry["start"] <= number <= entry["end"]:
                overlaps.append(
                    {
                        "residue_spec": token,
                        "kind": "UniProt",
                        "label": f"{entry['feature_type']} [{entry['label']}]",
                    }
                )
    for hit in motif_hits:
        hit_specs = set(hit["residue_specs"])
        for residue_spec in hit_specs:
            if residue_spec in top_specs:
                overlaps.append(
                    {
                        "residue_spec": residue_spec,
                        "kind": "motif",
                        "label": f"{hit['pattern_name']} [{hit['matched_sequence']}]",
                    }
                )
    deduped = []
    seen = set()
    for item in overlaps:
        key = (item["residue_spec"], item["kind"], item["label"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped
