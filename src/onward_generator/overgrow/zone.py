"""Dressing zone in local metres. A radius here is metres from the origin, not degrees."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from shapely import Point, box
from shapely.geometry import Polygon, shape
from shapely.geometry.base import BaseGeometry

from onward_generator.overgrow.constants import BLOCK_HALF_M


class ZoneError(ValueError):
    """The zone is not a finite area in local metres."""


@dataclass(frozen=True, slots=True)
class Zone:
    kind: str
    polygon: Polygon

    def contains(self, x: float, y: float) -> bool:
        return bool(self.polygon.covers(Point(float(x), float(y))))

    def bounds(self) -> tuple[float, float, float, float]:
        minx, miny, maxx, maxy = self.polygon.bounds
        return float(minx), float(miny), float(maxx), float(maxy)

    def stable_key(self) -> str:
        coords = [(round(float(x), 3), round(float(y), 3)) for x, y in self.polygon.exterior.coords]
        return f"{self.kind}:{coords}"


def square_zone(half_m: float, *, kind: str = "square") -> Zone:
    if half_m <= 0 or half_m > 2000:
        raise ZoneError("zone half-width must be within (0, 2000] metres")
    return Zone(kind, box(-half_m, -half_m, half_m, half_m))


def default_block_zone() -> Zone:
    """One block beside the origin: a 110 m square, not the whole city."""
    return square_zone(BLOCK_HALF_M, kind="block")


def radius_zone(radius_m: float) -> Zone:
    if radius_m <= 0 or radius_m > 2000:
        raise ZoneError("zone radius must be within (0, 2000] metres")
    return Zone("radius", Point(0.0, 0.0).buffer(radius_m, quad_segs=16))


def zone_from_polygon(polygon: Polygon, *, kind: str = "polygon") -> Zone:
    if polygon.is_empty or not polygon.is_valid:
        raise ZoneError("zone polygon is empty or invalid")
    if polygon.area < 4.0:
        raise ZoneError("zone covers less than 4 m²; it may still be in degrees")
    return Zone(kind, polygon)


def zone_from_file(path: str | Path) -> Zone:
    """GeoJSON Polygon or a ring ``[[x, y], ...]`` in local metres."""
    raw = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    if isinstance(raw, list):
        ring = [(float(p[0]), float(p[1])) for p in raw]
        return zone_from_polygon(Polygon(ring))
    if isinstance(raw, dict) and raw.get("type") == "Feature":
        raw = raw.get("geometry") or {}
    if isinstance(raw, dict) and raw.get("type") == "Polygon":
        return zone_from_polygon(shape(raw))
    raise ZoneError(f"{path}: expected a Polygon in local metres")


def within_core(zone: Zone, core: BaseGeometry | None) -> Zone:
    """The zone cut to the playable core (ADR-017). A zone already inside it, or a map without one, is kept as is."""
    if core is None or core.covers(zone.polygon):
        return zone
    clipped = zone.polygon.intersection(core)
    if not isinstance(clipped, Polygon) or clipped.is_empty:
        raise ZoneError("the zone does not overlap the playable core in one piece")
    return Zone(zone.kind, clipped)
