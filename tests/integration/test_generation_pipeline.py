"""IT-003/IT-004/IT-005/IT-007: synthetic block around MURCIA_CENTER → geo → heights → 3D scene."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from onward_generator.cli import main
from onward_generator.generation import GenerationError, generate_scene, load_generation_config
from onward_generator.generation.gltf import read_glb
from onward_generator.geo.pipeline import GeoResult, write_outputs
from onward_generator.heights import run_heights, write_height_outputs

CFG = load_generation_config()


@pytest.fixture
def inputs(pipeline_result: GeoResult, tmp_path: Path) -> tuple[Path, Path]:
    scene, _ = write_outputs(pipeline_result, tmp_path / "geo")
    heights, _ = write_height_outputs(run_heights(scene), tmp_path / "heights")
    return scene, heights


def test_synthetic_block_generates_without_critical_errors(inputs: tuple[Path, Path]) -> None:
    generated = generate_scene(*inputs, CFG)
    report = generated.report()
    assert report["phase_03_exit"]["passed"], report["issues"]
    assert report["checks"]["manifold_defects"] == 0 and report["checks"]["surface_overlap_m2"] < 0.01
    ids = {o.id for o in generated.objects}
    assert {"building/osm-way-101", "building/osm-way-102", "building/osm-relation-201"} <= ids
    assert "building/osm-way-108" not in ids  # duplicate of 101
    assert "building/osm-way-113" not in ids  # leaves the map; omitted whole
    assert "duplicate_building_skipped" in report["issues_by_code"]
    assert {"surface/carriageway", "surface/sidewalk", "surface/pedestrian", "surface/ground", "surface/curb"} <= ids
    assert {"collider/walkable", "collider/carriageway"} <= ids

    b101 = next(o for o in generated.objects if o.id == "building/osm-way-101")
    assert b101.record["height"]["height_m"] == 12.0 and b101.record["height"]["height_source"] == "explicit_height"
    assert b101.record["collider"]["closed"] and b101.record["collider"]["volume_m3"] == pytest.approx(900 * 12.0, rel=1e-3)
    assert b101.record["source"]["license"] == "CC0-1.0"
    road = next(o for o in generated.objects if o.id == "surface/carriageway")
    assert road.record["z_m"] == -CFG.carriageway_depth_m and road.record["source_ids"] == ["osm-way-105"]
    for o in generated.objects:
        if o.type == "building":
            fp = next(p for p in o.visual if p.material == "building_wall").mesh
            assert fp.vertices[:, 2].min() == 0.0 and fp.vertices[:, 2].max() == o.record["height"]["height_m"]


def test_heights_from_another_scene_are_rejected(inputs: tuple[Path, Path], tmp_path: Path) -> None:
    scene, heights = inputs
    doc = json.loads(heights.read_text(encoding="utf-8"))
    doc["scene"]["sha256"] = "0" * 64
    other = tmp_path / "other_heights.json"
    other.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(GenerationError, match="different geo scene"):
        generate_scene(scene, other, CFG)


def test_missing_height_is_critical(inputs: tuple[Path, Path], tmp_path: Path) -> None:
    scene, heights = inputs
    doc = json.loads(heights.read_text(encoding="utf-8"))
    doc["buildings"] = [b for b in doc["buildings"] if b["building_id"] != "osm-way-102"]
    edited = tmp_path / "edited_heights.json"
    edited.write_text(json.dumps(doc), encoding="utf-8")
    report = generate_scene(scene, edited, CFG).report()
    assert not report["phase_03_exit"]["passed"]
    assert [i["object_id"] for i in report["issues"] if i["severity"] == "critical"] == ["osm-way-102"]


def test_degrees_are_refused(inputs: tuple[Path, Path], tmp_path: Path) -> None:
    scene, heights = inputs
    doc = json.loads(scene.read_text(encoding="utf-8"))
    doc["frame"]["unit"] = "degree"
    bad = tmp_path / "geo_scene.json"
    bad.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(GenerationError):
        generate_scene(bad, heights, CFG)


def test_cli_writes_reproducible_outputs(inputs: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    scene, heights = inputs
    args = ["generate", "--scene", str(scene), "--heights", str(heights)]
    assert main([*args, "--out", str(tmp_path / "a")]) == 0
    assert "phase 03 exit: PASSED" in capsys.readouterr().out
    assert main([*args, "--out", str(tmp_path / "b"), "--no-preview"]) == 0
    for name in ("scene_visual.glb", "scene_collision.glb", "scene3d.json", "generation_report.json"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes(), name
    doc, _ = read_glb((tmp_path / "a" / "scene_visual.glb").read_bytes())
    assert doc["extras"]["attribution"][0]["license"] == "CC0-1.0"
    assert (tmp_path / "a" / "preview_southwest.png").read_bytes()[:4] == b"\x89PNG"
    assert main([*args, "--out", str(tmp_path / "a")]) == 1
    assert "refusing to overwrite" in capsys.readouterr().err
