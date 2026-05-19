"""PISA-like interface analysis helpers for Codex Bridge.

This module keeps the fast path local: it ranks chain-chain contacts from the
open atomic models, then runs ChimeraX's native interface, buried-area, contact,
and hbond commands for the best interface.
"""


PISA_WEB_URL = "https://www.ebi.ac.uk/pdbe/prot_int/"


def format_pisa_report(session, model_hint=None, contact_cutoff=8.0, display_limit=10):
    pairs = _interface_candidates(session, model_hint=model_hint, cutoff=contact_cutoff)
    session._codex_bridge_last_pisa_pairs = pairs
    if not pairs:
        if model_hint:
            return f"- No PISA-like chain-chain interface candidates matched: {model_hint}"
        return "- No PISA-like chain-chain interface candidates detected."
    try:
        cap = max(1, min(50, int(display_limit)))
    except Exception:
        cap = 10

    lines = [
        "- PISA-like interface candidates",
        "  - local ranking uses principal-atom distances; exact buried area is computed by `/pisa view` via ChimeraX `measure buriedarea` and appears in the Log.",
    ]
    current_model = None
    for item in pairs[:cap]:
        model_key = item["model_spec"]
        if model_key != current_model:
            current_model = model_key
            lines.append(f"  - {item['model_spec']} {item['model_name']}")
        score = item["contacts_a"] + item["contacts_b"]
        lines.append(
            f"    - {item['chain_a']}-{item['chain_b']}: min {item['min_distance']:.2f} A, "
            f"{score} contact-like residues ({item['contacts_a']}+{item['contacts_b']}); "
            f"commands: /pisa view {item['model_spec']} or /buriedarea {item['chain_a_spec']} {item['chain_b_spec']}"
        )
    return "\n".join(lines)


def run_pisa_view(session, arg="", executor=None, *, pair_index=1, cutoff=8.0, display_limit=10):
    """Pair_index = which interface candidate to render (1-based, default best/largest).
    cutoff = contact distance Å (default 8.0).
    display_limit = report row cap (1-50, default 10)."""
    action, model_hint = _split_action_arg(arg)
    if action in {"web", "open", "server", "pdbe", "online"}:
        from .toolbar_actions import launch_pisa_server

        return launch_pisa_server(session, executor=executor)
    if action in {"report", "summary", "analyze", "analysis", "list", "정보", "요약"}:
        return format_pisa_report(session, model_hint=model_hint or None,
                                  contact_cutoff=cutoff, display_limit=display_limit)

    pairs = _interface_candidates(session, model_hint=model_hint or None, cutoff=cutoff)
    session._codex_bridge_last_pisa_pairs = pairs
    if not pairs:
        return format_pisa_report(session, model_hint=model_hint or None)

    chosen_idx = max(1, min(int(pair_index), len(pairs))) - 1
    pair = pairs[chosen_idx]
    recolor_chains = not _chains_have_distinct_cartoon_colors(session, pair["chain_a_spec"], pair["chain_b_spec"])
    commands = _pisa_commands_for_pair(pair, action=action, recolor_chains=recolor_chains)
    session._codex_bridge_last_pisa_commands = commands
    failures = _run_commands(session, commands, executor=executor)

    # Surface PISA interface as a CodexNamedSelectionGroup so it appears in the
    # Models panel + benefits from session-state cleanup, mirroring Cavity/Site.
    try:
        from .named_selection import add_group
        chain_a_spec = pair.get("chain_a_spec") or ""
        chain_b_spec = pair.get("chain_b_spec") or ""
        spec_combined = " ".join(filter(None, [chain_a_spec, chain_b_spec]))
        if spec_combined:
            slug = f"pisa_{pair['model_spec'].lstrip('#')}_{pair['chain_a']}{pair['chain_b']}".replace("/", "_").lower()
            add_group(session, slug, spec_combined, color="#9ed5ff")
    except Exception:
        pass

    lines = [
        "PISA-like interface view prepared.",
        f"- selected interface: {pair['model_spec']} chains {pair['chain_a']}-{pair['chain_b']}",
        "- chain colors: preserved from current view" if not recolor_chains else "- chain colors: applied because the two chains were not visually distinct",
        f"- contact-like residues: {pair['contacts_a']}+{pair['contacts_b']}; closest distance {pair['min_distance']:.2f} A",
        "- buried surface area: see the ChimeraX Log entry from `measure buriedarea`",
        "- named selection: pisa_interface",
    ]
    if failures:
        lines.append("- command warnings:")
        lines.extend(f"  - {item}" for item in failures[:4])
    return "\n".join(lines)


def _interface_candidates(session, model_hint=None, cutoff=8.0):
    from chimerax.atomic import AtomicStructure

    from .semantic import get_complex_interfaces, resolve_model_spec

    selected_spec = resolve_model_spec(session, model_hint) if model_hint else None
    candidates = []
    for model in session.models.list(type=AtomicStructure):
        model_spec = f"#{getattr(model, 'id_string', '?')}"
        if selected_spec and model_spec != selected_spec:
            continue
        for item in get_complex_interfaces(model, contact_cutoff=cutoff):
            enriched = dict(item)
            enriched["model_spec"] = model_spec
            enriched["model_name"] = getattr(model, "name", "structure")
            enriched["contact_score"] = int(item["contacts_a"]) + int(item["contacts_b"])
            candidates.append(enriched)
    candidates.sort(key=lambda item: (-item["contact_score"], item["min_distance"], item["model_spec"]))
    return candidates


def _pisa_commands_for_pair(pair, action="view", *, recolor_chains=False):
    chain_a = pair["chain_a_spec"]
    chain_b = pair["chain_b_spec"]
    model_spec = pair["model_spec"]
    action = (action or "view").lower()
    commands = []

    if action in {"view", "show", "select", "measure", "contacts", "all", ""}:
        color_commands = []
        if recolor_chains:
            color_commands = [
                f"color {chain_a} #7aa2f7 target c",
                f"color {chain_b} #f5a65b target c",
            ]
        commands.extend(
            [
                f"interfaces {model_spec}",
                f"interfaces select {chain_a} contacting {chain_b} bothSides true",
                "name frozen pisa_interface sel",
                *color_commands,
                "color sel #ffcc66 target ab",
                "show sel atoms",
                "style sel stick",
                f"measure buriedarea {chain_a} withAtoms2 {chain_b} listResidues true select true",
                f"contacts {chain_a} restrict {chain_b} reveal true",
                f"hbonds {chain_a} restrict {chain_b} reveal true",
            ]
        )
    elif action in {"selection", "interface"}:
        commands.extend(
            [
                f"interfaces select {chain_a} contacting {chain_b} bothSides true",
                "name frozen pisa_interface sel",
                "color sel #ffcc66 target ab",
                "show sel atoms",
                "style sel stick",
            ]
        )
    elif action in {"area", "buried", "buriedarea", "bsa"}:
        commands.append(f"measure buriedarea {chain_a} withAtoms2 {chain_b} listResidues true select true")
    return commands or _pisa_commands_for_pair(pair, action="view", recolor_chains=recolor_chains)


def _chains_have_distinct_cartoon_colors(session, chain_a_spec, chain_b_spec):
    a = _average_ribbon_rgb(session, chain_a_spec)
    b = _average_ribbon_rgb(session, chain_b_spec)
    if a is None or b is None:
        return False
    distance = sum((a[i] - b[i]) ** 2 for i in range(3)) ** 0.5
    return distance >= 35.0


def _average_ribbon_rgb(session, spec):
    try:
        from .display_color import objects_for_spec

        objects = objects_for_spec(session, spec)
        atoms = getattr(objects, "atoms", None)
        if atoms is None or len(atoms) == 0:
            return None
        residues = getattr(atoms, "residues", None)
        colors = getattr(residues, "ribbon_colors", None)
        if colors is None or len(colors) == 0:
            return None
        total = [0.0, 0.0, 0.0]
        count = 0
        for color in colors:
            try:
                total[0] += float(color[0])
                total[1] += float(color[1])
                total[2] += float(color[2])
                count += 1
            except Exception:
                continue
        if count == 0:
            return None
        return tuple(value / count for value in total)
    except Exception:
        return None


def _run_commands(session, commands, executor=None):
    failures = []
    for command in commands:
        try:
            _run(session, command, executor=executor)
            _restore_charge_colors_for_command(session, command)
        except Exception as err:
            detail = str(err).strip() or err.__class__.__name__
            failures.append(f"{command}: {detail}")
    return failures


def _run(session, command, executor=None):
    if executor is not None:
        return executor(command)
    from chimerax.core.commands import run

    return run(session, command)


def _restore_charge_colors_for_command(session, command):
    text = str(command or "").strip()
    lower = text.lower()
    if not lower.startswith("color ") or " byelement" in lower or " byhetero" in lower or " byatom" in lower:
        return
    parts = text.split()
    if len(parts) < 3:
        return
    target = ""
    lowered = [part.lower() for part in parts]
    if "target" in lowered:
        index = lowered.index("target")
        target = lowered[index + 1] if index + 1 < len(lowered) else ""
    if target and "a" not in target:
        return
    from .display_color import restore_charge_colors

    restore_charge_colors(session, parts[1])


def _split_action_arg(arg):
    text = str(arg or "").strip()
    if not text:
        return "view", ""
    head, _, tail = text.partition(" ")
    action = head.strip().lower()
    if action.startswith("#") or "/" in action or ":" in action:
        return "view", text
    return action, tail.strip()
