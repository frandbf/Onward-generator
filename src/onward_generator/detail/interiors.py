"""Simplified interiors for selected buildings only.

An interior is generic and *not* a reconstruction of the real building: one open
room per furnished floor, a through-door from the street, and a straight stair
between each pair of floors. A see-through window is a hole in that wall plus one
closed pane. A building tall enough for three floors gets the third when the seed
says so. From the top floor more flights continue to the
flat roof, taking turns in two wells. Slabs and colliders leave the doorway and the stairs walkable.

A shop has one storey and one room per shop on its frontage: a partition wall at the
bay boundary between two shops' shopfronts, one more room for each other footprint
part with a shopfront, and a courtyard left as a hole with walls around it.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from itertools import pairwise
from typing import Any

import numpy as np
from shapely import Point, Polygon, make_valid, unary_union
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient

from onward_generator.detail.config import DetailConfig, Interiors, ShopCategory
from onward_generator.detail.facades import UP, Hole, Layout, Segment, face_with_openings, stable_unit
from onward_generator.detail.modules import StairGeometry, frame_matrix, stair, stair_geometry
from onward_generator.detail.shops import Shop, shop_bays, shop_category
from onward_generator.generation.mesh import (
    Mesh,
    box,
    cap,
    closed_manifold_defects,
    merge,
    polygons_of,
    prism,
    quad,
    signed_volume,
    transform,
    walls,
)

WALL_INSET_M = 0.05
RIM_TOLERANCE_M = 1e-3
# The door already cuts 5 cm past each face of the wall. Window holes use the same overlap
# so the concrete ring actually opens, instead of leaving a sliver on one side.
CUT_OVERLAP_M = 0.05
# Closed pane in the middle of the opening. Thin enough that the shot crosses one volume,
# inset from the jambs so it does not weld into the concrete.
PANE_THICKNESS_M = 0.02
PANE_INSET_M = 0.01
# Openings the player can stand on either side of. One quad, material glass_view.
# The adapter draws both faces with PP_Sprite_Lit. Do not add a second quad.
GLASS_VIEW = "glass_view"
# A shop's display window: frameless glass, the same single quad, and no pane collider, so a shot
# crosses it. The wall collider is open on it above the stallriser. The adapter draws it with the
# SDK glass material when the project has one, else PP_Sprite_Lit like ``glass_view``.
SHOP_GLASS = "shop_glass"
SEE_THROUGH = frozenset({GLASS_VIEW, SHOP_GLASS})
ROOM_GLASS = frozenset({"window", "narrow_window", "shopfront", "balcony_door"})
WallCut = tuple[np.ndarray, np.ndarray, float, float]
# Light anchors (ADR-013). Modelling assumptions, not observations: one light under the
# ceiling of each furnished room, a reach that covers the room's corners, and a small
# probe grid kept off the walls and out of the stair wells.
LIGHT_BELOW_CEILING_M = 0.3
LIGHT_RANGE_MIN_M = 4.0
LIGHT_RANGE_MAX_M = 12.0
WELL_CLEARANCE_M = 0.3
PROBE_INSET_M = 0.5
PROBE_STEP_M = 4.0
PROBE_MAX_PER_AXIS = 4
PROBE_LOW_M = 1.0
PROBE_HIGH_BELOW_CEILING_M = 0.5
# The smallest room behind a door for the stair interior (a shop room takes interiors.shops.min_room_area_m2).
MIN_ROOM_M2 = 20.0
# The wall between two shop rooms. Centred on a bay boundary, it stays in the pier
# (at least min_pier_m / 2 = 0.25 m each side), clear of both shopfronts.
PARTITION_M = 0.2


@dataclass(frozen=True, slots=True)
class Flight:
    """One straight stair from ``level`` to ``level + 1``. The mesh is built at z = 0 and lifted.

    ``role`` is ``storey`` between the furnished floors, or ``roof`` on the climb
    from the top floor to the roof deck.
    """

    geometry: StairGeometry
    matrix: np.ndarray
    well: Polygon
    level: int
    role: str = "storey"


@dataclass(frozen=True, slots=True)
class Room:
    """One ground-floor room of a shop. ``entrance`` (its walkable door and that door's wall) is ``None``
    for a room seen through its windows only.

    ``shops`` are the indices of the building's shops in this room; ``category`` paints it.
    """

    polygon: Polygon
    entrance: tuple[Hole, Segment] | None
    category: ShopCategory | None = None
    shops: tuple[int, ...] = ()


@dataclass(slots=True)
class InteriorPlan:
    """``inner`` is the room behind ``door``. ``rooms`` lists every room of a shop, that one first;
    a stair interior leaves it empty. ``hosts`` are the footprint parts that hold a room."""

    building_id: str
    inner: Polygon
    floors: int
    storey_m: float
    door: Hole
    door_segment: Segment
    flights: tuple[Flight, ...]
    rooms: tuple[Room, ...] = ()
    hosts: BaseGeometry | None = None
    depth_m: float | None = None
    """The band behind the door's wall the room is cut to (a shop above ``max_area_m2``), or ``None``."""

    @property
    def ground(self) -> BaseGeometry:
        """The ground-floor rooms: every shop room, or the one room behind the door."""
        return unary_union([room.polygon for room in self.rooms]) if self.rooms else self.inner

    @property
    def shop_doors(self) -> tuple[Hole, ...]:
        """The walkable doors that took a shop's shopfront: every room's door but the main one."""
        return tuple(room.entrance[0] for room in self.rooms[1:] if room.entrance is not None)


@dataclass(slots=True)
class Interior:
    plan: InteriorPlan
    visual: dict[str, list[Mesh]]
    colliders: list[tuple[str, Mesh]]
    spec: dict[str, Any]
    defects: int = 0
    checks: dict[str, Any] = field(default_factory=dict)
    roof_well: Polygon | None = None
    glass_colliders: list[Mesh] = field(default_factory=list)
    light_anchors: dict[str, Any] | None = None

    @property
    def visual_triangles(self) -> int:
        """Triangles this interior draws: every visual mesh of the interior node. Colliders are not drawn."""
        return visual_triangles(self.visual)

    @property
    def collision_triangles(self) -> int:
        """Triangles of the interior's colliders: its walls, slabs, stairs and solids, and the glass panes."""
        return sum(mesh.triangle_count for _, mesh in self.colliders) + sum(mesh.triangle_count for mesh in self.glass_colliders)


def rectangularity(geometry: BaseGeometry) -> float:
    mrr = geometry.minimum_rotated_rectangle
    return float(geometry.area / mrr.area) if mrr.area > 0 else 0.0


def _rect(origin: np.ndarray, x: np.ndarray, y: np.ndarray, x0: float, x1: float, y0: float, y1: float) -> Polygon:
    pts = [origin + x * a + y * b for a, b in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
    return Polygon([(p[0], p[1]) for p in pts])


def _seek_flight(
    inner: Polygon,
    door_zone: Polygon,
    blocked: Sequence[Polygon],
    geometry: StairGeometry,
    landing: float,
) -> tuple[np.ndarray, Polygon] | None:
    """First straight flight that fits, longest walls first. ``blocked`` wells are already placed."""
    coords = list(inner.exterior.coords)
    edges = sorted(range(len(coords) - 1), key=lambda k: (-round(math.dist(coords[k], coords[k + 1]), 6), k))
    shrunk = inner.buffer(-0.02)
    obstacles = [zone.buffer(0.15) for zone in blocked]
    z = np.array([0.0, 0.0, 1.0])
    for k in edges:
        a, b = np.array([*coords[k], 0.0]), np.array([*coords[k + 1], 0.0])
        length = float(np.linalg.norm(b - a))
        if length < geometry.run_m + 2 * (landing + WALL_INSET_M):
            continue
        u = (b - a) / length
        left = np.cross(z, u)
        for forward in (True, False):
            if forward:
                x_axis, y_axis = u, left
                origin = a + u * (landing + WALL_INSET_M) + left * WALL_INSET_M
            else:
                x_axis, y_axis = -u, -left
                origin = b - u * (landing + WALL_INSET_M) + left * (WALL_INSET_M + geometry.width_m)
            footprint = _rect(origin, x_axis, y_axis, 0.0, geometry.run_m, 0.0, geometry.width_m)
            before = _rect(origin, x_axis, y_axis, -landing, 0.0, 0.0, geometry.width_m)
            after = _rect(origin, x_axis, y_axis, geometry.run_m, geometry.run_m + landing, 0.0, geometry.width_m)
            if not (shrunk.contains(footprint) and shrunk.contains(before) and shrunk.contains(after)):
                continue
            if footprint.buffer(0.2).intersects(door_zone):
                continue
            if any(footprint.intersects(zone) or before.intersects(zone) or after.intersects(zone) for zone in obstacles):
                continue
            return frame_matrix(origin, x_axis, y_axis), footprint
    return None


def _roof_steps(start_z: float, height: float, storey: float) -> int:
    """How many storey-high flights reach the roof deck from ``start_z``."""
    gap = height - start_z
    if storey <= 0 or gap < storey * 0.75:
        return 0
    steps = int(round(gap / storey))
    if steps < 1 or abs(steps * storey - gap) > 0.15:
        return 0
    return steps


def _rim_on(room: Polygon, seg: Segment, hole: Hole, depth: float) -> bool:
    """Whether both inner corners of an opening, ``depth`` behind the face, lie on a ring of ``room``."""
    rim = (seg.point(hole.s0, depth_in=depth), seg.point(hole.s0 + hole.width, depth_in=depth))
    return not any(room.boundary.distance(Point(p[0], p[1])) > RIM_TOLERANCE_M for p in rim)


def _inset(part: Polygon, thickness: float) -> BaseGeometry:
    """The room inside a part's walls: the part shrunk by ``thickness``, corners mitred up to twice the thickness."""
    return part.buffer(-thickness, join_style="mitre", mitre_limit=2.0)


def _door_piece(inner: BaseGeometry, door: Hole, segment: Segment, thickness: float) -> Polygon | None:
    """The piece of ``inner`` whose ring holds the door's inner corners; else the largest piece; ``None`` when empty."""
    pieces = polygons_of(inner)
    behind = [piece for piece in pieces if _rim_on(piece, segment, door, thickness)]
    return max(behind or pieces, key=lambda piece: piece.area, default=None)


def _door_zone(segment: Segment, door: Hole, ic: Interiors) -> Polygon:
    """The floor kept clear behind a door: ``door_clearance_m`` deep and 0.3 m wider on each side."""
    t = ic.wall_thickness_m
    return segment.plan_rect(door.s0 - 0.3, door.s0 + door.width + 0.3, -(t + ic.door_clearance_m), -t)


def _door_problem(room: Polygon, door: Hole, segment: Segment, ic: Interiors) -> str | None:
    """Why ``door`` cannot lead into ``room``, or ``None`` when it can."""
    if not _rim_on(room, segment, door, ic.wall_thickness_m):
        return "door_not_on_inner_wall"
    if not room.buffer(1e-3).contains(_door_zone(segment, door, ic)):
        return "no_room_behind_door"
    return None


def plan_interior(
    building_id: str, refined: BaseGeometry, door: Hole, segment: Segment, height: float, storey_m: float, levels: int, cfg: DetailConfig,
    *,
    roof_access: bool = True,
    max_floors: int | None = None,
    several_parts: bool = False,
    depth_m: float | None = None,
    min_area_m2: float = MIN_ROOM_M2,
) -> InteriorPlan | str:
    """Return a plan, or the reason the building cannot host the generic interior.

    ``max_floors`` caps the furnished floors below ``interiors.floors``: a shop takes 1, so it has no stair.
    The stair interior leaves the top storey solid, except that a one-storey building has its room under
    the roof. A shop may use every storey. With ``roof_access`` the roof comes first: when the well of the
    climb does not fit beside the wells of the furnished floors, the building gives up furnished floors,
    down to the ground floor alone, before it gives up the roof. A building of three storeys or more with
    no room for any stair is ``no_room_for_stair``. A courtyard stays a hole in the room. A footprint of several parts is
    refused unless ``several_parts``; the room is then in the part that holds the door, and
    ``plan_shop_rooms`` adds the others.

    The room is the piece of the inset part the door opens into: a neck narrower than two walls splits
    the inset, and the other pieces stay solid. With ``depth_m`` the room is also cut to the band of
    that depth behind the door's wall, closed by a back wall (a shop above ``max_area_m2``). A room
    under ``min_area_m2`` is ``inner_space_too_small``.
    """
    ic = cfg.interiors
    parts = polygons_of(refined)
    if len(parts) != 1 and not several_parts:
        return "footprint_not_a_simple_polygon"
    if max_floors is not None:
        floors = min(max_floors, levels)
    else:
        floors = min(ic.floors, max(levels - 1, 1)) if levels > 0 else 0
    if floors < 1:
        return "not_enough_levels"
    host = min(parts, key=Point(segment.a).distance)
    inner = _inset(host, ic.wall_thickness_m)
    if depth_m is not None:
        reach = host.length  # longer than any line across the part
        inner = inner.intersection(segment.plan_rect(-reach, segment.length + reach, -depth_m, reach))
    room = _door_piece(inner, door, segment, ic.wall_thickness_m)
    if room is None or room.area < min_area_m2:
        return "inner_space_too_small"
    inner = orient(room)
    problem = _door_problem(inner, door, segment, ic)
    if problem is not None:
        return problem
    door_zone = _door_zone(segment, door, ic)

    if max_floors is not None and floors < 2:  # a shop has no stair
        return InteriorPlan(building_id, inner, floors, storey_m, door, segment, (), depth_m=depth_m)

    # Of the buildings that can hold a third floor, half do: sha256(seed, id).
    wanted = floors
    if floors >= 3 and stable_unit(cfg.seed, building_id, "interior_floors") >= 0.5:
        wanted = 2
    geometry = stair_geometry(storey_m, ic.stair.width_m, ic.stair.max_riser_m, ic.stair.tread_m)
    best: tuple[Flight, ...] | None = None
    for furnished in range(wanted, 0, -1):
        flights = _storey_flights(inner, door_zone, geometry, ic.stair.landing_m, furnished)
        if flights is None:
            continue
        best = flights if best is None else best
        climb = _roof_climb(inner, door_zone, flights, geometry, furnished, height, storey_m, ic) if roof_access else None
        if climb is not None:
            best = (*flights, *climb)
            break
    if not best and wanted >= 2:
        return "no_room_for_stair"
    return InteriorPlan(building_id, inner, sum(f.role == "storey" for f in best or ()) + 1, storey_m, door, segment, best or (), depth_m=depth_m)


def _storey_flights(inner: Polygon, door_zone: Polygon, geometry: StairGeometry, landing: float, furnished: int) -> tuple[Flight, ...] | None:
    """A flight between each pair of the ``furnished`` floors, or ``None`` when one does not fit."""
    flights: list[Flight] = []
    blocked: list[Polygon] = []
    for level in range(furnished - 1):
        placed = _seek_flight(inner, door_zone, blocked, geometry, landing)
        if placed is None:
            return None
        matrix, well = placed
        flights.append(Flight(geometry, matrix, well, level, "storey"))
        blocked.append(well)
    return tuple(flights)


def _roof_climb(inner: Polygon, door_zone: Polygon, flights: Sequence[Flight], geometry: StairGeometry, furnished: int,
                height: float, storey_m: float, ic: Any) -> tuple[Flight, ...] | None:
    """The flights from the top furnished floor to the roof deck, or ``None``.

    They take turns in two wells. Two flights of neighbouring levels never share a well: the upper one
    would stand in the head room of the lower one's top steps, and nobody could climb it. The second well
    is the one of the last storey flight, two levels below the first flight that reuses it.
    """
    steps = _roof_steps((furnished - 1) * storey_m, height, storey_m)
    if not steps:
        return None
    placed = _seek_flight(inner, door_zone, [f.well for f in flights], geometry, ic.stair.landing_m)
    if placed is None:
        return None
    turns = [placed]
    if steps > 1:
        alternate = (flights[-1].matrix, flights[-1].well) if flights else _seek_flight(inner, door_zone, [placed[1]], geometry, ic.stair.landing_m)
        if alternate is None:
            return None
        turns.append(alternate)
    return tuple(Flight(geometry, *turns[step % len(turns)], furnished - 1 + step, "roof") for step in range(steps))


def _bay_boundary(a: Hole, b: Hole) -> float:
    """Where the wall between the bays of ``a`` and ``b`` (same wall, ``a`` first) goes: the middle bay boundary."""
    ca, cb = a.s0 + a.width / 2, b.s0 + b.width / 2
    columns = b.column - a.column
    return ca + (cb - ca) / columns * ((columns - 1) // 2 + 0.5)


def _bay_door(room: Polygon, bay: Hole, seg: Segment, ic: Interiors) -> Hole | None:
    """The walkable door centred on a shop's shopfront bay, or ``None`` when it does not lead into ``room``."""
    width = ic.door.width_m
    door = Hole("interior_door", bay.segment, bay.s0 + (bay.width - width) / 2, 0.0, width, ic.door.height_m,
                ic.wall_thickness_m, None, bay.column, 0)
    return door if _door_problem(room, door, seg, ic) is None else None


def plan_shop_rooms(
    plan: InteriorPlan, refined: BaseGeometry, layout: Layout, segments: Sequence[Segment], shops: Sequence[Shop], cfg: DetailConfig,
) -> InteriorPlan:
    """Split a shop's one storey into one room per shop and add a room for each other part with a shopfront.

    Each shop takes its shopfront as ``plan_shops`` will (``shop_bays``). Between two shops whose
    shopfronts are on the door's wall, a ``PARTITION_M`` wall runs across the room, perpendicular to
    that wall, on the middle bay boundary between their shopfronts. Every other part of the footprint
    whose inner ring holds a shopfront is one more room. A shop belongs to the room its shopfront
    opens into; a shop without one belongs to the room of the main door. The room of the main door
    keeps it. Any other room with a shop gets a walkable door in place of its first shop's
    shopfront, which ``layout`` loses; a room left without a door is seen through its windows only.
    Each room takes the category of its shops, or of every shop when it has none.
    """
    ic = cfg.interiors
    t = ic.wall_thickness_m
    seg = plan.door_segment
    bays = shop_bays(shops, layout.holes, segments)
    front = sorted((bay for bay in bays if bay is not None and bay.segment == seg.index), key=lambda bay: bay.s0)
    reach = refined.length  # longer than any line across the footprint
    cuts = [seg.plan_rect(s - PARTITION_M / 2, s + PARTITION_M / 2, -reach, reach)
            for s in (_bay_boundary(a, b) for a, b in pairwise(front))]
    pieces = polygons_of(plan.inner.difference(unary_union(cuts)))
    fronts = [h for h in layout.holes if h.kind == "shopfront"]
    for part in polygons_of(refined):
        inner = _inset(part, t)
        if part.intersects(plan.inner) or inner.geom_type != "Polygon" or inner.is_empty:
            continue
        if any(_rim_on(inner, segments[h.segment], h, t) for h in fronts):
            pieces.append(inner)
    pieces = [orient(piece) for piece in pieces]
    home = next(i for i, piece in enumerate(pieces) if _rim_on(piece, seg, plan.door, t))
    owner = [next((i for i, piece in enumerate(pieces) if bay is not None and _rim_on(piece, segments[bay.segment], bay, t)), home)
             for bay in bays]
    every = shop_category([tags for _, tags, _ in shops], cfg)
    rooms: list[Room] = []
    for i in [home, *(i for i in range(len(pieces)) if i != home)]:
        held = tuple(k for k, o in enumerate(owner) if o == i)
        category = shop_category([shops[k][1] for k in held], cfg) if held else every
        entrance: tuple[Hole, Segment] | None = (plan.door, seg)
        if i != home:
            # Only the room of the main door holds a shop without a shopfront, so ``held`` shops have one here.
            bay = bays[held[0]] if held else None
            door = None if bay is None else _bay_door(pieces[i], bay, segments[bay.segment], ic)
            entrance = None if door is None else (door, segments[door.segment])
            layout.holes = [door if h == bay and door is not None else h for h in layout.holes]
        rooms.append(Room(pieces[i], entrance, category, held))
    area = unary_union(pieces)
    hosts = unary_union([part for part in polygons_of(refined) if part.intersects(area)])
    return replace(plan, inner=pieces[home], rooms=tuple(rooms), hosts=hosts)


def _probe_columns(inner: Polygon, wells: BaseGeometry) -> list[tuple[float, float]]:
    """Grid points across the room's rotated rectangle that stay inside it, off the walls and the wells."""
    free = inner.buffer(-PROBE_INSET_M).difference(wells)
    corners = list(inner.minimum_rotated_rectangle.exterior.coords)
    p0, p1, p2 = (np.array(c) for c in corners[:3])
    u, v = p1 - p0, p2 - p1
    nu = min(PROBE_MAX_PER_AXIS, max(2, math.ceil(float(np.linalg.norm(u)) / PROBE_STEP_M)))
    nv = min(PROBE_MAX_PER_AXIS, max(2, math.ceil(float(np.linalg.norm(v)) / PROBE_STEP_M)))
    columns = []
    for i in range(nu):
        for j in range(nv):
            p = p0 + u * ((i + 0.5) / nu) + v * ((j + 0.5) / nv)
            if free.contains(Point(p[0], p[1])):
                columns.append((float(p[0]), float(p[1])))
    return columns


def flight_landings(flight: Flight, landing_m: float) -> tuple[Polygon, Polygon]:
    """The floor ``_seek_flight`` kept clear at each end of ``flight``: its foot, on the floor of ``flight.level``, and its
    arrival, on the floor above. Each is ``landing_m`` long and as wide as the stair, against one short side of the well."""
    run, width = flight.geometry.run_m, flight.geometry.width_m
    origin, x_axis, y_axis = flight.matrix[:3, 3], flight.matrix[:3, 0], flight.matrix[:3, 1]  # frame_matrix's columns
    return (_rect(origin, x_axis, y_axis, -landing_m, 0.0, 0.0, width),
            _rect(origin, x_axis, y_axis, run, run + landing_m, 0.0, width))


def light_anchors(plan: InteriorPlan, rooms: Sequence[dict[str, Any]], *, furniture: BaseGeometry | None = None,
                  room_furniture: Mapping[str, BaseGeometry] | None = None) -> dict[str, Any]:
    """Where the adapter may put one baked light and a few light probes in each furnished room (ADR-013).

    Coordinates are local metres (x east, y north, z up), like ``uv_frame``. The light sits
    under the ceiling at the room's centroid, or at a point of the room clear of the
    stair wells when the centroid falls in one. ``range_m`` reaches the corners of the
    room's rotated rectangle, clamped. ``probes_m`` is a flat list of x, y, z triples:
    one layer above the floor and one under the ceiling. A shop's rooms are ``plan.rooms``, in order;
    a courtyard is not part of the room, so a centroid in it moves too. ``furniture`` is the plan of the
    furnishing pieces (ADR-031): no probe column stands in it. ``None`` leaves the grid as it was.
    ``room_furniture`` maps a room id to the plan of that room's pieces only (the floors of a stair interior share
    one polygon, ADR-034): the probe columns of that room keep out of it, and the other rooms keep theirs.
    """
    wells = unary_union([flight.well.buffer(WELL_CLEARANCE_M) for flight in plan.flights])
    probe_wells = unary_union([wells, furniture])  # shapely skips a None part
    areas = [room.polygon for room in plan.rooms] or [plan.inner] * len(rooms)
    anchors = []
    for index, room in enumerate(rooms):
        area = areas[index]
        clear = area.difference(wells)
        centre = area.centroid
        if not clear.contains(centre):
            centre = clear.representative_point()
        corners = list(area.minimum_rotated_rectangle.exterior.coords)
        reach = min(LIGHT_RANGE_MAX_M, max(LIGHT_RANGE_MIN_M, math.dist(corners[0], corners[2]) / 2 + 1.0))
        own = (room_furniture or {}).get(room["id"])
        columns = _probe_columns(area, probe_wells if own is None else unary_union([probe_wells, own]))
        floor, ceiling = float(room["floor_z_m"]), float(room["ceiling_z_m"])
        probes: list[float] = []
        for z in (floor + PROBE_LOW_M, ceiling - PROBE_HIGH_BELOW_CEILING_M):
            for x, y in columns:
                probes.extend((round(x, 3), round(y, 3), round(z, 3)))
        anchors.append({
            "room_id": room["id"],
            "light_m": [round(centre.x, 3), round(centre.y, 3), round(ceiling - LIGHT_BELOW_CEILING_M, 3)],
            "range_m": round(reach, 3),
            "probes_m": probes,
        })
    return {"attribute_origin": "generated", "method": "room_ceiling_centre", "rooms": anchors}


def _edge_parameters(ax: float, ay: float, bx: float, by: float, p: np.ndarray, q: np.ndarray) -> tuple[float, float] | None:
    """Parameters of ``p`` and ``q`` along one edge, when both points lie on it."""
    edge = np.array([bx - ax, by - ay], dtype=np.float64)
    length = float(np.linalg.norm(edge))
    if length < 1e-6:
        return None
    unit = edge / length
    origin = np.array([ax, ay], dtype=np.float64)
    params: list[float] = []
    for point in (p, q):
        delta = np.asarray(point, dtype=np.float64)[:2] - origin
        along = float(delta @ unit)
        if float(np.linalg.norm(delta - along * unit)) > RIM_TOLERANCE_M:
            return None
        if along < -1e-4 or along > length + 1e-4:
            return None
        params.append(min(max(along, 0.0), length))
    lo, hi = min(params), max(params)
    if hi - lo < 1e-4:
        return None
    return lo, hi


def _locate_cut(coords: Sequence[tuple[float, float]], cut: WallCut) -> tuple[int, float, float] | None:
    a, b = cut[0], cut[1]
    for index, ((ax, ay), (bx, by)) in enumerate(zip(coords[:-1], coords[1:], strict=True)):
        span = _edge_parameters(ax, ay, bx, by, a, b)
        if span is not None:
            return index, span[0], span[1]
    return None


def _rings(rooms: BaseGeometry) -> list[list[tuple[float, float]]]:
    """Every ring of every room, outer ring and courtyards alike."""
    return [list(ring.coords) for room in polygons_of(rooms) for ring in (room.exterior, *room.interiors)]


def pierce_room_windows(
    layout: Layout,
    segments: Sequence[Segment],
    inner: BaseGeometry,
    footprint: BaseGeometry,
    thickness: float,
    ceiling_z: float,
    hosts: BaseGeometry | None = None,
) -> tuple[int, int]:
    """Put one see-through pane in glass the player can reach from the room.

    A window, shopfront, or balcony door below the accessible ceiling becomes
    a hole of ``thickness`` with a single see-through quad (``shop_glass`` on a shopfront,
    ``glass_view`` on the rest) when that opening lands on the inner wall: any ring of any room in ``inner``. The inner wall is cut on the same span. Otherwise
    the opening is removed. Glass above the ceiling stays ``glass_dark``, and so does glass in a wall
    of a footprint part outside ``hosts`` (the parts that hold a room; ``None`` checks no part).
    Returns how many openings were pierced and how many were removed.
    """
    rings = _rings(inner)
    edges = [edge for coords in rings for edge in pairwise(coords)]
    opened = omitted = 0
    kept: list[Hole] = []
    for hole in layout.holes:
        if hole.kind not in ROOM_GLASS or hole.v0 >= ceiling_z - 1e-6:
            kept.append(hole)
            continue
        seg = segments[hole.segment]
        if hosts is not None and hosts.boundary.distance(Point(seg.a)) > RIM_TOLERANCE_M:
            kept.append(hole)  # a wall of a part without a room
            continue
        if hole.v0 + hole.height > ceiling_z + 1e-3:
            omitted += 1
            continue
        recess = seg.plan_rect(hole.s0, hole.s0 + hole.width, -thickness, 0.0)
        ends = (seg.point(hole.s0, depth_in=thickness), seg.point(hole.s0 + hole.width, depth_in=thickness))
        on_inner = any(_edge_parameters(ax, ay, bx, by, ends[0], ends[1]) is not None for (ax, ay), (bx, by) in edges)
        if not on_inner or not footprint.buffer(1e-6).contains(recess):
            omitted += 1
            continue
        kept.append(replace(hole, depth=thickness, back=SHOP_GLASS if hole.kind == "shopfront" else GLASS_VIEW))
        opened += 1
    layout.holes = kept
    return opened, omitted


def glass_penetrations(layout: Layout, segments: Sequence[Segment]) -> list[tuple[Segment, Hole]]:
    """See-through openings ``pierce_room_windows`` kept. The wall collider opens on each of them."""
    return [
        (segments[hole.segment], hole)
        for hole in layout.holes
        if hole.back in SEE_THROUGH and hole.kind in ROOM_GLASS
    ]


def room_window_cuts(layout: Layout, segments: Sequence[Segment]) -> list[WallCut]:
    """Inner-wall spans of the room panes ``pierce_room_windows`` kept."""
    cuts: list[WallCut] = []
    for hole in layout.holes:
        if hole.back not in SEE_THROUGH or hole.kind not in ROOM_GLASS:
            continue
        seg = segments[hole.segment]
        cuts.append((
            seg.point(hole.s0, 0.0, hole.depth),
            seg.point(hole.s0 + hole.width, 0.0, hole.depth),
            hole.v0,
            hole.v0 + hole.height,
        ))
    return cuts


def _inner_walls(inner: Polygon, z0: float, z1: float, cuts: Sequence[WallCut]) -> Mesh:
    """Inward-facing walls on every ring of the room, with door and window cuts.

    ``inner`` is oriented (outer ring counter-clockwise, courtyards clockwise), so the right of
    each edge is away from the room on both: a courtyard's walls face into the room too.
    """
    return merge(_ring_inner_walls(coords, z0, z1, cuts) for coords in _rings(inner))


def _ring_inner_walls(coords: Sequence[tuple[float, float]], z0: float, z1: float, cuts: Sequence[WallCut]) -> Mesh:
    parts: list[Mesh] = []
    grouped: dict[int, list[tuple[float, float, float, float]]] = {}
    for cut in cuts:
        located = _locate_cut(coords, cut)
        if located is None:
            continue
        index, s0, s1 = located
        grouped.setdefault(index, []).append((s0, cut[2], s1, cut[3]))
    for index, ((ax, ay), (bx, by)) in enumerate(zip(coords[:-1], coords[1:], strict=True)):
        length = math.dist((ax, ay), (bx, by))
        if length < 1e-6:
            continue
        a = np.array([ax, ay, 0.0])
        u = np.array([bx - ax, by - ay, 0.0]) / length
        n_out = np.array([u[1], -u[0], 0.0])  # away from the room
        b = a + u * length
        rects = grouped.get(index)
        if not rects:
            parts.append(quad(a + z0 * UP, a + z1 * UP, b + z1 * UP, b + z0 * UP))  # reverse of the outward quad
            continue
        face = face_with_openings(length, z0, z1, rects)
        m = np.eye(4)
        m[:3, 0], m[:3, 1], m[:3, 2], m[:3, 3] = u, [0.0, 0.0, 1.0], n_out, a
        mesh = transform(cap(face, 0.0), m)
        parts.append(Mesh(mesh.vertices, mesh.triangles[:, ::-1].copy()))
    return merge(parts)


def _pane(seg: Segment, hole: Hole, thickness: float) -> Mesh:
    """Closed box centred in the wall. The collision node that owns it is named ``#glass``."""
    inset = PANE_INSET_M if hole.width > 2 * PANE_INSET_M + 0.05 else 0.0
    half = PANE_THICKNESS_M / 2
    mid = thickness / 2
    local = box(
        hole.s0 + inset, hole.s0 + hole.width - inset,
        mid - half, mid + half,
        hole.v0, hole.v0 + hole.height,
    )
    origin = np.array([seg.a[0], seg.a[1], 0.0])
    return transform(local, frame_matrix(origin, seg.u, -seg.n_out))


def _pierced_walls(
    ring: BaseGeometry,
    doors: Sequence[tuple[Polygon, float]],
    penetrations: Sequence[tuple[Segment, Hole]],
    thickness: float,
    z_top: float,
) -> tuple[list[tuple[str, Mesh]], list[Mesh]]:
    """Concrete bands with each door ``(gap, height)`` and each see-through opening removed, plus one pane per
    ``glass_view`` opening. A ``shop_glass`` opening gets no pane: a shot crosses it, and so can a body.

    Bands split on the door heads and on every opening edge, so a band is either fully
    open or fully solid. The band above an opening keeps the full ring: a downward
    navmesh ray still hits concrete, and the sill or the pier still stops a body.
    """
    cuts: list[tuple[Polygon, float, float]] = []
    for seg, hole in penetrations:
        rect = seg.plan_rect(
            hole.s0, hole.s0 + hole.width,
            -(thickness + CUT_OVERLAP_M), CUT_OVERLAP_M,
        )
        cuts.append((rect, hole.v0, hole.v0 + hole.height))
    levels = {0.0, float(z_top), *(float(height) for _, height in doors)}
    for _, z0, z1 in cuts:
        if 1e-4 < z0 < z_top:
            levels.add(float(z0))
        if 1e-4 < z1 < z_top:
            levels.add(float(z1))
    walls_out: list[tuple[str, Mesh]] = []
    ordered = sorted(levels)
    for index, (za, zb) in enumerate(zip(ordered, ordered[1:])):
        if zb - za < 1e-4:
            continue
        poly: BaseGeometry = ring
        for gap, height in doors:
            if za < height:  # the band is below this door's head: the levels split on it
                poly = poly.difference(gap)
        for rect, z0, z1 in cuts:
            if z0 <= za + 1e-6 and zb <= z1 + 1e-6:
                poly = poly.difference(rect)
        if poly.is_empty:
            continue
        poly = make_valid(poly)
        if poly.is_empty or float(poly.area) < 1e-4:
            continue
        walls_out.append((f"walls_{index}", prism(poly, za, zb, bottom=True)))
    panes = [_pane(seg, hole, thickness) for seg, hole in penetrations if hole.back == GLASS_VIEW]
    return walls_out, panes


def room_ceiling(plan: InteriorPlan, height: float, slab_m: float) -> float:
    """Ceiling of the top furnished room: the storey line, or the roof slab's underside when the room reaches the roof."""
    top = plan.floors * plan.storey_m
    return height - slab_m if top >= height - 1e-3 else top


def _shop_record(category: ShopCategory) -> dict[str, Any]:
    return {"category": category.name, "wall_material": category.wall, "floor_material": category.floor, "attribute_origin": "generated"}


def _shop_room(building_id: str, index: int, room: Room, ceiling: float) -> dict[str, Any]:
    """The record of one shop room: ``<building>/L0``, then ``<building>/L0-<n>``. ``reachable`` says whether it has a door."""
    record: dict[str, Any] = {"id": f"{building_id}/L0" + (f"-{index}" if index else ""), "level": 0, "floor_z_m": 0.0,
                              "ceiling_z_m": round(ceiling, 3), "area_m2": round(room.polygon.area, 3),
                              "reachable": room.entrance is not None, "shops": list(room.shops)}
    if room.category is not None:
        record["shop"] = _shop_record(room.category)
    return record


def _ground_rooms(plan: InteriorPlan) -> tuple[Room, ...]:
    """The ground-floor rooms: every shop room, or the one room behind the door."""
    return plan.rooms or (Room(plan.inner, (plan.door, plan.door_segment)),)


def interior_visual(
    plan: InteriorPlan,
    height: float,
    cfg: DetailConfig,
    window_cuts: Sequence[WallCut] = (),
    *,
    wall_material: str = "interior_wall",
    floor_material: str = "interior_floor",
) -> dict[str, list[Mesh]]:
    """The meshes the planned interior draws, by material. No collider.

    ``wall_material`` and ``floor_material`` paint the room's walls and ground floor; a shop room takes its
    category's colours instead. Ceilings, slabs and upper floors keep ``interior_wall`` and ``interior_floor``.
    The scene counts these triangles against the interior budget before it builds the shell and the
    colliders (``assemble_interior``).
    """
    ic = cfg.interiors
    s, t = plan.storey_m, ic.wall_thickness_m
    top = room_ceiling(plan, height, ic.slab_m)
    visual: dict[str, list[Mesh]] = {"interior_wall": [], "interior_floor": [], "stair": [], wall_material: [], floor_material: []}
    for room in _ground_rooms(plan):
        cuts = list(window_cuts)
        if room.entrance is not None:
            door, seg = room.entrance
            cuts.append((seg.point(door.s0, depth_in=t), seg.point(door.s0 + door.width, depth_in=t), 0.0, door.height))
        paint = (room.category.wall, room.category.floor) if room.category is not None else (wall_material, floor_material)
        visual.setdefault(paint[0], []).append(_inner_walls(room.polygon, 0.0, top, cuts))
        visual.setdefault(paint[1], []).append(cap(room.polygon, 0.0))
    if not _climbs_to_roof(plan):
        visual["interior_wall"].append(cap(plan.ground, top, up=False))
    for flight in plan.flights:
        z_floor = (flight.level + 1) * s
        plate = plan.inner.difference(flight.well)
        if _lands_on_roof(flight, z_floor, height):
            visual["interior_wall"].append(cap(plate, height - ic.slab_m, up=False))
            visual["interior_wall"].append(walls(flight.well, height - ic.slab_m, height, inward=True))
        else:
            visual["interior_floor"].append(cap(plate, z_floor))
            visual["interior_wall"].append(cap(plate, z_floor - ic.slab_m, up=False))
            visual["interior_wall"].append(walls(flight.well, z_floor - ic.slab_m, z_floor, inward=True))
        for material, mesh in stair(flight.geometry).parts:
            visual[material].append(transform(mesh, _lifted(flight, s)))
    roof_underside = height - ic.slab_m
    if _climbs_to_roof(plan) and top < roof_underside:
        # The storeys the climb passes above the furnished rooms are empty floors. The shell is one-sided and
        # faces out, so without this lining a player on those flights sees the street through the walls.
        # It takes no cut: pierce_room_windows only opens glass below the furnished ceiling.
        visual["interior_wall"].append(_inner_walls(plan.inner, top, roof_underside, ()))
    return visual


def visual_triangles(visual: Mapping[str, Sequence[Mesh]]) -> int:
    """Triangles drawn by ``visual``, every material."""
    return sum(mesh.triangle_count for meshes in visual.values() for mesh in meshes)


def _climbs_to_roof(plan: InteriorPlan) -> bool:
    return any(flight.role == "roof" for flight in plan.flights)


def _lands_on_roof(flight: Flight, z_floor: float, height: float) -> bool:
    return flight.role == "roof" and abs(z_floor - height) <= 0.15


def _lifted(flight: Flight, storey_m: float) -> np.ndarray:
    lifted = flight.matrix.copy()
    lifted[2, 3] = flight.level * storey_m  # a flight is planned at z = 0 (``Flight``); this is its floor
    return lifted


def build_interior(
    plan: InteriorPlan,
    height: float,
    refined: BaseGeometry,
    cfg: DetailConfig,
    window_cuts: Sequence[WallCut] = (),
    penetrations: Sequence[tuple[Segment, Hole]] = (),
    *,
    wall_material: str = "interior_wall",
    floor_material: str = "interior_floor",
) -> Interior:
    """Build the planned interior: ``interior_visual``, then ``assemble_interior``."""
    visual = interior_visual(plan, height, cfg, window_cuts, wall_material=wall_material, floor_material=floor_material)
    return assemble_interior(plan, visual, height, refined, cfg, penetrations)


def assemble_interior(
    plan: InteriorPlan,
    visual: dict[str, list[Mesh]],
    height: float,
    refined: BaseGeometry,
    cfg: DetailConfig,
    penetrations: Sequence[tuple[Segment, Hole]] = (),
) -> Interior:
    """The interior that draws ``visual`` (``interior_visual``): its colliders and panes, rooms, connections and checks."""
    ic = cfg.interiors
    s, k, t = plan.storey_m, plan.floors, ic.wall_thickness_m
    ground = _ground_rooms(plan)
    area = plan.ground
    top = room_ceiling(plan, height, ic.slab_m)
    under_roof = top < k * s
    doors = [(seg.plan_rect(door.s0, door.s0 + door.width, -(t + CUT_OVERLAP_M), CUT_OVERLAP_M), door.height)
             for door, seg in (room.entrance for room in ground if room.entrance is not None)]
    colliders: list[tuple[str, Mesh]] = []

    # Everything outside the rooms stays solid: the outer walls, the partitions, and a part without a room.
    # Up to the roof deck, no higher: above it the parapet has its own collider (``facades.build_shell``), and a
    # partition raised to the parapet top stood 0.7 m over the roof, drawn by nothing, in front of a roof lift.
    wall_parts, glass = _pierced_walls(refined.difference(area), doors, penetrations, t, height)
    colliders.extend(wall_parts)
    if not _climbs_to_roof(plan):
        # A room under the roof is closed by a roof slab; otherwise the storeys above stay solid.
        colliders.append(("roof_slab" if under_roof else "upper_solid", prism(area, top, height, bottom=True)))

    rooms = [{"id": f"{plan.building_id}/L{i}", "level": i, "floor_z_m": round(i * s, 3),
              "ceiling_z_m": round(top if i + 1 == k else (i + 1) * s - ic.slab_m, 3), "area_m2": round(plan.inner.area, 3)} for i in range(k)]
    if plan.rooms:
        rooms = [_shop_room(plan.building_id, index, room, top) for index, room in enumerate(plan.rooms)]
    connections: list[dict[str, Any]] = []
    for index, room in enumerate(ground):  # a stair interior has one ground room and a record per floor
        if room.entrance is not None:
            door, seg = room.entrance
            connections.append({
                "type": "door", "from": "exterior", "to": rooms[index]["id"], "width_m": door.width, "height_m": door.height,
                "position_m": [round(float(v), 3) for v in seg.point(door.s0 + door.width / 2)[:2]],
            })
    roof_well: Polygon | None = None
    for flight in plan.flights:
        g = flight.geometry
        z_floor = (flight.level + 1) * s
        plate = plan.inner.difference(flight.well)
        if _lands_on_roof(flight, z_floor, height):
            colliders.append(("roof_deck", prism(plate, height - ic.slab_m, height, bottom=True)))
            roof_well = flight.well
            destination = "roof"
        else:
            colliders.append((f"slab_L{flight.level + 1}", prism(plate, z_floor - ic.slab_m, z_floor, bottom=True)))
            destination = rooms[flight.level + 1]["id"] if flight.level + 1 < k else f"{plan.building_id}/L{flight.level + 1}"
        proto = stair(g)
        lifted = _lifted(flight, s)
        assert proto.collider is not None
        colliders.append((f"stair_L{flight.level}", transform(proto.collider, lifted)))
        start = lifted @ np.array([0.0, g.width_m / 2, 0.0, 1.0])
        origin_id = rooms[flight.level]["id"] if flight.level < k else f"{plan.building_id}/L{flight.level}"
        connections.append({
            "type": "stair", "from": origin_id, "to": destination,
            "risers": g.risers, "riser_m": round(g.riser_m, 4),
            "tread_m": g.tread_m, "run_m": round(g.run_m, 3), "width_m": g.width_m, "rise_m": round(g.rise_m, 3),
            "slope_deg": round(math.degrees(math.atan2(g.rise_m, g.run_m)), 2),
            "start_m": [round(float(v), 3) for v in start[:2]],
        })

    defects = 0
    volumes = {}
    for name, mesh in colliders:
        d = closed_manifold_defects(mesh)
        defects += d
        volumes[name] = round(signed_volume(mesh), 3)
    for index, mesh in enumerate(glass):
        d = closed_manifold_defects(mesh)
        defects += d
        volumes[f"glass_{index}"] = round(signed_volume(mesh), 3)
    spec = {
        "building_id": plan.building_id,
        "representation": "generic_simplified_interior",
        "representative_of_real_interior": False,
        "rooms": rooms,
        "connections": connections,
        "wall_thickness_m": t,
        "accessible_floors": k,
        "roof_access": roof_well is not None,
        "glass_panes": len(glass),
        "open_shopfronts": sum(1 for _, hole in penetrations if hole.back == SHOP_GLASS),
    }
    if plan.depth_m is not None:
        spec["room_depth_m"] = plan.depth_m
    if plan.rooms and plan.rooms[0].category is not None:
        spec["shop"] = _shop_record(plan.rooms[0].category)
    built = Interior(plan, visual, colliders, spec, defects, {"collider_defects": defects, "collider_volumes_m3": volumes}, roof_well)
    built.glass_colliders = glass
    built.light_anchors = light_anchors(plan, rooms)
    return built


def cap_lights(interiors: Sequence[tuple[bool, dict[str, Any]]], limit: int) -> list[str]:
    """Keep at most ``limit`` room lights across the map; return the room ids that lost theirs.

    ``interiors`` holds ``(is_shop, light_anchors)`` per interior. Shop rooms rank first, then
    every room by the distance of its light from the local origin (the map centre), then by id.
    A room past the cap keeps its probes and loses ``light_m`` and ``range_m``.
    """
    rooms = [(shop, room) for shop, anchors in interiors for room in anchors["rooms"]]
    ranked = sorted(rooms, key=lambda pair: (not pair[0], math.hypot(*pair[1]["light_m"][:2]), pair[1]["room_id"]))
    dropped = []
    for _, room in ranked[limit:]:
        del room["light_m"], room["range_m"]
        room["light"] = "over_cap"
        dropped.append(room["room_id"])
    return sorted(dropped)
