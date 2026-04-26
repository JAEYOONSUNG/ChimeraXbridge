import importlib
import importlib.util
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
        if getattr(tool, "tool_name", "") in {"AI Assistant", "Action Pad", "Sequence Bar", "Display Controls"}:
            try:
                tool.delete()
            except Exception as err:
                session.logger.warning(f"Could not delete {tool.tool_name}: {err}")


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
_close_old_tools(session)
importlib.invalidate_caches()
synced_package_dirs = _sync_repo_source_to_installed_copies()
_drop_codex_modules()
_load_repo_package()

from chimerax.codex_bridge import _apply_startup_layout, _install_runtime_toolbar_buttons
from chimerax.codex_bridge.tool import CodexAssistant
from chimerax.codex_bridge.sequence_bar import CodexSequenceBar

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
