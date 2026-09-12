"""Exercise every bridge right panel in a disposable, native ChimeraX GUI.

This checks reachable content at short dock sizes, real Qt wheel routing,
nested text/list scrolling, and that scrolling cannot change scene settings.
The runner disables preference writes; no user process or AI job is touched.
"""
import ctypes
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

from Qt.QtCore import QPoint, QPointF, Qt
from Qt.QtGui import QWheelEvent
from Qt.QtWidgets import (QApplication, QAbstractButton, QAbstractScrollArea,
                          QComboBox, QDoubleSpinBox, QLineEdit, QSlider, QSpinBox,
                          QWidget)
from PyQt6 import sip
from PyQt6.QtCore import QLibraryInfo
from PyQt6.QtTest import QTest
from chimerax.atomic import AtomicStructure, check_for_changes
from chimerax.std_commands.view import NamedView, _named_views

ROOT = Path(__file__).resolve().parents[1]
REPORT = Path("/tmp/right-panel-scroll-report.json")
REPORT.unlink(missing_ok=True)
assert session.ui.is_gui, "Use scripts/run_panel_check.py all"
app = QApplication.instance()
QTest.qWait(5500)
for name in list(sys.modules):
    if name == "chimerax.codex_bridge" or name.startswith("chimerax.codex_bridge."):
        del sys.modules[name]
spec = importlib.util.spec_from_file_location("chimerax.codex_bridge", ROOT / "src/__init__.py",
    submodule_search_locations=[str(ROOT / "src")])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
package._schedule_helper_dock_layout = lambda *args, **kwargs: None

from chimerax.codex_bridge.action_pad import CodexActionPad
from chimerax.codex_bridge.camera_bookmarks import CameraBookmarks
from chimerax.codex_bridge.caver import CodexCaverTool
from chimerax.codex_bridge.cavity_browser import CodexCavityBrowser, set_cavity_candidates
from chimerax.codex_bridge.display_controls import CodexDisplayControls
from chimerax.codex_bridge.panel_scroll import PanelScrollArea
from chimerax.codex_bridge.quick_context import capture_context
from chimerax.codex_bridge.quick_results import QuickResults
from chimerax.codex_bridge.tool import CodexAssistant
from chimerax.codex_bridge.ui_theme import style_model_panel
from chimerax.model_panel.tool import ModelPanel, model_panel

models = []
for index in range(28):
    model = AtomicStructure(session, name=f"Structure {index + 1:02d}: long model description")
    for number in range(1, 5):
        residue = model.new_residue("ALA", "A", number)
        atom = model.new_atom("CA", "C")
        residue.add_atom(atom)
        atom.coord = (number * 3.8, index * 4, 0)
    session.models.add([model])
    model.atoms.colors = (71, 126, 139, 255)
    model.ss_assigned = True
    models.append(model)
models[0].atoms[0].selected = True
check_for_changes(session)
app.processEvents()

views = _named_views(session).views
for index in range(24):
    views[f"Comparison condition {index + 1:02d}"] = NamedView(
        session.main_view, (0, 0, 0), session.models.list())
set_cavity_candidates(session, [{"rank": i + 1, "spec": models[i].atomspec, "volume": 12.5 + i,
                                "label": f"Cavity comparison {i + 1}"} for i in range(24)])

assistant = CodexAssistant.get_singleton(session)
display = CodexDisplayControls.get_singleton(session)
action = CodexActionPad.get_singleton(session)
bookmarks = CameraBookmarks.get_singleton(session)
quick = QuickResults.get_singleton(session)
caver = CodexCaverTool.get_singleton(session)
cavity = CodexCavityBrowser.get_singleton(session)
native_models = ModelPanel.get_singleton(session) or model_panel(session, "Model Panel")
native_models.display(True)
native_models.countdown = 0
native_models._fill_tree(always_rebuild=True)
native_models.tree.expandAll()
assert len(native_models._items) >= len(models), "Native Model Panel fixture was not populated"
style_model_panel(native_models)
action.widget.refresh()
display.widget.refresh()
assistant._show_assistant_tab()
draft = "Keep this draft.\nCompare the selected chain.\nDo not change its colors."
assistant.prompt_edit.setPlainText(draft)
assistant.terminal_edit.setPlainText("\n".join(f"Earlier assistant entry {i}" for i in range(100)))
transcript = assistant.terminal_edit.toPlainText()
caver.output.setPlainText("\n".join(f"Tunnel calculation evidence line {i}" for i in range(100)))

context = capture_context(session, models[0].atomspec)
result = {"title": "Cavity comparison", "summary": ["28 independent geometric candidates"],
          "details": [f"Evidence line {i}: measured geometry, not binding affinity." for i in range(100)],
          "metrics": [], "candidates": [{"label": f"Candidate {i + 1:02d}", "kind": "geometry",
              "volume": 123.4 + i, "max_depth": 2.5 + i, "contact_residues": i + 3,
              "specs": [models[0].residues[0].atomspec]} for i in range(28)]}
job = SimpleNamespace(action="cavity", context=context, result=result, status="done",
                      candidate=0, elapsed=.2, cached=False)
quick.show_result(job)
quick.details_toggle.setChecked(True)

tools = {"assistant": assistant, "display": display, "action": action,
         "bookmarks": bookmarks, "quick": quick, "models": native_models,
         "caver": caver, "cavity": cavity}
hosts = {}
for name, tool in tools.items():
    host = tool.tool_window.ui_area
    host.setParent(None)
    host.show()
    hosts[name] = host
QTest.qWait(100)


def scene():
    return ([((m.id, m.display), m.atoms.coords.tobytes(), m.atoms.colors.tobytes(),
              m.atoms.displays.tobytes(), m.atoms.selected.tobytes(),
              m.position.matrix.tobytes(), m.residues.ribbon_colors.tobytes()) for m in models],
            session.main_view.camera.position.matrix.tobytes(),
            tuple(session.main_view.background_color))


before = scene()
library = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.LibrariesPath))
qtcore = ctypes.CDLL(str(library / "QtCore.framework/Versions/A/QtCore"))
native_send = getattr(qtcore, "_ZN16QCoreApplication20sendSpontaneousEventEP7QObjectP6QEvent")
native_send.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
native_send.restype = ctypes.c_bool


def wheel(target, *, pixel=False, direction=-1, native=False):
    position = QPointF(target.rect().center())
    global_position = QPointF(target.mapToGlobal(position.toPoint()))
    receiver = target
    if native:
        window = target.window()
        position = QPointF(target.mapTo(window, position.toPoint()))
        receiver = window.windowHandle()
    event = QWheelEvent(position, global_position,
        QPoint(0, direction * 61) if pixel else QPoint(),
        QPoint() if pixel else QPoint(0, direction * 120), Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    if native:
        native_send(sip.unwrapinstance(receiver), sip.unwrapinstance(event))
    else:
        app.sendEvent(receiver, event)
    app.processEvents()


def outer_scroll(host):
    candidates = host.findChildren(PanelScrollArea)
    assert candidates, ("No complete-panel scroll area", host.objectName())
    for candidate in candidates:
        parent = candidate.parentWidget()
        while parent is not None and not isinstance(parent, PanelScrollArea):
            parent = parent.parentWidget()
        if parent is None:
            return candidate
    raise AssertionError("No outermost panel scroll")


def native_widgets(host, classes):
    return [widget for widget in host.findChildren(QWidget)
            if isinstance(widget, classes) and widget.isVisibleTo(host) and not widget.isWindow()]


checks = {}
screens = []
for name, host in hosts.items():
    scroll = outer_scroll(host)
    bar = scroll.verticalScrollBar()
    viewport = scroll.viewport()
    dimensions = []
    for width, height in ((360, 150), (360, 220), (500, 150), (500, 220)):
        host.resize(width, height)
        if name == "assistant":
            assistant._apply_responsive_layout(width)
        QTest.qWait(70)
        assert (host.width(), host.height()) == (width, height), (
            name, "Minimum sizes prevent compact dock", width, height, host.size())
        assert scroll.horizontalScrollBar().maximum() == 0, (name, "Horizontal overflow")
        assert scroll.widget().width() <= viewport.width(), (name, "Clipped content width")
        if height == 150:
            assert bar.maximum() > 0, (name, "Whole panel cannot scroll at short height", width, height)
        bar.setValue(0)
        wheel(viewport, pixel=True)
        assert not bar.maximum() or bar.value() > 0, (name, "Trackpad did not move whole panel")
        bar.setValue(0)
        # Native QWindow dispatch exercises actual child hit testing. Point the
        # cursor at the outer scroll bar so child lists do not consume it first.
        wheel(bar, native=True)
        assert not bar.maximum() or bar.value() > 0, (name, "Native mouse wheel did not move panel")
        for _ in range(200):
            if bar.value() == bar.maximum():
                break
            wheel(viewport)
        assert bar.value() == bar.maximum(), (name, "Footer cannot be reached by wheel")
        content_bottom = scroll.widget().mapTo(viewport, QPoint(0, scroll.widget().height())).y()
        assert content_bottom <= viewport.height() + 2, (name, "Bottom remains clipped", content_bottom)
        for widget in native_widgets(scroll.widget(), (QAbstractButton, QComboBox, QSpinBox,
                                                        QDoubleSpinBox, QSlider, QLineEdit)):
            # Embedded tree cells may legitimately be clipped by their own
            # scrolling viewport; assess the full panel's regular controls.
            ancestor = widget.parentWidget()
            embedded = False
            while ancestor is not None and ancestor is not scroll.widget():
                if isinstance(ancestor, QAbstractScrollArea):
                    embedded = True
                    break
                ancestor = ancestor.parentWidget()
            if embedded:
                continue
            position = widget.mapTo(scroll.widget(), QPoint())
            assert position.x() >= -1 and position.x() + widget.width() <= scroll.widget().width() + 1, (
                name, "Control horizontally clipped", widget.objectName(), widget.size(), position)
        dimensions.append({"width": width, "height": height, "scroll_max": bar.maximum()})
        if width == 360:
            path = f"/tmp/right-panel-{name}-{width}x{height}-bottom.png"
            assert host.grab().save(path)
            screens.append(path)

    host.resize(500, 150)
    QTest.qWait(70)
    controls_checked = 0
    for control in native_widgets(scroll.widget(), (QComboBox, QSpinBox, QDoubleSpinBox, QSlider)):
        scroll.ensureWidgetVisible(control, 0, 8)
        app.processEvents()
        before_value = control.currentIndex() if isinstance(control, QComboBox) else control.value()
        wheel(control, pixel=True)
        wheel(control)
        after_value = control.currentIndex() if isinstance(control, QComboBox) else control.value()
        assert after_value == before_value, (name, "Wheel changed a setting", control.objectName(), before_value, after_value)
        controls_checked += 1

    nested_checked = 0
    for nested in host.findChildren(QAbstractScrollArea):
        if isinstance(nested, PanelScrollArea) or not nested.isVisibleTo(host):
            continue
        inner_bar = nested.verticalScrollBar()
        if inner_bar.maximum() <= 0:
            continue
        # Direct event delivery covers the exact text/list viewport regardless
        # of how much of that tall control fits the short dock.
        scroll.ensureWidgetVisible(nested, 0, 0)
        inner_bar.setValue(0)
        previous_outer = bar.value()
        wheel(nested.viewport(), pixel=True)
        assert inner_bar.value() > 0, (name, "Nested text/list ignored trackpad", type(nested).__name__)
        assert bar.value() == previous_outer, (name, "Outer moved before nested content")
        inner_bar.setValue(inner_bar.maximum())
        bar.setValue(0)
        wheel(nested.viewport(), pixel=True)
        assert bar.value() > 0, (name, "Nested list trapped wheel at its bottom", type(nested).__name__)
        inner_bar.setValue(0)
        bar.setValue(bar.maximum())
        wheel(nested.viewport(), direction=1)
        assert bar.value() < bar.maximum(), (name, "Nested list trapped wheel at its top", type(nested).__name__)
        nested_checked += 1
    checks[name] = {"sizes": dimensions, "protected_controls": controls_checked,
                    "nested_scrollers": nested_checked}

assert checks["cavity"]["nested_scrollers"] >= 1, "Cavity list scrolling was not exercised"

assert assistant.prompt_edit.toPlainText() == draft, "Scrolling changed the AI draft"
assert assistant.terminal_edit.toPlainText() == transcript, "Scrolling changed conversation text"
assert scene() == before, "Scrolling changed structure, colors, selection, or camera"
report = {"ok": True, "panels": checks, "draft_and_transcript_preserved": True,
          "scene_and_selection_preserved": True, "screenshots": screens}
REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")
print("RIGHT_PANEL_SCROLL_OK", json.dumps(report))
