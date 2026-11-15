"""Reusable urban elements: observed OSM street furniture, generated lamps, benches and bins.

Every element is one instance of a shared prototype mesh. Observed elements keep
their OSM id; generated ones are marked ``attribute_origin: generated``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from shapely import LineString, Point, STRtree
from shapely import box as plan_box
from shapely.affinity import affine_transform
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from onward_generator.detail.config import DetailConfig
from onward_generator.detail.facades import stable_unit
from onward_generator.detail.modules import Prototype, yaw_matrix
from onward_generator.generation.surfaces import LinearWay

WALL_MOUNT_M = 0.5
"""An OSM street lamp this close to a facade hangs on it (plan Q6): Paris maps its wall lamps on the facade line."""
WALL_MOUNT_TAGS = {"lamp_mount": ("wall",), "support": ("wall_mounted", "wall")}


def wall_mounted(tags: Mapping[str, str], point: Point, buildings: BaseGeometry) -> bool:
    """Whether an observed street lamp hangs on a facade: tagged so, or standing within ``WALL_MOUNT_M`` of one."""
    if any(tags.get(key) in values for key, values in WALL_MOUNT_TAGS.items()):
        return True
    return not buildings.is_empty and buildings.distance(point) < WALL_MOUNT_M


def facade_frame(point: Point, buildings: BaseGeometry) -> tuple[float, float, float]:
    """The facade point nearest ``point`` and the heading (radians) that points out of the building there.

    The outward side is the one whose probe 0.1 m away lies outside every footprint; the heading is the normal of
    the nearest wall, so a lamp on the facade line faces the street even when it stands exactly on that line."""
    boundary = buildings.boundary
    along = boundary.project(point)
    near = boundary.interpolate(along)
    ahead, behind = boundary.interpolate(along + 0.05), boundary.interpolate(max(0.0, along - 0.05))
    tx, ty = ahead.x - behind.x, ahead.y - behind.y
    length = math.hypot(tx, ty) or 1.0
    nx, ny = -ty / length, tx / length
    if buildings.contains(Point(near.x + 0.1 * nx, near.y + 0.1 * ny)):
        nx, ny = -nx, -ny
    return near.x, near.y, math.atan2(ny, nx)


def away_from_buildings(point: Point, buildings: BaseGeometry) -> float:
    """The heading from the nearest facade to ``point``: a free-standing lamp's arm reaches toward the street."""
    if buildings.is_empty:
        return 0.0
    near = buildings.boundary.interpolate(buildings.boundary.project(point))
    dx, dy = point.x - near.x, point.y - near.y
    return math.atan2(dy, dx) if math.hypot(dx, dy) > 1e-9 else 0.0


CROWN_WALL_GAP_M = 0.1
"""A tree's crown keeps this far from every facade. Within 2 m of a wall the generated crown, up to 2 m across its
radius, went through it and showed inside the rooms: 42 of the 105 OSM trees of Murcia Catedral (13 November 2026)."""
TREE_MIN_SPACING_M = 5.0
"""The default of ``urban.tree_min_spacing_m`` (15 November 2026: a map's own detail config may change it). An observed
tree keeps this far from the trees already placed, in id order, whatever OSM maps: Murcia Catedral's rows
stood about 4 m apart (median nearest neighbour 3.97 m), 105 trees where 56 read as the same rows with crowns, up to 4 m
across, that no longer touch (13 November 2026, at the maintainer's request: fewer triangles where trees crowd)."""
MIN_CROWN_SCALE = 0.35
"""The default of ``urban.tree_min_crown_scale``. The narrowest crown, as a share of its width: a tree closer to a facade than this is left out (``crown_against_building``).
At 0.35 every OSM tree of Murcia Catedral stays, the nearest (0.82 m from a wall) as a slim pruned street tree; at 0.5
thirteen of them were lost."""


@dataclass(frozen=True, slots=True)
class Element:
    id: str
    kind: str
    matrix: np.ndarray
    origin: str  # observed | generated
    source_ids: tuple[str, ...]
    rule: str
    crown_scale: float = 1.0
    """Across a tree's crown and trunk, never along its height: below 1 where the crown would reach a facade."""


def crown_radius(proto: Prototype) -> float:
    """The farthest a prototype's visual mesh reaches from its axis, in plan."""
    return max(float(np.hypot(mesh.vertices[:, 0], mesh.vertices[:, 1]).max()) for _material, mesh in proto.parts if len(mesh.vertices))


def crown_fit(options: Sequence[str], chosen: str, prototypes: Mapping[str, Prototype], room_m: float,
              min_scale: float = MIN_CROWN_SCALE) -> tuple[str, float] | None:
    """The tree prototype and the crown scale that keep a crown ``CROWN_WALL_GAP_M`` clear of the nearest facade, which
    stands ``room_m`` away. ``chosen`` when it fits; else the first of the other ``options`` that does; else the
    narrowest prototype scaled across, down to ``min_scale``; None when not even that fits. The tree stays where
    OSM puts it."""
    room = room_m - CROWN_WALL_GAP_M
    for kind in (chosen, *(k for k in options if k != chosen)):
        if crown_radius(prototypes[kind]) <= room:
            return kind, 1.0
    narrowest = min(options, key=lambda k: (crown_radius(prototypes[k]), k))
    scale = round(room / crown_radius(prototypes[narrowest]), 3)
    return (narrowest, scale) if scale >= min_scale else None


def crown_matrix(matrix: np.ndarray, scale: float) -> np.ndarray:
    """``matrix`` with its plan axes scaled by ``scale``: a narrower crown at the same height."""
    return matrix @ np.diag([scale, scale, 1.0, 1.0])


def crown_fields(element: Element) -> dict[str, float]:
    """The record's ``crown_scale`` (estimated) when the crown was narrowed, else nothing."""
    return {"crown_scale": element.crown_scale} if element.crown_scale != 1.0 else {}


@dataclass(frozen=True, slots=True)
class Rejected:
    id: str
    kind: str
    reason: str


def classify_poi(tags: Mapping[str, str], cfg: DetailConfig) -> str | None:
    for kind, rules in cfg.urban_observed.items():
        if any(k in tags and (tags[k] in v or "*" in v) for k, v in rules.items()):
            return kind
    return None


def _footprint(proto: Prototype, matrix: np.ndarray, clearance: float) -> BaseGeometry:
    assert proto.collider is not None
    lo, hi = proto.collider.vertices.min(axis=0), proto.collider.vertices.max(axis=0)
    local = plan_box(lo[0], lo[1], hi[0], hi[1]).buffer(clearance, join_style="mitre")
    m = matrix
    return affine_transform(local, [m[0, 0], m[0, 1], m[1, 0], m[1, 1], m[0, 3], m[1, 3]])


def place_elements(
    features: Sequence[Mapping[str, Any]],
    ways: Sequence[LinearWay],
    free: BaseGeometry,
    buildings: BaseGeometry,
    prototypes: Mapping[str, Prototype],
    cfg: DetailConfig,
    *,
    doors: BaseGeometry | None = None,
) -> tuple[list[Element], list[Rejected]]:
    """Place observed POIs, then generated lamps, on ``free`` walkable ground without overlaps.

    A generated element also keeps off ``doors``, the ground in front of the buildings' doors. An observed one
    stays where OSM puts it: the doors are generated.

    An observed street lamp on a facade (``wall_mounted``) becomes a ``wall_lamp`` on the nearest facade point, facing
    out; a free-standing one reaches its arm away from the nearest facade. Every street lamp, observed or generated,
    keeps ``street_lamps.min_spacing_m`` from the lamps already placed, in id order (plan Q6: twelve observed poles
    stood within 30 x 37 m on one Rue Mouffetard square).
    """
    placed: list[Element] = []
    rejected: list[Rejected] = []
    taken: list[BaseGeometry] = []
    clearance = cfg.urban_clearance_m

    def try_place(element: Element) -> str | None:
        fp = _footprint(prototypes[element.kind], element.matrix, clearance)
        if not free.contains(fp):
            return "not_on_free_walkable_ground"
        if element.origin == "generated" and doors is not None and doors.intersects(fp):
            return "in_front_of_a_door"
        if taken and STRtree(taken).query(fp, predicate="intersects").size:
            return "overlaps_another_element"
        taken.append(fp)
        placed.append(element)
        return None

    lamps = cfg.street_lamps
    lamp_points: list[Point] = []

    def lamp_spaced(point: Point) -> bool:
        return all(point.distance(q) >= lamps.min_spacing_m for q in lamp_points)

    tree_points: list[Point] = []

    for f in sorted(features, key=lambda f: f["id"]):
        if f.get("kind") != "poi":
            continue
        tags = f.get("tags") or {}
        kind = classify_poi(tags, cfg)
        if kind is None:
            continue
        if kind == "street_lamp":
            p = shape(f["geometry"])
            if wall_mounted(tags, p, buildings) and "wall_lamp" in prototypes and not buildings.is_empty:
                x, y, heading = facade_frame(p, buildings)
                if not lamp_spaced(Point(x, y)):
                    rejected.append(Rejected(f["id"], "wall_lamp", "too_close_to_another_lamp"))
                    continue
                placed.append(Element(f"urban/wall_lamp/{f['id']}", "wall_lamp", yaw_matrix(x, y, 0.0, heading), "observed", (f["id"],),
                                      "osm_poi_on_the_nearest_facade"))
                lamp_points.append(Point(x, y))
                continue
            if not lamp_spaced(p):
                rejected.append(Rejected(f["id"], kind, "too_close_to_another_lamp"))
                continue
        proto_kind = kind
        if kind == "tree":
            options = [k for k in ("tree", "tree_1", "tree_2") if k in prototypes]
            if options:
                proto_kind = options[int(stable_unit(cfg.seed, f["id"], "canopy") * len(options))]
        if proto_kind not in prototypes:
            continue
        p = shape(f["geometry"])
        scale = 1.0
        if kind == "tree":
            if any(p.distance(q) < cfg.tree_min_spacing_m for q in tree_points):
                rejected.append(Rejected(f["id"], kind, "too_close_to_another_tree"))
                continue
            yaw = 2 * math.pi * stable_unit(cfg.seed, f["id"], "yaw")
            if not buildings.is_empty:
                fit = crown_fit(options, proto_kind, prototypes, float(buildings.distance(p)), cfg.tree_min_crown_scale)
                if fit is None:
                    rejected.append(Rejected(f["id"], kind, "crown_against_building"))
                    continue
                proto_kind, scale = fit
        elif kind == "street_lamp":
            yaw = away_from_buildings(p, buildings)
        else:
            yaw = 0.0
        element = Element(f"urban/{kind}/{f['id']}", proto_kind, crown_matrix(yaw_matrix(p.x, p.y, 0.0, yaw), scale), "observed",
                          (f["id"],), "osm_poi_position", scale)
        reason = try_place(element)
        if reason:
            rejected.append(Rejected(f["id"], kind, reason))
        elif kind == "street_lamp":
            lamp_points.append(p)
        elif kind == "tree":
            tree_points.append(p)

    if lamps.enabled and "street_lamp" in prototypes:
        for way in sorted((w for w in ways if w.road_class == "vehicular" and isinstance(w.geometry, LineString)), key=lambda w: w.id):
            line = way.geometry
            n_lamps = int(line.length // lamps.spacing_m)
            for i in range(n_lamps):
                d = lamps.spacing_m * (i + 0.5)
                side = 1.0 if i % 2 == 0 else -1.0
                p0, p1 = line.interpolate(max(0.0, d - 0.5)), line.interpolate(min(line.length, d + 0.5))
                t = np.array([p1.x - p0.x, p1.y - p0.y])
                if np.linalg.norm(t) == 0:
                    continue
                t /= np.linalg.norm(t)
                normal = np.array([-t[1], t[0]]) * side
                base = line.interpolate(d)
                pos = np.array([base.x, base.y]) + normal * (way.width_m / 2 + lamps.curb_offset_m)
                pt = Point(*pos)
                gid = f"{way.id}#lamp{i}"
                if buildings.distance(pt) < lamps.min_building_distance_m:
                    rejected.append(Rejected(gid, "street_lamp", "too_close_to_building"))
                    continue
                if any(pt.distance(q) < lamps.min_spacing_m for q in lamp_points):
                    rejected.append(Rejected(gid, "street_lamp", "too_close_to_another_lamp"))
                    continue
                arm = -normal  # the lamp arm reaches over the road
                yaw = math.atan2(arm[1], arm[0])
                element = Element(f"urban/street_lamp/{gid}", "street_lamp", yaw_matrix(pos[0], pos[1], 0.0, yaw), "generated", (way.id,),
                                  f"every {lamps.spacing_m} m, alternating sides, {lamps.curb_offset_m} m behind the curb")
                reason = try_place(element)
                if reason:
                    rejected.append(Rejected(gid, "street_lamp", reason))
                else:
                    lamp_points.append(pt)

    furniture = cfg.street_furniture
    if furniture.enabled:
        pedestrian = sorted(
            (w for w in ways if w.road_class == "pedestrian" and isinstance(w.geometry, LineString) and w.geometry.length >= 8.0),
            key=lambda w: w.id,
        )
        for item in furniture.items:
            if item.kind not in prototypes or item.max_count <= 0:
                continue
            placed_n = 0
            for way in pedestrian:
                if placed_n >= item.max_count:
                    break
                line = way.geometry
                n = int(line.length // item.spacing_m)
                for i in range(n):
                    if placed_n >= item.max_count:
                        break
                    d = item.spacing_m * (i + 0.5)
                    p0 = line.interpolate(max(0.0, d - 0.5))
                    p1 = line.interpolate(min(line.length, d + 0.5))
                    t = np.array([p1.x - p0.x, p1.y - p0.y])
                    if np.linalg.norm(t) == 0:
                        continue
                    t /= np.linalg.norm(t)
                    side = 1.0 if stable_unit(cfg.seed, way.id, item.kind, i) < 0.5 else -1.0
                    normal = np.array([-t[1], t[0]]) * side
                    base = line.interpolate(d)
                    pos = np.array([base.x, base.y]) + normal * (way.width_m / 2 + item.offset_m)
                    gid = f"{way.id}#{item.kind}{i}"
                    if buildings.distance(Point(*pos)) < 0.6:
                        rejected.append(Rejected(gid, item.kind, "too_close_to_building"))
                        continue
                    yaw = math.atan2(t[1], t[0])
                    element = Element(
                        f"urban/{item.kind}/{gid}", item.kind, yaw_matrix(float(pos[0]), float(pos[1]), 0.0, yaw),
                        "generated", (way.id,),
                        f"every {item.spacing_m} m along a pedestrian way, {item.offset_m} m outside the way",
                    )
                    reason = try_place(element)
                    if reason:
                        rejected.append(Rejected(gid, item.kind, reason))
                    else:
                        placed_n += 1
    return placed, rejected
