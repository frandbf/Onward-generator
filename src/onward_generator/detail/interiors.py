"""Simplified interiors for selected buildings only.

An interior is generic and *not* a reconstruction of the real building: one open
room per furnished floor, a through-door from the street, and a straight stair
between each pair of floors. A see-through window is a hole in that wall plus one
closed pane. A building tall enough for three floors gets the third when the seed
says so. From the top floor a switchback continues to the
flat roof. Slabs and colliders leave the doorway and the stairs walkable.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np
from shapely import Point, Polygon, make_valid, unary_union
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient

from onward_generator.detail.config import DetailConfig
from onward_generator.detail.facades import UP, Hole, Layout, Segment, face_with_openings, stable_unit
from onward_generator.detail.modules import StairGeometry, frame_matrix, stair, stair_geometry
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


@dataclass(slots=True)
class InteriorPlan:
    building_id: str
    inner: Polygon
    floors: int
    storey_m: float
    door: Hole
    door_segment: Segment
    flights: tuple[Flight, ...]


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
        return sum(mesh.triangle_count for meshes in self.visual.values() for mesh in meshes)


def fits_triangle_budget(used: int, cost: int, limit: int) -> bool:
    """Whether one more interior of ``cost`` triangles keeps the interiors at or under ``limit``."""
    return used + cost <= limit


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


def _return_flight(flight: Flight, rise: float, width: float, max_riser: float, tread: float) -> Flight:
    """The same well, opposite direction, one level higher. A switchback."""
    geometry = stair_geometry(rise, width, max_riser, tread)
    x_axis = flight.matrix[:3, 0]
    y_axis = flight.matrix[:3, 1]
    origin = flight.matrix[:3, 3] + x_axis * flight.geometry.run_m + y_axis * flight.geometry.width_m
    matrix = frame_matrix(origin, -x_axis, -y_axis)
    return Flight(geometry, matrix, flight.well, flight.level + 1, "roof")


def _roof_steps(start_z: float, height: float, storey: float) -> int:
    """How many storey-high flights reach the roof deck from ``start_z``."""
    gap = height - start_z
    if storey <= 0 or gap < storey * 0.75:
        return 0
    steps = int(round(gap / storey))
    if steps < 1 or abs(steps * storey - gap) > 0.15:
        return 0
    return steps


def plan_interior(
    building_id: str, refined: BaseGeometry, door: Hole, segment: Segment, height: float, storey_m: float, levels: int, cfg: DetailConfig,
    *,
    roof_access: bool = True,
    max_floors: int | None = None,
) -> InteriorPlan | str:
    """Return a plan, or the reason the building cannot host the generic interior.

    ``max_floors`` caps the furnished floors below ``interiors.floors``: a shop takes 1, so it has no stair.
    The stair interior leaves the top storey solid. A shop may use every storey, so a one-storey shop
    has its room under the roof.
    """
    ic = cfg.interiors
    if len(polygons_of(refined)) != 1 or polygons_of(refined)[0].interiors:
        return "footprint_not_a_simple_polygon"
    floors = min(ic.floors, levels - 1) if max_floors is None else min(max_floors, levels)
    if floors < 1:
        return "not_enough_levels"
    inner = refined.buffer(-ic.wall_thickness_m, join_style="mitre", mitre_limit=2.0)
    if inner.geom_type != "Polygon" or inner.is_empty or inner.interiors or inner.area < 20.0:
        return "inner_space_too_small"
    inner = orient(inner, sign=1.0)
    rim = [segment.point(door.s0, 0, ic.wall_thickness_m), segment.point(door.s0 + door.width, 0, ic.wall_thickness_m)]
    if any(inner.exterior.distance(Point(p[0], p[1])) > RIM_TOLERANCE_M for p in rim):
        return "door_not_on_inner_wall"
    door_zone = segment.plan_rect(door.s0 - 0.3, door.s0 + door.width + 0.3, -(ic.wall_thickness_m + ic.door_clearance_m), -ic.wall_thickness_m)
    if not inner.buffer(1e-3).contains(door_zone):
        return "no_room_behind_door"

    if floors < 2:
        return InteriorPlan(building_id, inner, floors, storey_m, door, segment, ())

    # Of the buildings that can hold a third floor, half do: sha256(seed, id).
    wanted = floors
    if floors >= 3 and stable_unit(cfg.seed, building_id, "interior_floors") >= 0.5:
        wanted = 2
    geometry = stair_geometry(storey_m, ic.stair.width_m, ic.stair.max_riser_m, ic.stair.tread_m)
    landing = ic.stair.landing_m
    flights: list[Flight] = []
    blocked: list[Polygon] = []
    for level in range(wanted - 1):
        placed = _seek_flight(inner, door_zone, blocked, geometry, landing)
        if placed is None:
            break
        matrix, well = placed
        flights.append(Flight(geometry, matrix, well, level, "storey"))
        blocked.append(well)
    if not flights:
        return "no_room_for_stair"
    furnished = len(flights) + 1
    if roof_access:
        start_z = (furnished - 1) * storey_m
        steps = _roof_steps(start_z, height, storey_m)
        if steps:
            placed = _seek_flight(inner, door_zone, blocked, geometry, landing)
            if placed is not None:
                matrix, well = placed
                climb = Flight(geometry, matrix, well, furnished - 1, "roof")
                flights.append(climb)
                for _ in range(steps - 1):
                    climb = _return_flight(climb, storey_m, ic.stair.width_m, ic.stair.max_riser_m, ic.stair.tread_m)
                    flights.append(climb)
    return InteriorPlan(building_id, inner, furnished, storey_m, door, segment, tuple(flights))


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


def light_anchors(plan: InteriorPlan, rooms: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Where the adapter may put one baked light and a few light probes in each furnished room (ADR-013).

    Coordinates are local metres (x east, y north, z up), like ``uv_frame``. The light sits
    under the ceiling at the room's centroid, or at a point of the room clear of the
    stair wells when the centroid falls in one. ``range_m`` reaches the corners of the
    room's rotated rectangle, clamped. ``probes_m`` is a flat list of x, y, z triples:
    one layer above the floor and one under the ceiling.
    """
    wells = unary_union([flight.well.buffer(WELL_CLEARANCE_M) for flight in plan.flights])
    clear = plan.inner.difference(wells)
    centre = plan.inner.centroid
    if not clear.contains(centre):
        centre = clear.representative_point()
    corners = list(plan.inner.minimum_rotated_rectangle.exterior.coords)
    reach = min(LIGHT_RANGE_MAX_M, max(LIGHT_RANGE_MIN_M, math.dist(corners[0], corners[2]) / 2 + 1.0))
    columns = _probe_columns(plan.inner, wells)
    anchors = []
    for room in rooms:
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


def pierce_room_windows(
    layout: Layout,
    segments: Sequence[Segment],
    inner: Polygon,
    footprint: BaseGeometry,
    thickness: float,
    ceiling_z: float,
) -> tuple[int, int]:
    """Put one see-through pane in glass the player can reach from the room.

    A window, shopfront, or balcony door below the accessible ceiling becomes
    a hole of ``thickness`` with a single ``glass_view`` quad when that opening
    lands on the inner wall. The inner wall is cut on the same span. Otherwise
    the opening is removed. Glass above the ceiling stays ``glass_dark``.
    Returns how many openings were pierced and how many were removed.
    """
    coords = list(inner.exterior.coords)
    opened = omitted = 0
    kept: list[Hole] = []
    for hole in layout.holes:
        if hole.kind not in ROOM_GLASS or hole.v0 >= ceiling_z - 1e-6:
            kept.append(hole)
            continue
        if hole.v0 + hole.height > ceiling_z + 1e-3:
            omitted += 1
            continue
        seg = segments[hole.segment]
        recess = seg.plan_rect(hole.s0, hole.s0 + hole.width, -thickness, 0.0)
        ends = (seg.point(hole.s0, 0.0, thickness), seg.point(hole.s0 + hole.width, 0.0, thickness))
        on_inner = any(
            _edge_parameters(ax, ay, bx, by, ends[0], ends[1]) is not None
            for (ax, ay), (bx, by) in zip(coords[:-1], coords[1:], strict=True)
        )
        if not on_inner or not footprint.buffer(1e-6).contains(recess):
            omitted += 1
            continue
        kept.append(replace(hole, depth=thickness, back=GLASS_VIEW))
        opened += 1
    layout.holes = kept
    return opened, omitted


def glass_penetrations(layout: Layout, segments: Sequence[Segment]) -> list[tuple[Segment, Hole]]:
    """See-through openings ``pierce_room_windows`` kept. One pane each, both directions."""
    return [
        (segments[hole.segment], hole)
        for hole in layout.holes
        if hole.back == GLASS_VIEW and hole.kind in ROOM_GLASS
    ]


def room_window_cuts(layout: Layout, segments: Sequence[Segment]) -> list[WallCut]:
    """Inner-wall spans of the room panes ``pierce_room_windows`` kept."""
    cuts: list[WallCut] = []
    for hole in layout.holes:
        if hole.back != GLASS_VIEW or hole.kind not in ROOM_GLASS:
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
    """Inward-facing walls on the inner ring, with door and window cuts."""
    parts: list[Mesh] = []
    coords = list(inner.exterior.coords)
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
    gap: Polygon,
    penetrations: Sequence[tuple[Segment, Hole]],
    thickness: float,
    door_height: float,
    z_top: float,
) -> tuple[list[tuple[str, Mesh]], list[Mesh]]:
    """Concrete bands with the door and each see-through opening removed, plus one pane per opening.

    Bands split on the door head and on every opening edge, so a band is either fully
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
    levels = {0.0, float(door_height), float(z_top)}
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
        if zb <= door_height + 1e-6:
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
    panes = [_pane(seg, hole, thickness) for seg, hole in penetrations]
    return walls_out, panes


def room_ceiling(plan: InteriorPlan, height: float, slab_m: float) -> float:
    """Ceiling of the top furnished room: the storey line, or the roof slab's underside when the room reaches the roof."""
    top = plan.floors * plan.storey_m
    return height - slab_m if top >= height - 1e-3 else top


def build_interior(
    plan: InteriorPlan,
    height: float,
    z_top: float,
    refined: BaseGeometry,
    cfg: DetailConfig,
    window_cuts: Sequence[WallCut] = (),
    penetrations: Sequence[tuple[Segment, Hole]] = (),
    *,
    wall_material: str = "interior_wall",
    floor_material: str = "interior_floor",
) -> Interior:
    """Build the planned interior. ``wall_material`` and ``floor_material`` paint the room's walls and ground
    floor (a shop's colours); ceilings, slabs and upper floors keep ``interior_wall`` and ``interior_floor``."""
    ic = cfg.interiors
    s, k, t = plan.storey_m, plan.floors, ic.wall_thickness_m
    seg, door = plan.door_segment, plan.door
    rim = (seg.point(door.s0, 0, t), seg.point(door.s0 + door.width, 0, t))
    top = room_ceiling(plan, height, ic.slab_m)
    under_roof = top < k * s
    visual: dict[str, list[Mesh]] = {"interior_wall": [], "interior_floor": [], "stair": [], wall_material: [], floor_material: []}
    roof_flights = [flight for flight in plan.flights if flight.role == "roof"]
    visual[wall_material].append(_inner_walls(plan.inner, 0.0, top, ((rim[0], rim[1], 0.0, door.height), *window_cuts)))
    visual[floor_material].append(cap(plan.inner, 0.0))
    colliders: list[tuple[str, Mesh]] = []

    ring = refined.difference(plan.inner)
    gap = seg.plan_rect(door.s0, door.s0 + door.width, -(t + CUT_OVERLAP_M), CUT_OVERLAP_M)
    wall_parts, glass = _pierced_walls(ring, gap, penetrations, t, door.height, z_top)
    colliders.extend(wall_parts)
    if not roof_flights:
        # A room under the roof is closed by a roof slab; otherwise the storeys above stay solid.
        visual["interior_wall"].append(cap(plan.inner, top, up=False))
        colliders.append(("roof_slab" if under_roof else "upper_solid", prism(plan.inner, top, height, bottom=True)))

    rooms = [{"id": f"{plan.building_id}/L{i}", "level": i, "floor_z_m": round(i * s, 3),
              "ceiling_z_m": round(top if i + 1 == k else (i + 1) * s - ic.slab_m, 3), "area_m2": round(plan.inner.area, 3)} for i in range(k)]
    connections: list[dict[str, Any]] = [{
        "type": "door", "from": "exterior", "to": rooms[0]["id"], "width_m": door.width, "height_m": door.height,
        "position_m": [round(float(v), 3) for v in seg.point(door.s0 + door.width / 2)[:2]],
    }]
    roof_well: Polygon | None = None
    for flight in plan.flights:
        g = flight.geometry
        z_floor = (flight.level + 1) * s
        on_roof = flight.role == "roof" and abs(z_floor - height) <= 0.15
        plate = plan.inner.difference(flight.well)
        if on_roof:
            visual["interior_wall"].append(cap(plate, height - ic.slab_m, up=False))
            visual["interior_wall"].append(walls(flight.well, height - ic.slab_m, height, inward=True))
            colliders.append(("roof_deck", prism(plate, height - ic.slab_m, height, bottom=True)))
            roof_well = flight.well
            destination = "roof"
        else:
            visual["interior_floor"].append(cap(plate, z_floor))
            visual["interior_wall"].append(cap(plate, z_floor - ic.slab_m, up=False))
            visual["interior_wall"].append(walls(flight.well, z_floor - ic.slab_m, z_floor, inward=True))
            colliders.append((f"slab_L{flight.level + 1}", prism(plate, z_floor - ic.slab_m, z_floor, bottom=True)))
            destination = rooms[flight.level + 1]["id"] if flight.level + 1 < k else f"{plan.building_id}/L{flight.level + 1}"
        proto = stair(g)
        lifted = np.array(flight.matrix, copy=True)
        lifted[2, 3] += flight.level * s
        for material, mesh in proto.parts:
            visual[material].append(transform(mesh, lifted))
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
    }
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
