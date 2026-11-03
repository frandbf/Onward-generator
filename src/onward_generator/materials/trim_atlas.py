"""Bake the trim-sheet facade atlas of one style (ADR-020).

Every strip is painted in linear light and written as sRGB, so a pixel equals
what the per-material path drew as ``_BaseColor × _BaseMap`` with the same
greyscale photograph: the wall colour of the scene times the photo's grain.
Grime is baked in, because ``PP_Opaque_Simple_Lit`` has no other map: a
string course and the shadow under the next one in every storey, streaks from
the top of a storey and under the cornice, a splashed and darkened zocalo.

The photographs are CC0 library images (``config/dress/imported_library.json``);
everything else is drawn here. The same style, tints and photographs always give
the same pixels.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

from onward_generator.materials.trim_style import (
    BOARDED_CELL,
    DOOR_CELLS,
    SHOP_CELL,
    Cell,
    Strip,
    TrimLayout,
    window_cells,
)

# The cornice strip stands for a parapet of this height, the zocalo for ``zocalo_m``.
CORNICE_REF_M = 0.7
# The string course at the foot of every storey, and the light edge on top of it.
COURSE_M = 0.12
COURSE_EDGE_M = 0.03
# The shadow under the next string course fades in over this height.
UNDER_COURSE_M = 0.3
# Ashlar: course height and block length; joints darken by JOINT_DARK.
ASHLAR_COURSE_M = 0.4
ASHLAR_BLOCK_M = 0.8
ZOCALO_BLOCK_M = 0.75
JOINT_M = 0.015
JOINT_DARK = 0.28
# A photograph only lends its grain: it is divided by its mean, its contrast scaled, and the
# surface drawn at this fraction of its colour, so a dark rock photo does not darken a light stone.
BRIGHTNESS = 0.85
STONE_CONTRAST = 0.3
INF = np.inf
# Boards over a window, each 0.1 of its height.
PLANKS = 10


def between(value: Any, low: float, high: float) -> np.ndarray:
    """``low < value < high``, element-wise. Bounds may be ``±INF``.

    Callers pass pixel centres, random draws and triangle areas, which never sit on a bound.
    An infinite bound times zero is NaN, which is not above zero: still strict.
    """
    with np.errstate(invalid="ignore"):
        return np.asarray((value - low) * (high - value) > 0)  # pragma: no mutate: no caller passes a value on a bound (pixel centres, random draws, the doubled area of a Delaunay triangle), so > and >= agree


def grain(image: np.ndarray, contrast: float = 1.0) -> np.ndarray:
    """Linear grain of a resampled sRGB luma image: mean ``BRIGHTNESS``, deviations scaled by ``contrast``."""
    linear = srgb_to_linear(image)
    return BRIGHTNESS * (1.0 + contrast * (linear / linear.mean() - 1.0))


def srgb_to_linear(value: np.ndarray) -> np.ndarray:
    v = np.asarray(value)
    return np.where(between(v, -INF, 0.04045), v / 12.92, ((v + 0.055) / 1.055) ** 2.4)


def linear_to_srgb8(value: np.ndarray) -> np.ndarray:
    v = np.clip(value, 0.0, 1.0)
    s = np.where(between(v, -INF, 0.0031308), v * 12.92, 1.055 * v ** (1.0 / 2.4) - 0.055)
    return np.round(s * 255.0).astype(np.uint8)


def style_seed(name: str) -> int:
    return int.from_bytes(hashlib.sha256(f"trim:{name}".encode()).digest()[:8])


def sample_periodic(image: np.ndarray, width: int, height: int, span_m: tuple[float, float], tile_m: float) -> np.ndarray:
    """Resample ``image`` (one repeat of ``tile_m`` metres) over ``width × height`` pixels spanning ``span_m``.

    The horizontal span holds a whole number of repeats, so the result wraps
    seamlessly across the atlas width. Bilinear, wrapping on both axes. The
    image is first reduced (box filter, in float) to the pixels one repeat
    takes, so a 1k photo does not alias.
    """
    from PIL import Image

    repeats = max(1, round(span_m[0] / tile_m))
    tile_w = width / repeats
    tile_h = height * tile_m / span_m[1]
    src = np.asarray(image, dtype=np.float32)
    channels = [src] if src.ndim == 2 else [src[..., c] for c in range(src.shape[2])]
    size = (max(1, round(tile_w)), max(1, round(tile_h)))
    small = np.stack([np.asarray(Image.fromarray(np.ascontiguousarray(c)).resize(size, Image.Resampling.BOX)) for c in channels], axis=-1)
    h, w = small.shape[:2]
    xs = (np.arange(width) + 0.5) * w / tile_w - 0.5
    ys = (np.arange(height) + 0.5) * h / tile_h - 0.5
    x0 = np.floor(xs).astype(np.int64)
    y0 = np.floor(ys).astype(np.int64)
    fx = (xs - x0)[None, :, None]
    fy = (ys - y0)[:, None, None]
    xa, xb = x0 % w, (x0 + 1) % w
    ya, yb = y0 % h, (y0 + 1) % h
    top = small[ya][:, xa] * (1 - fx) + small[ya][:, xb] * fx
    bottom = small[yb][:, xa] * (1 - fx) + small[yb][:, xb] * fx
    out = top * (1 - fy) + bottom * fy
    return out[..., 0] if src.ndim == 2 else out


def _grid(width: int, height: int, span_m: tuple[float, float]) -> tuple[np.ndarray, np.ndarray]:
    """Metres across (x, left to right) and up (z, from the strip's bottom row) at each pixel centre."""
    x = (np.arange(width) + 0.5) / width * span_m[0]
    z = (height - np.arange(height) - 0.5) / height * span_m[1]
    return x[None, :], z[:, None]


def _mottle(rng: np.random.Generator, width: int, height: int, amount: float) -> np.ndarray:
    """Low-frequency blotches in ``1 ± amount`` that wrap across the width (whole periods only)."""
    xx = np.arange(width)[None, :] / width * 2 * np.pi
    yy = np.arange(height)[:, None] / height * 2 * np.pi
    field = np.zeros((height, width))
    for period in (3, 5, 9):
        phase_x, phase_y = rng.uniform(0, 2 * np.pi, 2)
        field += np.sin(period * xx + phase_x) * np.cos(yy * period / 3 + phase_y)
    return 1.0 + amount * field / 3.0


def _streaks(rng: np.random.Generator, x: np.ndarray, z: np.ndarray, span_m: tuple[float, float], grime: Mapping[str, Any], top_m: float) -> np.ndarray:
    """Dark streaks that run down from ``top_m`` and fade, wrapping across the width."""
    out = np.ones((z.shape[0], x.shape[1]))
    lo, hi = (float(v) for v in grime["streak_length_m"])
    for _ in range(int(grime["streaks_per_strip"])):
        x0 = rng.uniform(0, span_m[0])
        width = rng.uniform(0.02, 0.07)
        length = rng.uniform(lo, hi)
        dark = (0.3 + 0.7 * rng.random()) * float(grime["streak_dark"])
        dx = x - x0 - span_m[0] * np.round((x - x0) / span_m[0])
        across = np.maximum(1.0 - (dx / width) ** 2, 0.0)
        along = np.maximum((z - (top_m - length)) / length, 0.0) * between(z, -INF, top_m)
        out *= 1.0 - dark * across * along
    return out


def _joints(x: np.ndarray, z: np.ndarray, course_m: float, block_m: float, px_m: float) -> np.ndarray:
    """Ashlar joints: horizontal at every course, vertical every block, staggered by half a block."""
    half = max(JOINT_M, px_m) / 2
    course = np.floor(z / course_m)
    bed = between(np.abs(z - np.round(z / course_m) * course_m), -INF, half)
    shifted = x + (course % 2) * block_m / 2  # pragma: no mutate: a stagger of minus half a block draws the same joints as plus half
    head = between(np.abs(shifted - np.round(shifted / block_m) * block_m), -INF, half)
    return np.where(bed | head, 1.0 - JOINT_DARK, 1.0)


def wall_pixels(kind: str, tint: Sequence[float], width: int, height: int, atlas: Mapping[str, Any],
                sources: Mapping[str, np.ndarray], rng: np.random.Generator) -> np.ndarray:
    """One storey of wall: linear RGB, the bottom row at the floor line."""
    span = (float(atlas["wall_repeat_m"]), float(atlas["storey_ref_m"]))
    tiles = atlas["source_tile_m"]
    x, z = _grid(width, height, span)
    if kind == "brick":
        photo = srgb_to_linear(sample_periodic(sources["brick"], width, height, span, float(tiles["brick"])) / 255.0)
        base = photo / photo.mean(axis=(0, 1)) * np.asarray(tint) * BRIGHTNESS
    else:
        key = "stone" if kind == "ashlar" else "plaster"
        field = grain(sample_periodic(luma_rgb(sources[key]), width, height, span, float(tiles[key])), STONE_CONTRAST if kind == "ashlar" else 1.0)
        base = field[..., None] * np.asarray(tint)
        if kind == "ashlar":
            base = base * _joints(x, z, ASHLAR_COURSE_M, ASHLAR_BLOCK_M, span[1] / height)[..., None]
    grime = atlas["grime"]
    shade = _mottle(rng, width, height, float(grime["mottle"]))
    shade = shade * np.where(between(z, -INF, COURSE_M), 1.05, np.where(between(z, -INF, COURSE_M + COURSE_EDGE_M), 1.14, 1.0))
    under = np.maximum((z - (span[1] - UNDER_COURSE_M)) / UNDER_COURSE_M, 0.0)
    shade = shade * (1.0 - float(grime["under_course_dark"]) * under**2)
    shade = shade * _streaks(rng, x, z, span, grime, span[1] - COURSE_EDGE_M)
    return base * shade[..., None]


def luma_rgb(rgb: np.ndarray) -> np.ndarray:
    """Rec. 709 luma of an sRGB uint8 RGB image, still in sRGB (0–1)."""
    f = rgb / 255.0
    return 0.2126 * f[..., 0] + 0.7152 * f[..., 1] + 0.0722 * f[..., 2]


def zocalo_pixels(width: int, height: int, atlas: Mapping[str, Any], sources: Mapping[str, np.ndarray], rng: np.random.Generator) -> np.ndarray:
    """The plinth: two courses of stone, a lit chamfer on top, splashed and darkened at the foot."""
    span = (float(atlas["zocalo_repeat_m"]), float(atlas["zocalo_m"]))
    x, z = _grid(width, height, span)
    field = grain(sample_periodic(luma_rgb(sources["stone"]), width, height, span, float(atlas["source_tile_m"]["stone"])), STONE_CONTRAST)
    grime = atlas["grime"]
    shade = _joints(x, z, span[1] / 2, ZOCALO_BLOCK_M, span[1] / height)
    shade = shade * np.where(between(z, span[1] - 0.04, INF), 1.18, 1.0)
    splash = np.minimum(z / float(grime["base_splash_m"]), 1.0)
    shade = shade * (1.0 - float(grime["base_splash_dark"]) * (1.0 - splash) ** 2)
    shade = shade * np.where(between(rng.random((height, width)), -INF, 0.01), 0.7, 1.0)
    return field[..., None] * np.asarray(atlas["zocalo_color"]) * shade[..., None]


# The cornice profile from its foot: (top of the band in metres, light factor).
CORNICE_PROFILE = ((0.04, 0.55), (0.12, 0.85), (0.16, 1.12), (0.26, 0.95), (0.30, 1.15))


def cornice_pixels(width: int, height: int, atlas: Mapping[str, Any], sources: Mapping[str, np.ndarray], rng: np.random.Generator) -> np.ndarray:
    """A moulded cornice at the foot of the parapet, streaks below the coping, a shadow under it."""
    span = (float(atlas["cornice_repeat_m"]), CORNICE_REF_M)
    x, z = _grid(width, height, span)
    field = grain(sample_periodic(luma_rgb(sources["stone"]), width, height, span, float(atlas["source_tile_m"]["stone"])), STONE_CONTRAST)
    shade = np.ones((height, 1))
    for top, light in reversed(CORNICE_PROFILE):
        shade = np.where(between(z, -INF, top), light, shade)
    shade = shade * np.where(between(z, span[1] - 0.05, INF), 0.8, 1.0)
    shade = shade * _streaks(rng, x, z, span, atlas["grime"], span[1] - 0.05)
    return field[..., None] * np.asarray(atlas["cornice_color"]) * shade[..., None]


def stone_pixels(width: int, height: int, atlas: Mapping[str, Any], sources: Mapping[str, np.ndarray]) -> np.ndarray:
    """Window surrounds, sills, slabs and coping: fine stone, darker towards both edges of the strip."""
    span = (float(atlas["stone_repeat_m"]), float(atlas["stone_repeat_m"]) * height / width * 8)
    field = grain(sample_periodic(luma_rgb(sources["stone"]), width, height, span, float(atlas["source_tile_m"]["stone"]) / 2), STONE_CONTRAST)
    rel = (height - np.arange(height) - 0.5)[:, None] / height
    edge = np.minimum(rel, 1.0 - rel)
    shade = 0.82 + 0.18 * np.minimum(edge / 0.25, 1.0)
    return field[..., None] * np.asarray(atlas["stone_color"]) * shade[..., None]


def _cell_grid(width: int, height: int) -> tuple[np.ndarray, np.ndarray]:
    """Cell coordinates 0–1: x left to right, y bottom to top."""
    x = (np.arange(width) + 0.5) / width
    y = (height - np.arange(height) - 0.5) / height
    return x[None, :], y[:, None]


def _glass(x: np.ndarray, y: np.ndarray, reflect: float) -> np.ndarray:
    base = 0.035 + 0.05 * y + reflect * np.exp(-(((x - 0.6 * y - 0.1) / 0.08) ** 2))
    return np.stack([base, base * 1.15, base * 1.45], axis=-1)


def window_pixels(width: int, height: int, family: Mapping[str, Any], closed: float) -> np.ndarray:
    """A two-leaf window under a roller-shutter box, the shutter lowered over ``closed`` of the glass."""
    x, y = _cell_grid(width, height)
    rgb = _glass(x, y, 0.06)
    frame = ~between(x, 0.06, 0.94) | between(y, -INF, 0.05) | between(x, 0.485, 0.515) | between(np.abs(y - 0.88), -INF, 0.02)
    rgb = np.where(frame[..., None], np.asarray(family["frame"]), rgb)
    shutter = np.asarray(family["shutter"])
    rgb = np.where(between(y, 0.9, INF)[..., None], shutter * 0.8, rgb)
    bottom = 0.9 - closed * 0.85
    slat = 0.9 + 0.15 * ((y * 28.0) % 1.0)
    lowered = between(y, bottom, 0.9) & between(x, 0.02, 0.98)
    rgb = np.where(lowered[..., None], shutter * slat[..., None], rgb)
    rail = between(y, bottom, bottom + 0.02) & (closed > 0)
    rgb = np.where(rail[..., None], shutter * 0.55, rgb)
    return np.where(between(y, -INF, 0.02)[..., None], rgb * 0.6, rgb)


def door_pixels(width: int, height: int, color: Sequence[float], rng: np.random.Generator) -> np.ndarray:
    """A panelled two-leaf door: wood grain, two raised panels per leaf, a dark frame."""
    x, y = _cell_grid(width, height)
    wood = 0.85 + 0.1 * np.sin(x * 90.0 + 3.0 * np.sin(y * 7.0)) + 0.04 * rng.standard_normal((height, width))
    shade = np.broadcast_to(wood, (height, width)).copy()
    leaf_x = (x % 0.5) / 0.5
    for y0, y1 in ((0.08, 0.42), (0.5, 0.88)):
        inside = between(leaf_x, 0.15, 0.85) & between(y, y0, y1)
        rim = inside & (between(leaf_x, -INF, 0.19) | between(y, y1 - 0.02, INF))
        dark = inside & (between(leaf_x, 0.81, INF) | between(y, -INF, y0 + 0.02))
        shade = np.where(inside, shade * 1.08, shade)
        shade = np.where(rim, shade * 1.12, shade)
        shade = np.where(dark, shade * 0.7, shade)
    edges = between(x, 0.492, 0.508) | ~between(x, 0.04, 0.96) | between(y, 0.96, INF)
    shade = np.where(edges, shade * 0.55, shade)
    handle = (between(x, 0.445, 0.475) | between(x, 0.525, 0.555)) & between(y, 0.488, 0.512)
    rgb = shade[..., None] * np.asarray(color)
    return np.where(handle[..., None], np.asarray([0.55, 0.45, 0.25]), rgb)


def shop_glass_pixels(width: int, height: int) -> np.ndarray:
    """A display window or a glazed door: dark glass in a thin aluminium frame with a transom."""
    x, y = _cell_grid(width, height)
    rgb = _glass(x, y, 0.09)
    frame = ~between(x, 0.025, 0.975) | ~between(y, 0.03, 0.97) | between(y, 0.8, 0.82) | between(x, 0.494, 0.506)
    return np.where(frame[..., None], np.asarray([0.5, 0.51, 0.52]), rgb)


def boarded_pixels(width: int, height: int, rng: np.random.Generator) -> np.ndarray:
    """Weathered horizontal planks with dark gaps, over an abandoned window."""
    x, y = _cell_grid(width, height)
    plank = np.floor(y / 0.1)
    tone = (0.8 + 0.3 * rng.random(PLANKS))[plank.astype(np.int64)]
    shade = tone * (0.9 + 0.08 * np.sin(x * 60.0 + plank * 1.7))
    shade = np.where(between(y % 0.1, -INF, 0.008), 0.4, shade)
    return np.broadcast_to(shade, (height, width))[..., None] * np.asarray([0.36, 0.3, 0.24])


def _paint_strip(image: np.ndarray, strip: Strip, gutter: int, pixels: np.ndarray) -> None:
    """Write the strip's content rows and repeat its edge rows into the gutters, so filtering stays inside."""
    image[strip.row0 : strip.row1] = np.pad(pixels, ((gutter, gutter), (0, 0), (0, 0)), mode="edge")


def _paint_cell(image: np.ndarray, cell: Cell, gutter: int, pixels: np.ndarray) -> None:
    image[cell.row0 : cell.row1, cell.col0 : cell.col1] = np.pad(pixels, ((gutter, gutter), (gutter, gutter), (0, 0)), mode="edge")


def bake_trim_atlas(
    name: str,
    atlas: Mapping[str, Any],
    layout: TrimLayout,
    tints: Mapping[str, Sequence[float]],
    sources: Mapping[str, np.ndarray],
) -> np.ndarray:
    """The atlas as (height, size, 4) uint8 RGBA, opaque. ``tints`` are linear wall colours by wall id.

    Each wall colour has one strip per tone of the style: the colour times the tone.
    """
    rng = np.random.default_rng(style_seed(name))
    size, gutter = layout.size, layout.gutter
    image = np.zeros((layout.height, size, 3))

    def rows(strip: Strip) -> int:
        return strip.row1 - strip.row0 - 2 * gutter

    for wall in sorted(layout.tones):
        kind = str(atlas["walls"][wall])
        for index, strip in enumerate(layout.tones[wall]):
            tone = atlas["tones"][index]
            tint = [float(tints[wall][k]) * float(tone[k]) for k in range(3)]
            _paint_strip(image, strip, gutter, wall_pixels(kind, tint, size, rows(strip), atlas, sources, rng))
    _paint_strip(image, layout.zocalo, gutter, zocalo_pixels(size, rows(layout.zocalo), atlas, sources, rng))
    _paint_strip(image, layout.cornice, gutter, cornice_pixels(size, rows(layout.cornice), atlas, sources, rng))
    _paint_strip(image, layout.stone, gutter, stone_pixels(size, rows(layout.stone), atlas, sources))
    cell_px, fit_px = int(atlas["fit_cell_px"]), int(atlas["fit_row_px"])
    w, h = cell_px - 2 * gutter, fit_px - 2 * gutter
    names = iter(window_cells(atlas))
    for family in atlas["window_families"]:
        for closed in family["closed"]:
            _paint_cell(image, layout.cells[next(names)], gutter, window_pixels(w, h, family, float(closed)))
    colors = atlas["door_colors"]
    for index, door in enumerate(DOOR_CELLS):
        _paint_cell(image, layout.cells[door], gutter, door_pixels(w, h, colors[index % len(colors)], rng))
    _paint_cell(image, layout.cells[SHOP_CELL], gutter, shop_glass_pixels(w, h))
    _paint_cell(image, layout.cells[BOARDED_CELL], gutter, boarded_pixels(w, h, rng))
    # Right of the last cell the fit row is unused: the stone colour, not black, if filtering ever reaches it.
    image[:fit_px, len(layout.cells) * cell_px :] = image[layout.stone.row0 + gutter, 0]
    rgb = linear_to_srgb8(image)
    alpha = np.full((layout.height, size, 1), 255, dtype=np.uint8)
    return np.concatenate([rgb, alpha], axis=2)
