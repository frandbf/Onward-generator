"""tools/bench/bench.py against the real stage plan (plan_steps, run_steps) around MURCIA_CENTER.

Every stage runs through a stub ``execute`` that writes the file or folder its ``--out`` names, so no
network and no pipeline work happens; the export stage copies the fixture manifest and a thumbnail.
The last test builds a real package from the synthetic street block and reads it back.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from PIL import Image

from onward_generator.cadastre.acquire import buildings_path
from onward_generator.cli import main as cli_main
from onward_generator.export.package import MANIFEST_FILE, build_package
from onward_generator.osm.acquire import osm_target
from tests.conftest import FIXED_NOW, MURCIA_CENTER_LAT, MURCIA_CENTER_LON

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures" / "bench"
DOWNLOADS = {("osm", "download"), ("cadastre", "download"), ("source", "fetch"), ("overture", "fetch")}


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("onward_bench_pipeline", REPO / "tools" / "bench" / "bench.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bench = _load()


class Stub:
    """Writes each stage's output; ``fail`` is the stage (first argv word) that returns 1."""

    def __init__(self, fail: str | None = None) -> None:
        self.calls: list[list[str]] = []
        self.fail = fail

    def downloads(self) -> list[tuple[str, ...]]:
        return [tuple(argv[:2]) for argv in self.calls if tuple(argv[:2]) in DOWNLOADS]

    def __call__(self, argv: Sequence[str]) -> int:
        argv = list(argv)
        self.calls.append(argv)
        if argv[0] == self.fail:
            return 1
        head = tuple(argv[:2])
        if head == ("osm", "download"):
            target = osm_target(Path(argv[argv.index("--map") + 1]))
            target.write_bytes(b"<osm version='0.6'/>")
            target.with_name(target.name + ".source.json").write_text(json.dumps({"retrieved_at": "2026-10-18T12:00:00Z"}))
            return 0
        out = Path(argv[argv.index("--out") + 1])
        if head == ("cadastre", "download"):
            out.write_bytes(b"<gml/>")
            buildings_path(out).write_bytes(b"<gml buildings/>")
        elif head == ("source", "fetch"):
            out.mkdir(parents=True)
            (out / "tile_000.bin").write_bytes(b"\x00\x01")
            out.with_name(out.name + ".source.json").write_text("{}")
        elif head == ("overture", "fetch"):
            out.mkdir(parents=True, exist_ok=True)
            (out / "overture_buildings.parquet").write_bytes(b"PAR1")
            (out / "overture_places.parquet").write_bytes(b"PAR1")
        elif head == ("export", "package"):
            out.mkdir(parents=True)
            shutil.copy(FIXTURES / "onward_export.json", out / MANIFEST_FILE)
            Image.new("RGB", (bench.THUMBNAIL_WIDTH, bench.THUMBNAIL_HEIGHT), (90, 120, 150)).save(out / "thumbnail.png")
        elif out.suffix:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text("{}")
        else:
            out.mkdir(parents=True, exist_ok=True)
            (out / "stub.json").write_text("{}")
        return 0


@pytest.fixture
def workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    other = tmp_path / "maps" / "barcelona_other"
    other.mkdir(parents=True)
    (other / "map.json").write_text('{"keep": true}', encoding="utf-8")
    return tmp_path


def _run(root: Path, run_id: str, stub: Stub, *extra: str) -> int:
    return bench.main(["--host-os", "Test OS", "--host-gpu=Test GPU", "run", "--only", "bench_murcia", "--root", str(root),
                       "--run-id", run_id, *extra], execute=stub)


def _tree(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def test_murcia_reference_runs_every_python_stage(workdir: Path) -> None:
    stub = Stub()
    assert _run(workdir / "bench", "r1", stub) == 0
    names = [argv[0] for argv in stub.calls]
    assert names[0] == "osm"
    for stage in ("geo", "heights", "generate", "detail", "dress", "materials", "signs", "overgrow", "bounds", "perf", "export"):
        assert stage in names
    assert "--no-preview" in next(argv for argv in stub.calls if argv[0] == "generate")
    report = json.loads((workdir / "bench" / "runs" / "r1" / bench.REPORT_FILE).read_text(encoding="utf-8"))
    entry = report["maps"]["bench_murcia"]
    assert entry["reference"]["latitude"] == MURCIA_CENTER_LAT and entry["reference"]["longitude"] == MURCIA_CENTER_LON
    assert entry["visual_triangles"]["total"] == 1500
    assert report["timings"]["host"]["host_os"] == "Test OS" and report["timings"]["host"]["host_gpu"] == "Test GPU"
    timing = report["timings"]["maps"]["bench_murcia"]
    assert timing["unity"] == {"status": "missing"}
    assert timing["stages"][0]["stage"] == "map" and [row["stage"] for row in timing["stages"]][-1] == "export"
    assert (workdir / "bench" / "runs" / "r1" / bench.CONTACT_SHEET_FILE).is_file()


def test_first_run_fills_cache_and_second_run_offline_gives_same_report(workdir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = workdir / "bench"
    first = Stub()
    assert _run(root, "r1", first) == 0
    assert set(first.downloads()) == DOWNLOADS
    inputs = root / "inputs" / "bench_murcia"
    raw = sorted(p.relative_to(inputs).as_posix() for p in (inputs / "data" / "raw").rglob("*") if p.is_file())
    assert "data/raw/bench_murcia.osm" in raw and "data/raw/catastro_buildings.gml" in raw
    assert "data/raw/overture_buildings.parquet" in raw
    assert [p for p in (inputs / "data").rglob("*") if p.is_file() and "processed" in p.parts] == []
    assert "no previous run" in capsys.readouterr().out

    second = Stub()
    assert _run(root, "r2", second, "--offline") == 0
    assert second.downloads() == []
    one = json.loads((root / "runs" / "r1" / bench.REPORT_FILE).read_text(encoding="utf-8"))
    two = json.loads((root / "runs" / "r2" / bench.REPORT_FILE).read_text(encoding="utf-8"))
    assert bench.report_text(bench.strip_timings(one)) == bench.report_text(bench.strip_timings(two))
    assert [row["stage"] for row in two["timings"]["maps"]["bench_murcia"]["stages"]][0] == "geo"
    assert bench.main(["check", str(root / "runs" / "r1"), str(root / "runs" / "r2")]) == 0
    diff = (root / "runs" / "r2" / bench.DIFF_FILE).read_text(encoding="utf-8")
    assert diff.startswith("# Bench: r1 -> r2\n")
    assert "| bench_murcia | triangles total | 1500 | 1500 | 0 |" in diff


def test_cache_never_overwritten(workdir: Path) -> None:
    root = workdir / "bench"
    assert _run(root, "r1", Stub()) == 0
    inputs = root / "inputs" / "bench_murcia" / "data" / "raw"
    (inputs / "catastro.gml").write_bytes(b"cached by hand")
    buildings_path(inputs / "catastro.gml").unlink()
    again = Stub()
    assert _run(root, "r2", again) == 0
    assert again.downloads() == [("cadastre", "download")]
    assert (inputs / "catastro.gml").read_bytes() == b"cached by hand"
    assert buildings_path(inputs / "catastro.gml").read_bytes() == b"<gml buildings/>"


def test_offline_without_cache_fails_before_any_stage(workdir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = workdir / "bench"
    stub = Stub()
    assert _run(root, "r1", stub, "--offline") == 1
    assert stub.calls == []
    assert "--offline" in capsys.readouterr().err
    assert not (root / "runs").exists() and not (root / "inputs" / "bench_murcia").exists()


def test_offline_with_a_missing_download_fails_before_any_stage(workdir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = workdir / "bench"
    assert _run(root, "r1", Stub()) == 0
    (root / "inputs" / "bench_murcia" / "data" / "raw" / "overture_buildings.parquet").unlink()
    stub = Stub()
    assert _run(root, "r2", stub, "--offline") == 1
    assert stub.calls == []
    assert "the cache has no overture" in capsys.readouterr().err
    assert not (root / "runs" / "r2").exists()


def test_requested_source_not_active_fails(workdir: Path) -> None:
    krakow = next(ref for ref in bench.REFERENCE_MAPS if ref.id == "bench_krakow")
    wrong = bench.ReferenceMap("bench_wrong", "Bench Wrong", krakow.latitude, krakow.longitude, 150.0, 30.0, 10.0,
                               "test", (("es.catastro", True),))
    with pytest.raises(bench.BenchError, match="source es.catastro does not run"):
        bench.prepare_inputs(wrong, workdir / "inputs", offline=False)
    assert not (workdir / "inputs" / "bench_wrong").exists()


def test_inputs_frozen_for_other_parameters_are_refused(workdir: Path) -> None:
    murcia = bench.REFERENCE_MAPS[0]
    bench.prepare_inputs(murcia, workdir / "inputs", offline=False)
    moved = bench.ReferenceMap(murcia.id, murcia.name, murcia.latitude, murcia.longitude, 200.0, 30.0, 10.0, "test")
    with pytest.raises(bench.BenchError, match="frozen for other parameters"):
        bench.prepare_inputs(moved, workdir / "inputs", offline=False)


def test_stage_failure_stops_and_reports(workdir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = workdir / "bench"
    assert _run(root, "r1", Stub()) == 0
    before_run = _tree(root / "runs" / "r1")
    before_inputs = _tree(root / "inputs")
    capsys.readouterr()
    failing = Stub(fail="detail")
    assert _run(root, "r2", failing) == 1
    assert "bench: bench_murcia: stage detail failed (exit 1)" in capsys.readouterr().err
    assert failing.calls[-1][0] == "detail"
    assert not (root / "runs" / "r2" / bench.REPORT_FILE).exists()
    assert _tree(root / "runs" / "r1") == before_run
    assert _tree(root / "inputs") == before_inputs


def test_report_subcommand_picks_up_unity_after_the_fact(workdir: Path) -> None:
    root = workdir / "bench"
    assert _run(root, "r1", Stub()) == 0
    run = root / "runs" / "r1"
    before = json.loads((run / bench.REPORT_FILE).read_text(encoding="utf-8"))
    build = run / "bench_murcia" / "data" / "processed" / "export" / "pkg_01" / "build"
    build.mkdir()
    shutil.copy(FIXTURES / "scene_report.json", build / "scene_report.json")
    assert bench.main(["report", str(run)]) == 0
    after = json.loads((run / bench.REPORT_FILE).read_text(encoding="utf-8"))
    unity = after["timings"]["maps"]["bench_murcia"]["unity"]
    assert unity["status"] == "measured" and unity["seconds"]["scene.bake"] == 61.5
    assert after["timings"]["maps"]["bench_murcia"]["stages"] == before["timings"]["maps"]["bench_murcia"]["stages"]
    assert bench.differences(bench.strip_timings(before), bench.strip_timings(after)) == []


def test_maps_tree_untouched(workdir: Path) -> None:
    before = _tree(workdir / "maps")
    assert _run(workdir / "bench", "r1", Stub()) == 0
    assert _tree(workdir / "maps") == before
    assert sorted(p.name for p in (workdir / "maps").iterdir()) == ["barcelona_other"]


def test_real_export_metrics(detail_inputs: tuple[Path, Path, Path], tmp_path: Path) -> None:
    scene, heights, base = detail_inputs
    detail = tmp_path / "detail"
    assert cli_main(["detail", "--scene", str(scene), "--heights", str(heights), "--base", str(base),
                     "--out", str(detail), "--no-preview"]) == 0
    layout = tmp_path / "layout.json"
    assert cli_main(["perf", "layout", "--scene", str(scene), "--heights", str(heights), "--base", str(base),
                     "--detail", str(detail), "--out", str(layout)]) == 0
    result = build_package(REPO / "config" / "murcia-belluga.export.json", detail, layout, tmp_path / "pkg", now=FIXED_NOW)
    manifest: dict[str, Any] = result.manifest
    metrics = bench.map_metrics(result.directory)
    tiers = manifest["tiers"]
    if tiers is None:
        assert metrics["tiers_source"] == "visual_glb_no_core"
        assert metrics["visual_triangles"]["scenic"] == 0 and metrics["visual_triangles"]["total"] > 0
    else:
        assert metrics["tiers_source"] == "manifest"
        assert metrics["visual_triangles"] == {
            "core": tiers["core"]["visual_triangles"], "scenic": tiers["scenic"]["visual_triangles"],
            "shared": tiers["shared"]["visual_triangles"],
            "total": sum(tiers[t]["visual_triangles"] for t in ("core", "scenic", "shared"))}
        assert metrics["draws"]["total"] == tiers["draws"]
    view = manifest["view_draws"]
    assert metrics["view_draws"] == {key: view[key] for key in ("views", "median", "p90", "max")}
    assert metrics["lightmap"]["pages_at_least"] == manifest["lightmap_estimate"]["pages_at_least"]
    # The same package read as a map without a core: every triangle of the visual GLB lands in the core.
    whole = tmp_path / "whole"
    shutil.copytree(result.directory, whole)
    (whole / MANIFEST_FILE).write_text(json.dumps({**manifest, "tiers": None}), encoding="utf-8")
    counted = bench.map_metrics(whole)
    assert counted["tiers_source"] == "visual_glb_no_core"
    assert counted["visual_triangles"]["total"] == metrics["visual_triangles"]["total"] > 0
    assert counted["visual_triangles"]["core"] == counted["visual_triangles"]["total"]
    assert counted["visual_triangles"]["scenic"] == counted["visual_triangles"]["shared"] == 0
