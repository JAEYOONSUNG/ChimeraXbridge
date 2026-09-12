"""Use new installation defaults without changing an existing user's choices."""


_SECTION = "codex_bridge_install_defaults"


def preserve_legacy_defaults(settings, previous_defaults):
    """Record an old file's implicit values once, before its defaults change.

    ChimeraX omits values equal to a setting's default when saving. Therefore
    an old file without ``aa_charge`` or ``transparent`` still records a choice
    through the old default. A separate INI section distinguishes those files
    from files written after this migration. It also survives Settings.reset().

    For a fresh installation the marker is only added in memory. Its first
    ordinary settings save writes the marker together with the user's changes;
    opening a tool with fresh defaults does not require a preferences write.
    """
    config = getattr(settings, "_config", None)
    if config is None or config.has_section(_SECTION):
        # --safemode/disabled custom configuration may have no INI object.
        # A marker from a future release must also survive a downgrade.
        return
    from chimerax.core.configfile import ConfigFile
    existed = settings.on_disk()
    config.add_section(_SECTION)
    config.set(_SECTION, "version", "1")
    if not existed:
        return
    for name, value in previous_defaults.items():
        if name not in config["DEFAULT"]:
            # Initialization has no external Settings trigger subscribers yet.
            # Stage all old implicit values before the single atomic INI save.
            ConfigFile.__setattr__(settings, name, value, call_save=False)
            settings._cur_settings[name] = value
    try:
        ConfigFile.save(settings)
    except OSError as error:
        # A read-only preferences directory must not prevent the tool opening
        # or make the old implicit choices silently fall back to new defaults.
        settings._session.logger.warning(
            "Could not record the workspace-defaults upgrade; existing "
            f"preferences are preserved for this session: {error}")
