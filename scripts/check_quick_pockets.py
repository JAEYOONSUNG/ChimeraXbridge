"""Disposable-process checks for evidence, geometry, bounded memory and scope."""
from concurrent.futures import CancelledError, ThreadPoolExecutor
import importlib.util
from pathlib import Path
import sys
import threading
import time
import tracemalloc

import numpy as np

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('quick_pockets_check', root / 'src/quick_pockets.py')
qp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qp)


def model_snapshot(coords, ligand=False, spec='#1'):
    coords = np.asarray(coords, dtype=np.float64)
    residues = [dict(spec='%s/A:%d' % (spec, i + 1), name='ALA', chain='A', number=i + 1,
                     polymer='protein', category='main', selected=False, ss_type=0)
                for i in range(len(coords))]
    ri = np.arange(len(coords), dtype=np.int32)
    if ligand:
        rindex = len(residues)
        residues.append(dict(spec=spec + '/L:1', name='ATP', chain='L', number=1,
                             polymer='other', category='ligand', selected=False, ss_type=0))
        coords = np.concatenate((coords, [[0, 0, 0], [.5, 0, 0], [0, .5, 0], [0, 0, .5]]))
        ri = np.r_[ri, np.full(4, rindex, dtype=np.int32)]
    data = dict(spec=spec, name='synthetic fixture', coords=coords, elements=np.full(len(coords), 6, np.uint8),
                atom_names=tuple('C' for _ in coords), residue_index=ri,
                residues=tuple(residues), bfactors=np.full(len(coords), 20., np.float32),
                occupancies=np.ones(len(coords), np.float32), bonds=np.empty((0, 2), np.int32))
    for value in data.values():
        if isinstance(value, np.ndarray):
            value.flags.writeable = False
    return data


def snapshot(*models):
    return dict(models=list(models), target_label='fixture', selection_specs=(), signature='fixture')


def sphere_shell():
    # Dense carbon shell encloses a known void. A filled lattice is the
    # paired negative control, so absence is never tested in isolation.
    shell = []
    for theta in np.linspace(0, np.pi, 13):
        samples = max(1, int(25 * np.sin(theta)))
        for phi in np.linspace(0, 2 * np.pi, samples, endpoint=False):
            shell.append([5.2 * np.sin(theta) * np.cos(phi),
                          5.2 * np.sin(theta) * np.sin(phi), 5.2 * np.cos(theta)])
    return np.asarray(shell)


started = time.monotonic()
ligand_model = model_snapshot([[4, 0, 0], [-4, 0, 0], [0, 4, 0], [0, -4, 0], [0, 0, 4]], ligand=True)
# Worker executes with immutable arrays and no session or Qt. Reject command
# dispatch if it ever leaks into computation.
from chimerax.core import commands
old_run = commands.run
commands.run = lambda *a, **kw: (_ for _ in ()).throw(AssertionError('GUI command in compute'))
try:
    with ThreadPoolExecutor(max_workers=1) as worker:
        pocket = worker.submit(qp.compute, snapshot(ligand_model), 'pocket').result(timeout=20)
finally:
    commands.run = old_run
assert pocket['candidates'][0]['kind'] == 'observed_ligand'
assert pocket['candidates'][0]['contact_residues'] == 5
assert 'volume' not in pocket['candidates'][0] and 'max_depth' not in pocket['candidates'][0]
assert any('not established' in line for line in pocket['candidates'][0]['evidence'])

# A selected smaller ligand outranks an unselected well-contacted ligand;
# additives are excluded automatically but can be requested by selection.
import copy
rank_model = copy.deepcopy(ligand_model)
extra = dict(rank_model['residues'][-1], spec='#1/L:2', number=2, name='GOL', selected=True)
rank_model['residues'] += (extra,)
rank_model['coords'] = np.r_[rank_model['coords'], [[0, 0, 0], [.2, 0, 0], [0, .2, 0]]]
rank_model['elements'] = np.r_[rank_model['elements'], [6, 6, 6]]
rank_model['residue_index'] = np.r_[rank_model['residue_index'], [6, 6, 6]]
ranked = qp.compute(snapshot(rank_model), 'pocket')
assert ranked['candidates'][0]['ligand_specs'] == ['#1/L:2']
extra['selected'] = False
ranked = qp.compute(snapshot(rank_model), 'pocket')
assert all('GOL' not in c['label'] for c in ranked['candidates'])

# Missing KVFinder is an honest fallback, not an invented sphere measurement.
loader = qp._load_kvfinder
qp._load_kvfinder = lambda: (_ for _ in ()).throw(ImportError('fixture missing backend'))
try:
    fallback = qp.compute(snapshot(ligand_model), 'cavity')
    assert fallback['candidates'][0]['kind'] == 'observed_ligand'
    apo = qp.compute(snapshot(model_snapshot([[0, 0, 0], [3, 0, 0]])), 'cavity')
    assert apo['candidates'][0]['kind'] == 'neighborhood'
    assert 'volume' not in apo['candidates'][0]
    assert any('unavailable' in s.lower() for s in apo['details'])
finally:
    qp._load_kvfinder = loader

try:
    qp.compute(snapshot(ligand_model), 'pocket', cancelled=lambda: True)
    raise AssertionError('Cancellation did not stop computation')
except CancelledError:
    pass

phase_count = [0]
def cancel_after_phase(message):
    phase_count[0] += 1
try:
    qp.compute(snapshot(ligand_model), 'pocket', progress=cancel_after_phase,
               cancelled=lambda: phase_count[0] >= 1)
    raise AssertionError('Mid-phase cancellation did not stop computation')
except CancelledError:
    pass

# Large nearest-point queries would allocate 2.4 GB as a dense pairwise array.
# This test exercises the actual lining helper with a strict Python allocation bound.
rng = np.random.default_rng(17)
points = rng.uniform(-20, 20, (20_000, 3))
protein = rng.uniform(-25, 25, (15_000, 3))
tracemalloc.start()
spatial_start = time.monotonic()
near = qp._near_indices(points, protein, 3.5)
_, peak = tracemalloc.get_traced_memory()
tracemalloc.stop()
assert len(near) > 0 and peak < 12_000_000, peak
assert time.monotonic() - spatial_start < 5
probe = protein[:30]
expected = np.flatnonzero(np.sqrt(((probe[:, None] - points[None, :]) ** 2).sum(axis=2)).min(axis=1) <= 3.5)
actual = qp._near_indices(points, probe, 3.5)
assert np.array_equal(actual, expected)
assert qp._near_indices([[0, 0, 0]], np.asarray([[3.5, 0, 0], [3.50001, 0, 0]]), 3.5).tolist() == [0]

# Real installed KVFinder on known void and solid fixtures.
kv = qp._load_kvfinder()
shell = model_snapshot(sphere_shell())
phase_messages = []
cavity_start = time.monotonic()
with ThreadPoolExecutor(max_workers=1) as worker:
    job = worker.submit(qp.compute, snapshot(shell), 'cavity', phase_messages.append)
    heartbeat = []
    while not job.done():
        heartbeat.append(time.monotonic())
        time.sleep(.005)
    cavity = job.result(timeout=20)
geometry = [c for c in cavity['candidates'] if c['kind'] == 'geometry']
assert geometry, cavity
best = geometry[0]
assert 30 < best['volume'] < 4 / 3 * np.pi * 8 ** 3
assert np.linalg.norm(np.asarray(best['center'])) < 2
assert best['grid_cells'] <= qp.MAX_GRID_POINTS
assert best['mesh']['vertices'].shape[1] == 3 and len(best['mesh']['triangles']) > 20
assert np.isfinite(best['mesh']['normals']).all()
assert best['max_depth'] is None or best['max_depth'] >= 0
solid = np.stack(np.meshgrid(np.arange(-8, 9, 2), np.arange(-8, 9, 2),
                            np.arange(-8, 9, 2), indexing='ij'), axis=-1).reshape((-1, 3))
negative = qp.compute(snapshot(model_snapshot(solid)), 'cavity')
assert not any(c['kind'] == 'geometry' for c in negative['candidates']), negative
assert time.monotonic() - cavity_start < 30
assert any('background process' in m for m in phase_messages)

# Alternative ranking honors a selected lining region on a second cavity.
pair = model_snapshot(np.r_[sphere_shell(), sphere_shell() + [17, 0, 0]])
for residue in pair['residues'][len(shell['residues']):]:
    residue['selected'] = True
paired = qp.compute(snapshot(pair), 'cavity')
assert len([c for c in paired['candidates'] if c['kind'] == 'geometry']) >= 2
assert paired['candidates'][0]['center'][0] > 12
assert any('selected residues' in s for s in paired['candidates'][0]['evidence'])

# Biological fixture bundled with the dependency exercises a realistic grid
# and verifies Python can service a heartbeat while the C backend runs.
fixture = Path(kv.__file__).parent / 'data/tests/1FMO.pdb'
if fixture.exists():
    rows = [line for line in fixture.read_text().splitlines() if line.startswith('ATOM  ')]
    native = model_snapshot([[float(line[30:38]), float(line[38:46]), float(line[46:54])] for line in rows])
    elements = {'C': 6, 'N': 7, 'O': 8, 'S': 16, 'P': 15}
    native['elements'] = np.asarray([elements.get(line[76:78].strip(), 6) for line in rows], np.uint8)
    native['atom_names'] = tuple(line[12:16].strip() for line in rows)
    biological_start = time.monotonic()
    with ThreadPoolExecutor(max_workers=1) as worker:
        job = worker.submit(qp.compute, snapshot(native), 'cavity')
        ticks = [time.monotonic()]
        while not job.done():
            ticks.append(time.monotonic())
            time.sleep(.01)
        native_result = job.result(timeout=30)
    ticks.append(time.monotonic())
    max_gap = float(np.diff(ticks).max())
    assert any(c['kind'] == 'geometry' for c in native_result['candidates'])
    assert max_gap < .2, ('Cavity worker held Python execution too long', max_gap)
    assert time.monotonic() - biological_start < 30
    print('biological cavity fixture: %d atoms, %.2f s, max Python heartbeat gap %.3f s' %
          (len(rows), time.monotonic() - biological_start, max_gap))

# Cancellation terminates the launched process and removes its private data.
launched, stop = threading.Event(), threading.Event()
children = []
original_popen = qp.subprocess.Popen
def record_process(*args, **kwargs):
    child = original_popen(*args, **kwargs)
    children.append(child)
    launched.set()
    return child
qp.subprocess.Popen = record_process
try:
    with ThreadPoolExecutor(max_workers=1) as worker:
        job = worker.submit(qp.compute, snapshot(shell), 'cavity', None, stop.is_set)
        assert launched.wait(timeout=10), 'Background process did not start'
        cancel_time = time.monotonic()
        stop.set()
        try:
            job.result(timeout=3)
            raise AssertionError('Launched cavity worker did not cancel')
        except CancelledError:
            pass
        assert time.monotonic() - cancel_time < 2
    assert children and all(child.poll() is not None for child in children)
    assert all(not Path(child.args[-1]).parent.exists() for child in children)
finally:
    qp.subprocess.Popen = original_popen

# Adaptive grid must reject extreme spans, rather than allocating an enormous array.
enormous = model_snapshot(np.asarray([[0, 0, 0], [10000, 10000, 10000]]))
try:
    qp._bounded_grid(kv, qp._atomic_input(enormous, np.arange(2)))
    raise AssertionError('Huge grid was not bounded')
except ValueError:
    pass

# Real GUI-side model application, in a disposable no-GUI ChimeraX session.
from chimerax.atomic import AtomicStructure
from chimerax.geometry import translation


def live_model(data, name):
    model = AtomicStructure(session, name=name, auto_style=False, log_info=False)
    live_residues = [model.new_residue(r['name'], r['chain'], r['number']) for r in data['residues']]
    for i, xyz in enumerate(data['coords']):
        atom = model.new_atom('C', 'C')
        live_residues[int(data['residue_index'][i])].add_atom(atom)
        atom.coord = xyz
        atom.color = (140, 150, 170, 255)
        atom.display = False
    session.models.add([model])
    return model


live = live_model(ligand_model, 'pocket target')
unrelated = live_model(model_snapshot([[90, 0, 0], [92, 0, 0]], spec='#2'), 'unrelated')
unrelated.display = True
old_color = unrelated.atoms.colors.copy()
old_positions = unrelated.atoms.coords.copy()
old_display = unrelated.atoms.displays.copy()
selection_before = live.atoms.selecteds.copy()
target_colors = live.atoms.colors.copy()
# Exercise an existing nonopaque per-atom palette, including distinct colors.
target_colors[:, 0] = np.arange(len(target_colors)) * 13
target_colors[:, 3] = np.arange(len(target_colors)) * 7 + 120
live.atoms.colors = target_colors
context = dict(models=(live,), model_by_spec={'#1': live}, snapshot=snapshot(ligand_model), selection_specs=())
qp.apply(session, pocket, context)
assert np.array_equal(live.atoms.colors, target_colors), 'Pocket changed target palette/transparency'
assert np.array_equal(unrelated.atoms.colors, old_color)
assert np.array_equal(unrelated.atoms.coords, old_positions)
assert np.array_equal(unrelated.atoms.displays, old_display)
assert unrelated.display and np.array_equal(live.atoms.selecteds, selection_before)
overlays = [m for m in session.models.list() if getattr(m, '_codex_quick_overlay', False)]
assert overlays and all(m._codex_quick_target == '#1' for m in overlays)
session.models.close(overlays)

# Geometry preview is placed in world coordinates even for transformed targets.
shift = np.asarray([37., -19., 13.])
shell_live = live_model(shell, 'translated shell')
shell_live.position = translation(shift)
shell_colors = shell_live.atoms.colors.copy()
shell_colors[:, 3] = 173
shell_live.atoms.colors = shell_colors
world = dict(shell, coords=shell['coords'] + shift)
translated = qp.compute(snapshot(world), 'cavity')
context = dict(models=(shell_live,), model_by_spec={'#1': shell_live}, snapshot=snapshot(world), selection_specs=())
qp.apply(session, translated, context)
assert np.array_equal(shell_live.atoms.colors, shell_colors), 'Cavity changed target palette/transparency'
overlays = [m for m in session.models.list() if getattr(m, '_codex_quick_overlay', False)]
assert len(overlays) == 1
overlay = overlays[0]
assert np.linalg.norm(overlay.vertices.mean(axis=0) - shift) < 2
assert np.allclose(shell_live.position.origin(), shift)
assert np.allclose(shell_live.atoms.coords, shell['coords'])
assert np.array_equal(unrelated.atoms.colors, old_color) and unrelated.display
assert qp.apply(session, translated, {'model_by_spec': {}}) == 'The target model is no longer available.'

print('pockets evidence: ligand ranking/additive handling, missing backend, cancellation, known void/nonvoid, '
      'bounded grid, bounded nearest-point memory, isolated model apply and world-coordinate overlay passed')
print('pockets timing: %.2f s total; nearest-point peak %d bytes; cavity volume %.2f Å³' %
      (time.monotonic() - started, peak, best['volume']))
print('QUICK_POCKETS_OK')
