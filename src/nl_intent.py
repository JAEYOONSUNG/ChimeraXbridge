import re
import json
from datetime import datetime
from pathlib import Path


def canonical_intent_hints(text):
    lowered = normalize_user_phrase(text)
    hints = []

    def add(*items):
        for item in items:
            item = str(item or "").strip()
            if item and item not in hints:
                hints.append(item)

    if any(token in lowered for token in (
        "전체 다 보이", "전체 보여", "전체 보기", "전부 보여", "전부 보이",
        "한눈에", "줌 풀", "zoom out", "camera reset", "reset camera",
        "카메라 리셋", "시야 리셋", "전체 구조", "멀리서", "fit all",
    )):
        add("view all", "focus all", "fit all")

    if any(token in lowered for token in ("선택", "selection", "selected", "sel")) and any(
        token in lowered for token in ("보여", "보이", "focus", "확대", "클로즈업", "가까이", "highlight")
    ):
        add("view sel", "focus sel")

    if any(token in lowered for token in ("도메인", "domain")):
        if any(token in lowered for token in ("쪼개", "나눠", "분할", "separate", "split", "chunk")):
            add("domains view")
        if any(token in lowered for token in ("더 쪼개", "세분화", "잘게", "finer", "more split", "more detailed")):
            add("domains finer")
        if any(token in lowered for token in ("덜 쪼개", "합쳐", "coarser", "merge", "less split")):
            add("domains coarser")
        if any(token in lowered for token in ("원래대로", "reset", "기본값", "default")):
            add("domains reset")

    if any(token in lowered for token in ("예쁘", "이쁘", "깔끔", "보기좋", "보기 좋", "pretty", "beautiful", "beautify", "clean", "polish")):
        add("figure clean")

    if any(token in lowered for token in ("figure", "피겨", "논문 그림", "publication")):
        add("figure publication")
        if any(token in lowered for token in ("전체", "overview", "summary", "overall", "한눈에")):
            add("figure composite")
        if any(token in lowered for token in ("포켓", "리간드", "활성부위", "촉매", "pocket", "ligand", "active site")):
            add("figure pocket")
        if any(token in lowered for token in ("인터페이스", "접촉", "interface")):
            add("figure interface")
        if any(token in lowered for token in ("분리", "벌려", "explode", "spread", "separate")):
            add("figure explode")

    if any(token in lowered for token in ("리간드", "포켓", "ligand", "pocket")):
        add("ligand pocket")
    if any(token in lowered for token in ("촉매", "활성부위", "catalytic", "active site")):
        add("catalytic site")
    if any(token in lowered for token in ("인터페이스", "접촉", "interface")):
        add("interface")
    if any(token in lowered for token in ("motif", "모티프", "패턴")):
        add("motif")
    if any(token in lowered for token in ("uniprot", "타겟 서열", "타겟서열", "target sequence", "서열")) and any(
        token in lowered for token in ("motif", "모티프", "패턴", "analysis", "분석")
    ):
        add("uniprot motif analysis", "sequence motif")

    if any(token in lowered for token in ("scene", "bookmark", "장면", "씬", "북마크", "구도", "이 상태", "현재 상태", "이 화면", "현재 화면")):
        if any(token in lowered for token in ("save", "store", "remember", "저장", "기억")):
            add("scene save")
        if any(token in lowered for token in ("load", "restore", "open", "불러", "복원", "되돌")):
            add("scene load")
        if any(token in lowered for token in ("list", "목록", "리스트", "뭐있", "뭐 있")):
            add("scene list")
        if any(token in lowered for token in ("info", "details", "metadata", "정보", "상세")):
            add("scene info")
        if any(token in lowered for token in ("note", "memo", "메모", "노트")):
            add("scene note")

    if any(token in lowered for token in ("스틱", "stick", "sticks")):
        add("show atoms", "style stick")
    if any(token in lowered for token in ("표면", "surface")):
        add("surface")
    if any(token in lowered for token in ("숨겨", "가려", "hide")):
        add("hide")
    if any(token in lowered for token in ("보여", "표시", "show")):
        add("show")

    if any(token in lowered for token in ("색", "컬러", "color")):
        add("color")
        if any(token in lowered for token in ("원소", "element", "n/o", "nitrogen", "oxygen")):
            add("color byelement")
        if any(token in lowered for token in ("체인", "chain")):
            add("color bychain")
        if any(token in lowered for token in ("모델", "model")):
            add("color bymodel")

    if any(token in lowered for token in ("라벨", "label", "이름")):
        add("label residues")

    for hint in user_alias_hints(text):
        add(hint)
    return hints


def normalized_prompt_for_matching(text):
    lowered = normalize_user_phrase(text)
    hints = canonical_intent_hints(text)
    if hints:
        lowered = lowered + " " + " ".join(hints).lower()
    return lowered.strip()


def normalize_user_phrase(text):
    lowered = str(text or "").lower()
    lowered = lowered.replace("피겨", "figure")
    lowered = lowered.replace("뷰포트", "view")
    lowered = lowered.replace("카메라", "camera")
    lowered = lowered.replace("줌아웃", "zoom out")
    lowered = lowered.replace("줌 아웃", "zoom out")
    lowered = lowered.replace("줌풀", "줌 풀")
    lowered = lowered.replace("한 눈에", "한눈에")
    lowered = re.sub(r"\s+", " ", lowered)
    return lowered.strip()


def _user_data_dir():
    return Path.home() / "Library" / "Application Support" / "ChimeraX" / "1.10" / "codex_bridge_user"


def _alias_path():
    return _user_data_dir() / "nl_aliases.json"


def load_user_aliases():
    path = _alias_path()
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def save_user_aliases(aliases):
    path = _alias_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(aliases, ensure_ascii=False, indent=2), encoding="utf-8")


def user_alias_hints(text):
    normalized = normalize_user_phrase(text)
    if not normalized:
        return []
    aliases = load_user_aliases()
    hints = []
    for phrase, entry in aliases.items():
        phrase_text = str(phrase or "").strip()
        if not phrase_text:
            continue
        if phrase_text != normalized and (len(phrase_text) < 4 or phrase_text not in normalized):
            continue
        positive = int(entry.get("positive", 0) or 0)
        negative = int(entry.get("negative", 0) or 0)
        if negative > positive + 1:
            continue
        for hint in entry.get("hints", []):
            hint = str(hint or "").strip()
            if hint and hint not in hints:
                hints.append(hint)
    return hints


def remember_user_alias(prompt, hints, *, positive=True, source="auto"):
    normalized = normalize_user_phrase(prompt)
    if not normalized:
        return
    cleaned_hints = []
    for hint in hints or []:
        hint = str(hint or "").strip()
        if hint and hint not in cleaned_hints:
            cleaned_hints.append(hint)
    if not cleaned_hints:
        return

    aliases = load_user_aliases()
    entry = aliases.get(normalized, {})
    existing_hints = [str(h).strip() for h in entry.get("hints", []) if str(h).strip()]
    for hint in cleaned_hints:
        if hint not in existing_hints:
            existing_hints.append(hint)
    entry["hints"] = existing_hints
    entry["positive"] = int(entry.get("positive", 0) or 0) + (1 if positive else 0)
    entry["negative"] = int(entry.get("negative", 0) or 0) + (0 if positive else 1)
    entry["source"] = source
    entry["updated_at"] = datetime.now().isoformat(timespec="seconds")
    aliases[normalized] = entry
    save_user_aliases(aliases)


def user_alias_summary(limit=10):
    aliases = load_user_aliases()
    rows = []
    for phrase, entry in aliases.items():
        hints = ", ".join(entry.get("hints", [])[:4]) or "(none)"
        score = int(entry.get("positive", 0) or 0) - int(entry.get("negative", 0) or 0)
        rows.append((score, phrase, hints, entry))
    rows.sort(key=lambda item: (-item[0], item[1]))
    lines = []
    for score, phrase, hints, entry in rows[:limit]:
        lines.append(f"- {phrase} -> {hints} (score {score:+d})")
    return lines
