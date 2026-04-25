from chimerax.core.commands import BoolArg, CmdDesc, RestOfLine, StringArg
from chimerax.core.errors import UserError

from .backends import (
    backend_status_lines,
    clear_effort_override,
    clear_model_override,
    ensure_session_preferences,
    get_backend_defaults,
    get_effort_override,
    get_backend_label,
    get_backend_spec,
    get_current_backend_id,
    get_model_override,
    get_routing_mode,
    resolve_backend_api_key,
    list_backend_ids,
    resolve_backend_cli,
    resolve_request_quality,
    set_current_backend_id,
    set_effort_override,
    set_model_override,
    set_routing_mode,
    suggested_efforts_for_backend,
    suggested_models_for_backend,
)
from .control_intent import try_handle_control_intent
from .integration import command_batch
from .nl_intent import user_alias_summary
from .nl_memory import telemetry_summary
from .nl_intent import canonical_intent_hints, normalized_prompt_for_matching
from .service import build_session_context, run_mode_request


def ai(session, prompt=""):
    prompt = prompt.strip()
    if not prompt:
        if not session.ui.is_gui:
            raise UserError("Prompt text is required in non-GUI mode.")
        try:
            from chimerax.cmd_line.tool import CommandLine

            CommandLine.get_singleton(session, create=True, display=True)
        except Exception:
            pass
        from .tool import CodexAssistant
        assistant = CodexAssistant.get_singleton(session)
        if assistant is not None:
            assistant.display(True)
            assistant._show_assistant_tab()
            assistant._focus_prompt()
        return "Opened AI workspace."

    ensure_session_preferences(session)
    control_result = try_handle_control_intent(session, prompt)
    if control_result is not None:
        session.logger.info(control_result)
        return control_result
    progress = _session_progress(session)
    backend_label = get_backend_label(get_current_backend_id(session))
    fast_mode, _profile, effective = resolve_request_quality(session, "combined")
    session.logger.status(f"Running {backend_label} agent inside ChimeraX ({effective})...")
    with command_batch(session, f"AI:combined:{prompt[:80]}"):
        summary = run_mode_request(session, prompt, mode="combined", progress=progress, fast=fast_mode)
    session.logger.status(f"{backend_label} agent run finished.")
    return summary


ai_desc = CmdDesc(
    optional=[("prompt", RestOfLine)],
)


def codex_ask(session, prompt):
    prompt = prompt.strip()
    if not prompt:
        raise UserError("Prompt text is required.")

    ensure_session_preferences(session)
    progress = _session_progress(session)
    backend_label = get_backend_label(get_current_backend_id(session))
    fast_mode, _profile, effective = resolve_request_quality(session, "chat")
    session.logger.status(f"Querying {backend_label} ({effective})...")
    response = run_mode_request(session, prompt, mode="chat", progress=progress, fast=fast_mode)
    session.logger.info(response)
    session.logger.status(f"{backend_label} reply ready.")
    return response


codex_ask_desc = CmdDesc(
    required=[("prompt", RestOfLine)],
)


def codex_auto(session, enabled=True):
    session._codex_bridge_nl_fallback = bool(enabled)
    state = "enabled" if session._codex_bridge_nl_fallback else "disabled"
    message = f"AI natural-language fallback is {state}."
    session.logger.info(message)
    return message


codex_auto_desc = CmdDesc(
    optional=[("enabled", BoolArg)],
)


def codex_context(session, *, include_models=True, include_selection=True):
    context = build_session_context(
        session,
        include_models=include_models,
        include_selection=include_selection,
    )
    session.logger.info(context)
    return context


codex_context_desc = CmdDesc(
    keyword=[
        ("include_models", BoolArg),
        ("include_selection", BoolArg),
    ],
)


def codex_selftest(session):
    ensure_session_preferences(session)
    lines = ["Codex Bridge self-test"]
    lines.append(f"backend: {get_current_backend_id(session)} ({get_backend_label(get_current_backend_id(session))})")
    lines.append(f"routing: {get_routing_mode(session)}")
    lines.append(f"speed: {resolve_request_quality(session, 'combined')[2]}")

    lines.append("backends:")
    for backend_id in list_backend_ids():
        label = get_backend_label(backend_id)
        spec = get_backend_spec(backend_id)
        if spec.get("transport") == "api":
            status = "(API key set)" if resolve_backend_api_key(backend_id, strict=False) else "(missing API key)"
        else:
            cli_path = resolve_backend_cli(backend_id, strict=False)
            status = cli_path if cli_path else "(not found)"
        lines.append(f"- {backend_id} ({label}): {status}")

    sample_prompts = [
        "전체 다 보이게 해줘",
        "도메인 더 쪼개줘",
        "포켓 위주로 figure",
        "인터페이스 강조해줘",
        "줌 풀어",
    ]
    try:
        from .builtin_actions import _focus_command_from_text
    except Exception as err:
        lines.append(f"focus parser: FAIL ({type(err).__name__}: {err})")
    else:
        lines.append("natural-language parser:")
        for prompt in sample_prompts:
            hints = ", ".join(canonical_intent_hints(prompt)) or "(none)"
            norm = normalized_prompt_for_matching(prompt)
            focus = _focus_command_from_text(prompt)
            lines.append(f"- {prompt}")
            lines.append(f"  hints: {hints}")
            lines.append(f"  normalized: {norm}")
            lines.append(f"  focus: {focus or '(none)'}")

    if session.ui.is_gui:
        try:
            from .tool import CodexAssistant
            assistant = CodexAssistant.get_singleton(session, create=False, display=False)
            lines.append("tool: " + ("loaded" if assistant is not None else "not loaded"))
        except Exception as err:
            lines.append(f"tool: FAIL ({type(err).__name__}: {err})")
    else:
        lines.append("tool: non-GUI session")

    telemetry = telemetry_summary(limit=5)
    lines.append(f"nl telemetry: events={telemetry['events']} feedback={telemetry['feedback']}")
    if telemetry["recent"]:
        lines.append("recent events:")
        lines.extend(telemetry["recent"])

    aliases = user_alias_summary(limit=8)
    lines.append(f"user aliases: {len(aliases)} shown")
    lines.extend(aliases or ["- (none)"])

    message = "\n".join(lines)
    session.logger.info(message)
    return message


codex_selftest_desc = CmdDesc()


def codex_routing(session, name=None):
    ensure_session_preferences(session)
    if name is None:
        mode = get_routing_mode(session)
        message = "\n".join(
            [
                f"Routing mode: {mode}",
                "Available: ai-first, local-first, backend-only",
                "ai-first: send natural-language requests to the backend agent first",
                "local-first: keep deterministic local fastpaths first",
                "backend-only: skip local natural-language fastpaths",
            ]
        )
        session.logger.info(message)
        return message

    mode = name.strip().lower()
    set_routing_mode(session, mode)
    message = f"Routing mode: {mode}"
    session.logger.info(message)
    return message


codex_routing_desc = CmdDesc(
    optional=[("name", StringArg)],
)


def codex_tool(session):
    if not session.ui.is_gui:
        raise UserError("The Codex Assistant tool requires a ChimeraX GUI session.")

    try:
        from chimerax.cmd_line.tool import CommandLine

        CommandLine.get_singleton(session, create=True, display=True)
    except Exception:
        pass
    from .tool import CodexAssistant

    assistant = CodexAssistant.get_singleton(session)
    if assistant is not None:
        assistant.display(True)
        assistant._show_assistant_tab()
        assistant._focus_prompt()


codex_tool_desc = CmdDesc()


def codex_actions(session):
    if not session.ui.is_gui:
        raise UserError("The Codex Action Pad requires a ChimeraX GUI session.")

    from . import _ensure_action_pad_models_tab

    _ensure_action_pad_models_tab(session, raise_action=True)


codex_actions_desc = CmdDesc()


def codex_seqbar(session):
    if not session.ui.is_gui:
        raise UserError("The Codex Sequence Bar requires a ChimeraX GUI session.")

    from .sequence_bar import CodexSequenceBar

    sequence_bar = CodexSequenceBar.get_singleton(session)
    if sequence_bar is not None:
        sequence_bar.display(True)
        sequence_bar.refresh()


codex_seqbar_desc = CmdDesc()


def codex_backend(session, name=None):
    ensure_session_preferences(session)
    if name is None:
        message = "\n".join(backend_status_lines(session))
        session.logger.info(message)
        return message

    backend_id = name.strip().lower()
    if backend_id not in list_backend_ids():
        raise UserError(f"Unknown backend: {name}")
    set_current_backend_id(session, backend_id)
    message = f"Current backend: {backend_id} ({get_backend_label(backend_id)})"
    session.logger.info(message)
    return message


codex_backend_desc = CmdDesc(
    optional=[("name", StringArg)],
)


def codex_model(session, name=None):
    ensure_session_preferences(session)
    backend_id = get_current_backend_id(session)
    backend_label = get_backend_label(backend_id)

    if name is None:
        override = get_model_override(session, backend_id)
        fast_default, precise_default = get_backend_defaults(backend_id, True)[0], get_backend_defaults(backend_id, False)[0]
        lines = [
            f"Backend: {backend_id} ({backend_label})",
            f"Override model: {override or '(default)'}",
            f"Override effort: {get_effort_override(session, backend_id) or '(default)'}",
            f"Fast default: {fast_default or '(provider default)'}",
            f"Precise default: {precise_default or '(provider default)'}",
            "Suggested models: " + ", ".join(suggested_models_for_backend(backend_id)),
        ]
        message = "\n".join(lines)
        session.logger.info(message)
        return message

    model_name = name.strip()
    if model_name.lower() in ("list", "ls", "?"):
        message = "\n".join(
            [
                f"Backend: {backend_id} ({backend_label})",
                "Suggested models: " + ", ".join(suggested_models_for_backend(backend_id)),
            ]
        )
        session.logger.info(message)
        return message
    if model_name.lower() in ("default", "reset", "clear"):
        clear_model_override(session, backend_id)
        message = f"Cleared model override for {backend_label}."
    else:
        set_model_override(session, model_name, backend_id)
        message = f"Model override for {backend_label}: {model_name}"
    session.logger.info(message)
    return message


codex_model_desc = CmdDesc(
    optional=[("name", RestOfLine)],
)


def codex_effort(session, name=None):
    ensure_session_preferences(session)
    backend_id = get_current_backend_id(session)
    backend_label = get_backend_label(backend_id)

    if name is None:
        efforts = suggested_efforts_for_backend(backend_id)
        lines = [
            f"Backend: {backend_id} ({backend_label})",
            f"Override effort: {get_effort_override(session, backend_id) or '(default)'}",
            "Suggested efforts: " + (", ".join(efforts) if efforts else "(not supported)"),
        ]
        message = "\n".join(lines)
        session.logger.info(message)
        return message

    effort_name = name.strip().lower()
    if effort_name in ("list", "ls", "?"):
        message = "\n".join(
            [
                f"Backend: {backend_id} ({backend_label})",
                "Suggested efforts: " + (", ".join(suggested_efforts_for_backend(backend_id)) or "(not supported)"),
            ]
        )
        session.logger.info(message)
        return message

    if effort_name in ("default", "reset", "clear"):
        clear_effort_override(session, backend_id)
        message = f"Cleared effort override for {backend_label}."
        session.logger.info(message)
        return message

    allowed = suggested_efforts_for_backend(backend_id)
    if not allowed:
        raise UserError(f"{backend_label} does not expose effort controls in this bridge.")
    if effort_name not in allowed:
        raise UserError(f"Unknown effort for {backend_label}: {effort_name}")

    set_effort_override(session, effort_name, backend_id)
    message = f"Effort override for {backend_label}: {effort_name}"
    session.logger.info(message)
    return message


codex_effort_desc = CmdDesc(
    optional=[("name", RestOfLine)],
)


def _session_progress(session):
    ensure_session_preferences(session)
    backend_label = get_backend_label(get_current_backend_id(session))

    def progress(message, kind="info"):
        line = f"[{backend_label}] {message}"
        if kind == "error":
            session.logger.error(line)
        else:
            session.logger.info(line)
            session.logger.status(message)

    return progress
