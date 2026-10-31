"""Overture places against one geo scene, offline (ADR-021).

Places become third-party POIs in the scene's local grid: kept when their
confidence and operating status pass the configuration and their category maps
to one ``shop``/``amenity``/``office``/``craft`` tag. ``detail`` uses them only
for a building that has no OpenStreetMap POI.
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

from onward_generator.config.loader import ConfigLoadError, read_json_document
from onward_generator.geo.projection import LocalFrame
from onward_generator.overture.acquire import DOCUMENTED_LICENSES, FetchRecord, attribution_lines

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "overture.default.json"
SCHEMA_PATH = REPO_ROOT / "config" / "overture.schema.json"
PLACES_SCHEMA = "onward_generator.overture_places"
REPORT_SCHEMA = "onward_generator.overture_match_report"
SCHEMA_VERSION = "0.1.0"


class OvertureConfigError(ValueError):
    """The Overture configuration is invalid."""


@dataclass(frozen=True, slots=True)
class OvertureConfig:
    min_confidence: float
    operating_status: frozenset[str]
    accept_missing_status: bool
    categories: Mapping[str, tuple[str, str]]
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
    places = doc["places"]
    return OvertureConfig(
        min_confidence=float(places["min_confidence"]),
        operating_status=frozenset(places["operating_status"]),
        accept_missing_status=bool(places["accept_missing_status"]),
        categories={name: next(iter(tag.items())) for name, tag in places["categories"].items()},
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


def match_report(*, scene_ref: Mapping[str, Any], cfg: OvertureConfig, places: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": REPORT_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "scene": dict(scene_ref),
        "config": {"path": cfg.path, "version": cfg.version},
        "places": {"counts": places["counts"], "licenses": places["licenses"]},
    }
