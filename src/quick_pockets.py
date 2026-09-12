"""Local pocket evidence and bounded cavity geometry for the quick toolbar.

Only ``apply`` touches ChimeraX. All coordinates entering/leaving ``compute``
are scene coordinates; returned arrays contain no live model references.
"""

import math
import os
from pathlib import Path
import pickle
import subprocess
import sys
import tempfile
import time
from concurrent.futures import CancelledError

import numpy as np
from scipy.spatial import cKDTree


MAX_GRID_POINTS = 1_500_000
MAX_CANDIDATES = 6
MAX_ATOMS = 150_000
THREADS = min(2, max(1, (os.cpu_count() or 2) - 1))
ACCENT = (71, 157, 163, 180)
_ADDITIVES = frozenset(('HOH', 'WAT', 'DOD', 'GOL', 'EDO', 'PEG', 'PG4',
                       'MPD', 'DMS', 'ACT', 'ACE', 'SO4', 'PO4', 'NO3', 'CL'))
# Conventional element van der Waals radii; snapshot intentionally carries
# element identity rather than mutable display radii. Unknown elements use 1.8 Å.
_RADII = {1: 1.2, 6: 1.7, 7: 1.55, 8: 1.52, 9: 1.47, 15: 1.8,
          16: 1.8, 17: 1.75, 35: 1.85, 53: 1.98}


def _phase(progress, cancelled, message):
    if cancelled and cancelled():
        raise CancelledError('Pocket calculation cancelled')
    if progress:
        progress(message)
    if cancelled and cancelled():
        raise CancelledError('Pocket calculation cancelled')


def _arrays(model):
    coords = np.asarray(model.get('coords', ()), dtype=np.float64).reshape((-1, 3))
    elements = np.asarray(model.get('elements', np.full(len(coords), 6)))
    ri = np.asarray(model.get('residue_index', np.zeros(len(coords))), dtype=np.int32)
    residues = model.get('residues', ())
    valid = np.isfinite(coords).all(axis=1) & (elements > 1) & (ri >= 0) & (ri < len(residues))
    main = np.asarray([r.get('polymer') in ('protein', 'nucleic') or
                       r.get('category') == 'main' for r in residues], dtype=bool)
    main_atoms = np.zeros(len(coords), dtype=bool)
    main_atoms[valid] = main[ri[valid]]
    return coords, elements, ri, residues, valid, np.flatnonzero(main_atoms)


def _specs(residues, indices):
    return [residues[int(i)]['spec'] for i in np.unique(indices)
            if residues[int(i)].get('spec')]


def _near_indices(points, coords, radius):
    """Nearest-point distance uses O(atoms + points), never their product."""
    if not len(points) or not len(coords):
        return np.empty(0, dtype=np.int32)
    tree = cKDTree(points)
    close = []
    for start in range(0, len(coords), 8192):
        distance, _ = tree.query(coords[start:start + 8192], k=1,
                                 distance_upper_bound=np.nextafter(float(radius), np.inf), workers=1)
        close.append(np.flatnonzero(distance <= radius) + start)
    return np.concatenate(close).astype(np.int32)


def _ligand_candidates(model, cancelled=None):
    coords, elements, ri, residues, valid, main = _arrays(model)
    if not len(main):
        return []
    grouped = {}
    for ai in np.flatnonzero(valid):
        residue = residues[int(ri[ai])]
        if residue.get('category') == 'ligand':
            grouped.setdefault(int(ri[ai]), []).append(int(ai))
    candidates = []
    for rindex, atom_indices in grouped.items():
        _phase(None, cancelled, '')
        residue = residues[rindex]
        selected = bool(residue.get('selected'))
        name = str(residue.get('name', '?'))
        if not selected and (name.upper() in _ADDITIVES or len(atom_indices) < 4):
            continue
        points = coords[atom_indices]
        # Query only atoms within 4.5 Å of observed ligand heavy atoms.
        local = _near_indices(points, coords[main], 4.5)
        lining = main[local]
        contact_res = np.unique(ri[lining])
        if len(contact_res) < 2:
            continue
        contacts = len(contact_res)
        label = '%s %s:%s' % (name, residue.get('chain', ''), residue.get('number', '?'))
        selected_contacts = sum(bool(residues[int(i)].get('selected')) for i in contact_res)
        candidates.append({
            'id': '%s:ligand:%s' % (model.get('spec', ''), rindex),
            'label': '%s · %s · %d contacts' % (model.get('spec', ''), label, contacts),
            'model_spec': model.get('spec', ''), 'kind': 'observed_ligand',
            'specs': _specs(residues, np.r_[contact_res, rindex]),
            'lining_specs': _specs(residues, contact_res),
            'lining_atom_indices': lining.tolist(), 'ligand_atom_indices': atom_indices,
            'ligand_specs': _specs(residues, [rindex]),
            'center': points.mean(axis=0).tolist(),
            'contact_residues': contacts, 'ligand_heavy_atoms': len(atom_indices),
            'rank_score': 10000 * selected + 100 * min(selected_contacts, 20) +
                          5 * min(contacts, 30) + min(len(atom_indices), 60),
            'evidence': [
                'Observed ligand %s: %d heavy atoms.' % (label, len(atom_indices)),
                '%d polymer residues have a heavy atom within 4.5 Å.' % contacts,
                'Neighborhood evidence; cavity volume/depth and biological binding role are not established.',
            ],
        })
    return candidates


def _load_kvfinder():
    import pyKVFinder
    return pyKVFinder


def _atomic_input(model, indices):
    coords, elements, ri, residues, _, _ = _arrays(model)
    names = model.get('atom_names', ())
    # Object dtype avoids conversion of every float to a wide unicode array.
    data = np.empty((len(indices), 8), dtype=object)
    for out, ai in enumerate(indices):
        residue = residues[int(ri[ai])]
        data[out, :4] = (str(residue.get('number', int(ri[ai]) + 1)),
                         residue.get('chain', '') or '_', residue.get('name', 'UNK'),
                         names[ai] if ai < len(names) else 'C')
    data[:, 4:7] = coords[indices]
    data[:, 7] = [_RADII.get(int(elements[i]), 1.8) for i in indices]
    return data


def _bounded_grid(kv, atomic):
    step = 0.8
    while True:
        # The stock helper pads atom *centers* only. Include atom radii so a
        # probe can roll around the outermost surface without touching a grid
        # boundary (otherwise flat solids can yield spurious exterior slabs).
        margin = 4.0 + float(np.max(atomic[:, 7].astype(float))) + step
        vertices = kv.get_vertices(atomic, probe_out=margin, step=step)
        shape = kv.grid._get_dimensions(vertices, step)
        cells = math.prod(shape)
        if cells <= MAX_GRID_POINTS:
            return step, vertices, shape
        step = round(step + 0.1, 1)
        if step > 1.6:
            raise ValueError('Whole-model cavity grid exceeds the quick limit even at 1.6 Å; '
                             'select a smaller model for geometry analysis')


def _voxel_mesh(mask, origin, step, cancelled=None):
    """Welded boundary of actual occupied grid cells; no artificial sphere."""
    mask = np.asarray(mask, dtype=bool)
    positions = np.argwhere(mask)
    if not len(positions):
        return None
    low = positions.min(axis=0)
    high = positions.max(axis=0) + 1
    mask = mask[tuple(slice(int(a), int(b)) for a, b in zip(low, high))]
    origin = np.asarray(origin) + low * step
    padded = np.pad(mask, 1)
    quads = []
    for axis in range(3):
        _phase(None, cancelled, '')
        u, v = (axis + 1) % 3, (axis + 2) % 3
        for direction in (-1, 1):
            slices = [slice(1, -1)] * 3
            slices[axis] = slice(0, -2) if direction == -1 else slice(2, None)
            face = np.argwhere(mask & ~padded[tuple(slices)]) * 2
            corners = np.zeros((4, 3), dtype=np.int32)
            corners[:, axis] = direction
            corners[:, u] = (-1, 1, 1, -1)
            corners[:, v] = (-1, -1, 1, 1)
            if direction == -1:
                corners = corners[::-1]
            quads.append(face[:, None, :] + corners[None, :, :])
    quads = np.concatenate(quads)
    points, inverse = np.unique(quads.reshape((-1, 3)), axis=0, return_inverse=True)
    quads = inverse.reshape((-1, 4))
    triangles = np.concatenate((quads[:, [0, 1, 2]], quads[:, [0, 2, 3]])).astype(np.int32)
    vertices = (points * (step / 2) + origin).astype(np.float32)
    # Mild display smoothing removes the staircase; reported geometry metrics
    # remain the unmodified KVFinder grid measurements.
    edges = np.concatenate((triangles[:, [0, 1]], triangles[:, [1, 2]], triangles[:, [2, 0]]))
    edges = np.concatenate((edges, edges[:, ::-1]))
    counts = np.bincount(edges[:, 0], minlength=len(vertices)).clip(1)
    for _ in range(2):
        neighbors = np.column_stack([np.bincount(edges[:, 0], weights=vertices[edges[:, 1], i],
                                                 minlength=len(vertices)) for i in range(3)])
        vertices = (0.65 * vertices + 0.35 * neighbors / counts[:, None]).astype(np.float32)
    normals = np.zeros_like(vertices)
    p = vertices[triangles]
    cross = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
    for i in range(3):
        np.add.at(normals, triangles[:, i], cross)
    normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12)
    return {'vertices': vertices, 'triangles': triangles, 'normals': normals}


def _geometry_candidates(model, kv, progress, cancelled):
    """Isolate the native backend: its C calls can hold the Python GIL.

    Threading alone would still freeze Qt for a large grid. The short-lived
    worker uses this installation's interpreter and dependency paths, and only
    receives a pickle created here inside a private temporary directory.
    """
    executable = Path(sys.executable)
    version = '%d.%d' % sys.version_info[:2]
    choices = [executable.parent / ('python' + version), executable.parent / 'python.exe',
               Path(sys.base_prefix) / 'bin' / ('python' + version),
               Path(sys.base_prefix) / 'python.exe']
    if executable.name.lower().startswith('python'):
        choices.insert(0, executable)
    python = next((p for p in choices if p.is_file() and os.access(p, os.X_OK)), None)
    if python is None:
        raise RuntimeError('A matching standalone Python is unavailable for the background cavity worker')
    _phase(progress, cancelled, 'Computing cavity geometry in a background process…')
    with tempfile.TemporaryDirectory(prefix='chimerax-quick-cavity-') as directory:
        directory = Path(directory)
        source, destination = directory / 'input.pkl', directory / 'output.pkl'
        with source.open('wb') as handle:
            pickle.dump(model, handle, protocol=pickle.HIGHEST_PROTOCOL)
        driver = directory / 'worker.py'
        # ChimeraX can load bundled NumPy before adding user packages to its
        # path. Preserve the already-loaded versions, not merely path order.
        dependency_roots = [str(Path(sys.modules[name].__file__).parent.parent)
                            for name in ('numpy', 'scipy')]
        worker_paths = list(dict.fromkeys(dependency_roots + list(sys.path)))
        driver.write_text('import sys, runpy\n'
                          'sys.path[:] = %r\n' % worker_paths +
                          'sys.argv = [%r, %r, %r]\n' % (str(__file__), str(source), str(destination)) +
                          'runpy.run_path(%r, run_name="__main__")\n' % str(__file__))
        # No shell and no application startup. -I avoids user site startup
        # hooks; dependency paths are then restored explicitly by our driver.
        with (directory / 'stderr.txt').open('w+b') as errors:
            process = subprocess.Popen([str(python), '-I', '-S', str(driver)],
                                       stdout=subprocess.DEVNULL, stderr=errors)
            try:
                started = time.monotonic()
                while process.poll() is None:
                    _phase(None, cancelled, '')
                    if time.monotonic() - started > 40:
                        raise RuntimeError('Cavity computation exceeded the 40 second quick limit')
                    try:
                        process.wait(timeout=.05)
                    except subprocess.TimeoutExpired:
                        pass
                _phase(progress, cancelled, 'Preparing cavity result…')
                if process.returncode or not destination.exists():
                    errors.seek(0)
                    message = errors.read()[-1200:].decode('utf-8', errors='replace').strip()
                    raise RuntimeError('Background cavity worker failed: ' + message)
                with destination.open('rb') as handle:
                    return pickle.load(handle)
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=1)


def _geometry_in_process(model, kv, progress, cancelled):
    coords, elements, ri, residues, _, main = _arrays(model)
    if len(main) < 8:
        return [], ['Too few polymer heavy atoms for cavity geometry.']
    if len(main) > MAX_ATOMS:
        raise ValueError('Model exceeds 150,000 polymer heavy atoms; select a smaller model for quick cavities')
    _phase(progress, cancelled, 'Preparing bounded cavity grid…')
    atomic = _atomic_input(model, main)
    step, vertices, shape = _bounded_grid(kv, atomic)
    _phase(progress, cancelled, 'Finding cavities · %.1f Å grid · %d threads…' % (step, THREADS))
    count, matrix = kv.detect(atomic, vertices, step=step, probe_in=1.4, probe_out=4.0,
                              removal_distance=2.4, volume_cutoff=30.0, nthreads=THREADS)
    note = ('%s: %.1f Å grid, %s cells; probes 1.4/4.0 Å; 30 Å³ minimum. '
            'Element van der Waals radii; waters, ligands and ions excluded.' %
            (model.get('spec', ''), step, format(math.prod(shape), ',')))
    _phase(progress, cancelled, 'Ranking cavity geometry…')
    if not count:
        return [], [note, 'No cavity met these geometry settings; this does not prove absence of binding sites.']
    # Characterize at most eight cavities, prioritizing the selected region
    # before volume. Grid-sized spatial/depth arrays remain bounded.
    labels, counts = np.unique(matrix[matrix >= 2], return_counts=True)
    selected_atoms = [i for i in main if residues[int(ri[i])].get('selected')]
    shortlist_score = counts.astype(np.float64)
    if selected_atoms and len(selected_atoms) < len(main):
        positions = np.argwhere(matrix >= 2)
        selected_tree = cKDTree(coords[selected_atoms])
        near_labels = set()
        for offset in range(0, len(positions), 8192):
            _phase(None, cancelled, '')
            block = positions[offset:offset + 8192]
            distance, _ = selected_tree.query(block * step + vertices[0],
                                              distance_upper_bound=3.5, workers=1)
            close = block[distance <= 3.5]
            near_labels.update(matrix[tuple(close.T)].tolist())
        shortlist_score += np.asarray([int(label in near_labels) for label in labels]) * MAX_GRID_POINTS
        del positions
    labels = labels[np.argsort(-shortlist_score)[:8]].tolist()
    surface, volumes, areas = kv.spatial(matrix, step=step, selection=labels, nthreads=THREADS)
    del surface
    _phase(progress, cancelled, 'Measuring cavity depth…')
    depths, maximum, average = kv.depth(matrix, step=step, selection=labels, nthreads=THREADS)
    del depths
    candidates = []
    for label in labels:
        _phase(progress, cancelled, 'Building cavity preview…')
        key = kv.grid._get_cavity_name(label - 2)
        mask = matrix == label
        voxels = np.argwhere(mask)
        xyz = voxels * step + vertices[0]
        close = _near_indices(xyz, coords[main], 3.5)
        lining = main[close]
        if not len(lining):
            continue
        contact_res = np.unique(ri[lining])
        volume = float(volumes[key])
        depth = float(maximum[key])
        depth = depth if math.isfinite(depth) and depth >= 0 else None
        selected_contacts = (sum(bool(residues[int(i)].get('selected')) for i in contact_res)
                             if len(selected_atoms) < len(main) else 0)
        evidence = ['Geometry prediction by KVFinder; no biological function assigned.',
                    'Grid volume %.0f Å³; %d lining residues within 3.5 Å.' % (volume, len(contact_res)),
                    'Display boundary is lightly smoothed; measurements use the original grid.']
        if selected_contacts:
            evidence.insert(1, '%d selected residues line this cavity.' % selected_contacts)
        candidate = {'id': '%s:cavity:%s' % (model.get('spec', ''), key),
                     'label': '%s · %s · %.0f Å³ · %d residues' %
                              (model.get('spec', ''), key, volume, len(contact_res)),
                     'model_spec': model.get('spec', ''), 'kind': 'geometry',
                     'specs': _specs(residues, contact_res),
                     'lining_specs': _specs(residues, contact_res),
                     'lining_atom_indices': lining.tolist(), 'ligand_atom_indices': [],
                     'center': xyz.mean(axis=0).tolist(), 'contact_residues': len(contact_res),
                     'volume': volume, 'area': float(areas[key]), 'max_depth': depth,
                     'avg_depth': float(average[key]), 'grid_step': step, 'grid_cells': math.prod(shape),
                     'rank_score': 1000 * min(selected_contacts, 20) +
                                   math.log1p(volume) + 0.4 * (depth or 0),
                     'mesh': _voxel_mesh(mask, vertices[0], step, cancelled), 'evidence': evidence}
        candidates.append(candidate)
    return candidates, [note, 'Candidates prioritize selected lining residues, then volume and depth; '
                        'ranking is not a druggability or affinity score.']


def _fallback(model):
    coords, _, ri, residues, _, main = _arrays(model)
    if not len(main):
        return []
    selected = np.asarray([i for i in main if residues[int(ri[i])].get('selected')], dtype=np.int32)
    if len(selected):
        center = coords[selected].mean(axis=0)
        reason = 'selected polymer region'
    else:
        center = coords[main].mean(axis=0)
        # A real residue near the centroid is an explicit navigation fallback,
        # not a fictitious cavity placed in empty centroid space.
        center = coords[main[np.argmin(np.linalg.norm(coords[main] - center, axis=1))]]
        reason = 'polymer residue near the model center'
    lining = main[np.linalg.norm(coords[main] - center, axis=1) <= 7.0]
    contact_res = np.unique(ri[lining])
    return [{'id': model.get('spec', '') + ':neighborhood',
             'label': 'Local neighborhood · cavity unavailable', 'model_spec': model.get('spec', ''),
             'kind': 'neighborhood', 'specs': _specs(residues, contact_res),
             'lining_specs': _specs(residues, contact_res), 'lining_atom_indices': lining.tolist(),
             'ligand_atom_indices': [], 'center': center.tolist(), 'contact_residues': len(contact_res),
             'rank_score': 0, 'evidence': ['Navigation fallback: %s, 7 Å neighborhood.' % reason,
                                        'This is not a detected cavity; volume and depth are unavailable.']}]


def compute(snapshot, action, progress=None, cancelled=None):
    if action not in ('pocket', 'cavity'):
        raise ValueError('Pocket worker supports pocket and cavity')
    _phase(progress, cancelled, 'Checking local ligand evidence…')
    models = snapshot.get('models', ())
    ligands = []
    for model in models:
        ligands.extend(_ligand_candidates(model, cancelled))
    candidates, details = [], []
    if action == 'pocket' and ligands:
        candidates = ligands
        details.append('Ranked by explicit selection, contacted selected residues, contact count and ligand size. '
                       'Common crystallization additives are skipped unless selected.')
    else:
        try:
            kv = _load_kvfinder()
        except (ImportError, OSError) as error:
            kv = None
            details.append('KVFinder unavailable (%s); no cavity geometry was calculated.' % error)
        if kv is not None:
            for model in models:
                try:
                    found, notes = _geometry_candidates(model, kv, progress, cancelled)
                    candidates.extend(found)
                    details.extend(notes)
                except (ValueError, RuntimeError, MemoryError) as error:
                    details.append('%s: cavity calculation unavailable: %s' % (model.get('spec', ''), error))
        if not candidates:
            candidates = ligands
            if ligands:
                details.append('Showing observed ligand contacts because no usable cavity geometry is available.')
            elif models:
                for model in models:
                    candidates.extend(_fallback(model))
    _phase(progress, cancelled, 'Finishing pocket result…')
    candidates.sort(key=lambda c: (-c['rank_score'], c['id']))
    candidates = candidates[:MAX_CANDIDATES]
    if not candidates:
        return {'title': 'Pocket' if action == 'pocket' else 'Cavity', 'action': action,
                'summary': ['No usable polymer site in the target.'], 'details': details,
                'metrics': [], 'candidates': []}
    best = candidates[0]
    if best['kind'] != 'geometry':
        details.append('The translucent preview encloses local lining atoms, not a detected void; '
                       'it is limited to 600 atoms within 16 Å for a fast display. Contact counts use the full site.')
    metrics = [{'label': 'Site', 'value': best['kind'].replace('_', ' ')},
               {'label': 'Lining residues', 'value': str(best['contact_residues'])},
               {'label': 'Candidates', 'value': str(len(candidates))}]
    if best['kind'] == 'geometry':
        metrics += [{'label': 'Volume', 'value': '%.0f Å³' % best['volume']},
                    {'label': 'Grid', 'value': '%.1f Å' % best['grid_step']}]
        if best.get('max_depth') is not None:
            metrics.append({'label': 'Max depth', 'value': '%.1f Å' % best['max_depth']})
    return {'title': 'Pocket' if action == 'pocket' else 'Cavity', 'action': action,
            'summary': [best['label'], best['evidence'][0]], 'details': details,
            'metrics': metrics, 'candidates': candidates}


def apply(session, result, context, candidate=0):
    candidates = result.get('candidates', ())
    if not candidates:
        return 'No site to display.'
    selected = candidates[max(0, min(int(candidate), len(candidates) - 1))]
    model = context.get('model_by_spec', {}).get(selected['model_spec'])
    if model is None or getattr(model, 'deleted', False):
        return 'The target model is no longer available.'
    model.display = True
    # Only the chosen target's lining/ligand atoms are changed. Native colors
    # elsewhere and all other models' display states remain untouched.
    atoms = model.atoms
    indices = np.unique(selected.get('lining_atom_indices', []) + selected.get('ligand_atom_indices', []))
    indices = indices[(indices >= 0) & (indices < len(atoms))].astype(np.int32)
    shown = atoms[indices]
    if len(shown):
        shown.displays = True
        shown.draw_modes = shown.STICK_STYLE
    mesh = selected.get('mesh')
    if mesh is not None:
        from chimerax.core.models import Surface
        overlay = Surface('Cavity · ' + selected['label'], session)
        overlay._codex_quick_overlay = True
        overlay._codex_quick_target = selected['model_spec']
        overlay.set_geometry(mesh['vertices'], mesh['normals'], mesh['triangles'])
        overlay.color = ACCENT
        session.models.add([overlay])  # mesh already has world coordinates
    elif len(shown):
        # Exact local atom envelope, restricted to a small contact shell. It is
        # explicitly a lining surface, never presented as cavity geometry.
        from chimerax.atomic import Atoms
        from chimerax.core.colors import Color
        from chimerax.surface import surface
        lining = selected.get('lining_atom_indices', [])
        local_atoms = Atoms([atoms[int(i)] for i in lining if 0 <= int(i) < len(atoms)])
        if len(local_atoms):
            # Bound both atom count and spatial extent: long ligands can have
            # widely separated contacts and otherwise force a huge GUI grid.
            center = np.asarray(selected['center'])
            points = local_atoms.scene_coords
            distance = np.linalg.norm(points - center, axis=1)
            if distance.min() > 12:
                center = points[distance.argmin()]
                distance = np.linalg.norm(points - center, axis=1)
            nearest = np.argsort(distance)
            local_atoms = local_atoms[nearest[distance[nearest] <= 16][:600]]
            # The native surface API uses the parser's optional .spec metadata
            # for naming when enclose is supplied directly.
            local_atoms.spec = 'pocket lining'
            surfaces = surface(session, enclose=local_atoms, grid_spacing=0.8,
                               color=Color(tuple(c / 255 for c in ACCENT)),
                               transparency=65, nthread=THREADS, replace=False, update=False)
            for overlay in surfaces:
                overlay._codex_quick_overlay = True
                overlay._codex_quick_target = selected['model_spec']
                overlay.name = 'Pocket lining · ' + selected['label']
    if len(shown):
        from chimerax.core.objects import Objects
        from chimerax.std_commands.view import view
        view(session, Objects(atoms=shown), clip=False)
    return selected['label']


if __name__ == '__main__':
    # Both paths are passed by our private worker launcher; never load a user
    # document or downloaded file through this internal pickle protocol.
    with open(sys.argv[1], 'rb') as handle:
        _worker_model = pickle.load(handle)
    _worker_result = _geometry_in_process(_worker_model, _load_kvfinder(), None, None)
    with open(sys.argv[2], 'wb') as handle:
        pickle.dump(_worker_result, handle, protocol=pickle.HIGHEST_PROTOCOL)
