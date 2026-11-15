"""War damage (15 November 2026, plan 2026-11-15-ambiente-belico, increment A): more and heavier wall decals, and
decals on the ground, on a map whose ``damage`` asks for them.

``map.json`` ``"damage"`` is ``none`` (the default: the map is drawn as before, byte for byte), ``light``, ``heavy`` or
``auto``. ``auto`` reads the map's street profile: ``props_profile`` ``wrecked`` is ``heavy``, anything else ``none``.
No new field is asked of the model, so no stored street profile is invalidated.

With damage the wall pool gains three drawn kinds (a shell impact with the brick showing, a scorch mark and a spray
of shrapnel), the decal count rises with ``heavy`` and a share of the decals are war damage. The ground of the core
gets flat decals (road cracks, patched potholes, sand drifts, scorch and flat rubble) lying 1.5 cm above the
carriageway, sidewalk or open ground, on the same transparent atlas pages as the wall decals, so the adapter draws them
alike and no material is added. Everything here is drawn by code: CC0 by construction, no letters or numbers.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw
from shapely import STRtree
from shapely.geometry import Point, Polygon, box
from shapely.geometry.base import BaseGeometry

from onward_generator.generation.gltf import Node
from onward_generator.signs.decals import PROCEDURAL_SIZE, Decal, _blur, _rgba, _rng, _unit, turn_order

LEVELS = ("none", "light", "heavy")
"""The damage a map is drawn with; ``auto`` resolves to one of them."""
WAR_KINDS = frozenset({"bullets", "crack", "soot", "peeling", "shell_impact", "scorch", "shrapnel"})
"""Wall decal kinds that read as war damage."""
WAR_SHARE = {"light": 0.4, "heavy": 0.6}
"""The least share of a map's wall decals that are war damage."""
HEAVY_DECALS = (60, 90)
"""Wall decals on a ``heavy`` map, by its seed (30 to 50 otherwise)."""
GROUND_DECALS = {"light": (12, 20), "heavy": (30, 45)}
"""Ground decals by level, by the map's seed."""
GROUND_NODES = ("surface/carriageway", "surface/sidewalk", "surface/ground")
"""The ground a decal may lie on."""
GROUND_AVOID = ("zebra", "crossing", "manhole", "marking")
"""Materials a ground decal does not cover."""
GROUND_GAP_M = 0.015
"""How far above the ground a ground decal lies (no z-fighting)."""
GROUND_FLAT_M = 0.03
"""The ground under a decal's centre and corners lies within this height (it does not bridge a kerb)."""
GROUND_SPACING_M = 0.5
"""The least gap between two ground decals."""
GROUND_TRIES = 25
"""Spots tried per wanted ground decal."""
RAY_TOP_M = 200.0


def resolve_damage(value: object, props_profile: str | None) -> tuple[str, str]:
    """The level and where it came from (``map`` or ``street_profile``) for a ``map.json`` ``damage`` value."""
    if value is None:
        return "none", "default"
    if value == "auto":
        return ("heavy" if props_profile == "wrecked" else "none"), "street_profile"
    if value in LEVELS:
        return str(value), "map"
    raise ValueError(f'map.json "damage" must be one of auto, none, light, heavy, got {value!r}')


# Wall kinds -------------------------------------------------------------------------------------------------------


def _shell_impact(rng: np.random.Generator, n: int) -> np.ndarray:
    """A crater of render blown off a wall: brick or block courses showing, a dark centre, chips and cracks round it."""
    pixels = np.zeros((n, n, 4), dtype=np.uint8)
    cx, cy = n * rng.uniform(0.45, 0.55), n * rng.uniform(0.45, 0.55)
    corners = [(cx + n * rng.uniform(0.22, 0.38) * math.cos(a), cy + n * rng.uniform(0.18, 0.34) * math.sin(a))
               for a in np.linspace(0.0, 2 * math.pi, 14, endpoint=False)]  # pragma: no mutate: endpoint None is as false as False
    spall = Image.new("L", (n, n))
    ImageDraw.Draw(spall).polygon(corners, fill=255)  # pragma: no mutate: PIL clamps an L fill to 255
    inside = np.asarray(_blur(spall, 1.2)) > 128
    course = max(4, n // 24)  # pragma: no mutate: n is PROCEDURAL_SIZE, 256: n // 24 and n // 25 are both 10, above the floor
    yy, xx = np.mgrid[0:n, 0:n]
    mortar = (yy % course < 2) | (((xx + (yy // course % 2) * course) % (course * 2)) < 2)
    brick = np.where(mortar[..., None], np.array([150, 140, 126]), np.array([150, 86, 60]) + rng.integers(-18, 18, (n, n, 1)))
    pixels[..., :3] = np.where(inside[..., None], brick, np.array([200, 192, 178]))
    centre = Image.new("L", (n, n))
    r = n * rng.uniform(0.07, 0.11)
    ImageDraw.Draw(centre).ellipse((cx - r, cy - r, cx + r, cy + r), fill=255)  # pragma: no mutate: PIL clamps an L fill to 255
    dark = np.asarray(_blur(centre, n * 0.03)) / 255.0
    pixels[..., :3] = (pixels[..., :3] * (1 - 0.85 * dark[..., None])).astype(np.uint8)  # pragma: no mutate: assigning into the uint8 page casts the same way
    chips = Image.new("L", (n, n))
    draw = ImageDraw.Draw(chips)
    for _ in range(int(rng.integers(30, 50))):
        a, d = rng.uniform(0, 2 * math.pi), n * rng.uniform(0.3, 0.47)
        x, y, s = cx + d * math.cos(a), cy + d * math.sin(a), n * rng.uniform(0.006, 0.018)
        draw.ellipse((x - s, y - s, x + s, y + s), fill=int(rng.uniform(140, 220)))
    for _ in range(5):
        a = rng.uniform(0, 2 * math.pi)
        x, y = cx + n * 0.3 * math.cos(a), cy + n * 0.3 * math.sin(a)
        for _ in range(8):
            nx, ny = x + n * 0.03 * math.cos(a) + rng.uniform(-3, 3), y + n * 0.03 * math.sin(a) + rng.uniform(-3, 3)
            draw.line((x, y, nx, ny), fill=230, width=2)
            x, y = nx, ny
    alpha = np.maximum(inside * 255, np.asarray(_blur(chips, 0.6)))
    pixels[..., 3] = alpha.astype(np.uint8)  # pragma: no mutate: assigning into the uint8 page casts the same way
    return pixels


def _scorch(rng: np.random.Generator, n: int) -> np.ndarray:
    """A black scorch mark: a dark core fading out, with soot licking upward."""
    alpha = Image.new("L", (n, n))
    draw = ImageDraw.Draw(alpha)
    cx, cy = n * 0.5, n * rng.uniform(0.6, 0.72)
    for _ in range(26):
        r = n * rng.uniform(0.06, 0.24)
        x, y = cx + rng.normal(0, n * 0.08), cy + rng.normal(0, n * 0.05)
        draw.ellipse((x - r, y - r, x + r, y + r), fill=int(rng.uniform(110, 200)))
    for _ in range(14):
        x, w = cx + rng.normal(0, n * 0.12), n * rng.uniform(0.02, 0.05)
        draw.rectangle((x - w, n * rng.uniform(0.05, 0.4), x + w, cy), fill=int(rng.uniform(70, 140)))
    return _rgba((14, 12, 11), _blur(alpha, n * 0.025))


def _shrapnel(rng: np.random.Generator, n: int) -> np.ndarray:
    """A spray of small pits thrown out from one side, densest near its source."""
    pixels = np.zeros((n, n, 4), dtype=np.uint8)
    pits, chips = Image.new("L", (n, n)), Image.new("L", (n, n))
    pit_draw, chip_draw = ImageDraw.Draw(pits), ImageDraw.Draw(chips)
    sx, sy = n * rng.uniform(-0.1, 0.15), n * rng.uniform(0.6, 1.0)
    for _ in range(int(rng.integers(70, 120))):
        a, d = rng.uniform(-1.2, 0.2), n * abs(rng.normal(0.45, 0.25))
        x, y, s = sx + d * math.cos(a), sy + d * math.sin(a), n * rng.uniform(0.004, 0.012)
        chip_draw.ellipse((x - s * 2.2, y - s * 2.2, x + s * 2.2, y + s * 2.2), fill=int(rng.uniform(90, 170)))
        pit_draw.ellipse((x - s, y - s, x + s, y + s), fill=240)
    chipped, pit = np.asarray(_blur(chips, 0.8)), np.asarray(_blur(pits, 0.5))
    pixels[..., :3] = np.where(pit[..., None] > 40, np.array([28, 25, 23]), np.array([196, 188, 174]))
    pixels[..., 3] = np.maximum(chipped, pit)
    return pixels


WAR_WALL = {
    "shell_impact": ("shell_impact", _shell_impact, 1.6, 1.4, "A shell impact: render blown off, brick showing, a dark crater."),
    "scorch_mark": ("scorch", _scorch, 1.6, 1.8, "A black scorch mark with soot licking upward, as after a blast or fire."),
    "shrapnel_spray": ("shrapnel", _shrapnel, 1.8, 1.2, "A spray of shrapnel pits thrown across a wall."),
}


# Ground kinds -----------------------------------------------------------------------------------------------------


def _road_crack(rng: np.random.Generator, n: int) -> np.ndarray:
    alpha = Image.new("L", (n, n))
    draw = ImageDraw.Draw(alpha)
    for _ in range(int(rng.integers(2, 4))):
        x, y, a = rng.uniform(0.1, 0.25) * n, rng.uniform(0.35, 0.65) * n, rng.uniform(-0.4, 0.4)
        for _ in range(40):
            a += rng.normal(0, 0.3)
            nx, ny = min(max(x + 5 * math.cos(a), 4.0), n - 4.0), min(max(y + 5 * math.sin(a), 4.0), n - 4.0)
            draw.line((x, y, nx, ny), fill=235, width=int(rng.integers(2, 4)))
            if rng.random() < 0.12:  # pragma: no mutate: a uniform draw never lands on the threshold itself
                b = a + rng.choice([-1.0, 1.0]) * rng.uniform(0.6, 1.2)
                draw.line((nx, ny, nx + 18 * math.cos(b), ny + 18 * math.sin(b)), fill=200, width=2)
            x, y = nx, ny
    return _rgba((24, 22, 21), _blur(alpha, 0.7))


def _pothole_patch(rng: np.random.Generator, n: int) -> np.ndarray:
    pixels = np.zeros((n, n, 4), dtype=np.uint8)
    patch = Image.new("L", (n, n))
    corners = [(x * n + rng.uniform(-8, 8), y * n + rng.uniform(-8, 8)) for x, y in ((0.12, 0.18), (0.88, 0.14), (0.9, 0.84), (0.1, 0.86))]
    ImageDraw.Draw(patch).polygon(corners, fill=255)  # pragma: no mutate: PIL clamps an L fill to 255
    hole = Image.new("L", (n, n))
    r = n * rng.uniform(0.1, 0.18)
    hx, hy = n * rng.uniform(0.35, 0.65), n * rng.uniform(0.35, 0.65)
    ImageDraw.Draw(hole).ellipse((hx - r * 1.4, hy - r, hx + r * 1.4, hy + r), fill=255)  # pragma: no mutate: PIL clamps an L fill to 255
    inside, pit = np.asarray(_blur(patch, 1.0)), np.asarray(_blur(hole, 2.0)) / 255.0
    tone = np.array([46, 44, 42]) + rng.integers(-6, 6, (n, n, 1))
    pixels[..., :3] = (tone * (1 - 0.6 * pit[..., None])).astype(np.uint8)  # pragma: no mutate: assigning into the uint8 page casts the same way
    pixels[..., 3] = np.maximum(inside * 0.85, pit * 255).astype(np.uint8)  # pragma: no mutate: assigning into the uint8 page casts the same way
    return pixels


def _sand_drift(rng: np.random.Generator, n: int) -> np.ndarray:
    yy = np.linspace(0.0, 1.0, n)[:, None]
    xx = np.linspace(0.0, 2 * math.pi, n)[None, :]
    edge = 0.45 + 0.12 * np.sin(xx * rng.uniform(1.0, 2.5) + rng.uniform(0, 6)) + 0.05 * np.sin(xx * 7)
    sides = np.sin(np.linspace(0.0, math.pi, n))[None, :] ** 0.6 * np.clip((1.0 - yy) * 8.0, 0.0, 1.0)  # pragma: no mutate: fades out at every edge; (1 - yy) * 8 is never below 0
    field = np.clip((yy - edge) * 3.0, 0.0, 1.0) * sides * rng.uniform(0.75, 1.0, (n, n)) * 200
    return _rgba((196, 172, 130), _blur(Image.fromarray(field.astype(np.uint8)), 4.0))


def _scorch_ground(rng: np.random.Generator, n: int) -> np.ndarray:
    alpha = Image.new("L", (n, n))
    draw = ImageDraw.Draw(alpha)
    for _ in range(30):
        r = n * rng.uniform(0.05, 0.22)
        x, y = n * 0.5 + rng.normal(0, n * 0.1), n * 0.5 + rng.normal(0, n * 0.1)
        draw.ellipse((x - r, y - r, x + r, y + r), fill=int(rng.uniform(120, 210)))
    for _ in range(18):  # blast rays
        a, d = rng.uniform(0, 2 * math.pi), n * rng.uniform(0.25, 0.48)
        draw.line((n * 0.5, n * 0.5, n * 0.5 + d * math.cos(a), n * 0.5 + d * math.sin(a)), fill=int(rng.uniform(90, 160)), width=int(rng.integers(3, 8)))
    return _rgba((12, 11, 10), _blur(alpha, n * 0.02))


def _rubble_flat(rng: np.random.Generator, n: int) -> np.ndarray:
    paint = Image.new("RGBA", (n, n))
    draw = ImageDraw.Draw(paint)
    for _ in range(int(rng.integers(60, 110))):
        x, y = rng.normal(0.5, 0.18, 2).clip(0.05, 0.95) * n
        s = n * rng.uniform(0.01, 0.045)
        corners = [(x + s * rng.uniform(0.5, 1.3) * math.cos(a), y + s * rng.uniform(0.5, 1.3) * math.sin(a))
                   for a in np.linspace(0, 2 * math.pi, 5, endpoint=False)]  # pragma: no mutate: endpoint None is as false as False
        grey = int(rng.uniform(110, 190))
        colour = (grey, grey - 6, grey - 14) if rng.random() < 0.7 else (int(rng.uniform(130, 170)), 76, 52)  # pragma: no mutate: concrete or brick; a uniform draw never lands on the threshold itself
        draw.polygon(corners, fill=(*colour, 255))  # pragma: no mutate: PIL clamps an RGBA fill to 255
    dust = Image.new("L", (n, n))
    ImageDraw.Draw(dust).ellipse((n * 0.12, n * 0.12, n * 0.88, n * 0.88), fill=90)
    pixels = np.asarray(paint).copy()
    under = np.asarray(_blur(dust, n * 0.08))
    bare = pixels[..., 3] == 0
    pixels[bare, :3] = (150, 140, 124)
    pixels[..., 3] = np.maximum(pixels[..., 3], under)
    return pixels


WAR_GROUND = {
    "road_crack": ("road_crack", _road_crack, 2.4, 2.4, "Dark cracks branching across worn asphalt or concrete."),
    "pothole_patch": ("pothole", _pothole_patch, 1.8, 1.4, "A crude asphalt patch with a pothole broken through it."),
    "sand_drift": ("sand", _sand_drift, 3.0, 1.2, "Sand and dust drifted along the foot of a kerb."),
    "ground_scorch": ("ground_scorch", _scorch_ground, 2.2, 2.2, "A black blast scorch on the ground."),
    "flat_rubble": ("rubble", _rubble_flat, 2.0, 2.0, "Broken concrete and brick scattered flat on the ground."),
}


def war_wall_decals(seed: int) -> list[Decal]:
    """The drawn war damage wall decals, one of each kind; the same seed draws the same pixels."""
    return [Decal(f"gen_{name}", kind, w, h, draw(_rng(seed, name), PROCEDURAL_SIZE), "generated", text)
            for name, (kind, draw, w, h, text) in WAR_WALL.items()]


def ground_decals(seed: int) -> list[Decal]:
    """The drawn ground decals, one of each kind."""
    return [Decal(f"gen_{name}", kind, w, h, draw(_rng(seed, name), PROCEDURAL_SIZE), "generated", text)
            for name, (kind, draw, w, h, text) in WAR_GROUND.items()]


def wall_count(seed: int, level: str, base: int) -> int:
    """How many wall decals: ``base`` (30 to 50) unless ``heavy``, then ``HEAVY_DECALS`` by the seed."""
    if level != "heavy":
        return base
    low, high = HEAVY_DECALS
    return low + int(_unit(seed, "war_decal_count") * (high - low + 1))


def ground_count(seed: int, level: str) -> int:
    """How many ground decals ``level`` wants: none without damage."""
    if level not in GROUND_DECALS:
        return 0
    low, high = GROUND_DECALS[level]
    return low + int(_unit(seed, "ground_decal_count") * (high - low + 1))


def war_sequence(pool: Sequence[Decal], war: Sequence[Decal], count: int, seed: int, level: str) -> list[Decal]:
    """``count`` wall decals in their turn, at least ``WAR_SHARE`` of them war damage: the war kinds of ``pool`` and
    ``war`` in one turn, the rest of ``pool`` in another, interleaved so the war damage spreads over the walls."""
    wars = list({d.id: d for d in [*war, *(d for d in pool if d.kind in WAR_KINDS)]}.values())
    rest = [d for d in pool if d.kind not in WAR_KINDS]
    wanted = math.ceil(count * WAR_SHARE[level]) if rest else count
    first, second = turn_order(wars, wanted, seed), turn_order(rest, count - wanted, seed)
    sequence: list[Decal] = []
    for index in range(max(len(first), len(second))):
        sequence += [*first[index:index + 1], *second[index:index + 1]]
    return sequence


@dataclass(frozen=True, slots=True)
class GroundPlaced:
    decal: Decal
    centre: tuple[float, float, float]
    yaw: float

    def corners(self) -> np.ndarray:
        """The quad's four corners, counter-clockwise seen from above."""
        c, s = math.cos(self.yaw), math.sin(self.yaw)
        hw, hh = self.decal.width_m / 2, self.decal.height_m / 2
        local = [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)]
        x, y, z = self.centre
        return np.array([[x + u * c - v * s, y + u * s + v * c, z] for u, v in local])


class Ground:
    """The top ground under a point: the highest face of the built scene above it, and whether a decal may lie there."""

    def __init__(self, nodes: Sequence[Node]) -> None:
        triangles, allowed = [], []
        for node in nodes:
            ok_node = node.name.startswith(GROUND_NODES)
            for prim in node.world_primitives():
                tri = prim.mesh.vertices[prim.mesh.triangles]
                triangles.append(tri)
                allowed.append(np.full(len(tri), ok_node and not any(word in prim.material for word in GROUND_AVOID)))
        self.tris = np.concatenate(triangles) if triangles else np.zeros((0, 3, 3))
        self.allowed = np.concatenate(allowed) if allowed else np.zeros(0, dtype=bool)
        lows, highs = self.tris.min(axis=1), self.tris.max(axis=1)
        self.tree = STRtree([box(float(a[0]), float(a[1]), float(b[0]) + 1e-6, float(b[1]) + 1e-6) for a, b in zip(lows, highs, strict=True)])  # pragma: no mutate: the micrometre only gives an upright face an area

    def top(self, x: float, y: float) -> tuple[float, bool] | None:
        """The height of the highest face over (x, y) and whether a ground decal may lie on it; ``None`` over nothing."""
        best: tuple[float, bool] | None = None
        for index in self.tree.query(Point(x, y), predicate="intersects"):
            a, b, c = self.tris[index]
            d = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
            if abs(d) < 1e-12:  # an upright face: no height over a point
                continue
            u = ((b[1] - c[1]) * (x - c[0]) + (c[0] - b[0]) * (y - c[1])) / d
            v = ((c[1] - a[1]) * (x - c[0]) + (a[0] - c[0]) * (y - c[1])) / d
            if u < -1e-9 or v < -1e-9 or u + v > 1 + 1e-9:
                continue
            z = u * a[2] + v * b[2] + (1 - u - v) * c[2]
            if z <= RAY_TOP_M and (best is None or z > best[0]):
                best = (float(z), bool(self.allowed[index]))
        return best


def place_ground(ground: Ground, area: BaseGeometry, decals: Sequence[Decal], count: int, seed: int) -> list[GroundPlaced]:
    """Up to ``count`` ground decals in their turn at seeded spots inside ``area``: where the top face under the
    centre and the four corners is allowed ground (``GROUND_NODES``, not a crossing or a manhole), all within
    ``GROUND_FLAT_M`` of one height, and ``GROUND_SPACING_M`` clear of the decals already down."""
    if area.is_empty:  # no decal or no count leaves the turn empty below
        return []
    rng = _rng(seed, "ground_spots")
    x0, y0, x1, y1 = area.bounds
    placed: list[GroundPlaced] = []
    taken: list[Polygon] = []
    order = turn_order(decals, count, seed)
    tries = 0
    for decal in order:
        while tries < GROUND_TRIES * count:
            tries += 1
            x, y, yaw = rng.uniform(x0, x1), rng.uniform(y0, y1), rng.uniform(0.0, math.pi)
            if not area.contains(Point(x, y)):
                continue
            candidate = GroundPlaced(decal, (x, y, 0.0), yaw)  # pragma: no mutate: the trial quad's height is never read, only its plan
            plan = Polygon(candidate.corners()[:, :2])  # pragma: no mutate: shapely's plan distance ignores z
            if any(plan.distance(other) < GROUND_SPACING_M for other in taken):  # pragma: no mutate: no two quads lie exactly 0.5 m apart to the bit
                continue
            tops = [ground.top(px, py) for px, py in [(x, y), *candidate.corners()[:, :2].tolist()]]
            if any(t is None or not t[1] for t in tops):
                continue
            heights = [t[0] for t in tops if t is not None]
            if max(heights) - min(heights) > GROUND_FLAT_M:  # pragma: no mutate: no ground differs by exactly 3 cm to the bit
                continue
            placed.append(GroundPlaced(decal, (x, y, max(heights) + GROUND_GAP_M), yaw))
            taken.append(plan)
            break
    return placed


__all__ = ["GROUND_DECALS", "HEAVY_DECALS", "LEVELS", "WAR_GROUND", "WAR_KINDS", "WAR_SHARE", "WAR_WALL", "Ground", "GroundPlaced",
           "ground_count", "ground_decals", "place_ground", "resolve_damage", "wall_count", "war_sequence", "war_wall_decals"]
