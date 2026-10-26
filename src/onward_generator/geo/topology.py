"""Geometry validation and duplicate detection in local metres."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence

import numpy as np
import shapely
from shapely import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.validation import explain_validity

from onward_generator.domain.models import OsmRef
from onward_generator.geo.issues import GeoIssue

DUPLICATE_GRID_M = 0.001
"""Coordinates are snapped to a 1 mm grid before comparing geometries."""


def _has_repeated_vertices(geometry: BaseGeometry) -> bool:
    lines: Iterable[BaseGeometry]
    if isinstance(geometry, Polygon):
        lines = [geometry.exterior, *geometry.interiors]
    elif isinstance(geometry, MultiPolygon):
        lines = [r for p in geometry.geoms for r in (p.exterior, *p.interiors)]
    elif geometry.geom_type in ("LineString", "LinearRing"):
        lines = [geometry]
    elif geometry.geom_type == "MultiLineString":
        lines = list(geometry.geoms)
    else:
        return False
    for line in lines:
        coords = np.asarray(line.coords)
        if len(coords) > 1 and (np.abs(np.diff(coords, axis=0)).sum(axis=1) == 0).any():
            return True
    return False


def check_geometry(
    ref: OsmRef,
    geometry: BaseGeometry,
    *,
    min_area_m2: float = 1.0,
    min_length_m: float = 0.5,
) -> list[GeoIssue]:
    """Return errors (geometry unusable) and warnings (usable but suspect)."""
    if geometry.is_empty:
        return [GeoIssue("empty_geometry", "error", f"{ref.key} has an empty geometry", ref)]

    issues: list[GeoIssue] = []
    if not geometry.is_valid:
        # Area and length of an invalid geometry are meaningless (bow-tie lobes cancel out).
        issues.append(GeoIssue("invalid_geometry", "error", f"{ref.key}: {explain_validity(geometry)}", ref))
    elif isinstance(geometry, (Polygon, MultiPolygon)):
        if geometry.area < min_area_m2:
            issues.append(GeoIssue("tiny_polygon", "warning", f"{ref.key} area {geometry.area:.3f} m² < {min_area_m2} m²", ref))
    elif geometry.geom_type in ("LineString", "MultiLineString"):
        if geometry.length < min_length_m:
            issues.append(GeoIssue("tiny_line", "warning", f"{ref.key} length {geometry.length:.3f} m < {min_length_m} m", ref))
    if _has_repeated_vertices(geometry):
        issues.append(GeoIssue("repeated_vertices", "warning", f"{ref.key} has consecutive repeated vertices", ref))
    return issues


def has_errors(issues: Sequence[GeoIssue]) -> bool:
    return any(i.severity == "error" for i in issues)


def repair(geometry: BaseGeometry) -> BaseGeometry:
    """Return a valid geometry of the same dimension; the input is untouched."""
    fixed = shapely.make_valid(geometry)
    if isinstance(geometry, (Polygon, MultiPolygon)):
        polygons = [g for g in getattr(fixed, "geoms", [fixed]) if isinstance(g, (Polygon, MultiPolygon))]
        parts = [p for g in polygons for p in getattr(g, "geoms", [g])]
        return MultiPolygon(parts) if len(parts) != 1 else parts[0]
    return fixed


def find_duplicate_geometries(
    items: Sequence[tuple[OsmRef, str, BaseGeometry]],
) -> list[GeoIssue]:
    """Flag features of the same kind whose geometries are identical at 1 mm."""
    groups: dict[tuple[str, bytes], list[OsmRef]] = defaultdict(list)
    for ref, kind, geometry in items:
        key = shapely.normalize(shapely.set_precision(geometry, DUPLICATE_GRID_M)).wkb
        groups[(kind, key)].append(ref)
    issues = []
    for (kind, _), refs in groups.items():
        if len(refs) > 1:
            names = ", ".join(r.key for r in refs)
            for ref in refs[1:]:
                issues.append(GeoIssue("duplicate_geometry", "warning", f"{kind} geometry duplicated by: {names}", ref))
    return issues
