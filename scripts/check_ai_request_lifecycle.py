"""Real offscreen Assistant with controlled workers; no backend/network requests."""
import importlib.util
import os
from pathlib import Path
import runpy
import sys
import tempfile
import threading
import time
from unittest.mock import patch

from PyQt6 import sip
from PyQt6.QtTest import QTest

root = Path(__file__).resolve().parents[1]
app = runpy.run_path(str(root / "scripts/headless_ui_fixture.py"))["install"](session)
spec = importlib.util.spec_from_file_location(
    "chimerax.codex_bridge", root / "src/__init__.py",
    submodule_search_locations=[str(root / "src")])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
package._schedule_helper_dock_layout = lambda *a, **kw: None
from chimerax.codex_bridge import service, tool


def wait_for(predicate):
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(5)
    assert predicate(), "Timed out waiting for controlled AI worker"


assistant = tool.CodexAssistant(session, "AI Assistant")
entered = threading.Event()
release = threading.Event()
calls = []
commands_applied = []


def controlled_request(_session, prompt, **options):
    calls.append((prompt, options))
    entered.set()
    assert release.wait(5), "Test did not release AI worker"
    if "executor" in options:
        options["executor"]("select clear")
        commands_applied.append(prompt)
    return "Fixture response ready"


with patch.object(tool, "run_mode_request", side_effect=controlled_request):
    assistant._start_request("run", "First controlled request")
    wait_for(entered.is_set)
    first_id = assistant._active_request
    first_worker = assistant._request_worker
    first_progress = calls[0][1]["progress"]
    first_executor = calls[0][1]["executor"]
    assert assistant.prompt_edit.isReadOnly()
    stage = assistant.stage_label.text()

    # This previously unlocked the prompt even though the AI worker was alive.
    assistant._finish_quick_external("BLAST", "fixture failure", error=True)
    assert assistant.prompt_edit.isReadOnly(), "Quick error unlocked the running AI request"
    assert assistant.stage_label.text() == stage
    assistant._start_request("run", "Must not start a duplicate")
    assert assistant._request_worker is first_worker and len(calls) == 1
    assert "BLAST failed: fixture failure" in assistant.terminal_edit.toPlainText()

    release.set()
    wait_for(lambda: not assistant.prompt_edit.isReadOnly())
    first_worker.join(1)
    assert not first_worker.is_alive() and assistant._active_request is None
    assert commands_applied == ["First controlled request"]
    assert "Fixture response ready" in assistant.terminal_edit.toPlainText()

    # Delayed completion and progress from request 1 must not finish request 2.
    entered.clear()
    release.clear()
    assistant._start_request("run", "Second controlled request")
    wait_for(entered.is_set)
    second_id = assistant._active_request
    second_worker = assistant._request_worker
    stage = assistant.stage_label.text()
    terminal = assistant.terminal_edit.toPlainText()
    assistant._finish_request(first_id, "stale reply")
    assistant._finish_request(first_id, "stale error", error=True)
    first_progress("stale progress")
    QTest.qWait(10)
    assert assistant._active_request is second_id and assistant.prompt_edit.isReadOnly()
    assert assistant.stage_label.text() == stage
    assert assistant.terminal_edit.toPlainText() == terminal
    try:
        first_executor("select clear")
    except RuntimeError as error:
        assert "no longer active" in str(error)
    else:
        raise AssertionError("Stale request executed a scene command")
    release.set()
    wait_for(lambda: assistant._active_request is None)
    second_worker.join(1)
    assert commands_applied == ["First controlled request", "Second controlled request"]

# Both thread allocation/start failures must release the request for retry.
for failure_patch in (
        patch.object(tool.threading, "Thread", side_effect=RuntimeError("fixture allocation failure")),
        patch.object(tool.threading.Thread, "start", side_effect=RuntimeError("fixture start failure"))):
    with failure_patch:
        assistant._start_request("run", "Failed start")
    assert assistant._active_request is None and assistant._request_worker is None
    assert not assistant.prompt_edit.isReadOnly()

# An error from the actual owning request releases the prompt normally.
with patch.object(tool, "run_mode_request", side_effect=RuntimeError("fixture backend failure")):
    assistant._start_request("ask", "Backend failure")
    wait_for(lambda: assistant._active_request is None)
assert not assistant.prompt_edit.isReadOnly()
assert "fixture backend failure" in assistant.terminal_edit.toPlainText()

# Stop cancels the owning request: its executor refuses scene commands, the
# worker finishes through the normal path, and the prompt unlocks as "Stopped".
entered.clear()
release.clear()


def cancellable_request(_session, prompt, **options):
    calls.append((prompt, options))
    entered.set()
    assert release.wait(5), "Test did not release cancellable worker"
    options["cancel_token"].raise_if_cancelled()
    return "must not finish"


with patch.object(tool, "run_mode_request", side_effect=cancellable_request):
    assistant._start_request("run", "Cancel me")
    wait_for(entered.is_set)
    assert not assistant.prompt_stop_button.isHidden(), "Stop button hidden during request"
    cancel_executor = calls[-1][1]["executor"]
    cancel_worker = assistant._request_worker
    assert assistant.cancel_request() is True
    assert not assistant.prompt_stop_button.isEnabled()
    assert assistant.cancel_request() is False, "Second Stop must be a no-op"
    assert assistant.prompt_edit.isReadOnly(), "Stop unlocked the prompt before the worker finished"
    try:
        cancel_executor("select clear")
    except service.RequestCancelled:
        pass
    else:
        raise AssertionError("Cancelled request executed a scene command")
    release.set()
    wait_for(lambda: assistant._active_request is None)
    cancel_worker.join(1)
    assert not cancel_worker.is_alive()
    assert assistant.stage_label.text() == "Stopped", assistant.stage_label.text()
    assert not assistant.prompt_edit.isReadOnly()
    assert assistant.prompt_stop_button.isHidden()
    assert assistant._request_cancel is None
    assert "must not finish" not in assistant.terminal_edit.toPlainText()
    assert commands_applied == ["First controlled request", "Second controlled request"]

# Real backend subprocess: Stop kills the CLI at once, a timeout kills it too,
# and neither leaves a process behind. A fake CLI stands in for Codex.
fake_dir = Path(tempfile.mkdtemp(prefix="codex-fake-cli-"))
pid_file = fake_dir / "pid"
fake_cli = fake_dir / "fake_codex.py"
fake_cli.write_text(
    "#!/usr/bin/env python3\nimport os, sys, time\n"
    f"open({str(pid_file)!r}, 'w').write(str(os.getpid()))\n"
    "sys.stdin.read()\ntime.sleep(30)\n", encoding="utf-8")
os.chmod(fake_cli, 0o755)


def alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def run_backend(token, environment=None):
    outcome = {}

    def worker():
        try:
            with patch.dict(os.environ, environment or {}), service.bind_cancel_token(token):
                outcome["reply"] = service._run_backend_exec(session, "prompt line\n" * 20000)
        except Exception as error:
            outcome["error"] = error

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return thread, outcome


with patch.object(service, "get_current_backend_id", return_value="codex"), \
        patch.object(service, "resolve_backend_cli", return_value=str(fake_cli)):
    token = service.CancelToken()
    thread, outcome = run_backend(token)
    wait_for(pid_file.is_file)
    pid = int(pid_file.read_text())
    token.cancel()
    thread.join(5)
    assert not thread.is_alive(), "Backend worker did not stop after cancel"
    assert isinstance(outcome.get("error"), service.RequestCancelled), outcome
    wait_for(lambda: not alive(pid))

    pid_file.unlink()
    token = service.CancelToken()
    thread, outcome = run_backend(token, {"CODEX_BRIDGE_BACKEND_TIMEOUT": "1"})
    wait_for(pid_file.is_file)
    pid = int(pid_file.read_text())
    thread.join(8)
    assert not thread.is_alive(), "Backend worker did not stop after timeout"
    assert isinstance(outcome.get("error"), service.CodexBridgeError) and "timed out" in str(outcome["error"]), outcome
    wait_for(lambda: not alive(pid))
    assert token._process is None

# AI-generated commands pass through the safety gate on every execution path.
ran = []
typed = service._execute_typed_action(
    session,
    {"type": "chimeraX_command_batch", "label": "x", "reason": "r", "verify": "v",
     "commands": ["runscript evil.py", "select clear"], "figure_mode": ""},
    executor=ran.append)
assert ran == [] and typed["error"] and "blocked" in typed["error"], typed
embedded = service._apply_embedded_chimerax_commands(
    session, "```chimerax\nopen payload.py\nselect clear\n```", executor=ran.append)
assert ran == [] and embedded is None, (ran, embedded)
from chimerax.codex_bridge import agent_tools
try:
    agent_tools._single_command("color red; pyscript x.py")
except ValueError as error:
    assert "rejected" in str(error)
else:
    raise AssertionError("agent tool accepted a blocked command")
assert agent_tools._single_command("color #1 red") == "color #1 red"

# JSON extraction survives prose that contains braces.
assert service._extract_json_text('Result: {"a": 1} (see {notes})') == '{"a": 1}'
assert service._extract_json_text('```json\n{"a": [1, 2]}\n```') == '{"a": [1, 2]}'
assert service._extract_json_text('{"a": 1}\nTrailing } brace') == '{"a": 1}'

# Deleting a panel retires its callbacks and executors even if work returns later.
entered.clear()
release.clear()
with patch.object(tool, "run_mode_request", side_effect=controlled_request):
    assistant._start_request("run", "Return after panel deletion")
    wait_for(entered.is_set)
    last_worker = assistant._request_worker
    last_progress = calls[-1][1]["progress"]
    last_executor = calls[-1][1]["executor"]
    QTest.qWait(10)
    assistant.delete()
    sip.delete(assistant.tool_window.ui_area)
    last_progress("late progress on deleted panel")
    try:
        last_executor("select clear")
    except RuntimeError:
        pass
    else:
        raise AssertionError("Deleted Assistant executed a scene command")
    release.set()
    last_worker.join(1)
    assert not last_worker.is_alive()
    QTest.qWait(200)
assert assistant._active_request is None
assert commands_applied == ["First controlled request", "Second controlled request"]
print("AI_REQUEST_LIFECYCLE_OK: exclusive requests, unrelated errors, stale callbacks, Stop/cancel, backend kill on cancel/timeout, command safety gate, startup/backend failures and panel deletion")
