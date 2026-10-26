"""Low-poly overgrowth meshes and project-generated albedos.

The cards are opaque: Onward's map shader has no cutout. They are not photographs.
"""

from __future__ import annotations

import numpy as np

from onward_generator.detail.modules import _tree_parts
from onward_generator.dress.atlas import encode_png_rgba
from onward_generator.generation.mesh import Mesh, box, merge, quad


def _png(rgb: tuple[int, int, int], seed: int, size: int = 128) -> bytes:
    rng = np.random.default_rng(seed)
    image = np.zeros((size, size, 4), dtype=np.uint8)
    noise = rng.integers(-18, 19, size=(size, size, 3), dtype=np.int16)
    base = np.array(rgb, dtype=np.int16)
    image[..., :3] = np.clip(base + noise, 0, 255).astype(np.uint8)
    image[..., 3] = 255
    return encode_png_rgba(image)


def moss_png() -> bytes:
    return _png((78, 96, 48), 11)


def grass_png() -> bytes:
    return _png((62, 104, 38), 23)


def rust_png() -> bytes:
    return _png((112, 58, 32), 41)


def canopy_png() -> bytes:
    return _png((46, 82, 34), 17)


def trunk_png() -> bytes:
    return _png((74, 52, 32), 19)


def vine_png() -> bytes:
    return _png((48, 72, 36), 29)


def moss_skirt(start: np.ndarray, end: np.ndarray, outward: np.ndarray) -> Mesh:
    """A strip 0.8 m tall on the outside of a wall. Two triangles."""
    offset = outward * 0.04
    a = start + offset
    b = end + offset
    z0, z1 = 0.05, 0.85
    return quad(
        (float(a[0]), float(a[1]), z0),
        (float(b[0]), float(b[1]), z0),
        (float(b[0]), float(b[1]), z1),
        (float(a[0]), float(a[1]), z1),
    )


def grass_tuft() -> Mesh:
    height, half = 0.55, 0.32
    first = quad((-half, 0.0, 0.0), (half, 0.0, 0.0), (half, 0.0, height), (-half, 0.0, height))
    second = quad((0.0, -half, 0.0), (0.0, half, 0.0), (0.0, half, height), (0.0, -half, height))
    return merge([first, second])


def wreck_mesh() -> Mesh:
    """A generic rusted body. No badge, no lights, no wheels."""
    body = box(-2.0, 2.0, -0.8, 0.8, 0.0, 0.95)
    cabin = box(-0.4, 1.3, -0.7, 0.7, 0.85, 1.55)
    return merge([body, cabin])


def tree_parts(variant: int) -> tuple[tuple[str, Mesh], ...]:
    return _tree_parts(8, variant)
