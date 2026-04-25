from Qt.QtCore import Qt, QTimer
from Qt.QtGui import QColor, QLinearGradient, QPainter, QPen
from Qt.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from chimerax.core.tools import ToolInstance, get_singleton


def _run(session, command):
    from chimerax.core.commands import run

    run(session, command)


def _has_selection(session):
    try:
        from chimerax.atomic import selected_atoms, selected_residues

        return bool(session.selection.models() or len(selected_atoms(session)) or len(selected_residues(session)))
    except Exception:
        return False


class _RainbowRamp(QWidget):

    def __init__(self, color_callback, parent=None):
        super().__init__(parent)
        self._color_callback = color_callback
        self._hue = 0.12
        self._saturation = 0.78
        self.setMinimumHeight(34)
        self.setMaximumHeight(44)
        self.setMouseTracking(True)
        self.setToolTip("Click or drag across the rainbow ramp. Release to apply the color.")

    def _ramp_rect(self):
        return self.rect().adjusted(8, 9, -8, -9)

    def _event_x(self, event):
        if hasattr(event, "position"):
            try:
                return float(event.position().x())
            except Exception:
                pass
        try:
            return float(event.x())
        except Exception:
            return 0.0

    def _set_from_event(self, event, final=False):
        rect = self._ramp_rect()
        if rect.width() <= 0:
            return
        x = max(float(rect.left()), min(float(rect.right()), self._event_x(event)))
        self._hue = (x - float(rect.left())) / max(1.0, float(rect.width()))
        self.update()
        callback = self._color_callback
        if callback is not None:
            callback(self.color_code(), final)

    def set_saturation(self, saturation, final=False):
        self._saturation = max(0.0, min(1.0, float(saturation)))
        self.update()
        callback = self._color_callback
        if callback is not None:
            callback(self.color_code(), final)

    def color_code(self):
        return QColor.fromHsvF(float(self._hue), float(self._saturation), 0.95).name()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._set_from_event(event, final=False)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._set_from_event(event, final=False)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._set_from_event(event, final=True)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self._ramp_rect()
        if rect.width() <= 0 or rect.height() <= 0:
            return

        gradient = QLinearGradient(float(rect.left()), 0.0, float(rect.right()), 0.0)
        for index in range(13):
            hue = index / 12.0
            gradient.setColorAt(hue, QColor.fromHsvF(hue, float(self._saturation), 0.95))
        painter.setPen(QPen(QColor("#3a4046"), 1))
        painter.setBrush(gradient)
        painter.drawRoundedRect(rect, 7, 7)

        x = float(rect.left()) + float(rect.width()) * float(self._hue)
        painter.setPen(QPen(QColor("#f0f3f6"), 2))
        painter.drawLine(int(x), rect.top() - 4, int(x), rect.bottom() + 4)
        painter.setPen(QPen(QColor("#0d0f11"), 1))
        painter.setBrush(QColor(self.color_code()))
        painter.drawEllipse(int(x) - 5, int(rect.center().y()) - 5, 10, 10)


class _SliderBlock(QWidget):

    def __init__(self, title, minimum, maximum, value, formatter, changed_callback, parent=None):
        super().__init__(parent)
        self._formatter = formatter
        self.setObjectName("MetricSlider")
        layout = QVBoxLayout()
        layout.setContentsMargins(6, 7, 6, 7)
        layout.setSpacing(6)
        self.setLayout(layout)

        self.title_label = QLabel(title, self)
        self.title_label.setObjectName("MetricSliderTitle")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label.setWordWrap(True)
        layout.addWidget(self.title_label)

        self.slider = QSlider(Qt.Orientation.Vertical, self)
        self.slider.setRange(minimum, maximum)
        self.slider.setValue(value)
        self.slider.setTickPosition(QSlider.TickPosition.TicksRight)
        self.slider.setTickInterval(max(1, int((maximum - minimum) / 5)))
        self.slider.valueChanged.connect(changed_callback)
        self.slider.setMinimumHeight(154)
        layout.addWidget(self.slider, 1, Qt.AlignmentFlag.AlignHCenter)

        self.value_label = QLabel(self._formatter(value), self)
        self.value_label.setObjectName("MetricSliderValue")
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.value_label)

    def value(self):
        return self.slider.value()

    def set_value(self, value):
        self.slider.setValue(value)
        self.value_label.setText(self._formatter(value))

    def refresh_label(self):
        self.value_label.setText(self._formatter(self.slider.value()))


class DisplayControlsWidget(QWidget):

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self._pending = set()
        self._apply_timer = QTimer(self)
        self._apply_timer.setSingleShot(True)
        self._apply_timer.timeout.connect(self._apply_pending)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        self.setLayout(layout)
        self.setObjectName("DisplayControlsRoot")
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.setStyleSheet(
            "QWidget#DisplayControlsRoot { background: #171a1d; color: #e5e8ec; }"
            "QLabel { color: #d9dde2; background: transparent; }"
            "QLabel#PanelHeader { font-size: 14px; font-weight: 700; color: #f0f3f6; }"
            "QLabel#SectionTitle { font-size: 12px; font-weight: 700; color: #f0f3f6; }"
            "QLabel#MutedCaption { color: #aeb6bf; font-size: 11px; letter-spacing: 0.02em; }"
            "QWidget#InlineSliderRow {"
            " background: #20252a;"
            " border: 1px solid #37404a;"
            " border-radius: 10px;"
            "}"
            "QLabel#InlineSliderTitle {"
            " color: #e5e8ec;"
            " font-weight: 700;"
            " padding-left: 8px;"
            "}"
            "QLabel#InlineSliderValue {"
            " background: #111519;"
            " color: #f0f3f6;"
            " border: 1px solid #313942;"
            " border-radius: 8px;"
            " padding: 4px 8px;"
            " font-weight: 700;"
            "}"
            "QWidget#MetricSlider {"
            " background: #20252a;"
            " border: 1px solid #37404a;"
            " border-radius: 10px;"
            "}"
            "QLabel#MetricSliderTitle {"
            " color: #e6e9ed;"
            " font-weight: 700;"
            " line-height: 1.1;"
            "}"
            "QLabel#MetricSliderValue {"
            " background: #111519;"
            " color: #f0f3f6;"
            " border: 1px solid #313942;"
            " border-radius: 7px;"
            " padding: 3px 6px;"
            " font-weight: 700;"
            "}"
            "QPushButton {"
            " background: #24282d;"
            " color: #eef1f4;"
            " border: 1px solid #3a4046;"
            " border-radius: 7px;"
            " padding: 5px 8px;"
            "}"
            "QPushButton:hover { background: #2e343a; border-color: #656d76; }"
            "QComboBox, QLineEdit {"
            " background: #101214;"
            " color: #eef1f4;"
            " border: 1px solid #3a4046;"
            " border-radius: 7px;"
            " padding: 5px 8px;"
            "}"
            "QComboBox:hover, QLineEdit:hover { border-color: #656d76; }"
            "QSlider::groove:vertical {"
            " background: #0f1317;"
            " border: 1px solid #3d4650;"
            " width: 10px;"
            " border-radius: 5px;"
            "}"
            "QSlider::handle:vertical {"
            " background: #9aa4af;"
            " border: 1px solid #dbe1e7;"
            " height: 18px;"
            " margin: 0 -7px;"
            " border-radius: 9px;"
            "}"
            "QSlider::handle:vertical:hover {"
            " background: #c0c8d1;"
            " border-color: #f0f3f6;"
            "}"
            "QSlider::groove:horizontal {"
            " background: #0f1317;"
            " border: 1px solid #3d4650;"
            " height: 10px;"
            " border-radius: 5px;"
            "}"
            "QSlider::sub-page:horizontal {"
            " background: #8f9aa5;"
            " border-radius: 5px;"
            "}"
            "QSlider::handle:horizontal {"
            " background: #dbe1e7;"
            " border: 1px solid #101214;"
            " width: 18px;"
            " margin: -5px 0;"
            " border-radius: 9px;"
            "}"
            "QSlider::handle:horizontal:hover {"
            " background: #f0f3f6;"
            " border-color: #5f6973;"
            "}"
        )

        header = QLabel("Molecule Display Controls", self)
        header.setObjectName("PanelHeader")
        layout.addWidget(header)

        self.status_label = QLabel("", self)
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet(
            "QLabel { background: #101214; border: 1px solid #343a40; border-radius: 6px; padding: 6px 8px; }"
        )
        layout.addWidget(self.status_label)

        self._build_color_controls(layout)

        slider_grid = QGridLayout()
        slider_grid.setHorizontalSpacing(8)
        slider_grid.setVerticalSpacing(8)
        layout.addLayout(slider_grid, 1)

        self.selection_transparency = self._make_slider("Sel\ntrans", 0, 100, 0, lambda v: f"{v}%", "selection")
        self.protein_width = self._make_slider("All\nwidth", 5, 60, 20, self._angstrom_label, "protein")
        self.protein_thickness = self._make_slider("All\nthick", 1, 30, 4, self._angstrom_label, "protein")
        self.helix_width = self._make_slider("Helix\nwidth", 5, 60, 20, self._angstrom_label, "helix")
        self.helix_thickness = self._make_slider("Helix\nthick", 1, 30, 4, self._angstrom_label, "helix")
        self.strand_width = self._make_slider("Sheet\nwidth", 5, 60, 20, self._angstrom_label, "strand")
        self.strand_thickness = self._make_slider("Sheet\nthick", 1, 30, 4, self._angstrom_label, "strand")

        sliders = (
            self.selection_transparency,
            self.protein_width,
            self.protein_thickness,
            self.helix_width,
            self.helix_thickness,
            self.strand_width,
            self.strand_thickness,
        )
        for index, widget in enumerate(sliders):
            slider_grid.addWidget(widget, 0, index)
            slider_grid.setColumnStretch(index, 1)

        button_row = QHBoxLayout()
        button_row.setSpacing(6)
        layout.addLayout(button_row)

        self.refresh_button = QPushButton("Refresh selection", self)
        self.refresh_button.clicked.connect(self.refresh)
        button_row.addWidget(self.refresh_button)

        self.reset_cartoon_button = QPushButton("Reset cartoon", self)
        self.reset_cartoon_button.clicked.connect(self._reset_cartoon)
        button_row.addWidget(self.reset_cartoon_button)

        self.clear_transparency_button = QPushButton("Opaque selected", self)
        self.clear_transparency_button.clicked.connect(lambda: self._set_selection_transparency(0))
        button_row.addWidget(self.clear_transparency_button)

        self.refresh()

    def _build_color_controls(self, layout):
        title = QLabel("Color Palette", self)
        title.setObjectName("SectionTitle")
        layout.addWidget(title)

        control_row = QHBoxLayout()
        control_row.setSpacing(6)
        layout.addLayout(control_row)

        self.color_scope_combo = QComboBox(self)
        self.color_scope_combo.addItem("Selection", "sel")
        self.color_scope_combo.addItem("All", "all")
        self.color_scope_combo.setToolTip("Color selected atoms/residues/models or all open models.")
        control_row.addWidget(self.color_scope_combo, 1)

        self.color_target_combo = QComboBox(self)
        self.color_target_combo.addItem("All reps", "abcsp")
        self.color_target_combo.addItem("Cartoon", "c")
        self.color_target_combo.addItem("Atoms+bonds", "ab")
        self.color_target_combo.addItem("Surface", "s")
        self.color_target_combo.addItem("Models", "m")
        self.color_target_combo.setToolTip("ChimeraX color target letters: a atoms, b bonds, c cartoon, s surface, p pseudobonds, m models.")
        control_row.addWidget(self.color_target_combo, 1)

        self.color_hex_edit = QLineEdit("#d9d3c7", self)
        self.color_hex_edit.setMaxLength(9)
        self.color_hex_edit.setPlaceholderText("#RRGGBB")
        self.color_hex_edit.returnPressed.connect(self._apply_color_from_text)
        control_row.addWidget(self.color_hex_edit, 1)

        self.pick_color_button = QPushButton("Pick", self)
        self.pick_color_button.clicked.connect(self._pick_color)
        control_row.addWidget(self.pick_color_button)

        self.apply_color_button = QPushButton("Apply", self)
        self.apply_color_button.clicked.connect(self._apply_color_from_text)
        control_row.addWidget(self.apply_color_button)

        ramp_label = QLabel("Rainbow ramp - drag to choose, release to apply", self)
        ramp_label.setObjectName("MutedCaption")
        layout.addWidget(ramp_label)

        self.rainbow_ramp = _RainbowRamp(self._rainbow_color_changed, self)
        layout.addWidget(self.rainbow_ramp)

        self.saturation_slider, self.saturation_value_label = self._make_horizontal_slider(
            "Saturation",
            0,
            100,
            78,
            lambda value: f"{value}%",
            self._saturation_changed,
            layout,
        )
        self.saturation_slider.sliderReleased.connect(self._apply_current_color)

        self.color_transparency_slider, self.color_transparency_value_label = self._make_horizontal_slider(
            "Color trans",
            0,
            100,
            0,
            lambda value: f"{value}%",
            self._color_transparency_changed,
            layout,
        )
        self.color_transparency_slider.sliderReleased.connect(self._apply_current_color)

        scheme_row = QHBoxLayout()
        scheme_row.setSpacing(6)
        layout.addLayout(scheme_row)

        self.by_chain_button = QPushButton("By chain", self)
        self.by_chain_button.clicked.connect(lambda: self._apply_color_scheme("bychain"))
        scheme_row.addWidget(self.by_chain_button)

        self.by_element_button = QPushButton("By element", self)
        self.by_element_button.clicked.connect(lambda: self._apply_color_scheme("byelement"))
        scheme_row.addWidget(self.by_element_button)

        self.by_model_button = QPushButton("By model", self)
        self.by_model_button.clicked.connect(lambda: self._apply_color_scheme("bymodel"))
        scheme_row.addWidget(self.by_model_button)

    def _make_horizontal_slider(self, title, minimum, maximum, value, formatter, changed_callback, layout):
        row_widget = QWidget(self)
        row_widget.setObjectName("InlineSliderRow")
        row = QHBoxLayout()
        row.setContentsMargins(10, 7, 10, 7)
        row.setSpacing(10)
        row_widget.setLayout(row)
        layout.addWidget(row_widget)

        label = QLabel(title, row_widget)
        label.setObjectName("InlineSliderTitle")
        label.setMinimumWidth(92)
        row.addWidget(label, 0)

        slider = QSlider(Qt.Orientation.Horizontal, row_widget)
        slider.setRange(minimum, maximum)
        slider.setValue(value)
        slider.setTickPosition(QSlider.TickPosition.NoTicks)
        slider.valueChanged.connect(changed_callback)
        row.addWidget(slider, 1)

        value_label = QLabel(formatter(value), row_widget)
        value_label.setObjectName("InlineSliderValue")
        value_label.setMinimumWidth(54)
        value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(value_label, 0)
        return slider, value_label

    def _make_slider(self, title, minimum, maximum, value, formatter, group):
        return _SliderBlock(
            title,
            minimum,
            maximum,
            value,
            formatter,
            lambda _value, g=group: self._schedule_apply(g),
            self,
        )

    def _angstrom_label(self, value):
        return f"{value / 10:.1f} A"

    def _slider_float(self, slider):
        return slider.value() / 10.0

    def _normalized_color_code(self):
        text = str(self.color_hex_edit.text() or "").strip()
        if not text:
            return None
        if not text.startswith("#"):
            text = "#" + text
        hex_part = text[1:]
        if len(hex_part) not in (6, 8):
            return None
        try:
            int(hex_part, 16)
        except ValueError:
            return None
        return "#" + hex_part.lower()

    def _color_scope(self):
        scope = self.color_scope_combo.currentData()
        if scope == "sel" and not _has_selection(self.session):
            self.status_label.setText("No selection. Select residues/atoms/models first or switch Scope to All.")
            return None
        return scope or "sel"

    def _apply_color_from_text(self):
        color = self._normalized_color_code()
        if color is None:
            self.status_label.setText("Invalid color code. Use #RRGGBB or #RRGGBBAA.")
            return
        self.color_hex_edit.setText(color)
        self._apply_color(color)

    def _rainbow_color_changed(self, color, final):
        self.color_hex_edit.setText(color)
        if final:
            self._apply_color(color)

    def _saturation_changed(self, value):
        self.saturation_value_label.setText(f"{int(value)}%")
        self.rainbow_ramp.set_saturation(float(value) / 100.0, final=False)

    def _color_transparency_changed(self, value):
        self.color_transparency_value_label.setText(f"{int(value)}%")

    def _apply_current_color(self):
        color = self._normalized_color_code()
        if color is not None:
            self._apply_color(color)

    def _pick_color(self):
        from Qt.QtGui import QColor
        from Qt.QtWidgets import QColorDialog

        current = self._normalized_color_code() or "#d9d3c7"
        color = QColorDialog.getColor(QColor(current), self, "Choose ChimeraX color")
        if not color.isValid():
            return
        self.color_hex_edit.setText(color.name())
        self._apply_color(color.name())

    def _apply_color(self, color):
        scope = self._color_scope()
        if scope is None:
            return
        target = self.color_target_combo.currentData() or "abcsp"
        transparency = int(self.color_transparency_slider.value())
        command = f"color {scope} {color} target {target} transparency {transparency}"
        try:
            _run(self.session, command)
        except Exception as err:
            self.status_label.setText(str(err) if str(err) else err.__class__.__name__)
            return
        self.status_label.setText(f"Applied: {command}")

    def _apply_color_scheme(self, scheme):
        scope = self._color_scope()
        if scope is None:
            return
        target = self.color_target_combo.currentData() or "abcsp"
        transparency = int(self.color_transparency_slider.value())
        command = f"color {scope} {scheme} target {target} transparency {transparency}"
        try:
            _run(self.session, command)
        except Exception as err:
            self.status_label.setText(str(err) if str(err) else err.__class__.__name__)
            return
        self.status_label.setText(f"Applied: {command}")

    def _schedule_apply(self, group):
        self._pending.add(group)
        for slider in (
            self.selection_transparency,
            self.protein_width,
            self.protein_thickness,
            self.helix_width,
            self.helix_thickness,
            self.strand_width,
            self.strand_thickness,
        ):
            slider.refresh_label()
        self._apply_timer.start(80)

    def _apply_pending(self):
        pending = set(self._pending)
        self._pending.clear()
        try:
            if "selection" in pending:
                self._apply_selection_transparency()
            if "protein" in pending:
                self._apply_cartoon_style("protein", self.protein_width, self.protein_thickness)
            if "helix" in pending:
                self._apply_cartoon_style("helix", self.helix_width, self.helix_thickness)
            if "strand" in pending:
                self._apply_cartoon_style("strand", self.strand_width, self.strand_thickness)
        finally:
            self.refresh()

    def _apply_selection_transparency(self):
        percent = int(self.selection_transparency.value())
        if not _has_selection(self.session):
            self.status_label.setText("No selected atoms/residues/models. Select something first, then move Sel trans.")
            return
        command = f"transparency sel {percent} target abcsp"
        _run(self.session, command)
        self.status_label.setText(f"Applied: {command}")

    def _apply_cartoon_style(self, target, width_slider, thickness_slider):
        width = self._slider_float(width_slider)
        thickness = self._slider_float(thickness_slider)
        command = f"cartoon style {target} width {width:.1f} thickness {thickness:.1f}"
        _run(self.session, command)
        self.status_label.setText(f"Applied: {command}")

    def _set_selection_transparency(self, percent):
        self.selection_transparency.set_value(int(percent))
        self._schedule_apply("selection")

    def _reset_cartoon(self):
        for slider, value in (
            (self.protein_width, 20),
            (self.protein_thickness, 4),
            (self.helix_width, 20),
            (self.helix_thickness, 4),
            (self.strand_width, 20),
            (self.strand_thickness, 4),
        ):
            slider.set_value(value)
        for command in (
            "cartoon style protein width 2.0 thickness 0.4",
            "cartoon style helix width 2.0 thickness 0.4",
            "cartoon style strand width 2.0 thickness 0.4",
        ):
            _run(self.session, command)
        self.status_label.setText("Cartoon width/thickness reset to ChimeraX defaults.")

    def refresh(self):
        if _has_selection(self.session):
            self.status_label.setText("Selection ready. Sel trans applies to atoms, cartoons, surfaces, bonds, and pseudobonds.")
        else:
            self.status_label.setText("No selection. Select residues/atoms/models to use Sel trans.")


class CodexDisplayControls(ToolInstance):

    SESSION_ENDURING = False
    SESSION_SAVE = False
    UI_LAYOUT_VERSION = 5

    @classmethod
    def get_singleton(cls, session, create=True, display=True, **kw):
        instance = get_singleton(session, cls, "Display Controls", create=create, display=display, **kw)
        if instance is not None and getattr(instance, "_ui_layout_version", None) != cls.UI_LAYOUT_VERSION:
            try:
                instance.delete()
            except Exception:
                pass
            instance = get_singleton(session, cls, "Display Controls", create=create, display=display, **kw)
        return instance

    def __init__(self, session, tool_name):
        super().__init__(session, tool_name)
        self._ui_layout_version = self.UI_LAYOUT_VERSION

        from chimerax.ui import MainToolWindow

        self.tool_window = MainToolWindow(self, close_destroys=True)
        self._build_ui()

    def _build_ui(self):
        parent = self.tool_window.ui_area
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        parent.setLayout(layout)
        self.widget = DisplayControlsWidget(self.session, parent=parent)
        layout.addWidget(self.widget)
        self.tool_window.manage(placement="side")
