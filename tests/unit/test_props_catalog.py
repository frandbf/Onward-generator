"""Prop catalogue: Asset Store paths must exist; buildings of pack 71042 stay out."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from onward_generator.cli import main
from onward_generator.props.catalog import (
    CATALOG_PATH,
    CatalogError,
    bind_inventory,
    catalog_document,
    load_catalog,
    pack_allows,
)
from tests.conftest import MURCIA_CENTER

REPO = Path(__file__).resolve().parents[2]


def test_props_catalog_cli_lists_zero_usable(capsys) -> None:
    assert main(["props", "catalog"]) == 0
    assert "usable=0" in capsys.readouterr().out


def test_committed_catalog_is_pending_and_has_license_text() -> None:
    _ = MURCIA_CENTER
    catalog = load_catalog()
    assert catalog.path == CATALOG_PATH
    assert {p.id for p in catalog.packs} == {"327313", "71042", "251403"}
    assert catalog.props == ()
    assert catalog.usable_props == ()
    for pack in catalog.packs:
        assert pack.status == "approved"
        assert "Standard Unity Asset Store EULA" in pack.license_text
        assert pack.license_url.startswith("https://")


def test_european_city_buildings_are_excluded() -> None:
    catalog = load_catalog()
    city = catalog.packs_by_id["71042"]
    assert pack_allows(city, "Assets/City/Bench_01.fbx")
    assert pack_allows(city, "StreetLamp.prefab")
    assert not pack_allows(city, "Assets/City/Building_A.fbx")
    assert not pack_allows(city, "HouseCorner.prefab")
    assert not pack_allows(city, "RoadTile_2x2.fbx")


def test_asset_store_without_file_is_rejected(tmp_path: Path) -> None:
    src = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    src["props"] = [
        {
            "prop_id": "vehicle_missing",
            "category": "vehicle",
            "pack_id": "327313",
            "source": "asset_store",
            "path": "missing/car.fbx",
            "license": "Standard Unity Asset Store EULA",
            "lods": {"lod0": "missing/car.fbx"},
            "has_collision": False,
            "allowed_origin": ["osm", "placed"],
            "notes": "should fail",
        }
    ]
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(src), encoding="utf-8")
    with pytest.raises(CatalogError, match="does not exist"):
        load_catalog(path)


def test_pending_prop_may_point_at_a_missing_file(tmp_path: Path) -> None:
    src = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    src["props"] = [
        {
            "prop_id": "vehicle_later",
            "category": "vehicle",
            "pack_id": "327313",
            "source": "pending",
            "path": "~/Unity/OnwardPhase0Project/Assets/ThirdParty/AssetStore/327313/Car.fbx",
            "license": "Standard Unity Asset Store EULA",
            "lods": {},
            "has_collision": False,
            "allowed_origin": ["osm", "placed"],
            "notes": "waiting for Unity import",
        }
    ]
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(src), encoding="utf-8")
    catalog = load_catalog(path)
    assert catalog.props[0].source == "pending"
    assert catalog.usable_props == ()


def test_bind_inventory_keeps_furniture_and_drops_buildings(tmp_path: Path) -> None:
    mesh = tmp_path / "Bench_01.fbx"
    mesh.write_bytes(b"fbx")
    building = tmp_path / "Building_A.fbx"
    building.write_bytes(b"fbx")
    car = tmp_path / "LowPolyCar_02.prefab"
    car.write_bytes(b"prefab")
    catalog = load_catalog()
    bound = bind_inventory(
        catalog,
        [
            {"pack_id": "71042", "path": str(mesh)},
            {"pack_id": "71042", "path": str(building)},
            {"pack_id": "327313", "path": str(car)},
            {"pack_id": "71042", "path": str(tmp_path / "readme.txt")},
        ],
    )
    ids = {e.prop_id for e in bound.props}
    assert any("bench" in i for i in ids)
    assert any("car" in i for i in ids)
    assert not any("building" in i for i in ids)
    usable = bound.usable_props
    assert len(usable) == 2
    assert all(e.source == "asset_store" for e in usable)
    assert all(e.usable for e in usable)
    assert all(e.lods.get("lod0") for e in usable)
    doc = catalog_document(bound)
    assert doc["schema"] == "onward_generator.prop_catalog"
    assert len(doc["props"]) == 2
