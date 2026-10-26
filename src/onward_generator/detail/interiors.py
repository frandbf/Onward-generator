"""Simplified interiors for selected buildings only.

An interior is generic and *not* a reconstruction of the real building: one open
room per accessible floor, a through-door from the street, a straight stair
between floors, slabs, and closed colliders that leave the doorway and stair
walkable. Upper floors that are not accessible stay solid.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from shapely import Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient

from onward_generator.detail.config import DetailConfig
from onward_generator.detail.facades import UP, Hole, Segment, face_with_openings
from onward_generator.detail.modules import StairGeometry, frame_matrix, stair, stair_geometry
from onward_generator.generation.mesh import Mesh, cap, closed_manifold_defects, merge, polygons_of, prism, quad, signed_volume, transform, walls

WALL_INSET_M = 0.05
RIM_TOLERANCE_M = 1e-3


@dataclass(slots=True)
class InteriorPlan:
    building_id: str
    inner: Polygon
    floors: int
    storey_m: float
    door: Hole
    door_segment: Segment
    stair: StairGeometry | None
    stair_matrix: np.ndarray | None
    stairwell: Polygon | None


@dataclass(slots=True)
class Interior:
    plan: InteriorPlan
    visual: dict[str, list[Mesh]]
    colliders: list[tuple[str, Mesh]]
    spec: dict[str, Any]
    defects: int = 0
    checks: dict[str, Any] = field(default_factory=dict)


def rectangularity(geometry: BaseGeometry) -> float:
    mrr = geometry.minimum_rotated_rectangle
    return float(geometry.area / mrr.area) if mrr.area > 0 else 0.0


def _rect(origin: np.ndarray, x: np.ndarray, y: np.ndarray, x0: float, x1: float, y0: float, y1: float) -> Polygon:
    pts = [origin + x * a + y * b for a, b in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
    return Polygon([(p[0], p[1]) for p in pts])


def plan_interior(
    building_id: str, refined: BaseGeometry, door: Hole, segment: Segment, height: float, storey_m: float, levels: int, cfg: DetailConfig
) -> InteriorPlan | str:
    """Return a plan, or the reason the building cannot host the generic interior."""
    ic = cfg.interiors
    if len(polygons_of(refined)) != 1 or polygons_of(refined)[0].interiors:
        return "footprint_not_a_simple_polygon"
    floors = min(ic.floors, 2, levels - 1)
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
        return InteriorPlan(building_id, inner, floors, storey_m, door, segment, None, None, None)

    geometry = stair_geometry(storey_m, ic.stair.width_m, ic.stair.max_riser_m, ic.stair.tread_m)
    landing = ic.stair.landing_m
    coords = list(inner.exterior.coords)
    edges = sorted(range(len(coords) - 1), key=lambda k: (-round(math.dist(coords[k], coords[k + 1]), 6), k))
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
            shrunk = inner.buffer(-0.02)
            if not (shrunk.contains(footprint) and shrunk.contains(before) and shrunk.contains(after)):
                continue
            if footprint.buffer(0.2).intersects(door_zone):
                continue
            matrix = frame_matrix(origin, x_axis, y_axis)
            return InteriorPlan(building_id, inner, floors, storey_m, door, segment, geometry, matrix, footprint)
    return "no_room_for_stair"


def _inner_walls(inner: Polygon, z0: float, z1: float, door_rim: tuple[np.ndarray, np.ndarray] | None, door_height: float) -> Mesh:
    """Inward-facing walls on the inner ring, with the doorway cut where the door rim lies."""
    parts: list[Mesh] = []
    coords = list(inner.exterior.coords)
    for (ax, ay), (bx, by) in zip(coords[:-1], coords[1:], strict=True):
        length = math.dist((ax, ay), (bx, by))
        if length < 1e-6:
            continue
        a = np.array([ax, ay, 0.0])
        u = np.array([bx - ax, by - ay, 0.0]) / length
        n_out = np.array([u[1], -u[0], 0.0])  # away from the room
        hole = None
        if door_rim is not None:
            s = [float((p - a) @ u) for p in door_rim]
            off = [float(np.linalg.norm((p - a) - ((p - a) @ u) * u)) for p in door_rim]
            if max(off) < RIM_TOLERANCE_M and -1e-6 <= min(s) and max(s) <= length + 1e-6:
                hole = (min(s), max(s))
        b = a + u * length
        if hole is None:
            parts.append(quad(a + z0 * UP, a + z1 * UP, b + z1 * UP, b + z0 * UP))  # reverse of the outward quad
            continue
        face = face_with_openings(length, z0, z1, [(hole[0], z0, hole[1], z0 + door_height)])
        m = np.eye(4)
        m[:3, 0], m[:3, 1], m[:3, 2], m[:3, 3] = u, [0.0, 0.0, 1.0], n_out, a
        mesh = transform(cap(face, 0.0), m)
        parts.append(Mesh(mesh.vertices, mesh.triangles[:, ::-1].copy()))
    return merge(parts)


def build_interior(plan: InteriorPlan, height: float, z_top: float, refined: BaseGeometry, cfg: DetailConfig) -> Interior:
    ic = cfg.interiors
    s, k, t = plan.storey_m, plan.floors, ic.wall_thickness_m
    seg, door = plan.door_segment, plan.door
    rim = (seg.point(door.s0, 0, t), seg.point(door.s0 + door.width, 0, t))
    top = k * s
    visual: dict[str, list[Mesh]] = {"interior_wall": [], "interior_floor": [], "stair": []}
    visual["interior_wall"].append(_inner_walls(plan.inner, 0.0, top, rim, door.height))
    visual["interior_floor"].append(cap(plan.inner, 0.0))
    visual["interior_wall"].append(cap(plan.inner, top, up=False))
    colliders: list[tuple[str, Mesh]] = []

    ring = refined.difference(plan.inner)
    gap = seg.plan_rect(door.s0, door.s0 + door.width, -(t + 0.05), 0.05)
    colliders.append(("walls_doorway", prism(ring.difference(gap), 0.0, door.height, bottom=True)))
    colliders.append(("walls_upper", prism(ring, door.height, z_top, bottom=True)))
    colliders.append(("upper_solid", prism(plan.inner, top, height, bottom=True)))

    rooms = [{"id": f"{plan.building_id}/L{i}", "level": i, "floor_z_m": round(i * s, 3),
              "ceiling_z_m": round((i + 1) * s - (ic.slab_m if i + 1 < k else 0.0), 3), "area_m2": round(plan.inner.area, 3)} for i in range(k)]
    connections: list[dict[str, Any]] = [{
        "type": "door", "from": "exterior", "to": rooms[0]["id"], "width_m": door.width, "height_m": door.height,
        "position_m": [round(float(v), 3) for v in seg.point(door.s0 + door.width / 2)[:2]],
    }]
    if plan.stair is not None and plan.stair_matrix is not None and plan.stairwell is not None:
        g = plan.stair
        plate = plan.inner.difference(plan.stairwell)
        visual["interior_floor"].append(cap(plate, s))
        visual["interior_wall"].append(cap(plate, s - ic.slab_m, up=False))
        visual["interior_wall"].append(walls(plan.stairwell, s - ic.slab_m, s, inward=True))
        proto = stair(g)
        for material, mesh in proto.parts:
            visual[material].append(transform(mesh, plan.stair_matrix))
        colliders.append(("slab_L1", prism(plate, s - ic.slab_m, s, bottom=True)))
        assert proto.collider is not None
        colliders.append(("stair_ramp", transform(proto.collider, plan.stair_matrix)))
        start = plan.stair_matrix @ np.array([0.0, g.width_m / 2, 0.0, 1.0])
        connections.append({
            "type": "stair", "from": rooms[0]["id"], "to": rooms[1]["id"], "risers": g.risers, "riser_m": round(g.riser_m, 4),
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
    spec = {
        "building_id": plan.building_id,
        "representation": "generic_simplified_interior",
        "representative_of_real_interior": False,
        "rooms": rooms,
        "connections": connections,
        "wall_thickness_m": t,
        "accessible_floors": k,
    }
    return Interior(plan, visual, colliders, spec, defects, {"collider_defects": defects, "collider_volumes_m3": volumes})
