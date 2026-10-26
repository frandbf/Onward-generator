from __future__ import annotations

from pathlib import Path

import pytest

from onward_generator.domain.models import OsmRef
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
    assert [(i.code, i.ref) for i in issues] == [("missing_outer_ring", OsmRef("relation", 20))]


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
