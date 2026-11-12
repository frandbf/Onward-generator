"""Load-time level of detail of library props: a size-based triangle target, a bounding-box guard, provenance.

Placement fixtures use local metres from MURCIA_CENTER (x east, y north, z up).
"""

from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from onward_generator.generation.config import Material
from onward_generator.generation.gltf import Node, Primitive, build_glb, nodes_from_glb
from onward_generator.generation.mesh import Mesh, box, merge
from onward_generator.overgrow import library
from onward_generator.overgrow.catalog import ASSETS_BY_ID, AssetSpec
from onward_generator.overgrow.constants import LOD_BOX_TOLERANCE_M, LOD_MIN_TRIS, TARGET_ASSET_TRIS
from onward_generator.overgrow.digest import build_digest
from onward_generator.overgrow.library import Prop, PropPart, box_shift, level_of_detail, lod_target, read_library
from onward_generator.overgrow.place import overgrow
from onward_generator.overgrow.plan import parse_overgrow_plan
from onward_generator.overgrow.realize import realize
from onward_generator.overgrow.zone import square_zone
from tests.conftest import MURCIA_CENTER
from tests.unit.test_abandoned_city import _base, _city_features, _library, _scene
from tests.unit.test_overgrow_plan import _block_features, _props, _site
from tests.unit.test_overgrow_plan import _scene as _plan_scene

PNG_A = b"\x89PNG\r\n\x1a\nA"
PNG_B = b"\x89PNG\r\n\x1a\nB"
BARREL = AssetSpec("synth_barrel", "waste", "crate", ("sidewalk",), "ground", "test")  # a crate band holds the 0.5 m test meshes at scale 1
WRECK = AssetSpec("synth_wreck", "waste", "wreck", ("curb",), "ground", "test")  # the wreck fit scale, not a vehicle: vehicles keep their mesh
CAR = AssetSpec("synth_car", "vehicles", "wreck", ("curb",), "ground", "test")


def _sphere(radius: float, rings: int, segments: int, centre_z: float | None = None) -> Mesh:
    """A UV sphere resting on z = 0 (or centred at ``centre_z``): 2·segments·(rings − 1) triangles."""
    z0 = radius if centre_z is None else centre_z
    points = [(0.0, 0.0, z0 + radius)]
    for ring in range(1, rings):
        polar = math.pi * ring / rings
        for seg in range(segments):
            azimuth = 2 * math.pi * seg / segments
            points.append((radius * math.sin(polar) * math.cos(azimuth), radius * math.sin(polar) * math.sin(azimuth),
                           z0 + radius * math.cos(polar)))
    points.append((0.0, 0.0, z0 - radius))
    bottom = len(points) - 1
    triangles = []
    for seg in range(segments):
        nxt = (seg + 1) % segments
        triangles.append((0, 1 + seg, 1 + nxt))
        last = 1 + (rings - 2) * segments
        triangles.append((bottom, last + nxt, last + seg))
    for ring in range(rings - 2):
        for seg in range(segments):
            nxt = (seg + 1) % segments
            a, b = 1 + ring * segments + seg, 1 + ring * segments + nxt
            c, d = a + segments, b + segments
            triangles.extend([(a, c, d), (a, d, b)])
    return Mesh(np.array(points, dtype=np.float64), np.array(triangles, dtype=np.int64))


def _prop(mesh: Mesh, spec: AssetSpec = BARREL, parts: tuple[PropPart, ...] = ()) -> Prop:
    uvs = np.column_stack([mesh.vertices[:, 0], mesh.vertices[:, 1]]).astype(np.float32)
    return Prop(spec, mesh, uvs, PNG_A, parts=parts)


@pytest.mark.parametrize(("size", "expected"), [
    ((0.7, 0.7, 0.068), LOD_MIN_TRIS),       # flat: a manhole cover
    ((2.0, 2.0, 0.099), LOD_MIN_TRIS),       # flat however wide
    ((0.3, 0.3, 0.1), LOD_MIN_TRIS),         # 0.09 m² × 600 = 54, raised to the minimum
    ((2.0, 2.0, 0.1), TARGET_ASSET_TRIS),    # exactly 0.1 m tall is not flat
    ((0.6, 0.6, 0.9), 324),                  # largest face 0.6 × 0.9
    ((1.0, 0.5, 0.4), 300),                  # largest face 1.0 × 0.5
    ((0.3, 1.0, 0.8), 480),                  # largest face 1.0 × 0.8 (depth × height)
    ((4.3, 1.8, 1.4), TARGET_ASSET_TRIS),    # a car keeps the normalized cap
])
def test_lod_target_follows_the_largest_box_face(size: tuple[float, float, float], expected: int) -> None:
    length, depth, height = size
    assert lod_target(box(0, length, 0, depth, 0, height)) == expected


def test_lod_target_is_measured_at_the_street_scale() -> None:
    small = box(0, 0.5, 0, 0.02, 0, 0.05)
    assert lod_target(small) == LOD_MIN_TRIS
    # Ten times larger: a 5.0 × 0.5 m face, 0.5 m tall, no longer flat.
    assert lod_target(small, 10.0) == 1500


def test_box_shift_is_the_largest_face_move() -> None:
    a = box(0, 1, 0, 1, 0, 1)
    assert box_shift(a, a) == 0.0
    assert box_shift(a, box(0.1, 1, 0, 1, 0, 1)) == pytest.approx(0.1)
    assert box_shift(a, box(0, 1, 0, 1.3, 0, 1)) == pytest.approx(0.3)
    assert box_shift(a, box(0, 1, 0, 1, -0.2, 0.95)) == pytest.approx(0.2)


def test_a_prop_within_its_target_is_returned_as_is() -> None:
    prop = _prop(box(0, 0.5, 0, 0.5, 0, 0.5))
    assert level_of_detail(prop) is prop
    assert prop.lod() == {}


def test_a_dense_barrel_is_reduced_on_load_and_keeps_its_box() -> None:
    prop = _prop(_sphere(0.3, 30, 48))
    reduced = level_of_detail(prop)
    assert prop.triangles == 2 * 48 * 29
    assert reduced.triangles * 4 <= prop.triangles * 3
    assert reduced.triangles < prop.triangles // 2
    assert reduced.source_triangles == prop.triangles
    assert reduced.lod() == {"source_triangles": prop.triangles, "triangles": reduced.triangles}
    assert box_shift(prop.mesh, reduced.mesh) <= LOD_BOX_TOLERANCE_M
    assert float(reduced.mesh.vertices[:, 2].min()) == 0.0
    assert len(reduced.uvs) == reduced.mesh.vertex_count and reduced.uvs.dtype == np.float32
    assert reduced.parts == (PropPart(reduced.triangles, PNG_A),)
    again = level_of_detail(prop)
    assert np.array_equal(again.mesh.vertices, reduced.mesh.vertices)
    assert np.array_equal(again.mesh.triangles, reduced.mesh.triangles)


def test_each_albedo_part_is_reduced_apart_and_a_tiny_part_keeps_its_triangles() -> None:
    sphere = _sphere(0.3, 30, 48)
    lid = Mesh(np.array([[0.0, 0.0, 0.6], [0.05, 0.0, 0.6], [0.0, 0.05, 0.6]]), np.array([[0, 1, 2]]))
    mesh = merge([sphere, lid])
    prop = _prop(mesh, parts=(PropPart(sphere.triangle_count, PNG_A), PropPart(mesh.triangle_count, PNG_B)))
    reduced = level_of_detail(prop)
    first, second = reduced.primitives()
    assert first.material == "prop_synth_barrel" and second.material == "prop_synth_barrel__1"
    assert second.mesh.triangle_count == 1
    assert first.mesh.triangle_count < sphere.triangle_count // 2
    assert reduced.images() == {"prop_synth_barrel": PNG_A, "prop_synth_barrel__1": PNG_B}
    assert reduced.parts[-1].end == reduced.triangles


def test_a_copy_that_loses_a_thin_part_is_refused() -> None:
    # A plate on a 2 mm stalk: clustering drops the stalk, so the box would rise 0.5 m.
    plate = _sphere(0.3, 30, 48, centre_z=0.8)
    stalk = box(-0.001, 0.001, -0.001, 0.001, 0.0, 0.5)
    prop = _prop(merge([plate, stalk]))
    kept = level_of_detail(prop)
    assert box_shift(prop.mesh, kept.mesh) <= LOD_BOX_TOLERANCE_M


def _recording(monkeypatch: pytest.MonkeyPatch, shifts: list[float]) -> list[int]:
    tried: list[int] = []

    def fake(prop: Prop, target: int) -> Prop:
        tried.append(target)
        vertices = np.array(prop.mesh.vertices, copy=True)
        # The first vertex sits at the origin: its x becomes the box's new minimum.
        vertices[0, 0] = -shifts[len(tried) - 1]
        return replace(prop, mesh=Mesh(vertices, prop.mesh.triangles), source_triangles=prop.triangles)

    monkeypatch.setattr(library, "_decimated", fake)
    return tried


def _grid_prop(triangles: int, spec: AssetSpec = BARREL) -> Prop:
    vertices = np.array([[0.0, 0.0, 0.0], [0.5, 0.0, 0.0], [0.0, 0.5, 0.0], [0.0, 0.0, 0.5]])
    return _prop(Mesh(vertices, np.tile(np.array([[0, 1, 2]]), (triangles, 1))), spec)


def test_the_target_doubles_while_a_copy_still_saves_a_quarter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(library, "lod_target", lambda mesh, scale=1.0: 150)
    tried = _recording(monkeypatch, [0.05, 0.05, 0.05])
    # 4 × 300 = 3 × 400: the second target still saves exactly a quarter.
    assert level_of_detail(_grid_prop(400)).source_triangles == 0
    assert tried == [150, 300]
    tried.clear()
    assert level_of_detail(_grid_prop(399)).source_triangles == 0
    assert tried == [150]
    tried.clear()
    assert level_of_detail(_grid_prop(200)).source_triangles == 0
    assert tried == [150]


def test_a_copy_within_the_tolerance_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(library, "lod_target", lambda mesh, scale=1.0: 150)
    tried = _recording(monkeypatch, [0.05, LOD_BOX_TOLERANCE_M])
    assert level_of_detail(_grid_prop(400)).source_triangles == 400
    assert tried == [150, 300]


def test_the_tolerance_is_in_street_metres(monkeypatch: pytest.MonkeyPatch) -> None:
    # A 0.45 m wreck is placed about ten times larger; a 3 mm move becomes about 3 cm.
    scales: list[float] = []

    def target(mesh: Mesh, scale: float = 1.0) -> int:
        scales.append(scale)
        return 150

    monkeypatch.setattr(library, "lod_target", target)
    car = Mesh(box(0, 0.45, 0, 0.18, 0, 0.15).vertices, np.tile(np.array([[0, 1, 2]]), (400, 1)))
    tried = _recording(monkeypatch, [0.003, 0.003])
    assert level_of_detail(_prop(car, WRECK)).source_triangles == 0
    assert tried == [150, 300]
    assert scales[0] > 9.0
    tried.clear()
    assert level_of_detail(_prop(car, BARREL)).source_triangles == 400


def _dense_glb(path: Path, name: str) -> int:
    mesh = _sphere(0.25, 30, 48)
    uvs = np.zeros((mesh.vertex_count, 2), dtype=np.float32)
    path.write_bytes(build_glb(
        [Node(name, (Primitive(mesh, name, uvs),), {"type": "prop"})],
        {name: Material((1, 1, 1, 1), 0.9)}, preserve_uvs=True,
    ))
    return mesh.triangle_count


def test_the_library_loads_the_reduced_copy(tmp_path: Path) -> None:
    root = tmp_path / "library"
    root.mkdir()
    source = _dense_glb(root / "synth_barrel.glb", "synth_barrel")
    (root / "manifest.json").write_text(json.dumps({"assets": [{
        "id": "synth_barrel", "file": "synth_barrel.glb", "category": "waste", "role": "barrel",
        "surfaces": ["sidewalk"], "anchor": "ground", "lane": "forbid", "clearance_m": 0.0, "use": "test",
    }]}), encoding="utf-8")
    before = (root / "synth_barrel.glb").read_bytes()
    (prop,), rejects = read_library(root)
    assert rejects == []
    assert prop.source_triangles == source and prop.triangles < source // 2
    assert (root / "synth_barrel.glb").read_bytes() == before


def test_overgrow_reports_and_tags_the_reduced_props(tmp_path: Path) -> None:
    scene = tmp_path / "geo_scene.json"
    _scene(scene, _city_features())
    _base(tmp_path / "base")
    root = tmp_path / "library"
    _library(root)
    source = _dense_glb(root / "synth_trash.glb", "synth_trash")
    report = overgrow(tmp_path / "base", scene, tmp_path / "out", zone=square_zone(80), library=root, seed=48152)
    lod = report["library_lod"]
    assert list(lod) == ["synth_trash"]
    reduced = lod["synth_trash"]["triangles"]
    assert lod["synth_trash"]["source_triangles"] == source and reduced < source // 2
    placed = json.loads((tmp_path / "out" / "scene_detail.json").read_text())["objects"]
    copies = sum(1 for row in placed if row.get("asset_id") == "synth_trash")
    assert copies >= 1
    assert report["library_source_triangles"] == report["library_triangles"] + copies * (source - reduced)
    assert report["added_triangles"] == report["procedural_triangles"] + report["library_triangles"]
    nodes, _ = nodes_from_glb((tmp_path / "out" / "scene_visual.glb").read_bytes())
    tagged = [node for node in nodes if node.extras.get("asset_id") == "synth_trash"]
    assert len(tagged) == copies
    assert all(node.extras["lod"] == {"source_triangles": source, "triangles": reduced} for node in tagged)
    assert not any("lod" in node.extras for node in nodes if node.extras.get("asset_id") == "synth_car")


def test_a_planned_prop_carries_its_lod_in_the_node_extras() -> None:
    features = _block_features()
    digest = build_digest(_plan_scene(features, longitude=MURCIA_CENTER[0], latitude=MURCIA_CENTER[1], crs="EPSG:25830"))
    plan = parse_overgrow_plan(
        '{"plan_version":"0.1.0","mood":"watch","actions":[{"op":"watch","asset_id":"security_camera_02",'
        '"poi_id":"poi:osm-node-place","count":2}]}'
    )
    props = [replace(p, source_triangles=1750) if p.spec.id == "security_camera_02" else p for p in _props()]
    tagged = realize(plan, digest, props, _site(features))
    assert [node.extras["lod"] for node in tagged.nodes] == [{"source_triangles": 1750, "triangles": 12}] * 2
    plain = realize(plan, digest, _props(), _site(features))
    assert plain.nodes and not any("lod" in node.extras for node in plain.nodes)
    assert ASSETS_BY_ID["security_camera_02"].role == "camera"


def test_a_vehicle_keeps_its_whole_mesh() -> None:
    """Clustering tore holes in the cars' bodies on the headset (10 November 2026): a vehicle is never reduced."""
    car = Mesh(box(0, 4.5, 0, 1.8, 0, 1.5).vertices, np.tile(np.array([[0, 1, 2]]), (5000, 1)))
    prop = _prop(car, CAR)
    assert level_of_detail(prop) is prop
    assert level_of_detail(_prop(car, WRECK)) is not prop



def test_a_cup_or_a_croissant_is_scaled_down_to_its_real_size() -> None:
    """Rue Mouffetard: coffee cups 45 cm long and croissants 37 cm stood on the tables (12 November 2026)."""
    cup = _prop(Mesh(np.array([[0.0, 0.0, 0.0], [0.45, 0.0, 0.0], [0.0, 0.31, 0.0], [0.0, 0.0, 0.25]]), np.array([[0, 1, 2], [0, 2, 3]])))
    small = library.interior_size(cup, "decor_tableware")
    assert max(np.ptp(small.mesh.vertices, axis=0)) == pytest.approx(0.3)
    assert small.fit_scale == pytest.approx(0.3 / 0.45)
    assert np.allclose(small.mesh.vertices.min(axis=0), 0.0)  # scaled about the origin, as fit_scale is
    croissant = library.interior_size(cup, "decor_food")
    assert max(np.ptp(croissant.mesh.vertices, axis=0)) == pytest.approx(0.4)
    # A kind without a cap, or a piece already within it, is the same object.
    assert library.interior_size(cup, "wardrobe") is cup
    assert library.interior_size(cup, "") is cup
    tiny = _prop(Mesh(np.array([[0.0, 0.0, 0.0], [0.3, 0.0, 0.0], [0.0, 0.1, 0.0]]), np.array([[0, 1, 2]])))
    assert library.interior_size(tiny, "decor_tableware") is tiny
    assert library.INTERIOR_MAX_M == {"decor_tableware": 0.3, "decor_food": 0.4}
