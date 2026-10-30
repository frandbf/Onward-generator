"""Grow an abandoned city on a copy of a dressed scene.

OSM supplies the ground: building footprints and the drive lane. Grass,
trees and wrecks are generated and labelled as such, except a tree that comes
from an OSM point. Solid props also append a box to the collision mesh.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from shapely import LineString, Point, Polygon, STRtree
from shapely.geometry import shape
from shapely.geometry.polygon import orient

from onward_generator import __version__
from onward_generator.config.loader import MAX_SCENE_BYTES, ConfigLoadError, read_json_document
from onward_generator.detail.facades import stable_unit
from onward_generator.detail.modules import yaw_matrix
from onward_generator.dress.geometry import compare_fingerprints, file_sha256, geometry_fingerprint
from onward_generator.generation.config import Material
from onward_generator.generation.gltf import Node, Primitive, build_glb, glb_append, images_by_material, nodes_from_glb
from onward_generator.generation.mesh import Mesh, merge, transform
from onward_generator.io import atomic_write_bytes, atomic_write_json, prepare_outputs
from onward_generator.maps.workspace import MapError, load_map
from onward_generator.next_step import map_root
from onward_generator.overgrow.ambient import cross_yaw, plan_from_brief
from onward_generator.overgrow.constants import (
    LANE_M,
    MAX_ADDED_TRIS,
    MAX_LIBRARY_TRIS,
    MAX_TREES,
    MAX_TUFTS,
    MAX_WRECKS,
    SEED,
)
from onward_generator.overgrow.dressing import block_reason, collider_mesh, dress, keep, load_spawns
from onward_generator.overgrow.fit import object_reason
from onward_generator.overgrow.ground import road_floor
from onward_generator.overgrow.growth import (
    canopy_png,
    grass_png,
    grass_tuft,
    rust_png,
    tree_parts,
    trunk_png,
    vine_png,
    wreck_mesh,
)
from onward_generator.overgrow.library import read_library
from onward_generator.overgrow.library_catalog import read_selection
from onward_generator.overgrow.profiles import profile_for, scaled_cap
from onward_generator.overgrow.realize import Site, compose, load_replay
from onward_generator.overgrow.zone import Zone, default_block_zone, radius_zone, zone_from_file

GEOGRAPHIC = frozenset({"building", "surface", "curb", "landmark_part"})
SKIP_HIGHWAYS = frozenset({
    "footway", "path", "cycleway", "steps", "corridor", "elevator",
    "platform", "raceway", "bridleway", "proposed", "construction",
    "service", "busway", "bus_guideway",
})
# Radius each procedural object keeps clear of a sidewalk manhole cover.
OCCUPIED_M = {"wreck": 3.0, "tree": 1.5, "grass": 0.8}
OUTPUT_FILES = (
    "scene_visual.glb",
    "scene_collision.glb",
    "scene_detail.json",
    "detail_report.json",
    "overgrow_report.json",
)


class OvergrowError(ValueError):
    """Overgrowth cannot be written on this scene."""


def overgrow(
    base_dir: str | Path,
    geo_scene: str | Path,
    out_dir: str | Path,
    *,
    overwrite: bool = False,
    profile: int = 100,
    seed: int = SEED,
    zone: Zone | None = None,
    zone_radius_m: float | None = None,
    zone_polygon: str | Path | None = None,
    library: str | Path | None = None,
    layout: str | Path | None = None,
    selection: str | Path | None = None,
    brief: str | None = None,
    ai: bool | None = None,
    provider=None,
) -> dict[str, Any]:
    base = Path(base_dir).expanduser().resolve()
    scene_path = Path(geo_scene).expanduser().resolve()
    out = Path(out_dir).expanduser().resolve()
    if not (base / "scene_visual.glb").is_file() or not (base / "scene_detail.json").is_file():
        raise OvergrowError(f"{base}: needs scene_visual.glb and scene_detail.json")
    if out == base:
        raise OvergrowError("output must be a new directory")
    try:
        manifest, _ = read_json_document(base / "scene_detail.json", max_bytes=MAX_SCENE_BYTES)
        geo, _ = read_json_document(scene_path, max_bytes=MAX_SCENE_BYTES)
    except ConfigLoadError as exc:
        raise OvergrowError(str(exc)) from exc

    features = [f for f in (geo.get("features") or []) if isinstance(f, dict)]
    buildings, lanes = _grounds(features)
    lane_tree = STRtree(lanes) if lanes else None
    edges = _wall_edges(features)
    nodes, materials = nodes_from_glb((base / "scene_visual.glb").read_bytes())
    images = images_by_material((base / "scene_visual.glb").read_bytes())
    floor = road_floor(nodes)
    before = geometry_fingerprint([o for o in (manifest.get("objects") or []) if isinstance(o, dict) and o.get("type") in GEOGRAPHIC])

    years = profile_for(profile)
    area = _resolve_zone(zone, zone_radius_m, zone_polygon)
    spawns = load_spawns(layout if layout is not None else _layout_near(scene_path))
    props, library_rejects = read_library(library)
    chosen: list[str] | None = None
    if selection is not None:
        chosen = read_selection(selection)
        known = {prop.spec.id for prop in props}
        missing = [item for item in chosen if item not in known]
        if missing:
            raise OvergrowError(
                "selection names assets that are not installed in the library: " + ", ".join(missing)
            )
        props = [prop for prop in props if prop.spec.id in set(chosen)]
    stored_brief, stored_ai = _ambient_settings(base)
    use_ai = stored_ai if ai is None else ai
    brief_text = stored_brief if brief is None else brief
    # Keywords decide street blocking only when the placement plan is not used.
    plan = plan_from_brief(brief_text)
    tuft = grass_tuft()
    wreck = wreck_mesh()
    variants = [tree_parts(i) for i in range(3)]
    tree_tris = [sum(part.triangle_count for _, part in parts) for parts in variants]
    budget = _Budget(scaled_cap(MAX_ADDED_TRIS, 1.0))
    records: list[dict[str, Any]] = []
    rejects: list[dict[str, Any]] = []
    reject_counts: dict[str, int] = {}
    tuft_meshes: list[Mesh] = []
    extra_nodes: list[Node] = []
    tuft_cap = scaled_cap(MAX_TUFTS, years.grass)
    tree_cap = scaled_cap(MAX_TREES, years.trees)
    wreck_cap = scaled_cap(MAX_WRECKS, years.wrecks)

    for edge in edges:
        if len(tuft_meshes) >= tuft_cap or edge.length < 6.0:
            continue
        spot = edge.mid + edge.outward * 1.7
        reason = block_reason(
            float(spot[0]), float(spot[1]), zone=area, buildings=buildings,
            lane_tree=lane_tree, lanes=lanes, spawns=spawns,
        )
        if reason:
            _note(rejects, reject_counts, "grass", reason, spot)
            continue
        if not keep(seed, years.grass, edge.feature_id, edge.index, "tuft"):
            continue
        if not budget.take(tuft.triangle_count):
            break
        yaw = stable_unit(seed, f"{spot[0]:.2f}", f"{spot[1]:.2f}", "tuft") * 6.28
        z = floor.z(float(spot[0]), float(spot[1]))
        tuft_meshes.append(transform(tuft, yaw_matrix(float(spot[0]), float(spot[1]), z, yaw)))
        records.append(_record("grass", "generated", spot, None, z))

    tree_count = 0
    for feature in features:
        if tree_count >= tree_cap:
            break
        point = _tree_point(feature)
        if point is None:
            continue
        reason = block_reason(
            float(point[0]), float(point[1]), zone=area, buildings=buildings,
            lane_tree=lane_tree, lanes=lanes, spawns=spawns,
        )
        if reason:
            _note(rejects, reject_counts, "tree", reason, point)
            continue
        if not budget.take(tree_tris[0]):
            break
        z = floor.z(float(point[0]), float(point[1]))
        extra_nodes.append(_tree_node(point, variants[0], 0, observed=True, feature_id=str(feature.get("id") or ""), seed=seed, z=z))
        records.append(_record("tree", "observed", point, str(feature.get("id") or ""), z))
        tree_count += 1
    for edge in edges:
        if tree_count >= tree_cap or edge.length < 16.0:
            continue
        spot = edge.mid + edge.outward * 2.4
        reason = block_reason(
            float(spot[0]), float(spot[1]), zone=area, buildings=buildings,
            lane_tree=lane_tree, lanes=lanes, spawns=spawns,
        )
        if reason:
            _note(rejects, reject_counts, "tree", reason, spot)
            continue
        if not keep(seed, years.trees, edge.feature_id, edge.index, "tree"):
            continue
        variant = int(stable_unit(seed, edge.feature_id, edge.index, "tree") * 3)
        if not budget.take(tree_tris[variant]):
            break
        z = floor.z(float(spot[0]), float(spot[1]))
        extra_nodes.append(_tree_node(spot, variants[variant], variant, observed=False, feature_id=edge.feature_id, seed=seed, z=z))
        records.append(_record("tree", "generated", spot, edge.feature_id, z))
        tree_count += 1

    composed = None
    if use_ai:
        replay_path = out / "ambient_plan.json"
        replay = load_replay(replay_path.read_text(encoding="utf-8")) if replay_path.is_file() else None
        composed = compose(
            geo, props,
            Site(area, buildings, lanes, lane_tree, spawns, floor=floor),
            brief=brief_text, provider=provider, replay=replay,
        )
    use_plan = composed is not None and composed.ok
    wreck_count = 0
    wreck_colliders: list[Node] = []
    if not use_plan:
        for spot, yaw in _wreck_spots(lanes, buildings, seed, block_streets=plan.street_blocking):
            if wreck_count >= wreck_cap:
                break
            turn = cross_yaw("wreck", wreck_count, street_blocking=plan.street_blocking, on_street=True)
            z = floor.z(float(spot[0]), float(spot[1]))
            matrix = yaw_matrix(float(spot[0]), float(spot[1]), z, yaw + turn)
            reason = block_reason(
                float(spot[0]), float(spot[1]), zone=area, buildings=buildings,
                lane_tree=lane_tree, lanes=lanes, spawns=spawns,
                allow_lane=plan.street_blocking,
            ) or object_reason(role="wreck", mesh=wreck, matrix=matrix, buildings=buildings, lanes=lanes)
            if reason:
                _note(rejects, reject_counts, "wreck", reason, spot)
                continue
            if not keep(seed, years.wrecks, f"{spot[0]:.1f}", f"{spot[1]:.1f}", "wreck-keep"):
                continue
            if not budget.take(wreck.triangle_count):
                break
            extra_nodes.append(Node(
                f"overgrow/wreck/{wreck_count}",
                (Primitive(wreck, "abandoned_wreck"),),
                {"type": "overgrowth", "origin": "generated", "kind": "wreck", "crosswise": turn != 0.0},
                matrix,
                "overgrow:wreck",
            ))
            wreck_colliders.append(Node(
                f"overgrow/collider/wreck/{wreck_count}",
                (Primitive(collider_mesh(wreck), "collider"),),
                {"type": "collision", "origin": "generated", "role": "wreck"},
                matrix,
            ))
            row = _record("wreck", "generated", spot, None, z)
            if turn:
                row["crosswise"] = True
            records.append(row)
            wreck_count += 1
    planted = dress(
        [] if use_plan else props,
        edges, features, buildings, lanes, lane_tree, area, years, seed, spawns,
        grass_mesh=tuft, grass_left=max(0, tuft_cap - len(tuft_meshes)), triangle_budget=MAX_LIBRARY_TRIS,
        block_streets=False if use_plan else plan.street_blocking,
        placed_roles={"wreck": wreck_count},
        floor=floor,
        occupied=_occupied(records),
    )
    if use_plan and composed is not None:
        planted.nodes.extend(composed.dressing.nodes)
        planted.records.extend(composed.dressing.records)
        planted.colliders.extend(composed.dressing.colliders)
        planted.images.update(composed.dressing.images)
        planted.triangles += composed.dressing.triangles
        planted.placed_ids.extend(composed.dressing.placed_ids)
        planted.rejects.extend(composed.dressing.rejects)
        for key, count in composed.dressing.reject_counts.items():
            planted.reject_counts[key] = planted.reject_counts.get(key, 0) + count
    tuft_meshes.extend(planted.grass)
    records.extend(planted.records)
    rejects.extend(planted.rejects)
    for key, count in planted.reject_counts.items():
        reject_counts[key] = reject_counts.get(key, 0) + count
    extra_nodes.extend(planted.nodes)
    if planted.vines:
        extra_nodes.append(Node(
            "overgrow/vines",
            (Primitive(merge(planted.vines), "abandoned_vine"),),
            {"type": "overgrowth", "origin": "generated", "kind": "vine"},
        ))
    if tuft_meshes:
        extra_nodes.insert(0, Node(
            "overgrow/grass",
            (Primitive(merge(tuft_meshes), "abandoned_grass"),),
            {"type": "overgrowth", "origin": "generated", "kind": "grass"},
        ))

    images.setdefault("abandoned_grass", grass_png())
    images.setdefault("abandoned_wreck", rust_png())
    images.setdefault("abandoned_vine", vine_png())
    images.setdefault("tree_trunk", trunk_png())
    images.setdefault("tree_canopy", canopy_png())
    images.update(planted.images)
    materials.setdefault("abandoned_grass", Material((1, 1, 1, 1), 0.9))
    materials.setdefault("abandoned_wreck", Material((1, 1, 1, 1), 0.85))
    materials.setdefault("abandoned_vine", Material((1, 1, 1, 1), 0.9))
    materials.setdefault("tree_trunk", Material((1, 1, 1, 1), 0.9))
    materials.setdefault("tree_canopy", Material((1, 1, 1, 1), 0.8))
    for name in planted.images:
        materials.setdefault(name, Material((1, 1, 1, 1), 0.9))

    targets = prepare_outputs(
        out, OUTPUT_FILES, overwrite=overwrite,
        protected=(base / "scene_visual.glb", base / "scene_collision.glb", base / "scene_detail.json"),
    )
    visual = build_glb(
        [*nodes, *extra_nodes], materials,
        extras={"manifest": "scene_detail.json", "overgrow": len(records)},
        repeat_images=images, preserve_uvs=True,
        unit_uv_materials={"glass_dark", "glass_boarded", "glass_view", "door_wood"},
    )
    atomic_write_bytes(targets[0], visual)
    colliders = [*wreck_colliders, *planted.colliders]
    collision = base / "scene_collision.glb"
    atomic_write_bytes(targets[1], _collision_bytes(collision.read_bytes() if collision.is_file() else b"", colliders))
    if len(colliders) != wreck_count + sum(1 for row in planted.records if row.get("solid")):
        raise OvergrowError("a solid prop was placed without a collision box")
    objects = [dict(o) for o in (manifest.get("objects") or []) if isinstance(o, dict)]
    objects.extend(
        {
            "id": f"overgrow/{row['kind']}/{index}",
            "type": "overgrowth",
            "origin": row["origin"],
            "kind": row["kind"],
            "source_ids": [row["source_id"]] if row.get("source_id") else [],
            "position_m": row["position_m"],
            **({"asset_id": row["asset_id"]} if row.get("asset_id") else {}),
            **({"role": row["role"]} if row.get("role") else {}),
            **({"surface": row["surface"]} if row.get("surface") else {}),
            **({"crosswise": True} if row.get("crosswise") else {}),
        }
        for index, row in enumerate(records)
    )
    written = dict(manifest)
    written["objects"] = objects
    written["fidelity"] = {
        **dict(manifest.get("fidelity") or {}),
            "overgrow": "solid props append a collision box; bottles, plants and loose trash stay visual; OSM tree points stay observed",
    }
    atomic_write_json(targets[2], written)
    detail_report: dict[str, Any] = {}
    report_src = base / "detail_report.json"
    if report_src.is_file():
        detail_report = json.loads(report_src.read_text(encoding="utf-8"))
    after = geometry_fingerprint([o for o in objects if o.get("type") in GEOGRAPHIC])
    compare = compare_fingerprints(before, after)
    detail_report["overgrow"] = {"count": len(records), "geometry": compare, "added_triangles": budget.used}
    atomic_write_json(targets[3], detail_report)
    summary = {
        "schema": "onward_generator.overgrow",
        "schema_version": "0.2.0",
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "generator_version": __version__,
        "base_dir": str(base),
        "profile_years": years.years,
        "seed": seed,
        "zone": {"kind": area.kind, "key": area.stable_key()},
        # The facade moss skirt was removed; the key stays at 0 so the report keeps its shape.
        "moss": 0,
        "grass": len(tuft_meshes),
        "vines": len(planted.vines),
        "trees": tree_count,
        "trees_observed": sum(1 for row in records if row["kind"] == "tree" and row["origin"] == "observed"),
        "brief": plan.brief,
        "street_blocking": False if use_plan else plan.street_blocking,
        "ambient_origin": composed.origin if use_plan and composed is not None else plan.origin,
        "ambient_mode": "ai" if use_plan else "rules",
        "compose_actions": composed.applied if use_plan and composed is not None else 0,
        "compose_attempts": int(composed.record.get("attempts") or 0) if composed is not None else 0,
        "compose_fallback": None if composed is None or composed.ok else composed.reason,
        "colliders": len(colliders),
        "wrecks": wreck_count,
        "props": len(planted.placed_ids),
        "placed_assets": list(dict.fromkeys(planted.placed_ids)),
        "library_assets": len(props),
        "library_rejects": [reject.as_dict() for reject in library_rejects],
        "library_selection": chosen,
        "added_triangles": budget.used + planted.triangles,
        "procedural_triangles": budget.used,
        "library_triangles": planted.triangles,
        "max_added_triangles": MAX_ADDED_TRIS,
        "rejects": rejects[:80],
        "reject_counts": reject_counts,
        "geometry": compare,
        "input_visual_sha256": file_sha256(base / "scene_visual.glb"),
    }
    atomic_write_json(targets[4], summary)
    if composed is not None:
        atomic_write_json(out / "ambient_plan.json", composed.record)
    if not compare["identical"]:
        raise OvergrowError("overgrow changed a geographic object record; this is a bug")
    return summary


def _resolve_zone(zone: Zone | None, radius_m: float | None, polygon: str | Path | None) -> Zone:
    chosen = sum(item is not None for item in (zone, radius_m, polygon))
    if chosen > 1:
        raise OvergrowError("pass only one zone")
    if zone is not None:
        return zone
    if radius_m is not None:
        return radius_zone(radius_m)
    if polygon is not None:
        return zone_from_file(polygon)
    return default_block_zone()


def _layout_near(scene: Path) -> Path | None:
    root = map_root(scene)
    if root is None:
        return None
    candidate = root / "data" / "processed" / "layout" / "layout.json"
    return candidate if candidate.is_file() else None


def _occupied(records: list[dict[str, Any]]) -> list[tuple[float, float, float]]:
    """(x, y, radius) of each grass tuft, tree and wreck already placed, for the sidewalk covers."""
    return [(float(row["position_m"][0]), float(row["position_m"][1]), OCCUPIED_M[row["kind"]]) for row in records]


def _note(rejects: list[dict[str, Any]], counts: dict[str, int], kind: str, reason: str, point: np.ndarray) -> None:
    counts[reason] = counts.get(reason, 0) + 1
    if len(rejects) < 80:
        rejects.append({
            "asset_id": kind,
            "reason": reason,
            "kind": kind,
            "position_m": [round(float(point[0]), 3), round(float(point[1]), 3), 0.0],
        })


class _Budget:
    def __init__(self, limit: int) -> None:
        self.used = 0
        self.limit = limit

    def take(self, triangles: int) -> bool:
        if self.used + triangles > self.limit:
            return False
        self.used += triangles
        return True

    def give(self, triangles: int) -> None:
        self.used -= triangles


class _Edge:
    def __init__(self, feature_id: str, index: int, a: np.ndarray, b: np.ndarray, outward: np.ndarray, length: float) -> None:
        self.feature_id = feature_id
        self.index = index
        self.a = a
        self.b = b
        self.outward = outward
        self.length = length
        self.mid = (a + b) * 0.5


def _grounds(features: Sequence[Mapping[str, Any]]) -> tuple[STRtree, list[LineString]]:
    polygons = []
    lanes: list[LineString] = []
    for feature in features:
        geom = _geom(feature)
        if geom is None or geom.is_empty:
            continue
        kind = str(feature.get("kind") or "")
        if kind == "building" and geom.geom_type in {"Polygon", "MultiPolygon"}:
            polygons.extend(_polygons(geom))
        if kind == "highway":
            tags = feature.get("tags") or {}
            if str(tags.get("highway") or "") in SKIP_HIGHWAYS:
                continue
            if geom.geom_type == "LineString" and geom.length >= 8.0:
                lanes.append(geom)
            elif geom.geom_type == "MultiLineString":
                lanes.extend(part for part in geom.geoms if part.length >= 8.0)
    return STRtree(polygons or [Point(1.0e9, 1.0e9)]), lanes


def _wall_edges(features: Sequence[Mapping[str, Any]]) -> list[_Edge]:
    edges: list[tuple[float, _Edge]] = []
    for feature in features:
        if feature.get("kind") != "building":
            continue
        height = _height_m(feature.get("tags") or {})
        if height is not None and height < 2.0:
            continue
        geom = _geom(feature)
        if geom is None:
            continue
        for polygon in _polygons(geom):
            coords = [np.array(p[:2], dtype=np.float64) for p in polygon.exterior.coords]
            for index, (start, end) in enumerate(zip(coords, coords[1:])):
                edge = end - start
                length = float(np.linalg.norm(edge))
                if length < 2.5:
                    continue
                outward = np.array([edge[1], -edge[0]], dtype=np.float64) / length
                item = _Edge(str(feature.get("id") or ""), index, start, end, outward, length)
                edges.append((float(item.mid @ item.mid), item))
    edges.sort(key=lambda pair: (pair[0], pair[1].feature_id, pair[1].index))
    return [item for _, item in edges]


def _blocked(point: np.ndarray, buildings: STRtree, lanes: STRtree | None) -> bool:
    here = Point(float(point[0]), float(point[1]))
    if buildings.query(here, predicate="intersects").size:
        return True
    if lanes is None:
        return False
    return any(lanes.geometries[int(hit)].distance(here) < LANE_M for hit in lanes.query(here.buffer(LANE_M)))


def _tree_point(feature: Mapping[str, Any]) -> np.ndarray | None:
    tags = feature.get("tags") or {}
    if tags.get("natural") != "tree" and feature.get("kind") != "tree":
        return None
    geom = _geom(feature)
    if geom is None or geom.geom_type != "Point":
        return None
    return np.array([geom.x, geom.y], dtype=np.float64)


def _tree_node(point: np.ndarray, parts: tuple[tuple[str, Mesh], ...], variant: int, *, observed: bool, feature_id: str, seed: int, z: float = 0.0) -> Node:
    yaw = stable_unit(seed, f"{point[0]:.2f}", f"{point[1]:.2f}", "yaw") * 6.28
    return Node(
        f"overgrow/tree/{'osm' if observed else 'gen'}/{feature_id}",
        tuple(Primitive(mesh, material) for material, mesh in parts),
        {
            "type": "overgrowth",
            "origin": "observed" if observed else "generated",
            "kind": "tree",
            "source_ids": [feature_id] if observed else [],
        },
        yaw_matrix(float(point[0]), float(point[1]), z, yaw),
        f"overgrow:tree:{variant}",
    )


def _ambient_settings(base: Path) -> tuple[str, bool]:
    root = map_root(base)
    if root is None:
        return "", False
    try:
        document = load_map(root)
    except MapError:
        return "", False
    return str(document.get("brief") or ""), bool(document.get("ambient_ai"))


def _collision_bytes(existing: bytes, colliders: list[Node]) -> bytes:
    if not colliders:
        return existing
    materials = {"collider": Material((0.45, 0.45, 0.45, 1), 0.9)}
    try:
        return glb_append(existing, colliders, materials)
    except (ValueError, KeyError, IndexError, OSError):
        return build_glb(colliders, materials)


def _wreck_spots(lanes: Sequence[LineString], buildings: STRtree, seed: int, *, block_streets: bool = False) -> list[tuple[np.ndarray, float]]:
    spots: list[tuple[float, np.ndarray, float]] = []
    for line in lanes:
        distance = 40.0
        while distance < line.length:
            point = line.interpolate(distance)
            tangent = _tangent(line, distance)
            side = 1.0 if stable_unit(seed, f"{point.x:.1f}", f"{point.y:.1f}", "wreck") >= 0.5 else -1.0
            gap = 0.8 if block_streets else LANE_M + 1.6
            offset = np.array([-tangent[1], tangent[0]]) * side * gap
            spot = np.array([point.x, point.y]) + offset
            if not buildings.query(Point(float(spot[0]), float(spot[1])), predicate="intersects").size:
                yaw = float(np.arctan2(tangent[1], tangent[0]))
                spots.append((float(spot @ spot), spot, yaw))
            distance += 90.0
    spots.sort(key=lambda item: item[0])
    return [(spot, yaw) for _, spot, yaw in spots]


def _tangent(line: LineString, distance: float) -> np.ndarray:
    ahead = line.interpolate(min(line.length, distance + 2.0))
    behind = line.interpolate(max(0.0, distance - 2.0))
    vector = np.array([ahead.x - behind.x, ahead.y - behind.y], dtype=np.float64)
    length = float(np.linalg.norm(vector))
    if length < 1e-6:
        return np.array([1.0, 0.0])
    return vector / length


def _record(kind: str, origin: str, point: np.ndarray, source_id: str | None, z: float = 0.0) -> dict[str, Any]:
    return {
        "kind": kind,
        "origin": origin,
        "source_id": source_id,
        "position_m": [round(float(point[0]), 3), round(float(point[1]), 3), round(float(z), 3)],
    }


def _polygons(geom: Any) -> list[Polygon]:
    parts = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
    return [orient(part, sign=1.0) for part in parts if isinstance(part, Polygon) and not part.is_empty]


def _geom(feature: Mapping[str, Any]) -> Any:
    raw = feature.get("geometry") or {}
    if not raw.get("type"):
        return None
    return shape({"type": raw.get("type"), "coordinates": raw.get("coordinates")})


def _height_m(tags: Mapping[str, Any]) -> float | None:
    raw = tags.get("height") or tags.get("building:height")
    if raw not in (None, ""):
        try:
            return float(str(raw).split()[0].replace(",", "."))
        except ValueError:
            return None
    levels = tags.get("building:levels")
    if levels in (None, ""):
        return None
    try:
        return float(str(levels).split()[0]) * 3.0
    except ValueError:
        return None
