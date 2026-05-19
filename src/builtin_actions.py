from datetime import datetime
from pathlib import Path
import re
import shlex
import shutil
import tempfile
import webbrowser

from .display_color import maybe_apply_stick_context_colors_for_command, restore_charge_colors
from .nl_intent import normalized_prompt_for_matching

ANALYSIS_HANDLER_NAMES = ("_workflow_intent_fastpath", "_membrane_fastpath", "_pisa_fastpath", "_analysis_fastpath", "_annotation_fastpath", "_motif_fastpath", "_catalytic_fastpath", "_metal_fastpath", "_ligand_fastpath")
VISUAL_HANDLER_NAMES = (
    "_workflow_intent_fastpath",
    "_membrane_fastpath",
    "_scene_fastpath",
    "_builtin_tool_fastpath",
    "_manipulation_fastpath",
    "_appearance_fastpath",
    "_figure_control_fastpath",
    "_residue_visual_fastpath",
    "_site_fastpath",
    "_motif_visual_fastpath",
    "_interface_visual_fastpath",
    "_explode_visual_fastpath",
    "_selection_visual_fastpath",
    "_domains_visual_fastpath",
    "_roles_visual_fastpath",
    "_conservation_fastpath",
    "_publication_fastpath",
    "_transparency_fastpath",
    "_background_fastpath",
    "_style_fastpath",
    "_color_fastpath",
    "_focus_fastpath",
    "_snapshot_fastpath",
)
COMBINED_HANDLER_NAMES = ANALYSIS_HANDLER_NAMES + ("_align_fastpath",) + VISUAL_HANDLER_NAMES

DOMAIN_COLOR_FAMILIES = (
    ("#a8dadc", "#8fd0d3", "#77c5ca"),
    ("#f7c6b8", "#efb5a4", "#e5a492"),
    ("#cdb4db", "#bea5d4", "#af96cd"),
    ("#cde7be", "#bddcab", "#accf98"),
    ("#f9e2ae", "#f3d596", "#ecc87d"),
    ("#bde0fe", "#a8d3f7", "#93c5ef"),
)

ROLE_COLORS = {
    "active": "#8fd3c1",
    "scaffold": "#c7b6e5",
    "peripheral": "#e8c1a9",
}

PUBLICATION_CARTOON_WIDTH = 1.5
PUBLICATION_CARTOON_THICK = 0.3
PUBLICATION_SILHOUETTE_WIDTH = 1.6
PUBLICATION_SILHOUETTE_COLOR = "#5f6670"
PUBLICATION_SILHOUETTE_DEPTH_JUMP = 0.02
PUBLICATION_STICK_RADIUS = 0.24
BEAUTIFY_TOKENS = (
    "pretty",
    "beautiful",
    "beautify",
    "clean",
    "cleaner",
    "polish",
    "polished",
    "nice",
    "예쁘",
    "이쁘",
    "깔끔",
    "보기좋",
    "보기 좋",
    "멋있",
    "정돈",
)


def try_builtin_fastpath(session, prompt, progress=None, executor=None):
    compound_result = _try_compound_fastpath(session, prompt, progress, executor, COMBINED_HANDLER_NAMES)
    if compound_result is not None:
        return compound_result

    return _try_handlers(session, prompt, progress, executor, COMBINED_HANDLER_NAMES)


def try_analysis_fastpath(session, prompt, progress=None, executor=None):
    compound_result = _try_compound_fastpath(session, prompt, progress, executor, ANALYSIS_HANDLER_NAMES)
    if compound_result is not None:
        return compound_result
    return _try_handlers(session, prompt, progress, executor, ANALYSIS_HANDLER_NAMES)


def try_visual_fastpath(session, prompt, progress=None, executor=None):
    compound_result = _try_compound_fastpath(session, prompt, progress, executor, VISUAL_HANDLER_NAMES)
    if compound_result is not None:
        return compound_result
    return _try_handlers(session, prompt, progress, executor, VISUAL_HANDLER_NAMES)


def run_analysis_visual_companion(session, prompt, progress=None, executor=None):
    """Apply the 3D companion view expected for analysis-style requests.

    The AI panel's analyze route often asks report-format functions to identify
    candidates. For structure work, a candidate list is not enough; the primary
    candidate should also become visible in the current 3D scene unless the user
    explicitly asked for text only.
    """
    lowered = str(prompt or "").lower()
    if any(
        token in lowered
        for token in ("text only", "설명만", "말로만", "요약만", "command 없이", "시각화 없이", "no visualization")
    ):
        return None

    def emit(message):
        _emit(progress, message)

    selected = any(word in lowered for word in ("selected", "selection", "현재 선택", "선택"))
    if selected:
        emit("Local visual companion: selection-focused view")
        if any(word in lowered for word in ("interface", "접촉", "인터페이스")):
            return _run_figure(session, "selection-interface", executor=executor)
        if any(word in lowered for word in ("motif", "모티프", "패턴")):
            return _run_figure(session, "selection-motif", executor=executor)
        if any(word in lowered for word in ("pocket", "ligand", "metal", "active site", "active-site", "활성부위", "촉매", "리간드", "금속")):
            return _run_figure(session, "selection-pocket", executor=executor)
        return _run_figure(session, "selection", executor=executor)

    if any(word in lowered for word in ("conservation", "consurf", "conserved", "보존", "보존성", "보존도")):
        from .conservation import apply_conservation_view

        emit("Local visual companion: conservation view")
        return apply_conservation_view(session, model_hint=prompt, query_text=prompt, executor=executor)

    if any(
        word in lowered
        for word in ("membrane", "bilayer", "transmembrane", "lipid", "막", "멤브레인", "지질막", "막단백")
    ):
        from .membrane import run_membrane_view

        emit("Local visual companion: membrane slab")
        return run_membrane_view(session, executor=executor)

    if any(
        word in lowered
        for word in ("pisa", "pdbe-pisa", "buried surface", "buried area", "bsa", "접촉면", "계면", "매몰 면적")
    ):
        from .pisa import run_pisa_view

        emit("Local visual companion: PISA-like interface view")
        return run_pisa_view(session, "view", executor=executor)

    if any(word in lowered for word in ("interface", "oligomer", "contact surface", "인터페이스", "접촉", "올리고머")):
        emit("Local visual companion: interface view")
        return _run_interface_view(session, executor=executor)

    if any(word in lowered for word in ("domain", "domains", "chunk", "architecture", "도메인", "구조 구획")):
        emit("Local visual companion: domain view")
        return _run_domains_view(session, None, executor=executor)

    if any(word in lowered for word in ("role", "roles", "scaffold", "assembly role", "복합체 역할", "스캐폴드")):
        emit("Local visual companion: role view")
        return _run_roles_view(session, None, executor=executor)

    if any(word in lowered for word in ("motif", "모티프", "패턴", "sequence motif", "서열 모티프")):
        emit("Local visual companion: motif view")
        model_hint, motif_text = _parse_motif_args(prompt)
        return _run_motif_view(session, motif_text=motif_text, model_hint=model_hint, executor=executor)

    if any(word in lowered for word in ("catalytic", "active residue", "active site", "active-site", "촉매", "활성부위", "활성 잔기")):
        emit("Local visual companion: catalytic-candidate view")
        return _run_catalytic_view(session, None, executor=executor, preserve_existing=True)

    if any(word in lowered for word in ("ligand", "substrate", "pocket", "binding site", "리간드", "포켓", "결합부위")):
        emit("Local visual companion: KVFinder pocket overlay")
        pocket_overlay = _run_kvfinder_pocket_overlay(session, executor=executor)
        if pocket_overlay:
            return pocket_overlay
        emit("Local visual companion: ligand/pocket residue overlay")
        overlay = _run_site_overlay(session, "ligand", executor=executor)
        if overlay:
            return "\n".join(["Ligand/pocket companion overlay applied.", overlay])
        return _run_figure(session, "pocket", executor=executor)

    if any(word in lowered for word in ("metal", "zn", "mg", "mn", "fe", "cofactor", "금속")):
        explicit_place_words = any(
            word in lowered
            for word in (
                "place", "put", "insert", "add", "optimize", "optimise", "refine",
                "삽입", "넣", "박아", "주입", "배치", "추가", "최적화",
            )
        )
        review_words = any(
            word in lowered
            for word in ("position", "candidate", "predict", "preview", "review", "위치", "후보", "예측", "검토")
        )
        if explicit_place_words or review_words:
            from .metal_placement import run_metal_placement_pipeline

            emit(
                "Local visual companion: predicted metal marker"
                if explicit_place_words else "Local visual companion: predicted metal candidate preview"
            )
            return run_metal_placement_pipeline(
                session,
                model_hint=prompt,
                top_n=1 if explicit_place_words else 5,
                show_all=False if explicit_place_words else True,
                clear_existing=True,
                use_kvfinder=True,
                place=explicit_place_words,
                preview=not explicit_place_words,
                executor=executor,
            )
        emit("Local visual companion: existing metal-site overlay")
        overlay = _run_site_overlay(session, "metal", executor=executor)
        return "\n".join(["Metal-site companion overlay applied.", overlay]) if overlay else None

    return None


def _run_kvfinder_pocket_overlay(session, executor=None, *, top_n=3):
    from .semantic import find_kvfinder_pockets
    from .named_selection import add_group, list_groups, remove_group

    def slug(text):
        value = re.sub(r"[^A-Za-z0-9]+", "_", str(text or "")).strip("_").lower()
        return value or "geometry"

    prior_models = list(getattr(session, "_codex_analysis_pocket_models", []) or [])
    if prior_models:
        unique_models = []
        seen = set()
        for model in prior_models:
            if model is None or id(model) in seen:
                continue
            seen.add(id(model))
            unique_models.append(model)
        try:
            session.models.close(unique_models)
        except Exception:
            pass
    session._codex_analysis_pocket_models = []

    try:
        for group_name in list_groups(session):
            if group_name.startswith("analysis_pocket_"):
                remove_group(session, group_name)
    except Exception:
        pass

    try:
        pockets = find_kvfinder_pockets(
            session,
            top_n=max(1, min(10, int(top_n))),
            lining_shell=5.0,
            max_lining_shell=7.0,
            min_lining_residues=12,
            min_volume=60.0,
            min_depth=0.8,
            cleanup_models=False,
            return_cavity_models=True,
        )
    except Exception as err:
        try:
            session.logger.warning(f"KVFinder pocket companion failed: {err}")
        except Exception:
            pass
        return None
    if not pockets:
        return None

    color = "#5b8fb9"
    lines = [
        f"KVFinder pocket companion overlay applied: top pocket shown, {len(pockets)} candidate(s) registered.",
        "Protein display/color/labels were left unchanged.",
    ]
    managed = []
    for rank, pocket in enumerate(pockets, start=1):
        specs = list(pocket.get("lining_specs") or [])
        if not specs:
            continue
        tags = list(pocket.get("tags") or [])
        token = slug(tags[0].split(":", 1)[-1].strip().split()[0]) if tags else "geometry"
        group_name = f"analysis_pocket_{rank:02d}_{token}"
        try:
            add_group(session, group_name, " ".join(specs[:96]), color=color)
        except Exception:
            pass
        cavity_model = pocket.get("cavity_model")
        cavity_group = pocket.get("cavity_group")
        cavity_spec = str(pocket.get("cavity_model_spec") or "").strip()
        lines.append(
            f"- #{rank} score={float(pocket.get('rank_score', 0.0) or 0.0):.2f} "
            f"volume={float(pocket.get('volume', 0.0) or 0.0):.0f} A^3 "
            f"depth={float(pocket.get('max_depth', 0.0) or 0.0):.1f} A "
            f"lining={len(specs)} group={group_name} tags={', '.join(tags) if tags else 'geometry-only'}"
        )
        if rank != 1:
            try:
                if cavity_model is not None:
                    session.models.close([cavity_model])
            except Exception:
                pass
            continue
        if not cavity_spec:
            continue
        managed.append(cavity_group if cavity_group is not None else cavity_model)
        for command in (
            f"show {cavity_spec} atoms",
            f"style {cavity_spec} sphere",
            f"color {cavity_spec} {color} target a",
            f"transparency {cavity_spec} 45 target a",
            f"surface {cavity_spec}",
            f"color {cavity_spec} {color} target s",
            f"transparency {cavity_spec} 65 target s",
        ):
            try:
                _run(session, command, executor=executor)
            except Exception:
                pass
    session._codex_analysis_pocket_models = [model for model in managed if model is not None]
    return "\n".join(lines)


def run_partial_local_flow(session, prompt, *, mode="combined", progress=None, executor=None):
    handler_names = {
        "combined": COMBINED_HANDLER_NAMES,
        "analysis": ANALYSIS_HANDLER_NAMES,
        "visual": VISUAL_HANDLER_NAMES,
    }.get(mode, COMBINED_HANDLER_NAMES)

    parts = _split_compound_prompt(prompt)
    if not parts:
        return None, ""
    if len(parts) == 1:
        result = _try_handlers(session, parts[0], progress, executor, handler_names)
        return result, "" if result is not None else parts[0]

    local_blocks = []
    unresolved = []
    for part in parts:
        result = _try_handlers(session, part, progress, executor, handler_names)
        if result is None:
            unresolved.append(part)
            continue
        local_blocks.append(f"Request part: {part}")
        local_blocks.extend(result.splitlines())

    if not local_blocks:
        return None, prompt

    joined = "\n".join(["Local ChimeraX partial workflow executed.", *local_blocks])
    return joined, " and ".join(unresolved)


def _try_compound_fastpath(session, prompt, progress, executor, handler_names):
    parts = _split_compound_prompt(prompt)
    if len(parts) <= 1:
        return None

    results = []
    for part in parts:
        result = _try_handlers(session, part, progress, executor, handler_names)
        if result is None:
            return None
        results.append((part, result))

    lines = ["Local ChimeraX multi-step actions executed."]
    for part, result in results:
        lines.append(f"Request part: {part}")
        lines.extend(result.splitlines())
    return "\n".join(lines)


def _try_handlers(session, prompt, progress, executor, handler_names):
    lowered = normalized_prompt_for_matching(prompt)
    for handler in _handlers_by_name(handler_names):
        result = handler(session, prompt, lowered, progress, executor)
        if result is not None:
            return result
    return None


def list_builtin_commands():
    return [
        "/models              list open models and quick aliases",
        "/selected [analyze] [top=N]   selection overlap summary; top=N (1-50, default 4-6 per section) caps each block",
        "/groups              list the latest manual-edit group selections",
        "/workspace [domains|roles|sites|features|clear]  create editable models visible in Models panel",
        "/legend [save [file]|save-table [file]] [top=N]   color meanings; top=N (1-50, default 8) caps domain rows",
        "/caption [style] [sentences=N]   figure caption draft; sentences=N (1-20, default 3-6 by style) overrides cap",
        "/panels              suggest a multi-panel figure layout",
        "/package [prefix]    export captions, legend, panel plan, and key snapshots",
        "/blast [query] [database]  run ChimeraX Blast Protein using chain, sequence, or UniProt query",
        "/hhpred             open HHpred / HHblits with the current protein sequence",
        "/signalp [run|web|apply <path>|prodomain|clear] [model=#N] [organism=other|eukarya]   signal peptide/prodomain view",
        "/alphafoldtool [query]  open AlphaFold model search/fetch workflow from this plugin",
        "/similar [open|seq|traces|ligands|cluster] [count=N]  Foldseek workflows; count=N (1-20) hit count",
        "/foldmason           launch FoldMason MSTA; auto-opens similar structures if only one is available",
        "/folddisco           export selected residue motif and launch FoldDisco",
        "/nucdock <seq> [type]  dock a DNA/RNA sequence to the current structure through HDOCK",
        "/nucdock_load <path>  load downloaded HDOCK/NucDock structures into ChimeraX",
        "/afcomplex <seq> [type]  open AlphaFold Server with current protein chains plus DNA/RNA sequence",
        "/afcomplex_load <path>  load downloaded AlphaFold Server complex structures into ChimeraX",
        "/boltz [panel|setup] [model=boltz1|boltz2]   run Boltz CLI; setup auto-installs venv",
        "/boltz_load <path>    load Boltz output structures into ChimeraX",
        "/alphafold_load <path>  load downloaded AlphaFold prediction structures into ChimeraX",
        "/rapidock <peptide> [pocket=N] [engine=X] [n=K] [buffer=B]   docking; pocket=N (1-20, default 1), n=K poses (1-20, default 5), buffer=B Å (4-30, default 12), engine={auto|hpepdock|docker|native}",
        "/rapidock_setup [path]  clone RAPiDock to ~/RAPiDock or the supplied path",
        "/rapidock_load <output_dir> [peptide=PEP] [limit=N]   load RAPiDock pose outputs; limit=N (1-20, default 5) top poses",
        "/hpepdock_load <output_dir> [peptide=PEP] [limit=N]   package HPEPDOCK receptor+peptide complexes, summary TSV/MD, and load top poses",
        "/hpepdock_refine <output_dir> [limit=N] [steps=N]   rerun HPEPDOCK minimization/validation and write minimized complexes",
        "/alignpanel [#refspec]    register all open structures into the sequence-bar alignment panel",
        "/cavity [reset|show|dist=D|trans=T|pockets=K|show=R|min_vol=V|min_depth=D]    cavity; rank K candidates (1-6, default 5), show selected rank(s)",
        "/setup [tool]              install/configure CLI tools (rapidock, boltz, foldmason, folddisco, caver)",
        "/cancel                    stop all RAPiDock/HPEPDOCK/Downloads/external watchers in this session",
        "/membrane [view|mlp|web|opm|charmm|memgen|clear|report] [thickness=T] [trans=N] [width=W] [margin=M] [top=N]   virtual membrane slab; report top=N (1-50, default 10) TM segments",
        "/pisa [view|report|web] [pair=N] [cutoff=C] [top=N]   interface analysis; report top=N (1-50, default 10) candidate cap",
        "/pisaweb            export current/selected structure and upload to PDBePISA",
        "/caver [panel|prepare|run|web|import <path>|lining] [max_tunnels=N] [dist=D]   CAVER workflow; dist=D (1-15 Å, default 4) lining cutoff",
        "/caver_load <path>    import CAVER tunnel results from a folder, ZIP, or PDB",
        "/seqview [chain]     open Sequence Viewer for a chain",
        "/profile [alignment-id]  open Profile Grid for an existing alignment",
        "/dali [selection|domain N|spec]  export a target and open the DALI server",
        "/daliweb            export current/selected structure and upload to DALI web form",
        "/dali_load <path>     load downloaded DALI structures into ChimeraX",
        "/vast               export current/selected structure and upload to NCBI VAST",
        "/vast_load <path>     load downloaded VAST structures into ChimeraX",
        "/pdbefold           export current/selected structure and upload to PDBeFold / SSM",
        "/pdbefold_load <path> load downloaded PDBeFold structures into ChimeraX",
        "/usalign            align open structures in ChimeraX and run local US-align if available",
        "/usalign_load <path>  load downloaded US-align structures into ChimeraX",
        "/foldmason_load <path>  load FoldMason/Foldseek structure outputs into ChimeraX",
        "/folddisco_load <path>  load FoldDisco structure outputs into ChimeraX",
        "/daliurl <url>       store a DALI result URL for this session",
        "/dalisummary <text>  store a short DALI hit summary note",
        "/dalistatus          show the latest DALI export/result state",
        "/sequence [model] [gaps=N]   chain sequence/gap report; gaps=N (1-50, default 6) gap-list cap per chain",
        "/domains [model] [chunks=N]   domain chunk report; chunks=N (1-50, default 10) per chain cap",
        "/domains view [model] [labels=N]   color domain chunks; labels=N (0-50, default 8) labels to render",
        "/domains finer|coarser|reset|status [model]  refine current domain chunking",
        "/complex [model] [cutoff=C]   chain-interface summary; cutoff=C (3-15 Å, default 8) inter-chain contact distance",
        "/interfaces [spec]   compute interface network diagram/log summary",
        "/interfacesselect <spec1> <spec2>  select interface residues between two sets",
        "/roles [model]       active-site vs scaffold role summary",
        "/roles view [model] [labels=N]   color active/scaffold/peripheral; labels=N (0-50, default 8)",
        "/annotate [model] [top=N]   UniProt+ligand+metal+catalytic+motif+features summary; top=N (1-50, default 4-8 per section) caps each block",
        "/features [view] [model] [top=N]   UniProt features; report top=N (1-200, default 32); view top=N (1-100, default 24) entries to render",
        "/motif [model|pattern] [top=N]   motif report; top=N (1-200, default 32) display cap; view subaction caps at 50",
        "/motif view [pattern] [top=N]   highlight motif residues; top=N (1-50, default 12) hits to render",
        "/conservation [view] [model|chain] [top=N]   ConSurf-lite; view top=N (1-50, default 18); report top=N (1-50, default 10) display cap",
        "/consurf [web|view] [model|chain]  open ConSurf Colab or run local conservation report",
        "/ligand [model] [cutoff=C] [nearby=N] [catalytic=N]   ligand-pocket report; nearby/catalytic (1-50, default 10/8) display caps",
        "/catalytic [view|triage|zoom] [model] [top=N] [triads=N] [trans=N] [triad_color=#hex]   catalytic; view: top/triads (color auto-picked from chain palette); zoom: cartoon trans + view-fit",
        "/chains [model]      show chain ranges for a model",
        "/analyze [model]     detailed structure analysis report",
        "/metal [report|predict|place|evidence|clear] [model] [top=N] [all] [kvfinder]   metal-site report, RCSB fold evidence, or virtual metal placement",
        "/residue <spec>      highlight and label a residue or residue range",
        "/site <metal|ligand|interface|catalytic|all> [residues=N]  highlight sites; residues=N (1-50, default 12) caps shown residues",
        "/show [spec] [atoms|cartoons|surfaces|models]  show common representations",
        "/hide [spec] [atoms|cartoons|surfaces|models]  hide common representations",
        "/select <spec|clear>  create or clear selection",
        "/distance <spec1> <spec2>  measure distance between two objects",
        "/angle <spec1> <spec2> <spec3> [spec4]  measure angle",
        "/buriedarea <spec1> <spec2>  measure buried area between two atom sets",
        "/measurearea [spec]  measure surface area of a surface model",
        "/contactarea <surf1> <surf2>  measure surface contact area",
        "/convexity [spec]  color a surface by convexity",
        "/hbonds [spec]  find hydrogen bonds",
        "/hbondsdelete       remove hydrogen-bond pseudobonds",
        "/contacts [spec]  find contacts",
        "/contactsdelete     remove contact pseudobonds",
        "/clashes [spec]  find clashes",
        "/clashesdelete      remove clash pseudobonds",
        "/zone <cutoff> [spec]  select within cutoff of current selection or spec",
        "/surfacezone <cutoff> [spec]  show only surface within cutoff of atoms",
        "/surfaceunzone [spec]  turn off surface zoning",
        "/rock [axis angle]  rock the view",
        "/wobble [axis angle]  wobble the view",
        "/turn <axis> <angle>  rotate the scene",
        "/zoom [factor]  zoom the view",
        "/wait [frames]  wait for animations or motions",
        "/stop  stop ongoing motion",
        "/close [spec]  close models or pseudobond displays",
        "/style <mode>        cartoon | sticks | surface | hide-surface",
        "/color <scheme>      chain | model | rainbow",
        "/palette [list|name] list palettes or apply sequential coloring",
        "/transparency <percent> [surface|cartoon|atoms|all]  set transparency",
        "/focus [all|sel]     focus all models or current selection",
        "/scene save [name] [note...]   save a named scene bookmark (view + render + caption/legend metadata)",
        "/scene load [name] [frames]  restore a named scene bookmark; frames clamp 1-600 (default 15)",
        "/scene info [name]   show saved metadata for a scene bookmark",
        "/scene note <name> <text>  update the note attached to a scene bookmark",
        "/scene list          list saved scene bookmarks",
        "/scene delete <name|all>  delete saved scene bookmarks",
        "/layout reset|spacing <value>  manage explode-layout settings",
        "/snapshot [file|publication [file]] [w=W h=H ss=K]   png screenshot; pub default 2400×1800 ss=3 (200-8000, 1-8)",
        "/movie <record|stop|encode|status|reset|abort|formats> [path] [ss=K] [quality=Q]   record/encode movie; ss (1-8, default 3), quality {low|fair|good|high|highest}",
        "/figure [lab|next|cycle|back|repeat|clean|publication|selection|selection-{pocket|motif|interface|composite} [top=N]|composite|explode-composite [spacing] [labels=N]|domains|roles|assembly|pocket [catalytic=N focus=K]|interface|explode [spacing] [labels=N]|interactive]  figure modes; selection top=N (1-50, default 8), explode labels=N (0-50, default 10), spacing 0.5-500 Å, pocket catalytic=N (1-30, default 8) focus=K (1-10, default 4)",
    ]


def run_builtin_slash(session, command, arg, terminal_write, executor=None):
    def write_block(text):
        for line in text.splitlines() or [""]:
            terminal_write(line)

    command = command.lower()
    if command == "/models":
        from .semantic import format_models_report

        for line in format_models_report(session).splitlines():
            terminal_write(line)
        return True

    if command == "/selected":
        raw = str(arg or "").strip()
        display_limit = None
        kept_tokens = []
        for tok in raw.split():
            lower = tok.lower()
            if lower.startswith("top=") or lower.startswith("n=") or lower.startswith("limit="):
                try: display_limit = max(1, min(50, int(lower.split("=", 1)[1])))
                except: pass
            else:
                kept_tokens.append(tok)
        cleaned_arg = " ".join(kept_tokens)
        action, target_arg = _split_action_arg(cleaned_arg)
        if action in {"analyze", "focus", "detail"}:
            from .semantic import format_selection_focus_report

            report = format_selection_focus_report(session, target_arg or None, display_limit=display_limit)
        else:
            from .semantic import format_selection_overlap_report

            report = format_selection_overlap_report(session, cleaned_arg or None, display_limit=display_limit)
        for line in report.splitlines():
            terminal_write(line)
        return True

    if command == "/groups":
        write_block(_run_groups_status(session))
        return True

    if command == "/blast":
        write_block(_run_blast_tool(session, arg, executor=executor))
        return True

    if command == "/hhpred":
        write_block(_run_hhpred_tool(session, arg, executor=executor))
        return True

    if command == "/signalp":
        write_block(_run_signalp_tool(session, arg, executor=executor))
        return True

    if command == "/alphafoldtool":
        write_block(_run_alphafold_tool(session, arg, executor=executor))
        return True

    if command == "/similar":
        write_block(_run_similar_tool(session, arg, executor=executor))
        return True

    if command == "/foldmason":
        write_block(_run_foldmason_tool(session, arg, executor=executor))
        return True

    if command == "/folddisco":
        write_block(_run_folddisco_tool(session, arg, executor=executor))
        return True

    if command == "/nucdock":
        write_block(_run_nucdock_tool(session, arg, executor=executor))
        return True

    if command == "/nucdock_load":
        write_block(_run_external_load_tool(session, arg, "nucdock"))
        return True

    if command == "/afcomplex":
        write_block(_run_afcomplex_tool(session, arg, executor=executor))
        return True

    if command == "/afcomplex_load":
        write_block(_run_external_load_tool(session, arg, "afcomplex"))
        return True

    if command == "/boltz":
        write_block(_run_boltz_tool(session, arg, executor=executor))
        return True

    if command == "/boltz_load":
        write_block(_run_external_load_tool(session, arg, "boltz"))
        return True

    if command == "/alphafold_load":
        write_block(_run_external_load_tool(session, arg, "alphafold"))
        return True

    if command == "/rapidock":
        write_block(_run_rapidock_tool(session, arg, executor=executor))
        return True

    if command == "/rapidock_setup":
        write_block(_run_rapidock_setup_tool(session, arg, executor=executor))
        return True

    if command in ("/rapidock_load", "/hpepdock_load"):
        write_block(_run_rapidock_load_tool(session, arg, executor=executor))
        return True

    if command == "/hpepdock_refine":
        write_block(_run_hpepdock_refine_tool(session, arg, executor=executor))
        return True

    if command in ("/alignpanel", "/alignpanel_register"):
        write_block(_run_alignpanel_register_tool(session, arg, executor=executor))
        return True

    if command == "/cavity":
        write_block(_run_cavity_tool(session, arg, executor=executor))
        return True

    if command == "/setup":
        write_block(_run_setup_tool(session, arg, executor=executor))
        return True

    if command in ("/rapidock_cancel", "/hpepdock_cancel", "/cancel"):
        write_block(_run_cancel_tool(session, arg, executor=executor))
        return True

    if command == "/membrane":
        write_block(_run_membrane_tool(session, arg, executor=executor))
        return True

    if command == "/pisa":
        write_block(_run_pisa_tool(session, arg, executor=executor))
        return True

    if command == "/seqview":
        write_block(_run_seqview_tool(session, arg, executor=executor))
        return True

    if command == "/profile":
        write_block(_run_profile_tool(session, arg))
        return True

    if command == "/workspace":
        write_block(_run_workspace(session, arg, executor=executor))
        return True

    if command == "/legend":
        from .semantic import format_legend_report

        raw_l = str(arg or "").strip()
        display_limit = 8
        kept_l = []
        for tok in raw_l.split():
            lower = tok.lower()
            if lower.startswith("top=") or lower.startswith("n=") or lower.startswith("limit="):
                try: display_limit = max(1, min(50, int(lower.split("=", 1)[1])))
                except: pass
            else:
                kept_l.append(tok)
        cleaned_l = " ".join(kept_l)
        action, target_arg = _split_action_arg(cleaned_l)
        if action == "save":
            write_block(_run_legend_save(session, target_arg))
            return True
        if action in {"save-table", "savetable", "table"}:
            write_block(_run_legend_save(session, target_arg, style="table"))
            return True
        for line in format_legend_report(session, cleaned_l or None, display_limit=display_limit).splitlines():
            terminal_write(line)
        return True

    if command == "/caption":
        from .semantic import format_caption_draft

        raw_c = str(arg or "").strip()
        sentence_limit = None
        kept_c = []
        for tok in raw_c.split():
            lower = tok.lower()
            if lower.startswith("sentences=") or lower.startswith("top=") or lower.startswith("n=") or lower.startswith("limit="):
                try: sentence_limit = max(1, min(20, int(lower.split("=", 1)[1])))
                except: pass
            else:
                kept_c.append(tok)
        cleaned_c = " ".join(kept_c)
        style, target_arg = _split_action_arg(cleaned_c)
        if style not in {"short", "paper", "nature", "panels", "selection", "domain", "pocket", "interface"}:
            target_arg = cleaned_c or None
            style = "paper"
        for line in format_caption_draft(session, target_arg, style=style, sentence_limit=sentence_limit).splitlines():
            terminal_write(line)
        return True

    if command == "/panels":
        from .semantic import format_panel_plan

        for line in format_panel_plan(session, arg or None).splitlines():
            terminal_write(line)
        return True

    if command == "/package":
        write_block(_run_package_export(session, arg or None, executor=executor))
        return True

    if command == "/dali":
        write_block(_run_dali(session, arg or None, executor=executor))
        return True

    if command == "/daliweb":
        write_block(_run_structure_web_tool(session, "dali", executor=executor))
        return True

    if command == "/vast":
        write_block(_run_structure_web_tool(session, "vast", executor=executor))
        return True

    if command == "/pdbefold":
        write_block(_run_structure_web_tool(session, "pdbefold", executor=executor))
        return True

    if command == "/pisaweb":
        write_block(_run_structure_web_tool(session, "pisa", executor=executor))
        return True

    if command == "/caver":
        from .caver import run_caver_action

        write_block(run_caver_action(session, arg, executor=executor))
        return True

    if command == "/caver_load":
        write_block(_run_caver_load_tool(session, arg, executor=executor))
        return True

    if command == "/usalign":
        write_block(_run_structure_web_tool(session, "usalign", executor=executor))
        return True

    if command == "/dali_load":
        write_block(_run_external_load_tool(session, arg, "dali"))
        return True

    if command == "/vast_load":
        write_block(_run_external_load_tool(session, arg, "vast"))
        return True

    if command == "/pdbefold_load":
        write_block(_run_external_load_tool(session, arg, "pdbefold"))
        return True

    if command == "/usalign_load":
        write_block(_run_external_load_tool(session, arg, "usalign"))
        return True

    if command == "/foldmason_load":
        write_block(_run_external_load_tool(session, arg, "foldmason"))
        return True

    if command == "/folddisco_load":
        write_block(_run_external_load_tool(session, arg, "folddisco"))
        return True

    if command == "/daliurl":
        write_block(_run_dali_url(session, arg or None))
        return True

    if command == "/dalisummary":
        write_block(_run_dali_summary(session, arg or None))
        return True

    if command == "/dalistatus":
        write_block(_run_dali_status(session))
        return True

    if command == "/layout":
        action, target_arg = _split_action_arg(arg)
        if action == "reset":
            write_block(_run_layout_reset(session, executor=executor))
            return True
        if action == "spacing":
            write_block(_run_layout_spacing(session, target_arg))
            return True
        terminal_write("usage: /layout reset | /layout spacing <value>")
        return True

    if command == "/chains":
        from .semantic import format_chains_report

        for line in format_chains_report(session, arg or None).splitlines():
            terminal_write(line)
        return True

    if command == "/sequence":
        from .semantic import format_sequence_report
        raw = str(arg or "").strip()
        gaps_show = 6
        kept_tokens = []
        for tok in raw.split():
            lower = tok.lower()
            if lower.startswith("gaps=") or lower.startswith("top=") or lower.startswith("n="):
                try: gaps_show = max(1, min(50, int(lower.split("=", 1)[1])))
                except: pass
            else:
                kept_tokens.append(tok)
        cleaned_arg = " ".join(kept_tokens) or None
        for line in format_sequence_report(session, cleaned_arg, gaps_show=gaps_show).splitlines():
            terminal_write(line)
        return True

    if command == "/domains":
        # Parse labels=N before splitting action
        raw_d = str(arg or "").strip()
        label_n = 8
        kept_d = []
        for tok in raw_d.split():
            lower = tok.lower()
            if lower.startswith("labels=") or lower.startswith("label=") or lower.startswith("lbl="):
                try: label_n = max(0, min(50, int(lower.split("=", 1)[1])))
                except: pass
            else:
                kept_d.append(tok)
        cleaned_d = " ".join(kept_d)
        action, target_arg = _split_action_arg(cleaned_d)
        if action in {"view", "show", "color", "select", "selections"}:
            write_block(_run_domains_view(session, target_arg, executor=executor, label_n=label_n))
            return True
        if action in {"finer", "fine", "split-more", "more"}:
            write_block(_run_domain_split_refinement(session, "finer", model_hint=target_arg or None, executor=executor))
            return True
        if action in {"coarser", "coarse", "merge", "less"}:
            write_block(_run_domain_split_refinement(session, "coarser", model_hint=target_arg or None, executor=executor))
            return True
        if action in {"reset", "default"}:
            write_block(_run_domain_split_refinement(session, "reset", model_hint=target_arg or None, executor=executor))
            return True
        if action in {"status", "settings"}:
            write_block(_domain_split_status(session))
            return True
        from .semantic import format_domains_report
        chunks_show = 10
        kept_tokens = []
        for tok in cleaned_d.split():
            lower = tok.lower()
            if lower.startswith("chunks=") or lower.startswith("top=") or lower.startswith("n="):
                try: chunks_show = max(1, min(50, int(lower.split("=", 1)[1])))
                except: pass
            else:
                kept_tokens.append(tok)
        cleaned_arg = " ".join(kept_tokens) or None
        for line in format_domains_report(session, cleaned_arg, chunks_show=chunks_show).splitlines():
            terminal_write(line)
        return True

    if command == "/complex":
        from .semantic import format_complex_report
        raw = str(arg or "").strip()
        contact_cutoff = 8.0
        tokens = []
        for tok in raw.split():
            lower = tok.lower()
            if lower.startswith("cutoff=") or lower.startswith("contact="):
                try: contact_cutoff = max(3.0, min(15.0, float(lower.split("=", 1)[1])))
                except: pass
            else:
                tokens.append(tok)
        cleaned = " ".join(tokens) or None
        for line in format_complex_report(session, cleaned, contact_cutoff=contact_cutoff).splitlines():
            terminal_write(line)
        return True

    if command == "/interfaces":
        write_block(_run_interfaces(session, arg, executor=executor))
        return True

    if command == "/interfacesselect":
        write_block(_run_interfaces_select(session, arg, executor=executor))
        return True

    if command == "/roles":
        raw_r = str(arg or "").strip()
        label_n = 8
        kept_r = []
        for tok in raw_r.split():
            lower = tok.lower()
            if lower.startswith("labels=") or lower.startswith("label=") or lower.startswith("lbl="):
                try: label_n = max(0, min(50, int(lower.split("=", 1)[1])))
                except: pass
            else:
                kept_r.append(tok)
        cleaned_r = " ".join(kept_r)
        action, target_arg = _split_action_arg(cleaned_r)
        if action in {"view", "show", "color"}:
            write_block(_run_roles_view(session, target_arg, executor=executor, label_n=label_n))
            return True
        from .semantic import format_roles_report

        for line in format_roles_report(session, cleaned_r or None).splitlines():
            terminal_write(line)
        return True

    if command == "/annotate":
        from .semantic import format_annotation_report
        raw = str(arg or "").strip()
        display_limit = None
        kept_tokens = []
        for tok in raw.split():
            lower = tok.lower()
            if lower.startswith("top=") or lower.startswith("n=") or lower.startswith("limit="):
                try: display_limit = max(1, min(50, int(lower.split("=", 1)[1])))
                except: pass
            else:
                kept_tokens.append(tok)
        cleaned_arg = " ".join(kept_tokens) or None
        for line in format_annotation_report(session, cleaned_arg, display_limit=display_limit).splitlines():
            terminal_write(line)
        return True

    if command == "/features":
        raw = str(arg or "").strip()
        display_limit = 32
        view_top_n = 24
        kept = []
        for tok in raw.split():
            lower = tok.lower()
            if lower.startswith("top=") or lower.startswith("n=") or lower.startswith("limit="):
                try:
                    val = max(1, min(200, int(lower.split("=", 1)[1])))
                except Exception:
                    val = None
                if val is not None:
                    display_limit = val
                    view_top_n = max(1, min(100, val))
            else:
                kept.append(tok)
        cleaned_arg = " ".join(kept)
        action, target_arg = _split_action_arg(cleaned_arg)
        if action in {"view", "show", "highlight"}:
            write_block(_run_features_view(session, target_arg or None, executor=executor, top_n=view_top_n))
            return True
        from .semantic import format_uniprot_feature_report
        cleaned = cleaned_arg or None
        for line in format_uniprot_feature_report(session, cleaned, display_limit=display_limit).splitlines():
            terminal_write(line)
        return True

    if command == "/motif":
        raw = str(arg or "").strip()
        display_limit = 32
        view_top_n = 12
        kept_tokens = []
        for tok in raw.split():
            lower = tok.lower()
            if lower.startswith("top=") or lower.startswith("n=") or lower.startswith("limit="):
                try:
                    val = max(1, min(200, int(lower.split("=", 1)[1])))
                except Exception:
                    val = None
                if val is not None:
                    display_limit = val
                    view_top_n = max(1, min(50, val))
            else:
                kept_tokens.append(tok)
        cleaned_arg = " ".join(kept_tokens)
        action, target_arg = _split_action_arg(cleaned_arg)
        model_hint, motif_text = _parse_motif_args(target_arg if action in {"view", "show", "highlight"} else cleaned_arg)
        if action in {"view", "show", "highlight"}:
            write_block(_run_motif_view(session, motif_text=motif_text, model_hint=model_hint,
                                        executor=executor, top_n=view_top_n))
            return True
        from .semantic import format_motif_report

        for line in format_motif_report(session, model_hint=model_hint, motif_text=motif_text, display_limit=display_limit).splitlines():
            terminal_write(line)
        return True

    if command in {"/conservation", "/consurf"}:
        action, target_arg = _split_action_arg(arg)
        if command == "/consurf" and action in {"web", "open", "server", "colab", "online"}:
            from .toolbar_actions import _launch_consurf_page

            write_block(_launch_consurf_page(session))
            return True
        if action in {"view", "show", "highlight"}:
            from .conservation import apply_conservation_view
            # Parse top=N override
            top_n = 18
            cleaned_tokens = []
            for tok in (target_arg or "").split():
                lower = tok.lower()
                if lower.startswith("top=") or lower.startswith("n="):
                    try: top_n = max(1, min(50, int(lower.split("=", 1)[1])))
                    except: pass
                else:
                    cleaned_tokens.append(tok)
            cleaned_target = " ".join(cleaned_tokens) or None
            write_block(apply_conservation_view(session, model_hint=cleaned_target,
                                                query_text=arg, executor=executor, top_n=top_n))
            return True
        from .conservation import format_conservation_report
        raw = str(arg or "").strip()
        display_limit = 10
        kept_tokens = []
        for tok in raw.split():
            lower = tok.lower()
            if lower.startswith("top=") or lower.startswith("n=") or lower.startswith("limit="):
                try: display_limit = max(1, min(50, int(lower.split("=", 1)[1])))
                except: pass
            else:
                kept_tokens.append(tok)
        cleaned_arg = " ".join(kept_tokens) or None
        write_block(format_conservation_report(session, model_hint=cleaned_arg, query_text=arg, display_limit=display_limit))
        return True

    if command == "/ligand":
        from .semantic import format_ligand_report
        raw = str(arg or "").strip()
        cutoff = 4.5
        nearby_show = 10
        catalytic_show = 8
        tokens = []
        for tok in raw.split():
            lower = tok.lower()
            if lower.startswith("cutoff=") or lower.startswith("shell="):
                try: cutoff = max(2.0, min(15.0, float(lower.split("=", 1)[1])))
                except: pass
            elif lower.startswith("nearby=") or lower.startswith("near="):
                try: nearby_show = max(1, min(50, int(lower.split("=", 1)[1])))
                except: pass
            elif lower.startswith("catalytic=") or lower.startswith("cat="):
                try: catalytic_show = max(1, min(50, int(lower.split("=", 1)[1])))
                except: pass
            else:
                tokens.append(tok)
        cleaned = " ".join(tokens) or None
        for line in format_ligand_report(session, cleaned, shell_cutoff=cutoff,
                                         nearby_show=nearby_show, catalytic_show=catalytic_show).splitlines():
            terminal_write(line)
        return True

    if command == "/catalytic":
        action, target_arg = _split_action_arg(arg)
        if action in {"view", "show", "highlight"}:
            top_n = 12
            triads_n = 6
            triad_color_override = None
            import re as _re
            cleaned = []
            for tok in (target_arg or "").split():
                lower = tok.lower()
                if lower.startswith("top=") or lower.startswith("n="):
                    try: top_n = max(1, min(50, int(lower.split("=", 1)[1])))
                    except: pass
                elif lower.startswith("triad_color=") or lower.startswith("color="):
                    # Check this BEFORE "triad=" since "triad_color=" also starts with "triad"
                    val = tok.split("=", 1)[1]
                    if _re.fullmatch(r"#[0-9a-fA-F]{6}", val):
                        triad_color_override = val.lower()
                elif lower.startswith("triads=") or lower.startswith("triad="):
                    try: triads_n = max(1, min(20, int(lower.split("=", 1)[1])))
                    except: pass
                else:
                    cleaned.append(tok)
            target = " ".join(cleaned) or None
            write_block(_run_catalytic_view(session, target, executor=executor,
                                             top_n=top_n, triads_n=triads_n,
                                             triad_color=triad_color_override))
            return True
        if action in {"zoom", "focus", "closeup"}:
            # Make the catalytic triad pop: fade scaffold, zoom to triad atoms.
            # trans=N (0-100, default 80) overrides cartoon transparency.
            trans = 80
            for tok in (target_arg or "").split():
                lower = tok.lower()
                if lower.startswith("trans=") or lower.startswith("transparency="):
                    try: trans = max(0, min(100, int(lower.split("=", 1)[1])))
                    except: pass
            write_block(_run_catalytic_zoom(session, executor=executor, trans=trans))
            return True
        from .semantic import format_catalytic_report, format_catalytic_workflow_report
        raw = str(arg if action not in {"triage", "workflow", "review", "test", "검증"} else target_arg or "").strip()
        display_limit = 15
        kept_tokens = []
        for tok in raw.split():
            lower = tok.lower()
            if lower.startswith("top=") or lower.startswith("n=") or lower.startswith("limit="):
                try: display_limit = max(1, min(50, int(lower.split("=", 1)[1])))
                except: pass
            else:
                kept_tokens.append(tok)
        cleaned_arg = " ".join(kept_tokens) or None
        if action in {"triage", "workflow", "review", "test", "검증"}:
            report = format_catalytic_workflow_report(session, cleaned_arg, display_limit=display_limit)
        else:
            report = format_catalytic_report(session, cleaned_arg, display_limit=display_limit)
        for line in report.splitlines():
            terminal_write(line)
        return True

    if command == "/analyze":
        from .semantic import format_analyze_report

        for line in format_analyze_report(session, arg or None).splitlines():
            terminal_write(line)
        return True

    if command == "/metal":
        from .semantic import format_metal_report
        raw = str(arg or "").strip()
        raw_tokens = raw.split()
        action = raw_tokens[0].lower() if raw_tokens else "report"
        if action in {"report", "existing", "place", "put", "insert", "add", "optimize", "optimise", "refine", "predict", "candidate", "candidates", "scan", "evidence", "fold", "homolog", "homologue", "rcsb", "clear", "reset"}:
            raw_tokens = raw_tokens[1:]
        else:
            action = "report"

        if action in {"clear", "reset"}:
            from .metal_placement import clear_predicted_metals

            removed = clear_predicted_metals(session)
            terminal_write(f"[metal] cleared {removed} predicted-metal model(s)/pseudobond group(s).")
            return True

        if action in {"evidence", "fold", "homolog", "homologue", "rcsb"}:
            from .metal_placement import format_metal_evidence_report

            top_n = 5
            rows = 12
            model_tokens = []
            for tok in raw_tokens:
                lower = tok.lower()
                if lower.startswith(("top=", "n=", "sites=", "count=")):
                    try:
                        top_n = max(1, min(20, int(lower.split("=", 1)[1])))
                    except Exception:
                        pass
                elif lower.startswith(("rows=", "hits=")):
                    try:
                        rows = max(3, min(50, int(lower.split("=", 1)[1])))
                    except Exception:
                        pass
                else:
                    model_tokens.append(tok)
            model_hint = " ".join(model_tokens) or None
            text = format_metal_evidence_report(
                session,
                model_hint=model_hint,
                top_n=top_n,
                include_rcsb=True,
                rows=rows,
            )
            for line in text.splitlines():
                terminal_write(line)
            return True

        if action in {"place", "put", "insert", "add", "optimize", "optimise", "refine", "predict", "candidate", "candidates", "scan"}:
            from .metal_placement import run_metal_placement_pipeline

            place = action in {"place", "put", "insert", "add", "optimize", "optimise", "refine"}
            top_n = 1 if place else 5
            show_all = False if place else True
            clear_existing = True
            use_kvfinder = True
            preview = not place
            site_index = None
            model_tokens = []
            for tok in raw_tokens:
                lower = tok.lower()
                if lower in {"all", "show_all", "show-all"}:
                    show_all = True
                    if top_n == 1:
                        top_n = 5
                elif lower in {"keep", "append", "noclear", "no-clear"}:
                    clear_existing = False
                elif lower in {"kvfinder", "pocket", "pockets"}:
                    use_kvfinder = True
                elif lower in {"fast", "no-kvfinder", "no_kvfinder", "no-pocket", "no_pocket"}:
                    use_kvfinder = False
                elif lower in {"report", "no-preview", "no_preview"}:
                    preview = False
                elif lower.startswith(("top=", "n=", "sites=", "count=")):
                    try:
                        top_n = max(1, min(20, int(lower.split("=", 1)[1])))
                    except Exception:
                        pass
                elif lower.startswith(("site=", "rank=", "candidate=")):
                    try:
                        site_index = max(1, min(20, int(lower.split("=", 1)[1])))
                    except Exception:
                        pass
                elif lower.isdigit():
                    top_n = max(1, min(20, int(lower)))
                    if place:
                        show_all = True
                    else:
                        show_all = True
                else:
                    model_tokens.append(tok)
            model_hint = " ".join(model_tokens) or None
            text = run_metal_placement_pipeline(
                session,
                model_hint=model_hint,
                top_n=top_n,
                show_all=show_all,
                clear_existing=clear_existing,
                use_kvfinder=use_kvfinder,
                place=place,
                preview=preview,
                site_index=site_index,
                executor=executor,
            )
            for line in text.splitlines():
                terminal_write(line)
            return True

        direct_cutoff = 3.0
        shell_cutoff = 5.0
        direct_show = 8
        catalytic_show = 10
        tokens = []
        for tok in raw_tokens:
            lower = tok.lower()
            if lower.startswith("direct="):
                try: direct_cutoff = max(1.5, min(6.0, float(lower.split("=", 1)[1])))
                except: pass
            elif lower.startswith("shell=") or lower.startswith("cutoff="):
                try: shell_cutoff = max(2.0, min(15.0, float(lower.split("=", 1)[1])))
                except: pass
            elif lower.startswith("direct_show=") or lower.startswith("dshow="):
                try: direct_show = max(1, min(50, int(lower.split("=", 1)[1])))
                except: pass
            elif lower.startswith("catalytic_show=") or lower.startswith("cshow=") or lower.startswith("cat_show="):
                try: catalytic_show = max(1, min(50, int(lower.split("=", 1)[1])))
                except: pass
            else:
                tokens.append(tok)
        cleaned = " ".join(tokens) or None
        for line in format_metal_report(session, cleaned,
                                        direct_cutoff=direct_cutoff,
                                        shell_cutoff=shell_cutoff,
                                        direct_show=direct_show,
                                        catalytic_show=catalytic_show).splitlines():
            terminal_write(line)
        return True

    if command == "/residue":
        if not arg:
            terminal_write("usage: /residue <#model/chain:start-end | chain:start-end | RES123>")
            return True
        result = _run_residue_view(session, arg, executor=executor)
        write_block(result or "usage: /residue <#model/chain:start-end | chain:start-end | RES123>")
        return True

    if command == "/show":
        write_block(_run_show_hide(session, "show", arg, executor=executor))
        return True

    if command == "/hide":
        write_block(_run_show_hide(session, "hide", arg, executor=executor))
        return True

    if command == "/select":
        write_block(_run_select(session, arg, executor=executor))
        return True

    if command == "/distance":
        write_block(_run_distance(session, arg, executor=executor))
        return True

    if command == "/angle":
        write_block(_run_angle(session, arg, executor=executor))
        return True

    if command == "/buriedarea":
        write_block(_run_buriedarea(session, arg, executor=executor))
        return True

    if command == "/measurearea":
        write_block(_run_measure_area(session, arg, executor=executor))
        return True

    if command == "/contactarea":
        write_block(_run_contact_area(session, arg, executor=executor))
        return True

    if command == "/convexity":
        write_block(_run_convexity(session, arg, executor=executor))
        return True

    if command == "/hbonds":
        write_block(_run_hbonds(session, arg, executor=executor))
        return True

    if command == "/hbondsdelete":
        write_block(_run_delete_contacts_like(session, "hbonds", executor=executor))
        return True

    if command == "/contacts":
        write_block(_run_contacts(session, arg, executor=executor, kind="contacts"))
        return True

    if command == "/contactsdelete":
        write_block(_run_delete_contacts_like(session, "contacts", executor=executor))
        return True

    if command == "/clashes":
        write_block(_run_contacts(session, arg, executor=executor, kind="clashes"))
        return True

    if command == "/clashesdelete":
        write_block(_run_delete_contacts_like(session, "clashes", executor=executor))
        return True

    if command == "/zone":
        write_block(_run_zone_select(session, arg, executor=executor))
        return True

    if command == "/surfacezone":
        write_block(_run_surface_zone(session, arg, executor=executor))
        return True

    if command == "/surfaceunzone":
        write_block(_run_surface_unzone(session, arg, executor=executor))
        return True

    if command == "/rock":
        write_block(_run_rock(session, arg, executor=executor))
        return True

    if command == "/wobble":
        write_block(_run_wobble(session, arg, executor=executor))
        return True

    if command == "/turn":
        write_block(_run_turn(session, arg, executor=executor))
        return True

    if command == "/zoom":
        write_block(_run_zoom(session, arg, executor=executor))
        return True

    if command == "/wait":
        write_block(_run_wait(session, arg, executor=executor))
        return True

    if command == "/stop":
        write_block(_run_stop(session, executor=executor))
        return True

    if command == "/close":
        write_block(_run_close(session, arg, executor=executor))
        return True

    if command == "/site":
        if not arg:
            terminal_write("usage: /site metal|ligand|interface|catalytic|all [residues=N]")
            return True
        write_block(_run_site(session, arg, executor=executor))
        return True

    if command == "/style":
        if not arg:
            terminal_write("usage: /style cartoon|sticks|surface|hide-surface")
            return True
        write_block(_run_style(session, arg, executor=executor))
        return True

    if command == "/color":
        if not arg:
            terminal_write("usage: /color chain|model|rainbow")
            return True
        write_block(_run_color(session, arg, executor=executor))
        return True

    if command == "/palette":
        write_block(_run_palette(session, arg or "list", executor=executor))
        return True

    if command == "/transparency":
        if not arg:
            terminal_write("usage: /transparency <percent> [surface|cartoon|atoms|all]")
            return True
        write_block(_run_transparency(session, arg, executor=executor))
        return True

    if command == "/focus":
        write_block(_run_focus(session, arg or "sel", executor=executor))
        return True

    if command == "/scene":
        write_block(_run_scene(session, arg, executor=executor))
        return True

    if command == "/snapshot":
        action, target_arg = _split_action_arg(arg)
        if action in {"publication", "pub", "paper"}:
            write_block(_run_snapshot(session, target_arg or None, executor=executor, publication=True))
            return True
        write_block(_run_snapshot(session, arg or None, executor=executor))
        return True

    if command == "/movie":
        write_block(_run_movie(session, arg, executor=executor))
        return True

    if command == "/figure":
        write_block(_run_figure(session, arg or "publication", executor=executor))
        return True

    return False


def _analysis_fastpath(session, prompt, lowered, progress, executor):
    if not any(word in lowered for word in ("analy", "analysis", "inspect", "분석", "요약", "summarize")):
        return None
    from .semantic import format_analyze_report, format_selection_focus_report

    _emit(progress, "Local fast-path: structural analysis report")
    if any(word in lowered for word in ("selected", "selection", "선택")):
        return format_selection_focus_report(session)
    return format_analyze_report(session)


def _workflow_intent_fastpath(session, prompt, lowered, progress, executor):
    clauses = _split_compound_prompt(prompt)
    if len(clauses) <= 1:
        return None

    actions = []
    for clause in clauses:
        action = _infer_clause_action(session, clause)
        if action is None:
            return None
        actions.append(action)

    lines = ["Local ChimeraX workflow executed from natural language."]
    for action in actions:
        _emit(progress, f"Intent: {action['label']}")
        result = action["run"]()
        lines.append(f"Request part: {action['source']}")
        lines.extend((result or "(no result)").splitlines())
    return "\n".join(lines)


def _scene_fastpath(session, prompt, lowered, progress, executor):
    request = _scene_phrase_request(prompt, lowered=lowered)
    if request is None:
        return None
    arg = _scene_request_arg(request)
    _emit(progress, f"Local fast-path: scene {request['action']}")
    return _run_scene(session, arg, executor=executor)


def _builtin_tool_fastpath(session, prompt, lowered, progress, executor):
    if any(word in lowered for word in ("blast protein", "blast search", "blast", "블라스트")):
        _emit(progress, "Local fast-path: Blast Protein")
        return _run_blast_tool(session, prompt, executor=executor)
    if any(word in lowered for word in ("alphafold", "afdb", "알파폴드")):
        _emit(progress, "Local fast-path: AlphaFold")
        return _run_alphafold_tool(session, prompt, executor=executor)
    if any(word in lowered for word in ("similar structures", "foldseek", "유사 구조", "similarstructure")):
        _emit(progress, "Local fast-path: Similar Structures")
        return _run_similar_tool(session, prompt, executor=executor)
    if "foldmason" in lowered or "fold mason" in lowered:
        _emit(progress, "Local fast-path: FoldMason")
        return _run_foldmason_tool(session, prompt, executor=executor)
    if "folddisco" in lowered or "fold disco" in lowered:
        _emit(progress, "Local fast-path: FoldDisco")
        return _run_folddisco_tool(session, prompt, executor=executor)
    if any(word in lowered for word in ("nucdock", "hdock", "nucleotide dock", "dna docking", "rna docking", "핵산 도킹")):
        _emit(progress, "Local fast-path: nucleotide docking")
        return _run_nucdock_tool(session, prompt, executor=executor)
    if any(word in lowered for word in ("af complex", "alphafold complex", "nucleotide alphafold", "dna alphafold", "rna alphafold")):
        _emit(progress, "Local fast-path: AlphaFold complex")
        return _run_afcomplex_tool(session, prompt, executor=executor)
    if any(word in lowered for word in ("sequence viewer", "seqview", "show sequence", "서열 뷰어", "서열 보여")):
        _emit(progress, "Local fast-path: Sequence Viewer")
        return _run_seqview_tool(session, prompt, executor=executor)
    if any(word in lowered for word in ("profile grid", "profile", "consensus grid", "프로파일 그리드", "프로파일")):
        _emit(progress, "Local fast-path: Profile Grid")
        return _run_profile_tool(session, prompt)
    return None


def _annotation_fastpath(session, prompt, lowered, progress, executor):
    if not any(word in lowered for word in ("annotat", "annotation", "uniprot", "주석", "어노테이션")):
        return None
    from .semantic import format_annotation_report, format_uniprot_motif_report, format_uniprot_residue_report

    motif_like = any(
        word in lowered
        for word in (
            "motif", "모티프", "패턴", "sequence motif", "서열 모티프", "motif analysis", "모티프 분석",
            "target sequence", "타겟 서열", "타겟서열", "서열",
        )
    )

    residue_like = False
    try:
        residue_like = bool(_extract_residue_specs_for_phrase(session, prompt)) or any(
            word in lowered for word in ("residue", "residues", "잔기", "핵심", "core residue", "specific residue", "selection", "selected", "선택")
        )
    except Exception:
        residue_like = any(
            word in lowered for word in ("residue", "residues", "잔기", "핵심", "core residue", "specific residue", "selection", "selected", "선택")
        )

    if motif_like and not residue_like:
        _emit(progress, "Local fast-path: UniProt sequence motif analysis")
        model_hint, motif_text = _parse_motif_args(prompt)
        return format_uniprot_motif_report(session, query_text=prompt, model_hint=model_hint, motif_text=motif_text)

    if residue_like:
        _emit(progress, "Local fast-path: UniProt residue-focused lookup")
        return format_uniprot_residue_report(session, prompt)

    _emit(progress, "Local fast-path: annotation summary")
    return format_annotation_report(session)


def _motif_fastpath(session, prompt, lowered, progress, executor):
    if not any(word in lowered for word in ("motif", "모티프", "패턴", "sequence motif", "서열 모티프")):
        return None
    from .semantic import format_motif_report, format_uniprot_motif_report

    _emit(progress, "Local fast-path: motif scan")
    model_hint, motif_text = _parse_motif_args(prompt)
    if any(word in lowered for word in ("uniprot", "서열", "target sequence", "타겟 서열", "타겟서열")):
        return format_uniprot_motif_report(session, query_text=prompt, model_hint=model_hint, motif_text=motif_text)
    return format_motif_report(session, model_hint=model_hint, motif_text=motif_text)


def _conservation_fastpath(session, prompt, lowered, progress, executor):
    if not any(word in lowered for word in ("conservation", "consurf", "conserved", "보존", "보존성", "보존도")):
        return None
    from .conservation import apply_conservation_view, format_conservation_report

    if any(word in lowered for word in ("view", "show", "highlight", "표시", "강조", "보여")):
        _emit(progress, "Local fast-path: conservation view")
        return apply_conservation_view(session, model_hint=prompt, query_text=prompt, executor=executor)
    _emit(progress, "Local fast-path: conservation report")
    return format_conservation_report(session, model_hint=prompt, query_text=prompt)


def _metal_fastpath(session, prompt, lowered, progress, executor):
    if not any(word in lowered for word in ("metal", "zn", "mg", "mn", "fe", "cofactor", "금속")):
        return None
    evidence_words = any(word in lowered for word in (
        "evidence", "fold", "homolog", "homologue", "rcsb", "pdb",
        "solved", "experimental", "known structure",
        "현재 열", "열어놓", "구조기반", "구조 기반", "구조밝혀", "구조 밝혀",
        "점검", "확인",
    ))
    if evidence_words:
        from .metal_placement import format_metal_evidence_report

        _emit(progress, "Local fast-path: metal evidence from current structure and RCSB homologs")
        return format_metal_evidence_report(session, model_hint=prompt, include_rcsb=True)

    explicit_place_words = any(word in lowered for word in (
        "place", "put", "insert", "add", "optimize", "optimise", "refine",
        "삽입", "넣", "박아", "주입", "배치", "추가", "최적화",
    ))
    review_words = any(word in lowered for word in (
        "position", "coordination", "coordinating", "candidate", "predict",
        "preview", "review", "site", "sites",
        "찾", "후보", "예측", "위치", "좌표", "검토",
    ))
    if explicit_place_words or review_words:
        from .metal_placement import run_metal_placement_pipeline

        _emit(
            progress,
            "Local fast-path: virtual metal placement pipeline"
            if explicit_place_words else "Local fast-path: virtual metal-site prediction/preview",
        )
        return run_metal_placement_pipeline(
            session,
            model_hint=prompt,
            top_n=1 if explicit_place_words else 5,
            show_all=False if explicit_place_words else True,
            clear_existing=True,
            use_kvfinder=True,
            place=explicit_place_words,
            preview=not explicit_place_words,
            executor=executor,
        )

    _emit(progress, "Local fast-path: metal-centered site analysis")
    from .semantic import format_metal_report

    return format_metal_report(session)


def _ligand_fastpath(session, prompt, lowered, progress, executor):
    if not any(word in lowered for word in ("ligand", "substrate", "pocket", "cofactor", "binding site", "리간드")):
        return None
    from .semantic import format_ligand_report

    _emit(progress, "Local fast-path: ligand-pocket analysis")
    return format_ligand_report(session)


def _catalytic_fastpath(session, prompt, lowered, progress, executor):
    if not any(word in lowered for word in ("catalytic", "active residue", "active site", "active-site", "촉매", "활성부위", "활성 잔기", "촉매 잔기")):
        return None
    from .semantic import format_catalytic_report, format_catalytic_workflow_report

    _emit(progress, "Local fast-path: catalytic-residue scoring")
    if any(word in lowered for word in ("triage", "workflow", "review", "test", "검증", "최적화", "확인")):
        return format_catalytic_workflow_report(session)
    return format_catalytic_report(session)


def _membrane_fastpath(session, prompt, lowered, progress, executor):
    if not any(
        word in lowered
        for word in (
            "membrane",
            "bilayer",
            "lipid",
            "transmembrane",
            "tm protein",
            "hydrophobic slab",
            "opm",
            "ppm",
            "charmm-gui",
            "charmmgui",
            "memgen",
            "mlp",
            "막",
            "멤브레인",
            "지질막",
            "막단백",
            "막 단백",
            "소수성",
        )
    ):
        return None
    from .membrane import apply_membrane_mlp, format_membrane_report, launch_membrane_builder_sites, run_membrane_view

    _emit(progress, "Local fast-path: membrane analysis/view")
    if any(word in lowered for word in ("clear", "delete", "remove", "지워", "삭제", "없애")):
        from .membrane import clear_virtual_membrane

        return clear_virtual_membrane(session, executor=executor)
    if any(word in lowered for word in ("report", "analy", "분석", "추정", "판단", "tm-like")) and not any(
        word in lowered for word in ("view", "show", "fill", "채워", "표시", "띄워")
    ):
        return format_membrane_report(session)
    if any(word in lowered for word in ("charmm", "memgen", "opm", "ppm", "builder", "server", "web", "external", "외부", "서버")):
        if "memgen" in lowered:
            return launch_membrane_builder_sites(session, "memgen", executor=executor)
        if "charmm" in lowered:
            return launch_membrane_builder_sites(session, "charmm", executor=executor)
        if "opm" in lowered or "ppm" in lowered:
            return launch_membrane_builder_sites(session, "opm", executor=executor)
        return launch_membrane_builder_sites(session, "web", executor=executor)
    if "mlp" in lowered or "hydrophobic" in lowered or "소수성" in lowered:
        return apply_membrane_mlp(session, executor=executor)
    return run_membrane_view(session, executor=executor)


def _pisa_fastpath(session, prompt, lowered, progress, executor):
    if not any(
        word in lowered
        for word in (
            "pisa",
            "pdbe pisa",
            "pdbe-pisa",
            "interface area",
            "interface surface",
            "buried surface",
            "buried area",
            "bsa",
            "접촉면",
            "접촉 면",
            "계면",
            "매몰 면적",
            "인터페이스 면적",
        )
    ):
        return None
    from .pisa import format_pisa_report, run_pisa_view

    if any(word in lowered for word in ("web", "online", "server", "open", "pdbe", "웹", "서버")):
        _emit(progress, "Local fast-path: PDBePISA web export")
        return run_pisa_view(session, "web", executor=executor)
    if any(word in lowered for word in ("report", "summary", "analy", "분석", "요약", "정보")):
        _emit(progress, "Local fast-path: PISA-like interface report")
        return format_pisa_report(session)
    _emit(progress, "Local fast-path: PISA-like interface measurement")
    return run_pisa_view(session, "view", executor=executor)


def _residue_visual_fastpath(session, prompt, lowered, progress, executor):
    if not any(word in lowered for word in ("residue", "residues", "잔기", "label residue", "표시해", "보여줘")):
        return None
    result = _run_residue_view(session, prompt, executor=executor)
    if result is None:
        return None
    _emit(progress, "Local fast-path: residue/range highlight")
    return result


def _manipulation_fastpath(session, prompt, lowered, progress, executor):
    result = _run_manipulation_phrase(session, prompt, executor=executor)
    if result is None:
        return None
    _emit(progress, "Local fast-path: manipulation")
    return result


def _appearance_fastpath(session, prompt, lowered, progress, executor):
    if not any(
        word in lowered
        for word in (
            "surface", "cartoon", "ribbon", "stick", "sticks", "residue", "residues", "palette",
            "색", "컬러", "color", "colour", "팔레트", "무지개", "rainbow", "bychain", "bymodel",
            "byelement", "투명", "transparent", "transparency", "hide", "show", "fromatoms", "fromcartoons",
        )
    ):
        return None
    result = _run_appearance_phrase(session, prompt, executor=executor)
    if result is None:
        return None
    _emit(progress, "Local fast-path: appearance")
    return result


def _figure_control_fastpath(session, _prompt, lowered, progress, executor):
    if "figure" not in lowered and "그림" not in lowered:
        return None
    if any(word in lowered for word in ("next", "다음")):
        _emit(progress, "Local fast-path: figure next")
        return _run_figure(session, "next", executor=executor)
    if any(word in lowered for word in ("cycle", "순환", "돌려")):
        _emit(progress, "Local fast-path: figure cycle")
        return _run_figure(session, "cycle", executor=executor)
    if any(word in lowered for word in ("back", "previous", "이전", "되돌")):
        _emit(progress, "Local fast-path: figure back")
        return _run_figure(session, "back", executor=executor)
    if any(word in lowered for word in ("repeat", "again", "다시")):
        _emit(progress, "Local fast-path: figure repeat")
        return _run_figure(session, "repeat", executor=executor)
    if any(word in lowered for word in ("lab", "recommend", "추천", "추천해")):
        _emit(progress, "Local fast-path: figure lab")
        return _run_figure(session, "lab", executor=executor)
    return None


def _site_fastpath(session, prompt, lowered, progress, executor):
    if not any(word in lowered for word in ("site", "highlight", "show", "선택", "강조")):
        return None
    if "all" in lowered or "전체" in lowered or "전부" in lowered:
        _emit(progress, "Local fast-path: highlight all major sites")
        return _run_site(session, "all", executor=executor)
    if any(word in lowered for word in ("metal", "zn", "mg", "mn", "fe", "활성부위", "catalytic")):
        if "catalytic" in lowered or "촉매" in lowered:
            _emit(progress, "Local fast-path: highlight catalytic candidates")
            return _run_site(session, "catalytic", executor=executor)
        _emit(progress, "Local fast-path: highlight metal site")
        return _run_site(session, "metal", executor=executor)
    if any(word in lowered for word in ("ligand", "substrate", "pocket", "리간드")):
        _emit(progress, "Local fast-path: highlight ligand pocket")
        return _run_site(session, "ligand", executor=executor)
    if "interface" in lowered:
        _emit(progress, "Local fast-path: highlight interface")
        return _run_site(session, "interface", executor=executor)
    return None


def _motif_visual_fastpath(session, prompt, lowered, progress, executor):
    if "motif" not in lowered and "패턴" not in lowered:
        return None
    if not any(word in lowered for word in ("view", "show", "highlight", "label", "표시", "강조")):
        return None
    _emit(progress, "Local fast-path: motif highlight")
    model_hint, motif_text = _parse_motif_args(prompt)
    return _run_motif_view(session, motif_text=motif_text, model_hint=model_hint, executor=executor)


def _interface_visual_fastpath(session, _prompt, lowered, progress, executor):
    if "interface" not in lowered and "접촉" not in lowered and "oligomer" not in lowered:
        return None
    if not any(word in lowered for word in ("view", "show", "figure", "publication", "논문", "강조", "highlight")):
        return None
    _emit(progress, "Local fast-path: interface figure view")
    return _run_interface_view(session, executor=executor)


def _explode_visual_fastpath(session, _prompt, lowered, progress, executor):
    if not any(word in lowered for word in ("explode", "spread", "separate", "apart", "분리", "벌려")):
        return None
    if not any(word in lowered for word in ("figure", "view", "show", "layout", "publication", "논문")):
        return None
    _emit(progress, "Local fast-path: exploded assembly view")
    spacing = _extract_percent(lowered)
    suffix = f" {spacing}" if spacing is not None else ""
    return _run_figure(session, f"explode{suffix}", executor=executor)


def _selection_visual_fastpath(session, _prompt, lowered, progress, executor):
    if not any(word in lowered for word in ("selected", "selection", "선택")):
        return None
    if not any(word in lowered for word in ("figure", "view", "show", "publication", "논문", "focus")):
        return None
    _emit(progress, "Local fast-path: selection-focused figure view")
    if any(word in lowered for word in ("composite", "overview", "all", "summary", "요약")):
        return _run_figure(session, "selection-composite", executor=executor)
    if "interface" in lowered:
        return _run_figure(session, "selection-interface", executor=executor)
    if "motif" in lowered:
        return _run_figure(session, "selection-motif", executor=executor)
    if any(word in lowered for word in ("pocket", "active", "ligand", "metal")):
        return _run_figure(session, "selection-pocket", executor=executor)
    return _run_figure(session, "selection", executor=executor)


def _domains_visual_fastpath(session, _prompt, lowered, progress, executor):
    if not any(word in lowered for word in ("domain", "chunk", "도메인")):
        return None
    prompt = _prompt or ""
    model_hint = _extract_model_hint(session, prompt)
    if any(word in lowered for word in ("더 쪼개", "세분화", "잘게", "디테일", "자세히", "세세하게", "더 많이 나눠", "finer", "fine-grain", "split more", "more split", "more domains", "more detailed")):
        _emit(progress, "Local fast-path: refine domains (finer)")
        return _run_domain_split_refinement(session, "finer", model_hint=model_hint, executor=executor)
    if any(word in lowered for word in ("덜 쪼개", "합쳐", "덜 디테일", "단순하게", "coarser", "coarse", "merge", "less split", "less detailed")):
        _emit(progress, "Local fast-path: refine domains (coarser)")
        return _run_domain_split_refinement(session, "coarser", model_hint=model_hint, executor=executor)
    if any(word in lowered for word in ("원래대로", "reset", "기본값", "default")):
        _emit(progress, "Local fast-path: reset domain refinement")
        return _run_domain_split_refinement(session, "reset", model_hint=model_hint, executor=executor)
    if any(word in lowered for word in ("status", "설정", "현재")) and not any(word in lowered for word in ("view", "show", "figure", "color", "색", "selection", "select", "publication", "논문")):
        _emit(progress, "Local fast-path: domain split status")
        return _domain_split_status(session)
    if not any(word in lowered for word in ("view", "show", "figure", "color", "색", "selection", "select", "기본", "publication", "논문")):
        return None
    _emit(progress, "Local fast-path: domain-aware structure view")
    return _run_domains_view(session, model_hint, executor=executor)


def _roles_visual_fastpath(session, _prompt, lowered, progress, executor):
    if not any(word in lowered for word in ("role", "scaffold", "assembly", "active-site", "active site", "chain group", "multimer", "oligomer", "complex", "멀티머", "복합체")):
        return None
    if not any(word in lowered for word in ("view", "show", "figure", "color", "group", "publication", "논문")):
        return None
    _emit(progress, "Local fast-path: chain-role structure view")
    return _run_roles_view(session, None, executor=executor)


def _publication_fastpath(session, _prompt, lowered, progress, executor):
    beautify = _is_beautify_request(lowered)
    if not beautify and not any(word in lowered for word in ("publication", "paper figure", "figure for paper", "논문", "그림", "figure")):
        return None
    if any(word in lowered for word in ("save", "snapshot", "screenshot")):
        return None
    if any(word in lowered for word in ("multimer", "oligomer", "assembly", "complex", "복합체", "멀티머")):
        _emit(progress, "Local fast-path: applying assembly figure style")
        return _run_figure(session, "assembly", executor=executor)
    if any(word in lowered for word in ("explode", "spread", "separate", "분리", "벌려")):
        _emit(progress, "Local fast-path: applying exploded figure style")
        if any(word in lowered for word in ("composite", "overview", "site", "active", "pocket")):
            return _run_figure(session, "explode-composite", executor=executor)
        return _run_figure(session, "explode", executor=executor)
    if any(word in lowered for word in ("composite", "overview", "overview figure", "요약 그림", "전체 그림")):
        _emit(progress, "Local fast-path: applying composite figure style")
        if any(word in lowered for word in ("selected", "selection", "선택", "pocket", "active site", "활성부위")):
            return _run_figure(session, "selection-pocket", executor=executor)
        return _run_figure(session, "composite", executor=executor)
    if "interface" in lowered or "접촉" in lowered:
        _emit(progress, "Local fast-path: applying interface figure style")
        return _run_figure(session, "interface", executor=executor)
    if any(word in lowered for word in ("pocket", "active site", "active-site", "활성부위", "촉매")):
        _emit(progress, "Local fast-path: applying pocket figure style")
        return _run_figure(session, "pocket", executor=executor)
    if beautify:
        _emit(progress, "Local fast-path: applying clean structure figure style")
        return _run_figure(session, "clean", executor=executor)
    _emit(progress, "Local fast-path: applying publication figure style")
    return _run_figure(session, "publication", executor=executor)


def _transparency_fastpath(session, _prompt, lowered, progress, executor):
    if not any(word in lowered for word in ("transparent", "transparency", "투명", "불투명")):
        return None
    result = _run_transparency_from_text(session, lowered, executor=executor)
    if result is None:
        return None
    _emit(progress, "Local fast-path: transparency")
    return result


def _background_fastpath(session, _prompt, lowered, progress, executor):
    if "background" not in lowered and "bg" not in lowered:
        return None
    for color in ("black", "white", "gray", "grey"):
        if color in lowered:
            actual = "gray" if color == "grey" else color
            _emit(progress, f"Local fast-path: background -> {actual}")
            _run(session, f"set bgColor {actual}", executor=executor)
            return _format_local_result(f"set bgColor {actual}")
    return None


def _align_fastpath(session, prompt, lowered, progress, executor):
    if not any(word in lowered for word in ("align", "superimpose", "overlay")):
        return None
    from .semantic import match_models_from_text

    models = match_models_from_text(session, prompt, limit=2)
    if len(models) != 2:
        return None
    mobile, reference = models[1], models[0]
    command = f"mmaker {mobile} to {reference}"
    if any(word in lowered for word in ("core", "catalytic", "conserved")):
        command += " cutoffDistance 1.5"
    _emit(progress, f"Local fast-path: align {mobile} -> {reference}")
    _run(session, command, executor=executor)
    return _format_local_result(command)


def _style_fastpath(session, _prompt, lowered, progress, executor):
    mapping = {
        "cartoon": "cartoon",
        "ribbon": "cartoon",
        "sticks": "style stick",
        "stick": "style stick",
        "surface": "surface",
    }
    for key, base_cmd in mapping.items():
        if key in lowered:
            target = _selection_target(session)
            command = f"{base_cmd} {target}".strip()
            if "hide" in lowered and key == "surface":
                command = f"~surface {target}".strip()
            _emit(progress, f"Local fast-path: style -> {command}")
            _run(session, command, executor=executor)
            return _format_local_result(command)
    return None


def _color_fastpath(session, _prompt, lowered, progress, executor):
    mapping = {
        "by chain": "color bychain",
        "bychain": "color bychain",
        "chain color": "color bychain",
        "by model": "color bymodel",
        "bymodel": "color bymodel",
        "rainbow": "rainbow",
    }
    for key, base_cmd in mapping.items():
        if key in lowered:
            target = _selection_target(session)
            command = f"{base_cmd} {target}".strip()
            _emit(progress, f"Local fast-path: color -> {command}")
            _run(session, command, executor=executor)
            return _format_local_result(command)
    return None


def _focus_command_from_text(text):
    lowered = str(text or "").lower()

    global_cues = (
        "view all",
        "focus all",
        "fit all",
        "reset camera",
        "camera reset",
        "reset view",
        "show all",
        "전체 다",
        "전체가",
        "전체를",
        "전체 보기",
        "전체 보이",
        "전체 보여",
        "전부 보이",
        "전부 보여",
        "한눈에",
        "카메라 리셋",
        "시야 리셋",
        "줌 풀",
        "축소해서 전체",
        "멀리서",
        "전체 구조",
    )
    local_cues = (
        "view sel",
        "focus sel",
        "selection",
        "selected",
        "sel",
        "선택",
        "가까이",
        "클로즈업",
        "확대",
    )
    action_cues = (
        "focus",
        "zoom",
        "view",
        "fit",
        "camera",
        "show",
        "보이",
        "보여",
        "보게",
        "보이게",
        "맞춰",
        "리셋",
        "reset",
    )

    if any(token in lowered for token in global_cues):
        return "view all"
    if any(token in lowered for token in local_cues):
        return "view sel"
    if any(token in lowered for token in action_cues):
        if "all" in lowered or "전체" in lowered or "전부" in lowered:
            return "view all"
        return "view sel"
    return None


def _focus_fastpath(session, _prompt, lowered, progress, executor):
    command = _focus_command_from_text(lowered)
    if command is None:
        return None
    _emit(progress, f"Local fast-path: focus -> {command}")
    _run(session, command, executor=executor)
    return _format_local_result(command)


def _snapshot_fastpath(session, _prompt, lowered, progress, executor):
    if not any(word in lowered for word in ("snapshot", "screenshot", "save image", "save png")):
        return None
    publication = any(word in lowered for word in ("publication", "paper", "논문"))
    result = _run_snapshot(session, None, executor=executor, publication=publication)
    _emit(progress, "Local fast-path: snapshot")
    return result


def _run_style(session, arg, executor=None):
    mode = arg.strip().lower()
    target = _selection_target(session)
    mapping = {
        "cartoon": "cartoon",
        "ribbon": "cartoon",
        "sticks": "style stick",
        "stick": "style stick",
        "surface": "surface",
        "hide-surface": "~surface",
    }
    if mode not in mapping:
        return "usage: /style cartoon|sticks|surface|hide-surface"
    command = f"{mapping[mode]} {target}".strip()
    _run(session, command, executor=executor)
    return _format_local_result(command)


def _run_color(session, arg, executor=None):
    scheme = arg.strip().lower()
    target = _selection_target(session)
    mapping = {
        "chain": "color bychain",
        "model": "color bymodel",
        "rainbow": "rainbow",
    }
    if scheme not in mapping:
        return "usage: /color chain|model|rainbow"
    command = f"{mapping[scheme]} {target}".strip()
    _run(session, command, executor=executor)
    return _format_local_result(command)


_PALETTE_LEVEL_KEYS = {
    "chains": "chains",
    "chain": "chains",
    "structures": "structures",
    "structure": "structures",
    "models": "structures",
    "model": "structures",
    "residues": "residues",
    "residue": "residues",
    "polymer": "polymer",
    "polymers": "polymer",
}

_PALETTE_TARGET_KEYS = {
    "atoms": "a",
    "atom": "a",
    "cartoon": "c",
    "cartoons": "c",
    "ribbon": "c",
    "surface": "s",
    "surfaces": "s",
    "all": "acs",
}

_KNOWN_PALETTES = {
    "viridis", "plasma", "inferno", "magma", "cividis",
    "rainbow", "spectrum", "rwb", "bwr", "rgb", "rby", "ylorrd",
    "blues", "greens", "reds", "greys", "purples",
    "paired", "set1", "set2", "set3", "tableau", "tab10",
    "redbluepurple",
}


def _parse_palette_request(text):
    """Split a /palette request into (palette_name, level, target_letters).

    Defaults: palette_name='viridis', level='chains', target_letters=''.
    Unknown palette tokens fall back to the default; level/target are
    matched against known keys so arbitrary user input can't slip through
    to ChimeraX as a raw command.
    """
    palette_name = "viridis"
    level = "chains"
    target_letters = ""
    for tok in str(text or "").split():
        low = tok.lower()
        if low in _PALETTE_LEVEL_KEYS:
            level = _PALETTE_LEVEL_KEYS[low]
            continue
        if low in _PALETTE_TARGET_KEYS:
            target_letters = _PALETTE_TARGET_KEYS[low]
            continue
        if low in _KNOWN_PALETTES:
            palette_name = low
            continue
    return palette_name, level, target_letters


def _run_palette(session, arg, executor=None):
    text = (arg or "").strip()
    if not text or text.lower() in {"list", "ls", "show"}:
        command = "palette list"
        _run(session, command, executor=executor)
        return _format_local_result(command)

    palette_name, level, target_letters = _parse_palette_request(text)
    target = _selection_target(session)
    spec = f"{target} " if target else ""
    parts = [f"rainbow {spec}{level}"]
    if target_letters:
        parts.append(f"target {target_letters}")
    parts.append(f"palette {palette_name}")
    command = " ".join(part.strip() for part in parts if part.strip())
    command = " ".join(command.split())
    _run(session, command, executor=executor)
    return _format_local_result(command)


def _run_show_hide(session, action, arg, executor=None):
    command = _show_hide_command_from_text(session, action, arg)
    if command is None:
        return f"usage: /{action} [spec] [atoms|cartoons|surfaces|models]"
    _run(session, command, executor=executor)
    return _format_local_result(command)


def _run_select(session, arg, executor=None):
    command = _select_command_from_text(session, arg)
    if command is None:
        return "usage: /select <spec|clear>"
    _run(session, command, executor=executor)
    return _format_local_result(command)


def _run_distance(session, arg, executor=None):
    command = _distance_command_from_text(session, arg)
    if command is None:
        return "usage: /distance <spec1> <spec2>"
    _run(session, command, executor=executor)
    return _format_local_result(command)


def _run_angle(session, arg, executor=None):
    command = _angle_command_from_text(session, arg)
    if command is None:
        return "usage: /angle <spec1> <spec2> <spec3> [spec4]"
    _run(session, command, executor=executor)
    return _format_local_result(command)


def _run_buriedarea(session, arg, executor=None):
    command = _buriedarea_command_from_text(session, arg)
    if command is None:
        return "usage: /buriedarea <spec1> <spec2>"
    _run(session, command, executor=executor)
    return _format_local_result(command)


def _run_measure_area(session, arg, executor=None):
    command = _measure_area_command_from_text(session, arg)
    if command is None:
        return "usage: /measurearea [spec]"
    _run(session, command, executor=executor)
    return _format_local_result(command)


def _run_contact_area(session, arg, executor=None):
    command = _contactarea_command_from_text(session, arg)
    if command is None:
        return "usage: /contactarea <surf1> <surf2>"
    _run(session, command, executor=executor)
    return _format_local_result(command)


def _run_convexity(session, arg, executor=None):
    command = _convexity_command_from_text(session, arg)
    if command is None:
        return "usage: /convexity [spec]"
    _run(session, command, executor=executor)
    return _format_local_result(command)


def _run_hbonds(session, arg, executor=None):
    command = _hbonds_command_from_text(session, arg)
    if command is None:
        return "usage: /hbonds [spec]"
    _run(session, command, executor=executor)
    return _format_local_result(command)


def _run_delete_contacts_like(session, kind, executor=None):
    command = f"{kind} delete" if kind == "hbonds" else f"~{kind}"
    _run(session, command, executor=executor)
    return _format_local_result(command)


def _run_contacts(session, arg, executor=None, kind="contacts"):
    command = _contacts_command_from_text(session, arg, kind=kind)
    if command is None:
        return f"usage: /{kind} [spec]"
    _run(session, command, executor=executor)
    return _format_local_result(command)


def _run_zone_select(session, arg, executor=None):
    command = _zone_command_from_text(session, arg)
    if command is None:
        return "usage: /zone <cutoff> [spec]"
    _run(session, command, executor=executor)
    return _format_local_result(command)


def _run_surface_zone(session, arg, executor=None):
    command = _surface_zone_command_from_text(session, arg)
    if command is None:
        return "usage: /surfacezone <cutoff> [spec]"
    _run(session, command, executor=executor)
    return _format_local_result(command)


def _run_surface_unzone(session, arg, executor=None):
    command = _surface_unzone_command_from_text(arg)
    if command is None:
        return "usage: /surfaceunzone [spec]"
    _run(session, command, executor=executor)
    return _format_local_result(command)


def _run_interfaces(session, arg, executor=None):
    command = _interfaces_command_from_text(session, arg)
    if command is None:
        return "usage: /interfaces [spec]"
    _run(session, command, executor=executor)
    return _format_local_result(command)


def _run_interfaces_select(session, arg, executor=None):
    command = _interfaces_select_command_from_text(session, arg)
    if command is None:
        return "usage: /interfacesselect <spec1> <spec2>"
    _run(session, command, executor=executor)
    return _format_local_result(command)


def _run_turn(session, arg, executor=None):
    command = _turn_command_from_text(arg)
    if command is None:
        return "usage: /turn <x|y|z> <angle>"
    _run(session, command, executor=executor)
    return _format_local_result(command)


def _run_rock(session, arg, executor=None):
    command = _rock_command_from_text(arg)
    if command is None:
        return "usage: /rock [axis angle]"
    _run(session, command, executor=executor)
    return _format_local_result(command)


def _run_wobble(session, arg, executor=None):
    command = _wobble_command_from_text(arg)
    if command is None:
        return "usage: /wobble [axis angle]"
    _run(session, command, executor=executor)
    return _format_local_result(command)


def _run_zoom(session, arg, executor=None):
    command = _zoom_command_from_text(arg)
    if command is None:
        return "usage: /zoom [factor]"
    _run(session, command, executor=executor)
    return _format_local_result(command)


def _run_wait(session, arg, executor=None):
    command = _wait_command_from_text(arg)
    if command is None:
        return "usage: /wait [frames]"
    _run(session, command, executor=executor)
    return _format_local_result(command)


def _run_stop(session, executor=None):
    command = "stop"
    _run(session, command, executor=executor)
    return _format_local_result(command)


def _run_close(session, arg, executor=None):
    command = _close_command_from_text(arg)
    if command is None:
        return "usage: /close [spec]"
    _run(session, command, executor=executor)
    return _format_local_result(command)


def _run_focus(session, arg, executor=None):
    command = _focus_command_from_text(arg or "sel") or "view sel"
    _run(session, command, executor=executor)
    return _format_local_result(command)


def _run_transparency(session, arg, executor=None):
    parsed = _parse_transparency_arg(arg)
    if parsed is None:
        return "usage: /transparency <percent> [surface|cartoon|atoms|all]"
    percent, target_kind = parsed
    return _apply_transparency(session, percent, target_kind, executor=executor)


def _run_appearance_phrase(session, text, executor=None):
    commands, residue_specs = _build_appearance_commands(session, text)
    if not commands:
        return None

    for command in commands:
        _run(session, command, executor=executor)
    if residue_specs:
        _clear_selection(session)
    return "\n".join(
        [
            "Appearance request applied.",
            "Executed ChimeraX commands:",
            *[f"- {command}" for command in commands],
        ]
    )


def _run_manipulation_phrase(session, text, executor=None):
    commands = _build_manipulation_commands(session, text)
    if not commands:
        return None
    deduped = []
    for command in commands:
        if command not in deduped:
            deduped.append(command)
    for command in deduped:
        _run(session, command, executor=executor)
    return "\n".join(
        [
            "Manipulation request applied.",
            "Executed ChimeraX commands:",
            *[f"- {command}" for command in deduped],
        ]
    )


def _run_residue_view(session, arg, executor=None):
    from .semantic import extract_residue_specs_from_text

    specs = extract_residue_specs_from_text(session, arg)
    if not specs:
        return None

    commands = [
        *_publication_base_commands(""),
        "select " + " ".join(specs),
        "name frozen residue_focus sel",
        *_selection_stick_style_commands("gold"),
        "label sel residues",
        "view sel",
    ]
    for command in commands:
        _run(session, command, executor=executor)
    _clear_selection(session)
    return "\n".join(
        [
            "Residue focus view applied.",
            "Executed ChimeraX commands:",
            *[f"- {command}" for command in commands],
        ]
    )


def _run_site(session, arg, executor=None, preserve_existing=False):
    from .semantic import best_interface_pair, best_ligand_site, best_metal_site

    raw = (arg or "").strip()
    tokens = raw.split()
    residues_n = None
    keep_tokens = []
    for tok in tokens:
        lower_tok = tok.lower()
        if lower_tok.startswith("residues=") or lower_tok.startswith("res=") or lower_tok.startswith("top="):
            try:
                residues_n = max(1, min(50, int(tok.split("=", 1)[1])))
            except Exception:
                pass
        else:
            keep_tokens.append(tok)
    if residues_n is not None:
        session._codex_site_residues_n = residues_n
    try:
        n = max(1, min(50, int(getattr(session, "_codex_site_residues_n", 12) or 12)))
    except (TypeError, ValueError):
        n = 12
    target = " ".join(keep_tokens).strip().lower()
    if not target and residues_n is not None:
        return f"Site residue cap set to {n}. Provide a target (metal|ligand|interface|catalytic|all)."
    if target == "all":
        blocks = []
        for site_kind in ("catalytic", "metal", "ligand", "interface"):
            result = _run_site(session, site_kind, executor=executor, preserve_existing=preserve_existing)
            if result and not result.startswith("No "):
                blocks.extend(result.splitlines())
        if not blocks:
            return "No highlightable sites detected."
        return "\n".join(["All major site selections attempted.", *blocks])

    if target == "metal":
        site = best_metal_site(session)
        if site is None:
            return "No metal site detected."
        specs = [site["metal_spec"], *site["site_residue_specs"][:n]]
        commands = [
            *([] if preserve_existing else _publication_base_commands("")),
            "select " + " ".join(specs),
            "name frozen site_metal sel",
            "view sel",
            *_selection_stick_style_commands("orange"),
            "label sel residues",
        ]
        for command in commands:
            _run(session, command, executor=executor)
        restore_charge_colors(session, " ".join(specs))
        _clear_selection(session)
        return "\n".join(
            [
                "Metal site highlighted.",
                "Executed ChimeraX commands:",
                *[f"- {command}" for command in commands],
            ]
        )

    if target == "ligand":
        site = best_ligand_site(session)
        if site is None:
            return "No ligand pocket detected."
        specs = [site["ligand_spec"], *[f"{site['model_spec']}/{hit['chain_id']}:{int(hit['number'])}" for hit in site["nearby"][:n]]]
        commands = [
            *([] if preserve_existing else _publication_base_commands("")),
            "select " + " ".join(specs),
            "name frozen site_ligand sel",
            "view sel",
            *_selection_stick_style_commands("cornflowerblue"),
            "label sel residues",
        ]
        for command in commands:
            _run(session, command, executor=executor)
        restore_charge_colors(session, " ".join(specs))
        _clear_selection(session)
        return "\n".join(
            [
                "Ligand pocket highlighted.",
                "Executed ChimeraX commands:",
                *[f"- {command}" for command in commands],
            ]
        )

    if target == "interface":
        pair = best_interface_pair(session)
        if pair is None:
            return "No interface pair detected."
        commands = [
            *([] if preserve_existing else _publication_base_commands("")),
            f"interfaces select {pair['chain_a_spec']} contacting {pair['chain_b_spec']} bothSides true",
            "name frozen site_interface sel",
            "view sel",
            *_selection_stick_style_commands("hotpink"),
            "label sel residues",
        ]
        for command in commands:
            _run(session, command, executor=executor)
        _clear_selection(session)
        return "\n".join(
            [
                "Interface residues highlighted.",
                "Executed ChimeraX commands:",
                *[f"- {command}" for command in commands],
            ]
        )

    if target == "catalytic":
        from .semantic import best_catalytic_candidates

        candidates = best_catalytic_candidates(session)
        if not candidates:
            return "No catalytic candidates detected."
        specs = [c["residue_spec"] for c in candidates[:n]]
        commands = [
            *([] if preserve_existing else _publication_base_commands("")),
            "select " + " ".join(specs),
            "name frozen site_catalytic sel",
            "view sel",
            *_selection_stick_style_commands("gold"),
            "label sel residues",
        ]
        for command in commands:
            _run(session, command, executor=executor)
        restore_charge_colors(session, " ".join(specs))
        _clear_selection(session)
        return "\n".join(
            [
                "Catalytic candidates highlighted.",
                "Executed ChimeraX commands:",
                *[f"- {command}" for command in commands],
            ]
        )

    return "usage: /site metal|ligand|interface|catalytic|all [residues=N]"


def _run_snapshot(session, filename, executor=None, publication=False):
    from chimerax.core.commands import StringArg

    raw = str(filename or "").strip()
    width = 2400
    height = 1800
    supersample = 3
    kept = []
    for tok in raw.split():
        lower = tok.lower()
        if lower.startswith("w=") or lower.startswith("width="):
            try: width = max(200, min(8000, int(lower.split("=", 1)[1])))
            except: pass
        elif lower.startswith("h=") or lower.startswith("height="):
            try: height = max(200, min(8000, int(lower.split("=", 1)[1])))
            except: pass
        elif lower.startswith("ss=") or lower.startswith("supersample="):
            try: supersample = max(1, min(8, int(lower.split("=", 1)[1])))
            except: pass
        else:
            kept.append(tok)
    cleaned_filename = " ".join(kept)
    if cleaned_filename:
        path = Path(cleaned_filename).expanduser()
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        suffix = "_publication" if publication else ""
        path = Path.cwd() / f"chimeraX_snapshot_{stamp}{suffix}.png"
    quoted = StringArg.unparse(str(path))
    commands = []
    if publication:
        commands.extend(
            [
                "set bgColor white",
                f"cartoon style width {PUBLICATION_CARTOON_WIDTH} thick {PUBLICATION_CARTOON_THICK}",
                "lighting soft",
                f"graphics silhouettes true width {PUBLICATION_SILHOUETTE_WIDTH} color {PUBLICATION_SILHOUETTE_COLOR} depthJump {PUBLICATION_SILHOUETTE_DEPTH_JUMP}",
                f"save {quoted} width {width} height {height} supersample {supersample}",
            ]
        )
    else:
        commands.append(f"save {quoted}")
    for command in commands:
        _run(session, command, executor=executor)
    return "\n".join(
        [
            "Local ChimeraX action executed." if not publication else "Local ChimeraX publication snapshot saved.",
            "Executed ChimeraX commands:",
            *[f"- {command}" for command in commands],
            f"Saved: {path}",
        ]
    )


def _scene_bookmarks(session):
    """Return the live scene-bookmarks dict, lazy-initializing it on the
    session so callers' mutations persist. Previously returned a fresh
    {} default on first read, silently dropping the first /scene save.
    """
    bookmarks = getattr(session, "_codex_bridge_scene_bookmarks", None)
    if bookmarks is None:
        bookmarks = {}
        session._codex_bridge_scene_bookmarks = bookmarks
    return bookmarks


def _scene_default_name():
    return "scene_" + datetime.now().strftime("%Y%m%d_%H%M%S")


def _scene_render_state(session):
    ui = getattr(session, "ui", None)
    if ui is None or not getattr(ui, "is_gui", False):
        return {}
    view = getattr(session, "main_view", None)
    if view is None:
        return {}

    background = getattr(view, "background_color", None)
    bg_hex = None
    try:
        bg_hex = "#" + "".join(f"{max(0, min(255, int(round(float(channel) * 255)))):02x}" for channel in list(background)[:3])
    except Exception:
        bg_hex = None

    silhouette = getattr(view, "silhouette", None)
    state = {"background": bg_hex}
    if silhouette is not None:
        state["silhouettes"] = bool(getattr(silhouette, "enabled", False))
        state["silhouette_width"] = float(getattr(silhouette, "thickness", 1.0) or 1.0)
        state["silhouette_depth_jump"] = float(getattr(silhouette, "depth_jump", 0.03) or 0.03)
        color = getattr(silhouette, "color", None)
        try:
            state["silhouette_color"] = "#" + "".join(
                f"{max(0, min(255, int(round(float(channel) * 255)))):02x}" for channel in list(color)[:3]
            )
        except Exception:
            state["silhouette_color"] = None
    return state


def _restore_scene_render_state(session, state, executor=None):
    if not state:
        return []
    commands = []
    bg_hex = state.get("background")
    if bg_hex:
        commands.append(f"set bgColor {bg_hex}")
    if "silhouettes" in state:
        enabled = "true" if state.get("silhouettes") else "false"
        width = state.get("silhouette_width")
        depth_jump = state.get("silhouette_depth_jump")
        color = state.get("silhouette_color")
        pieces = [f"graphics silhouettes {enabled}"]
        if width is not None:
            pieces.append(f"width {float(width):.3g}")
        if color:
            pieces.append(f"color {color}")
        if depth_jump is not None:
            pieces.append(f"depthJump {float(depth_jump):.3g}")
        commands.append(" ".join(pieces))
    for command in commands:
        _run(session, command, executor=executor)
    return commands


def _scene_metadata_snapshot(session, note=""):
    from .semantic import format_caption_draft, format_legend_report, format_panel_plan

    selection = _capture_selection_snapshot(session)
    caption_style = "selection" if selection.get("ranges") else "paper"
    return {
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "selection": selection,
        "figure_mode": _get_last_figure_mode(session),
        "render_state": _scene_render_state(session),
        "note": str(note or "").strip(),
        "caption": format_caption_draft(session, style=caption_style),
        "legend": format_legend_report(session),
        "panel_plan": format_panel_plan(session),
    }


def _scene_info_text(name, item):
    item = dict(item or {})
    lines = [f"Scene: {name}"]
    if item.get("saved_at"):
        lines.append(f"- saved_at: {item['saved_at']}")
    if item.get("figure_mode"):
        lines.append(f"- figure_mode: {item['figure_mode']}")
    selection = item.get("selection", {}) or {}
    if selection.get("ranges"):
        lines.append("- selection: " + ", ".join(selection["ranges"]))
    elif selection.get("models"):
        lines.append("- selection: " + ", ".join(selection["models"]))
    if item.get("note"):
        lines.extend(["- note:", *[f"  {line}" for line in str(item['note']).splitlines() if line.strip()]])
    if item.get("caption"):
        lines.extend(["- caption:", *[f"  {line}" for line in str(item["caption"]).splitlines() if line.strip()]])
    if item.get("legend"):
        lines.extend(["- legend:", *[f"  {line}" for line in str(item["legend"]).splitlines() if line.strip()]])
    if item.get("panel_plan"):
        lines.extend(["- panel_plan:", *[f"  {line}" for line in str(item["panel_plan"]).splitlines() if line.strip()]])
    return "\n".join(lines)


def _scene_phrase_request(text, lowered=None):
    lowered = lowered or normalized_prompt_for_matching(text)
    scene_words = ("scene", "bookmark", "장면", "씬", "북마크", "구도")
    state_words = ("이 상태", "현재 상태", "이 화면", "현재 화면", "이 구도", "현재 구도", "saved one", "저장한 거", "저장한것")

    save_words = ("save", "store", "remember", "bookmark", "저장", "기억", "북마크")
    load_words = ("load", "restore", "open", "recall", "불러", "복원", "되돌", "다시 열")
    list_words = ("list", "show", "what", "목록", "리스트", "뭐 있", "뭐있", "보여")
    delete_words = ("delete", "remove", "forget", "clear", "삭제", "지워", "없애")
    info_words = ("info", "details", "metadata", "정보", "상세")
    note_words = ("note", "memo", "메모", "노트")

    has_scene_reference = any(word in lowered for word in scene_words) or any(word in lowered for word in state_words)

    action = None
    if any(word in lowered for word in save_words):
        action = "save"
    elif any(word in lowered for word in load_words):
        action = "load"
    elif any(word in lowered for word in delete_words):
        action = "delete"
    elif any(word in lowered for word in info_words):
        action = "info"
    elif any(word in lowered for word in note_words):
        action = "note"
    elif has_scene_reference and any(word in lowered for word in list_words):
        action = "list"

    if action is None:
        return None
    if not has_scene_reference and action not in {"load", "info"}:
        return None

    quoted = re.findall(r'["\']([^"\']+)["\']', str(text or ""))
    name = None
    note = None
    if action == "note":
        if len(quoted) >= 2:
            name, note = quoted[0], quoted[1]
        elif len(quoted) == 1:
            note = quoted[0]
    elif quoted:
        name = quoted[0]

    if name is None:
        name = _extract_scene_name_token(text, action)
    if action == "note" and note is None:
        note = _extract_scene_note_text(text)

    return {"action": action, "name": name, "note": note}


def _extract_scene_name_token(text, action):
    token_text = str(text or "")
    patterns = [
        r'(?:scene|bookmark|장면|씬|북마크|구도)\s+([A-Za-z0-9_.-]+)',
        r'(?:name|이름)\s+([A-Za-z0-9_.-]+)',
    ]
    if action in {"load", "delete", "info", "note"}:
        patterns.append(r'(?:load|restore|delete|remove|info|note|불러|복원|삭제|메모|정보)\s+([A-Za-z0-9_.-]+)')
    for pattern in patterns:
        match = re.search(pattern, token_text, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def _extract_scene_note_text(text):
    token_text = str(text or "").strip()
    match = re.search(r"(?:note|memo|메모|노트)\s+(.+)$", token_text, flags=re.IGNORECASE)
    if not match:
        return ""
    note = match.group(1).strip().strip("\"'")
    return note


def _scene_request_arg(request):
    action = request.get("action", "list")
    name = str(request.get("name") or "").strip()
    note = str(request.get("note") or "").strip()
    parts = [action]
    if name:
        parts.append(shlex.quote(name))
    if note and action in {"save", "note"}:
        parts.append(note)
    return " ".join(part for part in parts if str(part).strip())


def _run_scene(session, arg, executor=None):
    from chimerax.core.commands import StringArg

    text = str(arg or "").strip()
    action, rest = _split_action_arg(text)
    action = action or "list"
    bookmarks = _scene_bookmarks(session)

    if action in {"save", "name"}:
        parts = shlex.split(rest) if rest else []
        scene_name = parts[0] if parts else _scene_default_name()
        note_text = " ".join(parts[1:]).strip() if len(parts) > 1 else ""
        quoted_name = StringArg.unparse(scene_name)
        command = f"view name {quoted_name}"
        _run(session, command, executor=executor)
        bookmarks[scene_name] = _scene_metadata_snapshot(session, note=note_text)
        session._codex_bridge_last_scene_name = scene_name
        lines = [f"Scene saved: {scene_name}", "Executed ChimeraX commands:", f"- {command}"]
        if note_text:
            lines.append(f"- note: {note_text}")
        return "\n".join(lines)

    if action in {"load", "open", "restore"}:
        parts = shlex.split(rest) if rest else []
        scene_name = parts[0] if parts else getattr(session, "_codex_bridge_last_scene_name", None)
        if not scene_name:
            return "No saved scene name available. Use /scene save [name] first."
        frames = 15
        if len(parts) > 1:
            try:
                frames = max(1, min(600, int(parts[1])))
            except Exception:
                frames = 15
        quoted_name = StringArg.unparse(scene_name)
        command = f"view {quoted_name} {frames}"
        _run(session, command, executor=executor)
        bookmark = bookmarks.get(scene_name, {})
        render_commands = _restore_scene_render_state(session, bookmark.get("render_state"), executor=executor)
        _restore_selection_snapshot(session, bookmark.get("selection"), executor=executor)
        if bookmark.get("figure_mode"):
            _set_last_figure_mode(session, bookmark["figure_mode"])
        session._codex_bridge_last_scene_name = scene_name
        lines = [f"Scene loaded: {scene_name}", "Executed ChimeraX commands:", f"- {command}"]
        lines.extend(f"- {item}" for item in render_commands)
        return "\n".join(lines)

    if action in {"info", "show"}:
        target = rest.strip() or getattr(session, "_codex_bridge_last_scene_name", None)
        if not target:
            return "No saved scene name available. Use /scene save [name] first."
        item = bookmarks.get(target)
        if item is None:
            return f"No saved scene named: {target}"
        return _scene_info_text(target, item)

    if action == "note":
        parts = shlex.split(rest) if rest else []
        if not parts:
            return "usage: /scene note <name> <text>"
        if len(parts) == 1:
            scene_name = getattr(session, "_codex_bridge_last_scene_name", None)
            note_text = parts[0]
        else:
            scene_name = parts[0]
            note_text = " ".join(parts[1:]).strip()
        if not scene_name:
            return "No saved scene name available. Use /scene save [name] first."
        item = bookmarks.get(scene_name)
        if item is None:
            return f"No saved scene named: {scene_name}"
        item["note"] = note_text
        return f"Updated scene note: {scene_name}"

    if action in {"list", "ls"}:
        if not bookmarks:
            return "No saved scene bookmarks yet."
        lines = ["Saved scene bookmarks:"]
        for name in sorted(bookmarks):
            item = bookmarks[name]
            bits = [name]
            if item.get("figure_mode"):
                bits.append(f"figure {item['figure_mode']}")
            if item.get("saved_at"):
                bits.append(item["saved_at"])
            selection = item.get("selection", {}) or {}
            if selection.get("ranges"):
                bits.append("selection " + ", ".join(selection["ranges"][:2]))
            note = str(item.get("note", "") or "").strip()
            if note:
                bits.append("note " + (note[:40] + ("..." if len(note) > 40 else "")))
            lines.append("- " + " | ".join(bits))
        return "\n".join(lines)

    if action in {"delete", "remove", "rm"}:
        target = rest.strip()
        if not target:
            return "usage: /scene delete <name|all>"
        if target.lower() == "all":
            _run(session, "view delete all", executor=executor)
            bookmarks.clear()
            session._codex_bridge_last_scene_name = None
            return "Deleted all saved scenes."
        quoted_name = StringArg.unparse(target)
        _run(session, f"view delete {quoted_name}", executor=executor)
        bookmarks.pop(target, None)
        if getattr(session, "_codex_bridge_last_scene_name", None) == target:
            session._codex_bridge_last_scene_name = None
        return f"Deleted scene: {target}"

    return "usage: /scene save [name] [note...] | /scene load [name] [frames] | /scene info [name] | /scene note <name> <text> | /scene list | /scene delete <name|all>"


def _run_movie(session, arg, executor=None):
    command = _movie_command_from_text(arg)
    if command is None:
        return "usage: /movie <record|stop|encode|status|reset> [path]"
    _run(session, command, executor=executor)
    return _format_local_result(command)


def _default_chain_query(session, text=None):
    from .semantic import extract_chain_specs_from_text, get_session_semantics, resolve_default_model_spec, resolve_model_spec

    token_text = str(text or "").strip()
    chain_specs = extract_chain_specs_from_text(session, token_text)
    if chain_specs:
        return chain_specs[0]

    selection = get_session_semantics(session).get("selection", {})
    model_spec = resolve_model_spec(session, token_text) if token_text else None
    if not model_spec:
        for token in selection.get("ranges", []):
            if "/" in token:
                return token.split(":", 1)[0]

    model_spec = model_spec or resolve_default_model_spec(session)
    if not model_spec:
        return None
    semantics = get_session_semantics(session)
    for model in semantics.get("models", []):
        if model.get("spec") != model_spec or not model.get("atomic"):
            continue
        chains = model.get("chains", [])
        if len(chains) == 1:
            return f"{model_spec}/{chains[0]['id']}"
        for chain in chains:
            if chain.get("polymer_type") == "protein":
                return f"{model_spec}/{chain['id']}"
    return None


def _guess_uniprot_or_sequence(text):
    token_text = str(text or "").strip()
    if not token_text:
        return None
    quoted = re.findall(r'["\']([^"\']+)["\']', token_text)
    if quoted:
        return quoted[0].strip()
    if re.fullmatch(r"[A-Za-z0-9]{6,10}", token_text.replace(" ", "")):
        return token_text.replace(" ", "")
    letters = re.sub(r"[^A-Za-z]", "", token_text).upper()
    if len(letters) >= 20:
        return letters
    return None


def _run_seqview_tool(session, arg, executor=None):
    chain_spec = _default_chain_query(session, arg)
    if not chain_spec:
        return "No protein chain was resolved for Sequence Viewer."
    before = _alignment_ids(session)
    command = f"sequence chain {chain_spec}"
    _run(session, command, executor=executor)
    _remember_latest_alignment(session, before_ids=before)
    return _format_local_result(command)


def _run_blast_tool(session, arg, executor=None):
    token_text = str(arg or "").strip()
    query = _guess_uniprot_or_sequence(token_text) or _default_chain_query(session, token_text)
    if not query:
        return "No BLAST query was resolved. Select a protein chain or provide a sequence/UniProt ID."
    database = "pdb"
    lowered = normalized_prompt_for_matching(token_text)
    for db_name in ("alphafold", "esmfold", "nr", "uniref100", "uniref90", "uniref50", "pdb"):
        if db_name in lowered:
            database = db_name
            break
    instance_name = "codex_blast_" + datetime.now().strftime("%H%M%S")
    command = (
        f"blastprotein {query} database {database} "
        f"showResultsTable true showSequenceAlignment true onlyBest true name {instance_name}"
    )
    _run(session, command, executor=executor)
    session._codex_bridge_last_blast_name = instance_name
    return _format_local_result(command)


def _run_hhpred_tool(session, arg, executor=None):
    from .toolbar_actions import launch_sequence_analysis_site

    return launch_sequence_analysis_site(session, "hhpred")


def _run_signalp_tool(session, arg, executor=None):
    from .signalp import run_signalp_command_text

    return run_signalp_command_text(session, arg, executor=executor)


def _run_alphafold_tool(session, arg, executor=None):
    token_text = str(arg or "").strip()
    lowered = normalized_prompt_for_matching(token_text)
    query = _guess_uniprot_or_sequence(token_text) or _default_chain_query(session, token_text)
    if not query:
        return "No AlphaFold query was resolved. Select a protein chain or provide a sequence/UniProt ID."
    if any(word in lowered for word in ("fetch", "uniprot", "accession")) and re.fullmatch(r"[A-Za-z0-9]{6,10}", str(query)):
        command = f"alphafold fetch {query}"
    elif any(word in lowered for word in ("search", "검색")):
        command = f"alphafold search {query}"
    else:
        command = f"alphafold match {query}"
    _run(session, command, executor=executor)
    return _format_local_result(command)


def _run_similar_tool(session, arg, executor=None):
    token_text = str(arg or "").strip()
    # Extract count=N override BEFORE normalization (which lowercases tokens)
    hit_count = None
    remaining_tokens = []
    for tok in token_text.split():
        if tok.lower().startswith("count=") or tok.lower().startswith("n="):
            try:
                hit_count = max(1, min(20, int(tok.split("=", 1)[1])))
            except Exception:
                pass
        else:
            remaining_tokens.append(tok)
    token_text = " ".join(remaining_tokens)

    lowered = normalized_prompt_for_matching(token_text)
    before_sets = _similar_set_names(session)
    if not lowered or any(word in lowered for word in ("open", "load", "align", "aligned", "열어", "불러", "정렬", "얼라인")):
        from .toolbar_actions import launch_similar_open_aligned

        return launch_similar_open_aligned(session, count=(hit_count or 3), executor=executor)
    if any(word in lowered for word in ("sequences", "coverage", "sequence plot", "서열")):
        problem = _check_similar_results_query_chain(session, "Foldseek sequences")
        if problem:
            return problem
        command = _similar_command_with_from_set(session, "similarstructures sequences")
    elif any(word in lowered for word in ("traces", "trace", "백본", "backbone")):
        problem = _check_similar_results_query_chain(session, "Foldseek traces")
        if problem:
            return problem
        command = _similar_command_with_from_set(session, "similarstructures traces")
    elif any(word in lowered for word in ("ligand", "ligands", "pocket", "리간드")):
        problem = _check_similar_results_query_chain(session, "Foldseek ligands")
        if problem:
            return problem
        command = _similar_command_with_from_set(session, "similarstructures ligands")
    elif any(word in lowered for word in ("cluster", "umap", "scatter", "클러스터")):
        command = _similar_command_with_from_set(session, "similarstructures cluster")
    elif any(word in lowered for word in ("web", "browser", "rcsb", "웹", "브라우저")):
        from .toolbar_actions import launch_sequence_analysis_site

        return launch_sequence_analysis_site(session, "rcsb")
    else:
        from .toolbar_actions import launch_similar_open_aligned

        database = "afdb50" if any(word in lowered for word in ("alphafold", "afdb")) else "pdb100"
        return launch_similar_open_aligned(session, count=(hit_count or 3), database=database, executor=executor)
    _run(session, command, executor=executor)
    _remember_latest_similar_set(session, before_names=before_sets)
    return _format_local_result(command)


def _run_foldmason_tool(session, arg, executor=None):
    """`/foldmason [count=N] [no-similar]`

      count=N      Foldseek auto-fetch hit count if <2 structures open (default 5)
      no-similar   skip auto-Foldseek when only 1 structure open (return error)
    """
    from .toolbar_actions import launch_foldmason

    raw = str(arg or "").strip()
    similar_count = 5
    auto_similar = True
    for tok in raw.split():
        lower = tok.lower()
        if lower.startswith("count=") or lower.startswith("n="):
            try: similar_count = max(2, min(20, int(lower.split("=", 1)[1])))
            except: pass
        elif lower in ("no-similar", "no_similar", "manual"):
            auto_similar = False
    return launch_foldmason(session, executor=executor,
                            auto_similar=auto_similar, similar_count=similar_count)


def _run_folddisco_tool(session, arg, executor=None):
    from .toolbar_actions import launch_folddisco

    return launch_folddisco(session, executor=executor)


def _run_nucdock_tool(session, arg, executor=None):
    from .toolbar_actions import launch_nucleotide_docking_pipeline

    return launch_nucleotide_docking_pipeline(session, arg, executor=executor)


def _run_afcomplex_tool(session, arg, executor=None):
    from .toolbar_actions import launch_nucleotide_alphafold_pipeline

    return launch_nucleotide_alphafold_pipeline(session, arg)


def _run_boltz_tool(session, arg, executor=None):
    """`/boltz [panel|setup] [model=boltz1|boltz2]`

      panel        open native ChimeraX Boltz panel
      setup        show installation steps
      model=X      boltz1 or boltz2 (default boltz2)
    """
    from .toolbar_actions import launch_boltz_latest_predict, launch_boltz_panel

    raw = str(arg or "").strip()
    model = "boltz2"
    tokens = []
    for tok in raw.split():
        lower = tok.lower()
        if lower.startswith("model="):
            val = lower.split("=", 1)[1]
            if val in ("boltz1", "boltz2"):
                model = val
        else:
            tokens.append(tok)
    cleaned = " ".join(tokens)

    lowered = normalized_prompt_for_matching(cleaned)
    if any(word in lowered for word in ("panel", "gui", "native", "built-in", "builtin", "내장", "패널")):
        return launch_boltz_panel(session)
    if any(word in lowered for word in ("install", "setup", "설치", "세팅")):
        return _run_setup_tool(session, "boltz", executor=executor)
    return launch_boltz_latest_predict(session, model=model)


def _run_rapidock_tool(session, arg, executor=None):
    """`/rapidock [global|local] <peptide> [pocket=N] [engine=X] [n=K] [buffer=B]`

    Tunable args (all optional):
      pocket=N    pick Nth KVFinder pocket (1-based, default 1 = largest)
      engine=X   auto | hpepdock | docker | native (default auto)
      n=K        number of poses to generate (default 5)
      buffer=B   Å cube buffer around pocket lining (default 12.0)
    Run /cavity first to see available pockets + volumes.
    """
    from .toolbar_actions import launch_rapidock_prediction, _prompt_peptide_sequence

    action, target_arg = _split_action_arg(arg)
    mode = "global"
    if action in {"local", "pocket"}:
        mode = "local"
    elif action in {"global", "blind"}:
        mode = "global"
    raw = str((target_arg if action else arg) or "").strip()

    pocket_index = 1
    engine = "auto"
    n_samples = 5
    pocket_buffer = 12.0
    tokens = []
    for tok in raw.split():
        lower = tok.lower()
        if lower.startswith("pocket="):
            try: pocket_index = max(1, min(20, int(lower.split("=", 1)[1])))
            except: pass
        elif lower.startswith("engine="):
            val = lower.split("=", 1)[1].strip()
            if val in ("auto", "hpepdock", "docker", "native"):
                engine = val
        elif lower.startswith("n=") or lower.startswith("samples="):
            try: n_samples = max(1, min(20, int(lower.split("=", 1)[1])))
            except: pass
        elif lower.startswith("buffer="):
            try: pocket_buffer = max(4.0, min(30.0, float(lower.split("=", 1)[1])))
            except: pass
        else:
            tokens.append(tok)
    peptide = " ".join(tokens).strip()
    if not peptide:
        peptide = _prompt_peptide_sequence(session)
    return launch_rapidock_prediction(
        session, peptide=peptide, mode=mode,
        pocket_index=pocket_index, engine=engine,
        n_samples=n_samples, pocket_buffer=pocket_buffer,
    )


def _run_rapidock_setup_tool(session, arg, executor=None):
    import threading as _threading
    from .toolbar_actions import setup_rapidock_repo

    text = str(arg or "").strip()
    target_path = None
    skip_models = False
    gpu_mode = None
    if text:
        try:
            parts = shlex.split(text)
        except Exception:
            parts = [text]
        idx = 0
        while idx < len(parts):
            token = parts[idx]
            lower = token.lower()
            if lower in {"--skip-models", "skip-models", "no-models"}:
                skip_models = True
            elif lower in {"--cpu", "cpu"}:
                gpu_mode = None
            elif lower == "--gpu":
                next_token = parts[idx + 1] if idx + 1 < len(parts) else None
                if next_token and next_token.lower() in {"auto", "cu118", "cu121", "cpu"}:
                    gpu_mode = next_token.lower()
                    idx += 1
                else:
                    gpu_mode = "auto"
            elif lower.startswith("--gpu="):
                val = (lower.split("=", 1)[1] or "auto").strip()
                gpu_mode = val if val in {"auto", "cu118", "cu121", "cpu"} else "auto"
            elif lower in {"auto-gpu", "gpu", "gpu-auto"}:
                gpu_mode = "auto"
            elif lower in {"cu118", "cu121"}:
                gpu_mode = lower
            elif target_path is None:
                target_path = token
            idx += 1

    def _worker():
        try:
            message = setup_rapidock_repo(
                target_path,
                session=session,
                skip_models=skip_models,
                gpu=gpu_mode,
            )
        except Exception as err:
            try:
                session.logger.error(f"RAPiDock setup crashed: {err}")
            except Exception:
                pass
            return
        try:
            session.logger.info(message)
        except Exception:
            pass

    build_hint = (
        f"GPU build ({gpu_mode})" if gpu_mode else "CPU build"
    )
    try:
        session.logger.info(
            f"RAPiDock automated setup started in background ({build_hint}). "
            "Typical duration: 5-15 minutes (git clone + pip install + ~110 MB model download). "
            "Watch this log for progress."
        )
    except Exception:
        pass
    _threading.Thread(target=_worker, daemon=True).start()
    return f"RAPiDock setup started ({build_hint}) — see the log for progress."


def _run_setup_tool(session, arg, executor=None):
    """Unified `/setup <tool>` — auto-installs CLI tools when possible,
    or surfaces concrete install commands for tools that need manual steps.
    """
    text = str(arg or "").strip().lower()
    if not text or text in ("help", "list"):
        return "\n".join([
            "Setup helpers (run /setup <tool>):",
            "  rapidock   — auto: clone repo, venv, deps, models. Optional: --gpu auto",
            "  boltz      — auto: pip install boltz into ~/boltz2_latest venv",
            "  foldmason  — manual: brew install foldmason (or build from source)",
            "  folddisco  — manual: see https://github.com/steineggerlab/folddisco",
            "  caver      — manual: download CAVER 3 JAR from https://www.caver.cz",
            "Examples:",
            "  /setup rapidock --gpu auto",
            "  /setup boltz",
        ])
    parts = shlex.split(text)
    tool = parts[0]
    extras = parts[1:]
    if tool == "rapidock":
        return _run_rapidock_setup_tool(session, " ".join(extras), executor=executor)
    if tool == "boltz":
        return _run_boltz_setup(session, executor=executor)
    if tool in ("foldmason", "folddisco", "caver"):
        return {
            "foldmason": "Install foldmason: `brew install foldmason` (macOS) or build from https://github.com/steineggerlab/foldmason",
            "folddisco": "Install FoldDisco: `git clone https://github.com/steineggerlab/folddisco && cd folddisco && cargo build --release`",
            "caver":     "Install CAVER 3 JAR from https://www.caver.cz/download (Java required). Or use CAVER Web (auto-handled).",
        }[tool]
    return f"Unknown tool '{tool}'. Run /setup with no args for the list."


def _run_boltz_setup(session, executor=None):
    """Auto-install Boltz CLI into ~/boltz2_latest using the same pattern as
    RAPiDock setup (system Python venv, no contamination from ChimeraX bundle)."""
    import threading as _threading
    from .toolbar_actions import _find_system_python, _run_setup_step, _setup_log

    def _worker():
        target = Path.home() / "boltz2_latest"
        venv_python = target / "bin" / "python"
        if venv_python.exists() and shutil.which(str(venv_python)):
            try:
                session.logger.info(f"Boltz venv already present at {target}.")
            except Exception:
                pass
            return
        sys_py = _find_system_python(session)
        if not sys_py:
            try:
                session.logger.error(
                    "Boltz setup needs a system Python 3.9–3.11. "
                    "Install one (`brew install python@3.11`) and retry."
                )
            except Exception:
                pass
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        ok, err = _run_setup_step(
            session, f"create Boltz venv via {sys_py}",
            [sys_py, "-m", "venv", str(target)], timeout=120,
        )
        if not ok:
            try:
                session.logger.error(f"Boltz venv creation failed:\n{err}")
            except Exception:
                pass
            return
        ok, err = _run_setup_step(
            session, "pip install boltz (this can take 5-10 min)",
            [str(venv_python), "-m", "pip", "install", "-q", "-U", "boltz"],
            timeout=1800,
        )
        if not ok:
            try:
                session.logger.error(f"Boltz pip install failed:\n{err}")
            except Exception:
                pass
            return
        try:
            session.logger.info(
                f"Boltz installed at {target / 'bin' / 'boltz'}. "
                "Click the Boltz toolbar to predict structures."
            )
        except Exception:
            pass

    _threading.Thread(target=_worker, daemon=True).start()
    return (
        "Boltz setup started in background (~5-10 min for first-time pip install). "
        "Watch the log for progress."
    )


def _run_cancel_tool(session, arg, executor=None):
    """Cancel any running RAPiDock/HPEPDOCK watchers + downloads watchers in
    this session. Doesn't kill the remote HuangLab job (that runs server-side
    until done) — but stops local polling and frees daemon threads."""
    canceled = []
    # RAPiDock + HPEPDOCK watchers
    try:
        from .toolbar_actions import _cancel_rapidock_output_watchers
        watchers = getattr(session, "_codex_rapidock_watchers", None)
        if watchers:
            n = len(watchers)
            _cancel_rapidock_output_watchers(session)
            canceled.append(f"{n} RAPiDock/HPEPDOCK watcher(s)")
    except Exception as err:
        canceled.append(f"(rapidock cancel failed: {err})")
    # Downloads watchers — wrap whole block so a hostile dict can't block
    # the per-tool external watchers below from being cancelled too.
    try:
        dl_watchers = getattr(session, "_codex_downloads_watchers", None) or {}
        if dl_watchers:
            n = 0
            for entry in list(dl_watchers.values()):
                stop = entry.get("stop") if isinstance(entry, dict) else None
                if stop is not None:
                    try:
                        stop.set()
                        n += 1
                    except Exception:
                        pass
            try:
                session._codex_downloads_watchers = {}
            except Exception:
                pass
            canceled.append(f"{n} Downloads watcher(s)")
    except Exception as err:
        canceled.append(f"(downloads cancel failed: {err})")
    # External tool watchers (alphafold/foldmason/folddisco/etc.)
    for tool in ("alphafold", "afcomplex", "nucdock", "boltz", "foldmason", "folddisco",
                 "dali", "vast", "pdbefold", "usalign", "caver"):
        attr = f"_codex_{tool}_watchers"
        try:
            watchers = getattr(session, attr, None) or {}
            if not watchers:
                continue
            n = 0
            for entry in list(watchers.values()):
                stop = entry.get("stop") if isinstance(entry, dict) else None
                if stop is not None:
                    try:
                        stop.set()
                        n += 1
                    except Exception:
                        pass
            try:
                setattr(session, attr, {})
            except Exception:
                pass
            if n:
                canceled.append(f"{n} {tool} watcher(s)")
        except Exception:
            continue
    if not canceled:
        return "No active background watchers to cancel."
    return "Cancelled: " + ", ".join(canceled)


def _run_cavity_tool(session, arg, executor=None):
    """`/cavity [reset|show] [dist=D] [trans=T] [pockets=K] [show=R] [min_vol=V] [min_depth=D]`

      reset|show   manage cached preset
      dist=D       contact distance (Å, 2–15)
      trans=T      surface transparency (%, 0–100)
      pockets=K    KVFinder top-N pockets to rank (1–6, default 5)
      show=R       selected rank(s) to render, e.g. show=1 or show=1,3
      min_vol=V    minimum pocket volume Å³ (1-10000, default 30)
      min_depth=D  minimum pocket depth Å (0-50, default 1)

    Examples:
      /cavity                          # toolbar dialog or cached preset
      /cavity dist=5 trans=50          # force preset and dispatch
      /cavity pockets=5 show=2 min_vol=100
      /cavity reset
    """
    text = str(arg or "").strip()
    lower = text.lower()
    if lower in ("reset", "clear", "forget"):
        had = getattr(session, "_codex_cavity_last_params", None)
        for attr in ("_codex_cavity_last_params", "_codex_cavity_pocket_count",
                     "_codex_cavity_min_volume", "_codex_cavity_min_depth"):
            try: delattr(session, attr)
            except: pass
        return f"Cavity preset cleared (was {had})." if had else "No cached cavity preset to clear."
    if lower in ("show", "params", "current"):
        cached = getattr(session, "_codex_cavity_last_params", None)
        pockets = getattr(session, "_codex_cavity_pocket_count", 5)
        min_vol = getattr(session, "_codex_cavity_min_volume", 30.0)
        min_depth = getattr(session, "_codex_cavity_min_depth", 1.0)
        if cached:
            return (f"Cavity preset: distance {cached[0]:.1f} Å, transparency {int(cached[1])}%, "
                    f"pockets {pockets}, min_vol {min_vol:g} Å³, min_depth {min_depth:g} Å.")
        return (f"No cavity preset cached (pockets {pockets}, min_vol {min_vol:g} Å³, "
                f"min_depth {min_depth:g} Å). Click the Cavity toolbar to set one.")
    dist = None
    trans = None
    pockets = None
    selected_ranks = None
    min_vol = None
    min_depth = None
    for tok in text.split():
        lower_tok = tok.lower()
        if lower_tok.startswith("dist="):
            try: dist = max(2.0, min(15.0, float(tok.split("=", 1)[1])))
            except: pass
        elif lower_tok.startswith("trans="):
            try: trans = max(0, min(100, int(tok.split("=", 1)[1])))
            except: pass
        elif lower_tok.startswith("pockets=") or lower_tok.startswith("top="):
            try: pockets = max(1, min(6, int(tok.split("=", 1)[1])))
            except: pass
        elif lower_tok.startswith("show=") or lower_tok.startswith("select="):
            raw = tok.split("=", 1)[1]
            ranks = []
            for piece in re.split(r"[,;]", raw):
                try:
                    rank = max(1, min(6, int(piece.strip())))
                except Exception:
                    continue
                if rank not in ranks:
                    ranks.append(rank)
            if ranks:
                selected_ranks = ranks
        elif lower_tok.startswith("min_vol=") or lower_tok.startswith("vol=") or lower_tok.startswith("volume="):
            try: min_vol = max(1.0, min(10000.0, float(tok.split("=", 1)[1])))
            except: pass
        elif lower_tok.startswith("min_depth=") or lower_tok.startswith("depth="):
            try: min_depth = max(0.0, min(50.0, float(tok.split("=", 1)[1])))
            except: pass
    if dist is not None and trans is not None:
        session._codex_cavity_last_params = (float(dist), int(trans))
    elif dist is not None or trans is not None:
        cached = getattr(session, "_codex_cavity_last_params", None) or (3.5, 65)
        d = dist if dist is not None else cached[0]
        t = trans if trans is not None else cached[1]
        session._codex_cavity_last_params = (float(d), int(t))
    if pockets is not None:
        session._codex_cavity_pocket_count = pockets
    if min_vol is not None:
        session._codex_cavity_min_volume = min_vol
    if min_depth is not None:
        session._codex_cavity_min_depth = min_depth
    if text:
        cached = getattr(session, "_codex_cavity_last_params", None) or (5.0, 65)
        session._codex_cavity_force_options = {
            "distance": float(cached[0]),
            "transparency": int(cached[1]),
            "count": int(pockets if pockets is not None else getattr(session, "_codex_cavity_pocket_count", 5)),
            "min_volume": float(min_vol if min_vol is not None else getattr(session, "_codex_cavity_min_volume", 30.0)),
            "min_depth": float(min_depth if min_depth is not None else getattr(session, "_codex_cavity_min_depth", 1.0)),
        }
        if selected_ranks:
            session._codex_cavity_force_options["selected_ranks"] = selected_ranks
    from .toolbar_actions import run_toolbar_action
    run_toolbar_action(session, "ai-quick-cavity")
    final_preset = getattr(session, "_codex_cavity_last_params", None)
    final_pockets = getattr(session, "_codex_cavity_pocket_count", 5)
    return f"Cavity dispatched (preset: {final_preset}, pockets: {final_pockets})." if final_preset else "Cavity dispatched."


def _run_alignpanel_register_tool(session, arg, executor=None):
    """Register all currently open atomic structures as pairwise alignments
    for the sequence-bar alignment panel. Useful after loading external
    DALI/VAST/PDBeFold/US-align result PDBs into ChimeraX manually."""
    from .toolbar_actions import register_open_models_in_alignment_panel

    text = str(arg or "").strip()
    reference_spec = None
    if text:
        try:
            parts = shlex.split(text)
        except Exception:
            parts = [text]
        for token in parts:
            if token.startswith("#"):
                reference_spec = token
                break
    return register_open_models_in_alignment_panel(session, reference_spec=reference_spec)


def _run_rapidock_load_tool(session, arg, executor=None):
    """`/rapidock_load` / `/hpepdock_load <output_dir> [peptide=PEP] [limit=N]`

      limit=N   load top N poses (1-20, default 5)
      peptide=  override peptide token used in group names
    """
    from .toolbar_actions import load_rapidock_outputs

    text = str(arg or "").strip()
    if not text:
        return "Usage: /rapidock_load or /hpepdock_load <output_dir> [peptide=PEP] [limit=N]"
    try:
        parts = shlex.split(text)
    except Exception:
        parts = [text]
    output_dir = None
    peptide = None
    limit = 5
    positional = []
    for tok in parts:
        lower = tok.lower()
        if lower.startswith("peptide="):
            peptide = tok.split("=", 1)[1]
        elif lower.startswith("limit=") or lower.startswith("n="):
            try: limit = max(1, min(20, int(lower.split("=", 1)[1])))
            except: pass
        else:
            positional.append(tok)
    if positional:
        output_dir = positional[0]
        if len(positional) > 1 and peptide is None:
            peptide = positional[1]
    if not output_dir:
        return "Usage: /rapidock_load or /hpepdock_load <output_dir> [peptide=PEP] [limit=N]"
    return load_rapidock_outputs(session, output_dir, peptide=peptide, limit=limit)


def _run_hpepdock_refine_tool(session, arg, executor=None):
    """`/hpepdock_refine <output_dir> [limit=N] [steps=N]`"""
    from .hpepdock_client import prepare_hpepdock_results

    text = str(arg or "").strip()
    if not text:
        return "Usage: /hpepdock_refine <output_dir> [limit=N] [steps=N]"
    try:
        parts = shlex.split(text)
    except Exception:
        parts = [text]
    output_dir = None
    limit = 5
    steps = 240
    positional = []
    for tok in parts:
        lower = tok.lower()
        if lower.startswith("limit=") or lower.startswith("n="):
            try: limit = max(1, min(20, int(lower.split("=", 1)[1])))
            except: pass
        elif lower.startswith("steps=") or lower.startswith("step="):
            try: steps = max(0, min(2000, int(lower.split("=", 1)[1])))
            except: pass
        else:
            positional.append(tok)
    if positional:
        output_dir = positional[0]
    if not output_dir:
        return "Usage: /hpepdock_refine <output_dir> [limit=N] [steps=N]"
    package = prepare_hpepdock_results(
        output_dir,
        top_n=limit,
        minimize=True,
        minimize_steps=steps,
        force_minimize=True,
    )
    summaries = package.get("summaries") or []
    if not summaries:
        return f"HPEPDOCK refine: no HPEPDOCK pose PDBs found under {output_dir}."
    lines = [
        f"HPEPDOCK refine complete: {len(summaries)} pose(s).",
        f"- summary: {package.get('summary_tsv')}",
        f"- validation: {package.get('validation_tsv')}",
        f"- recommended ranking: {package.get('recommended_tsv')}",
        f"- best pose: {package.get('best_pose_pdb')}",
        f"- best report: {package.get('best_report_md')}",
        f"- view script: {package.get('view_best_cxc')}",
        f"- minimized complexes: {', '.join(Path(p).name for p in package.get('complexes', []))}",
    ]
    for item in summaries[: min(5, len(summaries))]:
        lines.append(
            f"validation rank {item.get('validation_rank')} / HPE rank {item.get('rank')}: "
            f"{item.get('validation_status')} ({item.get('validation_flags')}); ITScore {item.get('itscore')}; "
            f"clashes {item.get('raw_clash_atom_pairs_2a')} -> {item.get('clash_atom_pairs_2a')}; "
            f"closest {float(item.get('raw_min_distance_a') or 0):.2f} -> {float(item.get('min_distance_a') or 0):.2f} A; "
            f"shift {float(item.get('minimization_translation_a') or 0):.2f} A; "
            f"contacts retained {float(item.get('contact_retained_fraction') or 0):.2f}"
        )
    return "\n".join(lines)


def _run_external_load_tool(session, arg, tool):
    """Generic external loader: `/{tool}_load <path> [limit=N]`

    limit=N (1-50, default 10) caps how many result files are imported.
    """
    from .toolbar_actions import (
        _external_output_files,
        _start_external_output_watcher,
        load_external_tool_outputs,
    )

    labels = {
        "alphafold": "AlphaFold",
        "afcomplex": "AF Complex",
        "nucdock": "NucDock",
        "boltz": "Boltz",
        "foldmason": "FoldMason",
        "folddisco": "FoldDisco",
        "dali": "DALI",
        "vast": "VAST",
        "pdbefold": "PDBeFold",
        "usalign": "US-align",
    }
    label = labels.get(tool, tool)
    text = str(arg or "").strip()
    if not text:
        return f"Usage: /{tool}_load <file-or-output-dir> [limit=N]"
    try:
        parts = shlex.split(text)
    except Exception:
        parts = [text]
    limit = 10
    positional = []
    for tok in parts:
        lower = tok.lower()
        if lower.startswith("limit=") or lower.startswith("n="):
            try: limit = max(1, min(50, int(lower.split("=", 1)[1])))
            except: pass
        else:
            positional.append(tok)
    if not positional:
        return f"Usage: /{tool}_load <file-or-output-dir> [limit=N]"
    path = Path(positional[0]).expanduser()
    if not path.exists():
        return f"{label}: path does not exist: {path}"
    files = _external_output_files(path, limit=limit)
    if files:
        return load_external_tool_outputs(session, path, tool=tool, label=label, limit=limit)
    if path.is_dir():
        _start_external_output_watcher(session, path, tool=tool, label=label, limit=limit)
        return (
            f"{label}: no PDB/CIF/SDF/PSE files found yet under {path}.\n"
            f"Started a watcher (limit={limit}); files dropped there will auto-load."
        )
    return f"{label}: unsupported result file type: {path}"


def _run_caver_load_tool(session, arg, executor=None):
    text = str(arg or "").strip()
    if not text:
        return "Usage: /caver_load <folder|zip|pdb>"
    try:
        parts = shlex.split(text)
    except Exception:
        parts = [text]
    path = parts[0] if parts else text
    from .caver import import_caver_results

    return import_caver_results(session, path, executor=executor)


def _run_membrane_tool(session, arg, executor=None):
    """`/membrane [view|mlp|clear|web|opm|charmm|memgen|report] [thickness=T] [trans=N] [width=W] [margin=M]`

      thickness=T  slab core thickness Å (10-60, default auto from z-span)
      trans=N      surface transparency % (0-100, default 45)
      width=W      slab width override Å (30-300; default auto bbox+margin clamp 46-220)
      margin=M     padding around bbox Å (0-100, default 32)
    """
    from .membrane import (
        apply_membrane_mlp,
        clear_virtual_membrane,
        format_membrane_report,
        launch_membrane_builder_sites,
        run_membrane_view,
    )

    raw = str(arg or "").strip()
    thickness = None
    trans = 45
    width = None
    margin = None
    display_limit = 10
    tokens = []
    for tok in raw.split():
        lower = tok.lower()
        if lower.startswith("thickness="):
            try: thickness = max(10.0, min(60.0, float(lower.split("=", 1)[1])))
            except: pass
        elif lower.startswith("trans=") or lower.startswith("transparency="):
            try: trans = max(0, min(100, int(lower.split("=", 1)[1])))
            except: pass
        elif lower.startswith("width=") or lower.startswith("w="):
            try: width = max(30.0, min(300.0, float(lower.split("=", 1)[1])))
            except: pass
        elif lower.startswith("margin=") or lower.startswith("pad="):
            try: margin = max(0.0, min(100.0, float(lower.split("=", 1)[1])))
            except: pass
        elif lower.startswith("top=") or lower.startswith("n=") or lower.startswith("limit="):
            try: display_limit = max(1, min(50, int(lower.split("=", 1)[1])))
            except: pass
        else:
            tokens.append(tok)
    cleaned = " ".join(tokens)
    action, target_arg = _split_action_arg(cleaned)
    action = (action or "").lower()
    if action in {"view", "show", "fill", "slab", "virtual", "가상", "표시", "채워"}:
        return run_membrane_view(session, target_arg or None, executor=executor,
                                 thickness=thickness, transparency=trans,
                                 width=width, margin=margin)
    if action in {"mlp", "hydrophobic", "hydrophobicity", "소수성"}:
        return apply_membrane_mlp(session, target_arg or None, executor=executor)
    if action in {"clear", "delete", "remove", "지워", "삭제"}:
        return clear_virtual_membrane(session, executor=executor)
    if action in {"web", "all", "builder", "server", "external", "외부", "서버"}:
        return launch_membrane_builder_sites(session, "web", target_arg or None, executor=executor)
    if action in {"opm", "ppm", "database"}:
        return launch_membrane_builder_sites(session, "opm", target_arg or None, executor=executor)
    if action in {"charmm", "charmmgui", "charmm-gui"}:
        return launch_membrane_builder_sites(session, "charmm", target_arg or None, executor=executor)
    if action == "memgen":
        return launch_membrane_builder_sites(session, "memgen", target_arg or None, executor=executor)
    if action in {"report", "analyze", "analysis", "분석", ""}:
        return format_membrane_report(session, target_arg or None, display_limit=display_limit)
    return run_membrane_view(session, cleaned or None, executor=executor,
                             thickness=thickness, transparency=trans,
                             width=width, margin=margin)


def _run_pisa_tool(session, arg, executor=None):
    """`/pisa [view|report|web] [pair=N] [cutoff=C]`

      pair=N      pick Nth interface candidate (1-based, default 1 = largest)
      cutoff=C    contact distance Å (default 8.0)
    """
    from .pisa import run_pisa_view

    raw = str(arg or "").strip()
    pair_index = 1
    cutoff = 8.0
    display_limit = 10
    tokens = []
    for tok in raw.split():
        lower = tok.lower()
        if lower.startswith("pair=") or lower.startswith("interface="):
            try: pair_index = max(1, min(50, int(lower.split("=", 1)[1])))
            except: pass
        elif lower.startswith("cutoff="):
            try: cutoff = max(3.0, min(15.0, float(lower.split("=", 1)[1])))
            except: pass
        elif lower.startswith("top=") or lower.startswith("n=") or lower.startswith("limit="):
            try: display_limit = max(1, min(50, int(lower.split("=", 1)[1])))
            except: pass
        else:
            tokens.append(tok)
    cleaned_arg = " ".join(tokens) or "view"
    return run_pisa_view(session, cleaned_arg, executor=executor,
                         pair_index=pair_index, cutoff=cutoff, display_limit=display_limit)


def _run_profile_tool(session, arg):
    alignments = list(getattr(session.alignments, "alignments", []) or [])
    if not alignments:
        return "No alignment is open. Run Blast Protein or Sequence Viewer first."
    alignment = None
    target = str(arg or "").strip().lower()
    if target:
        for item in alignments:
            ident = str(getattr(item, "ident", "") or "").lower()
            description = str(getattr(item, "description", "") or "").lower()
            if target == ident or target in description:
                alignment = item
                break
    if alignment is None:
        last_id = getattr(session, "_codex_bridge_last_alignment_id", None)
        if last_id:
            for item in alignments:
                if getattr(item, "ident", None) == last_id:
                    alignment = item
                    break
    if alignment is None:
        alignment = alignments[-1]

    try:
        from chimerax.profile_grids.tool import ProfileGridsTool
    except ImportError:
        return (
            "Profile Grids bundle (ChimeraX-ProfileGrids) is not installed.\n"
            "Install it via Tools → More Tools, then retry /profile."
        )

    ProfileGridsTool(session, "Profile Grids", alignment)
    session._codex_bridge_last_alignment_id = getattr(alignment, "ident", None)
    return f"Opened Profile Grid for alignment {getattr(alignment, 'ident', '(unknown)')}."


def _alignment_ids(session):
    try:
        return [getattr(aln, "ident", None) for aln in session.alignments.alignments]
    except Exception:
        return []


def _remember_latest_alignment(session, before_ids=None):
    current = _alignment_ids(session)
    before_ids = list(before_ids or [])
    new_ids = [item for item in current if item not in before_ids and item is not None]
    if new_ids:
        session._codex_bridge_last_alignment_id = new_ids[-1]
        return new_ids[-1]
    if current:
        session._codex_bridge_last_alignment_id = current[-1]
        return current[-1]
    return None


def _similar_set_names(session):
    manager = getattr(session, "similar_structures", None)
    if manager is None:
        return []
    try:
        return list(manager.names)
    except Exception:
        return []


def _remember_latest_similar_set(session, before_names=None):
    current = _similar_set_names(session)
    before_names = list(before_names or [])
    new_names = [item for item in current if item not in before_names]
    if new_names:
        session._codex_bridge_last_similar_name = new_names[-1]
        return new_names[-1]
    if current:
        session._codex_bridge_last_similar_name = current[-1]
        return current[-1]
    return None


def _similar_command_with_from_set(session, base_command):
    set_name = getattr(session, "_codex_bridge_last_similar_name", None)
    if set_name:
        return f"{base_command} fromSet {set_name}"
    return base_command


def _check_similar_results_query_chain(session, label="Foldseek"):
    """Pre-flight: ChimeraX's similarstructures sub-commands raise UserError
    if the result set's query_chain has been closed. Catch this early with a
    friendly message instead of letting users hit
    'Cannot position Foldseek ligands without query structure'.
    """
    try:
        from .toolbar_actions import _similar_results
    except Exception:
        return None
    set_name = getattr(session, "_codex_bridge_last_similar_name", None)
    sets = _similar_set_names(session)
    if not sets:
        return (
            f"{label} needs a Foldseek result set. Run /similar (or click Similar in the toolbar) first, "
            "wait for hits to appear, then re-run this command."
        )
    if set_name and set_name not in sets:
        # Stale cache — fall through to pick the latest set
        set_name = None
    target_set = set_name or sets[-1]
    try:
        results = _similar_results(session, target_set)
    except Exception as err:
        return f"{label}: cannot inspect Foldseek result set '{target_set}' ({err})"
    if results is None:
        return f"{label}: Foldseek result set '{target_set}' is empty."
    query_chain = getattr(results, "query_chain", None)
    if query_chain is None:
        return (
            f"{label}: the Foldseek result set '{target_set}' has no query chain attached "
            "(the original receptor was probably closed, or the set was loaded from a stale file). "
            "Re-open the original receptor and run /similar again to refresh."
        )
    structure = getattr(query_chain, "structure", None)
    if structure is None or structure not in session.models.list():
        return (
            f"{label}: the query chain for '{target_set}' is no longer in this session. "
            "Re-open the original receptor and run /similar again."
        )
    return None


def _run_snapshot_hint():
    return "Tip: run `snapshot publication` after a composite/interface/pocket figure for a high-resolution white-background export."


def _run_figure_lab(session):
    from .semantic import format_figure_lab_report

    recommended = _recommended_figure_mode(session)
    recommended_list = _recommended_figure_modes(session)
    lines = [format_figure_lab_report(session)]
    if recommended:
        lines.extend(["", f"- Recommended next command: figure {recommended}"])
    if recommended_list:
        lines.extend(["- Figure cycle order:"] + [f"  - figure {mode}" for mode in recommended_list[:6]])
    return "\n".join(lines)


def _recommended_figure_mode(session):
    modes = _recommended_figure_modes(session)
    return modes[0] if modes else "publication"


def recommended_figure_mode(session):
    return _recommended_figure_mode(session)


def run_figure_mode(session, mode, executor=None):
    return _run_figure(session, mode, executor=executor)


def _recommended_figure_modes(session):
    from .semantic import get_selection_overlap_payload, best_interface_pair, best_ligand_site, best_metal_site

    payload = get_selection_overlap_payload(session)
    modes = []
    if payload is not None:
        if payload["interface_matches"] and (payload["motif_matches"] or payload["catalytic_hits"] or payload["ligand_matches"] or payload["metal_matches"]):
            modes.append("selection-composite")
        if payload["interface_matches"]:
            modes.append("selection-interface")
        if payload["motif_matches"]:
            modes.append("selection-motif")
        if payload["ligand_matches"] or payload["metal_matches"] or payload["catalytic_hits"]:
            modes.append("selection-pocket")
        modes.append("selection")
    else:
        if best_ligand_site(session) is not None or best_metal_site(session) is not None:
            modes.extend(["composite", "pocket"])
        if best_interface_pair(session) is not None:
            modes.extend(["interface", "explode-composite"])
        modes.extend(["domains", "publication"])

    deduped = []
    for mode in modes:
        if mode not in deduped:
            deduped.append(mode)
    return deduped


def _set_last_figure_mode(session, mode):
    cleaned = str(mode or "").strip()
    session._codex_bridge_last_figure_mode = cleaned
    history = list(getattr(session, "_codex_bridge_figure_history", []) or [])
    if not history or history[-1] != cleaned:
        history.append(cleaned)
    session._codex_bridge_figure_history = history[-20:]


def _get_last_figure_mode(session):
    return str(getattr(session, "_codex_bridge_last_figure_mode", "") or "").strip() or None


def _get_previous_figure_mode(session):
    history = list(getattr(session, "_codex_bridge_figure_history", []) or [])
    if len(history) < 2:
        return None
    return history[-2]


def _get_next_cycle_mode(session):
    modes = _recommended_figure_modes(session)
    if not modes:
        return None
    current = str(getattr(session, "_codex_bridge_figure_cycle_index", -1))
    try:
        index = int(current)
    except ValueError:
        index = -1
    index = (index + 1) % len(modes)
    session._codex_bridge_figure_cycle_index = index
    return modes[index]


def _run_figure(session, mode, executor=None):
    mode = (mode or "publication").strip().lower()
    mode_name, mode_arg = _split_action_arg(mode)
    mode_name = mode_name or mode
    if mode_name in {"pretty", "beauty", "beautiful", "polish", "polished"}:
        mode_name = "clean"
        mode = "clean"
    if mode_name not in {"lab", "next", "cycle", "back", "repeat", "clean", "publication", "selection", "selection-pocket", "selection-motif", "selection-interface", "selection-composite", "composite", "explode-composite", "domains", "roles", "assembly", "pocket", "interface", "explode", "interactive"}:
        return "usage: /figure lab|next|cycle|back|repeat|clean|publication|selection|selection-pocket|selection-motif|selection-interface|selection-composite|composite|explode-composite|domains|roles|assembly|pocket|interface|explode [spacing]|interactive"

    if mode_name == "lab":
        return _run_figure_lab(session)
    if mode_name == "next":
        recommended = _recommended_figure_mode(session)
        return _run_figure(session, recommended, executor=executor)
    if mode_name == "cycle":
        next_mode = _get_next_cycle_mode(session)
        if not next_mode:
            return "No figure cycle candidates available."
        return _run_figure(session, next_mode, executor=executor)
    if mode_name == "back":
        previous = _get_previous_figure_mode(session)
        if not previous:
            return "No previous figure mode recorded."
        current = _get_last_figure_mode(session)
        if current and current.startswith("explode") and not previous.startswith("explode"):
            _run_layout_reset(session, executor=executor)
        return _run_figure(session, previous, executor=executor)
    if mode_name == "repeat":
        repeat_mode = _get_last_figure_mode(session)
        if not repeat_mode:
            return "No previous figure mode recorded."
        return _run_figure(session, repeat_mode, executor=executor)

    if mode_name == "clean":
        result = _run_clean_publication_view(session, executor=executor)
        _set_last_figure_mode(session, "clean")
        return result
    if mode_name == "domains":
        result = _run_domains_view(session, None, executor=executor)
        _set_last_figure_mode(session, "domains")
        return result
    if mode_name == "selection":
        result = _run_selection_view(session, executor=executor)
        _set_last_figure_mode(session, "selection")
        return result
    if mode_name in {"selection-pocket", "selection-motif", "selection-interface", "selection-composite"}:
        for tok in (mode_arg or "").split():
            low = tok.lower()
            if low.startswith("top=") or low.startswith("n=") or low.startswith("limit="):
                try:
                    session._codex_selection_top_n = max(1, min(50, int(tok.split("=", 1)[1])))
                except Exception:
                    pass
        if mode_name == "selection-pocket":
            result = _run_selection_pocket_view(session, executor=executor)
        elif mode_name == "selection-motif":
            result = _run_selection_motif_view(session, executor=executor)
        elif mode_name == "selection-interface":
            result = _run_selection_interface_view(session, executor=executor)
        else:
            result = _run_selection_composite_view(session, executor=executor)
        _set_last_figure_mode(session, mode_name)
        return result
    if mode_name == "composite":
        result = _run_composite_view(session, executor=executor)
        _set_last_figure_mode(session, "composite")
        return result
    if mode_name == "explode-composite":
        spacing_val, label_n_val = _parse_explode_args(mode_arg)
        result = _run_explode_composite_view(session, executor=executor, spacing=spacing_val, label_n=label_n_val)
        _set_last_figure_mode(session, f"explode-composite {mode_arg}".strip())
        return result
    if mode_name in {"roles", "assembly"}:
        result = _run_roles_view(session, None, executor=executor)
        _set_last_figure_mode(session, mode_name)
        return result
    if mode_name == "pocket":
        for tok in (mode_arg or "").split():
            lower_tok = tok.lower()
            if lower_tok.startswith("catalytic=") or lower_tok.startswith("cat="):
                try: session._codex_pocket_catalytic_count = max(1, min(30, int(tok.split("=", 1)[1])))
                except Exception: pass
            elif lower_tok.startswith("focus="):
                try: session._codex_pocket_focus_count = max(1, min(10, int(tok.split("=", 1)[1])))
                except Exception: pass
        result = _run_pocket_view(session, executor=executor)
        _set_last_figure_mode(session, "pocket")
        return result
    if mode_name == "interface":
        result = _run_interface_view(session, executor=executor)
        _set_last_figure_mode(session, "interface")
        return result
    if mode_name == "explode":
        spacing_val, label_n_val = _parse_explode_args(mode_arg)
        result = _run_explode_view(session, executor=executor, spacing=spacing_val, label_n=label_n_val)
        _set_last_figure_mode(session, f"explode {mode_arg}".strip())
        return result

    target = _selection_target(session)
    view_target = target or "all"
    commands = []
    if mode == "publication":
        if not target:
            return _run_best_publication_view(session, executor=executor)
        commands = [
            *_publication_base_commands(target, scaffold_color="", session=session),
            *_figure_scaffold_color_commands(session, target, scaffold_color="lightgray", default_scheme="bychain"),
            f"view {view_target}",
        ]
    else:
        commands = [
            f"cartoon {target}".strip(),
            f"~surface {target}".strip(),
            "preset interactive",
            "lighting simple",
            "graphics silhouettes false",
            f"view {view_target}",
        ]

    for command in commands:
        _run(session, command, executor=executor)

    _set_last_figure_mode(session, mode_name)
    return "\n".join(
        [
            f"Local ChimeraX {mode} figure style applied.",
            "Executed ChimeraX commands:",
            *[f"- {command}" for command in commands],
        ]
    )


def _run_transparency_from_text(session, lowered, executor=None):
    percent = _extract_percent(lowered)
    if percent is None:
        return None
    target_kind = _transparency_target_kind(lowered)
    return _apply_transparency(session, percent, target_kind, executor=executor)


def _parse_transparency_arg(arg):
    tokens = arg.strip().lower().split()
    if not tokens:
        return None
    try:
        percent = int(tokens[0].rstrip("%"))
    except ValueError:
        return None
    if percent < 0 or percent > 100:
        return None
    target_kind = tokens[1] if len(tokens) > 1 else "surface"
    return percent, target_kind


def _apply_transparency(session, percent, target_kind, executor=None):
    target = _selection_target(session)
    target_letters = {
        "surface": "s",
        "surfaces": "s",
        "cartoon": "c",
        "cartoons": "c",
        "ribbon": "r",
        "ribbons": "r",
        "atoms": "a",
        "atom": "a",
        "all": "abcsp",
    }.get(target_kind, "s")
    command = f"transparency {target} {percent} target {target_letters}".strip()
    command = " ".join(command.split())
    _run(session, command, executor=executor)
    return _format_local_result(command)


def _extract_percent(text):
    m = re.search(r"(\d{1,3})\s*%?", text)
    if not m:
        return None
    value = int(m.group(1))
    if 0 <= value <= 100:
        return value
    return None


def _transparency_target_kind(text):
    if any(word in text for word in ("cartoon", "ribbon", "리본")):
        return "cartoon"
    if any(word in text for word in ("atom", "원자")):
        return "atoms"
    if any(word in text for word in ("all", "전체", "전부")):
        return "all"
    return "surface"


def _selection_target(session):
    from chimerax.atomic import selected_atoms, selected_residues

    if session.selection.models() or len(selected_atoms(session)) or len(selected_residues(session)):
        return "sel"
    return ""


def _selected_model_spec(session):
    models = list(session.selection.models())
    if not models:
        return None
    model = models[0]
    return f"#{getattr(model, 'id_string', '?')}"


def _selected_chain_spec(session):
    from .semantic import get_session_semantics

    selection = get_session_semantics(session).get("selection", {})
    ranges = list(selection.get("ranges", []))
    if ranges:
        spec = ranges[0]
        if "/" in spec:
            return spec.split(":", 1)[0]
    return None


def _clear_selection(session):
    try:
        session.selection.clear()
    except Exception:
        pass


def _split_action_arg(arg):
    text = (arg or "").strip()
    if not text:
        return "", ""
    action, _, rest = text.partition(" ")
    return action.lower(), rest.strip()


def _resolve_visual_model_hint(session, model_hint=None):
    from .semantic import resolve_model_spec

    if model_hint:
        if "/" in str(model_hint):
            return model_hint
        resolved = resolve_model_spec(session, model_hint)
        return resolved or model_hint
    return _selected_chain_spec(session) or _selected_model_spec(session)


def _run_domains_view(session, model_hint=None, executor=None, *, label_n=8):
    from .semantic import get_domain_selections

    try:
        label_cap = max(0, min(50, int(label_n)))
    except Exception:
        label_cap = 8
    resolved_hint = _resolve_visual_model_hint(session, model_hint)
    entries = get_domain_selections(session, model_hint=resolved_hint)
    chains = [entry for entry in entries if entry["kind"] == "chain"]
    domains = [entry for entry in entries if entry["kind"] == "domain"]
    scope = resolved_hint or ""
    view_target = scope or "all"

    color_mode = _figure_color_mode(session)
    commands = _publication_base_commands(scope, session=session)
    if not domains and chains and color_mode in {"auto", "bychain"}:
        commands.append(_color_scheme_command(scope, "bychain"))

    executed = []
    for command in commands:
        _run(session, command, executor=executor)
        executed.append(command)

    created = []
    groups = []
    label_specs = []
    for chain in chains:
        select_command = f"select {chain['spec']}"
        name_command = f"name frozen {chain['selection_name']} sel"
        group_command = f"name frozen {chain['group_name']} sel"
        _run(session, select_command, executor=executor)
        _run(session, name_command, executor=executor)
        _run(session, group_command, executor=executor)
        created.append(chain["selection_name"])
        groups.append(chain["group_name"])
        executed.extend([select_command, name_command, group_command])
        if chain.get("label_spec"):
            label_specs.append(chain["label_spec"])

    for index, domain in enumerate(domains):
        color = _domain_color(domain)
        select_command = f"select {domain['spec']}"
        name_command = f"name frozen {domain['selection_name']} sel"
        group_command = f"name frozen {domain['group_name']} sel"
        color_command = _color_selection_command(color)
        _run(session, select_command, executor=executor)
        _run(session, name_command, executor=executor)
        _run(session, group_command, executor=executor)
        _run(session, color_command, executor=executor)
        created.append(domain["selection_name"])
        groups.append(domain["group_name"])
        executed.extend([select_command, name_command, group_command, color_command])
        if domain.get("label_spec"):
            label_specs.append(domain["label_spec"])

    for command in _label_commands_for_specs(label_specs[:label_cap]):
        _run(session, command, executor=executor)
        executed.append(command)

    _clear_selection(session)
    view_command = _clean_command(f"view {view_target}")
    _run(session, view_command, executor=executor)
    executed.append(view_command)

    summary = [
        "Domain-aware publication view applied.",
        "Executed ChimeraX commands:",
        *[f"- {command}" for command in executed],
    ]
    if created:
        summary.append("Created named selections:")
        summary.extend(f"- {name}" for name in created[:24])
        if len(created) > 24:
            summary.append(f"- ... {len(created) - 24} more")
    if groups:
        summary.append("Manual-edit groups:")
        summary.extend(f"- {name}" for name in groups[:24])
        _set_named_groups(session, groups)
    workspace_report = _create_domain_workspace_models(session, entries, executor=executor)
    if workspace_report:
        summary.extend(["", *workspace_report.splitlines()])
    if not domains:
        summary.append("No strong multi-domain chunk split detected; fell back to chain-coloring with chain selections.")
    return "\n".join(summary)


def _run_roles_view(session, model_hint=None, executor=None, *, label_n=8):
    from .semantic import get_role_selections

    try:
        label_cap = max(0, min(50, int(label_n)))
    except Exception:
        label_cap = 8
    resolved_hint = _resolve_visual_model_hint(session, model_hint)
    entries = get_role_selections(session, model_hint=resolved_hint)
    if not entries:
        return _run_domains_view(session, resolved_hint, executor=executor, label_n=label_cap)

    scope = resolved_hint or ""
    view_target = scope or "all"
    commands = _publication_base_commands(scope, session=session)
    executed = []
    for command in commands:
        _run(session, command, executor=executor)
        executed.append(command)

    created = []
    groups = []
    label_specs = []
    for role_name in ("scaffold", "peripheral", "active"):
        for entry in entries:
            if entry["role"] != role_name:
                continue
            select_command = f"select {entry['spec']}"
            name_command = f"name frozen {entry['selection_name']} sel"
            group_command = f"name frozen {entry['group_name']} sel"
            color_command = _color_selection_command(ROLE_COLORS[role_name])
            _run(session, select_command, executor=executor)
            _run(session, name_command, executor=executor)
            _run(session, group_command, executor=executor)
            _run(session, color_command, executor=executor)
            created.append(entry["selection_name"])
            groups.append(entry["group_name"])
            executed.extend([select_command, name_command, group_command, color_command])
            label_specs.extend(entry.get("label_specs", [])[:2])

    for command in _label_commands_for_specs(label_specs[:label_cap]):
        _run(session, command, executor=executor)
        executed.append(command)

    _clear_selection(session)
    view_command = _clean_command(f"view {view_target}")
    _run(session, view_command, executor=executor)
    executed.append(view_command)

    summary = [
        "Chain-role view applied.",
        "Executed ChimeraX commands:",
        *[f"- {command}" for command in executed],
    ]
    if created:
        summary.append("Created named selections:")
        summary.extend(f"- {name}" for name in created)
    if groups:
        summary.append("Manual-edit groups:")
        summary.extend(f"- {name}" for name in groups)
        _set_named_groups(session, groups)
    workspace_report = _create_role_workspace_models(session, entries, executor=executor)
    if workspace_report:
        summary.extend(["", *workspace_report.splitlines()])
    return "\n".join(summary)


def _run_best_publication_view(session, executor=None):
    from .semantic import best_ligand_site, best_metal_site, get_complex_interfaces, get_domain_selections
    from chimerax.atomic import AtomicStructure

    if best_ligand_site(session) is not None or best_metal_site(session) is not None:
        return _run_pocket_view(session, executor=executor)

    interface_count = 0
    for model in session.models.list(type=AtomicStructure):
        interface_count += len(get_complex_interfaces(model))
    if interface_count:
        return _run_roles_view(session, None, executor=executor)

    domains = [entry for entry in get_domain_selections(session) if entry["kind"] == "domain"]
    if domains:
        return _run_domains_view(session, None, executor=executor)

    return _run_domains_view(session, None, executor=executor)


def _run_clean_publication_view(session, executor=None, preserve_existing=False, preserve_camera=True):
    commands = [] if preserve_existing else [
        *_publication_base_commands("", scaffold_color="", session=session),
        *_figure_scaffold_color_commands(session, "", scaffold_color="lightgray", default_scheme="bychain"),
    ]
    if not preserve_existing and not preserve_camera:
        commands.append("view all")
    executed = []
    failed = []
    for command in commands:
        cleaned = _clean_command(command)
        try:
            _run(session, cleaned, executor=executor)
        except Exception as err:
            failed.append((cleaned, str(err) if str(err) else err.__class__.__name__))
            continue
        executed.append(cleaned)
    lines = [
        "Clean protein structure view applied.",
        "Executed ChimeraX commands:",
        *[f"- {command}" for command in executed],
    ]
    if failed:
        lines.extend(
            [
                "Skipped commands:",
                *[f"- {command}: {error}" for command, error in failed],
            ]
        )
    return "\n".join(lines)


def _run_composite_view(session, executor=None):
    blocks = [_run_domains_view(session, None, executor=executor)]
    overlay_lines = ["Composite overlays applied."]
    for site_kind in ("catalytic", "ligand", "metal", "interface"):
        overlay = _run_site_overlay(session, site_kind, executor=executor)
        if overlay:
            overlay_lines.append(f"- {site_kind}: {overlay}")
    if len(overlay_lines) == 1:
        overlay_lines.append("- no overlayable sites detected")
    blocks.extend(["", *overlay_lines, "", _run_snapshot_hint()])
    return "\n".join(blocks)


def _run_selection_pocket_view(session, executor=None, include_base=True):
    from .semantic import get_selection_overlap_payload

    base = _run_selection_view(session, executor=executor) if include_base else None
    if include_base and base == "No current selection to focus.":
        return base
    if not include_base and not _selection_target(session):
        return "No current selection to focus."

    try:
        cap = max(1, min(50, int(getattr(session, "_codex_selection_top_n", 8) or 8)))
    except Exception:
        cap = 8
    payload = get_selection_overlap_payload(session)
    overlays = ["Selection-pocket overlays applied."]
    if payload is None:
        overlays.append("- no selection overlap payload available")
        return "\n".join([base, "", *overlays])

    catalytic_hits = payload["catalytic_hits"][:cap]
    if catalytic_hits:
        commands = [
            "select " + " ".join(entry["residue_spec"] for entry in catalytic_hits),
            "name frozen site_catalytic sel",
            _show_selection_atoms_command(),
            "style sel stick",
            _color_selection_command("gold"),
            "label sel residues",
        ]
        for command in commands:
            _run(session, command, executor=executor)
        _clear_selection(session)
        overlays.append("- catalytic: " + " ".join(commands))

    if payload["ligand_matches"]:
        site = payload["ligand_matches"][0]
        commands = [
            f"select {site['ligand_spec']}",
            "name frozen site_ligand sel",
            _show_selection_atoms_command(),
            "style sel stick",
            _color_selection_command("cornflowerblue"),
            "label sel residues",
        ]
        for command in commands:
            _run(session, command, executor=executor)
        _clear_selection(session)
        overlays.append("- ligand: " + " ".join(commands))

    if payload["metal_matches"]:
        site = payload["metal_matches"][0]
        commands = [
            f"select {site['metal_spec']}",
            "name frozen site_metal sel",
            _show_selection_atoms_command(),
            "style sel stick",
            _color_selection_command("orange"),
            "label sel residues",
        ]
        for command in commands:
            _run(session, command, executor=executor)
        _clear_selection(session)
        overlays.append("- metal: " + " ".join(commands))

    if len(overlays) == 1:
        overlays.append("- no catalytic/ligand/metal overlap within current selection")
    overlays.append(_run_snapshot_hint())
    if include_base:
        return "\n".join([base, "", *overlays])
    return "\n".join(overlays)


def _run_explode_view(session, executor=None, spacing=None, *, label_n=10):
    from .semantic import get_domain_selections

    try:
        label_cap = max(0, min(50, int(label_n)))
    except Exception:
        label_cap = 10
    chain_entries = [entry for entry in get_domain_selections(session) if entry["kind"] == "chain"]
    if len(chain_entries) <= 1:
        return _run_roles_view(session, None, executor=executor, label_n=label_cap)

    commands = [
        *_publication_base_commands("", scaffold_color="", session=session),
        *_figure_scaffold_color_commands(session, "", scaffold_color="lightgray", default_scheme="bychain"),
    ]
    executed = []
    for command in commands:
        _run(session, command, executor=executor)
        executed.append(command)

    step = spacing or _get_layout_spacing(session)
    offsets = _explode_offsets(len(chain_entries), step=step)
    reverse_commands = []
    for entry, (dx, dy) in zip(chain_entries, offsets):
        if abs(dx) > 0.01:
            command = f"move x {dx:.1f} atoms {entry['spec']}"
            _run(session, command, executor=executor)
            executed.append(command)
            reverse_commands.append(f"move x {-dx:.1f} atoms {entry['spec']}")
        if abs(dy) > 0.01:
            command = f"move y {dy:.1f} atoms {entry['spec']}"
            _run(session, command, executor=executor)
            executed.append(command)
            reverse_commands.append(f"move y {-dy:.1f} atoms {entry['spec']}")

    for command in _label_commands_for_specs([entry.get("label_spec") for entry in chain_entries][:label_cap]):
        _run(session, command, executor=executor)
        executed.append(command)
    view_command = "view all"
    _run(session, view_command, executor=executor)
    executed.append(view_command)
    _set_layout_reverse_commands(session, reverse_commands)

    return "\n".join(
        [
            "Exploded assembly view applied.",
            "Executed ChimeraX commands:",
            *[f"- {command}" for command in executed],
            f"Explode spacing: {step:.1f}",
            "Note: explode view moves atoms in the current session.",
        ]
    )


def _run_explode_composite_view(session, executor=None, spacing=None, *, label_n=10):
    exploded = _run_explode_view(session, executor=executor, spacing=spacing, label_n=label_n)
    overlay_lines = ["Exploded composite overlays applied."]
    for site_kind in ("catalytic", "ligand", "metal", "interface"):
        overlay = _run_site_overlay(session, site_kind, executor=executor)
        if overlay:
            overlay_lines.append(f"- {site_kind}: {overlay}")
    if len(overlay_lines) == 1:
        overlay_lines.append("- no overlayable sites detected")
    overlay_lines.append(_run_snapshot_hint())
    return "\n".join([exploded, "", *overlay_lines])


def _run_selection_motif_view(session, executor=None, include_base=True):
    from .semantic import get_selection_overlap_payload

    base = _run_selection_view(session, executor=executor) if include_base else None
    if include_base and base == "No current selection to focus.":
        return base
    if not include_base and not _selection_target(session):
        return "No current selection to focus."

    try:
        cap = max(1, min(50, int(getattr(session, "_codex_selection_top_n", 8) or 8)))
    except Exception:
        cap = 8
    payload = get_selection_overlap_payload(session)
    overlays = ["Selection-motif overlays applied."]
    if payload is None or not payload["motif_matches"]:
        overlays.append("- no motif overlap within current selection")
        if include_base:
            return "\n".join([base, "", *overlays, _run_snapshot_hint()])
        return "\n".join([*overlays, _run_snapshot_hint()])

    motif_specs = []
    for entry in payload["motif_matches"][:cap]:
        motif_specs.extend(entry["residue_specs"])
    motif_specs = list(dict.fromkeys(motif_specs))
    commands = [
        "select " + " ".join(motif_specs),
        "name frozen site_motif sel",
        _show_selection_atoms_command(),
        "style sel stick",
        _color_selection_command("#ffb347"),
        "label sel residues",
    ]
    for command in commands:
        _run(session, command, executor=executor)
    _clear_selection(session)
    overlays.append("- motif: " + " ".join(commands))
    overlays.append(_run_snapshot_hint())
    if include_base:
        return "\n".join([base, "", *overlays])
    return "\n".join(overlays)


def _run_selection_interface_view(session, executor=None, include_base=True):
    from .semantic import get_selection_overlap_payload

    base = _run_selection_view(session, executor=executor) if include_base else None
    if include_base and base == "No current selection to focus.":
        return base
    if not include_base and not _selection_target(session):
        return "No current selection to focus."

    payload = get_selection_overlap_payload(session)
    overlays = ["Selection-interface overlays applied."]
    if payload is None or not payload.get("interface_matches"):
        overlays.append("- no interface overlap within current selection")
        if include_base:
            return "\n".join([base, "", *overlays, _run_snapshot_hint()])
        return "\n".join([*overlays, _run_snapshot_hint()])

    pair = payload["interface_matches"][0]
    commands = [
        f"interfaces select {pair['chain_a_spec']} contacting {pair['chain_b_spec']} bothSides true",
        "name frozen site_interface sel",
        _show_selection_atoms_command(),
        "style sel stick",
        _color_selection_command("hotpink"),
        "label sel residues",
    ]
    for command in commands:
        _run(session, command, executor=executor)
    _clear_selection(session)
    overlays.append("- interface: " + " ".join(commands))
    overlays.append(_run_snapshot_hint())
    if include_base:
        return "\n".join([base, "", *overlays])
    return "\n".join(overlays)


def _run_selection_composite_view(session, executor=None):
    base = _run_selection_view(session, executor=executor)
    if base == "No current selection to focus.":
        return base

    blocks = [base, "", "Selection-composite overlays applied."]
    for helper in (_run_selection_pocket_view, _run_selection_motif_view, _run_selection_interface_view):
        text = helper(session, executor=executor, include_base=False)
        if text and text != "No current selection to focus.":
            blocks.extend(text.splitlines())
            blocks.append("")
    blocks.append(_run_snapshot_hint())
    return "\n".join(blocks)


def _run_selection_view(session, executor=None):
    if not _selection_target(session):
        return "No current selection to focus."

    commands = [
        *_publication_base_commands("", session=session),
        *_selection_stick_style_commands("gold"),
        "label sel residues",
        "view sel",
    ]
    for command in commands:
        _run(session, command, executor=executor)
    return "\n".join(
        [
            "Selection-focused publication view applied.",
            "Executed ChimeraX commands:",
            *[f"- {command}" for command in commands],
        ]
    )


def _run_pocket_view(session, executor=None):
    from .semantic import best_catalytic_candidates, best_ligand_site, best_metal_site

    try:
        cat_n = max(1, min(30, int(getattr(session, "_codex_pocket_catalytic_count", 8) or 8)))
    except Exception:
        cat_n = 8
    try:
        focus_n = max(1, min(10, int(getattr(session, "_codex_pocket_focus_count", 4) or 4)))
    except Exception:
        focus_n = 4

    ligand_site = best_ligand_site(session)
    metal_site = best_metal_site(session)
    catalytic = best_catalytic_candidates(session, limit=cat_n)

    scope = ""
    if ligand_site is not None:
        scope = ligand_site["model_spec"]
    elif metal_site is not None:
        scope = metal_site["model_spec"]

    commands = _publication_base_commands(scope, session=session)
    executed = []
    created = []
    for command in commands:
        _run(session, command, executor=executor)
        executed.append(command)

    catalytic_specs = [entry["residue_spec"] for entry in catalytic[:cat_n]]
    if catalytic_specs:
        select_command = "select " + " ".join(catalytic_specs)
        name_command = "name frozen site_catalytic sel"
        color_command = _color_selection_command("gold")
        stick_command = "style sel stick"
        for command in (select_command, name_command, stick_command, _clean_command(f"size sel stickRadius {PUBLICATION_STICK_RADIUS}"), color_command):
            _run(session, command, executor=executor)
            executed.append(command)
        created.append("site_catalytic")

    if ligand_site is not None:
        for command in (
            f"select {ligand_site['ligand_spec']}",
            "name frozen site_ligand sel",
            *_selection_stick_style_commands("cornflowerblue"),
        ):
            _run(session, command, executor=executor)
            executed.append(command)
        created.append("site_ligand")

    if metal_site is not None:
        for command in (
            f"select {metal_site['metal_spec']}",
            "name frozen site_metal sel",
            *_selection_stick_style_commands("orange"),
        ):
            _run(session, command, executor=executor)
            executed.append(command)
        created.append("site_metal")

    focus_specs = []
    if ligand_site is not None:
        focus_specs.append(ligand_site["ligand_spec"])
    if metal_site is not None:
        focus_specs.append(metal_site["metal_spec"])
    focus_specs.extend(catalytic_specs[:focus_n])
    _clear_selection(session)
    view_command = "view " + (" ".join(focus_specs) if focus_specs else (scope or "all"))
    _run(session, view_command, executor=executor)
    executed.append(view_command)

    summary = [
        "Pocket-focused publication view applied.",
        "Executed ChimeraX commands:",
        *[f"- {command}" for command in executed],
    ]
    if created:
        summary.append("Created named selections:")
        summary.extend(f"- {name}" for name in created)
    if ligand_site is None and metal_site is None and not catalytic_specs:
        summary.append("No ligand/metal/catalytic focus was detected; only the neutral scaffold style was applied.")
    return "\n".join(summary)


def _run_interface_view(session, executor=None):
    from .semantic import best_interface_pair

    # If the user opened the Figure -> Interface picker dialog, it stashed
    # explicit enzyme/ligand specs on the session. Honour those and run the
    # pastel-coloured interaction pipeline. Otherwise fall back to the
    # heuristic chain-pair picker.
    picked_enzyme = getattr(session, "_codex_interface_enzyme_spec", None)
    picked_ligand = getattr(session, "_codex_interface_ligand_spec", None)
    picked_cutoff = getattr(session, "_codex_interface_cutoff", 4.5)
    if picked_enzyme and picked_ligand:
        return _run_picked_interface_view(
            session,
            picked_enzyme,
            picked_ligand,
            picked_cutoff,
            executor=executor,
        )

    pair = best_interface_pair(session)
    if pair is None:
        return _run_roles_view(session, None, executor=executor)

    chain_a = pair["chain_a_spec"]
    chain_b = pair["chain_b_spec"]
    color_mode = _figure_color_mode(session)
    if color_mode == "auto":
        chain_color_commands = [
            f"color {chain_a} #78bfd2 target ac",
            f"color {chain_b} #d1987a target ac",
        ]
    elif color_mode == "bychain":
        chain_color_commands = [
            _color_scheme_command(chain_a, "bychain"),
            _color_scheme_command(chain_b, "bychain"),
        ]
    else:
        chain_color_commands = []
    commands = [
        *_publication_base_commands("", session=session),
        f"cartoon {chain_a} {chain_b}",
        *chain_color_commands,
        f"interfaces select {chain_a} contacting {chain_b} bothSides true",
        "name frozen site_interface sel",
        *_selection_stick_style_commands("hotpink"),
        "label sel residues",
        "view sel",
    ]
    executed = []
    for command in commands:
        _run(session, command, executor=executor)
        executed.append(command)
    for command in _label_commands_for_specs(_chain_representative_specs(session, [chain_a, chain_b])):
        _run(session, command, executor=executor)
        executed.append(command)
    _clear_selection(session)
    return "\n".join(
        [
            "Interface-focused publication view applied.",
            "Executed ChimeraX commands:",
            *[f"- {command}" for command in executed],
        ]
    )


def _run_picked_interface_view(session, enzyme_spec, ligand_spec, cutoff, executor=None):
    """Interface view scoped strictly to the picked enzyme + ligand.

    Renders a translucent cartoon (70%) for the enzyme so the pocket sticks
    pop out, sticks for the ligand and for every enzyme residue with any
    atom within ``cutoff`` of the ligand, and overlays the Codex pastel
    interaction pseudobonds (H-bond / salt-bridge / pi-stacking /
    hydrophobic). Other open models, the background, and global preset
    state are left untouched -- earlier versions called
    ``_publication_base_commands("")`` which mutated the whole scene.
    """
    from .interaction_colors import apply_interaction_coloring

    enzyme = enzyme_spec.strip()
    ligand = ligand_spec.strip()
    try:
        cutoff_val = max(2.5, min(8.0, float(cutoff)))
    except (TypeError, ValueError):
        cutoff_val = 4.5

    commands = [
        # ---- enzyme: cartoon, no atoms, label clean ----
        f"cartoon {enzyme}",
        f"hide {enzyme} atoms",
        f"~label {enzyme}",
        # Whole enzyme cartoon is heavily translucent context (80%) so the
        # eye locks onto the pocket. The interaction-residue cartoon will be
        # restored to opaque below, after we select those residues.
        f"transparency {enzyme} 80 target c",
        # ---- ligand: fully opaque sticks ----
        f"show {ligand} atoms",
        f"style {ligand} stick",
        f"transparency {ligand} 0 target abcs",
        # ---- pocket residues on the enzyme: any residue with an atom within
        # cutoff of the ligand. ChimeraX zone syntax `spec :< d` returns
        # atoms in residues that have any atom within d of spec.
        f"select (({ligand}) :< {cutoff_val:.2f}) & ({enzyme})",
        "name frozen site_interface sel",
        # Sticks + opaque atoms/bonds for the interaction residues...
        "show sel atoms",
        "style sel stick",
        "transparency sel 0 target ab",
        # ...and override the global 80% cartoon transparency for these
        # specific residues so their backbone stands out from the de-
        # emphasised rest of the protein.
        "transparency sel 0 target c",
        # ---- frame the view on what we just highlighted ----
        f"view (sel) | ({ligand})",
    ]
    executed = []
    for command in commands:
        _run(session, command, executor=executor)
        executed.append(command)

    # Pastel pseudobonds for H-bond / salt-bridge / pi-stacking / hydrophobic.
    # Failures here should not abort the rest of the figure setup.
    try:
        executed.extend(
            apply_interaction_coloring(
                session,
                enzyme,
                ligand,
                cutoff=cutoff_val,
                executor=executor,
            )
        )
    except Exception as err:
        try:
            session.logger.warning(f"interaction coloring failed: {err}")
        except Exception:
            pass

    _clear_selection(session)
    return "\n".join(
        [
            f"Interface view applied (enzyme={enzyme}, ligand={ligand}, cutoff={cutoff_val:.1f} Å).",
            "Other open models were not modified.",
            "Executed ChimeraX commands:",
            *[f"- {command}" for command in executed],
        ]
    )


def _run_site_overlay(session, target, executor=None):
    from .semantic import best_catalytic_candidates, best_interface_pair, best_ligand_site, best_metal_site

    target = (target or "").strip().lower()
    if target == "metal":
        site = best_metal_site(session)
        if site is None:
            return None
        specs = [site["metal_spec"], *site["site_residue_specs"][:10]]
        commands = [
            "select " + " ".join(specs),
            "name frozen site_metal sel",
            _show_selection_atoms_command(),
            "style sel stick",
            _color_selection_command("orange"),
            "label sel residues",
        ]
    elif target == "ligand":
        site = best_ligand_site(session)
        if site is None:
            return None
        specs = [site["ligand_spec"], *[f"{site['model_spec']}/{hit['chain_id']}:{int(hit['number'])}" for hit in site["nearby"][:10]]]
        commands = [
            "select " + " ".join(specs),
            "name frozen site_ligand sel",
            _show_selection_atoms_command(),
            "style sel stick",
            _color_selection_command("cornflowerblue"),
            "label sel residues",
        ]
    elif target == "catalytic":
        candidates = best_catalytic_candidates(session, limit=10)
        if not candidates:
            return None
        specs = [entry["residue_spec"] for entry in candidates]
        commands = [
            "select " + " ".join(specs),
            "name frozen site_catalytic sel",
            _show_selection_atoms_command(),
            "style sel stick",
            _color_selection_command("gold"),
            "label sel residues",
        ]
    elif target == "interface":
        pair = best_interface_pair(session)
        if pair is None:
            return None
        commands = [
            f"interfaces select {pair['chain_a_spec']} contacting {pair['chain_b_spec']} bothSides true",
            "name frozen site_interface sel",
            _show_selection_atoms_command(),
            "style sel stick",
            _color_selection_command("hotpink"),
            "label sel residues",
        ]
    else:
        return None

    for command in commands:
        _run(session, command, executor=executor)
    _clear_selection(session)
    return " ".join(commands)


def _run_motif_view(session, motif_text=None, model_hint=None, executor=None, *, top_n=12):
    from .semantic import get_motif_hits

    hits = get_motif_hits(session, model_hint=model_hint, motif_text=motif_text)
    if not hits:
        return "No motif hits detected."

    try:
        cap = max(1, min(50, int(top_n)))
    except Exception:
        cap = 12
    commands = [
        "preset sil",
        "preset cartoon",
        "cartoon",
        "~surface",
        _color_scope_command("", "lightgray"),
        "lighting soft",
        "graphics silhouettes true width 2 color black",
    ]
    created = []
    for command in commands:
        _run(session, command, executor=executor)

    motif_specs = []
    for hit in hits[:cap]:
        motif_specs.extend(hit["residue_specs"])
        _run(session, "select " + " ".join(hit["residue_specs"]), executor=executor)
        _run(session, f"name frozen {hit['selection_name']} sel", executor=executor)
        created.append(hit["selection_name"])
    motif_specs = list(dict.fromkeys(motif_specs))

    followup_commands = [
        "select " + " ".join(motif_specs),
        "name frozen site_motif sel",
        _show_selection_atoms_command(),
        "style sel stick",
        _color_selection_command("#ffb347"),
        "label sel residues",
        "view sel",
    ]
    for command in followup_commands:
        _run(session, command, executor=executor)
    _clear_selection(session)

    return "\n".join(
        [
            "Motif-focused view applied.",
            "Executed ChimeraX commands:",
            *[f"- {command}" for command in [*commands, *followup_commands]],
            "Created named selections:",
            *[f"- {name}" for name in created[:12]],
        ]
    )


def _run_catalytic_zoom(session, model_hint=None, executor=None, *, trans=80):
    """Close-up view: fade the scaffold cartoon and zoom to the catalytic triad.

    Targets the triad if `find_catalytic_triads` produces one; otherwise
    falls back to the top catalytic candidates from `best_catalytic_candidates`.
    Cartoon transparency clamps to 0-100.
    """
    from .semantic import best_catalytic_candidates, find_catalytic_triads
    try:
        trans_pct = max(0, min(100, int(trans)))
    except Exception:
        trans_pct = 80

    triad_specs = []
    try:
        triads = find_catalytic_triads(session, model_hint=model_hint) or []
        if triads:
            triad_specs = list(triads[0].get("specs") or [])
    except Exception:
        pass
    if not triad_specs:
        try:
            cands = best_catalytic_candidates(session, model_hint=model_hint, limit=6) or []
            triad_specs = [c["residue_spec"] for c in cands if c.get("residue_spec")]
        except Exception:
            pass

    if not triad_specs:
        return "No catalytic triad/candidate detected — nothing to zoom into."

    spec_text = " ".join(triad_specs)
    # Auto-pick the same accent color the Analyze button uses so the triad
    # in zoom view matches the named-group swatch the user sees in Models.
    try:
        from .display_color import choose_accent_color
        accent = choose_accent_color(session)
    except Exception:
        accent = "#d4845c"
    commands = [
        f"transparency {trans_pct} target c",
        f"select {spec_text}",
        "show sel atoms",
        "style sel stick",
        f"color sel {accent} target ab",
        "label sel residues",
        f"view {spec_text}",
    ]
    for command in commands:
        try:
            _run(session, command, executor=executor)
        except Exception:
            pass
    restore_charge_colors(session, spec_text)
    _clear_selection(session)
    return "\n".join([
        "Catalytic-zoom view applied.",
        f"- cartoon transparency: {trans_pct}%",
        f"- focus residues: {spec_text}",
        "Executed ChimeraX commands:",
        *[f"- {c}" for c in commands],
    ])


def _run_catalytic_view(session, model_hint=None, executor=None, preserve_existing=False,
                        *, top_n=12, triads_n=6, triad_color=None,
                        preserve_camera=True):
    """top_n: how many candidate catalytic residues to highlight (default 12).
    triad_color: optional '#rrggbb' override; default is auto-chosen via
    choose_accent_color() to harmonize with current chain coloring."""
    from .semantic import best_catalytic_candidates, format_catalytic_workflow_report, find_catalytic_triads
    from .named_selection import add_group

    try:
        n = max(1, min(50, int(top_n)))
    except Exception:
        n = 12

    candidates = best_catalytic_candidates(session, model_hint=model_hint, limit=n)
    if not candidates:
        return "No catalytic candidates detected."

    scope = candidates[0].get("model_spec", "")
    commands = [] if preserve_existing else [*_publication_base_commands(scope, session=session)]
    for command in commands:
        _run(session, command, executor=executor)

    specs = [candidate["residue_spec"] for candidate in candidates[:n]]
    followup_commands = [
        "select " + " ".join(specs),
        "name frozen site_catalytic sel",
        *_selection_stick_style_commands("gold"),
        "label sel residues",
    ]
    if not preserve_camera:
        followup_commands.append("view sel")
    for command in followup_commands:
        _run(session, command, executor=executor)
    restore_charge_colors(session, " ".join(specs))
    _clear_selection(session)

    # Surface results in the Models panel as CodexNamedSelectionGroup so they
    # behave like Cavity/Site groups (toggle visibility, color, etc.) and are
    # registered for the session-state cleanup on model close.
    try:
        triads = find_catalytic_triads(session, model_hint=model_hint) or []
    except Exception:
        triads = []
    # Adaptive accent: harmonizes with current chain coloring.
    # Override via triad_color=#hex when caller knows better.
    if triad_color is None:
        try:
            from .display_color import choose_accent_color
            triad_color = choose_accent_color(session)
        except Exception:
            triad_color = "#d4845c"
    try:
        triad_cap = max(1, min(20, int(triads_n)))
    except Exception:
        triad_cap = 6
    for index, triad in enumerate(triads[:triad_cap], start=1):
        triad_specs = list(triad.get("specs") or [])
        if not triad_specs:
            continue
        label = str(triad.get("label", "triad")).split()[0].lower().replace("/", "_")
        chain = ""
        first = triad_specs[0]
        if "/" in first and ":" in first:
            try:
                chain = first.split("/", 1)[1].split(":", 1)[0].lower()
            except Exception:
                chain = ""
        slug = f"triad_{label}_{chain}{index:02d}".replace("__", "_").strip("_")
        try:
            add_group(session, slug, " ".join(triad_specs), color=triad_color)
        except Exception:
            pass

    # Also register one umbrella "site_catalytic" group containing all candidates
    if specs:
        try:
            add_group(session, "site_catalytic", " ".join(specs), color=triad_color)
        except Exception:
            pass

    workflow_lines = format_catalytic_workflow_report(session, model_hint=model_hint).splitlines()[:12]
    return "\n".join(
        [
            "Catalytic-candidate view applied.",
            "Executed ChimeraX commands:",
            *[f"- {command}" for command in [*commands, *followup_commands]],
            "Review:",
            *workflow_lines,
        ]
    )


def _run_features_view(session, model_hint=None, executor=None, preserve_existing=False, *, top_n=24):
    from .semantic import get_uniprot_feature_entries

    entries = get_uniprot_feature_entries(session, model_hint=model_hint)
    if not entries:
        return "No UniProt feature annotations fetched or mapped."

    try:
        cap = max(1, min(100, int(top_n)))
    except Exception:
        cap = 24
    commands = [] if preserve_existing else [
        "preset sil",
        "preset cartoon",
        "cartoon",
        "~surface",
        _color_scope_command("", "lightgray"),
        "lighting soft",
        "graphics silhouettes true width 2 color black",
    ]
    for command in commands:
        _run(session, command, executor=executor)

    created = []
    groups = []
    feature_colors = {
        "Active site": "gold",
        "Metal binding": "orange",
        "Binding site": "cornflowerblue",
        "Motif": "#ffb347",
        "Zinc finger": "#c86cff",
        "DNA binding": "#6fc8d7",
        "Domain": "#78bfd2",
        "Region": "#d9b36c",
    }
    label_specs = []
    for entry in entries[:cap]:
        select_command = f"select {entry['spec']}"
        name_command = f"name frozen {entry['selection_name']} sel"
        group_command = f"name frozen {entry['group_name']} sel"
        color_command = _color_selection_command(feature_colors.get(entry["feature_type"], "#ffb347"))
        for command in (select_command, name_command, group_command, _show_selection_atoms_command(), "style sel stick", color_command):
            _run(session, command, executor=executor)
        restore_charge_colors(session, entry["spec"])
        created.append(entry["selection_name"])
        groups.append(entry["group_name"])
        label_specs.append(f"{entry['model_spec']}/{entry['chain_id']}:{entry['start']}")
    for command in _label_commands_for_specs(label_specs[:10]):
        _run(session, command, executor=executor)
    _clear_selection(session)
    _set_named_groups(session, groups)
    workspace_report = _create_feature_workspace_models(session, model_hint=model_hint, executor=executor)

    lines = [
        "UniProt feature view applied.",
        "Created named selections:",
        *[f"- {name}" for name in created[:24]],
    ]
    if groups:
        lines.extend(["Manual-edit groups:", *[f"- {name}" for name in groups[:24]]])
    if workspace_report:
        lines.extend(["", *workspace_report.splitlines()])
    return "\n".join(lines)


def _extract_motif_text(prompt):
    text = str(prompt or "").strip()
    if not text:
        return None
    for token in re.findall(r"[A-Za-z0-9\-]{3,}", text):
        upper = token.upper()
        if "MOTIF" in upper:
            continue
        if _looks_like_motif_token(upper):
            return token
    return None


def _parse_motif_args(text):
    token_text = str(text or "").strip()
    if not token_text:
        return None, None

    model_hint = None
    motif_text = None
    for token in token_text.split():
        if token.startswith("#"):
            model_hint = token
            continue
        if _looks_like_motif_token(token.upper()):
            motif_text = token
    return model_hint, motif_text


def _looks_like_motif_token(text):
    token = str(text or "").strip().upper().replace("-", "")
    if not token or len(token) > 16:
        return False
    if not re.fullmatch(r"[ACDEFGHIKLMNPQRSTVWXY0-9]+", token):
        return False
    return any(char in token for char in ("H", "D", "E", "C", "X")) and any(char.isdigit() or char == "X" for char in token[1:])


def _label_commands_for_specs(specs):
    commands = []
    seen = set()
    for spec in specs:
        token = str(spec or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        commands.append(f"label {token} residues")
    return commands


def _chain_representative_specs(session, chain_specs):
    from .semantic import get_session_semantics

    semantics = get_session_semantics(session)
    mapping = {}
    for model in semantics["models"]:
        if not model.get("atomic"):
            continue
        for chain in model["chains"]:
            if chain.get("start") is None:
                continue
            mapping[(model["spec"], chain["id"])] = f"{model['spec']}/{chain['id']}:{chain['start']}"

    reps = []
    for chain_spec in chain_specs:
        text = str(chain_spec or "")
        if "/" not in text:
            continue
        model_spec, chain_id = text.split("/", 1)
        label_spec = mapping.get((model_spec, chain_id))
        if label_spec:
            reps.append(label_spec)
    return reps


def _explode_offsets(count, step=18.0):
    if count <= 1:
        return [(0.0, 0.0)]
    offsets = []
    columns = max(2, int(np.ceil(np.sqrt(count))))
    rows = int(np.ceil(count / columns))
    x_center = (columns - 1) / 2.0
    y_center = (rows - 1) / 2.0
    for index in range(count):
        row = index // columns
        col = index % columns
        dx = (col - x_center) * step
        dy = (y_center - row) * step
        offsets.append((dx, dy))
    return offsets


def _set_layout_reverse_commands(session, commands):
    session._codex_bridge_layout_reverse_commands = list(commands or [])


def _set_named_groups(session, names):
    merged = list(getattr(session, "_codex_bridge_named_groups", []) or [])
    for name in names:
        if name not in merged:
            merged.append(name)
    session._codex_bridge_named_groups = merged[-100:]


def _run_groups_status(session):
    groups = list(getattr(session, "_codex_bridge_named_groups", []) or [])
    if not groups:
        return "No saved manual-edit groups yet. Run `domains view` or `roles view` first."
    return "\n".join(["Manual-edit groups:"] + [f"- {name}" for name in groups])


def _domain_split_status(session):
    from .semantic import get_domain_split_settings

    settings = get_domain_split_settings(session)
    return (
        "Current domain split settings:\n"
        f"- min_length: {settings['min_length']}\n"
        f"- max_chunks_per_chain: {settings['max_chunks_per_chain']}"
    )


def _adjust_domain_split_settings(session, direction):
    from .semantic import DEFAULT_DOMAIN_MAX_CHUNKS, DEFAULT_DOMAIN_MIN_LENGTH, get_domain_split_settings

    settings = get_domain_split_settings(session)
    min_length = settings["min_length"]
    max_chunks = settings["max_chunks_per_chain"]
    if direction == "finer":
        min_length = max(12, min_length - 8)
        max_chunks = min(12, max_chunks + 2)
    elif direction == "coarser":
        min_length = min(80, min_length + 8)
        max_chunks = max(3, max_chunks - 1)
    elif direction == "reset":
        min_length = DEFAULT_DOMAIN_MIN_LENGTH
        max_chunks = DEFAULT_DOMAIN_MAX_CHUNKS
    session._codex_bridge_domain_min_length = min_length
    session._codex_bridge_domain_max_chunks = max_chunks
    return {
        "min_length": min_length,
        "max_chunks_per_chain": max_chunks,
    }


def _run_domain_split_refinement(session, direction, model_hint=None, executor=None):
    settings = _adjust_domain_split_settings(session, direction)
    result = _run_domains_view(session, model_hint=model_hint, executor=executor)
    header = {
        "finer": "Domain split refined to a finer granularity.",
        "coarser": "Domain split refined to a coarser granularity.",
        "reset": "Domain split reset to the default granularity.",
    }.get(direction, "Domain split updated.")
    return "\n".join(
        [
            header,
            f"Current settings: min_length {settings['min_length']}, max_chunks {settings['max_chunks_per_chain']}",
            "",
            result,
        ]
    )


def _workspace_dir():
    path = Path(tempfile.gettempdir()) / "chimerax_codex_bridge_workspace"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _workspace_state(session):
    state = getattr(session, "_codex_bridge_workspace_models", None)
    if state is None:
        state = {}
        session._codex_bridge_workspace_models = state
    return state


def _current_model_specs(session):
    specs = []
    for model in session.models.list():
        model_id = getattr(model, "id_string", None)
        if model_id:
            specs.append(f"#{model_id}")
    return specs


def _workspace_files(session):
    files = getattr(session, "_codex_bridge_workspace_files", None)
    if files is None:
        files = []
        session._codex_bridge_workspace_files = files
    return files


def _set_workspace_models(session, kind, specs):
    state = _workspace_state(session)
    state[kind] = list(dict.fromkeys(specs))


def _close_workspace_kind(session, kind, executor=None):
    state = _workspace_state(session)
    specs = list(state.get(kind, []) or [])
    executed = []
    for spec in specs:
        command = f"close {spec}"
        try:
            _run(session, command, executor=executor)
            executed.append(command)
        except Exception:
            continue
    state[kind] = []
    return executed


def _close_all_workspace_models(session, executor=None):
    state = _workspace_state(session)
    executed = []
    for kind in list(state):
        executed.extend(_close_workspace_kind(session, kind, executor=executor))
    files = list(_workspace_files(session))
    for path_text in files:
        try:
            Path(path_text).unlink()
        except Exception:
            continue
    session._codex_bridge_workspace_files = []
    return executed


def _workspace_save_open(session, model_spec, select_spec, label, color=None, executor=None):
    from chimerax.core.commands import StringArg

    workspace_dir = _workspace_dir()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    safe_label = _slug_group_label(label) or "workspace"
    path = workspace_dir / f"{safe_label}_{stamp}.cif"
    selection_snapshot = _capture_selection_snapshot(session)
    before = set(_current_model_specs(session))
    commands = []
    try:
        if select_spec:
            select_command = f"select {select_spec}"
            _run(session, select_command, executor=executor)
            commands.append(select_command)
            save_command = f"save {StringArg.unparse(str(path))} format mmcif models {model_spec} selectedOnly true"
        else:
            save_command = f"save {StringArg.unparse(str(path))} format mmcif models {model_spec}"
        _run(session, save_command, executor=executor)
        commands.append(save_command)

        open_command = f"open {StringArg.unparse(str(path))} name {StringArg.unparse(label)}"
        _run(session, open_command, executor=executor)
        commands.append(open_command)

        after = set(_current_model_specs(session))
        new_specs = [spec for spec in after if spec not in before]
        for spec in new_specs:
            for command in (
                f"cartoon {spec}",
                f"~surface {spec}",
                f"color {spec} {color} target ac" if color else "",
            ):
                if not command:
                    continue
                _run(session, command, executor=executor)
                commands.append(command)
        _workspace_files(session).append(str(path))
        return new_specs, commands
    finally:
        _restore_selection_snapshot(session, selection_snapshot, executor=executor)


def _create_workspace_models(session, kind, items, executor=None):
    if not items:
        return ""
    _close_workspace_kind(session, kind, executor=executor)
    created_specs = []
    created_labels = []
    executed = []
    for item in items:
        new_specs, commands = _workspace_save_open(
            session,
            item["model_spec"],
            item.get("spec", ""),
            item["workspace_label"],
            color=item.get("workspace_color"),
            executor=executor,
        )
        created_specs.extend(new_specs)
        created_labels.append(item["workspace_label"])
        executed.extend(commands)
    _set_workspace_models(session, kind, created_specs)
    if not created_specs:
        return ""
    lines = [
        f"Workspace models created for {kind}.",
        "Visible in Models panel:",
    ]
    for label in created_labels[:24]:
        lines.append(f"- {label}")
    if len(created_labels) > 24:
        lines.append(f"- ... {len(created_labels) - 24} more")
    return "\n".join(lines)


def _create_domain_workspace_models(session, entries, executor=None):
    domains = [entry for entry in entries if entry["kind"] == "domain"]
    if domains:
        items = []
        for entry in domains:
            color = _domain_color(entry)
            items.append(
                {
                    "model_spec": entry["model_spec"],
                    "spec": entry["spec"],
                    "workspace_label": f"domain {entry['chain_id']}-{entry['display_index']} {entry.get('display_label', 'segment')}",
                    "workspace_color": color,
                }
            )
        return _create_workspace_models(session, "domains", items, executor=executor)

    chains = [entry for entry in entries if entry["kind"] == "chain"]
    if not chains:
        return ""
    items = [
        {
            "model_spec": entry["model_spec"],
            "spec": entry["spec"],
            "workspace_label": f"chain {entry['chain_id']}",
            "workspace_color": None,
        }
        for entry in chains
    ]
    return _create_workspace_models(session, "domains", items, executor=executor)


def _create_role_workspace_models(session, entries, executor=None):
    if not entries:
        return ""
    items = [
        {
            "model_spec": entry["model_spec"],
            "spec": entry["spec"],
            "workspace_label": f"role {entry['role']} ({','.join(entry['chain_ids'])})",
            "workspace_color": ROLE_COLORS.get(entry["role"]),
        }
        for entry in entries
    ]
    return _create_workspace_models(session, "roles", items, executor=executor)


def _create_site_workspace_models(session, executor=None):
    from .semantic import best_catalytic_candidates, best_interface_pair, best_ligand_site, best_metal_site

    items = []
    metal = best_metal_site(session)
    if metal is not None:
        items.append(
            {
                "model_spec": metal["model_spec"],
                "spec": " ".join([metal["metal_spec"], *metal["site_residue_specs"][:12]]),
                "workspace_label": "site metal",
                "workspace_color": "orange",
            }
        )
    ligand = best_ligand_site(session)
    if ligand is not None:
        items.append(
            {
                "model_spec": ligand["model_spec"],
                "spec": " ".join([ligand["ligand_spec"], *[f"{ligand['model_spec']}/{hit['chain_id']}:{int(hit['number'])}" for hit in ligand["nearby"][:12]]]),
                "workspace_label": "site ligand",
                "workspace_color": "cornflowerblue",
            }
        )
    catalytic = best_catalytic_candidates(session, limit=12)
    if catalytic:
        items.append(
            {
                "model_spec": catalytic[0]["model_spec"],
                "spec": " ".join(item["residue_spec"] for item in catalytic[:12]),
                "workspace_label": "site catalytic",
                "workspace_color": "gold",
            }
        )
    pair = best_interface_pair(session)
    if pair is not None:
        items.append(
            {
                "model_spec": pair["chain_a_spec"].split("/", 1)[0],
                "spec": f"{pair['chain_a_spec']} {pair['chain_b_spec']}",
                "workspace_label": "site interface",
                "workspace_color": "hotpink",
            }
        )
    return _create_workspace_models(session, "sites", items, executor=executor)


def _create_feature_workspace_models(session, model_hint=None, executor=None):
    from .semantic import get_uniprot_feature_entries

    entries = get_uniprot_feature_entries(session, model_hint=model_hint)
    items = [
        {
            "model_spec": entry["model_spec"],
            "spec": entry["spec"],
            "workspace_label": f"feature {entry['chain_id']} {entry['feature_type']} {entry['start']}-{entry['end']}",
            "workspace_color": "#ffb347",
        }
        for entry in entries[:24]
    ]
    return _create_workspace_models(session, "features", items, executor=executor)


def _run_workspace(session, arg, executor=None):
    action, target_arg = _split_action_arg(arg)
    action = action or "domains"
    if action == "clear":
        executed = _close_all_workspace_models(session, executor=executor)
        if not executed:
            return "No workspace models to clear."
        return "\n".join(["Workspace models cleared.", "Executed ChimeraX commands:", *[f"- {command}" for command in executed]])
    if action == "domains":
        from .semantic import get_domain_selections

        return _create_domain_workspace_models(session, get_domain_selections(session, model_hint=target_arg or None), executor=executor) or "No domain workspace models created."
    if action in {"roles", "assembly"}:
        from .semantic import get_role_selections

        return _create_role_workspace_models(session, get_role_selections(session, model_hint=target_arg or None), executor=executor) or "No role workspace models created."
    if action in {"sites", "site"}:
        return _create_site_workspace_models(session, executor=executor) or "No site workspace models created."
    if action in {"features", "feature", "uniprot"}:
        return _create_feature_workspace_models(session, model_hint=target_arg or None, executor=executor) or "No feature workspace models created."
    return "usage: /workspace [domains|roles|sites|features|clear]"


def _get_dali_history(session):
    return list(getattr(session, "_codex_bridge_dali_history", []) or [])


def _append_dali_history(session, item):
    history = list(getattr(session, "_codex_bridge_dali_history", []) or [])
    history.append(dict(item))
    session._codex_bridge_dali_history = history[-10:]


def _get_layout_spacing(session):
    try:
        return float(getattr(session, "_codex_bridge_layout_spacing", 18.0) or 18.0)
    except (TypeError, ValueError):
        return 18.0


def _run_layout_spacing(session, value_text):
    value = _coerce_spacing(value_text)
    if value is None:
        return "usage: /layout spacing <positive number>"
    session._codex_bridge_layout_spacing = value
    return f"Layout spacing set to {value:.1f}"


def _run_legend_save(session, filename=None, style="text"):
    from .semantic import format_legend_report

    if filename:
        path = Path(filename).expanduser()
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        suffix = ".tsv" if style == "table" else ".txt"
        path = Path.cwd() / f"chimeraX_legend_{stamp}{suffix}"
    text = format_legend_report(session, style=style)
    path.write_text(text + "\n", encoding="utf-8")
    return "\n".join(
        [
            "Legend saved." if style != "table" else "Legend table saved.",
            f"Saved: {path}",
        ]
    )


def _run_package_export(session, prefix=None, executor=None):
    from .semantic import format_caption_draft, format_dali_report, format_panel_plan

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = Path(prefix).expanduser() if prefix else (Path.cwd() / f"chimeraX_package_{stamp}")
    package_dir = base if base.suffix == "" else base.parent / base.stem
    package_dir.mkdir(parents=True, exist_ok=True)

    created = []

    panel_plan_path = package_dir / "panel_plan.txt"
    panel_plan_path.write_text(format_panel_plan(session) + "\n", encoding="utf-8")
    created.append(panel_plan_path)

    caption_nature = package_dir / "caption_nature.txt"
    caption_nature.write_text(format_caption_draft(session, style="nature") + "\n", encoding="utf-8")
    created.append(caption_nature)

    caption_panels = package_dir / "caption_panels.txt"
    caption_panels.write_text(format_caption_draft(session, style="panels") + "\n", encoding="utf-8")
    created.append(caption_panels)

    legend_table = package_dir / "legend.tsv"
    _run_legend_save(session, str(legend_table), style="table")
    created.append(legend_table)

    dali_report = package_dir / "dali.txt"
    dali_report.write_text(format_dali_report(session) + "\n", encoding="utf-8")
    created.append(dali_report)

    figure_jobs = [
        ("composite", "figure composite"),
        ("interface", "figure interface"),
        ("pocket", "figure pocket"),
    ]
    if _selection_target(session):
        figure_jobs.extend(
            [
                ("selection_composite", "figure selection-composite"),
                ("selection_interface", "figure selection-interface"),
                ("selection_motif", "figure selection-motif"),
            ]
        )
    figure_jobs.append(("explode_composite", "figure explode-composite"))

    for label, figure_cmd in figure_jobs:
        _run_figure(session, figure_cmd.split(" ", 1)[1], executor=executor)
        snapshot_path = package_dir / f"{label}.png"
        _run_snapshot(session, str(snapshot_path), executor=executor, publication=True)
        created.append(snapshot_path)
        if label == "explode_composite":
            _run_layout_reset(session, executor=executor)

    return "\n".join(
        [
            "Figure package exported.",
            f"Directory: {package_dir}",
            "Created files:",
            *[f"- {path}" for path in created],
        ]
    )


def _run_dali(session, arg=None, executor=None):
    target = _resolve_dali_target(session, arg)
    if target is None:
        return "usage: /dali [selection|domain N|spec]"

    export_dir = Path.cwd() / "chimeraX_dali_exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_label = re.sub(r"[^A-Za-z0-9._-]+", "_", target["label"]).strip("_") or "query"
    path = export_dir / f"{safe_label}_{stamp}.pdb"

    selection_snapshot = _capture_selection_snapshot(session)
    commands = []
    try:
        # Critic P1 #6/#7: paths with spaces or shell-special chars must be quoted
        # for the ChimeraX command parser, not just embedded raw.
        from .toolbar_actions import _quote_command_token as _q
        quoted_path = _q(str(path))
        if target["select_spec"]:
            select_command = f"select {target['select_spec']}"
            commands.append(select_command)
            _run(session, select_command, executor=executor)
            save_command = f"save {quoted_path} format pdb models {target['model_spec']} selectedOnly true"
        else:
            save_command = f"save {quoted_path} format pdb models {target['model_spec']}"
        commands.append(save_command)
        _run(session, save_command, executor=executor)
    finally:
        _restore_selection_snapshot(session, selection_snapshot, executor=executor)

    dali_url = "https://ekhidna2.biocenter.helsinki.fi/dali/"
    try:
        webbrowser.open(dali_url)
        opened = True
    except Exception:
        opened = False

    lines = [
        "DALI export prepared.",
        f"- target: {target['label']}",
        f"- file: {path}",
        f"- dali_url: {dali_url}",
        "- next: upload the exported PDB/mmCIF file to the DALI server form",
    ]
    _append_dali_history(
        session,
        {
            "target": target["label"],
            "file": str(path),
            "submit_url": dali_url,
            "result_url": "",
            "summary": "",
        },
    )
    if opened:
        lines.append("- browser: opened DALI server")
    lines.append("Executed ChimeraX commands:")
    lines.extend(f"- {command}" for command in commands)
    return "\n".join(lines)


def _run_structure_web_tool(session, tool_name, executor=None):
    from .toolbar_actions import (
        launch_dali_server,
        launch_pdbefold_search,
        launch_pisa_server,
        launch_usalign,
        launch_vast_search,
    )

    key = str(tool_name or "").strip().lower()
    if key == "dali":
        return launch_dali_server(session, executor=executor)
    if key == "vast":
        return launch_vast_search(session, executor=executor)
    if key == "pdbefold":
        return launch_pdbefold_search(session, executor=executor)
    if key == "pisa":
        return launch_pisa_server(session, executor=executor)
    if key == "usalign":
        return launch_usalign(session, executor=executor)
    return "Unknown structure web tool."


def _run_dali_url(session, url_text):
    url = str(url_text or "").strip()
    if not url:
        return "usage: /daliurl <url>"
    lower = url.lower()
    if not (lower.startswith("http://") or lower.startswith("https://")):
        return f"DALI URL must start with http:// or https:// — got: {url}"
    history = _get_dali_history(session)
    if not history:
        _append_dali_history(session, {"target": "", "file": "", "submit_url": "", "result_url": url, "summary": ""})
    else:
        history[-1]["result_url"] = url
    return "\n".join(["DALI result URL stored.", f"- result_url: {url}"])


def _run_dali_summary(session, summary_text):
    summary = str(summary_text or "").strip()
    if not summary:
        return "usage: /dalisummary <text>"
    parsed = _parse_dali_summary(summary)
    history = _get_dali_history(session)
    if not history:
        _append_dali_history(session, {"target": "", "file": "", "submit_url": "", "result_url": "", "summary": summary, "parsed": parsed})
    else:
        history[-1]["summary"] = summary
        history[-1]["parsed"] = parsed
    return "\n".join(["DALI summary stored.", f"- summary: {summary}"])


def _run_dali_status(session):
    history = _get_dali_history(session)
    if not history:
        return "No DALI workflow history yet."
    item = history[-1]
    lines = ["Latest DALI workflow state:"]
    for key in ("target", "file", "submit_url", "result_url", "summary"):
        value = item.get(key, "")
        if value:
            lines.append(f"- {key}: {value}")
    parsed = item.get("parsed", {}) or {}
    for key in ("top_hit", "fold", "zscore", "rmsd", "aligned_length"):
        value = parsed.get(key, "")
        if value:
            lines.append(f"- {key}: {value}")
    if parsed.get("hits"):
        lines.append("- top_hits:")
        for hit in parsed["hits"][:5]:
            bits = [hit.get("title", "").strip()]
            if hit.get("zscore"):
                bits.append(f"Z {hit['zscore']}")
            if hit.get("rmsd"):
                bits.append(f"RMSD {hit['rmsd']}")
            if hit.get("aligned_length"):
                bits.append(f"len {hit['aligned_length']}")
            lines.append("  - " + "; ".join(bit for bit in bits if bit))
    return "\n".join(lines)


def _run_layout_reset(session, executor=None):
    commands = list(getattr(session, "_codex_bridge_layout_reverse_commands", []) or [])
    if not commands:
        return "No saved layout transform to reset."
    executed = []
    for command in commands:
        _run(session, command, executor=executor)
        executed.append(command)
    session._codex_bridge_layout_reverse_commands = []
    return "\n".join(
        [
            "Layout reset applied.",
            "Executed ChimeraX commands:",
            *[f"- {command}" for command in executed],
        ]
    )


def _parse_explode_args(text):
    """Split 'spacing labels=N' into (spacing_or_None, label_n_default_10)."""
    raw = str(text or "").strip()
    label_n = 10
    spacing = None
    for tok in raw.split():
        lower = tok.lower()
        if lower.startswith("labels=") or lower.startswith("label=") or lower.startswith("lbl="):
            try:
                label_n = max(0, min(50, int(tok.split("=", 1)[1])))
            except Exception:
                pass
        else:
            if spacing is None:
                spacing = _coerce_spacing(tok)
    return spacing, label_n


def _coerce_spacing(text):
    token = str(text or "").strip()
    if not token:
        return None
    try:
        value = float(token)
    except ValueError:
        return None
    if value <= 0:
        return None
    return max(0.5, min(500.0, value))


def _capture_selection_snapshot(session):
    from .semantic import get_session_semantics

    semantics = get_session_semantics(session)
    return {
        "ranges": list(semantics["selection"]["ranges"]),
        "models": list(semantics["selection"]["models"]),
    }


def _restore_selection_snapshot(session, snapshot, executor=None):
    _run(session, "select clear", executor=executor)
    if not snapshot:
        return
    specs = [spec for spec in snapshot.get("ranges", []) if spec]
    if not specs:
        specs = [spec for spec in snapshot.get("models", []) if spec]
    if specs:
        _run(session, "select " + " ".join(specs), executor=executor)


def _resolve_dali_target(session, text):
    from .semantic import extract_model_specs_from_text, extract_residue_specs_from_text, get_domain_selections

    token_text = str(text or "").strip()
    lowered = token_text.lower()

    if not token_text or "selection" in lowered or "선택" in lowered:
        snapshot = _capture_selection_snapshot(session)
        if snapshot["ranges"]:
            model_spec = snapshot["ranges"][0].split("/", 1)[0]
            return {
                "label": "selection",
                "model_spec": model_spec,
                "select_spec": " ".join(snapshot["ranges"]),
            }

    domain_match = re.search(r"(?:domain|도메인)\s*(\d+)", lowered)
    if domain_match:
        index = int(domain_match.group(1))
        model_specs = extract_model_specs_from_text(session, token_text, limit=1)
        model_hint = model_specs[0] if model_specs else None
        domains = [entry for entry in get_domain_selections(session, model_hint=model_hint) if entry["kind"] == "domain"]
        for entry in domains:
            if entry.get("display_index") == index or entry.get("chunk_index") == index:
                return {
                    "label": entry["selection_name"],
                    "model_spec": entry["model_spec"],
                    "select_spec": entry["spec"],
                }

    residue_specs = extract_residue_specs_from_text(session, token_text)
    if residue_specs:
        model_spec = residue_specs[0].split("/", 1)[0]
        return {
            "label": "spec",
            "model_spec": model_spec,
            "select_spec": " ".join(residue_specs),
        }

    chain_specs = _extract_chain_specs_from_text(session, token_text)
    if chain_specs:
        model_spec = chain_specs[0].split("/", 1)[0]
        return {
            "label": "chain",
            "model_spec": model_spec,
            "select_spec": " ".join(chain_specs),
        }

    model_specs = extract_model_specs_from_text(session, token_text, limit=1)
    if model_specs:
        return {
            "label": model_specs[0].replace("#", "model_"),
            "model_spec": model_specs[0],
            "select_spec": "",
        }
    return None


def _parse_dali_summary(text):
    source = str(text or "")
    parsed = {}

    hit = re.search(r"(?:top hit|best hit|hit)\s*[:=]\s*([^,;]+)", source, flags=re.IGNORECASE)
    if hit:
        parsed["top_hit"] = hit.group(1).strip()

    fold = re.search(r"(?:fold|family)\s*[:=]\s*([^,;]+)", source, flags=re.IGNORECASE)
    if fold:
        parsed["fold"] = fold.group(1).strip()

    zscore = re.search(r"(?:z[- ]?score|z)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)", source, flags=re.IGNORECASE)
    if zscore:
        parsed["zscore"] = zscore.group(1)

    rmsd = re.search(r"(?:rmsd)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)", source, flags=re.IGNORECASE)
    if rmsd:
        parsed["rmsd"] = rmsd.group(1)

    length = re.search(r"(?:aligned length|alignment length|length)\s*[:=]?\s*([0-9]+)", source, flags=re.IGNORECASE)
    if length:
        parsed["aligned_length"] = length.group(1)

    hits = []
    for line in source.splitlines():
        line = line.strip()
        if not line:
            continue
        rank_match = re.match(r"^\s*(\d+)[\).:-]?\s+(.+)$", line)
        if not rank_match:
            continue
        rank = rank_match.group(1)
        rest = rank_match.group(2)
        hit = {"rank": rank}
        zscore = re.search(r"(?:z[- ]?score|z)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)", rest, flags=re.IGNORECASE)
        rmsd = re.search(r"(?:rmsd)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)", rest, flags=re.IGNORECASE)
        length = re.search(r"(?:len|length)\s*[:=]?\s*([0-9]+)", rest, flags=re.IGNORECASE)
        title = re.split(r"(?:z[- ]?score|z)\s*[:=]?", rest, flags=re.IGNORECASE)[0].strip(" ,;")
        if title:
            hit["title"] = title
        if zscore:
            hit["zscore"] = zscore.group(1)
        if rmsd:
            hit["rmsd"] = rmsd.group(1)
        if length:
            hit["aligned_length"] = length.group(1)
        hits.append(hit)

    if hits:
        parsed["hits"] = hits[:10]
        if "top_hit" not in parsed and hits[0].get("title"):
            parsed["top_hit"] = hits[0]["title"]
        if "zscore" not in parsed and hits[0].get("zscore"):
            parsed["zscore"] = hits[0]["zscore"]
        if "rmsd" not in parsed and hits[0].get("rmsd"):
            parsed["rmsd"] = hits[0]["rmsd"]
        if "aligned_length" not in parsed and hits[0].get("aligned_length"):
            parsed["aligned_length"] = hits[0]["aligned_length"]

    return parsed


def _build_appearance_commands(session, text):
    lowered = str(text or "").lower()
    residue_specs = _extract_residue_specs_for_phrase(session, text)
    explicit_specs = _spec_terms_from_text(session, text)
    selection_based = bool(residue_specs)
    commands = []

    if selection_based:
        target_spec = "sel"
    elif explicit_specs:
        target_spec = " ".join(explicit_specs)
    else:
        target_spec = _selection_target(session)

    if residue_specs:
        commands.append("select " + " ".join(residue_specs))
        commands.append("name frozen residue_focus sel")

    style_command = _style_command_from_text(lowered, target_spec)
    if style_command:
        commands.append(style_command)

    color_command = _color_command_from_text(lowered, target_spec)
    if color_command:
        commands.append(color_command)

    palette_command = _palette_command_from_text(lowered, target_spec)
    if palette_command:
        commands.append(palette_command)

    transparency_command = _transparency_command_from_text(lowered, target_spec)
    if transparency_command:
        commands.append(transparency_command)

    if "label" in lowered or "라벨" in lowered:
        if target_spec:
            commands.append(f"label {target_spec} residues")

    if any(word in lowered for word in ("focus", "zoom", "가까이", "확대")) and target_spec:
        commands.append(f"view {target_spec}")

    return commands, residue_specs


def _build_manipulation_commands(session, text):
    lowered = str(text or "").lower()
    commands = []

    if any(word in lowered for word in ("surface area", "measure area", "면적")):
        command = _measure_area_command_from_text(session, text)
        if command:
            commands.append(command)

    if any(word in lowered for word in ("contact area", "contactarea", "접촉 면적")):
        command = _contactarea_command_from_text(session, text)
        if command:
            commands.append(command)

    if any(word in lowered for word in ("convexity", "볼록", "오목")):
        command = _convexity_command_from_text(session, text)
        if command:
            commands.append(command)

    if any(word in lowered for word in ("buried area", "buriedarea", "매몰 면적")):
        command = _buriedarea_command_from_text(session, text)
        if command:
            commands.append(command)

    if any(word in lowered for word in ("surface zone", "pocket surface", "surface pocket", "표면 포켓", "표면 zone")):
        command = _surface_zone_command_from_text(session, text)
        if command:
            commands.append(command)

    if any(word in lowered for word in ("surface unzone", "unzone", "zone 해제", "표면 zone 해제")):
        command = _surface_unzone_command_from_text(text)
        if command:
            commands.append(command)

    if any(word in lowered for word in ("angle", "각도")):
        command = _angle_command_from_text(session, text)
        if command:
            commands.append(command)

    if any(word in lowered for word in ("interfaces", "interface network", "buried area", "network diagram", "네트워크")):
        command = _interfaces_command_from_text(session, text)
        if command:
            commands.append(command)

    if any(word in lowered for word in ("interface residue", "contact residue", "interface select", "인터페이스 잔기")):
        command = _interfaces_select_command_from_text(session, text)
        if command:
            commands.append(command)

    if any(word in lowered for word in ("distance", "dist", "거리")):
        command = _distance_command_from_text(session, text)
        if command:
            commands.append(command)

    if any(word in lowered for word in ("hbond", "hydrogen bond", "수소결합")):
        if any(word in lowered for word in ("delete", "remove", "clear", "지워", "삭제")):
            commands.append("hbonds delete")
        else:
            command = _hbonds_command_from_text(session, text)
            if command:
                commands.append(command)

    if any(word in lowered for word in ("contact", "contacts", "접촉")):
        if any(word in lowered for word in ("delete", "remove", "clear", "지워", "삭제")):
            commands.append("~contacts")
        else:
            command = _contacts_command_from_text(session, text, kind="contacts")
            if command:
                commands.append(command)

    if any(word in lowered for word in ("clash", "clashes", "충돌")):
        if any(word in lowered for word in ("delete", "remove", "clear", "지워", "삭제")):
            commands.append("~clashes")
        else:
            command = _contacts_command_from_text(session, text, kind="clashes")
            if command:
                commands.append(command)

    if any(word in lowered for word in ("within", "around", "near", "zone", "주변", "이내")):
        command = _zone_command_from_text(session, text)
        if command:
            commands.append(command)

    if any(word in lowered for word in ("select", "선택", "고르", "pick")):
        command = _select_command_from_text(session, text)
        if command:
            commands.append(command)

    if any(word in lowered for word in ("show", "보여", "표시", "display")) and not any(word in lowered for word in ("figure", "그림")):
        command = _show_hide_command_from_text(session, "show", text)
        if command:
            commands.append(command)

    if any(word in lowered for word in ("hide", "숨", "끄", "off")):
        command = _show_hide_command_from_text(session, "hide", text)
        if command:
            commands.append(command)

    if any(word in lowered for word in ("turn", "rotate", "돌려", "회전")):
        command = _turn_command_from_text(text)
        if command:
            commands.append(command)

    if any(word in lowered for word in ("wait", "기다", "대기")):
        command = _wait_command_from_text(text)
        if command:
            commands.append(command)

    if any(word in lowered for word in ("stop", "멈춰", "중지", "정지")):
        commands.append("stop")

    if any(word in lowered for word in ("rock", "흔들", "왕복")):
        command = _rock_command_from_text(text)
        if command:
            commands.append(command)

    if any(word in lowered for word in ("wobble", "figure-eight", "8자")):
        command = _wobble_command_from_text(text)
        if command:
            commands.append(command)

    if any(word in lowered for word in ("zoom", "줌", "확대", "축소")):
        command = _zoom_command_from_text(text)
        if command:
            commands.append(command)

    if any(word in lowered for word in ("close", "닫", "꺼")):
        command = _close_command_from_text(text)
        if command:
            commands.append(command)

    return commands


def _extract_residue_specs_for_phrase(session, text):
    from .semantic import extract_residue_specs_from_text

    return extract_residue_specs_from_text(session, text)


def _style_command_from_text(lowered, target_spec):
    target = _spec_or_all(target_spec)
    if "hide surface" in lowered or "~surface" in lowered or ("surface" in lowered and any(word in lowered for word in ("hide", "off", "끄", "없애"))):
        return _clean_command(f"~surface {target}")
    if "surface" in lowered:
        return _clean_command(f"surface {target}")
    if any(word in lowered for word in ("cartoon", "ribbon", "리본")):
        return _clean_command(f"cartoon {target}")
    if any(word in lowered for word in ("stick", "sticks", "스틱")):
        return _clean_command(f"style {target} stick")
    return None


def _color_command_from_text(lowered, target_spec):
    target = _spec_or_all(target_spec)
    if any(word in lowered for word in ("by chain", "bychain", "chain color", "체인별")):
        return _clean_command(f"color {target} bychain")
    if any(word in lowered for word in ("by model", "bymodel", "모델별")):
        return _clean_command(f"color {target} bymodel")
    if any(word in lowered for word in ("by element", "byelement", "byatom", "원자종류", "원소별")):
        return _clean_command(f"color {target} byelement")
    if any(word in lowered for word in ("from atoms", "fromatoms")):
        return _clean_command(f"color {target} fromatoms")
    if any(word in lowered for word in ("from cartoons", "fromcartoons", "from ribbons", "fromribbons")):
        return _clean_command(f"color {target} fromcartoons")

    target_letters = _appearance_target_letters(lowered)
    color_name = _extract_color_name(lowered)
    if color_name:
        base = f"color {target} {color_name}"
        if target_letters:
            base += f" target {target_letters}"
        return _clean_command(base)
    return None


def _palette_command_from_text(lowered, target_spec):
    if "palette" not in lowered and "팔레트" not in lowered and "rainbow" not in lowered and "무지개" not in lowered:
        return None
    palette_name = _extract_palette_name(lowered)
    if "list" in lowered or "뭐있" in lowered or "목록" in lowered:
        return "palette list"
    target = f"{target_spec} " if target_spec else ""
    level = _palette_level(lowered)
    target_letters = _appearance_target_letters(lowered)
    parts = [f"rainbow {target}{level}"]
    if target_letters:
        parts.append(f"target {target_letters}")
    if palette_name:
        parts.append(f"palette {palette_name}")
    command = " ".join(part.strip() for part in parts if part.strip())
    return _clean_command(command)


def _transparency_command_from_text(lowered, target_spec):
    percent = _extract_percent(lowered)
    if percent is None:
        return None
    if not any(word in lowered for word in ("transparent", "transparency", "투명", "불투명")):
        return None
    target_kind = _transparency_target_kind(lowered)
    target_letters = {
        "surface": "s",
        "cartoon": "c",
        "atoms": "a",
        "all": "abcsp",
    }.get(target_kind, "s")
    target = target_spec or ""
    return _clean_command(f"transparency {target} {percent} target {target_letters}")


def _appearance_target_letters(lowered):
    if "surface" in lowered or "표면" in lowered:
        return "s"
    if any(word in lowered for word in ("cartoon", "ribbon", "리본")):
        return "c"
    if any(word in lowered for word in ("atom", "atoms", "원자")):
        return "a"
    return ""


def _palette_level(lowered):
    if any(word in lowered for word in ("chain", "chains", "체인")):
        return "chains"
    if any(word in lowered for word in ("structure", "structures", "모델")):
        return "structures"
    if any(word in lowered for word in ("polymer", "polymers")):
        return "polymers"
    return "residues"


def _extract_palette_name(lowered):
    palette_aliases = {
        "rainbow": "rainbow",
        "redblue": "redblue",
        "red-white-blue": "redblue",
        "bluered": "bluered",
        "blue-white-red": "bluered",
        "gray": "gray",
        "grayscale": "gray",
        "alphafold": "alphafold",
        "esmfold": "esmfold",
        "pae": "pae",
        "paired": "paired-10",
        "paired-10": "paired-10",
        "paired-12": "paired-12",
        "set3": "set3-12",
        "set3-12": "set3-12",
        "pastel": "pastel2-6",
        "pastel2": "pastel2-6",
        "pastel2-6": "pastel2-6",
    }
    for key, value in palette_aliases.items():
        if key in lowered:
            return value
    return None


def _extract_color_name(lowered):
    colors = [
        "red", "blue", "green", "yellow", "orange", "purple", "magenta", "cyan",
        "white", "black", "gray", "grey", "gold", "hotpink", "cornflowerblue",
        "lightgray", "darkcyan", "darkgoldenrod", "lime", "maroon",
    ]
    korean = {
        "빨강": "red",
        "빨간": "red",
        "파랑": "blue",
        "파란": "blue",
        "초록": "green",
        "녹색": "green",
        "노랑": "yellow",
        "노란": "yellow",
        "주황": "orange",
        "보라": "purple",
        "분홍": "hotpink",
        "하양": "white",
        "흰색": "white",
        "검정": "black",
        "검은": "black",
        "회색": "gray",
        "회색빛": "gray",
        "금색": "gold",
        "청록": "cyan",
    }
    hex_match = re.search(r"#[0-9a-f]{6}\b", lowered)
    if hex_match:
        return hex_match.group(0)
    for token, value in korean.items():
        if token in lowered:
            return value
    for color in colors:
        if color in lowered:
            return "gray" if color == "grey" else color
    return None


def _extract_output_path(text):
    token_text = str(text or "").strip()
    match = re.search(r"((?:~|/)?[^\s,]+?\.(?:mp4|mov|avi|wmv|webm|ogv|png))", token_text, flags=re.IGNORECASE)
    if not match:
        return None
    return match.group(1)


def _show_hide_command_from_text(session, action, text):
    lowered = str(text or "").lower()
    specs = _spec_terms_from_text(session, text)
    spec = " ".join(specs) if specs else (_selection_target(session) or "all")

    level = None
    if "surface" in lowered or "표면" in lowered:
        if action == "show":
            return _clean_command(f"surface {spec}")
        return _clean_command(f"hide {spec} surfaces")
    if any(word in lowered for word in ("cartoon", "ribbon", "리본")):
        level = "cartoons"
    elif any(word in lowered for word in ("atom", "atoms", "원자")):
        level = "atoms"
    elif any(word in lowered for word in ("model", "models", "모델")):
        level = "models"

    if action == "show" and level == "cartoons":
        return _clean_command(f"cartoon {spec}")

    base = action
    if spec:
        base += f" {spec}"
    if level:
        base += f" {level}"
    return _clean_command(base)


def _select_command_from_text(session, text):
    lowered = str(text or "").lower()
    if any(word in lowered for word in ("clear", "해제", "지워", "reset selection")):
        return "select clear"
    specs = _spec_terms_from_text(session, text)
    if specs:
        return "select " + " ".join(specs)
    if any(word in lowered for word in ("all", "전체", "전부")):
        return "select"
    return None


def _distance_command_from_text(session, text):
    lowered = str(text or "").lower()
    specs = _spec_terms_from_text(session, text)
    if len(specs) >= 2:
        return f"distance {specs[0]} {specs[1]}"
    if any(word in lowered for word in ("sel", "selected", "selection", "선택")) and len(specs) >= 1:
        return f"distance sel {specs[0]}"
    return None


def _angle_command_from_text(session, text):
    specs = _spec_terms_from_text(session, text)
    if len(specs) >= 4:
        return f"angle {specs[0]} {specs[1]} {specs[2]} {specs[3]}"
    if len(specs) >= 3:
        return f"angle {specs[0]} {specs[1]} {specs[2]}"
    return None


def _buriedarea_command_from_text(session, text):
    lowered = str(text or "").lower()
    specs = _spec_terms_from_text(session, text)
    if len(specs) >= 2:
        return _clean_command(f"measure buriedarea {specs[0]} withAtoms2 {specs[1]} listResidues true select true")
    if "protein" in lowered and "ligand" in lowered:
        return "measure buriedarea ligand withAtoms2 protein listResidues true select true"
    return None


def _measure_area_command_from_text(session, text):
    specs = _spec_terms_from_text(session, text)
    if specs:
        return _clean_command(f"measure area {specs[0]}")
    target = _selection_target(session)
    if target:
        return _clean_command(f"measure area {target}")
    return None


def _contactarea_command_from_text(session, text):
    specs = _spec_terms_from_text(session, text)
    if len(specs) >= 2:
        return _clean_command(f"measure contactarea {specs[0]} withSurface {specs[1]}")
    return None


def _convexity_command_from_text(session, text):
    specs = _spec_terms_from_text(session, text)
    if specs:
        return _clean_command(f"measure convexity {specs[0]}")
    target = _selection_target(session)
    if target:
        return _clean_command(f"measure convexity {target}")
    return None


def _hbonds_command_from_text(session, text):
    specs = _spec_terms_from_text(session, text)
    lowered = str(text or "").lower()
    if len(specs) >= 2:
        return _clean_command(f"hbonds {specs[0]} restrict {specs[1]} reveal true")
    if specs:
        return _clean_command(f"hbonds {specs[0]} reveal true")
    if "protein" in lowered and "ligand" in lowered:
        return "hbonds protein restrict ligand reveal true"
    if _selection_target(session):
        return "hbonds sel reveal true"
    return None


def _contacts_command_from_text(session, text, kind="contacts"):
    specs = _spec_terms_from_text(session, text)
    lowered = str(text or "").lower()
    if len(specs) >= 2:
        return _clean_command(f"{kind} {specs[0]} restrict {specs[1]} reveal true")
    if specs:
        return _clean_command(f"{kind} {specs[0]} reveal true")
    if "protein" in lowered and "ligand" in lowered:
        return f"{kind} protein restrict ligand reveal true"
    if _selection_target(session):
        return f"{kind} sel reveal true"
    return None


def _interfaces_select_command_from_text(session, text):
    specs = _spec_terms_from_text(session, text)
    if len(specs) >= 2:
        return _clean_command(f"interfaces select {specs[0]} contacting {specs[1]} bothSides true")
    return None


def _interfaces_command_from_text(session, text):
    specs = _spec_terms_from_text(session, text)
    if specs:
        return _clean_command(f"interfaces {' '.join(specs)}")
    if _selection_target(session):
        return "interfaces sel"
    return "interfaces protein"


def _zone_command_from_text(session, text):
    lowered = str(text or "").lower()
    cutoff = _extract_distance_cutoff(lowered)
    if cutoff is None:
        return None
    specs = _spec_terms_from_text(session, text)
    if specs:
        return _clean_command(f"select zone {' '.join(specs)} {cutoff} residues true")
    if _selection_target(session):
        return _clean_command(f"select zone sel {cutoff} residues true")
    return None


def _surface_zone_command_from_text(session, text):
    lowered = str(text or "").lower()
    cutoff = _extract_distance_cutoff(lowered)
    if cutoff is None:
        cutoff = "2.0"
    specs = _spec_terms_from_text(session, text)
    atom_spec = " ".join(specs) if specs else (_selection_target(session) or "")
    if not atom_spec:
        return None
    surface_spec = _selection_target(session) or ""
    if surface_spec:
        return _clean_command(f"surface zone {surface_spec} nearAtoms {atom_spec} distance {cutoff}")
    return _clean_command(f"surface zone nearAtoms {atom_spec} distance {cutoff}")


def _surface_unzone_command_from_text(text):
    token = str(text or "").strip()
    if token:
        return _clean_command(f"surface unzone {token}")
    return "surface unzone"


def _movie_command_from_text(text):
    token_text = str(text or "").strip()
    lowered = token_text.lower()
    if not lowered:
        return None

    supersample = 3
    quality = "good"
    for tok in token_text.split():
        low = tok.lower()
        if low.startswith("ss=") or low.startswith("supersample="):
            try: supersample = max(1, min(8, int(tok.split("=", 1)[1])))
            except Exception: pass
        elif low.startswith("quality=") or low.startswith("q="):
            val = tok.split("=", 1)[1].strip().lower()
            if val in ("low", "fair", "good", "high", "highest"):
                quality = val

    if "formats" in lowered or "format list" in lowered or "포맷" in lowered:
        return "movie formats"
    if "abort" in lowered or "취소" in lowered:
        return "movie abort"
    if "status" in lowered or "상태" in lowered:
        return "movie status"
    if "reset" in lowered or "리셋" in lowered or "초기화" in lowered:
        return "movie reset"
    if "stop" in lowered or "중지" in lowered or "정지" in lowered:
        return "movie stop"
    if "record" in lowered or "start" in lowered or "녹화" in lowered:
        return f"movie record supersample {supersample}"
    if "encode" in lowered or "save" in lowered or "export" in lowered or "저장" in lowered:
        path = _extract_output_path(token_text)
        if path:
            return _clean_command(f"movie encode output {path} quality {quality}")
        return f"movie encode output ~/Desktop/movie.mp4 quality {quality}"
    return None


def _turn_command_from_text(text):
    lowered = str(text or "").lower()
    axis = "y"
    angle = None

    if any(word in lowered for word in ("left", "왼", "좌")):
        axis = "y"
        angle = -90
    elif any(word in lowered for word in ("right", "오른", "우")):
        axis = "y"
        angle = 90
    elif any(word in lowered for word in ("up", "위")):
        axis = "x"
        angle = -90
    elif any(word in lowered for word in ("down", "아래")):
        axis = "x"
        angle = 90

    if any(token in lowered for token in (" x ", " x축", " x-axis")):
        axis = "x"
    elif any(token in lowered for token in (" z ", " z축", " z-axis")):
        axis = "z"
    elif any(token in lowered for token in (" y ", " y축", " y-axis")):
        axis = "y"

    number_match = re.search(r"(-?\d+(?:\.\d+)?)\s*(?:도|deg|degrees?)", lowered)
    if number_match:
        angle = float(number_match.group(1))
    elif angle is None and any(word in lowered for word in ("turn", "rotate", "돌려", "회전")):
        angle = 90.0

    if angle is None:
        return None
    angle = float(angle)
    if angle.is_integer():
        angle_text = str(int(angle))
    else:
        angle_text = str(angle)
    return f"turn {axis} {angle_text}"


def _rock_command_from_text(text):
    lowered = str(text or "").lower()
    axis = "y"
    if "x" in lowered and any(token in lowered for token in ("x축", "x-axis", " x ")):
        axis = "x"
    elif "z" in lowered and any(token in lowered for token in ("z축", "z-axis", " z ")):
        axis = "z"
    angle = _extract_rotation_angle(lowered, default=30.0)
    return f"rock {axis} {angle}"


def _wobble_command_from_text(text):
    lowered = str(text or "").lower()
    axis = "y"
    if "x" in lowered and any(token in lowered for token in ("x축", "x-axis", " x ")):
        axis = "x"
    elif "z" in lowered and any(token in lowered for token in ("z축", "z-axis", " z ")):
        axis = "z"
    angle = _extract_rotation_angle(lowered, default=30.0)
    return f"wobble {axis} {angle}"


def _zoom_command_from_text(text):
    lowered = str(text or "").lower()
    factor = None
    number_match = re.search(r"(\d+(?:\.\d+)?)\s*(x|배)?", lowered)

    if any(word in lowered for word in ("zoom in", "zoomin", "확대")):
        factor = 1.5
    elif any(word in lowered for word in ("zoom out", "zoomout", "축소")):
        factor = 0.67

    if number_match and any(word in lowered for word in ("zoom", "줌", "확대", "축소")):
        value = float(number_match.group(1))
        if value > 10:
            factor = value / 100.0
        else:
            factor = value

    if factor is None:
        if "zoom" in lowered or "줌" in lowered:
            return "zoom"
        return None
    factor = max(0.05, min(50.0, float(factor)))
    if factor.is_integer():
        factor_text = str(int(factor))
    else:
        factor_text = f"{factor:.2f}".rstrip("0").rstrip(".")
    return f"zoom {factor_text}"


def _wait_command_from_text(text):
    lowered = str(text or "").lower()
    match = re.search(r"(\d+)\s*(?:frames?|프레임)?", lowered)
    if match:
        frames = max(1, min(3600, int(match.group(1))))
        return f"wait {frames}"
    if "wait" in lowered or "기다" in lowered or "대기" in lowered:
        return "wait 1"
    return None


def _close_command_from_text(text):
    token_text = str(text or "").strip()
    specs = _extract_model_specs_from_text(token_text)
    if specs:
        return "close " + " ".join(specs)
    if token_text:
        return _clean_command(f"close {token_text}")
    return "close"


def _extract_distance_cutoff(lowered):
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:a|å|angstrom|ang|옹스트롬)?", lowered)
    if not match:
        return None
    value = float(match.group(1))
    if value <= 0:
        return None
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _extract_rotation_angle(lowered, default=30.0):
    match = re.search(r"(-?\d+(?:\.\d+)?)\s*(?:도|deg|degrees?)", lowered)
    if match:
        value = float(match.group(1))
    else:
        value = float(default)
    if value.is_integer():
        return str(int(value))
    return str(value)


def _spec_terms_from_text(session, text):
    lowered = str(text or "").lower()
    specs = list(_extract_residue_specs_for_phrase(session, text))
    specs.extend(_extract_chain_specs_from_text(session, text))
    specs.extend(_extract_model_specs_from_text(session, text))
    builtin_terms = []
    for token in ("protein", "ligand", "solvent", "nucleic", "sidechain", "backbone", "sel"):
        if token in lowered:
            builtin_terms.append(token)
    if any(word in lowered for word in ("selected", "selection", "선택")) and "sel" not in builtin_terms:
        builtin_terms.append("sel")
    for term in builtin_terms:
        if term not in specs:
            specs.append(term)
    deduped = []
    for term in specs:
        if term not in deduped:
            deduped.append(term)
    return deduped


def _extract_chain_specs_from_text(session, text):
    try:
        from .semantic import extract_chain_specs_from_text as _extract
        return _extract(session, text)
    except Exception:
        return []


def _extract_model_specs_from_text(session, text):
    try:
        from .semantic import extract_model_specs_from_text as _extract
        return _extract(session, text)
    except Exception:
        hits = []
        for match in re.finditer(r"(#\d+(?:\.\d+)*)", str(text or "")):
            hits.append(match.group(1))
        return hits


def _format_local_result(command):
    return "\n".join(
        [
            "Local ChimeraX action executed.",
            "Executed ChimeraX commands:",
            f"- {command}",
        ]
    )


def _clean_command(command):
    return " ".join(str(command).split())


def _spec_or_all(spec):
    token = str(spec or "").strip()
    return token or "all"


def _show_selection_atoms_command():
    return "show sel atoms"


def _color_selection_command(color, target="c"):
    return _clean_command(f"color sel {color} target {target}")


def _figure_color_mode(session=None, explicit=None):
    raw = explicit if explicit is not None else getattr(session, "_codex_figure_color_mode", "auto")
    mode = str(raw or "auto").strip().lower().replace("-", "_")
    aliases = {
        "keep": "preserve",
        "current": "preserve",
        "existing": "preserve",
        "preserve_current": "preserve",
        "chain": "bychain",
        "by_chain": "bychain",
        "gray": "neutral",
        "grey": "neutral",
        "domain_safe": "domain",
        "domains": "domain",
    }
    mode = aliases.get(mode, mode)
    if mode in {"auto", "preserve", "bychain", "neutral", "domain"}:
        return mode
    return "auto"


def _figure_scaffold_color_commands(session=None, scope="", scaffold_color="lightgray", default_scheme=None, color_mode=None):
    mode = _figure_color_mode(session, explicit=color_mode)
    if mode == "preserve":
        return []
    if mode == "bychain":
        return [_color_scheme_command(scope, "bychain")]
    if mode in {"neutral", "domain"}:
        color = scaffold_color or "lightgray"
        return [_color_scope_command(scope, color)]
    if default_scheme:
        return [_color_scheme_command(scope, default_scheme)]
    if scaffold_color:
        return [_color_scope_command(scope, scaffold_color)]
    return []


def _publication_base_commands(scope="", scaffold_color="lightgray", session=None, color_mode=None):
    mode = _figure_color_mode(session, explicit=color_mode)
    if mode == "preserve":
        commands = [
            "set bgColor white",
            f"cartoon {scope}".strip(),
            _clean_command(f"hide {_spec_or_all(scope)} atoms"),
            f"~surface {scope}".strip(),
            f"cartoon style width {PUBLICATION_CARTOON_WIDTH} thick {PUBLICATION_CARTOON_THICK}",
            "lighting soft",
            f"graphics silhouettes true width {PUBLICATION_SILHOUETTE_WIDTH} color {PUBLICATION_SILHOUETTE_COLOR} depthJump {PUBLICATION_SILHOUETTE_DEPTH_JUMP}",
        ]
        return [_clean_command(command) for command in commands if str(command or "").strip()]

    commands = [
        "set bgColor white",
        "preset sil",
        "preset cartoon",
        f"cartoon {scope}".strip(),
        f"~surface {scope}".strip(),
        *_figure_scaffold_color_commands(session, scope, scaffold_color, color_mode=mode),
        f"cartoon style width {PUBLICATION_CARTOON_WIDTH} thick {PUBLICATION_CARTOON_THICK}",
        "lighting soft",
        f"graphics silhouettes true width {PUBLICATION_SILHOUETTE_WIDTH} color {PUBLICATION_SILHOUETTE_COLOR} depthJump {PUBLICATION_SILHOUETTE_DEPTH_JUMP}",
    ]
    return [_clean_command(command) for command in commands if str(command or "").strip()]


def _selection_stick_style_commands(color):
    return [
        _show_selection_atoms_command(),
        "style sel stick",
        _clean_command(f"size sel stickRadius {PUBLICATION_STICK_RADIUS}"),
        _color_selection_command(color),
    ]


def _color_scope_command(spec, color, target="ac"):
    return _clean_command(f"color {_spec_or_all(spec)} {color} target {target}")


def _color_scheme_command(spec, scheme):
    return _clean_command(f"color {_spec_or_all(spec)} {scheme}")


def _slug_group_label(text):
    return re.sub(r"[^A-Za-z0-9]+", "_", str(text or "")).strip("_").lower()


def _domain_color(entry):
    family_index = max(0, int(entry.get("display_index", 1)) - 1) % len(DOMAIN_COLOR_FAMILIES)
    family = DOMAIN_COLOR_FAMILIES[family_index]
    chain_token = str(entry.get("chain_id", "") or "")
    variant_index = sum(ord(char) for char in chain_token) % len(family)
    return family[variant_index]


def _run(session, command, executor=None):
    from chimerax.core.commands import run

    if executor is not None:
        result = executor(command)
        maybe_apply_stick_context_colors_for_command(session, command)
        _restore_charge_colors_for_color_command(session, command)
        return result
    result = run(session, command)
    maybe_apply_stick_context_colors_for_command(session, command)
    _restore_charge_colors_for_color_command(session, command)
    return result


def _restore_charge_colors_for_color_command(session, command):
    text = str(command or "").strip()
    if not text.lower().startswith("color "):
        return
    try:
        tokens = shlex.split(text)
    except Exception:
        tokens = text.split()
    if len(tokens) < 2 or tokens[0].lower() != "color":
        return
    lowered = [token.lower() for token in tokens]
    if any(token in lowered for token in ("byelement", "byatom", "byhetero", "byhet", "fromatoms", "fromcartoons", "fromribbons")):
        return
    if len(tokens) > 2 and lowered[1] in {"name", "delete", "list", "show", "modify", "sequential"}:
        return
    try:
        target_index = lowered.index("target")
        target = lowered[target_index + 1] if target_index + 1 < len(lowered) else ""
    except ValueError:
        target = "abcspf"
    if target and "a" not in target.lower():
        return
    if len(tokens) == 2 or lowered[1] in {"bychain", "bymodel", "byidentity", "bypolymer", "random"}:
        spec = "all"
    else:
        spec = tokens[1]
    restore_charge_colors(session, spec)


def _emit(progress, message):
    if progress is not None:
        progress(message, kind="info")


def _infer_clause_action(session, clause):
    lowered = normalized_prompt_for_matching(clause)
    if not lowered:
        return None

    model_hint = _extract_model_hint(session, clause)

    scene_request = _scene_phrase_request(clause, lowered=lowered)
    if scene_request is not None:
        return {
            "label": f"scene {scene_request['action']}",
            "source": clause,
            "run": lambda req=scene_request: _run_scene(session, _scene_request_arg(req)),
        }

    if "legend" in lowered:
        if any(word in lowered for word in ("save-table", "savetable", "table", "tsv")):
            return {
                "label": "legend save-table",
                "source": clause,
                "run": lambda: _run_legend_save(session, None, style="table"),
            }
        if any(word in lowered for word in ("save", "export", "write", "저장")):
            return {
                "label": "legend save",
                "source": clause,
                "run": lambda: _run_legend_save(session),
            }
        from .semantic import format_legend_report

        return {
            "label": "legend",
            "source": clause,
            "run": lambda: format_legend_report(session, model_hint=model_hint),
        }

    if "caption" in lowered or "캡션" in lowered:
        from .semantic import format_caption_draft

        style = _infer_caption_style(lowered)
        return {
            "label": f"caption {style}",
            "source": clause,
            "run": lambda: format_caption_draft(session, model_hint=model_hint, style=style),
        }

    if "panel" in lowered or "panels" in lowered:
        from .semantic import format_panel_plan

        return {
            "label": "panels",
            "source": clause,
            "run": lambda: format_panel_plan(session, model_hint=model_hint),
        }

    if "package" in lowered or "패키지" in lowered:
        return {
            "label": "package",
            "source": clause,
            "run": lambda: _run_package_export(session),
        }

    if "dali" in lowered:
        if "http" in lowered:
            return {
                "label": "daliurl",
                "source": clause,
                "run": lambda: _run_dali_url(session, clause),
            }
        if any(word in lowered for word in ("summary", "hit", "top hit", "요약", "결과")):
            return {
                "label": "dalisummary",
                "source": clause,
                "run": lambda: _run_dali_summary(session, clause),
            }
        if any(word in lowered for word in ("status", "state", "상태")):
            return {
                "label": "dalistatus",
                "source": clause,
                "run": lambda: _run_dali_status(session),
            }
        return {
            "label": "dali",
            "source": clause,
            "run": lambda: _run_dali(session, clause),
        }

    if any(word in lowered for word in ("snapshot", "screenshot", "save png", "save image", "이미지 저장")):
        publication = any(word in lowered for word in ("publication", "paper", "논문"))
        return {
            "label": "snapshot publication" if publication else "snapshot",
            "source": clause,
            "run": lambda: _run_snapshot(session, None, publication=publication),
        }

    if any(word in lowered for word in ("movie", "record", "encode", "animation", "animate", "영상", "무비", "녹화", "포맷", "abort", "취소")):
        return {
            "label": "movie",
            "source": clause,
            "run": lambda: _run_movie(session, clause),
        }

    report_action = _best_report_action(session, clause, lowered, model_hint)
    if report_action is not None and (report_action.get("score", 0) >= 4) and not _has_explicit_figure_words(lowered):
        return report_action

    if figure_mode := _infer_figure_mode_from_clause(session, clause, lowered):
        return {
            "label": f"figure {figure_mode}",
            "source": clause,
            "run": lambda: _run_figure(session, figure_mode),
        }

    appearance_commands, _appearance_specs = _build_appearance_commands(session, clause)
    manipulation_commands = _build_manipulation_commands(session, clause)
    appearance_score = _appearance_intent_score(lowered) if appearance_commands else 0
    manipulation_score = _manipulation_intent_score(lowered) if manipulation_commands else 0

    if appearance_score and appearance_score >= manipulation_score + 1:
        return {
            "label": "appearance",
            "source": clause,
            "run": lambda: _run_appearance_phrase(session, clause),
        }

    if manipulation_score:
        return {
            "label": "manipulation",
            "source": clause,
            "run": lambda: _run_manipulation_phrase(session, clause),
        }

    if appearance_score:
        return {
            "label": "appearance",
            "source": clause,
            "run": lambda: _run_appearance_phrase(session, clause),
        }

    if report_action is not None:
        return report_action

    return None


def _infer_figure_mode_from_clause(session, clause, lowered):
    figureish = _looks_like_figure_intent(lowered)
    if not figureish:
        return None

    if any(word in lowered for word in ("next", "다음")):
        return "next"
    if any(word in lowered for word in ("cycle", "순환", "돌려")):
        return "cycle"
    if any(word in lowered for word in ("back", "previous", "이전", "되돌")):
        return "back"
    if any(word in lowered for word in ("repeat", "again", "다시")):
        return "repeat"
    if any(word in lowered for word in ("lab", "recommend", "추천")):
        return "lab"

    selected = any(word in lowered for word in ("selected", "selection", "선택", "현재 선택"))
    overview = any(word in lowered for word in ("overview", "summary", "overall", "global", "한눈에", "개요", "전체 구조", "요약"))
    closeup = any(word in lowered for word in ("close-up", "closeup", "zoom", "확대", "클로즈업", "가까이"))
    interface = any(word in lowered for word in ("interface", "접촉", "올리고머", "oligomer"))
    motif = "motif" in lowered or "패턴" in lowered
    pocket = any(word in lowered for word in ("pocket", "active site", "active-site", "활성부위", "촉매", "ligand", "metal"))
    explode = any(word in lowered for word in ("explode", "spread", "separate", "apart", "분리", "벌려"))
    domain = any(word in lowered for word in ("domain", "chunk", "도메인"))
    assembly = any(word in lowered for word in ("assembly", "multimer", "complex", "복합체", "멀티머"))
    beautify = _is_beautify_request(lowered)

    if selected:
        if overview and (interface or motif or pocket):
            return "selection-composite"
        if interface:
            return "selection-interface"
        if motif:
            return "selection-motif"
        if pocket or closeup:
            return "selection-pocket"
        return "selection"

    if explode and (overview or pocket or interface):
        return "explode-composite"
    if explode:
        spacing = _extract_percent(lowered)
        return f"explode {spacing}" if spacing is not None else "explode"
    if interface:
        return "interface"
    if pocket or closeup:
        return "pocket"
    if assembly:
        return "assembly"
    if domain:
        return "domains"
    if overview:
        return "composite"
    if beautify:
        return "clean"
    return "publication"


def _is_beautify_request(lowered):
    return any(token in lowered for token in BEAUTIFY_TOKENS)


def _looks_like_figure_intent(lowered):
    explicit = any(word in lowered for word in ("figure", "publication", "paper figure", "그림", "논문 그림"))
    beautify = _is_beautify_request(lowered)
    if not explicit and not beautify:
        if any(
            word in lowered
            for word in (
                "distance", "angle", "hbond", "hydrogen bond", "contact", "clash",
                "buried area", "measure area", "contact area", "convexity",
                "surface zone", "unzone", "select zone", "zone",
                "movie", "record", "encode", "wait", "stop", "close",
                "거리", "각도", "수소결합", "접촉", "충돌", "매몰 면적", "면적", "볼록", "오목",
                "기다", "멈춰", "정지", "중지", "닫", "녹화", "영상",
            )
        ):
            return False
        if any(word in lowered for word in ("network", "diagram", "네트워크", "도표")):
            return False
    figure_controls = any(word in lowered for word in ("next", "cycle", "back", "repeat", "lab", "추천"))
    structural_cues = any(
        word in lowered
        for word in (
            "overview", "close-up", "closeup", "개요", "클로즈업", "한눈에",
            "interface", "접촉", "oligomer", "multimer", "복합체", "멀티머",
            "pocket", "active site", "active-site", "활성부위", "촉매",
            "domain", "chunk", "도메인",
            "explode", "spread", "separate", "분리", "벌려",
            "selection", "selected", "선택",
            "composite", "summary", "overall", "global", "요약",
        )
    )
    figure_verbs = any(word in lowered for word in ("view", "render", "publication", "show", "display", "보여", "보이", "표시", "강조"))
    return explicit or beautify or figure_controls or (structural_cues and figure_verbs)


def _has_explicit_figure_words(lowered):
    return any(word in lowered for word in ("figure", "publication", "paper figure", "그림", "논문 그림")) or _is_beautify_request(lowered)


def _appearance_intent_score(lowered):
    score = 0
    for token in (
        "surface", "표면", "cartoon", "ribbon", "리본", "stick", "sticks", "스틱",
        "color", "colour", "색", "컬러", "palette", "팔레트", "rainbow", "무지개",
        "transparency", "transparent", "투명", "불투명", "bychain", "bymodel", "byelement",
        "fromatoms", "fromcartoons", "원소별", "체인별", "모델별",
    ):
        if token in lowered:
            score += 2
    if any(word in lowered for word in ("show", "hide", "보여", "숨", "display")):
        score += 1
    return score


def _manipulation_intent_score(lowered):
    score = 0
    for token in (
        "distance", "dist", "거리", "angle", "각도", "hbond", "hydrogen bond", "수소결합",
        "contact", "contacts", "접촉", "clash", "clashes", "충돌",
        "within", "around", "near", "zone", "주변", "이내",
        "surface area", "surface zone", "unzone", "buried area", "contact area", "measure area",
        "convexity", "매몰 면적", "접촉 면적", "면적", "볼록", "오목",
        "turn", "rotate", "회전", "rock", "흔들", "wobble", "zoom", "줌",
        "wait", "기다", "stop", "멈춰", "close", "닫",
    ):
        if token in lowered:
            score += 2
    if any(word in lowered for word in ("select", "선택", "pick")):
        score += 1
    if any(word in lowered for word in ("surface area", "measure area", "contact area", "buried area", "surface zone", "interface network")):
        score += 1
    return score


def _best_report_action(session, clause, lowered, model_hint):
    from .semantic import (
        format_analyze_report,
        format_annotation_report,
        format_catalytic_report,
        format_catalytic_workflow_report,
        format_chains_report,
        format_complex_report,
        format_domains_report,
        format_ligand_report,
        format_metal_report,
        format_motif_report,
        format_roles_report,
        format_selection_focus_report,
        format_selection_overlap_report,
        format_sequence_report,
        format_uniprot_feature_report,
        format_uniprot_motif_report,
    )
    from .conservation import format_conservation_report
    from .membrane import format_membrane_report
    from .pisa import format_pisa_report

    motif_model_hint, motif_text = _parse_motif_args(clause)
    candidates = []

    def add(score, label, run):
        if score > 0:
            candidates.append((score, label, run))

    analyze_words = any(word in lowered for word in ("analy", "analysis", "inspect", "분석", "요약", "summarize"))
    selected_words = any(word in lowered for word in ("selected", "selection", "선택"))

    reportish = any(word in lowered for word in ("analy", "analysis", "inspect", "분석", "요약", "summarize", "report", "정보", "summary", "find", "identify", "찾", "후보", "triage"))
    add(6 if selected_words and analyze_words else 0, "selected analyze", lambda: format_selection_focus_report(session, model_hint=model_hint))
    add(6 if any(word in lowered for word in ("uniprot", "타겟 서열", "타겟서열", "target sequence", "서열")) and any(word in lowered for word in ("motif", "모티프", "패턴")) else 0,
        "uniprot motif",
        lambda: format_uniprot_motif_report(session, query_text=clause, model_hint=motif_model_hint or model_hint, motif_text=motif_text))
    add(5 if any(word in lowered for word in ("annotat", "annotation", "uniprot", "주석", "어노테이션")) else 0, "annotate", lambda: format_annotation_report(session, model_hint=model_hint))
    add(5 if any(word in lowered for word in ("feature", "features", "uniprot feature", "site annotation", "기능부위")) and reportish else 0, "features", lambda: format_uniprot_feature_report(session, model_hint=model_hint))
    add(5 if any(word in lowered for word in ("motif", "모티프", "패턴")) and reportish else 0, "motif", lambda: format_motif_report(session, model_hint=motif_model_hint or model_hint, motif_text=motif_text))
    add(6 if any(word in lowered for word in ("conservation", "consurf", "conserved", "보존", "보존성", "보존도")) and reportish else 0,
        "conservation",
        lambda: format_conservation_report(session, model_hint=model_hint, query_text=clause))
    add(5 if any(word in lowered for word in ("metal", "zn", "mg", "mn", "fe", "cofactor", "금속")) and reportish else 0, "metal", lambda: format_metal_report(session, model_hint=model_hint))
    add(5 if any(word in lowered for word in ("ligand", "substrate", "pocket", "리간드")) and reportish else 0, "ligand", lambda: format_ligand_report(session, model_hint=model_hint))
    add(5 if any(word in lowered for word in ("membrane", "bilayer", "transmembrane", "lipid", "opm", "ppm", "막", "멤브레인", "지질막", "막단백", "소수성")) and reportish else 0, "membrane", lambda: format_membrane_report(session, model_hint=model_hint))
    add(5 if any(word in lowered for word in ("pisa", "pdbe-pisa", "interface area", "interface surface", "buried surface", "접촉면", "계면", "매몰 면적")) and reportish else 0, "pisa", lambda: format_pisa_report(session, model_hint=model_hint))
    catalytic_words = any(word in lowered for word in ("catalytic", "촉매", "active residue", "active-site residue", "active site", "active-site"))
    catalytic_workflow_words = any(word in lowered for word in ("triage", "workflow", "review", "test", "검증", "최적화", "확인", "find", "identify", "찾", "후보"))
    add(
        6 if catalytic_words and reportish else 0,
        "catalytic",
        lambda: format_catalytic_workflow_report(session, model_hint=model_hint)
        if catalytic_workflow_words
        else format_catalytic_report(session, model_hint=model_hint),
    )
    add(4 if any(word in lowered for word in ("complex", "oligomer", "multimer", "복합체")) and reportish else 0, "complex", lambda: format_complex_report(session, model_hint=model_hint))
    add(4 if any(word in lowered for word in ("role", "scaffold", "assembly role")) and reportish else 0, "roles", lambda: format_roles_report(session, model_hint=model_hint))
    add(4 if any(word in lowered for word in ("domain", "chunk", "도메인")) and reportish else 0, "domains", lambda: format_domains_report(session, model_hint=model_hint))
    add(4 if any(word in lowered for word in ("sequence", "서열")) and reportish else 0, "sequence", lambda: format_sequence_report(session, model_hint=model_hint))
    add(4 if any(word in lowered for word in ("chain", "chains", "체인")) and reportish else 0, "chains", lambda: format_chains_report(session, model_hint=model_hint))
    add(3 if selected_words else 0, "selected", lambda: format_selection_overlap_report(session, model_hint=model_hint))
    add(1 if analyze_words else 0, "analyze", lambda: format_analyze_report(session, model_hint=model_hint))

    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], item[1]))
    score, label, run = candidates[0]
    return {
        "label": label,
        "source": clause,
        "run": run,
        "score": score,
    }


def _infer_caption_style(lowered):
    if "nature" in lowered:
        return "nature"
    if "panel" in lowered or "panels" in lowered:
        return "panels"
    if "selection" in lowered or "선택" in lowered:
        return "selection"
    if "domain" in lowered or "도메인" in lowered:
        return "domain"
    if "pocket" in lowered or "active site" in lowered or "활성부위" in lowered:
        return "pocket"
    if "interface" in lowered or "접촉" in lowered:
        return "interface"
    if "short" in lowered:
        return "short"
    return "paper"


def _extract_model_hint(session, text):
    token_text = str(text or "")
    try:
        chain_specs = _extract_chain_specs_from_text(session, token_text)
        if chain_specs:
            return chain_specs[0]
    except Exception:
        pass
    explicit = re.search(r"(#\d+(?:\.\d+)*)", token_text)
    if explicit:
        return explicit.group(1)
    try:
        from .semantic import extract_model_specs_from_text
        specs = extract_model_specs_from_text(session, token_text, limit=1)
        return specs[0] if specs else None
    except Exception:
        return None


def _split_compound_prompt(prompt):
    text = prompt.strip()
    if not text:
        return []
    parts = re.split(
        r"\s*(?:,|;| then | and then | and | plus | 그리고 | 하고 | 하면서 | 후에 | 다음에 | 이어서 | 그다음 )\s*",
        text,
        flags=re.IGNORECASE,
    )
    return [part.strip() for part in parts if part.strip()]


def _handlers_by_name(handler_names):
    namespace = globals()
    return [namespace[name] for name in handler_names]
