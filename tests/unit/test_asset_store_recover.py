"""Albedos a refused prefab variant held, read back for its raw model (ADR-035 amendment of 12 November 2026)."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

from PIL import Image

from onward_generator.assetstore.recover import ALBEDO_NAMES, MAX_PX, albedo_png, material_overrides, recover_albedos

MODEL, PREFAB, RUG, FRINGE, TEX, OTHER = ("a" * 32, "b" * 32, "c" * 32, "d" * 32, "e" * 32, "f" * 32)
VARIANT = f"""--- !u!1001 &1
PrefabInstance:
  m_Modification:
    m_Modifications:
    - target: {{fileID: -75, guid: {MODEL},
        type: 3}}
      propertyPath: m_Materials.Array.data[0]
      value:{' '}
      objectReference: {{fileID: 2100000, guid: {RUG}, type: 2}}
    - target: {{fileID: -75, guid: {MODEL},
        type: 3}}
      propertyPath: m_Materials.Array.data[1]
      value:{' '}
      objectReference: {{fileID: 2100000, guid: {FRINGE}, type: 2}}
  m_SourcePrefab: {{fileID: 100100000, guid: {MODEL}, type: 3}}
"""


def _mat(textures: dict[str, str], surface: int = 0) -> str:
    envs = "".join(f"    - {name}:\n        m_Texture: {{fileID: 2800000, guid: {guid}, type: 3}}\n" for name, guid in textures.items())
    return f"Material:\n  m_SavedProperties:\n    m_TexEnvs:\n{envs}    m_Floats:\n    - _Surface: {surface}\n"


def _png(px: int, colour: tuple[int, int, int] = (150, 40, 30)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (px, px), colour).save(out, format="PNG")
    return out.getvalue()


def _package(path: Path, members: dict[str, tuple[str, bytes]]) -> Path:
    with tarfile.open(path, "w:gz") as archive:
        for guid, (pathname, data) in members.items():
            for name, payload in (("pathname", pathname.encode()), ("asset", data)):
                info = tarfile.TarInfo(f"{guid}/{name}")
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
    return path


def _carpets(tmp_path: Path, rug: str | None = None) -> Path:
    return _package(tmp_path / "carpets.unitypackage", {
        MODEL: ("Assets/C/Models/Rug_004.FBX", b"fbx"),
        PREFAB: ("Assets/C/Prefab/Rug_004 Variant.prefab", VARIANT.encode()),
        RUG: ("Assets/C/Materials/Mat_Rug.mat", (rug or _mat({"_BumpMap": OTHER, "_BaseMap": TEX})).encode()),
        FRINGE: ("Assets/C/Materials/Mat_Fringe.mat", _mat({"_BaseMap": TEX}, surface=1).encode()),
        TEX: ("Assets/C/Textures/Rug_04.PNG", _png(1024)),
        OTHER: ("Assets/C/Textures/Normal.PNG", _png(8, (128, 128, 255))),
    })


MTL = "newmtl Rug_004_Carpet_04\nKd 0.2 0.2 0.2\nnewmtl Rug_004_Fringe\nKd 1 1 1\n"


def test_the_variant_gives_the_raw_model_its_rug_albedo_and_keeps_the_transparent_fringe(tmp_path: Path) -> None:
    package = _carpets(tmp_path)
    out = tmp_path / "export"
    out.mkdir()
    mtl, recovered = recover_albedos(package, "Assets/C/Models/Rug_004.FBX", MTL, out, "Rug_004")
    assert recovered == 1
    assert mtl == "newmtl Rug_004_Carpet_04\nKd 0.2 0.2 0.2\nmap_Kd Rug_004_recovered_0.png\nnewmtl Rug_004_Fringe\nKd 1 1 1\n"
    with Image.open(out / "Rug_004_recovered_0.png") as picture:
        assert picture.size == (MAX_PX, MAX_PX) == (512, 512) and picture.mode == "RGB"
        assert picture.getpixel((5, 5)) == (150, 40, 30)
    assert sorted(p.name for p in out.iterdir()) == ["Rug_004_recovered_0.png"]


def test_nothing_changes_without_a_dressing_prefab_or_a_readable_package(tmp_path: Path) -> None:
    package = _carpets(tmp_path)
    out = tmp_path / "export"
    out.mkdir()
    textured = "newmtl A\nKd 1 1 1\nmap_Kd a.png\n"
    assert recover_albedos(package, "Assets/C/Models/Rug_004.FBX", textured, out, "A") == (textured, 0)
    assert recover_albedos(package, "Assets/C/Models/Other.FBX", MTL, out, "B") == (MTL, 0)
    (tmp_path / "broken.unitypackage").write_bytes(b"not a tar")
    assert recover_albedos(tmp_path / "broken.unitypackage", "Assets/C/Models/Rug_004.FBX", MTL, out, "C") == (MTL, 0)
    assert list(out.iterdir()) == []


def test_the_overrides_and_the_albedo_are_read_by_name(tmp_path: Path) -> None:
    from onward_generator.assetstore.recover import _Package

    assert ALBEDO_NAMES[:3] == ("_BaseMap", "_BaseColorMap", "_MainTex")
    with tarfile.open(_carpets(tmp_path, rug=_mat({"_MainTex": TEX, "_BaseColorMap": OTHER})), "r:gz") as archive:
        package = _Package(archive)
        assert material_overrides(package, "Assets/C/Models/Rug_004.FBX") == {0: RUG, 1: FRINGE}
        assert material_overrides(package, "Assets/C/Missing.FBX") == {}
        assert material_overrides(package, "Assets/C/Textures/Normal.PNG") == {}  # in the package, but no prefab dresses it
        rug = albedo_png(package, RUG)
        assert rug is not None
        with Image.open(io.BytesIO(rug)) as picture:  # _BaseColorMap comes before _MainTex
            assert picture.size == (8, 8) and picture.getpixel((0, 0)) == (128, 128, 255)
        assert albedo_png(package, FRINGE) is None  # transparent
        assert albedo_png(package, "0" * 32) is None  # not in the package
    broken = _mat({"_BaseMap": OTHER})
    with tarfile.open(_package(tmp_path / "b.unitypackage", {RUG: ("Assets/M.mat", broken.encode()), OTHER: ("Assets/T.png", b"x")}), "r:gz") as archive:
        assert albedo_png(_Package(archive), RUG) is None  # an image that does not open


RUG2 = "9" * 32
VARIANT2 = VARIANT.replace(FRINGE, RUG2)


def _two_rugs(tmp_path: Path, rug: str | None = None) -> Path:
    return _package(tmp_path / "two.unitypackage", {
        MODEL: ("Assets/C/Models/Rug_004.FBX", b"fbx"),
        PREFAB: ("Assets/C/Prefab/Rug_004 Variant.prefab", VARIANT2.encode()),
        RUG: ("Assets/C/Materials/Mat_Rug.mat", (rug or _mat({"_BaseMap": TEX})).encode()),
        RUG2: ("Assets/C/Materials/Mat_Rug2.mat", _mat({"_MainTex": OTHER}).encode()),
        TEX: ("Assets/C/Textures/Rug_04.PNG", _png(1024)),
        OTHER: ("Assets/C/Textures/Rug_05.PNG", _png(8, (10, 200, 30))),
    })


def test_every_untextured_material_is_counted_and_a_textured_one_kept(tmp_path: Path) -> None:
    package = _two_rugs(tmp_path)
    out = tmp_path / "export"
    out.mkdir()
    mtl, recovered = recover_albedos(package, "Assets/C/Models/Rug_004.FBX", "newmtl A\nKd 1 1 1\nnewmtl B\nKd 1 1 1 ", out, "S")
    assert recovered == 2
    assert mtl == "newmtl A\nKd 1 1 1\nmap_Kd S_recovered_0.png\nnewmtl B\nKd 1 1 1 \nmap_Kd S_recovered_1.png\n"
    kept, again = recover_albedos(package, "Assets/C/Models/Rug_004.FBX", "newmtl A\nKd 1 1 1\nmap_Kd own.png\nnewmtl B\nKd 1 1 1\n", out, "T")
    assert again == 1 and kept == "newmtl A\nKd 1 1 1\nmap_Kd own.png\nnewmtl B\nKd 1 1 1\nmap_Kd T_recovered_1.png\n"


def test_the_albedo_is_rgb_resampled_with_lanczos_and_the_next_name_tried(tmp_path: Path) -> None:
    from onward_generator.assetstore.recover import _Package

    checker = Image.new("RGBA", (1024, 1024))
    checker.putdata([((x // 3 + y // 5) % 2 * 255, 90, 40, 128) for y in range(1024) for x in range(1024)])
    raw = io.BytesIO()
    checker.save(raw, format="PNG")
    broken = _mat({"_BaseMap": RUG2, "_MainTex": TEX})  # _BaseMap points at a .mat, which does not open as an image
    with tarfile.open(_package(tmp_path / "c.unitypackage", {RUG: ("Assets/M.mat", broken.encode()), RUG2: ("Assets/N.mat", b"Material:"),
                                                              TEX: ("Assets/T.png", raw.getvalue())}), "r:gz") as archive:
        png = albedo_png(_Package(archive), RUG)
    assert png is not None
    expected = checker.convert("RGB")
    expected.thumbnail((MAX_PX, MAX_PX), Image.Resampling.LANCZOS)
    with Image.open(io.BytesIO(png)) as picture:
        assert picture.mode == "RGB" and list(picture.getdata())[:4096] == list(expected.getdata())[:4096]


def test_build_catalogue_textures_a_raw_model_from_its_variant(tmp_path: Path) -> None:
    import json

    from onward_generator.assetstore.importer import build_catalogue
    from onward_generator.assetstore.packages import find_packages
    from onward_generator.generation.gltf import read_glb

    cache = tmp_path / "cache" / "Pub" / "3D ModelsPropsInterior"
    cache.mkdir(parents=True)
    _carpets(cache)
    (cache / "carpets.unitypackage").rename(cache / "Rug Carpets.unitypackage")
    package = find_packages(tmp_path / "cache")[0]
    exported = tmp_path / "export"
    exported.mkdir()
    (exported / "Rug_004.obj").write_text(
        "v 0 0 0\nv 1.2 0 0\nv 1.2 0.02 2\nv 0 0.02 2\nvt 0 0\nvt 1 0\nvt 1 1\nvt 0 1\nusemtl Rug_004_Carpet_04\nf 1/1 2/2 3/3\n"
        "usemtl Rug_004_Fringe\nf 1/1 3/3 4/4\n", encoding="utf-8")
    (exported / "Rug_004.mtl").write_text(MTL, encoding="utf-8")
    item = {"prefab": "Assets/C/Models/Rug_004.FBX", "file": "Rug_004.obj", "status": "exported", "size": [1.2, 2.0, 0.01]}
    (exported / "report.json").write_text(json.dumps({"id": "rug_carpets", "items": [item]}), encoding="utf-8")
    result = build_catalogue(package, exported, tmp_path / "assets")
    assert result.rows == 1
    data = (tmp_path / "assets" / "asset_store_rug_carpets" / "Rug_004.glb").read_bytes()
    doc, binary = read_glb(data)
    sizes = []
    for image in doc["images"]:
        view = doc["bufferViews"][image["bufferView"]]
        with Image.open(io.BytesIO(binary[view.get("byteOffset", 0):view.get("byteOffset", 0) + view["byteLength"]])) as picture:
            sizes.append(picture.size)
    assert (512, 512) in sizes  # the rug's own albedo, not a 4 px grey
    assert (exported / "Rug_004_recovered_0.png").exists()
