from __future__ import annotations

import pytest
from shapely import LineString, Point, Polygon, box

from onward_generator.geo.clipping import ClipError, ClipStatus, clip_feature, inclusion_zone
from onward_generator.geo.osm_import import FeatureKind
from tests.conftest import MURCIA_CENTER

AOI = box(-100, -100, 100, 100)
ZONE = inclusion_zone(AOI, 30.0)


def test_zone_is_buffered_by_margin_with_square_corners() -> None:
    assert ZONE.bounds == pytest.approx((-130, -130, 130, 130))
    assert ZONE.area == pytest.approx(260 * 260)


def test_zero_margin_returns_aoi() -> None:
    assert inclusion_zone(AOI, 0.0).equals(AOI)


@pytest.mark.parametrize("margin", [-1.0, float("nan"), float("inf")])
def test_invalid_margin_is_rejected(margin: float) -> None:
    with pytest.raises(ClipError):
        inclusion_zone(AOI, margin)


def test_aoi_in_degrees_is_rejected() -> None:
    lon, lat = MURCIA_CENTER
    degrees_box = box(lon - 0.001, lat - 0.001, lon + 0.001, lat + 0.001)
    with pytest.raises(ClipError, match="degrees"):
        inclusion_zone(degrees_box, 30.0)


def test_polygon_inside() -> None:
    decision = clip_feature(FeatureKind.BUILDING, box(-50, -50, -20, -20), AOI, ZONE)
    assert decision.status is ClipStatus.INSIDE


def test_building_fully_inside_the_margin_is_kept_whole() -> None:
    building = box(105, -20, 120, 10)
    decision = clip_feature(FeatureKind.BUILDING, building, AOI, ZONE)
    assert decision.status is ClipStatus.MARGIN
    assert decision.geometry is not None and decision.geometry.equals(building)


def test_building_that_leaves_the_map_is_dropped_whole() -> None:
    building = box(120, 80, 150, 100)
    decision = clip_feature(FeatureKind.BUILDING, building, AOI, ZONE)
    assert decision.status is ClipStatus.OUTSIDE and decision.geometry is None


def test_area_crossing_the_zone_edge_is_kept_whole() -> None:
    park = box(120, 80, 150, 100)
    decision = clip_feature(FeatureKind.AREA, park, AOI, ZONE)
    assert decision.status is ClipStatus.MARGIN
    assert decision.geometry is not None and decision.geometry.equals(park)


def test_polygon_outside_is_dropped() -> None:
    decision = clip_feature(FeatureKind.BUILDING, box(200, 200, 220, 220), AOI, ZONE)
    assert decision.status is ClipStatus.OUTSIDE and decision.geometry is None


def test_line_is_cut_to_zone() -> None:
    road = LineString([(-200, 10), (0, 10), (200, 10)])
    decision = clip_feature(FeatureKind.HIGHWAY, road, AOI, ZONE)
    assert decision.status is ClipStatus.CLIPPED
    assert decision.geometry is not None
    assert decision.geometry.bounds == pytest.approx((-130, 10, 130, 10))
    assert decision.geometry.length == pytest.approx(260)


def test_line_leaving_and_reentering_becomes_multiline() -> None:
    aoi = Polygon([(0, 0), (10, 0), (10, 10), (6, 10), (6, 2), (4, 2), (4, 10), (0, 10)])
    line = LineString([(-1, 5), (11, 5)])
    decision = clip_feature(FeatureKind.HIGHWAY, line, aoi, aoi)
    assert decision.status is ClipStatus.CLIPPED
    assert decision.geometry is not None and decision.geometry.geom_type == "MultiLineString"


def test_point_in_margin_and_outside() -> None:
    assert clip_feature(FeatureKind.POI, Point(115, 0), AOI, ZONE).status is ClipStatus.MARGIN
    assert clip_feature(FeatureKind.POI, Point(300, 0), AOI, ZONE).status is ClipStatus.OUTSIDE
