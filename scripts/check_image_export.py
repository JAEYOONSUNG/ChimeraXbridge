"""Exercise real OpenGL exports in a disposable ChimeraX GUI session.

Run with --notools --exit --script via a path without spaces.
The script writes only temporary files and suppresses preference writes.
"""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile

import numpy as np
from PIL import Image
from chimerax.atomic import AtomicStructure
from chimerax.core.commands import run
from chimerax.core.configfile import ConfigFile
from chimerax.std_commands.view import NamedView, _named_views

ConfigFile.save = lambda *args, **kwargs: None
root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "chimerax.codex_bridge.image_export", root / "src/image_export.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
export_image = module.export_image

model = AtomicStructure(session, name="image export test")
residue = model.new_residue("ALA", "A", 1)
for index in range(3):
    atom = model.new_atom("C%d" % index, "C")
    atom.coord = (index * 2, 0, 0)
    atom.color = (30, 110, 190, 255)
    atom.draw_mode = atom.SPHERE_STYLE
    residue.add_atom(atom)
session.models.add([model])
model.atoms[0].selected = True
session.main_view.background_color = (0.13, 0.18, 0.23, 1)
run(session, "view")
session.update_loop.update_graphics_now()
view = session.main_view
assert view.render is not None, "This check requires a real OpenGL renderer."
native = NamedView(view, view.center_of_rotation, session.models.list())
_named_views(session).views["Existing image-export bookmark"] = native


def scene():
    return (model.atoms.colors.tobytes(), model.atoms.selected.tobytes(),
            model.atoms.displays.tobytes(), model.position.matrix.tobytes(),
            view.camera.position.matrix.tobytes(), tuple(view.background_color),
            view.lighting.ambient_light_intensity,
            tuple(view.camera._pixel_shift), tuple(view.render.framebuffer_stack),
            tuple(_named_views(session).views.items()))


before = scene()
reports = []
with tempfile.TemporaryDirectory(prefix="chimerax-image-export-check-") as folder:
    folder = Path(folder)
    for format, suffix, width, height, dpi, transparent in (
        ("PNG", ".png", 320, 240, 300, True),
        ("JPEG", ".jpeg", 400, 300, 150, False),
        ("TIFF", ".tiff", 256, 256, 600, True),
        ("PNG", "", 240, 160, 96, False),
    ):
        result = export_image(session, folder / (format + suffix), format=format,
                              width=width, height=height, dpi=dpi,
                              transparent=transparent)
        with Image.open(result["path"]) as image:
            image.load()
            assert image.format == format, (image.format, format)
            assert image.size == (width, height), image.size
            assert all(abs(value - dpi) < 0.02 for value in image.info["dpi"]), image.info
            pixels = np.array(image)
            assert len(np.unique(pixels.reshape(-1, pixels.shape[-1]), axis=0)) > 20, \
                "Export contains no rendered structure."
            if transparent:
                assert image.mode == "RGBA", image.mode
                assert pixels[0, 0, 3] == 0, "PNG/TIFF background is not transparent."
                assert pixels[:, :, 3].max() == 255, "Structure is not opaque."
            else:
                assert image.mode == "RGB", image.mode
            reports.append({**result, "path": Path(result["path"]).name,
                            "actual_dpi": [float(value) for value in image.info["dpi"]],
                            "mode": image.mode})
        assert scene() == before, "%s export changed the scene." % format

    # Invalid choices fail before rendering or replacing another file.
    target = folder / "unchanged.png"
    target.write_bytes(b"existing image bytes")
    defaults = dict(format="PNG", width=100, height=100, dpi=300)
    for changes in ({"width": 0}, {"height": 20000}, {"dpi": 0},
                    {"dpi": 300.5}, {"width": 6000, "height": 6000},
                    {"format": "JPEG"}, {"format": "BMP"}):
        try:
            export_image(session, target, **dict(defaults, **changes))
        except Exception:
            pass
        else:
            raise AssertionError("Invalid export accepted: %r" % changes)
        assert target.read_bytes() == b"existing image bytes"
        assert scene() == before
    try:
        export_image(session, folder / "transparent.jpg", format="JPEG", width=100,
                     height=100, dpi=300, transparent=True)
    except Exception as error:
        assert "transparency" in str(error), error
    else:
        raise AssertionError("Transparent JPEG was accepted.")

    # A draw exception happens after native image_rgba pushes its temporary
    # framebuffer and changes the camera's subpixel shift.
    draw = view.draw
    def fail_draw(*args, **kwargs):
        raise RuntimeError("synthetic render failure")
    view.draw = fail_draw
    try:
        export_image(session, target, **defaults, transparent=True)
    except RuntimeError as error:
        assert "synthetic render failure" in str(error)
    else:
        raise AssertionError("Expected a rendering failure.")
    finally:
        view.draw = draw
    assert scene() == before, "Failed rendering changed scene/render state."
    assert not hasattr(view.render, "image_save")
    assert target.read_bytes() == b"existing image bytes"

    save = Image.Image.save
    def fail_save(image, path, *args, **kwargs):
        Path(path).write_bytes(b"partial export")
        raise OSError("synthetic encode failure")
    Image.Image.save = fail_save
    try:
        export_image(session, target, **defaults)
    except Exception as error:
        assert "synthetic encode failure" in str(error)
    else:
        raise AssertionError("Expected an encoding failure.")
    finally:
        Image.Image.save = save
    assert target.read_bytes() == b"existing image bytes", "Prior file was truncated."
    assert not list(folder.glob(".chimerax-export-*")), "Temporary output leaked."
    assert scene() == before
    # Real rendering and encoding still work after both failure cases.
    export_image(session, target, **defaults)
    with Image.open(target) as image:
        assert image.size == (100, 100)
    assert scene() == before

report = {"ok": True, "real_opengl": True, "exports": reports,
          "pixel_dimensions_and_dpi": True, "transparency": True,
          "scene_and_bookmarks_preserved": True, "render_failure_recovery": True,
          "atomic_file_replacement": True, "validation": True}
Path("/tmp/chimerax-image-export-check.json").write_text(json.dumps(report, indent=2))
print("IMAGE_EXPORT_OK", json.dumps(report), flush=True)
