import json

from .builtin_actions import run_figure_mode
from .docs_index import format_docs_snippets, likely_command_aliases
from .semantic import (
    format_annotation_report,
    format_catalytic_report,
    format_catalytic_workflow_report,
    format_chains_report,
    format_complex_report,
    format_domains_report,
    format_ligand_report,
    format_metal_report as format_existing_metal_report,
    format_models_report,
    format_motif_report,
    format_research_brief,
    format_roles_report,
    format_selection_focus_report,
    format_selection_overlap_report,
    format_sequence_report,
)
from .membrane import format_membrane_report
from .metal_placement import (
    format_metal_evidence_report,
    format_metal_report as format_predicted_metal_report,
    run_metal_placement_pipeline,
)
from .pisa import format_pisa_report


FIGURE_MODE_OPTIONS = (
    "lab",
    "next",
    "cycle",
    "back",
    "repeat",
    "clean",
    "publication",
    "selection",
    "selection-pocket",
    "selection-motif",
    "selection-interface",
    "selection-composite",
    "composite",
    "explode-composite",
    "domains",
    "roles",
    "assembly",
    "pocket",
    "interface",
    "explode",
)

ANALYSIS_REPORTS = (
    "brief",
    "models",
    "selection",
    "selection_focus",
    "annotation",
    "ligand",
    "metal",
    "metal_candidates",
    "metal_evidence",
    "catalytic",
    "catalytic_workflow",
    "membrane",
    "pisa",
    "domains",
    "complex",
    "roles",
    "sequence",
    "chains",
    "motif",
)


def openai_agent_tool_definitions():
    return [
        {
            "type": "function",
            "name": "get_scene_state",
            "description": "Return compact live ChimeraX context, including open models, selection, view, recent command history, and prior AI state.",
            "parameters": {
                "type": "object",
                "properties": {
                    "include_models": {"type": "boolean"},
                    "include_selection": {"type": "boolean"},
                },
                "required": ["include_models", "include_selection"],
                "additionalProperties": False,
            },
            "strict": True,
        },
        {
            "type": "function",
            "name": "run_chimerax_command",
            "description": "Run one exact UCSF ChimeraX command line in the live session. Use only documented ChimeraX commands and valid model/atom specs from scene state.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["command", "reason"],
                "additionalProperties": False,
            },
            "strict": True,
        },
        {
            "type": "function",
            "name": "run_chimerax_command_batch",
            "description": "Run a short ordered batch of exact UCSF ChimeraX command lines. Keep batches small so the scene can be inspected after each action.",
            "parameters": {
                "type": "object",
                "properties": {
                    "commands": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": 6,
                    },
                    "reason": {"type": "string"},
                },
                "required": ["commands", "reason"],
                "additionalProperties": False,
            },
            "strict": True,
        },
        {
            "type": "function",
            "name": "apply_figure_mode",
            "description": "Apply one of this plugin's built-in figure/view presets when it matches the user's visual goal better than raw ChimeraX commands.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": list(FIGURE_MODE_OPTIONS)},
                    "reason": {"type": "string"},
                },
                "required": ["mode", "reason"],
                "additionalProperties": False,
            },
            "strict": True,
        },
        {
            "type": "function",
            "name": "analyze_structure",
            "description": "Return deterministic local structural analysis from the current ChimeraX session. Use before making biological claims or selecting functional regions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "report": {"type": "string", "enum": list(ANALYSIS_REPORTS)},
                    "target": {"type": "string"},
                },
                "required": ["report", "target"],
                "additionalProperties": False,
            },
            "strict": True,
        },
        {
            "type": "function",
            "name": "predict_and_place_metal",
            "description": "Run the bundled metal-coordination pipeline against the current ChimeraX AtomicStructure models. Use place=false for prediction/review; use place=true only when the user explicitly asks to insert/add/place/optimize a metal marker.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "top_n": {"type": "integer"},
                    "place": {"type": "boolean"},
                    "show_all": {"type": "boolean"},
                    "clear_existing": {"type": "boolean"},
                    "use_kvfinder": {"type": "boolean"},
                },
                "required": ["target", "top_n", "place", "show_all", "clear_existing", "use_kvfinder"],
                "additionalProperties": False,
            },
            "strict": True,
        },
        {
            "type": "function",
            "name": "get_chimerax_docs",
            "description": "Retrieve compact official ChimeraX command-documentation snippets and likely command aliases for a natural-language query.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    ]


def dispatch_openai_agent_tool(session, name, arguments, *, executor, scene_context_callback=None, progress=None):
    try:
        args = json.loads(arguments or "{}") if isinstance(arguments, str) else dict(arguments or {})
    except Exception as err:
        return _json_result("error", error=f"Invalid JSON arguments: {err}")

    try:
        if name == "get_scene_state":
            include_models = bool(args.get("include_models", True))
            include_selection = bool(args.get("include_selection", True))
            if scene_context_callback is not None:
                text = scene_context_callback(
                    include_models=include_models,
                    include_selection=include_selection,
                )
            else:
                text = format_models_report(session)
            return _json_result("ok", scene_state=text)

        if name == "run_chimerax_command":
            command = _single_command(args.get("command", ""))
            _emit(progress, f"[tool] run_chimerax_command: {command}")
            result = executor(command)
            return _json_result("ok", command=command, result=_string_result(result))

        if name == "run_chimerax_command_batch":
            raw_commands = args.get("commands", [])
            if not isinstance(raw_commands, list):
                return _json_result("error", error="commands must be a list")
            executed = []
            for raw_command in raw_commands[:6]:
                command = _single_command(raw_command)
                _emit(progress, f"[tool] run_chimerax_command_batch: {command}")
                result = executor(command)
                executed.append({"command": command, "result": _string_result(result)})
            return _json_result("ok", executed=executed)

        if name == "apply_figure_mode":
            mode = str(args.get("mode", "")).strip()
            if mode not in FIGURE_MODE_OPTIONS:
                return _json_result("error", error=f"Unsupported figure mode: {mode}")
            _emit(progress, f"[tool] apply_figure_mode: {mode}")
            result = run_figure_mode(session, mode, executor=executor)
            return _json_result("ok", mode=mode, result=_string_result(result))

        if name == "analyze_structure":
            report = str(args.get("report", "")).strip() or "brief"
            target = str(args.get("target", "")).strip() or None
            if report not in ANALYSIS_REPORTS:
                return _json_result("error", error=f"Unsupported analysis report: {report}")
            return _json_result("ok", report=report, target=target or "", result=_analysis_report(session, report, target))

        if name == "predict_and_place_metal":
            target = str(args.get("target", "")).strip() or None
            try:
                top_n = max(1, min(20, int(args.get("top_n", 1))))
            except Exception:
                top_n = 1
            place = bool(args.get("place", False))
            show_all = bool(args.get("show_all", False))
            clear_existing = bool(args.get("clear_existing", True))
            use_kvfinder = bool(args.get("use_kvfinder", True))
            preview = bool(args.get("preview", not place))
            _emit(progress, "[tool] predict_and_place_metal")
            result = run_metal_placement_pipeline(
                session,
                model_hint=target,
                top_n=top_n,
                place=place,
                preview=preview,
                show_all=show_all,
                clear_existing=clear_existing,
                use_kvfinder=use_kvfinder,
                executor=executor,
            )
            return _json_result("ok", result=result)

        if name == "get_chimerax_docs":
            query = str(args.get("query", "")).strip()
            snippets = format_docs_snippets(query, limit=8)
            aliases = likely_command_aliases(query, limit=10)
            return _json_result("ok", query=query, likely_commands=aliases, snippets=snippets)

        return _json_result("error", error=f"Unknown tool: {name}")
    except Exception as err:
        return _json_result("error", error=str(err) if str(err) else err.__class__.__name__)


def _analysis_report(session, report, target):
    if report == "brief":
        return format_research_brief(session, model_hint=target)
    if report == "models":
        return format_models_report(session)
    if report == "selection":
        return format_selection_overlap_report(session, target)
    if report == "selection_focus":
        return format_selection_focus_report(session, target)
    if report == "annotation":
        return format_annotation_report(session, target)
    if report == "ligand":
        return format_ligand_report(session, target)
    if report == "metal":
        return _combined_metal_report(session, target)
    if report == "metal_candidates":
        return format_predicted_metal_report(session, model_hint=target)
    if report == "metal_evidence":
        return format_metal_evidence_report(session, model_hint=target)
    if report == "catalytic":
        return format_catalytic_report(session, target)
    if report == "catalytic_workflow":
        return format_catalytic_workflow_report(session, target)
    if report == "membrane":
        return format_membrane_report(session, target)
    if report == "pisa":
        return format_pisa_report(session, target)
    if report == "domains":
        return format_domains_report(session, target)
    if report == "complex":
        return format_complex_report(session, target)
    if report == "roles":
        return format_roles_report(session, target)
    if report == "sequence":
        return format_sequence_report(session, target)
    if report == "chains":
        return format_chains_report(session, target)
    if report == "motif":
        return format_motif_report(session, target)
    return ""


def _combined_metal_report(session, target):
    existing = format_existing_metal_report(session, target)
    predicted = format_predicted_metal_report(session, model_hint=target)
    return "\n\n".join(
        [
            "Existing metal-ion coordination:\n" + existing,
            "Predicted virtual metal-site candidates:\n" + predicted,
        ]
    )


def _single_command(command):
    command = str(command or "").strip()
    if not command:
        raise ValueError("command is required")
    if "\n" in command or "\r" in command:
        raise ValueError("Only one ChimeraX command line can be run per command tool call")
    if command.startswith(("!", "sh ")):
        raise ValueError("Shell commands are not allowed through ChimeraX agent tools")
    return command


def _string_result(value):
    text = str(value).strip() if value is not None else "ok"
    return text or "ok"


def _json_result(status, **payload):
    return json.dumps({"status": status, **payload}, ensure_ascii=True)


def _emit(progress, message):
    if progress is not None:
        progress(message, kind="info")
