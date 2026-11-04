"""Size and footprint checks for abandoned props. Murcia centre is the origin."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest
from shapely import LineString, STRtree, box

from onward_generator.detail.modules import frame_matrix, yaw_matrix
from onward_generator.generation.config import Material
from onward_generator.generation.gltf import Node, Primitive, build_glb
from onward_generator.generation.mesh import Mesh
from onward_generator.generation.mesh import box as mesh_box
from onward_generator.overgrow.ambient import cross_yaw
from onward_generator.overgrow.catalog import AssetSpec
from onward_generator.overgrow.dressing import Dressing, Spot, _place_props, dress
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
