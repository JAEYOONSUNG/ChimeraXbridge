"""Render Original vectors and measure real toolbar pixels in a disposable session.

ChimeraX --nogui --notools --exit --script scripts/check_original_vectors.py
Writes contact sheets, helper previews and a JSON report under /tmp.
Append --alignment-only to check toolbar pixels before all vector files exist.
"""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
from tempfile import NamedTemporaryFile
import xml.etree.ElementTree as ET

import numpy as np
from Qt.QtCore import QRectF, Qt
from Qt.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPalette, QPixmap
from Qt.QtWidgets import QApplication, QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget
from PyQt6.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path('/tmp')
SVG_NS = 'http://www.w3.org/2000/svg'
SIZES = (28, 32, 256, 1024)
DARK = '#303234'
LIGHT = '#F4F6F8'


def source_package():
    for name in list(sys.modules):
        if name == 'chimerax.codex_bridge' or name.startswith('chimerax.codex_bridge.'):
            del sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        'chimerax.codex_bridge', ROOT / 'src/__init__.py',
        submodule_search_locations=[str(ROOT / 'src')])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)


def save_image(image, name):
    path = OUTPUT / name
    with NamedTemporaryFile(dir=OUTPUT, prefix='.' + path.stem, suffix='.png', delete=False) as tmp:
        temporary = Path(tmp.name)
    try:
        assert image.save(str(temporary), 'PNG'), f'Could not save {name}'
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return str(path)


def pixels(image):
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    raw = rgba.constBits().asstring(rgba.sizeInBytes())
    rows = np.frombuffer(raw, dtype=np.uint8).reshape(rgba.height(), rgba.bytesPerLine())
    return rows[:, :rgba.width() * 4].reshape(rgba.height(), rgba.width(), 4).copy()


def bounds(mask, context):
    ys, xs = np.nonzero(mask)
    assert len(xs), f'{context}: empty pixel mask'
    return (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))


def validate_xml(path):
    raw = path.read_bytes()
    assert b'<!DOCTYPE' not in raw.upper() and b'<!ENTITY' not in raw.upper(), path.name
    svg = ET.fromstring(raw)
    assert svg.tag == '{' + SVG_NS + '}svg', path.name
    assert svg.get('viewBox', '').split() == ['0', '0', '128', '128'], path.name
    assert svg.get('width') == '128' and svg.get('height') == '128', path.name
    for tag in ('title', 'desc'):
        node = svg.find('{' + SVG_NS + '}' + tag)
        assert node is not None and ''.join(node.itertext()).strip(), (path.name, tag)
    identifiers = [node.get('id') for node in svg.iter() if node.get('id')]
    assert len(identifiers) == len(set(identifiers)), (path.name, 'duplicate IDs')
    allowed = {'svg', 'title', 'desc', 'defs', 'g', 'path', 'rect', 'circle', 'ellipse',
               'line', 'polyline', 'polygon', 'linearGradient', 'radialGradient', 'stop',
               'clipPath', 'use'}
    for node in svg.iter():
        tag = node.tag.rsplit('}', 1)[-1]
        assert node.tag == '{' + SVG_NS + '}' + tag and tag in allowed, (path.name, tag)
        for key, value in node.attrib.items():
            local_key = key.rsplit('}', 1)[-1].lower()
            assert not local_key.startswith('on') and 'font' not in local_key, (path.name, key)
            if local_key == 'href':
                assert value.startswith('#') and value[1:] in identifiers, (path.name, value)
            assert not re.search(r'(?:https?:|data:|file:|//)', value, re.I), (path.name, value)
            for reference in re.findall(r'url\((.*?)\)', value):
                reference = reference.strip(' \"\'')
                assert reference.startswith('#') and reference[1:] in identifiers, (path.name, value)
    renderer = QSvgRenderer(str(path))
    assert renderer.isValid(), f'{path.name}: QSvgRenderer rejected SVG'
    assert not renderer.animated(), f'{path.name}: unexpected animation'
    return renderer


def render_svg(renderer, size):
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    try:
        renderer.render(painter, QRectF(0, 0, size, size))
    finally:
        painter.end()
    return image


def inspect_render(image, name):
    rgba = pixels(image)
    size = image.width()
    alpha = rgba[:, :, 3]
    x0, y0, x1, y1 = bounds(alpha > 8, (name, size))
    assert not any(np.any(edge) for edge in (alpha[0], alpha[-1], alpha[:, 0], alpha[:, -1])), \
        (name, size, 'art touches viewport edge', (x0, y0, x1, y1))
    width, height = x1 - x0 + 1, y1 - y0 + 1
    assert min(width, height) >= size * .48, (name, size, 'undersized silhouette', (width, height))
    assert max(width, height) <= size * .98, (name, size, 'insufficient safe margin')
    coverage = float(np.count_nonzero(alpha > 8) / (size * size))
    assert .025 <= coverage <= .85, (name, size, 'unreadable or opaque-tile coverage', coverage)
    return {'bounds': [x0, y0, x1, y1], 'coverage': round(coverage, 4)}


def contact_sheet(entries, background, name):
    columns, cell_w, cell_h = 6, 128, 178
    rows = (len(entries) + columns - 1) // columns
    sheet = QImage(columns * cell_w, 32 + rows * cell_h, QImage.Format.Format_ARGB32_Premultiplied)
    sheet.fill(QColor(background))
    painter = QPainter(sheet)
    try:
        painter.setPen(QColor('#E7EDF2' if background == DARK else '#253544'))
        font = QFont()
        font.setPixelSize(11)
        painter.setFont(font)
        painter.drawText(QRectF(12, 4, sheet.width() - 24, 23), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         'Original vectors · 96 px preview / 28 px toolbar size')
        font.setPixelSize(9)
        painter.setFont(font)
        for index, (filename, renderer) in enumerate(entries):
            x = (index % columns) * cell_w
            y = 32 + (index // columns) * cell_h
            painter.drawImage(x + 16, y + 4, render_svg(renderer, 96))
            painter.drawImage(x + 50, y + 108, render_svg(renderer, 28))
            painter.drawText(QRectF(x + 4, y + 143, cell_w - 8, 29),
                             Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                             filename.removesuffix('.svg'))
    finally:
        painter.end()
    return save_image(sheet, name)


def render_widget(widget):
    widget.ensurePolished()
    if widget.layout() is not None:
        widget.layout().activate()
    widget.show()
    app.processEvents()
    image = QImage(widget.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    try:
        widget.render(painter)
    finally:
        painter.end()
    return image


def alignment_probe(style_button):
    # Orthogonal colors identify actual icon/text pixels without depending on
    # the implementation's newline padding or the geometry it claims to set.
    probe = QPixmap(28, 28)
    probe.fill(QColor('#FF00FF'))
    results = []
    images = []
    for title in ('Analyze', 'AF Complex'):
        button = QToolButton()
        button.setText(title)
        button.setIcon(QIcon(probe))
        style_button(button)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setStyleSheet(button.styleSheet() + 'QToolButton { color: #00FF40; background: #111111; }')
        image = render_widget(button)
        rgba = pixels(image)
        icon_mask = (rgba[:, :, 0] > 230) & (rgba[:, :, 1] < 25) & (rgba[:, :, 2] > 230)
        text_mask = (rgba[:, :, 0] < 75) & (rgba[:, :, 1] > 150) & (rgba[:, :, 2] < 120)
        icon_bounds = bounds(icon_mask, (title, 'icon'))
        text_bounds = bounds(text_mask, (title, 'text'))
        assert button.accessibleName() == title, (title, 'accessible name is not the logical label')
        assert text_bounds[1] > icon_bounds[3], (title, 'icon and text overlap')
        assert text_bounds[3] < image.height() - 1, (title, 'text clips the button edge')
        results.append({'title': title, 'height': button.height(), 'icon_bounds': icon_bounds,
                        'first_label_y': text_bounds[1], 'text_bounds': text_bounds})
        images.append(image)
        button.close()
    first, second = results
    assert first['height'] == second['height'], ('button heights differ', results)
    assert first['icon_bounds'][1::2] == second['icon_bounds'][1::2], ('icon vertical bounds differ', results)
    assert first['first_label_y'] == second['first_label_y'], ('first label pixel rows differ', results)
    image = QImage(sum(i.width() for i in images) + 12, max(i.height() for i in images) + 8,
                   QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(DARK))
    painter = QPainter(image)
    x = 4
    for sample in images:
        painter.drawImage(x, 4, sample)
        x += sample.width() + 4
    painter.end()
    path = save_image(image, 'original-icon-alignment-probe.png')
    return results, path


def helper_preview(style_button, icon_path, load_icon):
    previous_palette = app.palette()
    palette = QPalette(previous_palette)
    for role, color in ((QPalette.ColorRole.Window, DARK), (QPalette.ColorRole.Button, DARK),
                        (QPalette.ColorRole.Base, '#202426'), (QPalette.ColorRole.WindowText, '#E7EDF2'),
                        (QPalette.ColorRole.ButtonText, '#E7EDF2'), (QPalette.ColorRole.Text, '#E7EDF2')):
        palette.setColor(role, QColor(color))
    app.setPalette(palette)
    container = QWidget()
    container.setPalette(palette)
    container.setStyleSheet('QWidget { background: #303234; color: #E7EDF2; }')
    layout = QVBoxLayout(container)
    layout.setContentsMargins(12, 8, 12, 8)
    layout.setSpacing(8)
    rows = [
        ('Molecule Display · Helper', [('Molecule Display', 'Helper', title)
         for title in ('Display Ctrl', 'Sequence', 'Action Pad', 'Bookmarks', 'AI')]),
        ('Nucleotides · AI Tools', [('Nucleotides', 'AI Tools', title)
         for title in ('NucDock', 'AF Complex', 'Boltz', 'FoldDisco', 'FoldMason')]),
    ]
    resources = []
    for heading, keys in rows:
        paths = [icon_path(key, 'original') for key in keys]
        if not all(path.is_file() for path in paths):
            if keys[0][0] == 'Molecule Display':
                raise AssertionError('Missing helper icon resource')
            continue
        label = QLabel(heading, container)
        font = label.font()
        font.setPixelSize(11)
        label.setFont(font)
        layout.addWidget(label)
        row = QHBoxLayout()
        row.setSpacing(6)
        for key, path in zip(keys, paths):
            before_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            icon = load_icon(path)
            assert not icon.isNull(), str(path)
            assert np.any(pixels(icon.pixmap(28, 28).toImage())[:, :, 3]), str(path)
            if path.suffix.lower() == '.png':
                rendered = icon.pixmap(32, 32).toImage()
                x0, y0, x1, y1 = bounds(pixels(rendered)[:, :, 3] > 8, str(path))
                assert abs((x0 + x1) / 2 - (rendered.width() - 1) / 2) <= 1.5, (path.name, 'off-center x')
                assert abs((y0 + y1) / 2 - (rendered.height() - 1) / 2) <= 1.5, (path.name, 'off-center y')
                assert .65 <= max(x1 - x0 + 1, y1 - y0 + 1) / rendered.width() <= .94, \
                    (path.name, 'imported logo has unsuitable visible size')
            assert hashlib.sha256(path.read_bytes()).hexdigest() == before_hash, (path.name, 'source bytes changed')
            button = QToolButton(container)
            button.setText(key[2])
            button.setIcon(icon)
            style_button(button)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            row.addWidget(button)
            resources.append({'label': key[2], 'path': str(path), 'sha256': before_hash})
        row.addStretch(1)
        layout.addLayout(row)
    container.adjustSize()
    image = render_widget(container)
    path = save_image(image, 'original-helper-preview.png')
    container.close()
    app.setPalette(previous_palette)
    return path, resources


def main():
    source_package()
    from chimerax.codex_bridge.icon_theme import ORIGINAL_VECTOR_REPLACEMENTS, icon_path, _toolbar_icon
    from chimerax.codex_bridge.runtime_patches import _style_ai_button_widget
    if '--alignment-only' in sys.argv:
        alignment, probe_path = alignment_probe(_style_ai_button_widget)
        helper_path, _ = helper_preview(_style_ai_button_widget, icon_path, _toolbar_icon)
        print('ORIGINAL_ALIGNMENT_OK ' + json.dumps({'alignment': alignment, 'probe': probe_path, 'helper_preview': helper_path}))
        return
    (OUTPUT / 'original-vectors-report.json').unlink(missing_ok=True)
    filenames = sorted(set(ORIGINAL_VECTOR_REPLACEMENTS.values()))
    assert len(filenames) == 30, ('Expected 30 active vector replacements', len(filenames))
    directory = ROOT / 'src/icons/original/redrawn'
    missing = [name for name in filenames if not (directory / name).is_file()]
    if missing:
        print('ORIGINAL_VECTORS_BLOCKED: ' + ', '.join(missing))
        raise RuntimeError('Wait for all 30 redrawn vector assets before running this check')
    entries = []
    measurements = {}
    for name in filenames:
        renderer = validate_xml(directory / name)
        entries.append((name, renderer))
        measurements[name] = {str(size): inspect_render(render_svg(renderer, size), name) for size in SIZES}
    sheets = [contact_sheet(entries, DARK, 'original-vectors-dark.png'),
              contact_sheet(entries, LIGHT, 'original-vectors-light.png')]
    alignment, probe_path = alignment_probe(_style_ai_button_widget)
    helper_path, helper_resources = helper_preview(_style_ai_button_widget, icon_path, _toolbar_icon)
    report = {'ok': True, 'vector_assets': len(entries), 'render_sizes': SIZES,
              'render_count': len(entries) * len(SIZES), 'measurements': measurements,
              'alignment': alignment, 'contact_sheets': sheets, 'alignment_probe': probe_path,
              'helper_preview': helper_path, 'helper_resources': helper_resources}
    path = OUTPUT / 'original-vectors-report.json'
    with NamedTemporaryFile('w', encoding='utf-8', dir=OUTPUT, prefix='.' + path.stem, suffix='.json', delete=False) as tmp:
        json.dump(report, tmp, indent=2)
        temporary = Path(tmp.name)
    os.replace(temporary, path)
    print('ORIGINAL_VECTORS_OK ' + json.dumps({key: report[key] for key in
          ('vector_assets', 'render_count', 'render_sizes', 'alignment', 'contact_sheets', 'helper_preview')}))


app = QApplication.instance() or QApplication([])
main()
