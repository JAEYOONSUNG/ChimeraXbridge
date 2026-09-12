"""Explicitly apply a bundled, portable allowlist of workspace preferences.

This is deliberately not a settings-file importer. Profiles contain no account
configuration, executable or output paths, molecular data, or saved views.
Inspecting a profile does not instantiate Settings or touch the session.
"""
from copy import deepcopy
import json
from pathlib import Path


_PROFILE_NAMES = ("jaeyoon",)
_GROUP_KEYS = {
    "toolbar": {"style"},
    "panels": {"mode"},
    "sequence": {"aa_charge", "nucleotides", "base_palette"},
    "image_export": {"format", "width", "height", "dpi", "lock_ratio", "transparent"},
    "bookmarks": {"camera", "visibility", "colors", "lighting", "selection"},
}


def _keys(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError(f"Invalid {label}: only the documented profile fields are allowed")


def _choice(value, choices, label):
    if not isinstance(value, str) or value not in choices:
        raise ValueError(f"Invalid {label}")


def _boolean(value, label):
    if type(value) is not bool:
        raise ValueError(f"{label} must be true or false")


def _validate_profile(data):
    _keys(data, {"schema_version", "name", "title", "description", "settings", "ui"}, "profile")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ValueError("Unsupported profile schema version")
    _choice(data["name"], _PROFILE_NAMES, "profile name")
    for field in ("title", "description"):
        if not isinstance(data[field], str) or not data[field].strip() or len(data[field]) > 500:
            raise ValueError(f"Invalid profile {field}")
    settings = data["settings"]
    _keys(settings, _GROUP_KEYS, "settings groups")
    for group, expected in _GROUP_KEYS.items():
        _keys(settings[group], expected, group)
    _choice(settings["toolbar"]["style"], ("original", "modern"), "toolbar style")
    _choice(settings["panels"]["mode"], ("tabs", "all"), "panel layout")
    sequence = settings["sequence"]
    _choice(sequence["base_palette"], ("muted", "purine_pyrimidine", "monochrome"), "nucleotide palette")
    for key in ("aa_charge", "nucleotides"):
        _boolean(sequence[key], key)
    export = settings["image_export"]
    _choice(export["format"], ("PNG", "JPEG", "TIFF"), "image format")
    for key in ("lock_ratio", "transparent"):
        _boolean(export[key], key)
    if export["format"] == "JPEG" and export["transparent"]:
        raise ValueError("JPEG does not support a transparent background")
    for key in ("width", "height"):
        value = export[key]
        if type(value) is not int or not (value == 0 or 16 <= value <= 16384):
            raise ValueError(f"Image {key} must be zero or 16–16384 pixels")
    if (export["width"] == 0) != (export["height"] == 0):
        raise ValueError("Current-viewport dimensions must both be zero")
    if export["width"] * export["height"] > 32_000_000:
        raise ValueError("Image dimensions exceed 32 million pixels")
    if type(export["dpi"]) is not int or not 1 <= export["dpi"] <= 2400:
        raise ValueError("Image DPI must be 1–2400")
    for key, value in settings["bookmarks"].items():
        _boolean(value, key)
    _keys(data["ui"], {"sequence_visible", "all_chains"}, "UI defaults")
    # These two are the bundle's startup defaults, not transferable dock state.
    for key, value in data["ui"].items():
        if value is not True:
            raise ValueError(f"This profile schema only supports {key}=true")
    return deepcopy(data)


def profile_info(name="jaeyoon"):
    """Return a fresh, validated JSON-safe description without applying it."""
    _choice(name, _PROFILE_NAMES, "profile name; choose jaeyoon")
    path = Path(__file__).with_name("profiles") / f"{name}.json"
    data = _validate_profile(json.loads(path.read_text(encoding="utf-8")))
    if data["name"] != name:
        raise ValueError("The bundled profile name does not match its filename")
    return data


def _settings_objects(session):
    from .icon_theme import _settings as toolbar_settings
    from .panel_layout import _settings as layout_settings
    from .sequence_bar import _sequence_color_settings
    from .camera_bookmarks import _bookmark_settings, _image_export_settings
    return {
        "toolbar": toolbar_settings(session),
        "panels": layout_settings(session),
        "sequence": _sequence_color_settings(session),
        "image_export": _image_export_settings(session),
        "bookmarks": _bookmark_settings(session),
    }


def _set_widget(widget, setter, value):
    blocked = widget.blockSignals(True)
    try:
        getattr(widget, setter)(value)
    finally:
        widget.blockSignals(blocked)


def _refresh_sequence(session, data):
    from .sequence_bar import CodexSequenceBar
    bar = CodexSequenceBar.get_singleton(session, create=True, display=False)
    if bar is None:
        return
    settings = data["settings"]["sequence"]
    _set_widget(bar.charge_colors_button, "setChecked", settings["aa_charge"])
    _set_widget(bar.base_colors_button, "setChecked", settings["nucleotides"])
    bar._base_palette = settings["base_palette"]
    for key, action in bar._base_palette_actions.items():
        _set_widget(action, "setChecked", key == bar._base_palette)
    bar._set_residue_coloring()
    bar._update_color_key()
    if not bar._all_chains_enabled:
        bar._set_all_chains_enabled(True)
    bar.display(True)


def _refresh_bookmarks(session, data):
    from .camera_bookmarks import CameraBookmarks
    # Preserve the current bookmark name, selection, scroll position and popup.
    # The profile need not create a new panel just to update its saved defaults.
    for panel in session.tools.find_by_class(CameraBookmarks):
        if getattr(panel, "_disposed", False):
            continue
        for key, value in data["settings"]["bookmarks"].items():
            _set_widget(panel.include_buttons[key], "setChecked", value)
        panel._save_options_changed()
        export = data["settings"]["image_export"]
        width, height = export["width"], export["height"]
        if width == 0:
            width, height = panel._current_image_size()
        for widget, setter, value in (
                (panel.export_format, "setCurrentText", export["format"]),
                (panel.export_width, "setValue", width),
                (panel.export_height, "setValue", height),
                (panel.export_dpi, "setValue", export["dpi"]),
                (panel.export_lock_ratio, "setChecked", export["lock_ratio"]),
                (panel.export_transparent, "setChecked", export["transparent"])):
            _set_widget(widget, setter, value)
        panel._export_aspect = panel.export_width.value() / panel.export_height.value()
        panel._export_options_changed()
    # Opening/editing export controls normally remembers concrete dimensions.
    # Keep the explicit profile's viewport default until that next user action.
    settings = getattr(session, "_codex_image_export_settings", None)
    if settings is not None:
        settings.width = data["settings"]["image_export"]["width"]
        settings.height = data["settings"]["image_export"]["height"]


def _refresh_ui(session, data):
    from .icon_theme import set_icon_theme
    from .panel_layout import set_panel_layout
    set_icon_theme(session, data["settings"]["toolbar"]["style"], save=False)
    set_panel_layout(session, data["settings"]["panels"]["mode"], save=False)
    _refresh_sequence(session, data)
    _refresh_bookmarks(session, data)


def apply_profile(session, name="jaeyoon", *, refresh=True):
    """Persist only the bundled UI allowlist, optionally updating visible tools.

    ``refresh=False`` (and every headless session) changes saved preferences
    only; it never constructs or displays tools. Existing account settings,
    output folders, saved views and molecular state are outside this API.
    """
    if type(refresh) is not bool:
        raise ValueError("refresh must be true or false")
    data = profile_info(name)  # Validate every field before instantiating Settings.
    objects = _settings_objects(session)
    previous = []
    try:
        for group, values in data["settings"].items():
            settings = objects[group]
            for key, value in values.items():
                previous.append((settings, key, getattr(settings, key)))
                setattr(settings, key, value)
    except Exception:
        # Restore the allowlist if a preferences write fails partway through.
        # Deliberately do not reset() or replace an entire settings object.
        for settings, key, value in reversed(previous):
            try:
                setattr(settings, key, value)
            except Exception:
                pass
        raise
    session._codex_toolbar_icon_style = data["settings"]["toolbar"]["style"]
    session._codex_panel_layout_mode = data["settings"]["panels"]["mode"]
    refreshed = bool(refresh and getattr(getattr(session, "ui", None), "is_gui", False))
    if refreshed:
        _refresh_ui(session, data)
    return {**data, "applied": True, "refreshed": refreshed}
