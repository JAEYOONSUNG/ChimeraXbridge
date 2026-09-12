"""Export the current ChimeraX scene with real pixel and print dimensions."""

import os
from pathlib import Path
import tempfile

from chimerax.core.errors import LimitationError, UserError


MAX_DIMENSION = 16384
MAX_PIXELS = 32_000_000
MAX_DPI = 2400
FORMATS = {"PNG": (".png",), "JPEG": (".jpg", ".jpeg"),
           "TIFF": (".tif", ".tiff")}


def _integer(value, label, maximum):
    try:
        number = int(value)
        exact = float(value) == number
    except (TypeError, ValueError, OverflowError):
        exact = False
    if isinstance(value, bool) or not exact or not 1 <= number <= maximum:
        raise UserError("%s must be a whole number from 1 to %s." % (label, maximum))
    return number


def export_image(session, path, *, format, width, height, dpi, transparent=False):
    """Save PNG, JPEG, or TIFF without changing the current scene or bookmarks.

    Width and height are pixels. DPI is written into the image's resolution
    metadata; it does not rescale the requested pixels. A suffixless path gets
    the format's usual extension. Existing files are replaced only after a
    successful encode, using a temporary file in the destination directory.
    """
    format = str(format).upper()
    format = {"JPG": "JPEG", "TIF": "TIFF"}.get(format, format)
    if format not in FORMATS:
        raise UserError("Choose PNG, JPEG, or TIFF for image export.")
    width = _integer(width, "Image width", MAX_DIMENSION)
    height = _integer(height, "Image height", MAX_DIMENSION)
    dpi = _integer(dpi, "DPI", MAX_DPI)
    if width * height > MAX_PIXELS:
        raise UserError("Use an image size of at most 32 million pixels.")
    if transparent and format == "JPEG":
        raise UserError("JPEG does not support transparency. Choose PNG or TIFF.")
    if not str(path).strip():
        raise UserError("Choose a file for the exported image.")
    target = Path(path).expanduser().absolute()
    if not target.suffix:
        target = target.with_suffix(FORMATS[format][0])
    elif target.suffix.lower() not in FORMATS[format]:
        raise UserError("%s export needs a %s filename." %
                        (format, " or ".join(FORMATS[format])))
    if not target.parent.is_dir():
        raise UserError('Export folder does not exist: "%s".' % target.parent)
    if target.is_dir():
        raise UserError('Choose a file instead of the folder "%s".' % target)

    view = session.main_view
    render = view.render
    if render is None:
        raise LimitationError("Image export requires OpenGL rendering.")
    if render.make_current() is False:
        raise LimitationError("Image export requires an active OpenGL rendering context.")
    maximum = render.max_framebuffer_size()
    if maximum and (width > maximum or height > maximum):
        raise UserError("This graphics device supports image dimensions up to %s pixels." % maximum)

    # Follow ChimeraX's image_formats.save preparation to update clip planes.
    if session.in_script:
        session.update_loop.update_graphics_now()
    image = _render_image(view, width, height, bool(transparent))
    if image is None:
        raise UserError("ChimeraX could not render the requested image size.")
    temporary = None
    try:
        if image.size != (width, height):
            raise UserError("The renderer returned an unexpected image size.")
        metadata = {"dpi": (dpi, dpi)}
        if format == "JPEG":
            metadata.update(quality=95, subsampling=0)
        elif format == "TIFF":
            metadata["compression"] = "tiff_lzw"
        elif format == "PNG":
            from PIL.PngImagePlugin import PngInfo
            from chimerax.core.session import standard_metadata
            provenance = standard_metadata()
            info = PngInfo()
            for key, value in (("Software", provenance["generator"]),
                               ("Creation Time", provenance["created"]),
                               ("Author", provenance["creator"])):
                info.add_itxt(key, str(value))
            metadata["pnginfo"] = info
        # Writing into the same directory lets os.replace stay atomic, and
        # leaves an existing export untouched if encoding fails.
        descriptor, temporary = tempfile.mkstemp(prefix=".chimerax-export-",
                                                 suffix=target.suffix,
                                                 dir=target.parent)
        os.close(descriptor)
        image.save(temporary, format=format, **metadata)
        os.replace(temporary, target)
        temporary = None
    except OSError as error:
        raise UserError('Could not write image "%s": %s' % (target, error)) from error
    finally:
        image.close()
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
    return {"path": str(target), "format": format, "width": width,
            "height": height, "dpi": dpi, "transparent": bool(transparent)}


def _render_image(view, width, height, transparent):
    """Use ChimeraX's native supersampled renderer, restoring temporary state.

    ChimeraX 1.10's image_rgba does not unwind its framebuffer or camera pixel
    shift when drawing raises. Restore those here so a failed export cannot
    leave later viewport draws aimed at the export's offscreen framebuffer.
    Transparency is a framebuffer option; background/lighting are never edited.
    """
    camera, render = view.camera, view.render
    shift = camera._pixel_shift
    stack_depth = len(render.framebuffer_stack)
    missing = object()
    image_save = getattr(render, "image_save", missing)
    try:
        return view.image(width, height, supersample=3,
                          transparent_background=transparent)
    finally:
        camera.set_fixed_pixel_shift(shift)
        while len(render.framebuffer_stack) > stack_depth:
            framebuffer = render.pop_framebuffer()
            if framebuffer.name == "image capture":
                framebuffer.delete()
        if image_save is missing:
            if hasattr(render, "image_save"):
                del render.image_save
        else:
            render.image_save = image_save
        view.redraw_needed = True
