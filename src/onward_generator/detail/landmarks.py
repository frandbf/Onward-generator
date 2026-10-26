"""Landmark volumes that sit on an existing OSM footprint (phase 07.2).

A tower is added only when OpenStreetMap already records a tower point with an
observed ``height`` tag, and only on the intersection of a square around that
point with the building footprint. The square size is a modelling assumption;
the height and the point are observed. Nothing here invents streets or moves
the building.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from shapely import Point, box
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from onward_generator.detail.config import DetailConfig
from onward_generator.generation.gltf import Primitive
from onward_generator.generation.mesh import Mesh, cap, closed_manifold_defects, prism, walls
from onward_generator.generation.scene import Issue

_TOWER_KEYS = (
    ("man_made", "tower"),
    ("tower:type", "bell_tower"),
    ("historic", "tower"),
)
_HEIGHT = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(?:m|metres?|meters?)?\s*$", re.I)


@dataclass(frozen=True, slots=True)
class LandmarkPart:
    id: str
    building_id: str
    visual: tuple[Primitive, ...]
    collider: Mesh
    record: dict[str, Any]


def parse_observed_height_m(tags: Mapping[str, Any]) -> float | None:
    raw = tags.get("height")
    if raw is None:
        return None
    match = _HEIGHT.match(str(raw))
    return float(match.group(1)) if match else None


def is_tower_poi(tags: Mapping[str, Any]) -> bool:
    if tags.get("bell_tower") not in (None, "no", "false"):
        return True
    return any(tags.get(key) == value for key, value in _TOWER_KEYS)


def _point(feature: Mapping[str, Any]) -> Point | None:
    geom = feature.get("geometry") or {}
    if geom.get("type") != "Point":
        return None
    coords = geom.get("coordinates") or []
    if len(coords) < 2:
        return None
    return Point(float(coords[0]), float(coords[1]))


def attach_towers(
    building_id: str,
    footprint: BaseGeometry,
    features: Sequence[Mapping[str, Any]],
    cfg: DetailConfig,
) -> tuple[list[LandmarkPart], list[Issue]]:
    """Observed OSM tower points → prism clipped to ``footprint``. Empty if landmarks are off."""
    if not cfg.landmarks.enabled:
        return [], []
    parts: list[LandmarkPart] = []
    issues: list[Issue] = []
    settings = cfg.landmarks
    for feature in features:
        tags = feature.get("tags") or {}
        if not is_tower_poi(tags):
            continue
        point = _point(feature)
        if point is None or not footprint.covers(point):
            continue
        source_id = str(feature.get("id") or "")
        height = parse_observed_height_m(tags)
        if height is None:
            issues.append(Issue("landmark_tower_height_missing", "info", building_id,
                                f"{source_id} is a tower on this footprint but has no parseable height tag; no volume added"))
            continue
        if height > settings.max_tower_height_m or height <= 0:
            issues.append(Issue("landmark_tower_height_out_of_range", "warning", building_id,
                                f"{source_id} height={height:g} m is outside (0, {settings.max_tower_height_m:g}]; ignored"))
            continue
        half = settings.tower_half_width_m
        proposed = box(point.x - half, point.y - half, point.x + half, point.y + half)
        clipped = proposed.intersection(footprint)
        if clipped.is_empty or clipped.area < settings.min_clip_area_m2:
            issues.append(Issue("landmark_tower_footprint_too_small", "warning", building_id,
                                f"{source_id}: clipped tower area {clipped.area:.2f} m² is below {settings.min_clip_area_m2:g} m²; not added"))
            continue
        collider = prism(clipped, 0.0, height, bottom=True)
        defects = closed_manifold_defects(collider)
        if defects:
            issues.append(Issue("landmark_tower_mesh_not_closed", "critical", building_id,
                                f"{source_id}: {defects} collider defects"))
            continue
        part_id = f"landmark_tower/{building_id}/{source_id}"
        parts.append(LandmarkPart(
            part_id, building_id,
            (Primitive(walls(clipped, 0.0, height), settings.wall_material),
             Primitive(cap(clipped, height, up=True), settings.roof_material)),
            collider,
            {
                "id": part_id,
                "type": "landmark_part",
                "kind": "tower",
                "source_ids": [building_id, source_id],
                "representation": "volumetric_placeholder",
                "representative_of_real_tower": False,
                "needs_custom_model": True,
                "height": {
                    "height_m": height,
                    "origin": "observed",
                    "source_tag": "height",
                    "source_id": source_id,
                },
                "footprint": {
                    "origin": "estimated",
                    "method": "axis_aligned_square_clipped_to_building",
                    "half_width_m": half,
                    "area_m2": round(clipped.area, 3),
                    "note": "The square is a modelling assumption; it does not come from OSM. It is clipped to the observed building footprint.",
                },
                "position_m": [round(point.x, 3), round(point.y, 3)],
                "materials": {"wall": settings.wall_material, "roof": settings.roof_material},
                "generation": {"method": "observed_osm_tower_height + estimated_clip", "attribute_origin": "mixed"},
            },
        ))
    return parts, issues
