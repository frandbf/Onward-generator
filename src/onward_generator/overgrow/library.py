"""Load prop GLBs named by every catalogue manifest. A missing folder is an empty library.

A GLB that reads but cannot be shown as the export draws it is rejected with a
reason. The exported material is opaque and albedo only, so a material whose
``alphaMode`` is not ``OPAQUE`` loses its transparency and is refused. A
vegetation mesh anchored ``flat`` that rises more than a few centimetres is a
slope tile, not ground cover, and is refused too. A row whose curated
placement is ``kit_only`` is a building or road kit piece and is refused
before its GLB is read, and so is a row the manifest ``refused`` (for
example ``unknown_scale``); the refusal is the reject reason. A 0.2.0
manifest that fails its schema is an error.

The whole scene of a GLB is read: every node, with its transform, and every
triangle primitive. Primitives that share an albedo become one part of the
prop; each part is exported as its own ``prop_<id>`` material. A row's
``yaw_deg`` turns the mesh about the vertical on load, so a file whose long
side is not on +X is placed like the others. The GLB is not rewritten.

A mesh with more triangles than its size needs is decimated on load
(``level_of_detail``): the budget is ``LOD_TRIS_PER_M2`` per square metre of
its largest bounding-box face, between ``LOD_MIN_TRIS`` and
``TARGET_ASSET_TRIS``, and a prop lower than ``FLAT_PROP_M`` (a manhole cover,
a ground tile) takes ``LOD_MIN_TRIS``. The size is the street size placement
scales the prop to. Each albedo part is clustered apart, and a copy whose
bounding box moves more than ``LOD_BOX_TOLERANCE_M`` is refused for a larger
target. The copy keeps ``source_triangles``; the GLB is not rewritten.
"""

from __future__ import annotations

import io
import json
import math
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

import numpy as np
from PIL import Image

from onward_generator.generation.gltf import Primitive, images_by_material, read_glb, scene_primitives
from onward_generator.generation.mesh import Mesh, merge
from onward_generator.overgrow.catalog import ASSETS_BY_ID, AssetSpec
from onward_generator.overgrow.constants import (
    FLAT_PROP_M,
    FLAT_VEGETATION_MAX_M,
    LOD_BOX_TOLERANCE_M,
    LOD_MIN_TRIS,
    LOD_TRIS_PER_M2,
    TARGET_ASSET_TRIS,
)
from onward_generator.overgrow.fit import apply_fit_scale
from onward_generator.overgrow.library_catalog import LibraryError, library_directories
from onward_generator.overgrow.manifest import KIT_ONLY, check_manifest
from onward_generator.overgrow.normalize import cluster_decimate, rest_on_ground


@dataclass(frozen=True, slots=True)
class LibraryReject:
    """A manifest row that names a readable GLB the library refused."""

    asset_id: str
    reason: str

    def as_dict(self) -> dict[str, str]:
        return {"asset_id": self.asset_id, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class PropPart:
    """Triangles ``[previous end, end)`` of a prop's mesh share this albedo."""

    end: int
    albedo: bytes


@dataclass(frozen=True, slots=True)
class Prop:
    """A library mesh. ``parts`` splits it by albedo; empty means one part with ``albedo``."""

    spec: AssetSpec
    mesh: Mesh
    uvs: np.ndarray
    albedo: bytes
    fit_scale: float = 1.0
    parts: tuple[PropPart, ...] = ()
    # Triangles of the file when ``level_of_detail`` reduced the mesh on load; 0 when it did not.
    source_triangles: int = 0

    def material(self, index: int) -> str:
        """``prop_<id>`` for the first part, ``prop_<id>__<index>`` for the others."""
        base = f"prop_{self.spec.id}"
        return base if index == 0 else f"{base}__{index}"

    def primitives(self) -> tuple[Primitive, ...]:
        """One primitive per part, each with its own vertices, UVs and material."""
        found: list[Primitive] = []
        start = 0
        for index, part in enumerate(self._parts()):
            triangles = self.mesh.triangles[start:part.end]
            used = np.unique(triangles)
            local = Mesh(self.mesh.vertices[used], np.searchsorted(used, triangles))
            found.append(Primitive(local, self.material(index), self.uvs[used]))
            start = part.end
        return tuple(found)

    def images(self) -> dict[str, bytes]:
        """Albedo of every part, by material name."""
        return {self.material(index): part.albedo for index, part in enumerate(self._parts())}

    def _parts(self) -> tuple[PropPart, ...]:
        return self.parts or (PropPart(self.mesh.triangle_count, self.albedo),)

    def lod(self) -> dict[str, int]:
        """``{"source_triangles", "triangles"}`` of a mesh reduced on load; empty for the file's own mesh."""
        return {"source_triangles": self.source_triangles, "triangles": self.triangles} if self.source_triangles else {}

    @property
    def triangles(self) -> int:
        return self.mesh.triangle_count

    @property
    def length_m(self) -> float:
        return _span(self.mesh, 0)

    @property
    def half_y(self) -> float:
        return _span(self.mesh, 1) * 0.5

    @property
    def height_m(self) -> float:
        return _span(self.mesh, 2)


def _span(mesh: Mesh, axis: int) -> float:
    if not mesh.vertex_count:
        return 0.0
    column = mesh.vertices[:, axis]
    return float(column.max() - column.min())


def load_library(path: str | Path | None) -> list[Prop]:
    """Read every catalogue under ``path``. Absent path → no props. Rejections are dropped."""
    return read_library(path)[0]


def read_library(path: str | Path | None) -> tuple[list[Prop], list[LibraryReject]]:
    """Read every catalogue under ``path`` and say which readable GLBs were refused.

    A directory with its own ``manifest.json`` is one catalogue. A parent such as
    ``assets`` contributes each child catalogue. A GLB that cannot be read is skipped.
    The same id in two catalogues is an error.
    """
    if path is None:
        return [], []
    props: list[Prop] = []
    rejects: list[LibraryReject] = []
    seen: dict[str, Path] = {}
    for directory in library_directories(path):
        loaded, refused = _load_directory(directory)
        rejects.extend(refused)
        for prop in loaded:
            previous = seen.get(prop.spec.id)
            if previous is not None and previous != directory:
                raise LibraryError(f"{prop.spec.id}: declared in {previous.name} and {directory.name}")
            seen[prop.spec.id] = directory
            props.append(prop)
    return props, rejects


def reject_reason(spec: AssetSpec, doc: dict, mesh: Mesh) -> str | None:
    """Why a readable GLB cannot join the library. ``None`` means it can."""
    for material in doc.get("materials") or []:
        mode = str(material.get("alphaMode") or "OPAQUE")
        if mode != "OPAQUE":
            return f"alpha_mode_{mode.lower()}"
    if spec.category == "vegetation" and spec.anchor == "flat" and _span(mesh, 2) > FLAT_VEGETATION_MAX_M:
        return "flat_vegetation_not_flat"
    return None


def _load_directory(root: Path) -> tuple[list[Prop], list[LibraryReject]]:
    manifest = root / "manifest.json"
    try:
        document = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], []
    check_manifest(document, str(manifest))
    props: list[Prop] = []
    rejects: list[LibraryReject] = []
    for row in document.get("assets") or []:
        if not isinstance(row, dict) or not row.get("id") or not row.get("file"):
            continue
        refusal = KIT_ONLY if row.get("placement") == KIT_ONLY else row.get("refused")
        if refusal:
            rejects.append(LibraryReject(str(row["id"]), str(refusal)))
            continue
        file = root / str(row["file"])
        if not file.is_file():
            continue
        spec = _spec(row)
        if spec is None:
            continue
        try:
            prop = _read(file, spec, float(row.get("yaw_deg") or 0.0))
            doc, _blob = read_glb(file.read_bytes())
        except (OSError, ValueError, KeyError):
            continue
        reason = reject_reason(spec, doc, prop.mesh)
        if reason is not None:
            rejects.append(LibraryReject(spec.id, reason))
            continue
        props.append(level_of_detail(prop))
    return props, rejects


def lod_target(mesh: Mesh, scale: float = 1.0) -> int:
    """Triangles a prop needs at ``scale`` times its size: per square metre of its largest box face, within the bounds."""
    length, depth, height = (_span(mesh, axis) * scale for axis in range(3))
    if height < FLAT_PROP_M:
        return LOD_MIN_TRIS
    face = max(length * depth, length * height, depth * height)
    return int(min(TARGET_ASSET_TRIS, max(LOD_MIN_TRIS, LOD_TRIS_PER_M2 * face)))


def level_of_detail(prop: Prop) -> Prop:
    """A copy with about ``lod_target`` triangles at street size, or ``prop`` itself.

    The target is measured at the size placement will scale the prop to. Clustering
    can drop a thin part (the feet of an air-conditioner), so a copy whose bounding
    box moved more than ``LOD_BOX_TOLERANCE_M`` at street size is refused and the
    target doubles, while it still saves a quarter of the file's triangles.
    """
    _street, scale = apply_fit_scale(prop.mesh, prop.spec.role)
    target = lod_target(prop.mesh, scale)
    # A copy that saves less than a quarter of the triangles is not worth the lost detail.
    while 4 * target <= 3 * prop.triangles:
        reduced = _decimated(prop, target)
        if box_shift(prop.mesh, reduced.mesh) * scale <= LOD_BOX_TOLERANCE_M:
            return reduced
        target *= 2
    return prop


def box_shift(a: Mesh, b: Mesh) -> float:
    """Largest move of any face of the bounding box between two meshes."""
    low = np.abs(a.vertices.min(axis=0) - b.vertices.min(axis=0))
    high = np.abs(a.vertices.max(axis=0) - b.vertices.max(axis=0))
    return float(max(low.max(), high.max()))


def _decimated(prop: Prop, target: int) -> Prop:
    """Each albedo part clustered to its share of ``target``; a part that would vanish keeps its triangles.

    The copy rests on the ground again, since clustering moves the lowest vertex.
    """
    meshes: list[Mesh] = []
    uvs: list[np.ndarray] = []
    parts: list[PropPart] = []
    end = 0
    for primitive, part in zip(prop.primitives(), prop._parts(), strict=True):
        source = primitive.mesh
        source_uvs = cast(np.ndarray, primitive.uvs)  # Prop.primitives always sets the UVs.
        share = round(target * source.triangle_count / prop.triangles)
        vertices, triangles, coords = cluster_decimate(source.vertices, source.triangles, source_uvs, share)
        if not len(triangles):
            vertices, triangles, coords = source.vertices, source.triangles, source_uvs
        meshes.append(Mesh(vertices, triangles))
        uvs.append(np.asarray(coords, dtype=np.float32))
        end += len(triangles)
        parts.append(PropPart(end, part.albedo))
    return replace(
        prop, mesh=rest_on_ground(merge(meshes)), uvs=np.concatenate(uvs), parts=tuple(parts),
        source_triangles=prop.triangles,
    )


def _spec(row: dict) -> AssetSpec | None:
    known = ASSETS_BY_ID.get(str(row["id"]))
    surfaces = tuple(str(s) for s in (row.get("surfaces") or (known.surfaces if known else ())))
    if not surfaces or not row.get("category"):
        if known is None:
            return None
        return known
    return AssetSpec(
        id=str(row["id"]),
        category=str(row.get("category") or (known.category if known else "")),
        role=str(row.get("role") or (known.role if known else "prop")),
        surfaces=surfaces,
        anchor=str(row.get("anchor") or (known.anchor if known else "ground")),
        use=str(row.get("use") or (known.use if known else "")),
        lane=str(row.get("lane") or (known.lane if known else "forbid")),
        clearance_m=float(row.get("clearance_m") or (known.clearance_m if known else 0.0)),
        **_semantics(row),
    )


def _semantics(row: dict) -> dict[str, Any]:
    """Curated fields a 0.2.0 row carries. An older row has none."""
    found: dict[str, Any] = {key: str(row[key]) for key in ("description", "placement", "grouping", "condition") if key in row}
    if "tags" in row:
        found["tags"] = tuple(str(tag) for tag in row["tags"])
    return found


def _read(path: Path, spec: AssetSpec, yaw_deg: float) -> Prop:
    """Every primitive of the scene, grouped by albedo, turned by ``yaw_deg`` and set on the ground."""
    data = path.read_bytes()
    albedos: dict[str, bytes] = {}
    groups: dict[bytes, list[Primitive]] = {}
    for primitive in scene_primitives(data):
        if not primitive.mesh.triangle_count:
            continue
        if primitive.material not in albedos:
            albedos[primitive.material] = _albedo(path, data, primitive.material)
        groups.setdefault(albedos[primitive.material], []).append(primitive)
    if not groups:
        raise ValueError(f"{path}: no mesh")
    meshes: list[Mesh] = []
    uvs: list[np.ndarray] = []
    parts: list[PropPart] = []
    end = 0
    for albedo, members in groups.items():
        for primitive in members:
            meshes.append(primitive.mesh)
            uvs.append(_uvs(primitive))
            end += primitive.mesh.triangle_count
        parts.append(PropPart(end, albedo))
    mesh = rest_on_ground(_turned(merge(meshes), yaw_deg))
    return Prop(spec, mesh, np.concatenate(uvs), parts[0].albedo, parts=tuple(parts))


def _uvs(primitive: Primitive) -> np.ndarray:
    """The primitive's UVs, or zeros when it has none for every vertex."""
    uvs = primitive.uvs
    if uvs is None or len(uvs) != primitive.mesh.vertex_count:
        return np.zeros((primitive.mesh.vertex_count, 2), dtype=np.float32)
    return uvs


def _turned(mesh: Mesh, degrees: float) -> Mesh:
    """Counter-clockwise turn about +Z (up). 90 puts the file's +Y on -X."""
    angle = math.radians(degrees)
    cos, sin = math.cos(angle), math.sin(angle)
    # Row vectors times the transpose of the rotation [[cos, -sin], [sin, cos]].
    rotation = np.array([[cos, sin, 0.0], [-sin, cos, 0.0], [0.0, 0.0, 1.0]])
    return Mesh(mesh.vertices @ rotation, mesh.triangles)


def _albedo(path: Path, data: bytes, material: str) -> bytes:
    """Embedded image, a PNG beside the GLB, or a flat white stand-in."""
    try:
        embedded = images_by_material(data).get(material) or b""
    except (KeyError, ValueError, IndexError):
        embedded = b""
    if embedded:
        return embedded
    uri = _base_color_uri(data, material)
    relative = Path(uri) if uri else None
    if relative is not None and not relative.is_absolute() and ".." not in relative.parts:
        for base in (path.parent, path.parent.parent):
            candidate = base / relative
            if candidate.is_file():
                return candidate.read_bytes()
    return _flat_white_png()


def _base_color_uri(data: bytes, material: str) -> str:
    doc, _blob = read_glb(data)
    images = doc.get("images") or []
    for raw in doc.get("materials") or []:
        if str(raw.get("name") or "") != material:
            continue
        info = (raw.get("pbrMetallicRoughness") or {}).get("baseColorTexture") or {}
        index = info.get("index")
        if isinstance(index, int) and 0 <= index < len(images):
            uri = images[index].get("uri")
            if isinstance(uri, str):
                return uri
    return ""


_WHITE_PNG: bytes | None = None


def _flat_white_png() -> bytes:
    global _WHITE_PNG
    if _WHITE_PNG is None:
        buffer = io.BytesIO()
        Image.new("RGB", (1, 1), (255, 255, 255)).save(buffer, format="PNG")
        _WHITE_PNG = buffer.getvalue()
    return _WHITE_PNG
