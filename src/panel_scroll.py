"""Whole-panel scrolling that leaves form values unchanged during wheel gestures."""

import weakref

from Qt.QtCore import QEvent, Qt, QTimer
from Qt.QtWidgets import (
    QAbstractItemView, QAbstractScrollArea, QAbstractSlider, QAbstractSpinBox,
    QApplication, QComboBox, QLayout, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)


class PanelScrollArea(QScrollArea):
    """Scroll nested views first, then the containing panel at their boundaries.

    Descendant filters also prevent a wheel over a spin box, combo, or slider
    from editing it.  Filters are local to this panel; separate popup windows
    keep their normal input behavior.  Child events cover controls added later.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setMinimumSize(0, 0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        # Explicit scrollbar styling reserves a narrow gutter on macOS instead
        # of painting the native overlay scrollbar across controls at the edge.
        # Only scrolling surfaces are styled; panel typography stays inherited.
        self.setStyleSheet("""
            QScrollArea { background: transparent; border: none; }
            QScrollBar:vertical { background: transparent; width: 6px; }
            QScrollBar::handle:vertical {
                background: palette(mid); border-radius: 3px; min-height: 24px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: transparent;
            }
        """)
        self.verticalScrollBar().setSingleStep(24)
        self._watched = weakref.WeakSet()
        self._pending = {}
        self._fractional_steps = weakref.WeakKeyDictionary()

    def setWidget(self, content):
        super().setWidget(content)
        if content is not None:
            content.setMinimumWidth(0)
            policy = content.sizePolicy()
            policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
            content.setSizePolicy(policy)
            layout = content.layout()
            if layout is not None:
                layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
            self._watch_tree(content)

    def _contains(self, widget):
        """Return whether a widget belongs to the content, excluding popups."""
        try:
            content = self.widget()
            while isinstance(widget, QWidget):
                if widget is content:
                    return True
                if widget.isWindow():
                    return False
                widget = widget.parentWidget()
        except RuntimeError:
            # A queued child notification can outlive its child or this panel.
            pass
        return False

    def _watch_tree(self, widget):
        if not self._contains(widget):
            return
        if widget not in self._watched:
            widget.installEventFilter(self)
            self._watched.add(widget)
        for child in widget.findChildren(
                QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly):
            self._watch_tree(child)

    def _queue_children(self, widget):
        # ChildAdded can arrive before the child's QWidget constructor finishes.
        # Coalesce each changed subtree until the next Qt event-loop turn.
        if not self._pending:
            QTimer.singleShot(0, self._watch_pending)
        self._pending[id(widget)] = weakref.ref(widget)

    def _watch_pending(self):
        pending, self._pending = self._pending, {}
        for reference in pending.values():
            widget = reference()
            if widget is not None:
                self._watch_tree(widget)

    @staticmethod
    def _direction(event):
        pixels, angles = event.pixelDelta(), event.angleDelta()
        delta = pixels if not pixels.isNull() else angles
        horizontal = abs(delta.x()) > abs(delta.y())
        shifted = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if shifted and not horizontal:
            return True, delta.y(), not pixels.isNull()
        return horizontal, delta.x() if horizontal else delta.y(), not pixels.isNull()

    def _move_bar(self, area, horizontal, delta, pixels):
        bar = area.horizontalScrollBar() if horizontal else area.verticalScrollBar()
        if not ((delta > 0 and bar.value() > bar.minimum())
                or (delta < 0 and bar.value() < bar.maximum())):
            self._fractional_steps.pop(bar, None)
            return False
        if pixels:
            amount = float(delta)
            if isinstance(area, QAbstractItemView):
                mode = (area.horizontalScrollMode() if horizontal
                        else area.verticalScrollMode())
                if mode == QAbstractItemView.ScrollMode.ScrollPerItem:
                    # Item-based bars count rows/columns rather than pixels.
                    span = area.viewport().width() if horizontal else area.viewport().height()
                    amount *= max(1, bar.pageStep()) / max(1, span)
        else:
            amount = (delta / 120 * QApplication.wheelScrollLines()
                      * max(1, bar.singleStep()))
        remainder = self._fractional_steps.get(bar, 0.0)
        if remainder * amount < 0:
            remainder = 0.0
        amount += remainder
        whole = int(amount)
        self._fractional_steps[bar] = amount - whole
        if whole:
            bar.setValue(bar.value() - whole)
        # Accumulated sub-step gestures belong to this view too.
        return True

    def _route_wheel(self, watched, event):
        if watched is not self.viewport() and not self._contains(watched):
            return False
        horizontal, delta, pixels = self._direction(event)
        protected = False
        ancestor = watched
        while isinstance(ancestor, QWidget) and ancestor is not self:
            protected |= isinstance(ancestor, (QAbstractSlider, QAbstractSpinBox, QComboBox))
            if (delta and isinstance(ancestor, QAbstractScrollArea)
                    and self._move_bar(ancestor, horizontal, delta, pixels)):
                event.accept()
                return True
            ancestor = ancestor.parentWidget()
        if delta and not horizontal and self._move_bar(self, False, delta, pixels):
            event.accept()
            return True
        if protected:
            # Consume even at an outer boundary, including focused controls.
            event.accept()
            return True
        return False

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind == QEvent.Type.Wheel:
            if self._route_wheel(watched, event):
                return True
        elif kind in (QEvent.Type.ChildAdded, QEvent.Type.ChildPolished):
            if isinstance(watched, QWidget) and self._contains(watched):
                self._queue_children(watched)
        return super().eventFilter(watched, event)

    def wheelEvent(self, event):
        if not self._route_wheel(self.viewport(), event):
            super().wheelEvent(event)


def wrap_panel(parent, *, attribute="_codex_panel_scroll"):
    """Idempotently wrap a completed panel, keeping its layout and widgets.

    The original layout becomes the scroll content.  Its spacing, widget signal
    connections, and stretch factors remain intact; only the new outer layout
    has zero margins.  Keep a returned reference if the tool needs its scrollbar.
    """
    existing = getattr(parent, attribute, None)
    if isinstance(existing, QScrollArea):
        try:
            if existing.parentWidget() is parent:
                return existing
        except RuntimeError:
            pass
    content = QWidget()
    current = parent.layout()
    if current is not None:
        content.setLayout(current)
    else:
        QVBoxLayout(content)
    scroll = PanelScrollArea(parent)
    scroll.setWidget(content)
    outer = QVBoxLayout(parent)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)
    outer.addWidget(scroll)
    parent.setMinimumHeight(0)
    setattr(parent, attribute, scroll)
    return scroll
