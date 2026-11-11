"""The shared prop atlas (overgrow/atlas.py): candidates, packing, UV remap and the cover stage's reuse."""

from __future__ import annotations

import io

import numpy as np
from PIL import Image

from onward_generator.gameplay.cover_stage import _scene_assets
from onward_generator.generation.gltf import Node, Primitive
from onward_generator.generation.mesh import box
from onward_generator.overgrow.atlas import (
    ATLAS_PREFIX,
    ATLAS_PX,
    FLAT_PX,
    PAD_PX,
    Cell,
    apply_atlas,
    atlas_candidates,
    pack,
    remap_nodes,
)


def _png(colour, size=(4, 4), noise=False) -> bytes:
    pixels = np.tile(np.array(colour, dtype=np.uint8), (size[1], size[0], 1))
    if noise:
        pixels[::2, ::2] = 255 - pixels[::2, ::2]
    out = io.BytesIO()
    Image.fromarray(pixels).save(out, format="PNG")
    return out.getvalue()


def _node(name: str, material: str, uv: float) -> Node:
    mesh = box(0, 1, 0, 1, 0, 1)
    return Node(name, (Primitive(mesh, material, np.full((mesh.vertex_count, 2), uv, dtype=np.float32)),))


def test_only_props_with_unit_uvs_and_an_image_are_candidates() -> None:
    nodes = [_node("a", "prop_red", 0.5), _node("b", "prop_tiled", 3.0), _node("c", "urban_metal", 0.5),
             _node("d", "prop_noimage", 0.5), _node("e", "prop_red", 0.2)]
    images = {"prop_red": _png((200, 0, 0)), "prop_tiled": _png((0, 200, 0)), "urban_metal": _png((9, 9, 9))}
    assert atlas_candidates(nodes, images) == ["prop_red"]


def test_pack_gives_flat_colours_small_cells_and_textures_their_size() -> None:
    images = {"prop_flat": _png((10, 20, 30)), "prop_tex": _png((100, 100, 100), (64, 32), noise=True)}
    pngs, cells = pack(images, sorted(images))
    assert list(pngs) == [f"{ATLAS_PREFIX}0"]
    atlas = np.asarray(Image.open(io.BytesIO(pngs[f"{ATLAS_PREFIX}0"])).convert("RGB"))
    assert atlas.shape == (ATLAS_PX, ATLAS_PX, 3)
    flat, tex = cells["prop_flat"], cells["prop_tex"]
    assert round(flat.su * ATLAS_PX) == FLAT_PX and round(tex.su * ATLAS_PX) == 64 and round(tex.sv * ATLAS_PX) == 32
    # The flat cell's centre, read back through the V-up convention, is its colour.
    u, v = flat.u0 + 0.5 * flat.su, flat.v0 + 0.5 * flat.sv
    assert tuple(atlas[int((1 - v) * ATLAS_PX), int(u * ATLAS_PX)]) == (10, 20, 30)
    # Cells do not overlap, padding included.
    a = (tex.u0 * ATLAS_PX - PAD_PX, tex.u0 * ATLAS_PX + 64 + PAD_PX)
    b = (flat.u0 * ATLAS_PX - PAD_PX, flat.u0 * ATLAS_PX + FLAT_PX + PAD_PX)
    assert a[1] <= b[0] or b[1] <= a[0]


def test_apply_atlas_moves_props_to_one_material_and_keeps_the_rest() -> None:
    nodes = [_node("a", "prop_red", 0.5), _node("b", "prop_blue", 0.5), _node("c", "prop_tiled", 2.0)]
    images = {"prop_red": _png((200, 0, 0)), "prop_blue": _png((0, 0, 200)), "prop_tiled": _png((0, 200, 0))}
    out, written, cells = apply_atlas(nodes, images)
    assert sorted(cells) == ["prop_blue", "prop_red"]
    assert [p.material for n in out for p in n.primitives] == [f"{ATLAS_PREFIX}0", f"{ATLAS_PREFIX}0", "prop_tiled"]
    assert sorted(written) == [f"{ATLAS_PREFIX}0", "prop_tiled"]
    uv = out[0].primitives[0].uvs[0]
    assert np.allclose(uv, [cells["prop_red"].u0 + 0.5 * cells["prop_red"].su, cells["prop_red"].v0 + 0.5 * cells["prop_red"].sv])
    assert apply_atlas([_node("x", "urban_metal", 0.5)], {"urban_metal": _png((1, 1, 1))})[2] == {}


def test_cells_round_trip_and_cover_sees_atlased_assets() -> None:
    cell = Cell(f"{ATLAS_PREFIX}0", 0.25, 0.5, 0.125, 0.0625)
    assert Cell.from_list(cell.to_list()) == cell
    moved = remap_nodes([_node("a", "prop_red", 1.0)], {"prop_red": cell})
    assert moved[0].primitives[0].material == f"{ATLAS_PREFIX}0" and np.allclose(moved[0].primitives[0].uvs[0], [0.375, 0.5625])
    assets = _scene_assets({"prop_kept": None, f"{ATLAS_PREFIX}0": None, "urban_metal": None},  # type: ignore[dict-item]
                           {"prop_red": cell.to_list(), "prop_red__1": cell.to_list()})
    assert assets == frozenset({"kept", "red"})
