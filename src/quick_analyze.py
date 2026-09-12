"""Local, evidence-based quick analysis.  Only ``apply`` accesses ChimeraX.

Distances describe the loaded coordinates, including their current scene
transforms.  They do not establish binding, catalysis, or biological assembly.
"""
from collections import Counter
from concurrent.futures import CancelledError

import numpy as np


CONTACT_CUTOFF = 4.0
METAL_CUTOFF = 3.2
MAX_LABELS = 6
_METALS = frozenset((3, 4, 11, 12, 13, 19, 20, *range(21, 32), 37, 38,
                     *range(39, 51), 55, 56, *range(57, 85), 87, 88,
                     *range(89, 113)))
_WATERS = frozenset(("HOH", "WAT", "DOD", "H2O"))


def _check(cancelled):
    if cancelled is not None and cancelled():
        raise CancelledError("Quick analysis cancelled")


def _emit(progress, message):
    if progress is not None:
        progress(message)


def _column(model, name, count, default, dtype=float):
    value = model.get(name)
    if value is None:
        return np.full(count, default, dtype=dtype)
    array = np.asarray(value, dtype=dtype)
    if array.shape != (count,):
        return np.full(count, default, dtype=dtype)
    return array


def _flatten(snapshot, cancelled):
    """Keep snapshot indices while making one scene-coordinate spatial index."""
    coords, elements, owners, bfactors, occupancies, records = [], [], [], [], [], []
    for model in snapshot.get("models", ()):
        _check(cancelled)
        xyz = np.asarray(model.get("coords", ()), dtype=float).reshape((-1, 3))
        n = len(xyz)
        offset = len(records)
        for index, residue in enumerate(model.get("residues", ())):
            record = dict(residue)
            record.update(model_spec=str(model.get("spec", "")), residue_index=index)
            record.setdefault("name", "UNK")
            record.setdefault("chain", "")
            record.setdefault("number", "?")
            record.setdefault("spec", "")
            record.setdefault("polymer", "other")
            record.setdefault("category", "other")
            records.append(record)
        ri = _column(model, "residue_index", n, -1, np.int64)
        ri = np.where((ri >= 0) & (ri < len(records) - offset), ri + offset, -1)
        coords.append(xyz)
        elements.append(_column(model, "elements", n, 0, np.int16))
        owners.append(ri)
        bfactors.append(_column(model, "bfactors", n, np.nan))
        occupancies.append(_column(model, "occupancies", n, np.nan))
    return (np.concatenate(coords) if coords else np.empty((0, 3)),
            np.concatenate(elements) if elements else np.empty(0, int),
            np.concatenate(owners) if owners else np.empty(0, int),
            np.concatenate(bfactors) if bfactors else np.empty(0),
            np.concatenate(occupancies) if occupancies else np.empty(0), records)


def _ref(residue, distance=None):
    result = {"model_spec": residue["model_spec"],
              "residue_index": int(residue["residue_index"]),
              "spec": str(residue["spec"]),
              "label": "%s %s %s" % (residue["name"], residue["chain"] or "—", residue["number"])}
    if distance is not None:
        result["distance"] = float(distance)
    return result


def _neighbors(query, tree, polymer_xyz, polymer_owner, excluded, cutoff, cancelled):
    """Exact heavy-atom minima; bounded chunks avoid an all-by-all matrix."""
    distances = {}
    for start in range(0, len(query), 128):
        _check(cancelled)
        chunk = query[start:start + 128]
        hits = tree.query_ball_point(chunk, cutoff)
        lengths = np.fromiter((len(hit) for hit in hits), dtype=int, count=len(hits))
        if not lengths.sum():
            continue
        indices = np.concatenate([np.asarray(hit, dtype=int) for hit in hits])
        queried = np.repeat(chunk, lengths, axis=0)
        d = np.linalg.norm(polymer_xyz[indices] - queried, axis=1)
        residues = polymer_owner[indices]
        for owner in np.unique(residues):
            key = int(owner)
            if key not in excluded:
                value = float(np.min(d[residues == owner]))
                distances[key] = min(distances.get(key, float("inf")), value)
    return sorted(distances.items(), key=lambda item: (item[1], item[0]))


def _candidate(kind, anchor_ids, neighbors, records, cutoff, label, identifier):
    anchors = [_ref(records[index]) for index in anchor_ids]
    near = [_ref(records[index], distance) for index, distance in neighbors]
    closest = near[0]["distance"] if near else None
    evidence = ["%d polymer residues within %.1f Å (minimum heavy-atom distance)." % (len(near), cutoff)]
    if closest is not None:
        evidence.append("Nearest: %s, %.2f Å." % (near[0]["label"], closest))
    evidence.append("Observed proximity; binding and catalytic function are not established.")
    return {"id": identifier, "label": label, "kind": kind,
            "specs": [ref["spec"] for ref in anchors + near if ref["spec"]],
            "evidence": evidence, "anchors": anchors, "neighbors": near,
            "contact_count": len(near), "nearest_distance": closest, "cutoff": cutoff,
            "selected": any(bool(records[index].get("selected")) for index in anchor_ids)}


def _interfaces(tree, xyz, owner, records, cancelled):
    """Count observed interchain residue contacts, never infer an oligomer."""
    chain_keys = [(r["model_spec"], str(r["chain"])) for r in records]
    unique_chains = {key: i for i, key in enumerate(sorted(set(chain_keys)))}
    chains = np.asarray([unique_chains[chain_keys[int(r)]] for r in owner], dtype=int)
    chain_names = {index: "%s/%s" % (key[0], key[1] or "—")
                   for key, index in unique_chains.items()}
    if len(np.unique(chains)) < 2:
        return []
    groups = {}
    for start in range(0, len(xyz), 256):
        _check(cancelled)
        stop = min(start + 256, len(xyz))
        hits = tree.query_ball_point(xyz[start:stop], CONTACT_CUTOFF)
        lengths = np.fromiter((len(hit) for hit in hits), dtype=int, count=len(hits))
        if not lengths.sum():
            continue
        left = np.repeat(np.arange(start, stop), lengths)
        right = np.concatenate([np.asarray(hit, dtype=int) for hit in hits])
        keep = (right > left) & (chains[left] != chains[right])
        left, right = left[keep], right[keep]
        if not len(left):
            continue
        a, b = chains[left], chains[right]
        keys = np.minimum(a, b) * len(unique_chains) + np.maximum(a, b)
        distance = np.linalg.norm(xyz[left] - xyz[right], axis=1)
        for key in np.unique(keys):
            mask = keys == key
            item = groups.setdefault(int(key), {"residue_ids": set(), "distance": float("inf")})
            item["residue_ids"].update(int(i) for i in np.unique(np.concatenate((owner[left[mask]], owner[right[mask]]))))
            item["distance"] = min(item["distance"], float(distance[mask].min()))
    results = []
    for key, item in groups.items():
        a, b = divmod(key, len(unique_chains))
        results.append({"chains": [chain_names[a], chain_names[b]],
                        "residue_count": len(item["residue_ids"]),
                        "nearest_distance": item["distance"]})
    return sorted(results, key=lambda item: (-item["residue_count"], item["chains"]))


def compute(snapshot, action="analyze", progress=None, cancelled=None):
    """Compute plain data from copied arrays. No Qt, live objects or network."""
    if action != "analyze":
        raise ValueError("quick_analyze supports only analyze")
    _check(cancelled)
    _emit(progress, "Reading local composition and coordinate evidence…")
    xyz, elements, owner, bfactors, occupancies, records = _flatten(snapshot, cancelled)
    models = snapshot.get("models", ())
    polymers = Counter(record["polymer"] for record in records)
    water_ids = {i for i, record in enumerate(records)
                 if record["category"] == "solvent" or str(record["name"]).upper() in _WATERS}
    polymer_ids = {i for i, record in enumerate(records) if record["polymer"] in ("protein", "nucleic")}
    ligand_ids = [i for i, record in enumerate(records)
                  if i not in water_ids and i not in polymer_ids and record["category"] == "ligand"]
    ion_ids = [i for i, record in enumerate(records) if record["category"] == "ions"]
    chains = Counter((r["model_spec"], str(r["chain"]), r["polymer"])
                     for r in records if r["polymer"] in ("protein", "nucleic"))
    chain_count = len({(spec, chain) for spec, chain, _polymer in chains})
    selected = {i for i, record in enumerate(records) if record.get("selected", False)}
    finite = np.isfinite(xyz).all(axis=1)
    heavy = (elements > 1) & finite & (owner >= 0)
    metal_atoms = np.isin(elements, tuple(_METALS))
    categories = Counter(record["category"] for record in records)
    result = {"title": "Local structure analysis", "summary": [], "details": [], "metrics": [],
              "candidates": [], "counts": {"models": len(models), "atoms": len(xyz),
              "residues": len(records), "protein": polymers["protein"], "nucleic": polymers["nucleic"],
              "polymer_chains": chain_count, "ligands": len(ligand_ids), "ions": len(ion_ids),
              "waters": len(water_ids), "metal_atoms": int(metal_atoms.sum()),
              "selected_residues": len(selected)}, "interfaces": []}
    summary, details = result["summary"], result["details"]
    summary.append("%s · %s atoms · %s residues across %d polymer chains." %
                   (snapshot.get("target_label", "Current structure"), format(len(xyz), ","),
                    format(len(records), ","), chain_count))
    result["metrics"] = [{"label": label, "value": format(value, ",")} for label, value in
                         (("Protein residues", polymers["protein"]), ("Nucleotides", polymers["nucleic"]),
                          ("Ligands", len(ligand_ids)), ("Metal atoms", int(metal_atoms.sum())),
                          ("Waters", len(water_ids)), ("Selected residues", len(selected)))]
    details.append("Counts describe loaded residues and atoms, including explicit hydrogens; missing sequence is unavailable.")
    for (spec, chain, polymer), count in sorted(chains.items()):
        details.append("%s/%s: %d %s residues." % (spec, chain or "—", count, polymer))
    composition = Counter(str(record["name"]) for record in records if record["polymer"] in ("protein", "nucleic"))
    if composition:
        details.append("Polymer composition: " + ", ".join("%s %d" % item for item in sorted(composition.items())) + ".")
    protein_records = [record for record in records if record["polymer"] == "protein"]
    ss = Counter(record.get("ss_type", -1) for record in protein_records)
    if ss[1] or ss[2]:
        summary.append("Assigned secondary structure: %d helix, %d strand residues." % (ss[1], ss[2]))
        details.append("Loaded secondary-structure assignments: helix %d, strand %d, coil/unassigned %d; no assignment was recalculated." %
                       (ss[1], ss[2], len(protein_records) - ss[1] - ss[2]))
    elif protein_records:
        details.append("Secondary-structure assignment evidence is unavailable (all coil/unassigned); this is not a finding of no helices or strands.")
    if selected:
        summary.append("Selection: %d residues; heavy-atom neighborhoods are prioritized when available." % len(selected))
    valid_b = bfactors[np.isfinite(bfactors)]
    if len(valid_b) and np.any(valid_b != 0):
        details.append("B-factor field: median %.2f over %d atoms. Its meaning (displacement or confidence) is not inferred." %
                       (float(np.median(valid_b)), len(valid_b)))
    else:
        details.append("B-factor/confidence evidence unavailable or all zero.")
    valid_o = occupancies[np.isfinite(occupancies)]
    if len(valid_o):
        details.append("Occupancy field: %d of %d reported atom values below 1.0." % (int((valid_o < 1).sum()), len(valid_o)))
    else:
        details.append("Occupancy evidence unavailable.")
    if not finite.all():
        details.append("%d atoms with non-finite coordinates were excluded from distance checks." % int((~finite).sum()))
    if np.any(elements == 0):
        details.append("%d atoms have unknown elements and were excluded from heavy-atom distance checks." % int((elements == 0).sum()))
    details.append("Ligand/ion categories follow the loaded structure and may include buffers or crystallization additives.")
    if categories["other"]:
        details.append("%d residues have other/unclassified categories; they are not asserted to be biological ligands." % categories["other"])

    polymer_mask = heavy & np.isin(owner, tuple(polymer_ids))
    polymer_xyz, polymer_owner = xyz[polymer_mask], owner[polymer_mask]
    if len(polymer_xyz):
        _emit(progress, "Measuring ligand, metal and selected-region contacts…")
        _check(cancelled)
        from scipy.spatial import cKDTree
        tree = cKDTree(polymer_xyz)
        # One index pass instead of rescanning all atoms once per ligand.
        site_ids = set(ligand_ids + ion_ids)
        site_ids.update(int(i) for i in owner[heavy & metal_atoms] if int(i) not in polymer_ids and int(i) not in water_ids)
        site_atom_ids = {}
        for atom_index in np.flatnonzero(heavy & np.isin(owner, tuple(site_ids))):
            site_atom_ids.setdefault(int(owner[atom_index]), []).append(int(atom_index))
        candidates = []
        for site_id, atom_ids in sorted(site_atom_ids.items()):
            _check(cancelled)
            residue = records[site_id]
            metal = bool(np.all(metal_atoms[atom_ids]))
            kind = "metal" if metal else ("ion" if site_id in ion_ids else "ligand")
            cutoff = METAL_CUTOFF if metal else CONTACT_CUTOFF
            nearby = _neighbors(xyz[atom_ids], tree, polymer_xyz, polymer_owner, {site_id}, cutoff, cancelled)
            candidate = _candidate(kind, [site_id], nearby, records, cutoff,
                                   "%s · %d nearby residues" % (_ref(residue)["label"], len(nearby)),
                                   "site:%s:%d" % (residue["model_spec"], residue["residue_index"]))
            candidate["heavy_atoms"] = len(atom_ids)
            if metal:
                candidate["evidence"].append("The %.1f Å metal neighborhood is a geometric screen, not a coordination-bond assignment." % cutoff)
            candidates.append(candidate)
        candidates.sort(key=lambda item: (not item["selected"], not bool(item["contact_count"]),
                                         item["kind"] != "ligand", -item["contact_count"],
                                         -item["heavy_atoms"], item["id"]))
        if selected and heavy[np.isin(owner, tuple(selected))].any():
            query = xyz[heavy & np.isin(owner, tuple(selected))]
            nearby = _neighbors(query, tree, polymer_xyz, polymer_owner, selected, CONTACT_CUTOFF, cancelled)
            candidate = _candidate("selection", sorted(selected), nearby, records, CONTACT_CUTOFF,
                                   "Current selection · %d nearby residues" % len(nearby), "selection")
            candidates.insert(0, candidate)
        result["candidates"] = candidates[:32]
        if len(candidates) > 32:
            details.append("Showing the top 32 of %d observed sites; counts cover every loaded site." % len(candidates))
        contacting = sum(bool(candidate["contact_count"]) for candidate in candidates if candidate["kind"] != "selection")
        summary.append("%d observed ligand/ion sites have polymer neighbors; proximity does not establish binding." % contacting)
        if len(polymer_xyz) <= 250000:
            _emit(progress, "Summarizing interchain coordinate contacts…")
            result["interfaces"] = _interfaces(tree, polymer_xyz, polymer_owner, records, cancelled)
            for interface in result["interfaces"][:12]:
                details.append("%s ↔ %s: %d contacting residues total, closest %.2f Å (cutoff %.1f Å)." %
                               (*interface["chains"], interface["residue_count"], interface["nearest_distance"], CONTACT_CUTOFF))
            if result["interfaces"]:
                summary.append("%d chain pairs contact within %.1f Å in the current scene." % (len(result["interfaces"]), CONTACT_CUTOFF))
            details.append("Interchain distances use current scene positions; biological assembly and interface energetics are unavailable.")
        else:
            details.append("Interchain contacts unavailable in quick mode above 250,000 polymer heavy atoms.")
    else:
        details.append("Polymer heavy-atom coordinates unavailable; ligand neighborhoods and interchain contacts cannot be assessed.")
    if not ligand_ids and not ion_ids and not metal_atoms.any():
        summary.append("No ligand/ion records in the loaded model; possible binding sites or functions remain unassessed.")
    details.append("No external annotation or sequence-function evidence was queried; catalytic activity, affinity and druggability are unassessed.")
    _check(cancelled)
    return result


def apply(session, result, context, candidate=0):
    """Reveal a restrained observed neighborhood, preserving existing colors."""
    candidates = result.get("candidates", ())
    if not candidates:
        return "Local analysis ready; no observed site is available to annotate."
    candidate = candidates[max(0, min(int(candidate), len(candidates) - 1))]
    model_by_spec = context.get("model_by_spec", {})
    allowed = {id(model) for model in context.get("models", ())}
    anchors = candidate.get("anchors", ())
    neighbors = candidate.get("neighbors", ())
    # A large selection remains a context, not a request for thousands of sticks.
    reveal = list(anchors[:24]) + list(neighbors[:12])
    labels = list(anchors[:1]) + list(neighbors[:MAX_LABELS - 1])
    resolved = {}
    for ref in reveal + labels:
        model = model_by_spec.get(ref.get("model_spec"))
        index = int(ref.get("residue_index", -1))
        if model is None or id(model) not in allowed or model.deleted or not 0 <= index < len(model.residues):
            continue
        residue = model.residues[index]
        if residue.deleted:
            continue
        model.display = True
        resolved[(ref["model_spec"], index)] = (model, residue)
        atoms = residue.atoms
        heavy = atoms.filter(atoms.element_numbers > 1)
        heavy.displays = True
        from chimerax.atomic import Atom
        heavy.draw_modes = Atom.SPHERE_STYLE if candidate["kind"] == "metal" and ref in anchors else Atom.STICK_STYLE
    if session.ui.is_gui:
        from chimerax.label.label3d import ObjectLabels, ResidueLabel
        # Private label models avoid modifying any user-created labels.
        label_models = {}
        for ref in labels:
            found = resolved.get((ref["model_spec"], int(ref["residue_index"])))
            if found is None:
                continue
            model, residue = found
            overlay = label_models.get(id(model))
            if overlay is None:
                overlay = ObjectLabels(session)
                overlay.name = "Quick analysis labels"
                overlay._codex_quick_overlay = True
                model.add([overlay])
                label_models[id(model)] = overlay
            text = str(ref["label"])
            if "distance" in ref:
                text += " · %.1f Å" % ref["distance"]
            overlay.add_labels([residue], ResidueLabel, session.main_view,
                               settings={"text": text, "height": 0.7, "size": 28}, on_top=False)
    return "Showing %s; local sticks and up to %d labels preserve existing colors." % (candidate["label"], MAX_LABELS)
