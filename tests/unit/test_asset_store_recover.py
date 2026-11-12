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
        rug = albedo_png(package, RUG)
        assert rug is not None
        with Image.open(io.BytesIO(rug)) as picture:  # _BaseColorMap comes before _MainTex
            assert picture.size == (8, 8) and picture.getpixel((0, 0)) == (128, 128, 255)
        assert albedo_png(package, FRINGE) is None  # transparent
        assert albedo_png(package, "0" * 32) is None  # not in the package
    broken = _mat({"_BaseMap": OTHER})
    with tarfile.open(_package(tmp_path / "b.unitypackage", {RUG: ("Assets/M.mat", broken.encode()), OTHER: ("Assets/T.png", b"x")}), "r:gz") as archive:
        assert albedo_png(_Package(archive), RUG) is None  # an image that does not open
