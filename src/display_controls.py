from Qt.QtCore import Qt, QTimer
from Qt.QtGui import QColor, QLinearGradient, QPainter, QPen
from Qt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
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
        self.setMinimumHeight(18)
        self.setMaximumHeight(22)
        self.setMouseTracking(True)
        self.setToolTip("Click or drag across the rainbow ramp. Release to apply the color.")

    def _ramp_rect(self):
        return self.rect().adjusted(5, 3, -5, -3)

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
        for index in range(25):
            hue = index / 24.0
            gradient.setColorAt(hue, QColor.fromHsvF(hue, float(self._saturation), 0.95))
        painter.setPen(QPen(QColor("#2a2f35"), 1))
        painter.setBrush(gradient)
        painter.drawRoundedRect(rect, 7, 7)

        x = float(rect.left()) + float(rect.width()) * float(self._hue)
        painter.setPen(QPen(QColor("#0a0d10"), 1))
        painter.setBrush(QColor(self.color_code()))
        painter.drawEllipse(int(x) - 5, int(rect.center().y()) - 5, 10, 10)
        painter.setPen(QPen(QColor("#f0f3f6"), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(int(x) - 5, int(rect.center().y()) - 5, 10, 10)


class _SliderBlock(QWidget):

    def __init__(
        self,
        title,
        minimum,
        maximum,
        value,
        formatter,
        changed_callback,
        parent=None,
        editable=False,
        suffix="%",
        orientation=Qt.Orientation.Vertical,
        title_min_width=0,
        value_min_width=0,
        decimals=0,
        scale=1.0,
    ):
        super().__init__(parent)
        self._formatter = formatter
        self._editable = bool(editable)
        self._orientation = orientation
        self._decimals = max(0, int(decimals))
        self._scale = float(scale) if scale else 1.0
        is_horizontal = orientation == Qt.Orientation.Horizontal

        if is_horizontal:
            outer = QHBoxLayout()
            outer.setContentsMargins(0, 0, 0, 0)
            outer.setSpacing(4)
        else:
            outer = QVBoxLayout()
            outer.setContentsMargins(1, 1, 1, 1)
            outer.setSpacing(3)
        self.setLayout(outer)

        self.title_label = QLabel(title, self)
        if is_horizontal:
            self.title_label.setObjectName("InlineSliderTitle")
            self.title_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        else:
            self.title_label.setObjectName("MetricSliderTitle")
            self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.title_label.setWordWrap(True)
        if title_min_width > 0:
            self.title_label.setMinimumWidth(title_min_width)
            self.title_label.setMaximumWidth(max(title_min_width + 18, 34))
        outer.addWidget(self.title_label, 0)

        self.slider = QSlider(orientation, self)
        self.slider.setRange(minimum, maximum)
        self.slider.setValue(value)
        if not is_horizontal:
            self.slider.setTickPosition(QSlider.TickPosition.TicksRight)
            self.slider.setTickInterval(max(1, int((maximum - minimum) / 5)))
        else:
            self.slider.setTickPosition(QSlider.TickPosition.NoTicks)
        self.slider.valueChanged.connect(changed_callback)
        if is_horizontal:
            outer.addWidget(self.slider, 1)
        else:
            outer.addWidget(self.slider, 1, Qt.AlignmentFlag.AlignHCenter)

        if self._editable:
            use_double = self._decimals > 0 or self._scale != 1.0
            if use_double:
                spin = QDoubleSpinBox(self)
                spin.setRange(minimum * self._scale, maximum * self._scale)
                spin.setDecimals(self._decimals)
                step = self._scale if self._scale != 1.0 else (10 ** (-self._decimals))
                spin.setSingleStep(step)
                spin.setValue(value * self._scale)
                spin.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
            else:
                spin = QSpinBox(self)
                spin.setRange(minimum, maximum)
                spin.setValue(value)
                spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
            spin.setObjectName("InlineSliderInput" if is_horizontal else "MetricSliderInput")
            if suffix:
                spin.setSuffix(suffix)
            spin.setKeyboardTracking(False)
            if is_horizontal:
                spin.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            else:
                spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
            if value_min_width > 0:
                spin.setMinimumWidth(value_min_width)
                spin.setMaximumWidth(value_min_width + 10)
            if use_double:
                scale = self._scale
                decimals = self._decimals
                spin.valueChanged.connect(
                    lambda v, s=scale: self.slider.setValue(int(round(v / s)))
                )
                self.slider.valueChanged.connect(
                    lambda v, s=scale, d=decimals: spin.setValue(round(v * s, d))
                )
            else:
                spin.valueChanged.connect(self.slider.setValue)
                self.slider.valueChanged.connect(spin.setValue)
            outer.addWidget(spin, 0)
            self.value_label = spin
        else:
            self.value_label = QLabel(self._formatter(value), self)
            self.value_label.setObjectName("InlineSliderValue" if is_horizontal else "MetricSliderValue")
            if is_horizontal:
                self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            else:
                self.value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            if value_min_width > 0:
                self.value_label.setMinimumWidth(value_min_width)
                self.value_label.setMaximumWidth(value_min_width + 10)
            outer.addWidget(self.value_label, 0)

    def value(self):
        return self.slider.value()

    def set_value(self, value):
        self.slider.setValue(value)
        if not self._editable:
            self.value_label.setText(self._formatter(value))

    def refresh_label(self):
        if not self._editable:
            self.value_label.setText(self._formatter(self.slider.value()))


class DisplayControlsWidget(QWidget):

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self._pending = set()
        self._apply_timer = QTimer(self)
        self._apply_timer.setSingleShot(True)
        self._apply_timer.timeout.connect(self._apply_pending)
        self._refresh_pending = False
        self._handlers = []
        self._build_ui()
        self._install_handlers()

    def _install_handlers(self):
        if self._handlers:
            return
        try:
            self._handlers.append(
                self.session.triggers.add_handler("selection changed", self._queue_refresh)
            )
        except Exception:
            pass

    def _queue_refresh(self, *_args):
        if self._refresh_pending:
            return
        self._refresh_pending = True

        def run_refresh():
            self._refresh_pending = False
            if not self.isVisible():
                return
            try:
                self.refresh()
            except Exception:
                pass

        QTimer.singleShot(400, run_refresh)

    def closeEvent(self, event):
        for handler in self._handlers:
            try:
                self.session.triggers.remove_handler(handler)
            except Exception:
                pass
        self._handlers = []
        super().closeEvent(event)

    def _build_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(5, 4, 5, 4)
        layout.setSpacing(3)
        self.setLayout(layout)
        self.setObjectName("DisplayControlsRoot")
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setStyleSheet(
            "QWidget#DisplayControlsRoot {"
            " background: #15181b;"
            " color: #e5e8ec;"
            " font-size: 12px;"
            "}"
            "QWidget#DisplayControlsRoot QLabel,"
            "QWidget#DisplayControlsRoot QPushButton,"
            "QWidget#DisplayControlsRoot QComboBox,"
            "QWidget#DisplayControlsRoot QLineEdit,"
            "QWidget#DisplayControlsRoot QCheckBox,"
            "QWidget#DisplayControlsRoot QSpinBox,"
            "QWidget#DisplayControlsRoot QDoubleSpinBox {"
            " font-size: 12px;"
            "}"
            "QLabel { color: #d9dde2; background: transparent; border: none; }"
            "QLabel#SectionTitle {"
            " font-size: 10px;"
            " font-weight: 700;"
            " color: #aeb6bf;"
            " letter-spacing: 0.05em;"
            " padding: 1px 0px;"
            "}"
            "QLabel#MutedCaption {"
            " color: #8d959f;"
            " font-size: 10px;"
            " letter-spacing: 0.02em;"
            "}"
            "QLabel#InlineSliderTitle {"
            " background: transparent;"
            " border: none;"
            " color: #e6ebf0;"
            " font-weight: 600;"
            " padding: 0px;"
            "}"
            "QLabel#InlineSliderValue {"
            " background: transparent;"
            " border: none;"
            " color: #d9dde2;"
            " padding: 0px;"
            " font-weight: 600;"
            "}"
            "QLabel#MetricSliderTitle {"
            " background: transparent;"
            " border: none;"
            " color: #e6e9ed;"
            " font-weight: 600;"
            " line-height: 1.1;"
            " padding: 0px;"
            "}"
            "QLabel#MetricSliderValue {"
            " background: transparent;"
            " border: none;"
            " color: #d9dde2;"
            " padding: 0px;"
            " font-weight: 600;"
            "}"
            "QPushButton {"
            " background: #1d2126;"
            " color: #eef1f4;"
            " border: 1px solid #2a2f35;"
            " border-radius: 7px;"
            " padding: 3px 8px;"
            " font-weight: 600;"
            " min-height: 20px;"
            " max-height: 24px;"
            "}"
            "QPushButton:hover { background: #262a30; border-color: #44494f; }"
            "QPushButton:pressed { background: #11141a; border-color: #2a2f35; }"
            "QComboBox, QLineEdit {"
            " background: #0e1114;"
            " color: #eef1f4;"
            " border: 1px solid #2a2f35;"
            " border-radius: 7px;"
            " padding: 3px 7px;"
            " min-height: 20px;"
            " max-height: 24px;"
            "}"
            "QComboBox:hover, QLineEdit:hover { border-color: #44494f; }"
            "QComboBox:focus, QLineEdit:focus { border-color: #6e757d; }"
            "QComboBox::drop-down { border: none; width: 18px; }"
            "QComboBox QAbstractItemView {"
            " background: #14181c;"
            " color: #eef1f4;"
            " border: 1px solid #2a2f35;"
            " border-radius: 8px;"
            " selection-background-color: #2c333a;"
            " padding: 4px;"
            "}"
            "QCheckBox {"
            " color: #e6ebf0;"
            " font-weight: 600;"
            " spacing: 4px;"
            "}"
            "QCheckBox::indicator {"
            " width: 12px;"
            " height: 12px;"
            " border: 1px solid #2a2f35;"
            " border-radius: 3px;"
            " background: #0e1114;"
            "}"
            "QCheckBox::indicator:hover { border-color: #44494f; }"
            "QCheckBox::indicator:checked {"
            " background: #6e757d;"
            " border-color: #8d959f;"
            "}"
            "QSpinBox, QDoubleSpinBox {"
            " background: #0e1114;"
            " color: #f0f4f8;"
            " border: 1px solid #2a2f35;"
            " border-radius: 7px;"
            " padding: 2px 7px;"
            " font-weight: 600;"
            " min-height: 20px;"
            " max-height: 24px;"
            "}"
            "QSpinBox:hover, QDoubleSpinBox:hover { border-color: #44494f; }"
            "QSpinBox:focus, QDoubleSpinBox:focus { border-color: #6e757d; }"
            "QSlider::groove:vertical {"
            " background: #0a0d10;"
            " border: 1px solid #2a2f35;"
            " width: 6px;"
            " border-radius: 3px;"
            "}"
            "QSlider::add-page:vertical {"
            " background: #0a0d10;"
            " border-radius: 3px;"
            "}"
            "QSlider::sub-page:vertical {"
            " background: #6e757d;"
            " border-radius: 3px;"
            "}"
            "QSlider::handle:vertical {"
            " background: #c0c7cf;"
            " border: 1px solid #14171a;"
            " height: 18px;"
            " margin: 0 -7px;"
            " border-radius: 9px;"
            "}"
            "QSlider::handle:vertical:hover { background: #ffffff; }"
            "QSlider::groove:horizontal {"
            " background: #0a0d10;"
            " border: 1px solid #2a2f35;"
            " height: 4px;"
            " border-radius: 2px;"
            "}"
            "QSlider::sub-page:horizontal {"
            " background: #6e757d;"
            " border-radius: 2px;"
            "}"
            "QSlider::handle:horizontal {"
            " background: #c0c7cf;"
            " border: 1px solid #14171a;"
            " width: 14px;"
            " margin: -6px 0;"
            " border-radius: 7px;"
            "}"
            "QSlider::handle:horizontal:hover { background: #ffffff; }"
            "QMenu {"
            " background: #14181c;"
            " color: #eef1f4;"
            " border: 1px solid #2a2f35;"
            " border-radius: 8px;"
            " padding: 4px;"
            "}"
            "QMenu::item { background: transparent; padding: 6px 14px; border-radius: 6px; }"
            "QMenu::item:selected { background: #2c333a; }"
            "QMenu::separator { height: 1px; background: #2a2f35; margin: 4px 8px; }"
        )

        self.status_label = QLabel("", self)
        self.status_label.setWordWrap(True)
        self.status_label.setObjectName("MutedCaption")
        layout.addWidget(self.status_label)

        self._build_color_controls(layout)

        sel_title = QLabel("SELECTION", self)
        sel_title.setObjectName("SectionTitle")
        layout.addWidget(sel_title)

        sel_row = QHBoxLayout()
        sel_row.setSpacing(4)
        layout.addLayout(sel_row)

        self.selection_transparency = _SliderBlock(
            "Sel trans",
            0,
            100,
            0,
            lambda v: f"{v}%",
            lambda _v: self._schedule_apply("selection"),
            self,
            editable=True,
            suffix="%",
            orientation=Qt.Orientation.Horizontal,
            title_min_width=56,
            value_min_width=52,
        )
        sel_row.addWidget(self.selection_transparency, 1)

        self.clear_transparency_button = QPushButton("Opaque", self)
        self.clear_transparency_button.setToolTip("Reset Sel trans to 0% on the current selection.")
        self.clear_transparency_button.clicked.connect(lambda: self._set_selection_transparency(0))
        self.clear_transparency_button.setMaximumWidth(66)
        sel_row.addWidget(self.clear_transparency_button, 0)

        # ---- LAYERS section: independent visibility + transparency for
        # cartoon / surface / atoms-and-bonds. Each row drives ChimeraX's
        # `cartoon|~cartoon`, `show|~show surface`, `show|~show atoms`
        # toggles and `transparency <scope> <pct> target <c|s|ab>`. Scope is
        # the current selection if one exists, otherwise every loaded
        # atomic structure.
        layers_title = QLabel("LAYERS", self)
        layers_title.setObjectName("SectionTitle")
        layout.addWidget(layers_title)

        self.cartoon_visible_check, self.cartoon_transparency = self._build_layer_row(
            layout, "Cartoon", "cartoon"
        )
        self.surface_visible_check, self.surface_transparency = self._build_layer_row(
            layout, "Surface", "surface"
        )
        self.atoms_visible_check, self.atoms_transparency = self._build_layer_row(
            layout, "Sticks", "atoms"
        )

        cartoon_title_row = QHBoxLayout()
        cartoon_title_row.setSpacing(4)
        layout.addLayout(cartoon_title_row)
        cartoon_title = QLabel("CARTOON  STYLE", self)
        cartoon_title.setObjectName("SectionTitle")
        cartoon_title_row.addWidget(cartoon_title, 1)

        self.reset_cartoon_button = QPushButton("Reset", self)
        self.reset_cartoon_button.setToolTip("Reset cartoon width/thickness to ChimeraX defaults.")
        self.reset_cartoon_button.clicked.connect(self._reset_cartoon)
        self.reset_cartoon_button.setMaximumWidth(56)
        cartoon_title_row.addWidget(self.reset_cartoon_button, 0)

        self.refresh_button = QPushButton("Refresh", self)
        self.refresh_button.setToolTip("Re-read the current selection state.")
        self.refresh_button.clicked.connect(self.refresh)
        self.refresh_button.setMaximumWidth(62)
        cartoon_title_row.addWidget(self.refresh_button, 0)

        self.protein_width, self.protein_thickness = self._build_cartoon_row(layout, "All", "protein")
        self.helix_width, self.helix_thickness = self._build_cartoon_row(layout, "Helix", "helix")
        self.strand_width, self.strand_thickness = self._build_cartoon_row(layout, "Sheet", "strand")

        sil_row = QHBoxLayout()
        sil_row.setSpacing(4)
        layout.addLayout(sil_row)

        self.silhouette_check = QCheckBox("Silhouette", self)
        self.silhouette_check.setToolTip("Toggle silhouette outlines on the model.")
        self.silhouette_check.toggled.connect(self._on_silhouette_toggled)
        sil_row.addWidget(self.silhouette_check, 0)

        self.silhouette_width = _SliderBlock(
            "",
            2,
            80,
            10,
            lambda v: f"{v / 10:.1f} px",
            lambda _v: self._schedule_apply("silhouette"),
            self,
            editable=True,
            suffix=" px",
            decimals=1,
            scale=0.1,
            orientation=Qt.Orientation.Horizontal,
            title_min_width=0,
            value_min_width=58,
        )
        sil_row.addWidget(self.silhouette_width, 1)

        layout.addStretch(1)

        self._sync_silhouette_state()
        self._relax_min_size()

        self.refresh()

    def _relax_min_size(self):
        self.setMinimumSize(0, 0)
        for child in self.findChildren(QWidget):
            try:
                child.setMinimumWidth(0)
            except Exception:
                pass
        for child_layout in self.findChildren(QLayout):
            try:
                child_layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
            except Exception:
                pass

    def _build_cartoon_row(self, layout, group_label, group_key):
        row = QHBoxLayout()
        row.setSpacing(4)
        layout.addLayout(row)

        label = QLabel(group_label, self)
        label.setObjectName("InlineSliderTitle")
        label.setMinimumWidth(32)
        label.setMaximumWidth(46)
        row.addWidget(label, 0)

        width_block = _SliderBlock(
            "w",
            5,
            60,
            20,
            self._angstrom_label,
            lambda _v, g=group_key: self._schedule_apply(g),
            self,
            editable=True,
            suffix=" Å",
            decimals=1,
            scale=0.1,
            orientation=Qt.Orientation.Horizontal,
            title_min_width=10,
            value_min_width=52,
        )
        row.addWidget(width_block, 1)

        thick_block = _SliderBlock(
            "t",
            1,
            30,
            4,
            self._angstrom_label,
            lambda _v, g=group_key: self._schedule_apply(g),
            self,
            editable=True,
            suffix=" Å",
            decimals=1,
            scale=0.1,
            orientation=Qt.Orientation.Horizontal,
            title_min_width=10,
            value_min_width=52,
        )
        row.addWidget(thick_block, 1)

        return width_block, thick_block

    def _build_layer_row(self, layout, label_text, layer_key):
        """One row of: [label] [show ✓] [transparency slider].

        ``layer_key`` is the internal identifier (``cartoon`` / ``surface``
        / ``atoms``) used to route slider updates to the right ChimeraX
        command in ``_apply_layer_transparency``.
        """
        row = QHBoxLayout()
        row.setSpacing(4)
        layout.addLayout(row)

        label = QLabel(label_text, self)
        label.setObjectName("InlineSliderTitle")
        label.setMinimumWidth(50)
        label.setMaximumWidth(68)
        row.addWidget(label, 0)

        show_check = QCheckBox("Show", self)
        show_check.setChecked(True)
        show_check.toggled.connect(
            lambda on, key=layer_key: self._on_layer_visibility_toggled(key, on)
        )
        show_check.setMaximumWidth(58)
        row.addWidget(show_check, 0)

        slider = _SliderBlock(
            "",
            0,
            100,
            0,
            lambda v: f"{v}%",
            lambda _v, key=layer_key: self._schedule_apply(f"layer_{key}"),
            self,
            editable=True,
            suffix="%",
            orientation=Qt.Orientation.Horizontal,
            title_min_width=0,
            value_min_width=52,
        )
        row.addWidget(slider, 1)

        return show_check, slider

    def _build_color_controls(self, layout):
        title = QLabel("COLOR", self)
        title.setObjectName("SectionTitle")
        layout.addWidget(title)

        control_row = QHBoxLayout()
        control_row.setSpacing(4)
        layout.addLayout(control_row)

        self.color_scope_combo = QComboBox(self)
        self.color_scope_combo.addItem("Selection", "sel")
        self.color_scope_combo.addItem("All", "all")
        self.color_scope_combo.setToolTip("Color selected atoms/residues/models or all open models.")
        self.color_scope_combo.setMinimumWidth(78)
        self.color_scope_combo.setMaximumWidth(110)
        control_row.addWidget(self.color_scope_combo, 0)

        self.color_target_combo = QComboBox(self)
        self.color_target_combo.addItem("All reps", "abcsp")
        self.color_target_combo.addItem("Cartoon", "c")
        self.color_target_combo.addItem("Atoms+bonds", "ab")
        self.color_target_combo.addItem("Surface", "s")
        self.color_target_combo.addItem("Models", "m")
        self.color_target_combo.setToolTip("ChimeraX color target letters: a atoms, b bonds, c cartoon, s surface, p pseudobonds, m models.")
        self.color_target_combo.setMinimumWidth(102)
        self.color_target_combo.setMaximumWidth(134)
        control_row.addWidget(self.color_target_combo, 0)

        self.color_hex_edit = QLineEdit("#d9d3c7", self)
        self.color_hex_edit.setMaxLength(9)
        self.color_hex_edit.setPlaceholderText("#RRGGBB")
        self.color_hex_edit.returnPressed.connect(self._apply_color_from_text)
        self.color_hex_edit.setMinimumWidth(82)
        self.color_hex_edit.setMaximumWidth(96)
        control_row.addWidget(self.color_hex_edit, 0)

        self.pick_color_button = QPushButton("Pick", self)
        self.pick_color_button.clicked.connect(self._pick_color)
        self.pick_color_button.setMaximumWidth(54)
        control_row.addWidget(self.pick_color_button)

        self.apply_color_button = QPushButton("Apply", self)
        self.apply_color_button.clicked.connect(self._apply_color_from_text)
        self.apply_color_button.setMaximumWidth(58)
        control_row.addWidget(self.apply_color_button)
        control_row.addStretch(1)

        self.rainbow_ramp = _RainbowRamp(self._rainbow_color_changed, self)
        self.rainbow_ramp.setToolTip("Drag along the rainbow ramp; release to apply the color.")
        layout.addWidget(self.rainbow_ramp)

        self.saturation_slider, self.saturation_value_label = self._make_horizontal_slider(
            "Saturation",
            0,
            100,
            78,
            lambda value: f"{value}%",
            self._saturation_changed,
            layout,
            editable=True,
            suffix="%",
        )
        self.saturation_slider.sliderReleased.connect(self._apply_current_color_preserve_transparency)
        self.saturation_value_label.editingFinished.connect(self._apply_current_color_preserve_transparency)

        self.color_transparency_slider, self.color_transparency_value_label = self._make_horizontal_slider(
            "Color trans",
            0,
            100,
            0,
            lambda value: f"{value}%",
            self._color_transparency_changed,
            layout,
            editable=True,
            suffix="%",
        )
        self.color_transparency_slider.sliderReleased.connect(self._apply_current_color_with_transparency)
        self.color_transparency_value_label.editingFinished.connect(self._apply_current_color_with_transparency)

        scheme_row = QHBoxLayout()
        scheme_row.setSpacing(4)
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

    def _make_horizontal_slider(self, title, minimum, maximum, value, formatter, changed_callback, layout, editable=False, suffix="%"):
        row = QHBoxLayout()
        row.setSpacing(4)
        layout.addLayout(row)

        label = QLabel(title, self)
        label.setObjectName("InlineSliderTitle")
        label.setMinimumWidth(56)
        label.setMaximumWidth(78)
        row.addWidget(label, 0)

        slider = QSlider(Qt.Orientation.Horizontal, self)
        slider.setRange(minimum, maximum)
        slider.setValue(value)
        slider.setTickPosition(QSlider.TickPosition.NoTicks)
        slider.valueChanged.connect(changed_callback)
        row.addWidget(slider, 1)

        if editable:
            spin = QSpinBox(self)
            spin.setObjectName("InlineSliderInput")
            spin.setRange(minimum, maximum)
            spin.setValue(value)
            if suffix:
                spin.setSuffix(suffix)
            spin.setMinimumWidth(52)
            spin.setMaximumWidth(62)
            spin.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
            spin.setKeyboardTracking(False)
            spin.valueChanged.connect(slider.setValue)
            slider.valueChanged.connect(spin.setValue)
            row.addWidget(spin, 0)
            return slider, spin

        value_label = QLabel(formatter(value), self)
        value_label.setObjectName("InlineSliderValue")
        value_label.setMinimumWidth(42)
        value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(value_label, 0)
        return slider, value_label

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
        widget = self.saturation_value_label
        if hasattr(widget, "setText") and not isinstance(widget, (QSpinBox, QDoubleSpinBox)):
            widget.setText(f"{int(value)}%")
        self.rainbow_ramp.set_saturation(float(value) / 100.0, final=False)

    def _color_transparency_changed(self, value):
        widget = self.color_transparency_value_label
        if hasattr(widget, "setText") and not isinstance(widget, (QSpinBox, QDoubleSpinBox)):
            widget.setText(f"{int(value)}%")

    def _apply_current_color_preserve_transparency(self):
        color = self._normalized_color_code()
        if color is not None:
            self._apply_color(color)

    def _apply_current_color_with_transparency(self):
        color = self._normalized_color_code()
        if color is not None:
            self._apply_color(color, transparency=int(self.color_transparency_slider.value()))

    def _pick_color(self):
        from Qt.QtGui import QColor
        from Qt.QtWidgets import QColorDialog

        current = self._normalized_color_code() or "#d9d3c7"
        color = QColorDialog.getColor(QColor(current), self, "Choose ChimeraX color")
        if not color.isValid():
            return
        self.color_hex_edit.setText(color.name())
        self._apply_color(color.name())

    def _apply_color(self, color, transparency=None):
        scope = self._color_scope()
        if scope is None:
            return
        target = self.color_target_combo.currentData() or "abcsp"
        command = f"color {scope} {color} target {target}"
        if transparency is not None:
            command += f" transparency {int(transparency)}"
        try:
            _run(self.session, command)
        except Exception as err:
            self.status_label.setText(str(err) if str(err) else err.__class__.__name__)
            return
        self._preserve_charge_tips(scope, target)
        self._auto_name_region(scope, color)
        self.status_label.setText(f"Applied: {command}")

    def _apply_color_scheme(self, scheme, transparency=None):
        scope = self._color_scope()
        if scope is None:
            return
        target = self.color_target_combo.currentData() or "abcsp"
        command = f"color {scope} {scheme} target {target}"
        if transparency is not None:
            command += f" transparency {int(transparency)}"
        try:
            _run(self.session, command)
        except Exception as err:
            self.status_label.setText(str(err) if str(err) else err.__class__.__name__)
            return
        if scheme != "byelement":
            self._preserve_charge_tips(scope, target)
        self._auto_name_region(scope, scheme)
        self.status_label.setText(f"Applied: {command}")

    def _auto_name_region(self, scope, color_or_scheme):
        if color_or_scheme in (
            "bychain", "bymodel", "byelement", "byhetero", "byidentity",
        ):
            return
        spec = scope
        if scope == "sel":
            spec = self._concrete_selection_spec()
        if not spec:
            return
        s = str(spec).strip()
        if ":" not in s:
            return
        name = "codex_" + "".join(
            ch if (ch.isalnum() or ch == "_") else "_" for ch in s
        ).strip("_")
        if not name or name == "codex_":
            return
        try:
            _run(self.session, f"name {name} {s}")
            from .named_selection import add_group

            add_group(self.session, name, s)
        except Exception as err:
            self.session.logger.info(
                f"[Codex auto-name] skipped {s!r}: {err}"
            )

    def _concrete_selection_spec(self):
        try:
            from chimerax.atomic import selected_residues

            residues = selected_residues(self.session)
        except Exception:
            return None
        if residues is None or len(residues) == 0:
            return None
        try:
            chain_groups = list(residues.by_chain)
        except Exception:
            return None
        parts = []
        for structure, chain_id, chain_residues in chain_groups:
            model_spec = f"#{getattr(structure, 'id_string', '?')}"
            chain_label = str(chain_id or "?").strip() or "?"
            try:
                numbers = sorted({int(n) for n in chain_residues.numbers})
            except Exception:
                continue
            if not numbers:
                continue
            ranges = []
            start = prev = numbers[0]
            for n in numbers[1:]:
                if n == prev + 1:
                    prev = n
                    continue
                ranges.append((start, prev))
                start = prev = n
            ranges.append((start, prev))
            range_str = ",".join(
                f"{s}-{e}" if s != e else f"{s}" for s, e in ranges
            )
            parts.append(f"{model_spec}/{chain_label}:{range_str}")
        return " ".join(parts) if parts else None

    def _preserve_charge_tips(self, scope, target):
        if "a" not in (target or ""):
            return
        from .display_color import restore_charge_colors

        try:
            restore_charge_colors(self.session, scope)
        except Exception:
            pass

    def _schedule_apply(self, group):
        self._pending.add(group)
        sliders = [
            self.selection_transparency,
            self.protein_width,
            self.protein_thickness,
            self.helix_width,
            self.helix_thickness,
            self.strand_width,
            self.strand_thickness,
            self.silhouette_width,
        ]
        for extra in (
            getattr(self, "cartoon_transparency", None),
            getattr(self, "surface_transparency", None),
            getattr(self, "atoms_transparency", None),
        ):
            if extra is not None:
                sliders.append(extra)
        for slider in sliders:
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
            if "silhouette" in pending:
                self._apply_silhouette_width()
            if "layer_cartoon" in pending:
                self._apply_layer_transparency("cartoon")
            if "layer_surface" in pending:
                self._apply_layer_transparency("surface")
            if "layer_atoms" in pending:
                self._apply_layer_transparency("atoms")
        finally:
            self.refresh()

    def _apply_selection_transparency(self):
        percent = int(self.selection_transparency.value())
        if not _has_selection(self.session):
            self.status_label.setText("No selected atoms/residues/models. Select something first, then move Sel trans.")
            return
        spec = self._selection_transparency_spec()
        command = f"transparency {spec} {percent} target absp"
        _run(self.session, command)
        self.status_label.setText(f"Applied: {command} (cartoon excluded)")

    def _selection_transparency_spec(self):
        mode = getattr(self.session, "_codex_bridge_selection_click_mode", "")
        spec = str(getattr(self.session, "_codex_bridge_last_chain_selection_spec", "") or "").strip()
        if mode == "chain" and spec:
            return spec
        return "sel"

    # ---- LAYERS section apply paths ----
    # Mapping from layer key to ChimeraX `transparency target` letters.
    _LAYER_TARGET = {
        "cartoon": "c",
        "surface": "s",
        "atoms": "ab",  # atoms + bonds (sticks/ball-and-stick)
    }

    def _layer_scope(self):
        """Apply layer commands to the current selection if there is one,
        otherwise to every open atomic structure (so toggling Show off
        actually hides the cartoon for everything, not nothing)."""
        if _has_selection(self.session):
            return "sel"
        return ""

    def _apply_layer_transparency(self, layer_key):
        slider = {
            "cartoon": getattr(self, "cartoon_transparency", None),
            "surface": getattr(self, "surface_transparency", None),
            "atoms": getattr(self, "atoms_transparency", None),
        }.get(layer_key)
        if slider is None:
            return
        target = self._LAYER_TARGET.get(layer_key)
        if target is None:
            return
        percent = int(slider.value())
        scope = self._layer_scope()
        prefix = f"{scope} " if scope else ""
        command = f"transparency {prefix}{percent} target {target}".strip()
        _run(self.session, command)
        self.status_label.setText(f"Applied: {command}")

    def _on_layer_visibility_toggled(self, layer_key, on):
        scope = self._layer_scope()
        spec_token = scope if scope else ""
        if layer_key == "cartoon":
            command = (
                f"cartoon {spec_token}".strip()
                if on
                else f"~cartoon {spec_token}".strip()
            )
        elif layer_key == "surface":
            # `surface` on its own builds molecular surfaces; `~surface`
            # hides them. We scope to the active selection (or omit so it
            # acts on everything) the same way the other layer commands do.
            command = (
                f"surface {spec_token}".strip()
                if on
                else f"~surface {spec_token}".strip()
            )
        elif layer_key == "atoms":
            # "Sticks" toggle: show/hide atoms + bonds. Cartoon is on the
            # cartoon row, so we don't touch it here.
            command = (
                f"show {spec_token} atoms".strip()
                if on
                else f"hide {spec_token} atoms".strip()
            )
        else:
            return
        try:
            _run(self.session, command)
            self.status_label.setText(f"Applied: {command}")
        except Exception as err:
            message = str(err) if str(err) else err.__class__.__name__
            self.status_label.setText(f"{layer_key} toggle failed: {message}")

    def _apply_cartoon_style(self, target, width_slider, thickness_slider):
        width = self._slider_float(width_slider)
        thickness = self._slider_float(thickness_slider)
        command = f"cartoon style {target} width {width:.1f} thickness {thickness:.1f}"
        _run(self.session, command)
        self.status_label.setText(f"Applied: {command}")

    def _apply_silhouette_width(self):
        raw = int(self.silhouette_width.value())
        width = raw / 10.0
        try:
            _run(self.session, "graphics silhouettes true")
            _run(self.session, f"graphics silhouettes width {width:.1f}")
        except Exception as err:
            message = str(err) if str(err) else err.__class__.__name__
            self.status_label.setText(f"Silhouette failed: {message}")
            return
        if hasattr(self, "silhouette_check"):
            self.silhouette_check.blockSignals(True)
            self.silhouette_check.setChecked(True)
            self.silhouette_check.blockSignals(False)
        self.status_label.setText(f"Silhouette width: {width:.1f} px")

    def _on_silhouette_toggled(self, on):
        cmd = "graphics silhouettes true" if on else "graphics silhouettes false"
        try:
            _run(self.session, cmd)
        except Exception as err:
            message = str(err) if str(err) else err.__class__.__name__
            self.status_label.setText(f"Silhouette toggle failed: {message}")
            return
        self.status_label.setText(f"Silhouette: {'on' if on else 'off'}")

    def _sync_silhouette_state(self):
        try:
            view = self.session.main_view
            enabled = bool(getattr(view, "silhouette", None) and view.silhouette.enabled)
            width = float(getattr(view.silhouette, "thickness", 1.0) or 1.0)
        except Exception:
            enabled, width = False, 1.0
        self.silhouette_check.blockSignals(True)
        self.silhouette_check.setChecked(enabled)
        self.silhouette_check.blockSignals(False)
        raw = max(2, min(80, int(round(width * 10))))
        self.silhouette_width.set_value(raw)

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
        # Sync sliders + checkboxes with the SCENE state of the new scope so
        # the user always sees the actual current transparency / visibility
        # rather than whatever was last typed into the slider.
        try:
            self._sync_selection_transparency_from_scene()
            self._sync_layer_state_from_scene()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # State-sync helpers: read the CURRENT scene state (Atom/Residue/
    # Surface attributes) and push it into the slider/checkbox widgets so
    # they reflect reality. Slider signals are blocked while we set
    # values so this doesn't re-fire the apply pipeline.
    # ------------------------------------------------------------------

    @staticmethod
    def _alpha_to_percent(alpha_0_to_255):
        try:
            return int(round(100 * (255 - float(alpha_0_to_255)) / 255))
        except Exception:
            return 0

    def _set_slider_silent(self, slider_block, percent):
        try:
            inner = slider_block.slider
        except Exception:
            return
        try:
            inner.blockSignals(True)
            slider_block.set_value(max(0, min(100, int(percent))))
        finally:
            try:
                inner.blockSignals(False)
            except Exception:
                pass
        try:
            slider_block.refresh_label()
        except Exception:
            pass

    def _set_check_silent(self, check, on):
        try:
            check.blockSignals(True)
            check.setChecked(bool(on))
        finally:
            try:
                check.blockSignals(False)
            except Exception:
                pass

    def _scope_atoms(self):
        """Atoms collection to read state from: current selection if any,
        else every loaded atomic structure."""
        try:
            from chimerax.atomic import selected_atoms, all_atomic_structures
        except Exception:
            return None
        try:
            if _has_selection(self.session):
                atoms = selected_atoms(self.session)
                if atoms is not None and len(atoms) > 0:
                    return atoms
        except Exception:
            pass
        try:
            structures = all_atomic_structures(self.session)
        except Exception:
            structures = []
        if not structures:
            return None
        try:
            from chimerax.atomic import Atoms

            atom_arrays = [s.atoms for s in structures if getattr(s, "atoms", None) is not None]
            if not atom_arrays:
                return None
            return Atoms.concatenate(atom_arrays) if len(atom_arrays) > 1 else atom_arrays[0]
        except Exception:
            return None

    def _sync_selection_transparency_from_scene(self):
        if not _has_selection(self.session):
            return
        try:
            from chimerax.atomic import selected_atoms
            import numpy as np

            atoms = selected_atoms(self.session)
            if atoms is None or len(atoms) == 0:
                return
            avg = float(np.mean(atoms.colors[:, 3]))
            self._set_slider_silent(
                self.selection_transparency, self._alpha_to_percent(avg)
            )
        except Exception:
            pass

    def _sync_layer_state_from_scene(self):
        required = (
            "cartoon_transparency", "surface_transparency", "atoms_transparency",
            "cartoon_visible_check", "surface_visible_check", "atoms_visible_check",
        )
        if not all(hasattr(self, name) for name in required):
            return  # UI not built yet
        atoms = self._scope_atoms()
        if atoms is None or len(atoms) == 0:
            return
        try:
            import numpy as np
        except Exception:
            return

        # --- Sticks (atoms + bonds) ---
        try:
            avg = float(np.mean(atoms.colors[:, 3]))
            self._set_slider_silent(
                self.atoms_transparency, self._alpha_to_percent(avg)
            )
        except Exception:
            pass
        try:
            self._set_check_silent(
                self.atoms_visible_check, bool(atoms.displays.any())
            )
        except Exception:
            pass

        # --- Cartoon (per-residue ribbon) ---
        residues = None
        for accessor in ("unique_residues", "residues"):
            try:
                value = getattr(atoms, accessor)
                if accessor == "residues" and hasattr(value, "unique"):
                    value = value.unique()
                if value is not None and len(value) > 0:
                    residues = value
                    break
            except Exception:
                continue
        if residues is not None and len(residues) > 0:
            try:
                avg = float(np.mean(residues.ribbon_colors[:, 3]))
                self._set_slider_silent(
                    self.cartoon_transparency, self._alpha_to_percent(avg)
                )
            except Exception:
                pass
            try:
                self._set_check_silent(
                    self.cartoon_visible_check, bool(residues.ribbon_displays.any())
                )
            except Exception:
                pass

        # --- Surface (per-structure MolecularSurface child models) ---
        try:
            structures = atoms.unique_structures
        except Exception:
            structures = []
        surf_alpha, surf_visible = self._aggregate_surface_state(structures)
        if surf_alpha is not None:
            self._set_slider_silent(
                self.surface_transparency, self._alpha_to_percent(surf_alpha)
            )
        if surf_visible is not None:
            self._set_check_silent(self.surface_visible_check, surf_visible)

    def _aggregate_surface_state(self, structures):
        """Walk MolecularSurface children for the given structures and
        compute (average alpha, any visible). Returns (None, None) if no
        molecular surface exists at all."""
        try:
            from chimerax.atomic import MolecularSurface
        except Exception:
            return None, None
        total_alpha = 0.0
        total_n = 0
        any_visible = False
        surf_seen = False
        for structure in structures:
            try:
                children = list(structure.child_models())
            except Exception:
                children = []
            for surf in children:
                if not isinstance(surf, MolecularSurface):
                    continue
                surf_seen = True
                try:
                    if surf.display:
                        any_visible = True
                except Exception:
                    pass
                try:
                    colors = surf.vertex_colors
                    if colors is not None and len(colors) > 0:
                        total_alpha += float(colors[:, 3].sum())
                        total_n += len(colors)
                        continue
                except Exception:
                    pass
                try:
                    single = surf.single_color
                    if single is not None:
                        total_alpha += float(single[3])
                        total_n += 1
                except Exception:
                    pass
        if not surf_seen:
            return None, None
        avg = (total_alpha / total_n) if total_n else None
        return avg, any_visible


class CodexDisplayControls(ToolInstance):

    SESSION_ENDURING = False
    SESSION_SAVE = False
    UI_LAYOUT_VERSION = 18
    help = "help:user/tools/codex_assistant.html"

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
        try:
            from . import _schedule_helper_dock_layout

            _schedule_helper_dock_layout(self.session, raise_tool="display controls")
        except Exception:
            pass

    def displayed(self):
        dock_widget = getattr(self.tool_window, "_dock_widget", None)
        if dock_widget is not None:
            return bool(dock_widget.isVisible())
        ui_area = getattr(self.tool_window, "ui_area", None)
        return bool(ui_area is not None and ui_area.isVisible())

    def delete(self):
        widget = getattr(self, "widget", None)
        if widget is not None:
            for handler in list(getattr(widget, "_handlers", [])):
                try:
                    self.session.triggers.remove_handler(handler)
                except Exception:
                    pass
            widget._handlers = []
        super().delete()
