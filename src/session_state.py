"""Centralized cleanup of `session._codex_*` state when models close.

Without this, registry entries (cavity specs, RAPiDock pose models, hit
alignments, watcher tables) accumulate dead references that point to
already-closed structures. The next user click then either silently does
nothing or fires a cryptic NoneType error.

We register one `models removed` trigger handler at plugin init that prunes
every known `_codex_*` collection in O(n_open_models) time.
"""

from __future__ import annotations


def _structure_id_strings(session):
    """Set of id_string for currently open atomic structures."""
    try:
        from chimerax.atomic import AtomicStructure
        return {getattr(m, "id_string", "") for m in session.models.list(type=AtomicStructure)}
    except Exception:
        return set()


def _spec_to_model_id(spec):
    """Extract '#1' → '1' from any '#1', '#1/A', '#1/A:42' string."""
    if not spec or not isinstance(spec, str):
        return None
    text = spec.strip()
    if not text.startswith("#"):
        return None
    rest = text[1:]
    for sep in ("/", ":", " "):
        idx = rest.find(sep)
        if idx >= 0:
            rest = rest[:idx]
    return rest or None


def _prune_managed_specs(session, attr, alive_ids):
    specs = getattr(session, attr, None)
    if not specs or not isinstance(specs, list):
        return
    keep = []
    for entry in specs:
        # entry can be a single spec string, or a space-joined batch.
        if not isinstance(entry, str):
            keep.append(entry)
            continue
        toks = entry.split()
        if not toks:
            continue
        survivors = [t for t in toks if (_spec_to_model_id(t) or "") in alive_ids or _spec_to_model_id(t) is None]
        if survivors and len(survivors) == len(toks):
            keep.append(entry)
        elif survivors:
            keep.append(" ".join(survivors))
    setattr(session, attr, keep)


def _prune_pose_models(session, attr, alive_models):
    models = getattr(session, attr, None)
    if not models:
        return
    setattr(session, attr, [m for m in models if m in alive_models])


def _prune_alignment_registry(session, alive_ids):
    reg = getattr(session, "_codex_bridge_hit_alignments", None)
    if not isinstance(reg, dict):
        return
    dead = []
    for key, payload in reg.items():
        # Critic P1 #5: prune by KEY (entry spec) AND by payload's reference/moving
        # specs — if either the user's selected entry OR the alignment partner
        # closed, the payload is no longer valid.
        key_mid = _spec_to_model_id(key)
        if key_mid is not None and key_mid not in alive_ids:
            dead.append(key)
            continue
        if isinstance(payload, dict):
            ref_mid = _spec_to_model_id((payload.get("reference") or {}).get("spec", ""))
            mov_mid = _spec_to_model_id((payload.get("moving") or {}).get("spec", ""))
            if (ref_mid and ref_mid not in alive_ids) or (mov_mid and mov_mid not in alive_ids):
                dead.append(key)
    for key in dead:
        reg.pop(key, None)
    # Cap registry size to avoid unbounded growth across many Foldseek runs.
    max_entries = 64
    if len(reg) > max_entries:
        # Drop oldest insertion order (Python dict preserves it).
        for key in list(reg.keys())[: len(reg) - max_entries]:
            reg.pop(key, None)
    # Also clear "last alignment" caches if their referenced spec is gone
    cached = getattr(session, "_codex_bridge_last_structure_alignment_payload", None)
    if isinstance(cached, dict):
        for side in ("reference", "moving"):
            spec = (cached.get(side) or {}).get("spec", "")
            mid = _spec_to_model_id(spec)
            if mid is not None and mid not in alive_ids:
                session._codex_bridge_last_structure_alignment_payload = None
                session._codex_bridge_last_alignment_id = None
                break


def _prune_named_groups(session, alive_ids):
    """Remove CodexNamedSelectionGroup entries whose underlying structure closed."""
    try:
        from .named_selection import _registry as _ns_registry, remove_group
    except Exception:
        return
    reg = _ns_registry(session)
    if not reg:
        return
    dead = []
    for name, model in list(reg.items()):
        spec = getattr(model, "codex_named_spec", "") or ""
        mid = _spec_to_model_id(spec)
        if mid is not None and mid not in alive_ids:
            dead.append(name)
    for name in dead:
        try:
            remove_group(session, name)
        except Exception:
            pass


def _cleanup(session):
    alive_ids = _structure_id_strings(session)
    try:
        alive_models = set(session.models.list())
    except Exception:
        alive_models = set()
    # Each prune is wrapped so one failure cannot skip the others.
    for attr in (
        "_codex_cavity_managed_specs",
        "_codex_site_managed_specs",
    ):
        try:
            _prune_managed_specs(session, attr, alive_ids)
        except Exception:
            pass
    for attr in ("_codex_rapidock_pose_models",):
        try:
            _prune_pose_models(session, attr, alive_models)
        except Exception:
            pass
    try:
        _prune_alignment_registry(session, alive_ids)
    except Exception:
        pass
    try:
        _prune_named_groups(session, alive_ids)
    except Exception:
        pass


def _cancel_all_watchers(session):
    """Critic P1 #4: stop every codex daemon watcher when ChimeraX exits so
    threads don't outlive the session until their internal timeout expires.

    Each attribute is processed in its own try/except so one failing watcher
    table cannot block the rest from being cancelled at shutdown.
    """
    canceled = 0
    for attr in (
        "_codex_rapidock_watchers",
        "_codex_downloads_watchers",
        "_codex_alphafold_watchers",
        "_codex_afcomplex_watchers",
        "_codex_nucdock_watchers",
        "_codex_boltz_watchers",
        "_codex_foldmason_watchers",
        "_codex_folddisco_watchers",
        "_codex_dali_watchers",
        "_codex_vast_watchers",
        "_codex_pdbefold_watchers",
        "_codex_usalign_watchers",
        "_codex_caver_watchers",
    ):
        try:
            watchers = getattr(session, attr, None)
            if not isinstance(watchers, dict):
                continue
            for entry in list(watchers.values()):
                stop = entry.get("stop") if isinstance(entry, dict) else None
                if stop is not None:
                    try:
                        stop.set()
                        canceled += 1
                    except Exception:
                        pass
            try:
                setattr(session, attr, {})
            except Exception:
                pass
        except Exception:
            continue
    if canceled:
        try:
            session.logger.info(f"[Codex Bridge] Cancelled {canceled} background watcher(s) on shutdown.")
        except Exception:
            pass


def install_session_state_cleanup(session):
    """Register 'models removed' + app shutdown handlers.

    Each handler is tracked on its own session attr so a re-call of this
    function can retry whichever handler failed last time without skipping
    the other.
    """
    # 'models removed' / 'remove models' handler
    if not hasattr(session, "_codex_bridge_models_removed_handler"):
        handler = None
        for trigger_name in ("remove models", "models removed"):
            try:
                handler = session.triggers.add_handler(
                    trigger_name,
                    lambda _trigger, _data, ses=session: _cleanup(ses),
                )
                break
            except Exception:
                continue
        if handler is not None:
            session._codex_bridge_models_removed_handler = handler
        else:
            try:
                session.logger.warning(
                    "[Codex Bridge] Could not install 'models removed' handler — "
                    "stale registry entries won't be auto-pruned this session."
                )
            except Exception:
                pass

    # 'app quit' / 'session closed' watcher-cancel handler
    if not hasattr(session, "_codex_bridge_app_quit_handler"):
        quit_handler = None
        for trigger_name in ("app quit", "session closed"):
            try:
                quit_handler = session.triggers.add_handler(
                    trigger_name,
                    lambda _trigger, _data, ses=session: _cancel_all_watchers(ses),
                )
                break
            except Exception:
                continue
        if quit_handler is not None:
            session._codex_bridge_app_quit_handler = quit_handler
