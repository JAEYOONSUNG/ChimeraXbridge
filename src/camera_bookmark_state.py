"""Selected scene conditions attached to ChimeraX native named views.

Model and atomic object references, rather than model IDs, keep a bookmark from
accidentally changing a replacement model. Closed objects are pruned on save.
"""

from chimerax.core.errors import UserError
from chimerax.core.state import State, StateManager, copy_state


DEFAULT_OPTIONS = dict(camera=True, visibility=True, colors=True,
                       lighting=True, selection=False)
_STATE_TAG = "codex camera bookmarks"


def _native_views(session):
    from chimerax.std_commands.view import _named_views
    return _named_views(session).views


def _manager(session):
    manager = getattr(session, "_codex_camera_bookmark_state", None)
    if manager is None:
        manager = CameraBookmarkState()
        session._codex_camera_bookmark_state = manager
        session.add_state_manager(_STATE_TAG, manager)
    return manager


def list_bookmarks(session):
    """Include native views made outside this panel."""
    return list(_native_views(session))


def _record(session, name):
    records = _manager(session).bookmarks
    record = records.get(name)
    # A native `view name` command may replace an entry behind the panel.
    # Its newly saved camera must not inherit unrelated old scene conditions.
    if record is not None and record.get("native_view") is not _native_views(session).get(name):
        records.pop(name, None)
        return None
    return record


def bookmark_options(session, name):
    record = _record(session, name)
    if name not in _native_views(session):
        raise UserError('Bookmark "%s" does not exist.' % name)
    if record is None:
        return {key: key == "camera" for key in DEFAULT_OPTIONS}
    return dict(record["options"])


def _valid_name(name):
    name = str(name).strip()
    if not name or any(ord(c) < 32 for c in name):
        raise UserError("Enter a bookmark name without line breaks.")
    return name


def save_bookmark(session, name, options=None):
    """Save without moving the view. A repeated name explicitly replaces it."""
    name = _valid_name(name)
    chosen = dict(DEFAULT_OPTIONS) if options is None else {
        key: bool(options.get(key, False)) for key in DEFAULT_OPTIONS}
    if not any(chosen.values()):
        raise UserError("Choose at least one condition to save.")
    record = {"options": chosen, "models": []}
    models = session.models.list()
    if chosen["visibility"] or chosen["colors"] or chosen["selection"]:
        record["models"] = [_capture_model(model, chosen) for model in models]
    view = session.main_view
    if chosen["camera"]:
        record["camera_name"] = view.camera.name
        record["center_of_rotation"] = copy_state(view.center_of_rotation)
        record["center_of_rotation_method"] = view.center_of_rotation_method
    if chosen["lighting"]:
        from chimerax.graphics.gsession import LightingState, MaterialState, ViewState
        record["lighting"] = copy_state(LightingState.take_snapshot(
            view.lighting, session, State.SESSION))
        record["material"] = copy_state(MaterialState.take_snapshot(
            view.material, session, State.SESSION))
        record["background"] = tuple(view.background_color)
        record["silhouette"] = _attributes(view.silhouette, ViewState.silhouette_attrs)
    # Always keep the native entry so existing view commands and sessions work.
    # The panel honors the saved camera option when restoring this record.
    from chimerax.std_commands.view import NamedView
    native_view = NamedView(view, view.center_of_rotation, models)
    record["native_view"] = native_view
    _native_views(session)[name] = native_view
    _manager(session).bookmarks[name] = record
    return dict(chosen)


def restore_bookmark(session, name):
    native = _native_views(session).get(name)
    if native is None:
        raise UserError('Bookmark "%s" does not exist.' % name)
    record = _record(session, name)
    if record is None:
        from chimerax.std_commands.view import show_view
        show_view(session, native)
        return {"options": bookmark_options(session, name), "skipped_models": 0}
    chosen = record["options"]
    view = session.main_view
    if chosen["camera"]:
        # Native NamedView preserves poses and clipping as well as the camera.
        camera_name = record.get("camera_name")
        if camera_name != view.camera.name:
            from chimerax.graphics.camera import MonoCamera, OrthographicCamera
            camera_class = {"mono": MonoCamera,
                            "orthographic": OrthographicCamera}.get(camera_name)
            if camera_class is not None:
                view.camera = camera_class()
        from chimerax.std_commands.view import show_view
        show_view(session, native)
        view.center_of_rotation = copy_state(record["center_of_rotation"])
        view.center_of_rotation_method = record["center_of_rotation_method"]
    if chosen["selection"]:
        session.selection.clear()
    open_models = set(session.models.list())
    skipped = 0
    for model_state in record["models"]:
        model = model_state["model"]
        if not _alive(model) or model not in open_models:
            skipped += 1
            continue
        _restore_model(model_state)
    if chosen["lighting"]:
        from chimerax.graphics.gsession import LightingState, MaterialState
        LightingState.set_state_from_snapshot(view.lighting, session, record["lighting"])
        MaterialState.set_state_from_snapshot(view.material, session, record["material"])
        view.background_color = record["background"]
        for attribute, value in record.get("silhouette", {}).items():
            setattr(view.silhouette, attribute, copy_state(value))
        view.update_lighting = True
    view.redraw_needed = True
    return {"options": dict(chosen), "skipped_models": skipped}


def delete_bookmark(session, name):
    views = _native_views(session)
    if name not in views:
        raise UserError('Bookmark "%s" does not exist.' % name)
    del views[name]
    _manager(session).bookmarks.pop(name, None)


def rename_bookmark(session, old, new):
    """Rename the saved objects directly; never capture the current scene."""
    new = _valid_name(new)
    views = _native_views(session)
    if old not in views:
        raise UserError('Bookmark "%s" does not exist.' % old)
    if old == new:
        return
    if new in views:
        raise UserError('Bookmark "%s" already exists. Choose a different name.' % new)
    views[new] = views.pop(old)
    records = _manager(session).bookmarks
    if old in records:
        records[new] = records.pop(old)


def _alive(obj):
    return obj is not None and not getattr(obj, "deleted", False)


def _attributes(obj, names):
    return {name: copy_state(getattr(obj, name))
            for name in names if hasattr(obj, name)}


def _capture_collection(collection, names):
    # Individual references preserve alignment after any atom/residue deletion;
    # ChimeraX collections themselves silently shrink when members are deleted.
    return {"objects": list(collection), "attributes": _attributes(collection, names)}


def _capture_model(model, chosen):
    from chimerax.atomic import Structure, PseudobondGroup, MolecularSurface
    from chimerax.core.models import Surface
    attrs = []
    if chosen["visibility"]:
        attrs += ["display", "display_positions"]
    if chosen["colors"]:
        attrs += ["colors"]
    if chosen["selection"] and not isinstance(model, (Structure, PseudobondGroup, MolecularSurface)):
        attrs += ["selected", "selected_positions"]
    record = {"model": model, "attributes": _attributes(model, attrs), "collections": {}}
    if isinstance(model, Surface):
        surface_attrs = []
        if chosen["visibility"]:
            surface_attrs += ["triangle_mask", "display_style"]
        if chosen["colors"]:
            surface_attrs += ["vertex_colors"]
        record["attributes"].update(_attributes(model, surface_attrs))
        record["vertex_count"] = 0 if model.vertices is None else len(model.vertices)
        record["triangle_count"] = 0 if model.triangles is None else len(model.triangles)
    if isinstance(model, MolecularSurface):
        if chosen["visibility"]:
            record["attributes"]["show_atoms"] = model.show_atoms
        if chosen["colors"]:
            # These are the same color caches included by ChimeraX's surface
            # undo/session support; restoring them keeps later recomputation
            # from unexpectedly replacing the restored surface colors.
            record["surface_patches"] = {
                "objects": list(model.atoms),
                "attributes": _attributes(model, ("_atom_patch_colors", "_atom_patch_color_mask")),
            }
    collections = record["collections"]
    if isinstance(model, Structure):
        atom_attrs, bond_attrs, residue_attrs = [], [], []
        if chosen["visibility"]:
            atom_attrs += ["displays", "hides", "draw_modes"]
            bond_attrs += ["displays", "hides"]
            residue_attrs += ["ribbon_displays", "ring_displays"]
        if chosen["colors"]:
            atom_attrs += ["colors"]
            bond_attrs += ["colors", "halfbonds"]
            residue_attrs += ["ribbon_colors", "ring_colors"]
        if chosen["selection"]:
            atom_attrs += ["selected"]
            bond_attrs += ["selected"]
        for kind, collection, names in (("atoms", model.atoms, atom_attrs),
                                        ("bonds", model.bonds, bond_attrs),
                                        ("residues", model.residues, residue_attrs)):
            if names:
                collections[kind] = _capture_collection(collection, names)
    elif isinstance(model, PseudobondGroup):
        names = []
        if chosen["visibility"]:
            names += ["displays", "hides"]
        if chosen["colors"]:
            names += ["colors", "halfbonds"]
        if chosen["selection"]:
            names += ["selected"]
        collections["pseudobonds"] = _capture_collection(model.pseudobonds, names)
    return record


def _restore_model(record):
    model = record["model"]
    for name, value in record["attributes"].items():
        if value is not None:
            if name in ("display_positions", "selected_positions", "colors") and len(value) != len(model.positions):
                continue
            if name == "vertex_colors" and (model.vertices is None or len(value) != len(model.vertices)):
                continue
            if name == "triangle_mask" and (model.triangles is None or len(value) != len(model.triangles)):
                continue
        setattr(model, name, copy_state(value))
    from chimerax.atomic import Atoms, Bonds, Residues, Pseudobonds
    classes = dict(atoms=Atoms, bonds=Bonds, residues=Residues, pseudobonds=Pseudobonds)
    for kind, saved in record["collections"].items():
        indices = [i for i, obj in enumerate(saved["objects"]) if _alive(obj)]
        if not indices:
            continue
        collection = classes[kind]([saved["objects"][i] for i in indices])
        for name, values in saved["attributes"].items():
            setattr(collection, name, values[indices].copy())
    patches = record.get("surface_patches")
    if patches is not None:
        from numpy import zeros
        saved_indices = {atom: i for i, atom in enumerate(patches["objects"]) if _alive(atom)}
        matched = [(i, saved_indices[atom]) for i, atom in enumerate(model.atoms) if atom in saved_indices]
        for name, values in patches["attributes"].items():
            if values is None:
                setattr(model, name, None)
                continue
            restored = zeros((len(model.atoms),) + values.shape[1:], dtype=values.dtype)
            if matched:
                current_indices, previous_indices = zip(*matched)
                restored[list(current_indices)] = values[list(previous_indices)]
            setattr(model, name, restored)


def _pruned_record(record):
    cleaned = dict(record)
    cleaned["models"] = models = []
    for model_state in record["models"]:
        model = model_state["model"]
        if not _alive(model) or not model.SESSION_SAVE:
            continue
        saved_model = dict(model_state)
        if "surface_patches" in saved_model:
            saved_model["surface_patches"] = _pruned_collection(saved_model["surface_patches"])
        saved_model["collections"] = collections = {}
        for kind, saved in model_state["collections"].items():
            collections[kind] = _pruned_collection(saved)
        models.append(saved_model)
    return cleaned


def _pruned_collection(saved):
    indices = [i for i, obj in enumerate(saved["objects"]) if _alive(obj)]
    return {
        "objects": [saved["objects"][i] for i in indices],
        "attributes": {name: None if values is None else values[indices].copy()
                       for name, values in saved["attributes"].items()},
    }


class CameraBookmarkState(StateManager):
    """Session-persisted condition records supplementing native NamedViews."""

    def __init__(self):
        self.bookmarks = {}

    def take_snapshot(self, session, flags):
        native = _native_views(session)
        return {"version": 1, "bookmarks": {
            name: _pruned_record(record) for name, record in self.bookmarks.items()
            if name in native and record.get("native_view") is native[name]}}

    @staticmethod
    def restore_snapshot(session, data):
        manager = _manager(session)
        manager.bookmarks = data["bookmarks"]
        return manager

    def reset_state(self, session):
        self.bookmarks.clear()
