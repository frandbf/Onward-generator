"""Street-name plate painted into an albedo. The name is the OSM tag, not a translation.

Pillow's built-in font covers Latin-1 (tildes and eñe). This is not a photograph
and not an official street-sign design.
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from onward_generator.dress.atlas import encode_png_rgba

PLATE_W = 512
PLATE_H = 128
_BLUE = (22, 58, 112, 255)
_WHITE = (244, 242, 234, 255)


def plaque_png(name: str, *, width: int = PLATE_W, height: int = PLATE_H) -> bytes:
    image = Image.new("RGBA", (width, height), _BLUE)
    draw = ImageDraw.Draw(image)
    margin = max(width // 40, 8)
    text = " ".join(name.split())
    font = _fit(draw, text, width - 2 * margin, height - 2 * margin)
    box = draw.textbbox((0, 0), text, font=font)
    x = (width - (box[2] - box[0])) / 2 - box[0]
    y = (height - (box[3] - box[1])) / 2 - box[1]
    draw.text((x, y), text, fill=_WHITE, font=font)
    return encode_png_rgba(np.asarray(image, dtype=np.uint8))


def _fit(draw: ImageDraw.ImageDraw, text: str, max_w: int, max_h: int) -> ImageFont.ImageFont:
    for size in range(64, 15, -2):
        font = ImageFont.load_default(size)
        box = draw.textbbox((0, 0), text, font=font)
        if box[2] - box[0] <= max_w and box[3] - box[1] <= max_h:
            return font
    return ImageFont.load_default(16)
