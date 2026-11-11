"""Headset test packages of the SDK's post-process profiles, Twilight and SkyColor (plan E2, ADR-037). No Unity."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from onward_generator.cli import main
from onward_generator.config.validation import REPO_ROOT
from onward_generator.export.package import EXPORT_CONFIG_DEFAULT, ExportError, build_package, load_export_config
from tests.conftest import FIXED_NOW

EXPORT_PROJECT = REPO_ROOT / "config" / "murcia-belluga.export.json"
ADAPTER = REPO_ROOT / "tools" / "unity" / "onward_adapter" / "Editor" / "OnwardMapAdapter.cs"
PROFILES = ["UrbanDay1", "UrbanDay2", "DesertDay1", "DesertDay2", "ForestDay1", "ForestDay2",
            "UrbanNight1", "UrbanNight2", "DesertNight1", "DesertNight2", "ForestNight1", "ForestNight2"]
REFUSED = "export config: /build/experimental_environment: only for a day gameplay layout (plan E2)"
WARNING = "build.experimental_environment: a test package for the headset (plan E2, ADR-037); do not publish it"


@pytest.fixture(scope="module")
def playable(detail_inputs: tuple[Path, Path, Path], tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """The walled detail, and its day, night and perf layouts."""
    scene, heights, base = detail_inputs
    root = tmp_path_factory.mktemp("experimental")
    detail = root / "detail"
    assert main(["detail", "--scene", str(scene), "--heights", str(heights), "--base", str(base), "--out", str(detail), "--no-preview"]) == 0
    walled = root / "scene_playable"
    assert main(["bounds", "--base", str(detail), "--out", str(walled)]) == 0
    args = ["perf", "layout", "--scene", str(scene), "--heights", str(heights), "--base", str(base), "--detail", str(walled)]
    paths = {"walled": walled, "day": root / "day.json", "night": root / "night.json", "perf": root / "perf.json"}
    assert main([*args, "--playable", "--out", str(paths["day"])]) == 0
    assert main([*args, "--playable", "--time-of-day", "night", "--out", str(paths["night"])]) == 0
    assert main([*args, "--out", str(paths["perf"])]) == 0
    return paths


def _card(tmp_path: Path, name: str, environment: object) -> Path:
    card = json.loads(EXPORT_CONFIG_DEFAULT.read_bytes())
    card["build"]["experimental_environment"] = environment
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(card), encoding="utf-8")
    return path


def _package(playable: dict[str, Path], out: Path, layout: str = "day", card: Path | None = None):  # type: ignore[no-untyped-def]
    return build_package(EXPORT_PROJECT, playable["walled"], playable[layout], out, now=FIXED_NOW, export_config=card)


def test_a_test_package_carries_the_profile_and_is_marked(playable: dict[str, Path], tmp_path: Path) -> None:
    environment = {"time_of_day": "Twilight", "profile": "UrbanDay2", "sky_color": "#E8B890"}
    plain = _package(playable, tmp_path / "plain")
    test = _package(playable, tmp_path / "test", card=_card(tmp_path, "twilight", environment))
    options = json.loads((test.directory / "adapter_options.json").read_bytes())
    assert options["experimental_environment"] == environment
    assert test.manifest["experimental_environment"] == {**environment, "status": "untested"}
    assert WARNING in test.warnings and WARNING in test.manifest["warnings"]
    assert test.manifest["package_id"] != plain.manifest["package_id"]
    assert "experimental_environment" not in plain.manifest and WARNING not in plain.warnings
    assert "experimental_environment" not in json.loads((plain.directory / "adapter_options.json").read_bytes())


def test_the_sky_colour_is_optional(playable: dict[str, Path], tmp_path: Path) -> None:
    test = _package(playable, tmp_path / "test", card=_card(tmp_path, "day2", {"time_of_day": "Day", "profile": "UrbanDay2"}))
    assert test.manifest["experimental_environment"] == {"time_of_day": "Day", "profile": "UrbanDay2", "status": "untested"}


@pytest.mark.parametrize("layout", ["night", "perf"])
def test_a_night_or_perf_layout_refuses_a_test_environment(playable: dict[str, Path], tmp_path: Path, layout: str) -> None:
    card = _card(tmp_path, "day", {"time_of_day": "Day", "profile": "UrbanDay2"})
    with pytest.raises(ExportError) as refused:
        _package(playable, tmp_path / "pkg", layout=layout, card=card)
    assert REFUSED in refused.value.problems
    assert not (tmp_path / "pkg").exists()


@pytest.mark.parametrize("profile", PROFILES)
def test_every_profile_of_the_sdk_is_accepted(tmp_path: Path, profile: str) -> None:
    cfg, _path = load_export_config(_card(tmp_path, "p", {"time_of_day": "Day", "profile": profile}))
    assert cfg["build"]["experimental_environment"]["profile"] == profile


@pytest.mark.parametrize(
    "environment",
    [{"time_of_day": "Night", "profile": "UrbanDay1"}, {"time_of_day": "Day", "profile": "None"},
     {"time_of_day": "Day", "profile": "UrbanDay3"}, {"time_of_day": "Day"}, {"profile": "UrbanDay1"},
     {"time_of_day": "Day", "profile": "UrbanDay1", "sky_color": "E8B890"},
     {"time_of_day": "Day", "profile": "UrbanDay1", "sky_color": "#E8B89"},
     {"time_of_day": "Day", "profile": "UrbanDay1", "extra": 1}, "UrbanDay2", None],
)
def test_the_schema_refuses_a_bad_test_environment(tmp_path: Path, environment: Any) -> None:
    with pytest.raises(ExportError, match=r"/build/experimental_environment"):
        load_export_config(_card(tmp_path, "bad", environment))


def test_the_default_card_has_no_test_environment() -> None:
    assert "experimental_environment" not in json.loads(EXPORT_CONFIG_DEFAULT.read_bytes())["build"]


def test_the_adapter_applies_the_test_environment_after_the_layout() -> None:
    """No Unity here: this pins the wiring, which a headset session checks (plan E4)."""
    source = ADAPTER.read_text(encoding="utf-8")
    assert "public ExperimentalEnvironment experimental_environment = new ExperimentalEnvironment();" in source
    assert "ApplyEnvironment(shared, pkg.Layout);\n                ApplyExperimentalEnvironment(shared, opt);" in source
    assert "shared.SkyColor = colour;" in source
    assert "TimeOfDayFor(pkg)," in source and "TimeOfDayOf(pkg.Layout)," not in source
    body = source[source.index("private static void ApplyExperimentalEnvironment"):source.index("private static LevelDescriptor.ETimeOfDay TimeOfDayFor")]
    assert "if (!HasExperimentalEnvironment(opt)) return;" in body
    assert "shared.PPProfileId = (LevelDescriptor.EProfileId)Enum.Parse(typeof(LevelDescriptor.EProfileId), test.profile);" in body
