"""The interior catalogue loader shop furnishing reads (ADR-031), and the licence record behind it."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from onward_generator.detail.config import FURNISH_CATALOGUES, REPO_ROOT, load_detail_config
from onward_generator.overgrow.library import interior_licence, read_interior_catalogue, read_library
from onward_generator.overgrow.library_catalog import LibraryError

ASSETS = REPO_ROOT / "assets"
FURNISH = load_detail_config().interiors.shops.furnish
DEFAULT_IDS = sorted({e.id for s in FURNISH.sets.values() for slot in ("counter", "wall", "decor") for e in s.slot(slot)})


def _catalogue(tmp_path: Path, name: str, source: str, rows: list[dict], files: dict[str, Path]) -> Path:
    root = tmp_path / "lib"
    directory = root / name
    directory.mkdir(parents=True)
    for file, origin in files.items():
        shutil.copyfile(origin, directory / file)
    (directory / "manifest.json").write_text(json.dumps({"source": source, "assets": rows}), encoding="utf-8")
    return root


SHELF = {"id": "Shelf_01", "file": "Shelf_01.glb", "category": "furniture", "role": "shelf", "surfaces": ["interior"],
         "anchor": "ground", "placement": "interior"}


def test_only_interior_rows_of_the_allowed_catalogues_load() -> None:
    props, _rejects = read_interior_catalogue(ASSETS, FURNISH_CATALOGUES, DEFAULT_IDS)
    assert sorted(props) == DEFAULT_IDS
    assert {p.catalogue for p in props.values()} <= set(FURNISH_CATALOGUES)
    assert all(p.licence == "CC0-1.0" and len(p.glb_sha256) == 64 for p in props.values())
    assert all(p.prop.spec.placement == "interior" for p in props.values())


def test_a_kit_only_row_a_foreign_catalogue_and_a_non_commercial_licence_are_refused(tmp_path: Path) -> None:
    glb = ASSETS / "polyhaven_shop" / "Shelf_01.glb"
    kit = _catalogue(tmp_path / "a", "polyhaven_shop", "CC0 1.0", [{**SHELF, "placement": "kit_only"}], {"Shelf_01.glb": glb})
    assert read_interior_catalogue(kit, ["polyhaven_shop"])[0] == {}
    assert [r.reason for r in read_interior_catalogue(kit, ["polyhaven_shop"])[1]] == ["not_interior"]
    other = _catalogue(tmp_path / "b", "someone_shop", "CC0 1.0", [SHELF], {"Shelf_01.glb": glb})
    assert read_interior_catalogue(other, FURNISH_CATALOGUES) == ({}, [])
    nc = _catalogue(tmp_path / "c", "polyhaven_shop", "https://example.org, CC BY-NC 4.0", [SHELF], {"Shelf_01.glb": glb})
    props, rejects = read_interior_catalogue(nc, ["polyhaven_shop"])
    assert props == {} and [r.reason for r in rejects] == ["licence"]
    ok = _catalogue(tmp_path / "d", "polyhaven_shop", "https://polyhaven.com, CC0 1.0.", [SHELF], {"Shelf_01.glb": glb})
    assert list(read_interior_catalogue(ok, ["polyhaven_shop"])[0]) == ["Shelf_01"]


def test_a_transparent_glb_is_refused() -> None:
    props, rejects = read_interior_catalogue(ASSETS, ["kenney_furniture"], ["kenney_furniture_kitchenFridge"])
    assert props == {} and [(r.asset_id, r.reason) for r in rejects] == [("kenney_furniture_kitchenFridge", "alpha_mode_blend")]


def test_every_allowed_catalogue_is_cc0_in_its_manifest_and_in_the_licence_record() -> None:
    record = (REPO_ROOT / "docs" / "legal" / "data-sources.md").read_text(encoding="utf-8")
    shop_rows = [line for line in record.splitlines() if "CC0 1.0" in line and ("assets/kenney_" in line or "assets/polyhaven_shop" in line)]
    for name in FURNISH_CATALOGUES:
        manifest = json.loads((ASSETS / name / "manifest.json").read_text(encoding="utf-8"))
        assert interior_licence(manifest["source"]) == "CC0-1.0", name
        assert any(f"assets/{name}" in line for line in shop_rows), name
    assert interior_licence("CC BY-NC 4.0") is None


def test_street_overgrow_still_refuses_every_interior_row() -> None:
    for name in FURNISH_CATALOGUES:
        props, rejects = read_library(ASSETS / name)
        rows = json.loads((ASSETS / name / "manifest.json").read_text(encoding="utf-8"))["assets"]
        assert props == []
        assert {r.reason for r in rejects} == {"interior"} and len(rejects) == len(rows)


def _row(asset_id: str, file: str = "Shelf_01.glb", **extra) -> dict:
    return {**SHELF, "id": asset_id, "file": file, **extra}


def test_each_kind_of_bad_row_is_refused_and_the_rows_after_it_still_load(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    broken = root / "kenney_food"
    broken.mkdir(parents=True)
    (broken / "manifest.json").write_text("{ not json", encoding="utf-8")
    directory = root / "polyhaven_shop"
    directory.mkdir()
    shutil.copyfile(ASSETS / "polyhaven_shop" / "Shelf_01.glb", directory / "Shelf_01.glb")
    shutil.copyfile(ASSETS / "kenney_furniture" / "kitchenFridge.glb", directory / "kitchenFridge.glb")
    (directory / "corrupt.glb").write_bytes(b"glTF not really")
    rows = [
        "not a row", {"id": "no_file"}, {"file": "Shelf_01.glb"},
        _row("refused", refused="unknown_scale"), _row("ok_after_refused"),
        {"id": "no_category", "file": "Shelf_01.glb", "placement": "interior"}, _row("ok_after_no_category"),
        _row("no_glb", file="nope.glb"), _row("ok_after_no_glb"),
        _row("corrupt", file="corrupt.glb"), _row("ok_after_corrupt"),
        _row("fridge", file="kitchenFridge.glb"), _row("ok_after_fridge"),
        _row("flat_plant", category="vegetation", anchor="flat"), _row("ok_after_plant"),
        _row("turned", yaw_deg=90),
    ]
    (directory / "manifest.json").write_text(json.dumps({"source": "CC0 1.0", "assets": rows}), encoding="utf-8")
    props, rejects = read_interior_catalogue(root, FURNISH_CATALOGUES)
    assert [(r.asset_id, r.reason) for r in rejects] == [
        ("refused", "unknown_scale"), ("no_category", "unreadable"), ("no_glb", "unreadable"), ("corrupt", "unreadable"),
        ("fridge", "alpha_mode_blend"), ("flat_plant", "flat_vegetation_not_flat"),
    ]
    assert sorted(props) == sorted(["ok_after_refused", "ok_after_no_category", "ok_after_no_glb", "ok_after_corrupt",
                                    "ok_after_fridge", "ok_after_plant", "turned"])
    plain, turned = props["ok_after_plant"].prop.size_m, props["turned"].prop.size_m
    assert plain == pytest.approx((1.003, 0.257, 2.08), abs=1e-3)
    assert turned == pytest.approx((plain[1], plain[0], plain[2]), abs=1e-6)
    assert props["turned"].catalogue == "polyhaven_shop" and props["turned"].parts == 1


def test_a_manifest_that_fails_its_schema_names_its_file(tmp_path: Path) -> None:
    directory = tmp_path / "lib" / "polyhaven_shop"
    shutil.copytree(ASSETS / "polyhaven_shop", directory)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    del manifest["assets"][0]["placement"]
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(LibraryError, match=f"^{re.escape(str(directory / 'manifest.json'))}/assets/0: 'placement' is a required property$"):
        read_interior_catalogue(tmp_path / "lib", ["polyhaven_shop"])


def test_one_id_in_two_catalogues_is_an_error(tmp_path: Path) -> None:
    glb = ASSETS / "polyhaven_shop" / "Shelf_01.glb"
    root = _catalogue(tmp_path, "polyhaven_shop", "CC0 1.0", [SHELF], {"Shelf_01.glb": glb})
    other = root / "kenney_furniture"
    other.mkdir()
    shutil.copyfile(glb, other / "Shelf_01.glb")
    (other / "manifest.json").write_text(json.dumps({"source": "CC0 1.0", "assets": [SHELF]}), encoding="utf-8")
    with pytest.raises(LibraryError) as caught:
        read_interior_catalogue(root, FURNISH_CATALOGUES)
    assert str(caught.value) == "Shelf_01: declared in kenney_furniture and polyhaven_shop"
