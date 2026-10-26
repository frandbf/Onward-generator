"""Phases 1–3: profiles, zone, categories. Fixtures are metres around MURCIA_CENTER.

Downloads are not required. Props are tiny synthetic GLBs.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from shapely import LineString, Point, Polygon, STRtree

from onward_generator.generation.config import Material
from onward_generator.generation.gltf import Node, Primitive, build_glb, nodes_from_glb
from onward_generator.generation.mesh import Mesh, box
from onward_generator.overgrow.catalog import ALL_ASSETS, CATEGORIES, PLACEMENT_RULES, WEAR_NOTE, rules_for
from onward_generator.overgrow.catalog import AssetSpec
from onward_generator.overgrow.dressing import block_reason
from onward_generator.overgrow.growth import moss_png
from onward_generator.overgrow.library import load_library
from onward_generator.overgrow.normalize import cluster_decimate, normalize_arrays
from onward_generator.overgrow.place import overgrow
from onward_generator.overgrow.profiles import profile_for, scaled_cap
from onward_generator.overgrow.zone import radius_zone, square_zone
from tests.conftest import MURCIA_CENTER


def _feature(kind: str, geometry: dict, tags: dict, feature_id: str) -> dict:
    return {
        "id": feature_id,
        "kind": kind,
        "tags": tags,
        "geometry": {**geometry, "unit": "m", "coordinate_system": "local_grid"},
    }


def _scene(path: Path, features: list[dict]) -> None:
    path.write_text(json.dumps({
        "schema": "onward_generator.geo_scene",
        "origin": {"longitude": MURCIA_CENTER[0], "latitude": MURCIA_CENTER[1]},
        "features": features,
    }), encoding="utf-8")


def _base(path: Path) -> None:
    path.mkdir()
    ground = Mesh(
        np.array([[0.0, 0.0, 0.0], [4.0, 0.0, 0.0], [0.0, 4.0, 0.0]], dtype=np.float64),
        np.array([[0, 1, 2]]),
    )
    (path / "scene_visual.glb").write_bytes(build_glb(
        [Node("ground", (Primitive(ground, "ground"),), {"type": "surface"})],
        {"ground": Material((0.4, 0.4, 0.4, 1), 1)},
    ))
    (path / "scene_collision.glb").write_bytes(b"collision-bytes")
    (path / "scene_detail.json").write_text(json.dumps({
        "schema": "onward_generator.scene_detail",
        "objects": [{"id": "building/osm-way-block", "type": "building", "source_ids": ["osm-way-block"]}],
        "files": {"visual": "scene_visual.glb", "collision": "scene_collision.glb"},
    }), encoding="utf-8")


def _zigzag() -> dict:
    ring = [(0.0, 16.0)]
    for index in range(14):
        ring.append((4.0 * (index + 1), 16.0 if index % 2 == 0 else 21.0))
    ring.extend([(60.0, 36.0), (0.0, 36.0), (0.0, 16.0)])
    return _feature(
        "building",
        {"type": "Polygon", "coordinates": [ring]},
        {"building": "yes", "height": "12"},
        "osm-way-block",
    )


def _city_features() -> list[dict]:
    return [
        _feature("highway", {"type": "LineString", "coordinates": [[-50, 0], [50, 0]]},
                 {"highway": "residential", "name": "Calle Mayor"}, "osm-way-mayor"),
        _feature("building", {"type": "Polygon", "coordinates": [[[12, 10], [32, 10], [32, 28], [12, 28], [12, 10]]]},
                 {"building": "yes", "height": "12"}, "osm-way-block"),
        _feature("area", {"type": "Polygon", "coordinates": [[[-36, 10], [-16, 10], [-16, 28], [-36, 28], [-36, 10]]]},
                 {"leisure": "park"}, "osm-way-park"),
        _feature("poi", {"type": "Point", "coordinates": [8, 20]}, {"natural": "tree"}, "osm-node-tree"),
    ]


def _prop_glb(path: Path, name: str, *, length: float = 0.5, width: float = 0.3, height: float = 0.4) -> None:
    mesh = box(-length / 2, length / 2, -width / 2, width / 2, 0.0, height)
    uvs = np.zeros((mesh.vertex_count, 2), dtype=np.float32)
    path.write_bytes(build_glb(
        [Node(name, (Primitive(mesh, name, uvs),), {"type": "prop"})],
        {name: Material((1, 1, 1, 1), 0.9)},
        repeat_images={name: moss_png()},
        preserve_uvs=True,
    ))


def _library(path: Path) -> None:
    path.mkdir()
    specs = [
        ("synth_car", "vehicles", "wreck", ["curb"], "ground", "forbid", 0.0),
        ("synth_trash", "waste", "trash", ["sidewalk"], "ground", "forbid", 0.0),
        ("synth_barrier", "barriers", "barrier", ["curb"], "ground", "forbid", 0.0),
        ("synth_hole", "barriers", "manhole", ["carriageway"], "flat", "allow", 0.0),
        ("synth_table", "furniture", "table", ["sidewalk", "park"], "ground", "forbid", 0.0),
        ("synth_gate", "structures", "gate", ["block_edge"], "ground", "forbid", 0.0),
        ("synth_cam", "facade", "camera", ["facade"], "facade", "forbid", 0.0),
        ("synth_fern", "vegetation", "plant", ["sidewalk", "yard"], "ground", "forbid", 0.0),
        ("synth_bush", "vegetation", "bush", ["courtyard", "yard"], "ground", "forbid", 0.0),
        ("synth_tree", "vegetation", "tree", ["park", "plaza"], "ground", "forbid", 3.0),
    ]
    assets = []
    for asset_id, category, role, surfaces, anchor, lane, clearance in specs:
        if asset_id == "synth_car":
            _prop_glb(path / f"{asset_id}.glb", asset_id, length=4.2, width=1.8, height=1.5)
        else:
            _prop_glb(path / f"{asset_id}.glb", asset_id)
        assets.append({
            "id": asset_id, "file": f"{asset_id}.glb", "category": category, "role": role,
            "surfaces": surfaces, "anchor": anchor, "lane": lane, "clearance_m": clearance, "use": "test",
        })
    (path / "manifest.json").write_text(json.dumps({"assets": assets}), encoding="utf-8")


def test_rules_do_not_name_concrete_assets() -> None:
    blob = " ".join(rule.id + " " + " ".join(rule.roles) + " ".join(rule.surfaces) for rule in PLACEMENT_RULES)
    for spec in ALL_ASSETS:
        assert spec.id not in blob
    assert "wear" in CATEGORIES
    assert "albedo" in WEAR_NOTE
    assert (MURCIA_CENTER[0], MURCIA_CENTER[1]) == MURCIA_CENTER


def test_manifest_uses_the_wider_surface_catalog() -> None:
    root = Path(__file__).resolve().parents[2]
    document = json.loads((root / "assets/normalized/manifest.json").read_text(encoding="utf-8"))
    rows = {row["id"]: row for row in document["assets"]}
    assert set(rows["barrel_stove"]["surfaces"]) >= {"courtyard", "yard", "curb", "carriageway", "plaza"}
    assert set(rows["covered_car"]["surfaces"]) >= {"curb", "carriageway", "sidewalk", "empty_lot", "plaza"}
    assert set(rows["crashed_helicopter"]["surfaces"]) >= {"plaza", "park", "courtyard"}
    assert set(rows["watchtower"]["surfaces"]) >= {"block_edge", "plaza", "curb"}
    assert rows["water_manhole_cover"]["surfaces"] == ["carriageway"]
    assert rows["fire_escape_sf"]["surfaces"] == ["facade"]
    assert rows["jungle_ground_tile"]["surfaces"] == ["yard", "empty_lot"]
    for row in rows.values():
        spec = AssetSpec(
            row["id"], row["category"], row["role"], tuple(row["surfaces"]),
            row["anchor"], row["use"], lane=row.get("lane") or "forbid",
            clearance_m=float(row.get("clearance_m") or 0.0),
        )
        rules = rules_for(spec)
        assert rules, row["id"]
        allowed = set().union(*(set(rule.surfaces) for rule in rules))
        assert set(spec.surfaces) <= allowed, row["id"]


def test_profile_lowers_density_and_caps() -> None:
    assert profile_for(10).moss < profile_for(100).moss
    assert scaled_cap(12, profile_for(10).props) < scaled_cap(12, profile_for(100).props)


def test_profile_changes_how_many_moss_strips_are_kept(tmp_path: Path) -> None:
    scene = tmp_path / "geo_scene.json"
    _scene(scene, [
        _feature("highway", {"type": "LineString", "coordinates": [[-20, 0], [70, 0]]},
                 {"highway": "residential"}, "osm-way-mayor"),
        _zigzag(),
    ])
    _base(tmp_path / "base")
    zone = square_zone(80)
    low = overgrow(tmp_path / "base", scene, tmp_path / "low", profile=10, seed=48152, zone=zone)
    high = overgrow(tmp_path / "base", scene, tmp_path / "high", profile=100, seed=48152, zone=zone)
    assert high["moss"] > low["moss"]
    assert high["profile_years"] == 100 and low["profile_years"] == 10


def test_same_seed_is_byte_stable_and_a_different_seed_is_not(tmp_path: Path) -> None:
    scene = tmp_path / "geo_scene.json"
    _scene(scene, _city_features())
    _base(tmp_path / "base")
    zone = square_zone(80)
    overgrow(tmp_path / "base", scene, tmp_path / "a", seed=48152, zone=zone)
    overgrow(tmp_path / "base", scene, tmp_path / "b", seed=48152, zone=zone)
    overgrow(tmp_path / "base", scene, tmp_path / "c", seed=7, zone=zone)
    first = (tmp_path / "a" / "scene_visual.glb").read_bytes()
    assert first == (tmp_path / "b" / "scene_visual.glb").read_bytes()
    assert first != (tmp_path / "c" / "scene_visual.glb").read_bytes()


def test_zone_rejects_points_outside_the_disc(tmp_path: Path) -> None:
    scene = tmp_path / "geo_scene.json"
    _scene(scene, _city_features())
    _base(tmp_path / "base")
    report = overgrow(tmp_path / "base", scene, tmp_path / "out", zone=radius_zone(5))
    assert report["reject_counts"].get("outside_zone", 0) > 0
    for row in json.loads((tmp_path / "out" / "scene_detail.json").read_text())["objects"]:
        if row.get("type") != "overgrowth" or "position_m" not in row:
            continue
        x, y, _z = row["position_m"]
        assert x * x + y * y <= 5.1 ** 2


def test_block_reason_rejects_buildings_lanes_and_spawns() -> None:
    buildings = STRtree([Polygon([(0, 10), (4, 10), (4, 14), (0, 14)])])
    lanes = [LineString([(-20, 0), (20, 0)])]
    zone = square_zone(30)
    common = {"zone": zone, "buildings": buildings, "lane_tree": STRtree(lanes), "lanes": lanes, "spawns": [(8.0, 8.0)]}
    assert block_reason(2, 12, **common) == "inside_building"
    assert block_reason(8, 0.4, **common) == "drive_lane"
    assert block_reason(8, 0.4, **common, allow_lane=True) is None
    assert block_reason(8, 8, **common) == "spawn_point"
    assert block_reason(40, 0, **common) == "outside_zone"


def test_each_category_can_place_without_touching_collision_or_footprints(tmp_path: Path) -> None:
    scene = tmp_path / "geo_scene.json"
    _scene(scene, _city_features())
    _base(tmp_path / "base")
    library = tmp_path / "library"
    _library(library)
    report = overgrow(
        tmp_path / "base", scene, tmp_path / "out",
        zone=square_zone(80), library=library, seed=48152,
    )
    assert report["geometry"]["identical"] is True
    collision_nodes, _ = nodes_from_glb((tmp_path / "out" / "scene_collision.glb").read_bytes())
    names = [node.name for node in collision_nodes]
    assert any(name.startswith("overgrow/collider/") for name in names)
    assert not any("synth_trash" in name or "synth_fern" in name or "synth_cam" in name for name in names)
    placed = set(report["placed_assets"])
    kinds = {row["kind"] for row in json.loads((tmp_path / "out" / "scene_detail.json").read_text())["objects"] if row.get("type") == "overgrowth"}
    for category in ("vehicles", "waste", "barriers", "furniture", "structures", "facade", "vegetation"):
        assert category in kinds
    assert {"synth_car", "synth_trash", "synth_barrier", "synth_hole", "synth_gate", "synth_cam"} <= placed
    objects = json.loads((tmp_path / "out" / "scene_detail.json").read_text())["objects"]
    building = next(row for row in objects if row["id"] == "building/osm-way-block")
    assert building["source_ids"] == ["osm-way-block"]
    for row in objects:
        if row.get("asset_id") in {"synth_trash", "synth_table", "synth_fern", "synth_bush", "synth_car", "synth_barrier", "synth_gate"}:
            assert abs(row["position_m"][1]) > 4.2
        if row.get("asset_id") == "synth_hole":
            assert abs(row["position_m"][1]) < 4.2
        if row.get("asset_id") == "synth_cam":
            assert row["position_m"][2] > 0.5


def test_missing_library_does_not_crash(tmp_path: Path) -> None:
    scene = tmp_path / "geo_scene.json"
    _scene(scene, _city_features())
    _base(tmp_path / "base")
    report = overgrow(tmp_path / "base", scene, tmp_path / "out", library=tmp_path / "missing-library")
    assert report["props"] == 0
    assert report["library_assets"] == 0
    assert report["geometry"]["identical"] is True


def test_decimate_shrinks_a_dense_grid_and_keeps_a_single_triangle() -> None:
    step = 25
    vertices = np.array([(i, j, 0.0) for i in range(step) for j in range(step)], dtype=np.float64)
    triangles = []
    for i in range(step - 1):
        for j in range(step - 1):
            base = i * step + j
            triangles.append((base, base + 1, base + step))
            triangles.append((base + 1, base + step + 1, base + step))
    tris = np.array(triangles, dtype=np.int64)
    uvs = np.zeros((len(vertices), 2), dtype=np.float32)
    _small_v, small_t, _small_uv = cluster_decimate(vertices, tris, uvs, 40)
    assert len(small_t) <= 40
    kept_v, kept_t, kept_uv = cluster_decimate(vertices[:3], tris[:1], uvs[:3], 40)
    assert len(kept_t) == 1
    assert len(kept_v) == 3
    assert len(kept_uv) == 3
    assert Point(0, 0).distance(LineString([(0, 0), (1, 0)])) == 0.0


def test_simplification_puts_the_lowest_vertex_back_on_the_ground() -> None:
    step, layers = 18, 5
    vertices = np.array(
        [(i * 0.1, j * 0.1, k * 0.05) for k in range(layers) for i in range(step) for j in range(step)],
        dtype=np.float64,
    )
    span = step * step
    triangles = []
    for layer in range(layers - 1):
        for i in range(step - 1):
            for j in range(step - 1):
                base = layer * span + i * step + j
                above = base + span
                triangles.append((base, base + 1, above))
                triangles.append((base + 1, above + 1, above))
    tris = np.array(triangles, dtype=np.int64)
    assert len(tris) > 1800
    normalized = normalize_arrays(vertices, tris, np.zeros((len(vertices), 2), dtype=np.float32), None, anchor="ground", api_dimensions_mm=None)
    assert abs(float(normalized.mesh.vertices[:, 2].min())) < 1e-6


def test_a_loaded_prop_rests_its_bottom_on_zero(tmp_path: Path) -> None:
    root = tmp_path / "library"
    root.mkdir()
    mesh = box(-0.2, 0.2, -0.2, 0.2, 0.03, 0.8)
    (root / "metal_trash_can.glb").write_bytes(build_glb(
        [Node("metal_trash_can", (Primitive(mesh, "albedo"),), {})],
        {"albedo": Material((1, 1, 1, 1), 1)},
        repeat_images={"albedo": moss_png()},
    ))
    (root / "manifest.json").write_text(json.dumps({
        "assets": [{"id": "metal_trash_can", "file": "metal_trash_can.glb"}],
    }), encoding="utf-8")
    props = load_library(root)
    assert len(props) == 1
    assert abs(float(props[0].mesh.vertices[:, 2].min())) < 1e-6
    assert abs(props[0].height_m - 0.77) < 1e-6
