"""Shop rooms: one room per shop along the frontage, a room per footprint part with a shopfront, and courtyards.

Local metres. The fixture origin is MURCIA_CENTER. The test shop is 24 × 18 m on a street wall
along y = 0, with six 4 m bays (centres x = 2, 6, …, 22) and the main door in bay 2 (x = 10).
"""

from __future__ import annotations

import copy
import json
from dataclasses import replace

import numpy as np
import pytest
from shapely import MultiPolygon, Point, Polygon, box

from onward_generator.detail.config import DEFAULT_CONFIG_PATH, config_from_document, load_detail_config
from onward_generator.detail.facades import Hole, Layout, Segment
from onward_generator.detail.interiors import (
    GLASS_VIEW,
    PARTITION_M,
    RIM_TOLERANCE_M,
    SHOP_GLASS,
    InteriorPlan,
    _inner_walls,
    _inset,
    _rim_on,
    build_interior,
    glass_penetrations,
    light_anchors,
    pierce_room_windows,
    plan_interior,
    plan_shop_rooms,
    room_window_cuts,
)
from onward_generator.detail.shops import Shop, plan_shops, shop_bays
from tests.conftest import MURCIA_CENTER
from tests.unit.test_interiors import _area, _inside

CFG = load_detail_config()
DOC = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
T = CFG.interiors.wall_thickness_m
SEG = Segment(0, 0, 0, (0.0, 0.0), (24.0, 0.0), True)
FOOTPRINT = box(0.0, 0.0, 24.0, 18.0)
DOOR = Hole("interior_door", 0, 9.4, 0.0, 1.2, 2.4, T, None, 2, 0)
BAKERY, PHARMACY, BAR, BOOKS = {"shop": "bakery"}, {"amenity": "pharmacy"}, {"amenity": "bar"}, {"shop": "books"}


def _front(column: int, segment: int = 0, centre: float | None = None) -> Hole:
    c = 2.0 + 4.0 * column if centre is None else centre
    return Hole("shopfront", segment, c - 1.5, 0.25, 3.0, 2.3, 0.15, "glass_dark", column, 0)


def _shop(column: int, tags: dict[str, str]) -> Shop:
    return (f"poi-{column}", tags, (2.0 + 4.0 * column, 1.0))


def _plan(footprint=FOOTPRINT, cfg=CFG) -> InteriorPlan:
    plan = plan_interior("b", footprint, DOOR, SEG, 3.0, 3.0, 1, cfg, roof_access=False, max_floors=1, several_parts=True)
    assert not isinstance(plan, str), plan
    return plan


def _rooms(columns: tuple[int, ...], shops: list[Shop], footprint=FOOTPRINT, segments=(SEG,), extra=(), cfg=CFG):
    layout = Layout(holes=[DOOR, *(_front(c) for c in columns), *extra])
    return plan_shop_rooms(_plan(footprint, cfg), footprint, layout, list(segments), shops, cfg), layout


def _door(centre: float, column: int, segment: int = 0) -> Hole:
    return Hole("interior_door", segment, centre - 0.6, 0.0, 1.2, 2.4, T, None, column, 0)


# --- Several shops in one building ---------------------------------------------------------


@pytest.mark.parametrize(("columns", "boundary"), [((0, 1), 4.0), ((0, 3), 8.0), ((0, 5), 12.0), ((1, 4), 12.0), ((4, 1), 12.0), ((3, 5), 16.0)])
def test_two_shops_on_the_frontage_split_the_room_on_the_middle_bay_boundary(columns: tuple[int, int], boundary: float) -> None:
    assert MURCIA_CENTER == (-1.12863, 37.98407)
    shops = [_shop(columns[0], BAKERY), _shop(columns[1], PHARMACY)]
    plan, layout = _rooms((0, 1, 3, 4, 5), shops)
    spans = sorted((room.polygon.bounds[0], room.polygon.bounds[2]) for room in plan.rooms)
    assert spans == pytest.approx([(T, boundary - PARTITION_M / 2), (boundary + PARTITION_M / 2, 24.0 - T)])
    assert all(room.polygon.bounds[1::2] == pytest.approx((T, 18.0 - T)) for room in plan.rooms)
    # The room of the main door comes first and keeps it; the other one takes its shop's shopfront for a door.
    home, other = plan.rooms
    assert home.entrance == (DOOR, SEG) and plan.inner == home.polygon and home.polygon.contains(Point(10.0, 1.0))
    other_shop = next(k for k, (_, _, (x, _)) in enumerate(shops) if not home.polygon.contains(Point(x, 1.0)))
    column = columns[other_shop]
    assert other.shops == (other_shop,) and home.shops == (1 - other_shop,)
    door = _door(2.0 + 4.0 * column, column)
    assert other.entrance == (door, SEG) and plan.shop_doors == (door,)
    assert door in layout.holes and _front(column) not in layout.holes and len(layout.holes) == 6
    assert (home.category.name, other.category.name) == (["bakery", "pharmacy"][1 - other_shop], ["bakery", "pharmacy"][other_shop])
    assert plan.hosts.equals(FOOTPRINT)
    assert plan.ground.area == pytest.approx((24.0 - 2 * T) * (18.0 - 2 * T) - PARTITION_M * (18.0 - 2 * T))


def test_three_shops_get_three_rooms_each_with_its_own_door() -> None:
    plan, layout = _rooms((0, 3, 5), [_shop(0, BAKERY), _shop(3, PHARMACY), _shop(5, BAR)])
    # Partitions at x = 8 (bays 0 and 3) and x = 16 (bays 3 and 5); the main door is in the middle room.
    assert [room.shops for room in plan.rooms][0] == (1,) and sorted(room.shops for room in plan.rooms) == [(0,), (1,), (2,)]
    assert [room.category.name for room in plan.rooms][0] == "pharmacy"
    assert sorted(room.category.name for room in plan.rooms) == ["bakery", "bar", "pharmacy"]
    assert all(room.entrance is not None for room in plan.rooms)
    assert sorted(door.column for door in plan.shop_doors) == [0, 5]
    assert [h.kind for h in layout.holes].count("interior_door") == 3


def test_a_shop_without_a_shopfront_shares_the_room_of_the_main_door() -> None:
    # Two shopfronts for three shops: the bar gets none and joins the room of the main door, whose first matching shop it is.
    plan, _ = _rooms((0, 4), [_shop(0, BAKERY), _shop(4, BOOKS), ("bar", BAR, None)])
    home, other = plan.rooms
    assert home.shops == (1, 2) and home.category.name == "bar"
    assert other.shops == (0,) and other.category.name == "bakery"


def test_one_shop_keeps_one_room_with_the_main_door() -> None:
    plan, layout = _rooms((3,), [_shop(3, BOOKS), ("bar", BAR, None)])
    [room] = plan.rooms
    assert room.polygon.equals(_plan().inner) and room.entrance == (DOOR, SEG) and room.shops == (0, 1)
    assert room.category.name == "bar" and plan.shop_doors == () and len(layout.holes) == 2


@pytest.mark.parametrize(("width", "fits"), [(2.8, True), (2.85, False)])
@pytest.mark.parametrize("columns", [(0, 1), (4, 5)])
def test_a_shop_door_needs_its_clear_floor_inside_the_room(columns: tuple[int, int], width: float, fits: bool) -> None:
    # The room of bay 0 runs from x = 0.3 to the partition at 3.9, that of bay 5 from 20.1 to 23.7.
    # A door centred on its bay keeps 0.3 m of clear floor on the corner side: up to 2.8 m wide.
    doc = copy.deepcopy(DOC)
    doc["interiors"]["door"]["width_m"] = width
    plan, _ = _rooms((0, 1, 4, 5), [_shop(columns[0], BAKERY), _shop(columns[1], PHARMACY)], cfg=config_from_document(doc))
    assert (plan.rooms[1].entrance is not None) is fits


def test_a_room_whose_door_does_not_fit_is_seen_through_its_windows_only() -> None:
    doc = copy.deepcopy(DOC)
    doc["interiors"]["door"]["width_m"] = 3.0  # wider than the room behind the corner bay allows
    cfg = config_from_document(doc)
    plan, layout = _rooms((0, 4), [_shop(0, BAKERY), _shop(4, PHARMACY)], cfg=cfg)
    home, other = plan.rooms
    assert home.entrance == (DOOR, SEG) and other.entrance is None and other.shops == (0,)
    assert plan.shop_doors == () and _front(0) in layout.holes
    built = build_interior(plan, 3.0, FOOTPRINT, cfg)
    assert [r["reachable"] for r in built.spec["rooms"]] == [True, False]
    assert [c["to"] for c in built.spec["connections"]] == ["b/L0"]


def test_two_shop_rooms_are_built_with_their_colours_doors_lights_and_a_solid_partition() -> None:
    plan, layout = _rooms((0, 4), [_shop(0, BAKERY), _shop(4, PHARMACY)])  # partition at x = 8
    pierce_room_windows(layout, [SEG], plan.ground, FOOTPRINT, T, 3.0 - CFG.interiors.slab_m, plan.hosts)
    built = build_interior(plan, 3.0, FOOTPRINT, CFG, room_window_cuts(layout, [SEG]), glass_penetrations(layout, [SEG]))
    assert built.defects == 0
    rooms = built.spec["rooms"]
    assert [r["id"] for r in rooms] == ["b/L0", "b/L0-1"]
    assert [r["shop"]["category"] for r in rooms] == ["pharmacy", "bakery"] and [r["shops"] for r in rooms] == [[1], [0]]
    assert [r["reachable"] for r in rooms] == [True, True]
    assert [r["area_m2"] for r in rooms] == [round(room.polygon.area, 3) for room in plan.rooms]
    assert built.spec["shop"] == rooms[0]["shop"] == {"category": "pharmacy", "wall_material": "interior_shop_pharmacy",
                                                       "floor_material": "interior_shop_tile", "attribute_origin": "generated"}
    assert [(c["to"], c["position_m"]) for c in built.spec["connections"]] == [("b/L0", [10.0, 0.0]), ("b/L0-1", [2.0, 0.0])]
    used = {name: sum(m.triangle_count for m in meshes) for name, meshes in built.visual.items() if meshes}
    assert set(used) == {"interior_shop_pharmacy", "interior_shop_bakery", "interior_shop_tile", "interior_wall"}
    assert used["interior_shop_tile"] == 4 and used["interior_wall"] == 4  # two floors, two ceilings
    # Each room has its light inside it, under the roof slab.
    anchors = built.light_anchors["rooms"]
    assert [a["room_id"] for a in anchors] == ["b/L0", "b/L0-1"]
    for anchor, room in zip(anchors, plan.rooms, strict=True):
        assert room.polygon.contains(Point(anchor["light_m"][:2])) and anchor["light_m"][2] == pytest.approx(3.0 - 0.2 - 0.3)
    # Walk in through either door; the partition between the rooms is solid, both sides of it are free.
    concrete = [m for _, m in built.colliders]
    for x in (2.0, 10.0):
        for y in (T / 2, T + 1.0):
            assert not any(_inside(m, np.array([x, y, 1.0])) for m in concrete)
    assert any(_inside(m, np.array([8.0, 9.0, 1.0])) for m in concrete)
    for x in (8.0 - PARTITION_M / 2 - 0.02, 8.0 + PARTITION_M / 2 + 0.02):
        assert not any(_inside(m, np.array([x, 9.0, 1.0])) for m in concrete)
    # The partition is drawn from both rooms: one wall face on each side, storey high.
    walls = [m for name in ("interior_shop_pharmacy", "interior_shop_bakery") for m in built.visual[name]]
    sides = sorted({round(float(v), 3) for m in walls for tri in m.vertices[m.triangles] if np.ptp(tri[:, 0]) < 1e-9
                    for v in tri[:1, 0] if 7.0 < v < 9.0})
    assert sides == [8.0 - PARTITION_M / 2, 8.0 + PARTITION_M / 2]


# --- Footprints of several parts -----------------------------------------------------------

PART_B = box(30.0, 0.0, 40.0, 10.0)
SEG_B = Segment(1, 0, 1, (30.0, 0.0), (40.0, 0.0), True)
PART_C = box(50.0, 0.0, 60.0, 10.0)
SEG_C = Segment(2, 0, 2, (50.0, 0.0), (60.0, 0.0), True)
PARTS = MultiPolygon([PART_C, FOOTPRINT, PART_B])  # the door's part is neither first nor last


def test_a_stair_interior_still_refuses_several_parts_and_a_shop_takes_the_part_of_its_door() -> None:
    assert plan_interior("b", PARTS, DOOR, SEG, 9.0, 3.0, 3, CFG) == "footprint_not_a_simple_polygon"
    assert plan_interior("b", PARTS, DOOR, SEG, 9.0, 3.0, 3, CFG, max_floors=1) == "footprint_not_a_simple_polygon"
    plan = _plan(PARTS)
    assert plan.inner.equals(FOOTPRINT.buffer(-T, join_style="mitre"))


def test_each_part_with_a_shopfront_is_a_room_and_only_a_part_with_a_shop_gets_a_door() -> None:
    window_c = Hole("window", 2, 4.4, 0.9, 1.2, 1.4, 0.15, "glass_dark", 0, 0)
    segments = (SEG, SEG_B, SEG_C)
    front_b = _front(0, 1, 5.0)
    # One shop, in the door's part: part B is a room seen through its shopfront, part C (a window only) stays solid.
    plan, layout = _rooms((0,), [_shop(0, BAKERY)], PARTS, segments, (front_b, window_c))
    home, part_b = plan.rooms
    assert part_b.polygon.equals(PART_B.buffer(-T, join_style="mitre")) and part_b.entrance is None and part_b.shops == ()
    assert part_b.category.name == "bakery" and home.shops == (0,)
    assert plan.hosts.equals(FOOTPRINT.union(PART_B))
    opened, omitted = pierce_room_windows(layout, list(segments), plan.ground, PARTS, T, 2.8, plan.hosts)
    assert (opened, omitted) == (2, 0)
    assert [h.back for h in layout.holes if h.kind != "interior_door"] == [SHOP_GLASS, SHOP_GLASS, "glass_dark"]
    built = build_interior(plan, 3.0, PARTS, CFG, room_window_cuts(layout, list(segments)), glass_penetrations(layout, list(segments)))
    assert built.defects == 0 and [r["reachable"] for r in built.spec["rooms"]] == [True, False]
    concrete = [m for _, m in built.colliders]
    assert not any(_inside(m, np.array([35.0, 5.0, 1.0])) for m in concrete)  # part B is hollow
    assert any(_inside(m, np.array([55.0, 5.0, 1.0])) for m in concrete)  # part C is solid
    assert any(_inside(m, np.array([35.0, T / 2, 0.1])) for m in concrete)  # no door under part B's shopfront
    # A shop whose shopfront is in part B gives that room a door in its place.
    plan, layout = _rooms((0,), [_shop(0, BAKERY), ("b", PHARMACY, (35.0, 1.0))], PARTS, segments, (front_b,))
    _, part_b = plan.rooms
    assert part_b.entrance == (_door(5.0, 0, 1), SEG_B) and part_b.shops == (1,) and part_b.category.name == "pharmacy"


def test_glass_on_a_part_without_a_room_stays_dark_and_is_not_omitted() -> None:
    inner = FOOTPRINT.buffer(-T, join_style="mitre")
    layout = Layout(holes=[Hole("window", 1, 4.4, 0.9, 1.2, 1.4, 0.15, "glass_dark", 0, 0), _front(0)])
    assert pierce_room_windows(layout, [SEG, SEG_B], inner, PARTS, T, 2.8, FOOTPRINT) == (1, 0)
    assert [h.back for h in layout.holes] == ["glass_dark", SHOP_GLASS]
    layout.holes = layout.holes[:1]
    assert pierce_room_windows(layout, [SEG, SEG_B], inner, PARTS, T, 2.8) == (0, 1) and layout.holes == []


# --- Courtyards ----------------------------------------------------------------------------

COURTYARD = Polygon(FOOTPRINT.exterior.coords, [[(8.0, 6.0), (8.0, 12.0), (16.0, 12.0), (16.0, 6.0)]])
# The courtyard's south wall, walked clockwise: its outward side (n_out) is the courtyard, north.
YARD = Segment(0, 1, 1, (16.0, 6.0), (8.0, 6.0), True)


@pytest.mark.parametrize("max_floors", [1, None])
def test_a_courtyard_stays_a_hole_in_the_room(max_floors: int | None) -> None:
    plan = plan_interior("b", COURTYARD, DOOR, SEG, 9.0, 3.0, 3, CFG, max_floors=max_floors)
    assert not isinstance(plan, str), plan
    assert len(plan.inner.interiors) == 1 and plan.inner.area == pytest.approx(23.4 * 17.4 - 8.6 * 6.6)


def test_a_courtyard_shop_has_walls_round_the_hole_and_a_see_through_courtyard_window() -> None:
    window = Hole("window", 1, 3.4, 0.9, 1.2, 1.4, 0.15, "glass_dark", 0, 0)  # centred at x = 12
    plan, layout = _rooms((0,), [_shop(0, BAKERY)], COURTYARD, (SEG, YARD), (window,))
    [room] = plan.rooms
    assert len(room.polygon.interiors) == 1
    assert pierce_room_windows(layout, [SEG, YARD], plan.ground, COURTYARD, T, 2.8, plan.hosts) == (2, 0)
    built = build_interior(plan, 3.0, COURTYARD, CFG, room_window_cuts(layout, [SEG, YARD]), glass_penetrations(layout, [SEG, YARD]))
    # The courtyard window and the frameless shop glass each keep a #glass pane.
    assert [h.back for h in layout.holes if h.kind != "interior_door"] == [SHOP_GLASS, GLASS_VIEW]
    assert built.defects == 0 and (built.spec["glass_panes"], built.spec["open_shopfronts"]) == (2, 1)
    [walls] = built.visual["interior_shop_bakery"]
    ceiling = 3.0 - CFG.interiors.slab_m
    assert _area(walls) == pytest.approx((81.6 + 30.4) * ceiling - 1.2 * 2.4 - 1.2 * 1.4 - 3.0 * 2.3)
    # The walls round the courtyard face into the room, away from the hole.
    tris = walls.vertices[walls.triangles]
    normals = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    for x, sign in ((8.0 - T, -1.0), (16.0 + T, 1.0)):
        on = np.all(np.abs(tris[:, :, 0] - x) < 1e-9, axis=1)
        assert on.any() and np.all(np.sign(normals[on, 0]) == sign)
    # The courtyard is open air, its wall is solid below the window, and the window has its pane.
    concrete = [m for _, m in built.colliders]
    assert not any(_inside(m, np.array([12.0, 9.0, 1.0])) for m in [*concrete, *built.glass_colliders])
    assert any(_inside(m, np.array([12.0, 6.0 - T / 2, 0.5])) for m in concrete)
    assert not any(_inside(m, np.array([12.0, 6.0 - T / 2, 1.6])) for m in concrete)
    assert sum(_inside(m, np.array([12.0, 6.0 - T / 2, 1.6])) for m in built.glass_colliders) == 1
    # The shopfront (x 0.5 to 3.5) above its stallriser: no wall (a shot crosses its glass), one #glass pane (a body stops).
    assert any(_inside(m, np.array([2.0, T / 2, 0.1])) for m in concrete)
    assert not any(_inside(m, np.array([2.0, T / 2, 1.2])) for m in concrete)
    assert sum(_inside(m, np.array([2.0, T / 2, 1.2])) for m in built.glass_colliders) == 1
    # The light moves out of the courtyard into the room.
    [anchor] = built.light_anchors["rooms"]
    assert room.polygon.contains(Point(anchor["light_m"][:2])) and not Polygon(COURTYARD.interiors[0]).contains(Point(anchor["light_m"][:2]))
    assert built.light_anchors == light_anchors(plan, built.spec["rooms"])


def test_inner_walls_cover_every_ring_of_the_room() -> None:
    inner = COURTYARD.buffer(-T, join_style="mitre")
    assert _area(_inner_walls(inner, 0.0, 2.0, [])) == pytest.approx((81.6 + 30.4) * 2.0)


# --- Signs on a shop door --------------------------------------------------------------------


def test_a_door_that_took_a_shopfront_carries_its_shops_sign_and_no_vinyl() -> None:
    doc = copy.deepcopy(DOC)
    doc["shops"]["vinyl_share"] = 1.0
    cfg = config_from_document(doc)
    door = _door(6.0, 1)
    holes = [_front(0), door]
    shops = [_shop(1, BAKERY)]
    assert shop_bays(shops, holes, [SEG]) == [_front(0)]
    assert shop_bays(shops, holes, [SEG], [door]) == [door]
    assert shop_bays([*shops, ("x", BAR, None), ("y", BAR, None)], holes, [SEG], [door]) == [door, _front(0), None]
    [record] = plan_shops("b", shops, holes, [SEG], cfg, [door])
    assert record["sign"]["centre_m"][0] == pytest.approx(6.0 if record["sign"]["kind"] == "fascia" else 5.4 - cfg.min_pier_m / 4)
    assert len(record["vinyls"]) == 1 and record["vinyls"][0]["centre_m"][0] == pytest.approx(2.0)


# --- Edges of the rules --------------------------------------------------------------------


def test_an_opening_is_on_a_room_ring_up_to_the_tolerance() -> None:
    room = box(0.0, 0.0, 10.0, 10.0)
    hole = Hole("window", 0, 2.0, 0.9, 2.0, 1.4, 0.15, "glass_dark", 0, 0)
    # The face runs along y = 0 with the street to the south, so a depth is a step north.
    assert RIM_TOLERANCE_M == 0.001
    assert _rim_on(room, SEG, hole, 0.0) and _rim_on(room, SEG, hole, RIM_TOLERANCE_M)
    assert not _rim_on(room, SEG, hole, 0.002) and not _rim_on(room, SEG, replace(hole, s0=9.0), 0.0)


def test_a_window_counts_as_on_a_hosting_part_up_to_the_tolerance() -> None:
    inner = FOOTPRINT.buffer(-T, join_style="mitre")
    for hosts, result in ((box(0.0, 0.001, 24.0, 18.0), (1, 0)), (box(0.0, 0.002, 24.0, 18.0), (0, 0))):
        layout = Layout(holes=[Hole("window", 0, 4.0, 0.9, 1.2, 1.4, 0.15, "glass_dark", 0, 0)])
        assert pierce_room_windows(layout, [SEG], inner, FOOTPRINT, T, 2.8, hosts) == result


def test_a_door_off_the_room_or_a_room_too_small_is_refused_and_a_split_room_is_the_door_piece() -> None:
    floating = Segment(0, 0, 0, (2.0, 5.0), (18.0, 5.0), True)
    assert plan_interior("b", FOOTPRINT, DOOR, floating, 3.0, 3.0, 1, CFG, max_floors=1) == "door_not_on_inner_wall"
    corner = replace(DOOR, s0=0.35)  # on the inner wall, but the clear floor beside it runs into the side wall
    assert plan_interior("b", FOOTPRINT, corner, SEG, 3.0, 3.0, 1, CFG, max_floors=1) == "no_room_behind_door"
    door = Hole("interior_door", 0, 1.0, 0.0, 1.2, 2.4, T, None, 0, 0)
    exact = box(-T, -T, 4.0 + T, 5.0 + T)  # a 4 × 5 m room: 20 m², the smallest allowed
    wall = Segment(0, 0, 0, (-T, -T), (4.0 + T, -T), True)
    assert _inset(exact, T).area == 20.0
    assert not isinstance(plan_interior("b", exact, door, wall, 3.0, 3.0, 1, CFG, max_floors=1), str)
    small = box(-T, -T, 4.0 + T, 4.9 + T)
    assert plan_interior("b", small, door, wall, 3.0, 3.0, 1, CFG, max_floors=1) == "inner_space_too_small"
    # Two 6 m rooms joined by a 0.4 m neck: the walls close the neck; the room is the one behind the door (ADR-019).
    split = box(0.0, 0.0, 6.0, 6.0).union(box(6.0, 2.8, 8.0, 3.2)).union(box(8.0, 0.0, 14.0, 6.0))
    sill = Segment(0, 0, 0, (0.0, 0.0), (6.0, 0.0), True)
    plan = plan_interior("b", split, door, sill, 3.0, 3.0, 1, CFG, max_floors=1)
    assert not isinstance(plan, str) and plan.inner.bounds == pytest.approx((T, T, 6.0 - T, 6.0 - T))


def test_a_part_whose_walls_leave_two_rooms_gets_no_room() -> None:
    neck = box(30.0, 0.0, 36.0, 6.0).union(box(36.0, 2.8, 38.0, 3.2)).union(box(38.0, 0.0, 44.0, 6.0))
    plan, _ = _rooms((0,), [_shop(0, BAKERY)], MultiPolygon([FOOTPRINT, neck]), (SEG, SEG_B), (_front(0, 1, 3.0),))
    assert len(plan.rooms) == 1


def test_a_room_is_mitred_at_twice_the_wall_thickness() -> None:
    notch = Polygon([(0, 0), (20, 0), (20, 20), (11, 20), (10, 10), (9, 20), (0, 20)])  # a sharp inner corner
    assert _inset(notch, T).area == pytest.approx(360.776, abs=1e-3)
    assert _inset(box(0.0, 0.0, 4.0, 6.0), 0.5).equals(box(0.5, 0.5, 3.5, 5.5))


def test_a_shop_room_record_and_its_door_keep_their_precision() -> None:
    odd = box(0.0, 0.0, 24.0007, 18.0003)
    door = Hole("interior_door", 0, 9.40037, 0.0, 1.2, 2.4, T, None, 2, 0)
    plan = plan_interior("odd", odd, door, SEG, 3.0007, 3.0007, 1, CFG, roof_access=False, max_floors=1, several_parts=True)
    assert not isinstance(plan, str)
    plan = plan_shop_rooms(plan, odd, Layout(holes=[door]), [SEG], [("p", BAKERY, None)], CFG)
    built = build_interior(plan, 3.0007, odd, CFG)
    area = plan.inner.area
    assert round(area, 3) != round(area, 4)
    assert built.spec["rooms"] == [{
        "id": "odd/L0", "level": 0, "floor_z_m": 0.0, "ceiling_z_m": 2.801, "area_m2": round(area, 3), "reachable": True,
        "shops": [0], "shop": {"category": "bakery", "wall_material": "interior_shop_bakery", "floor_material": "interior_shop_tile",
                               "attribute_origin": "generated"}}]
    assert built.spec["connections"] == [{"type": "door", "from": "exterior", "to": "odd/L0", "width_m": 1.2, "height_m": 2.4,
                                          "position_m": [10.0, 0.0]}]


def test_a_shop_door_is_open_through_the_wall_and_its_lintel_and_ceiling_are_closed() -> None:
    plan, _ = _rooms((0, 4), [_shop(0, BAKERY), _shop(4, PHARMACY)])
    built = build_interior(plan, 3.0, FOOTPRINT, CFG)
    concrete = [m for _, m in built.colliders]
    for x in (2.0, 10.0):
        assert not any(_inside(m, np.array([x, T - 0.02, 1.0])) for m in concrete)
        assert any(_inside(m, np.array([x, T / 2, 2.6])) for m in concrete)  # over the 2.4 m door head
    # Each room's wall is cut on its own doorway, and the ceiling faces down into the rooms.
    walls = np.vstack([m.vertices for name in ("interior_shop_pharmacy", "interior_shop_bakery") for m in built.visual[name]])
    for x in (1.4, 2.6, 9.4, 10.6):
        assert np.any(np.all(np.isclose(walls, [x, T, 2.4]), axis=1))
    assert not np.any(np.all(np.isclose(walls, [0.2, T, 2.4]), axis=1))
    normals = np.vstack([np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]) for t in (m.vertices[m.triangles] for m in built.visual["interior_wall"])])
    assert len(normals) == 4 and np.all(normals[:, 2] < 0)
