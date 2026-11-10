"""Ladders and lifts to the roofs of buildings a stair does not reach (ADR-029).

A roof is a strong position in a match with many players, so an access is placed
where it is fair and does not get in the way: never near a spawn, on the building
closest to equidistant between the teams, and clear of routes and objectives.
Sizes are from the SDK v1.15 prefabs. Whether they play well is checked on the headset.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from itertools import pairwise
from typing import Any

from shapely.geometry import Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient

from onward_generator.bounds.envelope import DEFAULT_HEIGHT_M, DEFAULT_THICKNESS_M
from onward_generator.tiers import CORE, core_of

LADDERS_MAX = 3
LIFTS_MAX = 3
"""The request caps ladders so as not to saturate a map. A lift pair is two choke points, so it gets the same cap."""
LADDER_MAX_STOREYS = 3
LIFT_MIN_STOREYS = 3
LIFT_MAX_STOREYS = 6
"""A higher roof overlooks every street of the map, and a lift would carry a squad there in seconds."""
STOREY_M = 3.0
"""To read a measured height as storeys."""
FLAT_ROOFS = ("flat", "flat_parapet")
"""A player cannot land on a pitched roof, and a cabin cannot stand on one."""

SPAWN_CLEAR_M = 30.0
"""No access this close to a spawn: a roof above a spawn is spawn killing. Same value as ``TEAM_SEPARATION_M``."""
ACCESS_SPACING_M = 30.0
"""Accesses spread over the map, so a match does not meet at one corner."""
BALANCE_STEP_M = 10.0
"""Differences in distance to the two teams smaller than this count as equally fair; the lower roof then wins."""

LADDER_WALL_GAP_M = 0.15
"""From the wall to the ladder's plane. ``VaultTargetUpper`` is 0.91 m ahead, so a player lands 0.76 m inside the roof."""
LADDER_WIDTH_M = 1.6
LADDER_FOOT_M = 2.0
"""Clear ground at the foot, enough for a few players to wait."""
LANDING_DEPTH_M = 1.5

CABIN_WIDTH_M = 3.2
CABIN_DEPTH_M = 3.2
"""The SDK cabin is about 3.01 x 3.13 m."""
CABIN_WALL_GAP_M = 0.05
CABIN_APRON_M = 3.0
"""Clear in front of the door (the cabin's local +Z), so the queue does not close the street."""
ROOF_EDGE_M = 0.6
"""A cabin stands behind the parapet (0.3 m at most) and leaves a step of roof."""
ROOF_APRON_M = 2.0
CENTRE_DIGITS = 2
COVER_TOLERANCE_M = 0.01
"""A strip that ends on the wall of a turned building misses the roof by rounding alone."""
ROOF_MIN_M2 = 30.0
"""The least roof inside the parapet an access leads to: room for a squad to stand and move, about 5 x 6 m.

Barcelona Sarria: ladders led to roofs of 4.3 to 17.7 m2 under walls 4 to 12 m taller, dead ends for a player.
Estimated, not measured on the headset.
"""
SLIDE_STEP_M = 0.5
"""How far a ladder or a cabin moves along its wall at each try, away from what it must keep clear of."""
WALL_JUMP_CLEAR_M = 6.0
"""A roof above the perimeter wall stays this far from the core edge, so no one jumps from it over the wall.

Estimated (a run off a roof a few metres above the wall top), not measured on the headset.
"""

Wall = tuple[float, float, float, float, float]
"""x, y of the middle of a wall, its unit outward normal, and its length."""


def storeys(building: Mapping[str, Any]) -> int:
    """The levels, or what the height implies when that is more.

    A measured height and a cadastral level count can disagree: a roof of 22.9 m with 3 levels is a
    22.9 m climb, and the rules are about the climb.
    """
    height = building["height"]
    return max(int(height.get("estimated_levels") or 1), round(float(height["height_m"]) / STOREY_M))


def _walls(footprint: Polygon) -> list[Wall]:
    """The outer walls, longest first. The ring turns counter-clockwise, so outward is to the right of each edge."""
    coords = list(orient(footprint).exterior.coords)
    walls: list[Wall] = []
    for (ax, ay), (bx, by) in pairwise(coords):
        length = math.hypot(bx - ax, by - ay)
        if length > 0:
            walls.append(((ax + bx) / 2, (ay + by) / 2, (by - ay) / length, (ax - bx) / length, length))
    return sorted(walls, key=lambda wall: (-wall[4], wall[0], wall[1]))


def _strip(wall: Wall, width_m: float, near_m: float, far_m: float) -> Polygon:
    """A rectangle on the wall's middle, from ``near_m`` to ``far_m`` along the outward normal (negative is inside)."""
    x, y, nx, ny, _ = wall
    ax, ay = -ny * width_m / 2, nx * width_m / 2
    near = (x + nx * near_m, y + ny * near_m)
    far = (x + nx * far_m, y + ny * far_m)
    return Polygon([(near[0] - ax, near[1] - ay), (near[0] + ax, near[1] + ay), (far[0] + ax, far[1] + ay), (far[0] - ax, far[1] - ay)])


def _along(wall: Wall, distance_m: float) -> tuple[float, float]:
    return wall[0] + wall[2] * distance_m, wall[1] + wall[3] * distance_m


def _slid(wall: Wall, width_m: float) -> list[Wall]:
    """The wall with its middle moved along it in ``SLIDE_STEP_M`` steps, nearest the middle first, the strip still on the wall.

    A ladder or a cabin at the middle may stand over a door; Barcelona Sarria has 827 ground-floor doors in its core.
    """
    x, y, nx, ny, length = wall
    steps = max(0, int((length - width_m) / 2 / SLIDE_STEP_M))
    shifts = dict.fromkeys(s for k in range(steps + 1) for s in (k * SLIDE_STEP_M, -k * SLIDE_STEP_M))
    return [(x - ny * s, y + nx * s, nx, ny, length) for s in shifts]


def _yaw(nx: float, ny: float) -> float:
    """Unity yaw of a forward along ``(nx, ny)``: 0 looks along local +y."""
    return round(math.degrees(math.atan2(nx, ny)), 1)


def deck(footprint: Polygon, parapet_thickness_m: float) -> BaseGeometry:
    """The roof a player stands on: the footprint inside its parapet."""
    return footprint.buffer(-parapet_thickness_m, join_style="mitre", mitre_limit=2.0)


def ladder_on(
    footprint: Polygon, free: BaseGeometry, keep_out: BaseGeometry, height_m: float, parapet_m: float = 0.0, parapet_thickness_m: float = 0.0,
) -> dict[str, float] | None:
    """A ladder on the longest wall with clear ground at its foot and roof behind it. The ladder faces the wall.

    ``parapet_m`` is what the ladder has to rise above the roof, so its top rungs stand clear of the parapet. The
    landing lies on the roof inside the parapet (``parapet_thickness_m``): on Barcelona Sarria the side parapets of a
    narrow roof left 1.45 m for the 1.6 m ladder.
    """
    roof = deck(footprint, parapet_thickness_m).buffer(COVER_TOLERANCE_M)
    for wall in (slid for side in _walls(footprint) for slid in _slid(side, LADDER_WIDTH_M)):
        foot = _strip(wall, LADDER_WIDTH_M, CABIN_WALL_GAP_M, CABIN_WALL_GAP_M + LADDER_FOOT_M)
        landing = _strip(wall, LADDER_WIDTH_M, -(parapet_thickness_m + LANDING_DEPTH_M), -parapet_thickness_m)
        if free.covers(foot) and not keep_out.intersects(foot) and roof.covers(landing):
            x, y = _along(wall, LADDER_WALL_GAP_M)
            roof_x, roof_y = _along(wall, -(parapet_thickness_m + LANDING_DEPTH_M / 2))
            return {
                "x": round(x, CENTRE_DIGITS), "y": round(y, CENTRE_DIGITS), "yaw": _yaw(-wall[2], -wall[3]),
                "height_m": round(height_m, CENTRE_DIGITS), "parapet_m": parapet_m,
                "roof_x": round(roof_x, CENTRE_DIGITS), "roof_y": round(roof_y, CENTRE_DIGITS),
            }
    return None


def lift_on(
    footprint: Polygon, free: BaseGeometry, keep_out: BaseGeometry, height_m: float, parapet_thickness_m: float = 0.0,
) -> dict[str, float] | None:
    """A cabin against the longest wall that has room for it in the street and on the roof above. Both doors face away from the wall line.

    The roof cabin and its apron lie on the roof inside the parapet (``parapet_thickness_m``): on Barcelona Sarria an
    apron ran 0.26 m into the far parapet of an oblique roof.
    """
    roof = deck(footprint, parapet_thickness_m).buffer(COVER_TOLERANCE_M)
    street_far = CABIN_WALL_GAP_M + CABIN_DEPTH_M
    for wall in (slid for side in _walls(footprint) for slid in _slid(side, CABIN_WIDTH_M)):
        street = _strip(wall, CABIN_WIDTH_M, CABIN_WALL_GAP_M, street_far + CABIN_APRON_M)
        top = _strip(wall, CABIN_WIDTH_M, -(ROOF_EDGE_M + CABIN_DEPTH_M + ROOF_APRON_M), -ROOF_EDGE_M)
        if free.covers(street) and not keep_out.intersects(street) and roof.covers(top):
            x, y = _along(wall, CABIN_WALL_GAP_M + CABIN_DEPTH_M / 2)
            roof_x, roof_y = _along(wall, -(ROOF_EDGE_M + CABIN_DEPTH_M / 2))
            return {
                "x": round(x, CENTRE_DIGITS), "y": round(y, CENTRE_DIGITS), "yaw": _yaw(wall[2], wall[3]),
                "height_m": round(height_m, CENTRE_DIGITS), "roof_x": round(roof_x, CENTRE_DIGITS), "roof_y": round(roof_y, CENTRE_DIGITS),
                "roof_yaw": _yaw(-wall[2], -wall[3]),
            }
    return None


def _inside_wall(detail_manifest: Mapping[str, Any], footprint: BaseGeometry, height_m: float) -> bool:
    """Whether a roof stays in the playable core (ADR-017): the whole footprint inside the wall, and a roof above
    the wall top at least ``WALL_JUMP_CLEAR_M`` from the core edge. A map without a core keeps every roof."""
    core = core_of(detail_manifest)
    if core is None:
        return True
    envelope = detail_manifest.get("envelope") or {}
    if not core.buffer(-float(envelope.get("thickness_m") or DEFAULT_THICKNESS_M)).covers(footprint):
        return False
    return height_m <= float(envelope.get("height_m") or DEFAULT_HEIGHT_M) or core.exterior.distance(footprint) >= WALL_JUMP_CLEAR_M


def _candidates(detail_manifest: Mapping[str, Any], footprints: Mapping[str, BaseGeometry]) -> list[tuple[str, dict[str, Any], Polygon]]:
    objects = detail_manifest.get("objects", [])
    climbed = {o["source_ids"][0] for o in objects if o.get("type") == "interior" and o.get("roof_access")}
    found = []
    for building in objects:
        # Outside the core (scenic, or the plan D3 mid ring) no player can reach a roof.
        if building.get("type") != "building" or building.get("tier", CORE) != CORE:
            continue
        source = building["source_ids"][0]
        if source in climbed or source not in footprints or building["height"].get("landmark"):
            continue
        if not _inside_wall(detail_manifest, footprints[source], float(building["height"]["height_m"])):
            continue
        if building["roof"]["type"] in FLAT_ROOFS:
            footprint = footprints[source]
            largest = max(getattr(footprint, "geoms", [footprint]), key=lambda part: part.area)
            if deck(largest, _parapet_thickness(building)).area >= ROOF_MIN_M2:
                found.append((source, building, largest))
    return found


def _parapet_thickness(building: Mapping[str, Any]) -> float:
    return float(building["roof"].get("parapet_thickness_m", 0.0))


def _fairness(footprint: Polygon, attackers: Sequence[tuple[float, float]], defenders: Sequence[tuple[float, float]]) -> float:
    nearest = [min(footprint.distance(Point(p)) for p in team) for team in (attackers, defenders)]
    return abs(nearest[0] - nearest[1])


def place_climbs(
    detail_manifest: Mapping[str, Any],
    footprints: Mapping[str, BaseGeometry],
    free: BaseGeometry,
    keep_out: BaseGeometry,
    attackers: Sequence[tuple[float, float]],
    defenders: Sequence[tuple[float, float]],
) -> dict[str, list[dict[str, float]]]:
    """``ladders`` on buildings of at most 3 storeys with no interior, ``lifts`` on buildings of 3 to 6.

    A building gets one access. Ladders choose first because their rule is the narrower one.
    """
    spawns = [*attackers, *defenders]
    ranked = sorted(
        (row for row in _candidates(detail_manifest, footprints) if min(row[2].distance(Point(p)) for p in spawns) >= SPAWN_CLEAR_M),
        key=lambda row: (round(_fairness(row[2], attackers, defenders) / BALANCE_STEP_M), float(row[1]["height"]["height_m"]), row[0]),
    )
    ladders: list[dict[str, float]] = []
    lifts: list[dict[str, float]] = []
    used: set[str] = set()

    def spaced(site: Mapping[str, float]) -> bool:
        return all(math.dist((site["x"], site["y"]), (other["x"], other["y"])) >= ACCESS_SPACING_M for other in (*ladders, *lifts))

    for source, building, footprint in ranked:
        if len(ladders) < LADDERS_MAX and storeys(building) <= LADDER_MAX_STOREYS and not building.get("interior"):
            site = ladder_on(footprint, free, keep_out, float(building["height"]["height_m"]), float(building["roof"].get("parapet_m", 0.0)),
                             _parapet_thickness(building))
            if site is not None and spaced(site):
                ladders.append(site)
                used.add(source)
    for source, building, footprint in ranked:
        if source not in used and len(lifts) < LIFTS_MAX and LIFT_MIN_STOREYS <= storeys(building) <= LIFT_MAX_STOREYS:
            site = lift_on(footprint, free, keep_out, float(building["height"]["height_m"]), _parapet_thickness(building))
            if site is not None and spaced(site):
                lifts.append(site)
    return {"ladders": ladders, "lifts": lifts}


def climbed_roofs(climbs: Mapping[str, Iterable[Mapping[str, float]]]) -> list[tuple[float, float, float]]:
    """``x, y, height`` of each roof the climbs reach."""
    return [(site["roof_x"], site["roof_y"], site["height_m"]) for kind in ("ladders", "lifts") for site in climbs[kind]]
