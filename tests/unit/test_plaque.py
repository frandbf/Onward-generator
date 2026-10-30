"""The street plate draws Spanish names with real glyphs, never the notdef box."""

from __future__ import annotations

import hashlib
import unicodedata
from io import BytesIO

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from onward_generator.signs.plaque import FONT_PATH, PLATE_H, PLATE_W, plaque_font, plaque_png, plaque_text

NAMES = ["María", "Peña", "Ávila", "Begoña", "Calle de María del Carmen"]
# A code point DejaVu Sans does not map, so it draws the notdef glyph.
UNMAPPED = "\U0010fffd"
FONT_SHA256 = "e6476c1b80502924294eed40894c5b18e06c181444ca953e5334262df9c27724"


def _glyph(char: str, size: int = 64) -> bytes:
    mask = plaque_font(size).getmask(char)
    return bytes(mask.size) + bytes(mask)


def _pixels(png: bytes) -> np.ndarray:
    with Image.open(BytesIO(png)) as image:
        return np.asarray(image.convert("RGBA"))


def _ink_box(png: bytes) -> tuple[int, int, int, int]:
    pixels = _pixels(png)
    ink = np.argwhere(np.any(pixels[:, :, :3] != pixels[0, 0, :3], axis=2))
    (top, left), (bottom, right) = ink.min(axis=0), ink.max(axis=0)
    return int(left), int(top), int(right) + 1, int(bottom) + 1


def test_font_is_the_vendored_file() -> None:
    assert FONT_PATH.is_file()
    assert hashlib.sha256(FONT_PATH.read_bytes()).hexdigest() == FONT_SHA256
    assert (FONT_PATH.parent / "LICENSE").is_file()


def test_unmapped_code_point_draws_the_notdef_box() -> None:
    # The check below is only meaningful if notdef is a detectable glyph.
    assert _glyph(UNMAPPED) == _glyph("\U0010fffe")
    assert _glyph(UNMAPPED) != _glyph("a")


@pytest.mark.parametrize("form", ["NFC", "NFD"])
@pytest.mark.parametrize("name", NAMES)
def test_no_glyph_of_a_spanish_name_is_notdef(name: str, form: str) -> None:
    text = plaque_text(unicodedata.normalize(form, name))
    notdef = _glyph(UNMAPPED)
    assert [c for c in text if not c.isspace() and _glyph(c) == notdef] == []


@pytest.mark.parametrize("name", NAMES)
def test_decomposed_name_draws_the_same_plate(name: str) -> None:
    nfd = unicodedata.normalize("NFD", name)
    assert nfd != name
    assert plaque_text(nfd) == unicodedata.normalize("NFC", name)
    assert plaque_png(nfd) == plaque_png(name)


@pytest.mark.parametrize(("accent", "plain"), [("í", "i"), ("ñ", "n"), ("Á", "A"), ("é", "e")])
def test_accented_letter_differs_from_plain(accent: str, plain: str) -> None:
    assert _glyph(accent) != _glyph(plain)


@pytest.mark.parametrize(("accent", "plain"), [("María", "Maria"), ("Peña", "Pena"), ("Ávila", "Avila"), ("Begoña", "Begona")])
def test_accented_name_differs_from_plain(accent: str, plain: str) -> None:
    assert plaque_png(accent) != plaque_png(plain)


def test_whitespace_collapses_to_single_spaces() -> None:
    assert plaque_text("  Calle \t de\nMaría  ") == "Calle de María"


@pytest.mark.parametrize("name", NAMES)
def test_plate_size_and_text_stay_within_the_margins(name: str) -> None:
    png = plaque_png(name)
    assert _pixels(png).shape == (PLATE_H, PLATE_W, 4)
    margin = max(PLATE_W // 40, 8)
    left, top, right, bottom = _ink_box(png)
    assert left >= margin and top >= margin
    assert right <= PLATE_W - margin and bottom <= PLATE_H - margin


def _ink_size(text: str, size: int) -> tuple[int, int]:
    font = plaque_font(size)
    canvas = Image.new("L", (4096, 256))
    ImageDraw.Draw(canvas).text((16, 16), text, fill=255, font=font)
    left, top, right, bottom = canvas.getbbox() or (0, 0, 0, 0)
    return right - left, bottom - top


def _plate_ink_size(name: str) -> tuple[int, int]:
    left, top, right, bottom = _ink_box(plaque_png(name))
    return right - left, bottom - top


def test_short_name_uses_the_largest_size() -> None:
    assert _ink_size("Peña", 64) != _ink_size("Peña", 62)
    assert _plate_ink_size("Peña") == _ink_size("Peña", 64)


def test_sizes_step_down_two_at_a_time() -> None:
    # Too wide at 64 and 62, fits at 61 and 60: only even sizes are tried.
    name = "Plaza de Peña"
    assert _ink_size(name, 60) not in (_ink_size(name, 61), _ink_size(name, 62))
    assert _plate_ink_size(name) == _ink_size(name, 60)


def test_font_uses_basic_layout_even_where_raqm_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    # Pillow picks Raqm by default when it is present; the plate must not change with the host.
    monkeypatch.setattr(ImageFont.core, "HAVE_RAQM", True)
    assert plaque_font(32).layout_engine == ImageFont.Layout.BASIC


def test_long_name_shrinks_until_it_fits() -> None:
    name = "Calle de María del Carmen"
    width, _ = _plate_ink_size(name)
    assert width <= PLATE_W - 2 * max(PLATE_W // 40, 8)
    assert _plate_ink_size(name) != _ink_size(name, 64)


def test_name_too_long_for_any_size_uses_the_smallest() -> None:
    name = plaque_text("Avenida " * 12)
    assert _ink_size(name, 16)[0] > PLATE_W
    heights = {size: _ink_size(name, size)[1] for size in (14, 16, 18)}
    assert len(set(heights.values())) == 3
    assert _plate_ink_size(name)[1] == heights[16]


def test_plate_is_deterministic() -> None:
    assert plaque_png("Calle de María del Carmen") == plaque_png("Calle de María del Carmen")
