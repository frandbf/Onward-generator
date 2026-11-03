"""Shop signs and window vinyls, drawn from ``facade.shops`` in ``scene_detail.json``.

``detail`` decides where each fascia, blade sign and vinyl goes (``detail/shops.py``).
This module draws them. Every label is drawn once, with the vendored DejaVu Sans
Bold like the street plates, into an atlas page 2048 px wide. Every face on a page
shares one material, ``shop_sign_atlas_<page>``. Rims and blade brackets share
``shop_sign_edge``. The signs are visual only: no collider is added.

A vinyl on a ``glass_dark`` (or ``glass_view``) pane is an opaque strip with the dark
glass colour of the window image behind white letters, a few millimetres in front
of the pane. A vinyl on the frameless ``shop_glass`` of a shop interior is a decal:
black letters on a fully transparent background, on pages of their own,
``shop_decal_atlas_<page>``, which the adapter draws with the transparent sprite
shader, so the room stays visible around and between the letters.
These are generic signs, not the real sign of any shop.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from functools import partial
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from onward_generator.dress.atlas import encode_png_rgba, run_png_jobs
from onward_generator.generation.config import Material
from onward_generator.generation.gltf import Node, Primitive
from onward_generator.generation.mesh import Mesh
from onward_generator.signs.plaque import plaque_font, plaque_text

ATLAS_PX = 2048
MAX_PAGES = 2
PX_PER_M = 200
PAD_PX = 6
BRACKET_M = 0.03
ATLAS_MATERIAL = "shop_sign_atlas_{}"
# Transparent pages: the decals on shop glass. The adapter keys PP_Sprite_Lit on this prefix.
DECAL_MATERIAL = "shop_decal_atlas_{}"
DECAL_GLASS = "shop_glass"
EDGE_MATERIAL = "shop_sign_edge"
EDGE_COLOR = (0.10, 0.10, 0.11, 1.0)
# Background and letters of a board. The name picks the pair, so a chain keeps its colours.
BOARD_COLOURS = (
    ((24, 74, 52), (240, 232, 206)),
    ((122, 28, 36), (248, 240, 226)),
    ((22, 44, 92), (246, 246, 240)),
    ((28, 28, 30), (226, 186, 92)),
    ((236, 230, 214), (40, 40, 44)),
)
# The glass fill of materials/openings.py behind white letters.
VINYL_COLOURS = ((28, 34, 42), (238, 238, 232))
# Black letters. The background takes the ink colour at alpha 0, so a mipmap blends no halo into the letters.
DECAL_INK = (16, 16, 18)
DECAL_COLOURS = (DECAL_INK, DECAL_INK)
CLEAR = 0
UNIT_UV = np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]])
Z = np.array([0.0, 0.0, 1.0])
OPAQUE = 255
REFERENCE_PX = 100  # the font size the text is measured at
MIN_FONT_PX = 6
MIN_MARGIN_PX = 2


@dataclass(frozen=True, slots=True)
class Label:
    style: str  # board | vinyl | decal
    text: str
    width_px: int
    height_px: int

    @property
    def material(self) -> str:
        """The page material of this label: a decal goes on the transparent pages."""
        return DECAL_MATERIAL if self.style == "decal" else ATLAS_MATERIAL

    def colours(self) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
        if self.style == "decal":
            return DECAL_COLOURS
        if self.style == "vinyl":
            return VINYL_COLOURS
        digest = hashlib.sha256(self.text.encode("utf-8")).digest()
        return BOARD_COLOURS[digest[0] % len(BOARD_COLOURS)]


@dataclass(frozen=True, slots=True)
class Item:
    building: str
    shop: Mapping[str, Any]
    kind: str  # fascia | blade | vinyl
    board: Mapping[str, Any]
    label: Label


@dataclass(slots=True)
class ShopSigns:
    nodes: list[Node]
    materials: dict[str, Material]
    images: dict[str, bytes]
    rows: list[dict[str, Any]]
    skipped: int


def label_for(style: str, text: str, board: Mapping[str, Any]) -> Label:
    return Label(style, plaque_text(text), round(float(board["width_m"]) * PX_PER_M), round(float(board["height_m"]) * PX_PER_M))


def shop_items(manifest: Mapping[str, Any], boarded: Collection[str] = ()) -> list[Item]:
    """Every sign and vinyl that ``detail`` recorded, in building and shop order.

    A building in ``boarded`` (its glass was boarded up by ``materials apply``) keeps its signs and loses its vinyls.
    """
    items: list[Item] = []
    for record in manifest.get("objects") or []:
        if record.get("type") != "building":
            continue
        building = str(record["source_ids"][0])
        for shop in (record.get("facade") or {}).get("shops") or []:
            sign = shop.get("sign")
            if sign is not None:
                items.append(Item(building, shop, sign["kind"], sign, label_for("board", shop["name"], sign)))
            for vinyl in [] if building in boarded else shop["vinyls"]:
                style = "decal" if vinyl.get("glass") == DECAL_GLASS else "vinyl"
                items.append(Item(building, shop, "vinyl", vinyl, label_for(style, shop["name"], vinyl)))
    return items


def pack(sizes: Sequence[tuple[int, int]], side: int = ATLAS_PX) -> list[tuple[int, int, int]]:
    """Shelf packing, tallest first: ``(page, x, y)`` of each label's inner corner, padding included around it."""
    order = sorted(range(len(sizes)), key=lambda i: (-sizes[i][1], i))
    placed: dict[int, tuple[int, int, int]] = {}
    page = x = y = row = 0
    for i in order:
        w, h = sizes[i][0] + 2 * PAD_PX, sizes[i][1] + 2 * PAD_PX
        if x + w > side:
            x, y = 0, y + row
            row = 0  # pragma: no mutate: any start up to the smallest padded label (12 px) gives the same row height
        if y + h > side:
            page, x, y = page + 1, 0, 0
            row = 0  # pragma: no mutate: any start up to the smallest padded label (12 px) gives the same row height
        placed[i] = (page, x + PAD_PX, y + PAD_PX)
        x += w
        row = max(row, h)
    return [placed[i] for i in range(len(sizes))]


def label_rgba(label: Label) -> np.ndarray:
    """The label with ``PAD_PX`` of background on every side, so mipmaps do not bleed into a neighbour."""
    background, ink = label.colours()
    alpha = CLEAR if label.style == "decal" else OPAQUE
    image = Image.new("RGBA", (label.width_px + 2 * PAD_PX, label.height_px + 2 * PAD_PX), (*background, alpha))
    draw = ImageDraw.Draw(image)
    margin = max(MIN_MARGIN_PX, label.height_px // 8)
    reference = plaque_font(REFERENCE_PX)
    ascent, descent = reference.getmetrics()
    # Glyph sizes scale with the font size: the largest size whose line fits the height and whose text fits the width.
    scale = min((label.height_px - 2 * margin) / (ascent + descent), (label.width_px - 2 * margin) / draw.textlength(label.text, font=reference))
    font = plaque_font(max(MIN_FONT_PX, int(REFERENCE_PX * scale)))
    centre = (PAD_PX + label.width_px / 2, PAD_PX + label.height_px / 2)
    draw.text(centre, label.text, fill=(*ink, OPAQUE), font=font, anchor="mm")
    return np.asarray(image)


def page_height(used: int) -> int:
    """The smallest power of two, at least 16, that holds ``used`` rows."""
    height = 16
    while height < used:
        height *= 2
    return height


def _frame(board: Mapping[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    """The board normal and the reader's right, seen from the front."""
    n = np.array([*board["normal"], 0.0])
    return n, np.cross(-n, Z)


def _corners(board: Mapping[str, Any], centre: np.ndarray, width: float, height: float, depth: float) -> dict[str, np.ndarray]:
    """The faces of a box in the board frame, each (4, 3) counter-clockwise from outside."""
    n, r = _frame(board)
    hw, hh, hd = width / 2, height / 2, depth / 2

    def p(a: float, b: float, c: float) -> np.ndarray:
        return centre + r * a + Z * b + n * c

    return {
        "front": np.array([p(-hw, -hh, hd), p(hw, -hh, hd), p(hw, hh, hd), p(-hw, hh, hd)]),
        "back": np.array([p(hw, -hh, -hd), p(-hw, -hh, -hd), p(-hw, hh, -hd), p(hw, hh, -hd)]),
        "top": np.array([p(-hw, hh, hd), p(hw, hh, hd), p(hw, hh, -hd), p(-hw, hh, -hd)]),
        "bottom": np.array([p(-hw, -hh, -hd), p(hw, -hh, -hd), p(hw, -hh, hd), p(-hw, -hh, hd)]),
        "left": np.array([p(-hw, -hh, -hd), p(-hw, -hh, hd), p(-hw, hh, hd), p(-hw, hh, -hd)]),
        "right": np.array([p(hw, -hh, hd), p(hw, -hh, -hd), p(hw, hh, -hd), p(hw, hh, hd)]),
    }


def board_quads(item: Item, uv: np.ndarray) -> tuple[list[tuple[np.ndarray, np.ndarray]], list[np.ndarray]]:
    """``(atlas quads with their UVs, edge quads)`` of one sign or vinyl."""
    b = item.board
    centre = np.array(b["centre_m"])
    width, height, depth = float(b["width_m"]), float(b["height_m"]), float(b["depth_m"])
    box = _corners(b, centre, width, height, depth)
    faces = [(box["front"], uv)]
    if b.get("double_sided"):
        faces.append((box["back"], uv))
    edges = [box["top"], box["bottom"], box["left"], box["right"]] if depth > 0 else []
    bracket = float(b.get("bracket_m") or 0.0)
    if bracket > 0:
        _, r = _frame(b)
        middle = centre + r * (width / 2 - bracket / 2) + Z * (height / 2 + BRACKET_M / 2)
        bar = _corners(b, middle, bracket, BRACKET_M, BRACKET_M)
        edges += [bar["front"], bar["back"], bar["top"], bar["bottom"]]
    return faces, edges


def _mesh(quads: Sequence[np.ndarray]) -> Mesh:
    vertices = np.vstack(quads)
    triangles = np.vstack([np.array([[0, 1, 2], [0, 2, 3]]) + 4 * i for i in range(len(quads))])
    return Mesh(vertices, triangles)


def shop_signs(manifest: Mapping[str, Any], boarded: Collection[str] = ()) -> ShopSigns:
    """Nodes, materials and atlas pages for the shops in a detail manifest. Labels past ``MAX_PAGES`` are skipped."""
    items = shop_items(manifest, boarded)
    labels = sorted({item.label for item in items}, key=lambda lb: (lb.style, lb.text, lb.width_px, lb.height_px))
    kept: dict[Label, tuple[str, int, int]] = {}
    for sheet in (ATLAS_MATERIAL, DECAL_MATERIAL):  # the opaque pages and the transparent ones are packed apart
        on_sheet = [lb for lb in labels if lb.material == sheet]
        spots = pack([(lb.width_px, lb.height_px) for lb in on_sheet])
        for lb, (page, x, y) in zip(on_sheet, spots, strict=True):  # pragma: no mutate: pack returns one spot per size, so strict changes nothing a caller sees
            if page < MAX_PAGES:
                kept[lb] = (sheet.format(page), x, y)
    pages: dict[str, np.ndarray] = {}
    for name in sorted({spot[0] for spot in kept.values()}):
        used = max(spot[2] + lb.height_px + PAD_PX for lb, spot in kept.items() if spot[0] == name)
        pages[name] = np.zeros((page_height(used), ATLAS_PX, 4), dtype=np.uint8)
    for lb, (name, x, y) in kept.items():
        pages[name][y - PAD_PX:y + lb.height_px + PAD_PX, x - PAD_PX:x + lb.width_px + PAD_PX] = label_rgba(lb)

    atlas: dict[str, dict[str, list[tuple[np.ndarray, np.ndarray]]]] = defaultdict(lambda: defaultdict(list))
    edges: dict[str, list[np.ndarray]] = defaultdict(list)
    rows: list[dict[str, Any]] = []
    for item in items:
        if item.label not in kept:
            continue
        name, x, y = kept[item.label]
        side_h = pages[name].shape[0]
        u0, u1 = x / ATLAS_PX, (x + item.label.width_px) / ATLAS_PX
        v1, v0 = 1 - y / side_h, 1 - (y + item.label.height_px) / side_h
        faces, rims = board_quads(item, np.array([[u0, v0], [u1, v0], [u1, v1], [u0, v1]]))
        atlas[item.building][name] += faces
        edges[item.building] += rims
        rows.append({
            "building": item.building, "shop": item.shop["id"], "kind": item.kind, "name": item.label.text,
            "name_origin": item.shop["name_origin"], "page": int(name.rsplit("_", 1)[1]), "material": name,
            "triangles": 2 * (len(faces) + len(rims)),
        })

    nodes: list[Node] = []
    for building in sorted(atlas):
        prims = [Primitive(_mesh([q for q, _ in faces]), name, np.vstack([uv for _, uv in faces]))
                 for name, faces in sorted(atlas[building].items())]
        if edges[building]:
            prims.append(Primitive(_mesh(edges[building]), EDGE_MATERIAL, np.vstack([UNIT_UV] * len(edges[building]))))
        nodes.append(Node(f"shop_signs/{building}", tuple(prims), {"building": building, "attribute_origin": "generated"}))
    materials = {name: Material((1.0, 1.0, 1.0, 1.0), 0.6) for name in pages}
    materials[EDGE_MATERIAL] = Material(EDGE_COLOR, 0.6)
    images = run_png_jobs({name: partial(encode_png_rgba, pixels) for name, pixels in pages.items()})
    return ShopSigns(nodes, materials, images, rows, len(items) - len(rows))
