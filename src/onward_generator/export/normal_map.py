"""Plan D5 spike (ADR-033, proposed): a tangent-space normal map derived from one facade albedo.

Off unless the export card sets ``build.lit_normal_material``. The map is computed from the
albedo PNG already embedded in ``scene_visual.glb``, so no asset is added and nothing is
downloaded. The same albedo and strength always give the same pixels.

Convention: the luma of the albedo is read as a height field. A 3x3 Sobel filter, with the
border pixels repeated (never wrapped, so the atlas strips do not bleed into each other),
gives the slope across (x, columns left to right) and up (V, toward PNG row 0, the pipeline's
"V = 0 is the bottom row" rule). The normal is ``normalize(-s * dh/dx, -s * dh/dV, 1)``,
encoded as ``round((n * 0.5 + 0.5) * 255)``: the OpenGL / Unity green-up convention. A flat
area is exactly (128, 128, 255).

This module reads the visual GLB only; it imports nothing from the generation stages, so the
export adapter stays apart from the engine (ADR-007).
"""

from __future__ import annotations

from io import BytesIO

import numpy as np
from PIL import Image

from onward_generator.generation.gltf import read_glb

LIT_NORMAL_FILE = "lit_normal.png"
LIT_NORMAL_ROLE = "lit_normal"
DEFAULT_STRENGTH = 2.0
"""Relief strength when the card sets ``build.lit_normal_material`` without ``lit_normal_strength``."""
# Rec.709 luma weights, applied to the 8-bit sRGB values scaled to 0-1.
LUMA = (0.2126, 0.7152, 0.0722)
_PNG_COMPRESS_LEVEL = 6
_ASTC_BLOCK = 6
_ASTC_BLOCK_BYTES = 16
_TRIM_PREFIX = "facade_trim_"


class NormalMapError(ValueError):
    """The named material has no PNG albedo in the visual GLB to derive a normal map from."""


def albedo_png(visual: bytes, material: str) -> bytes:
    """The PNG bytes of ``material``'s base colour texture in the visual GLB.

    The path is material -> ``baseColorTexture.index`` -> ``textures[i].source`` -> image ->
    ``bufferView``. A GLB written without ``textures`` maps the index to the image directly,
    as the adapter does.
    """
    doc, blob = read_glb(visual)
    materials = [m for m in doc.get("materials") or [] if isinstance(m, dict)]
    found = next((m for m in materials if m.get("name") == material), None)
    if found is None:
        present = sorted(str(m.get("name")) for m in materials if str(m.get("name")).startswith(_TRIM_PREFIX))
        raise NormalMapError(f"material {material!r} is not in the visual GLB "
                             f"(facade trim materials present: {', '.join(present) or 'none'})")
    info = (found.get("pbrMetallicRoughness") or {}).get("baseColorTexture")
    if not isinstance(info, dict) or not isinstance(info.get("index"), int):
        raise NormalMapError(f"material {material!r} has no base colour texture")
    index = int(info["index"])
    textures = doc.get("textures")
    if isinstance(textures, list):
        if not 0 <= index < len(textures) or not isinstance(textures[index].get("source"), int):
            raise NormalMapError(f"material {material!r}: texture {index} has no image")
        index = int(textures[index]["source"])
    images = doc.get("images") or []
    if not 0 <= index < len(images):
        raise NormalMapError(f"material {material!r}: image {index} is not in the GLB")
    image = images[index]
    if image.get("mimeType") != "image/png":
        raise NormalMapError(f"material {material!r}: image {index} is {image.get('mimeType')!r}, not image/png")
    view_index = image.get("bufferView")
    if not isinstance(view_index, int):
        raise NormalMapError(f"material {material!r}: image {index} has no bufferView")
    view = doc["bufferViews"][view_index]
    start = int(view.get("byteOffset", 0))
    return bytes(blob[start : start + int(view["byteLength"])])


def decode_rgb(png: bytes) -> np.ndarray:
    """An 8-bit H x W x 3 array of the PNG's colour; alpha is dropped."""
    with Image.open(BytesIO(png)) as image:
        return np.asarray(image.convert("RGB"), dtype=np.uint8)


def normal_map(rgb: np.ndarray, strength: float) -> np.ndarray:
    """The H x W x 3 uint8 tangent-space normal map of an 8-bit H x W x 3 or H x W x 4 image (alpha ignored)."""
    if not strength > 0:
        raise ValueError(f"strength must be above 0, got {strength}")
    pixels = np.asarray(rgb)
    if pixels.ndim != 3 or pixels.shape[2] not in (3, 4):
        raise ValueError(f"expected an H x W x 3 or H x W x 4 image, got shape {pixels.shape}")
    height = pixels[:, :, :3].astype(np.float64) @ np.array(LUMA) / 255.0
    padded = np.pad(height, 1, mode="edge")

    def at(dy: int, dx: int) -> np.ndarray:
        """The height shifted by (dy, dx) rows and columns: ``at(0, 1)`` is the right neighbour."""
        return padded[1 + dy : padded.shape[0] - 1 + dy, 1 + dx : padded.shape[1] - 1 + dx]

    # Sobel over 8: the slope per pixel. Across: right minus left. Up (V): row above minus row below.
    dx = (at(-1, 1) + 2 * at(0, 1) + at(1, 1) - at(-1, -1) - 2 * at(0, -1) - at(1, -1)) / 8
    dv = (at(-1, -1) + 2 * at(-1, 0) + at(-1, 1) - at(1, -1) - 2 * at(1, 0) - at(1, 1)) / 8
    normals = np.stack([-strength * dx, -strength * dv, np.ones_like(height)], axis=-1)
    normals /= np.linalg.norm(normals, axis=-1, keepdims=True)
    return np.round((normals * 0.5 + 0.5) * 255.0).astype(np.uint8)


def encode_png(pixels: np.ndarray) -> bytes:
    """An RGB PNG with a fixed compression level and no metadata chunk: the same pixels give the same bytes."""
    array = np.ascontiguousarray(pixels, dtype=np.uint8)
    if array.ndim != 3 or array.shape[2] != 3:
        raise ValueError(f"expected an H x W x 3 normal map, got shape {array.shape}")
    out = BytesIO()
    Image.fromarray(array).save(out, format="PNG", compress_level=_PNG_COMPRESS_LEVEL)
    return out.getvalue()


def astc6x6_bytes(width: int, height: int) -> int:
    """Predicted GPU bytes of a ``width`` x ``height`` texture in ASTC 6x6 with its full mip chain."""
    if width < 1 or height < 1:
        raise ValueError(f"texture size must be at least 1 x 1, got {width} x {height}")
    total = 0
    while True:
        total += -(-width // _ASTC_BLOCK) * -(-height // _ASTC_BLOCK) * _ASTC_BLOCK_BYTES
        if width == height == 1:
            return total
        width, height = max(1, width // 2), max(1, height // 2)
