"""Utilities for insertion-style ChimeraX model ID reordering."""

import re


def parse_model_id_text(text):
    value = str(text or "").strip().lstrip("#")
    if not value:
        raise ValueError("Model ID is empty.")
    if not re.fullmatch(r"\d+(?:\.\d+)*", value):
        raise ValueError("Model ID must be one or more positive integers separated by dots.")
    ids = tuple(int(part) for part in value.split("."))
    if any(part <= 0 for part in ids):
        raise ValueError("Model ID values must be positive integers.")
    return ids


def model_from_spec(session, spec):
    ids = parse_model_id_text(spec)
    models = session.models.list(model_id=ids)
    return models[0] if models else None


def reorder_model_spec_to_id(session, model_spec, target_id):
    model = model_from_spec(session, model_spec)
    if model is None:
        raise ValueError(f"Could not find model {model_spec!r}.")
    return reorder_top_model_to_id(session, model, target_id)


def reorder_top_model_to_id(session, model, target_id):
    target = parse_model_id_text(target_id)
    if len(target) != 1:
        raise ValueError("Insertion-style reorder only supports top-level model IDs.")
    target_number = target[0]

    if model is None or getattr(model, "id", None) is None:
        raise ValueError("No model to reorder.")
    if len(model.id) != 1:
        raise ValueError(
            f"Only top-level models can be reordered this way; {model.atomspec} is a submodel."
        )
    if getattr(model, "parent", None) is not session.models.scene_root_model:
        raise ValueError(
            f"{model.atomspec} is not a normal scene model; overlay/root models cannot be reordered."
        )

    current_number = int(model.id[0])
    if current_number == target_number:
        return f"{model.atomspec} is already at ID #{target_number}."

    top_models = _top_level_scene_models(session)
    if model not in top_models:
        raise ValueError(f"{model.atomspec} is not in the top-level scene model list.")

    affected = _affected_models_for_move(top_models, model, current_number, target_number)
    new_ids = {}
    if target_number < current_number:
        for item in affected:
            old = int(item.id[0])
            new_ids[item] = (target_number,) if item is model else (old + 1,)
    else:
        for item in affected:
            old = int(item.id[0])
            new_ids[item] = (target_number,) if item is model else (old - 1,)

    _ensure_no_external_collisions(session, affected, new_ids)
    old_labels = {item: f"#{item.id_string}" for item in affected}
    temp_ids = _temporary_top_ids(session, len(affected))

    for item, temp_id in zip(affected, temp_ids):
        session.models.assign_id(item, (temp_id,))
    for item in sorted(affected, key=lambda m: new_ids[m][0]):
        session.models.assign_id(item, new_ids[item])

    _invalidate_semantics(session)
    moved_label = old_labels.get(model, f"#{current_number}")
    summary = ", ".join(
        f"{old_labels[item]}->{item.atomspec}"
        for item in sorted(affected, key=lambda m: new_ids[m][0])
    )
    return f"Moved {moved_label} to #{target_number}; shifted {len(affected) - 1} model(s): {summary}"


def _top_level_scene_models(session):
    root = session.models.scene_root_model
    models = [
        model
        for model in session.models.list()
        if getattr(model, "id", None)
        and len(model.id) == 1
        and getattr(model, "parent", None) is root
    ]
    return sorted(models, key=lambda model: int(model.id[0]))


def _affected_models_for_move(top_models, model, current, target):
    if target < current:
        return [
            item
            for item in top_models
            if item is model or target <= int(item.id[0]) < current
        ]
    return [
        item
        for item in top_models
        if item is model or current < int(item.id[0]) <= target
    ]


def _ensure_no_external_collisions(session, affected, new_ids):
    affected_set = set(affected)
    for item, new_id in new_ids.items():
        existing = session.models.list(model_id=new_id)
        if existing and existing[0] not in affected_set:
            raise ValueError(
                f"Cannot move to #{'.'.join(str(x) for x in new_id)} because it is used by "
                f"{existing[0]} outside the reorder range."
            )


def _temporary_top_ids(session, count):
    existing = {int(model.id[0]) for model in session.models.list() if getattr(model, "id", None)}
    start = max(existing or {0}) + 1000
    values = []
    candidate = start
    while len(values) < count:
        if candidate not in existing and not session.models.have_id((candidate,)):
            values.append(candidate)
        candidate += 1
    return values


def _invalidate_semantics(session):
    try:
        from .semantic import invalidate_semantic_cache

        invalidate_semantic_cache(session)
    except Exception:
        pass
