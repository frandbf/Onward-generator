"""The playable core and the scenic ring around it (ADR-017).

The core is a square of half-side ``half_side_m`` around the map centre, in
local metres. A building whose footprint meets the open square is ``core``:
it keeps today's detail. Every other building is ``scenic``: a plain shell
with no openings, interior, sign or collider. Stages after ``detail`` read
the core from the ``core`` block of ``scene_detail.json``.
"""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from typing import Any

from shapely import STRtree, box
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry

from onward_generator.generation.config import Material
from onward_generator.generation.gltf import Primitive, nodes_from_glb

CORE = "core"
SCENIC = "scenic"
SHARED = "shared"
TIERS = (CORE, SCENIC, SHARED)
MIN_CORE_HALF_SIDE_M = 50.0
MAX_CORE_HALF_SIDE_M = 2000.0
# Shared wall materials of the scenic shells. Three tones, so the ring is not one colour,
# and few, so a ring cell stays a handful of draws after the adapter's combine.
SCENIC_PREFIX = "scenic_"
SCENIC_WALLS: Mapping[str, Material] = {
    "scenic_wall_light": Material((0.90, 0.88, 0.84, 1.0), 0.9),
    "scenic_wall_warm": Material((0.92, 0.82, 0.62, 1.0), 0.9),
    "scenic_wall_ochre": Material((0.90, 0.68, 0.38, 1.0), 0.9),
}
# Every scenic roof: one roof material of the detail configuration, so it bakes at the roof scale.
SCENIC_ROOF = "roof_flat_grey"


class CoreError(ValueError):
    """The core is not a square the map can play in."""


def core_square(half_side_m: float) -> Polygon:
    """The core square around the local origin. Refuses a half-side outside [50, 2000] m."""
    if not MIN_CORE_HALF_SIDE_M <= half_side_m <= MAX_CORE_HALF_SIDE_M:
        raise CoreError(f"core half-side must be between {MIN_CORE_HALF_SIDE_M:g} and {MAX_CORE_HALF_SIDE_M:g} m, got {half_side_m:g}")
    return box(-half_side_m, -half_side_m, half_side_m, half_side_m)


def core_of(manifest: Mapping[str, Any]) -> Polygon | None:
    """The core square a ``scene_detail.json`` was built with, or None for a map without a core."""
    block = manifest.get("core")
    if not block:
        return None
    return core_square(float(block["half_side_m"]))


def building_tier(footprint: BaseGeometry, core: BaseGeometry | None) -> str:
    """``core`` when the footprint shares area with the core (or there is no core), else ``scenic``."""
    if core is None or (footprint.intersects(core) and not footprint.touches(core)):
        return CORE
    return SCENIC


def scenic_wall(seed: int, building_id: str) -> str:
    """One of the scenic wall materials, by ``sha256(seed, building, "scenic")``."""
    names = sorted(SCENIC_WALLS)
    return names[int(hashlib.sha256(f"{seed}:{building_id}:scenic".encode()).hexdigest(), 16) % len(names)]


def bounds_tier(bounds: tuple[float, float, float, float], core: BaseGeometry) -> str:
    """``core`` when the bounds lie inside the core, ``scenic`` when they miss it, ``shared`` otherwise."""
    extent = box(*bounds)
    if core.covers(extent):
        return CORE
    if not extent.intersects(core) or extent.touches(core):
        return SCENIC
    return SHARED


def _combined_nodes(visual: bytes, cell_m: float) -> Iterator[tuple[str, tuple[float, float, float, float], tuple[int, int], Sequence[Primitive]]]:
    """Each visual node: its object id, plan bounds, the adapter's combine cell and its world primitives."""
    nodes, _ = nodes_from_glb(visual)
    for node in nodes:
        primitives = node.world_primitives()
        lo = [min(float(p.mesh.vertices[:, k].min()) for p in primitives) for k in (0, 1)]
        hi = [max(float(p.mesh.vertices[:, k].max()) for p in primitives) for k in (0, 1)]
        # The adapter's cell is on the glTF ground plane (x east, z = -north), as perf measure.
        cell = (math.floor((lo[0] + hi[0]) / 2 / cell_m), math.floor(-(lo[1] + hi[1]) / 2 / cell_m))
        yield str(node.extras.get("object_id") or node.name), (lo[0], lo[1], hi[0], hi[1]), cell, primitives


def tier_summary(visual: bytes, objects: Iterable[Mapping[str, Any]], core: BaseGeometry, *, cell_m: float) -> dict[str, Any]:
    """Visual triangles and predicted draws per tier, after the adapter's (cell, material) combine.

    A node takes the ``tier`` of its object record; a node whose record has none
    (ground, curbs, markings, props, the backdrop) takes the tier of its bounds.
    A draw is one (cell, material) group; a group that holds two tiers counts
    once in each, so the tier draws can add up to more than ``draws``.
    """
    if not cell_m > 0:
        raise CoreError(f"cell_m must be positive, got {cell_m}")
    tier_of = {str(o["id"]): str(o["tier"]) for o in objects if o.get("tier") in TIERS}
    triangles: Counter[str] = Counter()
    groups: dict[str, set[tuple[int, int, str]]] = {tier: set() for tier in TIERS}
    for name, bounds, cell, primitives in _combined_nodes(visual, cell_m):
        tier = tier_of.get(name) or bounds_tier(bounds, core)
        for p in primitives:
            triangles[tier] += p.mesh.triangle_count
            groups[tier].add((cell[0], cell[1], p.material))
    return {
        "cell_m": cell_m,
        "draws": len(set().union(*groups.values())),
        **{tier: {"visual_triangles": triangles[tier], "draws": len(groups[tier])} for tier in TIERS},
    }


# A player's view for the draw estimate (view_draws): the horizontal field of a Quest eye with some
# margin, the far clip every playable layout is pinned to (export package checks far_clip_m 1000),
# cameras every VIEW_STEP_M across the playable area, VIEW_YAWS headings.
VIEW_FOV_DEG = 110.0
VIEW_FAR_M = 1000.0
VIEW_STEP_M = 25.0
VIEW_YAWS = 16
_WEDGE_ARC = 8


def _group_boxes(visual: bytes, cell_m: float) -> list[tuple[float, float, float, float]]:
    """The plan bounds of each (cell, material) group the adapter's combine makes, as tier_summary groups them."""
    groups: dict[tuple[int, int, str], list[float]] = {}
    for _, (x0, y0, x1, y1), cell, primitives in _combined_nodes(visual, cell_m):
        for p in primitives:
            b = groups.setdefault((cell[0], cell[1], p.material), [x0, y0, x1, y1])
            b[:] = [min(b[0], x0), min(b[1], y0), max(b[2], x1), max(b[3], y1)]
    return [(b[0], b[1], b[2], b[3]) for b in groups.values()]


def _wedge(x: float, y: float, yaw: float, fov: float, far: float) -> Polygon:
    half = fov / 2
    arc = [(x + far * math.cos(yaw - half + fov * k / _WEDGE_ARC), y + far * math.sin(yaw - half + fov * k / _WEDGE_ARC))
           for k in range(_WEDGE_ARC + 1)]
    return Polygon([(x, y), *arc])


def view_draws(
    visual: bytes,
    area: BaseGeometry,
    *,
    cell_m: float,
    fov_deg: float = VIEW_FOV_DEG,
    far_m: float = VIEW_FAR_M,
    step_m: float = VIEW_STEP_M,
    yaws: int = VIEW_YAWS,
) -> dict[str, Any]:
    """Draws a player would see: the (cell, material) groups whose plan bounds fall in a level view.

    Cameras stand every ``step_m`` across ``area`` (grid from its south-west corner) and turn through
    ``yaws`` headings, x east being 0 degrees. A group is seen when its bounds meet the camera's
    ``fov_deg`` wedge out to ``far_m``. No occlusion: a building behind another still counts, so this
    is the frustum culling the engine does and an upper bound per view. It counts glTF materials and
    does not apply the adapter's dedupe of identical ones.
    """
    if not cell_m > 0 or not step_m > 0 or yaws < 1:
        raise CoreError(f"cell_m and step_m must be positive and yaws at least 1, got {cell_m}, {step_m}, {yaws}")
    boxes = [box(*b) for b in _group_boxes(visual, cell_m)]
    tree = STRtree(boxes)
    x0, y0, x1, y1 = area.bounds
    fov = math.radians(fov_deg)
    counts: list[tuple[int, float, float, float]] = []
    nx, ny = int((x1 - x0) // step_m), int((y1 - y0) // step_m)  # pragma: no mutate: int() of a positive quotient equals its floor division
    for i in range(nx + 1):  # pragma: no mutate: a further column lies past the bounds, outside the area, and is skipped
        for j in range(ny + 1):  # pragma: no mutate: a further row lies past the bounds, outside the area, and is skipped
            x, y = x0 + i * step_m, y0 + j * step_m
            if not area.covers(box(x, y, x, y)):
                continue
            for k in range(yaws):
                yaw = 360.0 * k / yaws
                seen = len(tree.query(_wedge(x, y, math.radians(yaw), fov, far_m), predicate="intersects"))
                counts.append((seen, x, y, yaw))
    if not counts:
        raise CoreError("view_draws: no camera position falls inside the area")
    ordered = sorted(c[0] for c in counts)
    worst = max(counts, key=lambda c: c[0])  # the first of the busiest views in grid order: west, south, then heading
    return {
        "cell_m": cell_m, "fov_deg": fov_deg, "far_m": far_m, "step_m": step_m, "yaws": yaws,
        "views": len(counts),
        "median": ordered[len(ordered) // 2],
        "p90": ordered[int(len(ordered) * 0.9)],
        "max": worst[0],
        "worst": {"x_m": worst[1], "y_m": worst[2], "yaw_deg": worst[3]},
    }
