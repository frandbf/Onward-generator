"""The listing image is generated from the map, at the SDK size."""

from __future__ import annotations

from io import BytesIO

import numpy as np
from PIL import Image

from onward_generator.export.thumbnail import THUMBNAIL_HEIGHT, THUMBNAIL_WIDTH, render_listing_thumbnail
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
