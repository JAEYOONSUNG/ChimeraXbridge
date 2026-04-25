def format_analysis_tool_state(session):
    lines = ["Analysis tool state"]
    blocks = [
        _blast_state_lines(session),
        _alignment_state_lines(session),
        _similar_state_lines(session),
        _conservation_state_lines(session),
        _catalytic_state_lines(session),
        _membrane_state_lines(session),
        _pisa_state_lines(session),
    ]
    added = False
    for block in blocks:
        if not block:
            continue
        lines.extend(block)
        added = True
    if not added:
        lines.append("- No recent analysis-tool state recorded.")
    return "\n".join(lines)


def _blast_state_lines(session):
    try:
        from chimerax.blastprotein.ui.results import BlastProteinResults
    except Exception:
        return []

    last_name = getattr(session, "_codex_bridge_last_blast_name", None)
    tools = [tool for tool in session.tools.list() if isinstance(tool, BlastProteinResults)]
    if not tools:
        return []
    target = None
    if last_name:
        for tool in tools:
            if getattr(tool, "_instance_name", None) == last_name:
                target = tool
                break
    if target is None:
        target = tools[-1]

    params = getattr(target, "params", None)
    hits = list(getattr(target, "_hits", []) or [])
    lines = [
        f"- Blast Protein: {getattr(target, '_instance_name', '(unnamed)')} "
        f"(db {getattr(params, 'database', '?')}, hits {len(hits)})"
    ]
    for hit in hits[:5]:
        name = hit.get("name") or hit.get("id") or hit.get("database_full_id") or "(hit)"
        evalue = hit.get("e-value", hit.get("evalue"))
        score = hit.get("score", hit.get("pident"))
        bits = [str(name)]
        if evalue is not None:
            bits.append(f"E {evalue}")
        if score is not None:
            bits.append(f"score {score}")
        lines.append("  - " + "; ".join(bits))
    return lines


def _alignment_state_lines(session):
    manager = getattr(session, "alignments", None)
    if manager is None:
        return []
    alignments = list(getattr(manager, "alignments", []) or [])
    if not alignments:
        return []
    last_id = getattr(session, "_codex_bridge_last_alignment_id", None)
    target = None
    if last_id:
        for alignment in alignments:
            if getattr(alignment, "ident", None) == last_id:
                target = alignment
                break
    if target is None:
        target = alignments[-1]
    lines = [
        f"- Alignment: {getattr(target, 'ident', '(none)')} "
        f"({len(getattr(target, 'seqs', []) or [])} sequences)"
    ]
    description = str(getattr(target, "description", "") or "").strip()
    if description:
        lines.append("  - " + description)
    return lines


def _similar_state_lines(session):
    manager = getattr(session, "similar_structures", None)
    if manager is None:
        return []
    try:
        names = list(manager.names)
    except Exception:
        names = []
    if not names:
        return []

    target_name = getattr(session, "_codex_bridge_last_similar_name", None)
    try:
        from chimerax.similarstructures.simstruct import similar_structure_results
        results = similar_structure_results(session, target_name, raise_error=False)
    except Exception:
        results = None
    if results is None and names:
        try:
            from chimerax.similarstructures.simstruct import similar_structure_results
            results = similar_structure_results(session, names[-1], raise_error=False)
        except Exception:
            results = None
    if results is None:
        return []

    hits = list(getattr(results, "hits", []) or [])
    lines = [
        f"- Similar Structures: {getattr(results, 'name', '(unnamed)')} "
        f"({len(hits)} hits, program {getattr(results, 'program', '?')})"
    ]
    for hit in hits[:5]:
        name = hit.get("database_full_id") or hit.get("database_id") or "(hit)"
        pident = hit.get("pident")
        evalue = hit.get("evalue")
        bits = [str(name)]
        if pident is not None:
            bits.append(f"id {float(pident):.1f}%")
        if evalue is not None:
            bits.append(f"E {evalue}")
        lines.append("  - " + "; ".join(bits))
    return lines


def _conservation_state_lines(session):
    profile = getattr(session, "_codex_bridge_last_conservation_profile", None)
    if not profile:
        return []
    lines = [
        f"- Conservation: {profile.get('target_chain_spec', '(unknown)')} "
        f"({profile.get('sequence_count', 0)} sequences, alignment {profile.get('alignment_length', 0)})"
    ]
    for item in (profile.get("top_conserved") or [])[:5]:
        lines.append(
            f"  - {item['residue_spec']} grade {item['grade']} "
            f"(identity {item['identity_fraction']:.2f})"
        )
    return lines


def _catalytic_state_lines(session):
    candidates = list(getattr(session, "_codex_bridge_last_catalytic_candidates", []) or [])
    if not candidates:
        return []
    lines = [f"- Catalytic triage: {len(candidates)} ranked candidate(s)"]
    for item in candidates[:5]:
        lines.append(
            f"  - {item['residue_spec']} {item.get('name', '')} "
            f"score {item.get('score', '?')} [{item.get('consensus', 'candidate')}]"
        )
    return lines


def _membrane_state_lines(session):
    segments = list(getattr(session, "_codex_bridge_last_membrane_segments", []) or [])
    membrane_models = list(getattr(session, "_codex_bridge_virtual_membrane_models", []) or [])
    membrane_specs = list(getattr(session, "_codex_bridge_virtual_membrane_specs", []) or [])
    if not segments and not membrane_models and not membrane_specs:
        return []
    slab_count = len(membrane_models) + len(membrane_specs)
    lines = [f"- Membrane: {len(segments)} TM-like segment(s), {slab_count} virtual slab model(s)"]
    for item in segments[:5]:
        lines.append(
            f"  - {item.get('chain_id', '?')}:{item.get('start', '?')}-{item.get('end', '?')} "
            f"{item.get('sse', 'segment')} score {float(item.get('score', 0.0)):.2f}"
        )
    return lines


def _pisa_state_lines(session):
    pairs = list(getattr(session, "_codex_bridge_last_pisa_pairs", []) or [])
    commands = list(getattr(session, "_codex_bridge_last_pisa_commands", []) or [])
    if not pairs and not commands:
        return []
    lines = [f"- PISA-like interfaces: {len(pairs)} ranked pair(s)"]
    for item in pairs[:5]:
        lines.append(
            f"  - {item.get('model_spec', '?')} {item.get('chain_a', '?')}-{item.get('chain_b', '?')}: "
            f"{item.get('contacts_a', 0)}+{item.get('contacts_b', 0)} residues, "
            f"min {float(item.get('min_distance', 0.0)):.2f} A"
        )
    if commands:
        lines.append("  - last command: " + str(commands[-1]))
    return lines
