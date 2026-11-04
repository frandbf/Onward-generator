"""Whole GLB loading: every node with its transform, every primitive, one part per albedo.

Murcia centre is the origin of the placement fixtures.
"""

from __future__ import annotations

import io
import json
import math
import struct
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image
from shapely import LineString, STRtree, box

from onward_generator.detail.modules import yaw_matrix
from onward_generator.generation.config import Material
from onward_generator.generation.gltf import Node, Primitive, build_glb, scene_primitives
from onward_generator.generation.mesh import Mesh
from onward_generator.generation.mesh import box as mesh_box
from onward_generator.overgrow.catalog import ASSETS_BY_ID, AssetSpec
from onward_generator.overgrow.dressing import dress
from onward_generator.overgrow.fit import check_scene
from onward_generator.overgrow.library import Prop, PropPart, _read, read_library
from onward_generator.overgrow.library_catalog import LibraryError
from onward_generator.overgrow.manifest import MANIFEST_VERSION, check_manifest, keep_semantics
from onward_generator.overgrow.profiles import profile_for
from onward_generator.overgrow.zone import square_zone
from tests.conftest import MURCIA_CENTER

# One triangle in the glTF frame (Y up): (0,0,0), (1,0,0), (0,1,0).
TRIANGLE = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32)
SEMANTICS = {
    "origin": "estimated", "basis": "rendered views", "reviewed_on": "2026-10-30",
    "fields": ["description", "placement", "grouping", "condition", "tags"],
}


def _png(color: tuple[int, int, int]) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (1, 1), color).save(out, format="PNG")
    return out.getvalue()


RED, BLUE = _png((255, 0, 0)), _png((0, 0, 255))


def _glb(
    meshes: list[list[dict[str, Any]]],
    nodes: list[dict[str, Any]],
    *,
    scenes: list[dict[str, Any]] | None = None,
    scene: int | None = None,
    materials: list[dict[str, Any]] | None = None,
    images: list[bytes] = (),
) -> bytes:
    """A GLB written by hand. A primitive is {positions, indices, uvs?, material?, mode?}."""
    blob = bytearray()
    views: list[dict[str, Any]] = []
    accessors: list[dict[str, Any]] = []

    def add(array: np.ndarray, kind: str, component: int) -> int:
        while len(blob) % 4:
            blob.append(0)
        views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": array.nbytes})
        blob.extend(array.tobytes())
        count = len(array) if kind != "SCALAR" else array.size
        accessors.append({"bufferView": len(views) - 1, "componentType": component, "count": count, "type": kind})
        return len(accessors) - 1

    gltf_meshes = []
    for prims in meshes:
        out = []
        for prim in prims:
            attributes = {}
            if prim.get("positions") is not None:
                attributes["POSITION"] = add(np.asarray(prim["positions"], dtype=np.float32), "VEC3", 5126)
            if prim.get("uvs") is not None:
                attributes["TEXCOORD_0"] = add(np.asarray(prim["uvs"], dtype=np.float32), "VEC2", 5126)
            entry: dict[str, Any] = {"attributes": attributes}
            if prim.get("indices") is not None:
                entry["indices"] = add(np.asarray(prim["indices"], dtype=np.uint32).reshape(-1), "SCALAR", 5125)
            for key in ("material", "mode"):
                if key in prim:
                    entry[key] = prim[key]
            out.append(entry)
        gltf_meshes.append({"name": f"mesh{len(gltf_meshes)}", "primitives": out})
    image_rows = []
    for png in images:
        while len(blob) % 4:
            blob.append(0)
        views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(png)})
        blob.extend(png)
        image_rows.append({"mimeType": "image/png", "bufferView": len(views) - 1})
    while len(blob) % 4:
        blob.append(0)
    doc: dict[str, Any] = {
        "asset": {"version": "2.0"}, "nodes": nodes, "meshes": gltf_meshes,
        "accessors": accessors, "bufferViews": views, "buffers": [{"byteLength": len(blob)}],
    }
    if scenes is not None:
        doc["scenes"] = scenes
    if scene is not None:
        doc["scene"] = scene
    if materials is not None:
        doc["materials"] = materials
    if image_rows:
        doc["images"] = image_rows
        doc["textures"] = [{"source": index} for index in range(len(image_rows))]
    text = json.dumps(doc).encode("utf-8")
    text += b" " * (-len(text) % 4)
    total = 12 + 8 + len(text) + 8 + len(blob)
    return b"".join([
        struct.pack("<4sII", b"glTF", 2, total),
        struct.pack("<I4s", len(text), b"JSON"), text,
        struct.pack("<I4s", len(blob), b"BIN\x00"), bytes(blob),
    ])


def _tri(**extra: Any) -> dict[str, Any]:
    return {"positions": TRIANGLE, "indices": [0, 1, 2], **extra}


def _local(points: np.ndarray) -> np.ndarray:
    """glTF (x, y up, z south) → local (x east, y north, z up)."""
    points = np.asarray(points, dtype=np.float64)
    return np.column_stack([points[:, 0], -points[:, 2], points[:, 1]])


def _rodrigues(axis: np.ndarray, angle: float) -> np.ndarray:
    axis = axis / np.linalg.norm(axis)
    k = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    return np.eye(3) + math.sin(angle) * k + (1 - math.cos(angle)) * (k @ k)


def test_a_node_takes_translation_rotation_and_scale_in_that_order() -> None:
    axis = np.array([1.0, 2.0, 3.0])
    angle = 1.1
    quaternion = [*(axis / np.linalg.norm(axis) * math.sin(angle / 2)), math.cos(angle / 2)]
    node = {"mesh": 0, "translation": [5.0, -2.0, 0.5], "rotation": quaternion, "scale": [2.0, 3.0, 0.5]}
    # Every vertex has a component on every axis, so each entry of R·S shows.
    solid = np.array([[1.0, 2.0, 3.0], [-2.0, 0.5, 1.0], [0.3, -1.0, 2.0]], dtype=np.float32)
    data = _glb([[{"positions": solid, "indices": [0, 1, 2]}]], [node], scenes=[{"nodes": [0]}])
    [prim] = scene_primitives(data)
    expected = solid.astype(np.float64) * [2.0, 3.0, 0.5] @ _rodrigues(axis, angle).T + [5.0, -2.0, 0.5]
    assert np.allclose(prim.mesh.vertices, _local(expected))
    assert prim.mesh.triangles.tolist() == [[0, 1, 2]]


def test_a_child_inherits_its_parent_and_every_primitive_is_read() -> None:
    quarter = [0.0, math.sin(math.pi / 4), 0.0, math.cos(math.pi / 4)]  # 90° about glTF up
    nodes = [
        {"translation": [10.0, 0.0, 0.0], "children": [1, 2]},
        {"mesh": 0, "rotation": quarter},
        {"mesh": 1, "matrix": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 4, 0, 1]},
    ]
    data = _glb(
        [[_tri(material=0), _tri(material=1)], [_tri()]], nodes,
        scenes=[{"nodes": [0]}], materials=[{"name": "metal"}, {}],
    )
    prims = scene_primitives(data)
    assert [p.material for p in prims] == ["metal", "mat_1", "metal"]
    assert all(p.uvs is None for p in prims)
    turned = TRIANGLE.astype(np.float64) @ _rodrigues(np.array([0.0, 1.0, 0.0]), math.pi / 2).T + [10.0, 0.0, 0.0]
    assert np.allclose(prims[0].mesh.vertices, _local(turned))
    assert np.allclose(prims[1].mesh.vertices, _local(turned))
    assert np.allclose(prims[2].mesh.vertices, _local(TRIANGLE + [10.0, 4.0, 0.0]))


def test_the_default_scene_and_the_root_nodes_are_found() -> None:
    nodes = [{"mesh": 0, "translation": [1.0, 0.0, 0.0]}, {"mesh": 0, "translation": [2.0, 0.0, 0.0]}]
    two_scenes = _glb([[_tri()]], nodes, scenes=[{"nodes": [0]}, {"nodes": [1]}], scene=1)
    [prim] = scene_primitives(two_scenes)
    assert prim.mesh.vertices[0, 0] == pytest.approx(2.0)
    [first] = scene_primitives(_glb([[_tri()]], nodes, scenes=[{"nodes": [0]}, {"nodes": [1]}]))
    assert first.mesh.vertices[0, 0] == pytest.approx(1.0)
    # Without scenes, every node that is nobody's child is a root, so the shared mesh is drawn per root.
    loose = [{"mesh": 0}, {"children": [2], "translation": [0.0, 7.0, 0.0]}, {"mesh": 0}]
    prims = scene_primitives(_glb([[_tri()]], loose))
    assert [round(float(p.mesh.vertices[:, 2].min()), 6) for p in prims] == [0.0, 7.0]
    assert scene_primitives(_glb([[_tri()]], [{"mesh": 0}], scenes=[{}])) == []


def test_a_node_reached_twice_is_an_error() -> None:
    nodes = [{"children": [2]}, {"children": [2]}, {"mesh": 0}]
    with pytest.raises(ValueError, match=r"^node 2: reached twice in the scene graph$"):
        scene_primitives(_glb([[_tri()]], nodes, scenes=[{"nodes": [0, 1]}]))


def test_only_indexed_triangles_are_read() -> None:
    prims = [
        _tri(mode=1),
        {"positions": TRIANGLE},
        {"positions": None, "indices": [0, 1, 2]},
        _tri(mode=4, uvs=[[0, 0], [1, 0], [0, 1]]),
    ]
    [prim] = scene_primitives(_glb([prims], [{"mesh": 0}], scenes=[{"nodes": [0]}]))
    assert prim.uvs is not None and prim.uvs.tolist() == [[0, 0], [1, 0], [0, 1]]
    assert prim.uvs.dtype == np.float32 and prim.mesh.vertices.dtype == np.float64
    assert prim.material == "ground"


def test_a_mirroring_scale_keeps_the_faces_outward() -> None:
    data = _glb([[_tri()]], [{"mesh": 0, "scale": [-1.0, 1.0, 1.0]}], scenes=[{"nodes": [0]}])
    [prim] = scene_primitives(data)
    assert prim.mesh.triangles.tolist() == [[2, 1, 0]]


def _row(**changes: Any) -> dict[str, Any]:
    row = {
        "id": "bench", "file": "bench.glb", "category": "furniture", "role": "bench",
        "surfaces": ["sidewalk"], "anchor": "ground", "lane": "forbid", "clearance_m": 0.0, "use": "test bench",
        "description": "A test bench on the sidewalk.", "placement": "kerb", "grouping": "single",
        "condition": "worn", "tags": ["bench"],
    }
    row.update(changes)
    return row


def _library(root: Path, rows: list[dict[str, Any]], files: dict[str, bytes]) -> None:
    root.mkdir()
    for name, data in files.items():
        (root / name).write_bytes(data)
    document = {"schema": "onward_generator.abandoned_prefabs", "schema_version": MANIFEST_VERSION,
                "semantics": SEMANTICS, "assets": rows}
    (root / "manifest.json").write_text(json.dumps(document), encoding="utf-8")


def _two_material_glb(*, same_image: bool = False) -> bytes:
    """Legs (metal) at the origin; a seat (wood) translated by a child node; a second metal leg."""
    textured = [
        {"name": "metal", "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}},
        {"name": "wood", "pbrMetallicRoughness": {"baseColorTexture": {"index": 0 if same_image else 1}}},
    ]
    seat = {"positions": [[0, 0, 0], [2, 0, 0], [2, 0, -1], [0, 0, -1]], "indices": [0, 1, 2, 0, 2, 3],
            "uvs": [[0, 0], [1, 0], [1, 1], [0, 1]], "material": 1}
    leg = {"positions": [[0, 0, 0], [0.1, 0, 0], [0, 0.5, 0]], "indices": [0, 1, 2], "material": 0}
    other_leg = {**leg, "positions": [[2, 0, 0], [1.9, 0, 0], [2, 0.5, 0]], "indices": [0, 2, 1]}
    nodes = [{"mesh": 0, "children": [1]}, {"mesh": 1, "translation": [0, 0.5, 0]}]
    return _glb([[leg, other_leg], [seat]], nodes, scenes=[{"nodes": [0]}], materials=textured, images=[RED, BLUE])


def test_the_library_loads_every_primitive_as_one_part_per_albedo(tmp_path: Path) -> None:
    _library(tmp_path / "lib", [_row()], {"bench.glb": _two_material_glb()})
    [prop], rejects = read_library(tmp_path / "lib")
    assert rejects == []
    assert prop.triangles == 4
    assert [(part.end, part.albedo) for part in prop.parts] == [(2, RED), (4, BLUE)]
    assert prop.albedo == RED
    assert (round(prop.length_m, 6), round(prop.half_y * 2, 6), round(prop.height_m, 6)) == (2.0, 1.0, 0.5)
    legs, seat = prop.primitives()
    assert (legs.material, seat.material) == ("prop_bench", "prop_bench__1")
    assert legs.mesh.triangles.tolist() == [[0, 1, 2], [3, 5, 4]]
    assert legs.uvs is not None and legs.uvs.tolist() == [[0, 0]] * 6
    assert prop.uvs.dtype == np.float32
    assert seat.mesh.triangles.tolist() == [[0, 1, 2], [0, 2, 3]]
    # The file's glTF V (down from the top) is turned into the generator's V (up from the bottom).
    assert seat.uvs is not None and seat.uvs.tolist() == [[0, 1], [1, 1], [1, 0], [0, 0]]
    assert np.allclose(seat.mesh.vertices[:, 2], 0.5)
    assert prop.images() == {"prop_bench": RED, "prop_bench__1": BLUE}


def test_primitives_that_share_an_albedo_are_one_part(tmp_path: Path) -> None:
    _library(tmp_path / "lib", [_row()], {"bench.glb": _two_material_glb(same_image=True)})
    [prop], _rejects = read_library(tmp_path / "lib")
    assert [(part.end, part.albedo) for part in prop.parts] == [(4, RED)]
    [whole] = prop.primitives()
    assert whole.mesh.triangle_count == 4 and whole.material == "prop_bench"
    assert prop.images() == {"prop_bench": RED}


def test_yaw_turns_the_long_side_and_refused_rows_are_rejected(tmp_path: Path) -> None:
    long_on_y = _glb([[{"positions": [[0, 0, 0], [1, 0, 0], [0, 0, -4], [0, 2, 0]],
                        "indices": [0, 1, 2, 0, 2, 3]}]], [{"mesh": 0}], scenes=[{"nodes": [0]}])
    rows = [
        _row(id="truck", file="truck.glb", yaw_deg=90),
        _row(id="plain", file="truck.glb"),
        _row(id="tiny", file="truck.glb", refused="unknown_scale"),
        _row(id="flipped", file="truck.glb", yaw_deg=180),
    ]
    _library(tmp_path / "lib", rows, {"truck.glb": long_on_y})
    props, rejects = read_library(tmp_path / "lib")
    assert [reject.as_dict() for reject in rejects] == [{"asset_id": "tiny", "reason": "unknown_scale"}]
    by_id = {prop.spec.id: prop for prop in props}
    assert (by_id["plain"].length_m, by_id["plain"].half_y * 2) == pytest.approx((1.0, 4.0))
    assert (by_id["truck"].length_m, by_id["truck"].half_y * 2) == pytest.approx((4.0, 1.0))
    assert by_id["truck"].height_m == pytest.approx(2.0)
    assert np.allclose(by_id["truck"].mesh.vertices, [[0, 0, 0], [0, 1, 0], [-4, 0, 0], [0, 0, 2]])
    assert by_id["truck"].mesh.triangles.tolist() == [[0, 1, 2], [0, 2, 3]]
    # A quarter turn counter-clockwise puts the file's +Y (north) on -X.
    assert by_id["truck"].mesh.vertices[:, 0].min() == pytest.approx(-4.0)
    assert by_id["truck"].mesh.vertices[:, 1].max() == pytest.approx(1.0)
    assert by_id["flipped"].mesh.vertices[:, 0].min() == pytest.approx(-1.0)
    assert by_id["flipped"].mesh.vertices[:, 1].min() == pytest.approx(-4.0)


def test_a_glb_without_triangles_is_skipped(tmp_path: Path) -> None:
    empty = _glb([[{"positions": TRIANGLE, "indices": np.zeros(0, dtype=np.uint32)}]], [{"mesh": 0}],
                 scenes=[{"nodes": [0]}])
    _library(tmp_path / "lib", [_row(file="empty.glb")], {"empty.glb": empty})
    assert read_library(tmp_path / "lib") == ([], [])
    spec = AssetSpec("bench", "furniture", "bench", ("sidewalk",), "ground", "test bench")
    with pytest.raises(ValueError, match=r"^\S*empty\.glb: no mesh$"):
        _read(tmp_path / "lib" / "empty.glb", spec, 0.0)
    # An empty primitive before a real one is passed over, not the end of the file.
    mixed = _glb([[{"positions": TRIANGLE, "indices": np.zeros(0, dtype=np.uint32)}, _tri()]], [{"mesh": 0}],
                 scenes=[{"nodes": [0]}])
    (tmp_path / "mixed.glb").write_bytes(mixed)
    assert _read(tmp_path / "mixed.glb", spec, 0.0).triangles == 1


def test_the_manifest_checks_and_keeps_the_load_fields() -> None:
    document = {"schema": "onward_generator.abandoned_prefabs", "schema_version": MANIFEST_VERSION,
                "semantics": SEMANTICS, "assets": [_row(yaw_deg=90, refused="unknown_scale")]}
    check_manifest(document, "m")
    for bad in ({"yaw_deg": 45}, {"refused": "ugly"}):
        with pytest.raises(LibraryError, match="m/assets/0/"):
            check_manifest({**document, "assets": [_row(**bad)]}, "m")
    fresh = {key: value for key, value in _row(use="again").items()
             if key not in ("description", "placement", "grouping", "condition", "tags")}
    [row] = keep_semantics({"schema": "s", "assets": [fresh]}, document)["assets"]
    assert row["yaw_deg"] == 90 and row["refused"] == "unknown_scale"
    keys = list(row)
    assert keys[keys.index("use") + 6:keys.index("use") + 8] == ["yaw_deg", "refused"]


def _two_part(spec: AssetSpec, first: Mesh, second: Mesh) -> Prop:
    vertices = np.vstack([first.vertices, second.vertices])
    triangles = np.vstack([first.triangles, second.triangles + first.vertex_count])
    uvs = np.zeros((len(vertices), 2), dtype=np.float32)
    parts = (PropPart(first.triangle_count, RED), PropPart(first.triangle_count + second.triangle_count, BLUE))
    return Prop(spec, Mesh(vertices, triangles), uvs, RED, parts=parts)


def test_a_scaled_wreck_keeps_every_part_when_it_is_placed() -> None:
    spec = AssetSpec("big_wreck", "vehicles", "wreck", ("curb",), "ground", "big wreck")
    body = mesh_box(-10.0, 10.0, -6.0, 6.0, 0.0, 4.0)
    roof = mesh_box(-5.0, 5.0, -3.0, 3.0, 4.0, 7.0)
    lane = LineString([(-30.0, 0.0), (80.0, 0.0)])
    planted = dress(
        [_two_part(spec, body, roof)], [], [], STRtree([box(0.0, 30.0, 20.0, 50.0)]), [lane], STRtree([lane]),
        square_zone(80.0), profile_for(100), 48152, [], grass_mesh=None, grass_left=0, triangle_budget=60_000,
    )
    first = planted.nodes[0]
    assert [prim.material for prim in first.primitives] == ["prop_big_wreck", "prop_big_wreck__1"]
    assert [prim.mesh.triangle_count for prim in first.primitives] == [12, 12]
    assert planted.images == {"prop_big_wreck": RED, "prop_big_wreck__1": BLUE}
    scale = planted.records[0]["fit_scale"]
    assert scale < 1.0
    assert float(first.primitives[1].mesh.vertices[:, 2].max()) == pytest.approx(7.0 * scale)
    assert planted.triangles == 24 * len(planted.records)
    assert MURCIA_CENTER == (-1.12863, 37.98407)


def test_a_planned_cover_keeps_every_part() -> None:
    from onward_generator.overgrow.digest import build_digest
    from onward_generator.overgrow.plan import parse_overgrow_plan
    from onward_generator.overgrow.realize import realize
    from tests.unit.test_overgrow_plan import _block_features, _scene, _site

    features = _block_features()
    digest = build_digest(_scene(features, longitude=MURCIA_CENTER[0], latitude=MURCIA_CENTER[1], crs="EPSG:25830"))
    plan = parse_overgrow_plan(
        '{"plan_version":"0.1.0","mood":"wall","actions":[{"op":"cover","asset_id":"modular_fire_escape",'
        '"edge_id":"facade:osm-way-left:0:0"}]}'
    )
    escape = _two_part(
        ASSETS_BY_ID["modular_fire_escape"],
        mesh_box(-1.0, 1.0, -1.0, 1.0, 0.0, 1.0), mesh_box(-1.0, 1.0, -1.0, 1.0, 1.0, 3.0),
    )
    dressing = realize(plan, digest, [escape], _site(features))
    assert dressing.records and all(row["fit_scale"] < 1.0 for row in dressing.records)
    first = dressing.nodes[0]
    assert [prim.material for prim in first.primitives] == ["prop_modular_fire_escape", "prop_modular_fire_escape__1"]
    assert dressing.images == {"prop_modular_fire_escape": RED, "prop_modular_fire_escape__1": BLUE}
    assert len(dressing.nodes) > 1 and all(node.primitives == () for node in dressing.nodes[1:])
    assert float(first.primitives[1].mesh.vertices[:, 2].max()) == pytest.approx(3.0 * dressing.records[0]["fit_scale"])


def test_fit_measures_every_primitive_of_a_placed_prop(tmp_path: Path) -> None:
    scene = tmp_path / "geo_scene.json"
    scene.write_text(json.dumps({"features": [
        {"kind": "highway", "tags": {"highway": "residential"},
         "geometry": {"type": "LineString", "coordinates": [[-40, 0], [40, 0]]}},
    ]}), encoding="utf-8")
    base = tmp_path / "scene_overgrow"
    base.mkdir()
    # Each part alone is car-sized; together they are 9 m long, which no wreck may be.
    front = mesh_box(-4.5, 0.0, -0.8, 0.8, 0.0, 1.5)
    back = mesh_box(0.0, 4.5, -0.8, 0.8, 0.0, 1.5)
    material = {"a": Material((1, 1, 1, 1), 0.9), "b": Material((1, 1, 1, 1), 0.9)}
    nodes = [
        Node("overgrow/prop/w/0", (Primitive(front, "a"), Primitive(back, "b")),
             {"kind": "vehicles", "role": "wreck"}, yaw_matrix(0.0, 0.9, 0.0, 0.0), "prop:w"),
        Node("overgrow/prop/w/1", (), {"kind": "vehicles", "role": "wreck"}, yaw_matrix(10.0, 0.9, 0.0, 0.0), "prop:w"),
    ]
    (base / "scene_visual.glb").write_bytes(build_glb(nodes, material))
    checked, problems = check_scene(base, scene)
    assert checked == 2
    assert len(problems) == 2 and all("wrong_size 9.00x1.60x1.50" in problem for problem in problems)


def test_fit_reports_a_placed_node_without_triangles(tmp_path: Path) -> None:
    scene = tmp_path / "geo_scene.json"
    scene.write_text(json.dumps({"features": []}), encoding="utf-8")
    base = tmp_path / "scene_overgrow"
    base.mkdir()
    lines_only = _glb([[_tri(mode=1)], [_tri()]], [
        {"name": "overgrow/wreck/0", "mesh": 0},
        {"name": "overgrow/wreck/1", "mesh": 1, "translation": [0.0, 0.0, 30.0]},
    ], scenes=[{"nodes": [0, 1]}])
    (base / "scene_visual.glb").write_bytes(lines_only)
    checked, problems = check_scene(base, scene)
    assert checked == 2
    assert problems[0] == "overgrow/wreck/0 missing_mesh"
    assert problems[1].startswith("overgrow/wreck/1 wrong_size")


def test_a_blocking_wreck_stands_in_the_lane() -> None:
    spec = AssetSpec("car", "vehicles", "wreck", ("curb",), "ground", "car")
    car = mesh_box(-2.1, 2.1, -0.9, 0.9, 0.0, 1.5)
    lane = LineString([(-30.0, 0.0), (120.0, 0.0)])
    planted = dress(
        [Prop(spec, car, np.zeros((car.vertex_count, 2), dtype=np.float32), RED)],
        [], [], STRtree([box(0.0, 30.0, 20.0, 50.0)]), [lane], STRtree([lane]), square_zone(80.0),
        profile_for(100), 48152, [], grass_mesh=None, grass_left=0, triangle_budget=60_000, block_streets=True,
    )
    assert abs(planted.records[0]["position_m"][1]) == pytest.approx(0.9)
