"""Assign 360 pictures to exposed facades. Under development.

Party walls and landmarks are left alone. A building that received an interior
is left alone. Positions are converted to local metres with the map CRS.
Degrees are never treated as metres.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
from shapely import Point, Polygon, union_all
from shapely.geometry.base import BaseGeometry

from onward_generator.detail.config import DetailConfig, load_detail_config
from onward_generator.detail.facades import Segment, refine_exposure
from onward_generator.geo.projection import LocalFrame

MIN_DISTANCE_M = 2.0
MAX_DISTANCE_M = 30.0
MAX_INCIDENCE_DEG = 30.0
MIN_SPAN_M = 3.0
MAX_ACCURACY_M = 8.0
_SAMPLE_M = 0.25


@dataclass(frozen=True, slots=True)
class BuildingFootprint:
    id: str
    kind: str
    polygon: Polygon


@dataclass(frozen=True, slots=True)
class StreetPicture:
    id: str
    longitude: float
    latitude: float
    horizontal_accuracy_m: float | None
    author: str
    license: str


def match_facades(
    buildings: Sequence[BuildingFootprint],
    pictures: Sequence[StreetPicture],
    *,
    frame: LocalFrame,
    interior_ids: Sequence[str],
    config: DetailConfig | None = None,
) -> dict[str, object]:
    """Return assignments and skips. This function does not write a GLB."""
    cfg = config if config is not None else load_detail_config()
    interiors = set(interior_ids)
    located = [_Located(picture, _local_xy(frame, picture.longitude, picture.latitude)) for picture in pictures]
    candidates = [building for building in buildings if building.kind == "building" and building.id not in interiors]
    others_of: dict[str, BaseGeometry] = {}
    for building in candidates:
        neighbours = [other.polygon for other in buildings if other.id != building.id and other.kind == "building"]
        others_of[building.id] = union_all(neighbours) if neighbours else Polygon()

    assignments: list[dict[str, object]] = []
    skipped_faces: list[dict[str, object]] = []
    skipped_buildings: list[dict[str, str]] = []
    for building in buildings:
        if building.kind == "landmark_part":
            skipped_buildings.append({"building_id": building.id, "reason": "landmark"})
            continue
        if building.kind != "building":
            continue
        if building.id in interiors:
            skipped_buildings.append({"building_id": building.id, "reason": "interior"})
            continue
        _refined, segments = refine_exposure(building.polygon, others_of[building.id], cfg)
        for segment in segments:
            if not segment.exposed or segment.ring != 0:
                continue
            spans = _frontal_spans(building.polygon, segment, located)
            if not spans:
                skipped_faces.append({
                    "building_id": building.id,
                    "segment_index": segment.index,
                    "reason": "no_picture",
                })
                continue
            for s0, s1, located_picture, distance_m, angle_deg in spans:
                picture = located_picture.picture
                accuracy: dict[str, object]
                if picture.horizontal_accuracy_m is None:
                    accuracy = {"horizontal_accuracy_m": None, "accuracy": "unknown"}
                else:
                    accuracy = {
                        "horizontal_accuracy_m": picture.horizontal_accuracy_m,
                        "accuracy": "recorded",
                    }
                assignments.append({
                    "building_id": building.id,
                    "segment_index": segment.index,
                    "s0_m": round(s0, 3),
                    "s1_m": round(s1, 3),
                    "picture_id": picture.id,
                    "distance_m": round(distance_m, 3),
                    "angle_deg": round(angle_deg, 3),
                    "author": picture.author,
                    "license": picture.license,
                    "origin": "observed",
                    **accuracy,
                })
    return {
        "schema": "onward_generator.panoramax_match",
        "schema_version": "0.1.0",
        "assignments": assignments,
        "skipped_faces": skipped_faces,
        "skipped_buildings": skipped_buildings,
    }


@dataclass(frozen=True, slots=True)
class _Located:
    picture: StreetPicture
    xy: tuple[float, float]


def _local_xy(frame: LocalFrame, longitude: float, latitude: float) -> tuple[float, float]:
    local = frame.to_local(Point(longitude, latitude))
    return (float(local.x), float(local.y))


def _frontal_spans(
    footprint: Polygon,
    segment: Segment,
    pictures: Sequence[_Located],
) -> list[tuple[float, float, _Located, float, float]]:
    """Stretches where some picture sees the wall at ``MAX_INCIDENCE_DEG`` or less.

    Several pictures may share one face. At each sample the nearest picture wins.
    A stretch shorter than ``MIN_SPAN_M`` is not a facade texture.
    """
    usable = [item for item in pictures if _usable(footprint, item)]
    if not usable or segment.length < MIN_SPAN_M:
        return []
    count = max(1, int(math.ceil(segment.length / _SAMPLE_M)))
    samples = [min(segment.length, index * segment.length / count) for index in range(count + 1)]
    winners: list[tuple[_Located, float, float] | None] = [
        _winner_at(segment, usable, sample) for sample in samples
    ]
    spans: list[tuple[float, float, _Located, float, float]] = []
    start: int | None = None
    for index, winner in enumerate(winners):
        previous = winners[index - 1] if index else None
        same = (
            start is not None
            and winner is not None
            and previous is not None
            and winner[0].picture.id == previous[0].picture.id
        )
        if same:
            continue
        if start is not None and previous is not None:
            _close_span(spans, samples[start], samples[index - 1], previous)
        start = index if winner is not None else None
    if start is not None and winners[-1] is not None:
        _close_span(spans, samples[start], samples[-1], winners[-1])
    return spans


def _close_span(
    spans: list[tuple[float, float, _Located, float, float]],
    s0: float,
    s1: float,
    winner: tuple[_Located, float, float],
) -> None:
    if s1 - s0 < MIN_SPAN_M:
        return
    located, distance_m, angle_deg = winner
    spans.append((s0, s1, located, distance_m, angle_deg))


def _usable(footprint: Polygon, located: _Located) -> bool:
    accuracy = located.picture.horizontal_accuracy_m
    if accuracy is not None and accuracy > MAX_ACCURACY_M:
        return False
    return not footprint.covers(Point(located.xy))


def _winner_at(
    segment: Segment,
    pictures: Sequence[_Located],
    distance_along: float,
) -> tuple[_Located, float, float] | None:
    point = segment.point(distance_along)
    best: tuple[float, float, _Located] | None = None
    for located in pictures:
        accepted = _incidence(segment, located.xy, point)
        if accepted is None:
            continue
        distance_m, angle_deg = accepted
        if best is None or distance_m < best[0]:
            best = (distance_m, angle_deg, located)
    if best is None:
        return None
    return best[2], best[0], best[1]


def _incidence(
    segment: Segment,
    xy: tuple[float, float],
    point: np.ndarray,
) -> tuple[float, float] | None:
    camera = np.array([xy[0], xy[1], 0.0])
    origin = np.array([segment.a[0], segment.a[1], 0.0])
    if float(np.dot(camera - origin, segment.n_out)) <= 0.05:
        return None
    toward = np.array(point, dtype=np.float64) - camera
    toward[2] = 0.0
    distance_m = float(np.linalg.norm(toward))
    if distance_m < MIN_DISTANCE_M or distance_m > MAX_DISTANCE_M:
        return None
    inward = -segment.n_out
    inward_length = float(np.linalg.norm(inward))
    if inward_length < 1e-9:
        return None
    cosine = float(np.dot(toward / distance_m, inward / inward_length))
    angle_deg = math.degrees(math.acos(min(1.0, max(-1.0, cosine))))
    if angle_deg > MAX_INCIDENCE_DEG:
        return None
    return distance_m, angle_deg


def picture_from_feature(feature: Mapping[str, object], *, author: str, license_name: str) -> StreetPicture | None:
    """Read a cached search feature. Returns None when the fix is not a point."""
    geometry = feature.get("geometry")
    if not isinstance(geometry, Mapping) or geometry.get("type") != "Point":
        return None
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, Sequence) or len(coordinates) < 2:
        return None
    properties = feature.get("properties")
    accuracy: float | None = None
    if isinstance(properties, Mapping):
        raw_accuracy = properties.get("quality:horizontal_accuracy")
        if isinstance(raw_accuracy, (int, float)) and not isinstance(raw_accuracy, bool):
            accuracy = float(raw_accuracy)
    picture_id = feature.get("id")
    if not picture_id:
        return None
    return StreetPicture(
        id=str(picture_id),
        longitude=float(coordinates[0]),
        latitude=float(coordinates[1]),
        horizontal_accuracy_m=accuracy,
        author=author,
        license=license_name,
    )
