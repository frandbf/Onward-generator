"""Load a versioned area-of-interest polygon from GeoJSON (RFC 7946, WGS84)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from onward_generator.domain.models import AreaOfInterest
from onward_generator.geo.projection import DEFAULT_CRS

DEFAULT_MARGIN_M = 30.0
_CRS84 = {"urn:ogc:def:crs:OGC:1.3:CRS84", "urn:ogc:def:crs:OGC::CRS84", "EPSG:4326"}


class AoiError(ValueError):
    """Raised for an unusable area-of-interest file."""


def _single_polygon(doc: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    kind = doc.get("type")
    if kind == "FeatureCollection":
        features = doc.get("features") or []
        if len(features) != 1:
            raise AoiError(f"FeatureCollection must contain exactly one feature, found {len(features)}")
        return _single_polygon(features[0])
    if kind == "Feature":
        geometry = doc.get("geometry") or {}
        return geometry, doc.get("properties") or {}
    return doc, {}


def load_aoi_geojson(
    path: str | Path,
    *,
    margin_m: float | None = None,
    name: str | None = None,
    crs: str = DEFAULT_CRS,
) -> AreaOfInterest:
    source = Path(path)
    try:
        doc = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AoiError(f"{source}: cannot read GeoJSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise AoiError(f"{source}: GeoJSON root must be an object")
    declared = (doc.get("crs") or {}).get("properties", {}).get("name")
    if declared is not None and declared not in _CRS84:
        raise AoiError(f"{source}: AOI must be WGS84 lon/lat (RFC 7946), found crs {declared!r}")

    geometry, properties = _single_polygon(doc)
    if geometry.get("type") != "Polygon":
        raise AoiError(f"{source}: AOI geometry must be a Polygon, found {geometry.get('type')!r}")
    rings = geometry.get("coordinates") or []
    if len(rings) != 1:
        raise AoiError(f"{source}: AOI polygon must have exactly one ring (no holes)")
    try:
        ring = tuple((float(p[0]), float(p[1])) for p in rings[0])
        return AreaOfInterest(
            name=name or str(properties.get("name") or source.stem),
            polygon=ring,
            margin_m=float(margin_m if margin_m is not None else properties.get("margin_m", DEFAULT_MARGIN_M)),
            crs=crs,
        )
    except (TypeError, ValueError, IndexError) as exc:
        raise AoiError(f"{source}: {exc}") from exc
