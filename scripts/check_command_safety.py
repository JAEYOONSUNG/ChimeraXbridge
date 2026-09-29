"""Plain-Python check for the AI command safety gate (no ChimeraX needed)."""
import importlib.util
from pathlib import Path

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("command_safety", root / "src" / "command_safety.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
validate = module.validate_chimerax_command
Unsafe = module.UnsafeCommandError

ALLOWED = [
    "select /A:100-120",
    "color #1/A blue",
    "open 1abc",
    "open 4hhb format pdb",
    "save figure.png supersample 3",
    "hide #1 atoms; show #1 cartoons",
    "cartoon style #1 width 1.2",
    "label /A:100 text \"pipeline; ok\"",
    "perframe \"turn y 1\" frames 90",
    "view /A:100 pad 0.2",
    "select :pip",
    "measure buriedarea #1/A withAtoms2 #1/B",
]
BLOCKED = [
    "runscript evil.py",
    "pyscript hack.py",
    "open payload.py",
    "open payload.cxc",
    "open payload.PY",
    "open thing format python",
    "color red; runscript evil.py",
    "!rm -rf ~",
    "shell ls",
    "remotecontrol rest start",
    "toolshed install foo",
    "pip install foo",
    "alias select runscript evil.py",
    "exit",
    "quit",
    "perframe \"runscript evil.py\" frames 5",
    "save out.py",
    "select /A\nrunscript evil.py",
    "",
]

for command in ALLOWED:
    assert validate(command) == command.strip(), command
for command in BLOCKED:
    try:
        validate(command)
    except Unsafe:
        pass
    else:
        raise AssertionError(f"should have been blocked: {command!r}")

assert module.split_command_clauses("a; b \"x;y\"; c") == ["a", "b \"x;y\"", "c"]
assert module.is_safe_chimerax_command("turn y 90") and not module.is_safe_chimerax_command("quit")
print(f"COMMAND_SAFETY_OK {len(ALLOWED)} allowed, {len(BLOCKED)} blocked")
