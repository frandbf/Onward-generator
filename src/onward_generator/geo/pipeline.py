"""Geographic pipeline: registered OSM source → normalized local scene + report.

Stages, each in its own module:
``sources`` (provenance) → ``osm_import`` (parse, WGS84 geometry) →
``projection`` (metric CRS, local frame) → ``topology`` (validation) →
``clipping`` (AOI + margin) → ``attributes`` (observed vs inferred).
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

from shapely import Polygon
from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry

from onward_generator.domain.models import AreaOfInterest, Attribute, AttributeOrigin, OsmRef, SourceDataset
from onward_generator.geo.attributes import derive_attributes
from onward_generator.geo.clipping import ClipStatus, clip_feature, inclusion_zone
from onward_generator.geo.issues import GeoIssue
from onward_generator.geo.osm_import import FeatureKind, build_source_features, parse_osm_xml
from onward_generator.geo.projection import COORDINATE_SYSTEM, LocalFrame
from onward_generator.geo.sources import verify_unchanged
from onward_generator.geo.topology import check_geometry, find_duplicate_geometries, has_errors, repair
from onward_generator.io import atomic_write_json, prepare_outputs

SCENE_SCHEMA = "onward_generator.geo_scene"
REPORT_SCHEMA = "onward_generator.geo_report"
SCHEMA_VERSION = "0.2.0"
"""0.2.0: building heights are no longer inferred here (see onward_generator.heights)."""
SCENE_FILE = "geo_scene.json"
REPORT_FILE = "geo_report.json"
_COORD_DECIMALS = 3


@dataclass(frozen=True, slots=True)
class GeoOptions:
    repair_invalid: bool = False
    min_area_m2: float = 1.0
    min_length_m: float = 0.5

    def __post_init__(self) -> None:
        for name in ("min_area_m2", "min_length_m"):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be a finite, non-negative number")


@dataclass(frozen=True, slots=True)
class NormalizedFeature:
    ref: OsmRef
    kind: FeatureKind
    tags: Mapping[str, str]
    geometry_local: BaseGeometry
    clip_status: ClipStatus
    attributes: Mapping[str, Attribute]
    geometry_repaired: bool

    @property
    def id(self) -> str:
        return self.ref.key

    def to_dict(self, provider: str, license_id: str) -> dict[str, Any]:
        geometry = mapping(self.geometry_local)
        return {
            "id": self.id,
            "source": {"provider": provider, "license": license_id, **self.ref.to_dict()},
            "kind": self.kind.value,
            "tags": dict(sorted(self.tags.items())),
            "geometry": {
                "type": geometry["type"],
                "coordinates": _round(geometry["coordinates"]),
                "unit": "m",
                "coordinate_system": COORDINATE_SYSTEM,
            },
            "geometry_repaired": self.geometry_repaired,
            "clip_status": self.clip_status.value,
            "attributes": {k: v.to_dict() for k, v in sorted(self.attributes.items())},
        }


@dataclass(frozen=True, slots=True)
class GeoReport:
    counts: Mapping[str, int]
    issues: tuple[GeoIssue, ...]

    def issues_by_code(self) -> dict[str, int]:
        return dict(sorted(Counter(i.code for i in self.issues).items()))

    def codes_for(self, ref: OsmRef) -> set[str]:
        return {i.code for i in self.issues if i.ref == ref}


@dataclass(frozen=True, slots=True)
class GeoResult:
    source: SourceDataset
    aoi: AreaOfInterest
    frame: LocalFrame
    options: GeoOptions
    features: tuple[NormalizedFeature, ...]
    report: GeoReport

    def feature(self, ref: OsmRef) -> NormalizedFeature | None:
        return next((f for f in self.features if f.ref == ref), None)

    def _header(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "source": self.source.to_dict(),
            "attribution": {
                "text": self.source.attribution,
                "license": self.source.license,
                "license_url": self.source.license_url,
            },
            "area_of_interest": self.aoi.to_dict(),
            "frame": self.frame.to_dict(),
        }

    def scene_dict(self) -> dict[str, Any]:
        return {
            "schema": SCENE_SCHEMA,
            **self._header(),
            "features": [f.to_dict(self.source.provider, self.source.license) for f in self.features],
        }

    def report_dict(self) -> dict[str, Any]:
        return {
            "schema": REPORT_SCHEMA,
            **self._header(),
            "options": asdict(self.options),
            "counts": dict(self.report.counts),
            "issues_by_code": self.report.issues_by_code(),
            "issues": [i.to_dict() for i in self.report.issues],
        }


def _round(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, _COORD_DECIMALS) + 0.0
    if isinstance(value, (list, tuple)):
        return [_round(v) for v in value]
    return value


def default_origin(aoi: AreaOfInterest) -> tuple[float, float]:
    """AOI centroid computed in the projected CRS, returned as lon/lat."""
    lon0, lat0 = aoi.polygon[0]
    frame = LocalFrame.create(lon0, lat0, aoi.crs)
    centroid = frame.to_local(Polygon(aoi.polygon)).centroid
    point = frame.to_wgs84(centroid)
    return point.x, point.y


def run_geo_pipeline(
    source: SourceDataset,
    aoi: AreaOfInterest,
    *,
    origin: tuple[float, float] | None = None,
    options: GeoOptions | None = None,
) -> GeoResult:
    options = options or GeoOptions()
    verify_unchanged(source)
    lon, lat = origin or default_origin(aoi)
    frame = LocalFrame.create(lon, lat, aoi.crs)
    aoi_local = frame.to_local(Polygon(aoi.polygon))
    zone = inclusion_zone(aoi_local, aoi.margin_m)

    doc = parse_osm_xml(source.path)
    if source.data_timestamp is None and doc.data_timestamp is not None:
        source = replace(source, data_timestamp=doc.data_timestamp)
    source_features, build_issues, skipped = build_source_features(doc)
    issues: list[GeoIssue] = [*doc.issues, *build_issues]
    counts: Counter[str] = Counter(
        osm_nodes=len(doc.nodes),
        osm_ways=len(doc.ways),
        osm_relations=len(doc.relations),
        source_features=len(source_features),
        **skipped,
    )

    kept: list[NormalizedFeature] = []
    for feature in source_features:
        ref = feature.ref
        if not frame.within_area_of_use(feature.geometry_wgs84):
            issues.append(GeoIssue("outside_crs_area_of_use", "warning", f"{ref.key} lies outside the area of use of {frame.crs}", ref))
        local = frame.to_local(feature.geometry_wgs84)
        geometry_issues = check_geometry(ref, local, min_area_m2=options.min_area_m2, min_length_m=options.min_length_m)
        issues.extend(geometry_issues)
        repaired = False
        if has_errors(geometry_issues):
            if not options.repair_invalid:
                counts["rejected_invalid"] += 1
                continue
            local = repair(local)
            recheck = check_geometry(ref, local, min_area_m2=options.min_area_m2, min_length_m=options.min_length_m)
            if has_errors(recheck):
                issues.extend(recheck)
                counts["rejected_invalid"] += 1
                continue
            repaired = True
            counts["repaired"] += 1
            issues.append(GeoIssue("geometry_repaired", "warning", f"{ref.key} repaired with make_valid; source geometry unchanged", ref))

        decision = clip_feature(feature.kind, local, aoi_local, zone)
        if decision.status is ClipStatus.OUTSIDE or decision.geometry is None:
            counts["dropped_outside"] += 1
            continue
        attributes, attribute_issues = derive_attributes(ref, feature.kind, feature.tags)
        issues.extend(attribute_issues)
        kept.append(
            NormalizedFeature(
                ref=ref,
                kind=feature.kind,
                tags=feature.tags,
                geometry_local=decision.geometry,
                clip_status=decision.status,
                attributes=MappingProxyType(attributes),
                geometry_repaired=repaired,
            )
        )

    issues.extend(find_duplicate_geometries([(f.ref, f.kind.value, f.geometry_local) for f in kept]))
    kept.sort(key=lambda f: (f.kind.value, f.ref.osm_type, f.ref.osm_id))
    verify_unchanged(source)

    counts["kept"] = len(kept)
    for f in kept:
        counts[f"kept_{f.kind.value}"] += 1
        counts[f"status_{f.clip_status.value}"] += 1
        for attribute in f.attributes.values():
            counts[f"attributes_{attribute.origin.value}"] += 1
    for origin_kind in AttributeOrigin:
        counts.setdefault(f"attributes_{origin_kind.value}", 0)
    counts["errors"] = sum(1 for i in issues if i.severity == "error")
    counts["warnings"] = sum(1 for i in issues if i.severity == "warning")

    return GeoResult(
        source=source,
        aoi=aoi,
        frame=frame,
        options=options,
        features=tuple(kept),
        report=GeoReport(MappingProxyType(dict(sorted(counts.items()))), tuple(issues)),
    )


def write_outputs(result: GeoResult, out_dir: str | Path, *, overwrite: bool = False) -> tuple[Path, Path]:
    """Write the normalized scene and the report. Never touches the source."""
    scene, report = prepare_outputs(
        out_dir, (SCENE_FILE, REPORT_FILE), overwrite=overwrite, protected=(Path(result.source.path),)
    )
    atomic_write_json(scene, result.scene_dict())
    atomic_write_json(report, result.report_dict())
    return scene, report
