"""Exercise all six public buttons against native loaded structure data."""
import importlib.util
import json
from pathlib import Path
import sys
import time

import numpy as np
from Qt.QtCore import QTimer, QPoint
from Qt.QtWidgets import QApplication, QDialog
from PyQt6.QtTest import QTest
from chimerax.atomic import AtomicStructure, selected_atoms
from chimerax.core.commands import run, StringArg
from chimerax.geometry import translation
import pyKVFinder

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("chimerax.codex_bridge", root / "src/__init__.py",
    submodule_search_locations=[str(root / "src")])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
from chimerax.codex_bridge.toolbar_actions import run_toolbar_action
from chimerax.codex_bridge.quick_actions import controller, PROVIDERS
from chimerax.codex_bridge.quick_context import capture_selection

app = QApplication.instance()
QTest.qWait(5500)
fixture = Path(pyKVFinder.__file__).parent / "data/tests/1FMO.pdb"
assert fixture.exists(), "Bundled biological fixture is required"
run(session, "open " + StringArg.unparse(str(fixture)), log=False)
target = max(session.models.list(type=AtomicStructure), key=lambda m: m.num_atoms)
unrelated = AtomicStructure(session, name="Unrelated control")
r = unrelated.new_residue("ALA", "Z", 1)
a = unrelated.new_atom("CA", "C")
r.add_atom(a)
a.coord = (100, 100, 100)
session.models.add([unrelated])
unrelated.atoms.colors = (90, 80, 70, 140)
unrelated.display = False
target.atoms.colors = (110, 155, 195, 235)
target.residues.ribbon_colors = (135, 95, 170, 255)
target.residues[5:7].atoms.selected = True
run(session, "view " + target.atomspec, log=False)
run(session, "label " + target.residues[1].atomspec + " residues", log=False)
QTest.qWait(100)


def state(model):
    return (model.atoms.coords.tobytes(), model.position.matrix.tobytes(), model.atoms.colors.tobytes(),
            model.residues.ribbon_colors.tobytes(), model.atoms.displays.tobytes(),
            model.atoms.draw_modes.tobytes(), model.residues.ribbon_displays.tobytes(), model.display)


original_target = state(target)
original_other = state(unrelated)
original_selection = capture_selection(session)["signature"]
original_models = set(session.models.list())
original_camera = session.main_view.camera.position.matrix.copy()
original_background = tuple(session.main_view.background_color)
original_highlight = session.main_view.highlight_thickness
control = controller(session)
timings = {}
screens = []


def wait(job):
    started = time.monotonic()
    while not job.done.is_set() and time.monotonic() - started < 60:
        QTest.qWait(5)
    assert job.done.is_set() and job.status == "done", (job.action, job.status, job.error)
    QTest.qWait(30)


for provider, action in PROVIDERS.items():
    ticks = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(time.perf_counter()))
    timer.start(15)
    click = time.perf_counter()
    job = run_toolbar_action(session, provider)
    click_return = time.perf_counter() - click
    assert job is not None, provider
    wait(job)
    timer.stop()
    timings[action] = {"click_return_s": click_return, "total_s": job.elapsed,
                       "compute_s": job.compute_seconds, "gui_ticks": len(ticks),
                       "max_tick_gap_s": max(np.diff(ticks), default=0.0)}
    assert click_return < 1.0, ("Slow click dispatch", action, click_return)
    assert all(not widget.isVisible() for widget in app.topLevelWidgets() if isinstance(widget, QDialog)), action
    assert job.result["summary"] and job.result["details"] and job.result["metrics"], action
    assert control.panel.copy_button.isEnabled()
    assert state(unrelated) == original_other, ("Unrelated model changed", action)
    assert capture_selection(session)["signature"] == original_selection, ("Selection changed", action)
    assert set(original_models).issubset(session.models.list()), ("User model/label removed", action)
    assert target.atoms.coords.tobytes() == original_target[0]
    assert target.atoms.colors.tobytes() == original_target[2], ("Colors changed", action)
    assert target.residues.ribbon_colors.tobytes() == original_target[3]
    if action == "analyze":
        overlays = [m for m in session.models.list() if getattr(m, "_codex_quick_overlay", False)]
        assert overlays, "Analyze created no GUI labels for its observed selected region"
        assert all(hasattr(m, "_codex_quick_targets") for m in overlays)
    if action == "cavity":
        assert any(c.get("kind") == "geometry" for c in job.result["candidates"]), job.result
        assert len(ticks) >= 10 and max(np.diff(ticks), default=0.0) < 1.0, timings[action]
        if len(job.result["candidates"]) > 1:
            control.apply_candidate(1)
            next_candidate = job.result["candidates"][1]
            assert f"{next_candidate['volume']:.0f} Å³" in control.panel.summary.toPlainText()
            assert next_candidate["label"] in control.panel._report_text
            control.undo()
            assert control.panel.candidates.currentRow() == 0
    if action == "zoom":
        assert job.result["candidates"][0]["source"] == "selection"
    if action == "figure":
        assert tuple(session.main_view.background_color[:3]) == (1.0, 1.0, 1.0)
        assert session.main_view.highlight_thickness == 0
    control.panel.details_toggle.setChecked(True)
    screenshot = f"/tmp/quick-{action}-workspace.png"
    session.ui.main_window.grab().save(screenshot)
    screens.append(screenshot)
    # Native OpenGL is a child window and is omitted by QWidget.grab().
    # Capture the actual rendered structure separately for visual QA.
    rendered = session.main_view.image(width=1000, height=800, supersample=1)
    assert rendered is not None
    pixels = np.asarray(rendered.convert("RGB"), dtype=np.int16)
    assert np.count_nonzero(np.max(np.abs(pixels - pixels[0, 0]), axis=2) > 8) > 1000, (action, "Blank render")
    render_path = f"/tmp/quick-{action}-render.png"
    rendered.save(render_path)
    rendered.close()
    screens.append(render_path)
    control.panel.details_toggle.setChecked(False)
    control.undo()
    QTest.qWait(40)
    assert state(target) == original_target, ("Undo did not restore target", action)
    assert state(unrelated) == original_other
    assert set(session.models.list()) == original_models, ("Undo left models", action)
    np.testing.assert_array_equal(session.main_view.camera.position.matrix, original_camera)
    assert tuple(session.main_view.background_color) == original_background
    assert session.main_view.highlight_thickness == original_highlight

# Repeating the measured cavity on unchanged inputs avoids its subprocess.
cached = run_toolbar_action(session, "ai-quick-cavity")
wait(cached)
assert cached.cached and cached.compute_seconds == 0
assert cached.elapsed < timings["cavity"]["total_s"], (cached.elapsed, timings["cavity"])
control.undo()

# The result UI must fit compact docks and keep lower actions reachable.
panel = control.panel
host = panel.tool_window.ui_area
old_parent = host.parentWidget()
host.setParent(None)
host.show()
for width, height in ((380, 500), (540, 500), (360, 280)):
    host.resize(width, height)
    QTest.qWait(50)
    assert host.size().width() == width and host.size().height() == height
    assert panel.scroll.horizontalScrollBar().maximum() == 0
    panel.scroll.verticalScrollBar().setValue(panel.scroll.verticalScrollBar().maximum())
    QTest.qWait(30)
    for button in (panel.copy_button, panel.export_button):
        origin = button.mapTo(panel.scroll.viewport(), QPoint())
        assert 0 <= origin.y() and origin.y() + button.height() <= panel.scroll.viewport().height()
    path = f"/tmp/quick-results-{width}x{height}.png"
    host.grab().save(path)
    screens.append(path)
host.setParent(old_parent)
old_parent.layout().insertWidget(0, host)
host.show()

# Existing advanced scripted cavity options remain on the legacy branch.
import chimerax.codex_bridge.toolbar_actions as toolbar
old_runner = toolbar._run_toolbar_chimerax_task
old_prompt = toolbar._prompt_toolbar_target_model_spec
seen = []
toolbar._run_toolbar_chimerax_task = lambda ses, label, fn: seen.append(label)
toolbar._prompt_toolbar_target_model_spec = lambda *args, **kwargs: target.atomspec
session._codex_cavity_force_options = {"distance": 5, "transparency": 65, "count": 2}
try:
    run_toolbar_action(session, "ai-quick-cavity")
    assert seen == ["Cavity"] and not hasattr(session, "_codex_cavity_force_options")
finally:
    toolbar._run_toolbar_chimerax_task = old_runner
    toolbar._prompt_toolbar_target_model_spec = old_prompt

report = {"ok": True, "six_routes": list(PROVIDERS.values()), "timings": timings,
          "cached_cavity_s": cached.elapsed, "no_setup_dialogs": True, "selection_preserved": True,
          "user_colors_and_models_preserved": True, "undo": True, "gui_labels": True,
          "compact_results": True, "advanced_compatibility": True, "screenshots": screens}
Path("/tmp/quick-integration-report.json").write_text(json.dumps(report, indent=2))
control.close()
print("QUICK_INTEGRATION_OK", json.dumps(report))
