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
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from shapely import union_all
from shapely.geometry import Polygon, shape

from onward_generator.detail.config import load_detail_config
from onward_generator.detail.facades import Segment, refine_exposure
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
from onward_generator.maps.workspace import MapError, load_map
from onward_generator.next_step import map_root
from onward_generator.panoramax.cache import PanoramaxError, cache_directory
from onward_generator.panoramax.match import (
    BuildingFootprint,
    StreetPicture,
    match_facades,
    picture_from_feature,
)
from onward_generator.panoramax.project import BASE_COLOR, FacadeImage, ProjectionError, project_facade

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


def apply_pictures(base: Path, out: Path, *, overwrite: bool = False) -> dict[str, Any]:
    """Write ``scene_panoramax`` from a materials directory and the local cache."""
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
    visual, report = _compose(
        source.read_bytes(),
        manifest,
        json.loads(geo_path.read_text(encoding="utf-8")),
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
