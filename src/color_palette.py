"""A native Qt color palette with separate sampling, copying and application."""

from Qt.QtCore import QCoreApplication, Qt, Signal
from Qt.QtGui import QColor
from Qt.QtWidgets import (
    QApplication, QColorDialog, QHBoxLayout, QLabel, QPushButton,
    QSizePolicy, QVBoxLayout, QWidget,
)


def _button_text(text):
    return text.replace("&", "").replace("…", "").rstrip(".").strip().casefold()


class ColorPaletteDialog(QColorDialog):
    """Choose/sample freely; copy or apply only through an explicit action.

    ``colorApplied`` carries RGB only. The owner decides which target to change
    and its transparency behavior; this dialog has no molecular/session API.
    """

    colorApplied = Signal(str)

    def __init__(self, initial_color, parent=None, *, target_label="", can_apply=False):
        initial = QColor(initial_color)
        if not initial.isValid():
            initial = QColor("#808080")
        initial.setAlpha(255)
        super().__init__(initial, parent)
        self._disposed = False
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.finished.connect(self._retire)
        self.destroyed.connect(lambda *_args: self._retire())
        self.setObjectName("CodexColorPalette")
        self.setWindowTitle("Color palette")
        self.setModal(False)
        self.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog, True)
        self.setOption(QColorDialog.ColorDialogOption.NoButtons, True)
        self.setOption(QColorDialog.ColorDialogOption.ShowAlphaChannel, False)
        no_eyedropper = getattr(QColorDialog.ColorDialogOption, "NoEyeDropperButton", None)
        if no_eyedropper is not None:
            self.setOption(no_eyedropper, False)
        # macOS may create the RGB/HEX widgets only after native mode is
        # disabled. Sync the initial color into those newly created fields.
        self.setCurrentColor(initial)
        self._can_apply = False

        instructions = QLabel(
            "Choose a swatch, adjust RGB, or sample the screen.\n"
            "Then copy the HEX value or apply the color to your target.", self)
        instructions.setObjectName("palette_instructions")
        instructions.setWordWrap(True)
        instructions.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.instructions_label = instructions
        self.layout().insertWidget(0, instructions)

        footer = QWidget(self)
        footer.setObjectName("palette_actions")
        layout = QVBoxLayout(footer)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(6)
        self.target_label = QLabel(footer)
        self.target_label.setObjectName("palette_target")
        self.target_label.setTextFormat(Qt.TextFormat.PlainText)
        self.target_label.setWordWrap(True)
        self.target_label.setMinimumWidth(0)
        self.target_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.target_label)
        self.status_label = QLabel(footer)
        self.status_label.setObjectName("palette_status")
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.copy_button = QPushButton("Copy HEX", footer)
        self.copy_button.setObjectName("palette_copy")
        self.copy_button.setToolTip("Copy the selected #RRGGBB value to the clipboard.")
        self.copy_button.clicked.connect(self.copy_hex)
        self.apply_button = QPushButton("Apply color", footer)
        self.apply_button.setObjectName("palette_apply")
        self.apply_button.clicked.connect(self.apply_color)
        self.close_button = QPushButton("Close", footer)
        self.close_button.setObjectName("palette_close")
        self.close_button.setToolTip("Close the palette. Colors already applied are kept.")
        self.close_button.clicked.connect(self.reject)
        actions.addWidget(self.copy_button)
        actions.addStretch(1)
        actions.addWidget(self.apply_button)
        actions.addWidget(self.close_button)
        layout.addLayout(actions)
        self.layout().addWidget(footer)
        self.eyedropper_button = self._configure_eyedropper()
        self.hex_edit = self._rename_hex_label()
        if self.hex_edit is not None:
            # QColorDialog retains the last valid QColor while this field is
            # incomplete. Track the editable text as well as color changes so
            # an explicit action cannot silently use that previous color.
            self.hex_edit.textChanged.connect(self._hex_changed)
        for button in self.findChildren(QPushButton):
            # Enter in an RGB/HEX field must not accidentally apply a color,
            # start screen sampling, or overwrite a custom palette slot.
            button.setAutoDefault(False)
            button.setDefault(False)
        self.setStyleSheet(
            "QLabel#palette_instructions { padding: 0 0 7px 0; }"
            "QLabel#palette_target { font-weight: 600; }"
            "QLabel#palette_status { padding: 1px 0; }"
            "QWidget#palette_actions QPushButton { min-height: 26px; max-height: 32px; padding: 2px 10px; }"
            "QPushButton#palette_apply:enabled { font-weight: 600; }"
        )
        self.currentColorChanged.connect(self._color_changed)
        self.set_apply_target(target_label, can_apply)
        self._color_changed(self.currentColor())

    def _configure_eyedropper(self):
        names = {
            _button_text("&Pick Screen Color"),
            _button_text(QCoreApplication.translate("QColorDialog", "&Pick Screen Color")),
            _button_text(QCoreApplication.translate("QColorDialog", "Pick Screen Color")),
        }
        for button in self.findChildren(QPushButton):
            if _button_text(button.text()) in names:
                button.setObjectName("palette_eyedropper")
                button.setText("Pick screen color")
                button.setToolTip(
                    "Eyedropper: click a pixel anywhere on screen; Esc cancels sampling. "
                    "Then choose Copy HEX or Apply color.")
                return button
        # Native Qt remains responsible for platform availability and input
        # capture. Never substitute an OS screenshot or mouse-grab overlay.
        return None

    def _rename_hex_label(self):
        names = {_button_text("HTML:"),
                 _button_text(QCoreApplication.translate("QColorDialog", "&HTML:")),
                 _button_text(QCoreApplication.translate("QColorDialog", "HTML:"))}
        for label in self.findChildren(QLabel):
            if _button_text(label.text()) in names:
                label.setText("HEX:")
                label.setToolTip("RGB color written as #RRGGBB.")
                return label.buddy()

    def current_hex(self):
        if self._disposed:
            return ""
        color = self._candidate_color()
        return color.name(QColor.NameFormat.HexRgb).upper() if color.isValid() else ""

    def _candidate_color(self):
        if self.hex_edit is None:
            return self.currentColor()
        text = self.hex_edit.text()
        if (len(text) != 7 or not text.startswith("#")
                or any(char not in "0123456789abcdefABCDEF" for char in text[1:])):
            return QColor()
        return QColor(text)

    def _hex_changed(self, _text):
        self._color_changed(None)

    def _retire(self, *_args):
        self._disposed = True

    def set_apply_target(self, target_label, can_apply):
        """Refresh the owner's scope/availability without changing the color."""
        if self._disposed:
            return
        self._can_apply = bool(can_apply)
        description = str(target_label).strip()
        if self._can_apply:
            text = "Apply to: " + (description or "Current target")
            tip = "Apply the chosen RGB color to the target shown above."
        else:
            text = "Copy only · " + (description or "Choose a target in Display Controls to apply.")
            tip = "Choose a target in Display Controls before applying a color."
        self.target_label.setText(text)
        self.target_label.setToolTip(text)
        self.apply_button.setToolTip(tip)
        self.apply_button.setEnabled(self._can_apply and self._candidate_color().isValid())

    def _color_changed(self, _color):
        if self._disposed:
            return
        color = self._candidate_color()
        valid = color.isValid()
        self.copy_button.setEnabled(valid)
        self.apply_button.setEnabled(self._can_apply and valid)
        if valid:
            self.status_label.setText(
                f"{self.current_hex()}  ·  RGB {color.red()}, {color.green()}, {color.blue()}")
        else:
            self.status_label.setText("Enter a complete HEX color (#RRGGBB) to copy or apply.")

    def copy_hex(self):
        if self._disposed:
            return
        value = self.current_hex()
        if not value:
            return
        QApplication.clipboard().setText(value)
        self.status_label.setText(f"Copied {value}")

    def apply_color(self):
        if self._disposed or not self._can_apply:
            return
        value = self.current_hex()
        if value:
            self.colorApplied.emit(value)
