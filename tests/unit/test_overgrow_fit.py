"""Size and footprint checks for abandoned props. Murcia centre is the origin."""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from shapely import LineString, Point, STRtree, box

from onward_generator.detail.modules import frame_matrix, yaw_matrix
from onward_generator.generation.config import Material
from onward_generator.generation.gltf import Node, Primitive, build_glb
from onward_generator.generation.mesh import Mesh
from onward_generator.generation.mesh import box as mesh_box
from onward_generator.overgrow.ambient import cross_yaw, is_solid
from onward_generator.overgrow.catalog import AssetSpec
from onward_generator.overgrow.dressing import Dressing, Spot, _covers_sign, _in_keep_out, _on_solid, _place_props, _repeats, dress
from onward_generator.overgrow.fit import (
    apply_fit_scale,
    check_scene,
    fit_scale,
    measure,
    object_reason,
    size_reason,
)
from onward_generator.overgrow.library import Prop
from onward_generator.overgrow.place import _Edge
from onward_generator.overgrow.profiles import profile_for
from onward_generator.overgrow.zone import square_zone
from tests.conftest import MURCIA_CENTER


def _mesh(length: float, width: float, height: float) -> Mesh:
    return mesh_box(-length / 2, length / 2, -width / 2, width / 2, 0.0, height)


def _prop(asset_id: str, role: str, mesh: Mesh, *, anchor: str = "ground", surfaces: tuple[str, ...] = ("curb",)) -> Prop:
    spec = AssetSpec(asset_id, "vehicles" if role == "wreck" else "facade", role, surfaces, anchor, asset_id)
    return Prop(spec, mesh, np.zeros((mesh.vertex_count, 2), dtype=np.float32), b"")


def test_a_wreck_has_to_be_car_sized() -> None:
    assert size_reason("wreck", 4.0, 1.6, 1.5) is None
    assert size_reason("wreck", 5.4, 4.0, 1.2) is None
    assert size_reason("wreck", 5.4, 4.5, 1.2) == "wrong_size"
    assert size_reason("wreck", 0.51, 0.21, 0.14) == "wrong_size"
    assert size_reason("wreck", 49.0, 36.0, 17.0) == "wrong_size"
    assert size_reason("fire_escape", 4.2, 1.4, 10.0) is None
    assert size_reason("fire_escape", 6.0, 4.0, 9.0) == "too_deep"


def test_fit_scale_gives_a_street_size_before_rejecting() -> None:
    cases = (
        (_mesh(21.18, 12.16, 7.30), "wreck", "down"),
        (_mesh(0.51, 0.21, 0.14), "wreck", "up"),
        (_mesh(14.73, 9.35, 2.71), "wreck", "down"),
        (_mesh(4.22, 1.78, 10.92), "fire_escape", "down"),
    )
    scaled = []
    for source, role, direction in cases:
        before = measure(source)
        mesh, scale = apply_fit_scale(source, role)
        scaled.append((mesh, scale, role))
        length_m, width_m, height_m = measure(mesh)
        assert size_reason(role, length_m, width_m, height_m) is None
        assert measure(source) == before
        assert scale < 1.0 if direction == "down" else scale > 1.0
    assert fit_scale("wreck", 4.2, 1.8, 1.5) == 1.0
    _length, depth, _height = measure(scaled[3][0])
    assert depth <= 1.6
    _length, width, height = measure(scaled[2][0])
    assert width <= 4.0
    assert height >= 1.0


def test_a_mass_that_seals_the_street_is_rejected() -> None:
    lane = LineString([(-40.0, 0.0), (40.0, 0.0)])
    buildings = STRtree([box(0.0, 20.0, 10.0, 40.0)])
    mass = _mesh(14.0, 12.0, 3.0)
    reason = object_reason(
        role="gate", mesh=mass, matrix=yaw_matrix(0.0, 0.0, 0.0, 0.0),
        buildings=buildings, lanes=[lane],
    )
    assert reason == "blocks_passage"


def test_a_fire_escape_has_to_touch_the_wall() -> None:
    building = box(-8.0, 6.0, 8.0, 20.0)
    buildings = STRtree([building])
    escape = _mesh(4.0, 1.2, 8.0)
    on_wall = frame_matrix((0.0, 6.0 - 0.08 - 0.6, 0.0), (-1.0, 0.0, 0.0), (0.0, -1.0, 0.0))
    assert object_reason(role="fire_escape", mesh=escape, matrix=on_wall, buildings=buildings, lanes=[]) is None
    away = frame_matrix((0.0, 2.0, 0.0), (-1.0, 0.0, 0.0), (0.0, -1.0, 0.0))
    assert object_reason(role="fire_escape", mesh=escape, matrix=away, buildings=buildings, lanes=[]) == "off_wall"


def test_placement_scales_a_wreck_into_the_street_and_keeps_a_car() -> None:
    lane = LineString([(-30.0, 0.0), (80.0, 0.0)])
    buildings = STRtree([box(0.0, 30.0, 20.0, 50.0)])
    planted = dress(
        [
            _prop("abandoned_sedan_1", "wreck", _mesh(21.18, 12.16, 7.30)),
            _prop("abandoned_auto", "wreck", _mesh(0.51, 0.21, 0.14)),
            _prop("crashed_helicopter", "wreck", _mesh(14.73, 9.35, 2.71)),
            _prop("covered_car", "wreck", _mesh(4.2, 1.8, 1.5)),
        ],
        [], [], buildings, [lane], STRtree([lane]), square_zone(80.0), profile_for(100), 48152, [],
        grass_mesh=None, grass_left=0, triangle_budget=60_000,
    )
    assert "covered_car" in planted.placed_ids
    assert "abandoned_sedan_1" in planted.placed_ids
    assert "abandoned_auto" in planted.placed_ids
    assert "crashed_helicopter" in planted.placed_ids
    sized = {row["source_id"]: row for row in planted.records}
    assert sized["abandoned_sedan_1"]["fit_scale"] < 1.0
    assert sized["abandoned_auto"]["fit_scale"] > 1.0
    assert sized["crashed_helicopter"]["fit_scale"] < 1.0
    assert sized["covered_car"]["fit_scale"] == 1.0
    for asset_id in ("abandoned_sedan_1", "abandoned_auto", "crashed_helicopter", "covered_car"):
        length_m, width_m, height_m = sized[asset_id]["size_m"]
        assert size_reason("wreck", length_m, width_m, height_m) is None


def test_every_third_street_copy_turns_across_the_lane() -> None:
    assert cross_yaw("wreck", 0, street_blocking=True, on_street=True) == 0.0
    assert cross_yaw("wreck", 2, street_blocking=True, on_street=True) == math.pi / 2
    assert cross_yaw("gate", 5, street_blocking=True, on_street=True) == math.pi / 2
    assert cross_yaw("barrier", 2, street_blocking=True, on_street=True) == math.pi / 2
    assert cross_yaw("barrel", 2, street_blocking=True, on_street=True) == 0.0
    assert cross_yaw("wreck", 2, street_blocking=False, on_street=True) == 0.0
    assert cross_yaw("wreck", 2, street_blocking=True, on_street=False) == 0.0
    lane = LineString([(-30.0, 0.0), (120.0, 0.0)])
    buildings = STRtree([box(0.0, 30.0, 20.0, 50.0)])

    def plant(blocking: bool, already: int):
        return dress(
            [_prop("covered_car", "wreck", _mesh(4.2, 1.8, 1.5))],
            [], [], buildings, [lane], STRtree([lane]), square_zone(80.0), profile_for(100), 48152, [],
            grass_mesh=None, grass_left=0, triangle_budget=60_000,
            block_streets=blocking, placed_roles={"wreck": already},
        )

    across = plant(True, 2)
    along = plant(True, 0)
    assert across.records[0]["crosswise"] is True
    assert along.records[0]["crosswise"] is False
    assert abs(across.nodes[0].matrix[0, 0]) < 1e-6
    assert abs(across.nodes[0].matrix[1, 0]) > 0.99
    assert abs(along.nodes[0].matrix[0, 0]) > 0.99
    assert MURCIA_CENTER == (-1.12863, 37.98407)


def test_fit_fails_a_scene_that_still_holds_an_oversized_wreck(tmp_path: Path) -> None:
    scene = tmp_path / "geo_scene.json"
    scene.write_text(json.dumps({
        "schema": "onward_generator.geo_scene",
        "origin": {"longitude": MURCIA_CENTER[0], "latitude": MURCIA_CENTER[1]},
        "features": [
            {"id": "way", "kind": "highway", "tags": {"highway": "residential"},
             "geometry": {"type": "LineString", "coordinates": [[-40, 0], [40, 0]]}},
            {"id": "block", "kind": "building", "tags": {"building": "yes"},
             "geometry": {"type": "Polygon", "coordinates": [[[0, 20], [10, 20], [10, 40], [0, 40], [0, 20]]]}},
        ],
    }), encoding="utf-8")
    base = tmp_path / "scene_overgrow"
    base.mkdir()
    huge = _mesh(49.0, 36.0, 17.0)
    (base / "scene_visual.glb").write_bytes(build_glb(
        [Node(
            "overgrow/wreck/0",
            (Primitive(huge, "abandoned_wreck"),),
            {"type": "overgrowth", "kind": "wreck"},
            yaw_matrix(0.0, 0.9, 0.0, 0.0),
        )],
        {"abandoned_wreck": Material((0.4, 0.2, 0.1, 1), 0.9)},
    ))
    checked, problems = check_scene(base, scene)
    assert checked == 1
    assert problems and "wrong_size" in problems[0]
    assert MURCIA_CENTER == (-1.12863, 37.98407)


def test_fit_accepts_the_procedural_car(tmp_path: Path) -> None:
    scene = tmp_path / "geo_scene.json"
    scene.write_text(json.dumps({
        "features": [
            {"kind": "highway", "tags": {"highway": "residential"},
             "geometry": {"type": "LineString", "coordinates": [[-40, 0], [40, 0]]}},
        ],
    }), encoding="utf-8")
    base = tmp_path / "scene_overgrow"
    base.mkdir()
    car = _mesh(4.0, 1.6, 1.5)
    (base / "scene_visual.glb").write_bytes(build_glb(
        [
            Node(
                "overgrow/wreck/0",
                (Primitive(car, "abandoned_wreck"),),
                {"kind": "wreck"},
                yaw_matrix(0.0, 0.8, 0.0, 0.0),
            ),
            Node("overgrow/moss", (Primitive(_mesh(2.0, 0.1, 0.8), "abandoned_moss"),), {"kind": "moss"}),
        ],
        {"abandoned_wreck": Material((0.4, 0.2, 0.1, 1), 0.9), "abandoned_moss": Material((0.2, 0.4, 0.2, 1), 0.9)},
    ))
    checked, problems = check_scene(base, scene)
    assert problems == []
    assert checked == 1



@pytest.mark.parametrize(
    ("a", "b", "outward", "wall_at"),
    [
        ((-10.0, 6.0), (10.0, 6.0), (0.0, -1.0), 6.0),  # south wall, facing the lane
        ((10.0, 6.0), (10.0, 20.0), (1.0, 0.0), 10.0),  # east wall
    ],
)
def test_a_wall_mounted_prop_hangs_on_its_wall(
    a: tuple[float, float], b: tuple[float, float], outward: tuple[float, float], wall_at: float,
) -> None:
    building = box(-10.0, 6.0, 10.0, 20.0)
    lane = LineString([(-30.0, 0.0), (30.0, 0.0)])
    start, end, out = np.array(a), np.array(b), np.array(outward)
    wall = _Edge("osm-way-1", 0, start, end, out, float(np.linalg.norm(end - start)))
    planted = dress(
        [_prop("aircon_box", "aircon", _mesh(0.8, 0.5, 0.6), anchor="facade", surfaces=("facade",))],
        [wall], [], STRtree([building]), [lane], STRtree([lane]), square_zone(80.0), profile_for(100), 48152, [],
        grass_mesh=None, grass_left=0, triangle_budget=60_000,
    )
    assert planted.placed_ids == ["aircon_box"]
    back = planted.nodes[0].matrix @ np.array([0.0, -0.25, 0.0, 1.0])
    axis = 0 if out[0] else 1
    assert round(float((back[axis] - wall_at) * out[axis]), 3) == 0.08
    mid = (start + end) / 2
    assert round(float(back[1 - axis]), 3) == round(float(mid[1 - axis]), 3)
    assert planted.records[0]["position_m"][2] == 2.4


def test_an_awning_clears_the_door_under_it() -> None:
    building = box(-10.0, 6.0, 10.0, 20.0)
    lane = LineString([(-30.0, 0.0), (30.0, 0.0)])
    wall = _Edge("osm-way-1", 0, np.array([-10.0, 6.0]), np.array([10.0, 6.0]), np.array([0.0, -1.0]), 20.0)
    planted = dress(
        [_prop("awning_small", "awning", _mesh(1.9, 0.96, 0.9), anchor="facade", surfaces=("facade",))],
        [wall], [], STRtree([building]), [lane], STRtree([lane]), square_zone(80.0), profile_for(100), 48152, [],
        grass_mesh=None, grass_left=0, triangle_budget=60_000,
    )
    assert planted.placed_ids == ["awning_small"]
    assert planted.records[0]["position_m"][2] == 2.5


def test_props_from_one_lane_station_do_not_land_on_one_point() -> None:
    # One station (12 m along a 16 m lane): curb, sidewalk and carriageway spots on each side
    # all move to the same point beside the lane.
    lane = LineString([(-8.0, 0.0), (8.0, 0.0)])
    barrels = [
        Prop(
            AssetSpec(f"barrel_{index}", "waste", "barrel", ("curb", "sidewalk", "carriageway"), "ground", "test"),
            _mesh(0.6, 0.6, 0.9), np.zeros((24, 2), dtype=np.float32), b"",
        )
        for index in range(8)
    ]
    planted = dress(
        barrels, [], [], STRtree([box(30.0, 30.0, 40.0, 40.0)]), [lane], STRtree([lane]), square_zone(80.0),
        profile_for(100), 48152, [], grass_mesh=None, grass_left=0, triangle_budget=60_000,
    )
    points = [row["position_m"][:2] for row in planted.records]
    assert len(points) >= 2
    closest = min(math.dist(a, b) for index, a in enumerate(points) for b in points[index + 1:])
    assert closest >= 2.2



def _barrel() -> Prop:
    spec = AssetSpec("barrel_0", "waste", "barrel", ("sidewalk",), "ground", "test")
    mesh = _mesh(0.6, 0.6, 0.9)
    return Prop(spec, mesh, np.zeros((mesh.vertex_count, 2), dtype=np.float32), b"")


@pytest.mark.parametrize(
    ("taken", "placed"),
    [
        ((0.0, 2.3, 2.2), True),  # 2.3 m away, behind: clear of 2.2 m
        ((0.0, -2.0, 2.0), True),  # exactly on the radius: clear
        ((0.0, -2.5, 3.0), False),
        ((0.0, -0.4, 0.5), False),
    ],
)
def test_a_prop_keeps_the_radius_of_one_already_placed(taken: tuple[float, float, float], placed: bool) -> None:
    spot = Spot("sidewalk", 0.0, 0.0, 0.0, "grid:0", "grid", 10.0)
    result = Dressing()
    _place_props(
        [_barrel()], [spot], STRtree([box(30.0, 30.0, 40.0, 40.0)]), None, [], square_zone(80.0), profile_for(100),
        48152, [], 60_000, result, used=[taken], seen=set(),
    )
    assert (result.placed_ids == ["barrel_0"]) is placed


def test_a_street_blocking_wreck_is_spaced_where_it_crosses_the_lane() -> None:
    spec = AssetSpec("covered_car", "vehicles", "wreck", ("carriageway",), "ground", "test")
    car = Prop(spec, _mesh(4.2, 1.8, 1.5), np.zeros((24, 2), dtype=np.float32), b"")
    spot = Spot("carriageway", 0.0, -1.15, 0.0, "lane:0", "lane-0", 10.0, lane_x=0.0, lane_y=0.0, normal=(0.0, -1.0))

    def plant(taken: tuple[float, float, float]) -> list[str]:
        result = Dressing()
        _place_props(
            [car], [spot], STRtree([box(30.0, 30.0, 40.0, 40.0)]), None, [], square_zone(80.0), profile_for(100),
            48152, [], 60_000, result, block_streets=True, used=[taken], seen=set(),
        )
        return result.placed_ids

    # Blocking the street, the car lands 0.9 m off the axis, not beside the lane.
    assert plant((0.0, -0.9, 2.2)) == []
    assert plant((0.0, -5.6, 2.2)) == ["covered_car"]


def test_props_keep_clear_of_wrecks_and_trees_but_may_stand_on_grass() -> None:
    lane = LineString([(-8.0, 0.0), (8.0, 0.0)])
    barrels = [
        Prop(
            AssetSpec(f"barrel_{index}", "waste", "barrel", ("curb", "sidewalk", "carriageway"), "ground", "test"),
            _mesh(0.6, 0.6, 0.9), np.zeros((24, 2), dtype=np.float32), b"",
        )
        for index in range(8)
    ]

    def plant(**kwargs: object) -> list[list[float]]:
        planted = dress(
            barrels, [], [], STRtree([box(30.0, 30.0, 40.0, 40.0)]), [lane], STRtree([lane]), square_zone(80.0),
            profile_for(100), 48152, [], grass_mesh=None, grass_left=0, triangle_budget=60_000, **kwargs,  # type: ignore[arg-type]
        )
        return [row["position_m"][:2] for row in planted.records]

    alone = plant()
    wrecks = [(x, y, 3.0) for x, y in alone[:2]]
    assert plant(occupied=[(x, y, 0.8) for x, y in alone]) == alone
    beside = plant(occupied=wrecks, taken=wrecks)
    assert beside
    assert all(math.dist(point, (x, y)) >= 3.0 for point in beside for x, y, _r in wrecks)


BLOCK = [[-10, 8], [10, 8], [10, 20], [-10, 20], [-10, 8]]


def _fit_scene(tmp_path: Path, nodes: list[Node], ring: list[list[float]] = BLOCK, urban: list[dict] | None = None) -> tuple[int, list[str]]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    scene = tmp_path / "geo_scene.json"
    scene.write_text(json.dumps({
        "features": [
            {"kind": "highway", "tags": {"highway": "residential"},
             "geometry": {"type": "LineString", "coordinates": [[-40, 0], [40, 0]]}},
            {"kind": "building", "tags": {"building": "yes"},
             "geometry": {"type": "Polygon", "coordinates": [ring]}},
        ],
    }), encoding="utf-8")
    base = tmp_path / "scene_overgrow"
    base.mkdir()
    materials = {p.material: Material((0.5, 0.5, 0.5, 1), 0.9) for node in nodes for p in node.primitives}
    (base / "scene_visual.glb").write_bytes(build_glb(nodes, materials))
    if urban is not None:
        (base / "scene_detail.json").write_text(json.dumps({"objects": urban}), encoding="utf-8")
    return check_scene(base, scene)


def _mounted(name: str, gap: float) -> Node:
    # A 0.5 m deep aircon on the south wall (y = 8), its back ``gap`` off the wall.
    matrix = frame_matrix((0.0, 8.0 - gap - 0.25, 2.4), (-1.0, 0.0, 0.0), (0.0, -1.0, 0.0))
    return Node(f"overgrow/prop/{name}/0", (Primitive(_mesh(0.8, 0.5, 0.6), "prop_aircon"),),
                {"role": "aircon", "kind": "facade"}, matrix)


@pytest.mark.parametrize(("gap", "problem"), [(0.08, False), (0.14, False), (0.16, True), (0.43, True)])
def test_fit_fails_a_wall_prop_off_its_wall(tmp_path: Path, gap: float, problem: bool) -> None:
    checked, problems = _fit_scene(tmp_path, [_mounted("aircon", gap)])
    assert checked == 1
    assert [p.split()[1] for p in problems] == (["off_wall"] if problem else [])


def test_fit_fails_a_wall_prop_with_no_wall_near(tmp_path: Path) -> None:
    lone = Node("overgrow/prop/aircon/0", (Primitive(_mesh(0.8, 0.5, 0.6), "prop_aircon"),),
                {"role": "aircon", "kind": "facade"}, yaw_matrix(30.0, -20.0, 2.4, 0.0))
    _checked, problems = _fit_scene(tmp_path, [lone])
    assert [p.split()[1] for p in problems] == ["off_wall"]


def _ground(name: str, role: str, kind: str, x: float) -> Node:
    return Node(f"overgrow/prop/{name}/0", (Primitive(_mesh(1.0, 1.0, 1.0), f"prop_{name}"),),
                {"role": role, "kind": kind, "surface": "sidewalk"}, yaw_matrix(x, -6.0, 0.0, 0.0))


@pytest.mark.parametrize(
    ("second", "x", "problem"),
    [
        (("barrel", "waste"), 0.7, True),  # 0.3 m2 shared
        (("barrel", "waste"), 0.75, False),  # exactly 0.25 m2: allowed
        (("barrel", "waste"), 0.76, False),  # 0.24 m2
        (("shed", "structures"), 0.7, True),  # solid by its category, not its role
        (("plant", "vegetation"), 0.2, False),  # a plant does not stop anyone
        (("barrel", "waste"), 3.0, False),
    ],
)
def test_fit_fails_two_solid_props_inside_each_other(tmp_path: Path, second: tuple[str, str], x: float, problem: bool) -> None:
    nodes = [_ground("barrel_a", "barrel", "waste", 0.0), _ground("other", *second, x)]
    _checked, problems = _fit_scene(tmp_path, nodes)
    expected = ["overgrow/prop/barrel_a/0 overlaps overgrow/prop/other/0 0.30m2"] if problem else []
    assert problems == expected


def test_fit_counts_a_wreck_as_solid(tmp_path: Path) -> None:
    wreck = Node("overgrow/wreck/0", (Primitive(_mesh(4.0, 1.6, 1.5), "abandoned_wreck"),), {"kind": "wreck"},
                 yaw_matrix(0.0, -6.0, 0.0, 0.0))
    _checked, problems = _fit_scene(tmp_path, [wreck, _ground("barrel_a", "barrel", "waste", 1.0)])
    assert problems == ["overgrow/wreck/0 overlaps overgrow/prop/barrel_a/0 1.00m2"]



def test_fit_allows_a_wall_prop_exactly_at_the_gap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 0.125 m is exact in binary, so the boundary is tested without rounding.
    monkeypatch.setattr("onward_generator.overgrow.fit.FACADE_GAP_M", 0.125)
    _checked, problems = _fit_scene(tmp_path, [_mounted("aircon", 0.125)])
    assert problems == []



@pytest.mark.parametrize(
    ("size", "scale"),
    [((14.16, 13.41, 1.74), 2.5 / 14.16), ((1.95, 1.71, 0.41), 1.0), ((2.5, 1.0, 0.3), 1.0), ((1.0, 5.0, 0.3), 0.5)],
)
def test_a_plant_is_brought_down_to_2_5_m_across(size: tuple[float, float, float], scale: float) -> None:
    assert fit_scale("plant", *size) == pytest.approx(scale)
    mesh, applied = apply_fit_scale(_mesh(*size), "plant")
    assert applied == pytest.approx(scale)
    assert size_reason("plant", *measure(mesh)) is None
    assert size_reason("plant", *size) == (None if scale == 1.0 else "wrong_size")


def _road() -> Node:
    # The carriageway cap, 0.15 m down, from x 21 to 40 and y 3.2 to 6: off the origin, so only the true centre lands on it.
    cap = Mesh(np.array([[21, 3.2, -0.15], [40, 3.2, -0.15], [40, 6, -0.15], [21, 6, -0.15]], dtype=np.float64),
               np.array([[0, 1, 2], [0, 2, 3]]))
    return Node("surface/carriageway", (Primitive(cap, "asphalt"),), {"type": "surface"})


@pytest.mark.parametrize(
    ("x", "y", "z", "problem"),
    [
        (0.0, -6.0, 0.0, None),  # on the sidewalk
        (0.0, -6.0, 0.05, None),
        (0.0, -6.0, -0.05, None),
        (0.0, -6.0, 0.25, "floating_0.25m"),
        (0.0, -6.0, -0.25, "sunk_0.25m"),
        (30.0, 4.5, -0.15, None),  # on the carriageway
        (30.0, 4.5, 0.0, "floating_0.15m"),
    ],
)
def test_fit_fails_a_prop_that_misses_the_ground(tmp_path: Path, x: float, y: float, z: float, problem: str | None) -> None:
    barrel = Node("overgrow/prop/barrel_a/0", (Primitive(_mesh(1.0, 1.0, 1.0), "prop_barrel"),),
                  {"role": "barrel", "kind": "waste"}, yaw_matrix(x, y, z, 0.0))
    _checked, problems = _fit_scene(tmp_path, [_road(), barrel])
    assert [p.split()[1] for p in problems] == ([problem] if problem else [])


def test_a_wall_prop_is_not_held_to_the_ground(tmp_path: Path) -> None:
    _checked, problems = _fit_scene(tmp_path, [_mounted("aircon", 0.08)])
    assert problems == []


@pytest.mark.parametrize(("y", "problem"), [(8.0 - 0.075, False), (8.0 + 0.075, True), (14.0, True)])
def test_fit_fails_a_street_plate_inside_its_building(tmp_path: Path, y: float, problem: bool) -> None:
    # A 1.4 x 0.03 x 0.36 m plate on the south wall (y = 8): 7.5 cm out, or as far inside.
    plate = Node("street_sign/street_sign_atlas_0", (Primitive(mesh_box(-0.7, 0.7, y - 0.015, y + 0.015, 2.37, 2.73), "street_sign_atlas_0"),),
                 {"name": "Calle Mayor", "name_origin": "osm", "wall": "osm-way-1"})
    checked, problems = _fit_scene(tmp_path, [plate])
    assert checked == 1
    assert problems == (["street_sign/street_sign_atlas_0 inside_building"] if problem else [])




def _plate(x: float, y: float) -> Node:
    """A plate modelled at the origin and moved by its node matrix, as a GLB may store it."""
    mesh = mesh_box(-0.7, 0.7, -0.015, 0.015, 2.37, 2.73)
    return Node("street_sign/street_sign_atlas_0", (Primitive(mesh, "street_sign_atlas_0"),), {"name": "Calle Mayor"},
                yaw_matrix(x, y, 0.0, 0.0))


def test_fit_reads_a_plate_where_its_matrix_puts_it(tmp_path: Path) -> None:
    # The second is 1 m inside the east wall (x = 10), the third 2 m outside it.
    checked, problems = _fit_scene(tmp_path, [_plate(0.0, 7.925), _plate(9.0, 14.0), _plate(12.0, 14.0)])
    assert checked == 3
    assert problems == ["street_sign/street_sign_atlas_0 inside_building"]


def test_fit_keeps_a_plate_in_the_notch_of_an_l_shaped_building(tmp_path: Path) -> None:
    # The notch x 0..10, y 8..14 is inside the building's box but outside its outline.
    ell = [[-10, 8], [0, 8], [0, 14], [10, 14], [10, 20], [-10, 20], [-10, 8]]
    _checked, problems = _fit_scene(tmp_path, [_plate(5.0, 10.0)], ell)
    assert problems == []


def test_the_fit_command_names_the_glb_it_checked(tmp_path: Path) -> None:
    from onward_generator.cli import main
    from onward_generator.dress.geometry import file_sha256

    _fit_scene(tmp_path, [_plate(0.0, 7.925)])
    base = tmp_path / "scene_overgrow"
    out = base / "fit.json"
    assert main(["overgrow", "fit", "--base", str(base), "--scene", str(tmp_path / "geo_scene.json"), "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8")) == {
        "schema": "onward_generator.overgrow_fit", "checked": 1, "rejected": [],
        "visual_sha256": file_sha256(base / "scene_visual.glb"),
    }


@pytest.mark.parametrize(
    ("role", "category", "size", "solid"),
    [
        ("trash", "waste", (1.92, 1.45, 1.74), True),  # a dumpster
        ("debris", "waste", (1.3, 1.3, 1.3), True),  # a concrete block
        ("bench", "furniture", (1.93, 1.03, 1.31), True),
        ("debris", "waste", (1.3, 0.4, 0.5), True),  # exactly 0.4 m deep and 0.5 m tall
        ("debris", "waste", (1.3, 0.39, 0.5), False),
        ("debris", "waste", (1.3, 0.4, 0.49), False),  # a pallet is too low
        ("debris", "waste", (0.4, 1.3, 0.5), True),  # the short side may be the length
        ("lamp", "furniture", (1.87, 0.5, 3.33), False),  # a box would be a wall under the arm
        ("awning", "facade", (3.2, 0.96, 0.9), False),
        ("plant", "vegetation", (2.5, 2.4, 1.0), False),
        ("bench", "furniture", None, False),  # without a size, only the role and category count
        ("wreck", "vehicles", None, True),
        ("debris", "vehicles", (0.59, 0.16, 0.59), True),  # a tyre: solid by its category alone
        ("path", "structures", (2.8, 2.52, 0.07), True),
        ("hedge", "vegetation", (2.0, 1.0, 1.5), False),  # cover-sized, but vegetation
    ],
)
def test_a_prop_that_looks_like_cover_is_solid(role: str, category: str, size: tuple[float, float, float] | None, solid: bool) -> None:
    assert is_solid(role, category, size) is solid


def test_a_solid_prop_may_not_stand_inside_another() -> None:
    spot = Spot("sidewalk", 0.0, 0.0, 0.0, "grid:0", "grid", 10.0)
    block = _barrel()  # 0.6 x 0.6 x 0.9, a solid barrel
    result = Dressing()
    assert _on_solid(block, spot, False, result) is False
    result.solid_prints.append(box(0.1, -1.0, 2.0, 1.0))  # shares 0.2 x 0.6 = 0.12 m2
    assert _on_solid(block, spot, False, result) is False
    result.solid_prints.append(box(-0.3, -0.3, 0.1, 0.3))  # shares 0.4 x 0.6 = 0.24 m2
    assert _on_solid(block, spot, False, result) is False
    result.solid_prints.append(box(-0.3, -0.3, 0.3, 0.3))  # the whole 0.36 m2
    assert _on_solid(block, spot, False, result) is True
    can = Prop(AssetSpec("can_0", "waste", "can", ("sidewalk",), "ground", "test"), _mesh(0.12, 0.12, 0.15),
               np.zeros((24, 2), dtype=np.float32), b"")
    assert _on_solid(can, spot, False, result) is False


def test_a_solid_prop_is_measured_turned() -> None:
    # A 3 x 0.4 m fence turned a quarter lies along y and misses a box east of the spot.
    fence = Prop(AssetSpec("fence_0", "structures", "fence", ("sidewalk",), "ground", "test"), _mesh(3.0, 0.4, 1.0),
                 np.zeros((24, 2), dtype=np.float32), b"")
    spot = Spot("sidewalk", 0.0, 0.0, 0.0, "grid:0", "grid", 10.0)
    result = Dressing(solid_prints=[box(0.5, -0.5, 1.5, 0.5)])
    assert _on_solid(fence, spot, False, result) is True
    assert _on_solid(fence, spot, False, result, math.pi / 2) is False


def test_a_bench_gets_a_collision_box() -> None:
    bench = Prop(AssetSpec("bench_0", "furniture", "bench", ("sidewalk",), "ground", "test"), _mesh(1.93, 1.03, 1.31),
                 np.zeros((24, 2), dtype=np.float32), b"")
    result = Dressing()
    _place_props([bench], [Spot("sidewalk", 0.0, 0.0, 0.0, "grid:0", "grid", 10.0)], STRtree([box(30.0, 30.0, 40.0, 40.0)]),
                 None, [], square_zone(80.0), profile_for(100), 48152, [], 60_000, result, used=[], seen=set())
    assert result.placed_ids == ["bench_0"]
    assert result.records[0]["solid"] is True
    assert [node.name for node in result.colliders] == ["overgrow/collider/prop/bench_0/0"]
    assert len(result.solid_prints) == 1



def _solid_prop(asset_id: str, role: str, category: str, size: tuple[float, float, float]) -> Prop:
    return Prop(AssetSpec(asset_id, category, role, ("sidewalk",), "ground", "test"), _mesh(*size), np.zeros((24, 2), dtype=np.float32), b"")


@pytest.mark.parametrize(
    "prop",
    [
        _solid_prop("barrier_0", "barrier", "waste", (1.0, 1.0, 0.3)),  # solid by its role only: too low for cover
        _solid_prop("path_0", "path", "structures", (1.0, 1.0, 0.07)),  # by its category only
        _solid_prop("bench_0", "bench", "furniture", (1.0, 1.0, 1.0)),  # by its size only
    ],
)
def test_every_kind_of_solid_prop_keeps_off_a_solid_footprint(prop: Prop) -> None:
    spot = Spot("sidewalk", 0.0, 0.0, 0.0, "grid:0", "grid", 10.0)
    assert _on_solid(prop, spot, False, Dressing(solid_prints=[box(-0.5, -0.5, 0.5, 0.5)])) is True


def test_a_shared_area_of_exactly_0_25_m2_is_allowed() -> None:
    spot = Spot("sidewalk", 0.0, 0.0, 0.0, "grid:0", "grid", 10.0)
    barrel = _solid_prop("barrel_0", "barrel", "waste", (0.5, 0.5, 0.9))  # 0.25 m2
    assert _on_solid(barrel, spot, False, Dressing(solid_prints=[box(-0.25, -0.25, 0.25, 0.25)])) is False
    assert _on_solid(barrel, spot, False, Dressing(solid_prints=[box(-0.25, -0.25, 0.5, 0.5)])) is False
    big = _solid_prop("barrel_1", "barrel", "waste", (0.6, 0.6, 0.9))
    assert _on_solid(big, spot, False, Dressing(solid_prints=[box(-0.3, -0.3, 0.3, 0.3)])) is True


def test_a_street_blocking_wreck_is_footprinted_where_it_crosses_the_lane() -> None:
    car = _solid_prop("car_0", "wreck", "vehicles", (4.2, 1.8, 1.5))
    spot = Spot("carriageway", 0.0, -1.15, 0.0, "lane:0", "lane-0", 10.0, lane_x=0.0, lane_y=0.0, normal=(0.0, -1.0))
    # Blocking the street, the car stands 0.9 m off the axis; otherwise beside the lane, about 5.6 m off.
    on_axis = Dressing(solid_prints=[box(-2.0, -1.4, 2.0, -0.4)])
    assert _on_solid(car, spot, True, on_axis) is True
    assert _on_solid(car, spot, False, on_axis) is False


def test_a_solid_prop_over_a_solid_footprint_is_rejected_and_reported() -> None:
    bench = _solid_prop("bench_0", "bench", "furniture", (1.0, 1.0, 1.0))
    result = Dressing(solid_prints=[box(0.5, 1.5, 1.5, 2.5)])
    _place_props([bench], [Spot("sidewalk", 1.0, 2.0, 0.0, "grid:0", "grid", 10.0)], STRtree([box(30.0, 30.0, 40.0, 40.0)]),
                 None, [], square_zone(80.0), profile_for(100), 48152, [], 60_000, result, used=[], seen=set())
    assert result.placed_ids == []
    assert result.rejects == [{"asset_id": "bench_0", "reason": "overlaps_prop", "kind": "furniture", "position_m": [1.0, 2.0, 0.0]}]


def test_a_turned_street_barrier_is_footprinted_turned_and_across_the_lane() -> None:
    barrier = Prop(AssetSpec("barrier_0", "barriers", "barrier", ("carriageway",), "ground", "test"), _mesh(3.0, 0.4, 1.0),
                   np.zeros((24, 2), dtype=np.float32), b"")
    spot = Spot("carriageway", 0.0, -1.15, 0.0, "lane:0", "lane-0", 10.0, lane_x=0.0, lane_y=0.0, normal=(0.0, -1.0))
    # The third barrier on a blocked street turns a quarter and stands 0.9 m off the axis: x -0.2..0.2, y -2.4..0.6.
    # Unturned it would cross the first box; beside the lane, as on an open street, it would cross the second.
    result = Dressing(solid_prints=[box(0.5, -1.4, 1.5, -0.4), box(-0.5, -6.0, 0.5, -4.0)])
    _place_props([barrier], [spot], STRtree([box(30.0, 30.0, 40.0, 40.0)]), None, [], square_zone(80.0), profile_for(100),
                 48152, [], 60_000, result, block_streets=True, placed_roles=Counter({"barrier": 2}), used=[], seen=set())
    assert result.placed_ids == ["barrier_0"]
    assert result.records[0]["crosswise"] is True



def test_a_prop_too_thin_for_cover_gets_no_collider() -> None:
    board = _solid_prop("board_0", "debris", "waste", (1.0, 0.3, 1.0))  # 0.3 m deep: not cover
    result = Dressing()
    _place_props([board], [Spot("sidewalk", 0.0, 0.0, 0.0, "grid:0", "grid", 10.0)], STRtree([box(30.0, 30.0, 40.0, 40.0)]),
                 None, [], square_zone(80.0), profile_for(100), 48152, [], 60_000, result, used=[], seen=set())
    assert result.placed_ids == ["board_0"]
    assert result.records[0]["solid"] is False and result.colliders == [] and result.solid_prints == []


@pytest.mark.parametrize(
    ("role", "kind", "size"),
    [("trash", "waste", (1.0, 1.0, 1.5)), ("barrier", "barriers", (1.0, 1.0, 0.3))],  # solid by size; by role alone
)
def test_fit_counts_every_kind_of_solid_prop_in_a_pile(tmp_path: Path, role: str, kind: str, size: tuple[float, float, float]) -> None:
    nodes = [
        Node(f"overgrow/prop/{role}_{n}/0", (Primitive(_mesh(*size), f"prop_{role}_{n}"),), {"role": role, "kind": kind},
             yaw_matrix(0.0, -6.0, 0.0, 0.0))
        for n in (0, 1)
    ]
    _checked, problems = _fit_scene(tmp_path, nodes)
    assert problems == [f"overgrow/prop/{role}_0/0 overlaps overgrow/prop/{role}_1/0 1.00m2"]


def _spots(*keys_and_x: tuple[str, float]) -> list[Spot]:
    return [Spot("sidewalk", x, 0.0, 0.0, key, "grid", 10.0) for key, x in keys_and_x]


def test_a_taken_spot_does_not_end_the_search() -> None:
    bench = _solid_prop("bench_0", "bench", "furniture", (1.0, 1.0, 1.0))
    taken = box(-0.5, -0.5, 0.5, 0.5)
    spots = _spots(*[(f"grid:{n}", 0.0) for n in range(6)], ("grid:far", 20.0))
    result = Dressing(solid_prints=[taken])
    _place_props([bench], spots, STRtree([box(30.0, 30.0, 40.0, 40.0)]), None, [], square_zone(80.0), profile_for(100),
                 48152, [], 60_000, result, used=[], seen=set())
    assert [row["position_m"][:2] for row in result.records][:1] == [[20.0, 0.0]]


def _repeat(prop: Prop, spots: list[Spot], result: Dressing, *, block_streets: bool = False, already: int = 0) -> None:
    result.placed_ids.append(prop.spec.id)
    _repeats([prop], spots, STRtree([box(30.0, 30.0, 40.0, 40.0)]), None, [], square_zone(80.0), profile_for(100), 48152, [],
             60_000, result, [], Counter(), set(), block_streets, Counter({prop.spec.role: already}), None)


def test_a_repeat_skips_a_taken_spot_and_goes_on() -> None:
    bench = _solid_prop("bench_0", "bench", "furniture", (1.0, 1.0, 1.0))
    result = Dressing(solid_prints=[box(-0.5, -0.5, 0.5, 0.5)])
    _repeat(bench, _spots(*[(f"grid:{n}", 0.0) for n in range(6)], ("grid:far", 20.0)), result)
    assert [row["position_m"][:2] for row in result.records] == [[20.0, 0.0]]


def test_a_repeated_street_barrier_is_footprinted_turned_and_across_the_lane() -> None:
    barrier = Prop(AssetSpec("barrier_0", "barriers", "barrier", ("carriageway",), "ground", "test"), _mesh(3.0, 0.4, 1.0),
                   np.zeros((24, 2), dtype=np.float32), b"")
    spot = Spot("carriageway", 0.0, -1.15, 0.0, "lane:0", "lane-0", 10.0, lane_x=0.0, lane_y=0.0, normal=(0.0, -1.0))
    result = Dressing(solid_prints=[box(0.5, -1.4, 1.5, -0.4), box(-0.5, -6.0, 0.5, -4.0)])
    _repeat(barrier, [spot], result, block_streets=True, already=2)
    assert len(result.records) == 1 and result.records[0]["crosswise"] is True



@pytest.mark.parametrize(
    "prop",
    [
        _solid_prop("barrier_0", "barrier", "waste", (1.0, 1.0, 0.3)),  # solid by its role only
        _solid_prop("path_0", "path", "structures", (1.0, 1.0, 0.07)),  # by its category only
        _solid_prop("bench_0", "bench", "furniture", (1.0, 1.0, 1.0)),  # by its size only
    ],
)
def test_an_ai_plan_gives_every_kind_of_solid_prop_a_collider(prop: Prop) -> None:
    from onward_generator.overgrow.realize import _emit as emit_planned

    result = Dressing()
    emit_planned(prop, yaw_matrix(0.0, -6.0, 0.0, 0.0), result, op="place", anchor_id="poi:0", crosswise=False, op_index=0)
    assert result.records[0]["solid"] is True
    assert len(result.colliders) == 1


def test_a_prop_footprint_keeps_out_of_an_urban_element() -> None:
    spot = Spot("sidewalk", 0.0, 0.0, 0.0, "grid:0", "grid", 10.0)
    dumpster = _solid_prop("dumpster_0", "trash", "waste", (2.4, 2.2, 1.7))  # x -1.2..1.2
    near = Dressing(keep_out=[Point(1.6, 0.0).buffer(0.5)])  # its edge reaches x 1.1
    far = Dressing(keep_out=[Point(1.8, 0.0).buffer(0.5)])
    assert _in_keep_out(dumpster, spot, False, near) is True
    assert _in_keep_out(dumpster, spot, False, far) is False
    assert _in_keep_out(dumpster, spot, False, Dressing()) is False
    aircon = Prop(AssetSpec("aircon_0", "facade", "aircon", ("facade",), "facade", "test"), _mesh(0.8, 0.5, 0.6),
                  np.zeros((24, 2), dtype=np.float32), b"")
    assert _in_keep_out(aircon, spot, False, Dressing(keep_out=[Point(0.0, 0.0).buffer(1.0)])) is False


def test_a_turned_prop_is_kept_out_where_it_lies() -> None:
    fence = _solid_prop("fence_0", "fence", "structures", (3.0, 0.4, 1.0))
    spot = Spot("sidewalk", 0.0, 0.0, 0.0, "grid:0", "grid", 10.0)
    east = Dressing(keep_out=[Point(1.3, 0.0).buffer(0.2)])
    assert _in_keep_out(fence, spot, False, east) is True
    assert _in_keep_out(fence, spot, False, east, math.pi / 2) is False


def test_dress_keeps_every_prop_off_the_urban_elements() -> None:
    lane = LineString([(-8.0, 0.0), (8.0, 0.0)])
    barrels = [_solid_prop(f"barrel_{n}", "barrel", "waste", (0.6, 0.6, 0.9)) for n in range(8)]

    def plant(**kwargs: object) -> list[list[float]]:
        planted = dress(
            barrels, [], [], STRtree([box(30.0, 30.0, 40.0, 40.0)]), [lane], STRtree([lane]), square_zone(80.0),
            profile_for(100), 48152, [], grass_mesh=None, grass_left=0, triangle_budget=60_000, **kwargs,  # type: ignore[arg-type]
        )
        return [row["position_m"][:2] for row in planted.records]

    alone = plant()
    benches = [(x, y, 1.0) for x, y in alone[:2]]
    kept = plant(keep_out=benches)
    assert kept
    assert all(math.dist(point, (x, y)) > 1.0 + 0.3 for point in kept for x, y, _r in benches)


def test_a_prop_on_an_urban_element_is_rejected_and_reported() -> None:
    bench = _solid_prop("bench_0", "bench", "furniture", (1.0, 1.0, 1.0))
    result = Dressing(keep_out=[Point(1.0, 2.0).buffer(0.4)])
    _place_props([bench], [Spot("sidewalk", 1.0, 2.0, 0.0, "grid:0", "grid", 10.0)], STRtree([box(30.0, 30.0, 40.0, 40.0)]),
                 None, [], square_zone(80.0), profile_for(100), 48152, [], 60_000, result, used=[], seen=set())
    assert result.placed_ids == []
    assert result.rejects == [{"asset_id": "bench_0", "reason": "on_urban_element", "kind": "furniture", "position_m": [1.0, 2.0, 0.0]}]


def test_a_spot_on_an_urban_element_does_not_end_the_search() -> None:
    bench = _solid_prop("bench_0", "bench", "furniture", (1.0, 1.0, 1.0))
    spots = _spots(*[(f"grid:{n}", 0.0) for n in range(6)], ("grid:far", 20.0))
    result = Dressing(keep_out=[Point(0.0, 0.0).buffer(0.4)])
    _place_props([bench], spots, STRtree([box(30.0, 30.0, 40.0, 40.0)]), None, [], square_zone(80.0), profile_for(100),
                 48152, [], 60_000, result, used=[], seen=set())
    assert [row["position_m"][:2] for row in result.records][:1] == [[20.0, 0.0]]


def _street_barrier() -> tuple[Prop, Spot, Dressing]:
    barrier = Prop(AssetSpec("barrier_0", "barriers", "barrier", ("carriageway",), "ground", "test"), _mesh(3.0, 0.4, 1.0),
                   np.zeros((24, 2), dtype=np.float32), b"")
    spot = Spot("carriageway", 0.0, -1.15, 0.0, "lane:0", "lane-0", 10.0, lane_x=0.0, lane_y=0.0, normal=(0.0, -1.0))
    # Turned and across the lane the barrier covers x -0.2..0.2, y -2.4..0.6: unturned it would reach the first
    # circle, beside the lane (an open street) the second.
    return barrier, spot, Dressing(keep_out=[Point(1.0, -0.9).buffer(0.3), Point(0.0, -5.0).buffer(0.5)])


def test_a_turned_street_barrier_keeps_out_where_it_crosses_the_lane() -> None:
    barrier, spot, result = _street_barrier()
    _place_props([barrier], [spot], STRtree([box(30.0, 30.0, 40.0, 40.0)]), None, [], square_zone(80.0), profile_for(100),
                 48152, [], 60_000, result, block_streets=True, placed_roles=Counter({"barrier": 2}), used=[], seen=set())
    assert result.placed_ids == ["barrier_0"]


def test_a_repeated_street_barrier_keeps_out_where_it_crosses_the_lane() -> None:
    barrier, spot, result = _street_barrier()
    _repeat(barrier, [spot], result, block_streets=True, already=2)
    assert len(result.records) == 1


def test_a_repeat_skips_an_urban_element_and_goes_on() -> None:
    bench = _solid_prop("bench_0", "bench", "furniture", (1.0, 1.0, 1.0))
    result = Dressing(keep_out=[Point(0.0, 0.0).buffer(0.4)])
    _repeat(bench, _spots(*[(f"grid:{n}", 0.0) for n in range(6)], ("grid:far", 20.0)), result)
    assert [row["position_m"][:2] for row in result.records] == [[20.0, 0.0]]



def _bench(x: float, y: float) -> dict:
    return {"id": "urban/bench/osm-node-1", "type": "urban_element", "kind": "bench", "position_m": [x, y]}


@pytest.mark.parametrize(
    ("node", "problem"),
    [
        (_ground("barrel_a", "barrel", "waste", 0.0), True),  # on the bench at (1.5, -6)
        (Node("overgrow/prop/cover/0", (Primitive(_mesh(1.0, 1.0, 0.07), "prop_cover"),), {"role": "manhole", "kind": "barriers"},
              yaw_matrix(1.5, -6.0, 0.0, 0.0)), False),  # flat: lies under it
        (_ground("barrel_b", "barrel", "waste", 5.0), False),
    ],
)
def test_fit_fails_a_ground_prop_on_an_urban_element(tmp_path: Path, node: Node, problem: bool) -> None:
    _checked, problems = _fit_scene(tmp_path, [node], urban=[_bench(1.5, -6.0)])
    assert [p.split()[1] for p in problems] == (["on_urban_element"] if problem else [])


def test_fit_holds_no_wall_prop_or_scene_without_a_manifest_to_the_urban_elements(tmp_path: Path) -> None:
    _checked, problems = _fit_scene(tmp_path / "wall", [_mounted("aircon", 0.08)], urban=[_bench(0.0, 7.5)])
    assert problems == []
    _checked, problems = _fit_scene(tmp_path / "bare", [_ground("barrel_a", "barrel", "waste", 0.0)])
    assert problems == []


def test_a_flat_prop_may_lie_under_an_urban_element() -> None:
    spot = Spot("sidewalk", 0.0, 0.0, 0.0, "grid:0", "grid", 10.0)
    cover = _solid_prop("cover_0", "manhole", "barriers", (0.7, 0.7, 0.09))
    raised = _solid_prop("cover_1", "manhole", "barriers", (0.7, 0.7, 0.1))
    result = Dressing(keep_out=[Point(0.0, 0.0).buffer(1.0)])
    assert _in_keep_out(cover, spot, False, result) is False
    assert _in_keep_out(raised, spot, False, result) is True



def test_a_prop_exactly_at_the_flat_height_counts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 0.125 m is exact in float32, as the GLB stores it, so the boundary is tested without rounding.
    monkeypatch.setattr("onward_generator.overgrow.fit.FLAT_PROP_M", 0.125)
    kerb = Node("overgrow/prop/kerb/0", (Primitive(_mesh(1.0, 1.0, 0.125), "prop_kerb"),), {"role": "barrier", "kind": "barriers"},
                yaw_matrix(1.5, -6.0, 0.0, 0.0))
    _checked, problems = _fit_scene(tmp_path, [kerb], urban=[_bench(1.5, -6.0)])
    assert [p.split()[1] for p in problems] == ["on_urban_element"]


def _fascia(x: float, y: float, z: float, *, kind: str = "fascia", normal: tuple[float, float] = (0.0, -1.0)) -> dict:
    return {"kind": kind, "centre_m": [x, y, z], "normal": list(normal), "width_m": 2.0, "height_m": 0.4, "depth_m": 0.1}


def _shopfront(*signs: dict) -> dict:
    return {"objects": [
        "not a record",
        {"id": "urban/bench/x", "type": "urban_element"},
        {"id": "building/b", "type": "building", "facade": {"shops": [{"name": "no sign"}, "bad", *({"sign": s} for s in signs)]}},
    ]}


def test_shop_signs_are_a_footprint_and_a_height_band() -> None:
    from onward_generator.overgrow.urban import SIGN_CLEAR_M, shop_signs

    fascia, blade = shop_signs(_shopfront(_fascia(0.0, 8.0, 2.9), _fascia(5.0, 8.0, 3.0, kind="blade")))
    # A fascia runs along the wall (x), as deep as itself plus the clearance on each side.
    assert [round(v, 3) for v in fascia[0].bounds] == [round(v, 3) for v in (-1.0, 8.0 - 0.05 - SIGN_CLEAR_M, 1.0, 8.0 + 0.05 + SIGN_CLEAR_M)]
    assert (round(fascia[1], 3), round(fascia[2], 3)) == (2.7, 3.1)
    # A blade stands out of the wall, along its normal (y).
    assert [round(v, 3) for v in blade[0].bounds] == [5.0 - 0.1, 7.0, 5.0 + 0.1, 9.0]
    # Each is the full rectangle: 2 m long, 0.1 m deep plus the clearance either side.
    assert round(fascia[0].area, 6) == round(blade[0].area, 6) == round(2.0 * (0.1 + 2 * SIGN_CLEAR_M), 6)
    ((wide, _low, _high),) = shop_signs(_shopfront({**_fascia(0.0, 8.0, 2.9), "width_m": 3.0}))
    assert [round(v, 3) for v in wide.bounds][::2] == [-1.5, 1.5]


def _awning() -> Prop:
    return Prop(AssetSpec("awning_0", "facade", "awning", ("facade",), "facade", "test"), _mesh(1.9, 0.96, 0.9),
                np.zeros((24, 2), dtype=np.float32), b"")


def _wall_spot(x: float = 0.0) -> Spot:
    # A facade spot 0.35 m out from a south wall at y = 8; the awning hangs from 2.5 to 3.4 m.
    return Spot("facade", x, 8.0 - 0.35, 0.0, f"facade:b:{x}", "b", 10.0, outward=(0.0, -1.0), along=(1.0, 0.0),
                edge_length=20.0, wall_height=12.0)


@pytest.mark.parametrize(
    ("z", "covered"),
    [(2.9, True), (3.2, True), (3.59, True), (3.61, False), (2.29, False)],  # the fascia spans z - 0.2 .. z + 0.2
)
def test_a_wall_prop_may_not_cover_a_shop_sign(z: float, covered: bool) -> None:
    from onward_generator.overgrow.urban import shop_signs

    result = Dressing(signs=shop_signs(_shopfront(_fascia(0.0, 8.0, z))))
    assert _covers_sign(_awning(), _wall_spot(), result) is covered


def test_only_a_wall_prop_over_a_sign_counts() -> None:
    from onward_generator.overgrow.urban import shop_signs

    signs = shop_signs(_shopfront(_fascia(0.0, 8.0, 2.9)))
    assert _covers_sign(_awning(), _wall_spot(5.0), Dressing(signs=signs)) is False  # further along the wall
    assert _covers_sign(_awning(), _wall_spot(), Dressing()) is False
    # A 3.5 m pole right under the fascia is a ground prop: only wall props are held to the signs.
    pole = _solid_prop("pole_0", "pole", "structures", (0.2, 0.2, 3.5))
    assert _covers_sign(pole, Spot("sidewalk", 0.0, 8.0, 0.0, "grid:0", "grid", 10.0), Dressing(signs=signs)) is False


def test_a_wall_prop_over_a_sign_is_rejected_reported_and_the_search_goes_on() -> None:
    from onward_generator.overgrow.urban import shop_signs

    awning = _awning()
    signs = shop_signs(_shopfront(_fascia(0.0, 8.0, 2.9)))
    spots = [_wall_spot() for _ in range(6)] + [_wall_spot(10.0)]
    spots = [replace(spot, key=f"facade:b:{n}") for n, spot in enumerate(spots)]
    result = Dressing(signs=signs)
    _place_props([awning], spots, STRtree([box(-10.0, 8.0, 20.0, 20.0)]), None, [], square_zone(80.0), profile_for(100),
                 48152, [], 60_000, result, used=[], seen=set())
    assert [row["position_m"][0] for row in result.records][:1] == [10.0]
    assert {"asset_id": "awning_0", "reason": "covers_sign", "kind": "facade", "position_m": [0.0, 7.65, 0.0]} in result.rejects
    repeated = Dressing(signs=signs)
    _repeat(awning, spots, repeated)
    assert [row["position_m"][0] for row in repeated.records] == [10.0]


def test_fit_fails_a_wall_prop_over_a_shop_sign(tmp_path: Path) -> None:
    sign = {"id": "building/b", "type": "building", "facade": {"shops": [{"sign": _fascia(0.0, 8.0, 2.6)}]}}
    _checked, problems = _fit_scene(tmp_path / "over", [_mounted("aircon", 0.08)], urban=[sign])
    assert [p.split()[1] for p in problems] == ["covers_sign"]
    high = {"id": "building/b", "type": "building", "facade": {"shops": [{"sign": _fascia(0.0, 8.0, 3.21)}]}}
    _checked, problems = _fit_scene(tmp_path / "above", [_mounted("aircon", 0.08)], urban=[high])
    assert problems == []



@pytest.mark.parametrize(("z", "covered"), [(3.75, False), (2.25, False), (3.74, True), (2.26, True)])
def test_a_sign_that_only_touches_a_wall_prop_is_not_covered(z: float, covered: bool) -> None:
    from onward_generator.overgrow.urban import shop_signs

    # Exact in binary: the 1 m awning hangs from 2.5 to 3.5 m, the 0.5 m fascia from z - 0.25 to z + 0.25.
    awning = Prop(AssetSpec("awning_0", "facade", "awning", ("facade",), "facade", "test"), _mesh(1.9, 0.96, 1.0),
                  np.zeros((24, 2), dtype=np.float32), b"")
    sign = {**_fascia(0.0, 8.0, z), "height_m": 0.5}
    assert _covers_sign(awning, _wall_spot(), Dressing(signs=shop_signs(_shopfront(sign)))) is covered


@pytest.mark.parametrize(("z", "problem"), [(3.75, False), (2.25, False), (3.74, True), (2.26, True)])
def test_fit_lets_a_sign_touch_a_wall_prop(tmp_path: Path, z: float, problem: bool) -> None:
    # Exact in float32: a 1 m aircon from 2.5 to 3.5 m, 0.08 m off the south wall.
    matrix = frame_matrix((0.0, 8.0 - 0.08 - 0.25, 2.5), (-1.0, 0.0, 0.0), (0.0, -1.0, 0.0))
    aircon = Node("overgrow/prop/aircon/0", (Primitive(_mesh(0.8, 0.5, 1.0), "prop_aircon"),), {"role": "aircon", "kind": "facade"}, matrix)
    sign = {"id": "building/b", "type": "building", "facade": {"shops": [{"sign": {**_fascia(0.0, 8.0, z), "height_m": 0.5}}]}}
    _checked, problems = _fit_scene(tmp_path, [aircon], urban=[sign])
    assert [p.split()[1] for p in problems] == (["covers_sign"] if problem else [])
