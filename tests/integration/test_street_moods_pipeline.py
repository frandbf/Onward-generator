"""Street moods end to end (ADR-044, 16 November 2026): overgrow and signs on a map that names one, and the default unchanged."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from onward_generator.overgrow import place
from onward_generator.overgrow.place import OvergrowError, overgrow
from onward_generator.signs import place as signs_place
from onward_generator.signs.place import SignsError, place_signs
from tests.integration.test_overgrow_furnish import _detail_out
from tests.unit.test_street_signs import _scene
from tests.unit.test_war_damage import _damaged_base


@pytest.fixture(scope="module")
def runs(detail_inputs, tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    tmp = tmp_path_factory.mktemp("moods")
    base = _detail_out(detail_inputs, tmp, False)
    scene = detail_inputs[0]
    reports = {mood: overgrow(base, scene, tmp / str(mood), seed=777, street_mood=mood) for mood in (None, "abandoned", "lived", "emptied")}
    return {"tmp": tmp, "base": base, "scene": scene, "reports": reports}


def _report(out: Path) -> dict:
    report = json.loads((out / "overgrow_report.json").read_text(encoding="utf-8"))
    for key in ("created_at", "base_dir"):
        report.pop(key)
    return report


def test_abandoned_named_or_not_builds_what_overgrow_always_built(runs) -> None:  # type: ignore[no-untyped-def]
    tmp = runs["tmp"]
    for name in ("scene_visual.glb", "scene_collision.glb", "scene_detail.json"):
        assert (tmp / "None" / name).read_bytes() == (tmp / "abandoned" / name).read_bytes(), name
    assert _report(tmp / "None") == _report(tmp / "abandoned")
    assert "street_mood" not in _report(tmp / "abandoned")
    default = runs["reports"][None]
    assert default["vines"] > 0 and default["wrecks"] > 0  # the fixture grows vines and wrecks when abandoned


def test_a_lived_street_grows_nothing_and_keeps_its_mapped_trees(runs) -> None:  # type: ignore[no-untyped-def]
    lived, default = runs["reports"]["lived"], runs["reports"][None]
    assert (lived["vines"], lived["wrecks"]) == (0, 0)
    assert lived["street_mood"] == "lived"
    assert lived["trees_observed"] == default["trees_observed"]  # OSM's trees always stand
    assert lived["trees"] == lived["trees_observed"]  # overgrow plants none of its own
    emptied = runs["reports"]["emptied"]
    assert emptied["street_mood"] == "emptied" and emptied["vines"] == 0 and emptied["wrecks"] <= default["wrecks"]


def test_overgrow_keeps_only_the_props_of_the_mood(runs, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    seen: list[str] = []
    real = place.mood_props

    def spy(props, mood):  # type: ignore[no-untyped-def]
        seen.append(mood.id)
        return real(props, mood)

    monkeypatch.setattr(place, "mood_props", spy)
    overgrow(runs["base"], runs["scene"], runs["tmp"] / "spy", seed=777, street_mood="lived")
    assert seen == ["lived"]
    with pytest.raises(OvergrowError, match="street_mood must be one of abandoned, emptied, lived, got 'busy'"):
        overgrow(runs["base"], runs["scene"], runs["tmp"] / "busy", seed=777, street_mood="busy")


def test_signs_leave_out_the_wall_wear_of_the_mood(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scene = tmp_path / "geo_scene.json"
    _scene(scene)
    root = tmp_path / "map"
    base = root / "data" / "processed" / "scene_materials"
    base.parent.mkdir(parents=True)
    _damaged_base(base)
    monkeypatch.setattr(signs_place, "_ambient", lambda base: ("", False))
    (root / "map.json").write_text("{}", encoding="utf-8")
    document: dict = {}

    def load(r):  # type: ignore[no-untyped-def]
        assert r == root
        return document

    monkeypatch.setattr("onward_generator.maps.workspace.load_map", load)
    plain = place_signs(base, scene, tmp_path / "plain")["wall_decals"]
    excluded = {"soot", "bullets", "moss", "rust", "peeling", "crack", "chalk"}
    assert "street_mood" not in plain and {row["kind"] for row in plain["decals"]} & excluded  # the default draws that wear
    document["street_mood"] = "lived"
    lived = place_signs(base, scene, tmp_path / "lived")["wall_decals"]
    assert lived["street_mood"] == "lived" and lived["excluded_kinds"] == sorted(excluded)
    assert lived["decals"] and not {row["kind"] for row in lived["decals"]} & excluded
    document["street_mood"] = "abandoned"
    assert "street_mood" not in place_signs(base, scene, tmp_path / "abandoned")["wall_decals"]
    document["street_mood"] = "busy"
    with pytest.raises(SignsError, match=r"^map.json street_mood must be one of abandoned, emptied, lived, got 'busy'$"):
        place_signs(base, scene, tmp_path / "busy")
    document["street_mood"] = 3
    with pytest.raises(SignsError, match="got '3'"):
        place_signs(base, scene, tmp_path / "number")
    monkeypatch.setattr("onward_generator.maps.workspace.load_map", lambda r: (_ for _ in ()).throw(ValueError("broken")))
    assert "street_mood" not in place_signs(base, scene, tmp_path / "unreadable")["wall_decals"]  # an unreadable map.json reads as no key


def _planted(out: Path) -> list[float]:
    objects = json.loads((out / "scene_detail.json").read_text(encoding="utf-8"))["objects"]
    return [o["position_m"][0] for o in objects
            if (o.get("type") == "overgrow" or str(o.get("id", "")).startswith("overgrow")) and "tree" in json.dumps(o) and "generated" in json.dumps(o)]


def test_the_trees_overgrow_plants_follow_the_seed_and_the_years(runs) -> None:  # type: ignore[no-untyped-def]
    """Run here, not in the module fixture, so a changed line is run by the test that checks it."""
    planted: dict[int, list[float]] = {}
    for seed in range(1, 9):
        out = runs["tmp"] / f"years30_{seed}"
        overgrow(runs["base"], runs["scene"], out, seed=seed, profile=30)
        planted[seed] = _planted(out)
    assert planted == {1: [40.0], 2: [27.6], 3: [27.6, 52.4], 4: [40.0], 5: [40.0], 6: [27.6, 40.0], 7: [27.6, 52.4], 8: [40.0, 52.4]}


def test_the_report_names_a_mood_other_than_the_default(runs) -> None:  # type: ignore[no-untyped-def]
    lived = overgrow(runs["base"], runs["scene"], runs["tmp"] / "report_lived", seed=777, street_mood="lived")
    assert lived["street_mood"] == "lived" and (lived["vines"], lived["wrecks"]) == (0, 0)
    assert "street_mood" not in overgrow(runs["base"], runs["scene"], runs["tmp"] / "report_default", seed=777)
