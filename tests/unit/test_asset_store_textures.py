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


def _reference_jpeg(png: bytes) -> bytes:
    with Image.open(io.BytesIO(png)) as picture:
        image = picture.convert("RGB")
    image.thumbnail((MAX_PX, MAX_PX), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=92)
    return out.getvalue()


def test_a_row_and_its_manifest_are_whole_and_the_files_are_the_reference_encoding(tmp_path: Path) -> None:
    rng = np.random.default_rng(1)
    albedo = Image.fromarray(rng.integers(0, 256, (600, 1400, 3), dtype=np.uint8))
    raw = io.BytesIO()
    albedo.convert("RGBA").save(raw, format="PNG")
    normal = _png((120, 130, 250), (32, 32))
    path = _package(tmp_path, {"Assets/W/Aa_Diffuse.png": b"broken", "Assets/W/Brick Wall 2_Diffuse.png": raw.getvalue(),
                               "Assets/W/Brick Wall 2_Normal.png": normal, "Assets/W/zz_Diffuse.png": _png((4, 5, 6))})
    package = next(p for p in find_packages(tmp_path / "cache") if p.path == path)
    assets = tmp_path / "repo" / "assets"
    rows = texture_rows(package, assets, {"Aa": {"family": "rock", "note": "Unreadable, read first."},
                                          "Brick Wall 2": {"family": "concrete", "note": "Wall."},
                                          "missing": {"family": "rock", "note": "Not in the package."},
                                          "zz": {"family": "asphalt", "note": "Dark."}})
    folder = assets / package.catalogue
    data = (folder / "Brick_Wall_2_albedo.jpg").read_bytes()
    normal_data = (folder / "Brick_Wall_2_nor_gl.jpg").read_bytes()
    assert data == _reference_jpeg(raw.getvalue()) and normal_data == _reference_jpeg(normal)
    assert rows[0] == {
        "id": "as_wall_pack_Brick_Wall_2", "name": "Brick Wall 2", "family": "concrete", "provider": "asset_store",
        "license": "LicenseRef-Unity-Asset-Store-EULA", "license_url": "https://unity.com/legal/as-terms",
        "credit": "Brick Wall 2 from Wall Pack by Pub, Unity Asset Store EULA; local copy, never committed.", "source_url": "",
        "surface": "ground", "bound": False, "path": f"assets/{package.catalogue}/Brick_Wall_2_albedo.jpg",
        "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "note": "Albedo 1024 px. Wall.",
        "normal": {"map": "nor_gl", "path": f"assets/{package.catalogue}/Brick_Wall_2_nor_gl.jpg",
                   "sha256": hashlib.sha256(normal_data).hexdigest(), "bytes": len(normal_data)}}
    assert [row["name"] for row in rows] == ["Brick Wall 2", "zz"] and rows[1]["surface"] == "ground"  # read past a missing set
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert manifest == {"schema": LIBRARY_SCHEMA, "schema_version": "0.1.0",
                        "source": "Unity Asset Store EULA (Extension Asset), local only: Pub / Wall Pack. Converted on this "
                                  "machine; never committed or redistributed outside a built map.",
                        "package": {"publisher": "Pub", "category": "Textures MaterialsWall", "name": "Wall Pack"}, "textures": rows}
    assert (folder / "License.txt").read_text(encoding="utf-8") == (
        "Wall Pack by Pub, from the Unity Asset Store, under the Standard Unity Asset Store EULA.\n"
        "Local copy for this user's maps only. Do not commit, share or publish these files.\n")


def test_a_long_package_name_and_a_wall_family_shape_the_row(tmp_path: Path) -> None:
    path = tmp_path / "cache" / "Pub" / "Textures" / "Very Long Package Name For Walls.unitypackage"
    path.parent.mkdir(parents=True)
    with tarfile.open(path, "w:gz") as archive:
        for k, (p, data) in enumerate({"A/x_Diffuse.png": _png((1, 1, 1))}.items()):
            for name, payload in (("pathname", p.encode()), ("asset", data)):
                info = tarfile.TarInfo(f"./{k:032x}/{name}")  # a "./" prefix, as some exporters write
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
    package = next(iter(find_packages(tmp_path / "cache")))
    for family, surface in (("plaster", "wall"), ("brick", "wall"), ("paving", "ground"), ("concrete", "ground"), ("asphalt", "ground")):
        row = texture_rows(package, tmp_path / family, {"x": {"family": family, "note": "n"}})[0]
        assert row["surface"] == surface and row["id"] == "as_very_long_package_na_x"
    long_stem = "x" * 120
    with tarfile.open(path, "w:gz") as archive:
        for name, payload in (("pathname", f"A/{long_stem}_Diffuse.png".encode()), ("asset", _png((1, 1, 1)))):
            info = tarfile.TarInfo(f"{0:032x}/{name}")
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    row = texture_rows(package, tmp_path / "long", {long_stem: {"family": "rock", "note": "n"}})[0]
    assert len(row["id"]) == 80 and row["id"].startswith("as_very_long_package_na_xxx")


def test_only_small_images_join_a_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import onward_generator.assetstore.textures as textures

    path = _package(tmp_path, {"A/big_Diffuse.png": _png((1, 1, 1), (64, 64)), "A/edge_Diffuse.png": _png((1, 1, 1), (8, 8)),
                               "A/notes_Diffuse.txt": b"hello"})
    size = len(_png((1, 1, 1), (8, 8)))
    monkeypatch.setattr(textures, "MAX_MEMBER_BYTES", size)
    with tarfile.open(path) as archive:
        assert sorted(texture_sets(archive)) == ["edge"]  # exactly the limit is kept; a text file is not an image


def test_a_row_without_an_id_is_not_read_back(tmp_path: Path) -> None:
    folder = tmp_path / "asset_store_x"
    folder.mkdir()
    (folder / "manifest.json").write_text(json.dumps({"schema": LIBRARY_SCHEMA, "textures": [{"id": "a"}, {"name": "no id"}, "x"]}),
                                          encoding="utf-8")
    assert local_texture_rows(tmp_path) == [{"id": "a"}]


def test_the_catalogue_reads_this_repository_s_local_textures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from onward_generator.materials import library_plan

    folder = tmp_path / "assets" / "asset_store_x"
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text(json.dumps({"schema": LIBRARY_SCHEMA, "textures": [{"id": "as_x_local"}]}), encoding="utf-8")
    monkeypatch.setattr(library_plan, "REPO_ROOT", tmp_path)
    monkeypatch.setenv("ONWARD_LOCAL_CATALOGUES", "1")
    assert {"id": "as_x_local"} in library_plan.catalogue_rows()


def test_the_textures_command_imports_the_table_s_packages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                           capsys: pytest.CaptureFixture[str]) -> None:
    import onward_generator.assetstore.__main__ as cli

    _package(tmp_path, {"A/w1_Diffuse.png": _png((1, 1, 1)), "A/w1_Normal.png": _png((128, 128, 255)), "A/w2_Diffuse.png": _png((2, 2, 2))})
    second = tmp_path / "cache" / "Pub" / "Textures MaterialsGround" / "Ground Pack.unitypackage"
    second.parent.mkdir(parents=True)
    second.write_bytes(_package(tmp_path / "g", {"B/g1_Diffuse.png": _png((3, 3, 3))}).read_bytes())
    monkeypatch.setattr("onward_generator.assetstore.textures.read_table",
                        lambda: {"wall_pack": {"w1": {"family": "plaster", "note": "a"}, "w2": {"family": "rock", "note": "b"}},
                                 "absent_pack": {"x": {"family": "rock", "note": "c"}},
                                 "ground_pack": {"g1": {"family": "paving", "note": "d"}}})
    monkeypatch.setattr(cli, "ASSETS", tmp_path / "assets")
    assert cli.main(["textures", "--cache", str(tmp_path / "cache")]) == 0
    out = capsys.readouterr().out
    assert "absent_pack: not downloaded\n" in out and "wall_pack: 2 textures of 2 named, 1 with a normal map\n" in out
    assert "ground_pack: 1 textures of 1 named, 0 with a normal map\n" in out
    assert f"\n3 textures under {tmp_path / 'assets'}/asset_store_*\n" in out  # both packages counted
    assert cli.main(["textures", "--cache", str(tmp_path / "cache"), "--only", "wall_pack", "absent_pack"]) == 0
    assert "wall_pack: 2 textures" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="asset-store: other, zzz has no texture sets in config/dress/asset_store_textures.json"):
        cli.main(["textures", "--cache", str(tmp_path / "cache"), "--only", "zzz", "other"])
