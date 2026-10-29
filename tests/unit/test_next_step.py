"""The line printed at the end of a stage is a complete shell command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from onward_generator.maps.workspace import create_map
from onward_generator.config.loader import MAX_SCENE_BYTES, ConfigLoadError, read_json_document
from onward_generator.next_step import dress_command, export_command, geo_command, heights_command, stage_path

LON = -58.381647718021945
LAT = -34.60369254204782


def test_geo_command_is_pasteable_and_uses_the_map_crs(tmp_path: Path) -> None:
    root = tmp_path / "obelisco"
    create_map(root, map_id="obelisco", name="Obelisco", latitude=LAT, longitude=LON, radius_m=400)
    command = geo_command(root)
    assert command.startswith(".venv/bin/python -m onward_generator geo ")
    assert f"--origin {LON} {LAT}" in command
    assert "--crs EPSG:32721" in command
    assert "--retrieved-at" not in command
    assert str(root / "data" / "raw" / "obelisco.osm") in command
    assert str(root / "data" / "processed" / "geo") in command


def test_dress_command_carries_scene_and_heights(tmp_path: Path) -> None:
    detail = tmp_path / "scene_detail"
    scene = tmp_path / "geo" / "geo_scene.json"
    heights = tmp_path / "heights" / "heights.json"
    command = dress_command(detail, scene, heights)
    assert "--scene " in command and str(scene) in command
    assert "--heights " in command and str(heights) in command


def test_scene_manifest_over_one_megabyte_still_loads(tmp_path: Path) -> None:
    path = tmp_path / "scene_detail.json"
    path.write_text(json.dumps({"inputs": {"geo_scene": {"path": "x"}}, "pad": "a" * (1024 * 1024)}), encoding="utf-8")
    assert path.stat().st_size > 1024 * 1024
    with pytest.raises(ConfigLoadError, match="limit is 1048576"):
        read_json_document(path)
    document, _ = read_json_document(path, max_bytes=MAX_SCENE_BYTES)
    assert document["inputs"]["geo_scene"]["path"] == "x"


def test_heights_sits_beside_geo_when_there_is_no_map_folder(tmp_path: Path) -> None:
    scene = tmp_path / "geo" / "geo_scene.json"
    scene.parent.mkdir()
    scene.write_text("{}", encoding="utf-8")
    command = heights_command(scene)
    assert f"--scene {scene}" in command
    assert f"--out {tmp_path / 'heights'}" in command
    assert stage_path(scene, "heights") == tmp_path / "heights"


def test_export_command_includes_the_map_card_config(tmp_path: Path) -> None:
    root = tmp_path / "obelisco"
    create_map(root, map_id="obelisco", name="Obelisco", latitude=LAT, longitude=LON, radius_m=400)
    detail = root / "data" / "processed" / "scene_playable"
    layout = root / "data" / "processed" / "layout" / "layout.json"
    detail.mkdir(parents=True)
    layout.parent.mkdir(parents=True)
    layout.write_text("{}", encoding="utf-8")
    command = export_command(detail, layout)
    assert command is not None
    assert "--config " in command
    assert "config/export.json" in command
    assert "--playable" not in command
    document = json.loads((root / "config" / "export.json").read_text(encoding="utf-8"))
    assert document["map"]["title"] == "Obelisco"
