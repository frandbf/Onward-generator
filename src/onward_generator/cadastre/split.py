"""Split OSM building footprints by Catastro building parts (ADR-016).

A footprint that covers several constructions (parcels or storey counts)
becomes one sub-building per construction: the Catastro part geometry clipped
to the OSM footprint. The OSM outline stays the envelope, so no building is
added outside the extract. Each sub-building keeps the OSM id as its parent
and gets its own authoritative storey count.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from shapely import Polygon, STRtree, affinity, set_precision, union_all
from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry

from onward_generator.cadastre.acquire import CRS, CadastreError, DownloadRecord
from onward_generator.cadastre.gml import BuildingPart
from onward_generator.cadastre.match import SOURCE_LABEL

SPLIT_METHOD = "catastro_split"
GRID_M = 0.001
"""Coordinates are snapped to 1 mm, the precision ``geo`` writes."""
HEIGHT_ATTRIBUTES = ("height_m", "levels")
STATUSES = ("split", "single_group", "low_coverage", "no_parts", "landmark")


@dataclass(frozen=True, slots=True)
class SplitOptions:
    min_coverage: float = 0.8
    """Share of the footprint the parts must cover before it is split. Parts with 0 storeys count."""
    min_piece_area_m2: float = 6.0
    min_piece_width_m: float = 2.0
    """A clipped piece narrower than this (an inward buffer of half leaves nothing) is a sliver."""
    confident_share: float = 0.8
    """A sub-building whose own parts cover less than this share of it gets the lower confidence."""
    confidence_high: float = 0.85
    confidence_low: float = 0.7

    def __post_init__(self) -> None:
        for name in ("min_coverage", "confident_share", "confidence_high", "confidence_low"):
            if not 0 <= getattr(self, name) <= 1:
                raise ValueError(f"{name} must be within [0, 1]")
        for name in ("min_piece_area_m2", "min_piece_width_m"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative")

    def to_dict(self) -> dict[str, float]:
        return {name: getattr(self, name) for name in self.__slots__}


@dataclass(frozen=True, slots=True)
class SubBuilding:
    id: str
    parent_id: str
    refcat: str
    parts: tuple[str, ...]
    levels: int
    geometry: BaseGeometry
    leftover_m2: float

    @property
    def own_share(self) -> float:
        return 1.0 - self.leftover_m2 / self.geometry.area

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "refcat": self.refcat,
            "parts": list(self.parts),
            "levels": self.levels,
            "area_m2": round(self.geometry.area, 3),
            "leftover_m2": round(self.leftover_m2, 3),
        }


@dataclass(frozen=True, slots=True)
class ParentSplit:
    parent_id: str
    status: str
    coverage: float
    subs: tuple[SubBuilding, ...] = ()
    courtyard_m2: float = 0.0
    dropped_m2: float = 0.0
    """Stray pieces below the minimum area that touch their group only at a corner, left as open ground."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "building_id": self.parent_id,
            "status": self.status,
            "coverage": round(self.coverage, 4),
            "courtyard_m2": round(self.courtyard_m2, 3),
            "dropped_m2": round(self.dropped_m2, 3),
            "sub_buildings": [s.to_dict() for s in self.subs],
        }


def _polygons(geometry: BaseGeometry) -> list[Polygon]:
    if isinstance(geometry, Polygon):
        return [] if geometry.is_empty else [geometry]
    return [p for g in getattr(geometry, "geoms", ()) for p in _polygons(g)]


def is_sliver(piece: Polygon, options: SplitOptions) -> bool:
    return piece.area < options.min_piece_area_m2 or piece.buffer(-options.min_piece_width_m / 2).is_empty


def _nearest(piece: Polygon, groups: Mapping[str, BaseGeometry]) -> str:
    """The group closest to ``piece`` (within 1 mm counts as touching); then the longest shared boundary; then the id."""
    return min(
        groups,
        key=lambda gid: (
            max(piece.distance(groups[gid]), GRID_M),
            -piece.boundary.intersection(groups[gid].buffer(GRID_M)).length,
            gid,
        ),
    )


def split_footprint(
    parent_id: str,
    footprint: BaseGeometry,
    overlaps: Sequence[tuple[BuildingPart, BaseGeometry]],
    options: SplitOptions,
) -> ParentSplit:
    """Sub-buildings of one footprint. ``overlaps`` are parts with their geometry in the scene frame."""
    floored = [(p, g) for p, g in overlaps if p.floors_above >= 1]
    if not floored:
        return ParentSplit(parent_id, "no_parts", 0.0)
    built = union_all([g for _, g in floored])
    coverage = footprint.intersection(union_all([g for _, g in overlaps])).area / footprint.area
    if coverage < options.min_coverage:
        return ParentSplit(parent_id, "low_coverage", coverage)

    members: dict[tuple[str, int], list[tuple[BuildingPart, BaseGeometry]]] = defaultdict(list)
    for part, geometry in floored:
        members[(part.refcat, part.floors_above)].append((part, geometry))
    groups = sorted(members.values(), key=lambda group: min(p.local_id for p, _ in group))
    kept: dict[str, BaseGeometry] = {}
    info: dict[str, list[BuildingPart]] = {}
    taken: BaseGeometry = Polygon()
    for group in groups:
        area = footprint.intersection(union_all([g for _, g in group])).difference(taken)
        taken = taken.union(area)
        pieces = [piece for piece in _polygons(area) if not is_sliver(piece, options)]
        if pieces:
            gid = f"{parent_id}:{min(p.local_id for p, _ in group)}"
            kept[gid] = union_all(pieces)
            info[gid] = [p for p, _ in group]
    if len(kept) < 2:
        return ParentSplit(parent_id, "single_group", coverage)

    open_parts = [g for p, g in overlaps if p.floors_above == 0]
    open_area = footprint.intersection(union_all(open_parts)).difference(built)
    courtyard = union_all([piece for piece in _polygons(open_area) if not is_sliver(piece, options)])
    leftover = footprint.difference(courtyard).difference(union_all(list(kept.values())))
    extra: dict[str, list[Polygon]] = defaultdict(list)
    for piece in _polygons(leftover):
        extra[_nearest(piece, kept)].append(piece)

    subs = []
    dropped = 0.0
    for gid in sorted(kept):
        parts = info[gid]
        # One building per connected polygon: polygons that touch at a corner would make non-manifold walls.
        pieces = sorted(_polygons(set_precision(union_all([kept[gid], *extra[gid]]), GRID_M)), key=lambda p: (-p.area, p.bounds))
        dropped += sum(p.area for p in pieces if p.area < options.min_piece_area_m2)
        pieces = [p for p in pieces if p.area >= options.min_piece_area_m2]
        for index, piece in enumerate(pieces, start=1):
            subs.append(
                SubBuilding(
                    id=gid if index == 1 else f"{gid}.{index}",
                    parent_id=parent_id,
                    refcat=parts[0].refcat,
                    parts=tuple(sorted(p.local_id for p in parts)),
                    levels=parts[0].floors_above,
                    geometry=piece,
                    leftover_m2=sum((e.intersection(piece).area for e in extra[gid]), 0.0),
                )
            )
    return ParentSplit(parent_id, "split", coverage, tuple(subs), courtyard.area, dropped)


def _sub_feature(parent: Mapping[str, Any], sub: SubBuilding, record: DownloadRecord) -> dict[str, Any]:
    attributes = dict(parent.get("attributes") or {})
    observed = {name: attributes.pop(name) for name in HEIGHT_ATTRIBUTES if name in attributes}
    geometry = mapping(sub.geometry)
    return {
        **parent,
        "id": sub.id,
        "geometry": {**parent["geometry"], "type": geometry["type"], "coordinates": geometry["coordinates"]},
        "attributes": attributes,
        "parent": {"id": sub.parent_id},
        "subdivision": {
            "method": SPLIT_METHOD,
            "source": SOURCE_LABEL,
            "provider": record.provider,
            "license": record.license,
            "license_url": record.license_url,
            "attribution": record.attribution,
            "retrieved_at": record.retrieved_at,
            "refcat": sub.refcat,
            "parts": list(sub.parts),
            "levels": sub.levels,
            "leftover_m2": round(sub.leftover_m2, 3),
            "parent_observed": observed,
        },
    }


def split_scene(
    scene: Mapping[str, Any],
    parts: Sequence[BuildingPart],
    record: DownloadRecord,
    options: SplitOptions,
    *,
    is_landmark: Callable[[Mapping[str, str]], bool],
) -> tuple[list[dict[str, Any]], list[ParentSplit]]:
    """The scene features with split footprints replaced, and one result per OSM building."""
    frame = scene.get("frame") or {}
    if frame.get("crs") != CRS or frame.get("coordinate_system") != "local_grid" or frame.get("unit") != "m":
        raise CadastreError(f"scene must be a local grid in {CRS} metres")
    e0, n0 = frame["origin_projected"]
    local_parts = [affinity.translate(p.geometry, -e0, -n0) for p in parts]
    tree = STRtree(local_parts)

    features: list[dict[str, Any]] = []
    results: list[ParentSplit] = []
    for feature in scene["features"]:
        if feature.get("kind") != "building":
            features.append(dict(feature))
            continue
        if is_landmark(feature.get("tags") or {}):
            results.append(ParentSplit(feature["id"], "landmark", 0.0))
            features.append(dict(feature))
            continue
        footprint = shape(feature["geometry"])
        overlaps = [(parts[i], local_parts[i]) for i in sorted(int(k) for k in tree.query(footprint, predicate="intersects"))]
        result = split_footprint(feature["id"], footprint, overlaps, options)
        results.append(result)
        if result.subs:
            features.extend(_sub_feature(feature, sub, record) for sub in result.subs)
        else:
            features.append(dict(feature))
    results.sort(key=lambda r: r.parent_id)
    return features, results


def split_summary(results: Sequence[ParentSplit]) -> dict[str, Any]:
    """``split`` section of the cadastre report and ``subdivision.counts`` of the scene."""
    status = Counter(r.status for r in results)
    levels = [s.levels for r in results for s in r.subs]
    return {
        "osm_buildings": len(results),
        "osm_buildings_split": status["split"],
        "sub_buildings": len(levels),
        "levels_range": [min(levels), max(levels)] if levels else None,
        "courtyard_m2": round(sum(r.courtyard_m2 for r in results), 3),
        "dropped_m2": round(sum(r.dropped_m2 for r in results), 3),
        **{f"status_{name}": status[name] for name in STATUSES},
    }


def split_overrides(results: Sequence[ParentSplit], record: DownloadRecord, options: SplitOptions) -> list[dict[str, Any]]:
    """Heights overrides (``levels`` only) for every sub-building."""
    entries = []
    for result in results:
        for sub in result.subs:
            confident = sub.own_share >= options.confident_share
            entries.append(
                {
                    "id": sub.id,
                    "levels": sub.levels,
                    "source": SOURCE_LABEL,
                    "license": record.license,
                    "retrieved_at": record.retrieved_at,
                    "confidence": options.confidence_high if confident else options.confidence_low,
                    "note": (
                        f"split from {sub.parent_id}; refcat {sub.refcat}; {len(sub.parts)} part(s); "
                        f"own share {sub.own_share:.2f}"
                    ),
                }
            )
    return entries


def subdivided_scene(
    scene: Mapping[str, Any],
    features: Sequence[Mapping[str, Any]],
    results: Sequence[ParentSplit],
    *,
    scene_ref: Mapping[str, Any],
    record: DownloadRecord,
    options: SplitOptions,
) -> dict[str, Any]:
    """The geo scene contract with split footprints and a ``subdivision`` block."""
    return {
        **scene,
        "features": [dict(f) for f in features],
        "subdivision": {
            "method": SPLIT_METHOD,
            "geo_scene": dict(scene_ref),
            "cadastre": {
                "provider": record.provider,
                "license": record.license,
                "license_url": record.license_url,
                "attribution": record.attribution,
                "retrieved_at": record.retrieved_at,
                "sha256": record.sha256,
                "url": record.url,
            },
            "options": options.to_dict(),
            "counts": split_summary(results),
        },
    }

