"""Café terraces before the bars of a lived street (overgrow/terraces.py; plan 2026-11-16, increment 2)."""

from __future__ import annotations

import copy
import io
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image
from shapely import STRtree
from shapely.geometry import Point, Polygon
from shapely.geometry import box as shapely_box

from onward_generator.generation.mesh import box
from onward_generator.overgrow import terraces
from onward_generator.overgrow.catalog import ASSETS_BY_ID
from onward_generator.overgrow.library import InteriorProp, Prop
from onward_generator.overgrow.terraces import (
    COLLIDER_PREFIX,
    NODE_PREFIX,
    SCHEMA_PATH,
    TERRACES_PATH,
    Streets,
    TerraceError,
    chosen_models,
    facing_yaw,
    free_across,
    load_terraces,
    other_doors,
    parse_terraces,
    place_terraces,
    shop_doors,
    shop_fronts,
    street_reason,
    streets_of,
    wall_at,
)
from onward_generator.overgrow.zone import square_zone

DOC = json.loads(TERRACES_PATH.read_text(encoding="utf-8"))
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
CFG = parse_terraces(DOC, SCHEMA)


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (8, 8), (90, 90, 90)).save(out, format="PNG")
    return out.getvalue()


def _model(asset_id: str, role: str, width: float, depth: float, height: float) -> InteriorProp:
    spec = replace(ASSETS_BY_ID["wooden_picnic_table"], id=asset_id, role=role)
    mesh = box(1.0, 1.0 + width, 2.0, 2.0 + depth, 0.0, height)  # off the origin: the node centres the model's box
    return InteriorProp(Prop(spec, mesh, np.zeros((mesh.vertex_count, 2)), _png()), "kenney_furniture", "CC0-1.0", "0" * 64)


MODELS = {
    "table": _model("t", "table", 0.8, 0.8, 0.75),
    "chair": _model("c", "chair", 0.4, 0.4, 0.9),
    "board": _model("b", "sign", 0.6, 0.5, 1.2),
    "planter": _model("p", "planter", 0.5, 0.5, 1.0),
}


def _manifest(doors: list[tuple[str, str, tuple[float, float]]], facade_doors: list[list[float]] | None = None) -> dict[str, Any]:
    objects: list[dict[str, Any]] = []
    for building, category, (x, y) in doors:
        objects.append({"type": "interior", "building_id": building, "rooms": [{"id": f"{building}/L0", "shop": {"category": category}}],
                        "connections": [{"type": "door", "from": "exterior", "to": f"{building}/L0", "position_m": [x, y]}]})
    objects.append({"type": "building", "facade": {"doors_m": facade_doors or []}})
    return {"objects": objects}


def _street(highway: str = "pedestrian", y: float = -4.0) -> dict[str, Any]:
    return {"kind": "highway", "tags": {"highway": highway}, "geometry": {"type": "LineString", "coordinates": [[-40.0, y], [40.0, y]]}}


ROW = [shapely_box(-10, 0, 10, 10), shapely_box(10, 0, 20, 10), shapely_box(-20, 0, -10, 10)]


def _run(buildings: list[Polygon], manifest: dict[str, Any] | None = None, features: list[dict[str, Any]] | None = None,
         monkeypatch: pytest.MonkeyPatch | None = None, models: dict[str, InteriorProp] | None = None, **kwargs: Any) -> terraces.TerraceResult:
    if monkeypatch is not None:
        monkeypatch.setattr(terraces, "chosen_models", lambda library, cfg: (dict(models or MODELS), {}))
    options: dict[str, Any] = {"budget": 100_000, "ground_z": lambda x, y: 0.25, "near_spawn": lambda x, y: False, "config": CFG}
    options.update(kwargs)
    return place_terraces(manifest or _manifest([("A", "bar", (0.0, 0.0))]), features if features is not None else [_street()],
                          STRtree(buildings), square_zone(100.0), "assets", **options)


def test_the_configuration_is_read_whole() -> None:
    assert CFG.shop_categories == frozenset({"bar"}) and CFG.highways == frozenset({"pedestrian", "living_street"})
    assert (CFG.street_search_m, CFG.passage_m, CFG.wall_gap_m, CFG.door_clear_m) == (12.0, 1.2, 0.3, 0.9)
    assert (CFG.piece_gap_m, CFG.set_gap_m, CFG.reach_m, CFG.along_m, CFG.sets_per_shop, CFG.max_sets) == (0.1, 0.4, 30.0, 6.0, 2, 24)
    assert CFG.pieces["table"][0] == "qt_home_Table_RoundSmall" and CFG.pieces["chair"][0] == "qt_home_Chair_1"
    assert CFG.pieces["board"] == ("standing_chalkboard_01",) and CFG.pieces["planter"] == ("kenney_furniture_pottedPlant",)
    assert all(isinstance(value, float) for value in (CFG.street_search_m, CFG.passage_m, CFG.wall_gap_m, CFG.reach_m, CFG.along_m))
    assert load_terraces() == CFG


@pytest.mark.parametrize(("change", "message"), [
    (lambda d: d.update(schema="other"), "^terraces: schema: 'onward_generator.terraces' was expected$"),
    (lambda d: d.pop("pieces"), "^terraces: 'pieces' is a required property$"),
    (lambda d: d.update(passage_m=0.5), "^terraces: passage_m: "),
    (lambda d: d["pieces"].update(table=[]), "^terraces: pieces/table: "),
])
def test_a_bad_configuration_is_refused(change, message: str) -> None:  # type: ignore[no-untyped-def]
    doc = copy.deepcopy(DOC)
    change(doc)
    with pytest.raises(TerraceError, match=message):
        parse_terraces(doc, SCHEMA)


def test_an_unreadable_configuration_is_refused(tmp_path: Path) -> None:
    bad = tmp_path / "terraces.json"
    bad.write_text("{", encoding="utf-8")
    with pytest.raises(TerraceError, match="terraces.json"):
        load_terraces(bad)


def test_the_shop_doors_are_read_from_the_interiors() -> None:
    manifest = _manifest([("B", "bar", (5.0, 1.0)), ("A", "bakery", (1.0, 2.0)), ("C", "bar", (0.0, 0.0))], [[9.0, 9.0]])
    manifest["objects"].append({"type": "interior", "building_id": "D", "shop": {"category": "bar"}, "rooms": [{"id": "D/L0"}],
                                "connections": [{"type": "door", "from": "exterior", "to": "D/L0", "position_m": [3, 4, 0]},
                                                {"type": "door", "from": "D/L0", "to": "D/L1", "position_m": [3, 5]},
                                                {"type": "stair", "from": "exterior", "to": "D/L0", "position_m": [3, 6]}]})
    manifest["objects"].append({"type": "interior", "building_id": "E", "rooms": [{"id": "E/L0"}],
                                "connections": [{"type": "door", "from": "exterior", "to": "E/L0", "position_m": [7, 7]}]})
    assert shop_doors(manifest) == [("B", "bar", (5.0, 1.0)), ("A", "bakery", (1.0, 2.0)), ("C", "bar", (0.0, 0.0)), ("D", "bar", (3.0, 4.0))]
    fronts = shop_fronts(manifest, {"bar"})
    assert [(f.building_id, f.door) for f in fronts] == [("B", (5.0, 1.0)), ("C", (0.0, 0.0)), ("D", (3.0, 4.0))]
    assert other_doors(manifest) == [(5.0, 1.0), (1.0, 2.0), (0.0, 0.0), (3.0, 4.0), (9.0, 9.0)]


def test_a_door_finds_its_wall_and_the_way_out() -> None:
    tree = STRtree([shapely_box(-10, 0, 10, 10)])
    wall = wall_at((0.0, 0.0), tree)
    assert wall is not None and tuple(wall.u) == (1.0, 0.0) and tuple(wall.n) == (0.0, -1.0)
    assert (wall.length, wall.door_s) == (20.0, 10.0) and tuple(wall.point(10.0, 1.0)) == (0.0, -1.0)
    north = wall_at((2.0, 10.4), tree)
    assert north is not None and tuple(north.n) == (0.0, 1.0) and north.door_s == pytest.approx(8.0)
    assert wall_at((0.0, -1.2), tree) is None  # farther than WALL_SEARCH_M from every footprint
    yard = Polygon(shapely_box(-10, -10, 10, 10).exterior.coords, [shapely_box(-2, -2, 2, 2).exterior.coords])
    inner = wall_at((0.0, -2.0), STRtree([yard]))
    assert inner is not None and tuple(inner.n) == (0.0, 1.0)  # a courtyard's wall faces into the courtyard


def test_the_street_before_a_terrace() -> None:
    plaza = {"kind": "highway", "tags": {"highway": "pedestrian", "area": "yes"},
             "geometry": {"type": "Polygon", "coordinates": [[[50, 50], [60, 50], [60, 60], [50, 60], [50, 50]]]}}
    road = {"kind": "highway", "tags": {"highway": "residential"},
            "geometry": {"type": "MultiLineString", "coordinates": [[[-40, 30], [40, 30]], [[-40, 31], [40, 31]]]}}
    hall = {"kind": "highway", "tags": {"highway": "corridor"}, "geometry": {"type": "LineString", "coordinates": [[-40, 0], [40, 0]]}}
    streets = streets_of([_street(), road, plaza, hall, {"kind": "building", "geometry": {"type": "Point", "coordinates": [0, 0]}},
                          {"kind": "highway", "tags": {"highway": "residential"}, "geometry": {"type": "Polygon",
                                                                                           "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}}])
    assert [highway for _, highway in streets.lines] == ["pedestrian", "residential", "residential"] and len(streets.plazas) == 1
    assert street_reason(np.array([0.0, -2.0]), streets, CFG) is None
    assert street_reason(np.array([0.0, 28.0]), streets, CFG) == "not_pedestrian"
    assert street_reason(np.array([55.0, 55.0]), streets, CFG) is None  # a plaza, however far its street
    assert street_reason(np.array([0.0, 8.0]), streets_of([_street()]), CFG) is None  # 12 m: the search reaches 12 m
    assert street_reason(np.array([0.0, 8.1]), streets_of([_street()]), CFG) == "no_street"
    assert street_reason(np.array([0.0, 0.0]), Streets((), ()), CFG) == "no_street"
    assert street_reason(np.array([0.0, 1.0]), streets_of([hall]), CFG) == "no_street"  # a corridor is not a street


def test_the_free_width_across_the_street() -> None:
    tree = STRtree([shapely_box(-10, -20, 10, -8), shapely_box(-10, -40, 10, -30)])
    assert free_across(np.array([0.0, 0.0]), np.array([0.0, -1.0]), tree, 30.0) == pytest.approx(8.0)
    assert free_across(np.array([0.0, 0.0]), np.array([0.0, 1.0]), tree, 30.0) == 30.0
    assert free_across(np.array([0.0, 0.0]), np.array([0.0, -1.0]), tree, 5.0) == 5.0


def test_a_model_is_turned_to_face_a_way() -> None:
    assert [facing_yaw(np.array(v)) for v in ((0.0, -1.0), (1.0, 0.0), (0.0, 1.0), (-1.0, 0.0))] == [0.0, 90.0, 180.0, 270.0]


def _pieces(result: terraces.TerraceResult) -> list[tuple[str, float, float]]:
    return [(row["role"], row["position_m"][0], row["position_m"][1]) for row in result.records]


def test_a_bar_on_a_pedestrian_street_gets_its_terrace(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _run(ROW, monkeypatch=monkeypatch)
    assert _pieces(result) == [
        ("table", 1.8, -0.7), ("chair", 2.5, -0.7), ("chair", 1.1, -0.7),
        ("table", -1.8, -0.7), ("chair", -1.1, -0.7), ("chair", -2.5, -0.7),
        ("board", 3.4, -0.55), ("planter", -3.35, -0.55),
    ]
    assert (result.shops, result.sets, len(result.footprints)) == (1, 2, 4)
    assert all(row["position_m"][2] == 0.25 for row in result.records)  # on the ground the floor gives
    yaws = [node.matrix for node in result.nodes]
    assert [round(float(np.degrees(np.arctan2(m[1][0], m[0][0]))) % 360) for m in yaws[:3]] == [0, 270, 90]  # chairs face the table
    table = result.nodes[0]
    assert table.name == NODE_PREFIX + "0/0" and table.mesh_key == "prop:t" and table.primitives
    assert not result.nodes[3].primitives  # the second table shares the first one's mesh
    assert np.allclose(table.matrix @ np.array([1.4, 2.4, 0.0, 1.0]), [1.8, -0.7, 0.25, 1.0])  # the model's box centre stands on the spot
    assert table.extras["kind"] == "terrace" and table.extras["building_id"] == "A" and table.extras["licence"] == "CC0-1.0"
    assert [row["solid"] for row in result.records] == [True, False, False, True, False, False, True, True]  # a chair stays visual
    assert [node.name for node in result.colliders] == [COLLIDER_PREFIX + n for n in ("0/0", "1/0", "2/0", "3/0")]
    assert result.triangles == 8 * 12 and set(result.images) == {"prop_t", "prop_c", "prop_b", "prop_p"}
    assert result.records[0]["size_m"] == [0.8, 0.8, 0.75] and result.records[0]["source_id"] == "A"
    report = result.report()
    assert report == {"shops": 1, "sets": 2, "pieces": 8, "colliders": 4, "triangles": 96,
                      "models": {"board": "b", "chair": "c", "planter": "p", "table": "t"}, "skipped": {}, "missing": {}}


def test_a_terrace_keeps_the_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    across = shapely_box(-30, -20, 30, -2.2)  # 2.2 m to the building across: a table leaves 1.1 m, a board 1.4 m
    narrow = _run([*ROW, across], monkeypatch=monkeypatch)
    assert _pieces(narrow) == [("board", 1.2, -0.55), ("planter", -1.15, -0.55)] and narrow.skipped == {"narrow": 2}
    assert narrow.shops == 1 and narrow.sets == 0
    road = _run(ROW, features=[_street("residential")], monkeypatch=monkeypatch)
    assert road.records == [] and road.skipped == {"not_pedestrian": 1} and road.shops == 0
    alone = _run([shapely_box(-3, 0, 3, 10)], monkeypatch=monkeypatch)  # no facade in line: nothing runs past the corner
    assert [row["role"] for row in alone.records] == ["table", "chair", "chair", "table", "chair", "chair"]
    assert alone.skipped == {"no_wall_behind": 2}
    far = _run(ROW, manifest=_manifest([("A", "bar", (0.0, -3.0))]), monkeypatch=monkeypatch)
    assert far.skipped == {"no_wall": 1}
    spawn = _run(ROW, monkeypatch=monkeypatch, near_spawn=lambda x, y: x > 0)
    assert [row["position_m"][0] < 0 for row in spawn.records] == [True] * 7 and spawn.skipped == {"spawn": 1}
    door = _run(ROW, manifest=_manifest([("A", "bar", (0.0, 0.0))], [[3.0, 0.0], [-3.0, 0.0]]), monkeypatch=monkeypatch)
    assert _pieces(door) == [("board", 1.2, -0.55), ("planter", -1.15, -0.55)] and door.skipped == {"door": 2}  # 0.42 m from a door
    kept = _run(ROW, monkeypatch=monkeypatch, keep_out=[Point(-2.0, -0.7).buffer(0.3)])
    assert [row["position_m"][0] for row in kept.records if row["role"] == "table"] == [1.8, 4.0] and kept.skipped == {}  # the other side
    both = _run(ROW, monkeypatch=monkeypatch, keep_out=[Point(-2.0, -0.7).buffer(0.3), Point(2.0, -0.7).buffer(0.3)])
    assert _pieces(both) == [("board", 1.2, -0.55), ("planter", -1.15, -0.55)] and both.skipped == {"urban_element": 2}
    tight = _run(ROW, monkeypatch=monkeypatch, budget=36 + 12)
    assert [row["role"] for row in tight.records] == ["table", "chair", "chair", "board"] and tight.skipped == {"budget": 2}
    edge = _run(ROW, monkeypatch=monkeypatch, config=replace(CFG, along_m=3.0))
    assert [row["role"] for row in edge.records] == ["table", "chair", "chair", "table", "chair", "chair"] and edge.skipped == {"no_frontage": 2}


def test_a_terrace_stays_out_of_buildings_and_the_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    posts = [shapely_box(2.5, -0.6, 2.6, -0.5), shapely_box(-2.6, -0.6, -2.5, -0.5)]  # a post either side, where a table's chair would stand
    blocked = _run([*ROW, *posts], monkeypatch=monkeypatch)
    assert _pieces(blocked) == [("board", 1.2, -0.55), ("planter", -1.15, -0.55)] and blocked.skipped == {"building": 2}
    one = _run([*ROW, posts[0]], monkeypatch=monkeypatch)  # one side blocked: the tables go to the other
    assert [row["position_m"][0] for row in one.records if row["role"] != "chair"] == [-1.8, -4.0, 1.2, -5.55] and one.skipped == {}
    monkeypatch.setattr(terraces, "chosen_models", lambda library, cfg: (dict(MODELS), {}))
    small = place_terraces(_manifest([("A", "bar", (0.0, 0.0))]), [_street()], STRtree(ROW), square_zone(2.6), "assets",
                           budget=100_000, ground_z=lambda x, y: 0.0, near_spawn=lambda x, y: False, config=CFG)
    assert _pieces(small) == [("board", 1.2, -0.55), ("planter", -1.15, -0.55)] and small.skipped == {"outside_zone": 2}


def test_two_bars_side_by_side_do_not_overlap_and_the_map_has_a_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    pair = _run(ROW, manifest=_manifest([("A", "bar", (0.0, 0.0)), ("B", "bar", (15.0, 0.0))]), monkeypatch=monkeypatch,
                config=replace(CFG, door_clear_m=0.5))
    boxes = pair.footprints
    assert not any(a.intersects(b) for i, a in enumerate(boxes) for b in boxes[i + 1:])
    assert pair.shops == 2 and pair.sets == 4
    capped = _run(ROW, manifest=_manifest([("A", "bar", (0.0, 0.0)), ("B", "bar", (15.0, 0.0))]), monkeypatch=monkeypatch,
                  config=replace(CFG, max_sets=1))
    assert capped.sets == 1 and capped.shops == 1 and capped.skipped == {"max_sets": 1}
    assert [row["role"] for row in capped.records] == ["table", "chair", "chair", "board", "planter"]


def test_no_bar_no_library_or_no_table_sets_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(terraces, "chosen_models", lambda library, cfg: calls.append(library) or ({}, {"table": "t (licence)"}))  # type: ignore[func-returns-value]
    assert _run(ROW, manifest=_manifest([("A", "bakery", (0.0, 0.0))])).report()["skipped"] == {} and calls == []
    bare = _run(ROW, monkeypatch=None)
    assert bare.skipped == {"no_table_or_chair": 1} and bare.report()["missing"] == {"table": "t (licence)"}
    none = place_terraces(_manifest([("A", "bar", (0.0, 0.0))]), [_street()], STRtree(ROW), square_zone(100.0), None,
                          budget=1, ground_z=lambda x, y: 0.0, near_spawn=lambda x, y: False, config=CFG)
    assert none.skipped == {"no_library": 1} and calls == ["assets"]
    chairs = _run(ROW, monkeypatch=monkeypatch, models={"table": MODELS["table"], "chair": MODELS["chair"]})
    assert [row["role"] for row in chairs.records] == ["table", "chair", "chair"] * 2 and chairs.pieces == {"table": "t", "chair": "c"}


def test_the_installed_catalogue_gives_every_piece() -> None:
    models, missing = chosen_models("assets", CFG)
    assert {slot: model.prop.spec.id for slot, model in models.items()} == {
        "table": "qt_home_Table_RoundSmall", "chair": "qt_home_Chair_1", "board": "standing_chalkboard_01", "planter": "kenney_furniture_pottedPlant"}
    assert missing == {} and all(model.licence == "CC0-1.0" for model in models.values())
    models, missing = chosen_models("assets", replace(CFG, pieces={"table": ("nope",), "chair": ("qt_home_Chair_1",), "board": (), "planter": ("x",)}))
    assert sorted(models) == ["chair"] and missing == {"table": "nope (not installed)", "planter": "x (not installed)"}


def test_the_ray_starts_out_of_the_shop_s_own_wall() -> None:
    tree = STRtree([shapely_box(-10, 0, 10, 10)])
    wall = wall_at((0.0, 0.0), tree)
    assert wall is not None
    assert free_across(wall.point(10.0, terraces.RAY_START_M), wall.n, tree, 30.0) == 30.0
