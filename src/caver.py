import os
import csv
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

from chimerax.core.tools import ToolInstance, get_singleton


CAVER_WEB_URL = "https://loschmidt.chemi.muni.cz/caverweb/"
DEFAULT_MAX_TUNNELS = 40
_CAVER_JAR_CACHE = None
CAVER_TUNNEL_COLORS = (
    "#f0c419",
    "#00bcd4",
    "#ff5ea8",
    "#7bd88f",
    "#ff9f43",
    "#9b8cff",
    "#4dd6b8",
    "#ff6f61",
)


def run_caver_action(session, arg="", executor=None):
    """`/caver [panel|prepare|run|web|import <path>|lining|status] [max_tunnels=N] [dist=D]`"""
    raw = str(arg or "").strip()
    max_tunnels = None
    lining_dist = None
    tokens = []
    for tok in raw.split():
        lower = tok.lower()
        if lower.startswith("max_tunnels=") or lower.startswith("max="):
            try: max_tunnels = max(1, min(50, int(lower.split("=", 1)[1])))
            except: pass
        elif lower.startswith("dist=") or lower.startswith("distance="):
            try: lining_dist = max(1.0, min(15.0, float(lower.split("=", 1)[1])))
            except: pass
        else:
            tokens.append(tok)
    cleaned = " ".join(tokens)
    action, rest = _split_action_arg(cleaned)
    if action in {"", "panel", "tool", "gui", "open"}:
        tool = CodexCaverTool.get_singleton(session)
        if tool is not None:
            tool.display(True)
            tool.refresh()
        return "Opened CAVER panel."
    if action in {"web", "server", "online"}:
        from .toolbar_actions import launch_caver_server

        return launch_caver_server(session, executor=executor, model_hint=rest or None)
    if action in {"prepare", "export", "job"}:
        job = prepare_caver_job(session, executor=executor)
        return format_caver_job(job)
    if action in {"auto", "autostart", "auto-start", "cavity", "seed"}:
        return set_caver_start_from_cavity(session, executor=executor)
    if action in {"selection", "manual", "clear-start", "clear_start"}:
        return clear_caver_start_override(session)
    if action in {"run", "local"}:
        return run_caver_local(session, executor=executor)
    if action in {"import", "load"}:
        if not rest:
            return "Usage: /caver import /path/to/caver_results.zip-or-folder [max_tunnels=N]"
        if max_tunnels is not None:
            return import_caver_results(session, rest, executor=executor, max_tunnels=max_tunnels)
        return import_caver_results(session, rest, executor=executor)
    if action in {"lining", "neighbors", "neighbours", "select"}:
        if lining_dist is not None:
            return select_caver_lining_residues(session, rest, executor=executor, distance=lining_dist)
        return select_caver_lining_residues(session, rest, executor=executor)
    if action in {"status", "help"}:
        return caver_status(session)
    return "Usage: /caver [panel|prepare|run|web|import <path>|lining [tunnel-number] [dist=D]] [max_tunnels=N]"


def caver_status(session):
    start = get_caver_start_point(session)
    home, jar = _resolve_caver_home_and_jar()
    java = shutil.which(os.environ.get("CAVER_JAVA", "java"))
    imported = getattr(session, "_codex_bridge_caver_tunnels", []) or []
    if home and jar and java:
        local_line = f"- local run: ready (Java: {java})"
    else:
        missing = []
        if not java:
            missing.append("Java")
        if not home or not jar:
            missing.append("CAVER_HOME/CAVER_JAR")
        local_line = "- local run: missing " + ", ".join(missing)
    lines = [
        "CAVER integration",
        f"- start point: {start['label']} ({start['coords'][0]:.2f}, {start['coords'][1]:.2f}, {start['coords'][2]:.2f})"
        if start
        else "- start point: no model/selection resolved",
        f"- CAVER_HOME: {home or '(not configured)'}",
        f"- caver.jar: {jar or '(not configured)'}",
        f"- imported tunnels: {len(imported)}",
        local_line,
    ]
    return "\n".join(lines)


def get_caver_start_point(session):
    try:
        import numpy as np
        from chimerax.atomic import AtomicStructure, selected_atoms, selected_residues
    except Exception:
        return None

    override = getattr(session, "_codex_bridge_caver_start", None)
    if isinstance(override, dict):
        try:
            coords = tuple(float(v) for v in override.get("coords", ()))
        except Exception:
            coords = ()
        if len(coords) == 3:
            return {
                "coords": coords,
                "label": str(override.get("label") or "auto cavity start"),
                "source": "auto",
            }

    try:
        atoms = selected_atoms(session)
        if len(atoms):
            coords = np.asarray(atoms.scene_coords, dtype=float).mean(axis=0)
            return {"coords": tuple(float(v) for v in coords), "label": f"selected atoms ({len(atoms)})", "source": "selection"}
    except Exception:
        pass

    try:
        residues = selected_residues(session)
        residue_coords = []
        residue_count = 0
        for _structure, _chain_id, group in residues.by_chain:
            for residue in list(group):
                residue_count += 1
                try:
                    residue_coords.append(np.asarray(residue.atoms.scene_coords, dtype=float))
                except Exception:
                    pass
        if residue_coords:
            coords = np.concatenate(residue_coords, axis=0).mean(axis=0)
            return {"coords": tuple(float(v) for v in coords), "label": f"selected residues ({residue_count})", "source": "selection"}
    except Exception:
        pass

    try:
        for model in session.models.list(type=AtomicStructure):
            atoms = model.atoms
            if not len(atoms):
                continue
            coords = np.asarray(atoms.scene_coords, dtype=float).mean(axis=0)
            return {
                "coords": tuple(float(v) for v in coords),
                "label": f"model center #{getattr(model, 'id_string', '?')}",
                "source": "model center",
            }
    except Exception:
        pass
    return None


def clear_caver_start_override(session):
    had = hasattr(session, "_codex_bridge_caver_start")
    try:
        delattr(session, "_codex_bridge_caver_start")
    except Exception:
        pass
    return "CAVER start reset to current selection/model center." if had else "CAVER start was already using current selection/model center."


def set_caver_start_from_cavity(session, *, model_hint=None, executor=None, top_n=1):
    return _run_on_ui_thread(
        session,
        lambda: _set_caver_start_from_cavity_on_ui(session, model_hint=model_hint, executor=executor, top_n=top_n),
    )


def _set_caver_start_from_cavity_on_ui(session, *, model_hint=None, executor=None, top_n=1):
    try:
        import numpy as np
        from .semantic import find_kvfinder_pockets
    except Exception as err:
        return f"Auto CAVER start failed: KVFinder/cavity tools unavailable ({err})."

    _cleanup_caver_start_visuals(session)
    before_cavity_models = {id(model) for model in session.models.list()}
    pockets = find_kvfinder_pockets(
        session,
        model_hint=model_hint,
        top_n=max(1, int(top_n or 1)),
        lining_shell=3.5,
        max_lining_shell=5.0,
        min_lining_residues=3,
        min_volume=30.0,
        min_depth=1.0,
        cleanup_models=False,
        return_cavity_models=True,
    )
    created_cavity_models = _new_models(session, before_cavity_models)
    if not pockets:
        return (
            "Auto CAVER start did not find a usable cavity.\n"
            "- For blind void search, use the Cavity button first.\n"
            "- For CAVER tunnel tracing, select one catalytic/ligand/pocket residue or atom as the start point."
        )

    pocket = pockets[0]
    coords = None
    cavity_model = pocket.get("cavity_model")
    cavity_spec = str(pocket.get("cavity_model_spec") or "").strip()
    try:
        if cavity_model is not None and len(cavity_model.atoms):
            coords = np.asarray(cavity_model.atoms.scene_coords, dtype=float).mean(axis=0)
    except Exception:
        coords = None
    if coords is None:
        residue_coords = []
        for residue in pocket.get("lining_residues") or []:
            try:
                residue_coords.append(np.asarray(residue.atoms.scene_coords, dtype=float))
            except Exception:
                pass
        if residue_coords:
            coords = np.concatenate(residue_coords, axis=0).mean(axis=0)
    if coords is None:
        return "Auto CAVER start found a cavity, but could not compute a coordinate for it."

    label = (
        f"auto cavity #{pocket.get('index')} "
        f"(volume {float(pocket.get('volume', 0.0) or 0.0):.0f} A^3, "
        f"depth {float(pocket.get('max_depth', 0.0) or 0.0):.1f} A)"
    )
    session._codex_bridge_caver_start = {
        "coords": tuple(float(v) for v in coords),
        "label": label,
        "pocket_index": pocket.get("index"),
        "model_spec": pocket.get("model_spec"),
    }

    start_visuals = []
    retained_cavity_models = _retain_top_caver_cavity_preview(session, created_cavity_models, cavity_model)
    try:
        if cavity_model is not None:
            cavity_model.name = "CAVER start cavity preview (not tunnel)"
    except Exception:
        pass
    for model in retained_cavity_models:
        start_visuals.append({"model": model, "role": "cavity"})
    if cavity_spec:
        for command in (
            f"show {cavity_spec} atoms",
            f"style {cavity_spec} sphere",
            f"color {cavity_spec} cyan target a",
            f"transparency {cavity_spec} 78 target a",
            f"surface {cavity_spec}",
            f"color {cavity_spec} cyan target s",
            f"transparency {cavity_spec} 88 target s",
        ):
            try:
                _run(session, command, executor=executor)
            except Exception:
                pass

    coords_tuple = session._codex_bridge_caver_start["coords"]
    marker = _create_caver_start_marker(session, coords_tuple, label, executor=executor)
    if marker is not None:
        start_visuals.append({"model": marker, "role": "marker"})
    session._codex_bridge_caver_start_visuals = start_visuals
    return "\n".join([
        "Auto CAVER start set from top KVFinder cavity.",
        "- visual: faint cyan cavity is only a start preview; orange marker is the CAVER seed.",
        f"- start: {label}",
        f"- coordinates: {coords_tuple[0]:.2f}, {coords_tuple[1]:.2f}, {coords_tuple[2]:.2f}",
        "- next: Run Local renders the actual CAVER tunnel tube overlays.",
    ])


def prepare_caver_job(session, output_dir=None, executor=None):
    from .toolbar_actions import _export_first_structure_file

    start = get_caver_start_point(session)
    if start is None:
        raise RuntimeError("No atomic model or selection is available for CAVER start point.")

    root = Path(output_dir).expanduser() if output_dir else _default_job_dir()
    input_dir = root / "input_pdb"
    output = root / "output"
    input_dir.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)

    exported, _tmp_dir = _export_first_structure_file(session, fmt="pdb", prefix="chimerax_caver_query", executor=executor)
    if exported is None:
        raise RuntimeError("No atomic structure is open for CAVER.")
    query_path = input_dir / "query.pdb"
    shutil.copy2(exported, query_path)

    config_path = root / "config.txt"
    coords = start["coords"]
    config_path.write_text(
        "\n".join(
            [
                "# CAVER config generated by ChimeraXbridge",
                "# Edit probe/shell settings here if needed before running local CAVER.",
                "load_tunnels no",
                "load_cluster_tree no",
                "stop_after never",
                "time_sparsity 1",
                "first_frame 1",
                "last_frame 1",
                f"starting_point_coordinates {coords[0]:.3f} {coords[1]:.3f} {coords[2]:.3f}",
                "probe_radius 0.9",
                "shell_radius 3",
                "shell_depth 4",
                "",
            ]
        ),
        encoding="utf-8",
    )

    job = {
        "root": root,
        "input_dir": input_dir,
        "output_dir": output,
        "query_pdb": query_path,
        "config": config_path,
        "start": start,
    }
    session._codex_bridge_last_caver_job = job
    return job


def format_caver_job(job):
    return "\n".join(
        [
            "CAVER job prepared.",
            f"- query PDB: {job['query_pdb']}",
            f"- config: {job['config']}",
            f"- output: {job['output_dir']}",
            f"- start point: {job['start']['label']} ({job['start']['coords'][0]:.2f}, {job['start']['coords'][1]:.2f}, {job['start']['coords'][2]:.2f})",
        ]
    )


def run_caver_local(session, output_dir=None, executor=None, import_after=True):
    home, jar = _resolve_caver_home_and_jar()
    java = shutil.which(os.environ.get("CAVER_JAVA", "java"))
    if not java:
        return "Local CAVER not run: Java was not found. Set CAVER_JAVA or install Java."
    if not jar:
        return "Local CAVER not run: set CAVER_HOME to a CAVER 3.0 directory or CAVER_JAR to caver.jar."
    if not home:
        home = str(Path(jar).resolve().parent)

    try:
        job = prepare_caver_job(session, output_dir=output_dir, executor=executor)
    except Exception as err:
        return f"Local CAVER not run: {err}"

    command = [
        java,
        "-Xmx1200m",
        "-cp",
        str(Path(home) / "lib" / "*"),
        "-jar",
        str(jar),
        "-home",
        str(home),
        "-pdb",
        str(job["input_dir"]),
        "-conf",
        str(job["config"]),
        "-out",
        str(job["output_dir"]),
    ]
    env = os.environ.copy()
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=3600,
        check=False,
        env=env,
    )
    log_path = job["root"] / "caver_run.log"
    log_path.write_text(
        "COMMAND:\n"
        + " ".join(_quote_shell(part) for part in command)
        + "\n\nSTDOUT:\n"
        + (result.stdout or "")
        + "\n\nSTDERR:\n"
        + (result.stderr or ""),
        encoding="utf-8",
    )
    if result.returncode != 0:
        return "\n".join(
            [
                f"CAVER exited with code {result.returncode}.",
                f"- job: {job['root']}",
                f"- log: {log_path}",
                "- inspect config/output and rerun, or use CAVER Web from the panel.",
            ]
        )
    if import_after:
        imported = import_caver_results(session, job["output_dir"], executor=executor)
    else:
        imported = "Import skipped."
    return "\n".join(["Local CAVER finished.", f"- job: {job['root']}", f"- log: {log_path}", imported])


def import_caver_results(session, path, executor=None, max_tunnels=DEFAULT_MAX_TUNNELS):
    result_path = Path(str(path)).expanduser()
    return _run_on_ui_thread(
        session,
        lambda: _import_caver_results_on_ui(session, result_path, executor=executor, max_tunnels=max_tunnels),
    )


def select_caver_lining_residues(session, arg="", executor=None, distance=4.0):
    tunnels = list(getattr(session, "_codex_bridge_caver_tunnels", []) or [])
    if not tunnels:
        return "No CAVER tunnels are imported. Use CAVER panel > Import Result first."
    try:
        index = int(str(arg or "").strip().split()[0]) - 1 if str(arg or "").strip() else 0
    except Exception:
        index = 0
    index = max(0, min(index, len(tunnels) - 1))
    tunnel = tunnels[index]
    return _run_on_ui_thread(
        session,
        lambda: _select_lining_on_ui(session, tunnel, distance=distance, executor=executor),
    )


def _import_caver_results_on_ui(session, result_path, executor=None, max_tunnels=DEFAULT_MAX_TUNNELS):
    _cleanup_caver_import_state(session)
    root, cleanup_root = _materialize_caver_result_path(result_path)
    generated_root = None
    generated_roots = []
    try:
        generated_summary = []
        clean_summary = []
        generated_root, tunnel_files, generated_summary = _generate_tunnel_pdbs_from_profiles(root, max_tunnels=max_tunnels)
        if generated_root is not None:
            generated_roots.append(generated_root)
        if not tunnel_files:
            tunnel_files = _find_caver_tunnel_files(root)
            if generated_root is not None:
                generated_roots.append(generated_root)
        if not tunnel_files:
            return "\n".join([
                f"No displayable CAVER tunnel coordinates found under {result_path}.",
                "- expected: data/clusters_timeless/tun_cl_*.pdb, or analysis/tunnel_profiles.csv with X/Y/Z/R rows",
                "- if CAVER reports 0 tunnels, there is nothing to render in the 3D view.",
            ])
        tunnel_files = tunnel_files[: max(1, int(max_tunnels))]
        if not generated_summary:
            clean_root, clean_files, clean_summary = _clean_caver_tunnel_pdbs_for_chimerax(tunnel_files)
            if clean_files:
                generated_root = clean_root
                generated_roots.append(clean_root)
                tunnel_files = clean_files
        before = {id(model) for model in session.models.list()}
        opened = []
        commands = []
        for path in tunnel_files:
            label = _tunnel_label_from_path(path)
            command = f"open {_quote_chimerax(str(path))} name {_quote_chimerax(label)}"
            _run(session, command, executor=executor)
            commands.append(command)
            opened.extend(_new_atomic_models(session, before))
            before.update(id(model) for model in opened)

        opened = _dedupe_models(opened)
        if not opened:
            return f"CAVER tunnel files were opened, but no atomic tunnel models were detected: {result_path}"
        tube_models = _style_tunnel_models(session, opened, executor=executor)
        _retire_caver_start_cavity_preview(session)
        summary = _summarize_tunnels(opened)
        session._codex_bridge_caver_tunnels = [
            {
                "model": model,
                "tube_model": tube_models[index] if index < len(tube_models) else None,
                "spec": f"#{getattr(model, 'id_string', '?')}",
                "name": getattr(model, "name", "tunnel"),
            }
            for index, model in enumerate(opened)
        ]
        session._codex_bridge_caver_tube_models = tube_models
        _register_caver_groups(session, opened)
        lines = [
            f"Imported {len(opened)} CAVER tunnel model(s).",
            f"- source: {result_path}",
            "- visual: smooth CAVER tube overlays added; faint spheres keep radius-envelope context",
            "- radii: atom radii read from CAVER occupancy/B-factor values",
            "- camera: current 3D view preserved",
            "- command: /caver lining [1..N] selects protein residues within 4 A of a tunnel",
        ]
        if generated_summary:
            lines.append("- generated ChimeraX PDB overlays from tunnel_profiles.csv")
        if clean_summary:
            lines.append("- normalized CAVER tunnel PDB records before opening in ChimeraX")
        lines.extend(summary[:8])
        if len(summary) > 8:
            lines.append(f"- ... {len(summary) - 8} more tunnel(s)")
        return "\n".join(lines)
    finally:
        temp_roots = [path for path in [cleanup_root, *generated_roots] if path is not None]
        if temp_roots:
            session._codex_bridge_caver_temp_roots = list(getattr(session, "_codex_bridge_caver_temp_roots", []) or [])
            session._codex_bridge_caver_temp_roots.extend(str(path) for path in temp_roots)


def _cleanup_caver_import_state(session):
    prior = [
        item.get("model")
        for item in getattr(session, "_codex_bridge_caver_tunnels", []) or []
        if isinstance(item, dict) and item.get("model") is not None
    ]
    prior.extend([
        item.get("tube_model")
        for item in getattr(session, "_codex_bridge_caver_tunnels", []) or []
        if isinstance(item, dict) and item.get("tube_model") is not None
    ])
    prior.extend(list(getattr(session, "_codex_bridge_caver_tube_models", []) or []))
    prior = _dedupe_models([model for model in prior if model is not None])
    if prior:
        try:
            session.models.close(prior)
        except Exception as err:
            session.logger.warning(f"Could not close prior CAVER tunnel models: {err}")
    session._codex_bridge_caver_tunnels = []
    session._codex_bridge_caver_tube_models = []
    try:
        from .named_selection import list_groups, remove_group

        for group_name in list_groups(session):
            if str(group_name).startswith("caver_"):
                remove_group(session, group_name)
    except Exception:
        pass


def _register_caver_groups(session, models):
    try:
        from .named_selection import add_group
    except Exception:
        return
    for index, model in enumerate(models, start=1):
        spec = f"#{getattr(model, 'id_string', '?')}"
        name = f"caver_tunnel_{index}"
        try:
            add_group(session, name, spec, color=CAVER_TUNNEL_COLORS[(index - 1) % len(CAVER_TUNNEL_COLORS)])
        except Exception as err:
            session.logger.warning(f"Could not create CAVER group '{name}': {err}")


def _style_tunnel_models(session, models, executor=None):
    specs = " ".join(f"#{getattr(model, 'id_string', '?')}" for model in models)
    if not specs:
        return []
    tube_models = []
    for index, model in enumerate(models, start=1):
        color = CAVER_TUNNEL_COLORS[(index - 1) % len(CAVER_TUNNEL_COLORS)]
        try:
            model.name = f"CAVER radius envelope {index:02d}"
        except Exception:
            pass
        had_radius = False
        try:
            for atom in model.atoms:
                radius = _caver_atom_radius(atom)
                if radius > 0:
                    had_radius = True
                    atom.radius = max(0.08, min(radius, 8.0))
        except Exception:
            pass
        if not had_radius:
            try:
                for atom in model.atoms:
                    atom.radius = 0.8
            except Exception:
                pass
        spec = f"#{getattr(model, 'id_string', '?')}"
        _set_atomic_model_color(model, color)
        for command in (
            f"show {spec} atoms",
            f"style {spec} sphere",
            f"transparency {spec} 82 target a",
        ):
            try:
                _run(session, command, executor=executor)
            except Exception:
                pass
        tube = _create_caver_tube_overlay(session, model, index, color, executor=executor)
        tube_models.append(tube)
    visible_tubes = [model for model in tube_models if model is not None]
    for command in (
        f"show {' '.join(_model_spec(model) for model in visible_tubes)} models" if visible_tubes else "",
        f"transparency {' '.join(_model_spec(model) for model in visible_tubes)} 0 target s" if visible_tubes else "",
    ):
        if not command:
            continue
        try:
            _run(session, command, executor=executor)
        except Exception:
            pass
    return tube_models


def _create_caver_tube_overlay(session, model, index, color, executor=None):
    spec = _model_spec(model)
    if not spec:
        return None
    before = {id(item) for item in session.models.list()}
    label = f"CAVER tube {index:02d}"
    command = (
        f"shape tube {spec} radius 0.45 followBonds true "
        f"segmentSubdivisions 12 divisions 24 color {color} name {_quote_chimerax(label)}"
    )
    try:
        _run(session, command, executor=executor)
    except Exception as err:
        try:
            session.logger.warning(f"Could not create CAVER tube overlay for {spec}: {err}")
        except Exception:
            pass
        return None
    created = _new_models(session, before)
    if not created:
        return None
    tube = created[0]
    try:
        tube.name = label
    except Exception:
        pass
    return tube


def _set_atomic_model_color(model, color):
    rgba = _hex_to_rgba8(color)
    if rgba is None:
        return
    try:
        model.atoms.colors = rgba
    except Exception:
        pass
    try:
        model.bonds.colors = rgba
    except Exception:
        pass


def _hex_to_rgba8(color):
    text = str(color or "").strip()
    if text.startswith("#"):
        text = text[1:]
    if len(text) != 6:
        return None
    try:
        return tuple(int(text[index:index + 2], 16) for index in (0, 2, 4)) + (255,)
    except Exception:
        return None


def _create_caver_start_marker(session, coords, label, executor=None):
    try:
        x, y, z = (float(coords[0]), float(coords[1]), float(coords[2]))
    except Exception:
        return None
    before = {id(item) for item in session.models.list()}
    try:
        _run(
            session,
            f"shape sphere radius 0.85 center {x:.3f},{y:.3f},{z:.3f} color orange name {_quote_chimerax('CAVER start marker')}",
            executor=executor,
        )
    except Exception as err:
        try:
            session.logger.warning(f"Could not create CAVER start marker: {err}")
        except Exception:
            pass
        return None
    created = _new_models(session, before)
    if not created:
        return None
    marker = created[0]
    try:
        marker.name = "CAVER start marker"
    except Exception:
        pass
    spec = _model_spec(marker)
    if spec:
        text = "CAVER start seed (not tunnel)"
        try:
            _run(
                session,
                f"label {spec} models text {_quote_chimerax(text)} height 0.8 color orange bgColor none",
                executor=executor,
            )
        except Exception:
            pass
    return marker


def _cleanup_caver_start_visuals(session):
    visuals = getattr(session, "_codex_bridge_caver_start_visuals", []) or []
    models = []
    for item in visuals:
        model = item.get("model") if isinstance(item, dict) else item
        if model is not None:
            models.append(model)
    if models:
        try:
            session.models.close(_dedupe_models(models))
        except Exception as err:
            try:
                session.logger.warning(f"Could not close prior CAVER start preview models: {err}")
            except Exception:
                pass
    session._codex_bridge_caver_start_visuals = []


def _retain_top_caver_cavity_preview(session, created_models, top_model):
    if top_model is None:
        _close_models_quietly(session, created_models)
        return []
    top_id = str(getattr(top_model, "id_string", "") or "")
    keep = []
    close = []
    for model in created_models:
        model_id = str(getattr(model, "id_string", "") or "")
        if model is top_model or model_id == top_id or (model_id and top_id.startswith(model_id + ".")):
            keep.append(model)
        else:
            close.append(model)
    _close_models_quietly(session, close)
    if top_model not in keep:
        keep.append(top_model)
    return _dedupe_models(keep)


def _retire_caver_start_cavity_preview(session):
    visuals = getattr(session, "_codex_bridge_caver_start_visuals", []) or []
    keep = []
    close = []
    for item in visuals:
        if not isinstance(item, dict):
            continue
        model = item.get("model")
        if model is None:
            continue
        if item.get("role") == "cavity":
            close.append(model)
        else:
            keep.append(item)
    if close:
        _close_models_quietly(session, close)
    session._codex_bridge_caver_start_visuals = keep


def _close_models_quietly(session, models):
    models = _dedupe_models([model for model in models if model is not None])
    if not models:
        return
    try:
        session.models.close(models)
    except Exception:
        pass


def _caver_atom_radius(atom):
    for attr in ("occupancy", "bfactor"):
        try:
            value = float(getattr(atom, attr, 0.0) or 0.0)
        except Exception:
            value = 0.0
        if value > 0:
            return value
    return 0.0


def _select_lining_on_ui(session, tunnel, distance=4.0, executor=None):
    try:
        import numpy as np
        from chimerax.atomic import AtomicStructure
    except Exception as err:
        return f"Could not select CAVER lining residues: {err}"

    tunnel_model = tunnel.get("model")
    if tunnel_model is None:
        return "The selected CAVER tunnel model is no longer available."
    try:
        tunnel_coords = np.asarray(tunnel_model.atoms.scene_coords, dtype=float)
    except Exception:
        return "The selected CAVER tunnel has no atom coordinates."
    if tunnel_coords.size == 0:
        return "The selected CAVER tunnel has no coordinates."

    selected_specs = []
    tunnel_ids = {id(item.get("model")) for item in getattr(session, "_codex_bridge_caver_tunnels", []) or []}
    cutoff2 = float(distance) * float(distance)
    for model in session.models.list(type=AtomicStructure):
        if id(model) in tunnel_ids:
            continue
        model_spec = f"#{getattr(model, 'id_string', '?')}"
        for residue in model.residues:
            try:
                coords = np.asarray(residue.atoms.scene_coords, dtype=float)
            except Exception:
                continue
            if coords.size == 0:
                continue
            if _within_cutoff(coords, tunnel_coords, cutoff2):
                selected_specs.append(_residue_spec(model_spec, residue))

    if not selected_specs:
        return f"No protein residues found within {distance:.1f} A of {tunnel.get('name', 'CAVER tunnel')}."
    selection_text = " ".join(selected_specs[:400])
    commands = [
        f"select {selection_text}",
        "name frozen caver_lining sel",
        "show sel atoms",
        "style sel stick",
        "color sel #ffcc66 target ac",
    ]
    for command in commands:
        _run(session, command, executor=executor)
        if command.lower().startswith("color sel "):
            from .display_color import restore_charge_colors

            restore_charge_colors(session, "sel")
    return "\n".join(
        [
            f"Selected {len(selected_specs)} residue(s) lining {tunnel.get('name', 'CAVER tunnel')}.",
            "- named selection: caver_lining",
            "Executed ChimeraX commands:",
            *[f"- {command}" for command in commands],
        ]
    )


def _within_cutoff(coords_a, coords_b, cutoff2):
    import numpy as np

    # Chunked distance test avoids allocating a huge all-vs-all matrix for large proteins.
    coords_a = np.asarray(coords_a, dtype=float)
    coords_b = np.asarray(coords_b, dtype=float)
    for start in range(0, len(coords_a), 64):
        delta = coords_a[start : start + 64, None, :] - coords_b[None, :, :]
        if np.any(np.sum(delta * delta, axis=2) <= cutoff2):
            return True
    return False


def _residue_spec(model_spec, residue):
    chain = str(getattr(residue, "chain_id", "") or "?").strip() or "?"
    number = getattr(residue, "number", "?")
    return f"{model_spec}/{chain}:{number}"


def _find_caver_tunnel_files(root):
    root = Path(root)
    preferred_dirs = [
        root / "results" / "data" / "clusters_timeless",
        root / "data" / "clusters_timeless",
        root / "clusters_timeless",
    ]
    candidates = []
    for directory in preferred_dirs:
        if directory.exists():
            candidates.extend(directory.glob("tun*.pdb"))
    if not candidates:
        candidates.extend(root.rglob("tun_cl_*.pdb"))
    if not candidates:
        candidates.extend(path for path in root.rglob("*.pdb") if "tun" in path.name.lower())
    return sorted(set(candidates), key=_tunnel_sort_key)


def _generate_tunnel_pdbs_from_profiles(root, max_tunnels=DEFAULT_MAX_TUNNELS):
    profile_path = _find_caver_profile_table(root)
    if profile_path is None:
        return None, [], []

    profiles = _read_caver_profile_table(profile_path)
    if not profiles:
        return None, [], []

    output_root = Path(tempfile.mkdtemp(prefix="chimerax_caver_profiles_"))
    files = []
    summary = []
    for index, profile in enumerate(profiles[: max(1, int(max_tunnels or DEFAULT_MAX_TUNNELS))], start=1):
        path = output_root / f"tun_cl_{int(profile['cluster']):03d}_profile.pdb"
        _write_caver_profile_pdb(path, profile)
        files.append(path)
        summary.append(profile)
    return output_root, files, summary


def _clean_caver_tunnel_pdbs_for_chimerax(tunnel_files):
    output_root = Path(tempfile.mkdtemp(prefix="chimerax_caver_clean_"))
    files = []
    summary = []
    for index, source in enumerate(tunnel_files, start=1):
        points = _read_caver_tunnel_pdb_points(source)
        if not points:
            continue
        target = output_root / f"{Path(source).stem}_chimerax.pdb"
        _write_caver_points_pdb(
            target,
            points,
            remarks=[
                "REMARK   1 CAVER tunnel PDB normalized for ChimeraX display",
                f"REMARK   2 source {source}",
            ],
        )
        files.append(target)
        summary.append({"source": str(source), "points": len(points)})
    if not files:
        try:
            shutil.rmtree(output_root)
        except Exception:
            pass
        return None, [], []
    return output_root, files, summary


def _read_caver_tunnel_pdb_points(path):
    points = []
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return points
    for line in lines:
        if not line.startswith(("ATOM", "HETATM")):
            continue
        parsed = _parse_caver_tunnel_atom_line(line)
        if parsed is not None:
            points.append(parsed)
    return points


def _parse_caver_tunnel_atom_line(line):
    candidates = []
    try:
        x = float(line[30:38])
        y = float(line[38:46])
        z = float(line[46:54])
        tail_values = _numeric_values(re.split(r"\s+", line[54:].strip()))
        radius = tail_values[0] if tail_values else 1.0
        candidates.append((x, y, z, radius))
    except Exception:
        pass
    parts = str(line).split()
    if len(parts) >= 9:
        for start in range(max(0, len(parts) - 7), max(0, len(parts) - 2)):
            try:
                x = float(parts[start])
                y = float(parts[start + 1])
                z = float(parts[start + 2])
            except Exception:
                continue
            radius = 1.0
            for token in parts[start + 3:]:
                try:
                    value = float(token)
                except Exception:
                    continue
                if value > 0:
                    radius = value
                    break
            candidates.append((x, y, z, radius))
            break
    if not candidates:
        return None
    x, y, z, radius = candidates[-1]
    radius = max(0.1, min(float(radius or 1.0), 12.0))
    return (float(x), float(y), float(z), radius)


def _write_caver_points_pdb(path, points, remarks=None):
    lines = list(remarks or [])
    for serial, (x, y, z, radius) in enumerate(points, start=1):
        resseq = min(serial, 9999)
        lines.append(
            f"HETATM{serial:5d}  C   TUN T{resseq:4d}    "
            f"{x:8.3f}{y:8.3f}{z:8.3f}{radius:6.2f}{radius:6.2f}          C"
        )
    for serial in range(1, len(points)):
        lines.append(f"CONECT{serial:5d}{serial + 1:5d}")
    lines.extend(["TER", "END", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def _find_caver_profile_table(root):
    root = Path(root)
    candidates = [
        root / "analysis" / "tunnel_profiles.csv",
        root / "results" / "analysis" / "tunnel_profiles.csv",
        root / "tunnel_profiles.csv",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    matches = sorted(root.rglob("tunnel_profiles.csv"), key=lambda item: (len(item.parts), str(item)))
    return matches[0] if matches else None


def _read_caver_profile_table(path):
    profiles = {}
    order = []
    try:
        with Path(path).open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.reader(handle)
            header = next(reader, [])
            axis_index = _first_matching_index(header, "Axis")
            cluster_index = _first_matching_index(header, "Tunnel cluster")
            tunnel_index = _first_matching_index(header, "Tunnel")
            throughput_index = _first_matching_index(header, "Throughput")
            bottleneck_index = _first_matching_index(header, "Bottleneck radius")
            length_index = _first_matching_index(header, "Length")
            curvature_index = _first_matching_index(header, "Curvature")
            if axis_index is None or cluster_index is None:
                return []
            values_index = axis_index + 1
            for row in reader:
                if len(row) <= values_index:
                    continue
                axis = str(row[axis_index] or "").strip().upper()
                if axis not in {"X", "Y", "Z", "R"}:
                    continue
                cluster_text = str(row[cluster_index] or "").strip()
                if not cluster_text:
                    continue
                try:
                    cluster = int(float(cluster_text))
                except Exception:
                    continue
                tunnel = _field_text(row, tunnel_index) or str(cluster)
                key = (cluster, tunnel)
                if key not in profiles:
                    profiles[key] = {
                        "cluster": cluster,
                        "tunnel": tunnel,
                        "axes": {},
                        "throughput": _field_text(row, throughput_index),
                        "bottleneck": _field_text(row, bottleneck_index),
                        "length": _field_text(row, length_index),
                        "curvature": _field_text(row, curvature_index),
                    }
                    order.append(key)
                profiles[key]["axes"][axis] = _numeric_values(row[values_index:])
    except Exception:
        return []

    parsed = []
    for key in order:
        profile = profiles[key]
        axes = profile.get("axes") or {}
        count = min(len(axes.get(axis, [])) for axis in ("X", "Y", "Z", "R") if axis in axes) if all(axis in axes for axis in ("X", "Y", "Z", "R")) else 0
        if count <= 0:
            continue
        profile["points"] = [
            (
                axes["X"][i],
                axes["Y"][i],
                axes["Z"][i],
                max(0.1, axes["R"][i]),
            )
            for i in range(count)
        ]
        parsed.append(profile)
    return parsed


def _first_matching_index(header, name):
    target = str(name).strip().lower()
    for index, item in enumerate(header or []):
        if str(item).strip().lower() == target:
            return index
    return None


def _field_text(row, index):
    if index is None or index >= len(row):
        return ""
    return str(row[index] or "").strip()


def _numeric_values(values):
    parsed = []
    for value in values:
        text = str(value or "").strip()
        if not text or text == "-":
            continue
        try:
            parsed.append(float(text))
        except Exception:
            continue
    return parsed


def _write_caver_profile_pdb(path, profile):
    points = list(profile.get("points") or [])
    remarks = [
        "REMARK   1 CAVER tunnel profile generated for ChimeraX display",
        f"REMARK   2 cluster {profile.get('cluster')} tunnel {profile.get('tunnel')}",
        f"REMARK   3 throughput {profile.get('throughput') or 'n/a'} bottleneck_radius {profile.get('bottleneck') or 'n/a'}",
        f"REMARK   4 length {profile.get('length') or 'n/a'} curvature {profile.get('curvature') or 'n/a'}",
    ]
    _write_caver_points_pdb(path, points, remarks=remarks)


def _materialize_caver_result_path(path):
    path = Path(path).expanduser()
    if not path.exists():
        raise FileNotFoundError(str(path))
    if path.is_dir():
        return path, None
    suffix = path.suffix.lower()
    if suffix == ".zip":
        root = Path(tempfile.mkdtemp(prefix="chimerax_caver_import_"))
        with zipfile.ZipFile(path) as archive:
            archive.extractall(root)
        return root, root
    if suffix == ".pdb":
        root = Path(tempfile.mkdtemp(prefix="chimerax_caver_import_"))
        shutil.copy2(path, root / path.name)
        return root, root
    raise ValueError("CAVER import expects a result folder, .zip archive, or tunnel .pdb file.")


def _new_atomic_models(session, before_ids):
    try:
        from chimerax.atomic import AtomicStructure
    except Exception:
        return []
    return [model for model in session.models.list(type=AtomicStructure) if id(model) not in before_ids]


def _new_models(session, before_ids):
    return [model for model in session.models.list() if id(model) not in before_ids]


def _model_spec(model):
    if model is None:
        return ""
    value = str(getattr(model, "id_string", "") or "").strip()
    return f"#{value}" if value else ""


def _dedupe_models(models):
    seen = set()
    unique = []
    for model in models:
        marker = id(model)
        if marker in seen:
            continue
        seen.add(marker)
        unique.append(model)
    return unique


def _summarize_tunnels(models):
    lines = []
    for index, model in enumerate(models, start=1):
        radii = []
        try:
            radii = [_caver_atom_radius(atom) for atom in model.atoms]
            radii = [value for value in radii if value > 0]
        except Exception:
            radii = []
        if radii:
            lines.append(
                f"- tunnel {index}: {getattr(model, 'name', 'tunnel')} radius min {min(radii):.2f} A, max {max(radii):.2f} A"
            )
        else:
            lines.append(f"- tunnel {index}: {getattr(model, 'name', 'tunnel')}")
    return lines


def _tunnel_label_from_path(path):
    match = re.search(r"tun(?:nel)?[_-]?cl[_-]?(\d+)", path.stem, re.I)
    if match:
        return f"CAVER tunnel {int(match.group(1)):02d}"
    return "CAVER " + path.stem


def _tunnel_sort_key(path):
    numbers = [int(item) for item in re.findall(r"\d+", path.name)]
    return (numbers or [999999], path.name)


def _default_job_dir():
    stamp = time.strftime("%Y%m%d_%H%M%S")
    return Path.home() / "ChimeraX_CAVER" / f"caver_job_{stamp}"


def _resolve_caver_home_and_jar():
    jar_env = os.environ.get("CAVER_JAR")
    home_env = os.environ.get("CAVER_HOME")
    home = Path(home_env).expanduser() if home_env else None
    jar = Path(jar_env).expanduser() if jar_env else None
    if jar is None and home is not None:
        candidate = home / "caver.jar"
        if candidate.exists():
            jar = candidate
    if home is None and jar is not None:
        home = jar.parent
    if jar is None or not jar.exists():
        jar = _auto_detect_caver_jar()
        if jar is not None:
            home = jar.parent
            os.environ.setdefault("CAVER_JAR", str(jar))
            os.environ.setdefault("CAVER_HOME", str(home))
    return (str(home) if home and home.exists() else "", str(jar) if jar and jar.exists() else "")


def _auto_detect_caver_jar():
    global _CAVER_JAR_CACHE
    if _CAVER_JAR_CACHE:
        cached = Path(_CAVER_JAR_CACHE)
        if _looks_like_caver_jar(cached):
            return cached
    candidates = []

    if sys.platform == "darwin":
        try:
            result = subprocess.run(
                [
                    "mdfind",
                    'kMDItemFSName == "caver.jar"cd || kMDItemFSName == "*caver*.jar"cd',
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            for line in (result.stdout or "").splitlines():
                text = line.strip()
                if text:
                    candidates.append(Path(text))
        except Exception:
            pass

    for base in (Path.home() / "Downloads", Path.home() / "Applications", Path("/Applications")):
        try:
            if base.exists():
                candidates.extend(base.glob("**/caver.jar"))
        except Exception:
            pass

    seen = set()
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve()
        except Exception:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if _looks_like_caver_jar(resolved):
            _CAVER_JAR_CACHE = str(resolved)
            return resolved
    return None


def _looks_like_caver_jar(path):
    try:
        path = Path(path).expanduser()
    except Exception:
        return False
    if not path.exists() or path.name.lower() != "caver.jar":
        return False
    home = path.parent
    if not (home / "lib").exists():
        return False
    return True


def _quote_chimerax(text):
    from chimerax.core.commands import StringArg

    return StringArg.unparse(str(text))


def _quote_shell(text):
    import shlex

    return shlex.quote(str(text))


def _run(session, command, executor=None):
    if executor is not None:
        return executor(command)
    from chimerax.core.commands import run

    return run(session, command)


def _run_on_ui_thread(session, func):
    if _is_ui_thread():
        return func()
    result = {}
    done = threading.Event()

    def wrapped():
        try:
            result["value"] = func()
        except Exception as err:
            result["error"] = err
        finally:
            done.set()

    try:
        session.ui.thread_safe(wrapped)
        done.wait(120)
    except Exception:
        return func()
    if not done.is_set():
        raise TimeoutError("Timed out waiting for ChimeraX UI thread.")
    if "error" in result:
        raise result["error"]
    return result.get("value")


def _is_ui_thread():
    try:
        from Qt.QtCore import QThread
        from Qt.QtWidgets import QApplication

        app = QApplication.instance()
        return app is not None and QThread.currentThread() == app.thread()
    except Exception:
        return True


def _split_action_arg(arg):
    text = str(arg or "").strip()
    if not text:
        return "", ""
    head, _, tail = text.partition(" ")
    return head.strip().lower(), tail.strip()


class CodexCaverTool(ToolInstance):

    SESSION_ENDURING = False
    SESSION_SAVE = False
    UI_LAYOUT_VERSION = 3
    help = "help:user/tools/codex_assistant.html"

    @classmethod
    def get_singleton(cls, session, create=True, display=True, **kw):
        instance = get_singleton(session, cls, "CAVER", create=create, display=display, **kw)
        if instance is not None and getattr(instance, "_ui_layout_version", None) != cls.UI_LAYOUT_VERSION:
            try:
                instance.delete()
            except Exception:
                pass
            instance = get_singleton(session, cls, "CAVER", create=create, display=display, **kw)
        return instance

    def __init__(self, session, tool_name):
        super().__init__(session, tool_name)
        self._ui_layout_version = self.UI_LAYOUT_VERSION
        from chimerax.ui import MainToolWindow

        self.tool_window = MainToolWindow(self, close_destroys=True)
        self._build_ui()

    def _build_ui(self):
        from Qt.QtCore import Qt
        from Qt.QtWidgets import QGridLayout, QLabel, QLineEdit, QPushButton, QPlainTextEdit, QSizePolicy, QVBoxLayout

        parent = self.tool_window.ui_area
        layout = QVBoxLayout()
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        parent.setLayout(layout)
        parent.setStyleSheet(
            "QWidget { background: #171a1d; color: #e5e8ec; }"
            "QLabel { background: transparent; border: none; }"
            "QLineEdit, QPlainTextEdit { background: #101417; color: #eef1f4; border: 1px solid #344150; border-radius: 7px; padding: 5px 8px; }"
            "QPushButton { background: #20262d; color: #eef1f4; border: 1px solid #344150; border-radius: 7px; padding: 5px 7px; }"
            "QPushButton:hover { background: #29313a; border-color: #5c6a78; }"
        )

        title = QLabel("CAVER tunnel/channel analysis", parent)
        title_font = title.font()
        title_font.setPointSize(13)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        caption = QLabel(
            "Auto Start shows only a faint cavity seed preview. Run Local renders the actual CAVER tunnel tubes.",
            parent,
        )
        caption.setWordWrap(True)
        layout.addWidget(caption)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)

        self.start_label = QLabel("Start", parent)
        self.start_field = QLineEdit(parent)
        self.start_field.setReadOnly(True)
        self.start_field.setMinimumWidth(0)
        self.start_field.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        grid.addWidget(self.start_label, 0, 0)
        grid.addWidget(self.start_field, 0, 1)

        self.caver_home_label = QLabel("CAVER_HOME", parent)
        self.caver_home_field = QLineEdit(parent)
        self.caver_home_field.setPlaceholderText("Optional: /path/to/caver or set CAVER_HOME")
        self.caver_home_field.setMinimumWidth(0)
        self.caver_home_field.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        grid.addWidget(self.caver_home_label, 1, 0)
        grid.addWidget(self.caver_home_field, 1, 1)

        button_grid = QGridLayout()
        button_grid.setHorizontalSpacing(6)
        button_grid.setVerticalSpacing(6)
        layout.addLayout(button_grid)
        buttons = [
            ("Refresh", self.refresh),
            ("Auto Start", self.auto_start),
            ("Prepare Job", self.prepare_job),
            ("Run Local", self.run_local),
            ("Open Web", self.open_web),
            ("Use Selection", self.use_selection_start),
            ("Import ZIP/PDB", self.import_file),
            ("Import Folder", self.import_folder),
            ("Select Lining", self.select_lining),
            ("Status", self.show_status),
        ]
        for index, (label, callback) in enumerate(buttons):
            button = QPushButton(label, parent)
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.clicked.connect(callback)
            button_grid.addWidget(button, index // 3, index % 3)
        for column in range(3):
            button_grid.setColumnStretch(column, 1)

        self.output = QPlainTextEdit(parent)
        self.output.setReadOnly(True)
        self.output.setMinimumHeight(130)
        self.output.setMinimumWidth(0)
        layout.addWidget(self.output, 1)
        self.tool_window.manage(placement="side")
        self.refresh()

    def refresh(self):
        start = get_caver_start_point(self.session)
        if start is None:
            self.start_field.setText("No atomic model/selection")
            self.start_field.setToolTip("")
        else:
            coords = start["coords"]
            start_text = f"{start['label']} · {coords[0]:.2f}, {coords[1]:.2f}, {coords[2]:.2f}"
            self.start_field.setText(start_text)
            self.start_field.setToolTip(start_text)
        home, _jar = _resolve_caver_home_and_jar()
        if home and not self.caver_home_field.text().strip():
            self.caver_home_field.setText(home)
            self.caver_home_field.setToolTip(home)

    def prepare_job(self):
        self._append("Preparing CAVER job...")
        try:
            job = prepare_caver_job(self.session)
            self._append(format_caver_job(job))
        except Exception as err:
            self._append(f"error: {err}")

    def auto_start(self):
        self._append("Finding cavity-based CAVER start point; this preview is not the final tunnel result...")
        try:
            model_hint = None
            try:
                from .toolbar_actions import _TARGET_SELECTION_CANCELLED, _prompt_toolbar_target_model_spec

                model_hint = _prompt_toolbar_target_model_spec(self.session, "CAVER")
                if model_hint is _TARGET_SELECTION_CANCELLED:
                    self._append("CAVER Auto Start cancelled.")
                    return
            except Exception:
                model_hint = None
            self._append(set_caver_start_from_cavity(self.session, model_hint=model_hint))
            self.refresh()
        except Exception as err:
            self._append(f"error: {err}")

    def use_selection_start(self):
        self._append(clear_caver_start_override(self.session))
        self.refresh()

    def run_local(self):
        home_text = self.caver_home_field.text().strip()
        if home_text:
            os.environ["CAVER_HOME"] = home_text
        self._append("Running local CAVER in background...")

        def worker():
            try:
                result = run_caver_local(self.session)
            except Exception as err:
                result = f"error: {err}"
            self._append_threadsafe(result)

        threading.Thread(target=worker, daemon=True).start()

    def open_web(self):
        self._append("Opening CAVER Web...")
        try:
            from .toolbar_actions import (
                _TARGET_SELECTION_CANCELLED,
                _prompt_toolbar_target_model_spec,
                launch_caver_server,
            )

            model_hint = _prompt_toolbar_target_model_spec(self.session, "CAVER Web")
            if model_hint is _TARGET_SELECTION_CANCELLED:
                self._append("CAVER Web cancelled.")
                return
            self._append(launch_caver_server(self.session, model_hint=model_hint))
        except Exception as err:
            self._append(f"error: {err}")

    def import_file(self):
        from Qt.QtWidgets import QFileDialog

        path, _filter = QFileDialog.getOpenFileName(
            self.tool_window.ui_area,
            "Import CAVER result ZIP or tunnel PDB",
            str(Path.home()),
            "CAVER results (*.zip *.pdb);;All files (*)",
        )
        if path:
            self._append(import_caver_results(self.session, path))

    def import_folder(self):
        from Qt.QtWidgets import QFileDialog

        path = QFileDialog.getExistingDirectory(self.tool_window.ui_area, "Import CAVER result folder", str(Path.home()))
        if path:
            self._append(import_caver_results(self.session, path))

    def select_lining(self):
        try:
            self._append(select_caver_lining_residues(self.session))
        except Exception as err:
            self._append(f"error: {err}")

    def show_status(self):
        self.refresh()
        self._append(caver_status(self.session))

    def displayed(self):
        dock_widget = getattr(self.tool_window, "_dock_widget", None)
        if dock_widget is not None:
            return bool(dock_widget.isVisible())
        ui_area = getattr(self.tool_window, "ui_area", None)
        return bool(ui_area is not None and ui_area.isVisible())

    def _append(self, text):
        self.output.appendPlainText(str(text or ""))
        scrollbar = self.output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _append_threadsafe(self, text):
        try:
            self.session.ui.thread_safe(lambda: self._append(text))
        except Exception:
            pass
