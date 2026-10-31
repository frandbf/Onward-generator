"""Overture places and buildings against one geo scene, offline (ADR-021).

Places become third-party POIs in the scene's local grid: kept when their
confidence and operating status pass the configuration and their category maps
to one ``shop``/``amenity``/``office``/``craft`` tag. ``detail`` uses them only
for a building that has no OpenStreetMap POI.

Buildings lend a height or a floor count to the OSM footprint they overlap best,
when the intersection over union reaches ``min_iou``. Each value keeps the
Overture dataset that produced it, its licence and its origin: a dataset the
configuration does not call observed is ``estimated``, and an ML footprint
dataset is ``ai_generated``. The result is ``heights_overrides.overture.json``,
read by ``heights --secondary-overrides`` below Catastro and above OSM
``building:levels`` (docs/04-heights.md).
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

import duckdb
import shapely
from jsonschema import Draft202012Validator
from shapely import STRtree
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from onward_generator.config.loader import ConfigLoadError, read_json_document
from onward_generator.geo.projection import LocalFrame
from onward_generator.overture.acquire import DOCUMENTED_LICENSES, FetchRecord, attribution_lines

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "overture.default.json"
SCHEMA_PATH = REPO_ROOT / "config" / "overture.schema.json"
OVERRIDES_VERSION = "0.1.0"
PLACES_SCHEMA = "onward_generator.overture_places"
REPORT_SCHEMA = "onward_generator.overture_match_report"
SCHEMA_VERSION = "0.1.0"
HEIGHT_BOUNDS_M = (2.5, 120.0)
"""``bounds_m`` of config/heights.default.json: the heights stage refuses an override outside it."""


class OvertureConfigError(ValueError):
    """The Overture configuration is invalid."""


@dataclass(frozen=True, slots=True)
class OvertureConfig:
    min_confidence: float
    operating_status: frozenset[str]
    accept_missing_status: bool
    categories: Mapping[str, tuple[str, str]]
    min_iou: float
    skip_datasets: frozenset[str]
    origins: Mapping[str, str]
    default_origin: str
    confidence: Mapping[str, float]
    path: str
    version: str


@cache
def _validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))


def load_config(path: str | Path | None = None) -> OvertureConfig:
    """Read and validate ``config/overture.default.json`` or ``path``."""
    try:
        doc, resolved = read_json_document(path or DEFAULT_CONFIG_PATH)
    except ConfigLoadError as exc:
        raise OvertureConfigError(f"{path or DEFAULT_CONFIG_PATH}: {exc.diagnostic.message}") from exc
    errors = sorted(_validator().iter_errors(doc), key=lambda e: list(e.absolute_path))
    if errors:
        raise OvertureConfigError(f"{resolved}: " + "; ".join(f"/{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in errors))
    places, buildings = doc["places"], doc["buildings"]
    return OvertureConfig(
        min_confidence=float(places["min_confidence"]),
        operating_status=frozenset(places["operating_status"]),
        accept_missing_status=bool(places["accept_missing_status"]),
        categories={name: next(iter(tag.items())) for name, tag in places["categories"].items()},
        min_iou=float(buildings["min_iou"]),
        skip_datasets=frozenset(buildings["skip_datasets"]),
        origins=dict(buildings["origins"]),
        default_origin=str(buildings["default_origin"]),
        confidence={k: float(v) for k, v in buildings["confidence"].items()},
        path=str(resolved),
        version=str(doc["version"]),
    )


def scene_frame(scene: Mapping[str, Any]) -> LocalFrame:
    """The local grid of a geo scene, rebuilt from its WGS84 origin and CRS."""
    frame = scene.get("frame") or {}
    if frame.get("unit") != "m" or frame.get("coordinate_system") != "local_grid":
        raise ValueError("scene must be in the local metric grid (unit m)")
    lon, lat = frame["origin_wgs84"]
    return LocalFrame.create(float(lon), float(lat), str(frame["crs"]))


def _whole_record_source(sources: Sequence[Mapping[str, Any]] | None) -> Mapping[str, Any] | None:
    """The source of the whole record: the entry with no ``property``."""
    return next((s for s in sources or [] if not s.get("property")), None)


def _record_source(sources: Sequence[Mapping[str, Any]] | None, prop: str) -> Mapping[str, Any] | None:
    """The source of one property (``/properties/height``), else the whole-record source."""
    return next((s for s in sources or [] if s.get("property") == prop), None) or _whole_record_source(sources)


def _license(source: Mapping[str, Any] | None) -> tuple[str, str]:
    dataset = str((source or {}).get("dataset") or "unknown")
    stated = (source or {}).get("license")
    return dataset, str(stated or DOCUMENTED_LICENSES.get(dataset) or "unknown")


def _rows(parquet: Path, columns: str) -> list[tuple[Any, ...]]:
    connection = duckdb.connect()
    try:
        return connection.execute(f"SELECT {columns} FROM read_parquet(?) ORDER BY id", [str(parquet)]).fetchall()
    finally:
        connection.close()


def place_tag(taxonomy_primary: str | None, basic_category: str | None, cfg: OvertureConfig) -> tuple[str, str, str] | None:
    """``(key, value, matched category)``: ``taxonomy.primary`` first, then ``basic_category``."""
    for name in (taxonomy_primary, basic_category):
        if name and name in cfg.categories:
            key, value = cfg.categories[name]
            return key, value, name
    return None


def read_places(
    parquet: Path, frame: LocalFrame, cfg: OvertureConfig, allowed: frozenset[str],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Places that pass the configuration and whose licence is in ``allowed``, in the scene's local grid, and the count of each rejection."""
    rows = _rows(parquet, "id, names.primary, basic_category, taxonomy.primary, confidence, operating_status, sources, ST_AsWKB(geometry)")  # pragma: no mutate: SQL names are case-insensitive
    kept: list[dict[str, Any]] = []
    skipped: Counter[str] = Counter()
    for gers, name, basic, primary, confidence, status, sources, wkb in rows:
        if confidence is None or confidence < cfg.min_confidence:
            skipped["low_confidence"] += 1
            continue
        if status is None and not cfg.accept_missing_status:
            skipped["no_operating_status"] += 1
            continue
        if status is not None and status not in cfg.operating_status:
            skipped[f"status_{status}"] += 1
            continue
        tag = place_tag(primary, basic, cfg)
        if tag is None:
            skipped["unmapped_category"] += 1
            continue
        point = frame.to_local(shapely.from_wkb(wkb))
        if point.geom_type != "Point":
            skipped["not_a_point"] += 1
            continue
        dataset, lic = _license(_whole_record_source(sources))
        if lic not in allowed:
            skipped["licence_not_allowed"] += 1
            continue
        key, value, matched = tag
        tags = {key: value} | ({"name": " ".join(str(name).split())} if name else {})
        kept.append({
            "id": f"overture-place-{gers}",
            "gers_id": gers,
            "point_m": [round(point.x, 3), round(point.y, 3)],
            "tags": tags,
            "category": matched,
            "basic_category": basic,
            "taxonomy_primary": primary,
            "confidence": round(float(confidence), 4),
            "operating_status": status,
            "dataset": dataset,
            "license": lic,
            "attribute_origin": "observed",
            "observed_by": "third_party",
            "attribution": attribution_lines([(dataset, lic)])[0],
        })
    return kept, dict(sorted(skipped.items()))


def places_document(places: Sequence[Mapping[str, Any]], record: FetchRecord, skipped: Mapping[str, int], frame: LocalFrame) -> dict[str, Any]:
    datasets = Counter((p["dataset"], p["license"]) for p in places)
    return {
        "schema": PLACES_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "source": {"path": record.path, "sha256": record.sha256, "release": record.release, "retrieved_at": record.retrieved_at},
        "frame": frame.to_dict(),
        "counts": {"rows": record.rows, "kept": len(places), "skipped": dict(skipped)},
        "licenses": [{"dataset": d, "license": lic, "places": n} for (d, lic), n in sorted(datasets.items())],
        "attribution": attribution_lines((p["dataset"], p["license"]) for p in places),
        "places": list(places),
    }


@dataclass(frozen=True, slots=True)
class BuildingMatch:
    building_id: str
    gers_id: str
    iou: float
    height_m: float | None
    levels: int | None
    dataset: str
    license: str
    origin: str
    facade_color: str | None
    roof_color: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "building_id": self.building_id, "gers_id": self.gers_id, "iou": self.iou, "height_m": self.height_m,
            "levels": self.levels, "dataset": self.dataset, "license": self.license, "origin": self.origin,
            "tint_hints": {"facade_color": self.facade_color, "roof_color": self.roof_color},
        }


def _footprints(scene: Mapping[str, Any]) -> dict[str, BaseGeometry]:
    return {str(f["id"]): shape(f["geometry"]) for f in scene["features"] if f.get("kind") == "building"}


def _iou(a: BaseGeometry, b: BaseGeometry) -> float:
    union = a.union(b).area
    return a.intersection(b).area / union if union > 0 else 0.0


def _lent_values(height: float | None, floors: int | None, sources: Sequence[Mapping[str, Any]] | None, cfg: OvertureConfig,
                 allowed: frozenset[str], skipped: Counter[str]) -> tuple[float | None, int | None, str, str] | None:
    """``(height, levels, dataset, licence)`` an Overture building can lend, or None.

    Each value keeps the dataset of its own property. A value from ``skip_datasets`` (OpenStreetMap: already in
    the extract) or a height outside the heights bounds is dropped. When both remain from different datasets,
    only the height is kept, so one entry has one dataset and one licence. A licence not in ``allowed``
    (``config/sources.json``, so also ``unknown``) drops the entry.
    """
    value = float(height) if height is not None and height > 0 else None
    levels = int(floors) if floors is not None and floors >= 1 else None
    if value is None and levels is None:
        skipped["no_height_or_floors"] += 1
        return None
    if value is not None and not HEIGHT_BOUNDS_M[0] <= value <= HEIGHT_BOUNDS_M[1]:
        skipped["height_out_of_bounds"] += 1
        value = None
    height_from = _license(_record_source(sources, "/properties/height"))
    floors_from = _license(_record_source(sources, "/properties/num_floors"))
    lent = (None if height_from[0] in cfg.skip_datasets else value, None if floors_from[0] in cfg.skip_datasets else levels)
    if lent == (None, None):
        if (value, levels) != (None, None):
            skipped["skipped_dataset"] += 1
        return None
    value, levels = lent
    if value is not None and levels is not None and floors_from != height_from:
        levels = None
    dataset, lic = height_from if value is not None else floors_from
    if lic not in allowed:
        skipped["licence_not_allowed"] += 1
        return None
    return value, levels, dataset, lic


def match_buildings(
    parquet: Path, scene: Mapping[str, Any], cfg: OvertureConfig, allowed: frozenset[str],
) -> tuple[list[BuildingMatch], dict[str, int]]:
    """One Overture footprint per OSM footprint, best intersection over union first, each used once.

    A value whose licence is not in ``allowed`` is not lent."""
    frame = scene_frame(scene)
    rows = _rows(parquet, "id, height, num_floors, facade_color, roof_color, sources, ST_AsWKB(geometry)")  # pragma: no mutate: SQL names are case-insensitive
    footprints = _footprints(scene)
    ids = sorted(footprints)
    tree = STRtree([footprints[i] for i in ids]) if ids else None
    skipped: Counter[str] = Counter()
    pairs: list[tuple[float, str, int]] = []
    candidates: list[tuple[str, tuple[float | None, int | None, str, str], str | None, str | None]] = []
    for gers, height, floors, facade, roof, sources, wkb in rows:
        lent = _lent_values(height, floors, sources, cfg, allowed, skipped)
        if lent is None:
            continue
        geom = frame.to_local(shapely.from_wkb(wkb))
        if tree is None or geom.geom_type not in ("Polygon", "MultiPolygon"):
            skipped["no_footprint"] += 1
            continue
        index = len(candidates)
        candidates.append((str(gers), lent, facade, roof))
        for k in sorted(int(x) for x in tree.query(geom)):  # bbox candidates; the IoU threshold decides
            bid = ids[k]
            iou = _iou(footprints[bid], geom)
            if iou >= cfg.min_iou:
                pairs.append((iou, bid, index))
    used_osm: set[str] = set()
    used_overture: set[int] = set()
    matches: list[BuildingMatch] = []
    for iou, bid, index in sorted(pairs, key=lambda p: -p[0]):  # stable: ties keep row order, then id order
        if bid in used_osm or index in used_overture:
            continue
        used_osm.add(bid)
        used_overture.add(index)
        gers, (value, levels, dataset, lic), facade, roof = candidates[index]
        matches.append(BuildingMatch(
            building_id=bid, gers_id=gers, iou=round(iou, 4), height_m=value, levels=levels, dataset=dataset, license=lic,
            origin=cfg.origins.get(dataset, cfg.default_origin), facade_color=facade, roof_color=roof,
        ))
    skipped["unmatched"] = len(candidates) - len(used_overture)
    return sorted(matches, key=lambda m: m.building_id), dict(sorted(skipped.items()))


def overrides_document(matches: Sequence[BuildingMatch], record: FetchRecord, cfg: OvertureConfig) -> dict[str, Any]:
    """Heights overrides (``config/heights.overrides.schema.json``) with the origin of every value."""
    entries = []
    for m in matches:
        entry: dict[str, Any] = {"id": m.building_id}
        if m.height_m is not None:
            entry["height_m"] = m.height_m
        if m.levels is not None:
            entry["levels"] = m.levels
        entry |= {
            "source": f"Overture Maps buildings {record.release}: {m.dataset}",
            "license": m.license,
            "retrieved_at": record.retrieved_at,
            "confidence": cfg.confidence[m.origin],
            "origin": m.origin,
            "note": f"IoU {m.iou:.2f} with Overture {m.gers_id}",
        }
        entries.append(entry)
    return {"version": OVERRIDES_VERSION, "overrides": entries}


def match_report(
    *, scene_ref: Mapping[str, Any], cfg: OvertureConfig, places: Mapping[str, Any], matches: Sequence[BuildingMatch],
    building_skips: Mapping[str, int], buildings_record: FetchRecord, osm_buildings: int,
) -> dict[str, Any]:
    by_origin = Counter(m.origin for m in matches)
    return {
        "schema": REPORT_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "scene": dict(scene_ref),
        "config": {"path": cfg.path, "version": cfg.version},
        "places": {"counts": places["counts"], "licenses": places["licenses"]},
        "buildings": {
            "source": {"path": buildings_record.path, "sha256": buildings_record.sha256, "release": buildings_record.release},
            "counts": {
                "overture_rows": buildings_record.rows,
                "osm_buildings": osm_buildings,
                "matched": len(matches),
                "with_height": sum(m.height_m is not None for m in matches),
                "with_levels": sum(m.levels is not None for m in matches),
                "by_origin": dict(sorted(by_origin.items())),
                "skipped": dict(building_skips),
            },
            "licenses": [{"dataset": d, "license": lic, "buildings": n}
                         for (d, lic), n in sorted(Counter((m.dataset, m.license) for m in matches).items())],
            "matches": [m.to_dict() for m in matches],
        },
    }
