"""The helpers of ``perf cover`` that read the layout, the manifest and the catalogue, and write records (plan C7).

Local metres on small documents; ``tests/integration/test_sightline_cover_pipeline.py`` runs the whole stage
on the synthetic block around MURCIA_CENTER.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from shapely import LineString, MultiPolygon, Point, box
from shapely.geometry import Polygon

from onward_generator.gameplay.cover import Caps, CoverPlan, CoverProp, Placement, Spot, cover_props, footprint, pose
from onward_generator.gameplay.cover_stage import (
    CATALOGUE_LICENCES,
    COVER_DIR,
    COVER_REPORT,
    CoverError,
    CoverResult,
    _anchors,
    _doors,
    _load_json,
    _nodes,
    _point,
    _rays_cut,
    _records,
    _stretches,
    _xy,
    _zone,
    build_site,
    catalogue_licences,
    cover_note,
)
from onward_generator.gameplay.metrics import SightCaster
from onward_generator.gameplay.report import OpenPoint
from onward_generator.generation.config import Material
from onward_generator.generation.gltf import Node, Primitive, build_glb
from onward_generator.generation.mesh import box as mesh_box
from onward_generator.generation.surfaces import SurfaceClass
from onward_generator.overgrow.catalog import AssetSpec
from onward_generator.overgrow.fit import collider_mesh
from onward_generator.overgrow.library import Prop
from tests.conftest import MURCIA_CENTER


def _cover(asset_id: str = "barrier", size: tuple[float, float, float] = (1.23456, 0.54321, 1.11111), *,
           role: str = "barrier", fit_scale: float = 0.123456) -> CoverProp:
    length, width, height = size
    mesh = mesh_box(-length / 2, length / 2, -width / 2, width / 2, 0.0, height)
    spec = AssetSpec(asset_id, "barriers", role, ("sidewalk",), "ground", "test")
    prop = Prop(spec, mesh, np.zeros((mesh.vertex_count, 2), dtype=np.float32), b"png", fit_scale=fit_scale)
    kept, refused = cover_props([prop], {asset_id: "CC0-1.0"}, frozenset({asset_id}))
    assert refused == {}
    return kept[0]


def _placement(prop: CoverProp, x: float, y: float, yaw: float, z: float, target: OpenPoint) -> Placement:
    spot = Spot(x, y, yaw)
    matrix = pose(prop, spot, z)
    return Placement(prop, spot, matrix, footprint(prop, matrix), target)


TARGET = OpenPoint((12.3456, -7.6543), (90.0, -7.6543), 77.65)


def test_constants_name_the_directory_the_report_and_the_kenney_licence() -> None:
    assert (COVER_DIR, COVER_REPORT) == ("scene_covered", "cover_report.json")
    assert dict(CATALOGUE_LICENCES) == {"kenney": "CC0-1.0"}
    assert MURCIA_CENTER == (-1.12863, 37.98407)


def test_points_and_xy_lists_are_read_strictly() -> None:
    assert _point([3, 4.5]) == (3.0, 4.5)
    assert _point([3, 4, "z"]) == (3.0, 4.0)
    assert _point([3]) is None
    assert _point([3, "a"]) is None
    assert _point((3, 4)) is None
    assert _point("34") is None
    assert _point(None) is None
    assert _xy([{"x": 1, "y": 2}, {"x": 3}, {"y": 4}, "xy", [1, 2], {"x": 5.5, "y": -6}]) == [(1.0, 2.0), (5.5, -6.0)]
    assert _xy({"x": 1, "y": 2}) == []
    assert _xy(None) == []


def test_anchors_are_the_block_centres_the_uplink_and_the_ai_starts() -> None:
    layout: dict[str, Any] = {
        "attackers": {"centre_m": [1, 2]}, "defenders": {"centre_m": [3, 4]},
        "attackers_b": {"centre_m": [5, 6]}, "defenders_b": {"centre_m": [7, 8]},
        "uplink_m": [9, 10], "ai_m": [{"x": 11, "y": 12}],
    }
    assert _anchors(layout) == [(1.0, 2.0), (3.0, 4.0), (5.0, 6.0), (7.0, 8.0), (9.0, 10.0), (11.0, 12.0)]
    assert _anchors({"attackers": [1, 2], "defenders": {"centre_m": None}, "uplink_m": None}) == []
    assert _anchors({"defenders_b": {"centre_m": [7, 8]}}) == [(7.0, 8.0)]


def test_doors_are_the_interiors_street_doors_and_the_facade_doors() -> None:
    manifest = {"objects": [
        {"type": "interior", "connections": [
            {"from": "exterior", "position_m": [1, 2]}, {"from": "stair", "position_m": [9, 9]},
        ]},
        {"type": "interior"},
        {"type": "building", "facade": {"doors_m": [[3, 4], [5, 6]]}},
        {"type": "building"},
        {"type": "building", "facade": None},
        {"type": "urban_element", "connections": [{"from": "exterior", "position_m": [7, 7]}], "facade": {"doors_m": [[8, 8]]}},
    ]}
    assert _doors(manifest) == [(1.0, 2.0), (3.0, 4.0), (5.0, 6.0)]
    assert _doors({}) == [] and _doors({"objects": None}) == []


def test_the_zone_is_the_playable_polygon_or_the_box_of_another_shape() -> None:
    square = box(0, 0, 10, 10)
    zone = _zone(square)
    assert zone.kind == "playable" and zone.polygon is square
    parts = MultiPolygon([box(0, 0, 1, 1), box(5, 5, 6, 6)])
    assert _zone(parts).polygon.equals(box(0, 0, 6, 6))


def test_load_json_names_the_file_and_the_fault(tmp_path: Path) -> None:
    path = tmp_path / "doc.json"
    with pytest.raises(CoverError, match=f"^missing {path}$"):
        _load_json(path)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(CoverError, match=r": not JSON \(") as caught:
        _load_json(path)
    assert str(caught.value).startswith(f"{path}: not JSON (")
    path.write_text("[1]", encoding="utf-8")
    with pytest.raises(CoverError) as listed:
        _load_json(path)
    assert str(listed.value) == f"{path}: expected a JSON object"
    path.write_text('{"a": "é"}', encoding="utf-8")
    assert _load_json(path) == {"a": "é"}


def _catalogue(root: Path, name: str, rows: list[Any]) -> None:
    (root / name).mkdir(parents=True)
    (root / name / "manifest.json").write_text(json.dumps({"assets": rows}), encoding="utf-8")


def test_catalogue_licences_read_each_row_and_the_kenney_default(tmp_path: Path) -> None:
    _catalogue(tmp_path, "a_rows", ["text", {"file": "no_id.glb"}, {"id": "cc0", "license": "CC0-1.0"},
                                    {"id": "by", "license": "CC-BY-4.0"}, {"id": "sa", "license": "CC-BY-SA-4.0"}, {"id": "none"}])
    (tmp_path / "b_broken").mkdir()
    (tmp_path / "b_broken" / "manifest.json").write_text("{broken", encoding="utf-8")
    _catalogue(tmp_path, "kenney", [{"id": "kenney_block"}, {"id": "kenney_sa", "license": "CC-BY-SA-4.0"}])
    _catalogue(tmp_path, "z_last", [{"id": "last", "license": "CC0-1.0"}])
    assert catalogue_licences(tmp_path) == {"cc0": "CC0-1.0", "by": "CC-BY-4.0", "kenney_block": "CC0-1.0", "last": "CC0-1.0"}
    assert catalogue_licences(None) == {}
    assert catalogue_licences(tmp_path / "nowhere") == {}


def test_nodes_put_the_prop_in_the_visual_and_its_box_in_the_collision() -> None:
    prop = _cover()
    fence = _cover("rail", size=(2.0, 0.5, 1.0), role="fence")
    first = _placement(prop, 13.0, -7.65, math.pi / 2, 0.25, TARGET)
    second = _placement(fence, 30.0, -7.0, 0.0, 0.0, TARGET)
    visual, colliders, images = _nodes([first, second])
    assert [node.name for node in visual] == ["overgrow/prop/barrier/cover/0", "overgrow/prop/rail/cover/1"]
    assert [node.mesh_key for node in visual] == ["cover:barrier", "cover:rail"]
    assert dict(visual[0].extras) == {
        "type": "overgrowth", "origin": "generated", "kind": "barriers", "role": "barrier", "asset_id": "barrier",
        "op": "sightline_cover", "anchor_id": "sample:12.35,-7.65", "fit_scale": 0.1235,
        "size_m": [1.235, 0.543, 1.111], "crosswise": True, "solid": True,
    }
    assert visual[0].matrix is first.matrix and len(visual[0].primitives) == 1
    assert visual[0].primitives[0].material == "prop_barrier"
    assert [node.name for node in colliders] == ["overgrow/collider/prop/barrier/cover/0", "overgrow/collider/prop/rail/cover/1"]
    assert dict(colliders[1].extras) == {"type": "collision", "origin": "generated", "asset_id": "rail", "role": "fence", "op": "sightline_cover"}
    assert colliders[1].matrix is second.matrix and colliders[1].primitives[0].material == "collider"
    expected = collider_mesh(fence.prop.mesh, "fence")
    assert np.array_equal(colliders[1].primitives[0].mesh.vertices, expected.vertices)
    assert images == {"prop_barrier": b"png", "prop_rail": b"png"}


def test_records_carry_provenance_position_and_the_reason() -> None:
    prop = _cover()
    item = _placement(prop, 13.0, -7.65, math.pi / 2, 0.25, TARGET)
    other = _placement(prop, 40.12345, 5.98765, 0.0, 0.0, OpenPoint((39.0, 6.0), (39.0, 99.0), 93.0))
    rows = _records([item, other])
    assert rows[0] == {
        "id": "overgrow/cover/0", "type": "overgrowth", "origin": "generated", "kind": "barriers", "role": "barrier",
        "asset_id": "barrier", "licence": "CC0-1.0", "source_ids": [], "op": "sightline_cover",
        "position_m": [13.0, -7.65, 0.25], "yaw_deg": 90.0, "size_m": [1.235, 0.543, 1.111], "solid": True, "crosswise": True,
        "reason": {"sample_m": [12.3456, -7.6543], "ray_to_m": [90.0, -7.6543], "ray_length_m": 77.65},
    }
    assert rows[1]["id"] == "overgrow/cover/1"
    assert rows[1]["position_m"] == [40.123, 5.988, 0.0]
    turned = _placement(prop, 0.0, 0.0, math.radians(12.3456), 0.12345, TARGET)
    row = _records([turned])[0]
    assert row["yaw_deg"] == 12.35 and row["position_m"] == [0.0, 0.0, 0.123]


def test_stretches_count_the_uncovered_and_the_long_ones() -> None:
    def stretch(cover: int, sight: float | None) -> dict[str, Any]:
        return {"cover": cover, "longest_open_sight_m": sight}

    report = {
        "headline": {"longest_open_sight_m": 80.0}, "status": "ok", "colliders": {"cover": 7},
        "pairs": [{"routes": [{"stretches": [stretch(0, 60.0), stretch(0, 60.01), stretch(2, None)]}]},
                  {"routes": [{"stretches": [stretch(1, 90.0)]}, {"stretches": [stretch(0, None)]}]}],
    }
    assert _stretches(report) == {
        "headline": {"longest_open_sight_m": 80.0}, "status": "ok", "stretches": 5,
        "uncovered_stretches": 3, "stretches_over_sightline": 2, "cover_colliders": 7,
    }
    assert _stretches({"pairs": None, "colliders": None}) == {
        "headline": {}, "status": None, "stretches": 0, "uncovered_stretches": 0, "stretches_over_sightline": 0, "cover_colliders": None,
    }


def test_rays_cut_counts_the_rays_a_new_box_shortens() -> None:
    wall = np.array([[100.0, -500.0, 100.0, 500.0]])
    before = SightCaster(wall)
    nearer = SightCaster(np.array([[99.5, -500.0, 99.5, 500.0]]))
    assert _rays_cut(before, before, (0.0, 0.0)) == 0
    # The rays that reach the wall within the 300 m cap: up to 70 degrees either side of east, 29 of them.
    assert _rays_cut(before, nearer, (0.0, 0.0)) == 29
    assert _rays_cut(before, nearer, (200.0, 0.0)) == 0


def test_cover_note_prints_the_plan_and_the_headline_before_and_after(tmp_path: Path) -> None:
    def side(sight: float, uncovered: int, over: int) -> dict[str, Any]:
        return {"headline": {"longest_open_sight_m": sight}, "uncovered_stretches": uncovered, "stretches_over_sightline": over}

    result = CoverResult(CoverPlan(3, (), {}, Caps(), "no valid spot"), {"before": side(133.86, 17, 32), "after": side(129.42, 4, 29)}, tmp_path)
    assert cover_note(result) == [
        "cover: nothing placed (no valid spot)",
        "cover: sight 133.86 -> 129.42 m, uncovered stretches 17 -> 4, over 60 m 32 -> 29 (estimated, report only)",
    ]


def _glb(path: Path, boxes: dict[str, tuple[float, float, float, float, float, float]]) -> Path:
    nodes = [Node(name, (Primitive(mesh_box(*bounds), "collider"),), {"type": "collision"}) for name, bounds in boxes.items()]
    path.write_bytes(build_glb(nodes, {"collider": Material((1, 1, 1, 1), 0.9)}))
    return path


def test_build_site_reads_the_ground_the_layout_and_the_colliders(tmp_path: Path) -> None:
    sidewalk, plaza, road = box(-80, 4, 80, 80), box(-80, -80, 80, -4), box(-80, -4, 80, 4)
    house = box(10, 10, 20, 20)
    crossing = box(-2, -4, 2, 4)
    partition = SimpleNamespace(
        surfaces={SurfaceClass.SIDEWALK: SimpleNamespace(geometry=sidewalk), SurfaceClass.PEDESTRIAN: SimpleNamespace(geometry=plaza),
                  SurfaceClass.CARRIAGEWAY: SimpleNamespace(geometry=road)},
        buildings=house, crossings=crossing,
    )
    base = SimpleNamespace(partition=partition)
    manifest = {"core": {"half_side_m": 60.0}, "envelope": {"thickness_m": 0.5}, "objects": [
        {"type": "urban_element", "kind": "bench", "position_m": [5.0, 6.0]},
        {"type": "building", "facade": {"doors_m": [[12.0, 9.5]]}},
    ]}
    layout = {
        "ammo_m": [{"x": 1, "y": 2}], "ladders": [{"x": 3, "y": 4}], "lifts": [{"x": 5, "y": 6}],
        "routes": [{"points": [{"x": 0, "y": 10}, {"x": 20, "y": 10}]}, {"points": [{"x": 0, "y": 0}]}, {}],
        "attackers": {"centre_m": [-20, 20], "points_m": [{"x": -20, "y": 20}]}, "uplink_m": [0, 30], "ai_m": [{"x": 7, "y": 8}],
    }
    layout_path = tmp_path / "layout.json"
    layout_path.write_text(json.dumps(layout), encoding="utf-8")
    scene = tmp_path / "geo_scene.json"
    scene.write_text(json.dumps({"features": [
        {"kind": "highway", "tags": {"highway": "residential"}, "geometry": {"type": "LineString", "coordinates": [[-80, 0], [80, 0]]}},
        {"kind": "building", "geometry": {"type": "Polygon", "coordinates": [[[10, 10], [20, 10], [20, 20], [10, 20], [10, 10]]]}},
    ]}), encoding="utf-8")
    collision = _glb(tmp_path / "scene_collision.glb", {
        "overgrow/collider/prop/a/0": (0, 1, 0, 1, 0.5, 1.5),
        "overgrow/collider/prop/b/0": (2, 3, 2, 3, 0.51, 1.5),
        "envelope/perimeter_wall#collider": (-60, 60, -60, 60, 0, 12),
        "building/1#collider": (10, 20, 10, 20, 0, 9),
    })
    site = build_site(base, manifest, layout, layout_path, scene, collision)  # type: ignore[arg-type]
    playable = box(-59.5, -59.5, 59.5, 59.5)
    assert site.playable is not None and site.playable.equals(playable)
    assert site.zone.kind == "playable" and site.zone.polygon.equals(playable)
    free = sidewalk.union(plaza).difference(house).intersection(playable)
    assert site.free.symmetric_difference(free).area < 1e-6
    assert site.ground.symmetric_difference(free.union(crossing.intersection(playable))).area < 1e-6
    assert site.islands.covers(Point(0.0, 0.0)) and not site.islands.covers(Point(15.0, 15.0))
    assert not site.islands.covers(Point(59.6, 20.0)) and site.islands.covers(Point(58.0, 20.0))
    assert [s.bounds for s in site.solids] == [(0.0, 0.0, 1.0, 1.0)]
    assert site.ammo == [(1.0, 2.0)] and site.climbs == [(3.0, 4.0), (5.0, 6.0)] and site.doors == [(12.0, 9.5)]
    assert [list(r.coords) for r in site.routes] == [[(0.0, 10.0), (20.0, 10.0)]]
    assert len(site.urban) == 1 and site.urban[0].contains(Point(5.0, 6.0))
    assert site.anchors == [(-20.0, 20.0), (0.0, 30.0), (7.0, 8.0)]
    assert sorted(site.spawns) == sorted([(7.0, 8.0), (-20.0, 20.0), (0.0, 30.0)])
    assert [list(lane.coords) for lane in site.lanes] == [[(-80.0, 0.0), (80.0, 0.0)]]
    assert site.lane_tree is not None and len(site.lane_tree.geometries) == 1
    assert site.buildings.query(Point(15.0, 15.0), predicate="intersects").size == 1

    open_map = build_site(base, {"objects": []}, {}, tmp_path / "missing.json", scene, collision)  # type: ignore[arg-type]
    assert open_map.playable is None and open_map.spawns == [] and open_map.routes == []
    # Without a core the zone is the walkable ground itself, one polygon here.
    assert open_map.zone.polygon is open_map.ground
    assert open_map.free.symmetric_difference(sidewalk.union(plaza).difference(house)).area < 1e-6
    no_lanes = tmp_path / "no_lanes.json"
    no_lanes.write_text(json.dumps({"features": []}), encoding="utf-8")
    assert build_site(base, {"objects": []}, {}, layout_path, no_lanes, collision).lane_tree is None  # type: ignore[arg-type]
    assert isinstance(LineString([(0, 0), (1, 1)]), LineString) and isinstance(playable, Polygon)


def _inputs(tmp_path: Path) -> Path:
    detail = tmp_path / "scene_playable"
    detail.mkdir()
    for name in ("scene_visual.glb", "scene_collision.glb", "detail_report.json"):
        (detail / name).write_bytes(b"x")
    return detail


WALLED = {"envelope": {"id": "envelope/perimeter_wall"}, "objects": ["text", {"id": "building/1"}, {"id": "envelope/perimeter_wall"}]}
PLAYABLE = {"schema": "onward_generator.gameplay_layout", "schema_version": "0.3.0"}


def test_check_inputs_needs_the_wall_in_the_envelope_and_the_objects(tmp_path: Path) -> None:
    from onward_generator.gameplay.cover_stage import _check_inputs

    detail = _inputs(tmp_path)
    out = tmp_path / "scene_covered"
    _check_inputs(detail, WALLED, PLAYABLE, out, False)
    message = f"{detail}: has no envelope/perimeter_wall; cover reads the bounds output (scene_playable)"
    for manifest in (
        {"envelope": {"id": "envelope/perimeter_wall"}, "objects": [{"id": "building/1"}]},
        {"envelope": None, "objects": [{"id": "envelope/perimeter_wall"}]},
        {"objects": None},
    ):
        with pytest.raises(CoverError) as caught:
            _check_inputs(detail, manifest, PLAYABLE, out, False)
        assert str(caught.value) == message
    held = {**WALLED, "objects": [*WALLED["objects"], {"id": "overgrow/cover/0", "op": "sightline_cover"}]}
    with pytest.raises(CoverError, match="already holds sightline_cover props"):
        _check_inputs(detail, held, PLAYABLE, out, False)


def test_check_inputs_needs_a_playable_layout_its_files_and_a_new_output(tmp_path: Path) -> None:
    from onward_generator.gameplay.cover_stage import _check_inputs

    detail = _inputs(tmp_path)
    out = tmp_path / "scene_covered"
    for layout in ({**PLAYABLE, "schema_version": "0.2.0"}, {**PLAYABLE, "schema": "onward_generator.perf_layout"}):
        with pytest.raises(CoverError) as caught:
            _check_inputs(detail, WALLED, layout, out, False)
        assert str(caught.value) == (
            "cover reads a playable onward_generator.gameplay_layout 0.3.0 layout (perf layout --playable), "
            f"not {layout['schema']!r} {layout['schema_version']!r}"
        )
    with pytest.raises(CoverError) as same:
        _check_inputs(detail, WALLED, PLAYABLE, detail, True)
    assert str(same.value) == "output must be a new directory"
    out.mkdir()
    with pytest.raises(CoverError) as taken:
        _check_inputs(detail, WALLED, PLAYABLE, out, False)
    assert str(taken.value) == f"refusing to overwrite existing output {out}; pass --overwrite"
    _check_inputs(detail, WALLED, PLAYABLE, out, True)
    for name in ("scene_visual.glb", "scene_collision.glb", "detail_report.json"):
        (detail / name).unlink()
        with pytest.raises(CoverError) as missing:
            _check_inputs(detail, WALLED, PLAYABLE, out, True)
        assert str(missing.value) == f"{detail}: missing {name}"
        (detail / name).write_bytes(b"x")


def test_overgrow_triangles_and_scene_assets_read_the_visual() -> None:
    from onward_generator.gameplay.cover_stage import _overgrow_triangles, _scene_assets

    cube = mesh_box(0, 1, 0, 1, 0, 1)
    nodes = [Node("overgrow/prop/a/0", (Primitive(cube, "prop_a"), Primitive(cube, "prop_a__1"))), Node("building/1", (Primitive(cube, "wall"),)),
             Node("x_overgrow/1", (Primitive(cube, "m"),))]
    assert _overgrow_triangles(nodes) == 2 * cube.triangle_count
    grey = Material((1, 1, 1, 1), 0.9)
    assert _scene_assets({"prop_a": grey, "prop_b__1": grey, "prop_c__d": grey, "wall": grey, "x_prop_e": grey}) == frozenset({"a", "b", "c"})


@pytest.mark.parametrize("flag", ["--scene", "--heights", "--base", "--detail", "--layout", "--out"])
def test_perf_cover_needs_each_path(flag: str, tmp_path: Path) -> None:
    from onward_generator.perf.__main__ import build_parser

    argv = {name: str(tmp_path / name.strip("-")) for name in ("--scene", "--heights", "--base", "--detail", "--layout", "--out")}
    full = ["cover", *[part for name, value in argv.items() for part in (name, value)]]
    args = build_parser().parse_args(full)
    assert (args.library, args.generation_config, args.overwrite) == ("assets", None, False)
    with pytest.raises(SystemExit):
        build_parser().parse_args([part for part in full if part not in (flag, argv[flag])])
