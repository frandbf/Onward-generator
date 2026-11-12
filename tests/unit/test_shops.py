"""Shopfronts, shop doors, and where shop signs and vinyls go (detail stage).

Local metres. A synthetic wall along the x axis faces −y; the building is on +y.
"""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from typing import Any

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
    assert (door.s0, door.v0, door.width, door.height) == pytest.approx((5.45, 0.0, 1.1, 2.3))  # centred in its bay


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
        ({"building": "apartments", "catastro:current_use": "4_1_office"}, ("Oficina", "estimated", "catastro_use=4_1_office")),
        ({"catastro:current_use": "4_2_retail"}, ("Comercio", "estimated", "catastro_use=4_2_retail")),
        ({"catastro:current_use": "1_residential"}, ("Comercio", "estimated", "default")),
        ({"catastro:current_use": "4_1_office", "craft": "carpenter"}, ("Taller", "estimated", "craft")),
        ({"catastro:current_use": "4_1_office", "name": "Torre"}, ("Torre", "observed", "name")),
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
    # Its footprint stands out of the wall, from the bracket's gap to the board's far edge plus the clearance.
    from onward_generator.detail.shops import SIGN_CLEAR_M, sign_footprints

    [(plan, _, _)] = sign_footprints({"objects": [{"type": "building", "facade": {"shops": [record]}}]})
    x0, y0, x1, y1 = plan.bounds
    assert (y0, y1) == pytest.approx((-0.85, -0.15)) and x1 - x0 == pytest.approx(0.04 + 2 * SIGN_CLEAR_M)


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
    assert [len(r["vinyls"]) for r in records] == [1, 1, 1, 0, 0]  # the glass_view window of a shop interior gets one too
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
    assert (cfg.shop_door.width_m, cfg.shop_door.height_m) == (1.1, 2.3)
    assert cfg.shops == CFG.shops and cfg.shopfront == CFG.shopfront
    assert (FASCIA_GAP_M, FASCIA_CLEAR_M) == (0.05, 0.07)
    assert dict(cfg.shop_catastro_uses) == {"4_1_office": "Oficina", "4_2_retail": "Comercio"}


def test_catastro_shop_uses_are_known_codes_with_a_name() -> None:
    cfg = config_from_document(_doc(doors__shop_catastro_uses={"4_2_retail": "Tienda", "3_industrial": "Nave"}))
    assert list(cfg.shop_catastro_uses) == ["3_industrial", "4_2_retail"]
    assert dict(config_from_document(_doc(doors__shop_catastro_uses={})).shop_catastro_uses) == {}
    for bad, fragment in (({"5_retail": "Tienda"}, "is not one of"), ({"4_2_retail": ""}, "should be non-empty")):
        with pytest.raises(DetailConfigError, match=fragment):
            config_from_document(_doc(doors__shop_catastro_uses=bad))
    doc = _doc()
    del doc["doors"]["shop_catastro_uses"]
    with pytest.raises(DetailConfigError, match="'shop_catastro_uses' is a required property"):
        config_from_document(doc)


def _use_case() -> tuple[dict[str, dict], dict[str, list[str]], dict[str, Any]]:
    def use(code: str) -> dict:
        return {"catastro_use": {"current_use": code}}

    feature_of = {"a": use("4_2_retail"), "b": use("4_1_office"), "c": use("1_residential"), "d": {}, "e": use("4_2_retail"),
                  "f": use("4_1_office")}
    templates = {bid: CFG.template_for("commercial" if bid == "f" else "residential", False) for bid in feature_of}
    return feature_of, {"e": ["osm-node-1"], "a": []}, templates


def _sources(ranking: tuple[str, ...], fits: Any = lambda bid: True, places: dict[str, list[str]] | None = None) -> dict[str, str]:
    from onward_generator.detail.scene import _shop_sources

    feature_of, evidence, templates = _use_case()
    return _shop_sources(sorted(feature_of), feature_of, evidence, templates, CFG, ranking, fits, places or {})


def test_a_catastro_use_counts_only_where_osm_has_no_shop_evidence() -> None:
    # e has a POI, f a commercial category; c is residential and d has no use
    assert _sources(("osm", "es.catastro", "overture")) == {"a": "es.catastro", "b": "es.catastro", "e": "osm", "f": "osm"}


def test_the_evidence_ranking_decides_between_osm_and_the_catastro_use() -> None:
    assert _sources(("es.catastro", "osm")) == {"a": "es.catastro", "b": "es.catastro", "e": "es.catastro", "f": "es.catastro"}
    assert _sources(("osm",)) == {"e": "osm", "f": "osm"}
    assert _sources(("overture", "es.catastro")) == {"a": "es.catastro", "b": "es.catastro", "e": "es.catastro", "f": "es.catastro"}


def test_a_catastro_use_where_no_shop_fits_gives_way_to_the_next_source() -> None:
    # no shop fits a, which has no OSM evidence either: it sells nothing
    assert _sources(("es.catastro", "osm"), lambda bid: bid != "a") == {"b": "es.catastro", "e": "es.catastro", "f": "es.catastro"}


def test_overture_places_take_their_rank_in_the_evidence() -> None:
    places = {"a": ["overture-place-1"], "c": ["overture-place-2"], "e": ["overture-place-3"]}
    # default ranking: a keeps its Catastro use, e its OSM POI; only c, residential with no POI, takes its place
    assert _sources(("osm", "es.catastro", "overture"), places=places) == {
        "a": "es.catastro", "b": "es.catastro", "c": "overture", "e": "osm", "f": "osm"}
    assert _sources(("overture", "osm", "es.catastro"), places=places) == {
        "a": "overture", "b": "es.catastro", "c": "overture", "e": "overture", "f": "osm"}
    assert _sources(("osm", "es.catastro"), places=places) == {"a": "es.catastro", "b": "es.catastro", "e": "osm", "f": "osm"}


def _fits(target: Polygon, *others: Polygon) -> bool:
    from shapely import STRtree

    from onward_generator.detail.scene import _shop_fits

    footprints = {"a": target, **{f"n{i}": g for i, g in enumerate(others)}}
    ids = sorted(footprints)
    return _shop_fits("a", ids, footprints, STRtree([footprints[i] for i in ids]), CFG)


def test_a_shop_fits_a_building_with_a_street_front_and_room_for_the_door_clearance() -> None:
    assert CFG.interiors.wall_thickness_m + CFG.interiors.door_clearance_m / 2 == pytest.approx(1.05)
    assert _fits(box(0, 0, 10, 10))
    assert _fits(box(0, 0, 20, 5))  # 5 m deep: the clearance fits
    assert _fits(box(0, 0, 20, 2.2))  # just over 2 x 1.05 m
    assert not _fits(box(0, 0, 20, 1.8))  # a passage strip
    assert not _fits(box(0, 0, 20, 2.0))
    # Closed in on every side, with a neighbour 0.5 m away across a light well: no street front.
    ring = [box(-10, -10, 20, 0), box(-10, 10, 20, 20), box(-10, 0, 0, 10), box(10.5, 0, 20, 10)]
    assert not _fits(box(0, 0, 10, 10), *ring)
    assert _fits(box(0, 0, 10, 10), *ring[:3])  # the east wall is on the street


@pytest.mark.parametrize(
    ("slab", "centre_z"),
    [
        ((0, 1.5, 3.1, 2.85), 2.85 - FASCIA_GAP_M - 0.18),  # Barcelona Sarria: the top was 0.11 m inside this slab
        ((0, 4.1, 5.7, 2.85), 2.85 - FASCIA_GAP_M - 0.18),  # over the board's 0.15 m overhang only
        ((0, 4.16, 5.76, 2.85), None),  # past it
        ((1, 1.5, 3.1, 2.85), None),  # on another wall
        ((0, 1.5, 3.1, 0.25 + 2.3 + FASCIA_GAP_M + 0.36 + FASCIA_GAP_M), None),  # the gap is already kept
        ((0, 1.5, 3.1, 0.25 + 2.3 + FASCIA_GAP_M + 0.36 + FASCIA_GAP_M - 0.01), 2.6 + 0.18 - 0.01),
        ((0, 1.5, 3.1, 2.55), None),  # a slab under the board's bottom is not over it
        ((0, -0.75, 1.0 - 0.15, 2.85), None),  # ending where the board starts
        ((0, -0.5, 0.9, 2.85), 2.85 - FASCIA_GAP_M - 0.18),  # over its left overhang only
        ((0, 1.0 + 3.0 + 0.15, 5.75, 2.85), None),  # starting where it ends
        ((0, 1.5, 3.1, 0.25 + 2.3 + FASCIA_GAP_M), None),  # at the board's bottom exactly
    ],
)
def test_a_fascia_comes_down_under_a_balcony_slab(slab: tuple[int, float, float, float], centre_z: float | None) -> None:
    [record] = plan_shops("b1", [("n1", {"shop": "bakery"}, (2.0, -1.0))], [_front(1.0, 0)], [SEG], _cfg(1.0), slabs=[slab])
    high = 0.25 + 2.3 + FASCIA_GAP_M + 0.18
    assert record["sign"]["centre_m"][2] == pytest.approx(high if centre_z is None else centre_z)


@pytest.mark.parametrize(("gap", "kind"), [(0.9, "fascia"), (1.1, "blade")])
def test_a_blade_with_a_wall_within_1_m_of_a_face_becomes_a_fascia(gap: float, kind: str) -> None:
    """Barcelona Sarria: blades in re-entrant corners had one face 0.7 to 0.8 m from a neighbour's wall."""
    from onward_generator.detail.shops import BLADE_SIDE_CLEAR_M

    x = 1.0 - CFG.min_pier_m / 4  # the blade's plane, beside the window at s0 = 1 m
    own = box(0.0, 0.0, 12.0, 10.0)
    for neighbour in (box(-30.0, -5.0, x - gap, 0.0), box(x + gap, -5.0, 30.0, 0.0)):  # west, then east of the blade
        [record] = plan_shops("b1", [(None, {"shop": "books"}, None)], [_front(1.0, 0)], [SEG], _cfg(0.0), footprints=own | neighbour)
        assert record["sign"]["kind"] == kind
    [alone] = plan_shops("b1", [(None, {"shop": "books"}, None)], [_front(1.0, 0)], [SEG], _cfg(0.0), footprints=own)
    assert alone["sign"]["kind"] == "blade"  # its own building behind the wall is not in front of a face
    [far_out] = plan_shops("b1", [(None, {"shop": "books"}, None)], [_front(1.0, 0)], [SEG], _cfg(0.0),
                           footprints=own | box(x - 0.5, -3.0, x + 0.5, -0.86))  # past the board's far edge, 0.85 m out
    assert far_out["sign"]["kind"] == "blade"
    [near_in] = plan_shops("b1", [(None, {"shop": "books"}, None)], [_front(1.0, 0)], [SEG], _cfg(0.0),
                           footprints=own | box(x - 0.5, -0.84, x + 0.5, -0.8))
    assert near_in["sign"]["kind"] == "fascia"
    assert BLADE_SIDE_CLEAR_M == 1.0


def test_a_blade_on_an_oblique_wall_looks_along_it() -> None:
    from onward_generator.detail.shops import _faces_a_wall

    # A wall along u = (0.8, 0.6), outward n = (0.6, -0.8): the blade's face is -u, its 0.7 m board runs along n.
    sign = {"centre_m": [0.0, 0.0, 2.65], "normal": [-0.8, -0.6], "width_m": 0.7}

    def post(along_u: float, along_n: float) -> Polygon:
        x, y = 0.8 * along_u + 0.6 * along_n, 0.6 * along_u - 0.8 * along_n
        return box(x - 0.01, y - 0.01, x + 0.01, y + 0.01)

    for a, b in ((-0.9, 0.0), (0.9, 0.0), (0.0, 0.3), (0.5, -0.3)):
        assert _faces_a_wall(sign, post(a, b)), (a, b)
    for a, b in ((0.0, 0.4), (-1.1, 0.0), (0.9, -0.4)):
        assert not _faces_a_wall(sign, post(a, b)), (a, b)


def test_a_blade_turned_fascia_still_comes_down_under_a_balcony() -> None:
    x = 1.0 - CFG.min_pier_m / 4
    wall = box(-30.0, -5.0, x - 0.5, 0.0) | box(0.0, 0.0, 12.0, 10.0)
    slab = (0, 0.0, 6.0, 3.0)  # a balcony slab over the window, its bottom at 3 m
    [record] = plan_shops("b1", [(None, {"shop": "books"}, None)], [_front(1.0, 0)], [SEG], _cfg(0.0), slabs=[slab], footprints=wall)
    sign = record["sign"]
    assert sign["kind"] == "fascia"
    assert sign["centre_m"][2] + sign["height_m"] / 2 == pytest.approx(3.0 - FASCIA_GAP_M)
