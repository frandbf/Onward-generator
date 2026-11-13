"""Wall decals: stains, soot, rust, cracks, torn posters and brick patches on the street walls of the core (ADR-038).

Every map gets ``MIN_DECALS`` to ``MAX_DECALS`` of them, so the walls read lived-in. A decal is one quad 1 cm in front
of an exposed wall (2 triangles), on a transparent atlas page ``wall_decal_atlas_<page>`` that the adapter draws like
the shop decals, so the wall shows around it. The decals come from code (drawn here, deterministic, CC0 by
construction) and from the local decal catalogues of ADR-038 (``assetstore.decals``). The kinds are taken in a seeded
turn, so a map shows as many different decals as it has slots before any repeats. A decal covers no glass, door,
railing, stone frame or sign, stays off party walls, and stays under the wall top.
"""

from __future__ import annotations

import hashlib
import io
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from shapely import STRtree
from shapely.geometry import Point, Polygon, box

from onward_generator.generation.config import Material
from onward_generator.generation.gltf import Node, Primitive
from onward_generator.generation.mesh import Mesh

MIN_DECALS = 10
MAX_DECALS = 20
DECAL_MATERIAL = "wall_decal_atlas_{}"
ATLAS_PX = 2048
CELL_PX = 400
INNER_PX = CELL_PX - 8
"""A decal's room in its cell, 4 px of clear border on each side so mipmaps do not bleed into the next cell."""
WALL_GAP_M = 0.01
MIN_WALL_M = 2.5
PARTY_PROBE_M = 0.3
WALL_TOP_CLEAR_M = 0.5
GROUND_CLEAR_M = 0.15
DEFAULT_WALL_M = 6.0
GAP_M = 0.4
"""Clearance a decal keeps from another decal on its wall, and from what it must not cover."""
HEIGHTS_M = (1.4, 0.9, 2.4, 3.6, 5.2)
"""Centre heights tried on a wall, the eye line first."""
OBSTACLES = ("glass", "door", "railing", "frame_stone", "shop_sign", "street_sign", "balcony")
PROCEDURAL_SIZE = 256
UV = np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]])


@dataclass(frozen=True, slots=True)
class Decal:
    """A decal image: its id, kind, size on the wall and RGBA pixels."""

    id: str
    kind: str
    width_m: float
    height_m: float
    rgba: np.ndarray
    origin: str
    description: str = ""


@dataclass(frozen=True, slots=True)
class Wall:
    feature_id: str
    a: np.ndarray
    b: np.ndarray
    outward: np.ndarray
    height_m: float | None


@dataclass(frozen=True, slots=True)
class Placed:
    decal: Decal
    wall: Wall
    s_m: float
    z_m: float


def _unit(*key: object) -> float:
    digest = hashlib.sha256("|".join(map(str, key)).encode()).digest()
    return int.from_bytes(digest[:8]) / 2**64


def _rng(seed: int, name: str) -> np.random.Generator:
    return np.random.default_rng(int.from_bytes(hashlib.sha256(f"{seed}|{name}".encode()).digest()[:8]))


def _blur(image: Image.Image, radius: float) -> Image.Image:
    return image.filter(ImageFilter.GaussianBlur(radius))


def _rgba(colour: tuple[int, int, int], alpha: Image.Image) -> np.ndarray:
    pixels = np.zeros((alpha.height, alpha.width, 4), dtype=np.uint8)
    pixels[..., :3] = colour
    pixels[..., 3] = np.asarray(alpha)
    return pixels


def _damp(rng: np.random.Generator, n: int) -> np.ndarray:
    alpha = Image.new("L", (n, n))
    draw = ImageDraw.Draw(alpha)
    for _ in range(14):
        x, y, r = rng.uniform(0.3, 0.7) * n, rng.uniform(0.35, 0.75) * n, rng.uniform(0.08, 0.22) * n
        draw.ellipse((x - r, y - r, x + r, y + r), fill=int(rng.uniform(60, 120)))
    return _rgba((74, 60, 44), _blur(alpha, n * 0.06))


def _soot(rng: np.random.Generator, n: int) -> np.ndarray:
    alpha = Image.new("L", (n, n))
    draw = ImageDraw.Draw(alpha)
    for _ in range(40):
        x, w = rng.uniform(0.15, 0.85) * n, rng.uniform(0.02, 0.06) * n
        draw.rectangle((x - w, 0, x + w, rng.uniform(0.4) * n), fill=int(rng.uniform(30, 90)))
    return _rgba((28, 26, 25), _blur(alpha, n * 0.03))


def _rust(rng: np.random.Generator, n: int) -> np.ndarray:
    alpha = Image.new("L", (n, n))
    draw = ImageDraw.Draw(alpha)
    for _ in range(9):
        x = rng.uniform(0.2, 0.8) * n
        draw.line((x, 0, x + rng.uniform(-4, 4), rng.uniform(0.5) * n), fill=int(rng.uniform(110, 190)), width=int(rng.uniform(2, 6)))
    return _rgba((126, 66, 30), _blur(alpha, 2.0))


def _crack(rng: np.random.Generator, n: int) -> np.ndarray:
    alpha = Image.new("L", (n, n))
    draw = ImageDraw.Draw(alpha)
    for _ in range(3):
        x, y = rng.uniform(0.3, 0.7) * n, rng.uniform(0.1, 0.3) * n
        for _ in range(28):
            nx, ny = x + rng.uniform(-10, 10), y + rng.uniform(3, 12)
            draw.line((x, y, nx, ny), fill=230, width=2)
            x, y = nx, ny
    return _rgba((34, 30, 28), _blur(alpha, 0.6))


def _poster(rng: np.random.Generator, n: int) -> np.ndarray:
    paper = Image.new("RGBA", (n, n))
    draw = ImageDraw.Draw(paper)
    corners = [(0.12, 0.08), (0.88, 0.1), (0.86, 0.9), (0.14, 0.92)]
    torn = [(x * n + rng.uniform(-6, 6), y * n + rng.uniform(-6, 6)) for x, y in corners]
    base = tuple(int(v) for v in rng.integers(150, 230, 3))
    draw.polygon(torn, fill=(*base, 235))
    for _ in range(4):
        y0 = rng.uniform(0.15, 0.8) * n
        draw.rectangle((0.2 * n, y0, 0.8 * n, y0 + rng.uniform(0.04, 0.12) * n), fill=(*(int(v) for v in rng.integers(40, 200, 3)), 220))
    for _ in range(6):  # torn and weathered patches
        x, y, r = rng.uniform(0.1, 0.9) * n, rng.uniform(0.1, 0.9) * n, rng.uniform(0.03, 0.1) * n
        draw.ellipse((x - r, y - r, x + r, y + r), fill=(0, 0, 0, 0))
    return np.asarray(paper).copy()


def _scribble(rng: np.random.Generator, n: int) -> np.ndarray:
    alpha = Image.new("L", (n, n))
    draw = ImageDraw.Draw(alpha)
    points = [(rng.uniform(0.1, 0.9) * n, rng.uniform(0.3, 0.7) * n) for _ in range(9)]
    draw.line(points, fill=235, width=int(n * 0.04), joint="curve")
    red, green, blue = (int(v) for v in rng.integers(20, 230, 3))
    return _rgba((red, green, blue), _blur(alpha, 1.0))


def _grime(rng: np.random.Generator, n: int) -> np.ndarray:
    yy = np.linspace(0.0, 1.0, n)[:, None]
    field = np.clip((yy - 0.45) * 2.0, 0.0, 1.0) * rng.uniform(0.5, 1.0, (n, n)) * 120
    return _rgba((60, 54, 46), _blur(Image.fromarray(field.astype(np.uint8)), 3.0))


PROCEDURAL: Mapping[str, tuple[str, Callable[[np.random.Generator, int], np.ndarray], float, float, str]] = {
    "damp_stain": ("stain", _damp, 1.4, 1.4, "A brown damp stain spreading on a wall."),
    "soot_streak": ("soot", _soot, 1.0, 2.0, "Black soot streaks running down from above, as after a fire."),
    "rust_drip": ("rust", _rust, 0.8, 1.6, "Orange rust drips running down a wall under old metal."),
    "plaster_crack": ("crack", _crack, 1.0, 1.6, "Dark cracks branching through the plaster."),
    "torn_poster": ("poster", _poster, 0.7, 1.0, "A faded, torn paper poster without text."),
    "paint_scribble": ("graffiti", _scribble, 1.6, 0.9, "A single spray-paint scribble, abstract, no letters."),
    "splash_grime": ("grime", _grime, 2.4, 0.8, "Grime and splashes along the foot of a wall."),
}


def procedural_decals(seed: int) -> list[Decal]:
    """The decals drawn in code, one of each kind; the same seed draws the same pixels."""
    return [Decal(f"gen_{name}", kind, w, h, draw(_rng(seed, name), PROCEDURAL_SIZE), "generated", text)
            for name, (kind, draw, w, h, text) in PROCEDURAL.items()]


def library_decals(assets: Path) -> list[Decal]:
    """The decals of the local catalogues under ``assets`` (ADR-038), as loaded images."""
    from onward_generator.assetstore.decals import read_decal_catalogues

    found = []
    for image_path, row in read_decal_catalogues(assets):
        with Image.open(image_path) as picture:
            rgba = np.asarray(picture.convert("RGBA")).copy()
        found.append(Decal(str(row["id"]), str(row.get("kind") or "decal"), float(row["width_m"]), float(row["height_m"]), rgba, "library",
                           str(row.get("description") or "")))
    return found


def decal_count(seed: int) -> int:
    """How many decals a map gets: from ``MIN_DECALS`` to ``MAX_DECALS``, by its seed."""
    return MIN_DECALS + int(_unit(seed, "decal_count") * (MAX_DECALS - MIN_DECALS + 1))


def turn_order(decals: Sequence[Decal], count: int, seed: int) -> list[Decal]:
    """``count`` decals: every one once in a seeded order before any repeats."""
    if not decals:
        return []
    order = sorted(decals, key=lambda d: _unit(seed, "turn", d.id))
    return [order[i % len(order)] for i in range(count)]


def _plan(wall: Wall, s0: float, s1: float, depth: float) -> Polygon:
    along = (wall.b - wall.a) / float(np.linalg.norm(wall.b - wall.a))
    p0, p1 = wall.a + along * s0, wall.a + along * s1
    out = wall.outward * depth
    back = -wall.outward * 0.3
    return Polygon([p0 + back, p1 + back, p1 + out, p0 + out])


def exposed(wall: Wall, footprints: STRtree) -> bool:
    """Whether the ground just outside the middle of ``wall`` lies in no footprint: a party wall is not."""
    mid = (wall.a + wall.b) / 2 + wall.outward * PARTY_PROBE_M
    return not len(footprints.query(Point(*mid), predicate="intersects"))


def place_decals(walls: Sequence[Wall], footprints: STRtree, blocked: Callable[[Polygon, float, float], bool],
                 decals: Sequence[Decal], seed: int, count: int) -> list[Placed]:
    """Up to ``count`` decals from ``decals`` (in their turn) on the exposed ``walls`` of at least ``MIN_WALL_M``, walls
    taken in a seeded order from the one after the last decal's, each decal at the first height and position it fits:
    clear of ``blocked`` (a plan, bottom and top) and of the decals already on the wall. A decal that fits no wall is
    skipped."""
    candidates = sorted((w for w in walls if float(np.linalg.norm(w.b - w.a)) >= MIN_WALL_M and exposed(w, footprints)),
                        key=lambda w: _unit(seed, "wall", w.feature_id, float(w.a[0]), float(w.a[1])))
    placed: list[Placed] = []
    taken: dict[int, list[tuple[float, float, float, float]]] = {}
    start = 0
    for decal in turn_order(decals, count, seed):
        for k in range(len(candidates)):
            wall = candidates[(start + k) % len(candidates)]
            spot = _fit(wall, decal, blocked, taken.get(id(wall), []), seed)
            if spot is not None:
                s, z = spot
                placed.append(Placed(decal, wall, s, z))
                taken.setdefault(id(wall), []).append((s - decal.width_m / 2, s + decal.width_m / 2, z - decal.height_m / 2, z + decal.height_m / 2))
                start = (start + k + 1) % len(candidates)  # the next decal starts on the next wall
                break
    return placed


def _fit(wall: Wall, decal: Decal, blocked: Callable[[Polygon, float, float], bool], taken: Sequence[tuple[float, float, float, float]],
         seed: int) -> tuple[float, float] | None:
    length = float(np.linalg.norm(wall.b - wall.a))
    top = (wall.height_m or DEFAULT_WALL_M) - WALL_TOP_CLEAR_M
    half_w, half_h = decal.width_m / 2, decal.height_m / 2
    if decal.width_m + 2 * GAP_M > length:
        return None
    start = _unit(seed, "along", decal.id, wall.feature_id)
    steps = max(1, int((length - decal.width_m) / 0.5))
    for z in HEIGHTS_M:
        if z - half_h < GROUND_CLEAR_M or z + half_h > top:  # pragma: no mutate: no height less half a decal's lands on 0.15 m in floating point, so < and <= agree
            continue
        for k in range(steps + 1):
            s = half_w + ((start + k / (steps + 1)) % 1.0) * (length - decal.width_m)
            if any(s - half_w - GAP_M < s1 and s + half_w + GAP_M > s0 and z - half_h - GAP_M < z1 and z + half_h + GAP_M > z0  # pragma: no mutate: positions are sampled in steps from a seeded start, never exactly a gap apart, so a bound's < and <= agree
                   for s0, s1, z0, z1 in taken):
                continue
            if not blocked(_plan(wall, s - half_w - GAP_M, s + half_w + GAP_M, 0.3), z - half_h - GAP_M, z + half_h + GAP_M):
                return s, z
    return None


def _atlas(decals: Sequence[Decal]) -> tuple[dict[str, np.ndarray], dict[str, tuple[str, np.ndarray]]]:
    """Pages of ``CELL_PX`` cells, each decal scaled into its cell keeping its aspect; the page and UVs of each id."""
    per_row = ATLAS_PX // CELL_PX
    per_page = per_row * per_row
    pages: dict[str, np.ndarray] = {}
    slots: dict[str, tuple[str, np.ndarray]] = {}
    for index, decal in enumerate(decals):
        name = DECAL_MATERIAL.format(index // per_page)
        page = pages.setdefault(name, np.zeros((ATLAS_PX, ATLAS_PX, 4), dtype=np.uint8))
        cell = index % per_page
        x0, y0 = (cell % per_row) * CELL_PX, (cell // per_row) * CELL_PX
        image = Image.fromarray(decal.rgba)
        image.thumbnail((INNER_PX, INNER_PX), Image.Resampling.LANCZOS)
        w, h = image.size
        page[y0 + 4:y0 + 4 + h, x0 + 4:x0 + 4 + w] = np.asarray(image)
        u0, u1 = (x0 + 4) / ATLAS_PX, (x0 + 4 + w) / ATLAS_PX
        v1, v0 = 1 - (y0 + 4) / ATLAS_PX, 1 - (y0 + 4 + h) / ATLAS_PX
        slots[decal.id] = (name, np.array([[u0, v0], [u1, v0], [u1, v1], [u0, v1]]))
    return pages, slots


def decal_nodes(placed: Sequence[Placed]) -> tuple[list[Node], dict[str, Material], dict[str, bytes], list[dict[str, object]]]:
    """One node per decal, the transparent atlas materials and their PNGs, and a record per decal."""
    distinct = list({p.decal.id: p.decal for p in placed}.values())
    pages, slots = _atlas(distinct)
    nodes: list[Node] = []
    rows: list[dict[str, object]] = []
    for n, item in enumerate(placed):
        wall, decal = item.wall, item.decal
        along = (wall.b - wall.a) / float(np.linalg.norm(wall.b - wall.a))
        centre = wall.a + along * item.s_m + wall.outward * WALL_GAP_M
        lo, hi = centre - along * decal.width_m / 2, centre + along * decal.width_m / 2
        z0, z1 = item.z_m - decal.height_m / 2, item.z_m + decal.height_m / 2
        vertices = np.array([[lo[0], lo[1], z0], [hi[0], hi[1], z0], [hi[0], hi[1], z1], [lo[0], lo[1], z1]])
        page, uv = slots[decal.id]
        nodes.append(Node(f"wall_decal/{n}", (Primitive(Mesh(vertices, np.array([[0, 1, 2], [0, 2, 3]])), page, uv),),
                          {"type": "wall_decal", "decal": decal.id, "kind": decal.kind, "wall": wall.feature_id, "attribute_origin": "generated"}))
        rows.append({"id": f"wall_decal/{n}", "type": "wall_decal", "decal": decal.id, "kind": decal.kind, "origin": decal.origin,
                     "wall_feature": wall.feature_id, "position_m": [round(float(v), 3) for v in (*centre, item.z_m)],
                     "size_m": [decal.width_m, decal.height_m], "attribute_origin": "generated"})
    materials = {name: Material((1.0, 1.0, 1.0, 1.0), 0.8) for name in pages}
    images = {}
    for name, pixels in pages.items():
        out = io.BytesIO()
        Image.fromarray(pixels).save(out, format="PNG")
        images[name] = out.getvalue()
    return nodes, materials, images, rows


def decal_blocker(nodes: Sequence[Node], extra: Sequence[tuple[Polygon, float, float]] = ()) -> Callable[[Polygon, float, float], bool]:
    """What a decal must not cover: every triangle of a glass, door, railing, stone frame, sign or balcony material of
    ``nodes``, and ``extra`` as (plan, bottom, top)."""
    plans: list[Polygon] = [plan for plan, _, _ in extra]
    low = [b for _, b, _ in extra]
    high = [t for _, _, t in extra]
    for node in nodes:
        for prim in node.world_primitives():
            if not any(word in prim.material for word in OBSTACLES):
                continue
            tri = prim.mesh.vertices[prim.mesh.triangles]
            lows, highs = tri.min(axis=1), tri.max(axis=1)
            corners = zip(lows, highs, strict=True)  # pragma: no mutate: both come from one array, one row per triangle
            plans += [box(float(a[0]), float(a[1]), float(b[0]) + 1e-6, float(b[1]) + 1e-6) for a, b in corners]  # pragma: no mutate: the micrometre only gives a flat pane an area; box() orders its corners, so minus is the same pane
            low += lows[:, 2].tolist()
            high += highs[:, 2].tolist()
    tree = STRtree(plans)
    lows_a, highs_a = np.array(low), np.array(high)

    def blocked(plan: Polygon, bottom: float, top: float) -> bool:
        hits = tree.query(plan, predicate="intersects")
        return bool(((lows_a[hits] < top) & (highs_a[hits] > bottom)).any())

    return blocked


__all__ = ["DECAL_MATERIAL", "MAX_DECALS", "MIN_DECALS", "PROCEDURAL", "Decal", "Placed", "Wall", "decal_blocker", "decal_count",
           "decal_nodes", "exposed", "library_decals", "place_decals", "procedural_decals", "turn_order"]
