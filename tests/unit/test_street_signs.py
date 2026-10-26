"""Street plates use the OSM name and hang on the wall that faces the junction.

Local metres are relative to MURCIA_CENTER. The fixture is not the Murcia extract.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from onward_generator.generation.config import Material
from onward_generator.generation.gltf import Node, Primitive, build_glb, nodes_from_glb
from onward_generator.generation.mesh import Mesh
from onward_generator.signs.plaque import plaque_png
from onward_generator.signs.place import place_signs
from tests.conftest import MURCIA_CENTER


def _feature(kind: str, geometry: dict, tags: dict, feature_id: str) -> dict:
    return {
        "id": feature_id,
        "kind": kind,
        "tags": tags,
        "geometry": {**geometry, "unit": "m", "coordinate_system": "local_grid"},
    }


def _scene(path: Path) -> None:
    path.write_text(json.dumps({
        "schema": "onward_generator.geo_scene",
        "origin": {"longitude": MURCIA_CENTER[0], "latitude": MURCIA_CENTER[1]},
        "features": [
            _feature("highway", {"type": "LineString", "coordinates": [[-10, 8], [40, 8]]},
                     {"highway": "residential", "name": "Calle Mayor"}, "osm-way-mayor"),
            _feature("highway", {"type": "LineString", "coordinates": [[10, -10], [10, 40]]},
                     {"highway": "residential", "name": "Calle Nueva"}, "osm-way-nueva"),
            _feature("highway", {"type": "LineString", "coordinates": [[-10, 0], [40, 0]]},
                     {"highway": "residential"}, "osm-way-unnamed"),
            _feature("highway", {"type": "LineString", "coordinates": [[-5, 30], [30, 30]]},
                     {"highway": "footway", "name": "Sendero"}, "osm-way-path"),
            _feature("highway", {"type": "LineString", "coordinates": [[-10, 4], [40, 4]]},
                     {"highway": "service", "name": "Metrobús", "bus": "designated"}, "osm-way-bus"),
            _feature("building", {"type": "Polygon", "coordinates": [[[0, 12], [20, 12], [20, 28], [0, 28], [0, 12]]]},
                     {"building": "yes", "height": "12"}, "osm-way-north"),
            _feature("building", {"type": "Polygon", "coordinates": [[[14, -4], [30, -4], [30, 18], [14, 18], [14, -4]]]},
                     {"building": "yes", "height": "9"}, "osm-way-east"),
            _feature("building", {"type": "Polygon", "coordinates": [[[-8, 14], [-2, 14], [-2, 20], [-8, 20], [-8, 14]]]},
                     {"building": "yes", "height": "2"}, "osm-way-short"),
        ],
    }), encoding="utf-8")


def _base(path: Path) -> None:
    path.mkdir()
    ground = Mesh(
        np.array([[0.0, 0.0, 0.0], [4.0, 0.0, 0.0], [0.0, 4.0, 0.0]], dtype=np.float64),
        np.array([[0, 1, 2]]),
    )
    (path / "scene_visual.glb").write_bytes(build_glb(
        [Node("ground", (Primitive(ground, "ground"),))],
        {"ground": Material((0.4, 0.4, 0.4, 1.0), 1.0)},
    ))
    (path / "scene_collision.glb").write_bytes(b"collision-bytes")
    (path / "scene_detail.json").write_text(json.dumps({
        "schema": "onward_generator.scene_detail",
        "objects": [{"id": "building/osm-way-north", "type": "building", "source_ids": ["osm-way-north"]}],
        "attribution": {"osm": "© OpenStreetMap contributors"},
        "files": {"visual": "scene_visual.glb", "collision": "scene_collision.glb"},
    }), encoding="utf-8")


def test_named_streets_get_a_plate_on_the_facing_wall(tmp_path: Path) -> None:
    lon, lat = MURCIA_CENTER
    assert (lon, lat) == MURCIA_CENTER
    scene = tmp_path / "geo_scene.json"
    _scene(scene)
    base = tmp_path / "base"
    _base(base)
    out = tmp_path / "scene_signs"
    report = place_signs(base, scene, out)
    names = {row["name"] for row in report["signs"]}
    assert names == {"Calle Mayor", "Calle Nueva"}
    assert "Sendero" not in names
    assert report["geometry"]["identical"] is True
    assert (out / "scene_collision.glb").read_bytes() == b"collision-bytes"
    nodes, _ = nodes_from_glb((out / "scene_visual.glb").read_bytes())
    sign = next(n for n in nodes if n.name.startswith("street_sign/"))
    assert all(prim.uvs is not None and len(prim.uvs) == len(prim.mesh.vertices) for prim in sign.primitives)
    face = sign.primitives[0].uvs
    assert abs(float(face[:, 0].min())) < 1e-4 and abs(float(face[:, 0].max()) - 1.0) < 1e-4
    assert abs(float(face[:, 1].min())) < 1e-4 and abs(float(face[:, 1].max()) - 1.0) < 1e-4
    assert (base / "scene_visual.glb").read_bytes() != (out / "scene_visual.glb").read_bytes()


def test_accented_names_do_not_share_a_plate() -> None:
    plain = plaque_png("Saenz")
    accent = plaque_png("Sáenz Peña")
    assert plain.startswith(b"\x89PNG")
    assert accent != plain
