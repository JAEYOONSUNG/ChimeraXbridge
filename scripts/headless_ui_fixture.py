"""Test-only Qt hosts; never opens an OS window or installs into the bundle."""
import os


def install(session):
    assert not session.ui.is_gui, "Fixture requires ChimeraX --nogui"
    assert os.environ.get("QT_QPA_PLATFORM") == "offscreen", "Offscreen Qt is required"
    from Qt.QtCore import QObject, Signal, Qt, QThread
    from Qt.QtWidgets import QApplication, QWidget, QVBoxLayout, QMainWindow, QDockWidget
    app = QApplication.instance() or QApplication([])
    assert app.platformName() == "offscreen"

    class Dispatcher(QObject):
        queued = Signal(object)

        def __init__(self):
            super().__init__()
            self.queued.connect(self.invoke, Qt.ConnectionType.QueuedConnection)

        def invoke(self, callback):
            callback()

        def post(self, callback):
            if QThread.currentThread() is app.thread():
                callback()
            else:
                self.queued.emit(callback)

    class OffscreenHost(QDockWidget):
        def raise_(self):
            pass

    class OffscreenToolWindow:
        def __init__(self, tool, **kwargs):
            self._dock_widget = OffscreenHost()
            content = QWidget()
            layout = QVBoxLayout(content)
            layout.setContentsMargins(0, 0, 0, 0)
            self.ui_area = QWidget()
            layout.addWidget(self.ui_area)
            self._dock_widget.setWidget(content)
            self.shown = True

        def manage(self, **kwargs):
            self._dock_widget.show()
            self.ui_area.show()

    import chimerax.ui
    chimerax.ui.MainToolWindow = OffscreenToolWindow
    # Native get_singleton deliberately suppresses tools in --nogui. Only the
    # fixture's offscreen hosts may construct these widgets during this check.
    import chimerax.core.tools
    def offscreen_singleton(session, cls, name, create=True, display=True, **kwargs):
        existing = session.tools.find_by_class(cls)
        if existing:
            return existing[0]
        return cls(session, name, **kwargs) if create else None
    chimerax.core.tools.get_singleton = offscreen_singleton
    dispatcher = Dispatcher()
    window = QMainWindow()
    window.resize(1280, 800)
    # Keep all owned native objects alive throughout the test process.
    session._quality_offscreen_objects = (app, dispatcher, window)
    session.ui.main_window = window
    session.ui.thread_safe = dispatcher.post
    session.ui.forward_keystroke = lambda *args: None
    return app
