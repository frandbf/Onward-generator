"""WGS84 → projected metric CRS → local scene coordinates.

Local coordinates are projected grid metres relative to a fixed origin:
``x`` grows east, ``y`` grows north, both in metres. See
``docs/decisions/ADR-002-coordinate-system.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import shapely
from pyproj import CRS, Transformer
from shapely.geometry.base import BaseGeometry

from onward_generator.domain.models import validate_lon_lat

DEFAULT_CRS = "EPSG:25830"
"""ETRS89 / UTM zone 30N: official Spanish grid, covers mainland Spain 6°W–0°."""


def frame_for_origin(origin_lon: float, origin_lat: float) -> LocalFrame:
    """Metric frame for a WGS84 centre.

    Spain stays on EPSG:25830. Anywhere else uses the WGS 84 UTM zone that
    contains the centre (EPSG:326xx north, EPSG:327xx south). Degrees are
    never treated as metres. ADR-002.
    """
    try:
        return LocalFrame.create(origin_lon, origin_lat, DEFAULT_CRS)
    except ProjectionError as exc:
        if "outside the area of use" not in str(exc):
            raise
    zone = int((origin_lon + 180.0) / 6.0) + 1
    if not 1 <= zone <= 60:
        raise ProjectionError(f"longitude {origin_lon} does not fall in a UTM zone") from exc
    code = (32600 if origin_lat >= 0 else 32700) + zone
    return LocalFrame.create(origin_lon, origin_lat, f"EPSG:{code}")

COORDINATE_SYSTEM = "local_grid"


class ProjectionError(ValueError):
    """Raised for invalid CRS choices or coordinates outside their domain."""


def _check_lon_lat_array(coords: np.ndarray) -> None:
    lon, lat = coords[:, 0], coords[:, 1]
    if not (np.isfinite(coords).all() and (np.abs(lon) <= 180).all() and (np.abs(lat) <= 90).all()):
        raise ProjectionError("geometry has coordinates that are not WGS84 longitude/latitude degrees")


@dataclass(frozen=True)
class LocalFrame:
    """A projected CRS plus a local origin, both fixed for a scene."""

    crs: str
    origin_lon: float
    origin_lat: float
    origin_easting: float
    origin_northing: float
    _forward: Transformer = field(repr=False, compare=False)
    _inverse: Transformer = field(repr=False, compare=False)
    _area_of_use: tuple[float, float, float, float] = field(repr=False, compare=False)

    @classmethod
    def create(cls, origin_lon: float, origin_lat: float, crs: str = DEFAULT_CRS) -> LocalFrame:
        try:
            validate_lon_lat(origin_lon, origin_lat)
        except ValueError as exc:
            raise ProjectionError(str(exc)) from exc
        target = CRS.from_user_input(crs)
        if not target.is_projected:
            raise ProjectionError(f"{crs} is not a projected CRS")
        units = {axis.unit_name for axis in target.axis_info}
        if units != {"metre"}:
            raise ProjectionError(f"{crs} axes are not in metres: {sorted(units)}")
        aou = target.area_of_use
        if aou is None:
            raise ProjectionError(f"{crs} has no declared area of use")
        bounds = (aou.west, aou.south, aou.east, aou.north)
        if not (bounds[0] <= origin_lon <= bounds[2] and bounds[1] <= origin_lat <= bounds[3]):
            raise ProjectionError(f"origin ({origin_lon}, {origin_lat}) is outside the area of use of {crs}")

        forward = Transformer.from_crs("EPSG:4326", target, always_xy=True)
        inverse = Transformer.from_crs(target, "EPSG:4326", always_xy=True)
        easting, northing = forward.transform(origin_lon, origin_lat)
        return cls(crs, origin_lon, origin_lat, float(easting), float(northing), forward, inverse, bounds)

    def project(self, geometry_wgs84: BaseGeometry) -> BaseGeometry:
        """WGS84 degrees → projected CRS metres (absolute grid values)."""

        def forward(coords: np.ndarray) -> np.ndarray:
            _check_lon_lat_array(coords)
            e, n = self._forward.transform(coords[:, 0], coords[:, 1])
            out = np.column_stack([e, n])
            if not np.isfinite(out).all():
                raise ProjectionError(f"projection to {self.crs} produced non-finite values")
            return out

        return shapely.transform(geometry_wgs84, forward)

    def to_local(self, geometry_wgs84: BaseGeometry) -> BaseGeometry:
        """WGS84 degrees → local metres relative to the origin."""
        projected = self.project(geometry_wgs84)
        return shapely.transform(projected, lambda c: c - (self.origin_easting, self.origin_northing))

    def to_wgs84(self, geometry_local: BaseGeometry) -> BaseGeometry:
        """Local metres → WGS84 degrees."""

        def inverse(coords: np.ndarray) -> np.ndarray:
            lon, lat = self._inverse.transform(
                coords[:, 0] + self.origin_easting, coords[:, 1] + self.origin_northing
            )
            return np.column_stack([lon, lat])

        return shapely.transform(geometry_local, inverse)

    def within_area_of_use(self, geometry_wgs84: BaseGeometry) -> bool:
        west, south, east, north = self._area_of_use
        minx, miny, maxx, maxy = geometry_wgs84.bounds
        return west <= minx and maxx <= east and south <= miny and maxy <= north

    def to_dict(self) -> dict[str, Any]:
        return {
            "crs": self.crs,
            "coordinate_system": COORDINATE_SYSTEM,
            "unit": "m",
            "axes": {"x": "east", "y": "north"},
            "origin_wgs84": [self.origin_lon, self.origin_lat],
            "origin_projected": [self.origin_easting, self.origin_northing],
            "transformation": self._forward.description,
        }
