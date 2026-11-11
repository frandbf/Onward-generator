"""Cover on long open sightlines: where to stand solid catalogue props (plan C7, ADR-032 C7 amendment, off by default).

Pure functions. The targets are the route samples the gameplay report counts as open past
``MAX_SIGHTLINE_M`` (``report.open_sight_points``). For each, in that order, the ranked cover props are
tried at a few fixed spots within ``COVER_REACH_M`` of the sample, with the long side across the ray.
A spot is kept only when it stays out of every keep-out and leaves ``PASSAGE_M`` of walkable width: the
ground near it, less the solid colliders already there, eroded by half the passage, keeps every place it
reached joined (the ADR-031 test). After the greedy pass the same test runs on the whole playable ground;
while it fails, the last prop placed is dropped. Rules only, no model, no randomness: the order of the
targets, props and spots decides every tie, so two runs give the same plan.

Every figure is estimated and uncalibrated until the playtest (plan 4.2); none blocks a stage.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
from shapely import LineString, Point, STRtree, union_all
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry

from onward_generator.gameplay.metrics import COVER_REACH_M, MAX_SIGHTLINE_M, SIGHT_BLOCK_HEIGHT_M
from onward_generator.gameplay.report import OpenPoint
from onward_generator.generation.mesh import Mesh, polygons_of
from onward_generator.overgrow.ambient import COVER_DEPTH_M, COVER_HEIGHT_M, is_solid
from onward_generator.overgrow.constants import MAX_ADDED_TRIS
from onward_generator.overgrow.dressing import block_reason
from onward_generator.overgrow.fit import PASSAGE_M, PROP_OVERLAP_M2, _footprint, object_reason
from onward_generator.overgrow.ground import RoadFloor
from onward_generator.overgrow.library import Prop
from onward_generator.overgrow.zone import Zone
from onward_generator.perf.layout import AMMO_SPAWN_CLEAR_M, CLIMB_KEEP_OUT_M, CLIMB_ROUTE_KEEP_OUT_M, NAV_AGENT_RADIUS_M

MAX_COVER_PROPS = 24
"""Props one map may receive: almost every bench stretch is open past 60 m, so a cap is needed."""
COVER_MAX_ADDED_TRIS = 12_000
"""Triangles cover may add, and never more than what overgrow left of ``MAX_ADDED_TRIS``."""
COVER_MAX_ADDED_DRAWS = 24
"""Primitives cover may add (one per albedo part of each copy): an upper bound on the added draws before combining."""
MAX_NEW_COVER_ASSETS = 2
"""Catalogue props not already in the scene that cover may bring in: each one adds its own material."""
MAX_PROP_LENGTH_M = 3.0
"""A longer prop is a wall across the street, not cover."""
TARGET_SPACING_M = 12.0
"""A target this close to a prop already placed (centre to centre, strictly) is skipped."""
SPOT_ALONG_M = (1.0, 0.0)
"""Spots ahead of the sample along the open ray, nearest the ray's far end first."""
SPOT_ACROSS_M = (0.0, 1.0, -1.0)
"""Spots across the ray, at each distance along it."""
CONNECT_RADIUS_M = 6.0
"""The local passage test looks at the ground within this distance of a spot."""
ERODE_EPS_M = 1e-3
"""The erosion is half the passage less this, so a gap of exactly ``PASSAGE_M`` stays open."""
MIN_PART_M2 = 0.01
"""Eroded pieces smaller than this are slivers of the buffer, not places."""
COVER_ROLES = frozenset({"barrier", "barrel", "wreck", "debris", "fence"})
"""The street props cover may use. Never a building or a wall, which would invent a structure (AGENTS.md)."""

NO_TARGET = f"no open sightline over {MAX_SIGHTLINE_M:g} m"
NO_PROP = "no solid catalogue prop meets the cover rule"
NO_SPOT = "no valid spot"
NO_CAP = "max props is 0"
# perf cover --max-props: the counts a street profile may ask for (plan D2); MAX_COVER_PROPS stays the default.
MAX_PROPS_CHOICES = (0, 8, 16, MAX_COVER_PROPS)


@dataclass(frozen=True, slots=True)
class CoverProp:
    """A catalogue prop at street size that meets the cover rule. ``local`` puts its box centre on the origin,
    its base on z = 0 and its long side on x; ``length_m`` ≥ ``depth_m``."""

    prop: Prop
    licence: str
    in_scene: bool
    local: np.ndarray
    length_m: float
    depth_m: float
    height_m: float

    @property
    def asset_id(self) -> str:
        return self.prop.spec.id

    @property
    def triangles(self) -> int:
        return self.prop.triangles

    @property
    def parts(self) -> int:
        return len(self.prop.images())

    def key(self) -> tuple[bool, bool, bool, int, str]:
        """A barrier first, then in the scene (its material is reused), then tall enough to block sight, then fewer
        triangles, then id.

        A barrier is made to stand in a street. On Rue Mouffetard the old order put a bare 1.3 m concrete cube,
        overgrow's debris, in the middle of a clean pedestrian street five times (plan Q6)."""
        return (self.prop.spec.role != "barrier", not self.in_scene, self.height_m < SIGHT_BLOCK_HEIGHT_M, self.triangles, self.asset_id)


def _local(mesh: Mesh) -> tuple[np.ndarray, float, float, float]:
    low, high = mesh.vertices.min(axis=0), mesh.vertices.max(axis=0)
    span = high - low
    centre = np.eye(4)
    centre[:3, 3] = [-(low[0] + high[0]) / 2, -(low[1] + high[1]) / 2, -low[2]]
    if span[1] > span[0]:
        # A quarter turn puts the long side on x.
        turn = np.array([[0.0, -1.0, 0.0, 0.0], [1.0, 0.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]])
        return turn @ centre, float(span[1]), float(span[0]), float(span[2])
    return centre, float(span[0]), float(span[1]), float(span[2])


def prop_reason(prop: Prop, licence: str | None) -> str | None:
    """Why a street-size prop cannot be cover, or None."""
    if licence is None:
        return "no_licence"
    spec = prop.spec
    if spec.role not in COVER_ROLES:
        return "not_a_cover_role"
    if spec.anchor != "ground":
        return "not_on_the_ground"
    if not prop.mesh.vertex_count:
        return "no_mesh"
    _matrix, length, depth, height = _local(prop.mesh)
    if not is_solid(spec.role, spec.category, (length, depth, height)):
        return "not_solid"
    if height < COVER_HEIGHT_M:
        return "too_low"
    if depth < COVER_DEPTH_M:
        return "too_thin"
    if length > MAX_PROP_LENGTH_M:
        return "too_long"
    return None


def cover_props(
    props: Sequence[Prop], licences: Mapping[str, str], in_scene: frozenset[str],
) -> tuple[list[CoverProp], dict[str, str]]:
    """The props that meet the cover rule, ranked by ``CoverProp.key``, and the reason each other one is left out.

    ``props`` are at street size (``realize.fitted_props``); ``licences`` holds each installed row's licence;
    ``in_scene`` the asset ids whose material the scene already has.
    """
    kept: list[CoverProp] = []
    refused: dict[str, str] = {}
    for prop in props:
        licence = licences.get(prop.spec.id)
        reason = prop_reason(prop, licence)
        if reason is not None:
            refused[prop.spec.id] = reason
            continue
        assert licence is not None  # pragma: no mutate: prop_reason refuses a prop without a licence
        local, length, depth, height = _local(prop.mesh)
        kept.append(CoverProp(prop, licence, prop.spec.id in in_scene, local, length, depth, height))
    return sorted(kept, key=CoverProp.key), dict(sorted(refused.items()))


@dataclass(frozen=True, slots=True)
class Spot:
    x: float
    y: float
    yaw: float


def spots(target: OpenPoint) -> list[Spot]:
    """The fixed spots for one target: along the ray at ``SPOT_ALONG_M``, across it at ``SPOT_ACROSS_M``.

    The yaw is a quarter turn, 0 or 90 degrees, whichever lays the long side nearer across the ray: the
    report measures a collider by its axis-aligned box, so an axis-aligned prop is measured as it is.
    """
    px, py = target.point
    dx, dy = target.ray_to_m[0] - px, target.ray_to_m[1] - py
    length = math.hypot(dx, dy)
    dx, dy = dx / length, dy / length
    nx, ny = -dy, dx
    yaw = 0.0 if abs(nx) >= abs(ny) else math.pi / 2
    return [
        Spot(round(px + along * dx + across * nx, 2), round(py + along * dy + across * ny, 2), yaw)
        for along in SPOT_ALONG_M for across in SPOT_ACROSS_M
    ]


def pose(prop: CoverProp, spot: Spot, z: float = 0.0) -> np.ndarray:
    """The node matrix of ``prop`` at ``spot``: its box centre on the spot, its base at ``z``."""
    c, s = math.cos(spot.yaw), math.sin(spot.yaw)
    place = np.array([[c, -s, 0.0, spot.x], [s, c, 0.0, spot.y], [0.0, 0.0, 1.0, z], [0.0, 0.0, 0.0, 1.0]])
    return place @ prop.local


def footprint(prop: CoverProp, matrix: np.ndarray) -> Polygon:
    return _footprint(prop.prop.mesh, matrix)


def target_reason(target: OpenPoint, placed: Sequence[Placement]) -> str | None:
    """``covered`` when a placed prop lies within ``COVER_REACH_M``, ``spacing`` when one stands closer than ``TARGET_SPACING_M``."""
    here = Point(target.point)
    if any(item.footprint.envelope.distance(here) <= COVER_REACH_M for item in placed):
        return "covered"
    if any(math.dist(target.point, (item.spot.x, item.spot.y)) < TARGET_SPACING_M for item in placed):
        return "spacing"
    return None


@dataclass(frozen=True)
class CoverSite:
    """What a spot must keep clear of, in local metres.

    ``free`` is the walkable ground a prop may stand on (no carriageway, no building); ``ground`` the ground
    the passage test keeps joined (``free`` and the crossings); ``islands`` the navmesh islands; ``solids``
    the footprints of the street-level solid colliders already in the scene.
    """

    zone: Zone
    buildings: STRtree
    lanes: list[LineString]
    lane_tree: STRtree | None
    spawns: list[tuple[float, float]]
    free: BaseGeometry
    ground: BaseGeometry
    islands: BaseGeometry
    playable: BaseGeometry | None = None
    ammo: list[tuple[float, float]] = field(default_factory=list)
    climbs: list[tuple[float, float]] = field(default_factory=list)
    doors: list[tuple[float, float]] = field(default_factory=list)
    routes: list[LineString] = field(default_factory=list)
    urban: list[BaseGeometry] = field(default_factory=list)
    solids: list[BaseGeometry] = field(default_factory=list)
    anchors: list[tuple[float, float]] = field(default_factory=list)


def _near(points: Sequence[tuple[float, float]], x: float, y: float, radius: float) -> bool:
    return any(math.dist(point, (x, y)) < radius for point in points)


def _eroded(geometry: BaseGeometry) -> list[BaseGeometry]:
    if geometry.is_empty:
        return []
    shrunk = geometry.buffer(-(PASSAGE_M / 2 - ERODE_EPS_M))
    return [part for part in polygons_of(shrunk) if part.area >= MIN_PART_M2]  # pragma: no mutate: equal only for a piece of exactly 0.01 m², which a buffer does not produce


def joined(room: BaseGeometry, pieces: Sequence[BaseGeometry]) -> bool:
    """The ADR-031 test: ``room`` less ``pieces``, eroded by half of ``PASSAGE_M``, keeps each eroded piece of ``room`` in one piece."""
    before = _eroded(room)
    after = _eroded(room.difference(union_all(list(pieces))))
    for part in before:
        if sum(1 for other in after if other.intersects(part)) != 1:
            return False
    return True


def keeps_passage(site: CoverSite, print_: BaseGeometry, centre: tuple[float, float], placed: Sequence[BaseGeometry] = ()) -> bool:
    """Whether ``print_`` leaves ``PASSAGE_M`` of walkable width within ``CONNECT_RADIUS_M`` of ``centre``.

    The ground there less the existing solids and the props already placed is the room; the new footprint is the piece.
    """
    disc = Point(centre).buffer(CONNECT_RADIUS_M)
    blocked = [geometry for geometry in (*site.solids, *placed) if geometry.intersects(disc)]
    room = site.ground.intersection(disc).difference(union_all(blocked))
    return joined(room, [print_])


def keep_out(site: CoverSite, prop: CoverProp, matrix: np.ndarray, placed: Sequence[BaseGeometry] = ()) -> str | None:
    """Why ``prop`` posed by ``matrix`` cannot stay, or None. The first failing rule names the reason."""
    x, y = float(matrix[0, 3]), float(matrix[1, 3])
    print_ = footprint(prop, matrix)
    if site.playable is not None and not site.playable.covers(print_):
        return "outside_playable"
    if not site.islands.covers(Point(x, y)):
        return "off_navmesh"
    # No lane test here: the footprint must lie on walkable ground off the carriageway (below), not 4.2 m from the street's axis.
    reason = block_reason(x, y, zone=site.zone, buildings=site.buildings, lane_tree=None, lanes=[], spawns=site.spawns, allow_lane=True)  # pragma: no mutate: without a lane tree block_reason measures no lane, allowed or not
    if reason is not None:
        return reason
    if not site.free.covers(print_):
        return "off_walkable"
    if _near(site.ammo, x, y, AMMO_SPAWN_CLEAR_M):
        return "ammo_box"
    if _near(site.climbs, x, y, CLIMB_KEEP_OUT_M):
        return "climb_foot"
    if _near(site.doors, x, y, CLIMB_KEEP_OUT_M):
        return "door"
    if any(route.distance(print_) < NAV_AGENT_RADIUS_M for route in site.routes) or any(  # pragma: no mutate: no box edge lies exactly 0.35 m from a line in floating point
        _near(list(route.coords), x, y, CLIMB_ROUTE_KEEP_OUT_M) for route in site.routes
    ):
        return "hunt_route"
    if any(area.intersects(print_) for area in site.urban):
        return "on_urban_element"
    reason = object_reason(
        role=prop.prop.spec.role, mesh=prop.prop.mesh, matrix=matrix, buildings=site.buildings,
        lanes=site.lanes,  # pragma: no mutate: a prop at most 3 m long cannot leave under 1.2 m on both sides of the 8.4 m lane band
    )
    if reason is not None:
        return reason
    if any(other.intersection(print_).area > PROP_OVERLAP_M2 for other in (*site.solids, *placed)):
        return "overlaps_solid"
    if not keeps_passage(site, print_, (x, y), placed):
        return "blocks_passage"
    return None


def split_reason(site: CoverSite, prints: Sequence[BaseGeometry]) -> str | None:
    """``navmesh_split`` when the whole playable ground less ``prints`` fails the passage test, ``anchor_off_navmesh`` when
    a spawn, the Uplink or an AI start leaves the islands; None when neither happens."""
    if not prints:
        return None
    if not joined(site.ground.difference(union_all(site.solids)), prints):
        return "navmesh_split"
    islands = site.islands.difference(union_all([geometry.buffer(NAV_AGENT_RADIUS_M) for geometry in prints]))
    if any(site.islands.covers(Point(p)) and not islands.covers(Point(p)) for p in site.anchors):
        return "anchor_off_navmesh"
    return None


@dataclass(frozen=True, slots=True)
class Placement:
    prop: CoverProp
    spot: Spot
    matrix: np.ndarray
    footprint: Polygon
    target: OpenPoint


@dataclass(frozen=True, slots=True)
class Caps:
    """The limits one run applies; ``triangles`` already holds what overgrow left (``triangle_cap``)."""

    props: int = MAX_COVER_PROPS
    triangles: int = COVER_MAX_ADDED_TRIS
    draws: int = COVER_MAX_ADDED_DRAWS
    new_assets: int = MAX_NEW_COVER_ASSETS


DEFAULT_CAPS = Caps()


def triangle_cap(overgrow_triangles: int) -> int:
    """``COVER_MAX_ADDED_TRIS``, or less: what overgrow left of ``MAX_ADDED_TRIS``, never below 0."""
    return max(0, min(COVER_MAX_ADDED_TRIS, MAX_ADDED_TRIS - overgrow_triangles))


@dataclass(frozen=True)
class CoverPlan:
    targets: int
    placements: tuple[Placement, ...]
    rejects: dict[str, int]
    caps: Caps
    reason: str | None = None

    @property
    def triangles(self) -> int:
        return sum(item.prop.triangles for item in self.placements)

    @property
    def draws(self) -> int:
        return sum(item.prop.parts for item in self.placements)

    @property
    def new_assets(self) -> list[str]:
        return sorted({item.prop.asset_id for item in self.placements if not item.prop.in_scene})


def cap_reason(prop: CoverProp, placed: Sequence[Placement], caps: Caps) -> str | None:
    """``cap_triangles``, ``cap_draws`` or ``cap_new_assets`` when one more copy of ``prop`` would pass that cap."""
    if sum(item.prop.triangles for item in placed) + prop.triangles > caps.triangles:
        return "cap_triangles"
    if sum(item.prop.parts for item in placed) + prop.parts > caps.draws:
        return "cap_draws"
    new = {item.prop.asset_id for item in placed if not item.prop.in_scene}
    if not prop.in_scene and prop.asset_id not in new and len(new) + 1 > caps.new_assets:
        return "cap_new_assets"
    return None


def choose_cover(
    targets: Sequence[OpenPoint],
    props: Sequence[CoverProp],
    site: CoverSite,
    caps: Caps = DEFAULT_CAPS,
    *,
    floor: RoadFloor | None = None,
) -> CoverPlan:
    """Place cover target by target (longest ray first), each with the first prop and spot that pass; then the whole-map check.

    ``targets`` and ``props`` are sorted here, so the input order does not change the plan.
    """
    ordered = sorted(targets, key=lambda t: (-t.length_m, t.point, t.ray_to_m))
    ranked = sorted(props, key=CoverProp.key)
    if not ordered:
        return CoverPlan(0, (), {}, caps, NO_TARGET)
    if caps.props <= 0:
        return CoverPlan(len(ordered), (), {}, caps, NO_CAP)
    if not ranked:
        return CoverPlan(len(ordered), (), {}, caps, NO_PROP)
    rejects: Counter[str] = Counter()
    placed: list[Placement] = []
    for target in ordered:
        if len(placed) >= caps.props:
            rejects["cap_props"] += 1
            continue
        skip = target_reason(target, placed)
        if skip is not None:
            rejects[skip] += 1
            continue
        chosen = _first_fit(target, ranked, site, placed, caps, rejects, floor or RoadFloor.empty())
        if chosen is None:
            rejects["no_valid_spot"] += 1
        else:
            placed.append(chosen)
    while placed:
        reason = split_reason(site, [item.footprint for item in placed])
        if reason is None:
            break
        placed.pop()
        rejects[reason] += 1
    return CoverPlan(len(ordered), tuple(placed), dict(sorted(rejects.items())), caps, None if placed else NO_SPOT)


def _first_fit(
    target: OpenPoint,
    ranked: Sequence[CoverProp],
    site: CoverSite,
    placed: Sequence[Placement],
    caps: Caps,
    rejects: Counter[str],
    floor: RoadFloor,
) -> Placement | None:
    prints = [item.footprint for item in placed]
    for prop in ranked:
        capped = cap_reason(prop, placed, caps)
        if capped is not None:
            rejects[capped] += 1
            continue
        for spot in spots(target):
            matrix = pose(prop, spot, floor.z(spot.x, spot.y))
            print_ = footprint(prop, matrix)
            # The report measures the collider's axis-aligned box, so the sample's clearance is measured to it too. Every spot
            # is at most 1.42 m from the sample and inside the footprint, so the box is always within COVER_REACH_M.
            if print_.envelope.distance(Point(target.point)) < NAV_AGENT_RADIUS_M:  # pragma: no mutate: equal only at exactly 0.35 m, which rotated boxes do not hit
                rejects["on_route_sample"] += 1
                continue
            reason = keep_out(site, prop, matrix, prints)  # pragma: no mutate: targets keep 12 m from placed props, beyond the 6 m passage disc and any footprint
            if reason is None:
                return Placement(prop, spot, matrix, print_, target)
            rejects[reason] += 1
    return None


def note(plan: CoverPlan) -> str:
    """The ``cover:`` line ``perf cover`` prints. Estimated, not measured."""
    if plan.reason is not None:
        return f"cover: nothing placed ({plan.reason})"
    assets = ", ".join(sorted({item.prop.asset_id for item in plan.placements}))
    return (
        f"cover: placed {len(plan.placements)} of {plan.targets} targets over {MAX_SIGHTLINE_M:g} m ({assets}), "
        f"+{plan.triangles} triangles, +{plan.draws} primitives (estimated from the 2D navmesh)"
    )
