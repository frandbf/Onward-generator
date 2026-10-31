"""Anchors for one map, in local metres. The city name and the CRS are not read.

``geo`` has already projected the centre. This module only sees features:
buildings, highways, and points or areas tagged ``amenity``, ``leisure``,
``shop``, ``tourism``, or ``historic``. A new city does not need a new list.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
from shapely import Point, STRtree
from shapely.geometry import LineString, Polygon, shape
from shapely.geometry.polygon import orient

from onward_generator.geo.attributes import parse_metres
from onward_generator.overgrow.constants import LANE_M
from onward_generator.overgrow.fit import SKIP_HIGHWAYS

PLACE_KEYS = ("amenity", "leisure", "shop", "tourism", "historic")
MIN_EDGE_M = 2.5
MIN_STREET_M = 8.0
SAMPLE_START_M = 12.0
SAMPLE_STEP_M = 18.0
SIDEWALK_EXTRA_M = 0.6
GAP_MIN_M = 2.0
GAP_MAX_M = 16.0
ESTIMATED_HEIGHT_M = 12.0
PROMPT_FACADE_CAP = 64
PROMPT_CORNER_CAP = 64
PROMPT_GAP_CAP = 32
PROMPT_STREET_CAP = 32
PROMPT_PLACE_CAP = 32
PROMPT_ANCHOR_CAP = 64


@dataclass(frozen=True, slots=True)
class Facade:
    id: str
    feature_id: str
    ax: float
    ay: float
    bx: float
    by: float
    length_m: float
    height_m: float
    height_origin: str
    outward: tuple[float, float]
    along: tuple[float, float]

    @property
    def mid(self) -> tuple[float, float]:
        return ((self.ax + self.bx) * 0.5, (self.ay + self.by) * 0.5)


@dataclass(frozen=True, slots=True)
class Corner:
    id: str
    x: float
    y: float


@dataclass(frozen=True, slots=True)
class Gap:
    id: str
    from_id: str
    to_id: str
    length_m: float


@dataclass(frozen=True, slots=True)
class Street:
    id: str
    length_m: float
    coords: tuple[tuple[float, float], ...]


@dataclass(frozen=True, slots=True)
class Place:
    id: str
    x: float
    y: float
    tags: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class LaneAnchor:
    id: str
    x: float
    y: float
    yaw: float
    on_street: bool = True


@dataclass(frozen=True, slots=True)
class DigestLimits:
    facades: int = PROMPT_FACADE_CAP
    corners: int = PROMPT_CORNER_CAP
    gaps: int = PROMPT_GAP_CAP
    streets: int = PROMPT_STREET_CAP
    places: int = PROMPT_PLACE_CAP
    anchors: int = PROMPT_ANCHOR_CAP


@dataclass(frozen=True, slots=True)
class Digest:
    facades: tuple[Facade, ...]
    corners: tuple[Corner, ...]
    gaps: tuple[Gap, ...]
    streets: tuple[Street, ...]
    places: tuple[Place, ...]
    anchors: tuple[LaneAnchor, ...]

    def anchor_ids(self) -> dict[str, set[str]]:
        return {
            "corner": {item.id for item in self.corners},
            "facade": {item.id for item in self.facades},
            "poi": {item.id for item in self.places},
            "lane": {item.id for item in self.anchors},
        }

    def facade(self, item_id: str) -> Facade | None:
        return next((item for item in self.facades if item.id == item_id), None)

    def corner(self, item_id: str) -> Corner | None:
        return next((item for item in self.corners if item.id == item_id), None)

    def place(self, item_id: str) -> Place | None:
        return next((item for item in self.places if item.id == item_id), None)

    def anchor(self, item_id: str) -> LaneAnchor | None:
        return next((item for item in self.anchors if item.id == item_id), None)

    def to_dict(self) -> dict[str, object]:
        return {
            "facades": [
                {
                    "id": item.id,
                    "length_m": _round(item.length_m),
                    "height_m": _round(item.height_m),
                    "height_origin": item.height_origin,
                    "outward": [_round(item.outward[0], 4), _round(item.outward[1], 4)],
                }
                for item in self.facades
            ],
            "corners": [{"id": item.id, "x": _round(item.x), "y": _round(item.y)} for item in self.corners],
            "gaps": [
                {
                    "id": item.id,
                    "from_id": item.from_id,
                    "to_id": item.to_id,
                    "length_m": _round(item.length_m),
                }
                for item in self.gaps
            ],
            "streets": [{"id": item.id, "length_m": _round(item.length_m)} for item in self.streets],
            "places": [
                {"id": item.id, "x": _round(item.x), "y": _round(item.y), "tags": dict(item.tags)}
                for item in self.places
            ],
            "anchors": [
                {"id": item.id, "kind": "lane", "x": _round(item.x), "y": _round(item.y)}
                for item in self.anchors
            ],
        }

    def sha256(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_digest(scene: Mapping[str, object], *, limits: DigestLimits | None = None) -> Digest:
    """Digest of ``scene['features']`` only. Frame, origin, and CRS are ignored."""
    cap = limits or DigestLimits()
    features = [item for item in (scene.get("features") or []) if isinstance(item, dict)]
    buildings = _building_tree(features)
    facades = _nearest(_facades(features), cap.facades)
    corners = _nearest(_corners(features), cap.corners)
    streets = _nearest(_streets(features), cap.streets)
    places = _nearest(_places(features), cap.places)
    anchors = _nearest(_anchors(streets, buildings), cap.anchors)
    gaps = _gaps(corners, buildings)[: cap.gaps]
    return Digest(tuple(facades), tuple(corners), tuple(gaps), tuple(streets), tuple(places), tuple(anchors))


def _round(value: float, digits: int = 3) -> float:
    return round(float(value), digits)


def _distance_key(x: float, y: float, item_id: str) -> tuple[float, str]:
    return (x * x + y * y, item_id)


def _nearest(items: list, cap: int) -> list:
    def point(item: object) -> tuple[float, float, str]:
        if isinstance(item, Facade):
            return (*item.mid, item.id)
        if isinstance(item, Corner):
            return (item.x, item.y, item.id)
        if isinstance(item, Street):
            mid = item.length_m * 0.5
            line = LineString(item.coords)
            point_on = line.interpolate(mid)
            return (float(point_on.x), float(point_on.y), item.id)
        if isinstance(item, Place):
            return (item.x, item.y, item.id)
        if isinstance(item, LaneAnchor):
            return (item.x, item.y, item.id)
        raise TypeError(type(item).__name__)

    ranked = sorted(items, key=lambda item: _distance_key(*point(item)))
    return ranked[:cap]


def _geom(feature: Mapping[str, object]):
    raw = feature.get("geometry") or {}
    if not isinstance(raw, Mapping) or not raw.get("type"):
        return None
    return shape({"type": raw.get("type"), "coordinates": raw.get("coordinates")})


def _height(tags: Mapping[str, object]) -> tuple[float, str]:
    height = parse_metres(tags.get("height") or tags.get("building:height"))
    if height is not None:
        return height, "observed"
    levels = tags.get("building:levels")
    if levels not in (None, ""):
        try:
            return float(str(levels).split()[0]) * 3.0, "observed"
        except ValueError:
            pass
    return ESTIMATED_HEIGHT_M, "estimated"


def _polygons(geom) -> list[Polygon]:
    parts = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
    return [orient(part, sign=1.0) for part in parts if isinstance(part, Polygon) and not part.is_empty]


def _building_tree(features: Sequence[Mapping[str, object]]) -> STRtree:
    polygons: list[Polygon] = []
    for feature in features:
        if feature.get("kind") != "building":
            continue
        geom = _geom(feature)
        if geom is None:
            continue
        polygons.extend(_polygons(geom))
    return STRtree(polygons or [Point(1.0e9, 1.0e9)])


def _facades(features: Sequence[Mapping[str, object]]) -> list[Facade]:
    found: list[Facade] = []
    for feature in features:
        if feature.get("kind") != "building":
            continue
        tags = feature.get("tags") if isinstance(feature.get("tags"), Mapping) else {}
        height, origin = _height(tags)
        if origin == "observed" and height < 2.0:
            continue
        geom = _geom(feature)
        if geom is None:
            continue
        feature_id = str(feature.get("id") or "")
        for part, polygon in enumerate(_polygons(geom)):
            coords = [(float(x), float(y)) for x, y in polygon.exterior.coords]
            for index, (start, end) in enumerate(zip(coords, coords[1:])):
                edge = np.array([end[0] - start[0], end[1] - start[1]], dtype=np.float64)
                length = float(np.linalg.norm(edge))
                if length < MIN_EDGE_M:
                    continue
                along = edge / length
                outward = np.array([along[1], -along[0]], dtype=np.float64)
                found.append(Facade(
                    f"facade:{feature_id}:{part}:{index}",
                    feature_id,
                    start[0], start[1], end[0], end[1],
                    length, height, origin,
                    (float(outward[0]), float(outward[1])),
                    (float(along[0]), float(along[1])),
                ))
    return found


def _corners(features: Sequence[Mapping[str, object]]) -> list[Corner]:
    found: list[Corner] = []
    for feature in features:
        if feature.get("kind") != "building":
            continue
        tags = feature.get("tags") if isinstance(feature.get("tags"), Mapping) else {}
        height, origin = _height(tags)
        if origin == "observed" and height < 2.0:
            continue
        geom = _geom(feature)
        if geom is None:
            continue
        feature_id = str(feature.get("id") or "")
        for part, polygon in enumerate(_polygons(geom)):
            coords = [(float(x), float(y)) for x, y in polygon.exterior.coords]
            for index, (x, y) in enumerate(coords[:-1]):
                if index and (x - coords[index - 1][0]) ** 2 + (y - coords[index - 1][1]) ** 2 < 0.0025:
                    continue
                found.append(Corner(f"corner:{feature_id}:{part}:{index}", x, y))
    return found


def _streets(features: Sequence[Mapping[str, object]]) -> list[Street]:
    found: list[Street] = []
    for feature in features:
        if feature.get("kind") != "highway":
            continue
        tags = feature.get("tags") if isinstance(feature.get("tags"), Mapping) else {}
        if str(tags.get("highway") or "") in SKIP_HIGHWAYS:
            continue
        geom = _geom(feature)
        if geom is None:
            continue
        parts = [geom] if geom.geom_type == "LineString" else list(geom.geoms) if geom.geom_type == "MultiLineString" else []
        feature_id = str(feature.get("id") or "")
        for part, line in enumerate(parts):
            if line.length < MIN_STREET_M:
                continue
            coords = tuple((float(x), float(y)) for x, y in line.coords)
            found.append(Street(f"street:{feature_id}:{part}", float(line.length), coords))
    return found


def _places(features: Sequence[Mapping[str, object]]) -> list[Place]:
    found: list[Place] = []
    for feature in features:
        tags = feature.get("tags") if isinstance(feature.get("tags"), Mapping) else {}
        kept = tuple((key, str(tags[key])) for key in PLACE_KEYS if tags.get(key) not in (None, ""))
        if not kept:
            continue
        geom = _geom(feature)
        if geom is None or geom.is_empty:
            continue
        point = geom if geom.geom_type == "Point" else geom.centroid
        if point.is_empty:
            continue
        found.append(Place(f"poi:{feature.get('id')}", float(point.x), float(point.y), kept))
    return found


def _anchors(streets: Sequence[Street], buildings: STRtree) -> list[LaneAnchor]:
    found: list[LaneAnchor] = []
    for street in streets:
        line = LineString(street.coords)
        distance = SAMPLE_START_M
        sample = 0
        while distance < line.length - 4.0:
            point = line.interpolate(distance)
            tangent = _tangent(line, distance)
            normal = np.array([-tangent[1], tangent[0]], dtype=np.float64)
            yaw = float(np.arctan2(tangent[1], tangent[0]))
            for side, sign in (("p", 1.0), ("n", -1.0)):
                spot = np.array([point.x, point.y]) + normal * sign * (LANE_M + SIDEWALK_EXTRA_M)
                if buildings.query(Point(float(spot[0]), float(spot[1])), predicate="intersects").size:
                    continue
                found.append(LaneAnchor(
                    f"lane:{street.id}:{side}:{sample}",
                    float(spot[0]), float(spot[1]), yaw,
                ))
            distance += SAMPLE_STEP_M
            sample += 1
    return found


def _gaps(corners: Sequence[Corner], buildings: STRtree) -> list[Gap]:
    found: list[Gap] = []
    ordered = list(corners)
    for index, start in enumerate(ordered):
        for end in ordered[index + 1 :]:
            length = float(np.hypot(end.x - start.x, end.y - start.y))
            if length < GAP_MIN_M or length > GAP_MAX_M:
                continue
            mid = Point((start.x + end.x) * 0.5, (start.y + end.y) * 0.5)
            if buildings.query(mid, predicate="contains").size:
                continue
            pair = tuple(sorted((start.id, end.id)))
            found.append(Gap(f"gap:{pair[0]}:{pair[1]}", pair[0], pair[1], length))
    found.sort(key=lambda item: (item.length_m, item.id))
    return found


def _tangent(line: LineString, distance: float) -> np.ndarray:
    ahead = line.interpolate(min(line.length, distance + 2.0))
    behind = line.interpolate(max(0.0, distance - 2.0))
    vector = np.array([ahead.x - behind.x, ahead.y - behind.y], dtype=np.float64)
    length = float(np.linalg.norm(vector))
    if length < 1e-6:
        return np.array([1.0, 0.0])
    return vector / length
