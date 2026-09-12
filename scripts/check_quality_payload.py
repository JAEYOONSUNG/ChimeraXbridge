"""Verify actual installed/built/source bytes, syntax and toolbar resources.

This is a local installation check; uncommitted work is intentionally allowed.
No app, GUI, renderer, network, or git mutation is performed.
"""
import ast
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
from xml.etree import ElementTree
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'src'
INSTALLED = Path.home() / 'Library/Application Support/ChimeraX/1.10/lib/python/site-packages/chimerax/codex_bridge'
SUFFIXES = {'.py', '.svg', '.png', '.html', '.mjs', '.json'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--installed', type=Path,
        default=Path(os.environ.get('CHIMERAX_BRIDGE_INSTALLED', str(INSTALLED))),
        help='Installed chimerax/codex_bridge package directory')
    installed = parser.parse_args().installed
    version = ElementTree.parse(ROOT / 'bundle_info.xml').getroot().attrib['version']
    wheels = [path for path in (ROOT / 'dist').glob('*.whl')
              if f'-{version}-' in path.name]
    if len(wheels) != 1:
        raise AssertionError(f'Expected exactly one wheel for version {version}; found {len(wheels)}')
    errors, payload = [], []
    with ZipFile(wheels[0]) as archive:
        names = set(archive.namelist())
        for path in sorted(SOURCE.rglob('*')):
            if not path.is_file() or '__pycache__' in path.parts or path.suffix not in SUFFIXES:
                continue
            rel = path.relative_to(SOURCE)
            data = path.read_bytes()
            name = 'chimerax/codex_bridge/' + rel.as_posix()
            if name not in names or archive.read(name) != data:
                errors.append(f'wheel differs: {rel}')
            destination = installed / rel
            if not destination.is_file() or destination.read_bytes() != data:
                errors.append(f'installed differs: {rel}')
            payload.append({'path': rel.as_posix(), 'sha256': hashlib.sha256(data).hexdigest()})
    syntax_count = 0
    for base in (SOURCE, ROOT / 'scripts'):
        for path in base.rglob('*.py'):
            if '__pycache__' in path.parts:
                continue
            ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
            syntax_count += 1
    xml = ElementTree.parse(ROOT / 'bundle_info.xml')
    icons = set()
    for element in xml.iter():
        icon = element.attrib.get('icon')
        if icon:
            icons.add(icon)
    tree = ast.parse((SOURCE / '__init__.py').read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for keyword in node.keywords:
                if keyword.arg == 'icon' and isinstance(keyword.value, ast.Constant):
                    icons.add(keyword.value.value)
    for icon in icons:
        if not (SOURCE / 'icons' / icon).is_file():
            errors.append(f'missing icon: {icon}')
    if errors:
        raise AssertionError('\n'.join(errors))
    report = {'ok': True, 'payload_count': len(payload), 'syntax_count': syntax_count,
              'icon_count': len(icons), 'payload': payload}
    output = Path(tempfile.gettempdir()) / 'quality-payload-report.json'
    output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(f'QUALITY_PAYLOAD_OK {len(payload)} matching payload files; {syntax_count} Python files; {len(icons)} icons')


if __name__ == '__main__':
    main()
