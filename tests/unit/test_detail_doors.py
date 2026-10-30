"""Every building gets a door: the fallback wall order, the door size, and the openings it replaces."""

from __future__ import annotations

from dataclasses import replace

import pytest
from shapely import box

from onward_generator.detail.config import Opening, load_detail_config
from onward_generator.detail.facades import Hole, Layout, Segment, Storeys, door_walls, ensure_door

CFG = load_detail_config()
TEMPLATE = CFG.templates["generic_residential"]
DOOR = CFG.main_door
STOREY = Storeys(3, 3.0, "heights_levels")


def _wall(length: float, index: int = 0, *, exposed: bool = True, ring: int = 0) -> Segment:
    """A wall along +x from the origin. The building lies at +y; outside is −y."""
    return Segment(0, ring, index, (0.0, 0.0), (length, 0.0), exposed)


def _window(segment: int, s0: float, v0: float = 0.9) -> Hole:
    return Hole("window", segment, s0, v0, 1.1, 1.5, 0.15, "glass_dark", 0, 0)


def _door(layout: Layout) -> Hole:
    return next(h for h in layout.holes if h.kind == "door")


@pytest.mark.parametrize("kind", ["door", "landmark_door", "shop_door", "interior_door"])
def test_a_layout_with_a_door_is_left_alone(kind: str) -> None:
    layout = Layout([Hole(kind, 0, 3.0, 0.0, 1.2, 2.4, 0.15, "door_wood", 1, 0), _window(0, 6.0)])
    before = list(layout.holes)
    assert ensure_door(layout, [_wall(10.0)], STOREY, "door", DOOR, TEMPLATE, box(0, 0, 10, 10), CFG) == "template"
    assert layout.holes == before


@pytest.mark.parametrize(
    ("length", "s0", "width"),
    [
        (8.0, 5.1, 1.2),  # two bays: the door takes the second one, like the template main door
        (12.0, 5.4, 1.2),  # three bays: the middle one
        (2.0, 0.5, 1.0),  # no full bay: the wall centre, narrowed to keep a pier on each side
    ],
)
def test_the_fallback_door_sits_in_the_centre_bay(length: float, s0: float, width: float) -> None:
    layout = Layout([_window(0, 0.6), Hole("balcony_door", 0, 0.6, 3.0, 1.0, 2.2, 0.15, "glass_dark", 0, 1)])
    rule = ensure_door(layout, [_wall(length)], STOREY, "door", DOOR, TEMPLATE, box(0, 0, length, 10), CFG)
    assert rule == "fallback"
    door = _door(layout)
    assert (door.segment, door.v0, door.back, door.depth) == (0, 0.0, "door_wood", CFG.recess_depth_m)
    assert door.s0 == pytest.approx(s0) and door.width == pytest.approx(width) and door.height == pytest.approx(2.4)
    assert layout.holes[-1] is door
    assert any(h.kind == "balcony_door" for h in layout.holes)


def test_a_door_near_the_wall_end_narrows_to_keep_its_pier() -> None:
    # 0.9 m bays on a 3 m wall: two bays, the door centre at 1.95 m is 1.05 m from the end.
    layout = Layout()
    narrow_bays = replace(TEMPLATE, bay_m=0.9)
    assert ensure_door(layout, [_wall(3.0)], STOREY, "door", DOOR, narrow_bays, box(0, 0, 3, 10), CFG) == "fallback"
    assert (_door(layout).s0, _door(layout).width) == pytest.approx((1.4, 1.1))


def test_the_door_recess_is_checked_where_the_door_is() -> None:
    # A notch in the footprint just left of the door (s 3.0–5.0) does not stop the door at s 5.1–6.3.
    notched = box(0, 0, 8, 10).difference(box(3.0, 0, 5.0, 0.5))
    layout = Layout()
    assert ensure_door(layout, [_wall(8.0)], STOREY, "door", DOOR, TEMPLATE, notched, CFG) == "fallback"
    assert _door(layout).s0 == pytest.approx(5.1)


def test_the_door_kind_is_the_callers() -> None:
    layout = Layout()
    assert ensure_door(layout, [_wall(10.0)], STOREY, "landmark_door", CFG.landmark_door, TEMPLATE, box(0, 0, 10, 10), CFG) == "fallback"
    assert [h.kind for h in layout.holes] == ["landmark_door"]


def test_door_height_follows_the_storey() -> None:
    low = Layout()
    ensure_door(low, [_wall(8.0)], Storeys(3, 8.0 / 3, "heights_levels"), "door", DOOR, TEMPLATE, box(0, 0, 8, 10), CFG)
    assert _door(low).height == pytest.approx(8.0 / 3 - CFG.head_clearance_m)
    cfg = replace(CFG, head_clearance_m=0.5)
    exact = Layout()
    assert ensure_door(exact, [_wall(8.0)], Storeys(1, 2.5, "heights_levels"), "door", DOOR, TEMPLATE, box(0, 0, 8, 10), cfg) == "fallback"
    assert _door(exact).height == 2.0
    too_low = Layout()
    assert ensure_door(too_low, [_wall(8.0)], Storeys(1, 2.49, "heights_levels"), "door", DOOR, TEMPLATE, box(0, 0, 8, 10), cfg) == "none"
    assert too_low.holes == []


def test_a_wall_too_narrow_for_the_smallest_door_is_skipped() -> None:
    narrow, wide = _wall(1.8, 0), _wall(6.0, 1)
    layout = Layout()
    assert ensure_door(layout, [narrow], STOREY, "door", DOOR, TEMPLATE, box(0, 0, 1.8, 10), CFG) == "none"
    assert ensure_door(layout, [narrow, wide], STOREY, "door", DOOR, TEMPLATE, box(0, 0, 6, 10), CFG) == "fallback"
    assert _door(layout).segment == 1
    # A 0.9 m template door is exactly the smallest door and still fits.
    smallest = Layout()
    assert ensure_door(smallest, [_wall(6.0)], STOREY, "door", Opening(0.9, 2.4), TEMPLATE, box(0, 0, 6, 10), CFG) == "fallback"
    assert _door(smallest).width == 0.9


def test_a_door_whose_recess_leaves_the_footprint_moves_on() -> None:
    # The footprint starts 0.1 m inside the first wall, so a 0.15 m recess would leave it.
    first, second = _wall(8.0, 0), Segment(0, 0, 1, (0.0, 0.0), (0.0, 8.0), True)
    shifted = box(0, 0.1, 8, 10)
    assert ensure_door(Layout(), [first], STOREY, "door", DOOR, TEMPLATE, shifted, CFG) == "none"
    # The second wall runs up the x = 0 side with the building to its left (−x) side.
    layout = Layout()
    assert ensure_door(layout, [first, second], STOREY, "door", DOOR, TEMPLATE, box(-10, 0, 0, 8).union(shifted), CFG) == "fallback"
    assert _door(layout).segment == 1


def test_the_door_replaces_only_the_ground_openings_beside_it() -> None:
    # Door at s 5.1–6.3 on an 8 m wall; a 0.5 m pier on each side clears s 4.6–6.8.
    right, past_right = _window(0, 6.7), _window(0, 6.9)
    left, past_left = _window(0, 3.6), _window(0, 3.4)
    upper, other_wall = _window(0, 5.0, v0=3.9), _window(1, 5.0)
    layout = Layout([right, past_right, left, past_left, upper, other_wall])
    assert ensure_door(layout, [_wall(8.0)], STOREY, "door", DOOR, TEMPLATE, box(0, 0, 8, 10), CFG) == "fallback"
    assert layout.holes == [past_right, past_left, upper, other_wall, _door(layout)]


def test_no_wall_means_no_door() -> None:
    assert ensure_door(Layout(), [], STOREY, "door", DOOR, TEMPLATE, box(0, 0, 8, 10), CFG) == "none"


def test_door_walls_put_the_longest_street_wall_first_and_skip_party_walls() -> None:
    # A 10 × 12 building: x = 10 is a party wall, the other three are street-facing.
    south = Segment(0, 0, 0, (0.0, 0.0), (10.0, 0.0), True)
    party = Segment(0, 0, 1, (10.0, 0.0), (10.0, 12.0), False)
    north = Segment(0, 0, 2, (10.0, 12.0), (0.0, 12.0), True)
    west = Segment(0, 0, 3, (0.0, 12.0), (0.0, 0.0), True)
    court = Segment(0, 1, 4, (2.0, 2.0), (2.0, 9.0), True)
    assert door_walls([south, party, north, west, court], box(10, 0, 20, 12), CFG) == [west, south, north]
    # Street-facing walls come before blind ones, however long.
    short_street = Segment(0, 0, 5, (0.0, 0.0), (3.0, 0.0), True)
    long_blind = Segment(0, 0, 6, (0.0, 0.0), (9.0, 0.0), False)
    assert door_walls([long_blind, short_street], box(50, 50, 60, 60), CFG) == [short_street, long_blind]


def test_door_walls_fall_back_to_short_walls_that_touch_no_neighbour() -> None:
    # A 2 m wide building between two others: its 2 m front and back are too short to be
    # street-facing, but nothing stands outside them. Its sides are party walls.
    front = Segment(0, 0, 0, (0.0, 0.0), (2.0, 0.0), False)
    east = Segment(0, 0, 1, (2.0, 0.0), (2.0, 12.0), False)
    back = Segment(0, 0, 2, (2.0, 12.0), (0.0, 12.0), False)
    west = Segment(0, 0, 3, (0.0, 12.0), (0.0, 0.0), False)
    others = box(-10, 0, 0, 12).union(box(2, 0, 12, 12))
    assert door_walls([back, west, front, east], others, CFG) == [front, back]
    # A wall with a neighbour against its first 0.8 m only is still a party wall.
    assert door_walls([front], box(-1, -1, 0.8, 0), CFG) == []
    # A neighbour that only meets the wall's corner leaves it free.
    assert door_walls([front], box(-1, -1, 0, 0), CFG) == [front]


def test_a_shop_without_room_for_its_door_gets_a_low_glazed_one() -> None:
    low = Storeys(3, 8 / 3, "heights_levels")
    layout = Layout([Hole("shopfront", 0, 0.85, 0.25, 3.1, 1.97, 0.15, "glass_dark", 0, 0)])
    rule = ensure_door(layout, [_wall(12.0)], low, "shop_door", CFG.shop_door, TEMPLATE, box(0, 0, 12, 10), CFG)
    assert rule == "fallback"
    door = next(h for h in layout.holes if h.kind == "shop_door")
    assert (door.back, door.width) == ("glass_dark", CFG.shop_door.width_m)
    assert door.height == pytest.approx(8 / 3 - CFG.head_clearance_m)
