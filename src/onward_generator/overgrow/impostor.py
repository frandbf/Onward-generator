"""Flat impostors: a prop lying on the ground drawn as one textured polygon (plan triangle audit, 10 November 2026).

A manhole cover is a disc a few centimetres thick; its file has about 1 800 triangles, and clustering it to the
128 the load-time level of detail asks for moves its rim, so it stayed at 1 021. Seen from a standing player it is
its top face. The impostor keeps that: the convex outline of the prop's plan, at most ``IMPOSTOR_SIDES`` corners,
at the prop's top height with its rim down to the ground, and the top view of the original mesh baked into one
albedo image (the rim samples its edge).
"""

from __future__ import annotations

import io
from dataclasses import replace
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image
from shapely import MultiPoint
from shapely.geometry import Polygon
from shapely.geometry.polygon import orient

from onward_generator.generation.mesh import Mesh

if TYPE_CHECKING:
    from onward_generator.overgrow.library import Prop

IMPOSTOR_SIDES = 16
IMPOSTOR_PX = 128


def outline(xy: np.ndarray, sides: int = IMPOSTOR_SIDES) -> Polygon:
    """The convex hull of the plan, simplified until it has at most ``sides`` corners; counter-clockwise."""
    hull = MultiPoint([tuple(p) for p in xy]).convex_hull
    if not isinstance(hull, Polygon):  # a convex hull is a Polygon only when it has area
        raise ValueError("a flat impostor needs a plan with an area")
    size = max(hull.bounds[2] - hull.bounds[0], hull.bounds[3] - hull.bounds[1])
    shape, tolerance = hull, size * 0.002
    while len(shape.exterior.coords) - 1 > sides:
        shape = hull.simplify(tolerance, preserve_topology=True)  # pragma: no mutate: preserve_topology=True is shapely's default; it is spelled out for the reader
        tolerance *= 1.5
    return orient(shape)  # counter-clockwise


def top_view(prop: Prop, px: int = IMPOSTOR_PX) -> tuple[np.ndarray, tuple[float, float, float, float]]:
    """The prop seen from above: for each pixel the albedo of the highest triangle over it; the bounds it covers."""
    vertices = prop.mesh.vertices
    x0, y0 = vertices[:, 0].min(), vertices[:, 1].min()
    x1, y1 = vertices[:, 0].max(), vertices[:, 1].max()
    width, height = max(x1 - x0, 1e-6), max(y1 - y0, 1e-6)
    image = np.zeros((px, px, 3), dtype=np.float32)
    depth = np.full((px, px), -np.inf)
    for primitive, part in zip(prop.primitives(), prop._parts(), strict=True):  # pragma: no mutate: primitives() is built from _parts(), so they always have the same length
        albedo = np.asarray(Image.open(io.BytesIO(part.albedo)).convert("RGB"))
        h, w = albedo.shape[:2]
        mesh, uvs = primitive.mesh, primitive.uvs
        assert uvs is not None  # Prop.primitives always sets them
        sx = (mesh.vertices[:, 0] - x0) / width * px
        sy = (y1 - mesh.vertices[:, 1]) / height * px
        for a, b, c in mesh.triangles:
            xs, ys = np.array([sx[a], sx[b], sx[c]]), np.array([sy[a], sy[b], sy[c]])
            area = (ys[1] - ys[2]) * (xs[0] - xs[2]) + (xs[2] - xs[1]) * (ys[0] - ys[2])
            if abs(area) < 1e-9:  # pragma: no mutate: an area of exactly 1e-9 does not occur; the bound only skips degenerate triangles
                continue
            # A pixel column at ceil(max) has its centre past the triangle, so the ranges stop before it.
            lo_x, lo_y = max(int(xs.min()), 0), max(int(ys.min()), 0)
            hi_x = min(int(np.ceil(xs.max())), px)  # pragma: no mutate: the vertices span [0, px]; the clamp only guards rounding
            hi_y = min(int(np.ceil(ys.max())), px)  # pragma: no mutate: the vertices span [0, px]; the clamp only guards rounding
            gx, gy = np.meshgrid(np.arange(lo_x, hi_x) + 0.5, np.arange(lo_y, hi_y) + 0.5)
            l0 = ((ys[1] - ys[2]) * (gx - xs[2]) + (xs[2] - xs[1]) * (gy - ys[2])) / area
            l1 = ((ys[2] - ys[0]) * (gx - xs[2]) + (xs[0] - xs[2]) * (gy - ys[2])) / area
            l2 = 1.0 - l0 - l1
            inside = (l0 >= 0) & (l1 >= 0) & (l2 >= 0)
            if not inside.any():
                continue
            z = l0 * mesh.vertices[a, 2] + l1 * mesh.vertices[b, 2] + l2 * mesh.vertices[c, 2]
            u = l0 * uvs[a, 0] + l1 * uvs[b, 0] + l2 * uvs[c, 0]
            v = l0 * uvs[a, 1] + l1 * uvs[b, 1] + l2 * uvs[c, 1]
            rows, cols = np.nonzero(inside)
            ty, tx = rows + lo_y, cols + lo_x
            front = z[inside] > depth[ty, tx]
            depth[ty[front], tx[front]] = z[inside][front]
            # Library UVs run V up from the bottom of the image, as Unity's (library._uvs flips the file's glTF V).
            colour = albedo[((1.0 - np.mod(v[inside], 1.0)) * (h - 1)).astype(int), (np.mod(u[inside], 1.0) * (w - 1)).astype(int)]
            image[ty[front], tx[front]] = colour[front]
    covered = np.isfinite(depth)
    if covered.any():
        image[~covered] = image[covered].mean(axis=0)  # the hull's corners outside the mesh take its mean colour
    return image, (float(x0), float(y0), float(width), float(height))


def flat_impostor(prop: Prop, sides: int = IMPOSTOR_SIDES, px: int = IMPOSTOR_PX) -> Prop:
    """``prop`` as one polygon at its top height, textured with its top view; its file's triangles are kept as source."""
    image, (x0, y0, width, height) = top_view(prop, px)
    shape = outline(prop.mesh.vertices[:, :2], sides)
    ring = np.array(shape.exterior.coords[:-1])
    top = float(prop.mesh.vertices[:, 2].max())
    centre = ring.mean(axis=0)
    count = len(ring)
    # The top face, fanned from the centre, and the rim down to the ground: the prop rests on z = 0 as every prop.
    vertices = np.vstack([np.append(centre, top), np.column_stack([ring, np.full(count, top)]),
                          np.column_stack([ring, np.zeros(count)])])
    fan = [(0, 1 + k, 1 + (k + 1) % count) for k in range(count)]
    rim = [tri for k in range(count) for tri in ((1 + k, 1 + count + k, 1 + count + (k + 1) % count),
                                                 (1 + k, 1 + count + (k + 1) % count, 1 + (k + 1) % count))]
    triangles = np.array(fan + rim, dtype=np.int64)  # pragma: no mutate: Python ints are already int64 on every 64-bit numpy
    # V up, as every library prop: the image's top row (north, y0 + height) is V = 1.
    uvs = np.column_stack([(vertices[:, 0] - x0) / width, (vertices[:, 1] - y0) / height]).astype(np.float32)
    out = io.BytesIO()
    Image.fromarray(image.astype(np.uint8)).save(out, format="PNG")  # uint8 texels and their mean: no clip needed
    return replace(prop, mesh=Mesh(vertices, triangles), uvs=uvs, albedo=out.getvalue(), parts=(),
                   source_triangles=prop.source_triangles or prop.triangles)
