"""Place Asset Store props on a scene copy. Never invent parking on the carriageway."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from onward_generator import __version__
from onward_generator.config.loader import MAX_SCENE_BYTES, ConfigLoadError, read_json_document
from onward_generator.dress.geometry import compare_fingerprints, file_sha256, geometry_fingerprint
from onward_generator.io import atomic_write_bytes, atomic_write_json, prepare_outputs
from onward_generator.props.catalog import PropCatalog, PropEntry, load_catalog

GEOGRAPHIC = frozenset({"building", "surface", "curb", "landmark_part"})
OUTPUT_FILES = (
    "scene_visual.glb",
    "scene_collision.glb",
    "scene_detail.json",
    "detail_report.json",
    "props_instances.json",
    "props_report.json",
)
MAX_VEHICLES = 4
SURFACE_PARKING = frozenset({"parking", "parking_space"})
UNDERGROUND = frozenset({"underground", "multi-storey", "rooftop"})
PROTECTED = frozenset({
    "scene_detail_v07_2", "scene_detail_v08",
    "scene_detail_dress_01", "scene_detail_dress_02", "scene_detail_dress_03",
    "scene_detail_materials_01", "scene_detail_materials_02", "scene_detail_materials_03",
})


class PlaceError(ValueError):
    """The props layer cannot place instances on this scene."""


def _scene_path(manifest: Mapping[str, Any], explicit: str | Path | None) -> Path | None:
    if explicit:
        path = Path(explicit).expanduser()
        return path if path.is_file() else None
    raw = ((manifest.get("inputs") or {}).get("geo_scene") or {}).get("path")
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_file() else None


def _features(scene_path: Path) -> list[dict[str, Any]]:
    try:
        doc, _ = read_json_document(scene_path, max_bytes=MAX_SCENE_BYTES)
    except ConfigLoadError as exc:
        raise PlaceError(str(exc)) from exc
    return [f for f in (doc.get("features") or []) if isinstance(f, dict)]


def _centroid(geom: BaseGeometry) -> tuple[float, float] | None:
    if geom.is_empty:
        return None
    c = geom.centroid
    return float(c.x), float(c.y)


def _parking_points(features: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Surface parking only. Underground entrances are not places to stand a car."""
    points: list[dict[str, Any]] = []
    for feat in features:
        tags = feat.get("tags") or {}
        amenity = tags.get("amenity") or tags.get("parking")
        if amenity not in SURFACE_PARKING and tags.get("highway") != "parking_space":
            continue
        if tags.get("parking") in UNDERGROUND:
            continue
        geom = shape(feat["geometry"]) if feat.get("geometry") else None
        if geom is None:
            continue
        xy = _centroid(geom)
        if xy is None:
            continue
        points.append({"source_id": str(feat.get("id")), "x": xy[0], "y": xy[1], "tags": dict(tags)})
    return points


def _usable_vehicles(catalog: PropCatalog) -> list[PropEntry]:
    cars = [p for p in catalog.usable_props if p.category == "vehicle" and "osm" in p.allowed_origin]
    # Prefer civilian names; skip duplicate prefab/fbx of the same stem.
    seen: set[str] = set()
    unique: list[PropEntry] = []
    for prop in cars:
        stem = Path(prop.path).stem.lower()
        if stem in seen:
            continue
        seen.add(stem)
        unique.append(prop)
    return unique


def plan_instances(
    features: Sequence[Mapping[str, Any]],
    catalog: PropCatalog,
    *,
    max_vehicles: int = MAX_VEHICLES,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    vehicles = _usable_vehicles(catalog)
    spots = _parking_points(features)
    instances: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    if not vehicles:
        skipped.append({"reason": "no_usable_vehicle_props", "count": 0})
    if not spots:
        skipped.append({"reason": "no_surface_parking_osm", "count": 0})
    for i, spot in enumerate(spots[:max_vehicles]):
        if not vehicles:
            break
        prop = vehicles[i % len(vehicles)]
        instances.append({
            "instance_id": f"prop/{prop.prop_id}/{spot['source_id']}",
            "prop_id": prop.prop_id,
            "pack_id": prop.pack_id,
            "category": prop.category,
            "path": prop.path,
            "x": round(spot["x"], 3),
            "y": round(spot["y"], 3),
            "yaw_deg": 0.0,
            "origin": "osm",
            "source_ids": [spot["source_id"]],
        })
    trees = [p for p in catalog.usable_props if p.category == "ambient" and "tree" in p.prop_id]
    if not trees:
        skipped.append({"reason": "no_usable_tree_props", "note": "dress generated trees stay"})
    return instances, skipped


def place_props(
    base_dir: str | Path,
    out_dir: str | Path,
    *,
    catalog_path: str | Path | None = None,
    scene_path: str | Path | None = None,
    overwrite: bool = False,
    max_vehicles: int = MAX_VEHICLES,
) -> dict[str, Any]:
    base = Path(base_dir).expanduser().resolve()
    out = Path(out_dir).expanduser().resolve()
    if out.name in PROTECTED:
        raise PlaceError(f"refusing to write protected artifact directory {out.name}")
    if out == base:
        raise PlaceError("output must be a new directory")
    if not (base / "scene_visual.glb").is_file() or not (base / "scene_detail.json").is_file():
        raise PlaceError(f"{base}: needs scene_visual.glb and scene_detail.json")

    catalog = load_catalog(catalog_path)
    try:
        manifest, _ = read_json_document(base / "scene_detail.json", max_bytes=MAX_SCENE_BYTES)
    except ConfigLoadError as exc:
        raise PlaceError(str(exc)) from exc
    scene = _scene_path(manifest, scene_path)
    if scene is None:
        raise PlaceError("props place needs a geo_scene.json (pass --scene or record it in the manifest)")
    features = _features(scene)
    instances, skipped = plan_instances(features, catalog, max_vehicles=max_vehicles)
    objects = [o for o in (manifest.get("objects") or []) if isinstance(o, dict)]
    before = geometry_fingerprint([o for o in objects if o.get("type") in GEOGRAPHIC])

    protected = (base / "scene_visual.glb", base / "scene_collision.glb", base / "scene_detail.json")
    targets = prepare_outputs(out, OUTPUT_FILES, overwrite=overwrite, protected=protected)
    atomic_write_bytes(targets[0], (base / "scene_visual.glb").read_bytes())
    if (base / "scene_collision.glb").is_file():
        atomic_write_bytes(targets[1], (base / "scene_collision.glb").read_bytes())
    else:
        atomic_write_bytes(targets[1], b"")
    written = dict(manifest)
    written["fidelity"] = {
        **dict(manifest.get("fidelity") or {}),
        "props": "copy; Asset Store instances listed, not baked into the GLB",
    }
    atomic_write_json(targets[2], written)
    if (base / "detail_report.json").is_file():
        atomic_write_bytes(targets[3], (base / "detail_report.json").read_bytes())
    else:
        atomic_write_json(targets[3], {})
    payload = {
        "schema": "onward_generator.prop_instances",
        "schema_version": "0.1.0",
        "instances": instances,
    }
    atomic_write_json(targets[4], payload)
    after = geometry_fingerprint([o for o in objects if o.get("type") in GEOGRAPHIC])
    report = {
        "schema": "onward_generator.props_place_report",
        "schema_version": "0.1.0",
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "generator_version": __version__,
        "base_dir": str(base),
        "catalog": str(catalog.path),
        "usable_props": len(catalog.usable_props),
        "instances": len(instances),
        "vehicles": sum(1 for i in instances if i["category"] == "vehicle"),
        "skipped": skipped,
        "geometry": compare_fingerprints(before, after),
        "input_hashes": {
            "scene_detail.json": file_sha256(base / "scene_detail.json"),
            "scene_visual.glb": file_sha256(base / "scene_visual.glb"),
        },
    }
    atomic_write_json(targets[5], report)
    return report
