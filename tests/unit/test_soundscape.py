"""Soundscape by mood (plan 2026-11-17-efectos-sdk, increment D; ADR-046)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from onward_generator.export import soundscape as sound
from onward_generator.export.soundscape import (
    CONFIG_PATH,
    SCHEMA_PATH,
    SoundscapeError,
    Source,
    check_choice,
    flies_sites,
    load_soundscape,
    parse_soundscape,
    pick,
    pole_sites,
    resolve_mode,
    soundscape_block,
)
from onward_generator.maps.workspace import MapError, soundscape
from onward_generator.next_step import _soundscape

ADAPTER = Path(__file__).resolve().parents[2] / "tools" / "unity" / "onward_adapter" / "Editor" / "OnwardMapAdapter.cs"
SDK_STRINGS = Path.home() / "Unity" / "OnwardPhase0Project" / "Assets" / "OnwardCustomContent" / "Internal" / "Scripts" / "Data" / "OnwardAudioDataStrings.cs"


def _doc() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _wreck(i: int, x: float, y: float, z: float = 0.0) -> dict[str, Any]:
    return {"id": f"overgrow/wreck/{i}", "type": "overgrowth", "kind": "wreck", "position_m": [x, y, z]}


def _pole(i: int, x: float, y: float) -> dict[str, Any]:
    return {"id": f"overgrow/power_pole/{i}", "type": "overgrowth", "kind": "power_pole", "role": "pole", "position_m": [x, y, 0.0]}


def test_the_configuration_names_sdk_clips_by_mode_and_style() -> None:
    config = load_soundscape()
    assert config.globals_ == {"regional": None, "war": "Distant Combat Sparse", "battle": "Distant Combat Dense"}
    assert config.locals_ == {"regional": ("generator",), "war": ("generator", "fire", "flies"), "battle": ("generator", "fire", "flies")}
    assert config.regional == {"east_african": ("Birds African Bush 1", "Wind Desert"), "arid": (None, "Wind Desert")}
    assert config.sources["fire"] == Source("Fire", 5, 0.0, 0.0, 0.5)
    assert config.sources["flies"] == Source("Flies", 4, 20.0, 10.0, 0.8)
    assert config.sources["generator"] == Source("Engine Generator", 1, 0.0, 15.0, 1.0)
    assert config.flies_kinds == frozenset({"wreck", "waste"})


def test_every_clip_of_the_schema_is_one_the_sdk_ships() -> None:
    if not SDK_STRINGS.is_file():
        pytest.skip("the Onward SDK is not installed here")
    shipped = SDK_STRINGS.read_text(encoding="utf-8")
    for clip in _schema()["$defs"]["clip"]["enum"]:
        assert f'"{clip}"' in shipped, clip


@pytest.mark.parametrize(("path", "value", "message"), [
    (("modes", "war", "global"), "Gunfire", "modes/war/global: 'Gunfire' is not valid under any of the given schemas"),
    (("local", "flies", "max"), 0, "local/flies/max: 0 is less than the minimum of 1"),
    (("regional", "east_african", "wind"), "Wind Storm", "regional/east_african/wind: 'Wind Storm' is not valid under any of the given schemas"),
    (("modes", "war", "local"), ["rain"], "modes/war/local/0: 'rain' is not one of ['fire', 'flies', 'generator']"),
])
def test_a_bad_configuration_is_refused_with_its_path(path: tuple[str, ...], value: object, message: str) -> None:
    doc = _doc()
    target = doc
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(SoundscapeError) as caught:
        parse_soundscape(doc, _schema(), "here")
    assert str(caught.value) == f"here: {message}"


def test_a_configuration_that_is_not_an_object_is_refused_at_the_root() -> None:
    with pytest.raises(SoundscapeError, match=r"^here: \[\] is not of type 'object'$"):
        parse_soundscape([], _schema(), "here")


def test_an_unreadable_configuration_is_refused(tmp_path: Path) -> None:
    broken = tmp_path / "soundscape.json"
    broken.write_text("{", encoding="utf-8")
    with pytest.raises(SoundscapeError, match=r"soundscape\.json: Expecting property name"):
        load_soundscape.__wrapped__(broken)
    with pytest.raises(SoundscapeError, match=r"missing\.json: \[Errno 2\]"):
        load_soundscape.__wrapped__(tmp_path / "missing.json")


@pytest.mark.parametrize(("value", "expected"), [(None, "auto"), ("auto", "auto"), ("default", "default"), ("regional", "regional"),
                                                 ("war", "war"), ("battle", "battle")])
def test_the_choice_is_one_of_the_modes_or_auto(value: object, expected: str) -> None:
    assert check_choice(value) == expected


@pytest.mark.parametrize("value", ["War", "", 1, True, ["war"]])
def test_a_bad_choice_is_refused(value: object) -> None:
    with pytest.raises(SoundscapeError, match=r'^map.json "soundscape" must be one of auto, default, regional, war, battle, got '):
        check_choice(value)


@pytest.mark.parametrize(("choice", "damage", "mode"), [
    ("auto", "heavy", "war"), ("auto", "light", "regional"), ("auto", "none", "regional"),
    ("default", "heavy", "default"), ("battle", "none", "battle"), ("regional", "heavy", "regional"),
])
def test_auto_follows_the_war_damage_and_a_named_mode_wins(choice: str, damage: str, mode: str) -> None:
    assert resolve_mode(choice, damage) == mode


def test_a_site_stands_inside_the_core_clear_of_spawns_and_apart_each_bound_included() -> None:
    source = Source("Flies", 8, 20.0, 10.0, 0.8)
    spawns = [(0.0, 0.0)]
    assert pick([("edge", 50.0, 30.0, 0.0), ("out", 50.001, 0.0, 0.0)], source, 50.0, spawns) == [(50.0, 30.0, 0.0)]
    assert pick([("clear", 0.0, 10.0, 0.0), ("near", 0.0, -9.999, 0.0)], source, 50.0, spawns) == [(0.0, 10.0, 0.0)]
    assert len(pick([("out", 50.001, 0.0, 0.0)], source, None, spawns)) == 1  # no core: anywhere counts
    both = pick([("a", 20.0, 20.0, 0.0), ("b", 40.0, 20.0, 0.0)], source, 50.0, spawns)  # exactly 20 m apart
    assert sorted(both) == [(20.0, 20.0, 0.0), (40.0, 20.0, 0.0)]
    assert len(pick([("a", 20.0, 20.0, 0.0), ("b", 39.999, 20.0, 0.0)], source, 50.0, spawns)) == 1


def test_the_order_is_fixed_by_the_ids_and_the_count_caps_it() -> None:
    sites = [(f"s{i}", float(10 * i), 40.0, 0.0) for i in range(6)]
    one = pick(sites, Source("Fire", 2, 0.0, 0.0, 0.5), None, [])
    assert one == pick(list(reversed(sites)), Source("Fire", 2, 0.0, 0.0, 0.5), None, [])
    assert len(one) == 2 and one == [(x, y, z) for _i, x, y, z in sound._ordered(sites)[:2]]


def test_flies_buzz_over_wrecks_and_the_listed_kinds_only() -> None:
    objects = [
        _wreck(1, 1.0, 2.0, 0.5),
        {"id": "overgrow/vehicles/3", "type": "overgrowth", "kind": "vehicles", "role": "wreck", "position_m": [3.0, 4.0]},
        {"id": "overgrow/waste/1", "type": "overgrowth", "kind": "waste", "role": "barrel", "position_m": [5.0, 6.0, 0.25]},
        {"id": "overgrow/waste/2", "type": "overgrowth", "kind": "waste", "role": "crate", "position_m": [7.0, 8.0]},
        {"id": "overgrow/waste/3", "type": "overgrowth", "kind": "waste"},                       # no place
        {"id": "overgrow/tree/1", "type": "overgrowth", "kind": "tree", "position_m": [9.0, 9.0]},
        {"id": "waste_basket/1", "type": "urban_element", "kind": "waste", "position_m": [1.0, 1.0]},
    ]
    assert flies_sites(objects, frozenset({"wreck", "waste"})) == [
        ("overgrow/wreck/1", 1.0, 2.0, 0.5), ("overgrow/vehicles/3", 3.0, 4.0, 0.0),
        ("overgrow/waste/1", 5.0, 6.0, 0.25), ("overgrow/waste/2", 7.0, 8.0, 0.0),
    ]
    assert flies_sites(objects, frozenset({"wreck"})) == [("overgrow/wreck/1", 1.0, 2.0, 0.5), ("overgrow/vehicles/3", 3.0, 4.0, 0.0)]


def test_the_generator_stands_by_a_power_pole() -> None:
    objects = [_pole(1, 1.0, 2.0), {"id": "p2", "kind": "power_pole", "position_m": [3.0, 4.0, 0.5]}, {"id": "p3", "kind": "power_pole"},
               {"id": "w", "kind": "power_line", "position_m": [0.0, 0.0]}]
    assert pole_sites(objects) == [("overgrow/power_pole/1", 1.0, 2.0, 0.0), ("p2", 3.0, 4.0, 0.5)]


LAYOUT = {"attackers": {"points_m": [{"x": 0.0, "y": 0.0}]}}


def test_default_changes_nothing() -> None:
    block, record = soundscape_block("default", "east_african", load_soundscape(), [_pole(1, 40.0, 40.0)], [], 100.0, LAYOUT)
    assert block is None and record == {"mode": "default", "style": "east_african"}


def test_regional_without_a_row_or_a_pole_changes_nothing() -> None:
    block, record = soundscape_block("regional", "mediterranean", load_soundscape(), [_wreck(1, 40.0, 40.0)], [], 100.0, LAYOUT)
    assert block is None
    assert record == {"mode": "regional", "style": "mediterranean", "global": None, "birds": None, "wind": None, "sources": {"generator": 0}}


def test_regional_swaps_the_style_s_clips_and_sets_a_generator() -> None:
    objects = [_pole(1, 40.0, 40.0), _pole(2, 5.0, 5.0), _wreck(1, 60.0, 60.0)]
    block, record = soundscape_block("regional", "east_african", load_soundscape(), objects, [{"x": 1.0, "y": 1.0, "z": 1.0, "seed": 1}], 100.0, LAYOUT)
    assert block == {"mode": "regional", "global": None, "birds": "Birds African Bush 1", "wind": "Wind Desert",
                     "sources": [{"name": "Generator_1", "clip": "Engine Generator", "x": 40.0, "y": 40.0, "z": 1.0}]}
    assert record["sources"] == {"generator": 1} and record["birds"] == "Birds African Bush 1"


def test_a_pole_alone_is_enough_for_a_regional_block_in_any_style() -> None:
    block, _ = soundscape_block("regional", "mediterranean", load_soundscape(), [_pole(1, 40.0, 40.0)], [], 100.0, LAYOUT)
    assert block is not None and block["birds"] is None and [s["name"] for s in block["sources"]] == ["Generator_1"]


def test_war_adds_distant_combat_fire_under_the_smoke_and_flies() -> None:
    objects = [_wreck(1, 60.0, 60.0, 0.5), _wreck(2, -60.0, 60.0)]
    columns = [{"x": 60.0, "y": 60.0, "z": 1.5, "seed": 1}, {"x": -60.0, "y": 60.0, "z": 1.0, "seed": 2}]
    block, record = soundscape_block("war", "arid", load_soundscape(), objects, columns, 100.0, LAYOUT)
    assert block is not None
    assert (block["global"], block["birds"], block["wind"]) == ("Distant Combat Sparse", None, "Wind Desert")
    fires = sorted((s["x"], s["y"], s["z"]) for s in block["sources"] if s["clip"] == "Fire")
    assert fires == [(-60.0, 60.0, 1.5), (60.0, 60.0, 2.0)]  # 0.5 m over each column's foot
    flies = sorted((s["x"], s["y"], s["z"]) for s in block["sources"] if s["clip"] == "Flies")
    assert flies == [(-60.0, 60.0, 0.8), (60.0, 60.0, 1.3)]
    assert [s["name"] for s in block["sources"] if s["clip"] == "Fire"] == ["Fire_1", "Fire_2"]
    assert record == {"mode": "war", "style": "arid", "global": "Distant Combat Sparse", "birds": None, "wind": "Wind Desert",
                      "sources": {"generator": 0, "fire": 2, "flies": 2}}


def test_battle_is_dense_and_a_war_with_nothing_local_still_sounds() -> None:
    block, _ = soundscape_block("battle", "mediterranean", load_soundscape(), [], [], 100.0, LAYOUT)
    assert block == {"mode": "battle", "global": "Distant Combat Dense", "birds": None, "wind": None, "sources": []}


def test_a_source_keeps_the_millimetre() -> None:
    block, _ = soundscape_block("regional", "mediterranean", load_soundscape(), [_pole(1, 40.12345, -40.98765)], [], 100.0, LAYOUT)
    assert block is not None and block["sources"][0] == {"name": "Generator_1", "clip": "Engine Generator", "x": 40.123, "y": -40.988, "z": 1.0}


def test_an_unknown_mode_is_refused() -> None:
    with pytest.raises(SoundscapeError, match=r"^mode must be one of default, regional, war, battle, got 'auto'$"):
        soundscape_block("auto", "arid", load_soundscape(), [], [], None, {})


def test_the_configured_counts_and_heights_are_used(monkeypatch: pytest.MonkeyPatch) -> None:
    doc = _doc()
    doc["local"]["flies"] = {"clip": "Water Drip", "max": 1, "apart_m": 0, "spawn_clear_m": 0, "height_m": 2.5}
    config = parse_soundscape(copy.deepcopy(doc), _schema())
    block, _ = soundscape_block("war", "arid", config, [_wreck(1, 30.0, 30.0), _wreck(2, -30.0, 30.0)], [], 100.0, LAYOUT)
    assert block is not None
    assert [(s["clip"], s["z"]) for s in block["sources"] if s["name"].startswith("Flies")] == [("Water Drip", 2.5)]


def test_the_map_key_reaches_the_export_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "map.json").write_text("{}", encoding="utf-8")
    document: dict[str, Any] = {}
    monkeypatch.setattr("onward_generator.maps.workspace.load_map", lambda directory: document)
    monkeypatch.setattr("onward_generator.signs.place._damage", lambda base: ("none", "default"))
    monkeypatch.setattr("onward_generator.next_step._facade_style", lambda scene: ("--facade-style", "arid"))
    assert soundscape(tmp_path) == "auto"
    assert _soundscape(tmp_path, tmp_path) == ["--soundscape", "regional", "--facade-style", "arid"]
    monkeypatch.setattr("onward_generator.signs.place._damage", lambda base: ("heavy", "map"))
    assert _soundscape(tmp_path, tmp_path) == ["--soundscape", "war", "--facade-style", "arid"]
    document["soundscape"] = "default"
    assert _soundscape(tmp_path, tmp_path) == []
    document["soundscape"] = "battle"
    assert soundscape(tmp_path) == "battle" and _soundscape(tmp_path, tmp_path)[:2] == ["--soundscape", "battle"]
    document["soundscape"] = "loud"
    with pytest.raises(MapError, match=r'^map.json "soundscape" must be one of auto, default, regional, war, battle, got \'loud\'$'):
        soundscape(tmp_path)
    assert _soundscape(tmp_path / "elsewhere", tmp_path) == []  # no map.json there


def test_the_adapter_swaps_the_layer_s_clips_and_places_the_sources() -> None:
    source = ADAPTER.read_text(encoding="utf-8")
    assert "public SoundscapeOptions soundscape = new SoundscapeOptions();" in source
    assert "ApplySoundscape(pkg.Options.soundscape);" in source
    body = source[source.index("private static void ApplySoundscape"):source.index("private static void PlaceLocalAudio")]
    for line in ('string clip = name == "Ambience" ? sound.global', ': name == "Birds" ? sound.birds',
                 ': name == "TowerWind" || name.StartsWith("RoofWind_", StringComparison.Ordinal) ? sound.wind',
                 "source.AudioSourceName = clip;", "PrefabUtility.RecordPrefabInstancePropertyModifications(source);", 'PlaceLocalAudio("Sound_" + local.name, local.clip, new[] { local.x, local.y }, local.z);'):
        assert line in body, line
