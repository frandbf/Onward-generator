"""Wall decals (ADR-038): from 10 to 20 on every map, varied, clear of what they must not cover."""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from shapely import STRtree
from shapely.geometry import Polygon, box

from onward_generator.assetstore.decals import DECALS_SCHEMA, decal_rows, read_decal_catalogues
from onward_generator.assetstore.packages import find_packages
from onward_generator.generation.gltf import Node, Primitive
from onward_generator.generation.mesh import Mesh
from onward_generator.signs.decals import (
    DECAL_MATERIAL,
    MAX_DECALS,
    MIN_DECALS,
    PROCEDURAL,
    Decal,
    Wall,
    decal_blocker,
    decal_count,
    decal_nodes,
    exposed,
    library_decals,
    place_decals,
    procedural_decals,
    turn_order,
)


def _wall(x0: float, x1: float, y: float = 0.0, height: float | None = 12.0, fid: str = "w") -> Wall:
    # A south face: the building lies north of y, so outward is -y.
    return Wall(fid, np.array([x0, y]), np.array([x1, y]), np.array([0.0, -1.0]), height)


def _decal(name: str, w: float = 1.0, h: float = 1.0) -> Decal:
    return Decal(name, "stain", w, h, np.zeros((8, 8, 4), dtype=np.uint8), "generated")


NOTHING = STRtree([box(500, 500, 501, 501)])


def never(plan: Polygon, bottom: float, top: float) -> bool:
    return False


def test_every_map_gets_ten_to_twenty_decals_and_the_drawn_ones_are_stable() -> None:
    assert (MIN_DECALS, MAX_DECALS, DECAL_MATERIAL) == (10, 20, "wall_decal_atlas_{}")
    counts = {decal_count(seed) for seed in range(400)}
    assert min(counts) == 10 and max(counts) == 20
    first, again = procedural_decals(7), procedural_decals(7)
    assert [d.id for d in first] == [f"gen_{name}" for name in PROCEDURAL] and len(first) == 7
    assert all(np.array_equal(a.rgba, b.rgba) for a, b in zip(first, again, strict=True))
    assert any(not np.array_equal(a.rgba, b.rgba) for a, b in zip(first, procedural_decals(8), strict=True))
    for decal in first:
        alpha = decal.rgba[..., 3]
        assert decal.rgba.shape == (256, 256, 4) and (alpha == 0).mean() > 0.05 and alpha.max() > 0, decal.id


def test_each_decal_comes_once_before_any_repeats() -> None:
    pool = [_decal(f"d{k}") for k in range(5)]
    order = turn_order(pool, 12, 3)
    assert len(order) == 12 and {d.id for d in order[:5]} == {d.id for d in pool} and order[5:10] == order[:5]
    assert turn_order([], 12, 3) == [] and turn_order(pool, 0, 3) == []


def test_decals_go_on_exposed_walls_clear_of_each_other_and_of_what_they_must_not_cover() -> None:
    walls = [_wall(0, 30, fid="long"), _wall(40, 41.5, fid="short")]
    placed = place_decals(walls, NOTHING, never, [_decal("a"), _decal("b", 2.0, 0.8)], 5, 6)
    assert len(placed) == 6 and all(p.wall.feature_id == "long" for p in placed)
    boxes = [(p.s_m - p.decal.width_m / 2, p.s_m + p.decal.width_m / 2, p.z_m - p.decal.height_m / 2, p.z_m + p.decal.height_m / 2)
             for p in placed]
    for i, (s0, s1, z0, z1) in enumerate(boxes):
        assert z0 >= 0.15 and z1 <= 11.5 and s0 >= 0.0 and s1 <= 30.0
        for t0, t1, u0, u1 in boxes[i + 1:]:
            assert not (s0 < t1 + 0.4 and s1 > t0 - 0.4 and z0 < u1 + 0.4 and z1 > u0 - 0.4)
    # A party wall (a footprint just outside it) gets none; a wall blocked everywhere gets none.
    party = STRtree([box(0, -1, 30, -0.1)])
    assert place_decals([_wall(0, 30)], party, never, [_decal("a")], 5, 3) == []
    assert place_decals([_wall(0, 30)], NOTHING, lambda plan, b, t: True, [_decal("a")], 5, 3) == []
    assert exposed(_wall(0, 30), NOTHING) and not exposed(_wall(0, 30), party)


def test_a_low_wall_or_a_window_band_moves_the_decal() -> None:
    low = place_decals([_wall(0, 10, height=2.0)], NOTHING, never, [_decal("a", 1.0, 1.0)], 5, 1)
    assert low and low[0].z_m + 0.5 <= 1.5  # under the wall top less 0.5 m
    assert place_decals([_wall(0, 10, height=1.0)], NOTHING, never, [_decal("a", 1.0, 1.0)], 5, 1) == []
    # Glass from 1 m to 3 m along the whole wall: the decal leaves that band.
    pane = Node("b", (Primitive(Mesh(np.array([[0, 0.05, 1.0], [10, 0.05, 1.0], [10, 0.05, 3.0], [0, 0.05, 3.0]]),
                                     np.array([[0, 1, 2], [0, 2, 3]])), "glass_dark"),), {})
    placed = place_decals([_wall(0, 10)], NOTHING, decal_blocker([pane]), [_decal("a", 1.0, 1.0)], 5, 1)
    assert placed and (placed[0].z_m - 0.5 >= 3.0 + 0.4 or placed[0].z_m + 0.5 <= 1.0 - 0.4)
    sign = (box(0, -0.5, 10, 0.5), 0.0, 20.0)
    assert place_decals([_wall(0, 10)], NOTHING, decal_blocker([], [sign]), [_decal("a")], 5, 1) == []
    assert not decal_blocker([])(box(0, 0, 1, 1), 0.0, 1.0)


def test_each_decal_is_a_quad_in_front_of_its_wall_on_a_transparent_atlas_page() -> None:
    placed = place_decals([_wall(0, 30)], NOTHING, never, procedural_decals(1)[:3], 5, 4)
    nodes, materials, images, rows = decal_nodes(placed)
    assert len(nodes) == len(rows) == 4 and set(materials) == {"wall_decal_atlas_0"} == set(images)
    for node, row, item in zip(nodes, rows, placed, strict=True):
        mesh = node.primitives[0].mesh
        assert mesh.triangle_count == 2 and np.allclose(mesh.vertices[:, 1], -0.01)  # 1 cm out from the wall
        normal = np.cross(mesh.vertices[1] - mesh.vertices[0], mesh.vertices[2] - mesh.vertices[0])
        assert normal[1] < 0  # facing the street
        assert np.ptp(mesh.vertices[:, 0]) == pytest.approx(item.decal.width_m) and np.ptp(mesh.vertices[:, 2]) == pytest.approx(item.decal.height_m)
        assert row["type"] == "wall_decal" and row["decal"] == item.decal.id and row["kind"] == item.decal.kind
        uv = node.primitives[0].uvs
        assert uv.min() >= 0 and uv.max() <= 1
    with Image.open(io.BytesIO(images["wall_decal_atlas_0"])) as page:
        assert page.mode == "RGBA" and page.size == (2048, 2048)
    assert decal_nodes([]) == ([], {}, {}, [])


def _decal_package(cache: Path) -> Path:
    folder = cache / "Pub" / "Textures MaterialsBricks"
    folder.mkdir(parents=True)
    path = folder / "Bricks Decals FREE.unitypackage"

    def png(alpha: int, size: tuple[int, int]) -> bytes:
        out = io.BytesIO()
        image = Image.new("RGBA", size, (120, 60, 40, 255))
        image.paste((0, 0, 0, alpha), (0, 0, size[0] // 2, size[1]))
        image.save(out, format="PNG")
        return out.getvalue()

    members = {
        "a" * 32: ("Assets/Bricks Decals/Textures/Decals_Bricks_01/Decals_Bricks_01.png", png(0, (1024, 512))),
        "b" * 32: ("Assets/Bricks Decals/Textures/Decals_Bricks_01/Decals_Bricks_01_Normal.png", png(0, (64, 64))),
        "c" * 32: ("Assets/Bricks Decals/Textures/Floor texture/asphalt_2_diffuse.png", png(255, (64, 64))),
        "d" * 32: ("Assets/Bricks Decals/Materials/Decals_Bricks_01.mat", b"Material:"),
        "e" * 32: ("Assets/Bricks Decals/Textures/Decals_Bricks_02/Decals_Bricks_02.png", b"not an image"),
    }
    with tarfile.open(path, "w:gz") as archive:
        for guid, (pathname, data) in members.items():
            for name, payload in (("pathname", pathname.encode()), ("asset", data)):
                info = tarfile.TarInfo(f"{guid}/{name}")
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
    return path


def loaded_description(assets: Path) -> str:
    return library_decals(assets)[0].description


def test_a_decal_package_becomes_a_local_catalogue_of_its_transparent_albedos(tmp_path: Path) -> None:
    _decal_package(tmp_path / "cache")
    package = find_packages(tmp_path / "cache")[0]
    assets = tmp_path / "assets"
    rows = decal_rows(package, assets)
    assert [r["id"] for r in rows] == ["as_bricks_decals_free_Decals_Bricks_01"]  # no normal map, no opaque texture
    row = rows[0]
    assert row == {"id": "as_bricks_decals_free_Decals_Bricks_01", "file": "Decals_Bricks_01.png", "kind": "brick_patch",
                   "description": "Wall decal (decals bricks 01) from Bricks Decals FREE: brick showing through broken plaster.",
                   "width_m": 2.263, "height_m": 1.131, "license": "LicenseRef-Unity-Asset-Store-EULA"}
    assert row["width_m"] == pytest.approx(1.6 * 2 ** 0.5, abs=1e-3) and row["height_m"] == pytest.approx(1.6 / 2 ** 0.5, abs=1e-3)
    folder = assets / "asset_store_bricks_decals_free"
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema"] == DECALS_SCHEMA and manifest["schema_version"] == "0.1.0" and manifest["assets"] == rows
    assert manifest["package"] == {"publisher": "Pub", "category": "Textures MaterialsBricks", "name": "Bricks Decals FREE"}
    assert manifest["source"].startswith("Unity Asset Store EULA (Extension Asset), local only: Pub / Bricks Decals FREE.")
    assert "Do not commit" in (folder / "License.txt").read_text(encoding="utf-8")
    assert loaded_description(assets) == row["description"]
    with Image.open(folder / "Decals_Bricks_01.png") as image:
        assert image.size == (512, 256) and image.mode == "RGBA"
    found = read_decal_catalogues(assets)
    assert [r["id"] for _, r in found] == ["as_bricks_decals_free_Decals_Bricks_01"]
    loaded = library_decals(assets)
    assert [d.id for d in loaded] == ["as_bricks_decals_free_Decals_Bricks_01"] and loaded[0].origin == "library"
    assert loaded[0].rgba.shape == (256, 512, 4)
    (assets / "asset_store_models").mkdir()
    (assets / "asset_store_models" / "manifest.json").write_text(json.dumps({"schema": "other", "assets": [{"id": "x"}]}), encoding="utf-8")
    (assets / "asset_store_broken").mkdir()
    (assets / "asset_store_broken" / "manifest.json").write_text("{", encoding="utf-8")
    assert len(read_decal_catalogues(assets)) == 1


# --- the model picks the decals (ADR-038) ------------------------------------------------------------------------------

from onward_generator.ai.providers import ProviderError, ProviderResult  # noqa: E402 - the AI section of this file
from onward_generator.signs.decal_pick import MAX_PICK, MIN_KINDS, MIN_PICK, DecalPickError, parse, pick_decals, schema  # noqa: E402 - the AI section of this file


def _reply(*ids: str) -> str:
    return json.dumps({"pick_version": "0.1.0", "decal_ids": list(ids)})


class _Replies:
    name = "replies"

    def __init__(self, *answers: object) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, str, dict]] = []

    def complete(self, system: str, instruction: str, schema: dict) -> ProviderResult:
        self.calls.append((system, instruction, schema))
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return ProviderResult(str(answer), {})


def test_a_decal_pick_names_six_to_twelve_offered_ids_of_three_kinds() -> None:
    assert (MIN_PICK, MAX_PICK, MIN_KINDS) == (6, 12, 3)
    pool = procedural_decals(1)
    ids = [d.id for d in pool]
    assert parse(_reply(*ids[:6]), pool) == tuple(ids[:6])
    for text, message in (
        ("{", "not JSON"), ("[]", "pick_version"), (json.dumps({"pick_version": "0.1.0", "decal_ids": "x"}), "list of ids"),
        (_reply(*ids[:5], "gen_unknown"), "not offered: gen_unknown"), (_reply(*ids[:5], ids[0]), "chosen once"),
        (_reply(*ids[:5]), "from 6 to 12 ids, not 5"),
    ):
        with pytest.raises(DecalPickError, match=message):
            parse(text, pool)
    same_kind = [_decal(f"s{k}") for k in range(8)] + [Decal("p", "poster", 1, 1, np.zeros((2, 2, 4), np.uint8), "generated")]
    with pytest.raises(DecalPickError, match="at least 2 kinds"):
        parse(_reply(*[f"s{k}" for k in range(6)]), same_kind)
    sch = schema(pool[:4])
    assert sch["properties"]["decal_ids"]["minItems"] == 4 and sch["properties"]["decal_ids"]["items"]["enum"] == sorted(ids[:4])


def test_a_failed_pick_is_asked_again_then_every_decal_is_used_and_a_stored_one_replayed() -> None:
    pool = procedural_decals(1)
    ids = [d.id for d in pool]
    second = _Replies("{bad", _reply(*ids[:6]))
    picked = pick_decals(pool, brief="Calle quemada", provider=second)
    assert picked.ok and picked.decal_ids == tuple(ids[:6]) and picked.record["attempts"] == 2
    assert "previous reply was rejected" in second.calls[1][1] and '"Calle quemada"' in second.calls[0][1]
    assert "gen_soot_streak" in second.calls[0][1] and "Black soot" in second.calls[0][1]
    down = pick_decals(pool, brief="", provider=_Replies(ProviderError("off"), ProviderError("off")))
    assert not down.ok and down.decal_ids == tuple(sorted(ids)) and down.record["origin"] == "rules_fallback" and down.record["reason"] == "off"

    class _Never:
        name = "never"

        def complete(self, system: str, instruction: str, schema: dict) -> ProviderResult:
            raise AssertionError("replayed without a call")

    again = pick_decals(pool, brief="Calle quemada", provider=_Never(), replay=picked.record)
    assert again.decal_ids == picked.decal_ids and again.record["origin"] == "replay" and again.record["attempts"] == 0
    asked = _Replies(_reply(*ids[1:7]))
    assert pick_decals(pool, brief="other", provider=asked, replay=picked.record).decal_ids == tuple(ids[1:7])
    assert pick_decals(pool, brief="Calle quemada", provider=_Replies(_reply(*ids[:6])), replay={**picked.record, "raw": "{"}).ok


# --- the decals are deterministic: pinned pixels, placement, quads and page (generated in the Docker image) ------------

import hashlib  # noqa: E402 - the pinned section of this file

DIGESTS = {"gen_damp_stain": "f3501cfe3d427187", "gen_soot_streak": "f862dbd66ee9eb1b", "gen_rust_drip": "bb56a372c739b3b8",
           "gen_plaster_crack": "15117665b2f22852", "gen_torn_poster": "06711467ae46fd73", "gen_paint_scribble": "811451e3d45be684",
           "gen_splash_grime": "1bb0d559c78a14ec"}


def test_the_drawn_decals_are_pinned() -> None:
    assert {d.id: hashlib.sha256(d.rgba.tobytes()).hexdigest()[:16] for d in procedural_decals(7)} == DIGESTS
    assert [decal_count(s) for s in range(8)] == [11, 12, 13, 11, 11, 11, 20, 11]
    assert [(d.kind, d.width_m, d.height_m, d.origin) for d in procedural_decals(7)] == [
        ("stain", 1.4, 1.4, "generated"), ("soot", 1.0, 2.0, "generated"), ("rust", 0.8, 1.6, "generated"),
        ("crack", 1.0, 1.6, "generated"), ("poster", 0.7, 1.0, "generated"), ("graffiti", 1.6, 0.9, "generated"),
        ("grime", 2.4, 0.8, "generated")]
    assert all(d.description.endswith(".") for d in procedural_decals(7))


def test_the_placement_quads_and_page_of_a_known_street_are_pinned() -> None:
    walls = [_wall(0, 14, height=9.0, fid="w1"),
             Wall("w2", np.array([20.0, 0.0]), np.array([20.0, 8.0]), np.array([1.0, 0.0]), None),
             _wall(30, 32, height=9.0, fid="w3")]
    pane = Node("b", (Primitive(Mesh(np.array([[2, 0.05, 1.0], [6, 0.05, 1.0], [6, 0.05, 2.5], [2, 0.05, 2.5]], float),
                                     np.array([[0, 1, 2], [0, 2, 3]])), "glass_dark"),), {})
    placed = place_decals(walls, NOTHING, decal_blocker([pane]), procedural_decals(7), 11, 9)
    assert [(p.decal.id, p.wall.feature_id, round(p.s_m, 4), round(p.z_m, 4)) for p in placed] == [
        ("gen_rust_drip", "w1", 11.1058, 1.4), ("gen_paint_scribble", "w2", 1.0137, 1.4), ("gen_soot_streak", "w1", 6.9942, 1.4),
        ("gen_damp_stain", "w2", 2.9275, 1.4), ("gen_splash_grime", "w1", 3.1761, 3.6), ("gen_torn_poster", "w2", 4.4619, 1.4),
        ("gen_plaster_crack", "w1", 8.6519, 1.4), ("gen_rust_drip", "w2", 7.0371, 1.4), ("gen_paint_scribble", "w1", 13.0972, 1.4)]
    nodes, _, images, rows = decal_nodes(placed[:3])
    assert nodes[0].primitives[0].mesh.vertices.round(4).tolist() == [
        [10.7058, -0.01, 0.6], [11.5058, -0.01, 0.6], [11.5058, -0.01, 2.2], [10.7058, -0.01, 2.2]]
    assert nodes[0].primitives[0].uvs.round(5).tolist() == [[0.00195, 0.87305], [0.12695, 0.87305], [0.12695, 0.99805], [0.00195, 0.99805]]
    assert hashlib.sha256(images["wall_decal_atlas_0"]).hexdigest()[:16] == "30932d1155d142a1"
    assert rows[0] == {"id": "wall_decal/0", "type": "wall_decal", "decal": "gen_rust_drip", "kind": "rust", "origin": "generated",
                       "wall_feature": "w1", "position_m": [11.106, -0.01, 1.4], "size_m": [0.8, 1.6], "attribute_origin": "generated"}
    assert nodes[0].name == "wall_decal/0" and nodes[0].extras == {
        "type": "wall_decal", "decal": "gen_rust_drip", "kind": "rust", "wall": "w1", "attribute_origin": "generated"}


SEED_MOUFFETARD = 1043396788
SEED_NULL_ISLAND = 1382503250
SEED_ROUNDED = 396029759
DRAWN_HASHES = {"gen_damp_stain": "f3501cfe3d42", "gen_soot_streak": "f862dbd66ee9", "gen_rust_drip": "bb56a372c739",
                "gen_plaster_crack": "15117665b2f2", "gen_torn_poster": "06711467ae46", "gen_paint_scribble": "811451e3d45b",
                "gen_splash_grime": "1bb0d559c78a"}


def test_the_map_seed_is_its_centre() -> None:
    from onward_generator.signs.place import _seed

    assert _seed({"frame": {"origin_wgs84": [2.34981, 48.84045]}}) == SEED_MOUFFETARD
    assert _seed({}) == _seed({"frame": {}}) == SEED_NULL_ISLAND


def test_the_decal_schema_and_records_are_whole() -> None:
    pool = procedural_decals(1)[:4]
    assert schema(pool) == {
        "type": "object", "additionalProperties": False, "required": ["pick_version", "decal_ids"],
        "properties": {"pick_version": {"const": "0.1.0"}, "decal_ids": {"type": "array", "items": {"enum": sorted(d.id for d in pool)},
                                                                          "uniqueItems": True, "minItems": 4, "maxItems": 12}}}
    ids = [d.id for d in procedural_decals(1)]
    picked = pick_decals(procedural_decals(1), brief="b", provider=_Replies(_reply(*ids[:6])))
    assert set(picked.record) == {"pick_version", "instruction_sha256", "origin", "raw", "decal_ids", "reason", "attempts"}
    assert (picked.record["pick_version"], picked.record["origin"], picked.record["reason"], picked.record["attempts"]) == ("0.1.0", "ai_generated", None, 1)
    assert picked.record["raw"] == _reply(*ids[:6]) and len(picked.record["instruction_sha256"]) == 64
    assert picked.record["decal_ids"] == ids[:6]


def _png_bytes(size: tuple[int, int], alpha_left: int, mode: str = "RGBA", colour: tuple[int, int, int] = (120, 60, 40)) -> bytes:
    image = Image.new("RGBA", size, (*colour, 255))
    image.paste((0, 0, 0, alpha_left), (0, 0, size[0] // 2, size[1]))
    out = io.BytesIO()
    image.convert(mode).save(out, format="PNG")
    return out.getvalue()


def _tar(path: Path, members: dict[str, tuple[bytes, bytes]], prefix: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:gz") as archive:
        for guid, (pathname, data) in members.items():
            for name, payload in (("pathname", pathname), ("asset", data)):
                info = tarfile.TarInfo(f"{prefix}{guid}/{name}")
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
    return path


def _decal_folder_package(tmp_path: Path, members: dict[str, tuple[bytes, bytes]], name: str = "Bricks Decals FREE", prefix: str = ""):
    path = _tar(tmp_path / "cache" / "Pub" / "Textures MaterialsBricks" / f"{name}.unitypackage", members, prefix)
    return next(p for p in find_packages(tmp_path / "cache") if p.path == path)


def test_the_decal_import_keeps_going_past_a_bad_image_and_names_each_file(tmp_path: Path) -> None:
    long_stem = "Decal-Wall " + "x" * 90
    package = _decal_folder_package(tmp_path, {
        "a" * 32: (b"Assets/Decals/Decals_A.png", b"broken"),
        "b" * 32: (b"Assets/Decals/Decals_B.png", _png_bytes((64, 64), 255)),  # opaque: a texture, not a decal
        "c" * 32: (b"Assets/Decals/Decals C-1.png", _png_bytes((800, 100), 0)),  # 8:1, half transparent
        "d" * 32: (f"Assets/Decals/{long_stem}.png".encode(), _png_bytes((100, 800), 0)),
        "e" * 32: (b"Assets/Decals/Decals_E.png", _png_bytes((100, 100), 0, mode="RGB")),  # no alpha at all
    }, prefix="./")
    rows = decal_rows(package, tmp_path / "assets")
    by_file = {row["file"]: row for row in rows}
    assert sorted(by_file) == ["Decal_Wall_" + "x" * 90 + ".png", "Decals_C_1.png"]
    wide, tall = by_file["Decals_C_1.png"], by_file["Decal_Wall_" + "x" * 90 + ".png"]
    assert (wide["width_m"], wide["height_m"]) == (3.2, 0.566) and (tall["width_m"], tall["height_m"]) == (0.566, 3.2)
    assert wide["id"] == "as_bricks_decals_free_Decals_C_1" and len(tall["id"]) == 80 and tall["id"].startswith("as_bricks_decals_free_Decal_Wall_x")
    with Image.open(tmp_path / "assets" / "asset_store_bricks_decals_free" / "Decals_C_1.png") as image:
        assert image.size == (512, 64)
        expected = Image.open(io.BytesIO(_png_bytes((800, 100), 0))).convert("RGBA")
        expected.thumbnail((512, 512), Image.Resampling.LANCZOS)
        assert list(image.getdata()) == list(expected.getdata())
    folder = tmp_path / "assets" / "asset_store_bricks_decals_free"
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["source"] == ("Unity Asset Store EULA (Extension Asset), local only: Pub / Bricks Decals FREE. Converted on this machine; "
                                  "never committed or redistributed outside a built map.")
    assert (folder / "License.txt").read_text(encoding="utf-8") == (
        "Bricks Decals FREE by Pub, from the Unity Asset Store, under the Standard Unity Asset Store EULA.\n"
        "Local copy for this user's maps only. Do not commit, share or publish these files.\n")


def test_a_decal_is_judged_by_its_share_of_transparent_pixels(tmp_path: Path) -> None:
    def share(alpha: int, transparent_columns: int) -> list[dict]:
        image = Image.new("RGBA", (100, 50), (120, 60, 40, 255))
        image.paste((0, 0, 0, alpha), (0, 0, transparent_columns, 50))
        out = io.BytesIO()
        image.save(out, format="PNG")
        package = _decal_folder_package(tmp_path / f"{alpha}_{transparent_columns}", {"a" * 32: (b"Assets/Decals/Decals_A.png", out.getvalue())})
        return decal_rows(package, tmp_path / f"{alpha}_{transparent_columns}" / "assets")

    assert share(0, 5) != [] and share(0, 4) == []  # 5 % of the pixels transparent is a decal, 4 % is not
    assert share(249, 50) != [] and share(250, 50) == []  # alpha 250 and over is opaque


def test_the_decal_catalogues_are_read_past_broken_ones_and_sorted_by_id(tmp_path: Path) -> None:
    def catalogue(name: str, rows: list[dict], images: tuple[str, ...]) -> None:
        folder = tmp_path / name
        folder.mkdir()
        (folder / "manifest.json").write_text(json.dumps({"schema": DECALS_SCHEMA, "assets": rows}), encoding="utf-8")
        for image in images:
            Image.new("RGB", (4, 2), (1, 2, 3)).save(folder / image)

    (tmp_path / "asset_store_a_broken").mkdir()
    (tmp_path / "asset_store_a_broken" / "manifest.json").write_text("{", encoding="utf-8")
    catalogue("asset_store_b", [{"id": "z", "file": "z.png", "width_m": 1.0, "height_m": 0.5}, {"id": "missing", "file": "none.png"},
                                {"id": "nofile"}], ("z.png",))
    catalogue("asset_store_c", [{"id": "a", "file": "a.png", "kind": "brick_patch", "description": "d", "width_m": 2.0, "height_m": 1.0}], ("a.png",))
    assert [row["id"] for _, row in read_decal_catalogues(tmp_path)] == ["a", "z"]
    loaded = library_decals(tmp_path)
    assert [(d.id, d.kind, d.width_m, d.height_m, d.description, d.origin) for d in loaded] == [
        ("a", "brick_patch", 2.0, 1.0, "d", "library"), ("z", "decal", 1.0, 0.5, "", "library")]
    assert loaded[0].rgba.dtype == np.uint8 and loaded[0].rgba.shape == (2, 4, 4) and loaded[0].rgba[0, 0].tolist() == [1, 2, 3, 255]


def _oblique(fid: str, a: tuple[float, float], b: tuple[float, float], height: float | None) -> Wall:
    start, end = np.array(a, float), np.array(b, float)
    d = (end - start) / np.linalg.norm(end - start)
    return Wall(fid, start, end, np.array([d[1], -d[0]]), height)


def _quad(p0, p1, z0: float, z1: float, material: str) -> Primitive:
    v = np.array([[*p0, z0], [*p1, z0], [*p1, z1], [*p0, z1]], float)
    return Primitive(Mesh(v, np.array([[0, 1, 2], [0, 2, 3]])), material)


def test_a_street_of_slanted_and_short_walls_is_pinned() -> None:
    walls = [_oblique("w1", (3.3, 1.7), (17.9, 4.2), 8.4), _oblique("w2", (25.0, -2.0), (25.0, 9.5), None),
             _oblique("w3", (40.0, 0.0), (42.6, 0.0), 9.0), _oblique("w4", (50.0, 0.0), (52.4, 0.0), 9.0)]
    d1 = (np.array([17.9, 4.2]) - np.array([3.3, 1.7])) / np.linalg.norm(np.array([17.9, 4.2]) - np.array([3.3, 1.7]))
    p0, p1 = np.array([3.3, 1.7]) + d1 * 4, np.array([3.3, 1.7]) + d1 * 8
    nodes = [Node("b", (_quad(p0, p1, 1.0, 2.6, "glass_dark"), _quad((25.05, 1.0), (25.05, 3.0), 0.0, 2.2, "door_wood"),
                        _quad((25.05, 6), (25.05, 7), 3, 4, "brick")), {})]
    pool = [*procedural_decals(7), Decal("long", "banner", 4.0, 0.4, np.zeros((4, 40, 4), np.uint8), "generated", "x")]
    placed = place_decals(walls, NOTHING, decal_blocker(nodes, [(box(24.5, 3.0, 25.5, 5.0), 2.0, 3.0)]), pool, 23, 14)
    assert [(p.decal.id, p.wall.feature_id, round(p.s_m, 4), round(p.z_m, 4)) for p in placed] == [
        ("gen_damp_stain", "w2", 8.5577, 1.4), ("gen_splash_grime", "w1", 9.854, 1.4), ("gen_torn_poster", "w3", 1.6287, 1.4),
        ("gen_soot_streak", "w2", 10.6075, 1.4), ("long", "w1", 10.6266, 2.4), ("gen_plaster_crack", "w3", 1.8392, 3.6),
        ("gen_paint_scribble", "w2", 0.9445, 1.4), ("gen_rust_drip", "w1", 13.7846, 1.4), ("gen_damp_stain", "w2", 6.1529, 0.9),
        ("gen_splash_grime", "w1", 1.4135, 1.4), ("gen_torn_poster", "w2", 10.7201, 3.6), ("gen_soot_streak", "w1", 13.779, 3.6),
        ("long", "w2", 4.2601, 3.6), ("gen_plaster_crack", "w1", 0.7823, 3.6)]
    nodes_out, materials, images, _ = decal_nodes(placed)
    assert [n.primitives[0].mesh.vertices.round(3).tolist() for n in nodes_out[:4]] == [
        [[25.01, 5.858, 0.7], [25.01, 7.258, 0.7], [25.01, 7.258, 2.1], [25.01, 5.858, 2.1]],
        [[11.832, 3.151, 1.0], [14.197, 3.556, 1.0], [14.197, 3.556, 1.8], [11.832, 3.151, 1.8]],
        [[41.279, -0.01, 0.9], [41.979, -0.01, 0.9], [41.979, -0.01, 1.9], [41.279, -0.01, 1.9]],
        [[25.01, 8.108, 0.4], [25.01, 9.108, 0.4], [25.01, 9.108, 2.4], [25.01, 8.108, 2.4]]]
    assert [n.primitives[0].mesh.triangles.tolist() for n in nodes_out[:1]] == [[[0, 1, 2], [0, 2, 3]]]
    assert [n.primitives[0].uvs.round(5).tolist() for n in nodes_out[:2]] == [
        [[0.00195, 0.87305], [0.12695, 0.87305], [0.12695, 0.99805], [0.00195, 0.99805]],
        [[0.19727, 0.87305], [0.32227, 0.87305], [0.32227, 0.99805], [0.19727, 0.99805]]]
    assert {k: hashlib.sha256(v).hexdigest()[:16] for k, v in images.items()} == {"wall_decal_atlas_0": "bfba4218f58523f4"}
    assert {k: (m.color, m.roughness) for k, m in materials.items()} == {"wall_decal_atlas_0": ((1.0, 1.0, 1.0, 1.0), 0.8)}


def test_a_decal_fits_its_wall_and_its_band_to_the_centimetre() -> None:
    from onward_generator.signs.decals import _fit

    one = _decal("one", 1.0, 1.0)
    assert _fit(_wall(0, 1.8), one, never, [], 1) is not None  # exactly its width and a gap each side
    assert _fit(_wall(0, 1.79), one, never, [], 1) is None
    tall = _decal("tall", 1.0, 1.5)
    assert _fit(_wall(0, 10, height=2.65), tall, never, [], 1)[1] == 1.4  # its top exactly at the wall top less 0.5 m
    assert _fit(_wall(0, 10, height=2.6), tall, never, [], 1)[1] == 0.9  # its foot exactly 0.15 m off the ground
    assert _fit(_wall(0, 10, height=1.0), tall, never, [], 1) is None


def test_the_blocker_reads_only_what_a_decal_must_not_cover_and_open_bands() -> None:
    brick_first = Node("b", (_quad((0, 0.05), (10, 0.05), 0.0, 9.0, "brick"), _quad((0, 0.05), (10, 0.05), 2.0, 3.0, "glass_dark")), {})
    blocked = decal_blocker([brick_first])
    plan = box(4, -0.5, 6, 0.5)
    assert blocked(plan, 1.0, 2.0001) and blocked(plan, 2.9999, 4.0)
    assert not blocked(plan, 1.0, 2.0) and not blocked(plan, 3.0, 4.0)  # touching the glass is not covering it
    assert not decal_blocker([Node("b", (_quad((0, 0.05), (10, 0.05), 0.0, 9.0, "brick"),), {})])(plan, 0.0, 9.0)
    assert not blocked(box(20, -0.5, 22, 0.5), 0.0, 9.0)
    assert decal_blocker([], [(box(0, 0, 1, 1), 0.0, 1.0)])(box(0.5, 0.5, 2, 2), 0.5, 0.6)


def test_the_pick_sends_its_prompt_and_schema_and_keeps_whole_records(monkeypatch: pytest.MonkeyPatch) -> None:
    from onward_generator.signs import decal_pick as pick_module

    pool = procedural_decals(1)
    ids = [d.id for d in pool]
    asked = _Replies(_reply(*ids[:6]))
    picked = pick_decals(pool, brief="Muros quemados", provider=asked)
    system, message, sent = asked.calls[0]
    assert system == pick_module.SYSTEM_PROMPT and sent == schema(pool) and message == pick_module.instruction("Muros quemados", pool)
    assert picked.ok is True
    rows = json.loads(message.split("Decals:\n", 1)[1])
    assert rows[0] == {"id": "gen_damp_stain", "kind": "stain", "description": "A brown damp stain spreading on a wall."}
    replayed = pick_decals(pool, brief="Muros quemados", provider=_Replies(), replay=picked.record)
    assert replayed.ok is True and replayed.record == {**picked.record, "origin": "replay", "attempts": 0}
    again = pick_decals(pool, brief="Muros quemados", provider=_Replies(), replay=replayed.record)  # a replay replays too
    assert again.decal_ids == picked.decal_ids
    failed = pick_decals(pool, brief="Muros quemados", provider=_Replies(ProviderError("a"), ProviderError("b")))
    assert failed.ok is False and failed.record == {**picked.record, "origin": "rules_fallback", "raw": "", "decal_ids": sorted(ids),
                                                    "reason": "b", "attempts": 2}
    made: list[float] = []

    class _Fake:
        name = "fake"

        def __init__(self, *, timeout_s: float) -> None:
            made.append(timeout_s)

        def complete(self, system: str, instruction: str, schema: dict) -> ProviderResult:
            raise ProviderError("offline")

    import onward_generator.ai.providers as providers

    monkeypatch.setattr(providers, "OllamaProvider", _Fake)
    assert not pick_decals(pool, brief="").ok and made == [180.0]


def test_the_pick_bounds_and_messages() -> None:
    pool = procedural_decals(1) + [Decal(f"x{k}", "extra", 1, 1, np.zeros((2, 2, 4), np.uint8), "generated") for k in range(8)]
    ids = [d.id for d in pool]
    assert len(parse(_reply(*ids[:12]), pool)) == 12
    with pytest.raises(DecalPickError) as error:
        parse(_reply(*ids[:13]), pool)
    assert str(error.value) == "choose from 6 to 12 ids, not 13"
    three_kinds = ["gen_damp_stain", "gen_soot_streak", "gen_rust_drip", "x0", "x1", "x2"]
    assert parse(_reply(*three_kinds), pool) == tuple(three_kinds)
    for text, message in (
        (json.dumps({"pick_version": "0.1.0", "decal_ids": "x"}), "decal_ids must be a list of ids"),
        (_reply(*ids[:4], "zz", "aa"), "ids that are not offered: aa, zz"),
        (_reply(*ids[:5], ids[0]), "each id may be chosen once"),
        (_reply("x0", "x1", "x2", "x3", "x4", "gen_damp_stain"), "choose ids of at least 3 kinds"),
    ):
        with pytest.raises(DecalPickError) as error:
            parse(text, pool)
        assert str(error.value) == message


def test_the_seed_rounds_the_centre_to_the_micro_degree_and_kinds_count_exactly() -> None:
    from onward_generator.signs.place import _seed

    assert _seed({"frame": {"origin_wgs84": [2.3498149, 48.8404549]}}) == SEED_ROUNDED
    pool = procedural_decals(1) + [Decal(f"x{k}", "extra", 1, 1, np.zeros((2, 2, 4), np.uint8), "generated") for k in range(4)]
    exactly_three = ["gen_damp_stain", "gen_soot_streak", "x0", "x1", "x2", "x3"]
    assert parse(_reply(*exactly_three), pool) == tuple(exactly_three)


def test_a_decal_that_fits_nowhere_is_skipped_and_the_others_still_go() -> None:
    huge = _decal("huge", 50.0, 1.0)
    placed = place_decals([_wall(0, 2.5, fid="exact"), _wall(10, 20, fid="long")], NOTHING, never, [huge, _decal("a"), _decal("b")], 3, 3)
    assert {p.decal.id for p in placed} == {"a", "b"} and len(placed) == 2
    assert place_decals([_wall(0, 2.49)], NOTHING, never, [_decal("a")], 3, 1) == []
    assert len(place_decals([_wall(0, 2.5)], NOTHING, never, [_decal("a")], 3, 1)) == 1  # 2.5 m is long enough


def test_glass_well_behind_the_wall_does_not_block_a_decal() -> None:
    from onward_generator.signs.decals import _fit

    inside = Node("b", (_quad((0, 1.0), (10, 1.0), 0.0, 9.0, "glass_view"),), {})  # a room's pane 1 m behind the wall
    assert _fit(_wall(0, 10), _decal("a"), decal_blocker([inside]), [], 1) is not None
    just_behind = Node("b", (_quad((0, 0.2), (10, 0.2), 0.0, 9.0, "glass_dark"),), {})  # the window in the wall
    assert _fit(_wall(0, 10), _decal("a"), decal_blocker([just_behind]), [], 1) is None


def test_a_long_package_name_is_cut_in_the_decal_id(tmp_path: Path) -> None:
    package = _decal_folder_package(tmp_path, {"a" * 32: (b"Assets/Decals/Decals_A.png", _png_bytes((64, 64), 0))},
                                    name="Bricks Decals FREE Extended Collection")
    assert [row["id"] for row in decal_rows(package, tmp_path / "assets")] == ["as_bricks_decals_free_e_Decals_A"]  # the package id cut to 20 characters


def test_a_catalogue_of_another_schema_does_not_stop_the_reading(tmp_path: Path) -> None:
    (tmp_path / "asset_store_a_models").mkdir()
    (tmp_path / "asset_store_a_models" / "manifest.json").write_text(json.dumps({"schema": "x", "assets": []}), encoding="utf-8")
    (tmp_path / "asset_store_b").mkdir()
    Image.new("RGBA", (2, 2)).save(tmp_path / "asset_store_b" / "a.png")
    (tmp_path / "asset_store_b" / "manifest.json").write_text(json.dumps({"schema": DECALS_SCHEMA, "assets": [
        {"id": "a", "file": "a.png", "width_m": 1, "height_m": 1}]}), encoding="utf-8")
    assert [r["id"] for _, r in read_decal_catalogues(tmp_path)] == ["a"]


def test_a_decal_on_a_cramped_wall_tries_two_positions_at_each_height() -> None:
    from onward_generator.signs.decals import HEIGHTS_M, _fit

    asked: list[float] = []

    def always(plan: Polygon, bottom: float, top: float) -> bool:
        asked.append(round(bottom, 3))
        return True

    assert _fit(_wall(0, 2.5), _decal("a", 1.6, 0.4), always, [], 1) is None  # 0.9 m to spare: one step of 0.5 m
    assert len(asked) == 2 * len(HEIGHTS_M)


def test_what_stands_in_front_of_a_wall_beyond_a_hand_does_not_block_a_decal() -> None:
    from onward_generator.signs.decals import _fit

    a_metre_out = Node("p", (_quad((0, -1.0), (10, -1.0), 0.0, 9.0, "glass_dark"),), {})  # outward is -y
    assert _fit(_wall(0, 10), _decal("a"), decal_blocker([a_metre_out]), [], 1) is not None
    a_hand_out = Node("p", (_quad((0, -0.2), (10, -0.2), 0.0, 9.0, "glass_dark"),), {})
    assert _fit(_wall(0, 10), _decal("a"), decal_blocker([a_hand_out]), [], 1) is None


def test_the_blocker_tests_the_plan_itself_not_its_bounding_box() -> None:
    pane = Node("p", (_quad((1.4, 0.05), (1.6, 0.05), 0.0, 9.0, "glass_dark"),), {})
    slanted = Polygon([(1, 0), (2, 1), (1.9, 1.1), (0.9, 0.1)])  # its box holds the pane, the strip does not
    assert not decal_blocker([pane])(slanted, 0.0, 9.0)
    assert decal_blocker([pane])(box(1.3, 0.0, 1.7, 0.1), 0.0, 9.0)


def test_a_decal_taken_on_a_wall_keeps_its_whole_height_clear() -> None:
    wall = _wall(0, 2.8)
    first, second = _decal("first", 2.0, 0.1), _decal("second", 2.0, 0.08)
    assert [p.decal.id for p in place_decals([wall], NOTHING, never, [first, second], 4, 2)] == ["first", "second"]
    placed = place_decals([wall], NOTHING, never, [first, second], 4, 2)
    assert [(p.decal.id, p.z_m) for p in placed] == [("first", 1.4), ("second", 0.9)]  # just below it, a gap clear

    def low(plan: Polygon, bottom: float, top: float) -> bool:
        return bottom < 0.5 and top > 0.3

    tall, short = _decal("first", 2.0, 1.5), _decal("second", 2.0, 0.12)
    stacked = place_decals([_wall(0, 2.8, height=5.2)], NOTHING, low, [tall, short], 4, 2)
    assert [(p.decal.id, p.z_m) for p in stacked] == [("first", 2.4)]  # the short one would touch its foot at 1.4 m


def test_a_large_library_image_is_scaled_into_its_cell_smoothly() -> None:
    from onward_generator.signs.decals import INNER_PX, _atlas

    rng = np.random.default_rng(5)
    big = Decal("big", "x", 1.0, 0.5, rng.integers(0, 256, (600, 1200, 4), dtype=np.uint8), "library")
    pages, _ = _atlas([big])
    cell = pages["wall_decal_atlas_0"][4:4 + INNER_PX // 2, 4:4 + INNER_PX]
    expected = Image.fromarray(big.rgba)
    expected.thumbnail((INNER_PX, INNER_PX), Image.Resampling.LANCZOS)
    assert INNER_PX == 392 and cell.shape == (196, 392, 4) and np.array_equal(cell, np.asarray(expected))


def test_the_drawn_decals_are_pinned_to_the_pixel() -> None:
    assert {d.id: hashlib.sha256(d.rgba.tobytes()).hexdigest()[:12] for d in procedural_decals(7)} == DRAWN_HASHES


def test_the_posters_of_twelve_maps_are_pinned_to_the_pixel() -> None:
    # One seed can draw the same paper colour from a slightly different range; twelve cannot.
    posters = b"".join(d.rgba.tobytes() for s in range(1, 13) for d in procedural_decals(s) if d.id == "gen_torn_poster")
    assert hashlib.sha256(posters).hexdigest()[:16] == "ab0133642b15675f"


def _facade(*extra: Primitive, y: float = 0.0) -> Node:
    """A south wall from x 0 to 10 at ``y``, 12 m high, facing -y, and ``extra``."""
    return Node("b", (_quad((10, y), (0, y), 0.0, 12.0, "plaster_white"), *extra), {})


def test_the_surface_lays_a_decal_on_the_facade_where_it_stands() -> None:
    from onward_generator.signs.decals import FLAT_M, PROBE_OUT_M, PROBE_REACH_M, PROBE_STEP_M, wall_surface

    assert (PROBE_OUT_M, PROBE_REACH_M, PROBE_STEP_M, FLAT_M) == (0.6, 0.5, 0.3, 0.02)
    assert wall_surface([_facade()])(_wall(0, 10), 5.0, 2.0, 1.6, 1.2) == pytest.approx(0.0)
    assert wall_surface([_facade(y=0.3)])(_wall(0, 10), 5.0, 2.0, 1.6, 1.2) == pytest.approx(-0.3)  # built 0.3 m inside the line
    assert wall_surface([_facade(y=-0.2)])(_wall(0, 10), 5.0, 2.0, 1.6, 1.2) == pytest.approx(0.2)
    assert wall_surface([_facade(y=0.6)])(_wall(0, 10), 5.0, 2.0, 1.6, 1.2) is None  # beyond the reach: not this wall
    assert wall_surface([])(_wall(0, 10), 5.0, 2.0, 1.6, 1.2) is None


def test_the_surface_refuses_a_recess_flush_glass_an_edge_and_a_prop_in_front() -> None:
    from onward_generator.signs.decals import wall_surface

    wall = _wall(0, 10)
    recess = _quad((5.6, 0.15), (4.4, 0.15), 1.6, 2.6, "facade_trim_haussmann")  # one material, 0.15 m deep: a window back
    hole = Node("b", (_quad((10, 0), (5.6, 0), 0.0, 12.0, "plaster_white"), _quad((4.4, 0), (0, 0), 0.0, 12.0, "plaster_white"),
                      _quad((5.6, 0), (4.4, 0), 0.0, 1.6, "plaster_white"), _quad((5.6, 0), (4.4, 0), 2.6, 12.0, "plaster_white"),
                      recess), {})
    assert wall_surface([hole])(wall, 5.0, 2.0, 1.6, 1.2) is None
    assert wall_surface([hole])(wall, 2.0, 2.0, 1.6, 1.2) == pytest.approx(0.0)  # beside it, plain wall
    flush = _facade(_quad((5.6, -0.001), (4.4, -0.001), 1.6, 2.6, "glass_dark"))  # a pane on the face itself
    assert wall_surface([flush])(wall, 5.0, 2.0, 1.6, 1.2) is None
    assert wall_surface([_facade()])(wall, 9.5, 2.0, 1.6, 1.2) is None  # past the wall's end: half in the air
    lamp = _facade(_quad((5.1, -0.4), (4.9, -0.4), 0.0, 3.0, "lamp_metal"))
    assert wall_surface([lamp])(wall, 5.0, 2.0, 1.6, 1.2) is None
    narrow = _facade(_quad((5.15, -0.001), (4.85, -0.001), 1.5, 2.5, "glass_dark"))  # 0.3 m wide: still met by a ray
    assert wall_surface([narrow])(wall, 5.0, 2.0, 1.6, 1.2) is None


def test_a_decal_is_laid_on_the_facade_depth_the_surface_gives() -> None:
    placed = place_decals([_wall(0, 10)], NOTHING, never, [_decal("a")], 3, 1, lambda w, s, z, width, height: -0.25)
    assert len(placed) == 1 and placed[0].depth_m == -0.25
    nodes, _, _, rows = decal_nodes(placed)
    assert np.allclose(nodes[0].primitives[0].mesh.vertices[:, 1], 0.24)  # 0.25 m in, 1 cm proud of the face; outward is -y
    assert rows[0]["position_m"][1] == 0.24
    assert place_decals([_wall(0, 10)], NOTHING, never, [_decal("a")], 3, 1, lambda w, s, z, width, height: None) == []


def test_the_decals_read_the_openings_of_the_scene_the_trim_sheet_was_baked_from(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from onward_generator.generation.config import Material
    from onward_generator.generation.gltf import build_glb
    from onward_generator.signs import place as place_module

    trimmed = [Node("t", (_quad((0, 0), (1, 0), 0, 1, "facade_trim_x"),), {})]
    base = tmp_path / "scene_materials"
    base.mkdir()
    assert place_module.untrimmed(base, trimmed) is trimmed  # no materials stage
    dress = tmp_path / "scene_dress"
    dress.mkdir()
    (dress / "scene_visual.glb").write_bytes(build_glb([Node("d", (_quad((0, 0), (1, 0), 0, 1, "glass_dark"),), {})],
                                                       {"glass_dark": Material((0.1, 0.1, 0.1, 1.0), 0.2)}))
    (base / "materials_report.json").write_text(json.dumps({"base_dir": str(dress)}), encoding="utf-8")
    assert [p.material for n in place_module.untrimmed(base, trimmed) for p in n.primitives] == ["glass_dark"]
    monkeypatch.setattr(place_module, "REPO_ROOT", tmp_path)
    (base / "materials_report.json").write_text(json.dumps({"base_dir": "/work/scene_dress"}), encoding="utf-8")
    assert [p.material for n in place_module.untrimmed(base, trimmed) for p in n.primitives] == ["glass_dark"]  # a container path
    (base / "materials_report.json").write_text(json.dumps({"base_dir": "/work/gone"}), encoding="utf-8")
    assert place_module.untrimmed(base, trimmed) is trimmed
    (base / "materials_report.json").write_text(json.dumps({}), encoding="utf-8")
    assert place_module.untrimmed(base, trimmed) is trimmed


def _tiled(x0: float, x1: float, top: float, tile: float = 0.5) -> Node:
    """A south wall at y 0 from ``x0`` to ``x1`` and up to ``top``, built of ``tile`` squares facing -y."""
    prims = []
    for i in range(round((x1 - x0) / tile)):
        for j in range(round(top / tile)):
            a = x0 + i * tile
            prims.append(_quad((a + tile, 0), (a, 0), j * tile, (j + 1) * tile, "plaster_white"))
    return Node("tiles", tuple(prims), {})


def test_the_probe_reads_a_wall_of_small_faces_away_from_the_origin_to_its_edges() -> None:
    from onward_generator.signs.decals import wall_surface

    wall = _wall(20, 30, height=4.0)
    surface = wall_surface([_tiled(20, 30, 4.0)])
    assert surface(wall, 5.0, 2.0, 1.6, 1.2) == pytest.approx(0.0)
    assert surface(wall, 9.2, 2.0, 1.6, 1.2) == pytest.approx(0.0)  # up to the wall's end
    assert surface(wall, 9.25, 2.0, 1.6, 1.2) is None  # 5 cm past it
    assert surface(wall, 5.0, 3.4, 1.6, 1.2) == pytest.approx(0.0)  # up to the wall's top
    assert surface(wall, 5.0, 3.45, 1.6, 1.2) is None
    assert surface(wall, 0.8, 0.6, 1.6, 1.2) == pytest.approx(0.0)  # from its start and its foot


def test_the_probe_grid_is_dense_enough_to_meet_a_thin_pane() -> None:
    from onward_generator.signs.decals import wall_surface

    wall = _wall(20, 30)
    strip = wall_surface([_facade_at(20, 30, _quad((24.50, -0.001), (24.46, -0.001), 0.0, 12.0, "glass_dark"))])
    assert strip(wall, 5.0, 2.0, 1.6, 1.2) is None  # seven rays across 1.6 m, 0.26 m apart, meet a 4 cm pane 0.52 m left of centre
    centre = wall_surface([_facade_at(20, 30, _quad((25.025, -0.001), (24.975, -0.001), 0.0, 12.0, "glass_dark"))])
    assert centre(wall, 5.0, 2.0, 0.2, 0.2) is not None  # two rays across a 20 cm decal straddle a 5 cm pane
    row = wall_surface([_facade_at(20, 30, _quad((30, -0.001), (20, -0.001), 1.68, 1.75, "glass_dark"))])
    assert row(wall, 5.0, 2.0, 1.6, 1.2) is None  # five rows up 1.2 m, 0.29 m apart, meet a 7 cm band at the second row


def _facade_at(x0: float, x1: float, *extra: Primitive) -> Node:
    return Node("b", (_quad((x1, 0), (x0, 0), 0.0, 12.0, "plaster_white"), *extra), {})
