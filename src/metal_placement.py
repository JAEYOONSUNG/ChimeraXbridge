"""Predict metal-binding sites in a protein and place virtual metal ions.

Algorithm sketch (Codex consult):
  1. Enumerate coordinator side-chain atoms (Cys SG, His ND1/NE2, Asp OD1/OD2,
     Glu OE1/OE2, Met SD, Asn OD1/ND2, Ser OG, Tyr OH).
  2. Build a 6 Å adjacency graph; connected components with ≥3 distinct
     residues are candidate sites.
  3. Score each: coordinator count + geometric feasibility + KVFinder pocket
     bonus + catalytic-residue overlap.
  4. Predict metal type by donor composition (Cys-rich → Zn²⁺, His-rich →
     Cu/Zn, Asp/Glu-rich → Mg²⁺/Ca²⁺, mixed → Fe²⁺/Mn²⁺).
  5. Place a marker at the coordinator-weighted centroid, then refine via a
     short gradient descent toward ideal metal–donor distances.
  6. Emit ChimeraX `marker create` + `marker link` commands to render the
     metal sphere and coordination guides.
"""

import json
import math
import re
import ssl
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# Donor atom whitelist per residue (one-letter codes used loosely for clarity)
_DONOR_ATOMS = {
    "CYS": ("SG",),
    "HIS": ("ND1", "NE2"),
    "ASP": ("OD1", "OD2"),
    "GLU": ("OE1", "OE2"),
    "MET": ("SD",),
    "ASN": ("OD1", "ND2"),
    "GLN": ("OE1", "NE2"),
    "SER": ("OG",),
    "TYR": ("OH",),
    "THR": ("OG1",),
}

# Ideal metal–donor distances (Å)
_IDEAL_DIST = {
    "ZN": {"S": 2.30, "N": 2.05, "O": 2.05},
    "CU": {"S": 2.25, "N": 2.00, "O": 1.95},
    "FE": {"S": 2.30, "N": 2.10, "O": 2.05},
    "MN": {"S": 2.40, "N": 2.20, "O": 2.15},
    "MG": {"S": 2.60, "N": 2.20, "O": 2.10},
    "CA": {"S": 2.85, "N": 2.45, "O": 2.40},
    "NI": {"S": 2.30, "N": 2.10, "O": 2.05},
    "CO": {"S": 2.30, "N": 2.10, "O": 2.05},
    "M2": {"S": 2.35, "N": 2.15, "O": 2.20},
}

_PREFERRED_COORDINATION = {
    "ZN": (4, 4),
    "CU": (3, 4),
    "FE": (4, 6),
    "MN": (5, 6),
    "MG": (5, 6),
    "CA": (6, 8),
    "NI": (4, 6),
    "CO": (4, 6),
    "M2": (4, 6),
}

# CPK-ish display colors per metal element for the placed marker
_METAL_COLORS = {
    "ZN": "#7d80b0",
    "CU": "#c87533",
    "FE": "#e06633",
    "MN": "#9c7ac0",
    "MG": "#8aff00",
    "CA": "#3dff00",
    "NI": "#50d050",
    "CO": "#f090a0",
    "M2": "#8a94a6",
}

_METAL_CONFIDENCE_STYLES = {
    "resolved": {"alpha": 255, "guide_alpha": 235, "guide_radius": 0.095, "blend": 0.00, "label": "resolved"},
    "high": {"alpha": 245, "guide_alpha": 220, "guide_radius": 0.085, "blend": 0.00, "label": "high"},
    "medium": {"alpha": 185, "guide_alpha": 165, "guide_radius": 0.065, "blend": 0.22, "label": "medium"},
    "low": {"alpha": 115, "guide_alpha": 105, "guide_radius": 0.045, "blend": 0.48, "label": "low"},
}

_METAL_COMPONENT_IDS = {
    "LI", "NA", "K", "RB", "CS",
    "BE", "MG", "CA", "SR", "BA",
    "AL", "GA", "IN", "TL",
    "SC", "Y", "LA", "CE", "PR", "ND", "PM", "SM", "EU", "GD",
    "TB", "DY", "HO", "ER", "TM", "YB", "LU",
    "TI", "ZR", "HF", "V", "NB", "TA", "CR", "MO", "W",
    "MN", "TC", "RE", "FE", "RU", "OS", "CO", "RH", "IR",
    "NI", "PD", "PT", "CU", "AG", "AU", "ZN", "CD", "HG",
    "SN", "PB", "BI", "U",
}


def _clamp_byte(value):
    try:
        return max(0, min(255, int(round(float(value)))))
    except Exception:
        return 0


def _hex_rgb(hex_str):
    h = str(hex_str or "").strip().lstrip("#")
    if len(h) != 6:
        return 136, 136, 136
    try:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except Exception:
        return 136, 136, 136


def _rgba_from_rgb(rgb, alpha):
    return [_clamp_byte(rgb[0]), _clamp_byte(rgb[1]), _clamp_byte(rgb[2]), _clamp_byte(alpha)]


def _blend_rgb(rgb, target=(214, 220, 226), fraction=0.0):
    f = max(0.0, min(1.0, float(fraction or 0.0)))
    return tuple(_clamp_byte((1.0 - f) * rgb[i] + f * target[i]) for i in range(3))


def _candidate_confidence_style(candidate):
    tier = str(candidate.get("review_tier") or "").lower()
    try:
        score = float(candidate.get("review_score", candidate.get("score", 0.0)) or 0.0)
    except Exception:
        score = 0.0
    if tier not in _METAL_CONFIDENCE_STYLES:
        if score >= 0.74:
            tier = "high"
        elif score >= 0.58:
            tier = "medium"
        else:
            tier = "low"
    style = dict(_METAL_CONFIDENCE_STYLES.get(tier, _METAL_CONFIDENCE_STYLES["low"]))
    style["tier"] = tier
    style["score"] = score
    return style


def _candidate_visual_rgba(candidate):
    metal = str(candidate.get("best_metal") or "M2").upper()
    base_rgb = _hex_rgb(_METAL_COLORS.get(metal, "#888888"))
    style = _candidate_confidence_style(candidate)
    rgb = _blend_rgb(base_rgb, fraction=style.get("blend", 0.0))
    marker_rgba = _rgba_from_rgb(rgb, style.get("alpha", 160))
    guide_rgba = _rgba_from_rgb(rgb, style.get("guide_alpha", 140))
    return marker_rgba, guide_rgba, style


def _atom_element(atom):
    name = str(getattr(getattr(atom, "element", None), "name", "")).upper()
    return name or str(getattr(atom, "name", ""))[:1].upper()


def _donor_class(atom):
    el = _atom_element(atom)
    if el == "S":
        return "S"
    if el == "N":
        return "N"
    if el == "O":
        return "O"
    return None


def _collect_donor_atoms(structure):
    """Return list of (atom, residue, donor_class) for all candidate side-chain donors."""
    donors = []
    for residue in structure.residues:
        rname = str(getattr(residue, "name", "")).upper()
        atom_names = _DONOR_ATOMS.get(rname)
        if not atom_names:
            continue
        for atom in residue.atoms:
            aname = str(getattr(atom, "name", ""))
            if aname not in atom_names:
                continue
            cls = _donor_class(atom)
            if cls is None:
                continue
            donors.append((atom, residue, cls))
    return donors


def _atom_xyz(atom):
    coord = atom.scene_coord
    return float(coord[0]), float(coord[1]), float(coord[2])


def _dist(a, b):
    dx = a[0] - b[0]
    dy = a[1] - b[1]
    dz = a[2] - b[2]
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def _cluster_donors(donors, *, cutoff=6.0):
    """Connected-components clustering on donor-atom proximity graph."""
    n = len(donors)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    coords = [_atom_xyz(d[0]) for d in donors]
    cutoff_sq = cutoff * cutoff
    for i in range(n):
        for j in range(i + 1, n):
            dx = coords[i][0] - coords[j][0]
            if abs(dx) > cutoff:
                continue
            dy = coords[i][1] - coords[j][1]
            if abs(dy) > cutoff:
                continue
            dz = coords[i][2] - coords[j][2]
            if dx * dx + dy * dy + dz * dz <= cutoff_sq:
                union(i, j)

    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def _residue_spec(structure, residue):
    chain_id = str(getattr(residue, "chain_id", "") or "?")
    return f"#{getattr(structure, 'id_string', '?')}/{chain_id}:{int(residue.number)}"


def _atom_spec(structure, residue, atom):
    return f"{_residue_spec(structure, residue)}@{atom.name}"


def _atom_label(structure, atom):
    residue = getattr(atom, "residue", None)
    if residue is None:
        return getattr(atom, "name", "?")
    chain_id = str(getattr(residue, "chain_id", "") or "?").strip() or "?"
    try:
        number = int(getattr(residue, "number", 0))
    except Exception:
        number = getattr(residue, "number", "?")
    element = str(getattr(getattr(atom, "element", None), "name", "") or getattr(atom, "name", "")).upper()
    return f"{element} {chain_id}{number}:{getattr(atom, 'name', '?')}"


def _existing_metal_atoms(structure):
    metals = []
    for atom in getattr(structure, "atoms", []) or []:
        element = str(getattr(getattr(atom, "element", None), "name", "") or "").upper()
        if element not in _METAL_COMPONENT_IDS:
            continue
        metals.append(
            {
                "atom": atom,
                "label": _atom_label(structure, atom),
                "element": element,
                "coord": _atom_xyz(atom),
                "spec": _atom_spec(structure, getattr(atom, "residue", None), atom)
                if getattr(atom, "residue", None) is not None else "",
            }
        )
    return metals


def _residue_key(residue):
    chain_id = str(getattr(residue, "chain_id", "") or "?")
    number = int(getattr(residue, "number", 0) or 0)
    insertion_code = str(getattr(residue, "insertion_code", "") or "")
    name = str(getattr(residue, "name", "")).upper()
    return chain_id, number, insertion_code, name


def _residue_name_from_key(key):
    if isinstance(key, tuple) and key:
        return str(key[-1]).upper()
    return str(key).upper()


def _candidate_donor_windows(donors, *, cutoff=6.0, min_residues=3, max_residues=6):
    """Generate compact local donor windows instead of one large connected component.

    A connected graph of His/Asp/Glu/Cys atoms can easily span across neighboring
    surface patches. For metal-site prediction we want several local alternatives:
    each donor atom seeds a sorted window of nearby distinct residues, then we
    evaluate 3-, 4-, 5-, and 6-residue prefixes independently.
    """
    if len(donors) < min_residues:
        return []
    coords = [_atom_xyz(d[0]) for d in donors]
    seen = set()
    windows = []

    for i, seed_coord in enumerate(coords):
        nearest_by_residue = {}
        for j, coord in enumerate(coords):
            d = _dist(seed_coord, coord)
            if d > cutoff:
                continue
            key = _residue_key(donors[j][1])
            current = nearest_by_residue.get(key)
            if current is None or d < current[0]:
                nearest_by_residue[key] = (d, j)

        ordered = [j for _d, j in sorted(nearest_by_residue.values(), key=lambda item: item[0])]
        if len(ordered) < min_residues:
            continue
        for size in range(min_residues, min(max_residues, len(ordered)) + 1):
            window = tuple(sorted(ordered[:size]))
            if window in seen:
                continue
            seen.add(window)
            windows.append(list(window))

    # Preserve genuinely small connected components as additional candidates.
    for component in _cluster_donors(donors, cutoff=cutoff):
        residue_keys = {_residue_key(donors[i][1]) for i in component}
        if min_residues <= len(residue_keys) <= max_residues:
            window = tuple(sorted(component))
            if window not in seen:
                seen.add(window)
                windows.append(list(window))
    return windows


def _predict_metal_type(donor_classes_by_residue):
    """Return a ranked list of (metal_symbol, score, rationale) tuples."""
    counts = {"S": 0, "N": 0, "O": 0}
    res_counts = {"CYS": 0, "HIS": 0, "ASP": 0, "GLU": 0, "MET": 0,
                  "ASN": 0, "GLN": 0, "SER": 0, "TYR": 0, "THR": 0}
    for residue_key, cls_list in donor_classes_by_residue.items():
        rname = _residue_name_from_key(residue_key)
        res_counts[rname] = res_counts.get(rname, 0) + 1
        for cls in cls_list:
            counts[cls] = counts.get(cls, 0) + 1

    scores = []

    cys = res_counts["CYS"]
    his = res_counts["HIS"]
    acidic = res_counts["ASP"] + res_counts["GLU"]
    hydroxyl = res_counts["SER"] + res_counts["THR"] + res_counts["TYR"]
    amide = res_counts["ASN"] + res_counts["GLN"]
    met = res_counts["MET"]
    total_residues = len(donor_classes_by_residue)
    soft_residues = cys + met

    def add(symbol, score, rationale):
        scores.append((symbol, float(max(0.0, min(1.0, score))), rationale))

    # Zn: classic structural site; either Cys-heavy (zinc finger) or
    # mixed His/Cys/Asp tetrahedral.
    if cys >= 4:
        add("ZN", 0.98, f"Cys4 site ({cys} Cys) — strong structural Zn²⁺ motif")
    elif cys >= 2 and his + cys >= 3:
        add("ZN", 0.94, f"Cys/His-rich site ({cys} Cys, {his} His) — classical Zn²⁺ coordination")
    elif his >= 2 and cys >= 1:
        add("ZN", 0.88, f"His/Cys donors ({his} His, {cys} Cys) — Zn²⁺ favored over Mg/Ca")
    elif his >= 2 and acidic >= 1:
        add("ZN", 0.70, f"His/carboxylate donors ({his} His, {acidic} Asp/Glu) — Zn²⁺ alternative")
    elif his >= 3:
        add("ZN", 0.70, f"His-rich site ({his} His) — Zn²⁺/Cu²⁺ candidate")
    elif counts["N"] >= 1 and counts["O"] >= 2 and acidic >= 1:
        add("ZN", 0.42, "Mixed N/O donors with carboxylate — weak Zn²⁺ candidate")

    # Cu: His-dominant, often with Met or Cys
    if his >= 2 and met >= 1:
        add("CU", 0.86, f"His + Met donors ({his} His, {met} Met) — Cu Type-1/blue-copper-like motif")
    elif his >= 2 and cys >= 1:
        add("CU", 0.72, f"His + Cys donors ({his} His, {cys} Cys) — Cu²⁺ candidate, Zn²⁺ also plausible")
    elif his >= 3:
        add("CU", 0.64, f"{his} His donors — Cu²⁺ candidate")

    # Fe: His + Cys (Fe-S adjacent or heme-like) or 2-His-1-Asp/Glu
    if his >= 2 and acidic >= 1:
        add("FE", 0.78, "2-His-1-carboxylate facial triad — non-heme Fe²⁺/Fe³⁺ candidate")
    elif his >= 1 and cys >= 1 and acidic >= 1:
        add("FE", 0.62, "His/Cys/carboxylate donors — possible Fe site, Zn remains plausible")
    elif cys >= 3 and his == 0:
        add("FE", 0.50, "Cys-rich soft-donor site — possible Fe/S-associated site, but isolated ion favors Zn")

    # Mn: O-rich pocket with optional His
    if acidic >= 3 and his >= 1 and soft_residues == 0:
        add("MN", 0.74, f"{acidic} carboxylates + His — Mn²⁺ catalytic candidate")
    elif acidic >= 2 and his >= 1 and soft_residues == 0:
        add("MN", 0.64, f"{acidic} carboxylates + His — Mn²⁺/Fe²⁺ oxygen-rich candidate")

    # Mg/Ca should not win from generic Ser/Thr/Tyr oxygen clusters alone.
    # Require multiple carboxylate residues, no soft donors, and enough O donors.
    if acidic >= 4 and his == 0 and soft_residues == 0 and counts["O"] >= 5:
        add("CA", 0.72, f"{acidic} carboxylates and {counts['O']} O donors — Ca²⁺-like oxygen cage")
        add("MG", 0.66, f"{acidic} carboxylates and compact O donors — Mg²⁺ alternative")
    elif acidic >= 3 and his == 0 and soft_residues == 0 and counts["O"] >= 4 and hydroxyl <= 1:
        add("MG", 0.68, f"{acidic} carboxylates, no N/S donors — Mg²⁺ candidate")
        add("CA", 0.56, f"{acidic} carboxylates, no N/S donors — Ca²⁺ alternative")
    elif acidic >= 3 and his == 0 and soft_residues == 0:
        add("CA", 0.46, f"{acidic} carboxylates with hydroxyl/amide O donors — weak Ca²⁺ candidate")
        add("MG", 0.42, f"{acidic} carboxylates with incomplete compact O cage — weak Mg²⁺ candidate")
    elif acidic >= 2 and his == 0 and soft_residues == 0 and total_residues >= 4:
        add("CA", 0.34, f"Only {acidic} carboxylates plus {hydroxyl + amide} neutral O donors — low-confidence Ca²⁺/Mg²⁺")

    if not scores:
        add("M2", 0.36, "No clear donor pattern — generic divalent metal; element type not assigned")
    elif max(sc for _sym, sc, _rat in scores) < 0.50:
        add("M2", 0.49, "Donor pattern is too weak for element assignment — generic divalent metal candidate")

    # Sort high → low, dedupe by element keeping max score
    best = {}
    for sym, sc, rat in scores:
        if sym not in best or sc > best[sym][0]:
            best[sym] = (sc, rat)
    ranked = sorted(((s, sc, rat) for s, (sc, rat) in best.items()),
                    key=lambda t: -t[1])
    return ranked


def _dedupe_candidates(candidates, *, limit):
    kept = []
    for candidate in sorted(candidates, key=lambda c: -c["score"]):
        res_set = frozenset(spec for spec, _name in candidate["residues"])
        center = candidate["best_position"]
        duplicate = False
        for existing in kept:
            existing_set = frozenset(spec for spec, _name in existing["residues"])
            overlap = len(res_set & existing_set) / max(1, min(len(res_set), len(existing_set)))
            if res_set == existing_set or (overlap >= 0.75 and _dist(center, existing["best_position"]) < 2.5):
                duplicate = True
                break
        if duplicate:
            continue
        kept.append(candidate)
        if len(kept) >= limit:
            break
    return kept


def _selected_structures(session, model_hint=None):
    try:
        from chimerax.atomic import AtomicStructure, all_atomic_structures
    except Exception:
        return []
    from .semantic import resolve_model_spec

    def is_helper_model(model):
        name = str(getattr(model, "name", "") or "").strip().lower()
        if name.startswith((
            "domain ",
            "group_",
            "cavity",
            "binding_pocket",
            "metal candidate",
            "predicted metal",
            "rapidock_",
            "hpepdock_",
            "afcomplex_",
        )):
            return True
        id_string = str(getattr(model, "id_string", "") or "")
        return "." in id_string and any(token in name for token in ("cavity", "pocket"))

    if model_hint:
        spec = resolve_model_spec(session, model_hint)
        if spec is not None:
            return [
                m for m in session.models.list(type=AtomicStructure)
                if f"#{getattr(m, 'id_string', '?')}" == spec
                and not is_helper_model(m)
            ]
    primary = [m for m in all_atomic_structures(session) if not is_helper_model(m)]
    return primary or list(all_atomic_structures(session))


def _protein_sequence_entries(session, model_hint=None, *, max_chains=3, min_length=30):
    """Return protein-chain sequences from the currently open ChimeraX structures."""
    try:
        from chimerax.atomic import Residue
    except Exception:
        Residue = None

    entries = []
    for model in _selected_structures(session, model_hint=model_hint):
        model_spec = f"#{getattr(model, 'id_string', '?')}"
        for chain in getattr(model, "chains", []) or []:
            polymer_type = getattr(chain, "polymer_type", None)
            protein_types = ()
            if Residue is not None:
                protein_types = tuple(
                    value for value in (
                        getattr(Residue, "PT_AMINO", None),
                        getattr(Residue, "PT_PROTEIN", None),
                    )
                    if value is not None
                )
            if protein_types and polymer_type not in protein_types:
                continue
            try:
                sequence = str(chain.ungapped()).strip().upper()
            except Exception:
                sequence = str(getattr(chain, "characters", "") or "").replace("-", "").strip().upper()
            sequence = re.sub(r"[^A-Z]", "", sequence)
            if len(sequence) < min_length:
                continue
            chain_id = str(getattr(chain, "chain_id", "") or "?").strip() or "?"
            entries.append(
                {
                    "model_spec": model_spec,
                    "model_name": str(getattr(model, "name", "structure") or "structure"),
                    "chain_id": chain_id,
                    "spec": f"{model_spec}/{chain_id}",
                    "sequence": sequence,
                }
            )
            if len(entries) >= max_chains:
                return entries
    return entries


def _http_error_detail(err):
    try:
        detail = err.read().decode("utf-8", "replace").strip()
    except Exception:
        detail = ""
    return detail[:240]


def _json_post(url, payload, *, timeout=20, fallback_urls=(), retries=1):
    body = json.dumps(payload).encode("utf-8")
    endpoints = []
    for endpoint in (url, *tuple(fallback_urls or ())):
        if endpoint and endpoint not in endpoints:
            endpoints.append(endpoint)
    last_error = None

    def make_request(endpoint):
        return Request(
            endpoint,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "ChimeraX-Codex-Bridge/0.1",
            },
        )

    attempts = max(1, int(retries or 1))
    for endpoint_index, endpoint in enumerate(endpoints):
        for attempt in range(attempts):
            try:
                try:
                    response = urlopen(make_request(endpoint), timeout=timeout)
                except Exception as err:
                    # Some macOS Python installations lack a current CA bundle.
                    # Keep the fallback scoped to this request.
                    if "CERTIFICATE_VERIFY_FAILED" not in str(err):
                        raise
                    context = ssl._create_unverified_context()
                    response = urlopen(make_request(endpoint), timeout=timeout, context=context)
                with response:
                    if getattr(response, "status", 200) == 204:
                        return {"result_set": []}
                    text = response.read().decode("utf-8")
                    return json.loads(text) if text.strip() else {"result_set": []}
            except HTTPError as err:
                detail = _http_error_detail(err)
                last_error = RuntimeError(
                    f"HTTP {err.code} from {endpoint}"
                    + (f": {detail}" if detail else "")
                )
                transient = int(getattr(err, "code", 0) or 0) in {408, 429, 500, 502, 503, 504}
            except URLError as err:
                last_error = RuntimeError(f"network error from {endpoint}: {err}")
                transient = True
            except Exception as err:
                last_error = err
                transient = "timed out" in str(err).lower()

            has_more_attempts = attempt + 1 < attempts
            has_more_endpoints = endpoint_index + 1 < len(endpoints)
            if transient and has_more_attempts:
                time.sleep(min(2.0, 0.4 * (attempt + 1)))
                continue
            if transient and has_more_endpoints:
                break
            raise last_error
    if last_error is not None:
        raise last_error
    raise RuntimeError("No JSON endpoint configured")


def _rcsb_sequence_payload(sequence, *, rows=12, identity_cutoff=0.30):
    return {
        "query": {
            "type": "terminal",
            "service": "sequence",
            "parameters": {
                "evalue_cutoff": 1,
                "identity_cutoff": float(identity_cutoff),
                "sequence_type": "protein",
                "value": str(sequence),
            },
        },
        "return_type": "polymer_entity",
        "request_options": {
            "paginate": {"start": 0, "rows": int(rows)},
            "results_content_type": ["experimental"],
        },
    }


def _parse_rcsb_sequence_hits(data):
    hits = []
    seen_entries = set()
    for item in data.get("result_set", []) or []:
        if isinstance(item, str):
            identifier = item.upper()
            score = None
        else:
            identifier = str(item.get("identifier", "") or "").upper()
            score = item.get("score")
        if not identifier:
            continue
        entry_id = identifier.split("_", 1)[0]
        if not entry_id or entry_id in seen_entries:
            continue
        seen_entries.add(entry_id)
        hits.append(
            {
                "identifier": identifier,
                "entry_id": entry_id,
                "score": score,
            }
        )
    return hits


def _rcsb_sequence_hits_query(sequence, *, rows=12, identity_cutoff=0.30):
    payload = _rcsb_sequence_payload(sequence, rows=rows, identity_cutoff=identity_cutoff)
    data = _json_post(
        "https://search-east.rcsb.org/rcsbsearch/v2/query",
        payload,
        timeout=25,
        fallback_urls=(
            "https://search.rcsb.org/rcsbsearch/v2/query",
            "https://search-west.rcsb.org/rcsbsearch/v2/query",
        ),
        retries=1,
    )
    return _parse_rcsb_sequence_hits(data)


def _sequence_windows(sequence, *, target=220, overlap=60, min_length=80):
    seq = str(sequence or "")
    if len(seq) <= target:
        return []
    windows = []
    step = max(1, int(target) - int(overlap))
    for start in range(0, len(seq), step):
        end = min(len(seq), start + int(target))
        if end - start < int(min_length):
            break
        windows.append((start + 1, end, seq[start:end]))
        if end >= len(seq):
            break
    return windows


def _friendly_rcsb_error(error_text):
    text = str(error_text or "")
    lowered = text.lower()
    if "http 500" in lowered or "connection refused" in lowered or "timed out" in lowered:
        return "RCSB sequence service was unavailable after retrying regional endpoints."
    return text or "RCSB sequence search failed."


def _rcsb_sequence_hits(session, sequence, *, rows=12, identity_cutoff=0.30):
    cache = getattr(session, "_codex_bridge_rcsb_sequence_hit_cache", None)
    if cache is None:
        cache = {}
        session._codex_bridge_rcsb_sequence_hit_cache = cache
    key = (str(sequence), int(rows), float(identity_cutoff))
    if key in cache:
        return cache[key]

    try:
        hits = _rcsb_sequence_hits_query(
            sequence,
            rows=rows,
            identity_cutoff=identity_cutoff,
        )
    except Exception as first_err:
        chunk_hits = []
        seen_entries = set()
        chunk_errors = []
        for start, end, chunk in _sequence_windows(sequence):
            try:
                hits_for_chunk = _rcsb_sequence_hits_query(
                    chunk,
                    rows=max(int(rows), 8),
                    identity_cutoff=identity_cutoff,
                )
            except Exception as err:
                chunk_errors.append(str(err))
                continue
            for hit in hits_for_chunk:
                entry_id = hit["entry_id"]
                if entry_id in seen_entries:
                    continue
                seen_entries.add(entry_id)
                hit = dict(hit)
                hit["query_range"] = (start, end)
                chunk_hits.append(hit)
                if len(chunk_hits) >= int(rows):
                    break
            if len(chunk_hits) >= int(rows):
                break
        if not chunk_hits:
            raise RuntimeError(_friendly_rcsb_error(first_err)) from first_err
        try:
            session.logger.info(
                "RCSB full-chain sequence search failed; using chunked sequence fallback "
                f"({len(chunk_hits)} hit(s))."
            )
        except Exception:
            pass
        hits = chunk_hits[: int(rows)]
    cache[key] = hits
    return hits


def _component_metal_symbols(component):
    comp_id = str(component.get("id", "") or "").strip().upper()
    name = str(component.get("name", "") or "")
    formula = str(component.get("formula", "") or "")
    symbols = set()

    if comp_id in _METAL_COMPONENT_IDS:
        symbols.add(comp_id)
    match = re.match(r"^([A-Z][A-Z]?)(?:[0-9+-].*)?$", comp_id)
    if match and match.group(1) in _METAL_COMPONENT_IDS:
        symbols.add(match.group(1))

    for token in re.findall(r"\b([A-Z][a-z]?)\b", formula):
        upper = token.upper()
        if upper in _METAL_COMPONENT_IDS:
            symbols.add(upper)

    upper_name = name.upper()
    for symbol in _METAL_COMPONENT_IDS:
        if f"{symbol} ION" in upper_name or f"{symbol}(" in upper_name:
            symbols.add(symbol)
    return sorted(symbols)


def _rcsb_entry_metal_payloads(session, entry_ids):
    normalized = []
    for entry_id in entry_ids:
        eid = str(entry_id or "").strip().upper()
        if eid and eid not in normalized:
            normalized.append(eid)
    if not normalized:
        return {}

    cache = getattr(session, "_codex_bridge_rcsb_entry_metal_cache", None)
    if cache is None:
        cache = {}
        session._codex_bridge_rcsb_entry_metal_cache = cache
    missing = [eid for eid in normalized if eid not in cache]
    if missing:
        query = """
        query($ids:[String!]!) {
          entries(entry_ids:$ids) {
            rcsb_id
            struct { title }
            exptl { method }
            rcsb_entry_info { resolution_combined }
            nonpolymer_entities {
              nonpolymer_comp {
                chem_comp { id name type formula }
              }
            }
          }
        }
        """
        for start in range(0, len(missing), 20):
            chunk = missing[start:start + 20]
            data = _json_post(
                "https://data.rcsb.org/graphql",
                {"query": query, "variables": {"ids": chunk}},
                timeout=25,
            )
            for entry in ((data.get("data") or {}).get("entries") or []):
                if not entry:
                    continue
                eid = str(entry.get("rcsb_id", "") or "").upper()
                metal_components = []
                for entity in entry.get("nonpolymer_entities", []) or []:
                    comp = (((entity or {}).get("nonpolymer_comp") or {}).get("chem_comp") or {})
                    symbols = _component_metal_symbols(comp)
                    if not symbols:
                        continue
                    metal_components.append(
                        {
                            "id": str(comp.get("id", "") or "").upper(),
                            "name": str(comp.get("name", "") or "").strip(),
                            "formula": str(comp.get("formula", "") or "").strip(),
                            "metal_symbols": symbols,
                        }
                    )
                cache[eid] = {
                    "entry_id": eid,
                    "title": str(((entry.get("struct") or {}).get("title")) or "").strip(),
                    "methods": [
                        str((item or {}).get("method", "")).strip()
                        for item in (entry.get("exptl") or [])
                        if str((item or {}).get("method", "")).strip()
                    ],
                    "resolution": (entry.get("rcsb_entry_info") or {}).get("resolution_combined") or [],
                    "metal_components": metal_components,
                }
            for eid in chunk:
                cache.setdefault(
                    eid,
                    {
                        "entry_id": eid,
                        "title": "",
                        "methods": [],
                        "resolution": [],
                        "metal_components": [],
                    },
                )
    return {eid: cache.get(eid) for eid in normalized if cache.get(eid) is not None}


def _format_component(component):
    label = component.get("id") or "?"
    name = component.get("name") or ""
    formula = component.get("formula") or ""
    metals = "/".join(component.get("metal_symbols") or [])
    bits = [label]
    if metals and metals != label:
        bits.append(metals)
    if name:
        bits.append(name)
    if formula:
        bits.append(f"formula {formula}")
    if len(bits) == 1:
        return bits[0]
    return bits[0] + " (" + "; ".join(bits[1:]) + ")"


def collect_fold_metal_evidence(session, *, model_hint=None, rows=20,
                                identity_cutoff=0.30, max_chains=4):
    """Return structured RCSB homolog metal evidence for open protein chains."""
    chains = _protein_sequence_entries(session, model_hint=model_hint, max_chains=max_chains)
    evidence = {
        "chains": [],
        "metal_counts": {},
        "entry_count": 0,
        "metal_entry_count": 0,
        "errors": [],
        "rows": int(rows),
        "identity_cutoff": float(identity_cutoff),
    }
    if not chains:
        evidence["errors"].append(
            "No protein chain sequence was resolved from the currently open ChimeraX atomic models."
        )
        return evidence

    for chain in chains:
        chain_record = {
            "chain": chain,
            "hits": [],
            "metal_hits": [],
            "errors": [],
        }
        try:
            hits = _rcsb_sequence_hits(
                session,
                chain["sequence"],
                rows=rows,
                identity_cutoff=identity_cutoff,
            )
        except Exception as err:
            chain_record["errors"].append(str(err))
            evidence["errors"].append(str(err))
            evidence["chains"].append(chain_record)
            continue

        chain_record["hits"] = hits
        evidence["entry_count"] += len(hits)
        payloads = _rcsb_entry_metal_payloads(session, [h["entry_id"] for h in hits])
        for hit in hits:
            payload = payloads.get(hit["entry_id"]) or {}
            components = payload.get("metal_components") or []
            if not components:
                continue
            chain_record["metal_hits"].append((hit, payload))
            evidence["metal_entry_count"] += 1
            for component in components:
                for symbol in component.get("metal_symbols") or []:
                    evidence["metal_counts"][symbol] = evidence["metal_counts"].get(symbol, 0) + 1
        evidence["chains"].append(chain_record)
    return evidence


def format_fold_metal_evidence_report(session, *, model_hint=None, rows=12,
                                      identity_cutoff=0.30, max_chains=3,
                                      evidence=None):
    """Search experimental RCSB homolog/fold hits and report metal-bearing entries."""
    if evidence is None:
        evidence = collect_fold_metal_evidence(
            session,
            model_hint=model_hint,
            rows=rows,
            identity_cutoff=identity_cutoff,
            max_chains=max_chains,
        )
    chains = evidence.get("chains") or []
    if not chains:
        errors = evidence.get("errors") or []
        reason = errors[0] if errors else "No protein chain sequence was resolved from the currently open ChimeraX atomic models."
        return "## Fold/homolog metal evidence (RCSB)\n- " + reason

    lines = ["## Fold/homolog metal evidence (RCSB)"]
    lines.append(
        "- Basis: sequence search from currently open ChimeraX AtomicStructure chain(s); "
        "this is evidence for related experimental structures, not proof that the current model binds the same metal."
    )
    counts = evidence.get("metal_counts") or {}
    if counts:
        count_text = ", ".join(f"{symbol}:{count}" for symbol, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:8])
        lines.append(f"- Metal components observed across checked homologs: {count_text}")
    for chain_record in chains:
        chain = chain_record.get("chain") or {}
        sequence = chain["sequence"]
        lines.append(f"\n### {chain['spec']} {chain['model_name']} chain {chain['chain_id']} ({len(sequence)} aa)")
        if chain_record.get("errors"):
            err = "; ".join(chain_record["errors"])
            lines.append(f"- RCSB sequence search failed: {err}")
            continue
        hits = chain_record.get("hits") or []
        if not hits:
            lines.append(f"- No experimental RCSB sequence hits at identity >= {identity_cutoff:.2f}.")
            continue

        metal_hits = chain_record.get("metal_hits") or []
        hit_labels = ", ".join(h["entry_id"] for h in hits[:8])
        lines.append(f"- Top experimental hits checked: {hit_labels}")
        if not metal_hits:
            lines.append(f"- No metal-containing nonpolymer components found among top {len(hits)} RCSB hits.")
            continue
        for hit, payload in metal_hits[:8]:
            score = hit.get("score")
            score_text = f", sequence score {float(score):.2f}" if isinstance(score, (int, float)) else ""
            methods = ", ".join(payload.get("methods")[:2]) if payload.get("methods") else ""
            resolution = payload.get("resolution") or []
            res_text = f", resolution {resolution[0]} A" if resolution else ""
            comps = ", ".join(_format_component(c) for c in payload.get("metal_components", [])[:5])
            title = payload.get("title") or ""
            detail = "; " + title if title else ""
            lines.append(
                f"- {hit['entry_id']}{score_text}{res_text}"
                f"{'; ' + methods if methods else ''}{detail}: {comps}"
            )
    return "\n".join(lines)


def _candidate_review_annotations(candidate, fold_evidence=None):
    validation = candidate.get("validation") or {}
    predictions = candidate.get("predictions") or []
    best_metal = str(candidate.get("best_metal") or "M2").upper()
    likelihood = 0.0
    for symbol, score, _reason in predictions:
        if str(symbol).upper() == best_metal:
            try:
                likelihood = float(score)
            except Exception:
                likelihood = 0.0
            break
    geom = float(validation.get("geometry_score", candidate.get("geometry_score", 0.0)) or 0.0)
    rmsd = float(validation.get("distance_rmsd", 99.0) or 99.0)
    donor_count = int(validation.get("donor_count", len(candidate.get("donor_atoms", []) or [])) or 0)
    acceptable = int(validation.get("acceptable_donors", 0) or 0)
    donor_fraction = acceptable / max(1, donor_count)
    n_res = len(candidate.get("residues") or [])
    pocket_fraction = min(1.0, float(candidate.get("pocket_overlap", 0) or 0) / max(1, n_res))
    catalytic_fraction = min(1.0, float(candidate.get("catalytic_overlap", 0) or 0) / max(1, n_res))
    existing_distance = candidate.get("existing_metal_distance")
    existing_bonus = 1.0 if existing_distance is not None and float(existing_distance) <= 3.0 else 0.0
    relaxed_scan = bool(candidate.get("relaxed_scan"))

    fold_counts = (fold_evidence or {}).get("metal_counts") or {}
    fold_total = max(1, sum(int(v) for v in fold_counts.values()))
    fold_match = int(fold_counts.get(best_metal, 0) or 0)
    fold_any = sum(
        int(v)
        for symbol, v in fold_counts.items()
        if str(symbol).upper() in {str(p[0]).upper() for p in predictions[:3]}
    )
    fold_score = 0.0
    if fold_match:
        fold_score = min(1.0, 0.45 + fold_match / max(4.0, fold_total))
    elif fold_any:
        fold_score = min(0.55, fold_any / max(5.0, fold_total))

    local_score = float(candidate.get("score", 0.0) or 0.0)
    review_score = min(
        1.0,
        0.34 * local_score
        + 0.24 * geom
        + 0.15 * likelihood
        + 0.10 * donor_fraction
        + 0.07 * pocket_fraction
        + 0.05 * catalytic_fraction
        + 0.05 * fold_score
        + 0.10 * existing_bonus,
    )
    flags = []
    if best_metal == "M2":
        flags.append("metal element not confidently assigned")
        review_score = min(review_score, 0.58)
    if relaxed_scan:
        flags.append("relaxed donor-radius candidate; inspect before using")
        review_score = min(review_score, 0.56)
    if geom < 0.45:
        flags.append("weak coordination geometry")
        review_score = min(review_score, 0.62)
    if rmsd > 0.85:
        flags.append(f"donor-distance RMSD {rmsd:.2f} A")
    if donor_count < 4 and best_metal not in {"CU"}:
        flags.append(f"only {donor_count} donor atoms")
        review_score = min(review_score, 0.68)
    if not fold_counts:
        flags.append("no homolog metal evidence found/available")
    elif not fold_match:
        flags.append("homolog metals do not specifically support predicted element")
    if existing_bonus:
        tier = "resolved"
    elif review_score >= 0.74 and geom >= 0.55 and likelihood >= 0.55:
        tier = "high"
    elif review_score >= 0.58 and geom >= 0.42 and likelihood >= 0.42:
        tier = "medium"
    else:
        tier = "low"

    annotated = dict(candidate)
    annotated.update({
        "review_score": review_score,
        "review_tier": tier,
        "review_flags": flags,
        "fold_metal_counts": dict(fold_counts),
        "fold_metal_match_count": fold_match,
        "fold_score": fold_score,
        "donor_fraction": donor_fraction,
    })
    return annotated


def _tier_value(tier):
    return {"low": 0, "medium": 1, "high": 2, "resolved": 3}.get(str(tier or "").lower(), 0)


def _annotate_and_sort_candidates(candidates, fold_evidence=None):
    annotated = [_candidate_review_annotations(c, fold_evidence=fold_evidence) for c in candidates]
    annotated.sort(
        key=lambda c: (
            -_tier_value(c.get("review_tier")),
            -float(c.get("review_score", 0.0) or 0.0),
            -float(c.get("score", 0.0) or 0.0),
        )
    )
    for i, c in enumerate(annotated, start=1):
        c["site_index"] = i
    return annotated


def _candidate_merge_key(candidate):
    residues = frozenset(spec for spec, _name in candidate.get("residues", []) or [])
    return residues


def _merge_metal_candidate_lists(primary, secondary, *, limit):
    merged = list(primary or [])
    for candidate in secondary or []:
        candidate_set = _candidate_merge_key(candidate)
        center = candidate.get("best_position")
        duplicate = False
        for existing in merged:
            existing_set = _candidate_merge_key(existing)
            overlap = len(candidate_set & existing_set) / max(1, min(len(candidate_set), len(existing_set)))
            existing_center = existing.get("best_position")
            if candidate_set == existing_set:
                duplicate = True
                break
            if center is not None and existing_center is not None and overlap >= 0.6 and _dist(center, existing_center) < 3.0:
                duplicate = True
                break
        if duplicate:
            continue
        merged.append(candidate)
    return _dedupe_candidates(merged, limit=limit)


def _format_metal_review_report(
    candidates,
    *,
    fold_evidence=None,
    elapsed=None,
    min_tier="medium",
    requested_top=None,
    detected_count=None,
    preview_count=None,
):
    lines = [
        "## Metal coordination review",
        "- Mode: evidence-gated review from current open structure coordinates.",
        "- Checks: donor chemistry, metal-specific geometry, KVFinder pocket overlap, catalytic-like overlap, existing resolved metals, and RCSB experimental homolog metal evidence.",
    ]
    if elapsed is not None:
        lines.append(f"- Runtime: {elapsed:.1f} s")
    if requested_top is not None:
        detected = detected_count if detected_count is not None else len(candidates)
        lines.append(f"- Candidates requested: {int(requested_top)}; detected: {int(detected)}; listed: {len(candidates)}.")
    if fold_evidence:
        counts = fold_evidence.get("metal_counts") or {}
        if counts:
            count_text = ", ".join(f"{symbol}:{count}" for symbol, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:8])
            lines.append(f"- Homolog metal summary: {count_text}")
        else:
            lines.append("- Homolog metal summary: no metal-bearing top RCSB hits found or RCSB unavailable.")
    eligible = [c for c in candidates if _tier_value(c.get("review_tier")) >= _tier_value(min_tier)]
    if preview_count is not None:
        lines.append(f"- 3D preview: {int(preview_count)} candidate(s) rendered from the listed set using gate >= {min_tier}.")
    elif eligible:
        lines.append(f"- 3D preview: {len(eligible)} candidate(s) at confidence >= {min_tier}.")
    else:
        lines.append(
            f"- 3D overlay: no virtual metal marker inserted because no candidate reached {min_tier} confidence; "
            "top residue cluster is highlighted for inspection only."
        )
    for c in candidates:
        idx = c.get("site_index", "?")
        metal = _display_metal(c.get("best_metal"))
        validation = c.get("validation") or {}
        flags = "; ".join(c.get("review_flags") or []) or "passes current review gates"
        residues_text = ", ".join(spec for spec, _name in (c.get("residues") or []))
        pred_text = "; ".join(f"{_display_metal(m)} {p:.2f}" for m, p, _ in (c.get("predictions") or [])[:3])
        scan_text = " [relaxed 7.5 A scan]" if c.get("relaxed_scan") else ""
        lines.append(
            f"\n### Site {idx} — {metal} [{c.get('review_tier', 'low')}, review {float(c.get('review_score', 0.0) or 0.0):.2f}]{scan_text}"
            f"\n- Coordinates: ({c['best_position'][0]:.2f}, {c['best_position'][1]:.2f}, {c['best_position'][2]:.2f})"
            f"\n- Residues: {residues_text}"
            f"\n- Local score {float(c.get('score', 0.0) or 0.0):.2f}; geometry {float(validation.get('geometry_score', 0.0) or 0.0):.2f}; "
            f"RMSD {float(validation.get('distance_rmsd', 0.0) or 0.0):.2f} A; acceptable donors {validation.get('acceptable_donors', 0)}/{validation.get('donor_count', 0)}"
            f"\n- Metal alternatives: {pred_text}"
            f"\n- Evidence flags: {flags}"
        )
        if c.get("existing_metal_label"):
            lines.append(
                f"- Existing nearby metal: {c['existing_metal_label']} "
                f"({float(c.get('existing_metal_distance', 0.0) or 0.0):.2f} A)"
            )
    return "\n".join(lines)


def format_metal_evidence_report(session, *, model_hint=None, top_n=5,
                                 include_rcsb=True, rows=12):
    """Combine current metals, local predicted sites, and optional RCSB fold evidence."""
    from .semantic import format_metal_report as format_existing_metal_report

    parts = [
        "## Existing metal ions in currently open structures\n"
        + format_existing_metal_report(session, model_hint=model_hint),
        "## Predicted local metal-coordination candidates\n"
        + format_metal_report(session, model_hint=model_hint, top_n=top_n),
    ]
    if include_rcsb:
        parts.append(
            format_fold_metal_evidence_report(
                session,
                model_hint=model_hint,
                rows=rows,
            )
        )
    return "\n\n".join(parts)


def _ideal_dist_for(metal, cls):
    return _IDEAL_DIST.get(metal, _IDEAL_DIST["ZN"]).get(cls, 2.10)


def _display_metal(metal):
    return "M2+" if str(metal).upper() == "M2" else str(metal)


def _weighted_centroid(coord_atoms):
    weights = {"S": 1.10, "N": 1.00, "O": 0.90}
    sx = sy = sz = sw = 0.0
    for atom, _residue, cls in coord_atoms:
        x, y, z = _atom_xyz(atom)
        w = weights.get(cls, 1.0)
        sx += x * w
        sy += y * w
        sz += z * w
        sw += w
    if sw == 0:
        return None
    return (sx / sw, sy / sw, sz / sw)


def _refine_position(initial, coord_atoms, metal):
    """Short gradient descent to minimize Σ(d_i - ideal_i)²."""
    pos = list(initial)
    targets = []
    coords = []
    for atom, _residue, cls in coord_atoms:
        targets.append(_ideal_dist_for(metal, cls))
        coords.append(_atom_xyz(atom))
    lr = 0.05
    for _ in range(40):
        gx = gy = gz = 0.0
        for (cx, cy, cz), t in zip(coords, targets):
            dx = pos[0] - cx
            dy = pos[1] - cy
            dz = pos[2] - cz
            d = math.sqrt(dx * dx + dy * dy + dz * dz)
            if d < 1e-3:
                continue
            err = d - t
            gx += 2 * err * dx / d
            gy += 2 * err * dy / d
            gz += 2 * err * dz / d
        pos[0] -= lr * gx
        pos[1] -= lr * gy
        pos[2] -= lr * gz
    return tuple(pos)


def _coordination_number_score(metal, n):
    lo, hi = _PREFERRED_COORDINATION.get(str(metal).upper(), (4, 6))
    if lo <= n <= hi:
        return 1.0
    if n < lo:
        return max(0.0, 1.0 - 0.28 * (lo - n))
    return max(0.0, 1.0 - 0.18 * (n - hi))


def _coordination_validation(coord_atoms, metal, *, position=None):
    """Return local geometry checks for a proposed metal position."""
    if not coord_atoms:
        return {
            "position": position,
            "geometry_score": 0.0,
            "distance_rmsd": 99.0,
            "acceptable_donors": 0,
            "donor_count": 0,
            "flags": "no donor atoms",
            "distances": [],
        }
    if position is None:
        centroid = _weighted_centroid(coord_atoms)
        if centroid is None:
            return {
                "position": None,
                "geometry_score": 0.0,
                "distance_rmsd": 99.0,
                "acceptable_donors": 0,
                "donor_count": len(coord_atoms),
                "flags": "no centroid",
                "distances": [],
            }
        position = _refine_position(centroid, coord_atoms, metal)

    distances = []
    sq_error = 0.0
    bad = 0
    close = 0
    far = 0
    for atom, _residue, cls in coord_atoms:
        d = _dist(position, _atom_xyz(atom))
        ideal = _ideal_dist_for(metal, cls)
        delta = d - ideal
        sq_error += delta * delta
        if d < max(1.45, ideal - 0.45):
            bad += 1
            close += 1
        elif d > ideal + 1.05:
            bad += 1
            far += 1
        distances.append(
            {
                "atom_spec": None,
                "class": cls,
                "distance": d,
                "ideal": ideal,
                "delta": delta,
            }
        )
    n = len(coord_atoms)
    rmsd = math.sqrt(sq_error / max(1, n))
    distance_score = max(0.0, min(1.0, 1.0 - rmsd / 1.25))
    range_score = max(0.0, 1.0 - bad / max(1, n))
    cn_score = _coordination_number_score(metal, n)
    score = max(0.0, min(1.0, 0.58 * distance_score + 0.27 * range_score + 0.15 * cn_score))
    flags = []
    if close:
        flags.append(f"{close} too-close donor(s)")
    if far:
        flags.append(f"{far} long donor(s)")
    if cn_score < 0.85:
        lo, hi = _PREFERRED_COORDINATION.get(str(metal).upper(), (4, 6))
        flags.append(f"coordination number {n} outside preferred {lo}-{hi}")
    return {
        "position": tuple(float(v) for v in position),
        "geometry_score": score,
        "distance_rmsd": rmsd,
        "acceptable_donors": n - bad,
        "donor_count": n,
        "flags": "; ".join(flags) or "geometry ok",
        "distances": distances,
    }


def _rank_metal_predictions(coord_atoms, predictions):
    """Blend donor-composition likelihood with element-specific geometry checks."""
    ranked = []
    seen = set()
    for symbol, likelihood, rationale in predictions:
        symbol = str(symbol).upper()
        if symbol in seen:
            continue
        seen.add(symbol)
        validation = _coordination_validation(coord_atoms, symbol)
        geom = float(validation.get("geometry_score", 0.0) or 0.0)
        combined = 0.70 * float(likelihood) + 0.30 * geom
        if symbol == "M2":
            combined = min(combined, 0.50)
        ranked.append(
            {
                "symbol": symbol,
                "likelihood": max(0.0, min(1.0, combined)),
                "raw_likelihood": float(likelihood),
                "rationale": rationale,
                "position": validation.get("position"),
                "validation": validation,
            }
        )
    ranked.sort(
        key=lambda item: (
            -float(item["likelihood"]),
            -float((item.get("validation") or {}).get("geometry_score", 0.0) or 0.0),
            item["symbol"],
        )
    )
    return ranked


def _site_geometry_score(coord_atoms, metal):
    """Reward sites where current donor distances are in metal-coordination range."""
    return float(_coordination_validation(coord_atoms, metal).get("geometry_score", 0.0) or 0.0)


def find_metal_candidates(session, *, model_hint=None, top_n=5,
                          cluster_cutoff=6.0, min_donors=3,
                          use_kvfinder=False):
    """Detect candidate metal-binding sites and rank them.

    Returns list of dicts:
      {
        'site_index': int,
        'model': AtomicStructure,
        'model_spec': '#1',
        'residues': [(spec, name), ...],
        'donor_atoms': [(atom_spec, donor_class), ...],
        'centroid': (x, y, z),
        'predictions': [(metal_symbol, prob, rationale), ...],
        'best_metal': str,
        'best_position': (x, y, z),
        'score': float,
      }
    """
    from .semantic import score_catalytic_residues, find_kvfinder_pockets

    structures = _selected_structures(session, model_hint=model_hint)
    if not structures:
        return []
    analysis_model_hint = model_hint
    if analysis_model_hint is None and len(structures) == 1:
        analysis_model_hint = f"#{getattr(structures[0], 'id_string', '?')}"

    catalytic_specs = set()
    try:
        for record in score_catalytic_residues(session, model_hint=analysis_model_hint) or []:
            spec = record.get("residue_spec") or record.get("spec")
            if spec:
                catalytic_specs.add(spec)
    except Exception:
        pass

    pocket_resspecs = set()
    if use_kvfinder:
        try:
            pockets = _run_on_ui_thread(
                session,
                lambda: find_kvfinder_pockets(session, model_hint=analysis_model_hint, top_n=8),
                timeout=240,
            )
            for p in pockets or []:
                for s in p.get("lining_specs", []) or []:
                    pocket_resspecs.add(s)
        except Exception:
            pass

    candidates = []
    for structure in structures:
        donors = _collect_donor_atoms(structure)
        if len(donors) < min_donors:
            continue
        existing_metals = _existing_metal_atoms(structure)
        clusters = _candidate_donor_windows(
            donors,
            cutoff=cluster_cutoff,
            min_residues=min_donors,
        )
        for cluster_indices in clusters:
            cluster = [donors[i] for i in cluster_indices]
            unique_residues = {_residue_key(r): r for _a, r, _c in cluster}
            if len(unique_residues) < min_donors:
                continue
            donor_classes_by_residue = {}
            for _a, r, c in cluster:
                donor_classes_by_residue.setdefault(_residue_key(r), []).append(c)
            centroid = _weighted_centroid(cluster)
            if centroid is None:
                continue
            base_preds = _predict_metal_type(donor_classes_by_residue)
            ranked_metals = _rank_metal_predictions(cluster, base_preds)
            if not ranked_metals:
                continue
            best_record = ranked_metals[0]
            best_metal = best_record["symbol"]
            metal_likelihood = best_record["likelihood"]
            refined = best_record.get("position") or _refine_position(centroid, cluster, best_metal)
            validation = dict(best_record.get("validation") or {})
            geom = float(validation.get("geometry_score", 0.0) or 0.0)
            preds = [
                (
                    item["symbol"],
                    item["likelihood"],
                    (
                        f"{item['rationale']}; geometry "
                        f"{float((item.get('validation') or {}).get('geometry_score', 0.0) or 0.0):.2f}"
                    ),
                )
                for item in ranked_metals
            ]
            res_specs = [_residue_spec(structure, r) for r in unique_residues.values()]
            cat_overlap = sum(1 for s in res_specs if s in catalytic_specs)
            pocket_overlap = sum(1 for s in res_specs if s in pocket_resspecs)
            nearest_existing = None
            if existing_metals:
                nearest_existing = min(
                    (
                        {
                            "label": metal["label"],
                            "element": metal["element"],
                            "distance": _dist(refined, metal["coord"]),
                            "coord": metal["coord"],
                            "spec": metal.get("spec", ""),
                        }
                        for metal in existing_metals
                    ),
                    key=lambda item: item["distance"],
                )
            if nearest_existing is not None and nearest_existing["distance"] <= 3.0:
                resolved_symbol = str(nearest_existing.get("element") or "").upper()
                if resolved_symbol in _METAL_COMPONENT_IDS:
                    preds = [
                        (
                            resolved_symbol,
                            1.0,
                            f"Resolved metal already present at this site ({nearest_existing['label']})",
                        )
                    ] + [p for p in preds if p[0] != resolved_symbol]
                    best_metal, metal_likelihood, _best_rat = preds[0]
                    refined = tuple(nearest_existing["coord"])
                    validation = _coordination_validation(cluster, resolved_symbol, position=refined)
                    geom = float(validation.get("geometry_score", 0.0) or 0.0)
            n = len(unique_residues)
            score = (
                0.20 * min(n / 4.0, 1.0)
                + 0.30 * geom
                + 0.30 * metal_likelihood
                + 0.10 * min(pocket_overlap / max(1, n), 1.0)
                + 0.10 * min(cat_overlap / max(1, n), 1.0)
            )
            if nearest_existing is not None and nearest_existing["distance"] <= 3.0:
                score = min(1.0, score + 0.15)
            if best_metal == "M2":
                score = min(score, 0.55)
            elif metal_likelihood < 0.45:
                score = min(score, 0.60)
            candidates.append({
                "model": structure,
                "model_spec": f"#{getattr(structure, 'id_string', '?')}",
                "residues": [(_residue_spec(structure, r), str(r.name).upper())
                             for r in unique_residues.values()],
                "donor_atoms": [(_atom_spec(structure, r, a), c)
                                for a, r, c in cluster],
                "centroid": centroid,
                "best_position": refined,
                "predictions": preds,
                "best_metal": best_metal,
                "score": score,
                "geometry_score": geom,
                "catalytic_overlap": cat_overlap,
                "pocket_overlap": pocket_overlap,
                "existing_metal_label": nearest_existing["label"] if nearest_existing and nearest_existing["distance"] <= 4.0 else None,
                "existing_metal_spec": nearest_existing["spec"] if nearest_existing and nearest_existing["distance"] <= 4.0 else None,
                "existing_metal_distance": nearest_existing["distance"] if nearest_existing and nearest_existing["distance"] <= 4.0 else None,
                "validation": validation,
            })
    candidates = _dedupe_candidates(candidates, limit=top_n)
    for i, c in enumerate(candidates, start=1):
        c["site_index"] = i
    return candidates


def _marker_set_for(session, *, name="predicted metals"):
    """Get or create the shared MarkerSet for metal preview/placement."""
    try:
        from chimerax.markers import MarkerSet
    except Exception:
        return None
    for child in session.models.list(type=MarkerSet):
        if getattr(child, "name", None) == name:
            return child
    ms = MarkerSet(session, name=name)
    session.models.add([ms])
    return ms


def place_metal_in_view(session, candidate, *, draw_guides=True,
                        marker_set_name="predicted metals",
                        pb_prefix="metal_coord", radius=0.55):
    """Add a marker for the predicted metal and pseudobond guides to coordinators.

    Returns (marker_spec, list_of_pseudobond_specs) or (None, []) on failure.
    """
    metal = candidate["best_metal"]
    pos = candidate["best_position"]
    marker_rgba, guide_rgba, style = _candidate_visual_rgba(candidate)
    marker_radius = float(radius) * (0.82 if style["tier"] == "low" else 0.92 if style["tier"] == "medium" else 1.0)

    ms = _marker_set_for(session, name=marker_set_name)
    if ms is None:
        return None, []

    try:
        # ChimeraX MarkerSet.create_marker(xyz, rgba, radius) — rgba positional, no kw
        marker = ms.create_marker(tuple(float(c) for c in pos), marker_rgba, marker_radius)
        try:
            marker.residue.name = metal
        except Exception:
            pass
    except Exception as err:
        try:
            session.logger.warning(f"metal: marker create failed: {err}")
        except Exception:
            pass
        return None, []

    site_index = candidate.get("site_index", 1)
    pb_group_name = f"{pb_prefix}_{site_index}_{metal}"
    pb_specs = []
    if draw_guides:
        try:
            pb_group = session.pb_manager.get_group(pb_group_name)
            # Newly-created pseudobond groups must be added to session.models
            # to render in the 3D view (chimerax.clashes / chimerax.struts pattern).
            try:
                if pb_group not in session.models.list():
                    session.models.add([pb_group])
            except Exception:
                pass
            try:
                pb_group.dashes = 6
            except Exception:
                pass
            for atom_spec, _cls in candidate["donor_atoms"]:
                atom = _resolve_single_atom(session, atom_spec)
                if atom is None:
                    continue
                try:
                    pb = pb_group.new_pseudobond(marker, atom)
                except Exception:
                    continue
                try:
                    pb.color = guide_rgba
                    pb.radius = float(style.get("guide_radius", 0.06))
                    pb.halfbond = False
                except Exception:
                    pass
                pb_specs.append(atom_spec)
        except Exception as err:
            try:
                session.logger.warning(f"metal: guide pseudobonds failed: {err}")
            except Exception:
                pass

    try:
        marker_spec = f"#{ms.id_string}:{int(marker.residue.number)}"
    except Exception:
        marker_spec = f"#{getattr(ms, 'id_string', '?')}"
    return marker_spec, pb_specs


def _hex_to_rgba8(hex_str):
    h = hex_str.lstrip("#")
    if len(h) == 6:
        r = int(h[0:2], 16)
        g = int(h[2:4], 16)
        b = int(h[4:6], 16)
        a = 255
    elif len(h) == 8:
        r = int(h[0:2], 16)
        g = int(h[2:4], 16)
        b = int(h[4:6], 16)
        a = int(h[6:8], 16)
    else:
        r = g = b = 128; a = 255
    return [r, g, b, a]


def _resolve_single_atom(session, atom_spec):
    try:
        from chimerax.atomic import AtomsArg
    except Exception:
        try:
            from chimerax.core.commands import AtomsArg
        except Exception:
            return None
    try:
        atoms, _used, _rest = AtomsArg.parse(atom_spec, session)
    except Exception:
        return None
    if atoms is None or len(atoms) == 0:
        return None
    return atoms[0]


def clear_predicted_metals(session):
    """Remove predicted/preview metal MarkerSets and coordination pseudobonds."""
    removed = 0
    try:
        from chimerax.markers import MarkerSet
        targets = [m for m in session.models.list(type=MarkerSet)
                   if getattr(m, "name", None) in {"predicted metals", "metal candidate preview"}]
        if targets:
            try:
                session.models.close(targets)
                removed += len(targets)
            except Exception:
                pass
    except Exception:
        pass
    # Pseudobond groups appear as Models too; close any whose name matches
    try:
        from chimerax.atomic import PseudobondGroup
        pb_targets = [g for g in session.models.list(type=PseudobondGroup)
                      if str(getattr(g, "name", "")).startswith(("metal_coord_", "metal_candidate_"))]
        if pb_targets:
            try:
                session.models.close(pb_targets)
                removed += len(pb_targets)
            except Exception:
                pass
    except Exception:
        pass
    return removed


def format_metal_report(session, *, model_hint=None, top_n=5, candidates=None):
    """Render a Markdown report of predicted metal sites.

    Pass `candidates` (output of find_metal_candidates) to avoid re-running
    the pipeline. Otherwise, the function will compute candidates fresh.
    """
    if candidates is None:
        candidates = find_metal_candidates(session, model_hint=model_hint, top_n=top_n)
    if not candidates:
        return (
            "No candidate metal-binding sites detected from the currently open ChimeraX "
            "AtomicStructure coordinates (need ≥3 side-chain donors within 6 Å)."
        )
    lines = [
        f"## Predicted metal-binding sites (top {len(candidates)})",
        "- Basis: current open ChimeraX AtomicStructure coordinates.",
        "- Ranking: local donor geometry, predicted metal chemistry, optional pocket overlap, and catalytic-like overlap.",
    ]
    for c in candidates:
        idx = c.get("site_index", "?")
        metal = _display_metal(c["best_metal"])
        score = c["score"]
        n_res = len(c["residues"])
        residues_text = ", ".join(spec for spec, _name in c["residues"])
        donors_text = ", ".join(f"{spec}/{cls}" for spec, cls in c["donor_atoms"][:8])
        preds_text = "; ".join(f"{_display_metal(m)} {p:.2f}" for m, p, _ in c["predictions"][:3])
        validation = c.get("validation") or {}
        validation_text = ""
        if validation:
            validation_text = (
                f"\n- Geometry validation: score {float(validation.get('geometry_score', 0.0) or 0.0):.2f}; "
                f"distance RMSD {float(validation.get('distance_rmsd', 0.0) or 0.0):.2f} Å; "
                f"acceptable donors {validation.get('acceptable_donors', 0)}/{validation.get('donor_count', 0)}; "
                f"{validation.get('flags', 'geometry ok')}"
            )
        existing_text = ""
        if c.get("existing_metal_label"):
            existing_text = (
                f"\n- Existing nearby metal: {c['existing_metal_label']} "
                f"({c.get('existing_metal_distance', 0.0):.2f} Å from refined position)"
            )
        lines.append(
            f"\n### Site {idx} — {metal} (score {score:.2f})"
            f"\n- {n_res} coordinating residues: {residues_text}"
            f"\n- Donor atoms: {donors_text}"
            f"\n- Position: ({c['best_position'][0]:.2f}, {c['best_position'][1]:.2f}, {c['best_position'][2]:.2f})"
            f"\n- Top metals: {preds_text}; geometry {c.get('geometry_score', 0.0):.2f}"
            f"{validation_text}"
            f"{existing_text}"
            f"\n- Rationale: {c['predictions'][0][2]}"
            f"\n- Place after review: /metal place site={idx}"
        )
    return "\n".join(lines)


def _is_qt_main_thread():
    try:
        from Qt.QtCore import QCoreApplication, QThread

        app = QCoreApplication.instance()
        return app is not None and QThread.currentThread() == app.thread()
    except Exception:
        return False


def _run_on_ui_thread(session, fn, *, timeout=60):
    if _is_qt_main_thread():
        return fn()
    ui = getattr(session, "ui", None)
    thread_safe = getattr(ui, "thread_safe", None)
    if not callable(thread_safe):
        return fn()

    result_box = {}
    done = threading.Event()

    def runner():
        try:
            result_box["result"] = fn()
        except Exception as err:
            result_box["error"] = err
        finally:
            done.set()

    try:
        thread_safe(runner)
    except Exception:
        return fn()
    if not done.wait(timeout):
        raise TimeoutError("metal placement UI operation timed out")
    if "error" in result_box:
        raise result_box["error"]
    return result_box.get("result")


def _run_chimerax_command(session, command, executor=None):
    if executor is not None:
        return executor(command)

    def _run_direct():
        from chimerax.core.commands import run

        return run(session, command)

    return _run_on_ui_thread(session, _run_direct)


def _candidate_residue_specs(candidate):
    return [spec for spec, _name in candidate.get("residues", []) if spec]


def _focus_metal_results(session, placed_items, *, executor=None, label_top=True,
                         preserve_camera=True):
    if not placed_items:
        return
    focus_specs = []
    residue_specs = []
    marker_specs = []
    for cand, marker_spec, _pb_specs in placed_items:
        if marker_spec:
            marker_specs.append(marker_spec)
            focus_specs.append(marker_spec)
        for spec in _candidate_residue_specs(cand):
            if spec not in residue_specs:
                residue_specs.append(spec)
            if spec not in focus_specs:
                focus_specs.append(spec)
    commands = []
    if residue_specs:
        spec_text = " ".join(residue_specs[:80])
        commands.extend([
            f"show {spec_text} atoms",
            f"style {spec_text} stick",
        ])
    if label_top and placed_items:
        label_specs = []
        for cand, _marker_spec, _pb_specs in placed_items:
            for spec in _candidate_residue_specs(cand):
                if spec not in label_specs:
                    label_specs.append(spec)
        if label_specs:
            commands.append("label " + " ".join(label_specs[:32]) + " residues")
    if marker_specs:
        commands.append("show " + " ".join(marker_specs) + " atoms")
    if focus_specs and not preserve_camera:
        commands.append("view " + " ".join(focus_specs[:40]))
    for command in commands:
        try:
            _run_chimerax_command(session, command, executor=executor)
        except Exception:
            pass
    if residue_specs:
        try:
            from .display_color import restore_charge_colors

            restore_charge_colors(session, " ".join(residue_specs[:80]))
        except Exception:
            pass


def _focus_existing_metal_candidates(session, candidates, *, executor=None,
                                     preserve_camera=True):
    specs = []
    for cand in candidates:
        metal_spec = cand.get("existing_metal_spec")
        if metal_spec and metal_spec not in specs:
            specs.append(metal_spec)
        for res_spec in _candidate_residue_specs(cand):
            if res_spec not in specs:
                specs.append(res_spec)
    if not specs:
        return
    commands = [
        "show " + " ".join(specs[:80]) + " atoms",
        "style " + " ".join(specs[:80]) + " stick",
    ]
    if not preserve_camera:
        commands.append("view " + " ".join(specs[:40]))
    for command in commands:
        try:
            _run_chimerax_command(session, command, executor=executor)
        except Exception:
            pass
    try:
        from .display_color import restore_charge_colors

        restore_charge_colors(session, " ".join(specs[:80]))
    except Exception:
        pass


def _focus_candidate_residue_clusters(session, candidates, *, executor=None,
                                      label_top=True, preserve_camera=True):
    residue_specs = []
    for cand in candidates:
        for spec in _candidate_residue_specs(cand):
            if spec not in residue_specs:
                residue_specs.append(spec)
    if not residue_specs:
        return
    spec_text = " ".join(residue_specs[:80])
    commands = [
        f"show {spec_text} atoms",
        f"style {spec_text} stick",
    ]
    if label_top:
        top_specs = _candidate_residue_specs(candidates[0])[:8]
        if top_specs:
            commands.append("label " + " ".join(top_specs) + " residues")
    if not preserve_camera:
        commands.append("view " + " ".join(residue_specs[:40]))
    for command in commands:
        try:
            _run_chimerax_command(session, command, executor=executor)
        except Exception:
            pass
    try:
        from .display_color import restore_charge_colors

        restore_charge_colors(session, spec_text)
    except Exception:
        pass


def _select_candidates_for_placement(candidates, *, show_all=False, top_n=1, site_index=None):
    if site_index is not None:
        try:
            index = int(site_index)
        except Exception:
            index = 1
        index = max(1, min(len(candidates), index))
        return [candidates[index - 1]]
    n_to_place = int(top_n) if show_all else 1
    return candidates[: max(1, min(len(candidates), n_to_place))]


def run_metal_placement_pipeline(session, *, model_hint=None, top_n=1,
                                 show_all=False, clear_existing=True,
                                 use_kvfinder=False, place=False,
                                 preview=False, site_index=None,
                                 draw_guides=True, executor=None,
                                 candidates=None, preserve_camera=True):
    """Find coordination residues, refine virtual-metal positions, and optionally place markers."""
    try:
        requested_top = max(1, min(20, int(top_n)))
    except Exception:
        requested_top = 1

    scan_n = max(requested_top, 5)
    if candidates is None:
        candidates = find_metal_candidates(
            session,
            model_hint=model_hint,
            top_n=scan_n,
            use_kvfinder=use_kvfinder,
        )
    else:
        candidates = list(candidates)
        scan_n = max(scan_n, len(candidates))
        for i, c in enumerate(candidates, start=1):
            c.setdefault("site_index", i)
    if not candidates:
        return "No candidate metal-binding sites detected (need >=3 side-chain donors within 6 A)."

    if not place and not preview:
        return format_metal_report(session, top_n=scan_n, candidates=candidates)

    selected_candidates = _select_candidates_for_placement(
        candidates,
        show_all=show_all or (preview and not place),
        top_n=requested_top,
        site_index=site_index,
    )

    def _place():
        if clear_existing:
            clear_predicted_metals(session)
        placed_items = []
        skipped_existing = []
        for cand in selected_candidates:
            if cand.get("existing_metal_label") and float(cand.get("existing_metal_distance", 999.0)) <= 2.5:
                skipped_existing.append(cand)
                continue
            marker_spec, pb_specs = place_metal_in_view(
                session,
                cand,
                draw_guides=draw_guides,
                marker_set_name="predicted metals" if place else "metal candidate preview",
                pb_prefix="metal_coord" if place else "metal_candidate",
                radius=0.55 if place else 0.38,
            )
            if marker_spec is not None:
                placed_items.append((cand, marker_spec, pb_specs))
        return placed_items, skipped_existing

    placed, skipped_existing = _run_on_ui_thread(session, _place)

    if placed:
        _focus_metal_results(
            session,
            placed,
            executor=executor,
            label_top=True,
            preserve_camera=preserve_camera,
        )
    elif skipped_existing:
        _focus_existing_metal_candidates(
            session,
            skipped_existing,
            executor=executor,
            preserve_camera=preserve_camera,
        )

    lines = [
        "[metal] Placement pipeline complete." if place
        else "[metal] Candidate preview rendered in the 3D view (no final metal insertion)."
    ]
    if skipped_existing:
        for cand in skipped_existing:
            idx = cand.get("site_index", "?")
            lines.append(
                f"  Site {idx} already has resolved metal {cand.get('existing_metal_label')} "
                f"within {cand.get('existing_metal_distance', 0.0):.2f} Å; skipped duplicate virtual marker."
            )
    if not placed:
        if skipped_existing:
            lines.append("[metal] No virtual marker was inserted because the selected site already contains a metal.")
        else:
            lines.append("[metal] Candidate sites were found, but marker rendering failed.")
    for cand, marker_spec, pb_specs in placed:
        idx = cand.get("site_index", "?")
        metal = cand["best_metal"]
        metal_label = _display_metal(metal)
        style = _candidate_confidence_style(cand)
        x, y, z = cand["best_position"]
        residues = ", ".join(spec for spec, _ in cand["residues"])
        validation = cand.get("validation") or {}
        lines.append(
            f"  Site {idx} -> {metal_label} marker {marker_spec} "
            f"at ({x:.2f}, {y:.2f}, {z:.2f}); score {cand['score']:.2f}; "
            f"visual confidence {style['label']} "
            f"(review {float(style.get('score', 0.0) or 0.0):.2f}, alpha {int(style.get('alpha', 0))}/255); "
            f"coordination residues: {residues}"
        )
        if validation:
            lines.append(
                f"  Validation: geometry {float(validation.get('geometry_score', 0.0) or 0.0):.2f}; "
                f"distance RMSD {float(validation.get('distance_rmsd', 0.0) or 0.0):.2f} A; "
                f"acceptable donors {validation.get('acceptable_donors', 0)}/{validation.get('donor_count', 0)}; "
                f"{validation.get('flags', 'geometry ok')}"
            )
        if draw_guides:
            lines.append(f"  Coordination guide bonds: {len(pb_specs)}")
    if len(candidates) > len(selected_candidates):
        lines.append(
            f"  {len(candidates) - len(selected_candidates)} additional candidate(s) available; "
            "use /metal predict all to preview more or /metal place site=N after reviewing."
        )
    if not place:
        lines.append("  To insert one reviewed virtual metal, run /metal place site=N (for example: /metal place site=1).")
    lines.append("")
    lines.append(format_metal_report(session, top_n=scan_n, candidates=candidates))
    return "\n".join(lines)


def run_metal_review_pipeline(session, *, model_hint=None, top_n=5,
                              include_rcsb=True, rows=20,
                              identity_cutoff=0.30,
                              min_tier="low",
                              use_kvfinder=True,
                              clear_existing=True,
                              preview=True,
                              executor=None,
                              preserve_camera=True):
    """Run slower evidence-gated metal analysis and render only reviewed candidates."""
    started = time.time()
    try:
        requested_top = max(1, min(20, int(top_n)))
    except Exception:
        requested_top = 5
    scan_n = max(requested_top, 10)

    fold_evidence = None
    if include_rcsb:
        fold_evidence = collect_fold_metal_evidence(
            session,
            model_hint=model_hint,
            rows=rows,
            identity_cutoff=identity_cutoff,
            max_chains=4,
        )

    candidates = find_metal_candidates(
        session,
        model_hint=model_hint,
        top_n=scan_n,
        use_kvfinder=use_kvfinder,
    )
    if len(candidates) < requested_top:
        relaxed_candidates = find_metal_candidates(
            session,
            model_hint=model_hint,
            top_n=scan_n,
            cluster_cutoff=7.5,
            use_kvfinder=use_kvfinder,
        )
        for candidate in relaxed_candidates:
            candidate["relaxed_scan"] = True
            candidate["score"] = min(float(candidate.get("score", 0.0) or 0.0), 0.55)
        candidates = _merge_metal_candidate_lists(candidates, relaxed_candidates, limit=scan_n)
    if not candidates:
        return (
            "No candidate metal-binding sites detected after the full review "
            "(need >=3 side-chain donors within the tested 6.0-7.5 A local donor radii)."
        )

    annotated = _annotate_and_sort_candidates(candidates, fold_evidence=fold_evidence)
    session._codex_last_metal_candidates = annotated
    listed_candidates = annotated[:requested_top]

    eligible = [
        c for c in listed_candidates
        if _tier_value(c.get("review_tier")) >= _tier_value(min_tier)
    ]
    preview_summary = ""
    if preview and eligible:
        preview_summary = run_metal_placement_pipeline(
            session,
            model_hint=model_hint,
            top_n=len(eligible),
            show_all=True,
            clear_existing=clear_existing,
            use_kvfinder=use_kvfinder,
            place=False,
            preview=True,
            draw_guides=True,
            executor=executor,
            candidates=eligible,
            preserve_camera=preserve_camera,
        )
    else:
        if clear_existing:
            try:
                clear_predicted_metals(session)
            except Exception:
                pass
        _focus_candidate_residue_clusters(
            session,
            listed_candidates[:max(1, min(requested_top, 3))],
            executor=executor,
            preserve_camera=preserve_camera,
        )

    elapsed = time.time() - started
    report = _format_metal_review_report(
        listed_candidates,
        fold_evidence=fold_evidence,
        elapsed=elapsed,
        min_tier=min_tier,
        requested_top=requested_top,
        detected_count=len(annotated),
        preview_count=len(eligible) if preview else 0,
    )
    if include_rcsb:
        report += "\n\n" + format_fold_metal_evidence_report(
            session,
            model_hint=model_hint,
            rows=rows,
            identity_cutoff=identity_cutoff,
            evidence=fold_evidence,
        )
    if preview_summary:
        preview_lines = []
        for line in str(preview_summary).splitlines():
            if (
                "visual confidence" in line
                or "Coordination guide bonds" in line
                or "Candidate preview rendered" in line
            ):
                preview_lines.append(line)
        if preview_lines:
            report += "\n\n3D preview styling\n" + "\n".join(preview_lines)
    return report


def cmd_metal_place(session, top_n: int = 1, model: str = None,
                    show_all: bool = False, clear: bool = False,
                    use_kvfinder: bool = True, site_index: int = None):
    """`metal place` slash command body.

    top_n: number of top sites to render in the 3D view (default 1).
    show_all: if True, place top_n sites; if False, place only the best.
    clear: remove previously placed predicted-metal markers first.
    use_kvfinder: include parKVFinder pocket-overlap bonus (slower, more accurate).
    """
    if clear:
        n = clear_predicted_metals(session)
        session.logger.info(f"[metal] cleared {n} predicted-metal model(s)/pseudobond group(s).")
        return

    message = run_metal_placement_pipeline(
        session,
        model_hint=model,
        top_n=top_n,
        show_all=show_all,
        clear_existing=True,
        use_kvfinder=use_kvfinder,
        place=True,
        preview=False,
        site_index=site_index,
        draw_guides=True,
    )
    if "No candidate" in message:
        session.logger.warning(f"[metal] {message}")
        return message
    lines = message.splitlines()
    for line in lines:
        session.logger.info(line)
    return message


def cmd_metal_predict(session, top_n: int = 5, model: str = None,
                      use_kvfinder: bool = True, preview: bool = True):
    """Report candidate metal sites and optionally render review markers."""
    message = run_metal_placement_pipeline(
        session,
        model_hint=model,
        top_n=top_n,
        show_all=True,
        use_kvfinder=use_kvfinder,
        place=False,
        preview=preview,
        draw_guides=True,
    )
    for line in message.splitlines():
        session.logger.info(line)
    return message


def cmd_metal_evidence(session, top_n: int = 5, model: str = None,
                       rows: int = 12):
    """Report current metals, local candidates, and RCSB homolog metal evidence."""
    message = format_metal_evidence_report(
        session,
        model_hint=model,
        top_n=top_n,
        include_rcsb=True,
        rows=rows,
    )
    for line in message.splitlines():
        session.logger.info(line)
    return message


def register_metal_commands(bundle_api, command_name, logger):
    from chimerax.core.commands import CmdDesc, register, IntArg, StringArg, BoolArg

    if command_name == "metal place":
        desc = CmdDesc(
            optional=[("top_n", IntArg)],
            keyword=[
                ("model", StringArg),
                ("show_all", BoolArg),
                ("clear", BoolArg),
                ("use_kvfinder", BoolArg),
                ("site_index", IntArg),
            ],
            synopsis="Predict and place virtual metal ions at the best coordination sites",
        )
        register("metal place", desc, cmd_metal_place, logger=logger)
    elif command_name == "metal predict":
        desc = CmdDesc(
            optional=[("top_n", IntArg)],
            keyword=[
                ("model", StringArg),
                ("use_kvfinder", BoolArg),
                ("preview", BoolArg),
            ],
            synopsis="Report predicted metal-binding candidates without placing markers",
        )
        register("metal predict", desc, cmd_metal_predict, logger=logger)
    elif command_name == "metal evidence":
        desc = CmdDesc(
            optional=[("top_n", IntArg)],
            keyword=[
                ("model", StringArg),
                ("rows", IntArg),
            ],
            synopsis="Check current metals, predicted candidates, and RCSB homolog metal evidence",
        )
        register("metal evidence", desc, cmd_metal_evidence, logger=logger)
    elif command_name == "metal clear":
        def _clear(session):
            n = clear_predicted_metals(session)
            session.logger.info(f"[metal] cleared {n} predicted-metal model(s)/pseudobond group(s).")
        desc = CmdDesc(synopsis="Clear all predicted metal markers and coordination guides")
        register("metal clear", desc, _clear, logger=logger)
