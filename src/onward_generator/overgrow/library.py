"""Load prop GLBs named by every catalogue manifest. A missing folder is an empty library.

A GLB that reads but cannot be shown as the export draws it is rejected with a
reason. The exported material is opaque and albedo only, so a material whose
``alphaMode`` is not ``OPAQUE`` loses its transparency and is refused. A
vegetation mesh anchored ``flat`` that rises more than a few centimetres is a
slope tile, not ground cover, and is refused too.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from onward_generator.generation.gltf import images_by_material, nodes_from_glb, read_glb
from onward_generator.generation.mesh import Mesh, transform
from onward_generator.overgrow.catalog import ASSETS_BY_ID, AssetSpec
from onward_generator.overgrow.constants import FLAT_VEGETATION_MAX_M
from onward_generator.overgrow.library_catalog import LibraryError, library_directories
from onward_generator.overgrow.normalize import rest_on_ground


@dataclass(frozen=True, slots=True)
class LibraryReject:
    """A manifest row that names a readable GLB the library refused."""

    asset_id: str
    reason: str

    def as_dict(self) -> dict[str, str]:
        return {"asset_id": self.asset_id, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class Prop:
    spec: AssetSpec
    mesh: Mesh
    uvs: np.ndarray
    albedo: bytes
    fit_scale: float = 1.0

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
    props: list[Prop] = []
    rejects: list[LibraryReject] = []
    for row in document.get("assets") or []:
        if not isinstance(row, dict) or not row.get("id") or not row.get("file"):
            continue
        file = root / str(row["file"])
        if not file.is_file():
            continue
        spec = _spec(row)
        if spec is None:
            continue
        try:
            prop = _read(file, spec)
            doc, _blob = read_glb(file.read_bytes())
        except (OSError, ValueError, KeyError):
            continue
        reason = reject_reason(spec, doc, prop.mesh)
        if reason is not None:
            rejects.append(LibraryReject(spec.id, reason))
            continue
        props.append(prop)
    return props, rejects


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
    )


def _read(path: Path, spec: AssetSpec) -> Prop:
    data = path.read_bytes()
    nodes, _materials = nodes_from_glb(data)
    if not nodes or not nodes[0].primitives:
        raise ValueError(f"{path}: no mesh")
    primitive = nodes[0].primitives[0]
    mesh = primitive.mesh
    if nodes[0].matrix is not None:
        mesh = transform(mesh, nodes[0].matrix)
    mesh = rest_on_ground(mesh)
    albedo = _albedo(path, data, primitive.material)
    uvs = primitive.uvs
    if uvs is None or len(uvs) != mesh.vertex_count:
        uvs = np.zeros((mesh.vertex_count, 2), dtype=np.float32)
    return Prop(spec, mesh, np.asarray(uvs, dtype=np.float32), albedo)


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
