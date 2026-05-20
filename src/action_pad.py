import threading

from Qt.QtWidgets import (
    QGridLayout,
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
from Qt.QtGui import QColor, QFontDatabase
from Qt.QtCore import Qt, QTimer

from chimerax.core.tools import ToolInstance, get_singleton

from .display_color import (
    apply_stick_context_colors,
    maybe_apply_stick_context_colors_for_command,
    restore_charge_colors,
    show_sticks_with_cartoon_anchor,
)
from .integration import command_batch
from .pick_mode import bind_pick_mode


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


class ActionPadWidget(QWidget):

    BUTTON_HEIGHT = 28
    PANEL_MARGIN = 8
    PANEL_GAP = 6

    def __init__(self, session, *, open_ai_callback=None, launch_ai_callback=None, parent=None):
        super().__init__(parent)
        self.session = session
        self._open_ai_callback = open_ai_callback
        self._launch_ai_callback = launch_ai_callback
        self.handlers = []
        self._refresh_pending = False
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(self.PANEL_MARGIN, self.PANEL_MARGIN, self.PANEL_MARGIN, self.PANEL_MARGIN)
        layout.setSpacing(self.PANEL_GAP)
        self.setLayout(layout)
        self.setObjectName("ActionPadRoot")
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.setStyleSheet(
            "QWidget#ActionPadRoot { background: #171a1d; color: #e6eaee; }"
            "QLabel { color: #dce1e6; background: transparent; border: none; }"
            "QLabel#ActionPadHeader { color: #dce1e6; font-size: 12px; font-weight: 500; }"
            "QLabel#ActionPadStatus { color: #adb6bf; font-size: 11px; }"
            "QPushButton {"
            " background: #22282e;"
            " color: #eef2f5;"
            " border: 1px solid #36424d;"
            " border-radius: 6px;"
            " padding: 1px 8px;"
            " min-height: 22px;"
            " max-height: 28px;"
            " font-size: 12px;"
            " font-weight: 400;"
            "}"
            "QPushButton:hover { background: #2b333a; border-color: #52616f; }"
            "QPushButton:pressed { background: #11161a; }"
            "QPushButton:disabled { color: #68717a; background: #191d21; border-color: #252b31; }"
        )

        fixed_font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)

        header = QLabel("Object actions")
        header.setObjectName("ActionPadHeader")
        layout.addWidget(header)

        toolbar = QGridLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setHorizontalSpacing(self.PANEL_GAP)
        toolbar.setVerticalSpacing(self.PANEL_GAP)
        self.refresh_button = QPushButton("Refresh", self)
        self.refresh_button.clicked.connect(self.refresh)
        toolbar.addWidget(self.refresh_button, 0, 0)

        self.open_ai_button = QPushButton("AI Tab", self)
        self.open_ai_button.clicked.connect(self._open_ai)
        toolbar.addWidget(self.open_ai_button, 0, 1)

        self.pick_residue_button = QPushButton("Residue Pick", self)
        self.pick_residue_button.clicked.connect(lambda: self._set_pick_mode("residue"))
        toolbar.addWidget(self.pick_residue_button, 0, 2)

        self.pick_chain_button = QPushButton("Chain Pick", self)
        self.pick_chain_button.clicked.connect(lambda: self._set_pick_mode("chain"))
        toolbar.addWidget(self.pick_chain_button, 1, 0)

        self.pick_menu_button = QPushButton("Right-click Menu", self)
        self.pick_menu_button.clicked.connect(lambda: self._set_pick_mode("menu"))
        toolbar.addWidget(self.pick_menu_button, 1, 1)

        self.pick_default_button = QPushButton("Default Mouse", self)
        self.pick_default_button.clicked.connect(lambda: self._set_pick_mode("default"))
        toolbar.addWidget(self.pick_default_button, 1, 2)

        self.ai_analyze_button = QPushButton("AI Analyze", self)
        self.ai_analyze_button.clicked.connect(
            lambda: self._launch_ai_prompt("Analyze the current ChimeraX scene with evidence and confidence.", "analyze")
        )
        toolbar.addWidget(self.ai_analyze_button, 2, 0, 1, 2)

        self.conserve3d_button = QPushButton("3D Conserve", self)
        self.conserve3d_button.setToolTip(
            "Highlight sequence-unique residues across the structures currently open and aligned"
        )
        self.conserve3d_button.clicked.connect(self._open_3d_conserve)
        toolbar.addWidget(self.conserve3d_button, 2, 2)

        for column in range(3):
            toolbar.setColumnStretch(column, 1)
        layout.addLayout(toolbar)
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
            button.setMinimumHeight(self.BUTTON_HEIGHT)
            button.setMaximumHeight(self.BUTTON_HEIGHT)
            button.setFixedHeight(self.BUTTON_HEIGHT)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.status_label = QLabel("Ready.", self)
        self.status_label.setFont(fixed_font)
        self.status_label.setObjectName("ActionPadStatus")
        self.status_label.setFixedHeight(16)
        layout.addWidget(self.status_label)

        targets_layout = QVBoxLayout()
        targets_layout.setContentsMargins(0, 0, 0, 0)
        targets_layout.setSpacing(self.PANEL_GAP)
        layout.addLayout(targets_layout, 1)

        self.tree = QTreeWidget(self)
        self.tree.setFont(fixed_font)
        self.tree.setMinimumWidth(0)
        self.tree.setMinimumHeight(150)
        self.tree.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.tree.setColumnCount(6)
        self.tree.setHeaderLabels(["Object", "A", "S", "H", "L", "C"])
        self.tree.setRootIsDecorated(True)
        self.tree.setIndentation(14)
        self.tree.setAlternatingRowColors(True)
        self.tree.itemSelectionChanged.connect(self._update_current_spec_label)
        self.tree.itemDoubleClicked.connect(self._activate_current_item)
        self.tree.setStyleSheet(
            "QTreeWidget {"
            " background: #101214;"
            " color: #edf0f3;"
            " border: 1px solid #343a40;"
            " border-radius: 6px;"
            " alternate-background-color: #171a1d;"
            "}"
            "QHeaderView::section {"
            " background: #171a1d;"
            " color: #d8dde3;"
            " border: 0;"
            " padding: 4px;"
            "}"
        )
        header_view = self.tree.header()
        header_view.setStretchLastSection(False)
        header_view.setMinimumSectionSize(18)
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for index in range(1, 6):
            header_view.setSectionResizeMode(index, QHeaderView.ResizeMode.Fixed)
            self.tree.setColumnWidth(index, 30)
        targets_layout.addWidget(self.tree, 1)

        self.current_spec_label = QLabel("Current target: (none)", self)
        self.current_spec_label.setFont(fixed_font)
        self.current_spec_label.setStyleSheet(
            "QLabel {"
            " background: #12161a;"
            " color: #d9dde2;"
            " border: 1px solid #343a40;"
            " border-radius: 6px;"
            " padding: 6px 8px;"
            "}"
        )
        targets_layout.addWidget(self.current_spec_label)

        current_header = QLabel("Current Target Actions", self)
        current_header.setStyleSheet("QLabel { color: #d8dde3; font-weight: 700; }")
        targets_layout.addWidget(current_header)

        current_actions = QHBoxLayout()
        current_actions.setContentsMargins(0, 0, 0, 0)
        current_actions.setSpacing(self.PANEL_GAP)
        self.current_focus_button = QPushButton("Focus", self)
        self.current_focus_button.clicked.connect(self._focus_current_item)
        current_actions.addWidget(self.current_focus_button)

        self.current_clear_button = QPushButton("Clear Sel", self)
        self.current_clear_button.clicked.connect(lambda: self._run_commands("Selection clear", ["select clear"]))
        current_actions.addWidget(self.current_clear_button)

        self.current_action_button = self._menu_button("A", parent=self)
        current_actions.addWidget(self.current_action_button)

        self.current_show_button = self._menu_button("S", parent=self)
        current_actions.addWidget(self.current_show_button)

        self.current_hide_button = self._menu_button("H", parent=self)
        current_actions.addWidget(self.current_hide_button)

        self.current_label_button = self._menu_button("L", parent=self)
        current_actions.addWidget(self.current_label_button)

        self.current_color_button = self._menu_button("C", parent=self)
        current_actions.addWidget(self.current_color_button)
        current_actions.addStretch(1)
        targets_layout.addLayout(current_actions)
        for button in (self.current_focus_button, self.current_clear_button):
            button.setMinimumHeight(self.BUTTON_HEIGHT)
            button.setMaximumHeight(self.BUTTON_HEIGHT)
            button.setFixedHeight(self.BUTTON_HEIGHT)
            button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        self.pick_mode_label = QLabel("Mouse: default", self)
        self.pick_mode_label.setFont(fixed_font)
        self.pick_mode_label.setStyleSheet("QLabel { color: #95f0b8; background: transparent; border: none; }")
        targets_layout.addWidget(self.pick_mode_label)

    def install_handlers(self):
        if self.handlers:
            return
        self.handlers = [
            self.session.triggers.add_handler("selection changed", self._queue_selection_refresh),
            self.session.triggers.add_handler("command finished", self._queue_command_refresh),
        ]
        self.refresh()

    def cleanup(self):
        for handler in self.handlers:
            try:
                handler.remove()
            except Exception:
                pass
        self.handlers = []

    def refresh(self):
        from .semantic import get_session_semantics

        self._refresh_pending = False
        semantics = get_session_semantics(self.session)
        selected_token = self._selected_session_spec(semantics)

        self.tree.clear()
        model_count = 0
        selection_root = self._section_item("Current Selection")
        selection_count = 0
        for spec in semantics.get("selection", {}).get("ranges", []):
            self._add_spec_item(selection_root, spec, spec, spec, selected_token)
            selection_count += 1
        for spec in semantics.get("selection", {}).get("models", []):
            if any(spec == child.data(0, Qt.ItemDataRole.UserRole) for child in [selection_root.child(i) for i in range(selection_root.childCount())]):
                continue
            self._add_spec_item(selection_root, spec, spec, spec, selected_token)
            selection_count += 1
        if selection_count:
            self.tree.addTopLevelItem(selection_root)
            selection_root.setExpanded(True)

        models_root = self._section_item("Models")
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
            placeholder.setForeground(0, QColor("#89929b"))
            models_root.addChild(placeholder)
        self.tree.addTopLevelItem(models_root)
        models_root.setExpanded(True)

        self._update_current_spec_label()
        self.status_label.setText(f"{model_count} model row(s), {selection_count} selection row(s).")

    def _queue_command_refresh(self, _trigger_name=None, command=None, *_args):
        if _is_selection_only_command_text(command):
            self._queue_selection_refresh()
            return
        self._queue_refresh(delay_ms=150)

    def _queue_selection_refresh(self, *_args, **_kwargs):
        self._queue_refresh(delay_ms=500, require_visible=True)

    def _queue_refresh(self, *_args, delay_ms=150, require_visible=False, **_kwargs):
        if self._refresh_pending:
            return
        self._refresh_pending = True

        def run_refresh():
            self._refresh_pending = False
            if require_visible and not self.isVisible():
                return
            self.refresh()

        QTimer.singleShot(int(delay_ms), lambda: self.session.ui.thread_safe(run_refresh))

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
        return item

    def _add_spec_item(self, parent_item, text, spec, label, selected_token):
        item = QTreeWidgetItem([text, "", "", "", "", ""])
        item.setData(0, Qt.ItemDataRole.UserRole, spec)
        parent_item.addChild(item)
        self._attach_row_buttons(item, spec, label=label)
        if selected_token and (spec == selected_token or selected_token.startswith(spec + ":")):
            item.setSelected(True)
        return item

    def _attach_row_buttons(self, item, spec, label):
        self.tree.setItemWidget(item, 1, self._menu_button("A", self._action_menu(spec, label)))
        self.tree.setItemWidget(item, 2, self._menu_button("S", self._show_menu(spec, label)))
        self.tree.setItemWidget(item, 3, self._menu_button("H", self._hide_menu(spec, label)))
        self.tree.setItemWidget(item, 4, self._menu_button("L", self._label_menu(spec, label)))
        self.tree.setItemWidget(item, 5, self._menu_button("C", self._color_menu(spec, label)))

    def _menu_button(self, text, menu=None, parent=None):
        button = QToolButton(parent or self.tree)
        button.setText(text)
        if menu is not None:
            button.setMenu(menu)
            button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        button.setFixedSize(24, 22)
        button.setStyleSheet(
            "QToolButton {"
            " background: #24282d;"
            " color: #eef1f4;"
            " border: 1px solid #464d55;"
            " border-radius: 4px;"
            " padding: 0px;"
            " font-weight: 700;"
            "}"
            "QToolButton::menu-indicator { image: none; width: 0px; }"
        )
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

    def _update_current_spec_label(self):
        spec = self._current_spec()
        if not spec:
            self.current_spec_label.setText("Current target: (none)")
            self._refresh_current_target_controls(None, None)
            return
        self.current_spec_label.setText(f"Current target: {spec}")
        self._refresh_current_target_controls(spec, self._current_label())

    def _refresh_current_target_controls(self, spec, label):
        controls = (
            self.current_action_button,
            self.current_show_button,
            self.current_hide_button,
            self.current_label_button,
            self.current_color_button,
            self.current_focus_button,
        )
        if not spec:
            for button in controls:
                button.setEnabled(False)
                if isinstance(button, QToolButton):
                    button.setMenu(None)
            return

        self.current_focus_button.setEnabled(True)
        self.current_action_button.setEnabled(True)
        self.current_show_button.setEnabled(True)
        self.current_hide_button.setEnabled(True)
        self.current_label_button.setEnabled(True)
        self.current_color_button.setEnabled(True)
        self.current_action_button.setMenu(self._action_menu(spec, label))
        self.current_show_button.setMenu(self._show_menu(spec, label))
        self.current_hide_button.setMenu(self._hide_menu(spec, label))
        self.current_label_button.setMenu(self._label_menu(spec, label))
        self.current_color_button.setMenu(self._color_menu(spec, label))
        for button in (
            self.current_action_button,
            self.current_show_button,
            self.current_hide_button,
            self.current_label_button,
            self.current_color_button,
        ):
            button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)

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
            self.status_label.setText(str(err) if str(err) else err.__class__.__name__)
            return
        self.pick_mode_label.setText("Mouse: " + message.replace("Left click ", "").replace("Right click ", ""))
        self.status_label.setText(message)


class CodexActionPad(ToolInstance):

    SESSION_ENDURING = False
    SESSION_SAVE = False
    help = "help:user/tools/codex_action_pad.html"
    UI_LAYOUT_VERSION = 16

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
        layout.addWidget(self.widget)
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
