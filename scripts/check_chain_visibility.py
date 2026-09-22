"""Headless real atomic/surface visibility, restoration, and undo checks."""

import importlib.util
from pathlib import Path
import sys

import numpy as np
from chimerax.atomic import AtomicStructure, Atoms, MolecularSurface, check_for_changes
from chimerax.core.models import Model
from chimerax.core.errors import UserError

assert not session.ui.is_gui
root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("chimerax.codex_bridge", root / "src/__init__.py",
    submodule_search_locations=[str(root / "src")])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
from chimerax.codex_bridge.chain_visibility import ChainVisibilityController


def make_model(name):
    model = AtomicStructure(session, name=name)
    chains = {}
    for c, chain in enumerate(("A", "B")):
        residues = []
        previous = None
        for i in range(2):
            residue = model.new_residue("ALA", chain, i + 1)
            atoms = []
            for j, (name, element) in enumerate((("N", "N"), ("CA", "C"), ("C", "C"), ("O", "O"))):
                atom = model.new_atom(name, element)
                residue.add_atom(atom)
                atom.coord = (i * 6 + j * 1.4, c * 20, 0)
                atoms.append(atom)
            for a, b in zip(atoms, atoms[1:]):
                model.new_bond(a, b)
            if previous is not None:
                model.new_bond(previous, atoms[0])
            previous = atoms[2]
            residues.append(residue)
        chains[chain] = Atoms([a for residue in residues for a in residue.atoms])
    model.ss_assigned = True
    session.models.add([model])
    check_for_changes(session)
    return model, chains["A"], chains["B"]


def make_surface(model):
    atoms = model.atoms
    surface = MolecularSurface(session, atoms, atoms.copy(), 1.4, 0.5, None, None,
                               "Shared molecular surface", (90, 110, 150, 255), None, True, update=False)
    vertices = np.array([(i, j, 0) for i in range(5) for j in (0, 1, 2)], dtype=np.float32)
    triangles = np.arange(15, dtype=np.int32).reshape((-1, 3))
    surface.set_geometry(vertices, np.tile((0, 0, 1), (15, 1)).astype(np.float32), triangles)
    surface._vertex_to_atom = np.array([0]*3 + [4]*3 + [8]*3 + [12]*3 + [0, 0, 8], dtype=np.int32)
    surface._vertex_to_atom_count = len(atoms)
    model.add([surface])
    assert surface.has_atom_patches()
    return surface


def settle():
    check_for_changes(session)


model, a, b = make_model("Visibility fixture")
controller = ChainVisibilityController(session)
model.atoms.displays = True
model.residues.ribbon_displays = False
assert controller.state(a) == "shown"
a.unique_residues[1].atoms.displays = False
assert controller.state(a) == "mixed"
a.displays = False
assert controller.state(a) == "hidden"
a.unique_residues.ribbon_displays = True
assert controller.state(a) == "shown", "Cartoon-only chain was reported hidden"
model.display = False
assert controller.state(a) == "hidden", "Hidden parent ignored"
model.display = True

# Preserve sparse atom/cartoon representations, colors, coordinates and camera.
a.displays = [False, True, False, False, False, False, True, False]
a.unique_residues.ribbon_displays = [True, False]
original_atoms = a.displays.copy()
original_ribbons = a.unique_residues.ribbon_displays.copy()
other_atoms = b.displays.copy()
colors, coords = model.atoms.colors.copy(), model.atoms.coords.copy()
camera = session.main_view.camera.position.matrix.copy()
controller.set_visible(a, False)
assert controller.state(a) == "hidden"
assert np.array_equal(b.displays, other_atoms)
session.undo.undo()
assert np.array_equal(a.displays, original_atoms)
assert np.array_equal(a.unique_residues.ribbon_displays, original_ribbons)
session.undo.redo()
assert controller.state(a) == "hidden"
controller.set_visible(a, True)
assert np.array_equal(a.displays, original_atoms)
assert np.array_equal(a.unique_residues.ribbon_displays, original_ribbons)
assert np.array_equal(model.atoms.colors, colors) and np.array_equal(model.atoms.coords, coords)
assert np.array_equal(session.main_view.camera.position.matrix, camera)

# A later external style change becomes the next hide/show baseline.
controller.set_visible(a, False)
a.displays = True
a.unique_residues.ribbon_displays = False
controller.set_visible(a, False)
controller.set_visible(a, True)
assert a.displays.all() and not a.unique_residues.ribbon_displays.any()
a.displays = original_atoms
a.unique_residues.ribbon_displays = original_ribbons

# Show all must actually reveal chains that were already hidden before Hide.
b.displays = False
b.unique_residues.ribbon_displays = False
controller.set_visible(b, False)
controller.show_all()
assert controller.state(b) == "shown"
b.displays = other_atoms
b.unique_residues.ribbon_displays = False

# Shared surfaces preserve existing custom triangle masks, boundary triangles,
# show_atoms, and automatic remasking callbacks across toggle and undo.
surface = make_surface(model)
original_mask = np.array([True, False, True, True, True])
surface.triangle_mask = original_mask.copy()
remask = lambda *args: None
surface.auto_remask_triangles = remask
surface_colors = surface.color.copy()
controller.set_visible(a, False)
assert not (surface.show_atoms & a)
assert np.array_equal(surface.triangle_mask, [False, False, True, True, False])
assert controller.state(a) == "hidden" and controller.state(b) == "shown"
session.undo.undo()
assert np.array_equal(surface.triangle_mask, original_mask)
assert surface.auto_remask_triangles is remask
session.undo.redo()
assert not surface.triangle_mask[:2].any()
controller.set_visible(a, True)
assert np.array_equal(surface.triangle_mask, original_mask)
assert surface.auto_remask_triangles is remask
assert np.array_equal(surface.color, surface_colors)

# Editing a different chain's mask while A is hidden survives A's restoration.
controller.set_visible(a, False)
changed = surface.triangle_mask.copy()
changed[2] = False
surface.triangle_mask = changed
controller.set_visible(a, True)
assert np.array_equal(surface.triangle_mask, [True, False, False, True, True])
surface.triangle_mask = original_mask.copy()
surface.auto_remask_triangles = remask

# Failure after atom flags have already changed rolls the whole edit back.
before_atoms = model.atoms.displays.copy()
before_ribbons = model.residues.ribbon_displays.copy()
before_mask = surface.triangle_mask.copy()
before_surface_atoms = surface.show_atoms.pointers.copy()
undo_count = len(session.undo.undo_stack)
allowed = controller._allowed
def failed_mask(*args):
    raise RuntimeError("injected mask failure")
controller._allowed = failed_mask
try:
    controller.set_visible(a, False)
except RuntimeError as error:
    assert "injected mask failure" in str(error)
else:
    raise AssertionError("Injected surface failure was swallowed")
finally:
    controller._allowed = allowed
assert np.array_equal(model.atoms.displays, before_atoms)
assert np.array_equal(model.residues.ribbon_displays, before_ribbons)
assert np.array_equal(surface.triangle_mask, before_mask)
assert np.array_equal(surface.show_atoms.pointers, before_surface_atoms)
assert len(session.undo.undo_stack) == undo_count

# Overlapping hides restore boundary triangles only when both chains return.
controller.set_visible(a, False)
controller.set_visible(b, False)
controller.set_visible(a, True)
assert not surface.triangle_mask[4]
controller.set_visible(b, True)
assert np.array_equal(surface.triangle_mask, original_mask)

# Native external surface hides are reflected, then revealed using the same
# surface representation rather than manufacturing a new one.
style_atoms = model.atoms.displays.copy()
style_ribbons = model.residues.ribbon_displays.copy()
model.atoms.displays = False
model.residues.ribbon_displays = False
surface.hide(a)
assert controller.state(a) == "hidden"
controller.set_visible(a, True)
assert controller.state(a) == "shown" and not a.displays.any()
assert not a.unique_residues.ribbon_displays.any()
surface.triangle_mask = original_mask.copy()
surface.display = False
controller.set_visible(a, True)
assert surface.display and not (surface.show_atoms & b)
controller.set_visible(b, True)
assert np.array_equal(surface.triangle_mask, original_mask)
assert set(surface.show_atoms.pointers) == set(model.atoms.pointers)
model.atoms.displays = style_atoms
model.residues.ribbon_displays = style_ribbons

# Replaced mesh topology must never receive a saved mask for old triangles.
controller.set_visible(a, False)
surface.set_geometry(surface.vertices.copy(), surface.normals.copy(), surface.triangles[::-1].copy())
controller.set_visible(a, True)
assert surface.triangle_mask is None or surface.triangle_mask.all()
surface.set_geometry(surface.vertices.copy(), surface.normals.copy(), surface.triangles[::-1].copy())
surface.triangle_mask = original_mask.copy()

# Isolate and show-all affect molecular representations but leave annotations.
annotation = Model("Unrelated annotation", session)
session.models.add([annotation])
controller.isolate(a)
assert controller.state(a) != "hidden" and controller.state(b) == "hidden"
assert annotation.display
session.undo.undo()
assert controller.state(b) == "shown"
session.undo.redo()
assert controller.state(b) == "hidden"
controller.show_all()
assert controller.state(b) == "shown" and annotation.display

# Showing a chain through a hidden structure reveals only that chain. Undo
# returns both the parent's display and the sibling's local representation.
attached_annotation = Model("Attached annotation", session)
model.add([attached_annotation])
independent, ia, ib = make_model("Independently hidden model")
independent.atoms.displays = True
independent.residues.ribbon_displays = True
independent.display = False
independent_flags = (independent.atoms.displays.copy(), independent.residues.ribbon_displays.copy())
b_before = b.displays.copy()
model.display = False
controller.set_visible(a, True)
assert model.display and controller.state(a) != "hidden"
assert controller.state(b) == "hidden" and not attached_annotation.display
assert not independent.display
assert np.array_equal(independent.atoms.displays, independent_flags[0])
assert np.array_equal(independent.residues.ribbon_displays, independent_flags[1])
session.undo.undo()
assert not model.display and attached_annotation.display
assert np.array_equal(b.displays, b_before)
session.undo.redo()
assert controller.state(a) != "hidden" and controller.state(b) == "hidden"
controller.show_all()
session.models.close([independent])

# A mixed surface with no atom patches cannot be changed partially. Rollback
# must include atom/ribbon flags and must not add a spurious undo entry.
surface._vertex_to_atom = None
before = (model.atoms.displays.copy(), model.residues.ribbon_displays.copy(), surface.display,
          surface.show_atoms.pointers.copy(), surface.triangle_mask.copy())
undo_count = len(session.undo.undo_stack)
try:
    controller.set_visible(a, False)
except UserError as error:
    assert "without atom patches" in str(error)
else:
    raise AssertionError("Unsupported partial surface edit was silently accepted")
assert np.array_equal(model.atoms.displays, before[0])
assert np.array_equal(model.residues.ribbon_displays, before[1])
assert surface.display == before[2] and np.array_equal(surface.show_atoms.pointers, before[3])
assert np.array_equal(surface.triangle_mask, before[4])
assert len(session.undo.undo_stack) == undo_count
controller.set_visible(model.atoms, False)
assert not surface.display and controller.state(model.atoms) == "hidden"
controller.set_visible(model.atoms, True)
assert surface.display

# Deleting atoms/models cannot make saved visibility or native undo dereference
# freed objects. Existing survivors can still undo, toggle, and clean up.
session.models.close([surface])
controller.set_visible(a, False)
a[0].delete()
settle()
session.undo.undo()
assert len(a) == 7
controller.set_visible(a, False)
controller.set_visible(a, True)
session.models.close([model])
controller.set_visible(a, False)
assert controller.state(a) == "hidden"
controller.cleanup()
controller.cleanup()
assert not controller._atom_memory and not controller._surface_memory
print("CHAIN_VISIBILITY_OK")
