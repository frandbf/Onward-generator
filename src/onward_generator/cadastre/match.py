"""Match cadastre building parts to OSM building footprints → heights overrides.

For each OSM footprint the overlapping parts with at least one floor above
ground are collected. The floor count is the overlap-area-weighted median,
which ignores small courtyard or annex parts. The result is a floor count, not
a height: the heights module turns it into metres with the category storey
height (``authoritative_levels``).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from shapely import STRtree, affinity, union_all
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from onward_generator.cadastre.acquire import CRS, CadastreError, DownloadRecord
from onward_generator.cadastre.gml import BuildingPart, PartIssue

OVERRIDES_VERSION = "0.1.0"
SOURCE_LABEL = "Dirección General del Catastro, INSPIRE BU BuildingPart.numberOfFloorsAboveGround"
REPORT_SCHEMA = "onward_generator.cadastre_match_report"
REPORT_VERSION = "0.1.0"
DEFAULT_MIN_COVERAGE = 0.5


@dataclass(frozen=True, slots=True)
class MatchOptions:
    min_coverage: float = DEFAULT_MIN_COVERAGE
    """Share of the OSM footprint that must be covered by parts with floors ≥ 1."""
    min_overlap_m2: float = 1.0
    high_coverage: float = 0.8
    confidence_high: float = 0.85
    confidence_low: float = 0.7
    mixed_floors_share: float = 0.6
    """If the median floor value holds less than this share of the overlap, the match is 'mixed'."""
    mixed_penalty: float = 0.1

    def __post_init__(self) -> None:
        for name in ("min_coverage", "high_coverage", "confidence_high", "confidence_low", "mixed_floors_share", "mixed_penalty"):
            if not 0 <= getattr(self, name) <= 1:
                raise ValueError(f"{name} must be within [0, 1]")
        if self.min_overlap_m2 < 0:
            raise ValueError("min_overlap_m2 must be non-negative")


@dataclass(frozen=True, slots=True)
class BuildingMatch:
    building_id: str
    status: str  # matched | low_coverage | no_parts
    coverage: float
    levels: int | None
    levels_min: int | None
    levels_max: int | None
    median_share: float | None
    parts: tuple[str, ...]
    refcats: tuple[str, ...]
    confidence: float | None
    osm_levels: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "building_id": self.building_id,
            "status": self.status,
            "coverage": self.coverage,
            "levels": self.levels,
            "levels_range": [self.levels_min, self.levels_max] if self.levels is not None else None,
            "median_share": self.median_share,
            "parts": list(self.parts),
            "refcats": list(self.refcats),
            "confidence": self.confidence,
            "osm_levels": self.osm_levels,
        }


def weighted_median(values: Sequence[tuple[int, float]]) -> int:
    """Lower weighted median of (value, weight) pairs."""
    ordered = sorted(values)
    total = sum(w for _, w in ordered)
    acc = 0.0
    for value, weight in ordered:
        acc += weight
        if acc >= total / 2:
            return value
    return ordered[-1][0]


def _osm_levels(feature: Mapping[str, Any]) -> int | None:
    attr = (feature.get("attributes") or {}).get("levels")
    if isinstance(attr, Mapping) and attr.get("origin") == "observed" and isinstance(attr.get("value"), int):
        return int(attr["value"])
    return None


def match_buildings(
    scene: Mapping[str, Any], parts: Sequence[BuildingPart], options: MatchOptions | None = None
) -> list[BuildingMatch]:
    options = options or MatchOptions()
    frame = scene.get("frame") or {}
    if frame.get("crs") != CRS or frame.get("coordinate_system") != "local_grid" or frame.get("unit") != "m":
        raise CadastreError(f"scene must be a local grid in {CRS} metres")
    e0, n0 = frame["origin_projected"]
    local_parts = [affinity.translate(p.geometry, -e0, -n0) for p in parts]
    tree = STRtree(local_parts)

    matches: list[BuildingMatch] = []
    for feature in sorted((f for f in scene.get("features", []) if f.get("kind") == "building"), key=lambda f: f["id"]):
        footprint: BaseGeometry = shape(feature["geometry"])
        osm_levels = _osm_levels(feature)
        overlaps: list[tuple[BuildingPart, BaseGeometry]] = []
        for idx in sorted(int(i) for i in tree.query(footprint, predicate="intersects")):
            part = parts[idx]
            if part.floors_above < 1:
                continue
            inter = footprint.intersection(local_parts[idx])
            if inter.area >= options.min_overlap_m2:
                overlaps.append((part, inter))
        if not overlaps:
            matches.append(BuildingMatch(feature["id"], "no_parts", 0.0, None, None, None, None, (), (), None, osm_levels))
            continue
        coverage = min(1.0, union_all([g for _, g in overlaps]).area / footprint.area)
        weighted = [(p.floors_above, g.area) for p, g in overlaps]
        levels = weighted_median(weighted)
        total = sum(w for _, w in weighted)
        share = sum(w for v, w in weighted if v == levels) / total
        status = "matched" if coverage >= options.min_coverage else "low_coverage"
        confidence = None
        if status == "matched":
            confidence = options.confidence_high if coverage >= options.high_coverage else options.confidence_low
            if share < options.mixed_floors_share:
                confidence -= options.mixed_penalty
            confidence = round(confidence, 3)
        matches.append(
            BuildingMatch(
                building_id=feature["id"],
                status=status,
                coverage=round(coverage, 4),
                levels=levels,
                levels_min=min(v for v, _ in weighted),
                levels_max=max(v for v, _ in weighted),
                median_share=round(share, 4),
                parts=tuple(sorted(p.local_id for p, _ in overlaps)),
                refcats=tuple(sorted({p.refcat for p, _ in overlaps})),
                confidence=confidence,
                osm_levels=osm_levels,
            )
        )
    return matches


def overrides_document(matches: Sequence[BuildingMatch], record: DownloadRecord) -> dict[str, Any]:
    """Heights overrides (``config/heights.overrides.schema.json``) for matched buildings."""
    entries = []
    for m in matches:
        if m.status != "matched" or m.levels is None or m.confidence is None:
            continue
        refcats = ", ".join(m.refcats[:5]) + (" …" if len(m.refcats) > 5 else "")
        entries.append(
            {
                "id": m.building_id,
                "levels": m.levels,
                "source": SOURCE_LABEL,
                "license": record.license,
                "retrieved_at": record.retrieved_at,
                "confidence": m.confidence,
                "note": (
                    f"coverage {m.coverage:.2f}; {len(m.parts)} part(s); floors {m.levels_min}–{m.levels_max}; "
                    f"median share {m.median_share:.2f}; refcat {refcats}"
                ),
            }
        )
    return {"version": OVERRIDES_VERSION, "overrides": entries}


def match_report(
    matches: Sequence[BuildingMatch],
    *,
    record: DownloadRecord,
    gml: Mapping[str, Any],
    scene_ref: Mapping[str, Any],
    part_count: int,
    part_issues: Sequence[PartIssue],
    options: MatchOptions,
) -> dict[str, Any]:
    status = Counter(m.status for m in matches)
    matched = [m for m in matches if m.status == "matched"]
    compared = [m for m in matched if m.osm_levels is not None]
    return {
        "schema": REPORT_SCHEMA,
        "schema_version": REPORT_VERSION,
        "scene": dict(scene_ref),
        "cadastre": {**record.to_dict(), **gml},
        "attribution": {"text": record.attribution, "license": record.license, "license_url": record.license_url, "notes": record.license_notes},
        "options": {k: getattr(options, k) for k in options.__slots__},  # type: ignore[attr-defined]
        "counts": {
            "cadastre_parts": part_count,
            "osm_buildings": len(matches),
            **{f"status_{k}": status.get(k, 0) for k in ("matched", "low_coverage", "no_parts")},
            "mixed_floors": sum(1 for m in matched if (m.median_share or 0) < options.mixed_floors_share),
            "osm_levels_compared": len(compared),
            "osm_levels_agree": sum(1 for m in compared if m.osm_levels == m.levels),
        },
        "levels_distribution": dict(sorted(Counter(m.levels for m in matched).items())),
        "part_issues": [i.to_dict() for i in part_issues],
        "buildings": [m.to_dict() for m in matches],
    }
