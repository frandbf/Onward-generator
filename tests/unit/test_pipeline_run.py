"""The pipeline runner waits for each stage and stops at the first failure."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from onward_generator.cli import build_parser, main
from onward_generator.maps.workspace import create_map, store_catastro
from onward_generator.next_step import (
    cadastre_split_command,
    catastro_command,
    detail_command,
    dress_command,
    export_command,
    fit_command,
    generate_command,
    geo_command,
    heights_command,
    materials_command,
    overgrow_command,
    signs_command,
)
from onward_generator.osm.acquire import osm_target
from onward_generator.pipeline_run import (
    _build_package,
    _push_package,
    _stage_summary,
    _unity_summary,
    finish_device,
    format_duration,
    map_create_argv,
    offer_quest_upload,
    output_ready,
    plan_steps,
    prepare_from,
    print_header,
    print_total,
    print_unity_command,
    run_map,
    run_steps,
    unity_editor_open,
)
from onward_generator.platform import resolve_host
from onward_generator.run_log import Alert, Palette, RunLog, StageReport
from tests.conftest import MURCIA_CENTER_LAT, MURCIA_CENTER_LON


@pytest.fixture(autouse=True)
def _unity_editor_is_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The developer's Editor may be open. These tests do not ask it to close."""
    monkeypatch.setattr("onward_generator.pipeline_run.unity_editor_open", lambda: False)


def test_map_create_keeps_latitude_then_longitude() -> None:
    argv = map_create_argv(
        name="Murcia",
        latitude=MURCIA_CENTER_LAT,
        longitude=MURCIA_CENTER_LON,
        radius_m=400,
        margin_m=30,
        brief="ciudad abandonada",
        ambient_ai=True,
    )
    assert "--id" not in argv and "--out" not in argv
    assert "--brief" in argv and "ciudad abandonada" in argv and "--ai" in argv
    assert argv[argv.index("--lat") + 1] == str(MURCIA_CENTER_LAT)
    assert argv[argv.index("--lon") + 1] == str(MURCIA_CENTER_LON)
    assert argv[argv.index("--lightmap-texels-per-m") + 1] == "10.0"
    assert MURCIA_CENTER_LAT == 37.98407
    assert MURCIA_CENTER_LON == -1.12863


def test_plan_runs_python_stages_and_stops_before_unity(tmp_path) -> None:
    root = tmp_path / "murcia"
    create_map(root, map_id="murcia", name="Murcia", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=400)
    names = [name for name, _argv in plan_steps(root)]
    assert names == [
        "geo", "catastro", "cadastre", "heights", "generate", "detail", "dress", "materials",
        "signs", "overgrow", "fit", "bounds", "layout", "export",
    ]
    joined = " ".join(" ".join(argv) for _name, argv in plan_steps(root))
    assert "unity" not in joined
    assert "push" not in joined
    assert f"--origin {MURCIA_CENTER_LON} {MURCIA_CENTER_LAT}" in joined
    assert "panoramax" not in names


def test_a_map_without_the_panoramax_key_keeps_the_current_graph(tmp_path: Path) -> None:
    root = tmp_path / "murcia"
    create_map(root, map_id="murcia", name="Murcia", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=400)
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
        root, map_id="murcia", name="Murcia", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=400, panoramax=True,
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
    create_map(root, map_id="murcia", name="Murcia", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=400)
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
            print("finished heights")
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
    assert captured.out == (
        " [1/3] geo        0.0 s\n"
        "FAILED heights (exit 1, 0.0 s). Step 2/3. The following steps were not started.\n"
        "Next: run again from heights (step 2/3). The steps before it do not need to run again.\n"
        "heights: the scene is outside the area\n"
        "  finished heights\n"
    )


def test_a_failed_setup_step_has_no_number(capsys: pytest.CaptureFixture[str]) -> None:
    def execute(argv: list[str]) -> int:
        print("osm: Overpass timed out", file=sys.stderr)
        return 3

    log = RunLog(palette=Palette(True))
    code = run_steps([("osm", ["osm"]), ("geo", ["geo"])], execute=execute, log=log)
    assert code == 3
    assert capsys.readouterr().out == (
        "\033[31mFAILED\033[0m osm (exit 3, 0.0 s). The following steps were not started.\n"
        "Next: run again from osm. The steps before it do not need to run again.\n"
        "osm: Overpass timed out\n"
    )
    assert [name for name, _seconds in log.times] == ["osm"]


def test_each_stage_prints_its_duration(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    ticks = iter((0.0, 30.0, 10.0, 12.4, 12.4, 87.4))
    monkeypatch.setattr("onward_generator.pipeline_run.time.perf_counter", lambda: next(ticks))
    log = RunLog()

    code = run_steps(
        [("map", ["map"]), ("geo", ["geo"]), ("heights", ["heights"])],
        execute=lambda _argv: 0,
        log=log,
    )
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == " [1/2] geo        2.4 s\n [2/2] heights   1 min 15 s\n"
    assert log.times == [("map", 30.0), ("geo", pytest.approx(2.4)), ("heights", 75.0)]
    assert format_duration(3661) == "1 h 01 min"


def _report(name: str, _argv: Sequence[str]) -> StageReport:
    if name == "detail":
        return StageReport(
            "21 194 tris",
            details=(("openings", ("window 435", "door 3")),),
            alerts=(Alert("warning", "warning 2 (late 2)", 2),),
        )
    if name == "geo":
        return StageReport("geo done", alerts=(Alert("error", "1 error", 1),))
    return StageReport(f"{name} done")


def test_a_stage_prints_its_report_line_breakdown_and_warnings(capsys: pytest.CaptureFixture[str]) -> None:
    log = RunLog()
    code = run_steps(
        [("geo", ["geo"]), ("detail", ["detail"])],
        execute=lambda _argv: print("raw stage text") or 0,
        log=log,
        summarize_stage=_report,
    )
    assert code == 0
    assert log.warnings == 3
    assert capsys.readouterr().out == (
        " [1/2] geo        0.0 s  geo done\n"
        "         ! 1 error\n"
        " [2/2] detail     0.0 s  21 194 tris\n"
        "         openings   window 435, door 3\n"
        "         ! warning 2 (late 2)\n"
    )


def test_verbose_adds_each_stage_output_unfiltered(capsys: pytest.CaptureFixture[str]) -> None:
    def execute(argv: list[str]) -> int:
        print(f"wrote {argv[0]}/out")
        print("next: something")
        print(f"{argv[0]} note", file=sys.stderr)
        return 0

    code = run_steps(
        [("map", ["map"]), ("geo", ["geo"]), ("signs", ["signs"])],
        execute=execute,
        log=RunLog(verbose=True),
        summarize_stage=_report,
    )
    assert code == 0
    assert capsys.readouterr().out == (
        "wrote map/out\nnext: something\nmap note\n"
        " [1/2] geo        0.0 s  geo done\n"
        "         ! 1 error\n"
        "wrote geo/out\nnext: something\ngeo note\n"
        " [2/2] signs      0.0 s  signs done\n"
        "wrote signs/out\nnext: something\nsigns note\n"
    )


def test_without_verbose_setup_and_raw_output_stay_quiet(capsys: pytest.CaptureFixture[str]) -> None:
    def execute(argv: list[str]) -> int:
        print("wrote x")
        print("warn", file=sys.stderr)
        return 0

    assert run_steps([("map", ["map"]), ("osm", ["osm"])], execute=execute, summarize_stage=_report) == 0
    assert capsys.readouterr().out == ""


def test_total_line_counts_python_setup_and_warnings(capsys: pytest.CaptureFixture[str]) -> None:
    log = RunLog(times=[("map", 50.0), ("osm", 6.4), ("geo", 2.5), ("export", 7.1)], warnings=1)
    print_total(log, failed=False)
    assert capsys.readouterr().out == "\n✓ Python 9.6 s  (map setup 56.4 s)  1 warning\n"
    print_total(RunLog(times=[("geo", 1.0)]), failed=True)
    assert capsys.readouterr().out == "\n✗ Python 1.0 s  0 warnings\n"
    print_total(RunLog(), failed=False)
    assert capsys.readouterr().out == ""


def test_total_line_colours_its_mark_and_non_zero_warnings(capsys: pytest.CaptureFixture[str]) -> None:
    print_total(RunLog(palette=Palette(True), times=[("geo", 1.0)], warnings=3), failed=False)
    assert capsys.readouterr().out == "\n\033[32m✓\033[0m Python 1.0 s  \033[33m3 warnings\033[0m\n"
    print_total(RunLog(palette=Palette(True), times=[("geo", 1.0)]), failed=True)
    assert capsys.readouterr().out == "\n\033[31m✗\033[0m Python 1.0 s  0 warnings\n"


def test_header_names_the_map_and_its_area(capsys: pytest.CaptureFixture[str]) -> None:
    print_header(
        "MadridTexturav6", Path("maps/madridtexturav6"),
        latitude=40.40657, longitude=-3.719187, radius_m=90.0, margin_m=5.0, lightmap_texels_per_m=10.0,
    )
    assert capsys.readouterr().out == (
        "MadridTexturav6  maps/madridtexturav6\n"
        "40.40657, -3.719187  r 90 m  margin 5 m  lightmap 10 t/m\n"
        "\n"
    )


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
        "[export] step 'scene' OK in 4 min 05 s",
        "[export] step 'tool' OK",
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
    assert "  scene OK in 4 min 05 s\n" in summary
    assert "tool OK" not in summary
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


def test_unity_summary_keeps_timings_and_lightmapper_lines() -> None:
    text = "\n".join([
        "[OnwardAdapter] Hunt routes 4: loops on, changed 0.",
        "[OnwardAdapter] Lightmaps: 3, 9000000 texels, at 10 texels/m, at most 2048 px.",
        "[OnwardAdapter] Lightmapper: ProgressiveCPU (requested ProgressiveGPU, fallback yes). Lightmaps: 3 (2048x2048 x3).",
        "[OnwardAdapter] Timings: import 12.3 s, combine 4.0 s, gi_bake 200.1 s, total 240.0 s",
        "[export] lightmapper: Falling back to CPU lightmapper.",
        "[OnwardAdapter] Timings: open 9.0 s, bundle 30.2 s, copy 0.4 s, total 41.0 s",
    ])
    assert _unity_summary(text, failed=False).splitlines() == [
        "  Hunt routes 4: loops on, changed 0.",
        "  Lightmapper: ProgressiveCPU (requested ProgressiveGPU, fallback yes). Lightmaps: 3 (2048x2048 x3).",
        "  Timings: import 12.3 s, combine 4.0 s, gi_bake 200.1 s, total 240.0 s",
        "  lightmapper: Falling back to CPU lightmapper.",
        "  Timings: open 9.0 s, bundle 30.2 s, copy 0.4 s, total 41.0 s",
    ]


def test_plan_steps_argv_equals_command_argv(tmp_path: Path) -> None:
    root = tmp_path / "murcia"
    create_map(root, map_id="murcia", name="Murcia", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=400)
    steps = dict(plan_steps(root))

    def out(name: str) -> Path:
        return Path(steps[name][steps[name].index("--out") + 1])

    geo_scene = out("geo") / "geo_scene.json"
    gml = root / "data" / "raw" / "catastro.gml"
    scene = out("cadastre") / "geo_scene.json"
    heights = out("heights") / "heights.json"
    assert steps["geo"] == list(geo_command(root).argv)
    assert steps["catastro"] == list(catastro_command(geo_scene, gml).argv)
    assert steps["cadastre"] == list(cadastre_split_command(geo_scene, gml).argv)
    assert steps["heights"] == list(heights_command(scene, out("cadastre") / "heights_overrides.catastro.json").argv)
    assert steps["generate"] == list(generate_command(scene, heights).argv)
    assert steps["detail"] == list(detail_command(scene, heights, out("generate")).argv)
    assert steps["dress"] == list(dress_command(out("detail"), scene, heights).argv)
    assert steps["materials"] == list(materials_command(out("dress")).argv)
    assert steps["signs"] == list(signs_command(out("materials")).argv)
    assert steps["overgrow"] == list(overgrow_command(out("signs")).argv)
    assert steps["fit"] == list(fit_command(scene, out("overgrow")).argv)
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
        return 0

    code = run_steps([("signs", ["signs"])], execute=execute)
    assert code == 0
    assert capsys.readouterr().out == " [1/1] signs      0.0 s\n"


def test_run_command_passes_the_centre(monkeypatch: pytest.MonkeyPatch) -> None:
    received: dict[str, object] = {}

    def fake_run_map(**kwargs: object) -> int:
        received.update(kwargs)
        return 0

    monkeypatch.setattr("onward_generator.pipeline_run.run_map", fake_run_map)
    code = main([
        "run", "--name", "Apocalipsis Murciana",
        "--lat", str(MURCIA_CENTER_LAT), "--lon", str(MURCIA_CENTER_LON),
    ])
    assert code == 0
    assert received["latitude"] == MURCIA_CENTER_LAT
    assert received["longitude"] == MURCIA_CENTER_LON
    assert received["map_id"] == "apocalipsis_murciana"
    assert received["out"] == "maps/apocalipsis_murciana"
    assert received["name"] == "Apocalipsis Murciana"


def test_rerun_after_a_failed_osm_fetch_continues_at_osm(tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "murciav2"
    create_map(root, map_id="murcia", name="Murcia", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=250, margin_m=30)
    seen: list[str] = []

    def execute(argv: list[str]) -> int:
        seen.append(argv[0])
        return 0

    code = run_map(
        map_id="murcia",
        name="Murcia",
        latitude=MURCIA_CENTER_LAT,
        longitude=MURCIA_CENTER_LON,
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
    create_map(root, map_id="murcia", name="Murcia", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=400)
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
        latitude=MURCIA_CENTER_LAT,
        longitude=MURCIA_CENTER_LON,
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
    assert seen[1][:2] == ["cadastre", "download"]
    assert seen[2][:2] == ["cadastre", "split"]
    assert seen[3][0] == "heights"


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
            if name in ("geo", "cadastre"):
                destination.mkdir(parents=True, exist_ok=True)
                (destination / "geo_scene.json").write_text("{}", encoding="utf-8")
            elif name == "catastro":
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text("<gml/>", encoding="utf-8")
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
    create_map(root, map_id="murcia", name="Murcia", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=400)
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
        latitude=MURCIA_CENTER_LAT,
        longitude=MURCIA_CENTER_LON,
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
    assert "\n→ Unity: already has the Quest build.\n" in captured.out
    assert "¿Subir al Quest?" in captured.out
    assert pushed == [str(package)]
    assert "OK quest" in captured.out
    assert "run: time" not in captured.out


def _write_launcher(package: Path) -> None:
    launcher = package / "build" / "launcher"
    launcher.mkdir(parents=True, exist_ok=True)
    (launcher / "unity-export.local.content3").write_bytes(b"content")
    (launcher / "unity-export.local.info3").write_bytes(b"info")


def test_unity_builds_the_quest_files_before_asking(tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _ready_map(tmp_path)
    built: list[list[str]] = []
    pushed: list[str] = []

    def build(path: Path, steps: list[str]) -> int:
        built.append(list(steps))
        _write_launcher(path)
        return 0

    code = run_map(
        map_id="murcia",
        name="Murcia",
        latitude=MURCIA_CENTER_LAT,
        longitude=MURCIA_CENTER_LON,
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
    assert "\nrun: every Python stage is already present.\n→ Unity: tool, scene, launcher\nOK unity" in captured.out
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
        latitude=MURCIA_CENTER_LAT,
        longitude=MURCIA_CENTER_LON,
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
        latitude=MURCIA_CENTER_LAT,
        longitude=MURCIA_CENTER_LON,
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
        latitude=MURCIA_CENTER_LAT,
        longitude=MURCIA_CENTER_LON,
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
    create_map(root, map_id="murcia", name="Murcia", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=400)
    asked = {"n": 0}

    def ask() -> str:
        asked["n"] += 1
        return "s"

    code = run_map(
        map_id="murcia",
        name="Murcia",
        latitude=MURCIA_CENTER_LAT,
        longitude=MURCIA_CENTER_LON,
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


def _capture_run(seen: dict[str, object]):
    def fake_run(args, *, env, check, capture_output, text):
        seen["args"] = list(args)
        seen["env"] = dict(env)
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    return fake_run


def test_build_package_passes_host_env(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    seen: dict[str, object] = {}
    monkeypatch.setattr("onward_generator.pipeline_run.subprocess.run", _capture_run(seen))
    monkeypatch.setenv("ONWARD_PLATFORM", "macos")
    host = resolve_host("linux", env={"HOME": "/home/tester"})
    assert _build_package(tmp_path, ["tool", "scene"], host=host) == 0
    env = seen["env"]
    assert isinstance(env, dict)
    assert env["ONWARD_PLATFORM"] == "linux"
    assert env["UNITY"] == "/home/tester/Unity/Hub/Editor/2021.3.23f1/Editor/Unity"
    assert env["LOG_ROOT"] == "/home/tester/.local/state/onward-generator"
    assert seen["args"][-3:] == [str(tmp_path), "tool", "scene"]


def test_push_keeps_confirm_and_host(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    seen: dict[str, object] = {}
    monkeypatch.setattr("onward_generator.pipeline_run.subprocess.run", _capture_run(seen))
    monkeypatch.setenv("ONWARD_PLATFORM", "macos")
    monkeypatch.delenv("UNITY", raising=False)
    assert _push_package(tmp_path) == 0
    env = seen["env"]
    assert isinstance(env, dict)
    assert env["CONFIRM_PUSH"] == "yes"
    assert env["ONWARD_PLATFORM"] == "macos"
    assert env["UNITY"] == "/Applications/Unity/Hub/Editor/2021.3.23f1/Unity.app/Contents/MacOS/Unity"


def test_run_map_passes_an_explicit_host_to_build_and_push(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = _ready_map(tmp_path)
    package = root / "data" / "processed" / "export" / "pkg_01"
    calls: list[tuple[str, dict[str, str]]] = []

    def fake_run(args, *, env, check, capture_output, text):
        calls.append((args[-1], dict(env)))
        if args[-1] == "launcher":
            _write_launcher(package)
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("onward_generator.pipeline_run.subprocess.run", fake_run)
    monkeypatch.setenv("ONWARD_PLATFORM", "macos")
    monkeypatch.delenv("UNITY", raising=False)
    host = resolve_host("linux", env={"HOME": "/home/tester"})
    code = run_map(
        map_id="murcia",
        name="Murcia",
        latitude=MURCIA_CENTER_LAT,
        longitude=MURCIA_CENTER_LON,
        radius_m=400,
        margin_m=30,
        out=str(root),
        execute=lambda _argv: 1,
        interactive=True,
        ask=lambda: "s",
        host=host,
    )
    assert code == 0
    assert [step for step, _env in calls] == ["launcher", "push"]
    for _step, env in calls:
        assert env["ONWARD_PLATFORM"] == "linux"
        assert env["UNITY"] == str(host.unity_editor)
    assert calls[1][1]["CONFIRM_PUSH"] == "yes"


def test_an_unknown_platform_does_not_start_unity(monkeypatch: pytest.MonkeyPatch, tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("onward_generator.pipeline_run.subprocess.run", lambda *a, **k: pytest.fail("Unity started"))
    monkeypatch.setenv("ONWARD_PLATFORM", "windows")
    assert _build_package(tmp_path, ["tool"]) == 2
    assert "unknown platform" in capsys.readouterr().out


def test_signs_keeps_the_map_scene_path_before_geo_exists(tmp_path: Path) -> None:
    root = tmp_path / "murcia"
    create_map(root, map_id="murcia", name="Murcia", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=400)
    signs = dict(plan_steps(root))["signs"]
    scene = signs[signs.index("--scene") + 1]
    assert scene.endswith("data/processed/geo/geo_scene.json")
    bounds = dict(plan_steps(root))["bounds"]
    assert Path(bounds[bounds.index("--out") + 1]).name == "scene_playable"


def test_bounds_stays_on_scene_playable_when_later_copies_exist(tmp_path: Path) -> None:
    root = tmp_path / "murcia"
    create_map(root, map_id="murcia", name="Murcia", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=400)
    for name in ("scene_playable", "scene_playable_compact", "scene_playable_next"):
        directory = root / "data" / "processed" / name
        directory.mkdir(parents=True)
        (directory / "scene_visual.glb").write_bytes(b"glb")
    bounds = dict(plan_steps(root))["bounds"]
    assert Path(bounds[bounds.index("--out") + 1]).name == "scene_playable"


def test_an_open_editor_asks_before_any_stage_starts(tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "murcia"
    create_map(root, map_id="murcia", name="Murcia", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=400)
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
        latitude=MURCIA_CENTER_LAT,
        longitude=MURCIA_CENTER_LON,
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
    assert captured.out.index("El pipeline empieza.") < captured.out.index("FAILED osm")


def test_declining_to_close_unity_does_not_start(tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "murcia"
    create_map(root, map_id="murcia", name="Murcia", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=400)
    seen: list[str] = []

    code = run_map(
        map_id="murcia",
        name="Murcia",
        latitude=MURCIA_CENTER_LAT,
        longitude=MURCIA_CENTER_LON,
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
    assert "Murcia  " not in captured.out


def test_a_closed_editor_starts_without_asking(tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "murcia"
    create_map(root, map_id="murcia", name="Murcia", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=400)
    asked = {"n": 0}

    code = run_map(
        map_id="murcia",
        name="Murcia",
        latitude=MURCIA_CENTER_LAT,
        longitude=MURCIA_CENTER_LON,
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
    create_map(root, map_id="murcia", name="Murcia", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=400)
    seen: list[str] = []

    code = run_map(
        map_id="murcia",
        name="Murcia",
        latitude=MURCIA_CENTER_LAT,
        longitude=MURCIA_CENTER_LON,
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
        latitude=MURCIA_CENTER_LAT,
        longitude=MURCIA_CENTER_LON,
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
        latitude=MURCIA_CENTER_LAT,
        longitude=MURCIA_CENTER_LON,
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
    assert " [1/1] export     0.0 s" in captured.out


def test_no_unity_skips_the_editor_check_and_prints_the_host_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _ready_map(tmp_path)
    built: list[str] = []

    def export(argv: list[str]) -> int:
        package = Path(argv[argv.index("--out") + 1])
        (package / "build").mkdir(parents=True)
        (package / "onward_export.json").write_text("{}", encoding="utf-8")
        (package / "build" / "scene_report.json").write_text("{}", encoding="utf-8")
        return 0

    code = run_map(
        map_id="murcia",
        name="Murcia",
        latitude=MURCIA_CENTER_LAT,
        longitude=MURCIA_CENTER_LON,
        radius_m=400,
        margin_m=30,
        out=str(root),
        start="export",
        execute=export,
        interactive=True,
        ask=lambda: "s",
        unity_open=lambda: True,
        build=lambda path, _steps: built.append(str(path)) or 0,
        no_unity=True,
    )
    out = capsys.readouterr().out
    assert code == 0
    assert built == []
    assert "Unity tiene abierto" not in out
    assert "¿Subir al Quest?" not in out
    assert "\n✓ Python 0.0 s  0 warnings\n" in out
    assert out.endswith("→ Unity: tool, launcher. On the host, run:\n  task unity -- " + str(root / "data" / "processed" / "export" / "pkg_02") + "\n")


def test_unity_command_for_a_package_already_built_or_missing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    package = tmp_path / "pkg_01"
    assert print_unity_command(package) == 0
    assert capsys.readouterr().out == "→ Unity: skipped. The export package is not on disk.\n"
    package.mkdir()
    (package / "onward_export.json").write_text("{}", encoding="utf-8")
    assert print_unity_command(package) == 0
    assert capsys.readouterr().out == f"→ Unity: tool, scene, launcher. On the host, run:\n  task unity -- {package}\n"
    _write_launcher(package)
    assert print_unity_command(package) == 0
    assert capsys.readouterr().out == f"→ Unity: the Quest build is already in {package}\n"


def test_a_new_map_prints_the_header_and_folds_setup_into_the_total(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)

    def execute(argv: list[str]) -> int:
        if argv[:2] == ["map", "create"]:
            create_map(Path("maps/murcia"), map_id="murcia", name="Murcia", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=90)
            return 0
        return 2

    code = run_map(
        map_id="murcia",
        name="Murcia",
        latitude=MURCIA_CENTER_LAT,
        longitude=MURCIA_CENTER_LON,
        radius_m=90,
        margin_m=5,
        lightmap_texels_per_m=12.5,
        out="maps/murcia",
        execute=execute,
        interactive=False,
        palette=Palette(True),
    )
    out = capsys.readouterr().out
    assert code == 2
    assert out.startswith(f"Murcia  maps/murcia\n{MURCIA_CENTER_LAT}, {MURCIA_CENTER_LON}  r 90 m  margin 5 m  lightmap 12.5 t/m\n\n\033[31mFAILED\033[0m geo")
    assert "(map setup 0.0 s)" in out
    assert "\033[31m✗\033[0m Python" in out


def test_a_failed_map_setup_ends_with_the_total(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.chdir(tmp_path)
    code = run_map(
        map_id="murcia",
        name="Murcia",
        latitude=MURCIA_CENTER_LAT,
        longitude=MURCIA_CENTER_LON,
        radius_m=90,
        margin_m=5,
        out="maps/murcia",
        execute=lambda _argv: 4,
        interactive=False,
        palette=Palette(),
    )
    out = capsys.readouterr().out
    assert code == 4
    assert "FAILED map (exit 4, 0.0 s). The following steps were not started." in out
    assert out.endswith("\n✗ Python 0.0 s  (map setup 0.0 s)  0 warnings\n")


def test_verbose_reaches_the_stages(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _ready_map(tmp_path)
    code = run_map(
        map_id="murcia",
        name="Murcia",
        latitude=MURCIA_CENTER_LAT,
        longitude=MURCIA_CENTER_LON,
        radius_m=400,
        margin_m=30,
        out=str(root),
        start="layout",
        execute=lambda argv: print(f"full {argv[0]} output") or 0,
        interactive=False,
        no_unity=True,
        verbose=True,
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "full perf output" in out
    assert "full export output" in out


def test_run_passes_verbose_and_no_unity(monkeypatch: pytest.MonkeyPatch) -> None:
    received: dict[str, object] = {}
    monkeypatch.setattr("onward_generator.pipeline_run.run_map", lambda **kwargs: received.update(kwargs) or 0)
    argv = ["run", "--name", "Murcia", "--lat", str(MURCIA_CENTER_LAT), "--lon", str(MURCIA_CENTER_LON)]
    assert main(argv) == 0
    assert received["verbose"] is False and received["no_unity"] is False
    assert main([*argv, "--verbose", "--no-unity"]) == 0
    assert received["verbose"] is True and received["no_unity"] is True


def test_finish_device_says_what_unity_does(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    package = tmp_path / "pkg_01"
    kwargs = {"interactive": False, "ask": None, "push": lambda _path: 0}
    assert finish_device(package, build=lambda _path, _steps: 0, **kwargs) == 0
    assert capsys.readouterr().out == "→ Unity: skipped. The export package is not on disk.\n"
    package.mkdir()
    (package / "onward_export.json").write_text("{}", encoding="utf-8")
    ticks = iter((10.0, 12.5))
    monkeypatch.setattr("onward_generator.pipeline_run.time.perf_counter", lambda: next(ticks))

    def build(path: Path, _steps: list[str]) -> int:
        _write_launcher(path)
        return 0

    assert finish_device(package, build=build, **kwargs) == 0
    assert capsys.readouterr().out == (
        "→ Unity: tool, scene, launcher\nOK unity  2.5 s\n"
        "run: Quest upload skipped. Run this in a terminal to answer the question.\n"
    )
    assert finish_device(package, build=build, **kwargs) == 0
    assert capsys.readouterr().out.startswith("→ Unity: already has the Quest build.\n")


def test_quest_upload_reports_its_duration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    package = tmp_path / "pkg_01"
    _write_launcher(package)
    ticks = iter((5.0, 8.25))
    monkeypatch.setattr("onward_generator.pipeline_run.time.perf_counter", lambda: next(ticks))
    assert offer_quest_upload(package, interactive=True, ask=lambda: "s", push=lambda _path: 0) == 0
    assert capsys.readouterr().out.endswith("run: quest\nOK quest  3.2 s\n")


def test_run_map_colours_a_terminal_and_stays_quiet_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    root = _ready_map(tmp_path)
    monkeypatch.setattr("onward_generator.pipeline_run.use_color", lambda _stream: True)
    code = run_map(
        map_id="murcia",
        name="Murcia",
        latitude=MURCIA_CENTER_LAT,
        longitude=MURCIA_CENTER_LON,
        radius_m=400,
        margin_m=30,
        out=str(root),
        start="layout",
        execute=lambda argv: print(f"full {argv[0]} output") or 0,
        interactive=False,
        no_unity=True,
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "full perf output" not in out
    assert "\033[32m✓\033[0m Python" in out


def test_run_help_describes_the_new_flags() -> None:
    parser = build_parser()
    run = next(
        choice
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
        for name, choice in action.choices.items()
        if name == "run"
    )
    helps = {action.dest: action.help for action in run._actions}
    assert helps["verbose"] == "also print each stage's full output"
    assert helps["no_unity"] == "stop after export and print the command for the Unity steps (task map uses this)"


# --- Catastro split (ADR-016). ---


def _murcia(tmp_path: Path) -> Path:
    root = tmp_path / "murcia"
    create_map(root, map_id="murcia", name="Murcia", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=400)
    return root


def test_catastro_off_keeps_the_osm_scene_for_heights(tmp_path: Path) -> None:
    root = _murcia(tmp_path)
    store_catastro(root, False)
    steps = dict(plan_steps(root))
    assert "catastro" not in steps and "cadastre" not in steps
    scene = Path(steps["geo"][steps["geo"].index("--out") + 1]) / "geo_scene.json"
    assert steps["heights"] == list(heights_command(scene).argv)
    assert "--overrides" not in steps["heights"]


def test_catastro_steps_read_the_geo_scene_and_write_the_split_scene(tmp_path: Path) -> None:
    root = _murcia(tmp_path).resolve()
    steps = dict(plan_steps(root))
    processed = root / "data" / "processed"
    assert steps["catastro"][-2:] == ["--out", str(root / "data" / "raw" / "catastro.gml")]
    assert steps["cadastre"][steps["cadastre"].index("--out") + 1] == str(processed / "cadastre")
    for name in ("generate", "detail", "fit"):
        assert steps[name][steps[name].index("--scene") + 1] == str(processed / "cadastre" / "geo_scene.json")


def _touch(path: Path, when: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    os.utime(path, (when, when))


def test_catastro_and_cadastre_are_stale_after_their_input(tmp_path: Path) -> None:
    root = _murcia(tmp_path)
    processed = root / "data" / "processed"
    now = (root / "map.json").stat().st_mtime
    _touch(processed / "geo" / "geo_scene.json", now)
    _touch(root / "data" / "raw" / "catastro.gml", now + 1)
    _touch(processed / "cadastre" / "geo_scene.json", now + 2)
    _touch(processed / "heights" / "heights.json", now + 3)
    assert all(output_ready(name, [], root) for name in ("catastro", "cadastre", "heights"))
    _touch(processed / "cadastre" / "geo_scene.json", now + 4)
    assert not output_ready("heights", [], root)
    _touch(root / "data" / "raw" / "catastro.gml", now + 5)
    assert not output_ready("cadastre", [], root)
    _touch(processed / "geo" / "geo_scene.json", now + 6)
    assert not output_ready("catastro", [], root)
    store_catastro(root, False)
    assert output_ready("heights", [], root) is False  # heights now follows geo, which is newer
    _touch(processed / "heights" / "heights.json", now + 7)
    assert output_ready("heights", [], root)


def test_a_catastro_sidecar_alone_is_downloaded_again_with_overwrite(tmp_path: Path) -> None:
    root = _murcia(tmp_path)
    steps = plan_steps(root)
    gml = root / "data" / "raw" / "catastro.gml"
    assert "--overwrite" not in dict(prepare_from(steps, "catastro", root))["catastro"]
    gml.parent.mkdir(parents=True, exist_ok=True)
    gml.with_name("catastro.gml.source.json").write_text("{}", encoding="utf-8")
    assert "--overwrite" in dict(prepare_from(steps, "catastro", root))["catastro"]


def test_run_stores_the_catastro_switch(tmp_path: Path) -> None:
    root = _ready_map(tmp_path)

    def run(catastro: bool | None) -> None:
        code = run_map(
            map_id="murcia", name="Murcia", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON,
            radius_m=400, margin_m=30, out=str(root), catastro=catastro,
            execute=lambda argv: 0, interactive=False, no_unity=True,
        )
        assert code == 0

    run(None)
    assert "catastro" not in json.loads((root / "map.json").read_text(encoding="utf-8"))
    run(False)
    assert json.loads((root / "map.json").read_text(encoding="utf-8"))["catastro"] is False
    run(True)
    assert json.loads((root / "map.json").read_text(encoding="utf-8"))["catastro"] is True


@pytest.mark.parametrize(
    ("catastro", "steps"),
    [(False, ["map", "geo", "heights"]), (True, ["map", "geo", "cadastre", "cadastre", "heights"])],
)
def test_a_new_map_stores_the_catastro_switch_before_planning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, catastro: bool, steps: list[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    seen: list[list[str]] = []

    def execute(argv: list[str]) -> int:
        seen.append(list(argv))
        if argv[:2] == ["map", "create"]:
            create_map(Path("maps/murcia"), map_id="murcia", name="Murcia", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=90)
            return 0
        return 2 if argv[0] == "heights" else 0

    code = run_map(
        map_id="murcia", name="Murcia", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON,
        radius_m=90, margin_m=5, out="maps/murcia", catastro=catastro, execute=execute, interactive=False,
        no_unity=True,
    )
    assert code == 2
    assert json.loads(Path("maps/murcia/map.json").read_text(encoding="utf-8"))["catastro"] is catastro
    assert [argv[0] for argv in seen] == steps


def test_heights_without_catastro_ignores_a_newer_split_scene(tmp_path: Path) -> None:
    root = _murcia(tmp_path)
    processed = root / "data" / "processed"
    now = (root / "map.json").stat().st_mtime
    _touch(processed / "geo" / "geo_scene.json", now)
    _touch(processed / "heights" / "heights.json", now + 1)
    _touch(processed / "cadastre" / "geo_scene.json", now + 2)
    assert not output_ready("heights", [], root)
    store_catastro(root, False)
    assert output_ready("heights", [], root)


def test_run_passes_the_catastro_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    received: dict[str, object] = {}
    monkeypatch.setattr("onward_generator.pipeline_run.run_map", lambda **kwargs: received.update(kwargs) or 0)
    argv = ["run", "--name", "Murcia", "--lat", str(MURCIA_CENTER_LAT), "--lon", str(MURCIA_CENTER_LON)]
    assert main(argv) == 0
    assert received["catastro"] is None
    assert main([*argv, "--no-catastro"]) == 0
    assert received["catastro"] is False
    assert main([*argv, "--catastro"]) == 0
    assert received["catastro"] is True


def test_run_help_describes_the_catastro_switch() -> None:
    parser = build_parser()
    subparsers = next(action for action in parser._actions if isinstance(action, argparse._SubParsersAction))
    run = subparsers.choices["run"]
    helps = {action.dest: action.help for action in run._actions}
    assert helps["catastro"] == (
        "split OSM blocks into Catastro constructions (default on in Spain outside Navarre and the Basque Country); "
        "--no-catastro turns it off for this map"
    )
    listed = {choice.dest: choice.help for choice in subparsers._choices_actions}
    assert listed["cadastre"] == "Catastro INSPIRE floors: download | match | split (see: cadastre --help)"


def test_cadastre_help_lists_split() -> None:
    from onward_generator.cadastre.__main__ import build_parser as cadastre_parser

    subparsers = next(action for action in cadastre_parser()._actions if isinstance(action, argparse._SubParsersAction))
    listed = {choice.dest: choice.help for choice in subparsers._choices_actions}
    assert listed["split"] == "split OSM footprints by Catastro parts; write a new geo scene and overrides (offline)"
