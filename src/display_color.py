import re

from chimerax.core.commands import ObjectsArg


PROTEIN_BACKBONE_ATOM_NAMES = {
    "N",
    "C",
    "O",
    "OXT",
    "H",
    "H1",
    "H2",
    "H3",
}


def objects_for_spec(session, spec):
    objects, _used, _rest = ObjectsArg.parse(str(spec or "").strip(), session)
    return objects


def apply_stick_context_colors(session, spec):
    """Color a residue's stick atoms in 'cartoon-context' style:
       - Carbons inherit the residue's ribbon (cartoon) color so the stick
         backbone visually matches the surrounding cartoon strip.
       - Heteroatoms (N, O, S, P, halogens, ...) get standard element colors
         (red O, blue N, yellow S, etc.) for chemical readability."""
    from chimerax.atomic.colors import element_colors

    objects = objects_for_spec(session, spec)
    atoms = getattr(objects, "atoms", None)
    if atoms is None or len(atoms) == 0:
        return False

    colors = atoms.colors.copy()

    try:
        ribbon_colors = atoms.residues.ribbon_colors
        if ribbon_colors is not None and len(ribbon_colors) == len(atoms):
            colors[:, :3] = ribbon_colors[:, :3]
    except Exception:
        pass

    hetero_mask = atoms.element_numbers != 6
    if hetero_mask.any():
        hetero_colors = element_colors(atoms.element_numbers[hetero_mask])
        hetero_colors[:, 3] = colors[hetero_mask, 3]
        colors[hetero_mask, :] = hetero_colors

    atoms.colors = colors
    return True


def ensure_stick_cartoon_anchor(session, spec):
    """Keep residue sticks visually connected to cartoon ribbons.

    ChimeraX ribbons normally suppress backbone/connector atoms.  That is clean
    for whole-protein cartoons, but it makes highlighted side chains look
    detached.  For explicitly shown sticks, keep the target residue's connector
    atoms visible and enable a slim cartoon tether on the owning structure.
    """
    try:
        objects = objects_for_spec(session, spec)
    except Exception:
        return False
    atoms = getattr(objects, "atoms", None)
    if atoms is None or len(atoms) == 0:
        return False

    try:
        residues = atoms.unique_residues
    except Exception:
        residues = None
    if residues is not None and len(residues) > 0:
        try:
            residues.ribbon_hide_backbones = False
        except Exception:
            pass
        try:
            residues.ribbon_clear_hides()
        except Exception:
            pass

    try:
        atoms.update_ribbon_backbone_atom_visibility()
    except Exception:
        pass

    try:
        structures = residues.unique_structures if residues is not None else atoms.unique_structures
    except Exception:
        structures = []
    try:
        from chimerax.atomic import Structure

        structures.ribbon_tether_scales = 0.42
        structures.ribbon_tether_shapes = Structure.TETHER_CYLINDER
        structures.ribbon_tether_sides = 12
        structures.ribbon_tether_opacities = 1.0
    except Exception:
        pass
    return True


def show_sticks_with_cartoon_anchor(session, spec):
    from chimerax.core.commands import run

    sidechain_atoms, backbone_atoms = _sidechain_and_backbone_atoms(session, spec)
    ensure_stick_cartoon_anchor(session, spec)
    if sidechain_atoms:
        _force_sidechain_stick_display(sidechain_atoms, backbone_atoms)
        _remember_sidechain_stick_atoms(session, sidechain_atoms, fallback=spec)
    elif not backbone_atoms:
        run(session, f"show {spec} atoms")
        run(session, f"style {spec} stick")
    apply_stick_context_colors(session, spec)
    return True


def hide_sticks_preserving_sidechains(session, spec, *, preserve_marked=False, forget=False):
    from chimerax.core.commands import run

    forget_specs = _residue_specs_for_spec(session, spec) if forget else []
    run(session, f"hide {spec} atoms")
    if forget:
        _forget_sidechain_stick_specs(session, forget_specs)
        _forget_sidechain_stick_spec(session, spec)
    return True


_STYLE_STICK_RE = re.compile(r"^style(?:\s+(.+?))?\s+stick(?:\s+|$)", re.IGNORECASE)


def maybe_apply_stick_context_colors_for_command(session, command):
    text = str(command or "").strip()
    match = _STYLE_STICK_RE.match(text)
    if match is None:
        return False
    spec = str(match.group(1) or "").strip() or "sel"
    return apply_stick_context_colors(session, spec)


def _sidechain_and_backbone_atoms(session, spec):
    try:
        objects = objects_for_spec(session, spec)
    except Exception:
        return [], []
    atoms = getattr(objects, "atoms", None)
    if atoms is None or len(atoms) == 0:
        return [], []
    sidechain_atoms = []
    backbone_atoms = []
    for atom in atoms:
        name = str(getattr(atom, "name", "") or "").upper()
        if name in PROTEIN_BACKBONE_ATOM_NAMES:
            backbone_atoms.append(atom)
        else:
            sidechain_atoms.append(atom)
    return sidechain_atoms, backbone_atoms


def _run_atom_chunks(session, template, atoms, *, chunk_size=80):
    from chimerax.core.commands import run

    specs = [str(getattr(atom, "atomspec", "") or "").strip() for atom in atoms or []]
    specs = [spec for spec in specs if spec]
    for start in range(0, len(specs), chunk_size):
        joined = " ".join(specs[start : start + chunk_size])
        if joined:
            run(session, template.format(atoms=joined))


def _force_sidechain_stick_display(sidechain_atoms, backbone_atoms=None):
    """Force CA-CB and side-chain bonds visible after ribbon display suppression."""
    try:
        from chimerax.atomic import Atom, Atoms
    except Exception:
        return False
    try:
        side_atoms = Atoms(list(sidechain_atoms or []))
    except Exception:
        side_atoms = None
    if side_atoms is None or len(side_atoms) == 0:
        return False
    try:
        side_atoms.displays = True
    except Exception:
        pass
    try:
        side_atoms.draw_modes = Atom.STICK_STYLE
    except Exception:
        pass
    try:
        side_atoms.bonds.displays = True
    except Exception:
        pass
    try:
        side_atoms.intra_bonds.displays = True
    except Exception:
        pass
    if backbone_atoms:
        try:
            back_atoms = Atoms(list(backbone_atoms or []))
            back_atoms.displays = False
        except Exception:
            pass
    try:
        side_atoms.displays = True
        side_atoms.draw_modes = Atom.STICK_STYLE
        side_atoms.bonds.displays = True
        side_atoms.intra_bonds.displays = True
    except Exception:
        pass
    return True


def _remembered_sidechain_stick_specs(session):
    specs = getattr(session, "_codex_sidechain_stick_specs", None)
    if not isinstance(specs, list):
        specs = []
        setattr(session, "_codex_sidechain_stick_specs", specs)
    return specs


def _remember_sidechain_stick_spec(session, spec):
    spec = str(spec or "").strip()
    if not spec:
        return
    specs = _remembered_sidechain_stick_specs(session)
    if spec not in specs:
        specs.append(spec)


def _remember_sidechain_stick_atoms(session, atoms, fallback=None):
    residue_specs = _residue_specs_for_atoms(atoms)
    if not residue_specs and fallback:
        residue_specs = [str(fallback or "").strip()]
    for spec in residue_specs:
        _remember_sidechain_stick_spec(session, spec)


def _forget_sidechain_stick_spec(session, spec):
    spec = str(spec or "").strip()
    if not spec:
        return
    specs = _remembered_sidechain_stick_specs(session)
    setattr(session, "_codex_sidechain_stick_specs", [item for item in specs if item != spec])


def _forget_sidechain_stick_specs(session, specs_to_remove):
    remove = {str(spec or "").strip() for spec in specs_to_remove or [] if str(spec or "").strip()}
    if not remove:
        return
    specs = _remembered_sidechain_stick_specs(session)
    setattr(session, "_codex_sidechain_stick_specs", [item for item in specs if item not in remove])


def _residue_specs_for_spec(session, spec):
    try:
        objects = objects_for_spec(session, spec)
    except Exception:
        return []
    atoms = getattr(objects, "atoms", None)
    return _residue_specs_for_atoms(atoms)


def _residue_specs_for_atoms(atoms):
    result = []
    seen = set()
    for atom in atoms or []:
        residue = getattr(atom, "residue", None)
        residue_spec = str(getattr(residue, "atomspec", "") or "").strip()
        if not residue_spec or residue_spec in seen:
            continue
        seen.add(residue_spec)
        result.append(residue_spec)
    return result


CHARGE_TIP_ATOM_SPECS = (
    ":asp@OD1,OD2",
    ":glu@OE1,OE2",
    ":lys@NZ",
    ":arg@NE,NH1,NH2,CZ",
)


def apply_charge_tip_colors(session, scope_spec):
    """Restore element coloring on charged side-chain tips (Asp/Glu COO-, Lys/Arg N+).

    Called after a bulk color command so the chemically meaningful red/blue
    on the charged tip atoms is not erased by `color sel <hex>` etc.
    """
    from chimerax.core.commands import run

    scope = (str(scope_spec or "").strip()) or "all"
    for spec in CHARGE_TIP_ATOM_SPECS:
        target_spec = spec if scope == "all" else f"({scope}) & {spec}"
        try:
            run(session, f"color {target_spec} byhetero target ab")
        except Exception:
            try:
                run(session, f"color {target_spec} byelement target ab")
            except Exception:
                pass
    return True


def restore_charge_colors(session, spec, *, target="ab"):
    from chimerax.core.commands import run

    spec = (str(spec or "sel").strip()) or "sel"
    try:
        run(session, f"color {spec} byhetero target {target}")
    except Exception:
        pass


_FLAT_COLOR_RE = None
_AUTO_RESTORE_GUARD = "_codex_bridge_auto_restore_active"


def install_auto_chemistry_restore(session):
    """Listen for `color <spec> #hex` commands ChimeraX-wide and auto-run
    `color <spec> byhetero target ab` afterwards so element/charge tints
    survive any flat color override (Models-panel swatch, command, anywhere).

    Idempotent. Recursion-safe via per-session guard. Skips non-flat commands
    (`byelement`, `bychain`, `byhetero` etc) so it never loops on its own
    restorer call.
    """
    global _FLAT_COLOR_RE
    import re
    if _FLAT_COLOR_RE is None:
        _FLAT_COLOR_RE = re.compile(r"^color\s+(\S+)\s+(#[0-9a-fA-F]{6,8})\b")

    if hasattr(session, "_codex_bridge_chem_restore_handler"):
        return

    def _on_command_finished(_trigger_name, cmd_text):
        if getattr(session, _AUTO_RESTORE_GUARD, False):
            return
        text = str(cmd_text or "").strip()
        if not text.startswith("color "):
            return
        m = _FLAT_COLOR_RE.match(text)
        if not m:
            return
        spec = m.group(1)
        # Skip if spec includes a target like "color sel byhetero target ab"
        if "byhetero" in text or "byelement" in text or "bychain" in text:
            return
        # Skip "color zone", "color sample", "color gradient" etc — only handle flat hex
        try:
            from chimerax.core.commands import run
            session._codex_bridge_chem_restore_active = True
            try:
                run(session, f"color {spec} byhetero target ab")
            finally:
                session._codex_bridge_chem_restore_active = False
        except Exception:
            try:
                session._codex_bridge_chem_restore_active = False
            except Exception:
                pass

    try:
        handler = session.triggers.add_handler("command finished", _on_command_finished)
        session._codex_bridge_chem_restore_handler = handler
    except Exception:
        pass


def _rgb_to_hsl(r, g, b):
    """Convert RGB 0-255 → HSL with H in [0,360), S/L in [0,1]."""
    r, g, b = r / 255.0, g / 255.0, b / 255.0
    mx, mn = max(r, g, b), min(r, g, b)
    l = (mx + mn) / 2.0
    if mx == mn:
        return 0.0, 0.0, l
    d = mx - mn
    s = d / (2.0 - mx - mn) if l > 0.5 else d / (mx + mn)
    if mx == r:
        h = ((g - b) / d) % 6
    elif mx == g:
        h = (b - r) / d + 2
    else:
        h = (r - g) / d + 4
    return h * 60.0, s, l


def _hsl_to_rgb_hex(h, s, l):
    """Convert HSL → '#rrggbb'."""
    import colorsys

    r, g, b = colorsys.hls_to_rgb(h / 360.0, l, s)
    return f"#{int(round(r * 255)):02x}{int(round(g * 255)):02x}{int(round(b * 255)):02x}"


def _sample_dominant_chain_color(session):
    """Return RGB tuple of the most prevalent chain ribbon color across open
    atomic models, or None if no ribbons are colored."""
    try:
        from chimerax.atomic import AtomicStructure
    except Exception:
        return None
    from collections import Counter

    counts = Counter()
    for model in session.models.list(type=AtomicStructure):
        try:
            for chain in model.chains:
                residues = chain.existing_residues
                if len(residues) == 0:
                    continue
                rc = getattr(residues, "ribbon_colors", None)
                if rc is None or len(rc) == 0:
                    continue
                for c in rc:
                    rgb = tuple(int(x) for x in c[:3])
                    counts[rgb] += 1
        except Exception:
            continue
    if not counts:
        return None
    return counts.most_common(1)[0][0]


def choose_accent_color(session, *, fallback="#d4845c"):
    """Pick a highlight accent that stays in the chain's color family but
    pops via higher saturation + lower lightness than the subtle tier.

    Algorithm (per Codex review iter 171):
      1. Sample the most-used chain ribbon color across open atomic models
      2. If grayscale (S < 0.15) → warm sienna fallback
      3. Else → analogous (hue + ~14°), S=0.60, L=0.42
         The small hue nudge prevents blending with the ribbon while
         keeping the accent inside the same family. Subtle uses S=0.18,
         L=0.70; the accent's much higher saturation and lower lightness
         creates clear tier separation without flipping to a complementary
         hue (which can read as garish cyan when chain is warm).

    Avoids the previous failure mode: chain orange → split-complement +150°
    landed on ~189° (cyan/teal) which felt completely off-palette.
    """
    rgb = _sample_dominant_chain_color(session)
    if rgb is None:
        return fallback
    r, g, b = rgb
    try:
        h, s, _l = _rgb_to_hsl(r, g, b)
    except Exception:
        return fallback
    if s < 0.15:
        return fallback
    new_h = (h + 14.0) % 360.0
    return _hsl_to_rgb_hex(new_h, 0.60, 0.42)


def choose_subtle_color(session, *, fallback="#aab2bc"):
    """Pick a muted secondary-highlight color that sits close to the chain
    palette in hue but is desaturated so it reads as a soft annotation.

    Used by the Analyze button for active-site / motif / metal-binding
    highlights: they should be visible but not compete with the catalytic
    triad accent or the cartoon backbone.
    """
    rgb = _sample_dominant_chain_color(session)
    if rgb is None:
        return fallback
    r, g, b = rgb
    try:
        h, s, _l = _rgb_to_hsl(r, g, b)
    except Exception:
        return fallback
    if s < 0.15:
        return fallback
    # Same hue family as the chain, but desaturated and slightly lighter so
    # it reads as a quiet annotation overlay rather than a bold accent.
    return _hsl_to_rgb_hex(h, 0.18, 0.70)
