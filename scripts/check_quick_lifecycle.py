"""Actual Qt destruction and queued quick actions, without visible windows."""
from concurrent.futures import CancelledError
import importlib.util
from pathlib import Path
import runpy
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

from Qt.QtCore import QTimer
from Qt.QtWidgets import QFileDialog
from PyQt6 import sip
from PyQt6.QtTest import QTest
from chimerax.atomic import AtomicStructure, check_for_changes, get_triggers
from chimerax.core.models import MODEL_POSITION_CHANGED, MODEL_NAME_CHANGED, MODEL_ID_CHANGED, REMOVE_MODELS

root = Path(__file__).resolve().parents[1]
app = runpy.run_path(str(root / "scripts/headless_ui_fixture.py"))["install"](session)
for name in tuple(sys.modules):
    if name == "chimerax.codex_bridge" or name.startswith("chimerax.codex_bridge."):
        del sys.modules[name]
spec = importlib.util.spec_from_file_location("chimerax.codex_bridge", root / "src/__init__.py",
    submodule_search_locations=[str(root / "src")])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
from chimerax.codex_bridge import quick_actions as actions
from chimerax.codex_bridge.quick_results import QuickResults

exceptions = []
original_hook = sys.excepthook
sys.excepthook = lambda kind, value, tb: exceptions.append(f"{kind.__name__}: {value}")


def wait(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(5)
    assert predicate(), "Asynchronous operation timed out"


def destroy(panel):
    host = panel.tool_window._dock_widget
    panel.delete()
    sip.delete(host)
    assert sip.isdeleted(panel.status)


def quick_threads():
    return {thread for thread in threading.enumerate() if thread.name.startswith("ChimeraX Quick ")}


def observer_counts():
    return (len(get_triggers().trigger_handlers("changes")),
            *(len(session.triggers.trigger_handlers(name)) for name in
              (MODEL_POSITION_CHANGED, MODEL_NAME_CHANGED, MODEL_ID_CHANGED, REMOVE_MODELS, "app quit")))


model = AtomicStructure(session, name="Lifecycle target")
for number in range(1, 4):
    residue = model.new_residue("ALA", "A", number)
    atom = model.new_atom("CA", "C")
    residue.add_atom(atom)
    atom.coord = (number * 3.8, 0, 0)
session.models.add([model])
check_for_changes(session)


class Backend:
    def __init__(self, delayed=False):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()
        self.applies = []
        if not delayed:
            self.release.set()

    def compute(self, snapshot, action, *, progress, cancelled):
        self.entered.set()
        try:
            progress("Working")
            while not self.release.wait(.005):
                if cancelled():
                    raise CancelledError()
            if cancelled():
                raise CancelledError()
            return {"title": "Lifecycle result", "summary": ["Two candidates"],
                    "candidates": [{"label": "Visible"}, {"label": "Hidden"}]}
        finally:
            self.finished.set()

    def apply(self, session, result, context, candidate=0):
        self.applies.append(candidate)
        model.display = candidate == 0


baseline_threads = quick_threads()
baseline_observers = observer_counts()
failures = []

# A closing dock must cancel its delayed fit work as well as destroying widgets.
# The queued callbacks previously captured the main window and resized it later.
window = session.ui.main_window
window.resize(600, 400)
panel = QuickResults(session, "Quick Results")

# A targeted Quick reload can coexist with an older in-memory bookmark class.
# Prefer the new reveal method, and retain accessible export for the old API.
from chimerax.codex_bridge.camera_bookmarks import CameraBookmarks
modern = SimpleNamespace(show_image_export=Mock())
with patch.object(CameraBookmarks, "get_singleton", return_value=modern):
    panel._export()
modern.show_image_export.assert_called_once_with()
button = object()
group = SimpleNamespace(height=lambda: 200)
legacy = SimpleNamespace(display=Mock(),
    tool_window=SimpleNamespace(_dock_widget=SimpleNamespace(raise_=Mock())),
    export_group=group, export_button=button,
    scroll_area=SimpleNamespace(viewport=lambda: SimpleNamespace(height=lambda: 224),
                                ensureWidgetVisible=Mock()))
with patch.object(CameraBookmarks, "get_singleton", return_value=legacy):
    panel._export()
    legacy.display.assert_called_once_with(True)
    legacy.tool_window._dock_widget.raise_.assert_called_once_with()
    legacy.scroll_area.ensureWidgetVisible.assert_called_once_with(group, 0, 6)
    legacy.scroll_area.viewport = lambda: SimpleNamespace(height=lambda: 128)
    panel._export()
    legacy.scroll_area.ensureWidgetVisible.assert_called_with(button, 0, 6)

window.resize(900, 900)
size_after_close = window.size()
destroy(panel)
QTest.qWait(330)
if window.size() != size_after_close:
    failures.append("A deleted results panel still resized the main window")

# Close a tool while worker progress/completion and scientific observers are queued.
# Reopen before the queued events run: old callbacks must not update the new panel.
control = actions.QuickController(session)
backend = Backend(delayed=True)
posted = []
with patch.object(actions, "backend_for", return_value=backend), \
        patch.object(session.ui, "thread_safe", side_effect=posted.append):
    job = control.start("analyze", model.atomspec)
    wait(backend.entered.is_set)
    wait(lambda: bool(posted))
    old_panel = control.panel
    control._queue_validation()
    destroy(old_panel)
    assert job.cancel.is_set() and job.done.is_set()
    replacement = control.show_panel()
    assert replacement is not old_panel
    status_before = replacement.status.text()
    backend.release.set()
    wait(backend.finished.is_set)
    for callback in tuple(posted):
        callback()
    QTest.qWait(60)
    assert replacement.status.text() == status_before
    assert backend.applies == [], "Closed tool applied a late computation"
    assert model.display

# Pending cache hits use the UI event queue too, and cannot apply after deletion.
ready = Backend()
with patch.object(actions, "backend_for", return_value=ready):
    completed = control.start("analyze", model.atomspec)
    wait(completed.done.is_set)
    assert completed.status == "done", completed.error
    assert ready.applies == [0]
    cached = control.start("analyze", model.atomspec)
    assert cached.cached and not cached.done.is_set()
    destroy(control.panel)
    QTest.qWait(60)
    assert cached.status == "cancelled" and ready.applies == [0]

# Completed results reopen at the previous candidate without applying again.
with patch.object(actions, "backend_for", return_value=ready):
    completed = control.start("analyze", model.atomspec)
    wait(completed.done.is_set)
    control.apply_candidate(1)
    assert completed.candidate == 1 and not model.display
    before_reopen = list(ready.applies)
    destroy(control.panel)
    reopened = control.show_panel()
    assert reopened.job is completed and reopened.candidates.currentRow() == 1
    assert "Hidden" in reopened.details.toPlainText()
    assert ready.applies == before_reopen and not model.display
    sip.delete(reopened.tool_window._dock_widget)
    replaced_completed = control.show_panel()
    assert replaced_completed is not reopened and replaced_completed.job is completed
    assert replaced_completed.candidates.currentRow() == 1
    assert ready.applies == before_reopen and not model.display
    control.undo()
    assert model.display
    destroy(replaced_completed)

# A native dialog runs a nested event loop. Its parent can be destroyed before
# the accepted filename returns; the frozen report can save without widget use.
with patch.object(actions, "backend_for", return_value=ready):
    completed = control.start("analyze", model.atomspec)
    wait(completed.done.is_set)
    panel = control.panel
    with tempfile.TemporaryDirectory(prefix="quick-lifecycle-report-") as folder:
        path = Path(folder) / "closed-panel.json"
        def close_during_dialog(*args, **kwargs):
            destroy(panel)
            return str(path), ""
        with patch.object(QFileDialog, "getSaveFileName", side_effect=close_during_dialog):
            panel._save_report("json")
        assert path.is_file() and '"target"' in path.read_text(encoding="utf-8")

# Repeated X-close/reopen uses the same session controller and bounded workers;
# native deletion may arrive independently of ToolInstance.delete during teardown.
for _ in range(6):
    replacement = control.show_panel()
    destroy(replacement)
    QTest.qWait(2)
assert len(quick_threads() - baseline_threads) == 2

# Actions queued before app quit/controller replacement are inert after close.
# Test actual scene state, not just absence of an exception.
with patch.object(actions, "backend_for", return_value=ready):
    completed = control.start("analyze", model.atomspec)
    wait(completed.done.is_set)
    panel = control.panel
    applies_before = list(ready.applies)
    before = model.display
    queued_progress = []
    with patch.object(session.ui, "thread_safe", side_effect=queued_progress.append):
        control._post(lambda: control._progress(completed, "Late progress"))
    QTimer.singleShot(0, lambda: control.apply_candidate(1))
    QTimer.singleShot(0, control.undo)
    control._queue_validation()
    control.close()
    sip.delete(panel.tool_window._dock_widget)
    deleted_item = panel.candidates.item(0)
    for callback in (lambda: panel._candidate_changed(deleted_item, None),
                     lambda: panel._step_candidate(1),
                     lambda: panel._filter_candidates("late"),
                     lambda: panel._details_changed(True),
                     panel._copy_report, panel._export,
                     lambda: panel._save_report("json")):
        QTimer.singleShot(0, callback)
    for callback in queued_progress:
        try:
            callback()
        except RuntimeError as error:
            exceptions.append(str(error))
    QTest.qWait(80)
    if ready.applies != applies_before or model.display != before:
        failures.append("A queued action changed the scene after controller.close")
    # Tool cleanup must still work if native destruction preceded logical close.
    try:
        panel.delete()
        panel.delete()
    except RuntimeError as error:
        exceptions.append(str(error))

wait(lambda: not (quick_threads() - baseline_threads))
assert control._handlers == [] and control._quit_handler is None
assert observer_counts() == baseline_observers, "Native trigger registry retained closed controller handlers"

# Native destruction can precede ToolInstance.delete during app/session teardown.
# Repeat whole-controller creation and close with actual workers still computing.
from chimerax.ui.gui import MainWindow
window.tool_instance_to_windows = {}
window._hide_tools_shown_states = {}
window._hide_floating_tools_shown_states = {}
window._tool_window_destroyed = lambda tool: MainWindow._tool_window_destroyed(window, tool)
for _ in range(3):
    pending_controller = actions.QuickController(session)
    slow = Backend(delayed=True)
    with patch.object(actions, "backend_for", return_value=slow):
        pending = pending_controller.start("analyze", model.atomspec)
    wait(slow.entered.is_set)
    panel = pending_controller.panel
    native_window = panel.tool_window
    native_window.tool_instance = panel
    # Exercise the actual native registry cleanup without a visible main window.
    window.tool_instance_to_windows[panel] = [native_window]
    window._hide_tools_shown_states[native_window] = True
    window._hide_floating_tools_shown_states[native_window] = True
    sip.delete(panel.tool_window._dock_widget)
    assert pending.cancel.is_set() and pending.done.is_set()
    assert pending_controller.panel is None
    replacement = pending_controller.show_panel()
    assert replacement is not panel and not sip.isdeleted(replacement.status)
    assert panel not in session.tools.list()
    assert panel not in window.tool_instance_to_windows
    assert native_window not in window._hide_tools_shown_states
    assert native_window not in window._hide_floating_tools_shown_states
    assert QuickResults.get_singleton(session, create=False) is replacement
    # A late logical delete from the old host must not remove its replacement.
    panel.delete()
    assert replacement in session.tools.list()
    pending_controller.close()
    destroy(replacement)
    wait(slow.finished.is_set)
    wait(lambda: not (quick_threads() - baseline_threads))
    assert not slow.applies and model.display
    assert observer_counts() == baseline_observers

# Pending fit callbacks must never address an already-destroyed native window.
last_panel = QuickResults(session, "Quick Results")
sip.delete(last_panel.tool_window._dock_widget)
assert QuickResults.get_singleton(session, create=False) is None
assert last_panel not in session.tools.list()
sip.delete(window)
QTest.qWait(330)
sys.excepthook = original_hook
if exceptions:
    failures.append("Deleted Qt callbacks: " + "; ".join(exceptions))
assert not failures, "\n".join(failures)
print("QUICK_LIFECYCLE_OK: native destruction, delayed fits, queued work, cached completion, "
      "six reopen cycles, three active-worker shutdowns, closed-action isolation and released workers/observers")
