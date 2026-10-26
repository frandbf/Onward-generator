"""Demonstration 3 (unit level): geometric errors are detected."""

from __future__ import annotations

from shapely import LineString, Polygon, box

from onward_generator.domain.models import OsmRef
from onward_generator.geo.topology import check_geometry, find_duplicate_geometries, has_errors, repair

REF = OsmRef("way", 1)


def codes(geometry) -> set[str]:
    return {issue.code for issue in check_geometry(REF, geometry)}


def test_valid_square_has_no_issues() -> None:
    assert check_geometry(REF, box(0, 0, 10, 10)) == []


def test_bowtie_is_invalid() -> None:
    issues = check_geometry(REF, Polygon([(0, 0), (20, 20), (20, 0), (0, 20)]))
    assert has_errors(issues)
    assert "invalid_geometry" in {i.code for i in issues}
    assert "Self-intersection" in issues[0].message


def test_collapsed_polygon_is_error_without_size_noise() -> None:
    assert codes(Polygon([(0, 0), (10, 0), (20, 0)])) == {"invalid_geometry"}
    assert codes(Polygon([(0, 0), (20, 20), (20, 0), (0, 20)])) == {"invalid_geometry"}


def test_tiny_polygon_and_line_are_warnings() -> None:
    assert codes(box(0, 0, 0.5, 0.5)) == {"tiny_polygon"}
    assert codes(LineString([(0, 0), (0.1, 0)])) == {"tiny_line"}


def test_repeated_vertices_warning() -> None:
    assert "repeated_vertices" in codes(LineString([(0, 0), (5, 0), (5, 0), (10, 0)]))


def test_empty_geometry_is_error() -> None:
    assert codes(Polygon()) == {"empty_geometry"}


def test_repair_returns_valid_polygonal_geometry_without_touching_input() -> None:
    bowtie = Polygon([(0, 0), (20, 20), (20, 0), (0, 20)])
    wkt_before = bowtie.wkt
    fixed = repair(bowtie)
    assert fixed.is_valid and fixed.geom_type == "MultiPolygon"
    assert fixed.area == 200
    assert bowtie.wkt == wkt_before


def test_duplicates_detected_regardless_of_vertex_order_and_sub_mm_noise() -> None:
    a = box(0, 0, 10, 10)
    b = Polygon([(10, 10), (0.0004, 10), (0, 0), (10, 0)])
    c = box(20, 0, 30, 10)
    issues = find_duplicate_geometries(
        [(OsmRef("way", 1), "building", a), (OsmRef("way", 2), "building", b), (OsmRef("way", 3), "building", c)]
    )
    assert [(i.code, i.ref) for i in issues] == [("duplicate_geometry", OsmRef("way", 2))]


def test_same_geometry_different_kind_is_not_duplicate() -> None:
    square = box(0, 0, 10, 10)
    assert find_duplicate_geometries([(OsmRef("way", 1), "building", square), (OsmRef("way", 2), "area", square)]) == []
