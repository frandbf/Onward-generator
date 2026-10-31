"""Hierarchical, deterministic building-height estimation.

Order per building: override height → explicit ``height`` → authoritative
floor count (override ``levels``) → third-party height, then floor count
(secondary overrides, Overture) → explicit ``building:levels`` → building
type → neighbourhood median → category fallback. Landmarks without
an explicit or authoritative height get a flagged placeholder instead of the
generic type/neighbourhood estimates.

Input is the ``onward_generator.geo_scene`` JSON contract; this module does not
depend on the geo pipeline internals, geometry generation or export.
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from shapely import STRtree
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from onward_generator.contracts import is_schema
from onward_generator.heights.classify import Classification, classify
from onward_generator.heights.config import HeightConfig

SCENE_SCHEMA = "onward_generator.geo_scene"
SUPPORTED_SCENE_VERSIONS = frozenset({"0.1.0", "0.2.0", "0.3.0"})
_DECIMALS = 2


class HeightSource(StrEnum):
    OVERRIDE = "override"
    EXPLICIT_HEIGHT = "explicit_height"
    AUTHORITATIVE_LEVELS = "authoritative_levels"
    THIRD_PARTY_HEIGHT = "third_party_height"
    THIRD_PARTY_LEVELS = "third_party_levels"
    EXPLICIT_LEVELS = "explicit_levels"
    BUILDING_TYPE = "building_type"
    NEIGHBOURHOOD = "neighbourhood"
    FALLBACK = "fallback"
    LANDMARK_PLACEHOLDER = "landmark_placeholder"


class HeightClass(StrEnum):
    MEASURED = "measured"
    INFERRED = "inferred"
    FALLBACK = "fallback"


HEIGHT_CLASS: Mapping[HeightSource, HeightClass] = {
    HeightSource.OVERRIDE: HeightClass.MEASURED,
    HeightSource.EXPLICIT_HEIGHT: HeightClass.MEASURED,
    HeightSource.AUTHORITATIVE_LEVELS: HeightClass.INFERRED,
    HeightSource.THIRD_PARTY_HEIGHT: HeightClass.INFERRED,
    HeightSource.THIRD_PARTY_LEVELS: HeightClass.INFERRED,
    HeightSource.EXPLICIT_LEVELS: HeightClass.INFERRED,
    HeightSource.BUILDING_TYPE: HeightClass.INFERRED,
    HeightSource.NEIGHBOURHOOD: HeightClass.INFERRED,
    HeightSource.FALLBACK: HeightClass.FALLBACK,
    HeightSource.LANDMARK_PLACEHOLDER: HeightClass.FALLBACK,
}


class SceneError(ValueError):
    """The input is not a usable geo scene."""


@dataclass(frozen=True, slots=True)
class HeightIssue:
    code: str
    severity: str
    building_id: str | None
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "severity": self.severity, "building_id": self.building_id, "message": self.message}


@dataclass(frozen=True, slots=True)
class BuildingInput:
    id: str
    tags: Mapping[str, str]
    footprint: BaseGeometry | None
    explicit_height_m: float | None
    explicit_levels: int | None

    @property
    def name(self) -> str | None:
        return self.tags.get("name")


@dataclass(frozen=True, slots=True)
class BuildingHeight:
    id: str
    name: str | None
    category: str
    category_tag: str | None
    landmark: bool
    height_m: float
    height_source: HeightSource
    height_confidence: float
    estimated_levels: int
    levels_source: str
    rule: str
    needs_manual_review: bool
    review_reasons: tuple[str, ...]
    samples: int | None = None

    @property
    def height_class(self) -> HeightClass:
        return HEIGHT_CLASS[self.height_source]

    def to_dict(self) -> dict[str, Any]:
        return {
            "building_id": self.id,
            "name": self.name,
            "category": self.category,
            "category_tag": self.category_tag,
            "landmark": self.landmark,
            "height_m": self.height_m,
            "height_source": self.height_source.value,
            "height_class": self.height_class.value,
            "height_confidence": self.height_confidence,
            "estimated_levels": self.estimated_levels,
            "levels_source": self.levels_source,
            "rule": self.rule,
            "neighbourhood_samples": self.samples,
            "needs_manual_review": self.needs_manual_review,
            "review_reasons": list(self.review_reasons),
        }


@dataclass(slots=True)
class _Draft:
    building: BuildingInput
    cls: Classification
    review: list[str] = field(default_factory=list)
    result: BuildingHeight | None = None


def _observed(attributes: Mapping[str, Any], name: str) -> Any:
    attr = attributes.get(name)
    if isinstance(attr, Mapping) and attr.get("origin") == "observed":
        return attr.get("value")
    return None


def buildings_from_scene(scene: Mapping[str, Any]) -> tuple[list[BuildingInput], list[HeightIssue]]:
    """Extract building footprints and explicit (observed) height data from a geo scene."""
    if not is_schema(scene.get("schema"), SCENE_SCHEMA):
        raise SceneError(f"expected schema {SCENE_SCHEMA!r}, got {scene.get('schema')!r}")
    if scene.get("schema_version") not in SUPPORTED_SCENE_VERSIONS:
        raise SceneError(f"unsupported geo scene version {scene.get('schema_version')!r}")
    frame = scene.get("frame") or {}
    if frame.get("unit") != "m" or frame.get("coordinate_system") != "local_grid":
        raise SceneError("scene must be in the local metric grid (unit m); refusing non-metric coordinates")

    buildings: list[BuildingInput] = []
    issues: list[HeightIssue] = []
    seen: set[str] = set()
    for feature in scene.get("features", []):
        if feature.get("kind") != "building":
            continue
        fid = str(feature["id"])
        if fid in seen:
            issues.append(HeightIssue("duplicate_building_id", "error", fid, "building id appears twice; later copy ignored"))
            continue
        seen.add(fid)
        footprint: BaseGeometry | None = None
        geometry = feature.get("geometry")
        if geometry and geometry.get("unit", "m") == "m":
            try:
                footprint = shape({"type": geometry["type"], "coordinates": geometry["coordinates"]})
            except (KeyError, TypeError, ValueError, AttributeError):
                footprint = None
        if footprint is None or footprint.is_empty or footprint.geom_type not in ("Polygon", "MultiPolygon"):
            issues.append(HeightIssue("missing_footprint", "warning", fid, "no usable polygon; excluded from neighbourhood statistics"))
            footprint = None
        attributes = feature.get("attributes") or {}
        height = _observed(attributes, "height_m")
        levels = _observed(attributes, "levels")
        buildings.append(
            BuildingInput(
                id=fid,
                tags=dict(feature.get("tags") or {}),
                footprint=footprint,
                explicit_height_m=float(height) if isinstance(height, (int, float)) and not isinstance(height, bool) else None,
                explicit_levels=int(levels) if isinstance(levels, int) and not isinstance(levels, bool) else None,
            )
        )
    buildings.sort(key=lambda b: b.id)
    return buildings, issues


def _round(value: float) -> float:
    return round(value, _DECIMALS) + 0.0


def _levels_for(height: float, storey: float) -> int:
    return max(1, round(height / storey))


class _Estimator:
    def __init__(self, config: HeightConfig) -> None:
        self.cfg = config
        self.issues: list[HeightIssue] = []

    def issue(self, code: str, severity: str, building_id: str | None, message: str) -> None:
        self.issues.append(HeightIssue(code, severity, building_id, message))

    def in_bounds(self, value: float) -> bool:
        return self.cfg.bounds_min_m <= value <= self.cfg.bounds_max_m

    def clamp(self, draft: _Draft, value: float, low: float, high: float, label: str) -> float:
        clamped = min(max(value, low), high)
        if clamped != value:
            self.issue("estimate_clamped", "warning", draft.building.id, f"{label} {value:.2f} m clamped to [{low}, {high}]")
        return clamped

    def finish(
        self,
        draft: _Draft,
        height: float,
        source: HeightSource,
        confidence: float,
        rule: str,
        *,
        levels: int | None = None,
        levels_source: str = "derived_from_height",
        samples: int | None = None,
    ) -> None:
        category = draft.cls.category
        if levels is None:
            levels = _levels_for(height, self.cfg.storey_height(category))
        draft.result = BuildingHeight(
            id=draft.building.id,
            name=draft.building.name,
            category=category,
            category_tag=draft.cls.matched_tag,
            landmark=draft.cls.landmark,
            height_m=_round(height),
            height_source=source,
            height_confidence=confidence,
            estimated_levels=levels,
            levels_source=levels_source,
            rule=rule,
            needs_manual_review=bool(draft.review),
            review_reasons=tuple(draft.review),
            samples=samples,
        )

    def from_levels(self, draft: _Draft, levels: int, source: HeightSource, confidence: float, origin: str, levels_source: str) -> None:
        storey = self.cfg.storey_height(draft.cls.category)
        raw = levels * storey
        if not self.in_bounds(raw):
            draft.review.append("levels_height_out_of_bounds")
        height = self.clamp(draft, raw, self.cfg.bounds_min_m, self.cfg.bounds_max_m, "levels height")
        rule = f"{origin} {levels} × {storey} m ({draft.cls.category})"
        if draft.cls.landmark:
            draft.review.append("landmark_height_from_levels")
            floor = self.cfg.fallback_m[draft.cls.category].default
            if self.cfg.landmark_levels_floor and height < floor:
                height = floor
                rule += f"; raised to landmark {draft.cls.category} default {floor} m"
        self.finish(draft, height, source, confidence, rule, levels=levels, levels_source=levels_source)

    # Step 1: measured, explicitly tagged or authoritative data.
    def explicit(self, draft: _Draft) -> None:
        b, cfg = draft.building, self.cfg
        override = cfg.overrides.get(b.id)
        if override is not None and override.height_m is not None:
            if b.explicit_height_m is not None and abs(b.explicit_height_m - override.height_m) > 0.1 * override.height_m:
                self.issue(
                    "override_differs_from_osm", "info", b.id,
                    f"override {override.height_m} m replaces OSM height={b.explicit_height_m} m",
                )
            self.finish(
                draft, override.height_m, HeightSource.OVERRIDE, override.confidence,
                f"authoritative override: {override.source} ({override.license})",
                levels=override.levels or b.explicit_levels,
                levels_source="override" if override.levels else ("explicit" if b.explicit_levels else "derived_from_height"),
            )
            return
        if b.explicit_height_m is not None:
            if self.in_bounds(b.explicit_height_m):
                self.finish(
                    draft, b.explicit_height_m, HeightSource.EXPLICIT_HEIGHT, cfg.confidence["explicit_height"],
                    "OSM height tag",
                    levels=b.explicit_levels, levels_source="explicit" if b.explicit_levels else "derived_from_height",
                )
                return
            self.issue(
                "explicit_height_out_of_bounds", "warning", b.id,
                f"height={b.explicit_height_m} m outside [{cfg.bounds_min_m}, {cfg.bounds_max_m}]; ignored",
            )
            draft.review.append("explicit_height_out_of_bounds")
        if override is not None and override.levels is not None:
            if b.explicit_levels is not None and b.explicit_levels != override.levels:
                self.issue(
                    "override_levels_differ_from_osm", "info", b.id,
                    f"authoritative {override.levels} levels replace OSM building:levels={b.explicit_levels}",
                )
            self.from_levels(
                draft, override.levels, HeightSource.AUTHORITATIVE_LEVELS, override.confidence,
                f"{override.source} ({override.license}): levels", "override",
            )
            return
        if self.third_party(draft):
            return
        if b.explicit_levels is not None:
            self.from_levels(
                draft, b.explicit_levels, HeightSource.EXPLICIT_LEVELS, cfg.confidence["explicit_levels"],
                "building:levels", "explicit",
            )

    def third_party(self, draft: _Draft) -> bool:
        """A secondary override (Overture): its height, else its floor count. True when it set the result."""
        b = draft.building
        entry = self.cfg.secondary_overrides.get(b.id)
        if entry is None:
            return False
        label = f"third-party {entry.origin or 'unstated'}: {entry.source} ({entry.license})"
        if entry.height_m is not None:
            self.finish(
                draft, entry.height_m, HeightSource.THIRD_PARTY_HEIGHT, entry.confidence, label,
                levels=entry.levels or b.explicit_levels,
                levels_source="override" if entry.levels else ("explicit" if b.explicit_levels else "derived_from_height"),
            )
            return True
        if entry.levels is not None:
            self.from_levels(draft, entry.levels, HeightSource.THIRD_PARTY_LEVELS, entry.confidence, f"{label}: levels", "override")
            return True
        return False

    # Landmarks: no generic estimate.
    def landmark(self, draft: _Draft) -> None:
        fallback = self.cfg.fallback_m[draft.cls.category]
        draft.review.append("landmark_without_authoritative_height")
        self.finish(
            draft, fallback.default, HeightSource.LANDMARK_PLACEHOLDER, self.cfg.confidence["landmark_placeholder"],
            f"placeholder for landmark ({draft.cls.landmark_reason}); {draft.cls.category} default {fallback.default} m",
        )

    # Step 2: building type.
    def building_type(self, draft: _Draft) -> None:
        category = draft.cls.category
        levels = self.cfg.typical_levels.get(category)
        if draft.cls.matched_tag is None or levels is None:
            return
        storey = self.cfg.storey_height(category)
        r = self.cfg.fallback_m[category]
        height = self.clamp(draft, levels * storey, max(r.min, self.cfg.bounds_min_m), min(r.max, self.cfg.bounds_max_m), "type height")
        self.finish(
            draft, height, HeightSource.BUILDING_TYPE, self.cfg.confidence["building_type"],
            f"{draft.cls.matched_tag} → {category}: {levels} typical levels × {storey} m",
            levels=levels, levels_source="building_type",
        )

    # Step 4: fallback.
    def fallback(self, draft: _Draft) -> None:
        r = self.cfg.fallback_m[draft.cls.category]
        self.finish(
            draft, r.default, HeightSource.FALLBACK, self.cfg.confidence["fallback"],
            f"{draft.cls.category} fallback default {r.default} m (plausible range {r.min}–{r.max} m)",
        )


def _blocks(footprints: Sequence[BaseGeometry | None], gap_m: float) -> list[int]:
    """Union-find of footprints closer than ``gap_m``: a proxy for street blocks."""
    parent = list(range(len(footprints)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    indexed = [(i, g) for i, g in enumerate(footprints) if g is not None]
    if indexed:
        tree = STRtree([g for _, g in indexed])
        for i, geom in indexed:
            for local_j in tree.query(geom, predicate="dwithin", distance=gap_m):
                j = indexed[int(local_j)][0]
                if j != i:
                    ri, rj = find(i), find(j)
                    if ri != rj:
                        parent[max(ri, rj)] = min(ri, rj)
    return [find(i) for i in range(len(footprints))]


def _neighbourhood(est: _Estimator, drafts: list[_Draft]) -> None:
    cfg = est.cfg
    footprints = [d.building.footprint for d in drafts]
    blocks = _blocks(footprints, cfg.block_gap_m)
    known = [
        i for i, d in enumerate(drafts)
        if d.result is not None and d.result.height_source.value in cfg.reliable_sources and footprints[i] is not None
    ]
    tree = STRtree([footprints[i] for i in known]) if known else None
    tiers = (
        ("neighbourhood_block_same_category", "same block and category", True, True),
        ("neighbourhood_block", "same block", True, False),
        ("neighbourhood_radius_same_category", f"{cfg.radius_m:g} m radius and same category", False, True),
        ("neighbourhood_radius", f"{cfg.radius_m:g} m radius", False, False),
    )
    for i, draft in enumerate(drafts):
        if draft.result is not None or footprints[i] is None or tree is None:
            continue
        near = sorted(known[int(k)] for k in tree.query(footprints[i], predicate="dwithin", distance=cfg.radius_m))
        near = [j for j in near if j != i]
        for key, label, block_only, same_category in tiers:
            sample = [
                drafts[j].result.height_m  # type: ignore[union-attr]
                for j in near
                if (not block_only or blocks[j] == blocks[i]) and (not same_category or drafts[j].cls.category == draft.cls.category)
            ]
            if len(sample) >= cfg.min_samples:
                median = statistics.median(sample)
                height = est.clamp(draft, median, cfg.bounds_min_m, cfg.bounds_max_m, "neighbourhood median")
                est.finish(
                    draft, height, HeightSource.NEIGHBOURHOOD, cfg.confidence[key],
                    f"median of {len(sample)} known heights ({label})", samples=len(sample),
                )
                break


@dataclass(frozen=True, slots=True)
class HeightResult:
    buildings: tuple[BuildingHeight, ...]
    issues: tuple[HeightIssue, ...]

    def get(self, building_id: str) -> BuildingHeight | None:
        return next((b for b in self.buildings if b.id == building_id), None)


def estimate_heights(buildings: Sequence[BuildingInput], config: HeightConfig) -> HeightResult:
    """Estimate one height per building. Output order and values do not depend on input order."""
    est = _Estimator(config)
    drafts = [_Draft(b, classify(b.tags, config)) for b in sorted(buildings, key=lambda b: b.id)]
    for draft in drafts:
        est.explicit(draft)
    for draft in drafts:
        if draft.result is None and draft.cls.landmark:
            est.landmark(draft)
    for draft in drafts:
        if draft.result is None:
            est.building_type(draft)
    _neighbourhood(est, drafts)
    for draft in drafts:
        if draft.result is None:
            est.fallback(draft)

    ids = {d.building.id for d in drafts}
    for override_id in sorted((set(config.overrides) | set(config.secondary_overrides)) - ids):
        est.issue("override_not_in_scene", "warning", override_id, "override id matches no building in the scene")
    results = tuple(d.result for d in drafts if d.result is not None)
    assert len(results) == len(drafts)
    for r in results:
        if not (r.height_m > 0 and config.bounds_min_m <= r.height_m <= config.bounds_max_m):
            raise AssertionError(f"{r.id}: height {r.height_m} escaped bounds")
    return HeightResult(results, tuple(est.issues))
