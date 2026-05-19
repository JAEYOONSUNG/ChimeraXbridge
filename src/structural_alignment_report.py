"""ESPript-like structural alignment reports for open ChimeraX models.

This module intentionally writes an HTML report instead of drawing a long MSA
inside the 3D viewport. The report is generated from the current ChimeraX scene
coordinates, so it reflects the structural alignment already present in the
session without turning hidden cartoons or extra chains back on.
"""

from __future__ import annotations

import html
import json
import math
import re
import tempfile
import webbrowser
from pathlib import Path


AA3_TO_1 = {
    "ALA": "A",
    "ARG": "R",
    "ASN": "N",
    "ASP": "D",
    "CYS": "C",
    "GLN": "Q",
    "GLU": "E",
    "GLY": "G",
    "HIS": "H",
    "ILE": "I",
    "LEU": "L",
    "LYS": "K",
    "MET": "M",
    "PHE": "F",
    "PRO": "P",
    "SER": "S",
    "THR": "T",
    "TRP": "W",
    "TYR": "Y",
    "VAL": "V",
    "MSE": "M",
    "SEC": "U",
    "PYL": "O",
    "UNK": "X",
}

AA_CLASS = {
    "D": "acidic",
    "E": "acidic",
    "K": "basic",
    "R": "basic",
    "H": "basic",
    "N": "amide",
    "Q": "amide",
    "S": "hydroxyl",
    "T": "hydroxyl",
    "Y": "hydroxyl",
    "C": "sulfur",
    "M": "sulfur",
    "F": "aromatic",
    "W": "aromatic",
    "A": "hydrophobic",
    "V": "hydrophobic",
    "L": "hydrophobic",
    "I": "hydrophobic",
    "G": "glycine",
    "P": "proline",
}

METAL_ELEMENTS = {
    "CA",
    "MG",
    "MN",
    "ZN",
    "FE",
    "CU",
    "CO",
    "NI",
    "CD",
    "SR",
    "BA",
    "YB",
}

SIDECHAIN_DONORS = {
    "ASP": {"OD1", "OD2"},
    "GLU": {"OE1", "OE2"},
    "HIS": {"ND1", "NE2"},
    "ASN": {"OD1", "ND2"},
    "GLN": {"OE1", "NE2"},
    "SER": {"OG"},
    "THR": {"OG1"},
    "TYR": {"OH"},
    "CYS": {"SG"},
    "MET": {"SD"},
}


def generate_structural_alignment_report(
    session,
    *,
    entries=None,
    mode="full",
    core_cutoff=2.0,
    map_cutoff=4.5,
    direct_metal_cutoff=3.2,
    candidate_cutoff=6.0,
    open_report=True,
    output_path=None,
):
    """Create an ESPript-like HTML report for currently aligned structures.

    Parameters
    ----------
    entries
        Optional toolbar model entries. The first entry is the reference.
    mode
        ``"core"`` writes only structurally confident columns in the main MSA.
        ``"full"`` writes every reference-indexed column and annotates loop /
        uncertain positions.
    """
    structures = _resolve_structures(session, entries)
    if len(structures) < 2:
        return "StructAlign: need at least 2 open atomic structures."

    ref_model = structures[0]
    ref_chain = _longest_protein_chain(ref_model)
    if ref_chain is None:
        return "StructAlign: reference has no protein chain."

    ref_residues = _chain_residues(ref_chain)
    if not ref_residues:
        return "StructAlign: reference chain has no resolved residues."

    targets = []
    for model in structures[1:]:
        chain, stats = _best_aligned_chain(model, ref_residues, map_cutoff=map_cutoff)
        if chain is None:
            continue
        targets.append(
            {
                "model": model,
                "chain": chain,
                "residues": _chain_residues(chain),
                "stats": stats,
            }
        )
    if not targets:
        return "StructAlign: no target protein chains could be mapped to the reference."

    metal_hits = _metal_near_residues(structures, direct_metal_cutoff, candidate_cutoff)
    rows, columns, insertion_rows = _build_reference_indexed_alignment(
        ref_model,
        ref_chain,
        ref_residues,
        targets,
        metal_hits,
        core_cutoff=core_cutoff,
        map_cutoff=map_cutoff,
    )

    report_mode = str(mode or "full").strip().lower()
    if report_mode.startswith("core") and not any(col["core"] for col in columns):
        report_mode = "full-no-core-fallback"

    html_text = _render_html_report(
        rows,
        columns,
        all_columns=columns,
        insertion_rows=insertion_rows,
        targets=targets,
        report_mode=report_mode,
        core_cutoff=core_cutoff,
        map_cutoff=map_cutoff,
        direct_metal_cutoff=direct_metal_cutoff,
        candidate_cutoff=candidate_cutoff,
    )

    path = Path(output_path) if output_path else _default_output_path(ref_model)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html_text, encoding="utf-8")
    if open_report:
        try:
            webbrowser.open(path.as_uri())
        except Exception:
            pass

    core_count = sum(1 for col in columns if col["core"])
    metal_count = sum(1 for col in columns if col["metal"])
    return (
        f"StructAlign: wrote {path} | structures={len(structures)} "
        f"columns={len(columns)} mode={report_mode} "
        f"core={core_count} metal-near={metal_count}"
    )


def _resolve_structures(session, entries):
    try:
        from chimerax.atomic import AtomicStructure
    except Exception:
        AtomicStructure = None

    if entries:
        structures = []
        for entry in entries:
            model = entry.get("model") if isinstance(entry, dict) else None
            for atomic_model in _atomic_child_structures(model, AtomicStructure):
                if atomic_model not in structures:
                    structures.append(atomic_model)
        if len(structures) >= 2:
            return structures

    structures = []
    for root_model in session.models.list():
        for model in _atomic_child_structures(root_model, AtomicStructure):
            name = str(getattr(model, "name", "") or "").lower()
            if name.startswith(("metal candidate", "predicted metal", "cavity", "binding_pocket")):
                continue
            if model not in structures:
                structures.append(model)
    return structures


def _atomic_child_structures(model, AtomicStructure=None):
    if model is None:
        return []
    if AtomicStructure is None:
        try:
            from chimerax.atomic import AtomicStructure
        except Exception:
            AtomicStructure = None
    try:
        all_models = list(model.all_models())
    except Exception:
        all_models = [model]
    if model not in all_models:
        all_models.insert(0, model)
    structures = []
    seen = set()
    for item in all_models:
        ident = id(item)
        if ident in seen:
            continue
        seen.add(ident)
        if AtomicStructure is not None and not isinstance(item, AtomicStructure):
            continue
        model = item
        if not hasattr(model, "residues") or not hasattr(model, "atoms"):
            continue
        try:
            atoms = getattr(model, "atoms", None)
            if atoms is not None and len(atoms) == 0:
                continue
        except Exception:
            pass
        structures.append(model)
    return structures


def _chain_residues(chain):
    residues = list(getattr(chain, "existing_residues", []) or [])
    return [res for res in residues if _aa1(res) != "" and _ca_atom(res) is not None]


def _longest_protein_chain(model):
    best = None
    best_count = -1
    for chain in getattr(model, "chains", []) or []:
        residues = _chain_residues(chain)
        if len(residues) > best_count:
            best = chain
            best_count = len(residues)
    return best


def _best_aligned_chain(model, ref_residues, *, map_cutoff):
    best = None
    best_stats = None
    ref_points = [(_scene_xyz(_ca_atom(res)), idx) for idx, res in enumerate(ref_residues)]
    for chain in getattr(model, "chains", []) or []:
        residues = _chain_residues(chain)
        if not residues:
            continue
        distances = []
        close = 0
        for res in residues:
            ca = _ca_atom(res)
            point = _scene_xyz(ca)
            nearest = min((_dist(point, ref_point), idx) for ref_point, idx in ref_points)
            distances.append(nearest[0])
            if nearest[0] <= map_cutoff:
                close += 1
        if not distances:
            continue
        stats = {
            "close": close,
            "median": _median(distances),
            "mean": sum(distances) / len(distances),
            "count": len(residues),
        }
        key = (-stats["close"], stats["median"], -stats["count"], str(getattr(chain, "chain_id", "")))
        if best_stats is None or key < best_stats["key"]:
            best = chain
            best_stats = dict(stats)
            best_stats["key"] = key
    return best, best_stats


def _build_reference_indexed_alignment(
    ref_model,
    ref_chain,
    ref_residues,
    targets,
    metal_hits,
    *,
    core_cutoff,
    map_cutoff,
):
    row_defs = [
        {
            "label": _row_label(ref_model, ref_chain),
            "model": ref_model,
            "chain": ref_chain,
            "residue_by_column": {},
            "distance_by_column": {},
            "is_reference": True,
        }
    ]
    for target in targets:
        row_defs.append(
            {
                "label": _row_label(target["model"], target["chain"]),
                "model": target["model"],
                "chain": target["chain"],
                "residue_by_column": {},
                "distance_by_column": {},
                "is_reference": False,
            }
        )

    ref_points = [(_scene_xyz(_ca_atom(res)), idx) for idx, res in enumerate(ref_residues)]
    for idx, res in enumerate(ref_residues):
        row_defs[0]["residue_by_column"][idx] = res
        row_defs[0]["distance_by_column"][idx] = 0.0

    insertion_rows = []
    for row_index, target in enumerate(targets, start=1):
        assigned = {}
        for res in target["residues"]:
            point = _scene_xyz(_ca_atom(res))
            d, ref_idx = min((_dist(point, ref_point), idx) for ref_point, idx in ref_points)
            if d <= map_cutoff:
                current = assigned.get(ref_idx)
                if current is None or d < current[1]:
                    assigned[ref_idx] = (res, d)
        used_residues = set()
        for ref_idx, (res, d) in assigned.items():
            row_defs[row_index]["residue_by_column"][ref_idx] = res
            row_defs[row_index]["distance_by_column"][ref_idx] = d
            used_residues.add(id(res))
        unmapped = [res for res in target["residues"] if id(res) not in used_residues]
        insertion_rows.extend(_summarize_unmapped_segments(target, unmapped, ref_points))

    columns = []
    row_count = len(row_defs)
    for idx, ref_res in enumerate(ref_residues):
        residues = [row["residue_by_column"].get(idx) for row in row_defs]
        aas = [_aa1(res) if res is not None else "-" for res in residues]
        distances = [
            row["distance_by_column"].get(idx)
            for row in row_defs[1:]
            if row["distance_by_column"].get(idx) is not None
        ]
        coverage = sum(1 for res in residues if res is not None) / float(row_count)
        core = bool(distances) and coverage >= 0.70 and _median(distances) <= core_cutoff
        metal = any(_residue_key(res) in metal_hits for res in residues if res is not None)
        metal_keys = {_residue_key(res) for res in residues if res is not None and _residue_key(res) in metal_hits}
        columns.append(
            {
                "index": idx,
                "ref_residue": ref_res,
                "ref_number": _res_number(ref_res),
                "ss": _ss_char(ref_res),
                "core": core,
                "coverage": coverage,
                "median_distance": _median(distances) if distances else None,
                "metal": metal,
                "metal_keys": metal_keys,
                "consensus": _consensus_char(aas),
                "aas": aas,
            }
        )
    return row_defs, columns, insertion_rows


def _summarize_unmapped_segments(target, unmapped, ref_points):
    if not unmapped:
        return []
    chain = target["chain"]
    model = target["model"]
    ordered = sorted(unmapped, key=lambda res: _res_number(res))
    segments = []
    current = [ordered[0]]
    for res in ordered[1:]:
        if _res_number(res) == _res_number(current[-1]) + 1:
            current.append(res)
        else:
            segments.append(current)
            current = [res]
    segments.append(current)

    rows = []
    for segment in segments:
        min_pair = None
        for res in segment:
            point = _scene_xyz(_ca_atom(res))
            d, ref_idx = min((_dist(point, ref_point), idx) for ref_point, idx in ref_points)
            if min_pair is None or d < min_pair[0]:
                min_pair = (d, ref_idx)
        d, ref_idx = min_pair if min_pair is not None else (None, None)
        rows.append(
            {
                "structure": _model_name(model),
                "chain": getattr(chain, "chain_id", ""),
                "range": _range_label(segment),
                "length": len(segment),
                "nearest_reference": ref_idx + 1 if ref_idx is not None else "",
                "nearest_ca_distance": d,
                "sequence": "".join(_aa1(res) or "X" for res in segment),
            }
        )
    return rows


def _metal_near_residues(structures, direct_cutoff, candidate_cutoff):
    hits = set()
    for model in structures:
        metals = [
            atom
            for atom in getattr(model, "atoms", [])
            if str(getattr(getattr(atom, "element", None), "name", "")).upper() in METAL_ELEMENTS
            and _aa1(getattr(atom, "residue", None)) == ""
        ]
        if not metals:
            continue
        residues = [
            res
            for chain in getattr(model, "chains", []) or []
            for res in _chain_residues(chain)
        ]
        for metal in metals:
            metal_point = _coord_xyz(metal)
            for res in residues:
                candidate_names = SIDECHAIN_DONORS.get(str(getattr(res, "name", "")).upper(), set())
                for atom in getattr(res, "atoms", []):
                    element = str(getattr(getattr(atom, "element", None), "name", "")).upper()
                    if element not in {"O", "N", "S"}:
                        continue
                    name = str(getattr(atom, "name", "")).strip().upper()
                    distance = _dist(metal_point, _coord_xyz(atom))
                    if distance <= direct_cutoff or (name in candidate_names and distance <= candidate_cutoff):
                        hits.add(_residue_key(res))
                        break
    return hits


def _render_html_report(
    rows,
    columns,
    *,
    all_columns,
    insertion_rows,
    targets,
    report_mode,
    core_cutoff,
    map_cutoff,
    direct_metal_cutoff,
    candidate_cutoff,
):
    title = "Structural Alignment Report"
    summary_rows = []
    for target in targets:
        stats = target["stats"] or {}
        summary_rows.append(
            "<tr>"
            f"<td>{_esc(_model_name(target['model']))}</td>"
            f"<td>{_esc(getattr(target['chain'], 'chain_id', ''))}</td>"
            f"<td>{stats.get('close', '')}</td>"
            f"<td>{_fmt_float(stats.get('median'))}</td>"
            f"<td>{_fmt_float(stats.get('mean'))}</td>"
            f"<td>{stats.get('count', '')}</td>"
            "</tr>"
        )

    blocks = []
    width = 90
    for start in range(0, len(columns), width):
        block_columns = columns[start : start + width]
        blocks.append(_render_alignment_block(rows, block_columns, start))

    insertion_html = "".join(
        "<tr>"
        f"<td>{_esc(row['structure'])}</td>"
        f"<td>{_esc(row['chain'])}</td>"
        f"<td>{_esc(row['range'])}</td>"
        f"<td>{row['length']}</td>"
        f"<td>{row['nearest_reference']}</td>"
        f"<td>{_fmt_float(row['nearest_ca_distance'])}</td>"
        f"<td><code>{_esc(_shorten(row['sequence'], 80))}</code></td>"
        "</tr>"
        for row in insertion_rows
    )
    if not insertion_html:
        insertion_html = "<tr><td colspan='7'>No unmapped loop/insertion segments at the current cutoff.</td></tr>"

    core_count = sum(1 for col in all_columns if col["core"])
    metal_count = sum(1 for col in all_columns if col["metal"])
    uncertain_count = len(all_columns) - core_count
    default_core = "true" if str(report_mode).startswith("core") else "false"
    column_details = _column_details_json(rows, all_columns)
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
:root {{
  --msa-font: 13px;
  --label-width: 23ch;
  --paper-width: 1120px;
  --ink: #111827;
  --muted: #5b6472;
  --line: #d5dbe3;
  --paper: #ffffff;
  --bg: #f2f4f7;
  --accent: #145ea8;
  --metal: #0f8a43;
  --identical-bg: #c81e2b;
  --similar-bg: #f8d16c;
  --metal-bg: #d9f7e7;
  --loop-opacity: .34;
  --msa-line-height: 1.52;
}}
body {{
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}}
.page {{
  max-width: var(--paper-width);
  margin: 0 auto;
  padding: 24px 28px 40px;
}}
h1 {{ margin: 0 0 8px; font-size: 27px; letter-spacing: 0; }}
h2 {{ margin: 26px 0 10px; font-size: 18px; }}
.meta {{ color: #4b5563; margin-bottom: 14px; }}
.pill {{
  display: inline-block; padding: 3px 9px; border-radius: 999px;
  background: #e5e7eb; margin-right: 5px; font-size: 12px; font-weight: 700;
}}
.controls {{
  position: sticky;
  top: 0;
  z-index: 20;
  background: rgba(242, 244, 247, .94);
  backdrop-filter: blur(10px);
  border-bottom: 1px solid var(--line);
}}
.controls-inner {{
  max-width: var(--paper-width);
  margin: 0 auto;
  padding: 10px 28px;
  display: flex;
  flex-wrap: wrap;
  gap: 8px 14px;
  align-items: center;
}}
.control {{
  display: inline-flex;
  gap: 6px;
  align-items: center;
  font-size: 13px;
  font-weight: 650;
  color: #1f2937;
}}
.control input[type="range"] {{ width: 118px; }}
.control input[type="color"] {{
  width: 28px;
  height: 24px;
  padding: 0;
  border: 1px solid #aeb7c2;
  border-radius: 5px;
  background: white;
}}
button {{
  border: 1px solid #b9c2cd;
  background: white;
  color: #111827;
  border-radius: 7px;
  padding: 5px 10px;
  font-weight: 700;
  cursor: pointer;
}}
button:hover {{ background: #f8fafc; }}
.panel {{
  background: var(--paper); border: 1px solid var(--line); border-radius: 7px;
  padding: 14px; margin: 12px 0; box-shadow: 0 1px 2px rgba(15, 23, 42, .05);
}}
.caption {{
  font-size: 13px;
  line-height: 1.45;
  color: #374151;
}}
pre.aln {{
  margin: 0;
  overflow-x: auto;
  font-family: Menlo, Consolas, "SFMono-Regular", monospace;
  font-size: var(--msa-font);
  line-height: var(--msa-line-height);
  white-space: pre;
}}
.label {{
  color: #273242;
  font-weight: 800;
  display: inline-block;
  width: var(--label-width);
  user-select: none;
}}
.colcell {{
  display: inline-block;
  min-width: 1ch;
  text-align: center;
  border-radius: 2px;
  cursor: crosshair;
}}
.seqcell {{
  font-weight: 700;
  color: #111827;
}}
.gap {{ color: #c7ced8; font-weight: 500; }}
.ann {{ color: #475569; }}
.ann.ssline {{ color: #6d28d9; font-weight: 700; }}
.ann.coreline {{ color: #145ea8; font-weight: 800; }}
.ann.metalline {{ color: var(--metal); font-weight: 900; }}
.ann.consline {{ color: #111827; font-weight: 800; }}
.seqcell.identical {{ background: var(--identical-bg); color: #fff; }}
.seqcell.similar {{ background: var(--similar-bg); color: #111827; }}
.aa-acidic {{ background: #f4b4b4; }}
.aa-basic {{ background: #b9d3ff; }}
.aa-amide, .aa-hydroxyl {{ background: #cce8c8; }}
.aa-hydrophobic {{ background: #e6e2d3; }}
.aa-aromatic {{ background: #dfcff2; }}
.aa-sulfur {{ background: #ead394; }}
.aa-glycine {{ background: #e5e7eb; }}
.aa-proline {{ background: #d7dce2; }}
.seqcell.res-metal {{ box-shadow: inset 0 -3px 0 var(--metal); }}
.seqcell.col-metal:not(.res-metal) {{ box-shadow: inset 0 -1px 0 rgba(15, 138, 67, .35); }}
.show-metal .metalline.col-metal {{ background: var(--metal-bg); color: #086735; }}
.show-metal .seqcell.col-metal {{ outline: 1px solid #0f8a43; outline-offset: -1px; }}
.dim-loops .col-loop {{ opacity: var(--loop-opacity); }}
.hide-noncore .col-loop {{ display: none; }}
.no-conservation .seqcell.identical,
.no-conservation .seqcell.similar {{
  background: transparent;
  color: #111827;
}}
.no-aa-color .seqcell:not(.identical):not(.similar) {{ background: transparent; }}
.hide-metal .seqcell.res-metal,
.hide-metal .seqcell.col-metal {{ box-shadow: none; outline: none; }}
.hide-metal .metalline {{ visibility: hidden; }}
.selected-col {{ outline: 2px solid #111827 !important; outline-offset: -1px; }}
.legend {{
  display: flex;
  flex-wrap: wrap;
  gap: 8px 12px;
  font-size: 12px;
  color: #374151;
}}
.swatch {{
  display: inline-block;
  width: 18px;
  height: 12px;
  border: 1px solid #aeb7c2;
  vertical-align: -2px;
  margin-right: 4px;
}}
table {{ border-collapse: collapse; width: 100%; background: white; }}
th, td {{ border: 1px solid #d7dce2; padding: 6px 8px; text-align: left; vertical-align: top; }}
th {{ background: #eef2f7; }}
code {{ font-family: Menlo, Consolas, "SFMono-Regular", monospace; }}
#detailBox {{
  min-height: 42px;
  color: #1f2937;
  font-size: 13px;
  line-height: 1.45;
}}
@media print {{
  body {{ background: white; }}
  .controls {{ display: none; }}
  .page {{ max-width: none; padding: 0; }}
  .panel {{ box-shadow: none; break-inside: avoid; }}
  pre.aln {{ font-size: 10.5px; }}
}}
</style>
</head>
<body class="show-metal dim-loops">
<div class="controls">
  <div class="controls-inner">
    <label class="control"><input id="coreOnly" type="checkbox"> core only</label>
    <label class="control"><input id="dimLoops" type="checkbox" checked> dim loops</label>
    <label class="control"><input id="showConservation" type="checkbox" checked> conservation color</label>
    <label class="control"><input id="showAAColor" type="checkbox" checked> residue class color</label>
    <label class="control"><input id="showMetal" type="checkbox" checked> metal marks</label>
    <label class="control">font <input id="fontSize" type="range" min="9" max="18" value="13"></label>
    <label class="control">label <input id="labelWidth" type="range" min="14" max="34" value="23"></label>
    <label class="control">line <input id="lineHeight" type="range" min="120" max="190" value="152"></label>
    <label class="control">loop opacity <input id="loopOpacity" type="range" min="10" max="90" value="34"></label>
    <label class="control">same <input id="sameColor" type="color" value="#c81e2b"></label>
    <label class="control">similar <input id="similarColor" type="color" value="#f8d16c"></label>
    <label class="control">metal <input id="metalColor" type="color" value="#0f8a43"></label>
    <button id="publicationPreset" type="button">publication preset</button>
    <button id="printButton" type="button">print / save PDF</button>
  </div>
</div>
<div class="page">
<h1>{title}</h1>
<div class="meta">
  <span class="pill">mode: {_esc(report_mode)}</span>
  <span class="pill">columns: {len(columns)}</span>
  <span class="pill">core: {core_count}</span>
  <span class="pill">loop/uncertain: {uncertain_count}</span>
  <span class="pill">metal-near: {metal_count}</span>
</div>
<div class="panel caption">
Reference-indexed structural MSA from the current 3D superposition. Core calls use median C-alpha distance <= {core_cutoff:.1f} A and coverage >= 70%.
Metal-near calls use direct O/N/S <= {direct_metal_cutoff:.1f} A or D/E/H/N/Q/S/T/Y/C/M side-chain donor <= {candidate_cutoff:.1f} A.
Click any alignment column to inspect residues and distances.
</div>
<div class="panel legend">
  <span><span class="swatch" style="background:#c81e2b"></span>same as reference</span>
  <span><span class="swatch" style="background:#f8d16c"></span>same residue class</span>
  <span><span class="swatch" style="background:#d9f7e7"></span>metal-near column</span>
  <span><span class="swatch" style="background:white; box-shadow:inset 0 -3px 0 #0f8a43"></span>direct metal-near residue</span>
  <span><b>C</b> core column</span>
  <span><b>.</b> loop/uncertain column</span>
</div>
<h2>Structural MSA</h2>
{''.join(blocks)}
<h2>Column Inspector</h2>
<div id="detailBox" class="panel">Click an alignment column to inspect residue numbers, conservation, metal-near status, and C-alpha distances.</div>
<h2>Alignment Summary</h2>
<table>
<tr><th>Structure</th><th>Chain</th><th>Residues within {map_cutoff:.1f} A</th><th>Median C-alpha A</th><th>Mean C-alpha A</th><th>Residues</th></tr>
{''.join(summary_rows)}
</table>
<h2>Loop / Insertion Audit</h2>
<table>
<tr><th>Structure</th><th>Chain</th><th>Unmapped range</th><th>Length</th><th>Nearest reference column</th><th>Nearest C-alpha A</th><th>Sequence</th></tr>
{insertion_html}
</table>
</div>
<script>
const COLUMN_DETAILS = {column_details};
const DEFAULT_CORE = {default_core};

function setBodyClass(name, enabled) {{
  document.body.classList.toggle(name, !!enabled);
}}

function applyControls() {{
  setBodyClass('hide-noncore', document.getElementById('coreOnly').checked);
  setBodyClass('dim-loops', document.getElementById('dimLoops').checked);
  setBodyClass('no-conservation', !document.getElementById('showConservation').checked);
  setBodyClass('no-aa-color', !document.getElementById('showAAColor').checked);
  setBodyClass('hide-metal', !document.getElementById('showMetal').checked);
  document.documentElement.style.setProperty('--msa-font', document.getElementById('fontSize').value + 'px');
  document.documentElement.style.setProperty('--label-width', document.getElementById('labelWidth').value + 'ch');
  document.documentElement.style.setProperty('--msa-line-height', String(Number(document.getElementById('lineHeight').value) / 100));
  document.documentElement.style.setProperty('--loop-opacity', String(Number(document.getElementById('loopOpacity').value) / 100));
  document.documentElement.style.setProperty('--identical-bg', document.getElementById('sameColor').value);
  document.documentElement.style.setProperty('--similar-bg', document.getElementById('similarColor').value);
  document.documentElement.style.setProperty('--metal', document.getElementById('metalColor').value);
}}

function selectColumn(col) {{
  document.querySelectorAll('.selected-col').forEach(el => el.classList.remove('selected-col'));
  document.querySelectorAll('[data-col="' + col + '"]').forEach(el => el.classList.add('selected-col'));
  const detail = COLUMN_DETAILS[String(col)];
  const box = document.getElementById('detailBox');
  if (!detail) {{
    box.textContent = 'No details for column ' + col + '.';
    return;
  }}
  const residues = detail.residues.map(r => {{
    const dist = r.distance === null ? '' : ' / d=' + r.distance.toFixed(2) + ' A';
    const metal = r.metal ? ' / metal-near' : '';
    return '<li><b>' + escapeHtml(r.label) + '</b>: ' + escapeHtml(r.residue) + dist + metal + '</li>';
  }}).join('');
  box.innerHTML =
    '<b>Reference column ' + detail.ref_column + '</b> | ' +
    escapeHtml(detail.ref_residue) + ' | SS=' + escapeHtml(detail.ss) +
    ' | core=' + (detail.core ? 'yes' : 'no') +
    ' | metal=' + (detail.metal ? 'yes' : 'no') +
    ' | coverage=' + Math.round(detail.coverage * 100) + '%' +
    ' | median C-alpha=' + (detail.median_distance === null ? 'n/a' : detail.median_distance.toFixed(2) + ' A') +
    '<ul>' + residues + '</ul>';
}}

function escapeHtml(value) {{
  return String(value).replace(/[&<>"']/g, ch => ({{
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;'
  }}[ch]));
}}

document.querySelectorAll('input').forEach(el => el.addEventListener('input', applyControls));
document.querySelectorAll('.colcell').forEach(el => {{
  el.addEventListener('click', () => selectColumn(el.dataset.col));
}});
document.getElementById('publicationPreset').addEventListener('click', () => {{
  document.getElementById('coreOnly').checked = false;
  document.getElementById('dimLoops').checked = true;
  document.getElementById('showConservation').checked = true;
  document.getElementById('showAAColor').checked = true;
  document.getElementById('showMetal').checked = true;
  document.getElementById('fontSize').value = 13;
  document.getElementById('labelWidth').value = 23;
  document.getElementById('lineHeight').value = 152;
  document.getElementById('loopOpacity').value = 34;
  document.getElementById('sameColor').value = '#c81e2b';
  document.getElementById('similarColor').value = '#f8d16c';
  document.getElementById('metalColor').value = '#0f8a43';
  applyControls();
}});
document.getElementById('printButton').addEventListener('click', () => window.print());
document.getElementById('coreOnly').checked = DEFAULT_CORE;
applyControls();
</script>
</body>
</html>
"""


def _render_alignment_block(rows, columns, start_index):
    ss_line = "".join(_annotation_span(col, col["ss"], "ssline") for col in columns)
    core_line = "".join(_annotation_span(col, "C" if col["core"] else ".", "coreline") for col in columns)
    metal_line = "".join(_annotation_span(col, "^" if col["metal"] else " ", "metalline") for col in columns)
    consensus_line = "".join(_annotation_span(col, col["consensus"], "consline") for col in columns)
    number_line = "".join(_annotation_span(col, _number_char(col), "numline") for col in columns)
    lines = [
        f"<span class='label'>ref#</span>{number_line}",
        f"<span class='label'>SS</span>{ss_line}",
        f"<span class='label'>core</span>{core_line}",
        f"<span class='label'>metal</span>{metal_line}",
        f"<span class='label'>consensus</span>{consensus_line}",
    ]
    for row_index, row in enumerate(rows):
        chars = []
        for col in columns:
            res = row["residue_by_column"].get(col["index"])
            aa = _aa1(res) if res is not None else "-"
            classes = ["seqcell"]
            if aa == "-":
                classes.append("gap")
            elif row_index > 0 and col["aas"][0] == aa and aa != "-":
                classes.append("identical")
            elif row_index > 0 and _same_class(col["aas"][0], aa):
                classes.append("similar")
            if aa != "-":
                classes.append(f"aa-{AA_CLASS.get(aa, 'other')}")
            if not col["core"]:
                classes.append("col-loop")
            else:
                classes.append("col-core")
            residue_is_metal = res is not None and _residue_key(res) in col.get("metal_keys", set())
            if col["metal"]:
                classes.append("col-metal")
                if residue_is_metal:
                    classes.append("res-metal")
            else:
                classes.append("col-nometal")
            title = _cell_title(col, row, res)
            chars.append(
                f"<span class='colcell {' '.join(classes)}' data-col='{col['index']}' title='{_esc(title)}'>{_esc(aa)}</span>"
            )
        lines.append(f"<span class='label'>{_esc(_short_label(row['label']))}</span>{''.join(chars)}")
    return f"<div class='panel'><pre class='aln'>{chr(10).join(lines)}</pre></div>"


def _annotation_span(col, char, kind):
    classes = ["colcell", "ann", kind, "col-core" if col["core"] else "col-loop"]
    classes.append("col-metal" if col["metal"] else "col-nometal")
    title = _column_title(col)
    return f"<span class='{' '.join(classes)}' data-col='{col['index']}' title='{_esc(title)}'>{_esc(char)}</span>"


def _number_char(col):
    number = col["ref_number"]
    if number % 10 == 0:
        return str(number)[-1]
    if number % 10 == 1:
        return "|"
    return " "


def _cell_title(col, row, residue):
    bits = [_column_title(col), _short_label(row["label"])]
    if residue is None:
        bits.append("gap")
    else:
        bits.append(_residue_label(residue))
        distance = row["distance_by_column"].get(col["index"])
        if distance is not None:
            bits.append(f"C-alpha distance {distance:.2f} A")
    return " | ".join(bits)


def _column_title(col):
    distance = col["median_distance"]
    distance_text = "n/a" if distance is None else f"{distance:.2f} A"
    return (
        f"reference {col['ref_number']} | SS {col['ss']} | "
        f"core {'yes' if col['core'] else 'no'} | "
        f"metal {'yes' if col['metal'] else 'no'} | "
        f"coverage {col['coverage']:.0%} | median {distance_text}"
    )


def _column_details_json(rows, columns):
    details = {}
    for col in columns:
        residues = []
        for row in rows:
            res = row["residue_by_column"].get(col["index"])
            distance = row["distance_by_column"].get(col["index"])
            residues.append(
                {
                    "label": _short_label(row["label"]),
                    "residue": _residue_label(res) if res is not None else "-",
                    "aa": _aa1(res) if res is not None else "-",
                    "distance": None if distance is None else round(float(distance), 3),
                    "metal": bool(res is not None and _residue_key(res) in col.get("metal_keys", set())),
                }
            )
        details[str(col["index"])] = {
            "ref_column": col["index"] + 1,
            "ref_residue": _residue_label(col["ref_residue"]),
            "ss": col["ss"],
            "core": bool(col["core"]),
            "metal": bool(col["metal"]),
            "coverage": round(float(col["coverage"]), 3),
            "median_distance": (
                None if col["median_distance"] is None else round(float(col["median_distance"]), 3)
            ),
            "consensus": col["consensus"],
            "residues": residues,
        }
    return json.dumps(details, ensure_ascii=True, separators=(",", ":"))


def _residue_label(residue):
    if residue is None:
        return "-"
    chain = str(getattr(residue, "chain_id", "") or "")
    aa = _aa1(residue) or "X"
    return f"{chain}:{aa}{_res_number(residue)}"


def _aa1(residue):
    if residue is None:
        return ""
    return AA3_TO_1.get(str(getattr(residue, "name", "")).upper(), "")


def _ca_atom(residue):
    if residue is None:
        return None
    for atom in getattr(residue, "atoms", []):
        if str(getattr(atom, "name", "")).strip().upper() == "CA":
            element = str(getattr(getattr(atom, "element", None), "name", "")).upper()
            if element == "C":
                return atom
    return None


def _scene_xyz(atom):
    coord = atom.scene_coord
    return float(coord[0]), float(coord[1]), float(coord[2])


def _coord_xyz(atom):
    coord = atom.coord
    return float(coord[0]), float(coord[1]), float(coord[2])


def _dist(a, b):
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def _median(values):
    values = sorted(float(v) for v in values if v is not None)
    if not values:
        return None
    mid = len(values) // 2
    if len(values) % 2:
        return values[mid]
    return (values[mid - 1] + values[mid]) / 2.0


def _res_number(residue):
    try:
        return int(getattr(residue, "number", 0))
    except Exception:
        return 0


def _residue_key(residue):
    structure = getattr(residue, "structure", None)
    return (
        str(getattr(structure, "id_string", "") or ""),
        str(getattr(residue, "chain_id", "") or ""),
        _res_number(residue),
        str(getattr(residue, "name", "") or ""),
    )


def _ss_char(residue):
    value = str(getattr(residue, "ss_type", "") or "").lower()
    if "helix" in value or value in {"1", "h"}:
        return "H"
    if "strand" in value or "sheet" in value or value in {"2", "e"}:
        return "E"
    # ChimeraX also exposes booleans on many builds.
    try:
        if bool(getattr(residue, "is_helix", False)):
            return "H"
    except Exception:
        pass
    try:
        if bool(getattr(residue, "is_strand", False)):
            return "E"
    except Exception:
        pass
    return "."


def _consensus_char(aas):
    present = [aa for aa in aas if aa and aa != "-"]
    if len(present) < 2:
        return " "
    if len(set(present)) == 1:
        return "*"
    classes = {AA_CLASS.get(aa) for aa in present}
    classes.discard(None)
    return ":" if len(classes) == 1 else "."


def _same_class(a, b):
    if a in ("", "-") or b in ("", "-"):
        return False
    return AA_CLASS.get(a) is not None and AA_CLASS.get(a) == AA_CLASS.get(b)


def _model_name(model):
    return str(getattr(model, "name", "") or f"model_{getattr(model, 'id_string', '?')}")


def _row_label(model, chain):
    return f"#{getattr(model, 'id_string', '?')}/{getattr(chain, 'chain_id', '?')} {_model_name(model)}"


def _short_label(label):
    label = str(label)
    label = re.sub(r"\.pdb\b", "", label, flags=re.I)
    return label[:18]


def _range_label(segment):
    if not segment:
        return ""
    start = segment[0]
    end = segment[-1]
    chain = str(getattr(start, "chain_id", "") or "")
    if start is end:
        return f"{chain}:{_aa1(start)}{_res_number(start)}"
    return f"{chain}:{_aa1(start)}{_res_number(start)}-{_aa1(end)}{_res_number(end)}"


def _shorten(text, max_len):
    text = str(text or "")
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "..."


def _fmt_float(value):
    if value is None or value == "":
        return ""
    try:
        return f"{float(value):.2f}"
    except Exception:
        return str(value)


def _esc(text):
    return html.escape(str(text), quote=True)


def _default_output_path(ref_model):
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", _model_name(ref_model)).strip("_") or "structure"
    root = Path.home() / "Desktop"
    if not root.exists():
        root = Path(tempfile.gettempdir())
    return root / f"structural_alignment_{safe}.html"
