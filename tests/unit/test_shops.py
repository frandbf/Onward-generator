"""Shopfronts, shop doors, and where shop signs and vinyls go (detail stage).

Local metres. A synthetic wall along the x axis faces −y; the building is on +y.
"""

from __future__ import annotations

import copy
import json
from dataclasses import replace

import pytest
from shapely import Polygon, box

from onward_generator.detail.config import (
    DEFAULT_CONFIG_PATH,
    FASCIA_CLEAR_M,
    FASCIA_GAP_M,
    DetailConfigError,
    config_from_document,
    load_detail_config,
)
from onward_generator.detail.facades import (
    DOOR_BACK,
    Hole,
    Layout,
    Segment,
    Storeys,
    entrance,
    layout_segment,
    shopfront_opening,
    stable_unit,
)
from onward_generator.detail.scene import _shops_of
from onward_generator.detail.shops import plan_shops, shop_name

CFG = load_detail_config()
DOC = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
SEG = Segment(0, 0, 0, (0.0, 0.0), (12.0, 0.0), True)


def _doc(**changes: object) -> dict:
    doc = copy.deepcopy(DOC)
    for path, value in changes.items():
        target = doc
        keys = path.split("__")
        for key in keys[:-1]:
            target = target[key]
        target[keys[-1]] = value
    return doc


def _front(s0: float, column: int, *, back: str = "glass_dark", v0: float = 0.25, height: float = 2.3,
           segment: int = 0) -> Hole:
    return Hole("shopfront", segment, s0, v0, 3.0, height, 0.15, back, column, 0)


# --- Shopfront and shop door ---------------------------------------------------------


def test_shopfront_fills_the_bay_and_the_storey_within_its_limits() -> None:
    small = shopfront_opening(3.0, 3.0, CFG)
    assert small is not None
    assert (small.width_m, small.height_m, small.sill_m) == pytest.approx((2.5, 2.3, 0.25))
    large = shopfront_opening(5.0, 4.0, CFG)
    assert large is not None
    assert (large.width_m, large.height_m) == pytest.approx((3.4, 3.2))


def test_shopfront_is_dropped_below_its_minimum_width_or_height() -> None:
    cfg = config_from_document(_doc(doors__shopfront__head_m=0.5, doors__shopfront__min_height_m=2.0))
    assert shopfront_opening(2.5, 2.75, cfg) is not None  # exactly the minimum width (2.0) and height (2.0)
    assert shopfront_opening(2.49, 2.75, cfg) is None
    assert shopfront_opening(2.5, 2.74, cfg) is None


def test_entrance_kind_and_size() -> None:
    assert entrance(True, True, CFG) == ("landmark_door", CFG.landmark_door)
    assert entrance(False, True, CFG) == ("shop_door", CFG.shop_door)
    assert entrance(False, False, CFG) == ("door", CFG.main_door)
    assert CFG.shop_door != CFG.main_door
    assert DOOR_BACK == {"door": "door_wood", "landmark_door": "door_wood", "shop_door": "glass_dark", "interior_door": None}


def _layout(kind: str, ground: str = "shopfront", storey_m: float = 3.0, depth: float = 0.15) -> Layout:
    layout = Layout()
    door = entrance(kind == "landmark_door", kind == "shop_door", CFG)[1] if kind != "interior_door" else CFG.interiors.door
    layout_segment(SEG, Storeys(1, storey_m, "heights_levels"), CFG.templates["generic_commercial"], ground, door, kind, depth,
                   "b1", box(0, 0, 12, 10), Polygon(), CFG, layout)
    return layout


def test_shop_bays_get_a_glazed_door_and_display_windows_on_a_stallriser() -> None:
    holes = sorted(_layout("shop_door").holes, key=lambda h: h.s0)
    assert [(h.kind, h.back, h.column, h.storey) for h in holes] == [
        ("shopfront", "glass_dark", 0, 0), ("shop_door", "glass_dark", 1, 0), ("shopfront", "glass_dark", 2, 0),
    ]
    first, door, _ = holes
    assert (first.s0, first.v0, first.width, first.height) == pytest.approx((0.85, 0.25, 3.1, 2.3))
    assert (door.s0, door.v0, door.width, door.height) == pytest.approx((5.2, 0.0, 1.6, 2.5))


@pytest.mark.parametrize("kind", ["door", "landmark_door", "interior_door"])
def test_other_entrances_keep_their_back(kind: str) -> None:
    holes = _layout(kind, ground="door_and_windows", storey_m=4.5, depth=0.3).holes
    door = next(h for h in holes if h.kind == kind)
    assert (door.back, door.v0, door.storey, door.depth) == (DOOR_BACK[kind], 0.0, 0, 0.3)
    assert not any(h.kind == "shopfront" for h in holes)
    assert door.back != "glass_dark"


# --- Names -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        ({"shop": "bakery", "name": "  Horno   de  San Juan "}, ("Horno de San Juan", "observed", "name")),
        ({"shop": "bakery", "name": ""}, ("Panadería", "estimated", "shop=bakery")),
        ({"amenity": "pharmacy"}, ("Farmacia", "estimated", "amenity=pharmacy")),
        ({"shop": "vacuum_cleaner"}, ("Tienda", "estimated", "shop")),
        ({"amenity": "pharmacy", "shop": "bakery"}, ("Panadería", "estimated", "shop=bakery")),
        ({"craft": "carpenter"}, ("Taller", "estimated", "craft")),
        ({"building": "retail"}, ("Comercio", "estimated", "default")),
    ],
)
def test_shop_name_is_the_osm_name_or_a_generic_spanish_word(tags: dict, expected: tuple[str, str, str]) -> None:
    assert shop_name(tags, CFG) == dict(zip(("name", "name_origin", "name_rule"), expected, strict=True))


def test_a_key_without_a_generic_word_falls_through_to_the_next_key() -> None:
    cfg = config_from_document(_doc(shops__generic_names={"amenity=bar": "Bar"}))
    assert shop_name({"shop": "bakery", "amenity": "bar"}, cfg)["name"] == "Bar"
    assert shop_name({"shop": "bakery"}, cfg) == {"name": "Comercio", "name_origin": "estimated", "name_rule": "default"}


def test_shops_of_a_building_are_its_pois_or_the_building() -> None:
    tags = {"building": "retail", "name": "Galerías"}
    assert _shops_of([], {}, tags) == [(None, tags, None)]
    features = {"n1": {"geometry": {"type": "Point", "coordinates": [3.5, -1.25]}, "tags": {"shop": "books"}},
                "n2": {"geometry": {"type": "Point", "coordinates": [9.0, -2.0]}}}
    assert _shops_of(["n1", "n2"], features, tags) == [("n1", {"shop": "books"}, (3.5, -1.25)), ("n2", {}, (9.0, -2.0))]


# --- Signs and vinyls ------------------------------------------------------------------


def _cfg(fascia: float, vinyl: float = 0.0, seed: int | None = None) -> object:
    shops = replace(CFG.shops, fascia_share=fascia, vinyl_share=vinyl)
    return replace(CFG, shops=shops, seed=CFG.seed if seed is None else seed)


def test_fascia_spans_the_window_just_above_its_head() -> None:
    [record] = plan_shops("b1", [("n1", {"shop": "bakery"}, (2.0, -1.0))], [_front(1.0, 0)], [SEG], _cfg(1.0))
    assert record["id"] == "shop/b1/n1" and record["poi_id"] == "n1"
    assert record["name"] == "Panadería" and record["name_origin"] == "estimated"
    sign = record["sign"]
    assert sign["kind"] == "fascia" and sign["attribute_origin"] == "generated"
    assert sign["centre_m"] == pytest.approx([2.5, -0.03, 0.25 + 2.3 + FASCIA_GAP_M + 0.18])
    assert sign["normal"] == pytest.approx([0.0, -1.0])
    assert (sign["width_m"], sign["height_m"], sign["depth_m"]) == pytest.approx((3.3, 0.36, 0.06))
    assert sign["double_sided"] is False and sign["bracket_m"] == 0.0
    assert record["vinyls"] == []


def test_blade_sign_hangs_on_a_bracket_beside_the_window() -> None:
    [record] = plan_shops("b1", [(None, {"shop": "books"}, None)], [_front(1.0, 0)], [SEG], _cfg(0.0))
    assert record["id"] == "shop/b1/building" and record["poi_id"] is None
    sign = record["sign"]
    assert sign["kind"] == "blade"
    assert sign["centre_m"] == pytest.approx([1.0 - CFG.min_pier_m / 4, -0.5, 2.65])
    assert sign["normal"] == pytest.approx([-1.0, 0.0])
    assert (sign["width_m"], sign["height_m"], sign["depth_m"]) == pytest.approx((0.7, 0.5, 0.04))
    assert sign["double_sided"] is True and sign["bracket_m"] == pytest.approx(0.85)


def test_sign_kind_is_seeded_per_building_and_shop_and_both_kinds_appear() -> None:
    def kind(seed: int, building: str = "b1") -> str:
        cfg = replace(CFG, seed=seed)
        return plan_shops(building, [("n1", {}, None)], [_front(1.0, 0)], [SEG], cfg)[0]["sign"]["kind"]

    kinds = [kind(seed) for seed in range(40)]
    assert kinds == [kind(seed) for seed in range(40)]
    assert {"fascia", "blade"} == set(kinds)
    assert 8 <= kinds.count("fascia") <= 32
    assert {kind(7, f"b{n}") for n in range(20)} == {"fascia", "blade"}
    for n in range(20):
        expected = "fascia" if stable_unit(7, f"b{n}", "n1", "shop_sign") < CFG.shops.fascia_share else "blade"
        assert kind(7, f"b{n}") == expected
    # The share is a strict upper bound on the draw.
    draw = stable_unit(CFG.seed, "b1", "n1", "shop_sign")
    at = plan_shops("b1", [("n1", {}, None)], [_front(1.0, 0)], [SEG], _cfg(draw))[0]["sign"]["kind"]
    assert at == "blade"


def test_each_shop_takes_the_nearest_free_window_and_owns_the_nearest_vinyls() -> None:
    fronts = [_front(1.0, 0), _front(5.0, 1), _front(9.0, 2, back="glass_view")]
    shops = [("far", {"shop": "books", "name": "Libros"}, (10.0, -3.0)), ("near", {"amenity": "cafe"}, (2.0, -3.0)),
             ("extra", {"shop": "gift"}, (6.0, -3.0)), ("none", {"craft": "x"}, (6.0, -3.0)), ("last", {}, None)]
    records = plan_shops("b1", shops, fronts, [SEG], _cfg(1.0, vinyl=1.0))
    assert [r["poi_id"] for r in records] == ["far", "near", "extra", "none", "last"]
    assert [r["sign"]["centre_m"][0] if r["sign"] else None for r in records[:4]] == pytest.approx([10.5, 2.5, 6.5, None])
    assert records[4]["sign"] is None
    assert [len(r["vinyls"]) for r in records] == [0, 1, 1, 0, 0]  # the glass_view window gets none
    vinyl = records[1]["vinyls"][0]
    assert vinyl["centre_m"] == pytest.approx([2.5, 0.14, 1.6])
    assert vinyl["normal"] == pytest.approx([0.0, -1.0])
    assert (vinyl["width_m"], vinyl["height_m"], vinyl["depth_m"]) == pytest.approx((2.4, 0.26, 0.0))
    assert records[3]["sign"] is None and records[3]["name"] == "Taller"


def test_a_shop_without_a_point_takes_the_first_window_and_vinyls_go_to_the_nearest_sign() -> None:
    fronts = [_front(5.0, 1), _front(1.0, 0), _front(9.0, 2)]
    records = plan_shops("b1", [(None, {}, None)], fronts, [SEG], _cfg(1.0, vinyl=1.0))
    assert records[0]["sign"]["centre_m"][0] == pytest.approx(2.5)
    assert [v["centre_m"][0] for v in records[0]["vinyls"]] == pytest.approx([2.5, 6.5, 10.5])
    two = plan_shops("b1", [("a", {}, (0.0, 0.0)), ("b", {}, (12.0, 0.0))], fronts, [SEG], _cfg(1.0, vinyl=1.0))
    assert [[round(v["centre_m"][0], 6) for v in r["vinyls"]] for r in two] == [[2.5, 6.5], [10.5]]


def test_the_nearest_window_is_measured_from_its_centre() -> None:
    # Centres 2.5 and 6.5: a POI at 4.4 is nearer the first.
    [record] = plan_shops("b1", [("n1", {}, (4.4, -1.0))], [_front(5.0, 1), _front(1.0, 0)], [SEG], _cfg(1.0))
    assert record["sign"]["centre_m"][0] == pytest.approx(2.5)


def test_vinyl_ratio_and_its_strict_bound() -> None:
    fronts = [_front(0.5 + 3.2 * k, k, segment=0) for k in range(40)]
    seg = Segment(0, 0, 0, (0.0, 0.0), (140.0, 0.0), True)
    records = plan_shops("b1", [(None, {}, None)], fronts, [seg], _cfg(1.0, vinyl=0.5))
    assert 10 <= len(records[0]["vinyls"]) <= 30
    assert plan_shops("b1", [(None, {}, None)], fronts, [seg], _cfg(1.0, vinyl=0.0))[0]["vinyls"] == []
    draw = stable_unit(CFG.seed, "b1", 0, 3, "vinyl")
    one = plan_shops("b1", [(None, {}, None)], [fronts[3]], [seg], _cfg(1.0, vinyl=draw))
    assert one[0]["vinyls"] == []
    above = plan_shops("b1", [(None, {}, None)], [fronts[3]], [seg], _cfg(1.0, vinyl=draw + 1e-9))
    assert len(above[0]["vinyls"]) == 1


@pytest.mark.parametrize(("v0", "height", "z"), [(1.5, 2.0, 1.63), (0.0, 1.0, 0.87), (0.25, 2.3, 1.6)])
def test_vinyl_stays_on_its_window(v0: float, height: float, z: float) -> None:
    [record] = plan_shops("b1", [(None, {}, None)], [_front(1.0, 0, v0=v0, height=height)], [SEG], _cfg(1.0, vinyl=1.0))
    assert record["vinyls"][0]["centre_m"][2] == pytest.approx(z)


def test_no_shopfront_means_no_sign() -> None:
    door = Hole("shop_door", 0, 5.0, 0.0, 1.6, 2.5, 0.15, "glass_dark", 1, 0)
    assert plan_shops("b1", [(None, {}, (0.0, 0.0))], [door], [SEG], _cfg(1.0, 1.0))[0]["sign"] is None


# --- Configuration -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "problem"),
    [
        ({"doors__shopfront__min_width_m": 3.5}, "/doors/shopfront: a minimum exceeds its maximum"),
        ({"doors__shopfront__min_height_m": 3.3}, "/doors/shopfront: a minimum exceeds its maximum"),
        ({"doors__shopfront__head_m": 0.4}, "/shops/fascia/height_m: plus 0.07 m does not fit in /doors/shopfront/head_m"),
        ({"shops__fascia__overhang_m": 0.26}, "/shops/fascia/overhang_m: two overhangs exceed /openings/min_pier_m"),
        ({"openings__min_pier_m": 0.3, "shops__blade__thickness_m": 0.16}, "/shops/blade/thickness_m: exceeds half of /openings/min_pier_m"),
        ({"doors__shopfront__min_height_m": 0.9, "shops__vinyl__height_m": 0.95}, "/shops/vinyl/height_m: taller than /doors/shopfront/min_height_m"),
    ],
)
def test_inconsistent_shop_config_is_rejected(changes: dict, problem: str) -> None:
    with pytest.raises(DetailConfigError) as raised:
        config_from_document(_doc(**changes))
    assert raised.value.problems == [problem]


@pytest.mark.parametrize(
    ("changes", "fragment"),
    [
        ({"shops__fascia_share": 1.5}, "maximum"),
        ({"shops__vinyl_share": -0.1}, "minimum"),
        ({"shops__generic_names": {"Shop=Bakery": "Panadería"}}, "does not match"),
        ({"shops__default_name": ""}, "should be non-empty"),
        ({"doors__shopfront": {"width_m": 2.4, "height_m": 2.5}}, "required property"),
    ],
)
def test_invalid_shop_config_is_rejected(changes: dict, fragment: str) -> None:
    with pytest.raises(DetailConfigError, match=fragment):
        config_from_document(_doc(**changes))


def test_shop_config_limits_are_inclusive() -> None:
    config_from_document(_doc(doors__shopfront__min_width_m=3.4, doors__shopfront__min_height_m=3.2,
                              shops__vinyl__height_m=1.0))
    config_from_document(_doc(doors__shopfront__head_m=0.36 + FASCIA_CLEAR_M, shops__fascia__overhang_m=0.25))
    config_from_document(_doc(openings__min_pier_m=0.3, shops__fascia__overhang_m=0.15, shops__blade__thickness_m=0.15))
    config_from_document(_doc(doors__shopfront__min_height_m=0.9, shops__vinyl__height_m=0.9))
    doc = _doc()
    del doc["shops"]
    with pytest.raises(DetailConfigError, match="'shops' is a required property"):
        config_from_document(doc)


def test_shop_config_is_read_as_given() -> None:
    cfg = config_from_document(_doc())
    shops = cfg.shops
    assert (shops.fascia_share, shops.vinyl_share) == (0.5, 0.5)
    assert (shops.fascia_height_m, shops.fascia_overhang_m, shops.fascia_depth_m) == (0.36, 0.15, 0.06)
    assert (shops.blade_width_m, shops.blade_height_m, shops.blade_thickness_m, shops.blade_wall_gap_m,
            shops.blade_clearance_m) == (0.7, 0.5, 0.04, 0.15, 2.4)
    assert (shops.vinyl_height_m, shops.vinyl_centre_m, shops.vinyl_width_share) == (0.26, 1.6, 0.8)
    assert shops.default_name == "Comercio" and shops.generic_names["amenity=pharmacy"] == "Farmacia"
    assert list(shops.generic_names) == sorted(shops.generic_names)
    assert (cfg.shopfront.min_width_m, cfg.shopfront.max_width_m, cfg.shopfront.sill_m, cfg.shopfront.min_height_m,
            cfg.shopfront.max_height_m, cfg.shopfront.head_m) == (2.0, 3.4, 0.25, 1.9, 3.2, 0.45)
    assert (cfg.shop_door.width_m, cfg.shop_door.height_m) == (1.6, 2.5)
    assert cfg.shops == CFG.shops and cfg.shopfront == CFG.shopfront
    assert (FASCIA_GAP_M, FASCIA_CLEAR_M) == (0.05, 0.07)
