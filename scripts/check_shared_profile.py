"""Exercise portable profiles against real, isolated Settings and offscreen Qt.

Run through run_quality_check.py shared_profile. Every preferences write is
guarded to a temporary directory; no existing GUI process is contacted.
"""
import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

assert not session.ui.is_gui
assert os.environ.get("QT_QPA_PLATFORM") == "offscreen"

import chimerax
from chimerax.atomic import AtomicStructure
from chimerax.core.configfile import ConfigFile
from chimerax.core.commands import run
from chimerax.core.errors import UserError
from chimerax.geometry import translation
from chimerax.std_commands.view import NamedView, _named_views

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "chimerax.codex_bridge", root / "src/__init__.py",
    submodule_search_locations=[str(root / "src")])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
package._schedule_helper_dock_layout = lambda *args, **kwargs: None
from chimerax.codex_bridge import shared_profile as profile


def rejected(call):
    try:
        call()
    except ValueError:
        return
    raise AssertionError("Invalid profile input was accepted")


info = profile.profile_info()
assert info["settings"]["sequence"] == {
    "aa_charge": False, "nucleotides": True, "base_palette": "muted"}
assert info["settings"]["image_export"] == {
    "format": "PNG", "width": 0, "height": 0, "dpi": 300,
    "lock_ratio": True, "transparent": True}
assert info["settings"]["toolbar"]["style"] == "original"
assert info["settings"]["panels"]["mode"] == "tabs"
assert info["ui"] == {"sequence_visible": True, "all_chains": True}
serialized = json.dumps(info)
assert all(value not in serialized for value in ("/Users/", "directory", "token", "password", "cli_path"))
assert not hasattr(session, "_codex_sequence_color_settings"), "Inspection instantiated preferences"
mutated = profile.profile_info()
mutated["settings"]["sequence"]["aa_charge"] = True
assert profile.profile_info() == info, "Inspection leaked mutable shared state"
for name in ("../jaeyoon", "/tmp/jaeyoon", "", None, ["jaeyoon"], "unknown"):
    rejected(lambda name=name: profile.profile_info(name))

for mutate in (
        lambda d: d.update(token="dummy"),
        lambda d: d["settings"].update(backend={"key": "dummy"}),
        lambda d: d["settings"]["image_export"].update(directory="dummy"),
        lambda d: d["settings"]["bookmarks"].update(views=[]),
        lambda d: d["settings"]["toolbar"].update(style="../icons"),
        lambda d: d["settings"]["sequence"].update(aa_charge=1),
        lambda d: d["settings"]["sequence"].update(base_palette="rainbow"),
        lambda d: d["settings"]["image_export"].update(width=True),
        lambda d: d["settings"]["image_export"].update(width=100),
        lambda d: d["settings"]["image_export"].update(width=16000, height=16000),
        lambda d: d["settings"]["image_export"].update(format="JPEG"),
        lambda d: d["settings"]["image_export"].update(dpi=0),
        lambda d: d["settings"]["image_export"].update(dpi=2401),
        lambda d: d.update(schema_version=True),
        lambda d: d["ui"].update(sequence_visible=False),
        lambda d: d["settings"]["sequence"].pop("aa_charge")):
    invalid = copy.deepcopy(info)
    mutate(invalid)
    rejected(lambda invalid=invalid: profile._validate_profile(invalid))

from chimerax.codex_bridge.icon_theme import ToolbarIconSettings
from chimerax.codex_bridge.panel_layout import PanelLayoutSettings
from chimerax.codex_bridge.sequence_bar import SequenceColorSettings
from chimerax.codex_bridge.camera_bookmarks import BookmarkSettings, ImageExportSettings

classes = {
    "toolbar": (ToolbarIconSettings, "Codex Toolbar Icons"),
    "panels": (PanelLayoutSettings, "Codex Panel Layout"),
    "sequence": (SequenceColorSettings, "Codex Sequence Colors"),
    "image_export": (ImageExportSettings, "Codex Image Export"),
    "bookmarks": (BookmarkSettings, "Codex Bookmarks"),
}
old_dirs = chimerax.app_dirs_unversioned
old_save = ConfigFile.save
with tempfile.TemporaryDirectory(prefix="shared-profile-") as folder:
    config_dir = Path(folder)
    chimerax.app_dirs_unversioned = SimpleNamespace(user_config_dir=folder)
    writes = []

    def guarded_save(settings):
        filename = Path(settings.filename)
        assert filename.parent == config_dir, "A preferences write escaped the isolated directory"
        assert filename.name in {name + "-1" for _, name in classes.values()}
        writes.append(filename.name)
        old_save(settings)

    ConfigFile.save = guarded_save
    sentinel_file = config_dir / "Unrelated tool-1"
    sentinel_file.write_text("[DEFAULT]\ncredential = 'synthetic-test-value'\n", encoding="utf-8")
    sentinel_bytes = sentinel_file.read_bytes()
    private = SimpleNamespace(credential="synthetic-test-value", executable="recipient-choice")
    session._profile_test_unrelated = private
    model = AtomicStructure(session, name="Shared profile scene fixture")
    residue = model.new_residue("ALA", "A", 1)
    atom = model.new_atom("CA", "C")
    residue.add_atom(atom)
    atom.coord = (2, 3, 4)
    session.models.add([model])
    model.atoms.colors = (41, 82, 123, 180)
    model.atoms.selected = True
    model.position = translation((5, 6, 7))
    session.main_view.camera.position = translation((0, 0, 25))
    session.main_view.background_color = (0.15, 0.2, 0.3, 1)
    native = NamedView(session.main_view, (0, 0, 0), session.models.list())
    _named_views(session).views["Recipient view"] = native

    def scene():
        return (model.atoms.colors.tobytes(), model.atoms.selected.tobytes(),
                model.atoms.coords.tobytes(), model.atoms.displays.tobytes(),
                model.residues.ribbon_colors.tobytes(), model.position.matrix.tobytes(),
                session.main_view.camera.position.matrix.tobytes(),
                tuple(session.main_view.background_color), tuple(session.models.list()),
                session.main_view.lighting.ambient_light_intensity)

    try:
        package.bundle_api.register_command(None, SimpleNamespace(
            name="codex profile", synopsis="Inspect or apply portable workspace preferences"), session.logger)
        inspected = run(session, "codex profile")
        assert inspected == info and not writes
        assert list(config_dir.iterdir()) == [sentinel_file], "Inspect command wrote settings"
        try:
            run(session, "codex profile unknown apply true")
        except UserError:
            pass
        else:
            raise AssertionError("The command accepted an unknown profile")
        assert not writes
        objects = profile._settings_objects(session)
        initial = {
            "toolbar": {"style": "modern"},
            "panels": {"mode": "all"},
            "sequence": {"aa_charge": True, "nucleotides": False, "base_palette": "monochrome"},
            "image_export": {"format": "TIFF", "width": 2048, "height": 1024,
                             "dpi": 150, "lock_ratio": False, "transparent": False},
            "bookmarks": {key: not value for key, value in info["settings"]["bookmarks"].items()},
        }
        for group, values in initial.items():
            for key, value in values.items():
                setattr(objects[group], key, value)
        recipient_directory = str(config_dir / "recipient-exports")
        objects["image_export"].directory = recipient_directory
        before = scene()
        rejected(lambda: profile.apply_profile(session, refresh="yes"))
        rejected(lambda: profile.apply_profile(session, "unknown"))
        assert all(getattr(objects[group], key) == value
                   for group, values in initial.items() for key, value in values.items())
        result = run(session, "codex profile jaeyoon apply true")
        assert result["applied"] and not result["refreshed"]
        assert not session.tools.find_by_class(__import__(
            "chimerax.codex_bridge.sequence_bar", fromlist=["CodexSequenceBar"]).CodexSequenceBar)
        assert scene() == before
        assert _named_views(session).views == {"Recipient view": native}
        assert session._profile_test_unrelated is private
        assert vars(private) == {"credential": "synthetic-test-value", "executable": "recipient-choice"}
        assert sentinel_file.read_bytes() == sentinel_bytes
        assert objects["image_export"].directory == recipient_directory

        def persisted_matches():
            for group, (cls, name) in classes.items():
                reloaded = cls(session, name)
                for key, value in info["settings"][group].items():
                    assert getattr(reloaded, key) == value, (group, key, value)
                if group == "image_export":
                    assert reloaded.directory == recipient_directory

        persisted_matches()
        bytes_before = {path.name: path.read_bytes() for path in config_dir.iterdir()}
        write_count = len(writes)
        # refresh=True still must not construct GUI tools in a headless session.
        assert not profile.apply_profile(session)["refreshed"]
        assert len(writes) == write_count, "Reapplying identical preferences rewrote settings"
        assert {path.name: path.read_bytes() for path in config_dir.iterdir()} == bytes_before
        assert scene() == before

        # Exercise real existing controls with Qt's offscreen platform. There
        # is no visible window, native application activation, or live session.
        sys.path.insert(0, str(root / "scripts"))
        from headless_ui_fixture import install
        app = install(session)
        from Qt.QtWidgets import QWidget
        session.ui.main_window.main_view = QWidget(session.ui.main_window)
        from chimerax.codex_bridge import sequence_bar
        from chimerax.codex_bridge import camera_bookmarks
        bar = sequence_bar.CodexSequenceBar(session, "Sequence Bar")
        panel = camera_bookmarks.CameraBookmarks(session, "Camera Bookmarks")
        bar._set_all_chains_enabled(False)
        bar.charge_colors_button.setChecked(True)
        bar.base_colors_button.setChecked(False)
        bar.display(False)
        panel.name_input.setText("Keep recipient draft")
        panel.export_dpi.setValue(200)
        panel.export_format.setCurrentText("TIFF")
        for button in panel.include_buttons.values():
            button.setChecked(False)
        assert not panel.save_btn.isEnabled(), "Empty bookmark conditions must disable saving"
        profile.apply_profile(session, refresh=False)
        sequence_bar.CodexSequenceBar.get_singleton = classmethod(
            lambda cls, *args, **kwargs: bar)
        profile._refresh_sequence(session, info)
        profile._refresh_bookmarks(session, info)
        assert not bar.charge_colors_button.isChecked()
        assert bar.base_colors_button.isChecked()
        assert bar._base_palette == "muted" and bar._all_chains_enabled
        assert not bar.bar_widget.isHidden()
        assert panel.name_input.text() == "Keep recipient draft"
        assert panel.export_format.currentText() == "PNG"
        assert panel.export_dpi.value() == 300
        assert panel.export_transparent.isChecked() and panel.export_lock_ratio.isChecked()
        assert not panel.include_buttons["selection"].isChecked()
        assert panel.save_btn.isEnabled(), "Applying valid bookmark conditions must enable saving"
        assert (panel.export_width.value(), panel.export_height.value()) == panel._current_image_size()
        assert objects["image_export"].directory == recipient_directory
        assert _named_views(session).views == {"Recipient view": native}
        assert scene() == before
        persisted_matches()
        assert sentinel_file.read_bytes() == sentinel_bytes
        panel.delete()
        bar.delete()
        app.processEvents()
    finally:
        ConfigFile.save = old_save
        chimerax.app_dirs_unversioned = old_dirs

print("SHARED_PROFILE_OK: isolated persistence, idempotence, strict allowlist, existing controls and scene preservation")
