"""Street plates use the OSM name and hang on the wall that faces the junction.

Local metres are relative to MURCIA_CENTER. The fixture is not the Murcia extract.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from shapely import LineString, STRtree

from onward_generator.cli import main
from onward_generator.detail.config import load_detail_config
from onward_generator.detail.facades import Hole, Segment
from onward_generator.detail.shops import plan_shops
from onward_generator.generation.config import Material
from onward_generator.generation.gltf import Node, Primitive, build_glb, images_by_material, nodes_from_glb, read_glb
from onward_generator.generation.mesh import Mesh
from onward_generator.signs.place import _choose, _sign_for, _streets, _walls, place_signs
from onward_generator.signs.plaque import plaque_png
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


def _shop_base(path: Path) -> None:
    cfg = load_detail_config()
    cfg = replace(cfg, shops=replace(cfg.shops, fascia_share=1.0, vinyl_share=1.0))
    _base(path)
    seg = Segment(0, 0, 0, (0.0, 12.0), (20.0, 12.0), True)
    holes = [Hole("shopfront", 0, 1.0, 0.25, 3.0, 2.3, 0.15, "glass_dark", 0, 0)]
    shops = plan_shops("osm-way-north", [("osm-node-1", {"amenity": "pharmacy"}, (2.0, 11.0))], holes, [seg], cfg)
    manifest = json.loads((path / "scene_detail.json").read_text(encoding="utf-8"))
    manifest["objects"][0]["facade"] = {"shops": shops}
    (path / "scene_detail.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_shop_signs_are_drawn_and_reported(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    scene = tmp_path / "geo_scene.json"
    _scene(scene)
    base = tmp_path / "base"
    _shop_base(base)
    out = tmp_path / "scene_signs"
    assert main(["signs", "place", "--base", str(base), "--scene", str(scene), "--out", str(out)]) == 0
    assert "signs: plates=2 shop_signs=2" in capsys.readouterr().out
    report = json.loads((out / "signs_report.json").read_text(encoding="utf-8"))
    shops = report["shop_signs"]
    assert (shops["count"], shops["skipped_atlas_full"], shops["pages"], shops["triangles"]) == (2, 0, 1, 12)
    assert shops["kinds"] == {"fascia": 1, "blade": 0, "vinyl": 1}
    assert [(row["kind"], row["name"], row["name_origin"]) for row in shops["signs"]] == [
        ("fascia", "Farmacia", "estimated"), ("vinyl", "Farmacia", "estimated"),
    ]
    data = (out / "scene_visual.glb").read_bytes()
    assert read_glb(data)[0]["extras"] == {"manifest": "scene_detail.json", "street_signs": 2, "shop_signs": 2}
    nodes, _ = nodes_from_glb(data)
    node = next(n for n in nodes if n.name == "shop_signs/osm-way-north")
    assert [p.material for p in node.primitives] == ["shop_sign_atlas_0", "shop_sign_edge"]
    assert all(p.uvs is not None and len(p.uvs) == len(p.mesh.vertices) for p in node.primitives)
    assert "shop_sign_atlas_0" in images_by_material(data)
    manifest = json.loads((out / "scene_detail.json").read_text(encoding="utf-8"))
    rows = [o for o in manifest["objects"] if o["type"] == "shop_sign"]
    assert [(o["id"], o["kind"], o["attribute_origin"], o["building"]) for o in rows] == [
        ("shop_sign/osm-way-north/0", "fascia", "generated", "osm-way-north"),
        ("shop_sign/osm-way-north/1", "vinyl", "generated", "osm-way-north"),
    ]
    detail_report = json.loads((out / "detail_report.json").read_text(encoding="utf-8"))
    assert detail_report["shop_signs"] == {"count": 2, "skipped": 0}
    assert (out / "scene_collision.glb").read_bytes() == b"collision-bytes"
    assert report["geometry"]["identical"] is True


@pytest.mark.parametrize(("glass", "vinyls"), [("glass_boarded", 0), ("glass_dark", 1)])
def test_a_boarded_shop_keeps_its_sign_and_loses_its_vinyl(tmp_path: Path, glass: str, vinyls: int) -> None:
    scene = tmp_path / "geo_scene.json"
    _scene(scene)
    base = tmp_path / "base"
    _shop_base(base)
    pane = Mesh(np.array([[0.0, 12.0, 1.0], [1.0, 12.0, 1.0], [1.0, 12.0, 2.0]]), np.array([[0, 1, 2]]))
    grey = Material((0.4, 0.4, 0.4, 1.0), 1.0)
    (base / "scene_visual.glb").write_bytes(build_glb(
        [Node("ground", (Primitive(pane, "ground"),)),
         Node("building/osm-way-north", (Primitive(pane, glass),)),
         Node("building/osm-way-east", (Primitive(pane, "glass_dark"),))],
        {"ground": grey, "glass_boarded": grey, "glass_dark": grey},
    ))
    report = place_signs(base, scene, tmp_path / "out")
    assert report["shop_signs"]["kinds"] == {"fascia": 1, "blade": 0, "vinyl": vinyls}


class _EveryWall:
    """Stands in for the wall tree: every wall is a candidate, as before the tree."""

    def __init__(self, count: int) -> None:
        self.count = count

    def query(self, _geometry: object) -> np.ndarray:
        return np.arange(self.count)


def _block_town() -> list[dict]:
    """A 360 m square of blocks on two crossing streets; every ninth block is drawn twice."""
    rng = np.random.default_rng(48152)
    features = [
        _feature("highway", {"type": "LineString", "coordinates": [[-180, 2], [100, 2]]},
                 {"highway": "residential", "name": "Calle Mayor"}, "osm-way-mayor"),
        _feature("highway", {"type": "LineString", "coordinates": [[3, -180], [3, 100]]},
                 {"highway": "residential", "name": "Calle Nueva"}, "osm-way-nueva"),
    ]
    for index in range(70):
        x0, y0 = (float(v) for v in rng.uniform(-170, 160, 2))
        w, d = (float(v) for v in rng.uniform(6, 20, 2))
        ring = [[x0, y0], [x0 + w, y0], [x0 + w, y0 + d], [x0, y0 + d], [x0, y0]]
        for twin in range(2 if index % 9 == 0 else 1):
            features.append(_feature("building", {"type": "Polygon", "coordinates": [ring]},
                                     {"building": "yes", "height": "12"}, f"osm-way-{index}-{twin}"))
    return features


def test_the_wall_tree_picks_the_same_wall_as_trying_every_wall() -> None:
    features = _block_town()
    streets, walls = _streets(features), _walls(features)
    tree = STRtree([LineString([wall.a, wall.b]) for wall in walls])
    picked = 0
    for x in range(-170, 171, 34):
        for y in range(-170, 171, 34):
            for name in ("Calle Mayor", "Calle Nueva"):
                point = np.array([float(x), float(y)])
                fast = _sign_for(name, point, streets, walls, tree, set())
                slow = _sign_for(name, point, streets, walls, _EveryWall(len(walls)), set())  # type: ignore[arg-type]
                assert (fast is None) == (slow is None)
                if fast is not None and slow is not None:
                    picked += 1
                    assert fast.feature_id == slow.feature_id
                    assert np.array_equal(fast.position, slow.position)
                    assert np.array_equal(fast.along, slow.along)
    assert picked > 60, picked
    # Each street's first plate is the one for the crossing at (3, 2), not for its midpoint 40 m away.
    chosen = {sign.name: sign for sign in _choose(streets, walls, 64)}
    for name in ("Calle Mayor", "Calle Nueva"):
        at_crossing = _sign_for(name, np.array([3.0, 2.0]), streets, walls, tree, set())
        assert at_crossing is not None and np.array_equal(chosen[name].position, at_crossing.position)


def test_no_plate_goes_outside_the_playable_core(tmp_path: Path) -> None:
    """The block moved 40 m east: with a 50 m core the east building (x 54–70 m) is past the edge."""
    scene = tmp_path / "geo_scene.json"
    _scene(scene)
    document = json.loads(scene.read_text(encoding="utf-8"))

    def shift(coords: list) -> list:  # type: ignore[type-arg]
        if isinstance(coords[0], (int, float)):
            return [coords[0] + 40, *coords[1:]]
        return [shift(part) for part in coords]

    for feature in document["features"]:
        feature["geometry"]["coordinates"] = shift(feature["geometry"]["coordinates"])
    scene.write_text(json.dumps(document), encoding="utf-8")
    base = tmp_path / "base"
    _base(base)
    every = place_signs(base, scene, tmp_path / "all")["signs"]
    assert {row["wall_feature"] for row in every} >= {"osm-way-east"}
    manifest = json.loads((base / "scene_detail.json").read_text(encoding="utf-8"))
    (base / "scene_detail.json").write_text(json.dumps({**manifest, "core": {"half_side_m": 50.0}}), encoding="utf-8")
    kept = place_signs(base, scene, tmp_path / "core")["signs"]
    assert kept and all(row["position_m"][0] <= 50.0 for row in kept)
    assert "osm-way-east" not in {row["wall_feature"] for row in kept}
    assert any(row["position_m"][0] > 50.0 for row in every)
