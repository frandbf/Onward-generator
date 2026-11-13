"""Asset Store texture packages (ADR-035 amendment of 13 November 2026): albedo and OpenGL normal map of each set."""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from onward_generator.assetstore.packages import find_packages
from onward_generator.assetstore.textures import (
    LIBRARY_SCHEMA,
    MAX_PX,
    TABLE_PATH,
    local_texture_rows,
    opengl_normal,
    read_table,
    texture_rows,
    texture_sets,
)


def _png(colour: tuple[int, int, int], size: tuple[int, int] = (16, 16)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, colour).save(out, format="PNG")
    return out.getvalue()


def _package(tmp_path: Path, members: dict[str, bytes]) -> Path:
    path = tmp_path / "cache" / "Pub" / "Textures MaterialsWall" / "Wall Pack.unitypackage"
    path.parent.mkdir(parents=True)
    with tarfile.open(path, "w:gz") as archive:
        for k, (asset_path, data) in enumerate(members.items()):
            guid = f"{k:032x}"
            for name, payload in (("pathname", asset_path.encode()), ("asset", data)):
                info = tarfile.TarInfo(f"{guid}/{name}")
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
    return path


def test_the_table_names_families_the_texture_plan_knows() -> None:
    table = read_table()
    assert TABLE_PATH.name == "asset_store_textures.json" and sorted(table) == ["18_high_resolution_wall_textures",
                                                                              "p3d_outdoor_wall_tile_texture_pack_lr"]
    families = {spec["family"] for sets in table.values() for spec in sets.values()}
    assert families <= {"plaster", "brick", "rock", "paving", "concrete", "asphalt"}
    assert sum(len(sets) for sets in table.values()) == 26


def test_the_sets_pair_each_albedo_with_its_normal_map_and_read_directx() -> None:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for k, path in enumerate(("A/wall01_Diffuse.tga", "A/wall01_Normal.tga", "A/wall01_Height.tga", "B/T01_Base_Color.png",
                                  "B/T01_Normal_DirectX.png", "B/T01_Roughness.png", "C/readme.txt", "D/Ground001.tif")):
            for name, payload in (("pathname", path.encode()), ("asset", b"x")):
                info = tarfile.TarInfo(f"{k:032x}/{name}")
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
    buffer.seek(0)
    with tarfile.open(fileobj=buffer) as archive:
        sets = texture_sets(archive)
        assert sorted(sets) == ["T01", "wall01"]
        assert sorted(sets["wall01"]) == ["albedo", "normal"] and sets["wall01"]["normal"][1] is False
        assert sets["T01"]["normal"][1] is True and sets["T01"]["albedo"][1] is False


def test_a_directx_normal_map_has_its_green_flipped() -> None:
    image = Image.new("RGB", (2, 2), (128, 40, 255))
    assert opengl_normal(image, False) is image
    assert np.asarray(opengl_normal(image, True))[0, 0].tolist() == [128, 215, 255]


def test_the_chosen_sets_are_written_with_their_rows_and_read_back(tmp_path: Path) -> None:
    path = _package(tmp_path, {
        "Assets/W/wall01_Diffuse.png": _png((180, 160, 140), (2048, 1024)), "Assets/W/wall01_Normal_DirectX.png": _png((128, 40, 255)),
        "Assets/W/wall02_Diffuse.png": _png((90, 90, 90)), "Assets/W/wall03_Diffuse.png": b"broken",
        "Assets/W/wall09_Normal.png": _png((128, 128, 255)),
    })
    package = next(p for p in find_packages(tmp_path / "cache") if p.path == path)
    assets = tmp_path / "repo" / "assets"
    chosen = {"wall01": {"family": "plaster", "note": "Render."}, "wall02": {"family": "paving", "note": "Slabs."},
              "wall03": {"family": "rock", "note": "Unreadable."}, "wall09": {"family": "rock", "note": "No albedo."}}
    rows = texture_rows(package, assets, chosen)
    assert [row["name"] for row in rows] == ["wall01", "wall02"]  # an unreadable albedo and a lone normal map are left out
    first, second = rows
    folder = assets / package.catalogue
    assert first["path"] == f"assets/{package.catalogue}/wall01_albedo.jpg" and first["family"] == "plaster" and first["surface"] == "wall"
    assert second["surface"] == "ground" and "normal" not in second
    assert first["id"] == "as_wall_pack_wall01" and first["license"] == "LicenseRef-Unity-Asset-Store-EULA" and first["provider"] == "asset_store"
    with Image.open(folder / "wall01_albedo.jpg") as albedo:
        assert albedo.size == (MAX_PX, MAX_PX // 2)
    assert hashlib.sha256((folder / "wall01_albedo.jpg").read_bytes()).hexdigest() == first["sha256"]
    with Image.open(folder / "wall01_nor_gl.jpg") as normal:
        assert abs(int(np.asarray(normal.convert("RGB"))[0, 0, 1]) - 215) <= 3  # flipped from DirectX
    assert first["normal"]["path"] == f"assets/{package.catalogue}/wall01_nor_gl.jpg" and first["normal"]["map"] == "nor_gl"
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema"] == LIBRARY_SCHEMA and manifest["textures"] == rows and "never committed" in manifest["source"]
    assert "Do not commit" in (folder / "License.txt").read_text(encoding="utf-8")
    (assets / "asset_store_broken").mkdir()
    (assets / "asset_store_broken" / "manifest.json").write_text("{", encoding="utf-8")
    (assets / "asset_store_models").mkdir()
    (assets / "asset_store_models" / "manifest.json").write_text(json.dumps({"schema": "x", "assets": []}), encoding="utf-8")
    assert local_texture_rows(assets) == rows
    assert texture_rows(package, tmp_path / "other", {"wall09": chosen["wall09"]}) == []
    assert not (tmp_path / "other").exists()


def test_the_local_textures_join_the_catalogue_only_while_local_catalogues_are_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from onward_generator.materials import library_plan

    row = {"id": "as_x_wall01", "family": "plaster", "path": "assets/asset_store_x/wall01_albedo.jpg"}
    monkeypatch.setattr("onward_generator.assetstore.textures.local_texture_rows", lambda assets: [row])
    monkeypatch.setenv("ONWARD_LOCAL_CATALOGUES", "1")
    assert row in library_plan.catalogue_rows()
    monkeypatch.setenv("ONWARD_LOCAL_CATALOGUES", "0")
    assert row not in library_plan.catalogue_rows()
