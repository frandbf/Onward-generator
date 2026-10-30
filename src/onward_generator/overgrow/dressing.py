"""Place downloaded props and invasive vegetation inside a zone.

Colliders are not created. Vehicles and barriers stay visual so they cannot
seal the drive lane. Vegetation has no collider at all.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from shapely import Point, STRtree
from shapely.geometry import LineString, Polygon

from onward_generator.detail.facades import stable_unit
from onward_generator.detail.modules import frame_matrix, yaw_matrix
from onward_generator.generation.gltf import Node, Primitive
from onward_generator.generation.mesh import Mesh, box, quad, transform
from onward_generator.overgrow.ambient import blocks_street, cross_yaw, is_solid
from onward_generator.overgrow.catalog import FACADE_Z_M, RULES_BY_ID, SIDEWALK_COVER_RULE, PlacementRule, rules_for
from onward_generator.overgrow.constants import (
    COVER_CLEAR_M,
    COVER_SPACING_M,
    COVER_STEP_M,
    LANE_M,
    MAX_VINES,
    SPAWN_CLEAR_M,
    VINE_HEIGHT_M,
    VINE_WIDTH_M,
)
from onward_generator.overgrow.fit import apply_fit_scale, measure, object_reason, size_reason
from onward_generator.overgrow.ground import RoadFloor
from onward_generator.overgrow.library import Prop
from onward_generator.overgrow.profiles import Profile, rule_factor, scaled_cap
from onward_generator.overgrow.zone import Zone


@dataclass
class Spot:
    surface: str
    x: float
    y: float
    yaw: float
    key: str
    feature_id: str
    clearance: float
    outward: tuple[float, float] | None = None
    along: tuple[float, float] | None = None
    edge_length: float = 0.0
    wall_height: float = 12.0
    lane_x: float | None = None
    lane_y: float | None = None
    normal: tuple[float, float] | None = None


@dataclass
class Dressing:
    nodes: list[Node] = field(default_factory=list)
    grass: list[Mesh] = field(default_factory=list)
    vines: list[Mesh] = field(default_factory=list)
    records: list[dict] = field(default_factory=list)
    rejects: list[dict] = field(default_factory=list)
    reject_counts: dict[str, int] = field(default_factory=dict)
    triangles: int = 0
    images: dict[str, bytes] = field(default_factory=dict)
    placed_ids: list[str] = field(default_factory=list)
    colliders: list[Node] = field(default_factory=list)


def keep(seed: int, factor: float, *parts: object) -> bool:
    if factor >= 1.0:
        return True
    if factor <= 0.0:
        return False
    return stable_unit(seed, *parts) < factor


def block_reason(
    x: float,
    y: float,
    *,
    zone: Zone,
    buildings: STRtree,
    lane_tree: STRtree | None,
    lanes: list[LineString],
    spawns: list[tuple[float, float]],
    allow_lane: bool = False,
    clearance_m: float = 0.0,
) -> str | None:
    """Why a point cannot take a prop. ``None`` means it is allowed."""
    if not zone.contains(x, y):
        return "outside_zone"
    here = Point(float(x), float(y))
    if buildings.query(here, predicate="intersects").size:
        return "inside_building"
    if _near_spawn(x, y, spawns):
        return "spawn_point"
    if not allow_lane and _on_lane(here, lane_tree, lanes):
        return "drive_lane"
    if clearance_m > 0.0 and _clearance(here, buildings) < clearance_m:
        return "too_close_to_building"
    return None


def dress(
    props: list[Prop],
    edges: list,
    features: list,
    buildings: STRtree,
    lanes: list[LineString],
    lane_tree: STRtree | None,
    zone: Zone,
    profile: Profile,
    seed: int,
    spawns: list[tuple[float, float]],
    *,
    grass_mesh: Mesh | None,
    grass_left: int,
    triangle_budget: int,
    block_streets: bool = False,
    placed_roles: dict[str, int] | None = None,
    floor: RoadFloor | None = None,
    occupied: list[tuple[float, float, float]] | None = None,
) -> Dressing:
    """``occupied`` lists (x, y, radius) already taken by wrecks, trees and grass.

    Only the sidewalk covers read it, so the other props keep their places.
    """
    result = Dressing()
    ground = floor if floor is not None else RoadFloor.empty()
    parks = _parks(features)
    heights = _heights(features)
    spots = _spots(edges, lanes, buildings, lane_tree, parks, heights, zone, result)
    used: list[tuple[float, float, float]] = []
    seen: set[str] = set()
    _place_props(
        props, spots, buildings, lane_tree, lanes, zone, profile, seed, spawns, triangle_budget, result,
        block_streets=block_streets, placed_roles=Counter(placed_roles or {}), floor=ground,
        used=used, seen=seen,
    )
    sidewalk_covers(
        props, lanes, buildings, lane_tree, zone, profile, seed, spawns, triangle_budget, result,
        used=[*used, *(occupied or [])], seen=seen, floor=ground,
    )
    _vines(edges, buildings, lane_tree, lanes, zone, profile, seed, spawns, result)
    if grass_mesh is not None and grass_left > 0:
        _extra_grass(spots, grass_mesh, buildings, lane_tree, lanes, zone, profile, seed, spawns, grass_left, result, ground)
    return result


def _place_props(
    props: list[Prop],
    spots: list[Spot],
    buildings: STRtree,
    lane_tree: STRtree | None,
    lanes: list[LineString],
    zone: Zone,
    profile: Profile,
    seed: int,
    spawns: list[tuple[float, float]],
    budget: int,
    result: Dressing,
    block_streets: bool = False,
    placed_roles: Counter[str] | None = None,
    floor: RoadFloor | None = None,
    *,
    used: list[tuple[float, float, float]],
    seen: set[str],
) -> None:
    counts: Counter[str] = Counter()
    role_placed = placed_roles if placed_roles is not None else Counter()
    props = [_fitted(prop) for prop in props]
    for prop in props:
        rules = _spot_rules(prop)
        if not rules:
            _reject(result, prop.spec.id, "no_rule", prop.spec.category, 0.0, 0.0)
            continue
        rule = rules[0]
        factor = rule_factor(profile, prop.spec.category, rule.roles)
        cap = scaled_cap(rule.cap, factor)
        if not keep(seed, factor, prop.spec.id, zone.stable_key(), "asset"):
            _reject(result, prop.spec.id, "profile", prop.spec.category, 0.0, 0.0)
            continue
        surfaces = set(prop.spec.surfaces) & set(rule.surfaces)
        candidates = [spot for spot in spots if spot.surface in surfaces]
        candidates.sort(key=lambda spot: (spot.x * spot.x + spot.y * spot.y, spot.key))
        if not candidates:
            _reject(result, prop.spec.id, "no_surface", prop.spec.category, 0.0, 0.0)
            continue
        # The first copy of each asset must land somewhere valid. The nearest
        # handful can all sit in the plaza or on a consumed edge, so the search
        # continues through the zone instead of stopping at eight spots.
        index = int(stable_unit(seed, prop.spec.id, zone.stable_key(), "spot") * len(candidates))
        ordered = candidates[index:] + candidates[:index]
        placed = False
        for spot in ordered[:128]:
            if counts[rule.id] >= cap or result.triangles + prop.triangles > budget:
                _reject(result, prop.spec.id, "budget" if result.triangles + prop.triangles > budget else "cap", prop.spec.category, spot.x, spot.y)
                break
            turn = _turn(prop, spot, role_placed, block_streets)
            reason = _spot_reason(
                prop, spot, zone, buildings, lane_tree, lanes, spawns, rule.lane == "allow",
                max(rule.clearance_m, prop.spec.clearance_m), block_streets, turn, floor,
            )
            if reason:
                _reject(result, prop.spec.id, reason, prop.spec.category, spot.x, spot.y)
                continue
            if any((spot.x - px) ** 2 + (spot.y - py) ** 2 < radius ** 2 for px, py, radius in used):
                _reject(result, prop.spec.id, "too_close", prop.spec.category, spot.x, spot.y)
                continue
            placed_x, placed_y = _emit(prop, spot, result, seen, block_streets, turn, floor)
            counts[rule.id] += 1
            role_placed[prop.spec.role] += 1
            used.append((placed_x, placed_y, 2.2 if prop.spec.anchor == "ground" else 1.4))
            placed = True
            break
        if not placed and counts[rule.id] < cap:
            continue
    _repeats(
        props, spots, buildings, lane_tree, lanes, zone, profile, seed, spawns, budget, result, used, counts, seen,
        block_streets, role_placed, floor,
    )


def _repeats(props, spots, buildings, lane_tree, lanes, zone, profile, seed, spawns, budget, result, used, counts, seen, block_streets, role_placed, floor) -> None:
    for prop in props:
        rules = rules_for(prop.spec)
        if not rules or prop.spec.id not in result.placed_ids:
            continue
        rule = rules[0]
        cap = scaled_cap(rule.cap, rule_factor(profile, prop.spec.category, rule.roles))
        surfaces = set(prop.spec.surfaces) & set(rule.surfaces)
        pool = [spot for spot in spots if spot.surface in surfaces]
        pool.sort(key=lambda spot: stable_unit(seed, prop.spec.id, spot.key, "repeat"))
        for spot in pool:
            if counts[rule.id] >= cap or result.triangles + prop.triangles > budget:
                break
            turn = _turn(prop, spot, role_placed, block_streets)
            reason = _spot_reason(
                prop, spot, zone, buildings, lane_tree, lanes, spawns, rule.lane == "allow",
                max(rule.clearance_m, prop.spec.clearance_m), block_streets, turn, floor,
            )
            final_x, final_y = _final_xy(prop, spot, block_streets, turn, floor)
            if reason or any((final_x - px) ** 2 + (final_y - py) ** 2 < radius ** 2 for px, py, radius in used):
                continue
            placed_x, placed_y = _emit(prop, spot, result, seen, block_streets, turn, floor)
            counts[rule.id] += 1
            role_placed[prop.spec.role] += 1
            used.append((placed_x, placed_y, 2.2))


def _spot_rules(prop: Prop) -> tuple[PlacementRule, ...]:
    """Rules served by the shared spots. Sidewalk covers have their own placer."""
    return tuple(rule for rule in rules_for(prop.spec) if rule.id != SIDEWALK_COVER_RULE)


def sidewalk_covers(
    props: list[Prop],
    lanes: list[LineString],
    buildings: STRtree,
    lane_tree: STRtree | None,
    zone: Zone,
    profile: Profile,
    seed: int,
    spawns: list[tuple[float, float]],
    budget: int,
    result: Dressing,
    *,
    used: list[tuple[float, float, float]],
    seen: set[str],
    floor: RoadFloor | None = None,
) -> int:
    """A few manhole covers on the sidewalk band, at seeded random spacing.

    Candidates walk each street at an irregular step, on a random side and at a
    random depth inside the sidewalk band. A seeded order picks among them across
    the whole zone. Covers are flat and have no size band, so they are not refitted.
    Returns how many were placed.
    """
    rule = RULES_BY_ID[SIDEWALK_COVER_RULE]
    covers = [prop for prop in props if rule in rules_for(prop.spec)]
    if not covers:
        return 0
    # Covers are barriers, so the profile's prop density scales the cap.
    cap = scaled_cap(rule.cap, profile.props)
    candidates = _cover_candidates(lanes, buildings, seed)
    candidates.sort(key=lambda spot: stable_unit(seed, spot.key, "cover-order"))
    placed = 0
    for spot in candidates:
        if placed >= cap:
            break
        prop = covers[int(stable_unit(seed, spot.key, "cover-asset") * len(covers))]
        if result.triangles + prop.triangles > budget:
            _reject(result, prop.spec.id, "budget", prop.spec.category, spot.x, spot.y)
            break
        reason = _cover_reason(prop, spot, zone, lane_tree, lanes, spawns, used)
        if reason:
            _reject(result, prop.spec.id, reason, prop.spec.category, spot.x, spot.y)
            continue
        placed_x, placed_y = _emit(prop, spot, result, seen, floor=floor)
        used.append((placed_x, placed_y, COVER_SPACING_M))
        placed += 1
    return placed


def _cover_candidates(lanes: list[LineString], buildings: STRtree, seed: int) -> list[Spot]:
    spots: list[Spot] = []
    for index, line in enumerate(lanes):
        length = float(line.length)
        distance = stable_unit(seed, index, "cover-start") * COVER_STEP_M
        while distance < length:  # pragma: no mutate: distance is a seeded float and never equals the street length
            key = f"cover:{index}:{distance:.2f}"
            tangent = _tangent(line, distance)
            sides = ((tangent[1], -tangent[0]), (-tangent[1], tangent[0]))
            normal = sides[int(stable_unit(seed, key, "cover-side") * 2)]
            # Sidewalk band is [LANE_M + 2.2, LANE_M + 6.5); keep the cover inside it.
            offset = LANE_M + 2.8 + stable_unit(seed, key, "cover-depth") * 3.1
            point = line.interpolate(distance)
            x = float(point.x + normal[0] * offset)
            y = float(point.y + normal[1] * offset)
            yaw = stable_unit(seed, key, "cover-yaw") * 6.28
            spots.append(Spot(
                "sidewalk", x, y, yaw, key,
                "",  # pragma: no mutate: covers never read feature_id; _emit records the asset id
                _clearance(Point(x, y), buildings),
            ))
            distance += COVER_STEP_M * (0.6 + 0.8 * stable_unit(seed, key, "cover-step"))
    return spots


def _cover_reason(
    prop: Prop,
    spot: Spot,
    zone: Zone,
    lane_tree: STRtree | None,
    lanes: list[LineString],
    spawns: list[tuple[float, float]],
    used: list[tuple[float, float, float]],
) -> str | None:
    """Why a cover cannot sit here. The band keeps it off the lane, the clearance off the walls."""
    lane_distance = _lane_distance(Point(spot.x, spot.y), lane_tree, lanes)
    if not LANE_M + 2.2 <= lane_distance < LANE_M + 6.5:
        return "not_sidewalk"
    if spot.clearance < COVER_CLEAR_M + max(prop.length_m, prop.half_y * 2) * 0.5:
        return "too_close_to_building"
    if any((spot.x - px) ** 2 + (spot.y - py) ** 2 < radius ** 2 for px, py, radius in used):
        return "too_close"
    if not zone.contains(spot.x, spot.y):
        return "outside_zone"
    if _near_spawn(spot.x, spot.y, spawns):
        return "spawn_point"
    return None


def _emit(prop: Prop, spot: Spot, result: Dressing, seen: set[str], block_streets: bool = False, turn: float = 0.0, floor: RoadFloor | None = None) -> tuple[float, float]:
    matrix, x, y, z = _frame(prop, spot, block_streets, turn, floor)
    material = f"prop_{prop.spec.id}"
    first = prop.spec.id not in seen
    seen.add(prop.spec.id)
    result.nodes.append(Node(
        f"overgrow/prop/{prop.spec.id}/{len(result.nodes)}",
        (Primitive(prop.mesh, material, prop.uvs),) if first else (),
        {
            "type": "overgrowth",
            "origin": "generated",
            "kind": prop.spec.category,
            "role": prop.spec.role,
            "asset_id": prop.spec.id,
            "surface": spot.surface,
            "fit_scale": round(prop.fit_scale, 4),
            "size_m": [round(prop.length_m, 3), round(prop.half_y * 2, 3), round(prop.height_m, 3)],
            "crosswise": turn != 0.0,
        },
        matrix,
        f"prop:{prop.spec.id}",
    ))
    result.images[material] = prop.albedo
    result.triangles += prop.triangles
    result.placed_ids.append(prop.spec.id)
    result.records.append({
        "kind": prop.spec.category,
        "role": prop.spec.role,
        "origin": "generated",
        "source_id": prop.spec.id,
        "asset_id": prop.spec.id,
        "surface": spot.surface,
        "position_m": [round(x, 3), round(y, 3), round(z, 3)],
        "fit_scale": round(prop.fit_scale, 4),
        "size_m": [round(prop.length_m, 3), round(prop.half_y * 2, 3), round(prop.height_m, 3)],
        "crosswise": turn != 0.0,
        "solid": is_solid(prop.spec.role, prop.spec.category),
    })
    if is_solid(prop.spec.role, prop.spec.category):
        result.colliders.append(Node(
            f"overgrow/collider/prop/{prop.spec.id}/{len(result.colliders)}",
            (Primitive(collider_mesh(prop.mesh), "collider"),),
            {"type": "collision", "origin": "generated", "asset_id": prop.spec.id, "role": prop.spec.role},
            matrix,
        ))
    return x, y


def collider_mesh(mesh: Mesh) -> Mesh:
    """A box around the prop. The visible mesh stays in the other file."""
    vertices = mesh.vertices
    return box(
        float(vertices[:, 0].min()), float(vertices[:, 0].max()),
        float(vertices[:, 1].min()), float(vertices[:, 1].max()),
        float(vertices[:, 2].min()), float(vertices[:, 2].max()),
    )


def _frame(prop: Prop, spot: Spot, block_streets: bool = False, turn: float = 0.0, floor: RoadFloor | None = None) -> tuple[np.ndarray, float, float, float]:
    if prop.spec.anchor == "facade" and spot.outward and spot.along:
        push = prop.half_y + 0.08
        x = spot.x + spot.outward[0] * push
        y = spot.y + spot.outward[1] * push
        z = float(FACADE_Z_M.get(prop.spec.role, 1.2))
        if spot.wall_height > 0.0 and z + prop.height_m > spot.wall_height:
            z = max(0.05, spot.wall_height - prop.height_m - 0.1)
        # Reverse the wall so +Y (outward) stays a proper rotation with +X along the pier.
        return frame_matrix(
            (x, y, z),
            (-spot.along[0], -spot.along[1], 0.0),
            (spot.outward[0], spot.outward[1], 0.0),
        ), x, y, z
    x, y = spot.x, spot.y
    if spot.normal and spot.lane_x is not None and spot.lane_y is not None and prop.spec.anchor != "flat":
        offset = 0.9 if blocks_street(prop.spec.role, street_blocking=block_streets) else LANE_M + prop.half_y + 0.45
        x = spot.lane_x + spot.normal[0] * offset
        y = spot.lane_y + spot.normal[1] * offset
    elif prop.spec.role == "manhole" and spot.normal and spot.lane_x is not None and spot.lane_y is not None:
        x = spot.lane_x + spot.normal[0] * 1.15
        y = spot.lane_y + spot.normal[1] * 1.15
    z = 0.0 if floor is None else floor.z(x, y)
    return yaw_matrix(x, y, z, spot.yaw + turn), x, y, z


def _turn(prop: Prop, spot: Spot, role_placed: Counter[str], block_streets: bool) -> float:
    return cross_yaw(
        prop.spec.role, role_placed[prop.spec.role],
        street_blocking=block_streets, on_street=spot.lane_x is not None,
    )


def _spot_reason(prop, spot, zone, buildings, lane_tree, lanes, spawns, allow_lane, clearance_m, block_streets=False, turn: float = 0.0, floor: RoadFloor | None = None) -> str | None:
    matrix, matrix_x, matrix_y, _z = _frame(prop, spot, block_streets, turn, floor)
    if prop.length_m > 0.0 and spot.edge_length and prop.length_m > spot.edge_length + 0.4 and spot.surface in {"facade", "block_edge"}:
        return "edge_too_short"
    if prop.spec.anchor == "facade" and spot.wall_height and prop.height_m > spot.wall_height + 0.2 and prop.spec.role == "fire_escape":
        return "taller_than_wall"
    point = block_reason(
        matrix_x, matrix_y,
        zone=zone, buildings=buildings, lane_tree=lane_tree, lanes=lanes, spawns=spawns,
        allow_lane=allow_lane or prop.spec.lane == "allow" or blocks_street(prop.spec.role, street_blocking=block_streets),
        clearance_m=clearance_m if prop.spec.role == "tree" else 0.0,
    )
    if point:
        return point
    # Size was already scaled in ``_fitted``. A spot that still fails moves on.
    return object_reason(role=prop.spec.role, mesh=prop.mesh, matrix=matrix, buildings=buildings, lanes=lanes)


def _fitted(prop: Prop) -> Prop:
    """Copy the mesh at the street size. The library mesh stays untouched."""
    mesh, scale = apply_fit_scale(prop.mesh, prop.spec.role)
    if scale == 1.0:
        return prop
    length_m, width_m, height_m = measure(mesh)
    if size_reason(prop.spec.role, length_m, width_m, height_m) is not None:
        return prop
    return Prop(prop.spec, mesh, prop.uvs, prop.albedo, scale)


def _final_xy(prop: Prop, spot: Spot, block_streets: bool = False, turn: float = 0.0, floor: RoadFloor | None = None) -> tuple[float, float]:
    _matrix, x, y, _z = _frame(prop, spot, block_streets, turn, floor)
    return x, y


def _vines(edges, buildings, lane_tree, lanes, zone, profile, seed, spawns, result: Dressing) -> None:
    cap = scaled_cap(MAX_VINES, profile.vines)
    for edge in edges:
        if len(result.vines) >= cap:
            break
        if edge.length < 3.0 or not keep(seed, profile.vines, edge.feature_id, edge.index, "vine"):
            continue
        along = (edge.b - edge.a) / edge.length
        outward = edge.outward
        x = float(edge.a[0] + outward[0] * 0.06)
        y = float(edge.a[1] + outward[1] * 0.06)
        reason = block_reason(x, y, zone=zone, buildings=buildings, lane_tree=lane_tree, lanes=lanes, spawns=spawns)
        if reason:
            _reject(result, "vine", reason, "vegetation", x, y)
            continue
        x2 = x + float(along[0]) * VINE_WIDTH_M
        y2 = y + float(along[1]) * VINE_WIDTH_M
        result.vines.append(quad(
            (x, y, 0.15),
            (x2, y2, 0.15),
            (x2, y2, VINE_HEIGHT_M),
            (x, y, VINE_HEIGHT_M),
        ))
        result.triangles += 2
        result.records.append({
            "kind": "vine",
            "role": "vine",
            "origin": "generated",
            "source_id": edge.feature_id,
            "asset_id": None,
            "surface": "facade",
            "position_m": [round(x, 3), round(y, 3), 0.15],
        })


def _extra_grass(spots, grass_mesh, buildings, lane_tree, lanes, zone, profile, seed, spawns, grass_left, result: Dressing, floor: RoadFloor | None = None) -> None:
    cap = min(grass_left, scaled_cap(24, profile.grass))
    chosen = [spot for spot in spots if spot.surface in {"yard", "empty_lot", "sidewalk"}]
    chosen.sort(key=lambda spot: (spot.x * spot.x + spot.y * spot.y, spot.key))
    for spot in chosen:
        if len(result.grass) >= cap:
            break
        if not keep(seed, profile.grass, spot.key, "weed"):
            continue
        reason = block_reason(spot.x, spot.y, zone=zone, buildings=buildings, lane_tree=lane_tree, lanes=lanes, spawns=spawns)
        if reason:
            continue
        yaw = stable_unit(seed, spot.key, "weed-yaw") * 6.28
        z = 0.0 if floor is None else floor.z(spot.x, spot.y)
        result.grass.append(transform(grass_mesh, yaw_matrix(spot.x, spot.y, z, yaw)))
        result.triangles += grass_mesh.triangle_count
        result.records.append({
            "kind": "grass",
            "role": "weed",
            "origin": "generated",
            "source_id": spot.feature_id,
            "asset_id": None,
            "surface": spot.surface,
            "position_m": [round(spot.x, 3), round(spot.y, 3), round(z, 3)],
        })


def _spots(edges, lanes, buildings, lane_tree, parks, heights, zone: Zone, result: Dressing) -> list[Spot]:
    spots: list[Spot] = []
    for index, line in enumerate(lanes):
        distance = 12.0
        while distance < float(line.length) - 3.0:
            point = line.interpolate(distance)
            tangent = _tangent(line, distance)
            yaw = float(np.arctan2(tangent[1], tangent[0]))
            for sign in (1.0, -1.0):
                normal = (float(tangent[1] * sign), float(-tangent[0] * sign))
                samples = {
                    "curb": LANE_M + 1.8,
                    "sidewalk": LANE_M + 3.6,
                    "carriageway": 1.15,
                }
                for surface, offset in samples.items():
                    x = float(point.x + normal[0] * offset)
                    y = float(point.y + normal[1] * offset)
                    key = f"lane:{index}:{distance:.1f}:{sign}:{surface}"
                    if not zone.contains(x, y):
                        _reject(result, surface, "outside_zone", surface, x, y)
                        continue
                    spots.append(Spot(
                        surface, x, y, yaw, key, f"lane-{index}", _clearance(Point(x, y), buildings),
                        lane_x=float(point.x), lane_y=float(point.y), normal=normal,
                    ))
            distance += 18.0
    for edge in edges:
        along = (edge.b - edge.a) / edge.length
        outward = edge.outward
        mid = edge.mid
        height = float(heights.get(edge.feature_id, 12.0))
        facade = (float(mid[0] + outward[0] * 0.35), float(mid[1] + outward[1] * 0.35))
        _add_area_spot(spots, result, zone, buildings, "facade", facade, edge, height, outward, along)
        front = (float(mid[0] + outward[0] * 1.6), float(mid[1] + outward[1] * 1.6))
        if _lane_distance(Point(front), lane_tree, lanes) < 18.0:
            _add_area_spot(spots, result, zone, buildings, "block_edge", front, edge, height, outward, along)
        if _lane_distance(Point(float(mid[0]), float(mid[1])), lane_tree, lanes) > 12.0:
            yard = (float(mid[0] + outward[0] * 2.4), float(mid[1] + outward[1] * 2.4))
            _add_area_spot(spots, result, zone, buildings, "courtyard", yard, edge, height, None, None)
            _add_area_spot(spots, result, zone, buildings, "yard", yard, edge, height, None, None)
    minx, miny, maxx, maxy = zone.bounds()
    step = 10.0
    x = minx + step * 0.5
    while x < maxx:
        y = miny + step * 0.5
        while y < maxy:
            _classify_grid(spots, result, zone, buildings, lane_tree, lanes, parks, x, y)
            y += step
        x += step
    return _trim(spots)


def _add_area_spot(spots, result, zone, buildings, surface, xy, edge, height, outward, along) -> None:
    x, y = xy
    if not zone.contains(x, y):
        _reject(result, surface, "outside_zone", surface, x, y)
        return
    yaw = float(np.arctan2(along[1], along[0])) if along is not None else 0.0
    spots.append(Spot(
        surface, x, y, yaw, f"{surface}:{edge.feature_id}:{edge.index}", edge.feature_id,
        _clearance(Point(x, y), buildings),
        outward=(float(outward[0]), float(outward[1])) if outward is not None else None,
        along=(float(along[0]), float(along[1])) if along is not None else None,
        edge_length=float(edge.length), wall_height=height,
    ))


def _classify_grid(spots, result, zone, buildings, lane_tree, lanes, parks, x: float, y: float) -> None:
    if not zone.contains(x, y):
        return
    here = Point(x, y)
    if buildings.query(here, predicate="intersects").size:
        return
    lane_distance = _lane_distance(here, lane_tree, lanes)
    clearance = _clearance(here, buildings)
    if any(park.covers(here) for park in parks):
        surface = "park"
    elif lane_distance < LANE_M:
        surface = "carriageway"
    elif lane_distance < LANE_M + 2.2:
        surface = "curb"
    elif lane_distance < LANE_M + 6.5:
        surface = "sidewalk"
    elif clearance > 8.0 and x * x + y * y < 45.0 ** 2:
        surface = "plaza"
    elif clearance > 6.0 and lane_distance > 10.0:
        surface = "empty_lot"
    elif clearance < 7.0 and lane_distance > 12.0:
        surface = "yard"
    else:
        return
    spots.append(Spot(surface, x, y, 0.0, f"grid:{x:.1f}:{y:.1f}", "grid", clearance))


def _trim(spots: list[Spot]) -> list[Spot]:
    grouped: dict[str, list[Spot]] = {}
    for spot in spots:
        grouped.setdefault(spot.surface, []).append(spot)
    trimmed: list[Spot] = []
    for items in grouped.values():
        items.sort(key=lambda spot: (spot.x * spot.x + spot.y * spot.y, spot.key))
        trimmed.extend(items[:48])
    trimmed.sort(key=lambda spot: (spot.surface, spot.key))
    return trimmed


def _parks(features: list) -> list[Polygon]:
    parks: list[Polygon] = []
    for feature in features:
        if feature.get("kind") not in {"area", "leisure", "landuse"}:
            continue
        tags = feature.get("tags") or {}
        marker = " ".join(str(tags.get(key) or "") for key in ("leisure", "landuse", "place"))
        if marker not in {"park", "garden", "grass", "recreation_ground", "square", "plaza"} and "park" not in marker:
            continue
        geometry = feature.get("geometry") or {}
        if geometry.get("type") not in {"Polygon", "MultiPolygon"}:
            continue
        from shapely.geometry import shape

        geom = shape({"type": geometry["type"], "coordinates": geometry["coordinates"]})
        if geom.geom_type == "Polygon":
            parks.append(geom)
        elif geom.geom_type == "MultiPolygon":
            parks.extend(part for part in geom.geoms if isinstance(part, Polygon))
    return parks


def _heights(features: list) -> dict[str, float]:
    found: dict[str, float] = {}
    for feature in features:
        if feature.get("kind") != "building":
            continue
        tags = feature.get("tags") or {}
        raw = tags.get("height") or tags.get("building:height")
        height = None
        if raw not in (None, ""):
            try:
                height = float(str(raw).split()[0].replace(",", "."))
            except ValueError:
                height = None
        if height is None and tags.get("building:levels") not in (None, ""):
            try:
                height = float(str(tags.get("building:levels")).split()[0]) * 3.0
            except ValueError:
                height = None
        if height is not None:
            found[str(feature.get("id") or "")] = height
    return found


def _reject(result: Dressing, asset_id: str, reason: str, kind: str, x: float, y: float) -> None:
    result.reject_counts[reason] = result.reject_counts.get(reason, 0) + 1
    if len(result.rejects) < 80:
        result.rejects.append({
            "asset_id": asset_id,
            "reason": reason,
            "kind": kind,
            "position_m": [round(float(x), 3), round(float(y), 3), 0.0],
        })


def _near_spawn(x: float, y: float, spawns: list[tuple[float, float]]) -> bool:
    if not spawns:
        return False
    points = np.asarray(spawns, dtype=np.float64)
    return bool(np.hypot(points[:, 0] - x, points[:, 1] - y).min() < SPAWN_CLEAR_M)


def _on_lane(point: Point, lane_tree: STRtree | None, lanes: list[LineString]) -> bool:
    return _lane_distance(point, lane_tree, lanes) < LANE_M


def _lane_distance(point: Point, lane_tree: STRtree | None, lanes: list[LineString]) -> float:
    if lane_tree is None or not lanes:
        return 1.0e9
    hits = lane_tree.query(point.buffer(LANE_M + 30.0))
    if len(hits) == 0:
        return 1.0e9
    return min(float(lanes[int(hit)].distance(point)) for hit in hits)


def _clearance(point: Point, buildings: STRtree) -> float:
    if buildings.query(point, predicate="intersects").size:
        return 0.0
    hits = buildings.query(point.buffer(40.0))
    if len(hits) == 0:
        return 40.0
    return min(float(buildings.geometries[int(hit)].distance(point)) for hit in hits)


def load_spawns(path: str | Path | None) -> list[tuple[float, float]]:
    """Hunt and match spawns from a gameplay layout. Routes are not spawns."""
    if path is None:
        return []
    file = Path(path).expanduser()
    if not file.is_file():
        return []
    try:
        document = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    points: list[tuple[float, float]] = []
    for key in ("ai_m", "harddrives_m"):
        points.extend(_xy_objects(document.get(key)))
    for key in ("attackers", "defenders", "attackers_b", "defenders_b"):
        block = document.get(key) or {}
        if isinstance(block, dict):
            points.extend(_xy_objects(block.get("points_m")))
    for key in ("uplink_m", "evac_m", "escort_a_m", "escort_b_m", "map_quad_m"):
        value = document.get(key)
        if isinstance(value, list) and len(value) >= 2 and isinstance(value[0], (int, float)):
            points.append((float(value[0]), float(value[1])))
    return points


def _xy_objects(value: object) -> list[tuple[float, float]]:
    if not isinstance(value, list):
        return []
    points: list[tuple[float, float]] = []
    for item in value:
        if isinstance(item, dict) and "x" in item and "y" in item:
            points.append((float(item["x"]), float(item["y"])))
    return points


def _tangent(line: LineString, distance: float) -> np.ndarray:
    ahead = line.interpolate(min(line.length, distance + 2.0))
    behind = line.interpolate(max(0.0, distance - 2.0))
    vector = np.array([ahead.x - behind.x, ahead.y - behind.y], dtype=np.float64)
    length = float(np.linalg.norm(vector))
    if length < 1e-6:
        return np.array([1.0, 0.0])
    return vector / length
