"""Native Qt runtime regression: targets, responsiveness, cache, cancel and undo."""
import importlib.util
import json
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
from concurrent.futures import CancelledError
from unittest.mock import patch

import numpy as np
from Qt.QtCore import QTimer
from Qt.QtWidgets import QApplication
from PyQt6.QtTest import QTest
from chimerax.atomic import AtomicStructure, Atoms, selected_atoms, selected_bonds, check_for_changes
from chimerax.core.models import Surface
from chimerax.geometry import translation

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("chimerax.codex_bridge", root / "src/__init__.py",
    submodule_search_locations=[str(root / "src")])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
from chimerax.codex_bridge import quick_actions as actions
from chimerax.codex_bridge.quick_context import capture_context, context_valid, capture_selection
from chimerax.codex_bridge.quick_cache import ResultCache, retained_bytes, copy_preview

app = QApplication.instance()
QTest.qWait(5500)

# Cache quotas include Python containers and array backing stores, not only entry
# count. Repeated references/cycles cannot inflate accounting or recurse forever.
array = np.arange(1024, dtype=np.float32)
shared = {"a": array, "b": array[4:], "c": array}
assert retained_bytes(shared) < array.nbytes * 2
cycle = [array]
cycle.append(cycle)
tiny_cache = ResultCache(max_bytes=11000, max_entries=2)
tiny_cache["a"] = {"array": array, "cycle": cycle, "labels": ["original"]}
stored = tiny_cache["a"]
array[0] = 999
assert stored["array"][0] == 0 and stored["cycle"][1] is stored["cycle"]
stored["labels"][0] = "caller mutation"
assert tiny_cache["a"]["labels"] == ["original"]
try:
    stored["array"].flags.writeable = True
except ValueError:
    pass
else:
    raise AssertionError("A cache reader enabled writes to shared array storage")
tiny_cache["b"] = {"array": np.zeros(1024, np.float32)}
assert list(tiny_cache) == ["a", "b"]
tiny_cache["a"]
tiny_cache["c"] = {"array": np.zeros(1024, np.float32)}
assert list(tiny_cache) == ["a", "c"] and "b" not in tiny_cache
assert [key for key, value in tiny_cache.items()] == ["a", "c"]
assert tiny_cache.bytes_used <= tiny_cache.max_bytes
tiny_cache["huge"] = {"array": np.zeros(16000, np.float32)}
assert "huge" not in tiny_cache and len(tiny_cache) == 2
del tiny_cache["a"]
assert tiny_cache.bytes_used > 0
tiny_cache.clear()
assert not tiny_cache and tiny_cache.bytes_used == 0
tiny_cache["preview"] = {"candidates": [{"mesh": np.zeros(20, np.float32)},
                                         {"mesh": np.ones(20, np.float32)}]}
frozen = tiny_cache["preview"]
preview = copy_preview(frozen, 1)
assert np.shares_memory(preview["candidates"][0]["mesh"], frozen["candidates"][0]["mesh"])
assert not preview["candidates"][0]["mesh"].flags.writeable
assert not np.shares_memory(preview["candidates"][1]["mesh"], frozen["candidates"][1]["mesh"])
assert preview["candidates"][1]["mesh"].flags.writeable


def model(name, shift):
    structure = AtomicStructure(session, name=name)
    for i in range(4):
        residue = structure.new_residue("ALA", "A", i + 1)
        atom = structure.new_atom("CA", "C")
        atom.coord = (shift + i * 3.8, 0, 0)
        residue.add_atom(atom)
    structure.new_bond(structure.atoms[0], structure.atoms[1])
    session.models.add([structure])
    return structure


first = model("first <target>", 0)
second = model("second", 30)
first.atoms.colors = (20, 60, 140, 160)
second.atoms.colors = (90, 110, 130, 255)
surface = Surface("unrelated selected surface", session)
surface.set_geometry(np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], np.float32), None,
                     np.array([[0, 1, 2]], np.int32))
session.models.add([surface])
first.atoms[0].selected = True
first.bonds[0].selected = True
surface.selected = True
context = capture_context(session)
assert context["models"] == (first,), "Atomic selection must choose the target"
assert context["snapshot"]["models"][0]["coords"].flags.writeable is False
assert context_valid(session, context)
from datetime import datetime
assert datetime.fromisoformat(context["snapshot"]["captured_at"]).tzinfo is not None
first.atoms[0].coord = (1, 0, 0)
assert not context_valid(session, context), "Coordinate edits must invalidate results"
first.atoms[0].coord = (0, 0, 0)

calls = []
main_thread = threading.get_ident()


def compute(snapshot, action, progress=None, cancelled=None):
    assert threading.get_ident() != main_thread, "Compute ran on GUI thread"
    assert all(not value["coords"].flags.writeable for value in snapshot["models"])
    calls.append(snapshot["signature"])
    for _ in range(35):
        if cancelled():
            raise CancelledError()
        time.sleep(0.005)
    progress("Fixture geometry finished")
    return {"title": "Runtime fixture", "summary": ["<script>plain evidence</script>"],
            "mesh": {"vertices": np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], np.float32),
                     "triangles": np.array([[0, 1, 2]], np.int32)},
            "details": ["Measured fixture result"], "metrics": [{"label": "Atoms", "value": "4"}],
            "candidates": [{"label": "A", "specs": [], "evidence": ["First"]},
                           {"label": "B", "specs": [], "evidence": ["Second"]}]}


def apply(session, result, context, candidate=0):
    assert threading.get_ident() == main_thread, "Apply left the GUI thread"
    context["models"][0].atoms.colors = (200, 100, 50 + candidate, 255)
    overlay = Surface("Quick fixture overlay", session)
    overlay.set_geometry(result["mesh"]["vertices"], None, result["mesh"]["triangles"])
    overlay._codex_quick_overlay = True
    session.models.add([overlay])


original_backend = actions.backend_for
actions.backend_for = lambda action: SimpleNamespace(compute=compute, apply=apply)
controller = actions.controller(session)


def wait(job, timeout=10):
    start = time.monotonic()
    while not job.done.is_set() and time.monotonic() - start < timeout:
        QTest.qWait(5)
    assert job.done.is_set(), (job.action, job.status, "timed out")
    return job


heartbeats = []
timer = QTimer()
timer.timeout.connect(lambda: heartbeats.append(time.perf_counter()))
timer.start(10)
before_colors = first.atoms.colors.copy()
before_selection = capture_selection(session)["signature"]
start = time.perf_counter()
job = controller.start("analyze")
returned_in = time.perf_counter() - start
assert controller.start("analyze") is job, "Duplicate click started another calculation"
wait(job)
assert job.status == "done", job.error
assert len(heartbeats) >= 8, "GUI heartbeat stopped during calculation"
assert capture_selection(session)["signature"] == before_selection, "Selection types were lost"
assert "<script>plain evidence</script>" in controller.panel.summary.toPlainText()
timer.stop()

# Undo must not rewind later edits to an unrelated model.
second.position = translation((5, 6, 7))
controller.undo()
np.testing.assert_array_equal(first.atoms.colors, before_colors)
np.testing.assert_array_equal(second.position.matrix[:, 3], [5, 6, 7])
assert capture_selection(session)["signature"] == before_selection
assert not any(getattr(m, "_codex_quick_overlay", False) for m in session.models.list())

cached = wait(controller.start("analyze"))
assert cached.cached and len(calls) == 1, "Unchanged input was recalculated"
first.atoms[1].coord += np.array((0.1, 0, 0))
changed = wait(controller.start("analyze"))
assert not changed.cached and len(calls) == 2, "Changed coordinates reused stale results"

# Candidate repeats must not accumulate duplicate overlays / unbounded Undo.
for i in range(14):
    controller.apply_candidate((i + 1) % 2)
count = sum(bool(getattr(m, "_codex_quick_overlay", False)) for m in session.models.list())
assert count <= 4, ("Unbounded managed overlays", count)
depth = len(controller.undo_states)
controller.apply_candidate(controller.latest.candidate)
assert len(controller.undo_states) == depth

first.atoms[2].coord += np.array((0.1, 0, 0))
cancelled = controller.start("analyze")
colors = first.atoms.colors.copy()
controller.cancel_active()
QTest.qWait(250)
assert cancelled.status == "cancelled"
np.testing.assert_array_equal(first.atoms.colors, colors)
assert not controller.panel.copy_button.isEnabled(), "Cancelled result copied old target data"

first.atoms[2].coord += np.array((0.1, 0, 0))
stale = controller.start("analyze")
session.selection.clear()
second.atoms[0].selected = True
wait(stale)
assert stale.status == "stale", stale.status
np.testing.assert_array_equal(first.atoms.colors, colors)

# Closure and reuse of a model ID must not target the replacement object.
closing = controller.start("analyze")
second_id = second.id
session.models.close([second])
replacement = model("replacement", 50)
wait(closing)
assert closing.status == "stale"

# A burst of superseding requests must stay bounded and not throw queue races.
session.selection.clear()
first.atoms[0].selected = True
for i in range(20):
    first.atoms[3].coord = (11.4, i * 0.01, 0)
    latest = controller.start("analyze")
wait(latest)
assert latest.status == "done", latest.error
assert controller._queue.qsize() <= 1
assert len(controller.undo_states) <= 3

# Native surface geometry is writable but must not alias job or cache arrays.
overlay = controller._current_overlays[0]
overlay.vertices[0, 0] = 123
assert latest.result["mesh"]["vertices"][0, 0] == 0
assert controller.cache[latest.key]["mesh"]["vertices"][0, 0] == 0

# Explicit recalculation bypasses an otherwise valid entry, including duplicate
# in-flight input. A later ordinary run may reuse the fresh result.
before = len(calls)
fresh = wait(controller.start("analyze", force=True))
assert not fresh.cached and len(calls) == before + 1
forced_running = controller.start("analyze", force=True)
forced_replacement = controller.start("analyze", force=True)
assert forced_replacement is not forced_running and forced_running.cancel.is_set()
wait(forced_replacement)
assert forced_replacement.status == "done" and not forced_replacement.cached
fresh = wait(controller.start("analyze"))
assert fresh.cached

# Toggle affects the active generated candidate only. User surfaces, atoms and
# older candidates keep their own visibility; new previews honor the preference.
assert controller.has_preview_overlays()
user_display, atom_displays = surface.display, first.atoms.displays.copy()
controller.set_overlay_visibility(False)
assert controller.overlay_visible is False
assert not any(model.display for model in controller._current_overlays)
old = tuple(controller._current_overlays)
controller.apply_candidate(1)
assert not any(model.display for model in controller._current_overlays)
controller.set_overlay_visibility(True)
assert all(model.display for model in controller._current_overlays)
assert all(not model.display for model in old if model in session.models.list())
assert surface.display == user_display
np.testing.assert_array_equal(first.atoms.displays, atom_displays)

# Invalid indices are rejected before native scene changes; repeats still validate
# scientific input (the old implementation returned early for the same index).
depth = len(controller.undo_states)
overlays_before = tuple(controller._current_overlays)
for invalid in (-1, 99, "1", 1.5, True, None):
    controller.apply_candidate(invalid)
    assert len(controller.undo_states) == depth and controller._current_overlays == overlays_before
controller.panel.show_result(fresh)
check_for_changes(session)
QTest.qWait(80)
first.atoms.colors = (31, 92, 150, 255)
first.atoms.displays = False
first.display = False
check_for_changes(session)
QTest.qWait(100)
assert fresh.status == "done" and controller.has_preview_overlays(), (
    "Cosmetic edits invalidated a result", fresh.status, controller.latest is fresh,
    controller._preview_job is fresh, len(controller._current_overlays), controller.panel.status.text())
first.display = True
first.atoms.displays = True
first.atoms[0].bfactor += 1
controller.apply_candidate(fresh.candidate)
assert fresh.status == "stale", "Same-candidate click bypassed scientific validation"
assert all(not model.display for model in overlays_before if model in session.models.list())

# Actual atomic ChangeTracker events coalesce; they hide stale generated previews
# and cancel active computations without requiring another button click.
fresh = wait(controller.start("analyze"))
check_for_changes(session)
QTest.qWait(80)
with patch.object(controller, "_validate_observed", wraps=controller._validate_observed) as observed:
    for index in range(12):
        first.atoms[0].coord += np.array((0.01, 0, 0))
        check_for_changes(session)
    assert controller._validation_pending
    QTest.qWait(100)
    assert observed.call_count == 1, "Scientific edits were not coalesced"
assert fresh.status == "stale" and not controller.has_preview_overlays()
assert not any(getattr(model, "_codex_quick_overlay", False) and model.display
               for model in session.models.list())
pending = controller.start("analyze", force=True)
first.atoms[0].occupancy = 0.75
check_for_changes(session)
QTest.qWait(100)
assert pending.status == "stale" and pending.cancel.is_set() and pending.done.is_set()
QTest.qWait(180)
assert pending.status == "stale", "Late worker callback overwrote the stale status"

fresh = wait(controller.start("analyze"))
first.position = translation((2, 0, 0))
QTest.qWait(100)
assert fresh.status == "stale", "Native model-position change was not observed"

# Restoring a cached input can finish before the coalesced old-input observer.
# Pruning an obsolete Undo record must not hide the new, scientifically valid mesh.
state_a = wait(controller.start("analyze"))
point_a = np.array(first.atoms[0].coord, copy=True)
first.atoms[0].coord += np.array((0.2, 0, 0))
state_b = wait(controller.start("analyze"))
first.atoms[0].coord = point_a
state_a_again = wait(controller.start("analyze"))
assert state_a_again.cached
QTest.qWait(100)
assert state_a_again.status == "done" and all(model.display for model in controller._current_overlays)

first.name = "renamed target"
QTest.qWait(100)
assert state_a_again.status == "stale", "Native model-name change was not observed"
fresh = wait(controller.start("analyze"))
session.models.assign_id(first, (71,))
QTest.qWait(100)
assert fresh.status == "stale", "Native model-ID change was not observed"

# Failed start cannot export/re-apply the previous target. Closed-target undo must
# preserve a subsequently edited unrelated scene, including the camera.
fresh = wait(controller.start("analyze"))
assert controller.start("analyze", model_hint="#99999") is None
assert controller.latest is None and controller.panel.job is None
assert not controller.panel.copy_button.isEnabled()
fresh = wait(controller.start("analyze", model_hint=f"#{first.id_string}"))
session.models.close([first])
replacement.position = translation((9, 8, 7))
camera_before = session.main_view.camera.position.matrix.copy()
controller.undo()
np.testing.assert_array_equal(replacement.position.matrix[:, 3], [9, 8, 7])
np.testing.assert_array_equal(session.main_view.camera.position.matrix, camera_before)
assert not controller.undo_states and fresh.status == "stale"
controller.set_overlay_visibility(True)
assert not controller.has_preview_overlays()

# Missing native toolbars schedule one bounded set of checks, never a retry tree.
scheduled = []
fake = SimpleNamespace(logger=None, ui=SimpleNamespace(is_gui=True),
    toolbar=SimpleNamespace(_toolbar={}, add_provider=lambda *args, **kwargs: None),
    toolshed=SimpleNamespace(find_bundle=lambda *args, **kwargs: SimpleNamespace(name="ChimeraX-CodexBridge")))
with patch("chimerax.toolbar.tool.get_toolbar_singleton", return_value=None), \
        patch.object(QTimer, "singleShot", side_effect=lambda delay, callback: scheduled.append(callback)):
    for _ in range(3):
        package._install_runtime_toolbar_buttons(fake, force_rebuild=True)
    assert len(scheduled) == 3
    for callback in list(scheduled):
        callback()
    assert len(scheduled) == 3, "Toolbar retry callbacks multiplied while no toolbar existed"

report = {"ok": True, "click_return_s": returned_in, "gui_ticks": len(heartbeats),
          "cache_hits": controller.cache_hits, "overlay_bound": count,
          "selection_types_preserved": True, "stale_target_rejected": True,
          "closed_model_rejected": True, "cancellation": True, "bounded_queue": True,
          "bounded_toolbar_retries": True, "byte_bounded_immutable_cache": True,
          "native_mesh_isolation": True, "force_recalculate": True,
          "current_overlay_visibility": True, "native_scientific_observer": True,
          "coalesced_changes": True, "cosmetic_changes_preserved": True,
          "cached_preview_survives_old_observer": True,
          "failed_start_no_old_report": True, "closed_target_undo_scoped": True}
Path("/tmp/quick-runtime-report.json").write_text(json.dumps(report, indent=2))
controller.close()
actions.backend_for = original_backend
print("QUICK_RUNTIME_OK", json.dumps(report))
