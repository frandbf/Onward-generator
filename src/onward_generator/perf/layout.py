"""Minimal gameplay layout for the device measurement map (phase 05).

The Onward SDK validator refuses a map without two spawn systems (≥ 5
points each), an Uplink objective, a map quad and a tent (see
``docs/11-onward-sdk.md``). This module places those on free
walkable ground so the scene can be loaded and measured on the Quest. It is a
measurement fixture, not a gameplay design.

Coordinates are local metres (x east, y north, z up). Unity uses
(x, y, z)_unity = (x, z, y)_local: east, up, north, a proper mapping for a
left-handed y-up engine.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
from shapely import LineString, MultiLineString, Point, STRtree, contains_xy, dwithin, offset_curve, prepare, union_all
from shapely.geometry.base import BaseGeometry
from shapely.ops import linemerge, nearest_points, polylabel

from onward_generator.generation.mesh import polygons_of
from onward_generator.generation.scene import GeneratedScene
from onward_generator.generation.surfaces import LinearWay, SurfaceClass
from onward_generator.perf.climbs import climbed_roofs, place_climbs
from onward_generator.tiers import core_of

LAYOUT_SCHEMA = "onward_generator.perf_layout"
LAYOUT_VERSION = "0.1.0"
GAMEPLAY_SCHEMA = "onward_generator.gameplay_layout"
GAMEPLAY_VERSION = "0.3.0"
# Codes from GM_Base.EGameModeType in SDK v1.15. Assault is 6 (the workshop
# enum calls that value UplinkAssault). Retrieval is 15 and is not named in
# GameVariantTypes. Free Roam stays because the custom-content guide requires it.
MODE_CODES = {
    "free_roam": 4,
    "uplink": 1,
    "assault": 6,
    "escort": 2,
    "hunt": 10,
    "evac": 11,
    "retrieval": 15,
}
HARDDRIVE_COUNT = 3
BLOCK_GAP_M = 10.0
AI_COUNT = 32
AMMO_COUNT = 4
# No ammo crate this close to a spawn point: a player would spawn inside or against it.
AMMO_SPAWN_CLEAR_M = 3.0
AI_SPACING_M = 8.0
# One spawn per route. Starts closer than this share a spot, and Hunt then
# drops several bots on what reads as a single position. A short sidewalk
# uses a smaller gap, down to MIN_SPAWN_SEPARATION_M, so the 32 still fit.
SPAWN_SEPARATION_M = 8.0
SEPARATION_STEP_M = 0.1
# The SDK links a bot to a waypoint set only when the spawn is within 5 m of
# that set's start. The spawn is the start itself, so the link does not depend
# on a second snap. The floor stays 0.1 m past that link: a closer start would
# put the next bot inside it.
PATROL_LINK_M = 5.0
MIN_SPAWN_SEPARATION_M = 5.1
# A route is up to ROUTE_POINTS points, ROUTE_STEP_M apart along its line.
ROUTE_POINTS = 16
ROUTE_STEP_M = 14.0
# The adapter bakes the navmesh with a 0.35 m agent radius. Ground closer than
# that to a wall or kerb is not on the mesh, and a gap narrower than twice that
# splits it into islands.
NAV_AGENT_RADIUS_M = 0.35
# A second line along each sidewalk, this far past the kerb, for a street whose
# facades leave less than the configured sidewalk width.
KERB_OFFSET_M = 0.5

SPAWN_COLUMNS = 4
SPAWN_POINTS = 8
SPAWN_SPACING_M = 1.2
CLEARANCE_M = 1.5
TEAM_SEPARATION_M = 30.0
GRID_M = 2.0
TENT_DEPTH_M = -30.0
DAMAGE_DEPTH_M = -80.0
# The adapter's first navigation spacing (docs/11-onward-sdk.md): the larger of the
# navmesh span over the walk depth limit and sqrt(area / node budget), at least 1 m.
NAV_WALK_DEPTH = 400
NAV_NODE_BUDGET = 320
# The perimeter wall's default thickness (bounds), when the manifest does not record one.
WALL_THICKNESS_M = 0.4


class LayoutError(ValueError):
    pass


def component_holding(parts: Sequence[BaseGeometry], point: tuple[float, float]) -> BaseGeometry:
    """The walkable piece that contains ``point``.

    Eroding the sidewalks by 1 m and by 1.5 m can make two different pieces the
    largest. The Uplink was placed on the 1.5 m piece, so the later objectives
    stay on that same piece instead of a larger sidewalk elsewhere.
    """
    here = [part for part in parts if part.covers(Point(point))]
    if not here:
        raise LayoutError("the Uplink is not on walkable ground")
    return max(here, key=lambda part: (part.area, part.centroid.x, part.centroid.y))


def playable_area(detail_manifest: Mapping[str, Any]) -> BaseGeometry | None:
    """The playable core inside its wall (ADR-017), or None for a map without a core."""
    core = core_of(detail_manifest)
    if core is None:
        return None
    thickness = float((detail_manifest.get("envelope") or {}).get("thickness_m") or WALL_THICKNESS_M)
    # An inward buffer of a square keeps its corners square.
    return core.buffer(-thickness)


def free_ground(base: GeneratedScene, urban_positions: list[tuple[float, float]], *, element_radius_m: float = 1.0) -> BaseGeometry:
    walkable = [s.geometry for c, s in base.partition.surfaces.items() if c not in (SurfaceClass.CARRIAGEWAY, SurfaceClass.WATER)]
    if not walkable:
        raise LayoutError("the base has no walkable surface")
    free = union_all(walkable).difference(base.partition.buildings)
    if urban_positions:
        free = free.difference(union_all([Point(x, y).buffer(element_radius_m, 8) for x, y in urban_positions]))
    return free


def _block(cx: float, cy: float) -> list[tuple[float, float]]:
    rows = math.ceil(SPAWN_POINTS / SPAWN_COLUMNS)
    pts = []
    for i in range(SPAWN_POINTS):
        col, row = i % SPAWN_COLUMNS, i // SPAWN_COLUMNS
        pts.append((cx + (col - (SPAWN_COLUMNS - 1) / 2) * SPAWN_SPACING_M, cy + (row - (rows - 1) / 2) * SPAWN_SPACING_M))
    return pts


def _fits(region: BaseGeometry, pts: list[tuple[float, float]]) -> bool:
    xy = np.asarray(pts)
    return bool(contains_xy(region, xy[:, 0], xy[:, 1]).all())


def extra_spawn_block(
    area: BaseGeometry,
    near: tuple[float, float],
    avoid: list[tuple[float, float]],
) -> tuple[float, float]:
    """A second 8-point spawn block, nearest ``near`` and at least 10 m from ``avoid``.

    Assault's objective validator wants two SpawnSystem components on each team.
    Each component is the SDK prefab, so the block is the same 8-point grid.
    """
    minx, miny, maxx, maxy = area.bounds
    slots: list[tuple[float, float, float]] = []
    y = math.floor(miny / GRID_M) * GRID_M
    while y <= maxy + 1e-6:
        x = math.floor(minx / GRID_M) * GRID_M
        while x <= maxx + 1e-6:
            slots.append((math.hypot(x - near[0], y - near[1]), y, x))
            x += GRID_M
        y += GRID_M
    prepare(area)  # pragma: no mutate: only makes the containment tests faster
    # Nearest first, then south to north, then west to east: the first slot that fits wins.
    for _, y, x in sorted(slots):
        clear = all(math.hypot(x - ax, y - ay) >= BLOCK_GAP_M - 1e-6 for ax, ay in avoid)
        if clear and _fits(area, _block(x, y)):
            # Grid slots are whole multiples of GRID_M, exact in floating point.
            return (x, y)
    raise LayoutError("no second spawn block fits for Assault")


def gameplay_layout(base: GeneratedScene, detail_manifest: Mapping[str, Any], *, farthest: bool = False) -> dict[str, Any]:
    """Two spawn blocks and the Uplink between them on the largest free area.

    The device fixture keeps the defenders within ``TEAM_SEPARATION_M`` of the attackers.
    With ``farthest`` (the playable layout) they go to the farthest grid point of that area
    whose block fits, and it must be at least ``TEAM_SEPARATION_M`` away.
    """
    urban = [tuple(o["position_m"]) for o in detail_manifest.get("objects", []) if o.get("type") == "urban_element"]
    free = free_ground(base, urban)
    inside = playable_area(detail_manifest)
    if inside is not None:
        free = free.intersection(inside)
    region = free.buffer(-CLEARANCE_M)
    parts = [p for p in polygons_of(region) if p.area > 0]
    if not parts:
        raise LayoutError(f"no free walkable area with {CLEARANCE_M} m clearance")
    area = max(parts, key=lambda p: (p.area, p.centroid.x, p.centroid.y))
    prepare(area)  # pragma: no mutate: only makes the containment tests faster
    centre = polylabel(area, tolerance=0.1)
    a = (round(centre.x, 2), round(centre.y, 2))
    if not _fits(area, _block(*a)):
        raise LayoutError("the largest free area cannot hold a spawn block")

    bx, by = _defenders(area, a, farthest=farthest)
    b = (round(bx, 2), round(by, 2))

    mid = Point((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    if not area.contains(mid):
        mid = nearest_points(area, mid)[0]
    site = base.partition.site
    sx0, sy0, sx1, sy1 = site.bounds
    quad = (a[0], a[1] - SPAWN_SPACING_M * 2)
    return {
        "schema": LAYOUT_SCHEMA,
        "schema_version": LAYOUT_VERSION,
        "purpose": "device measurement fixture; not a gameplay design",
        "frame": {"unit": "m", "axes_local": "x east, y north, z up", "unity_from_local": "(x, y, z) -> (x, z, y)"},
        "ground_z_m": 0.0,
        "free_area_m2": round(area.area, 1),
        # Points are {x, y} objects: Unity's JsonUtility cannot read nested arrays.
        "attackers": {"centre_m": list(a), "points_m": [{"x": round(x, 2), "y": round(y, 2)} for x, y in _block(*a)]},
        "defenders": {"centre_m": list(b), "points_m": [{"x": round(x, 2), "y": round(y, 2)} for x, y in _block(*b)]},
        "uplink_m": [round(mid.x, 2), round(mid.y, 2)],
        "map_quad_m": [round(quad[0], 2), round(quad[1], 2)],
        "level_size_m": round(max(sx1 - sx0, sy1 - sy0), 1),
        "site_bounds_m": [round(v, 2) for v in (sx0, sy0, sx1, sy1)],
        "tent_z_m": TENT_DEPTH_M,
        "damage_volume": {"z_m": DAMAGE_DEPTH_M, "size_m": [round(sx1 - sx0 + 200, 1), 10.0, round(sy1 - sy0 + 200, 1)],
                          "centre_m": [round((sx0 + sx1) / 2, 2), round((sy0 + sy1) / 2, 2)]},
    }


def _defenders(area: BaseGeometry, a: tuple[float, float], *, farthest: bool) -> tuple[float, float]:
    """The grid point farthest from ``a`` whose block fits: within ``TEAM_SEPARATION_M`` for the fixture, anywhere with ``farthest``."""
    minx, miny, maxx, maxy = area.bounds
    x0, y0 = math.floor(minx / GRID_M) * GRID_M, math.floor(miny / GRID_M) * GRID_M
    columns = int((maxx - minx) / GRID_M) + 2  # pragma: no mutate: more columns only add points past the bounds, where no block fits
    rows = int((maxy - miny) / GRID_M) + 2  # pragma: no mutate: more rows only add points past the bounds, where no block fits
    candidates = [
        (x, y)
        for y in (y0 + GRID_M * j for j in range(rows))
        for x in (x0 + GRID_M * i for i in range(columns))
        if farthest or math.hypot(x - a[0], y - a[1]) <= TEAM_SEPARATION_M
    ]
    # Farthest first; ties go north, then east. The first block that fits wins.
    for x, y in sorted(candidates, key=lambda c: (-math.hypot(c[0] - a[0], c[1] - a[1]), -c[1], -c[0])):
        if not _fits(area, _block(x, y)):
            continue
        gap = math.hypot(x - a[0], y - a[1])
        if farthest and gap < TEAM_SEPARATION_M:
            raise LayoutError(f"the largest free area holds the teams only {gap:.1f} m apart; {TEAM_SEPARATION_M:.0f} m are required")
        if gap < 2 * SPAWN_SPACING_M * SPAWN_COLUMNS:
            raise LayoutError("no second spawn block fits apart from the first")
        return (x, y)
    raise LayoutError("no second spawn block fits near the first")


def yaw_toward(x: float, y: float, focus_x: float, focus_y: float) -> float:
    """Unity yaw in degrees: 0 looks north (local +y, Unity +z)."""
    return round(math.degrees(math.atan2(focus_x - x, focus_y - y)), 1)


# A circular buffer is a polygon. Its chords sit inside the true circle, so the
# radius grows just enough that every point outside the polygon is still at
# least ``spacing_m`` from the centre.
_BUFFER_RESOLUTION = 16


def _keep_out(spacing_m: float) -> float:
    return spacing_m / math.cos(math.pi / (2 * _BUFFER_RESOLUTION))


def _interior(part: BaseGeometry) -> Point:
    """A point inside ``part``. The pole of inaccessibility stays off the edge."""
    point = polylabel(part, tolerance=0.1)
    if part.contains(point):
        return point
    return part.representative_point()


def scatter_points(
    region: BaseGeometry,
    count: int,
    spacing_m: float,
    origin: tuple[float, float],
    seeds: list[tuple[float, float]] | tuple[tuple[float, float], ...] = (),
) -> list[tuple[float, float]]:
    """Deterministic points inside ``region``, at least ``spacing_m`` apart.

    Each new point is the interior of the largest piece of ground still outside
    the circles of the points already chosen. ``seeds`` are kept first even
    when they are closer to each other than ``spacing_m`` (a Hunt patrol start
    must sit on its route). Every point added after the seeds stays at least
    ``spacing_m`` from all of them. ``origin`` breaks ties toward the nearer piece.
    """
    chosen: list[tuple[float, float]] = [(float(x), float(y)) for x, y in seeds]
    if len(chosen) > count:
        raise LayoutError(f"{len(chosen)} patrol seeds exceed the {count} point budget")
    available = region
    radius = _keep_out(spacing_m)
    for x, y in chosen:
        available = available.difference(Point(x, y).buffer(radius, quad_segs=_BUFFER_RESOLUTION))
    while len(chosen) < count:
        parts = [part for part in polygons_of(available) if part.area > 1e-3]
        if not parts:
            break
        part = max(
            parts,
            key=lambda piece: (
                round(piece.area, 3),
                -math.hypot(piece.centroid.x - origin[0], piece.centroid.y - origin[1]),
                round(piece.centroid.x, 3),
                round(piece.centroid.y, 3),
            ),
        )
        point = _interior(part)
        xy = (float(point.x), float(point.y))
        if any(math.hypot(xy[0] - other[0], xy[1] - other[1]) < spacing_m - 1e-6 for other in chosen):
            before = available.area
            available = available.difference(Point(xy).buffer(0.25))
            if available.area >= before - 1e-6:
                break
            continue
        chosen.append(xy)
        available = available.difference(Point(xy).buffer(radius, quad_segs=_BUFFER_RESOLUTION))
    if len(chosen) < count:
        raise LayoutError(f"walkable ground holds {len(chosen)} points at {spacing_m} m; {count} are required")
    return chosen


def patrol_spawns(routes: list[dict[str, Any]], count: int, *, separation_m: float = SPAWN_SEPARATION_M) -> list[tuple[float, float]]:
    """One point on each route start, at least ``separation_m`` apart.

    Hunt picks a spawn at random and only patrols when that spawn is within 5 m
    of a route start. A second point on the same start, or 2 m along it, is the
    same position in the round. This never places two bots on one start.
    """
    chosen: list[tuple[float, float]] = []
    for route in routes:
        points = route.get("points") or []
        if len(points) < 2:
            continue
        seed = (float(points[0]["x"]), float(points[0]["y"]))
        if any(math.hypot(seed[0] - other[0], seed[1] - other[1]) < separation_m - 1e-6 for other in chosen):
            continue
        chosen.append(seed)
        if len(chosen) >= count:
            break
    if len(chosen) < count:
        raise LayoutError(f"route starts hold {len(chosen)} spawns at {separation_m:g} m; {count} are required")
    return chosen


def hunt_spawn_problems(ai: list[Any], routes: list[Any], *, separation_m: float = SPAWN_SEPARATION_M) -> list[str]:
    """Reject a Hunt layout whose bots share a spot or sit off every route start."""
    starts: list[tuple[float, float]] = []
    for route in routes:
        points = route.get("points") or [] if isinstance(route, Mapping) else []
        if len(points) >= 2 and isinstance(points[0], Mapping) and "x" in points[0] and "y" in points[0]:
            starts.append((float(points[0]["x"]), float(points[0]["y"])))
    coords: list[tuple[float, float]] = []
    for point in ai:
        if isinstance(point, Mapping) and "x" in point and "y" in point:
            coords.append((float(point["x"]), float(point["y"])))
    if len(coords) < 32 or not starts:
        return []
    problems: list[str] = []
    if any(min(math.hypot(x - sx, y - sy) for sx, sy in starts) > 1.0 for x, y in coords):
        problems.append("layout: an AI point is more than 1 m from every route start")
    for index, left in enumerate(coords):
        if any(math.hypot(left[0] - right[0], left[1] - right[1]) < separation_m - 1e-6 for right in coords[index + 1:]):
            problems.append(f"layout: two AI points are closer than {separation_m:g} m")
            break
    return problems


def patrol_seeds(routes: list[dict[str, Any]], limit: int) -> list[tuple[float, float]]:
    """One point on the first vertex of each route.

    The SDK v1.15 guide links a bot to a waypoint set only when the spawn is
    within 5 m of that set's start. Placing the spawn on the start makes the
    link certain. Starts closer than 4 m share one bot.
    """
    seeds: list[tuple[float, float]] = []
    for route in routes:
        points = route.get("points") or []
        if len(points) < 2:
            continue
        seed = (float(points[0]["x"]), float(points[0]["y"]))
        if any(math.hypot(seed[0] - other[0], seed[1] - other[1]) < 4.0 for other in seeds):
            continue
        seeds.append(seed)
        if len(seeds) >= limit:
            break
    return seeds


def _xy(x: float, y: float, focus: tuple[float, float]) -> dict[str, float]:
    return {"x": round(x, 2), "y": round(y, 2), "yaw": yaw_toward(x, y, *focus)}


def _focus_xy(base: GeneratedScene, heights: Mapping[str, Any]) -> tuple[float, float] | None:
    names = {b["building_id"]: str(b.get("name") or "") for b in heights.get("buildings", []) if b.get("landmark")}
    centroids = []
    for obj in base.objects:
        if obj.type != "building" or not obj.visual:
            continue
        source = (obj.record.get("source_ids") or [None])[0]
        if source not in names:
            continue
        vertices = np.vstack([prim.mesh.vertices for prim in obj.visual])
        centroids.append((names[source], (float(vertices[:, 0].mean()), float(vertices[:, 1].mean()))))
    for name, point in centroids:
        if "catedral" in name.lower():
            return point
    if not centroids:
        return None
    return max((point for _, point in centroids), key=lambda p: (p[1], p[0]))


def _centerlines(geometry: BaseGeometry) -> list[LineString]:
    if isinstance(geometry, LineString):
        return [geometry]
    if isinstance(geometry, MultiLineString):
        return [part for part in geometry.geoms if isinstance(part, LineString)]
    return []


def _patrol_lines(ways: Sequence[LinearWay], sidewalk_width_m: float) -> list[LineString]:
    """Pedestrian centrelines, and two lines along each sidewalk of a street.

    OSM splits a street at every junction and tag change, so an alley is often
    a run of 10 to 15 m ways that each hold no route. Ways that continue one
    another are merged first: every pedestrian way together, and streets of the
    same width together. The carriageway is not walkable, so a street patrol
    runs along the middle of the sidewalk and also ``KERB_OFFSET_M`` past the
    kerb. That second line still lands on a narrow alley whose facades leave less
    than the configured sidewalk.
    """
    ordered = sorted(ways, key=lambda item: item.id)
    pedestrian = [line for way in ordered if way.road_class == "pedestrian" for line in _centerlines(way.geometry)]
    lines = _centerlines(linemerge(pedestrian))
    streets = [way for way in ordered if way.road_class != "pedestrian"]
    for width in sorted({way.width_m for way in streets}):
        street = [line for way in streets if way.width_m == width for line in _centerlines(way.geometry)]
        for line in _centerlines(linemerge(street)):
            for shift in (width / 2.0 + sidewalk_width_m / 2.0, width / 2.0 + KERB_OFFSET_M):
                for signed in (shift, -shift):
                    lines.extend(_centerlines(offset_curve(line, signed)))
    return lines


def _sample_route(line: Any, free: BaseGeometry, start_m: float, parts: Sequence[BaseGeometry], tree: STRtree) -> list[dict[str, float]]:
    """Up to ``ROUTE_POINTS`` points ``ROUTE_STEP_M`` apart along ``line``.

    A point on furniture is skipped. The first point fixes the walkable island,
    the piece of ``parts`` within the agent radius. The route stops at the first
    point off that island: the carriageway splits the navmesh, and a step to
    another island has no return path, so the adapter drops it.
    """
    points: list[dict[str, float]] = []
    island: BaseGeometry | None = None
    distance = start_m
    while distance < line.length - 2 and len(points) < ROUTE_POINTS:
        point = line.interpolate(distance)
        distance += ROUTE_STEP_M
        if not free.covers(point):
            continue
        if island is None:
            hits = sorted(int(index) for index in tree.query(point, predicate="dwithin", distance=NAV_AGENT_RADIUS_M))
            if not hits:
                continue
            island = parts[hits[0]]
        elif not dwithin(island, point, NAV_AGENT_RADIUS_M):
            break
        points.append({"x": round(float(point.x), 2), "y": round(float(point.y), 2)})
    return points


def _routes(
    base: GeneratedScene, free: BaseGeometry, parts: Sequence[BaseGeometry], separation_m: float = SPAWN_SEPARATION_M,
) -> list[dict[str, Any]]:
    """Patrols on pedestrian ways and along the sidewalks of each street.

    A long line is cut into several routes whose starts are at least
    ``separation_m`` apart. Each route begins on walkable ground inside one of
    ``parts`` and keeps the points ahead of that start on the same piece. A
    second start on the same corner is dropped. The carriageway itself is not
    used: bots spawn on the sidewalk.
    """
    tree = STRtree(list(parts))
    candidates: list[list[dict[str, float]]] = []
    for line in _patrol_lines(base.ways, base.config.sidewalk_width_m):
        distance = 4.0
        while distance <= line.length - 16:  # pragma: no mutate: a start on the bound holds one point, which the length check drops; test_routes_start_every_separation_until_the_last_two_points pins 16
            points = _sample_route(line, free, distance, parts, tree)
            if len(points) >= 2:
                candidates.append(points)
            distance += separation_m
    candidates.sort(key=lambda points: (points[0]["x"], points[0]["y"], points[-1]["x"], points[-1]["y"]))
    routes: list[dict[str, Any]] = []
    for points in candidates:
        start = points[0]
        if any(math.hypot(start["x"] - kept["points"][0]["x"], start["y"] - kept["points"][0]["y"]) < separation_m - 1e-6 for kept in routes):
            continue
        routes.append({"points": points})
    return routes


def fit_spawn_separation(
    base: GeneratedScene, free: BaseGeometry, parts: Sequence[BaseGeometry], count: int,
) -> tuple[list[dict[str, Any]], float]:
    """Largest gap in ``[5.1, 8]`` m that still yields ``count`` route starts on ``parts``.

    The search steps down by 0.1 m from 8 m. A map that already holds the roster
    keeps 8 m. The floor stays above the 5 m patrol link.
    """
    separation = SPAWN_SEPARATION_M
    held = 0
    while separation >= MIN_SPAWN_SEPARATION_M - 1e-9:
        routes = _routes(base, free, parts, separation)
        held = len(routes)
        if held >= count:
            return routes, round(separation, 2)
        separation = round(separation - SEPARATION_STEP_M, 2)
    raise LayoutError(f"route starts hold {held} spawns at {MIN_SPAWN_SEPARATION_M:g} m; {count} are required")


def nav_islands(base: GeneratedScene, inside: BaseGeometry | None = None) -> list[BaseGeometry]:
    """The walkable ground the adapter bakes, shrunk by the agent radius, one piece per island.

    Furniture is not a navmesh obstacle, so it does not split an island here.
    The walkable crossings over the carriageway are baked too (ADR-015), so
    the blocks on both sides of a street are one island. ``inside`` is the
    playable core: the ground colliders end at its wall (ADR-017).
    """
    free = free_ground(base, [])  # pragma: no mutate: free_ground reads [] and None alike, as no furniture
    ground = union_all([free, base.partition.crossings])
    if inside is not None:
        ground = ground.intersection(inside)
    return list(polygons_of(ground.buffer(-NAV_AGENT_RADIUS_M)))


def navigation_estimate(islands: Sequence[BaseGeometry]) -> dict[str, float]:
    """The adapter's first node spacing for this navmesh: ``max(1, span / 400, sqrt(area / 320))``.

    An estimate from the 2D islands; the adapter measures the baked mesh and
    raises the spacing while the walk is deeper than 400.
    """
    area = sum(part.area for part in islands)
    minx, miny, maxx, maxy = union_all(list(islands)).bounds
    span = max(maxx - minx, maxy - miny)
    spacing = max(1.0, span / NAV_WALK_DEPTH, math.sqrt(area / NAV_NODE_BUDGET))
    return {"walkable_area_m2": round(area, 1), "span_m": round(span, 1), "node_spacing_estimate_m": round(spacing, 2)}


def hunt_routes(
    base: GeneratedScene, free: BaseGeometry, islands: Sequence[BaseGeometry], home: BaseGeometry, count: int, focus: tuple[float, float],
) -> tuple[list[dict[str, Any]], float]:
    """``count`` patrols, on the navmesh island that holds the origin when it fits them.

    ``GenerateNodes`` keeps only the nodes that reach the navigation origin, so
    a patrol on another island has no node to walk on. When ``home`` cannot hold
    ``count`` starts, every route on it is kept and the rest are spread over the
    other islands, as before, so the roster still has one route per bot.
    """
    try:
        routes, separation = fit_spawn_separation(base, free, [home], count)
        return spread_routes(routes, count, focus), separation
    except LayoutError:
        pass
    routes, separation = fit_spawn_separation(base, free, islands, count)
    near = [route for route in routes if dwithin(home, Point(route["points"][0]["x"], route["points"][0]["y"]), NAV_AGENT_RADIUS_M)]  # pragma: no mutate: every point of a route lies on the island of its start, so any index finds the same island
    chosen = near[:count]
    rest = [route for route in routes if route not in near]
    return chosen + spread_routes(rest, count - len(chosen), focus), separation


def spread_routes(routes: list[dict[str, Any]], count: int, origin: tuple[float, float]) -> list[dict[str, Any]]:
    """Keep ``count`` patrols spread over the map, starting from the one nearest ``origin``.

    Taking the first routes in coordinate order fills the western edge and leaves
    the plaza empty. Farthest-point sampling keeps one patrol near the focus and
    then the starts that sit farthest from the ones already chosen.
    """
    if count <= 0:
        return []
    if len(routes) <= count:
        return list(routes)

    def xy(route: dict[str, Any]) -> tuple[float, float]:
        point = route["points"][0]
        return float(point["x"]), float(point["y"])

    remaining = list(routes)
    first = min(remaining, key=lambda route: (math.hypot(xy(route)[0] - origin[0], xy(route)[1] - origin[1]), xy(route)))
    chosen = [first]
    remaining.remove(first)
    while len(chosen) < count and remaining:
        def gap(route: dict[str, Any]) -> float:
            x, y = xy(route)
            return min(math.hypot(x - xy(other)[0], y - xy(other)[1]) for other in chosen)

        nxt = max(remaining, key=lambda route: (gap(route), xy(route)))
        chosen.append(nxt)
        remaining.remove(nxt)
    return chosen


TIME_OF_DAY_SETTINGS: Mapping[str, tuple[str, str]] = {"day": ("Day", "UrbanDay1"), "night": ("Night", "UrbanNight1")}
"""``time_of_day`` → SDK ``ETimeOfDay`` and ``EProfileId`` names (``LevelDescriptor.cs``, SDK v1.15)."""
STREET_LIGHTS_MAX = 64
"""As many as the schema allows interior lights. A baked light costs bake time, not frame time."""
LAMP_HEAD_REACH_M = 1.0
LAMP_LIGHT_HEIGHT_M = 4.2
"""Under the head of the ``street_lamp`` prototype (``detail/modules.py``): 1 m along its arm, just below 4.25 m."""


def street_lights(detail_manifest: Mapping[str, Any], focus: tuple[float, float]) -> list[float]:
    """Flat ``x, y, z`` triples of a light under each street lamp's head, nearest the focus first, at most ``STREET_LIGHTS_MAX``."""
    heads: list[tuple[float, float]] = []
    for element in detail_manifest.get("objects", []):
        if element.get("type") == "urban_element" and element.get("kind") == "street_lamp":
            x, y = element["position_m"][:2]
            yaw = math.radians(float(element.get("yaw_deg", 0.0)))
            heads.append((x + LAMP_HEAD_REACH_M * math.cos(yaw), y + LAMP_HEAD_REACH_M * math.sin(yaw)))
    heads.sort(key=lambda head: (math.dist(head, focus), head))
    return [value for x, y in heads[:STREET_LIGHTS_MAX] for value in (round(x, 2), round(y, 2), LAMP_LIGHT_HEIGHT_M)]


ROOF_WIND_MAX = 8
"""A local source per reachable roof would be many in a dense map; these are few at a time, like the other ambience."""
ROOF_WIND_SPACING_M = 35.0
"""The radius of ``Wind Rooftop`` (``OnwardAudioDataStrings.cs``, SDK v1.15). A roof closer than this already hears a source."""
ROOF_WIND_ABOVE_M = 1.5
"""About the head of a player standing on the roof."""


def roof_wind(detail_manifest: Mapping[str, Any], focus: tuple[float, float], climbed: Sequence[tuple[float, float, float]] = ()) -> list[float]:
    """Flat ``x, y, z`` triples of wind above the roofs a player can reach by stair or by ``climbed``, nearest the focus first."""
    objects = detail_manifest.get("objects", [])
    heights = {o["id"]: float(o["height"]["height_m"]) for o in objects if o.get("type") == "building"}
    roofs: list[tuple[float, float, float]] = list(climbed)
    for interior in objects:
        if interior.get("type") == "interior" and interior.get("roof_access"):
            x, y = interior["connections"][-1]["start_m"]
            roofs.append((x, y, heights[f"building/{interior['source_ids'][0]}"]))
    roofs.sort(key=lambda roof: (math.dist(roof[:2], focus), roof))
    chosen: list[tuple[float, float, float]] = []
    for roof in roofs:
        if len(chosen) < ROOF_WIND_MAX and all(math.dist(roof[:2], other[:2]) >= ROOF_WIND_SPACING_M for other in chosen):
            chosen.append(roof)
    return [value for x, y, height in chosen for value in (round(x, 2), round(y, 2), round(height + ROOF_WIND_ABOVE_M, 2))]


CLIMB_KEEP_OUT_M = 3.0
"""A cabin or a ladder's foot stays this far from a spawn, an objective, a box and an interior's door."""
CLIMB_ROUTE_KEEP_OUT_M = 1.5
"""The navmesh is baked without them, so a bot's route must not cross them."""
CLIMB_PROP_CLEAR_M = 1.0
"""From the spot of an overgrow prop: the radius ``free_ground`` gives a detail urban element. An estimate, not a measured footprint."""
CLIMB_TREE_CLEAR_M = 2.0
"""From an overgrow tree: a generated crown is about 3.8 m across, and it once stood in a street cabin."""
CLIMB_FLAT_GROWTH = frozenset({"grass", "vine", "moss"})
"""Overgrowth a cabin may stand on or beside: flat on the ground or on a facade."""


def ammo_points(
    area: BaseGeometry,
    ai: Sequence[tuple[float, float]],
    spawns: Sequence[tuple[float, float]],
    origin: tuple[float, float],
) -> list[tuple[float, float]]:
    """``AMMO_COUNT`` crates, 3 m clear of the AI starts when there is room, and always ``AMMO_SPAWN_CLEAR_M`` from every spawn point.

    The first crate goes to the pole of the ground, which is where the attackers' block was put.
    """
    spawn_clear = union_all([Point(p).buffer(_keep_out(AMMO_SPAWN_CLEAR_M), quad_segs=_BUFFER_RESOLUTION) for p in spawns])  # pragma: no mutate: shapely's default quad_segs is 16, the same polygon
    open_ground = area.difference(spawn_clear)
    region = open_ground.difference(union_all([Point(p).buffer(3.0) for p in ai]))
    return scatter_points(region if not region.is_empty else open_ground, AMMO_COUNT, 6.0, origin)


def climb_keep_out(points: Sequence[tuple[float, float]], routes: Sequence[Mapping[str, Any]], detail_manifest: Mapping[str, Any]) -> BaseGeometry:
    """Where neither a ladder's foot nor a cabin may stand: around ``points``, the interiors' street doors, the bots' routes
    and the overgrow props. The layout runs after ``overgrow``, which knew nothing of the cabins: bricks and a tree stood in one."""
    objects = detail_manifest.get("objects", [])
    doors = [
        tuple(connection["position_m"])
        for interior in objects if interior.get("type") == "interior"
        for connection in interior.get("connections", []) if connection.get("from") == "exterior"
    ]
    props = [
        Point(prop["position_m"]).buffer(CLIMB_TREE_CLEAR_M if "tree" in (prop.get("kind"), prop.get("role")) else CLIMB_PROP_CLEAR_M)
        for prop in objects if prop.get("type") == "overgrowth" and prop.get("kind") not in CLIMB_FLAT_GROWTH
    ]
    return union_all([
        *[Point(p).buffer(CLIMB_KEEP_OUT_M) for p in (*points, *doors)],
        *[LineString([(p["x"], p["y"]) for p in route["points"]]).buffer(CLIMB_ROUTE_KEEP_OUT_M) for route in routes],
        *props,
    ])


def playable_layout(base: GeneratedScene, detail_manifest: Mapping[str, Any], heights: Mapping[str, Any], *, time_of_day: str = "day", storm: bool = False, footprints: Mapping[str, BaseGeometry] | None = None) -> dict[str, Any]:
    """Spawns, Hunt anchors, SDK audio names and the urban profile of ``time_of_day``.

    The measurement fixture stays ``perf_layout``. This document is what a
    playable package exports. There is one AI point per route, on that route's
    start. The gap is 8 m when the sidewalks hold 32 starts, and otherwise the
    largest 0.1 m step that still does, down to 5.1 m. The SDK only patrols a
    bot that spawns within 5 m of a start, and a second bot on the same start is
    the pile seen in game. Audio names are copied from the SDK v1.15 library. A night map has no
    birds and a light under each street lamp (ADR-026).
    Rules that the adapter applies from the same guide live in ``docs/11-onward-sdk.md``.
    """
    if time_of_day not in TIME_OF_DAY_SETTINGS:
        raise LayoutError(f"time_of_day must be one of {', '.join(sorted(TIME_OF_DAY_SETTINGS))}, not {time_of_day!r}")
    if storm and time_of_day != "night":
        raise LayoutError("storm needs a night map: a daytime flash looks wrong (ADR-028)")
    sdk_time, profile = TIME_OF_DAY_SETTINGS[time_of_day]
    doc = gameplay_layout(base, detail_manifest, farthest=True)
    urban = [tuple(o["position_m"]) for o in detail_manifest.get("objects", []) if o.get("type") == "urban_element"]
    free = free_ground(base, urban)
    inside = playable_area(detail_manifest)
    if inside is not None:
        free = free.intersection(inside)
    region = free.buffer(-1.0)
    parts = [p for p in polygons_of(region) if p.area > 0]
    if not parts:
        raise LayoutError("no walkable ground with 1 m clearance for AI and ammo")
    uplink = tuple(doc["uplink_m"])
    area = component_holding(parts, uplink)
    focus = _focus_xy(base, heights) or uplink

    islands = nav_islands(base, inside)
    routes, separation = hunt_routes(base, free, islands, component_holding(islands, uplink), AI_COUNT, focus)
    ai = patrol_spawns(routes, AI_COUNT, separation_m=separation)
    attack = (float(doc["attackers"]["centre_m"][0]), float(doc["attackers"]["centre_m"][1]))
    defend = (float(doc["defenders"]["centre_m"][0]), float(doc["defenders"]["centre_m"][1]))
    attack_b = extra_spawn_block(area, attack, [attack, defend])
    defend_b = extra_spawn_block(area, defend, [attack, defend, attack_b])
    spawn_pts = [point for centre in (attack, defend, attack_b, defend_b) for point in _block(*centre)]
    occupied_pts = [uplink, *spawn_pts]

    ammo = ammo_points(area, ai, spawn_pts, uplink)

    def with_yaw(block: dict[str, Any]) -> dict[str, Any]:
        return {
            "centre_m": block["centre_m"],
            "points_m": [_xy(p["x"], p["y"], focus) for p in block["points_m"]],
        }
    open_ground = area.difference(union_all([Point(p).buffer(2.0) for p in occupied_pts]))
    if open_ground.is_empty:
        raise LayoutError("no ground left for Escort, Evac and Retrieval")
    escort = scatter_points(open_ground, 2, 8.0, uplink)
    rest = open_ground.difference(union_all([Point(p).buffer(4.0) for p in escort]))
    if rest.is_empty:
        rest = open_ground
    minx, miny, maxx, maxy = area.bounds
    far = max(
        ((minx, miny), (minx, maxy), (maxx, miny), (maxx, maxy)),
        key=lambda corner: math.hypot(corner[0] - uplink[0], corner[1] - uplink[1]),
    )
    evac = scatter_points(rest, 1, 8.0, far)[0]
    after_evac = rest.difference(Point(evac).buffer(4.0))
    drives = scatter_points(after_evac if not after_evac.is_empty else rest, HARDDRIVE_COUNT, 8.0, focus)

    def spawn_block(centre: tuple[float, float]) -> dict[str, Any]:
        return {"centre_m": [centre[0], centre[1]], "points_m": [_xy(x, y, focus) for x, y in _block(*centre)]}

    gameplay_points = [uplink, *ai, *ammo, *escort, evac, *drives, *occupied_pts]
    keep_out = climb_keep_out(gameplay_points, routes, detail_manifest)
    climbs = place_climbs(detail_manifest, footprints or {}, free, keep_out, (attack, attack_b), (defend, defend_b))

    doc.update({
        "schema": GAMEPLAY_SCHEMA,
        "schema_version": GAMEPLAY_VERSION,
        "purpose": "playable layout: Free Roam, Uplink, Assault, Escort, Hunt, Evac and Retrieval",
        "modes": dict(MODE_CODES),
        "attackers_b": spawn_block(attack_b),
        "defenders_b": spawn_block(defend_b),
        "escort_a_m": [round(escort[0][0], 2), round(escort[0][1], 2)],
        "escort_b_m": [round(escort[1][0], 2), round(escort[1][1], 2)],
        "evac_m": [round(evac[0], 2), round(evac[1], 2)],
        "harddrives_m": [_xy(*point, focus) for point in drives],
        "focus_m": [round(focus[0], 2), round(focus[1], 2)],
        "attackers": with_yaw(doc["attackers"]),
        "defenders": with_yaw(doc["defenders"]),
        "ai_m": [_xy(*p, focus) for p in ai],
        "ai_separation_m": separation,
        "routes": routes,
        "navigation": navigation_estimate(islands),
        "ammo_m": [_xy(*p, focus) for p in ammo],
        "audio_global": "Suburban Day",
        "audio_birds_m": list(uplink) if time_of_day == "day" else [],
        "audio_wind_m": [round(focus[0], 2), round(focus[1], 2)],
        "audio_roof_wind_m": roof_wind(detail_manifest, focus, climbed_roofs(climbs)),
        **climbs,
        "environment": "Urban",
        "time_of_day": sdk_time,
        "profile": profile,
        "street_lights_m": street_lights(detail_manifest, focus) if time_of_day == "night" else [],
        "far_clip_m": 1000,
        "flyover": 1,
        "lightning": 1 if storm else 0,
    })
    return doc


def load_detail_manifest(detail_dir: str | Path) -> dict[str, Any]:
    path = Path(detail_dir).expanduser() / "scene_detail.json"
    if not path.is_file():
        raise LayoutError(f"{path} not found")
    return json.loads(path.read_text(encoding="utf-8"))
