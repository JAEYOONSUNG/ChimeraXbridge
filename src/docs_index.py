import re
from collections import Counter
from functools import lru_cache
from pathlib import Path


APP_ROOT = Path("/Applications/ChimeraX-1.10.1.app")
DOCS_DIR = APP_ROOT / "Contents/share/docs/user/commands"
STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "into", "then", "plus",
    "show", "view", "display", "command", "commands", "request", "user", "using",
    "structure", "model", "models", "figure", "publication", "please",
    "그리고", "하고", "다음", "현재", "선택", "보여줘", "보여", "그림",
}


def docs_dir_exists():
    return any(iter_command_doc_paths())


@lru_cache(maxsize=1)
def load_docs_index():
    paths = list(iter_command_doc_paths())
    if not paths:
        return []

    docs = []
    for path in paths:
        if path.name in {"index.html", "usageconventions.html"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        title = _extract_title(text) or path.stem
        aliases = _extract_aliases(title)
        usages = _extract_usages(text)
        summary = _extract_summary(text)
        source = _doc_source_label(path)
        docs.append(
            {
                "command": title,
                "aliases": aliases,
                "path": str(path),
                "usage": usages[:3],
                "summary": summary,
                "source": source,
                "keywords": _keyword_bag(title, aliases, usages, summary),
            }
        )
    return docs


def command_snippets_for_prompt(prompt, limit=6):
    docs = load_docs_index()
    if not docs:
        return []

    tokens = _query_tokens(prompt)
    scored = []
    for doc in docs:
        score = 0
        joined = " ".join(doc["keywords"])
        for token in tokens:
            if token in doc.get("aliases", []):
                score += 6
            elif token in joined:
                score += 1
        if score:
            scored.append((score, doc))

    scored.sort(key=lambda item: (-item[0], item[1]["command"]))
    results = []
    seen = set()
    for _score, doc in scored:
        if doc["command"] in seen:
            continue
        seen.add(doc["command"])
        results.append(doc)
        if len(results) >= limit:
            break
    return results


def likely_command_aliases(prompt, limit=8):
    docs = command_snippets_for_prompt(prompt, limit=limit)
    aliases = []
    for doc in docs:
        for alias in doc.get("aliases", []):
            if alias not in aliases:
                aliases.append(alias)
    return aliases[:limit]


def format_docs_snippets(prompt, limit=6):
    snippets = command_snippets_for_prompt(prompt, limit=limit)
    if not snippets:
        return "(no matching local command-doc snippets)"

    lines = []
    for item in snippets:
        alias_text = ""
        aliases = item.get("aliases", [])
        if aliases:
            alias_text = " (" + ", ".join(aliases[:4]) + ")"
        lines.append(f"- {item['command']}{alias_text}" + (f" [{item['source']}]" if item.get("source") else ""))
        if item["usage"]:
            lines.extend(f"  - usage: {usage}" for usage in item["usage"][:2])
        if item["summary"]:
            lines.append(f"  - summary: {item['summary']}")
    return "\n".join(lines)


def docs_index_stats():
    docs = load_docs_index()
    sources = Counter(item.get("source", "share") for item in docs)
    return {
        "count": len(docs),
        "sources": dict(sources),
    }


def iter_command_doc_paths():
    if DOCS_DIR.exists():
        for path in sorted(DOCS_DIR.glob("*.html")):
            yield path
    pattern = "Contents/Library/Frameworks/Python.framework/Versions/3.11/lib/python3.11/site-packages/chimerax/*/docs/user/commands/*.html"
    for path in sorted(APP_ROOT.glob(pattern)):
        yield path


def _doc_source_label(path):
    rel = str(path.relative_to(APP_ROOT))
    if "site-packages/chimerax/" not in rel:
        return "core"
    frag = rel.split("site-packages/chimerax/", 1)[1]
    return frag.split("/", 1)[0]


def _extract_title(html):
    match = re.search(r"<title>Command:\s*([^<]+)</title>", html, flags=re.IGNORECASE)
    if not match:
        return None
    return " ".join(match.group(1).split())


def _extract_aliases(title):
    pieces = []
    for part in re.split(r"\s*,\s*|\s*/\s*|\s+or\s+", str(title or ""), flags=re.IGNORECASE):
        token = part.strip().lower()
        if token:
            pieces.append(token)
    deduped = []
    for piece in pieces:
        if piece not in deduped:
            deduped.append(piece)
    return deduped


def _extract_usages(html):
    usages = []
    for match in re.finditer(r"<h3 class=\"usage\".*?>.*?<br>\s*(.*?)</h3>", html, flags=re.IGNORECASE | re.DOTALL):
        usage = _strip_html(match.group(1))
        usage = " ".join(usage.split())
        if usage:
            usages.append(usage)
    return usages


def _extract_summary(html):
    for match in re.finditer(r"<p>(.*?)</p>", html, flags=re.IGNORECASE | re.DOTALL):
        text = _strip_html(match.group(1))
        text = " ".join(text.split())
        if len(text) >= 40:
            return text
    return ""


def _strip_html(text):
    stripped = re.sub(r"<[^>]+>", " ", text)
    stripped = stripped.replace("&nbsp;", " ").replace("&ndash;", "-")
    stripped = stripped.replace("&Aring;", "A").replace("&deg;", "deg")
    return stripped


def _keyword_bag(title, aliases, usages, summary):
    corpus = " ".join([title, *aliases, *usages, summary]).lower()
    return sorted(set(re.findall(r"[a-z0-9][a-z0-9._-]+", corpus)))


def _query_tokens(prompt):
    text = str(prompt or "").lower()
    raw = re.findall(r"[a-z0-9][a-z0-9._-]+", text)
    aliases = set()
    try:
        for item in load_docs_index():
            aliases.update(item.get("aliases", []))
    except Exception:
        aliases = set()
    return [token for token in raw if (token not in STOPWORDS or token in aliases) and len(token) > 1]
