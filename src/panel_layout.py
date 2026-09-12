"""Persistent, user-controlled layouts for the molecular helper panels."""
import weakref

from chimerax.core.settings import Settings


class PanelLayoutSettings(Settings):
    AUTO_SAVE = {"mode": "tabs"}


_PRIMARY = ("models", "ai assistant", "display controls", "action pad")


def _settings(session):
    settings = getattr(session, "_codex_panel_layout_settings", None)
    if settings is None:
        settings = PanelLayoutSettings(session, "Codex Panel Layout")
        session._codex_panel_layout_settings = settings
    return settings


def panel_layout_mode(session):
    mode = getattr(session, "_codex_panel_layout_mode", _settings(session).mode)
    return mode if mode in ("all", "tabs") else "tabs"


def _sync_button(button, mode):
    button.setText("Panels: All" if mode == "all" else "Panels: Tabs")
    for value, action in button._layout_actions.items():
        action.setChecked(value == mode)


def create_layout_button(session, parent=None):
    from Qt.QtWidgets import QMenu, QToolButton, QSizePolicy
    from Qt.QtGui import QActionGroup
    button = QToolButton(parent)
    button.setObjectName("codex_panel_layout")
    button.setMinimumWidth(112)
    button.setFixedHeight(30)
    button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    button.setToolTip("Show all four helper panels together, or use the tabbed sidebar.")
    button.setStyleSheet(
        "QToolButton { padding-right: 18px; }"
        "QToolButton::menu-indicator { width: 8px; height: 8px; subcontrol-position: right center; right: 4px; }")
    menu = QMenu(button)
    group = QActionGroup(menu)
    group.setExclusive(True)
    button._layout_actions = {}
    for mode, label in (("tabs", "Tabbed sidebar"), ("all", "All panels below")):
        action = menu.addAction(label)
        action.setCheckable(True)
        group.addAction(action)
        action.triggered.connect(lambda _checked=False, value=mode: set_panel_layout(session, value))
        button._layout_actions[mode] = action
    button.setMenu(menu)
    menu.aboutToShow.connect(lambda: _sync_button(button, panel_layout_mode(session)))
    buttons = getattr(session, "_codex_panel_layout_buttons", None)
    if buttons is None:
        session._codex_panel_layout_buttons = buttons = weakref.WeakSet()
    buttons.add(button)
    _sync_button(button, panel_layout_mode(session))
    return button


def _ensure_primary_tools(session):
    from chimerax.model_panel.tool import ModelPanel, model_panel
    from .tool import CodexAssistant
    from .display_controls import CodexDisplayControls
    from .action_pad import CodexActionPad
    models = ModelPanel.get_singleton(session) or model_panel(session, "Model Panel")
    models.display(True)
    for cls in (CodexAssistant, CodexDisplayControls, CodexActionPad):
        cls.get_singleton(session, create=True, display=True).display(True)


def _highlighted_models(session):
    from chimerax.model_panel.tool import ModelPanel
    panels = session.tools.find_by_class(ModelPanel)
    if not panels:
        return []
    panel = panels[0]
    result = []
    for item in panel.tree.selectedItems():
        try:
            result.append(panel.models[panel._items.index(item)])
        except (ValueError, IndexError):
            continue
    return result


def set_panel_layout(session, mode, *, save=True):
    if mode not in ("all", "tabs"):
        raise ValueError("Unknown panel layout")
    highlighted = _highlighted_models(session)
    session._codex_panel_layout_mode = mode
    if save:
        _settings(session).mode = mode
    # Constructors can request layout while the other tools are still being built.
    session._codex_panel_layout_applying = True
    try:
        _ensure_primary_tools(session)
    finally:
        session._codex_panel_layout_applying = False
    apply_helper_layout(session, force=True, highlighted=highlighted)
    for button in list(getattr(session, "_codex_panel_layout_buttons", ())):
        try:
            _sync_button(button, mode)
        except RuntimeError:
            continue
    return mode


def _docks(session):
    from . import _HELPER_DOCK_TOKENS, _find_dock_widget
    result = {}
    for key, tokens in _HELPER_DOCK_TOKENS:
        dock = _find_dock_widget(session, tokens)
        if dock is not None:
            result[key] = dock
    return result, _find_dock_widget(session, ("log",))


def apply_helper_layout(session, *, raise_tool=None, force=False, highlighted=None):
    if not getattr(getattr(session, "ui", None), "is_gui", False):
        return
    if getattr(session, "_codex_panel_layout_applying", False):
        return
    from Qt.QtCore import Qt
    from Qt.QtWidgets import QSizePolicy, QTabWidget
    window = getattr(session.ui, "main_window", None)
    if window is None:
        return
    mode = panel_layout_mode(session)
    docks, log = _docks(session)
    primary = [(key, docks[key]) for key in _PRIMARY if key in docks]
    if not primary:
        return
    from chimerax.cmd_line.tool import CommandLine
    command_tool = CommandLine.get_singleton(session, create=False, display=False)
    command = getattr(getattr(command_tool, "tool_window", None), "_dock_widget", None)
    if command is not None and (command.isFloating() or window.dockWidgetArea(command) != Qt.DockWidgetArea.BottomDockWidgetArea):
        command = None
    tracked = primary if mode == "all" else list(docks.items())
    signature = (mode, tuple((key, id(dock)) for key, dock in tracked))
    if not force and signature == getattr(session, "_codex_panel_layout_signature", None):
        # A scheduled retry or opening an existing tool must not undo the
        # user's divider positions, floating panels or current layout choice.
        if raise_tool:
            for key, dock in docks.items():
                if raise_tool.lower() in key:
                    dock.raise_()
                    break
        return
    from chimerax.model_panel.tool import model_panel
    models_tool = model_panel(session, "Model Panel")
    if highlighted is None:
        highlighted = _highlighted_models(session)
    session._codex_panel_layout_applying = True
    window.setUpdatesEnabled(False)
    try:
        window.setDockNestingEnabled(True)
        if not hasattr(session, "_codex_panel_layout_corners"):
            session._codex_panel_layout_corners = {
                corner: window.corner(corner) for corner in
                (Qt.Corner.BottomLeftCorner, Qt.Corner.BottomRightCorner)}
        managed = primary if mode == "all" else list(docks.items())
        for _key, dock in managed:
            if dock.isFloating():
                dock.setFloating(False)
            window.removeDockWidget(dock)
            dock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
            dock.setMinimumSize(0, 0)
            dock.setMaximumSize(16777215, 16777215)
        command_visible = command is not None and command.isVisible()
        if command is not None:
            window.removeDockWidget(command)
            command_height = max(24, command.widget().sizeHint().height())
            command.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            command.setMinimumHeight(command_height)
            command.setMaximumHeight(command_height)
        if mode == "all":
            for corner in (Qt.Corner.BottomLeftCorner, Qt.Corner.BottomRightCorner):
                window.setCorner(corner, Qt.DockWidgetArea.BottomDockWidgetArea)
            # Establish a full-width command row first, then split only the
            # helper row. Otherwise Qt places the command line beside the four
            # helpers and stretches the one-line input to their full height.
            first = primary[0][1]
            window.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, first)
            first.show()
            if command is not None:
                window.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, command)
                window.splitDockWidget(first, command, Qt.Orientation.Vertical)
                command.setVisible(command_visible)
            previous = first
            for _key, dock in primary[1:]:
                window.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock)
                window.splitDockWidget(previous, dock, Qt.Orientation.Horizontal)
                previous = dock
                dock.show()
            width = max(1, window.width() // len(primary))
            window.resizeDocks([dock for _, dock in primary], [width]*len(primary), Qt.Orientation.Horizontal)
            height = max(320, min(600, int(window.height()*0.43)))
            window.resizeDocks([primary[0][1]], [height], Qt.Orientation.Vertical)
            if log is not None:
                window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, log)
                window.resizeDocks([log], [max(240, min(420, int(window.width()*0.2)))], Qt.Orientation.Horizontal)
        else:
            window.setTabPosition(Qt.DockWidgetArea.RightDockWidgetArea, QTabWidget.TabPosition.South)
            if command is not None:
                window.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, command)
                command.setVisible(command_visible)
            for corner, area in session._codex_panel_layout_corners.items():
                window.setCorner(corner, area)
            anchor = primary[0][1]
            if log is not None:
                window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, log)
            for _key, dock in managed:
                window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
                dock.show()
            if log is not None:
                window.splitDockWidget(log, anchor, Qt.Orientation.Vertical)
            for _key, dock in managed:
                if dock is not anchor:
                    window.tabifyDockWidget(anchor, dock)
            window.resizeDocks([anchor], [max(380, int(window.width()*0.30))], Qt.Orientation.Horizontal)
            if log is not None:
                window.resizeDocks([log, anchor], [max(130, int(window.height()*0.23)), max(300, int(window.height()*0.7))], Qt.Orientation.Vertical)
        target = docks.get(raise_tool, primary[0][1])
        target.raise_()
        session._codex_panel_layout_signature = signature
    finally:
        window.setUpdatesEnabled(True)
        session._codex_panel_layout_applying = False
        window.update()
    # Native Models clears its tree while hidden. Refill after moving it so
    # a newly exposed pane is immediately useful, retaining highlighted rows.
    pending = getattr(models_tool, "_frame_drawn_handler", None)
    if pending is not None:
        session.triggers.remove_handler(pending)
        models_tool._frame_drawn_handler = None
    models_tool.countdown = 1
    models_tool._fill_tree(always_rebuild=True)
    for model, item in zip(models_tool.models, models_tool._items):
        if model in highlighted:
            item.setSelected(True)
