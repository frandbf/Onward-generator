"""The pipeline runner waits for each stage and stops at the first failure."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from onward_generator.cli import main
from onward_generator.maps.workspace import create_map
from onward_generator.next_step import export_command, geo_command
from onward_generator.osm.acquire import osm_target
from onward_generator.pipeline_run import (
    _build_package,
    _push_package,
    _stage_summary,
    _unity_summary,
    format_duration,
    map_create_argv,
    output_ready,
    plan_steps,
    prepare_from,
    run_map,
    run_steps,
    unity_editor_open,
)
from tests.conftest import MURCIA_CENTER

LON, LAT = MURCIA_CENTER


@pytest.fixture(autouse=True)
def _unity_editor_is_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The developer's Editor may be open. These tests do not ask it to close."""
    monkeypatch.setattr("onward_generator.pipeline_run.unity_editor_open", lambda: False)


def test_map_create_keeps_latitude_then_longitude() -> None:
    argv = map_create_argv(
        name="Murcia",
        latitude=LAT,
        longitude=LON,
        radius_m=400,
        margin_m=30,
        brief="ciudad abandonada",
        ambient_ai=True,
    )
    assert "--id" not in argv and "--out" not in argv
    assert "--brief" in argv and "ciudad abandonada" in argv and "--ai" in argv
    assert argv[argv.index("--lat") + 1] == str(LAT)
    assert argv[argv.index("--lon") + 1] == str(LON)
    assert argv[argv.index("--lightmap-texels-per-m") + 1] == "10.0"
    assert LAT == 37.98407
    assert LON == -1.12863


def test_plan_runs_python_stages_and_stops_before_unity(tmp_path) -> None:
    root = tmp_path / "murcia"
    create_map(root, map_id="murcia", name="Murcia", latitude=LAT, longitude=LON, radius_m=400)
    names = [name for name, _argv in plan_steps(root)]
    assert names == [
        "geo", "heights", "generate", "detail", "dress", "materials",
        "signs", "overgrow", "fit", "bounds", "layout", "export",
    ]
    joined = " ".join(" ".join(argv) for _name, argv in plan_steps(root))
    assert "unity" not in joined
    assert "push" not in joined
    assert f"--origin {LON} {LAT}" in joined
    assert "panoramax" not in names


def test_a_map_without_the_panoramax_key_keeps_the_current_graph(tmp_path: Path) -> None:
    root = tmp_path / "murcia"
    create_map(root, map_id="murcia", name="Murcia", latitude=LAT, longitude=LON, radius_m=400)
    document = json.loads((root / "map.json").read_text(encoding="utf-8"))
    document.pop("panoramax")
    (root / "map.json").write_text(json.dumps(document), encoding="utf-8")
    steps = dict(plan_steps(root))
    assert "panoramax" not in steps
    signs = steps["signs"]
    assert "scene_materials" in signs[signs.index("--base") + 1]


def test_panoramax_true_inserts_the_step_before_signs(tmp_path: Path) -> None:
    root = tmp_path / "murcia"
    create_map(
        root, map_id="murcia", name="Murcia", latitude=LAT, longitude=LON, radius_m=400, panoramax=True,
    )
    names = [name for name, _argv in plan_steps(root)]
    assert names[names.index("materials") + 1] == "panoramax"
    assert names[names.index("panoramax") + 1] == "signs"
    steps = dict(plan_steps(root))
    signs = steps["signs"]
    assert steps["panoramax"][0] == "panoramax"
    assert "scene_panoramax" in signs[signs.index("--base") + 1]


def test_panoramax_off_writes_nothing(tmp_path: Path) -> None:
    root = tmp_path / "murcia"
    create_map(root, map_id="murcia", name="Murcia", latitude=LAT, longitude=LON, radius_m=400)
    out = tmp_path / "scene_panoramax"
    code = main([
        "panoramax", "apply",
        "--base", str(root / "data" / "processed" / "scene_materials"),
        "--out", str(out),
    ])
    assert code == 0
    assert not out.exists()


def test_failure_is_reported_and_later_steps_do_not_start(capsys: pytest.CaptureFixture[str]) -> None:
    seen: list[str] = []

    def execute(argv: list[str]) -> int:
        seen.append(argv[0])
        if argv[0] == "heights":
            print("heights: the scene is outside the area", file=sys.stderr)
            return 1
        print(f"finished {argv[0]}")
        return 0

    code = run_steps(
        [("geo", ["geo"]), ("heights", ["heights"]), ("generate", ["generate"])],
        execute=execute,
    )
    captured = capsys.readouterr()
    assert code == 1
    assert seen == ["geo", "heights"]
    failed_at = captured.out.index("FAILED heights")
    assert "Next: run again from heights (step 2/3). The steps before it do not need to run again." in captured.out
    assert captured.out.index("finished geo") < failed_at
    assert "heights: the scene is outside the area" in captured.out
    assert "generate" not in captured.out


def test_each_stage_prints_its_duration(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    ticks = iter((10.0, 12.4, 12.4, 87.4))
    monkeypatch.setattr("onward_generator.pipeline_run.time.perf_counter", lambda: next(ticks))
    recorded: list[tuple[str, float]] = []

    code = run_steps(
        [("geo", ["geo"]), ("heights", ["heights"])],
        execute=lambda _argv: 0,
        times=recorded,
    )
    captured = capsys.readouterr()
    assert code == 0
    assert "OK geo  2.4 s" in captured.out
    assert "OK heights  1 min 15 s" in captured.out
    assert recorded[0][0] == "geo" and recorded[1][0] == "heights"
    assert format_duration(3661) == "1 h 01 min"


def test_a_stage_summary_drops_paths_and_groups_skips() -> None:
    text = "\n".join([
        "source:   /Volumes/Personal/Repositorios/Onward/maps/lascondes/data/raw/lascondes.osm (sha256 80498da080b6…, ODbL-1.0)",
        "frame:    EPSG:32719, origin -70.5638000, -33.4039900",
        "kept:     35  dropped: 32  rejected: 0",
        "written:  /Volumes/Personal/Repositorios/Onward/maps/lascondes/data/processed/geo/geo_scene.json",
        "materials apply: applied=10 skipped=6 brick_nodes=0 cobble=False",
        "  skip plaster_ochre_wall: color_id_not_used",
        "  skip tree_bark: color_id_not_used",
        "  WARNING project: the adapter has not passed the Quest acceptance test yet",
        "package lascondes_b31e46f683 written to /Volumes/Personal/Repositorios/Onward/maps/lascondes/data/processed/export/pkg_01",
        "  adapter onward_unity 0.1.0 (device_verified: False)",
    ])
    summary = _stage_summary(text)
    assert "lascondes.osm" not in summary
    assert "geo_scene.json" not in summary
    assert "acceptance test" not in summary
    assert "written to" not in summary
    assert "  frame:    EPSG:32719, origin -70.5638000, -33.4039900" in summary
    assert "  kept:     35  dropped: 32  rejected: 0" in summary
    assert "  skipped 2: plaster_ochre_wall, tree_bark" in summary
    assert "  package lascondes_b31e46f683" in summary
    assert "device_verified: False" in summary


def test_unity_summary_keeps_the_result_and_drops_the_file_listing() -> None:
    text = "\n".join([
        "already applied",
        "[export] copied OnwardMapAdapter.cs and listing thumbnail into the project",
        "[export] step 'scene' -> /Users/frandbf/Library/Logs/onward-export/lascondes-scene.log",
        "[OnwardAdapter] Package lascondes: 5 files verified (manifest sha256 abc)",
        "[OnwardAdapter] Navigation spacing 5.8 m, attempt 1: 445 nodes, depth 43, one-way 1147, dropped 0.",
        "[OnwardAdapter] Route_30 dropped: fewer than 2 points on a walkable path.",
        "[OnwardAdapter] Route_30 WP-1 dropped: no return path from the previous point.",
        "[OnwardAdapter] Scene saved: Assets/OnwardAdapter/lascondes.unity. Lightmaps: 2.",
        "[OnwardAdapter] OK",
        "[OnwardAdapter] Launcher output OK in 21.2 s: /tmp/unity-export.local.content3 (67067253 bytes)",
        "total 131000",
        "-rw-r--r--  1 frandbf  staff  67067253 Oct 21 19:06 unity-export.local.content3",
        "info3 MapName='Unity Export' package_title='LasCondes' environment=Urban",
        "[export] content3 md5 0000000000000000000000000000000a",
        "[export] device backup: /tmp/backup (2 files)",
        "[export] pushed lascondes_b31e46f683 (md5 76ec57e757646c7dd9c488dfdae47957 verified on device)",
    ])
    summary = _unity_summary(text, failed=False, content_md5="76ec57e757646c7dd9c488dfdae47957")
    assert "445 nodes" in summary
    assert "routes dropped: 1" in summary
    assert "launcher OK in 21.2 s, 64 MB" in summary
    assert "info3 MapName='Unity Export'" in summary
    assert "content3 md5 76ec57e75764" in summary
    assert "000000000000" not in summary
    assert "device backup saved" in summary
    assert "pushed lascondes_b31e46f683" in summary
    assert "Scene saved" not in summary
    assert "-rw-" not in summary
    assert "onward-export" not in summary


def test_plan_steps_argv_equals_command_argv(tmp_path: Path) -> None:
    root = tmp_path / "murcia"
    create_map(root, map_id="murcia", name="Murcia", latitude=LAT, longitude=LON, radius_m=400)
    steps = dict(plan_steps(root))
    assert steps["geo"] == list(geo_command(root).argv)
    playable = root / "data" / "processed" / "scene_playable"
    layout = root / "data" / "processed" / "layout" / "layout.json"
    exported = export_command(playable, layout)
    assert exported is not None
    assert steps["export"] == list(exported.argv)


def test_unity_summary_reads_md5_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    package = tmp_path / "pkg_01"
    (package / "build").mkdir(parents=True)
    (package / "build" / "content3.md5").write_text("76ec57e757646c7dd9c488dfdae47957\n", encoding="utf-8")
    log = "info3 MapName='Unity Export'\n[export] content3 md5 ffffffffffffffffffffffffffffffff\n"
    monkeypatch.setattr("onward_generator.pipeline_run._run_export", lambda args, env=None: (0, log))
    assert _build_package(package, ["launcher"]) == 0
    assert capsys.readouterr().out == "  info3 MapName='Unity Export'\n  content3 md5 76ec57e75764\n"


def test_missing_md5_file_is_silent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    package = tmp_path / "pkg_01"
    package.mkdir()
    log = "[export] content3 md5 76ec57e757646c7dd9c488dfdae47957\n"
    monkeypatch.setattr("onward_generator.pipeline_run._run_export", lambda args, env=None: (0, log))
    assert _build_package(package, ["launcher"]) == 0
    assert "md5" not in capsys.readouterr().out


def test_a_successful_step_prints_its_result_without_the_file_list(capsys: pytest.CaptureFixture[str]) -> None:
    def execute(argv: list[str]) -> int:
        print("signs: plates=2")
        print("  Calle Mayor at [1.0, 2.0, 2.55]")
        print("wrote maps/murcia/scene_signs")
        print("next: .venv/bin/python -m onward_generator bounds")
        print('{"schema": "onward_generator.gameplay_layout", "purpose": "playable"}')
        return 0

    code = run_steps([("signs", ["signs"])], execute=execute)
    captured = capsys.readouterr()
    assert code == 0
    assert "signs: plates=2" in captured.out
    assert "OK signs" in captured.out
    assert "Calle Mayor" not in captured.out
    assert "wrote " not in captured.out
    assert "next:" not in captured.out
    assert "gameplay_layout" not in captured.out


def test_run_command_passes_the_centre(monkeypatch: pytest.MonkeyPatch) -> None:
    received: dict[str, object] = {}

    def fake_run_map(**kwargs: object) -> int:
        received.update(kwargs)
        return 0

    monkeypatch.setattr("onward_generator.pipeline_run.run_map", fake_run_map)
    code = main([
        "run", "--name", "Apocalipsis Murciana",
        "--lat", str(LAT), "--lon", str(LON),
    ])
    assert code == 0
    assert received["latitude"] == LAT
    assert received["longitude"] == LON
    assert received["map_id"] == "apocalipsis_murciana"
    assert received["out"] == "maps/apocalipsis_murciana"
    assert received["name"] == "Apocalipsis Murciana"


def test_rerun_after_a_failed_osm_fetch_continues_at_osm(tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "murciav2"
    create_map(root, map_id="murcia", name="Murcia", latitude=LAT, longitude=LON, radius_m=250, margin_m=30)
    seen: list[str] = []

    def execute(argv: list[str]) -> int:
        seen.append(argv[0])
        return 0

    code = run_map(
        map_id="murcia",
        name="Murcia",
        latitude=LAT,
        longitude=LON,
        radius_m=250,
        margin_m=30,
        out=str(root),
        execute=execute,
        interactive=False,
    )
    captured = capsys.readouterr()
    assert code == 0
    assert seen[0] == "osm"
    assert "map" not in seen
    assert "continue from osm" in captured.out


def test_several_finished_steps_are_listed_and_the_answer_is_used(tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "murcia"
    create_map(root, map_id="murcia", name="Murcia", latitude=LAT, longitude=LON, radius_m=400)
    osm = root / "data" / "raw" / "murcia.osm"
    osm.write_text("<osm/>", encoding="utf-8")
    geo = root / "data" / "processed" / "geo"
    geo.mkdir(parents=True)
    (geo / "geo_scene.json").write_text("{}", encoding="utf-8")
    seen: list[list[str]] = []

    def execute(argv: list[str]) -> int:
        seen.append(list(argv))
        return 0

    code = run_map(
        map_id="murcia",
        name="Murcia",
        latitude=LAT,
        longitude=LON,
        radius_m=400,
        margin_m=30,
        out=str(root),
        execute=execute,
        interactive=True,
        ask=lambda: "geo",
    )
    captured = capsys.readouterr()
    assert code == 0
    assert "Pasos ya hechos:" in captured.out
    assert "1 map" in captured.out
    assert "2 osm" in captured.out
    assert "3 geo" in captured.out
    assert seen[0][0] == "geo"
    assert "--overwrite" in seen[0]
    assert seen[1][0] == "heights"


def _finish_python_stages(root: Path) -> None:
    osm = osm_target(root)
    osm.parent.mkdir(parents=True, exist_ok=True)
    osm.write_text("<osm/>", encoding="utf-8")
    for _attempt in range(4):
        pending = False
        for name, argv in plan_steps(root):
            if output_ready(name, argv, root):
                continue
            pending = True
            destination = Path(argv[argv.index("--out") + 1])
            if name == "geo":
                destination.mkdir(parents=True, exist_ok=True)
                (destination / "geo_scene.json").write_text("{}", encoding="utf-8")
            elif name == "heights":
                destination.mkdir(parents=True, exist_ok=True)
                (destination / "heights.json").write_text("{}", encoding="utf-8")
            elif name == "layout":
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text("{}", encoding="utf-8")
            elif name == "export":
                destination.mkdir(parents=True, exist_ok=True)
                (destination / "onward_export.json").write_text("{}", encoding="utf-8")
            elif name == "fit":
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text("{}", encoding="utf-8")
            else:
                destination.mkdir(parents=True, exist_ok=True)
                (destination / "scene_visual.glb").write_bytes(b"glb")
        if not pending:
            return
    raise AssertionError("pipeline outputs were not marked ready")


def _ready_map(tmp_path):
    root = tmp_path / "murcia"
    create_map(root, map_id="murcia", name="Murcia", latitude=LAT, longitude=LON, radius_m=400)
    _finish_python_stages(root)
    return root


def test_a_yes_uploads_the_built_package(tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _ready_map(tmp_path)
    package = root / "data" / "processed" / "export" / "pkg_01"
    launcher = package / "build" / "launcher"
    launcher.mkdir(parents=True)
    (launcher / "unity-export.local.content3").write_bytes(b"content")
    (launcher / "unity-export.local.info3").write_bytes(b"info")
    pushed: list[str] = []

    code = run_map(
        map_id="murcia",
        name="Murcia",
        latitude=LAT,
        longitude=LON,
        radius_m=400,
        margin_m=30,
        out=str(root),
        execute=lambda _argv: 1,
        interactive=True,
        ask=lambda: "s",
        push=lambda path: pushed.append(str(path)) or 0,
        build=lambda _path, _steps: 1,
    )
    captured = capsys.readouterr()
    assert code == 0
    assert "unity already has the Quest build" in captured.out
    assert "¿Subir al Quest?" in captured.out
    assert pushed == [str(package)]
    assert "OK quest" in captured.out
    assert "run: time" in captured.out
    assert "  quest" in captured.out
    assert "  total" in captured.out


def _write_launcher(package: Path) -> None:
    launcher = package / "build" / "launcher"
    launcher.mkdir(parents=True, exist_ok=True)
    (launcher / "unity-export.local.content3").write_bytes(b"content")
    (launcher / "unity-export.local.info3").write_bytes(b"info")


def test_unity_builds_the_quest_files_before_asking(tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _ready_map(tmp_path)
    package = root / "data" / "processed" / "export" / "pkg_01"
    built: list[list[str]] = []
    pushed: list[str] = []

    def build(path: Path, steps: list[str]) -> int:
        built.append(list(steps))
        _write_launcher(path)
        return 0

    code = run_map(
        map_id="murcia",
        name="Murcia",
        latitude=LAT,
        longitude=LON,
        radius_m=400,
        margin_m=30,
        out=str(root),
        interactive=True,
        ask=lambda: "",
        push=lambda path: pushed.append(str(path)) or 0,
        build=build,
    )
    captured = capsys.readouterr()
    assert code == 0
    assert built == [["tool", "scene", "launcher"]]
    assert "OK unity" in captured.out
    assert "¿Subir al Quest?" in captured.out
    assert pushed == []


def test_unity_skips_a_scene_that_already_exists(tmp_path) -> None:
    root = _ready_map(tmp_path)
    package = root / "data" / "processed" / "export" / "pkg_01"
    report = package / "build" / "scene_report.json"
    report.parent.mkdir(parents=True)
    report.write_text("{}", encoding="utf-8")
    built: list[list[str]] = []

    def build(path: Path, steps: list[str]) -> int:
        built.append(list(steps))
        _write_launcher(path)
        return 0

    code = run_map(
        map_id="murcia",
        name="Murcia",
        latitude=LAT,
        longitude=LON,
        radius_m=400,
        margin_m=30,
        out=str(root),
        interactive=False,
        push=lambda _path: 0,
        build=build,
    )
    assert code == 0
    assert built == [["tool", "launcher"]]


def test_any_other_answer_does_not_upload(tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _ready_map(tmp_path)
    pushed: list[str] = []

    def build(path: Path, steps: list[str]) -> int:
        del steps
        _write_launcher(path)
        return 0

    code = run_map(
        map_id="murcia",
        name="Murcia",
        latitude=LAT,
        longitude=LON,
        radius_m=400,
        margin_m=30,
        out=str(root),
        interactive=True,
        ask=lambda: "",
        push=lambda path: pushed.append(str(path)) or 0,
        build=build,
    )
    captured = capsys.readouterr()
    assert code == 0
    assert pushed == []
    assert "Quest upload skipped." in captured.out


def test_a_failed_unity_build_does_not_ask_to_upload(tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _ready_map(tmp_path)
    asked = {"n": 0}

    def ask() -> str:
        asked["n"] += 1
        return "s"

    code = run_map(
        map_id="murcia",
        name="Murcia",
        latitude=LAT,
        longitude=LON,
        radius_m=400,
        margin_m=30,
        out=str(root),
        interactive=True,
        ask=ask,
        push=lambda _path: 0,
        build=lambda _path, _steps: 1,
    )
    captured = capsys.readouterr()
    assert code == 1
    assert asked["n"] == 0
    assert "FAILED unity" in captured.out
    assert "¿Subir al Quest?" not in captured.out


def test_a_failed_step_does_not_ask_to_upload(tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "murcia"
    create_map(root, map_id="murcia", name="Murcia", latitude=LAT, longitude=LON, radius_m=400)
    asked = {"n": 0}

    def ask() -> str:
        asked["n"] += 1
        return "s"

    code = run_map(
        map_id="murcia",
        name="Murcia",
        latitude=LAT,
        longitude=LON,
        radius_m=400,
        margin_m=30,
        out=str(root),
        execute=lambda _argv: 1,
        interactive=True,
        ask=ask,
        push=lambda _path: 0,
    )
    captured = capsys.readouterr()
    assert code == 1
    assert asked["n"] == 0
    assert "¿Subir al Quest?" not in captured.out


def test_push_sets_confirm_only_for_the_script(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    seen: dict[str, object] = {}

    def fake_run(args, *, env, check, capture_output, text):
        seen["args"] = list(args)
        seen["confirm"] = env.get("CONFIRM_PUSH")
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("onward_generator.pipeline_run.subprocess.run", fake_run)
    before = os.environ.get("CONFIRM_PUSH")
    assert _push_package(tmp_path) == 0
    assert seen["confirm"] == "yes"
    assert seen["args"][-2:] == [str(tmp_path), "push"]
    assert os.environ.get("CONFIRM_PUSH") == before


def test_signs_keeps_the_map_scene_path_before_geo_exists(tmp_path: Path) -> None:
    root = tmp_path / "murcia"
    create_map(root, map_id="murcia", name="Murcia", latitude=LAT, longitude=LON, radius_m=400)
    signs = dict(plan_steps(root))["signs"]
    scene = signs[signs.index("--scene") + 1]
    assert scene.endswith("data/processed/geo/geo_scene.json")
    bounds = dict(plan_steps(root))["bounds"]
    assert Path(bounds[bounds.index("--out") + 1]).name == "scene_playable"


def test_bounds_stays_on_scene_playable_when_later_copies_exist(tmp_path: Path) -> None:
    root = tmp_path / "murcia"
    create_map(root, map_id="murcia", name="Murcia", latitude=LAT, longitude=LON, radius_m=400)
    for name in ("scene_playable", "scene_playable_compact", "scene_playable_next"):
        directory = root / "data" / "processed" / name
        directory.mkdir(parents=True)
        (directory / "scene_visual.glb").write_bytes(b"glb")
    bounds = dict(plan_steps(root))["bounds"]
    assert Path(bounds[bounds.index("--out") + 1]).name == "scene_playable"


def test_an_open_editor_asks_before_any_stage_starts(tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "murcia"
    create_map(root, map_id="murcia", name="Murcia", latitude=LAT, longitude=LON, radius_m=400)
    seen: list[str] = []
    state = {"open": True, "asks": 0}

    def unity_open() -> bool:
        return state["open"]

    def ask() -> str:
        state["asks"] += 1
        if state["asks"] == 1:
            return "s"
        state["open"] = False
        return "si"

    code = run_map(
        map_id="murcia",
        name="Murcia",
        latitude=LAT,
        longitude=LON,
        radius_m=400,
        margin_m=30,
        out=str(root),
        execute=lambda argv: seen.append(argv[0]) or 1,
        interactive=True,
        ask=ask,
        unity_open=unity_open,
    )
    captured = capsys.readouterr()
    assert code == 1
    assert state["asks"] == 2
    assert seen == ["osm"]
    assert "Unity tiene abierto" in captured.out
    assert "Sigue abierto" in captured.out
    assert "run: Unity está cerrado. El pipeline empieza." in captured.out
    assert captured.out.index("El pipeline empieza.") < captured.out.index("run: 1/")


def test_declining_to_close_unity_does_not_start(tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "murcia"
    create_map(root, map_id="murcia", name="Murcia", latitude=LAT, longitude=LON, radius_m=400)
    seen: list[str] = []

    code = run_map(
        map_id="murcia",
        name="Murcia",
        latitude=LAT,
        longitude=LON,
        radius_m=400,
        margin_m=30,
        out=str(root),
        execute=lambda argv: seen.append(argv[0]) or 0,
        interactive=True,
        ask=lambda: "",
        unity_open=lambda: True,
    )
    captured = capsys.readouterr()
    assert code == 1
    assert seen == []
    assert "run: cancelled. El pipeline no ha empezado." in captured.out
    assert "run: 1/" not in captured.out


def test_a_closed_editor_starts_without_asking(tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "murcia"
    create_map(root, map_id="murcia", name="Murcia", latitude=LAT, longitude=LON, radius_m=400)
    asked = {"n": 0}

    code = run_map(
        map_id="murcia",
        name="Murcia",
        latitude=LAT,
        longitude=LON,
        radius_m=400,
        margin_m=30,
        out=str(root),
        execute=lambda _argv: 1,
        interactive=True,
        ask=lambda: asked.__setitem__("n", asked["n"] + 1) or "s",
        unity_open=lambda: False,
    )
    captured = capsys.readouterr()
    assert code == 1
    assert asked["n"] == 0
    assert "Unity tiene abierto" not in captured.out
    assert "FAILED osm" in captured.out


def test_a_non_interactive_run_stops_when_unity_is_open(tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "murcia"
    create_map(root, map_id="murcia", name="Murcia", latitude=LAT, longitude=LON, radius_m=400)
    seen: list[str] = []

    code = run_map(
        map_id="murcia",
        name="Murcia",
        latitude=LAT,
        longitude=LON,
        radius_m=400,
        margin_m=30,
        out=str(root),
        execute=lambda argv: seen.append(argv[0]) or 0,
        interactive=False,
        unity_open=lambda: True,
    )
    captured = capsys.readouterr()
    assert code == 1
    assert seen == []
    assert "FAILED unity. Unity tiene abierto" in captured.out
    assert "terminal" in captured.out


def test_unity_editor_open_reads_a_live_instance_or_a_held_lock(tmp_path: Path) -> None:
    assert unity_editor_open(tmp_path) is False
    instance = tmp_path / "Library" / "EditorInstance.json"
    instance.parent.mkdir(parents=True)
    finished = subprocess_pid()
    instance.write_text(json.dumps({"process_id": finished}), encoding="utf-8")
    assert unity_editor_open(tmp_path) is False
    instance.write_text(json.dumps({"process_id": os.getpid()}), encoding="utf-8")
    assert unity_editor_open(tmp_path) is True
    lock = tmp_path / "Temp" / "UnityLockfile"
    lock.parent.mkdir()
    lock.write_bytes(b"")
    assert unity_editor_open(tmp_path) is False
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import fcntl,sys,time; f=open(sys.argv[1],'rb'); fcntl.flock(f.fileno(), fcntl.LOCK_EX); print('held', flush=True); time.sleep(30)",
            str(lock),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "held"
        assert unity_editor_open(tmp_path) is True
    finally:
        holder.kill()
        holder.wait(timeout=5)


def subprocess_pid() -> int:
    finished = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], check=True, capture_output=True, text=True)
    return int(finished.stdout.strip())


def test_an_incomplete_directory_is_rerun_with_overwrite(tmp_path: Path) -> None:
    root = tmp_path / "murcia"
    out = root / "data" / "processed" / "scene_detail"
    out.mkdir(parents=True)
    (out / "partial.json").write_text("{}", encoding="utf-8")
    prepared = prepare_from([("detail", ["detail", "--out", str(out)])], "detail", root)
    assert "--overwrite" in prepared[0][1]
    assert not output_ready("detail", prepared[0][1], root)


def test_turning_panoramax_on_rewrites_signs_and_keeps_the_package(tmp_path: Path) -> None:
    root = _ready_map(tmp_path)
    assert not (root / "data" / "processed" / "scene_panoramax").exists()
    seen: list[list[str]] = []

    code = run_map(
        map_id="murcia",
        name="Murcia",
        latitude=LAT,
        longitude=LON,
        radius_m=400,
        margin_m=30,
        out=str(root),
        panoramax=True,
        execute=lambda argv: seen.append(list(argv)) or 0,
        interactive=False,
        push=lambda _path: 0,
    )
    assert code == 0
    assert not output_ready("signs", [], root)

    def label(argv: list[str]) -> str:
        if argv[:2] == ["overgrow", "fit"]:
            return "fit"
        if argv[:2] == ["perf", "layout"]:
            return "layout"
        return argv[0]

    by_name = {label(argv): argv for argv in seen}
    assert label(seen[0]) == "panoramax"
    assert "--overwrite" not in by_name["panoramax"]
    assert "scene_panoramax" in by_name["signs"][by_name["signs"].index("--base") + 1]
    for name in ("signs", "overgrow", "fit", "bounds", "layout"):
        assert "--overwrite" in by_name[name]
    export = by_name["export"]
    assert "--overwrite" not in export
    assert Path(export[export.index("--out") + 1]).name == "pkg_02"
    assert (root / "data" / "processed" / "export" / "pkg_01").is_dir()


def test_a_newer_layout_exports_the_next_package(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _ready_map(tmp_path)
    now = Path(root / "map.json").stat().st_mtime
    playable = root / "data" / "processed" / "scene_playable" / "scene_visual.glb"
    layout = root / "data" / "processed" / "layout" / "layout.json"
    package = root / "data" / "processed" / "export" / "pkg_01" / "onward_export.json"
    os.utime(playable, (now + 10, now + 10))
    os.utime(layout, (now + 11, now + 11))
    os.utime(package, (now - 100, now - 100))
    seen: list[list[str]] = []

    code = run_map(
        map_id="murcia",
        name="Murcia",
        latitude=LAT,
        longitude=LON,
        radius_m=400,
        margin_m=30,
        out=str(root),
        execute=lambda argv: seen.append(list(argv)) or 0,
        interactive=False,
        push=lambda _path: 0,
    )
    captured = capsys.readouterr()
    assert code == 0
    assert seen[0][0] == "export"
    assert Path(seen[0][seen[0].index("--out") + 1]).name == "pkg_02"
    assert "--overwrite" not in seen[0]
    assert "export ->" in captured.out
    assert "pkg_02" in captured.out
