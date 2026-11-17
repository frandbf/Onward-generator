"""Street moods end to end (ADR-044, 16 November 2026): overgrow and signs on a map that names one, and the default unchanged."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import Point

from onward_generator.generation.gltf import Node, Primitive, nodes_from_glb
from onward_generator.generation.mesh import box
from onward_generator.overgrow import place
from onward_generator.overgrow.place import OvergrowError, overgrow
from onward_generator.overgrow.terraces import TerraceResult
from onward_generator.overgrow.urban import detail_elements
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
    # Without the key, the mood the map's street profile applies (plan increment 4); a map.json mood wins over it.
    profiled: list[Path] = []
    monkeypatch.setattr("onward_generator.ai.street_profile.applied_street_mood", lambda r: profiled.append(r) or "lived")
    del document["street_mood"]
    assert place_signs(base, scene, tmp_path / "profiled")["wall_decals"]["street_mood"] == "lived" and profiled == [root]
    document["street_mood"] = "abandoned"
    assert "street_mood" not in place_signs(base, scene, tmp_path / "user_wins")["wall_decals"] and len(profiled) == 1
    monkeypatch.setattr("onward_generator.ai.street_profile.applied_street_mood", lambda r: None)
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


def test_a_lived_street_reports_its_terraces_and_the_default_none(runs) -> None:  # type: ignore[no-untyped-def]
    terraces = runs["reports"]["lived"]["terraces"]
    assert set(terraces) == {"shops", "sets", "pieces", "colliders", "parasols", "triangles", "models", "skipped", "missing"}
    assert "terraces" not in runs["reports"][None] and "terraces" not in runs["reports"]["emptied"]


def test_the_terraces_join_the_scene(runs, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    """A terrace's node, collider, record and footprint reach the visual and collision GLBs, the manifest and the street props."""
    seen: dict[str, object] = {}
    made = TerraceResult()
    table = box(-0.4, 0.4, -0.4, 0.4, 0.0, 0.75)
    made.nodes.append(Node("overgrow/terrace/0/0", (Primitive(table, "prop_t"),), {"type": "overgrowth", "kind": "terrace"}))
    made.colliders.append(Node("overgrow/collider/terrace/0/0", (Primitive(table, "collider"),), {"type": "collision"}))
    made.images["prop_t"] = place.grass_png()
    made.footprints.append(Point(3.0, 4.0).buffer(1.0))
    made.records.append({"kind": "terrace", "origin": "generated", "source_id": "B", "position_m": [3.0, 4.0, 0.0],
                         "asset_id": "t", "role": "table", "size_m": [0.8, 0.8, 0.75], "solid": True})
    made.triangles = 12

    def fake(manifest, features, buildings, zone, library, **kwargs):  # type: ignore[no-untyped-def]
        seen.update(kwargs, library=library, zone=zone.kind, features=features, buildings=buildings, manifest=manifest)
        return made

    dressed: dict[str, object] = {}
    real_dress = place.dress

    def dress_spy(*args, **kwargs):  # type: ignore[no-untyped-def]
        dressed.update(kwargs)
        return real_dress(*args, **kwargs)

    monkeypatch.setattr(place, "place_terraces", fake)
    monkeypatch.setattr(place, "dress", dress_spy)
    out = runs["tmp"] / "terrace_join"
    library = runs["tmp"] / "empty_library"
    library.mkdir(exist_ok=True)
    layout = runs["tmp"] / "terrace_layout.json"
    layout.write_text(json.dumps({"ai_m": [{"x": 40.0, "y": -30.0}]}), encoding="utf-8")
    report = overgrow(runs["base"], runs["scene"], out, seed=777, street_mood="lived", library=library, layout=layout)
    assert report["terraces"]["pieces"] == 1 and seen["library"] == library
    assert seen["features"] and all(isinstance(f, dict) for f in seen["features"]) and any(f.get("kind") == "highway" for f in seen["features"])
    assert len(seen["buildings"].geometries) > 1 and all(g.geom_type == "Polygon" for g in seen["buildings"].geometries)
    elements = detail_elements(seen["manifest"])
    assert elements and len(seen["keep_out"]) >= len(elements)  # detail's urban elements are kept off
    assert seen["keep_out"][0].contains(Point(*elements[0][:2]))
    assert seen["near_spawn"](41.0, -30.0) is True and seen["near_spawn"](40.0, -40.0) is False and seen["near_spawn"](30.0, -30.0) is False
    # A quarter of the headroom before they spend it; the awnings hung after them spend theirs too.
    assert seen["budget"] == int((report["triangle_headroom"] + made.triangles + report["awnings"]["triangles"]) * 0.25)
    assert seen["ground_z"](1e6, 1e6) == 0.0
    assert (3.0, 4.0, 1.0) in [tuple(round(v, 3) for v in c) for c in dressed["keep_out"]]  # the street props keep off the terrace
    visual = {node.name for node in nodes_from_glb((out / "scene_visual.glb").read_bytes())[0]}
    collision = {node.name for node in nodes_from_glb((out / "scene_collision.glb").read_bytes())[0]}
    assert "overgrow/terrace/0/0" in visual and "overgrow/collider/terrace/0/0" in collision
    objects = json.loads((out / "scene_detail.json").read_text(encoding="utf-8"))["objects"]
    row = next(o for o in objects if o.get("kind") == "terrace")
    assert row["size_m"] == [0.8, 0.8, 0.75] and row["role"] == "table" and row["source_ids"] == ["B"] and row["asset_id"] == "t"
    assert report["colliders"] == runs["reports"]["lived"]["colliders"] + 1


def test_a_lived_street_reports_its_awnings_and_draws_the_cloth(runs) -> None:  # type: ignore[no-untyped-def]
    awnings = runs["reports"]["lived"]["awnings"]
    assert set(awnings) == {"fascias", "awnings", "triangles", "skipped"}
    assert "awnings" not in runs["reports"][None] and "awnings" not in runs["reports"]["emptied"]
    default = nodes_from_glb((runs["tmp"] / "None" / "scene_visual.glb").read_bytes())[1]
    assert "awning_fabric" not in default  # the default mood draws no cloth


def test_the_awnings_join_the_scene(runs, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    """An awning's node, record and footprint reach the visual GLB with the cloth, the manifest and the wall props."""
    from shapely.geometry import box as shapely_box

    from onward_generator.overgrow.awnings import AwningResult
    from onward_generator.overgrow.cloth import awning_mesh

    seen: dict[str, object] = {}
    made = AwningResult()
    mesh, uvs = awning_mesh(np.array([5.0, 5.0]), np.array([1.0, 0.0]), np.array([0.0, -1.0]), 2.0, 3.0, 0.9, 0.3, 0.2, 0)
    made.nodes.append(Node("overgrow/awning/0", (Primitive(mesh, "awning_fabric", uvs),), {"type": "overgrowth", "kind": "awning"}))
    made.footprints.append((shapely_box(4.0, 4.1, 6.0, 5.0), 2.5, 3.0))
    made.records.append({"kind": "awning", "origin": "generated", "source_id": "B", "role": "awning", "position_m": [5.0, 4.55, 2.5],
                         "size_m": [2.0, 0.9, 0.5], "solid": False})
    made.triangles = 12

    def fake(manifest, buildings, zone, seed, **kwargs):  # type: ignore[no-untyped-def]
        seen.update(kwargs, buildings=buildings, zone=zone.kind, seed=seed, manifest=manifest)
        return made

    dressed: dict[str, object] = {}
    real_dress = place.dress

    def dress_spy(*args, **kwargs):  # type: ignore[no-untyped-def]
        dressed.update(kwargs)
        return real_dress(*args, **kwargs)

    monkeypatch.setattr(place, "place_awnings", fake)
    monkeypatch.setattr(place, "dress", dress_spy)
    out = runs["tmp"] / "awning_join"
    report = overgrow(runs["base"], runs["scene"], out, seed=777, street_mood="lived")
    assert report["awnings"] == made.report() and seen["seed"] == 777 and seen["zone"] == report["zone"]["kind"]
    assert seen["budget"] == int((report["triangle_headroom"] + made.triangles) * 0.25)
    assert len(seen["buildings"].geometries) > 1 and seen["manifest"]["objects"]
    assert made.footprints[0] in dressed["signs"]  # a wall prop keeps off the awning as off a sign
    nodes, materials = nodes_from_glb((out / "scene_visual.glb").read_bytes())
    assert "overgrow/awning/0" in {node.name for node in nodes} and "awning_fabric" in materials
    assert tuple(materials["awning_fabric"].color) == (1.0, 1.0, 1.0, 1.0) and materials["awning_fabric"].roughness == pytest.approx(0.9)
    objects = json.loads((out / "scene_detail.json").read_text(encoding="utf-8"))["objects"]
    row = next(o for o in objects if o.get("kind") == "awning")
    assert row["size_m"] == [2.0, 0.9, 0.5] and row["source_ids"] == ["B"] and row["role"] == "awning"
