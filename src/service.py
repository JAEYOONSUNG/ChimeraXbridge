import json
import os
import base64
import re
import subprocess
import tempfile
import threading
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .backends import (
    backend_supports_tools,
    clear_effort_override,
    ensure_session_preferences,
    get_backend_defaults,
    get_effort_override,
    get_backend_label,
    get_backend_spec,
    get_current_backend_id,
    get_model_override,
    get_routing_mode,
    resolve_backend_api_key,
    resolve_backend_cli,
    sanitize_model_override,
)
from .agent_tools import dispatch_openai_agent_tool, openai_agent_tool_definitions
from .builtin_actions import recommended_figure_mode, run_figure_mode, run_partial_local_flow, try_analysis_fastpath, try_builtin_fastpath, try_visual_fastpath
from .docs_index import format_docs_snippets, likely_command_aliases
from .docs_hints import docs_hints_for_prompt
from .nl_memory import record_nl_event, register_feedback
from .nl_intent import canonical_intent_hints
from .tool_state import format_analysis_tool_state
from .semantic import (
    format_analyze_report,
    format_annotation_report,
    format_catalytic_report,
    format_catalytic_workflow_report,
    format_complex_report,
    format_dali_report,
    format_domains_report,
    format_external_reference_report,
    format_ligand_report,
    format_metal_report,
    format_motif_report,
    format_research_brief,
    format_uniprot_residue_report,
    format_roles_report,
    format_sequence_report,
    best_catalytic_candidates,
    get_session_semantics,
    summarize_semantics,
)
from .membrane import format_membrane_report
from .pisa import format_pisa_report


class CodexBridgeError(RuntimeError):
    pass


ANALYSIS_CONFIDENCE_LEVELS = ("high", "medium", "low")
ACTION_TYPES = ("chimeraX_command_batch", "figure_mode")
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


def _analysis_response_schema():
    evidence_item = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "claim": {"type": "string"},
            "confidence": {"type": "string", "enum": list(ANALYSIS_CONFIDENCE_LEVELS)},
            "evidence": {"type": "array", "items": {"type": "string"}},
            "focus_specs": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["claim", "confidence", "evidence", "focus_specs"],
    }
    next_check_item = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "goal": {"type": "string"},
            "why": {"type": "string"},
            "commands": {"type": "array", "items": {"type": "string"}},
            "focus_specs": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["goal", "why", "commands", "focus_specs"],
    }
    visual_item = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "mode": {"type": "string", "enum": list(FIGURE_MODE_OPTIONS)},
            "reason": {"type": "string"},
        },
        "required": ["mode", "reason"],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "summary": {"type": "string"},
            "observations": {"type": "array", "items": evidence_item},
            "hypotheses": {"type": "array", "items": evidence_item},
            "next_checks": {"type": "array", "items": next_check_item},
            "visual_suggestions": {"type": "array", "items": visual_item},
            "command_suggestions": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "summary",
            "observations",
            "hypotheses",
            "next_checks",
            "visual_suggestions",
            "command_suggestions",
        ],
    }


def _action_plan_schema(*, include_done):
    action_item = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "type": {"type": "string", "enum": list(ACTION_TYPES)},
            "label": {"type": "string"},
            "reason": {"type": "string"},
            "verify": {"type": "string"},
            "commands": {"type": "array", "items": {"type": "string"}},
            "figure_mode": {"type": "string", "enum": ["", *FIGURE_MODE_OPTIONS]},
        },
        "required": ["type", "label", "reason", "verify", "commands", "figure_mode"],
    }
    properties = {
        "message": {"type": "string"},
        "actions": {"type": "array", "items": action_item},
    }
    required = ["message", "actions"]
    if include_done:
        properties["done"] = {"type": "boolean"}
        required.append("done")
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": required,
    }


def ask_codex(
    session,
    user_prompt,
    *,
    include_models=True,
    include_selection=True,
    progress=None,
    fast=True,
):
    ensure_session_preferences(session)
    _emit_progress(progress, "[phase] collect ChimeraX context")
    prepared_context = _prepare_prompt_context(
        session,
        include_models=include_models,
        include_selection=include_selection,
    )
    prompt = build_codex_prompt(
        session,
        user_prompt,
        include_models=include_models,
        include_selection=include_selection,
        prepared_context=prepared_context,
    )
    image_paths, cleanup_paths = _collect_backend_images(
        session,
        prompt=user_prompt,
        mode="chat",
        progress=progress,
    )
    _emit_progress(progress, f"[phase] send request to {get_backend_label(get_current_backend_id(session))}")
    reply = _run_backend_exec(
        session,
        prompt,
        progress=progress,
        fast=fast,
        image_paths=image_paths,
        cleanup_paths=cleanup_paths,
    )
    _emit_progress(progress, f"[phase] {get_backend_label(get_current_backend_id(session))} reply received")
    _mark_prompt_context_used(session, prepared_context)
    return reply


def ask_analysis(session, user_prompt, *, progress=None, fast=True):
    ensure_session_preferences(session)
    _emit_progress(progress, "[phase] collect structural analysis context")
    prepared_context = _prepare_prompt_context(session)
    brief = format_research_brief(session)
    general = format_analyze_report(session)
    annotation = format_annotation_report(session)
    metal = format_metal_report(session)
    motifs = format_motif_report(session)
    dali = format_dali_report(session)
    sequence = format_sequence_report(session)
    domains = format_domains_report(session)
    complex_report = format_complex_report(session)
    roles_report = format_roles_report(session)
    ligand_report = format_ligand_report(session)
    catalytic_report = format_catalytic_report(session)
    catalytic_workflow = format_catalytic_workflow_report(session)
    membrane_report = format_membrane_report(session)
    pisa_report = format_pisa_report(session) if _mentions_pisa_interface(user_prompt) else "- PISA-like interface analysis not requested this turn."
    external_reference = format_external_reference_report(session)
    focused_uniprot = _focused_uniprot_context(session, user_prompt)
    focus_hint = _analysis_focus_hint(user_prompt)
    conversational_hint = _analysis_conversation_hint(session, user_prompt)
    canonical_hints = canonical_intent_hints(user_prompt)
    schema = _analysis_response_schema()
    prompt = "\n\n".join(
        [
            "You are a structural biology copilot working inside UCSF ChimeraX.",
            "Return JSON that matches the provided schema.",
            "Analyze the structure and produce high-signal scientific insight.",
            "Use only the evidence in the provided ChimeraX analysis context and standard biochemical reasoning.",
            "Do not invent annotations or residue roles not supported by the structure context.",
            "Do not tell the user to paste commands manually into ChimeraX.",
            "Treat this as an ongoing conversation, not a fresh report every time.",
            "Every observation and hypothesis must include a confidence level and concrete evidence strings grounded in the provided context.",
            "Use focus_specs to name the most relevant model, chain, or residue specs when possible.",
            "Use visual_suggestions only for allowed figure modes that would help inspect the current structure.",
            "If exact commands would help, put them in command_suggestions as plain ChimeraX command strings.",
            "For catalytic-residue questions, prioritize candidates supported by multiple evidence streams: motif, ligand/metal neighborhood, 3D catalytic-like clustering, and recent conservation.",
            "For membrane-protein questions, use membrane context and prefer a virtual in-app slab for visualization; use OPM/PPM or CHARMM-GUI/MemGen for authoritative orientation or explicit lipids.",
            "For interface-area or PISA questions, use the PISA-like context and prefer `/pisa view` so ChimeraX measures buried surface area in the Log.",
            "Keep claims compact and evidence high-signal.",
            conversational_hint,
            focus_hint,
            "Canonical intent hints:\n" + ("\n".join(f"- {hint}" for hint in canonical_hints) if canonical_hints else "- (none)"),
            f"Current ChimeraX session context:\n{prepared_context['context_text']}",
            f"Recent analysis-tool state:\n{format_analysis_tool_state(session)}",
            f"Recent ChimeraX state changes since last AI turn:\n{prepared_context['state_delta_text']}",
            f"Recent ChimeraX command and terminal history:\n{_recent_command_history_text(session, limit=12)}",
            f"Recent assistant memory:\n{_turn_memory_text(session, limit=4)}",
            f"External retrieval context:\n{external_reference}",
            f"Focused UniProt residue lookup:\n{focused_uniprot}",
            f"Current local research brief:\n{brief}",
            f"Previous analysis memory:\n{_analysis_memory_text(session)}",
            "Relevant official ChimeraX docs hints:\n" + "\n".join(f"- {hint}" for hint in docs_hints_for_prompt(user_prompt)),
            f"Relevant official ChimeraX docs excerpts:\n{format_docs_snippets(user_prompt, limit=10)}",
            "Likely documented command names: " + ", ".join(likely_command_aliases(user_prompt, limit=10)),
            f"Detailed structure analysis:\n{general}",
            f"Annotation summary:\n{annotation}",
            f"Motif summary:\n{motifs}",
            f"DALI context:\n{dali}",
            f"Sequence analysis:\n{sequence}",
            f"Domain-like chunk analysis:\n{domains}",
            f"Complex/interface analysis:\n{complex_report}",
            f"PISA-like interface analysis:\n{pisa_report}",
            f"Chain-role analysis:\n{roles_report}",
            f"Ligand-pocket analysis:\n{ligand_report}",
            f"Catalytic-residue scoring:\n{catalytic_report}",
            f"Catalytic workflow triage:\n{catalytic_workflow}",
            f"Membrane analysis:\n{membrane_report}",
            f"Metal-site analysis:\n{metal}",
            f"User request:\n{user_prompt.strip()}",
        ]
    )
    image_paths, cleanup_paths = _collect_backend_images(
        session,
        prompt=user_prompt,
        mode="analyze",
        progress=progress,
    )
    _emit_progress(progress, f"[phase] send analysis request to {get_backend_label(get_current_backend_id(session))}")
    raw_reply = _run_backend_exec(
        session,
        prompt,
        output_schema=schema,
        progress=progress,
        fast=fast,
        image_paths=image_paths,
        cleanup_paths=cleanup_paths,
    )
    _emit_progress(progress, f"[phase] {get_backend_label(get_current_backend_id(session))} analysis reply received")
    _mark_prompt_context_used(session, prepared_context)
    parsed_reply = _parse_analysis_response(raw_reply)
    reply = _format_analysis_response(parsed_reply)
    _append_analysis_memory(session, user_prompt, brief, reply)
    return reply


def _parse_analysis_response(raw_reply):
    try:
        payload = json.loads(raw_reply)
    except json.JSONDecodeError as err:
        raise CodexBridgeError(f"Structured analysis reply was not valid JSON: {err}") from err

    summary = str(payload.get("summary", "") or "").strip()
    observations = _normalize_analysis_items(payload.get("observations", []), key="claim")
    hypotheses = _normalize_analysis_items(payload.get("hypotheses", []), key="claim")
    next_checks = _normalize_next_checks(payload.get("next_checks", []))
    visual_suggestions = _normalize_visual_suggestions(payload.get("visual_suggestions", []))
    command_suggestions = [
        str(command).strip()
        for command in payload.get("command_suggestions", [])
        if isinstance(command, str) and str(command).strip()
    ]

    return {
        "summary": summary,
        "observations": observations[:8],
        "hypotheses": hypotheses[:6],
        "next_checks": next_checks[:6],
        "visual_suggestions": visual_suggestions[:4],
        "command_suggestions": command_suggestions[:8],
    }


def _normalize_analysis_items(items, *, key):
    normalized = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        claim = str(item.get(key, "") or "").strip()
        if not claim:
            continue
        confidence = str(item.get("confidence", "medium") or "medium").strip().lower()
        if confidence not in ANALYSIS_CONFIDENCE_LEVELS:
            confidence = "medium"
        evidence = [
            str(line).strip()
            for line in item.get("evidence", [])
            if isinstance(line, str) and str(line).strip()
        ]
        focus_specs = [
            str(spec).strip()
            for spec in item.get("focus_specs", [])
            if isinstance(spec, str) and str(spec).strip()
        ]
        normalized.append(
            {
                "claim": claim,
                "confidence": confidence,
                "evidence": evidence[:4],
                "focus_specs": focus_specs[:5],
            }
        )
    return normalized


def _normalize_next_checks(items):
    normalized = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        goal = str(item.get("goal", "") or "").strip()
        why = str(item.get("why", "") or "").strip()
        if not goal:
            continue
        commands = [
            str(command).strip()
            for command in item.get("commands", [])
            if isinstance(command, str) and str(command).strip()
        ]
        focus_specs = [
            str(spec).strip()
            for spec in item.get("focus_specs", [])
            if isinstance(spec, str) and str(spec).strip()
        ]
        normalized.append(
            {
                "goal": goal,
                "why": why,
                "commands": commands[:4],
                "focus_specs": focus_specs[:5],
            }
        )
    return normalized


def _normalize_visual_suggestions(items):
    normalized = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        mode = str(item.get("mode", "") or "").strip()
        reason = str(item.get("reason", "") or "").strip()
        if mode not in FIGURE_MODE_OPTIONS or not reason:
            continue
        normalized.append({"mode": mode, "reason": reason})
    return normalized


def _format_analysis_response(payload):
    lines = []
    summary = payload.get("summary")
    if summary:
        lines.append("Summary")
        lines.append(f"- {summary}")
        lines.append("")

    lines.append("Observations")
    if payload["observations"]:
        for item in payload["observations"]:
            lines.extend(_format_evidence_item_lines(item))
    else:
        lines.append("- No supported observation returned.")
    lines.append("")

    lines.append("Hypotheses")
    if payload["hypotheses"]:
        for item in payload["hypotheses"]:
            lines.extend(_format_evidence_item_lines(item))
    else:
        lines.append("- No supported hypothesis returned.")
    lines.append("")

    lines.append("Next checks")
    if payload["next_checks"]:
        for item in payload["next_checks"]:
            lines.append(f"- {item['goal']}")
            if item["why"]:
                lines.append(f"  why: {item['why']}")
            if item["commands"]:
                lines.append("  commands: " + " | ".join(item["commands"]))
            if item["focus_specs"]:
                lines.append("  focus: " + ", ".join(item["focus_specs"]))
    else:
        lines.append("- No next checks returned.")

    if payload["visual_suggestions"]:
        lines.append("")
        lines.append("Suggested visuals")
        for item in payload["visual_suggestions"]:
            lines.append(f"- figure {item['mode']}: {item['reason']}")

    if payload["command_suggestions"]:
        lines.append("")
        lines.append("Suggested ChimeraX commands")
        lines.extend(f"- {command}" for command in payload["command_suggestions"])

    return "\n".join(lines)


def _format_evidence_item_lines(item):
    lines = [f"- [{item['confidence']}] {item['claim']}"]
    if item["evidence"]:
        lines.append("  evidence: " + " | ".join(item["evidence"]))
    if item["focus_specs"]:
        lines.append("  focus: " + ", ".join(item["focus_specs"]))
    return lines


def _parse_action_plan(raw_reply, *, include_done):
    try:
        payload = json.loads(raw_reply)
    except json.JSONDecodeError as err:
        raise CodexBridgeError(f"Structured action plan was not valid JSON: {err}") from err

    message = str(payload.get("message", "") or "").strip()
    actions = _normalize_typed_actions(payload.get("actions", []))
    parsed = {
        "message": message,
        "actions": actions,
    }
    if include_done:
        done = payload.get("done")
        if not isinstance(done, bool):
            raise CodexBridgeError("Structured action plan did not include a valid boolean done field.")
        parsed["done"] = done
    return parsed


def _normalize_typed_actions(actions):
    normalized = []
    for item in actions or []:
        if not isinstance(item, dict):
            continue
        action_type = str(item.get("type", "") or "").strip()
        label = str(item.get("label", "") or "").strip()
        reason = str(item.get("reason", "") or "").strip()
        verify = str(item.get("verify", "") or "").strip()
        commands = [
            str(command).strip()
            for command in item.get("commands", [])
            if isinstance(command, str) and str(command).strip()
        ]
        figure_mode = str(item.get("figure_mode", "") or "").strip()

        if action_type == "chimeraX_command_batch":
            if not commands:
                continue
        elif action_type == "figure_mode":
            if figure_mode not in FIGURE_MODE_OPTIONS:
                continue
        else:
            continue

        normalized.append(
            {
                "type": action_type,
                "label": label or action_type.replace("_", " "),
                "reason": reason or "No reason provided.",
                "verify": verify or "Check for the expected structural state change.",
                "commands": commands[:6],
                "figure_mode": figure_mode,
            }
        )
    return normalized


def _execute_typed_action(session, action, *, executor, progress=None):
    pre_state = _capture_state_snapshot(session)
    transcript_lines = [f"Action: {action['label']}", f"- type: {action['type']}", f"- reason: {action['reason']}"]
    error = None

    if action["type"] == "chimeraX_command_batch":
        executed_commands = []
        for command in action["commands"]:
            try:
                _emit_progress(progress, f"[run] {command}")
                result = executor(command)
                executed_commands.append(command)
                status = str(result).strip() if result is not None else ""
                if status and status != "ok":
                    transcript_lines.append(f"- {command} -> {status}")
                else:
                    transcript_lines.append(f"- {command}")
            except Exception as err:
                error = str(err) if str(err) else err.__class__.__name__
                transcript_lines.append(f"- command failed: {command}")
                transcript_lines.append(f"- error: {error}")
                _emit_progress(progress, f"[error] {command} -> {error}", kind="error")
                break
    elif action["type"] == "figure_mode":
        try:
            _emit_progress(progress, f"[run] figure {action['figure_mode']}")
            figure_result = run_figure_mode(session, action["figure_mode"], executor=executor)
            transcript_lines.append(f"- figure mode: {action['figure_mode']}")
            transcript_lines.extend(f"- {line}" for line in figure_result.splitlines()[:8] if line.strip())
        except Exception as err:
            error = str(err) if str(err) else err.__class__.__name__
            transcript_lines.append(f"- figure mode failed: {action['figure_mode']}")
            transcript_lines.append(f"- error: {error}")
            _emit_progress(progress, f"[error] figure {action['figure_mode']} -> {error}", kind="error")

    post_state = _capture_state_snapshot(session)
    delta_lines = _state_delta_lines(pre_state, post_state)
    meaningful_changes = [
        line for line in delta_lines
        if line != "No major ChimeraX state changes since the last AI turn."
    ]
    transcript_lines.append(f"- verify target: {action['verify']}")
    if meaningful_changes:
        transcript_lines.append("- observed changes: " + " | ".join(meaningful_changes[:4]))
    else:
        transcript_lines.append("- observed changes: no major state change")
    transcript_lines.extend(_post_action_verification_lines(session, action))

    return {
        "transcript_lines": transcript_lines,
        "delta_lines": delta_lines,
        "error": error,
    }


def _post_action_verification_lines(session, action):
    probe = " ".join(
        str(action.get(key, ""))
        for key in ("label", "reason", "verify", "figure_mode")
    ).lower()
    probe += " " + " ".join(str(command).lower() for command in action.get("commands", []) or [])
    if not any(
        token in probe
        for token in (
            "catalytic",
            "active",
            "pocket",
            "ligand",
            "metal",
            "motif",
            "촉매",
            "활성",
            "포켓",
            "리간드",
        )
    ):
        return []
    try:
        candidates = best_catalytic_candidates(session, limit=5)
    except Exception as err:
        return [f"- post-action catalytic check failed: {err}"]
    if not candidates:
        return ["- post-action catalytic check: no ranked candidates"]
    summary = ", ".join(
        f"{item['residue_spec']} score {item['score']}"
        for item in candidates[:5]
    )
    return [f"- post-action catalytic check: {summary}"]


def plan_codex_actions(session, user_prompt, *, include_models=True, include_selection=True):
    ensure_session_preferences(session)
    schema = _action_plan_schema(include_done=False)
    prepared_context = _prepare_prompt_context(
        session,
        include_models=include_models,
        include_selection=include_selection,
    )
    external_reference = format_external_reference_report(session)
    canonical_hints = canonical_intent_hints(user_prompt)
    prompt = "\n\n".join(
        [
            "You are controlling UCSF ChimeraX for the user.",
            "Return JSON that matches the provided schema.",
            "Use typed actions instead of a raw command list.",
            "Action type chimeraX_command_batch: use for exact ChimeraX commands. Keep batches short and safe.",
            "Action type figure_mode: use when a built-in figure preset is the right tool.",
            "Each commands item must be a single ChimeraX command line string with no markdown fences.",
            "Allowed figure modes: " + ", ".join(FIGURE_MODE_OPTIONS),
            f"Recommended figure mode right now: {recommended_figure_mode(session) or 'none'}",
            "If the request should not trigger execution, return an empty actions array and explain briefly in message.",
            "Do not invent files, shell commands, or model IDs that are not in the context.",
            f"Recent ChimeraX state changes since last AI turn:\n{prepared_context['state_delta_text']}",
            f"Recent ChimeraX command and terminal history:\n{_recent_command_history_text(session, limit=10)}",
            f"Recent assistant memory:\n{_turn_memory_text(session, limit=4)}",
            f"External retrieval context:\n{external_reference}",
            f"Recent analysis-tool state:\n{format_analysis_tool_state(session)}",
            "Canonical intent hints:\n" + ("\n".join(f"- {hint}" for hint in canonical_hints) if canonical_hints else "- (none)"),
            f"Relevant official ChimeraX docs excerpts:\n{format_docs_snippets(user_prompt, limit=10)}",
            "Likely documented command names: " + ", ".join(likely_command_aliases(user_prompt, limit=10)),
            f"ChimeraX session context:\n{prepared_context['context_text']}",
            f"User request:\n{user_prompt.strip()}",
        ]
    )
    image_paths, cleanup_paths = _collect_backend_images(
        session,
        prompt=user_prompt,
        mode="combined",
    )
    raw = _run_backend_exec(
        session,
        prompt,
        output_schema=schema,
        image_paths=image_paths,
        cleanup_paths=cleanup_paths,
    )
    _mark_prompt_context_used(session, prepared_context)
    return _parse_action_plan(raw, include_done=False)


def apply_codex_plan(session, plan):
    from chimerax.core.commands import run

    message = plan.get("message", "").strip()
    actions = list(plan.get("actions", []))

    summary_lines = []
    if message:
        summary_lines.append(message)

    if not actions:
        if not summary_lines:
            summary_lines.append("Codex did not return executable ChimeraX actions.")
        return "\n".join(summary_lines)

    for action in actions:
        result = _execute_typed_action(
            session,
            action,
            executor=lambda command: run(session, command),
        )
        summary_lines.extend(result["transcript_lines"])
        if result.get("error"):
            break

    return "\n".join(summary_lines)


def run_codex_actions(session, user_prompt, *, include_models=True, include_selection=True):
    plan = plan_codex_actions(
        session,
        user_prompt,
        include_models=include_models,
        include_selection=include_selection,
    )
    return apply_codex_plan(session, plan)


def plan_codex_agent_step(
    session,
    user_prompt,
    history,
    *,
    include_models=True,
    include_selection=True,
    step_number=1,
    max_steps=5,
    progress=None,
    fast=True,
):
    ensure_session_preferences(session)
    schema = _action_plan_schema(include_done=True)
    prepared_context = _prepare_prompt_context(
        session,
        include_models=include_models,
        include_selection=include_selection,
    )
    history_text = json.dumps(history, ensure_ascii=True, indent=2)
    external_reference = format_external_reference_report(session)
    canonical_hints = canonical_intent_hints(user_prompt)
    _emit_progress(progress, f"[phase] step {step_number}: refresh ChimeraX context")
    prompt = "\n\n".join(
        [
            "You are an agent operating inside a live UCSF ChimeraX session.",
            "You can act only through typed ChimeraX actions.",
            "Use official ChimeraX 1.10.1 user-guide command semantics. Do not invent command or preset names.",
            "Return JSON that matches the provided schema.",
            f"This is step {step_number} of at most {max_steps}.",
            "Use short, safe action batches. After each batch, the session state will be refreshed and shown to you again.",
            "For catalytic-residue or active-site goals, first gather local catalytic, motif, ligand/metal, and conservation evidence; do not claim a catalytic residue from appearance alone.",
            "If the user's goal appears complete, or you should stop and report back, set done to true.",
            "For conversational or explanatory requests that do not require changing ChimeraX, return an empty actions array and set done to true.",
            "Use action type chimeraX_command_batch for exact ChimeraX commands.",
            "Use action type figure_mode for built-in figure presets when they better match the task.",
            "Allowed figure modes: " + ", ".join(FIGURE_MODE_OPTIONS),
            f"Recommended figure mode right now: {recommended_figure_mode(session) or 'none'}",
            "Do not invent files, shell commands, or model IDs that are not in the context.",
            f"Recent ChimeraX state changes since last AI turn:\n{prepared_context['state_delta_text']}",
            f"Recent ChimeraX command and terminal history:\n{_recent_command_history_text(session, limit=10)}",
            f"Recent assistant memory:\n{_turn_memory_text(session, limit=5)}",
            f"External retrieval context:\n{external_reference}",
            f"Recent analysis-tool state:\n{format_analysis_tool_state(session)}",
            "Canonical intent hints:\n" + ("\n".join(f"- {hint}" for hint in canonical_hints) if canonical_hints else "- (none)"),
            "Relevant official ChimeraX docs hints:\n" + "\n".join(f"- {hint}" for hint in docs_hints_for_prompt(user_prompt)),
            f"Relevant official ChimeraX docs excerpts:\n{format_docs_snippets(user_prompt, limit=10)}",
            "Likely documented command names: " + ", ".join(likely_command_aliases(user_prompt, limit=10)),
            f"User goal:\n{user_prompt.strip()}",
            f"Current ChimeraX session context:\n{prepared_context['context_text']}",
            f"Previous step history:\n{history_text}",
        ]
    )
    _emit_progress(progress, f"[phase] step {step_number}: ask {get_backend_label(get_current_backend_id(session))} for next action")
    image_paths, cleanup_paths = _collect_backend_images(
        session,
        prompt=user_prompt,
        mode="combined",
        progress=progress,
    )
    raw = _run_backend_exec(
        session,
        prompt,
        output_schema=schema,
        progress=progress,
        fast=fast,
        image_paths=image_paths,
        cleanup_paths=cleanup_paths,
    )
    _mark_prompt_context_used(session, prepared_context)
    parsed = _parse_action_plan(raw, include_done=True)
    _emit_progress(
        progress,
        f"[phase] step {step_number}: {get_backend_label(get_current_backend_id(session))} returned {len(parsed['actions'])} action(s)"
        + (" and marked the task done." if parsed["done"] else "."),
    )
    return parsed


def run_codex_agent(
    session,
    user_prompt,
    *,
    include_models=True,
    include_selection=True,
    max_steps=5,
    executor=None,
    progress=None,
    fast=True,
):
    ensure_session_preferences(session)
    routing_mode = get_routing_mode(session)
    if executor is None:
        from chimerax.core.commands import run

        executor = lambda command: run(session, command)

    current_backend_id = get_current_backend_id(session)
    transcript_prefix = ""

    if backend_supports_tools(current_backend_id) and routing_mode != "backend-only":
        _emit_progress(progress, "[route] local fastpath check")
        local_result = try_builtin_fastpath(session, user_prompt, progress=progress, executor=executor)
        if local_result is not None:
            _emit_progress(progress, "[route] local only")
            return local_result

        partial_local, unresolved = run_partial_local_flow(
            session,
            user_prompt,
            mode="combined",
            progress=progress,
            executor=executor,
        )
        if partial_local and not unresolved:
            _emit_progress(progress, "[route] local only")
            return partial_local
        if partial_local and unresolved:
            user_prompt = unresolved
            transcript_prefix = partial_local + "\n\n"

    if backend_supports_tools(current_backend_id):
        remote_result = run_openai_tool_agent(
            session,
            user_prompt,
            include_models=include_models,
            include_selection=include_selection,
            max_steps=max_steps,
            executor=executor,
            progress=progress,
            fast=fast,
        )
        return transcript_prefix + remote_result if transcript_prefix else remote_result

    if routing_mode == "local-first":
        local_result = try_builtin_fastpath(session, user_prompt, progress=progress, executor=executor)
        if local_result is not None:
            _emit_progress(progress, "[route] local only")
            return local_result

        partial_local, unresolved = run_partial_local_flow(
            session,
            user_prompt,
            mode="combined",
            progress=progress,
            executor=executor,
        )
        if partial_local and not unresolved:
            _emit_progress(progress, "[route] local only")
            return partial_local
        if partial_local and unresolved:
            user_prompt = unresolved
            transcript_prefix = partial_local + "\n\n"
    else:
        _emit_progress(progress, f"[route] {routing_mode} -> prefer backend agent")

    history = []
    transcript = []
    backend_label = get_backend_label(get_current_backend_id(session))
    _emit_progress(progress, f"[route] hybrid/backend agent")
    _emit_progress(progress, f"[phase] start {backend_label} agent run (max steps {max_steps})")

    for step_number in range(1, max_steps + 1):
        plan = plan_codex_agent_step(
            session,
            user_prompt,
            history,
            include_models=include_models,
            include_selection=include_selection,
            step_number=step_number,
            max_steps=max_steps,
            progress=progress,
            fast=fast,
        )

        step_header = f"Step {step_number}"
        if plan["message"]:
            transcript.append(f"{step_header}: {plan['message']}")
            _emit_progress(progress, f"[phase] {step_header.lower()}: {plan['message']}")

        action_results = []
        step_state_changes = []
        if plan["actions"]:
            transcript.append(f"{step_header} actions:")
            _emit_progress(progress, f"[phase] {step_header.lower()}: execute planned actions")
            all_changes = []
            for action in plan["actions"]:
                result = _execute_typed_action(
                    session,
                    action,
                    executor=executor,
                    progress=progress,
                )
                action_results.append(
                    {
                        "type": action["type"],
                        "label": action["label"],
                        "delta_lines": result["delta_lines"],
                        "error": result["error"],
                    }
                )
                transcript.extend(result["transcript_lines"])
                for line in result["delta_lines"]:
                    if line not in all_changes:
                        all_changes.append(line)
                if result["error"]:
                    plan["done"] = True
                    break
            step_state_changes = all_changes
            meaningful_changes = [
                line for line in step_state_changes
                if line != "No major ChimeraX state changes since the last AI turn."
            ]
            if meaningful_changes:
                transcript.append(f"{step_header} state changes:")
                transcript.extend(f"- {line}" for line in meaningful_changes)
        else:
            transcript.append(f"{step_header}: no actions to run.")
            _emit_progress(progress, f"[phase] {step_header.lower()}: nothing to execute")

        history.append(
            {
                "step": step_number,
                "message": plan["message"],
                "actions": plan["actions"],
                "results": action_results,
                "state_changes": step_state_changes,
            }
        )

        if plan["done"]:
            _emit_progress(progress, f"[phase] {step_header.lower()}: agent run complete")
            break
    else:
        transcript.append(f"Stopped after {max_steps} steps.")
        _emit_progress(progress, f"[phase] stopped after {max_steps} steps")

    body = "\n".join(transcript)
    return transcript_prefix + body if transcript_prefix else body


def run_mode_request(
    session,
    user_prompt,
    *,
    mode,
    progress=None,
    fast=True,
    executor=None,
    record_memory=True,
):
    resolved_mode = mode
    register_feedback(session, user_prompt)

    if mode == "analyze":
        if _visual_first_request(user_prompt):
            _emit_progress(progress, "[route] visual intent detected -> switching analyze request to visualize")
            resolved_mode = "visualize"
            result = run_mode_request(
                session,
                user_prompt,
                mode="visualize",
                progress=progress,
                fast=fast,
                executor=executor,
                record_memory=False,
            )
        else:
            _emit_progress(progress, "[phase] build local structural brief")
            brief = format_research_brief(session)
            _emit_progress(progress, "[route] local analysis heuristics")
            visual = try_visual_fastpath(session, user_prompt, progress=progress, executor=executor)
            local = try_analysis_fastpath(session, user_prompt, progress=progress, executor=executor)
            try:
                _emit_progress(progress, "[route] backend scientific interpretation")
                analysis_reply = ask_analysis(session, user_prompt, progress=progress, fast=fast)
            except Exception as err:
                error_text = str(err) if str(err) else err.__class__.__name__
                blocks = []
                if visual:
                    blocks.extend([visual, ""])
                if local:
                    blocks.extend([local, ""])
                blocks.extend([brief, "", "Backend analysis failed:", error_text])
                result = "\n".join(blocks)
            else:
                auto_visual = None
                if visual is None and not _analysis_text_only_request(user_prompt):
                    mode_name = _analysis_visual_mode_for_prompt(session, user_prompt)
                    if mode_name:
                        _emit_progress(progress, f"[route] explicit visual companion -> figure {mode_name}")
                        auto_visual = run_figure_mode(session, mode_name, executor=executor)

                extracted_visual = None
                suggested_commands = None
                if _analysis_auto_apply_request(user_prompt) and executor is not None:
                    extracted_visual = _apply_embedded_chimerax_commands(
                        session,
                        analysis_reply,
                        executor=executor,
                        progress=progress,
                    )
                else:
                    suggested_commands = _summarize_embedded_chimerax_commands(analysis_reply)

                if _looks_like_structured_analysis_reply(analysis_reply):
                    blocks = []
                    if visual:
                        blocks.extend([visual, ""])
                    elif auto_visual:
                        blocks.extend([auto_visual, ""])
                    if extracted_visual:
                        blocks.extend([extracted_visual, ""])
                    if suggested_commands:
                        blocks.extend([suggested_commands, ""])
                    blocks.append(analysis_reply)
                    result = "\n".join(blocks)
                else:
                    blocks = []
                    if visual:
                        blocks.extend([visual, ""])
                    elif auto_visual:
                        blocks.extend([auto_visual, ""])
                    if extracted_visual:
                        blocks.extend([extracted_visual, ""])
                    if suggested_commands:
                        blocks.extend([suggested_commands, ""])
                    if local:
                        blocks.append(local)
                    else:
                        blocks.append(brief)
                    blocks.extend(["", analysis_reply])
                    result = "\n".join(blocks)

    elif mode == "visualize":
        routing_mode = get_routing_mode(session)
        if routing_mode == "local-first":
            _emit_progress(progress, "[route] local visualization")
            local = try_visual_fastpath(session, user_prompt, progress=progress, executor=executor)
            if local is not None:
                _emit_progress(progress, "[route] local only")
                result = local
            else:
                partial_local, unresolved = run_partial_local_flow(
                    session,
                    user_prompt,
                    mode="visual",
                    progress=progress,
                    executor=executor,
                )
                if partial_local and not unresolved:
                    _emit_progress(progress, "[route] local only")
                    result = partial_local
                elif partial_local and unresolved:
                    _emit_progress(progress, "[route] local partial -> backend for remaining clauses")
                    remote = run_codex_agent(
                        session,
                        unresolved,
                        progress=progress,
                        fast=fast,
                        max_steps=2 if fast else 4,
                        executor=executor,
                    )
                    result = partial_local + "\n\n" + remote
                else:
                    _emit_progress(progress, "[route] backend agent planning")
                    result = run_codex_agent(
                        session,
                        user_prompt,
                        progress=progress,
                        fast=fast,
                        max_steps=2 if fast else 4,
                        executor=executor,
                    )
        else:
            _emit_progress(progress, f"[route] {routing_mode} -> backend visualization")
            result = run_codex_agent(
                session,
                user_prompt,
                progress=progress,
                fast=fast,
                max_steps=2 if fast else 4,
                executor=executor,
            )
            if _looks_like_noop_visual_result(result):
                result = result + "\nNo visual change was applied."

    elif mode == "chat":
        _emit_progress(progress, "[route] backend chat")
        result = ask_codex(session, user_prompt, progress=progress, fast=fast)

    else:
        resolved_mode = "combined"
        result = run_codex_agent(
            session,
            user_prompt,
            progress=progress,
            fast=fast,
            max_steps=3 if fast else 5,
            executor=executor,
        )

    if record_memory:
        _append_turn_memory(session, resolved_mode, user_prompt, result)
    record_nl_event(session, prompt=user_prompt, mode=resolved_mode, result=result)
    return result


def build_codex_prompt(session, user_prompt, *, include_models=True, include_selection=True, prepared_context=None):
    prepared_context = prepared_context or _prepare_prompt_context(
        session,
        include_models=include_models,
        include_selection=include_selection,
    )
    external_reference = format_external_reference_report(session)
    focused_uniprot = _focused_uniprot_context(session, user_prompt)
    canonical_hints = canonical_intent_hints(user_prompt)
    sections = [
        "You are helping a user from inside UCSF ChimeraX.",
        "Prefer concrete ChimeraX commands, atom specs, and short troubleshooting guidance.",
        "If commands are appropriate, put them in a fenced chimerax code block after the explanation.",
        "Do not assume command blocks will be executed automatically.",
        "Use official ChimeraX 1.10.1 user-guide command semantics. Do not invent command or preset names.",
        "Keep the answer concise and do not assume session state beyond the context below.",
        f"Recent ChimeraX state changes since last AI turn:\n{prepared_context['state_delta_text']}",
        f"Recent ChimeraX command and terminal history:\n{_recent_command_history_text(session, limit=10)}",
        f"Recent assistant memory:\n{_turn_memory_text(session, limit=4)}",
        f"External retrieval context:\n{external_reference}",
        f"Focused UniProt residue lookup:\n{focused_uniprot}",
        f"Recent analysis-tool state:\n{format_analysis_tool_state(session)}",
        "Canonical intent hints:\n" + ("\n".join(f"- {hint}" for hint in canonical_hints) if canonical_hints else "- (none)"),
        "Relevant official ChimeraX docs hints:\n" + "\n".join(f"- {hint}" for hint in docs_hints_for_prompt(user_prompt)),
        f"Relevant official ChimeraX docs excerpts:\n{format_docs_snippets(user_prompt, limit=10)}",
        "Likely documented command names: " + ", ".join(likely_command_aliases(user_prompt, limit=10)),
        f"ChimeraX session context:\n{prepared_context['context_text']}",
        f"User request:\n{user_prompt.strip()}",
    ]
    return "\n\n".join(sections)


def build_session_context(session, *, include_models=True, include_selection=True):
    snapshot = _capture_state_snapshot(session)
    return _format_session_context(
        snapshot,
        include_models=include_models,
        include_selection=include_selection,
    )


def _prepare_prompt_context(session, *, include_models=True, include_selection=True):
    snapshot = _capture_state_snapshot(session)
    previous = getattr(session, "_codex_bridge_last_prompt_state", None)
    return {
        "snapshot": snapshot,
        "context_text": _format_session_context(
            snapshot,
            include_models=include_models,
            include_selection=include_selection,
        ),
        "state_delta_text": _format_state_delta(previous, snapshot),
    }


def _mark_prompt_context_used(session, prepared_context):
    snapshot = prepared_context.get("snapshot") if prepared_context else None
    if snapshot is not None:
        session._codex_bridge_last_prompt_state = snapshot


def _capture_state_snapshot(session):
    semantics = get_session_semantics(session)
    selection = semantics.get("selection", {})
    return {
        "semantics": semantics,
        "models": [
            {
                "spec": model.get("spec"),
                "name": model.get("name"),
                "type": model.get("type"),
                "visible": bool(model.get("visible")),
                "atomic": bool(model.get("atomic")),
            }
            for model in semantics.get("models", [])
        ],
        "selection": {
            "models": list(selection.get("models", [])),
            "ranges": list(selection.get("ranges", [])),
            "atoms": int(selection.get("atoms", 0)),
            "residues": int(selection.get("residues", 0)),
        },
        "view": _capture_view_state(session),
    }


def _format_session_context(snapshot, *, include_models=True, include_selection=True):
    semantics = snapshot["semantics"]
    lines = list(
        summarize_semantics(
            semantics,
            include_models=include_models,
            include_selection=include_selection,
        )
    )
    lines.extend(_view_context_lines(snapshot.get("view")))
    if not lines:
        lines.append("- No ChimeraX context attached.")
    return "\n".join(lines)


def _capture_view_state(session):
    ui = getattr(session, "ui", None)
    if ui is None or not getattr(ui, "is_gui", False):
        return {"available": False, "reason": "nogui"}

    view = getattr(session, "main_view", None)
    if view is None:
        return {"available": False, "reason": "no-main-view"}

    camera = getattr(view, "camera", None)
    position = getattr(camera, "position", None) if camera is not None else None
    clip_planes = []
    plane_container = getattr(view, "clip_planes", None)
    if plane_container is not None and hasattr(plane_container, "planes"):
        for plane in plane_container.planes()[:4]:
            clip_planes.append(
                {
                    "name": getattr(plane, "name", "plane"),
                    "point": _vector_tuple(getattr(plane, "plane_point", None)),
                    "normal": _vector_tuple(getattr(plane, "normal", None)),
                }
            )

    window_size = getattr(view, "window_size", None)
    if isinstance(window_size, (tuple, list)) and len(window_size) >= 2:
        window_size = (int(window_size[0]), int(window_size[1]))
    else:
        window_size = None

    return {
        "available": True,
        "camera_name": getattr(camera, "name", None),
        "camera_origin": _vector_tuple(position.origin()) if position is not None and hasattr(position, "origin") else None,
        "view_direction": _vector_tuple(camera.view_direction()) if camera is not None and hasattr(camera, "view_direction") else None,
        "field_of_view": getattr(camera, "field_of_view", None),
        "window_size": window_size,
        "background": _rgba_tuple(getattr(view, "background_color", None)),
        "center_of_rotation": _vector_tuple(getattr(view, "center_of_rotation", None)),
        "center_of_rotation_method": getattr(view, "center_of_rotation_method", None),
        "clip_planes": clip_planes,
    }


def _view_context_lines(view_state):
    if not view_state or not view_state.get("available"):
        return ["- Viewport: unavailable"]

    lines = []
    window_size = view_state.get("window_size")
    background = _rgba_to_hex(view_state.get("background"))
    if window_size is not None:
        lines.append(f"- Viewport: {window_size[0]}x{window_size[1]} background {background}")
    else:
        lines.append(f"- Viewport background: {background}")

    camera_bits = [view_state.get("camera_name") or "unknown camera"]
    field_of_view = view_state.get("field_of_view")
    if field_of_view is not None:
        try:
            camera_bits.append(f"fov {float(field_of_view):.1f}")
        except Exception:
            pass
    origin = view_state.get("camera_origin")
    direction = view_state.get("view_direction")
    if origin is not None:
        camera_bits.append(f"origin {_format_vector(origin)}")
    if direction is not None:
        camera_bits.append(f"dir {_format_vector(direction)}")
    lines.append("- Camera: " + ", ".join(camera_bits))

    center = view_state.get("center_of_rotation")
    if center is not None:
        method = view_state.get("center_of_rotation_method") or "unknown"
        lines.append(f"- Center of rotation: {_format_vector(center)} ({method})")

    planes = view_state.get("clip_planes") or []
    if planes:
        lines.append("- Clip planes: " + "; ".join(_format_clip_plane(plane) for plane in planes))
    else:
        lines.append("- Clip planes: none")
    return lines


def _format_clip_plane(plane):
    name = plane.get("name", "plane")
    point = plane.get("point")
    normal = plane.get("normal")
    pieces = [name]
    if point is not None:
        pieces.append("point " + _format_vector(point))
    if normal is not None:
        pieces.append("normal " + _format_vector(normal))
    return ", ".join(pieces)


def _format_state_delta(previous, current):
    return "\n".join(f"- {line}" for line in _state_delta_lines(previous, current))


def _state_delta_lines(previous, current):
    if previous is None:
        return ["First AI turn in this ChimeraX session."]

    lines = []

    prev_models = {item["spec"]: item for item in previous.get("models", []) if item.get("spec")}
    curr_models = {item["spec"]: item for item in current.get("models", []) if item.get("spec")}
    opened = sorted(set(curr_models) - set(prev_models))
    closed = sorted(set(prev_models) - set(curr_models))
    if opened:
        lines.append("Opened models: " + ", ".join(opened[:8]))
    if closed:
        lines.append("Closed models: " + ", ".join(closed[:8]))

    visibility_changes = []
    for spec in sorted(set(prev_models) & set(curr_models)):
        if bool(prev_models[spec].get("visible")) != bool(curr_models[spec].get("visible")):
            state = "visible" if curr_models[spec].get("visible") else "hidden"
            visibility_changes.append(f"{spec} -> {state}")
    if visibility_changes:
        lines.append("Visibility changes: " + ", ".join(visibility_changes[:8]))

    prev_selection = previous.get("selection", {})
    curr_selection = current.get("selection", {})
    if _selection_signature(prev_selection) != _selection_signature(curr_selection):
        lines.append(
            "Selection changed: "
            + _selection_summary(curr_selection)
        )

    prev_view = previous.get("view", {})
    curr_view = current.get("view", {})
    if prev_view.get("available") and curr_view.get("available"):
        if prev_view.get("camera_name") != curr_view.get("camera_name"):
            lines.append(
                "Camera type changed: "
                + f"{prev_view.get('camera_name') or 'unknown'} -> {curr_view.get('camera_name') or 'unknown'}"
            )
        if prev_view.get("window_size") != curr_view.get("window_size"):
            if curr_view.get("window_size") is not None:
                width, height = curr_view["window_size"]
                lines.append(f"Viewport size changed: {width}x{height}")
        if prev_view.get("background") != curr_view.get("background"):
            lines.append(
                "Background changed: "
                + f"{_rgba_to_hex(prev_view.get('background'))} -> {_rgba_to_hex(curr_view.get('background'))}"
            )
        origin_shift = _vector_distance(prev_view.get("camera_origin"), curr_view.get("camera_origin"))
        if origin_shift is not None and origin_shift >= 0.5:
            lines.append(f"Camera moved by about {origin_shift:.2f} scene units.")
        direction_shift = _vector_distance(prev_view.get("view_direction"), curr_view.get("view_direction"))
        if direction_shift is not None and direction_shift >= 0.05:
            lines.append("Camera direction changed.")
        center_shift = _vector_distance(prev_view.get("center_of_rotation"), curr_view.get("center_of_rotation"))
        if center_shift is not None and center_shift >= 0.5:
            lines.append(f"Center of rotation moved by about {center_shift:.2f} scene units.")
        prev_plane_names = [plane.get("name") for plane in prev_view.get("clip_planes", [])]
        curr_plane_names = [plane.get("name") for plane in curr_view.get("clip_planes", [])]
        if prev_plane_names != curr_plane_names:
            lines.append(
                "Clip planes changed: "
                + (", ".join(curr_plane_names) if curr_plane_names else "none")
            )
    elif prev_view.get("available") != curr_view.get("available"):
        lines.append("Viewport availability changed.")

    if not lines:
        lines.append("No major ChimeraX state changes since the last AI turn.")
    return lines


def _selection_signature(selection):
    return (
        tuple(selection.get("models", [])),
        tuple(selection.get("ranges", [])),
        int(selection.get("atoms", 0)),
        int(selection.get("residues", 0)),
    )


def _selection_summary(selection):
    focus = selection.get("ranges") or selection.get("models") or []
    if focus:
        preview = ", ".join(focus[:4])
        if len(focus) > 4:
            preview += f", ... (+{len(focus) - 4})"
    else:
        preview = "(cleared)"
    return f"{preview}; {selection.get('atoms', 0)} atoms, {selection.get('residues', 0)} residues"


def _vector_tuple(value):
    if value is None:
        return None
    try:
        sequence = list(value)
    except Exception:
        return None
    if len(sequence) < 3:
        return None
    try:
        return tuple(round(float(sequence[index]), 3) for index in range(3))
    except Exception:
        return None


def _rgba_tuple(value):
    if value is None:
        return None
    try:
        sequence = list(value)
    except Exception:
        return None
    if len(sequence) < 3:
        return None
    alpha = sequence[3] if len(sequence) > 3 else 1.0
    try:
        return tuple(round(float(channel), 3) for channel in (sequence[0], sequence[1], sequence[2], alpha))
    except Exception:
        return None


def _rgba_to_hex(rgba):
    if rgba is None:
        return "(unknown)"
    try:
        rgb = [max(0, min(255, int(round(float(channel) * 255)))) for channel in rgba[:3]]
    except Exception:
        return "(unknown)"
    return "#" + "".join(f"{channel:02x}" for channel in rgb)


def _format_vector(vector):
    if vector is None:
        return "(unknown)"
    return "(" + ", ".join(f"{float(component):.2f}" for component in vector[:3]) + ")"


def _vector_distance(left, right):
    if left is None or right is None:
        return None
    try:
        return float(sum((float(a) - float(b)) ** 2 for a, b in zip(left[:3], right[:3]))) ** 0.5
    except Exception:
        return None


def _backend_subprocess_env(cli_path):
    env = dict(os.environ)
    path_entries = env.get("PATH", "").split(os.pathsep) if env.get("PATH") else []

    extra_dirs = [
        str(Path(cli_path).resolve().parent),
        str(Path.home() / "bin"),
        str(Path.home() / ".local" / "bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
    ]

    for entry in extra_dirs:
        if entry and entry not in path_entries and Path(entry).exists():
            path_entries.append(entry)

    env["PATH"] = os.pathsep.join(path_entries)
    return env


def _collect_backend_images(session, *, prompt, mode, progress=None):
    if not _should_attach_viewport_snapshot(session, prompt, mode):
        return [], []
    try:
        image_path = _capture_viewport_snapshot(session)
    except Exception as err:
        _emit_progress(progress, f"Viewport snapshot skipped: {err}", kind="info")
        return [], []
    _emit_progress(progress, "[phase] attached current ChimeraX viewport snapshot")
    return [image_path], [image_path]


def _should_attach_viewport_snapshot(session, prompt, mode):
    if get_current_backend_id(session) != "codex":
        return False
    ui = getattr(session, "ui", None)
    if ui is None or not getattr(ui, "is_gui", False):
        return False

    normalized_mode = str(mode or "").strip().lower()
    if normalized_mode in {"analyze", "visualize", "combined", "agent"}:
        return True

    lowered = str(prompt or "").lower()
    structure_tokens = (
        "#",
        "structure",
        "model",
        "chain",
        "residue",
        "domain",
        "interface",
        "ligand",
        "metal",
        "motif",
        "pocket",
        "selection",
        "surface",
        "cartoon",
        "align",
        "view",
        "show",
        "구조",
        "모델",
        "체인",
        "잔기",
        "도메인",
        "인터페이스",
        "리간드",
        "금속",
        "선택",
        "표시",
        "보여",
        "시각",
    )
    return any(token in lowered for token in structure_tokens)


def _capture_viewport_snapshot(session):
    from chimerax.core.commands import StringArg

    width = 1280
    height = 960
    view = getattr(session, "main_view", None)
    window_size = getattr(view, "window_size", None)
    if isinstance(window_size, (tuple, list)) and len(window_size) >= 2:
        try:
            width = max(960, min(1600, int(window_size[0])))
            height = max(720, min(1200, int(window_size[1])))
        except Exception:
            width = 1280
            height = 960

    fd, path = tempfile.mkstemp(prefix="chimerax-viewport-", suffix=".png")
    os.close(fd)
    quoted = StringArg.unparse(path)
    _run_session_command(session, f"save {quoted} width {width} height {height} supersample 2")
    return path


def _run_session_command(session, command):
    from chimerax.core.commands import run

    ui = getattr(session, "ui", None)
    thread_safe = getattr(ui, "thread_safe", None)
    if thread_safe is None:
        return run(session, command)
    if _is_qt_main_thread():
        return run(session, command)

    event = threading.Event()
    result_box = {}

    def runner():
        try:
            result_box["result"] = run(session, command)
        except Exception as err:
            result_box["error"] = err
        finally:
            event.set()

    thread_safe(runner)
    event.wait()
    if "error" in result_box:
        raise result_box["error"]
    return result_box.get("result")


def _is_qt_main_thread():
    try:
        from Qt.QtCore import QCoreApplication, QThread

        app = QCoreApplication.instance()
        return app is not None and QThread.currentThread() == app.thread()
    except Exception:
        return False


def _resolve_backend_model_and_reasoning(session, backend_id, fast, progress=None):
    model_override = get_model_override(session, backend_id)
    default_model, default_reasoning = get_backend_defaults(backend_id, fast)
    sanitized_override, override_warning = sanitize_model_override(backend_id, model_override)
    if override_warning:
        _emit_progress(progress, override_warning, kind="error")
    model = sanitized_override or default_model
    reasoning = get_effort_override(session, backend_id) or default_reasoning
    return model, reasoning


def _run_backend_exec(
    session,
    prompt,
    output_schema=None,
    progress=None,
    fast=True,
    image_paths=None,
    cleanup_paths=None,
):
    backend_id = get_current_backend_id(session)
    spec = get_backend_spec(backend_id)
    model, reasoning = _resolve_backend_model_and_reasoning(session, backend_id, fast, progress=progress)

    if backend_id == "openai":
        return _run_openai_response_exec(
            session,
            prompt,
            model=model,
            reasoning=reasoning,
            output_schema=output_schema,
            progress=progress,
            image_paths=image_paths,
            cleanup_paths=cleanup_paths,
        )

    cli_path = resolve_backend_cli(backend_id)
    env = _backend_subprocess_env(cli_path)

    fd, output_path = tempfile.mkstemp(prefix="chimerax-codex-", suffix=".txt")
    os.close(fd)

    schema_path = None
    if output_schema is not None:
        schema_fd, schema_tmp = tempfile.mkstemp(prefix="chimerax-codex-schema-", suffix=".json")
        os.close(schema_fd)
        Path(schema_tmp).write_text(json.dumps(output_schema), encoding="utf-8")
        schema_path = schema_tmp

    if backend_id == "codex":
        command = _build_codex_command(
            cli_path,
            output_path,
            prompt,
            model,
            reasoning,
            schema_path,
            image_paths=image_paths,
        )
        expect_output_file = True
        effective_prompt = prompt
    elif backend_id == "claude":
        effective_prompt = prompt
        command = _build_claude_command(cli_path, effective_prompt, model, reasoning, output_schema)
        expect_output_file = False
    elif backend_id == "gemini":
        effective_prompt = _with_json_schema_prompt(prompt, output_schema) if output_schema is not None else prompt
        command = _build_gemini_command(cli_path, effective_prompt, model)
        expect_output_file = False
    else:
        raise CodexBridgeError(f"Unsupported backend: {backend_id}")

    _emit_progress(
        progress,
        f"Launching {spec['label']}..."
        if output_schema is None
        else f"Launching {spec['label']} for a structured action plan...",
    )
    _emit_progress(progress, f"Model: {model or 'default'} / reasoning: {reasoning or 'default'}")

    stdout_lines = []
    stderr_lines = []
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
            bufsize=1,
        )

        stdout_thread = threading.Thread(
            target=_read_process_stream,
            args=(process.stdout, stdout_lines, progress, "backend_stdout"),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=_read_process_stream,
            args=(process.stderr, stderr_lines, progress, "backend_stderr"),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()
        try:
            return_code = process.wait(timeout=300)
        except subprocess.TimeoutExpired as err:
            process.kill()
            raise CodexBridgeError(f"{spec['label']} request timed out after 300 seconds.") from err
        stdout_thread.join(timeout=2)
        stderr_thread.join(timeout=2)
        if return_code != 0:
            detail = ("\n".join(stderr_lines) or "\n".join(stdout_lines)).strip()
            raise CodexBridgeError(detail or f"{spec['label']} exited with status {return_code}.")

        if expect_output_file:
            reply = Path(output_path).read_text(encoding="utf-8").strip()
        else:
            reply = "\n".join(stdout_lines).strip()
        if not reply:
            raise CodexBridgeError(f"{spec['label']} returned no final message.")
        if output_schema is not None and not spec["supports_schema"]:
            reply = _extract_json_text(reply)
        elif output_schema is not None and backend_id == "claude":
            reply = _extract_json_text(reply)
        _emit_progress(progress, f"{spec['label']} process finished.")
        return reply
    except FileNotFoundError as err:
        raise CodexBridgeError(f"{spec['label']} CLI not found: {err}") from err
    finally:
        try:
            os.remove(output_path)
        except OSError:
            pass
        if schema_path is not None:
            try:
                os.remove(schema_path)
            except OSError:
                pass
        for path in cleanup_paths or []:
            try:
                os.remove(path)
            except OSError:
                pass


def run_openai_tool_agent(
    session,
    user_prompt,
    *,
    include_models=True,
    include_selection=True,
    max_steps=5,
    executor=None,
    progress=None,
    fast=True,
):
    ensure_session_preferences(session)
    if executor is None:
        from chimerax.core.commands import run

        executor = lambda command: run(session, command)

    model, reasoning = _resolve_backend_model_and_reasoning(session, "openai", fast, progress=progress)
    prepared_context = _prepare_prompt_context(
        session,
        include_models=include_models,
        include_selection=include_selection,
    )
    _mark_prompt_context_used(session, prepared_context)
    image_paths, cleanup_paths = _collect_backend_images(
        session,
        prompt=user_prompt,
        mode="agent",
        progress=progress,
    )
    prompt = "\n\n".join(
        [
            "User goal:\n" + user_prompt.strip(),
            "Initial live ChimeraX context:\n" + prepared_context["context_text"],
            "Recent ChimeraX state changes:\n" + prepared_context["state_delta_text"],
            "Recent command and terminal history:\n" + _recent_command_history_text(session, limit=10),
            "Recent assistant memory:\n" + _turn_memory_text(session, limit=4),
            "Relevant official ChimeraX docs excerpts:\n" + format_docs_snippets(user_prompt, limit=8),
            "Likely documented command names: " + ", ".join(likely_command_aliases(user_prompt, limit=10)),
        ]
    )
    input_items = _openai_input_items(prompt, image_paths=image_paths)
    transcript = []

    try:
        _emit_progress(progress, f"[route] OpenAI tool-calling agent ({model}, {reasoning or 'default'} reasoning)")
        for step_number in range(1, max_steps + 1):
            _emit_progress(progress, f"[phase] OpenAI tool round {step_number}/{max_steps}")
            response = _openai_create_response(
                api_key=resolve_backend_api_key("openai"),
                model=model,
                reasoning=reasoning,
                input_items=input_items,
                instructions=_openai_agent_instructions(),
                tools=openai_agent_tool_definitions(),
                parallel_tool_calls=False,
            )
            output_items = list(response.get("output") or [])
            text = _openai_response_text(response)
            tool_calls = _openai_function_calls(response)

            if text:
                transcript.append(text)
            if not tool_calls:
                _emit_progress(progress, "[phase] OpenAI agent returned final response")
                break

            input_items.extend(output_items)
            for call in tool_calls:
                name = call.get("name") or ""
                call_id = call.get("call_id") or call.get("id")
                arguments = call.get("arguments") or "{}"
                _emit_progress(progress, f"[tool] {name}")
                result = dispatch_openai_agent_tool(
                    session,
                    name,
                    arguments,
                    executor=executor,
                    scene_context_callback=lambda include_models=True, include_selection=True: _fresh_openai_scene_context(
                        session,
                        include_models=include_models,
                        include_selection=include_selection,
                    ),
                    progress=progress,
                )
                transcript.append(f"Tool {name}: {_tool_result_preview(result)}")
                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": result,
                    }
                )
        else:
            transcript.append(f"Stopped after {max_steps} OpenAI tool round(s).")
            _emit_progress(progress, f"[phase] stopped after {max_steps} OpenAI tool rounds")

        return "\n".join(line for line in transcript if str(line).strip())
    finally:
        for path in cleanup_paths or []:
            try:
                os.remove(path)
            except OSError:
                pass


def _fresh_openai_scene_context(session, *, include_models=True, include_selection=True):
    prepared_context = _prepare_prompt_context(
        session,
        include_models=include_models,
        include_selection=include_selection,
    )
    _mark_prompt_context_used(session, prepared_context)
    return "\n\n".join(
        [
            "Current ChimeraX session context:\n" + prepared_context["context_text"],
            "State changes since previous agent context:\n" + prepared_context["state_delta_text"],
            "Recent command and terminal history:\n" + _recent_command_history_text(session, limit=12),
            "Recent assistant memory:\n" + _turn_memory_text(session, limit=5),
            "Recent analysis-tool state:\n" + format_analysis_tool_state(session),
        ]
    )


def _openai_agent_instructions():
    return "\n".join(
        [
            "You are an agent operating inside a live UCSF ChimeraX session.",
            "Use the provided tools to inspect state, run exact ChimeraX commands, apply built-in figure presets, and gather deterministic structure reports.",
            "Use official ChimeraX command semantics. Do not invent commands, atom specs, model IDs, file paths, or figure modes.",
            "Prefer a tool call when the user asks you to change the scene, inspect current structure state, or verify whether an action worked.",
            "Call analyze_structure before making functional, active-site, ligand-pocket, interface, or domain claims that are not directly obvious from the current context.",
            "For catalytic-residue or active-site questions, use analyze_structure with report=catalytic_workflow, then verify visual or selection actions against the updated scene state.",
            "For membrane-protein questions, use analyze_structure with report=membrane; for a quick in-app membrane visualization run `/membrane view` through run_chimerax_command.",
            "For contact-surface, buried-area, or PISA questions, use analyze_structure with report=pisa; for direct measurement run ChimeraX `interfaces select` and `measure buriedarea` commands from that report.",
            "Keep command batches short. After visual changes, inspect scene state if the next action depends on whether the view changed.",
            "Do not use shell commands. Do not perform destructive broad actions unless the user explicitly asked for them.",
            "When the goal is complete, stop calling tools and return a concise final report with what changed and any residual uncertainty.",
        ]
    )


def _run_openai_response_exec(
    session,
    prompt,
    *,
    model,
    reasoning,
    output_schema=None,
    progress=None,
    image_paths=None,
    cleanup_paths=None,
):
    try:
        _emit_progress(progress, "Launching OpenAI Responses API...")
        _emit_progress(progress, f"Model: {model or 'default'} / reasoning: {reasoning or 'default'}")
        response = _openai_create_response(
            api_key=resolve_backend_api_key("openai"),
            model=model,
            reasoning=reasoning,
            input_items=_openai_input_items(prompt, image_paths=image_paths),
            instructions="You are helping a user from inside UCSF ChimeraX. Keep replies concise and grounded in the provided session context.",
            text_schema=output_schema,
        )
        reply = _openai_response_text(response).strip()
        if not reply:
            raise CodexBridgeError("OpenAI returned no final message.")
        if output_schema is not None:
            reply = _extract_json_text(reply)
        _emit_progress(progress, "OpenAI response received.")
        return reply
    finally:
        for path in cleanup_paths or []:
            try:
                os.remove(path)
            except OSError:
                pass


def _openai_create_response(
    *,
    api_key,
    model,
    reasoning,
    input_items,
    instructions=None,
    tools=None,
    text_schema=None,
    parallel_tool_calls=None,
):
    body = {
        "model": model,
        "input": input_items,
    }
    if instructions:
        body["instructions"] = instructions
    if reasoning:
        body["reasoning"] = {"effort": reasoning}
    if tools:
        body["tools"] = tools
    if parallel_tool_calls is not None:
        body["parallel_tool_calls"] = bool(parallel_tool_calls)
    if text_schema is not None:
        body["text"] = {
            "format": {
                "type": "json_schema",
                "name": "chimerax_response",
                "schema": text_schema,
                "strict": True,
            }
        }

    request = Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=300) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as err:
        detail = err.read().decode("utf-8", errors="replace")
        raise CodexBridgeError(f"OpenAI API error {err.code}: {detail[:1200]}") from err
    except URLError as err:
        raise CodexBridgeError(f"OpenAI API request failed: {err}") from err


def _openai_input_items(prompt, *, image_paths=None):
    content = [{"type": "input_text", "text": prompt}]
    for image_path in image_paths or []:
        data_url = _image_data_url(image_path)
        if data_url:
            content.append({"type": "input_image", "image_url": data_url})
    return [{"role": "user", "content": content}]


def _image_data_url(path):
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return None
    suffix = Path(path).suffix.lower()
    mime = "image/jpeg" if suffix in {".jpg", ".jpeg"} else "image/png"
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def _openai_response_text(response):
    direct = response.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct

    parts = []
    for item in response.get("output") or []:
        item_type = item.get("type")
        if item_type == "message":
            for content in item.get("content") or []:
                if not isinstance(content, dict):
                    continue
                if content.get("type") in {"output_text", "text"}:
                    text = content.get("text")
                    if text:
                        parts.append(str(text))
        elif item_type in {"output_text", "text"}:
            text = item.get("text")
            if text:
                parts.append(str(text))
    return "\n".join(parts).strip()


def _openai_function_calls(response):
    calls = []
    for item in response.get("output") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "function_call":
            calls.append(item)
    return calls


def _tool_result_preview(result):
    text = str(result or "").strip()
    if not text:
        return "(empty)"
    text = re.sub(r"\s+", " ", text)
    return text[:240] + ("..." if len(text) > 240 else "")


def _emit_progress(progress, message, kind="info"):
    if progress is not None:
        progress(message, kind=kind)


def _analysis_text_only_request(prompt):
    lowered = str(prompt or "").lower()
    return any(
        token in lowered
        for token in ("text only", "설명만", "말로만", "요약만", "command 없이", "시각화 없이", "no visualization")
    )


def _analysis_auto_apply_request(prompt):
    lowered = str(prompt or "").lower()
    apply_tokens = (
        "apply",
        "run it",
        "execute",
        "do it",
        "update the view",
        "change the view",
        "show it in chimerax",
        "apply it",
        "실행",
        "적용",
        "바꿔",
        "보여줘",
        "시각화해",
        "그려줘",
    )
    return any(token in lowered for token in apply_tokens)


def _analysis_focus_hint(prompt):
    lowered = str(prompt or "").lower()
    if any(word in lowered for word in ("domain", "domains", "chunk", "architecture", "도메인", "구조 구획")):
        return "Prioritize domain architecture, inserts, repeats, and whether the current chunking is over- or under-split. Do not default to interface analysis unless the user explicitly asks about interfaces."
    if any(word in lowered for word in ("interface", "oligomer", "assembly", "complex", "접촉", "인터페이스", "복합체")):
        return "Prioritize interface organization, assembly topology, symmetry, and interface-vs-scaffold roles."
    if any(word in lowered for word in ("motif", "active site", "active-site", "catalytic", "metal", "ligand", "pocket", "촉매", "활성부위", "금속", "리간드")):
        return "Prioritize motif, pocket, ligand, metal, and catalytic-site interpretation instead of generic assembly summaries."
    return "Prioritize the user's latest question and avoid over-emphasizing interfaces unless specifically relevant."


def _mentions_pisa_interface(prompt):
    lowered = str(prompt or "").lower()
    return any(
        word in lowered
        for word in (
            "pisa",
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
    )


def _focused_uniprot_context(session, prompt):
    lowered = str(prompt or "").lower()
    residue_tokens = (
        "uniprot",
        "feature",
        "site annotation",
        "binding site",
        "active site",
        "active-site",
        "residue",
        "residues",
        "selection",
        "selected",
        "잔기",
        "핵심",
        "선택",
        "어노테이션",
    )
    if not any(token in lowered for token in residue_tokens):
        return "(not requested)"
    return format_uniprot_residue_report(session, prompt)


def _analysis_conversation_hint(session, prompt):
    memory = getattr(session, "_codex_bridge_analysis_memory", [])
    if not memory:
        return "This may be the first analysis turn. A compact structured overview is fine."
    lowered = str(prompt or "").lower()
    if any(word in lowered for word in ("again", "처음부터", "full", "전체", "처음부터 다시")):
        return "The user asked for a fresh pass, so a compact whole-structure overview is acceptable."
    return "This is a follow-up request. Focus on new or clarified points, respond directly to the latest question, and do not repeat generic whole-assembly summaries already covered."


def _analysis_visual_mode_for_prompt(session, prompt):
    lowered = str(prompt or "").lower()
    if _analysis_text_only_request(prompt):
        return None

    beautify = any(
        word in lowered
        for word in ("pretty", "beautiful", "beautify", "clean", "polish", "예쁘", "이쁘", "깔끔", "보기좋", "보기 좋", "멋있", "정돈")
    )
    explicit_visual = beautify or any(
        word in lowered
        for word in ("visual", "visualize", "figure", "view", "show", "display", "render", "시각화", "보여", "표시", "그림")
    )
    selected = any(word in lowered for word in ("selected", "selection", "선택"))

    if not explicit_visual:
        return None

    if any(word in lowered for word in ("domain", "domains", "chunk", "도메인", "architecture")):
        return "domains"
    if any(word in lowered for word in ("role", "assembly", "multimer", "complex", "복합체", "멀티머")):
        return "roles"
    if any(word in lowered for word in ("interface", "oligomer", "접촉", "인터페이스")):
        return "selection-interface" if selected else "interface"
    if any(word in lowered for word in ("motif", "패턴")):
        return "selection-motif" if selected else "domains"
    if any(word in lowered for word in ("ligand", "metal", "catalytic", "active site", "active-site", "pocket", "리간드", "금속", "활성부위", "촉매")):
        return "selection-pocket" if selected else "pocket"
    if explicit_visual and selected:
        return "selection"
    if beautify:
        return "clean"
    if explicit_visual:
        return "publication"
    return None


def _visual_first_request(prompt):
    lowered = str(prompt or "").lower()
    visual_words = (
        "visual", "visualize", "render", "figure", "publication", "paper figure",
        "시각화", "그림", "보여", "보이", "표시", "색", "color", "surface", "cartoon",
        "transparency", "zoom", "turn", "view", "label", "도메인별", "nature", "네이처",
        "pretty", "beautiful", "beautify", "clean", "polish", "예쁘", "이쁘", "깔끔",
    )
    analysis_words = (
        "analy", "analysis", "hypothesis", "observ", "next checks", "interpret", "explain",
        "분석", "해석", "설명", "가설", "요약", "정리",
    )
    visual_score = sum(1 for word in visual_words if word in lowered)
    analysis_score = sum(1 for word in analysis_words if word in lowered)
    return visual_score >= 2 and analysis_score == 0


def _extract_chimerax_commands_from_reply(text):
    reply = str(text or "")
    blocks = []
    fence_pattern = re.compile(r"```(?:chimerax)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)
    for match in fence_pattern.finditer(reply):
        body = match.group(1)
        commands = []
        for raw_line in body.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            commands.append(line)
        if commands:
            blocks.append(commands)
    if blocks:
        return blocks

    lines = reply.splitlines()
    in_section = False
    section_commands = []
    for raw_line in lines:
        line = raw_line.strip()
        if line == "Suggested ChimeraX commands":
            in_section = True
            continue
        if in_section and line in {"Summary", "Observations", "Hypotheses", "Next checks", "Suggested visuals"}:
            break
        if in_section and line.startswith("- "):
            command = line[2:].strip()
            if command:
                section_commands.append(command)
    if section_commands:
        blocks.append(section_commands)
    return blocks


def _summarize_embedded_chimerax_commands(reply):
    blocks = _extract_chimerax_commands_from_reply(reply)
    if not blocks:
        return None
    commands = []
    for block in blocks:
        commands.extend(block)
    if not commands:
        return None
    return "\n".join(
        [
            "Suggested ChimeraX commands from backend reply (not auto-executed in analyze/chat mode).",
            "Suggested ChimeraX commands:",
            *[f"- {command}" for command in commands],
        ]
    )


def _apply_embedded_chimerax_commands(session, reply, *, executor, progress=None):
    blocks = _extract_chimerax_commands_from_reply(reply)
    if not blocks:
        return None
    commands = []
    for block in blocks:
        commands.extend(block)
    if not commands:
        return None
    _emit_progress(progress, f"[route] executing {len(commands)} ChimeraX command(s) extracted from backend reply")
    executed = []
    for command in commands:
        try:
            _emit_progress(progress, f"[run] {command}")
            executor(command)
            executed.append(command)
            _emit_progress(progress, f"[done] {command}")
        except Exception as err:
            error_text = str(err) if str(err) else err.__class__.__name__
            _emit_progress(progress, f"[error] extracted command failed -> {command}: {error_text}", kind="error")
            break
    if not executed:
        return None
    return "\n".join(
        [
            "Executed ChimeraX commands extracted from backend reply.",
            "Executed ChimeraX commands:",
            *[f"- {command}" for command in executed],
        ]
    )


def _looks_like_structured_analysis_reply(text):
    lowered = str(text or "").lower()
    return "observations" in lowered and "hypotheses" in lowered and "next checks" in lowered


def _looks_like_noop_visual_result(text):
    lowered = str(text or "").lower()
    if "executed chimeraX commands".lower() in lowered:
        return False
    return "no commands to run" in lowered or "no actions to run" in lowered or "nothing to execute" in lowered


def _read_process_stream(stream, sink, progress, kind):
    if stream is None:
        return
    try:
        for line in stream:
            text = line.rstrip("\n")
            sink.append(text)
            _emit_progress(progress, text, kind=kind)
    finally:
        try:
            stream.close()
        except Exception:
            pass


def _append_turn_memory(session, mode, user_prompt, reply):
    memory = session._codex_bridge_turn_memory
    memory.append(
        {
            "mode": str(mode or "chat"),
            "backend": get_current_backend_id(session),
            "prompt": str(user_prompt or "").strip(),
            "summary_lines": _reply_memory_lines(reply),
        }
    )
    if len(memory) > 8:
        del memory[:-8]


def _turn_memory_text(session, limit=5):
    memory = getattr(session, "_codex_bridge_turn_memory", [])
    if not memory:
        return "(none)"
    blocks = []
    for index, item in enumerate(memory[-limit:], start=1):
        summary = " | ".join(item.get("summary_lines", [])[:4]) or "(none)"
        blocks.append(
            "\n".join(
                [
                    f"Turn {index} [{item.get('mode', 'chat')}/{item.get('backend', 'unknown')}]: {item.get('prompt', '').strip()}",
                    f"Summary: {summary}",
                ]
            )
        )
    return "\n\n".join(blocks)


def _recent_command_history_text(session, limit=10):
    command_history = list(getattr(session, "_codex_bridge_command_history", []) or [])
    terminal_history = list(getattr(session, "_codex_bridge_terminal_history", []) or [])
    lines = []
    if command_history:
        lines.append("Recent ChimeraX commands:")
        for command in command_history[-limit:]:
            lines.append(f"- {command}")
    if terminal_history:
        if lines:
            lines.append("")
        lines.append("Recent in-app terminal events:")
        for item in terminal_history[-limit:]:
            kind = item.get("kind", "terminal")
            command = item.get("command", "")
            status = item.get("status", "")
            preview = item.get("preview", "")
            line = f"- [{kind}] {command}".strip()
            if status:
                line += f" -> {status}"
            if preview:
                line += f" | {preview}"
            lines.append(line)
    return "\n".join(lines) if lines else "(none)"


def _reply_memory_lines(reply):
    lines = []
    for raw_line in str(reply or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line in {"Executed ChimeraX commands:", "Suggested ChimeraX commands:"}:
            continue
        if line.startswith(("```", "# ", "! ")):
            continue
        lines.append(line[:180])
        if len(lines) >= 6:
            break
    return lines or ["(no summary)"]


def _append_analysis_memory(session, user_prompt, brief, reply):
    memory = session._codex_bridge_analysis_memory
    catalytic = best_catalytic_candidates(session)
    dali = format_dali_report(session)
    memory.append(
        {
            "request": user_prompt.strip(),
            "brief": "\n".join(brief.splitlines()[:12]),
            "reply": "\n".join(reply.splitlines()[:18]),
            "observations": _extract_section(reply, "Observations"),
            "hypotheses": _extract_section(reply, "Hypotheses"),
            "next_checks": _extract_section(reply, "Next checks"),
            "catalytic_candidates": [c["residue_spec"] for c in catalytic[:8]],
            "top_catalytic": [f"{c['residue_spec']} score {c['score']}" for c in catalytic[:5]],
            "dali": dali,
        }
    )
    if len(memory) > 5:
        del memory[:-5]


def _analysis_memory_text(session):
    memory = getattr(session, "_codex_bridge_analysis_memory", [])
    if not memory:
        return "(none)"
    blocks = []
    for index, item in enumerate(memory[-3:], start=1):
        observation_lines = item.get("observations", [])[:3]
        hypothesis_lines = item.get("hypotheses", [])[:3]
        next_lines = item.get("next_checks", [])[:3]
        blocks.append(
            "\n".join(
                [
                    f"Memory {index}: {item['request']}",
                    "Observations: " + (" | ".join(observation_lines) if observation_lines else "(none)"),
                    "Hypotheses: " + (" | ".join(hypothesis_lines) if hypothesis_lines else "(none)"),
                    "Next checks: " + (" | ".join(next_lines) if next_lines else "(none)"),
                ]
            )
        )
    return "\n\n".join(blocks)


def format_memory_compare(session):
    memory = getattr(session, "_codex_bridge_analysis_memory", [])
    if len(memory) < 2:
        return "Not enough analysis history to compare."

    prev = memory[-2]
    curr = memory[-1]
    prev_h = set(prev.get("hypotheses", []))
    curr_h = set(curr.get("hypotheses", []))
    added = sorted(curr_h - prev_h)
    removed = sorted(prev_h - curr_h)
    prev_c = set(prev.get("catalytic_candidates", []))
    curr_c = set(curr.get("catalytic_candidates", []))
    added_c = sorted(curr_c - prev_c)
    removed_c = sorted(prev_c - curr_c)
    prev_n = set(prev.get("next_checks", []))
    curr_n = set(curr.get("next_checks", []))
    added_n = sorted(curr_n - prev_n)
    removed_n = sorted(prev_n - curr_n)

    lines = [
        f"Previous: {prev['request']}",
        f"Current: {curr['request']}",
    ]
    if added:
        lines.append("Added hypotheses:")
        lines.extend(f"- {line}" for line in added[:8])
    if removed:
        lines.append("Removed hypotheses:")
        lines.extend(f"- {line}" for line in removed[:8])
    if added_c:
        lines.append("New catalytic candidates:")
        lines.extend(f"- {line}" for line in added_c[:8])
    if removed_c:
        lines.append("Dropped catalytic candidates:")
        lines.extend(f"- {line}" for line in removed_c[:8])
    prev_o = set(prev.get("observations", []))
    curr_o = set(curr.get("observations", []))
    added_o = sorted(curr_o - prev_o)
    if added_o:
        lines.append("New observations:")
        lines.extend(f"- {line}" for line in added_o[:8])
    if added_n:
        lines.append("New next checks:")
        lines.extend(f"- {line}" for line in added_n[:8])
    if removed_n:
        lines.append("Removed next checks:")
        lines.extend(f"- {line}" for line in removed_n[:8])
    if curr.get("top_catalytic"):
        lines.append("Current top catalytic set:")
        lines.extend(f"- {line}" for line in curr["top_catalytic"][:5])
    if not added and not removed and not added_c and not removed_c and not added_n and not removed_n:
        lines.append("No major hypothesis changes detected.")
    return "\n".join(lines)


def _extract_section(text, title):
    lines = text.splitlines()
    collected = []
    in_section = False
    headers = {
        "Summary",
        "Observations",
        "Hypotheses",
        "Next checks",
        "Suggested visuals",
        "Suggested ChimeraX commands",
    }
    for line in lines:
        stripped = line.strip()
        if stripped == title:
            in_section = True
            continue
        if in_section and stripped in headers:
            break
        if in_section and stripped:
            collected.append(stripped)
    return collected

def _build_codex_command(cli_path, output_path, prompt, model, reasoning, schema_path, image_paths=None):
    command = [
        cli_path,
        "exec",
        "--skip-git-repo-check",
        "--ephemeral",
        "--sandbox",
        "read-only",
        "--color",
        "never",
        "--output-last-message",
        output_path,
    ]
    if model:
        command.extend(["-m", model])
    if reasoning:
        command.extend(["-c", f'model_reasoning_effort="{reasoning}"'])
    if schema_path is not None:
        command.extend(["--output-schema", schema_path])
    for image_path in image_paths or []:
        command.extend(["--image", image_path])
    # `--image <FILE>...` is variadic in Codex CLI and can greedily consume a
    # trailing prompt token unless we terminate option parsing explicitly.
    command.extend(["--", prompt])
    return command


def _build_claude_command(cli_path, prompt, model, reasoning, output_schema):
    command = [
        cli_path,
        "-p",
        "--output-format",
        "text",
        "--permission-mode",
        "plan",
        "--bare",
    ]
    if model:
        command.extend(["--model", model])
    if reasoning:
        command.extend(["--effort", reasoning])
    if output_schema is not None:
        command.extend(["--json-schema", json.dumps(output_schema, separators=(",", ":"))])
    command.append(prompt)
    return command


def _build_gemini_command(cli_path, prompt, model):
    command = [
        cli_path,
        "-p",
        prompt,
        "--output-format",
        "text",
        "--approval-mode",
        "plan",
    ]
    if model:
        command.extend(["-m", model])
    return command


def _with_json_schema_prompt(prompt, schema):
    return "\n\n".join(
        [
            prompt,
            "Return only raw JSON with no markdown fences.",
            f"JSON schema:\n{json.dumps(schema, indent=2)}",
        ]
    )


def _extract_json_text(reply):
    text = reply.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()
    if text.startswith("{") or text.startswith("["):
        return text
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end != -1 and end > start:
            return text[start:end + 1]
    return text
