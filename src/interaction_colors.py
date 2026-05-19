"""Pastel-colored interaction pseudobonds for enzyme/ligand pairs.

Each interaction class lives in its OWN named pseudobond group so the user
can toggle / hide / colour them independently from the ChimeraX Models panel.
That fixes the previous design where every contact ended up in the single
``contacts`` group with per-bond colours -- which looked correct on screen
but presented as one indivisible model.

Pipeline
========

1.  ``hbonds`` stock command (``hydrogen bonds`` group, all atoms incl.
    backbone -- backbone-mediated H-bonds are real and biologically
    meaningful so we keep them).
2.  ``contacts (enzyme) restrict (ligand) distanceOnly ...`` stock command
    (``distances`` group). Backbone atoms are kept so backbone-mediated
    interactions are not lost; the visual clutter that previously came from
    backbone contacts is killed in the split step instead, by (a) dropping
    any contact that's already drawn as an H-bond, and (b) only keeping
    contacts in the hydrophobic bucket when both atoms are carbon.
3.  Walk the ``distances`` group; classify each pseudobond by atom
    chemistry and move it into one of three codex-managed groups:
      * ``salt bridge``    -> pastel magenta
      * ``pi-stacking``    -> pastel green
      * ``hydrophobic``    -> pastel gray
    The source ``distances`` group is then deleted so it doesn't double-up.

Re-runs delete the codex groups first, so toggling the Interface action with
different cutoffs doesn't pile up stale pseudobonds.
"""
from __future__ import annotations


# Canonical PyMOL/PLIP interaction hues, desaturated into pastel but kept
# saturated enough to read against a white background. Earlier #cfcfcf for
# the hydrophobic group was effectively invisible on the publication-style
# white scene; #999999 keeps the muted look while staying legible.
PASTEL_HBOND = (231, 196, 78, 255)         # #e7c44e   warm yellow
PASTEL_SALT_BRIDGE = (217, 110, 165, 255)  # #d96ea5   magenta
PASTEL_PI_STACKING = (114, 191, 137, 255)  # #72bf89   green
PASTEL_HYDROPHOBIC = (153, 153, 153, 255)  # #999999   gray
PASTEL_CATION_PI = (164, 124, 198, 255)    # #a47cc6   violet
PASTEL_HALOGEN = (110, 186, 199, 255)      # #6ebac7   teal

# Uniform thickness across every interaction class so the eye reads the lines
# as the same kind of visual element regardless of chemistry. Tune in one place.
PSEUDOBOND_RADIUS = 0.10

# Pseudobond group names. These appear verbatim in the Models panel.
HBOND_GROUP_NAME = "hydrogen bonds"   # set by stock `hbonds` command
SALT_GROUP_NAME = "salt bridge"
PI_GROUP_NAME = "pi-stacking"
HYDROPHOBIC_GROUP_NAME = "hydrophobic"
CATION_PI_GROUP_NAME = "cation-pi"
HALOGEN_GROUP_NAME = "halogen bond"

# Atom chemistry tables ------------------------------------------------------

_POSITIVE_ATOMS_BY_RES = {
    "ARG": {"NH1", "NH2", "NE"},
    "LYS": {"NZ"},
    "HIS": {"ND1", "NE2"},  # ambiguous - included for completeness
}
_NEGATIVE_ATOMS_BY_RES = {
    "ASP": {"OD1", "OD2"},
    "GLU": {"OE1", "OE2"},
}
_AROMATIC_ATOMS_BY_RES = {
    "PHE": {"CG", "CD1", "CD2", "CE1", "CE2", "CZ"},
    "TYR": {"CG", "CD1", "CD2", "CE1", "CE2", "CZ"},
    "TRP": {"CG", "CD1", "CD2", "NE1", "CE2", "CE3", "CZ2", "CZ3", "CH2"},
    "HIS": {"CG", "ND1", "CD2", "CE1", "NE2"},
}

# IDATM types for arbitrary ligand atoms.
_AROMATIC_IDATM = {"Car", "Npl"}
_POSITIVE_IDATM = {"N3+", "Ng+"}
_NEGATIVE_IDATM = {"O2-", "O3-"}

# Halogen-bond geometry. Halogen atoms with non-trivial sigma-hole are
# Cl/Br/I (and weakly F). Acceptors are protein O / N lone-pair atoms.
_HALOGEN_ELEMENTS = {"F", "Cl", "Br", "I"}
_HBOND_ACCEPTOR_ELEMENTS = {"O", "N", "S"}

# Per-interaction maximum distance (Angstrom). PLIP/Maestro defaults.
# Hydrophobic uses the user-picked cutoff so the dialog still controls
# packing density; the others use chemistry-appropriate ceilings so they
# don't get clipped just because the user picked a tight hydrophobic
# cutoff.
SALT_MAX_DIST = 5.5
CATION_PI_MAX_DIST = 6.0
PI_MAX_DIST = 5.5
_HALOGEN_BOND_MAX_DIST = 3.7

# Internal `contacts ... distanceOnly` cutoff. We run the stock command at
# the loosest of the per-type cutoffs so every candidate makes it into the
# pseudobond group, then we filter per type in the splitter.
INTERNAL_CONTACTS_CUTOFF = max(SALT_MAX_DIST, CATION_PI_MAX_DIST, PI_MAX_DIST, _HALOGEN_BOND_MAX_DIST)


def _rgba_to_hex(rgba):
    r, g, b, _ = rgba
    return f"#{r:02x}{g:02x}{b:02x}"


def _atom_residue_name(atom):
    res = getattr(atom, "residue", None)
    return getattr(res, "name", "") if res is not None else ""


def _atom_is_positive(atom):
    if atom.name in _POSITIVE_ATOMS_BY_RES.get(_atom_residue_name(atom), set()):
        return True
    return (getattr(atom, "idatm_type", "") or "") in _POSITIVE_IDATM


def _atom_is_negative(atom):
    if atom.name in _NEGATIVE_ATOMS_BY_RES.get(_atom_residue_name(atom), set()):
        return True
    return (getattr(atom, "idatm_type", "") or "") in _NEGATIVE_IDATM


def _atom_is_aromatic(atom):
    if atom.name in _AROMATIC_ATOMS_BY_RES.get(_atom_residue_name(atom), set()):
        return True
    return (getattr(atom, "idatm_type", "") or "") in _AROMATIC_IDATM


def _is_carbon_carbon(a1, a2):
    """Both atoms must be plain carbon to count as a hydrophobic contact.

    Mixed pairs (C-O, C-N, N-O, ...) are usually polar proximity, not
    hydrophobic packing -- excluding them is what removes most of the
    visual clutter from the previous version.
    """
    try:
        return a1.element.name == "C" and a2.element.name == "C"
    except Exception:
        return False


def _atom_key(atom):
    """Stable hashable key for a ChimeraX Atom.

    ``pb.atoms`` returns fresh Python wrappers around the underlying C++
    atoms each call, so ``id(atom)`` differs even when two pseudobonds
    reference the same physical atom. We key on
    ``(id(parent structure), serial_number)`` instead, both of which are
    stable for the lifetime of the loaded model.
    """
    try:
        return (id(atom.structure), int(atom.serial_number))
    except Exception:
        return (None, id(atom))


def _hbond_atom_pair_keys(pb_mgr):
    """Return a set of frozenset({atom_key(a1), atom_key(a2)}) for every
    pseudobond in the stock "hydrogen bonds" group. Used to dedup contacts
    pseudobonds whose endpoints are already drawn as an H-bond.
    """
    keys = set()
    try:
        group = pb_mgr.get_group(HBOND_GROUP_NAME, create=False)
    except Exception:
        return keys
    if group is None:
        return keys
    try:
        bonds = list(group.pseudobonds)
    except Exception:
        return keys
    for pb in bonds:
        try:
            a1, a2 = pb.atoms
            keys.add(frozenset((_atom_key(a1), _atom_key(a2))))
        except Exception:
            continue
    return keys


def _atom_element(atom):
    try:
        return str(atom.element.name)
    except Exception:
        return ""


def _atom_is_halogen(atom):
    return _atom_element(atom) in _HALOGEN_ELEMENTS


def _atom_is_hbond_acceptor(atom):
    """Cheap acceptor check: any O / N / S (lone-pair carriers in protein)."""
    return _atom_element(atom) in _HBOND_ACCEPTOR_ELEMENTS


def _classify_pair(a1, a2):
    """Classify an atom pair into the interaction category that best describes
    its chemistry. Returns one of:
        'salt'        - opposing formal charges
        'cation_pi'   - cation atom near aromatic ring atom
        'halogen'     - ligand halogen near protein H-bond acceptor
        'pi'          - both atoms aromatic
        'hydrophobic' - default (filtered to C-C only at the splitter stage)
    """
    pos1 = _atom_is_positive(a1)
    pos2 = _atom_is_positive(a2)
    neg1 = _atom_is_negative(a1)
    neg2 = _atom_is_negative(a2)
    if (pos1 and neg2) or (pos2 and neg1):
        return "salt"

    arom1 = _atom_is_aromatic(a1)
    arom2 = _atom_is_aromatic(a2)
    # Cation-pi takes precedence over plain pi when one side is a charged
    # nitrogen (Lys NZ, Arg NE/NH, ammonium-like ligand atoms).
    if (pos1 and arom2) or (pos2 and arom1):
        return "cation_pi"

    hal1 = _atom_is_halogen(a1)
    hal2 = _atom_is_halogen(a2)
    if (hal1 and _atom_is_hbond_acceptor(a2)) or (hal2 and _atom_is_hbond_acceptor(a1)):
        return "halogen"

    if arom1 and arom2:
        return "pi"
    return "hydrophobic"


# Public entrypoint ----------------------------------------------------------

def apply_interaction_coloring(session, enzyme_spec, ligand_spec, cutoff=4.5, executor=None):
    """Build H-bond + salt + pi-stacking + hydrophobic pseudobond groups.

    Returns a list of ChimeraX command strings + summary lines (for logging).
    """
    from chimerax.core.commands import run as _cmd_run

    if executor is not None:
        def run(cmd):
            return executor(cmd)
    else:
        def run(cmd):
            return _cmd_run(session, cmd)

    enzyme = enzyme_spec.strip()
    ligand = ligand_spec.strip()
    try:
        cutoff_val = float(cutoff)
    except (TypeError, ValueError):
        cutoff_val = 4.5
    cutoff_val = max(2.5, min(8.0, cutoff_val))

    # Clear any prior run so re-clicking doesn't stack pseudobonds.
    clear_codex_interactions(session, executor=executor)

    executed = []

    # 1) H-bonds: keep both backbone and sidechain donors/acceptors -- those
    # are real interactions even from the protein backbone. The slop
    # parameters relax the default 3.5 Å / 120 deg cut just enough to catch
    # marginal weak H-bonds that the user can see by eye but ChimeraX's
    # default filter rejects.
    hb_color = _rgba_to_hex(PASTEL_HBOND)
    cmd_hb = (
        f"hbonds ({enzyme}) restrict ({ligand}) reveal true "
        f"color {hb_color} radius {PSEUDOBOND_RADIUS:.2f} dashes 6 "
        f"distSlop 0.5 angleSlop 25"
    )
    try:
        run(cmd_hb)
        executed.append(cmd_hb)
    except Exception as err:
        try:
            session.logger.warning(f"hbonds step failed: {err}")
        except Exception:
            pass

    # 2) Contacts at the union of all per-type cutoffs (currently 6 Å for
    # cation-pi). The split step then filters each candidate against its
    # type-specific max distance, so e.g. a 5.0 Å salt bridge is kept while
    # a 5.5 Å C-C hydrophobic stays filtered out by the user's 4.5 Å pick.
    internal_cutoff = max(cutoff_val, INTERNAL_CONTACTS_CUTOFF)
    hphob_color = _rgba_to_hex(PASTEL_HYDROPHOBIC)
    cmd_cont = (
        f"contacts ({enzyme}) restrict ({ligand}) reveal true "
        f"color {hphob_color} radius 0.10 dashes 6 "
        f"distanceOnly {internal_cutoff:.2f}"
    )
    try:
        run(cmd_cont)
        executed.append(cmd_cont)
    except Exception as err:
        try:
            session.logger.warning(f"contacts step failed: {err}")
        except Exception:
            pass

    # 3) Split the contacts pseudobonds into named per-class groups so each
    # interaction type is its own toggleable model in the Models panel.
    counts = _split_contacts_into_codex_groups(session, hydrophobic_cutoff=cutoff_val)
    if counts:
        executed.append(
            f"# split contacts: salt={counts['salt']}, "
            f"pi={counts['pi']}, hydrophobic={counts['hydrophobic']}"
        )
    return executed


def _split_contacts_into_codex_groups(session, hydrophobic_cutoff=4.5):
    """Move pseudobonds out of the stock contacts/distances group into named
    codex groups. Returns a dict of counts per class.

    Rules:
      - Skip any contact whose atom pair is already in the "hydrogen bonds"
        group (so we don't double-draw H-bonds as both yellow and gray).
      - Salt bridge: positive <-> negative atom.
      - Pi-stacking: both atoms aromatic.
      - Hydrophobic: both atoms are carbon. Pairs with any N/O/S go in the
        bin, not the hydrophobic group, because they're proximity artefacts
        rather than real packing contacts.
    """
    pb_mgr = getattr(session, "pb_manager", None)
    if pb_mgr is None:
        return None

    src = None
    for src_name in ("distances", "contacts"):
        try:
            cand = pb_mgr.get_group(src_name, create=False)
        except Exception:
            cand = None
        if cand is not None:
            src = cand
            break
    if src is None:
        return None

    # Build a set of frozenset({atom_id, atom_id}) for every H-bond pair so we
    # can dedup against it.
    hbond_pairs = _hbond_atom_pair_keys(pb_mgr)

    salt_group = _ensure_group(pb_mgr, SALT_GROUP_NAME, PASTEL_SALT_BRIDGE, radius=PSEUDOBOND_RADIUS, session=session)
    pi_group = _ensure_group(pb_mgr, PI_GROUP_NAME, PASTEL_PI_STACKING, radius=PSEUDOBOND_RADIUS, session=session)
    hphob_group = _ensure_group(pb_mgr, HYDROPHOBIC_GROUP_NAME, PASTEL_HYDROPHOBIC, radius=PSEUDOBOND_RADIUS, session=session)
    cation_pi_group = _ensure_group(pb_mgr, CATION_PI_GROUP_NAME, PASTEL_CATION_PI, radius=PSEUDOBOND_RADIUS, session=session)
    halogen_group = _ensure_group(pb_mgr, HALOGEN_GROUP_NAME, PASTEL_HALOGEN, radius=PSEUDOBOND_RADIUS, session=session)

    counts = {
        "salt": 0,
        "pi": 0,
        "cation_pi": 0,
        "halogen": 0,
        "hydrophobic": 0,
        "skipped_hbond": 0,
        "skipped_polar_noise": 0,
    }

    # Per-residue-pair best (shortest) hydrophobic candidate so the
    # hydrophobic group stays at "moderate" density (one line per pocket
    # residue) instead of one per atom-pair. Salt and pi keep every atom
    # pair because those are chemically specific.
    hphob_best: dict[frozenset, tuple] = {}

    try:
        bonds = list(src.pseudobonds)
    except Exception:
        bonds = []
    for pb in bonds:
        try:
            atoms = pb.atoms
            if len(atoms) != 2:
                continue
            a1, a2 = atoms[0], atoms[1]
            if frozenset((_atom_key(a1), _atom_key(a2))) in hbond_pairs:
                counts["skipped_hbond"] += 1
                continue
            kind = _classify_pair(a1, a2)

            try:
                length = float(pb.length)
            except Exception:
                length = 0.0

            if kind == "salt":
                if length > SALT_MAX_DIST:
                    counts["skipped_polar_noise"] += 1
                    continue
                try:
                    salt_group.new_pseudobond(a1, a2)
                    counts["salt"] += 1
                except Exception:
                    pass
                continue

            if kind == "cation_pi":
                if length > CATION_PI_MAX_DIST:
                    counts["skipped_polar_noise"] += 1
                    continue
                try:
                    cation_pi_group.new_pseudobond(a1, a2)
                    counts["cation_pi"] += 1
                except Exception:
                    pass
                continue

            if kind == "halogen":
                if length > _HALOGEN_BOND_MAX_DIST:
                    counts["skipped_polar_noise"] += 1
                    continue
                try:
                    halogen_group.new_pseudobond(a1, a2)
                    counts["halogen"] += 1
                except Exception:
                    pass
                continue

            if kind == "pi":
                if length > PI_MAX_DIST:
                    counts["skipped_polar_noise"] += 1
                    continue
                try:
                    pi_group.new_pseudobond(a1, a2)
                    counts["pi"] += 1
                except Exception:
                    pass
                continue

            # Default bucket. Drop polar proximity entirely (those are
            # mostly meaningless backbone-vs-ligand N/O neighbours, not
            # interactions); keep only true C-C hydrophobic packing, and
            # only inside the user's hydrophobic cutoff (the contacts cmd
            # was run wider so other types still see their longer-range
            # candidates).
            if not _is_carbon_carbon(a1, a2):
                counts["skipped_polar_noise"] += 1
                continue
            if length > hydrophobic_cutoff:
                counts["skipped_polar_noise"] += 1
                continue
            res_key = frozenset((id(a1.residue), id(a2.residue)))
            existing = hphob_best.get(res_key)
            if existing is None or length < existing[2]:
                hphob_best[res_key] = (a1, a2, length)
        except Exception:
            continue

    for a1, a2, _length in hphob_best.values():
        try:
            hphob_group.new_pseudobond(a1, a2)
            counts["hydrophobic"] += 1
        except Exception:
            continue

    try:
        session.logger.info(
            f"Codex interactions: salt={counts['salt']}, "
            f"cation-pi={counts['cation_pi']}, halogen={counts['halogen']}, "
            f"pi={counts['pi']}, hydrophobic={counts['hydrophobic']} "
            f"(skipped hbond-dup={counts['skipped_hbond']}, "
            f"polar-noise={counts['skipped_polar_noise']})"
        )
    except Exception:
        pass

    # Diagnostic: list the codex pseudobond groups currently registered with
    # the session so we can confirm they reach the Models panel.
    try:
        registered = []
        for g in (salt_group, cation_pi_group, halogen_group, pi_group, hphob_group):
            if g is None:
                continue
            try:
                n_pbs = len(g.pseudobonds)
            except Exception:
                n_pbs = -1
            try:
                in_models = g in session.models
            except Exception:
                in_models = "?"
            try:
                disp = bool(getattr(g, "display", True))
            except Exception:
                disp = "?"
            registered.append(f"{g.name}: pbs={n_pbs}, in_models={in_models}, display={disp}")
        if registered:
            session.logger.info("Codex pb groups -> " + " | ".join(registered))
    except Exception:
        pass

    # Remove the now-redundant stock contacts group.
    try:
        if hasattr(src, "delete"):
            src.delete()
        elif hasattr(pb_mgr, "delete_group"):
            pb_mgr.delete_group(src)
    except Exception:
        pass

    # If a group ended up empty, remove it so it doesn't clutter the
    # Models panel with an empty entry.
    for group in (salt_group, pi_group, hphob_group, cation_pi_group, halogen_group):
        try:
            if group is not None and len(group.pseudobonds) == 0:
                if hasattr(group, "delete"):
                    group.delete()
                elif hasattr(pb_mgr, "delete_group"):
                    pb_mgr.delete_group(group)
        except Exception:
            continue

    return counts


def _ensure_group(pb_mgr, name, color_rgba, radius=0.13, *, session=None):
    """Create-or-fetch a pseudobond group, register it with the session's
    Models panel, and prime its display attributes.

    Some ChimeraX builds skip the session.models.add step when
    ``get_group(create=True)`` builds a brand-new global pb group, which
    leaves the group invisible in the Models panel even though pseudobonds
    are added to it successfully. The explicit add below guarantees the
    group shows up; it's a no-op if the group is already registered.
    """
    try:
        group = pb_mgr.get_group(name, create=True)
    except Exception:
        return None
    if group is None:
        return None
    sess = session if session is not None else getattr(pb_mgr, "session", None)
    if sess is not None:
        try:
            if group not in sess.models:
                sess.models.add([group])
        except Exception:
            pass
    try:
        group.display = True
    except Exception:
        pass
    try:
        group.color = color_rgba
    except Exception:
        pass
    try:
        group.radius = float(radius)
    except Exception:
        pass
    try:
        # Dashed lines so the user can still distinguish pseudobonds from
        # real bonds when they're rendered close together.
        group.dashes = 6
    except Exception:
        pass
    return group


def clear_codex_interactions(session, executor=None):
    """Remove all Codex Bridge interaction pseudobonds from the scene.

    Wipes the stock ``hydrogen bonds`` / ``contacts`` / ``distances`` groups
    *and* the three codex-managed groups, so the next coloring pass starts
    from a clean slate.
    """
    from chimerax.core.commands import run as _cmd_run

    if executor is not None:
        def run(cmd):
            return executor(cmd)
    else:
        def run(cmd):
            return _cmd_run(session, cmd)

    # Clear stock-named groups via their CLI inverse commands.
    for cmd in ("~hbonds", "~contacts", "~distances"):
        try:
            run(cmd)
        except Exception:
            pass

    # Clear codex-managed groups by deleting them outright.
    pb_mgr = getattr(session, "pb_manager", None)
    if pb_mgr is None:
        return
    for name in (
        SALT_GROUP_NAME,
        PI_GROUP_NAME,
        HYDROPHOBIC_GROUP_NAME,
        CATION_PI_GROUP_NAME,
        HALOGEN_GROUP_NAME,
    ):
        try:
            group = pb_mgr.get_group(name, create=False)
        except Exception:
            group = None
        if group is None:
            continue
        try:
            if hasattr(group, "delete"):
                group.delete()
            elif hasattr(pb_mgr, "delete_group"):
                pb_mgr.delete_group(group)
        except Exception:
            pass
