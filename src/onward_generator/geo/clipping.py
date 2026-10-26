"""Area-of-interest selection in local metres.

Inclusion zone = AOI polygon buffered by ``margin_m``. Polygons and points
that touch the zone are kept whole (no abrupt cuts through buildings);
lines are cut to the zone.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from shapely import LineString, MultiLineString, Polygon
from shapely.geometry.base import BaseGeometry

from onward_generator.geo.osm_import import FeatureKind

MIN_AOI_AREA_M2 = 1.0


class ClipError(ValueError):
    """Raised for an unusable area of interest."""


class ClipStatus(StrEnum):
    INSIDE = "inside"
    """Covered by the AOI itself."""
    MARGIN = "margin"
    """Touches the inclusion zone but is not covered by the AOI; kept whole."""
    CLIPPED = "clipped"
    """A line cut to the inclusion zone."""
    OUTSIDE = "outside"
    """Does not touch the inclusion zone; dropped."""


@dataclass(frozen=True, slots=True)
class ClipDecision:
    status: ClipStatus
    geometry: BaseGeometry | None


def inclusion_zone(aoi_local: Polygon, margin_m: float) -> Polygon:
    """Buffer the local AOI polygon by a margin in metres."""
    if not isinstance(aoi_local, Polygon) or aoi_local.is_empty or not aoi_local.is_valid:
        raise ClipError("area of interest must be a valid, non-empty polygon")
    if aoi_local.area < MIN_AOI_AREA_M2:
        raise ClipError(
            f"area of interest covers {aoi_local.area:.3g} m²; it is probably still in degrees, not metres"
        )
    if not math.isfinite(margin_m) or margin_m < 0:
        raise ClipError("margin_m must be a finite, non-negative number of metres")
    return aoi_local.buffer(margin_m, join_style="mitre") if margin_m > 0 else aoi_local


def _is_linear(geometry: BaseGeometry) -> bool:
    return isinstance(geometry, (LineString, MultiLineString))


def clip_feature(kind: FeatureKind, geometry: BaseGeometry, aoi_local: Polygon, zone: Polygon) -> ClipDecision:
    """Decide whether a valid local geometry is kept, and its kept shape."""
    if geometry.is_empty or not geometry.intersects(zone):
        return ClipDecision(ClipStatus.OUTSIDE, None)
    if aoi_local.covers(geometry):
        return ClipDecision(ClipStatus.INSIDE, geometry)
    if not _is_linear(geometry) or zone.covers(geometry):
        return ClipDecision(ClipStatus.MARGIN, geometry)

    clipped = geometry.intersection(zone)
    linear = [g for g in getattr(clipped, "geoms", [clipped]) if _is_linear(g) and not g.is_empty]
    if not linear:
        return ClipDecision(ClipStatus.OUTSIDE, None)
    if len(linear) == 1:
        return ClipDecision(ClipStatus.CLIPPED, linear[0])
    parts = [line for g in linear for line in getattr(g, "geoms", [g])]
    return ClipDecision(ClipStatus.CLIPPED, MultiLineString(parts))
