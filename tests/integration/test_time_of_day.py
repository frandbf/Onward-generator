"""A night map from run to layout: profile, street lights and the map setting (ADR-026). No Unity."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

from onward_generator.cli import main
from onward_generator.config.validation import REPO_ROOT
from onward_generator.detail import detail_scene, load_base, load_detail_config
from onward_generator.export.package import _check_layout
from onward_generator.generation import load_generation_config
from onward_generator.maps.workspace import MapError, create_map, load_map, map_time_of_day, store_time_of_day
from onward_generator.perf.layout import LayoutError, playable_layout
from onward_generator.pipeline_run import map_create_argv, plan_steps, run_map
from tests.conftest import MURCIA_CENTER_LAT, MURCIA_CENTER_LON

GEN = load_generation_config()


@pytest.fixture(scope="module")
def layouts(detail_inputs: tuple[Path, Path, Path]) -> dict[str, dict]:
    scene, heights, base = detail_inputs
    generated, ref = load_base(base, scene, heights, GEN)
    manifest = detail_scene(generated, ref, heights, scene, load_detail_config()).manifest()
    levels = json.loads(Path(heights).read_text(encoding="utf-8"))
    return {time: playable_layout(generated, manifest, levels, time_of_day=time) for time in ("day", "night")} | {"manifest": manifest}


def test_a_night_layout_changes_only_the_profile_the_birds_and_the_lights(layouts: dict[str, dict]) -> None:
    day, night = layouts["day"], layouts["night"]
    assert (day["time_of_day"], day["profile"], day["street_lights_m"]) == ("Day", "UrbanDay1", [])
    assert isinstance(day["audio_roof_wind_m"], list)
    assert (night["time_of_day"], night["profile"]) == ("Night", "UrbanNight1")
    assert day["audio_birds_m"] == day["uplink_m"] and night["audio_birds_m"] == []
    lamps = [o for o in layouts["manifest"]["objects"] if o.get("kind") == "street_lamp"]
    assert lamps and len(night["street_lights_m"]) == 3 * len(lamps)
    ignored = {"time_of_day", "profile", "street_lights_m", "audio_birds_m"}
    assert {k: v for k, v in day.items() if k not in ignored} == {k: v for k, v in night.items() if k not in ignored}
    assert _check_layout(day) == [] and _check_layout(night) == []


def test_a_layout_is_a_day_layout_unless_told_otherwise(detail_inputs: tuple[Path, Path, Path], layouts: dict[str, dict]) -> None:
    scene, heights, base = detail_inputs
    generated, _ = load_base(base, scene, heights, GEN)
    levels = json.loads(Path(heights).read_text(encoding="utf-8"))
    assert playable_layout(generated, layouts["manifest"], levels) == layouts["day"]


def test_an_unknown_time_of_day_is_refused(detail_inputs: tuple[Path, Path, Path]) -> None:
    scene, heights, base = detail_inputs
    generated, _ = load_base(base, scene, heights, GEN)
    with pytest.raises(LayoutError, match="time_of_day must be one of day, night, not 'dusk'"):
        playable_layout(generated, {"objects": []}, {}, time_of_day="dusk")


def _map(tmp_path: Path, **options: object) -> Path:
    root = tmp_path / "m"
    create_map(root, map_id="m", name="M", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=150, **options)
    return root


def _layout_step(root: Path) -> list[str]:
    return next(argv for name, argv in plan_steps(root) if name == "layout")


def test_the_map_setting_reaches_the_layout_stage(tmp_path: Path) -> None:
    root = _map(tmp_path, time_of_day="night")
    assert load_map(root)["time_of_day"] == map_time_of_day(root) == "night"
    argv = _layout_step(root)
    assert argv[argv.index("--time-of-day") + 1] == "night"


def test_a_map_is_a_day_map_by_default_and_from_before_the_setting(tmp_path: Path) -> None:
    root = _map(tmp_path)
    assert load_map(root)["time_of_day"] == "day"
    document = load_map(root)
    del document["time_of_day"]
    (root / "map.json").write_text(json.dumps(document), encoding="utf-8")
    assert map_time_of_day(root) == "day"
    assert map_time_of_day(tmp_path / "no_such_map") == "day"


def test_storing_a_time_of_day_says_whether_it_changed(tmp_path: Path) -> None:
    root = _map(tmp_path)
    document = load_map(root)
    del document["time_of_day"]
    (root / "map.json").write_text(json.dumps(document), encoding="utf-8")
    assert store_time_of_day(root, "day") is False  # no key means day
    assert store_time_of_day(root, "day") is False
    assert store_time_of_day(root, "night") is True and map_time_of_day(root) == "night"
    assert store_time_of_day(root, "night") is False
    with pytest.raises(MapError, match="time_of_day must be one of day, night, not 'dusk'"):
        store_time_of_day(root, "dusk")
    assert map_time_of_day(root) == "night"
    with pytest.raises(MapError, match="time_of_day must be one of"):
        _map(tmp_path / "other", time_of_day="dusk")


def test_the_adapter_reads_the_time_of_day_and_lights_the_lamps() -> None:
    """No Unity here: this pins the wiring. Whether it compiles and looks right is the headset checklist of ADR-026."""
    source = (REPO_ROOT / "tools" / "unity" / "onward_adapter" / "Editor" / "OnwardMapAdapter.cs").read_text(encoding="utf-8")
    assert "LevelDescriptor.ETimeOfDay.Day,\n" not in source and "TimeOfDayOf(pkg.Layout)," in source
    assert "ApplyNight(light, pkg.Layout);" in source and "AddStreetLights(pkg.Layout);" in source
    assert "public float[] street_lights_m;" in source
    assert "public float[] audio_roof_wind_m;" in source and "layout.audio_roof_wind_m" in source


def test_run_passes_the_time_of_day_to_map_create_only_when_named() -> None:
    base = {"name": "M", "latitude": 1.0, "longitude": 2.0, "radius_m": 90.0, "margin_m": 5.0}
    assert "--time-of-day" not in map_create_argv(**base)
    argv = map_create_argv(**base, time_of_day="night")
    assert argv[argv.index("--time-of-day") + 1] == "night"


def test_the_run_command_passes_the_time_of_day_and_refuses_another(monkeypatch: pytest.MonkeyPatch) -> None:
    received: dict[str, object] = {}

    def fake_run_map(**kwargs: object) -> int:
        received.update(kwargs)
        return 0

    monkeypatch.setattr("onward_generator.pipeline_run.run_map", fake_run_map)
    args = ["run", "--name", "M", "--lat", str(MURCIA_CENTER_LAT), "--lon", str(MURCIA_CENTER_LON)]
    assert main([*args, "--time-of-day", "night"]) == 0
    assert received["time_of_day"] == "night"
    assert main(args) == 0
    assert received["time_of_day"] is None
    assert main([*args, "--time-of-day", "dusk"]) == 2  # argparse refuses it
    assert received["time_of_day"] is None


def _run_map(root: Path, execute: Callable[[list[str]], int], **options: object) -> int:
    return run_map(map_id="m", name="M", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=150, margin_m=30, out=str(root),
                   execute=execute, interactive=False, **options)


def test_a_new_map_is_created_with_the_time_of_day(tmp_path: Path) -> None:
    seen: list[list[str]] = []

    def execute(argv: list[str]) -> int:
        seen.append(list(argv))
        return 1  # stop at the first step: only its arguments matter here

    assert _run_map(tmp_path / "m", execute, time_of_day="night") == 1
    assert seen[0][seen[0].index("--time-of-day") + 1] == "night"


def test_run_stores_a_new_time_of_day_on_an_existing_map_and_says_what_to_do(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _map(tmp_path)
    (root / "data" / "raw" / "m.osm").write_text("<osm/>", encoding="utf-8")
    hint = "time of day is now night: run again with --from layout"
    assert _run_map(root, lambda argv: 1, time_of_day="night") == 1
    assert map_time_of_day(root) == "night" and hint in capsys.readouterr().out
    assert _run_map(root, lambda argv: 1, time_of_day="night") == 1
    assert hint not in capsys.readouterr().out
    assert _run_map(root, lambda argv: 1) == 1
    assert map_time_of_day(root) == "night"


def test_perf_layout_writes_the_time_it_is_given_and_day_otherwise(detail_inputs: tuple[Path, Path, Path], tmp_path: Path) -> None:
    scene, heights, base = detail_inputs
    detail = tmp_path / "detail"
    assert main(["detail", "--scene", str(scene), "--heights", str(heights), "--base", str(base), "--out", str(detail), "--no-preview"]) == 0
    args = ["perf", "layout", "--scene", str(scene), "--heights", str(heights), "--base", str(base), "--detail", str(detail), "--playable"]

    def layout(name: str, *extra: str) -> dict:
        out = tmp_path / f"{name}.json"
        assert main([*args, "--out", str(out), *extra]) == 0
        return json.loads(out.read_text(encoding="utf-8"))

    night = layout("night", "--time-of-day", "night")
    assert (night["time_of_day"], night["profile"]) == ("Night", "UrbanNight1") and night["street_lights_m"]
    assert night["routes"] and night["ai_m"]  # the whole layout, from the same base and manifest
    day = layout("default")
    assert (day["time_of_day"], day["profile"], day["street_lights_m"]) == ("Day", "UrbanDay1", [])
    assert main([*args, "--out", str(tmp_path / "x.json"), "--time-of-day", "dusk"]) == 2
    assert not (tmp_path / "x.json").exists()


def test_map_create_stores_the_time_of_day_it_is_given(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    downloaded = SimpleNamespace(size_bytes=12, sha256="abc", path="noche.osm")
    monkeypatch.setattr("onward_generator.maps.__main__.download_map_osm", lambda *_args, **_kwargs: downloaded)
    monkeypatch.chdir(tmp_path)
    args = ["map", "create", "--lat", "37.625", "--lon", "-0.986", "--radius-m", "300"]
    assert main([*args, "--name", "Noche", "--time-of-day", "night"]) == 0
    assert map_time_of_day(tmp_path / "maps" / "noche") == "night"
    assert main([*args, "--name", "Dia"]) == 0
    assert map_time_of_day(tmp_path / "maps" / "dia") == "day"
