"""The listing image is generated from the map, at the SDK size."""

from __future__ import annotations

from io import BytesIO

import numpy as np
from PIL import Image, ImageDraw

from onward_generator.export.thumbnail import THUMBNAIL_HEIGHT, THUMBNAIL_WIDTH, _fit_font, render_listing_thumbnail
from onward_generator.generation.config import Material
from onward_generator.generation.gltf import Node, Primitive, build_glb
from onward_generator.generation.mesh import Mesh


def _scene() -> bytes:
    ground = Mesh(
        np.array([[0.0, 0.0, 0.0], [8.0, 0.0, 0.0], [0.0, 8.0, 0.0], [8.0, 8.0, 0.0]], dtype=np.float64),
        np.array([[0, 1, 2], [1, 3, 2]]),
    )
    return build_glb(
        [Node("surface/ground", (Primitive(ground, "ground"),))],
        {"ground": Material((0.45, 0.42, 0.38, 1.0), 1.0)},
    )


def test_thumbnail_is_512_by_256_and_uses_the_map_title() -> None:
    png = render_listing_thumbnail(_scene(), "Obelisco")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    image = Image.open(BytesIO(png))
    assert image.size == (THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT)
    other = render_listing_thumbnail(_scene(), "Otro mapa")
    assert other != png
    pixels = np.asarray(image)
    assert pixels[THUMBNAIL_HEIGHT - 8].mean() < pixels[8].mean()


def test_accented_titles_draw_their_letters_and_ignore_unicode_form() -> None:
    scene = _scene()
    nfc = render_listing_thumbnail(scene, "Calle de María")
    assert render_listing_thumbnail(scene, "Calle de Mari\u0301a") == nfc
    # An unmapped code point draws the notdef box; an accented letter must not.
    assert render_listing_thumbnail(scene, "Calle de Mar\U0010fffda") != nfc
    assert render_listing_thumbnail(scene, "Calle de Maria") != nfc


def test_titles_shrink_by_two_down_to_twelve() -> None:
    draw = ImageDraw.Draw(Image.new("RGBA", (THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT)))
    assert _fit_font(draw, "Obelisco", THUMBNAIL_WIDTH - 32, 40).size == 40
    assert _fit_font(draw, "Plaza " * 30, THUMBNAIL_WIDTH - 32, 40).size == 12
    sizes = {_fit_font(draw, "Plaza de " + "x" * n, THUMBNAIL_WIDTH - 32, 40).size for n in range(0, 60, 3)}
    assert sizes <= set(range(12, 41, 2)) and len(sizes) > 3


def test_an_empty_title_is_drawn_as_map() -> None:
    assert render_listing_thumbnail(_scene(), "   ") == render_listing_thumbnail(_scene(), "Map")
