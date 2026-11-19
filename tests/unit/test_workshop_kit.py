"""tools/unity/workshop_kit.py: one Workshop kit per map, the latest, with the Dashboard's fields filled in."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import struct
import sys
import zipfile
import zlib
from datetime import date
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCENE_GUID = "e8cb4435207314e358f4d2fbf049f9b1"
SHARED = f"""%YAML 1.1
--- !u!114 &11400000
MonoBehaviour:
  m_Name: SharedLevelSettings
  MapEnvType: 2
  Title:
  Description:
  PublishedItemId:
  DisplayVersion: 0.1
  FarClipPlane: 1000
  Scene_PC: {{fileID: 0}}
  Scene_Quest: {{fileID: 102900000, guid: {SCENE_GUID}, type: 3}}
"""


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("workshop_kit", ROOT / "tools" / "unity" / "workshop_kit.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolve the module through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


kit_tool = _load()


def _png(width: int, height: int) -> bytes:
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    chunk = b"IHDR" + header
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", len(header)) + chunk + struct.pack(">I", zlib.crc32(chunk))


def _write(path: Path, text: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8")


def _setup(tmp_path: Path, *, profile: str = "final", description: str = "Calle Mayor at dusk. Map data © "
           "OpenStreetMap contributors (ODbL).", thumb: tuple[int, int] = (512, 256), title: str = "Calle Mayor",
           pkg: str = "pkg_03", package_id: str = "calle_mayor_aaaa") -> tuple[Path, Path]:
    """A map with a built package, and a Unity project holding its scene and another map's."""
    project = tmp_path / "project"
    scene = f"Assets/OnwardAdapter/{package_id}/{package_id}.unity"
    _write(project / scene, "scene")
    _write(project / f"Assets/OnwardAdapter/{package_id}/SharedLevelSettings.asset", SHARED)
    _write(project / f"Assets/OnwardAdapter/{package_id}.meta", "meta")
    _write(project / "Assets/OnwardAdapter/other_map_bbbb/other_map_bbbb.unity", "other")
    _write(project / "Assets/OnwardAdapter/other_map_bbbb.meta", "meta")
    _write(project / "Assets/OnwardCustomContent/sdk.cs", "sdk")
    _write(project / "Packages/manifest.json", "{}")
    _write(project / "ProjectSettings/ProjectVersion.txt", "2021.3.23f1")
    _write(project / "Library/cache.bin", "cache")

    mapdir = tmp_path / "maps" / "calle_mayor"
    _write(mapdir / "config/export.json", json.dumps({"map": {"title": title, "description": description}}))
    package = mapdir / "data/processed/export" / pkg
    manifest = {"package_id": package_id, "map": {"id": "calle_mayor", "title": title, "description": "long"},
                "attribution": [{"text": "© OpenStreetMap contributors", "license": "ODbL-1.0"},
                                {"text": "© OpenStreetMap contributors", "license": "ODbL-1.0"},
                                {"text": "IGN", "license": "CC-BY-4.0"}],
                "smoke": {"texture": {"path": "assets/asset_store_particle_pack/smoke.png"}}}
    _write(package / "onward_export.json", json.dumps(manifest))
    _write(package / "layout.json", json.dumps({"modes": {"free_roam": 4, "uplink": 1, "hunt": 10}}))
    _write(package / "scene_visual.glb", b"glTF...asset_store_wood_furnitures_2...asset_store_wood_furnitures_2")
    _write(package / "thumbnail.png", _png(*thumb))
    sha = hashlib.sha256((package / "onward_export.json").read_bytes()).hexdigest()
    report = {"manifest_sha256": sha, "scene": scene, "lightmap_bake": {"profile": profile}, "triangles": 123456,
              "lightmaps": 7, "options": {"lightmap_texels_per_m": 20.0}}
    _write(package / "build/scene_report.json", json.dumps(report))
    item = {"WorkshopItem": {"EnvironmentType": 2, "MapSize": 1, "TimeOfDay": 2, "GameModes": [4, 10, 1]}}
    _write(package / "build/launcher/unity-export.local.info3", "\ufeff" + json.dumps(item))
    return package, project


def test_a_kit_holds_the_reduced_project_the_sheet_and_the_cover(tmp_path: Path) -> None:
    package, project = _setup(tmp_path)
    out = tmp_path / "out"
    kit, folder = kit_tool.write_kit(package, project, out, date(2026, 11, 19))
    assert folder == out / "calle_mayor"
    assert sorted(p.name for p in (folder / "OnwardProject").iterdir()) == ["Assets", "Packages", "ProjectSettings"]
    adapter = folder / "OnwardProject/Assets/OnwardAdapter"
    assert sorted(p.name for p in adapter.iterdir()) == ["calle_mayor_aaaa", "calle_mayor_aaaa.meta"]
    assert (folder / "OnwardProject/Assets/OnwardCustomContent/sdk.cs").is_file()
    assert (folder / "workshop/cover_512x256.png").read_bytes() == (package / "thumbnail.png").read_bytes()
    published = json.loads((folder / "workshop/publish.json").read_text(encoding="utf-8"))
    assert published["title"] == "Calle Mayor"
    assert published["description"].startswith("Calle Mayor at dusk.")
    assert (published["map_environment"], published["map_size"], published["time_of_day"]) == ("Urban", "Medium",
                                                                                                 "Twilight")
    assert published["game_modes"] == ["Free Roam", "Hunt", "Uplink"]
    assert published["display_version"] == "1.0"
    assert published["package"] == {"id": "calle_mayor_aaaa", "map": "calle_mayor", "pkg": "pkg_03", "bake": "final",
                                    "lightmap_texels_per_m": 20.0, "lightmaps": 7, "unity_triangles": 123456}
    sheet = (folder / "workshop/PUBLICAR.md").read_text(encoding="utf-8")
    assert sheet.count("© OpenStreetMap contributors (ODbL-1.0)") == 1
    assert "- IGN (CC-BY-4.0)" in sheet
    assert "- `asset_store_particle_pack`\n- `asset_store_wood_furnitures_2`\n" in sheet
    assert "123 456 triángulos" in sheet
    assert "Avisos" not in sheet
    readme = (folder / "LEEME.txt").read_text(encoding="utf-8")
    assert readme.startswith("CALLE MAYOR · paquete pkg_03 (calle_mayor_aaaa) · 19-11-2026")
    assert "C:\\Onward\\CalleMayor" in readme
    assert kit.warnings == []
    with zipfile.ZipFile(out / "calle_mayor.zip") as archive:
        names = archive.namelist()
    assert "calle_mayor/workshop/PUBLICAR.md" in names
    assert not any("Library" in name or "other_map" in name for name in names)
    assert not (out / ".staging-calle_mayor").exists()


def test_the_scene_settings_carry_the_workshop_fields(tmp_path: Path) -> None:
    package, project = _setup(tmp_path)
    _kit, folder = kit_tool.write_kit(package, project, tmp_path / "out", date(2026, 11, 19))
    text = (folder / "OnwardProject/Assets/OnwardAdapter/calle_mayor_aaaa/SharedLevelSettings.asset").read_text()
    assert '  Title: "Calle Mayor"\n' in text
    assert '  Description: "Calle Mayor at dusk. Map data \\u00a9 OpenStreetMap contributors (ODbL)."\n' in text
    assert "  DisplayVersion: 1.0\n" in text
    assert f"  Scene_PC: {{fileID: 102900000, guid: {SCENE_GUID}, type: 3}}\n" in text
    assert "\n  PublishedItemId:" in text
    assert (project / "Assets/OnwardAdapter/calle_mayor_aaaa/SharedLevelSettings.asset").read_text() == SHARED


def test_a_newer_kit_replaces_the_map_s_older_one_and_keeps_the_others(tmp_path: Path) -> None:
    out = tmp_path / "out"
    old = out / "2026-11-16_calle_mayor_pkg01"
    _write(old / "workshop/publish.json", json.dumps({"package": {"map": "calle_mayor"}}))
    _write(out / "2026-11-16_calle_mayor_pkg01.zip", "zip")
    other = out / "2026-11-16_other_pkg02"
    _write(other / "workshop/publish.json", json.dumps({"package": {"map": "other"}}))
    _write(out / "notes", "not a kit")
    _write(out / "LEEME.txt", "private")
    package, project = _setup(tmp_path)
    kit_tool.write_kit(package, project, out, date(2026, 11, 19))
    assert sorted(p.name for p in out.iterdir()) == ["2026-11-16_other_pkg02", "LEEME.txt", "calle_mayor",
                                                     "calle_mayor.zip", "notes"]
    kit_tool.write_kit(package, project, out, date(2026, 11, 20))
    assert (out / "calle_mayor/LEEME.txt").read_text(encoding="utf-8").startswith("CALLE MAYOR · paquete pkg_03 "
                                                                                  "(calle_mayor_aaaa) · 20-11-2026")


@pytest.mark.parametrize(("change", "message"), [
    ({"profile": "draft"}, "light bake is 'draft'"),
    ({"thumb": (512, 512)}, "thumbnail is not 512x256"),
    ({"description": "x" * 501}, "description has 501 characters"),
    ({"description": " "}, "description has 0 characters"),
    ({"title": "T" * 25}, "title has 25 characters"),
])
def test_a_package_not_ready_for_the_workshop_is_refused(tmp_path: Path, change: dict[str, object],
                                                         message: str) -> None:
    package, project = _setup(tmp_path, **change)  # type: ignore[arg-type]
    with pytest.raises(kit_tool.KitError, match=message):
        kit_tool.read_kit(package, project)


def test_a_build_of_another_manifest_or_a_missing_scene_is_refused(tmp_path: Path) -> None:
    package, project = _setup(tmp_path)
    (project / "Assets/OnwardAdapter/calle_mayor_aaaa/calle_mayor_aaaa.unity").unlink()
    with pytest.raises(kit_tool.KitError, match="build the package in Unity again"):
        kit_tool.read_kit(package, project)
    manifest = package / "onward_export.json"
    manifest.write_text(manifest.read_text() + " ", encoding="utf-8")
    with pytest.raises(kit_tool.KitError, match="not of this package"):
        kit_tool.read_kit(package, project)
    (package / "build/scene_report.json").unlink()
    with pytest.raises(kit_tool.KitError, match="build the package in Unity first"):
        kit_tool.read_kit(package, project)


def test_long_texts_and_a_missing_osm_credit_are_warnings(tmp_path: Path) -> None:
    package, project = _setup(tmp_path, title="Barcelona Sarria Night", description="y" * 320)
    kit = kit_tool.read_kit(package, project)
    assert kit.warnings == ["the title has 22 characters; the guide suggests 6 to 18",
                            "the description has 320 characters; the guide suggests about 250",
                            "the description does not credit OpenStreetMap"]


def test_main_reports_each_kit_and_fails_on_a_bad_package(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    package, project = _setup(tmp_path)
    out = tmp_path / "out"
    assert kit_tool.main([str(package), str(tmp_path / "nowhere"), "--project", str(project), "--out", str(out)]) == 1
    captured = capsys.readouterr()
    assert f"calle_mayor  pkg_03  → {out / 'calle_mayor'}  (calle_mayor.zip)" in captured.out
    assert "nowhere" in captured.err
    assert kit_tool.main([str(package), "--project", str(project), "--out", str(out)]) == 0


def test_shared_settings_without_a_quest_scene_or_a_field_are_refused() -> None:
    with pytest.raises(kit_tool.KitError, match="no Quest scene"):
        kit_tool.set_shared_settings(SHARED.replace(f"guid: {SCENE_GUID}, ", ""), "T", "D")
    with pytest.raises(kit_tool.KitError, match="no DisplayVersion"):
        kit_tool.set_shared_settings(SHARED.replace("  DisplayVersion: 0.1\n", ""), "T", "D")


def test_png_size_reads_the_header_and_refuses_another_file() -> None:
    assert kit_tool.png_size(_png(512, 256)) == (512, 256)
    with pytest.raises(kit_tool.KitError, match="not a PNG"):
        kit_tool.png_size(b"GIF89a" + bytes(30))
