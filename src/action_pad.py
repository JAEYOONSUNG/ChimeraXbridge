import threading

from Qt.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from Qt.QtGui import QPalette
from Qt.QtCore import QSignalBlocker, Qt, QTimer

from chimerax.core.tools import ToolInstance, get_singleton

from .display_color import (
    apply_stick_context_colors,
    maybe_apply_stick_context_colors_for_command,
    restore_charge_colors,
    show_sticks_with_cartoon_anchor,
)
from .integration import command_batch
from .pick_mode import bind_pick_mode
from .ui_theme import panel_stylesheet


def _is_qt_main_thread():
    try:
        from Qt.QtCore import QCoreApplication, QThread

        app = QCoreApplication.instance()
        return app is not None and QThread.currentThread() == app.thread()
    except Exception:
        return False


def _is_selection_only_command_text(command):
    text = str(command or "").strip().lower()
    if not text:
        return False
    return text == "select" or text.startswith("select ") or text.startswith("~select")


def _run_command_thread_safe(session, command):
    from chimerax.core.commands import run

    if _is_qt_main_thread():
        result = run(session, command)
        maybe_apply_stick_context_colors_for_command(session, command)
        return result

    result_box = {}
    event = threading.Event()

    def runner():
        try:
            result_box["result"] = run(session, command)
            maybe_apply_stick_context_colors_for_command(session, command)
        except Exception as err:
            result_box["error"] = err
        finally:
            event.set()

    session.ui.thread_safe(runner)
    if not event.wait(120):
        raise TimeoutError(
            f"action_pad UI bounce did not complete within 120s: {command!r}"
        )
    if "error" in result_box:
        raise result_box["error"]
    return result_box.get("result")


class _ElidedLabel(QLabel):
    """Keep target and status text readable without widening the dock."""

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self._full_text = ""
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(18)
        self.setText(text)

    def setText(self, text):
        self._full_text = str(text)
        self.setToolTip(self._full_text)
        self._elide_text()

    def _elide_text(self):
        super().setText(self.fontMetrics().elidedText(
            self._full_text, Qt.TextElideMode.ElideRight, max(0, self.width())
        ))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide_text()


class ActionPadWidget(QWidget):

    BUTTON_HEIGHT = 24
    PANEL_MARGIN = 6
    PANEL_GAP = 4

    def __init__(self, session, *, open_ai_callback=None, launch_ai_callback=None, parent=None):
        super().__init__(parent)
        self.session = session
        self._open_ai_callback = open_ai_callback
        self._launch_ai_callback = launch_ai_callback
        self.handlers = []
        self._closed = False
        self._refresh_pending = False
        self._refresh_require_visible = False
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self._run_queued_refresh)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(self.PANEL_MARGIN, self.PANEL_MARGIN, self.PANEL_MARGIN, self.PANEL_MARGIN)
        layout.setSpacing(self.PANEL_GAP)
        self.setLayout(layout)
        self.setObjectName("ActionPadRoot")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.setStyleSheet(
            panel_stylesheet("ActionPadRoot")
            + "QTreeWidget::item { min-height: 24px; }"
            "QToolButton[role='row-action'] { padding: 0px; min-height: 18px; }"
            "QToolButton::menu-indicator { image: none; width: 0px; }"
        )

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(self.PANEL_GAP)
        self.refresh_button = QPushButton("Refresh", self)
        self.refresh_button.setToolTip("Refresh the model, chain and current-selection rows")
        self.refresh_button.clicked.connect(self.refresh)
        toolbar.addWidget(self.refresh_button)

        self.open_ai_button = QPushButton("Assistant", self)
        self.open_ai_button.setToolTip("Open AI Assistant")
        self.open_ai_button.clicked.connect(self._open_ai)
        toolbar.addWidget(self.open_ai_button)

        self.ai_analyze_button = QPushButton("AI Analyze", self)
        self.ai_analyze_button.clicked.connect(
            lambda: self._launch_ai_prompt("Analyze the current ChimeraX scene with evidence and confidence.", "analyze")
        )
        self.ai_analyze_button.setToolTip("Ask AI Assistant to analyze the current scene")
        toolbar.addWidget(self.ai_analyze_button)

        self.conserve3d_button = QPushButton("3D Conserve", self)
        self.conserve3d_button.setToolTip(
            "Highlight sequence-unique residues across the structures currently open and aligned"
        )
        self.conserve3d_button.clicked.connect(self._open_3d_conserve)
        toolbar.addWidget(self.conserve3d_button)
        layout.addLayout(toolbar)

        pick_row = QHBoxLayout()
        pick_row.setContentsMargins(0, 0, 0, 0)
        pick_row.setSpacing(self.PANEL_GAP)
        pick_row.addWidget(QLabel("Mouse", self))
        self._pick_buttons = {}
        for which, text, tip, attribute in (
            ("residue", "Residue", "Left click selects a residue; Shift-left click adds residues.", "pick_residue_button"),
            ("chain", "Chain", "Left click selects a chain; Shift-left click adds chains.", "pick_chain_button"),
            ("menu", "Right menu", "Right click opens the residue context menu.", "pick_menu_button"),
            ("default", "Default", "Restore the standard left and right mouse buttons.", "pick_default_button"),
        ):
            button = QPushButton(text, self)
            button.setCheckable(True)
            button.setToolTip(tip)
            button.clicked.connect(lambda checked=False, mode=which: self._set_pick_mode(mode))
            setattr(self, attribute, button)
            self._pick_buttons[which] = button
            pick_row.addWidget(button, 1)
        layout.addLayout(pick_row)

        for button in (
            self.refresh_button,
            self.open_ai_button,
            self.pick_residue_button,
            self.pick_chain_button,
            self.pick_menu_button,
            self.pick_default_button,
            self.ai_analyze_button,
            self.conserve3d_button,
        ):
            button.setFixedHeight(self.BUTTON_HEIGHT)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.pick_mode_label = _ElidedLabel("Mouse bindings unchanged", self)
        self.pick_mode_label.setProperty("role", "caption")
        layout.addWidget(self.pick_mode_label)

        self.status_label = _ElidedLabel("Ready", self)
        self.status_label.setProperty("role", "caption")
        self.status_label.setObjectName("ActionPadStatus")

        targets_layout = QVBoxLayout()
        targets_layout.setContentsMargins(0, 0, 0, 0)
        targets_layout.setSpacing(self.PANEL_GAP)
        layout.addLayout(targets_layout, 1)

        self.tree = QTreeWidget(self)
        self.tree.setMinimumWidth(0)
        self.tree.setMinimumHeight(120)
        self.tree.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.tree.setColumnCount(6)
        self.tree.setHeaderLabels(["Object / chain", "Action", "Show", "Hide", "Label", "Color"])
        self.tree.setRootIsDecorated(True)
        self.tree.setIndentation(12)
        self.tree.setAlternatingRowColors(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.tree.setToolTip("Select a row to use its target actions. Double-click to focus it.")
        self.tree.itemSelectionChanged.connect(self._update_current_spec_label)
        self.tree.currentItemChanged.connect(self._update_current_spec_label)
        self.tree.itemDoubleClicked.connect(self._activate_current_item)
        header_view = self.tree.header()
        header_view.setStretchLastSection(False)
        header_view.setMinimumSectionSize(24)
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for index, width in enumerate((44, 36, 34, 36, 38), start=1):
            header_view.setSectionResizeMode(index, QHeaderView.ResizeMode.Fixed)
            self.tree.setColumnWidth(index, width)
            self.tree.headerItem().setToolTip(index, (
                "Select, focus, AI tools and object management",
                "Show cartoon, atoms, surfaces or solvent",
                "Hide cartoon, atoms, surfaces or solvent",
                "Add residue or model labels; clear labels",
                "Color by element, chain, model or a chosen color",
            )[index - 1])
        targets_layout.addWidget(self.tree, 1)

        target_row = QHBoxLayout()
        target_row.setContentsMargins(0, 0, 0, 0)
        target_row.setSpacing(self.PANEL_GAP)
        self.current_spec_label = _ElidedLabel("Target: select an object", self)
        self.current_spec_label.setProperty("role", "heading")
        target_row.addWidget(self.current_spec_label, 1)
        self.current_clear_button = QPushButton("Clear selection", self)
        self.current_clear_button.setToolTip("Clear the ChimeraX scene selection")
        self.current_clear_button.clicked.connect(lambda: self._run_commands("Selection clear", ["select clear"]))
        target_row.addWidget(self.current_clear_button)
        targets_layout.addLayout(target_row)

        current_actions = QHBoxLayout()
        current_actions.setContentsMargins(0, 0, 0, 0)
        current_actions.setSpacing(self.PANEL_GAP)
        self.current_focus_button = QPushButton("Focus", self)
        self.current_focus_button.clicked.connect(self._focus_current_item)
        self.current_focus_button.setToolTip("Select and focus the current target")
        current_actions.addWidget(self.current_focus_button, 1)

        self.current_action_button = self._menu_button("Action", parent=self, compact=False)
        current_actions.addWidget(self.current_action_button, 1)

        self.current_show_button = self._menu_button("Show", parent=self, compact=False)
        current_actions.addWidget(self.current_show_button, 1)

        self.current_hide_button = self._menu_button("Hide", parent=self, compact=False)
        current_actions.addWidget(self.current_hide_button, 1)

        self.current_label_button = self._menu_button("Label", parent=self, compact=False)
        current_actions.addWidget(self.current_label_button, 1)

        self.current_color_button = self._menu_button("Color", parent=self, compact=False)
        current_actions.addWidget(self.current_color_button, 1)
        targets_layout.addLayout(current_actions)
        for button in (self.current_focus_button, self.current_clear_button):
            button.setFixedHeight(self.BUTTON_HEIGHT)
        layout.addWidget(self.status_label)
        self._refresh_current_target_controls(None, None)
        self._update_pick_mode_feedback()

    def install_handlers(self):
        if self.handlers:
            return
        self.handlers = [
            self.session.triggers.add_handler("selection changed", self._queue_selection_refresh),
            self.session.triggers.add_handler("command finished", self._queue_command_refresh),
        ]
        self.refresh()

    def cleanup(self):
        self._closed = True
        self._refresh_pending = False
        self._refresh_timer.stop()
        for handler in self.handlers:
            try:
                handler.remove()
            except Exception:
                pass
        self.handlers = []

    def refresh(self):
        if self._closed:
            return
        from .semantic import get_session_semantics, invalidate_semantic_cache

        self._refresh_timer.stop()
        self._refresh_pending = False
        # Scene edits and picks can happen without a command-finished event.
        invalidate_semantic_cache(self.session)
        semantics = get_session_semantics(self.session)
        selected_token = self._selected_session_spec(semantics)
        if not selected_token:
            selected_token = self._current_spec()

        expanded = self._tree_expansion_state()
        scroll_position = self.tree.verticalScrollBar().value()
        signal_blocker = QSignalBlocker(self.tree)
        self.tree.clear()
        model_count = 0
        selection_root = self._section_item("Current Selection")
        self.tree.addTopLevelItem(selection_root)
        selection_count = 0
        for spec in semantics.get("selection", {}).get("ranges", []):
            self._add_spec_item(selection_root, spec, spec, spec, selected_token)
            selection_count += 1
        for spec in semantics.get("selection", {}).get("models", []):
            if any(spec == child.data(0, Qt.ItemDataRole.UserRole) for child in [selection_root.child(i) for i in range(selection_root.childCount())]):
                continue
            self._add_spec_item(selection_root, spec, spec, spec, selected_token)
            selection_count += 1
        selection_root.setHidden(not selection_count)
        selection_root.setExpanded(True)

        models_root = self._section_item("Models")
        self.tree.addTopLevelItem(models_root)
        for model in semantics.get("models", []):
            if not model.get("atomic"):
                continue
            model_count += 1
            item = self._add_spec_item(
                models_root,
                f"{model['spec']} {model['name']}",
                model["spec"],
                f"{model['spec']} {model['name']}",
                selected_token,
            )
            for chain in model.get("chains", []):
                start = chain.get("start")
                end = chain.get("end")
                span = f"{start}-{end}" if start is not None and end is not None else "?"
                chain_spec = f"{model['spec']}/{chain['id']}"
                self._add_spec_item(item, f"{chain_spec}  [{span}]", chain_spec, chain_spec, selected_token)
            item.setExpanded(True)
        if model_count == 0:
            placeholder = QTreeWidgetItem(["No atomic models loaded", "", "", "", "", ""])
            placeholder.setFlags(placeholder.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            placeholder.setForeground(0, self.palette().brush(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text))
            models_root.addChild(placeholder)
        models_root.setExpanded(True)

        self._restore_tree_expansion(expanded)
        self.tree.doItemsLayout()
        self.tree.verticalScrollBar().setValue(scroll_position)
        del signal_blocker
        self._update_current_spec_label()
        self._update_pick_mode_feedback()
        models_text = f"{model_count} model{'s' if model_count != 1 else ''}"
        selections_text = f"{selection_count} selection target{'s' if selection_count != 1 else ''}"
        self.status_label.setText(f"{models_text} · {selections_text}")

    def _tree_expansion_state(self):
        return {key: item.isExpanded() for key, item in self._tree_branches()}

    def _tree_branches(self):
        """Section plus target identifies branches independently of row order."""
        def walk(parent, path):
            for index in range(parent.childCount()):
                item = parent.child(index)
                key = path + (item.data(0, Qt.ItemDataRole.UserRole) or item.text(0),)
                if item.childCount():
                    yield key, item
                    yield from walk(item, key)

        yield from walk(self.tree.invisibleRootItem(), ())

    def _restore_tree_expansion(self, expanded):
        for key, item in self._tree_branches():
            if key in expanded:
                item.setExpanded(expanded[key])

    def _queue_command_refresh(self, _trigger_name=None, command=None, *_args):
        if _is_selection_only_command_text(command):
            self._queue_selection_refresh()
            return
        self._queue_refresh(delay_ms=150)

    def _queue_selection_refresh(self, *_args, **_kwargs):
        self._queue_refresh(delay_ms=0, require_visible=True)

    def _queue_refresh(self, *_args, delay_ms=150, require_visible=False, **_kwargs):
        if self._closed:
            return
        delay_ms = max(0, int(delay_ms))
        if self._refresh_pending:
            self._refresh_require_visible = self._refresh_require_visible and require_visible
        else:
            self._refresh_require_visible = require_visible
        self._refresh_pending = True
        # A pick takes priority over a delayed command refresh already queued.
        if not self._refresh_timer.isActive() or delay_ms < self._refresh_timer.remainingTime():
            self._refresh_timer.start(delay_ms)

    def _run_queued_refresh(self):
        self._refresh_pending = False
        if self._closed or (self._refresh_require_visible and not self.isVisible()):
            return
        self.refresh()

    def _selected_session_spec(self, semantics):
        selection = semantics.get("selection", {})
        ranges = list(selection.get("ranges", []))
        if ranges:
            return ranges[0]
        models = list(selection.get("models", []))
        return models[0] if models else None

    def _section_item(self, title):
        item = QTreeWidgetItem([title, "", "", "", "", ""])
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        font = item.font(0)
        font.setBold(True)
        item.setFont(0, font)
        return item

    def _add_spec_item(self, parent_item, text, spec, label, selected_token):
        item = QTreeWidgetItem([text, "", "", "", "", ""])
        item.setData(0, Qt.ItemDataRole.UserRole, spec)
        item.setToolTip(0, f"{label}\nTarget: {spec}\nDouble-click to focus")
        parent_item.addChild(item)
        self._attach_row_buttons(item, spec, label=label)
        if selected_token and (spec == selected_token or selected_token.startswith(spec + ":")):
            if self._current_spec() != selected_token:
                self.tree.setCurrentItem(item)
        return item

    def _attach_row_buttons(self, item, spec, label):
        for column, text, title, factory in (
            (1, "A", "Actions", self._action_menu),
            (2, "S", "Show", self._show_menu),
            (3, "H", "Hide", self._hide_menu),
            (4, "L", "Label", self._label_menu),
            (5, "C", "Color", self._color_menu),
        ):
            cell = QWidget(self.tree)
            cell_layout = QHBoxLayout(cell)
            cell_layout.setContentsMargins(0, 1, 0, 1)
            cell_layout.setSpacing(0)
            menu = factory(spec, label)
            menu.aboutToShow.connect(lambda row=item: self.tree.setCurrentItem(row))
            button = self._menu_button(text, menu, parent=cell)
            button.setToolTip(f"{title}: {label}")
            button.setAccessibleName(f"{title} for {label}")
            cell_layout.addWidget(button, 0, Qt.AlignmentFlag.AlignCenter)
            self.tree.setItemWidget(item, column, cell)

    def _menu_button(self, text, menu=None, parent=None, *, compact=True):
        button = QToolButton(parent or self.tree)
        button.setText(text)
        button.setToolTip(f"{text} menu for the current target")
        if menu is not None:
            menu.setParent(button, menu.windowFlags())
            button.setMenu(menu)
            button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        if compact:
            button.setProperty("role", "row-action")
            button.setFixedSize(24, 22)
        else:
            button.setFixedHeight(self.BUTTON_HEIGHT)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return button

    def _action_menu(self, spec, label):
        menu = QMenu(self.tree)
        menu.addAction("Select", lambda: self._run_commands(f"Action select:{label}", [f"select {spec}"]))
        menu.addAction("Focus", lambda: self._run_commands(f"Action focus:{label}", [f"select {spec}", "view sel"]))
        menu.addAction("AI Analyze", lambda: self._launch_ai_prompt(f"Analyze {spec} with evidence and confidence.", "analyze"))
        menu.addAction("AI Improve View", lambda: self._launch_ai_prompt(f"Improve the view for {spec} and apply the changes directly.", "agent"))
        try:
            from .chain_cleanup import is_chain_spec, target_model_spec

            model_spec = target_model_spec(spec)
            if model_spec and not is_chain_spec(spec):
                menu.addSeparator()
                menu.addAction("Move ID...", lambda ms=model_spec: self._move_model_id(ms))
        except Exception:
            pass
        menu.addSeparator()
        delete_menu = menu.addMenu("Delete")
        delete_menu.addAction("Delete target atoms...", lambda: self._delete_target_atoms(spec, label))
        try:
            from .chain_cleanup import is_chain_spec, target_model_spec

            model_spec = target_model_spec(spec)
            if model_spec:
                delete_menu.addAction(
                    "Delete waters / solvent in model...",
                    lambda checked=False, ms=model_spec: self._run_solvent_action("delete", ms),
                )
            delete_menu.addAction(
                "Delete waters / solvent in all models...",
                lambda checked=False: self._run_solvent_action("delete", None),
            )
            if is_chain_spec(spec):
                delete_menu.addSeparator()
                delete_menu.addAction("Keep only this chain in model...", lambda: self._keep_only_chain(spec, label))
        except Exception:
            pass
        return menu

    def _show_menu(self, spec, label):
        menu = QMenu(self.tree)
        menu.addAction("Cartoon", lambda: self._run_commands(f"Show cartoon:{label}", self._show_commands(spec, "cartoon")))
        menu.addAction("Sticks", lambda: self._run_display_action(spec, label, "show", "sticks"))
        menu.addAction("Surface", lambda: self._run_commands(f"Show surface:{label}", self._show_commands(spec, "surface")))
        menu.addAction("All", lambda: self._run_display_action(spec, label, "show", "all"))
        self._add_solvent_display_actions(menu, spec, "show")
        return menu

    def _hide_menu(self, spec, label):
        menu = QMenu(self.tree)
        menu.addAction("Cartoon", lambda: self._run_commands(f"Hide cartoon:{label}", self._hide_commands(spec, "cartoon")))
        menu.addAction("Atoms", lambda: self._run_commands(f"Hide atoms:{label}", self._hide_commands(spec, "atoms")))
        menu.addAction("Surface", lambda: self._run_commands(f"Hide surface:{label}", self._hide_commands(spec, "surface")))
        menu.addAction("All", lambda: self._run_commands(f"Hide all:{label}", self._hide_commands(spec, "all")))
        self._add_solvent_display_actions(menu, spec, "hide")
        return menu

    def _add_solvent_display_actions(self, menu, spec, action):
        try:
            from .chain_cleanup import target_model_spec

            model_spec = target_model_spec(spec)
        except Exception:
            model_spec = None
        menu.addSeparator()
        if model_spec:
            menu.addAction(
                f"Waters / solvent (model {model_spec})",
                lambda checked=False, ms=model_spec, a=action: self._run_solvent_action(a, ms),
            )
        menu.addAction(
            "Waters / solvent (all models)",
            lambda checked=False, a=action: self._run_solvent_action(a, None),
        )

    def _label_menu(self, spec, label):
        menu = QMenu(self.tree)
        menu.addAction("Residues", lambda: self._run_commands(f"Label residues:{label}", [f"label {spec} residues"]))
        menu.addAction("Models", lambda: self._run_commands(f"Label models:{label}", [f"label {spec} models"]))
        menu.addAction("Clear labels", lambda: self._run_commands("Clear labels", ["label delete"]))
        return menu

    def _color_menu(self, spec, label):
        menu = QMenu(self.tree)
        menu.addAction("By element", lambda: self._run_commands(f"Color by element:{label}", [f"color {spec} byelement"]))
        menu.addAction("Carbon from cartoon + hetero by element", lambda: self._apply_stick_colors(spec, f"Color context:{label}"))
        menu.addAction("By chain", lambda: self._color_and_restore(f"Color by chain:{label}", spec, "bychain"))
        menu.addAction("By model", lambda: self._color_and_restore(f"Color by model:{label}", spec, "bymodel"))
        preset_menu = menu.addMenu("Preset")
        for color_name in ("yellow", "cyan", "magenta", "hotpink", "cornflowerblue", "orange", "gold"):
            preset_menu.addAction(color_name, lambda checked=False, c=color_name: self._color_and_restore(f"Color {c}:{label}", spec, c))
        menu.addAction("Custom...", lambda: self._pick_custom_color(spec, label))
        menu.addAction("Rainbow", lambda: self._run_commands(f"Rainbow:{label}", [f"rainbow {spec}"]))
        return menu

    def _run_commands(self, batch_label, commands):
        try:
            with command_batch(self.session, batch_label):
                for command in commands:
                    _run_command_thread_safe(self.session, command)
        except Exception as err:
            self.status_label.setText(str(err) if str(err) else err.__class__.__name__)
            return
        self.status_label.setText(batch_label)
        self.refresh()

    def _confirm_destructive(self, title, message):
        try:
            from Qt.QtWidgets import QMessageBox

            reply = QMessageBox.question(
                self,
                title,
                message,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            return reply == QMessageBox.StandardButton.Yes
        except Exception:
            return True

    def _delete_target_atoms(self, spec, label):
        if not self._confirm_destructive(
            "Delete atoms",
            f"Delete atoms for {label}?\n\nThis removes them from the model, not just from the display.",
        ):
            return
        try:
            from .chain_cleanup import delete_atomspec

            message = delete_atomspec(self.session, spec)
        except Exception as err:
            self.status_label.setText(str(err) if str(err) else err.__class__.__name__)
            return
        self.status_label.setText(message)
        self.refresh()

    def _move_model_id(self, model_spec):
        try:
            from Qt.QtWidgets import QInputDialog

            current = str(model_spec or "").strip().lstrip("#")
            text, ok = QInputDialog.getText(
                self,
                "Move model ID",
                f"Move #{current} to ID:",
                text=current,
            )
            if not ok:
                return
        except Exception as err:
            self.status_label.setText(str(err) if str(err) else err.__class__.__name__)
            return
        try:
            from .model_order import reorder_model_spec_to_id

            message = reorder_model_spec_to_id(self.session, model_spec, text)
        except Exception as err:
            self.status_label.setText(str(err) if str(err) else err.__class__.__name__)
            return
        self.status_label.setText(message)
        self.refresh()

    def _keep_only_chain(self, spec, label):
        if not self._confirm_destructive(
            "Keep only this chain",
            f"Keep {label} and delete every other chain in the same model?\n\nThis removes atoms from the model.",
        ):
            return
        try:
            from .chain_cleanup import keep_only_chain

            message = keep_only_chain(self.session, spec)
        except Exception as err:
            self.status_label.setText(str(err) if str(err) else err.__class__.__name__)
            return
        self.status_label.setText(message)
        self.refresh()

    def _run_solvent_action(self, action, model_spec=None):
        if action == "delete":
            scope = model_spec or "all models"
            if not self._confirm_destructive(
                "Delete waters / solvent",
                f"Delete waters / solvent in {scope}?\n\nThis removes atoms from the model, not just from the display.",
            ):
                return
        try:
            from .chain_cleanup import delete_solvent, hide_solvent, show_solvent

            functions = {
                "delete": delete_solvent,
                "hide": hide_solvent,
                "show": show_solvent,
            }
            message = functions[action](self.session, model_spec=model_spec)
        except Exception as err:
            self.status_label.setText(str(err) if str(err) else err.__class__.__name__)
            return
        self.status_label.setText(message)
        self.refresh()

    def _color_and_restore(self, batch_label, spec, color):
        try:
            with command_batch(self.session, batch_label):
                _run_command_thread_safe(self.session, f"color {spec} {color}")
                restore_charge_colors(self.session, spec)
        except Exception as err:
            self.status_label.setText(str(err) if str(err) else err.__class__.__name__)
            return
        self.status_label.setText(batch_label)
        self.refresh()

    def _open_ai(self):
        if self._open_ai_callback is not None:
            self._open_ai_callback()

    def _open_3d_conserve(self):
        from chimerax.core.commands import run as _cx_run
        try:
            _cx_run(self.session, "ui tool show '3D Conserve'")
            self.status_label.setText("3D Conserve panel opened.")
        except Exception as exc:
            self.status_label.setText(f"3D Conserve unavailable: {exc}")

    def _launch_ai_prompt(self, prompt, mode):
        if self._launch_ai_callback is not None:
            self._launch_ai_callback(prompt, mode)

    def _current_item(self):
        item = self.tree.currentItem()
        if item is None:
            return None
        spec = item.data(0, Qt.ItemDataRole.UserRole)
        return item if spec else None

    def _current_spec(self):
        item = self._current_item()
        if item is None:
            return None
        return item.data(0, Qt.ItemDataRole.UserRole)

    def _current_label(self):
        item = self._current_item()
        if item is None:
            return "(none)"
        return item.text(0)

    def _select_current_item(self):
        spec = self._current_spec()
        if not spec:
            self.status_label.setText("No target selected.")
            return
        self._run_commands(f"Action select:{self._current_label()}", [f"select {spec}"])

    def _focus_current_item(self):
        spec = self._current_spec()
        if not spec:
            self.status_label.setText("No target selected.")
            return
        self._run_commands(f"Action focus:{self._current_label()}", [f"select {spec}", "view sel"])

    def _run_current_item_commands(self, verb, variant):
        spec = self._current_spec()
        if not spec:
            self.status_label.setText("No target selected.")
            return
        label = self._current_label()
        if verb == "show":
            self._run_display_action(spec, label, verb, variant)
            return
        commands = self._hide_commands(spec, variant)
        self._run_commands(f"{verb.title()} {variant}:{label}", commands)

    def _activate_current_item(self, item, _column):
        spec = item.data(0, Qt.ItemDataRole.UserRole)
        if not spec:
            return
        self._run_commands(f"Action focus:{item.text(0)}", [f"select {spec}", "view sel"])

    def _show_commands(self, spec, variant):
        commands = []
        if variant in {"cartoon", "all"}:
            commands.append(f"cartoon {spec}")
        if variant in {"sticks", "all"}:
            commands.extend([f"show {spec} atoms", f"style {spec} stick"])
        if variant in {"surface", "all"}:
            commands.append(f"surface {spec}")
        return commands

    def _hide_commands(self, spec, variant):
        commands = []
        if variant in {"cartoon", "all"}:
            commands.append(f"hide {spec} cartoons")
        if variant in {"atoms", "all"}:
            commands.append(f"hide {spec} atoms")
        if variant in {"surface", "all"}:
            commands.append(f"~surface {spec}")
        return commands

    def _update_current_spec_label(self, *_args):
        spec = self._current_spec()
        if not spec:
            self.current_spec_label.setText("Target: select an object")
            self._refresh_current_target_controls(None, None)
            return
        self.current_spec_label.setText(f"Target: {spec}")
        self.current_spec_label.setToolTip(f"{self._current_label()}\nTarget: {spec}")
        self._refresh_current_target_controls(spec, self._current_label())

    def _refresh_current_target_controls(self, spec, label):
        target_key = (spec, label)
        if getattr(self, "_current_target_key", None) == target_key:
            return
        self._current_target_key = target_key
        self.current_focus_button.setEnabled(bool(spec))
        for button, factory in (
            (self.current_action_button, self._action_menu),
            (self.current_show_button, self._show_menu),
            (self.current_hide_button, self._hide_menu),
            (self.current_label_button, self._label_menu),
            (self.current_color_button, self._color_menu),
        ):
            old_menu = button.menu()
            button.setMenu(None)
            if old_menu is not None:
                old_menu.deleteLater()
            button.setEnabled(bool(spec))
            button.setToolTip(
                f"{button.text()}: {label}" if spec else "Select an object to use this menu"
            )
            if spec:
                menu = factory(spec, label)
                menu.setParent(button, menu.windowFlags())
                button.setMenu(menu)
                button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)

    def _update_pick_mode_feedback(self):
        mouse_modes = getattr(getattr(self.session, "ui", None), "mouse_modes", None)
        try:
            left = mouse_modes.mode("left", [], exact=True)
            right = mouse_modes.mode("right", [], exact=True)
        except (AttributeError, TypeError):
            for button in self._pick_buttons.values():
                button.setChecked(False)
            self.pick_mode_label.setText("Mouse bindings unavailable")
            return

        residue = left is not None and left is getattr(self.session, "_codex_bridge_residue_pick_mode", None)
        chain = left is not None and left is getattr(self.session, "_codex_bridge_chain_pick_mode", None)
        menu = right is not None and right is getattr(self.session, "_codex_bridge_context_menu_mode", None)
        left_name = "residues" if residue else "chains" if chain else getattr(left, "name", "unbound")
        right_name = "context menu" if menu else getattr(right, "name", "unbound")
        for which, active in (
            ("residue", residue),
            ("chain", chain),
            ("menu", menu),
            ("default", left_name == "rotate" and right_name == "translate"),
        ):
            self._pick_buttons[which].setChecked(active)
        self.pick_mode_label.setText(f"Left: {left_name} · Right: {right_name}")
        self.pick_mode_label.setToolTip(
            f"Left click: {left_name}. Right click: {right_name}.\n"
            "In Residue or Chain mode, Shift-left click adds to the selection."
        )

    def _run_display_action(self, spec, label, verb, variant):
        commands = self._show_commands(spec, variant) if verb == "show" else self._hide_commands(spec, variant)
        self._run_commands(f"{verb.title()} {variant}:{label}", commands)
        if verb == "show" and variant in {"sticks", "all"}:
            self._apply_stick_colors(spec, f"Stick colors:{label}")

    def _apply_stick_colors(self, spec, status_text):
        try:
            show_sticks_with_cartoon_anchor(self.session, spec)
        except Exception as err:
            self.status_label.setText(str(err) if str(err) else err.__class__.__name__)
            return
        self.status_label.setText(status_text)
        self.refresh()

    def _pick_custom_color(self, spec, label):
        from Qt.QtGui import QColor
        from Qt.QtWidgets import QColorDialog

        color = QColorDialog.getColor(QColor("#ffd166"), self, "Choose ChimeraX Color")
        if not color.isValid():
            return
        self._color_and_restore(f"Color custom:{label}", spec, color.name())

    def _set_pick_mode(self, which):
        try:
            message = bind_pick_mode(self.session, which)
        except Exception as err:
            self._update_pick_mode_feedback()
            self.status_label.setText(str(err) if str(err) else err.__class__.__name__)
            return
        self._update_pick_mode_feedback()
        self.status_label.setText(message)


class CodexActionPad(ToolInstance):

    SESSION_ENDURING = False
    SESSION_SAVE = False
    help = "help:user/tools/codex_action_pad.html"
    UI_LAYOUT_VERSION = 20

    @classmethod
    def get_singleton(cls, session, create=True, display=True, **kw):
        instance = get_singleton(session, cls, "Action Pad", create=create, display=display, **kw)
        if instance is not None and getattr(instance, "_ui_layout_version", None) != cls.UI_LAYOUT_VERSION:
            try:
                instance.delete()
            except Exception:
                pass
            instance = get_singleton(session, cls, "Action Pad", create=create, display=display, **kw)
        return instance

    def __init__(self, session, tool_name, placement_tool=None):
        super().__init__(session, tool_name)
        self._ui_layout_version = self.UI_LAYOUT_VERSION
        self._placement_tool = placement_tool

        from chimerax.ui import MainToolWindow

        self.tool_window = MainToolWindow(self, close_destroys=True)
        self._build_ui()

    def _build_ui(self):
        parent = self.tool_window.ui_area
        from .panel_scroll import PanelScrollArea
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        parent.setLayout(layout)
        self.widget = ActionPadWidget(
            self.session,
            open_ai_callback=self._open_ai,
            launch_ai_callback=self._launch_ai_prompt,
            parent=parent,
        )
        self.widget.install_handlers()
        self.scroll_area = PanelScrollArea(parent)
        self.scroll_area.setWidget(self.widget)
        layout.addWidget(self.scroll_area)
        if self._placement_tool is not None:
            self.tool_window.manage(placement=self._placement_tool)
        else:
            self.tool_window.manage(placement="side")
        try:
            from . import _schedule_helper_dock_layout

            _schedule_helper_dock_layout(self.session, raise_tool="action pad")
        except Exception:
            pass

    def delete(self):
        self.widget.cleanup()
        super().delete()

    def displayed(self):
        dock_widget = getattr(self.tool_window, "_dock_widget", None)
        if dock_widget is not None:
            return bool(dock_widget.isVisible())
        ui_area = getattr(self.tool_window, "ui_area", None)
        return bool(ui_area is not None and ui_area.isVisible())

    def display(self, b):
        dock_widget = getattr(self.tool_window, "_dock_widget", None)
        if dock_widget is not None:
            if b:
                dock_widget.show()
                dock_widget.raise_()
            else:
                dock_widget.hide()
            return
        try:
            super().display(b)
        except Exception:
            pass

    def _open_ai(self):
        from .tool import CodexAssistant

        assistant = CodexAssistant.get_singleton(self.session)
        if assistant is not None:
            assistant.display(True)

    def _launch_ai_prompt(self, prompt, mode):
        from .tool import CodexAssistant

        assistant = CodexAssistant.get_singleton(self.session)
        if assistant is not None:
            assistant.display(True)
            assistant._launch_quick_request(prompt, mode)
