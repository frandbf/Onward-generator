"""A night map from run to layout: profile, street lights and the map setting (ADR-026). No Unity."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from onward_generator.config.validation import REPO_ROOT
from onward_generator.detail import detail_scene, load_base, load_detail_config
from onward_generator.export.package import _check_layout
from onward_generator.generation import load_generation_config
from onward_generator.maps.workspace import MapError, create_map, load_map, map_time_of_day, store_time_of_day
from onward_generator.perf.layout import LayoutError, playable_layout
from onward_generator.pipeline_run import map_create_argv, plan_steps
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
    assert (night["time_of_day"], night["profile"]) == ("Night", "UrbanNight1")
    assert day["audio_birds_m"] and night["audio_birds_m"] == []
    lamps = [o for o in layouts["manifest"]["objects"] if o.get("kind") == "street_lamp"]
    assert lamps and len(night["street_lights_m"]) == 3 * len(lamps)
    ignored = {"time_of_day", "profile", "street_lights_m", "audio_birds_m"}
    assert {k: v for k, v in day.items() if k not in ignored} == {k: v for k, v in night.items() if k not in ignored}
    assert _check_layout(day) == [] and _check_layout(night) == []


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


def test_run_passes_the_time_of_day_to_map_create_only_when_named() -> None:
    base = {"name": "M", "latitude": 1.0, "longitude": 2.0, "radius_m": 90.0, "margin_m": 5.0}
    assert "--time-of-day" not in map_create_argv(**base)
    argv = map_create_argv(**base, time_of_day="night")
    assert argv[argv.index("--time-of-day") + 1] == "night"
