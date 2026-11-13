"""Whether a library model is drawn in flat colours: every face samples a dot of a palette, not an image (ADR-036 amendment).

Many low-poly packs colour a model by pointing each face's UVs at one swatch of a small palette. Such a model has
no grain, no seam and no print; beside the photographic walls and floors of a map it reads as untextured (a pale
bookcase and a white fridge in a Rue Mouffetard shop, 12 November 2026). The test reads the GLB's JSON, its
accessors and its images' sizes only, never their pixels, so a configuration load can afford it for every row.
"""

from __future__ import annotations

import io
import json
import struct
from functools import cache
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image

from onward_generator.generation.mesh import Mesh

if TYPE_CHECKING:
    from onward_generator.overgrow.library import Prop

# A face whose UVs span less than this many texels of its image samples one swatch.
SWATCH_TEXELS = 2.0
# An image this small or smaller is one colour per material (the catalogue converters bake a material's colour into a
# 4 x 4 image), so every face that samples it is a swatch, whatever its UVs span.
SOLID_IMAGE_PX = 8
# A model is flat when at least this share of its surface is such faces (or has no texture at all).
FLAT_SHARE = 0.9
_COMPONENTS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3}
_DTYPES = {5121: np.uint8, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}


def flat_share(positions: np.ndarray, triangles: np.ndarray, uvs: np.ndarray | None, image_px: tuple[int, int] | None) -> tuple[float, float]:
    """Surface area of the faces that sample one swatch (all of them without UVs, without an image, or on an image of
    at most ``SOLID_IMAGE_PX``), and the whole area."""
    area = Mesh(positions, triangles).triangle_areas()
    total = float(area.sum())
    if uvs is None or image_px is None or max(image_px) <= SOLID_IMAGE_PX:
        return total, total
    spans = uvs[triangles]
    texels = (spans.max(axis=1) - spans.min(axis=1)) * np.array(image_px)
    swatch = texels.max(axis=1) < SWATCH_TEXELS
    return float(area[swatch].sum()), total


def is_flat(swatch_m2: float, total_m2: float) -> bool:
    """Whether a model whose ``swatch_m2`` of ``total_m2`` sample one swatch is drawn in flat colours."""
    return total_m2 > 0.0 and swatch_m2 >= FLAT_SHARE * total_m2


@cache
def _flat_file(path: str, size: int, mtime_ns: int) -> bool:
    data = Path(path).read_bytes()
    length = struct.unpack_from("<I", data, 12)[0]
    doc = json.loads(data[20:20 + length])
    start = 20 + length + 8
    binary = data[start:start + struct.unpack_from("<I", data, 20 + length)[0]] if len(data) > 20 + length else b""

    def accessor(index: int) -> np.ndarray:
        acc = doc["accessors"][index]
        view = doc["bufferViews"][acc["bufferView"]]
        count, width = acc["count"], _COMPONENTS[acc["type"]]
        offset = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
        return np.frombuffer(binary, dtype=_DTYPES[acc["componentType"]], count=count * width, offset=offset).reshape(count, width)

    sizes: dict[int, tuple[int, int]] = {}
    swatch = total = 0.0
    for mesh in doc.get("meshes", []):
        for prim in mesh["primitives"]:
            positions = accessor(prim["attributes"]["POSITION"]).astype(np.float64)
            triangles = (accessor(prim["indices"]).reshape(-1, 3) if "indices" in prim
                         else np.arange(len(positions)).reshape(-1, 3)).astype(np.int64)
            material = doc["materials"][prim["material"]] if "material" in prim else {}
            texture = material.get("pbrMetallicRoughness", {}).get("baseColorTexture")
            image_px = None
            uvs = None
            if texture is not None and "TEXCOORD_0" in prim["attributes"]:
                source = doc["textures"][texture["index"]]["source"]
                if source not in sizes:
                    sizes[source] = _image_size(doc["images"][source], binary, doc, Path(path).parent)
                image_px = sizes[source]
                uvs = accessor(prim["attributes"]["TEXCOORD_0"]).astype(np.float64)
            part, whole = flat_share(positions, triangles, uvs, image_px)
            swatch += part
            total += whole
    return is_flat(swatch, total)


def _image_size(image: dict, binary: bytes, doc: dict, folder: Path) -> tuple[int, int]:
    if "bufferView" in image:
        view = doc["bufferViews"][image["bufferView"]]
        start = view.get("byteOffset", 0)
        source = io.BytesIO(binary[start:start + view["byteLength"]])
    else:
        source = io.BytesIO((folder / image["uri"]).read_bytes())
    with Image.open(source) as picture:  # the header only: the pixels are never decoded
        return picture.size


def _albedo_size(data: bytes) -> tuple[int, int] | None:
    try:
        with Image.open(io.BytesIO(data)) as picture:  # the header only
            return picture.size
    except (OSError, ValueError):
        return None


def prop_flat(prop: Prop) -> bool:
    """Whether a loaded library prop is drawn in flat colours: each albedo part read like ``flat_colour`` reads a GLB."""
    parts = prop._parts()
    bounds = list(pairwise([0, *(part.end for part in parts)]))
    pairs = zip(bounds, parts, strict=True)  # pragma: no mutate: the bounds come from the parts, so both sides always have one entry per part
    shares = [flat_share(prop.mesh.vertices, prop.mesh.triangles[lo:hi], prop.uvs, _albedo_size(part.albedo)) for (lo, hi), part in pairs]
    return is_flat(sum(swatch for swatch, _ in shares), sum(total for _, total in shares))


def flat_colour(path: Path) -> bool:
    """Whether the GLB at ``path`` is drawn in flat colours; a file that cannot be read is not."""
    try:
        stat = path.stat()
        return _flat_file(str(path), stat.st_size, stat.st_mtime_ns)
    except (OSError, ValueError, KeyError, IndexError, struct.error):
        return False


__all__ = ["FLAT_SHARE", "SOLID_IMAGE_PX", "SWATCH_TEXELS", "flat_colour", "flat_share", "is_flat", "prop_flat"]
