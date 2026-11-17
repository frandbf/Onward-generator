"""Street moods (ADR-044, 16 November 2026): the table, how a mood scales the year profile, and where the map names it."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import pytest

from onward_generator.overgrow.moods import (
    MOODS_PATH,
    SCHEMA_PATH,
    MoodError,
    apply_mood,
    load_moods,
    mood_for,
    mood_props,
    parse_moods,
)
from onward_generator.overgrow.profiles import PROFILES, Profile, rule_factor

DOC = json.loads(MOODS_PATH.read_text(encoding="utf-8"))
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def test_the_table_has_the_three_moods_and_abandoned_is_the_default() -> None:
    table = load_moods()
    assert table.default == "abandoned" and sorted(table.moods) == ["abandoned", "emptied", "lived"]
    assert mood_for(None).id == "abandoned" and mood_for("lived").id == "lived"
    lived = mood_for("lived")
    assert (lived.vines, lived.planted_trees, lived.wrecks) == (0.0, 0.0, 0.0)
    assert lived.conditions == frozenset({"intact"})
    assert dict(lived.categories) == {"barriers": 0.15, "waste": 0.25, "vehicles": 0.25, "structures": 0.5, "furniture": 2.0, "vegetation": 1.5}
    assert {"soot", "bullets"} <= lived.wall_decals_excluded and "poster" not in lived.wall_decals_excluded
    emptied = mood_for("emptied")
    assert emptied.wrecks == 0.3 and emptied.conditions == frozenset({"intact", "worn"})
    assert mood_for("abandoned").conditions is None and not mood_for("abandoned").wall_decals_excluded
    assert (lived.terraces, emptied.terraces, mood_for("abandoned").terraces) == (True, False, False)  # only a lived street sets terraces
    assert (lived.awnings, emptied.awnings, mood_for("abandoned").awnings) == (True, False, False)
    # Cover by mood (plan increment 5): civic props in a lived street, civic props and barriers in an emptied one.
    assert lived.cover_roles == {"utility_box", "dumpster", "planter", "trashcan"} and lived.cover_conditions == {"intact"}
    assert emptied.cover_roles == {"barrier", "barrel", "fence", "utility_box", "dumpster", "trash", "planter", "trashcan"}
    assert emptied.cover_conditions == {"intact", "worn"}
    assert (mood_for("abandoned").cover_roles, mood_for("abandoned").cover_conditions) == (None, None)  # the stage's own rule


def test_abandoned_is_the_identity_on_every_year_profile() -> None:
    """A map without the key builds exactly what it built before."""
    for profile in PROFILES.values():
        assert apply_mood(profile, mood_for("abandoned")) == profile
        assert apply_mood(profile, mood_for("abandoned")).categories == {}


def test_a_mood_scales_vines_planted_trees_and_wrecks_and_sets_the_categories() -> None:
    profile = Profile(30, 0.5, 0.45, 0.55, 1.0, 0.45, 0.5)
    lived = apply_mood(profile, mood_for("lived"))
    assert (lived.vines, lived.planted, lived.wrecks) == (0.0, 0.0, 0.0)
    assert (lived.grass, lived.trees, lived.bushes, lived.props, lived.years) == (0.5, 0.45, 0.5, 1.0, 30)  # mapped trees and plants stay
    emptied = apply_mood(profile, mood_for("emptied"))
    assert emptied.wrecks == pytest.approx(0.55 * 0.3) and emptied.vines == 0.0
    assert rule_factor(lived, "barriers", ("barrier",)) == pytest.approx(0.15)
    assert rule_factor(lived, "furniture", ("bench",)) == pytest.approx(2.0)
    assert rule_factor(lived, "vegetation", ("tree",)) == pytest.approx(0.45 * 1.5)
    assert rule_factor(lived, "vegetation", ("bush",)) == pytest.approx(0.5 * 1.5)
    assert rule_factor(lived, "vegetation", ("plant",)) == pytest.approx(0.5 * 1.5)
    assert rule_factor(lived, "facade", ("aircon",)) == 1.0  # a category the mood does not name keeps its factor
    assert rule_factor(profile, "barriers", ("barrier",)) == 1.0


def test_a_mood_keeps_the_props_of_its_conditions_and_those_without_one() -> None:
    props = [SimpleNamespace(spec=SimpleNamespace(id=i, condition=c)) for i, c in
             (("a", "intact"), ("b", "worn"), ("c", "wrecked"), ("d", ""), ("e", "burned"))]
    assert [p.spec.id for p in mood_props(props, mood_for("lived"))] == ["a", "d"]  # type: ignore[arg-type]
    assert [p.spec.id for p in mood_props(props, mood_for("emptied"))] == ["a", "b", "d"]  # type: ignore[arg-type]
    assert [p.spec.id for p in mood_props(props, mood_for("abandoned"))] == ["a", "b", "c", "d", "e"]  # type: ignore[arg-type]


def test_the_parsed_table_keeps_every_value() -> None:
    """Read through parse_moods, not the cached load_moods, so a changed parser is the one tested."""
    table = parse_moods(DOC, SCHEMA)
    assert table.default == "abandoned" and sorted(table.moods) == ["abandoned", "emptied", "lived"]
    for name, body in DOC["moods"].items():
        mood = table.moods[name]
        assert mood.id == name
        assert (mood.vines, mood.planted_trees, mood.wrecks) == (body["vines"], body["planted_trees"], body["wrecks"])
        assert all(isinstance(v, float) for v in (mood.vines, mood.planted_trees, mood.wrecks))
        assert mood.conditions == (frozenset(body["conditions"]) if body["conditions"] is not None else None)
        assert dict(mood.categories) == body["categories"] and isinstance(mood.categories, MappingProxyType)
        assert mood.wall_decals_excluded == frozenset(body["wall_decals_excluded"])
        assert mood.terraces is body["terraces"] and mood.awnings is body["awnings"]
        cover = body["cover"]
        assert mood.cover_roles == (frozenset(cover["roles"]) if cover is not None else None)
        assert mood.cover_conditions == (frozenset(cover["conditions"]) if cover is not None else None)
    doc = copy.deepcopy(DOC)
    doc["moods"]["lived"]["cover"] = {"roles": ["planter"], "conditions": None}
    assert (parse_moods(doc, SCHEMA).moods["lived"].cover_roles, parse_moods(doc, SCHEMA).moods["lived"].cover_conditions) == (
        frozenset({"planter"}), None)
    assert table.moods["lived"].conditions == frozenset({"intact"}) and table.moods["abandoned"].conditions is None


def test_an_unknown_mood_names_the_ones_there_are() -> None:
    with pytest.raises(MoodError, match=r"^street_mood must be one of abandoned, emptied, lived, got 'busy'$"):
        mood_for("busy")


@pytest.mark.parametrize(("change", "message"), [
    (lambda d: d.update(schema="other"), "^street_moods: schema: 'onward_generator.street_moods' was expected$"),
    (lambda d: d.pop("moods"), "^street_moods: 'moods' is a required property$"),
    (lambda d: d.update(default="busy"), r"^street_moods: default 'busy' is not one of its moods: \['abandoned', 'emptied', 'lived'\]$"),
    (lambda d: d["moods"]["lived"].update(vines=2), "moods/lived/vines"),
    (lambda d: d["moods"]["lived"].update(conditions=["shiny"]), "moods/lived/conditions"),
    (lambda d: d["moods"]["lived"]["categories"].update(rubble=1), "moods/lived/categories"),
    (lambda d: d["moods"]["lived"].pop("note"), "'note' is a required property"),
    (lambda d: d["moods"]["lived"].update(terraces="yes"), "moods/lived/terraces"),
    (lambda d: d["moods"]["lived"].pop("awnings"), "'awnings' is a required property"),
    (lambda d: d["moods"]["lived"].pop("cover"), "'cover' is a required property"),
    (lambda d: d["moods"]["lived"].update(cover={"roles": []}), "moods/lived/cover"),
    (lambda d: d["moods"]["lived"].update(cover={"roles": ["planter"], "conditions": ["shiny"]}), "moods/lived/cover"),
])
def test_a_bad_table_is_refused(change, message: str) -> None:  # type: ignore[no-untyped-def]
    doc = copy.deepcopy(DOC)
    change(doc)
    with pytest.raises(MoodError, match=message):
        parse_moods(doc, SCHEMA)


def test_an_unreadable_table_is_refused(tmp_path: Path) -> None:
    bad = tmp_path / "moods.json"
    bad.write_text("{", encoding="utf-8")
    with pytest.raises(MoodError, match="moods.json"):
        load_moods(bad)
    table = parse_moods(DOC, SCHEMA)
    assert isinstance(table.moods, MappingProxyType) and mood_for("emptied", table).id == "emptied"


def _map(tmp_path: Path, **keys: object) -> Path:
    root = tmp_path / "map"
    root.mkdir(exist_ok=True)
    (root / "map.json").write_text(json.dumps(keys), encoding="utf-8")
    return root


def test_the_map_json_key_is_read_and_checked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from onward_generator.maps import workspace
    from onward_generator.maps.workspace import MapError, street_mood

    document: dict[str, object] = {}
    monkeypatch.setattr(workspace, "load_map", lambda directory: document)
    assert street_mood(tmp_path) is None
    document["street_mood"] = "lived"
    assert street_mood(tmp_path) == "lived"
    document["street_mood"] = "busy"
    with pytest.raises(MapError, match=r"^map.json street_mood must be one of abandoned, emptied, lived, got 'busy'$"):
        street_mood(tmp_path)
    document["street_mood"] = 3
    with pytest.raises(MapError, match=r'^map.json "street_mood" must be a string, got 3$'):
        street_mood(tmp_path)


def test_run_passes_the_map_s_mood_to_overgrow(tmp_path: Path) -> None:
    from onward_generator.maps.workspace import MapError, create_map
    from onward_generator.next_step import _street_mood, overgrow_command
    from tests.conftest import MURCIA_CENTER

    root = tmp_path / "murcia"
    create_map(root, map_id="murcia", name="Murcia", latitude=MURCIA_CENTER[1], longitude=MURCIA_CENTER[0], radius_m=200)
    signs = root / "data" / "processed" / "scene_signs"
    assert "--street-mood" not in overgrow_command(signs).argv  # no key: overgrow's default
    document = json.loads((root / "map.json").read_text(encoding="utf-8"))
    for mood in ("lived", "abandoned"):
        (root / "map.json").write_text(json.dumps({**document, "street_mood": mood}), encoding="utf-8")
        argv = list(overgrow_command(signs).argv)
        assert argv.count("--street-mood") == 1 and argv[argv.index("--street-mood") + 1] == mood
    (root / "map.json").write_text(json.dumps({**document, "street_mood": "busy"}), encoding="utf-8")
    with pytest.raises(MapError, match="street_mood"):
        overgrow_command(signs)
    assert _street_mood(None) == () and _street_mood(tmp_path / "elsewhere") == ()


def test_run_passes_the_map_s_or_the_profile_s_mood_to_cover(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from onward_generator.ai import street_profile
    from onward_generator.maps.workspace import create_map
    from onward_generator.next_step import cover_street_mood
    from tests.conftest import MURCIA_CENTER

    root = tmp_path / "murcia"
    create_map(root, map_id="murcia", name="Murcia", latitude=MURCIA_CENTER[1], longitude=MURCIA_CENTER[0], radius_m=200)
    asked: list[Path] = []
    monkeypatch.setattr(street_profile, "applied_street_mood", lambda r: asked.append(r) or None)
    assert cover_street_mood(root) == () and asked == [root]  # neither: the stage's default
    monkeypatch.setattr(street_profile, "applied_street_mood", lambda r: "lived")
    assert cover_street_mood(root) == ("--street-mood", "lived")  # the profile's, without the key
    document = json.loads((root / "map.json").read_text(encoding="utf-8"))
    (root / "map.json").write_text(json.dumps({**document, "street_mood": "emptied"}), encoding="utf-8")
    assert cover_street_mood(root) == ("--street-mood", "emptied")  # the key wins
    assert cover_street_mood(None) == () and cover_street_mood(tmp_path / "elsewhere") == ()
    from onward_generator.maps.workspace import store_sightline_cover
    from onward_generator.pipeline_run import plan_steps

    store_sightline_cover(root, True)
    cover = dict(plan_steps(root))["cover"]
    assert cover[cover.index("--street-mood") + 1] == "emptied" and cover[-2] == "--out"  # run hands it to perf cover


def test_the_overgrow_command_passes_the_mood(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from onward_generator.overgrow import __main__ as cli

    seen: dict[str, object] = {}

    def fake(*args, **kwargs):  # type: ignore[no-untyped-def]
        seen.update(kwargs)
        raise OSError("stop here")

    monkeypatch.setattr(cli, "overgrow", fake)
    common = ["place", "--base", str(tmp_path), "--scene", str(tmp_path / "s.json"), "--out", str(tmp_path / "o")]
    assert cli.main([*common, "--street-mood", "lived"]) == 1
    assert seen["street_mood"] == "lived"
    assert cli.main(common) == 1
    assert seen["street_mood"] is None


@pytest.mark.parametrize("module", ["onward_generator.export.package", "onward_generator.signs", "onward_generator.overgrow"])
def test_each_stage_imports_alone_in_a_fresh_interpreter(module: str) -> None:
    """16 November 2026: signs imported the moods at the top, and export → signs → overgrow → export closed a cycle
    that only a fresh interpreter (``run`` starting at export) showed."""
    import subprocess
    import sys

    done = subprocess.run([sys.executable, "-c", f"import {module}"], capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
