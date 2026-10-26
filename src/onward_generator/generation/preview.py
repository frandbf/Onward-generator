"""Dependency-free preview renderer (orthographic, z-buffered, flat Lambert shading → PNG)."""

from __future__ import annotations

import math
import struct
import zlib
from collections.abc import Mapping, Sequence

import numpy as np

from onward_generator.generation.config import Material
from onward_generator.generation.gltf import Node

_LIGHT = np.array([-0.45, -0.35, 0.82])
_LIGHT = _LIGHT / np.linalg.norm(_LIGHT)
_BACKGROUND = np.array([0.93, 0.95, 0.97])


def _png(rgb: np.ndarray) -> bytes:
    height, width, _ = rgb.shape
    raw = b"".join(b"\x00" + rgb[y].tobytes() for y in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def render(
    nodes: Sequence[Node],
    materials: Mapping[str, Material],
    *,
    width: int = 1600,
    height: int = 1100,
    azimuth_deg: float = 210.0,
    elevation_deg: float = 40.0,
    margin_px: int = 30,
    focus: tuple[float, float, float] | None = None,
    splat_subpixel: bool = False,
) -> bytes:
    """Render local-frame meshes (x east, y north, z up). Azimuth is the compass direction the camera looks from.

    ``focus = (x, y, radius)`` frames a sphere of that radius around ground point (x, y)
    instead of the whole scene, so different scenes rendered with the same focus align.
    ``splat_subpixel`` draws triangles smaller than a pixel as one sample (much faster for dense scenes).
    """
    return _png((render_rgb(nodes, materials, width=width, height=height, azimuth_deg=azimuth_deg,
                            elevation_deg=elevation_deg, margin_px=margin_px, focus=focus,
                            splat_subpixel=splat_subpixel) * 255).round().astype(np.uint8))


def side_by_side(images: Sequence[np.ndarray], gap_px: int = 8) -> bytes:
    """Concatenate float RGB images horizontally with a dark separator (before | after)."""
    h = max(i.shape[0] for i in images)
    parts = []
    for k, img in enumerate(images):
        if k:
            parts.append(np.full((h, gap_px, 3), 0.15))
        pad = np.tile(_BACKGROUND, (h, img.shape[1], 1))
        pad[: img.shape[0]] = img
        parts.append(pad)
    return _png((np.concatenate(parts, axis=1) * 255).round().astype(np.uint8))


def render_rgb(
    nodes: Sequence[Node],
    materials: Mapping[str, Material],
    *,
    width: int = 1600,
    height: int = 1100,
    azimuth_deg: float = 210.0,
    elevation_deg: float = 40.0,
    margin_px: int = 30,
    focus: tuple[float, float, float] | None = None,
    splat_subpixel: bool = False,
) -> np.ndarray:
    az, el = math.radians(azimuth_deg), math.radians(elevation_deg)
    forward = -np.array([math.sin(az) * math.cos(el), math.cos(az) * math.cos(el), math.sin(el)])
    right = np.cross(forward, [0.0, 0.0, 1.0])
    right /= np.linalg.norm(right)
    up = np.cross(right, forward)

    tris, colors = [], []
    for node in nodes:
        for prim in node.world_primitives():
            if not prim.mesh.triangle_count:
                continue
            v = prim.mesh.vertices[prim.mesh.triangles]
            n = np.cross(v[:, 1] - v[:, 0], v[:, 2] - v[:, 0])
            length = np.linalg.norm(n, axis=1)
            ok = length > 0
            v, n = v[ok], n[ok] / length[ok, None]
            facing = n @ -forward > 0
            v, n = v[facing], n[facing]
            base = np.array(materials[prim.material].color[:3])
            shade = 0.45 + 0.55 * np.clip(n @ _LIGHT, 0, 1)
            tris.append(v)
            colors.append(np.clip(base[None, :] * shade[:, None], 0, 1))
    canvas = np.tile(_BACKGROUND, (height, width, 1))
    if not tris:
        return canvas
    v = np.concatenate(tris)
    c = np.concatenate(colors)
    sx, sy, depth = v @ right, v @ up, v @ forward
    if focus is None:
        xmin, xmax, ymin, ymax = sx.min(), sx.max(), sy.min(), sy.max()
    else:
        fx, fy, radius = focus
        centre = np.array([fx, fy, 0.0])
        cx, cy = centre @ right, centre @ up
        xmin, xmax, ymin, ymax = cx - radius, cx + radius, cy - radius * 0.7, cy + radius * 0.7
    scale = min((width - 2 * margin_px) / (xmax - xmin), (height - 2 * margin_px) / (ymax - ymin))
    px = (sx - xmin) * scale + margin_px + ((width - 2 * margin_px) - (xmax - xmin) * scale) / 2
    py = (ymax - sy) * scale + margin_px + ((height - 2 * margin_px) - (ymax - ymin) * scale) / 2

    zbuf = np.full((height, width), np.inf)
    lo_px, hi_px = px.min(axis=1), px.max(axis=1)
    lo_py, hi_py = py.min(axis=1), py.max(axis=1)
    visible = (hi_px >= 0) & (lo_px < width) & (hi_py >= 0) & (lo_py < height)
    # Sub-pixel triangles become one depth-tested sample at their centroid, in submission order.
    tiny = splat_subpixel & visible & ((hi_px - lo_px) < 1.0) & ((hi_py - lo_py) < 1.0)
    for i in np.flatnonzero(tiny):
        x, y = int(px[i].mean()), int(py[i].mean())
        if 0 <= x < width and 0 <= y < height:
            z = depth[i].mean()
            if z < zbuf[y, x]:
                zbuf[y, x] = z
                canvas[y, x] = c[i]
    for i in np.flatnonzero(visible & ~tiny):
        x0, x1, x2 = px[i]
        y0, y1, y2 = py[i]
        lo_x, hi_x = max(int(min(x0, x1, x2)), 0), min(int(max(x0, x1, x2)) + 1, width - 1)
        lo_y, hi_y = max(int(min(y0, y1, y2)), 0), min(int(max(y0, y1, y2)) + 1, height - 1)
        if lo_x > hi_x or lo_y > hi_y:
            continue
        area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
        if abs(area) < 1e-12:
            continue
        gx, gy = np.meshgrid(np.arange(lo_x, hi_x + 1) + 0.5, np.arange(lo_y, hi_y + 1) + 0.5)
        w0 = ((x1 - gx) * (y2 - gy) - (x2 - gx) * (y1 - gy)) / area
        w1 = ((x2 - gx) * (y0 - gy) - (x0 - gx) * (y2 - gy)) / area
        w2 = 1.0 - w0 - w1
        inside = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
        if not inside.any():
            continue
        z = w0 * depth[i, 0] + w1 * depth[i, 1] + w2 * depth[i, 2]
        region = zbuf[lo_y : hi_y + 1, lo_x : hi_x + 1]
        closer = inside & (z < region)
        region[closer] = z[closer]
        canvas[lo_y : hi_y + 1, lo_x : hi_x + 1][closer] = c[i]
    return canvas
