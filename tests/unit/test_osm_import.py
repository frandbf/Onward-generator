from __future__ import annotations

from pathlib import Path

import pytest
from shapely import Point

from onward_generator.domain.models import OsmRef
from onward_generator.geo.issues import GeoIssue
from onward_generator.geo.osm_import import (
    FeatureKind,
    OsmImportError,
    build_source_features,
    is_area,
    parse_osm_xml,
)
from tests.conftest import MURCIA_CENTER, OSM_FIXTURE

LON, LAT = MURCIA_CENTER


def write(tmp_path: Path, body: str, name: str = "t.osm") -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def test_fixture_counts_and_header() -> None:
    doc = parse_osm_xml(OSM_FIXTURE)
    assert len(doc.nodes) == 41
    assert len(doc.ways) == 13
    assert len(doc.relations) == 2
    assert doc.generator == "onward-synthetic-fixture"
    assert doc.data_timestamp == "2026-10-18T00:00:00Z"


def test_original_ids_versions_and_tags_preserved() -> None:
    doc = parse_osm_xml(OSM_FIXTURE)
    way = doc.ways[101]
    assert way.version == 3
    assert dict(way.tags) == {"building": "yes", "height": "12", "building:levels": "4", "name": "Edificio Sintético A"}
    assert doc.nodes[40].tags["name"] == "Fuente Sintética"


def test_duplicate_element_id_keeps_first() -> None:
    doc = parse_osm_xml(OSM_FIXTURE)
    duplicate = [i for i in doc.issues if i.code == "duplicate_element_id"]
    assert [i.ref for i in duplicate] == [OsmRef("way", 101)]
    assert "height" in doc.ways[101].tags


def test_feature_assembly_and_build_errors() -> None:
    features, issues, skipped = build_source_features(parse_osm_xml(OSM_FIXTURE))
    by_ref = {f.ref: f for f in features}
    assert by_ref[OsmRef("way", 101)].kind is FeatureKind.BUILDING
    assert by_ref[OsmRef("way", 105)].kind is FeatureKind.HIGHWAY
    assert by_ref[OsmRef("node", 40)].kind is FeatureKind.POI
    multipolygon = by_ref[OsmRef("relation", 201)]
    assert multipolygon.kind is FeatureKind.BUILDING
    assert multipolygon.geometry_wgs84.geom_type == "MultiPolygon"
    assert len(multipolygon.geometry_wgs84.geoms[0].interiors) == 1

    codes = {(i.code, i.ref) for i in issues}
    assert ("missing_node_reference", OsmRef("way", 109)) in codes
    assert ("degenerate_ring", OsmRef("way", 112)) in codes
    assert ("unsupported_relation", OsmRef("relation", 202)) in codes
    assert skipped == {"untagged_ways": 2, "unsupported_relations": 1}
    assert OsmRef("way", 110) not in by_ref


def test_multipolygon_without_outer_is_reported(tmp_path: Path) -> None:
    nodes = "".join(
        f'<node id="{i}" lat="{LAT + dy}" lon="{LON + dx}"/>'
        for i, (dx, dy) in enumerate([(0, 0), (0.0001, 0), (0.0001, 0.0001)], start=1)
    )
    path = write(
        tmp_path,
        f"""<osm version="0.6">{nodes}
        <way id="10"><nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="1"/></way>
        <relation id="20"><member type="way" ref="10" role="inner"/>
        <tag k="type" v="multipolygon"/><tag k="highway" v="pedestrian"/></relation>
        </osm>""",
    )
    features, issues, _ = build_source_features(parse_osm_xml(path))
    assert features == []
    assert issues == [GeoIssue("missing_outer_ring", "error", "osm-relation-20 has no outer member", OsmRef("relation", 20))]


def test_is_area_rules() -> None:
    assert is_area({"building": "yes"})
    assert not is_area({"highway": "pedestrian"})
    assert is_area({"highway": "pedestrian", "area": "yes"})
    assert not is_area({"building": "yes", "area": "no"})
    assert not is_area({"natural": "tree_row"})


def test_doctype_is_rejected(tmp_path: Path) -> None:
    path = write(tmp_path, '<?xml version="1.0"?><!DOCTYPE osm [<!ENTITY a "x">]><osm version="0.6"/>')
    with pytest.raises(OsmImportError, match="DTD"):
        parse_osm_xml(path)


def test_wrong_root_and_broken_xml(tmp_path: Path) -> None:
    with pytest.raises(OsmImportError, match="root"):
        parse_osm_xml(write(tmp_path, "<gpx/>", "a.osm"))
    with pytest.raises(OsmImportError, match="invalid XML"):
        parse_osm_xml(write(tmp_path, "<osm><node id='1'", "b.osm"))


def test_malformed_and_deleted_elements_are_reported(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        f"""<osm version="0.6">
        <node id="1" lat="95.0" lon="{LON}"/>
        <node id="2" lat="x" lon="{LON}"/>
        <node id="3" lat="{LAT}" lon="{LON}" action="delete"/>
        <node lat="{LAT}" lon="{LON}"/>
        <node id="4" lat="{LAT}" lon="{LON}"/>
        </osm>""",
    )
    doc = parse_osm_xml(path)
    assert list(doc.nodes) == [4]
    assert sorted(i.code for i in doc.issues) == ["deleted_element", "malformed_element", "malformed_element", "malformed_element"]


def _multipolygon_file(tmp_path: Path, points: dict[int, tuple[int, int]], ways: dict[int, list[int]], members: list[tuple[int, str]]) -> Path:
    """Nodes on a grid of 0.0001 degrees, ways by node id, and one ``building`` multipolygon."""
    nodes = "".join(f'<node id="{i}" lat="{LAT + y * 0.0001}" lon="{LON + x * 0.0001}"/>' for i, (x, y) in points.items())
    way_xml = "".join(f'<way id="{w}">' + "".join(f'<nd ref="{n}"/>' for n in refs) + "</way>" for w, refs in ways.items())
    member_xml = "".join(f'<member type="way" ref="{w}" role="{role}"/>' for w, role in members)
    return write(
        tmp_path,
        f"""<osm version="0.6">{nodes}{way_xml}
        <relation id="30">{member_xml}<tag k="type" v="multipolygon"/><tag k="building" v="yes"/></relation>
        </osm>""",
    )


def _square(first: int, x0: int, y0: int, x1: int, y1: int) -> dict[int, tuple[int, int]]:
    return {first: (x0, y0), first + 1: (x1, y0), first + 2: (x1, y1), first + 3: (x0, y1)}


def _ring(first: int) -> list[int]:
    return [first, first + 1, first + 2, first + 3, first]


def test_multipolygon_skips_a_node_member(tmp_path: Path) -> None:
    path = _multipolygon_file(tmp_path, _square(1, 0, 0, 10, 10), {10: _ring(1)}, [(10, "outer")])
    text = path.read_text(encoding="utf-8").replace('<relation id="30">', '<relation id="30"><member type="node" ref="1" role="label"/>')
    path.write_text(text, encoding="utf-8")
    features, issues, _ = build_source_features(parse_osm_xml(path))
    assert issues == []
    [polygon] = features[0].geometry_wgs84.geoms
    assert polygon.area == pytest.approx(1e-6)


def test_multipolygon_outer_split_across_three_ways(tmp_path: Path) -> None:
    path = _multipolygon_file(
        tmp_path,
        _square(1, 0, 0, 10, 10),
        {10: [1, 2], 11: [2, 3, 4], 12: [4, 1]},
        [(11, "outer"), (10, "outer"), (12, "outer")],
    )
    features, issues, _ = build_source_features(parse_osm_xml(path))
    assert issues == []
    [feature] = features
    assert feature.ref == OsmRef("relation", 30)
    assert feature.geometry_wgs84.geom_type == "MultiPolygon"
    [polygon] = feature.geometry_wgs84.geoms
    assert polygon.area == pytest.approx(1e-6)
    assert len(polygon.interiors) == 0


def test_multipolygon_open_outer_is_reported(tmp_path: Path) -> None:
    path = _multipolygon_file(tmp_path, _square(1, 0, 0, 10, 10), {10: [1, 2, 3, 4]}, [(10, "outer")])
    features, issues, _ = build_source_features(parse_osm_xml(path))
    assert features == []
    assert issues == [
        GeoIssue(
            "unclosed_multipolygon_ring",
            "error",
            "osm-relation-30 has rings that do not close (1 open piece(s) in the member ways)",
            OsmRef("relation", 30),
        )
    ]


def test_multipolygon_closed_outer_with_an_open_inner_is_reported(tmp_path: Path) -> None:
    points = _square(1, 0, 0, 10, 10) | _square(5, 2, 2, 8, 8)
    path = _multipolygon_file(tmp_path, points, {10: _ring(1), 11: [5, 6, 7]}, [(10, "outer"), (11, "inner")])
    features, issues, _ = build_source_features(parse_osm_xml(path))
    assert features == []
    assert [i.code for i in issues] == ["unclosed_multipolygon_ring"]


@pytest.mark.parametrize("ways", [{10: _ring(1)}, {10: _ring(1), 11: [5]}])
def test_multipolygon_missing_or_short_member_is_reported(tmp_path: Path, ways: dict[int, list[int]]) -> None:
    points = _square(1, 0, 0, 10, 10) | {5: (2, 2)}
    path = _multipolygon_file(tmp_path, points, ways, [(10, "outer"), (11, "inner")])
    features, issues, _ = build_source_features(parse_osm_xml(path))
    assert features == []
    assert issues[-1] == GeoIssue(
        "missing_member", "error", "osm-relation-30 member way/11 is missing or incomplete", OsmRef("relation", 30)
    )


def test_inner_outside_outer_is_reported(tmp_path: Path) -> None:
    points = _square(1, 0, 0, 10, 10) | _square(5, 20, 0, 24, 4)
    path = _multipolygon_file(tmp_path, points, {10: _ring(1), 11: _ring(5)}, [(10, "outer"), (11, "inner")])
    features, issues, _ = build_source_features(parse_osm_xml(path))
    assert issues == [
        GeoIssue(
            "inner_ring_outside_outer",
            "warning",
            "osm-relation-30 has an inner ring that no outer ring encloses; it is kept as an area",
            OsmRef("relation", 30),
        )
    ]
    [feature] = features
    polygons = sorted(feature.geometry_wgs84.geoms, key=lambda p: p.area)
    assert [p.area for p in polygons] == pytest.approx([16e-8, 1e-6])
    assert all(len(p.interiors) == 0 for p in polygons)


def test_island_inside_hole(tmp_path: Path) -> None:
    points = _square(1, 0, 0, 10, 10) | _square(5, 2, 2, 8, 8) | _square(9, 4, 4, 6, 6)
    path = _multipolygon_file(
        tmp_path,
        points,
        {10: _ring(1), 11: _ring(5), 12: _ring(9)},
        [(10, "outer"), (11, "inner"), (12, "outer")],
    )
    features, issues, _ = build_source_features(parse_osm_xml(path))
    assert issues == []
    [feature] = features
    shell, island = sorted(feature.geometry_wgs84.geoms, key=lambda p: p.area, reverse=True)
    assert len(shell.interiors) == 1
    assert shell.area == pytest.approx((100 - 36) * 1e-8)
    assert len(island.interiors) == 0
    assert island.area == pytest.approx(4e-8)
    assert shell.exterior.is_ccw and not shell.interiors[0].is_ccw


def test_inner_touching_outer_at_a_node_is_a_hole(tmp_path: Path) -> None:
    points = _square(1, 0, 0, 10, 10) | {5: (0, 5), 6: (3, 3), 7: (3, 7)}
    path = _multipolygon_file(
        tmp_path,
        points,
        {10: [1, 2, 3, 4, 5, 1], 11: [5, 6, 7, 5]},
        [(10, "outer"), (11, "inner")],
    )
    features, issues, _ = build_source_features(parse_osm_xml(path))
    assert issues == []
    [polygon] = features[0].geometry_wgs84.geoms
    assert polygon.area == pytest.approx((100 - 6) * 1e-8)


@pytest.mark.parametrize("outer_has_the_shared_nodes", [True, False])
def test_inner_sharing_an_edge_with_outer_is_a_hole(tmp_path: Path, outer_has_the_shared_nodes: bool) -> None:
    # The courtyard runs along the west wall, so polygonize yields two simply connected faces.
    points = _square(1, 0, 0, 10, 10) | _square(5, 0, 3, 4, 7)
    outer = [1, 2, 3, 4, 8, 5, 1] if outer_has_the_shared_nodes else _ring(1)
    path = _multipolygon_file(tmp_path, points, {10: outer, 11: _ring(5)}, [(10, "outer"), (11, "inner")])
    features, issues, _ = build_source_features(parse_osm_xml(path))
    assert issues == []
    [feature] = features
    assert feature.ref == OsmRef("relation", 30)
    [polygon] = feature.geometry_wgs84.geoms
    assert polygon.area == pytest.approx((100 - 16) * 1e-8)
    assert len(polygon.interiors) == 0
    assert not polygon.contains(Point(LON + 2 * 0.0001, LAT + 5 * 0.0001))


def test_inner_touching_an_outer_corner_is_a_hole(tmp_path: Path) -> None:
    points = _square(1, 0, 0, 10, 10) | {5: (3, 1), 6: (1, 3)}
    path = _multipolygon_file(tmp_path, points, {10: _ring(1), 11: [1, 5, 6, 1]}, [(10, "outer"), (11, "inner")])
    features, issues, _ = build_source_features(parse_osm_xml(path))
    assert issues == []
    [polygon] = features[0].geometry_wgs84.geoms
    assert polygon.area == pytest.approx((100 - 4) * 1e-8)
    assert not polygon.contains(Point(LON + 1.3 * 0.0001, LAT + 1.3 * 0.0001))


def test_inner_crossing_the_outer_edge_follows_even_odd(tmp_path: Path) -> None:
    # The part of the inner inside the outer is a hole; the part outside is filled. Neither is reported.
    points = _square(1, 0, 0, 10, 10) | _square(5, 8, 4, 12, 6)
    path = _multipolygon_file(tmp_path, points, {10: _ring(1), 11: _ring(5)}, [(10, "outer"), (11, "inner")])
    features, issues, _ = build_source_features(parse_osm_xml(path))
    assert issues == []
    geometry = features[0].geometry_wgs84
    assert geometry.area == pytest.approx(100e-8)
    assert not geometry.contains(Point(LON + 9 * 0.0001, LAT + 5 * 0.0001))
    assert geometry.contains(Point(LON + 11 * 0.0001, LAT + 5 * 0.0001))


def _two_outers_sharing_a_way(tmp_path: Path, with_inner: bool) -> Path:
    # Outers A (0,0)-(10,10) and B (10,0)-(20,10); way 12 is the wall between them, used once.
    points = {1: (0, 0), 2: (10, 0), 3: (10, 10), 4: (0, 10), 5: (20, 0), 6: (20, 10)} | _square(7, 2, 2, 8, 8)
    ways = {11: [2, 1, 4, 3], 12: [2, 3], 13: [3, 6, 5, 2], 14: _ring(7)}
    members = [(11, "outer"), (12, "outer"), (13, "outer")] + ([(14, "inner")] if with_inner else [])
    return _multipolygon_file(tmp_path, points, ways, members)


def test_two_outers_sharing_a_way_are_both_filled(tmp_path: Path) -> None:
    features, issues, _ = build_source_features(parse_osm_xml(_two_outers_sharing_a_way(tmp_path, with_inner=False)))
    assert issues == []
    [feature] = features
    assert feature.ref == OsmRef("relation", 30)
    assert feature.geometry_wgs84.area == pytest.approx(200e-8)
    assert feature.geometry_wgs84.contains(Point(LON + 5 * 0.0001, LAT + 5 * 0.0001))
    assert feature.geometry_wgs84.contains(Point(LON + 15 * 0.0001, LAT + 5 * 0.0001))


def test_inner_in_one_of_two_outers_sharing_a_way_is_a_hole(tmp_path: Path) -> None:
    features, issues, _ = build_source_features(parse_osm_xml(_two_outers_sharing_a_way(tmp_path, with_inner=True)))
    assert issues == []
    geometry = features[0].geometry_wgs84
    assert geometry.area == pytest.approx((200 - 36) * 1e-8)
    assert not geometry.contains(Point(LON + 5 * 0.0001, LAT + 5 * 0.0001))


def test_outer_inside_outer_sharing_an_edge_is_kept_filled(tmp_path: Path) -> None:
    # Known limitation: without inner members this is the same linework as two adjacent outers.
    points = _square(1, 0, 0, 10, 10) | _square(5, 0, 3, 4, 7)
    path = _multipolygon_file(tmp_path, points, {10: _ring(1), 11: _ring(5)}, [(10, "outer"), (11, "outer")])
    features, issues, _ = build_source_features(parse_osm_xml(path))
    assert issues == []
    assert features[0].geometry_wgs84.area == pytest.approx(100e-8)


def test_two_inners_sharing_an_edge_are_both_holes(tmp_path: Path) -> None:
    points = _square(1, 0, 0, 10, 10) | _square(5, 2, 2, 5, 8) | {9: (8, 2), 10: (8, 8)}
    path = _multipolygon_file(
        tmp_path,
        points,
        {10: _ring(1), 11: _ring(5), 12: [6, 9, 10, 7, 6]},
        [(10, "outer"), (11, "inner"), (12, "inner")],
    )
    features, issues, _ = build_source_features(parse_osm_xml(path))
    assert issues == []
    [polygon] = features[0].geometry_wgs84.geoms
    assert polygon.area == pytest.approx((100 - 36) * 1e-8)
    assert len(polygon.interiors) == 1


def test_nested_outer_without_inner_is_reported_as_hole(tmp_path: Path) -> None:
    points = _square(1, 0, 0, 10, 10) | _square(5, 2, 2, 8, 8)
    path = _multipolygon_file(tmp_path, points, {10: _ring(1), 11: _ring(5)}, [(10, "outer"), (11, "outer")])
    features, issues, _ = build_source_features(parse_osm_xml(path))
    assert issues == [
        GeoIssue(
            "outer_ring_inside_outer",
            "warning",
            "osm-relation-30 has an outer ring inside another outer ring; it is kept as a hole",
            OsmRef("relation", 30),
        )
    ]
    [polygon] = features[0].geometry_wgs84.geoms
    assert len(polygon.interiors) == 1
