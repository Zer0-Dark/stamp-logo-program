"""Image compositing: full-resolution logo stamping and preview generation.

Quality rules: never downscale the source, re-encode JPEG at high quality with
no chroma subsampling, and carry EXIF/ICC across so colours stay put.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from PIL import Image, ImageOps

# Pillow refuses very large files by default as a decompression-bomb guard.
# These are the user's own photos, so lift the ceiling.
Image.MAX_IMAGE_PIXELS = None

SUPPORTED = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp"}

PREVIEW_MAX = 1500      # long edge of the preview shown while clicking
PREVIEW_QUALITY = 82
JPEG_QUALITY = 96       # visually lossless; pair with subsampling=0


def list_images(folder: Path) -> list[Path]:
    return sorted(
        (p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED),
        key=lambda p: p.name.lower(),
    )


def _load_oriented(path: Path) -> tuple[Image.Image, bytes | None, bytes | None]:
    """Open an image with its EXIF rotation baked in.

    The browser applies EXIF orientation when it displays the preview, so the
    click coordinates are in upright space. Baking the rotation into the output
    keeps what the user clicked and what gets saved in agreement.
    """
    im = Image.open(path)
    icc = im.info.get("icc_profile")
    im = ImageOps.exif_transpose(im)
    exif = im.info.get("exif")  # exif_transpose already cleared the orientation tag
    return im, exif, icc


def preview_path(cache_dir: Path, src: Path) -> Path:
    key = f"{src}:{src.stat().st_mtime_ns}:{PREVIEW_MAX}"
    return cache_dir / (hashlib.md5(key.encode()).hexdigest() + ".jpg")


def make_preview(src: Path, cache_dir: Path) -> Path:
    """Return a cached downscaled preview, generating it if needed."""
    dst = preview_path(cache_dir, src)
    if dst.exists():
        return dst
    im, _, _ = _load_oriented(src)
    im.thumbnail((PREVIEW_MAX, PREVIEW_MAX), Image.LANCZOS)
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    cache_dir.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(".part")
    im.save(tmp, "JPEG", quality=PREVIEW_QUALITY, optimize=True)
    os.replace(tmp, dst)
    im.close()
    return dst


def image_size(src: Path) -> tuple[int, int]:
    im, _, _ = _load_oriented(src)
    size = im.size
    im.close()
    return size


def reference_size(width: int, height: int) -> float:
    """The length the logo is sized against: the photo's geometric mean.

    Using sqrt(w*h) rather than the width means one global percentage looks
    right on a 4000x3000 landscape and a 3000x4000 portrait alike.
    """
    return (width * height) ** 0.5


def load_logo(path: Path) -> Image.Image:
    """Open a logo and fully decode it up front.

    Pillow decodes lazily, and the render threads all share this one object.
    An already-RGBA file would otherwise still be undecoded on first use, so
    two workers could decode it at the same time and trip an assertion inside
    Pillow. Forcing the decode here makes later resize() calls read-only.
    """
    logo = Image.open(path)
    if logo.mode != "RGBA":
        logo = logo.convert("RGBA")
    logo.load()
    return logo


def stamp(
    src: Path,
    out_dir: Path,
    marks: list[dict],
    logos: list[Image.Image],
    default_scale: float,
    opacity: float = 1.0,
    suffix: str = "_stamped",
) -> Path:
    """Composite one or more logos onto `src` and write the result.

    Each mark is {"logo": index, "x": .., "y": .., "scale": ..} with the
    position normalised (0..1) and `scale` expressed against the photo's
    *geometric mean* (sqrt(w*h)) rather than its width. Measuring against area
    keeps a logo at the same visual weight on landscape and portrait shots
    alike, which matters because the source photos are not a fixed size.
    """
    im, exif, icc = _load_oriented(src)
    base = im.convert("RGBA") if im.mode != "RGBA" else im.copy()
    im.close()

    reference = reference_size(base.width, base.height)

    # Paste each logo straight onto the photo using its own alpha as the mask.
    # A separate full-size overlay layer would be tidier but costs another
    # width*height*4 bytes per mark -- 48 MB a time on a 24MP photo, across
    # every worker at once.
    for mark in marks:
        logo = logos[min(mark.get("logo", 0), len(logos) - 1)]
        scale = mark.get("scale") or default_scale
        target_w = max(1, round(reference * scale))
        target_h = max(1, round(target_w * logo.height / logo.width))
        art = logo.resize((target_w, target_h), Image.LANCZOS)

        mark_opacity = mark.get("opacity", opacity)
        if mark_opacity < 1.0:
            alpha = art.getchannel("A").point(lambda v: round(v * mark_opacity))
            art.putalpha(alpha)

        left = round(mark["x"] * base.width - target_w / 2)
        top = round(mark["y"] * base.height - target_h / 2)
        # Keep the logo wholly on the photo. A mark placed in a corner and then
        # enlarged by the global size slider would otherwise hang over the edge
        # and be silently cropped.
        left = max(0, min(left, base.width - target_w))
        top = max(0, min(top, base.height - target_h))
        base.paste(art, (left, top), art)
        art.close()

    out = base

    out_dir.mkdir(parents=True, exist_ok=True)
    ext = src.suffix.lower()
    dst = out_dir / f"{src.stem}{suffix}{src.suffix}"

    params: dict = {}
    if ext in (".jpg", ".jpeg"):
        fmt = "JPEG"
        out = out.convert("RGB")
        params = dict(quality=JPEG_QUALITY, subsampling=0, optimize=True, progressive=True)
    elif ext == ".webp":
        fmt = "WEBP"
        params = dict(quality=98, method=6)
    elif ext in (".tif", ".tiff"):
        fmt = "TIFF"
        params = dict(compression="tiff_lzw")
    elif ext == ".bmp":
        fmt = "BMP"
        out = out.convert("RGB")
    else:
        fmt = "PNG"

    if exif:
        params["exif"] = exif
    if icc:
        params["icc_profile"] = icc

    tmp = dst.with_name(dst.name + ".part")
    # The temp file has no real extension, so Pillow must be told the format.
    out.save(tmp, fmt, **params)
    os.replace(tmp, dst)

    if out is not base:
        base.close()
    out.close()
    return dst
