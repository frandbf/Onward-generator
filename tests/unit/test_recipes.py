"""Sets chosen from the described library by room type, and the zoned planner that places them (ADR-036)."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from shapely import box

from onward_generator.detail import recipes
from onward_generator.detail.config import ALL_SLOTS, ZONE_SLOTS, FurnishEntry, FurnishSet, load_detail_config
from onward_generator.detail.furnish import (
    AREA_PER_PIECE_M2,
    CAP_FREE,
    MIN_PIECES,
    ON_TOP,
    FurnishAsset,
    FurnishRoom,
    RoomFurnishing,
    _Planner,
    plan_room,
)
from onward_generator.detail.recipes import Candidate, choose, library, semantic_sets, with_semantic_sets

SEED = 48152


def _c(asset_id: str, kind: str, size=(1.0, 0.4, 1.8), triangles=100, settings=("shop",), catalogue="cat") -> Candidate:
    return Candidate(asset_id, catalogue, kind, frozenset(settings), *size, triangles)


# ---- choose ---------------------------------------------------------------------------------------------------------


def test_choose_takes_kinds_in_turn_and_stops_at_the_slot_limit() -> None:
    pool = [_c(f"tall{i}", "shelf_tall", triangles=100 + i) for i in range(6)] + [
        _c(f"low{i}", "shelf_low", size=(1.0, 0.4, 0.9), triangles=100 + i) for i in range(6)]
    chosen = choose("wall", ("shelf_tall", "shelf_low"), ("shop",), pool, SEED, "k")
    assert len(chosen) == recipes.MODELS_PER_SLOT["wall"] == 5
    kinds = ["tall" if e.id.startswith("tall") else "low" for e in chosen]
    assert kinds == ["tall", "low", "tall", "low", "tall"]
    # Only the POOL lightest rows of a kind are drawn from.
    assert {int(e.id[-1]) for e in chosen} <= set(range(recipes.POOL))
    assert choose("wall", ("shelf_tall", "shelf_low"), ("shop",), list(reversed(pool)), SEED, "k") == chosen


def test_choose_refuses_wrong_setting_size_and_triangles() -> None:
    pool = [_c("home", "shelf_tall", settings=("bedroom",)),
            _c("deep", "shelf_tall", size=(1.0, 0.9, 1.8)),
            _c("heavy", "shelf_tall", triangles=recipes.FURNITURE_MAX_TRIANGLES + 1),
            _c("ok", "shelf_tall")]
    assert [e.id for e in choose("wall", ("shelf_tall",), ("shop", "pharmacy"), pool, SEED, "k")] == ["ok"]


def test_corners_take_any_setting_and_decor_prefers_what_reads_from_the_door() -> None:
    pool = [_c("plant", "plant_pot", size=(0.5, 0.5, 1.0), settings=("living",))]
    assert [e.id for e in choose("corner", ("plant_pot",), ("shop",), pool, SEED, "k")] == ["plant"]
    decor = [_c(f"d{i}", "decor_food", size=(0.05 * (i + 1), 0.05, 0.05), settings=("bakery",)) for i in range(8)]
    picked = {e.id for e in choose("decor", ("decor_food",), ("bakery",), decor, SEED, "k")}
    assert picked == {"d7", "d6", "d5", "d4"}  # the four biggest of eight


def test_a_long_piece_standing_across_is_turned_along_the_wall() -> None:
    pool = [_c("across", "shelf_low", size=(0.4, 1.2, 0.9))]
    assert choose("wall", ("shelf_low",), ("shop",), pool, SEED, "k") == [FurnishEntry("across", 90)]
    assert choose("table", ("table",), ("shop",), [_c("t", "table", size=(0.6, 1.0, 0.75))], SEED, "k") == [FurnishEntry("t", 0)]


# Each bound of each slot: met exactly (fits) and missed by a centimetre (does not). (length, depth, height).
BASE = {"counter": (1.2, 0.6, 1.0), "wall": (1.0, 0.4, 1.0), "wall_deep": (2.0, 1.0, 0.8), "center": (1.5, 0.8, 1.0),
        "table": (1.2, 0.8, 0.75), "seat": (0.5, 0.5, 0.9), "corner": (0.5, 0.5, 1.0), "rug": (2.0, 1.4, 0.02),
        "decor": (0.2, 0.2, 0.2), "top": (0.2, 0.2, 0.2)}
BOUNDS = [  # slot, axis (0 length, 1 depth, 2 height), bound, upper
    ("counter", 1, 1.0, True), ("counter", 2, 0.7, False), ("counter", 2, 1.3, True), ("counter", 0, 0.6, False),
    ("wall", 1, 0.8, True), ("wall", 2, 0.4, False), ("wall", 2, 2.3, True),
    ("wall_deep", 1, 0.6, False), ("wall_deep", 1, 2.4, True), ("wall_deep", 0, 2.6, True), ("wall_deep", 2, 0.3, False),
    ("wall_deep", 2, 1.6, True),
    ("center", 1, 1.2, True), ("center", 0, 2.5, True), ("center", 2, 0.5, False), ("center", 2, 1.9, True),
    ("table", 0, 2.0, True), ("table", 1, 1.2, True), ("table", 2, 0.4, False), ("table", 2, 1.15, True),
    ("seat", 0, 1.1, True), ("seat", 1, 1.1, True), ("seat", 2, 1.1, True), ("seat", 2, 0.4, False),
    ("corner", 0, 1.0, True), ("corner", 1, 1.0, True), ("corner", 2, 0.3, False),
    ("rug", 2, 0.06, True), ("rug", 0, 3.5, True), ("rug", 1, 3.5, True), ("rug", 0, 0.5, False), ("rug", 1, 0.5, False),
    ("decor", 0, 0.45, True), ("decor", 1, 0.45, True), ("decor", 2, 0.45, True),
    ("top", 0, 0.45, True), ("top", 1, 0.45, True), ("top", 2, 0.45, True),
]


def _bound_cases() -> list[tuple[str, tuple[float, ...], bool]]:
    cases = []
    for slot, axis, bound, upper in BOUNDS:
        for value, fits in ((bound, True), (round(bound + (0.01 if upper else -0.01), 4), False)):
            size = list(BASE[slot])
            size[axis] = value
            cases.append((slot, tuple(size), fits))
    return cases


@pytest.mark.parametrize(("slot", "size", "fits"), _bound_cases())
def test_each_slot_bound_is_met_exactly_and_missed_by_a_centimetre(slot, size, fits) -> None:
    assert recipes._fits(slot, _c("x", "k", size=size))[0] is fits


@pytest.mark.parametrize("slot", sorted(BASE))
def test_the_base_size_of_each_slot_fits_and_an_unknown_slot_never_does(slot) -> None:
    assert recipes._fits(slot, _c("x", "k", size=BASE[slot])) == (True, 0)
    assert recipes._fits("nowhere", _c("x", "k", size=BASE[slot]))[0] is False


@pytest.mark.parametrize("slot", ["wall", "counter", "center"])
def test_a_piece_deeper_than_long_is_turned_on_the_wall_slots_only(slot) -> None:
    length, depth, height = BASE[slot]
    # Exactly 1.2 times as deep as long is not turned; a little more is, and its sides swap.
    assert recipes._fits(slot, _c("x", "k", size=(0.5, 0.6, height)))[1] == 0
    turned = recipes._fits(slot, _c("x", "k", size=(depth, length, height)))
    assert turned == (True, 90)
    assert recipes._fits("table", _c("x", "k", size=(0.6, 1.0, 0.75))) == (True, 0)


# ---- library and sets ------------------------------------------------------------------------------------------------


def _manifest(folder: Path, rows: list[dict]) -> None:
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text(json.dumps({"assets": rows}), encoding="utf-8")


def _lib_row(asset_id: str, kind: str, size=(1.0, 0.4, 1.8), settings=("shop",), mount="wall", **extra) -> dict:
    return {"id": asset_id, "placement": "interior", "length_m": size[0], "depth_m": size[1], "height_m": size[2],
            "triangles": 100, "semantic": {"kind": kind, "mount": mount, "settings": list(settings)}, **extra}


def test_library_keeps_placeable_interior_rows_only(tmp_path: Path) -> None:
    _manifest(tmp_path / "cat", [
        _lib_row("ok", "shelf_tall"),
        _lib_row("street", "bench") | {"placement": "street"},
        _lib_row("part", "other", mount="none"),
        _lib_row("refused", "shelf_tall", refused="low_detail"),
        {"id": "plain", "placement": "interior"},
    ])
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "manifest.json").write_text("{", encoding="utf-8")
    found = library(tmp_path, ["cat", "broken", "missing"])
    assert [(c.asset_id, c.catalogue, c.kind, c.settings) for c in found] == [("ok", "cat", "shelf_tall", frozenset({"shop"}))]


def test_a_slot_no_row_fills_keeps_the_written_entries_and_zone_slots_stay_empty() -> None:
    cfg = load_detail_config()
    shops = cfg.interiors.shops.furnish
    sets, used = semantic_sets(shops, {"generic": recipes.SHOP_RECIPES["generic"]}, [], SEED, "shops")
    assert used == set()
    assert sets["generic"].counter == shops.sets["generic"].counter and sets["generic"].wall == shops.sets["generic"].wall
    assert all(sets["generic"].slot(name) == () for name in ZONE_SLOTS)


def test_semantic_sets_fill_each_recipe_slot_and_name_the_catalogues_used() -> None:
    pool = [_c("shelf", "shelf_tall", catalogue="a"), _c("cnt", "counter", size=(1.2, 0.6, 1.0), catalogue="b"),
            _c("plant", "plant_pot", size=(0.5, 0.5, 1.0), catalogue="c")]
    shops = load_detail_config().interiors.shops.furnish
    sets, used = semantic_sets(shops, {"generic": recipes.SHOP_RECIPES["generic"]}, pool, SEED, "shops")
    generic = sets["generic"]
    assert [e.id for e in generic.wall] == ["shelf"] and [e.id for e in generic.counter] == ["cnt"]
    assert [e.id for e in generic.corner] == ["plant"] and used == {"a", "b", "c"}


def test_with_semantic_sets_is_off_under_the_switch_and_for_a_sets_block(tmp_path: Path, monkeypatch) -> None:
    cfg = load_detail_config()
    monkeypatch.setenv(recipes.ENV, "0")
    assert with_semantic_sets(cfg, tmp_path) is cfg
    monkeypatch.setenv(recipes.ENV, "1")
    hand = replace(cfg, interiors=replace(cfg.interiors, furnish=replace(cfg.interiors.furnish, selection="sets"),
                                          shops=replace(cfg.interiors.shops, furnish=replace(cfg.interiors.shops.furnish, selection="sets"))))
    assert with_semantic_sets(hand, tmp_path) is hand


def test_with_semantic_sets_builds_sets_from_the_shared_catalogues(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(recipes.ENV, "1")
    monkeypatch.setenv("ONWARD_LOCAL_CATALOGUES", "0")
    _manifest(tmp_path / "kenney_furniture", [
        _lib_row("kf_shelf", "shelf_tall"), _lib_row("kf_sofa", "sofa", size=(2.0, 0.9, 0.8), settings=("living",)),
        _lib_row("kf_rug", "rug", size=(2.0, 1.4, 0.02), settings=("living",))])
    cfg = load_detail_config()
    out = with_semantic_sets(cfg, tmp_path)
    shops, floors = out.interiors.shops.furnish, out.interiors.furnish
    assert [e.id for e in shops.sets["generic"].wall] == ["kf_shelf"]
    assert [e.id for e in floors.sets["generic"].wall_deep] == ["kf_sofa"] and [e.id for e in floors.sets["generic"].rug] == ["kf_rug"]
    assert "kenney_furniture" in shops.catalogues and "kenney_furniture" in floors.catalogues
    assert out.interiors.shops.furnish.max_triangles == cfg.interiors.shops.furnish.max_triangles


def test_the_default_recipes_use_known_kinds_and_slots() -> None:
    from onward_generator.overgrow.semantics import KINDS, SETTINGS

    for recipe in (*recipes.SHOP_RECIPES.values(), *recipes.FLOOR_RECIPES.values()):
        assert set(recipe["settings"]) <= set(SETTINGS)
        for slot, kinds in recipe["slots"].items():
            assert slot in ALL_SLOTS and set(kinds) <= set(KINDS)


# ---- the zoned planner -----------------------------------------------------------------------------------------------


def _a(asset_id: str, size, role="shelf", triangles=100) -> FurnishAsset:
    return FurnishAsset(asset_id, "cat", "CC0-1.0", role, "furniture", size, triangles, "0" * 64)


CATALOGUE = {a.asset_id: a for a in (
    _a("shelf", (1.0, 0.4, 1.8)), _a("counter", (1.6, 0.6, 1.0), role="counter"), _a("cake", (0.2, 0.2, 0.15), role="decor"),
    _a("table", (1.2, 0.7, 0.75), role="table"), _a("chair", (0.45, 0.45, 0.9), role="chair"), _a("cup", (0.1, 0.1, 0.1), role="decor"),
    _a("plant", (0.5, 0.5, 1.0), role="plant"), _a("rug", (2.0, 1.4, 0.02), role="rug"), _a("gondola", (1.8, 0.8, 1.4)),
    _a("sofa", (2.0, 0.9, 0.8), role="sofa"))}


def _zoned(**slots) -> FurnishSet:
    entries = {name: tuple(FurnishEntry(i) for i in slots.get(name, ())) for name in ALL_SLOTS}
    return FurnishSet(**entries)


def _shop_cfg(zone_set: FurnishSet, cap: int = 12):
    base = load_detail_config().interiors.shops.furnish
    return replace(base, sets={"generic": zone_set}, max_pieces_per_room=cap, max_triangles=100000, max_collision_triangles=20000)


ROOM = box(0.0, 0.0, 10.0, 8.0)
DOOR = box(4.0, 0.0, 6.0, 1.5)


def _plan(zone_set: FurnishSet, polygon=ROOM, cap: int = 12) -> RoomFurnishing:
    result = plan_room(FurnishRoom("b/L0", polygon, DOOR, 3.0), _shop_cfg(zone_set, cap), CATALOGUE, SEED, "b")
    assert isinstance(result, RoomFurnishing)
    return result


def test_a_zoned_room_scales_its_pieces_with_its_area() -> None:
    zone = _zoned(wall=("shelf",), corner=("plant",))
    planner = _Planner(FurnishRoom("b/L0", ROOM, DOOR, 3.0), _shop_cfg(zone, cap=24), CATALOGUE, SEED, "b")
    assert planner.zoned and planner.cap == round(80 / AREA_PER_PIECE_M2)
    small = _Planner(FurnishRoom("b/L0", box(0, 0, 3, 3), box(1, 0, 2, 1), 3.0), _shop_cfg(zone, cap=24), CATALOGUE, SEED, "b")
    assert small.cap == MIN_PIECES
    hand = _Planner(FurnishRoom("b/L0", ROOM, DOOR, 3.0), _shop_cfg(_zoned(wall=("shelf",)), cap=24), CATALOGUE, SEED, "b")
    assert not hand.zoned and hand.cap == 24  # counter, wall and decor only: the plan of ADR-031


def test_a_zoned_shop_gets_walls_islands_corners_and_stays_inside_the_rules() -> None:
    result = _plan(_zoned(counter=("counter",), decor=("cake",), wall=("shelf",), center=("gondola",), corner=("plant",)))
    slots = [p.slot for p in result.pieces]
    assert {"wall", "center", "corner"} <= set(slots)
    floor = [p for p in result.pieces if p.slot not in ON_TOP]
    assert len([p for p in floor if p.slot not in CAP_FREE]) <= min(12, round(80 / AREA_PER_PIECE_M2))
    for i, p in enumerate(floor):
        assert ROOM.contains(p.footprint) and p.footprint.intersection(DOOR).area <= 1e-9
        for q in floor[i + 1:]:
            assert p.footprint.intersection(q.footprint).area <= 1e-9
    # One wall model per edge, so a wall reads as a run.
    assert len({p.asset.asset_id for p in result.pieces if p.slot == "wall"}) == 1


def test_tables_get_their_seats_and_their_tops() -> None:
    result = _plan(_zoned(wall=("shelf",), table=("table",), seat=("chair",), top=("cup",)))
    tables = [p for p in result.pieces if p.slot == "table"]
    seats = [p for p in result.pieces if p.slot == "seat"]
    tops = [p for p in result.pieces if p.slot == "top"]
    assert tables and len(seats) >= 2 * len(tables)
    for top in tops:
        assert any(t.footprint.contains(top.footprint.centroid) for t in tables)
        assert top.z_m == pytest.approx(0.75) and not top.solid
    for seat in seats:
        assert min(seat.footprint.distance(t.footprint) for t in tables) <= 0.06


def test_a_rug_lies_under_the_furniture_and_takes_no_floor() -> None:
    result = _plan(_zoned(wall=("shelf",), rug=("rug",), table=("table",), seat=("chair",)))
    rug = [p for p in result.pieces if p.slot == "rug"]
    assert len(rug) == 1 and not rug[0].solid
    assert ROOM.contains(rug[0].footprint)


def test_a_home_puts_its_sofa_against_a_wall() -> None:
    floors = replace(load_detail_config().interiors.furnish, sets={"generic": _zoned(wall=("shelf",), wall_deep=("sofa",))},
                     max_pieces_per_room=12, max_triangles=100000, max_collision_triangles=20000)
    result = plan_room(FurnishRoom("b/L0", ROOM, DOOR, 3.0), floors, CATALOGUE, SEED, "b")
    assert isinstance(result, RoomFurnishing)
    sofa = [p for p in result.pieces if p.slot == "wall_deep"]
    assert sofa and all(ROOM.exterior.distance(p.footprint) <= 0.25 for p in sofa)


def test_zoned_planning_is_deterministic() -> None:
    zone = _zoned(counter=("counter",), wall=("shelf",), table=("table",), seat=("chair",), corner=("plant",), top=("cup",))
    assert _plan(zone).record("b", 0) == _plan(zone).record("b", 0)


# ---- what each rule decides (mutation coverage, ADR-036) ---------------------------------------------------------


def _rotated(rows: list[Candidate], seed: int, key: str, slot: str, kind: str) -> list[str]:
    from onward_generator.detail.facades import stable_unit

    start = int(stable_unit(seed, key, slot, kind) * len(rows))
    return [c.asset_id for c in rows[start:] + rows[:start]]


@pytest.mark.parametrize(("seed", "key"), [(1, "a"), (7, "shops:bar"), (48152, "floors:generic"), (99, "z")])
def test_each_kind_starts_at_the_row_its_seed_key_slot_and_kind_pick(seed, key) -> None:
    tall = [_c(f"t{i}", "shelf_tall", triangles=10 + i) for i in range(4)]
    low = [_c(f"l{i}", "shelf_low", size=(1.0, 0.4, 0.9), triangles=10 + i) for i in range(4)]
    chosen = [e.id for e in choose("wall", ("shelf_tall", "shelf_low"), ("shop",), tall + low, seed, key)]
    a, b = _rotated(tall, seed, key, "wall", "shelf_tall"), _rotated(low, seed, key, "wall", "shelf_low")
    assert chosen == [a[0], b[0], a[1], b[1], a[2]]


def test_decor_takes_its_own_triangle_cap_and_a_row_at_the_cap_is_kept() -> None:
    cap = recipes.MAX_TRIANGLES["decor"]
    pool = [_c("at", "decor_food", size=(0.2, 0.2, 0.2), triangles=cap), _c("over", "decor_food", size=(0.2, 0.2, 0.2), triangles=cap + 1)]
    assert [e.id for e in choose("decor", ("decor_food",), ("shop",), pool, SEED, "k")] == ["at"]
    heavy = [_c("f_at", "shelf_tall", triangles=recipes.FURNITURE_MAX_TRIANGLES)]
    assert [e.id for e in choose("wall", ("shelf_tall",), ("shop",), heavy, SEED, "k")] == ["f_at"]


@pytest.mark.parametrize("slot", ["decor", "top"])
def test_small_slots_rank_by_the_biggest_side_whichever_it_is(slot) -> None:
    sizes = {"long": (0.40, 0.05, 0.05), "deep": (0.05, 0.41, 0.05), "tall": (0.05, 0.05, 0.42), "tiny": (0.06, 0.06, 0.06),
             "small": (0.07, 0.07, 0.07)}
    pool = [_c(name, "decor_misc", size=size, settings=("shop",)) for name, size in sizes.items()]
    picked = [e.id for e in choose(slot, ("decor_misc",), ("shop",), pool, SEED, "k")]
    assert set(picked) == {"tall", "deep", "long", "small"}  # POOL keeps the four biggest; "tiny" is left out


def test_the_furniture_slots_rank_by_triangles_not_size() -> None:
    pool = [_c(f"s{i}", "shelf_tall", size=(1.0 - 0.05 * i, 0.4, 1.8), triangles=500 - 10 * i) for i in range(6)]
    picked = {e.id for e in choose("wall", ("shelf_tall",), ("shop",), pool, SEED, "k")}
    assert picked == {"s2", "s3", "s4", "s5"}


def test_choose_with_no_kinds_or_no_rows_chooses_nothing() -> None:
    assert choose("wall", (), ("shop",), [_c("a", "shelf_tall")], SEED, "k") == []
    assert choose("wall", ("shelf_tall",), ("shop",), [], SEED, "k") == []


def test_library_skips_a_non_row_an_excluded_id_and_keeps_reading_after_a_skip(tmp_path: Path) -> None:
    from onward_generator.detail.config import FURNISH_EXCLUDED_IDS

    excluded = sorted(FURNISH_EXCLUDED_IDS)[0]
    _manifest(tmp_path / "cat", ["not a row", _lib_row(excluded, "shelf_tall"), _lib_row("café", "shelf_tall"),
                                 {"id": "bare", "placement": "interior", "semantic": {"kind": "rug", "mount": "floor_flat"}}])
    found = library(tmp_path, ["cat"])
    assert [c.asset_id for c in found] == ["café", "bare"]
    assert found[1] == Candidate("bare", "cat", "rug", frozenset(), 0.0, 0.0, 0.0, 0)


def test_a_slot_falls_back_to_the_generic_written_set_and_to_nothing_without_one() -> None:
    shops = load_detail_config().interiors.shops.furnish
    pharmacy = {"pharmacy": recipes.SHOP_RECIPES["pharmacy"]}
    only_generic = replace(shops, sets={"generic": shops.sets["generic"]})
    sets, _ = semantic_sets(only_generic, pharmacy, [], SEED, "shops")
    assert sets["pharmacy"].wall == shops.sets["generic"].wall and sets["pharmacy"].decor == shops.sets["generic"].decor
    none_written = replace(shops, sets={})
    sets, _ = semantic_sets(none_written, pharmacy, [], SEED, "shops")
    assert all(sets["pharmacy"].slot(name) == () for name in ALL_SLOTS)


def test_a_written_zone_slot_is_the_fallback_of_an_empty_recipe_slot() -> None:
    shops = load_detail_config().interiors.shops.furnish
    written = replace(shops.sets["generic"], corner=(FurnishEntry("kept_plant"),))
    sets, _ = semantic_sets(replace(shops, sets={"generic": written}), {"generic": recipes.SHOP_RECIPES["generic"]}, [], SEED, "shops")
    assert sets["generic"].corner == (FurnishEntry("kept_plant"),)


def test_semantic_sets_choose_with_the_seed_and_the_scope_and_name_key() -> None:
    pool = [_c(f"t{i}", "shelf_tall", triangles=10 + i) for i in range(4)] + [
        _c(f"l{i}", "shelf_low", size=(1.0, 0.4, 0.9), triangles=10 + i) for i in range(4)]
    shops = load_detail_config().interiors.shops.furnish
    recipe = recipes.SHOP_RECIPES["generic"]
    for seed in (3, 11, 48152):
        sets, _ = semantic_sets(shops, {"generic": recipe}, pool, seed, "shops")
        assert list(sets["generic"].wall) == choose("wall", recipe["slots"]["wall"], recipe["settings"], pool, seed, "shops:generic")


def _local(tmp_path: Path, rows: list[dict]) -> None:
    from onward_generator.assetstore.local import LICENCE_NAME

    folder = tmp_path / "asset_store_test"
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text(json.dumps({"source": LICENCE_NAME, "assets": rows}), encoding="utf-8")


def test_local_catalogues_join_the_pool_and_the_block_catalogues(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ONWARD_LOCAL_CATALOGUES", "1")
    monkeypatch.delenv(recipes.ENV, raising=False)  # on unless switched off
    _local(tmp_path, [_lib_row("as_test_shelf", "shelf_tall"), _lib_row("as_test_sofa", "sofa", size=(2.0, 0.9, 0.8), settings=("living",))])
    assert recipes.allowed_catalogues(tmp_path) == ["asset_store_test"]
    cfg = load_detail_config()
    out = with_semantic_sets(cfg, tmp_path)
    shops, floors = out.interiors.shops.furnish, out.interiors.furnish
    assert [e.id for e in shops.sets["generic"].wall] == ["as_test_shelf"] and "asset_store_test" in shops.catalogues
    assert [e.id for e in floors.sets["generic"].wall_deep] == ["as_test_sofa"] and "asset_store_test" in floors.catalogues
    pool = library(tmp_path, ["asset_store_test"])
    expected, _ = semantic_sets(cfg.interiors.shops.furnish, {"generic": recipes.SHOP_RECIPES["generic"]}, pool, cfg.seed, "shops")
    assert shops.sets["generic"] == expected["generic"]
    expected, _ = semantic_sets(cfg.interiors.furnish, recipes.FLOOR_RECIPES, pool, cfg.seed, "floors")
    assert floors.sets["generic"] == expected["generic"]


def _with(cfg, *, shops: tuple[bool, str], floors: tuple[bool, str]):
    s, f = cfg.interiors.shops.furnish, cfg.interiors.furnish
    return replace(cfg, interiors=replace(cfg.interiors, furnish=replace(f, enabled=floors[0], selection=floors[1]),
                                          shops=replace(cfg.interiors.shops, furnish=replace(s, enabled=shops[0], selection=shops[1]))))


@pytest.mark.parametrize(("shops", "floors", "shops_change", "floors_change"), [
    ((True, "semantic"), (True, "sets"), True, False),
    ((True, "sets"), (True, "semantic"), False, True),
    ((False, "semantic"), (True, "semantic"), False, True),
    ((True, "semantic"), (False, "semantic"), True, False),
])
def test_only_an_enabled_semantic_block_gets_chosen_sets(tmp_path: Path, monkeypatch, shops, floors, shops_change, floors_change) -> None:
    monkeypatch.setenv("ONWARD_LOCAL_CATALOGUES", "1")
    monkeypatch.setenv(recipes.ENV, "1")
    _local(tmp_path, [_lib_row("as_test_shelf", "shelf_tall", settings=("shop", "living")),
                      _lib_row("as_test_sofa", "sofa", size=(2.0, 0.9, 0.8), settings=("living",))])
    cfg = _with(load_detail_config(), shops=shops, floors=floors)
    out = with_semantic_sets(cfg, tmp_path)
    assert (out.interiors.shops.furnish != cfg.interiors.shops.furnish) is shops_change
    assert (out.interiors.furnish != cfg.interiors.furnish) is floors_change


def test_load_detail_config_reads_the_repository_assets(monkeypatch) -> None:
    from onward_generator.detail import config as config_module

    roots: list[Path] = []

    def spy(name):
        real = getattr(recipes if name == "with_semantic_sets" else config_module, name)

        def record(cfg, root):
            roots.append(root)
            return real(cfg, root)
        return record

    monkeypatch.setattr(config_module, "with_local_furnishing", spy("with_local_furnishing"))
    monkeypatch.setattr(recipes, "with_semantic_sets", spy("with_semantic_sets"))
    load_detail_config()
    assert roots == [config_module.REPO_ROOT / "assets"] * 2


def test_a_block_without_selection_uses_its_written_sets() -> None:
    from onward_generator.detail.config import config_from_document
    from tests.unit.test_shop_rooms import DOC

    doc = json.loads(json.dumps(DOC))
    doc["interiors"]["shops"]["furnish"].pop("selection", None)
    assert config_from_document(doc).interiors.shops.furnish.selection == "sets"


def _home(zone_set: FurnishSet, cap: int = 12) -> RoomFurnishing:
    floors = replace(load_detail_config().interiors.furnish, sets={"generic": zone_set}, max_pieces_per_room=cap,
                     max_triangles=100000, max_collision_triangles=20000)
    result = plan_room(FurnishRoom("b/L0", ROOM, DOOR, 3.0), floors, CATALOGUE, SEED, "b")
    assert isinstance(result, RoomFurnishing)
    return result


def test_a_room_gets_at_most_two_deep_wall_pieces() -> None:
    result = _home(_zoned(wall_deep=("sofa",), wall=("shelf",)))
    assert [p.slot for p in result.pieces].count("wall_deep") == 2


def test_the_walls_take_their_share_and_the_tables_the_rest() -> None:
    # Pieces are recorded in the order they were placed: the first wall pass stops at ceil(0.4 * cap), then tables.
    slots = [p.slot for p in _plan(_zoned(wall=("shelf",), table=("table",), seat=("chair",)), cap=10).pieces]
    assert slots[:slots.index("table")] == ["wall"] * 4
    # A cap of two still leaves the walls one piece and the tables the other.
    small = [p.slot for p in _plan(_zoned(wall=("shelf",), table=("table",), seat=("chair",)), cap=2).pieces]
    assert small[:2] == ["wall", "table"] and small.count("wall") == 1
