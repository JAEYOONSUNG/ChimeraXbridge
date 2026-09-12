"""Compact, scrollable bookmarks with selectable camera and appearance state."""

from chimerax.core.tools import ToolInstance, get_singleton
from chimerax.core.settings import Settings
from Qt.QtCore import Qt
from Qt.QtWidgets import QApplication, QComboBox, QLabel, QListWidget, QSizePolicy

from .panel_scroll import PanelScrollArea


_SAVE_OPTIONS = (
    ("camera", "Camera", "Save camera angle, zoom, pivot, clipping and model positions."),
    ("visibility", "Display", "Save shown/hidden models, atoms, cartoons, surfaces and drawing styles."),
    ("colors", "Colors", "Save atom, cartoon and surface colors, including transparency."),
    ("lighting", "Light / BG", "Save lighting, background and silhouette settings."),
    ("selection", "Selection", "Save the selected atoms and models."),
)


class BookmarkSettings(Settings):
    AUTO_SAVE = {"camera": True, "visibility": True, "colors": True,
                 "lighting": True, "selection": False}


class ImageExportSettings(Settings):
    AUTO_SAVE = {"format": "PNG", "width": 0, "height": 0, "dpi": 300,
                 "lock_ratio": True, "transparent": False, "directory": ""}


def _image_export_settings(session):
    settings = getattr(session, "_codex_image_export_settings", None)
    if settings is None:
        settings = ImageExportSettings(session, "Codex Image Export")
        session._codex_image_export_settings = settings
    return settings


def _bookmark_settings(session):
    settings = getattr(session, "_codex_bookmark_settings", None)
    if settings is None:
        session._codex_bookmark_settings = settings = BookmarkSettings(session, "Codex Bookmarks")
    return settings


class _BookmarkCombo(QComboBox):
    def wheelEvent(self, event):
        # Wheel gestures scroll the panel instead of changing a saved condition.
        event.ignore()


class _BookmarkScrollArea(PanelScrollArea):
    """Compatibility name for the shared full-panel scroll behavior."""


class _BookmarkStatus(QLabel):
    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(18)
        self.setMinimumWidth(0)
        self.setText(text)

    def setText(self, text):
        self._message = str(text)
        self.setToolTip(self._message)
        self._elide()

    def _elide(self):
        super().setText(self.fontMetrics().elidedText(
            self._message, Qt.TextElideMode.ElideRight, max(0, self.contentsRect().width())))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()


def _list_named_views(session):
    """Return the names of currently saved views as known to ChimeraX.
    Named views live on session._named_views.views (a NamedViews instance,
    see chimerax.std_commands.view._named_views)."""
    try:
        from chimerax.std_commands.view import _named_views as _nv_helper
        nvs = _nv_helper(session)
        if nvs is not None and hasattr(nvs, "views"):
            return list(nvs.views.keys())
    except Exception:
        pass
    # Direct attribute fallback
    try:
        nvs = getattr(session, "_named_views", None)
        if nvs is not None and hasattr(nvs, "views"):
            return list(nvs.views.keys())
    except Exception:
        pass
    # Last-resort fallback: parse `view list` output.
    try:
        from chimerax.core.commands import run as cx_run
        result = cx_run(session, "view list", log=False)
        if isinstance(result, list):
            return [str(x) for x in result]
        if isinstance(result, str):
            names = [s.strip() for s in result.replace(",", "\n").splitlines()]
            return [n for n in names if n]
    except Exception:
        pass
    return []


class CameraBookmarks(ToolInstance):

    SESSION_ENDURING = False
    # Native views and the condition StateManager own saved bookmarks. The
    # panel itself carries no scene data and does not need session restoration.
    SESSION_SAVE = False
    help = None

    UI_LAYOUT_VERSION = 6

    @classmethod
    def get_singleton(cls, session, create=True, display=True):
        instance = get_singleton(session, cls, "Camera Bookmarks", create=create, display=display)
        if instance is not None and (getattr(instance, "_disposed", False)
                or getattr(instance, "_ui_layout_version", None) != cls.UI_LAYOUT_VERSION):
            instance.delete()
            instance = get_singleton(session, cls, "Camera Bookmarks", create=create, display=display)
        return instance

    def __init__(self, session, tool_name):
        super().__init__(session, tool_name)
        from chimerax.ui import MainToolWindow
        from Qt.QtWidgets import (QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
            QListWidget, QLineEdit, QSizePolicy, QGridLayout, QWidget,
            QScrollArea, QLayout, QToolButton, QMenu, QAbstractItemView,
            QCheckBox, QGroupBox, QWidgetAction)
        from Qt.QtCore import Qt, QTimer
        from pathlib import Path
        from .ui_theme import panel_stylesheet

        self._ui_layout_version = self.UI_LAYOUT_VERSION
        self._disposed = False
        self._native_destroyed = False
        self._delete_called = False
        self._settings = _bookmark_settings(session)
        self._export_settings = _image_export_settings(session)
        self.tool_window = MainToolWindow(self)
        parent = self.tool_window.ui_area
        parent.destroyed.connect(self._native_ui_destroyed)
        parent.setObjectName("codex_camera_bookmarks")
        chevron = (Path(__file__).parent / "icons/chevron-down.svg").as_posix()
        parent.setStyleSheet(panel_stylesheet("codex_camera_bookmarks") +
            "QWidget { font-size: 12px; }"
            "QPushButton, QToolButton, QComboBox, QLineEdit, QSpinBox {"
            " font-size: 12px; padding: 2px 6px; min-height: 20px; max-height: 20px; }"
            "QComboBox { padding-right: 22px; }"
            "QComboBox::drop-down { border: none; width: 20px; }"
            f"QComboBox::down-arrow {{ image: url('{chevron}'); width: 12px; height: 12px; }}"
            "QToolButton#bookmark_legend, QToolButton#bookmark_options { padding-right: 20px; }"
            "QToolButton#bookmark_legend::menu-indicator, QToolButton#bookmark_options::menu-indicator { width: 8px; height: 8px;"
            " subcontrol-position: right center; right: 5px; }"
            "QToolButton#bookmark_more::menu-indicator { image: none; width: 0; height: 0; }"
            "QToolButton#bookmark_export_size { padding-right: 22px; }"
            "QToolButton#bookmark_export_size::menu-button { width: 18px; border-left: 1px solid palette(mid); }"
            f"QToolButton#bookmark_export_size::menu-arrow {{ image: url('{chevron}'); width: 16px; height: 16px; }}"
            "QGroupBox { border: 1px solid palette(mid);"
            " border-radius: 4px; margin-top: 9px; padding-top: 5px; }"
            "QGroupBox::title { subcontrol-origin: margin;"
            " left: 8px; padding: 0 4px; color: palette(window-text); font-weight: 600; }"
            "QCheckBox { font-size: 12px; spacing: 6px; }"
            "QPushButton#bookmark_export { background: palette(highlight);"
            " color: palette(highlighted-text); border-color: palette(highlight); font-weight: 600; }"
            "QListWidget::item { min-height: 24px; padding: 1px 4px; }"
            "QScrollBar:vertical { width: 10px; }")
        wrapper = QVBoxLayout(parent)
        wrapper.setContentsMargins(0, 0, 0, 0)
        self.scroll_area = _BookmarkScrollArea(parent)
        self.scroll_area.setObjectName("bookmark_scroll")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.content = QWidget()
        self.content.setObjectName("bookmark_content")
        outer = QVBoxLayout(self.content)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(5)
        # The content keeps its usable minimum height; the dock itself can shrink.
        outer.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.scroll_area.setWidget(self.content)
        wrapper.addWidget(self.scroll_area)
        parent.setMinimumSize(0, 0)

        pivot_row = QHBoxLayout()
        pivot_row.setSpacing(5)
        pivot_row.addWidget(QLabel("Pivot"))
        self.cofr_combo = _BookmarkCombo()
        self.cofr_combo.addItems(["Current", "Selection", "All models"])
        self.cofr_combo.setMinimumContentsLength(8)
        self.cofr_combo.setSizeAdjustPolicy(self.cofr_combo.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.cofr_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.cofr_combo.setToolTip("Rotate around the current pivot, the selected atoms, or all models.")
        pivot_row.addWidget(self.cofr_combo, 1)
        pivot_row.addWidget(QLabel("Step"))
        self.step_combo = _BookmarkCombo()
        self.step_combo.addItems(["15°", "30°", "45°", "90°", "180°"])
        self.step_combo.setCurrentText("90°")
        self.step_combo.setFixedWidth(70)
        pivot_row.addWidget(self.step_combo)
        outer.addLayout(pivot_row)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        rotation = QWidget()
        rotation.setObjectName("bookmark_rotation")
        rotation.setFixedWidth(104)
        rotation_layout = QVBoxLayout(rotation)
        rotation_layout.setContentsMargins(0, 0, 0, 0)
        rotation_layout.setSpacing(5)
        rotation_header = QHBoxLayout()
        rotation_header.addWidget(QLabel("Rotate"))
        self.reset_btn = QPushButton("Reset")
        self.reset_btn.setToolTip("Reset the camera orientation.")
        self.reset_btn.clicked.connect(self._reset_orient)
        rotation_header.addWidget(self.reset_btn)
        rotation_layout.addLayout(rotation_header)
        grid = QGridLayout()
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(4)
        self.rotation_buttons = []
        for row, axis in enumerate("xyz"):
            grid.addWidget(QLabel(axis.upper()), row, 0)
            for column, (label, sign) in enumerate((("−", -1), ("+", 1)), 1):
                button = QPushButton(label)
                button.setFixedWidth(36)
                button.setToolTip(f"Rotate {axis.upper()} {'backward' if sign < 0 else 'forward'} by the selected step.")
                button.clicked.connect(lambda _checked=False, a=axis, s=sign: self._rotate(a, s))
                grid.addWidget(button, row, column)
                self.rotation_buttons.append(button)
        rotation_layout.addLayout(grid)
        controls.addWidget(rotation)

        # Image output settings are always visible beside the compact axes.
        self.export_group = self._build_export_controls()
        controls.addWidget(self.export_group, 1)
        self.focus_btn = QPushButton("Focus sel")
        self.focus_btn.setToolTip("Center and zoom on the current selection.")
        self.focus_btn.clicked.connect(self._center_on_selection)
        rotation_layout.addWidget(self.focus_btn)
        rotation_layout.addStretch(1)
        outer.addLayout(controls)

        # Keep existing bookmark-condition functionality in its own popup.
        self.conditions_group = QGroupBox("Bookmark contents")
        self.conditions_group.setObjectName("bookmark_save_conditions")
        conditions = QVBoxLayout(self.conditions_group)
        conditions.setContentsMargins(8, 9, 8, 6)
        conditions.setSpacing(5)
        self.preset_combo = _BookmarkCombo()
        self.preset_combo.addItem("View + appearance", "appearance")
        self.preset_combo.addItem("Camera only", "camera")
        self.preset_combo.addItem("Custom conditions", "custom")
        conditions.addWidget(self.preset_combo)
        toggles = QGridLayout()
        self.include_buttons = {}
        for index, (key, label, tooltip) in enumerate(_SAVE_OPTIONS):
            button = QCheckBox(label)
            button.setChecked(getattr(self._settings, key))
            button.setToolTip(tooltip)
            button.toggled.connect(self._save_options_changed)
            toggles.addWidget(button, index // 2, index % 2)
            self.include_buttons[key] = button
        conditions.addLayout(toggles)
        self.preset_combo.currentIndexChanged.connect(self._apply_save_preset)
        self._sync_save_preset()

        save_row = QHBoxLayout()
        save_row.setSpacing(5)
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Bookmark name (optional)")
        self.name_input.setMinimumWidth(0)
        self.name_input.returnPressed.connect(self._save_view)
        save_row.addWidget(self.name_input, 1)
        self.bookmark_options_button = QToolButton()
        self.bookmark_options_button.setObjectName("bookmark_options")
        self.bookmark_options_button.setText("Options")
        self.bookmark_options_button.setToolTip("Choose which scene conditions a bookmark remembers.")
        self.bookmark_options_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        bookmark_menu = QMenu(self.bookmark_options_button)
        options_action = QWidgetAction(bookmark_menu)
        options_action.setDefaultWidget(self.conditions_group)
        bookmark_menu.addAction(options_action)
        self.bookmark_options_button.setMenu(bookmark_menu)
        save_row.addWidget(self.bookmark_options_button)
        self.save_btn = QPushButton("Bookmark")
        self.save_btn.setObjectName("bookmark_save")
        self.save_btn.setMinimumWidth(62)
        self.save_btn.setEnabled(any(self._save_options().values()))
        self.save_btn.clicked.connect(self._save_view)
        save_row.addWidget(self.save_btn)
        outer.addLayout(save_row)

        self.list_label = QLabel("Saved bookmarks · click to restore")
        outer.addWidget(self.list_label)
        self.list_widget = QListWidget()
        self.list_widget.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list_widget.itemClicked.connect(self._on_item_clicked)
        self.list_widget.itemActivated.connect(self._on_item_clicked)
        self.list_widget.setMinimumHeight(86)
        self.list_widget.setMinimumWidth(0)
        self.list_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        outer.addWidget(self.list_widget, 1)

        actions = QHBoxLayout()
        actions.setSpacing(4)
        for attr, label, callback in (("refresh_btn", "Refresh", self._refresh_list),
                ("rename_btn", "Rename", self._rename_selected),
                ("delete_btn", "Delete", self._delete_selected)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            setattr(self, attr, button)
            actions.addWidget(button, 1)
        self.legend_button = QToolButton()
        self.legend_button.setObjectName("bookmark_legend")
        self.legend_button.setText("Legend")
        self.legend_button.setToolTip("Add or remove a color legend for screenshots.")
        self.legend_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        legend_menu = QMenu(self.legend_button)
        self._legend_choices = (
            "Hydrophobicity (mlp: darkcyan→white→darkgoldenrod)",
            "Hydrophobicity (Kyte-Doolittle: blue→white→red)",
            "Conservation (red→white)", "Custom (blue→white→red)")
        for index, label in enumerate(("Hydrophobicity · MLP", "Hydrophobicity · Kyte–Doolittle", "Conservation", "Custom")):
            legend_menu.addAction(label, lambda i=index: self._add_legend(i))
        legend_menu.addSeparator()
        legend_menu.addAction("Remove legend", self._clear_legend)
        self.legend_button.setMenu(legend_menu)
        actions.addWidget(self.legend_button)
        self.more_button = QToolButton()
        self.more_button.setObjectName("bookmark_more")
        self.more_button.setText("⋯")
        self.more_button.setFixedWidth(30)
        self.more_button.setToolTip("More bookmark actions")
        self.more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        more_menu = QMenu(self.more_button)
        more_menu.addAction("Clear all bookmarks…", self._delete_all)
        self.more_button.setMenu(more_menu)
        actions.addWidget(self.more_button)
        outer.addLayout(actions)
        self.status = _BookmarkStatus("")
        outer.addWidget(self.status)
        self._refresh_list()
        self.tool_window.manage(placement="side")
        try:
            from . import _schedule_helper_dock_layout
            _schedule_helper_dock_layout(self.session, raise_tool="camera bookmarks")
        except Exception:
            pass
        self._reveal_export_timer = QTimer(self.content)
        self._reveal_export_timer.setSingleShot(True)
        self._reveal_export_timer.timeout.connect(self._reveal_image_export)

    def show_image_export(self):
        """Open the export controls even after the bookmark list was scrolled."""
        if self._disposed:
            return
        self.display(True)
        dock = getattr(self.tool_window, "_dock_widget", None)
        if dock is not None:
            dock.raise_()
        self._reveal_image_export()
        # The native dock can finish changing tabs after this call. Parent the
        # timer to the content so closing the tool also cancels its callback.
        self._reveal_export_timer.start(0)

    def _reveal_image_export(self):
        if self._disposed:
            return
        target = (self.export_group if self.export_group.height() + 12
                  <= self.scroll_area.viewport().height() else self.export_button)
        self.scroll_area.ensureWidgetVisible(target, 0, 6)

    def _mark_disposed(self, *_args):
        self._disposed = True

    def _native_ui_destroyed(self, *_args):
        self._native_destroyed = True
        self._mark_disposed()

    def delete(self):
        if self._delete_called:
            return
        self._delete_called = True
        self._mark_disposed()
        if self._native_destroyed:
            from .ui_theme import forget_destroyed_tool_window
            forget_destroyed_tool_window(self)
        super().delete()

    def _build_export_controls(self):
        from Qt.QtWidgets import (QGroupBox, QVBoxLayout, QHBoxLayout, QLabel,
                                  QSpinBox, QCheckBox, QPushButton, QToolButton, QMenu)
        from .image_export import MAX_DIMENSION, MAX_DPI
        group = QGroupBox("이미지 내보내기 · px")
        group.setObjectName("bookmark_image_export")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(8, 9, 8, 6)
        layout.setSpacing(4)
        settings = self._export_settings
        self._export_size_updating = False

        format_row = QHBoxLayout()
        format_row.setSpacing(4)
        self.export_format = _BookmarkCombo()
        self.export_format.addItems(["PNG", "JPEG", "TIFF"])
        self.export_format.setCurrentText(settings.format if settings.format in ("PNG", "JPEG", "TIFF") else "PNG")
        self.export_format.setToolTip("Image file format")
        format_row.addWidget(self.export_format, 1)
        format_row.addWidget(QLabel("DPI"))
        self.export_dpi = QSpinBox()
        self.export_dpi.setRange(1, MAX_DPI)
        self.export_dpi.setValue(settings.dpi)
        self.export_dpi.setKeyboardTracking(False)
        self.export_dpi.setFixedWidth(68)
        self.export_dpi.setToolTip("Print resolution stored in the exported file. Pixel dimensions are set below.")
        format_row.addWidget(self.export_dpi)
        layout.addLayout(format_row)

        size_row = QHBoxLayout()
        size_row.setSpacing(4)
        self.export_width = QSpinBox()
        self.export_height = QSpinBox()
        for label, field in (("W", self.export_width), ("H", self.export_height)):
            field.setRange(16, MAX_DIMENSION)
            field.setMinimumWidth(0)
            field.setKeyboardTracking(False)
            field.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            field.setToolTip("Width in pixels" if label == "W" else "Height in pixels")
            size_row.addWidget(QLabel(label))
            size_row.addWidget(field, 1)
        current_width, current_height = self._current_image_size()
        width = settings.width if settings.width >= 16 else current_width
        height = settings.height if settings.height >= 16 else current_height
        self.export_width.setValue(width)
        self.export_height.setValue(height)
        self._export_aspect = self.export_width.value() / self.export_height.value()
        layout.addLayout(size_row)

        ratio_row = QHBoxLayout()
        self.export_lock_ratio = QCheckBox("Lock ratio")
        self.export_lock_ratio.setChecked(settings.lock_ratio)
        self.export_lock_ratio.setToolTip("Keep the image's current width-to-height ratio while editing its dimensions.")
        ratio_row.addWidget(self.export_lock_ratio)
        self.export_current_button = QToolButton()
        self.export_current_button.setText("Current")
        self.export_current_button.setObjectName("bookmark_export_size")
        self.export_current_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.export_current_button.setToolTip(
            "Click Current for the graphics view size. The arrow offers common widths, 2× view, and Fit to limit. DPI stays unchanged.")
        self.export_current_button.clicked.connect(self._use_current_image_size)
        self.export_size_menu = QMenu(self.export_current_button)
        self.export_size_menu.setToolTipsVisible(True)
        self._export_size_actions = {}
        for key, title in (("current", "Current view"), ("view2x", "2× current view"),
                           (1600, "Width 1600 px"), (2400, "Width 2400 px"),
                           (3840, "Width 3840 px"), ("fit", "Fit to 32 MP limit")):
            if key in (1600, "fit"):
                self.export_size_menu.addSeparator()
            action = self.export_size_menu.addAction(title)
            action.triggered.connect(lambda _checked=False, key=key: self._apply_export_size_preset(key))
            self._export_size_actions[key] = action
        self.export_size_menu.aboutToShow.connect(self._refresh_export_size_menu)
        self.export_current_button.setMenu(self.export_size_menu)
        ratio_row.addWidget(self.export_current_button)
        layout.addLayout(ratio_row)
        self.export_transparent = QCheckBox("Transparent bg")
        self.export_transparent.setChecked(settings.transparent)
        self.export_transparent.setToolTip("Transparent background for PNG and TIFF. JPEG uses the current background color.")
        layout.addWidget(self.export_transparent)
        self.export_size_hint = _BookmarkStatus("")
        layout.addWidget(self.export_size_hint)
        self.export_button = QPushButton("Export PNG…")
        self.export_button.setObjectName("bookmark_export")
        self.export_button.setToolTip("Choose a destination and export the current 3D view as an image.")
        self.export_button.clicked.connect(self._export_image)
        layout.addWidget(self.export_button)

        self.export_format.currentTextChanged.connect(self._export_options_changed)
        self.export_dpi.valueChanged.connect(self._export_options_changed)
        self.export_width.valueChanged.connect(lambda value: self._export_dimension_changed("width", value))
        self.export_height.valueChanged.connect(lambda value: self._export_dimension_changed("height", value))
        self.export_lock_ratio.toggled.connect(self._export_ratio_changed)
        self.export_transparent.toggled.connect(self._export_options_changed)
        self._export_options_changed()
        return group

    def _current_image_size(self):
        width, height = getattr(self.session.main_view, "window_size", (1200, 900))
        return max(16, int(width)), max(16, int(height))

    def _export_preset_size(self, key):
        from math import sqrt
        from .image_export import MAX_DIMENSION, MAX_PIXELS
        width, height = self.export_width.value(), self.export_height.value()
        if key == "current":
            return self._current_image_size()
        if key == "view2x":
            current = self._current_image_size()
            return current[0] * 2, current[1] * 2
        if key == "fit":
            scale = min(1., MAX_DIMENSION / width, MAX_DIMENSION / height,
                        sqrt(MAX_PIXELS / (width * height)))
            return max(16, int(width * scale)), max(16, int(height * scale))
        if key in (1600, 2400, 3840):
            return key, round(key * height / width)
        raise ValueError("Unknown image-size preset")

    def _refresh_export_size_menu(self):
        if self._disposed:
            return
        from .image_export import MAX_DIMENSION, MAX_PIXELS
        for key, action in self._export_size_actions.items():
            width, height = self._export_preset_size(key)
            label = ("Current view" if key == "current" else "2× current view" if key == "view2x"
                     else "Fit to 32 MP limit" if key == "fit" else f"Width {key} px")
            action.setText(f"{label} · {width} × {height}")
            valid = (16 <= width <= MAX_DIMENSION and 16 <= height <= MAX_DIMENSION
                     and width * height <= MAX_PIXELS)
            changed = (width, height) != (self.export_width.value(), self.export_height.value())
            action.setEnabled(valid and (key != "fit" or changed)
                              and not getattr(self, "_export_busy", False))
            ratio = "graphics view" if key in ("current", "view2x") else "current image"
            action.setToolTip(
                f"Uses the {ratio}'s aspect ratio. DPI and background stay unchanged." if valid else
                f"This shape is outside 16–{MAX_DIMENSION} px per side or exceeds {MAX_PIXELS / 1e6:g} MP. Choose another width.")

    def _apply_export_size_preset(self, key):
        if self._disposed or getattr(self, "_export_busy", False):
            return
        self._refresh_export_size_menu()
        if not self._export_size_actions[key].isEnabled():
            return
        width, height = self._export_preset_size(key)
        self._export_size_updating = True
        try:
            self.export_width.setValue(width)
            self.export_height.setValue(height)
        finally:
            self._export_size_updating = False
        self._export_aspect = self.export_width.value() / self.export_height.value()
        self._export_options_changed()

    def _use_current_image_size(self):
        width, height = self._current_image_size()
        self._export_size_updating = True
        try:
            self.export_width.setValue(width)
            self.export_height.setValue(height)
        finally:
            self._export_size_updating = False
        self._export_aspect = self.export_width.value() / self.export_height.value()
        self._export_options_changed()

    def _export_ratio_changed(self, _checked):
        self._export_aspect = self.export_width.value() / self.export_height.value()
        self._export_options_changed()

    def _export_dimension_changed(self, axis, value):
        if self._export_size_updating:
            return
        self._export_size_updating = True
        try:
            if self.export_lock_ratio.isChecked():
                if axis == "width":
                    field = self.export_height
                    expected = round(value / self._export_aspect)
                    field.setValue(expected)
                    if expected != field.value():
                        self.export_width.setValue(round(field.value() * self._export_aspect))
                else:
                    field = self.export_width
                    expected = round(value * self._export_aspect)
                    field.setValue(expected)
                    if expected != field.value():
                        self.export_height.setValue(round(field.value() / self._export_aspect))
            else:
                self._export_aspect = self.export_width.value() / self.export_height.value()
        finally:
            self._export_size_updating = False
        self._export_options_changed()

    def _export_options_changed(self, *_args):
        if self._disposed:
            return
        from .image_export import MAX_PIXELS
        settings = self._export_settings
        settings.format = self.export_format.currentText()
        settings.width = self.export_width.value()
        settings.height = self.export_height.value()
        settings.dpi = self.export_dpi.value()
        settings.lock_ratio = self.export_lock_ratio.isChecked()
        settings.transparent = self.export_transparent.isChecked()
        self.export_transparent.setEnabled(settings.format != "JPEG")
        self.export_button.setText(f"Export {settings.format}…")
        pixels = settings.width * settings.height
        valid = pixels <= MAX_PIXELS
        self.export_button.setEnabled(valid and not getattr(self, "_export_busy", False))
        if valid:
            hint = (f"{settings.width / settings.dpi * 2.54:.1f} × "
                    f"{settings.height / settings.dpi * 2.54:.1f} cm at {settings.dpi} DPI")
            self.export_button.setToolTip("Choose a destination and export the current 3D view as an image.")
        else:
            hint = (f"{pixels / 1e6:.1f} MP · maximum {MAX_PIXELS / 1e6:g} MP; "
                    "Current ▾ → Fit to limit, or reduce W/H")
            self.export_button.setToolTip(hint)
        self.export_size_hint.setText(hint)

    def _export_image(self):
        from pathlib import Path
        from Qt.QtWidgets import QFileDialog
        from .image_export import export_image
        if self._disposed or not self.export_button.isEnabled():
            return
        format_name = self.export_format.currentText()
        # A file dialog has a nested event loop. Keep the dimensions and format
        # the user requested even if a pending update changes the form meanwhile.
        options = dict(format=format_name, width=self.export_width.value(),
            height=self.export_height.value(), dpi=self.export_dpi.value(),
            transparent=self.export_transparent.isEnabled() and self.export_transparent.isChecked())
        extension = {"PNG": ".png", "JPEG": ".jpg", "TIFF": ".tif"}[format_name]
        selected = self.list_widget.currentItem()
        name = self.name_input.text().strip() or (selected.text() if selected is not None else "ChimeraX-view")
        name = "".join("-" if char in '/\\:' or ord(char) < 32 else char for char in name)
        directory = Path(self._export_settings.directory).expanduser() if self._export_settings.directory else Path.home() / "Desktop"
        if not directory.is_dir():
            directory = Path.home()
        self._export_busy = True
        self._export_options_changed()
        try:
            path, _filter = QFileDialog.getSaveFileName(
                self.tool_window.ui_area, "Export image", str(directory / (name + extension)),
                f"{format_name} image (*{extension})")
            if not path:
                return
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                result = export_image(self.session, path, **options)
                self._export_settings.directory = str(Path(result["path"]).parent)
                self._set_status(f"Exported {Path(result['path']).name} · "
                                 f"{result['width']} × {result['height']} px · {result['dpi']} DPI")
            finally:
                QApplication.restoreOverrideCursor()
        except Exception as exc:
            self._set_status(f"Export failed: {exc}", err=True)
        finally:
            self._export_busy = False
            self._export_options_changed()

    def _save_options(self):
        return {key: button.isChecked() for key, button in self.include_buttons.items()}

    def _sync_save_preset(self):
        options = self._save_options()
        for key, label, _tip in _SAVE_OPTIONS:
            self.include_buttons[key].setText(label)
        if options == {key: key != "selection" for key in options}:
            preset = "appearance"
        elif options == {key: key == "camera" for key in options}:
            preset = "camera"
        else:
            preset = "custom"
        blocked = self.preset_combo.blockSignals(True)
        self.preset_combo.setCurrentIndex(self.preset_combo.findData(preset))
        self.preset_combo.blockSignals(blocked)

    def _save_options_changed(self, _checked=None):
        for key, value in self._save_options().items():
            setattr(self._settings, key, value)
        self._sync_save_preset()
        if hasattr(self, "save_btn"):
            self.save_btn.setEnabled(any(self._save_options().values()))

    def _apply_save_preset(self, _index):
        preset = self.preset_combo.currentData()
        if preset == "custom":
            return
        for key, button in self.include_buttons.items():
            blocked = button.blockSignals(True)
            button.setChecked(key != "selection" if preset == "appearance" else key == "camera")
            button.blockSignals(blocked)
        self._save_options_changed()

    # ---------------------------------------------------------------- helpers

    def _run(self, command, status_ok=None, status_err_prefix="Error"):
        from chimerax.core.commands import run as cx_run
        try:
            cx_run(self.session, command)
            if status_ok is not None:
                self._set_status(status_ok)
            return True
        except Exception as exc:
            self._set_status(f"{status_err_prefix}: {exc}", err=True)
            return False

    def _set_status(self, msg, err=False):
        if self._disposed:
            return
        self.status.setText(msg)
        self.status.setStyleSheet(
            "color: #df7979; font-size: 12px;" if err
            else "color: palette(window-text); font-size: 12px;"
        )

    def _next_auto_name(self):
        existing = set(_list_named_views(self.session))
        i = 1
        while f"View {i}" in existing:
            i += 1
        return f"View {i}"

    # ---------------------------------------------------------------- rotation

    def _apply_pivot(self):
        """Apply the chosen center-of-rotation before issuing a `turn`."""
        from chimerax.core.commands import run as cx_run
        choice = self.cofr_combo.currentText()
        if choice.startswith("Selection"):
            try:
                cx_run(self.session, "cofr sel")
            except Exception as exc:
                self._set_status(f"cofr sel failed (no selection?): {exc}", err=True)
                return False
        elif choice.startswith("All models"):
            try:
                cx_run(self.session, "cofr center")
            except Exception:
                pass
        # "Current (no change)" -> leave cofr alone
        return True

    def _step_degrees(self):
        text = self.step_combo.currentText().rstrip("°").strip()
        try:
            return float(text)
        except Exception:
            return 90.0

    def _rotate(self, axis, sign):
        if not self._apply_pivot():
            return
        deg = self._step_degrees() * sign
        self._run(f"turn {axis} {deg}",
                  status_ok=f"Rotated {axis.upper()} {deg:+g}°",
                  status_err_prefix="Turn failed")

    def _reset_orient(self):
        self._run("view orient",
                  status_ok="Orientation reset (view orient).",
                  status_err_prefix="Reset failed")

    def _center_on_selection(self):
        from chimerax.core.commands import run as cx_run
        try:
            cx_run(self.session, "cofr sel")
            cx_run(self.session, "view sel")
            self._set_status("Centered on selection.")
        except Exception as exc:
            self._set_status(f"Center on selection failed: {exc}", err=True)

    # ---------------------------------------------------------------- legend

    def _add_legend(self, index=0):
        choice = self._legend_choices[index]
        # Common positioning: bottom-left, ~40% wide, slim band.
        common_opts = (
            " pos 0.05,0.06 size 0.35,0.04 fontSize 14 "
            "labelOffset 4 numericLabelSpacing equal "
            "colorTreatment blended ticks true tickThickness 1.5 "
            "labelColor black"
        )
        if choice.startswith("Hydrophobicity (mlp"):
            # Exact ChimeraX `lipophilicity` palette colours.
            cmd = ('key darkcyan:"Hydrophilic" white:0 darkgoldenrod:"Lipophilic"'
                   + common_opts + ' title "Hydrophobicity (MLP)"')
        elif choice.startswith("Hydrophobicity (Kyte"):
            cmd = ('key blue:"-4.5" white:0 red:"+4.5"'
                   + common_opts + ' title "Hydrophobicity (Kyte-Doolittle)"')
        elif choice.startswith("Conservation"):
            cmd = ('key red:"Conserved" orange:"" yellow:"" white:"Variable"'
                   + common_opts + ' title "Conservation"')
        else:
            # Custom: fall back to a generic 3-stop placeholder users can edit.
            cmd = ('key blue:"Low" white:"" red:"High"'
                   + common_opts + ' title "Custom"')
        self._run(cmd, status_ok=f"Added legend: {choice}",
                  status_err_prefix="Add legend failed")

    def _clear_legend(self):
        self._run("~key", status_ok="Legend removed.",
                  status_err_prefix="Clear failed")

    # ---------------------------------------------------------------- actions

    def _save_view(self):
        name = self.name_input.text().strip()
        if not name:
            name = self._next_auto_name()
        from .camera_bookmark_state import save_bookmark
        try:
            save_bookmark(self.session, name, self._save_options())
            self.name_input.clear()
            self._refresh_list()
            # Highlight the just-saved item.
            for i in range(self.list_widget.count()):
                item = self.list_widget.item(i)
                if item.text() == name:
                    self.list_widget.setCurrentItem(item)
                    break
            self._set_status(f"Saved: {name} · {self._conditions_text(self._save_options())}")
        except Exception as exc:
            self._set_status(f"Save failed: {exc}", err=True)

    def _on_item_clicked(self, item):
        name = item.text()
        from .camera_bookmark_state import restore_bookmark
        try:
            result = restore_bookmark(self.session, name)
            skipped = result.get("skipped_models", 0)
            note = f" · {skipped} unavailable models skipped" if skipped else ""
            self._set_status(f"Restored: {name}{note}")
        except Exception as exc:
            self._set_status(f"Restore failed: {exc}", err=True)

    def _delete_selected(self):
        item = self.list_widget.currentItem()
        if item is None:
            return
        name = item.text()
        from .camera_bookmark_state import delete_bookmark
        try:
            delete_bookmark(self.session, name)
            self._refresh_list()
            self._set_status(f"Deleted: {name}")
        except Exception as exc:
            self._set_status(f"Delete failed: {exc}", err=True)

    def _delete_all(self):
        names = _list_named_views(self.session)
        if not names:
            return
        from Qt.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self.tool_window.ui_area, "Clear all bookmarks",
            f"Delete all {len(names)} saved views?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        ok = 0
        from .camera_bookmark_state import delete_bookmark
        for name in names:
            try:
                delete_bookmark(self.session, name)
                ok += 1
            except Exception as exc:
                self._set_status(f"Delete failed: {exc}", err=True)
        self._set_status(f"Cleared {ok}/{len(names)} bookmarks.")
        self._refresh_list()

    def _rename_selected(self):
        from Qt.QtWidgets import QInputDialog
        item = self.list_widget.currentItem()
        if item is None:
            self._set_status("Pick a saved view first.", err=True)
            return
        old_name = item.text()
        new_name, ok = QInputDialog.getText(
            self.tool_window.ui_area, "Rename view", "New name:", text=old_name
        )
        if not ok:
            return
        new_name = new_name.strip()
        if not new_name or new_name == old_name:
            return
        from .camera_bookmark_state import rename_bookmark
        try:
            rename_bookmark(self.session, old_name, new_name)
            self._set_status(f"Renamed: {old_name} -> {new_name}")
            self._refresh_list()
        except Exception as exc:
            self._set_status(f"Rename failed: {exc}", err=True)

    def _refresh_list(self):
        from .camera_bookmark_state import bookmark_options
        names = sorted(_list_named_views(self.session))
        selected = self.list_widget.currentItem()
        selected_name = selected.text() if selected is not None else None
        self.list_widget.clear()
        for n in names:
            self.list_widget.addItem(n)
            item = self.list_widget.item(self.list_widget.count() - 1)
            item.setToolTip(f"{n}\nRestores: {self._conditions_text(bookmark_options(self.session, n))}")
            if n == selected_name:
                self.list_widget.setCurrentItem(item)
        self.list_label.setText(f"Saved bookmarks ({len(names)}) · click to restore")
        if not names:
            self._set_status("Choose conditions above, then save a bookmark.")

    @staticmethod
    def _conditions_text(options):
        return ", ".join(label for key, label, _tip in _SAVE_OPTIONS if options.get(key))
