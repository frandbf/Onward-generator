"""512×256 listing image for one map, drawn from its visual mesh and title.

The Belluga artwork stays a fallback for packages that have no generated file.
A new map must not reuse that picture.
"""

from __future__ import annotations

from io import BytesIO

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from onward_generator.generation.gltf import nodes_from_glb
from onward_generator.generation.preview import render_rgb
from onward_generator.signs.plaque import plaque_font, plaque_text

THUMBNAIL_WIDTH = 512
THUMBNAIL_HEIGHT = 256
_BAR_TOP = 188


def render_listing_thumbnail(visual_glb: bytes, title: str) -> bytes:
    """Oblique view of the visual mesh with the map title on a dark band."""
    nodes, materials = nodes_from_glb(visual_glb)
    rgb = render_rgb(
        nodes, materials,
        width=THUMBNAIL_WIDTH, height=THUMBNAIL_HEIGHT,
        azimuth_deg=210.0, elevation_deg=38.0, margin_px=10, splat_subpixel=True,
    )
    image = Image.fromarray((np.clip(rgb, 0.0, 1.0) * 255.0).round().astype(np.uint8)).convert("RGBA")
    label = plaque_text(title) or "Map"
    bar = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(bar)
    draw.rectangle((0, _BAR_TOP, THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT), fill=(16, 18, 20, 200))
    font = _fit_font(draw, label, THUMBNAIL_WIDTH - 32, 40)
    left, top, right, bottom = draw.textbbox((0, 0), label, font=font)
    x = (THUMBNAIL_WIDTH - (right - left)) / 2 - left
    y = _BAR_TOP + (THUMBNAIL_HEIGHT - _BAR_TOP - (bottom - top)) / 2 - top
    draw.text((x, y), label, font=font, fill=(245, 242, 234, 255))
    framed = Image.alpha_composite(image, bar).convert("RGB")
    if framed.size != (THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT):
        raise RuntimeError(f"thumbnail is {framed.size}, expected {THUMBNAIL_WIDTH}×{THUMBNAIL_HEIGHT}")
    out = BytesIO()
    framed.save(out, format="PNG")
    return out.getvalue()


_MIN_SIZE = 12


def _fit_font(draw: ImageDraw.ImageDraw, label: str, max_width: int, size: int) -> ImageFont.FreeTypeFont:
    """The largest even size from ``size`` down to 12 that fits. A title too long for 12 still gets 12.

    Same vendored font as the street plates, so accented titles render (no notdef boxes).
    """
    for step in range(size, _MIN_SIZE - 2, -2):
        font = plaque_font(step)
        left, _, right, _ = draw.textbbox((0, 0), label, font=font)
        if right - left <= max_width:
            return font
    return font
