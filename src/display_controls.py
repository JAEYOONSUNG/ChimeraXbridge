from Qt.QtCore import Qt, QTimer, QRectF, QSize
from Qt.QtGui import QColor, QLinearGradient, QPainter, QPen, QPainterPath
from Qt.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSlider,
    QFrame,
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
        self._value = 0.95
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
        self._value = max(self._value, 0.95)
        if self._saturation == 0:
            self._saturation = 0.78
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
        return QColor.fromHsvF(float(self._hue), float(self._saturation), self._value).name()

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


class _ColorPreview(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.colors = []
        self.setFixedSize(22, 22)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setToolTip("Colors present in the current color target.")

    def sizeHint(self):
        return QSize(22, 22)

    def minimumSizeHint(self):
        return self.sizeHint()

    def set_colors(self, colors):
        self.colors = [QColor(*[int(v) for v in color[:3]]) for color in colors]
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        clip = QPainterPath()
        clip.addRoundedRect(rect, 3, 3)
        p.setClipPath(clip)
        colors = self.colors or [QColor("#303f51")]
        width = rect.width() / len(colors)
        for i, color in enumerate(colors):
            p.fillRect(QRectF(rect.left() + i * width, rect.top(), width + 1, rect.height()), color)


class _StatusLabel(QLabel):
    def setText(self, text):
        super().setText(text)
        self.setVisible(bool(text))


class DisplayControlsWidget(QWidget):

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self._pending = set()
        self._apply_timer = QTimer(self)
        self._apply_timer.setSingleShot(True)
        self._apply_timer.timeout.connect(self._apply_pending)
        self._refresh_pending = False
        self._syncing = False
        self._closed = False
        self._handlers = []
        self._build_ui()
        self._install_handlers()

    def _install_handlers(self):
        if self._handlers:
            return
        for name, callback in (("selection changed", self._selection_changed),
                               ("command finished", self._queue_refresh)):
            self._handlers.append(self.session.triggers.add_handler(name, callback))
        from chimerax.atomic import get_triggers
        self._handlers.append(get_triggers().add_handler("changes done", self._queue_refresh))

    def _selection_changed(self, *_args):
        # A queued edit belongs to the previous selection. Never apply it to
        # the object that was just picked.
        self._apply_timer.stop()
        self._pending.clear()
        self._queue_refresh()

    def _queue_refresh(self, *_args):
        if self._closed or self._refresh_pending:
            return
        self._refresh_pending = True

        def run_refresh():
            self._refresh_pending = False
            if self._closed or not self.isVisible() or self._pending:
                return
            self.refresh()

        QTimer.singleShot(0, run_refresh)

    def showEvent(self, event):
        super().showEvent(event)
        self._queue_refresh()

    def cleanup(self):
        self._closed = True
        self._apply_timer.stop()
        self._pending.clear()
        for handler in self._handlers:
            handler.remove()
        self._handlers = []

    def closeEvent(self, event):
        self.cleanup()
        super().closeEvent(event)

    def _build_ui(self):
        self.setObjectName("DisplayControlsRoot")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumSize(280, 0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet(self._stylesheet())
        from .panel_scroll import PanelScrollArea
        wrapper = QVBoxLayout(self)
        wrapper.setContentsMargins(0, 0, 0, 0)
        self.scroll_area = PanelScrollArea(self)
        content = QWidget()
        content.setObjectName("ControlsBody")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(7, 6, 7, 5)
        layout.setSpacing(3)
        self.scroll_area.setWidget(content)
        wrapper.addWidget(self.scroll_area)
        header = QHBoxLayout()
        self.scope_label = QLabel("", self)
        self.scope_label.setObjectName("ScopeLabel")
        self.scope_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        header.addWidget(self.scope_label, 1)
        self.refresh_button = QPushButton("Refresh", self)
        self.refresh_button.clicked.connect(self.refresh)
        header.addWidget(self.refresh_button)
        layout.addLayout(header)
        self.scope_mode_label = QLabel(self)
        self.scope_mode_label.hide()
        self.scope_detail_label = QLabel("", self)
        self.scope_detail_label.setObjectName("Caption")
        layout.addWidget(self.scope_detail_label)

        body = QVBoxLayout()
        body.setContentsMargins(0, 0, 5, 0)
        body.setSpacing(4)
        layout.addLayout(body, 1)

        self._build_color_controls(body)
        row = self._section(body, "Layers")
        self.layer_scope_label = QLabel("", self)
        self.layer_scope_label.setObjectName("Caption")
        row.addWidget(self.layer_scope_label)
        for title, key in (("Cartoon", "cartoon"), ("Surface", "surface"), ("Atoms", "atoms")):
            check, slider = self._build_layer_row(body, title, key)
            setattr(self, key + "_visible_check", check)
            setattr(self, key + "_transparency", slider)
        row = QHBoxLayout()
        row.setSpacing(4)
        self.selection_transparency = self._slider("Selection", 0, 100, 0, "selection")
        self.selection_transparency.title_label.setFixedWidth(58)
        self.selection_transparency.setToolTip("Transparency of selected atoms, bonds and surfaces. Cartoon is unchanged.")
        row.addWidget(self.selection_transparency, 1)
        self.clear_transparency_button = QPushButton("Opaque", self)
        self.clear_transparency_button.clicked.connect(lambda: self._set_selection_transparency(0))
        row.addWidget(self.clear_transparency_button)
        body.addLayout(row)

        row = self._section(body, "Cartoon")
        note = QLabel("Per model", self)
        note.setObjectName("Caption")
        row.addWidget(note)
        self.reset_cartoon_button = QPushButton("Reset", self)
        self.reset_cartoon_button.clicked.connect(self._reset_cartoon)
        row.addWidget(self.reset_cartoon_button)
        self._geometry_titles = {}
        self.protein_width, self.protein_thickness = self._build_cartoon_row(body, "Protein", "protein")
        self.helix_width, self.helix_thickness = self._build_cartoon_row(body, "Helix", "helix")
        self.strand_width, self.strand_thickness = self._build_cartoon_row(body, "Sheet", "strand")
        row = self._section(body, "Outline")
        note = QLabel("Whole scene", self)
        note.setObjectName("Caption")
        row.addWidget(note)
        row = QHBoxLayout()
        row.setSpacing(4)
        self.silhouette_check = QPushButton("Off", self)
        self.silhouette_check.setObjectName("VisibilityButton")
        self.silhouette_check.setCheckable(True)
        self.silhouette_check.toggled.connect(self._on_silhouette_toggled)
        label = QLabel("Silhouette", self)
        label.setFixedWidth(58)
        row.addWidget(label)
        row.addWidget(self.silhouette_check)
        self.silhouette_width = self._slider("", 2, 80, 10, "silhouette", " px", 0.1)
        row.addWidget(self.silhouette_width, 1)
        body.addLayout(row)
        body.addStretch()
        self.status_label = _StatusLabel("", self)
        self.status_label.setWordWrap(True)
        self.status_label.setObjectName("Caption")
        self.status_label.hide()
        layout.addWidget(self.status_label)
        self.refresh()

    @staticmethod
    def _stylesheet():
        from .ui_theme import panel_stylesheet
        return panel_stylesheet("DisplayControlsRoot") + """
        QLabel#ScopeLabel { font-size: 12px; font-weight: 600; }
        QLabel#Caption { font-size: 10px; }
        QLabel#SectionHeading { font-weight: 600; }
        QFrame#SectionRule { border: none; border-top: 1px solid palette(mid); margin-top: 4px; }
        QWidget#ControlsBody { background: transparent; }
        QPushButton, QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox { max-height: 22px; }
        QPushButton#VisibilityButton { min-width: 35px; max-width: 43px; font-size: 10px; padding: 2px 5px; }
        QSpinBox, QDoubleSpinBox, QLineEdit#HexInput { font-family: "Menlo", "Consolas", monospace; font-size: 10px; }
        QSpinBox:disabled, QDoubleSpinBox:disabled { color: #858585; }
        QSlider::groove:horizontal { height: 3px; background: palette(base); border-radius: 1px; }
        QSlider::sub-page:horizontal { background: palette(mid); border-radius: 1px; }
        QSlider::handle:horizontal { background: palette(button-text); border: 1px solid palette(window); width: 11px; margin: -5px 0; border-radius: 6px; }
        QSlider::handle:horizontal:hover { background: palette(highlight); }
        QSlider::handle:horizontal:disabled { background: palette(mid); }
        """

    def _section(self, layout, title):
        rule = QFrame(self)
        rule.setObjectName("SectionRule")
        rule.setFixedHeight(5)
        layout.addWidget(rule)
        row = QHBoxLayout()
        row.setSpacing(5)
        label = QLabel(title, self)
        label.setObjectName("SectionHeading")
        row.addWidget(label)
        row.addStretch()
        layout.addLayout(row)
        return row

    def _slider(self, title, minimum, maximum, value, group, suffix="%", scale=1.0):
        block = _SliderBlock(title, minimum, maximum, value, lambda v: str(v),
                             lambda _v: self._schedule_apply(group), self,
                             editable=True, suffix=suffix, decimals=1 if scale != 1 else 0,
                             scale=scale, orientation=Qt.Orientation.Horizontal,
                             value_min_width=49)
        if not title:
            block.title_label.hide()
        block.slider.setMinimumHeight(22)
        block.value_label.setMaximumWidth(61)
        return block

    def _build_layer_row(self, layout, title, key):
        row = QHBoxLayout()
        row.setSpacing(4)
        label = QLabel(title, self)
        label.setFixedWidth(58)
        row.addWidget(label)
        state = QLabel(self)
        state.hide()
        setattr(self, key + "_state_label", state)
        check = QPushButton("Shown", self)
        check.setObjectName("VisibilityButton")
        check.setCheckable(True)
        check.setAccessibleName(title + " visibility")
        check.toggled.connect(lambda on, layer=key: self._on_layer_visibility_toggled(layer, on))
        row.addWidget(check)
        slider = self._slider("", 0, 100, 0, "layer_" + key)
        slider.setToolTip(title + " transparency")
        row.addWidget(slider, 1)
        layout.addLayout(row)
        return check, slider

    def _build_cartoon_row(self, layout, title, key):
        row = QHBoxLayout()
        row.setSpacing(4)
        label = QLabel(title, self)
        label.setFixedWidth(49)
        self._geometry_titles[key] = label
        row.addWidget(label)
        width = self._slider("w", 5, 60, 20, key, " Å", 0.1)
        thickness = self._slider("t", 1, 30, 4, key, " Å", 0.1)
        width.setToolTip("Cartoon width in angstroms, for models in the current scope.")
        thickness.setToolTip("Cartoon thickness in angstroms, for models in the current scope.")
        row.addWidget(width, 1)
        row.addWidget(thickness, 1)
        layout.addLayout(row)
        return width, thickness

    def _build_color_controls(self, layout):
        row = self._section(layout, "Color")
        self.color_scope_combo = QComboBox(self)
        self.color_scope_combo.addItem("Selection", "sel")
        self.color_scope_combo.addItem("All models", "all")
        self.color_scope_combo.setAccessibleName("Color scope")
        self.color_target_combo = QComboBox(self)
        for label, target in (("All reps", "abcsp"), ("Cartoon", "c"),
                              ("Atoms & bonds", "ab"), ("Surface", "s"), ("Models", "m")):
            self.color_target_combo.addItem(label, target)
        self.color_target_combo.setAccessibleName("Color representation")
        for combo in (self.color_scope_combo, self.color_target_combo):
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(7)
            combo.currentIndexChanged.connect(self._queue_refresh)
            row.addWidget(combo)
        row = QHBoxLayout()
        row.setSpacing(4)
        self.color_preview = _ColorPreview(self)
        row.addWidget(self.color_preview)
        self.color_name_label = QLabel(self)
        self.color_name_label.hide()
        self.color_hex_edit = QLineEdit(self)
        self.color_hex_edit.setObjectName("HexInput")
        self.color_hex_edit.setMaxLength(9)
        self.color_hex_edit.setMaximumWidth(86)
        self.color_hex_edit.setPlaceholderText("#RRGGBB")
        self.color_hex_edit.returnPressed.connect(self._apply_color_from_text)
        row.addWidget(self.color_hex_edit)
        self.rainbow_ramp = _RainbowRamp(self._rainbow_color_changed, self)
        self.rainbow_ramp.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        row.addWidget(self.rainbow_ramp, 1)
        self.pick_color_button = QPushButton("Pick", self)
        self.pick_color_button.clicked.connect(self._pick_color)
        row.addWidget(self.pick_color_button)
        self.apply_color_button = QPushButton("Apply", self)
        self.apply_color_button.clicked.connect(self._apply_color_from_text)
        row.addWidget(self.apply_color_button)
        layout.addLayout(row)
        self.saturation_slider, self.saturation_value_label = self._make_horizontal_slider(
            "Saturation", 0, 100, 78, lambda v: f"{v}%", self._saturation_changed, layout)
        self.saturation_slider.sliderReleased.connect(self._apply_current_color_preserve_transparency)
        self.saturation_value_label.editingFinished.connect(self._apply_current_color_preserve_transparency)
        self.color_transparency_slider, self.color_transparency_value_label = self._make_horizontal_slider(
            "Transparency", 0, 100, 0, lambda v: f"{v}%", self._color_transparency_changed, layout)
        self.color_transparency_slider.sliderReleased.connect(self._apply_current_color_with_transparency)
        self.color_transparency_value_label.editingFinished.connect(self._apply_current_color_with_transparency)
        row = QHBoxLayout()
        row.setSpacing(4)
        for attr, title, scheme in (("by_chain_button", "By chain", "bychain"),
                                    ("by_element_button", "By element", "byelement"),
                                    ("by_model_button", "By model", "bymodel")):
            button = QPushButton(title, self)
            button.clicked.connect(lambda _checked, value=scheme: self._apply_color_scheme(value))
            setattr(self, attr, button)
            row.addWidget(button, 1)
        layout.addLayout(row)

    def _make_horizontal_slider(self, title, minimum, maximum, value, formatter, callback, layout):
        block = _SliderBlock(title, minimum, maximum, value, formatter, callback, self,
                             editable=True, orientation=Qt.Orientation.Horizontal,
                             value_min_width=49)
        block.title_label.setFixedWidth(77)
        block.slider.setMinimumHeight(22)
        block.value_label.setMaximumWidth(61)
        layout.addWidget(block)
        return block.slider, block.value_label

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
        rgb = QColor(color)
        self.color_preview.set_colors([(rgb.red(), rgb.green(), rgb.blue())])
        self.color_name_label.setText("Custom color")
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
        elif self.color_hex_edit.placeholderText() == "Mixed":
            scope = self._color_scope()
            if scope:
                target = self.color_target_combo.currentData()
                percent = self.color_transparency_slider.value()
                _run(self.session, f"transparency {scope} {percent} target {target}")
                self.refresh()

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
        self.refresh()

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
        self.refresh()

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
        if self._syncing:
            return
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
        atoms = self._scope_atoms()
        if atoms is None or not len(atoms):
            return
        models = ",".join(m.id_string for m in atoms.unique_structures)
        command = f"cartoon style (#{models} & {target}) width {width:.1f} thickness {thickness:.1f}"
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
        self.silhouette_check.setText("On" if enabled else "Off")
        raw = max(2, min(80, int(round(width * 10))))
        self._set_slider_silent(self.silhouette_width, raw)

    def _set_selection_transparency(self, percent):
        self.selection_transparency.set_value(int(percent))
        self._schedule_apply("selection")

    def _reset_cartoon(self):
        for target, width, thickness in (
            ("protein", self.protein_width, self.protein_thickness),
            ("helix", self.helix_width, self.helix_thickness),
            ("strand", self.strand_width, self.strand_thickness),
        ):
            self._set_slider_silent(width, 20)
            self._set_slider_silent(thickness, 4)
            self._apply_cartoon_style(target, width, thickness)
        self.refresh()

    def refresh(self):
        if self._closed:
            return
        self._syncing = True
        try:
            selected = _has_selection(self.session)
            atoms = self._scope_atoms()
            count = len(atoms) if atoms is not None else 0
            spec = self._concrete_selection_spec() if selected else None
            groups = list(atoms.unique_residues.by_chain) if count else []
            if selected:
                parts = [f"#{m.id_string} / {chain or '—'}" for m, chain, _ in groups]
                heading = "  ·  ".join(parts[:2]) or "Selection"
                if len(parts) > 2:
                    heading += f"  +{len(parts) - 2}"
                self.scope_mode_label.setText("CURRENT SELECTION")
            else:
                heading = "All structures" if count else "Nothing open yet"
                self.scope_mode_label.setText("SCENE")
            self.scope_label.setText(heading)
            residues = len(atoms.unique_residues) if count else 0
            self.scope_detail_label.setText(
                f"{len(groups):,} chains  ·  {residues:,} residues  ·  {count:,} atoms" if count else
                "Open a structure to start editing its appearance.")
            self.scope_label.setToolTip(spec or "Layer controls apply to all structures when nothing is selected.")
            self.layer_scope_label.setText("Selection · transparency" if selected else "All structures · transparency")
            self.status_label.setText("")
            self.selection_transparency.setEnabled(selected and count > 0)
            self.clear_transparency_button.setEnabled(selected and count > 0)
            self._sync_selection_transparency_from_scene()
            self._sync_layer_state_from_scene()
            self._sync_layer_labels(atoms)
            self._sync_color_from_scene()
            self._sync_cartoon_from_scene()
            self._sync_silhouette_state()
        finally:
            self._syncing = False

    @staticmethod
    def _alpha_to_percent(alpha_0_to_255):
        return int(round(100 * (255 - float(alpha_0_to_255)) / 255))

    @staticmethod
    def _set_widget_silent(widget, value):
        blocked = widget.blockSignals(True)
        try:
            widget.setValue(value)
        finally:
            widget.blockSignals(blocked)

    def _set_slider_silent(self, slider_block, value):
        self._set_widget_silent(slider_block.slider, int(value))
        # Blocking slider signals also blocks the slider -> spinbox connection.
        # Update both explicitly, including scaled Å / px input fields.
        if slider_block._editable:
            scaled = slider_block.value() * slider_block._scale
            self._set_widget_silent(slider_block.value_label,
                                    scaled if slider_block._decimals else int(scaled))
        else:
            slider_block.refresh_label()

    def _sync_color_from_scene(self):
        import numpy as np
        all_models = self.color_scope_combo.currentData() == "all"
        atoms = self._scope_atoms(all_models=all_models) if all_models or _has_selection(self.session) else None
        target = self.color_target_combo.currentData()
        # A surface/volume model can be a valid native color target even when
        # there are no atomic colors to preview. Only an empty scope is disabled.
        has_models = bool(self.session.models.list())
        has_target = has_models if all_models else _has_selection(self.session)
        empty_hint = ("Open a structure to color its atoms, cartoon or surface." if not has_models else
                      "Select atoms, residues or a model, or choose All models in Color scope.")
        for widget in (self.color_hex_edit, self.pick_color_button, self.apply_color_button,
                       self.by_chain_button, self.by_element_button, self.by_model_button):
            widget.setEnabled(has_target)
            widget.setToolTip("Apply color to the chosen scope and representation." if has_target else empty_hint)
        self.color_scope_combo.setToolTip(
            "Color scope is independent of the layer controls below." if has_target else empty_hint)
        colors = []
        if atoms is not None and len(atoms):
            if target in ("c", "abcsp"):
                residues = atoms.unique_residues
                colors.extend(residues.ribbon_colors[residues.ribbon_displays])
            if target in ("s", "abcsp"):
                for surf, mask in self._scope_surfaces(atoms):
                    if surf.display:
                        vc = surf.vertex_colors
                        colors.extend(vc[mask] if vc is not None else [surf.color])
            if target in ("ab", "abcsp"):
                colors.extend(atoms.colors if target == "ab" else atoms.colors[atoms.displays])
            if target == "m":
                colors.extend(m.color for m in atoms.unique_structures if m.color is not None)
            if not len(colors) and target == "abcsp":
                colors = atoms.colors
        valid = len(colors) > 0
        for widget in (self.rainbow_ramp, self.saturation_slider, self.saturation_value_label,
                       self.color_transparency_slider, self.color_transparency_value_label):
            widget.setEnabled(valid)
        if not valid:
            for widget in (self.color_transparency_slider, self.color_transparency_value_label,
                           self.saturation_slider, self.saturation_value_label):
                self._set_widget_silent(widget, 0)
            self.color_hex_edit.clear()
            self.color_hex_edit.setPlaceholderText(
                "No color" if has_target else "Select first" if has_models else "Open model")
            self.color_hex_edit.setToolTip(
                "No displayed color in this representation. Enter a color or use Pick to color the target."
                if has_target else empty_hint)
            self.color_preview.set_colors([])
            self.color_name_label.setText("No displayed color in this representation" if has_target else empty_hint)
            self.color_preview.setToolTip(self.color_name_label.text())
            return
        colors = np.asarray(colors)
        unique, counts = np.unique(colors[:, :3], axis=0, return_counts=True)
        rgb = unique[counts.argmax()]
        color = QColor(*(int(c) for c in rgb))
        mixed = len(unique) > 1
        self.color_preview.set_colors(unique[counts.argsort()[::-1][:8]])
        self.color_name_label.setText(f"{len(unique):,} colors in this target" if mixed else color.name().upper())
        self.color_preview.setToolTip(self.color_name_label.text())
        self.color_hex_edit.setText("" if mixed else color.name())
        self.color_hex_edit.setPlaceholderText("Mixed" if mixed else "#RRGGBB")
        self.color_hex_edit.setToolTip("Multiple colors. Pick a color to replace them." if mixed else color.name())
        self.rainbow_ramp._hue = max(0.0, color.hsvHueF())
        self.rainbow_ramp._saturation = color.hsvSaturationF()
        self.rainbow_ramp._value = color.valueF()
        self.rainbow_ramp.update()
        saturation = round(color.hsvSaturationF() * 100)
        for widget in (self.saturation_slider, self.saturation_value_label):
            self._set_widget_silent(widget, saturation)
        percent = self._alpha_to_percent(np.mean(colors[:, 3]))
        for widget in (self.color_transparency_slider, self.color_transparency_value_label):
            self._set_widget_silent(widget, percent)
            widget.setToolTip("Average transparency across the current color target.")

    def _sync_cartoon_from_scene(self):
        atoms = self._scope_atoms()
        structures = atoms.unique_structures if atoms is not None and len(atoms) else []
        for group, label, key, width, thickness in (
            ("helix", "Helix", "scale_helix", self.helix_width, self.helix_thickness),
            ("strand", "Sheet", "scale_sheet", self.strand_width, self.strand_thickness),
            ("protein", "Protein", "scale_helix", self.protein_width, self.protein_thickness),
        ):
            values = [getattr(m.ribbon_xs_mgr, key) for m in structures]
            if group == "protein":
                values += [m.ribbon_xs_mgr.scale_sheet for m in structures]
            any_mixed = False
            for index, block in enumerate((width, thickness)):
                block.setEnabled(bool(values))
                if values:
                    self._set_slider_silent(block, round(values[0][index] * 20))
                    mixed = any(v[index] != values[0][index] for v in values)
                    any_mixed |= mixed
                    block.setToolTip("Mixed dimensions; showing the first value." if mixed else
                                     "Dimensions apply to entire models in the current scope.")
                else:
                    self._set_slider_silent(block, block.slider.minimum())
            self._geometry_titles[group].setText(label + (" *" if any_mixed else ""))

    def _set_check_silent(self, check, on):
        try:
            check.blockSignals(True)
            check.setChecked(bool(on))
            if isinstance(check, QPushButton):
                check.setText("Shown" if on else "Hidden")
        finally:
            try:
                check.blockSignals(False)
            except Exception:
                pass

    def _sync_layer_labels(self, atoms):
        import numpy as np
        available = atoms is not None and len(atoms) > 0
        descriptions = {"cartoon": "Backbone ribbon", "surface": "Molecular envelope", "atoms": "Atomic detail"}
        for key in descriptions:
            check = getattr(self, key + "_visible_check")
            slider = getattr(self, key + "_transparency")
            check.setEnabled(available)
            slider.setEnabled(available)
            slider.value_label.setPrefix("")
            getattr(self, key + "_state_label").setText(descriptions[key] if available else "No atoms in this target")
        if not available:
            self._set_slider_silent(self.selection_transparency, 0)
            return
        for key, flags, alphas in (
            ("atoms", atoms.displays, atoms.colors[:, 3]),
            ("cartoon", atoms.unique_residues.ribbon_displays, atoms.unique_residues.ribbon_colors[:, 3]),
        ):
            check = getattr(self, key + "_visible_check")
            if flags.any() and not flags.all():
                check.setText("Mixed")
                check.setToolTip("Some are shown. Click to hide all in this target.")
            else:
                check.setToolTip("Toggle visibility for this target.")
            if len(alphas) and np.min(alphas) != np.max(alphas):
                getattr(self, key + "_state_label").setText("Mixed transparency · average shown")
                getattr(self, key + "_transparency").value_label.setPrefix("~")
                getattr(self, key + "_transparency").setToolTip("Mixed transparency; the average is shown.")
        surface_alphas = []
        for surf, mask in self._scope_surfaces(atoms):
            colors = surf.vertex_colors
            surface_alphas.extend(np.unique(colors[mask, 3]) if colors is not None else [surf.color[3]])
        if len(set(surface_alphas)) > 1:
            self.surface_state_label.setText("Mixed transparency · average shown")
            self.surface_transparency.value_label.setPrefix("~")
            self.surface_transparency.setToolTip("Mixed transparency; the average is shown.")

    def _scope_atoms(self, all_models=False):
        """Atoms collection to read state from: current selection if any,
        else every loaded atomic structure."""
        try:
            from chimerax.atomic import selected_atoms, all_atomic_structures
        except Exception:
            return None
        try:
            if not all_models and _has_selection(self.session):
                atoms = selected_atoms(self.session)
                if atoms is not None and len(atoms) > 0:
                    return atoms
                models = self.session.selection.models()
                from chimerax.atomic import concatenate, Atoms
                return concatenate([m.atoms for m in models if hasattr(m, "atoms")], Atoms)
        except Exception:
            pass
        try:
            structures = all_atomic_structures(self.session)
        except Exception:
            structures = []
        if not structures:
            return None
        try:
            from chimerax.atomic import concatenate

            atom_arrays = [s.atoms for s in structures if getattr(s, "atoms", None) is not None]
            if not atom_arrays:
                return None
            return concatenate(atom_arrays) if len(atom_arrays) > 1 else atom_arrays[0]
        except Exception:
            return None

    def _sync_selection_transparency_from_scene(self):
        if not _has_selection(self.session):
            self._set_slider_silent(self.selection_transparency, 0)
            return
        try:
            from chimerax.atomic import selected_atoms
            import numpy as np

            atoms = self._scope_atoms()
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
            for name in ("cartoon", "surface", "atoms"):
                self._set_slider_silent(getattr(self, name + "_transparency"), 0)
                self._set_check_silent(getattr(self, name + "_visible_check"), False)
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

        alpha, visible = self._aggregate_surface_state(atoms)
        self._set_slider_silent(self.surface_transparency, self._alpha_to_percent(alpha))
        self._set_check_silent(self.surface_visible_check, visible)

    def _scope_surfaces(self, atoms):
        from chimerax.atomic import MolecularSurface
        for structure in atoms.unique_structures:
            for surf in structure.child_models():
                if not isinstance(surf, MolecularSurface) or surf.vertices is None:
                    continue
                if not surf.atoms.mask(atoms).any():
                    continue
                mask, _ = surf._vertices_for_atoms(atoms)
                if mask is not None:
                    yield surf, mask

    def _aggregate_surface_state(self, atoms):
        import numpy as np
        total_alpha = total_n = 0
        visible = False
        for surf, mask in self._scope_surfaces(atoms):
            if surf.display:
                triangles = surf.triangles
                vmask = np.zeros(len(surf.vertices), dtype=bool)
                vmask[mask] = True
                tmask = vmask[triangles].any(axis=1)
                if surf.triangle_mask is not None:
                    tmask &= surf.triangle_mask
                visible |= bool(tmask.any())
            colors = surf.vertex_colors
            if colors is not None:
                alpha = colors[mask, 3]
                total_alpha += float(alpha.sum())
                total_n += len(alpha)
            else:
                total_alpha += float(surf.color[3])
                total_n += 1
        return (total_alpha / total_n if total_n else 255), visible


class CodexDisplayControls(ToolInstance):

    SESSION_ENDURING = False
    SESSION_SAVE = False
    UI_LAYOUT_VERSION = 24
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
            widget.cleanup()
        super().delete()
