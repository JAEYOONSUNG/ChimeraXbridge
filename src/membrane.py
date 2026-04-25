import io
from pathlib import Path
import re
import sys
import tempfile
import webbrowser

OPM_PPM_URL = "https://opm.phar.umich.edu/ppm_server"
OPM_DATABASE_URL = "https://opm.phar.umich.edu"
CHARMM_GUI_MEMBRANE_URL = "https://www.charmm-gui.org/?doc=input/membrane"
MEMGEN_URL = "https://memgen.uni-saarland.de/#/memgen"

HYDROPHOBIC_AA = set("AILMFWVYC")
KD_SCALE = {
    "A": 1.8,
    "C": 2.5,
    "D": -3.5,
    "E": -3.5,
    "F": 2.8,
    "G": -0.4,
    "H": -3.2,
    "I": 4.5,
    "K": -3.9,
    "L": 3.8,
    "M": 1.9,
    "N": -3.5,
    "P": -1.6,
    "Q": -3.5,
    "R": -4.5,
    "S": -0.8,
    "T": -0.7,
    "V": 4.2,
    "W": -0.9,
    "Y": -1.3,
}


def _np():
    import numpy as np

    return np


def _resolve_model_spec(session, model_hint):
    try:
        from .semantic import resolve_model_spec

        return resolve_model_spec(session, model_hint)
    except Exception:
        return _spec_from_raw_hint(model_hint)


def _resolve_default_model_spec(session):
    try:
        from .semantic import resolve_default_model_spec

        return resolve_default_model_spec(session)
    except Exception:
        return None


def format_membrane_report(session, model_hint=None):
    target = _resolve_target_structure(session, model_hint)
    if target is None:
        if model_hint:
            return f"- No atomic model matched for membrane analysis: {model_hint}"
        return "- No atomic model is open for membrane analysis."

    model, spec = target
    bounds = _model_bounds(model)
    segments = _tm_like_segments(model, spec)
    session._codex_bridge_last_membrane_segments = segments[:16]

    lines = ["Membrane workflow"]
    lines.append(f"- target: {spec} {getattr(model, 'name', 'structure')}")
    if bounds is not None:
        center, size = bounds
        lines.append(
            f"- bbox: center {center[0]:.1f},{center[1]:.1f},{center[2]:.1f}; "
            f"size {size[0]:.1f} x {size[1]:.1f} x {size[2]:.1f} A"
        )

    if segments:
        lines.append(f"- TM-like hydrophobic segments: {len(segments)}")
        for segment in segments[:10]:
            lines.append(
                f"  - {segment['chain_id']}:{segment['start']}-{segment['end']} "
                f"{segment['sse']} score {segment['score']:.2f}; "
                f"hydrophobic {segment['hydrophobic_fraction']:.2f}; "
                f"KD {segment['kd_average']:.2f}"
            )
    else:
        lines.append("- TM-like hydrophobic segments: none above the local heuristic cutoff")

    lines.append("- in-app view: /membrane view")
    lines.append("- hydrophobic surface: /membrane mlp")
    lines.append("- external builders: /membrane web | /membrane opm | /membrane charmm | /membrane memgen")
    lines.append("- clear virtual slab: /membrane clear")
    return "\n".join(lines)


def run_membrane_view(session, model_hint=None, *, executor=None, apply_mlp=True):
    target = _resolve_target_structure(session, model_hint)
    if target is None:
        if model_hint:
            return f"No atomic model matched for membrane view: {model_hint}"
        return "No atomic model is open for membrane view."

    model, spec = target
    bounds = _model_bounds(model)
    if bounds is None:
        return f"No coordinates found for membrane view: {spec}"

    _close_virtual_membrane(session, executor=executor)
    center, size = bounds
    slab = _membrane_geometry(model, center, size)
    models = _open_membrane_bild(session, _membrane_bild(slab), "AI virtual membrane", executor=executor)
    session._codex_bridge_virtual_membrane_models = list(models)

    commands = [
        f"cartoon {spec}",
        f"surface {spec}",
        f"transparency {spec} 45 target s",
    ]
    executed = []
    for command in commands:
        try:
            _run_chimerax(session, command, executor=executor)
            executed.append(command)
        except Exception:
            pass

    mlp_line = "MLP skipped"
    if apply_mlp:
        try:
            _run_chimerax(session, f"mlp {spec}", executor=executor)
            mlp_line = f"MLP hydrophobicity surface requested for {spec}"
        except Exception as err:
            mlp_line = f"MLP failed: {err}"

    segments = _tm_like_segments(model, spec)
    session._codex_bridge_last_membrane_segments = segments[:16]
    lines = [
        "Virtual membrane view created.",
        f"- target: {spec} {getattr(model, 'name', 'structure')}",
        f"- slab: {slab['width']:.1f} x {slab['height']:.1f} A, core thickness {slab['thickness']:.1f} A",
        f"- membrane center: {slab['center'][0]:.1f},{slab['center'][1]:.1f},{slab['center'][2]:.1f}",
        f"- orientation: XY slab around current coordinates; use OPM/PPM for authoritative orientation",
        f"- {mlp_line}",
    ]
    if segments:
        top = segments[0]
        lines.append(
            f"- strongest TM-like segment: {top['chain_id']}:{top['start']}-{top['end']} "
            f"{top['sse']} score {top['score']:.2f}"
        )
    if executed:
        lines.append("- view commands: " + " | ".join(executed))
    return "\n".join(lines)


def apply_membrane_mlp(session, model_hint=None, *, executor=None):
    target = _resolve_target_structure(session, model_hint)
    if target is None:
        return "No atomic model is open for MLP membrane analysis."
    _model, spec = target
    try:
        _run_chimerax(session, f"mlp {spec}", executor=executor)
    except Exception as err:
        return f"MLP failed for {spec}: {err}"
    return f"MLP hydrophobicity analysis requested for {spec}."


def clear_virtual_membrane(session, *, executor=None):
    count = _close_virtual_membrane(session, executor=executor)
    return f"Cleared {count} virtual membrane model(s)." if count else "No virtual membrane model was open."


def launch_membrane_builder_sites(session, tool="all", model_hint=None, *, executor=None):
    target = _resolve_target_structure(session, model_hint)
    if target is None:
        return "No atomic model is open for membrane-builder export."

    model, spec = target
    export_path = _export_structure(session, model, spec, executor=executor)
    _copy_text_to_clipboard(str(export_path))

    tool_key = str(tool or "all").strip().lower()
    choices = {
        "opm": [("OPM/PPM membrane orientation", OPM_PPM_URL)],
        "ppm": [("OPM/PPM membrane orientation", OPM_PPM_URL)],
        "database": [("OPM database", OPM_DATABASE_URL)],
        "charmm": [("CHARMM-GUI Membrane Builder", CHARMM_GUI_MEMBRANE_URL)],
        "charmmgui": [("CHARMM-GUI Membrane Builder", CHARMM_GUI_MEMBRANE_URL)],
        "char": [("CHARMM-GUI Membrane Builder", CHARMM_GUI_MEMBRANE_URL)],
        "memgen": [("MemGen", MEMGEN_URL)],
        "web": [
            ("OPM/PPM membrane orientation", OPM_PPM_URL),
            ("CHARMM-GUI Membrane Builder", CHARMM_GUI_MEMBRANE_URL),
            ("MemGen", MEMGEN_URL),
        ],
        "all": [
            ("OPM/PPM membrane orientation", OPM_PPM_URL),
            ("CHARMM-GUI Membrane Builder", CHARMM_GUI_MEMBRANE_URL),
            ("MemGen", MEMGEN_URL),
        ],
    }
    sites = choices.get(tool_key, choices["all"])
    for _label, url in sites:
        webbrowser.open_new_tab(url)

    labels = ", ".join(label for label, _url in sites)
    return (
        f"Opened {labels}. Exported current structure for upload and copied its path:\n"
        f"- {export_path}"
    )


def _resolve_target_structure(session, model_hint=None):
    from chimerax.atomic import AtomicStructure

    selected_spec = _resolve_model_spec(session, model_hint) if model_hint else None
    if selected_spec is None and model_hint:
        selected_spec = _spec_from_raw_hint(model_hint)
    if selected_spec is None:
        selected_spec = _selected_atomic_model_spec(session)
    if selected_spec is None:
        selected_spec = _resolve_default_model_spec(session)

    first = None
    for model in session.models.list(type=AtomicStructure):
        spec = f"#{getattr(model, 'id_string', '?')}"
        if first is None:
            first = (model, spec)
        if selected_spec and spec == selected_spec:
            return model, spec
    return first


def _selected_atomic_model_spec(session):
    try:
        models = list(session.selection.models())
    except Exception:
        models = []
    for model in models:
        spec = f"#{getattr(model, 'id_string', '?')}"
        if spec != "#?":
            return spec
    return None


def _spec_from_raw_hint(text):
    match = re.search(r"#\d+(?:\.\d+)*", str(text or ""))
    return match.group(0) if match else None


def _model_bounds(model):
    np = _np()
    atoms = getattr(model, "atoms", None)
    if atoms is None or len(atoms) == 0:
        return None
    try:
        heavy = atoms.filter(atoms.element_names != "H")
        if len(heavy) > 0:
            atoms = heavy
    except Exception:
        pass
    coords = np.asarray(atoms.scene_coords, dtype=float)
    if coords.size == 0:
        return None
    mins = coords.min(axis=0)
    maxs = coords.max(axis=0)
    center = (mins + maxs) / 2.0
    size = maxs - mins
    return center, size


def _membrane_geometry(model, center, size):
    np = _np()
    segments = _tm_like_segments(model, f"#{getattr(model, 'id_string', '?')}")
    z_values = [segment.get("center_z") for segment in segments if segment.get("center_z") is not None]
    z_center = float(np.median(z_values)) if z_values else float(center[2])
    width = _clamp(float(max(size[0], size[1]) + 32.0), 46.0, 220.0)
    height = width
    thickness = 30.0
    if segments:
        z_span = max((segment.get("z_span") or 0.0) for segment in segments[:8])
        if z_span > 20.0:
            thickness = _clamp(z_span + 8.0, 28.0, 42.0)
    return {
        "center": (float(center[0]), float(center[1]), z_center),
        "width": width,
        "height": height,
        "thickness": thickness,
    }


def _membrane_bild(slab):
    cx, cy, cz = slab["center"]
    half_x = slab["width"] / 2.0
    half_y = slab["height"] / 2.0
    half_t = slab["thickness"] / 2.0
    boundary = 0.8
    x0, x1 = cx - half_x, cx + half_x
    y0, y1 = cy - half_y, cy + half_y
    z0, z1 = cz - half_t, cz + half_t
    lines = [
        ".comment AI virtual graphite membrane generated by Codex Bridge",
        ".color 0.07 0.08 0.09",
        ".transparency 0.72",
        f".box {x0:.3f} {y0:.3f} {z0:.3f} {x1:.3f} {y1:.3f} {z1:.3f}",
        ".color 0.34 0.36 0.39",
        ".transparency 0.42",
        f".box {x0:.3f} {y0:.3f} {z1 - boundary:.3f} {x1:.3f} {y1:.3f} {z1 + boundary:.3f}",
        f".box {x0:.3f} {y0:.3f} {z0 - boundary:.3f} {x1:.3f} {y1:.3f} {z0 + boundary:.3f}",
        ".color 0.56 0.59 0.63",
        ".transparency 0.18",
    ]
    tick_count = 7
    tick_radius = max(slab["width"], slab["height"]) / 330.0
    for i in range(tick_count):
        frac = (i + 0.5) / tick_count
        x = x0 + frac * (x1 - x0)
        lines.append(f".cylinder {x:.3f} {y0:.3f} {z1:.3f} {x:.3f} {y1:.3f} {z1:.3f} {tick_radius:.3f} open")
        lines.append(f".cylinder {x:.3f} {y0:.3f} {z0:.3f} {x:.3f} {y1:.3f} {z0:.3f} {tick_radius:.3f} open")
    return "\n".join(lines) + "\n"


def _open_membrane_bild(session, bild_text, name, *, executor=None):
    if executor is not None:
        model_spec = _next_membrane_model_spec(session)
        path = _write_temp_bild(bild_text)
        from chimerax.core.commands import StringArg

        _run_chimerax(session, f"open {StringArg.unparse(str(path))} id {model_spec}", executor=executor)
        session._codex_bridge_virtual_membrane_specs = [model_spec]
        return []

    from chimerax.bild.bild import read_bild

    models, _status = read_bild(session, io.BytesIO(bild_text.encode("utf-8")), name)
    for model in models:
        try:
            model.name = name
        except Exception:
            pass
    session.models.add(models)
    session._codex_bridge_virtual_membrane_specs = [
        f"#{getattr(model, 'id_string', '?')}" for model in models if getattr(model, "id_string", None)
    ]
    return models


def _close_virtual_membrane(session, *, executor=None):
    models = list(getattr(session, "_codex_bridge_virtual_membrane_models", []) or [])
    specs = list(getattr(session, "_codex_bridge_virtual_membrane_specs", []) or [])
    closed = 0
    for model in models:
        try:
            model.delete()
            closed += 1
        except Exception:
            try:
                session.models.remove([model])
                closed += 1
            except Exception:
                pass
    for spec in specs:
        try:
            _run_chimerax(session, f"close {spec}", executor=executor)
            closed += 1
        except Exception:
            pass
    session._codex_bridge_virtual_membrane_models = []
    session._codex_bridge_virtual_membrane_specs = []
    return closed


def _next_membrane_model_spec(session):
    used = set()
    try:
        used = {str(getattr(model, "id_string", "")) for model in session.models.list()}
    except Exception:
        used = set()
    for model_id in range(9009, 9050):
        if str(model_id) not in used:
            return f"#{model_id}"
    return "#9050"


def _write_temp_bild(bild_text):
    output_dir = Path(tempfile.gettempdir()) / "chimerax_codex_bridge"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "ai_virtual_membrane.bild"
    path.write_text(bild_text, encoding="utf-8")
    return path


def _tm_like_segments(model, spec):
    from chimerax.atomic import Residue

    segments = []
    for chain in getattr(model, "chains", []):
        polymer_type = getattr(chain, "polymer_type", None)
        if polymer_type not in (Residue.PT_AMINO, Residue.PT_PROTEIN):
            continue
        sequence = str(getattr(chain, "characters", "") or "").upper()
        try:
            residues = list(getattr(chain, "existing_residues", []))
        except Exception:
            residues = []
        if len(sequence) < 12 or not residues:
            continue
        usable = min(len(sequence), len(residues))
        sequence = sequence[:usable]
        residues = residues[:usable]
        chain_id = str(getattr(chain, "chain_id", "")).strip() or "?"
        windows = _hydrophobic_windows(sequence, residues)
        for start_idx, end_idx, score, kd_average, hydrophobic_fraction in _merge_windows(windows):
            residue_slice = residues[start_idx:end_idx + 1]
            numbers = [int(getattr(residue, "number", 0)) for residue in residue_slice]
            if not numbers:
                continue
            sse = _segment_sse_label(residue_slice)
            coords = _residue_coords(residue_slice)
            center_z = None
            z_span = 0.0
            if coords is not None and len(coords) > 0:
                center_z = float(np.median(coords[:, 2]))
                z_span = float(coords[:, 2].max() - coords[:, 2].min())
            segments.append(
                {
                    "model_spec": spec,
                    "chain_id": chain_id,
                    "start": min(numbers),
                    "end": max(numbers),
                    "length": len(residue_slice),
                    "sse": sse,
                    "score": score,
                    "kd_average": kd_average,
                    "hydrophobic_fraction": hydrophobic_fraction,
                    "center_z": center_z,
                    "z_span": z_span,
                    "residue_spec": f"{spec}/{chain_id}:{min(numbers)}-{max(numbers)}",
                }
            )
    segments.sort(key=lambda item: (-item["score"], -item["length"], item["chain_id"], item["start"]))
    return segments


def _hydrophobic_windows(sequence, residues, window=19):
    if len(sequence) < window:
        window = max(12, len(sequence))
    if len(sequence) < window:
        return []
    windows = []
    for start in range(0, len(sequence) - window + 1):
        end = start + window - 1
        chunk = sequence[start:end + 1]
        kd_average = sum(KD_SCALE.get(aa, 0.0) for aa in chunk) / len(chunk)
        hydrophobic_fraction = sum(1 for aa in chunk if aa in HYDROPHOBIC_AA) / len(chunk)
        sse_bonus = _window_sse_bonus(residues[start:end + 1])
        score = kd_average + hydrophobic_fraction + sse_bonus
        if kd_average >= 1.25 and hydrophobic_fraction >= 0.48:
            windows.append((start, end, score, kd_average, hydrophobic_fraction))
    return windows


def _merge_windows(windows):
    np = _np()
    if not windows:
        return []
    merged = []
    current = list(windows[0])
    scores = [windows[0][2]]
    kds = [windows[0][3]]
    hydros = [windows[0][4]]
    for window in windows[1:]:
        start, end, score, kd_average, hydrophobic_fraction = window
        if start <= current[1] + 3:
            current[1] = max(current[1], end)
            scores.append(score)
            kds.append(kd_average)
            hydros.append(hydrophobic_fraction)
            continue
        merged.append((current[0], current[1], max(scores), float(np.mean(kds)), float(np.mean(hydros))))
        current = [start, end, score, kd_average, hydrophobic_fraction]
        scores = [score]
        kds = [kd_average]
        hydros = [hydrophobic_fraction]
    merged.append((current[0], current[1], max(scores), float(np.mean(kds)), float(np.mean(hydros))))
    return merged


def _window_sse_bonus(residues):
    if not residues:
        return 0.0
    helix = sum(1 for residue in residues if bool(getattr(residue, "is_helix", False)))
    strand = sum(1 for residue in residues if bool(getattr(residue, "is_strand", False)))
    fraction = max(helix, strand) / len(residues)
    return 0.45 if fraction >= 0.65 else 0.20 if fraction >= 0.40 else 0.0


def _segment_sse_label(residues):
    if not residues:
        return "loop-like"
    helix = sum(1 for residue in residues if bool(getattr(residue, "is_helix", False)))
    strand = sum(1 for residue in residues if bool(getattr(residue, "is_strand", False)))
    if helix >= max(4, strand * 2):
        return "helix-supported"
    if strand >= max(4, helix * 2):
        return "strand-supported"
    if helix or strand:
        return "mixed-SSE"
    return "hydrophobic"


def _residue_coords(residues):
    np = _np()
    coords = []
    for residue in residues:
        try:
            atom = getattr(residue, "principal_atom", None)
            if atom is not None:
                coords.append(np.asarray(atom.scene_coord, dtype=float))
                continue
        except Exception:
            pass
        try:
            atom_coords = np.asarray(residue.atoms.scene_coords, dtype=float)
            if len(atom_coords) > 0:
                coords.append(atom_coords.mean(axis=0))
        except Exception:
            pass
    if not coords:
        return None
    return np.asarray(coords, dtype=float)


def _export_structure(session, model, spec, *, executor=None):
    from chimerax.core.commands import StringArg

    output_dir = Path(tempfile.gettempdir()) / "chimerax_codex_bridge"
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = _safe_stem(f"{getattr(model, 'name', 'structure')}_{spec.strip('#')}")
    path = output_dir / f"{stem}_membrane_input.cif"
    command = f"save {StringArg.unparse(str(path))} format mmcif models {spec}"
    _run_chimerax(session, command, executor=executor)
    return path


def _run_chimerax(session, command, executor=None):
    if executor is not None:
        return executor(command)
    from chimerax.core.commands import run

    return run(session, command)


def _copy_text_to_clipboard(text):
    if sys.platform != "darwin" or not text:
        return
    try:
        import subprocess

        subprocess.run(["pbcopy"], input=str(text).encode("utf-8"), check=False)
    except Exception:
        pass


def _safe_stem(text):
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(text or "")).strip("._")
    return stem or "structure"


def _clamp(value, low, high):
    return max(low, min(high, value))
