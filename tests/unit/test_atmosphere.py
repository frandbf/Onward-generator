"""Atmosphere presets: the presets file, its schema and the day-only rule (ADR-037, proposed)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from onward_generator.export.atmosphere import (
    NEUTRAL,
    NEUTRAL_SUN_AZIMUTH_DEG,
    PRESETS_DEFAULT,
    AtmosphereError,
    check_atmosphere,
    lighting,
    load_presets,
    preset_names,
)


def _document() -> dict[str, Any]:
    return json.loads(PRESETS_DEFAULT.read_bytes())


def _write(tmp_path: Path, document: object) -> Path:
    path = tmp_path / "atmosphere.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_the_default_file_has_the_two_presets_of_the_plan() -> None:
    presets = load_presets()
    assert sorted(presets) == ["golden_hour", "overcast"]
    golden = presets["golden_hour"]
    assert golden["sun"] == {"elevation_deg": 30, "azimuth": "streets", "color": "#FFE0B8", "intensity": 1.4, "shadow_angle_deg": 1.5}
    assert golden["ambient"] == {"mode": "trilight", "sky": "#8A9BB3", "equator": "#9A9080", "ground": "#5A5044"}
    assert presets["overcast"]["ambient"] == {"mode": "flat", "sky": "#9AA3AD"}
    assert presets["overcast"]["sun"]["azimuth"] == 150


def test_the_presets_cannot_be_changed_through_the_mapping() -> None:
    with pytest.raises(TypeError):
        load_presets()["golden_hour"] = {}  # type: ignore[index]


def test_neutral_comes_first_and_the_rest_in_name_order(tmp_path: Path) -> None:
    assert preset_names() == ("neutral", "golden_hour", "overcast")
    document = _document()
    document["presets"] = {"zeta": document["presets"]["overcast"], "alpha": document["presets"]["golden_hour"]}
    assert preset_names(_write(tmp_path, document)) == ("neutral", "alpha", "zeta")


@pytest.mark.parametrize(("time_of_day", "name"), [("day", "neutral"), ("day", "golden_hour"), ("day", "overcast"), ("night", "neutral")])
def test_a_known_preset_on_a_day_map_and_neutral_anywhere_are_accepted(time_of_day: str, name: str) -> None:
    assert check_atmosphere(name, time_of_day) == name


def test_an_unknown_preset_is_refused_with_the_names() -> None:
    with pytest.raises(AtmosphereError) as refused:
        check_atmosphere("sunset", "day")
    assert str(refused.value) == "atmosphere must be one of neutral, golden_hour, overcast, not 'sunset'"


@pytest.mark.parametrize("time_of_day", ["night", "Day", ""])
def test_a_preset_needs_a_day_map(time_of_day: str) -> None:
    with pytest.raises(AtmosphereError) as refused:
        check_atmosphere("golden_hour", time_of_day)
    assert str(refused.value) == "atmosphere golden_hour needs a day map: a preset sets the sun, and a night map has the moon (ADR-037)"


def test_the_check_reads_the_file_it_is_given(tmp_path: Path) -> None:
    document = _document()
    document["presets"] = {"dusk": document["presets"]["golden_hour"]}
    path = _write(tmp_path, document)
    assert check_atmosphere("dusk", "day", path) == "dusk"
    with pytest.raises(AtmosphereError, match="one of neutral, dusk, not 'golden_hour'"):
        check_atmosphere("golden_hour", "day", path)


def _set(document: dict[str, Any], dotted: str, value: object) -> None:
    *path, last = dotted.split(".")
    node = document
    for key in path:
        node = node[key]
    if value is _DELETE:
        del node[last]
    else:
        node[last] = value


_DELETE = object()
_G = "presets.golden_hour"
_O = "presets.overcast"


@pytest.mark.parametrize(
    ("dotted", "value", "where"),
    [
        ("version", "0.2.0", "/version"),
        ("extra", 1, "/"),
        ("presets", {}, "/presets"),
        (f"{_G}.sun.elevation_deg", 4, "/presets/golden_hour/sun/elevation_deg"),
        (f"{_G}.sun.elevation_deg", 90, "/presets/golden_hour/sun/elevation_deg"),
        (f"{_G}.sun.azimuth", "north", "/presets/golden_hour/sun/azimuth"),
        (f"{_G}.sun.azimuth", 361, "/presets/golden_hour/sun/azimuth"),
        (f"{_G}.sun.color", "#FFE0B", "/presets/golden_hour/sun/color"),
        (f"{_G}.sun.color", "FFE0B8", "/presets/golden_hour/sun/color"),
        (f"{_G}.sun.intensity", 0, "/presets/golden_hour/sun/intensity"),
        (f"{_G}.sun.intensity", 3.1, "/presets/golden_hour/sun/intensity"),
        (f"{_G}.sun.shadow_angle_deg", -1, "/presets/golden_hour/sun/shadow_angle_deg"),
        (f"{_G}.sun.shadow_angle_deg", 21, "/presets/golden_hour/sun/shadow_angle_deg"),
        (f"{_G}.sun.extra", 1, "/presets/golden_hour/sun"),
        (f"{_G}.ambient.mode", "skybox", "/presets/golden_hour/ambient"),
        (f"{_G}.ambient.equator", _DELETE, "/presets/golden_hour/ambient"),
        (f"{_G}.ambient.ground", _DELETE, "/presets/golden_hour/ambient"),
        (f"{_O}.ambient.equator", "#000000", "/presets/overcast/ambient"),
        (f"{_O}.ambient.ground", "#000000", "/presets/overcast/ambient"),
        (f"{_G}.ao.max_distance_m", 0, "/presets/golden_hour/ao/max_distance_m"),
        (f"{_G}.ao.max_distance_m", 10.5, "/presets/golden_hour/ao/max_distance_m"),
        (f"{_G}.ao.exponent_direct", -0.1, "/presets/golden_hour/ao/exponent_direct"),
        (f"{_G}.ao.exponent_indirect", 10.5, "/presets/golden_hour/ao/exponent_indirect"),
        (f"{_G}.fog.start_m", -1, "/presets/golden_hour/fog/start_m"),
        (f"{_G}.fog.end_m", 0, "/presets/golden_hour/fog/end_m"),
        (f"{_G}.fog.end_m", 1001, "/presets/golden_hour/fog/end_m"),
        (f"{_G}.lightmap_texels_per_m_core", 0, "/presets/golden_hour/lightmap_texels_per_m_core"),
        (f"{_G}.lightmap_texels_per_m_core", 41, "/presets/golden_hour/lightmap_texels_per_m_core"),
        (f"{_G}.grime", -0.1, "/presets/golden_hour/grime"),
        (f"{_G}.grime", 2.1, "/presets/golden_hour/grime"),
        (f"{_G}.wall_desaturation", 0.6, "/presets/golden_hour/wall_desaturation"),
        (f"{_G}.wall_value_max", 0.4, "/presets/golden_hour/wall_value_max"),
        (f"{_G}.wall_value_max", 1.1, "/presets/golden_hour/wall_value_max"),
        (f"{_G}.description", "", "/presets/golden_hour/description"),
        (f"{_G}.fog", _DELETE, "/presets/golden_hour"),
    ],
)
def test_the_schema_refuses_each_bad_value(tmp_path: Path, dotted: str, value: object, where: str) -> None:
    document = copy.deepcopy(_document())
    _set(document, dotted, value)
    with pytest.raises(AtmosphereError) as refused:
        load_presets(_write(tmp_path, document))
    assert str(refused.value).startswith(f"{tmp_path / 'atmosphere.json'}: {where}: ")


@pytest.mark.parametrize("name", ["neutral", "Golden", "9am", "a" * 33])
def test_a_preset_name_must_be_a_lower_case_id_other_than_neutral(tmp_path: Path, name: str) -> None:
    document = _document()
    document["presets"][name] = document["presets"]["overcast"]
    with pytest.raises(AtmosphereError, match=r": /presets: "):
        load_presets(_write(tmp_path, document))


def test_the_schema_accepts_the_boundaries(tmp_path: Path) -> None:
    document = _document()
    golden = document["presets"]["golden_hour"]
    golden["sun"].update(elevation_deg=5, azimuth=-360, intensity=3, shadow_angle_deg=20)
    golden["ao"].update(max_distance_m=10, exponent_indirect=0, exponent_direct=10)
    golden.update(lightmap_texels_per_m_core=40, grime=2, wall_desaturation=0.5, wall_value_max=0.5)
    golden["fog"].update(start_m=0, end_m=1000)
    document["presets"]["overcast"]["sun"].update(elevation_deg=89, azimuth=360)
    document["presets"]["overcast"].update(grime=0, wall_desaturation=0, wall_value_max=1)
    assert load_presets(_write(tmp_path, document))["golden_hour"]["sun"]["elevation_deg"] == 5


def test_a_missing_or_broken_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(AtmosphereError, match=r"missing\.json: "):
        load_presets(tmp_path / "missing.json")
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    with pytest.raises(AtmosphereError, match=r"broken\.json: "):
        load_presets(broken)


def test_neutral_is_the_name_of_no_preset() -> None:
    assert NEUTRAL == "neutral" and NEUTRAL not in load_presets()


def test_the_neutral_sun_stands_opposite_the_heading_of_the_neutral_light() -> None:
    """The adapter's light points toward heading -30; the sun it stands for is at 150 degrees."""
    assert NEUTRAL_SUN_AZIMUTH_DEG == 150.0


def test_the_lighting_block_carries_the_sun_of_the_preset() -> None:
    block, record = lighting(load_presets()["overcast"])
    assert block == {"sun_elevation_deg": 55.0, "sun_azimuth_deg": 150.0, "sun_color": "#DDE3EA", "sun_intensity": 0.7,
                     "sun_shadow_angle_deg": 8.0, "ambient_mode": "flat", "ambient_sky_color": "#9AA3AD",
                     "ao_max_distance_m": 1.5, "ao_exponent_indirect": 1.0, "ao_exponent_direct": 0.0}
    assert record == {"elevation_deg": 55.0, "azimuth_deg": 150.0, "azimuth_source": "preset"}
    assert all(type(v) is float for k, v in block.items() if not k.endswith(("_color", "_mode")))


def test_a_street_azimuth_keeps_the_neutral_one_until_the_street_axis_is_measured() -> None:
    block, record = lighting(load_presets()["golden_hour"])
    assert block == {"sun_elevation_deg": 30.0, "sun_azimuth_deg": 150.0, "sun_color": "#FFE0B8", "sun_intensity": 1.4,
                     "sun_shadow_angle_deg": 1.5, "ambient_mode": "trilight", "ambient_sky_color": "#8A9BB3",
                     "ambient_equator_color": "#9A9080", "ambient_ground_color": "#5A5044",
                     "ao_max_distance_m": 1.5, "ao_exponent_indirect": 1.0, "ao_exponent_direct": 0.2}
    assert record == {"elevation_deg": 30.0, "azimuth_deg": 150.0, "azimuth_source": "streets_pending"}


def test_an_integer_azimuth_becomes_a_float() -> None:
    preset = {"sun": {"elevation_deg": 40, "azimuth": 200, "color": "#FFFFFF", "intensity": 1, "shadow_angle_deg": 0},
              "ambient": {"mode": "flat", "sky": "#808080"}, "ao": {"max_distance_m": 2, "exponent_indirect": 1, "exponent_direct": 0}}
    block, record = lighting(preset)
    assert block["sun_azimuth_deg"] == 200.0 and type(block["sun_azimuth_deg"]) is float
    assert (block["sun_elevation_deg"], block["sun_intensity"], block["sun_shadow_angle_deg"]) == (40.0, 1.0, 0.0)
    assert [type(block[k]) for k in ("ao_max_distance_m", "ao_exponent_indirect", "ao_exponent_direct")] == [float, float, float]
    assert (block["ao_max_distance_m"], block["ao_exponent_indirect"], block["ao_exponent_direct"]) == (2.0, 1.0, 0.0)
    assert record["azimuth_source"] == "preset"
