import os
import json
import shlex
import subprocess
import threading

from Qt.QtCore import Qt
from Qt.QtWidgets import QWidget

from chimerax.core.tools import ToolInstance, get_singleton

from .backends import (
    backend_availability,
    backend_status_lines,
    clear_effort_override,
    clear_model_override,
    ensure_session_preferences,
    get_backend_spec,
    get_backend_defaults,
    get_effort_override,
    get_backend_label,
    get_current_backend_id,
    get_model_override,
    get_routing_mode,
    get_speed_profile,
    list_backend_ids,
    resolve_backend_cli,
    resolve_request_quality,
    set_current_backend_id,
    set_effort_override,
    set_model_override,
    set_speed_profile,
    suggested_efforts_for_backend,
    suggested_models_for_backend,
)
from .builtin_actions import list_builtin_commands, recommended_figure_mode, run_builtin_slash, run_figure_mode
from .control_intent import try_handle_control_intent
from .display_color import apply_stick_context_colors, maybe_apply_stick_context_colors_for_command
from .integration import command_batch
from .service import format_memory_compare, run_mode_request


def _is_qt_main_thread():
    try:
        from Qt.QtCore import QCoreApplication, QThread

        app = QCoreApplication.instance()
        return app is not None and QThread.currentThread() == app.thread()
    except Exception:
        return False


class _PromptEditMixin:

    def _init_prompt(self, submit_callback, cycle_mode_callback):
        self._submit_callback = submit_callback
        self._cycle_mode_callback = cycle_mode_callback

    def keyPressEvent(self, event):
        from Qt.QtCore import Qt

        enter_keys = (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        shifted = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if event.key() == Qt.Key.Key_Backtab:
            event.accept()
            self._cycle_mode_callback()
            return
        if event.key() in enter_keys and not shifted:
            if self.isReadOnly():
                event.accept()
                return
            event.accept()
            self._submit_callback()
            return
        super().keyPressEvent(event)


class _DockResizeGripMixin:

    def _init_resize_grip(self, begin_callback, drag_callback, end_callback):
        self._begin_callback = begin_callback
        self._drag_callback = drag_callback
        self._end_callback = end_callback
        self._dragging = False

    def mousePressEvent(self, event):
        from Qt.QtCore import Qt

        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._begin_callback(self._global_x(event))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging:
            self._drag_callback(self._global_x(event))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._dragging = False
        self._end_callback()
        super().mouseReleaseEvent(event)

    def _global_x(self, event):
        if hasattr(event, "globalPosition"):
            try:
                return int(event.globalPosition().x())
            except Exception:
                pass
        if hasattr(event, "globalX"):
            try:
                return int(event.globalX())
            except Exception:
                pass
        return None


class _DockResizeGrip(_DockResizeGripMixin, QWidget):

    def enterEvent(self, event):
        self.setProperty("activeGrip", True)
        self.style().unpolish(self)
        self.style().polish(self)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.setProperty("activeGrip", False)
        self.style().unpolish(self)
        self.style().polish(self)
        super().leaveEvent(event)


class _SelectionPanelWindow(QWidget):

    def _init_selection_window(self, visibility_callback):
        from Qt.QtCore import Qt

        self._visibility_callback = visibility_callback
        self.setWindowFlag(Qt.WindowType.Tool, True)
        self.setWindowTitle("Selection")

    def hideEvent(self, event):
        super().hideEvent(event)
        callback = getattr(self, "_visibility_callback", None)
        if callback is not None:
            callback(False)


class _ResponsiveContent(QWidget):

    def __init__(self, resize_callback, parent=None):
        super().__init__(parent)
        self._resize_callback = resize_callback

    def resizeEvent(self, event):
        super().resizeEvent(event)
        try:
            self._resize_callback(self.width())
        except Exception:
            pass


class CodexAssistant(ToolInstance):

    SESSION_ENDURING = False
    SESSION_SAVE = False
    help = "help:user/tools/codex_assistant.html"
    UI_LAYOUT_VERSION = 42

    @classmethod
    def get_singleton(cls, session, create=True, display=True):
        instance = get_singleton(session, cls, "AI Assistant", create=create, display=display)
        if instance is not None and getattr(instance, "_ui_layout_version", None) != cls.UI_LAYOUT_VERSION:
            try:
                instance.delete()
            except Exception:
                pass
            instance = get_singleton(session, cls, "AI Assistant", create=create, display=display)
        return instance

    def __init__(self, session, tool_name):
        super().__init__(session, tool_name)
        self._ui_layout_version = self.UI_LAYOUT_VERSION
        ensure_session_preferences(session)
        self._mode_order = ["agent", "analyze", "visualize", "chat"]
        self._mode = "agent"
        self._last_request_signature = None
        self._saw_backend_output = False
        self._last_response_text = ""
        self._workspace_suggestions = []
        self._workspace_refresh_pending = False
        self._workspace_visible = False
        self._selection_panel_visible = False
        self._command_terminal_visible = False
        self._selection_controls_updating = False
        self._dock_drag_origin_x = None
        self._dock_drag_origin_width = None
        self._dock_constrained_widgets = []
        self._compact_layout_active = None
        self.handlers = []

        from chimerax.ui import MainToolWindow

        self.tool_window = MainToolWindow(self, close_destroys=True)
        self._build_ui()

    def _build_ui(self):
        from Qt.QtWidgets import (
            QComboBox,
            QGridLayout,
            QHBoxLayout,
            QLabel,
            QMenu,
            QLineEdit,
            QListWidget,
            QListWidgetItem,
            QPlainTextEdit,
            QPushButton,
            QScrollArea,
            QSizePolicy,
            QSlider,
            QSplitter,
            QTabWidget,
            QToolButton,
            QVBoxLayout,
            QWidget,
        )
        from Qt.QtGui import QFont, QFontDatabase
        from Qt.QtCore import Qt

        parent = self.tool_window.ui_area
        outer_layout = QHBoxLayout()
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)
        layout = QVBoxLayout()
        ui_font = parent.font()
        try:
            ui_font.setPointSize(11)
        except Exception:
            pass
        try:
            available_fonts = set(QFontDatabase.families())
        except Exception:
            try:
                available_fonts = set(QFontDatabase().families())
            except Exception:
                available_fonts = set()
        system_fixed = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        mono_family = system_fixed.family() or "Courier New"
        for candidate in ("Menlo", "Monaco", "SF Mono", "Consolas", "Courier New", "Courier"):
            if candidate in available_fonts:
                mono_family = candidate
                break
        fixed_font = QFont(mono_family)
        try:
            fixed_font.setStyleHint(QFont.StyleHint.Monospace)
        except Exception:
            try:
                fixed_font.setStyleHint(QFont.Monospace)
            except Exception:
                pass
        try:
            fixed_font.setPointSize(11)
        except Exception:
            pass
        mono_qss = f' font-family: "{mono_family}"; font-size: 11px;'
        self._fixed_font = fixed_font
        self._mono_qss = mono_qss
        control_arrow = self._icon_path("chevron-down.svg").replace("\\", "/")

        self.resize_grip = _DockResizeGrip(parent)
        self.resize_grip._init_resize_grip(self._begin_dock_resize, self._resize_dock_by_position, self._end_dock_resize)
        self.resize_grip.setProperty("activeGrip", False)
        self.resize_grip.setFixedWidth(22)
        self.resize_grip.setCursor(Qt.CursorShape.SizeHorCursor)
        self.resize_grip.setStyleSheet(
            "QWidget {"
            " background: #171a1d;"
            " border-right: 2px solid #3a4046;"
            "}"
            "QWidget[activeGrip=\"true\"] {"
            " background: #24282d;"
            " border-right: 2px solid #8b949e;"
            "}"
        )
        outer_layout.addWidget(self.resize_grip)

        content_container = _ResponsiveContent(self._apply_responsive_layout, parent)
        content_container.setMinimumWidth(0)
        content_container.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        content_container.setFont(ui_font)
        content_container.setLayout(layout)
        content_container.setStyleSheet(
            "QLabel { color: #d9dde2; font-size: 13px; }"
            "QPushButton, QToolButton {"
            " background: #20252a;"
            " color: #eef1f4;"
            " border: 1px solid #3a424b;"
            " border-radius: 8px;"
            " padding: 5px 9px;"
            " font-size: 13px;"
            "}"
            "QPushButton:hover, QToolButton:hover {"
            " background: #2a3036;"
            " border-color: #59636f;"
            "}"
            "QPushButton:pressed, QToolButton:pressed { background: #191d21; }"
            "QToolButton { padding-right: 24px; }"
            "QToolButton::menu-indicator {"
            f" image: url(\"{control_arrow}\");"
            " subcontrol-origin: padding;"
            " subcontrol-position: center right;"
            " width: 12px;"
            " height: 12px;"
            " right: 8px;"
            "}"
            "QComboBox, QLineEdit {"
            " background: #20252a;"
            " color: #edf0f3;"
            " border: 1px solid #3a424b;"
            " border-radius: 8px;"
            " padding: 5px 30px 5px 9px;"
            " selection-background-color: #3a424a;"
            " font-size: 13px;"
            "}"
            "QComboBox:hover, QLineEdit:hover {"
            " background: #242a30;"
            " border-color: #59636f;"
            "}"
            "QComboBox::drop-down {"
            " subcontrol-origin: padding;"
            " subcontrol-position: top right;"
            " width: 30px;"
            " border: none;"
            " background: #20252a;"
            " border-top-right-radius: 8px;"
            " border-bottom-right-radius: 8px;"
            "}"
            "QComboBox::down-arrow {"
            f" image: url(\"{control_arrow}\");"
            " width: 12px;"
            " height: 12px;"
            "}"
            "QComboBox QAbstractItemView {"
            " background: #1b1f23;"
            " color: #edf0f3;"
            " border: 1px solid #3a424b;"
            " selection-background-color: #2f3740;"
            "}"
            "QComboBox:disabled, QLineEdit:disabled, QPushButton:disabled, QToolButton:disabled {"
            " background: #181c20;"
            " color: #7f8790;"
            " border-color: #2d343b;"
            "}"
        )
        scroll_area = QScrollArea(parent)
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll_area.setFrameStyle(0)
        scroll_area.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        scroll_area.setWidget(content_container)
        outer_layout.addWidget(scroll_area, 1)

        intro = QLabel("AI workspace for ChimeraX. Enter runs, Shift+Enter adds a line.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        top_control_row = QGridLayout()
        top_control_row.setHorizontalSpacing(7)
        top_control_row.setVerticalSpacing(7)
        self.top_control_row = top_control_row
        self.engine_label = QLabel("Engine", parent)
        self.model_label = QLabel("Model", parent)
        self.effort_label = QLabel("Reasoning", parent)
        self.mode_label = QLabel("Mode", parent)
        self.speed_label = QLabel("Speed", parent)
        self.backend_combo = QComboBox(parent)
        self.backend_combo.currentIndexChanged.connect(self._backend_combo_changed)
        self.backend_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.backend_combo.setMinimumContentsLength(9)
        self.backend_combo.setMinimumWidth(0)
        self.backend_combo.setMaximumWidth(380)
        self._populate_backend_combo()

        self.backend_setup_button = QToolButton(parent)
        self.backend_setup_button.setText("Setup")
        self.backend_setup_button.setMinimumWidth(0)
        self.backend_setup_button.setMaximumWidth(120)
        self.backend_setup_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.backend_setup_menu = QMenu(parent)
        self.backend_setup_menu.aboutToShow.connect(self._refresh_backend_setup_menu)
        self.backend_setup_button.setMenu(self.backend_setup_menu)

        self.model_combo = QComboBox(parent)
        self.model_combo.currentIndexChanged.connect(self._model_combo_changed)
        self.model_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.model_combo.setMinimumContentsLength(14)
        self.model_combo.setMinimumWidth(0)
        self.model_combo.setMaximumWidth(460)

        self.effort_combo = QComboBox(parent)
        self.effort_combo.currentIndexChanged.connect(self._effort_combo_changed)
        self.effort_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.effort_combo.setMinimumContentsLength(8)
        self.effort_combo.setMinimumWidth(0)
        self.effort_combo.setMaximumWidth(150)

        self.quick_menu_button = QToolButton(parent)
        self.quick_menu_button.setText("Settings")
        self.quick_menu_button.setMinimumWidth(0)
        self.quick_menu_button.setMaximumWidth(140)
        self.quick_menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.quick_menu_button.setMenu(self._build_quick_menu(parent))

        self.analysis_menu_button = QToolButton(parent)
        self.analysis_menu_button.setText("Analysis")
        self.analysis_menu_button.setMinimumWidth(0)
        self.analysis_menu_button.setMaximumWidth(140)
        self.analysis_menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.analysis_menu_button.setMenu(self._build_analysis_menu(parent))

        self.mode_combo = QComboBox(parent)
        for mode in self._mode_order:
            self.mode_combo.addItem(mode, mode)
        self.mode_combo.currentIndexChanged.connect(self._mode_combo_changed)
        self.mode_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.mode_combo.setMinimumContentsLength(7)
        self.mode_combo.setMinimumWidth(0)
        self.mode_combo.setMaximumWidth(140)

        self.speed_combo = QComboBox(parent)
        for profile in ("auto", "fast", "precise"):
            self.speed_combo.addItem(profile, profile)
        self.speed_combo.currentIndexChanged.connect(self._speed_combo_changed)
        self.speed_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.speed_combo.setMinimumContentsLength(7)
        self.speed_combo.setMinimumWidth(0)
        self.speed_combo.setMaximumWidth(140)

        self.width_slider = QSlider(Qt.Orientation.Horizontal, parent)
        self.width_slider.setRange(10, 50)
        self.width_slider.setFixedWidth(90)
        self.width_slider.valueChanged.connect(self._dock_width_slider_changed)
        self.width_slider.setVisible(False)

        self.width_2to1_button = QPushButton("2:1", parent)
        self.width_2to1_button.clicked.connect(lambda: self._set_dock_fraction(1.0 / 3.0))
        self.width_2to1_button.setVisible(False)

        self.width_equal_button = QPushButton("1:1", parent)
        self.width_equal_button.clicked.connect(lambda: self._set_dock_fraction(0.5))
        self.width_equal_button.setVisible(False)
        self._set_top_controls_compact("narrow")
        layout.addLayout(top_control_row)

        bottom_control_row = QGridLayout()
        bottom_control_row.setHorizontalSpacing(6)
        bottom_control_row.setVerticalSpacing(6)
        self.bottom_control_row = bottom_control_row
        self.refresh_button = QPushButton("Refresh", parent)
        self.refresh_button.clicked.connect(self._refresh_workspace)

        self.toggle_workspace_button = QPushButton("Context", parent)
        self.toggle_workspace_button.clicked.connect(self._toggle_workspace_visibility)

        self.toggle_selection_button = QPushButton("Selection", parent)
        self.toggle_selection_button.clicked.connect(self._toggle_selection_panel_visibility)

        self.toggle_terminal_button = QPushButton("Terminal", parent)
        self.toggle_terminal_button.clicked.connect(self._toggle_command_terminal_visibility)

        self.open_action_pad_button = QPushButton("Actions", parent)
        self.open_action_pad_button.clicked.connect(self._open_action_pad)
        self._set_action_buttons_compact(True)
        layout.addLayout(bottom_control_row)

        sequence_control_row = QGridLayout()
        sequence_control_row.setHorizontalSpacing(6)
        sequence_control_row.setVerticalSpacing(6)
        self.sequence_control_row = sequence_control_row
        self.sequence_status_label = QLabel("Sequence: no protein chain resolved", parent)
        self.sequence_status_label.setWordWrap(True)
        self.sequence_status_label.setMinimumWidth(0)
        self.sequence_status_label.setStyleSheet(
            "QLabel {"
            " background: #171a1d;"
            " color: #e4e7eb;"
            " border: 1px solid #3a4046;"
            " border-radius: 6px;"
            " padding: 6px 8px;"
            "}"
        )
        sequence_control_row.addWidget(self.sequence_status_label, 0, 0, 1, 4)

        self.sequence_strip_edit = None

        self.quick_sequence_bar_button = QPushButton("Top Seq", parent)
        self.quick_sequence_bar_button.clicked.connect(self._toggle_sequence_bar)

        self.quick_sequence_button = QPushButton("Report", parent)
        self.quick_sequence_button.clicked.connect(self._quick_sequence_summary)

        self.quick_motif_button = QPushButton("Motif", parent)
        self.quick_motif_button.clicked.connect(self._quick_motif_summary)

        self.quick_motif_view_button = QPushButton("View", parent)
        self.quick_motif_view_button.clicked.connect(self._quick_motif_view)
        self.quick_catalytic_button = QPushButton("Catalytic", parent)
        self.quick_catalytic_button.clicked.connect(self._quick_catalytic)
        self.quick_membrane_button = QPushButton("Membrane", parent)
        self.quick_membrane_button.clicked.connect(self._quick_membrane)
        self.quick_pisa_button = QPushButton("PISA", parent)
        self.quick_pisa_button.clicked.connect(self._quick_pisa)

        self.quick_blast_button = QPushButton("BLAST", parent)
        self.quick_blast_button.clicked.connect(self._quick_blast)
        self.quick_hhpred_button = QPushButton("HHpred", parent)
        self.quick_hhpred_button.clicked.connect(self._quick_hhpred)
        self.quick_alphafold_button = QPushButton("AlphaFold", parent)
        self.quick_alphafold_button.clicked.connect(self._quick_alphafold)
        self.quick_profile_button = QPushButton("UniProt", parent)
        self.quick_profile_button.clicked.connect(self._quick_profile)
        self.quick_conservation_button = QPushButton("ConSurf", parent)
        self.quick_conservation_button.clicked.connect(self._quick_conservation)
        self.quick_afcomplex_button = QPushButton("AF Complex", parent)
        self.quick_afcomplex_button.clicked.connect(self._quick_afcomplex)
        self.quick_boltz_button = QPushButton("Boltz", parent)
        self.quick_boltz_button.clicked.connect(self._quick_boltz)
        self.quick_similar_web_button = QPushButton("Foldseek", parent)
        self.quick_similar_web_button.clicked.connect(self._quick_similar_web)
        self.quick_foldmason_button = QPushButton("FoldMason", parent)
        self.quick_foldmason_button.clicked.connect(self._quick_foldmason)
        self.quick_folddisco_button = QPushButton("FoldDisco", parent)
        self.quick_folddisco_button.clicked.connect(self._quick_folddisco)
        self.quick_nucdock_button = QPushButton("NucDock", parent)
        self.quick_nucdock_button.clicked.connect(self._quick_nucdock)
        self.quick_dali_button = QPushButton("DALI", parent)
        self.quick_dali_button.clicked.connect(self._quick_dali)
        self.quick_vast_button = QPushButton("VAST", parent)
        self.quick_vast_button.clicked.connect(self._quick_vast)
        self.quick_pdbefold_button = QPushButton("PDBeFold", parent)
        self.quick_pdbefold_button.clicked.connect(self._quick_pdbefold)
        self.quick_usalign_button = QPushButton("US-align", parent)
        self.quick_usalign_button.clicked.connect(self._quick_usalign)
        self.quick_selection_menu_button = QToolButton(parent)
        self.quick_selection_menu_button.setText("Selection")
        self.quick_selection_menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.quick_selection_menu_button.setMenu(self._build_selection_menu(parent))
        self._apply_analysis_button_icons()
        self._hide_removed_quick_analysis_buttons()
        self._set_sequence_buttons_compact(True)
        layout.addLayout(sequence_control_row)

        selection_panel = _SelectionPanelWindow()
        selection_panel._init_selection_window(self._set_selection_panel_visible)
        selection_panel.setStyleSheet(
            "QWidget {"
            " background: #171a1d;"
            " border: 1px solid #3a4046;"
            " border-radius: 8px;"
            "}"
            "QLabel { color: #d9dde2; }"
            "QLineEdit, QComboBox {"
            " background: #101214;"
            " color: #edf0f3;"
            " border: 1px solid #3a4046;"
            " border-radius: 6px;"
            " padding: 4px 6px;"
            "}"
            "QPushButton {"
            " background: #24282d;"
            " color: #eef1f4;"
            " border: 1px solid #464d55;"
            " border-radius: 6px;"
            " padding: 5px 8px;"
            "}"
        )
        selection_layout = QVBoxLayout()
        selection_layout.setContentsMargins(10, 10, 10, 10)
        selection_layout.setSpacing(6)
        selection_panel.setLayout(selection_layout)
        self.selection_panel = selection_panel
        selection_panel.resize(520, 220)

        selection_header = QHBoxLayout()
        selection_label = QLabel("Selection Bar", selection_panel)
        selection_label.setStyleSheet("QLabel { color: #d8dde3; font-weight: 700; }")
        selection_header.addWidget(selection_label)
        self.selection_use_current_button = QPushButton("Use Current", selection_panel)
        self.selection_use_current_button.clicked.connect(self._apply_current_selection_to_controls)
        selection_header.addWidget(self.selection_use_current_button)
        selection_layout.addLayout(selection_header)

        selection_grid = QGridLayout()
        selection_grid.setHorizontalSpacing(8)
        selection_grid.setVerticalSpacing(6)
        selection_layout.addLayout(selection_grid)

        selection_grid.addWidget(QLabel("Model", selection_panel), 0, 0)
        self.selection_model_combo = QComboBox(selection_panel)
        self.selection_model_combo.currentIndexChanged.connect(self._selection_model_changed)
        selection_grid.addWidget(self.selection_model_combo, 0, 1)

        selection_grid.addWidget(QLabel("Chain", selection_panel), 0, 2)
        self.selection_chain_combo = QComboBox(selection_panel)
        self.selection_chain_combo.currentIndexChanged.connect(self._update_selection_spec_preview)
        selection_grid.addWidget(self.selection_chain_combo, 0, 3)

        selection_grid.addWidget(QLabel("Start", selection_panel), 1, 0)
        self.selection_start_edit = QLineEdit(selection_panel)
        self.selection_start_edit.setPlaceholderText("45")
        self.selection_start_edit.textChanged.connect(self._update_selection_spec_preview)
        selection_grid.addWidget(self.selection_start_edit, 1, 1)

        selection_grid.addWidget(QLabel("End", selection_panel), 1, 2)
        self.selection_end_edit = QLineEdit(selection_panel)
        self.selection_end_edit.setPlaceholderText("78")
        self.selection_end_edit.textChanged.connect(self._update_selection_spec_preview)
        selection_grid.addWidget(self.selection_end_edit, 1, 3)

        selection_grid.addWidget(QLabel("Rep", selection_panel), 2, 0)
        self.selection_rep_combo = QComboBox(selection_panel)
        self.selection_rep_combo.addItem("cartoon", "cartoon")
        self.selection_rep_combo.addItem("sticks", "sticks")
        self.selection_rep_combo.addItem("surface", "surface")
        selection_grid.addWidget(self.selection_rep_combo, 2, 1)

        self.selection_spec_label = QLabel("Spec: (none)", selection_panel)
        self.selection_spec_label.setStyleSheet(
            "QLabel {"
            " background: #101214;"
            " color: #e4e7eb;"
            " border: 1px solid #3a4046;"
            " border-radius: 6px;"
            " padding: 5px 8px;"
            "}"
        )
        selection_grid.addWidget(self.selection_spec_label, 2, 2, 1, 2)

        selection_actions = QHBoxLayout()
        self.selection_select_button = QPushButton("Select", selection_panel)
        self.selection_select_button.clicked.connect(self._selection_select)
        selection_actions.addWidget(self.selection_select_button)

        self.selection_focus_button = QPushButton("Focus", selection_panel)
        self.selection_focus_button.clicked.connect(self._selection_focus)
        selection_actions.addWidget(self.selection_focus_button)

        self.selection_show_button = QPushButton("Show", selection_panel)
        self.selection_show_button.clicked.connect(self._selection_show)
        selection_actions.addWidget(self.selection_show_button)

        self.selection_hide_button = QPushButton("Hide", selection_panel)
        self.selection_hide_button.clicked.connect(self._selection_hide)
        selection_actions.addWidget(self.selection_hide_button)

        self.selection_analyze_button = QPushButton("Analyze", selection_panel)
        self.selection_analyze_button.clicked.connect(self._selection_analyze)
        selection_actions.addWidget(self.selection_analyze_button)

        self.selection_clear_button = QPushButton("Clear Sel", selection_panel)
        self.selection_clear_button.clicked.connect(self._selection_clear)
        selection_actions.addWidget(self.selection_clear_button)
        selection_layout.addLayout(selection_actions)

        self.session_status_label = QLabel("Session: initializing", parent)
        self.session_status_label.setFont(fixed_font)
        self.session_status_label.setWordWrap(True)
        self.session_status_label.setMinimumWidth(0)
        self.session_status_label.setStyleSheet(
            "QLabel {"
            " background: #171a1d;"
            " color: #d9dde2;"
            " border: 1px solid #3a4046;"
            " border-radius: 6px;"
            " padding: 6px 8px;"
            f"{mono_qss}"
            "}"
        )
        layout.addWidget(self.session_status_label)

        self.result_status_label = QLabel("Result: ready", parent)
        self.result_status_label.setFont(fixed_font)
        self.result_status_label.setWordWrap(True)
        self.result_status_label.setMinimumWidth(0)
        self.result_status_label.setStyleSheet(
            "QLabel {"
            " background: #15181b;"
            " color: #c7ccd2;"
            " border: 1px solid #343a40;"
            " border-radius: 6px;"
            " padding: 6px 8px;"
            f"{mono_qss}"
            "}"
        )
        layout.addWidget(self.result_status_label)

        self.error_banner_label = QLabel("", parent)
        self.error_banner_label.setWordWrap(True)
        self.error_banner_label.setVisible(False)
        self.error_banner_label.setStyleSheet(
            "QLabel {"
            " background: #2a1116;"
            " color: #ffdce1;"
            " border: 1px solid #8a3c48;"
            " border-radius: 8px;"
            " padding: 8px 10px;"
            " font-weight: 700;"
            "}"
        )
        layout.addWidget(self.error_banner_label)

        self.result_detail_edit = QPlainTextEdit(parent)
        self.result_detail_edit.setReadOnly(True)
        self.result_detail_edit.setFont(fixed_font)
        self.result_detail_edit.setFixedHeight(88)
        self.result_detail_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.result_detail_edit.setPlaceholderText("Recent result details will appear here.")
        self.result_detail_edit.setStyleSheet(
            "QPlainTextEdit {"
            " background: #101214;"
            " color: #d9dde2;"
            " border: 1px solid #343a40;"
            " border-radius: 8px;"
            " padding: 7px;"
            f"{mono_qss}"
            "}"
        )
        layout.addWidget(self.result_detail_edit)

        self.content_tabs = QTabWidget(parent)
        self.content_tabs.setStyleSheet(
            "QTabWidget::pane { border: 1px solid #343a40; border-radius: 12px; }"
            "QTabBar::tab { background: #111315; color: #aeb4bb; padding: 8px 14px; margin-right: 2px; border-top-left-radius: 8px; border-top-right-radius: 8px; }"
            "QTabBar::tab:selected { background: #2a3035; color: #f0f2f4; }"
        )
        self.content_tabs.setMinimumWidth(0)
        self.content_tabs.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        layout.addWidget(self.content_tabs, 1)

        assistant_page = QWidget(parent)
        assistant_page_layout = QVBoxLayout()
        assistant_page_layout.setContentsMargins(6, 6, 6, 6)
        assistant_page_layout.setSpacing(6)
        assistant_page.setLayout(assistant_page_layout)
        self.content_tabs.addTab(assistant_page, "AI")

        self.action_pad_widget = None

        self.splitter = None

        workspace_panel = QWidget(parent)
        workspace_layout = QVBoxLayout()
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(6)
        workspace_panel.setLayout(workspace_layout)
        self.workspace_panel = workspace_panel
        self.workspace_panel.setMinimumWidth(0)
        self.workspace_panel.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)

        workspace_label = QLabel("Workspace", workspace_panel)
        workspace_label.setStyleSheet("QLabel { color: #d8dde3; font-weight: 700; }")
        workspace_layout.addWidget(workspace_label)

        self.workspace_edit = QPlainTextEdit(workspace_panel)
        self.workspace_edit.setReadOnly(True)
        self.workspace_edit.setFont(fixed_font)
        self.workspace_edit.setPlaceholderText("Live ChimeraX context will appear here.")
        self.workspace_edit.setStyleSheet(
            "QPlainTextEdit {"
            " background: #101214;"
            " color: #d9dde2;"
            " border: 1px solid #343a40;"
            " border-radius: 6px;"
            " padding: 6px;"
            f"{mono_qss}"
            "}"
        )
        workspace_layout.addWidget(self.workspace_edit, 1)

        suggestion_header = QHBoxLayout()
        suggestion_label = QLabel("Interactive Suggestions", workspace_panel)
        suggestion_label.setStyleSheet("QLabel { color: #d8dde3; font-weight: 700; }")
        suggestion_header.addWidget(suggestion_label)
        self.use_suggestion_button = QPushButton("To Prompt", workspace_panel)
        self.use_suggestion_button.clicked.connect(self._copy_selected_suggestion_to_prompt)
        suggestion_header.addWidget(self.use_suggestion_button)
        workspace_layout.addLayout(suggestion_header)

        self.suggestion_list = QListWidget(workspace_panel)
        self.suggestion_list.itemDoubleClicked.connect(self._apply_suggestion_item)
        self.suggestion_list.setStyleSheet(
            "QListWidget {"
            " background: #101214;"
            " color: #edf0f3;"
            " border: 1px solid #343a40;"
            " border-radius: 6px;"
            " padding: 4px;"
            "}"
            "QListWidget::item:selected {"
            " background: #3a424a;"
            "}"
        )
        workspace_layout.addWidget(self.suggestion_list, 1)

        assistant_panel = QWidget(parent)
        assistant_panel.setStyleSheet(
            "QWidget {"
            " background: #11100d;"
            "}"
        )
        assistant_panel.setMinimumWidth(0)
        assistant_panel.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        assistant_layout = QVBoxLayout()
        assistant_layout.setContentsMargins(0, 0, 0, 0)
        assistant_layout.setSpacing(6)
        assistant_panel.setLayout(assistant_layout)
        self.assistant_vertical_splitter = QSplitter(assistant_panel)
        self.assistant_vertical_splitter.setOrientation(Qt.Orientation.Vertical)
        self.assistant_vertical_splitter.setChildrenCollapsible(False)
        self.assistant_vertical_splitter.setHandleWidth(10)
        self.assistant_vertical_splitter.setStyleSheet(
            "QSplitter::handle {"
            " background: #24282d;"
            " border: 1px solid #3a4046;"
            " margin: 1px 0;"
            "}"
        )
        assistant_layout.addWidget(self.assistant_vertical_splitter, 1)

        transcript_panel = QWidget(assistant_panel)
        transcript_layout = QVBoxLayout()
        transcript_layout.setContentsMargins(0, 0, 0, 0)
        transcript_layout.setSpacing(6)
        transcript_panel.setLayout(transcript_layout)

        self.ai_header_label = QLabel("", parent)
        self.ai_header_label.setFont(fixed_font)
        self.ai_header_label.setStyleSheet(
            "QLabel {"
            " background: transparent;"
            " color: #c6b79d;"
            " border: none;"
            " padding: 0px;"
            f"{mono_qss}"
            "}"
        )
        self.ai_header_label.setVisible(False)
        transcript_layout.addWidget(self.ai_header_label)

        self.terminal_edit = QPlainTextEdit(parent)
        self.terminal_edit.setReadOnly(True)
        self.terminal_edit.setPlaceholderText("AI activity and command results will appear here.")
        self.terminal_edit.setFont(fixed_font)
        self.terminal_edit.setStyleSheet(
            "QPlainTextEdit {"
            " background: #0b0d0f;"
            " color: #d9dde2;"
            " border: 1px solid #343a40;"
            " border-radius: 10px;"
            " padding: 10px;"
            " selection-background-color: #3a424a;"
            f"{mono_qss}"
            "}"
        )
        transcript_layout.addWidget(self.terminal_edit, 1)

        interaction_panel = QWidget(assistant_panel)
        interaction_layout = QVBoxLayout()
        interaction_layout.setContentsMargins(0, 0, 0, 0)
        interaction_layout.setSpacing(6)
        interaction_panel.setLayout(interaction_layout)
        self.interaction_vertical_splitter = QSplitter(interaction_panel)
        self.interaction_vertical_splitter.setOrientation(Qt.Orientation.Vertical)
        self.interaction_vertical_splitter.setChildrenCollapsible(False)
        self.interaction_vertical_splitter.setHandleWidth(10)
        self.interaction_vertical_splitter.setStyleSheet(
            "QSplitter::handle {"
            " background: #24282d;"
            " border: 1px solid #3a4046;"
            " margin: 1px 0;"
            "}"
        )
        interaction_layout.addWidget(self.interaction_vertical_splitter, 1)

        prompt_panel = QWidget(interaction_panel)
        self.prompt_panel = prompt_panel
        self.prompt_panel.setMinimumHeight(96)
        self.prompt_panel.setMaximumHeight(150)
        self.prompt_panel.setObjectName("PromptPanel")
        self.prompt_panel.setStyleSheet(
            "QWidget#PromptPanel {"
            " background: #111315;"
            " border: 1px solid #343a40;"
            " border-radius: 12px;"
            "}"
        )
        prompt_layout = QVBoxLayout()
        prompt_layout.setContentsMargins(10, 9, 10, 10)
        prompt_layout.setSpacing(7)
        prompt_panel.setLayout(prompt_layout)

        self.stage_label = QLabel("Ready", parent)
        self.stage_label.setFont(fixed_font)
        self.stage_label.setWordWrap(True)
        self.stage_label.setMinimumWidth(0)
        self.stage_label.setStyleSheet(
            "QLabel {"
            " background: transparent;"
            " color: #c2b49c;"
            " border: none;"
            " padding: 0px;"
            f"{mono_qss}"
            "}"
        )
        prompt_layout.addWidget(self.stage_label)

        self.status_label = QLabel("Ready.", parent)
        self.status_label.setFont(fixed_font)
        self.status_label.setWordWrap(True)
        self.status_label.setMinimumWidth(0)
        self.status_label.setStyleSheet("QLabel { color: #b7aa94;" + mono_qss + " }")
        self.status_label.setVisible(False)
        prompt_layout.addWidget(self.status_label)

        prompt_header = QLabel("AI Prompt", parent)
        prompt_header.setFont(fixed_font)
        prompt_header.setStyleSheet("QLabel { color: #d8dde3; font-weight: 700;" + mono_qss + " }")
        prompt_header.setVisible(False)
        prompt_layout.addWidget(prompt_header)

        prompt_row = QHBoxLayout()
        prompt_label = QLabel(">", parent)
        prompt_label.setFont(fixed_font)
        prompt_label.setAlignment(Qt.AlignmentFlag.AlignTop)
        prompt_label.setStyleSheet("QLabel { color: #d8dde3; padding-top: 8px;" + mono_qss + " }")
        prompt_label.setVisible(False)
        prompt_row.addWidget(prompt_label)

        class _PromptEdit(_PromptEditMixin, QPlainTextEdit):
            pass

        self.prompt_edit = _PromptEdit(parent)
        self.prompt_edit._init_prompt(self._submit_prompt, self._cycle_mode)
        self.prompt_edit.setPlaceholderText(
            "Ask for a ChimeraX action..."
        )
        self.prompt_edit.setFont(fixed_font)
        self.prompt_edit.setTabChangesFocus(True)
        self.prompt_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.prompt_edit.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.prompt_edit.setStyleSheet(
            "QPlainTextEdit {"
            " background: #0b0d0f;"
            " color: #edf0f3;"
            " border: 1px solid #3a4046;"
            " border-radius: 10px;"
            " padding: 8px;"
            " selection-background-color: #3a424a;"
            f"{mono_qss}"
            "}"
            "QPlainTextEdit:focus { border: 1px solid #8b949e; }"
        )
        self.prompt_edit.textChanged.connect(self._resize_prompt)
        prompt_row.addWidget(self.prompt_edit, 1)
        self.prompt_run_button = QPushButton("Run", parent)
        self.prompt_run_button.clicked.connect(self._submit_and_run)
        self.prompt_run_button.setMinimumHeight(38)
        self.prompt_run_button.setStyleSheet(
            "QPushButton {"
            " background: #30363d;"
            " color: #f0f2f4;"
            " border: 1px solid #59616b;"
            " border-radius: 9px;"
            " padding: 8px 14px;"
            " font-weight: 700;"
            "}"
            "QPushButton:hover { background: #3a4149; }"
            "QPushButton:pressed { background: #1f2429; }"
        )
        prompt_row.addWidget(self.prompt_run_button)
        prompt_layout.addLayout(prompt_row)
        self._resize_prompt()

        terminal_panel = QWidget(interaction_panel)
        self.terminal_panel = terminal_panel
        terminal_layout = QVBoxLayout()
        terminal_layout.setContentsMargins(0, 0, 0, 0)
        terminal_layout.setSpacing(6)
        terminal_panel.setLayout(terminal_layout)

        terminal_label = QLabel("In-App Terminal", parent)
        terminal_label.setStyleSheet("QLabel { color: #d8dde3; font-weight: 700; }")
        terminal_layout.addWidget(terminal_label)
        self.command_terminal_label = terminal_label

        self.command_terminal_output = QPlainTextEdit(parent)
        self.command_terminal_output.setReadOnly(True)
        self.command_terminal_output.setFont(fixed_font)
        self.command_terminal_output.setPlaceholderText("Run raw ChimeraX commands here. Prefix shell commands with !")
        self.command_terminal_output.setStyleSheet(
            "QPlainTextEdit {"
            " background: #101214;"
            " color: #d9e2cf;"
            " border: 1px solid #4a5334;"
            " border-radius: 6px;"
            " padding: 6px;"
            f"{mono_qss}"
            "}"
        )
        self.command_terminal_output.setMinimumHeight(60)
        terminal_layout.addWidget(self.command_terminal_output, 1)

        terminal_row = QHBoxLayout()
        terminal_prompt = QLabel("cx>", parent)
        terminal_prompt.setFont(fixed_font)
        terminal_prompt.setStyleSheet("QLabel { color: #d8dde3;" + mono_qss + " }")
        terminal_row.addWidget(terminal_prompt)

        self.command_terminal_input = QLineEdit(parent)
        self.command_terminal_input.setFont(fixed_font)
        self.command_terminal_input.setPlaceholderText("show sel   or   !pwd")
        self.command_terminal_input.setStyleSheet(
            "QLineEdit {"
            " background: #20252a;"
            " color: #edf0f3;"
            " border: 1px solid #3a424b;"
            " border-radius: 8px;"
            " padding: 5px 9px;"
            " selection-background-color: #3a424a;"
            f"{mono_qss}"
            "}"
        )
        self.command_terminal_input.returnPressed.connect(self._run_terminal_command)
        terminal_row.addWidget(self.command_terminal_input, 1)

        self.command_terminal_run_button = QPushButton("Run", parent)
        self.command_terminal_run_button.clicked.connect(self._run_terminal_command)
        terminal_row.addWidget(self.command_terminal_run_button)

        self.command_terminal_clear_button = QPushButton("Clear", parent)
        self.command_terminal_clear_button.clicked.connect(self.command_terminal_output.clear)
        terminal_row.addWidget(self.command_terminal_clear_button)
        terminal_layout.addLayout(terminal_row)
        self.command_terminal_row = terminal_row

        self.interaction_vertical_splitter.addWidget(prompt_panel)
        self.interaction_vertical_splitter.addWidget(terminal_panel)
        self.interaction_vertical_splitter.setStretchFactor(0, 2)
        self.interaction_vertical_splitter.setStretchFactor(1, 1)
        self.interaction_vertical_splitter.setSizes([135, 120])

        self.assistant_vertical_splitter.addWidget(transcript_panel)
        self.assistant_vertical_splitter.addWidget(interaction_panel)
        self.assistant_vertical_splitter.setStretchFactor(0, 1)
        self.assistant_vertical_splitter.setStretchFactor(1, 0)
        self.assistant_vertical_splitter.setSizes([620, 150])

        assistant_page_layout.addWidget(assistant_panel, 1)
        self.workspace_tab_index = self.content_tabs.insertTab(1, workspace_panel, "Context")

        self._make_small_screen_friendly(content_container)
        self._compact_layout_active = None
        self._apply_responsive_layout(content_container.width() or 640)
        parent.setLayout(outer_layout)
        self.tool_window.manage(placement="side")
        self._sync_control_widgets()
        self.handlers = [
            self.session.triggers.add_handler("selection changed", self._queue_workspace_refresh),
            self.session.triggers.add_handler("command finished", self._queue_workspace_refresh),
        ]
        self._set_result_detail("No recent result.")
        self._refresh_workspace()
        self._set_selection_panel_visible(False)
        self._set_workspace_visible(False)
        self._set_command_terminal_visible(False)
        self._apply_dock_fraction()
        self._show_assistant_tab()
        self._append_system(
            f"engine {self._backend_label()} · mode {self._mode} · speed {self._speed_text(self._mode)}"
        )
        self._append_system("Enter runs · Shift+Enter newline · /help lists commands · /figure clean makes a clean protein view")

    def _submit_prompt(self):
        if self.prompt_edit.isReadOnly():
            return
        prompt = self.prompt_edit.toPlainText().strip()
        if not prompt:
            self._show_error("Enter a prompt first.")
            return
        if prompt.startswith("/"):
            self.prompt_edit.clear()
            self._handle_slash_command(prompt)
            return
        if self._handle_mode_command(prompt):
            self.prompt_edit.clear()
            return
        control_result = try_handle_control_intent(self.session, prompt)
        if control_result is not None:
            self.prompt_edit.clear()
            for line in control_result.splitlines():
                self._append_system(line)
            self._sync_control_widgets()
            self._queue_workspace_refresh()
            return
        if self._handle_plain_command(prompt):
            self.prompt_edit.clear()
            return
        self._start_request("run", prompt)

    def _submit_and_run(self):
        self._submit_prompt()

    def _start_request(self, mode, prompt):
        if self.prompt_edit.isReadOnly():
            self._append_error("an AI request is already running")
            return

        include_models = True
        include_selection = True
        request_mode = self._request_mode_for_action(mode)
        fast_mode = self._effective_fast_mode(request_mode)
        speed_text = self._speed_text(request_mode)

        self.prompt_edit.setReadOnly(True)
        self.status_label.setText("Checking local route...")
        self.stage_label.setText(f"Routing · {request_mode} · {speed_text}")
        self._set_result_status(f"routing {request_mode} request", tone="running")
        self._saw_backend_output = False
        self._append_terminal("")
        self._append_terminal(f"> {prompt}")
        self._append_system(f"stage: start")
        signature = (
            self._mode,
            get_current_backend_id(self.session),
            speed_text,
            self._active_model_display(request_mode),
            self._active_effort_display(request_mode),
        )
        if signature != self._last_request_signature:
            self._append_system(f"mode: {self._mode}")
            self._append_system(f"backend: {get_current_backend_id(self.session)}")
            self._append_system(f"speed: {speed_text}")
            self._append_system(f"model: {self._active_model_display(request_mode)}")
            self._append_system(f"effort: {self._active_effort_display(request_mode)}")
            self._last_request_signature = signature
        self.prompt_edit.clear()

        worker = threading.Thread(
            target=self._run_codex_request,
            args=(mode, prompt, include_models, include_selection, fast_mode),
            daemon=True,
        )
        worker.start()

    def _run_codex_request(self, mode, prompt, include_models, include_selection, fast_mode):
        try:
            if mode == "run":
                with command_batch(self.session, f"AI:{self._mode}:{prompt[:80]}"):
                    summary = run_mode_request(
                        self.session,
                        prompt,
                        mode=self._mode,
                        progress=self._progress_callback(),
                        fast=fast_mode,
                        executor=self._run_command_thread_safe,
                    )
                self.session.ui.thread_safe(lambda s=summary: self._show_response(s, mode="run"))
                return

            response = run_mode_request(
                self.session,
                prompt,
                mode="chat",
                progress=self._progress_callback(),
                fast=fast_mode,
            )
        except Exception as err:
            message = str(err) if str(err) else err.__class__.__name__
            self.session.ui.thread_safe(lambda m=message: self._show_error(m))
            return

        self.session.ui.thread_safe(lambda r=response: self._show_response(r, mode="ask"))

    def _show_response(self, response, mode="ask"):
        self.prompt_edit.setReadOnly(False)
        self.prompt_edit.setFocus()
        self.status_label.setText("Reply ready.")
        self.stage_label.setText("Complete")
        self._clear_error_banner()
        self._last_response_text = str(response or "")
        if mode == "run" or not self._saw_backend_output:
            for line in response.splitlines() or [""]:
                self._append_terminal(line)
        response_suggestions = self._response_suggestions(response)
        if response_suggestions:
            self._set_workspace_suggestions(response_suggestions)
        else:
            self._set_workspace_suggestions(self._default_workspace_suggestions())
        self.session.logger.status(f"{self._backend_label()} reply ready.")
        self._append_system("stage: complete")
        if self._response_indicates_no_visual_change(response):
            self._append_error("no visual change was applied")
        else:
            self._append_system("done")
        result_text, result_tone = self._summarize_result_status(response)
        self._set_result_status(result_text, tone=result_tone)
        self._set_result_detail(self._result_detail_text(response))
        self._queue_workspace_refresh()

    def _show_error(self, message):
        self.prompt_edit.setReadOnly(False)
        self.prompt_edit.setFocus()
        self.status_label.setText("Error.")
        self.stage_label.setText("Error")
        self._append_error(message)
        self.session.logger.error(message)
        self._set_result_status(message, tone="error")
        self._set_result_detail(message)
        self._queue_workspace_refresh()

    def delete(self):
        try:
            if self.action_pad_widget is not None:
                self.action_pad_widget.cleanup()
        except Exception:
            pass
        for handler in self.handlers:
            try:
                handler.remove()
            except Exception:
                pass
        self.handlers = []
        super().delete()

    def _clear_output(self):
        self.status_label.setText("Ready.")
        self.stage_label.setText("Ready")
        self.terminal_edit.clear()
        self._last_response_text = ""
        self._set_workspace_suggestions(self._default_workspace_suggestions())
        self._set_result_status("ready", tone="neutral")
        self._set_result_detail("No recent result.")
        self._clear_error_banner()
        self._append_system("cleared")

    def _queue_workspace_refresh(self, *_args, **_kwargs):
        if self._workspace_refresh_pending:
            return
        self._workspace_refresh_pending = True

        def refresh():
            self._refresh_workspace()

        self.session.ui.thread_safe(refresh)

    def _refresh_workspace(self):
        self._workspace_refresh_pending = False
        self._sync_control_widgets()
        self._refresh_selection_controls()
        self.workspace_edit.setPlainText(self._workspace_text())
        self._update_sequence_status()
        if not self._workspace_suggestions:
            self._set_workspace_suggestions(self._default_workspace_suggestions())
        self._update_session_status()

    def _make_small_screen_friendly(self, root):
        from Qt.QtWidgets import QComboBox, QLineEdit, QPushButton, QToolButton, QSizePolicy

        for widget in root.findChildren(QPushButton):
            widget.setMinimumWidth(0)
            widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        for widget in root.findChildren(QToolButton):
            widget.setMinimumWidth(0)
            widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        for widget in root.findChildren(QComboBox):
            widget.setMinimumWidth(0)
            widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        for widget in root.findChildren(QLineEdit):
            widget.setMinimumWidth(0)
            widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)

    def _clear_grid_layout(self, grid):
        while grid.count():
            item = grid.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                try:
                    if widget.property("codexDynamicTopControlRow"):
                        widget.deleteLater()
                except Exception:
                    pass
        for column in range(10):
            try:
                grid.setColumnStretch(column, 0)
                grid.setColumnMinimumWidth(column, 0)
            except Exception:
                pass
        for row in range(10):
            try:
                grid.setRowStretch(row, 0)
                grid.setRowMinimumHeight(row, 0)
            except Exception:
                pass

    def _apply_responsive_layout(self, width):
        width = int(width or 0)
        if width < 430:
            layout_mode = "narrow"
        elif width < 920:
            layout_mode = "compact"
        else:
            layout_mode = "wide"
        if layout_mode == self._compact_layout_active:
            return
        self._compact_layout_active = layout_mode
        self._set_top_controls_compact(layout_mode)
        compact = layout_mode != "wide"
        self._set_action_buttons_compact(compact)
        self._set_sequence_buttons_compact(compact)

    def _set_top_controls_compact(self, layout_mode):
        from Qt.QtWidgets import QHBoxLayout, QSizePolicy, QWidget

        grid = getattr(self, "top_control_row", None)
        if grid is None:
            return
        if layout_mode is True:
            layout_mode = "narrow"
        elif layout_mode is False:
            layout_mode = "wide"
        self._clear_grid_layout(grid)
        max_qt_width = 16777215
        labels = (
            self.engine_label,
            self.model_label,
            self.effort_label,
            self.mode_label,
            self.speed_label,
        )
        controls = (
            self.backend_combo,
            self.model_combo,
            self.effort_combo,
            self.mode_combo,
            self.speed_combo,
            self.backend_setup_button,
            self.quick_menu_button,
            self.analysis_menu_button,
        )
        label_alignment = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        fixed_control_alignment = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        for label in labels:
            label.setAlignment(label_alignment)

        def expand_control(widget, min_width):
            widget.setMinimumWidth(min_width)
            widget.setMaximumWidth(max_qt_width)
            widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        def fixed_control(widget, width):
            widget.setMinimumWidth(width)
            widget.setMaximumWidth(width)
            widget.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        def fixed_label(widget, width):
            widget.setMinimumWidth(width)
            widget.setMaximumWidth(width)
            widget.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        def top_row(*widgets):
            row = QWidget(grid.parentWidget())
            row.setProperty("codexDynamicTopControlRow", True)
            row_layout = QHBoxLayout()
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(7)
            row.setLayout(row_layout)
            for widget in widgets:
                row_layout.addWidget(widget)
            row_layout.addStretch(1)
            return row

        if layout_mode == "narrow":
            grid.setAlignment(Qt.AlignmentFlag.AlignTop)
            expand_control(self.backend_combo, 180)
            expand_control(self.model_combo, 180)
            fixed_control(self.effort_combo, 160)
            fixed_control(self.mode_combo, 130)
            fixed_control(self.speed_combo, 130)
            fixed_control(self.backend_setup_button, 130)
            fixed_control(self.quick_menu_button, 135)
            fixed_control(self.analysis_menu_button, 135)
            for label in labels:
                label.setMinimumWidth(88)
                label.setMaximumWidth(88)
            grid.addWidget(self.engine_label, 0, 0, 1, 1, label_alignment)
            grid.addWidget(self.backend_combo, 0, 1)
            grid.addWidget(self.model_label, 1, 0, 1, 1, label_alignment)
            grid.addWidget(self.model_combo, 1, 1)
            grid.addWidget(self.effort_label, 2, 0, 1, 1, label_alignment)
            grid.addWidget(self.effort_combo, 2, 1, 1, 1, fixed_control_alignment)
            grid.addWidget(self.mode_label, 3, 0, 1, 1, label_alignment)
            grid.addWidget(self.mode_combo, 3, 1, 1, 1, fixed_control_alignment)
            grid.addWidget(self.speed_label, 4, 0, 1, 1, label_alignment)
            grid.addWidget(self.speed_combo, 4, 1, 1, 1, fixed_control_alignment)
            grid.addWidget(self.backend_setup_button, 5, 1, 1, 1, fixed_control_alignment)
            grid.addWidget(self.quick_menu_button, 6, 1, 1, 1, fixed_control_alignment)
            grid.addWidget(self.analysis_menu_button, 7, 1, 1, 1, fixed_control_alignment)
            grid.setColumnStretch(0, 0)
            grid.setColumnStretch(1, 1)
            grid.setColumnMinimumWidth(0, 88)
        elif layout_mode == "compact":
            grid.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            fixed_control(self.backend_combo, 420)
            fixed_control(self.model_combo, 385)
            fixed_control(self.effort_combo, 150)
            fixed_control(self.mode_combo, 120)
            fixed_control(self.speed_combo, 105)
            fixed_control(self.backend_setup_button, 120)
            fixed_control(self.quick_menu_button, 125)
            fixed_control(self.analysis_menu_button, 125)
            for label in labels:
                fixed_label(label, 82)
            fixed_label(self.mode_label, 58)
            fixed_label(self.speed_label, 58)
            grid.addWidget(top_row(self.engine_label, self.backend_combo, self.backend_setup_button), 0, 0)
            grid.addWidget(top_row(self.model_label, self.model_combo), 1, 0)
            grid.addWidget(
                top_row(
                    self.effort_label,
                    self.effort_combo,
                    self.mode_label,
                    self.mode_combo,
                    self.speed_label,
                    self.speed_combo,
                    self.quick_menu_button,
                    self.analysis_menu_button,
                ),
                2,
                0,
            )
            grid.setColumnStretch(0, 0)
        else:
            grid.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            fixed_control(self.backend_combo, 420)
            fixed_control(self.model_combo, 385)
            fixed_control(self.effort_combo, 160)
            fixed_control(self.mode_combo, 130)
            fixed_control(self.speed_combo, 110)
            fixed_control(self.backend_setup_button, 125)
            fixed_control(self.quick_menu_button, 135)
            fixed_control(self.analysis_menu_button, 135)
            for label in labels:
                fixed_label(label, 74)
            fixed_label(self.mode_label, 54)
            fixed_label(self.speed_label, 54)
            grid.addWidget(
                top_row(
                    self.engine_label,
                    self.backend_combo,
                    self.backend_setup_button,
                    self.mode_label,
                    self.mode_combo,
                    self.speed_label,
                    self.speed_combo,
                ),
                0,
                0,
            )
            grid.addWidget(
                top_row(
                    self.model_label,
                    self.model_combo,
                    self.effort_label,
                    self.effort_combo,
                    self.quick_menu_button,
                    self.analysis_menu_button,
                ),
                1,
                0,
            )
            grid.setColumnStretch(0, 0)

    def _set_action_buttons_compact(self, compact):
        grid = getattr(self, "bottom_control_row", None)
        if grid is None:
            return
        self._clear_grid_layout(grid)
        if compact:
            grid.addWidget(self.refresh_button, 0, 0)
            grid.addWidget(self.toggle_workspace_button, 0, 1)
            grid.addWidget(self.toggle_selection_button, 1, 0)
            grid.addWidget(self.toggle_terminal_button, 1, 1)
            grid.addWidget(self.open_action_pad_button, 2, 0, 1, 2)
            grid.setColumnStretch(0, 1)
            grid.setColumnStretch(1, 1)
        else:
            grid.addWidget(self.refresh_button, 0, 0)
            grid.addWidget(self.toggle_workspace_button, 0, 1)
            grid.addWidget(self.toggle_selection_button, 0, 2)
            grid.addWidget(self.toggle_terminal_button, 1, 0)
            grid.addWidget(self.open_action_pad_button, 1, 1, 1, 2)
            grid.setColumnStretch(0, 1)
            grid.setColumnStretch(1, 1)
            grid.setColumnStretch(2, 1)

    def _icon_path(self, icon_name):
        return os.path.join(os.path.dirname(__file__), "icons", icon_name)

    def _set_button_icon(self, button, icon_name):
        from Qt.QtCore import QSize
        from Qt.QtGui import QIcon

        path = self._icon_path(icon_name)
        if not os.path.exists(path):
            return
        button.setIcon(QIcon(path))
        button.setIconSize(QSize(22, 22))
        try:
            button.setMinimumHeight(34)
        except Exception:
            pass

    def _apply_analysis_button_icons(self):
        icon_map = (
            (self.quick_blast_button, "blast-logo.png"),
            (self.quick_hhpred_button, "hhpred-logo.svg"),
            (self.quick_alphafold_button, "alphafold-logo.png"),
            (self.quick_profile_button, "uniprot-logo.png"),
            (self.quick_conservation_button, "consurf-logo.png"),
            (self.quick_catalytic_button, "ai-site.svg"),
            (self.quick_membrane_button, "ai-membrane.svg"),
            (self.quick_pisa_button, "pisa-logo.svg"),
            (self.quick_similar_web_button, "foldseek-logo.png"),
            (self.quick_foldmason_button, "foldmason-logo.png"),
            (self.quick_folddisco_button, "folddisco-logo.png"),
            (self.quick_nucdock_button, "hdock-logo.png"),
            (self.quick_afcomplex_button, "alphafold-logo.png"),
            (self.quick_boltz_button, "boltz-logo.svg"),
            (self.quick_dali_button, "dali-logo.png"),
            (self.quick_vast_button, "vast-logo.png"),
            (self.quick_pdbefold_button, "pdbefold-logo.png"),
            (self.quick_usalign_button, "usalign-logo.png"),
        )
        for button, icon_name in icon_map:
            self._set_button_icon(button, icon_name)

    def _hide_removed_quick_analysis_buttons(self):
        removed_buttons = (
            self.quick_catalytic_button,
            self.quick_membrane_button,
            self.quick_pisa_button,
            self.quick_blast_button,
            self.quick_hhpred_button,
            self.quick_alphafold_button,
            self.quick_profile_button,
            self.quick_conservation_button,
            self.quick_afcomplex_button,
            self.quick_boltz_button,
            self.quick_similar_web_button,
            self.quick_foldmason_button,
            self.quick_folddisco_button,
            self.quick_nucdock_button,
            self.quick_dali_button,
            self.quick_vast_button,
            self.quick_pdbefold_button,
            self.quick_usalign_button,
            self.quick_selection_menu_button,
        )
        for button in removed_buttons:
            try:
                button.hide()
                button.setVisible(False)
            except Exception:
                pass

    def _set_sequence_buttons_compact(self, compact):
        grid = getattr(self, "sequence_control_row", None)
        if grid is None:
            return
        self._clear_grid_layout(grid)
        if compact:
            grid.addWidget(self.sequence_status_label, 0, 0, 1, 2)
            grid.addWidget(self.quick_sequence_bar_button, 1, 0)
            grid.addWidget(self.quick_sequence_button, 1, 1)
            grid.addWidget(self.quick_motif_button, 2, 0)
            grid.addWidget(self.quick_motif_view_button, 2, 1)
            grid.setColumnStretch(0, 1)
            grid.setColumnStretch(1, 1)
        else:
            grid.addWidget(self.sequence_status_label, 0, 0, 1, 4)
            grid.addWidget(self.quick_sequence_bar_button, 1, 0)
            grid.addWidget(self.quick_sequence_button, 1, 1)
            grid.addWidget(self.quick_motif_button, 1, 2)
            grid.addWidget(self.quick_motif_view_button, 1, 3)
            for column in range(4):
                grid.setColumnStretch(column, 1)

    def _toggle_workspace_visibility(self):
        self._set_workspace_visible(not self._workspace_visible)

    def _set_workspace_visible(self, visible):
        self._workspace_visible = bool(visible)
        if self._workspace_visible:
            self.content_tabs.setCurrentIndex(getattr(self, "workspace_tab_index", 1))
        else:
            self.content_tabs.setCurrentIndex(0)
        self.toggle_workspace_button.setText("AI" if self._workspace_visible else "Context")

    def _toggle_selection_panel_visibility(self):
        self._set_selection_panel_visible(not self._selection_panel_visible)

    def _set_selection_panel_visible(self, visible):
        self._selection_panel_visible = bool(visible)
        if self._selection_panel_visible:
            try:
                anchor = self.toggle_selection_button.mapToGlobal(self.toggle_selection_button.rect().bottomLeft())
                self.selection_panel.move(anchor)
            except Exception:
                pass
            self.selection_panel.show()
            self.selection_panel.raise_()
            self.selection_panel.activateWindow()
        else:
            self.selection_panel.hide()
        self.toggle_selection_button.setText("Hide Sel" if self._selection_panel_visible else "Selection")

    def _toggle_command_terminal_visibility(self):
        self._set_command_terminal_visible(not self._command_terminal_visible)

    def _set_command_terminal_visible(self, visible):
        self._command_terminal_visible = bool(visible)
        self.terminal_panel.setVisible(self._command_terminal_visible)
        if self._command_terminal_visible:
            self.prompt_panel.setMaximumHeight(170)
            self.interaction_vertical_splitter.setSizes([135, 120])
        else:
            self.prompt_panel.setMaximumHeight(170)
            self.interaction_vertical_splitter.setSizes([135, 0])
        self.toggle_terminal_button.setText("Hide Term" if self._command_terminal_visible else "Terminal")

    def _workspace_text(self):
        from .semantic import format_figure_lab_report, format_selection_focus_report
        from .service import _recent_command_history_text, build_session_context
        from .tool_state import format_analysis_tool_state

        blocks = [
            f"backend: {get_current_backend_id(self.session)} ({self._backend_label()})",
            f"mode: {self._mode}",
            f"speed: {self._speed_text(self._mode)}",
            f"recommended figure: {recommended_figure_mode(self.session) or '(none)'}",
            "",
            "Session context",
            build_session_context(self.session),
            "",
            format_analysis_tool_state(self.session),
            "",
            "Selection focus",
            format_selection_focus_report(self.session),
            "",
            "Recent commands",
            _recent_command_history_text(self.session, limit=10),
            "",
            "Figure lab",
            format_figure_lab_report(self.session),
        ]
        return "\n".join(blocks)

    def _build_quick_menu(self, parent):
        from Qt.QtWidgets import QMenu

        menu = QMenu(parent)

        mode_menu = menu.addMenu("Mode")
        for mode in self._mode_order:
            mode_menu.addAction(mode, lambda checked=False, m=mode: self._quick_set_mode(m))

        speed_menu = menu.addMenu("Speed")
        for profile in ("auto", "fast", "precise"):
            speed_menu.addAction(profile, lambda checked=False, p=profile: self._quick_set_speed(p))

        backend_menu = menu.addMenu("Engine")
        for backend_id in list_backend_ids():
            available, _status, detail = backend_availability(backend_id)
            action = backend_menu.addAction(
                self._backend_combo_label(backend_id),
                lambda checked=False, b=backend_id: self._quick_set_backend(b),
            )
            action.setEnabled(bool(available))
            action.setToolTip(detail)

        model_menu = menu.addMenu("Model")
        for model_name in suggested_models_for_backend(get_current_backend_id(self.session))[:8]:
            model_menu.addAction(model_name, lambda checked=False, m=model_name: self._quick_set_model(m))
        model_menu.addAction("provider default", lambda: self._quick_clear_model())
        sequence_menu = menu.addMenu("Sequence")
        sequence_menu.addAction("Toggle top sequence bar", self._toggle_sequence_bar)
        sequence_menu.addAction("Sequence summary", self._quick_sequence_summary)
        sequence_menu.addAction("Motif summary", self._quick_motif_summary)
        sequence_menu.addAction("Highlight motifs", self._quick_motif_view)
        sequence_menu.addAction("Virtual membrane view", self._quick_membrane)
        sequence_menu.addAction("PISA-like interface area", self._quick_pisa)
        sequence_menu.addAction("RCSB similar web", self._quick_similar_web)
        self._populate_selection_menu(sequence_menu.addMenu("Selection"))
        self._populate_analysis_menu(menu.addMenu("Analysis"))
        menu.addSeparator()
        menu.addAction("Open Action Pad", self._open_action_pad)
        return menu

    def _refresh_backend_setup_menu(self):
        menu = self.backend_setup_menu
        menu.clear()
        current_backend = get_current_backend_id(self.session)
        for backend_id in list_backend_ids():
            available, status, detail = backend_availability(backend_id)
            prefix = "*" if backend_id == current_backend else "-"
            action = menu.addAction(f"{prefix} {get_backend_label(backend_id)}: {status}")
            action.setEnabled(False)
            action.setToolTip(detail)
        menu.addSeparator()
        menu.addAction("Refresh engine status", self._refresh_engine_status)
        current_action = menu.addAction(f"Setup current: {get_backend_label(current_backend)}")
        current_action.triggered.connect(lambda _checked=False, b=current_backend: self._setup_backend(b))
        for backend_id in list_backend_ids():
            if backend_id == current_backend:
                continue
            action = menu.addAction(f"Setup {get_backend_label(backend_id)}")
            action.triggered.connect(lambda _checked=False, b=backend_id: self._setup_backend(b))

    def _refresh_engine_status(self):
        self._populate_backend_combo()
        self._populate_model_controls()
        self._sync_control_widgets()
        self._set_result_status("Engine status refreshed.")

    def _setup_backend(self, backend_id):
        spec = get_backend_spec(backend_id)
        if spec.get("transport") == "api":
            env_names = ", ".join(spec.get("api_key_envs") or ())
            self._copy_text_to_clipboard(f"{spec.get('api_key_envs', ('OPENAI_API_KEY',))[0]}=YOUR_API_KEY")
            self._set_result_status(
                f"{get_backend_label(backend_id)} uses an API key. Copied env template; set {env_names} before launching ChimeraX.",
                tone="warn",
            )
            return

        cli_path = resolve_backend_cli(backend_id, strict=False)
        if not cli_path:
            env_names = ", ".join(spec.get("cli_envs") or ())
            self._set_result_status(
                f"{get_backend_label(backend_id)} CLI not found. Install it or set {env_names} before launching ChimeraX.",
                tone="warn",
            )
            return

        login_args = {
            "codex": "login",
            "claude": "login",
            "gemini": "auth login",
        }.get(backend_id, "login")
        command = f"{shlex.quote(cli_path)} {login_args}"
        try:
            script = f'tell application "Terminal" to do script {json.dumps(command)}\ntell application "Terminal" to activate'
            subprocess.Popen(["osascript", "-e", script])
            self._set_result_status(f"Opened Terminal for {get_backend_label(backend_id)} login. Refresh engine status after login.", tone="warn")
        except Exception as err:
            self._copy_text_to_clipboard(command)
            self._set_result_status(
                f"Could not open Terminal automatically. Copied login command: {command}. Error: {str(err) if str(err) else err.__class__.__name__}",
                tone="warn",
            )

    def _copy_text_to_clipboard(self, text):
        try:
            from Qt.QtWidgets import QApplication

            clipboard = QApplication.clipboard()
            if clipboard is not None:
                clipboard.setText(str(text or ""))
                return True
        except Exception:
            pass
        return False

    def _build_analysis_menu(self, parent):
        from Qt.QtWidgets import QMenu

        menu = QMenu(parent)
        self._populate_analysis_menu(menu)
        return menu

    def _populate_analysis_menu(self, menu):
        local_menu = menu.addMenu("Local / scene analysis")
        local_menu.addAction("Sequence report", self._quick_sequence_summary)
        local_menu.addAction("Motif report", self._quick_motif_summary)
        local_menu.addAction("Highlight motifs", self._quick_motif_view)
        local_menu.addAction("Catalytic residue triage", self._quick_catalytic)
        local_menu.addAction("Membrane view", self._quick_membrane)
        local_menu.addAction("PISA-like interfaces", self._quick_pisa)

        sequence_menu = menu.addMenu("Sequence / modeling web tools")
        sequence_menu.addAction("BLAST", self._quick_blast)
        sequence_menu.addAction("HHpred / HHblits", self._quick_hhpred)
        sequence_menu.addAction("UniProt BLAST", self._quick_profile)
        sequence_menu.addAction("ConSurf", self._quick_conservation)
        sequence_menu.addAction("AlphaFold Server", self._quick_alphafold)
        sequence_menu.addAction("AF Complex", self._quick_afcomplex)
        sequence_menu.addAction("Boltz", self._quick_boltz)

        structure_menu = menu.addMenu("Structure search / alignment")
        structure_menu.addAction("Foldseek similar", self._quick_similar_web)
        structure_menu.addAction("FoldMason", self._quick_foldmason)
        structure_menu.addAction("FoldDisco motif", self._quick_folddisco)
        structure_menu.addAction("NucDock", self._quick_nucdock)
        structure_menu.addAction("DALI", self._quick_dali)
        structure_menu.addAction("VAST", self._quick_vast)
        structure_menu.addAction("PDBeFold", self._quick_pdbefold)
        structure_menu.addAction("US-align", self._quick_usalign)

        menu.addSeparator()
        menu.addAction("Open Action Pad controls", self._open_action_pad)

    def _build_selection_menu(self, parent):
        from Qt.QtWidgets import QMenu

        menu = QMenu("Selection", parent)
        self._populate_selection_menu(menu)
        return menu

    def _populate_selection_menu(self, menu):
        menu.addAction("Select current chain", self._select_current_sequence_chain)
        menu.addAction("Clear selection", self._clear_structure_selection)
        menu.addSeparator()
        mode_menu = menu.addMenu("Top sequence click mode")
        mode_menu.addAction("Residue / range", lambda: self._set_sequence_bar_click_mode("residue"))
        mode_menu.addAction("Alpha helix segment", lambda: self._set_sequence_bar_click_mode("helix"))
        mode_menu.addAction("Beta sheet segment", lambda: self._set_sequence_bar_click_mode("strand"))
        menu.addSeparator()
        menu.addAction("Select all alpha helices", lambda: self._select_secondary_structure("helix", "all"))
        menu.addSeparator()
        menu.addAction("Select all beta sheets/strands", lambda: self._select_secondary_structure("strand", "all"))

    def _current_sequence_entry(self):
        try:
            from .toolbar_actions import _protein_chain_entries

            entries = _protein_chain_entries(self.session)
        except Exception:
            return None
        return entries[0] if entries else None

    def _set_sequence_bar_click_mode(self, mode):
        try:
            from .sequence_bar import CodexSequenceBar

            sequence_bar = CodexSequenceBar.get_singleton(self.session, create=True, display=True)
            sequence_bar.display(True)
            sequence_bar.refresh()
            sequence_bar._set_selection_click_mode(mode)
            self.quick_sequence_bar_button.setText("Hide Seq")
            self._set_result_status(f"Top sequence click mode: {mode}.")
        except Exception as err:
            self._set_result_status(str(err) if str(err) else err.__class__.__name__, tone="error")

    def _select_current_sequence_chain(self):
        entry = self._current_sequence_entry()
        if not entry:
            self._set_result_status("No protein chain resolved.", tone="error")
            return
        from chimerax.core.commands import run

        with command_batch(self.session, f"Select chain {entry['spec']}"):
            run(self.session, f"select {entry['spec']}")
        self._set_result_status(f"Selected chain {entry['spec']}.")
        self._queue_workspace_refresh()

    def _clear_structure_selection(self):
        from chimerax.core.commands import run

        with command_batch(self.session, "Clear selection"):
            run(self.session, "select clear")
        self._set_result_status("Selection cleared.")
        self._queue_workspace_refresh()

    def _select_secondary_structure(self, selector, scope):
        from chimerax.core.commands import run

        selector = "strand" if selector == "strand" else "helix"
        label = "beta sheets/strands" if selector == "strand" else "alpha helices"
        with command_batch(self.session, f"Select {label}"):
            run(self.session, f"select {selector}")
        self._set_result_status(f"Selected {label} in all models.")
        self._queue_workspace_refresh()

    def _show_assistant_tab(self):
        self._workspace_visible = False
        self.toggle_workspace_button.setText("Context")
        self.content_tabs.setCurrentIndex(0)
        self._focus_prompt()

    def _show_action_pad_tab(self):
        try:
            from . import _ensure_action_pad_models_tab

            _ensure_action_pad_models_tab(self.session, raise_action=True)
        except Exception as err:
            self._show_error(str(err) if str(err) else err.__class__.__name__)

    def _focus_prompt(self):
        try:
            self.prompt_edit.setFocus()
        except Exception:
            pass

    def _quick_set_mode(self, mode):
        self._mode = mode
        self._append_system(f"mode: {self._mode}")
        self._sync_control_widgets()
        self._queue_workspace_refresh()

    def _quick_set_speed(self, profile):
        set_speed_profile(self.session, profile)
        self._append_system(f"speed: {self._speed_text(self._mode)}")
        self._sync_control_widgets()
        self._queue_workspace_refresh()

    def _quick_set_backend(self, backend_id):
        available, _status, detail = backend_availability(backend_id)
        if not available:
            self._set_result_status(f"{get_backend_label(backend_id)} unavailable: {detail}", tone="warn")
            return
        set_current_backend_id(self.session, backend_id)
        self._append_system(f"backend: {backend_id} ({self._backend_label()})")
        self._sync_control_widgets()
        self._queue_workspace_refresh()

    def _quick_set_model(self, model_name):
        set_model_override(self.session, model_name)
        self._append_system(f"model override: {model_name}")
        self._sync_control_widgets()
        self._queue_workspace_refresh()

    def _quick_clear_model(self):
        clear_model_override(self.session)
        self._append_system("model override: cleared")
        self._sync_control_widgets()
        self._queue_workspace_refresh()

    def _open_action_pad(self):
        self._show_action_pad_tab()

    def _toggle_sequence_bar(self):
        try:
            from .sequence_bar import CodexSequenceBar

            sequence_bar = CodexSequenceBar.get_singleton(self.session, create=False, display=False)
            if sequence_bar is not None and sequence_bar.displayed():
                sequence_bar.display(False)
                self.quick_sequence_bar_button.setText("Show Seq")
                self._append_system("top sequence bar hidden")
                return
            sequence_bar = CodexSequenceBar.get_singleton(self.session, create=True, display=True)
            if sequence_bar is not None:
                sequence_bar.display(True)
                sequence_bar.refresh()
                self.quick_sequence_bar_button.setText("Hide Seq")
                self._append_system("top sequence bar shown")
        except Exception as err:
            self._show_error(str(err) if str(err) else err.__class__.__name__)

    def _open_sequence_bar(self):
        try:
            from .sequence_bar import CodexSequenceBar

            sequence_bar = CodexSequenceBar.get_singleton(self.session)
            if sequence_bar is not None:
                sequence_bar.display(True)
                sequence_bar.refresh()
                self.quick_sequence_bar_button.setText("Hide Seq")
        except Exception as err:
            self._show_error(str(err) if str(err) else err.__class__.__name__)

    def _refresh_selection_controls(self):
        from .semantic import get_session_semantics

        semantics = get_session_semantics(self.session)
        current_model = self.selection_model_combo.currentData()
        current_chain = self.selection_chain_combo.currentData()

        self._selection_controls_updating = True
        try:
            self.selection_model_combo.blockSignals(True)
            self.selection_model_combo.clear()
            for model in semantics.get("models", []):
                if not model.get("atomic"):
                    continue
                label = f"{model['spec']} {model['name']}"
                self.selection_model_combo.addItem(label, model["spec"])

            model_spec = current_model if self.selection_model_combo.findData(current_model) != -1 else self._preferred_selection_model_spec(semantics)
            if model_spec is not None:
                model_index = max(0, self.selection_model_combo.findData(model_spec))
                self.selection_model_combo.setCurrentIndex(model_index)

            self._refresh_selection_chain_combo(semantics, preferred_chain=current_chain)
        finally:
            self.selection_model_combo.blockSignals(False)
            self._selection_controls_updating = False
        self._update_selection_spec_preview()

    def _preferred_selection_model_spec(self, semantics):
        selection = semantics.get("selection", {})
        ranges = list(selection.get("ranges", []))
        if ranges:
            return ranges[0].split("/", 1)[0]
        models = list(selection.get("models", []))
        if models:
            return models[0]
        for model in semantics.get("models", []):
            if model.get("atomic"):
                return model["spec"]
        return None

    def _refresh_selection_chain_combo(self, semantics, preferred_chain=None):
        model_spec = self.selection_model_combo.currentData()
        self.selection_chain_combo.blockSignals(True)
        try:
            self.selection_chain_combo.clear()
            self.selection_chain_combo.addItem("all", "")
            if not model_spec:
                return
            model_entry = None
            for item in semantics.get("models", []):
                if item.get("spec") == model_spec:
                    model_entry = item
                    break
            if model_entry is None:
                return
            for chain in model_entry.get("chains", []):
                self.selection_chain_combo.addItem(chain["id"], chain["id"])
            if preferred_chain and self.selection_chain_combo.findData(preferred_chain) != -1:
                self.selection_chain_combo.setCurrentIndex(self.selection_chain_combo.findData(preferred_chain))
        finally:
            self.selection_chain_combo.blockSignals(False)

    def _selection_model_changed(self, _index):
        if self._selection_controls_updating:
            return
        from .semantic import get_session_semantics

        self._refresh_selection_chain_combo(get_session_semantics(self.session))
        self._update_selection_spec_preview()

    def _apply_current_selection_to_controls(self):
        from .semantic import get_session_semantics

        semantics = get_session_semantics(self.session)
        selection = semantics.get("selection", {})
        ranges = list(selection.get("ranges", []))
        models = list(selection.get("models", []))
        if not ranges and not models:
            self._append_error("no current ChimeraX selection to import")
            return

        target_spec = ranges[0] if ranges else models[0]
        model_spec, chain_id, start, end = self._parse_selection_spec(target_spec)

        self._selection_controls_updating = True
        try:
            if model_spec and self.selection_model_combo.findData(model_spec) != -1:
                self.selection_model_combo.setCurrentIndex(self.selection_model_combo.findData(model_spec))
            self._refresh_selection_chain_combo(semantics, preferred_chain=chain_id)
            if chain_id and self.selection_chain_combo.findData(chain_id) != -1:
                self.selection_chain_combo.setCurrentIndex(self.selection_chain_combo.findData(chain_id))
            self.selection_start_edit.setText("" if start is None else str(start))
            self.selection_end_edit.setText("" if end is None or end == start else str(end))
        finally:
            self._selection_controls_updating = False
        self._update_selection_spec_preview()

    def _parse_selection_spec(self, spec):
        import re

        text = str(spec or "").strip()
        match = re.match(r"(#\d+(?:\.\d+)*)?(?:/([A-Za-z0-9?]+))?(?::(\d+)(?:-(\d+))?)?$", text)
        if not match:
            return None, None, None, None
        model_spec = match.group(1)
        chain_id = match.group(2)
        start = int(match.group(3)) if match.group(3) else None
        end = int(match.group(4)) if match.group(4) else start
        return model_spec, chain_id, start, end

    def _selection_spec_text(self):
        model_spec = self.selection_model_combo.currentData()
        if not model_spec:
            return ""
        chain_id = self.selection_chain_combo.currentData()
        start_text = self.selection_start_edit.text().strip()
        end_text = self.selection_end_edit.text().strip()

        spec = model_spec
        if chain_id:
            spec += f"/{chain_id}"
        if start_text or end_text:
            if not start_text and end_text:
                start_text = end_text
            residue_text = start_text
            if end_text and end_text != start_text:
                residue_text = f"{start_text}-{end_text}"
            if residue_text:
                spec += f":{residue_text}"
        return spec

    def _update_selection_spec_preview(self):
        spec = self._selection_spec_text()
        if spec:
            self.selection_spec_label.setText("Spec: " + spec)
        else:
            self.selection_spec_label.setText("Spec: (none)")

    def _selection_command_target(self):
        spec = self._selection_spec_text()
        if not spec:
            self._append_error("selection target is empty")
            return None
        return spec

    def _selection_select(self):
        spec = self._selection_command_target()
        if not spec:
            return
        try:
            with command_batch(self.session, f"Selection select:{spec}"):
                self._run_command_thread_safe(f"select {spec}")
        except Exception as err:
            self._show_error(str(err) if str(err) else err.__class__.__name__)
            return
        self._append_system(f"selection applied: {spec}")
        self._queue_workspace_refresh()

    def _selection_focus(self):
        spec = self._selection_command_target()
        if not spec:
            return
        try:
            with command_batch(self.session, f"Selection focus:{spec}"):
                self._run_command_thread_safe(f"select {spec}")
                self._run_command_thread_safe("view sel")
        except Exception as err:
            self._show_error(str(err) if str(err) else err.__class__.__name__)
            return
        self._append_system(f"focused: {spec}")
        self._queue_workspace_refresh()

    def _selection_show(self):
        self._selection_show_hide("show")

    def _selection_hide(self):
        self._selection_show_hide("hide")

    def _selection_show_hide(self, verb):
        spec = self._selection_command_target()
        if not spec:
            return
        rep = self.selection_rep_combo.currentData() or "cartoon"
        command = self._selection_rep_command(verb, spec, rep)
        if not command:
            self._append_error("no selection display command is available")
            return
        try:
            with command_batch(self.session, f"Selection {verb}:{spec}:{rep}"):
                for part in command:
                    self._run_command_thread_safe(part)
                if verb == "show" and rep == "sticks":
                    apply_stick_context_colors(self.session, spec)
        except Exception as err:
            self._show_error(str(err) if str(err) else err.__class__.__name__)
            return
        self._append_system(f"{verb} {rep}: {spec}")
        self._queue_workspace_refresh()

    def _selection_rep_command(self, verb, spec, rep):
        if rep == "sticks":
            if verb == "show":
                return [f"show {spec} atoms", f"style {spec} stick"]
            return [f"hide {spec} atoms"]
        if rep == "surface":
            if verb == "show":
                return [f"surface {spec}"]
            return [f"~surface {spec}"]
        if verb == "show":
            return [f"cartoon {spec}"]
        return [f"hide {spec} cartoons"]

    def _selection_analyze(self):
        spec = self._selection_command_target()
        if not spec:
            return
        self._launch_quick_request(
            f"Analyze {spec} with evidence, confidence, and next checks. Treat it as the primary focus.",
            "analyze",
        )

    def _selection_clear(self):
        try:
            with command_batch(self.session, "Selection clear"):
                self._run_command_thread_safe("select clear")
        except Exception as err:
            self._show_error(str(err) if str(err) else err.__class__.__name__)
            return
        self._append_system("selection cleared")
        self._queue_workspace_refresh()

    def _update_session_status(self):
        from .semantic import get_session_semantics

        semantics = get_session_semantics(self.session)
        selection = semantics.get("selection", {})
        models = semantics.get("models", [])
        selected_ranges = list(selection.get("ranges", []))
        if selected_ranges:
            selection_text = ", ".join(selected_ranges[:2])
            if len(selected_ranges) > 2:
                selection_text += f", ... (+{len(selected_ranges) - 2})"
        elif selection.get("models"):
            selection_text = ", ".join(selection["models"][:2])
        else:
            selection_text = "none"

        self.session_status_label.setText(
            "Session: "
            + f"{len(models)} model(s) | selection {selection_text} | "
            + f"engine {self._backend_label()} | model {self._active_model_display(self._mode)} | "
            + f"route {get_routing_mode(self.session)} | mode {self._mode} | speed {self._speed_text(self._mode)}"
        )

    def _update_sequence_status(self):
        status_text, strip_text = self._sequence_status_payload()
        self.sequence_status_label.setText(status_text)
        if self.sequence_strip_edit is not None:
            self.sequence_strip_edit.setPlainText(strip_text)
        self._sync_sequence_bar_button()

    def _sync_sequence_bar_button(self):
        try:
            from .sequence_bar import CodexSequenceBar

            sequence_bar = CodexSequenceBar.get_singleton(self.session, create=False, display=False)
            visible = bool(sequence_bar is not None and sequence_bar.displayed())
        except Exception:
            visible = False
        self.quick_sequence_bar_button.setText("Hide Seq" if visible else "Show Seq")

    def _sequence_status_text(self):
        return self._sequence_status_payload()[0]

    def _sequence_status_payload(self):
        try:
            from .semantic import get_motif_hits
            from .toolbar_actions import _protein_chain_entries

            entries = _protein_chain_entries(self.session)
            if not entries:
                return "Sequence: no protein chain resolved", ""

            primary = entries[0]
            chain_id = str(primary.get("chain_id", "") or "")
            model_spec = str(primary.get("spec", "")).split("/", 1)[0]
            motifs = [
                hit for hit in get_motif_hits(self.session, model_hint=model_spec)
                if str(hit.get("chain_id", "")) == chain_id
            ]
            motif_bits = []
            top_motifs = sorted(motifs, key=lambda hit: (hit.get("priority", 99), hit.get("start_number", 0), hit.get("pattern_name", "")))
            for hit in top_motifs[:3]:
                motif_bits.append(
                    f"{hit['pattern_name']} {hit['chain_id']}:{hit['start_number']}-{hit['end_number']}"
                )
            if len(motifs) > 3:
                motif_bits.append(f"+{len(motifs) - 3} more")
            motif_text = ", ".join(motif_bits) if motif_bits else "no default motif hits"
            extra = f" | {len(entries)} chains" if len(entries) > 1 else ""
            status = f"Sequence: {primary['spec']} | {len(primary['sequence'])} aa{extra} | motifs: {motif_text}"
            return status, self._sequence_strip_text(primary, motifs)
        except Exception as err:
            message = str(err) if str(err) else err.__class__.__name__
            return f"Sequence: unavailable ({message})", ""

    def _sequence_strip_text(self, entry, motifs):
        sequence = str(entry.get("sequence", "") or "").upper()
        if not sequence:
            return ""
        spec = str(entry.get("spec", "") or "")
        tick_line = self._sequence_tick_line(len(sequence))
        motif_line = self._sequence_motif_line(len(sequence), motifs)
        lines = [f"{spec}  1-{len(sequence)}", tick_line, sequence]
        if motif_line:
            lines.append(motif_line)
        return "\n".join(lines)

    def _sequence_tick_line(self, length):
        chars = [" "] * length
        for pos in range(10, length + 1, 10):
            label = str(pos)
            start = max(0, pos - len(label))
            for index, char in enumerate(label):
                if start + index < length:
                    chars[start + index] = char
        return "".join(chars)

    def _sequence_motif_line(self, length, motifs):
        chars = [" "] * length
        for hit in motifs:
            try:
                start = max(1, int(hit.get("start_number", 1)))
                end = min(length, int(hit.get("end_number", start)))
            except Exception:
                continue
            if start > length or end < 1:
                continue
            for pos in range(start, end + 1):
                chars[pos - 1] = "^"
        text = "".join(chars).rstrip()
        return f"motifs {text}" if text else ""

    def _set_result_status(self, text, tone="neutral"):
        mono_qss = getattr(self, "_mono_qss", "")
        color_map = {
            "neutral": ("#15181b", "#c7ccd2", "#343a40"),
            "running": ("#1b1f23", "#d8dde3", "#464d55"),
            "success": ("#122018", "#c8efcf", "#2d5a3c"),
            "warn": ("#21180f", "#f3d6a0", "#5b4625"),
            "error": ("#241315", "#ffcad0", "#6d2d34"),
        }
        background, color, border = color_map.get(tone, color_map["neutral"])
        self.result_status_label.setStyleSheet(
            "QLabel {"
            f" background: {background};"
            f" color: {color};"
            f" border: 1px solid {border};"
            " border-radius: 6px;"
            " padding: 6px 8px;"
            f"{mono_qss}"
            "}"
        )
        display_text = " ".join(str(text or "").split())
        if len(display_text) > 180:
            display_text = display_text[:177] + "..."
        self.result_status_label.setText("Result: " + display_text)

    def _summarize_result_status(self, response):
        text = str(response or "")
        lowered = text.lower()
        if "result: not found" in lowered or "no uniprot feature annotations overlap" in lowered:
            return "not found", "warn"
        if "error:" in lowered or "failed" in lowered:
            return "completed with an error", "error"
        if "no visual change was applied" in lowered or "no actions to run" in lowered:
            return "completed without scene change", "warn"
        if "observed changes:" in lowered or "executed chimeraX commands".lower() in lowered or "Action:" in text:
            return "scene updated", "success"
        return "analysis ready", "success"

    def _set_result_detail(self, text):
        mono_qss = getattr(self, "_mono_qss", "")
        lowered = str(text or "").lower()
        if any(token in lowered for token in ("error", "failed", "traceback", "exception")):
            self.result_detail_edit.setStyleSheet(
                "QPlainTextEdit {"
                " background: #1a0f12;"
                " color: #ffd8dd;"
                " border: 1px solid #74404a;"
                " border-radius: 8px;"
                " padding: 7px;"
                f"{mono_qss}"
                "}"
            )
        else:
            self.result_detail_edit.setStyleSheet(
                "QPlainTextEdit {"
                " background: #101214;"
                " color: #d9dde2;"
                " border: 1px solid #343a40;"
                " border-radius: 8px;"
                " padding: 7px;"
                f"{mono_qss}"
                "}"
            )
        self.result_detail_edit.setPlainText(str(text or "").strip())

    def _show_error_banner(self, text):
        message = " ".join(str(text or "").split())
        if not message:
            self._clear_error_banner()
            return
        if len(message) > 260:
            message = message[:257] + "..."
        self.error_banner_label.setText("ERROR: " + message)
        self.error_banner_label.setVisible(True)

    def _clear_error_banner(self):
        self.error_banner_label.setText("")
        self.error_banner_label.setVisible(False)

    def _result_detail_text(self, response):
        lines = []
        for raw_line in str(response or "").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith(("# ", "! ")):
                continue
            if line in {"Summary", "Observations", "Hypotheses", "Next checks", "Suggested visuals", "Suggested ChimeraX commands"}:
                continue
            lines.append(line)
        if not lines:
            return "No additional result detail."

        lowered = "\n".join(lines).lower()
        if "result: not found" in lowered:
            focus = [line for line in lines if "result:" in line or "detail:" in line][:4]
            return "\n".join(focus) if focus else "Not found."
        if "result: found" in lowered:
            focus = [line for line in lines if "result:" in line or "chain " in line or ":" in line][:6]
            return "\n".join(focus[:6])
        if any("observed changes:" in line.lower() for line in lines):
            focus = [line for line in lines if line.startswith("Action:") or "observed changes:" in line.lower() or "verify target:" in line.lower()]
            return "\n".join(focus[:6]) if focus else "\n".join(lines[:6])
        return "\n".join(lines[:6])

    def _populate_backend_combo(self):
        if not hasattr(self, "backend_combo"):
            return
        current = get_current_backend_id(self.session)
        self.backend_combo.blockSignals(True)
        try:
            self.backend_combo.clear()
            for backend_id in list_backend_ids():
                available, status, detail = backend_availability(backend_id)
                label = self._backend_combo_label(backend_id, available=available, status=status)
                self.backend_combo.addItem(label, backend_id)
                index = self.backend_combo.count() - 1
                self.backend_combo.setItemData(index, detail, Qt.ItemDataRole.ToolTipRole)
                if not available:
                    item = self.backend_combo.model().item(index)
                    if item is not None:
                        item.setEnabled(False)
            index = self.backend_combo.findData(current)
            if index < 0:
                index = 0
            self.backend_combo.setCurrentIndex(index)
        finally:
            self.backend_combo.blockSignals(False)

    def _populate_model_controls(self):
        if not hasattr(self, "model_combo") or not hasattr(self, "effort_combo"):
            return
        backend_id = get_current_backend_id(self.session)
        available, _status, detail = backend_availability(backend_id)

        self.model_combo.blockSignals(True)
        self.effort_combo.blockSignals(True)
        try:
            self.model_combo.clear()
            default_model = self._active_default_model_display(self._mode)
            self.model_combo.addItem(f"provider default ({default_model})", "__default__")
            seen = set()
            override = get_model_override(self.session, backend_id)
            models = list(suggested_models_for_backend(backend_id))
            if override and override not in models:
                models.insert(0, override)
            for model in models:
                if not model or model in seen:
                    continue
                seen.add(model)
                self.model_combo.addItem(model, model)
            model_index = self.model_combo.findData(override) if override else 0
            self.model_combo.setCurrentIndex(max(0, model_index))
            self.model_combo.setEnabled(bool(available))
            self.model_combo.setToolTip(
                "Model is passed to the active backend request."
                if available
                else f"Disabled because {get_backend_label(backend_id)} is unavailable: {detail}"
            )

            self.effort_combo.clear()
            default_effort = self._active_default_effort_display(self._mode)
            self.effort_combo.addItem(f"default ({default_effort})", "__default__")
            override_effort = get_effort_override(self.session, backend_id)
            efforts = list(suggested_efforts_for_backend(backend_id))
            for effort in efforts:
                self.effort_combo.addItem(effort, effort)
            effort_index = self.effort_combo.findData(override_effort) if override_effort else 0
            self.effort_combo.setCurrentIndex(max(0, effort_index))
            self.effort_combo.setEnabled(bool(available and efforts))
            if not efforts:
                self.effort_combo.setToolTip(f"{get_backend_label(backend_id)} does not expose reasoning controls here.")
            elif not available:
                self.effort_combo.setToolTip(f"Disabled because {get_backend_label(backend_id)} is unavailable: {detail}")
            else:
                self.effort_combo.setToolTip("Reasoning effort is passed to the active backend when supported.")
        finally:
            self.model_combo.blockSignals(False)
            self.effort_combo.blockSignals(False)

    def _sync_control_widgets(self):
        self.backend_combo.blockSignals(True)
        self.model_combo.blockSignals(True)
        self.effort_combo.blockSignals(True)
        self.mode_combo.blockSignals(True)
        self.speed_combo.blockSignals(True)
        self.width_slider.blockSignals(True)
        try:
            self._populate_backend_combo()
            backend_id = get_current_backend_id(self.session)
            backend_index = max(0, self.backend_combo.findData(backend_id))
            self.backend_combo.setCurrentIndex(backend_index)
            self._populate_model_controls()

            mode_index = max(0, self.mode_combo.findData(self._mode))
            self.mode_combo.setCurrentIndex(mode_index)

            speed_profile = self._speed_profile_value()
            speed_index = max(0, self.speed_combo.findData(speed_profile))
            self.speed_combo.setCurrentIndex(speed_index)
            self.width_slider.setValue(int(round(self._dock_fraction_value() * 100)))
        finally:
            self.backend_combo.blockSignals(False)
            self.model_combo.blockSignals(False)
            self.effort_combo.blockSignals(False)
            self.mode_combo.blockSignals(False)
            self.speed_combo.blockSignals(False)
            self.width_slider.blockSignals(False)

    def _speed_profile_value(self):
        return get_speed_profile(self.session)

    def _dock_fraction_value(self):
        value = getattr(self.session, "_codex_bridge_dock_fraction", 0.30)
        try:
            numeric = float(value)
        except Exception:
            numeric = 0.30
        return max(0.10, min(0.50, numeric))

    def _dock_width_slider_changed(self, value):
        self._set_dock_fraction(float(value) / 100.0)

    def _set_dock_fraction(self, fraction):
        self.session._codex_bridge_dock_fraction = max(0.10, min(0.50, float(fraction)))
        self._apply_dock_fraction()
        self._sync_control_widgets()
        self._update_session_status()

    def _begin_dock_resize(self, global_x):
        dock_widget = getattr(self.tool_window, "_dock_widget", None)
        if dock_widget is None:
            self._dock_drag_origin_x = None
            self._dock_drag_origin_width = None
            self._dock_constrained_widgets = []
            return
        self._dock_drag_origin_x = global_x
        self._dock_drag_origin_width = max(dock_widget.width(), 220)
        self._dock_constrained_widgets = self._right_side_dock_widgets()

    def _resize_dock_by_position(self, global_x):
        if global_x is None or self._dock_drag_origin_x is None or self._dock_drag_origin_width is None:
            return
        dock_widget = getattr(self.tool_window, "_dock_widget", None)
        if dock_widget is None:
            return
        main_window = self.session.ui.main_window
        overall_width = max(main_window.width(), 900)
        delta_x = int(global_x - self._dock_drag_origin_x)
        target_width = int(self._dock_drag_origin_width - delta_x)
        target_width = max(int(overall_width * 0.10), min(int(overall_width * 0.50), target_width))
        self.session._codex_bridge_dock_fraction = float(target_width) / float(overall_width)
        self._apply_dock_width(target_width, keep_constraints=True)

    def _end_dock_resize(self):
        for dock_widget in self._dock_constrained_widgets:
            try:
                dock_widget.setMinimumWidth(0)
                dock_widget.setMaximumWidth(16777215)
            except Exception:
                pass
        self._dock_constrained_widgets = []
        self._dock_drag_origin_x = None
        self._dock_drag_origin_width = None
        self._sync_control_widgets()
        self._update_session_status()

    def _apply_dock_fraction(self, live=False):
        if getattr(self.tool_window, "_dock_widget", None) is None:
            return
        main_window = self.session.ui.main_window
        overall_width = max(main_window.width(), 900)
        target_width = int(overall_width * self._dock_fraction_value())
        self._apply_dock_width(target_width, keep_constraints=live)

    def _right_side_dock_widgets(self):
        from Qt.QtCore import Qt
        from Qt.QtWidgets import QDockWidget

        dock_widget = getattr(self.tool_window, "_dock_widget", None)
        if dock_widget is None:
            return []
        main_window = self.session.ui.main_window
        side = main_window.dockWidgetArea(dock_widget)
        if side != Qt.DockWidgetArea.RightDockWidgetArea:
            return [dock_widget]
        widgets = []
        for candidate in main_window.findChildren(QDockWidget):
            try:
                if candidate.isFloating():
                    continue
                if main_window.dockWidgetArea(candidate) != side:
                    continue
            except Exception:
                continue
            widgets.append(candidate)
        return widgets or [dock_widget]

    def _apply_dock_width(self, target_width, keep_constraints=False):
        from Qt.QtCore import Qt

        dock_widgets = self._dock_constrained_widgets or self._right_side_dock_widgets()
        if not dock_widgets:
            return
        main_window = self.session.ui.main_window
        if keep_constraints:
            for dock_widget in dock_widgets:
                try:
                    dock_widget.setMinimumWidth(target_width)
                    dock_widget.setMaximumWidth(target_width)
                except Exception:
                    pass
        try:
            main_window.resizeDocks(dock_widgets, [target_width] * len(dock_widgets), Qt.Orientation.Horizontal)
        except Exception:
            pass
        if not keep_constraints:
            for dock_widget in dock_widgets:
                try:
                    dock_widget.setMinimumWidth(0)
                    dock_widget.setMaximumWidth(16777215)
                except Exception:
                    pass

    def _backend_combo_changed(self, index):
        backend_id = self.backend_combo.itemData(index)
        if not backend_id:
            return
        available, _status, detail = backend_availability(backend_id)
        if not available:
            self._set_result_status(f"{get_backend_label(backend_id)} unavailable: {detail}", tone="warn")
            self._sync_control_widgets()
            return
        set_current_backend_id(self.session, backend_id)
        self._append_system(f"backend: {backend_id} ({self._backend_label()})")
        self._append_system(f"model: {self._active_model_display(self._mode)}")
        self._populate_model_controls()
        self._queue_workspace_refresh()

    def _model_combo_changed(self, index):
        value = self.model_combo.itemData(index)
        if not value:
            return
        backend_id = get_current_backend_id(self.session)
        if value == "__default__":
            clear_model_override(self.session, backend_id)
            self._append_system(f"model override cleared for {get_backend_label(backend_id)}")
        else:
            set_model_override(self.session, str(value), backend_id)
            self._append_system(f"model override for {get_backend_label(backend_id)}: {value}")
        self._populate_model_controls()
        self._queue_workspace_refresh()

    def _effort_combo_changed(self, index):
        value = self.effort_combo.itemData(index)
        if not value:
            return
        backend_id = get_current_backend_id(self.session)
        if value == "__default__":
            clear_effort_override(self.session, backend_id)
            self._append_system(f"reasoning override cleared for {get_backend_label(backend_id)}")
        else:
            set_effort_override(self.session, str(value), backend_id)
            self._append_system(f"reasoning override for {get_backend_label(backend_id)}: {value}")
        self._populate_model_controls()
        self._queue_workspace_refresh()

    def _mode_combo_changed(self, index):
        mode = self.mode_combo.itemData(index)
        if not mode:
            return
        self._mode = mode
        self._append_system(f"mode: {self._mode}")
        self.status_label.setText(f"Mode: {self._mode}")
        self.stage_label.setText(f"Mode · {self._mode}")
        self._populate_model_controls()
        self._queue_workspace_refresh()

    def _speed_combo_changed(self, index):
        profile = self.speed_combo.itemData(index)
        if not profile:
            return
        set_speed_profile(self.session, profile)
        self._append_system(f"speed: {self._speed_text(self._mode)}")
        self._populate_model_controls()
        self._queue_workspace_refresh()

    def _default_workspace_suggestions(self):
        suggestions = [
            {
                "kind": "prompt",
                "mode": "agent",
                "label": "Improve current structure view",
                "prompt": "Improve the current ChimeraX structure view for interpretability and apply the changes directly. Use a short safe action sequence and verify the visual change.",
            },
            {
                "kind": "prompt",
                "mode": "analyze",
                "label": "Explain likely active site",
                "prompt": "Identify and explain the most likely active site or functional pocket in the current ChimeraX view. Use evidence and confidence.",
            },
        ]
        figure_mode = recommended_figure_mode(self.session)
        if figure_mode:
            suggestions.append(
                {
                    "kind": "figure",
                    "mode": figure_mode,
                    "label": f"Apply recommended figure: {figure_mode}",
                }
            )
        return suggestions

    def _set_workspace_suggestions(self, suggestions):
        from Qt.QtCore import Qt
        from Qt.QtWidgets import QListWidgetItem

        self._workspace_suggestions = list(suggestions)
        self.suggestion_list.clear()
        for suggestion in self._workspace_suggestions:
            item = QListWidgetItem(self._suggestion_label(suggestion))
            item.setData(Qt.ItemDataRole.UserRole, suggestion)
            self.suggestion_list.addItem(item)
        if self.suggestion_list.count():
            self.suggestion_list.setCurrentRow(0)

    def _suggestion_label(self, suggestion):
        kind = suggestion.get("kind")
        if kind == "command":
            return f"Command: {suggestion.get('command', '')}"
        if kind == "figure":
            return f"Figure: {suggestion.get('mode', '')}"
        if kind == "prompt":
            return f"Prompt ({suggestion.get('mode', 'chat')}): {suggestion.get('label', '')}"
        return suggestion.get("label", "(suggestion)")

    def _response_suggestions(self, response):
        suggestions = []
        section = None
        for raw_line in str(response or "").splitlines():
            line = raw_line.strip()
            if line == "Suggested visuals":
                section = "visual"
                continue
            if line == "Suggested ChimeraX commands":
                section = "command"
                continue
            if line in {"Summary", "Observations", "Hypotheses", "Next checks"}:
                section = None
                continue
            if not line:
                continue
            if section == "visual" and line.startswith("- figure "):
                body = line[2:].strip()
                mode_part, _, reason = body.partition(":")
                _, _, mode = mode_part.partition("figure ")
                suggestions.append(
                    {
                        "kind": "figure",
                        "mode": mode.strip(),
                        "label": body,
                        "reason": reason.strip(),
                    }
                )
            elif section == "command" and line.startswith("- "):
                command = line[2:].strip()
                suggestions.append(
                    {
                        "kind": "command",
                        "command": command,
                        "label": command,
                    }
                )
        return suggestions

    def _selected_suggestion(self):
        from Qt.QtCore import Qt

        item = self.suggestion_list.currentItem()
        if item is None and self.suggestion_list.count():
            item = self.suggestion_list.item(0)
            self.suggestion_list.setCurrentItem(item)
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def _apply_suggestion_item(self, item):
        from Qt.QtCore import Qt

        suggestion = item.data(Qt.ItemDataRole.UserRole)
        self._apply_suggestion_payload(suggestion)

    def _apply_selected_suggestion(self):
        suggestion = self._selected_suggestion()
        if suggestion is None:
            self._append_error("no suggestion is available")
            return
        self._apply_suggestion_payload(suggestion)

    def _copy_selected_suggestion_to_prompt(self):
        suggestion = self._selected_suggestion()
        if suggestion is None:
            self._append_error("no suggestion is available")
            return
        kind = suggestion.get("kind")
        if kind == "command":
            text = suggestion.get("command", "")
        elif kind == "figure":
            text = f"figure {suggestion.get('mode', '')}"
        else:
            text = suggestion.get("prompt", suggestion.get("label", ""))
        self.prompt_edit.setPlainText(text)
        self.prompt_edit.setFocus()

    def _apply_suggestion_payload(self, suggestion):
        if not suggestion:
            self._append_error("no suggestion is available")
            return
        kind = suggestion.get("kind")
        if kind == "command":
            command = suggestion.get("command", "").strip()
            if not command:
                self._append_error("selected suggestion has no command")
                return
            self._append_system(f"interactive apply: {command}")
            try:
                with command_batch(self.session, f"Interactive command:{command[:80]}"):
                    result = self._run_command_thread_safe(command)
            except Exception as err:
                self._show_error(str(err) if str(err) else err.__class__.__name__)
                return
            status = str(result).strip() if result is not None else "ok"
            self._append_terminal(f"- {command}")
            if status != "ok":
                self._append_system(f"result: {status}")
            self._queue_workspace_refresh()
            return
        if kind == "figure":
            mode = suggestion.get("mode", "").strip()
            if not mode:
                self._append_error("selected suggestion has no figure mode")
                return
            self._append_system(f"interactive figure: {mode}")
            try:
                with command_batch(self.session, f"Interactive figure:{mode}"):
                    result = run_figure_mode(self.session, mode, executor=self._run_command_thread_safe)
            except Exception as err:
                self._show_error(str(err) if str(err) else err.__class__.__name__)
                return
            for line in result.splitlines():
                self._append_terminal(line)
            self._queue_workspace_refresh()
            return
        if kind == "prompt":
            prompt = suggestion.get("prompt", "").strip()
            mode = suggestion.get("mode", self._mode)
            if not prompt:
                self._append_error("selected suggestion has no prompt")
                return
            self._launch_quick_request(prompt, mode)
            return
        self._append_error("unknown suggestion type")

    def _launch_quick_request(self, prompt, mode):
        if mode in self._mode_order:
            self._mode = mode
        self._sync_control_widgets()
        self._start_request("run", prompt)

    def _quick_analyze_selection(self):
        self._launch_quick_request(
            "Analyze the current ChimeraX selection with evidence, confidence, and next checks. Focus tightly on the selected region if one exists.",
            "analyze",
        )

    def _quick_visualize_selection(self):
        mode = self._next_quick_view_mode()
        if mode:
            self._apply_suggestion_payload({"kind": "figure", "mode": mode, "label": f"figure {mode}"})
            return
        self._launch_quick_request(
            "Improve the current ChimeraX structure view for interpretation and apply the changes directly. If a selection exists, prioritize the selected region; otherwise improve the whole-structure view.",
            "agent",
        )

    def _quick_explain_site(self):
        mode = self._quick_site_mode()
        if mode:
            self._apply_suggestion_payload({"kind": "figure", "mode": mode, "label": f"figure {mode}"})
            return
        self._launch_quick_request(
            "Explain the most likely active site, interface, or functional hotspot in the current ChimeraX view. Use evidence and confidence and keep the explanation focused.",
            "analyze",
        )

    def _quick_apply_best_figure(self):
        mode = self._next_figure_cycle_mode()
        if not mode:
            self._append_error("no recommended figure mode is available right now")
            return
        self._apply_suggestion_payload({"kind": "figure", "mode": mode, "label": f"figure {mode}"})

    def _run_quick_slash(self, command):
        try:
            self._handle_slash_command(command)
        except Exception as err:
            self._show_error(str(err) if str(err) else err.__class__.__name__)

    def _run_quick_external(self, label, fn):
        self._append_system(f"{label}: starting...")
        self._set_result_status(f"{label}: starting...", tone="running")

        def worker():
            try:
                message = fn()
            except Exception as err:
                error = str(err) if str(err) else err.__class__.__name__
                self.session.ui.thread_safe(
                    lambda e=error: self._finish_quick_external(label, e, error=True)
                )
                return
            self.session.ui.thread_safe(
                lambda m=message: self._finish_quick_external(label, m, error=False)
            )

        threading.Thread(target=worker, daemon=True).start()

    def _finish_quick_external(self, label, message, error=False):
        if error:
            self._show_error(f"{label} failed: {message}")
            return
        detail = str(message or f"{label}: done").strip()
        self._append_system(detail)
        self._set_result_status(f"{label}: done", tone="success")
        self._set_result_detail(detail)
        self._queue_workspace_refresh()

    def _run_quick_builtin_async(self, label, command_text):
        def worker():
            command, _, rest = str(command_text or "").partition(" ")
            if not command:
                return f"{label}: no command"
            lines = []
            ok = run_builtin_slash(
                self.session,
                command.lower(),
                rest.strip(),
                lines.append,
                executor=self._run_command_thread_safe,
            )
            if not ok:
                raise RuntimeError(f"unknown command: {command_text}")
            return "\n".join(lines) if lines else f"{label}: done"

        self._run_quick_external(label, worker)

    def _run_quick_sequence_site(self, label, site):
        def worker():
            from .toolbar_actions import launch_sequence_analysis_site

            return launch_sequence_analysis_site(self.session, site)

        self._run_quick_external(label, worker)

    def _run_quick_nucleotide_pipeline(self, label, command):
        try:
            from .toolbar_actions import _prompt_nucleotide_sequence

            request_text = _prompt_nucleotide_sequence(self.session)
        except Exception as err:
            self._show_error(str(err) if str(err) else err.__class__.__name__)
            return
        if request_text is None:
            self._append_system(f"{label}: cancelled")
            return
        self._run_quick_builtin_async(label, f"{command} {request_text}")

    def _quick_sequence_summary(self):
        self._update_sequence_status()
        self._open_sequence_bar()
        self._append_system("top sequence bar refreshed")
        self._run_quick_slash("/sequence")

    def _quick_motif_summary(self):
        self._run_quick_slash("/motif")

    def _quick_motif_view(self):
        self._run_quick_slash("/motif view")

    def _quick_catalytic(self):
        self._run_quick_slash("/catalytic triage")

    def _quick_membrane(self):
        self._run_quick_builtin_async("Membrane", "/membrane view")

    def _quick_pisa(self):
        self._run_quick_builtin_async("PISA", "/pisa view")

    def _quick_similar_web(self):
        def worker():
            from .toolbar_actions import launch_similar_open_aligned

            return launch_similar_open_aligned(self.session, executor=self._run_command_thread_safe)

        self._run_quick_external("Similar", worker)

    def _quick_blast(self):
        self._run_quick_builtin_async("BLAST", "/blast")

    def _quick_hhpred(self):
        self._run_quick_sequence_site("HHpred", "hhpred")

    def _quick_alphafold(self):
        self._run_quick_sequence_site("AlphaFold", "alphafold")

    def _quick_similar(self):
        self._run_quick_builtin_async("Similar", "/similar")

    def _quick_foldmason(self):
        self._run_quick_builtin_async("FoldMason", "/foldmason")

    def _quick_folddisco(self):
        self._run_quick_builtin_async("FoldDisco", "/folddisco")

    def _quick_nucdock(self):
        self._run_quick_nucleotide_pipeline("NucDock", "/nucdock")

    def _quick_afcomplex(self):
        self._run_quick_nucleotide_pipeline("AF Complex", "/afcomplex")

    def _quick_boltz(self):
        self._run_quick_builtin_async("Boltz", "/boltz")

    def _quick_dali(self):
        self._run_quick_builtin_async("DALI", "/daliweb")

    def _quick_vast(self):
        self._run_quick_builtin_async("VAST", "/vast")

    def _quick_pdbefold(self):
        self._run_quick_builtin_async("PDBeFold", "/pdbefold")

    def _quick_usalign(self):
        self._run_quick_builtin_async("US-align", "/usalign")

    def _quick_profile(self):
        self._run_quick_sequence_site("UniProt", "uniprot")

    def _quick_conservation(self):
        self._run_quick_sequence_site("ConSurf", "consurf")

    def _next_quick_view_mode(self):
        from .semantic import best_interface_pair, best_ligand_site, best_metal_site, get_selection_overlap_payload

        payload = get_selection_overlap_payload(self.session)
        modes = []
        if payload is not None:
            if payload.get("interface_matches"):
                modes.append("selection-interface")
            if payload.get("motif_matches"):
                modes.append("selection-motif")
            if payload.get("ligand_matches") or payload.get("metal_matches") or payload.get("catalytic_hits"):
                modes.append("selection-pocket")
            modes.extend(["selection", "selection-composite"])
        else:
            if best_ligand_site(self.session) is not None or best_metal_site(self.session) is not None:
                modes.append("pocket")
            if best_interface_pair(self.session) is not None:
                modes.append("interface")
            modes.extend(["domains", "composite", "publication"])

        deduped = []
        for mode in modes:
            if mode not in deduped:
                deduped.append(mode)
        if not deduped:
            return None

        current = int(getattr(self.session, "_codex_bridge_quick_view_index", -1))
        index = (current + 1) % len(deduped)
        self.session._codex_bridge_quick_view_index = index
        return deduped[index]

    def _quick_site_mode(self):
        from .semantic import best_interface_pair, best_ligand_site, best_metal_site, get_selection_overlap_payload

        payload = get_selection_overlap_payload(self.session)
        if payload is not None:
            if payload.get("ligand_matches") or payload.get("metal_matches") or payload.get("catalytic_hits"):
                return "selection-pocket"
            if payload.get("interface_matches"):
                return "selection-interface"
            if payload.get("motif_matches"):
                return "selection-motif"
            return "selection"

        if best_ligand_site(self.session) is not None or best_metal_site(self.session) is not None:
            return "pocket"
        if best_interface_pair(self.session) is not None:
            return "interface"
        return None

    def _next_figure_cycle_mode(self):
        from .builtin_actions import _recommended_figure_modes

        modes = list(_recommended_figure_modes(self.session))
        if not modes:
            fallback = recommended_figure_mode(self.session)
            return fallback
        current = int(getattr(self.session, "_codex_bridge_quick_figure_index", -1))
        index = (current + 1) % len(modes)
        self.session._codex_bridge_quick_figure_index = index
        return modes[index]

    def _run_command_thread_safe(self, command):
        from chimerax.core.commands import run

        if _is_qt_main_thread():
            result = run(self.session, command)
            maybe_apply_stick_context_colors_for_command(self.session, command)
            return result

        result_box = {}
        event = threading.Event()

        def runner():
            try:
                result_box["result"] = run(self.session, command)
                maybe_apply_stick_context_colors_for_command(self.session, command)
            except Exception as err:
                result_box["error"] = err
            finally:
                event.set()

        self.session.ui.thread_safe(runner)
        event.wait()
        if "error" in result_box:
            raise result_box["error"]
        return result_box.get("result")

    def _progress_callback(self):
        def progress(message, kind="info"):
            self.session.ui.thread_safe(
                lambda m=message, k=kind: self._handle_progress(m, k)
            )

        return progress

    def _handle_progress(self, message, kind):
        if kind == "error":
            self.status_label.setText("Error.")
            self.stage_label.setText("Error")
            self._append_error(message)
            self._set_result_status(message, tone="error")
            self._set_result_detail(message)
        elif kind == "backend_stderr":
            self._saw_backend_output = True
            self._append_error(message)
        elif kind == "backend_stdout":
            self._saw_backend_output = True
            self._append_terminal(message)
        else:
            self.status_label.setText(message)
            self.stage_label.setText(str(message or "").replace("[", "").replace("]", ""))
            self._append_system(message)
            self._set_result_status(message, tone="running")

    def _response_indicates_no_visual_change(self, response):
        text = str(response or "")
        lowered = text.lower()
        if "executed chimeraX commands".lower() in lowered:
            return False
        if "[run]" in lowered:
            return False
        if "no commands to run" in lowered or "no actions to run" in lowered or "nothing to execute" in lowered:
            return True
        if "completed locally without backend round-trip" in lowered and "executed chimeraX commands".lower() not in lowered:
            return True
        return False

    def _append_terminal(self, line):
        if not str(line or "").strip():
            return
        self.terminal_edit.appendPlainText(line)
        scrollbar = self.terminal_edit.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _append_command_terminal(self, line):
        self.command_terminal_output.appendPlainText(line)
        scrollbar = self.command_terminal_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _append_system(self, line):
        self._append_terminal(f"# {line}")

    def _append_error(self, line):
        message = str(line or "").strip()
        if not message:
            return
        self._show_error_banner(message)
        self._append_terminal(f"! ERROR: {message}")
        self._set_result_detail(message)

    def _run_terminal_command(self):
        text = self.command_terminal_input.text().strip()
        if not text:
            return
        self.command_terminal_input.clear()

        shell_command = None
        chimera_command = None
        if text.startswith("!"):
            shell_command = text[1:].strip()
        elif text.lower().startswith("sh "):
            shell_command = text[3:].strip()
        else:
            chimera_command = text

        if shell_command:
            self._append_command_terminal(f"$ {shell_command}")
            worker = threading.Thread(
                target=self._run_shell_terminal_command,
                args=(shell_command,),
                daemon=True,
            )
            worker.start()
            return

        self._append_command_terminal(f"cx> {chimera_command}")
        try:
            result = self._run_command_thread_safe(chimera_command)
            status = str(result).strip() if result is not None else "ok"
            if status:
                for line in status.splitlines():
                    self._append_command_terminal(line)
            self._record_terminal_history("chimerax", chimera_command, status or "ok", status)
        except Exception as err:
            message = str(err) if str(err) else err.__class__.__name__
            self._append_command_terminal(f"error: {message}")
            self._record_terminal_history("chimerax", chimera_command, "error", message)
        self._queue_workspace_refresh()

    def _run_shell_terminal_command(self, command):
        try:
            completed = subprocess.run(
                ["/bin/zsh", "-lc", command],
                capture_output=True,
                text=True,
                timeout=60,
            )
            stdout = (completed.stdout or "").strip()
            stderr = (completed.stderr or "").strip()
            status = "ok" if completed.returncode == 0 else f"exit {completed.returncode}"
            payload = stdout if stdout else "(no stdout)"
            if stderr:
                payload = payload + ("\n" if payload else "") + stderr
        except Exception as err:
            status = "error"
            payload = str(err) if str(err) else err.__class__.__name__

        preview = payload.splitlines()[0][:160] if payload else ""

        def finalize():
            for line in (payload.splitlines()[:40] if payload else ["(no output)"]):
                self._append_command_terminal(line)
            self._record_terminal_history("shell", command, status, preview)
            self._queue_workspace_refresh()

        self.session.ui.thread_safe(finalize)

    def _record_terminal_history(self, kind, command, status, preview):
        history = getattr(self.session, "_codex_bridge_terminal_history", None)
        if history is None:
            self.session._codex_bridge_terminal_history = history = []
        history.append(
            {
                "kind": str(kind or "terminal"),
                "command": str(command or "").strip(),
                "status": str(status or "").strip(),
                "preview": str(preview or "").strip(),
            }
        )
        if len(history) > 40:
            del history[:-40]

    def _resize_prompt(self):
        line_height = self.prompt_edit.fontMetrics().lineSpacing()
        blocks = max(2, min(self.prompt_edit.document().blockCount(), 4))
        height = 22 + (line_height * blocks)
        self.prompt_edit.setFixedHeight(height)

    def _cycle_mode(self):
        current_index = self._mode_order.index(self._mode)
        self._mode = self._mode_order[(current_index + 1) % len(self._mode_order)]
        self._append_system(f"mode: {self._mode}")
        self.status_label.setText(f"Mode: {self._mode}")
        self.stage_label.setText(f"Mode · {self._mode}")
        self._sync_control_widgets()
        self._queue_workspace_refresh()

    def _handle_mode_command(self, prompt):
        lowered = prompt.strip().lower()
        if lowered in self._mode_order:
            self._mode = lowered
            self._append_system(f"mode: {self._mode}")
            self.status_label.setText(f"Mode: {self._mode}")
            self.stage_label.setText(f"Mode · {self._mode}")
            self._sync_control_widgets()
            self._queue_workspace_refresh()
            return True
        if lowered.startswith("mode "):
            _, _, arg = lowered.partition(" ")
            arg = arg.strip()
            if arg in self._mode_order:
                self._mode = arg
                self._append_system(f"mode: {self._mode}")
                self.status_label.setText(f"Mode: {self._mode}")
                self.stage_label.setText(f"Mode · {self._mode}")
                self._sync_control_widgets()
                self._queue_workspace_refresh()
                return True
        return False

    def _handle_slash_command(self, prompt):
        from .service import build_session_context

        command, _, rest = prompt.partition(" ")
        command = command.lower()
        arg = rest.strip()

        if command == "/clear":
            self._clear_output()
            return

        if command == "/help":
            self._append_system("/ask <text>         explanation only")
            self._append_system("/mode [name]        set mode: agent|analyze|visualize|chat")
            self._append_system("/speed [profile]    set speed: auto|fast|precise")
            self._append_system("/backend [name]     list or switch backend")
            self._append_system("/model [name]       show, list, or set model override")
            self._append_system("/effort [name]      show, list, or set reasoning effort")
            self._append_system("/status             backend/model/speed status")
            for line in list_builtin_commands():
                self._append_system(line)
            self._append_system("/memory             show recent analysis memory")
            self._append_system("/memory compare     compare latest two analyses")
            self._append_system("/clear              clear transcript")
            self._append_system("/fast on|off|auto   compatibility alias for speed")
            self._append_system("/context            show attached ChimeraX context")
            self._append_system("any other input runs the agent")
            return

        if command == "/context":
            self._append_system("current ChimeraX context:")
            for line in build_session_context(self.session).splitlines():
                self._append_system(line)
            return

        if command == "/fast":
            if arg.lower() in ("on", "true", "1", "fast"):
                set_speed_profile(self.session, "fast")
            elif arg.lower() in ("off", "false", "0", "precise"):
                set_speed_profile(self.session, "precise")
            elif arg.lower() == "auto":
                set_speed_profile(self.session, "auto")
            else:
                self._append_error("usage: /fast on|off|auto")
                return
            self._append_system(f"speed: {self._speed_text(self._mode)}")
            self._sync_control_widgets()
            self._queue_workspace_refresh()
            return

        if command == "/speed":
            if not arg:
                self._append_system(f"speed: {self._speed_text(self._mode)}")
                self._append_system("available: auto, fast, precise")
                return
            if arg.lower() not in ("auto", "fast", "precise"):
                self._append_error("usage: /speed auto|fast|precise")
                return
            set_speed_profile(self.session, arg.lower())
            self._append_system(f"speed: {self._speed_text(self._mode)}")
            self._sync_control_widgets()
            self._queue_workspace_refresh()
            return

        if command == "/mode":
            if not arg:
                self._append_system(f"mode: {self._mode}")
                self._append_system("available: agent, analyze, visualize, chat")
                return
            if arg.lower() not in self._mode_order:
                self._append_error("usage: /mode agent|analyze|visualize|chat")
                return
            self._mode = arg.lower()
            self._append_system(f"mode: {self._mode}")
            self._sync_control_widgets()
            self._queue_workspace_refresh()
            return

        if command == "/backend":
            if not arg:
                for line in backend_status_lines(self.session):
                    self._append_system(line)
                return
            backend_id = arg.lower()
            if backend_id not in list_backend_ids():
                self._append_error(f"unknown backend: {backend_id}")
                return
            set_current_backend_id(self.session, backend_id)
            self._append_system(f"backend: {backend_id} ({self._backend_label()})")
            self._append_system(f"speed: {self._speed_text(self._mode)}")
            self._append_system(f"model: {self._active_model_display(self._mode)}")
            self._append_system(f"effort: {self._active_effort_display(self._mode)}")
            self._sync_control_widgets()
            self._queue_workspace_refresh()
            return

        if command == "/model":
            if not arg:
                self._append_system(f"model: {self._active_model_display(self._mode)}")
                self._append_system("suggested: " + ", ".join(suggested_models_for_backend(get_current_backend_id(self.session))))
                return
            if arg.lower() in ("list", "ls", "?"):
                self._append_system("suggested: " + ", ".join(suggested_models_for_backend(get_current_backend_id(self.session))))
                return
            if arg.lower() in ("default", "reset", "clear"):
                clear_model_override(self.session)
                self._append_system("model override: cleared")
            else:
                set_model_override(self.session, arg)
                self._append_system(f"model override: {arg}")
            self._append_system(f"model: {self._active_model_display(self._mode)}")
            self._sync_control_widgets()
            self._queue_workspace_refresh()
            return

        if command == "/effort":
            current_backend = get_current_backend_id(self.session)
            if not arg:
                self._append_system(f"effort: {self._active_effort_display(self._mode)}")
                efforts = suggested_efforts_for_backend(current_backend)
                self._append_system("suggested: " + (", ".join(efforts) if efforts else "(not supported)"))
                return
            if arg.lower() in ("list", "ls", "?"):
                efforts = suggested_efforts_for_backend(current_backend)
                self._append_system("suggested: " + (", ".join(efforts) if efforts else "(not supported)"))
                return
            if arg.lower() in ("default", "reset", "clear"):
                clear_effort_override(self.session)
                self._append_system("effort override: cleared")
                self._append_system(f"effort: {self._active_effort_display(self._mode)}")
                self._sync_control_widgets()
                self._queue_workspace_refresh()
                return
            efforts = suggested_efforts_for_backend(current_backend)
            if not efforts:
                self._append_error(f"{self._backend_label()} does not support effort control here")
                return
            if arg.lower() not in efforts:
                self._append_error(f"unknown effort: {arg}")
                return
            set_effort_override(self.session, arg.lower())
            self._append_system(f"effort override: {arg.lower()}")
            self._append_system(f"effort: {self._active_effort_display(self._mode)}")
            self._sync_control_widgets()
            self._queue_workspace_refresh()
            return

        if command == "/status":
            self._append_system(f"backend: {get_current_backend_id(self.session)} ({self._backend_label()})")
            self._append_system(f"mode: {self._mode}")
            self._append_system(f"speed: {self._speed_text(self._mode)}")
            self._append_system(f"model: {self._active_model_display(self._mode)}")
            self._append_system(f"effort: {self._active_effort_display(self._mode)}")
            return

        if command == "/memory":
            memory = getattr(self.session, "_codex_bridge_analysis_memory", [])
            if not memory:
                self._append_system("(no analysis memory yet)")
                return
            if arg and arg.lower() == "compare" and len(memory) >= 2:
                for line in format_memory_compare(self.session).splitlines():
                    self._append_system(line)
                return
            for idx, item in enumerate(memory, start=1):
                self._append_system(f"memory {idx}: {item['request']}")
            return

        if command == "/ask":
            if not arg:
                self._append_error("usage: /ask <text>")
                return
            self._start_request("ask", arg)
            return

        if run_builtin_slash(self.session, command, arg, self._append_system, executor=self._run_command_thread_safe):
            return

        self._append_error(f"unknown slash command: {command}")

    def _handle_plain_command(self, prompt):
        stripped = prompt.strip()
        if not stripped:
            return True

        command, _, rest = stripped.partition(" ")
        command = command.lower()
        arg = rest.strip()

        backend_aliases = {
            "codex": "codex",
            "claude": "claude",
            "ccd": "claude",
            "gemini": "gemini",
        }
        if command in backend_aliases and not arg:
            backend_id = backend_aliases[command]
            set_current_backend_id(self.session, backend_id)
            self._append_system(f"backend: {backend_id} ({self._backend_label()})")
            self._append_system(f"speed: {self._speed_text(self._mode)}")
            self._append_system(f"model: {self._active_model_display(self._mode)}")
            self._sync_control_widgets()
            self._queue_workspace_refresh()
            return True

        builtin_aliases = {
            "mode": "/mode",
            "model": "/model",
            "status": "/status",
            "models": "/models",
            "selected": "/selected",
            "groups": "/groups",
            "workspace": "/workspace",
            "legend": "/legend",
            "caption": "/caption",
            "panels": "/panels",
            "package": "/package",
            "scene": "/scene",
            "blast": "/blast",
            "alphafoldtool": "/alphafoldtool",
            "hhpred": "/hhpred",
            "hhblits": "/hhpred",
            "daliweb": "/daliweb",
            "vast": "/vast",
            "pdbefold": "/pdbefold",
            "ssm": "/pdbefold",
            "pisa": "/pisa",
            "pisaweb": "/pisaweb",
            "pdbe-pisa": "/pisa",
            "pdbepisa": "/pisa",
            "usalign": "/usalign",
            "us-align": "/usalign",
            "tmalign": "/usalign",
            "tm-align": "/usalign",
            "similar": "/similar",
            "foldmason": "/foldmason",
            "folddisco": "/folddisco",
            "nucdock": "/nucdock",
            "nucleotide": "/nucdock",
            "nucleotides": "/nucdock",
            "afcomplex": "/afcomplex",
            "boltz": "/boltz",
            "seqview": "/seqview",
            "profile": "/profile",
            "dali": "/dali",
            "daliurl": "/daliurl",
            "dalisummary": "/dalisummary",
            "dalistatus": "/dalistatus",
            "sequence": "/sequence",
            "domains": "/domains",
            "complex": "/complex",
            "interfaces": "/interfaces",
            "interfacesselect": "/interfacesselect",
            "roles": "/roles",
            "annotate": "/annotate",
            "features": "/features",
            "motif": "/motif",
            "conservation": "/conservation",
            "consurf": "/consurf",
            "ligand": "/ligand",
            "pocket": "/ligand",
            "catalytic": "/catalytic",
            "active": "/catalytic",
            "activesite": "/catalytic",
            "catalyticview": "/catalytic view",
            "metal": "/metal",
            "residue": "/residue",
            "site": "/site",
            "show": "/show",
            "hide": "/hide",
            "select": "/select",
            "distance": "/distance",
            "angle": "/angle",
            "buriedarea": "/buriedarea",
            "measurearea": "/measurearea",
            "contactarea": "/contactarea",
            "convexity": "/convexity",
            "hbonds": "/hbonds",
            "hbondsdelete": "/hbondsdelete",
            "contacts": "/contacts",
            "contactsdelete": "/contactsdelete",
            "clashes": "/clashes",
            "clashesdelete": "/clashesdelete",
            "zone": "/zone",
            "surfacezone": "/surfacezone",
            "surfaceunzone": "/surfaceunzone",
            "turn": "/turn",
            "rock": "/rock",
            "wobble": "/wobble",
            "zoom": "/zoom",
            "wait": "/wait",
            "stop": "/stop",
            "close": "/close",
            "memory": "/memory",
            "chains": "/chains",
            "analyze": "/analyze",
            "style": "/style",
            "color": "/color",
            "palette": "/palette",
            "transparency": "/transparency",
            "focus": "/focus",
            "layout": "/layout",
            "snapshot": "/snapshot",
            "movie": "/movie",
            "figure": "/figure",
            "speed": "/speed",
            "fast": "/fast",
            "effort": "/effort",
            "clear": "/clear",
            "context": "/context",
            "help": "/help",
            "ask": "/ask",
        }
        if command in builtin_aliases:
            slash = builtin_aliases[command]
            synthetic = slash if not arg else f"{slash} {arg}"
            self._handle_slash_command(synthetic)
            return True

        return False

    def _backend_label(self):
        return get_backend_label(get_current_backend_id(self.session))

    def _backend_combo_label(self, backend_id, available=None, status=None):
        detail = {
            "codex": "local app",
            "openai": "direct API",
            "claude": "local CLI",
            "gemini": "local CLI",
        }.get(backend_id, "engine")
        if available is None:
            available, status, _detail = backend_availability(backend_id)
        status_text = status or ("ready" if available else "unavailable")
        return f"{get_backend_label(backend_id)} · {detail} · {status_text}"

    def _request_mode_for_action(self, action):
        return "chat" if action == "ask" else self._mode

    def _effective_fast_mode(self, request_mode=None):
        fast_mode, _profile, _effective = resolve_request_quality(self.session, request_mode or self._mode)
        return fast_mode

    def _speed_text(self, request_mode=None):
        _fast_mode, profile, effective = resolve_request_quality(self.session, request_mode or self._mode)
        return profile if profile != "auto" else f"auto -> {effective}"

    def _active_model_display(self, request_mode=None):
        backend_id = get_current_backend_id(self.session)
        override = get_model_override(self.session, backend_id)
        if override:
            return override
        return self._active_default_model_display(request_mode)

    def _active_default_model_display(self, request_mode=None):
        backend_id = get_current_backend_id(self.session)
        fast_mode = self._effective_fast_mode(request_mode)
        model, _reasoning = get_backend_defaults(backend_id, fast_mode)
        return model or "(provider default)"

    def _active_effort_display(self, request_mode=None):
        backend_id = get_current_backend_id(self.session)
        override = get_effort_override(self.session, backend_id)
        if override:
            return override
        return self._active_default_effort_display(request_mode)

    def _active_default_effort_display(self, request_mode=None):
        backend_id = get_current_backend_id(self.session)
        fast_mode = self._effective_fast_mode(request_mode)
        _model, reasoning = get_backend_defaults(backend_id, fast_mode)
        return reasoning or "(provider default)"
