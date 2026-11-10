"""``perf cover``: copy the playable scene and stand solid catalogue props on its long open sightlines (plan C7, off by default).

Reads the bounds output (``scene_playable``), the playable ``layout.json`` and the catalogue under
``assets``; writes a new directory (``scene_covered``) and changes none of its inputs. The new props
are visual nodes in ``scene_visual.glb`` only, their boxes in ``scene_collision.glb`` only, and one
``overgrowth`` record each in ``scene_detail.json`` with origin ``generated`` and op ``sightline_cover``.
``cover_report.json`` holds the gameplay headline before and after, the placements and the rejects;
``gameplay_report.json`` is the report on the new collision file. Every figure is estimated and none
blocks a stage (ADR-032). The directory is written whole or not at all.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import shapely
from shapely import LineString, Point, STRtree, union_all
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry

from onward_generator.bounds.envelope import WALL_ID
from onward_generator.dress.geometry import compare_fingerprints, geometry_fingerprint
from onward_generator.gameplay.cover import (
    MAX_PROP_LENGTH_M,
    TARGET_SPACING_M,
    Caps,
    CoverPlan,
    CoverSite,
    Placement,
    choose_cover,
    cover_props,
    note,
    triangle_cap,
)
from onward_generator.gameplay.metrics import GROUND_CONTACT_M, MAX_SIGHTLINE_M, SightCaster, sight_segments
from onward_generator.gameplay.report import (
    LAYOUT_SCHEMA,
    LAYOUT_VERSION,
    REPORT_FILE,
    build_report,
    open_sight_points,
    read_colliders,
    report_text,
)
from onward_generator.generation.config import Material
from onward_generator.generation.gltf import Node, Primitive, build_glb, glb_append, images_by_material, nodes_from_glb, read_glb
from onward_generator.generation.scene import GeneratedScene
from onward_generator.io import atomic_write_bytes, atomic_write_json, sha256_file
from onward_generator.overgrow.constants import MAX_ADDED_TRIS
from onward_generator.overgrow.dressing import load_spawns
from onward_generator.overgrow.fit import check_scene, collider_mesh, grounds_from_scene
from onward_generator.overgrow.ground import road_floor
from onward_generator.overgrow.library import read_library
from onward_generator.overgrow.library_catalog import library_directories
from onward_generator.overgrow.realize import fitted_props
from onward_generator.overgrow.urban import detail_elements
from onward_generator.overgrow.zone import Zone
from onward_generator.perf.layout import free_ground, nav_islands, playable_area

COVER_DIR = "scene_covered"
COVER_REPORT = "cover_report.json"
COVER_SCHEMA = "onward_generator.cover_report"
COVER_VERSION = "0.1.0"
OP = "sightline_cover"
GEOGRAPHIC = frozenset({"building", "surface", "curb", "landmark_part"})
ALLOWED_LICENCES = frozenset({"CC0-1.0", "CC-BY-4.0"})
"""The licences docs/legal/data-sources.md records for the street catalogues."""
CATALOGUE_LICENCES: Mapping[str, str] = {"kenney": "CC0-1.0"}
"""Catalogues whose rows carry no ``license``: each Kenney pack's ``License.txt`` is CC0 1.0 (docs/legal/data-sources.md)."""
COPIED = ("bounds_report.json",)
PROP_MATERIAL = Material((1, 1, 1, 1), 0.9)
"""A catalogue prop's material when the scene has none of that name: white under the albedo, as overgrow writes it."""
COLLIDER_MATERIAL = Material((0.45, 0.45, 0.45, 1), 0.9)
"""The material overgrow gives the collision boxes it appends."""
NOTE = (
    "Estimated. Cover props stand where the 2D gameplay report finds a route sample open past the proposed sightline; "
    "a covered sample stops counting as open even when the prop is lower than a sight blocker, so part of the gain is "
    "by definition. The navmesh Unity bakes is the real one (ADR-032, C7 amendment)."
)


class CoverError(ValueError):
    """The inputs cannot take sightline cover."""


@dataclass(frozen=True)
class CoverResult:
    plan: CoverPlan
    report: dict[str, Any]
    out: Path


def catalogue_licences(library: str | Path | None) -> dict[str, str]:
    """Each installed row's licence when it is one ``ALLOWED_LICENCES`` names: the row's ``license``, else its catalogue's."""
    if library is None:
        return {}
    found: dict[str, str] = {}
    for directory in library_directories(library):
        try:
            document = json.loads((directory / "manifest.json").read_bytes())
        except (OSError, json.JSONDecodeError):
            continue
        for row in document.get("assets") or []:
            if not isinstance(row, dict) or not row.get("id"):
                continue
            licence = row.get("license") or CATALOGUE_LICENCES.get(directory.name)
            if licence in ALLOWED_LICENCES:
                found[str(row["id"])] = str(licence)
    return found


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise CoverError(f"missing {path}")
    try:
        document = json.loads(path.read_bytes())
    except json.JSONDecodeError as exc:
        raise CoverError(f"{path}: not JSON ({exc})") from exc
    if not isinstance(document, dict):
        raise CoverError(f"{path}: expected a JSON object")
    return document


def _xy(value: object) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, Mapping) and "x" in item and "y" in item:
            points.append((float(item["x"]), float(item["y"])))
    return points


def _point(value: object) -> tuple[float, float] | None:
    if isinstance(value, list) and len(value) >= 2 and all(isinstance(v, int | float) for v in value[:2]):
        return (float(value[0]), float(value[1]))
    return None


def _doors(manifest: Mapping[str, Any]) -> list[tuple[float, float]]:
    """The interiors' street doors and every ground-floor facade door, as ``perf.layout.climb_keep_out`` reads them."""
    objects = manifest.get("objects") or []
    doors = [
        (float(connection["position_m"][0]), float(connection["position_m"][1]))
        for interior in objects if interior.get("type") == "interior"
        for connection in interior.get("connections", []) if connection.get("from") == "exterior"
    ]
    doors += [
        (float(door[0]), float(door[1]))
        for building in objects if building.get("type") == "building" for door in (building.get("facade") or {}).get("doors_m", [])
    ]
    return doors


def _anchors(layout: Mapping[str, Any]) -> list[tuple[float, float]]:
    found: list[tuple[float, float]] = []
    for block in ("attackers", "defenders", "attackers_b", "defenders_b"):
        value = layout.get(block)
        centre = _point(value.get("centre_m")) if isinstance(value, Mapping) else None
        if centre is not None:
            found.append(centre)
    uplink = _point(layout.get("uplink_m"))
    if uplink is not None:
        found.append(uplink)
    return found + _xy(layout.get("ai_m"))


def _zone(area: BaseGeometry) -> Zone:
    return Zone("playable", area if isinstance(area, Polygon) else shapely.box(*area.bounds))


def build_site(
    base: GeneratedScene, manifest: Mapping[str, Any], layout: Mapping[str, Any], layout_path: Path, geo_scene: Path, collision: Path,
) -> CoverSite:
    """The keep-outs and the ground of one playable scene, in local metres."""
    playable = playable_area(manifest)
    free = free_ground(base, [])  # pragma: no mutate: free_ground reads [] and None alike, as no furniture
    ground = union_all([free, base.partition.crossings])
    if playable is not None:
        free, ground = free.intersection(playable), ground.intersection(playable)
    islands = union_all(nav_islands(base, playable))
    buildings, lanes = grounds_from_scene(geo_scene)
    solids = [
        shapely.box(*collider.bounds) for collider in read_colliders(collision)
        if collider.kind == "prop" and collider.min_z <= GROUND_CONTACT_M
    ]
    climbs = [(float(site["x"]), float(site["y"])) for kind in ("ladders", "lifts") for site in layout.get(kind) or []]
    routes = [
        LineString([(float(p["x"]), float(p["y"])) for p in route.get("points") or []])
        for route in layout.get("routes") or [] if len(route.get("points") or []) >= 2
    ]
    return CoverSite(
        zone=_zone(playable if playable is not None else ground),
        buildings=buildings, lanes=lanes, lane_tree=STRtree(lanes) if lanes else None,
        spawns=load_spawns(layout_path), free=free, ground=ground, islands=islands, playable=playable,
        ammo=_xy(layout.get("ammo_m")), climbs=climbs, doors=_doors(manifest), routes=routes,
        urban=[Point(x, y).buffer(radius) for x, y, radius in detail_elements(manifest)],
        solids=solids, anchors=_anchors(layout),
    )


def _overgrow_triangles(nodes: Sequence[Node]) -> int:
    """Triangles of every ``overgrow/`` node in the visual, each copy counted: what overgrow added."""
    return sum(prim.mesh.triangle_count for node in nodes if node.name.startswith("overgrow/") for prim in node.primitives)


def _scene_assets(materials: Mapping[str, Material]) -> frozenset[str]:
    return frozenset(name.removeprefix("prop_").split("__")[0] for name in materials if name.startswith("prop_"))


def _check_inputs(detail: Path, manifest: Mapping[str, Any], layout: Mapping[str, Any], out: Path, overwrite: bool) -> None:
    if (manifest.get("envelope") or {}).get("id") != WALL_ID or not any(
        isinstance(o, Mapping) and o.get("id") == WALL_ID for o in manifest.get("objects") or []
    ):
        raise CoverError(f"{detail}: has no {WALL_ID}; cover reads the bounds output (scene_playable)")
    if any(isinstance(o, Mapping) and o.get("op") == OP for o in manifest.get("objects") or []):
        raise CoverError(f"{detail}: already holds {OP} props; cover reads the bounds output, not its own")
    if layout.get("schema") != LAYOUT_SCHEMA or layout.get("schema_version") != LAYOUT_VERSION:
        raise CoverError(
            f"cover reads a playable {LAYOUT_SCHEMA} {LAYOUT_VERSION} layout (perf layout --playable), "
            f"not {layout.get('schema')!r} {layout.get('schema_version')!r}"
        )
    for name in ("scene_visual.glb", "scene_collision.glb", "detail_report.json"):
        if not (detail / name).is_file():
            raise CoverError(f"{detail}: missing {name}")
    if out.resolve() == detail.resolve():
        raise CoverError("output must be a new directory")
    if out.exists() and not overwrite:
        raise CoverError(f"refusing to overwrite existing output {out}; pass --overwrite")


def _nodes(placements: Sequence[Placement]) -> tuple[list[Node], list[Node], dict[str, bytes]]:
    visual: list[Node] = []
    colliders: list[Node] = []
    images: dict[str, bytes] = {}
    for index, item in enumerate(placements):
        prop = item.prop.prop
        spec = prop.spec
        extras = {
            "type": "overgrowth", "origin": "generated", "kind": spec.category, "role": spec.role, "asset_id": spec.id,
            "op": OP, "anchor_id": f"sample:{item.target.point[0]:.2f},{item.target.point[1]:.2f}",
            "fit_scale": round(prop.fit_scale, 4),
            "size_m": [round(item.prop.length_m, 3), round(item.prop.depth_m, 3), round(item.prop.height_m, 3)],
            "crosswise": True, "solid": True,
        }
        visual.append(Node(f"overgrow/prop/{spec.id}/cover/{index}", prop.primitives(), extras, item.matrix, f"cover:{spec.id}"))
        colliders.append(Node(
            f"overgrow/collider/prop/{spec.id}/cover/{index}",
            (Primitive(collider_mesh(prop.mesh, spec.role), "collider"),),
            {"type": "collision", "origin": "generated", "asset_id": spec.id, "role": spec.role, "op": OP},
            item.matrix,
        ))
        images.update(prop.images())
    return visual, colliders, images


def _records(placements: Sequence[Placement]) -> list[dict[str, Any]]:
    rows = []
    for index, item in enumerate(placements):
        spec = item.prop.prop.spec
        x, y, z = (float(v) for v in item.matrix[:3, 3])
        rows.append({
            "id": f"overgrow/cover/{index}",
            "type": "overgrowth",
            "origin": "generated",
            "kind": spec.category,
            "role": spec.role,
            "asset_id": spec.id,
            "licence": item.prop.licence,
            "source_ids": [],
            "op": OP,
            "position_m": [round(x, 3), round(y, 3), round(z, 3)],
            "yaw_deg": round(math.degrees(item.spot.yaw), 2),
            "size_m": [round(item.prop.length_m, 3), round(item.prop.depth_m, 3), round(item.prop.height_m, 3)],
            "solid": True,
            "crosswise": True,
            "reason": {
                "sample_m": list(item.target.point), "ray_to_m": list(item.target.ray_to_m), "ray_length_m": item.target.length_m,
            },
        })
    return rows


def _stretches(report: Mapping[str, Any]) -> dict[str, Any]:
    stretches = [s for pair in report.get("pairs") or [] for route in pair["routes"] for s in route["stretches"]]
    return {
        "headline": dict(report.get("headline") or {}),
        "status": report.get("status"),
        "stretches": len(stretches),
        "uncovered_stretches": sum(1 for s in stretches if s["cover"] == 0),
        "stretches_over_sightline": sum(
            1 for s in stretches if s["longest_open_sight_m"] is not None and s["longest_open_sight_m"] > MAX_SIGHTLINE_M
        ),
        "cover_colliders": (report.get("colliders") or {}).get("cover"),
    }


def _rays_cut(before: SightCaster, after: SightCaster, point: tuple[float, float]) -> int:
    return int(np.count_nonzero(after.lengths(point) < before.lengths(point) - 1e-6))  # pragma: no mutate: equal only for a ray shortened by exactly 1 micrometre


def apply_cover(
    base: GeneratedScene,
    geo_scene: str | Path,
    detail_dir: str | Path,
    layout_path: str | Path,
    out_dir: str | Path,
    *,
    library: str | Path | None,
    overwrite: bool = False,
) -> CoverResult:
    """Write ``out_dir``: the playable scene with cover props, or a verbatim copy with none. Inputs are not modified."""
    detail = Path(detail_dir).expanduser().resolve()
    out = Path(out_dir).expanduser().resolve()
    layout_file = Path(layout_path).expanduser().resolve()
    scene_file = Path(geo_scene).expanduser().resolve()
    manifest = _load_json(detail / "scene_detail.json")
    layout = _load_json(layout_file)
    _check_inputs(detail, manifest, layout, out, overwrite)
    visual_path, collision_path = detail / "scene_visual.glb", detail / "scene_collision.glb"
    visual_bytes = visual_path.read_bytes()
    nodes, materials = nodes_from_glb(visual_bytes)
    colliders = read_colliders(collision_path)
    islands = nav_islands(base, playable_area(manifest))
    before_report = build_report(islands, base.partition.buildings, colliders, layout)
    targets = open_sight_points(islands, base.partition.buildings, colliders, layout)
    props, _library_rejects = read_library(library)
    candidates, refused = cover_props(fitted_props(props), catalogue_licences(library), _scene_assets(materials))
    overgrown = _overgrow_triangles(nodes)
    caps = Caps(triangles=triangle_cap(overgrown))
    site = build_site(base, manifest, layout, layout_file, scene_file, collision_path)
    plan = choose_cover(
        targets, candidates, site, caps,
        floor=road_floor(nodes),  # pragma: no mutate: cover stands off the carriageway, where the road floor is z = 0 as without one
    )

    out.parent.mkdir(parents=True, exist_ok=True)
    # Beside the output, so the final rename stays on one file system; the name never outlives the call.
    work = Path(tempfile.mkdtemp(dir=out.parent, prefix=f".{out.name}.", suffix=".tmp"))  # pragma: no mutate: tests write on one file system, and the name is not kept
    try:
        written = _write(work, detail, manifest, plan, nodes, materials, visual_bytes, scene_file)
        after_colliders = read_colliders(work / "scene_collision.glb")
        after_report = build_report(islands, base.partition.buildings, after_colliders, layout)
        after_report["layout_sha256"] = sha256_file(layout_file)
        atomic_write_bytes(work / REPORT_FILE, report_text(after_report).encode("utf-8"))
        before_caster = SightCaster(sight_segments(base.partition.buildings, colliders))
        after_caster = SightCaster(sight_segments(base.partition.buildings, after_colliders))
        report = {
            "schema": COVER_SCHEMA,
            "schema_version": COVER_VERSION,
            "provenance": "estimated",
            "blocking": False,
            "note": NOTE,
            "inputs": {
                "detail_manifest_sha256": sha256_file(detail / "scene_detail.json"),
                "visual_sha256": sha256_file(visual_path),
                "collision_sha256": sha256_file(collision_path),
                "layout_sha256": sha256_file(layout_file),
            },
            "parameters": {
                "max_sightline_m": MAX_SIGHTLINE_M, "target_spacing_m": TARGET_SPACING_M, "max_prop_length_m": MAX_PROP_LENGTH_M,
                "max_added_tris": MAX_ADDED_TRIS,
            },
            "caps": {"props": plan.caps.props, "triangles": plan.caps.triangles, "draws": plan.caps.draws,
                     "new_assets": plan.caps.new_assets, "overgrow_triangles": overgrown},
            "keep_outs": {
                "spawn_points": len(site.spawns), "ammo": len(site.ammo), "climbs": len(site.climbs), "doors": len(site.doors),
                "hunt_routes": len(site.routes), "urban_elements": len(site.urban), "solids": len(site.solids),
            },
            "targets": plan.targets,
            "candidates": [item.asset_id for item in candidates],
            "refused_props": refused,
            "placed": [
                {**record, "triangles": item.prop.triangles, "primitives": item.prop.parts,
                 "rays_cut": _rays_cut(before_caster, after_caster, item.target.point)}
                for record, item in zip(written, plan.placements, strict=True)  # pragma: no mutate: _records makes one row per placement
            ],
            "rejects": plan.rejects,
            "nothing_placed": plan.reason,
            "added": {"props": len(plan.placements), "triangles": plan.triangles, "primitives": plan.draws, "new_assets": plan.new_assets},
            "before": _stretches(before_report),
            "after": _stretches(after_report),
        }
        atomic_write_bytes(work / COVER_REPORT, report_text(report).encode("utf-8"))
        _swap(work, out)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)  # pragma: no mutate: the work directory exists here, so removing it raises nothing to ignore
        raise
    return CoverResult(plan, report, out)


def _write(
    work: Path, detail: Path, manifest: Mapping[str, Any], plan: CoverPlan,
    nodes: list[Node], materials: dict[str, Material], visual_bytes: bytes, scene_file: Path,
) -> list[dict[str, Any]]:
    """The scene files in ``work``; returns the new manifest records. A refused scene raises CoverError."""
    for name in COPIED:
        if (detail / name).is_file():
            shutil.copyfile(detail / name, work / name)
    if not plan.placements:
        for name in ("scene_visual.glb", "scene_collision.glb", "scene_detail.json", "detail_report.json"):
            shutil.copyfile(detail / name, work / name)
        return []
    visual_nodes, collider_nodes, images = _nodes(plan.placements)
    all_images = images_by_material(visual_bytes)
    all_images.update(images)
    merged = dict(materials)
    for name in images:
        merged.setdefault(name, PROP_MATERIAL)
    extras = read_glb(visual_bytes)[0].get("extras")
    atomic_write_bytes(work / "scene_visual.glb", build_glb(
        [*nodes, *visual_nodes], merged, extras=extras, repeat_images=all_images, preserve_uvs=True,
    ))
    atomic_write_bytes(work / "scene_collision.glb", glb_append(
        (detail / "scene_collision.glb").read_bytes(), collider_nodes, {"collider": COLLIDER_MATERIAL},
    ))
    records = _records(plan.placements)
    objects = [dict(o) for o in manifest.get("objects") or [] if isinstance(o, Mapping)]
    before = geometry_fingerprint([o for o in objects if o.get("type") in GEOGRAPHIC])
    objects.extend(records)
    after = geometry_fingerprint([o for o in objects if o.get("type") in GEOGRAPHIC])
    compare = compare_fingerprints(before, after)
    if not compare["identical"]:  # pragma: no mutate: a guard; the records added are overgrowth, never a geographic type
        raise CoverError("cover changed a geographic object record; this is a bug")  # pragma: no mutate: unreachable guard
    # overgrow fit reads the urban elements and shop signs of the manifest beside the visual.
    shutil.copyfile(detail / "scene_detail.json", work / "scene_detail.json")
    checked, problems = check_scene(work, scene_file)
    if problems:
        raise CoverError("the covered scene fails overgrow fit: " + "; ".join(problems[:10]))
    visual_sha = sha256_file(work / "scene_visual.glb")
    envelope = dict(manifest.get("envelope") or {})
    envelope["fit"] = {"checked": checked, "visual_sha256": visual_sha}
    atomic_write_json(work / "scene_detail.json", {**manifest, "objects": objects, "envelope": envelope})
    detail_report = _load_json(detail / "detail_report.json")
    detail_report["cover"] = {"op": OP, "count": len(records), "geometry": compare, "added_triangles": plan.triangles}
    atomic_write_json(work / "detail_report.json", detail_report)
    vis, _ = nodes_from_glb((work / "scene_visual.glb").read_bytes())
    col, _ = nodes_from_glb((work / "scene_collision.glb").read_bytes())
    # Guards against a bug: _nodes names the two node lists apart, and glb_append keeps every existing node.
    if any(node.name.startswith("overgrow/collider/") for node in vis) or any(node.name.startswith("overgrow/prop/") for node in col):  # pragma: no mutate: unreachable guard
        raise CoverError("a cover collider reached the visual mesh or a visual node the collision mesh; this is a bug")  # pragma: no mutate: unreachable guard
    if not any(node.name == f"{WALL_ID}#collider" for node in col):  # pragma: no mutate: unreachable guard; _check_inputs saw the wall
        raise CoverError("the covered collision GLB lost the perimeter wall; this is a bug")  # pragma: no mutate: unreachable guard
    return records


def _swap(work: Path, out: Path) -> None:
    """Move ``work`` to ``out`` in one rename, or file by file over an existing ``out`` (``--overwrite``)."""
    if not out.exists():
        os.replace(work, out)
        return
    for path in sorted(work.iterdir()):
        os.replace(path, out / path.name)
    work.rmdir()


def cover_note(result: CoverResult) -> list[str]:
    """The lines ``perf cover`` prints: the plan, then the headline before and after."""
    before, after = result.report["before"], result.report["after"]
    sight = (before["headline"].get("longest_open_sight_m"), after["headline"].get("longest_open_sight_m"))
    return [
        note(result.plan),
        f"cover: sight {sight[0]} -> {sight[1]} m, uncovered stretches {before['uncovered_stretches']} -> {after['uncovered_stretches']}, "
        f"over {MAX_SIGHTLINE_M:g} m {before['stretches_over_sightline']} -> {after['stretches_over_sightline']} (estimated, report only)",
    ]


__all__ = ["COVER_DIR", "COVER_REPORT", "CoverError", "CoverResult", "apply_cover", "catalogue_licences", "cover_note"]
