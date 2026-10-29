"""Project one equirectangular picture onto a facade plane. Under development.

The long side of the JPEG is 512 px. The other side follows the wall's real
width and height. The new material's base colour is white, so the shader does
not tint the photograph.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass

import numpy as np
from PIL import Image

LONG_SIDE_PX = 512
BASE_COLOR = (1.0, 1.0, 1.0, 1.0)


class ProjectionError(ValueError):
    """This wall cannot receive a picture."""


@dataclass(frozen=True, slots=True)
class FacadeImage:
    png: bytes
    width: int
    height: int
    base_color: tuple[float, float, float, float]


def direction_uv(dx: float, dy: float, dz: float, azimuth_deg: float) -> tuple[float, float]:
    """Equirectangular coordinates for a direction. The image centre looks along ``azimuth_deg``."""
    bearing = math.degrees(math.atan2(dx, dy)) % 360.0
    delta = (bearing - azimuth_deg + 180.0) % 360.0 - 180.0
    horizontal = math.hypot(dx, dy)
    if horizontal < 1e-9:
        elevation = 90.0 if dz >= 0.0 else -90.0
    else:
        elevation = math.degrees(math.atan2(dz, horizontal))
    u = (0.5 + delta / 360.0) % 1.0
    v = min(1.0, max(0.0, 0.5 - elevation / 180.0))
    return u, v


def project_facade(
    image: Image.Image,
    *,
    camera_xyz: tuple[float, float, float],
    azimuth_deg: float,
    wall_a: tuple[float, float],
    wall_b: tuple[float, float],
    height_m: float,
    z_min: float = 0.0,
    long_side: int = LONG_SIDE_PX,
) -> FacadeImage:
    """Sample ``image`` on the wall from ``z_min`` up to ``height_m``.

    ``wall_a`` and ``wall_b`` are local metres. ``z_min`` 0 keeps the whole
    wall, which is what a centred test picture expects.
    """
    if long_side < 2:
        raise ProjectionError("long_side must be at least 2")
    if height_m <= z_min:
        raise ProjectionError("wall height must be above z_min")
    edge = (wall_b[0] - wall_a[0], wall_b[1] - wall_a[1])
    length = math.hypot(*edge)
    photo_height = height_m - z_min
    if length < 1e-3:
        raise ProjectionError("wall length must be positive")
    if length >= photo_height:
        width_px = long_side
        height_px = max(1, int(round(long_side * photo_height / length)))
    else:
        height_px = long_side
        width_px = max(1, int(round(long_side * length / photo_height)))
    columns = (np.arange(width_px, dtype=np.float64) + 0.5) / width_px
    rows = (np.arange(height_px, dtype=np.float64) + 0.5) / height_px
    along, vertical = np.meshgrid(columns, rows)
    unit_x = edge[0] / length
    unit_y = edge[1] / length
    px = wall_a[0] + along * length * unit_x
    py = wall_a[1] + along * length * unit_y
    pz = height_m - vertical * photo_height
    dx = px - camera_xyz[0]
    dy = py - camera_xyz[1]
    dz = pz - camera_xyz[2]
    bearing = np.degrees(np.arctan2(dx, dy)) % 360.0
    delta = (bearing - azimuth_deg + 180.0) % 360.0 - 180.0
    horizontal = np.hypot(dx, dy)
    elevation = np.degrees(np.arctan2(dz, np.maximum(horizontal, 1e-9)))
    u = (0.5 + delta / 360.0) % 1.0
    v = np.clip(0.5 - elevation / 180.0, 0.0, 1.0)
    rgb = _sample(image, u, v)
    buffer = io.BytesIO()
    Image.fromarray(rgb).save(buffer, format="PNG")
    return FacadeImage(png=buffer.getvalue(), width=width_px, height=height_px, base_color=BASE_COLOR)


def _sample(image: Image.Image, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    rgb = np.asarray(image.convert("RGB"), dtype=np.float64)
    height, width, _ = rgb.shape
    x = u * width
    y = np.clip(v, 0.0, 1.0) * (height - 1)
    x0 = np.floor(x).astype(np.int64) % width
    x1 = (x0 + 1) % width
    y0 = np.floor(y).astype(np.int64)
    y1 = np.clip(y0 + 1, 0, height - 1)
    tx = (x - np.floor(x))[..., None]
    ty = (y - y0)[..., None]
    top = rgb[y0, x0] * (1.0 - tx) + rgb[y0, x1] * tx
    bottom = rgb[y1, x0] * (1.0 - tx) + rgb[y1, x1] * tx
    mixed = top * (1.0 - ty) + bottom * ty
    return np.clip(np.rint(mixed), 0, 255).astype(np.uint8)
