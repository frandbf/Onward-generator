"""Fixed eye-level views of a baked scene (plan E1, ADR-037): the cameras, the file and the Unity wiring. No Unity."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from onward_generator.cli import main
from onward_generator.config.validation import REPO_ROOT
from onward_generator.export.views import (
    EYE_HEIGHT_M,
    FOV_DEG,
    ViewError,
    capture_views,
    heading_deg,
    views_document,
    write_views,
)

ADAPTER = REPO_ROOT / "tools" / "unity" / "onward_adapter" / "Editor" / "OnwardMapAdapter.cs"
RUN_EXPORT = REPO_ROOT / "tools" / "unity" / "onward_adapter" / "run_export.sh"


def _layout(**extra: Any) -> dict[str, Any]:
    return {"schema": "onward_generator.gameplay_layout", "schema_version": "0.3.0", "ground_z_m": 0.0,
            "attackers": {"centre_m": [0.0, 0.0]}, "defenders": {"centre_m": [0.0, 100.0]}, "uplink_m": [100.0, 0.0], **extra}


@pytest.mark.parametrize(
    ("target", "expected"),
    [((0, 10), 0.0), ((10, 0), 90.0), ((0, -10), 180.0), ((-10, 0), 270.0), ((10, 10), 45.0), ((-10, -10), 225.0),
     ((3, 4), 36.87), ((0, 0), 0.0)],
)
def test_heading_is_clockwise_from_north(target: tuple[float, float], expected: float) -> None:
    assert heading_deg((0.0, 0.0), target) == expected


def test_heading_is_measured_from_the_origin() -> None:
    assert heading_deg((5.0, 5.0), (5.0, 15.0)) == 0.0
    assert heading_deg((5.0, 5.0), (15.0, 5.0)) == 90.0


def test_each_spawn_looks_at_the_other_and_the_uplink_at_each() -> None:
    views = capture_views(_layout())
    assert views == [
        {"name": "attackers", "position_m": [0.0, 0.0, EYE_HEIGHT_M], "heading_deg": 0.0, "pitch_deg": 0.0},
        {"name": "defenders", "position_m": [0.0, 100.0, EYE_HEIGHT_M], "heading_deg": 180.0, "pitch_deg": 0.0},
        {"name": "uplink_to_attackers", "position_m": [100.0, 0.0, EYE_HEIGHT_M], "heading_deg": 270.0, "pitch_deg": 0.0},
        {"name": "uplink_to_defenders", "position_m": [100.0, 0.0, EYE_HEIGHT_M], "heading_deg": 315.0, "pitch_deg": 0.0},
    ]


def test_without_an_uplink_only_the_spawns_look() -> None:
    layout = _layout()
    del layout["uplink_m"]
    assert [v["name"] for v in capture_views(layout)] == ["attackers", "defenders"]
    assert [v["name"] for v in capture_views({**layout, "uplink_m": None})] == ["attackers", "defenders"]


def test_the_eye_stands_above_the_ground_of_the_layout() -> None:
    assert {v["position_m"][2] for v in capture_views(_layout(ground_z_m=2.5))} == {4.2}
    layout = _layout()
    del layout["ground_z_m"]
    assert {v["position_m"][2] for v in capture_views(layout)} == {EYE_HEIGHT_M}


def test_a_perf_layout_has_no_views() -> None:
    with pytest.raises(ViewError) as refused:
        capture_views({**_layout(), "schema": "onward_generator.perf_layout"})
    assert str(refused.value) == "capture views need a gameplay layout: a perf layout has no spawns to look from"


@pytest.mark.parametrize(
    ("change", "message"),
    [({"attackers": {}}, "layout: attackers.centre_m must be [x, y] in metres"),
     ({"attackers": None}, "layout: attackers.centre_m must be [x, y] in metres"),
     ({"defenders": {"centre_m": [1.0]}}, "layout: defenders.centre_m must be [x, y] in metres"),
     ({"defenders": {"centre_m": "1,2"}}, "layout: defenders.centre_m must be [x, y] in metres"),
     ({"uplink_m": [1.0, 2.0, 3.0]}, "layout: uplink_m must be [x, y] in metres")],
)
def test_a_missing_or_malformed_point_is_refused(change: dict[str, Any], message: str) -> None:
    with pytest.raises(ViewError) as refused:
        capture_views(_layout(**change))
    assert str(refused.value) == message


def test_the_document_carries_the_image_and_the_lens() -> None:
    document = views_document(_layout())
    assert {k: v for k, v in document.items() if k != "views"} == {
        "schema": "onward_generator.capture_views", "schema_version": "0.1.0",
        "frame": "local metres, x east, y north, z up; heading clockwise from north; Unity (x, z, y)",
        "width": 1920, "height": 1080, "fov_deg": FOV_DEG,
    }
    assert document["views"] == capture_views(_layout())


def _package(tmp_path: Path, layout: dict[str, Any] | None = None) -> Path:
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "layout.json").write_text(json.dumps(layout or _layout()), encoding="utf-8")
    return package


def test_the_views_are_written_under_build_and_nothing_else_changes(tmp_path: Path) -> None:
    package = _package(tmp_path)
    path = write_views(package)
    assert path == package.resolve() / "build" / "views" / "views.json"
    assert json.loads(path.read_bytes()) == views_document(_layout())
    assert sorted(p.relative_to(package).as_posix() for p in package.rglob("*")) == [
        "build", "build/views", "build/views/views.json", "layout.json"]


def test_an_existing_views_folder_is_never_overwritten(tmp_path: Path) -> None:
    package = _package(tmp_path)
    (package / "build" / "views").mkdir(parents=True)
    (package / "build" / "views" / "attackers.png").write_bytes(b"old")
    with pytest.raises(ViewError, match=r"views exists; captures are never overwritten"):
        write_views(package)
    assert (package / "build" / "views" / "attackers.png").read_bytes() == b"old"
    assert not (package / "build" / "views" / "views.json").exists()


def test_a_folder_without_a_layout_or_with_a_perf_layout_writes_nothing(tmp_path: Path) -> None:
    with pytest.raises(ViewError, match="has no layout.json; is it an export package"):
        write_views(tmp_path)
    package = _package(tmp_path, {"schema": "onward_generator.perf_layout"})
    with pytest.raises(ViewError, match="need a gameplay layout"):
        write_views(package)
    assert not (package / "build").exists()


def test_the_command_writes_the_views_and_names_the_unity_step(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    package = _package(tmp_path)
    assert main(["export", "views", str(package)]) == 0
    out = capsys.readouterr().out
    assert f"views written to {package.resolve() / 'build' / 'views' / 'views.json'}\n" in out
    assert f"next: tools/unity/onward_adapter/run_export.sh {package.resolve()} views\n" in out
    assert main(["export", "views", str(package)]) == 1
    assert "export views: " in capsys.readouterr().err


def test_the_adapter_renders_the_views_it_is_given() -> None:
    """No Unity here: this pins the wiring. It rendered Madrid Sol Arena pkg_13 on 10 November 2026 in 21 s."""
    source = ADAPTER.read_text(encoding="utf-8")
    assert "public static void CaptureViews() => CaptureViewsFromPaths(RequireArg(\"-exportPackage\"));" in source
    assert 'list.schema != "onward_generator.capture_views"' in source
    assert "new Vector3(view.position_m[0], view.position_m[2], view.position_m[1])" in source
    assert "Quaternion.Euler(-view.pitch_deg, view.heading_deg, 0f)" in source
    assert 'throw new IOException($"{png} exists; captures are never overwritten");' in source
    assert "EnsureLightmapsAssigned();" in source[source.index("CaptureViewsFromPaths(string"):]


def test_run_export_has_a_views_step_after_the_scene() -> None:
    script = RUN_EXPORT.read_text(encoding="utf-8")
    assert "tool|scene|views|launcher|push|inspect|archive) \"step_$step\" ;;" in script
    body = script[script.index("step_views() {"):script.index("step_launcher() {")]
    assert '[[ -e "$BUILD/scene_report.json" ]] || { log "run the scene step first' in body
    assert "-m onward_generator export views \"$PACKAGE\"" in body
    assert "-executeMethod OnwardGenerator.OnwardMapAdapter.CaptureViews -exportPackage \"$PACKAGE\"" in body


def test_heading_and_eye_keep_their_decimals() -> None:
    assert heading_deg((0.0, 0.0), (1.0, 2.0)) == 26.57
    assert {v["position_m"][2] for v in capture_views(_layout(ground_z_m=0.1234))} == {1.823}


def test_the_export_help_lists_the_views_command(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["export", "--help"]) == 0
    assert "write build/views/views.json, the fixed eye-level cameras the adapter's views step renders (plan E1)" in " ".join(capsys.readouterr().out.split())
