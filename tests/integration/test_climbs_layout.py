"""Ladders and lifts from the command to the layout (ADR-029). No Unity."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from shapely.geometry import Point

from onward_generator.cli import main
from onward_generator.config.validation import REPO_ROOT
from onward_generator.detail import detail_scene, load_base, load_detail_config
from onward_generator.export.package import _check_layout
from onward_generator.generation import load_generation_config
from onward_generator.perf.layout import AMMO_OBJECTIVE_CLEAR_M, TEAM_SEPARATION_M, gameplay_layout, playable_layout


def test_perf_layout_reads_the_footprints_of_the_scene(detail_inputs: tuple[Path, Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    scene, heights, base = detail_inputs
    detail = tmp_path / "detail"
    assert main(["detail", "--scene", str(scene), "--heights", str(heights), "--base", str(base), "--out", str(detail), "--no-preview"]) == 0
    out = tmp_path / "layout.json"
    assert main(["perf", "layout", "--scene", str(scene), "--heights", str(heights), "--base", str(base), "--detail", str(detail), "--playable", "--out", str(out)]) == 0
    layout = json.loads(out.read_text(encoding="utf-8"))
    assert isinstance(layout["ladders"], list) and isinstance(layout["lifts"], list)
    assert _check_layout(layout) == []
    assert f" ladders={len(layout['ladders'])} lifts={len(layout['lifts'])}" in capsys.readouterr().out


def test_perf_layout_hands_the_scene_footprints_to_the_layout(detail_inputs: tuple[Path, Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scene, heights, base = detail_inputs
    detail = tmp_path / "detail"
    assert main(["detail", "--scene", str(scene), "--heights", str(heights), "--base", str(base), "--out", str(detail), "--no-preview"]) == 0
    seen: dict[str, object] = {}

    def spy(*args: object, **kwargs: object) -> dict:
        seen.update(kwargs)
        return playable_layout(*args, **kwargs)

    monkeypatch.setattr("onward_generator.perf.__main__.playable_layout", spy)
    assert main(["perf", "layout", "--scene", str(scene), "--heights", str(heights), "--base", str(base), "--detail", str(detail), "--playable", "--out", str(tmp_path / "l.json")]) == 0
    features = json.loads(Path(scene).read_text(encoding="utf-8"))["features"]
    assert set(seen["footprints"]) == {f["id"] for f in features}
    assert all(not geometry.is_empty for geometry in seen["footprints"].values())


def test_the_adapter_builds_both_after_the_navigation_bake() -> None:
    """No Unity here: this pins the wiring. Whether it compiles and plays is the headset checklist of ADR-029."""
    source = (REPO_ROOT / "tools" / "unity" / "onward_adapter" / "Editor" / "OnwardMapAdapter.cs").read_text(encoding="utf-8")
    assert source.index("BakeNavigation(root, collisionRoot, pkg.Layout);") < source.index("AddClimbs(pkg.Layout, root);")
    assert "public Climb[] ladders;" in source and "public Climb[] lifts;" in source
    assert 'InstantiatePrefab("Ladder.prefab")' in source and 'InstantiatePrefab("Elevator.prefab")' in source
    assert 'InstantiatePrefab("AreaLadderPerch.prefab")' in source
    assert "street.PairedElevator = roof;" in source and "roof.PairedElevator = street;" in source


def test_the_layout_carries_the_climbs_and_blows_wind_on_their_roofs(detail_inputs: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    scene, heights, base = detail_inputs
    generated, ref = load_base(base, scene, heights, load_generation_config())
    manifest = detail_scene(generated, ref, heights, scene, load_detail_config()).manifest()
    levels = json.loads(Path(heights).read_text(encoding="utf-8"))
    site = {"x": 1.0, "y": 2.0, "yaw": 0.0, "height_m": 9.0, "parapet_m": 0.7, "roof_x": 500.0, "roof_y": 500.0}
    seen: list[tuple] = []

    def fake_place(*args: object) -> dict:
        seen.append(args)
        return {"ladders": [site], "lifts": []}

    monkeypatch.setattr("onward_generator.perf.layout.place_climbs", fake_place)
    layout = playable_layout(generated, manifest, levels, footprints={"b": Point(0, 0)})
    assert layout["ladders"] == [site] and layout["lifts"] == []
    wind = layout["audio_roof_wind_m"]
    assert (500.0, 500.0, 10.5) in zip(wind[::3], wind[1::3], wind[2::3], strict=True)
    detail_arg, footprints, free, keep_out, attackers, defenders = seen[0]
    assert detail_arg is manifest and set(footprints) == {"b"}
    assert keep_out.contains(Point(layout["uplink_m"])) and keep_out.contains(Point(layout["ai_m"][0]["x"], layout["ai_m"][0]["y"]))
    assert free.area > 0
    assert tuple(layout["attackers"]["centre_m"]) in attackers and tuple(layout["defenders"]["centre_m"]) in defenders
    assert tuple(layout["attackers_b"]["centre_m"]) in attackers and tuple(layout["defenders_b"]["centre_m"]) in defenders



def test_the_playable_teams_spawn_farther_apart_than_the_fixture_allows(detail_inputs: tuple[Path, Path, Path]) -> None:
    scene, heights, base = detail_inputs
    generated, ref = load_base(base, scene, heights, load_generation_config())
    manifest = detail_scene(generated, ref, heights, scene, load_detail_config()).manifest()

    def gap(doc: dict) -> float:
        (ax, ay), (dx, dy) = doc["attackers"]["centre_m"], doc["defenders"]["centre_m"]
        return ((ax - dx) ** 2 + (ay - dy) ** 2) ** 0.5

    assert gap(gameplay_layout(generated, manifest)) <= TEAM_SEPARATION_M
    assert gap(playable_layout(generated, manifest, json.loads(Path(heights).read_text(encoding="utf-8")))) > TEAM_SEPARATION_M


def test_no_objective_lands_on_an_ammo_crate(detail_inputs: tuple[Path, Path, Path]) -> None:
    """On Barcelona Sarria both Escort points sat exactly on a crate, and a hard drive 0.75 m from one."""
    scene, heights, base = detail_inputs
    generated, ref = load_base(base, scene, heights, load_generation_config())
    manifest = detail_scene(generated, ref, heights, scene, load_detail_config()).manifest()
    layout = playable_layout(generated, manifest, json.loads(Path(heights).read_text(encoding="utf-8")))
    objectives = [layout["escort_a_m"], layout["escort_b_m"], layout["evac_m"], *layout["harddrives_m"]]
    points = [(o["x"], o["y"]) if isinstance(o, dict) else (o[0], o[1]) for o in objectives]
    crates = [(a["x"], a["y"]) for a in layout["ammo_m"]]
    assert min(math.dist(o, c) for o in points for c in crates) >= AMMO_OBJECTIVE_CLEAR_M
