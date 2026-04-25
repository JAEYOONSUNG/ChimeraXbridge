def docs_hints_for_prompt(prompt):
    lowered = prompt.lower()
    hints = [
        "Use official ChimeraX 1.10.1 user-guide command syntax. Prefer documented commands over invented aliases.",
    ]

    if any(word in lowered for word in ("transparent", "transparency", "투명", "불투명")):
        hints.append(
            "Official docs: `transparency <spec> <percent> [target <letters>]`; default target is surfaces (`s`)."
        )
    if any(word in lowered for word in ("publication", "figure", "논문", "그림", "preset")):
        hints.append(
            "Official docs: built-in presets include `preset sil`, `preset cartoon`, and `preset interactive`."
        )
    if any(word in lowered for word in ("surface", "cartoon", "ribbon", "stick")):
        hints.append(
            "Official docs: `surface`, `~surface`, `cartoon`, and `style <spec> stick` are valid commands."
        )
    if any(word in lowered for word in ("color", "색", "chain")):
        hints.append("Official docs: `color bychain`, `color bymodel`, and `rainbow` are valid.")
    if any(word in lowered for word in ("focus", "zoom", "view")):
        hints.append("Official docs: use `view all` or `view sel` to refocus the scene.")
    return hints
