"""Single validation gate for ChimeraX commands that originate from an AI reply.

Every path that executes model-generated text (typed action batches, embedded
commands extracted from prose, and OpenAI tool calls) must pass the command
through :func:`validate_chimerax_command` first. Commands typed by the user or
produced by built-in actions are trusted and are not routed through here.
"""
from __future__ import annotations

import re
import shlex


class UnsafeCommandError(ValueError):
    """Raised when an AI-generated command could run code or leave ChimeraX."""


# Verbs that execute arbitrary code, change installed software, or terminate
# the application. Matched against the first word of each `;`-separated clause.
BLOCKED_VERBS = {
    "runscript": "runs an external script",
    "pyscript": "runs Python code",
    "shell": "runs a shell command",
    "sh": "runs a shell command",
    "remotecontrol": "opens a remote control channel",
    "toolshed": "installs or removes bundles",
    "pip": "installs Python packages",
    "alias": "redefines command names",
    "exit": "quits ChimeraX",
    "quit": "quits ChimeraX",
    "devel": "runs developer build commands",
}

# `open` (and `runscript`-like aliases) with these formats or suffixes executes
# code instead of loading data.
SCRIPT_SUFFIXES = (".py", ".pyc", ".pyw", ".cxc", ".cmd", ".com")
SCRIPT_FORMATS = {"python", "py", "cmd", "chimerax-command", "cxc"}

# Verbs whose remaining text is itself a command line that must be validated.
NESTED_COMMAND_VERBS = {"perframe", "cd"}

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")


def split_command_clauses(command):
    """Split on `;` the way ChimeraX does, ignoring `;` inside quotes."""
    clauses = []
    current = []
    quote = None
    for char in str(command):
        if quote:
            current.append(char)
            if char == quote:
                quote = None
        elif char in ("'", '"'):
            quote = char
            current.append(char)
        elif char == ";":
            clauses.append("".join(current))
            current = []
        else:
            current.append(char)
    clauses.append("".join(current))
    return [clause.strip() for clause in clauses if clause.strip()]


def _tokens(clause):
    try:
        return shlex.split(clause, posix=True)
    except ValueError:
        return clause.split()


def _check_clause(clause, *, depth=0):
    if depth > 3:
        raise UnsafeCommandError("Command nesting is too deep to validate.")
    stripped = clause.strip()
    if not stripped:
        return
    if stripped.startswith("!"):
        raise UnsafeCommandError("Shell escapes (`!`) are not allowed from AI replies.")
    match = _WORD.match(stripped)
    verb = match.group(0).lower() if match else stripped.split()[0].lower()
    if verb in BLOCKED_VERBS:
        raise UnsafeCommandError(f"`{verb}` is blocked: it {BLOCKED_VERBS[verb]}.")

    tokens = _tokens(stripped)
    lowered = [token.lower() for token in tokens]

    if verb == "open":
        for index, token in enumerate(lowered[1:], start=1):
            if token.endswith(SCRIPT_SUFFIXES):
                raise UnsafeCommandError(f"`open {tokens[index]}` would execute a script file.")
            if token == "format" and index + 1 < len(lowered) and lowered[index + 1] in SCRIPT_FORMATS:
                raise UnsafeCommandError(f"`open ... format {tokens[index + 1]}` would execute code.")
    elif verb in NESTED_COMMAND_VERBS:
        rest = stripped[len(verb):].strip()
        if verb == "perframe":
            # perframe "<command>" [interval N] ... ; validate the quoted command.
            inner = _tokens(rest)
            if inner:
                _check_clause(inner[0], depth=depth + 1)
    elif verb == "save":
        for token in lowered[1:]:
            if token.endswith(SCRIPT_SUFFIXES):
                raise UnsafeCommandError(f"`save {token}` would write an executable script.")


def validate_chimerax_command(command):
    """Return the stripped command, or raise :class:`UnsafeCommandError`.

    A single logical command line is expected; embedded newlines are rejected
    so a reply cannot smuggle a second command past the caller's bookkeeping.
    """
    text = str(command or "").strip()
    if not text:
        raise UnsafeCommandError("Empty command.")
    if "\n" in text or "\r" in text:
        raise UnsafeCommandError("Only one command line may be run per command.")
    for clause in split_command_clauses(text):
        _check_clause(clause)
    return text


def is_safe_chimerax_command(command):
    try:
        validate_chimerax_command(command)
    except UnsafeCommandError:
        return False
    return True
