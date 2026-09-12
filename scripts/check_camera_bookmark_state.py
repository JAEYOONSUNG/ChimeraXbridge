"""ChimeraX --nogui --notools --exit --script <this file>.

Creates only disposable local models and a temporary .cxs; never saves preferences.
"""
import importlib.util
from pathlib import Path
import sys
import tempfile

import numpy as np
from chimerax.core.configfile import ConfigFile
ConfigFile.save = lambda *args, **kwargs: None
from chimerax.atomic import AtomicStructure, MolecularSurface, check_for_changes
from chimerax.core.models import Model, Surface
from chimerax.core.commands import run, StringArg
from chimerax.geometry import translation
from chimerax.std_commands.view import NamedView, _named_views

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "chimerax.codex_bridge", root / "src/__init__.py",
    submodule_search_locations=[str(root / "src")])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
from chimerax.codex_bridge.camera_bookmark_state import (
    CameraBookmarkState, DEFAULT_OPTIONS, _manager, save_bookmark,
    restore_bookmark, rename_bookmark, delete_bookmark, bookmark_options,
    list_bookmarks,
)
assert package.bundle_api.get_class("CameraBookmarkState") is CameraBookmarkState


def equal(actual, expected):
    np.testing.assert_array_equal(actual, expected)


def structure(name):
    model = AtomicStructure(session, name=name)
    residue = model.new_residue("ALA", "A", 1)
    atoms = []
    for i, atom_name in enumerate(("N", "CA", "C")):
        atom = model.new_atom(atom_name, "N" if i == 0 else "C")
        atom.coord = (i * 1.4, 0, 0)
        residue.add_atom(atom)
        atoms.append(atom)
    model.new_bond(atoms[0], atoms[1])
    model.new_bond(atoms[1], atoms[2])
    session.models.add([model])
    return model


model = structure("bookmark-test")
surface = Surface("bookmark-surface", session)
surface.set_geometry(np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], np.float32),
                     None, np.array([[0, 1, 2]], np.int32))
session.models.add([surface])
model.atoms.displays = [True, False, True]
model.atoms.colors = [[110, 120, 130, 255], [30, 50, 70, 128], [10, 20, 30, 100]]
model.residues.ribbon_displays = True
model.residues.ribbon_colors = (80, 90, 100, 120)
model.atoms.selected = [False, True, False]
surface.vertex_colors = np.array([[10, 20, 30, 100], [40, 50, 60, 120], [70, 80, 90, 150]], np.uint8)
surface.triangle_mask = np.array([False])
session.main_view.background_color = (0.2, 0.3, 0.4, 1)
session.main_view.lighting.ambient_light_intensity = 0.44
session.main_view.silhouette.enabled = True
session.main_view.silhouette.thickness = 2.5
saved_colors = model.atoms.colors.copy()
saved_surface = surface.vertex_colors.copy()
saved_camera = session.main_view.camera.position.matrix.copy()
saved_background = session.main_view.background_color.copy()
all_options = dict(DEFAULT_OPTIONS, selection=True)
save_bookmark(session, '조건 "A"; special', all_options)
model.atoms.displays = True
model.atoms.colors = (255, 255, 0, 255)
model.residues.ribbon_displays = False
model.residues.ribbon_colors = (250, 250, 250, 255)
model.atoms.selected = [True, False, True]
model.position = translation((4, 5, 6))
surface.vertex_colors = None
surface.triangle_mask = None
session.main_view.camera.position = translation((8, 9, 10))
session.main_view.background_color = (1, 1, 1, 1)
session.main_view.lighting.ambient_light_intensity = 0.9
session.main_view.silhouette.enabled = False
session.main_view.silhouette.thickness = 5.0
restore_bookmark(session, '조건 "A"; special')
equal(model.atoms.colors, saved_colors)
equal(model.atoms.displays, [True, False, True])
equal(model.atoms.selected, [False, True, False])
equal(model.residues.ribbon_colors, [[80, 90, 100, 120]])
equal(model.residues.ribbon_displays, [True])
equal(model.position.matrix[:, 3], [0, 0, 0])
equal(surface.vertex_colors, saved_surface)
equal(surface.triangle_mask, [False])
equal(session.main_view.camera.position.matrix, saved_camera)
equal(session.main_view.background_color, saved_background)
assert session.main_view.lighting.ambient_light_intensity == 0.44
assert session.main_view.silhouette.enabled
assert session.main_view.silhouette.thickness == 2.5

# Turning conditions off leaves those conditions untouched on restore.
save_bookmark(session, "color only", {"colors": True})
model.atoms.colors = (255, 0, 0, 255)
model.atoms.displays = False
model.atoms.selected = True
model.position = translation((9, 8, 7))
session.main_view.camera.position = translation((3, 2, 1))
session.main_view.background_color = (0, 0, 0, 1)
restore_bookmark(session, "color only")
equal(model.atoms.colors, saved_colors)
equal(model.atoms.displays, [False] * 3)
equal(model.atoms.selected, [True] * 3)
equal(model.position.matrix[:, 3], [9, 8, 7])
equal(session.main_view.camera.position.matrix[:, 3], [3, 2, 1])
equal(session.main_view.background_color[:3], [0, 0, 0])

# Rename preserves the saved camera rather than replacing it with the current one.
native = _named_views(session).views['조건 "A"; special']
before_rename = session.main_view.camera.position.matrix.copy()
rename_bookmark(session, '조건 "A"; special', "renamed")
assert _named_views(session).views["renamed"] is native
equal(session.main_view.camera.position.matrix, before_rename)
try:
    rename_bookmark(session, "renamed", "color only")
except Exception as err:
    assert "already exists" in str(err)
else:
    raise AssertionError("Rename must reject another bookmark's name")

# Native-only views remain available and report the camera-only condition.
_named_views(session).views["native"] = NamedView(session.main_view, (0, 0, 0), session.models.list())
assert bookmark_options(session, "native") == {key: key == "camera" for key in DEFAULT_OPTIONS}
assert "native" in list_bookmarks(session)
restore_bookmark(session, "native")

# Every condition independently restores only its corresponding state.
for condition in DEFAULT_OPTIONS:
    model.atoms.colors = (40, 50, 60, 120)
    model.atoms.displays = True
    model.atoms.selected = [False, True, False]
    session.main_view.camera.position = translation((1, 2, 3))
    session.main_view.lighting.ambient_light_intensity = 0.4
    save_bookmark(session, "one-condition", {condition: True})
    model.atoms.colors = (70, 80, 90, 200)
    model.atoms.displays = False
    model.atoms.selected = [True, False, True]
    session.main_view.camera.position = translation((4, 5, 6))
    session.main_view.lighting.ambient_light_intensity = 0.8
    restore_bookmark(session, "one-condition")
    equal(model.atoms.colors[0], [40, 50, 60, 120] if condition == "colors" else [70, 80, 90, 200])
    equal(model.atoms.displays, [condition == "visibility"] * 3)
    equal(model.atoms.selected, [False, True, False] if condition == "selection" else [True, False, True])
    equal(session.main_view.camera.position.matrix[:, 3], [1, 2, 3] if condition == "camera" else [4, 5, 6])
    assert session.main_view.lighting.ambient_light_intensity == (0.4 if condition == "lighting" else 0.8)
delete_bookmark(session, "one-condition")

# A native overwrite drops old conditions without changing the original entry.
save_bookmark(session, "native-overwrite", all_options)
_named_views(session).views["native-overwrite"] = NamedView(session.main_view, (0, 0, 0), session.models.list())
assert bookmark_options(session, "native-overwrite") == bookmark_options(session, "native")

# Deleted atomic objects must not shift the saved arrays onto different atoms.
model.atoms[0].delete()
model.atoms.colors = (200, 200, 200, 255)
restore_bookmark(session, "color only")
equal(model.atoms.colors, saved_colors[1:])

# Closed model IDs may be reused; object references keep replacement models safe.
closed = structure("to-close")
save_bookmark(session, "closed", {"colors": True})
closed_id = closed.id
session.models.close([closed])
replacement = AtomicStructure(session, name="replacement")
session.models.add([replacement], minimum_id=closed_id[0])
replacement.colors = [[222, 111, 44, 255]]
restore_result = restore_bookmark(session, "closed")
assert restore_result["skipped_models"] == 1
equal(replacement.colors, [[222, 111, 44, 255]])

# Molecular surfaces keep atom patches and color caches through recomputation.
surface_structure = structure("surface-test")
run(session, "surface #" + surface_structure.id_string)
molecular_surface = next(m for m in session.models.list(type=MolecularSurface)
                         if m.atoms.unique_structures[0] is surface_structure)
molecular_surface.color_atom_patches(surface_structure.atoms[:2], color=(120, 130, 140, 110))
molecular_surface.hide(surface_structure.atoms[:1])
saved_patch_colors = molecular_surface._atom_patch_colors.copy()
saved_patch_mask = molecular_surface._atom_patch_color_mask.copy()
saved_shown_atoms = list(molecular_surface.show_atoms)
save_bookmark(session, "molecular-surface", {"visibility": True, "colors": True})
molecular_surface.color_atom_patches(surface_structure.atoms, color=(250, 0, 0, 255))
molecular_surface.show(surface_structure.atoms)
restore_bookmark(session, "molecular-surface")
equal(molecular_surface._atom_patch_colors, saved_patch_colors)
equal(molecular_surface._atom_patch_color_mask, saved_patch_mask)
assert list(molecular_surface.show_atoms) == saved_shown_atoms
check_for_changes(session)
equal(molecular_surface._atom_patch_colors, saved_patch_colors)

# Round-trip the manager, native view, object references and condition arrays.
with tempfile.TemporaryDirectory(prefix="bookmark-state-") as folder:
    session_path = str(Path(folder) / "conditions.cxs")
    run(session, "save " + StringArg.unparse(session_path))
    run(session, "close session")
    assert not _manager(session).bookmarks
    run(session, "open " + StringArg.unparse(session_path))
    assert "renamed" in list_bookmarks(session)
    assert bookmark_options(session, "renamed") == all_options
    molecular_surface = session.models.list(type=MolecularSurface)[0]
    restore_bookmark(session, "molecular-surface")
    equal(molecular_surface._atom_patch_colors, saved_patch_colors)
    assert len(molecular_surface.show_atoms) == len(saved_shown_atoms)
    model = next(m for m in session.models.list(type=AtomicStructure) if m.name == "bookmark-test")
    model.atoms.colors = (1, 2, 3, 255)
    restore_bookmark(session, "renamed")
    equal(model.atoms.colors, saved_colors[1:])
    equal(model.atoms.selected, [True, False])
    equal(session.main_view.camera.position.matrix, saved_camera)
    delete_bookmark(session, "renamed")
    assert "renamed" not in list_bookmarks(session)
    assert "renamed" not in _manager(session).bookmarks

print("CAMERA_BOOKMARK_STATE_OK: condition isolation, alpha colors, selection, deletion, rename, native views, .cxs round-trip")
