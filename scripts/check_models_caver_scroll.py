"""Real native widgets in a disposable offscreen ChimeraX session.

Run with --nogui --notools --exit and a no-space runpy driver.  No native
actions leave the test session and user settings are never saved.
"""
import importlib.util
import json
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from Qt.QtCore import QPoint, QPointF, Qt
from Qt.QtGui import QWheelEvent
from Qt.QtWidgets import QApplication, QPushButton, QWidget
from chimerax.atomic import AtomicStructure
from chimerax.core.configfile import ConfigFile

ConfigFile.save = lambda *args, **kwargs: None
root = Path(__file__).resolve().parents[1]
for module in ("ui_theme", "caver", "panel_scroll"):
    sys.modules.pop(f"chimerax.codex_bridge.{module}", None)
spec = importlib.util.spec_from_file_location("chimerax.codex_bridge", root / "src/__init__.py",
    submodule_search_locations=[str(root / "src")])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)

app = QApplication.instance() or QApplication([])


class DisposableToolWindow:
    def __init__(self, tool, **kwargs):
        self.ui_area = QWidget()
        self.shown = True

    def manage(self, **kwargs):
        pass


import chimerax.ui
chimerax.ui.MainToolWindow = DisposableToolWindow
session.ui.forward_keystroke = lambda *args: None
from chimerax.model_panel.tool import model_panel
from chimerax.codex_bridge.caver import CodexCaverTool
from chimerax.codex_bridge.panel_scroll import wrap_panel
from chimerax.codex_bridge.ui_theme import style_model_panel

structures = []
for index in range(28):
    model = AtomicStructure(session, name=f"Scroll fixture {index + 1}")
    residue = model.new_residue("ALA", "A", 1)
    atom = model.new_atom("CA", "C")
    residue.add_atom(atom)
    atom.coord = (index * 4, 0, 0)
    structures.append(model)
session.models.add(structures)
structures[0].atoms.selected = True


def scene():
    return (tuple((model.id, model.display, model.atoms.coords.tobytes(),
                   model.atoms.colors.tobytes(), model.atoms.selected.tobytes())
                  for model in structures),
            session.main_view.camera.position.matrix.tobytes())


def settle():
    for _ in range(8):
        app.processEvents()


def wheel(target, direction=-1, pixel=True):
    pos = QPointF(target.rect().center())
    event = QWheelEvent(pos, QPointF(target.mapToGlobal(pos.toPoint())),
                        QPoint(0, direction * 50) if pixel else QPoint(),
                        QPoint() if pixel else QPoint(0, direction * 120),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    app.sendEvent(target, event)
    settle()


before = scene()
models = model_panel(session, "Model Panel")
models._fill_tree(always_rebuild=True)
models.tree.setCurrentItem(models._items[0])
native_buttons = list(models.tool_window.ui_area.findChildren(QPushButton))
buttons = {button.text(): button for button in native_buttons if button.text()}
style_model_panel(models)
assert models._codex_model_views.currentIndex() == 0
models._codex_model_views.setCurrentIndex(1)
scroll = models._codex_models_scroll
content = scroll.widget()
for _ in range(4):
    style_model_panel(models)
assert models._codex_models_scroll is scroll
assert scroll.widget() is content
assert all(button in models.tool_window.ui_area.findChildren(QPushButton) for button in native_buttons)
assert len(models.tree.selectedItems()) == 1
assert scene() == before, "Styling/re-wrapping changed models, selection or camera"

# Native button callbacks remain attached to the same highlighted model.
buttons["Hide"].click()
assert not structures[0].display and all(model.display for model in structures[1:])
buttons["Show"].click()
assert scene() == before
models.showing_sequence_controls = True

host = models.tool_window.ui_area
host.resize(320, 150)
host.show()
settle()
assert host.height() == 150, ("Models dock could not shrink", host.height())
outer = scroll.verticalScrollBar()
assert outer.maximum() > 0
outer.setValue(outer.maximum())
settle()
for button in models._seq_buttons:
    position = button.mapTo(scroll.viewport(), QPoint())
    assert position.y() >= 0 and position.y() + button.height() <= scroll.viewport().height() + 1

# Scroll model rows first, then continue through the panel at the boundary.
outer.setValue(0)
inner = models.tree.verticalScrollBar()
inner.setValue(0)
wheel(models.tree.viewport())
assert inner.value() > 0 and outer.value() == 0
inner.setValue(inner.maximum())
wheel(models.tree.viewport())
assert outer.value() > 0
assert scene() == before
outer.setValue(outer.maximum())
settle()
host.grab().save("/tmp/models-scroll-check.png")

caver = CodexCaverTool(session, "CAVER")
caver.caver_home_field.setText("/tmp/keep this CAVER draft")
caver.output.setPlainText("\n".join(f"Analysis output {index}" for index in range(150)))
draft = caver.caver_home_field.text()
output = caver.output.toPlainText()
host = caver.tool_window.ui_area
scroll = caver.scroll_area
assert wrap_panel(host) is scroll
host.resize(320, 150)
host.show()
settle()
assert host.height() == 150, ("CAVER dock could not shrink", host.height())
outer = scroll.verticalScrollBar()
assert outer.maximum() > 0

# Every action can be brought fully into the viewport, even at short heights.
for button in host.findChildren(QPushButton):
    scroll.ensureWidgetVisible(button, 0, 0)
    settle()
    position = button.mapTo(scroll.viewport(), QPoint())
    assert position.y() >= 0 and position.y() + button.height() <= scroll.viewport().height() + 1, button.text()
    assert button.width() >= button.fontMetrics().horizontalAdvance(button.text()) + 14, button.text()
outer.setValue(0)
inner = caver.output.verticalScrollBar()
inner.setValue(0)
wheel(caver.output.viewport())
assert inner.value() > 0 and outer.value() == 0
inner.setValue(inner.maximum())
wheel(caver.output.viewport(), pixel=False)
assert outer.value() > 0
outer.setValue(outer.maximum())
settle()
position = caver.output.mapTo(scroll.viewport(), QPoint())
assert position.y() + caver.output.height() <= scroll.viewport().height() + 1
assert caver.caver_home_field.text() == draft and caver.output.toPlainText() == output
assert scene() == before
host.grab().save("/tmp/caver-scroll-check.png")
report = {"ok": True, "short_height": 150, "models": len(structures),
          "original_native_buttons": len(native_buttons), "caver_actions": 10,
          "scene_preserved": True, "nested_scroll": True, "idempotent": True}
Path("/tmp/models-caver-scroll-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print("MODELS_CAVER_SCROLL_OK", json.dumps(report))
