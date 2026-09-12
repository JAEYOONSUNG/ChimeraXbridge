#!/usr/bin/env python3
"""Validate the portable SVG toolbar family without ChimeraX or Qt."""

from pathlib import Path
import sys
import xml.etree.ElementTree as ET


REPO_ROOT = Path(__file__).resolve().parents[1]
SVG_NAMESPACE = "http://www.w3.org/2000/svg"
MAX_ASSET_BYTES = 4096
MAX_ELEMENTS = 40
PALETTE = {"#8B98AA", "#659ACB", "#54A69C", "#A18BC7", "#C49A55"}
ALLOWED_ELEMENTS = {
    "svg", "title", "g", "path", "rect", "circle", "ellipse", "line",
    "polyline", "polygon",
}
REQUIRED_ASSETS = {
    "afcomplex-logo.svg", "ai-action-pad.svg", "ai-alphafold.svg",
    "ai-analyze.svg", "ai-assistant.svg", "ai-blast.svg",
    "ai-camera-bookmarks.svg", "ai-catalytic.svg", "ai-cavity.svg",
    "ai-conserve.svg", "ai-energy.svg", "ai-figure.svg",
    "ai-hydrophobicity.svg", "ai-interface.svg", "ai-md.svg",
    "ai-membrane.svg", "ai-metal.svg", "ai-profile.svg", "ai-pyrosetta.svg",
    "ai-rmsd.svg", "ai-sequence-bar.svg", "ai-signalp.svg", "ai-similar.svg",
    "ai-site.svg", "ai-view.svg", "ai-zoom.svg", "boltz-logo.svg",
    "caverweb-logo.svg", "chevron-down.svg", "consurf-logo.svg",
    "dali-logo.svg", "display-controls.svg", "folddisco-logo.svg",
    "foldmason-logo.svg", "hdock-logo.svg", "hhpred-logo.svg",
    "hpepdock-logo.svg", "pdbefold-logo.svg", "pisa-logo.svg",
    "rapidock-logo.svg", "sequence-bar.svg", "usalign-logo.svg", "vast-logo.svg",
}


def check_assets(root=REPO_ROOT):
    """Return actionable errors; an empty list means every asset passed."""
    root = Path(root)
    errors = []
    folders = (root / "icons", root / "src" / "icons")
    file_sets = [{p.name for p in folder.glob("*.svg")} for folder in folders]
    for folder, names in zip(folders, file_sets):
        for name in sorted(REQUIRED_ASSETS - names):
            errors.append(f"{folder.relative_to(root)}/{name}: required SVG missing")
    if file_sets[0] != file_sets[1]:
        errors.append("icons/ and src/icons/ contain different SVG filenames")

    for name in sorted(file_sets[0] | file_sets[1]):
        copies = [folder / name for folder in folders]
        if all(path.is_file() for path in copies):
            if copies[0].read_bytes() != copies[1].read_bytes():
                errors.append(f"{name}: the packaged and source copies differ")

        # Validate both directories, including any unmatched or stale assets.
        for path in copies:
            if not path.is_file():
                continue
            label = str(path.relative_to(root))
            raw = path.read_bytes()
            if len(raw) > MAX_ASSET_BYTES:
                errors.append(f"{label}: exceeds {MAX_ASSET_BYTES} bytes")
            if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
                errors.append(f"{label}: SVG must be self-contained XML")
                continue
            try:
                svg = ET.fromstring(raw)
            except ET.ParseError as exc:
                errors.append(f"{label}: invalid XML: {exc}")
                continue
            if svg.tag != f"{{{SVG_NAMESPACE}}}svg":
                errors.append(f"{label}: invalid SVG root or namespace")
            if svg.get("viewBox", "").split() != ["0", "0", "32", "32"]:
                errors.append(f"{label}: expected viewBox 0 0 32 32")
            for key, value in {
                "width": "32", "height": "32", "fill": "none",
                "stroke": "#8B98AA", "stroke-width": "1.85",
                "stroke-linecap": "round", "stroke-linejoin": "round",
            }.items():
                if svg.get(key) != value:
                    errors.append(f"{label}: expected {key}={value}")
            elements = list(svg.iter())
            if len(elements) > MAX_ELEMENTS:
                errors.append(f"{label}: exceeds {MAX_ELEMENTS} vector elements")
            colors = set()
            for element in elements:
                tag = element.tag.rsplit("}", 1)[-1]
                if tag not in ALLOWED_ELEMENTS:
                    errors.append(f"{label}: forbidden element {tag}")
                if element.tag != f"{{{SVG_NAMESPACE}}}{tag}":
                    errors.append(f"{label}: unexpected element namespace")
                if tag != "title" and (element.text or "").strip():
                    errors.append(f"{label}: visible or non-vector text content")
                for key, value in element.attrib.items():
                    attr = key.rsplit("}", 1)[-1]
                    if attr in {"href", "filter", "style"} or attr.startswith("on"):
                        errors.append(f"{label}: forbidden attribute {attr}")
                    if "url(" in value.lower() or "data:" in value.lower():
                        errors.append(f"{label}: external or embedded asset reference")
                    if attr in {"fill", "stroke"} and value != "none":
                        colors.add(value)
                        if value not in PALETTE:
                            errors.append(f"{label}: color outside the toolbar palette: {value}")
            if len(colors - {"#8B98AA"}) > 1:
                errors.append(f"{label}: expected at most one accent color")
    return errors


def main():
    errors = check_assets()
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("SVG_ASSETS_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
