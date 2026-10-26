"""Demonstration 1: a known geographic geometry is transformed correctly."""

from __future__ import annotations

import math

import pytest
from shapely import LineString, Point, Polygon

from onward_generator.geo.projection import DEFAULT_CRS, LocalFrame, ProjectionError
from tests.conftest import MURCIA_CENTER

# GRS80 (ETRS89) and UTM zone 30N constants.
A = 6_378_137.0
F = 1 / 298.257222101
K0 = 0.9996
LON0 = math.radians(-3.0)
FALSE_EASTING = 500_000.0


def utm30n_kruger(lon_deg: float, lat_deg: float) -> tuple[float, float]:
    """Independent Transverse Mercator forward formula (Krüger series, 3rd order)."""
    n = F / (2 - F)
    big_a = A / (1 + n) * (1 + n**2 / 4 + n**4 / 64)
    alpha = (n / 2 - 2 * n**2 / 3 + 5 * n**3 / 16, 13 * n**2 / 48 - 3 * n**3 / 5, 61 * n**3 / 240)
    phi, dlam = math.radians(lat_deg), math.radians(lon_deg) - LON0
    c = 2 * math.sqrt(n) / (1 + n)
    t = math.sinh(math.atanh(math.sin(phi)) - c * math.atanh(c * math.sin(phi)))
    xi = math.atan2(t, math.cos(dlam))
    eta = math.atanh(math.sin(dlam) / math.sqrt(1 + t * t))
    easting = FALSE_EASTING + K0 * big_a * (
        eta + sum(a * math.cos(2 * j * xi) * math.sinh(2 * j * eta) for j, a in enumerate(alpha, 1))
    )
    northing = K0 * big_a * (xi + sum(a * math.sin(2 * j * xi) * math.cosh(2 * j * eta) for j, a in enumerate(alpha, 1)))
    return easting, northing


MURCIA = MURCIA_CENTER
LON, LAT = MURCIA


@pytest.fixture
def frame() -> LocalFrame:
    return LocalFrame.create(*MURCIA)


def test_default_crs_is_etrs89_utm30n(frame: LocalFrame) -> None:
    assert frame.crs == DEFAULT_CRS == "EPSG:25830"
    assert frame.to_dict()["unit"] == "m"


@pytest.mark.parametrize(
    "lon, lat",
    [(LON, LAT), (LON - 0.01, LAT - 0.01), (LON + 0.01, LAT + 0.01), (LON - 0.01, LAT + 0.01), (LON + 0.01, LAT - 0.01)],
)
def test_projection_matches_independent_kruger_formula(frame: LocalFrame, lon: float, lat: float) -> None:
    projected = frame.project(Point(lon, lat))
    easting, northing = utm30n_kruger(lon, lat)
    assert projected.x == pytest.approx(easting, abs=0.005)
    assert projected.y == pytest.approx(northing, abs=0.005)


def test_central_meridian_on_equator_is_false_origin(frame: LocalFrame) -> None:
    point = frame.project(Point(-3.0, 0.0))
    assert point.x == pytest.approx(500_000.0, abs=1e-6)
    assert point.y == pytest.approx(0.0, abs=1e-6)


def test_origin_maps_to_local_zero(frame: LocalFrame) -> None:
    local = frame.to_local(Point(*MURCIA))
    assert (local.x, local.y) == pytest.approx((0.0, 0.0), abs=1e-6)
    expected = utm30n_kruger(*MURCIA)
    assert (frame.origin_easting, frame.origin_northing) == pytest.approx(expected, abs=0.005)


def test_known_square_keeps_metric_size_and_orientation(frame: LocalFrame) -> None:
    square_local = Polygon([(0, 0), (100, 0), (100, 100), (0, 100)])
    roundtrip = frame.to_local(frame.to_wgs84(square_local))
    assert roundtrip.area == pytest.approx(10_000.0, abs=1e-4)
    assert roundtrip.equals_exact(square_local, tolerance=1e-6)

    # 100 m east in the local frame is a smaller change in longitude than 100 m north in latitude.
    corner = frame.to_wgs84(Point(100, 100))
    dlon, dlat = corner.x - MURCIA[0], corner.y - MURCIA[1]
    assert dlon > dlat > 0


def test_meridian_distance_of_a_thousandth_degree(frame: LocalFrame) -> None:
    line = frame.to_local(LineString([(LON, LAT), (LON, LAT + 0.001)]))
    # Meridian arc for 0.001° at ~38° N on GRS80 ≈ 110.98 m, times the UTM scale factor here (~0.99994).
    assert line.length == pytest.approx(110.97, abs=0.05)


def test_degrees_are_rejected_as_local_input(frame: LocalFrame) -> None:
    with pytest.raises(ProjectionError):
        frame.to_local(Point(500.0, 20.0))


@pytest.mark.parametrize("crs", ["EPSG:4326", "EPSG:2229"])
def test_non_metric_or_geographic_crs_is_rejected(crs: str) -> None:
    with pytest.raises(ProjectionError):
        LocalFrame.create(*MURCIA, crs=crs)


def test_origin_outside_crs_area_is_rejected() -> None:
    with pytest.raises(ProjectionError):
        LocalFrame.create(10.0, 50.0)


def test_area_of_use_check(frame: LocalFrame) -> None:
    assert frame.within_area_of_use(Point(*MURCIA))
    assert not frame.within_area_of_use(Point(3.0, 40.0))
