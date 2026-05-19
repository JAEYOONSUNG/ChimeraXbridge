import importlib
import importlib.util
import gc
import shutil
import sys
from pathlib import Path

from Qt.QtCore import QTimer
from Qt.QtCore import QSize
from Qt.QtWidgets import QAbstractButton, QAbstractScrollArea, QComboBox, QDockWidget, QLayout, QSizePolicy, QWidget

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = REPO_ROOT / "src"
PACKAGE_NAME = "chimerax.codex_bridge"
IGNORE_SYNC_NAMES = {"__pycache__", ".DS_Store"}


def _dock_title(dock_widget):
    try:
        return str(dock_widget.windowTitle() or dock_widget.objectName() or "")
    except Exception:
        return ""


def _relax_dock_content_constraints(dock_widget):
    title = _dock_title(dock_widget).lower()
    aggressive = any(token in title for token in ("models", "model panel", "action pad"))
    root = dock_widget.widget()
    widgets = [dock_widget]
    if root is not None:
        widgets.append(root)
        try:
            widgets.extend(root.findChildren(QWidget))
        except Exception:
            pass

    for widget in tuple(dict.fromkeys(widgets)):
        protected_control = isinstance(widget, (QAbstractButton, QComboBox))
        try:
            widget.setMaximumWidth(16777215)
            if not protected_control:
                widget.setMinimumWidth(0)
            if protected_control:
                # Keep explicit button/combo heights; only prevent collapse.
                widget.setMinimumHeight(24)
            else:
                widget.setMinimumSize(0, 0)
                widget.setMinimumHeight(0)
                widget.setMaximumHeight(16777215)
        except Exception:
            pass

        if aggressive and not protected_control:
            try:
                policy = widget.sizePolicy()
                policy.setVerticalPolicy(QSizePolicy.Policy.Ignored)
                widget.setSizePolicy(policy)
            except Exception:
                pass

        try:
            layout = widget.layout()
            if layout is not None:
                layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        except Exception:
            pass

        if isinstance(widget, QAbstractScrollArea):
            try:
                widget.setMinimumViewportSize(QSize(0, 0))
                widget.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored)
            except Exception:
                pass
            try:
                widget.viewport().setMinimumSize(0, 0)
                widget.viewport().setMinimumHeight(0)
            except Exception:
                pass


def _release_all_dock_constraints(session):
    main_window = getattr(session.ui, "main_window", None)
    if main_window is None:
        return
    for dock_widget in main_window.findChildren(QDockWidget):
        try:
            dock_widget.setMinimumWidth(0)
            dock_widget.setMaximumWidth(16777215)
            dock_widget.setMinimumHeight(0)
            dock_widget.setMaximumHeight(16777215)
            _relax_dock_content_constraints(dock_widget)
        except Exception:
            pass


def _close_old_tools(session):
    for tool in list(session.tools.list()):
        if getattr(tool, "tool_name", "") in {"AI Assistant", "Action Pad", "Sequence Bar", "Display Controls", "Cavity Browser"}:
            try:
                tool.delete()
            except Exception as err:
                session.logger.warning(f"Could not delete {tool.tool_name}: {err}")


def _legacy_selected_chain_specs_from_session(session):
    specs = set()
    try:
        from chimerax.atomic import selected_residues

        for residue in selected_residues(session):
            structure = getattr(residue, "structure", None)
            model_id = getattr(structure, "id_string", None)
            chain_id = str(getattr(residue, "chain_id", "") or "").strip()
            if model_id and chain_id:
                specs.add(f"#{model_id}/{chain_id}")
    except Exception:
        pass
    try:
        from chimerax.atomic import selected_atoms

        for atom in selected_atoms(session):
            residue = getattr(atom, "residue", None)
            structure = getattr(residue, "structure", None)
            model_id = getattr(structure, "id_string", None)
            chain_id = str(getattr(residue, "chain_id", "") or "").strip()
            if model_id and chain_id:
                specs.add(f"#{model_id}/{chain_id}")
    except Exception:
        pass
    return specs


def _legacy_canonical_residue_key_from_spec(spec):
    spec = str(spec or "").strip()
    if not spec or ":" not in spec:
        return ""
    model_chain, number = spec.rsplit(":", 1)
    if "/" not in model_chain:
        return ""
    model_spec, chain_id = model_chain.rsplit("/", 1)
    return f"{model_spec}|{chain_id}|{number}"


def _legacy_residue_object_keys(residue):
    keys = set()
    if residue is None:
        return keys
    spec = str(getattr(residue, "atomspec", "") or "").strip()
    if spec:
        keys.add(spec)
        keys.add(_legacy_canonical_residue_key_from_spec(spec))
    structure = getattr(residue, "structure", None)
    model_id = getattr(structure, "id_string", None)
    chain_id = str(getattr(residue, "chain_id", "") or "").strip()
    number = str(getattr(residue, "number", "") or "").strip()
    if model_id and chain_id and number:
        keys.add(f"#{model_id}/{chain_id}:{number}")
        keys.add(f"#{model_id}|{chain_id}|{number}")
    return {key for key in keys if key}


def _legacy_selected_residue_keys_from_session(session):
    keys = set()
    try:
        from chimerax.atomic import selected_residues

        for residue in selected_residues(session):
            keys.update(_legacy_residue_object_keys(residue))
    except Exception:
        pass
    try:
        from chimerax.atomic import selected_atoms

        for atom in selected_atoms(session):
            keys.update(_legacy_residue_object_keys(getattr(atom, "residue", None)))
    except Exception:
        pass
    return keys


def _legacy_residue_spec_matches_keys(spec, selected_keys):
    spec = str(spec or "").strip()
    if not spec:
        return False
    return spec in selected_keys or _legacy_canonical_residue_key_from_spec(spec) in selected_keys


def _legacy_indices_to_ranges(indices):
    values = sorted(set(int(index) for index in indices or []))
    if not values:
        return []
    ranges = []
    start = previous = values[0]
    for index in values[1:]:
        if index == previous + 1:
            previous = index
            continue
        ranges.append((start, previous))
        start = previous = index
    ranges.append((start, previous))
    return ranges


def _legacy_alignment_selected_columns_from_session(session, payload):
    selected_keys = _legacy_selected_residue_keys_from_session(session)
    if not selected_keys:
        return {"reference": [], "moving": []}
    result = {}
    for row_name in ("reference", "moving"):
        side = payload.get(row_name, {}) if isinstance(payload, dict) else {}
        columns = []
        for column, spec in enumerate(side.get("column_map") or []):
            if _legacy_residue_spec_matches_keys(spec, selected_keys):
                columns.append(column)
        result[row_name] = _legacy_indices_to_ranges(columns)
    return result


def _legacy_first_alignment_selected_column(selected_columns):
    starts = []
    for ranges in (selected_columns or {}).values():
        starts.extend(start for start, _end in ranges or [])
    return min(starts) if starts else None


def _legacy_alignment_selected_status(payload, selected_columns):
    first_column = _legacy_first_alignment_selected_column(selected_columns)
    if first_column is None:
        return ""
    statuses = list(payload.get("display_statuses") or [])
    if not (0 <= first_column < len(statuses)):
        return ""
    total = sum(
        (end - start + 1)
        for ranges in (selected_columns or {}).values()
        for start, end in (ranges or [])
    )
    prefix = "Selected"
    if total > 1:
        prefix = f"Selected {total} alignment residue(s)"
    return f"{prefix} · {statuses[first_column]}"


LEGACY_SEQUENCE_BAR_GLOBALS = {
    "_selected_chain_specs_from_session": _legacy_selected_chain_specs_from_session,
    "_canonical_residue_key_from_spec": _legacy_canonical_residue_key_from_spec,
    "_residue_object_keys": _legacy_residue_object_keys,
    "_selected_residue_keys_from_session": _legacy_selected_residue_keys_from_session,
    "_residue_spec_matches_keys": _legacy_residue_spec_matches_keys,
    "_indices_to_ranges": _legacy_indices_to_ranges,
    "_alignment_selected_columns_from_session": _legacy_alignment_selected_columns_from_session,
    "_first_alignment_selected_column": _legacy_first_alignment_selected_column,
    "_alignment_selected_status": _legacy_alignment_selected_status,
}


def _patch_function_globals(function):
    target = getattr(function, "__func__", function)
    globals_dict = getattr(target, "__globals__", None)
    if isinstance(globals_dict, dict):
        for name, value in LEGACY_SEQUENCE_BAR_GLOBALS.items():
            globals_dict.setdefault(name, value)


def _patch_legacy_sequence_bar_callbacks(session):
    """Protect queued callbacks from an older Sequence Bar module during reload.

    ChimeraX can still run a QTimer-triggered refresh from an old tool instance
    after the repo files have changed.  If that old refresh references a helper
    added in a later edit, its stale module globals may not have it.
    """
    for name, module in list(sys.modules.items()):
        if name == f"{PACKAGE_NAME}.sequence_bar" or name.endswith(".sequence_bar"):
            for helper_name, helper in LEGACY_SEQUENCE_BAR_GLOBALS.items():
                module.__dict__.setdefault(helper_name, helper)
            for attr in ("refresh", "_render_alignment_panel", "_queue_refresh", "_set_current_entry_from_combo"):
                tool_class = module.__dict__.get("CodexSequenceBar")
                if tool_class is not None:
                    _patch_function_globals(getattr(tool_class, attr, None))

    candidates = []
    try:
        candidates.extend(session.tools.list())
    except Exception:
        pass
    try:
        for obj in gc.get_objects():
            try:
                if getattr(obj, "tool_name", "") == "Sequence Bar":
                    candidates.append(obj)
            except Exception:
                pass
    except Exception:
        pass

    seen = set()
    for tool in candidates:
        ident = id(tool)
        if ident in seen:
            continue
        seen.add(ident)
        for attr in ("refresh", "_render_alignment_panel", "_queue_refresh", "_set_current_entry_from_combo"):
            _patch_function_globals(getattr(tool, attr, None))
            _patch_function_globals(getattr(tool.__class__, attr, None))


def _drop_codex_modules():
    for name in list(sys.modules):
        if name == PACKAGE_NAME or name.startswith(PACKAGE_NAME + "."):
            del sys.modules[name]


def _installed_package_dirs():
    candidates = []
    app_support = Path.home() / "Library" / "Application Support" / "ChimeraX"
    if app_support.exists():
        candidates.extend(app_support.glob("*/lib/python/site-packages/chimerax/codex_bridge"))
    candidates.append(REPO_ROOT / "build" / "lib" / "chimerax" / "codex_bridge")

    seen = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except Exception:
            continue
        if resolved == SOURCE_DIR.resolve() or resolved in seen:
            continue
        if not candidate.exists():
            continue
        seen.add(resolved)
        yield candidate


def _sync_repo_source_to_installed_copies():
    synced = []
    for package_dir in _installed_package_dirs():
        for item in SOURCE_DIR.iterdir():
            if item.name in IGNORE_SYNC_NAMES:
                continue
            target = package_dir / item.name
            if item.is_dir():
                shutil.copytree(
                    item,
                    target,
                    dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(*IGNORE_SYNC_NAMES),
                )
            else:
                shutil.copy2(item, target)
        synced.append(str(package_dir))
    return synced


def _load_repo_package():
    init_path = SOURCE_DIR / "__init__.py"
    if not init_path.exists():
        session.logger.warning(f"Codex Bridge reload source not found: {init_path}")
        return
    spec = importlib.util.spec_from_file_location(
        PACKAGE_NAME,
        str(init_path),
        submodule_search_locations=[str(SOURCE_DIR)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE_NAME] = module
    spec.loader.exec_module(module)


_release_all_dock_constraints(session)
_patch_legacy_sequence_bar_callbacks(session)
_close_old_tools(session)
importlib.invalidate_caches()
synced_package_dirs = _sync_repo_source_to_installed_copies()
_patch_legacy_sequence_bar_callbacks(session)
_drop_codex_modules()
_load_repo_package()

from chimerax.codex_bridge import _apply_startup_layout, _install_runtime_toolbar_buttons
from chimerax.codex_bridge.pick_mode import install_pick_modes
from chimerax.codex_bridge.runtime_patches import apply_runtime_patches
from chimerax.codex_bridge.tool import CodexAssistant
from chimerax.codex_bridge.sequence_bar import CodexSequenceBar

apply_runtime_patches(session)
install_pick_modes(session)

assistant = CodexAssistant.get_singleton(session, create=True, display=True)
assistant.display(True)
assistant._show_assistant_tab()
assistant._focus_prompt()
try:
    assistant.prompt_edit.setReadOnly(False)
    assistant._append_system("reload ok: fresh AI Assistant instance")
    assistant._set_result_status("reload ok", tone="success")
    assistant._set_result_detail("Reloaded Codex Bridge UI. Buttons and prompts are ready.")
except Exception:
    pass

sequence_bar = CodexSequenceBar.get_singleton(session, create=True, display=True)
sequence_bar.display(True)
sequence_bar.refresh()

_apply_startup_layout(session, assistant)
_install_runtime_toolbar_buttons(session, force_rebuild=True)
for delay in (250, 800):
    QTimer.singleShot(
        delay,
        lambda ses=session, tool=assistant: (
            _release_all_dock_constraints(ses),
            _apply_startup_layout(ses, tool),
            _install_runtime_toolbar_buttons(ses),
        ),
    )
QTimer.singleShot(1200, lambda ses=session: _release_all_dock_constraints(ses))

session.logger.info(
    f"Reloaded Codex AI UI from {sys.modules['chimerax.codex_bridge.tool'].__file__}; "
    f"repo_source={SOURCE_DIR}; "
    f"synced_installed={synced_package_dirs}; "
    f"UI_LAYOUT_VERSION={CodexAssistant.UI_LAYOUT_VERSION}; "
    f"SEQUENCE_BAR_VERSION={CodexSequenceBar.UI_LAYOUT_VERSION}"
)
