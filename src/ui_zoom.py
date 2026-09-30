"""Browser-style UI zoom for the whole ChimeraX window (Cmd/Ctrl + / - / 0).

Qt cannot change its device scale factor after start-up, so the zoom rescales
what widgets are built from: the application font, explicit widget fonts,
``px``/``pt`` lengths in style sheets, fixed/minimum/maximum sizes, icon sizes
and web views (Log, help). Each widget remembers its unzoomed base values, so
zooming is repeatable and 100 % restores the original exactly. Widgets that
appear or restyle themselves later are caught by an application event filter.
The OpenGL graphics view is untouched.
"""
import re

from chimerax.core.settings import Settings

LEVELS = (0.5, 0.67, 0.75, 0.8, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0, 2.5)
_LENGTH = re.compile(r"(?<![\w.#-])(\d+(?:\.\d+)?)(px|pt)\b")
_QT_MAX = 16777215


class UiZoomSettings(Settings):
    AUTO_SAVE = {"factor": 1.0}


def _scale_length(value, factor):
    # Hairlines stay crisp; everything else rounds to whole pixels.
    return value if value <= 1 else max(1, round(value * factor))


def scale_stylesheet(sheet, factor):
    if factor == 1.0:
        return sheet

    def replace(match):
        number = float(match.group(1))
        if match.group(2) == "pt":
            return "%gpt" % round(number * factor, 1)
        return "%dpx" % _scale_length(number, factor)
    return _LENGTH.sub(replace, sheet)


def _scale_pair(pair, factor):
    return tuple(value if value in (0, _QT_MAX) else _scale_length(value, factor) for value in pair)


def _font_key(font):
    return (font.pixelSize(), round(font.pointSizeF(), 2))


def _scaled_font(font, factor):
    from Qt.QtGui import QFont
    scaled = QFont(font)
    if font.pixelSize() > 0:
        scaled.setPixelSize(max(1, round(font.pixelSize() * factor)))
    elif font.pointSizeF() > 0:
        scaled.setPointSizeF(font.pointSizeF() * factor)
    return scaled


class UiZoom:
    """One per session; owns the event filter, zoom keys and saved factor."""

    def __init__(self, session):
        from Qt.QtCore import QObject, QEvent, QTimer
        from Qt.QtGui import QFont
        from Qt.QtWidgets import QApplication

        self.session = session
        self.app = QApplication.instance()
        self.settings = UiZoomSettings(session, "Codex UI Zoom")
        self.factor = 1.0
        self._base_app_font = QFont(self.app.font())
        self._busy = False
        self._pending = []
        self._flush_timer = QTimer()
        self._flush_timer.setSingleShot(True)
        self._flush_timer.timeout.connect(self._flush)
        watched = {QEvent.Type.Polish, QEvent.Type.Show, QEvent.Type.StyleChange, QEvent.Type.FontChange}
        zoom = self

        keys = {QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress}

        class Filter(QObject):
            def eventFilter(self, obj, event):
                kind = event.type()
                if kind in keys and zoom.keys_enabled:
                    try:
                        action = zoom._key_action(event)
                    except Exception:
                        action = None  # never break keyboard input
                    if action is not None:
                        if kind == QEvent.Type.ShortcutOverride:
                            event.accept()  # claim the key before any QAction/QShortcut
                        else:
                            action()
                        return True
                elif zoom.factor != 1.0 and not zoom._busy and kind in watched:
                    zoom._queue(obj)
                return False

        self._filter = Filter()
        self.app.installEventFilter(self._filter)
        self.keys_enabled = False
        self._states = {}  # C++ address -> {key: [base, applied]}

    # -- public -----------------------------------------------------------
    def enable_keys(self):
        """Cmd/Ctrl + (or =), Cmd/Ctrl -, Cmd/Ctrl 0 in every ChimeraX window.

        Handled in the application event filter rather than QShortcut: tool
        windows share the main window's shortcut scope, so a second binding of
        the Help viewer's own zoom keys would make both ambiguous and dead.
        """
        self.keys_enabled = True

    def _key_action(self, event):
        from Qt.QtCore import Qt
        from Qt.QtWidgets import QApplication
        ignored = Qt.KeyboardModifier.KeypadModifier.value | Qt.KeyboardModifier.ShiftModifier.value
        if event.modifiers().value & ~ignored != Qt.KeyboardModifier.ControlModifier.value:  # Cmd on macOS
            return None
        action = {Qt.Key.Key_Plus.value: self.zoom_in, Qt.Key.Key_Equal.value: self.zoom_in,
                  Qt.Key.Key_Minus.value: self.zoom_out, Qt.Key.Key_0.value: self.reset}.get(event.key())
        if action is None:
            return None
        widget = QApplication.focusWidget()
        while widget is not None:  # the Help viewer keeps its own page zoom
            if type(widget).__module__.startswith("chimerax.help_viewer"):
                return None
            widget = widget.parentWidget()
        return action

    def zoom_in(self):
        self.set_factor(next((level for level in LEVELS if level > self.factor + 1e-6), LEVELS[-1]))

    def zoom_out(self):
        self.set_factor(next((level for level in reversed(LEVELS) if level < self.factor - 1e-6), LEVELS[0]))

    def reset(self):
        self.set_factor(1.0)

    def set_factor(self, factor, *, save=True, announce=True):
        factor = min(LEVELS[-1], max(LEVELS[0], round(float(factor), 3)))
        self.factor = factor
        self._busy = True
        try:
            self.app.setFont(_scaled_font(self._base_app_font, factor))
            for widget in self.app.allWidgets():
                self._apply(widget)
        finally:
            self._busy = False
        if save:
            self.settings.factor = factor
        if announce:
            self.session.logger.status(f"UI zoom {round(factor * 100)}%  (Cmd/Ctrl + / − / 0)",
                                       secondary=True)
        return factor

    # -- internals --------------------------------------------------------
    def _queue(self, obj):
        from Qt.QtWidgets import QWidget
        if isinstance(obj, QWidget):
            self._pending.append(obj)
            if not self._flush_timer.isActive():
                self._flush_timer.start(0)

    def _flush(self):
        pending, self._pending = self._pending, []
        self._busy = True
        try:
            seen = set()
            for widget in pending:
                try:
                    if id(widget) not in seen:
                        seen.add(id(widget))
                        self._apply(widget)
                except RuntimeError:
                    continue  # deleted before the deferred pass
        finally:
            self._busy = False

    def _track(self, state, key, current, scale, assign):
        """Scale from the remembered base; a value changed by other code becomes the new base."""
        record = state.get(key)
        if record is None or record[1] != current:
            record = [current, current]
        target = scale(record[0])
        if target != current:
            assign(target)
        record[1] = target
        state[key] = record

    def _state(self, widget, create):
        from PyQt6 import sip
        key = sip.unwrapinstance(widget)
        state = self._states.get(key)
        if state is None and create:
            state = self._states[key] = {}
            widget.destroyed.connect(lambda *_args, key=key: self._states.pop(key, None))
        return state

    def _apply(self, widget):
        from Qt.QtCore import QSize, Qt
        from Qt.QtWidgets import (QAbstractButton, QAbstractItemView, QComboBox, QTabBar,
                                  QToolBar)
        factor = self.factor
        state = self._state(widget, create=factor != 1.0)
        if state is None:
            return  # never zoomed: nothing to restore

        if hasattr(widget, "setZoomFactor") and hasattr(widget, "page"):
            widget.setZoomFactor(factor)  # QWebEngineView: Log, help, reports

        sheet = widget.styleSheet()
        if sheet or "qss" in state:
            self._track(state, "qss", sheet, lambda base: scale_stylesheet(base, factor),
                        widget.setStyleSheet)

        if widget.testAttribute(Qt.WidgetAttribute.WA_SetFont) or "font" in state:
            font = widget.font()
            record = state.get("font")
            if record is None or record[1] != _font_key(font):
                record = [font, _font_key(font)]
            target = _scaled_font(record[0], factor)
            if _font_key(target) != _font_key(font):
                widget.setFont(target)
            state["font"] = [record[0], _font_key(target)]

        def pair(size):
            return (size.width(), size.height())

        if not widget.isWindow():  # top-level minimums come from their layouts
            for key, getter, setter in (("min", widget.minimumSize, widget.setMinimumSize),
                                        ("max", widget.maximumSize, widget.setMaximumSize)):
                current = pair(getter())
                if key in state or any(value not in (0, _QT_MAX) for value in current):
                    self._track(state, key, current, lambda base: _scale_pair(base, factor),
                                lambda value, setter=setter: setter(QSize(*value)))

        if isinstance(widget, (QAbstractButton, QAbstractItemView, QComboBox, QTabBar, QToolBar)):
            self._track(state, "icon", pair(widget.iconSize()), lambda base: _scale_pair(base, factor),
                        lambda value: widget.setIconSize(QSize(*value)))


def get_ui_zoom(session, create=True):
    zoom = getattr(session, "_codex_ui_zoom", None)
    if zoom is None and create:
        zoom = session._codex_ui_zoom = UiZoom(session)
    return zoom


def install_ui_zoom(session):
    """Called once the main window exists; restores the saved factor."""
    if not getattr(session.ui, "is_gui", False):
        return None
    zoom = get_ui_zoom(session)
    zoom.enable_keys()
    saved = zoom.settings.factor
    if saved and abs(saved - 1.0) > 1e-6:
        zoom.set_factor(saved, save=False, announce=False)
    return zoom
