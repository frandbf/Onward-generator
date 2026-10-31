"""Shop signs and vinyls drawn by ``signs place`` from the detail manifest (local metres)."""

from __future__ import annotations

import hashlib
from dataclasses import replace

import numpy as np
import pytest

from onward_generator.detail.config import load_detail_config
from onward_generator.detail.facades import Hole, Segment
from onward_generator.detail.shops import plan_shops
from onward_generator.dress.atlas import decode_png_rgba
from onward_generator.signs import shop
from onward_generator.signs.shop import (
    ATLAS_PX,
    BOARD_COLOURS,
    PAD_PX,
    VINYL_COLOURS,
    Item,
    Label,
    _page_height,
    board_quads,
    label_for,
    label_rgba,
    pack,
    shop_items,
    shop_signs,
)

CFG = load_detail_config()
SEG = Segment(0, 0, 0, (0.0, 0.0), (12.0, 0.0), True)


def _front(s0: float, column: int) -> Hole:
    return Hole("shopfront", 0, s0, 0.25, 3.0, 2.3, 0.15, "glass_dark", column, 0)


def _shops(fascia: float, building: str = "b1") -> list[dict]:
    cfg = replace(CFG, shops=replace(CFG.shops, fascia_share=fascia, vinyl_share=1.0))
    return plan_shops(building, [("n1", {"shop": "bakery"}, (2.0, -1.0))], [_front(1.0, 0), _front(5.0, 1)], [SEG], cfg)


def _manifest() -> dict:
    return {"objects": [
        {"id": "street/1", "type": "surface", "facade": {"shops": _shops(1.0, "x")}},
        {"id": "building/b1", "type": "building", "source_ids": ["b1"], "facade": {"shops": _shops(1.0, "b1")}},
        {"id": "building/b2", "type": "building", "source_ids": ["b2"], "facade": {"shops": _shops(0.0, "b2")}},
        {"id": "building/b3", "type": "building", "source_ids": ["b3"], "facade": {"shops": []}},
        {"id": "building/b4", "type": "building", "source_ids": ["b4"]},
    ]}


def _area(mesh) -> float:
    v = mesh.vertices[mesh.triangles]
    return float(np.linalg.norm(np.cross(v[:, 1] - v[:, 0], v[:, 2] - v[:, 0]), axis=1).sum() / 2)


def _normal(quad: np.ndarray) -> np.ndarray:
    n = np.cross(quad[1] - quad[0], quad[2] - quad[0])
    return n / np.linalg.norm(n)


# --- Labels -------------------------------------------------------------------------------


def test_label_size_and_colours() -> None:
    board = {"width_m": 3.3, "height_m": 0.36}
    assert label_for("board", " Horno   de San Juan ", board) == Label("board", "Horno de San Juan", 660, 72)
    assert Label("vinyl", "Bar", 10, 10).colours() == VINYL_COLOURS
    for text in ("Bar", "Panadería", "Farmacia"):
        index = hashlib.sha256(text.encode("utf-8")).digest()[0] % len(BOARD_COLOURS)
        assert Label("board", text, 10, 10).colours() == BOARD_COLOURS[index]
    assert len({Label("board", t, 10, 10).colours() for t in ("Bar", "Panadería", "Farmacia", "Banco", "Tienda")}) > 1


@pytest.mark.parametrize(
    ("label", "digest"),
    [
        (Label("board", "Panadería", 660, 72), "40dedef11a05d67ee543b81823c22e78a6c919f1d93ffd7459f854469705875b"),
        (Label("board", "Farmacia Ñoño Pérez de la Fuente", 660, 72), "3ada25ba0ad265b49608082d36bd4504f08650476b1af337b30a4a1636f5533d"),
        (Label("board", "Bar", 140, 100), "713218449c387f15e6ced3fbe4af874813ad21b7959e9fd11fc52b4529a05a4a"),
        (Label("vinyl", "Panadería", 480, 52), "1a82a72f00c8713469b99b89808b2936be5627fa6617009009ea9fabba38e127"),
        (Label("board", "Comercio de prueba", 40, 16), "03843a9703c0cae35fceb323ad6cf1872f3e2b34f615a32ac6c12d0dfcfcbd4c"),
        (Label("board", "Comercio de prueba", 40, 8), "6637fc37178d76be33dc7c81f7c4a6e78e4fe862f1c08e086e8300ec0f748dc2"),
    ],
)
def test_label_pixels_are_reproducible(label: Label, digest: str) -> None:
    assert hashlib.sha256(label_rgba(label).tobytes()).hexdigest() == digest


@pytest.mark.parametrize("text", ["Bar", "Farmacia Ñoño Pérez de la Fuente", "Panadería y Bollería Artesana Hermanos García"])
def test_label_text_fits_inside_its_margin(text: str) -> None:
    label = Label("board", text, 400, 64)
    background, _ = label.colours()
    pixels = label_rgba(label)
    assert pixels.shape == (64 + 2 * PAD_PX, 400 + 2 * PAD_PX, 4)
    ink = np.any(pixels[..., :3] != np.array(background, dtype=np.uint8), axis=2)
    assert (pixels[..., 3] == 255).all()
    rows, cols = np.nonzero(ink)
    margin = 64 // 8
    assert rows.min() >= PAD_PX + margin - 1 and rows.max() <= PAD_PX + 64 - margin
    assert cols.min() >= PAD_PX + margin - 1 and cols.max() <= PAD_PX + 400 - margin
    assert rows.max() - rows.min() >= 12  # the text is not shrunk to nothing


# --- Packing -------------------------------------------------------------------------------


def test_pack_rows_tallest_first_and_opens_pages() -> None:
    assert pack([(10, 10), (20, 30), (40, 10), (50, 20)], side=64) == [(1, 6, 38), (0, 6, 6), (2, 6, 6), (1, 6, 6)]


def test_pack_uses_the_exact_side() -> None:
    assert pack([(10, 10), (10, 10)], side=44) == [(0, 6, 6), (0, 28, 6)]
    assert pack([(30, 10), (30, 10)], side=44) == [(0, 6, 6), (0, 6, 28)]
    assert pack([(1, 1)]) == [(0, PAD_PX, PAD_PX)]


@pytest.mark.parametrize(("used", "height"), [(1, 16), (16, 16), (17, 32), (1025, 2048)])
def test_page_height_is_a_power_of_two(used: int, height: int) -> None:
    assert _page_height(used) == height


# --- Geometry ------------------------------------------------------------------------------


def _items() -> list[Item]:
    return shop_items(_manifest())


def test_items_come_from_building_shops_only() -> None:
    items = _items()
    assert [(i.building, i.kind) for i in items] == [("b1", "fascia"), ("b1", "vinyl"), ("b1", "vinyl"),
                                                     ("b2", "blade"), ("b2", "vinyl"), ("b2", "vinyl")]
    assert items[0].label == Label("board", "Panadería", 660, 72)
    assert items[1].label == Label("vinyl", "Panadería", 480, 52)
    assert items[3].label == Label("board", "Panadería", 140, 100)
    unsigned = {"objects": [{"type": "building", "source_ids": ["b"], "facade": {"shops": [{**_shops(1.0)[0], "sign": None}]}}]}
    assert [i.kind for i in shop_items(unsigned)] == ["vinyl", "vinyl"]
    assert [p.material for p in shop_signs(unsigned).nodes[0].primitives] == ["shop_sign_atlas_0"]


def test_boarded_buildings_keep_their_signs_and_lose_their_vinyls() -> None:
    items = shop_items(_manifest(), {"b1"})
    assert [(i.building, i.kind) for i in items] == [("b1", "fascia"), ("b2", "blade"), ("b2", "vinyl"), ("b2", "vinyl")]
    signs = shop_signs(_manifest(), {"b2"})
    assert [(r["building"], r["kind"]) for r in signs.rows] == [("b1", "fascia"), ("b1", "vinyl"), ("b1", "vinyl"), ("b2", "blade")]
    assert signs.skipped == 0


def test_fascia_is_a_box_open_against_the_wall() -> None:
    fascia = _items()[0]
    faces, edges = board_quads(fascia, np.zeros((4, 2)))
    assert len(faces) == 1 and len(edges) == 4
    front = faces[0][0]
    assert _normal(front) == pytest.approx([0.0, -1.0, 0.0])
    assert front[1][0] > front[0][0]  # read left to right from the street
    assert front[:, 1] == pytest.approx([-0.06] * 4)
    assert sorted(_normal(q)[2] for q in edges)[0] == pytest.approx(-1.0)
    assert {tuple(np.round(_normal(q), 6)) for q in edges} == {(0, 0, 1), (0, 0, -1), (-1, 0, 0), (1, 0, 0)}
    every = np.vstack([front, *edges])
    assert every[:, 1].max() == pytest.approx(0.0) and every[:, 1].min() == pytest.approx(-0.06)
    assert every[:, 0].min() == pytest.approx(0.85) and every[:, 0].max() == pytest.approx(4.15)
    assert every[:, 2].min() == pytest.approx(2.6) and every[:, 2].max() == pytest.approx(2.96)


def test_blade_reads_from_both_sides_and_hangs_from_a_bracket() -> None:
    blade = _items()[3]
    faces, edges = board_quads(blade, np.zeros((4, 2)))
    assert len(faces) == 2 and len(edges) == 8
    front, back = faces[0][0], faces[1][0]
    assert _normal(front) == pytest.approx([-1.0, 0.0, 0.0]) and _normal(back) == pytest.approx([1.0, 0.0, 0.0])
    assert front[1][1] < front[0][1] and back[1][1] > back[0][1]  # each side reads left to right
    board = np.vstack([front, back, *edges[:4]])
    assert board[:, 1].max() == pytest.approx(-0.15) and board[:, 1].min() == pytest.approx(-0.85)
    assert board[:, 2].min() == pytest.approx(2.4) and board[:, 2].max() == pytest.approx(2.9)
    bar = np.vstack(edges[4:])
    assert bar[:, 1].max() == pytest.approx(0.0) and bar[:, 1].min() == pytest.approx(-0.85)
    assert bar[:, 2].min() == pytest.approx(2.9) and bar[:, 2].max() == pytest.approx(2.93)
    assert bar[:, 0].max() - bar[:, 0].min() == pytest.approx(0.03)


def test_vinyl_is_one_quad_in_front_of_the_glass() -> None:
    faces, edges = board_quads(_items()[1], np.zeros((4, 2)))
    assert len(faces) == 1 and edges == []
    quad = faces[0][0]
    assert _normal(quad) == pytest.approx([0.0, -1.0, 0.0])
    assert quad[:, 1] == pytest.approx([0.14] * 4)
    assert quad[:, 2].min() == pytest.approx(1.47) and quad[:, 2].max() == pytest.approx(1.73)


# --- Nodes and atlas -----------------------------------------------------------------------------


def test_one_node_per_building_with_one_atlas_material() -> None:
    signs = shop_signs(_manifest())
    assert [n.name for n in signs.nodes] == ["shop_signs/b1", "shop_signs/b2"]
    assert signs.nodes[0].extras == {"building": "b1", "attribute_origin": "generated"}
    assert [[p.material for p in n.primitives] for n in signs.nodes] == [["shop_sign_atlas_0", "shop_sign_edge"]] * 2
    assert sorted(signs.materials) == ["shop_sign_atlas_0", "shop_sign_edge"]
    assert signs.materials["shop_sign_edge"].color == (0.10, 0.10, 0.11, 1.0)
    assert signs.materials["shop_sign_atlas_0"].color == (1.0, 1.0, 1.0, 1.0)
    assert signs.materials["shop_sign_atlas_0"].roughness == signs.materials["shop_sign_edge"].roughness == 0.6
    for node in signs.nodes:
        for prim in node.primitives:
            assert prim.uvs is not None and prim.uvs.shape == (len(prim.mesh.vertices), 2)
            assert prim.uvs.min() >= 0.0 and prim.uvs.max() <= 1.0
    assert [(r["kind"], r["triangles"]) for r in signs.rows] == [
        ("fascia", 10), ("vinyl", 2), ("vinyl", 2), ("blade", 20), ("vinyl", 2), ("vinyl", 2),
    ]
    assert signs.rows[0] == {"building": "b1", "shop": "shop/b1/n1", "kind": "fascia", "name": "Panadería",
                             "name_origin": "estimated", "page": 0, "material": "shop_sign_atlas_0", "triangles": 10}
    assert sum(p.mesh.triangle_count for n in signs.nodes for p in n.primitives) == 38
    assert signs.skipped == 0
    b1_atlas, b1_edge = signs.nodes[0].primitives
    assert _area(b1_atlas.mesh) == pytest.approx(3.3 * 0.36 + 2 * 2.4 * 0.26)
    assert _area(b1_edge.mesh) == pytest.approx(2 * 3.3 * 0.06 + 2 * 0.36 * 0.06)
    assert b1_atlas.mesh.triangles.tolist() == [[0, 1, 2], [0, 2, 3], [4, 5, 6], [4, 6, 7], [8, 9, 10], [8, 10, 11]]
    b2_atlas, b2_edge = signs.nodes[1].primitives
    assert _area(b2_atlas.mesh) == pytest.approx(2 * 0.7 * 0.5 + 2 * 2.4 * 0.26)
    assert _area(b2_edge.mesh) == pytest.approx(2 * 0.7 * 0.04 + 2 * 0.5 * 0.04 + 4 * 0.85 * 0.03)
    assert np.array_equal(b1_edge.uvs, np.tile([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]], (4, 1)))
    page = signs.images["shop_sign_atlas_0"]
    assert hashlib.sha256(page).hexdigest() == "36dd44ff3ce9e3c9a810a7790089a82272a16bbeed02e07ff98b17aef19dac1b"


def test_each_face_samples_its_own_label_in_the_atlas() -> None:
    signs = shop_signs(_manifest())
    page = decode_png_rgba(signs.images["shop_sign_atlas_0"])
    assert page.shape == (128, 2048, 4)
    labels = [i.label for i in _items()]
    faces = [uv for n in signs.nodes for uv in np.split(n.primitives[0].uvs, len(n.primitives[0].uvs) // 4)]
    assert len(faces) == 7  # the blade has two faces
    for uv, label in zip(faces, [labels[0], labels[1], labels[2], labels[3], labels[3], labels[4], labels[5]], strict=True):
        (u0, v0), (u1, _), (_, v1) = uv[0], uv[1], uv[2]
        assert v1 > v0 and u1 > u0
        x0, x1 = round(u0 * ATLAS_PX), round(u1 * ATLAS_PX)
        y0, y1 = round((1 - v1) * page.shape[0]), round((1 - v0) * page.shape[0])
        assert (x1 - x0, y1 - y0) == (label.width_px, label.height_px)
        assert np.array_equal(page[y0:y1, x0:x1], label_rgba(label)[PAD_PX:-PAD_PX, PAD_PX:-PAD_PX])


def test_labels_past_the_last_page_are_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shop, "MAX_PAGES", 0)
    signs = shop_signs(_manifest())
    assert signs.nodes == [] and signs.rows == [] and signs.images == {}
    assert signs.skipped == 6
    assert sorted(signs.materials) == ["shop_sign_edge"]


def test_a_second_page_gets_its_own_material_and_a_third_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    # Labels in order: blade board, fascia board, vinyl. One page each; the fascia 51 px down.
    monkeypatch.setattr(shop, "pack", lambda sizes: [(k, 6, 6 if k == 0 else 51) for k in range(len(sizes))])
    signs = shop_signs(_manifest())
    assert sorted(signs.images) == ["shop_sign_atlas_0", "shop_sign_atlas_1"]
    assert [(r["kind"], r["page"]) for r in signs.rows] == [("fascia", 1), ("blade", 0)]
    assert signs.skipped == 4
    # 51 + 72 + 6 px of padding is 129: the fascia page is 256 px tall.
    assert decode_png_rgba(signs.images["shop_sign_atlas_1"]).shape == (256, ATLAS_PX, 4)
    assert decode_png_rgba(signs.images["shop_sign_atlas_0"]).shape == (128, ATLAS_PX, 4)
    assert [p.material for p in signs.nodes[0].primitives] == ["shop_sign_atlas_1", "shop_sign_edge"]


# --- Decals on shop glass -----------------------------------------------------------------


def _glass_manifest() -> dict:
    cfg = replace(CFG, shops=replace(CFG.shops, fascia_share=1.0, vinyl_share=1.0))
    fronts = [replace(_front(1.0, 0), back="shop_glass"), _front(5.0, 1)]
    shops = plan_shops("b1", [("n1", {"shop": "bakery"}, (2.0, -1.0))], fronts, [SEG], cfg)
    return {"objects": [{"id": "building/b1", "type": "building", "source_ids": ["b1"], "facade": {"shops": shops}}]}


def test_a_vinyl_on_shop_glass_is_a_black_decal_on_its_own_transparent_page() -> None:
    assert Label("decal", "Bar", 10, 10).colours() == ((16, 16, 18), (16, 16, 18))
    assert (Label("decal", "Bar", 10, 10).material, Label("vinyl", "Bar", 10, 10).material) == ("shop_decal_atlas_{}", "shop_sign_atlas_{}")
    signs = shop_signs(_glass_manifest())
    assert [(r["kind"], r["material"], r["page"], r["triangles"]) for r in signs.rows] == [
        ("fascia", "shop_sign_atlas_0", 0, 10), ("vinyl", "shop_decal_atlas_0", 0, 2), ("vinyl", "shop_sign_atlas_0", 0, 2),
    ]
    assert sorted(signs.materials) == ["shop_decal_atlas_0", "shop_sign_atlas_0", "shop_sign_edge"]
    assert signs.materials["shop_decal_atlas_0"].color == (1.0, 1.0, 1.0, 1.0)
    [node] = signs.nodes
    assert [p.material for p in node.primitives] == ["shop_decal_atlas_0", "shop_sign_atlas_0", "shop_sign_edge"]
    decal = node.primitives[0]
    assert decal.mesh.triangle_count == 2 and _area(decal.mesh) == pytest.approx(2.4 * 0.26)
    page = decode_png_rgba(signs.images["shop_decal_atlas_0"])
    assert page.shape == (64, ATLAS_PX, 4)
    # Black letters and nothing else: every drawn pixel is the ink colour, opaque in the letters, clear around them.
    drawn = page[..., 3] > 0
    assert drawn.any() and (page[..., 3] == 255).any() and (page[..., 3] == 0).sum() > drawn.sum()
    assert np.all(page[drawn][:, :3] == (16, 16, 18))
    label = label_rgba(Label("decal", "Panadería", 480, 52))
    assert np.all(label[:PAD_PX, :, 3] == 0) and np.all(label[:, :PAD_PX, 3] == 0)
    # The strip on the dark glass keeps its opaque background on the board page.
    strip = label_rgba(Label("vinyl", "Panadería", 480, 52))
    assert np.all(strip[..., 3] == 255) and tuple(strip[0, 0, :3]) == VINYL_COLOURS[0]
