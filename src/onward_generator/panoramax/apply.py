"""Street-photo decals. Under development.

The generated shell stays. A frontal stretch of a 360 picture is added in front of the wall.

The roof, the collision mesh, interiors, landmarks, and buildings without a
photo keep the vertices they already had. A party wall keeps the wall material.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from shapely import union_all
from shapely.geometry import Polygon, shape

from onward_generator.detail.config import load_detail_config
from onward_generator.detail.facades import Segment, party_material, refine_exposure
from onward_generator.detail.s3db import TINT_SEPARATOR
from onward_generator.generation.config import Material
from onward_generator.generation.gltf import (
    Node,
    Primitive,
    build_glb,
    images_by_material,
    nodes_from_glb,
)
from onward_generator.generation.mesh import Mesh, quad
from onward_generator.geo.projection import LocalFrame
from onward_generator.io import atomic_write_bytes, atomic_write_json, prepare_outputs
from onward_generator.maps.workspace import DEFAULT_COMBINE_CELL_M, MapError, load_map
from onward_generator.materials.trim import scenic_trim_material, trim_material
from onward_generator.materials.trim_style import Strip, TrimLayout, load_regional_table, load_style_table, style_atlas, trim_layout
from onward_generator.next_step import map_root
from onward_generator.panoramax.cache import PanoramaxError, cache_directory
from onward_generator.panoramax.match import (
    BuildingFootprint,
    StreetPicture,
    match_facades,
    picture_from_feature,
)
from onward_generator.panoramax.project import BASE_COLOR, FacadeImage, ProjectionError, project_facade
from onward_generator.tiers import combine_cell

REPORT_SCHEMA = "onward_generator.panoramax_scene"
_INSET = 0.001
_CAMERA_Z = 1.6
_DECAL_M = 0.04
_PHOTO_Z_MIN = 2.2
_MAX_ELEVATION_DEG = 35.0
_MIN_PHOTO_HEIGHT_M = 2.0
_PIXELS_PER_METRE = 80
_ATLAS_SIDE = 1024
OUTPUT_FILES = (
    "scene_visual.glb",
    "scene_collision.glb",
    "scene_detail.json",
    "detail_report.json",
    "panoramax_report.json",
)


def apply_pictures(
    base: Path,
    out: Path,
    *,
    overwrite: bool = False,
    atlas: bool = False,
    cell_m: float = DEFAULT_COMBINE_CELL_M,
) -> dict[str, Any]:
    """Write ``scene_panoramax`` from a materials directory and the local cache.

    ``atlas`` (map.json ``"panoramax_atlas": true``) packs the decals into one
    image per combine cell of ``cell_m`` metres and tints the photographed
    buildings' trim walls; off, the stage writes what it always wrote.
    """
    root = map_root(base)
    if root is None:
        raise PanoramaxError(f"{base} is not inside a map")
    source = base / "scene_visual.glb"
    collision_path = base / "scene_collision.glb"
    manifest_path = base / "scene_detail.json"
    if not source.is_file() or not collision_path.is_file() or not manifest_path.is_file():
        raise PanoramaxError(f"materials scene not found in {base}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    geo_path = Path(str((manifest.get("inputs") or {}).get("geo_scene", {}).get("path") or ""))
    if not geo_path.is_file():
        raise PanoramaxError("the materials scene does not point at a geo scene")
    report_path = base / "detail_report.json"
    detail_report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {}
    pictures = _cached_pictures(cache_directory(root))
    geo = json.loads(geo_path.read_bytes())
    if atlas:
        materials_report_path = base / "materials_report.json"
        materials_report = json.loads(materials_report_path.read_bytes()) if materials_report_path.is_file() else {}
        visual, report = _compose_atlas(
            source.read_bytes(),
            manifest,
            geo,
            detail_report,
            pictures,
            _local_frame(root),
            cell_m=cell_m,
            facades=materials_report.get("facades"),
        )
    else:
        visual, report = _compose(
            source.read_bytes(),
            manifest,
            geo,
            detail_report,
            pictures,
            _local_frame(root),
        )
    collision = collision_path.read_bytes()
    report["collision_sha256"] = hashlib.sha256(collision).hexdigest()
    prepare_outputs(out, OUTPUT_FILES, overwrite=overwrite)
    atomic_write_bytes(out / "scene_visual.glb", visual)
    atomic_write_bytes(out / "scene_collision.glb", collision)
    atomic_write_json(out / "scene_detail.json", manifest)
    atomic_write_json(out / "detail_report.json", detail_report)
    atomic_write_json(out / "panoramax_report.json", report)
    return report


def _compose(
    visual: bytes,
    manifest: dict[str, Any],
    geo: dict[str, Any],
    detail_report: dict[str, Any],
    pictures: list[tuple[StreetPicture, Path, float]],
    frame: LocalFrame,
) -> tuple[bytes, dict[str, Any]]:
    nodes, materials = nodes_from_glb(visual)
    images = images_by_material(visual)
    footprints = _footprints(geo, manifest)
    interiors = _interior_object_ids(manifest, listed_interiors(detail_report.get("counts") or {}))
    match = match_facades(footprints, [item[0] for item in pictures], frame=frame, interior_ids=interiors)
    by_building: dict[str, list[dict[str, Any]]] = {}
    for row in match["assignments"]:
        if isinstance(row, dict):
            by_building.setdefault(str(row["building_id"]), []).append(row)
    used = {str(row["picture_id"]) for rows in by_building.values() for row in rows}
    blobs = {item[0].id: _decode(item[1]) for item in pictures if item[0].id in used}
    azimuths = {item[0].id: item[2] for item in pictures}
    cameras = {item[0].id: item[0] for item in pictures}
    replaced: list[Node] = []
    for node in nodes:
        rows = by_building.get(node.name)
        if not rows or node.matrix is not None:
            replaced.append(node)
            continue
        replaced.append(_replace_node(node, rows, footprints, blobs, azimuths, cameras, frame, images, materials))
    matched = [row for row in match["assignments"] if isinstance(row, dict)]
    applied_ids = {id(row) for row in matched if row.get("applied")}
    assignments: list[dict[str, Any]] = []
    for row in matched:
        if id(row) in applied_ids:
            row.pop("applied", None)
            assignments.append(row)
    skipped_faces = list(match["skipped_faces"])
    skipped_faces.extend(
        {
            "building_id": str(row["building_id"]),
            "segment_index": int(row["segment_index"]),
            "reason": "elevation_window",
        }
        for row in matched
        if id(row) not in applied_ids
    )
    extras = {
        "manifest": "scene_detail.json",
        "panoramax": {"assignments": assignments, "license": "CC-BY-SA-4.0", "origin": "observed"},
    }
    written = build_glb(replaced, materials, extras=extras, preserve_uvs=True, repeat_images=images)
    report = {
        "schema": REPORT_SCHEMA,
        "schema_version": "0.1.0",
        "assignments": assignments,
        "skipped_faces": skipped_faces,
        "skipped_buildings": match["skipped_buildings"],
        "license": "CC-BY-SA-4.0",
        "origin": "observed",
    }
    return written, report


def _replace_node(
    node: Node,
    rows: list[dict[str, Any]],
    footprints: list[BuildingFootprint],
    blobs: dict[str, Image.Image],
    azimuths: dict[str, float],
    cameras: dict[str, StreetPicture],
    frame: LocalFrame,
    images: dict[str, bytes],
    materials: dict[str, Material],
) -> Node:
    """Keep the generated shell and stick a photo on each frontal stretch."""
    height = _facade_height(node.primitives)
    segments = {segment.index: segment for segment in _ring_segments(node.name, footprints)}
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(int(row["segment_index"]), []).append(row)
    added: list[Primitive] = []
    noted: list[dict[str, Any]] = []
    for index, face_rows in sorted(grouped.items()):
        segment = segments.get(index)
        if segment is None:
            continue
        patches: list[tuple[Mesh, FacadeImage, dict[str, Any]]] = []
        for row in face_rows:
            picture = cameras[str(row["picture_id"])]
            patch = _photo_patch(segment, row, picture, azimuths[picture.id], frame, height, blobs[picture.id])
            if patch is not None:
                row["applied"] = True
                patches.append(patch)
        if not patches:
            continue
        png, uv_list = _pack_patches([item[1] for item in patches])
        name = f"pano-{_safe(node.name)}-{index}"
        images[name] = png
        materials[name] = Material(BASE_COLOR, 0.9)
        for (mesh, _image, note), uvs in zip(patches, uv_list):
            added.append(Primitive(mesh, name, uvs))
            noted.append(note)
    if not added:
        return node
    extras = dict(node.extras or {})
    extras["panoramax"] = noted
    return Node(node.name, tuple(node.primitives) + tuple(added), extras, node.matrix, None)


def _photo_patch(
    segment: Segment,
    row: dict[str, Any],
    picture: StreetPicture,
    azimuth_deg: float,
    frame: LocalFrame,
    wall_height: float,
    image: Image.Image,
) -> tuple[Mesh, FacadeImage, dict[str, Any]] | None:
    s0 = float(row["s0_m"])
    s1 = float(row["s1_m"])
    if s1 - s0 < 0.5:
        return None
    start = segment.point(s0)
    end = segment.point(s1)
    camera = _camera_xyz(picture, frame)
    origin = np.array([segment.a[0], segment.a[1]])
    outward = segment.n_out[:2]
    outward_length = float(np.linalg.norm(outward))
    if outward_length < 1e-9:
        return None
    outward = outward / outward_length
    distance_m = abs(float(np.dot(np.array(camera[:2]) - origin, outward)))
    z1 = min(wall_height, camera[2] + distance_m * math.tan(math.radians(_MAX_ELEVATION_DEG)))
    z0 = _PHOTO_Z_MIN
    if z1 - z0 < _MIN_PHOTO_HEIGHT_M:
        return None
    span_m = max(s1 - s0, 0.5)
    long_side = min(512, max(64, int(round(max(span_m, z1 - z0) * _PIXELS_PER_METRE))))
    try:
        facade = project_facade(
            image,
            camera_xyz=camera,
            azimuth_deg=azimuth_deg,
            wall_a=(float(start[0]), float(start[1])),
            wall_b=(float(end[0]), float(end[1])),
            height_m=z1,
            z_min=z0,
            long_side=long_side,
        )
    except ProjectionError as exc:
        raise PanoramaxError(str(exc)) from exc
    offset = outward * _DECAL_M
    mesh = quad(
        (float(start[0]) + offset[0], float(start[1]) + offset[1], z0),
        (float(end[0]) + offset[0], float(end[1]) + offset[1], z0),
        (float(end[0]) + offset[0], float(end[1]) + offset[1], z1),
        (float(start[0]) + offset[0], float(start[1]) + offset[1], z1),
    )
    note = {
        "segment": segment.index,
        "s0_m": round(s0, 3),
        "s1_m": round(s1, 3),
        "z0_m": round(z0, 3),
        "z1_m": round(z1, 3),
        "picture_id": picture.id,
        "author": row["author"],
        "license": row["license"],
        "origin": "observed",
    }
    return mesh, facade, note


def _pack_patches(patches: list[FacadeImage]) -> tuple[bytes, list[np.ndarray]]:
    """One image per face. A single patch keeps the full 0–1 UV range."""
    if len(patches) == 1:
        return patches[0].png, [_inset_uvs()]
    images = [Image.open(io.BytesIO(patch.png)).convert("RGB") for patch in patches]
    scale = 1.0
    placed: list[tuple[Image.Image, int, int]] = []
    side = _ATLAS_SIDE
    for _attempt in range(5):
        placed = []
        cursor_x = cursor_y = row_height = 0
        fits = True
        for image in images:
            width = max(1, int(image.width * scale))
            height = max(1, int(image.height * scale))
            if cursor_x + width > side:
                cursor_x = 0
                cursor_y += row_height + 2
                row_height = 0
            if cursor_y + height > side:
                fits = False
                break
            placed.append((image.resize((width, height), Image.Resampling.LANCZOS) if scale != 1.0 else image, cursor_x, cursor_y))
            cursor_x += width + 2
            row_height = max(row_height, height)
        if fits and placed:
            break
        scale *= 0.75
    else:
        raise PanoramaxError("facade photographs do not fit in one texture")
    canvas = Image.new("RGB", (side, side), (0, 0, 0))
    uvs: list[np.ndarray] = []
    for image, left, top in placed:
        canvas.paste(image, (left, top))
        u0 = (left + 0.5) / side
        u1 = (left + image.width - 0.5) / side
        v_top = 1.0 - (top + 0.5) / side
        v_bottom = 1.0 - (top + image.height - 0.5) / side
        uvs.append(np.array([[u0, v_bottom], [u1, v_bottom], [u1, v_top], [u0, v_top]], dtype=np.float32))
    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG")
    return buffer.getvalue(), uvs


def _inset_uvs() -> np.ndarray:
    lo, hi = _INSET, 1.0 - _INSET
    return np.array([[lo, lo], [hi, lo], [hi, hi], [lo, hi]], dtype=np.float32)


def _facade_height(primitives: tuple[Primitive, ...]) -> float:
    walls = [prim for prim in primitives if "roof" not in prim.material and prim.mesh.vertex_count]
    pool = walls or [prim for prim in primitives if prim.mesh.vertex_count]
    if not pool:
        return 3.0
    return float(max(prim.mesh.vertices[:, 2].max() for prim in pool))


def _ring_segments(building_id: str, footprints: list[BuildingFootprint]) -> list[Segment]:
    try:
        own = next(item for item in footprints if item.id == building_id)
    except StopIteration as exc:
        raise PanoramaxError(f"no footprint for {building_id}") from exc
    neighbours = [item.polygon for item in footprints if item.id != building_id and item.kind == "building"]
    others = union_all(neighbours) if neighbours else Polygon()
    _, segments = refine_exposure(own.polygon, others, load_detail_config())
    return [segment for segment in segments if segment.ring == 0]


def listed_interiors(counts: dict[str, Any]) -> list[Any]:
    """Buildings ``detail`` gave an interior: the stair interiors, then the shops, whose display windows are see-through too."""
    return [*(counts.get("interiors") or []), *(counts.get("shop_interiors") or [])]


def _interior_object_ids(manifest: dict[str, Any], listed: object) -> list[str]:
    """Building node names whose source id or object id is in the detail interior list."""
    raw = {str(item) for item in listed} if isinstance(listed, list) else set()
    blocked: set[str] = set()
    for obj in manifest.get("objects") or []:
        if not isinstance(obj, dict) or obj.get("type") != "building":
            continue
        object_id = str(obj.get("id") or "")
        sources = [str(item) for item in obj.get("source_ids") or []]
        if object_id in raw or any(source in raw for source in sources):
            blocked.add(object_id)
    return sorted(blocked)


def _footprints(geo: dict[str, Any], manifest: dict[str, Any]) -> list[BuildingFootprint]:
    source_to_object: dict[str, str] = {}
    for obj in manifest.get("objects") or []:
        if not isinstance(obj, dict) or obj.get("type") != "building":
            continue
        object_id = str(obj.get("id") or "")
        source_to_object[object_id] = object_id
        for source in obj.get("source_ids") or []:
            source_to_object[str(source)] = object_id
    found: list[BuildingFootprint] = []
    for feature in geo.get("features") or []:
        if feature.get("kind") != "building":
            continue
        object_id = source_to_object.get(str(feature.get("id") or ""))
        if object_id is None:
            continue
        geom = shape(feature["geometry"])
        if geom.geom_type == "MultiPolygon":
            geom = max(geom.geoms, key=lambda item: item.area)
        if geom.geom_type != "Polygon" or geom.is_empty:
            continue
        found.append(BuildingFootprint(object_id, "building", geom))
    return found


def _local_frame(root: Path) -> LocalFrame:
    try:
        document = load_map(root)
    except MapError as exc:
        raise PanoramaxError(str(exc)) from exc
    center = document["center"]
    return LocalFrame.create(float(center["longitude"]), float(center["latitude"]), str(document["crs"]))


def _camera_xyz(picture: StreetPicture, frame: LocalFrame) -> tuple[float, float, float]:
    from shapely import Point

    local = frame.to_local(Point(picture.longitude, picture.latitude))
    return (float(local.x), float(local.y), _CAMERA_Z)


def _decode(jpeg: Path) -> Image.Image:
    """Read the pixels and close the file. ``project_facade`` converts to RGB when it samples."""
    with Image.open(jpeg) as image:
        image.load()
        return image


def _cached_pictures(cache: Path) -> list[tuple[StreetPicture, Path, float]]:
    """Cached pictures with their JPEG path. A JPEG is decoded only when a facade uses it."""
    from onward_generator.panoramax.cache import catalogue_directory

    report_path = cache / "cache_report.json"
    if not report_path.is_file():
        return []
    report = json.loads(report_path.read_text(encoding="utf-8"))
    features = {
        str(feature.get("id")): feature
        for feature in json.loads((cache / "search.geojson").read_text(encoding="utf-8")).get("features") or []
    }
    catalogue = catalogue_directory()
    loaded: list[tuple[StreetPicture, Path, float]] = []
    for picture_id in report.get("kept") or []:
        sidecar_path = catalogue / f"{picture_id}.source.json"
        jpeg = catalogue / f"{picture_id}.jpg"
        feature = features.get(str(picture_id))
        if feature is None or not sidecar_path.is_file() or not jpeg.is_file():
            continue
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        picture = picture_from_feature(
            feature,
            author=str(sidecar.get("author") or ""),
            license_name=str(sidecar.get("license") or ""),
        )
        if picture is None:
            continue
        loaded.append((picture, jpeg, float(sidecar.get("view_azimuth") or 0.0)))
    return loaded


def _safe(value: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in value)


# --- The decal atlas per combine cell and the photo tint (map.json "panoramax_atlas": true, ADR-020) ---

_CELL_SIDE = 2048
_CELL_SMALL_SIDES = (256, 512, 1024)
_CELL_ATTEMPTS = 12
_CELL_SHRINK = 0.75
_CELL_GAP_PX = 2
# Half a pixel of the atlas: V this close to a strip edge is inside it. The gutters are wider.
_STRIP_TOLERANCE_PX = 0.5
TINT_SKIP_REASONS = ("no_trim_atlas", "layout_mismatch", "osm_colour", "overture_colour", "no_wall_strip", "unchanged")


@dataclass(frozen=True, slots=True)
class Patch:
    """One applied crop: the decal quad, its CC BY-SA image and its note."""

    node: str
    mesh: Mesh
    image: FacadeImage
    note: dict[str, Any]

    @property
    def key(self) -> tuple[str, int, float, str]:
        """(building, face, start along the face, picture id): the packing order."""
        return (self.node, int(self.note["segment"]), float(self.note["s0_m"]), str(self.note["picture_id"]))


@dataclass(frozen=True, slots=True)
class TintAtlas:
    """The trim atlas a photographed building's wall strip can move inside."""

    style: str
    layout: TrimLayout
    families: Mapping[str, str]
    colours: Mapping[tuple[str, int], tuple[float, float, float]]
    materials: frozenset[str]


def _compose_atlas(
    visual: bytes,
    manifest: dict[str, Any],
    geo: dict[str, Any],
    detail_report: dict[str, Any],
    pictures: list[tuple[StreetPicture, Path, float]],
    frame: LocalFrame,
    *,
    cell_m: float,
    facades: Mapping[str, Any] | None,
) -> tuple[bytes, dict[str, Any]]:
    """The scene with one decal image and material per combine cell, the tint and the coverage."""
    nodes, materials = nodes_from_glb(visual)
    images = images_by_material(visual)
    tint_atlas = tint_atlas_for(facades, images)
    footprints = _footprints(geo, manifest)
    interiors = _interior_object_ids(manifest, listed_interiors(detail_report.get("counts") or {}))
    match = match_facades(footprints, [item[0] for item in pictures], frame=frame, interior_ids=interiors)
    matched = _rows(match["assignments"])
    by_building: dict[str, list[dict[str, Any]]] = {}
    for row in matched:
        by_building.setdefault(str(row["building_id"]), []).append(row)
    used = {str(row["picture_id"]) for row in matched}
    blobs = {item[0].id: _decode(item[1]) for item in pictures if item[0].id in used}
    azimuths = {item[0].id: item[2] for item in pictures}
    cameras = {item[0].id: item[0] for item in pictures}
    applied: set[int] = set()
    patches_of: dict[str, list[Patch]] = {}
    for node in nodes:
        rows = by_building.get(node.name)
        if not rows or node.matrix is not None:
            continue
        # In match order: faces in ring order, stretches along each face. The cells sort their own.
        found = _node_patches(node, rows, footprints, blobs, azimuths, cameras, frame, applied)
        if found:
            patches_of[node.name] = found
    cells = group_cells(nodes, patches_of, cell_m)
    decal_uvs: dict[tuple[str, int, float, str], tuple[str, np.ndarray]] = {}
    decal_cells: list[dict[str, Any]] = []
    for cell, ordered in cells.items():
        png, uv_list, side, scale = _pack_cell([patch.image for patch in ordered])
        name = cell_material(cell)
        images[name] = png
        materials[name] = Material(BASE_COLOR, 0.9)
        for index, patch in enumerate(ordered):
            decal_uvs[patch.key] = (name, uv_list[index])
        decal_cells.append(cell_row(cell, ordered, side, scale))
    tints: list[dict[str, Any]] = []
    tint_skipped: list[dict[str, str]] = []
    replaced: list[Node] = []
    for node in nodes:
        patches = patches_of.get(node.name)
        if not patches:
            replaced.append(node)
            continue
        tinted, row = tint_node(node, patches, tint_atlas)
        if "reason" in row:
            tint_skipped.append({"building_id": node.name, "reason": str(row["reason"])})
        else:
            tints.append({"building_id": node.name, **row})
        added = tuple(Primitive(patch.mesh, *decal_uvs[patch.key]) for patch in patches)
        extras = {**tinted.extras, "panoramax": [patch.note for patch in patches]}
        if "reason" not in row:
            extras["panoramax_tint"] = row
        # A node with a matrix gets no photo (see above), so the default matrix and mesh key are its own.
        replaced.append(Node(node.name, tuple(tinted.primitives) + added, extras))
    assignments: list[dict[str, Any]] = [row for row in matched if id(row) in applied]
    skipped_faces = _rows(match["skipped_faces"])
    skipped_faces.extend(
        {
            "building_id": str(row["building_id"]),
            "segment_index": int(row["segment_index"]),
            "reason": "elevation_window",
        }
        for row in matched
        if id(row) not in applied
    )
    extras_doc = {
        "manifest": "scene_detail.json",
        "panoramax": {"assignments": assignments, "license": "CC-BY-SA-4.0", "origin": "observed"},
    }
    written = build_glb(replaced, materials, extras=extras_doc, preserve_uvs=True, repeat_images=images)
    faces = _rows(match["faces"])
    report = {
        "schema": REPORT_SCHEMA,
        "schema_version": "0.1.0",
        "assignments": assignments,
        "skipped_faces": skipped_faces,
        "skipped_buildings": match["skipped_buildings"],
        "license": "CC-BY-SA-4.0",
        "origin": "observed",
        "atlas": {"cell_m": cell_m, "side_px": _CELL_SIDE},
        "coverage": coverage(faces, assignments),
        "decal_cells": decal_cells,
        "draws_added": len(decal_cells),
        "tints": tints,
        "tint_skipped": tint_skipped,
    }
    return written, report


def group_cells(
    nodes: Sequence[Node],
    patches_of: Mapping[str, Sequence[Patch]],
    cell_m: float,
) -> dict[tuple[int, int], list[Patch]]:
    """The crops of each combine cell, sorted. A node's cell counts its decals, as the adapter will."""
    cells: dict[tuple[int, int], list[Patch]] = {}
    for node in nodes:
        patches = patches_of.get(node.name)
        if not patches:
            continue
        decals = [Primitive(patch.mesh, "") for patch in patches]  # pragma: no mutate: combine_cell reads only the mesh, never the material name
        cells.setdefault(combine_cell((*node.primitives, *decals), cell_m), []).extend(patches)
    return {cell: sorted(patches, key=lambda patch: patch.key) for cell, patches in sorted(cells.items())}


def _rows(value: object) -> list[dict[str, Any]]:
    """The dict rows of a ``match_facades`` list."""
    if not isinstance(value, list):
        raise TypeError(f"match_facades gave {type(value).__name__}, not a list")
    return [row for row in value if isinstance(row, dict)]


def cell_row(cell: tuple[int, int], ordered: Sequence[Patch], side: int, scale: float) -> dict[str, Any]:
    """The ``decal_cells`` row of one combine cell."""
    return {
        "cell": [cell[0], cell[1]],
        "material": cell_material(cell),
        "decals": len(ordered),
        "buildings": sorted({patch.node for patch in ordered}),
        "side_px": side,
        "scale": round(scale, 4),
    }


def cell_material(cell: tuple[int, int]) -> str:
    """The one decal material of a combine cell."""
    return f"pano-cell-{cell[0]}_{cell[1]}"


def _node_patches(
    node: Node,
    rows: list[dict[str, Any]],
    footprints: list[BuildingFootprint],
    blobs: dict[str, Image.Image],
    azimuths: dict[str, float],
    cameras: dict[str, StreetPicture],
    frame: LocalFrame,
    applied: set[int],
) -> list[Patch]:
    """The crops of one building, as ``_replace_node`` builds them; the ids of the applied rows go in ``applied``."""
    height = _facade_height(node.primitives)
    segments = {segment.index: segment for segment in _ring_segments(node.name, footprints)}
    found: list[Patch] = []
    for row in rows:
        # match_facades took the row from these same exposed segments, so its face is there.
        segment = segments[int(row["segment_index"])]
        picture = cameras[str(row["picture_id"])]
        patch = _photo_patch(segment, row, picture, azimuths[picture.id], frame, height, blobs[picture.id])
        if patch is not None:
            applied.add(id(row))
            found.append(Patch(node.name, *patch))
    return found


def _shelf(sizes: list[tuple[int, int]], side: int) -> list[tuple[int, int]] | None:
    """Top-left corners of ``sizes`` packed in rows across a ``side`` square, or None when they do not fit."""
    corners: list[tuple[int, int]] = []
    cursor_x = cursor_y = row_height = 0
    for width, height in sizes:
        if cursor_x + width > side:
            cursor_x = 0
            cursor_y += row_height + _CELL_GAP_PX
            row_height = 0  # pragma: no mutate: every crop is at least 1 px tall, so the max below overwrites 0 or 1 alike
        if cursor_x + width > side or cursor_y + height > side:
            return None
        corners.append((cursor_x, cursor_y))
        cursor_x += width + _CELL_GAP_PX
        row_height = max(row_height, height)
    return corners


def _pack_cell(patches: list[FacadeImage]) -> tuple[bytes, list[np.ndarray], int, float]:
    """One square image for the crops of a cell: ``(png, uvs, side_px, scale)``.

    The side is the smallest of 256, 512 and 1024 px that holds every crop at
    full size, else ``_CELL_SIDE``, where the crops shrink by 0.75 at a time.
    Each crop's UVs sit half a pixel inside its rectangle. Nothing is dropped:
    a set that does not fit raises :class:`PanoramaxError`.
    """
    if not patches:
        raise PanoramaxError("a decal cell needs at least one photograph")
    # Pasting onto the RGB canvas converts any other mode.
    images = [Image.open(io.BytesIO(patch.png)) for patch in patches]
    full = [(image.width, image.height) for image in images]
    plan: tuple[int, float, list[tuple[int, int]], list[tuple[int, int]]] | None = None
    # The full-size try at _CELL_SIDE is the first attempt below, so trying it here as well changes nothing.
    for side in [side for side in _CELL_SMALL_SIDES if side < _CELL_SIDE]:  # pragma: no mutate: see the comment above
        corners = _shelf(full, side)
        if corners is not None:
            plan = (side, 1.0, full, corners)
            break
    scale = 1.0
    for _attempt in range(_CELL_ATTEMPTS):
        if plan is not None:
            break
        sizes = [(max(1, int(width * scale)), max(1, int(height * scale))) for width, height in full]
        corners = _shelf(sizes, _CELL_SIDE)
        if corners is not None:
            plan = (_CELL_SIDE, scale, sizes, corners)
            break
        scale *= _CELL_SHRINK
    if plan is None:
        raise PanoramaxError(f"the facade photographs of one combine cell do not fit in {_CELL_SIDE} px")
    side, scale, sizes, corners = plan
    canvas = Image.new("RGB", (side, side))  # black between the crops
    uvs: list[np.ndarray] = []
    for index, image in enumerate(images):
        (width, height), (left, top) = sizes[index], corners[index]
        # Pillow returns a copy when the size is unchanged.
        canvas.paste(image.resize((width, height), Image.Resampling.LANCZOS), (left, top))
        u0 = (left + 0.5) / side
        u1 = (left + width - 0.5) / side
        v_top = 1.0 - (top + 0.5) / side
        v_bottom = 1.0 - (top + height - 0.5) / side
        uvs.append(np.array([[u0, v_bottom], [u1, v_bottom], [u1, v_top], [u0, v_top]], dtype=np.float32))
    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG")
    return buffer.getvalue(), uvs, side, scale


def coverage(faces: Sequence[Mapping[str, Any]], applied: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Exposed frontage and the part of it under an applied photo, in metres, per building and in all.

    Spans on one face are merged (overlapping or touching) and clipped to the
    face. A row for a face that is not in ``faces`` adds nothing.
    """
    lengths = {(str(face["building_id"]), int(face["segment_index"])): float(face["length_m"]) for face in faces}
    spans: dict[tuple[str, int], list[tuple[float, float]]] = {}
    for row in applied:
        key = (str(row["building_id"]), int(row["segment_index"]))
        if key not in lengths:
            continue
        s0 = max(0.0, float(row["s0_m"]))
        s1 = min(lengths[key], float(row["s1_m"]))
        if s1 > s0:  # pragma: no mutate: a zero-length span adds nothing to the merged length
            spans.setdefault(key, []).append((s0, s1))
    exposed: dict[str, float] = {}
    photo: dict[str, float] = {}
    for (building, _index), length in lengths.items():
        exposed[building] = exposed.get(building, 0.0) + length
        photo.setdefault(building, 0.0)
    for (building, _index), items in spans.items():
        photo[building] += _merged_length(items)
    rows = [
        {
            "building_id": building,
            "exposed_m": round(exposed[building], 3),
            "photo_m": round(photo[building], 3),
            "percent": _percent(photo[building], exposed[building]),
        }
        for building in sorted(exposed)
    ]
    total_exposed = sum(exposed.values())
    total_photo = sum(photo.values())
    return {
        "exposed_m": round(total_exposed, 3),
        "photo_m": round(total_photo, 3),
        "percent": _percent(total_photo, total_exposed),
        "buildings": rows,
    }


def _merged_length(spans: list[tuple[float, float]]) -> float:
    total = 0.0
    start, end = sorted(spans)[0]
    for s0, s1 in sorted(spans)[1:]:
        if s0 <= end:  # pragma: no mutate: touching spans give the same total merged or apart
            end = max(end, s1)
            continue
        total += end - start
        start, end = s0, s1
    return total + end - start


def _percent(part: float, whole: float) -> float | None:
    return round(100.0 * part / whole, 1) if whole > 0 else None


def tint_atlas_for(facades: Mapping[str, Any] | None, images: Mapping[str, bytes]) -> TintAtlas | str:
    """The trim atlas of the materials scene, or the reason no wall can be tinted.

    A materials scene baked from another style table is refused: its strips may
    not be where the current table puts them.
    """
    if not isinstance(facades, Mapping) or facades.get("mode") != "trim":
        return "no_trim_atlas"
    table = load_style_table()
    if facades.get("style_table_sha256") != table.sha256:
        raise PanoramaxError("the materials scene was built with another facade style table; rerun materials")
    style = str(facades.get("style"))
    entry = table.styles.get(style)
    png = images.get(trim_material(style))
    regional = None
    if "regional_table_sha256" in facades:
        # Baked from the opt-in regional recipes (plan D1): they must still be the ones the scene used.
        regional = load_regional_table(table)
        if facades["regional_table_sha256"] != regional.sha256:
            raise PanoramaxError("the materials scene was built with another regional facade table; rerun materials")
    atlas = None if entry is None else style_atlas(table, style, regional)[0]
    if atlas is None or png is None:
        return "no_trim_atlas"
    layout = trim_layout(atlas)
    with Image.open(io.BytesIO(png)) as image:
        if image.size != (layout.size, layout.height):
            return "layout_mismatch"
        rgb = np.asarray(image.convert("RGB"))
    trims = (trim_material(style), scenic_trim_material(style))
    return TintAtlas(
        style,
        layout,
        {str(wall): str(family) for wall, family in atlas["walls"].items()},
        strip_colours(rgb, layout),
        frozenset([*trims, *(party_material(name) for name in trims)]),
    )


def strip_colours(rgb: np.ndarray, layout: TrimLayout) -> dict[tuple[str, int], tuple[float, float, float]]:
    """Mean sRGB of each wall strip, inside its gutters, keyed by (wall, tone)."""
    found: dict[tuple[str, int], tuple[float, float, float]] = {}
    for wall, strips in sorted(layout.tones.items()):
        for tone, strip in enumerate(strips):
            mean = rgb[strip.row0 + layout.gutter : strip.row1 - layout.gutter].mean(axis=(0, 1))
            found[(wall, tone)] = (float(mean[0]), float(mean[1]), float(mean[2]))
    return found


def dominant_rgb(pngs: Sequence[bytes]) -> tuple[int, int, int]:
    """The per-channel median of every pixel of the crops: an outlier block does not move it, the order does not matter."""
    # numpy reads any negative size as "the rest".
    pixels = np.vstack([np.asarray(Image.open(io.BytesIO(png)).convert("RGB")).reshape(-1, 3) for png in pngs])  # pragma: no mutate: see the comment above
    median = np.median(pixels, axis=0)
    return (int(round(median[0])), int(round(median[1])), int(round(median[2])))


def nearest_strip(
    rgb: Sequence[float],
    colours: Mapping[tuple[str, int], Sequence[float]],
    families: Mapping[str, str],
    wall: str,
) -> tuple[str, int]:
    """The strip of ``wall``'s family (any tone) closest to ``rgb`` by squared sRGB distance; a tie goes to the first key."""
    family = families[wall]
    candidates = sorted(key for key in colours if families.get(key[0]) == family)

    target = np.asarray(rgb)

    def distance(key: tuple[str, int]) -> float:
        return float(np.sum((target - np.asarray(colours[key])) ** 2))

    return min(candidates, key=lambda key: (distance(key), key))


def _inside(v: np.ndarray, strip: Strip, layout: TrimLayout) -> np.ndarray:
    tolerance = _STRIP_TOLERANCE_PX / layout.height
    return np.asarray((v >= strip.v0 - tolerance) & (v <= strip.v1 + tolerance))


def current_strip(node: Node, atlas: TintAtlas) -> tuple[str, int] | None:
    """The wall strip most of the node's trim wall vertices sample, or None when none does."""
    counts: Counter[tuple[str, int]] = Counter()
    for prim in node.primitives:
        if prim.material not in atlas.materials or prim.uvs is None:
            continue
        for wall, strips in atlas.layout.tones.items():
            for tone, strip in enumerate(strips):
                counts[(wall, tone)] += int(_inside(prim.uvs[:, 1], strip, atlas.layout).sum())
    if not counts:
        return None
    best = min(counts, key=lambda key: (-counts[key], key))
    return best if counts[best] else None


def retint(node: Node, atlas: TintAtlas, source: tuple[str, int], target: tuple[str, int]) -> Node:
    """Move the V of the vertices inside the ``source`` strip to the same place in ``target``.

    Only the trim primitives change, and only their V inside the source strip:
    positions, indices, U, materials and the other strips stay. A triangle with
    corners inside and outside the source strip is refused.
    """
    layout = atlas.layout
    old = layout.tones[source[0]][source[1]]
    new = layout.tones[target[0]][target[1]]
    out: list[Primitive] = []
    for prim in node.primitives:
        if prim.material not in atlas.materials or prim.uvs is None:
            out.append(prim)
            continue
        uvs = prim.uvs.copy()
        v = uvs[:, 1]
        inside = _inside(v, old, layout)
        corners = inside[prim.mesh.triangles]
        if np.any(corners.any(axis=1) & ~corners.all(axis=1)):
            raise PanoramaxError(f"{node.name}: a {prim.material} triangle straddles the {source[0]} strip")
        moved = new.v0 + (v - old.v0) / (old.v1 - old.v0) * (new.v1 - new.v0)
        uvs[:, 1] = np.where(inside, moved, v)
        out.append(Primitive(prim.mesh, prim.material, uvs))
    return replace(node, primitives=tuple(out))


def tint_node(node: Node, patches: Sequence[Patch], atlas: TintAtlas | str) -> tuple[Node, dict[str, Any]]:
    """The node with its wall strip moved to the strip nearest its photo colour, and the record of that choice.

    The record is ``{"reason": ...}`` when the node keeps its strip. The colour is
    observed (source panoramax); no photo pixel enters the CC0 atlas.
    """
    if isinstance(atlas, str):
        return node, {"reason": atlas}
    if any(TINT_SEPARATOR in prim.material for prim in node.primitives):
        # ``detail`` marks a building whose tints all came from Overture colours (plan C2); any other tint is an OSM colour.
        overture = (node.extras or {}).get("tint_source") == "overture"
        return node, {"reason": "overture_colour" if overture else "osm_colour"}
    source = current_strip(node, atlas)
    if source is None:
        return node, {"reason": "no_wall_strip"}
    rgb = dominant_rgb([patch.image.png for patch in patches])
    target = nearest_strip(rgb, atlas.colours, atlas.families, source[0])
    if target == source:
        return node, {"reason": "unchanged"}
    record = {
        "rgb": list(rgb),
        "from": {"wall": source[0], "tone": source[1]},
        "to": {"wall": target[0], "tone": target[1]},
        "picture_ids": sorted({str(patch.note["picture_id"]) for patch in patches}),
        "authors": sorted({str(patch.note["author"]) for patch in patches}),
        "origin": "observed",
        "source": "panoramax",
    }
    return retint(node, atlas, source, target), record
