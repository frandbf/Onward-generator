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


@dataclass(frozen=True, slots=True)
class Element:
    id: str
    kind: str
    matrix: np.ndarray
    origin: str  # observed | generated
    source_ids: tuple[str, ...]
    rule: str


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
) -> tuple[list[Element], list[Rejected]]:
    """Place observed POIs, then generated lamps, on ``free`` walkable ground without overlaps."""
    placed: list[Element] = []
    rejected: list[Rejected] = []
    taken: list[BaseGeometry] = []
    clearance = cfg.urban_clearance_m

    def try_place(element: Element) -> str | None:
        fp = _footprint(prototypes[element.kind], element.matrix, clearance)
        if not free.contains(fp):
            return "not_on_free_walkable_ground"
        if taken and STRtree(taken).query(fp, predicate="intersects").size:
            return "overlaps_another_element"
        taken.append(fp)
        placed.append(element)
        return None

    for f in sorted(features, key=lambda f: f["id"]):
        if f.get("kind") != "poi":
            continue
        kind = classify_poi(f.get("tags") or {}, cfg)
        if kind is None:
            continue
        proto_kind = kind
        if kind == "tree":
            options = [k for k in ("tree", "tree_1", "tree_2") if k in prototypes]
            if options:
                proto_kind = options[int(stable_unit(cfg.seed, f["id"], "canopy") * len(options))]
        if proto_kind not in prototypes:
            continue
        p = shape(f["geometry"])
        yaw = 2 * math.pi * stable_unit(cfg.seed, f["id"], "yaw") if kind == "tree" else 0.0
        element = Element(f"urban/{kind}/{f['id']}", proto_kind, yaw_matrix(p.x, p.y, 0.0, yaw), "observed", (f["id"],), "osm_poi_position")
        reason = try_place(element)
        if reason:
            rejected.append(Rejected(f["id"], kind, reason))

    lamps = cfg.street_lamps
    if lamps.enabled and "street_lamp" in prototypes:
        lamp_points: list[Point] = [Point(e.matrix[0, 3], e.matrix[1, 3]) for e in placed if e.kind == "street_lamp"]
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
