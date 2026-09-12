"""Upgrade open right panels in place, preserving their fields, jobs and scene."""
import importlib
import importlib.util
import json
from pathlib import Path
import sys

from Qt.QtCore import QTimer
from Qt.QtWidgets import QComboBox, QLineEdit, QPlainTextEdit, QScrollArea, QVBoxLayout, QWidget


def _scene_state(session):
    import hashlib
    digest = hashlib.sha256()
    view = session.main_view
    digest.update(view.camera.position.matrix.tobytes())
    digest.update(str(tuple(view.background_color)).encode())
    for model in session.models.list():
        digest.update(str((id(model), model.display)).encode())
        digest.update(model.positions.array().tobytes())
        if hasattr(model, "atoms"):
            for array in (model.atoms.coords, model.atoms.colors,
                          model.atoms.displays, model.atoms.selected,
                          model.residues.ribbon_colors):
                digest.update(array.tobytes())
    return digest.hexdigest()


def _load_source(name):
    source = Path(__file__).resolve().parents[1] / "src" / (name + ".py")
    qualified = "chimerax.codex_bridge." + name
    spec = importlib.util.spec_from_file_location(qualified, source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[qualified] = module
    spec.loader.exec_module(module)
    package = importlib.import_module("chimerax.codex_bridge")
    setattr(package, name, module)
    return module


def _replace_scroll(old, panel_class):
    if isinstance(old, panel_class):
        return old
    parent = old.parentWidget()
    layout = parent.layout()
    if layout is None:
        raise RuntimeError("Cannot locate the existing panel scroll layout")
    position = old.verticalScrollBar().value()
    content = old.takeWidget()
    for child in (content, *content.findChildren(QWidget)):
        child.removeEventFilter(old)
    new = panel_class(parent)
    layout.replaceWidget(old, new)
    new.setWidget(content)
    new.setObjectName(old.objectName())
    old.hide()
    old.setParent(None)
    old.deleteLater()
    new.show()
    QTimer.singleShot(0, lambda: new.verticalScrollBar().setValue(position))
    return new


def reload_right_panel_scrolling(session, *, report_path="/tmp/right-panel-scroll-live-report.json"):
    shared = _load_source("panel_scroll")
    PanelScrollArea, wrap_panel = shared.PanelScrollArea, shared.wrap_panel
    before = _scene_state(session)
    updated = []
    fields = []
    names = {"AI Assistant", "Display Controls", "Action Pad", "Camera Bookmarks",
             "Quick Results", "CAVER", "Cavity Browser", "Model Panel", "Models"}
    for tool in list(session.tools.list()):
        if tool.tool_name not in names:
            continue
        parent = tool.tool_window.ui_area
        for field in parent.findChildren(QWidget):
            if isinstance(field, QLineEdit):
                fields.append((field, field.text(), "text"))
            elif isinstance(field, QPlainTextEdit):
                fields.append((field, field.toPlainText(), "toPlainText"))
            elif isinstance(field, QComboBox):
                fields.append((field, field.currentText(), "currentText"))
        if getattr(tool, "_codex_panel_scroll_version", 0) == 1:
            updated.append(tool.tool_name)
            continue
        name = tool.tool_name
        if name == "AI Assistant":
            old = getattr(tool, "scroll_area", None) or next(
                area for area in parent.findChildren(QScrollArea)
                if area.widget() is not None and area.widget().layout() is not None)
            content = old.widget()
            prompt = tool.prompt_panel
            if prompt.parentWidget() is not content:
                prompt.parentWidget().layout().removeWidget(prompt)
                content.layout().addWidget(prompt)
            tool.scroll_area = _replace_scroll(old, PanelScrollArea)
        elif name == "Display Controls":
            widget, old = tool.widget, tool.widget.scroll_area
            if getattr(tool, "_ui_layout_version", 0) < 23:
                # Bring the original heading and status inside the same scroll.
                original = widget.layout()
                content = QWidget()
                content.setLayout(original)
                body = old.takeWidget()
                original.replaceWidget(old, body)
                old.hide()
                old.setParent(None)
                old.deleteLater()
                outer = QVBoxLayout(widget)
                outer.setContentsMargins(0, 0, 0, 0)
                widget.scroll_area = PanelScrollArea(widget)
                widget.scroll_area.setWidget(content)
                outer.addWidget(widget.scroll_area)
                widget.setMinimumHeight(0)
            else:
                widget.scroll_area = _replace_scroll(old, PanelScrollArea)
        elif name == "Action Pad":
            old = getattr(tool, "scroll_area", None)
            tool.scroll_area = (_replace_scroll(old, PanelScrollArea) if old is not None
                                else wrap_panel(parent))
        elif name == "Camera Bookmarks":
            tool.scroll_area = _replace_scroll(tool.scroll_area, PanelScrollArea)
        elif name == "Quick Results":
            tool.scroll = _replace_scroll(tool.scroll, PanelScrollArea)
        elif name in ("CAVER", "Cavity Browser"):
            old = getattr(tool, "scroll_area", None)
            if name == "Cavity Browser":
                tool.list_widget.setMinimumHeight(100)
            else:
                from Qt.QtWidgets import QLabel, QPushButton, QGridLayout
                for label in parent.findChildren(QLabel):
                    if "CAVER tunnel/channel" in label.text():
                        label.setWordWrap(True)
                for grid in parent.findChildren(QGridLayout):
                    buttons = [grid.itemAt(i).widget() for i in range(grid.count())]
                    if len(buttons) == 10 and all(isinstance(b, QPushButton) for b in buttons):
                        # QGridLayout retains stretch on emptied columns.
                        # Clear the old third column before reflowing buttons.
                        for column in range(grid.columnCount()):
                            grid.setColumnStretch(column, 0)
                        for button in buttons:
                            grid.removeWidget(button)
                        for index, button in enumerate(buttons):
                            grid.addWidget(button, index // 2, index % 2)
                        for column in range(2):
                            grid.setColumnStretch(column, 1)
            tool.scroll_area = (_replace_scroll(old, PanelScrollArea) if old is not None
                                else wrap_panel(parent))
        else:
            theme = _load_source("ui_theme")
            theme.style_model_panel(tool)
        parent.setMinimumHeight(0)
        dock = getattr(tool.tool_window, "_dock_widget", None)
        if dock is not None:
            dock.setMinimumHeight(0)
        tool._codex_panel_scroll_version = 1
        updated.append(name)
    fields_preserved = all(getattr(widget, method)() == value
                           for widget, value, method in fields)
    report = {"ok": fields_preserved and _scene_state(session) == before,
              "panels": updated, "fields_preserved": fields_preserved,
              "scene_preserved": _scene_state(session) == before}
    Path(report_path).write_text(
        json.dumps(report, indent=2), encoding="utf-8")
    if not report["ok"]:
        raise RuntimeError("Panel refresh state verification failed; inspect the report")
    session.logger.status("Right panels updated: full scrolling, nested lists and wheel-safe controls.")
    return report


if "session" in globals():
    reload_right_panel_scrolling(session)
