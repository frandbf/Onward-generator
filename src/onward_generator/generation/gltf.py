"""Minimal deterministic glTF 2.0 binary (GLB) writer.

GLB is an intermediate preview/transport format here (docs/06-generation.md); it is not
assumed to be directly loadable by Onward. Local axes (x east, y north, z up)
map to glTF axes (+X east, +Y up, +Z south): (x, y, z) → (x, z, −y), a proper
rotation, so triangle winding is preserved.
"""

from __future__ import annotations

import json
import struct
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from onward_generator import __version__
from onward_generator.generation.config import Material
from onward_generator.generation.mesh import Mesh

_TO_GLTF = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, -1.0, 0.0]])
_ARRAY_BUFFER = 34962
_ELEMENT_ARRAY_BUFFER = 34963
_FLOAT = 5126
_UINT = 5125


@dataclass(frozen=True, slots=True)
class Primitive:
    mesh: Mesh
    material: str
    uvs: np.ndarray | None = None


@dataclass(frozen=True, slots=True)
class Node:
    """A glTF node. ``matrix`` (4×4, local frame) places the primitives; nodes sharing ``mesh_key`` reuse one glTF mesh."""

    name: str
    primitives: tuple[Primitive, ...]
    extras: Mapping[str, Any] = field(default_factory=dict)
    matrix: np.ndarray | None = None
    mesh_key: str | None = None

    def world_primitives(self) -> tuple[Primitive, ...]:
        if self.matrix is None:
            return self.primitives
        from onward_generator.generation.mesh import transform

        return tuple(Primitive(transform(p.mesh, self.matrix), p.material) for p in self.primitives)


def gltf_matrix(matrix: np.ndarray) -> list[float]:
    """Local 4×4 → glTF column-major list, conjugated by the axis map."""
    a = np.eye(4)
    a[:3, :3] = _TO_GLTF
    return [float(v) for v in (a @ matrix @ a.T).T.reshape(-1)]


def vertex_normals(mesh: Mesh) -> np.ndarray:
    """Area-weighted vertex normals (exact for the unwelded planar groups this package builds)."""
    normals = np.zeros_like(mesh.vertices)
    face = mesh.face_normals()
    for k in range(3):
        np.add.at(normals, mesh.triangles[:, k], face)
    length = np.linalg.norm(normals, axis=1, keepdims=True)
    length[length == 0] = 1.0
    return normals / length


class _Builder:
    def __init__(self) -> None:
        self.blob = bytearray()
        self.views: list[dict[str, Any]] = []
        self.accessors: list[dict[str, Any]] = []

    def add(self, data: np.ndarray, *, target: int, component: int, kind: str, minmax: bool = False) -> int:
        while len(self.blob) % 4:
            self.blob.append(0)
        raw = data.tobytes()
        self.views.append({"buffer": 0, "byteOffset": len(self.blob), "byteLength": len(raw), "target": target})
        self.blob.extend(raw)
        accessor: dict[str, Any] = {
            "bufferView": len(self.views) - 1,
            "componentType": component,
            "count": int(data.shape[0]) if kind != "SCALAR" else int(data.size),
            "type": kind,
        }
        if minmax:
            accessor["min"] = [float(v) for v in data.min(axis=0)]
            accessor["max"] = [float(v) for v in data.max(axis=0)]
        self.accessors.append(accessor)
        return len(self.accessors) - 1


# A face this close to level is projected on world X/Y. Steeper faces use the face plane.
FLAT_NZ = 0.75
# Roofs follow the slope down to almost flat. Only roofs: ``cap`` shares vertices
# across a whole ground or slab surface, which must keep one planar projection.
ROOF_FLAT_NZ = 0.98


def surface_uvs(
    mesh: Mesh, tile_m: float, *, fit_unit: bool = False, roof: bool = False, frame: Mapping[str, Any] | None = None,
) -> np.ndarray:
    """UVs on the face plane, in repeats of ``tile_m`` metres.

    Vertical faces use world up as V, so a tall wall repeats instead of stretching
    one tile across the whole height. ``fit_unit`` maps each disconnected quad to
    0–1 (a window or a door). ``roof`` keeps slope-aligned UVs on faces up to
    ``ROOF_FLAT_NZ``, so a pitched roof is measured along the slope, not top-down.
    ``frame`` is a building's ``uv_frame`` (ADR-012): faces on an exposed run follow
    the run and the storeys instead.
    """
    uvs = _surface_uvs(mesh, tile_m, fit_unit=fit_unit, roof=roof)
    if frame is not None:
        _facade_uvs(mesh, tile_m, frame, uvs)
    return uvs.astype(np.float32)


def _surface_uvs(mesh: Mesh, tile_m: float, *, fit_unit: bool, roof: bool) -> np.ndarray:
    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    triangles = np.asarray(mesh.triangles, dtype=np.int64)
    uvs = np.zeros((len(vertices), 2), dtype=np.float64)
    if len(vertices) == 0 or len(triangles) == 0:
        return uvs
    normals = mesh.face_normals()
    length = np.linalg.norm(normals, axis=1, keepdims=True)
    normals = normals / np.maximum(length, 1e-12)
    horizontal = np.abs(normals[:, 2]) >= (ROOF_FLAT_NZ if roof else FLAT_NZ)
    tangent = np.cross(np.array([0.0, 0.0, 1.0]), normals)
    tangent_length = np.linalg.norm(tangent, axis=1, keepdims=True)
    fallback = horizontal | (tangent_length[:, 0] < 1e-8)
    tangent = tangent / np.maximum(tangent_length, 1e-12)
    bitangent = np.cross(normals, tangent)
    bitangent_length = np.linalg.norm(bitangent, axis=1, keepdims=True)
    bitangent = bitangent / np.maximum(bitangent_length, 1e-12)
    down = (~fallback) & (bitangent[:, 2] < 0.0)
    bitangent[down] *= -1.0
    tangent[fallback] = (1.0, 0.0, 0.0)
    bitangent[fallback] = (0.0, 1.0, 0.0)
    scale = max(float(tile_m), 1e-6)
    corners = vertices[triangles]
    u = np.einsum("tij,tj->ti", corners, tangent) / scale
    v = np.einsum("tij,tj->ti", corners, bitangent) / scale
    for corner in range(3):
        uvs[triangles[:, corner], 0] = u[:, corner]
        uvs[triangles[:, corner], 1] = v[:, corner]
    if fit_unit:
        for group in _triangle_groups(triangles).values():
            index = np.unique(triangles[np.array(group)].reshape(-1))
            span_u = float(uvs[index, 0].max() - uvs[index, 0].min())
            span_v = float(uvs[index, 1].max() - uvs[index, 1].min())
            uvs[index, 0] = (uvs[index, 0] - uvs[index, 0].min()) / max(span_u, 1e-6)
            uvs[index, 1] = (uvs[index, 1] - uvs[index, 1].min()) / max(span_v, 1e-6)
    return uvs


# A wall triangle belongs to a run segment when it lies this close to the segment's plane and span.
FRAME_TOL_M = 1e-3


def _snapped(length: float, tile_m: float) -> float:
    """``tile_m`` stretched so ``length`` holds a whole number of repeats (at least one)."""
    return length / max(1, round(length / tile_m))


def _facade_uvs(mesh: Mesh, tile_m: float, frame: Mapping[str, Any], uvs: np.ndarray) -> np.ndarray:
    """Overwrite the UVs of faces on an exposed run. Return each vertex's run, or −1.

    U is the distance along the run, in a whole number of repeats per run. V is the
    height, in a whole number of repeats per storey when ``storey_h`` is set.
    """
    vertices = np.asarray(mesh.vertices, dtype=np.float64)  # pragma: no mutate: Mesh vertices are float64 by contract, so the dtype changes nothing
    triangles = np.asarray(mesh.triangles, dtype=np.int64)  # pragma: no mutate: triangles are only used as indices, and any integer dtype indexes the same vertices
    run_of = np.full(len(vertices), -1, dtype=np.int64)  # pragma: no mutate: numpy infers int64 from -1, and callers only test run_of >= 0, so any negative fill reads as no run
    if len(triangles) == 0:
        return run_of
    scale = max(float(tile_m), 1e-6)
    storey_h = frame.get("storey_h")
    tile_v = _snapped(float(storey_h), scale) if storey_h else scale
    normals = mesh.face_normals()
    normals = normals / np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12)
    corners = vertices[triangles]
    free = np.ones(len(triangles), dtype=bool)
    for index, run in enumerate(frame.get("runs") or []):
        segments = np.asarray(run, dtype=np.float64).reshape(-1, 4)  # pragma: no mutate: hypot and every later step promote integer coordinates to the same float64 values, and numpy reads any negative size as the unknown one
        lengths = np.hypot(segments[:, 2] - segments[:, 0], segments[:, 3] - segments[:, 1])
        # A run of zero length skips every segment below, so it needs no check of its own.
        tile_u = _snapped(float(lengths.sum()), scale)
        s0 = 0.0
        for (ax, ay, bx, by), length in zip(segments, lengths, strict=True):  # pragma: no mutate: lengths is computed row by row from segments, so both always have the same length
            if length > 0.0:
                along = np.array([bx - ax, by - ay, 0.0]) / length
                out = np.array([along[1], -along[0], 0.0])
                rel = corners - np.array([ax, ay, 0.0])  # pragma: no mutate: along and out have zero z, so this z never reaches t or the plane distance
                t = rel @ along
                hit = (
                    free
                    & (normals @ out > 0.999)
                    & (np.abs(rel @ out) <= FRAME_TOL_M).all(axis=1)
                    & (t >= -FRAME_TOL_M).all(axis=1)
                    & (t <= length + FRAME_TOL_M).all(axis=1)
                )
                for corner in range(3):
                    vertex = triangles[hit, corner]
                    uvs[vertex, 0] = (s0 + t[hit, corner]) / tile_u
                    uvs[vertex, 1] = corners[hit, corner, 2] / tile_v
                    run_of[vertex] = index
                free &= ~hit
            s0 += float(length)
    return run_of


def uv_islands(triangles: np.ndarray, count: int) -> np.ndarray:
    """One label per vertex. Vertices joined through triangles share a label."""
    label = np.arange(count, dtype=np.int64)
    tris = np.asarray(triangles, dtype=np.int64).reshape(-1, 3)
    left = np.concatenate([tris[:, 0], tris[:, 1]])
    right = np.concatenate([tris[:, 1], tris[:, 2]])
    while True:
        while True:
            jumped = label[label]
            if np.array_equal(jumped, label):
                break
            label = jumped
        a, b = label[left], label[right]
        differ = a != b
        if not differ.any():
            return label
        # Hook the larger root under the smaller one; labels only decrease, so no cycle forms.
        np.minimum.at(label, np.maximum(a[differ], b[differ]), np.minimum(a[differ], b[differ]))


def recentre_uvs(uvs: np.ndarray, triangles: np.ndarray, runs: np.ndarray | None = None) -> np.ndarray:
    """Subtract ``floor(min)`` per UV island so repeat UVs stay small.

    World projections reach hundreds of repeats far from the origin, where a
    half-precision shader loses the fraction. An integer shift is invisible with
    Repeat wrapping, and each island moves as one, so no seam opens. A vertex with
    ``runs ≥ 0`` belongs to that run's island instead, so a facade run stays continuous.
    """
    out = np.array(uvs, dtype=np.float64, copy=True)
    if len(out) == 0:
        return out
    labels = uv_islands(triangles, len(out))
    if runs is not None:
        labels = np.where(runs >= 0, len(out) + runs, labels)
    _, island = np.unique(labels, return_inverse=True)
    low = np.full((int(island.max()) + 1, 2), np.inf)
    np.minimum.at(low, island, out)
    out -= np.floor(low)[island]
    return out


def _triangle_groups(triangles: np.ndarray) -> dict[int, list[int]]:
    """Triangles that share a vertex belong to one opening."""
    parent = np.arange(len(triangles))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = int(parent[index])
        return index

    seen: dict[int, int] = {}
    for tri_index, tri in enumerate(triangles):
        for vertex in tri:
            vertex = int(vertex)
            other = seen.get(vertex)
            if other is None:
                seen[vertex] = tri_index
                continue
            left, right = find(other), find(tri_index)
            if left != right:
                parent[right] = left
    groups: dict[int, list[int]] = {}
    for tri_index in range(len(triangles)):
        groups.setdefault(find(tri_index), []).append(tri_index)
    return groups


def build_glb(
    nodes: Sequence[Node],
    materials: Mapping[str, Material],
    *,
    extras: Mapping[str, Any] | None = None,
    atlas_png: bytes | None = None,
    repeat_images: Mapping[str, bytes] | None = None,
    repeat_tile_m: Mapping[str, float] | None = None,
    unit_uv_materials: Collection[str] | None = None,
    roof_uv_materials: Collection[str] | None = None,
    preserve_uvs: bool = False,
) -> bytes:
    builder = _Builder()
    material_names = sorted({p.material for n in nodes for p in n.primitives})
    material_index = {name: i for i, name in enumerate(material_names)}
    repeat = {name: png for name, png in (repeat_images or {}).items() if name in material_index}
    using_repeat = bool(repeat)
    textured = atlas_png is not None and not using_repeat
    unit_uv = frozenset(unit_uv_materials or ())
    roof_uv = frozenset(roof_uv_materials or ())
    imaged = [name for name in material_names if name in repeat]
    gltf_meshes: list[dict[str, Any]] = []
    gltf_nodes: list[dict[str, Any]] = []
    shared: dict[str, int] = {}
    for node in nodes:
        if node.mesh_key is not None and node.mesh_key in shared:
            entry = {"name": node.name, "mesh": shared[node.mesh_key]}
            if node.matrix is not None:
                entry["matrix"] = gltf_matrix(node.matrix)
            if node.extras:
                entry["extras"] = dict(node.extras)
            gltf_nodes.append(entry)
            continue
        prims = []
        for prim in node.primitives:
            if not prim.mesh.triangle_count:
                continue
            positions = (prim.mesh.vertices @ _TO_GLTF.T).astype(np.float32)
            normals = (vertex_normals(prim.mesh) @ _TO_GLTF.T).astype(np.float32)
            indices = prim.mesh.triangles.astype(np.uint32).reshape(-1)
            attributes = {
                "POSITION": builder.add(positions, target=_ARRAY_BUFFER, component=_FLOAT, kind="VEC3", minmax=True),
                "NORMAL": builder.add(normals, target=_ARRAY_BUFFER, component=_FLOAT, kind="VEC3"),
            }
            if preserve_uvs and prim.uvs is not None and len(prim.uvs) == len(prim.mesh.vertices):
                attributes["TEXCOORD_0"] = builder.add(np.asarray(prim.uvs, dtype=np.float32), target=_ARRAY_BUFFER, component=_FLOAT, kind="VEC2")
            elif using_repeat and prim.material in repeat:
                tile = float((repeat_tile_m or {}).get(prim.material, 2.0))
                unit = prim.material in unit_uv
                roof = prim.material in roof_uv
                uvs = _surface_uvs(prim.mesh, tile, fit_unit=unit, roof=roof)
                if not unit:
                    frame = (node.extras or {}).get("uv_frame") if node.matrix is None and not roof else None
                    runs = _facade_uvs(prim.mesh, tile, frame, uvs) if frame else np.full(len(uvs), -1)  # pragma: no mutate: runs is only tested >= 0, so any negative fill reads as no run
                    phase = (node.extras or {}).get("uv_phase")
                    if phase:
                        uvs[:, 0] += float(phase[0])
                        # Storey-aligned V keeps its ground line; only U takes the building phase.
                        storeys = (runs >= 0) if frame and frame.get("storey_h") else np.zeros(len(uvs), dtype=bool)
                        uvs[~storeys, 1] += float(phase[1])
                    uvs = recentre_uvs(uvs, prim.mesh.triangles, runs)
                attributes["TEXCOORD_0"] = builder.add(uvs.astype(np.float32), target=_ARRAY_BUFFER, component=_FLOAT, kind="VEC2")
            elif prim.uvs is not None and len(prim.uvs) == len(prim.mesh.vertices):
                attributes["TEXCOORD_0"] = builder.add(np.asarray(prim.uvs, dtype=np.float32), target=_ARRAY_BUFFER, component=_FLOAT, kind="VEC2")
            prims.append(
                {
                    "attributes": attributes,
                    "indices": builder.add(indices, target=_ELEMENT_ARRAY_BUFFER, component=_UINT, kind="SCALAR"),
                    "material": material_index[prim.material],
                    "mode": 4,
                }
            )
        if not prims:
            continue
        gltf_meshes.append({"name": node.mesh_key or node.name, "primitives": prims})
        if node.mesh_key is not None:
            shared[node.mesh_key] = len(gltf_meshes) - 1
        entry: dict[str, Any] = {"name": node.name, "mesh": len(gltf_meshes) - 1}
        if node.matrix is not None:
            entry["matrix"] = gltf_matrix(node.matrix)
        if node.extras:
            entry["extras"] = dict(node.extras)
        gltf_nodes.append(entry)

    doc: dict[str, Any] = {
        "asset": {"version": "2.0", "generator": f"onward-generator {__version__}"},
        "scene": 0,
        "scenes": [{"name": "scene", "nodes": list(range(len(gltf_nodes)))}],
        "nodes": gltf_nodes,
        "meshes": gltf_meshes,
        "materials": [
            {
                "name": name,
                "pbrMetallicRoughness": {
                    "baseColorFactor": list(materials[name].color),
                    "metallicFactor": 0.0,
                    "roughnessFactor": materials[name].roughness,
                    **(
                        {"baseColorTexture": {"index": imaged.index(name), "texCoord": 0}}
                        if using_repeat and name in repeat
                        else {"baseColorTexture": {"index": 0, "texCoord": 0}}
                        if textured
                        else {}
                    ),
                },
                "doubleSided": False,
            }
            for name in material_names
        ],
        "accessors": builder.accessors,
        "bufferViews": builder.views,
        "buffers": [{"byteLength": 0}],
    }
    if using_repeat or textured:
        images_out: list[dict[str, Any]] = []
        payloads = [repeat[name] for name in imaged] if using_repeat else [atlas_png or b""]
        for png in payloads:
            while len(builder.blob) % 4:
                builder.blob.append(0)
            view = {"buffer": 0, "byteOffset": len(builder.blob), "byteLength": len(png)}
            builder.blob.extend(png)
            builder.views.append(view)
            images_out.append({"mimeType": "image/png", "bufferView": len(builder.views) - 1})
        doc["images"] = images_out
        doc["samplers"] = [{"magFilter": 9729, "minFilter": 9729, "wrapS": 10497, "wrapT": 10497}]
        doc["textures"] = [{"sampler": 0, "source": index} for index in range(len(images_out))]
    doc["buffers"] = [{"byteLength": len(builder.blob)}]
    if extras:
        doc["extras"] = dict(extras)
    json_chunk = json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    json_chunk += b" " * (-len(json_chunk) % 4)
    bin_chunk = bytes(builder.blob) + b"\x00" * (-len(builder.blob) % 4)
    total = 12 + 8 + len(json_chunk) + 8 + len(bin_chunk)
    return b"".join(
        [
            struct.pack("<4sII", b"glTF", 2, total),
            struct.pack("<I4s", len(json_chunk), b"JSON"),
            json_chunk,
            struct.pack("<I4s", len(bin_chunk), b"BIN\x00"),
            bin_chunk,
        ]
    )


def images_by_material(data: bytes) -> dict[str, bytes]:
    """PNG bytes already embedded, keyed by material name."""
    doc, blob = read_glb(data)
    images: list[bytes] = []
    for image in doc.get("images") or []:
        view = doc["bufferViews"][int(image["bufferView"])]
        start = int(view.get("byteOffset", 0))
        images.append(bytes(blob[start : start + int(view["byteLength"])]))
    found: dict[str, bytes] = {}
    for material in doc.get("materials") or []:
        info = (material.get("pbrMetallicRoughness") or {}).get("baseColorTexture")
        if not info:
            continue
        found[str(material.get("name") or "")] = images[int(info["index"])]
    return found


def read_glb(data: bytes) -> tuple[dict[str, Any], bytes]:
    """Parse a GLB container (used by tests and validation)."""
    if len(data) < 28:
        raise ValueError("not a glTF 2.0 binary: too short")
    magic, version, total = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF" or version != 2 or total != len(data):
        raise ValueError("not a glTF 2.0 binary")
    json_len, json_type = struct.unpack_from("<I4s", data, 12)
    if json_type != b"JSON":
        raise ValueError("first chunk must be JSON")
    doc = json.loads(data[20 : 20 + json_len].decode("utf-8"))
    bin_len, bin_type = struct.unpack_from("<I4s", data, 20 + json_len)
    if bin_type != b"BIN\x00":
        raise ValueError("second chunk must be BIN")
    start = 28 + json_len
    return doc, data[start : start + bin_len]


def write_glb(path: Path, data: bytes) -> None:
    from onward_generator.io import atomic_write_bytes

    atomic_write_bytes(path, data)


def _local_matrix(gltf_cols: Sequence[float]) -> np.ndarray:
    """Inverse of :func:`gltf_matrix`."""
    g = np.array([float(v) for v in gltf_cols], dtype=np.float64).reshape(4, 4).T
    a = np.eye(4)
    a[:3, :3] = _TO_GLTF
    return a.T @ g @ a


def _accessor(doc: Mapping[str, Any], blob: bytes, index: int) -> np.ndarray:
    acc = doc["accessors"][index]
    view = doc["bufferViews"][acc["bufferView"]]
    offset = int(view.get("byteOffset", 0)) + int(acc.get("byteOffset", 0))
    count = int(acc["count"])
    components = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[acc["type"]]
    dtype = {
        5120: np.int8,
        5121: np.uint8,
        5122: np.int16,
        5123: np.uint16,
        5125: np.uint32,
        5126: np.float32,
    }[acc["componentType"]]
    width = int(np.dtype(dtype).itemsize) * components
    stride = view.get("byteStride")
    if stride not in (None, width):
        raise ValueError(f"accessor {index}: interleaved stride {stride} is not packed")
    raw = blob[offset : offset + count * width]
    if len(raw) != count * width:
        raise ValueError(f"accessor {index}: buffer is short")
    data = np.frombuffer(raw, dtype=dtype)
    return data if components == 1 else data.reshape(count, components)


def nodes_from_glb(data: bytes) -> tuple[list[Node], dict[str, Material]]:
    """Read a generator GLB back into local-metre nodes. UVs are kept if present."""
    doc, blob = read_glb(data)
    materials: dict[str, Material] = {}
    for i, raw in enumerate(doc.get("materials") or []):
        pbr = raw.get("pbrMetallicRoughness") or {}
        color = tuple(float(c) for c in (pbr.get("baseColorFactor") or [0.5, 0.5, 0.5, 1.0]))
        if len(color) < 4:
            color = (*color, 1.0)
        materials[str(raw.get("name") or f"mat_{i}")] = Material(color, float(pbr.get("roughnessFactor", 1.0)))
    mat_names = [str(m.get("name") or f"mat_{i}") for i, m in enumerate(doc.get("materials") or [])]
    uses: dict[int, int] = {}
    for node in doc.get("nodes") or []:
        if "mesh" in node:
            uses[int(node["mesh"])] = uses.get(int(node["mesh"]), 0) + 1
    meshes: list[tuple[Primitive, ...] | None] = [None] * len(doc.get("meshes") or [])
    nodes: list[Node] = []
    for raw in doc.get("nodes") or []:
        if "mesh" not in raw:
            continue
        mi = int(raw["mesh"])
        if meshes[mi] is None:
            prims: list[Primitive] = []
            for prim in (doc["meshes"][mi].get("primitives") or []):
                attrs = prim.get("attributes") or {}
                if "POSITION" not in attrs or "indices" not in prim:
                    continue
                pos = (_accessor(doc, blob, attrs["POSITION"]).astype(np.float64)) @ _TO_GLTF
                idx = _accessor(doc, blob, int(prim["indices"])).astype(np.int64).reshape(-1, 3)
                uvs = None
                if "TEXCOORD_0" in attrs:
                    uvs = _accessor(doc, blob, attrs["TEXCOORD_0"]).astype(np.float32)
                name = mat_names[int(prim.get("material", 0))] if mat_names else "ground"
                prims.append(Primitive(Mesh(pos, idx), name, uvs))
            meshes[mi] = tuple(prims)
        key = None
        mesh_name = (doc["meshes"][mi] or {}).get("name")
        if uses.get(mi, 0) > 1 and mesh_name:
            key = str(mesh_name)
        matrix = _local_matrix(raw["matrix"]) if raw.get("matrix") else None
        extras = dict(raw.get("extras") or {})
        nodes.append(Node(str(raw.get("name") or "node"), meshes[mi] or (), extras, matrix, key))
    return nodes, materials


def glb_append(data: bytes, nodes: Sequence[Node], materials: Mapping[str, Material]) -> bytes:
    """Keep every existing mesh, material and image; append ``nodes`` at the end."""
    existing, extra = nodes_from_glb(data)
    merged_mats = dict(extra)
    merged_mats.update(materials)
    extras = read_glb(data)[0].get("extras") or {}
    atlas_png = None
    doc, blob = read_glb(data)
    if doc.get("images"):
        img = doc["images"][0]
        view = doc["bufferViews"][img["bufferView"]]
        start = int(view.get("byteOffset", 0))
        atlas_png = bytes(blob[start : start + int(view["byteLength"])])
    return build_glb([*existing, *nodes], merged_mats, extras=extras if extras else None, atlas_png=atlas_png)
