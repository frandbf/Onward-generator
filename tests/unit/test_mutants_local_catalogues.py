"""Local Asset Store catalogues: which manifests are read, which rows furnish, and how they join the furnishing sets."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from onward_generator.assetstore import local
from onward_generator.detail.config import DetailConfig, load_detail_config, with_local_furnishing

ON = {local.ENV: "1"}
OFF = {local.ENV: "0"}
SOURCE = f"{local.LICENCE_NAME} local"


def _catalogue(assets: Path, folder: str, document: Any) -> None:
    (assets / folder).mkdir(parents=True, exist_ok=True)
    text = document if isinstance(document, str) else json.dumps(document)
    (assets / folder / "manifest.json").write_text(text, encoding="utf-8")


def _shelf(
    asset_id: str,
    *,
    slot: str = "wall",
    sets: list[str] | None = None,
    floors: bool = False,
    depth: float = 0.4,
    triangles: int | None = 100,
    placement: str = "interior",
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": asset_id,
        "file": "x.glb",
        "placement": placement,
        "length_m": 1.0,
        "depth_m": depth,
        "height_m": 1.5,
        "furnish": {"slot": slot, "shop_sets": sets or ["generic"], "floors": floors},
    }
    if triangles is not None:
        row["triangles"] = triangles
    return row


# ---- enabled -------------------------------------------------------------------------------------------------------------


def test_enabled_reads_the_given_mapping_before_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(local.ENV, "1")
    assert local.enabled(OFF) is False
    assert local.enabled({}) is True
    monkeypatch.setenv(local.ENV, "0")
    assert local.enabled(ON) is True
    assert local.enabled() is False


# ---- local_manifests -----------------------------------------------------------------------------------------------------


def test_local_manifests_follow_the_given_mapping_not_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _catalogue(tmp_path, "asset_store_a", {"source": SOURCE, "assets": []})
    monkeypatch.setenv(local.ENV, "0")
    assert [name for name, _ in local.local_manifests(tmp_path, ON)] == ["asset_store_a"]
    monkeypatch.setenv(local.ENV, "1")
    assert local.local_manifests(tmp_path, OFF) == []


def test_local_manifests_skip_what_is_not_a_readable_eula_catalogue_and_keep_reading(tmp_path: Path) -> None:
    _catalogue(tmp_path, "a_shared", {"source": SOURCE, "assets": []})  # not a local catalogue name
    _catalogue(tmp_path, "asset_store_b_broken", "{not json")
    _catalogue(tmp_path, "asset_store_c_other", {"source": "CC0", "assets": []})
    _catalogue(tmp_path, "asset_store_d_list", [SOURCE])
    (tmp_path / "asset_store_e_empty").mkdir()
    (tmp_path / "asset_store_f_file").write_text("{}", encoding="utf-8")
    _catalogue(tmp_path, "asset_store_g_nosource", {"assets": []})
    _catalogue(tmp_path, "asset_store_z", {"source": SOURCE, "assets": []})
    _catalogue(tmp_path, "asset_store_m", {"source": SOURCE, "assets": []})
    assert [name for name, _ in local.local_manifests(tmp_path, ON)] == ["asset_store_m", "asset_store_z"]


def test_local_manifests_of_a_missing_folder_are_empty(tmp_path: Path) -> None:
    assert local.local_manifests(tmp_path / "missing", ON) == []


# ---- furnish_rows --------------------------------------------------------------------------------------------------------


def test_furnish_rows_follow_the_given_mapping(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _catalogue(tmp_path, "asset_store_a", {"source": SOURCE, "assets": [_shelf("as_a_shelf")]})
    monkeypatch.setenv(local.ENV, "0")
    assert [row["id"] for _, row in local.furnish_rows(tmp_path, floors=False, env=ON)] == ["as_a_shelf"]
    monkeypatch.setenv(local.ENV, "1")
    assert local.furnish_rows(tmp_path, floors=False, env=OFF) == []


def test_furnish_rows_skip_rows_that_cannot_furnish_and_keep_reading(tmp_path: Path) -> None:
    no_id = _shelf("")
    outside = _shelf("as_a_outside", placement="ground")
    no_hint = {"id": "as_a_plain", "placement": "interior"}
    rows = [
        "not a row",
        no_id,
        outside,
        no_hint,
        _shelf("as_a_shop"),
        _shelf("as_a_home", floors=True),
        _shelf("as_a_counter", slot="counter", floors=True),
    ]
    _catalogue(tmp_path, "asset_store_a", {"source": SOURCE, "assets": rows})
    assert [row["id"] for _, row in local.furnish_rows(tmp_path, floors=False, env=ON)] == ["as_a_shop", "as_a_home", "as_a_counter"]
    assert [row["id"] for _, row in local.furnish_rows(tmp_path, floors=True, env=ON)] == ["as_a_home"]


def test_furnish_rows_for_the_floors_skip_shop_rows_and_keep_reading(tmp_path: Path) -> None:
    rows = [_shelf("as_a_shop"), _shelf("as_a_counter", slot="counter", floors=True), _shelf("as_a_home", floors=True)]
    _catalogue(tmp_path, "asset_store_a", {"source": SOURCE, "assets": rows})
    assert [(name, row["id"]) for name, row in local.furnish_rows(tmp_path, floors=True, env=ON)] == [("asset_store_a", "as_a_home")]


# ---- with_local_furnishing -----------------------------------------------------------------------------------------------


@pytest.fixture
def base() -> DetailConfig:
    return load_detail_config()  # ONWARD_LOCAL_CATALOGUES=0 in the suite: this machine's catalogues stay out


def _local_ids(entries: tuple[Any, ...]) -> list[str]:
    return [e.id for e in entries if e.id.startswith("as_")]


def test_a_shop_row_joins_only_its_named_sets_and_a_home_row_joins_the_generic_floor_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, base: DetailConfig
) -> None:
    monkeypatch.setenv(local.ENV, "1")
    rows = [_shelf("as_a_bakery", sets=["bakery"]), _shelf("as_a_home", sets=["bakery"], floors=True)]
    _catalogue(tmp_path, "asset_store_a", {"source": SOURCE, "assets": rows})
    merged = with_local_furnishing(base, tmp_path)
    shops = merged.interiors.shops.furnish.sets
    assert _local_ids(shops["bakery"].wall) == ["as_a_bakery", "as_a_home"]
    assert _local_ids(shops["generic"].wall) == []
    assert _local_ids(merged.interiors.furnish.sets["generic"].wall) == ["as_a_home"]


def test_a_disabled_furnishing_block_takes_no_local_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, base: DetailConfig) -> None:
    monkeypatch.setenv(local.ENV, "1")
    _catalogue(tmp_path, "asset_store_a", {"source": SOURCE, "assets": [_shelf("as_a_home", floors=True)]})
    shops_off = replace(base.interiors.shops, furnish=replace(base.interiors.shops.furnish, enabled=False))
    cfg = replace(base, interiors=replace(base.interiors, shops=shops_off))
    merged = with_local_furnishing(cfg, tmp_path)
    assert merged.interiors.shops.furnish is cfg.interiors.shops.furnish
    assert _local_ids(merged.interiors.furnish.sets["generic"].wall) == ["as_a_home"]
    floors_off = replace(base, interiors=replace(base.interiors, furnish=replace(base.interiors.furnish, enabled=False)))
    merged = with_local_furnishing(floors_off, tmp_path)
    assert merged.interiors.furnish is floors_off.interiors.furnish
    assert _local_ids(merged.interiors.shops.furnish.sets["generic"].wall) == ["as_a_home"]
    both_off = replace(cfg, interiors=replace(cfg.interiors, furnish=floors_off.interiors.furnish))
    assert with_local_furnishing(both_off, tmp_path) is both_off


def test_a_counter_row_may_be_one_metre_deep_and_a_too_deep_row_does_not_stop_the_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, base: DetailConfig
) -> None:
    monkeypatch.setenv(local.ENV, "1")
    rows = [_shelf("as_a_deep", depth=0.95), _shelf("as_a_counter", slot="counter", depth=0.95), _shelf("as_a_wall")]
    _catalogue(tmp_path, "asset_store_a", {"source": SOURCE, "assets": rows})
    generic = with_local_furnishing(base, tmp_path).interiors.shops.furnish.sets["generic"]
    assert _local_ids(generic.counter) == ["as_a_counter"]
    assert _local_ids(generic.wall) == ["as_a_wall"]


def test_a_row_without_triangles_counts_as_the_lightest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, base: DetailConfig) -> None:
    monkeypatch.setenv(local.ENV, "1")
    rows = [_shelf("as_a_one", triangles=1), _shelf("as_b_none", triangles=None)]
    _catalogue(tmp_path, "asset_store_a", {"source": SOURCE, "assets": rows})
    generic = with_local_furnishing(base, tmp_path).interiors.shops.furnish.sets["generic"]
    assert _local_ids(generic.wall) == ["as_b_none", "as_a_one"]
