"""Shop interiors: configuration, category colours, one-storey plan, and the shared light cap.

Local metres. The fixture origin is MURCIA_CENTER.
"""

from __future__ import annotations

import copy
import json
import math

import numpy as np
import pytest
from shapely import Polygon

from onward_generator.detail.config import DEFAULT_CONFIG_PATH, DetailConfigError, config_from_document, load_detail_config
from onward_generator.detail.facades import Hole, Segment
from onward_generator.detail.interiors import MIN_ROOM_M2, build_interior, cap_lights, plan_interior, room_ceiling
from onward_generator.detail.shops import VINYL_BACKS, plan_shops, shop_category
from onward_generator.export.lightmap import surface_class
from tests.conftest import MURCIA_CENTER
from tests.integration.test_detail_pipeline import _inside

CFG = load_detail_config()
DOC = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
SEG = Segment(0, 0, 0, (0.0, 0.0), (24.0, 0.0), True)
FOOTPRINT = Polygon([(0.0, 0.0), (24.0, 0.0), (24.0, 18.0), (0.0, 18.0)])
DOOR = Hole("interior_door", 0, 11.0, 0.0, 1.2, 2.4, CFG.interiors.wall_thickness_m, None, 0, 0)


def _area(mesh) -> float:
    triangles = mesh.vertices[mesh.triangles]
    return float(0.5 * np.linalg.norm(np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]), axis=1).sum())


def _doc(**changes: object) -> dict:
    doc = copy.deepcopy(DOC)
    for path, value in changes.items():
        target = doc
        keys = path.split("__")
        for key in keys[:-1]:
            target = target[key]
        target[keys[-1]] = value
    return doc


# --- Configuration -------------------------------------------------------------------------


def test_shop_interior_config_is_read_as_given() -> None:
    assert MURCIA_CENTER == (-1.12863, 37.98407)
    cfg = config_from_document(_doc())
    rooms = cfg.interiors.shops
    assert (rooms.enabled, rooms.max_triangles, rooms.max_area_m2, cfg.interiors.lights_max) == (True, 6000, 600.0, 160)
    assert (rooms.band_depth_m, rooms.min_room_area_m2) == (12.0, 9.0)
    assert [(c.name, c.wall, c.floor) for c in rooms.categories] == [
        ("bakery", "interior_shop_bakery", "interior_shop_tile"),
        ("pharmacy", "interior_shop_pharmacy", "interior_shop_tile"),
        ("bar", "interior_shop_bar", "interior_shop_wood"),
    ]
    assert rooms.categories[1].tags == {"amenity": {"pharmacy"}, "healthcare": {"pharmacy"}, "shop": {"chemist"}}
    assert list(rooms.categories[1].tags) == ["amenity", "healthcare", "shop"]
    assert (rooms.generic.name, rooms.generic.wall, rooms.generic.floor, dict(rooms.generic.tags)) == (
        "generic", "interior_shop_generic", "interior_shop_wood", {})
    # Six shared materials for every shop, all albedo-only and in the interior lightmap class.
    names = {m for c in (*rooms.categories, rooms.generic) for m in (c.wall, c.floor)}
    assert len(names) == 6 and all(name in cfg.materials and surface_class(name) == "interior" for name in names)
    assert not rooms.generic.matches({"shop": "bakery"}) and rooms.generic.tags == {}


@pytest.mark.parametrize(
    ("changes", "problem"),
    [
        ({"interiors__shops__generic__wall": "brick_red"},
         "/interiors/shops/generic: material 'brick_red' must be defined in /materials and start with 'interior_'"),
        ({"interiors__shops__generic__floor": "interior_nope"},
         "/interiors/shops/generic: material 'interior_nope' must be defined in /materials and start with 'interior_'"),
        ({"interiors__shops__categories": [{"name": "bar", "tags": {"amenity": ["bar"]}, "wall": "interior_wall", "floor": "plaster_white"}]},
         "/interiors/shops/bar: material 'plaster_white' must be defined in /materials and start with 'interior_'"),
        ({"interiors__shops__categories": [{"name": "generic", "tags": {"amenity": ["bar"]}, "wall": "interior_wall", "floor": "interior_floor"}]},
         "/interiors/shops/categories: names must be unique and not 'generic'"),
        ({"interiors__shops__categories": [{"name": "bar", "tags": {"amenity": ["bar"]}, "wall": "interior_wall", "floor": "interior_floor"}] * 2},
         "/interiors/shops/categories: names must be unique and not 'generic'"),
    ],
)
def test_inconsistent_shop_interior_config_is_rejected(changes: dict, problem: str) -> None:
    with pytest.raises(DetailConfigError) as raised:
        config_from_document(_doc(**changes))
    assert raised.value.problems == [problem]


@pytest.mark.parametrize(
    ("changes", "fragment"),
    [
        ({"interiors__lights_max": 257}, "maximum"),
        ({"interiors__lights_max": -1}, "minimum"),
        ({"interiors__shops__max_triangles": -1}, "minimum"),
        ({"interiors__shops__max_area_m2": 0}, "exclusiveMinimum|less than or equal"),
        ({"interiors__shops__band_depth_m": 2.9}, "minimum"),
        ({"interiors__shops__band_depth_m": 50.1}, "maximum"),
        ({"interiors__shops__min_room_area_m2": 3.9}, "minimum"),
        ({"interiors__shops__min_room_area_m2": 100.1}, "maximum"),
        ({"interiors__shops__categories": [{"name": "bar", "tags": {}, "wall": "interior_wall", "floor": "interior_floor"}]},
         "should be non-empty|does not have enough properties"),
    ],
)
def test_invalid_shop_interior_config_is_rejected(changes: dict, fragment: str) -> None:
    with pytest.raises(DetailConfigError, match=fragment):
        config_from_document(_doc(**changes))


def test_shop_interior_config_is_required_and_its_limits_are_inclusive() -> None:
    config_from_document(_doc(interiors__lights_max=0, interiors__shops__max_triangles=0, interiors__shops__categories=[]))
    config_from_document(_doc(interiors__lights_max=256))
    config_from_document(_doc(interiors__shops__band_depth_m=3, interiors__shops__min_room_area_m2=4))
    config_from_document(_doc(interiors__shops__band_depth_m=50, interiors__shops__min_room_area_m2=100))
    for key in ("band_depth_m", "min_room_area_m2"):
        doc = _doc()
        del doc["interiors"]["shops"][key]
        with pytest.raises(DetailConfigError, match=f"'{key}' is a required property"):
            config_from_document(doc)
    for key in ("shops", "lights_max"):
        doc = _doc()
        del doc["interiors"][key]
        with pytest.raises(DetailConfigError, match=f"'{key}' is a required property"):
            config_from_document(doc)


# --- Category ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("shops", "category"),
    [
        ([{"shop": "bakery"}], "bakery"),
        ([{"shop": "confectionery", "name": "Dulces"}], "bakery"),
        ([{"amenity": "pharmacy"}], "pharmacy"),
        ([{"healthcare": "pharmacy"}], "pharmacy"),
        ([{"shop": "chemist"}], "pharmacy"),
        ([{"amenity": "cafe"}], "bar"),
        ([{"shop": "books"}], "generic"),
        ([{"amenity": "bakery"}], "generic"),  # the tag key matters, not only the value
        ([{}], "generic"),
        ([], "generic"),
        ([{"shop": "books"}, {"amenity": "bar"}, {"shop": "bakery"}], "bar"),  # the first shop that matches
        ([{"amenity": "bar", "shop": "bakery"}], "bakery"),  # categories in their configured order
    ],
)
def test_shop_category_is_the_first_matching_shop_or_generic(shops: list[dict], category: str) -> None:
    assert shop_category(shops, CFG).name == category


# --- Plan and build ------------------------------------------------------------------------


def test_a_shop_plan_has_one_floor_and_no_stair() -> None:
    plan = plan_interior("shop", FOOTPRINT, DOOR, SEG, 18.0, 3.0, 6, CFG, roof_access=False, max_floors=1)
    assert not isinstance(plan, str)
    assert (plan.floors, plan.flights) == (1, ())
    tall = plan_interior("block", FOOTPRINT, DOOR, SEG, 18.0, 3.0, 6, CFG)
    assert not isinstance(tall, str) and tall.floors > 1 and tall.flights
    two = plan_interior("block", FOOTPRINT, DOOR, SEG, 18.0, 3.0, 6, CFG, max_floors=2)
    assert not isinstance(two, str) and (two.floors, len(two.flights)) == (2, 1 + sum(f.role == "roof" for f in two.flights))
    # A shop may use its only storey and has no stair; a one-storey home has its room under the roof and a climb to it.
    low = plan_interior("low", FOOTPRINT, DOOR, SEG, 3.0, 3.0, 1, CFG, max_floors=1)
    assert not isinstance(low, str) and (low.floors, low.flights) == (1, ())
    home = plan_interior("low", FOOTPRINT, DOOR, SEG, 3.0, 3.0, 1, CFG)
    assert not isinstance(home, str) and home.floors == 1 and [f.role for f in home.flights] == ["roof"]
    assert plan_interior("none", FOOTPRINT, DOOR, SEG, 3.0, 3.0, 0, CFG) == "not_enough_levels"
    assert plan_interior("none", FOOTPRINT, DOOR, SEG, 3.0, 3.0, 0, CFG, max_floors=1) == "not_enough_levels"
    four = plan_interior("block", FOOTPRINT, DOOR, SEG, 12.0, 3.0, 4, CFG, max_floors=3)
    assert not isinstance(four, str) and four.floors >= 2


def test_a_one_storey_shop_room_reaches_the_roof_slab() -> None:
    plan = plan_interior("kiosk", FOOTPRINT, DOOR, SEG, 3.0, 3.0, 1, CFG, roof_access=False, max_floors=1)
    assert not isinstance(plan, str)
    slab = CFG.interiors.slab_m
    assert room_ceiling(plan, 3.0, slab) == pytest.approx(3.0 - slab)
    built = build_interior(plan, 3.0, 3.0, FOOTPRINT, CFG, wall_material="interior_shop_bar", floor_material="interior_shop_wood")
    assert built.defects == 0
    names = [n for n, _ in built.colliders]
    assert names[-1] == "roof_slab" and "upper_solid" not in names
    roof = dict(built.colliders)["roof_slab"]
    assert (roof.vertices[:, 2].min(), roof.vertices[:, 2].max()) == pytest.approx((3.0 - slab, 3.0))
    [room] = built.spec["rooms"]
    assert room["ceiling_z_m"] == pytest.approx(3.0 - slab)
    [ceiling] = built.visual["interior_wall"]
    assert set(ceiling.vertices[:, 2].round(9)) == {round(3.0 - slab, 9)}
    [walls] = built.visual["interior_shop_bar"]
    assert walls.vertices[:, 2].max() == pytest.approx(3.0 - slab)
    assert built.light_anchors["rooms"][0]["light_m"][2] == pytest.approx(3.0 - slab - 0.3)


def test_rooms_of_a_stair_interior_keep_their_slabs_and_area() -> None:
    odd = Polygon([(0.0, 0.0), (24.0007, 0.0), (24.0007, 18.0003), (0.0, 18.0003)])
    s = 3.0007  # a storey whose slab line has more than three decimals
    plan = plan_interior("block", odd, DOOR, SEG, 6 * s, s, 6, CFG)
    assert not isinstance(plan, str) and plan.floors >= 2
    built = build_interior(plan, 6 * s, 6 * s, odd, CFG)
    area = plan.inner.area
    assert round(area, 3) != round(area, 4)
    slab = CFG.interiors.slab_m
    assert built.spec["rooms"] == [
        {"id": f"block/L{i}", "level": i, "floor_z_m": round(i * s, 3),
         "ceiling_z_m": round(s * plan.floors if i + 1 == plan.floors else (i + 1) * s - slab, 3), "area_m2": round(area, 3)}
        for i in range(plan.floors)
    ]
    assert built.spec["rooms"][0]["ceiling_z_m"] == 2.801


@pytest.mark.parametrize(("height", "ceiling"), [(9.0, 3.0), (3.0, 2.8), (3.0005, 2.8005), (3.001, 2.801), (3.002, 3.0)])
def test_room_ceiling_is_the_storey_line_or_the_roof_slab(height: float, ceiling: float) -> None:
    plan = plan_interior("shop", FOOTPRINT, DOOR, SEG, 9.0, 3.0, 3, CFG, max_floors=1)
    assert not isinstance(plan, str)
    assert room_ceiling(plan, height, 0.2) == pytest.approx(ceiling)


def test_a_shop_room_paints_its_walls_and_floor_and_keeps_a_white_ceiling() -> None:
    plan = plan_interior("shop", FOOTPRINT, DOOR, SEG, 18.0, 3.0, 6, CFG, roof_access=False, max_floors=1)
    assert not isinstance(plan, str)
    built = build_interior(plan, 18.0, 18.0, FOOTPRINT, CFG, wall_material="interior_shop_bar", floor_material="interior_shop_wood")
    used = {name: sum(m.triangle_count for m in meshes) for name, meshes in built.visual.items() if meshes}
    plain = build_interior(plan, 18.0, 18.0, FOOTPRINT, CFG)
    default = {name: sum(m.triangle_count for m in meshes) for name, meshes in plain.visual.items() if meshes}
    assert set(used) == {"interior_shop_bar", "interior_shop_wood", "interior_wall"}
    assert used["interior_shop_wood"] == default["interior_floor"] == 2
    assert used["interior_shop_bar"] + used["interior_wall"] == default["interior_wall"]
    assert used["interior_wall"] == 2  # the ceiling under the solid upper storeys
    [floor] = built.visual["interior_shop_wood"]
    assert set(floor.vertices[:, 2]) == {0.0}
    # The walls are the inner ring, storey high, less the whole doorway from the floor up.
    inner = FOOTPRINT.buffer(-CFG.interiors.wall_thickness_m, join_style="mitre")
    [walls] = built.visual["interior_shop_bar"]
    assert _area(walls) == pytest.approx(inner.length * 3.0 - DOOR.width * DOOR.height)
    assert built.visual_triangles == plain.visual_triangles
    assert [c["type"] for c in built.spec["connections"]] == ["door"]
    assert [n for n, _ in built.colliders][-1] == "upper_solid" and built.defects == 0
    assert len(built.light_anchors["rooms"]) == 1


# Two rooms joined by a 0.4 m neck: an inset of 0.3 m walls splits it in two pieces.
NECK = Polygon([(0.0, 0.0), (24.0, 0.0), (24.0, 8.0), (12.2, 8.0), (12.2, 10.0), (24.0, 10.0), (24.0, 18.0),
                (0.0, 18.0), (0.0, 10.0), (11.8, 10.0), (11.8, 8.0), (0.0, 8.0)])


def test_a_neck_narrower_than_two_walls_leaves_the_room_behind_the_door_and_the_rest_solid() -> None:
    t = CFG.interiors.wall_thickness_m
    assert NECK.buffer(-t, join_style="mitre", mitre_limit=2.0).geom_type == "MultiPolygon"
    plan = plan_interior("neck", NECK, DOOR, SEG, 3.0, 3.0, 1, CFG, roof_access=False, max_floors=1)
    assert not isinstance(plan, str)
    assert plan.inner.bounds == pytest.approx((t, t, 24.0 - t, 8.0 - t))  # the piece the door opens into
    assert plan.depth_m is None
    built = build_interior(plan, 3.0, 3.0, NECK, CFG)
    assert built.defects == 0 and "room_depth_m" not in built.spec
    assert not any(_inside(m, np.array([12.0, 4.0, 1.0])) for _, m in built.colliders)
    assert any(_inside(m, np.array([12.0, 13.0, 1.0])) for _, m in built.colliders)  # the far piece stays solid
    # A door on the far side opens into the far piece.
    top = Segment(1, 0, 0, (24.0, 18.0), (0.0, 18.0), True)
    far = plan_interior("neck", NECK, Hole("interior_door", 1, 11.0, 0.0, 1.2, 2.4, t, None, 0, 0), top, 3.0, 3.0, 1, CFG,
                        roof_access=False, max_floors=1)
    assert not isinstance(far, str) and far.inner.bounds == pytest.approx((t, 10.0 + t, 24.0 - t, 18.0 - t))


def test_a_door_on_no_inset_piece_is_refused_after_the_largest_piece_is_taken() -> None:
    lost = Hole("interior_door", 0, 30.0, 0.0, 1.2, 2.4, CFG.interiors.wall_thickness_m, None, 0, 0)
    assert plan_interior("neck", NECK, lost, SEG, 3.0, 3.0, 1, CFG, max_floors=1) == "door_not_on_inner_wall"


def test_a_shop_room_can_be_cut_to_a_band_behind_its_door_wall() -> None:
    t = CFG.interiors.wall_thickness_m
    plan = plan_interior("big", FOOTPRINT, DOOR, SEG, 3.0, 3.0, 1, CFG, roof_access=False, max_floors=1, depth_m=6.0)
    assert not isinstance(plan, str) and plan.depth_m == 6.0
    assert plan.inner.bounds == pytest.approx((t, t, 24.0 - t, 6.0))
    built = build_interior(plan, 3.0, 3.0, FOOTPRINT, CFG)
    assert built.defects == 0 and built.spec["room_depth_m"] == 6.0
    assert built.spec["rooms"][0]["area_m2"] == pytest.approx((24.0 - 2 * t) * (6.0 - t), abs=1e-3)
    # The back wall closes the room: the floor behind it is solid.
    assert not any(_inside(m, np.array([12.0, 5.5, 1.0])) for _, m in built.colliders)
    assert any(_inside(m, np.array([12.0, 6.5, 1.0])) for _, m in built.colliders)
    [walls] = built.visual["interior_wall"][:1]
    assert walls.vertices[:, 1].max() == pytest.approx(6.0)
    # The band reaches a plan of several floors too.
    tall = plan_interior("big", FOOTPRINT, DOOR, SEG, 18.0, 3.0, 6, CFG, depth_m=12.0)
    assert not isinstance(tall, str) and tall.flights and tall.depth_m == 12.0
    # A band deeper than the footprint leaves the whole room.
    deep = plan_interior("big", FOOTPRINT, DOOR, SEG, 3.0, 3.0, 1, CFG, max_floors=1, depth_m=30.0)
    assert not isinstance(deep, str) and deep.inner.area == pytest.approx((24.0 - 2 * t) * (18.0 - 2 * t))


@pytest.mark.parametrize(("width", "minimum", "result"), [(4.0, 9.0, None), (4.0, 20.0, "inner_space_too_small"),
                                                          (5.0, MIN_ROOM_M2, "inner_space_too_small"), (6.6, MIN_ROOM_M2, None),
                                                          (0.5, 4.0, "inner_space_too_small")])
def test_the_smallest_room_is_a_parameter(width: float, minimum: float, result: str | None) -> None:
    # A 4.6 m deep footprint: the inset room is (width - 0.6) x 4.0 m.
    kiosk = Polygon([(9.0, 0.0), (9.0 + width, 0.0), (9.0 + width, 4.6), (9.0, 4.6)])
    door = Hole("interior_door", 0, 9.0 + (width - 1.2) / 2, 0.0, 1.2, 2.4, CFG.interiors.wall_thickness_m, None, 0, 0)
    plan = plan_interior("kiosk", kiosk, door, SEG, 3.0, 3.0, 1, CFG, max_floors=1, min_area_m2=minimum)
    assert (plan if isinstance(plan, str) else None) == result
    if result is None:
        assert plan.inner.area >= minimum
    assert MIN_ROOM_M2 == 20.0


def test_a_room_of_exactly_the_smallest_area_is_kept() -> None:
    kiosk = Polygon([(9.0, 0.0), (13.0, 0.0), (13.0, 4.6), (9.0, 4.6)])
    door = Hole("interior_door", 0, 10.4, 0.0, 1.2, 2.4, CFG.interiors.wall_thickness_m, None, 0, 0)
    first = plan_interior("kiosk", kiosk, door, SEG, 3.0, 3.0, 1, CFG, max_floors=1, min_area_m2=4.0)
    assert not isinstance(first, str)
    area = first.inner.area
    assert not isinstance(plan_interior("kiosk", kiosk, door, SEG, 3.0, 3.0, 1, CFG, max_floors=1, min_area_m2=area), str)
    assert plan_interior("kiosk", kiosk, door, SEG, 3.0, 3.0, 1, CFG, max_floors=1, min_area_m2=area + 1e-9) == "inner_space_too_small"


# --- Vinyls --------------------------------------------------------------------------------


@pytest.mark.parametrize(("back", "count"), [("glass_dark", 1), ("glass_view", 1), ("shop_glass", 1), ("glass_boarded", 0), ("door_wood", 0)])
def test_vinyls_go_on_dark_and_see_through_glass_only(back: str, count: int) -> None:
    assert frozenset({"glass_dark", "glass_view", "shop_glass"}) == VINYL_BACKS
    front = Hole("shopfront", 0, 1.0, 0.25, 3.0, 2.3, 0.3, back, 0, 0)
    cfg = config_from_document(_doc(shops__vinyl_share=1.0))
    [record] = plan_shops("b1", [(None, {}, None)], [front], [SEG], cfg)
    assert len(record["vinyls"]) == count
    # Each vinyl names the glass it sits on, so ``signs`` draws a decal on shop glass.
    assert [v["glass"] for v in record["vinyls"]] == [back] * count


# --- Light cap -----------------------------------------------------------------------------


def _anchors(*rooms: tuple[str, float, float]) -> dict:
    return {"rooms": [{"room_id": rid, "light_m": [x, y, 2.7], "range_m": 5.0, "probes_m": [x, y, 1.0]} for rid, x, y in rooms]}


def test_cap_keeps_shops_first_then_the_rooms_nearest_the_centre() -> None:
    home = _anchors(("h/L0", 1.0, 0.0), ("h/L1", 1.0, 0.0))
    far_shop = _anchors(("s1/L0", 30.0, 40.0))
    near_shop = _anchors(("s2/L0", 0.0, -20.0))
    other = _anchors(("o/L0", 0.0, 3.0))
    interiors = [(False, home), (True, far_shop), (True, near_shop), (False, other)]
    assert cap_lights(interiors, 3) == ["h/L1", "o/L0"]
    kept = [r["room_id"] for _, a in interiors for r in a["rooms"] if "light_m" in r]
    assert kept == ["h/L0", "s1/L0", "s2/L0"]
    for _, anchors in interiors:
        for room in anchors["rooms"]:
            assert room["probes_m"]
            if "light_m" not in room:
                assert room["light"] == "over_cap" and "range_m" not in room
            else:
                assert "light" not in room and room["range_m"] == 5.0


@pytest.mark.parametrize(("limit", "dropped"), [(0, ["a/L0", "b/L0", "c/L0"]), (1, ["b/L0", "c/L0"]), (2, ["c/L0"]), (3, []), (9, [])])
def test_cap_drops_every_room_past_the_limit(limit: int, dropped: list[str]) -> None:
    rooms = [(False, _anchors(("c/L0", 0.0, 3.0))), (False, _anchors(("a/L0", 1.0, 0.0))), (False, _anchors(("b/L0", 0.0, 2.0)))]
    assert cap_lights(rooms, limit) == dropped


@pytest.mark.parametrize(("near", "far"), [((5.0, 0.0), (0.0, 10.0)), ((0.0, 5.0), (10.0, 0.0)), ((3.0, 3.0), (0.0, 4.5))])
def test_cap_measures_both_plane_axes(near: tuple[float, float], far: tuple[float, float]) -> None:
    rooms = [(False, _anchors(("a/L0", *far))), (False, _anchors(("b/L0", *near)))]
    assert cap_lights(rooms, 1) == ["a/L0"]


def test_cap_breaks_distance_ties_by_room_id_and_measures_in_the_plane() -> None:
    rooms = [(False, _anchors(("b/L0", 3.0, 4.0))), (False, _anchors(("a/L0", -4.0, 3.0))), (False, _anchors(("c/L0", 0.0, 5.01)))]
    rooms[1][1]["rooms"][0]["light_m"][2] = 99.0  # height does not count
    assert cap_lights(rooms, 1) == ["b/L0", "c/L0"]
    assert math.hypot(3.0, 4.0) == 5.0
