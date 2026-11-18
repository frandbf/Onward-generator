"""A package with a soundscape (plan 2026-11-17-efectos-sdk, increment D; ADR-046)."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from onward_generator.cli import main
from onward_generator.export.package import ExportError, build_package, verify_package
from tests.conftest import FIXED_NOW
from tests.integration.test_export_package import EXPORT_PROJECT, GOLDEN_OPTIONS_SHA256, GOLDEN_PACKAGE_ID
from tests.integration.test_smoke_package import _wrecked, inputs  # noqa: F401 - the module fixture is shared


def _far(layout: Path) -> list[tuple[float, float]]:
    spawns = json.loads(layout.read_bytes())
    return [(x, y) for x, y in ((-70.0, -70.0), (70.0, -70.0), (-70.0, 70.0), (70.0, 70.0))
            if all(((x - p["x"]) ** 2 + (y - p["y"]) ** 2) ** 0.5 >= 15.0
                   for key in ("attackers", "defenders") for p in (spawns.get(key) or {}).get("points_m") or [])]


@pytest.mark.parametrize("mode", ["default", "regional"])
def test_a_map_without_war_poles_or_a_regional_style_keeps_its_package(inputs: tuple[Path, Path], tmp_path: Path, mode: str) -> None:  # noqa: F811 - the shared fixture
    detail, layout = inputs
    result = build_package(EXPORT_PROJECT, detail, layout, tmp_path / "pkg", now=FIXED_NOW, soundscape=mode)
    assert result.manifest["package_id"] == GOLDEN_PACKAGE_ID
    assert hashlib.sha256((result.directory / "adapter_options.json").read_bytes()).hexdigest() == GOLDEN_OPTIONS_SHA256
    if mode == "default":
        assert "soundscape" not in result.manifest
    else:  # the fixture is Murcia: mediterranean, no row and no pole
        assert result.manifest["soundscape"] == {"mode": "regional", "style": "mediterranean", "global": None, "birds": None,
                                                 "wind": None, "sources": {"generator": 0}}


def test_war_sounds_combat_fire_under_the_smoke_and_flies(inputs: tuple[Path, Path], tmp_path: Path) -> None:  # noqa: F811 - the shared fixture
    detail, layout = inputs
    far = _far(layout)
    result = build_package(EXPORT_PROJECT, _wrecked(detail, tmp_path, far), layout, tmp_path / "pkg", now=FIXED_NOW,
                           smoke=len(far), soundscape="war", facade_style="east_african")
    block = json.loads((result.directory / "adapter_options.json").read_bytes())["soundscape"]
    assert (block["mode"], block["global"], block["birds"], block["wind"]) == ("war", "Distant Combat Sparse", "Birds African Bush 1", "Wind Desert")
    fires = sorted((s["x"], s["y"], s["z"]) for s in block["sources"] if s["clip"] == "Fire")
    assert fires == sorted((x, y, 1.5) for x, y in far)  # each column stands 1 m over its wreck, the fire 0.5 m more
    assert sorted((s["x"], s["y"]) for s in block["sources"] if s["clip"] == "Flies") == sorted(far)[:4]
    assert result.manifest["soundscape"]["sources"] == {"generator": 0, "fire": len(far), "flies": min(len(far), 4)}
    assert result.manifest["package_id"] != GOLDEN_PACKAGE_ID and verify_package(result.directory) == []


def test_a_generator_stands_by_a_pole_of_the_core_only(inputs: tuple[Path, Path], tmp_path: Path) -> None:  # noqa: F811 - the shared fixture
    detail, layout = inputs
    copy = tmp_path / "detail"
    shutil.copytree(detail, copy)
    document = json.loads((copy / "scene_detail.json").read_bytes())
    assert document.get("core") is None  # the fixture plays the whole map: give the copy a core
    half = 60.0
    document["core"] = {"half_side_m": half}
    document["objects"] = [*document["objects"], {"id": "overgrow/power_pole/1", "type": "overgrowth", "origin": "generated", "kind": "power_pole",
                                                  "role": "pole", "source_ids": [], "position_m": [half + 5.0, -half - 5.0, 0.0], "size_m": [0.3, 0.3, 8.0]}]
    document["envelope"] = {**(document.get("envelope") or {}), "fit": {"checked": 1, "problems": []}}
    (copy / "scene_detail.json").write_text(json.dumps(document), encoding="utf-8")
    result = build_package(EXPORT_PROJECT, copy, layout, tmp_path / "pkg", now=FIXED_NOW, soundscape="regional")
    assert result.manifest["soundscape"]["sources"] == {"generator": 0}  # past the core's edge


def test_the_command_takes_the_mode_and_the_style(inputs: tuple[Path, Path], tmp_path: Path) -> None:  # noqa: F811 - the shared fixture
    detail, layout = inputs
    out = tmp_path / "pkg"
    assert main(["export", "package", "--project", str(EXPORT_PROJECT), "--detail", str(detail), "--layout", str(layout),
                 "--out", str(out), "--soundscape", "battle", "--facade-style", "arid"]) == 0
    block = json.loads((out / "adapter_options.json").read_bytes())["soundscape"]
    assert block == {"mode": "battle", "global": "Distant Combat Dense", "birds": None, "wind": "Wind Desert", "sources": []}
    assert main(["export", "package", "--project", str(EXPORT_PROJECT), "--detail", str(detail), "--layout", str(layout),
                 "--out", str(tmp_path / "bad"), "--soundscape", "loud"]) == 2


def test_an_unknown_style_or_mode_stops_the_export(inputs: tuple[Path, Path], tmp_path: Path) -> None:  # noqa: F811 - the shared fixture
    detail, layout = inputs
    with pytest.raises(ExportError) as caught:
        build_package(EXPORT_PROJECT, detail, layout, tmp_path / "style", now=FIXED_NOW, soundscape="war", facade_style="martian")
    assert caught.value.problems[0].startswith("facade_style: unknown facade style 'martian'")
    with pytest.raises(ExportError, match="soundscape"):
        build_package(EXPORT_PROJECT, detail, layout, tmp_path / "mode", now=FIXED_NOW, soundscape="auto")
    assert not (tmp_path / "style").exists() and not (tmp_path / "mode").exists()
