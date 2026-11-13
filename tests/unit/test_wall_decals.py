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


def test_a_decal_package_becomes_a_local_catalogue_of_its_transparent_albedos(tmp_path: Path) -> None:
    _decal_package(tmp_path / "cache")
    package = find_packages(tmp_path / "cache")[0]
    assets = tmp_path / "assets"
    rows = decal_rows(package, assets)
    assert [r["id"] for r in rows] == ["as_bricks_decals_free_Decals_Bricks_01"]  # no normal map, no opaque texture
    row = rows[0]
    assert row["kind"] == "brick_patch" and row["license"] == "LicenseRef-Unity-Asset-Store-EULA"
    assert row["width_m"] == pytest.approx(1.6 * 2 ** 0.5, abs=1e-3) and row["height_m"] == pytest.approx(1.6 / 2 ** 0.5, abs=1e-3)
    folder = assets / "asset_store_bricks_decals_free"
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema"] == DECALS_SCHEMA and "Do not commit" in (folder / "License.txt").read_text(encoding="utf-8")
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
