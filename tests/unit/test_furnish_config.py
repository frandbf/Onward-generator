"""``interiors.shops.furnish`` configuration (ADR-031): on by default since 10 November 2026, validated rows and ranges."""

from __future__ import annotations

import copy
import json
from dataclasses import replace

import pytest

from onward_generator.detail import config as config_module
from onward_generator.detail.config import (
    DEFAULT_CONFIG_PATH,
    MAX_WALL_DEPTH_M,
    DetailConfigError,
    FurnishEntry,
    FurnishSet,
    config_from_document,
    load_detail_config,
)

DOC = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))


def _doc(enabled: bool = True) -> dict:
    doc = copy.deepcopy(DOC)
    doc["interiors"]["shops"]["furnish"]["enabled"] = enabled
    return doc


def _furnish(doc: dict) -> dict:
    return doc["interiors"]["shops"]["furnish"]


def _problems(doc: dict) -> list[str]:
    with pytest.raises(DetailConfigError) as caught:
        config_from_document(doc)
    return caught.value.problems


def test_the_default_config_has_furnishing_on_and_checks_its_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    checked: list[object] = []
    real = config_module.furnish_problems

    def record(*args, **kwargs):
        checked.append(args[0])
        return real(*args, **kwargs)

    monkeypatch.setattr(config_module, "furnish_problems", record)
    furnish = load_detail_config().interiors.shops.furnish
    assert furnish.enabled is True and len(checked) == 1
    assert sorted(furnish.sets) == ["bakery", "bar", "generic", "pharmacy"]
    assert furnish.catalogues == ("kenney_food", "kenney_furniture", "kenney_mini_shop", "polyhaven_shop", "quaternius_home_interior")
    assert furnish.sets["generic"].counter[0] == FurnishEntry("qt_home_Drawer_2")


def test_a_document_without_the_block_loads_with_furnishing_off() -> None:
    doc = copy.deepcopy(DOC)
    del doc["interiors"]["shops"]["furnish"]
    furnish = config_from_document(doc).interiors.shops.furnish
    assert furnish.enabled is False and dict(furnish.sets) == {}


def test_the_enabled_default_sets_load() -> None:
    furnish = config_from_document(_doc()).interiors.shops.furnish
    assert furnish.enabled and furnish.walk_width_m == 1.2 and furnish.max_triangles == 12000
    assert furnish.set_for("butcher") is furnish.sets["generic"]


def test_an_unknown_set_key_is_refused_by_name() -> None:
    doc = _doc()
    _furnish(doc)["sets"]["butcher"] = {"counter": [], "wall": ["Shelf_01"], "decor": []}
    assert any("'butcher' is not a shop category" in p for p in _problems(doc))


def test_the_generic_set_is_required() -> None:
    doc = _doc()
    del _furnish(doc)["sets"]["generic"]
    assert any("generic" in p for p in _problems(doc))


@pytest.mark.parametrize(("asset_id", "why"), [
    ("kenney_bench", "is not a row of an allowed catalogue"),  # a street prop of assets/kenney
    ("no_such_row", "is not a row of an allowed catalogue"),
    ("kenney_shop_floor", "is a tile, a character or loose clutter"),
    ("kenney_shop_mannequin", "is a tile, a character or loose clutter"),
    ("kenney_shop_shopping_basket", "is a tile, a character or loose clutter"),
])
def test_a_row_that_is_not_allowed_furniture_is_refused(asset_id: str, why: str) -> None:
    doc = _doc()
    _furnish(doc)["sets"]["generic"]["wall"].append(asset_id)
    assert any(asset_id in p and why in p for p in _problems(doc))


def test_a_street_catalogue_row_is_refused_even_when_its_catalogue_is_listed(tmp_path) -> None:
    street = tmp_path / "kenney_food"
    street.mkdir()
    (street / "manifest.json").write_text(json.dumps({"assets": [{"id": "street_box", "placement": "street", "length_m": 0.1,
                                                                    "depth_m": 0.1, "height_m": 0.1}]}), encoding="utf-8")
    furnish = config_from_document(_doc(False)).interiors.shops.furnish
    sets = {"generic": FurnishSet((), (), (FurnishEntry("street_box"),))}
    problems = config_module.furnish_problems(replace(furnish, catalogues=("kenney_food",), sets=sets), [], tmp_path)
    assert problems == ["/interiors/shops/furnish/sets/generic/decor: 'street_box' has placement 'street', not 'interior'"]


def test_decor_must_come_from_the_food_kit() -> None:
    doc = _doc()
    _furnish(doc)["sets"]["generic"]["decor"] = ["Shelf_01"]
    assert any("'Shelf_01' is decor outside kenney_food" in p for p in _problems(doc))


def test_the_wall_depth_boundary(tmp_path) -> None:
    catalogue = tmp_path / "polyhaven_shop"
    catalogue.mkdir()
    rows = [{"id": f"d{k}", "placement": "interior", "length_m": 1.0, "depth_m": depth, "height_m": 1.0}
            for k, depth in enumerate((0.79, MAX_WALL_DEPTH_M + 0.01))]
    (catalogue / "manifest.json").write_text(json.dumps({"assets": rows}), encoding="utf-8")
    base = config_from_document(_doc(False)).interiors.shops.furnish
    sets = {"generic": FurnishSet((), (FurnishEntry("d0"), FurnishEntry("d1"), FurnishEntry("d1", 90)), ())}
    problems = config_module.furnish_problems(replace(base, catalogues=("polyhaven_shop",), sets=sets), [], tmp_path)
    assert problems == [
        "/interiors/shops/furnish/sets/generic/wall: 'd1' is 0.81 m deep, more than 0.8 m for a wall piece",
        "/interiors/shops/furnish/sets/generic/wall: 'd1' is 1 m deep, more than 0.8 m for a wall piece",  # turned, its length is its depth
    ]


def test_a_counter_may_be_one_metre_deep_and_no_more() -> None:
    doc = _doc()
    _furnish(doc)["sets"]["generic"]["counter"] = ["kenney_furniture_kitchenCabinet", "kenney_shop_freezer"]
    problems = _problems(doc)
    assert problems == ["/interiors/shops/furnish/sets/generic/counter: 'kenney_shop_freezer' is 1.5 m deep, more than 1 m for a counter piece"]


@pytest.mark.parametrize(("key", "value", "ok"), [
    ("walk_width_m", 1.19, False), ("walk_width_m", 1.2, True), ("max_triangles", -1, False),
    ("max_pieces_per_room", 25, False), ("max_assets", 33, False), ("max_collision_triangles", 20001, False),
])
def test_the_schema_ranges(key: str, value: float, ok: bool) -> None:
    doc = _doc()
    _furnish(doc)[key] = value
    if ok:
        assert getattr(config_from_document(doc).interiors.shops.furnish, key) == value
    else:
        assert any(f"furnish/{key}" in p for p in _problems(doc))


def test_a_turn_must_be_a_quarter_and_no_extra_key_is_allowed() -> None:
    doc = _doc()
    _furnish(doc)["sets"]["generic"]["wall"] = [{"id": "Shelf_01", "turn_deg": 45}]
    assert _problems(doc)
    doc = _doc()
    _furnish(doc)["sets"]["generic"]["wall"] = [{"id": "kenney_furniture_chair", "turn_deg": 90}]
    entry = config_from_document(doc).interiors.shops.furnish.sets["generic"].wall[0]
    assert (entry.id, entry.turn_deg) == ("kenney_furniture_chair", 90)
    doc = _doc()
    _furnish(doc)["colour"] = "red"
    assert any("colour" in p for p in _problems(doc))
    doc = _doc()
    _furnish(doc)["catalogues"] = ["kenney"]
    assert _problems(doc)


def test_the_row_refusals_read_whole() -> None:
    doc = _doc()
    _furnish(doc)["sets"]["generic"]["wall"] += ["no_such_row", "kenney_shop_floor"]
    assert _problems(doc) == [
        "/interiors/shops/furnish/sets/generic/wall: 'no_such_row' is not a row of an allowed catalogue",
        "/interiors/shops/furnish/sets/generic/wall: 'kenney_shop_floor' is a tile, a character or loose clutter, not furniture",
    ]


def _manifest(directory, rows) -> None:
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_text(json.dumps({"assets": rows}), encoding="utf-8")


def test_a_half_turn_keeps_the_depth_and_a_piece_exactly_at_the_limit_loads(tmp_path) -> None:
    _manifest(tmp_path / "polyhaven_shop", ["not a row", {"placement": "interior"},
                                            {"id": "e", "placement": "interior", "length_m": 2.0, "depth_m": MAX_WALL_DEPTH_M, "height_m": 1.0}])
    base = config_from_document(_doc(False)).interiors.shops.furnish
    sets = {"generic": FurnishSet((), (FurnishEntry("e"), FurnishEntry("e", 180)), ())}
    assert config_module.furnish_problems(replace(base, catalogues=("polyhaven_shop",), sets=sets), [], tmp_path) == []
    sets = {"generic": FurnishSet((), (FurnishEntry("e", 270),), ())}
    assert config_module.furnish_problems(replace(base, catalogues=("polyhaven_shop",), sets=sets), [], tmp_path) == [
        "/interiors/shops/furnish/sets/generic/wall: 'e' is 2 m deep, more than 0.8 m for a wall piece"]


def test_a_catalogue_without_a_manifest_is_named_and_the_others_still_resolve(tmp_path) -> None:
    _manifest(tmp_path / "polyhaven_shop", [{"id": "e", "placement": "interior", "length_m": 1.0, "depth_m": 0.3, "height_m": 1.0}])
    base = config_from_document(_doc(False)).interiors.shops.furnish
    sets = {"generic": FurnishSet((), (FurnishEntry("e"),), ())}
    problems = config_module.furnish_problems(replace(base, catalogues=("kenney_food", "polyhaven_shop"), sets=sets), [], tmp_path)
    assert problems == [f"/interiors/shops/furnish/catalogues: 'kenney_food' has no readable manifest under {tmp_path}"]


def test_every_key_of_the_block_is_read() -> None:
    doc = _doc(False)
    values = {"max_triangles": 900, "max_collision_triangles": 48, "max_pieces_per_room": 3, "max_assets": 5, "walk_width_m": 1.5,
              "wall_gap_m": 0.05, "piece_gap_m": 0.2, "pane_clear_m": 0.3, "head_clear_m": 0.25}
    _furnish(doc).update(values, catalogues=["polyhaven_shop", "kenney_food"])
    _furnish(doc)["sets"]["generic"]["wall"] = ["Shelf_01", {"id": "kenney_furniture_chair"}, {"id": "kenney_furniture_desk", "turn_deg": 180}]
    furnish = config_from_document(doc).interiors.shops.furnish
    assert {key: getattr(furnish, key) for key in values} == values
    assert furnish.catalogues == ("kenney_food", "polyhaven_shop")
    assert furnish.sets["generic"].wall == (FurnishEntry("Shelf_01", 0), FurnishEntry("kenney_furniture_chair", 0),
                                            FurnishEntry("kenney_furniture_desk", 180))


def test_furnishing_problems_add_to_the_other_problems() -> None:
    doc = _doc()
    doc["interiors"]["min_buildings"], doc["interiors"]["max_buildings"] = 9, 8
    _furnish(doc)["sets"]["generic"]["wall"].append("no_such_row")
    assert _problems(doc) == ["/interiors: min_buildings must be at most max_buildings",
                              "/interiors/shops/furnish/sets/generic/wall: 'no_such_row' is not a row of an allowed catalogue"]


# ---- interiors.furnish: the floors of the stair interior (ADR-034) ----------------------------------------------------


def _floors_doc(enabled: bool = True) -> dict:
    doc = copy.deepcopy(DOC)
    doc["interiors"]["furnish"]["enabled"] = enabled
    return doc


def test_the_floor_block_is_on_by_default_and_checks_its_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    checked: list[object] = []
    real = config_module.floor_furnish_problems

    def record(*args, **kwargs):
        checked.append(args[0])
        return real(*args, **kwargs)

    monkeypatch.setattr(config_module, "floor_furnish_problems", record)
    furnish = load_detail_config().interiors.furnish
    assert furnish.enabled is True and len(checked) == 1 and sorted(furnish.sets) == ["generic"]
    assert furnish.max_triangles == 12000 and furnish.max_collision_triangles == 1200 and furnish.max_assets == 12
    assert furnish.max_pieces_per_room == 6
    assert furnish.catalogues == ("kenney_furniture", "quaternius_home_interior")
    assert len({e.id for e in furnish.sets["generic"].wall}) <= furnish.max_assets  # no map can hit asset_cap on variety alone
    generic = furnish.sets["generic"]
    assert generic.counter == () and generic.decor == () and generic.wall


def test_a_document_without_the_floor_block_loads_with_it_off() -> None:
    doc = copy.deepcopy(DOC)
    del doc["interiors"]["furnish"]
    furnish = config_from_document(doc).interiors.furnish
    assert furnish.enabled is False and dict(furnish.sets) == {}


def test_the_enabled_default_floor_block_loads_and_leaves_the_shop_block_alone() -> None:
    doc = _floors_doc()
    doc["interiors"]["shops"]["furnish"]["enabled"] = False
    cfg = config_from_document(doc)
    assert cfg.interiors.furnish.enabled and cfg.interiors.shops.furnish.enabled is False
    assert cfg.interiors.furnish.sets["generic"].wall[0] == FurnishEntry("qt_home_Bookshelf")


def test_the_schema_takes_only_the_generic_set_and_no_extra_key() -> None:
    doc = _floors_doc()
    doc["interiors"]["furnish"]["sets"]["bedroom"] = {"counter": [], "wall": [], "decor": []}
    assert any("/interiors/furnish/sets" in p and "bedroom" in p for p in _problems(doc))
    doc = _floors_doc()
    del doc["interiors"]["furnish"]["sets"]["generic"]
    assert any("/interiors/furnish/sets" in p and "generic" in p for p in _problems(doc))
    doc = _floors_doc()
    doc["interiors"]["furnish"]["colour"] = "red"
    assert any("/interiors/furnish" in p and "colour" in p for p in _problems(doc))
    doc = _floors_doc()
    doc["interiors"]["furnish"]["walk_width_m"] = 1.19
    assert any("/interiors/furnish/walk_width_m" in p for p in _problems(doc))


def _floor_problems(tmp_path, sets: dict[str, FurnishSet], rows: list[dict] | None = None, catalogues=("kenney_furniture",)) -> list[str]:
    base = config_from_document(_floors_doc(False)).interiors.furnish
    root = config_module.REPO_ROOT / "assets"
    if rows is not None:
        (tmp_path / "kenney_furniture").mkdir()
        (tmp_path / "kenney_furniture" / "manifest.json").write_text(json.dumps({"assets": rows}), encoding="utf-8")
        root = tmp_path
    return config_module.floor_furnish_problems(replace(base, catalogues=catalogues, sets=sets), root)


def test_floor_set_keys_other_than_generic_and_a_missing_generic_set_are_named(tmp_path) -> None:
    wall = (FurnishEntry("kenney_furniture_bookcaseOpen"),)
    assert _floor_problems(tmp_path, {"bedroom": FurnishSet((), wall, ())}) == [
        "/interiors/furnish/sets: 'bedroom' is not 'generic'", "/interiors/furnish/sets: the 'generic' set is required"]
    assert _floor_problems(tmp_path, {"generic": FurnishSet((), wall, ())}) == []


def test_floors_take_wall_pieces_only(tmp_path) -> None:
    piece = (FurnishEntry("kenney_furniture_bookcaseOpenLow"),)
    assert _floor_problems(tmp_path, {"generic": FurnishSet(piece, piece, piece)}) == [
        "/interiors/furnish/sets/generic/counter: residential floors take wall pieces only",
        "/interiors/furnish/sets/generic/decor: residential floors take wall pieces only"]


@pytest.mark.parametrize(("asset_id", "why"), [
    ("kenney_furniture_bedSingle", "is 2.284 m deep, more than 0.8 m for a wall piece"),
    ("kenney_furniture_loungeSofa", "is 0.832 m deep, more than 0.8 m for a wall piece"),
    ("kenney_furniture_floorFull", "is 2.03 m deep, more than 0.8 m for a wall piece"),
    ("kenney_shop_floor", "is not a row of an allowed catalogue"),  # its catalogue is not listed in this block
    ("kenney_bench", "is not a row of an allowed catalogue"),
])
def test_a_floor_row_that_is_not_allowed_is_refused_by_name(tmp_path, asset_id: str, why: str) -> None:
    sets = {"generic": FurnishSet((), (FurnishEntry(asset_id),), ())}
    assert _floor_problems(tmp_path, sets) == [f"/interiors/furnish/sets/generic/wall: {asset_id!r} {why}"]


def test_an_excluded_id_and_a_street_row_are_refused_even_in_a_listed_catalogue(tmp_path) -> None:
    rows = [{"id": "kenney_shop_mannequin", "placement": "interior", "length_m": 0.5, "depth_m": 0.5, "height_m": 1.8},
            {"id": "street_bin", "placement": "street", "length_m": 0.5, "depth_m": 0.5, "height_m": 1.0},
            {"id": "shelf", "placement": "interior", "length_m": 1.0, "depth_m": MAX_WALL_DEPTH_M, "height_m": 1.0}]
    sets = {"generic": FurnishSet((), tuple(FurnishEntry(r["id"]) for r in rows), ())}
    assert _floor_problems(tmp_path, sets, rows) == [
        "/interiors/furnish/sets/generic/wall: 'kenney_shop_mannequin' is a tile, a character or loose clutter, not furniture",
        "/interiors/furnish/sets/generic/wall: 'street_bin' has placement 'street', not 'interior'"]


def test_a_floor_catalogue_without_a_manifest_is_named(tmp_path) -> None:
    sets = {"generic": FurnishSet((), (FurnishEntry("kenney_furniture_bookcaseOpen"),), ())}
    base = config_from_document(_floors_doc(False)).interiors.furnish
    assert config_module.floor_furnish_problems(replace(base, catalogues=("kenney_furniture",), sets=sets), tmp_path) == [
        f"/interiors/furnish/catalogues: 'kenney_furniture' has no readable manifest under {tmp_path}",
        "/interiors/furnish/sets/generic/wall: 'kenney_furniture_bookcaseOpen' is not a row of an allowed catalogue"]


def test_floor_problems_stop_the_load_beside_the_others() -> None:
    doc = _floors_doc()
    doc["interiors"]["min_buildings"], doc["interiors"]["max_buildings"] = 9, 8
    doc["interiors"]["furnish"]["sets"]["generic"]["wall"].append("kenney_furniture_bedDouble")
    doc["interiors"]["furnish"]["sets"]["generic"]["decor"].append("kenney_food_loaf")
    assert _problems(doc) == ["/interiors: min_buildings must be at most max_buildings",
                              "/interiors/furnish/sets/generic/decor: residential floors take wall pieces only",
                              "/interiors/furnish/sets/generic/wall: 'kenney_furniture_bedDouble' is 2.284 m deep, more than 0.8 m for a wall piece"]
