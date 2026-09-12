"""Switch original / new toolbar artwork without rebuilding native widgets."""
from pathlib import Path
from chimerax.core.settings import Settings


class ToolbarIconSettings(Settings):
    AUTO_SAVE = {"style": "original"}


# (tab, section, label): (original resource, new SVG resource)
ICON_PAIRS = {
    ('AI', 'Quick', 'Analyze'): ('ai-analyze.svg', 'ai-analyze.svg'),
    ('AI', 'Quick', 'View'): ('ai-view.svg', 'ai-view.svg'),
    ('AI', 'Quick', 'Pocket'): ('ai-site.svg', 'ai-site.svg'),
    ('AI', 'Quick', 'Cavity'): ('ai-cavity.svg', 'ai-cavity.svg'),
    ('AI', 'Quick', 'Figure'): ('ai-figure.svg', 'ai-figure.svg'),
    ('AI', 'Quick', 'Zoom'): ('ai-zoom.svg', 'ai-zoom.svg'),
    ('AI', 'Sequence', 'Blast'): ('blast-logo.png', 'ai-blast.svg'),
    ('AI', 'Sequence', 'Profile'): ('uniprot-logo.png', 'ai-profile.svg'),
    ('AI', 'Sequence', 'HHpred'): ('hhpred-logo.svg', 'hhpred-logo.svg'),
    ('AI', 'Sequence', 'SignalP'): ('ai-signalp.png', 'ai-signalp.svg'),
    ('AI', 'Sequence', 'Consurf'): ('consurf-logo.png', 'consurf-logo.svg'),
    ('AI', 'Sequence', 'Conserve'): ('ai-conserve.png', 'ai-conserve.svg'),
    ('AI', 'Sequence', 'MLP'): ('ai-hydrophobicity.svg', 'ai-hydrophobicity.svg'),
    ('AI', 'Modeling', 'AlphaFold'): ('alphafold-logo.png', 'ai-alphafold.svg'),
    ('AI', 'Modeling', 'AF Complex'): ('ai-alphafold.svg', 'afcomplex-logo.svg'),
    ('AI', 'Modeling', 'NucDock'): ('hdock-logo.png', 'hdock-logo.svg'),
    ('AI', 'Modeling', 'Boltz'): ('boltz-logo.svg', 'boltz-logo.svg'),
    ('AI', 'Modeling', 'RAPiDock'): ('rapidock-logo.svg', 'rapidock-logo.svg'),
    ('AI', 'Modeling', 'HPEPDOCK'): ('hpepdock-logo.svg', 'hpepdock-logo.svg'),
    ('AI', 'Modeling', 'MD'): ('ai-md.svg', 'ai-md.svg'),
    ('AI', 'Modeling', 'PyRosetta'): ('ai-pyrosetta.svg', 'ai-pyrosetta.svg'),
    ('AI', 'Sites', 'Energy'): ('ai-energy.svg', 'ai-energy.svg'),
    ('AI', 'Sites', 'Catalytic'): ('ai-catalytic.svg', 'ai-catalytic.svg'),
    ('AI', 'Sites', 'Interface'): ('ai-interface.svg', 'ai-interface.svg'),
    ('AI', 'Sites', 'FoldDisco'): ('folddisco-logo.png', 'folddisco-logo.svg'),
    ('AI', 'Channels', 'CAVER'): ('caverweb-logo.svg', 'caverweb-logo.svg'),
    ('AI', 'Channels', 'Membrane'): ('ai-membrane.svg', 'ai-membrane.svg'),
    ('AI', 'Sites', 'PISA'): ('pisa-logo.svg', 'pisa-logo.svg'),
    ('AI', 'Sites', 'Metal'): ('ai-metal.svg', 'ai-metal.svg'),
    ('AI', 'Structure', 'Similar'): ('foldseek-logo.png', 'ai-similar.svg'),
    ('AI', 'Structure', 'FoldMason'): ('foldmason-logo.png', 'foldmason-logo.svg'),
    ('AI', 'Structure', 'DALI'): ('dali-logo.svg', 'dali-logo.svg'),
    ('AI', 'Structure', 'VAST'): ('vast-logo.svg', 'vast-logo.svg'),
    ('AI', 'Structure', 'PDBeFold'): ('pdbefold-logo.png', 'pdbefold-logo.svg'),
    ('AI', 'Structure', 'US-align'): ('usalign-logo.svg', 'usalign-logo.svg'),
    ('AI', 'Structure', 'RMSD'): ('ai-rmsd.svg', 'ai-rmsd.svg'),
    ('AI', 'Structure', 'StructMSA'): ('ai-sequence-bar.svg', 'ai-sequence-bar.svg'),
    ('Nucleotides', 'AI Tools', 'NucDock'): ('hdock-logo.png', 'hdock-logo.svg'),
    ('Nucleotides', 'AI Tools', 'AF Complex'): ('ai-alphafold.svg', 'afcomplex-logo.svg'),
    ('Nucleotides', 'AI Tools', 'Boltz'): ('boltz-logo.svg', 'boltz-logo.svg'),
    ('Nucleotides', 'AI Tools', 'FoldDisco'): ('folddisco-logo.png', 'folddisco-logo.svg'),
    ('Nucleotides', 'AI Tools', 'FoldMason'): ('foldmason-logo.png', 'foldmason-logo.svg'),
    ('Molecule Display', 'Helper', 'Display Ctrl'): ('display-controls.svg', 'display-controls.svg'),
    ('Molecule Display', 'Helper', 'Sequence'): ('ai-sequence-bar.svg', 'sequence-bar.svg'),
    ('Molecule Display', 'Helper', 'Action Pad'): ('ai-action-pad.svg', 'ai-action-pad.svg'),
    ('Molecule Display', 'Helper', 'Bookmarks'): ('ai-camera-bookmarks.svg', 'ai-camera-bookmarks.svg'),
    ('Molecule Display', 'Helper', 'AI'): ('ai-assistant.svg', 'ai-assistant.svg'),
}

# Imported logos keep their original bytes. Home-grown fallback artwork in
# Original mode uses a dedicated detailed vector set; the frozen archive and
# the alternate, minimal New SVG theme remain independently verifiable.
ORIGINAL_VECTOR_REPLACEMENTS = {
    'ai-action-pad.svg': 'ai-action-pad.svg',
    'ai-alphafold.svg': 'afcomplex-logo.svg',
    'ai-analyze.svg': 'ai-analyze.svg',
    'ai-assistant.svg': 'ai-assistant.svg',
    'ai-camera-bookmarks.svg': 'ai-camera-bookmarks.svg',
    'ai-catalytic.svg': 'ai-catalytic.svg',
    'ai-cavity.svg': 'ai-cavity.svg',
    'ai-conserve.png': 'ai-conserve.svg',
    'ai-energy.svg': 'ai-energy.svg',
    'ai-figure.svg': 'ai-figure.svg',
    'ai-hydrophobicity.svg': 'ai-hydrophobicity.svg',
    'ai-interface.svg': 'ai-interface.svg',
    'ai-md.svg': 'ai-md.svg',
    'ai-membrane.svg': 'ai-membrane.svg',
    'ai-metal.svg': 'ai-metal.svg',
    'ai-pyrosetta.svg': 'ai-pyrosetta.svg',
    'ai-rmsd.svg': 'ai-rmsd.svg',
    'ai-sequence-bar.svg': 'ai-sequence-bar.svg',
    'ai-signalp.png': 'ai-signalp.svg',
    'ai-site.svg': 'ai-site.svg',
    'ai-view.svg': 'ai-view.svg',
    'ai-zoom.svg': 'ai-zoom.svg',
    'boltz-logo.svg': 'boltz-logo.svg',
    'dali-logo.svg': 'dali-logo.svg',
    'display-controls.svg': 'display-controls.svg',
    'hpepdock-logo.svg': 'hpepdock-logo.svg',
    'pisa-logo.svg': 'pisa-logo.svg',
    'rapidock-logo.svg': 'rapidock-logo.svg',
    'usalign-logo.svg': 'usalign-logo.svg',
    'vast-logo.svg': 'vast-logo.svg',
}

_RASTER_ICON_CACHE = {}


def _toolbar_icon(path):
    """Use native SVG scaling and optically center imported raster logos.

    PNG files retain their original pixels and resolution. Only transparent
    canvas padding is normalized at display time, so small molecular logos
    occupy the same 104/128 artwork box as the detailed vectors.
    """
    from Qt.QtGui import QIcon, QImage, QPainter, QPixmap
    if path.suffix.lower() != ".png":
        return QIcon(str(path))
    stamp = path.stat()
    key = (str(path), stamp.st_mtime_ns, stamp.st_size)
    if key in _RASTER_ICON_CACHE:
        return _RASTER_ICON_CACHE[key]
    image = QImage(str(path))
    if image.isNull():
        return QIcon(str(path))
    import numpy as np
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    data = rgba.constBits().asstring(rgba.sizeInBytes())
    pixels = np.frombuffer(data, dtype=np.uint8).reshape(rgba.height(), rgba.bytesPerLine())
    alpha = pixels[:, :rgba.width() * 4].reshape(rgba.height(), rgba.width(), 4)[:, :, 3]
    rows, columns = np.nonzero(alpha > 8)
    if len(rows):
        x, y = int(columns.min()), int(rows.min())
        cropped = image.copy(x, y, int(columns.max()) - x + 1, int(rows.max()) - y + 1)
        side = max(1, round(max(cropped.width(), cropped.height()) * 128 / 104))
        canvas = QImage(side, side, QImage.Format.Format_ARGB32_Premultiplied)
        canvas.setColorSpace(image.colorSpace())
        canvas.fill(0)
        painter = QPainter(canvas)
        painter.drawImage((side - cropped.width()) // 2, (side - cropped.height()) // 2, cropped)
        painter.end()
        icon = QIcon(QPixmap.fromImage(canvas))
    else:
        icon = QIcon(str(path))
    _RASTER_ICON_CACHE[key] = icon
    return icon


def _settings(session):
    settings = getattr(session, "_codex_toolbar_icon_settings", None)
    if settings is None:
        settings = ToolbarIconSettings(session, "Codex Toolbar Icons")
        session._codex_toolbar_icon_settings = settings
    return settings


def icon_theme(session):
    value = getattr(session, "_codex_toolbar_icon_style", _settings(session).style)
    return value if value in ("original", "modern") else "original"


def icon_path(key, theme):
    original, modern = ICON_PAIRS[key]
    folder = Path(__file__).resolve().with_name("icons")
    if theme == "original":
        if original in ORIGINAL_VECTOR_REPLACEMENTS:
            return folder / "original" / "redrawn" / ORIGINAL_VECTOR_REPLACEMENTS[original]
        return folder / "original" / original
    return folder / modern


def toggle_icon_theme(session):
    return set_icon_theme(session, "modern" if icon_theme(session) == "original" else "original")


def set_icon_theme(session, theme, *, save=True):
    if theme not in ("original", "modern"):
        raise ValueError("Unknown toolbar icon style")
    session._codex_toolbar_icon_style = theme
    if save:
        _settings(session).style = theme
    from chimerax.toolbar.tool import get_toolbar_singleton
    toolbar = get_toolbar_singleton(session, create=False)
    if toolbar is not None:
        apply_icon_theme(session, toolbar.ttb)
    return theme


def _configure_toggle(action, theme):
    modern = theme == "modern"
    action.setProperty("codexIconThemeToggle", True)
    action.setCheckable(True)
    blocked = action.blockSignals(True)
    action.setChecked(modern)
    action.blockSignals(blocked)
    action.setText("New SVG\nicons" if modern else "Original\nicons")
    action.setToolTip(
        "New SVG icons active. Click to switch to original icons." if modern else
        "Original logos and detailed SVG icons active. Click to switch to new SVG icons.")


def style_section_theme(section, widget=None):
    """Also called for newly-created toolbar overflow widgets."""
    theme = getattr(section, "_codex_icon_theme", None)
    if theme is None:
        return
    from .runtime_patches import _style_ai_button_widget
    from Qt.QtWidgets import QToolButton
    for action in section._actions.get("Icons", {}).values():
        try:
            _configure_toggle(action, theme)
        except RuntimeError:
            continue  # An overflow widget can be deleted between updates.
    widgets = [widget] if widget is not None else section.createdWidgets()
    for container in widgets:
        for button in container.findChildren(QToolButton):
            action = button.defaultAction()
            if action is not None and (action.property("codexIconThemeToggle")
                                       or action.property("codexToolbarArtwork")):
                # QAction icon/text updates can reset the text-under-icon
                # layout. Reapply both label rows after every theme switch.
                _style_ai_button_widget(button)
        if container.layout() is not None:
            container.layout().invalidate()
        container.updateGeometry()


def apply_icon_theme(session, ttb):
    """Change existing QAction icons AND future overflow-button definitions."""
    from Qt.QtGui import QIcon, QPainter
    theme = icon_theme(session)
    for tab, sections in getattr(ttb, "_buttons", {}).items():
        if tab not in {"AI", "Molecule Display", "Nucleotides"}:
            continue
        for name, section in sections.items():
            if name == "__toolbar__" or not hasattr(section, "_actions"):
                continue
            section._codex_icon_theme = theme
            for info in section._buttons:
                key = (tab, name, " ".join(info.title.split()))
                if key not in ICON_PAIRS:
                    continue
                path = icon_path(key, theme)
                icon = _toolbar_icon(path)
                info.icon = icon
                if info.highlight_icon is not None:
                    pixmap = icon.pixmap(32, 32)
                    painter = QPainter(pixmap)
                    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationOver)
                    painter.fillRect(pixmap.rect(), section.highlight_color)
                    painter.end()
                    info.highlight_icon = QIcon(pixmap)
                    icon = info.highlight_icon
                for action in section._actions.get(info.title, {}).values():
                    try:
                        action.setProperty("codexToolbarArtwork", True)
                        action.setIcon(icon)
                    except RuntimeError:
                        continue
            style_section_theme(section)
