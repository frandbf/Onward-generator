"""Reusable prototype meshes for phase 04, built once and placed by 4×4 matrices.

Module frame: X along the facade, Y into the building (outward is −Y), Z up,
so a facade frame (u, −n_out, up) is a proper rotation and keeps winding.
Urban elements use a ground frame with the origin at the base centre.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from shapely import Polygon

from onward_generator.generation.mesh import Mesh, box, merge, prism, quad, transform

RAIL_THICKNESS_M = 0.05


@dataclass(frozen=True, slots=True)
class Prototype:
    """A reusable visual module (material → mesh) and its optional simplified collider."""

    key: str
    parts: tuple[tuple[str, Mesh], ...]
    collider: Mesh | None = None

    @property
    def triangle_count(self) -> int:
        return sum(m.triangle_count for _, m in self.parts)


def frame_matrix(origin: Sequence[float], x_axis: Sequence[float], y_axis: Sequence[float], z_axis: Sequence[float] = (0.0, 0.0, 1.0)) -> np.ndarray:
    m = np.eye(4)
    m[:3, 0], m[:3, 1], m[:3, 2], m[:3, 3] = x_axis, y_axis, z_axis, origin
    if np.linalg.det(m[:3, :3]) <= 0:
        raise ValueError("module frame must be a proper (right-handed) rotation")
    return m


def yaw_matrix(x: float, y: float, z: float, yaw_rad: float) -> np.ndarray:
    c, s = math.cos(yaw_rad), math.sin(yaw_rad)
    return frame_matrix((x, y, z), (c, s, 0.0), (-s, c, 0.0))


def _key(*parts: object) -> str:
    return ":".join(f"{p:.3f}" if isinstance(p, float) else str(p) for p in parts)


def recess(width: float, height: float, depth: float, back: str | None, frame_material: str = "frame_stone") -> Prototype:
    """Opening reveals (sill, head, jambs) and an optional back panel at ``depth``.

    ``back=None`` leaves the back open: a through-opening for doors into interiors.
    """
    w, h, d = width, height, depth
    reveals = merge([
        quad((0, 0, 0), (w, 0, 0), (w, d, 0), (0, d, 0)),
        quad((0, 0, h), (0, d, h), (w, d, h), (w, 0, h)),
        quad((0, 0, 0), (0, d, 0), (0, d, h), (0, 0, h)),
        quad((w, 0, 0), (w, 0, h), (w, d, h), (w, d, 0)),
    ])
    parts: list[tuple[str, Mesh]] = [(frame_material, reveals)]
    if back is not None:
        parts.append((back, quad((0, d, 0), (w, d, 0), (w, d, h), (0, d, h))))
    return Prototype(_key("recess", w, h, d, back or "open"), tuple(parts))


def recess_rim(width: float, height: float, depth: float, *, include_bottom: bool) -> list[tuple[tuple[float, float, float], tuple[float, float, float]]]:
    """The open back rim of a through-opening, as module-frame segments."""
    w, h, d = width, height, depth
    rim = [((0, d, 0), (0, d, h)), ((0, d, h), (w, d, h)), ((w, d, h), (w, d, 0))]
    if include_bottom:
        rim.append(((w, d, 0), (0, d, 0)))
    return rim


def balcony(width: float, depth: float, slab: float, railing: float) -> Prototype:
    """Slab and a solid front parapet in front of a balcony door (20 triangles).

    Origin at the slab's left end on the wall, top of slab at Z = 0. Faces hidden
    against the wall or the slab are omitted; side railings are left out for cost.
    """
    w, d, t = width, depth, RAIL_THICKNESS_M
    slab_mesh = box(0, w, -d, 0, -slab, 0, omit=("+y",))
    rail = box(0, w, -d, -d + t, 0, railing, omit=("-z",))
    collider = box(0, w, -d, 0, -slab, railing)
    return Prototype(_key("balcony", w, d, slab, railing), (("frame_stone", slab_mesh), ("railing_metal", rail)), collider)


@dataclass(frozen=True, slots=True)
class StairGeometry:
    risers: int
    riser_m: float
    tread_m: float
    run_m: float
    width_m: float
    rise_m: float


def stair_geometry(rise: float, width: float, max_riser: float, tread: float) -> StairGeometry:
    risers = max(2, math.ceil(rise / max_riser - 1e-9))
    return StairGeometry(risers, rise / risers, tread, (risers - 1) * tread, width, rise)


def stair(geometry: StairGeometry) -> Prototype:
    """Straight flight: solid stepped block from Z = 0, X ∈ [0, run], Y ∈ [0, width].

    Step i (1 … n−1) has its top at i·riser over X ∈ [(i−1)·tread, i·tread]; the
    last riser lands on the upper floor at X = run. The collider is a ramp wedge
    rising to the full storey height at X = run.
    """
    g = geometry
    profile = [(0.0, 0.0)]
    for i in range(1, g.risers):
        profile += [((i - 1) * g.tread_m, i * g.riser_m), (i * g.tread_m, i * g.riser_m)]
    profile += [(g.run_m, 0.0)]
    # Profile in (X, Z) extruded along Y: map (px, py, pz) → (px, width − pz, py), a proper rotation.
    to_frame = frame_matrix((0.0, g.width_m, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, -1.0, 0.0))
    solid = transform(prism(Polygon(profile), 0.0, g.width_m, bottom=True), to_frame)
    ramp = transform(prism(Polygon([(0.0, 0.0), (g.run_m, 0.0), (g.run_m, g.rise_m)]), 0.0, g.width_m, bottom=True), to_frame)
    return Prototype(_key("stair", g.risers, g.riser_m, g.tread_m, g.width_m), (("stair", solid),), ramp)


def ngon(radius: float, sides: int) -> Polygon:
    return Polygon([(radius * math.cos(2 * math.pi * k / sides), radius * math.sin(2 * math.pi * k / sides)) for k in range(sides)])


def lathe(profile: Sequence[tuple[float, float]], sides: int) -> Mesh:
    """Closed surface of revolution; ``profile`` is (radius, z) from the bottom pole to the top pole."""
    if profile[0][0] != 0 or profile[-1][0] != 0:
        raise ValueError("lathe profile must start and end on the axis")
    angles = [2 * math.pi * k / sides for k in range(sides)]
    vertices: list[tuple[float, float, float]] = []
    triangles: list[tuple[int, int, int]] = []

    def ring(r: float, z: float) -> list[int]:
        start = len(vertices)
        if r == 0:
            vertices.append((0.0, 0.0, z))
            return [start] * sides
        vertices.extend((r * math.cos(a), r * math.sin(a), z) for a in angles)
        return list(range(start, start + sides))

    rings = [ring(r, z) for r, z in profile]
    for lower, upper in zip(rings[:-1], rings[1:], strict=True):
        for j in range(sides):
            k = (j + 1) % sides
            a, b, c, d = lower[j], lower[k], upper[k], upper[j]
            if a != b:
                triangles.append((a, b, c))
            if c != d:
                triangles.append((a, c, d))
    return Mesh(np.array(vertices, dtype=np.float64), np.array(triangles, dtype=np.int64))


def _post(radius: float, height: float, sides: int = 6) -> Mesh:
    return prism(ngon(radius, sides), 0.0, height, bottom=False)


TREE_PROFILES: tuple[list[tuple[float, float]], ...] = (
    [(0.0, 2.2), (1.6, 2.9), (1.9, 3.8), (1.2, 4.9), (0.0, 5.3)],
    [(0.0, 2.0), (1.2, 2.6), (2.0, 3.5), (1.5, 4.8), (0.0, 5.5)],
    [(0.0, 2.4), (1.1, 2.8), (1.5, 4.0), (0.9, 5.0), (0.0, 5.4)],
)
TREE_UPPER = [(0.0, 4.2), (0.9, 4.6), (1.1, 5.2), (0.6, 5.8), (0.0, 6.1)]


def _tree_parts(sides: int, variant: int) -> tuple[tuple[str, Mesh], ...]:
    trunk = _post(0.15, 2.6)
    canopy = lathe(TREE_PROFILES[variant % len(TREE_PROFILES)], sides)
    if sides > 8:
        canopy = merge([canopy, lathe(TREE_UPPER, sides)])
    return (("tree_trunk", trunk), ("tree_canopy", canopy))


def urban_prototypes(*, tree_sides: int = 8, tree_variants: int = 1) -> dict[str, Prototype]:
    """Low-poly street furniture. Generic shapes: they stand for a kind of object, not a specific model.

    Phase 04 keeps the default 8-sided single canopy. Dress style may request more sides
    and extra canopy variants; those keys are ``tree``, ``tree_1``, ``tree_2``.
    """
    m = "urban_metal"
    variants = max(1, min(len(TREE_PROFILES), tree_variants))
    protos: dict[str, Prototype] = {}
    for i in range(variants):
        key = "tree" if i == 0 else f"tree_{i}"
        protos[key] = Prototype(
            f"urban:{key}:s{tree_sides}",
            _tree_parts(tree_sides, i),
            box(-0.2, 0.2, -0.2, 0.2, 0.0, 2.5),
        )
    protos.update({
        "street_lamp": Prototype(
            "urban:street_lamp",
            ((m, merge([_post(0.08, 4.5), box(0.0, 1.0, -0.04, 0.04, 4.4, 4.5), box(0.8, 1.2, -0.12, 0.12, 4.25, 4.4)])),),
            box(-0.1, 0.1, -0.1, 0.1, 0.0, 4.5),
        ),
        "bollard": Prototype("urban:bollard", ((m, _post(0.1, 0.9, 8)),), box(-0.1, 0.1, -0.1, 0.1, 0.0, 0.9)),
        "bicycle_parking": Prototype(
            "urban:bicycle_parking",
            ((m, merge([box(-1.0, 1.0, -0.025, 0.025, 0.65, 0.75), *(box(x - 0.025, x + 0.025, -0.025, 0.025, 0.0, 0.65, omit=("-z", "+z"))
                                                                      for x in (-0.95, 0.0, 0.95))])),),
            box(-1.0, 1.0, -0.1, 0.1, 0.0, 0.75),
        ),
        "bus_stop": Prototype(
            "urban:bus_stop",
            ((m, merge([_post(0.05, 2.8), box(-0.25, 0.25, -0.08, -0.04, 2.3, 2.8)])),),
            box(-0.08, 0.08, -0.08, 0.08, 0.0, 2.8),
        ),
        "fire_hydrant": Prototype("urban:fire_hydrant", ((m, _post(0.12, 0.7, 8)),), box(-0.12, 0.12, -0.12, 0.12, 0.0, 0.7)),
        "traffic_signals": Prototype(
            "urban:traffic_signals",
            ((m, merge([_post(0.07, 2.2), box(-0.15, 0.15, -0.15, 0.15, 2.2, 3.0)])),),
            box(-0.15, 0.15, -0.15, 0.15, 0.0, 3.0),
        ),
        "bench": Prototype(
            "urban:bench",
            ((m, merge([box(-0.9, 0.9, -0.25, 0.25, 0.42, 0.47), box(-0.9, 0.9, 0.2, 0.25, 0.47, 0.85, omit=("-z",)),
                        *(box(x - 0.04, x + 0.04, -0.2, 0.2, 0.0, 0.42, omit=("-z", "+z")) for x in (-0.8, 0.8))])),),
            box(-0.9, 0.9, -0.25, 0.25, 0.0, 0.85),
        ),
        "waste_basket": Prototype("urban:waste_basket", ((m, _post(0.2, 0.9, 8)),), box(-0.2, 0.2, -0.2, 0.2, 0.0, 0.9)),
    })
    return protos
