"""Street-name plate painted into an albedo. The name is the OSM tag, not a translation.

The text is DejaVu Sans Bold 2.37, vendored at ``assets/fonts/dejavu`` and loaded by
path, so the plate does not depend on system fonts. It covers Latin-1 and Latin
Extended-A (á, é, í, ó, ú, ñ, ç, ü and the rest). Names are normalised to NFC first,
so a decomposed "i" + U+0301 draws as "í". This is not a photograph and not an
official street-sign design.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

FONT_PATH = Path(__file__).resolve().parents[3] / "assets" / "fonts" / "dejavu" / "DejaVuSans-Bold.ttf"
PLATE_W = 512
PLATE_H = 128
_BLUE = (22, 58, 112, 255)
_WHITE = (244, 242, 234, 255)
_MAX_SIZE = 64
_MIN_SIZE = 16


def plaque_rgba(name: str) -> np.ndarray:
    """The plate's pixels, ``PLATE_H`` x ``PLATE_W`` x 4."""
    width, height = PLATE_W, PLATE_H
    image = Image.new("RGBA", (width, height), _BLUE)
    draw = ImageDraw.Draw(image)
    margin = max(width // 40, 8)
    text = plaque_text(name)
    font = _fit(draw, text, width - 2 * margin, height - 2 * margin)
    box = draw.textbbox((0, 0), text, font=font)
    x = (width - (box[2] - box[0])) / 2 - box[0]
    y = (height - (box[3] - box[1])) / 2 - box[1]
    draw.text((x, y), text, fill=_WHITE, font=font)
    return np.asarray(image)


def plaque_text(name: str) -> str:
    """The text drawn on the plate: NFC, with runs of whitespace collapsed to one space."""
    return " ".join(unicodedata.normalize("NFC", name).split())


def plaque_font(size: int) -> ImageFont.FreeTypeFont:
    # BASIC layout: the same glyph positions with or without libraqm installed.
    return ImageFont.truetype(str(FONT_PATH), size, layout_engine=ImageFont.Layout.BASIC)


def _fit(draw: ImageDraw.ImageDraw, text: str, max_w: int, max_h: int) -> ImageFont.FreeTypeFont:
    """The largest even size from 64 down to 16 that fits. A name too long for 16 still gets 16."""
    for size in range(_MAX_SIZE, _MIN_SIZE - 2, -2):
        font = plaque_font(size)
        box = draw.textbbox((0, 0), text, font=font)
        if box[2] - box[0] <= max_w and box[3] - box[1] <= max_h:
            return font
    return font
