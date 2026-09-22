"""Undoable chain visibility without changing molecular representations."""

from dataclasses import dataclass

import numpy as np


def _copy(value):
    return None if value is None else value.copy()


def _same(a, b):
    return a is b or (a is not None and b is not None and np.array_equal(a, b))


def _deleted(obj):
    return obj.deleted


def _without_solvent(atoms):
    """Return the atoms whose visibility defines a molecular chain.

    Crystal waters commonly share a protein's chain ID and are hidden by
    default. They must not make that chain look partly shown or be revealed
    by it. A target consisting only of solvent remains explicitly controllable.
    """
    if not len(atoms):
        return atoms
    solvent = atoms.structure_categories == "solvent"
    return atoms if solvent.all() or not solvent.any() else atoms.filter(~solvent)


def _displayed(model):
    while model is not None:
        positions = model.display_positions
        if not model.display or (positions is not None and not positions.any()):
            return False
        model = model.parent
    return True


@dataclass
class _SurfaceState:
    atoms: object
    mask: object
    triangles: object
    display: bool
    remask: object

    @classmethod
    def capture(cls, surface):
        return cls(surface.show_atoms.copy(), _copy(surface.triangle_mask),
                   surface.triangles, surface.display, surface.auto_remask_triangles)


class _SurfaceRestore:
    def __init__(self, surface):
        self.surface = surface

    def apply(self, state):
        surface = self.surface
        if surface.deleted:
            return
        surface.show_atoms = state.atoms & surface.atoms
        # A rebuilt mesh has different atom/triangle correspondence, even when
        # its triangle count happens to match the previous mesh.
        surface.triangle_mask = (_copy(state.mask) if surface.triangles is state.triangles
                                 else surface._calc_triangle_mask())
        surface.auto_remask_triangles = state.remask
        surface.display = state.display


class ChainVisibilityController:
    """Control existing atoms, cartoons, and molecular surface patches.

    A shared surface without atom patches cannot be split by chain. Such an
    operation raises UserError before leaving any scene changes in place.
    """

    # Instance state with a factory for its initial value. adopt() fills in
    # any field that an older controller class did not create.
    _STATE_FIELDS = (("_atom_memory", dict), ("_ribbon_memory", dict),
                     ("_surface_memory", dict), ("_closed", bool))

    def __init__(self, session):
        self.session = session
        for name, factory in self._STATE_FIELDS:
            setattr(self, name, factory())

    @classmethod
    def adopt(cls, controller):
        """Upgrade a live controller from a reloaded module to this class.

        The identity is kept, so native Undo entries and remembered styles
        stay valid. A closed controller is never revived; a new one is
        returned instead.
        """
        session = getattr(controller, "session", None)
        if session is None:
            raise TypeError(f"Cannot adopt {type(controller).__name__} as a chain visibility controller")
        if getattr(controller, "_closed", False):
            return cls(session)
        if type(controller) is not cls:
            controller.__class__ = cls
        for name, factory in cls._STATE_FIELDS:
            if not hasattr(controller, name):
                setattr(controller, name, factory())
        return controller

    def _all_atoms(self):
        from chimerax.atomic import Atoms, all_atomic_structures, concatenate
        return concatenate([m.atoms for m in all_atomic_structures(self.session)], Atoms)

    def _live(self, atoms):
        from chimerax.atomic import Atoms, all_atomic_structures, concatenate
        opened = set(all_atomic_structures(self.session))
        groups = list(atoms.by_structure)
        if all(model in opened for model, _local in groups):
            return atoms
        return concatenate([local for model, local in groups if model in opened], Atoms)

    def _surfaces(self, atoms):
        from chimerax.atomic import MolecularSurface
        return [s for s in self.session.models.list(type=MolecularSurface)
                if not s.deleted and s.atoms.intersects(atoms)]

    @staticmethod
    def _allowed(surface, atoms):
        mapping = surface.vertex_to_atom_map()
        return surface.atoms.mask(atoms)[mapping][surface.triangles].all(axis=1)

    @staticmethod
    def _affected(surface, atoms):
        mapping = surface.vertex_to_atom_map()
        return surface.atoms.mask(atoms)[mapping][surface.triangles].any(axis=1)

    def state(self, atoms):
        """Report visible residue coverage, including existing surface patches."""
        if self._closed:
            return "hidden"
        atoms = _without_solvent(self._live(atoms))
        if not len(atoms):
            return "hidden"
        residues = atoms.unique_residues
        visible = set()
        for model, local in atoms.by_structure:
            if _displayed(model):
                represented = local.filter(local.visibles | local.residues.ribbon_displays)
                visible.update(represented.unique_residues)
        for surface in self._surfaces(atoms):
            if not _displayed(surface) or surface.triangles is None:
                continue
            shown = surface.show_atoms & atoms
            mask = surface.triangle_mask
            if mask is not None and not mask.any():
                continue
            if surface.has_atom_patches() and mask is not None:
                mapping = surface.vertex_to_atom_map()
                indexes = np.unique(mapping[surface.triangles[mask]].reshape(-1))
                shown = shown & surface.atoms[indexes]
            visible.update(shown.unique_residues)
        count = len(visible.intersection(residues))
        return "hidden" if not count else "shown" if count == len(residues) else "mixed"

    def _memory(self):
        return (self._atom_memory.copy(), self._ribbon_memory.copy(), self._surface_memory.copy())

    def _restore_memory(self, state):
        if not self._closed:
            self._atom_memory, self._ribbon_memory, self._surface_memory = (
                part.copy() for part in state)

    def _capture(self):
        atoms = self._all_atoms()
        from chimerax.atomic import MolecularSurface
        return {
            "atoms": {a: (bool(d), int(h)) for a, d, h in zip(atoms, atoms.displays, atoms.hides)},
            "ribbons": {r: bool(d) for r, d in zip(atoms.unique_residues,
                                                  atoms.unique_residues.ribbon_displays)},
            "surfaces": {s: _SurfaceState.capture(s) for s in
                         self.session.models.list(type=MolecularSurface)},
            "models": {m: (m.display, _copy(m.display_positions)) for m in
                       self.session.models.list() if not isinstance(m, MolecularSurface)},
        }

    @staticmethod
    def _surface_equal(a, b):
        return (a.display == b.display and a.remask is b.remask
                and a.triangles is b.triangles and _same(a.mask, b.mask)
                and np.array_equal(a.atoms.pointers, b.atoms.pointers))

    def _undo_delta(self, name, before, after, memory_before, memory_after):
        from chimerax.core.undo import UndoState
        undo = UndoState(name)
        for category, index, attribute in (("atoms", 0, "display"),
                                            ("atoms", 1, "hide"),
                                            ("ribbons", None, "ribbon_display")):
            old, new = before[category], after[category]
            objects = tuple(obj for obj in old if obj in new and
                            (old[obj] != new[obj] if index is None else
                             old[obj][index] != new[obj][index]))
            if objects:
                values = lambda source: tuple(source[obj] if index is None else source[obj][index]
                                              for obj in objects)
                # Atoms and residues are Cython objects whose "deleted" is
                # not a Python property. Native Undo's default check misses
                # them and reports a ChimeraX bug after "delete" or "close".
                undo.add(objects, attribute, values(old), values(new), option="S",
                         deleted_check=_deleted)
        for surface, old in before["surfaces"].items():
            new = after["surfaces"].get(surface)
            if new is not None and not self._surface_equal(old, new):
                undo.add(_SurfaceRestore(surface), "apply", old, new, option="M")
        for model, old in before["models"].items():
            new = after["models"].get(model)
            if new is None:
                continue
            if old[0] != new[0]:
                undo.add(model, "display", old[0], new[0])
            if not _same(old[1], new[1]):
                undo.add(model, "display_positions", old[1], new[1])
        if undo.state:
            undo.add(self, "_restore_memory", memory_before, memory_after, option="M")
        return undo

    def _edit(self, name, callback):
        if self._closed:
            return
        self._atom_memory = {a: d for a, d in self._atom_memory.items() if not a.deleted}
        self._ribbon_memory = {r: d for r, d in self._ribbon_memory.items() if not r.deleted}
        self._surface_memory = {s: d for s, d in self._surface_memory.items() if not s.deleted}
        before, memory_before = self._capture(), self._memory()
        try:
            callback()
        except Exception:
            rollback = self._undo_delta(name, before, self._capture(), memory_before, self._memory())
            rollback.undo()
            self._restore_memory(memory_before)
            raise
        undo = self._undo_delta(name, before, self._capture(), memory_before, self._memory())
        if undo.state:
            self.session.undo.register(undo)

    def _check_surface(self, surface, target):
        if not surface.has_atom_patches() and len(surface.atoms - target):
            from chimerax.core.errors import UserError
            raise UserError(f"Surface #{surface.id_string} spans several chains without atom patches. "
                            "Hide the whole surface or recreate it with atom patches before toggling this chain.")

    def _hide(self, atoms):
        if not len(atoms):
            return
        surfaces = self._surfaces(atoms)
        for surface in surfaces:
            self._check_surface(surface, atoms)
        residues = atoms.unique_residues
        for _model, _chain, chain_residues in residues.by_chain:
            local = chain_residues.atoms & atoms
            # External commands can reveal a previously hidden chain in a
            # different style. The next hide must remember that current style.
            active = local.displays.any() or chain_residues.ribbon_displays.any()
            for atom, display in zip(local, local.displays):
                if active or atom not in self._atom_memory:
                    self._atom_memory[atom] = bool(display)
            for residue, display in zip(chain_residues, chain_residues.ribbon_displays):
                if active or residue not in self._ribbon_memory:
                    self._ribbon_memory[residue] = bool(display)
        atoms.displays = False
        residues.ribbon_displays = False
        atoms.update_ribbon_backbone_atom_visibility()
        for surface in surfaces:
            target = surface.atoms & atoms
            previous = self._surface_memory.get(surface)
            original = _SurfaceState.capture(surface) if previous is None else previous[0]
            hidden = target if previous is None else previous[1] | target
            self._surface_memory[surface] = (original, hidden)
            surface.show_atoms = surface.show_atoms - target
            if surface.has_atom_patches():
                allowed = self._allowed(surface, surface.show_atoms)
                mask = surface.triangle_mask
                surface.triangle_mask = allowed if mask is None else mask & allowed
            else:
                surface.display = False

    def _reveal_parents(self, atoms):
        # Before opening a hidden parent, retain effective invisibility of
        # every unrelated molecular chain and sibling annotation.
        paths = set()
        for model in atoms.unique_structures:
            item = model
            while item is not None:
                paths.add(item)
                item = item.parent
        hidden = {m for m in paths if not _displayed(m)}
        if not hidden:
            return
        opening = {m for m in paths if not m.display or
                   (m.display_positions is not None and not m.display_positions.any())}
        def affected(model):
            while model is not None:
                if model in opening:
                    return True
                model = model.parent
            return False
        other = self._all_atoms() - atoms
        preserve = other.filter(np.fromiter(
            (affected(a.structure) for a in other), bool, count=len(other)))
        self._hide(preserve)
        relevant_surfaces = set(self._surfaces(atoms))
        for parent in paths:
            if parent not in hidden:
                continue
            for child in parent.child_models():
                if child not in paths and child not in relevant_surfaces:
                    child.display = False
        for model in paths:
            model.display = True
            positions = model.display_positions
            if positions is not None and not positions.any():
                model.display_positions = None

    def _show(self, atoms):
        if not len(atoms):
            return
        self._reveal_parents(atoms)
        remembered_atoms = set(self._atom_memory).intersection(atoms)
        remembered_residues = set(self._ribbon_memory).intersection(atoms.unique_residues)
        for atom in remembered_atoms:
            atom.display = self._atom_memory.pop(atom)
        for residue in remembered_residues:
            residue.ribbon_display = self._ribbon_memory.pop(residue)
        represented_surfaces = set()
        for surface in self._surfaces(atoms):
            target = atoms & surface.atoms
            previous = self._surface_memory.get(surface)
            if previous is not None:
                original, hidden = previous
                restored = target & hidden
                surface.show_atoms = surface.show_atoms | (original.atoms & restored)
                remaining = hidden - target
                if surface.has_atom_patches():
                    allowed = self._allowed(surface, surface.show_atoms)
                    if surface.triangles is original.triangles:
                        mask = (np.ones(len(surface.triangles), bool) if surface.triangle_mask is None
                                else surface.triangle_mask.copy())
                        affected = self._affected(surface, restored)
                        baseline = np.ones(len(mask), bool) if original.mask is None else original.mask
                        mask[affected] = baseline[affected]
                        surface.triangle_mask = mask & allowed
                        if not len(remaining) and original.mask is None and surface.triangle_mask.all():
                            surface.triangle_mask = None
                    else:
                        surface.triangle_mask = surface._calc_triangle_mask()
                surface.display = original.display
                if len(remaining):
                    self._surface_memory[surface] = (original, remaining)
                else:
                    surface.auto_remask_triangles = original.remask
                    del self._surface_memory[surface]
            elif not surface.display:
                self._check_surface(surface, target)
                # Enabling a whole hidden surface must not reveal its other chains.
                original = _SurfaceState.capture(surface)
                remaining = surface.atoms - target
                if len(remaining):
                    # The user is making this surface visible now. Remember
                    # other patches so a later Show can retain their old mask.
                    original.display = True
                    self._surface_memory[surface] = (original, remaining)
                surface.show_atoms = surface.show_atoms & target
                if surface.has_atom_patches():
                    mask = self._allowed(surface, surface.show_atoms)
                    surface.triangle_mask = (mask if surface.triangle_mask is None
                                             else surface.triangle_mask & mask)
                surface.display = True
            elif (surface.has_atom_patches() and len(target - surface.show_atoms)
                  and not target.displays.any() and not target.unique_residues.ribbon_displays.any()):
                # External "hide surfaces" may remove a patch without going
                # through this controller. Reuse that existing representation.
                self._check_surface(surface, target)
                surface.show_atoms = surface.show_atoms | target
                mask = (np.ones(len(surface.triangles), bool) if surface.triangle_mask is None
                        else surface.triangle_mask.copy())
                affected = self._affected(surface, target)
                allowed = self._allowed(surface, surface.show_atoms)
                mask[affected] = allowed[affected]
                surface.triangle_mask = mask
            if surface.display:
                represented_surfaces.update((surface.show_atoms & target).unique_residues)
        from chimerax.atomic import Residue
        # Fallback styles apply to the chain itself. Previously hidden solvent
        # in a mixed target stays hidden; remembered solvent was restored above.
        scope = _without_solvent(atoms)
        # With no remembered appearance, use an existing representation. Only
        # completely unrepresented residues receive a sensible initial style.
        for residue in scope.unique_residues:
            if (residue in remembered_residues or residue in represented_surfaces
                    or residue.ribbon_display or residue.atoms.displays.any()):
                continue
            if residue.polymer_type != Residue.PT_NONE:
                residue.ribbon_display = True
            else:
                local = residue.atoms & scope
                local.displays = True
        # An explicit Show still has to reveal a chain whose remembered state
        # was entirely hidden (for example, after Hide all then Show all).
        for _model, _chain, residues in scope.unique_residues.by_chain:
            local = residues.atoms & scope
            if self.state(local) != "hidden":
                continue
            polymers = residues.filter(residues.polymer_types != Residue.PT_NONE)
            polymers.ribbon_displays = True
            nonpolymer = residues.filter(residues.polymer_types == Residue.PT_NONE).atoms & scope
            nonpolymer.displays = True
        atoms.update_ribbon_backbone_atom_visibility()

    def set_visible(self, atoms, visible):
        atoms = self._live(atoms)
        self._edit("Show chains" if visible else "Hide chains",
                   lambda: self._show(atoms) if visible else self._hide(atoms))

    def isolate(self, atoms):
        atoms = self._live(atoms)
        if not len(atoms):
            return
        def apply():
            self._hide(self._all_atoms() - atoms)
            self._show(atoms)
        self._edit("Isolate chains", apply)

    def show_all(self):
        self.set_visible(self._all_atoms(), True)

    def cleanup(self):
        self._closed = True
        self._atom_memory.clear()
        self._ribbon_memory.clear()
        self._surface_memory.clear()
