"""Bounds overlay on the synthetic block around MURCIA_CENTER."""

from __future__ import annotations

import json
from pathlib import Path

from onward_generator.cli import main
from onward_generator.dress.geometry import compare_fingerprints, geometry_fingerprint
from onward_generator.generation.gltf import nodes_from_glb, read_glb
from tests.conftest import MURCIA_CENTER


def test_bounds_adds_wall_without_moving_geography(detail_inputs: tuple[Path, Path, Path], tmp_path: Path) -> None:
    lon, lat = MURCIA_CENTER
    assert (lon, lat) == MURCIA_CENTER
    scene, heights, base = detail_inputs
    assert scene.is_file() and heights.is_file()
    out = tmp_path / "playable"
    assert main(["bounds", "--base", str(base), "--out", str(out)]) == 0
    assert (out / "bounds_report.json").is_file()
    report = json.loads((out / "bounds_report.json").read_text())
    assert report["wall"]["id"] == "envelope/perimeter_wall"
    assert report["geometry"]["identical"] is True
    src = json.loads((base / "scene3d.json").read_text())
    dst = json.loads((out / "scene3d.json").read_text())
    keep = {"building", "surface", "curb", "landmark_part"}
    compare = compare_fingerprints(
        geometry_fingerprint([o for o in src["objects"] if o.get("type") in keep]),
        geometry_fingerprint([o for o in dst["objects"] if o.get("type") in keep]),
    )
    assert compare["identical"], compare
    assert any(o.get("id") == "envelope/perimeter_wall" for o in dst["objects"])
    names = {n.name for n in nodes_from_glb((out / "scene_visual.glb").read_bytes())[0]}
    assert "envelope/perimeter_wall" not in names
    collision = {n.name for n in nodes_from_glb((out / "scene_collision.glb").read_bytes())[0]}
    assert "envelope/perimeter_wall#collider" in collision
    doc, _ = read_glb((out / "scene_collision.glb").read_bytes())
    assert any(n.get("name") == "envelope/perimeter_wall#collider" for n in doc["nodes"])
    # original generate dir is untouched
    assert not any(o.get("id") == "envelope/perimeter_wall" for o in src["objects"])


def test_bounds_on_detail_keeps_phase04_report(detail_inputs: tuple[Path, Path, Path], tmp_path: Path) -> None:
    scene, heights, base = detail_inputs
    detail = tmp_path / "detail"
    assert main(["detail", "--scene", str(scene), "--heights", str(heights),
                 "--base", str(base), "--out", str(detail), "--no-preview"]) == 0
    out = tmp_path / "detail_playable"
    assert main(["bounds", "--base", str(detail), "--out", str(out), "--height-m", "10"]) == 0
    report = json.loads((out / "detail_report.json").read_text())
    assert report["phase_04_checks"]["automatic_checks_passed"] is True
    assert report["bounds"]["wall"]["height_m"] == 10
    assert (detail / "scene_visual.glb").read_bytes() == (out / "scene_visual.glb").read_bytes()
    collision = {n.name for n in nodes_from_glb((out / "scene_collision.glb").read_bytes())[0]}
    assert "envelope/perimeter_wall#collider" in collision
