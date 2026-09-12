"""Small native-theme controls shared by the bridge's dock panels."""


def forget_destroyed_tool_window(tool):
    """Retire a dead native window before normal ToolInstance cleanup.

    A QWidget can be destroyed before ChimeraX removes its ToolInstance. Its
    native registry would otherwise try to hide/destroy the same dock again.
    Call this only after the UI area's destroyed signal has fired.
    """
    window = getattr(getattr(tool.session, "ui", None), "main_window", None)
    registry = getattr(window, "tool_instance_to_windows", None)
    retire = getattr(window, "_tool_window_destroyed", None)
    if registry is None or not callable(retire):
        return
    native = getattr(tool, "tool_window", None)
    if native in registry.get(tool, ()):
        retire(native)
    if tool in registry and not registry[tool]:
        registry.pop(tool)


def panel_stylesheet(root_name):
    """Return palette-based QSS; root_name must be an internal object name."""
    return f"""
    QWidget#{root_name} {{ background: palette(window); color: palette(window-text); }}
    QWidget {{ font-size: 11px; }}
    QLabel {{ background: transparent; color: palette(window-text); border: none; }}
    QLabel[role="heading"] {{ font-weight: 600; }}
    QLabel[role="caption"] {{ font-size: 10px; }}
    QPushButton, QToolButton {{ background: palette(button); color: palette(button-text); border: 1px solid palette(mid); border-radius: 4px; padding: 2px 6px; min-height: 18px; }}
    QPushButton:hover, QToolButton:hover {{ border-color: palette(highlight); }}
    QPushButton:checked, QToolButton:checked {{ background: palette(midlight); }}
    QPushButton:focus, QToolButton:focus {{ border-color: palette(highlight); }}
    QPushButton:disabled, QToolButton:disabled {{ color: #858585; }}
    QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {{ background: palette(base); color: palette(text); border: 1px solid palette(mid); border-radius: 4px; padding: 2px 4px; min-height: 18px; }}
    QComboBox:focus, QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: palette(highlight); }}
    QComboBox {{ padding-right: 16px; }}
    QComboBox::drop-down {{ border: none; width: 15px; }}
    QComboBox QAbstractItemView {{ background: palette(base); color: palette(text); selection-background-color: palette(highlight); }}
    QTextEdit, QPlainTextEdit, QListWidget, QTreeWidget {{ background: palette(base); color: palette(text); border: 1px solid palette(mid); border-radius: 4px; selection-background-color: palette(highlight); }}
    QHeaderView::section {{ background: palette(button); color: palette(button-text); border: none; border-bottom: 1px solid palette(mid); padding: 3px 5px; font-size: 10px; }}
    QTreeWidget::item {{ min-height: 22px; }}
    QScrollArea {{ background: transparent; border: none; }}
    QScrollBar:vertical {{ background: transparent; width: 6px; }}
    QScrollBar::handle:vertical {{ background: palette(mid); border-radius: 3px; min-height: 24px; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    QSplitter::handle {{ background: palette(mid); }}
    """


def style_model_panel(panel):
    """Keep native Model Panel actions, but give the model names the full width."""
    from Qt.QtCore import Qt
    from Qt.QtWidgets import QBoxLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy

    parent = panel.tool_window.ui_area
    parent.setObjectName("CodexModelsRoot")
    parent.setStyleSheet(panel_stylesheet("CodexModelsRoot") + "QTreeWidget QPushButton { padding: 0; min-height: 14px; max-height: 16px; min-width: 14px; max-width: 16px; }")
    tree = panel.tree
    tree.setAlternatingRowColors(False)
    tree.setIndentation(13)
    tree.setAnimated(False)
    tree.setTextElideMode(Qt.TextElideMode.ElideMiddle)
    tree.setMinimumWidth(0)
    tree.setMinimumHeight(120)
    if not getattr(panel, "_codex_compact_models_layout", False):
        layout = parent.layout()
        # Reuse every native button and signal connection. Only move the
        # narrow vertical button rail below the tree.
        buttons = [panel.buttons_layout.itemAt(i).widget()
                   for i in range(panel.buttons_layout.count())]
        buttons = [button for button in buttons if isinstance(button, QPushButton)]
        rail = next((layout.itemAt(i).widget() for i in range(layout.count())
                     if isinstance(layout.itemAt(i).widget(), QScrollArea)), None)
        layout.setDirection(QBoxLayout.Direction.TopToBottom)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(5)
        heading = QHBoxLayout()
        panel._codex_model_count = QLabel(parent)
        panel._codex_model_count.setProperty("role", "heading")
        heading.addWidget(panel._codex_model_count, 1)
        hint = QLabel("Double-click name / ID to edit", parent)
        hint.setProperty("role", "caption")
        hint.setToolTip(hint.text())
        hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        heading.addWidget(hint)
        layout.insertLayout(0, heading)
        for label in (panel._codex_model_count, hint):
            label.setFixedHeight(18)
            label.show()
        actions = QHBoxLayout()
        actions.setSpacing(4)
        sequential = QHBoxLayout()
        sequential.setSpacing(4)
        by_title = {button.text(): button for button in buttons}
        for title in ("Show", "Hide", "View", "Info", "Close"):
            button = by_title.get(title)
            if button is not None:
                button.setParent(parent)
                button.setFixedHeight(24)
                button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                actions.addWidget(button, 1)
                button.show()
        for button in panel._seq_buttons:
            was_hidden = button.isHidden()
            button.setParent(parent)
            button.setFixedHeight(24)
            sequential.addWidget(button, 1)
            button.setHidden(was_hidden)
        if rail is not None:
            layout.removeWidget(rail)
            rail.hide()
        layout.addLayout(actions)
        layout.addLayout(sequential)
        layout.setStretchFactor(tree, 1)
        tree.itemSelectionChanged.connect(lambda: update_model_panel_columns(panel))
        panel._codex_compact_models_layout = True
    # Keep the native tree and every action in one scrollable content area.
    # The tree retains its own scrolling; short docks can also reach the
    # action rows and optional sequential controls below it.
    from .panel_scroll import wrap_panel
    panel._codex_models_scroll = wrap_panel(parent)
    update_model_panel_columns(panel)


def update_model_panel_columns(panel):
    from Qt.QtWidgets import QHeaderView
    from Qt.QtCore import Qt
    tree = panel.tree
    blocked = tree.blockSignals(True)
    try:
        header = tree.header()
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(20)
        header.setSectionResizeMode(panel.NAME_COLUMN, QHeaderView.ResizeMode.Stretch)
        for column, width in ((panel.ID_COLUMN, 42), (panel.COLOR_COLUMN, 24),
                              (panel.SHOWN_COLUMN, 35), (panel.SELECT_COLUMN, 35), (panel.SKIP_COLUMN, 35)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            if column == panel.ID_COLUMN:
                ids = [item.text(column) for item in getattr(panel, "_items", [])]
                width = max(width, min(84, max((tree.fontMetrics().horizontalAdvance(t) for t in ids), default=0) + 14))
            tree.setColumnWidth(column, width)
        for column, tip in ((panel.COLOR_COLUMN, "Model color"), (panel.SHOWN_COLUMN, "Show / hide model"),
                            (panel.SELECT_COLUMN, "Select model in the scene"), (panel.SKIP_COLUMN, "Skip during sequential display")):
            tree.headerItem().setToolTip(column, tip)
        for item in getattr(panel, "_items", []):
            item.setToolTip(panel.NAME_COLUMN, item.text(panel.NAME_COLUMN))
            item.setTextAlignment(panel.ID_COLUMN, Qt.AlignmentFlag.AlignCenter)
        label = getattr(panel, "_codex_model_count", None)
        if label is not None:
            count = len(getattr(panel, "models", []))
            selected = len(tree.selectedItems())
            label.setText(f"{count} models" + (f" · {selected} highlighted" if selected else ""))
    finally:
        tree.blockSignals(blocked)
