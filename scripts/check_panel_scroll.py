"""Native Qt checks for nested, dynamic, and value-preserving panel scrolling."""

import importlib.util
from pathlib import Path

from Qt.QtCore import QPoint, QPointF, Qt
from Qt.QtGui import QWheelEvent
from Qt.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QLabel, QPushButton, QSlider, QSpinBox,
    QTextEdit, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)
from PyQt6.QtTest import QTest
from PyQt6 import sip

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("panel_scroll_under_test", root / "src/panel_scroll.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
app = QApplication.instance() or QApplication([])


def wheel(widget, y=-120, *, pixels=False, x=0, modifiers=Qt.KeyboardModifier.NoModifier):
    position = widget.rect().center()
    event = QWheelEvent(QPointF(position), QPointF(widget.mapToGlobal(position)),
        QPoint(x, y) if pixels else QPoint(), QPoint() if pixels else QPoint(x, y),
        Qt.MouseButton.NoButton, modifiers, Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(widget, event)
    QApplication.processEvents()


host = QWidget()
host.setObjectName("panel_scroll_test")
layout = QVBoxLayout(host)
title = QLabel("Full content stays reachable")
layout.addWidget(title)
tree = QTreeWidget()
tree.setHeaderLabels(["Nested evidence"])
tree.setFixedHeight(100)
tree.header().setStretchLastSection(False)
tree.setColumnWidth(0, 700)
for index in range(60):
    QTreeWidgetItem(tree, [f"Evidence {index}"])
layout.addWidget(tree)
details = QTextEdit()
details.setPlainText("\n".join(f"Report line {index}" for index in range(80)))
details.setFixedHeight(90)
layout.addWidget(details)
combo = QComboBox()
combo.addItems([str(index) for index in range(80)])
combo.setMaxVisibleItems(6)
combo.setCurrentIndex(20)
layout.addWidget(combo)
spin = QSpinBox()
spin.setValue(42)
layout.addWidget(spin)
slider = QSlider(Qt.Orientation.Horizontal)
slider.setValue(42)
layout.addWidget(slider)
for index in range(8):
    label = QLabel(f"Condition {index}")
    label.setMinimumHeight(28)
    layout.addWidget(label)
bottom = QPushButton("Export at the very bottom")
layout.addWidget(bottom)
scroll = module.wrap_panel(host)
assert module.wrap_panel(host) is scroll, "Repeated styling nested the full-panel wrapper"
assert scroll.widget().layout() is layout, "Existing layout replaced instead of preserved"
assert scroll.minimumHeight() == 0
assert scroll.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
host.resize(300, 240)
host.show()
QTest.qWait(100)
outer = scroll.verticalScrollBar()
assert outer.maximum() > 0, "Small panel did not acquire whole-content overflow"
assert host.height() == 240, (host.height(), host.minimumHeight())
assert outer.width() == 6, (outer.width(), "Inconsistent scrollbar gutter")
viewport_edge = scroll.viewport().mapTo(scroll, QPoint(scroll.viewport().width(), 0)).x()
scrollbar_edge = outer.mapTo(scroll, QPoint()).x()
assert viewport_edge <= scrollbar_edge, "Scrollbar overlays the content controls"

# Normal nested wheel stays inside the tree; the following gesture at its edge
# advances the full panel, so the export button remains reachable.
inner = tree.verticalScrollBar()
inner.setValue(inner.minimum())
outer.setValue(0)
wheel(tree.viewport())
assert inner.value() > 0 and outer.value() == 0, (inner.value(), outer.value())
inner.setValue(inner.maximum())
wheel(tree.viewport())
assert outer.value() > 0, "Nested tree trapped scrolling at its bottom"
inner.setValue(0)
outer.setValue(100)
wheel(tree.viewport(), 120)
assert outer.value() < 100, "Nested tree trapped upward scrolling at its top"

# Trackpad pixels scroll text accurately, then hand over at the document edge.
text_bar = details.verticalScrollBar()
text_bar.setValue(0)
outer.setValue(0)
wheel(details.viewport(), -23, pixels=True)
assert text_bar.value() == 23 and outer.value() == 0, text_bar.value()
text_bar.setValue(text_bar.maximum())
wheel(details.viewport(), -23, pixels=True)
assert outer.value() == 23, outer.value()

# Horizontal gestures remain horizontal within a nested view.
horizontal = tree.horizontalScrollBar()
horizontal.setValue(0)
outer.setValue(0)
assert horizontal.maximum() > 0
wheel(tree.viewport(), 0, pixels=True, x=-31)
assert horizontal.value() > 0 and outer.value() == 0
horizontal.setValue(0)
wheel(tree.viewport(), -120, modifiers=Qt.KeyboardModifier.ShiftModifier)
assert horizontal.value() > 0 and outer.value() == 0, "Shift wheel scrolled vertically"

# Wheel never edits conditions, even when the control has keyboard focus and
# the full panel has reached its edge. Explicit keyboard changes still work.
for control, read_value in ((combo, combo.currentIndex), (spin, spin.value), (slider, slider.value)):
    before = read_value()
    control.setFocus()
    outer.setValue(0)
    wheel(control)
    assert read_value() == before and outer.value() > 0, type(control).__name__
    outer.setValue(outer.maximum())
    wheel(control)
    assert read_value() == before, f"Boundary wheel edited {type(control).__name__}"
    wheel(control, 0, pixels=True, x=-24)
    assert read_value() == before, f"Horizontal wheel edited {type(control).__name__}"
spin.setFocus()
QTest.keyClick(spin, Qt.Key.Key_Up)
assert spin.value() == 43, "Wheel protection disabled explicit keyboard editing"

# Filters follow new controls created after setWidget, including their editors.
dynamic = QSpinBox()
dynamic.setValue(57)
layout.insertWidget(0, dynamic)
QApplication.processEvents()
QApplication.processEvents()
outer.setValue(0)
wheel(dynamic.lineEdit(), -19, pixels=True)
assert dynamic.value() == 57 and outer.value() == 19

# A moved-out widget no longer belongs to this panel's event-routing scope.
other = QWidget()
dynamic.setParent(other)
assert not scroll._contains(dynamic)

# A control moved into another panel is owned by that panel's wheel filter,
# even though its original panel's filter is still installed on the widget.
other_layout = QVBoxLayout(other)
other_layout.addWidget(dynamic)
other_filler = QLabel("Second panel content")
other_filler.setMinimumHeight(600)
other_layout.addWidget(other_filler)
other_scroll = module.wrap_panel(other)
other.resize(300, 220)
other.show()
QApplication.processEvents()
QApplication.processEvents()
outer.setValue(0)
other_bar = other_scroll.verticalScrollBar()
assert other_bar.maximum() > 0
wheel(dynamic.lineEdit(), -17, pixels=True)
assert dynamic.value() == 57 and other_bar.value() == 17 and outer.value() == 0

# Replacing the content leaves any pending scan of the old subtree harmless.
# New descendants and a layout built before insertion are watched recursively.
other_scroll._queue_children(other_scroll.widget())
old_content = other_scroll.takeWidget()
replacement = QWidget()
replacement_layout = QVBoxLayout(replacement)
replacement_editor = QSpinBox()
replacement_editor.setValue(61)
replacement_layout.addWidget(replacement_editor)
replacement_filler = QLabel("Replacement content")
replacement_filler.setMinimumHeight(650)
replacement_layout.addWidget(replacement_filler)
other_scroll.setWidget(replacement)
QApplication.processEvents()
assert not other_scroll._contains(dynamic)
assert other_scroll._contains(replacement_editor.lineEdit())
other_bar.setValue(0)
wheel(replacement_editor.lineEdit(), -13, pixels=True)
assert replacement_editor.value() == 61 and other_bar.value() == 13
sip.delete(old_content)
other.close()
other.deleteLater()

# Small angle increments accumulate instead of being rounded away forever.
tree.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerItem)
inner.setValue(0)
outer.setValue(0)
for unused in range(12):
    wheel(tree.viewport(), -10)
assert inner.value() > 0 and outer.value() == 0, (inner.value(), outer.value(), inner.maximum())
inner.setValue(0)
for unused in range(50):
    wheel(tree.viewport(), -1, pixels=True)
assert inner.value() > 0 and outer.value() == 0, "Small trackpad deltas never moved item rows"

outer.setValue(outer.maximum())
QApplication.processEvents()
bottom_position = bottom.mapTo(scroll.viewport(), QPoint(0, bottom.height()))
assert bottom_position.y() <= scroll.viewport().height(), (bottom_position, scroll.viewport().size())
assert bottom_position.y() > 0, "Last action could not be reached"

# Native combo popups retain their own wheel behavior and never scroll the form.
combo.showPopup()
QApplication.processEvents()
popup = combo.view()
assert not scroll._contains(popup.viewport()), "Combo popup was treated as form content"
before = outer.value()
wheel(popup.viewport())
assert outer.value() == before
combo.hidePopup()

# Closing a panel can destroy its native widget before a coalesced ChildAdded
# callback runs. The queued scan must tolerate that normal teardown order.
closing_host = QWidget()
closing_layout = QVBoxLayout(closing_host)
closing_layout.addWidget(QLabel("Closing panel"))
closing_scroll = module.wrap_panel(closing_host)
closing_child = QSpinBox(closing_scroll.widget())
closing_scroll._queue_children(closing_scroll.widget())
assert closing_scroll._pending
sip.delete(closing_host)
assert not closing_scroll._contains(closing_child)
closing_scroll._watch_pending()
QApplication.processEvents()

host.close()
host.deleteLater()
QApplication.processEvents()
print("PANEL_SCROLL_OK")
