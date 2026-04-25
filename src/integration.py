from contextlib import contextmanager

from .backends import ensure_session_preferences


def initialize_command_history(session):
    ensure_session_preferences(session)
    if hasattr(session, "_codex_bridge_command_history_handler"):
        return

    def _record(_trigger_name, cmd_text, ses=session):
        _append_command_history(ses, cmd_text)

    session._codex_bridge_command_history_handler = session.triggers.add_handler(
        "command finished",
        _record,
    )


def _append_command_history(session, cmd_text):
    ensure_session_preferences(session)
    command = str(cmd_text or "").strip()
    if not command:
        return
    if getattr(session, "_codex_bridge_command_batch_depth", 0) > 0:
        session._codex_bridge_command_batch_commands.append(command)
        return
    history = session._codex_bridge_command_history
    history.append(command)
    if len(history) > 40:
        del history[:-40]


@contextmanager
def command_batch(session, label):
    ensure_session_preferences(session)
    outermost = session._codex_bridge_command_batch_depth == 0
    session._codex_bridge_command_batch_depth += 1
    if outermost:
        session._codex_bridge_command_batch_commands = []
        session._codex_bridge_command_batch_label = str(label or "").strip() or "AI action"
    try:
        yield
    finally:
        session._codex_bridge_command_batch_depth = max(0, session._codex_bridge_command_batch_depth - 1)
        if session._codex_bridge_command_batch_depth == 0:
            commands = list(getattr(session, "_codex_bridge_command_batch_commands", []) or [])
            batch_label = getattr(session, "_codex_bridge_command_batch_label", None) or "AI action"
            session._codex_bridge_command_batch_commands = []
            session._codex_bridge_command_batch_label = None
            if commands:
                preview = " | ".join(commands[:3])
                if len(commands) > 3:
                    preview += f" | ... (+{len(commands) - 3})"
                summary = f"[{batch_label}] {preview}"
                history = session._codex_bridge_command_history
                history.append(summary)
                if len(history) > 40:
                    del history[:-40]


def initialize_command_line_integration(session):
    session._codex_bridge_nl_fallback = True
    if not session.ui.is_gui:
        return

    from chimerax.cmd_line.tool import CommandLine

    if getattr(CommandLine, "_codex_bridge_nl_patched", False):
        return

    CommandLine._codex_bridge_nl_patched = True
    CommandLine._codex_bridge_orig_execute = CommandLine.execute

    def execute_with_codex_fallback(self):
        from contextlib import contextmanager
        from html import escape

        from chimerax.core import errors
        from chimerax.core.commands import Command
        from chimerax.core.logger import error_text_format

        @contextmanager
        def processing_command(line_edit, cmd_text, command_worked, select_failed):
            line_edit.blockSignals(True)
            self._processing_command = True
            try:
                yield
            finally:
                line_edit.blockSignals(False)
                line_edit.setText(cmd_text)
                if command_worked[0] or select_failed:
                    line_edit.selectAll()
                self._processing_command = False

        session = self.session
        logger = session.logger
        text = self.text.lineEdit().text()
        logger.status("")

        for cmd_text in text.split("\n"):
            if not cmd_text:
                continue

            command_worked = [False]
            with processing_command(
                self.text.lineEdit(),
                cmd_text,
                command_worked,
                self.settings.select_failed,
            ):
                try:
                    self._just_typed_command = cmd_text
                    cmd = Command(session)
                    cmd.run(cmd_text)
                    command_worked[0] = True
                except SystemExit:
                    raise
                except errors.UserError as err:
                    err_text = str(err)
                    if _should_codex_fallback(session, cmd_text, err_text):
                        try:
                            from .cmd import ai

                            logger.info("[Codex] Natural-language fallback engaged.")
                            ai(session, cmd_text)
                            command_worked[0] = True
                            continue
                        except errors.UserError as ai_err:
                            err_text = str(ai_err)
                    logger.status(err_text, color="crimson")
                    msg = error_text_format(escape(err_text)).replace("\n", "<br>")
                    logger.info(msg, is_html=True)
                except BaseException:
                    raise
        self.set_focus()

    CommandLine.execute = execute_with_codex_fallback


def _should_codex_fallback(session, cmd_text, err_text):
    if not getattr(session, "_codex_bridge_nl_fallback", False):
        return False
    if not err_text.startswith("Unknown command:"):
        return False
    stripped = cmd_text.strip()
    if not stripped:
        return False
    if stripped.startswith(("ai ", "codex ")):
        return False
    if " " not in stripped:
        return False
    return True
