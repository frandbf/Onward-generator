"""Urban elements (phase 04): observed POIs, generated street lamps and street furniture.

Local coordinates are metres from MURCIA_CENTER (x east, y north, z up).
"""

from __future__ import annotations

import dataclasses
from types import MappingProxyType
from typing import Any

import numpy as np
import pytest
from shapely import LineString, box

from onward_generator.detail.config import DetailConfig, FurnitureItem, StreetFurniture, StreetLamps, load_detail_config
from onward_generator.detail.modules import Prototype, urban_prototypes
from onward_generator.detail.urban import Element, classify_poi, place_elements
from onward_generator.generation.surfaces import LinearWay

FREE = box(-200.0, -200.0, 200.0, 200.0)
FAR_BUILDING = box(500.0, 500.0, 510.0, 510.0)
PROTOS = urban_prototypes()


def _cfg(*, lamps: bool = False, furniture: tuple[FurnitureItem, ...] = ()) -> DetailConfig:
    base = load_detail_config()
    return dataclasses.replace(
        base,
        street_lamps=StreetLamps(lamps, 25.0, 0.5, 1.0, 8.0),
        street_furniture=StreetFurniture(bool(furniture), furniture),
    )


def _poi(fid: str, x: float, y: float, **tags: str) -> dict[str, Any]:
    return {"id": fid, "kind": "poi", "tags": tags, "geometry": {"type": "Point", "coordinates": [x, y]}}


def _xy(e: Element) -> tuple[float, float]:
    return float(e.matrix[0, 3]), float(e.matrix[1, 3])


def test_classify_poi_matches_listed_values_and_wildcards() -> None:
    cfg = _cfg()
    assert classify_poi({"amenity": "bench"}, cfg) == "bench"
    assert classify_poi({"natural": "tree", "name": "Ficus"}, cfg) == "tree"
    assert classify_poi({"amenity": "cafe"}, cfg) is None
    assert classify_poi({}, cfg) is None
    wild = dataclasses.replace(cfg, urban_observed=MappingProxyType({"post": MappingProxyType({"man_made": frozenset({"*"})})}))
    assert classify_poi({"man_made": "anything"}, wild) == "post"
    assert classify_poi({"amenity": "bench"}, wild) is None


def test_observed_poi_is_placed_with_provenance() -> None:
    features = [
        _poi("node/2", 10.0, 0.0, amenity="bench"),
        {"id": "way/9", "kind": "building", "tags": {"amenity": "bench"}, "geometry": {"type": "Point", "coordinates": [30.0, 0.0]}},
        _poi("node/3", 20.0, 0.0, amenity="cafe"),
    ]
    placed, rejected = place_elements(features, [], FREE, FAR_BUILDING, PROTOS, _cfg())
    assert rejected == []
    assert len(placed) == 1
    e = placed[0]
    assert (e.id, e.kind, e.origin, e.source_ids, e.rule) == ("urban/bench/node/2", "bench", "observed", ("node/2",), "osm_poi_position")
    assert _xy(e) == pytest.approx((10.0, 0.0))
    assert np.allclose(e.matrix[:3, :3], np.eye(3))  # observed furniture keeps yaw 0


def test_observed_poi_off_free_ground_or_overlapping_is_rejected() -> None:
    features = [
        _poi("node/1", 0.0, 0.0, amenity="bench"),
        _poi("node/2", 0.3, 0.0, amenity="bench"),  # overlaps node/1
        _poi("node/3", 199.95, 0.0, amenity="bench"),  # footprint leaves the free area
        _poi("node/4", 0.0, 50.0, amenity="waste_basket"),
    ]
    placed, rejected = place_elements(features, [], FREE, FAR_BUILDING, PROTOS, _cfg())
    assert [e.id for e in placed] == ["urban/bench/node/1", "urban/waste_basket/node/4"]
    assert [(r.id, r.kind, r.reason) for r in rejected] == [
        ("node/2", "bench", "overlaps_another_element"),
        ("node/3", "bench", "not_on_free_walkable_ground"),
    ]


def test_observed_poi_without_a_prototype_is_skipped() -> None:
    protos = {"bench": PROTOS["bench"]}
    placed, rejected = place_elements([_poi("node/1", 0.0, 0.0, natural="tree")], [], FREE, FAR_BUILDING, protos, _cfg())
    assert placed == [] and rejected == []


def test_observed_trees_pick_a_stable_canopy_and_yaw() -> None:
    protos = urban_prototypes(tree_variants=3)
    features = [_poi(f"node/{i}", 5.0 * i, 0.0, natural="tree") for i in range(1, 9)]
    first, _ = place_elements(features, [], FREE, FAR_BUILDING, protos, _cfg())
    again, _ = place_elements(list(reversed(features)), [], FREE, FAR_BUILDING, protos, _cfg())
    assert [(e.id, e.kind) for e in first] == [(e.id, e.kind) for e in again]
    assert all(np.allclose(a.matrix, b.matrix) for a, b in zip(first, again, strict=True))
    assert {e.kind for e in first} <= {"tree", "tree_1", "tree_2"}
    assert len({e.kind for e in first}) > 1
    assert any(not np.allclose(e.matrix[:3, :3], np.eye(3)) for e in first)
    assert all(e.id.startswith("urban/tree/") for e in first)


def test_generated_lamps_alternate_sides_behind_the_curb() -> None:
    road = LinearWay("way/1", "vehicular", LineString([(0.0, 0.0), (100.0, 0.0)]), 6.0, "observed")
    path = LinearWay("way/2", "pedestrian", LineString([(0.0, 50.0), (100.0, 50.0)]), 3.0, "observed")
    placed, rejected = place_elements([], [road, path], FREE, FAR_BUILDING, PROTOS, _cfg(lamps=True))
    assert rejected == []
    assert [e.id for e in placed] == [f"urban/street_lamp/way/1#lamp{i}" for i in range(4)]
    assert [_xy(e) for e in placed] == [
        pytest.approx((12.5, 3.5)), pytest.approx((37.5, -3.5)), pytest.approx((62.5, 3.5)), pytest.approx((87.5, -3.5)),
    ]
    assert all(e.origin == "generated" and e.source_ids == ("way/1",) for e in placed)
    assert placed[0].rule == "every 25.0 m, alternating sides, 0.5 m behind the curb"
    # The arm (local +x) reaches back over the road.
    assert placed[0].matrix[:2, 0] == pytest.approx((0.0, -1.0))
    assert placed[1].matrix[:2, 0] == pytest.approx((0.0, 1.0))


def test_generated_lamps_respect_buildings_and_observed_lamps() -> None:
    road = LinearWay("way/1", "vehicular", LineString([(0.0, 0.0), (50.0, 0.0)]), 6.0, "observed")
    building = box(10.0, 4.0, 15.0, 10.0)  # 0.5 m from lamp 0 at (12.5, 3.5)
    observed = [_poi("node/7", 40.0, -5.0, highway="street_lamp")]  # 2.7 m from lamp 1 at (37.5, -3.5)
    placed, rejected = place_elements(observed, [road], FREE, building, PROTOS, _cfg(lamps=True))
    assert [e.id for e in placed] == ["urban/street_lamp/node/7"]
    assert [(r.id, r.reason) for r in rejected] == [
        ("way/1#lamp0", "too_close_to_building"),
        ("way/1#lamp1", "too_close_to_another_lamp"),
    ]


def test_generated_lamps_need_the_option_and_a_prototype() -> None:
    road = LinearWay("way/1", "vehicular", LineString([(0.0, 0.0), (100.0, 0.0)]), 6.0, "observed")
    off, _ = place_elements([], [road], FREE, FAR_BUILDING, PROTOS, _cfg(lamps=False))
    assert off == []
    without = {k: v for k, v in PROTOS.items() if k != "street_lamp"}
    missing, _ = place_elements([], [road], FREE, FAR_BUILDING, without, _cfg(lamps=True))
    assert missing == []


def test_generated_lamp_off_free_ground_is_rejected() -> None:
    road = LinearWay("way/1", "vehicular", LineString([(0.0, 0.0), (30.0, 0.0)]), 6.0, "observed")
    free = box(-5.0, -2.0, 40.0, 2.0)  # only the carriageway
    placed, rejected = place_elements([], [road], free, FAR_BUILDING, PROTOS, _cfg(lamps=True))
    assert placed == []
    assert [(r.id, r.kind, r.reason) for r in rejected] == [("way/1#lamp0", "street_lamp", "not_on_free_walkable_ground")]


def test_generated_furniture_follows_pedestrian_ways() -> None:
    items = (FurnitureItem("bench", 28.0, 0.4, 80), FurnitureItem("waste_basket", 40.0, 0.6, 40))
    walk = LinearWay("way/3", "pedestrian", LineString([(0.0, 20.0), (60.0, 20.0)]), 4.0, "observed")
    short = LinearWay("way/4", "pedestrian", LineString([(0.0, -40.0), (7.9, -40.0)]), 4.0, "observed")
    road = LinearWay("way/5", "vehicular", LineString([(0.0, -80.0), (100.0, -80.0)]), 6.0, "observed")
    placed, rejected = place_elements([], [walk, short, road], FREE, FAR_BUILDING, PROTOS, _cfg(furniture=items))
    assert rejected == []
    assert [e.id for e in placed] == ["urban/bench/way/3#bench0", "urban/bench/way/3#bench1", "urban/waste_basket/way/3#waste_basket0"]
    xs = [_xy(e)[0] for e in placed]
    assert xs == pytest.approx([14.0, 42.0, 20.0])
    assert [abs(_xy(e)[1] - 20.0) for e in placed] == pytest.approx([2.4, 2.4, 2.6])
    assert all(e.origin == "generated" and e.source_ids == ("way/3",) for e in placed)
    assert placed[0].rule == "every 28.0 m along a pedestrian way, 0.4 m outside the way"
    assert placed[0].matrix[:2, 0] == pytest.approx((1.0, 0.0))  # the bench runs along the way


def test_generated_furniture_stops_at_max_count_and_skips_unknown_kinds() -> None:
    items = (FurnitureItem("bench", 10.0, 0.4, 2), FurnitureItem("kiosk", 10.0, 0.4, 5), FurnitureItem("waste_basket", 10.0, 0.6, 0))
    ways = [
        LinearWay("way/a", "pedestrian", LineString([(0.0, 20.0), (60.0, 20.0)]), 4.0, "observed"),
        LinearWay("way/b", "pedestrian", LineString([(0.0, 60.0), (60.0, 60.0)]), 4.0, "observed"),
    ]
    placed, _ = place_elements([], ways, FREE, FAR_BUILDING, PROTOS, _cfg(furniture=items))
    assert [e.id for e in placed] == ["urban/bench/way/a#bench0", "urban/bench/way/a#bench1"]


def test_generated_furniture_near_a_building_or_an_element_is_rejected() -> None:
    items = (FurnitureItem("bench", 28.0, 0.4, 80),)
    walk = LinearWay("way/3", "pedestrian", LineString([(0.0, 20.0), (60.0, 20.0)]), 4.0, "observed")
    wall = box(0.0, 22.5, 30.0, 30.0) | box(0.0, 10.0, 30.0, 17.5)  # bench 0 lands 0.1 m from either wall
    observed = [_poi("node/8", 42.0, 22.4, amenity="bench"), _poi("node/9", 42.0, 17.6, amenity="bench")]
    placed, rejected = place_elements(observed, [walk], FREE, wall, PROTOS, _cfg(furniture=items))
    assert [e.id for e in placed] == ["urban/bench/node/8", "urban/bench/node/9"]
    assert [(r.id, r.reason) for r in rejected] == [
        ("way/3#bench0", "too_close_to_building"),
        ("way/3#bench1", "overlaps_another_element"),
    ]


def test_slot_on_a_way_that_doubles_back_has_no_direction_and_is_skipped() -> None:
    """At the turning point of an out-and-back way the tangent is zero; the slot is skipped, nothing raises."""
    road = LinearWay("way/r", "vehicular", LineString([(0.0, 0.0), (12.5, 0.0), (0.0, 0.0)]), 6.0, "observed")
    walk = LinearWay("way/w", "pedestrian", LineString([(0.0, 30.0), (5.0, 30.0), (0.0, 30.0)]), 4.0, "observed")
    cfg = _cfg(lamps=True, furniture=(FurnitureItem("bench", 10.0, 0.4, 5),))
    placed, rejected = place_elements([], [road, walk], FREE, FAR_BUILDING, PROTOS, cfg)
    assert placed == [] and rejected == []


def test_prototype_without_collider_is_a_programming_error() -> None:
    protos = {"bench": Prototype("urban:bench", PROTOS["bench"].parts, None)}
    with pytest.raises(AssertionError):
        place_elements([_poi("node/1", 0.0, 0.0, amenity="bench")], [], FREE, FAR_BUILDING, protos, _cfg())


def test_generated_furniture_and_lamps_keep_off_the_ground_in_front_of_a_door_and_observed_ones_do_not() -> None:
    """Barcelona Sarria: a generated bench stood 0.47 m in front of a shop door, across its whole width."""
    items = (FurnitureItem("bench", 28.0, 0.4, 80),)
    walk = LinearWay("way/3", "pedestrian", LineString([(0.0, 20.0), (60.0, 20.0)]), 4.0, "observed")
    road = LinearWay("way/1", "vehicular", LineString([(0.0, -40.0), (30.0, -40.0)]), 6.0, "observed")
    doors = box(13.0, 21.5, 15.0, 23.0) | box(13.0, 17.0, 15.0, 18.5) | box(11.0, -37.5, 14.0, -36.0)  # bench 0 either side, lamp 0
    observed = [_poi("node/8", 14.0, 22.4, amenity="bench")]
    cfg = _cfg(lamps=True, furniture=items)
    placed, rejected = place_elements(observed, [walk, road], FREE, FAR_BUILDING, PROTOS, cfg, doors=doors)
    assert [e.id for e in placed] == ["urban/bench/node/8", "urban/bench/way/3#bench1"]
    assert [(r.id, r.reason) for r in rejected] == [("way/1#lamp0", "in_front_of_a_door"), ("way/3#bench0", "in_front_of_a_door")]
    alone, none = place_elements([], [walk, road], FREE, FAR_BUILDING, PROTOS, cfg)
    assert none == [] and len(alone) == 3


# ------------------------------------------------------------------------------------------------ street lamps (plan Q6)


def test_an_observed_lamp_on_a_facade_hangs_on_it_facing_out() -> None:
    """A lamp within 0.5 m of a facade becomes a wall lamp on the nearest facade point, local +x out of the wall."""
    building = box(0.0, 0.0, 20.0, 20.0)
    features = [_poi("node/1", 10.0, 20.3, highway="street_lamp"),   # 0.3 m north of the north wall
                _poi("node/2", 20.0, 10.0, highway="street_lamp"),   # exactly on the east wall
                _poi("node/3", 10.0, -0.49, highway="street_lamp")]  # 0.49 m south of the south wall
    placed, rejected = place_elements(features, [], FREE, building, PROTOS, _cfg())
    assert rejected == []
    assert [(e.id, e.kind, e.origin, e.rule) for e in placed] == [
        (f"urban/wall_lamp/node/{i}", "wall_lamp", "observed", "osm_poi_on_the_nearest_facade") for i in (1, 2, 3)]
    assert [_xy(e) for e in placed] == [pytest.approx((10.0, 20.0)), pytest.approx((20.0, 10.0)), pytest.approx((10.0, 0.0))]
    assert [tuple(e.matrix[:2, 0]) for e in placed] == [pytest.approx((0.0, 1.0)), pytest.approx((1.0, 0.0)), pytest.approx((0.0, -1.0))]


@pytest.mark.parametrize(("distance", "kind"), [(0.49, "wall_lamp"), (0.5, "street_lamp")])
def test_a_lamp_half_a_metre_from_a_facade_stands_on_its_own(distance: float, kind: str) -> None:
    building = box(0.0, -50.0, 10.0, 50.0)
    placed, _ = place_elements([_poi("node/1", 10.0 + distance, 0.0, highway="street_lamp")], [], FREE, building, PROTOS, _cfg())
    assert [e.kind for e in placed] == [kind]


@pytest.mark.parametrize("tags", [{"lamp_mount": "wall"}, {"support": "wall_mounted"}, {"support": "wall"}])
def test_a_lamp_tagged_on_a_wall_hangs_on_the_nearest_facade(tags: dict[str, str]) -> None:
    building = box(0.0, -50.0, 10.0, 50.0)
    placed, _ = place_elements([_poi("node/1", 14.0, 0.0, highway="street_lamp", **tags)], [], FREE, building, PROTOS, _cfg())
    assert [(e.kind, _xy(e)) for e in placed] == [("wall_lamp", pytest.approx((10.0, 0.0)))]
    other, _ = place_elements([_poi("node/1", 14.0, 0.0, highway="street_lamp", lamp_mount="straight_mast")], [], FREE, building, PROTOS, _cfg())
    assert [e.kind for e in other] == ["street_lamp"]


def test_without_a_wall_lamp_prototype_or_a_building_a_facade_lamp_is_a_pole() -> None:
    without = {k: v for k, v in PROTOS.items() if k != "wall_lamp"}
    building = box(0.0, -50.0, 10.0, 50.0)
    tagged = [_poi("node/1", 14.0, 0.0, highway="street_lamp", lamp_mount="wall")]
    assert [e.kind for e in place_elements(tagged, [], FREE, building, without, _cfg())[0]] == ["street_lamp"]
    from shapely.geometry import Polygon
    assert [e.kind for e in place_elements(tagged, [], FREE, Polygon(), PROTOS, _cfg())[0]] == ["street_lamp"]


def test_observed_lamps_keep_8_m_from_each_other_in_id_order() -> None:
    building = box(0.0, -50.0, 10.0, 50.0)
    features = [_poi("node/1", 30.0, 0.0, highway="street_lamp"), _poi("node/2", 37.99, 0.0, highway="street_lamp"),
                _poi("node/3", 38.0, 0.0, highway="street_lamp"), _poi("node/4", 10.2, 0.0, highway="street_lamp"),
                _poi("node/5", 10.2, 7.9, highway="street_lamp"), _poi("node/6", 10.2, 8.1, highway="street_lamp")]
    placed, rejected = place_elements(features, [], FREE, building, PROTOS, _cfg())
    assert [e.id for e in placed] == ["urban/street_lamp/node/1", "urban/street_lamp/node/3", "urban/wall_lamp/node/4", "urban/wall_lamp/node/6"]
    assert [(r.id, r.kind, r.reason) for r in rejected] == [
        ("node/2", "street_lamp", "too_close_to_another_lamp"), ("node/5", "wall_lamp", "too_close_to_another_lamp")]


def test_a_free_standing_observed_lamp_reaches_away_from_the_nearest_facade() -> None:
    building = box(0.0, -50.0, 10.0, 50.0)
    placed, _ = place_elements([_poi("node/1", 13.0, 0.0, highway="street_lamp"), _poi("node/2", -4.0, 20.0, highway="street_lamp")],
                               [], FREE, building, PROTOS, _cfg())
    assert [tuple(e.matrix[:2, 0]) for e in placed] == [pytest.approx((1.0, 0.0)), pytest.approx((-1.0, 0.0))]
    alone, _ = place_elements([_poi("node/1", 13.0, 0.0, highway="street_lamp")], [], FREE, box(0, 0, 0, 0).buffer(0), PROTOS, _cfg())
    assert np.allclose(alone[0].matrix[:3, :3], np.eye(3))  # no facade: yaw 0


def test_generated_lamps_keep_off_wall_lamps_too() -> None:
    road = LinearWay("way/1", "vehicular", LineString([(0.0, 0.0), (50.0, 0.0)]), 6.0, "observed")
    building = box(30.0, -20.0, 45.0, -10.0)
    observed = [_poi("node/7", 37.5, -10.2, highway="street_lamp")]  # on the facade, 6.7 m from lamp 1 at (37.5, -3.5)
    placed, rejected = place_elements(observed, [road], FREE, building, PROTOS, _cfg(lamps=True))
    assert ("way/1#lamp1", "too_close_to_another_lamp") in [(r.id, r.reason) for r in rejected]
    assert [e.kind for e in placed if e.origin == "observed"] == ["wall_lamp"]


def test_the_wall_lamp_prototype_hangs_above_head_height_without_a_collider() -> None:
    # Built here, not read from PROTOS: mutation testing maps a function to the tests that call it while they run.
    lamp = urban_prototypes()["wall_lamp"]
    vertices = np.vstack([mesh.vertices for _, mesh in lamp.parts])
    assert lamp.collider is None and lamp.key == "urban:wall_lamp"
    assert vertices[:, 2].min() == pytest.approx(3.1) and vertices[:, 0].min() == pytest.approx(0.0)
    assert vertices[:, 0].max() == pytest.approx(0.72) and lamp.triangle_count <= 40


@pytest.mark.parametrize(("rid", "reason", "severity"), [
    ("way/1#lamp0", "too_close_to_building", "info"),
    ("way/2#bench3", "not_on_free_walkable_ground", "info"),
    ("node/5", "too_close_to_another_lamp", "info"),
    ("node/6", "not_on_free_walkable_ground", "warning"),
    ("node/7", "overlaps_another_element", "warning"),
])
def test_only_an_observed_element_that_finds_no_place_is_a_warning(rid: str, reason: str, severity: str) -> None:
    from onward_generator.detail.scene import rejection_severity
    from onward_generator.detail.urban import Rejected

    assert rejection_severity(Rejected(rid, "street_lamp", reason)) == severity
