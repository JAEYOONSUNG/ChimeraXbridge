"""Offscreen palette semantics and layout; never sample or capture the screen."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile

assert not session.ui.is_gui
assert os.environ.get("QT_QPA_PLATFORM") == "offscreen"

from Qt.QtCore import QCoreApplication, QEvent, QPoint, QTimer, Qt
from Qt.QtGui import QColor
from Qt.QtWidgets import QApplication, QColorDialog, QDialogButtonBox, QLabel, QLineEdit, QPushButton, QWidget
from PyQt6.QtTest import QTest
from PyQt6 import sip

app = QApplication.instance() or QApplication([])
assert app.platformName() == "offscreen"
root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "chimerax.codex_bridge", root / "src/__init__.py",
    submodule_search_locations=[str(root / "src")])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
from chimerax.codex_bridge.color_palette import ColorPaletteDialog


def settle():
    for _ in range(5):
        app.processEvents()


clipboard = QApplication.clipboard()
clipboard.setText("Offscreen clipboard sentinel")
dialog = ColorPaletteDialog(QColor(20, 40, 60, 80))
applied = []
dialog.colorApplied.connect(applied.append)
dialog.show()
dialog.resize(540, 520)
settle()
assert not dialog.isModal()
assert dialog.testAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
assert dialog.testOption(QColorDialog.ColorDialogOption.DontUseNativeDialog)
assert dialog.testOption(QColorDialog.ColorDialogOption.NoButtons)
assert not dialog.testOption(QColorDialog.ColorDialogOption.ShowAlphaChannel)
assert dialog.current_hex() == "#14283C" and dialog.currentColor().alpha() == 255
assert QColorDialog.customCount() == 16
assert len({QColorDialog.standardColor(i).name() for i in range(48)}) >= 40
wells = [widget for widget in dialog.findChildren(QWidget)
         if widget.metaObject().className().rsplit("::", 1)[-1] == "QWellArray"]
assert len(wells) >= 2 and all(well.isVisible() for well in wells), [
    widget.metaObject().className() for widget in dialog.findChildren(QWidget)]
assert dialog.eyedropper_button is not None and dialog.eyedropper_button.isVisible()
assert dialog.eyedropper_button.text() == "Pick screen color"
assert "Esc" in dialog.eyedropper_button.toolTip()
assert dialog.hex_edit is not None, "The native editable HEX field was not retained"

def assert_initial_fields(palette, red, green, blue):
    assert QColor(palette.hex_edit.text()) == QColor(red, green, blue), palette.hex_edit.text()
    channels = {label.text().replace('&', '').strip().rstrip(':'): label.buddy()
                for label in palette.findChildren(QLabel) if label.buddy() is not None}
    assert [channels[channel].value() for channel in ('Red', 'Green', 'Blue')] == [red, green, blue]

assert_initial_fields(dialog, 20, 40, 60)
white_dialog = ColorPaletteDialog('#FFFFFF')
assert_initial_fields(white_dialog, 255, 255, 255)
white_dialog.close()
settle()

assert dialog.copy_button.isEnabled() and not dialog.apply_button.isEnabled()
assert "Copy only" in dialog.target_label.text()
assert all(not button.isDefault() and not button.autoDefault()
           for button in dialog.findChildren(QPushButton))
assert not any(box.isVisible() for box in dialog.findChildren(QDialogButtonBox))
assert clipboard.text() == "Offscreen clipboard sentinel" and not applied

# Native palette/HSV/RGB changes update only the candidate, never the scene or
# clipboard. Screen sampling emits the same native currentColorChanged signal;
# the actual eyedropper is deliberately never clicked during this test.
dialog.setCurrentColor(QColor("#E24B67"))
settle()
assert dialog.current_hex() == "#E24B67"
assert "RGB 226, 75, 103" in dialog.status_label.text()
assert clipboard.text() == "Offscreen clipboard sentinel" and not applied
dialog.apply_button.click()
dialog.apply_color()
assert not applied
dialog.copy_button.click()
assert clipboard.text() == "#E24B67" and not applied
assert dialog.status_label.text() == "Copied #E24B67"
assert dialog.isVisible()

dialog.set_apply_target("#1 / chain A · atoms", True)
assert dialog.apply_button.isEnabled()
assert dialog.target_label.text() == "Apply to: #1 / chain A · atoms"
assert dialog.current_hex() == "#E24B67" and not applied
dialog.hex_edit.setFocus()
dialog.hex_edit.selectAll()
QTest.keyClicks(dialog.hex_edit, "#1A2B3C")
QTest.keyClick(dialog.hex_edit, Qt.Key.Key_Return)
settle()
assert dialog.current_hex() == "#1A2B3C", "Enter did not commit the editable HEX field"
assert dialog.isVisible(), "Enter unexpectedly closed the palette"
assert clipboard.text() == "#E24B67" and not applied, "Entering HEX applied or copied implicitly"
dialog.apply_button.click()
assert applied == ["#1A2B3C"] and clipboard.text() == "#E24B67"
assert dialog.isVisible(), "Applying should keep the palette open for comparison"
dialog.setCurrentColor(QColor("#ABCDEF"))
assert applied == ["#1A2B3C"]
dialog.apply_button.click()
assert applied == ["#1A2B3C", "#ABCDEF"]

# A partially typed HEX value leaves Qt's last QColor intact. Neither a real
# mouse click nor a queued/direct action may apply or copy that stale color.
validation_dialog = ColorPaletteDialog("#ABCDEF", can_apply=True)
validation_applied = []
validation_dialog.colorApplied.connect(validation_applied.append)
validation_dialog.show()
settle()
clipboard.setText("Invalid HEX clipboard sentinel")
for incomplete in ("#12", "#", "", "#ABC", "#12345", "#12ZZZZ"):
    validation_dialog.hex_edit.setFocus()
    validation_dialog.hex_edit.selectAll()
    QTest.keyClick(validation_dialog.hex_edit, Qt.Key.Key_Backspace)
    QTest.keyClicks(validation_dialog.hex_edit, incomplete)
    settle()
    assert not validation_dialog.copy_button.isEnabled(), incomplete
    assert not validation_dialog.apply_button.isEnabled(), incomplete
    assert "#RRGGBB" in validation_dialog.status_label.text()
    assert validation_dialog.current_hex() == ""
    validation_dialog.set_apply_target("Current model", True)
    assert not validation_dialog.apply_button.isEnabled(), "Target refresh bypassed HEX validation"
    QTest.mouseClick(validation_dialog.apply_button, Qt.MouseButton.LeftButton)
    QTest.mouseClick(validation_dialog.copy_button, Qt.MouseButton.LeftButton)
    validation_dialog.apply_color()
    validation_dialog.copy_hex()
    settle()
    assert not validation_applied, incomplete
    assert clipboard.text() == "Invalid HEX clipboard sentinel", incomplete

validation_dialog.hex_edit.setFocus()
validation_dialog.hex_edit.selectAll()
QTest.keyClicks(validation_dialog.hex_edit, "#12ABef")
settle()
assert validation_dialog.copy_button.isEnabled() and validation_dialog.apply_button.isEnabled()
QTest.mouseClick(validation_dialog.apply_button, Qt.MouseButton.LeftButton)
QTest.mouseClick(validation_dialog.copy_button, Qt.MouseButton.LeftButton)
assert validation_applied == ["#12ABEF"] and clipboard.text() == "#12ABEF"

# RGB edits must replace an incomplete HEX value and restore valid actions.
# Use the native spin-box editor and keyboard commit, not setCurrentColor.
validation_dialog.hex_edit.setFocus()
validation_dialog.hex_edit.selectAll()
QTest.keyClicks(validation_dialog.hex_edit, "#12")
settle()
assert not validation_dialog.copy_button.isEnabled()
channels = {label.text().replace('&', '').strip().rstrip(':'): label.buddy()
            for label in validation_dialog.findChildren(QLabel) if label.buddy() is not None}
for channel, value in (("Red", 17), ("Green", 34), ("Blue", 51)):
    editor = channels[channel].findChild(QLineEdit)
    assert editor is not None, channel
    editor.setFocus()
    editor.selectAll()
    QTest.keyClicks(editor, str(value))
    QTest.keyClick(editor, Qt.Key.Key_Return)
    settle()
    assert channels[channel].value() == value
    assert validation_dialog.copy_button.isEnabled() and validation_dialog.apply_button.isEnabled()
assert validation_dialog.current_hex() == "#112233"
assert validation_dialog.hex_edit.text().upper() == "#112233"
assert validation_applied == ["#12ABEF"] and clipboard.text() == "#12ABEF"
QTest.mouseClick(validation_dialog.apply_button, Qt.MouseButton.LeftButton)
assert validation_applied == ["#12ABEF", "#112233"]

# Native swatches can also recover an invalid edit without any explicit apply
# or copy. This clicks only a color cell in the offscreen dialog.
validation_dialog.hex_edit.setFocus()
validation_dialog.hex_edit.selectAll()
QTest.keyClicks(validation_dialog.hex_edit, "#12")
assert not validation_dialog.copy_button.isEnabled()
validation_wells = [widget for widget in validation_dialog.findChildren(QWidget)
                    if widget.metaObject().className().rsplit("::", 1)[-1] == "QWellArray"]
QTest.mouseClick(validation_wells[0], Qt.MouseButton.LeftButton, pos=QPoint(8, 8))
settle()
assert validation_dialog.current_hex() == QColorDialog.standardColor(0).name().upper()
assert validation_dialog.copy_button.isEnabled() and validation_dialog.apply_button.isEnabled()
assert validation_applied == ["#12ABEF", "#112233"] and clipboard.text() == "#12ABEF"
validation_dialog.close()
settle()
QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
assert sip.isdeleted(validation_dialog)

# A stale/closed target disables only application; sampling and copying remain
# available, and rich-looking model names are displayed as literal text.
dialog.set_apply_target("Target closed: <b>old structure</b>", False)
assert not dialog.apply_button.isEnabled() and dialog.copy_button.isEnabled()
assert dialog.target_label.textFormat() == Qt.TextFormat.PlainText
assert "<b>old structure</b>" in dialog.target_label.text()
dialog.apply_color()
assert applied == ["#1A2B3C", "#ABCDEF"]
dialog.copy_button.click()
assert clipboard.text() == "#ABCDEF"

# Long target names must fit the existing dialog width, with no footer/control
# overlap or clipped action labels. QWidget.grab renders only this offscreen
# widget; it does not capture any desktop window or the user's screen.
dialog.set_apply_target("Model " + "verylongrecipientmodelname" * 10 + " / chain A", True)
dialog.resize(540, 520)
settle()
assert dialog.width() <= 600, (dialog.size().width(), dialog.minimumSizeHint().width())
for button in (dialog.copy_button, dialog.apply_button, dialog.close_button):
    point = button.mapTo(dialog, button.rect().topLeft())
    assert point.x() >= 0 and point.y() >= 0
    assert point.x() + button.width() <= dialog.width()
    assert point.y() + button.height() <= dialog.height()
    assert button.fontMetrics().horizontalAdvance(button.text()) + 12 <= button.width()
assert dialog.copy_button.geometry().right() < dialog.apply_button.geometry().left()
assert dialog.apply_button.geometry().right() < dialog.close_button.geometry().left()
dialog.set_apply_target("#1 / chain A · atoms", True)
dialog.resize(540, 520)
settle()
artifact = Path(tempfile.gettempdir()) / "color-palette-panel.png"
assert dialog.grab().save(str(artifact))
report = {"ok": True, "visible_windows": False,
          "dialog_size": [dialog.width(), dialog.height()],
          "stock_color_wells": len(wells), "custom_slots": QColorDialog.customCount(),
          "eyedropper_available": True, "eyedropper_activated": False,
          "screenshot": str(artifact)}
(Path(tempfile.gettempdir()) / "color-palette-report.json").write_text(
    json.dumps(report, indent=2), encoding="utf-8")
before = list(applied)
QTimer.singleShot(0, dialog.apply_color)
QTimer.singleShot(0, dialog.copy_hex)
dialog.close_button.click()
settle()
assert dialog._disposed and applied == before and clipboard.text() == "#ABCDEF"
QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
assert sip.isdeleted(dialog), "Closing left a hidden palette child alive"
dialog.apply_color()
dialog.copy_hex()
dialog.set_apply_target("Stale queued target", True)
dialog._color_changed(QColor("#111111"))
assert dialog.current_hex() == ""
assert applied == before and clipboard.text() == "#ABCDEF"

escape_dialog = ColorPaletteDialog("#123456", can_apply=True)
escape_dialog.colorApplied.connect(applied.append)
escape_dialog.show()
settle()
QTest.keyClick(escape_dialog, Qt.Key.Key_Escape)
settle()
assert escape_dialog._disposed and applied == before
QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
assert sip.isdeleted(escape_dialog)

deleted_dialog = ColorPaletteDialog("#654321", can_apply=True)
deleted_dialog.colorApplied.connect(applied.append)
QTimer.singleShot(0, deleted_dialog.apply_color)
QTimer.singleShot(0, deleted_dialog.copy_hex)
sip.delete(deleted_dialog)
assert deleted_dialog._disposed, "Native destruction did not retire the palette"
deleted_dialog.set_apply_target("Deleted native target", True)
deleted_dialog._color_changed(QColor("#222222"))
deleted_dialog.copy_hex()
deleted_dialog.apply_color()
settle()
assert applied == before and clipboard.text() == "#ABCDEF"
print("COLOR_PALETTE_OK: native swatches/eyedropper, validated HEX/RGB, explicit copy/apply, Enter/Esc, lifecycle and compact layout; " + str(artifact))
