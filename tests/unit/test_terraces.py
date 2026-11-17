"""Café terraces before the bars of a lived street (overgrow/terraces.py; plan 2026-11-16, increment 2)."""

from __future__ import annotations

import copy
import io
import json
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

import numpy as np
import pytest
from PIL import Image
from shapely import STRtree
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
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
    TerraceConfig,
    TerraceError,
    _backed,
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
PLAIN = replace(CFG, parasol=None)
"""The rules without parasols: the tests of placement count the furniture only."""


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (8, 8), (90, 90, 90)).save(out, format="PNG")
    return out.getvalue()


def _model(asset_id: str, role: str, width: float, depth: float, height: float, category: str = "furniture") -> InteriorProp:
    spec = replace(ASSETS_BY_ID["wooden_picnic_table"], id=asset_id, role=role, category=category)
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
    options: dict[str, Any] = {"budget": 100_000, "ground_z": lambda x, y: 0.25, "near_spawn": lambda x, y: False, "config": PLAIN}
    options.update(kwargs)
    return place_terraces(manifest or _manifest([("A", "bar", (0.0, 0.0))]), features if features is not None else [_street()],
                          STRtree(buildings), square_zone(100.0), "assets", **options)


def test_the_configuration_is_read_whole() -> None:
    """Parsed here, not at import, so a changed parser is the one tested (mutmut forks after the module is loaded)."""
    cfg = parse_terraces(DOC, SCHEMA)
    assert cfg == TerraceConfig(
        shop_categories=frozenset({"bar"}), highways=frozenset({"pedestrian", "living_street"}), street_search_m=12.0, passage_m=1.2,
        wall_gap_m=0.3, door_clear_m=0.9, piece_gap_m=0.1, set_gap_m=0.4, reach_m=30.0, along_m=6.0, sets_per_shop=2, max_sets=24,
        pieces=MappingProxyType({"table": ("qt_home_Table_RoundSmall", "qt_furn_Table"), "chair": ("qt_home_Chair_1", "kenney_furniture_chair"),
                                 "board": ("standing_chalkboard_01",), "planter": ("kenney_furniture_pottedPlant",)}),
        parasol=terraces.Parasol(radius_m=1.1, rim_m=2.15, apex_m=2.5, pole_m=0.05),
    )
    assert parse_terraces({**DOC, "parasol": None}, SCHEMA).parasol is None
    assert cfg.parasol is not None and all(isinstance(v, float) for v in (cfg.parasol.radius_m, cfg.parasol.rim_m, cfg.parasol.pole_m))
    assert isinstance(cfg.pieces, MappingProxyType) and list(cfg.pieces) == ["table", "chair", "board", "planter"]
    assert all(isinstance(getattr(cfg, name), float) for name in ("street_search_m", "passage_m", "wall_gap_m", "door_clear_m", "piece_gap_m",
                                                                  "set_gap_m", "reach_m", "along_m"))
    assert isinstance(cfg.sets_per_shop, int) and isinstance(cfg.max_sets, int)
    whole = {**DOC, "street_search_m": 7, "sets_per_shop": 3, "max_sets": 9}
    other = parse_terraces(whole, SCHEMA)
    assert (other.street_search_m, other.sets_per_shop, other.max_sets) == (7.0, 3, 9) and isinstance(other.street_search_m, float)
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
    manifest["objects"].insert(0, "not a record")
    manifest["objects"].insert(1, {"type": "building", "connections": [{"type": "door", "from": "exterior", "to": "X", "position_m": [8, 8]}]})
    manifest["objects"].append({"type": "interior", "building_id": "D", "shop": {"category": "bar"}, "rooms": [{"id": "D/L0"}, {"id": "D/L1"}],
                                "connections": [{"type": "door", "from": "D/L0", "to": "D/L1", "position_m": [3, 5]},
                                                {"type": "stair", "from": "exterior", "to": "D/L0", "position_m": [3, 6]},
                                                {"type": "door", "from": "exterior", "to": "D/L0", "position_m": [3, 4, 0]}]})
    manifest["objects"].append({"type": "interior", "rooms": [{"id": "E/L0"}, {"id": "E/L1", "shop": {"category": "bar"}}],
                                "connections": [{"type": "door", "from": "exterior", "to": "E/L0", "position_m": [7, 7]},
                                                {"type": "door", "from": "exterior", "to": "E/L1", "position_m": [6, 6]}]})
    assert shop_doors(manifest) == [("B", "bar", (5.0, 1.0)), ("A", "bakery", (1.0, 2.0)), ("C", "bar", (0.0, 0.0)), ("D", "bar", (3.0, 4.0)),
                                    ("", "bar", (6.0, 6.0))]
    fronts = shop_fronts(manifest, {"bar"})
    assert [(f.building_id, f.category, f.door) for f in fronts] == [
        ("", "bar", (6.0, 6.0)), ("B", "bar", (5.0, 1.0)), ("C", "bar", (0.0, 0.0)), ("D", "bar", (3.0, 4.0))]
    assert other_doors(manifest) == [(5.0, 1.0), (1.0, 2.0), (0.0, 0.0), (3.0, 4.0), (6.0, 6.0), (9.0, 9.0)]


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
    slim = Polygon(shapely_box(-2.5, -10, 2.5, 10).exterior.coords, [shapely_box(-2, -2, 2, 2).exterior.coords])
    near_corner = wall_at((-1.9, -2.0), STRtree([slim]))  # the side test is made at the door, not elsewhere on the wall's line
    assert near_corner is not None and tuple(near_corner.n) == (0.0, 1.0) and near_corner.door_s == pytest.approx(0.1)
    far_end = wall_at((1.9, -2.0), STRtree([slim]))
    assert far_end is not None and tuple(far_end.n) == (0.0, 1.0) and far_end.door_s == pytest.approx(3.9)
    assert wall_at((0.0, -1.0), tree) is not None  # exactly WALL_SEARCH_M away still counts
    corner = wall_at((10.0, 0.0), tree)  # on two edges: the first of the ring wins
    assert corner is not None and tuple(corner.u) == (0.0, 1.0) and tuple(corner.n) == (1.0, 0.0)
    start = wall_at((-9.5, 0.0), tree)
    assert start is not None and start.door_s == pytest.approx(0.5)


def test_a_wall_at_an_angle_a_repeated_vertex_and_other_shapes() -> None:
    diamond = Polygon([(0, -10), (10, 0), (0, 10), (-10, 0)])
    wall = wall_at((5.0, -5.0), STRtree([diamond]))
    assert wall is not None and np.allclose(wall.n, [np.sqrt(0.5), -np.sqrt(0.5)]) and np.isclose(wall.n @ wall.u, 0.0)
    doubled = Polygon([(-10, 0), (-10, 0), (10, 0), (10, 10), (-10, 10)])
    found = wall_at((0.0, 0.0), STRtree([doubled]))
    assert found is not None and tuple(found.n) == (0.0, -1.0) and found.door_s == pytest.approx(10.0)
    lifted = Polygon([(-10, 0, 3), (10, 0, 3), (10, 10, 3), (-10, 10, 3)])  # a footprint with heights is read in plan
    raised = wall_at((0.0, 0.0), STRtree([lifted]))
    assert raised is not None and tuple(raised.n) == (0.0, -1.0)
    mixed = wall_at((0.0, 0.0), STRtree([LineString([(-1, -0.5), (1, -0.5)]), shapely_box(-10, 0, 10, 10)]))
    assert mixed is not None and tuple(mixed.n) == (0.0, -1.0)  # a line in the tree is passed over, not the end of the search


def test_the_street_before_a_terrace() -> None:
    plaza = {"kind": "highway", "tags": {"highway": "pedestrian", "area": "yes"},
             "geometry": {"type": "Polygon", "coordinates": [[[50, 50], [60, 50], [60, 60], [50, 60], [50, 50]]]}}
    road = {"kind": "highway", "tags": {"highway": "residential"},
            "geometry": {"type": "MultiLineString", "coordinates": [[[-40, 30], [40, 30]], [[-40, 31], [40, 31]]]}}
    hall = {"kind": "highway", "tags": {"highway": "corridor"}, "geometry": {"type": "LineString", "coordinates": [[-40, 0], [40, 0]]}}
    streets = streets_of([{"kind": "building", "geometry": {"type": "Point", "coordinates": [0, 0]}}, _street(), road, plaza, hall,
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
    square = {"kind": "highway", "tags": {"highway": "pedestrian"},
              "geometry": {"type": "MultiPolygon", "coordinates": [[[[50, -5], [60, -5], [60, 5], [50, 5], [50, -5]]],
                                                                   [[[70, -5], [80, -5], [80, 5], [70, 5], [70, -5]]]]}}
    squares = streets_of([square])
    assert squares.lines == () and len(squares.plazas) == 2 and all(isinstance(p, Polygon) for p in squares.plazas)
    assert not isinstance(squares.plazas[0], MultiPolygon)
    assert street_reason(np.array([55.0, 0.0]), squares, CFG) is None and street_reason(np.array([65.0, 0.0]), squares, CFG) == "no_street"
    untagged = streets_of([{"kind": "highway", "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 0]]}}])
    assert [highway for _, highway in untagged.lines] == [""]


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
    probed: list[tuple[float, float]] = []
    result = _run(ROW, monkeypatch=monkeypatch, near_spawn=lambda x, y: probed.append((round(x, 3), round(y, 3))) is None and False)
    assert probed == [(1.8, -0.7), (-1.8, -0.7), (3.4, -0.55), (-3.35, -0.55)]  # each unit's middle is asked about the spawns
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
    assert [bool(node.primitives) for node in result.nodes] == [True, True, False, False, False, False, True, True]  # copies share a mesh
    assert np.allclose(table.matrix @ np.array([1.4, 2.4, 0.0, 1.0]), [1.8, -0.7, 0.25, 1.0])  # the model's box centre stands on the spot
    assert table.extras == {"type": "overgrowth", "origin": "generated", "attribute_origin": "generated", "semantics_origin": "estimated",
                            "kind": "terrace", "slot": "table", "asset_id": "t", "catalogue": "kenney_furniture", "licence": "CC0-1.0",
                            "role": "table", "building_id": "A", "solid": True}
    assert result.nodes[1].extras["slot"] == "chair" and result.nodes[1].extras["solid"] is False
    collider = result.colliders[0]
    assert collider.extras == {"type": "collision", "origin": "generated", "asset_id": "t", "role": "table", "terrace": True}
    assert np.array_equal(collider.matrix, table.matrix) and collider.primitives[0].material == "collider"
    corners = collider.primitives[0].mesh.vertices
    assert np.allclose(corners.min(axis=0), [1.0, 2.0, 0.0]) and np.allclose(corners.max(axis=0), [1.8, 2.8, 0.75])
    assert result.records[0] == {"kind": "terrace", "origin": "generated", "source_id": "A", "position_m": [1.8, -0.7, 0.25],
                                 "asset_id": "t", "role": "table", "size_m": [0.8, 0.8, 0.75], "solid": True}
    assert [row["solid"] for row in result.records] == [True, False, False, True, False, False, True, True]  # a chair stays visual
    assert [node.name for node in result.colliders] == [COLLIDER_PREFIX + n for n in ("0/0", "1/0", "2/0", "3/0")]
    assert result.triangles == 8 * 12 and set(result.images) == {"prop_t", "prop_c", "prop_b", "prop_p"}
    assert result.records[0]["size_m"] == [0.8, 0.8, 0.75] and result.records[0]["source_id"] == "A"
    report = result.report()
    assert report == {"shops": 1, "sets": 2, "pieces": 8, "colliders": 4, "parasols": 0, "triangles": 96,
                      "models": {"board": "b", "chair": "c", "planter": "p", "table": "t"}, "skipped": {}, "missing": {}}


def test_what_collides_and_where_the_ground_is(monkeypatch: pytest.MonkeyPatch) -> None:
    models = {
        "table": _model("t", "table", 0.8, 0.8, 0.45),  # too low for cover: solid because it is a table
        "chair": _model("c", "chair", 0.4, 0.4, 1.2),  # tall enough to be solid, but a chair stays visual
        "board": _model("b", "sign", 0.6, 0.15, 0.7),  # 15 cm thin: not cover
        "planter": _model("p", "planter", 0.15, 0.5, 0.6),
    }
    result = _run(ROW, monkeypatch=monkeypatch, models=models, ground_z=lambda x, y: round(x / 10 - y, 3))
    assert [(row["role"], row["solid"]) for row in result.records if row["role"] != "chair"] == [
        ("table", True), ("table", True), ("board", False), ("planter", False)]
    assert not any(row["solid"] for row in result.records if row["role"] == "chair")
    assert [row["position_m"][2] for row in result.records] == [round(x / 10 - y, 3) for _, x, y in _pieces(result)]
    green = _run(ROW, monkeypatch=monkeypatch, models={**MODELS, "planter": _model("p", "planter", 0.5, 0.5, 1.0, "vegetation")})
    assert [row["solid"] for row in green.records if row["role"] == "planter"] == [False]  # a plant never stops a body


def test_a_terrace_keeps_the_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    across = shapely_box(-30, -20, 30, -2.2)  # 2.2 m to the building across: a table leaves 1.1 m, a board 1.4 m
    narrow = _run([*ROW, across], monkeypatch=monkeypatch)
    assert _pieces(narrow) == [("board", 1.2, -0.55), ("planter", -1.15, -0.55)] and narrow.skipped == {"narrow": 2}
    assert narrow.shops == 1 and narrow.sets == 0
    wide = _run([*ROW, shapely_box(-30, -20, 30, -2.35)], monkeypatch=monkeypatch)  # 2.35 m: the 5 cm the ray starts out are not free
    assert [row["role"] for row in wide.records] == ["board", "planter"] and wide.skipped == {"narrow": 2}
    road = _run(ROW, manifest=_manifest([("A", "bar", (0.0, 0.0)), ("B", "bar", (15.0, 0.0))]), features=[_street("residential")],
                monkeypatch=monkeypatch)
    assert road.records == [] and road.skipped == {"not_pedestrian": 2} and road.shops == 0
    behind = _run(ROW, features=[_street("pedestrian", -6.0), _street("residential", 3.0)], monkeypatch=monkeypatch)
    assert behind.sets == 2  # the street is looked for out of the terrace, not behind the wall
    alone = _run([shapely_box(-3, 0, 3, 10)], monkeypatch=monkeypatch)  # no facade in line: nothing runs past the corner
    assert [row["role"] for row in alone.records] == ["table", "chair", "chair", "table", "chair", "chair"]
    assert alone.skipped == {"no_wall_behind": 2}
    far = _run(ROW, manifest=_manifest([("A", "bar", (0.0, -3.0)), ("B", "bar", (5.0, -3.0)), ("C", "bar", (15.0, 0.0))]), monkeypatch=monkeypatch)
    assert far.skipped == {"no_wall": 2} and far.shops == 1 and far.sets == 2  # a front with no wall does not stop the next
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
    edge = _run(ROW, monkeypatch=monkeypatch, config=replace(PLAIN, along_m=3.0))
    assert [row["role"] for row in edge.records] == ["table", "chair", "chair", "table", "chair", "chair"] and edge.skipped == {"no_frontage": 2}
    width = terraces.unit_size("table", MODELS, CFG)[0]
    reach = (10.0 + CFG.door_clear_m + width) - 10.0  # the far end of the first table, as the code adds it up
    exact = _run(ROW, monkeypatch=monkeypatch, config=replace(PLAIN, along_m=reach, sets_per_shop=1),
                 models={"table": MODELS["table"], "chair": MODELS["chair"]})
    assert exact.sets == 1  # a table ending exactly along_m from the door stands
    none = _run(ROW, monkeypatch=monkeypatch, config=replace(PLAIN, along_m=1.0))
    assert none.records == [] and none.shops == 0 and none.skipped == {"no_frontage": 4}


def test_a_terrace_stays_out_of_buildings_and_the_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    posts = [shapely_box(2.5, -0.6, 2.6, -0.5), shapely_box(-2.6, -0.6, -2.5, -0.5)]  # a post either side, where a table's chair would stand
    blocked = _run([*ROW, *posts], monkeypatch=monkeypatch)
    assert _pieces(blocked) == [("board", 1.2, -0.55), ("planter", -1.15, -0.55)] and blocked.skipped == {"building": 2}
    one = _run([*ROW, posts[0]], monkeypatch=monkeypatch)  # one side blocked: the tables go to the other
    assert [row["position_m"][0] for row in one.records if row["role"] != "chair"] == [-1.8, -4.0, 1.2, -5.55] and one.skipped == {}
    monkeypatch.setattr(terraces, "chosen_models", lambda library, cfg: (dict(MODELS), {}))
    small = place_terraces(_manifest([("A", "bar", (0.0, 0.0))]), [_street()], STRtree(ROW), square_zone(2.6), "assets",
                           budget=100_000, ground_z=lambda x, y: 0.0, near_spawn=lambda x, y: False, config=PLAIN)
    assert _pieces(small) == [("board", 1.2, -0.55), ("planter", -1.15, -0.55)] and small.skipped == {"outside_zone": 2}


def test_two_bars_side_by_side_do_not_overlap_and_the_map_has_a_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    pair = _run(ROW, manifest=_manifest([("A", "bar", (0.0, 0.0)), ("B", "bar", (15.0, 0.0))]), monkeypatch=monkeypatch,
                config=replace(PLAIN, door_clear_m=0.5))
    boxes = pair.footprints
    assert not any(a.intersects(b) for i, a in enumerate(boxes) for b in boxes[i + 1:])
    assert pair.shops == 2 and pair.sets == 4
    capped = _run(ROW, manifest=_manifest([("A", "bar", (0.0, 0.0)), ("B", "bar", (15.0, 0.0)), ("C", "bar", (-15.0, 0.0))]),
                  monkeypatch=monkeypatch, config=replace(PLAIN, max_sets=1))
    assert capped.sets == 1 and capped.shops == 1 and capped.skipped == {"max_sets": 2}
    assert [row["role"] for row in capped.records] == ["table", "chair", "chair", "board", "planter"]
    close = _run(ROW, manifest=_manifest([("A", "bar", (0.0, 0.0)), ("B", "bar", (4.5, 0.0))]), monkeypatch=monkeypatch,
                 keep_out=[Point(6.3, -0.7).buffer(0.2)])
    boxes = close.footprints
    assert not any(a.intersects(b) for i, a in enumerate(boxes) for b in boxes[i + 1:])
    assert close.skipped == {"overlap": 1, "urban_element": 2}  # B's left side is A's terrace, its right side an urban element


def test_no_bar_no_library_or_no_table_sets_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(terraces, "chosen_models", lambda library, cfg: calls.append(library) or ({}, {"table": "t (licence)"}))  # type: ignore[func-returns-value]
    assert _run(ROW, manifest=_manifest([("A", "bakery", (0.0, 0.0))])).report()["skipped"] == {} and calls == []
    bare = _run(ROW, monkeypatch=None)
    assert bare.skipped == {"no_table_or_chair": 1} and bare.report()["missing"] == {"table": "t (licence)"}
    two = _manifest([("A", "bar", (0.0, 0.0)), ("B", "bar", (15.0, 0.0))])
    assert _run(ROW, manifest=two).skipped == {"no_table_or_chair": 2}
    none = place_terraces(two, [_street()], STRtree(ROW), square_zone(100.0), None,
                          budget=1, ground_z=lambda x, y: 0.0, near_spawn=lambda x, y: False, config=PLAIN)
    assert none.skipped == {"no_library": 2} and calls == ["assets", "assets"]
    tables = _run(ROW, monkeypatch=monkeypatch, models={"table": MODELS["table"], "board": MODELS["board"]})
    assert tables.records == [] and tables.skipped == {"no_table_or_chair": 1}  # a table without chairs sets nothing
    chairs = _run(ROW, monkeypatch=monkeypatch, models={"table": MODELS["table"], "chair": MODELS["chair"]})
    assert [row["role"] for row in chairs.records] == ["table", "chair", "chair"] * 2 and chairs.pieces == {"table": "t", "chair": "c"}


def test_the_installed_catalogue_gives_every_piece() -> None:
    models, missing = chosen_models("assets", CFG)
    assert {slot: model.prop.spec.id for slot, model in models.items()} == {
        "table": "qt_home_Table_RoundSmall", "chair": "qt_home_Chair_1", "board": "standing_chalkboard_01", "planter": "kenney_furniture_pottedPlant"}
    assert missing == {} and all(model.licence == "CC0-1.0" for model in models.values())
    models, missing = chosen_models("assets", replace(CFG, pieces={"table": ("nope",), "chair": ("qt_home_Chair_1",), "board": (), "planter": ("x",)}))
    assert sorted(models) == ["chair"] and missing == {"table": "nope (not installed)", "planter": "x (not installed)"}
    _, two = chosen_models("assets", replace(CFG, pieces={**CFG.pieces, "table": ("nope", "never")}))
    assert two == {"table": "nope (not installed), never (not installed)"}


def test_a_refused_row_says_why(tmp_path: Path) -> None:
    catalogue = tmp_path / "kenney_furniture"
    catalogue.mkdir()
    document = json.loads(Path("assets/kenney_furniture/manifest.json").read_text(encoding="utf-8"))
    document["source"] = "a proprietary pack"
    (catalogue / "manifest.json").write_text(json.dumps(document), encoding="utf-8")
    _, missing = chosen_models(tmp_path, replace(CFG, pieces={**CFG.pieces, "chair": ("kenney_furniture_chair",)}))
    assert missing["chair"] == "kenney_furniture_chair (licence)"


def test_place_terraces_reads_the_installed_catalogue_with_its_configuration() -> None:
    result = place_terraces(_manifest([("A", "bar", (0.0, 0.0))]), [_street()], STRtree(ROW), square_zone(100.0), "assets", budget=100_000,
                            ground_z=lambda x, y: 0.0, near_spawn=lambda x, y: False, config=replace(PLAIN, sets_per_shop=1))
    assert result.pieces == {"table": "qt_home_Table_RoundSmall", "chair": "qt_home_Chair_1", "board": "standing_chalkboard_01",
                             "planter": "kenney_furniture_pottedPlant"}
    assert result.sets == 1 and result.missing == {}


def test_a_wall_is_backed_when_nine_tenths_of_the_strip_is_building() -> None:
    strip = shapely_box(0, 0, 10, 1)
    assert _backed(strip, STRtree([shapely_box(0, 0, 9, 1)])) and not _backed(strip, STRtree([shapely_box(0, 0, 8.9, 1)]))
    assert _backed(strip, STRtree([shapely_box(0, 0, 5, 1), shapely_box(5, 0, 10, 2)]))  # two buildings add up


def test_the_ray_starts_out_of_the_shop_s_own_wall() -> None:
    tree = STRtree([shapely_box(-10, 0, 10, 10)])
    wall = wall_at((0.0, 0.0), tree)
    assert wall is not None
    assert free_across(wall.point(10.0, terraces.RAY_START_M), wall.n, tree, 30.0) == 30.0


def test_the_records_keep_millimetres_and_a_chair_clears_its_table_s_width(monkeypatch: pytest.MonkeyPatch) -> None:
    models = {"table": _model("t", "table", 0.81234, 0.6, 0.74567), "chair": _model("c", "chair", 0.43219, 0.41111, 0.9)}
    shifted = [shapely_box(x0, 0.12345, x1, 10) for x0, x1 in ((-10, 10), (10, 20), (-20, -10))]
    result = _run(shifted, manifest=_manifest([("A", "bar", (0.12345, 0.12345))]), monkeypatch=monkeypatch, models=models,
                  ground_z=lambda x, y: x / 7)
    assert result.records[0]["size_m"] == [0.812, 0.6, 0.746] and result.records[1]["size_m"] == [0.432, 0.411, 0.9]
    for row in result.records:
        assert all(value == round(value, 3) for value in row["position_m"]), row["position_m"]
    table, chair = result.records[0]["position_m"], result.records[1]["position_m"]
    assert chair[0] - table[0] == pytest.approx(0.81234 / 2 + CFG.piece_gap_m + 0.41111 / 2, abs=1e-3)


def test_each_table_stands_under_a_parasol_in_its_bar_s_colour(monkeypatch: pytest.MonkeyPatch) -> None:
    from onward_generator.detail.facades import stable_unit
    from onward_generator.overgrow.cloth import FABRIC_MATERIAL, band_v

    result = _run(ROW, monkeypatch=monkeypatch, config=CFG, ground_z=lambda x, y: 0.25)
    assert [row["role"] for row in result.records] == ["table", "chair", "chair", "parasol", "table", "chair", "chair", "parasol", "board", "planter"]
    assert result.parasols == 2 and result.report()["parasols"] == 2 and result.report()["pieces"] == 10
    assert result.triangles == 8 * 12 + 2 * 32
    shade = result.nodes[3]
    assert shade.name == NODE_PREFIX + "0/parasol" and shade.matrix is None and shade.mesh_key is None
    assert shade.extras == {"type": "overgrowth", "origin": "generated", "attribute_origin": "generated", "kind": "terrace", "slot": "parasol",
                            "building_id": "A", "solid": False}
    primitive = shade.primitives[0]
    assert primitive.material == FABRIC_MATERIAL and primitive.mesh.triangle_count == 32
    assert np.allclose(primitive.mesh.vertices[0], [1.8, -0.7, 2.75])  # its apex over the table, the table on the ground
    assert np.allclose(primitive.mesh.vertices[1], [2.9, -0.7, 2.4])
    band = int(stable_unit(0, "A", "parasol") * 3)
    assert np.allclose(primitive.uvs[0], [0.5, band_v(band, 0.0)])
    assert result.records[3] == {"kind": "terrace", "origin": "generated", "source_id": "A", "role": "parasol", "position_m": [1.8, -0.7, 0.25],
                                 "size_m": [2.2, 2.2, 2.5], "solid": False}
    assert len(result.colliders) == 4  # a parasol does not collide
    tight = _run(ROW, monkeypatch=monkeypatch, config=CFG, budget=36 + 31)
    assert tight.sets == 0 and tight.skipped == {"budget": 2}  # a table and its parasol fit together or not at all
    snug = _run(ROW, monkeypatch=monkeypatch, config=replace(CFG, sets_per_shop=1), budget=36 + 32)
    assert snug.sets == 1 and snug.parasols == 1


def test_a_parasol_stands_on_its_table_s_ground_in_its_bar_s_colour(monkeypatch: pytest.MonkeyPatch) -> None:
    from onward_generator.detail.facades import stable_unit
    from onward_generator.overgrow.cloth import band_v

    odd = replace(CFG, parasol=terraces.Parasol(radius_m=1.23456, rim_m=2.15, apex_m=2.51234, pole_m=0.05), sets_per_shop=1)
    shifted = [shapely_box(x0, 0.12345, x1, 10) for x0, x1 in ((-10, 10), (10, 20), (-20, -10))]
    result = _run(shifted, manifest=_manifest([("A", "bar", (0.12345, 0.12345))]), monkeypatch=monkeypatch, config=odd,
                  ground_z=lambda x, y: x / 7 - y)
    table = result.records[0]
    shade = next(row for row in result.records if row["role"] == "parasol")
    assert shade["size_m"] == [2.469, 2.469, 2.512]
    assert shade["position_m"][:2] == table["position_m"][:2] and all(v == round(v, 3) for v in shade["position_m"])
    x, y = table["position_m"][:2]
    assert shade["position_m"][2] == pytest.approx(x / 7 - y, abs=1e-3)
    apex = next(node for node in result.nodes if node.name.endswith("/parasol")).primitives[0].mesh.vertices[0]
    assert apex[2] == pytest.approx(x / 7 - y + 2.51234, abs=2e-3)
    doors = [(f"B{n}", "bar", (float(-50 + 20 * n), 0.0)) for n in range(6)]
    row = [shapely_box(-80, 0, 80, 10)]
    many = _run(row, manifest=_manifest(doors), monkeypatch=monkeypatch, config=replace(CFG, sets_per_shop=1))
    shades = [node for node in many.nodes if node.name.endswith("/parasol")]
    bands = [int(stable_unit(0, f"B{n}", "parasol") * 3) for n in range(6)]
    assert len(shades) == 6 and len(set(bands)) > 1
    for node, band in zip(shades, bands, strict=True):
        assert np.allclose(node.primitives[0].uvs[0], [0.5, band_v(band, 0.0)])
