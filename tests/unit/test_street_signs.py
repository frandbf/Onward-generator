"""Street plates use the OSM name and hang on the wall that faces the junction.

Local metres are relative to MURCIA_CENTER. The fixture is not the Murcia extract.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from shapely import LineString, STRtree, box

from onward_generator.cli import main
from onward_generator.detail.config import load_detail_config
from onward_generator.detail.facades import Hole, Segment
from onward_generator.detail.shops import plan_shops
from onward_generator.generation.config import Material
from onward_generator.generation.gltf import Node, Primitive, build_glb, images_by_material, nodes_from_glb, read_glb
from onward_generator.generation.mesh import Mesh
from onward_generator.signs.place import (
    SIGN_ATLAS_PX,
    _choose,
    _footprints,
    _sign_for,
    _streets,
    _walls,
    place_signs,
    plate_atlas,
)
from onward_generator.signs.plaque import PLATE_H, PLATE_W, plaque_rgba
from onward_generator.signs.shop import PAD_PX
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
    data = (out / "scene_visual.glb").read_bytes()
    nodes, _ = nodes_from_glb(data)
    signs = [n for n in nodes if n.name.startswith("street_sign/")]
    assert all(prim.uvs is not None and len(prim.uvs) == len(prim.mesh.vertices) for sign in signs for prim in sign.primitives)
    # Every plate face is on the one atlas page, in a 512 x 128 px slot of its own name.
    assert {p.material for sign in signs for p in sign.primitives[:2]} == {"street_sign_atlas_0"}
    assert {row["material"] for row in report["signs"]} == {"street_sign_atlas_0"}
    assert "street_sign_atlas_0" in images_by_material(data)
    _, materials = nodes_from_glb(data)
    page = materials["street_sign_atlas_0"]
    assert tuple(page.color) == pytest.approx((1.0, 1.0, 1.0, 1.0)) and page.roughness == pytest.approx(0.6)  # white: the image carries the colour
    slots = {}
    for sign in signs:
        face, back = sign.primitives[0].uvs, sign.primitives[1].uvs
        assert np.array_equal(face, back)
        assert float(face[:, 0].max() - face[:, 0].min()) == pytest.approx(PLATE_W / SIGN_ATLAS_PX, abs=1e-6)
        assert 0.0 <= float(face.min()) and float(face.max()) <= 1.0
        slots.setdefault(sign.extras["name"], tuple(np.round(face.ravel(), 6)))
    assert len(set(slots.values())) == len(slots) == 2
    assert (base / "scene_visual.glb").read_bytes() != (out / "scene_visual.glb").read_bytes()


def test_each_plate_is_painted_into_its_slot_with_its_own_edge_around_it() -> None:
    names = [f"Calle {n}" for n in range(5)]
    pages, slots = plate_atlas(names)
    [(material, pixels)] = pages.items()
    assert material == "street_sign_atlas_0" and pixels.shape[1] == SIGN_ATLAS_PX
    assert pixels.shape[0] & (pixels.shape[0] - 1) == 0  # a power of two
    for name in names:
        page, uv = slots[name]
        assert page == material
        x = round(float(uv[0, 0]) * SIGN_ATLAS_PX)
        y = round((1 - float(uv[2, 1])) * pixels.shape[0])
        plate = plaque_rgba(name)
        assert np.array_equal(pixels[y:y + PLATE_H, x:x + PLATE_W], plate)
        assert np.array_equal(pixels[y - PAD_PX, x:x + PLATE_W], plate[0])  # the padding repeats the plate's edge
        assert np.array_equal(pixels[y + PLATE_H + PAD_PX - 1, x:x + PLATE_W], plate[-1])
    many = [f"Calle {n}" for n in range(64)]
    pages, slots = plate_atlas(many)
    assert sorted(pages) == ["street_sign_atlas_0", "street_sign_atlas_1"]
    assert len({(page, tuple(np.round(uv.ravel(), 6))) for page, uv in slots.values()}) == 64


def test_accented_names_do_not_share_a_plate() -> None:
    plain = plaque_rgba("Saenz")
    accent = plaque_rgba("Sáenz Peña")
    assert plain.shape == accent.shape == (PLATE_H, PLATE_W, 4) and plain.dtype == np.uint8
    assert not np.array_equal(accent, plain)


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


def _corner(ring: list[list[float]], *extra: dict) -> list[dict]:
    return [
        _feature("highway", {"type": "LineString", "coordinates": [[-30, 0], [30, 0]]},
                 {"highway": "residential", "name": "Calle Mayor"}, "osm-way-mayor"),
        _feature("highway", {"type": "LineString", "coordinates": [[0, -30], [0, 30]]},
                 {"highway": "residential", "name": "Calle Nueva"}, "osm-way-nueva"),
        _feature("building", {"type": "Polygon", "coordinates": [ring]}, {"building": "yes", "height": "12"}, "osm-way-corner"),
        *extra,
    ]


@pytest.mark.parametrize("clockwise", [False, True])
def test_a_plate_hangs_outside_its_building_whichever_way_the_ring_runs(clockwise: bool) -> None:
    ring = [[6.0, 6.0], [26.0, 6.0], [26.0, 26.0], [6.0, 26.0], [6.0, 6.0]]
    features = _corner(ring[::-1] if clockwise else ring)
    signs = _choose(_streets(features), _walls(features), 64)
    assert {sign.name for sign in signs} == {"Calle Mayor", "Calle Nueva"}
    for sign in signs:
        x, y = float(sign.position[0]), float(sign.position[1])
        # On the street side of the wall it faces, 7.5 cm out (the 6 cm gap plus half the 3 cm plate).
        assert min(x, y) == pytest.approx(6.0 - 0.075)
        assert float(sign.outward @ (sign.position[:2] - np.array([16.0, 16.0]))) > 0


def _covered_corner() -> list[dict]:
    ring = [[6.0, 6.0], [26.0, 6.0], [26.0, 26.0], [6.0, 26.0], [6.0, 6.0]]
    low = {"building": "yes", "height": "3"}  # too low for a plate of its own
    return _corner(
        ring,
        # A kiosk 0.4 m in front of the corner's south wall: that wall has no street in front of it.
        _feature("building", {"type": "Polygon", "coordinates": [[[6, 5.6], [26, 5.6], [26, 4], [6, 4], [6, 5.6]]]}, low, "osm-way-kiosk"),
        # An L-shaped shed whose box, not its outline, covers the front of the corner's west wall.
        _feature("building", {"type": "MultiPolygon", "coordinates": [
            [[[-4, 2], [-2, 2], [-2, 28], [5.8, 28], [5.8, 30], [-4, 30], [-4, 2]]],
        ]}, low, "osm-way-shed"),
        # Across the street, a block whose south wall has a low wall 1.1 m out: there is still room for a plate.
        _feature("building", {"type": "Polygon", "coordinates": [[[-26, 6], [-6, 6], [-6, 26], [-26, 26], [-26, 6]]]},
                 {"building": "yes", "height": "12"}, "osm-way-west"),
        _feature("building", {"type": "Polygon", "coordinates": [[[-26, 3.5], [-6, 3.5], [-6, 4.9], [-26, 4.9], [-26, 3.5]]]}, low, "osm-way-low-wall"),
    )


def test_a_plate_skips_a_wall_with_another_building_in_front() -> None:
    features = _covered_corner()
    assert len(_footprints(features)) == 5
    open_wall = {sign.name: sign for sign in _choose(_streets(features), _walls(features), 64)}
    covered = {sign.name: sign for sign in _choose(_streets(features), _walls(features), 64, STRtree(_footprints(features)))}
    assert open_wall["Calle Mayor"].feature_id == "osm-way-corner"
    assert covered["Calle Mayor"].feature_id == "osm-way-west"
    assert covered["Calle Nueva"].feature_id == "osm-way-corner"
    assert float(covered["Calle Nueva"].position[0]) == pytest.approx(6.0 - 0.075)


def test_place_signs_reads_the_footprints_of_its_scene(tmp_path: Path) -> None:
    scene = tmp_path / "geo_scene.json"
    scene.write_text(json.dumps({
        "schema": "onward_generator.geo_scene",
        "origin": {"longitude": MURCIA_CENTER[0], "latitude": MURCIA_CENTER[1]},
        "features": _covered_corner(),
    }), encoding="utf-8")
    base = tmp_path / "base"
    _base(base)
    report = place_signs(base, scene, tmp_path / "scene_signs", max_signs=1)
    assert [(row["name"], row["wall_feature"]) for row in report["signs"]] == [("Calle Mayor", "osm-way-west")]


def test_a_street_without_a_crossing_gets_its_plate_at_its_midpoint() -> None:
    features = [
        _feature("highway", {"type": "LineString", "coordinates": [[-30, 0], [30, 0]]},
                 {"highway": "residential", "name": "Calle Sola"}, "osm-way-sola"),
        _feature("building", {"type": "Polygon", "coordinates": [[[-10, 6], [10, 6], [10, 20], [-10, 20], [-10, 6]]]},
                 {"building": "yes", "height": "12"}, "osm-way-block"),
    ]
    signs = _choose(_streets(features), _walls(features), 64)
    assert [(sign.name, sign.feature_id) for sign in signs] == [("Calle Sola", "osm-way-block")]
    assert np.allclose(signs[0].position, [0.0, 6.0 - 0.075, 2.55])
    # A 3 m kiosk along the whole wall leaves the street without a plate.
    features.append(_feature("building", {"type": "Polygon", "coordinates": [[[-10, 4], [10, 4], [10, 5.6], [-10, 5.6], [-10, 4]]]},
                             {"building": "yes", "height": "3"}, "osm-way-kiosk"))
    assert _choose(_streets(features), _walls(features), 64, STRtree(_footprints(features))) == []



def test_a_part_just_inside_another_gets_no_plate() -> None:
    ring = [[6.0, 6.0], [26.0, 6.0], [26.0, 26.0], [6.0, 26.0], [6.0, 6.0]]
    # The enclosing part's south wall is 0.3 m in front of the corner's: the plate would hang inside it.
    enclosing = _feature("building", {"type": "Polygon", "coordinates": [[[6, 5.7], [26, 5.7], [26, 26], [6, 26], [6, 5.7]]]},
                         {"building": "yes", "height": "3"}, "osm-way-enclosing")
    features = _corner(ring, enclosing)
    signs = {sign.name: sign for sign in _choose(_streets(features), _walls(features), 64, STRtree(_footprints(features)))}
    assert "Calle Mayor" not in signs
    assert signs["Calle Nueva"].feature_id == "osm-way-corner"


def _pane(x0: float, x1: float, y: float, z0: float, z1: float, material: str = "glass_view") -> Node:
    """A glass quad in the wall plane y, from x0 to x1 and z0 to z1."""
    mesh = Mesh(np.array([[x0, y, z0], [x1, y, z0], [x1, y, z1], [x0, y, z1]], dtype=np.float64), np.array([[0, 1, 2], [0, 2, 3]]))
    return Node(f"building/pane/{x0}", (Primitive(mesh, material),))


def test_glass_is_a_plan_box_and_a_height_band() -> None:
    from onward_generator.signs.place import _glass

    assert _glass([Node("ground", (Primitive(Mesh(np.zeros((3, 3)), np.array([[0, 1, 2]])), "ground"),))]) is None
    glass = _glass([_pane(0.0, 2.0, 6.0, 2.0, 3.0), _pane(5.0, 6.0, 6.0, 0.0, 1.0, "shop_glass")])
    assert glass is not None
    near = box(0.5, 5.9, 1.5, 6.1)
    assert glass.meets(near, 2.4, 2.7) is True
    assert glass.meets(near, 3.0, 3.5) is False  # only touches its top
    assert glass.meets(near, 1.5, 2.0) is False
    assert glass.meets(box(5.2, 5.9, 5.8, 6.1), 0.5, 0.6) is True


def _choose_on_corner(*panes: Node) -> dict:
    from onward_generator.signs.place import _glass

    ring = [[6.0, 6.0], [26.0, 6.0], [26.0, 26.0], [6.0, 26.0], [6.0, 6.0]]
    features = _corner(ring)
    return {sign.name: sign for sign in _choose(_streets(features), _walls(features), 64, None, _glass(list(panes)))}


def test_a_plate_slides_along_its_wall_off_the_glass() -> None:
    # Without glass Calle Mayor's plate hangs at the corner, its centre half a plate in: x 6.7 on the south wall (y = 6).
    assert float(_choose_on_corner()["Calle Mayor"].position[0]) == pytest.approx(6.7)
    # A window from x 5 to 8 at plate height: the centre must reach 8 + 0.7 + 0.05; in 0.5 m steps that is 9.2.
    moved = _choose_on_corner(_pane(5.0, 8.0, 6.0, 2.0, 3.0))["Calle Mayor"]
    assert float(moved.position[0]) == pytest.approx(9.2)
    assert moved.feature_id == "osm-way-corner"
    assert float(moved.position[1]) == pytest.approx(6.0 - 0.075)


def test_glass_below_or_above_the_plate_does_not_move_it() -> None:
    signs = _choose_on_corner(_pane(5.0, 8.0, 6.0, 0.0, 2.2), _pane(5.0, 8.0, 6.0, 3.0, 4.0, "shop_glass"))
    assert float(signs["Calle Mayor"].position[0]) == pytest.approx(6.7)


def test_a_wall_glazed_all_along_gives_its_turn_to_the_next_wall() -> None:
    # Glass the whole south wall: Calle Mayor has no other wall on this corner, Calle Nueva keeps the west one.
    signs = _choose_on_corner(_pane(5.0, 27.0, 6.0, 2.0, 3.0))
    assert "Calle Mayor" not in signs
    assert signs["Calle Nueva"].feature_id == "osm-way-corner"


def test_a_sliding_plate_stays_on_its_wall() -> None:
    from onward_generator.signs.place import PLATE_SLIDE_MAX_M, PLATE_SLIDE_STEP_M, _clear_of_glass, _glass, _Wall

    wall = _Wall("w", np.array([0.0, 6.0]), np.array([4.0, 6.0]), np.array([0.0, -1.0]), 12.0)
    along = np.array([1.0, 0.0])
    # The 1.4 m plate may slide to x 3.3 at most, its end on the wall's; it needs its centre 0.75 m past the glass.
    start = np.array([0.5, 6.0])
    assert _clear_of_glass(wall, along, start, _glass([_pane(0.0, 2.0, 6.0, 2.0, 3.0)])) == pytest.approx([3.0, 6.0])
    assert _clear_of_glass(wall, along, start, _glass([_pane(0.0, 2.6, 6.0, 2.0, 3.0)])) is None  # 3.35 is off the wall
    # Sliding back towards a: with glass from x 1.8 on, the plate clears it at x 1.0, two steps back from 2.0.
    back = _clear_of_glass(wall, along, np.array([2.0, 6.0]), _glass([_pane(1.8, 4.0, 6.0, 2.0, 3.0)]))
    assert back == pytest.approx([1.0, 6.0])
    assert _clear_of_glass(wall, along, start, None) == pytest.approx([0.5, 6.0])
    # With no glass in reach the closest point stays, even on a wall shorter than the plate.
    short = _Wall("s", np.array([0.0, 6.0]), np.array([1.0, 6.0]), np.array([0.0, -1.0]), 12.0)
    far_glass = _glass([_pane(50.0, 51.0, 6.0, 2.0, 3.0)])
    assert np.allclose(_clear_of_glass(short, along, np.array([0.5, 6.0]), far_glass), [0.5, 6.0])
    assert (PLATE_SLIDE_STEP_M, PLATE_SLIDE_MAX_M) == (0.5, 4.0)



def test_place_signs_keeps_the_plates_off_the_glass_of_its_base(tmp_path: Path) -> None:
    ring = [[6.0, 6.0], [26.0, 6.0], [26.0, 26.0], [6.0, 26.0], [6.0, 6.0]]
    scene = tmp_path / "geo_scene.json"
    scene.write_text(json.dumps({
        "schema": "onward_generator.geo_scene",
        "origin": {"longitude": MURCIA_CENTER[0], "latitude": MURCIA_CENTER[1]},
        "features": _corner(ring),
    }), encoding="utf-8")
    base = tmp_path / "base"
    _base(base)
    ground = Mesh(np.array([[0.0, 0.0, 0.0], [4.0, 0.0, 0.0], [0.0, 4.0, 0.0]], dtype=np.float64), np.array([[0, 1, 2]]))
    (base / "scene_visual.glb").write_bytes(build_glb(
        [Node("ground", (Primitive(ground, "ground"),)), _pane(5.0, 8.0, 6.0, 2.0, 3.0)],
        {"ground": Material((0.4, 0.4, 0.4, 1.0), 1.0), "glass_view": Material((0.2, 0.3, 0.4, 1.0), 0.1)},
    ))
    report = place_signs(base, scene, tmp_path / "scene_signs")
    mayor = next(row for row in report["signs"] if row["name"] == "Calle Mayor")
    assert mayor["position_m"][0] == pytest.approx(9.2)


def test_glass_also_holds_the_shop_signs_it_is_given() -> None:
    from onward_generator.signs.place import _glass

    fascia = (box(6.3, 5.9, 9.2, 6.05), 2.6, 2.96)
    signs_only = _glass([], [fascia])
    assert signs_only is not None
    assert signs_only.meets(box(8.0, 5.9, 8.5, 6.1), 2.32, 2.78) is True
    assert signs_only.meets(box(8.0, 5.9, 8.5, 6.1), 2.0, 2.6) is False  # only touches its bottom
    assert signs_only.meets(box(9.3, 5.9, 9.8, 6.1), 2.32, 2.78) is False
    both = _glass([_pane(20.0, 22.0, 6.0, 2.0, 3.0)], [fascia])
    assert both is not None
    assert both.meets(box(8.0, 5.9, 8.5, 6.1), 2.32, 2.78) and both.meets(box(20.5, 5.9, 21.0, 6.1), 2.32, 2.78)
    assert _glass([], []) is None


def test_place_signs_keeps_a_plate_off_a_shop_sign(tmp_path: Path) -> None:
    """On the headset the plate of Calle Platería hid most of a shop's fascia: the plates only knew the glass."""
    ring = [[6.0, 6.0], [26.0, 6.0], [26.0, 26.0], [6.0, 26.0], [6.0, 6.0]]
    scene = tmp_path / "geo_scene.json"
    scene.write_text(json.dumps({
        "schema": "onward_generator.geo_scene",
        "origin": {"longitude": MURCIA_CENTER[0], "latitude": MURCIA_CENTER[1]},
        "features": _corner(ring),
    }), encoding="utf-8")
    base = tmp_path / "base"
    _base(base)
    cfg = load_detail_config()
    cfg = replace(cfg, shops=replace(cfg.shops, fascia_share=1.0, vinyl_share=0.0))
    # A 2.5 m shopfront from x 6.5 on the south wall: its fascia runs x 6.35..9.15 at z 2.6..2.96, over the plate's band.
    holes = [Hole("shopfront", 0, 0.5, 0.25, 2.5, 2.3, 0.15, "glass_dark", 0, 0)]
    shops = plan_shops("osm-way-corner", [("osm-node-1", {"amenity": "pharmacy"}, (8.0, 7.0))], holes,
                       [Segment(0, 0, 0, (6.0, 6.0), (26.0, 6.0), True)], cfg)
    manifest = json.loads((base / "scene_detail.json").read_text(encoding="utf-8"))
    manifest["objects"].append({"id": "building/osm-way-corner", "type": "building", "source_ids": ["osm-way-corner"],
                                "facade": {"shops": shops}})
    (base / "scene_detail.json").write_text(json.dumps(manifest), encoding="utf-8")
    report = place_signs(base, scene, tmp_path / "scene_signs")
    mayor = next(row for row in report["signs"] if row["name"] == "Calle Mayor")
    # Without the sign the plate hangs at x 6.7; it needs its centre 0.75 m past 9.2, so 10.2 in 0.5 m steps.
    assert mayor["position_m"][0] == pytest.approx(10.2)
    assert report["shop_signs"]["kinds"]["fascia"] == 1



def _slide(start_x: float, *panes: Node, length: float = 4.0) -> list[float] | None:
    from onward_generator.signs.place import _clear_of_glass, _glass, _Wall

    wall = _Wall("w", np.array([0.0, 6.0]), np.array([length, 6.0]), np.array([0.0, -1.0]), 12.0)
    spot = _clear_of_glass(wall, np.array([1.0, 0.0]), np.array([start_x, 6.0]), _glass(list(panes)) if panes else None)
    return None if spot is None else [round(float(v), 6) for v in spot]


@pytest.mark.parametrize(
    ("start", "panes", "length", "expected"),
    [
        (2.0, (), 4.0, [2.0, 6.0]),  # no glass: the spot itself
        (2.0, (_pane(50.0, 51.0, 6.0, 2.0, 3.0),), 4.0, [2.0, 6.0]),  # glass out of reach: the spot itself
        (2.0, (_pane(2.3, 4.0, 6.0, 2.0, 3.0),), 4.0, [1.5, 6.0]),  # one step back
        (3.0, (_pane(2.9, 3.1, 6.0, 2.0, 3.0),), 6.0, [4.0, 6.0]),  # both ways clear at 1 m: forward first
        (1.0, (_pane(0.0, 4.05, 6.0, 2.0, 3.0),), 10.0, [5.0, 6.0]),  # the full 4 m slide
        (1.0, (_pane(0.0, 4.55, 6.0, 2.0, 3.0),), 10.0, None),  # 4.5 m would be needed
        (1.2, (_pane(1.5, 4.0, 6.0, 2.0, 3.0),), 4.0, [0.7, 6.0]),  # back to the wall's start exactly
        (1.0, (_pane(1.3, 4.0, 6.0, 2.0, 3.0),), 4.0, None),  # 0.5 would leave the wall
        (2.8, (_pane(0.0, 2.5, 6.0, 2.0, 3.0),), 4.0, [3.3, 6.0]),  # forward to the wall's end exactly
        (2.0, (_pane(1.0, 3.0, 6.0, 2.0, 2.35),), 6.0, [4.0, 6.0]),  # glass 2 cm under the plate still counts
        (2.0, (_pane(1.0, 3.0, 6.0, 2.75, 3.5),), 6.0, [4.0, 6.0]),  # and 2 cm over it
        (2.0, (_pane(1.0, 3.0, 6.0, 2.64, 3.5),), 6.0, [4.0, 6.0]),  # overlapping its top
        (2.0, (_pane(2.72, 4.0, 6.0, 2.0, 3.0),), 4.0, [1.5, 6.0]),  # 2 cm past its forward end still counts
        (2.0, (_pane(1.0, 1.28, 6.0, 2.0, 3.0),), 4.0, [2.5, 6.0]),  # and 2 cm past its back end
        (2.0, (_pane(1.0, 3.0, 6.1, 2.0, 3.0),), 6.0, [4.0, 6.0]),  # a window set 0.1 m into the wall
        (2.0, (_pane(1.0, 3.0, 6.3, 2.0, 3.0),), 6.0, [4.0, 6.0]),  # a shopfront at the back of a 0.3 m wall
        (2.0, (_pane(1.0, 3.0, 6.41, 2.0, 3.0),), 6.0, [2.0, 6.0]),  # glass past the 0.4 m reach behind the wall
        (2.0, (_pane(1.0, 3.0, 5.84, 2.0, 3.0),), 6.0, [2.0, 6.0]),  # and past the 0.14 m in front of it

    ],
)
def test_a_plate_slides_to_the_nearest_clear_stretch_of_its_wall(
    start: float, panes: tuple[Node, ...], length: float, expected: list[float] | None,
) -> None:
    assert _slide(start, *panes, length=length) == expected



def test_glass_on_a_wall_off_the_x_axis_is_seen_where_it_is() -> None:
    from onward_generator.signs.place import _glass

    # A pane in the wall x = 20, from y 2 to 4 and z 2 to 3: its plan box is x 20..20, y 2..4.
    mesh = Mesh(np.array([[20.0, 2.0, 2.0], [20.0, 4.0, 2.0], [20.0, 4.0, 3.0], [20.0, 2.0, 3.0]], dtype=np.float64), np.array([[0, 1, 2], [0, 2, 3]]))
    glass = _glass([Node("building/pane", (Primitive(mesh, "glass_view"),))])
    assert glass is not None
    assert glass.meets(box(19.9, 3.0, 20.1, 3.5), 2.4, 2.6) is True
    assert glass.meets(box(19.9, 2.0 - 0.5, 20.1, 1.9), 2.4, 2.6) is False  # z 2..3 must not read as y
    assert glass.meets(box(19.9, 4.1, 20.1, 4.5), 2.4, 2.6) is False



def test_a_glazed_nearest_wall_hands_the_plate_to_the_one_across_the_street() -> None:
    from onward_generator.signs.place import _glass

    ring = [[6.0, 6.0], [26.0, 6.0], [26.0, 26.0], [6.0, 26.0], [6.0, 6.0]]
    west = _feature("building", {"type": "Polygon", "coordinates": [[[-26, 7], [-6, 7], [-6, 26], [-26, 26], [-26, 7]]]},
                    {"building": "yes", "height": "12"}, "osm-way-west")
    features = _corner(ring, west)
    glazed = _glass([_pane(5.0, 27.0, 6.0, 2.0, 3.0)])
    signs = {sign.name: sign for sign in _choose(_streets(features), _walls(features), 64, None, glazed)}
    assert signs["Calle Mayor"].feature_id == "osm-way-west"


def test_a_wall_holds_one_plate() -> None:
    from onward_generator.signs.place import material_id

    ring = [[6.0, 6.0], [26.0, 6.0], [26.0, 26.0], [6.0, 26.0], [6.0, 6.0]]
    features = _corner(ring)
    streets, walls = _streets(features), _walls(features)
    tree = STRtree([LineString([wall.a, wall.b]) for wall in walls])
    used: set[tuple[str, int]] = set()
    first = _sign_for("Calle Mayor", np.array([0.0, 0.0]), streets, walls, tree, used)
    assert first is not None and first.material == material_id("Calle Mayor")
    assert len(used) == 1
    assert _sign_for("Calle Mayor", np.array([0.0, 0.0]), streets, walls, tree, used) is None


@pytest.mark.parametrize(("depth", "moved"), [(0.30, True), (0.39, True), (0.41, False), (-0.13, True), (-0.15, False)])
def test_a_plate_slides_off_glass_set_back_in_its_wall(depth: float, moved: bool) -> None:
    """On Barcelona Sarria two plates hung over shopfront glass 0.30 m behind the wall line, past a 0.14 m check."""
    # The wall line is y = 6, its street side -y: depth > 0 is into the wall, < 0 in front of the plate.
    spot = _slide(2.0, _pane(1.0, 3.0, 6.0 + depth, 2.0, 3.0))
    assert (spot != [2.0, 6.0]) is moved


def test_two_footprints_on_one_wall_line_do_not_get_a_plate_each_in_one_spot() -> None:
    """On Barcelona Sarria a Catastro sliver 3 mm in front of its neighbour's wall gave two names one volume."""
    from onward_generator.signs.place import _plate_box

    ring = [[6.0, 6.0], [26.0, 6.0], [26.0, 26.0], [6.0, 26.0], [6.0, 6.0]]
    sliver = [[6.0, 5.997], [26.0, 5.997], [26.0, 6.0], [6.0, 6.0], [6.0, 5.997]]
    features = _corner(
        ring,
        _feature("highway", {"type": "LineString", "coordinates": [[-30, -1], [30, -1]]},
                 {"highway": "residential", "name": "Calle Vieja"}, "osm-way-vieja"),
        _feature("building", {"type": "Polygon", "coordinates": [sliver]}, {"building": "yes", "height": "12"}, "osm-way-sliver"),
    )
    signs = _choose(_streets(features), _walls(features), 64)
    assert {"Calle Mayor", "Calle Nueva"} <= {sign.name for sign in signs}
    boxes = [_plate_box(sign) for sign in signs]
    assert not any(a.intersects(b) for i, a in enumerate(boxes) for b in boxes[i + 1:])


def test_a_plate_box_is_its_width_along_the_wall_and_its_thickness_out() -> None:
    from onward_generator.signs.place import PLATE_THICK_M, PLATE_WIDTH_M, Sign, _plate_box

    sign = Sign("Calle Mayor", "m", np.array([2.0, 5.0, 2.55]), np.array([1.0, 0.0]), np.array([0.0, -1.0]), "w")
    plate = _plate_box(sign)
    assert plate.is_valid and plate.area == pytest.approx(PLATE_WIDTH_M * PLATE_THICK_M)
    assert plate.bounds == pytest.approx((2.0 - PLATE_WIDTH_M / 2, 5.0 - PLATE_THICK_M / 2, 2.0 + PLATE_WIDTH_M / 2, 5.0 + PLATE_THICK_M / 2))


def test_streets_without_a_junction_do_not_share_a_plate_spot_either() -> None:
    ring = [[6.0, 6.0], [26.0, 6.0], [26.0, 26.0], [6.0, 26.0], [6.0, 6.0]]
    sliver = [[6.0, 5.997], [26.0, 5.997], [26.0, 6.0], [6.0, 6.0], [6.0, 5.997]]
    features = [
        _feature("highway", {"type": "LineString", "coordinates": [[-30, -1], [30, -1]]}, {"highway": "residential", "name": "Calle Vieja"}, "osm-way-a"),
        _feature("highway", {"type": "LineString", "coordinates": [[-30, -2], [30, -2]]}, {"highway": "residential", "name": "Calle Baja"}, "osm-way-b"),
        _feature("building", {"type": "Polygon", "coordinates": [ring]}, {"building": "yes", "height": "12"}, "osm-way-corner"),
        _feature("building", {"type": "Polygon", "coordinates": [sliver]}, {"building": "yes", "height": "12"}, "osm-way-sliver"),
    ]
    from onward_generator.signs.place import _plate_box

    signs = _choose(_streets(features), _walls(features), 64)
    assert signs
    boxes = [_plate_box(sign) for sign in signs]
    assert not any(a.intersects(b) for i, a in enumerate(boxes) for b in boxes[i + 1:])


def test_a_plate_that_finds_its_wall_taken_tries_the_next_wall() -> None:
    ring = [[6.0, 6.0], [26.0, 6.0], [26.0, 26.0], [6.0, 26.0], [6.0, 6.0]]
    sliver = [[6.0, 5.997], [26.0, 5.997], [26.0, 6.0], [6.0, 6.0], [6.0, 5.997]]
    far = [[6.0, -20.0], [26.0, -20.0], [26.0, -10.0], [6.0, -10.0], [6.0, -20.0]]  # across the street, farther
    features = _corner(
        ring,
        _feature("highway", {"type": "LineString", "coordinates": [[-30, -1], [30, -1]]},
                 {"highway": "residential", "name": "Calle Vieja"}, "osm-way-vieja"),
        _feature("building", {"type": "Polygon", "coordinates": [sliver]}, {"building": "yes", "height": "12"}, "osm-way-sliver"),
        _feature("building", {"type": "Polygon", "coordinates": [far]}, {"building": "yes", "height": "12"}, "osm-way-far"),
    )
    signs = {sign.name: sign for sign in _choose(_streets(features), _walls(features), 64)}
    assert signs["Calle Mayor"].feature_id == "osm-way-sliver"
    assert signs["Calle Vieja"].feature_id == "osm-way-far"
