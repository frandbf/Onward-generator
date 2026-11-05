"""Where ladders and lifts go: which buildings, which wall, and what they must stay clear of (ADR-029)."""

from __future__ import annotations

import pytest
from shapely.geometry import MultiPolygon, Point, box
from shapely.geometry.polygon import Polygon

from onward_generator.perf.climbs import (
    ACCESS_SPACING_M,
    LADDERS_MAX,
    LIFTS_MAX,
    ROOF_MIN_M2,
    SLIDE_STEP_M,
    SPAWN_CLEAR_M,
    _slid,
    _strip,
    _walls,
    _yaw,
    climbed_roofs,
    deck,
    ladder_on,
    lift_on,
    place_climbs,
    storeys,
)

FREE = box(-500, -500, 500, 500)
NOTHING = Point(9999, 9999).buffer(0.1)
ATTACK = (-400.0, 0.0)
DEFEND = (400.0, 0.0)


def _building(source: str, storey_count: int, *, roof: str = "flat_parapet", interior: bool = False, **height: object) -> dict:
    return {
        "id": f"building/{source}", "type": "building", "source_ids": [source], "interior": interior,
        "height": {"height_m": storey_count * 3.0, "estimated_levels": storey_count, **height},
        "roof": {"type": roof},
    }


def _place(objects: list[dict], footprints: dict, *, free: object = None, keep_out: object = NOTHING) -> dict:
    ground = free if free is not None else FREE.difference(MultiPolygon([f for f in footprints.values() if isinstance(f, Polygon)]))
    return place_climbs({"objects": objects}, footprints, ground, keep_out, [ATTACK], [DEFEND])


@pytest.mark.parametrize(("height", "expected"), [({"height_m": 12.0, "estimated_levels": 5}, 5), ({"height_m": 9.0}, 3), ({"height_m": 1.0}, 1), ({"height_m": 10.4, "estimated_levels": 0}, 3), ({"height_m": 22.9, "estimated_levels": 3}, 8)])
def test_storeys_are_the_levels_or_what_the_height_implies_when_more(height: dict, expected: int) -> None:
    assert storeys({"height": height}) == expected


def test_a_ladder_carries_the_parapet_of_its_roof() -> None:
    walled = {**_building("b", 2), "roof": {"type": "flat_parapet", "parapet_m": 0.7}}
    assert _place([walled], {"b": box(0, 0, 20, 10)})["ladders"][0]["parapet_m"] == 0.7
    bare = {**_building("b", 2), "roof": {"type": "flat"}}
    assert _place([bare], {"b": box(0, 0, 20, 10)})["ladders"][0]["parapet_m"] == 0.0


def test_a_ladder_stands_at_the_middle_of_the_longest_wall_and_faces_it() -> None:
    site = ladder_on(box(0, 0, 20, 10), FREE.difference(box(0, 0, 20, 10)), NOTHING, 9.0)
    assert site == {"x": 10.0, "y": -0.15, "yaw": 0.0, "height_m": 9.0, "parapet_m": 0.0, "roof_x": 10.0, "roof_y": 0.75}


def test_a_ladder_moves_to_the_next_wall_when_the_ground_is_taken() -> None:
    footprint = box(0, 0, 20, 10)
    free = box(-30, 0, 50, 40).difference(footprint)  # nothing south of the building
    site = ladder_on(footprint, free, NOTHING, 9.0)
    assert site is not None and (site["x"], site["y"], abs(site["yaw"])) == (10.0, 10.15, 180.0)
    # Something kept clear at the middle of the wall: the ladder slides along it, 0.5 m at a time, east first.
    slid = ladder_on(footprint, free, Point(10, 11).buffer(1.0), 9.0)
    assert (slid["x"], slid["y"]) == (8.0, 10.15)  # the north wall runs west, so +0.5 m along it is -0.5 m in x
    assert ladder_on(footprint, box(100, 100, 110, 110), NOTHING, 9.0) is None


def test_a_ladder_needs_a_roof_behind_it() -> None:
    sliver = box(0, 0, 20, 1.0)
    assert ladder_on(sliver, FREE.difference(sliver), NOTHING, 9.0) is None


def test_a_ladder_lands_on_the_roof_inside_its_parapet() -> None:
    """Barcelona Sarria: the side parapets of a narrow roof left 1.45 m for the 1.6 m ladder."""
    narrow = box(0, 0, 20, 1.7)  # 1.7 m of roof: 1.2 m inside a 0.25 m parapet, less than the 1.5 m landing
    assert ladder_on(narrow, FREE.difference(narrow), NOTHING, 9.0) is not None
    assert ladder_on(narrow, FREE.difference(narrow), NOTHING, 9.0, 0.7, 0.25) is None
    footprint = box(0, 0, 20, 10)
    site = ladder_on(footprint, FREE.difference(footprint), NOTHING, 9.0, 0.7, 0.25)
    assert site is not None and (site["y"], site["roof_y"]) == (-0.15, 1.0)  # the roof point is the middle of the landing


def test_a_lift_cabin_and_its_apron_stand_on_the_roof_inside_its_parapet() -> None:
    """Barcelona Sarria: the apron of a roof cabin ran 0.26 m into the far parapet."""
    shallow = box(0, 0, 5.9, 5.9)  # 0.6 + 3.2 + 2.0 = 5.8 m of roof needed from any wall, 5.65 m inside a 0.25 m parapet
    assert lift_on(shallow, FREE.difference(shallow), NOTHING, 15.0) is not None
    assert lift_on(shallow, FREE.difference(shallow), NOTHING, 15.0, 0.25) is None


@pytest.mark.parametrize(("thickness", "kept"), [(0.0, True), (0.25, False)])
def test_a_roof_needs_30_square_metres_inside_its_parapet(thickness: float, kept: bool) -> None:
    """Barcelona Sarria: ladders led to roofs of 4.3 to 17.7 m2 under taller walls, dead ends for a player."""
    roof = {**_building("b", 2), "roof": {"type": "flat_parapet", "parapet_m": 0.7, "parapet_thickness_m": thickness}}
    placed = _place([roof], {"b": box(0, 0, 6, 5)})  # 30 m2 bare, 24.75 m2 inside a 0.25 m parapet
    assert bool(placed["ladders"]) is kept
    assert ROOF_MIN_M2 == 30.0


def test_a_lift_has_a_cabin_in_the_street_and_one_on_the_roof_with_opposite_doors() -> None:
    footprint = box(0, 0, 20, 10)
    site = lift_on(footprint, FREE.difference(footprint), NOTHING, 15.0)
    assert site == {"x": 10.0, "y": -1.65, "yaw": 180.0, "height_m": 15.0, "roof_x": 10.0, "roof_y": 2.2, "roof_yaw": 0.0}


def test_a_lift_keeps_a_clear_apron_in_front_of_the_street_door() -> None:
    footprint = box(0, 0, 20, 10)
    assert lift_on(footprint, box(-30, -6.3, 50, 0), NOTHING, 15.0)["y"] == -1.65
    assert lift_on(footprint, box(-30, -6.2, 50, 0), NOTHING, 15.0) is None


def test_a_lift_needs_room_on_the_roof_and_stays_out_of_what_is_kept_clear() -> None:
    small = box(0, 0, 5, 5)
    assert lift_on(small, FREE.difference(small), NOTHING, 15.0) is None
    footprint = box(0, 0, 20, 10)
    moved = lift_on(footprint, FREE.difference(footprint), Point(10, -5).buffer(0.5), 15.0)
    assert moved["y"] == -1.65 and abs(moved["x"] - 10.0) == 2.5  # slid along the same wall past the kept-clear point


def test_the_rules_choose_ladders_up_to_3_storeys_and_lifts_from_3_to_6() -> None:
    far = 35.0
    objects = [_building(f"b{n}", n) for n in (1, 3, 6, 7)]
    footprints = {f"b{n}": box(k * far * 2, 100, k * far * 2 + 20, 110) for k, n in enumerate((1, 3, 6, 7))}
    placed = _place(objects, footprints)
    centre = {site["x"] for site in placed["ladders"]}
    assert centre == {10.0, 80.0}  # the 1 and the 3
    assert [site["x"] for site in placed["lifts"]] == [150.0]  # the 6; the 3 is taken, the 7 is too high


@pytest.mark.parametrize(
    "building",
    [
        _building("b", 2, roof="hip"),
        _building("b", 2, interior=True),
        _building("b", 2, landmark=True),
        {**_building("b", 2), "tier": "scenic"},
    ],
)
def test_a_building_a_ladder_cannot_use_is_skipped(building: dict) -> None:
    assert _place([building], {"b": box(0, 100, 20, 110)}) == {"ladders": [], "lifts": []}


def test_a_building_with_an_interior_can_have_a_lift_but_not_with_a_stair_to_the_roof() -> None:
    footprints = {"b": box(0, 100, 20, 120)}
    lifted = _building("b", 4, interior=True)
    assert len(_place([lifted], footprints)["lifts"]) == 1
    stair = {"type": "interior", "source_ids": ["b"], "roof_access": True}
    assert _place([lifted, stair], footprints)["lifts"] == []
    no_stair = {"type": "interior", "source_ids": ["b"], "roof_access": False}
    assert len(_place([lifted, no_stair], footprints)["lifts"]) == 1


def test_a_building_with_no_footprint_or_part_is_ignored_and_a_multipolygon_uses_its_largest() -> None:
    assert _place([_building("gone", 2), {"type": "urban_element"}], {}) == {"ladders": [], "lifts": []}
    pieces = MultiPolygon([box(0, 100, 2, 102), box(50, 100, 70, 110)])
    placed = _place([_building("b", 2)], {"b": pieces}, free=FREE.difference(pieces))
    assert placed["ladders"][0]["x"] == 60.0


def test_no_access_goes_near_a_spawn() -> None:
    edge = ATTACK[0] + SPAWN_CLEAR_M
    assert _place([_building("b", 2)], {"b": box(edge - 0.01, 0, edge + 20, 20)})["ladders"] == []
    assert len(_place([_building("b", 2)], {"b": box(edge, 0, edge + 20, 20)})["ladders"]) == 1
    edge = DEFEND[0] - SPAWN_CLEAR_M
    assert _place([_building("b", 2)], {"b": box(edge - 20, 0, edge + 0.01, 20)})["ladders"] == []


def test_the_building_closest_to_equidistant_goes_first_then_the_lowest() -> None:
    objects = [_building("side", 1), _building("low", 2), _building("high", 3)]
    footprints = {"side": box(200, 0, 220, 10), "low": box(0, 200, 20, 210), "high": box(0, 0, 20, 10)}
    placed = place_climbs({"objects": objects}, footprints, FREE.difference(MultiPolygon(list(footprints.values()))), NOTHING, [ATTACK], [DEFEND])
    assert [(s["x"], s["height_m"]) for s in placed["ladders"]] == [(10.0, 6.0), (10.0, 9.0), (210.0, 3.0)]


def test_fairness_counts_the_nearest_spawn_of_each_team() -> None:
    objects = [_building("a", 2), _building("b", 2)]
    footprints = {"a": box(100, 0, 120, 10), "b": box(0, 300, 20, 310)}
    placed = place_climbs({"objects": objects}, footprints, FREE.difference(MultiPolygon(list(footprints.values()))), NOTHING,
                          [ATTACK, (110.0, -280.0)], [DEFEND])
    assert placed["ladders"][0]["x"] == 110.0  # alone, ATTACK leaves b fairer; the second attacker spawn turns it


def test_accesses_keep_their_spacing_across_ladders_and_lifts() -> None:
    gap = ACCESS_SPACING_M - 0.5
    footprints = {"a": box(0, 100, 20, 120), "b": box(gap, 100, gap + 20, 120), "c": box(0, 100 + gap + 20, 20, 140 + gap)}
    objects = [_building("a", 2), _building("b", 2), _building("c", 4)]
    placed = _place(objects, footprints)
    assert len(placed["ladders"]) == 1
    assert len(placed["lifts"]) == 1


def test_the_caps_hold() -> None:
    step = 60.0
    ladders = {f"l{k}": box(k * step, 100, k * step + 20, 120) for k in range(LADDERS_MAX + 2)}
    lifts = {f"h{k}": box(k * step, 200, k * step + 20, 220) for k in range(LIFTS_MAX + 2)}
    objects = [_building(n, 2) for n in ladders] + [_building(n, 4) for n in lifts]
    placed = _place(objects, {**ladders, **lifts})
    assert (len(placed["ladders"]), len(placed["lifts"])) == (LADDERS_MAX, LIFTS_MAX)


def test_climbed_roofs_lists_both_kinds() -> None:
    climbs = {"ladders": [{"roof_x": 1.0, "roof_y": 2.0, "height_m": 3.0}], "lifts": [{"roof_x": 4.0, "roof_y": 5.0, "height_m": 6.0}]}
    assert climbed_roofs(climbs) == [(1.0, 2.0, 3.0), (4.0, 5.0, 6.0)]


def test_a_strip_is_exactly_the_rectangle_asked_for_even_on_a_turned_wall() -> None:
    assert _strip((10.0, 0.0, 0.0, -1.0, 20.0), 4.0, 1.0, 3.0).symmetric_difference(box(8, -3, 12, -1)).area < 1e-9
    turned = _strip((0.0, 0.0, 0.6, 0.8, 20.0), 2.0, 1.0, 3.0)
    expected = Polygon([(0.6 + 0.8, 0.8 - 0.6), (0.6 - 0.8, 0.8 + 0.6), (1.8 - 0.8, 2.4 + 0.6), (1.8 + 0.8, 2.4 - 0.6)])
    assert turned.is_valid and turned.symmetric_difference(expected).area < 1e-9


def test_walls_are_longest_first_then_west_then_south_and_short_ones_count() -> None:
    assert [wall[:2] for wall in _walls(box(0, 0, 10, 10))] == [(0.0, 5.0), (5.0, 0.0), (5.0, 10.0), (10.0, 5.0)]
    assert [wall[:2] for wall in _walls(box(0, 0, 0.5, 10))] == [(0.0, 5.0), (0.5, 5.0), (0.25, 0.0), (0.25, 10.0)]
    assert len(_walls(Polygon([(0, 0), (10, 0), (10, 0), (10, 10), (0, 10)]))) == 4


def test_walls_face_outward_whichever_way_the_ring_turns() -> None:
    clockwise = Polygon([(0, 0), (0, 10), (20, 10), (20, 0)])
    assert _walls(clockwise)[0] == (10.0, 0.0, 0.0, -1.0, 20.0)


def test_yaw_is_rounded_to_a_tenth_of_a_degree() -> None:
    assert _yaw(0.31, 0.7) == 23.9


def test_sites_are_rounded_and_face_the_right_way_on_a_side_wall() -> None:
    footprint = box(0.123, 0.456, 10.789, 30.321)
    ladder = ladder_on(footprint, FREE.difference(footprint), NOTHING, 9.1234)
    assert ladder == {"x": -0.03, "y": 15.39, "yaw": 90.0, "height_m": 9.12, "parapet_m": 0.0, "roof_x": 0.87, "roof_y": 15.39}
    lift = lift_on(footprint, FREE.difference(footprint), NOTHING, 15.1234)
    assert lift == {"x": -1.53, "y": 15.39, "yaw": -90.0, "height_m": 15.12, "roof_x": 2.32, "roof_y": 15.39, "roof_yaw": 90.0}


def test_the_ground_outside_is_never_taken_for_roof() -> None:
    footprint = box(0, 0, 20, 1.48)
    assert ladder_on(footprint, FREE.difference(footprint), NOTHING, 9.0) is None
    deep = box(0, 0, 20, 5.78)
    south = box(-30, -7, 50, 0)  # only the long south wall has ground, so only its roof strip can be used
    assert lift_on(deep, south, NOTHING, 15.0) is None
    assert lift_on(box(0, 0, 20, 5.81), south, NOTHING, 15.0) is not None


def test_one_skipped_row_does_not_end_the_search() -> None:
    footprints = {"b": box(0, 100, 20, 110), "s": box(100, 100, 120, 110), "l": box(200, 100, 220, 110)}
    valid = _building("b", 2)
    for skipped in ({"type": "urban_element"}, {**_building("s", 2), "tier": "scenic"}, _building("gone", 2), {**_building("l", 2), "height": {"height_m": 6.0, "landmark": True}}):
        assert len(_place([skipped, valid], footprints)["ladders"]) == 1
    assert place_climbs({}, {}, FREE, NOTHING, [ATTACK], [DEFEND]) == {"ladders": [], "lifts": []}


def test_a_3_storey_building_with_an_interior_gets_a_lift() -> None:
    placed = _place([_building("b", 3, interior=True)], {"b": box(0, 100, 20, 110)})
    assert (placed["ladders"], len(placed["lifts"])) == ([], 1)


def test_a_building_with_a_ladder_gets_no_lift_even_on_a_far_wall() -> None:
    footprint = box(0, 100, 60, 135)
    free = box(-50, 97, 110, 100).union(box(-50, 135, 110, 200))
    placed = _place([_building("b", 3)], {"b": footprint}, free=free)
    assert (len(placed["ladders"]), placed["lifts"]) == (1, [])


def test_differences_inside_the_balance_step_go_to_the_lower_roof() -> None:
    objects = [_building("lean", 2), _building("fair", 3)]
    footprints = {"lean": box(-8, 100, 12, 110), "fair": box(-10, 300, 10, 310)}
    placed = _place(objects, footprints)
    assert [site["height_m"] for site in placed["ladders"]] == [6.0, 9.0]


def test_accesses_exactly_at_the_spacing_are_allowed_and_closer_lifts_are_not() -> None:
    footprints = {"a": box(0, 100, 20, 110), "b": box(ACCESS_SPACING_M, 100, ACCESS_SPACING_M + 20, 110)}
    assert len(_place([_building("a", 2), _building("b", 2)], footprints)["ladders"]) == 2
    close = {"a": box(0, 100, 20, 110), "b": box(25, 100, 45, 110)}
    assert len(_place([_building("a", 4), _building("b", 4)], close)["lifts"]) == 1


def test_a_tie_goes_to_the_lower_id() -> None:
    footprints = {"z": box(-30, 100, -10, 110), "a": box(10, 100, 30, 110)}
    placed = _place([_building("z", 2), _building("a", 2)], footprints)
    assert [site["x"] for site in placed["ladders"]] == [20.0, -20.0]


def test_a_tall_roof_with_few_cadastral_levels_gets_neither_a_ladder_nor_a_lift() -> None:
    tall = {**_building("b", 3), "height": {"height_m": 22.9, "estimated_levels": 3}}
    assert _place([tall], {"b": box(0, 100, 20, 110)}) == {"ladders": [], "lifts": []}


def _cored(objects: list[dict], footprints: dict, envelope: dict | None = None) -> dict:
    """``place_climbs`` on a map with a 100 m core (the square from -100 to 100)."""
    manifest: dict = {"objects": objects, "core": {"half_side_m": 100.0}}
    if envelope is not None:
        manifest["envelope"] = envelope
    ground = FREE.difference(MultiPolygon([f for f in footprints.values() if isinstance(f, Polygon)]))
    return place_climbs(manifest, footprints, ground, NOTHING, [ATTACK], [DEFEND])


def _count(placed: dict) -> int:
    return len(placed["ladders"]) + len(placed["lifts"])


@pytest.mark.parametrize(
    ("footprint", "count"),
    [
        (box(0, 70, 20, 85), 1),
        (box(0, 90, 20, 105), 0),  # crosses the core edge: its roof runs past the wall
        (box(0, 85, 20, 99.7), 0),  # inside the core, but its wall side under the 0.4 m wall
    ],
)
def test_a_climb_stays_on_a_roof_inside_the_wall(footprint: Polygon, count: int) -> None:
    assert _count(_cored([_building("b", 2)], {"b": footprint})) == count


def test_the_wall_thickness_comes_from_the_envelope() -> None:
    assert _count(_cored([_building("b", 2)], {"b": box(0, 85, 20, 99.7)}, {"thickness_m": 0.2})) == 1


@pytest.mark.parametrize(
    ("north", "envelope", "count"),
    [
        (94.0, None, 1),  # 6 m from the edge: no jump over the wall
        (94.5, None, 0),  # 5.5 m: a 15 m roof over the 12 m wall is too close
        (94.5, {"height_m": 15.0}, 1),  # a 15 m wall: the roof is not above it
        (94.5, {"height_m": 14.9}, 0),
    ],
)
def test_a_roof_above_the_wall_keeps_clear_of_the_edge(north: float, envelope: dict | None, count: int) -> None:
    assert _count(_cored([_building("b", 5)], {"b": box(0, 70, 20, north)}, envelope)) == count


def test_a_roof_no_higher_than_the_wall_may_stand_next_to_it() -> None:
    assert _count(_cored([_building("b", 4)], {"b": box(0, 75, 20, 99.5)})) == 1


def test_a_refused_roof_does_not_end_the_search() -> None:
    objects = [_building("a", 2), _building("b", 2)]
    footprints = {"a": box(0, 90, 20, 105), "b": box(-60, 0, -40, 15)}
    placed = _cored(objects, footprints)
    assert [site["x"] for site in placed["ladders"]] == [-50.0]


def test_the_building_record_gives_each_access_its_parapet_thickness() -> None:
    def walled(source: str, storey_count: int, thickness: float) -> dict:
        return {**_building(source, storey_count), "roof": {"type": "flat_parapet", "parapet_m": 0.7, "parapet_thickness_m": thickness}}

    ladder = _place([walled("b", 2, 0.25)], {"b": box(0, 0, 20, 10)})["ladders"]
    assert [site["roof_y"] for site in ladder] == [1.0]
    street = box(-30, -10, 50, 0)  # only the south wall has its street, and 5.9 m of roof behind it
    assert _place([walled("c", 4, 0.0)], {"c": box(0, 0, 20, 5.9)}, free=street)["lifts"]
    assert _place([walled("c", 4, 0.25)], {"c": box(0, 0, 20, 5.9)}, free=street)["lifts"] == []


def test_the_deck_is_the_inner_edge_detail_gives_the_parapet_sharp_corners_and_all() -> None:
    """``build_shell`` cuts the parapet with a mitred inward offset, limit 2: a reflex corner stays sharp, a needle is bevelled."""
    ell = box(0, 0, 10, 10).difference(box(5, 5, 10, 10))
    assert deck(ell, 1.0).area == pytest.approx(8 * 8 - 5 * 5)  # sharp at the reflex corner, not rounded
    wedge = Polygon([(0, 0), (20, 0), (20, 25.5), (10, 3), (0, 25.5)])  # a notch of 24 degrees each side: its mitre is 2.5 times the offset
    assert deck(wedge, 0.5).equals(wedge.buffer(-0.5, join_style="mitre", mitre_limit=2.0))
    assert not deck(wedge, 0.5).equals(wedge.buffer(-0.5, join_style="mitre", mitre_limit=3.0))


def test_a_wall_slides_from_its_middle_outward_and_keeps_the_strip_on_it() -> None:
    wall = (10.0, 0.0, 0.0, -1.0, 4.0)  # the south wall of a box, 8 to 12 m in x, running east
    assert [w[0] for w in _slid(wall, 1.6)] == [10.0, 10.5, 9.5, 11.0, 9.0]  # 1.2 m each side of the middle: two steps
    assert all(w[1:] == wall[1:] for w in _slid(wall, 1.6))
    assert [w[0] for w in _slid(wall, 3.2)] == [10.0]  # 0.4 m each side: no full step
    assert [w[0] for w in _slid(wall, 5.0)] == [10.0]  # wider than the wall: the middle only, as before
    assert [w[0] for w in _slid((10.0, 0.0, 0.0, -1.0, 2.6), 1.6)] == [10.0, 10.5, 9.5]  # exactly one step
    east = (20.0, 5.0, 1.0, 0.0, 10.0)  # the east wall of a box, running north: along it is +y
    assert [w[:2] for w in _slid(east, 9.2)] == [(20.0, 5.0)] and [w[:2] for w in _slid(east, 9.0)] == [(20.0, 5.0), (20.0, 5.5), (20.0, 4.5)]
    assert SLIDE_STEP_M == 0.5
