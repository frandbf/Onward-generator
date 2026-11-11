"""OBJ + MTL (+ albedo PNG) to an opaque, albedo-only GLB, as the library reads it.

The Unity exporter (``tools/unity/asset_store_export``) writes right-handed, Y-up OBJ in metres, with ``vt`` and one
``map_Kd`` PNG per textured material (already tinted by the material colour and at most 512 px), or a ``Kd`` colour
for an untextured one. A textured primitive keeps its UVs (OBJ v up becomes glTF v down) and its PNG; an untextured
one gets a 4 px PNG of its colour, as the Kenney and Quaternius imports do, because the library reads albedo images.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from onward_generator.generation.gltf import pack_glb

FLAT_ALBEDO_PX = 4
DEFAULT_KD = (0.8, 0.8, 0.8)


class ObjError(ValueError):
    """An OBJ, MTL or texture that cannot be converted."""


@dataclass(frozen=True, slots=True)
class MtlMaterial:
    kd: tuple[float, float, float]
    texture: str | None = None
    scale: tuple[float, float] = (1.0, 1.0)
    offset: tuple[float, float] = (0.0, 0.0)


@dataclass(frozen=True, slots=True)
class ObjGroup:
    """Triangles of one material, expanded per corner."""

    positions: np.ndarray
    normals: np.ndarray
    uvs: np.ndarray


def srgb(linear: float) -> float:
    """Linear to sRGB, clamped to [0, 1]."""
    value = min(max(float(linear), 0.0), 1.0)
    return 12.92 * value if value <= 0.0031308 else 1.055 * value ** (1 / 2.4) - 0.055


def parse_mtl(text: str) -> dict[str, MtlMaterial]:
    """``Kd`` (linear) and ``map_Kd`` (with its ``-s`` and ``-o`` options) of each material."""
    found: dict[str, dict[str, Any]] = {}
    name: str | None = None
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "newmtl":
            name = " ".join(parts[1:])
            found[name] = {"kd": DEFAULT_KD}
        elif name is None:
            continue
        elif parts[0] == "Kd" and len(parts) >= 4:
            found[name]["kd"] = (float(parts[1]), float(parts[2]), float(parts[3]))
        elif parts[0] == "map_Kd" and len(parts) >= 2:
            found[name].update(_map_options(parts[1:]))
    return {key: MtlMaterial(**value) for key, value in found.items()}


def _map_options(tokens: list[str]) -> dict[str, Any]:
    options: dict[str, Any] = {}
    i = 0
    while i < len(tokens) - 1 and tokens[i] in ("-s", "-o"):
        if i + 2 >= len(tokens):
            raise ObjError(f"map option {tokens[i]} needs two values")
        key = "scale" if tokens[i] == "-s" else "offset"
        options[key] = (float(tokens[i + 1]), float(tokens[i + 2]))
        i += 4  # u v w
    options["texture"] = " ".join(tokens[i:])
    return options


def parse_obj(text: str) -> dict[str, ObjGroup]:
    """Corner-expanded triangles per ``usemtl``; polygons are fanned, a missing normal is the face normal."""
    positions: list[list[float]] = []
    normals: list[list[float]] = []
    uvs: list[list[float]] = []
    corners: dict[str, list[tuple[list[float], list[float], list[float]]]] = {}
    material = "default"
    for number, line in enumerate(text.splitlines(), start=1):
        parts = line.split()
        if not parts:
            continue
        head = parts[0]
        if head == "v":
            positions.append([float(x) for x in parts[1:4]])
        elif head == "vn":
            normals.append([float(x) for x in parts[1:4]])
        elif head == "vt":
            uvs.append([float(x) for x in parts[1:3]])
        elif head == "usemtl":
            material = " ".join(parts[1:])
        elif head == "f":
            if len(parts) < 4:
                raise ObjError(f"line {number}: a face needs three corners")
            refs = [_corner(token, len(positions), len(uvs), len(normals)) for token in parts[1:]]
            for k in range(1, len(refs) - 1):
                tri = (refs[0], refs[k], refs[k + 1])
                p = [positions[r[0]] for r in tri]
                if all(r[2] is not None for r in tri):
                    n = [normals[r[2]] for r in tri]  # type: ignore[index]
                else:
                    face = np.cross(np.subtract(p[1], p[0]), np.subtract(p[2], p[0]))  # pragma: no mutate: cross(a, c - b) equals cross(a, c - a) when a = b - a
                    length = float(np.linalg.norm(face))
                    n = [list(face / length) if length else [0.0, 1.0, 0.0]] * 3
                t = [uvs[r[1]] if r[1] is not None else [0.5, 0.5] for r in tri]
                bucket = corners.setdefault(material, [])
                bucket.extend(zip(p, n, t, strict=True))  # pragma: no mutate: p, n and t always hold the three corners
    return {
        name: ObjGroup(np.array([c[0] for c in items], dtype=np.float32), np.array([c[1] for c in items], dtype=np.float32),
                       np.array([c[2] for c in items], dtype=np.float32))
        for name, items in corners.items()
    }


def _corner(token: str, nv: int, nt: int, nn: int) -> tuple[int, int | None, int | None]:
    fields = token.split("/")

    def index(value: str, count: int) -> int | None:
        if not value:
            return None
        i = int(value)
        resolved = i - 1 if i > 0 else count + i  # pragma: no mutate: index 0 is out of range on either branch, with the same message
        if not 0 <= resolved < count:
            raise ObjError(f"face index {value} is out of range")
        return resolved

    v = index(fields[0], nv)
    if v is None:
        raise ObjError(f"face corner {token!r} has no vertex")
    return v, index(fields[1], nt) if len(fields) > 1 else None, index(fields[2], nn) if len(fields) > 2 else None


MAX_TEXTURE_PX = 512


def _png(data: bytes) -> bytes:
    """The albedo as an RGB PNG, at most ``MAX_TEXTURE_PX`` on its long side."""
    picture = Image.open(io.BytesIO(data)).convert("RGB")
    if max(picture.size) > MAX_TEXTURE_PX:  # pragma: no mutate: thumbnail leaves an image already at the limit unchanged
        picture.thumbnail((MAX_TEXTURE_PX, MAX_TEXTURE_PX), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    picture.save(out, format="PNG", optimize=True)
    return out.getvalue()


def _flat_png(kd: tuple[float, float, float]) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (FLAT_ALBEDO_PX, FLAT_ALBEDO_PX), tuple(round(255 * srgb(c)) for c in kd)).save(out, format="PNG")
    return out.getvalue()


def obj_to_glb(obj_text: str, mtl_text: str, name: str, texture_dir: Path | None = None, factor: float = 1.0) -> bytes:
    """One mesh, one primitive per material, every material opaque with a base colour image; positions times ``factor``."""
    materials = parse_mtl(mtl_text)
    groups = {key: ObjGroup(g.positions * np.float32(factor), g.normals, g.uvs) for key, g in parse_obj(obj_text).items()}
    if not groups:
        raise ObjError(f"{name}: no faces")
    blob = bytearray()
    doc: dict[str, Any] = {"asset": {"version": "2.0", "generator": "onward_generator.assetstore"}, "buffers": [], "bufferViews": [],
                           "accessors": [], "materials": [], "images": [], "textures": [], "meshes": [], "nodes": [], "scenes": [], "scene": 0}

    def view(data: bytes) -> int:
        blob.extend(b"\x00" * (-len(blob) % 4))
        doc["bufferViews"].append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(data)})
        blob.extend(data)
        return len(doc["bufferViews"]) - 1

    def accessor(array: np.ndarray, kind: str, bounds: bool = False) -> int:
        extra = {"min": array.min(axis=0).tolist(), "max": array.max(axis=0).tolist()} if bounds else {}
        component = 5125 if array.dtype == np.uint32 else 5126
        doc["accessors"].append({"bufferView": view(array.tobytes()), "componentType": component, "count": len(array), "type": kind, **extra})
        return len(doc["accessors"]) - 1

    primitives = []
    for material_name, group in groups.items():
        material = materials.get(material_name, MtlMaterial(DEFAULT_KD))
        if material.texture is not None:
            if texture_dir is None or not (texture_dir / material.texture).is_file():
                raise ObjError(f"{name}: missing texture {material.texture}")
            image = _png((texture_dir / material.texture).read_bytes())
            uv = group.uvs * np.array(material.scale, dtype=np.float32) + np.array(material.offset, dtype=np.float32)
            uv = np.column_stack([uv[:, 0], 1.0 - uv[:, 1]]).astype(np.float32)
        else:
            image = _flat_png(material.kd)
            uv = np.full((len(group.positions), 2), 0.5, dtype=np.float32)
        doc["images"].append({"mimeType": "image/png", "bufferView": view(image)})
        doc["textures"].append({"source": len(doc["images"]) - 1})
        doc["materials"].append({"name": re.sub(r"[^A-Za-z0-9_]+", "_", material_name), "alphaMode": "OPAQUE", "doubleSided": False,
                                 "pbrMetallicRoughness": {"baseColorTexture": {"index": len(doc["textures"]) - 1},
                                                          "metallicFactor": 0.0, "roughnessFactor": 1.0}})
        primitives.append({"attributes": {"POSITION": accessor(group.positions, "VEC3", bounds=True),
                                          "NORMAL": accessor(group.normals, "VEC3"), "TEXCOORD_0": accessor(uv, "VEC2")},
                           "indices": accessor(np.arange(len(group.positions), dtype=np.uint32), "SCALAR"),
                           "material": len(doc["materials"]) - 1, "mode": 4})
    doc["meshes"].append({"name": name, "primitives": primitives})
    doc["nodes"].append({"name": name, "mesh": 0})
    doc["scenes"].append({"nodes": [0]})
    doc["buffers"].append({"byteLength": len(blob)})
    return pack_glb(doc, bytes(blob))
