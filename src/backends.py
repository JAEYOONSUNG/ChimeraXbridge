import json
import os
import shutil
from pathlib import Path


BACKEND_SPECS = {
    "openai": {
        "label": "OpenAI API",
        "cli_envs": (),
        "api_key_envs": ("OPENAI_API_KEY", "CODEX_BRIDGE_OPENAI_API_KEY"),
        "candidates": (),
        "fast_model_env": "CODEX_BRIDGE_OPENAI_FAST_MODEL",
        "fast_reasoning_env": "CODEX_BRIDGE_OPENAI_FAST_REASONING",
        "precise_model_env": "CODEX_BRIDGE_OPENAI_PRECISE_MODEL",
        "precise_reasoning_env": "CODEX_BRIDGE_OPENAI_PRECISE_REASONING",
        "fast_model_default": "gpt-5.5",
        "fast_reasoning_default": "high",
        "precise_model_default": "gpt-5.5",
        "precise_reasoning_default": "high",
        "supports_schema": True,
        "supports_tools": True,
        "transport": "api",
    },
    "codex": {
        "label": "Codex CLI",
        "cli_envs": ("CODEX_BRIDGE_CLI", "CODEX_BRIDGE_CODEX_CLI"),
        "candidates": (
            lambda: shutil.which("codex"),
            lambda: str(Path.home() / "bin" / "codex"),
            lambda: "/opt/homebrew/bin/codex",
            lambda: "/usr/local/bin/codex",
        ),
        "fast_model_env": "CODEX_BRIDGE_CODEX_FAST_MODEL",
        "fast_reasoning_env": "CODEX_BRIDGE_CODEX_FAST_REASONING",
        "precise_model_env": "CODEX_BRIDGE_CODEX_PRECISE_MODEL",
        "precise_reasoning_env": "CODEX_BRIDGE_CODEX_PRECISE_REASONING",
        "fast_model_default": "gpt-5.5",
        "fast_reasoning_default": "high",
        "precise_model_default": "gpt-5.5",
        "precise_reasoning_default": "high",
        "supports_schema": True,
        "supports_tools": False,
        "transport": "cli",
    },
    "claude": {
        "label": "Claude",
        "cli_envs": ("CODEX_BRIDGE_CLAUDE_CLI",),
        "candidates": (
            lambda: shutil.which("claude"),
            lambda: str(Path.home() / "bin" / "claude"),
            lambda: str(Path.home() / ".local" / "bin" / "claude"),
        ),
        "fast_model_env": "CODEX_BRIDGE_CLAUDE_FAST_MODEL",
        "fast_reasoning_env": "CODEX_BRIDGE_CLAUDE_FAST_REASONING",
        "precise_model_env": "CODEX_BRIDGE_CLAUDE_PRECISE_MODEL",
        "precise_reasoning_env": "CODEX_BRIDGE_CLAUDE_PRECISE_REASONING",
        "fast_model_default": "claude-opus-4-7",
        "fast_reasoning_default": "xhigh",
        "precise_model_default": "claude-opus-4-7",
        "precise_reasoning_default": "xhigh",
        "supports_schema": True,
        "supports_tools": False,
        "transport": "cli",
    },
    "gemini": {
        "label": "Gemini",
        "cli_envs": ("CODEX_BRIDGE_GEMINI_CLI",),
        "candidates": (
            lambda: shutil.which("gemini"),
            lambda: "/opt/homebrew/bin/gemini",
        ),
        "fast_model_env": "CODEX_BRIDGE_GEMINI_FAST_MODEL",
        "fast_reasoning_env": "CODEX_BRIDGE_GEMINI_FAST_REASONING",
        "precise_model_env": "CODEX_BRIDGE_GEMINI_PRECISE_MODEL",
        "precise_reasoning_env": "CODEX_BRIDGE_GEMINI_PRECISE_REASONING",
        "fast_model_default": "gemini-3.1-pro-preview",
        "fast_reasoning_default": None,
        "precise_model_default": "gemini-3.1-pro-preview",
        "precise_reasoning_default": None,
        "supports_schema": False,
        "supports_tools": False,
        "transport": "cli",
    },
}

SPEED_PROFILES = ("auto", "fast", "precise")
ROUTING_MODES = ("ai-first", "local-first", "backend-only")


def _cached_codex_models():
    cache_path = Path.home() / ".codex" / "models_cache.json"
    if not cache_path.exists():
        return []
    try:
        data = json.loads(cache_path.read_text())
    except Exception:
        return []
    return [m.get("slug") for m in data.get("models", []) if m.get("slug")]


def _dedupe_models(*model_lists):
    seen = set()
    models = []
    for model_list in model_lists:
        for model in model_list or []:
            value = str(model or "").strip()
            if not value or value in seen:
                continue
            seen.add(value)
            models.append(value)
    return models


def _split_model_list(value):
    if not value:
        return []
    for separator in (",", ";", "\n", "\t"):
        value = str(value).replace(separator, " ")
    return [part.strip() for part in value.split(" ") if part.strip()]


def _catalog_model_paths():
    explicit = os.environ.get("CODEX_BRIDGE_MODEL_CATALOG")
    if explicit:
        yield Path(explicit).expanduser()
    yield Path.home() / ".config" / "chimerax_codex_bridge" / "models.json"
    yield Path.home() / ".chimerax_codex_models.json"


def _models_from_catalog(backend_id):
    for path in _catalog_model_paths():
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict):
            if isinstance(data.get("models"), dict):
                values = data["models"].get(backend_id) or data["models"].get(backend_id.upper())
            else:
                values = data.get(backend_id) or data.get(backend_id.upper())
            if isinstance(values, str):
                return _split_model_list(values)
            if isinstance(values, list):
                return [str(item).strip() for item in values if str(item).strip()]
    return []


def _configured_models_for_backend(backend_id):
    env_key = f"CODEX_BRIDGE_{backend_id.upper()}_MODELS"
    return _dedupe_models(
        _split_model_list(os.environ.get(env_key)),
        _models_from_catalog(backend_id),
    )


def _pick_available_codex_model(preferred):
    available = _cached_codex_models()
    if preferred and preferred[0] == "gpt-5.5":
        return preferred[0]
    if not available:
        return preferred[0]
    for candidate in preferred:
        if candidate in available:
            return candidate
    return available[0]


def ensure_session_preferences(session):
    if not hasattr(session, "_codex_bridge_backend_id"):
        session._codex_bridge_backend_id = "codex"
    if not hasattr(session, "_codex_bridge_model_overrides"):
        session._codex_bridge_model_overrides = {}
    if not hasattr(session, "_codex_bridge_effort_overrides"):
        session._codex_bridge_effort_overrides = {}
    if not hasattr(session, "_codex_bridge_last_backend_error"):
        session._codex_bridge_last_backend_error = {}
    if not hasattr(session, "_codex_bridge_analysis_memory"):
        session._codex_bridge_analysis_memory = []
    if not hasattr(session, "_codex_bridge_dali_history"):
        session._codex_bridge_dali_history = []
    if not hasattr(session, "_codex_bridge_speed_profile"):
        session._codex_bridge_speed_profile = "auto"
    if not hasattr(session, "_codex_bridge_routing_mode"):
        session._codex_bridge_routing_mode = "ai-first"
    if not hasattr(session, "_codex_bridge_turn_memory"):
        session._codex_bridge_turn_memory = []
    if not hasattr(session, "_codex_bridge_last_prompt_state"):
        session._codex_bridge_last_prompt_state = None
    if not hasattr(session, "_codex_bridge_command_history"):
        session._codex_bridge_command_history = []
    if not hasattr(session, "_codex_bridge_terminal_history"):
        session._codex_bridge_terminal_history = []
    if not hasattr(session, "_codex_bridge_command_batch_depth"):
        session._codex_bridge_command_batch_depth = 0
    if not hasattr(session, "_codex_bridge_command_batch_commands"):
        session._codex_bridge_command_batch_commands = []
    if not hasattr(session, "_codex_bridge_command_batch_label"):
        session._codex_bridge_command_batch_label = None
    if not hasattr(session, "_codex_bridge_dock_fraction"):
        session._codex_bridge_dock_fraction = 0.30
    if not hasattr(session, "_codex_bridge_workspace_fraction"):
        session._codex_bridge_workspace_fraction = 0.35
    if not hasattr(session, "_codex_bridge_pick_mode_initialized"):
        session._codex_bridge_pick_mode_initialized = False
    if not hasattr(session, "_codex_bridge_scene_bookmarks"):
        session._codex_bridge_scene_bookmarks = {}
    if not hasattr(session, "_codex_bridge_last_scene_name"):
        session._codex_bridge_last_scene_name = None
    if not hasattr(session, "_codex_bridge_last_nl_event"):
        session._codex_bridge_last_nl_event = None
    if not hasattr(session, "_codex_bridge_last_feedback"):
        session._codex_bridge_last_feedback = None
    if not hasattr(session, "_codex_bridge_last_blast_name"):
        session._codex_bridge_last_blast_name = None
    if not hasattr(session, "_codex_bridge_last_alignment_id"):
        session._codex_bridge_last_alignment_id = None
    if not hasattr(session, "_codex_bridge_last_similar_name"):
        session._codex_bridge_last_similar_name = None
    if not hasattr(session, "_codex_bridge_last_conservation_profile"):
        session._codex_bridge_last_conservation_profile = None
    if not hasattr(session, "_codex_bridge_conservation_cache"):
        session._codex_bridge_conservation_cache = {}
    if not hasattr(session, "_codex_bridge_last_catalytic_candidates"):
        session._codex_bridge_last_catalytic_candidates = []
    if not hasattr(session, "_codex_bridge_last_membrane_segments"):
        session._codex_bridge_last_membrane_segments = []
    if not hasattr(session, "_codex_bridge_virtual_membrane_models"):
        session._codex_bridge_virtual_membrane_models = []
    if not hasattr(session, "_codex_bridge_virtual_membrane_specs"):
        session._codex_bridge_virtual_membrane_specs = []
    if not hasattr(session, "_codex_bridge_last_pisa_pairs"):
        session._codex_bridge_last_pisa_pairs = []
    if not hasattr(session, "_codex_bridge_last_pisa_commands"):
        session._codex_bridge_last_pisa_commands = []


def list_backend_ids():
    return tuple(BACKEND_SPECS.keys())


def get_backend_spec(backend_id):
    return BACKEND_SPECS[backend_id]


def get_backend_label(backend_id):
    return BACKEND_SPECS[backend_id]["label"]


def backend_transport(backend_id):
    return BACKEND_SPECS[backend_id].get("transport", "cli")


def backend_uses_cli(backend_id):
    return backend_transport(backend_id) == "cli"


def backend_supports_tools(backend_id):
    return bool(BACKEND_SPECS[backend_id].get("supports_tools"))


def get_current_backend_id(session):
    ensure_session_preferences(session)
    return session._codex_bridge_backend_id


def set_current_backend_id(session, backend_id):
    ensure_session_preferences(session)
    if backend_id not in BACKEND_SPECS:
        raise ValueError(f"Unknown backend: {backend_id}")
    session._codex_bridge_backend_id = backend_id


def get_model_override(session, backend_id=None):
    ensure_session_preferences(session)
    backend_id = backend_id or get_current_backend_id(session)
    return session._codex_bridge_model_overrides.get(backend_id)


def set_model_override(session, model, backend_id=None):
    ensure_session_preferences(session)
    backend_id = backend_id or get_current_backend_id(session)
    if model:
        session._codex_bridge_model_overrides[backend_id] = model


def clear_model_override(session, backend_id=None):
    ensure_session_preferences(session)
    backend_id = backend_id or get_current_backend_id(session)
    session._codex_bridge_model_overrides.pop(backend_id, None)


def get_effort_override(session, backend_id=None):
    ensure_session_preferences(session)
    backend_id = backend_id or get_current_backend_id(session)
    return session._codex_bridge_effort_overrides.get(backend_id)


def set_effort_override(session, effort, backend_id=None):
    ensure_session_preferences(session)
    backend_id = backend_id or get_current_backend_id(session)
    if effort:
        session._codex_bridge_effort_overrides[backend_id] = effort


def clear_effort_override(session, backend_id=None):
    ensure_session_preferences(session)
    backend_id = backend_id or get_current_backend_id(session)
    session._codex_bridge_effort_overrides.pop(backend_id, None)


def get_speed_profile(session):
    ensure_session_preferences(session)
    profile = getattr(session, "_codex_bridge_speed_profile", "auto")
    return profile if profile in SPEED_PROFILES else "auto"


def set_speed_profile(session, profile):
    ensure_session_preferences(session)
    normalized = str(profile).strip().lower()
    if normalized not in SPEED_PROFILES:
        raise ValueError(f"Unknown speed profile: {profile}")
    session._codex_bridge_speed_profile = normalized


def get_routing_mode(session):
    ensure_session_preferences(session)
    mode = getattr(session, "_codex_bridge_routing_mode", "ai-first")
    return mode if mode in ROUTING_MODES else "ai-first"


def set_routing_mode(session, mode):
    ensure_session_preferences(session)
    normalized = str(mode).strip().lower()
    if normalized not in ROUTING_MODES:
        raise ValueError(f"Unknown routing mode: {mode}")
    session._codex_bridge_routing_mode = normalized


def resolve_request_quality(session, mode):
    profile = get_speed_profile(session)
    normalized_mode = str(mode or "").strip().lower()
    if profile == "auto":
        effective = "fast" if normalized_mode in {"agent", "combined", "visualize"} else "precise"
    else:
        effective = profile
    return effective == "fast", profile, effective


def get_backend_defaults(backend_id, fast):
    spec = BACKEND_SPECS[backend_id]
    if fast:
        env_override = os.environ.get(spec["fast_model_env"])
        if backend_id == "codex":
            model = env_override or _pick_available_codex_model(
                [
                    spec["fast_model_default"],
                    "gpt-5.4",
                    "gpt-5.3-codex-spark",
                    "gpt-5.3-codex",
                    "gpt-5.2",
                ]
            )
        else:
            model = env_override or spec["fast_model_default"]
        reasoning = os.environ.get(spec["fast_reasoning_env"], spec["fast_reasoning_default"])
    else:
        env_override = os.environ.get(spec["precise_model_env"])
        if backend_id == "codex":
            model = env_override or _pick_available_codex_model(
                [
                    spec["precise_model_default"],
                    "gpt-5.2",
                    "gpt-5.3-codex",
                    "gpt-5.4-mini",
                ]
            )
        else:
            model = env_override or spec["precise_model_default"]
        reasoning = os.environ.get(spec["precise_reasoning_env"], spec["precise_reasoning_default"])
    return model, reasoning


def sanitize_model_override(backend_id, model):
    if not model:
        return None, None

    normalized = str(model).strip()
    if normalized.lower() in {"low", "medium", "high", "xhigh", "max"}:
        return None, (
            f'Ignoring invalid model override "{normalized}" for {get_backend_label(backend_id)}. '
            "It looks like a reasoning level, not a model name."
        )

    return normalized, None


def resolve_backend_cli(backend_id, strict=True):
    spec = BACKEND_SPECS[backend_id]
    if not backend_uses_cli(backend_id):
        return None
    candidates = []
    for env_name in spec["cli_envs"]:
        env_value = os.environ.get(env_name)
        if env_value:
            candidates.append(env_value)
    for provider in spec["candidates"]:
        candidates.append(provider())

    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)

    if strict:
        label = get_backend_label(backend_id)
        raise FileNotFoundError(
            f"Could not find the {label} CLI. "
            f"Set one of: {', '.join(spec['cli_envs'])}"
        )
    return None


def resolve_backend_api_key(backend_id, strict=True):
    spec = BACKEND_SPECS[backend_id]
    env_names = spec.get("api_key_envs") or ()
    for env_name in env_names:
        value = os.environ.get(env_name)
        if value:
            return value

    if strict:
        label = get_backend_label(backend_id)
        raise FileNotFoundError(
            f"Could not find an API key for {label}. "
            f"Set one of: {', '.join(env_names)}"
        )
    return None


def backend_availability(backend_id):
    """Return (available, short_status, detail) for UI gating."""
    spec = BACKEND_SPECS[backend_id]
    if backend_uses_cli(backend_id):
        path = resolve_backend_cli(backend_id, strict=False)
        if path:
            return True, "installed", f"{path}. Login/subscription is verified by the CLI when a request runs."
        cli_hint = ", ".join(spec.get("cli_envs") or ())
        detail = f"CLI not found. Set {cli_hint} or install/login to {spec['label']}."
        return False, "missing CLI", detail

    api_key = resolve_backend_api_key(backend_id, strict=False)
    if api_key:
        return True, "ready", "API key available"
    env_hint = ", ".join(spec.get("api_key_envs") or ())
    return False, "missing API key", f"Set {env_hint} before launching ChimeraX."


def backend_status_lines(session):
    ensure_session_preferences(session)
    current = get_current_backend_id(session)
    lines = []
    for backend_id in list_backend_ids():
        spec = get_backend_spec(backend_id)
        label = spec["label"]
        prefix = "*" if backend_id == current else "-"
        if backend_uses_cli(backend_id):
            path = resolve_backend_cli(backend_id, strict=False)
            if path:
                model = get_model_override(session, backend_id)
                effort = get_effort_override(session, backend_id)
                model_text = f" model={model}" if model else ""
                effort_text = f" effort={effort}" if effort else ""
                lines.append(f"{prefix} {backend_id} ({label}) available{model_text}{effort_text}")
            else:
                lines.append(f"{prefix} {backend_id} ({label}) unavailable")
        else:
            api_key = resolve_backend_api_key(backend_id, strict=False)
            model = get_model_override(session, backend_id)
            effort = get_effort_override(session, backend_id)
            model_text = f" model={model}" if model else ""
            effort_text = f" effort={effort}" if effort else ""
            status = "available" if api_key else "unavailable (missing API key)"
            lines.append(f"{prefix} {backend_id} ({label}) {status}{model_text}{effort_text}")
    return lines


def suggested_models_for_backend(backend_id):
    configured = _configured_models_for_backend(backend_id)
    if backend_id == "openai":
        return _dedupe_models(
            configured,
            [
                "gpt-5.5",
                "gpt-5.4",
                "gpt-5.4-mini",
                "gpt-5.4-nano",
            ],
        )

    if backend_id == "codex":
        models = _cached_codex_models()
        return _dedupe_models(
            configured,
            models,
            [
                "gpt-5.5",
                "gpt-5.4",
                "gpt-5.4-mini",
                "gpt-5.3-codex",
                "gpt-5.3-codex-spark",
                "gpt-5.2-codex",
            ],
        )

    if backend_id == "claude":
        return _dedupe_models(
            configured,
            [
                "opus",
                "claude-opus-4-7",
                "sonnet",
                "claude-sonnet-4-6",
                "haiku",
            ],
        )

    if backend_id == "gemini":
        return _dedupe_models(
            configured,
            [
                "gemini-2.5-flash",
                "gemini-2.5-pro",
                "gemini-3.1-pro-preview",
            ],
        )

    return []


def suggested_efforts_for_backend(backend_id):
    if backend_id in {"openai", "codex"}:
        return ["low", "medium", "high", "xhigh"]
    if backend_id == "claude":
        return ["low", "medium", "high", "xhigh", "max"]
    return []
