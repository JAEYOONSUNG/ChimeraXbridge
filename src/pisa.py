"""PISA-like interface analysis helpers for Codex Bridge.

This module keeps the fast path local: it ranks chain-chain contacts from the
open atomic models, then runs ChimeraX's native interface, buried-area, contact,
and hbond commands for the best interface.
"""


PISA_WEB_URL = "https://www.ebi.ac.uk/pdbe/prot_int/"


def format_pisa_report(session, model_hint=None, contact_cutoff=8.0):
    pairs = _interface_candidates(session, model_hint=model_hint, cutoff=contact_cutoff)
    session._codex_bridge_last_pisa_pairs = pairs
    if not pairs:
        if model_hint:
            return f"- No PISA-like chain-chain interface candidates matched: {model_hint}"
        return "- No PISA-like chain-chain interface candidates detected."

    lines = [
        "- PISA-like interface candidates",
        "  - local ranking uses principal-atom distances; exact buried area is computed by `/pisa view` via ChimeraX `measure buriedarea` and appears in the Log.",
    ]
    current_model = None
    for item in pairs[:10]:
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


def run_pisa_view(session, arg="", executor=None):
    action, model_hint = _split_action_arg(arg)
    if action in {"web", "open", "server", "pdbe", "online"}:
        from .toolbar_actions import launch_pisa_server

        return launch_pisa_server(session, executor=executor)
    if action in {"report", "summary", "analyze", "analysis", "list", "정보", "요약"}:
        return format_pisa_report(session, model_hint=model_hint or None)

    pairs = _interface_candidates(session, model_hint=model_hint or None)
    session._codex_bridge_last_pisa_pairs = pairs
    if not pairs:
        return format_pisa_report(session, model_hint=model_hint or None)

    pair = pairs[0]
    commands = _pisa_commands_for_pair(pair, action=action)
    session._codex_bridge_last_pisa_commands = commands
    failures = _run_commands(session, commands, executor=executor)

    lines = [
        "PISA-like interface view prepared.",
        f"- selected interface: {pair['model_spec']} chains {pair['chain_a']}-{pair['chain_b']}",
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


def _pisa_commands_for_pair(pair, action="view"):
    chain_a = pair["chain_a_spec"]
    chain_b = pair["chain_b_spec"]
    model_spec = pair["model_spec"]
    action = (action or "view").lower()
    commands = []

    if action in {"view", "show", "select", "measure", "contacts", "all", ""}:
        commands.extend(
            [
                f"interfaces {model_spec}",
                f"interfaces select {chain_a} contacting {chain_b} bothSides true",
                "name frozen pisa_interface sel",
                f"color {model_spec} #d9dde2",
                f"color {chain_a} #7aa2f7",
                f"color {chain_b} #f5a65b",
                "color sel #ffcc66",
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
                "color sel #ffcc66",
                "show sel atoms",
                "style sel stick",
            ]
        )
    elif action in {"area", "buried", "buriedarea", "bsa"}:
        commands.append(f"measure buriedarea {chain_a} withAtoms2 {chain_b} listResidues true select true")
    return commands or _pisa_commands_for_pair(pair, action="view")


def _run_commands(session, commands, executor=None):
    failures = []
    for command in commands:
        try:
            _run(session, command, executor=executor)
        except Exception as err:
            detail = str(err).strip() or err.__class__.__name__
            failures.append(f"{command}: {detail}")
    return failures


def _run(session, command, executor=None):
    if executor is not None:
        return executor(command)
    from chimerax.core.commands import run

    return run(session, command)


def _split_action_arg(arg):
    text = str(arg or "").strip()
    if not text:
        return "view", ""
    head, _, tail = text.partition(" ")
    action = head.strip().lower()
    if action.startswith("#") or "/" in action or ":" in action:
        return "view", text
    return action, tail.strip()
