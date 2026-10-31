"""Bring installed Kenney GLBs from kit units up to street metres.

The packs are built on a module of about one file-unit. That unit is not a
metre: a Retro Urban door is 0.628 tall and a truck is 1.643 on its long
side. One factor per pack keeps a door, a window, a wall and a truck in the
same proportion. Debris is the exception. A pallet is already about a metre
in the file, close to a real pallet, so it does not take the pack factor.

The factor is applied once, to the stored positions, and recorded in the
GLB extras as ``street_scale``. Running it again does nothing. Removed
building meshes are not in ``assets/kenney`` and are left alone.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from onward_generator.generation.gltf import pack_glb, read_glb
from onward_generator.io import atomic_write_bytes, atomic_write_json

# Retro Urban Kit. 0.628 m door → 2.01 m. 1 m wall → 3.2 m storey.
# Truck long side 1.643 → 5.26 m, width 2.66 m, height 3.22 m.
URBAN_SCALE = 3.2
# Modular Buildings. 0.50 m door → 2.05 m.
MODULAR_SCALE = 4.1
# City Kit Suburban. 0.27 m fence → 1.89 m. 0.767 m tree → 5.37 m.
SUBURBAN_SCALE = 7.0
# Debris shorter than this on its long side (blocks, brick piles, the small
# pallet) grows by DEBRIS_SMALL_SCALE. A full pallet and the planks, already
# at least this long, grow only by DEBRIS_LARGE_SCALE.
DEBRIS_LONG_M = 0.9
DEBRIS_SMALL_SCALE = 2.6
DEBRIS_LARGE_SCALE = 1.25

_STREET_SCALE = "street_scale"
_VEC3_FLOAT = 12


def street_factor(pack: str, role: str, length_m: float, depth_m: float) -> float:
    """Uniform scale for one mesh, from the kit box before any street scale."""
    if pack == "suburban":
        return SUBURBAN_SCALE
    if pack == "modular":
        return MODULAR_SCALE
    if pack == "urban":
        if role == "debris":
            long_side = max(length_m, depth_m)
            return DEBRIS_LARGE_SCALE if long_side >= DEBRIS_LONG_M else DEBRIS_SMALL_SCALE
        return URBAN_SCALE
    raise ValueError(f"unknown Kenney pack {pack}")


def bounds_yz_up(data: bytes) -> tuple[float, float, float]:
    """Spans of every POSITION, Y up: length (X), depth (Z), height (Y)."""
    doc, blob = read_glb(data)
    _reject_node_transforms(doc)
    points: list[np.ndarray] = []
    seen: set[int] = set()
    for mesh in doc.get("meshes") or []:
        for prim in mesh.get("primitives") or []:
            index = (prim.get("attributes") or {}).get("POSITION")
            if index is None or int(index) in seen:
                continue
            seen.add(int(index))
            points.append(_read_vec3(doc, blob, int(index)))
    if not points:
        raise ValueError("GLB has no positions")
    cloud = np.concatenate(points, axis=0)
    span = cloud.max(axis=0) - cloud.min(axis=0)
    return float(span[0]), float(span[2]), float(span[1])


def scale_glb_positions(data: bytes, factor: float) -> bytes:
    """Scale POSITION accessors. UVs, normals, indices and texture URIs stay."""
    if factor <= 0.0:
        raise ValueError("street scale must be positive")
    doc, blob = read_glb(data)
    _reject_node_transforms(doc)
    raw = bytearray(blob)
    seen: set[int] = set()
    for mesh in doc.get("meshes") or []:
        for prim in mesh.get("primitives") or []:
            index = (prim.get("attributes") or {}).get("POSITION")
            if index is None or int(index) in seen:
                continue
            seen.add(int(index))
            _scale_vec3(doc, raw, int(index), factor)
    if not seen:
        raise ValueError("GLB has no positions")
    extras = dict(doc.get("extras") or {})
    extras[_STREET_SCALE] = factor
    doc["extras"] = extras
    return pack_glb(doc, bytes(raw))


def normalize_installed(repo: Path) -> int:
    """Scale every installed Kenney GLB once and record the new box.

    Updates ``assets/kenney/manifest.json``, ``assets/kenney_review.json`` and
    the sha256 of each GLB in ``config/dress/imported_library.json``.
    """
    root = repo / "assets" / "kenney"
    manifest_path = root / "manifest.json"
    review_path = repo / "assets" / "kenney_review.json"
    library_path = repo / "config" / "dress" / "imported_library.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    review = json.loads(review_path.read_text(encoding="utf-8"))
    library = json.loads(library_path.read_text(encoding="utf-8"))
    review_by_id = {str(row["id"]): row for row in review["assets"]}
    library_by_id = {str(row["id"]): row for row in library.get("objects") or []}
    scaled = 0
    for row in manifest["assets"]:
        pack = str(row["file"]).split("/", 1)[0]
        path = root / str(row["file"])
        data = path.read_bytes()
        doc, _blob = read_glb(data)
        if _STREET_SCALE not in (doc.get("extras") or {}):
            factor = street_factor(pack, str(row["role"]), float(row["length_m"]), float(row["depth_m"]))
            data = scale_glb_positions(data, factor)
            atomic_write_bytes(path, data)
            scaled += 1
        length_m, depth_m, height_m = bounds_yz_up(data)
        row["length_m"] = _round_m(length_m)
        row["depth_m"] = _round_m(depth_m)
        row["height_m"] = _round_m(height_m)
        card = review_by_id[str(row["id"])]
        card["length_m"] = row["length_m"]
        card["depth_m"] = row["depth_m"]
        card["height_m"] = row["height_m"]
        card[_STREET_SCALE] = (read_glb(data)[0].get("extras") or {})[_STREET_SCALE]
        library_row = library_by_id[str(row["id"])]
        library_row["sha256"] = hashlib.sha256(data).hexdigest()
        library_row["bytes"] = len(data)
    review["note"] = _NOTE
    atomic_write_json(manifest_path, manifest)
    atomic_write_json(review_path, review)
    atomic_write_json(library_path, library)
    return scaled


_NOTE = (
    "Kenney CC0 packs copied from the local downloads. Sizes are the GLB bounding box "
    "in metres, Y up, as stored. Building-role meshes were removed on 2026-10-26. "
    "On 2026-10-27 the installed GLBs were scaled once so that box is a street size. "
    "Retro Urban Kit uses 3.2, except debris: 2.6 when the long side was under 0.9 m, "
    "otherwise 1.25. Modular Buildings uses 4.1. City Kit Suburban uses 7.0. "
    "street_scale on an installed card is that factor. Removed meshes were not scaled. "
    "The meshes that remain are in assets/kenney. overgrow reads this catalogue together "
    "with every other assets/*/manifest.json. Each GLB references Textures/<name>.png "
    "in its pack folder. Kenney publishes a pack page, so url and api_url both point at that page."
)


def _round_m(value: float) -> float:
    return round(float(value), 3)


def _reject_node_transforms(doc: dict) -> None:
    for node in doc.get("nodes") or []:
        present = [key for key in ("matrix", "translation", "rotation", "scale") if key in node]
        if present:
            raise ValueError(f"node {node.get('name')}: transform {present} is not scaled")


def _read_vec3(doc: dict, blob: bytes, index: int) -> np.ndarray:
    offset, count = _vec3_span(doc, index)
    raw = blob[offset : offset + count * _VEC3_FLOAT]
    if len(raw) != count * _VEC3_FLOAT:
        raise ValueError(f"accessor {index}: buffer is short")
    return np.frombuffer(raw, dtype="<f4").reshape(count, 3).astype(np.float64)


def _scale_vec3(doc: dict, blob: bytearray, index: int, factor: float) -> None:
    offset, count = _vec3_span(doc, index)
    values = np.frombuffer(blob[offset : offset + count * _VEC3_FLOAT], dtype="<f4").reshape(count, 3).copy()
    values *= np.float32(factor)
    blob[offset : offset + count * _VEC3_FLOAT] = np.ascontiguousarray(values, dtype="<f4").tobytes()
    acc = doc["accessors"][index]
    acc["min"] = [_clean(v) for v in values.min(axis=0)]
    acc["max"] = [_clean(v) for v in values.max(axis=0)]


def _vec3_span(doc: dict, index: int) -> tuple[int, int]:
    acc = doc["accessors"][index]
    if acc.get("type") != "VEC3" or int(acc.get("componentType")) != 5126:
        raise ValueError(f"accessor {index}: POSITION must be float VEC3")
    view = doc["bufferViews"][acc["bufferView"]]
    stride = view.get("byteStride")
    if stride not in (None, _VEC3_FLOAT):
        raise ValueError(f"accessor {index}: interleaved stride {stride} is not packed")
    offset = int(view.get("byteOffset", 0)) + int(acc.get("byteOffset", 0))
    return offset, int(acc["count"])


def _clean(value: float) -> float:
    number = float(value)
    if abs(number) < 1e-7:
        return 0.0
    return number


