"""Copy a scene directory and add a closed collision barrier on the site ring.

The barrier is not added to the visual mesh. An optional backdrop ring outside it
is visual only (ADR-014).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient

from onward_generator.contracts import is_schema
from onward_generator.dress.atlas import encode_png
from onward_generator.dress.geometry import compare_fingerprints, file_sha256, geometry_fingerprint
from onward_generator.generation.config import Material
from onward_generator.generation.gltf import (
    Node,
    Primitive,
    build_glb,
    glb_append,
    images_by_material,
    nodes_from_glb,
    read_glb,
)
from onward_generator.generation.mesh import Mesh, polygons_of, prism
from onward_generator.geo.clipping import inclusion_zone
from onward_generator.geo.projection import LocalFrame
from onward_generator.io import atomic_write_bytes, atomic_write_json, prepare_outputs
from onward_generator.tiers import core_of

WALL_ID = "envelope/perimeter_wall"
WALL_MATERIAL = "envelope_wall"
DEFAULT_HEIGHT_M = 12.0
DEFAULT_THICKNESS_M = 0.4
OVERGROW_REPORT = "overgrow_report.json"
FIT_FILE = "fit.json"
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


def _fit_record(base: Path, visual_path: Path) -> dict[str, Any] | None:
    """What ``overgrow fit`` passed, for an overgrow output; ``None`` for a scene without overgrowth.

    A base that ``overgrow place`` wrote must carry the ``fit.json`` of that same ``scene_visual.glb``.
    """
    if not (base / OVERGROW_REPORT).is_file():
        return None
    path = base / FIT_FILE
    if not path.is_file():
        raise BoundsError(f"{base}: overgrow output without {FIT_FILE}; run overgrow fit first")
    fit = _load_json(path)
    if not is_schema(fit.get("schema"), "onward_generator.overgrow_fit") or fit.get("rejected") != []:
        raise BoundsError(f"{path}: not a passing overgrow fit")
    if fit.get("visual_sha256") != file_sha256(visual_path):
        raise BoundsError(f"{path}: checked another scene_visual.glb; run overgrow fit again")
    return {"checked": int(fit["checked"]), "visual_sha256": str(fit["visual_sha256"])}


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


# ---- Horizon backdrop (optional, docs/12-export.md) ------------------------------------------
BACKDROP_ID = "horizon/backdrop"
BACKDROP_MATERIAL = "horizon_backdrop"
DEFAULT_BACKDROP_OFFSET_M = 2.0
DEFAULT_BACKDROP_HEIGHT_M = 30.0
# Below the ground line, so no gap shows between the ground edge and the ring.
BACKDROP_BASE_Z_M = -1.0
# The simplified hull lies up to this far inside the true hull; the offset adds it back.
BACKDROP_SIMPLIFY_M = 1.0
# sRGB, near white. The ring is lit by the scene's probes and ambient light, so it shows far darker than its albedo:
# from #C8CDD2 and #D6E0EC (the default fog colour and a pale sky) it read as a slate wall on the headset.
HAZE_HEX = "#EEF1F4"
SKY_HEX = "#F7FAFE"
GRADIENT_ROWS = 64
BACKDROP_PNG_LEVEL = 6  # zlib default, which Pillow used before the level was named


def _rgb(hex_colour: str) -> np.ndarray:
    return np.array([int(hex_colour[k : k + 2], 16) for k in (1, 3, 5)])


def backdrop_png() -> bytes:
    """A vertical gradient, sky at the top row and haze at the bottom row. 4 px wide, no noise."""
    t = np.linspace(0.0, 1.0, GRADIENT_ROWS)[:, None]
    rows = np.rint(_rgb(SKY_HEX) * (1.0 - t) + _rgb(HAZE_HEX) * t).astype(np.uint8)
    return encode_png(np.repeat(rows[:, None, :], 4, axis=1), BACKDROP_PNG_LEVEL)


def backdrop_node(site: BaseGeometry, *, offset_m: float, height_m: float) -> Node:
    """One inward-facing quad per edge of the site's convex hull, ``offset_m`` outside it.

    Visual only: no collider, no building. V runs from the bottom row of the gradient
    (haze) to its top row (sky), at the centres of the first and last rows.
    """
    if not 0 < offset_m <= 200:
        raise BoundsError("backdrop offset_m must be in (0, 200] metres")
    if not 2 <= height_m <= 200:
        raise BoundsError("backdrop height_m must be between 2 and 200 metres")
    hull = site.convex_hull.simplify(BACKDROP_SIMPLIFY_M)
    ring = orient(hull.buffer(offset_m + BACKDROP_SIMPLIFY_M, join_style="mitre")).exterior.coords
    edges = list(pairwise(ring))
    z0, z1 = BACKDROP_BASE_Z_M, float(height_m)
    v0, v1 = 0.5 / GRADIENT_ROWS, 1.0 - 0.5 / GRADIENT_ROWS
    vertices: list[tuple[float, float, float]] = []
    uvs: list[tuple[float, float]] = []
    triangles: list[tuple[int, int, int]] = []
    for (ax, ay), (bx, by) in edges:
        # Counter-clockwise ring: b → a seen from inside, so the normal points into the site.
        base = len(vertices)
        vertices += [(bx, by, z0), (ax, ay, z0), (ax, ay, z1), (bx, by, z1)]
        uvs += [(0.5, v0), (0.5, v0), (0.5, v1), (0.5, v1)]
        triangles += [(base, base + 1, base + 2), (base, base + 2, base + 3)]
    mesh = Mesh(np.array(vertices), np.array(triangles))
    extras = {"object_id": BACKDROP_ID, "type": "backdrop", "attribute_origin": "generated", "source_ids": []}
    return Node(BACKDROP_ID, (Primitive(mesh, BACKDROP_MATERIAL, np.array(uvs)),), extras)


def with_backdrop(visual: bytes, node: Node) -> bytes:
    """The visual GLB with ``node`` appended. Every image, UV and node extra is kept."""
    nodes, materials = nodes_from_glb(visual)
    images = images_by_material(visual)
    images[BACKDROP_MATERIAL] = backdrop_png()
    materials[BACKDROP_MATERIAL] = Material((1.0, 1.0, 1.0, 1.0), 1.0)
    extras = read_glb(visual)[0].get("extras")
    return build_glb([*nodes, node], materials, extras=extras, repeat_images=images, preserve_uvs=True)


def _backdrop_record(node: Node, offset_m: float, height_m: float) -> dict[str, Any]:
    triangles = node.primitives[0].mesh.triangle_count
    return {
        "id": BACKDROP_ID,
        "type": "backdrop",
        "attribute_origin": "generated",
        "generation": {"method": "horizon_backdrop_ring"},
        "offset_m": offset_m,
        "height_m": height_m,
        "quads": triangles // 2,
        "material": BACKDROP_MATERIAL,
        "visual": {"triangles": triangles},
        "collider": {"triangles": 0},
        "lightmap": False,
        "visible": True,
    }


def wall_site(site: BaseGeometry, core: BaseGeometry | None) -> BaseGeometry:
    """Where the wall stands: the site, or the part of it inside the playable core (ADR-017)."""
    return site if core is None else site.intersection(core)


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
    backdrop: bool = False,
    backdrop_offset_m: float = DEFAULT_BACKDROP_OFFSET_M,
    backdrop_height_m: float = DEFAULT_BACKDROP_HEIGHT_M,
) -> dict[str, Any]:
    """Write a new directory: copied scene plus the perimeter wall, and the backdrop ring when asked. Inputs are not modified."""
    base = Path(base_dir).expanduser().resolve()
    kind, manifest = _detect(base)
    visual_path = base / "scene_visual.glb"
    collision_path = base / "scene_collision.glb"
    if not visual_path.is_file() or not collision_path.is_file():
        raise BoundsError(f"{base}: missing scene_visual.glb or scene_collision.glb")
    if any(o.get("id") == WALL_ID for o in (manifest.get("objects") or [])):
        raise BoundsError(f"{base}: already has {WALL_ID}")
    fit = _fit_record(base, visual_path)
    full_site = site_polygon(manifest)
    core = core_of(manifest)
    core_m = (manifest.get("core") or {}).get("half_side_m")
    site = wall_site(full_site, core)
    visual_wall, collision_wall = wall_nodes(site, height_m=height_m, thickness_m=thickness_m)
    tris = visual_wall.primitives[0].mesh.triangle_count
    # The backdrop stays at the site edge, past the scenic ring (ADR-014, ADR-017).
    ring = backdrop_node(full_site, offset_m=backdrop_offset_m, height_m=backdrop_height_m) if backdrop else None

    names = ("scene_visual.glb", "scene_collision.glb", "bounds_report.json")
    if kind == "detail":
        names = names + ("scene_detail.json", "detail_report.json")
    else:
        names = names + ("scene3d.json", "generation_report.json")
    protected = (visual_path, collision_path, base / ("scene_detail.json" if kind == "detail" else "scene3d.json"))
    targets = prepare_outputs(out_dir, names, overwrite=overwrite, protected=protected)

    wall_mats = {"collider": Material((0.2, 0.8, 0.3, 1.0), 1.0)}
    visual = visual_path.read_bytes()
    atomic_write_bytes(targets[0], visual if ring is None else with_backdrop(visual, ring))
    atomic_write_bytes(targets[1], glb_append(collision_path.read_bytes(), (collision_wall,), wall_mats))

    objects = list(manifest.get("objects") or [])
    objects.append(_wall_record(height_m, thickness_m, site, tris))
    ring_record = None if ring is None else _backdrop_record(ring, backdrop_offset_m, backdrop_height_m)
    if ring_record is not None:
        objects.append(ring_record)
    written = dict(manifest)
    written["objects"] = objects
    written.setdefault("envelope", {})
    written["envelope"] = {
        "id": WALL_ID, "height_m": height_m, "thickness_m": thickness_m,
        "source_sha256": file_sha256(protected[2]),
        "backdrop": ring_record,
        "fit": fit,
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
        "wall": {"id": WALL_ID, "height_m": height_m, "thickness_m": thickness_m, "triangles": tris, "core_half_side_m": core_m,
                 "site_area_m2": round(float(site.area), 3)},
        "backdrop": ring_record,
        "fit": fit,
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
