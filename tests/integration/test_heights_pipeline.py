"""Geo fixture (around MURCIA_CENTER) → geo_scene.json → heights outputs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from onward_generator.cli import main
from onward_generator.geo.pipeline import GeoResult, write_outputs
from onward_generator.geo.sources import sha256_file
from onward_generator.heights import SceneError, run_heights, write_height_outputs


@pytest.fixture
def scene_path(pipeline_result: GeoResult, tmp_path: Path) -> Path:
    scene, _ = write_outputs(pipeline_result, tmp_path / "geo")
    return scene


def test_heights_for_geo_fixture(scene_path: Path, tmp_path: Path) -> None:
    before = sha256_file(scene_path)
    run = run_heights(scene_path)
    heights_path, report_path = write_height_outputs(run, tmp_path / "heights")
    assert sha256_file(scene_path) == before

    heights = json.loads(heights_path.read_text(encoding="utf-8"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    by_id = {b["building_id"]: b for b in heights["buildings"]}

    assert heights["schema"] == "onward_generator.building_heights"
    assert heights["scene"]["sha256"] == before
    assert heights["attribution"]["license"] == "CC0-1.0"
    assert heights["assumptions"]["fallback_m"]["unknown"]["default"] == 8.0
    assert len(by_id) == report["counts"]["buildings"] == 5
    assert "osm-way-113" not in by_id
    assert by_id["osm-way-101"]["height_m"] == 12.0 and by_id["osm-way-101"]["height_source"] == "explicit_height"
    assert by_id["osm-way-102"]["height_source"] == "explicit_levels" and by_id["osm-way-102"]["height_m"] == 9.0
    for b in by_id.values():
        assert b["height_m"] > 0 and b["height_source"] and 0 <= b["height_confidence"] <= 1 and b["estimated_levels"] >= 1

    assert sum(report["percent_by_class"].values()) == pytest.approx(100.0, abs=0.05)
    assert sum(report["counts"]["by_source"].values()) == 5
    assert report["percent_by_class"]["measured"] == pytest.approx(100 * 1 / 5, abs=0.01)


def test_outputs_are_deterministic_and_not_overwritten(scene_path: Path, tmp_path: Path) -> None:
    a = write_height_outputs(run_heights(scene_path), tmp_path / "a")
    b = write_height_outputs(run_heights(scene_path), tmp_path / "b")
    for x, y in zip(a, b, strict=True):
        assert x.read_bytes().replace(b"/a/", b"/b/") == y.read_bytes()
    with pytest.raises(FileExistsError):
        write_height_outputs(run_heights(scene_path), tmp_path / "a")
    write_height_outputs(run_heights(scene_path), tmp_path / "a", overwrite=True)


def test_refuses_to_write_over_scene(scene_path: Path) -> None:
    renamed = scene_path.with_name("heights.json")
    scene_path.rename(renamed)
    with pytest.raises(ValueError, match="input"):
        write_height_outputs(run_heights(renamed), renamed.parent, overwrite=True)


def test_rejects_non_scene_input(tmp_path: Path) -> None:
    bad = tmp_path / "x.json"
    bad.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(SceneError):
        run_heights(bad)
    with pytest.raises(SceneError):
        run_heights(tmp_path / "missing.json")


def test_cli(scene_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "cli"
    assert main(["heights", "--scene", str(scene_path), "--out", str(out)]) == 0
    assert "buildings=5" in capsys.readouterr().out
    assert (out / "heights.json").is_file() and (out / "heights_report.json").is_file()
    assert main(["heights", "--scene", str(scene_path), "--out", str(out)]) == 1
    assert "refusing to overwrite" in capsys.readouterr().err
    assert main(["heights", "--scene", str(scene_path), "--out", str(out), "--config", str(tmp_path / "no.json")]) == 1
