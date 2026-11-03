"""A storm from the command to the layout: the setting, the night-only rule and the package check (ADR-028). No Unity."""

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
from onward_generator.maps.workspace import MapError, create_map, load_map, map_storm, map_time_of_day, store_storm, store_time_of_day
from onward_generator.perf.layout import LayoutError, playable_layout
from onward_generator.pipeline_run import map_create_argv, plan_steps, run_map
from tests.conftest import MURCIA_CENTER_LAT, MURCIA_CENTER_LON

GEN = load_generation_config()


def _map(tmp_path: Path, **options: object) -> Path:
    root = tmp_path / "m"
    create_map(root, map_id="m", name="M", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=150, **options)
    return root


def _layout_step(root: Path) -> list[str]:
    return next(argv for name, argv in plan_steps(root) if name == "layout")


def test_a_storm_adds_only_the_lightning_to_a_night_layout(detail_inputs: tuple[Path, Path, Path]) -> None:
    scene, heights, base = detail_inputs
    generated, ref = load_base(base, scene, heights, GEN)
    manifest = detail_scene(generated, ref, heights, scene, load_detail_config()).manifest()
    levels = json.loads(Path(heights).read_text(encoding="utf-8"))
    calm = playable_layout(generated, manifest, levels, time_of_day="night")
    storm = playable_layout(generated, manifest, levels, time_of_day="night", storm=True)
    assert calm["lightning"] == 0 and storm["lightning"] == 1
    assert {**storm, "lightning": 0} == calm
    assert _check_layout(storm) == []
    assert playable_layout(generated, manifest, levels)["lightning"] == 0
    with pytest.raises(LayoutError) as refused:
        playable_layout(generated, manifest, levels, storm=True)
    assert str(refused.value) == "storm needs a night map: a daytime flash looks wrong (ADR-028)"


def _night_layout() -> dict[str, object]:
    return {"schema": "onward_generator.gameplay_layout", "time_of_day": "Night", "lightning": 1}


@pytest.mark.parametrize("lightning", [2, -1, True, 1.0, "1", None, [1]])
def test_the_package_refuses_a_lightning_that_is_not_0_or_1(lightning: object) -> None:
    assert "layout: lightning must be 0 or 1" in _check_layout({**_night_layout(), "lightning": lightning})


def test_the_package_refuses_lightning_by_day_and_accepts_its_absence() -> None:
    assert "layout: lightning is only for a night map" in _check_layout({**_night_layout(), "time_of_day": "Day"})
    day_without = _check_layout({"schema": "onward_generator.gameplay_layout", "time_of_day": "Day", "lightning": 0})
    missing = _check_layout({"schema": "onward_generator.gameplay_layout", "time_of_day": "Day"})
    assert not [p for p in day_without + missing if "lightning" in p]
    assert not [p for p in _check_layout(_night_layout()) if "lightning" in p]


def test_the_map_setting_reaches_the_layout_stage(tmp_path: Path) -> None:
    root = _map(tmp_path, time_of_day="night", storm=True)
    assert load_map(root)["storm"] is True and map_storm(root) is True
    assert "--storm" in _layout_step(root)


def test_a_map_has_no_storm_by_default_or_from_before_the_setting(tmp_path: Path) -> None:
    root = _map(tmp_path)
    assert load_map(root)["storm"] is False
    assert "--storm" not in _layout_step(root)
    document = load_map(root)
    del document["storm"]
    (root / "map.json").write_text(json.dumps(document), encoding="utf-8")
    assert map_storm(root) is False
    assert map_storm(tmp_path / "no_such_map") is False


def test_a_day_map_cannot_have_a_storm(tmp_path: Path) -> None:
    with pytest.raises(MapError) as refused:
        _map(tmp_path, storm=True)
    assert str(refused.value) == "storm needs a night map: a daytime flash looks wrong (ADR-028)"
    root = _map(tmp_path)
    with pytest.raises(MapError, match="storm needs a night map"):
        store_storm(root, True)
    assert map_storm(root) is False


def test_storing_a_storm_says_whether_it_changed(tmp_path: Path) -> None:
    root = _map(tmp_path, time_of_day="night")
    assert store_storm(root, False) is False
    assert store_storm(root, True) is True and map_storm(root) is True
    assert store_storm(root, True) is False
    assert store_storm(root, False) is True and map_storm(root) is False


def test_a_storm_map_cannot_turn_to_day_and_keep_the_storm(tmp_path: Path) -> None:
    root = _map(tmp_path, time_of_day="night", storm=True)
    with pytest.raises(MapError, match="storm needs a night map"):
        store_time_of_day(root, "day")
    assert map_time_of_day(root) == "night"
    assert store_time_of_day(root, "night") is False


def test_map_create_stores_the_storm_it_is_given(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    downloaded = SimpleNamespace(size_bytes=12, sha256="abc", path="tormenta.osm")
    monkeypatch.setattr("onward_generator.maps.__main__.download_map_osm", lambda *_args, **_kwargs: downloaded)
    monkeypatch.chdir(tmp_path)
    args = ["map", "create", "--lat", "37.625", "--lon", "-0.986", "--radius-m", "300", "--time-of-day", "night"]
    assert main([*args, "--name", "Tormenta", "--storm"]) == 0
    assert map_storm(tmp_path / "maps" / "tormenta") is True
    assert main([*args, "--name", "Calma"]) == 0
    assert map_storm(tmp_path / "maps" / "calma") is False


def test_run_passes_the_storm_to_map_create_only_when_named() -> None:
    base = {"name": "M", "latitude": 1.0, "longitude": 2.0, "radius_m": 90.0, "margin_m": 5.0}
    assert "--storm" not in map_create_argv(**base)
    assert "--storm" in map_create_argv(**base, time_of_day="night", storm=True)


@pytest.mark.parametrize(("flags", "expected"), [([], None), (["--storm"], True), (["--no-storm"], False)])
def test_the_run_command_passes_on_off_or_unset(monkeypatch: pytest.MonkeyPatch, flags: list[str], expected: bool | None) -> None:
    received: dict[str, object] = {}

    def fake_run_map(**kwargs: object) -> int:
        received.update(kwargs)
        return 0

    monkeypatch.setattr("onward_generator.pipeline_run.run_map", fake_run_map)
    assert main(["run", "--name", "M", "--lat", str(MURCIA_CENTER_LAT), "--lon", str(MURCIA_CENTER_LON), *flags]) == 0
    assert received["storm"] is expected


def _run_map(root: Path, execute: Callable[[list[str]], int], **options: object) -> int:
    return run_map(map_id="m", name="M", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=150, margin_m=30, out=str(root),
                   execute=execute, interactive=False, start="layout", **options)


@pytest.mark.parametrize(
    ("before", "options", "after"),
    [
        ({"time_of_day": "night"}, {"storm": True}, ("night", True)),
        ({"time_of_day": "night", "storm": True}, {"storm": False}, ("night", False)),
        ({"time_of_day": "night", "storm": True}, {}, ("night", True)),
        ({"time_of_day": "day"}, {"time_of_day": "night", "storm": True}, ("night", True)),
        ({"time_of_day": "night", "storm": True}, {"time_of_day": "day", "storm": False}, ("day", False)),
    ],
)
def test_an_existing_map_stores_the_change_whatever_the_order_of_the_rules(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], before: dict[str, object], options: dict[str, object], after: tuple[str, bool],
) -> None:
    root = _map(tmp_path, **before)
    _run_map(root, lambda _argv: 0, **options)
    assert (load_map(root)["time_of_day"], load_map(root)["storm"]) == after
    assert "not started" not in capsys.readouterr().out


def test_run_says_when_the_layout_is_out_of_date(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _map(tmp_path, time_of_day="night")
    _run_map(root, lambda _argv: 0, storm=True)
    assert "storm is on: run again with --from layout, then bake a new package\n" in capsys.readouterr().out
    _run_map(root, lambda _argv: 0, storm=False)
    assert "no storm now: run again with --from layout, then bake a new package\n" in capsys.readouterr().out
    _run_map(root, lambda _argv: 0, storm=False)
    assert "no storm now" not in capsys.readouterr().out


def test_a_new_map_is_created_with_the_storm(tmp_path: Path) -> None:
    seen: list[list[str]] = []

    def execute(argv: list[str]) -> int:
        seen.append(argv)
        return 1

    root = tmp_path / "new"
    run_map(map_id="m", name="M", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=150, margin_m=30, out=str(root),
            execute=execute, interactive=False, time_of_day="night", storm=True)
    assert "--storm" in seen[0]
    seen.clear()
    run_map(map_id="m", name="M", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=150, margin_m=30, out=str(root),
            execute=execute, interactive=False, storm=None)
    assert "--storm" not in seen[0]


@pytest.mark.parametrize(
    ("before", "options"),
    [({"time_of_day": "night", "storm": True}, {"time_of_day": "day"}), ({"time_of_day": "day"}, {"storm": True})],
)
def test_run_refuses_a_storm_by_day_on_an_existing_map_without_starting(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], before: dict[str, object], options: dict[str, object],
) -> None:
    root = _map(tmp_path, **before)
    seen: list[list[str]] = []
    assert _run_map(root, lambda argv: seen.append(argv) or 0, **options) == 1
    assert seen == []
    out = capsys.readouterr().out
    assert "FAILED plan" in out and "storm needs a night map" in out


def test_the_adapter_places_the_lightning_prefab_only_when_the_layout_asks() -> None:
    """No Unity here: this pins the wiring. Whether it compiles and looks right is the headset checklist of ADR-028."""
    source = (REPO_ROOT / "tools" / "unity" / "onward_adapter" / "Editor" / "OnwardMapAdapter.cs").read_text(encoding="utf-8")
    assert "public int lightning;" in source
    assert "if (layout.lightning != 0)" in source and 'InstantiatePrefab("LightningSystem.prefab")' in source
    assert "storm.LightningChance = 1f;" in source and "storm.LightningChanceLight = 1f;" in source


def test_perf_layout_writes_the_lightning_only_when_asked(detail_inputs: tuple[Path, Path, Path], tmp_path: Path) -> None:
    scene, heights, base = detail_inputs
    detail = tmp_path / "detail"
    assert main(["detail", "--scene", str(scene), "--heights", str(heights), "--base", str(base), "--out", str(detail), "--no-preview"]) == 0
    args = ["perf", "layout", "--scene", str(scene), "--heights", str(heights), "--base", str(base), "--detail", str(detail), "--playable", "--time-of-day", "night"]

    def layout(name: str, *extra: str) -> dict:
        out = tmp_path / f"{name}.json"
        assert main([*args, "--out", str(out), *extra]) == 0
        return json.loads(out.read_text(encoding="utf-8"))

    assert layout("storm", "--storm")["lightning"] == 1
    assert layout("calm")["lightning"] == 0
