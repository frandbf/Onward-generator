"""Shop furnishing planner (ADR-031): sets by category, wall snap, door and path clearance, budget, colliders.

Local metres. Plan geometry is synthetic; the shop-room fixture is the 24 x 18 m shop of
``tests/unit/test_shop_rooms.py`` (origin MURCIA_CENTER). Catalogue sizes come from the manifests, so
these tests read no GLB.
"""

from __future__ import annotations

import json
import math
from dataclasses import replace

import pytest
from shapely import LineString, Point, Polygon, box, unary_union
from shapely.affinity import rotate

from onward_generator.detail.config import (
    REPO_ROOT,
    FurnishEntry,
    FurnishSet,
    ShopFurnish,
    config_from_document,
)
from onward_generator.detail.furnish import (
    COLLIDER_TRIANGLES,
    FurnishAsset,
    FurnishBudget,
    FurnishRoom,
    Piece,
    RoomFurnishing,
    _Planner,
    footprint_size,
    free_intervals,
    furnish_rooms,
    piece_solid,
    plan_room,
    room_edges,
)
from onward_generator.detail.interiors import _door_zone, light_anchors, plan_shop_rooms
from onward_generator.detail.shops import CATASTRO_USE_TAG, shop_category
from onward_generator.generation.mesh import polygons_of
from tests.unit.test_shop_rooms import BAKERY, DOC, PHARMACY, SEG, _front, _plan, _shop

ON = json.loads(json.dumps(DOC))
ON["interiors"]["shops"]["furnish"]["enabled"] = True
CFG = config_from_document(ON)
FURNISH = CFG.interiors.shops.furnish
GAP = FURNISH.wall_gap_m


def _manifest_assets() -> dict[str, FurnishAsset]:
    found = {}
    for catalogue in FURNISH.catalogues:
        doc = json.loads((REPO_ROOT / "assets" / catalogue / "manifest.json").read_text(encoding="utf-8"))
        for row in doc["assets"]:
            found[row["id"]] = FurnishAsset(row["id"], catalogue, "CC0-1.0", row["role"], row["category"],
                                            (row["length_m"], row["depth_m"], row["height_m"]), row["triangles"], "0" * 64)
    return found


ASSETS = _manifest_assets()


def _asset(asset_id: str, size: tuple[float, float, float], triangles: int = 100, role: str = "shelf") -> FurnishAsset:
    return FurnishAsset(asset_id, "polyhaven_shop", "CC0-1.0", role, "furniture", size, triangles, "f" * 64)


def _furnish(counter=(), wall=(), decor=(), **changes) -> ShopFurnish:
    sets = {"generic": FurnishSet(tuple(FurnishEntry(i) for i in counter), tuple(FurnishEntry(i) for i in wall),
                                  tuple(FurnishEntry(i) for i in decor))}
    return replace(FURNISH, sets=sets, **changes)


def _room(polygon: Polygon, door_zone: Polygon | None, **kw) -> FurnishRoom:
    return FurnishRoom("b/L0", polygon, door_zone, kw.pop("ceiling_m", 3.0), **kw)


ROOM = box(0.0, 0.0, 6.0, 4.0)
DOOR_ZONE = box(3.5, 0.0, 5.3, 1.5)  # a 1.2 m door at x 3.8..5.0 on the street wall y = 0
WINDOW = Polygon([(0.5, 0.0), (2.0, 0.0), (2.0, 0.001), (0.5, 0.001)])


def _plan_default(room: FurnishRoom, seed: int = 48152, building: str = "b", catalogue=None, furnish=FURNISH) -> RoomFurnishing:
    result = plan_room(room, furnish, catalogue or ASSETS, seed, building)
    assert isinstance(result, RoomFurnishing), result
    return result


def _floor(result: RoomFurnishing) -> list[Piece]:
    return [p for p in result.pieces if p.slot != "decor"]


@pytest.mark.parametrize("slot", ["counter", "wall", "decor"])
@pytest.mark.parametrize(("tags", "category"), [
    ({"shop": "bakery"}, "bakery"), ({"amenity": "pharmacy"}, "pharmacy"), ({"amenity": "cafe"}, "bar"),
    ({"shop": "clothes"}, "generic"), ({CATASTRO_USE_TAG: "C"}, "generic"),
])
def test_each_category_picks_only_from_its_own_set(tags: dict[str, str], category: str, slot: str) -> None:
    chosen = shop_category([tags], CFG).name
    assert chosen == category
    room = _room(box(0.0, 0.0, 10.0, 8.0), box(3.0, 0.0, 4.8, 1.5), category=chosen,
                 display=(Polygon([(6.0, 0.0), (9.0, 0.0), (9.0, 1e-3), (6.0, 1e-3)]).exterior,))
    result = _plan_default(room)
    allowed = {entry.id for entry in FURNISH.set_for(category).slot(slot)}
    used = {p.asset.asset_id for p in result.pieces if p.slot == slot}
    assert used <= allowed
    assert result.category == category
    if slot != "decor" or allowed:
        assert used, f"no {slot} piece in a 10 x 8 m {category} room"


def _wall_of(piece: Piece, polygon: Polygon) -> tuple[float, float]:
    """Distance from the piece's footprint to the nearest wall, and |cos| between its facing and that wall."""
    facing = (math.sin(math.radians(piece.yaw_deg)), -math.cos(math.radians(piece.yaw_deg)))  # the model's -Y, turned
    depth = footprint_size(piece.asset, piece.turn_deg)[1]
    back = Point(piece.centre[0] - facing[0] * depth / 2, piece.centre[1] - facing[1] * depth / 2)
    ring = list(polygon.exterior.coords)
    wall = min((LineString([ring[k], ring[k + 1]]) for k in range(len(ring) - 1)), key=back.distance)
    (ax, ay), (bx, by) = wall.coords
    ux, uy = (bx - ax) / wall.length, (by - ay) / wall.length
    return back.distance(wall), abs(facing[0] * ux + facing[1] * uy)


@pytest.mark.parametrize("turn", [0.0, 37.0])
def test_wall_pieces_stand_back_to_the_wall_a_wall_gap_off_it(turn: float) -> None:
    polygon = rotate(ROOM, turn, origin=(0, 0))
    zone = rotate(DOOR_ZONE, turn, origin=(0, 0))
    furnish = replace(FURNISH, max_pieces_per_room=8)  # enough pieces to pack a wall: the packing is what this checks
    result = _plan_default(_room(polygon, zone), furnish=furnish)
    assert len(result.pieces) == furnish.max_pieces_per_room
    for piece in _floor(result):
        distance, cosine = _wall_of(piece, polygon)
        assert distance == pytest.approx(GAP, abs=1e-6)
        assert cosine == pytest.approx(0.0, abs=1e-6)  # it faces straight into the room
        assert polygon.buffer(-GAP + 1e-9).contains(piece.footprint)
    gaps = [a.footprint.distance(b.footprint) for i, a in enumerate(_floor(result)) for b in _floor(result)[i + 1:]]
    assert min(gaps) >= FURNISH.piece_gap_m - 1e-6
    assert any(g == pytest.approx(FURNISH.piece_gap_m, abs=1e-6) for g in gaps)


def test_the_same_layout_in_a_room_turned_by_37_degrees() -> None:
    flat = _plan_default(_room(ROOM, DOOR_ZONE))
    turned = _plan_default(_room(rotate(ROOM, 37.0, origin=(0, 0)), rotate(DOOR_ZONE, 37.0, origin=(0, 0))))
    assert [p.asset.asset_id for p in flat.pieces] == [p.asset.asset_id for p in turned.pieces]
    c, s = math.cos(math.radians(37.0)), math.sin(math.radians(37.0))
    for a, b in zip(flat.pieces, turned.pieces, strict=True):
        assert b.centre == pytest.approx((a.centre[0] * c - a.centre[1] * s, a.centre[0] * s + a.centre[1] * c), abs=1e-6)
        assert (b.yaw_deg - a.yaw_deg) % 360.0 == pytest.approx(37.0, abs=1e-6)


NARROW = box(0.0, 0.0, 8.0, 2.6)
WEST_DOOR = box(0.0, 0.4, 1.5, 2.2)  # a 1.2 m door in the west wall


def _walk_ok(polygon: Polygon, result: RoomFurnishing, zone: Polygon, width: float) -> bool:
    walk = polygon.difference(result.floor_footprints).buffer(-width / 2)
    parts = [p for p in polygons_of(walk) if p.area > 1e-6]
    return len(parts) == 1 and parts[0].intersects(zone)


def test_a_far_wall_shelf_that_would_leave_1_1_m_is_refused() -> None:
    deep = _asset("deep", (1.5, 0.72, 2.0))  # two facing it leave 2.6 - 2 x 0.75 = 1.1 m
    result = _plan_default(_room(NARROW, WEST_DOOR), catalogue={"deep": deep}, furnish=_furnish(wall=["deep"], max_pieces_per_room=12))
    assert "blocks_path" in result.refusals
    assert _walk_ok(NARROW, result, WEST_DOOR, FURNISH.walk_width_m)
    ys = [p.footprint.bounds for p in result.pieces]
    low = [b for b in ys if b[1] < 1.0]
    high = [b for b in ys if b[1] > 1.0]
    assert low and high
    assert not any(a[0] < b[2] and b[0] < a[2] for a in low for b in high)  # no two face each other across 1.1 m


@pytest.mark.parametrize(("corridor", "furnished"), [(1.21, True), (1.19, False)])
def test_the_corridor_beside_a_piece_must_stay_1_2_m(corridor: float, furnished: bool) -> None:
    width = 2.0
    depth = width - GAP - corridor
    piece = _asset("p", (2.0, depth, 1.8))
    room = _room(box(0.0, 0.0, 6.0, width), box(0.0, 0.1, 1.5, 1.9))
    result = plan_room(room, _furnish(wall=["p"], max_pieces_per_room=1), {"p": piece}, 48152, "b")
    if furnished:
        assert isinstance(result, RoomFurnishing) and len(result.pieces) == 1
    else:
        assert result == "blocks_path"


def test_no_piece_enters_the_door_zone_beside_a_free_wall() -> None:
    result = _plan_default(_room(ROOM, DOOR_ZONE), furnish=replace(FURNISH, max_pieces_per_room=24))
    assert len(result.pieces) > 4
    assert all(p.footprint.intersection(DOOR_ZONE).area <= 1e-9 for p in result.pieces)
    assert _walk_ok(ROOM, result, DOOR_ZONE, FURNISH.walk_width_m)


def _two_shops():
    layout_holes = [_front(c) for c in (0, 1, 3, 4, 5)]
    from onward_generator.detail.facades import Layout

    layout = Layout()
    layout.holes = layout_holes
    plan = _plan()
    shops = [_shop(0, BAKERY), _shop(4, PHARMACY)]
    rooms = plan_shop_rooms(plan, box(0.0, 0.0, 24.0, 18.0), layout, [SEG], shops, CFG)
    records = [{"id": f"b/L0{'-' + str(i) if i else ''}", "ceiling_z_m": 3.0} for i in range(len(rooms.rooms))]
    return rooms, records


def test_every_walkable_door_of_a_two_shop_plan_keeps_its_zone_and_the_partition_clear() -> None:
    plan, records = _two_shops()
    rooms = furnish_rooms(plan, records, [], CFG.interiors)
    assert len(rooms) == 2 and all(room.door_zone is not None for room in rooms)
    inner = box(0.0, 0.0, 24.0, 18.0).buffer(-CFG.interiors.wall_thickness_m, join_style="mitre")
    partition = inner.difference(unary_union([room.polygon for room in rooms]))
    assert partition.area > 0
    for room in rooms:
        result = _plan_default(room)
        assert result.category in {"bakery", "pharmacy"}
        for piece in result.pieces:
            assert room.polygon.contains(piece.footprint)
            for other in rooms:
                assert piece.footprint.intersection(other.door_zone).area <= 1e-9
            assert piece.footprint.intersection(partition).area <= 1e-9


def test_the_counter_stands_on_the_display_wall_or_beside_it() -> None:
    room = _room(ROOM, DOOR_ZONE, display=(WINDOW.exterior,), panes=(box(0.5, -0.3, 2.0, FURNISH.pane_clear_m),))
    result = _plan_default(room)
    counter = next(p for p in result.pieces if p.slot == "counter")
    walls = {"street": counter.footprint.distance(box(0, -1, 6, 0)), "west": counter.footprint.distance(box(-1, 0, 0, 4)),
             "east": counter.footprint.distance(box(6, 0, 7, 4))}
    assert min(walls.values()) == pytest.approx(GAP, abs=1e-6)
    assert counter.footprint.intersection(DOOR_ZONE).area <= 1e-9
    assert counter.footprint.intersection(room.panes[0]).area <= 1e-9


def test_a_room_with_no_counter_spot_records_it_once_and_keeps_its_wall_pieces() -> None:
    catalogue = {"long": _asset("long", (9.0, 0.8, 1.0), role="counter"), "shelf": _asset("shelf", (1.0, 0.3, 1.8))}
    result = _plan_default(_room(ROOM, DOOR_ZONE), catalogue=catalogue, furnish=_furnish(counter=["long"], wall=["shelf"]))
    assert result.refusals.count("no_counter_spot") == 1
    assert {p.slot for p in result.pieces} == {"wall"}


def test_pieces_never_overlap_and_keep_off_a_courtyard() -> None:
    courtyard = box(4.0, 4.0, 6.0, 6.0)
    polygon = box(0.0, 0.0, 10.0, 10.0).difference(courtyard)
    result = _plan_default(_room(polygon, box(3.0, 0.0, 4.8, 1.5)), furnish=replace(FURNISH, max_pieces_per_room=24))
    floor = _floor(result)
    assert any(p.footprint.distance(courtyard) <= GAP + 1e-6 for p in floor)  # the courtyard walls take pieces too
    for i, a in enumerate(floor):
        assert a.footprint.intersection(courtyard.buffer(GAP - 1e-6)).area <= 1e-9
        for b in floor[i + 1:]:
            assert a.footprint.intersection(b.footprint).area <= 1e-9


@pytest.mark.parametrize(("depth", "reason"), [(0.149, "pane_band"), (0.151, None)])
def test_the_pane_band_boundary(depth: float, reason: str | None) -> None:
    band = box(1.0, -0.3, 3.0, FURNISH.pane_clear_m)
    planner = _Planner(_room(ROOM, DOOR_ZONE, panes=(band,)), FURNISH, ASSETS, 48152, "b")
    assert planner.conflict(box(1.2, depth, 2.0, depth + 0.5)) == reason


def test_no_piece_reaches_the_head_clearance() -> None:
    result = _plan_default(_room(ROOM, DOOR_ZONE, ceiling_m=2.0))
    assert result.pieces
    assert all(p.z_m + p.asset.size_m[2] <= 2.0 - FURNISH.head_clear_m + 1e-9 for p in result.pieces)


def test_the_piece_count_is_capped_and_a_room_with_no_door_is_unreachable() -> None:
    big = _room(box(0.0, 0.0, 20.0, 20.0), box(3.0, 0.0, 4.8, 1.5))
    assert len(_plan_default(big, furnish=replace(FURNISH, max_pieces_per_room=3)).pieces) == 3
    assert plan_room(_room(ROOM, None), FURNISH, ASSETS, 48152, "b") == "unreachable"


def _furnished(triangles: int, solid: int = 0, asset_id: str = "x") -> RoomFurnishing:
    asset = _asset(asset_id, (1.0, 0.5, 1.0), triangles)
    pieces = tuple(Piece(asset, "wall", box(0, 0, 1, 0.5), (0.5, 0.25), 0.0, 0.0, 0, k < solid) for k in range(max(solid, 1)))
    return RoomFurnishing("b/L0", "generic", pieces)


def test_the_budget_takes_a_room_that_fits_exactly_and_refuses_one_triangle_more() -> None:
    budget = FurnishBudget(1000, 1200, 16)
    budget.used["triangles"] = 600
    assert budget.refuse(_furnished(400)) is None
    assert budget.refuse(_furnished(401)) == "triangle_budget: needs 401 triangles, 400 left of shops.furnish.max_triangles 1000"
    collision = FurnishBudget(100000, 24, 16)
    assert collision.refuse(_furnished(1, solid=2)) is None
    assert collision.refuse(_furnished(1, solid=3)) == (
        "collision_budget: needs 36 collision triangles, 24 left of shops.furnish.max_collision_triangles 24")
    assert FurnishBudget(0, 1200, 16).refuse(_furnished(1)) is not None


def test_the_asset_cap_refuses_a_seventeenth_distinct_asset() -> None:
    budget = FurnishBudget(100000, 1200, 16)
    budget.assets = {f"a{k}" for k in range(16)}
    assert budget.refuse(_furnished(1, asset_id="a3")) is None
    assert budget.refuse(_furnished(1, asset_id="new")) == "asset_cap: needs 17 distinct assets, 16 allowed by shops.furnish.max_assets"
    budget.spend(_furnished(5, solid=1, asset_id="a3"))
    assert budget.used == {"triangles": 5, "collision_triangles": COLLIDER_TRIANGLES}


@pytest.mark.parametrize(("size", "solid"), [
    ((1.0, 0.4, 0.5), True), ((1.0, 0.2, 0.5), True), ((1.0, 0.19, 0.5), False), ((1.0, 0.4, 0.49), False), ((0.4, 2.0, 0.5), True), ((1.2, 0.1, 2.0), True), ((1.2, 0.09, 2.0), False),
])
def test_a_piece_gets_a_collider_only_when_it_is_cover_sized(size: tuple[float, float, float], solid: bool) -> None:
    asset = _asset("p", size, role="room_furniture")
    assert piece_solid(asset, "wall") is solid
    assert piece_solid(asset, "decor") is False
    assert _furnished(1, solid=2).collision_triangles == 2 * COLLIDER_TRIANGLES


def test_food_decor_is_never_solid_and_sits_on_the_counter() -> None:
    room = _room(ROOM, DOOR_ZONE, category="bakery", display=(WINDOW.exterior,))
    result = _plan_default(room)
    decor = [p for p in result.pieces if p.slot == "decor"]
    counter = next(p for p in result.pieces if p.slot == "counter")
    assert decor and all(not p.solid and p.asset.catalogue == "kenney_food" for p in decor)
    assert all(counter.footprint.contains(p.footprint) and p.z_m == pytest.approx(counter.asset.size_m[2]) for p in decor)


def _record(result: RoomFurnishing) -> str:
    return json.dumps(result.record("b", 0), sort_keys=True)


def test_the_plan_is_deterministic_and_does_not_depend_on_set_or_catalogue_order() -> None:
    room = _room(ROOM, DOOR_ZONE, category="bar", display=(WINDOW.exterior,))
    first = _record(_plan_default(room))
    assert first == _record(_plan_default(room))
    reversed_sets = {name: FurnishSet(*(tuple(reversed(s.slot(slot))) for slot in ("counter", "wall", "decor")))
                     for name, s in FURNISH.sets.items()}
    shuffled = dict(reversed(list(ASSETS.items())))
    assert _record(_plan_default(room, catalogue=shuffled, furnish=replace(FURNISH, sets=reversed_sets))) == first
    assert _record(_plan_default(room, seed=7)) != first
    assert _plan_default(room, building="c").record("b", 0) != json.loads(first)


def test_every_piece_records_its_provenance() -> None:
    record = _plan_default(_room(ROOM, DOOR_ZONE, category="bakery", display=(WINDOW.exterior,))).record("osm-way-1", 3)
    assert record["attribute_origin"] == "generated" and record["method"] == "shop_furnishing"
    assert [p["id"] for p in record["pieces"]][:2] == ["furnish/osm-way-1/3", "furnish/osm-way-1/4"]
    for piece in record["pieces"]:
        assert piece["slot"] in {"counter", "wall", "decor"}
        assert piece["attribute_origin"] == "generated" and piece["semantics_origin"] == "estimated"
        assert piece["licence"] == "CC0-1.0" and piece["catalogue"] in FURNISH.catalogues
        assert piece["asset_id"] in ASSETS and len(piece["glb_sha256"]) == 64
        assert set(piece) >= {"position_m", "yaw_deg", "turn_deg", "size_m", "footprint_m", "triangles", "solid"}


def test_light_probes_keep_out_of_the_furniture_and_stay_put_without_it() -> None:
    plan, records = _two_shops()
    rooms = [dict(r, floor_z_m=0.0) for r in records]
    before = light_anchors(plan, rooms)
    assert light_anchors(plan, rooms, furniture=None) == before
    assert light_anchors(plan, rooms, furniture=Polygon()) == before
    furniture = unary_union([box(1.0, 0.3, 11.0, 3.0), box(13.0, 0.3, 23.0, 3.0)])
    after = light_anchors(plan, rooms, furniture=furniture)
    assert after != before
    for room in after["rooms"]:
        probes = room["probes_m"]
        for k in range(0, len(probes), 3):
            assert not furniture.contains(Point(probes[k], probes[k + 1]))


def test_door_zone_used_is_the_interiors_one() -> None:
    plan, records = _two_shops()
    rooms = furnish_rooms(plan, records, [], CFG.interiors)
    door, seg = plan.rooms[0].entrance
    assert rooms[0].door_zone.equals(_door_zone(seg, door, CFG.interiors))


@pytest.mark.parametrize(("role", "category", "solid"), [
    ("table", "furniture", True), ("room_furniture", "structures", True), ("shelf", "vegetation", False), ("plant", "furniture", False),
])
def test_the_cover_rule_reads_the_role_and_the_category(role: str, category: str, solid: bool) -> None:
    small, big = (0.2, 0.2, 0.2), (1.0, 1.0, 1.0)
    asset = FurnishAsset("p", "kenney_furniture", "CC0-1.0", role, category, small if solid else big, 10, "0" * 64)
    assert piece_solid(asset, "wall") is solid


@pytest.mark.parametrize(("turn", "size"), [(0, (1.0, 0.3)), (90, (0.3, 1.0)), (180, (1.0, 0.3)), (270, (0.3, 1.0))])
def test_a_quarter_turn_swaps_length_and_depth(turn: int, size: tuple[float, float]) -> None:
    assert footprint_size(_asset("p", (1.0, 0.3, 2.0)), turn) == size


def test_room_edges_skip_a_repeated_vertex_keep_short_edges_and_number_the_rings() -> None:
    outer = [(0.0, 0.0), (0.0, 0.0), (6.0, 0.0), (6.0, 0.5), (6.0, 4.0), (0.0, 4.0)]
    polygon = Polygon(outer, [[(2.0, 1.0), (2.0, 2.0), (3.0, 2.0), (3.0, 1.0)]])
    edges = room_edges(polygon)
    assert [(e.ring, round(e.length, 6)) for e in edges] == [(0, 6.0), (0, 0.5), (0, 3.5), (0, 6.0), (0, 4.0),
                                                            (1, 1.0), (1, 1.0), (1, 1.0), (1, 1.0)]
    for edge in edges:
        assert edge.u[0] * edge.n[1] - edge.u[1] * edge.n[0] == pytest.approx(1.0)  # the room is on the left of every ring
        assert polygon.contains(Point(*(edge.a + edge.u * edge.length / 2 + edge.n * 0.01)))


def test_free_intervals_leave_no_empty_stretch_at_either_end() -> None:
    edge = room_edges(Polygon([(0.0, 0.0), (6.0, 0.0), (6.0, 4.0), (0.0, 4.0)]))[0]  # (0, 0) to (6, 0)
    assert free_intervals(edge, 0.5, box(-1.0, -1.0, 7.0, 5.0), 0.03) == [(0.0, 6.0)]
    assert free_intervals(edge, 0.5, box(1.0, -1.0, 7.0, 5.0), 0.03) == [(1.0, 6.0)]
    assert free_intervals(edge, 0.5, box(-1.0, -1.0, 5.0, 5.0), 0.03) == [(0.0, 5.0)]
    assert free_intervals(edge, 0.5, box(-1.0, -1.0, 7.0, 5.0).difference(box(2.0, 0.0, 3.0, 1.0)), 0.03) == [(0.0, 2.0), (3.0, 6.0)]


def test_plan_room_says_why_a_room_gets_nothing() -> None:
    narrow = _room(box(0.0, 0.0, 6.0, 1.0), box(0.0, 0.0, 1.5, 1.0))
    assert plan_room(narrow, FURNISH, ASSETS, 48152, "b") == "blocks_path"
    shopfront = _room(ROOM, DOOR_ZONE, display=(WINDOW.exterior,))
    assert _plan_default(shopfront).pieces[0].slot == "counter"
    assert plan_room(shopfront, replace(FURNISH, max_pieces_per_room=0), ASSETS, 48152, "b") == "no_free_wall"
    huge = {"x": _asset("x", (20.0, 0.5, 1.0))}
    assert plan_room(_room(ROOM, DOOR_ZONE), _furnish(counter=["x"], wall=["x"]), huge, 48152, "b") == "no_free_wall"
    other = replace(_room(ROOM, DOOR_ZONE, category="butcher"), room_id="b/L0-2")
    result = _plan_default(other)
    assert result.room_id == "b/L0-2" and result.category == "generic"


def test_furnish_rooms_reads_doors_bands_and_display_spans_of_each_room() -> None:
    from onward_generator.detail.facades import Hole
    from onward_generator.detail.interiors import GLASS_VIEW, SHOP_GLASS, InteriorPlan, Room

    t = CFG.interiors.wall_thickness_m
    door = Hole("interior_door", 0, 9.4, 0.0, 1.2, 2.4, t, None, 2, 0)
    left, right = box(t, t, 12.0, 18.0 - t), box(12.2, t, 24.0 - t, 18.0 - t)
    category = CFG.interiors.shops.categories[0]
    rooms = (Room(left, (door, SEG), category, (0,)), Room(right, None, None, ()))
    plan = InteriorPlan("b", left, 1, 3.0, door, SEG, (), rooms)
    front = Hole("shopfront", 0, 0.5, 0.25, 3.0, 2.3, t, SHOP_GLASS, 0, 0)
    window = Hole("window", 0, 14.0, 0.9, 1.2, 1.4, t, GLASS_VIEW, 3, 0)
    far = Hole("shopfront", 0, 15.5, 0.25, 3.0, 2.3, t, SHOP_GLASS, 4, 0)
    records = [{"id": "b/L0", "ceiling_z_m": 3.0}, {"id": "b/L0-1", "ceiling_z_m": 2.8}]
    a, b = furnish_rooms(plan, records, [(SEG, front), (SEG, window), (SEG, far)], CFG.interiors)
    pane = FURNISH.pane_clear_m
    assert (a.room_id, a.ceiling_m, a.category, b.room_id, b.ceiling_m, b.category) == ("b/L0", 3.0, category.name, "b/L0-1", 2.8, "generic")
    assert [p.bounds for p in a.panes] == [pytest.approx((0.5, 0.0, 3.5, t + pane))]
    assert [p.bounds for p in b.panes] == [pytest.approx((14.0, 0.0, 15.2, t + pane)), pytest.approx((15.5, 0.0, 18.5, t + pane))]
    assert [list(s.coords) for s in a.display] == [[(0.5, t), (3.5, t)]]
    assert [list(s.coords) for s in b.display] == [[(15.5, t), (18.5, t)]]
    assert list(a.door_span.coords) == [(9.4, t), (10.6, t)] and a.door_zone.equals(_door_zone(SEG, door, CFG.interiors))
    assert b.door_zone is None and b.door_span is None
    with pytest.raises(ValueError):
        furnish_rooms(plan, records[:1], [], CFG.interiors)


def test_load_catalogue_gives_exactly_the_rows_of_the_sets_with_their_provenance(tmp_path) -> None:
    import hashlib

    from onward_generator.detail import furnish as module
    from onward_generator.detail.furnish import FurnishError, load_catalogue
    from onward_generator.overgrow.library import read_interior_catalogue

    module._CATALOGUES.clear()
    furnish = _furnish(counter=["kenney_furniture_kitchenCabinet"], wall=["Shelf_01", "wooden_display_shelves_01"])
    assets = load_catalogue(furnish, REPO_ROOT / "assets")
    assert sorted(assets) == ["Shelf_01", "kenney_furniture_kitchenCabinet", "wooden_display_shelves_01"]
    props, _ = read_interior_catalogue(REPO_ROOT / "assets", FURNISH.catalogues, sorted(assets))
    for asset_id, asset in assets.items():
        found = props[asset_id]
        size = tuple(round(v, 6) for v in found.prop.size_m)
        assert asset == FurnishAsset(asset_id, found.catalogue, "CC0-1.0", found.prop.spec.role, found.prop.spec.category, size,
                                     found.prop.triangles, found.glb_sha256, found.parts)
    shelf = (REPO_ROOT / "assets" / "polyhaven_shop" / "Shelf_01.glb").read_bytes()
    assert assets["Shelf_01"].glb_sha256 == hashlib.sha256(shelf).hexdigest()
    assert assets["Shelf_01"].catalogue == "polyhaven_shop" and assets["kenney_furniture_kitchenCabinet"].role == "room_furniture"
    assert sorted(load_catalogue(_furnish(wall=["Shelf_01"]), REPO_ROOT / "assets")) == ["Shelf_01"]
    with pytest.raises(FurnishError) as caught:
        load_catalogue(furnish, tmp_path)
    assert str(caught.value) == (f"shops.furnish: rows the catalogues under {tmp_path} do not give: Shelf_01 (not installed), "
                                 "kenney_furniture_kitchenCabinet (not installed), wooden_display_shelves_01 (not installed)")
    with pytest.raises(FurnishError, match=r"kenney_furniture_kitchenFridge \(alpha_mode_blend\)$"):
        load_catalogue(_furnish(wall=["kenney_furniture_kitchenFridge"]), REPO_ROOT / "assets")


def test_the_map_pass_takes_the_seed_of_the_configuration() -> None:
    from onward_generator.detail.scene import _furnishing

    assert _furnishing(replace(CFG, seed=7)).seed == 7


# ---- The floors of the stair interior (ADR-034) -------------------------------------------------------------------
# A 24 x 18 m block (local metres, origin MURCIA_CENTER), 15 m high in 3 m storeys: with this seed it keeps three
# furnished floors, a storey flight from L0 and from L1, and a roof climb from L2 in two wells.

from onward_generator.detail.config import load_detail_config  # noqa: E402 - the residential fixtures below read the default block
from onward_generator.detail.facades import Hole, Segment  # noqa: E402 - as above
from onward_generator.detail.furnish import FLOOR_SCOPE, ShopFurnishing, floor_rooms  # noqa: E402 - as above
from onward_generator.detail.interiors import GLASS_VIEW, WELL_CLEARANCE_M, build_interior, flight_landings, plan_interior  # noqa: E402 - as above

DEFAULT = load_detail_config()
FLOORS = replace(DEFAULT.interiors.furnish, enabled=True)
RES_SEED = 3  # stable_unit(3, "b", "interior_floors") < 0.5: three furnished floors
BLOCK = Polygon([(0, 0), (24, 0), (24, 18), (0, 18)])
SOUTH = Segment(0, 0, 0, (0.0, 0.0), (24.0, 0.0), True)
FRONT_DOOR = Hole("interior_door", 0, 11.0, 0.0, 1.2, 2.4, 0.3, None, 0, 0)


def _stairs():
    cfg = replace(DEFAULT, seed=RES_SEED)
    plan = plan_interior("b", BLOCK, FRONT_DOOR, SOUTH, 15.0, 3.0, 5, cfg)
    assert not isinstance(plan, str)
    built = build_interior(plan, 15.0, BLOCK, cfg)
    return plan, built


PLAN, BUILT = _stairs()
RECORDS = BUILT.spec["rooms"]
LANDING = DEFAULT.interiors.stair.landing_m
ENDS = [flight_landings(f, LANDING) for f in PLAN.flights]


def _floors(penetrations=()) -> list[FurnishRoom]:
    return floor_rooms(PLAN, RECORDS, list(penetrations), DEFAULT.interiors, FLOORS)


def test_the_fixture_has_three_floors_two_storey_flights_and_a_roof_climb() -> None:
    assert [(f.role, f.level) for f in PLAN.flights] == [("storey", 0), ("storey", 1), ("roof", 2), ("roof", 3), ("roof", 4)]
    assert PLAN.flights[3].well.equals(PLAN.flights[1].well)  # the climb reuses the last storey well two levels up
    assert [r["id"] for r in RECORDS] == ["b/L0", "b/L1", "b/L2"]


def test_floor_rooms_one_per_record_with_floor_z_ceiling_and_entry() -> None:
    rooms = _floors()
    assert [r.room_id for r in rooms] == ["b/L0", "b/L1", "b/L2"]
    assert [(r.floor_m, r.ceiling_m) for r in rooms] == [(0.0, 2.8), (3.0, 5.8), (6.0, 9.0)]
    assert all(r.polygon.equals(PLAN.inner) and r.category == "generic" and r.reach_m == FLOORS.walk_width_m / 2 for r in rooms)
    assert rooms[0].door_zone.equals(_door_zone(SOUTH, FRONT_DOOR, DEFAULT.interiors))
    assert rooms[1].door_zone.equals(ENDS[0][1]) and rooms[2].door_zone.equals(ENDS[1][1])  # the arrival from below
    assert all(r.panes == () and r.display == () and r.door_span is None for r in rooms)


def test_floor_rooms_holes_keepouts_and_targets_per_level() -> None:
    l0, l1, l2 = _floors()
    wells = [f.well for f in PLAN.flights]
    assert [h.bounds for h in l0.holes] == [wells[0].bounds]
    assert [h.bounds for h in l1.holes] == [wells[1].bounds, wells[0].bounds]
    assert [h.bounds for h in l2.holes] == [wells[2].bounds, wells[1].bounds]  # by level: the roof flight of L3 is not L2's
    for room in (l0, l1, l2):
        buffered = [h.buffer(WELL_CLEARANCE_M) for h in room.holes]
        assert all(k.equals(b) for k, b in zip(room.keepouts, buffered, strict=False))
    assert [k.bounds for k in l0.keepouts[1:]] == [ENDS[0][0].bounds]
    assert [k.bounds for k in l1.keepouts[2:]] == [ENDS[1][0].bounds, ENDS[0][1].bounds]
    assert [k.bounds for k in l2.keepouts[2:]] == [ENDS[2][0].bounds, ENDS[1][1].bounds]
    assert [t.bounds for t in l0.targets] == [ENDS[0][0].bounds]
    assert [t.bounds for t in l1.targets] == [ENDS[1][0].bounds]
    assert [t.bounds for t in l2.targets] == [ENDS[2][0].bounds]  # the foot of the roof climb


@pytest.mark.parametrize(("v0", "floors"), [(0.9, ["b/L0"]), (3.0, ["b/L1"]), (3.9, ["b/L1"]), (2.8, []), (6.9, ["b/L2"]), (9.0, [])])
def test_floor_room_panes_only_from_its_own_storey(v0: float, floors: list[str]) -> None:
    window = Hole("window", 0, 14.0, v0, 1.2, 1.4, 0.3, GLASS_VIEW, 3, 0)
    dark = Hole("window", 0, 3.0, v0, 1.2, 1.4, 0.3, "glass_dark", 1, 0)
    rooms = _floors([(SOUTH, window), (SOUTH, dark)])
    assert [r.room_id for r in rooms if r.panes] == floors
    t = DEFAULT.interiors.wall_thickness_m
    for room in rooms:
        assert [p.bounds for p in room.panes] == ([pytest.approx((14.0, 0.0, 15.2, t + FLOORS.pane_clear_m))] if room.panes else [])


def _planner(room: FurnishRoom, furnish: ShopFurnish = FLOORS) -> _Planner:
    return _Planner(room, furnish, ASSETS, 48152, "b")


def _well_room(*, holes=True, targets=()) -> FurnishRoom:
    well = box(4.0, 4.0, 7.0, 5.1)
    return FurnishRoom("b/L1", box(0.0, 0.0, 10.0, 8.0), DOOR_ZONE, 5.8, panes=(), floor_m=3.0,
                       holes=(well,) if holes else (), keepouts=(well.buffer(WELL_CLEARANCE_M),), targets=targets, reach_m=0.6)


@pytest.mark.parametrize(("gap", "reason"), [(0.29, "stair_zone"), (0.31, None)])
def test_no_piece_in_a_well_plus_clearance(gap: float, reason: str | None) -> None:
    assert _planner(_well_room()).conflict(box(7.0 + gap, 4.2, 7.8, 4.8)) == reason


@pytest.mark.parametrize(("level", "zone"), [(0, ("foot", 0)), (1, ("arrival", 0)), (1, ("foot", 1)), (2, ("foot", 2)), (2, ("arrival", 1))])
def test_no_piece_in_a_stair_foot_or_arrival_zone(level: int, zone: tuple[str, int]) -> None:
    room = _floors()[level]
    kind, flight = zone
    rect = ENDS[flight][0 if kind == "foot" else 1]
    inside = rect.buffer(-0.1, join_style="mitre")
    planner = _planner(room)
    expected = "door_zone" if room.door_zone.equals(rect) else "stair_zone"  # the arrival from below is that floor's entry
    assert planner.conflict(inside) == expected
    assert planner.allowed().intersection(rect).area <= 1e-9  # the wall search never offers it either


def test_no_piece_in_the_ground_floor_door_zone_and_none_needed_upstairs() -> None:
    l0, l1, _ = _floors()
    door = _door_zone(SOUTH, FRONT_DOOR, DEFAULT.interiors)
    spot = door.buffer(-0.05, join_style="mitre")
    assert _planner(l0).conflict(spot) == "door_zone"
    assert not any(k.intersects(door) for k in l1.keepouts)  # the precondition of the next line
    assert _planner(l1).conflict(spot) is None


def test_no_piece_in_a_pane_band_and_the_piece_stays_inside_the_polygon_less_wall_gap() -> None:
    window = Hole("window", 0, 14.0, 3.9, 1.2, 1.4, 0.3, GLASS_VIEW, 3, 0)
    l1 = _floors([(SOUTH, window)])[1]
    planner = _planner(l1)
    t = DEFAULT.interiors.wall_thickness_m
    assert planner.conflict(box(14.2, t + FLOORS.pane_clear_m - 0.01, 15.0, 1.0)) == "pane_band"
    assert planner.conflict(box(14.2, t + FLOORS.pane_clear_m + 0.01, 15.0, 1.0)) is None
    assert planner.conflict(box(14.2, t + FLOORS.wall_gap_m - 0.01, 15.0, 1.0).difference(box(0, 0, 24, 0.3 + 0.16))) is None
    assert planner.conflict(box(8.0, 17.7 - FLOORS.wall_gap_m + 0.01, 9.0, 17.0)) == "outside_room"
    assert planner.conflict(box(8.0, 17.0, 9.0, 17.7 - FLOORS.wall_gap_m)) is None


@pytest.mark.parametrize(("height", "fits"), [(5.8 - 3.0 - 0.1, True), (5.8 - 3.0 - 0.1 + 0.01, False)])
def test_head_clear_on_an_upper_floor_both_sides_of_the_boundary(height: float, fits: bool) -> None:
    room = _floors()[1]
    asset = _asset("tall", (1.0, 0.4, height), role="room_furniture")
    assert _planner(room).fits_height(asset) is fits
    assert _planner(replace(room, floor_m=0.0)).fits_height(asset)  # from the ground it would fit: the floor counts
    result = plan_room(room, _furnish(wall=["tall"], max_pieces_per_room=1), {"tall": asset}, 48152, "b")
    if fits:
        assert isinstance(result, RoomFurnishing) and result.pieces[0].z_m == 3.0
    else:
        assert result == "no_free_wall"


def _furnished_stairs(seed: int = 48152, furnish: ShopFurnish = FLOORS, catalogue=None):
    plan, built = _stairs()
    furnishing = ShopFurnishing.of_floors(furnish, catalogue or ASSETS, seed)
    messages = furnishing.furnish_floors("b", plan, built, [], DEFAULT.interiors)
    return furnishing, built, messages


def test_pieces_on_upper_floors_record_z_equal_floor_z_and_ids_continue_across_floors() -> None:
    furnishing, built, messages = _furnished_stairs()
    assert messages == []
    rooms = built.spec["rooms"]
    assert all("furnishing" in r for r in rooms)
    ids = [p["id"] for r in rooms for p in r["furnishing"]["pieces"]]
    assert ids == [f"furnish/b/{n}" for n in range(len(ids))]
    for room in rooms:
        assert room["furnishing"]["pieces"]
        assert {p["position_m"][2] for p in room["furnishing"]["pieces"]} == {room["floor_z_m"]}
    assert furnishing.stats()["floor_rooms"] == furnishing.stats()["furnished_rooms"] == 3


def test_every_furnished_floor_keeps_its_entry_feet_and_well_clear() -> None:
    _, built, _ = _furnished_stairs()
    for room, record in zip(_floors(), built.spec["rooms"], strict=True):
        pieces = [Polygon(p["footprint_m"]) for p in record["furnishing"]["pieces"]]
        for footprint in pieces:
            assert PLAN.inner.buffer(-FLOORS.wall_gap_m + 1e-6, join_style="mitre").contains(footprint)
            for zone in (room.door_zone, *room.keepouts, *room.holes):
                assert footprint.intersection(zone).area <= 1e-9
        walk = PLAN.inner.difference(unary_union([*room.holes, *pieces])).buffer(-FLOORS.walk_width_m / 2)
        reached = [c for c in polygons_of(walk) if c.distance(room.door_zone) <= 0.6 + 1e-9]
        assert reached and all(any(c.distance(t) <= 0.6 + 1e-9 for c in reached) for t in room.targets)


def _corner_room(**kw) -> FurnishRoom:
    target = box(8.95, 0.05, 9.95, 1.15)  # a stair foot in the corner, between two walls and a well
    return FurnishRoom("b/L1", box(0.0, 0.0, 10.0, 6.0), box(0.0, 2.0, 1.5, 3.8), 5.8, floor_m=3.0,
                       keepouts=(target,), targets=kw.pop("targets", (target,)), reach_m=0.6, **kw)


def test_a_piece_that_walls_off_a_stair_foot_cuts_the_path_even_where_nothing_is_cut_off() -> None:
    fence = [box(7.4, 0.0, 8.9, 2.6), box(8.9, 1.2, 10.0, 2.6)]
    assert _planner(_corner_room()).path_cut(fence)
    assert not _planner(_corner_room(targets=())).path_cut(fence)  # the same pieces cut nothing else off
    assert not _planner(_corner_room()).path_cut([box(7.4, 3.0, 8.9, 4.0)])


def test_a_piece_that_would_cut_the_path_to_a_stair_foot_is_refused_blocks_path() -> None:
    target = box(6.9, 0.05, 7.95, 1.15)  # a stair foot at the far end of the corridor from the door
    room = FurnishRoom("b/L1", NARROW, WEST_DOOR, 5.8, floor_m=3.0, keepouts=(target,), targets=(target,), reach_m=0.6)
    deep = _asset("deep", (1.5, 0.72, 2.0))
    result = plan_room(room, _furnish(wall=["deep"], max_pieces_per_room=12), {"deep": deep}, 48152, "b")
    assert result == "blocks_path" or "blocks_path" in result.refusals
    pieces = [] if isinstance(result, str) else [p.footprint for p in result.pieces]
    walk = room.polygon.difference(unary_union(pieces)).buffer(-0.6)
    reached = [c for c in polygons_of(walk) if c.distance(room.door_zone) <= 0.6]
    assert any(c.distance(target) <= 0.6 + 1e-9 for c in reached)


@pytest.mark.parametrize(("shift", "blocked"), [(0.0, True), (0.04, False)])
def test_a_front_zone_over_a_well_blocks(shift: float, blocked: bool) -> None:
    well = box(4.0, 5.0 - shift, 7.0, 6.0 - shift)  # the front of a 0.8 m deep piece on the north wall ends at y 5.97
    room = FurnishRoom("b/L1", box(0.0, 0.0, 10.0, 8.0), DOOR_ZONE, 5.8, floor_m=3.0, holes=(well,), reach_m=0.6)
    planner = _planner(room)
    footprint, front = box(4.5, 7.17, 6.0, 7.97), box(4.5, 5.97, 6.0, 7.17)
    assert planner.blocks(footprint, front) is blocked


def test_an_empty_floor_with_no_path_from_its_entry_to_a_foot_is_refused_whole() -> None:
    target = box(6.5, 0.6, 7.5, 1.7)
    split = FurnishRoom("b/L1", NARROW, WEST_DOOR, 5.8, floor_m=3.0, holes=(box(4.0, 0.05, 5.0, 2.55),), targets=(target,), reach_m=0.6)
    assert plan_room(split, FLOORS, ASSETS, 48152, "b") == "blocks_path"
    whole = replace(split, holes=())
    assert isinstance(plan_room(whole, FLOORS, ASSETS, 48152, "b"), RoomFurnishing)
    alone = replace(split, targets=())  # without the foot, the floor west of the well is furnished
    assert isinstance(plan_room(alone, FLOORS, ASSETS, 48152, "b"), RoomFurnishing)
    none_wanted = replace(FLOORS, max_pieces_per_room=0)  # refused before any piece is tried, not for want of a wall
    assert plan_room(split, none_wanted, ASSETS, 48152, "b") == "blocks_path"
    assert plan_room(alone, none_wanted, ASSETS, 48152, "b") == "no_free_wall"


def _floor_records(**kw) -> str:
    _, built, _ = _furnished_stairs(**kw)
    return json.dumps(built.spec["rooms"], sort_keys=True)


def test_floor_furnishing_is_deterministic_and_set_order_free() -> None:
    first = _floor_records()
    assert first == _floor_records()
    generic = FLOORS.sets["generic"]
    reversed_set = {"generic": FurnishSet((), tuple(reversed(generic.wall)), ())}
    assert _floor_records(furnish=replace(FLOORS, sets=reversed_set), catalogue=dict(reversed(list(ASSETS.items())))) == first
    assert _floor_records(seed=7) != first


def test_residential_budget_wording_and_boundaries() -> None:
    budget = FurnishBudget.of(replace(FLOORS, max_triangles=1000, max_collision_triangles=24, max_assets=1), FLOOR_SCOPE)
    assert budget.scope == "interiors.furnish"
    assert budget.refuse(_furnished(1000)) is None
    assert budget.refuse(_furnished(1001)) == "triangle_budget: needs 1001 triangles, 1000 left of interiors.furnish.max_triangles 1000"
    assert budget.refuse(_furnished(1, solid=2)) is None
    assert budget.refuse(_furnished(1, solid=3)) == (
        "collision_budget: needs 36 collision triangles, 24 left of interiors.furnish.max_collision_triangles 24")
    budget.spend(_furnished(10, asset_id="a"))
    assert budget.refuse(_furnished(1, asset_id="b")) == "asset_cap: needs 2 distinct assets, 1 allowed by interiors.furnish.max_assets"
    shop = FurnishBudget.of(FURNISH)
    assert shop.scope == "shops.furnish" and shop.used == {"triangles": 0, "collision_triangles": 0} and budget.used["triangles"] == 10


def test_room_record_method_residential_furnishing_and_provenance() -> None:
    furnishing, built, _ = _furnished_stairs()
    for room in built.spec["rooms"]:
        block = room["furnishing"]
        assert block["method"] == "residential_furnishing" and block["set"] == "generic" and block["attribute_origin"] == "generated"
        assert set(block["refusals"]) <= {"blocks_path"}  # residential floors have no counter to miss
        for piece in block["pieces"]:
            assert piece["slot"] == "wall" and piece["catalogue"] in FLOORS.catalogues
            assert piece["attribute_origin"] == "generated" and piece["semantics_origin"] == "estimated"
            assert piece["asset_id"] in {e.id for e in FLOORS.sets["generic"].wall}
            assert piece["licence"] == "CC0-1.0" and len(piece["glb_sha256"]) == 64 and piece["triangles"] > 0
            assert isinstance(piece["solid"], bool)
        assert block["collision_triangles"] == COLLIDER_TRIANGLES * sum(p["solid"] for p in block["pieces"])
        assert block["visual_triangles"] == sum(p["triangles"] for p in block["pieces"])
    stats = furnishing.stats()
    assert "shop_rooms" not in stats and stats["max_triangles"] == FLOORS.max_triangles
    assert stats["visual_triangles"] == sum(r["furnishing"]["visual_triangles"] for r in built.spec["rooms"])


def test_the_light_of_each_floor_stays_and_its_probes_leave_its_own_pieces() -> None:
    _, built, _ = _furnished_stairs()
    bare = BUILT.light_anchors["rooms"]
    for before, after, record in zip(bare, built.light_anchors["rooms"], built.spec["rooms"], strict=True):
        assert (after["light_m"], after["range_m"]) == (before["light_m"], before["range_m"])
        pieces = [Polygon(p["footprint_m"]) for p in record["furnishing"]["pieces"]]
        probes = after["probes_m"]
        columns = {(probes[k], probes[k + 1]) for k in range(0, len(probes), 3)}
        assert columns and not any(f.contains(Point(x, y)) for f in pieces for x, y in columns)
        old = before["probes_m"]
        assert columns <= {(old[k], old[k + 1]) for k in range(0, len(old), 3)}


B3_SHOP_CATALOGUES = ["kenney_food", "kenney_furniture", "kenney_mini_shop", "polyhaven_shop"]
B3_SHOP_SETS = {
    "bakery": {"counter": ["kenney_furniture_kitchenCabinet", "kenney_furniture_kitchenCabinetDrawer"],
               "wall": ["Shelf_01", "kenney_furniture_bookcaseOpenLow", "kenney_furniture_bookcaseOpen"],
               "decor": ["kenney_food_loaf", "kenney_food_croissant", "kenney_food_loaf_round", "kenney_food_cake"]},
    "pharmacy": {"counter": ["kenney_furniture_kitchenCabinetDrawer", "kenney_furniture_desk"],
                 "wall": ["kenney_furniture_bookcaseClosedDoors", "kenney_furniture_bookcaseClosed", "Shelf_01"], "decor": []},
    "bar": {"counter": ["kenney_furniture_kitchenBar", "kenney_furniture_kitchenCabinet"],
            "wall": ["kenney_furniture_bookcaseOpenLow", "kenney_furniture_sideTable", "kenney_furniture_cabinetTelevision"],
            "decor": ["kenney_food_cup_coffee", "kenney_food_glass_wine", "kenney_food_wine_red", "kenney_food_soda_bottle"]},
    "generic": {"counter": ["kenney_furniture_kitchenCabinet", "kenney_furniture_desk"],
                "wall": ["kenney_furniture_bookcaseOpen", "Shelf_01", "kenney_furniture_bookcaseClosedWide", "kenney_shop_display_small"],
                "decor": []},
}


def test_shop_planner_unchanged_with_default_fields() -> None:
    """B3 records, pinned from the commit before ADR-034 (the default room fields must not move a shop piece)."""
    import hashlib

    # The pins were taken with the B3 sets (Kenney and Poly Haven only); the Quaternius rows added to the default
    # sets on 10 November 2026 change which piece comes first, not the planner, so the B3 sets are rebuilt here.
    b3 = json.loads(json.dumps(DOC))
    b3["interiors"]["shops"]["furnish"].update(enabled=True, catalogues=B3_SHOP_CATALOGUES, sets=B3_SHOP_SETS, max_pieces_per_room=8)
    shop = config_from_document(b3).interiors.shops.furnish
    pins = {"bar": "90fd63787a4ad3367dd14ae570e08da6fd34d9a7f18dacbc1e9152d7f72acc2f",
            # Re-pinned 10 November 2026: the same pieces in the same places; more of them are solid (SOLID_DEPTH_M 0.2).
            "bakery": "2a7d6461b1ee376bcbe82b914e276ddeee911a7d466e56cf86a1030af12ad39c",
            "generic": "0ba89183c0ab73af63b67caac7ba4f037bea8c71faec8b088e17b7683cb8ef05"}
    for category, pin in pins.items():
        room = FurnishRoom("b/L0", ROOM, DOOR_ZONE, 3.0, category, (box(0.5, -0.3, 2.0, 0.15),), (WINDOW.exterior,))
        result = plan_room(room, shop, ASSETS, 48152, "b")
        assert isinstance(result, RoomFurnishing) and result.method == "shop_furnishing"
        assert hashlib.sha256(json.dumps(result.record("b", 0), sort_keys=True).encode()).hexdigest() == pin


def test_the_floor_pass_takes_the_seed_and_the_block_of_the_configuration() -> None:
    from onward_generator.detail.scene import _floor_furnishing

    cfg = replace(DEFAULT, seed=7, interiors=replace(DEFAULT.interiors, furnish=FLOORS))
    furnishing = _floor_furnishing(cfg)
    assert furnishing.seed == 7 and furnishing.furnish is FLOORS
    assert furnishing.budget.scope == FLOOR_SCOPE and furnishing.method == "residential_furnishing"
