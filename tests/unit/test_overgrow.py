"""Abandoned dressing stays off the drive lane and keeps OSM trees observed.

The fixture is local metres around MURCIA_CENTER. It is not the Murcia extract.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from shapely.geometry import Polygon

from onward_generator.ai.providers import StaticProvider
from onward_generator.generation.config import Material
from onward_generator.generation.gltf import Node, Primitive, build_glb, nodes_from_glb, read_glb
from onward_generator.generation.mesh import Mesh, cap
from onward_generator.maps.workspace import create_map
from onward_generator.materials.apply import building_boarded, building_uv_phase, stamp_abandoned_facades
from onward_generator.overgrow.ambient import is_solid, plan_from_brief
from onward_generator.overgrow.place import overgrow
from tests.conftest import MURCIA_CENTER


def _feature(kind: str, geometry: dict, tags: dict, feature_id: str) -> dict:
    return {
        "id": feature_id,
        "kind": kind,
        "tags": tags,
        "geometry": {**geometry, "unit": "m", "coordinate_system": "local_grid"},
    }


def test_boarded_buildings_are_a_fixed_hash_of_the_name() -> None:
    # Golden values: the choice must not drift between runs or releases.
    assert [i for i in range(40) if building_boarded(f"building/{i}")] == [6, 21, 34, 38]


def test_each_building_shifts_its_albedo_and_some_windows_are_boarded() -> None:
    wall = Mesh(
        np.array([[0.0, 0.0, 0.0], [4.0, 0.0, 0.0], [4.0, 0.0, 3.0]], dtype=np.float64),
        np.array([[0, 1, 2]]),
    )
    glass = Mesh(
        np.array([[0.0, 0.0, 1.0], [1.0, 0.0, 1.0], [1.0, 0.0, 2.0], [0.0, 0.0, 2.0]], dtype=np.float64),
        np.array([[0, 1, 2], [0, 2, 3]]),
    )
    names = [f"building/{index}" for index in range(40)]
    boarded = next(name for name in names if building_boarded(name))
    plain = next(name for name in names if name != boarded)
    nodes = stamp_abandoned_facades([
        Node(boarded, (Primitive(wall, "plaster_cream"), Primitive(glass, "glass_dark")), {"type": "building"}),
        Node(plain, (Primitive(wall, "plaster_cream"), Primitive(glass, "glass_dark")), {"type": "building"}),
    ], board=True)
    assert nodes[0].primitives[1].material == "glass_boarded"
    assert nodes[1].primitives[1].material == "glass_dark"
    assert building_uv_phase(boarded) != building_uv_phase(plain)
    data = build_glb(
        nodes,
        {"plaster_cream": Material((1, 1, 1, 1), 1), "glass_dark": Material((1, 1, 1, 1), 0.2), "glass_boarded": Material((1, 1, 1, 1), 0.8)},
        repeat_images={"plaster_cream": b"\x89PNG\r\n\x1a\n" + b"x" * 16, "glass_dark": b"\x89PNG\r\n\x1a\n" + b"y" * 16, "glass_boarded": b"\x89PNG\r\n\x1a\n" + b"z" * 16},
        repeat_tile_m={"plaster_cream": 2.0},
        unit_uv_materials={"glass_dark", "glass_boarded"},
    )
    doc, _ = read_glb(data)
    # Two wall primitives share a material but not a UV phase, so the meshes stay distinct.
    assert len(doc["meshes"]) == 2


def test_each_map_gets_its_own_texture_plan(tmp_path: Path) -> None:
    murcia = create_map(tmp_path / "murcia", map_id="murcia", name="Murcia", latitude=MURCIA_CENTER[1], longitude=MURCIA_CENTER[0], radius_m=100)
    other = create_map(tmp_path / "obelisco", map_id="obelisco", name="Obelisco", latitude=-34.60, longitude=-58.38, radius_m=100)
    assert murcia["id"] == "murcia"
    assert other["id"] == "obelisco"
    for folder in (tmp_path / "murcia", tmp_path / "obelisco"):
        plan = json.loads((folder / "config" / "texture_plan.json").read_text(encoding="utf-8"))
        assert plan["provider"] == "library"
        assert plan["entries"]


def test_growth_stays_off_the_lane_and_keeps_an_osm_tree(tmp_path: Path) -> None:
    scene = tmp_path / "geo_scene.json"
    scene.write_text(json.dumps({
        "schema": "onward_generator.geo_scene",
        "origin": {"longitude": MURCIA_CENTER[0], "latitude": MURCIA_CENTER[1]},
        "features": [
            _feature("highway", {"type": "LineString", "coordinates": [[-30, 0], [60, 0]]},
                     {"highway": "residential", "name": "Calle Mayor"}, "osm-way-mayor"),
            _feature("building", {"type": "Polygon", "coordinates": [[[0, 12], [24, 12], [24, 30], [0, 30], [0, 12]]]},
                     {"building": "yes", "height": "12"}, "osm-way-block"),
            _feature("poi", {"type": "Point", "coordinates": [10, 36]},
                     {"natural": "tree"}, "osm-node-tree"),
            _feature("building", {"type": "Polygon", "coordinates": [[[40, 12], [48, 12], [48, 18], [40, 18], [40, 12]]]},
                     {"building": "yes", "height": "1.5"}, "osm-way-low"),
        ],
    }), encoding="utf-8")
    base = tmp_path / "base"
    base.mkdir()
    ground = Mesh(
        np.array([[0.0, 0.0, 0.0], [4.0, 0.0, 0.0], [0.0, 4.0, 0.0]], dtype=np.float64),
        np.array([[0, 1, 2]]),
    )
    (base / "scene_visual.glb").write_bytes(build_glb(
        [Node("ground", (Primitive(ground, "ground"),), {"type": "surface"})],
        {"ground": Material((0.4, 0.4, 0.4, 1), 1)},
    ))
    (base / "scene_collision.glb").write_bytes(b"collision-bytes")
    (base / "scene_detail.json").write_text(json.dumps({
        "schema": "onward_generator.scene_detail",
        "objects": [{"id": "building/osm-way-block", "type": "building", "source_ids": ["osm-way-block"]}],
        "files": {"visual": "scene_visual.glb", "collision": "scene_collision.glb"},
    }), encoding="utf-8")
    report = overgrow(base, scene, tmp_path / "scene_overgrow")
    assert report["geometry"]["identical"] is True
    assert report["trees_observed"] == 1
    assert report["grass"] >= 1
    assert report["moss"] == 0
    assert report["wrecks"] >= 1
    collision_nodes, _ = nodes_from_glb((tmp_path / "scene_overgrow" / "scene_collision.glb").read_bytes())
    assert sum(node.name.startswith("overgrow/collider/wreck/") for node in collision_nodes) == report["wrecks"]
    assert report["street_blocking"] is False
    manifest = json.loads((tmp_path / "scene_overgrow" / "scene_detail.json").read_text())
    growth = [o for o in manifest["objects"] if o["type"] == "overgrowth" and o["kind"] in {"grass", "tree"}]
    for item in growth:
        assert abs(item["position_m"][1]) > 4.2
    nodes, _ = nodes_from_glb((tmp_path / "scene_overgrow" / "scene_visual.glb").read_bytes())
    assert any(node.name.startswith("overgrow/") for node in nodes)


def test_an_abandoned_brief_puts_a_wreck_in_the_carriageway(tmp_path: Path) -> None:
    scene = tmp_path / "geo_scene.json"
    scene.write_text(json.dumps({
        "schema": "onward_generator.geo_scene",
        "origin": {"longitude": MURCIA_CENTER[0], "latitude": MURCIA_CENTER[1]},
        "features": [
            _feature("highway", {"type": "LineString", "coordinates": [[-30, 0], [60, 0]]},
                     {"highway": "residential", "name": "Calle Mayor"}, "osm-way-mayor"),
            _feature("building", {"type": "Polygon", "coordinates": [[[0, 12], [24, 12], [24, 30], [0, 30], [0, 12]]]},
                     {"building": "yes", "height": "12"}, "osm-way-block"),
        ],
    }), encoding="utf-8")
    base = tmp_path / "base"
    base.mkdir()
    ground = Mesh(
        np.array([[0.0, 0.0, 0.0], [4.0, 0.0, 0.0], [0.0, 4.0, 0.0]], dtype=np.float64),
        np.array([[0, 1, 2]]),
    )
    (base / "scene_visual.glb").write_bytes(build_glb(
        [Node("ground", (Primitive(ground, "ground"),), {"type": "surface"})],
        {"ground": Material((0.4, 0.4, 0.4, 1), 1)},
    ))
    (base / "scene_collision.glb").write_bytes(b"collision-bytes")
    (base / "scene_detail.json").write_text(json.dumps({
        "schema": "onward_generator.scene_detail",
        "objects": [{"id": "building/osm-way-block", "type": "building", "source_ids": ["osm-way-block"]}],
    }), encoding="utf-8")
    report = overgrow(base, scene, tmp_path / "out", brief="Ciudad abandonada, autos bloqueando las calles")
    assert report["street_blocking"] is True
    assert report["ambient_origin"] == "rules"
    # No library props here: the colliders are the wrecks and the tree trunks.
    assert report["colliders"] == report["wrecks"] + report["trees"]
    assert report["wrecks"] >= 1
    manifest = json.loads((tmp_path / "out" / "scene_detail.json").read_text())
    wrecks = [row for row in manifest["objects"] if row.get("kind") == "wreck"]
    assert wrecks
    assert min(abs(row["position_m"][1]) for row in wrecks) < 4.2


def test_a_wreck_in_the_street_sits_on_the_asphalt(tmp_path: Path) -> None:
    scene = tmp_path / "geo_scene.json"
    scene.write_text(json.dumps({
        "schema": "onward_generator.geo_scene",
        "origin": {"longitude": MURCIA_CENTER[0], "latitude": MURCIA_CENTER[1]},
        "features": [
            _feature("highway", {"type": "LineString", "coordinates": [[-30, 0], [60, 0]]},
                     {"highway": "residential", "name": "Calle Mayor"}, "osm-way-mayor"),
            _feature("building", {"type": "Polygon", "coordinates": [[[0, 12], [24, 12], [24, 30], [0, 30], [0, 12]]]},
                     {"building": "yes", "height": "12"}, "osm-way-block"),
        ],
    }), encoding="utf-8")
    base = tmp_path / "base"
    base.mkdir()
    road = cap(Polygon([(-40.0, -3.0), (80.0, -3.0), (80.0, 3.0), (-40.0, 3.0)]), -0.15)
    (base / "scene_visual.glb").write_bytes(build_glb(
        [Node("surface/carriageway", (Primitive(road, "carriageway"),), {"type": "surface"})],
        {"carriageway": Material((0.3, 0.3, 0.3, 1), 1)},
    ))
    (base / "scene_collision.glb").write_bytes(b"collision-bytes")
    (base / "scene_detail.json").write_text(json.dumps({
        "schema": "onward_generator.scene_detail",
        "objects": [{"id": "building/osm-way-block", "type": "building", "source_ids": ["osm-way-block"]}],
    }), encoding="utf-8")
    report = overgrow(base, scene, tmp_path / "out", brief="Ciudad abandonada, autos bloqueando las calles")
    manifest = json.loads((tmp_path / "out" / "scene_detail.json").read_text())
    wrecks = [row for row in manifest["objects"] if row.get("kind") == "wreck"]
    grass = [row for row in manifest["objects"] if row.get("kind") == "grass"]
    assert wrecks and grass
    assert all(abs(row["position_m"][2] - (-0.15)) < 1e-6 for row in wrecks)
    for row in grass:
        x, y, z = row["position_m"]
        on_road = -40.0 <= x <= 80.0 and -3.0 <= y <= 3.0
        assert abs(z - (-0.15 if on_road else 0.0)) < 1e-6
    nodes, _ = nodes_from_glb((tmp_path / "out" / "scene_visual.glb").read_bytes())
    placed = [node for node in nodes if node.name.startswith("overgrow/wreck/") and node.primitives]
    assert placed
    assert abs(float(placed[0].matrix[2, 3]) - (-0.15)) < 1e-4
    assert abs(float(placed[0].primitives[0].mesh.vertices[:, 2].min())) < 1e-6
    assert report["wrecks"] == len(wrecks)


def test_the_model_may_only_choose_the_street_rule() -> None:
    ordered = plan_from_brief("una ciudad ordenada")
    assert ordered.street_blocking is False and ordered.origin == "rules"
    abandoned = plan_from_brief("ciudad abandonada con caos")
    assert abandoned.street_blocking is True
    override = plan_from_brief("ciudad ordenada", provider=StaticProvider("replay", '{"street_blocking": true}', "test"))
    assert override.street_blocking is True and override.origin == "ai_generated"
    fallback = plan_from_brief("ciudad abandonada", provider=StaticProvider("replay", "no es json", "test"))
    assert fallback.origin == "rules" and fallback.street_blocking is True
    assert is_solid("wreck", "vehicles") and is_solid("barrel", "waste") and is_solid("pole", "structures")
    assert not is_solid("trash", "waste") and not is_solid("plant", "vegetation") and not is_solid("can", "waste")



def _tree_scene(root: Path, urban: list[dict]) -> tuple[Path, Path]:
    root.mkdir()
    scene = root / "geo_scene.json"
    scene.write_text(json.dumps({
        "schema": "onward_generator.geo_scene",
        "origin": {"longitude": MURCIA_CENTER[0], "latitude": MURCIA_CENTER[1]},
        "features": [
            _feature("highway", {"type": "LineString", "coordinates": [[-30, 0], [60, 0]]},
                     {"highway": "residential", "name": "Calle Mayor"}, "osm-way-mayor"),
            _feature("building", {"type": "Polygon", "coordinates": [[[0, 12], [24, 12], [24, 30], [0, 30], [0, 12]]]},
                     {"building": "yes", "height": "12"}, "osm-way-block"),
            _feature("poi", {"type": "Point", "coordinates": [10, 36]}, {"natural": "tree"}, "osm-node-tree"),
            _feature("poi", {"type": "Point", "coordinates": [18, 38]}, {"natural": "tree"}, "osm-node-tree-2"),
        ],
    }), encoding="utf-8")
    base = root / "base"
    base.mkdir()
    ground = Mesh(np.array([[0.0, 0.0, 0.0], [4.0, 0.0, 0.0], [0.0, 4.0, 0.0]], dtype=np.float64), np.array([[0, 1, 2]]))
    (base / "scene_visual.glb").write_bytes(build_glb(
        [Node("ground", (Primitive(ground, "ground"),), {"type": "surface"})],
        {"ground": Material((0.4, 0.4, 0.4, 1), 1)},
    ))
    (base / "scene_collision.glb").write_bytes(b"collision-bytes")
    (base / "scene_detail.json").write_text(json.dumps({
        "schema": "onward_generator.scene_detail",
        "objects": [{"id": "building/osm-way-block", "type": "building", "source_ids": ["osm-way-block"]}, *urban],
        "files": {"visual": "scene_visual.glb", "collision": "scene_collision.glb"},
    }), encoding="utf-8")
    return base, scene


def _urban(source_id: str, x: float, y: float, *, kind: str = "tree", kind_of: str = "urban_element") -> dict:
    return {
        "id": f"urban/{kind}/{source_id}", "type": kind_of, "kind": kind, "source_ids": [source_id],
        "attribute_origin": "observed", "position_m": [x, y],
    }


def _trees(out: Path) -> list[dict]:
    manifest = json.loads((out / "scene_detail.json").read_text())
    return [o for o in manifest["objects"] if o["type"] == "overgrowth" and o["kind"] == "tree"]


def _bare_trees(tmp_path: Path) -> list[list[float]]:
    base, scene = _tree_scene(tmp_path / "bare", [])
    alone = overgrow(base, scene, tmp_path / "bare" / "out")
    assert alone["trees_observed"] == 2
    generated = [o["position_m"] for o in _trees(tmp_path / "bare" / "out") if o["origin"] == "generated"]
    assert generated
    return generated


def test_a_tree_detail_already_planted_is_not_planted_again(tmp_path: Path) -> None:
    generated = _bare_trees(tmp_path)
    # detail holds the first OSM tree and a tree 2.9 m from each generated one; the second OSM tree is not in detail.
    urban = [
        _urban("osm-node-tree", 10.0, 36.0),
        *(_urban(f"osm-node-near-{index}", x + 2.9, y) for index, (x, y, _z) in enumerate(generated)),
    ]
    base, scene = _tree_scene(tmp_path / "planted", urban)
    report = overgrow(base, scene, tmp_path / "planted" / "out")
    assert report["trees_observed"] == 1
    assert report["trees"] == 1
    assert report["reject_counts"]["in_detail"] == 1
    assert report["reject_counts"]["near_detail_tree"] == len(generated)
    assert [(row["kind"], row["position_m"][:2]) for row in report["rejects"] if row["reason"] == "in_detail"] == [("tree", [10.0, 36.0])]
    assert [o["source_ids"] for o in _trees(tmp_path / "planted" / "out")] == [["osm-node-tree-2"]]


def test_only_a_detail_tree_within_three_metres_moves_a_generated_one(tmp_path: Path) -> None:
    generated = _bare_trees(tmp_path)
    x, y, _z = generated[0]
    urban = [
        *(_urban(f"osm-node-far-{index}", gx + 3.0, gy) for index, (gx, gy, _gz) in enumerate(generated)),
        # On the spot, but a bench, and a tree that is not a detail urban element.
        _urban("osm-node-bench", x, y, kind="bench"),
        _urban("osm-node-other", x, y, kind_of="overgrowth"),
    ]
    base, scene = _tree_scene(tmp_path / "planted", urban)
    report = overgrow(base, scene, tmp_path / "planted" / "out")
    assert report["trees_observed"] == 2
    assert report["trees"] == 2 + len(generated)



def test_every_overgrow_tree_has_the_trunk_box_of_a_street_tree(tmp_path: Path) -> None:
    from onward_generator.detail.modules import urban_prototypes
    from onward_generator.overgrow.place import TRUNK_COLLIDER

    trunk = urban_prototypes()["tree"].collider
    assert trunk is not None and np.array_equal(TRUNK_COLLIDER.vertices, trunk.vertices)
    base, scene = _tree_scene(tmp_path / "town", [])
    report = overgrow(base, scene, tmp_path / "out")
    visual, _ = nodes_from_glb((tmp_path / "out" / "scene_visual.glb").read_bytes())
    collision, _ = nodes_from_glb((tmp_path / "out" / "scene_collision.glb").read_bytes())
    # Generated trees on one building share a node name, so they are kept as a list.
    trees = [node for node in visual if node.name.startswith("overgrow/tree/")]
    boxes = [node for node in collision if node.name.startswith("overgrow/collider/tree/")]
    assert report["trees"] == len(trees) == len(boxes) >= 2
    assert {node.extras["origin"] for node in boxes} == {"observed", "generated"}
    for node, tree in zip(boxes, trees, strict=True):
        assert np.array_equal(node.matrix, tree.matrix)
        assert node.extras == {"type": "collision", "origin": tree.extras["origin"], "role": "tree"}
