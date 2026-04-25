import re

from chimerax.core.commands import ObjectsArg


def objects_for_spec(session, spec):
    objects, _used, _rest = ObjectsArg.parse(str(spec or "").strip(), session)
    return objects


def apply_stick_context_colors(session, spec):
    from chimerax.atomic.colors import element_colors

    objects = objects_for_spec(session, spec)
    atoms = getattr(objects, "atoms", None)
    if atoms is None or len(atoms) == 0:
        return False

    colors = atoms.colors.copy()
    hetero_mask = atoms.element_numbers != 6
    if hetero_mask.any():
        hetero_colors = element_colors(atoms.element_numbers[hetero_mask])
        hetero_colors[:, 3] = colors[hetero_mask, 3]
        colors[hetero_mask, :] = hetero_colors

    atoms.colors = colors
    return True


_STYLE_STICK_RE = re.compile(r"^style(?:\s+(.+?))?\s+stick(?:\s+|$)", re.IGNORECASE)


def maybe_apply_stick_context_colors_for_command(session, command):
    text = str(command or "").strip()
    match = _STYLE_STICK_RE.match(text)
    if match is None:
        return False
    spec = str(match.group(1) or "").strip() or "sel"
    return apply_stick_context_colors(session, spec)
