"""An atmosphere preset from the command to the package: the setting, the day-only rule and the package (ADR-037).

Plan E0 records the preset; plan E3 carries its sun to the adapter. No Unity."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from onward_generator.cli import main
from onward_generator.export.package import ExportError, build_package
from onward_generator.export.sun import street_sun
from onward_generator.maps.workspace import (
    MapError,
    create_map,
    load_map,
    map_atmosphere,
    map_time_of_day,
    store_atmosphere,
    store_time_of_day,
)
from onward_generator.next_step import export_invocation
from onward_generator.pipeline_run import map_create_argv, plan_steps, run_map
from tests.conftest import FIXED_NOW, MURCIA_CENTER_LAT, MURCIA_CENTER_LON
from tests.integration.test_export_package import EXPORT_PROJECT, GOLDEN_OPTIONS_SHA256, GOLDEN_PACKAGE_ID

DAY_ONLY = "atmosphere golden_hour needs a day map: a preset sets the sun, and a night map has the moon (ADR-037)"


def _map(tmp_path: Path, **options: object) -> Path:
    root = tmp_path / "m"
    create_map(root, map_id="m", name="M", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=150, **options)
    return root


def _export_step(root: Path) -> list[str]:
    return next(argv for name, argv in plan_steps(root) if name == "export")


def test_a_preset_is_written_to_map_json_and_reaches_the_export_stage(tmp_path: Path) -> None:
    root = _map(tmp_path, atmosphere="golden_hour")
    assert load_map(root)["atmosphere"] == "golden_hour" and map_atmosphere(root) == "golden_hour"
    step = _export_step(root)
    assert step[-2:] == ["--atmosphere", "golden_hour"]


def test_neutral_writes_no_key_and_passes_nothing(tmp_path: Path) -> None:
    root = _map(tmp_path, atmosphere="neutral")
    assert "atmosphere" not in load_map(root) and map_atmosphere(root) == "neutral"
    assert "--atmosphere" not in _export_step(root)
    assert "atmosphere" not in load_map(_map(tmp_path / "default"))


def test_a_map_from_before_the_setting_or_no_map_is_neutral(tmp_path: Path) -> None:
    assert map_atmosphere(tmp_path / "no_such_map") == "neutral"
    root = _map(tmp_path)
    document = load_map(root)
    document["atmosphere"] = "neutral"
    (root / "map.json").write_text(json.dumps(document), encoding="utf-8")
    assert "--atmosphere" not in _export_step(root)


def test_the_export_command_passes_whatever_map_json_names(tmp_path: Path) -> None:
    """``export package`` checks the name, so a hand-edited map.json fails there with the list of presets."""
    root = _map(tmp_path)
    document = load_map(root)
    document["atmosphere"] = "sunset"
    (root / "map.json").write_text(json.dumps(document), encoding="utf-8")
    argv = list(export_invocation(root, tmp_path / "d", tmp_path / "l.json", tmp_path / "o").argv)
    assert argv[-2:] == ["--atmosphere", "sunset"]
    assert "--atmosphere" not in export_invocation(tmp_path / "no_map", tmp_path / "d", tmp_path / "l.json", tmp_path / "o").argv


def test_an_unknown_preset_or_one_on_a_night_map_is_refused(tmp_path: Path) -> None:
    with pytest.raises(MapError, match="atmosphere must be one of neutral, golden_hour, overcast, not 'sunset'"):
        _map(tmp_path, atmosphere="sunset")
    with pytest.raises(MapError) as refused:
        _map(tmp_path, time_of_day="night", atmosphere="golden_hour")
    assert str(refused.value) == DAY_ONLY
    assert not (tmp_path / "m").exists()
    assert "atmosphere" not in load_map(_map(tmp_path, time_of_day="night", atmosphere="neutral"))


def test_storing_a_preset_says_whether_it_changed(tmp_path: Path) -> None:
    root = _map(tmp_path)
    assert store_atmosphere(root, "neutral") is False
    assert "atmosphere" not in load_map(root)
    assert store_atmosphere(root, "golden_hour") is True and map_atmosphere(root) == "golden_hour"
    assert store_atmosphere(root, "golden_hour") is False
    assert store_atmosphere(root, "overcast") is True and map_atmosphere(root) == "overcast"
    assert store_atmosphere(root, "neutral") is True and "atmosphere" not in load_map(root)


def test_a_refused_preset_leaves_the_map_as_it_was(tmp_path: Path) -> None:
    root = _map(tmp_path, atmosphere="overcast")
    before = (root / "map.json").read_bytes()
    with pytest.raises(MapError, match="one of neutral"):
        store_atmosphere(root, "sunset")
    night = _map(tmp_path / "night", time_of_day="night")
    with pytest.raises(MapError) as refused:
        store_atmosphere(night, "golden_hour")
    assert str(refused.value) == DAY_ONLY
    assert (root / "map.json").read_bytes() == before and map_atmosphere(night) == "neutral"


def test_a_map_with_a_preset_cannot_turn_to_night(tmp_path: Path) -> None:
    root = _map(tmp_path, atmosphere="golden_hour")
    with pytest.raises(MapError) as refused:
        store_time_of_day(root, "night")
    assert str(refused.value) == DAY_ONLY
    assert map_time_of_day(root) == "day"
    assert store_time_of_day(root, "day") is False


def test_map_create_stores_the_preset_it_is_given(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    downloaded = SimpleNamespace(size_bytes=12, sha256="abc", path="luz.osm")
    monkeypatch.setattr("onward_generator.maps.__main__.download_map_osm", lambda *_args, **_kwargs: downloaded)
    monkeypatch.chdir(tmp_path)
    args = ["map", "create", "--lat", "37.625", "--lon", "-0.986", "--radius-m", "300"]
    assert main([*args, "--name", "Dorada", "--atmosphere", "golden_hour"]) == 0
    assert map_atmosphere(tmp_path / "maps" / "dorada") == "golden_hour"
    assert main([*args, "--name", "Neutra"]) == 0
    assert "atmosphere" not in load_map(tmp_path / "maps" / "neutra")
    capsys.readouterr()
    with pytest.raises(SystemExit) as refused:
        main([*args, "--name", "Mala", "--atmosphere", "sunset"])
    assert refused.value.code == 2 and "invalid choice: 'sunset'" in capsys.readouterr().err


def test_run_passes_the_preset_to_map_create_only_when_named() -> None:
    base = {"name": "M", "latitude": 1.0, "longitude": 2.0, "radius_m": 90.0, "margin_m": 5.0}
    assert "--atmosphere" not in map_create_argv(**base)
    argv = map_create_argv(**base, atmosphere="overcast")
    assert argv[argv.index("--atmosphere") + 1] == "overcast"


@pytest.mark.parametrize(("flags", "expected"), [([], None), (["--atmosphere", "golden_hour"], "golden_hour"), (["--atmosphere", "neutral"], "neutral")])
def test_the_run_command_passes_the_preset_or_unset(monkeypatch: pytest.MonkeyPatch, flags: list[str], expected: str | None) -> None:
    received: dict[str, object] = {}

    def fake_run_map(**kwargs: object) -> int:
        received.update(kwargs)
        return 0

    monkeypatch.setattr("onward_generator.pipeline_run.run_map", fake_run_map)
    assert main(["run", "--name", "M", "--lat", str(MURCIA_CENTER_LAT), "--lon", str(MURCIA_CENTER_LON), *flags]) == 0
    assert received["atmosphere"] == expected


def _run_map(root: Path, execute: Callable[[list[str]], int], **options: object) -> int:
    return run_map(map_id="m", name="M", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=150, margin_m=30, out=str(root),
                   execute=execute, interactive=False, start="export", **options)


@pytest.mark.parametrize(
    ("before", "options", "after"),
    [
        ({}, {"atmosphere": "golden_hour"}, ("day", "golden_hour")),
        ({"atmosphere": "golden_hour"}, {}, ("day", "golden_hour")),
        ({"atmosphere": "golden_hour"}, {"atmosphere": "neutral"}, ("day", "neutral")),
        ({"atmosphere": "golden_hour"}, {"time_of_day": "night", "atmosphere": "neutral"}, ("night", "neutral")),
        ({"time_of_day": "night"}, {"time_of_day": "day", "atmosphere": "overcast"}, ("day", "overcast")),
    ],
)
def test_an_existing_map_stores_the_change_whatever_the_order_of_the_rules(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], before: dict[str, object], options: dict[str, object], after: tuple[str, str],
) -> None:
    root = _map(tmp_path, **before)
    _run_map(root, lambda _argv: 0, **options)
    assert (map_time_of_day(root), map_atmosphere(root)) == after
    assert "not started" not in capsys.readouterr().out


def test_run_says_when_the_package_is_out_of_date(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _map(tmp_path)
    _run_map(root, lambda _argv: 0, atmosphere="golden_hour")
    assert "atmosphere is now golden_hour: run again with --from export, then bake a new package\n" in capsys.readouterr().out
    _run_map(root, lambda _argv: 0, atmosphere="golden_hour")
    assert "atmosphere is now" not in capsys.readouterr().out
    _run_map(root, lambda _argv: 0, atmosphere="neutral")
    assert "atmosphere is now neutral: run again with --from export, then bake a new package\n" in capsys.readouterr().out
    _run_map(root, lambda _argv: 0, atmosphere="neutral")
    assert "atmosphere is now" not in capsys.readouterr().out


@pytest.mark.parametrize(
    ("before", "options", "message"),
    [({}, {"atmosphere": "sunset"}, "not 'sunset'"), ({"time_of_day": "night"}, {"atmosphere": "golden_hour"}, DAY_ONLY),
     ({"atmosphere": "golden_hour"}, {"time_of_day": "night"}, DAY_ONLY)],
)
def test_run_refuses_a_bad_preset_on_an_existing_map_without_starting(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], before: dict[str, object], options: dict[str, object], message: str,
) -> None:
    root = _map(tmp_path, **before)
    seen: list[list[str]] = []
    assert _run_map(root, lambda argv: seen.append(argv) or 0, **options) == 1
    assert seen == []
    out = capsys.readouterr().out
    assert "FAILED plan" in out and message in out


def test_a_new_map_is_created_with_the_preset(tmp_path: Path) -> None:
    seen: list[list[str]] = []

    def execute(argv: list[str]) -> int:
        seen.append(argv)
        return 1

    root = tmp_path / "new"
    run_map(map_id="m", name="M", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=150, margin_m=30, out=str(root),
            execute=execute, interactive=False, atmosphere="overcast")
    assert seen[0][seen[0].index("--atmosphere") + 1] == "overcast"
    seen.clear()
    run_map(map_id="m", name="M", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=150, margin_m=30, out=str(root),
            execute=execute, interactive=False)
    assert "--atmosphere" not in seen[0]


@pytest.fixture(scope="module")
def inputs(detail_inputs: tuple[Path, Path, Path], tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, Path]:
    scene, heights, base = detail_inputs
    root = tmp_path_factory.mktemp("atmosphere")
    detail = root / "detail"
    assert main(["detail", "--scene", str(scene), "--heights", str(heights), "--base", str(base), "--out", str(detail), "--no-preview"]) == 0
    layout = root / "layout.json"
    assert main(["perf", "layout", "--scene", str(scene), "--heights", str(heights), "--base", str(base), "--detail", str(detail), "--out", str(layout)]) == 0
    night = root / "night.json"
    document = json.loads(layout.read_bytes())
    night.write_text(json.dumps({**document, "time_of_day": "Night", "profile": "UrbanNight1"}), encoding="utf-8")
    return detail, layout, night


def _package(inputs: tuple[Path, Path, Path], out: Path, **kw: Any):  # type: ignore[no-untyped-def]
    detail, layout, _night = inputs
    return build_package(EXPORT_PROJECT, detail, kw.pop("layout", layout), out, now=FIXED_NOW, **kw)


@pytest.mark.parametrize("atmosphere", [None, "neutral"])
def test_no_preset_or_neutral_keeps_the_package_byte_for_byte(inputs: tuple[Path, Path, Path], tmp_path: Path, atmosphere: str | None) -> None:
    result = _package(inputs, tmp_path / "pkg", atmosphere=atmosphere)
    assert result.manifest["package_id"] == GOLDEN_PACKAGE_ID
    assert hashlib.sha256((result.directory / "adapter_options.json").read_bytes()).hexdigest() == GOLDEN_OPTIONS_SHA256
    assert "atmosphere" not in result.manifest


def test_a_preset_is_recorded_in_the_options_and_the_manifest(inputs: tuple[Path, Path, Path], tmp_path: Path) -> None:
    """The fixture is a perf layout, which has no time of day: the preset reads it as a day map."""
    assert "time_of_day" not in json.loads(inputs[1].read_bytes())
    plain = _package(inputs, tmp_path / "plain")
    golden = _package(inputs, tmp_path / "golden", atmosphere="golden_hour")
    options = json.loads((golden.directory / "adapter_options.json").read_bytes())
    before = json.loads((plain.directory / "adapter_options.json").read_bytes())
    detail = inputs[0]
    street = street_sun((detail / "scene_visual.glb").read_bytes(), (json.loads((detail / "scene_detail.json").read_bytes()).get("core") or {}).get("half_side_m"))
    assert street.axis_deg is not None and street.wall_area_m2 > 0
    lighting = {"sun_elevation_deg": 30.0, "sun_azimuth_deg": street.azimuth_deg, "sun_color": "#FFE0B8", "sun_intensity": 1.4, "sun_shadow_angle_deg": 1.5}
    assert options == {**before, "variant": golden.manifest["package_id"], "atmosphere": "golden_hour", "lighting": lighting}
    assert golden.manifest["package_id"] != plain.manifest["package_id"]
    assert golden.manifest["atmosphere"] == {"preset": "golden_hour", "origin": "generated", "applied": ["sun"],
                                             "sun": {"elevation_deg": 30.0, "azimuth_source": "streets", "axis_deg": street.axis_deg,
                                                     "wall_area_m2": street.wall_area_m2, "azimuth_deg": street.azimuth_deg}}
    overcast = _package(inputs, tmp_path / "overcast_sun", atmosphere="overcast").manifest["atmosphere"]["sun"]
    assert overcast == {"elevation_deg": 55.0, "azimuth_source": "preset", "azimuth_deg": 150.0}
    assert "lighting" not in before
    assert _package(inputs, tmp_path / "overcast", atmosphere="overcast").manifest["package_id"] != golden.manifest["package_id"]


def test_the_package_refuses_an_unknown_preset_or_one_on_a_night_layout(inputs: tuple[Path, Path, Path], tmp_path: Path) -> None:
    with pytest.raises(ExportError) as unknown:
        _package(inputs, tmp_path / "unknown", atmosphere="sunset")
    assert unknown.value.problems == ["atmosphere: atmosphere must be one of neutral, golden_hour, overcast, not 'sunset'"]
    with pytest.raises(ExportError) as night:
        _package(inputs, tmp_path / "night", atmosphere="golden_hour", layout=inputs[2])
    assert night.value.problems == [f"atmosphere: {DAY_ONLY}"]
    assert not (tmp_path / "unknown").exists() and not (tmp_path / "night").exists()
    assert "atmosphere" not in _package(inputs, tmp_path / "neutral_night", atmosphere="neutral", layout=inputs[2]).manifest


def test_the_export_command_takes_the_preset(inputs: tuple[Path, Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    detail, layout, _night = inputs
    args = ["export", "package", "--project", str(EXPORT_PROJECT), "--detail", str(detail), "--layout", str(layout)]
    assert main([*args, "--out", str(tmp_path / "golden"), "--atmosphere", "golden_hour"]) == 0
    sun = json.loads((tmp_path / "golden" / "onward_export.json").read_bytes())["atmosphere"]["sun"]
    assert f"  atmosphere golden_hour (ADR-037 proposed): applies sun; sun at 30 deg, azimuth {sun['azimuth_deg']:g} deg (streets)\n" in capsys.readouterr().out
    assert main([*args, "--out", str(tmp_path / "plain")]) == 0
    assert "atmosphere" not in capsys.readouterr().out
    assert main([*args, "--out", str(tmp_path / "bad"), "--atmosphere", "sunset"]) == 1
    assert "  ERROR atmosphere: atmosphere must be one of" in capsys.readouterr().err


def test_the_adapter_turns_the_sun_only_when_the_package_has_a_lighting_block() -> None:
    """No Unity here: this pins the wiring. A bake of Madrid Sol Arena checked it (plan E3)."""
    from onward_generator.config.validation import REPO_ROOT

    source = (REPO_ROOT / "tools" / "unity" / "onward_adapter" / "Editor" / "OnwardMapAdapter.cs").read_text(encoding="utf-8")
    assert "sun.transform.rotation = Quaternion.Euler(50f, -30f, 0f);\n                ApplySun(light, opt.lighting);\n                ApplyFog(opt);" in source
    body = source[source.index("private static void ApplySun"):source.index("// ADR-014. Unity fog is measured from the camera.")]
    assert "if (lighting == null || lighting.sun_intensity <= 0f) return;" in body
    assert "Quaternion.Euler(lighting.sun_elevation_deg, lighting.sun_azimuth_deg + 180f, 0f)" in body
    assert "light.shadowAngle = lighting.sun_shadow_angle_deg;" in body
