"""Reload only Display Controls without rebuilding the other helper panels."""
import importlib.util
import sys
import shutil
from pathlib import Path
source = Path(__file__).resolve().parents[1] / "src" / "display_controls.py"
name = "chimerax.codex_bridge.display_controls"
theme_name = "chimerax.codex_bridge.ui_theme"
theme_spec = importlib.util.spec_from_file_location(theme_name, source.with_name("ui_theme.py"))
theme_module = importlib.util.module_from_spec(theme_spec)
sys.modules[theme_name] = theme_module
theme_spec.loader.exec_module(theme_module)
for tool in list(session.tools.list()):
    if tool.tool_name == "Display Controls":
        tool.delete()
for target in (Path.home() / "Library/Application Support/ChimeraX").glob("*/lib/python/site-packages/chimerax/codex_bridge/display_controls.py"):
    shutil.copy2(source, target)
    shutil.copy2(source.with_name("ui_theme.py"), target.with_name("ui_theme.py"))
    shutil.copy2(source.with_name("panel_scroll.py"), target.with_name("panel_scroll.py"))
    shutil.copy2(source.with_name("color_palette.py"), target.with_name("color_palette.py"))
    shutil.copy2(source.with_name("display_color.py"), target.with_name("display_color.py"))
    shutil.copy2(source.with_name("compound_selection.py"), target.with_name("compound_selection.py"))
for dependency in ("color_palette", "display_color", "compound_selection"):
    dependency_name = "chimerax.codex_bridge." + dependency
    dependency_spec = importlib.util.spec_from_file_location(dependency_name, source.with_name(dependency + ".py"))
    dependency_module = importlib.util.module_from_spec(dependency_spec)
    sys.modules[dependency_name] = dependency_module
    dependency_spec.loader.exec_module(dependency_module)
old_hook = getattr(session, "_codex_bridge_chem_restore_handler", None)
if old_hook is not None:
    old_hook.remove()
    delattr(session, "_codex_bridge_chem_restore_handler")
sys.modules["chimerax.codex_bridge.display_color"].install_auto_chemistry_restore(session)
spec = importlib.util.spec_from_file_location(name, source)
module = importlib.util.module_from_spec(spec)
sys.modules[name] = module
spec.loader.exec_module(module)
tool = module.CodexDisplayControls.get_singleton(session)
tool.display(True)
tool.widget.refresh()
session.logger.info("Display Controls updated: compound selection toggle, color palette, screen eyedropper and Copy HEX.")
