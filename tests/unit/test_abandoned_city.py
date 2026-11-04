"""Phases 1–3: profiles, zone, categories. Fixtures are metres around MURCIA_CENTER.

Downloads are not required. Props are tiny synthetic GLBs.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest
from shapely import LineString, Point, Polygon, STRtree

from onward_generator.generation.config import Material
from onward_generator.generation.gltf import Node, Primitive, build_glb, nodes_from_glb, read_glb
from onward_generator.generation.mesh import Mesh, box
from onward_generator.overgrow.catalog import ALL_ASSETS, CATEGORIES, PLACEMENT_RULES, WEAR_NOTE, AssetSpec, rules_for
from onward_generator.overgrow.dressing import Dressing, block_reason
from onward_generator.overgrow.growth import grass_png
from onward_generator.overgrow.library import load_library, read_library
from onward_generator.overgrow.normalize import cluster_decimate, normalize_arrays
from onward_generator.overgrow.place import overgrow
from onward_generator.overgrow.profiles import profile_for, scaled_cap
from onward_generator.overgrow.zone import ZoneError, radius_zone, square_zone, within_core, zone_from_polygon
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
        repeat_images={name: grass_png()},
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
    assert rows["water_manhole_cover"]["surfaces"] == ["carriageway", "sidewalk"]
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
    assert profile_for(10).grass < profile_for(100).grass
    assert scaled_cap(12, profile_for(10).props) < scaled_cap(12, profile_for(100).props)


def test_profile_changes_how_many_grass_tufts_are_kept(tmp_path: Path) -> None:
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
    assert high["grass"] > low["grass"]
    assert high["profile_years"] == 100 and low["profile_years"] == 10


def test_no_moss_skirt_is_written_but_the_report_keeps_the_key(tmp_path: Path) -> None:
    scene = tmp_path / "geo_scene.json"
    _scene(scene, [
        _feature("highway", {"type": "LineString", "coordinates": [[-20, 0], [70, 0]]},
                 {"highway": "residential"}, "osm-way-mayor"),
        _zigzag(),
    ])
    _base(tmp_path / "base")
    report = overgrow(tmp_path / "base", scene, tmp_path / "out", profile=100, seed=48152, zone=square_zone(80))
    assert report["moss"] == 0
    assert all(row.get("kind") != "moss" for row in report["rejects"])
    nodes, materials = nodes_from_glb((tmp_path / "out" / "scene_visual.glb").read_bytes())
    assert not any(node.name == "overgrow/moss" for node in nodes)
    assert "abandoned_moss" not in materials


def test_overgrow_place_prints_the_summary_without_moss(tmp_path: Path, capsys) -> None:
    from onward_generator.overgrow.__main__ import build_parser as overgrow_parser
    from onward_generator.overgrow.__main__ import main as overgrow_main

    assert overgrow_parser().description == "Add abandoned-city grass, trees and wrecks to a scene copy. Does not export."
    scene = tmp_path / "geo_scene.json"
    _scene(scene, _city_features())
    _base(tmp_path / "base")
    out = tmp_path / "out"
    assert overgrow_main(["place", "--base", str(tmp_path / "base"), "--scene", str(scene), "--out", str(out), "--zone-radius", "60"]) == 0
    report = json.loads((out / "overgrow_report.json").read_text(encoding="utf-8"))
    first = capsys.readouterr().out.splitlines()[0]
    assert first == (
        f"overgrow: grass={report['grass']} vines={report['vines']} trees={report['trees']} "
        f"({report['trees_observed']} observed) wrecks={report['wrecks']} props={report['props']} tris={report['added_triangles']}"
    )


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
    # The block's long walls each get a generated tree 2.4 m out, where their block edge is, and a gate
    # keeps clear of a tree. The kiosk's walls are too short for a tree, so the gate has an edge of its own.
    kiosk = _feature("building", {"type": "Polygon", "coordinates": [[[40, 12], [48, 12], [48, 18], [40, 18], [40, 12]]]},
                     {"building": "yes", "height": "3"}, "osm-way-kiosk")
    _scene(scene, [*_city_features(), kiosk])
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
    written = json.loads((tmp_path / "out" / "scene_detail.json").read_text())
    assert written["fidelity"]["overgrow"] == (
        "solid props, and any other prop the size of cover, append a collision box, and every procedural tree a trunk box; "
        "bottles, plants, lamps and wall props stay visual; OSM tree points stay observed"
    )
    objects = written["objects"]
    growth = [row for row in objects if row.get("type") == "overgrowth"]
    props = [row["position_m"][:2] for row in growth if row.get("asset_id")]
    # Clear of every tree (1.5 m) and wreck (3 m); a tuft of grass may lie under a prop.
    for kind, radius in (("tree", 1.5), ("wreck", 3.0)):
        assert all(math.dist(p, row["position_m"][:2]) >= radius for p in props for row in growth if row["kind"] == kind)
    assert any(math.dist(p, row["position_m"][:2]) < 0.8 for p in props for row in growth if row["kind"] == "grass")
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
    assert report["library_rejects"] == []
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
        repeat_images={"albedo": grass_png()},
    ))
    (root / "manifest.json").write_text(json.dumps({
        "assets": [{"id": "metal_trash_can", "file": "metal_trash_can.glb"}],
    }), encoding="utf-8")
    props = load_library(root)
    assert len(props) == 1
    assert abs(float(props[0].mesh.vertices[:, 2].min())) < 1e-6
    assert abs(props[0].height_m - 0.77) < 1e-6


def _with_alpha(data: bytes, mode: str) -> bytes:
    """The same GLB with every material set to ``mode``, repacked."""
    import struct

    doc, blob = read_glb(data)
    for material in doc["materials"]:
        material["alphaMode"] = mode
    chunk = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    chunk += b" " * (-len(chunk) % 4)
    return b"".join((
        struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(chunk) + 8 + len(blob)),
        struct.pack("<I4s", len(chunk), b"JSON"),
        chunk,
        struct.pack("<I4s", len(blob), b"BIN\x00"),
        blob,
    ))


def _alpha_library(root: Path) -> None:
    root.mkdir()
    mesh = box(-0.3, 0.3, -0.3, 0.3, 0.0, 0.6)
    plain = build_glb(
        [Node("albedo", (Primitive(mesh, "albedo"),), {})],
        {"albedo": Material((1, 1, 1, 1), 1)},
        repeat_images={"albedo": grass_png()},
    )
    rows = []
    for asset_id, mode in (("plain", None), ("opaque", "OPAQUE"), ("card", "BLEND"), ("cutout", "MASK")):
        (root / f"{asset_id}.glb").write_bytes(plain if mode is None else _with_alpha(plain, mode))
        rows.append({
            "id": asset_id, "file": f"{asset_id}.glb", "category": "waste", "role": "trash",
            "surfaces": ["sidewalk"], "anchor": "ground", "use": "test",
        })
    (root / "manifest.json").write_text(json.dumps({"assets": rows}), encoding="utf-8")


def test_a_transparent_material_is_rejected_with_a_reason(tmp_path: Path) -> None:
    _alpha_library(tmp_path / "library")
    props, rejects = read_library(tmp_path / "library")
    assert [prop.spec.id for prop in props] == ["plain", "opaque"]
    assert [reject.as_dict() for reject in rejects] == [
        {"asset_id": "card", "reason": "alpha_mode_blend"},
        {"asset_id": "cutout", "reason": "alpha_mode_mask"},
    ]
    assert [prop.spec.id for prop in load_library(tmp_path / "library")] == ["plain", "opaque"]
    assert read_library(None) == ([], [])


def test_the_overgrow_report_lists_library_rejections(tmp_path: Path) -> None:
    scene = tmp_path / "geo_scene.json"
    _scene(scene, _city_features())
    _base(tmp_path / "base")
    _alpha_library(tmp_path / "library")
    report = overgrow(tmp_path / "base", scene, tmp_path / "out", zone=square_zone(80), library=tmp_path / "library")
    assert report["library_assets"] == 2
    assert report["library_rejects"] == [
        {"asset_id": "card", "reason": "alpha_mode_blend"},
        {"asset_id": "cutout", "reason": "alpha_mode_mask"},
    ]
    assert not {"card", "cutout"} & set(report["placed_assets"])


def _flat_vegetation_library(root: Path) -> None:
    root.mkdir()
    rows = []
    for asset_id, height, category in (
        ("moss_patch", 0.04, "vegetation"),
        ("grass_slope", 1.6, "vegetation"),
        ("just_over", 0.06, "vegetation"),
        ("drain_plate", 0.3, "barriers"),
    ):
        mesh = box(-1.0, 1.0, -1.0, 1.0, 0.0, height)
        (root / f"{asset_id}.glb").write_bytes(build_glb(
            [Node("albedo", (Primitive(mesh, "albedo"),), {})],
            {"albedo": Material((1, 1, 1, 1), 1)},
            repeat_images={"albedo": grass_png()},
        ))
        rows.append({
            "id": asset_id, "file": f"{asset_id}.glb", "category": category, "role": "plant",
            "surfaces": ["sidewalk"], "anchor": "flat", "use": "test",
        })
    (root / "manifest.json").write_text(json.dumps({"assets": rows}), encoding="utf-8")


def test_flat_vegetation_taller_than_five_centimetres_is_rejected(tmp_path: Path) -> None:
    _flat_vegetation_library(tmp_path / "library")
    props, rejects = read_library(tmp_path / "library")
    assert [prop.spec.id for prop in props] == ["moss_patch", "drain_plate"]
    assert [reject.as_dict() for reject in rejects] == [
        {"asset_id": "grass_slope", "reason": "flat_vegetation_not_flat"},
        {"asset_id": "just_over", "reason": "flat_vegetation_not_flat"},
    ]


def test_ground_vegetation_may_stand_tall() -> None:
    from onward_generator.overgrow.catalog import AssetSpec as Spec
    from onward_generator.overgrow.library import reject_reason

    tall = box(-0.5, 0.5, -0.5, 0.5, 0.0, 2.0)
    shrub = Spec("shrub", "vegetation", "bush", ("yard",), "ground", "test")
    tile = Spec("tile", "vegetation", "plant", ("yard",), "flat", "test")
    edge = box(-0.5, 0.5, -0.5, 0.5, 0.0, 0.05)
    assert reject_reason(shrub, {"materials": [{}]}, tall) is None
    assert reject_reason(tile, {"materials": [{}]}, tall) == "flat_vegetation_not_flat"
    assert reject_reason(tile, {"materials": [{}]}, edge) is None
    assert reject_reason(tile, {"materials": [{"alphaMode": "BLEND"}]}, edge) == "alpha_mode_blend"


def _cover_library(root: Path, *, surfaces: list[str]) -> None:
    root.mkdir()
    _prop_glb(root / "cover.glb", "cover", length=0.7, width=0.7, height=0.06)
    (root / "manifest.json").write_text(json.dumps({"assets": [{
        "id": "cover", "file": "cover.glb", "category": "barriers", "role": "manhole",
        "surfaces": surfaces, "anchor": "flat", "lane": "allow", "use": "test",
    }]}), encoding="utf-8")


def _street() -> tuple[STRtree, list[LineString], STRtree]:
    buildings = STRtree([Polygon([(12, 10), (32, 10), (32, 28), (12, 28)])])
    lanes = [LineString([(-60, 0), (60, 0)])]
    return buildings, lanes, STRtree(lanes)


def _covers(tmp_path: Path, **kwargs: object) -> tuple[int, Dressing]:
    from onward_generator.overgrow.dressing import sidewalk_covers

    if not (tmp_path / "covers").exists():
        _cover_library(tmp_path / "covers", surfaces=["carriageway", "sidewalk"])
    buildings, lanes, lane_tree = _street()
    options: dict = {
        "props": load_library(tmp_path / "covers"), "profile": profile_for(100), "seed": 48152,
        "budget": 60_000, "used": [], "zone": square_zone(80),
    }
    options.update(kwargs)
    result = Dressing()
    placed = sidewalk_covers(
        options["props"], lanes, buildings, lane_tree, options["zone"], options["profile"], options["seed"], [],
        options["budget"], result, used=options["used"], seen=set(),
    )
    return placed, result


def test_sidewalk_covers_land_on_the_sidewalk_band_at_irregular_spacing(tmp_path: Path) -> None:
    placed, result = _covers(tmp_path)
    assert placed == len(result.records) == 6
    points = [row["position_m"] for row in result.records]
    for row in result.records:
        assert row["surface"] == "sidewalk"
        assert row["origin"] == "generated"
        assert row["asset_id"] == "cover"
        assert row["role"] == "manhole"
        assert 4.2 + 2.2 <= abs(row["position_m"][1]) < 4.2 + 6.5
        assert not (12 - 0.95 <= row["position_m"][0] <= 32 + 0.95 and row["position_m"][1] > 10 - 0.95)
    for index, first in enumerate(points):
        for second in points[index + 1:]:
            assert float(np.hypot(first[0] - second[0], first[1] - second[1])) >= 8.0
    along = sorted(point[0] for point in points)
    steps = {round(b - a, 1) for a, b in zip(along, along[1:], strict=False)}
    assert len(steps) > 1
    assert len({round(abs(point[1]), 2) for point in points}) == len(points)
    assert result.nodes[0].primitives and not any(node.primitives for node in result.nodes[1:])
    assert [node.extras["surface"] for node in result.nodes] == ["sidewalk"] * 6


def test_sidewalk_covers_are_seeded(tmp_path: Path) -> None:
    _placed, first = _covers(tmp_path)
    _placed, again = _covers(tmp_path)
    _placed, other = _covers(tmp_path, seed=7)
    assert [row["position_m"] for row in first.records] == [row["position_m"] for row in again.records]
    assert [row["position_m"] for row in first.records] != [row["position_m"] for row in other.records]


def test_sidewalk_covers_follow_the_cap_budget_and_the_catalogue(tmp_path: Path) -> None:
    assert _covers(tmp_path, profile=profile_for(10))[0] == scaled_cap(6, profile_for(10).props) == 2
    placed, result = _covers(tmp_path, budget=12 * 3)
    assert placed == 3
    assert result.triangles == 36
    assert result.reject_counts["budget"] == 1
    assert {(row["asset_id"], row["kind"]) for row in result.rejects if row["reason"] == "budget"} == {("cover", "barriers")}
    assert _covers(tmp_path, props=[])[0] == 0
    _cover_library(tmp_path / "lane_only", surfaces=["carriageway"])
    assert _covers(tmp_path, props=load_library(tmp_path / "lane_only"))[0] == 0
    assert _covers(tmp_path, zone=radius_zone(1.0))[1].reject_counts["outside_zone"] > 0


def test_sidewalk_covers_keep_clear_of_props_already_placed(tmp_path: Path) -> None:
    _placed, free = _covers(tmp_path)
    taken = [(row["position_m"][0], row["position_m"][1], 3.0) for row in free.records]
    _placed, result = _covers(tmp_path, used=list(taken))
    assert result.reject_counts["too_close"] >= 6
    for row in result.records:
        for x, y, radius in taken:
            assert float(np.hypot(row["position_m"][0] - x, row["position_m"][1] - y)) >= radius


def test_a_cover_is_refused_off_the_sidewalk_and_near_a_wall(tmp_path: Path) -> None:
    from onward_generator.overgrow.dressing import Spot, _cover_reason

    _cover_library(tmp_path / "covers", surfaces=["carriageway", "sidewalk"])
    cover = load_library(tmp_path / "covers")[0]
    buildings, lanes, lane_tree = _street()
    zone = square_zone(40)

    def reason(x: float, y: float, clearance: float = 20.0) -> str | None:
        spot = Spot("sidewalk", x, y, 0.0, "k", "lane-0", clearance)
        return _cover_reason(cover, spot, zone, lane_tree, lanes, [], [])

    assert reason(0.0, 8.0) is None
    assert reason(0.0, 4.2 + 2.2) is None
    assert reason(0.0, 4.2 + 2.19) == "not_sidewalk"
    assert reason(0.0, 4.2 + 6.5) == "not_sidewalk"
    assert reason(0.0, 4.2 + 6.49) is None
    assert reason(0.0, -8.0) is None
    assert reason(0.0, 8.0, clearance=0.6 + 0.35 + 0.01) is None
    assert reason(0.0, 8.0, clearance=0.6 + 0.35 - 0.01) == "too_close_to_building"
    assert reason(50.0, 8.0) == "outside_zone"


def test_overgrow_places_sidewalk_covers_away_from_wrecks_and_trees(tmp_path: Path) -> None:
    scene = tmp_path / "geo_scene.json"
    _scene(scene, _city_features())
    _base(tmp_path / "base")
    library = tmp_path / "library"
    _library(library)
    manifest = json.loads((library / "manifest.json").read_text(encoding="utf-8"))
    for row in manifest["assets"]:
        if row["id"] == "synth_hole":
            row["surfaces"] = ["carriageway", "sidewalk"]
    (library / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    report = overgrow(tmp_path / "base", scene, tmp_path / "out", zone=square_zone(80), library=library, seed=48152)
    objects = json.loads((tmp_path / "out" / "scene_detail.json").read_text())["objects"]
    covers = [row for row in objects if row.get("asset_id") == "synth_hole" and row.get("surface") == "sidewalk"]
    assert 1 <= len(covers) <= 6
    others = [row for row in objects if row.get("type") == "overgrowth" and row.get("kind") in {"wreck", "tree"}]
    assert others
    for cover in covers:
        assert cover["origin"] == "generated"
        assert cover["source_ids"] == ["synth_hole"]
        for row in others:
            gap = float(np.hypot(cover["position_m"][0] - row["position_m"][0], cover["position_m"][1] - row["position_m"][1]))
            assert gap >= (3.0 if row["kind"] == "wreck" else 1.5)
    assert "synth_hole" in report["placed_assets"]


def test_sidewalk_covers_are_pinned_for_the_default_seed(tmp_path: Path) -> None:
    _placed, result = _covers(tmp_path)
    assert [row["position_m"] for row in result.records] == [
        [-14.832, -8.543, 0.0], [-5.936, 9.413, 0.0], [19.788, -9.112, 0.0],
        [-35.346, -8.571, 0.0], [-25.824, 7.359, 0.0], [44.303, 7.746, 0.0],
    ]
    cosines = [round(float(node.matrix[0][0]), 4) for node in result.nodes if node.matrix is not None]
    assert cosines == [0.7782, 0.1043, -0.9159, 0.9702, -0.941, 0.6077]


def _two_covers(root: Path) -> list:
    """Two cover models, listed out of name order, of different sizes."""
    root.mkdir(parents=True)
    _prop_glb(root / "cover_b.glb", "cover_b", length=0.9, width=0.9, height=0.05)
    _prop_glb(root / "cover_a.glb", "cover_a", length=0.6, width=0.6, height=0.05)
    rows = [
        {"id": asset_id, "file": f"{asset_id}.glb", "category": "barriers", "role": "manhole",
         "surfaces": ["sidewalk"], "anchor": "flat", "lane": "allow", "use": "test"}
        for asset_id in ("cover_b", "cover_a")
    ]
    (root / "manifest.json").write_text(json.dumps({"assets": rows}), encoding="utf-8")
    return load_library(root)


def _diagonal(tmp_path: Path, *, spawns: list | None = None, used: list | None = None) -> Dressing:
    from onward_generator.overgrow.dressing import sidewalk_covers
    from onward_generator.overgrow.ground import RoadFloor

    props = _two_covers(tmp_path / "two")
    lanes = [LineString([(-40, -30), (40, 30)])]
    buildings = STRtree([Polygon([(-10, 8), (0, 8), (0, 18), (-10, 18)])])
    floor = RoadFloor(STRtree([Polygon([(-60, -60), (0, -60), (0, 60), (-60, 60)])]), -0.25)
    result = Dressing()
    sidewalk_covers(
        props, lanes, buildings, STRtree(lanes), square_zone(80), profile_for(100), 48152, spawns or [],
        60_000, result, used=used if used is not None else [], seen=set(), floor=floor,
    )
    return result


def test_covers_on_a_diagonal_street_are_pinned_for_the_default_seed(tmp_path: Path) -> None:
    result = _diagonal(tmp_path)
    assert [(row["asset_id"], row["position_m"]) for row in result.records] == [
        ("cover_a", [1.26, -9.734, 0.0]),
        ("cover_a", [29.298, 10.584, 0.0]),
        ("cover_b", [-15.135, -22.065, -0.25]),
        ("cover_b", [-17.075, -3.607, -0.25]),
        ("cover_b", [20.395, 5.481, 0.0]),
        ("cover_b", [6.899, 14.148, 0.0]),
    ]
    assert [round(float(node.matrix[0][0]), 4) for node in result.nodes if node.matrix is not None] == [
        0.7782, -0.9159, 0.9702, -0.941, 0.0882, -0.597,
    ]
    assert result.rejects == [{
        "asset_id": "cover_b", "reason": "too_close_to_building", "kind": "barriers",
        "position_m": [-2.396, 9.968, 0.0],
    }]
    assert result.reject_counts == {"too_close_to_building": 1}


def test_a_spawn_point_moves_a_cover_to_the_next_candidate(tmp_path: Path) -> None:
    result = _diagonal(tmp_path, spawns=[(1.26, -9.734)])
    assert [row["position_m"] for row in result.records][0] == [29.298, 10.584, 0.0]
    assert [row["position_m"] for row in result.records][-1] == [24.679, 28.093, 0.0]
    assert result.reject_counts == {"spawn_point": 1, "too_close_to_building": 1}


def test_cover_reason_boundaries(tmp_path: Path) -> None:
    from onward_generator.overgrow.constants import COVER_CLEAR_M, LANE_M
    from onward_generator.overgrow.dressing import Spot, _cover_reason

    root = tmp_path / "wide"
    root.mkdir()
    _prop_glb(root / "wide.glb", "wide", length=0.4, width=0.9, height=0.05)
    (root / "manifest.json").write_text(json.dumps({"assets": [{
        "id": "wide", "file": "wide.glb", "category": "barriers", "role": "manhole",
        "surfaces": ["sidewalk"], "anchor": "flat", "lane": "allow", "use": "test",
    }]}), encoding="utf-8")
    wide = load_library(root)[0]
    buildings, lanes, lane_tree = _street()
    zone = square_zone(40)

    def reason(x: float, y: float, clearance: float = 20.0, used: list | None = None, spawns: list | None = None) -> str | None:
        spot = Spot("sidewalk", x, y, 0.0, "k", "", clearance)
        return _cover_reason(wide, spot, zone, lane_tree, lanes, spawns or [], used or [])

    assert reason(0.0, LANE_M + 6.5) == "not_sidewalk"
    assert reason(0.0, -(LANE_M + 6.5)) == "not_sidewalk"
    assert reason(0.0, LANE_M + 2.2) is None
    edge = COVER_CLEAR_M + max(wide.length_m, wide.half_y * 2) * 0.5
    assert reason(0.0, 8.0, clearance=edge) is None
    assert reason(0.0, 8.0, clearance=0.9) == "too_close_to_building"
    assert reason(0.0, 8.0, used=[(3.0, 8.0, 3.0)]) is None
    assert reason(0.0, 8.0, used=[(2.6, 8.0, 3.0)]) == "too_close"
    assert reason(0.0, 8.0, used=[(0.0, 9.0, 1.2)]) == "too_close"
    assert reason(0.0, 8.0, used=[(0.0, 7.0, 1.2)]) == "too_close"
    assert reason(0.0, 8.0, spawns=[(0.0, 8.0)]) == "spawn_point"
    assert reason(0.0, 8.0, spawns=[(0.0, 13.1)]) is None
    assert reason(-50.0, 8.0) == "outside_zone"
    # A one-metre lane makes GEOS return the band edges exactly.
    short = [LineString([(-0.5, 0.0), (0.5, 0.0)])]
    for y, expected in ((LANE_M + 6.5, "not_sidewalk"), (LANE_M + 2.2, None)):
        spot = Spot("sidewalk", 0.0, y, 0.0, "k", "", 20.0)
        assert _cover_reason(wide, spot, zone, STRtree(short), short, [], []) == expected


def test_occupied_lists_each_placed_object_with_its_radius() -> None:
    from onward_generator.overgrow.place import _occupied

    rows = [
        {"kind": "grass", "position_m": [1.0, 2.0, 0.0]},
        {"kind": "tree", "position_m": [3.5, -4.0, 0.1]},
        {"kind": "wreck", "position_m": [-6.0, 7.25, -0.2]},
    ]
    assert _occupied(rows) == [(1.0, 2.0, 0.8), (3.5, -4.0, 1.5), (-6.0, 7.25, 3.0)]
    assert _occupied([]) == []


def _cover_run(tmp_path: Path, name: str, features: list[dict], *, layout: Path | None = None, road: tuple | None = None) -> list[list[float]]:
    scene = tmp_path / f"{name}.json"
    _scene(scene, features)
    base = tmp_path / f"{name}_base"
    _base(base)
    if road is not None:
        x, y = road
        cap = Mesh(
            np.array([[x - 2.0, y - 2.0, -0.2], [x + 2.0, y - 2.0, -0.2], [x, y + 2.0, -0.2]], dtype=np.float64),
            np.array([[0, 1, 2]]),
        )
        (base / "scene_visual.glb").write_bytes(build_glb(
            [Node("surface/carriageway", (Primitive(cap, "asphalt"),), {"type": "surface"})],
            {"asphalt": Material((0.2, 0.2, 0.2, 1), 1)},
        ))
    library = tmp_path / "covers"
    if not library.exists():
        _cover_library(library, surfaces=["sidewalk"])
    overgrow(base, scene, tmp_path / f"{name}_out", zone=square_zone(80), library=library, seed=48152, layout=layout)
    objects = json.loads((tmp_path / f"{name}_out" / "scene_detail.json").read_text())["objects"]
    return [row["position_m"] for row in objects if row.get("asset_id") == "cover"]


def test_overgrow_covers_avoid_trees_and_spawns_and_sit_on_the_floor(tmp_path: Path) -> None:
    features = [row for row in _city_features() if row["kind"] != "poi"]
    plain = _cover_run(tmp_path, "plain", features)
    assert plain == [
        [-4.832, -8.543, 0.0], [4.064, 9.413, 0.0], [29.788, -9.112, 0.0],
        [-25.346, -8.571, 0.0], [-15.824, 7.359, 0.0], [19.605, -7.852, 0.0],
    ]
    tree = _feature("poi", {"type": "Point", "coordinates": plain[0][:2]}, {"natural": "tree"}, "osm-node-cover-tree")
    layout = tmp_path / "layout.json"
    layout.write_text(json.dumps({"ai_m": [{"x": plain[1][0], "y": plain[1][1]}]}), encoding="utf-8")
    moved = _cover_run(tmp_path, "moved", [*features, tree], layout=layout, road=tuple(plain[2][:2]))
    assert len(moved) == 6
    for point in moved:
        assert float(np.hypot(point[0] - plain[0][0], point[1] - plain[0][1])) >= 1.5
        assert float(np.hypot(point[0] - plain[1][0], point[1] - plain[1][1])) >= 5.0
    assert [plain[2][0], plain[2][1], -0.2] in moved


def test_the_zone_is_cut_to_the_playable_core() -> None:
    from shapely import Polygon, box

    zone = square_zone(80)
    assert within_core(zone, None) is zone
    inner = square_zone(40)
    assert within_core(inner, box(-100, -100, 100, 100)) is inner
    cut = within_core(zone, box(-50, -50, 50, 50))
    assert cut.kind == "square" and cut.polygon.bounds == (-50, -50, 50, 50)
    with pytest.raises(ZoneError, match="does not overlap the playable core"):
        within_core(zone_from_polygon(box(200, 200, 260, 260)), box(-50, -50, 50, 50))
    # Two bars joined outside the core leave two pieces inside it.
    u_shape = Polygon([(-40, -100), (40, -100), (40, 100), (30, 100), (30, -90), (-30, -90), (-30, 100), (-40, 100)])
    with pytest.raises(ZoneError, match="^the zone does not overlap the playable core in one piece$"):
        within_core(zone_from_polygon(u_shape), box(-50, -50, 50, 50))


def test_overgrow_keeps_its_zone_inside_the_core(tmp_path: Path) -> None:
    scene = tmp_path / "geo_scene.json"
    _scene(scene, _city_features())
    _base(tmp_path / "base")
    manifest = tmp_path / "base" / "scene_detail.json"
    manifest.write_text(json.dumps({**json.loads(manifest.read_text(encoding="utf-8")), "core": {"half_side_m": 50.0}}),
                        encoding="utf-8")
    report = overgrow(tmp_path / "base", scene, tmp_path / "out", seed=48152, zone=square_zone(80))
    assert report["zone"]["key"] == within_core(square_zone(80), square_zone(50).polygon).stable_key()
    assert report["zone"]["key"] != square_zone(80).stable_key()
