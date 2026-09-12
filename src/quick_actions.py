"""Responsive, context-bound execution for the six quick toolbar buttons."""
from concurrent.futures import CancelledError
from dataclasses import dataclass, field
import importlib
import queue
import threading
import time
from numbers import Integral

from .quick_context import capture_context, context_valid, VisualState, capture_selection, restore_selection
from .quick_cache import ResultCache, copy_result, copy_preview


PROVIDERS = {"ai-quick-analyze": "analyze", "ai-quick-view": "view",
             "ai-quick-site": "pocket", "ai-quick-cavity": "cavity",
             "ai-quick-figure": "figure", "ai-quick-triad-zoom": "zoom"}


def backend_for(action):
    module = "quick_analyze" if action == "analyze" else "quick_pockets" if action in ("pocket", "cavity") else "quick_views"
    return importlib.import_module(f".{module}", __package__)


@dataclass
class QuickJob:
    action: str
    context: dict
    module: object
    key: tuple
    cancel: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    started: float = field(default_factory=time.perf_counter)
    status: str = "running"
    result: dict = None
    error: str = ""
    cached: bool = False
    elapsed: float = 0.0
    compute_seconds: float = 0.0
    candidate: int = 0


class QuickController:
    def __init__(self, session):
        self.session = session
        self.active = None
        self.latest = None
        self.panel = None
        self.cache = ResultCache()
        self.undo_states = []
        self.overlay_visible = True
        self._current_overlays = ()
        self._preview_job = None
        self._validation_pending = False
        self._handlers = []
        self.closed = False
        self._queue = queue.Queue(maxsize=1)
        self.compute_count = 0
        self.cache_hits = 0
        self._normal_highlight_width = None
        for index in range(2):
            threading.Thread(target=self._worker, name=f"ChimeraX Quick {index + 1}", daemon=True).start()
        try:
            self._quit_handler = session.triggers.add_handler("app quit", lambda *_: self.close())
        except Exception:
            self._quit_handler = None
        self._observe_changes()

    def _observe_changes(self):
        from chimerax.atomic import get_triggers
        from chimerax.core.models import MODEL_POSITION_CHANGED, MODEL_NAME_CHANGED, MODEL_ID_CHANGED, REMOVE_MODELS
        self._handlers.append(get_triggers().add_handler("changes", self._atomic_changed))
        for name in (MODEL_POSITION_CHANGED, MODEL_NAME_CHANGED, MODEL_ID_CHANGED, REMOVE_MODELS):
            self._handlers.append(self.session.triggers.add_handler(name, self._model_changed))

    def _atomic_changed(self, _name, changes):
        # These are native ChangeTracker reason strings. Style/selection edits do
        # not need a fingerprint scan, even for very large structures.
        scientific = {
            "atom": {"coord changed", "alt_loc changed", "element changed", "name changed",
                     "bfactor changed", "occupancy changed", "structure_category changed"},
            "residue": {"name changed", "chain_id changed", "number changed",
                        "insertion_code changed", "ss_type changed", "polymer_type changed"},
            "atomic_structure": {"active_coordset changed", "scene_coord changed"},
            "coordset": {"coord changed"},
        }
        for kind, reasons in scientific.items():
            if reasons.intersection(getattr(changes, kind + "_reasons")()):
                self._queue_validation()
                return
        for kind in ("atoms", "bonds", "residues", "chains", "coordsets"):
            if getattr(changes, "num_deleted_" + kind)() or len(getattr(changes, "created_" + kind)()):
                self._queue_validation()
                return

    def _model_changed(self, _name, changed):
        changed = changed if isinstance(changed, (list, tuple, set)) else (changed,)
        jobs = [job for job in (self.active, self.latest) if job is not None]
        targets = {model for job in jobs for model in job.context["models"]}
        targets.update(model for state in self.undo_states for model in state.targets)
        # A parent transform changes each descendant's scene coordinates.
        for target in targets:
            parent = target
            while parent is not None:
                if parent in changed:
                    self._queue_validation()
                    return
                parent = getattr(parent, "parent", None)

    def _queue_validation(self):
        if self.closed or self._validation_pending:
            return
        if self.active is None and self.latest is None and not self.undo_states:
            return
        self._validation_pending = True
        from Qt.QtCore import QTimer
        QTimer.singleShot(40, self._validate_observed)

    def _validate_observed(self):
        self._validation_pending = False
        if self.closed:
            return
        validity = {}
        def valid(context):
            key = (tuple(id(model) for model in context["models"]), context["scientific_signature"])
            if key not in validity:
                validity[key] = context_valid(self.session, context, check_selection=False)
            return validity[key]
        jobs = list({id(job): job for job in (self.active, self.latest) if job is not None}.values())
        for job in jobs:
            if job.status != "stale" and not valid(job.context):
                self._mark_stale(job, "Structure changed. Recalculate before using these results.")
        retained = []
        for state in self.undo_states:
            if valid(state.job.context):
                retained.append(state)
            else:
                self._hide_target_overlays(state.targets, state.scientific_signature)
        self.undo_states = retained
        if self.panel is not None:
            self.panel.undo_button.setEnabled(bool(self.undo_states))

    def _hide_target_overlays(self, targets, scientific_signature=None):
        ids = {id(model) for model in targets}
        for model in self.session.models.list():
            if (getattr(model, "_codex_quick_overlay", False)
                    and ids.intersection(getattr(model, "_codex_quick_targets", ()))
                    and (scientific_signature is None
                         or getattr(model, "_codex_quick_signature", scientific_signature) == scientific_signature)):
                model.display = False

    def _mark_stale(self, job, message):
        job.status = "stale"
        job.cancel.set()
        job.done.set()
        self._hide_target_overlays(job.context["models"], job.context["scientific_signature"])
        if self._preview_job is job:
            self._current_overlays = ()
            self._preview_job = None
        if self.panel is not None and (job is self.active or job is self.latest):
            show_stale = getattr(self.panel, "show_stale", None)
            if show_stale is not None:
                show_stale(message)
            else:
                self.panel.show_error(message)

    def set_overlay_visibility(self, visible):
        """Toggle only the currently generated preview, never a user's models."""
        if self.closed:
            return
        self.overlay_visible = bool(visible)
        job = self._preview_job
        if job is None:
            return
        if job.status == "stale" or not context_valid(self.session, job.context, check_selection=False):
            self._mark_stale(job, "Structure changed. Recalculate before showing the preview.")
            return
        live = set(self.session.models.list())
        self._current_overlays = tuple(model for model in self._current_overlays
                                       if model in live and not model.deleted)
        for model in self._current_overlays:
            model.display = self.overlay_visible

    def has_preview_overlays(self):
        live = set(self.session.models.list())
        return bool(self._preview_job is self.latest and self.latest is not None
                    and self.latest.status == "done"
                    and any(model in live and not model.deleted for model in self._current_overlays))

    def _post(self, callback):
        if not self.closed:
            self.session.ui.thread_safe(lambda: None if self.closed else callback())

    def show_panel(self):
        if self.closed:
            return None
        from .quick_results import QuickResults
        previous = self.panel
        self.panel = QuickResults.get_singleton(self.session)
        self.panel.controller = self
        if (self.panel is not previous and self.latest is not None
                and self.latest.result is not None
                and self.latest.status in ("done", "stale")):
            self.panel.show_result(self.latest)
            if self.latest.status == "stale":
                self.panel.show_stale("Structure changed. Recalculate before using these results.")
        self.panel.display(True)
        self.panel.tool_window._dock_widget.raise_()
        return self.panel

    def start(self, action, model_hint=None, *, force=False):
        if self.closed:
            return None
        try:
            if action not in PROVIDERS.values():
                raise ValueError(f"Unknown quick action: {action}")
            context = capture_context(self.session, model_hint)
            module = backend_for(action)
        except Exception as error:
            self.cancel_active()
            self.active = self.latest = None
            self._hide_target_overlays(self._preview_job.context["models"] if self._preview_job else ())
            self._preview_job, self._current_overlays = None, ()
            panel = self.show_panel()
            panel.job = None
            panel.show_error(str(error))
            self.session.logger.warning(str(error))
            return None
        key = (action, context["snapshot"]["signature"])
        if not force and self.active is not None and not self.active.done.is_set() and self.active.key == key:
            self.show_panel().show_progress(f"{action.title()} is already running for this target.")
            return self.active
        self.cancel_active()
        self.latest = None
        job = QuickJob(action, context, module, key)
        self.active = job
        self.session.logger.status(f"{action.title()}: {context['snapshot']['target_label']}")
        self.show_panel().show_running(job)
        if not force and key in self.cache:
            self.cache_hits += 1
            job.cached = True
            job.result = self.cache[key]
            from Qt.QtCore import QTimer
            QTimer.singleShot(0, lambda: self._complete(job))
            return job
        while True:
            try:
                self._queue.put_nowait(job)
                break
            except queue.Full:
                try:
                    superseded = self._queue.get_nowait()
                except queue.Empty:
                    continue
                superseded.cancel.set()
                if superseded.status != "stale":
                    superseded.status = "cancelled"
                superseded.done.set()
        return job

    def _worker(self):
        while not self.closed:
            try:
                job = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if job.cancel.is_set():
                if job.status != "stale":
                    job.status = "cancelled"
                job.done.set()
                continue
            try:
                started = time.perf_counter()
                self.compute_count += 1
                def progress(message, current=job):
                    self._post(lambda: self._progress(current, str(message)))
                job.result = job.module.compute(job.context["snapshot"], job.action,
                                                progress=progress, cancelled=job.cancel.is_set)
                job.compute_seconds = time.perf_counter() - started
                if job.cancel.is_set():
                    raise CancelledError()
            except (CancelledError, InterruptedError):
                if job.status != "stale":
                    job.status = "cancelled"
            except Exception as error:
                if job.status != "stale":
                    job.status = "error"
                    job.error = str(error) or error.__class__.__name__
            self._post(lambda current=job: self._complete(current))

    def _progress(self, job, message):
        if not self.closed and job is self.active and not job.cancel.is_set() and self.panel is not None:
            self.panel.show_progress(message)

    def _complete(self, job):
        if self.closed or job is not self.active or job.cancel.is_set():
            if job.status != "stale":
                job.status = "cancelled"
            job.done.set()
            return
        try:
            if job.status == "cancelled":
                self.panel.show_error("Cancelled. The scene was left unchanged.")
                return
            if job.status == "error":
                self.panel.show_error(f"{job.action.title()} failed: {job.error}")
                self.session.logger.warning(f"{job.action.title()}: {job.error}")
                return
            if not context_valid(self.session, job.context):
                self._mark_stale(job, "The target or structure changed during calculation. Run again for the current view.")
                return
            if not isinstance(job.result, dict):
                raise TypeError("Quick action returned an invalid result")
            if not job.cached:
                self.cache[job.key] = job.result
                job.result = (self.cache[job.key] if job.key in self.cache
                              else copy_result(job.result, readonly=True))
            self._apply(job, 0)
            self.latest = job
            job.status = "done"
            job.elapsed = time.perf_counter() - job.started
            self.panel.show_result(job)
            self.session.logger.status(f"{job.action.title()} ready · {job.elapsed:.2f} s" + (" · cached" if job.cached else ""))
            summary = "\n".join(str(text) for text in job.result.get("summary", ()))
            self.session.logger.info(f"{job.result.get('title', job.action.title())}\n{summary}")
        except Exception as error:
            job.status = "error"
            job.error = str(error) or error.__class__.__name__
            self.panel.show_error(f"{job.action.title()} failed: {job.error}")
            self.session.logger.warning(job.error)
        finally:
            job.elapsed = time.perf_counter() - job.started
            job.done.set()

    def _apply(self, job, candidate):
        saved_selection = capture_selection(self.session)
        state = VisualState(self.session, job.context)
        state.job = job
        state.previous_candidate = job.candidate if self.latest is job else -1
        state.previous_preview = (self._preview_job, self._current_overlays)
        try:
            view = self.session.main_view
            if job.action == "figure" and view.highlight_thickness > 0:
                self._normal_highlight_width = view.highlight_thickness
            elif job.action != "figure" and view.highlight_thickness == 0 and self._normal_highlight_width is not None:
                view.highlight_thickness = self._normal_highlight_width
            targets = {id(model) for model in job.context["models"]}
            for model in self.session.models.list():
                if getattr(model, "_codex_quick_overlay", False) and targets.intersection(getattr(model, "_codex_quick_targets", ())):
                    model.display = False
            # Native surfaces keep vertex/triangle arrays by reference. A writable
            # renderer copy also keeps backend edits out of cached report data.
            job.module.apply(self.session, copy_preview(job.result, candidate), job.context, candidate=candidate)
            state.finish(self.session, job.context)
        except Exception:
            state.finish(self.session, job.context)
            state.restore(self.session)
            raise
        finally:
            restore_selection(self.session, saved_selection)
        self._preview_job = job
        self._current_overlays = tuple(state.created)
        if not self.overlay_visible:
            for model in self._current_overlays:
                model.display = False
        job.candidate = candidate
        self.undo_states.append(state)
        if len(self.undo_states) > 3:
            self.undo_states.pop(0)
        protected = {record["model"] for entry in self.undo_states for record in entry.records
                     if record["attributes"].get("display", False)}
        protected.update(self._current_overlays)
        obsolete = [m for m in self.session.models.list() if getattr(m, "_codex_quick_overlay", False)
                    and not m.display and not m.selected and m not in protected]
        if obsolete:
            self.session.models.close(obsolete)
            obsolete = set(obsolete)
            for entry in self.undo_states:
                entry.before_models.difference_update(obsolete)
                entry.records = [record for record in entry.records if record["model"] not in obsolete]
                entry.created = [m for m in entry.created if m not in obsolete]

    def apply_candidate(self, index):
        if self.closed or self.panel is None:
            return
        job = self.latest
        if job is None:
            return
        if self.active is not None and not self.active.done.is_set():
            return
        if job.status != "done" or not context_valid(self.session, job.context, check_selection=False):
            self._mark_stale(job, "The structure changed. Run the calculation again before choosing a candidate.")
            return
        if (not isinstance(index, Integral) or isinstance(index, bool)
                or not 0 <= index < len(job.result.get("candidates", ()))):
            self.panel.show_error("Choose a valid candidate from the current results.")
            return
        index = int(index)
        if job.candidate == index:
            self.panel.show_candidate_evidence(job.result, index)
            return
        try:
            self._apply(job, index)
            self.panel.show_candidate_evidence(job.result, index)
        except Exception as error:
            self.panel.show_error(str(error))

    def cancel_active(self):
        if self.active is not None and not self.active.done.is_set():
            self.active.cancel.set()
            self.active.status = "cancelled"
            self.active.done.set()
            if self.panel is not None:
                self.panel.show_error("Cancelled. The scene was left unchanged.")

    def undo(self):
        if self.closed or not self.undo_states:
            return
        self.cancel_active()
        self._validate_observed()
        if not self.undo_states:
            return
        state = self.undo_states.pop()
        if not state.restore(self.session):
            return
        if self.latest is not None:
            self.latest.candidate = state.previous_candidate if self.latest is state.job else -1
        preview_job, overlays = state.previous_preview
        live = set(self.session.models.list())
        self._preview_job = preview_job if preview_job is self.latest else None
        self._current_overlays = tuple(model for model in overlays if model in live) if self._preview_job else ()
        if self.panel is not None:
            if self.latest is not None:
                self.panel.candidates.setCurrentRow(self.latest.candidate)
                self.panel.show_candidate_evidence(self.latest.result, self.latest.candidate)
            self.panel.show_progress("Previous appearance restored.")
            self.panel.undo_button.setEnabled(bool(self.undo_states))

    def close(self):
        if self.closed:
            return
        self.closed = True
        panel, self.panel = self.panel, None
        if panel is not None:
            panel.controller = None
        self.cancel_active()
        self._validation_pending = False
        # Superseded work must not retain snapshots while a worker is exiting.
        while True:
            try:
                queued = self._queue.get_nowait()
            except queue.Empty:
                break
            queued.cancel.set()
            if queued.status != "stale":
                queued.status = "cancelled"
            queued.done.set()
        for handler in self._handlers:
            handler.remove()
        self._handlers.clear()
        if self._quit_handler is not None:
            self._quit_handler.remove()
            self._quit_handler = None
        self.cache.clear()


def controller(session):
    current = getattr(session, "_codex_quick_controller", None)
    if current is None or current.closed:
        session._codex_quick_controller = current = QuickController(session)
    return current


def run_quick_action(session, action, model_hint=None):
    return controller(session).start(PROVIDERS.get(action, action), model_hint=model_hint)
