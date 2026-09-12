"""Run using run_quick_check.py views: disposable native models and camera."""
import importlib.util
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from chimerax.atomic import Atom, AtomicStructure, MolecularSurface
from chimerax.core.commands import run
from chimerax.core.models import Surface
from chimerax.geometry import rotation, translation

root = Path(__file__).resolve().parents[1]
module_spec = importlib.util.spec_from_file_location("quick_views_tested", root / "src/quick_views.py")
quick_views = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(quick_views)


def make_model(name, with_sites=True):
    model = AtomicStructure(session, name=name)
    metadata = []
    previous_c = None
    for number in range(1, 22):
        residue = model.new_residue("ALA", "A", number)
        metadata.append(dict(name="ALA", chain="A", number=number, polymer="protein", category="main"))
        last = None
        atom_list = []
        for atom_name, element, offset in (("N", "N", (0, 0, 0)), ("CA", "C", (1.3, .4, 0)),
                                            ("C", "C", (2.5, 0, 0)), ("O", "O", (2.5, -1, 0)),
                                            ("CB", "C", (1.3, 1.7, 0))):
            atom = model.new_atom(atom_name, element)
            atom.coord = np.array((3.8 * number, .5 * (number % 2), 0)) + offset
            residue.add_atom(atom)
            atom_list.append(atom)
            if last is not None and atom_name != "CB":
                model.new_bond(last, atom)
            last = atom
        model.new_bond(atom_list[1], atom_list[-1])
        if previous_c is not None:
            model.new_bond(previous_c, atom_list[0])
        previous_c = atom_list[2]
    if with_sites:
        for name_, category, number, coords in (
                ("LIG", "ligand", 50, ((11, 2.5, 1), (12, 3, 1), (13, 3, 1), (12, 4, 1))),
                ("GOL", "ligand", 51, ((400, 0, 0), (401, 0, 0))),
                ("ZN", "ions", 60, ((26, 3, 1),)),
                ("HOH", "solvent", 70, ((-20, 0, 0),)),
                ("HOH", "solvent", 71, ((-25, 0, 0),))):
            residue = model.new_residue(name_, "B", number)
            metadata.append(dict(name=name_, chain="B", number=number, polymer="other", category=category))
            previous = None
            for j, coord in enumerate(coords):
                element = "Zn" if category == "ions" else "O" if category == "solvent" else "C"
                atom = model.new_atom(element + str(j), element)
                atom.coord = coord
                residue.add_atom(atom)
                if previous is not None:
                    model.new_bond(previous, atom)
                previous = atom
        for number in (80, 81):
            residue = model.new_residue("DA", "D", number)
            metadata.append(dict(name="DA", chain="D", number=number, polymer="nucleic", category="main"))
            previous = None
            for j, atom_name in enumerate(("P", "C4'", "C1'", "N9", "C8")):
                atom = model.new_atom(atom_name, "P" if j == 0 else "N" if j == 3 else "C")
                atom.coord = (number - 30 + j, 9, 1)
                residue.add_atom(atom)
                if previous is not None:
                    model.new_bond(previous, atom)
                previous = atom
    session.models.add([model])
    model._fixture_metadata = metadata
    return model


def snapshot(models):
    saved = []
    for model in models:
        atoms, residues = model.atoms, model.residues
        residue_ids = {r: i for i, r in enumerate(residues)}
        spec = "#" + model.id_string
        residue_rows = [dict(meta, spec=f"{spec}/{r.chain_id}:{r.number}", selected=bool(r.atoms.selected.any()), ss_type=0)
                        for r, meta in zip(residues, model._fixture_metadata)]
        saved.append(dict(spec=spec, name=model.name, coords=atoms.scene_coords.copy(),
                          elements=atoms.element_numbers.copy(), atom_names=tuple(atoms.names),
                          residue_index=np.asarray([residue_ids[a.residue] for a in atoms], dtype=np.int32),
                          bfactors=atoms.bfactors.copy(), occupancies=atoms.occupancies.copy(),
                          residues=tuple(residue_rows), bonds=np.empty((0, 2), np.int32)))
    return dict(models=saved, target_label=" + ".join(m.name for m in models),
                selection_specs=tuple(r["spec"] for s in saved for r in s["residues"] if r["selected"]), signature="test")


def context(models):
    return dict(models=tuple(models), model_by_spec={"#" + m.id_string: m for m in models},
                snapshot=snapshot(models), selection_specs=())


def scene_state(model):
    return dict(coords=model.atoms.coords.copy(), colors=model.atoms.colors.copy(),
                ribbon_colors=model.residues.ribbon_colors.copy(), displays=model.atoms.displays.copy(),
                ribbons=model.residues.ribbon_displays.copy(), modes=model.atoms.draw_modes.copy(),
                selected=model.atoms.selected.copy(), position=model.position.matrix.copy(), display=model.display)


def assert_state(model, state, names=None):
    actual = scene_state(model)
    for key in (state if names is None else names):
        np.testing.assert_array_equal(actual[key], state[key], err_msg=key)


target = make_model("target")
unrelated = make_model("unrelated", False)
unrelated.position = translation((1000, 20, 30))
target.position = translation((10, 20, 30)) * rotation((0, 0, 1), 22)
target.atoms.colors = np.array([[30 + i % 80, 110, 160, 230] for i in range(len(target.atoms))], np.uint8)
target.residues.ribbon_colors = (90, 120, 145, 235)
unrelated.atoms.colors = (150, 20, 100, 100)
unrelated.atoms.draw_modes = Atom.BALL_STYLE
unrelated.residues.ribbon_colors = (9, 25, 70, 100)
unrelated.display = False
target.residues[2].atoms.selected = True
target.residues[-3].atoms.selected = True  # selected water must survive tidy
target_state = scene_state(target)
other_state = scene_state(unrelated)
run(session, "surface #" + target.id_string)
surface = next(m for m in session.models.list(type=MolecularSurface)
               if target in m.atoms.unique_structures)
annotation = Surface("user annotation", session)
annotation.set_geometry(np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], np.float32), None,
                        np.array([[0, 1, 2]], np.int32))
session.models.add([annotation])
model_ids = [id(m) for m in session.models.list()]
annotation_colors = annotation.colors.copy()
camera = translation((5, 8, 140)) * rotation((0, 1, 0), 15)
session.main_view.camera.position = camera
before_camera = camera.matrix.copy()
ctx = context([target])

# Worker receives only immutable copied arrays and never changes the source.
original_snapshot_coords = ctx["snapshot"]["models"][0]["coords"].copy()
for model in ctx["snapshot"]["models"]:
    for value in model.values():
        if isinstance(value, np.ndarray):
            value.flags.writeable = False
with ThreadPoolExecutor(max_workers=1) as pool:
    view_result = pool.submit(quick_views.compute, ctx["snapshot"], "view").result()
assert view_result["metrics"] and view_result["details"]
quick_views.apply(session, view_result, ctx)
assert target.residues[:21].ribbon_displays.all()
assert not target.residues[1].atoms.displays.any()
assert target.residues[2].atoms.displays.all()
assert not target.residues[-4].atoms.displays.any()  # unselected water
assert target.residues[-3].atoms.displays.all()  # selected water
assert target.residues[-2:].atoms.displays.all()  # nucleic bases
assert target.residues[23].atoms.draw_modes[0] == Atom.SPHERE_STYLE
assert not surface.display
assert annotation.display
np.testing.assert_array_equal(annotation.colors, annotation_colors)
assert_state(target, target_state, ("coords", "colors", "ribbon_colors", "selected", "position"))
assert_state(unrelated, other_state)
np.testing.assert_array_equal(session.main_view.camera.position.matrix, before_camera)
np.testing.assert_array_equal(ctx["snapshot"]["models"][0]["coords"], original_snapshot_coords)

figure_result = quick_views.compute(ctx["snapshot"], "figure")
session.main_view.highlight_thickness = 2
figure_selection = target.atoms.selected.copy()
quick_views.apply(session, figure_result, ctx)
assert session.main_view.silhouette.enabled and session.main_view.silhouette.thickness == 1
assert session.main_view.highlight_thickness == 0
np.testing.assert_array_equal(target.atoms.selected, figure_selection)
assert "Selection outlines are hidden" in " ".join(figure_result["summary"])
assert session.main_view.lighting.multishadow == 0
assert not session.main_view.lighting.depth_cue
np.testing.assert_array_equal(session.main_view.background_color[:3], (1, 1, 1))
np.testing.assert_array_equal(session.main_view.camera.position.matrix, before_camera)
assert "Scene-wide change" in " ".join(figure_result["details"])
assert_state(target, target_state, ("coords", "colors", "ribbon_colors", "selected", "position"))
assert_state(unrelated, other_state)
assert [id(m) for m in session.models.list()] == model_ids

# Selection precedes both observed ligands and overview, and fits a tight bbox.
target.atoms.selected = False
target.residues[2:4].atoms.selected = True
ctx = context([target])
zoom_result = quick_views.compute(ctx["snapshot"], "zoom")
selected = zoom_result["candidates"][0]
assert selected["source"] == "selection"
assert set(selected["specs"]) == set(ctx["snapshot"]["selection_specs"])
selected_span = np.max(np.subtract(selected["bounds"][1], selected["bounds"][0]))
overview_span = np.max(np.subtract(zoom_result["candidates"][-1]["bounds"][1], zoom_result["candidates"][-1]["bounds"][0]))
assert selected_span < overview_span / 4
direction = session.main_view.camera.view_direction().copy()
quick_views.apply(session, zoom_result, ctx, len(zoom_result["candidates"]) - 1)
overview_distance = np.linalg.norm(session.main_view.camera.position.origin() - session.main_view.center_of_rotation)
quick_views.apply(session, zoom_result, ctx)
selected_distance = np.linalg.norm(session.main_view.camera.position.origin() - session.main_view.center_of_rotation)
assert selected_distance < overview_distance / 4, (selected_distance, overview_distance)
np.testing.assert_allclose(session.main_view.camera.view_direction(), direction, atol=1e-6)
np.testing.assert_allclose(session.main_view.center_of_rotation, np.mean(selected["bounds"], axis=0), atol=1e-5)
assert set(ctx["snapshot"]["selection_specs"]) == set(snapshot([target])["selection_specs"])
assert_state(unrelated, other_state)

# Evidence comes from loaded geometry; isolated additives rank below neighbors.
target.atoms.selected = False
ctx = context([target])
ligand_result = quick_views.compute(ctx["snapshot"], "zoom")
ligand = ligand_result["candidates"][0]
assert ligand["source"] == "observed-ligand" and "LIG" in ligand["label"]
assert ligand["contact_count"] > 0
assert all(spec.startswith("#" + target.id_string + "/") for spec in ligand["specs"])
assert "unverified" in " ".join(ligand["evidence"])
assert "catalytic function has not been established" in " ".join(ligand_result["summary"])
surface.display = True
quick_views.apply(session, ligand_result, ctx)
assert not surface.display
assert annotation.display
assert_state(unrelated, other_state)

# A target with no ligands has an honest overview, not a guessed catalytic triad.
ctx_apo = context([unrelated])
apo_result = quick_views.compute(ctx_apo["snapshot"], "zoom")
assert apo_result["candidates"][0]["source"] == "overview"
assert "unavailable" in " ".join(apo_result["candidates"][0]["evidence"])
assert "target overview" in " ".join(apo_result["summary"])
before_target = scene_state(target)
quick_views.apply(session, apo_result, ctx_apo)
assert_state(target, before_target)

# Ion-only observations produce a geometry-limited alternative when no ligand exists.
ion_snapshot = snapshot([target])
for row in ion_snapshot["models"][0]["residues"]:
    if row["category"] == "ligand":
        row["category"] = "other"
ion_result = quick_views.compute(ion_snapshot, "zoom")
assert ion_result["candidates"][0]["source"] == "observed-ion"
assert ion_result["candidates"][0]["cutoff"] == 3.5

# A single amino acid has no cartoon; a quick view must still show its atoms.
fragment = AtomicStructure(session, name="single amino acid")
residue = fragment.new_residue("ALA", "A", 1)
previous = None
for index, atom_name in enumerate(("N", "CA", "C")):
    atom = fragment.new_atom(atom_name, "N" if index == 0 else "C")
    atom.coord = (index * 1.3, 0, 0)
    residue.add_atom(atom)
    if previous is not None:
        fragment.new_bond(previous, atom)
    previous = atom
fragment._fixture_metadata = [dict(name="ALA", chain="A", number=1, polymer="protein", category="main")]
session.models.add([fragment])
fragment.atoms.displays = False
fragment_context = context([fragment])
quick_views.apply(session, quick_views.compute(fragment_context["snapshot"], "view"), fragment_context)
assert fragment.atoms.displays.all()
assert not fragment.residues.ribbon_displays.any()

# Bounded-memory fallback agrees with a known contact / absence control.
points = np.array([[0., 0, 0], [2., 0, 0], [100., 0, 0]])
np.testing.assert_array_equal(quick_views._neighbor_indices(points, points[:1], 3.5, None), [0, 1])
assert len(quick_views._neighbor_indices(points, np.array([[200., 0, 0]]), 3.5, None)) == 0
try:
    quick_views.compute(ctx["snapshot"], "zoom", cancelled=lambda: True)
except InterruptedError:
    pass
else:
    raise AssertionError("Cancellation must stop computation")
try:
    quick_views.apply(session, zoom_result, ctx, candidate=99)
except ValueError:
    pass
else:
    raise AssertionError("An invalid candidate must fail")
print("QUICK_VIEWS_OK: native View/Figure/Zoom, colors/camera/scope, selected tight fit, ligand/ion/apo evidence, immutable worker and cancellation")
