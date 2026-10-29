"""Copy a scene directory and add a closed collision barrier on the site ring.

The barrier is not added to the visual mesh.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry

from onward_generator.contracts import is_schema
from onward_generator.dress.geometry import compare_fingerprints, file_sha256, geometry_fingerprint
from onward_generator.generation.config import Material
from onward_generator.generation.gltf import Node, Primitive, glb_append, nodes_from_glb
from onward_generator.generation.mesh import polygons_of, prism
from onward_generator.geo.clipping import inclusion_zone
from onward_generator.geo.projection import LocalFrame
from onward_generator.io import atomic_write_bytes, atomic_write_json, prepare_outputs

WALL_ID = "envelope/perimeter_wall"
WALL_MATERIAL = "envelope_wall"
DEFAULT_HEIGHT_M = 12.0
DEFAULT_THICKNESS_M = 0.4
GEOGRAPHIC = frozenset({"building", "surface", "curb", "landmark_part"})


class BoundsError(ValueError):
    """The input scene cannot receive a playable envelope."""


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise BoundsError(f"missing {path}")
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise BoundsError(f"{path}: expected a JSON object")
    return doc


def _detect(base: Path) -> tuple[str, dict[str, Any]]:
    detail = base / "scene_detail.json"
    scene3d = base / "scene3d.json"
    if detail.is_file():
        return "detail", _load_json(detail)
    if scene3d.is_file():
        return "generate", _load_json(scene3d)
    raise BoundsError(f"{base}: need scene_detail.json or scene3d.json")


def site_polygon(manifest: Mapping[str, Any]) -> Polygon:
    """Inclusion zone (AOI + margin) in local metres. Never treats degrees as metres."""
    geo_ref = (manifest.get("inputs") or {}).get("geo_scene") or {}
    path = Path(str(geo_ref.get("path") or ""))
    if not path.is_file():
        raise BoundsError("manifest has no readable inputs.geo_scene.path")
    scene = _load_json(path)
    if not is_schema(scene.get("schema"), "onward_generator.geo_scene"):
        raise BoundsError(f"{path}: not a geo_scene")
    if file_sha256(path) != geo_ref.get("sha256"):
        raise BoundsError(f"{path}: sha256 does not match the scene manifest")
    frame = scene.get("frame") or {}
    if frame.get("unit") != "m" or frame.get("coordinate_system") != "local_grid":
        raise BoundsError("geo scene must be in the local metric grid; refusing degrees")
    aoi = scene["area_of_interest"]
    local = LocalFrame.create(frame["origin_wgs84"][0], frame["origin_wgs84"][1], frame["crs"])
    polygon = local.to_local(Polygon(aoi["polygon_wgs84"]))
    return inclusion_zone(polygon, float(aoi["margin_m"]))


def wall_footprint(site: BaseGeometry, thickness_m: float) -> BaseGeometry:
    if thickness_m <= 0 or thickness_m > 5:
        raise BoundsError("thickness_m must be in (0, 5] metres")
    inner = site.buffer(-float(thickness_m), join_style="mitre")
    ring = site.difference(inner)
    if ring.is_empty or ring.area < 0.5:
        raise BoundsError("site is too small for a perimeter wall at this thickness")
    return ring


def wall_nodes(site: BaseGeometry, *, height_m: float, thickness_m: float) -> tuple[Node, Node]:
    if height_m < 2 or height_m > 40:
        raise BoundsError("height_m must be between 2 and 40 metres")
    footprint = wall_footprint(site, thickness_m)
    mesh = prism(footprint, 0.0, float(height_m), bottom=True)
    if mesh.triangle_count == 0:
        raise BoundsError("perimeter wall produced no triangles")
    extras = {"object_id": WALL_ID, "type": "envelope", "source_ids": []}
    collision = Node(f"{WALL_ID}#collider", (Primitive(mesh, "collider"),), extras)
    # The same mesh is returned as a visual node only so tests can measure it.
    # apply_bounds does not write it: the barrier is collision, not a concrete wall.
    visual = Node(WALL_ID, (Primitive(mesh, WALL_MATERIAL),), extras)
    return visual, collision


def _wall_record(height_m: float, thickness_m: float, site: BaseGeometry, triangles: int) -> dict[str, Any]:
    return {
        "id": WALL_ID,
        "type": "envelope",
        "attribute_origin": "generated",
        "generation": {"method": "playable_perimeter_wall"},
        "height_m": height_m,
        "thickness_m": thickness_m,
        "site_area_m2": round(float(site.area), 3),
        "rings": len(list(polygons_of(site))),
        "visual": {"triangles": 0},
        "collider": {"triangles": triangles, "closed": True},
        "visible": False,
    }


def apply_bounds(
    base_dir: str | Path,
    out_dir: str | Path,
    *,
    height_m: float = DEFAULT_HEIGHT_M,
    thickness_m: float = DEFAULT_THICKNESS_M,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Write a new directory: copied scene plus the perimeter wall. Inputs are not modified."""
    base = Path(base_dir).expanduser().resolve()
    kind, manifest = _detect(base)
    visual_path = base / "scene_visual.glb"
    collision_path = base / "scene_collision.glb"
    if not visual_path.is_file() or not collision_path.is_file():
        raise BoundsError(f"{base}: missing scene_visual.glb or scene_collision.glb")
    if any(o.get("id") == WALL_ID for o in (manifest.get("objects") or [])):
        raise BoundsError(f"{base}: already has {WALL_ID}")
    site = site_polygon(manifest)
    visual_wall, collision_wall = wall_nodes(site, height_m=height_m, thickness_m=thickness_m)
    tris = visual_wall.primitives[0].mesh.triangle_count

    names = ("scene_visual.glb", "scene_collision.glb", "bounds_report.json")
    if kind == "detail":
        names = names + ("scene_detail.json", "detail_report.json")
    else:
        names = names + ("scene3d.json", "generation_report.json")
    protected = (visual_path, collision_path, base / ("scene_detail.json" if kind == "detail" else "scene3d.json"))
    targets = prepare_outputs(out_dir, names, overwrite=overwrite, protected=protected)

    wall_mats = {"collider": Material((0.2, 0.8, 0.3, 1.0), 1.0)}
    atomic_write_bytes(targets[0], visual_path.read_bytes())
    atomic_write_bytes(targets[1], glb_append(collision_path.read_bytes(), (collision_wall,), wall_mats))

    objects = list(manifest.get("objects") or [])
    objects.append(_wall_record(height_m, thickness_m, site, tris))
    written = dict(manifest)
    written["objects"] = objects
    written.setdefault("envelope", {})
    written["envelope"] = {
        "id": WALL_ID, "height_m": height_m, "thickness_m": thickness_m,
        "source_sha256": file_sha256(protected[2]),
    }

    before = geometry_fingerprint([o for o in (manifest.get("objects") or []) if o.get("type") in GEOGRAPHIC])
    after = geometry_fingerprint([o for o in objects if o.get("type") in GEOGRAPHIC])
    compare = compare_fingerprints(before, after)
    if not compare["identical"]:
        raise BoundsError("geographic objects changed while adding the envelope")

    report = {
        "schema": "onward_generator.bounds_report",
        "schema_version": "0.1.0",
        "base": {"dir": str(base), "kind": kind, "manifest_sha256": file_sha256(protected[2])},
        "wall": {"id": WALL_ID, "height_m": height_m, "thickness_m": thickness_m, "triangles": tris,
                 "site_area_m2": round(float(site.area), 3)},
        "geometry": compare,
        "note": "DamageVolume in the layout remains a fall-kill. The perimeter is a collision barrier and is not drawn.",
    }

    if kind == "detail":
        atomic_write_json(targets[3], written)
        detail_report = _load_json(base / "detail_report.json") if (base / "detail_report.json").is_file() else {}
        detail_report["bounds"] = report
        atomic_write_json(targets[4], detail_report)
    else:
        atomic_write_json(targets[3], written)
        gen_report = _load_json(base / "generation_report.json") if (base / "generation_report.json").is_file() else {}
        gen_report["bounds"] = report
        atomic_write_json(targets[4], gen_report)
    atomic_write_json(targets[2], report)

    vis_nodes, _ = nodes_from_glb(targets[0].read_bytes())
    if any(n.name == WALL_ID for n in vis_nodes):
        raise BoundsError("perimeter barrier was written into the visual mesh")
    col_nodes, _ = nodes_from_glb(targets[1].read_bytes())
    if not any(n.name == f"{WALL_ID}#collider" for n in col_nodes):
        raise BoundsError("written collision GLB is missing the perimeter barrier")
    return report
