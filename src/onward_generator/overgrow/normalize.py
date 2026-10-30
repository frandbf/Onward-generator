"""Turn a Poly Haven glTF into one albedo-only local-metre mesh.

Source files stay on disk. This module writes a separate normalized GLB:
base colour only, no normal, roughness, metal or alpha cutout.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from onward_generator.generation.config import Material
from onward_generator.generation.gltf import _TO_GLTF, Node, Primitive, build_glb
from onward_generator.generation.mesh import Mesh
from onward_generator.overgrow.constants import ALBEDO_PX, TARGET_ASSET_TRIS


@dataclass(frozen=True, slots=True)
class NormalizedMesh:
    mesh: Mesh
    uvs: np.ndarray
    albedo_png: bytes
    triangles_before: int
    triangles_after: int
    scale_note: str
    orientation: str
    length_m: float
    depth_m: float
    height_m: float


def cluster_decimate(
    vertices: np.ndarray,
    triangles: np.ndarray,
    uvs: np.ndarray,
    target: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Vertex clustering. Deterministic. Keeps meshes that are already under ``target``."""
    if len(triangles) <= target or len(vertices) == 0:
        return vertices, triangles, uvs
    if len(triangles) > 80_000:
        extent = vertices.max(axis=0) - vertices.min(axis=0)
        coarse = max(float(np.max(extent)) / max(target ** 0.5, 1.0), 1e-4)
        vertices, triangles, uvs = _cluster(vertices, triangles, uvs, coarse)
        if len(triangles) <= target or len(triangles) == 0:
            return vertices, triangles, uvs
    extent = vertices.max(axis=0) - vertices.min(axis=0)
    hi = float(max(extent.max(), 1e-3))
    lo = hi / 10_000.0
    best_v, best_t, best_u = vertices, triangles, uvs
    for _ in range(16):
        cell = (lo + hi) * 0.5
        cand_v, cand_t, cand_u = _cluster(vertices, triangles, uvs, cell)
        best_v, best_t, best_u = cand_v, cand_t, cand_u
        if len(cand_t) > target:
            lo = cell
        else:
            hi = cell
    if len(best_t) > target:
        best_v, best_t, best_u = _cluster(vertices, triangles, uvs, hi)
    return best_v, best_t, best_u


def _cluster(
    vertices: np.ndarray,
    triangles: np.ndarray,
    uvs: np.ndarray,
    cell: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    origin = vertices.min(axis=0)
    quant = np.floor((vertices - origin) / cell).astype(np.int64)
    _, inverse = np.unique(quant, axis=0, return_inverse=True)
    counts = np.bincount(inverse)
    acc = np.zeros((len(counts), 3), dtype=np.float64)
    uv_acc = np.zeros((len(counts), 2), dtype=np.float64)
    np.add.at(acc, inverse, vertices)
    np.add.at(uv_acc, inverse, uvs)
    new_v = acc / counts[:, None]
    new_uv = (uv_acc / counts[:, None]).astype(np.float32)
    remapped = inverse[triangles]
    keep = (remapped[:, 0] != remapped[:, 1]) & (remapped[:, 1] != remapped[:, 2]) & (remapped[:, 0] != remapped[:, 2])
    remapped = remapped[keep]
    if len(remapped) == 0:
        return new_v[:1], np.zeros((0, 3), dtype=np.int64), new_uv[:1]
    return _compact(new_v, remapped, new_uv)


def _compact(vertices: np.ndarray, triangles: np.ndarray, uvs: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    used = np.unique(triangles.reshape(-1))
    remap = np.full(len(vertices), -1, dtype=np.int64)
    remap[used] = np.arange(len(used))
    return vertices[used], remap[triangles], uvs[used]


def flatten_albedo(data: bytes, size: int = ALBEDO_PX) -> bytes:
    """RGB PNG. Alpha is flattened so the shader never sees a cutout."""
    image = Image.open(io.BytesIO(data))
    if image.mode == "RGBA":
        background = Image.new("RGB", image.size, (90, 90, 90))
        background.paste(image, mask=image.split()[-1])
        image = background
    else:
        image = image.convert("RGB")
    image.thumbnail((size, size), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def gltf_to_local(folder: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray, bytes | None]:
    """World-space glTF vertices → local Z-up, plus the diffuse image bytes."""
    gltf_path = next(folder.glob("*.gltf"))
    doc = json.loads(gltf_path.read_text(encoding="utf-8"))
    blobs = _blobs(folder, doc)
    nodes = doc.get("nodes") or []
    worlds = _worlds(nodes, doc.get("scenes") or [{}])
    positions: list[np.ndarray] = []
    indices: list[np.ndarray] = []
    uvs: list[np.ndarray] = []
    offset = 0
    for index, node in enumerate(nodes):
        if "mesh" not in node:
            continue
        matrix = worlds[index]
        for prim in (doc["meshes"][int(node["mesh"])].get("primitives") or []):
            attrs = prim.get("attributes") or {}
            if "POSITION" not in attrs or int(prim.get("mode", 4)) != 4:
                continue
            pos = _accessor(doc, blobs, int(attrs["POSITION"])).astype(np.float64)
            pos = _apply(matrix, pos)
            if "TEXCOORD_0" in attrs:
                uv = _accessor(doc, blobs, int(attrs["TEXCOORD_0"])).astype(np.float32)
                if len(uv) != len(pos):
                    uv = _planar_uv(pos)
            else:
                uv = _planar_uv(pos)
            if "indices" in prim:
                idx = _accessor(doc, blobs, int(prim["indices"])).astype(np.int64).reshape(-1, 3)
            else:
                idx = np.arange(len(pos), dtype=np.int64).reshape(-1, 3)
            positions.append(pos)
            uvs.append(uv[:, :2])
            indices.append(idx + offset)
            offset += len(pos)
    if not positions:
        raise ValueError(f"{folder}: glTF has no triangles")
    local = np.vstack(positions) @ _TO_GLTF
    return local, np.vstack(indices), np.vstack(uvs), _diffuse_bytes(folder, doc)


def normalize_arrays(
    vertices: np.ndarray,
    triangles: np.ndarray,
    uvs: np.ndarray,
    albedo: bytes | None,
    *,
    anchor: str,
    api_dimensions_mm: list[float] | None,
) -> NormalizedMesh:
    before = int(len(triangles))
    scale_note = _apply_scale(vertices, api_dimensions_mm)
    vertices, orientation = _align(vertices, anchor)
    vertices = _ground(vertices)
    vertices, triangles, uvs = cluster_decimate(vertices, triangles, uvs, TARGET_ASSET_TRIS)
    mesh = Mesh(np.ascontiguousarray(vertices, dtype=np.float64), np.ascontiguousarray(triangles, dtype=np.int64))
    mesh, dropped = mesh.drop_degenerate()
    # Clustering replaces the contact vertices with a cell average, which lifts
    # the bottom by about half a cell. Put the lowest vertex back on z = 0.
    mesh = rest_on_ground(mesh)
    if dropped and len(uvs) == before:
        pass
    extent = mesh.vertices.max(axis=0) - mesh.vertices.min(axis=0) if mesh.vertex_count else np.zeros(3)
    png = flatten_albedo(albedo) if albedo else _flat_png()
    if len(uvs) != mesh.vertex_count:
        uvs = _planar_uv(mesh.vertices)
    return NormalizedMesh(
        mesh, np.ascontiguousarray(uvs[: mesh.vertex_count], dtype=np.float32), png,
        before, mesh.triangle_count, scale_note, orientation,
        float(extent[0]), float(extent[1]), float(extent[2]),
    )


def write_normalized_glb(path: Path, asset_id: str, normalized: NormalizedMesh) -> None:
    material = f"prop_{asset_id}"
    node = Node(
        asset_id,
        (Primitive(normalized.mesh, material, normalized.uvs),),
        {"type": "prop", "asset_id": asset_id, "anchor": normalized.orientation},
        mesh_key=f"prop:{asset_id}",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(build_glb(
        [node],
        {material: Material((1.0, 1.0, 1.0, 1.0), 0.9)},
        repeat_images={material: normalized.albedo_png},
        preserve_uvs=True,
    ))


def _apply_scale(vertices: np.ndarray, api_dimensions_mm: list[float] | None) -> str:
    extent = vertices.max(axis=0) - vertices.min(axis=0)
    if api_dimensions_mm:
        api_m = np.sort(np.array(api_dimensions_mm, dtype=np.float64) / 1000.0)
        got = np.sort(extent)
        close = max(0.25, 0.2 * float(api_m.max())) if len(api_m) == 3 else 0.0
        if len(api_m) == 3 and float(np.max(np.abs(api_m - got))) < close:
            return "glTF bbox matches the API dimensions converted from millimetres to metres"
        if len(api_m) == 3 and float(extent.max()) > 80.0 and float(np.max(np.abs(api_m - got * 0.001))) < close:
            vertices *= 0.001
            return "glTF bbox was millimetres; divided by 1000 to match the API"
    return "glTF bbox kept in metres"


def _align(vertices: np.ndarray, anchor: str) -> tuple[np.ndarray, str]:
    """Proper rotation so the pivot axes match the anchor. Z stays up except for flat covers."""
    extent = vertices.max(axis=0) - vertices.min(axis=0)
    order = [int(i) for i in np.argsort(extent)]
    if anchor == "flat" and order[0] != 2:
        thin = order[0]
        vertices = _map_axis_to_z(vertices, thin)
        return vertices, "thinnest axis rotated onto +Z (flat cover); proper rotation"
    if anchor == "facade":
        horizontal = [axis for axis in order if axis != 2] or [0, 1]
        thin_h = min(horizontal, key=lambda axis: extent[axis])
        if thin_h != 1:
            vertices = _swap_xy(vertices)
            return vertices, "thinnest horizontal axis aligned to +Y (wall outward); Z up"
        return vertices, "authored +Y is already the thin axis; Z up"
    if extent[0] < extent[1]:
        vertices = _swap_xy(vertices)
        return vertices, "longest horizontal axis aligned to +X; Z up"
    return vertices, "longest horizontal axis already +X; Z up"


def _swap_xy(vertices: np.ndarray) -> np.ndarray:
    # (x, y, z) → (y, -x, z), det +1.
    out = np.empty_like(vertices)
    out[:, 0] = vertices[:, 1]
    out[:, 1] = -vertices[:, 0]
    out[:, 2] = vertices[:, 2]
    return out


def _map_axis_to_z(vertices: np.ndarray, axis: int) -> np.ndarray:
    out = np.empty_like(vertices)
    if axis == 0:
        out[:, 0] = vertices[:, 2]
        out[:, 1] = vertices[:, 1]
        out[:, 2] = -vertices[:, 0]
        return out
    if axis == 1:
        out[:, 0] = vertices[:, 0]
        out[:, 1] = vertices[:, 2]
        out[:, 2] = -vertices[:, 1]
        return out
    return vertices


def rest_on_ground(mesh: Mesh) -> Mesh:
    """Copy with the lowest vertex at z = 0. X and Y stay where they are."""
    if mesh.vertex_count == 0:
        return mesh
    low = float(mesh.vertices[:, 2].min())
    if abs(low) <= 1e-9:
        return mesh
    vertices = np.array(mesh.vertices, dtype=np.float64, copy=True)
    vertices[:, 2] -= low
    return Mesh(np.ascontiguousarray(vertices), mesh.triangles)


def _ground(vertices: np.ndarray) -> np.ndarray:
    centre = (vertices[:, 0].min() + vertices[:, 0].max()) * 0.5, (vertices[:, 1].min() + vertices[:, 1].max()) * 0.5
    out = vertices.copy()
    out[:, 0] -= centre[0]
    out[:, 1] -= centre[1]
    out[:, 2] -= vertices[:, 2].min()
    return out


def _planar_uv(pos: np.ndarray) -> np.ndarray:
    if len(pos) == 0:
        return np.zeros((0, 2), dtype=np.float32)
    span = np.maximum(pos.max(axis=0) - pos.min(axis=0), 1e-6)
    return np.column_stack([(pos[:, 0] - pos[:, 0].min()) / span[0], (pos[:, 1] - pos[:, 1].min()) / span[1]]).astype(np.float32)


def _flat_png() -> bytes:
    image = Image.new("RGB", (8, 8), (120, 120, 120))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def materialize_images(folder: Path) -> None:
    """Write bufferView images next to the glTF so the albedo loader can read a file."""
    gltf_path = next(folder.glob("*.gltf"))
    doc = json.loads(gltf_path.read_text(encoding="utf-8"))
    blobs = _blobs(folder, doc)
    changed = False
    for index, image in enumerate(doc.get("images") or []):
        if image.get("uri") or image.get("bufferView") is None:
            continue
        view = doc["bufferViews"][int(image["bufferView"])]
        blob = blobs[int(view.get("buffer", 0))]
        start = int(view.get("byteOffset", 0))
        chunk = blob[start:start + int(view["byteLength"])]
        suffix = ".jpg" if "jpeg" in str(image.get("mimeType") or "") else ".png"
        name = f"image_{index}{suffix}"
        (folder / name).write_bytes(chunk)
        image["uri"] = name
        image.pop("bufferView", None)
        changed = True
    if changed:
        gltf_path.write_text(json.dumps(doc), encoding="utf-8")


def _blobs(folder: Path, doc: dict) -> list[bytes]:
    import base64
    blobs: list[bytes] = []
    for buf in doc.get("buffers") or []:
        uri = buf.get("uri")
        if not uri:
            raise ValueError("glTF buffer is embedded; expected an external .bin")
        if uri.startswith("data:"):
            blobs.append(base64.b64decode(uri.split(",", 1)[1]))
        else:
            blobs.append((folder / uri).read_bytes())
    if not blobs:
        raise ValueError(f"{folder}: glTF has no buffers")
    return blobs


def _diffuse_bytes(folder: Path, doc: dict) -> bytes | None:
    materials = doc.get("materials") or []
    textures = doc.get("textures") or []
    images = doc.get("images") or []
    found: list[str] = []
    for material in materials:
        texture = (material.get("pbrMetallicRoughness") or {}).get("baseColorTexture")
        if not texture:
            continue
        source = textures[int(texture["index"])].get("source")
        uri = images[int(source)].get("uri")
        if uri and (folder / uri).is_file():
            found.append(str(uri))
    for uri in found:
        if "leaves_diff" in uri:
            return (folder / uri).read_bytes()
    if found:
        return (folder / found[0]).read_bytes()
    return None


def _worlds(nodes: list, scenes: list) -> list[np.ndarray]:
    parent = {child: index for index, node in enumerate(nodes) for child in (node.get("children") or [])}
    local = [_local_matrix(node) for node in nodes]
    roots = [int(i) for i in (scenes[0].get("nodes") or range(len(nodes)))]
    world = [np.eye(4) for _ in nodes]
    seen: set[int] = set()

    def walk(index: int, parent_world: np.ndarray) -> None:
        if index in seen:
            return
        seen.add(index)
        world[index] = parent_world @ local[index]
        for child in nodes[index].get("children") or []:
            walk(int(child), world[index])

    for root in roots:
        walk(root, np.eye(4))
    for index in range(len(nodes)):
        if index not in seen:
            walk(index, world[parent[index]] if index in parent else np.eye(4))
    return world


def _local_matrix(node: dict) -> np.ndarray:
    if node.get("matrix"):
        return np.array(node["matrix"], dtype=np.float64).reshape((4, 4), order="F")
    matrix = np.eye(4)
    scale = node.get("scale") or [1.0, 1.0, 1.0]
    rotation = node.get("rotation") or [0.0, 0.0, 0.0, 1.0]
    translation = node.get("translation") or [0.0, 0.0, 0.0]
    rotate = np.eye(4)
    rotate[:3, :3] = _quaternion(rotation)
    scaled = np.diag([float(scale[0]), float(scale[1]), float(scale[2]), 1.0])
    matrix = rotate @ scaled
    matrix[:3, 3] = translation
    return matrix


def _quaternion(q: list) -> np.ndarray:
    x, y, z, w = (float(v) for v in q)
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ], dtype=np.float64)


def _apply(matrix: np.ndarray, points: np.ndarray) -> np.ndarray:
    homo = np.column_stack([points, np.ones(len(points))])
    return (matrix @ homo.T).T[:, :3]


def _accessor(doc: dict, blobs: list[bytes], index: int) -> np.ndarray:
    acc = doc["accessors"][index]
    view = doc["bufferViews"][acc["bufferView"]]
    blob = blobs[int(view.get("buffer", 0))]
    components = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[acc["type"]]
    dtype = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}[acc["componentType"]]
    count = int(acc["count"])
    offset = int(view.get("byteOffset", 0)) + int(acc.get("byteOffset", 0))
    stride = int(view.get("byteStride", 0))
    width = int(np.dtype(dtype).itemsize) * components
    if stride and stride != width:
        out = np.empty((count, components), dtype=np.float64)
        for row in range(count):
            start = offset + row * stride
            out[row] = np.frombuffer(bytes(blob[start:start + width]), dtype=dtype, count=components)
        return out
    raw = np.frombuffer(bytes(blob[offset:offset + count * width]), dtype=dtype, count=count * components).astype(np.float64, copy=False)
    return raw if components == 1 else raw.reshape(count, components)
