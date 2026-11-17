"""Procedural cloth for a lived street: a shop's awning and a terrace's parasol (plan 2026-11-16, increment 3; ADR-044).

Both draw one material, ``awning_fabric``: a generated image of ``BANDS`` horizontal bands, each a pattern of
vertical stripes in one colour on cream, and a last band of plain grey for a parasol's pole. A piece picks its band by
its V range and repeats the stripes along U, one image width per ``STRIPE_REPEAT_M`` metres, so every awning and
parasol of a map shares one draw per combine cell. No model is downloaded: the cloth is CC0 by construction.

The cloth is two-sided (each face is drawn again turned the other way): a player sees an awning from the street and
from under it. Neither collides.
"""

from __future__ import annotations

import io
import math

import numpy as np
from PIL import Image

from onward_generator.generation.mesh import Mesh

FABRIC_MATERIAL = "awning_fabric"
BANDS = 4
"""Three striped colours and the pole's grey."""
POLE_BAND = 3
STRIPE_COLOURS = ((168, 44, 38), (38, 98, 62), (40, 70, 128))
CREAM = (236, 228, 206)
POLE_GREY = (70, 72, 74)
IMAGE_PX = 256
STRIPES = 8
"""Stripe pairs across the image."""
BAND_PX = IMAGE_PX // BANDS
STRIPE_PX = IMAGE_PX // (2 * STRIPES)
STRIPE_REPEAT_M = 1.0
"""One image width along the cloth: eight 12.5 cm stripe pairs a metre."""
BAND_MARGIN = 0.3
"""The share of a band left unused at each edge, so mipmaps do not bleed the next band in."""


def fabric_png() -> bytes:
    """The ``awning_fabric`` image."""
    pixels = np.full((IMAGE_PX, IMAGE_PX, 3), POLE_GREY, dtype=np.uint8)
    pixels[:POLE_BAND * BAND_PX] = CREAM
    # The last band first: a stripe that ran into the next band would show, not be painted over.
    for band in reversed(range(len(STRIPE_COLOURS))):
        for stripe in range(STRIPES):
            x0 = 2 * stripe * STRIPE_PX
            pixels[band * BAND_PX:(band + 1) * BAND_PX, x0:x0 + STRIPE_PX] = STRIPE_COLOURS[band]
    out = io.BytesIO()
    Image.fromarray(pixels).save(out, format="PNG")  # three uint8 channels: RGB
    return out.getvalue()


def band_v(band: int, share: float) -> float:
    """V of a point ``share`` (0 to 1) across the usable middle of ``band``; glTF V runs down the image."""
    return (band + BAND_MARGIN + (1.0 - 2 * BAND_MARGIN) * share) / BANDS


def two_sided(vertices: np.ndarray, triangles: np.ndarray, uvs: np.ndarray) -> tuple[Mesh, np.ndarray]:
    """The mesh with each triangle drawn again in the other winding, on the same vertices."""
    both = np.vstack([triangles, triangles[:, ::-1]])
    return Mesh(np.asarray(vertices, dtype=np.float64), both.astype(np.int64)), np.asarray(uvs, dtype=np.float64)


def awning_mesh(base: np.ndarray, along: np.ndarray, out: np.ndarray, width: float, top_z: float, depth: float, drop: float,
                valance: float, band: int) -> tuple[Mesh, np.ndarray]:
    """A sloped canopy from the wall at ``top_z`` out ``depth`` and down ``drop``, a valance ``valance`` deep hanging from its
    front edge, and a triangular cheek at each end; ``base`` is the middle of its top edge on the wall."""
    half = along * (width / 2)
    back_l, back_r = base - half, base + half
    front_l, front_r = back_l + out * depth, back_r + out * depth
    low = top_z - drop
    vertices = np.array([
        [*back_l, top_z], [*back_r, top_z], [*front_r, low], [*front_l, low],  # canopy 0-3
        [*front_l, low - valance], [*front_r, low - valance],  # valance bottom 4-5
    ])
    triangles = np.array([[0, 1, 2], [0, 2, 3], [3, 2, 5], [3, 5, 4], [0, 3, 4], [1, 5, 2]])
    u_right = width / STRIPE_REPEAT_M  # pragma: no mutate: STRIPE_REPEAT_M is 1, so / and * give the same U
    uvs = np.array([
        [0.0, band_v(band, 0.0)], [u_right, band_v(band, 0.0)], [u_right, band_v(band, 0.7)], [0.0, band_v(band, 0.7)],
        [0.0, band_v(band, 1.0)], [u_right, band_v(band, 1.0)],
    ])
    return two_sided(vertices, triangles, uvs)


def parasol_mesh(x: float, y: float, ground_z: float, radius: float, rim_z: float, apex_z: float, pole: float, band: int,
                 segments: int = 8) -> tuple[Mesh, np.ndarray]:
    """A cone of cloth ``segments`` sided, its apex ``apex_z`` and its rim ``rim_z`` above ``ground_z``, on a square pole
    ``pole`` wide from the ground to the apex, centred on (``x``, ``y``)."""
    rim = [(x + radius * math.cos(2 * math.pi * k / segments), y + radius * math.sin(2 * math.pi * k / segments)) for k in range(segments)]
    vertices = [[x, y, ground_z + apex_z], *([px, py, ground_z + rim_z] for px, py in rim)]
    uvs = [[0.5, band_v(band, 0.0)], *([k / 2, band_v(band, 1.0)] for k in range(segments))]
    triangles = [[0, 1 + (k + 1) % segments, 1 + k] for k in range(segments)]
    h = pole / 2
    base = len(vertices)
    for k, (dx, dy) in enumerate(((-h, -h), (h, -h), (h, h), (-h, h))):
        vertices += [[x + dx, y + dy, ground_z], [x + dx, y + dy, ground_z + apex_z]]
        uvs += [[k / 4, band_v(POLE_BAND, 0.0)], [k / 4, band_v(POLE_BAND, 1.0)]]
    for k in range(4):
        a, b = base + 2 * k, base + 2 * ((k + 1) % 4)
        triangles += [[a, b, b + 1], [a, b + 1, a + 1]]
    return two_sided(np.array(vertices), np.array(triangles), np.array(uvs))


__all__ = ["BANDS", "FABRIC_MATERIAL", "POLE_BAND", "STRIPE_REPEAT_M", "awning_mesh", "band_v", "fabric_png", "parasol_mesh", "two_sided"]
