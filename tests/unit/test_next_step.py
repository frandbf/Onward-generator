"""The line printed at the end of a stage is a complete shell command."""

from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest

from onward_generator.config.loader import MAX_SCENE_BYTES, ConfigLoadError, read_json_document
from onward_generator.maps.workspace import MapError, create_map
from onward_generator.next_step import (
    StageCommand,
    cadastre_split_command,
    catastro_command,
    core_budget,
    core_radius,
    detail_command,
    dress_command,
    emit,
    export_command,
    geo_command,
    heights_command,
    invoke,
    layout_command,
    overgrow_command,
    signs_command,
    stage_path,
)

LON = -58.381647718021945
LAT = -34.60369254204782


def test_geo_command_is_pasteable_and_uses_the_map_crs(tmp_path: Path) -> None:
    root = tmp_path / "obelisco"
    create_map(root, map_id="obelisco", name="Obelisco", latitude=LAT, longitude=LON, radius_m=400)
    command = geo_command(root).display()
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
    command = dress_command(detail, scene, heights).display()
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
    command = heights_command(scene).display()
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
    stage = export_command(detail, layout)
    assert stage is not None
    command = stage.display()
    assert "--config " in command
    assert "config/export.json" in command
    assert "--playable" not in command
    document = json.loads((root / "config" / "export.json").read_text(encoding="utf-8"))
    assert document["map"]["title"] == "Obelisco"


def test_stage_command_display_round_trips_quoted_paths(capsys: pytest.CaptureFixture[str]) -> None:
    command = invoke("heights", "--scene", "maps/it's here/geo scene.json", "--out", "a b")
    assert command == StageCommand(("heights", "--scene", "maps/it's here/geo scene.json", "--out", "a b"))
    line = command.display()
    parts = shlex.split(line)
    assert parts[parts.index("onward_generator") + 1 :] == list(command.argv)
    emit(command)
    assert capsys.readouterr().out == f"next: {line}\n"


def test_export_command_without_card_config_leaves_out_the_flag(tmp_path: Path) -> None:
    root = tmp_path / "obelisco"
    create_map(root, map_id="obelisco", name="Obelisco", latitude=LAT, longitude=LON, radius_m=400)
    (root / "config" / "export.json").unlink()
    detail = root / "data" / "processed" / "scene_playable"
    layout = root / "data" / "processed" / "layout" / "layout.json"
    command = export_command(detail, layout)
    assert command is not None
    assert "--config" not in command.argv


def test_catastro_commands_download_then_split_beside_geo(tmp_path: Path) -> None:
    scene = tmp_path / "geo" / "geo_scene.json"
    scene.parent.mkdir()
    scene.write_text("{}", encoding="utf-8")
    gml = tmp_path / "raw" / "catastro.gml"
    assert catastro_command(scene, gml).argv == ("cadastre", "download", "--scene", str(scene), "--out", str(gml))
    assert cadastre_split_command(scene, gml).argv == (
        "cadastre", "split", "--scene", str(scene), "--gml", str(gml),
        "--buildings", str(tmp_path / "raw" / "catastro_buildings.gml"), "--out", str(tmp_path / "cadastre"),
    )
    split_scene = tmp_path / "cadastre" / "geo_scene.json"
    split_scene.parent.mkdir()
    split_scene.write_text("{}", encoding="utf-8")
    overrides = tmp_path / "cadastre" / "heights_overrides.catastro.json"
    assert heights_command(split_scene, overrides).argv == (
        "heights", "--scene", str(split_scene), "--overrides", str(overrides), "--out", str(tmp_path / "heights"),
    )


def test_detail_command_carries_the_maps_playable_core(tmp_path: Path) -> None:
    root = tmp_path / "madrid"
    create_map(root, map_id="madrid", name="Madrid", latitude=40.40657, longitude=-3.719187, radius_m=300, core_radius_m=120)
    scene = root / "data" / "processed" / "cadastre" / "geo_scene.json"
    heights = root / "data" / "processed" / "heights" / "heights.json"
    argv = list(detail_command(scene, heights, root / "data" / "processed" / "scene3d").argv)
    assert argv[-2:] == ["--core-radius-m", "120"]
    assert core_radius(scene) == 120.0


def test_detail_command_without_a_core_has_no_core_flag(tmp_path: Path) -> None:
    root = tmp_path / "small"
    create_map(root, map_id="small", name="Small", latitude=40.40657, longitude=-3.719187, radius_m=120, core_radius_m=0)
    scene = root / "data" / "processed" / "geo" / "geo_scene.json"
    assert core_radius(scene) is None
    assert "--core-radius-m" not in detail_command(scene, scene, root).argv
    # An older map.json without the key, and a scene outside any map, have no core either.
    document = json.loads((root / "map.json").read_text(encoding="utf-8"))
    del document["core_radius_m"]
    (root / "map.json").write_text(json.dumps(document), encoding="utf-8")
    assert core_radius(scene) is None
    assert core_radius(tmp_path / "elsewhere" / "geo_scene.json") is None


def test_layout_and_compact_overgrow_write_their_own_directories(tmp_path: Path) -> None:
    root = tmp_path / "obelisco"
    create_map(root, map_id="obelisco", name="Obelisco", latitude=LAT, longitude=LON, radius_m=400)
    processed = root / "data" / "processed"
    for path in (processed / "geo" / "geo_scene.json", processed / "heights" / "heights.json"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    (processed / "scene3d").mkdir()
    playable = processed / "scene_playable"
    layout = layout_command(playable)
    assert layout is not None
    assert layout.argv[-2:] == ("--out", str(processed / "layout" / "layout.json"))
    assert overgrow_command(processed / "scene_signs_compact").argv[-2:] == ("--out", str(processed / "scene_phase3"))
    assert overgrow_command(processed / "scene_signs").argv[-2:] == ("--out", str(processed / "scene_overgrow"))
    assert signs_command(processed / "scene_materials_compact").argv[-2:] == ("--out", str(processed / "scene_signs_compact"))
    assert signs_command(processed / "scene_materials").argv[-2:] == ("--out", str(processed / "scene_signs"))


def _facades(root: Path, block: object) -> None:
    document = json.loads((root / "map.json").read_text(encoding="utf-8"))
    document["facades"] = block
    (root / "map.json").write_text(json.dumps(document), encoding="utf-8")


def test_materials_command_carries_the_maps_facade_block(tmp_path: Path) -> None:
    from onward_generator.maps.workspace import MapError, facade_options
    from onward_generator.next_step import materials_command

    root = tmp_path / "madrid"
    create_map(root, map_id="madrid", name="Madrid", latitude=40.40657, longitude=-3.719187, radius_m=150)
    dress = root / "data" / "processed" / "scene_dress"
    argv = list(materials_command(dress).argv)
    assert "--facades" not in argv and "--facade-style" not in argv
    assert argv[-2] == "--out"
    _facades(root, {"mode": "legacy"})
    argv = list(materials_command(dress).argv)
    assert argv[argv.index("--download") + 1 : -2] == ["--facades", "legacy"]
    _facades(root, {"style": "mediterranean"})
    assert facade_options(root) == ["--facade-style", "mediterranean"]
    _facades(root, {"mode": "trim", "style": "auto"})
    assert facade_options(root) == ["--facades", "trim", "--facade-style", "auto"]
    _facades(root, {})
    assert facade_options(root) == []
    for bad in ("trim", {"mode": 1}, {"mode": "trim", "colour": "red"}, ["trim"]):
        _facades(root, bad)
        with pytest.raises(MapError, match=r'^map\.json "facades" must be \{"mode": "trim" \| "legacy", "style": "<name>" \| "auto"\}$'):
            facade_options(root)
    assert facade_options(tmp_path / "elsewhere") == []
    assert "--facades" not in materials_command(tmp_path / "loose" / "scene_dress").argv


@pytest.mark.parametrize("command", [signs_command, overgrow_command])
def test_signs_and_overgrow_read_the_scene_detail_built_from(tmp_path: Path, command: object) -> None:
    root = tmp_path / "murcia"
    create_map(root, map_id="murcia", name="Murcia", latitude=LAT, longitude=LON, radius_m=400)
    processed = root / "data" / "processed"
    osm = processed / "geo" / "geo_scene.json"
    split = processed / "cadastre" / "geo_scene.json"
    for path in (osm, split):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    materials = processed / "scene_materials"
    materials.mkdir()

    def scene_of(command: object) -> str:
        argv = command.argv  # type: ignore[attr-defined]
        return argv[argv.index("--scene") + 1]

    # No manifest yet: the OSM scene, as before.
    assert scene_of(command(materials)) == str(osm)  # type: ignore[operator]
    (materials / "scene_detail.json").write_text(json.dumps({"inputs": {"geo_scene": {"path": str(split)}}}), encoding="utf-8")
    assert scene_of(command(materials)) == str(split)  # type: ignore[operator]
    # The runner passes its own scene, and that one wins.
    assert scene_of(command(materials, osm)) == str(osm)  # type: ignore[operator]


def test_detail_command_on_a_budget_map_carries_the_largest_core_and_the_target(tmp_path: Path) -> None:
    fixed = tmp_path / "fixed"
    create_map(fixed, map_id="fixed", name="Fixed", latitude=40.40657, longitude=-3.719187, radius_m=300)
    scene = fixed / "data" / "processed" / "geo" / "geo_scene.json"
    heights = fixed / "data" / "processed" / "heights" / "heights.json"
    base = fixed / "data" / "processed" / "scene3d"
    assert detail_command(scene, heights, base).argv == (
        "detail", "--scene", str(scene), "--heights", str(heights), "--base", str(base),
        "--out", str(fixed / "data" / "processed" / "scene_detail"), "--core-radius-m", "150",
    )
    assert core_budget(scene) is None
    root = tmp_path / "madrid"
    create_map(root, map_id="madrid", name="Madrid", latitude=40.40657, longitude=-3.719187, radius_m=300,
               core_target_triangles=150000)
    scene = root / "data" / "processed" / "geo" / "geo_scene.json"
    argv = list(detail_command(scene, heights, base).argv)
    assert argv[-4:] == ["--core-radius-m", "250", "--core-target-triangles", "150000"]
    assert argv.count("--core-radius-m") == 1
    assert core_budget(scene) == (250.0, 150000)
    assert core_radius(scene) == 150.0
    assert core_budget(tmp_path / "elsewhere" / "geo_scene.json") is None
    document = json.loads((root / "map.json").read_text(encoding="utf-8"))
    document["core"]["mode"] = "polygon"
    (root / "map.json").write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(MapError, match='core mode must be "budget"'):
        detail_command(scene, heights, base)


def test_materials_command_carries_the_repetition_switch(tmp_path: Path) -> None:
    from onward_generator.maps.workspace import store_repetition
    from onward_generator.next_step import materials_command

    root = tmp_path / "murcia"
    create_map(root, map_id="murcia", name="Murcia", latitude=37.98407, longitude=-1.12863, radius_m=150)
    dress = root / "data" / "processed" / "scene_dress"
    off = list(materials_command(dress).argv)
    assert "--repetition" not in off and "--cell-m" not in off
    assert off[off.index("--download") + 1] == "--out"
    store_repetition(root, False)
    assert list(materials_command(dress).argv) == off
    store_repetition(root, True)
    on = list(materials_command(dress).argv)
    assert on[on.index("--download") + 1 : -2] == ["--repetition", "--cell-m", "100.0"]
    assert on[:-2] == off[:-2] + ["--repetition", "--cell-m", "100.0"] and on[-2:] == off[-2:]
    card_path = root / "config" / "export.json"
    card = json.loads(card_path.read_text(encoding="utf-8"))
    card["build"]["combine_cell_m"] = 50
    card_path.write_text(json.dumps(card), encoding="utf-8")
    on = list(materials_command(dress).argv)
    assert on[on.index("--repetition") : on.index("--repetition") + 3] == ["--repetition", "--cell-m", "50.0"]
    assert "--repetition" not in materials_command(tmp_path / "loose" / "scene_dress").argv


def test_detail_command_reads_the_roof_spread_only_when_given(tmp_path: Path) -> None:
    root = tmp_path / "small"
    create_map(root, map_id="small", name="Small", latitude=40.40657, longitude=-3.719187, radius_m=120, core_radius_m=0)
    scene = root / "data" / "processed" / "geo" / "geo_scene.json"
    base = root / "data" / "processed" / "scene3d"
    report = root / "data" / "processed" / "es.pnoa_ndsm" / "es.pnoa_ndsm_report.json"
    plain = list(detail_command(scene, scene, base).argv)
    assert "--lidar-roofs" not in plain
    assert list(detail_command(scene, scene, base, None, None, None).argv) == plain
    argv = list(detail_command(scene, scene, base, roof_spread=report).argv)
    at = argv.index("--lidar-roofs")
    assert argv[at + 1] == str(report) and argv[:at] + argv[at + 2:] == plain
    assert argv[at + 2] == "--out"


def test_materials_command_carries_the_regional_facades_switch(tmp_path: Path) -> None:
    from onward_generator.maps.workspace import store_regional_facades, store_repetition
    from onward_generator.next_step import materials_command

    root = tmp_path / "krakow"
    create_map(root, map_id="krakow", name="Krakow", latitude=50.0614, longitude=19.9366, radius_m=150)
    dress = root / "data" / "processed" / "scene_dress"
    off = list(materials_command(dress).argv)
    assert "--regional-facades" not in off
    store_regional_facades(root, False)
    assert list(materials_command(dress).argv) == off
    store_regional_facades(root, True)
    on = list(materials_command(dress).argv)
    assert on.count("--regional-facades") == 1
    assert on[:-2] == off[:-2] + ["--regional-facades"] and on[-2:] == off[-2:]
    store_repetition(root, True)
    both = list(materials_command(dress).argv)
    assert both[both.index("--regional-facades") + 1] == "--repetition"
    assert "--regional-facades" not in materials_command(tmp_path / "loose" / "scene_dress").argv


def test_detail_command_carries_the_mid_ring_after_the_core(tmp_path: Path) -> None:
    """Plan D3: ``mid_ring_m`` of map.json follows the fixed or the budget core flags, and only when set."""
    from onward_generator.maps.workspace import store_mid_ring
    from onward_generator.next_step import mid_ring

    fixed = tmp_path / "fixed"
    create_map(fixed, map_id="fixed", name="Fixed", latitude=40.40657, longitude=-3.719187, radius_m=300)
    scene = fixed / "data" / "processed" / "geo" / "geo_scene.json"
    heights = fixed / "data" / "processed" / "heights" / "heights.json"
    base = fixed / "data" / "processed" / "scene3d"
    head = ("detail", "--scene", str(scene), "--heights", str(heights), "--base", str(base),
            "--out", str(fixed / "data" / "processed" / "scene_detail"))
    assert mid_ring(scene) is None and mid_ring(tmp_path / "elsewhere" / "x.json") is None
    store_mid_ring(fixed, 100)
    assert mid_ring(scene) == 100.0
    assert detail_command(scene, heights, base).argv == (*head, "--core-radius-m", "150", "--mid-ring-m", "100")
    store_mid_ring(fixed, 37.5)
    assert detail_command(scene, heights, base).argv[-2:] == ("--mid-ring-m", "37.5")
    budget = tmp_path / "budget"
    create_map(budget, map_id="budget", name="Budget", latitude=40.40657, longitude=-3.719187, radius_m=300,
               core_target_triangles=150000)
    scene = budget / "data" / "processed" / "geo" / "geo_scene.json"
    store_mid_ring(budget, 50)
    assert detail_command(scene, heights, base).argv[-6:] == (
        "--core-radius-m", "250", "--core-target-triangles", "150000", "--mid-ring-m", "50")
    document = json.loads((budget / "map.json").read_text(encoding="utf-8")) | {"mid_ring_m": 5000}
    (budget / "map.json").write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(MapError, match="from 10 to 1000"):
        detail_command(scene, heights, base)
