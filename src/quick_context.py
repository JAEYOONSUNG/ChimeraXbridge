"""GUI-thread snapshots and scoped visual undo for the six quick actions."""
import hashlib
from datetime import datetime, timezone

import numpy as np


def primary_models(session):
    from chimerax.atomic import AtomicStructure
    from .toolbar_actions import _is_codex_helper_atomic_model
    return [m for m in session.models.list(type=AtomicStructure)
            if not getattr(m, "_codex_quick_overlay", False)
            and not _is_codex_helper_atomic_model(m) and m.num_atoms]


def selected_specs(session):
    from chimerax.atomic import selected_residues
    return tuple(sorted(r.atomspec for r in selected_residues(session)))


def resolve_targets(session, model_hint=None):
    from chimerax.atomic import selected_atoms
    from chimerax.core.errors import UserError
    models = primary_models(session)
    if not models:
        raise UserError("Open a protein or nucleic-acid structure first.")
    if model_hint:
        wanted = set(str(model_hint).split())
        chosen = [m for m in models if f"#{m.id_string}" in wanted]
        if not chosen:
            raise UserError("The chosen structure is no longer open.")
        return chosen, "Chosen target"
    selected = set(selected_atoms(session).unique_structures)
    chosen = [m for m in models if m in selected]
    if chosen:
        return chosen, "Selected atoms"
    from .toolbar_actions import _model_panel_selected_atomic_model_specs
    highlighted = _model_panel_selected_atomic_model_specs(session)
    chosen = [m for m in models if f"#{m.id_string}" in highlighted]
    if chosen:
        return chosen, "Highlighted model"
    visible = [m for m in models if m.visible]
    chosen = max(visible or models, key=lambda m: (m.num_residues, m.num_atoms))
    return [chosen], "Largest visible structure" if visible else "Largest open structure"


def fingerprint(models):
    """Include scientific inputs and object identity, not cosmetic colors."""
    digest = hashlib.blake2b(digest_size=20)
    for model in models:
        atoms, residues = model.atoms, model.residues
        digest.update(str((id(model), model.id_string, model.name)).encode())
        for array in (atoms.pointers, atoms.scene_coords, atoms.element_numbers,
                      atoms.bfactors, atoms.occupancies, atoms.residues.pointers,
                      model.bonds.pointers):
            digest.update(np.asarray(array).tobytes())
        digest.update("\0".join(atoms.names).encode())
        digest.update("\0".join(residues.names).encode())
        digest.update(str(tuple((r.chain_id, r.number, r.insertion_code, r.ss_type)
                                for r in residues)).encode())
    return digest.hexdigest()


def _frozen(values, dtype=None):
    array = np.array(values, dtype=dtype, copy=True)
    array.flags.writeable = False
    return array


def capture_context(session, model_hint=None):
    from chimerax.atomic import Residue
    models, reason = resolve_targets(session, model_hint)
    selection = selected_specs(session)
    selected = set(selection)
    entries = []
    for model in models:
        atoms, residues = model.atoms, model.residues
        residue_index = residues.indices(atoms.residues)
        categories = atoms.structure_categories
        first_atom = {}
        for index, residue in enumerate(residue_index):
            first_atom.setdefault(int(residue), index)
        rows = []
        for index, residue in enumerate(residues):
            polymer = ("protein" if residue.polymer_type == Residue.PT_AMINO else
                       "nucleic" if residue.polymer_type == Residue.PT_NUCLEIC else "other")
            rows.append({"spec": residue.atomspec, "name": residue.name,
                         "chain": residue.chain_id, "number": int(residue.number),
                         "polymer": polymer, "selected": residue.atomspec in selected,
                         "category": str(categories[first_atom[index]]) if index in first_atom else "other",
                         "ss_type": int(residue.ss_type)})
        bonds = model.bonds
        ends = bonds.atoms
        bond_indices = np.column_stack((atoms.indices(ends[0]), atoms.indices(ends[1])))
        entries.append({"spec": f"#{model.id_string}", "name": str(model.name),
                        "coords": _frozen(atoms.scene_coords, np.float64),
                        "elements": _frozen(atoms.element_numbers, np.uint8),
                        "atom_names": tuple(atoms.names), "residue_index": _frozen(residue_index, np.int32),
                        "bfactors": _frozen(atoms.bfactors, np.float32),
                        "occupancies": _frozen(atoms.occupancies, np.float32),
                        "residues": tuple(rows), "bonds": _frozen(bond_indices, np.int32)})
    scientific_signature = fingerprint(models)
    signature = hashlib.blake2b((scientific_signature + repr(selection)).encode(), digest_size=20).hexdigest()
    label = ", ".join(f"#{m.id_string} · {m.name}" for m in models)
    captured_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    snapshot = {"models": entries, "target_label": label, "captured_at": captured_at,
                "selection_specs": selection, "signature": signature}
    return {"models": tuple(models), "model_by_spec": {f"#{m.id_string}": m for m in models},
            "selection_specs": selection, "snapshot": snapshot, "reason": reason,
            "scientific_signature": scientific_signature, "explicit_target": bool(model_hint),
            "captured_at": captured_at}


def context_valid(session, context, *, check_selection=True):
    live = set(session.models.list())
    if any(m not in live or m.deleted for m in context["models"]):
        return False
    if check_selection and selected_specs(session) != context["selection_specs"]:
        return False
    try:
        if check_selection and not context.get("explicit_target"):
            if tuple(resolve_targets(session)[0]) != context["models"]:
                return False
        return fingerprint(context["models"]) == context["scientific_signature"]
    except (RuntimeError, ValueError, ReferenceError):
        return False


class VisualState:
    """One complete visual transaction without polluting named bookmarks."""

    def __init__(self, session, context):
        from chimerax.std_commands.view import NamedView
        from chimerax.graphics.gsession import LightingState, MaterialState, ViewState
        from chimerax.core.state import State, copy_state
        from .camera_bookmark_state import _capture_model
        self.before_models = set(session.models.list())
        targets = set(context["models"])
        target_ids = {id(model) for model in targets}
        self.scientific_signature = context["scientific_signature"]
        self.records = []
        for model in self.before_models:
            parent = model
            while parent is not None and parent not in targets:
                parent = getattr(parent, "parent", None)
            if parent in targets or (getattr(model, "_codex_quick_overlay", False)
                    and target_ids.intersection(getattr(model, "_codex_quick_targets", ()))):
                self.records.append(_capture_model(model, dict(visibility=True, colors=True, selection=False)))
        view = session.main_view
        self.camera = view.camera
        self.targets = tuple(context["models"])
        self.named_view = NamedView(view, view.center_of_rotation, list(self.targets))
        self.pivot = copy_state(view.center_of_rotation)
        self.pivot_method = view.center_of_rotation_method
        self.lighting = copy_state(LightingState.take_snapshot(view.lighting, session, State.SESSION))
        self.material = copy_state(MaterialState.take_snapshot(view.material, session, State.SESSION))
        self.background = tuple(view.background_color)
        self.highlight_thickness = view.highlight_thickness
        self.silhouette = {key: copy_state(getattr(view.silhouette, key)) for key in ViewState.silhouette_attrs}
        self.created = []

    def finish(self, session, context):
        self.created = [m for m in session.models.list() if m not in self.before_models]
        for model in self.created:
            model._codex_quick_overlay = True
            model._codex_quick_targets = tuple(id(m) for m in context["models"])
            model._codex_quick_signature = context["scientific_signature"]

    def restore(self, session):
        from .camera_bookmark_state import _restore_model
        from chimerax.graphics.gsession import LightingState, MaterialState
        from chimerax.core.state import copy_state
        live = set(session.models.list())
        # Closed/replaced targets must never rewind an unrelated scene camera.
        if any(model not in live or model.deleted for model in self.targets):
            return False
        created = [m for m in self.created if m in live]
        if created:
            session.models.close(created)
        live = set(session.models.list())
        for record in self.records:
            if record["model"] in live and not record["model"].deleted:
                _restore_model(record)
        view = session.main_view
        view.camera = self.camera
        self.named_view.set_view(view, [model for model in self.targets if model in live])
        view.center_of_rotation = copy_state(self.pivot)
        view.center_of_rotation_method = self.pivot_method
        LightingState.set_state_from_snapshot(view.lighting, session, self.lighting)
        MaterialState.set_state_from_snapshot(view.material, session, self.material)
        view.background_color = self.background
        view.highlight_thickness = self.highlight_thickness
        for key, value in self.silhouette.items():
            setattr(view.silhouette, key, copy_state(value))
        view.update_lighting = True
        view.redraw_needed = True
        return True


def capture_selection(session):
    from chimerax.atomic import (selected_atoms, selected_bonds, selected_pseudobonds,
                                 Structure, PseudobondGroup, MolecularSurface)
    collections = [selected_atoms(session), selected_bonds(session), selected_pseudobonds(session)]
    models = []
    for model in session.selection.models():
        if not isinstance(model, (Structure, PseudobondGroup, MolecularSurface)):
            positions = getattr(model, "selected_positions", None)
            models.append((model, bool(model.selected), None if positions is None else positions.copy()))
    signature = (tuple(c.pointers.tobytes() for c in collections),
                 tuple((id(m), s, None if p is None else p.tobytes()) for m, s, p in models))
    return {"collections": [(c.__class__, list(c)) for c in collections], "models": models, "signature": signature}


def restore_selection(session, saved):
    if capture_selection(session)["signature"] == saved["signature"]:
        return
    session.selection.clear()
    for cls, objects in saved["collections"]:
        alive = [obj for obj in objects if not obj.deleted]
        if alive:
            cls(alive).selected = True
    live = set(session.models.list())
    for model, selected, positions in saved["models"]:
        if model in live:
            model.selected = selected
            if positions is not None and len(positions) == len(model.positions):
                model.selected_positions = positions.copy()
