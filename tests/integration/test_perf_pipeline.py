"""Phase 05 offline steps on the synthetic street block around MURCIA_CENTER (phase 04 output → perf)."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from shapely import Point

from onward_generator.cli import main
from onward_generator.detail import load_base
from onward_generator.generation import load_generation_config
from onward_generator.perf.layout import CLEARANCE_M, SPAWN_POINTS, LayoutError, free_ground, load_detail_manifest

GEN = load_generation_config()


@pytest.fixture(scope="module")
def detail_dir(detail_inputs: tuple[Path, Path, Path], tmp_path_factory: pytest.TempPathFactory) -> Path:
    scene, heights, base = detail_inputs
    out = tmp_path_factory.mktemp("perf") / "detail"
    assert main(["detail", "--scene", str(scene), "--heights", str(heights), "--base", str(base),
                 "--out", str(out), "--no-preview"]) == 0
    return out


def test_layout_places_both_teams_on_free_ground(detail_inputs: tuple[Path, Path, Path], detail_dir: Path,
                                                 tmp_path: Path) -> None:
    scene, heights, base = detail_inputs
    out = tmp_path / "layout.json"
    args = ["perf", "layout", "--scene", str(scene), "--heights", str(heights), "--base", str(base),
            "--detail", str(detail_dir), "--out", str(out)]
    assert main(args) == 0
    layout = json.loads(out.read_text(encoding="utf-8"))
    assert layout["schema"] == "onward_generator.perf_layout"

    generated, _ = load_base(base, scene, heights, GEN)
    manifest = load_detail_manifest(detail_dir)
    urban = [tuple(o["position_m"]) for o in manifest["objects"] if o.get("type") == "urban_element"]
    free = free_ground(generated, urban)
    for team in ("attackers", "defenders"):
        points = [(p["x"], p["y"]) for p in layout[team]["points_m"]]
        assert len(points) == SPAWN_POINTS >= 5  # the SDK validator needs at least five per team
        for x, y in points:
            assert free.contains(Point(x, y).buffer(CLEARANCE_M - 0.01)), (team, x, y)
    (ax, ay), (bx, by) = layout["attackers"]["centre_m"], layout["defenders"]["centre_m"]
    assert math.hypot(bx - ax, by - ay) > 0
    assert free.contains(Point(*layout["uplink_m"]))
    assert layout["tent_z_m"] < 0 and layout["damage_volume"]["z_m"] < layout["tent_z_m"]

    assert main([*args[:-1], str(tmp_path / "again.json")]) == 0
    assert (tmp_path / "again.json").read_bytes() == out.read_bytes()  # deterministic
    assert main(args) == 1  # refuses to overwrite


def test_layout_fails_without_a_detail_manifest(tmp_path: Path) -> None:
    with pytest.raises(LayoutError, match="scene_detail.json"):
        load_detail_manifest(tmp_path)


def test_measure_reports_counts_and_merge_predictions(detail_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "measure.json"
    assert main(["perf", "measure", str(detail_dir), "--combine-cell-m", "10", "20", "40", "100000",
                 "--out", str(out)]) == 0
    (scene,) = json.loads(out.read_text(encoding="utf-8"))["scenes"]
    visual, collision = scene["visual"], scene["collision"]
    assert visual["triangles"] > 0 and collision["triangles"] > 0
    assert "collider" not in visual["triangles_by_material"]  # visual and collision stay separate
    draws = [p["draws"] for p in scene["combine_predictions"]]
    assert all(d <= visual["primitives_drawn"] for d in draws)
    assert draws == sorted(draws, reverse=True)  # nested cells never add draws
    # Cells are anchored at the origin (MURCIA_CENTER): a huge cell still splits the four quadrants.
    huge = scene["combine_predictions"][-1]
    assert huge["cells"] <= 4
    assert len(visual["primitives_by_material"]) <= huge["draws"] <= 4 * len(visual["primitives_by_material"])
    assert all(p["primitives_merged"] == visual["primitives_drawn"] for p in scene["combine_predictions"])


def test_measure_fails_on_a_directory_without_glb(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["perf", "measure", str(tmp_path), "--out", str(tmp_path / "m.json")]) == 1
    assert "scene_visual.glb not found" in capsys.readouterr().err
