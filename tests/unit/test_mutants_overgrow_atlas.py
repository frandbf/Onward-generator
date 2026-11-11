"""The shared prop atlas (overgrow/atlas.py) at its boundaries: candidates, cell images, shelf order and the remap."""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

from onward_generator.generation.gltf import Node, Primitive
from onward_generator.generation.mesh import box
from onward_generator.overgrow import atlas as atlas_module
from onward_generator.overgrow.atlas import ATLAS_PREFIX, CELL_PX, FLAT_PX, PAD_PX, UV_SLACK, Cell, atlas_candidates, pack, remap_nodes

MESH = box(0, 1, 0, 1, 0, 1)


def _png(pixels: np.ndarray) -> bytes:
    out = io.BytesIO()
    Image.fromarray(pixels.astype(np.uint8)).save(out, format="PNG")
    return out.getvalue()


def _noise(h: int, w: int, seed: int) -> np.ndarray:
    return np.random.default_rng(seed).integers(20, 236, size=(h, w, 3), dtype=np.uint8)


def _decode(data: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(data)).convert("RGB"))


def _prim(material: str, uvs: np.ndarray) -> Primitive:
    return Primitive(MESH, material, uvs)


# --- candidates ------------------------------------------------------------------------------------------------------


def test_candidates_hold_the_unit_square_with_its_slack_inclusive() -> None:
    def uv(*values: float) -> np.ndarray:
        return np.array([[v, 0.5] for v in values], dtype=np.float64)

    nodes = [
        Node("first", (_prim("urban_metal", uv(0.5)), _prim("prop_after_other", uv(0.5)))),
        Node(
            "edges",
            (
                _prim("prop_low_edge", uv(-UV_SLACK, 0.5)),
                _prim("prop_zero", uv(0.0)),
                _prim("prop_high_edge", uv(1 + UV_SLACK)),
                _prim("prop_one", uv(1.0)),
            ),
        ),
        Node("out", (_prim("prop_low_out", uv(-2 * UV_SLACK)), _prim("prop_high_out", uv(1 + 2 * UV_SLACK)))),
        Node("sizes", (_prim("prop_empty", np.zeros((0, 2))), _prim("prop_single", uv(0.25)), Primitive(MESH, "prop_none"))),
        Node("mixed", (_prim("prop_mixed", uv(2.0)), _prim("prop_mixed", uv(0.5)))),
    ]
    names = [
        "prop_after_other",
        "prop_low_edge",
        "prop_zero",
        "prop_high_edge",
        "prop_one",
        "prop_low_out",
        "prop_high_out",
        "prop_empty",
        "prop_single",
        "prop_none",
        "prop_mixed",
        "urban_metal",
    ]
    images = {name: b"png" for name in names}
    assert atlas_candidates(nodes, images) == [
        "prop_after_other",
        "prop_high_edge",
        "prop_low_edge",
        "prop_one",
        "prop_single",
        "prop_zero",
    ]


# --- cell images -----------------------------------------------------------------------------------------------------


def _cell_pixels(pngs: dict[str, bytes], cell: Cell, size: int) -> np.ndarray:
    atlas = _decode(pngs[cell.atlas])
    side = atlas.shape[0]
    left, top = round(cell.u0 * side), side - round(cell.v0 * side) - round(cell.sv * side)
    assert round(cell.su * side) == size or round(cell.sv * side) == size
    return atlas[top : top + round(cell.sv * side), left : left + round(cell.su * side)]


def test_a_small_or_even_image_is_one_flat_colour_and_a_varied_one_keeps_its_size() -> None:
    small = _noise(4, 4, 1)
    exact = _noise(FLAT_PX, FLAT_PX, 2)
    tinted = np.tile(np.array([10, 200, 30], dtype=np.uint8), (16, 16, 1))  # each channel even, the channels apart
    at_std = np.where((np.indices((16, 16)).sum(axis=0) % 2)[..., None] == 0, 100, 104).repeat(3, axis=2)  # std exactly 2
    varied = _noise(12, 20, 3)
    images = {
        "prop_small": _png(small),
        "prop_exact": _png(exact),
        "prop_tinted": _png(tinted),
        "prop_std": _png(at_std),
        "prop_varied": _png(varied),
    }
    pngs, cells = pack(images, sorted(images))
    for name, source in (("prop_small", small), ("prop_exact", exact), ("prop_tinted", tinted)):
        pixels = _cell_pixels(pngs, cells[name], FLAT_PX)
        assert pixels.shape == (FLAT_PX, FLAT_PX, 3), name
        mean = source.reshape(-1, 3).mean(axis=0).round().astype(np.uint8)
        assert (pixels == mean).all(), name
    assert np.array_equal(_cell_pixels(pngs, cells["prop_std"], 16), at_std)
    assert np.array_equal(_cell_pixels(pngs, cells["prop_varied"], 20), varied)


def test_an_image_above_the_cell_size_is_shrunk_with_lanczos() -> None:
    big = _noise(300, CELL_PX + 88, 4)
    pngs, cells = pack({"prop_big": _png(big)}, ["prop_big"])
    want = Image.fromarray(big)
    want.thumbnail((CELL_PX, CELL_PX), Image.Resampling.LANCZOS)
    expected = np.asarray(want)
    assert expected.shape[1] == CELL_PX
    assert np.array_equal(_cell_pixels(pngs, cells["prop_big"], CELL_PX), expected)
    cell_image = atlas_module._cell_image(_png(_noise(CELL_PX, CELL_PX // 2, 5)))
    assert cell_image.shape == (CELL_PX, CELL_PX // 2, 3)  # at the cell size: kept as it is


# --- shelf packing (a 64 px atlas, so a few tiles fill shelves and pages) ---------------------------------------------


@pytest.fixture
def small_atlas(monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.setattr(atlas_module, "ATLAS_PX", 64)
    return 64


def test_tiles_go_tallest_then_widest_then_by_name_onto_shelves(small_atlas: int) -> None:
    tiles = {
        "prop_a": np.full((4, 4, 3), 90, dtype=np.uint8),
        "prop_b": _noise(16, 24, 6),
        "prop_c": _noise(24, 8, 7),
        "prop_d": _noise(16, 32, 8),
    }
    pngs, cells = pack({name: _png(t) for name, t in tiles.items()}, sorted(tiles))
    assert list(pngs) == [f"{ATLAS_PREFIX}0"]
    s = float(small_atlas)
    # c (24 tall) at x 0, d (16 tall, wider than b) beside it; b does not fit (56 + 32 > 64) and opens the next shelf at
    # the height of c's slot (32), then a.
    assert cells == {
        "prop_c": Cell(f"{ATLAS_PREFIX}0", 4 / s, 36 / s, 8 / s, 24 / s),
        "prop_d": Cell(f"{ATLAS_PREFIX}0", 20 / s, 44 / s, 32 / s, 16 / s),
        "prop_b": Cell(f"{ATLAS_PREFIX}0", 4 / s, 12 / s, 24 / s, 16 / s),
        "prop_a": Cell(f"{ATLAS_PREFIX}0", 36 / s, 20 / s, 8 / s, 8 / s),
    }
    atlas = _decode(pngs[f"{ATLAS_PREFIX}0"])
    c = tiles["prop_c"]
    assert np.array_equal(atlas[4:28, 4:12], c)
    # The pad repeats the tile's edge, corners included, so a mipmap does not bleed the neighbour in.
    assert (atlas[0:4, 0:4] == c[0, 0]).all() and (atlas[28:32, 12:16] == c[-1, -1]).all()
    assert np.array_equal(atlas[0:4, 4:12], np.broadcast_to(c[0], (4, 8, 3)))


def test_a_tile_that_ends_on_the_atlas_edge_stays_on_its_shelf_and_page(small_atlas: int) -> None:
    s = float(small_atlas)
    row = {"prop_r1": _noise(16, 24, 9), "prop_r2": _noise(16, 24, 10)}  # 32 + 32 = 64: the shelf is exactly full
    _, cells = pack({name: _png(t) for name, t in row.items()}, sorted(row))
    assert cells["prop_r2"] == Cell(f"{ATLAS_PREFIX}0", 36 / s, 44 / s, 24 / s, 16 / s)
    page = {f"prop_p{i}": _noise(24, 56, 11 + i) for i in range(3)}  # 64 px wide, 32 tall: two shelves fill a page
    pngs, cells = pack({name: _png(t) for name, t in page.items()}, sorted(page))
    assert sorted(pngs) == [f"{ATLAS_PREFIX}0", f"{ATLAS_PREFIX}1"]
    assert cells["prop_p0"] == Cell(f"{ATLAS_PREFIX}0", PAD_PX / s, 36 / s, 56 / s, 24 / s)
    assert cells["prop_p1"] == Cell(f"{ATLAS_PREFIX}0", PAD_PX / s, PAD_PX / s, 56 / s, 24 / s)
    assert cells["prop_p2"] == Cell(f"{ATLAS_PREFIX}1", PAD_PX / s, 36 / s, 56 / s, 24 / s)
    second = _decode(pngs[f"{ATLAS_PREFIX}1"])
    assert second.shape == (64, 64, 3) and np.array_equal(second[4:28, 4:60], page["prop_p2"])


# --- remap -----------------------------------------------------------------------------------------------------------


def test_a_node_with_an_atlased_and_an_own_material_moves_only_the_first() -> None:
    kept = Primitive(MESH, "urban_metal", np.full((MESH.vertex_count, 2), 3.0, dtype=np.float32))
    node = Node("a", (_prim("prop_red", np.full((MESH.vertex_count, 2), 0.5, dtype=np.float32)), kept))
    cell = Cell(f"{ATLAS_PREFIX}0", 0.25, 0.5, 0.125, 0.0625)
    moved = remap_nodes([node], {"prop_red": cell})[0]
    assert moved.primitives[0].material == f"{ATLAS_PREFIX}0" and np.allclose(moved.primitives[0].uvs, [0.3125, 0.53125])
    assert moved.primitives[1] is kept


def test_a_flat_cell_is_uint8_like_a_textured_one() -> None:
    from onward_generator.overgrow.atlas import _cell_image

    out = io.BytesIO()
    Image.new("RGB", (4, 4), (10, 20, 30)).save(out, format="PNG")
    cell = _cell_image(out.getvalue())
    assert cell.dtype == np.uint8 and cell.shape == (8, 8, 3) and cell[0, 0].tolist() == [10, 20, 30]
