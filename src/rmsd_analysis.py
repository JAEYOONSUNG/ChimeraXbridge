"""Reference-vs-targets RMSD table generator.

Wraps ChimeraX's MatchMaker (chimerax.match_maker.match.cmd_match) so the
caller gets two RMSD numbers per pair without having to scrape the log:

  - pruned RMSD (the iterative-cutoff core fit, "final RMSD" in MatchMaker)
  - native RMSD over every sequence-aligned atom pair before pruning ("full RMSD")

Two operating modes:

  - realign=True  -> MatchMaker is allowed to rotate/translate each target onto
                     the reference (the standard MatchMaker behaviour); the
                     reported RMSDs reflect the optimal rigid-body fit.
  - realign=False -> target scene_positions are saved before MatchMaker runs
                     (so we still get the sequence-aligned atom pairings) and
                     restored afterwards. RMSDs are then recomputed from the
                     *current* coordinates over the same atom pairings, so the
                     numbers reflect how close the user's existing alignment is
                     rather than the geometrically optimal alignment.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass
class RmsdRow:
    ref_name: str
    ref_spec: str
    target_name: str
    target_spec: str
    pruned_rmsd: float
    pruned_n: int
    native_rmsd: float
    native_n: int
    note: str = ""


def _structures_from_spec(session, spec):
    """Resolve a #N spec string to the matching AtomicStructure objects."""
    from chimerax.atomic import AtomicStructure

    target_id = str(spec or "").strip().lstrip("#")
    if not target_id:
        return []
    out = []
    for model in session.models.list(type=AtomicStructure):
        if str(getattr(model, "id_string", "")) == target_id:
            out.append(model)
    return out


def _atoms_from_structures(structures):
    from chimerax.atomic import Atoms

    if not structures:
        return None
    arrays = [s.atoms for s in structures]
    if len(arrays) == 1:
        return arrays[0]
    return Atoms.concatenate(arrays)


def _rmsd_numpy(coords_a, coords_b):
    import numpy as np

    if len(coords_a) == 0 or len(coords_a) != len(coords_b):
        return float("nan")
    diff = coords_a - coords_b
    return float(np.sqrt(np.mean(np.sum(diff * diff, axis=1))))


def compute_rmsd_table(
    session,
    ref_spec: str,
    target_specs: List[str],
    *,
    realign: bool = True,
    cutoff: float = 2.0,
) -> List[RmsdRow]:
    """Compute (pruned, native) RMSDs of each target against the reference.

    Returns one RmsdRow per chain-pair that MatchMaker finds. ``target_specs``
    is a list of model specs (e.g. ``["#2", "#3", "#4"]``). ``ref_spec`` is
    the reference model spec (e.g. ``"#1"``). ``cutoff`` is the iterative
    pruning cutoff in Angstroms; pairs farther than this are dropped from
    the pruned RMSD.
    """
    from chimerax.match_maker.match import cmd_match

    ref_structures = _structures_from_spec(session, ref_spec)
    if not ref_structures:
        raise ValueError(f"No model matches reference spec {ref_spec!r}")
    ref_name = (ref_structures[0].name or ref_spec).strip()
    ref_atoms = _atoms_from_structures(ref_structures)
    if ref_atoms is None or len(ref_atoms) == 0:
        raise ValueError(f"Reference {ref_spec} has no atoms")

    rows: List[RmsdRow] = []
    for tspec in target_specs:
        try:
            target_structures = _structures_from_spec(session, tspec)
            if not target_structures:
                rows.append(RmsdRow(
                    ref_name=ref_name, ref_spec=ref_spec,
                    target_name=tspec, target_spec=tspec,
                    pruned_rmsd=float("nan"), pruned_n=0,
                    native_rmsd=float("nan"), native_n=0,
                    note="no model matches spec",
                ))
                continue
            target_name = (target_structures[0].name or tspec).strip()
            target_atoms = _atoms_from_structures(target_structures)
            if target_atoms is None or len(target_atoms) == 0:
                rows.append(RmsdRow(
                    ref_name=ref_name, ref_spec=ref_spec,
                    target_name=target_name, target_spec=tspec,
                    pruned_rmsd=float("nan"), pruned_n=0,
                    native_rmsd=float("nan"), native_n=0,
                    note="target has no atoms",
                ))
                continue

            # Preserve scene_position for "compute on current coords" mode.
            saved_positions = None
            if not realign:
                saved_positions = {s: s.scene_position for s in target_structures}

            results = cmd_match(
                session,
                target_atoms,
                to=ref_atoms,
                cutoff_distance=cutoff,
                verbose=None,
                log_parameters=False,
            )

            if not realign and saved_positions:
                for s, pos in saved_positions.items():
                    try:
                        s.scene_position = pos
                    except Exception:
                        pass

            if not results:
                rows.append(RmsdRow(
                    ref_name=ref_name, ref_spec=ref_spec,
                    target_name=target_name, target_spec=tspec,
                    pruned_rmsd=float("nan"), pruned_n=0,
                    native_rmsd=float("nan"), native_n=0,
                    note="MatchMaker returned no chain pairings",
                ))
                continue

            for r in results:
                full_match = r["full match atoms"]
                full_ref = r["full ref atoms"]
                final_match = r["final match atoms"]
                final_ref = r["final ref atoms"]
                if realign:
                    # MatchMaker rotated the target onto the reference;
                    # the dict-returned RMSDs are the post-fit numbers.
                    pruned = float(r["final RMSD"])
                    native = float(r["full RMSD"])
                else:
                    # We restored the target's pre-call position. Recompute
                    # RMSD on the current coordinates over the same atom
                    # pairings MatchMaker chose.
                    native = _rmsd_numpy(full_match.scene_coords, full_ref.scene_coords)
                    pruned = _rmsd_numpy(final_match.scene_coords, final_ref.scene_coords)

                ref_seq = r["aligned ref seq"].name
                match_seq = r["aligned match seq"].name
                rows.append(RmsdRow(
                    ref_name=f"{ref_name} {ref_seq}",
                    ref_spec=ref_spec,
                    target_name=f"{target_name} {match_seq}",
                    target_spec=tspec,
                    pruned_rmsd=pruned,
                    pruned_n=len(final_match),
                    native_rmsd=native,
                    native_n=len(full_match),
                ))
        except Exception as err:
            rows.append(RmsdRow(
                ref_name=ref_name, ref_spec=ref_spec,
                target_name=tspec, target_spec=tspec,
                pruned_rmsd=float("nan"), pruned_n=0,
                native_rmsd=float("nan"), native_n=0,
                note=f"error: {err}",
            ))
    return rows


def format_rmsd_table(rows: List[RmsdRow]) -> str:
    """Render the RMSD rows as a fixed-width log table."""
    if not rows:
        return "(no rows)"
    headers = ("Target", "Pruned RMSD (Å)", "N pruned", "Native RMSD (Å)", "N total", "Note")
    body = []
    for row in rows:
        body.append((
            row.target_name,
            "n/a" if row.pruned_rmsd != row.pruned_rmsd else f"{row.pruned_rmsd:.3f}",
            str(row.pruned_n),
            "n/a" if row.native_rmsd != row.native_rmsd else f"{row.native_rmsd:.3f}",
            str(row.native_n),
            row.note,
        ))
    widths = [max(len(h), max(len(r[i]) for r in body)) for i, h in enumerate(headers)]
    sep = "  "
    lines = [sep.join(h.ljust(widths[i]) for i, h in enumerate(headers))]
    lines.append(sep.join("-" * widths[i] for i in range(len(headers))))
    for r in body:
        lines.append(sep.join(c.ljust(widths[i]) for i, c in enumerate(r)))
    return "\n".join(lines)
