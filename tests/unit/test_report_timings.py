"""tools/unity/report_timings.py: the Unity phase timings table from scene and launcher reports."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_TOOL = Path(__file__).resolve().parents[2] / "tools" / "unity" / "report_timings.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("report_timings", _TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


timings = _load()


def _package(root: Path, name: str, scene: dict[str, Any], launcher: dict[str, Any] | None = None) -> Path:
    build = root / name / "build"
    build.mkdir(parents=True)
    (build / "scene_report.json").write_text(json.dumps(scene), encoding="utf-8")
    if launcher is not None:
        (build / "launcher_report.json").write_text(json.dumps(launcher), encoding="utf-8")
    return root / name


NEW_SCENE = {
    "package_id": "murcia_new",
    "lightmap_bake": {"lightmapper_requested": "ProgressiveGPU", "lightmapper": "ProgressiveCPU", "fallback": True,
                      "fallback_warning": "Falling back to CPU", "sizes": ["2048x2048", "2048x2048", "1024x1024"]},
    "seconds": {"scene": 50.04, "bake": 210.0, "import": 12.25, "gi_bake": 200.0, "total": 261.0},
}
LAUNCHER = {"package_id": "murcia_new", "seconds": {"open": 9.0, "bundle": 30.2, "total": 41.0}}
OLD_SCENE = {"package_id": "murcia_old", "seconds": {"scene": 61.0, "bake": 300.0}}


def test_table_has_one_column_per_package_and_rows_in_first_seen_order(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    new = _package(tmp_path, "pkg_02", NEW_SCENE, LAUNCHER)
    old = _package(tmp_path, "pkg_01", OLD_SCENE)
    assert timings.main([str(new), str(old / "build")]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "| phase | murcia_new | murcia_old |",
        "|---|---:|---:|",
        "| scene.scene | 50.0 | 61.0 |",
        "| scene.bake | 210.0 | 300.0 |",
        "| scene.import | 12.2 | - |",
        "| scene.gi_bake | 200.0 | - |",
        "| scene.total | 261.0 | - |",
        "| launcher.open | 9.0 | - |",
        "| launcher.bundle | 30.2 | - |",
        "| launcher.total | 41.0 | - |",
        "| lightmapper | ProgressiveCPU (fallback from ProgressiveGPU) | - |",
        "| lightmaps | 3 (2048x2048 x2, 1024x1024) | - |",
    ]


def test_gpu_bake_without_fallback_and_report_file_argument(tmp_path: Path) -> None:
    scene = {"package_id": "p", "seconds": {"bake": 1}, "lightmap_bake": {"lightmapper": "ProgressiveGPU", "fallback": False, "sizes": []}}
    report = _package(tmp_path, "pkg", scene) / "build" / "scene_report.json"
    title, cells = timings.column(timings.report_path(report))
    assert title == "p"
    assert cells == {"scene.bake": "1.0", "lightmapper": "ProgressiveGPU", "lightmaps": "0 (none)"}


def test_fallback_without_requested_lightmapper_and_title_from_folder(tmp_path: Path) -> None:
    scene = {"seconds": {"total": 2.0, "note": "x"}, "lightmap_bake": {"lightmapper": "ProgressiveCPU", "fallback": True}}
    _, cells = timings.column(timings.report_path(_package(tmp_path, "pkg_07", scene)))
    assert timings.column(timings.report_path(tmp_path / "pkg_07"))[0] == "pkg_07"
    assert cells == {"scene.total": "2.0", "lightmapper": "ProgressiveCPU (fallback from ?)", "lightmaps": "0 (none)"}


@pytest.mark.parametrize("scene", [{"seconds": [1, 2]}, {"lightmap_bake": {"lightmapper": ""}}, {"lightmap_bake": "GPU"}, {}])
def test_report_without_timings_or_bake_gives_an_empty_column(tmp_path: Path, scene: dict[str, Any]) -> None:
    assert timings.column(timings.report_path(_package(tmp_path, "pkg", scene))) == ("pkg", {})


def test_missing_report_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert timings.main([str(tmp_path)]) == 1
    assert capsys.readouterr().err == f"report_timings: no scene_report.json at {tmp_path}\n"


def test_report_that_is_not_an_object_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    report = tmp_path / "scene_report.json"
    report.write_text("[1]", encoding="utf-8")
    assert timings.main([str(report)]) == 1
    assert capsys.readouterr().err == f"report_timings: {report} is not a JSON object\n"


def test_no_argument_prints_usage(capsys: pytest.CaptureFixture[str]) -> None:
    assert timings.main([]) == 2
    assert "report_timings.py PATH" in capsys.readouterr().err
