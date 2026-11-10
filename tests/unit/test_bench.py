"""tools/bench/bench.py: reference maps, report building, normalisation and comparison (plan 2026-11-07, A1)."""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import re
import shutil
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from PIL import Image

from onward_generator.materials.trim_style import load_style_table, select_style
from onward_generator.sources.registry import choices
from tests.conftest import MURCIA_CENTER_LAT, MURCIA_CENTER_LON

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "bench"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("onward_bench", ROOT / "tools" / "bench" / "bench.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolve the module through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bench = _load()


def _manifest() -> dict[str, Any]:
    return json.loads((FIXTURES / "onward_export.json").read_text(encoding="utf-8"))


def _package(root: Path, manifest: Any | None = None, *, thumbnail: tuple[int, int, int] | None = None) -> Path:
    package = root / "pkg_01"
    package.mkdir(parents=True)
    text = json.dumps(_manifest() if manifest is None else manifest)
    (package / "onward_export.json").write_text(text, encoding="utf-8")
    if thumbnail is not None:
        Image.new("RGB", (bench.THUMBNAIL_WIDTH, bench.THUMBNAIL_HEIGHT), thumbnail).save(package / "thumbnail.png")
    return package


def _report(**changes: Any) -> dict[str, Any]:
    entry = {"reference": bench.REFERENCE_MAPS[0].parameters(), "inputs_sha256": "ab" * 32,
             "generator_version": "0.1.0", "tiers_source": "manifest",
             "visual_triangles": {"total": 1500, "core": 1200, "scenic": 300, "shared": 0},
             "draws": {"total": 50, "core": 40, "scenic": 12, "shared": 0},
             "view_draws": {"views": 64, "median": 33, "p90": 41, "max": 47},
             "lightmap": {"pages_at_least": 3, "texels": 9000000}}
    timing = {"stages": [{"stage": "geo", "seconds": 1.2}], "setup_s": 4.0, "python_s": 20.5, "unity": {"status": "missing"}}
    host = {"host_os": "Darwin 25.6.0 arm64", "host_gpu": "Apple M5 Pro", "machine": "aarch64", "in_container": True}
    report = bench.build_report({"bench_murcia": entry}, {"bench_murcia": timing}, host)
    for path, value in changes.items():
        *parents, last = path.split("__")
        block = report
        for key in parents:
            block = block[key]
        block[last] = value
    return report


# ---------------------------------------------------------------------------------------------------- reference maps


def test_reference_maps_cover_plan() -> None:
    maps = {ref.id: ref for ref in bench.REFERENCE_MAPS}
    assert list(maps) == ["bench_murcia", "bench_madrid", "bench_paris", "bench_krakow"]
    murcia, madrid, paris, krakow = maps.values()
    assert (murcia.latitude, murcia.longitude, murcia.radius_m) == (MURCIA_CENTER_LAT, MURCIA_CENTER_LON, 150.0)
    assert madrid.radius_m == 300.0 and dict(madrid.sources) == {"es.catastro": True}
    assert paris.radius_m == 50.0 and paris.lightmap_texels_per_m == 40.0 and dict(paris.sources) == {"fr.lidar_hd": True}
    assert krakow.radius_m == 150.0
    assert bench.SEED == 48152
    for ref in maps.values():
        assert bench._RUN_ID.fullmatch(ref.id) and 50.0 <= ref.radius_m <= 1500.0
        running = {c.plugin.id for c in choices({"center": {"latitude": ref.latitude, "longitude": ref.longitude}}) if c.runs}
        assert {plugin for plugin, on in ref.sources if on} <= running
    krakow_running = {c.plugin.id for c in choices({"center": {"latitude": krakow.latitude, "longitude": krakow.longitude}}) if c.runs}
    assert "es.catastro" not in krakow_running
    table = load_style_table()
    style = select_style(table, krakow.latitude, krakow.longitude)
    assert style == "central_european"
    assert table.styles[style].atlas is None


def test_unknown_reference_map_is_refused() -> None:
    with pytest.raises(bench.BenchError, match="unknown reference map 'bench_x'"):
        bench.reference("bench_x")


# ---------------------------------------------------------------------------------------------------- metrics


def test_map_metrics_reads_manifest(tmp_path: Path) -> None:
    metrics = bench.map_metrics(_package(tmp_path))
    assert metrics == {
        "generator_version": "0.1.0",
        "visual_triangles": {"total": 1500, "core": 1200, "scenic": 300, "shared": 0},
        "draws": {"total": 50, "core": 40, "scenic": 12, "shared": 0},
        "tiers_source": "manifest",
        "view_draws": {"views": 64, "median": 33, "p90": 41, "max": 47},
        "lightmap": {"pages_at_least": 3, "texels": 9000000},
    }
    text = json.dumps(metrics)
    assert "created_at" not in text and "package_id" not in text and "/work" not in text


def test_map_metrics_missing_package(tmp_path: Path) -> None:
    with pytest.raises(bench.BenchError, match=r"onward_export.json not found"):
        bench.map_metrics(tmp_path / "pkg_01")


def _without(key: str) -> dict[str, Any]:
    manifest = _manifest()
    del manifest[key]
    return manifest


def _changed(path: tuple[str, ...], value: Any) -> dict[str, Any]:
    manifest = _manifest()
    block = manifest
    for key in path[:-1]:
        block = block[key]
    block[path[-1]] = value
    return manifest


@pytest.mark.parametrize(
    ("manifest", "message"),
    [
        ([1, 2], "is not a JSON object"),
        (_without("tiers"), "no tiers"),
        (_without("view_draws"), "no view_draws"),
        (_without("lightmap_estimate"), "no lightmap_estimate"),
        (_changed(("lightmap_estimate", "pages_at_least"), 2.5), "lightmap_estimate: pages_at_least must be an integer, got 2.5"),
        (_changed(("lightmap_estimate", "pages_at_least"), True), "pages_at_least must be an integer, got True"),
        (_changed(("tiers", "scenic"), None), r"tiers: scenic must be an object, got None"),
        (_changed(("tiers", "core", "visual_triangles"), "12"), r"tiers.core: visual_triangles must be an integer"),
        (_changed(("view_draws",), [1]), "view_draws must be an object or null"),
        (_changed(("view_draws", "p90"), None), r"view_draws: p90 must be an integer, got None"),
        (_changed(("tiers",), "core"), "tiers must be an object or null"),
    ],
)
def test_map_metrics_malformed(tmp_path: Path, manifest: Any, message: str) -> None:
    package = _package(tmp_path, manifest)
    with pytest.raises(bench.BenchError, match=message) as caught:
        bench.map_metrics(package)
    assert str(package / "onward_export.json") in str(caught.value)


def test_map_metrics_truncated_file(tmp_path: Path) -> None:
    package = tmp_path / "pkg_01"
    package.mkdir()
    shutil.copy(FIXTURES / "onward_export.truncated.json", package / "onward_export.json")
    with pytest.raises(bench.BenchError, match="not valid JSON"):
        bench.map_metrics(package)


def test_map_without_core_and_without_visual_fails(tmp_path: Path) -> None:
    package = _package(tmp_path, _changed(("tiers",), None))
    with pytest.raises(bench.BenchError, match="tiers is null and .*scene_visual.glb is missing"):
        bench.map_metrics(package)


def test_view_draws_null_is_reported_as_missing_not_zero(tmp_path: Path) -> None:
    metrics = bench.map_metrics(_package(tmp_path, _changed(("view_draws",), None)))
    assert metrics["view_draws"] == {"views": None, "median": None, "p90": None, "max": None}


# ---------------------------------------------------------------------------------------------------- report


def test_report_is_byte_identical_apart_from_timings(tmp_path: Path) -> None:
    first = _report()
    second = _report(timings__maps={"bench_murcia": {"stages": [{"stage": "geo", "seconds": 9.9}], "setup_s": 1.0,
                                                      "python_s": 30.0, "unity": {"status": "missing"}}})
    assert bench.report_text(first) != bench.report_text(second)
    assert bench.report_text(bench.strip_timings(first)) == bench.report_text(bench.strip_timings(second))
    shuffled = json.loads(json.dumps(first))
    shuffled = dict(reversed(list(shuffled.items())))
    shuffled["maps"]["bench_murcia"] = dict(reversed(list(shuffled["maps"]["bench_murcia"].items())))
    assert bench.report_text(shuffled) == bench.report_text(first)
    bench.write_report(first, tmp_path / "a.json")
    text = (tmp_path / "a.json").read_text(encoding="utf-8")
    assert text.endswith("}\n") and not text.endswith("\n\n")
    assert '"total": 1500\n' in text and '"seed": 48152,' in text and '1500.0' not in text
    assert bench.split_times([("geo", 1.04), ("heights", 2.06)], [])["stages"] == [
        {"stage": "geo", "seconds": 1.0}, {"stage": "heights", "seconds": 2.1}]


def test_report_has_no_nonreproducible_fields(tmp_path: Path) -> None:
    package = _package(tmp_path / "run")
    inputs = tmp_path / "inputs"
    (inputs / "data" / "raw").mkdir(parents=True)
    (inputs / "map.json").write_text("{}", encoding="utf-8")
    entry = bench.map_entry(bench.REFERENCE_MAPS[0], package, inputs)
    report = bench.build_report({"bench_murcia": entry}, {"bench_murcia": {"setup_s": 1.0}}, bench.host_info(None, None))
    body = bench.strip_timings(report)
    text = json.dumps(body)
    for word in ("created_at", "package_id", "hostname", "node"):
        assert word not in text

    def strings(value: Any) -> list[str]:
        if isinstance(value, dict):
            return [s for v in value.values() for s in strings(v)]
        if isinstance(value, list):
            return [s for v in value for s in strings(v)]
        return [value] if isinstance(value, str) else []

    assert [s for s in strings(body) if s.startswith("/") or str(tmp_path) in s] == []
    assert set(report) == {"schema", "schema_version", "seed", "maps", "contact_sheet", "timings"}
    assert report["schema"] == "onward-bench" and report["schema_version"] == 1


def test_check_detects_metric_change(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    bench.write_report(_report(), a)
    changed = _report()
    changed["maps"]["bench_murcia"]["draws"]["core"] = 41
    changed["timings"]["maps"]["bench_murcia"]["python_s"] = 99.0
    bench.write_report(changed, b)
    assert bench.main(["check", str(a), str(b)]) == 1
    assert capsys.readouterr().out == "bench: the reports differ outside timings at maps.bench_murcia.draws.core\n"
    bench.write_report(_report(timings__host={"host_os": "other"}), b)
    assert bench.main(["check", str(a), str(b)]) == 0
    assert "same apart from timings" in capsys.readouterr().out


def test_differences_names_added_keys_types_and_list_items() -> None:
    assert bench.differences({"a": 1}, {"a": 1.0}) == ["a"]
    assert bench.differences({"a": [1, 2]}, {"a": [1, 3]}) == ["a[1]"]
    assert bench.differences({"a": [1]}, {"a": [1, 2]}) == ["a"]
    assert bench.differences({"a": 1}, {"b": 1}) == ["a", "b"]
    assert bench.differences(1, 2) == ["$"]
    assert bench.differences({"a": {"b": None}}, {"a": {"b": None}}) == []


def test_setup_and_python_split() -> None:
    times = [("map", 5.0), ("osm", 50.0), ("geo", 1.0), ("overture", 20.0), ("heights", 2.0)]
    split = bench.split_times(times, ["osm", "overture"])
    assert split["setup_s"] == 75.0 and split["python_s"] == 3.0
    assert [row["stage"] for row in split["stages"]] == ["map", "osm", "geo", "overture", "heights"]
    assert bench.split_times([], []) == {"stages": [], "setup_s": 0.0, "python_s": 0.0}
    assert type(bench.split_times([], [])["setup_s"]) is float


def test_unity_missing_not_zero(tmp_path: Path) -> None:
    package = _package(tmp_path)
    assert bench.unity_timings(package) == {"status": "missing"}
    build = package / "build"
    build.mkdir()
    shutil.copy(FIXTURES / "scene_report.json", build / "scene_report.json")
    shutil.copy(FIXTURES / "launcher_report.json", build / "launcher_report.json")
    result = bench.unity_timings(package)
    assert result == {
        "status": "measured",
        "seconds": {"scene.scene": 12.0, "scene.bake": 61.5, "scene.import": 4.0, "scene.total": 73.5,
                    "launcher.open": 9.0, "launcher.bundle": 30.2, "launcher.total": 41.0},
        "profile": "draft", "lightmapper": "ProgressiveGPU", "fallback": False,
    }
    (build / "scene_report.json").write_text("[1]", encoding="utf-8")
    with pytest.raises(bench.BenchError, match="is not a JSON object"):
        bench.unity_timings(package)
    (build / "scene_report.json").write_text("{", encoding="utf-8")
    with pytest.raises(bench.BenchError, match="not valid JSON"):
        bench.unity_timings(package)
    (build / "scene_report.json").write_text("{}", encoding="utf-8")
    (build / "launcher_report.json").write_text("[]", encoding="utf-8")
    with pytest.raises(bench.BenchError, match="launcher_report.json is not a JSON object"):
        bench.unity_timings(package)


def test_inputs_sha256_stable_and_sensitive(tmp_path: Path) -> None:
    root = tmp_path / "m"
    (root / "data" / "raw").mkdir(parents=True)
    (root / "config").mkdir()
    (root / "map.json").write_text("{}", encoding="utf-8")
    (root / "config" / "project.json").write_text("{}", encoding="utf-8")
    (root / "data" / "raw" / "m.osm").write_bytes(b"<osm/>")
    first = bench.inputs_sha256(root)
    assert bench.inputs_sha256(root) == first and len(first) == 64
    (root / "data" / "processed" / "geo").mkdir(parents=True)
    (root / "data" / "processed" / "geo" / "geo_scene.json").write_text("{}", encoding="utf-8")
    assert bench.inputs_sha256(root) == first
    (root / "data" / "raw" / "m.osm").write_bytes(b"<osm />")
    changed = bench.inputs_sha256(root)
    assert changed != first
    (root / "data" / "raw" / "m.osm").rename(root / "data" / "raw" / "n.osm")
    assert bench.inputs_sha256(root) not in (first, changed)


# ---------------------------------------------------------------------------------------------------- comparison


def test_compare_table() -> None:
    old = _report()
    new = _report()
    new["maps"]["bench_murcia"]["visual_triangles"]["total"] = 1512
    new["maps"]["bench_murcia"]["draws"]["core"] = 37
    new["timings"]["maps"]["bench_murcia"]["python_s"] = 22.0
    new["maps"]["bench_paris"] = copy.deepcopy(new["maps"]["bench_murcia"])
    old["maps"]["bench_krakow"] = copy.deepcopy(old["maps"]["bench_murcia"])
    text = bench.compare(old, new, old_name="r1", new_name="r2")
    lines = text.splitlines()
    assert lines[0] == "# Bench: r1 -> r2"
    assert "Host changed" not in text
    assert "| bench_murcia | triangles total | 1500 | 1512 | +12 |" in lines
    assert "| bench_murcia | draws core | 40 | 37 | -3 |" in lines
    assert "| bench_murcia | draws scenic | 12 | 12 | 0 |" in lines
    assert "| bench_murcia | python s | 20.5 | 22.0 | +1.5 |" in lines
    assert "| bench_murcia | unity bake s | - | - | - |" in lines
    assert "| bench_paris | added | - | - | - |" in lines
    assert "| bench_paris | triangles total | - | 1512 | - |" in lines
    assert "| bench_krakow | removed | - | - | - |" in lines
    assert "| bench_krakow | lightmap pages | 3 | - | - |" in lines
    assert text.endswith("|\n")
    moved = _report(timings__host={"host_os": "Linux 6.1 x86_64", "host_gpu": "unknown", "machine": "x86_64", "in_container": True})
    assert "**Host changed**" in bench.compare(old, moved, old_name="r1", new_name="r2")


def test_compare_with_unity_bake(tmp_path: Path) -> None:
    old = _report()
    new = _report()
    new["timings"]["maps"]["bench_murcia"]["unity"] = {"status": "measured", "seconds": {"scene.bake": 61.5}}
    old["timings"]["maps"]["bench_murcia"]["unity"] = {"status": "measured", "seconds": {"scene.bake": 70.0}}
    assert "| bench_murcia | unity bake s | 70.0 | 61.5 | -8.5 |" in bench.compare(old, new, old_name="a", new_name="b")


def _run_with_report(root: Path, name: str, report: dict[str, Any] | None = None) -> Path:
    run = root / "runs" / name
    run.mkdir(parents=True)
    if report is not None:
        bench.write_report(report, run / bench.REPORT_FILE)
    return run


def test_previous_report_picks_newest_earlier_run(tmp_path: Path) -> None:
    assert bench.previous_report(tmp_path, tmp_path / "runs" / "20261107T000000Z") is None
    _run_with_report(tmp_path, "20261101T000000Z", _report())
    middle = _run_with_report(tmp_path, "20261103T000000Z", _report())
    _run_with_report(tmp_path, "20261105T000000Z")
    current = _run_with_report(tmp_path, "20261106T000000Z", _report())
    _run_with_report(tmp_path, "20261109T000000Z", _report())
    assert bench.previous_report(tmp_path, current) == middle / bench.REPORT_FILE


def test_compare_command_writes_diff_beside_new(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    old = _run_with_report(tmp_path, "a", _report())
    new = _run_with_report(tmp_path, "b", _report(seed=1))
    assert bench.main(["compare", str(old), str(new)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("# Bench: a -> b\n")
    assert (new / bench.DIFF_FILE).read_text(encoding="utf-8") == out


@pytest.mark.parametrize("bad", ["[1]", "{", json.dumps({"schema": "onward-bench", "schema_version": 2, "maps": {}}),
                                 json.dumps({"schema": "other", "schema_version": 1, "maps": {}}),
                                 json.dumps({"schema": "onward-bench", "schema_version": 1, "maps": []})])
def test_compare_refuses_malformed_or_other_schema(tmp_path: Path, bad: str, capsys: pytest.CaptureFixture[str]) -> None:
    old = tmp_path / "old.json"
    old.write_text(bad, encoding="utf-8")
    new = _run_with_report(tmp_path, "b", _report())
    assert bench.main(["compare", str(old), str(new)]) == 1
    assert capsys.readouterr().err.startswith("bench: ")
    assert (new / bench.REPORT_FILE).is_file()
    assert not (new / bench.DIFF_FILE).exists()


# ---------------------------------------------------------------------------------------------------- contact sheet, host, usage


def test_contact_sheet_deterministic(tmp_path: Path) -> None:
    red = _package(tmp_path / "a", thumbnail=(200, 20, 20))
    small = _package(tmp_path / "b")
    Image.new("RGB", (64, 32), (20, 200, 20)).save(small / "thumbnail.png")
    tiles = [("bench_murcia", red / "thumbnail.png"), ("bench_madrid", small / "thumbnail.png"),
             ("bench_paris", tmp_path / "none" / "thumbnail.png")]
    bench.contact_sheet(tiles, tmp_path / "one.png")
    bench.contact_sheet(tiles, tmp_path / "two.png")
    assert (tmp_path / "one.png").read_bytes() == (tmp_path / "two.png").read_bytes()
    with Image.open(tmp_path / "one.png") as sheet:
        assert sheet.size == (2 * bench.THUMBNAIL_WIDTH, 2 * (bench.THUMBNAIL_HEIGHT + bench._LABEL_H))
        assert sheet.getpixel((10, bench._LABEL_H + 10)) == (200, 20, 20)
        assert sheet.getpixel((bench.THUMBNAIL_WIDTH + 10, bench._LABEL_H + 10)) == (20, 200, 20)
        third = (10, bench.THUMBNAIL_HEIGHT + 2 * bench._LABEL_H + 10)
        assert sheet.getpixel(third) == bench._MISSING_TILE
        assert sheet.getpixel((bench.THUMBNAIL_WIDTH + 10, third[1])) == bench._BACKGROUND
        assert not sheet.info.get("Software") and "date" not in str(sheet.info).lower()


def test_host_info() -> None:
    host = bench.host_info("Darwin 25.6.0 arm64", "Apple M5 Pro")
    assert host["host_os"] == "Darwin 25.6.0 arm64" and host["host_gpu"] == "Apple M5 Pro"
    assert set(host) == {"host_os", "host_gpu", "system", "machine", "release", "python", "cpu_count", "in_container"}
    blank = bench.host_info("", "  ")
    assert blank["host_os"] == "unknown" and blank["host_gpu"] == "unknown"
    assert bench.host_info(None, None)["host_gpu"] == "unknown"


def test_main_usage_and_refuses_existing_run_dir(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert bench.main(["run", "--only", "bench_nowhere"]) == 2
    assert bench.main(["check", "only-one"]) == 2
    assert bench.main(["frobnicate"]) == 2
    capsys.readouterr()
    existing = tmp_path / "runs" / "r1"
    existing.mkdir(parents=True)
    (existing / "keep.txt").write_text("keep", encoding="utf-8")

    def never(argv: Any) -> int:
        raise AssertionError(f"no stage may run: {argv}")

    assert bench.main(["--root", str(tmp_path), "--run-id", "r1"], execute=never) == 1
    assert "already exists" in capsys.readouterr().err
    assert sorted(p.name for p in existing.iterdir()) == ["keep.txt"]
    assert bench.main(["run", "--root", str(tmp_path), "--run-id", "../x"], execute=never) == 1
    assert "run id" in capsys.readouterr().err


def test_host_flags_go_before_any_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    report = tmp_path / "a.json"
    bench.write_report(_report(), report)
    assert bench.main(["--host-os", "Darwin 25.6.0 arm64", "--host-gpu=", "check", str(report), str(report)]) == 0
    assert bench.main(["--host-os", "X", "compare", str(report), str(report)]) == 0
    assert bench.main(["check", str(report), str(report), "--host-os", "X"]) == 2
    capsys.readouterr()


# ---------------------------------------------------------------------------------------------------- gameplay


def _gameplay(root: Path, report: Any) -> Path:
    path = root / "data" / "processed" / "layout" / "gameplay_report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report), encoding="utf-8")
    return root


HEADLINE = {"distinct_routes": 2, "max_overlap_pct": 16.9, "longest_open_sight_m": 133.86,
            "cover_per_100m_min_stretch": 0.0, "contact_diff_pct": 5.23}


def test_gameplay_metrics_reads_the_headline(tmp_path: Path) -> None:
    assert bench.gameplay_metrics(tmp_path) is None
    root = _gameplay(tmp_path, {"status": "ok", "headline": HEADLINE, "pairs": []})
    assert bench.gameplay_metrics(root) == {**HEADLINE, "status": "ok"}
    nulls = dict.fromkeys(HEADLINE)
    _gameplay(tmp_path, {"status": "no_path", "headline": nulls})
    assert bench.gameplay_metrics(root) == {**nulls, "status": "no_path"}


@pytest.mark.parametrize(("report", "message"), [
    ({"status": "ok", "headline": {k: v for k, v in HEADLINE.items() if k != "contact_diff_pct"}}, "headline has no contact_diff_pct"),
    ({"status": "ok", "headline": {**HEADLINE, "distinct_routes": "3"}}, "headline.distinct_routes must be a number or null"),
    ({"status": "ok", "headline": {**HEADLINE, "max_overlap_pct": True}}, "headline.max_overlap_pct must be a number or null"),
    ({"status": "ok"}, "headline must be an object"),
    ({"status": 1, "headline": HEADLINE}, "status must be a string"),
])
def test_gameplay_metrics_refuses_a_broken_report(tmp_path: Path, report: Any, message: str) -> None:
    with pytest.raises(bench.BenchError, match=message):
        bench.gameplay_metrics(_gameplay(tmp_path, report))


def test_map_entry_has_gameplay_only_when_the_report_exists(tmp_path: Path) -> None:
    package = _package(tmp_path / "pkg")
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "map.json").write_text("{}", encoding="utf-8")
    ref = bench.REFERENCE_MAPS[0]
    plain = bench.map_entry(ref, package, inputs)
    assert "gameplay" not in plain
    assert bench.map_entry(ref, package, inputs, tmp_path / "run") == plain
    root = _gameplay(tmp_path / "run", {"status": "ok", "headline": HEADLINE})
    assert bench.map_entry(ref, package, inputs, root) == {**plain, "gameplay": {**HEADLINE, "status": "ok"}}


def test_compare_rows_for_gameplay_read_missing_on_an_old_report() -> None:
    old = _report()
    new = _report()
    new["maps"]["bench_murcia"]["gameplay"] = {**HEADLINE, "status": "ok"}
    lines = bench.compare(old, new, old_name="r1", new_name="r2").splitlines()
    assert "| bench_murcia | distinct routes | - | 2 | - |" in lines
    assert "| bench_murcia | max route overlap % | - | 16.9 | - |" in lines
    assert "| bench_murcia | longest open sight m | - | 133.9 | - |" in lines
    assert "| bench_murcia | cover per 100 m (min stretch) | - | 0.0 | - |" in lines
    assert "| bench_murcia | contact diff % | - | 5.2 | - |" in lines
    same = bench.compare(new, new, old_name="r2", new_name="r2").splitlines()
    assert "| bench_murcia | distinct routes | 2 | 2 | 0 |" in same
    assert "| bench_murcia | contact diff % | 5.2 | 5.2 | 0.0 |" in same


def test_report_command_reads_the_gameplay_report_again(tmp_path: Path) -> None:
    run_dir = tmp_path / "r1"
    _package(run_dir / "bench_murcia" / "data" / "processed" / "export")
    report = _report()
    report["maps"] = {"bench_murcia": report["maps"]["bench_murcia"]}
    (run_dir / bench.REPORT_FILE).write_text(json.dumps(report), encoding="utf-8")
    _gameplay(run_dir / "bench_murcia", {"status": "ok", "headline": HEADLINE})
    assert bench.main(["report", str(run_dir)]) == 0
    again = json.loads((run_dir / bench.REPORT_FILE).read_text(encoding="utf-8"))
    assert again["maps"]["bench_murcia"]["gameplay"] == {**HEADLINE, "status": "ok"}


# ---------------------------------------------------------------------------------------------------- repetition (plan C1)


def _repetition(root: Path, report: Any) -> Path:
    path = root / "data" / "processed" / "scene_materials" / "materials_report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report), encoding="utf-8")
    return root


SLOTS = [
    {"slot": "roofs", "status": "measured", "coarse_contrast": 0.0211, "limit": 0.019, "over_limit": True},
    {"slot": "sidewalks", "status": "measured", "coarse_contrast": 0.006, "limit": 0.012, "over_limit": False},
    {"slot": "doors", "status": "not_applicable", "coarse_contrast": None, "limit": None, "over_limit": False},
    {"slot": "trunks", "status": "unmeasured", "coarse_contrast": None, "limit": None, "over_limit": False},
]


def test_repetition_metrics_summarise_the_measured_slots(tmp_path: Path) -> None:
    assert bench.repetition_metrics(tmp_path) is None
    root = _repetition(tmp_path, {"applied": []})
    assert bench.repetition_metrics(root) is None
    _repetition(tmp_path, {"repetition": {"slots": SLOTS}})
    assert bench.repetition_metrics(root) == {"slots_measured": 2, "max_ratio": round(0.0211 / 0.019, 4), "over_limit_count": 1}
    _repetition(tmp_path, {"repetition": {"slots": SLOTS[2:]}})
    assert bench.repetition_metrics(root) == {"slots_measured": 0, "max_ratio": None, "over_limit_count": 0}


@pytest.mark.parametrize(("block", "message"), [
    ([], "repetition must be an object"),
    ({"slots": "x"}, "slots must be a list of objects"),
    ({"slots": [1]}, "slots must be a list of objects"),
    ({"slots": [{"slot": "roofs", "status": "measured", "coarse_contrast": None, "limit": 0.019}]}, "slot 'roofs' needs a number"),
    ({"slots": [{"slot": "roofs", "status": "measured", "coarse_contrast": 0.01, "limit": True}]}, "slot 'roofs' needs a number"),
    ({"slots": [{"slot": "roofs", "status": "measured", "coarse_contrast": 0.01, "limit": 0}]}, "slot 'roofs' needs a number"),
])
def test_repetition_metrics_refuse_a_broken_block(tmp_path: Path, block: Any, message: str) -> None:
    with pytest.raises(bench.BenchError, match=message):
        bench.repetition_metrics(_repetition(tmp_path, {"repetition": block}))


def test_map_entry_has_repetition_only_when_the_block_exists(tmp_path: Path) -> None:
    package = _package(tmp_path / "pkg")
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "map.json").write_text("{}", encoding="utf-8")
    ref = bench.REFERENCE_MAPS[0]
    plain = bench.map_entry(ref, package, inputs)
    root = _repetition(tmp_path / "run", {"applied": []})
    assert bench.map_entry(ref, package, inputs, root) == plain
    _repetition(root, {"repetition": {"slots": SLOTS}})
    assert bench.map_entry(ref, package, inputs, root)["repetition"]["over_limit_count"] == 1


def test_compare_shows_repetition_rows_only_when_a_report_has_them() -> None:
    old, new = _report(), _report()
    assert "repetition" not in bench.compare(old, new, old_name="r1", new_name="r2")
    new["maps"]["bench_murcia"]["repetition"] = {"slots_measured": 2, "max_ratio": 1.1105, "over_limit_count": 1}
    lines = bench.compare(old, new, old_name="r1", new_name="r2").splitlines()
    assert "| bench_murcia | repetition slots measured | - | 2 | - |" in lines
    assert "| bench_murcia | repetition max contrast/limit | - | 1.1 | - |" in lines
    assert "| bench_murcia | repetition tiles over limit | - | 1 | - |" in lines
    assert not any("repetition" in line for line in lines if "bench_madrid" in line)


def test_report_command_reads_the_repetition_block_again(tmp_path: Path) -> None:
    run_dir = tmp_path / "r1"
    _package(run_dir / "bench_murcia" / "data" / "processed" / "export")
    report = _report()
    report["maps"] = {"bench_murcia": report["maps"]["bench_murcia"]}
    (run_dir / bench.REPORT_FILE).write_text(json.dumps(report), encoding="utf-8")
    _repetition(run_dir / "bench_murcia", {"repetition": {"slots": SLOTS}})
    assert bench.main(["report", str(run_dir)]) == 0
    again = json.loads((run_dir / bench.REPORT_FILE).read_text(encoding="utf-8"))
    assert again["maps"]["bench_murcia"]["repetition"]["slots_measured"] == 2


# ---------------------------------------------------------------------------------------------------- lidar roofs (plan C3)


def _detail_report(root: Path, report: Any) -> Path:
    path = root / "data" / "processed" / "scene_detail" / "detail_report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report), encoding="utf-8")
    return root


LIDAR = {"source": "es.pnoa_ndsm", "buildings": 10, "core_buildings": 8, "flat": 2, "pitched": 3,
         "unclassified": {"tagged": 1, "scenic": 2, "too_few_cells": 2}}


def test_lidar_roofs_metrics_summarise_the_classes(tmp_path: Path) -> None:
    assert bench.lidar_roofs_metrics(tmp_path) is None
    root = _detail_report(tmp_path, {"counts": {"roofs": {}}})
    assert bench.lidar_roofs_metrics(root) is None
    _detail_report(tmp_path, {"counts": {"lidar_roofs": LIDAR}})
    assert bench.lidar_roofs_metrics(root) == {"flat": 2, "pitched": 3, "unclassified": 5, "classified_pct": 62.5}
    _detail_report(tmp_path, {"counts": {"lidar_roofs": LIDAR | {"core_buildings": 0, "flat": 0, "pitched": 0}}})
    assert bench.lidar_roofs_metrics(root) == {"flat": 0, "pitched": 0, "unclassified": 5, "classified_pct": None}
    _detail_report(tmp_path, {"counts": {"lidar_roofs": LIDAR | {"core_buildings": 3, "flat": 1, "pitched": 0}}})
    assert bench.lidar_roofs_metrics(root)["classified_pct"] == 33.3


@pytest.mark.parametrize(("report", "message"), [
    ({}, "counts must be an object"),
    ({"counts": {"lidar_roofs": []}}, "lidar_roofs must be an object"),
    ({"counts": {"lidar_roofs": LIDAR | {"unclassified": []}}}, "needs integer core_buildings"),
    ({"counts": {"lidar_roofs": LIDAR | {"flat": "2"}}}, "needs integer core_buildings"),
    ({"counts": {"lidar_roofs": LIDAR | {"pitched": True}}}, "needs integer core_buildings"),
    ({"counts": {"lidar_roofs": LIDAR | {"core_buildings": None}}}, "needs integer core_buildings"),
    ({"counts": {"lidar_roofs": LIDAR | {"unclassified": {"mixed": 1.5}}}}, "needs integer core_buildings"),
])
def test_lidar_roofs_metrics_refuse_a_broken_block(tmp_path: Path, report: Any, message: str) -> None:
    with pytest.raises(bench.BenchError, match=message):
        bench.lidar_roofs_metrics(_detail_report(tmp_path, report))


def test_map_entry_and_report_have_lidar_roofs_only_when_the_block_exists(tmp_path: Path) -> None:
    package = _package(tmp_path / "pkg")
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "map.json").write_text("{}", encoding="utf-8")
    ref = bench.REFERENCE_MAPS[0]
    plain = bench.map_entry(ref, package, inputs)
    root = _detail_report(tmp_path / "run", {"counts": {}})
    assert bench.map_entry(ref, package, inputs, root) == plain
    _detail_report(root, {"counts": {"lidar_roofs": LIDAR}})
    assert bench.map_entry(ref, package, inputs, root)["lidar_roofs"]["pitched"] == 3
    run_dir = tmp_path / "r1"
    _package(run_dir / "bench_murcia" / "data" / "processed" / "export")
    report = _report()
    report["maps"] = {"bench_murcia": report["maps"]["bench_murcia"]}
    (run_dir / bench.REPORT_FILE).write_text(json.dumps(report), encoding="utf-8")
    assert bench.main(["report", str(run_dir)]) == 0
    assert "lidar_roofs" not in json.loads((run_dir / bench.REPORT_FILE).read_text(encoding="utf-8"))["maps"]["bench_murcia"]
    _detail_report(run_dir / "bench_murcia", {"counts": {"lidar_roofs": LIDAR}})
    assert bench.main(["report", str(run_dir)]) == 0
    again = json.loads((run_dir / bench.REPORT_FILE).read_text(encoding="utf-8"))
    assert again["maps"]["bench_murcia"]["lidar_roofs"] == {"flat": 2, "pitched": 3, "unclassified": 5, "classified_pct": 62.5}


def test_compare_shows_lidar_roof_rows_only_when_a_report_has_them() -> None:
    old, new = _report(), _report()
    assert "lidar roofs" not in bench.compare(old, new, old_name="r1", new_name="r2")
    new["maps"]["bench_murcia"]["lidar_roofs"] = {"flat": 2, "pitched": 3, "unclassified": 5, "classified_pct": 62.5}
    lines = bench.compare(old, new, old_name="r1", new_name="r2").splitlines()
    assert "| bench_murcia | lidar roofs flat | - | 2 | - |" in lines
    assert "| bench_murcia | lidar roofs pitched | - | 3 | - |" in lines
    assert "| bench_murcia | lidar roofs classified % | - | 62.5 | - |" in lines
    assert not any("lidar roofs" in line for line in lines if "bench_madrid" in line)


def test_the_run_parser_takes_the_lidar_roofs_switch() -> None:
    args = bench.build_parser().parse_args(["run", "--lidar-roofs"])
    assert args.lidar_roofs is True
    assert bench.build_parser().parse_args(["run"]).lidar_roofs is False


def test_the_run_parser_takes_the_scenic_out_of_lightmap_switch() -> None:
    assert bench.build_parser().parse_args(["run", "--scenic-out-of-lightmap"]).scenic_out_of_lightmap is True
    assert bench.build_parser().parse_args(["run"]).scenic_out_of_lightmap is False


def _scenic_map(root: Path, card: Any, export: str | None = "config/export.json") -> Path:
    root.mkdir(parents=True)
    (root / "map.json").write_text(json.dumps({"schema": "onward_generator.map", "id": "m", "paths": {"export": export} if export else {}}), encoding="utf-8")
    if card is not None:
        path = root / (export or "config/export.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(card if isinstance(card, str) else json.dumps(card), encoding="utf-8")
    return root


@pytest.mark.parametrize("export", ["config/export.json", "config/other.json", None])
def test_store_scenic_out_of_lightmap_sets_the_card_key(tmp_path: Path, export: str | None) -> None:
    root = _scenic_map(tmp_path / "m", {"map": {"id": "m"}, "build": {"combine_cell_m": 100.0}}, export)
    bench.store_scenic_out_of_lightmap(root)
    card = json.loads((root / (export or "config/export.json")).read_text(encoding="utf-8"))
    assert card == {"map": {"id": "m"}, "build": {"combine_cell_m": 100.0, "scenic_out_of_lightmap": True}}


@pytest.mark.parametrize(("card", "match"), [
    (None, "has no export card for --scenic-out-of-lightmap"),
    ("{not json", "not valid JSON"),
    ({"build": []}, r"build must be an object, got \[\]"),
    ({"map": {}}, "build must be an object, got None"),
    ([1], "build must be an object, got None"),
])
def test_store_scenic_out_of_lightmap_refuses_a_bad_card(tmp_path: Path, card: Any, match: str) -> None:
    root = _scenic_map(tmp_path / "m", card)
    with pytest.raises(bench.BenchError, match=match):
        bench.store_scenic_out_of_lightmap(root)


def test_store_scenic_out_of_lightmap_needs_a_map(tmp_path: Path) -> None:
    with pytest.raises(bench.BenchError, match="no map.json to find the export card"):
        bench.store_scenic_out_of_lightmap(tmp_path)


def test_the_run_parser_takes_lightmap_pages_max() -> None:
    assert bench.build_parser().parse_args(["run", "--lightmap-pages-max", "8"]).lightmap_pages_max == 8
    assert bench.build_parser().parse_args(["run", "--lightmap-pages-max", "1"]).lightmap_pages_max == 1
    assert bench.build_parser().parse_args(["run"]).lightmap_pages_max is None


@pytest.mark.parametrize("value", ["0", "-1", "2.5", "x", ""])
def test_the_run_parser_refuses_a_bad_lightmap_pages_max(value: str, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        bench.build_parser().parse_args(["run", "--lightmap-pages-max", value])
    assert "--lightmap-pages-max" in capsys.readouterr().err


@pytest.mark.parametrize(("value", "message"), [("0", "0 is below 1"), ("-3", "-3 is below 1"), ("2.5", "'2.5' is not a whole number")])
def test_positive_int_messages(value: str, message: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError, match=f"^{re.escape(message)}$"):
        bench._positive_int(value)
    assert bench._positive_int("12") == 12


@pytest.mark.parametrize("export", ["config/export.json", "config/other.json", None])
def test_store_lightmap_pages_max_sets_the_card_key(tmp_path: Path, export: str | None) -> None:
    root = _scenic_map(tmp_path / "m", {"map": {"id": "m"}, "build": {"combine_cell_m": 100.0}}, export)
    bench.store_lightmap_pages_max(root, 8)
    card = json.loads((root / (export or "config/export.json")).read_text(encoding="utf-8"))
    assert card == {"map": {"id": "m"}, "build": {"combine_cell_m": 100.0, "lightmap_pages_max": 8}}


@pytest.mark.parametrize(("card", "match"), [
    (None, "has no export card for --lightmap-pages-max"),
    ("{not json", "not valid JSON"),
    ({"build": []}, r"build must be an object, got \[\]"),
    ({"map": {}}, "build must be an object, got None"),
])
def test_store_lightmap_pages_max_refuses_a_bad_card(tmp_path: Path, card: Any, match: str) -> None:
    root = _scenic_map(tmp_path / "m", card)
    with pytest.raises(bench.BenchError, match=match):
        bench.store_lightmap_pages_max(root, 8)


def test_store_lightmap_pages_max_needs_a_map(tmp_path: Path) -> None:
    with pytest.raises(bench.BenchError, match="no map.json to find the export card"):
        bench.store_lightmap_pages_max(tmp_path, 8)


def test_the_run_parser_takes_the_balanced_spawns_switch() -> None:
    assert bench.build_parser().parse_args(["run", "--balanced-spawns"]).balanced_spawns is True
    assert bench.build_parser().parse_args(["run"]).balanced_spawns is False
