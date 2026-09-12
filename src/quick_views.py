"""Local, scoped View/Figure/Zoom actions for the quick toolbar.

Only ``apply`` touches ChimeraX.  The worker-side planning uses copied scene
coordinates, so fitting a rotated model does not require any model movement.
"""

import numpy as np


def _checkpoint(progress, cancelled, message):
    if cancelled is not None and cancelled():
        raise InterruptedError("Quick action cancelled")
    if progress is not None:
        progress(message)


def _records(snapshot):
    """Normalize missing fields without assigning unknown residues a function."""
    records = []
    for model in snapshot.get("models", ()):
        coords = np.asarray(model.get("coords", ()), dtype=np.float64).reshape((-1, 3))
        count = len(coords)
        elements = np.asarray(model.get("elements", np.zeros(count)), dtype=np.int32)
        residue_index = np.asarray(model.get("residue_index", np.full(count, -1)), dtype=np.int32)
        finite = np.isfinite(coords).all(axis=1)
        heavy = finite & (elements != 1)
        records.append((model, coords, elements, residue_index, heavy))
    return records


def _bounds(coords, minimum=12.0):
    if len(coords) == 0:
        return None
    low, high = coords.min(axis=0), coords.max(axis=0)
    center = (low + high) * 0.5
    half = np.maximum((high - low) * 0.5 + 1.5, minimum * 0.5)
    return [(center - half).tolist(), (center + half).tolist()]


def _scope_parts(records):
    selected, polymer, ligands, ions, solvent = [], [], [], [], []
    for model, *_ in records:
        for residue in model.get("residues", ()):
            spec = residue.get("spec")
            if not spec:
                continue
            if residue.get("selected", False):
                selected.append(spec)
            if residue.get("polymer") in ("protein", "nucleic"):
                polymer.append(spec)
            elif residue.get("category") == "ligand":
                ligands.append(spec)
            elif residue.get("category") == "ions":
                ions.append(spec)
            elif residue.get("category") == "solvent":
                solvent.append(spec)
    return selected, polymer, ligands, ions, solvent


def _coords_for_specs(records, specs):
    wanted = set(specs)
    pieces = []
    for model, coords, _, ri, heavy in records:
        residue_ids = [i for i, residue in enumerate(model.get("residues", ()))
                       if residue.get("spec") in wanted]
        if residue_ids:
            pieces.append(coords[heavy & np.isin(ri, residue_ids)])
    return np.concatenate(pieces) if pieces else np.empty((0, 3))


def _candidate(identifier, label, specs, evidence, records, source, **extra):
    coords = _coords_for_specs(records, specs)
    return dict(id=identifier, label=label, specs=list(specs), evidence=evidence,
                source=source, bounds=_bounds(coords), atom_count=int(len(coords)), **extra)


def _neighbor_indices(points, queries, cutoff, cancelled, tree=None):
    """Spatial query with a bounded-memory fallback for minimal installations."""
    if not len(points) or not len(queries):
        return np.empty(0, dtype=np.int32)
    if tree is None:
        hits = np.zeros(len(points), dtype=bool)
        for begin in range(0, len(points), 2048):
            if cancelled is not None and cancelled():
                raise InterruptedError("Quick action cancelled")
            block = points[begin:begin + 2048]
            mask = np.zeros(len(block), dtype=bool)
            for start in range(0, len(queries), 64):
                delta = block[:, None, :] - queries[None, start:start + 64, :]
                mask |= (np.einsum("ijk,ijk->ij", delta, delta) <= cutoff * cutoff).any(axis=1)
            hits[begin:begin + len(block)] = mask
        return np.flatnonzero(hits)
    hits = set()
    for start in range(0, len(queries), 128):
        if cancelled is not None and cancelled():
            raise InterruptedError("Quick action cancelled")
        for indices in tree.query_ball_point(queries[start:start + 128], cutoff):
            hits.update(indices)
    return np.asarray(sorted(hits), dtype=np.int32)


def _site_candidates(records, cancelled):
    # Polymer atoms from all target models allow a ligand on a separate selected
    # model to have a meaningful neighborhood. Unrelated models are never read.
    points, point_residues = [], []
    residue_lookup, observed = [], []
    for model, coords, _, ri, heavy in records:
        residues = model.get("residues", ())
        offset = len(residue_lookup)
        residue_lookup.extend(residues)
        polymer_ids = [i for i, r in enumerate(residues) if r.get("polymer") in ("protein", "nucleic")]
        mask = heavy & np.isin(ri, polymer_ids)
        points.append(coords[mask])
        point_residues.append(ri[mask] + offset)
        for i, residue in enumerate(residues):
            if (residue.get("category") in ("ligand", "ions")
                    and residue.get("polymer") not in ("protein", "nucleic")
                    and residue.get("spec")):
                observed.append((residue, coords[heavy & (ri == i)]))
    if not observed:
        return []
    polymer_coords = np.concatenate(points) if points else np.empty((0, 3))
    polymer_residues = np.concatenate(point_residues) if point_residues else np.empty(0, dtype=np.int32)
    try:
        from scipy.spatial import cKDTree
    except ImportError:
        tree = None
    else:
        tree = cKDTree(polymer_coords) if len(polymer_coords) else None
    candidates = []
    for residue, ligand_coords in observed:
        if cancelled is not None and cancelled():
            raise InterruptedError("Quick action cancelled")
        if not len(ligand_coords):
            continue
        is_ion = residue.get("category") == "ions"
        cutoff = 3.5 if is_ion else 5.0
        nearby_atoms = _neighbor_indices(polymer_coords, ligand_coords, cutoff, cancelled, tree)
        nearby_ids = np.unique(polymer_residues[nearby_atoms])
        neighbors = [residue_lookup[i]["spec"] for i in nearby_ids if residue_lookup[i].get("spec")]
        spec = residue["spec"]
        kind = "Ion" if is_ion else "Ligand"
        name = residue.get("name", "Unknown")
        label = f"{name} {spec} · {len(neighbors)} nearby residues"
        evidence = [f"{kind} {name} is present in the loaded coordinates.",
                    f"{len(neighbors)} polymer residues have a heavy atom within {cutoff:g} Å.",
                    "Proximity describes observed geometry; biological binding or catalytic function is unverified."]
        candidate = dict(id=f"site:{spec}", label=label, specs=[spec, *neighbors], evidence=evidence,
                         source="observed-ion" if is_ion else "observed-ligand",
                         contact_count=len(neighbors), cutoff=cutoff,
                         anchor_specs=[spec], ligand_atom_count=int(len(ligand_coords)))
        candidates.append(candidate)
    # Contacting observed ligands lead; isolated buffer molecules and ions follow.
    candidates.sort(key=lambda c: (c["contact_count"] == 0, c["source"] == "observed-ion",
                                    -c["contact_count"], -c["ligand_atom_count"], c["id"]))
    # Bounds require scanning complete residue atoms. Do that only for the
    # ranked choices shown in the UI, not for every crystallization additive.
    choices = candidates[:8]
    for candidate in choices:
        if cancelled is not None and cancelled():
            raise InterruptedError("Quick action cancelled")
        coords = _coords_for_specs(records, candidate["specs"])
        candidate.update(bounds=_bounds(coords), atom_count=int(len(coords)))
    return choices


def compute(snapshot, action, progress=None, cancelled=None):
    """Return a plain, deterministic plan, with observed evidence distinguished."""
    if action not in ("view", "figure", "zoom"):
        raise ValueError(f"Unsupported quick view action: {action}")
    _checkpoint(progress, cancelled, "Reading the target structure")
    records = _records(snapshot)
    if not records or not any(len(record[1]) for record in records):
        raise ValueError("Open an atomic structure before using this action.")
    selected, polymer, ligands, ions, solvent = _scope_parts(records)
    target = str(snapshot.get("target_label") or "Target structure")
    metrics = [{"label": "Target", "value": target},
               {"label": "Models", "value": str(len(records))},
               {"label": "Polymer residues", "value": str(len(polymer))},
               {"label": "Ligands / ions", "value": f"{len(ligands)} / {len(ions)}"},
               {"label": "Selected residues", "value": str(len(selected))}]
    details = [f"Scope: {target}.", "Existing atom and chain colors are retained.",
               "Coordinates, user annotations and unrelated model representations are retained."]
    result = dict(action=action, title={"view": "Clean structure view", "figure": "Figure style", "zoom": "Context zoom"}[action],
                  summary=[], details=details, metrics=metrics, candidates=[])
    if action in ("view", "figure"):
        result["summary"] = ["Protein cartoons, nucleic bases and observed ligands/ions are made legible.",
                             "The current camera framing and selected residues are retained."]
        details.extend([f"Hide unselected solvent ({len(solvent)} residues) and hydrogens; retain selected solvent.",
                        "Hide molecular surfaces owned entirely by the target so the structure remains visible.",
                        "Display observed ligands and ions without assigning them a biological role."])
        if action == "figure":
            result["summary"][0] = "A clear figure style is applied with a white background and fine outlines."
            result["summary"].append("Selection outlines are hidden; the selected residues remain selected.")
            details.extend(["Scene-wide change: white background, shadow-free lighting, depth cue off and 1 px silhouettes.",
                            "Scene-wide change: selection outline width is set to 0 for a clean figure; Undo restores it.",
                            "Shadow-free lighting keeps interaction responsive; export resolution is set in Image Export.",
                            "This action styles the scene; it does not save an image file."])
        _checkpoint(progress, cancelled, "The display plan is ready")
        return result

    _checkpoint(progress, cancelled, "Finding a supported focus region")
    candidates = []
    if selected:
        selection = _candidate("selection", f"Selection · {len(selected)} residues", selected,
                               ["The current residue selection has first priority.",
                                "The focus includes the heavy atoms of these residues, not only their selected atoms."],
                               records, "selection")
        if selection["bounds"] is not None:
            candidates.append(selection)
    # Avoid a redundant whole-scene neighbor search for the common selection case.
    sites = [] if candidates else _site_candidates(records, cancelled)
    candidates.extend(sites)
    overview_specs = [r["spec"] for model, *_ in records for r in model.get("residues", ())
                      if r.get("spec") and (r.get("polymer") in ("protein", "nucleic")
                                           or r.get("category") != "solvent")]
    if not overview_specs:
        overview_specs = [r["spec"] for model, *_ in records for r in model.get("residues", ()) if r.get("spec")]
    overview = _candidate("overview", "Target overview", overview_specs,
                          ["Fit the loaded target coordinates.",
                           "Functional-site evidence is unavailable from this quick local action."], records, "overview")
    if overview["bounds"] is not None:
        candidates.append(overview)
    if not candidates:
        raise ValueError("The target contains no finite atomic coordinates to fit.")
    focus = candidates[0]
    result["candidates"] = candidates
    if focus["source"] == "selection":
        result["summary"] = [f"Focus on {len(selected)} selected residues."]
    elif focus["source"].startswith("observed-"):
        result["summary"] = [f"Focus on {focus['label']}.",
                             "This is an observed neighborhood; catalytic function has not been established."]
    else:
        result["summary"] = ["Show a target overview.",
                             "No selection or usable observed ligand/ion coordinates are available for a local site focus."]
    result["details"].extend(["Keep the current viewing direction; fit the focus and set its rotation center.",
                              "Near/far clipping is cleared so the focused atoms are visible.",
                              "Target molecular surfaces are hidden to expose the focus; maps and annotations remain visible.",
                              "No catalytic residues are inferred from generic residue identities."])
    result["metrics"].append({"label": "Focus", "value": focus["label"]})
    _checkpoint(progress, cancelled, "The focus region is ready")
    return result


def _tidy_target(context):
    from chimerax.atomic import Atom

    snapshot_models = {m["spec"]: m for m in context["snapshot"].get("models", ())}
    target_models = tuple(context.get("models", ()))
    for spec, model in context.get("model_by_spec", {}).items():
        saved = snapshot_models.get(spec)
        if saved is None or model not in target_models:
            continue
        model.display = True
        atoms = model.atoms
        residues = model.residues
        ri = np.asarray(saved["residue_index"], dtype=np.int32)
        elements = np.asarray(saved.get("elements", np.zeros(len(atoms))))
        visible = np.zeros(len(residues), dtype=bool)
        ribbon = np.zeros(len(residues), dtype=bool)
        sphere = np.zeros(len(residues), dtype=bool)
        # A lone amino acid or a fragment without CA atoms cannot draw a useful
        # cartoon. Keep those atoms visible rather than producing a blank view.
        ca_chains = {}
        atom_names = np.asarray(saved.get("atom_names", ()))
        if len(atom_names) == len(ri):
            for index in np.unique(ri[atom_names == "CA"]):
                if 0 <= index < len(saved.get("residues", ())):
                    row = saved["residues"][index]
                    if row.get("polymer") == "protein":
                        ca_chains.setdefault(row.get("chain", ""), []).append(int(index))
        cartoon_protein = {index for indices in ca_chains.values() if len(indices) >= 2 for index in indices}
        for i, residue in enumerate(saved.get("residues", ())):
            polymer = residue.get("polymer")
            category = residue.get("category", "other")
            ribbon[i] = polymer == "nucleic" or (polymer == "protein" and i in cartoon_protein)
            visible[i] = ((polymer != "protein" and category != "solvent")
                          or (polymer == "protein" and i not in cartoon_protein)
                          or residue.get("selected", False))
            sphere[i] = category == "ions" and polymer not in ("protein", "nucleic")
        valid = (ri >= 0) & (ri < len(residues))
        display = atoms.displays.copy()
        display[valid] = visible[ri[valid]] & (elements[valid] != 1)
        atoms.displays = display
        # Respect other hide flags (for example a user's nucleotide display mode).
        modes = atoms.draw_modes.copy()
        modes[valid & display] = Atom.STICK_STYLE
        ion_atoms = np.zeros(len(atoms), dtype=bool)
        ion_atoms[valid] = sphere[ri[valid]]
        modes[valid & display & ion_atoms] = Atom.SPHERE_STYLE
        atoms.draw_modes = modes
        residues.ribbon_displays = ribbon
    _hide_target_surfaces(context)


def _hide_target_surfaces(context):
    from chimerax.atomic import MolecularSurface

    target_models = tuple(context.get("models", ()))
    target_set = set(target_models)
    # Hide only molecular surfaces whose complete atom ownership is in scope.
    # Maps, marker surfaces and annotations must remain exactly as they were.
    for model in target_models:
        for child in model.all_models():
            if isinstance(child, MolecularSurface):
                owners = set(child.atoms.unique_structures)
                if owners and owners.issubset(target_set):
                    child.display = False


def _show_focus(context, specs):
    from chimerax.atomic import Atom

    wanted = set(specs)
    for saved in context["snapshot"].get("models", ()):
        model = context.get("model_by_spec", {}).get(saved.get("spec"))
        if model is None or model not in context.get("models", ()):
            continue
        residue_ids = [i for i, residue in enumerate(saved.get("residues", ()))
                       if residue.get("spec") in wanted]
        if not residue_ids:
            continue
        model.display = True
        atoms = model.atoms
        ri = np.asarray(saved["residue_index"])
        elements = np.asarray(saved.get("elements", np.zeros(len(atoms))))
        focus = np.isin(ri, residue_ids) & (elements != 1)
        shown = atoms.displays.copy()
        shown[focus] = True
        atoms.displays = shown
        modes = atoms.draw_modes.copy()
        modes[focus] = Atom.STICK_STYLE
        ion_ids = [i for i in residue_ids if saved["residues"][i].get("category") == "ions"]
        modes[focus & np.isin(ri, ion_ids)] = Atom.SPHERE_STYLE
        atoms.draw_modes = modes


def apply(session, result, context, candidate=0):
    """Apply on the GUI thread. Root owns validity checks and a single undo."""
    action = result.get("action")
    if action in ("view", "figure"):
        _tidy_target(context)
        if action == "figure":
            from chimerax.core.commands import run
            # Native commands flag graphics updates and are covered by root undo.
            run(session, "set bgColor white", log=False)
            run(session, "lighting simple depthCue false", log=False)
            run(session, "graphics silhouettes true width 1 color #454d59", log=False)
            run(session, "graphics selection width 0", log=False)
        return " ".join(result["summary"])
    if action != "zoom":
        raise ValueError(f"Unsupported quick view action: {action}")
    candidates = result.get("candidates", ())
    if not 0 <= int(candidate) < len(candidates):
        raise ValueError("The requested focus region is unavailable.")
    focus = candidates[int(candidate)]
    from chimerax.geometry import Bounds
    bounds = focus.get("bounds")
    if bounds is None:
        raise ValueError("The focus region contains no finite coordinates.")
    # Overview keeps a legible scaffold instead of expanding every sidechain.
    if focus["source"] == "overview":
        _tidy_target(context)
    else:
        _show_focus(context, focus.get("specs", ()))
        _hide_target_surfaces(context)
    view = session.main_view
    fit_bounds = Bounds(np.asarray(bounds[0]), np.asarray(bounds[1]))
    view.view_all(fit_bounds, pad=0.12)
    view.center_of_rotation = fit_bounds.center()
    view.clip_planes.remove_plane("near")
    view.clip_planes.remove_plane("far")
    return f"Focused: {focus['label']}. " + " ".join(focus.get("evidence", ()))
