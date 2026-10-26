"""Phase 07.2: landmark towers stay on the OSM footprint and only use observed heights."""

from __future__ import annotations

from shapely import box

from onward_generator.detail.config import load_detail_config
from onward_generator.detail.landmarks import attach_towers, is_tower_poi, parse_observed_height_m
from tests.conftest import MURCIA_CENTER

CFG = load_detail_config()
# Local square around the Murcia test origin; the tower point is inside it.
FOOTPRINT = box(-20, -20, 20, 20)


def _poi(osm_id: int, x: float, y: float, tags: dict[str, str]) -> dict:
    return {"id": f"osm-node-{osm_id}", "kind": "poi", "tags": tags,
            "geometry": {"type": "Point", "coordinates": [x, y]}}


def test_height_and_tower_tags() -> None:
    assert parse_observed_height_m({"height": "93"}) == 93.0
    assert parse_observed_height_m({"height": "93 m"}) == 93.0
    assert parse_observed_height_m({"height": "tall"}) is None
    assert is_tower_poi({"man_made": "tower", "height": "93"})
    assert is_tower_poi({"historic": "tower", "bell_tower": "yes"})
    assert not is_tower_poi({"amenity": "place_of_worship"})


def test_observed_tower_is_clipped_to_the_building(tmp_path=None) -> None:
    features = [_poi(1, 0.0, 0.0, {"man_made": "tower", "height": "93", "name": "Torre"})]
    parts, issues = attach_towers("osm-way-cathedral", FOOTPRINT, features, CFG)
    assert issues == [] and len(parts) == 1
    part = parts[0]
    assert part.record["height"] == {"height_m": 93.0, "origin": "observed", "source_tag": "height",
                                    "source_id": "osm-node-1"}
    assert part.record["footprint"]["origin"] == "estimated"
    assert part.record["footprint"]["area_m2"] == 144.0  # 12 m × 12 m, fully inside
    zs = part.collider.vertices[:, 2]
    assert zs.min() == 0.0 and zs.max() == 93.0
    assert FOOTPRINT.covers(box(*part.collider.vertices[:, :2].min(axis=0), *part.collider.vertices[:, :2].max(axis=0)))


def test_tower_outside_the_footprint_is_ignored() -> None:
    features = [_poi(2, 50.0, 50.0, {"man_made": "tower", "height": "40"})]
    parts, issues = attach_towers("osm-way-cathedral", FOOTPRINT, features, CFG)
    assert parts == [] and issues == []


def test_missing_or_invalid_height_does_not_invent_a_volume() -> None:
    missing, issues_m = attach_towers("b", FOOTPRINT, [_poi(3, 0, 0, {"man_made": "tower"})], CFG)
    bad, issues_b = attach_towers("b", FOOTPRINT, [_poi(4, 0, 0, {"man_made": "tower", "height": "999"})], CFG)
    assert missing == [] and issues_m[0].code == "landmark_tower_height_missing"
    assert bad == [] and issues_b[0].code == "landmark_tower_height_out_of_range"


def test_origin_is_the_murcia_test_point() -> None:
    assert MURCIA_CENTER == (-1.12863, 37.98407)
