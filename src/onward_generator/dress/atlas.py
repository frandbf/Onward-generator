"""Material atlas: procedural greyscale tiles plus optional licensed photo slots.

Tiles default to a mid-grey *pattern* (not the material colour). The Onward
shader multiplies ``_BaseMap`` by ``_BaseColor``; a coloured or empty tile made
trees and metal sample near-black on Quest. Photographic albedo is opt-in per
slot and is supplied by the ``materials`` stage, not invented here.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from io import BytesIO

import numpy as np

from onward_generator.dress.catalog import MaterialCatalog
from onward_generator.generation.config import Material

MIN_TILE_PX = 256
MIN_MEAN_LUMA = 80  # unused / fallback tiles stay visible after shader multiply
PNG_LEVEL = 9  # zlib level of an RGBA PNG (windows, signs, plaques, growth, dress tiles)
# zlib level of an opaque tile (materials repeats and the contact sheet). Level 3 encodes about
# three times faster than 9 for about 4 % more bytes; Unity recompresses the textures anyway.
TILE_PNG_LEVEL = 3
PNG_FORMAT = "PNG"  # pragma: no mutate: Pillow reads the format name case-insensitively
# Threads that bake and encode texture PNGs at once. zlib and Pillow's resize release the GIL.
PNG_WORKERS = 6  # pragma: no mutate: the pool size changes speed, never the bytes or their order


@dataclass(frozen=True, slots=True)
class Atlas:
    png: bytes
    size: int
    tiles: int
    slots: dict[str, tuple[float, float, float, float]]  # material id → (u0, v0, u1, v1)
    note: str


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def encode_png_rgba(pixels: np.ndarray) -> bytes:
    """8-bit RGBA PNG through Pillow at ``PNG_LEVEL``. ``pixels`` is (h, w, 4) uint8.

    Fixed save options and no metadata chunks, so the same pixels give the
    same bytes for the pinned Pillow version.
    """
    _check_rgba(pixels)
    return encode_png(pixels, PNG_LEVEL)


def encode_opaque_png(pixels: np.ndarray) -> bytes:
    """An (h, w, 4) uint8 tile whose alpha is 255 everywhere, as an 8-bit RGB PNG at ``TILE_PNG_LEVEL``.

    The alpha channel carries nothing, so it is not written: the PNG is smaller
    and faster to encode, and decodes to the same RGBA. A tile with any other
    alpha is refused.
    """
    _check_rgba(pixels)
    if not (pixels[..., 3] == 255).all():
        raise ValueError("an opaque tile has alpha 255 everywhere")
    return encode_png(pixels[..., :3], TILE_PNG_LEVEL)


def _check_rgba(pixels: np.ndarray) -> None:
    if pixels.ndim != 3 or pixels.shape[2] != 4 or pixels.dtype != np.uint8:
        raise ValueError("pixels must be an HxWx4 uint8 array")


def encode_png(pixels: np.ndarray, level: int) -> bytes:
    """``pixels`` (h, w, 3 or 4, uint8) as a PNG at zlib ``level``, with fixed save options and no metadata."""
    from PIL import Image

    image = Image.fromarray(np.ascontiguousarray(pixels))
    buffer = BytesIO()
    image.save(buffer, format=PNG_FORMAT, compress_level=level)
    return buffer.getvalue()


def run_png_jobs[K, T](jobs: Mapping[K, Callable[[], T]]) -> dict[K, T]:
    """Run each job on a pool of ``PNG_WORKERS`` threads and return its result under the same key, in key order.

    A job bakes and encodes one image. Each job is pure, so the bytes are the
    same as running the jobs one after the other. The first exception is raised.
    """
    with ThreadPoolExecutor(max_workers=PNG_WORKERS) as pool:  # pragma: no mutate: the pool size changes speed, never the bytes or their order
        return dict(zip(jobs, pool.map(_call, jobs.values())))


def _call[T](job: Callable[[], T]) -> T:
    return job()


def decode_png_rgba(data: bytes) -> np.ndarray:
    """Decode a PNG to (h, w, 4) uint8 RGBA. Anything that is not a PNG is refused."""
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError("not a PNG")
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(BytesIO(data)) as image:
            if image.format != "PNG":
                raise ValueError("not a PNG")
            return np.asarray(image.convert("RGBA"), dtype=np.uint8)
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise ValueError(f"unreadable PNG: {exc}") from exc


def _grid(n: int) -> tuple[int, int]:
    """Square power-of-two grid that fits ``n`` tiles; 256 px per tile, at least 512."""
    count = max(int(n), 1)
    tiles = 4
    while tiles * tiles < count:
        tiles *= 2
    return tiles, max(512, tiles * MIN_TILE_PX)


def pattern_kind(color_id: str) -> str:
    if color_id.startswith("tree_canopy") or color_id == "green":
        return "foliage"
    if color_id.startswith("tree_trunk") or color_id == "door_wood":
        return "wood"
    if color_id.startswith("roof_tile"):
        return "tiles"
    if color_id == "carriageway":
        return "asphalt"
    if color_id in {"sidewalk", "pedestrian", "curb", "stair", "ground"}:
        return "paving"
    if "metal" in color_id:
        return "metal"
    if color_id.startswith("plaster") or color_id.startswith("stone") or color_id.startswith("roof_flat"):
        return "plaster"
    if color_id == "water":
        return "water"
    if color_id.startswith("glass"):
        return "glass"
    return "speckle"


def _modulation(kind: str, size: int, seed: int) -> np.ndarray:
    """Per-pixel multiplier in ~0.62–1.12 so ``_BaseColor * _BaseMap`` stays readable."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float64)
    noise = rng.normal(0.0, 1.0, (size, size))
    if kind == "asphalt":
        grit = 0.88 + 0.10 * noise
        grit[rng.random((size, size)) < 0.035] *= 0.55
        return grit
    if kind == "paving":
        cell = max(size // 8, 8)
        grout = ((xx % cell) < 2) | ((yy % cell) < 2)
        field = 0.96 + 0.04 * noise
        return np.where(grout, 0.62, field)
    if kind == "tiles":
        row = max(size // 10, 8)
        wave = ((xx + 5.0 * np.sin(yy / 6.0)) % (row * 1.6)) < 2.0
        grout_y = (yy % row) < 2
        field = 0.94 + 0.06 * noise
        return np.where(wave | grout_y, 0.58, field)
    if kind == "wood":
        return 0.82 + 0.14 * np.sin(xx / 2.8) + 0.05 * noise
    if kind == "foliage":
        blobs = np.sin(xx / 9.0) * np.cos(yy / 11.0)
        return 0.80 + 0.16 * blobs + 0.08 * noise
    if kind == "metal":
        return 0.78 + 0.16 * np.sin(yy / 2.2) + 0.04 * noise
    if kind == "plaster":
        mottling = np.sin((xx + yy) / 13.0) * np.cos(xx / 17.0)
        return 0.86 + 0.10 * mottling + 0.06 * noise
    if kind == "water":
        return 0.76 + 0.14 * np.sin(xx / 7.0 + yy / 11.0) + 0.04 * noise
    if kind == "glass":
        return 0.72 + 0.10 * np.sin((xx + yy) / 8.0) + 0.03 * noise
    return 0.88 + 0.08 * noise


def _tile_pixels(color_id: str, size: int, seed: int) -> np.ndarray:
    mod = np.clip(_modulation(pattern_kind(color_id), size, seed), 0.55, 1.15)
    # Neutral grey so the shader tint (``_BaseColor``) keeps the material hue.
    rgb = np.clip(np.round(200.0 * mod[..., None]), MIN_MEAN_LUMA, 255).astype(np.uint8)
    rgb = np.broadcast_to(rgb, (size, size, 3)).copy()
    rgb[0, :, :] = np.clip(rgb[0, :, :].astype(np.int16) - 18, 0, 255).astype(np.uint8)
    rgb[:, 0, :] = np.clip(rgb[:, 0, :].astype(np.int16) - 18, 0, 255).astype(np.uint8)
    alpha = np.full((size, size, 1), 255, dtype=np.uint8)
    return np.concatenate([rgb, alpha], axis=2)


def bake_photo_tile(rgb: np.ndarray, size: int, mode: str) -> np.ndarray:
    """Resize an RGB photo into one atlas tile.

    ``greyscale_modulation`` keeps cream/ochre on ``_BaseColor``.
    ``color_albedo`` keeps the photograph and expects a near-white base colour.
    """
    if rgb.ndim != 3 or rgb.shape[2] < 3:
        raise ValueError("photo must be HxWx3")
    from PIL import Image

    src = np.ascontiguousarray(rgb[..., :3], dtype=np.uint8)
    image = Image.fromarray(src).resize((size, size), Image.Resampling.LANCZOS)
    arr = np.asarray(image, dtype=np.uint8)
    if mode == "greyscale_modulation":
        luma = 0.2126 * arr[..., 0] + 0.7152 * arr[..., 1] + 0.0722 * arr[..., 2]
        grey = np.clip(np.round(luma), MIN_MEAN_LUMA, 255).astype(np.uint8)
        arr = np.stack([grey, grey, grey], axis=-1)
    alpha = np.full((size, size, 1), 255, dtype=np.uint8)
    return np.concatenate([arr, alpha], axis=2)


def decode_jpeg_rgb(data: bytes) -> np.ndarray:
    """Decode a JPEG (or other Pillow-readable still) to HxWx3 uint8."""
    from PIL import Image

    image = Image.open(BytesIO(data)).convert("RGB")
    return np.asarray(image, dtype=np.uint8)


def _unused_tile(size: int) -> np.ndarray:
    rgb = np.full((size, size, 3), 200, dtype=np.uint8)
    alpha = np.full((size, size, 1), 255, dtype=np.uint8)
    return np.concatenate([rgb, alpha], axis=2)


def build_atlas(
    catalog: MaterialCatalog,
    materials: Mapping[str, Material],
    *,
    used_color_ids: Sequence[str] | None = None,
    seed: int = 48152,
    photo_tiles: Mapping[str, tuple[np.ndarray, str]] | None = None,
) -> Atlas:
    """One tile per used material id. The grid grows so trees and metal are never dropped.

    ``photo_tiles`` maps a slot key (generation colour id) to ``(HxWx3 RGB, albedo_mode)``.
    """
    _ = materials  # colours stay on the glTF / Unity ``_BaseColor`` unless apply sets white.
    photos = dict(photo_tiles or {})
    wanted = list(dict.fromkeys(used_color_ids or [e.color_id for e in catalog.entries]))
    tiles, size = _grid(len(wanted))
    tile = size // tiles
    image = np.zeros((size, size, 4), dtype=np.uint8)
    image[..., :3] = 200
    image[..., 3] = 255
    slots: dict[str, tuple[float, float, float, float]] = {}
    photo_ids: list[str] = []
    for index, color_id in enumerate(wanted):
        row, col = divmod(index, tiles)
        y0, x0 = row * tile, col * tile
        if color_id in photos:
            rgb, mode = photos[color_id]
            image[y0 : y0 + tile, x0 : x0 + tile] = bake_photo_tile(rgb, tile, mode)
            photo_ids.append(color_id)
        else:
            image[y0 : y0 + tile, x0 : x0 + tile] = _tile_pixels(color_id, tile, seed + index)
        u0, v0 = col / tiles, 1.0 - (row + 1) / tiles
        u1, v1 = (col + 1) / tiles, 1.0 - row / tiles
        slots[color_id] = (u0, v0, u1, v1)
    filled = len(wanted)
    for index in range(filled, tiles * tiles):
        row, col = divmod(index, tiles)
        y0, x0 = row * tile, col * tile
        image[y0 : y0 + tile, x0 : x0 + tile] = _unused_tile(tile)
    pending = [e.id for e in catalog.entries if e.textures["kind"] == "pending"]
    note = (
        f"Atlas {size}², {len(slots)} tiles on a {tiles}×{tiles} grid "
        f"({len(photo_ids)} photographic, rest greyscale procedural). "
        "Not a photograph of Murcia. "
        + (f"Photo slots: {', '.join(photo_ids)}. " if photo_ids else "")
        + (f"Pending photographic textures: {', '.join(pending)}." if pending else "")
    )
    return Atlas(encode_opaque_png(image), size, tiles, slots, note)


def tile_luminance(pixels: np.ndarray) -> float:
    """Mean Rec. 709 luma of an RGB or RGBA tile."""
    rgb = pixels[..., :3].astype(np.float64)
    return float((0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]).mean())
