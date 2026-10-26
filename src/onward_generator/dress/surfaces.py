"""Collect and group surfaces from a phase 04 manifest plus optional OSM tags."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from onward_generator.config.loader import MAX_SCENE_BYTES, ConfigLoadError, read_json_document

Role = Literal[
    "wall", "roof", "ground", "sidewalk", "street", "plaza", "curb",
    "water", "green", "opening", "accessory", "other",
]
Context = Literal["historic", "residential", "commercial", "religious", "modern", "unknown"]
Origin = Literal["observed", "estimated", "generated", "unknown"]
SAMPLE_ROLES: tuple[str, ...] = (
    "wall", "roof", "street", "plaza", "sidewalk", "ground", "curb",
    "green", "water", "opening", "accessory", "other",
)

RELIGIOUS_BUILDINGS = frozenset({"cathedral", "church", "chapel", "mosque", "synagogue", "basilica"})
OPENING_MATERIALS = frozenset({"door_wood", "glass_dark", "frame_stone", "railing_metal"})
ROOF_MATERIALS = frozenset({"roof_tile", "roof_flat_light", "roof_flat_grey", "building_roof"})

OSM_WALL_MATERIAL = {
    "stone": "limestone_warm",
    "limestone": "limestone_warm",
    "sandstone": "limestone_warm",
    "brick": "brick_red_wall",
    "plaster": "historic_plaster_cream",
    "stucco": "historic_plaster_cream",
    "concrete": "plaster_white_wall",
}
OSM_ROOF_MATERIAL = {
    "tile": "terracotta_roof",
    "tiles": "terracotta_roof",
    "roof_tiles": "terracotta_roof",
    "clay": "terracotta_roof",
    "concrete": "flat_roof_grey",
    "metal": "flat_roof_grey",
    "asphalt": "flat_roof_grey",
}
OSM_PAVING = {
    "asphalt": "asphalt_road",
    "concrete": "asphalt_road",
    "sett": "cobblestone_paving",
    "cobblestone": "cobblestone_paving",
    "paving_stones": "cobblestone_paving",
    "unhewn_cobblestone": "cobblestone_paving",
}

SURFACE_ROLE: dict[str, Role] = {
    "carriageway": "street",
    "sidewalk": "sidewalk",
    "pedestrian": "plaza",
    "pedestrian_cobble": "plaza",
    "ground": "ground",
    "green": "green",
    "water": "water",
    "curb": "curb",
}


class SurfaceError(ValueError):
    """The detail manifest or geo scene cannot be read as surface groups."""


@dataclass(frozen=True, slots=True)
class SurfaceGroup:
    group_id: str
    surface_ids: tuple[str, ...]
    role: Role
    object_type: str
    osm_tags: dict[str, str]
    building_context: Context
    orientation: dict[str, Any]
    inclination_deg: float | None
    height_m: float | None
    dimensions_m: dict[str, float | None]
    area_m2: float | None
    current_material: str | None
    current_origin: Origin
    confidence_hint: float
    count: int
    osm_override: str | None = None

    def to_document(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "surface_ids": list(self.surface_ids),
            "role": self.role,
            "object_type": self.object_type,
            "osm_tags": dict(self.osm_tags),
            "building_context": self.building_context,
            "orientation": dict(self.orientation),
            "inclination_deg": self.inclination_deg,
            "height_m": self.height_m,
            "dimensions_m": dict(self.dimensions_m),
            "area_m2": self.area_m2,
            "current_material": self.current_material,
            "current_origin": self.current_origin,
            "confidence_hint": self.confidence_hint,
            "count": self.count,
        }


@dataclass(frozen=True, slots=True)
class SurfaceCollection:
    groups: tuple[SurfaceGroup, ...]
    fingerprint: dict[str, Any]
    objects: tuple[dict[str, Any], ...]
    source_files: dict[str, str]

    def limited(self, n: int | None) -> SurfaceCollection:
        """Keep at most ``n`` groups, round-robin by role so a sample is not all street furniture."""
        if n is None or n >= len(self.groups):
            return self
        buckets: dict[str, list[SurfaceGroup]] = defaultdict(list)
        for group in self.groups:
            buckets[group.role].append(group)
        picked: list[SurfaceGroup] = []
        while len(picked) < n and any(buckets.values()):
            for role in SAMPLE_ROLES:
                if buckets[role]:
                    picked.append(buckets[role].pop(0))
                    if len(picked) >= n:
                        break
        return SurfaceCollection(tuple(picked), self.fingerprint, self.objects, self.source_files)


def _tags_of(features: Mapping[str, Mapping[str, Any]], source_ids: Sequence[str]) -> dict[str, str]:
    merged: dict[str, str] = {}
    for sid in source_ids:
        tags = features.get(sid, {}).get("tags") or {}
        for key, value in tags.items():
            if isinstance(value, str) and key not in merged:
                merged[key] = value
    return merged


def building_context(tags: Mapping[str, str], category: str | None) -> tuple[Context, Origin]:
    """Context from explicit OSM tags or the heights category (itself tag-based). Never from footprint shape."""
    if tags.get("historic"):
        return "historic", "observed"
    if tags.get("amenity") == "place_of_worship" or tags.get("building") in RELIGIOUS_BUILDINGS:
        return "religious", "observed"
    arch = (tags.get("building:architecture") or tags.get("architectural_style") or "").lower()
    if arch == "modern":
        return "modern", "observed"
    if category == "religious":
        return "religious", "estimated"
    if category in {"residential_multi", "residential_small", "residential"}:
        return "residential", "estimated"
    if category == "commercial":
        return "commercial", "estimated"
    return "unknown", "unknown"


def osm_material_override(role: Role, tags: Mapping[str, str]) -> str | None:
    if role == "wall":
        value = tags.get("building:material") or tags.get("material")
        return OSM_WALL_MATERIAL.get(value) if value else None
    if role == "roof":
        value = tags.get("roof:material")
        return OSM_ROOF_MATERIAL.get(value) if value else None
    if role in {"street", "plaza", "sidewalk"}:
        value = tags.get("surface")
        if not value:
            return None
        if value in {"sett", "cobblestone", "paving_stones", "unhewn_cobblestone"}:
            return "cobblestone_paving"
        if role == "street" and value in {"asphalt", "concrete"}:
            return "asphalt_road"
        if role == "sidewalk" and value in {"concrete", "paving_stones"}:
            return "sidewalk_concrete"
    return None


def _orientation(role: Role, inclination: float | None) -> dict[str, Any]:
    if role in {"wall", "curb"}:
        return {"normal": [0.0, 0.0, 0.0], "facing": "vertical"}
    if role == "roof" and inclination and inclination > 5:
        return {"normal": [0.0, 0.0, 1.0], "facing": "inclined"}
    if role in {"roof", "ground", "sidewalk", "street", "plaza", "water", "green"}:
        return {"normal": [0.0, 0.0, 1.0], "facing": "horizontal"}
    return {"normal": [0.0, 0.0, 0.0], "facing": "unknown"}


def _related_tags(tags: Mapping[str, str]) -> dict[str, str]:
    keys = (
        "building", "building:material", "material", "roof:material", "roof:shape",
        "historic", "amenity", "surface", "highway", "place", "leisure", "landuse",
        "natural", "name", "building:architecture", "architectural_style",
    )
    return {k: tags[k] for k in keys if k in tags}


@dataclass
class _Draft:
    surface_id: str
    role: Role
    object_type: str
    osm_tags: dict[str, str]
    context: Context
    inclination_deg: float | None
    height_m: float | None
    width_m: float | None
    depth_m: float | None
    area_m2: float | None
    current_material: str | None
    current_origin: Origin
    confidence_hint: float
    osm_override: str | None = None


def _drafts_from_object(obj: Mapping[str, Any], features: Mapping[str, Mapping[str, Any]]) -> list[_Draft]:
    obj_type = str(obj.get("type") or "other")
    obj_id = str(obj.get("id") or "")
    source_ids = [str(s) for s in obj.get("source_ids") or []]
    tags = _related_tags(_tags_of(features, source_ids))
    category = None
    facade = obj.get("facade") if isinstance(obj.get("facade"), Mapping) else {}
    height = obj.get("height") if isinstance(obj.get("height"), Mapping) else {}
    if facade:
        # heights category is stored on the facade template applies_to, not always on the record
        pass
    gen = obj.get("generation") if isinstance(obj.get("generation"), Mapping) else {}
    origin: Origin
    attr = obj.get("attribute_origin") or gen.get("attribute_origin") or (facade or {}).get("attribute_origin")
    if attr in {"observed", "estimated", "generated"}:
        origin = attr  # type: ignore[assignment]
    else:
        origin = "generated" if obj_type in {"surface", "curb", "urban_element"} else "unknown"

    drafts: list[_Draft] = []

    if obj_type == "building":
        height_m = height.get("height_m") if height else None
        area = obj.get("footprint_area_m2")
        roof = obj.get("roof") if isinstance(obj.get("roof"), Mapping) else {}
        wall_mat = (facade or {}).get("materials", {}).get("wall") if facade else None
        roof_mat = (facade or {}).get("materials", {}).get("roof") if facade else (roof.get("material") if roof else None)
        template = (facade or {}).get("template") or ""
        category = _category_from_template(template)
        context, ctx_origin = building_context(tags, category)
        hint = 0.7 if ctx_origin == "observed" else 0.35
        pitch = None
        if isinstance(roof.get("pitch_deg"), (int, float)):
            pitch = float(roof["pitch_deg"])
        elif roof.get("type") == "hip":
            pitch = 22.0
        elif roof.get("type") == "flat":
            pitch = 0.0
        wall_override = osm_material_override("wall", tags)
        roof_override = osm_material_override("roof", tags)
        drafts.append(_Draft(
            f"{obj_id}#wall", "wall", "building", tags, context, 90.0, _num(height_m),
            None, None, _num(area), wall_mat or "building_wall", "generated", hint, wall_override,
        ))
        drafts.append(_Draft(
            f"{obj_id}#roof", "roof", "building", tags, context, pitch, _num(height_m),
            None, None, _num(roof.get("area_m2") or area), roof_mat or "building_roof", "generated", hint, roof_override,
        ))
        materials = [str(m) for m in obj.get("materials") or []]
        for mat in materials:
            if mat in OPENING_MATERIALS:
                role: Role = "opening"
                drafts.append(_Draft(
                    f"{obj_id}#{mat}", role, "building", tags, context, 90.0, None,
                    None, None, None, mat, "generated", 0.5, None,
                ))
        return drafts

    if obj_type == "landmark_part":
        context, ctx_origin = building_context(tags, "religious" if "tower" in obj_id else None)
        mats = [str(m) for m in obj.get("materials") or []]
        height_m = obj.get("height_m")
        for mat in mats or ["stone_warm"]:
            role = "roof" if mat in ROOF_MATERIALS else "wall"
            drafts.append(_Draft(
                f"{obj_id}#{mat}", role, "landmark_part", tags, context, 90.0 if role == "wall" else 0.0,
                _num(height_m), None, None, None, mat, "generated",
                0.6 if ctx_origin == "observed" else 0.3, osm_material_override(role, tags),
            ))
        return drafts

    if obj_type in {"surface", "curb"}:
        surface_class = str(obj.get("surface_class") or obj_type)
        role = SURFACE_ROLE.get(surface_class, "other")
        override = osm_material_override(role, tags)
        hint = 0.8 if override else (0.55 if origin == "observed" else 0.3)
        mats = [str(m) for m in obj.get("materials") or [surface_class]]
        drafts.append(_Draft(
            obj_id, role, obj_type, tags, "unknown", 0.0, None,
            None, None, _num(obj.get("area_m2")), mats[0], origin, hint, override,
        ))
        return drafts

    if obj_type == "urban_element":
        kind = str(obj.get("kind") or "accessory")
        mat = "tree_canopy" if kind.startswith("tree") else "urban_metal"
        if kind.startswith("tree"):
            drafts.append(_Draft(
                f"{obj_id}#trunk", "accessory", "urban_element", tags, "unknown", None, None,
                None, None, None, "tree_trunk", origin, 0.7, None,
            ))
            drafts.append(_Draft(
                f"{obj_id}#canopy", "accessory", "urban_element", tags, "unknown", None, None,
                None, None, None, "tree_canopy", origin, 0.7, None,
            ))
            return drafts
        drafts.append(_Draft(
            obj_id, "accessory", "urban_element", tags, "unknown", None, None,
            None, None, None, mat, origin, 0.6 if origin == "observed" else 0.3, None,
        ))
        return drafts

    return drafts


def _category_from_template(template: str) -> str | None:
    mapping = {
        "generic_residential": "residential_multi",
        "generic_commercial": "commercial",
        "generic_public": "public",
        "generic_minor": "minor",
        "landmark_placeholder": "religious",
    }
    return mapping.get(template)


def _num(value: Any) -> float | None:
    if isinstance(value, (int, float)) and value == value:  # noqa: PLR0124
        return float(value)
    return None


def _group_key(d: _Draft) -> tuple[Any, ...]:
    return (
        d.role, d.object_type, d.context, d.current_material, d.current_origin,
        d.osm_override or "", tuple(sorted(d.osm_tags.items())),
    )


def _group_id(first: _Draft, used: set[str]) -> str:
    parts = [first.role, first.object_type, first.context, first.current_material or "unknown"]
    if first.osm_override:
        parts.append(first.osm_override)
    if first.osm_tags:
        parts.append("+".join(f"{k}={v}" for k, v in sorted(first.osm_tags.items())[:3]))
    else:
        head = first.surface_id.split("#", 1)[0]
        token = head.split("/")[1] if "/" in head else head
        parts.append(token)
    group_id = "/".join(parts)
    if group_id in used:
        group_id = f"{group_id}#{first.surface_id}"
    return group_id


def group_drafts(drafts: Sequence[_Draft]) -> list[SurfaceGroup]:
    buckets: dict[tuple[Any, ...], list[_Draft]] = defaultdict(list)
    for d in drafts:
        buckets[_group_key(d)].append(d)
    groups: list[SurfaceGroup] = []
    used_ids: set[str] = set()
    for key, items in sorted(buckets.items(), key=lambda kv: (kv[1][0].role, kv[1][0].surface_id)):
        first = items[0]
        areas = [i.area_m2 for i in items if i.area_m2 is not None]
        heights = [i.height_m for i in items if i.height_m is not None]
        group_id = _group_id(first, used_ids)
        used_ids.add(group_id)
        groups.append(
            SurfaceGroup(
                group_id=group_id,
                surface_ids=tuple(i.surface_id for i in items),
                role=first.role,
                object_type=first.object_type,
                osm_tags=dict(first.osm_tags),
                building_context=first.context,
                orientation=_orientation(first.role, first.inclination_deg),
                inclination_deg=first.inclination_deg,
                height_m=round(sum(heights) / len(heights), 3) if heights else None,
                dimensions_m={"width_m": first.width_m, "depth_m": first.depth_m},
                area_m2=round(sum(areas), 3) if areas else None,
                current_material=first.current_material,
                current_origin=first.current_origin,
                confidence_hint=min(i.confidence_hint for i in items),
                count=len(items),
                osm_override=first.osm_override,
            )
        )
    return groups


def _load_objects(detail_dir: Path) -> tuple[list[dict[str, Any]], Path]:
    manifest = detail_dir / "scene_detail.json"
    if not manifest.is_file():
        raise SurfaceError(f"detail manifest not found: {manifest}")
    try:
        doc, resolved = read_json_document(manifest, max_bytes=MAX_SCENE_BYTES)
    except ConfigLoadError as exc:
        raise SurfaceError(str(exc)) from exc
    objects = doc.get("objects")
    if not isinstance(objects, list):
        raise SurfaceError(f"{resolved}: objects must be a JSON array")
    return [o for o in objects if isinstance(o, dict)], resolved


def _load_features(scene_path: Path | None) -> dict[str, Mapping[str, Any]]:
    if scene_path is None:
        return {}
    try:
        doc, _ = read_json_document(scene_path, max_bytes=MAX_SCENE_BYTES)
    except ConfigLoadError as exc:
        raise SurfaceError(str(exc)) from exc
    features = {}
    for feat in doc.get("features") or []:
        if isinstance(feat, dict) and feat.get("id"):
            features[str(feat["id"])] = feat
    return features


def collect_surfaces(
    detail_dir: str | Path,
    *,
    scene_path: str | Path | None = None,
    limit: int | None = None,
) -> SurfaceCollection:
    from onward_generator.dress.geometry import geometry_fingerprint

    detail = Path(detail_dir).expanduser()
    objects, manifest = _load_objects(detail)
    scene = Path(scene_path).expanduser() if scene_path else _scene_from_manifest(detail)
    features = _load_features(scene)
    drafts: list[_Draft] = []
    for obj in objects:
        drafts.extend(_drafts_from_object(obj, features))
    groups = group_drafts(drafts)
    collection = SurfaceCollection(
        tuple(groups),
        geometry_fingerprint(objects),
        tuple(objects),
        {"detail_manifest": str(manifest), "geo_scene": str(scene) if scene and scene.is_file() else ""},
    )
    return collection.limited(limit)


def _scene_from_manifest(detail_dir: Path) -> Path | None:
    manifest = detail_dir / "scene_detail.json"
    try:
        doc = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    path = ((doc.get("inputs") or {}).get("geo_scene") or {}).get("path")
    if not path:
        return None
    candidate = Path(path)
    return candidate if candidate.is_file() else None
