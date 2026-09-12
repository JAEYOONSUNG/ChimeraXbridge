"""Check Sequence startup/restore ordering with plain Python and a fake event loop."""
import ast
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / "src" / "__init__.py"
NAMES = {"_ensure_sequence_bar_visible", "_open_startup_sequence_bar",
         "_install_sequence_bar_startup"}
TREE = ast.parse(SOURCE.read_text())
NAMESPACE = {"__package__": "sequence_startup_check"}
exec(compile(ast.Module(body=[node for node in TREE.body
    if isinstance(node, ast.FunctionDef) and node.name in NAMES],
    type_ignores=[]), str(SOURCE), "exec"), NAMESPACE)
install = NAMESPACE["_install_sequence_bar_startup"]
open_once = NAMESPACE["_open_startup_sequence_bar"]


class Triggers:
    def __init__(self):
        self.handlers = {}

    def add_handler(self, name, callback):
        self.handlers.setdefault(name, []).append(callback)
        return callback

    def fire(self, name):
        for callback in self.handlers.get(name, ()):
            callback(name, None)


class Bar:
    tool_name = "Sequence Bar"

    def __init__(self):
        self.visible = False
        self.show_count = 0

    def display(self, visible):
        self.visible = visible
        self.show_count += bool(visible)

    def displayed(self):
        return self.visible

    def refresh(self):
        pass


class SequenceStartupChecks(unittest.TestCase):
    def setUp(self):
        self.pending = []
        self.bars = []
        self.session = SimpleNamespace(
            ui=SimpleNamespace(is_gui=True, main_window=None, triggers=Triggers()),
            tools=SimpleNamespace(list=lambda: self.bars[:]), triggers=Triggers())

        def singleton(session, create, display):
            if not self.bars and create:
                self.bars.append(Bar())
            return self.bars[0] if self.bars else None

        qt = ModuleType("Qt.QtCore")
        qt.QTimer = SimpleNamespace(singleShot=lambda delay, callback:
                                    self.pending.append((delay, callback)))
        sequence = ModuleType("sequence_startup_check.sequence_bar")
        sequence.CodexSequenceBar = SimpleNamespace(get_singleton=singleton)
        self.modules = patch.dict(sys.modules, {"Qt.QtCore": qt,
            "sequence_startup_check.sequence_bar": sequence})
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def drain(self):
        while self.pending:
            callbacks, self.pending = sorted(self.pending, key=lambda item: item[0]), []
            for _, callback in callbacks:
                callback()

    def ready(self):
        self.session.ui.main_window = object()
        self.session.ui.triggers.fire("ready")

    def test_waits_for_ui_and_opens_without_models(self):
        install(self.session)
        self.drain()
        self.assertEqual(self.bars, [])
        self.ready()
        self.assertTrue(self.bars[0].displayed())

    def test_late_initialization_opens_once(self):
        self.session.ui.main_window = object()
        install(self.session)
        install(self.session)
        self.drain()
        self.assertEqual(self.bars[0].show_count, 1)
        self.assertEqual(len(self.session.triggers.handlers["begin restore session"]), 1)

    def test_close_is_respected_by_pending_startup_callbacks(self):
        install(self.session)
        self.ready()
        self.bars[0].display(False)
        open_once(self.session)  # The later workspace startup callback.
        self.drain()
        self.assertFalse(self.bars[0].displayed())
        self.assertEqual(self.bars[0].show_count, 1)

    def test_slow_restore_recreates_visible_panel_after_reset(self):
        install(self.session)
        self.ready()
        self.session.triggers.fire("begin restore session")
        self.bars.clear()  # ChimeraX resets tools while restoring a session.
        self.drain()  # All startup retry deadlines elapse while loading.
        self.assertEqual(self.bars, [])
        self.session.triggers.fire("end restore session")
        self.assertEqual(self.bars, [])  # Wait for the restored graphics widget.
        self.drain()
        self.assertTrue(self.bars[0].displayed())

    def test_restore_does_not_reopen_panel_the_user_closed(self):
        install(self.session)
        self.ready()
        self.bars[0].display(False)
        self.session.triggers.fire("begin restore session")
        self.bars.clear()
        self.session.triggers.fire("end restore session")
        self.drain()
        self.assertEqual(self.bars, [])

    def test_restore_before_first_open_still_opens_sequence(self):
        install(self.session)
        self.session.triggers.fire("begin restore session")
        self.ready()
        self.drain()
        self.assertEqual(self.bars, [])
        self.session.triggers.fire("end restore session")
        self.drain()
        self.assertTrue(self.bars[0].displayed())

    def test_headless_session_does_not_schedule_ui_work(self):
        self.session.ui.is_gui = False
        install(self.session)
        self.assertEqual(self.pending, [])
        self.assertEqual(self.session.triggers.handlers, {})


if __name__ == "__main__":
    unittest.main()
