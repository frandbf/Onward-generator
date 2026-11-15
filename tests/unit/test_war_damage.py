"""War damage on walls and ground (signs/damage.py, plan 2026-11-15-ambiente-belico, increment A)."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import Point, box

from onward_generator.generation.config import Material
from onward_generator.generation.gltf import Node, Primitive, build_glb, nodes_from_glb
from onward_generator.generation.mesh import Mesh, prism
from onward_generator.signs import damage
from onward_generator.signs import place as signs_place
from onward_generator.signs.damage import (
    GROUND_DECALS,
    GROUND_FLAT_M,
    GROUND_GAP_M,
    GROUND_SPACING_M,
    HEAVY_DECALS,
    WAR_KINDS,
    WAR_SHARE,
    Ground,
    GroundPlaced,
    ground_count,
    ground_decals,
    place_ground,
    resolve_damage,
    wall_count,
    war_sequence,
    war_wall_decals,
)
from onward_generator.signs.decals import Decal, decal_nodes, procedural_decals
from onward_generator.signs.place import SignsError, place_signs
from tests.unit.test_street_signs import _scene


def _square(x0: float, y0: float, x1: float, y1: float, z: float) -> Mesh:
    """A flat square at height z, facing up."""
    return Mesh(np.array([[x0, y0, z], [x1, y0, z], [x1, y1, z], [x0, y1, z]]), np.array([[0, 1, 2], [0, 2, 3]]))


def test_the_settings() -> None:
    assert damage.LEVELS == ("none", "light", "heavy")
    assert WAR_KINDS == frozenset({"bullets", "crack", "soot", "peeling", "shell_impact", "scorch", "shrapnel"})
    assert WAR_SHARE == {"light": 0.4, "heavy": 0.6} and HEAVY_DECALS == (60, 90)
    assert GROUND_DECALS == {"light": (12, 20), "heavy": (30, 45)}
    assert (GROUND_GAP_M, GROUND_FLAT_M, GROUND_SPACING_M) == (0.015, 0.03, 0.5)
    assert damage.GROUND_NODES == ("surface/carriageway", "surface/sidewalk", "surface/ground")


@pytest.mark.parametrize(("value", "profile", "expected"), [
    (None, "wrecked", ("none", "default")),
    ("auto", "wrecked", ("heavy", "street_profile")),
    ("auto", "worn", ("none", "street_profile")),
    ("auto", None, ("none", "street_profile")),
    ("none", "wrecked", ("none", "map")),
    ("light", None, ("light", "map")),
    ("heavy", "worn", ("heavy", "map")),
])
def test_the_level_comes_from_the_map_or_its_street_profile(value: object, profile: str | None, expected: tuple[str, str]) -> None:
    assert resolve_damage(value, profile) == expected


@pytest.mark.parametrize("value", ["extreme", True, 3, "Heavy"])
def test_an_unknown_level_is_refused(value: object) -> None:
    with pytest.raises(ValueError, match=r'^map.json "damage" must be one of auto, none, light, heavy, got '):
        resolve_damage(value, None)


def test_the_war_decals_are_drawn_from_the_seed() -> None:
    first, again, other = war_wall_decals(7), war_wall_decals(7), war_wall_decals(8)
    assert [d.id for d in first] == ["gen_shell_impact", "gen_scorch_mark", "gen_shrapnel_spray"]
    assert [d.kind for d in first] == ["shell_impact", "scorch", "shrapnel"]
    assert all(d.kind in WAR_KINDS and d.origin == "generated" and d.description for d in first)
    assert [(d.width_m, d.height_m) for d in first] == [(1.6, 1.4), (1.6, 1.8), (1.8, 1.2)]
    for a, b, c in zip(first, again, other, strict=True):
        assert a.rgba.shape == (256, 256, 4) and a.rgba.dtype == np.uint8
        assert np.array_equal(a.rgba, b.rgba) and not np.array_equal(a.rgba, c.rgba)
        alpha = a.rgba[..., 3]
        assert 0.02 < float((alpha > 0).mean()) < 0.98  # neither empty nor a solid square


def test_the_ground_decals_are_drawn_from_the_seed() -> None:
    first, again, other = ground_decals(3), ground_decals(3), ground_decals(4)
    assert [d.kind for d in first] == ["road_crack", "pothole", "sand", "ground_scorch", "rubble"]
    assert [(d.width_m, d.height_m) for d in first] == [(2.4, 2.4), (1.8, 1.4), (3.0, 1.2), (2.2, 2.2), (2.0, 2.0)]
    for a, b, c in zip(first, again, other, strict=True):
        assert np.array_equal(a.rgba, b.rgba) and not np.array_equal(a.rgba, c.rgba)
        assert 0.02 < float((a.rgba[..., 3] > 0).mean()) < 0.995


def test_no_drawn_decal_carries_a_new_kind_without_damage() -> None:
    # A map without damage draws the decals it drew before: the war kinds stay out of procedural_decals.
    assert not {d.id for d in procedural_decals(1)} & {d.id for d in [*war_wall_decals(1), *ground_decals(1)]}


def test_the_counts_by_level() -> None:
    assert wall_count(5, "none", 37) == 37 and wall_count(5, "light", 37) == 37
    heavy = {wall_count(seed, "heavy", 37) for seed in range(200)}
    assert min(heavy) == 60 and max(heavy) == 90
    assert ground_count(5, "none") == 0
    light = {ground_count(seed, "light") for seed in range(200)}
    assert min(light) == 12 and max(light) == 20
    heavy_ground = {ground_count(seed, "heavy") for seed in range(200)}
    assert min(heavy_ground) == 30 and max(heavy_ground) == 45
    assert wall_count(9, "heavy", 30) == wall_count(9, "heavy", 50)  # the seed decides, not the base


def _decal(name: str, kind: str) -> Decal:
    return Decal(name, kind, 1.0, 1.0, np.zeros((4, 4, 4), dtype=np.uint8), "generated")


@pytest.mark.parametrize(("level", "count"), [("heavy", 60), ("heavy", 7), ("light", 30), ("light", 5)])
def test_a_share_of_the_wall_decals_are_war_damage_spread_over_the_walls(level: str, count: int) -> None:
    pool = [_decal("a", "stain"), _decal("b", "moss"), _decal("c", "bullets"), _decal("d", "poster")]
    war = [_decal("w", "shell_impact")]
    sequence = war_sequence(pool, war, count, 11, level)
    assert len(sequence) == count
    wars = sum(d.kind in WAR_KINDS for d in sequence)
    assert wars == math.ceil(count * WAR_SHARE[level])
    assert sequence[0].kind in WAR_KINDS and sequence[1].kind not in WAR_KINDS  # interleaved, war first
    assert {d.id for d in sequence if d.kind in WAR_KINDS} == {"c", "w"}  # the pool's war kinds and the drawn ones


def test_without_other_decals_every_one_is_war_damage() -> None:
    sequence = war_sequence([_decal("c", "crack")], [_decal("w", "scorch")], 9, 2, "light")
    assert len(sequence) == 9 and all(d.kind in WAR_KINDS for d in sequence)


def test_a_war_decal_in_both_lists_is_one_decal() -> None:
    shared = _decal("w", "scorch")
    sequence = war_sequence([shared, _decal("a", "stain")], [shared], 4, 2, "heavy")
    assert [d.id for d in sequence].count("w") == 3 and len(sequence) == 4


def _ground_nodes() -> list[Node]:
    carriageway = Node("surface/carriageway", (Primitive(_square(0.0, 0.0, 20.0, 10.0, 0.1), "carriageway"),))
    sidewalk = Node("surface/sidewalk", (Primitive(_square(0.0, 10.0, 20.0, 14.0, 0.25), "sidewalk"),))
    zebra = Node("surface/carriageway/zebra", (Primitive(_square(14.0, 0.0, 16.0, 10.0, 0.1005), "zebra_paint"),))
    house = Node("building/x", (Primitive(prism(box(2.0, 2.0, 6.0, 6.0), 0.0, 5.0, bottom=False), "plaster"),))
    return [carriageway, sidewalk, zebra, house]


def test_the_ground_under_a_point_is_its_top_face() -> None:
    ground = Ground(_ground_nodes())
    assert ground.top(10.0, 5.0) == pytest.approx((0.1, True))
    assert ground.top(10.0, 12.0) == pytest.approx((0.25, True))
    assert ground.top(4.0, 4.0) == pytest.approx((5.0, False))  # a roof over the carriageway
    assert ground.top(15.0, 5.0) == pytest.approx((0.1005, False))  # a zebra crossing
    assert ground.top(30.0, 5.0) is None
    assert ground.top(0.0, 0.0) == pytest.approx((0.1, True))  # a corner of a face counts
    assert Ground([]).top(0.0, 0.0) is None


def test_ground_decals_lie_flat_on_one_surface_spaced_apart() -> None:
    ground = Ground(_ground_nodes())
    decals = [d for d in ground_decals(5) if d.width_m <= 2.0]
    placed = place_ground(ground, box(0.0, 0.0, 20.0, 14.0), decals, 8, 5)
    assert len(placed) == 8
    for item in placed:
        corners = item.corners()
        assert np.allclose(corners[:, 2], item.centre[2])
        surface = ground.top(item.centre[0], item.centre[1])
        assert surface is not None and surface[1]
        assert item.centre[2] == pytest.approx(surface[0] + GROUND_GAP_M)
        for x, y in corners[:, :2]:
            under = ground.top(x, y)
            assert under is not None and under[1] and abs(under[0] - surface[0]) <= GROUND_FLAT_M  # off the kerb, house and zebra
    from shapely.geometry import Polygon
    plans = [Polygon(p.corners()[:, :2]) for p in placed]
    assert all(a.distance(b) >= GROUND_SPACING_M for i, a in enumerate(plans) for b in plans[i + 1:])
    assert place_ground(ground, box(0.0, 0.0, 20.0, 14.0), decals, 8, 5) == placed  # deterministic


def test_nothing_to_place_places_nothing() -> None:
    ground = Ground(_ground_nodes())
    assert place_ground(ground, box(0.0, 0.0, 20.0, 14.0), [], 5, 1) == []
    assert place_ground(ground, box(0.0, 0.0, 20.0, 14.0), ground_decals(1), 0, 1) == []
    assert place_ground(ground, box(0.0, 0.0, 0.0, 0.0).buffer(0), ground_decals(1), 5, 1) == []
    # No allowed ground at all: tries run out.
    roofs = Ground([_ground_nodes()[3]])
    assert place_ground(roofs, box(0.0, 0.0, 20.0, 14.0), ground_decals(1), 3, 1) == []


def test_a_ground_quad_faces_up_and_turns_by_its_yaw() -> None:
    decal = _decal("g", "sand")
    item = GroundPlaced(decal, (1.0, 2.0, 0.3), math.pi / 2)
    corners = item.corners()
    assert np.allclose(corners, [[1.5, 1.5, 0.3], [1.5, 2.5, 0.3], [0.5, 2.5, 0.3], [0.5, 1.5, 0.3]])
    normal = np.cross(corners[1] - corners[0], corners[2] - corners[0])
    assert normal[2] > 0


def test_ground_decals_share_the_wall_decal_pages() -> None:
    sand = Decal("gen_sand_drift", "sand", 3.0, 1.2, np.full((8, 8, 4), 200, dtype=np.uint8), "generated")
    nodes, materials, images, rows = decal_nodes([], [GroundPlaced(sand, (1.0, 2.0, 0.115), 0.25)])
    assert [n.name for n in nodes] == ["ground_decal/0"] and list(materials) == ["wall_decal_atlas_0"] == list(images)
    prim = nodes[0].primitives[0]
    assert prim.material == "wall_decal_atlas_0" and prim.mesh.triangles.tolist() == [[0, 1, 2], [0, 2, 3]]
    assert nodes[0].extras == {"type": "ground_decal", "decal": "gen_sand_drift", "kind": "sand", "attribute_origin": "generated"}
    assert rows == [{"id": "ground_decal/0", "type": "ground_decal", "decal": "gen_sand_drift", "kind": "sand", "origin": "generated",
                     "position_m": [1.0, 2.0, 0.115], "yaw_deg": 14.32, "size_m": [3.0, 1.2], "attribute_origin": "generated"}]


def _damaged_base(path: Path) -> None:
    """The ``test_street_signs`` base with a carriageway under the three buildings."""
    path.mkdir()
    built = [Node(f"building/{fid}", (Primitive(prism(box(*bounds), 0.0, height, bottom=False), "plaster_white"),))
             for fid, bounds, height in (("osm-way-north", (0, 12, 20, 28), 12.0), ("osm-way-east", (14, -4, 30, 18), 9.0),
                                         ("osm-way-short", (-8, 14, -2, 20), 2.0))]
    road = Node("surface/carriageway", (Primitive(_square(-20.0, -20.0, 40.0, 40.0, 0.0), "carriageway"),))
    (path / "scene_visual.glb").write_bytes(build_glb(
        [road, *built], {"carriageway": Material((0.3, 0.3, 0.3, 1.0), 1.0), "plaster_white": Material((0.9, 0.9, 0.9, 1.0), 0.9)}))
    (path / "scene_collision.glb").write_bytes(b"collision-bytes")
    (path / "scene_detail.json").write_text(json.dumps({
        "schema": "onward_generator.scene_detail",
        "objects": [{"id": "building/osm-way-north", "type": "building", "source_ids": ["osm-way-north"]}],
        "attribution": {"osm": "© OpenStreetMap contributors"},
        "files": {"visual": "scene_visual.glb", "collision": "scene_collision.glb"},
    }), encoding="utf-8")


def test_signs_place_draws_the_map_s_damage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scene = tmp_path / "geo_scene.json"
    _scene(scene)
    root = tmp_path / "map"
    base = root / "data" / "processed" / "scene_materials"
    base.parent.mkdir(parents=True)
    _damaged_base(base)
    monkeypatch.setattr(signs_place, "_ambient", lambda base: ("", False))
    (root / "map.json").write_text("{}", encoding="utf-8")
    document: dict = {}
    monkeypatch.setattr("onward_generator.maps.workspace.load_map", lambda r: document)

    plain = place_signs(base, scene, tmp_path / "plain")
    assert "damage" not in plain
    plain_nodes, _ = nodes_from_glb((tmp_path / "plain" / "scene_visual.glb").read_bytes())
    assert not any(n.name.startswith("ground_decal/") for n in plain_nodes)

    document["damage"] = "heavy"
    heavy = place_signs(base, scene, tmp_path / "heavy")
    block = heavy["damage"]
    assert block["level"] == "heavy" and block["origin"] == "map" and block["war_share"] == 0.6
    assert heavy["wall_decals"]["wanted"] == wall_count(signs_place._seed(json.loads((base / "scene_detail.json").read_text())), "heavy", 0)
    assert block["war_decals"] == sum(1 for row in heavy["wall_decals"]["decals"] if row["kind"] in WAR_KINDS) > 0
    ground_rows = block["ground_decals"]["decals"]
    assert block["ground_decals"]["count"] == len(ground_rows) > 0
    assert all(row["type"] == "ground_decal" and row["position_m"][2] == pytest.approx(GROUND_GAP_M) for row in ground_rows)
    nodes, _ = nodes_from_glb((tmp_path / "heavy" / "scene_visual.glb").read_bytes())
    assert sum(n.name.startswith("ground_decal/") for n in nodes) == len(ground_rows)
    detail = json.loads((tmp_path / "heavy" / "detail_report.json").read_text())
    assert detail["ground_decals"]["count"] == len(ground_rows)

    from onward_generator.signs.decals import procedural_decals as drawn

    seed = signs_place._seed(json.loads((base / "scene_detail.json").read_text()))
    kinds = {row["kind"] for row in heavy["wall_decals"]["decals"]}
    assert {"shell_impact", "scorch", "shrapnel"} & kinds  # the drawn war kinds come in with the sequence
    # The walls follow the war sequence of the map's seed (a decal that fits no wall drops out, so a subsequence).
    expected = iter(d.id for d in war_sequence(drawn(seed), war_wall_decals(seed), heavy["wall_decals"]["wanted"], seed, "heavy"))
    assert all(any(row["decal"] == want for want in expected) for row in heavy["wall_decals"]["decals"])
    # The ground is the one place_ground lays for the map's seed on the built scene.
    built, _ = nodes_from_glb((base / "scene_visual.glb").read_bytes())
    again = place_ground(Ground(built), damage_area(base, scene), ground_decals(seed), ground_count(seed, "heavy"), seed)
    assert [[round(v, 3) for v in p.centre] for p in again] == [row["position_m"] for row in ground_rows]

    # With a core, the ground decals are laid in the core square, not the walls' envelope.
    plain_manifest = json.loads((base / "scene_detail.json").read_text())
    cored = {**plain_manifest, "core": {"half_side_m": 50.0}}
    (base / "scene_detail.json").write_text(json.dumps(cored), encoding="utf-8")
    inside = place_signs(base, scene, tmp_path / "cored")["damage"]["ground_decals"]["decals"]
    in_core = place_ground(Ground(built), signs_place.core_of(cored), ground_decals(seed), ground_count(seed, "heavy"), seed)
    assert [[round(v, 3) for v in p.centre] for p in in_core] == [r["position_m"] for r in inside] != [row["position_m"] for row in ground_rows]
    (base / "scene_detail.json").write_text(json.dumps(plain_manifest), encoding="utf-8")

    document["damage"] = "auto"

    def record(path: object) -> dict:
        assert Path(str(path)) == root / "config" / "street_profile.json"
        return {"ids": {"props_profile": "wrecked"}}

    monkeypatch.setattr("onward_generator.ai.street_profile.load_record", record)
    assert place_signs(base, scene, tmp_path / "auto")["damage"]["origin"] == "street_profile"
    monkeypatch.setattr("onward_generator.ai.street_profile.load_record", lambda path: {"ids": {"props_profile": "worn"}})
    assert "damage" not in place_signs(base, scene, tmp_path / "auto_worn")

    def missing(path: object) -> dict:
        from onward_generator.ai.street_profile import StreetProfileError
        raise StreetProfileError("AI_PROFILE_RECORD", "not found")

    monkeypatch.setattr("onward_generator.ai.street_profile.load_record", missing)
    assert "damage" not in place_signs(base, scene, tmp_path / "auto_none")

    document["damage"] = "loads"
    with pytest.raises(SignsError, match='^map.json "damage" must be one of auto, none, light, heavy, got \'loads\'$'):
        place_signs(base, scene, tmp_path / "bad")
    monkeypatch.setattr("onward_generator.maps.workspace.load_map", lambda r: (_ for _ in ()).throw(ValueError("broken")))
    assert "damage" not in place_signs(base, scene, tmp_path / "unreadable")


WAR_HASHES = {"gen_shell_impact": "6682a0670bd9", "gen_scorch_mark": "bafcf5ac0b17", "gen_shrapnel_spray": "b737f439fd55"}
GROUND_HASHES = {"gen_road_crack": "83454fa162b2", "gen_pothole_patch": "631c5174a011", "gen_sand_drift": "a9935331af6a",
                 "gen_ground_scorch": "397969ff0d72", "gen_flat_rubble": "3651cf3d5597"}


def test_the_drawn_pixels_are_pinned() -> None:
    """Every drawn decal, pixel for pixel, for one seed: a change to how one is drawn shows here first."""
    import hashlib

    assert {d.id: hashlib.sha256(d.rgba.tobytes()).hexdigest()[:12] for d in war_wall_decals(7)} == WAR_HASHES
    assert {d.id: hashlib.sha256(d.rgba.tobytes()).hexdigest()[:12] for d in ground_decals(7)} == GROUND_HASHES


def test_twelve_seeds_are_pinned() -> None:
    """Every drawn kind over twelve seeds: a draw that changes only for some seeds still shows here."""
    import hashlib

    digest = hashlib.sha256()
    for seed in range(12):
        for decal in [*war_wall_decals(seed), *ground_decals(seed)]:
            digest.update(decal.rgba.tobytes())
    assert digest.hexdigest()[:16] == "e6c58ce14580421e"


def test_the_counts_are_pinned_by_seed() -> None:
    assert [wall_count(s, "heavy", 0) for s in range(6)] == [82, 89, 60, 85, 75, 86]
    assert [ground_count(s, "light") for s in range(6)] == [13, 14, 19, 12, 15, 20]
    assert [ground_count(s, "heavy") for s in range(6)] == [32, 35, 43, 31, 35, 45]


def test_the_war_sequence_is_pinned() -> None:
    pool = [_decal("a", "stain"), _decal("b", "moss"), _decal("c", "bullets"), _decal("d", "poster")]
    sequence = war_sequence(pool, [_decal("w", "shell_impact"), _decal("v", "scorch")], 10, 11, "heavy")
    assert [d.id for d in sequence] == ["w", "d", "v", "b", "c", "a", "w", "d", "v", "c"]


def _spots(placed: list[GroundPlaced]) -> list[tuple[str, list[float], float]]:
    return [(p.decal.id, [round(v, 3) for v in p.centre], round(p.yaw, 3)) for p in placed]


def test_the_ground_placements_are_pinned() -> None:
    ground = Ground(_ground_nodes())
    decals = [d for d in ground_decals(5) if d.width_m <= 2.0]
    assert _spots(place_ground(ground, box(0.0, 0.0, 20.0, 14.0), decals, 8, 5)) == [
        ("gen_flat_rubble", [7.029, 1.473, 0.115], 0.939), ("gen_pothole_patch", [9.972, 12.298, 0.265], 0.211),
        ("gen_flat_rubble", [10.037, 4.914, 0.115], 1.594), ("gen_pothole_patch", [5.146, 12.409, 0.265], 3.131),
        ("gen_flat_rubble", [1.474, 12.514, 0.265], 1.741), ("gen_pothole_patch", [12.843, 2.164, 0.115], 1.299),
        ("gen_flat_rubble", [3.981, 8.06, 0.115], 1.129), ("gen_pothole_patch", [1.16, 4.248, 0.115], 1.664)]
    # A round area: spots outside it are tried again, not given up.
    assert _spots(place_ground(ground, Point(10.0, 7.0).buffer(4.0), decals[:1], 4, 3)) == [
        ("gen_pothole_patch", [9.108, 5.641, 0.115], 0.239), ("gen_pothole_patch", [9.262, 8.669, 0.115], 1.766),
        ("gen_pothole_patch", [11.409, 4.467, 0.115], 1.102), ("gen_pothole_patch", [6.401, 8.002, 0.115], 1.94)]
    # Tries run out: a vast area with little allowed ground places one of six in its 150 tries.
    assert len(place_ground(ground, box(0.0, 0.0, 200.0, 140.0), decals[:1], 6, 2)) == 1
    assert len(place_ground(ground, box(0.0, 0.0, 20.0, 14.0), decals[:1], 1, 2)) == 1


def test_a_ground_quad_carries_its_atlas_cell_and_the_millimetre() -> None:
    sand = Decal("gen_sand_drift", "sand", 3.0, 1.2, np.full((8, 8, 4), 200, dtype=np.uint8), "generated")
    nodes, _, _, rows = decal_nodes([], [GroundPlaced(sand, (1.23456, 2.0, 0.11549), 0.25)])
    uv = nodes[0].primitives[0].uvs
    assert uv is not None and uv.shape == (4, 2) and uv.min() > 0.0 and uv.max() < 1.0
    assert rows[0]["position_m"] == [1.235, 2.0, 0.115]


def test_a_given_sequence_replaces_the_turn_of_the_decals() -> None:
    from shapely import STRtree

    from onward_generator.signs.decals import Wall, place_decals

    wall = Wall("w", np.array([0.0, 0.0]), np.array([30.0, 0.0]), np.array([0.0, -1.0]), 9.0)
    first, second = _decal("first", "stain"), _decal("second", "scorch")
    placed = place_decals([wall], STRtree([]), lambda plan, b, t: False, [first], 1, 5, sequence=[second, second])
    assert [p.decal.id for p in placed] == ["second", "second"]


def damage_area(base: Path, scene: Path):  # type: ignore[no-untyped-def]
    """The area ``signs place`` lays ground decals in without a core: the envelope of the core walls' first ends."""
    from shapely.geometry import MultiPoint

    walls = signs_place._walls(json.loads(scene.read_text()).get("features") or [])
    return MultiPoint([tuple(w.a) for w in walls]).envelope
