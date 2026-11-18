"""A package with smoke columns (plan 2026-11-15-ambiente-belico, increment C)."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from onward_generator.cli import main
from onward_generator.export.package import build_package, verify_package
from onward_generator.export.smoke import COLUMN, puff_png
from tests.conftest import FIXED_NOW
from tests.integration.test_export_package import EXPORT_PROJECT, GOLDEN_OPTIONS_SHA256, GOLDEN_PACKAGE_ID


@pytest.fixture(scope="module")
def inputs(detail_inputs: tuple[Path, Path, Path], tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    scene, heights, base = detail_inputs
    root = tmp_path_factory.mktemp("smoke")
    detail = root / "detail"
    assert main(["detail", "--scene", str(scene), "--heights", str(heights), "--base", str(base), "--out", str(detail), "--no-preview"]) == 0
    layout = root / "layout.json"
    assert main(["perf", "layout", "--scene", str(scene), "--heights", str(heights), "--base", str(base), "--detail", str(detail), "--out", str(layout)]) == 0
    return detail, layout


def _wrecked(detail: Path, tmp_path: Path, positions: list[tuple[float, float]]) -> Path:
    copy = tmp_path / "detail"
    shutil.copytree(detail, copy)
    document = json.loads((copy / "scene_detail.json").read_bytes())
    document["objects"] = [*document["objects"], *({"id": f"overgrow/wreck/{i}", "type": "overgrowth", "origin": "generated", "kind": "wreck",
                                                     "source_ids": [], "position_m": [x, y, 0.0], "size_m": [4.0, 1.6, 1.55]}
                                                    for i, (x, y) in enumerate(positions))]
    # overgrow fit checked them, as bounds records (export refuses overgrowth without it).
    document["envelope"] = {**(document.get("envelope") or {}), "fit": {"checked": len(positions), "problems": []}}
    (copy / "scene_detail.json").write_text(json.dumps(document), encoding="utf-8")
    return copy


def test_no_smoke_keeps_the_package_byte_for_byte(inputs: tuple[Path, Path], tmp_path: Path) -> None:
    detail, layout = inputs
    result = build_package(EXPORT_PROJECT, detail, layout, tmp_path / "pkg", now=FIXED_NOW, smoke=0)
    assert result.manifest["package_id"] == GOLDEN_PACKAGE_ID
    assert hashlib.sha256((result.directory / "adapter_options.json").read_bytes()).hexdigest() == GOLDEN_OPTIONS_SHA256
    assert "smoke" not in result.manifest and not (result.directory / "smoke_puff.png").exists()
    assert "fire" not in result.manifest and not (result.directory / "fire_flame.png").exists()  # no fire unless asked


def test_smoke_columns_stand_on_the_wrecks_with_their_texture(inputs: tuple[Path, Path], tmp_path: Path) -> None:
    detail, layout = inputs
    spawns = json.loads(layout.read_bytes())
    far = [(x, y) for x, y in ((-70.0, -70.0), (70.0, -70.0), (-70.0, 70.0), (70.0, 70.0))
           if all(((x - p["x"]) ** 2 + (y - p["y"]) ** 2) ** 0.5 >= 15.0
                  for key in ("attackers", "defenders") for p in (spawns.get(key) or {}).get("points_m") or [])]
    assert len(far) >= 2  # the fixture's spawns leave at least two corners free
    result = build_package(EXPORT_PROJECT, _wrecked(detail, tmp_path, far), layout, tmp_path / "pkg", now=FIXED_NOW, smoke=len(far))
    options = json.loads((result.directory / "adapter_options.json").read_bytes())
    block = options["smoke"]
    assert {k: block[k] for k in COLUMN} == COLUMN and block["texture"] == "smoke_puff.png"
    assert sorted((c["x"], c["y"]) for c in block["columns"]) == sorted(far)
    assert result.manifest["smoke"] == {"wanted": len(far), "columns": len(far), "origin": "generated",
                                        "refused": {"outside_core": 0, "near_spawn": 0, "near_climb": 0, "near_column": 0}}
    listed = [f for f in result.manifest["files"] if f["role"] == "smoke_texture"]
    assert [f["path"] for f in listed] == ["smoke_puff.png"] and listed[0]["source"] == "generated"
    assert listed[0]["bytes"] == (result.directory / "smoke_puff.png").stat().st_size
    assert not [w for w in result.manifest["warnings"] if w.startswith("smoke:")]  # every column asked for stands
    assert (result.directory / "smoke_puff.png").read_bytes() == puff_png()
    assert result.manifest["package_id"] != GOLDEN_PACKAGE_ID
    assert verify_package(result.directory) == []


def test_smoke_without_a_wreck_warns_and_writes_nothing(inputs: tuple[Path, Path], tmp_path: Path) -> None:
    detail, layout = inputs
    result = build_package(EXPORT_PROJECT, detail, layout, tmp_path / "pkg", now=FIXED_NOW, smoke=3)
    options = json.loads((result.directory / "adapter_options.json").read_bytes())
    assert "smoke" not in options and not (result.directory / "smoke_puff.png").exists()
    assert result.manifest["smoke"]["columns"] == 0
    assert "smoke: 0 of 3 columns found a wreck of the core clear of the spawns, the climbs and each other (no wreck)" in result.manifest["warnings"]


def test_the_export_command_takes_the_count(inputs: tuple[Path, Path], tmp_path: Path) -> None:
    detail, layout = inputs
    args = ["export", "package", "--project", str(EXPORT_PROJECT), "--detail", str(detail), "--layout", str(layout)]
    assert main([*args, "--out", str(tmp_path / "three"), "--smoke", "3"]) == 0
    assert json.loads((tmp_path / "three" / "onward_export.json").read_bytes())["smoke"]["wanted"] == 3
    assert main([*args, "--out", str(tmp_path / "six"), "--smoke", "6"]) == 2


def test_the_default_is_no_smoke(inputs: tuple[Path, Path], tmp_path: Path) -> None:
    detail, layout = inputs
    manifest = build_package(EXPORT_PROJECT, detail, layout, tmp_path / "pkg", now=FIXED_NOW).manifest
    assert manifest["package_id"] == GOLDEN_PACKAGE_ID and "smoke" not in manifest


def test_wrecks_outside_the_core_or_too_close_are_refused_and_named(inputs: tuple[Path, Path], tmp_path: Path) -> None:
    detail, layout = inputs
    spawns = json.loads(layout.read_bytes())
    points = [(p["x"], p["y"]) for key in ("attackers", "defenders") for p in (spawns.get(key) or {}).get("points_m") or []]
    x0, y0 = next((x, y) for x, y in ((-70.0, -70.0), (70.0, -70.0), (-70.0, 70.0), (70.0, 70.0))
                  if all(((x - px) ** 2 + (y - py) ** 2) ** 0.5 >= 20.0 for px, py in points))
    copy = _wrecked(detail, tmp_path, [(150.0, 0.0), (x0, y0), (x0 * 0.95, y0)])  # the third 3.5 m from the second
    document = json.loads((copy / "scene_detail.json").read_bytes())
    document["core"] = {**(document.get("core") or {}), "half_side_m": 100.0}
    (copy / "scene_detail.json").write_text(json.dumps(document), encoding="utf-8")
    result = build_package(EXPORT_PROJECT, copy, layout, tmp_path / "pkg", now=FIXED_NOW, smoke=3)
    assert result.manifest["smoke"]["refused"] == {"outside_core": 1, "near_spawn": 0, "near_climb": 0, "near_column": 1}
    assert result.manifest["smoke"]["columns"] == 1
    assert ("smoke: 1 of 3 columns found a wreck of the core clear of the spawns, the climbs and each other "
            "(outside_core 1, near_column 1)") in result.manifest["warnings"]


def test_fewer_columns_than_wrecks_take_the_first_in_turn(inputs: tuple[Path, Path], tmp_path: Path) -> None:
    detail, layout = inputs
    spawns = json.loads(layout.read_bytes())
    points = [(p["x"], p["y"]) for key in ("attackers", "defenders") for p in (spawns.get(key) or {}).get("points_m") or []]
    far = [(x, y) for x, y in ((-70.0, -70.0), (70.0, -70.0), (-70.0, 70.0), (70.0, 70.0))
           if all(((x - px) ** 2 + (y - py) ** 2) ** 0.5 >= 15.0 for px, py in points)]
    result = build_package(EXPORT_PROJECT, _wrecked(detail, tmp_path, far), layout, tmp_path / "pkg", now=FIXED_NOW, smoke=1)
    assert result.manifest["smoke"]["columns"] == 1 and len(json.loads((result.directory / "adapter_options.json").read_bytes())["smoke"]["columns"]) == 1


def test_fire_burns_under_each_column_with_its_texture(inputs: tuple[Path, Path], tmp_path: Path) -> None:
    from onward_generator.export.fire import EMBERS, FLAMES, flame_png

    detail, layout = inputs
    spawns = json.loads(layout.read_bytes())
    far = [(x, y) for x, y in ((-70.0, -70.0), (70.0, -70.0), (-70.0, 70.0), (70.0, 70.0))
           if all(((x - p["x"]) ** 2 + (y - p["y"]) ** 2) ** 0.5 >= 15.0
                  for key in ("attackers", "defenders") for p in (spawns.get(key) or {}).get("points_m") or [])]
    result = build_package(EXPORT_PROJECT, _wrecked(detail, tmp_path, far), layout, tmp_path / "pkg", now=FIXED_NOW, smoke=len(far), fire=True)
    block = json.loads((result.directory / "adapter_options.json").read_bytes())["fire"]
    assert block["texture"] == "fire_flame.png" and block["flames"] == FLAMES and block["embers"] == EMBERS
    assert sorted((s["x"], s["y"], s["z"]) for s in block["sites"]) == sorted((x, y, 1.6) for x, y in far)
    assert result.manifest["fire"] == {"sites": len(far), "origin": "generated"}
    listed = [f for f in result.manifest["files"] if f["role"] == "fire_texture"]
    assert [f["path"] for f in listed] == ["fire_flame.png"] and listed[0]["source"] == "generated"
    assert listed[0]["bytes"] == (result.directory / "fire_flame.png").stat().st_size
    assert (result.directory / "fire_flame.png").read_bytes() == flame_png()
    assert verify_package(result.directory) == []


def test_fire_without_smoke_warns_and_writes_nothing(inputs: tuple[Path, Path], tmp_path: Path) -> None:
    detail, layout = inputs
    result = build_package(EXPORT_PROJECT, detail, layout, tmp_path / "pkg", now=FIXED_NOW, fire=True)
    assert result.manifest["package_id"] == GOLDEN_PACKAGE_ID and not (result.directory / "fire_flame.png").exists()
    assert result.manifest["fire"] == {"sites": 0, "origin": "generated"}
    assert 'fire: no smoke column to burn under; set map.json "smoke" to light the fire' in result.manifest["warnings"]
    assert main(["export", "package", "--project", str(EXPORT_PROJECT), "--detail", str(detail), "--layout", str(layout),
                 "--out", str(tmp_path / "cli"), "--fire"]) == 0
    assert "fire" in json.loads((tmp_path / "cli" / "onward_export.json").read_bytes())


def _far(layout: Path) -> list[tuple[float, float]]:
    spawns = json.loads(layout.read_bytes())
    return [(x, y) for x, y in ((-70.0, -70.0), (70.0, -70.0), (-70.0, 70.0), (70.0, 70.0))
            if all(((x - p["x"]) ** 2 + (y - p["y"]) ** 2) ** 0.5 >= 15.0
                   for key in ("attackers", "defenders") for p in (spawns.get(key) or {}).get("points_m") or [])]


def _pack(tmp_path: Path) -> dict[str, dict[str, object]]:
    """Rows as ``local_particles`` returns them, with their files under ``tmp_path``."""
    rows: dict[str, dict[str, object]] = {}
    for role, tiles, data in (("smoke", [5, 5], b"smoke-sheet"), ("flame", [10, 10], b"flame-sheet")):
        file = tmp_path / "assets" / "asset_store_pack" / f"particle_{role}.png"
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(data)
        rows[role] = {"role": role, "path": f"assets/asset_store_pack/particle_{role}.png", "file": file,
                      "sha256": hashlib.sha256(data).hexdigest(), "tiles": tiles, "license": "LicenseRef-Unity-Asset-Store-EULA",
                      "credit": f"{role} from Pack by Pub, Unity Asset Store EULA; local copy, never committed.", "frames": 1}
    return rows


def test_a_local_pack_s_flipbooks_draw_the_smoke_and_the_flames(inputs: tuple[Path, Path], tmp_path: Path,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    from onward_generator.export.fire import EMBERS, SHEET_FLAMES, flame_png

    detail, layout = inputs
    far = _far(layout)
    pack = _pack(tmp_path)
    asked: list[Path] = []
    monkeypatch.setattr("onward_generator.export.package.local_particles", lambda assets: asked.append(assets) or pack)
    wrecked = _wrecked(detail, tmp_path, far)
    result = build_package(EXPORT_PROJECT, wrecked, layout, tmp_path / "pkg", now=FIXED_NOW, smoke=len(far), fire=True)
    assert asked == [Path(__file__).resolve().parents[2] / "assets"]
    options = json.loads((result.directory / "adapter_options.json").read_bytes())
    assert options["smoke"]["sheet"] == {"tiles": [5, 5]} and options["smoke"]["texture"] == "smoke_puff.png"
    assert options["fire"]["sheet"] == {"texture": "fire_flame_sheet.png", "tiles": [10, 10], "pivot_y": 0.5}
    assert options["fire"]["flames"] == SHEET_FLAMES and options["fire"]["embers"] == EMBERS
    assert (result.directory / "smoke_puff.png").read_bytes() == b"smoke-sheet"
    assert (result.directory / "fire_flame_sheet.png").read_bytes() == b"flame-sheet"
    assert (result.directory / "fire_flame.png").read_bytes() == flame_png()  # the embers keep the sprite
    files = {f["role"]: f for f in result.manifest["files"]}
    assert files["smoke_texture"]["source"] == "assets/asset_store_pack/particle_smoke.png"
    assert files["fire_sheet_texture"]["source"] == "assets/asset_store_pack/particle_flame.png"
    assert files["fire_sheet_texture"]["path"] == "fire_flame_sheet.png" and files["fire_sheet_texture"]["bytes"] == len(b"flame-sheet")
    assert files["fire_texture"]["source"] == "generated"
    assert result.manifest["smoke"]["texture"] == {
        "origin": "asset_store", "role": "smoke", "path": "assets/asset_store_pack/particle_smoke.png",
        "sha256": hashlib.sha256(b"smoke-sheet").hexdigest(), "tiles": [5, 5], "license": "LicenseRef-Unity-Asset-Store-EULA",
        "credit": "smoke from Pack by Pub, Unity Asset Store EULA; local copy, never committed."}
    assert result.manifest["fire"]["texture"]["role"] == "flame" and result.manifest["fire"]["texture"]["tiles"] == [10, 10]
    assert result.manifest["fire"]["origin"] == "generated" and result.manifest["fire"]["sites"] == len(far)
    assert verify_package(result.directory) == []
    plain = build_package(EXPORT_PROJECT, wrecked, layout, tmp_path / "plain", now=FIXED_NOW, smoke=len(far), fire=True,
                          pack_particles=False)
    assert len(asked) == 1  # not even looked for
    assert plain.manifest["package_id"] != result.manifest["package_id"]
    assert (plain.directory / "smoke_puff.png").read_bytes() == puff_png() and not (plain.directory / "fire_flame_sheet.png").exists()
    assert "texture" not in plain.manifest["smoke"] and "texture" not in plain.manifest["fire"]


def test_a_pack_with_only_a_flame_keeps_the_drawn_smoke(inputs: tuple[Path, Path], tmp_path: Path,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    detail, layout = inputs
    far = _far(layout)
    pack = {"flame": _pack(tmp_path)["flame"]}
    monkeypatch.setattr("onward_generator.export.package.local_particles", lambda assets: pack)
    result = build_package(EXPORT_PROJECT, _wrecked(detail, tmp_path, far), layout, tmp_path / "pkg", now=FIXED_NOW, smoke=len(far))
    options = json.loads((result.directory / "adapter_options.json").read_bytes())
    assert "sheet" not in options["smoke"] and "fire" not in options and "texture" not in result.manifest["smoke"]
    assert (result.directory / "smoke_puff.png").read_bytes() == puff_png() and not (result.directory / "fire_flame_sheet.png").exists()
    assert {f["role"]: f["source"] for f in result.manifest["files"]}["smoke_texture"] == "generated"


def test_no_column_looks_for_no_pack(inputs: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    detail, layout = inputs
    monkeypatch.setattr("onward_generator.export.package.local_particles", lambda assets: pytest.fail("looked for a pack"))
    result = build_package(EXPORT_PROJECT, detail, layout, tmp_path / "pkg", now=FIXED_NOW, smoke=2, fire=True)
    assert result.manifest["smoke"]["columns"] == 0 and "texture" not in result.manifest["smoke"]


def test_the_export_command_passes_no_pack_particles(inputs: tuple[Path, Path], tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    detail, layout = inputs
    seen: list[bool] = []
    real = build_package

    def spy(*args: object, **kwargs: object) -> object:
        seen.append(bool(kwargs["pack_particles"]))
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("onward_generator.export.__main__.build_package", spy)
    base = ["export", "package", "--project", str(EXPORT_PROJECT), "--detail", str(detail), "--layout", str(layout)]
    assert main([*base, "--out", str(tmp_path / "a")]) == 0
    assert main([*base, "--out", str(tmp_path / "b"), "--no-pack-particles"]) == 0
    assert seen == [True, False]
