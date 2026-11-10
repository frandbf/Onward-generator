"""One shared albedo atlas for the props and furniture of a scene (draw audit, 10 November 2026).

Each library prop had its own material, ``prop_<id>``: the adapter combines by (cell, material), so every distinct
model was one more draw per 100 m cell, or per building and storey for furniture. On Madrid r200 they were 260 of
479 draw groups for 21 % of the triangles. Here every prop whose UVs stay inside the unit square moves to a cell of a
shared atlas (``prop_atlas_<n>``) and its UVs are scaled into that cell; a prop whose texture repeats keeps its own
material. A flat colour takes an 8 px cell, a texture at most ``CELL_PX`` (512: a photoscan's small UV islands blurred at 256 on the headset), each with ``PAD_PX`` of its edge repeated
so mipmaps do not bleed. The name keeps the ``prop_`` prefix, so the adapter lights and bakes the atlas as before.

UVs follow the generator's convention (V up from the bottom of the image), as the library's props do.
"""

from __future__ import annotations

import io
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

import numpy as np
from PIL import Image

from onward_generator.generation.gltf import Node, Primitive

ATLAS_PREFIX = "prop_atlas_"
PROP_ATLAS_EXTRA = "prop_atlas"
"""The visual GLB's ``extras`` key: each atlased material and its cell, for the stages after overgrow (``perf cover``)."""
ATLAS_PX = 2048
CELL_PX = 512
FLAT_PX = 8
PAD_PX = 4
UV_SLACK = 1e-3
FLAT_STD = 2.0


@dataclass(frozen=True, slots=True)
class Cell:
    """Where one material's image sits: the atlas, the origin and the scale of its UVs (V up)."""

    atlas: str
    u0: float
    v0: float
    su: float
    sv: float

    def to_list(self) -> list[float | str]:
        return [self.atlas, round(self.u0, 6), round(self.v0, 6), round(self.su, 6), round(self.sv, 6)]

    @classmethod
    def from_list(cls, values: Sequence[float | str]) -> Cell:
        atlas, u0, v0, su, sv = values
        return cls(str(atlas), float(u0), float(v0), float(su), float(sv))

    def remap(self, uvs: np.ndarray) -> np.ndarray:
        return np.column_stack([self.u0 + uvs[:, 0] * self.su, self.v0 + uvs[:, 1] * self.sv]).astype(np.float32)


def is_prop(material: str) -> bool:
    return material.startswith("prop_") and not material.startswith(ATLAS_PREFIX)


def atlas_candidates(nodes: Sequence[Node], images: Mapping[str, bytes]) -> list[str]:
    """Prop materials with an image whose every primitive keeps its UVs in the unit square, sorted."""
    inside: dict[str, bool] = {}
    for node in nodes:
        for prim in node.primitives:
            if not is_prop(prim.material) or prim.material not in images:
                continue
            ok = prim.uvs is not None and len(prim.uvs) > 0 and bool(
                (prim.uvs >= -UV_SLACK).all() and (prim.uvs <= 1 + UV_SLACK).all())
            inside[prim.material] = inside.get(prim.material, True) and ok
    return sorted(name for name, ok in inside.items() if ok)


def _cell_image(data: bytes) -> np.ndarray:
    picture = Image.open(io.BytesIO(data)).convert("RGB")
    pixels = np.asarray(picture, dtype=np.uint8)
    if max(picture.size) <= FLAT_PX or float(pixels.reshape(-1, 3).astype(np.float32).std(axis=0).max()) < FLAT_STD:
        colour = pixels.reshape(-1, 3).mean(axis=0).round().astype(np.uint8)
        return np.tile(colour, (FLAT_PX, FLAT_PX, 1))
    if max(picture.size) > CELL_PX:
        picture.thumbnail((CELL_PX, CELL_PX), Image.Resampling.LANCZOS)
    return np.asarray(picture, dtype=np.uint8)


def pack(images: Mapping[str, bytes], names: Sequence[str]) -> tuple[dict[str, bytes], dict[str, Cell]]:
    """Shelf-pack the images of ``names`` into ``ATLAS_PX`` atlases; their PNGs and each name's cell."""
    tiles = {name: _cell_image(images[name]) for name in names}
    order = sorted(names, key=lambda n: (-tiles[n].shape[0], -tiles[n].shape[1], n))
    atlases: list[np.ndarray] = []
    cells: dict[str, Cell] = {}
    x = y = shelf = 0
    canvas: np.ndarray | None = None
    for name in order:
        tile = tiles[name]
        h, w = tile.shape[:2]
        bw, bh = w + 2 * PAD_PX, h + 2 * PAD_PX
        if canvas is None or x + bw > ATLAS_PX:
            x, y, shelf = 0, y + shelf, 0
        if canvas is None or y + bh > ATLAS_PX:
            canvas = np.zeros((ATLAS_PX, ATLAS_PX, 3), dtype=np.uint8)
            atlases.append(canvas)
            x = y = shelf = 0
        canvas[y:y + bh, x:x + bw] = np.pad(tile, ((PAD_PX, PAD_PX), (PAD_PX, PAD_PX), (0, 0)), mode="edge")
        atlas = f"{ATLAS_PREFIX}{len(atlases) - 1}"
        top, left = y + PAD_PX, x + PAD_PX
        cells[name] = Cell(atlas, left / ATLAS_PX, (ATLAS_PX - top - h) / ATLAS_PX, w / ATLAS_PX, h / ATLAS_PX)
        x += bw
        shelf = max(shelf, bh)
    pngs = {}
    for index, canvas in enumerate(atlases):
        out = io.BytesIO()
        Image.fromarray(canvas).save(out, format="PNG", optimize=True)
        pngs[f"{ATLAS_PREFIX}{index}"] = out.getvalue()
    return pngs, cells


def remap_nodes(nodes: Sequence[Node], cells: Mapping[str, Cell]) -> list[Node]:
    """``nodes`` with every primitive of an atlased material moved to its atlas and its UVs scaled into its cell."""
    out = []
    for node in nodes:
        if not any(prim.material in cells for prim in node.primitives):
            out.append(node)
            continue
        prims = tuple(
            Primitive(prim.mesh, cells[prim.material].atlas, cells[prim.material].remap(prim.uvs))  # type: ignore[arg-type]
            if prim.material in cells else prim
            for prim in node.primitives
        )
        out.append(replace(node, primitives=prims))
    return out


def apply_atlas(nodes: Sequence[Node], images: Mapping[str, bytes]) -> tuple[list[Node], dict[str, bytes], dict[str, Cell]]:
    """Atlas the prop materials of ``nodes``: the new nodes, the images to write (atlases in, atlased out) and the cells."""
    names = atlas_candidates(nodes, images)
    if not names:
        return list(nodes), dict(images), {}
    pngs, cells = pack(images, names)
    kept = {name: data for name, data in images.items() if name not in cells}
    return remap_nodes(nodes, cells), {**kept, **pngs}, cells
