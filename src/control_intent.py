from .backends import (
    backend_status_lines,
    clear_effort_override,
    clear_model_override,
    get_backend_defaults,
    get_backend_label,
    get_current_backend_id,
    get_effort_override,
    get_model_override,
    list_backend_ids,
    set_current_backend_id,
    set_effort_override,
    set_model_override,
    suggested_efforts_for_backend,
    suggested_models_for_backend,
)
from .service import build_session_context


BACKEND_ALIASES = {
    "codex": "codex",
    "코덱스": "codex",
    "코드엑스": "codex",
    "claude": "claude",
    "ccd": "claude",
    "클로드": "claude",
    "gemini": "gemini",
    "제미나이": "gemini",
    "제미니": "gemini",
}

EFFORT_ALIASES = {
    "low": "low",
    "fast": "low",
    "quick": "low",
    "빠르게": "low",
    "가볍게": "low",
    "light": "low",
    "medium": "medium",
    "balanced": "medium",
    "normal": "medium",
    "보통": "medium",
    "균형": "medium",
    "high": "high",
    "deep": "high",
    "careful": "high",
    "정밀": "high",
    "깊게": "high",
    "xhigh": "xhigh",
    "max": "max",
    "maximum": "max",
    "최대": "max",
}

CHANGE_HINTS = (
    "switch",
    "change",
    "use",
    "set",
    "select",
    "turn",
    "로",
    "으로",
    "바꿔",
    "바꿔줘",
    "전환",
    "사용",
    "설정",
    "선택",
    "켜",
)

STATUS_HINTS = ("status", "상태", "지금", "현재")
MODEL_LIST_HINTS = ("model list", "models", "모델 목록", "모델 뭐", "모델 추천", "모델 보여")
EFFORT_LIST_HINTS = ("effort list", "effort", "reasoning", "추론", "effort 뭐", "effort 보여")


def try_handle_control_intent(session, prompt):
    text = prompt.strip()
    lowered = text.lower()
    if not text:
        return None

    # Plain backend command: "codex", "ccd", "gemini"
    if lowered in BACKEND_ALIASES:
        backend_id = BACKEND_ALIASES[lowered]
        set_current_backend_id(session, backend_id)
        return _lines(
            f"backend: {backend_id} ({get_backend_label(backend_id)})",
            f"model: {_active_model_display(session)}",
            f"effort: {_active_effort_display(session)}",
        )

    # Status/context shortcuts
    if _looks_like_simple_command(lowered, ("context", "컨텍스트")):
        return build_session_context(session)
    if _looks_like_simple_command(lowered, STATUS_HINTS):
        return _status_text(session)

    # Model listing
    if lowered == "model" or lowered == "models" or _looks_like_simple_command(lowered, MODEL_LIST_HINTS):
        return _model_text(session)

    # Effort listing
    if lowered == "effort" or _looks_like_simple_command(lowered, EFFORT_LIST_HINTS):
        return _effort_text(session)

    backend_id = _find_backend(text)
    current_backend = backend_id or get_current_backend_id(session)
    model_name = _find_model(text, current_backend)
    effort_name = _find_effort(text, current_backend)

    looks_like_control = _looks_like_control(text, backend_id, model_name, effort_name)
    if not looks_like_control:
        return None

    lines = []

    if backend_id:
        set_current_backend_id(session, backend_id)
        lines.append(f"backend: {backend_id} ({get_backend_label(backend_id)})")
        current_backend = backend_id

    if model_name == "default":
        clear_model_override(session, current_backend)
        lines.append("model override: cleared")
    elif model_name:
        set_model_override(session, model_name, current_backend)
        lines.append(f"model override: {model_name}")

    if effort_name == "default":
        clear_effort_override(session, current_backend)
        lines.append("effort override: cleared")
    elif effort_name:
        allowed = suggested_efforts_for_backend(current_backend)
        if effort_name in allowed:
            set_effort_override(session, effort_name, current_backend)
            lines.append(f"effort override: {effort_name}")

    if lines:
        lines.append(f"model: {_active_model_display(session)}")
        lines.append(f"effort: {_active_effort_display(session)}")
        return "\n".join(lines)

    return None


def _status_text(session):
    lines = [
        f"backend: {get_current_backend_id(session)} ({get_backend_label(get_current_backend_id(session))})",
        f"model: {_active_model_display(session)}",
        f"effort: {_active_effort_display(session)}",
    ]
    return "\n".join(lines)


def _model_text(session):
    backend_id = get_current_backend_id(session)
    return "\n".join(
        [
            f"backend: {backend_id} ({get_backend_label(backend_id)})",
            f"model: {_active_model_display(session)}",
            "suggested: " + ", ".join(suggested_models_for_backend(backend_id)),
        ]
    )


def _effort_text(session):
    backend_id = get_current_backend_id(session)
    efforts = suggested_efforts_for_backend(backend_id)
    return "\n".join(
        [
            f"backend: {backend_id} ({get_backend_label(backend_id)})",
            f"effort: {_active_effort_display(session)}",
            "suggested: " + (", ".join(efforts) if efforts else "(not supported)"),
        ]
    )


def _find_backend(text):
    lowered = text.lower()
    for alias, backend_id in BACKEND_ALIASES.items():
        if alias.lower() in lowered or alias in text:
            return backend_id
    return None


def _find_model(text, backend_id):
    lowered = text.lower().strip()
    if any(token in lowered for token in ("model default", "model reset", "모델 기본", "모델 초기화", "default model")):
        return "default"

    candidates = suggested_models_for_backend(backend_id)
    if any(token in lowered for token in ("latest model", "newest model", "최신 모델", "최신모델")) and candidates:
        return candidates[0]
    for candidate in candidates:
        if candidate.lower() in lowered:
            return candidate

    # Useful short aliases
    if backend_id == "codex":
        alias_map = {
            "mini": "gpt-5.4-mini",
            "54": "gpt-5.4",
            "5.4": "gpt-5.4",
            "spark": "gpt-5.3-codex-spark",
        }
    elif backend_id == "claude":
        alias_map = {
            "sonnet": "sonnet",
            "opus 4.7": "claude-opus-4-7",
            "opus4.7": "claude-opus-4-7",
            "opus-4-7": "claude-opus-4-7",
            "opus": "claude-opus-4-7",
        }
    elif backend_id == "gemini":
        alias_map = {
            "flash": "gemini-2.5-flash",
            "pro": "gemini-2.5-pro",
        }
    else:
        alias_map = {}

    for alias, model in alias_map.items():
        if alias in lowered:
            return model
    return None


def _find_effort(text, backend_id):
    lowered = text.lower()
    if any(token in lowered for token in ("effort default", "effort reset", "추론 기본", "추론 초기화")):
        return "default"

    allowed = set(suggested_efforts_for_backend(backend_id))
    for alias, effort in EFFORT_ALIASES.items():
        if alias.lower() in lowered and effort in allowed:
            return effort
    return None


def _looks_like_control(text, backend_id, model_name, effort_name):
    lowered = text.lower()
    if any(hint in text for hint in CHANGE_HINTS):
        return True
    if backend_id and (model_name or effort_name):
        return True
    if model_name and any(token in lowered for token in ("model", "모델")):
        return True
    if effort_name and any(token in lowered for token in ("effort", "reason", "추론")):
        return True
    if text.count(" ") <= 2 and (backend_id or model_name or effort_name):
        return True
    return False


def _active_model_display(session):
    backend_id = get_current_backend_id(session)
    override = get_model_override(session, backend_id)
    if override:
        return override
    model, _reasoning = _defaults(session)
    return model or "(provider default)"


def _active_effort_display(session):
    backend_id = get_current_backend_id(session)
    override = get_effort_override(session, backend_id)
    if override:
        return override
    _model, reasoning = _defaults(session)
    return reasoning or "(provider default)"


def _defaults(session):
    backend_id = get_current_backend_id(session)
    return get_backend_defaults(backend_id, True)


def _lines(*lines):
    return "\n".join(lines)


def _looks_like_simple_command(text, hints):
    if len(text.split()) > 4:
        return False
    return any(hint in text for hint in hints)
