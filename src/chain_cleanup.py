import re


def parse_chain_spec(spec):
    """Return (model_spec, chain_id) for a simple ChimeraX chain spec."""
    text = str(spec or "").strip()
    text = text.split()[0] if text else ""
    text = text.split(":", 1)[0]
    match = re.match(r"^(#\d+(?:\.\d+)*)(?:/([^/:]+))?$", text)
    if not match:
        return None, None
    return match.group(1), match.group(2)


def parse_model_spec(spec):
    text = str(spec or "").strip()
    text = text.split()[0] if text else ""
    match = re.match(r"^(#\d+(?:\.\d+)*)", text)
    return match.group(1) if match else None


def is_chain_spec(spec):
    model_spec, chain_id = parse_chain_spec(spec)
    return bool(model_spec and chain_id)


def find_model_by_spec(session, model_spec):
    target = str(model_spec or "").strip().lstrip("#")
    if not target:
        return None
    try:
        from chimerax.atomic import AtomicStructure
        models = session.models.list(type=AtomicStructure)
    except Exception:
        models = session.models.list()
    for model in models:
        if str(getattr(model, "id_string", "") or "") == target:
            return model
    return None


def solvent_atoms(session, model_spec=None):
    """Return solvent atoms, optionally scoped to one atomic model."""
    atom_sets = []
    if model_spec:
        models = [find_model_by_spec(session, model_spec)]
    else:
        try:
            from chimerax.atomic import AtomicStructure

            models = session.models.list(type=AtomicStructure)
        except Exception:
            models = session.models.list()
    for model in models:
        if model is None or not hasattr(model, "atoms"):
            continue
        atoms = getattr(model, "atoms", None)
        if atoms is None or len(atoms) == 0:
            continue
        try:
            atom_sets.append(atoms.filter(atoms.structure_categories == "solvent"))
        except Exception:
            continue
    atom_sets = [atoms for atoms in atom_sets if atoms is not None and len(atoms)]
    if not atom_sets:
        return None
    if len(atom_sets) == 1:
        return atom_sets[0]
    from chimerax.atomic import Atoms, concatenate

    return concatenate(atom_sets, Atoms, remove_duplicates=True)


def target_model_spec(spec):
    return parse_model_spec(spec)


def hide_solvent(session, model_spec=None):
    atoms = solvent_atoms(session, model_spec=model_spec)
    scope = model_spec or "all models"
    if atoms is None or len(atoms) == 0:
        return f"No solvent atoms found in {scope}."
    atoms.displays = False
    return f"Hid {len(atoms)} solvent atom(s) in {scope}."


def show_solvent(session, model_spec=None):
    atoms = solvent_atoms(session, model_spec=model_spec)
    scope = model_spec or "all models"
    if atoms is None or len(atoms) == 0:
        return f"No solvent atoms found in {scope}."
    atoms.displays = True
    return f"Showed {len(atoms)} solvent atom(s) in {scope}."


def delete_solvent(session, model_spec=None):
    atoms = solvent_atoms(session, model_spec=model_spec)
    scope = model_spec or "all models"
    if atoms is None or len(atoms) == 0:
        return f"No solvent atoms found in {scope}."
    atom_count = len(atoms)
    atoms.delete()
    _invalidate_semantics(session)
    return f"Deleted {atom_count} solvent atom(s) in {scope}."


def keep_only_chain(session, chain_spec):
    """Delete all atoms in the model except atoms belonging to chain_spec."""
    model_spec, keep_chain = parse_chain_spec(chain_spec)
    if not model_spec or not keep_chain:
        raise ValueError(f"Expected a chain spec like #1/A, got {chain_spec!r}")

    model = find_model_by_spec(session, model_spec)
    if model is None:
        raise ValueError(f"Could not find model {model_spec}")

    chains = list(getattr(model, "chains", []) or [])
    if not chains:
        raise ValueError(f"{model_spec} has no chains")

    chain_ids = [str(getattr(chain, "chain_id", "") or "?") for chain in chains]
    if keep_chain not in chain_ids:
        raise ValueError(f"{model_spec} has no chain {keep_chain}; chains: {', '.join(chain_ids)}")

    atom_sets = []
    removed_chains = []
    for chain in chains:
        chain_id = str(getattr(chain, "chain_id", "") or "?")
        if chain_id == keep_chain:
            continue
        try:
            atoms = chain.existing_residues.atoms
        except Exception:
            atoms = None
        if atoms is None or len(atoms) == 0:
            continue
        atom_sets.append(atoms)
        removed_chains.append(chain_id)

    if not atom_sets:
        return f"{model_spec}/{keep_chain}: no other chain atoms to delete."

    from chimerax.atomic import Atoms, concatenate

    atoms = concatenate(atom_sets, Atoms, remove_duplicates=True)
    atom_count = len(atoms)
    atoms.delete()
    _invalidate_semantics(session)
    removed = ", ".join(removed_chains)
    return f"Kept {model_spec}/{keep_chain}; deleted {atom_count} atom(s) from chain(s) {removed}."


def delete_atomspec(session, spec):
    from chimerax.core.commands import run

    target = str(spec or "").strip()
    if not target:
        raise ValueError("No atomspec to delete.")
    run(session, f"delete {target}")
    _invalidate_semantics(session)
    return f"Deleted atoms: {target}"


def _invalidate_semantics(session):
    try:
        from .semantic import invalidate_semantic_cache

        invalidate_semantic_cache(session)
    except Exception:
        pass
