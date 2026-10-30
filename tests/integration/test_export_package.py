"""Phase 07.0: Onward export package on the synthetic street block around MURCIA_CENTER.

The Unity half of the adapter needs Unity and the SDK; it is exercised by
tools/unity/onward_adapter/run_export.sh, not here. These tests cover the package
contract the C# side relies on.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

import pytest

from onward_generator.cli import main
from onward_generator.config.loader import read_json_document
from onward_generator.config.models import GeneratorConfig
from onward_generator.export.package import (
    EXPORT_CONFIG_DEFAULT,
    MANIFEST_FILE,
    SDK_SPAWN_POINTS,
    ExportError,
    build_package,
    sha256_file,
    verify_package,
)
from tests.conftest import FIXED_NOW, MURCIA_CENTER

REPO = Path(__file__).resolve().parents[2]
EXPORT_PROJECT = REPO / "config" / "murcia-belluga.export.json"
EXAMPLE_PROJECT = REPO / "config" / "murcia-belluga.example.json"
ADAPTER_CS = REPO / "tools" / "unity" / "onward_adapter" / "Editor" / "OnwardMapAdapter.cs"


@pytest.fixture(scope="module")
def inputs(detail_inputs: tuple[Path, Path, Path], tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    scene, heights, base = detail_inputs
    root = tmp_path_factory.mktemp("export")
    detail = root / "detail"
    assert main(["detail", "--scene", str(scene), "--heights", str(heights), "--base", str(base),
                 "--out", str(detail), "--no-preview"]) == 0
    layout = root / "layout.json"
    assert main(["perf", "layout", "--scene", str(scene), "--heights", str(heights), "--base", str(base),
                 "--detail", str(detail), "--out", str(layout)]) == 0
    return detail, layout


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def package(inputs: tuple[Path, Path], out: Path, **kw: Any):  # type: ignore[no-untyped-def]
    detail, layout = inputs
    return build_package(kw.pop("project", EXPORT_PROJECT), kw.pop("detail", detail), kw.pop("layout", layout), out,
                         now=FIXED_NOW, **kw)


def test_export_project_is_centred_on_murcia() -> None:
    doc, path = read_json_document(EXPORT_PROJECT)
    assert GeneratorConfig.from_document(doc, path).area.center == MURCIA_CENTER


def test_package_is_complete_and_verifiable(inputs: tuple[Path, Path], tmp_path: Path) -> None:
    result = package(inputs, tmp_path / "pkg")
    out, m = result.directory, result.manifest
    assert sorted(p.name for p in out.iterdir()) == sorted(
        ["onward_export.json", "adapter_options.json", "quest_acceptance.md", "scene_visual.glb", "scene_collision.glb",
         "layout.json", "thumbnail.png"])
    assert verify_package(out) == []
    assert load(out / MANIFEST_FILE) == m
    assert m["adapter"] == {"name": "onward_unity", "version": "0.1.0", "device_verified": False}
    assert m["target"] == {"unity": "2021.3.23f1", "sdk": "1.15", "platform": "quest"}
    assert re.fullmatch(r"murcia_belluga_[0-9a-f]{10}", m["package_id"])
    assert load(out / "adapter_options.json")["variant"] == m["package_id"]
    detail, layout = inputs
    by_role = {f["role"]: f for f in m["files"]}
    assert by_role["visual"]["sha256"] == sha256_file(detail / "scene_visual.glb")
    assert by_role["layout"]["sha256"] == sha256_file(layout)
    attribution = load(detail / "scene_detail.json")["attribution"]
    assert m["attribution"] == attribution and attribution
    for a in attribution:
        assert f"{a['text']} ({a['license']})" in m["map"]["description"]
    assert any("export_adapter_unverified" in w for w in m["warnings"])
    checklist = (out / "quest_acceptance.md").read_text(encoding="utf-8")
    assert "Uplink" in checklist and "Free Roam" in checklist and m["package_id"] in checklist
    assert "docs/12-export.md" in checklist and "este `quest_acceptance.md` esté completo" in checklist


def test_package_id_depends_only_on_content(inputs: tuple[Path, Path], tmp_path: Path) -> None:
    a = package(inputs, tmp_path / "a").manifest["package_id"]
    b = package(inputs, tmp_path / "b").manifest["package_id"]
    assert a == b
    cfg = load(EXPORT_CONFIG_DEFAULT)
    cfg["build"]["combine_cell_m"] = 50.0
    (tmp_path / "cfg.json").write_text(json.dumps(cfg), encoding="utf-8")
    assert package(inputs, tmp_path / "c", export_config=tmp_path / "cfg.json").manifest["package_id"] != a


def test_existing_output_is_never_overwritten(inputs: tuple[Path, Path], tmp_path: Path) -> None:
    out = tmp_path / "pkg"
    package(inputs, out)
    before = sha256_file(out / MANIFEST_FILE)
    with pytest.raises(FileExistsError):
        package(inputs, out)
    assert sha256_file(out / MANIFEST_FILE) == before


def test_export_disabled_project_is_refused(inputs: tuple[Path, Path], tmp_path: Path) -> None:
    with pytest.raises(ExportError, match="/export must be"):
        package(inputs, tmp_path / "pkg", project=EXAMPLE_PROJECT)
    assert not (tmp_path / "pkg").exists()


def test_failed_phase04_checks_are_refused(inputs: tuple[Path, Path], tmp_path: Path) -> None:
    detail = tmp_path / "detail"
    shutil.copytree(inputs[0], detail)
    report = load(detail / "detail_report.json")
    report["phase_04_checks"]["automatic_checks_passed"] = False
    (detail / "detail_report.json").write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ExportError, match="automatic checks did not pass"):
        package(inputs, tmp_path / "pkg", detail=detail)
    assert not (tmp_path / "pkg").exists()


def test_missing_attribution_is_refused(inputs: tuple[Path, Path], tmp_path: Path) -> None:
    detail = tmp_path / "detail"
    shutil.copytree(inputs[0], detail)
    manifest = load(detail / "scene_detail.json")
    manifest["attribution"] = []
    (detail / "scene_detail.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ExportError, match="attribution"):
        package(inputs, tmp_path / "pkg", detail=detail)


@pytest.mark.parametrize(("mutate", "match"), [
    (lambda d: d.update(schema="other"), "schema must be"),
    (lambda d: d["attackers"].update(points_m=d["attackers"]["points_m"][:SDK_SPAWN_POINTS - 1]), "attackers has 7 spawn points"),
    (lambda d: d.pop("uplink_m"), "uplink_m"),
    (lambda d: d.update(level_size_m=0), "level_size_m"),
    (lambda d: d["damage_volume"].pop("size_m"), "damage_volume"),
])
def test_invalid_layout_is_refused(inputs: tuple[Path, Path], tmp_path: Path, mutate: Any, match: str) -> None:
    doc = load(inputs[1])
    mutate(doc)
    layout = tmp_path / "layout.json"
    layout.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ExportError, match=match):
        package(inputs, tmp_path / "pkg", layout=layout)
    assert not (tmp_path / "pkg").exists()


@pytest.mark.parametrize(("mutate", "match"), [
    (lambda c: c["map"].update(id="murcia belluga"), "/map/id"),
    (lambda c: c["adapter"].update(version="9.9.9"), "/adapter/version"),
    (lambda c: c["target"].update(sdk="1.16"), "/target/sdk"),
    (lambda c: c["build"].update(lightmap_max_size=3000), "/build/lightmap_max_size"),
])
def test_invalid_export_config_is_refused(inputs: tuple[Path, Path], tmp_path: Path, mutate: Any, match: str) -> None:
    cfg = load(EXPORT_CONFIG_DEFAULT)
    mutate(cfg)
    (tmp_path / "cfg.json").write_text(json.dumps(cfg), encoding="utf-8")
    with pytest.raises(ExportError, match=match):
        package(inputs, tmp_path / "pkg", export_config=tmp_path / "cfg.json")


def test_verify_detects_tampering(inputs: tuple[Path, Path], tmp_path: Path) -> None:
    out = package(inputs, tmp_path / "pkg").directory
    with (out / "layout.json").open("ab") as f:
        f.write(b" ")
    assert verify_package(out) == ["layout.json: sha256 differs from the manifest"]
    manifest = load(out / MANIFEST_FILE)
    manifest["files"][0]["path"] = "../escape.glb"
    (out / MANIFEST_FILE).write_text(json.dumps(manifest), encoding="utf-8")
    assert "../escape.glb: package paths must be plain file names" in verify_package(out)


def test_cli_package_and_verify(inputs: tuple[Path, Path], tmp_path: Path) -> None:
    detail, layout = inputs
    args = ["export", "package", "--project", str(EXPORT_PROJECT), "--detail", str(detail), "--layout", str(layout),
            "--out", str(tmp_path / "pkg")]
    assert main(args) == 0
    assert main(args) == 1
    assert main(["export", "verify", str(tmp_path / "pkg")]) == 0
    assert main(["export", "package", "--project", str(EXAMPLE_PROJECT), "--detail", str(detail), "--layout", str(layout),
                 "--out", str(tmp_path / "other")]) == 1
    assert not (tmp_path / "other").exists()
    assert main(["export", "bogus"]) == 2


def test_playable_export_refuses_a_scene_without_the_perimeter_wall(tmp_path: Path) -> None:
    layout = tmp_path / "layout.json"
    layout.write_text(json.dumps({"schema": "onward_generator.gameplay_layout"}), encoding="utf-8")
    detail = tmp_path / "detail"
    detail.mkdir()
    with pytest.raises(ExportError, match="perimeter_wall"):
        build_package(EXPORT_PROJECT, detail, layout, tmp_path / "pkg")


def test_default_listing_title_is_apocalipsis_murciana() -> None:
    cfg = load(EXPORT_CONFIG_DEFAULT)
    assert cfg["map"]["title"] == "Apocalipsis Murciana"
    thumb = REPO / "tools" / "unity" / "onward_adapter" / "Editor" / "Thumbnails" / "apocalipsis_murciana.png"
    assert thumb.is_file() and thumb.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_info3_listing_is_rewritten_without_touching_the_bundle_id(tmp_path: Path) -> None:
    from onward_generator.export.info3 import Info3Error, apply_local_listing

    path = tmp_path / "unity-export.local.info3"
    path.write_text(json.dumps({
        "WorkshopItem": {
            "ID": "unity-export.local", "MapName": "Unity Export", "AvgRating": 0.0,
            "GameModes": [1, 4], "ThumbnailUrl": "", "BundleHash": "ABC123",
            "Description": "Your map that has been exported from the UGC Tool",
        },
        "IsSanitized": False,
    }), encoding="utf-8")
    doc = apply_local_listing(path, title="Apocalipsis Murciana",
                              description="Generated test. Data: © OpenStreetMap contributors (ODbL-1.0).")
    assert doc["WorkshopItem"]["MapName"] == "Unity Export"
    assert doc["WorkshopItem"]["ID"] == "unity-export.local"
    assert doc["WorkshopItem"]["BundleHash"] == "ABC123"
    assert doc["WorkshopItem"]["GameModes"] == [4, 10, 1]
    assert "OpenStreetMap" in doc["WorkshopItem"]["Description"]
    other = tmp_path / "workshop.info3"
    other.write_text(json.dumps({
        "WorkshopItem": {"ID": "workshop-123", "MapName": "Other", "BundleHash": "ABC123", "Description": "x"},
    }), encoding="utf-8")
    with pytest.raises(Info3Error, match="only the local test slot"):
        apply_local_listing(other, title="X", description="Y")


def test_unity_adapter_matches_the_package_contract() -> None:
    source = ADAPTER_CS.read_text(encoding="utf-8")
    cfg = load(EXPORT_CONFIG_DEFAULT)
    assert f'AdapterName = "{cfg["adapter"]["name"]}"' in source
    assert f'AdapterVersion = "{cfg["adapter"]["version"]}"' in source
    assert 'PackageSchema = "onward_generator.onward_export_package"' in source
    assert 'PackageSchemaVersion = "0.1.0"' in source
    for option in cfg["build"]:
        assert re.search(rf"public \w+ {option} =", source), option
    assert "apocalipsis_murciana.png" in source
    assert "f.role == \"thumbnail\"" in source
    assert "LoadListingThumbnail" in source
    assert "ContributesGi" in source
    assert "tree_" in source
