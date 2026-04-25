import json
from datetime import datetime
from pathlib import Path

from .backends import ensure_session_preferences, get_routing_mode
from .nl_intent import canonical_intent_hints, normalize_user_phrase, remember_user_alias


def _user_data_dir():
    return Path.home() / "Library" / "Application Support" / "ChimeraX" / "1.10" / "codex_bridge_user"


def _events_path():
    return _user_data_dir() / "nl_events.jsonl"


def _feedback_path():
    return _user_data_dir() / "nl_feedback.jsonl"


def _append_jsonl(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def detect_feedback_signal(text):
    lowered = normalize_user_phrase(text)
    positive_words = (
        "좋다", "좋네", "좋아요", "됐다", "잘됐다", "오 잘", "works", "working", "good", "great", "perfect", "딱이다",
    )
    negative_words = (
        "안돼", "안되", "똑같", "이상", "별로", "못알아", "에러", "오류", "안바뀌", "안 바뀌", "wrong", "bad", "fail",
        "not working", "no visual change",
    )
    has_positive = any(token in lowered for token in positive_words)
    has_negative = any(token in lowered for token in negative_words)
    if has_positive and not has_negative:
        return "positive"
    if has_negative and not has_positive:
        return "negative"
    return None


def register_feedback(session, prompt):
    ensure_session_preferences(session)
    signal = detect_feedback_signal(prompt)
    if signal is None:
        return None
    last_event = getattr(session, "_codex_bridge_last_nl_event", None)
    if not last_event:
        return signal

    payload = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "feedback": signal,
        "feedback_text": str(prompt or "").strip(),
        "event_id": last_event.get("event_id"),
        "prompt": last_event.get("prompt"),
        "normalized": last_event.get("normalized"),
        "hints": list(last_event.get("hints", []) or []),
        "mode": last_event.get("mode"),
        "routing": last_event.get("routing"),
    }
    _append_jsonl(_feedback_path(), payload)
    remember_user_alias(last_event.get("prompt", ""), last_event.get("hints", []), positive=(signal == "positive"))
    session._codex_bridge_last_feedback = payload
    return signal


def record_nl_event(session, *, prompt, mode, result):
    ensure_session_preferences(session)
    prompt_text = str(prompt or "").strip()
    hints = canonical_intent_hints(prompt_text)
    normalized = normalize_user_phrase(prompt_text)
    preview = _result_preview(result)
    payload = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "event_id": datetime.now().strftime("%Y%m%d%H%M%S%f"),
        "prompt": prompt_text,
        "normalized": normalized,
        "hints": hints,
        "mode": str(mode or "").strip(),
        "routing": get_routing_mode(session),
        "scene_changed": _scene_changed_signal(result),
        "result_preview": preview,
    }
    _append_jsonl(_events_path(), payload)
    session._codex_bridge_last_nl_event = payload
    return payload


def telemetry_summary(limit=5):
    events = 0
    feedback = 0
    last_lines = []
    for path, counter_name in ((_events_path(), "events"), (_feedback_path(), "feedback")):
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue
        if counter_name == "events":
            events = len(lines)
        else:
            feedback = len(lines)
        if counter_name == "events":
            for line in lines[-limit:]:
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                last_lines.append(
                    f"- {item.get('timestamp', '?')} | {item.get('mode', '?')} | {item.get('prompt', '')[:60]} | changed={item.get('scene_changed')}"
                )
    return {
        "events": events,
        "feedback": feedback,
        "recent": last_lines[:limit],
    }


def _scene_changed_signal(result):
    lowered = str(result or "").lower()
    if "no visual change" in lowered or "completed without scene change" in lowered:
        return False
    if "executed chimeraX commands".lower() in lowered or "scene updated" in lowered:
        return True
    if "local chimeraX action executed".lower() in lowered:
        return True
    return None


def _result_preview(result):
    text = str(result or "").strip()
    if not text:
        return ""
    line = text.splitlines()[0].strip()
    return line[:200]
