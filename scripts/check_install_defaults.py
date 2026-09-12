"""Real Settings regression for fresh installations and preserved old choices.

Only temporary preference directories are used. No Qt application, visible
window, live session, login configuration, or external process is controlled.
"""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

assert not session.ui.is_gui
assert os.environ.get("QT_QPA_PLATFORM") == "offscreen"

import chimerax
from chimerax.core.configfile import ConfigFile
from chimerax.core.settings import Settings

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "chimerax.codex_bridge", root / "src/__init__.py",
    submodule_search_locations=[str(root / "src")])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
from chimerax.codex_bridge.sequence_bar import SequenceColorSettings
from chimerax.codex_bridge.camera_bookmarks import ImageExportSettings, BookmarkSettings
from chimerax.codex_bridge.icon_theme import ToolbarIconSettings
from chimerax.codex_bridge.panel_layout import PanelLayoutSettings
from chimerax.codex_bridge.shared_profile import profile_info
from chimerax.codex_bridge.first_run_defaults import _SECTION


class LegacySequenceSettings(Settings):
    AUTO_SAVE = {"aa_charge": True, "nucleotides": True, "base_palette": "muted"}


class LegacyExportSettings(Settings):
    AUTO_SAVE = {"format": "PNG", "width": 0, "height": 0, "dpi": 300,
                 "lock_ratio": True, "transparent": False, "directory": ""}


def sequence(cls=SequenceColorSettings):
    return cls(session, "Codex Sequence Colors")


def export(cls=ImageExportSettings):
    return cls(session, "Codex Image Export")


old_dirs = chimerax.app_dirs_unversioned
old_save = ConfigFile.save
with tempfile.TemporaryDirectory(prefix="install-defaults-") as folder:
    temporary_root = Path(folder)
    writes = []

    def guarded_save(settings):
        filename = Path(settings.filename)
        assert filename.parent.is_relative_to(temporary_root), "A settings save escaped the test directory"
        assert filename.name in ("Codex Sequence Colors-1", "Codex Image Export-1")
        writes.append(str(filename.relative_to(temporary_root)))
        old_save(settings)

    ConfigFile.save = guarded_save

    def use_case(name):
        directory = temporary_root / name
        directory.mkdir()
        chimerax.app_dirs_unversioned = SimpleNamespace(user_config_dir=str(directory))
        return directory

    try:
        fresh = use_case("fresh")
        seq, image = sequence(), export()
        assert not seq.aa_charge and image.transparent
        assert not seq.on_disk() and not image.on_disk()
        assert not list(fresh.iterdir()), "Constructing fresh defaults required an unnecessary settings file"
        expected = profile_info()["settings"]
        for group, settings in (
                ("sequence", seq), ("image_export", image),
                ("toolbar", ToolbarIconSettings(session, "Codex Toolbar Icons")),
                ("panels", PanelLayoutSettings(session, "Codex Panel Layout")),
                ("bookmarks", BookmarkSettings(session, "Codex Bookmarks"))):
            assert all(getattr(settings, key) == value for key, value in expected[group].items()), group
        assert not writes
        # The first ordinary save must carry the marker even when these two
        # new defaults are omitted by ChimeraX's native serializer.
        seq.nucleotides = False
        image.width = 1200
        assert "aa_charge" not in seq._config["DEFAULT"]
        assert "transparent" not in image._config["DEFAULT"]
        assert _SECTION in Path(seq.filename).read_text(encoding="utf-8")
        assert _SECTION in Path(image.filename).read_text(encoding="utf-8")
        count = len(writes)
        for _ in range(3):
            assert sequence().aa_charge is False
            assert export().transparent is True
        assert len(writes) == count, "Fresh settings were repeatedly migrated"

        legacy = use_case("legacy_implicit")
        old_seq, old_image = sequence(LegacySequenceSettings), export(LegacyExportSettings)
        old_seq.nucleotides = False
        old_seq.base_palette = "monochrome"
        old_image.width = 1600
        old_image.height = 800
        old_image.dpi = 600
        old_image.lock_ratio = False
        recipient_directory = str(legacy / "recipient-exports")
        old_image.directory = recipient_directory
        assert "aa_charge" not in old_seq._config["DEFAULT"]
        assert "transparent" not in old_image._config["DEFAULT"]
        assert _SECTION not in old_seq._config and _SECTION not in old_image._config
        sentinel = legacy / "Unrelated preferences-1"
        sentinel.write_text("[DEFAULT]\nvalue = 'synthetic unrelated setting'\n", encoding="utf-8")
        sentinel_before = sentinel.read_bytes()
        count = len(writes)
        seq, image = sequence(), export()
        assert seq.aa_charge is True, "An old omitted AA-charge value was silently changed"
        assert image.transparent is False, "An old omitted transparency value was silently changed"
        assert seq.nucleotides is False and seq.base_palette == "monochrome"
        assert (image.width, image.height, image.dpi, image.lock_ratio) == (1600, 800, 600, False)
        assert image.directory == recipient_directory
        assert len(writes) == count + 2, "Each old settings file must migrate in one save"
        file_bytes = {path.name: path.read_bytes() for path in legacy.iterdir()}
        count = len(writes)
        for _ in range(3):
            assert sequence().aa_charge is True
            assert export().transparent is False
        assert len(writes) == count
        assert {path.name: path.read_bytes() for path in legacy.iterdir()} == file_bytes
        # Switching to the new defaults removes their INI keys. The marker
        # must prevent the old implicit defaults from returning next launch.
        seq.aa_charge = False
        image.transparent = True
        assert "aa_charge" not in seq._config["DEFAULT"]
        assert "transparent" not in image._config["DEFAULT"]
        seq, image = sequence(), export()
        assert seq.aa_charge is False and image.transparent is True
        seq.aa_charge = True
        image.transparent = False
        assert sequence().aa_charge is True and export().transparent is False
        seq.reset()
        image.reset()
        assert sequence().aa_charge is False and export().transparent is True
        assert sentinel.read_bytes() == sentinel_before

        use_case("legacy_explicit_new_defaults")
        old_seq, old_image = sequence(LegacySequenceSettings), export(LegacyExportSettings)
        old_seq.aa_charge = False
        old_image.transparent = True
        assert "aa_charge" in old_seq._config["DEFAULT"]
        assert "transparent" in old_image._config["DEFAULT"]
        assert sequence().aa_charge is False and export().transparent is True
        count = len(writes)
        assert sequence().aa_charge is False and export().transparent is True
        assert len(writes) == count

        explicit = use_case("legacy_explicit_old_defaults")
        (explicit / "Codex Sequence Colors-1").write_text(
            "[DEFAULT]\naa_charge = True\n", encoding="utf-8")
        (explicit / "Codex Image Export-1").write_text(
            "[DEFAULT]\ntransparent = False\n", encoding="utf-8")
        assert sequence().aa_charge is True and export().transparent is False

        future = use_case("future_marker")
        for filename in ("Codex Sequence Colors-1", "Codex Image Export-1"):
            (future / filename).write_text(
                "[DEFAULT]\n\n[" + _SECTION + "]\nversion = 2\n", encoding="utf-8")
        count = len(writes)
        assert sequence().aa_charge is False and export().transparent is True
        assert len(writes) == count, "Loading a future marker reran the old migration"

        read_only = use_case("read_only_legacy")
        for filename in ("Codex Sequence Colors-1", "Codex Image Export-1"):
            (read_only / filename).write_text("[DEFAULT]\n", encoding="utf-8")

        def denied_save(settings):
            assert Path(settings.filename).parent == read_only
            raise PermissionError("Synthetic read-only preferences directory")

        ConfigFile.save = denied_save
        assert sequence().aa_charge is True and export().transparent is False
        assert all(path.read_text(encoding="utf-8") == "[DEFAULT]\n" for path in read_only.iterdir())
        ConfigFile.save = guarded_save
        assert sequence().aa_charge is True and export().transparent is False
        assert all(_SECTION in path.read_text(encoding="utf-8") for path in read_only.iterdir())
        assert not session.ui.is_gui
    finally:
        ConfigFile.save = old_save
        chimerax.app_dirs_unversioned = old_dirs

print("INSTALL_DEFAULTS_OK: fresh profile defaults, legacy implicit/explicit choices, one-time migration and repeated saves")
