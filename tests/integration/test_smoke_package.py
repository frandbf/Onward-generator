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
