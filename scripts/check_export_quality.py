"""Headless export quality check with real Pillow files and supplied image data.

Run only with ChimeraX --nogui --notools --exit. No native window, graphics
context or existing user session is created or contacted. The production
exporter encodes deterministic PIL images supplied by a small view double;
this checks file output and failure recovery, not real OpenGL rendering.
"""
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PIL import Image
from chimerax.core.errors import LimitationError, UserError

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("quality_image_export", root / "src/image_export.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Framebuffer:
    def __init__(self, name):
        self.name = name
        self.deleted = False

    def delete(self):
        self.deleted = True


class Render:
    def __init__(self):
        self.framebuffer_stack = [Framebuffer("original framebuffer")]
        self.current_result = True
        self.maximum = module.MAX_DIMENSION
        self.make_current_calls = 0
        self.limit_calls = 0

    def make_current(self):
        self.make_current_calls += 1
        return self.current_result

    def max_framebuffer_size(self):
        self.limit_calls += 1
        return self.maximum

    def pop_framebuffer(self):
        return self.framebuffer_stack.pop()


class Camera:
    def __init__(self):
        self._pixel_shift = (0.125, -0.25)

    def set_fixed_pixel_shift(self, shift):
        self._pixel_shift = shift


class View:
    def __init__(self):
        self.camera = Camera()
        self.render = Render()
        self.calls = []
        self.failure = None
        self.returned_image = None
        self.redraw_needed = False
        self.background_color = (0.2, 0.3, 0.4, 1.0)
        self.test_framebuffers = []

    def image(self, width, height, *, supersample, transparent_background):
        self.calls.append((width, height, supersample, transparent_background))
        if self.failure == "render":
            framebuffer = Framebuffer("image capture")
            self.test_framebuffers.append(framebuffer)
            self.render.framebuffer_stack.append(framebuffer)
            self.render.image_save = True
            self.camera.set_fixed_pixel_shift((0.4, 0.4))
            raise RuntimeError("supplied renderer failed")
        if self.failure == "none":
            return None
        if self.failure == "size":
            width += 1
        y, x = np.indices((height, width))
        pixels = np.empty((height, width, 4 if transparent_background else 3), np.uint8)
        pixels[..., 0] = x % 256
        pixels[..., 1] = y % 256
        pixels[..., 2] = (x + y) % 256
        if transparent_background:
            pixels[..., 3] = np.where((x + y) % 3 == 0, 0, 255)
        self.returned_image = Image.fromarray(pixels)
        return self.returned_image


view = View()
updates = []
supplied_session = SimpleNamespace(
    main_view=view, in_script=False,
    update_loop=SimpleNamespace(update_graphics_now=lambda: updates.append(True)),
    bookmarks={"untouched": object()}, selected_atoms=["one atom"],
)


def scene():
    return (view.camera._pixel_shift, tuple(view.render.framebuffer_stack),
            view.background_color, tuple(supplied_session.bookmarks.items()),
            tuple(supplied_session.selected_atoms))


def assert_closed(image):
    try:
        image.getpixel((0, 0))
    except ValueError:
        return
    raise AssertionError("Export did not release its supplied image")


def rejected(path, *, expected=UserError, message=None, **changes):
    options = dict(format="PNG", width=37, height=29, dpi=300)
    options.update(changes)
    try:
        module.export_image(supplied_session, path, **options)
    except expected as error:
        if message:
            assert message in str(error), str(error)
    else:
        raise AssertionError("Invalid export accepted: %r" % options)


report = {"real_opengl": False, "real_pillow_files": True, "exports": []}
with tempfile.TemporaryDirectory(prefix="export-quality-") as temporary:
    folder = Path(temporary)
    before = scene()
    for index, (format_name, suffix, dpi, transparent) in enumerate((
        ("PNG", ".PNG", 1, True), ("JPEG", ".jpeg", 150, False),
        ("TIFF", ".tiff", 600, True), ("png", "", 96, False),
        ("JPG", ".jpg", 2400, False), ("TIF", ".tif", 300, False),
    )):
        width, height = 37 + index * 7, 29 + index * 3
        output = module.export_image(supplied_session, folder / (str(index) + suffix),
            format=format_name, width=width, height=height, dpi=dpi, transparent=transparent)
        assert view.calls[-1] == (width, height, 3, transparent)
        with Image.open(output["path"]) as decoded:
            decoded.load()
            assert decoded.format == output["format"]
            assert decoded.size == (width, height)
            assert all(abs(float(value) - dpi) < 0.02 for value in decoded.info["dpi"])
            assert decoded.mode == ("RGBA" if transparent else "RGB")
            if transparent:
                assert set(np.asarray(decoded)[..., 3].flat) == {0, 255}
            if output["format"] == "PNG":
                assert all(decoded.info.get(key) for key in ("Software", "Creation Time", "Author"))
            if output["format"] != "JPEG":
                assert decoded.getpixel((1, 2))[:3] == (1, 2, 3)
            report["exports"].append(dict(format=decoded.format, size=decoded.size,
                dpi=[float(value) for value in decoded.info["dpi"]], mode=decoded.mode))
        assert_closed(view.returned_image)
        assert scene() == before

    target = folder / "existing.png"
    original = b"existing file must survive all failed exports"
    target.write_bytes(original)
    render_calls = len(view.calls)
    context_calls = view.render.make_current_calls
    for changes in ({"width": 0}, {"height": -1}, {"width": 16_385},
                    {"width": 6000, "height": 6000}, {"width": 3.5},
                    {"width": True}, {"height": None}, {"dpi": False},
                    {"dpi": "nan"}, {"dpi": float("inf")}, {"dpi": 2401},
                    {"format": "BMP"}, {"format": "JPEG"},
                    {"format": "JPEG", "transparent": True}):
        rejected(target, **changes)
        assert target.read_bytes() == original
    for path in ("", "   ", folder / "missing" / "figure.png"):
        rejected(path)
    directory = folder / "directory.png"
    directory.mkdir()
    rejected(directory)
    assert len(view.calls) == render_calls and view.render.make_current_calls == context_calls

    view.render.maximum = 32
    rejected(target, message="graphics device")
    view.render.maximum = module.MAX_DIMENSION
    assert len(view.calls) == render_calls

    render = view.render
    view.render = None
    rejected(target, expected=LimitationError, message="OpenGL")
    view.render = render
    render.current_result = False
    queried = render.limit_calls
    rejected(target, expected=LimitationError, message="OpenGL")
    assert render.limit_calls == queried and len(view.calls) == render_calls
    render.current_result = True

    for failure, expected, message in (("none", UserError, "could not render"),
            ("size", UserError, "unexpected image size"),
            ("render", RuntimeError, "supplied renderer failed")):
        view.failure = failure
        rejected(target, expected=expected, message=message)
        assert target.read_bytes() == original and scene() == before
        assert not hasattr(view.render, "image_save")
        if failure == "size":
            assert_closed(view.returned_image)
    assert all(framebuffer.deleted for framebuffer in view.test_framebuffers)
    view.render.image_save = "previous setting"
    rejected(target, expected=RuntimeError, message="supplied renderer failed")
    assert view.render.image_save == "previous setting"
    del view.render.image_save
    view.failure = None

    def partial_save(image, path, **kwargs):
        Path(path).write_bytes(b"partial encoding")
        raise OSError("supplied encode failure")

    with patch.object(Image.Image, "save", partial_save):
        rejected(target, message="supplied encode failure")
    assert_closed(view.returned_image)
    with patch.object(module.os, "replace", side_effect=OSError("supplied replace failure")):
        rejected(target, message="supplied replace failure")
    assert_closed(view.returned_image)
    with patch.object(module.tempfile, "mkstemp", side_effect=PermissionError("supplied folder failure")):
        rejected(target, message="supplied folder failure")
    assert_closed(view.returned_image)
    assert target.read_bytes() == original and not list(folder.glob(".chimerax-export-*"))
    assert scene() == before

    supplied_session.in_script = True
    module.export_image(supplied_session, target, format="PNG", width=37, height=29, dpi=300)
    assert updates == [True]
    with Image.open(target) as decoded:
        decoded.load()
        assert decoded.size == (37, 29)
    assert scene() == before and not list(folder.glob(".chimerax-export-*"))

report.update(validation=True, atomic_failure_recovery=True, supplied_render_state_recovery=True,
              supplied_scene_preserved=True, context_failure_before_gl_query=True)
print("EXPORT_QUALITY_OK", json.dumps(report), flush=True)
