"""Local-analysis oracle using native ChimeraX atoms and controlled geometry."""
import ast
from concurrent.futures import ThreadPoolExecutor, CancelledError
import copy
import importlib.util
import json
from pathlib import Path
import time

import numpy as np
from chimerax.atomic import AtomicStructure, Residue


root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("quick_analyze_check", root / "src/quick_analyze.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
assert (Residue.SS_HELIX, Residue.SS_STRAND) == (1, 2)

model = AtomicStructure(session, name="Local evidence fixture")


def residue(name, chain, number, atoms):
    r = model.new_residue(name, chain, number)
    for atom_name, element, coord in atoms:
        atom = model.new_atom(atom_name, element)
        r.add_atom(atom)
        atom.coord = coord
        atom.bfactor = 18.0
        atom.occupancy = 1.0
    return r


ala = residue("ALA", "A", 1, [("CA", "C", (0, 0, 0)), ("CB", "C", (1, 0, 0)), ("H", "H", (3.9, 0, 0))])
gly = residue("GLY", "A", 2, [("CA", "C", (12, 0, 0))])
ser = residue("SER", "B", 3, [("CA", "C", (0, 0, 3)), ("OG", "O", (0, 1, 3))])
dna = residue("DA", "D", 7, [("P", "P", (40, 0, 0))])
ligand = residue("LIG", "L", 101, [("C1", "C", (4, 0, 0)), ("C2", "C", (5, 0, 0))])
zinc = residue("ZN", "L", 150, [("ZN", "Zn", (0, 0, -2))])
water = residue("HOH", "W", 1, [("O", "O", (0, 0, 1))])
remote = residue("LIG", "L", 102, [("C1", "C", (100, 0, 0))])
ala.ss_type = Residue.SS_HELIX
ser.ss_type = Residue.SS_STRAND
session.models.add([model])

by_residue = {r: i for i, r in enumerate(model.residues)}
protein = {ala, gly, ser}
records = []
for r in model.residues:
    polymer = "protein" if r in protein else "nucleic" if r == dna else "other"
    category = "main" if polymer != "other" else "solvent" if r == water else "ions" if r == zinc else "ligand"
    records.append({"spec": r.atomspec, "name": r.name, "number": r.number, "chain": r.chain_id,
                    "polymer": polymer, "category": category, "selected": False, "ss_type": int(r.ss_type)})
snapshot = {"target_label": model.name, "selection_specs": (), "signature": "fixture",
            "models": [{"spec": model.atomspec, "name": model.name,
                        "coords": model.atoms.scene_coords.copy(), "elements": model.atoms.element_numbers.copy(),
                        "residue_index": np.array([by_residue[a.residue] for a in model.atoms], dtype=np.int32),
                        "bfactors": model.atoms.bfactors.copy(), "occupancies": model.atoms.occupancies.copy(),
                        "atom_names": tuple(model.atoms.names), "residues": tuple(records),
                        "bonds": np.empty((0, 2), dtype=np.int32)}]}


def plain(value):
    if type(value) in (type(None), bool, int, float, str):
        return
    if type(value) in (list, tuple):
        for child in value:
            plain(child)
        return
    if type(value) is dict:
        for key, child in value.items():
            assert type(key) is str
            plain(child)
        return
    raise AssertionError("Result contains non-plain object %s" % type(value))


with ThreadPoolExecutor(max_workers=1) as pool:
    result = pool.submit(module.compute, snapshot, "analyze").result(timeout=10)
plain(result)
json.dumps(result)
assert result["counts"] == {"models": 1, "atoms": 12, "residues": 8, "protein": 3, "nucleic": 1,
                            "polymer_chains": 3, "ligands": 2, "ions": 1, "waters": 1,
                            "metal_atoms": 1, "selected_residues": 0}, result["counts"]
site = next(candidate for candidate in result["candidates"] if candidate["anchors"][0]["residue_index"] == by_residue[ligand])
assert site["contact_count"] == 1, site
assert site["nearest_distance"] == 3.0, "Hydrogen or solvent incorrectly contributed to polymer distances"
metal = next(candidate for candidate in result["candidates"] if candidate["kind"] == "metal")
assert metal["nearest_distance"] == 2.0 and metal["cutoff"] == 3.2
no_contact = next(candidate for candidate in result["candidates"] if candidate["anchors"][0]["residue_index"] == by_residue[remote])
assert no_contact["contact_count"] == 0 and no_contact["nearest_distance"] is None
assert result["candidates"][0]["id"] == site["id"], result["candidates"]
assert result["interfaces"] == [{"chains": [model.atomspec + "/A", model.atomspec + "/B"],
                                 "residue_count": 2, "nearest_distance": 3.0}], result["interfaces"]
assert any("1 helix, 1 strand" in line for line in result["summary"])

# Known positive and negative fixtures establish contact absence honestly.
moved = copy.deepcopy(snapshot)
moved["models"][0]["coords"][snapshot["models"][0]["residue_index"] == by_residue[ligand]] += (100, 0, 0)
moved_result = module.compute(moved, "analyze")
assert next(c for c in moved_result["candidates"] if c["id"] == site["id"])["contact_count"] == 0
assert np.array_equal(snapshot["models"][0]["coords"], model.atoms.scene_coords)

selected = copy.deepcopy(snapshot)
selected["models"][0]["residues"][by_residue[ala]]["selected"] = True
selection_result = module.compute(selected, "analyze")
assert selection_result["candidates"][0]["kind"] == "selection"
assert selection_result["candidates"][0]["contact_count"] == 1
assert selection_result["candidates"][0]["neighbors"][0]["residue_index"] == by_residue[ser]

missing = {"models": [{"spec": "#99", "coords": np.array([[0., 0., 0.], [float("nan"), 0., 0.]]),
                       "residues": ({"name": "UNK"},)}]}
missing_result = module.compute(missing, "analyze")
plain(missing_result)
assert any("unavailable" in line for line in missing_result["details"])
assert not missing_result["candidates"]
assert module.compute({"models": []}, "analyze")["counts"]["atoms"] == 0
all_coil = copy.deepcopy(snapshot)
for r in all_coil["models"][0]["residues"]:
    r["ss_type"] = 0
assert any("all coil/unassigned" in line for line in module.compute(all_coil)["details"])

# Cross-model neighborhoods use copied scene coordinates and keep model-local indices.
cross = copy.deepcopy(snapshot)
cross["models"].append({"spec": "#second", "coords": np.array([[4., 0., 1.]]),
                         "elements": np.array([6], dtype=np.uint8), "residue_index": np.array([0]),
                         "residues": ({"spec": "#second/Q:8", "name": "ALA", "chain": "Q", "number": 8,
                                       "polymer": "protein", "category": "main"},)})
cross_site = next(c for c in module.compute(cross)["candidates"] if c["id"] == site["id"])
assert cross_site["nearest_distance"] == 1.0
assert cross_site["neighbors"][0]["model_spec"] == "#second"
assert cross_site["neighbors"][0]["residue_index"] == 0
assert any(ref["model_spec"] == model.atomspec for ref in cross_site["neighbors"])
cross["models"][-1]["coords"] += (100, 0, 0)
assert next(c for c in module.compute(cross)["candidates"] if c["id"] == site["id"])["nearest_distance"] == 3.0

mixed_chain = copy.deepcopy(snapshot)
mixed_chain["models"][0]["residues"][by_residue[dna]]["chain"] = "A"
assert module.compute(mixed_chain)["counts"]["polymer_chains"] == 2

# Many loaded additives produce a bounded alternative list, with honest totals.
many = copy.deepcopy(snapshot)
for index in range(40):
    many["models"].append({"spec": "#site%d" % index, "coords": np.array([[2., 0., 0.]]),
                            "elements": np.array([6], dtype=np.uint8), "residue_index": np.array([0]),
                            "residues": ({"spec": "#site%d/L:1" % index, "name": "LIG", "chain": "L",
                                          "number": 1, "polymer": "other", "category": "ligand"},)})
many_result = module.compute(many)
assert len(many_result["candidates"]) == 32
assert many_result["counts"]["ligands"] == 42
assert any("top 32 of 43" in line for line in many_result["details"])

cancel_calls = [0]
def cancel_midway():
    cancel_calls[0] += 1
    return cancel_calls[0] >= 6
for callback in (lambda: True, cancel_midway):
    try:
        module.compute(snapshot, "analyze", cancelled=callback)
    except CancelledError:
        pass
    else:
        raise AssertionError("Cancellation did not stop analysis")
assert cancel_calls[0] >= 6

# Static separation plus the actual worker execution above catch GUI leakage.
tree = ast.parse((root / "src/quick_analyze.py").read_text())
for node in tree.body:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "apply":
        continue
    for child in ast.walk(node):
        if isinstance(child, ast.ImportFrom):
            assert not (child.module or "").startswith(("Qt", "chimerax")), ast.dump(child)
        elif isinstance(child, ast.Import):
            assert all(not alias.name.startswith(("Qt", "chimerax")) for alias in child.names)

# Native apply changes only context atoms, never colors/coordinates/selection.
other = AtomicStructure(session, name="Untouched structure")
other_residue = other.new_residue("GLY", "Z", 1)
other_atom = other.new_atom("CA", "C")
other_residue.add_atom(other_atom)
other_atom.coord = (4, 0, 0)
session.models.add([other])
model.atoms.displays = False
other.atoms.displays = False
model.atoms.colors = (55, 115, 160, 255)
other.atoms.colors = (210, 130, 80, 255)
ala.atoms.selected = True
colors = model.atoms.colors.copy()
selection = model.atoms.selected.copy()
coordinates = model.atoms.coords.copy()
other_state = (other.atoms.colors.tobytes(), other.atoms.displays.tobytes(), other.atoms.coords.tobytes())
context = {"models": (model,), "model_by_spec": {model.atomspec: model, other.atomspec: other},
           "selection_specs": (), "snapshot": snapshot}
tampered = copy.deepcopy(result)
tampered["candidates"][0]["neighbors"].append({"model_spec": other.atomspec, "residue_index": 0,
                                               "spec": other_residue.atomspec, "label": "out of context"})
module.apply(session, tampered, context)
assert ligand.atoms.displays.all() and ala.atoms.filter(ala.atoms.element_numbers > 1).displays.all()
assert not gly.atoms.displays.any() and not remote.atoms.displays.any()
assert not ala.atoms.filter(ala.atoms.element_numbers == 1).displays.any()
assert np.array_equal(model.atoms.colors, colors)
assert np.array_equal(model.atoms.selected, selection)
assert np.array_equal(model.atoms.coords, coordinates)
assert other_state == (other.atoms.colors.tobytes(), other.atoms.displays.tobytes(), other.atoms.coords.tobytes())

# 40k atoms exercise bounded spatial queries without a dense pairwise array.
large = copy.deepcopy(snapshot)
count = 40000
coords = np.zeros((count, 3), dtype=float)
coords[:, 0] = (np.arange(count) // 2) * 5.0
coords[:, 1] = (np.arange(count) % 2) * 3.0
large["models"] = [{"spec": "#large", "name": "large fixture", "coords": coords,
                     "elements": np.full(count, 6, dtype=np.uint8), "residue_index": np.arange(count, dtype=np.int32),
                     "residues": tuple({"spec": "#large/%s:%d" % ("A" if i % 2 else "B", i),
                                        "name": "ALA", "chain": "A" if i % 2 else "B",
                                        "number": i, "polymer": "protein", "category": "main"} for i in range(count))}]
started = time.monotonic()
large_result = module.compute(large)
elapsed = time.monotonic() - started
assert large_result["counts"]["protein"] == count
assert large_result["interfaces"][0]["residue_count"] == count
assert large_result["interfaces"][0]["nearest_distance"] == 3.0
assert elapsed < 10, "Sparse 40k-atom analysis exceeded 10 s: %.3f" % elapsed
print("QUICK_ANALYZE_OK", json.dumps({"native_fixture": True, "distance_positive_negative_controls": True,
                                    "worker_plain_data": True, "cancellation": True,
                                    "cross_model_scene_coordinates": True, "candidate_cap": True,
                                    "apply_scoping_colors_selection_coordinates": True,
                                    "large_atoms": count, "large_seconds": round(elapsed, 3)}), flush=True)
