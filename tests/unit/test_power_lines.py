"""Overhead power lines: poles, spans and service drops drawn by code (overgrow/power_lines.py, ADR-045).

The configuration is parsed inside each test, never at import: a value read before the mutation run forks hides the
mutants of the parser.
"""

from __future__ import annotations

import io
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image
from shapely import LineString, Point, STRtree
from shapely.geometry import box as shapely_box

from onward_generator.generation.gltf import vertex_normals
from onward_generator.overgrow import power_lines as pl
from onward_generator.overgrow.power_lines import (
    LINE_MATERIAL,
    LineConfig,
    LinesError,
    Pole,
    Street,
    attach_points,
    building_heights,
    crosses,
    drops,
    lines_enabled,
    lines_png,
    load_lines,
    parse_lines,
    place_lines,
    place_poles,
    pole_mesh,
    pole_spot,
    sag_points,
    spans,
    streets_for,
    wire_mesh,
)
from onward_generator.overgrow.zone import square_zone

DOC_PATH = pl.LINES_PATH
SCHEMA_PATH = pl.SCHEMA_PATH


def _doc() -> dict[str, Any]:
    return json.loads(DOC_PATH.read_text(encoding="utf-8"))


def _schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def cfg(**changes: Any) -> LineConfig:
    return replace(parse_lines(_doc(), _schema()), **changes)


def _never(x: float, y: float) -> bool:
    return False


def _flat(x: float, y: float) -> float:
    return 0.0


NORTH = shapely_box(-60, 6, 60, 16)
SOUTH = shapely_box(-60, -16, 60, -6)


def _street(name: str = "osm-way-1", x0: float = -50.0, x1: float = 50.0, width: float = 6.0, highway: str = "residential") -> dict[str, Any]:
    return {"id": name, "kind": "highway", "tags": {"highway": highway},
            "geometry": {"type": "LineString", "coordinates": [[x0, 0.0], [x1, 0.0]]},
            "attributes": {"width_m": {"value": width}}}


def _building(name: str, polygon: Any) -> dict[str, Any]:
    return {"id": name, "kind": "building", "tags": {"building": "yes"},
            "geometry": {"type": "Polygon", "coordinates": [list(polygon.exterior.coords)]}}


def _manifest(heights: dict[str, float], doors: list[list[float]] | None = None) -> dict[str, Any]:
    objects = [{"type": "building", "source_ids": [name], "height": {"height_m": h}} for name, h in heights.items()]
    if doors:
        objects.append({"type": "building", "source_ids": ["doors"], "facade": {"doors_m": doors}})
    return {"objects": objects}


# ------------------------------------------------------------------------------------------------ configuration


def test_the_configuration_is_read_whole() -> None:
    config = parse_lines(_doc(), _schema())
    assert config == LineConfig(
        styles=frozenset({"east_african", "arid", "latin_american"}),
        highways=frozenset({"primary", "secondary", "tertiary", "unclassified", "residential", "living_street", "service", "track"}),
        spacing_m=28.0, kerb_m=0.5, back_m=1.4, min_axis_m=1.5, pole_height_m=8.0, pole_radius_m=0.12, crossarm_m=1.4, max_span_m=40.0,
        sag_ratio=0.03, wire_m=0.035, drops_per_pole=2, drop_reach_m=14.0, drop_min_z_m=3.0, drop_max_z_m=5.0, clear_m=1.0,
        max_poles=40, pole_colour=(118, 112, 104), wire_colour=(26, 26, 28),
    )
    assert all(isinstance(getattr(config, name), float) for name in ("spacing_m", "clear_m", "drop_reach_m", "wire_m"))
    assert isinstance(config.drops_per_pole, int) and isinstance(config.max_poles, int)
    assert load_lines() == config


@pytest.mark.parametrize(("change", "message"), [
    (lambda d: d.update(schema="other"), "^overhead_lines: schema: 'onward_generator.overhead_lines' was expected$"),
    (lambda d: d.pop("styles"), "^overhead_lines: 'styles' is a required property$"),
    (lambda d: d.update(back_m=1.0), "^overhead_lines: back_m: 1.0 is less than the minimum of 1.2$"),
    (lambda d: d.update(pole_colour=[1, 2]), "^overhead_lines: pole_colour: "),
    (lambda d: d.update(pole_colour=[1, 2, 300]), "^overhead_lines: pole_colour/2: 300 is greater than the maximum of 255$"),
    (lambda d: d.update(extra=1), "^overhead_lines: Additional properties"),
])
def test_a_bad_configuration_is_refused(change: Any, message: str) -> None:
    doc = _doc()
    change(doc)
    with pytest.raises(LinesError, match=message):
        parse_lines(doc, _schema())


def test_an_unreadable_configuration_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "overhead_lines.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(LinesError, match=f"^{path}: "):
        load_lines.__wrapped__(path)


def test_the_facade_style_decides_unless_the_map_says() -> None:
    config = cfg()
    assert lines_enabled(None, "east_african", config) and lines_enabled(None, "arid", config)
    assert lines_enabled(None, "latin_american", config)
    assert not lines_enabled(None, "mediterranean", config) and not lines_enabled(None, "haussmann", config)
    assert lines_enabled(True, "mediterranean", config) and not lines_enabled(False, "east_african", config)


def test_the_image_is_the_poles_colour_over_the_wires() -> None:
    image = Image.open(io.BytesIO(lines_png(cfg(pole_colour=(10, 20, 30), wire_colour=(40, 50, 60)))))
    assert image.size == (16, 16) and image.mode == "RGB"
    assert image.getpixel((0, 0)) == (10, 20, 30) and image.getpixel((15, 7)) == (10, 20, 30)
    assert image.getpixel((0, 8)) == (40, 50, 60) and image.getpixel((15, 15)) == (40, 50, 60)
    assert (pl.POLE_V, pl.WIRE_V) == (0.25, 0.75)  # the middle of each half


# ------------------------------------------------------------------------------------------------ meshes


def test_a_pole_is_a_tapered_capped_prism_with_a_crossarm() -> None:
    mesh, uvs = pole_mesh(np.array([2.0, 3.0, 0.5]), 8.0, 0.2, 1.4, np.array([0.0, 1.0]))
    assert mesh.vertex_count == 8 + 8 + 8 and mesh.triangle_count == pl.POLE_TRIANGLES == 34
    v = mesh.vertices
    assert np.allclose(v[0], [2.2, 3.0, 0.5]) and np.allclose(v[2], [2.0, 3.2, 0.5])  # the foot ring, a quarter turn every two
    assert np.allclose(v[8], [2.15, 3.0, 8.5]) and np.allclose(v[10], [2.0, 3.15, 8.5])  # the top ring, three quarters as wide
    assert np.allclose(v[16:], [[2.05, 2.3, 8.05], [2.05, 3.7, 8.05], [1.95, 3.7, 8.05], [1.95, 2.3, 8.05],
                                [2.05, 2.3, 8.15], [2.05, 3.7, 8.15], [1.95, 3.7, 8.15], [1.95, 2.3, 8.15]])
    assert mesh.triangles[:2].tolist() == [[0, 1, 9], [0, 9, 8]] and mesh.triangles[14:16].tolist() == [[7, 0, 8], [7, 8, 15]]
    assert mesh.triangles[16].tolist() == [8, 9, 10] and mesh.triangles[21].tolist() == [8, 14, 15]  # the cap's fan
    assert mesh.triangles[22:].tolist() == [[16 + i for i in face] for face in (
        [0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7])]
    assert mesh.vertices.dtype == np.float64 and mesh.triangles.dtype == np.int64
    assert np.allclose(uvs, [0.5, 0.25]) and uvs.shape == (24, 2)
    normals = mesh.face_normals()
    assert normals[16][2] > 0 and normals[22][2] < 0 and normals[24][2] > 0  # the cap faces up; the arm's bottom down, its top up


def test_the_wires_rest_on_the_crossarm() -> None:
    tops = attach_points(np.array([2.0, 3.0, 0.5]), 8.0, 1.4, np.array([0.0, 1.0]))
    assert np.allclose(tops, [[2.0, 2.4, 8.2], [2.0, 3.0, 8.2], [2.0, 3.6, 8.2]])


def test_a_wire_sags_as_a_parabola() -> None:
    points = sag_points(np.array([0.0, 0.0, 8.0]), np.array([10.0, 0.0, 6.0]), 0.5, segments=4)
    assert np.allclose(points, [[0, 0, 8], [2.5, 0, 7.125], [5, 0, 6.5], [7.5, 0, 6.125], [10, 0, 6]])
    assert len(sag_points(np.zeros(3), np.ones(3), 0.0)) == pl.WIRE_SEGMENTS + 1 == 9


def test_a_wire_is_two_crossed_ribbons_drawn_both_ways() -> None:
    mesh, uvs = wire_mesh(np.array([0.0, 0.0, 8.0]), np.array([10.0, 0.0, 8.0]), 0.0, 0.04)
    assert mesh.triangle_count == pl.WIRE_TRIANGLES == 64 and mesh.vertex_count == 2 * 4 * 9
    v = mesh.vertices
    assert np.allclose(v[0], [0, -0.02, 8]) and np.allclose(v[9], [0, 0.02, 8])  # the flat ribbon across the run
    assert np.allclose(v[18], [0, 0, 7.98]) and np.allclose(v[27], [0, 0, 8.02])  # the upright one
    assert np.allclose(v[17], [10, 0.02, 8]) and np.allclose(v[72 // 2:], v[:72 // 2])
    front = [tri for first, second in ((0, 9), (18, 27)) for k in range(8)
             for tri in ([first + k, second + k, second + k + 1], [first + k, second + k + 1, first + k + 1])]
    assert mesh.triangles.tolist() == front + [[c + 36 for c in reversed(tri)] for tri in front]
    assert np.allclose(uvs, [0.5, 0.75])
    assert np.allclose(np.linalg.norm(vertex_normals(mesh), axis=1), 1.0)  # each side keeps its normal
    upright = wire_mesh(np.array([1.0, 1.0, 5.0]), np.array([1.0, 1.0, 7.0]), 0.0, 0.04)[0]
    assert np.allclose(upright.vertices[0], [0.98, 1.0, 5.0])  # a wire with no run in plan still has a width
    diagonal = wire_mesh(np.array([0.0, 0.0, 5.0]), np.array([3.0, 4.0, 5.0]), 0.0, 0.1)[0]
    assert np.allclose(diagonal.vertices[0], [0.04, -0.03, 5.0])
    short = wire_mesh(np.array([0.0, 0.0, 5.0]), np.array([0.3, 0.4, 5.0]), 0.0, 0.1)[0]
    assert np.allclose(short.vertices[0], [0.04, -0.03, 5.0])  # half a metre in plan still runs across its own way


# ------------------------------------------------------------------------------------------------ streets and buildings


def test_the_streets_a_line_follows() -> None:
    features = [
        _street("short", 0, 10), _street("long", -50, 50, width=8.0), _street("path", 0, 30, highway="footway"),
        {"id": "nowidth", "kind": "highway", "tags": {"highway": "track"}, "geometry": {"type": "MultiLineString", "coordinates": [
            [[0, 5], [20, 5]], [[0, 9], [20, 9]]]}},
        {"id": "nogeom", "kind": "highway", "tags": {"highway": "residential"}},
        {"id": "point", "kind": "highway", "tags": {"highway": "residential"}, "geometry": {"type": "LineString", "coordinates": [[1, 1], [1, 1]]}},
        _building("b", NORTH),
    ]
    found = streets_for(features, cfg().highways)
    assert [(s.feature_id, s.half_width, round(s.line.length, 3)) for s in found] == [
        ("long", 4.0, 100.0), ("nowidth", 3.0, 20.0), ("nowidth#1", 3.0, 20.0), ("short", 3.0, 10.0)]
    zero = streets_for([{**_street("zero"), "attributes": {"width_m": {"value": 0}}}], cfg().highways)
    assert zero[0].half_width == 3.0
    narrow = {"kind": "highway", "tags": {"highway": "service"}, "geometry": {"type": "LineString", "coordinates": [[0, 0], [0.5, 0]]},
              "attributes": {"width_m": {"value": 0.5}}}
    assert [(s.feature_id, s.half_width, s.line.length) for s in streets_for([narrow], cfg().highways)] == [("", 0.25, 0.5)]
    assert streets_for([{**_street("untagged"), "tags": {}}], cfg().highways) == []


def test_each_footprint_takes_the_height_detail_gave_it() -> None:
    features = [_building("a", NORTH), _building("b", SOUTH), {"id": "c", "kind": "building", "geometry": {"type": "MultiPolygon", "coordinates": [
        [list(shapely_box(0, 0, 1, 1).exterior.coords)], [list(shapely_box(2, 0, 3, 1).exterior.coords)]]}}, _street()]
    manifest = {"objects": [{"type": "building", "source_ids": ["a"], "height": {"height_m": 7}},
                            {"type": "building", "source_ids": ["c"], "height": {"height_m": 4.5}},
                            {"type": "building", "source_ids": ["b"], "height": {}}, "text", {"type": "interior"}]}
    found = building_heights(features, manifest)
    assert [(p.bounds, h) for p, h in found] == [((-60.0, 6.0, 60.0, 16.0), 7.0), ((0.0, 0.0, 1.0, 1.0), 4.5), ((2.0, 0.0, 3.0, 1.0), 4.5)]
    assert isinstance(found[0][1], float)


def test_a_pole_stands_past_the_half_width_or_nearer_a_close_building() -> None:
    config = cfg()
    up = np.array([0.0, 1.0])
    assert np.allclose(pole_spot(np.zeros(2), up, 3.0, STRtree([NORTH]), config), [0, 3.5])  # 3 m and the kerb
    assert np.allclose(pole_spot(np.zeros(2), up, 3.0, STRtree([shapely_box(-5, 4.25, 5, 9)]), config), [0, 2.85])  # 1.4 m free behind
    assert pole_spot(np.zeros(2), up, 3.0, STRtree([shapely_box(-5, 2.875, 5, 9)]), config) is None  # 1.475 m from the axis
    assert np.allclose(pole_spot(np.zeros(2), up, 3.0, STRtree([shapely_box(-5, 2.9, 5, 9)]), config), [0, 1.5])  # exactly min_axis_m


# ------------------------------------------------------------------------------------------------ poles


def _poles(streets: list[Street], buildings: Any = None, **kwargs: Any) -> tuple[list[Pole], dict[str, int]]:
    options: dict[str, Any] = {"near_spawn": _never, "blocked": [], "doors": [], "config": cfg(), "ground_z": _flat}
    options.update(kwargs)
    return place_poles(streets, STRtree(buildings or [NORTH, SOUTH]), square_zone(200), 7, **options)


def test_poles_stand_every_spacing_on_one_side_by_hash() -> None:
    street = Street("osm-way-1", LineString([(-50, 0), (50, 0)]), 3.0)
    poles, skipped = _poles([street])
    side = 1.0 if pl.stable_unit(7, "osm-way-1", "side") < 0.5 else -1.0
    assert [(round(float(p.foot[0]), 3), round(float(p.foot[1]), 3), float(p.foot[2])) for p in poles] == [
        (-36.0, 3.5 * side, 0.0), (-8.0, 3.5 * side, 0.0), (20.0, 3.5 * side, 0.0), (48.0, 3.5 * side, 0.0)]
    assert [p.order for p in poles] == [0, 1, 2, 3] and {p.street for p in poles} == {"osm-way-1"} and skipped == {}
    assert np.allclose(poles[0].across, [0, 1])
    other = Street("osm-way-2", LineString([(-50, 0), (50, 0)]), 3.0)
    sides = {np.sign(_poles([s])[0][0].foot[1]) for s in (street, other, Street("osm-way-3", street.line, 3.0), Street("osm-way-9", street.line, 3.0))}
    assert sides == {1.0, -1.0}  # the side follows the street's id


def test_a_blocked_side_tries_the_other_and_names_why_neither_fits() -> None:
    street = Street("osm-way-1", LineString([(-50, 0), (50, 0)]), 3.0)
    side = 1.0 if pl.stable_unit(7, "osm-way-1", "side") < 0.5 else -1.0
    wall = shapely_box(-60, 2.0, 60, 9) if side > 0 else shapely_box(-60, -9, 60, -2.0)
    poles, _ = _poles([street], [wall])
    assert {round(float(p.foot[1]), 3) for p in poles} == {-3.5 * side}  # the hashed side is too narrow
    both = [shapely_box(-60, 2.0, 60, 9), shapely_box(-60, -9, 60, -2.0)]
    assert _poles([street], both) == ([], {"narrow": 4})
    _poles_, why = _poles([street], near_spawn=lambda x, y: x < 0)
    assert len(_poles_) == 2 and why == {"spawn": 2}
    assert _poles([street], doors=[(-36.0, 3.5 * side), (-36.0, -3.5 * side)])[1] == {"door": 1}
    kept, why = _poles([street], doors=[(-36.0, 3.5 * side + 2.4)])
    assert why == {} and round(float(kept[0].foot[1]), 3) == 3.5 * side  # just past clear_m and back_m
    moved, why = _poles([street], doors=[(-36.0, 3.5 * side + 2.39)])
    assert why == {} and round(float(moved[0].foot[1]), 3) == -3.5 * side  # too near: the pole crosses the street
    assert _poles([street], blocked=[Point(-8.0, 3.5 * side + 0.99), Point(-8.0, -3.5 * side - 0.99)])[1] == {"occupied": 1}
    assert _poles([street], blocked=[Point(-8.0, 3.5 * side + 1.0)])[1] == {}
    out = place_poles([street], STRtree([NORTH, SOUTH]), square_zone(30), 7, near_spawn=_never, blocked=[], doors=[], config=cfg(), ground_z=_flat)
    assert len(out[0]) == 2 and out[1] == {"outside_zone": 2}


def test_a_pole_keeps_off_another_and_the_count_stops_at_max_poles() -> None:
    street = Street("osm-way-1", LineString([(-50, 0), (50, 0)]), 3.0)
    cross = Street("osm-way-2", LineString([(-36, -20), (-36, 20)]), 3.0)
    poles, skipped = _poles([street, cross], buildings=[shapely_box(-60, 30, 60, 40)])
    assert skipped == {"near_pole": 1} and len(poles) == 4
    assert len(_poles([street], config=cfg(max_poles=2))[0]) == 2
    near = cfg(spacing_m=10.0)
    assert len(_poles([street], config=near)[0]) == 10  # 10 m apart: just past four tenths of the spacing
    lifted = place_poles([street], STRtree([NORTH, SOUTH]), square_zone(200), 7, near_spawn=_never, blocked=[], doors=[], config=cfg(),
                         ground_z=lambda x, y: x / 100)
    assert [round(float(p.foot[2]), 3) for p in lifted[0]] == [-0.36, -0.08, 0.2, 0.48]


# ------------------------------------------------------------------------------------------------ spans and drops


def _pole(street: str, order: int, x: float, y: float = 3.5) -> Pole:
    return Pole(street, order, np.array([x, y, 0.0]), np.array([0.0, 1.0]))


def test_spans_join_one_street_s_poles_in_a_row() -> None:
    a, b, c = _pole("s", 0, 0), _pole("s", 1, 28), _pole("s", 2, 56)
    gap = _pole("s", 4, 84)
    other = _pole("t", 5, 100)
    far = _pole("t", 6, 140.01)
    assert spans([a, b, c, gap, other, far], STRtree([shapely_box(500, 500, 501, 501)]), cfg()) == [(a, b), (b, c)]
    exact = _pole("s", 1, 40)
    assert spans([a, exact], STRtree([shapely_box(500, 500, 501, 501)]), cfg()) == [(a, exact)]  # 40 m: max_span_m
    assert spans([a, b], STRtree([shapely_box(10, 3, 12, 4)]), cfg()) == []  # it runs 2 m through a building


def test_a_segment_crosses_a_building_only_past_five_centimetres() -> None:
    tree = STRtree([shapely_box(0, 0, 10, 10)])
    assert crosses(LineString([(-1, 5), (0.06, 5)]), tree) and not crosses(LineString([(-1, 5), (0.05, 5)]), tree)
    assert crosses(LineString([(-1, 0), (11, 0)]), tree)  # along a wall it touches the whole way
    assert not crosses(LineString([(-1, -0.01), (11, -0.01)]), tree)


def test_drops_reach_the_nearest_tall_enough_facades() -> None:
    near, far, low = shapely_box(-5, 6, 5, 16), shapely_box(6, 0, 16, 10), shapely_box(-12, -2, -9, 2)
    heights = [(near, 9.0), (far, 4.0), (low, 3.3)]
    tree = STRtree([p for p, _ in heights])
    pole = _pole("s", 0, 0.0, 3.5)
    index = next(i for i in range(50) if round(pl.stable_unit(7, i, "drops") * 2) == 2)
    ends = drops(pole, index, 7, heights, tree, STRtree([p for p, _ in heights]), cfg())
    assert len(ends) == 2
    (s0, e0), (s1, e1) = ends
    assert np.allclose(e0, [0.0, 5.95, 5.0]) and np.allclose(s0, [0.0, 4.1, 7.5])  # drop_max_z_m; from the nearer crossarm end
    assert np.allclose(e1, [5.95, 3.5, 3.6]) and np.allclose(s1, [0.0, 3.5, 7.5])  # 0.4 m under a 4 m roof; 5 cm out of the wall
    none = next(i for i in range(50) if round(pl.stable_unit(7, i, "drops") * 2) == 0)
    assert drops(pole, none, 7, heights, tree, tree, cfg()) == []
    blocked = STRtree([p for p, _ in heights] + [shapely_box(-1, 4.5, 1, 5.5)])
    assert len(drops(pole, index, 7, heights, tree, blocked, cfg())) == 1  # the way to the near facade crosses a shed
    assert len(drops(pole, index, 7, heights, tree, STRtree([p for p, _ in heights]), cfg(drop_reach_m=5.0))) == 1
    short = [(near, 3.39), (far, 3.4)]
    assert [round(float(e[2]), 3) for _, e in drops(pole, index, 7, short, STRtree([p for p, _ in short]), tree, cfg())] == [3.0]


def test_a_pole_touching_a_facade_sends_it_no_drop() -> None:
    wall = shapely_box(-5, 3.5, 5, 9)
    heights = [(wall, 9.0)]
    tree = STRtree([wall])
    index = next(i for i in range(50) if round(pl.stable_unit(7, i, "drops") * 2) == 2)
    assert drops(_pole("s", 0, 0.0, 3.5), index, 7, heights, tree, tree, cfg()) == []


# ------------------------------------------------------------------------------------------------ the whole map


def _run(features: list[dict[str, Any]], manifest: dict[str, Any], budget: int = 100_000, **kwargs: Any):  # type: ignore[no-untyped-def]
    buildings = STRtree([NORTH, SOUTH])
    return place_lines(manifest, features, buildings, square_zone(200), 7, budget=budget, ground_z=_flat, near_spawn=_never, **kwargs)


def test_a_street_gets_poles_spans_and_drops_with_their_records() -> None:
    features = [_street(), _building("n", NORTH), _building("s", SOUTH)]
    result = _run(features, _manifest({"n": 9.0, "s": 9.0}), config=cfg(drops_per_pole=0))
    assert result.report("east_african", None) == {"style": "east_african", "requested": None, "poles": 4, "spans": 3, "drops": 0,
                                                   "triangles": 4 * 34 + 3 * 3 * 64, "colliders": 4, "skipped": {}}
    pole = result.nodes[0]
    assert pole.name == "overgrow/power/pole/0" and pole.primitives[0].material == LINE_MATERIAL == "overhead_line"
    assert pole.extras == {"type": "overgrowth", "origin": "generated", "attribute_origin": "generated", "kind": "power_pole",
                           "role": "pole", "solid": True}
    wire = result.nodes[2]
    assert wire.name == "overgrow/power/wire/2" and wire.extras["kind"] == "power_line" and wire.extras["role"] == "span"
    assert wire.extras["solid"] is False
    y = round(float(result.records[0]["position_m"][1]), 3)
    assert result.records[0] == {"kind": "power_pole", "origin": "generated", "source_id": "osm-way-1", "role": "pole",
                                 "position_m": [-36.0, y, 0.0], "size_m": [0.3, 0.3, 8.0], "solid": True}
    span = result.records[2]
    assert span["kind"] == "power_line" and span["role"] == "span" and span["solid"] is False and span["source_id"] == "osm-way-1"
    assert span["size_m"] == [28.0, 0.035, 0.84] and span["position_m"] == [-22.0, round(y - 0.6, 3), 6.86]  # 7.7 m less a 0.84 m sag
    collider = result.colliders[0]
    assert collider.name == "overgrow/collider/pole/0" and collider.extras == {"type": "collision", "origin": "generated", "role": "pole"}
    assert np.allclose(collider.primitives[0].mesh.vertices.min(axis=0), [-36.15, y - 0.15, 0.0])
    assert np.allclose(collider.primitives[0].mesh.vertices.max(axis=0), [-35.85, y + 0.15, 8.0])
    assert [round(f.area, 2) for f in result.footprints][:1] == [round(Point(0, 0).buffer(1.0).area, 2)]
    assert result.footprints[0].centroid.distance(Point(-36.0, y)) < 1e-9


def test_drops_follow_the_poles_and_a_pole_with_no_wire_is_left_out() -> None:
    features = [_street(), _building("n", NORTH), _building("s", SOUTH)]
    result = _run(features, _manifest({"n": 9.0, "s": 9.0}))
    assert result.spans == 3 and result.drops > 0
    roles = [n.extras["role"] for n in result.nodes if n.extras["kind"] == "power_line"]
    assert roles == ["span"] * 9 + ["drop"] * result.drops  # the drops come after every span
    drop = next(row for row in result.records if row["role"] == "drop")
    assert drop["kind"] == "power_line" and drop["source_id"] == "osm-way-1" and drop["size_m"][1] == 0.035
    lone = [_street("lone", -10, 10), _building("n", NORTH), _building("s", SOUTH)]
    alone = _run(lone, _manifest({}), config=cfg(spacing_m=14.0))  # one pole, no span, no height to drop to
    assert alone.report("arid", True)["poles"] == 0 and alone.skipped == {"unwired": 1}
    fed = _run(lone, _manifest({"n": 9.0, "s": 9.0}), config=cfg(spacing_m=14.0, drops_per_pole=6))
    assert fed.report("arid", True)["poles"] == 1 and fed.drops >= 1 and fed.spans == 0


def test_the_budget_takes_poles_and_spans_first_then_drops() -> None:
    features = [_street(), _building("n", NORTH), _building("s", SOUTH)]
    manifest = _manifest({"n": 9.0, "s": 9.0})
    full = _run(features, manifest, config=cfg(drops_per_pole=6))
    assert full.drops > 0
    poles_and_spans = 4 * 34 + 3 * 3 * 64
    tight = _run(features, manifest, budget=poles_and_spans, config=cfg(drops_per_pole=6))
    assert (tight.spans, tight.drops, tight.triangles) == (3, 0, poles_and_spans) and tight.skipped == {"drop_budget": full.drops}
    two = _run(features, manifest, budget=2 * 34 + 3 * 64, config=cfg(drops_per_pole=0))
    assert (two.report("x", None)["poles"], two.spans, two.skipped) == (2, 1, {"budget": 2})
    none = _run(features, manifest, budget=33, config=cfg(drops_per_pole=0))
    assert none.nodes == [] and none.skipped == {"budget": 4}
    no_heights = _run(features, _manifest({}))
    assert no_heights.drops == 0 and no_heights.spans == 3


# ------------------------------------------------------------------------------------------------ the map's word


def test_the_map_json_key_is_read_checked_and_passed_to_overgrow(tmp_path: Path) -> None:
    from onward_generator.maps.workspace import MapError, create_map, overhead_lines
    from onward_generator.next_step import _overhead_lines, overgrow_command
    from tests.conftest import MURCIA_CENTER

    root = tmp_path / "murcia"
    create_map(root, map_id="murcia", name="Murcia", latitude=MURCIA_CENTER[1], longitude=MURCIA_CENTER[0], radius_m=200)
    signs = root / "data" / "processed" / "scene_signs"
    assert overhead_lines(root) is None and "--overhead-lines" not in overgrow_command(signs).argv  # the style decides
    document = json.loads((root / "map.json").read_text(encoding="utf-8"))
    for value, flag in ((True, "on"), (False, "off")):
        (root / "map.json").write_text(json.dumps({**document, "overhead_lines": value}), encoding="utf-8")
        assert overhead_lines(root) is value
        argv = list(overgrow_command(signs).argv)
        assert argv.count("--overhead-lines") == 1 and argv[argv.index("--overhead-lines") + 1] == flag
    (root / "map.json").write_text(json.dumps({**document, "overhead_lines": "yes"}), encoding="utf-8")
    with pytest.raises(MapError, match=r"""^map.json "overhead_lines" must be true or false, got 'yes'$"""):
        overhead_lines(root)
    assert _overhead_lines(None) == () and _overhead_lines(tmp_path / "elsewhere") == ()


def test_the_overgrow_command_passes_the_switch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from onward_generator.overgrow import __main__ as cli

    seen: dict[str, object] = {}

    def fake(*args, **kwargs):  # type: ignore[no-untyped-def]
        seen.update(kwargs)
        raise OSError("stop here")

    monkeypatch.setattr(cli, "overgrow", fake)
    common = ["place", "--base", str(tmp_path), "--scene", str(tmp_path / "s.json"), "--out", str(tmp_path / "o")]
    for flag, value in (("on", True), ("off", False)):
        assert cli.main([*common, "--overhead-lines", flag]) == 1
        assert seen["overhead_lines"] is value
    assert cli.main(common) == 1
    assert seen["overhead_lines"] is None



# ------------------------------------------------------------------------------------------------ millimetres and edges


def test_the_records_keep_millimetres_on_an_odd_street() -> None:
    x0, y0 = -50.1234, 0.4321
    street = {**_street("odd", x0, 50.5678), "geometry": {"type": "LineString", "coordinates": [[x0, y0], [50.5678, y0]]}}
    buildings = STRtree([shapely_box(-60, 6.4321, 60, 16), shapely_box(-60, -16, 60, -5.5679)])
    result = place_lines(_manifest({}), [street], buildings, square_zone(200), 7, budget=100_000, ground_z=lambda x, y: x / 71 + 0.01234,
                         near_spawn=_never, config=cfg(drops_per_pole=0))
    side = 1.0 if pl.stable_unit(7, "odd", "side") < 0.5 else -1.0
    xs = [x0 + 14 + 28 * k for k in range(4)]
    feet = [np.array([x, y0 + 3.5 * side, x / 71 + 0.01234]) for x in xs]
    poles = [row for row in result.records if row["kind"] == "power_pole"]
    assert poles == [{"kind": "power_pole", "origin": "generated", "source_id": "odd", "role": "pole",
                      "position_m": [round(float(f[0]), 3), round(float(f[1]), 3), round(float(f[2]), 3)],
                      "size_m": [0.3, 0.3, 8.0], "solid": True} for f in feet]
    assert [n.name for n in result.nodes if n.extras["kind"] == "power_pole"] == [f"overgrow/power/pole/{k}" for k in range(4)]
    assert [n.name for n in result.colliders] == [f"overgrow/collider/pole/{k}" for k in range(4)]
    a = attach_points(feet[0], 8.0, 1.4, np.array([0.0, 1.0]))[0]
    b = attach_points(feet[1], 8.0, 1.4, np.array([0.0, 1.0]))[0]
    sag = float(np.linalg.norm(b - a)) * 0.03
    lowest = float(sag_points(a, b, sag)[:, 2].min())
    span = next(row for row in result.records if row["kind"] == "power_line")
    assert span == {"kind": "power_line", "origin": "generated", "source_id": "odd", "role": "span",
                    "position_m": [round(float(a[0] + b[0]) / 2, 3), round(float(a[1] + b[1]) / 2, 3), round(lowest, 3)],
                    "size_m": [round(float(np.linalg.norm(b - a)), 3), 0.035, round(max(float(a[2]), float(b[2])) - lowest, 3)],
                    "solid": False}
    wire = next(n for n in result.nodes if n.extras["kind"] == "power_line")
    assert wire.extras == {"type": "overgrowth", "origin": "generated", "attribute_origin": "generated", "kind": "power_line",
                           "role": "span", "solid": False}
    for node in result.nodes:
        primitive = node.primitives[0]
        assert primitive.uvs is not None and len(primitive.uvs) == primitive.mesh.vertex_count
    assert result.nodes[0].primitives[0].uvs[0].tolist() == [0.5, 0.25] and wire.primitives[0].uvs[0].tolist() == [0.5, 0.75]


def test_a_spot_s_reasons_at_their_bounds() -> None:
    config = cfg()
    zone = square_zone(200)
    spot = np.array([0.0, 0.0])
    reason = pl._spot_reason
    assert reason(spot, zone, lambda x, y: y == 0.0 and x == 0.0, [], [], [], config) == "spawn"
    assert reason(np.array([0.0, 5.0]), zone, lambda x, y: y == 0.0, [], [], [], config) is None
    assert reason(spot, zone, _never, [Point(config.clear_m, 0.0)], [], [], config) is None  # exactly clear_m away
    assert reason(spot, zone, _never, [Point(config.clear_m - 0.001, 0.0)], [], [], config) == "occupied"
    edge = config.spacing_m * pl.NEAR_POLE
    high = Pole("s", 0, np.array([edge, 0.0, 5.0]), np.array([0.0, 1.0]))  # the height plays no part
    assert reason(spot, zone, _never, [], [], [high], config) is None  # exactly four tenths of the spacing
    nearer = Pole("s", 0, np.array([edge - 0.001, 0.0, 5.0]), np.array([0.0, 1.0]))
    assert reason(spot, zone, _never, [], [], [nearer], config) == "near_pole"
    door = config.clear_m + config.back_m
    assert reason(spot, zone, _never, [], [(door, 0.0)], [], config) is None
    assert reason(spot, zone, _never, [], [(door - 0.001, 0.0)], [], config) == "door"
    assert reason(np.array([200.0, 0.0]), zone, _never, [], [], [], config) is None and reason(np.array([200.01, 0.0]), zone, _never, [], [], [], config) == "outside_zone"


def test_drops_reach_a_facade_within_a_metre_and_at_drop_reach() -> None:
    index = next(i for i in range(50) if round(pl.stable_unit(7, i, "drops") * 2) == 2)
    pole = _pole("s", 0, 0.0, 3.5)
    close = shapely_box(-2, 4.0, 2, 9)  # 0.5 m from the pole
    at_reach = shapely_box(14.0, 0, 20, 7)  # exactly drop_reach_m
    heights = [(close, 9.0), (at_reach, 9.0)]
    tree = STRtree([close, at_reach])
    ends = drops(pole, index, 7, heights, tree, tree, cfg())
    assert [round(float(end[0]), 3) for _, end in ends] == [0.0, 13.95] and np.allclose(ends[0][1][:2], [0.0, 3.95])
    past = [(shapely_box(14.01, 0, 20, 7), 9.0)]
    assert drops(pole, index, 7, past, STRtree([p for p, _ in past]), tree, cfg()) == []


def test_a_building_without_geometry_or_id_is_handled() -> None:
    features = [{"id": "x", "kind": "building"}, {"kind": "building", "geometry": {"type": "Polygon", "coordinates": [list(NORTH.exterior.coords)]}}]
    manifest = {"objects": [{"type": "building", "source_ids": ["x", ""], "height": {"height_m": 6.0}}]}
    found = building_heights(features, manifest)
    assert [(p.bounds, h) for p, h in found] == [(NORTH.bounds, 6.0)]  # the footprint without an id reads as ""



def test_drops_go_nearest_first_whatever_the_footprints_order_and_ties_by_order() -> None:
    near, far = shapely_box(-5, 6, 5, 16), shapely_box(6, 0, 16, 10)
    heights = [(far, 9.0), (near, 9.0)]  # listed far first
    tree = STRtree([p for p, _ in heights])
    pole = _pole("s", 0, 0.0, 3.5)
    one = next(i for i in range(50) if round(pl.stable_unit(7, i, "drops") * 2) == 1)
    assert [np.round(end[:2], 3).tolist() for _, end in drops(pole, one, 7, heights, tree, tree, cfg())] == [[0.0, 5.95]]
    left, right = shapely_box(-5, 6, -1, 16), shapely_box(1, 6, 5, 16)  # both corners 2.693 m away
    tie = [(right, 9.0), (left, 9.0)]
    found = drops(pole, one, 7, tie, STRtree([p for p, _ in tie]), STRtree([left, right]), cfg())
    assert [round(float(end[0]), 3) for _, end in found] == [0.981]  # the first footprint listed, 5 cm out of its corner
    wanted = [round(pl.stable_unit(7, i, "drops") * 6) for i in range(6)]
    many = [(shapely_box(-30 + 4 * k, 6, -27 + 4 * k, 9), 9.0) for k in range(15)]
    many_tree = STRtree([p for p, _ in many])
    assert [len(drops(pole, i, 7, many, many_tree, many_tree, cfg(drops_per_pole=6))) for i in range(6)] == wanted


def test_poles_follow_a_corner_and_a_diagonal_and_stop_before_the_end() -> None:
    far = [shapely_box(500, 500, 501, 501)]
    corner = Street("corner", LineString([(-43, 0), (0, 0), (0, 50)]), 3.0)
    poles, _ = _poles([corner], far)
    assert [np.round(p.foot[:2], 3).tolist() for p in poles] == [[-29.0, 3.5], [-1.0, 3.5], [-3.5, 27.0]]
    diagonal = Street("diag", LineString([(0, 0), (30, 40)]), 3.0)
    poles, _ = _poles([diagonal], far)
    assert [np.round(p.foot[:2], 3).tolist() for p in poles] == [[11.2, 9.1], [28.0, 31.5]]
    assert np.allclose(poles[0].across, [-0.8, 0.6])
    exact = Street("short42", LineString([(0, 0), (42, 0)]), 3.0)
    assert len(_poles([exact], far)[0]) == 1  # a pole would stand on the very end


def test_spans_skip_a_bad_pair_and_keep_looking() -> None:
    clear = STRtree([shapely_box(500, 500, 501, 501)])
    other, a, b = _pole("t", 0, -28), _pole("s", 0, 0), _pole("s", 1, 28)
    assert spans([other, a, b], clear, cfg()) == [(a, b)]
    c, d = _pole("s", 2, 56), _pole("s", 3, 84)
    assert spans([a, c, d], clear, cfg()) == [(c, d)]
    far, nxt = _pole("s", 1, 41), _pole("s", 2, 69)
    assert spans([a, far, nxt], clear, cfg()) == [(far, nxt)]
    assert spans([a, b, c], STRtree([shapely_box(10, 3, 12, 4)]), cfg()) == [(b, c)]


def _four(budget: int, **changes: Any):  # type: ignore[no-untyped-def]
    features = [_street(), _building("n", NORTH), _building("s", SOUTH)]
    return _run(features, _manifest({"n": 9.0, "s": 9.0}), budget=budget, config=cfg(drops_per_pole=0, **changes))


def test_the_budget_counts_a_span_only_with_the_pole_it_links() -> None:
    three = _four(4 * 34 + 2 * 192 - 34 - 192 + 1)  # 295: the third pole and its span do not fit, the fourth alone does
    assert (three.report("x", None)["poles"], three.spans, three.triangles, three.skipped) == (3, 1, 294, {"budget": 1})
    two = _four(100)
    assert (two.report("x", None)["poles"], two.spans, two.triangles, two.skipped) == (2, 0, 68, {"budget": 2})
    one = _four(34)
    assert (one.report("x", None)["poles"], one.spans, one.triangles) == (1, 0, 34)


def test_the_drops_spend_the_budget_to_the_last_triangle() -> None:
    features = [_street(), _building("n", NORTH), _building("s", SOUTH)]
    manifest = _manifest({"n": 9.0, "s": 9.0})
    full = _run(features, manifest, config=cfg(drops_per_pole=6))
    assert full.drops >= 2
    tight = _run(features, manifest, budget=full.triangles - 64, config=cfg(drops_per_pole=6))
    assert tight.drops == full.drops - 1 and tight.triangles == full.triangles - 64 and tight.skipped == {"drop_budget": 1}


def test_each_pole_takes_its_own_drops() -> None:
    streets = [_street()]
    many = [_building(f"b{k}", shapely_box(-56 + 4 * k, 6, -53 + 4 * k, 9)) for k in range(28)]
    manifest = _manifest({f"b{k}": 9.0 for k in range(28)})
    config = cfg(drops_per_pole=6)
    buildings = STRtree([NORTH, SOUTH])
    result = place_lines(manifest, streets + many, buildings, square_zone(200), 7, budget=100_000, ground_z=_flat, near_spawn=_never,
                         config=config)
    poles, _ = place_poles(streets_for(streets, config.highways), buildings, square_zone(200), 7, near_spawn=_never, blocked=[],
                           doors=[], config=config, ground_z=_flat)
    heights = building_heights(many, manifest)
    tree = STRtree([p for p, _ in heights])
    assert result.drops == sum(len(drops(pole, i, 7, heights, tree, buildings, config)) for i, pole in enumerate(poles)) > 4


def test_unwired_poles_are_counted_and_a_later_wired_one_still_stands() -> None:
    features = [_street(), _building("h", shapely_box(44, 4.5, 52, 9)), _building("n", NORTH), _building("s", SOUTH)]
    lone = _run(features, _manifest({}), config=cfg(max_span_m=10.0))
    assert lone.skipped == {"unwired": 4} and lone.nodes == []
    last = _run(features, _manifest({"h": 9.0}), config=cfg(max_span_m=10.0, drops_per_pole=6))
    assert last.report("x", None)["poles"] == 1 and last.skipped == {"unwired": 3} and last.drops >= 1
