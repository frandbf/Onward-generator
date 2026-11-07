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
from dataclasses import dataclass
from itertools import pairwise
from typing import Any, Literal

from shapely import STRtree, box
from shapely.geometry import MultiPolygon, Point, Polygon
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
# Budget mode (ADR-017 amendment, 7 November 2026): the core half-side solved from a triangle target.
CORE_BUDGET_MODE = "budget"
CORE_BUDGET_TOLERANCE = 0.05
MIN_TARGET_TRIANGLES = 1000
MAX_TARGET_TRIANGLES = 2_000_000


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


def check_target(value: object) -> int:
    """``value`` as a triangle target: an integer from MIN_TARGET_TRIANGLES to MAX_TARGET_TRIANGLES."""
    if isinstance(value, bool) or not isinstance(value, int) or not MIN_TARGET_TRIANGLES <= value <= MAX_TARGET_TRIANGLES:
        raise CoreError(f"target_triangles must be an integer from {MIN_TARGET_TRIANGLES} to {MAX_TARGET_TRIANGLES}, got {value!r}")
    return value


def within_tolerance(achieved: int, target: int) -> bool:
    """True when ``achieved`` is at most CORE_BUDGET_TOLERANCE of ``target`` away from it."""
    return abs(achieved - target) <= CORE_BUDGET_TOLERANCE * target


def _segment_reach(x0: float, y0: float, x1: float, y1: float) -> float:
    """The smallest ``max(|x|, |y|)`` on a segment. Along it the function is convex and piecewise linear, so
    its minimum is at an end or where it bends: where x = 0, y = 0, x = y or x = -y."""
    dx, dy = x1 - x0, y1 - y0
    ts = [0.0, 1.0]
    for a, b in ((dx, x0), (dy, y0), (dx - dy, x0 - y0), (dx + dy, x0 + y0)):  # the bend where a t + b = 0
        if a != 0:
            t = -b / a
            if 0.0 < t < 1.0:  # pragma: no mutate: t of 0 or 1 is an end, already in ts
                ts.append(t)
    return min(max(abs(x0 + t * dx), abs(y0 + t * dy)) for t in ts)


def core_reach(footprint: BaseGeometry) -> float:
    """The L-infinity distance in local metres from the map centre to ``footprint``, 0 when it holds the centre.

    A polygon footprint is ``core`` (``building_tier``) exactly when its reach is under the core half-side.
    """
    if isinstance(footprint, MultiPolygon):
        return min(core_reach(part) for part in footprint.geoms)
    if not isinstance(footprint, Polygon) or footprint.is_empty:
        raise CoreError(f"core_reach needs a polygon footprint, got {footprint.geom_type}")
    if footprint.covers(Point(0.0, 0.0)):
        return 0.0
    rings = [footprint.exterior, *footprint.interiors]
    return min(_segment_reach(*a, *b) for ring in rings for a, b in pairwise(ring.coords))


def core_extent(footprint: BaseGeometry) -> float:
    """The largest ``max(|x|, |y|)`` of ``footprint`` in local metres: the smallest core half-side that covers it.

    An interior needs the whole footprint inside the core (``detail``), so it starts at this half-side.
    """
    if isinstance(footprint, MultiPolygon):
        return max(core_extent(part) for part in footprint.geoms)
    if not isinstance(footprint, Polygon) or footprint.is_empty:
        raise CoreError(f"core_extent needs a polygon footprint, got {footprint.geom_type}")
    return max(max(abs(x), abs(y)) for x, y in footprint.exterior.coords)


def building_costs(objects: Iterable[tuple[Mapping[str, Any], int]]) -> tuple[dict[str, int], dict[str, dict[str, int]]]:
    """Visual triangles per building of the ``core`` objects of a detail run, and of their interiors per kind.

    ``objects`` are (record, visual triangles) pairs. A building's cost is every core object whose
    ``source_ids[0]`` is that building: the building, its ``building_part`` and ``landmark_part`` pieces.
    Interiors are kept apart by their ``kind`` (``residential`` or ``shop``): each kind has its own
    triangle budget, and an interior needs its whole footprint inside the core. Records without the
    core tier (scenic, ground, urban elements) are left out.
    """
    costs: Counter[str] = Counter()
    interiors: dict[str, Counter[str]] = {}
    for record, triangles in objects:
        if record.get("tier") != CORE:
            continue
        bid = str(record["source_ids"][0])
        if record.get("type") == "interior":
            interiors.setdefault(str(record.get("kind", "residential")), Counter())[bid] += triangles
        else:
            costs[bid] += triangles
    return dict(sorted(costs.items())), {kind: dict(sorted(c.items())) for kind, c in sorted(interiors.items())}


@dataclass(frozen=True, slots=True)
class InteriorPool:
    """Interiors that share one triangle budget: per building, the half-side that covers its footprint and its triangles."""

    limit: int
    costs: Mapping[str, tuple[float, int]]


@dataclass(frozen=True, slots=True)
class CoreBudget:
    """The core a triangle target picks: its half-side, the triangles the cost model predicts and the buildings it holds.

    ``clamped`` is ``floor`` when the smallest core (MIN_CORE_HALF_SIDE_M) is already over the target, ``ceiling``
    when the largest one (``max_half_side_m``) is still under it, and None otherwise.
    """

    half_side_m: float
    predicted_triangles: int
    buildings: tuple[str, ...]
    clamped: Literal["floor", "ceiling"] | None


def _count(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CoreError(f"{name} must be a non-negative integer, got {value!r}")
    return value


def _metres(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise CoreError(f"{name} must be a finite non-negative number, got {value!r}")
    return float(value)


def _sweep(halves: Sequence[float], reach: Mapping[str, float], cost: Mapping[str, int], pools: Sequence[InteriorPool]) -> list[int]:
    """Predicted triangles at each of the ascending ``halves``: every building whose reach is under the
    half-side, and every interior whose extent is at most it, each pool up to its limit."""
    buildings = sorted((r, cost.get(bid, 0)) for bid, r in reach.items())
    rooms = [sorted(extent for extent, _ in p.costs.values()) for p in pools]
    room_costs = [[t for _, t in sorted(p.costs.values())] for p in pools]
    at, total = 0, 0
    at_rooms, room_totals = [0] * len(pools), [0] * len(pools)
    predicted = []
    for h in halves:
        while at < len(buildings) and buildings[at][0] < h:
            total += buildings[at][1]
            at += 1
        for k, extents in enumerate(rooms):
            while at_rooms[k] < len(extents) and extents[at_rooms[k]] <= h:
                room_totals[k] += room_costs[k][at_rooms[k]]
                at_rooms[k] += 1
        predicted.append(total + sum(min(p.limit, room_totals[k]) for k, p in enumerate(pools)))
    return predicted


def solve_core(reach: Mapping[str, float], cost: Mapping[str, int], target: int, *, max_half_side_m: float,
               interiors: Sequence[InteriorPool] = ()) -> CoreBudget:
    """The square core whose predicted triangles come nearest ``target``, buildings taken whole.

    ``reach`` is each building's ``core_reach`` and ``cost`` its triangles (a building without one costs 0).
    A building is in the core of half-side h when its reach is under h; its interior, from one of the
    ``interiors`` pools, when its ``core_extent`` is at most h, and each pool adds at most its limit.
    Under MIN_CORE_HALF_SIDE_M a building is always core; at or past ``max_half_side_m`` it never is.
    The half-sides tried are MIN_CORE_HALF_SIDE_M, ``max_half_side_m`` and the midpoint between two
    neighbouring reaches or extents, so no footprint lies on the edge. A tie goes to the smaller core,
    except under a target the largest core cannot reach: that core is kept, so ``detail`` reuses its run.
    """
    if isinstance(target, bool) or not isinstance(target, int) or target < 1:
        raise CoreError(f"target_triangles must be a positive integer, got {target!r}")
    if isinstance(max_half_side_m, bool) or not MIN_CORE_HALF_SIDE_M <= max_half_side_m <= MAX_CORE_HALF_SIDE_M:
        raise CoreError(f"max_half_side_m must be between {MIN_CORE_HALF_SIDE_M:g} and {MAX_CORE_HALF_SIDE_M:g} m, got {max_half_side_m!r}")
    cap = float(max_half_side_m)
    for bid, r in reach.items():
        _metres(f"reach of {bid}", r)
    for bid, c in cost.items():
        _count(f"cost of {bid}", c)
        if bid not in reach:
            raise CoreError(f"cost of {bid} has no reach")
    for pool in interiors:
        _count("interior limit", pool.limit)
        for bid, (extent, t) in pool.costs.items():
            _count(f"interior of {bid}", t)
            if bid not in reach:
                raise CoreError(f"interior of {bid} has no reach")
            if _metres(f"extent of {bid}", extent) <= reach[bid]:
                raise CoreError(f"extent of {bid} must exceed its reach")
    inside = {bid: float(r) for bid, r in reach.items() if r < cap}  # pragma: no mutate: a reach at the cap is under no half-side tried
    pools = [InteriorPool(p.limit, {b: v for b, v in p.costs.items() if b in inside}) for p in interiors]
    values = {MIN_CORE_HALF_SIDE_M, cap, *inside.values(), *(e for p in pools for e, _ in p.costs.values())}
    edges = sorted(v for v in values if MIN_CORE_HALF_SIDE_M <= v <= cap)  # pragma: no mutate: 50 and the cap are in the set already
    halves = [MIN_CORE_HALF_SIDE_M, *((a + b) / 2 for a, b in pairwise(edges)), cap]
    predicted = _sweep(halves, inside, cost, pools)
    best = min(range(len(halves)), key=lambda k: (abs(predicted[k] - target), k))
    clamped: Literal["floor", "ceiling"] | None = None
    if predicted[-1] < target:  # even the largest core is under the target: it is the one kept
        clamped, best = "ceiling", len(halves) - 1
    elif predicted[0] > target:
        clamped = "floor"
    h = halves[best]
    return CoreBudget(h, predicted[best], tuple(sorted(bid for bid, r in inside.items() if r < h)), clamped)


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
        lo, hi = _plan_bounds(primitives)
        yield str(node.extras.get("object_id") or node.name), (lo[0], lo[1], hi[0], hi[1]), _cell(lo, hi, cell_m), primitives


def _plan_bounds(primitives: Sequence[Primitive]) -> tuple[list[float], list[float]]:
    lo = [min(float(p.mesh.vertices[:, k].min()) for p in primitives) for k in (0, 1)]
    hi = [max(float(p.mesh.vertices[:, k].max()) for p in primitives) for k in (0, 1)]
    return lo, hi


def _cell(lo: Sequence[float], hi: Sequence[float], cell_m: float) -> tuple[int, int]:
    # The adapter's cell is on the glTF ground plane (x east, z = -north), as perf measure.
    return (math.floor((lo[0] + hi[0]) / 2 / cell_m), math.floor(-(lo[1] + hi[1]) / 2 / cell_m))


def combine_cell(primitives: Sequence[Primitive], cell_m: float) -> tuple[int, int]:
    """The adapter's combine cell of a node from its world primitives: the plan-bounds centre, x and -y, floored by ``cell_m``."""
    lo, hi = _plan_bounds(primitives)
    return _cell(lo, hi, cell_m)


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
