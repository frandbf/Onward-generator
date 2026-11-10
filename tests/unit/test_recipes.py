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


@pytest.mark.parametrize(("slot", "size", "fits"), [
    ("counter", (1.2, 0.6, 1.0), True), ("counter", (1.2, 0.6, 0.5), False),
    ("wall_deep", (2.0, 1.6, 0.6), True), ("wall_deep", (2.0, 0.4, 0.6), False),
    ("rug", (2.0, 1.4, 0.02), True), ("rug", (2.0, 1.4, 0.2), False),
    ("seat", (0.5, 0.5, 0.9), True), ("seat", (1.5, 0.5, 0.9), False),
    ("top", (0.2, 0.2, 0.2), True), ("top", (0.6, 0.2, 0.2), False),
    ("nowhere", (1.0, 1.0, 1.0), False),
])
def test_each_slot_has_its_size_limits(slot, size, fits) -> None:
    assert recipes._fits(slot, _c("x", "k", size=size))[0] is fits


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
