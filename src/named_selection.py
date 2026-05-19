"""Named selection groups that surface in the ChimeraX Models panel.

Each group is a lightweight Model wrapping a stored atom-spec. When the
user toggles display or changes color from the Models panel, the change
is propagated to the actual atoms referenced by the spec via standard
ChimeraX commands (`show`, `hide`, `color`).
"""

from chimerax.core.models import Model


def _spec_is_safe(spec):
    """Reject specs that contain ChimeraX command separators / shell metas.

    Specs are stored verbatim and later interpolated into commands like
    `show <spec>` or `color <spec> #rgb`. ChimeraX treats `;` and newlines
    as command separators, so a malformed spec could chain extra commands.
    All call sites build specs from controlled atom-spec helpers, but this
    guard keeps a defense-in-depth boundary in case a future caller forgets.
    """
    if not isinstance(spec, str):
        return False
    if not spec.strip():
        return False
    # Disallow ChimeraX command/script separators and quoting tricks.
    for ch in (";", "\n", "\r", "\x00", "$", "`", "&&", "||", "|"):
        if ch in spec:
            return False
    return True


def _normalize_color(color):
    if color is None:
        return None
    if isinstance(color, str):
        text = color.strip().lstrip("#")
        if len(text) == 6:
            try:
                r = int(text[0:2], 16)
                g = int(text[2:4], 16)
                b = int(text[4:6], 16)
                return (r, g, b, 255)
            except Exception:
                return None
        if len(text) == 8:
            try:
                r = int(text[0:2], 16)
                g = int(text[2:4], 16)
                b = int(text[4:6], 16)
                a = int(text[6:8], 16)
                return (r, g, b, a)
            except Exception:
                return None
        return None
    try:
        seq = list(color)
    except Exception:
        return None
    if len(seq) >= 3:
        r, g, b = seq[0], seq[1], seq[2]
        a = seq[3] if len(seq) >= 4 else 255
        try:
            return (int(r), int(g), int(b), int(a))
        except Exception:
            return None
    return None


class CodexNamedSelectionGroup(Model):
    SESSION_SAVE = False
    SESSION_ENDURING = False

    def __init__(self, session, name, spec, color=None):
        # Block propagation during initialization so we don't fire spurious
        # show/hide/color commands while the model is wiring itself up.
        self.__dict__["_codex_propagating"] = True
        self.__dict__["_codex_named_spec"] = str(spec or "")
        super().__init__(name, session)
        self.display = False
        self.set_panel_swatch(color)
        self.__dict__["_codex_propagating"] = False

    @property
    def codex_named_spec(self):
        return self.__dict__.get("_codex_named_spec", "")

    def update_spec(self, spec):
        self.__dict__["_codex_named_spec"] = str(spec or "")

    def set_panel_swatch(self, color):
        """Set the Models-panel swatch without recoloring referenced atoms."""
        rgba = _normalize_color(color)
        if rgba is None:
            return
        previous = self.__dict__.get("_codex_propagating", False)
        self.__dict__["_codex_propagating"] = True
        try:
            try:
                self.single_color = rgba
            except Exception:
                pass
            try:
                self.model_color = rgba
            except Exception:
                pass
            try:
                self.color = rgba
            except Exception:
                pass
        finally:
            self.__dict__["_codex_propagating"] = previous

    def __setattr__(self, attr, value):
        super().__setattr__(attr, value)
        if self.__dict__.get("_codex_propagating", True):
            return
        spec = self.__dict__.get("_codex_named_spec") or ""
        if not spec:
            return
        if attr == "display":
            self._propagate_display(bool(value), spec)
        elif attr in ("single_color", "model_color", "overall_color", "color"):
            self._propagate_color(value, spec)

    def _propagate_display(self, value, spec):
        if not _spec_is_safe(spec):
            try:
                self.session.logger.warning(
                    f"[Codex group display] refusing unsafe spec {spec!r}"
                )
            except Exception:
                pass
            return
        self.__dict__["_codex_propagating"] = True
        try:
            from chimerax.core.commands import run

            cmd = "show" if value else "hide"
            run(self.session, f"{cmd} {spec}")
        except Exception as err:
            try:
                self.session.logger.warning(
                    f"[Codex group display] propagate failed for spec {spec!r}: {err}"
                )
            except Exception:
                pass
        finally:
            self.__dict__["_codex_propagating"] = False

    def _propagate_color(self, value, spec):
        if value is None:
            return
        if not _spec_is_safe(spec):
            try:
                self.session.logger.warning(
                    f"[Codex group color] refusing unsafe spec {spec!r}"
                )
            except Exception:
                pass
            return
        try:
            if hasattr(value, "uint8x4"):
                rgba = value.uint8x4()
            else:
                rgba = list(value)
        except Exception:
            return
        if len(rgba) < 3:
            return
        try:
            r = int(rgba[0])
            g = int(rgba[1])
            b = int(rgba[2])
        except Exception:
            return
        hex_color = f"#{r:02x}{g:02x}{b:02x}"
        self.__dict__["_codex_propagating"] = True
        try:
            from chimerax.core.commands import run

            run(self.session, f"color {spec} {hex_color} target ab")
            # Restore chemistry-meaningful coloring after the bulk override:
            # 1. byhetero re-tints all N/O/S/P heteroatoms by element
            # 2. apply_charge_tip_colors specifically restores the charged
            #    side-chain tips (Asp/Glu COO−, Lys NZ, Arg guanidinium CZ)
            #    that the bulk color would otherwise hide.
            from .display_color import apply_charge_tip_colors, restore_charge_colors

            restore_charge_colors(self.session, spec)
            apply_charge_tip_colors(self.session, spec)
        except Exception as err:
            try:
                self.session.logger.warning(
                    f"[Codex group color] propagate failed for spec {spec!r}: {err}"
                )
            except Exception:
                pass
        finally:
            self.__dict__["_codex_propagating"] = False


def _registry(session):
    if not hasattr(session, "_codex_named_selection_groups"):
        session._codex_named_selection_groups = {}
    return session._codex_named_selection_groups


def list_groups(session):
    return list(_registry(session).keys())


def get_group_spec(session, name):
    model = _registry(session).get(name)
    return model.codex_named_spec if model is not None else None


def add_group(session, name, spec, color=None):
    """Create or update a named selection group. Returns the model.

    The optional `color` (hex string '#rrggbb' or rgba tuple) sets the
    model's swatch color in the Models panel so it visually matches the
    color used for the corresponding stick highlight in 3D.

    If `session.models.add` fails, the model is NOT recorded in the
    registry — otherwise subsequent add_group calls would find a stale
    entry and call update_spec on a model that isn't actually in the
    session, silently desynchronizing the registry from ChimeraX.
    """
    registry = _registry(session)
    existing = registry.get(name)
    if existing is not None:
        existing.update_spec(spec)
        existing.set_panel_swatch(color)
        return existing
    model = CodexNamedSelectionGroup(session, name, spec, color=color)
    try:
        session.models.add([model])
    except Exception as err:
        try:
            session.logger.warning(
                f"Could not add named group '{name}' to Models panel: {err}"
            )
        except Exception:
            pass
        return None
    registry[name] = model
    return model


def rename_group(session, old_name, new_name):
    """Rename a named group. Returns the model on success, None otherwise.

    Refuses the rename if `new_name` is already registered to a different
    model (the previous code overwrote the entry, orphaning the prior
    model in session.models without registry tracking). Also short-circuits
    if old_name == new_name.
    """
    registry = _registry(session)
    model = registry.get(old_name)
    if model is None:
        return None
    if old_name == new_name:
        return model
    if new_name in registry and registry[new_name] is not model:
        try:
            session.logger.warning(
                f"rename_group: '{new_name}' is already registered; refusing to overwrite."
            )
        except Exception:
            pass
        return None
    model.name = new_name
    registry.pop(old_name, None)
    registry[new_name] = model
    try:
        session.triggers.activate_trigger("models renamed", [model])
    except Exception:
        pass
    return model


def remove_group(session, name):
    """Close the named-group model and remove it from the registry.

    The registry entry is only popped if `session.models.close` succeeds —
    otherwise the entry is preserved so a future `remove_group` retry can
    target the same model rather than orphaning it (the previous code
    popped first, leaving the model alive in session.models but invisible
    to the registry's auto-prune handlers).
    """
    registry = _registry(session)
    model = registry.get(name)
    if model is None:
        return False
    try:
        session.models.close([model])
    except Exception as err:
        try:
            session.logger.warning(f"Could not remove named group '{name}': {err}")
        except Exception:
            pass
        return False
    registry.pop(name, None)
    return True
