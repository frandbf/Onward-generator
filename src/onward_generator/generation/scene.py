"""Phase 03 scene assembly: geo scene + heights → visual meshes, colliders, manifest, report.

Inputs are the ``onward_generator.geo_scene`` and ``onward_generator.building_heights``
JSON contracts. Nothing here knows about Onward or Unity.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from shapely import MultiPolygon, Polygon, STRtree, make_valid, union_all
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from onward_generator import __version__
from onward_generator.contracts import is_schema
from onward_generator.generation.config import GenerationConfig
from onward_generator.generation.gltf import Node, Primitive
from onward_generator.generation.markings import (
    ZEBRA_BAR_M,
    ZEBRA_GAP_M,
    ZEBRA_LIFT_M,
    Markings,
    crossing_evidence,
    paint_crossings,
)
from onward_generator.generation.markings import summary as crossing_summary
from onward_generator.generation.mesh import (
    Mesh,
    cap,
    closed_manifold_defects,
    drop_degenerate_holes,
    merge,
    open_base_defects,
    open_point_contacts,
    polygons_of,
    prism,
    signed_volume,
    upward_area,
    walls,
)
from onward_generator.generation.surfaces import (
    AreaFeature,
    LinearWay,
    Partition,
    SurfaceClass,
    build_partition,
    carriageway_slab,
    crossing_sites,
    split_pedestrian_by_paving,
)
from onward_generator.geo.clipping import covered_by, inclusion_zone
from onward_generator.geo.projection import LocalFrame

SCENE_SCHEMA = "onward_generator.geo_scene"
HEIGHTS_SCHEMA = "onward_generator.building_heights"
MANIFEST_SCHEMA = "onward_generator.scene3d"
REPORT_SCHEMA = "onward_generator.generation_report"
SCHEMA_VERSION = "0.1.0"
MAX_INPUT_BYTES = 256 * 1024 * 1024
OVERLAP_CRITICAL_M2 = 0.5
GAP_WARNING_M2 = 0.5
DUPLICATE_SHARE = 0.95


class GenerationError(ValueError):
    """Inputs are missing, inconsistent or not in metres."""


@dataclass(frozen=True, slots=True)
class Issue:
    code: str
    severity: str  # critical | warning | info
    object_id: str | None
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "severity": self.severity, "object_id": self.object_id, "message": self.message}


@dataclass(slots=True)
class SceneObject:
    id: str
    type: str
    visual: list[Primitive]
    collider: Mesh | None
    record: dict[str, Any]


@dataclass(slots=True)
class GeneratedScene:
    objects: list[SceneObject]
    issues: list[Issue]
    partition: Partition
    inputs: dict[str, Any]
    attribution: list[dict[str, Any]]
    config: GenerationConfig
    stats: dict[str, Any] = field(default_factory=dict)
    ways: list[LinearWay] = field(default_factory=list)

    @property
    def critical(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "critical"]

    def visual_nodes(self) -> list[Node]:
        return [
            Node(o.id, tuple(o.visual), {"object_id": o.id, "type": o.type, "source_ids": o.record.get("source_ids", [])})
            for o in self.objects
            if o.visual
        ]

    def collision_nodes(self) -> list[Node]:
        return [
            Node(f"{o.id}#collider", (Primitive(o.collider, "collider"),), {"object_id": o.id, "type": o.type})
            for o in self.objects
            if o.collider is not None and o.collider.triangle_count
        ]

    def manifest(self) -> dict[str, Any]:
        return {
            "schema": MANIFEST_SCHEMA,
            "schema_version": SCHEMA_VERSION,
            "generator": {"name": "onward-generator", "version": __version__, "method": "deterministic extrusion and planar partition"},
            "inputs": self.inputs,
            "attribution": self.attribution,
            "frame": {
                **self.inputs["frame"],
                "gltf_axes": {"X": "east", "Y": "up", "Z": "south"},
                "gltf_from_local": "(x, y, z) -> (x, z, -y)",
            },
            "files": {"visual": "scene_visual.glb", "collision": "scene_collision.glb"},
            "objects": [o.record for o in self.objects],
        }

    def report(self) -> dict[str, Any]:
        severities = Counter(i.severity for i in self.issues)
        return {
            "schema": REPORT_SCHEMA,
            "schema_version": SCHEMA_VERSION,
            "inputs": self.inputs,
            "config": dict(self.config.document),
            "counts": self.stats,
            "checks": {
                "surface_overlap_m2": round(self.partition.overlap_area(), 4),
                "surface_gap_m2": round(self.partition.gap_area(), 4),
                "manifold_defects": sum(o.record.get("checks", {}).get("manifold_defects", 0) for o in self.objects),
            },
            "issues_by_code": dict(sorted(Counter(i.code for i in self.issues).items())),
            "severity": {k: severities.get(k, 0) for k in ("critical", "warning", "info")},
            "phase_03_exit": {
                "condition": "buildings and streets generated without critical topological errors (docs/06-generation.md, Checks)",
                "critical_errors": severities.get("critical", 0),
                "passed": severities.get("critical", 0) == 0,
            },
            "issues": [i.to_dict() for i in self.issues],
        }


def _read_json(path: Path, schema: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if not path.is_file():
        raise GenerationError(f"input not found: {path}")
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise GenerationError(f"{path} is larger than {MAX_INPUT_BYTES} bytes")
    raw = path.read_bytes()
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GenerationError(f"{path}: not UTF-8 JSON ({exc})") from exc
    if not isinstance(doc, dict) or not is_schema(doc.get("schema"), schema):
        raise GenerationError(f"{path}: expected schema {schema!r}")
    return doc, {"path": str(path.resolve()), "sha256": hashlib.sha256(raw).hexdigest(), "schema_version": doc.get("schema_version")}


def _site(scene: Mapping[str, Any]) -> Polygon:
    frame, aoi = scene["frame"], scene["area_of_interest"]
    local = LocalFrame.create(frame["origin_wgs84"][0], frame["origin_wgs84"][1], frame["crs"])
    if abs(local.origin_easting - frame["origin_projected"][0]) > 1e-3 or abs(local.origin_northing - frame["origin_projected"][1]) > 1e-3:
        raise GenerationError("scene frame origin does not reproject to its recorded projected origin")
    polygon = local.to_local(Polygon(aoi["polygon_wgs84"]))
    return inclusion_zone(polygon, float(aoi["margin_m"]))


def _match(tags: Mapping[str, str], rules: Mapping[str, frozenset[str]]) -> bool:
    return any(key in tags and (tags[key] in values or "*" in values) for key, values in rules.items())


def _width(feature: Mapping[str, Any], default: float) -> tuple[float, str]:
    attr = (feature.get("attributes") or {}).get("width_m")
    if isinstance(attr, Mapping) and isinstance(attr.get("value"), (int, float)) and attr["value"] > 0:
        return float(attr["value"]), str(attr.get("origin", "inferred"))
    return default, "config_default"


def generate_scene(scene_path: str | Path, heights_path: str | Path, config: GenerationConfig) -> GeneratedScene:
    scene, scene_ref = _read_json(Path(scene_path).expanduser(), SCENE_SCHEMA)
    heights, heights_ref = _read_json(Path(heights_path).expanduser(), HEIGHTS_SCHEMA)
    frame = scene.get("frame") or {}
    if frame.get("unit") != "m" or frame.get("coordinate_system") != "local_grid":
        raise GenerationError("scene must be in the local metric grid; refusing degrees")
    if heights.get("scene", {}).get("sha256") != scene_ref["sha256"]:
        raise GenerationError("heights.json was computed from a different geo scene (sha256 mismatch)")

    issues: list[Issue] = []
    site = _site(scene)
    features = sorted(scene.get("features", []), key=lambda f: f["id"])
    height_of = {b["building_id"]: b for b in heights.get("buildings", [])}

    # --- Buildings: footprints, duplicates, overlaps. ---
    footprints: list[tuple[Mapping[str, Any], BaseGeometry]] = []
    for f in features:
        if f.get("kind") != "building":
            continue
        geom = shape(f["geometry"])
        if not geom.is_valid:
            geom = union_all(polygons_of(make_valid(geom)))
            issues.append(Issue("footprint_repaired", "warning", f["id"], "invalid footprint repaired with make_valid"))
        if geom.is_empty or geom.area <= 0:
            issues.append(Issue("building_invalid_footprint", "critical", f["id"], "empty footprint after repair"))
            continue
        if not covered_by(site, geom):
            issues.append(Issue(
                "building_outside_map", "info", f["id"],
                "part of the footprint lies outside the map; the building is omitted",
            ))
            continue
        footprints.append((f, geom))

    skipped: set[str] = set()
    tree = STRtree([g for _, g in footprints])
    for i, (fi, gi) in enumerate(footprints):
        for j in sorted(int(k) for k in tree.query(gi, predicate="intersects")):
            if j <= i:
                continue
            fj, gj = footprints[j]
            overlap = gi.intersection(gj).area
            if overlap <= 0:
                continue
            if overlap >= DUPLICATE_SHARE * max(gi.area, gj.area):
                skipped.add(fj["id"])
                issues.append(Issue("duplicate_building_skipped", "warning", fj["id"], f"same footprint as {fi['id']}; not generated twice"))
            elif overlap > config.overlap_warning_m2:
                issues.append(Issue("overlapping_buildings", "warning", fj["id"], f"overlaps {fi['id']} by {overlap:.1f} m²"))

    objects: list[SceneObject] = []
    kept_footprints: list[BaseGeometry] = []
    degenerate = 0
    for f, geom in footprints:
        fid = f["id"]
        if fid in skipped:
            continue
        h = height_of.get(fid)
        if h is None or not isinstance(h.get("height_m"), (int, float)) or not np.isfinite(h["height_m"]) or h["height_m"] <= 0:
            issues.append(Issue("building_missing_height", "critical", fid, "no positive height in heights.json"))
            continue
        height = float(h["height_m"])
        wall_mesh, d1 = walls(geom, 0.0, height).drop_degenerate()
        roof_mesh, d2 = cap(geom, height).drop_degenerate()
        degenerate += d1 + d2
        visual_defects = open_base_defects(merge([wall_mesh, roof_mesh]), 0.0)
        roof_error = abs(upward_area(roof_mesh) - geom.area)
        collider_fp = make_valid(geom.simplify(config.collider_simplify_m, preserve_topology=True))
        collider_fp = union_all(polygons_of(collider_fp)) if not collider_fp.is_empty else geom
        collider = prism(collider_fp, 0.0, height, bottom=True)
        collider_defects = closed_manifold_defects(collider)
        volume = signed_volume(collider)
        expected = collider_fp.area * height
        if visual_defects:
            issues.append(Issue("building_mesh_not_manifold", "critical", fid, f"{visual_defects} open or non-manifold edges above the base"))
        if roof_error > max(0.01, 1e-3 * geom.area):
            issues.append(Issue("roof_area_mismatch", "critical", fid, f"roof triangulation differs from footprint by {roof_error:.3f} m²"))
        if collider_defects or abs(volume - expected) > 0.01 * expected:
            issues.append(Issue("collider_not_closed", "critical", fid, f"{collider_defects} defects; volume {volume:.1f} vs {expected:.1f} m³"))
        kept_footprints.append(geom)
        objects.append(
            SceneObject(
                id=f"building/{fid}",
                type="building",
                visual=[Primitive(wall_mesh, "building_wall"), Primitive(roof_mesh, "building_roof")],
                collider=collider,
                record={
                    "id": f"building/{fid}",
                    "type": "building",
                    "source_ids": [fid],
                    "source": f.get("source"),
                    "name": (f.get("tags") or {}).get("name"),
                    "generation": {"method": "footprint_extrusion", "roof": "flat", "generator_version": __version__},
                    "footprint_area_m2": round(geom.area, 3),
                    "height": {
                        "height_m": height,
                        "height_source": h.get("height_source"),
                        "height_class": h.get("height_class"),
                        "height_confidence": h.get("height_confidence"),
                        "estimated_levels": h.get("estimated_levels"),
                        "needs_manual_review": h.get("needs_manual_review"),
                    },
                    "geometry_repaired": bool(f.get("geometry_repaired")),
                    "materials": ["building_wall", "building_roof"],
                    "visual": {"triangles": wall_mesh.triangle_count + roof_mesh.triangle_count},
                    "collider": {
                        "triangles": collider.triangle_count,
                        "simplify_tolerance_m": config.collider_simplify_m,
                        "volume_m3": round(volume, 3),
                        "closed": collider_defects == 0,
                    },
                    "checks": {"manifold_defects": visual_defects + collider_defects},
                },
            )
        )

    # --- Streets and open areas. ---
    ways: list[LinearWay] = []
    areas: list[AreaFeature] = []
    ignored: Counter[str] = Counter()
    for f in features:
        kind, tags, geom = f.get("kind"), f.get("tags") or {}, shape(f["geometry"])
        if kind == "highway":
            value = tags.get("highway", "")
            road_class = "vehicular" if value in config.vehicular else "pedestrian" if value in config.pedestrian else None
            if road_class is None:
                ignored[f"highway={value}"] += 1
                continue
            paving = tags.get("surface")
            if geom.geom_type in ("Polygon", "MultiPolygon"):
                areas.append(AreaFeature(
                    f["id"],
                    SurfaceClass.PEDESTRIAN if road_class == "pedestrian" else SurfaceClass.CARRIAGEWAY,
                    geom,
                    paving,
                ))
                continue
            default = config.default_width_vehicular_m if road_class == "vehicular" else config.default_width_pedestrian_m
            width, origin = _width(f, default)
            ways.append(LinearWay(f["id"], road_class, geom, width, origin, paving))
        elif kind == "area":
            if _match(tags, config.surface_tags["water"]):
                areas.append(AreaFeature(f["id"], SurfaceClass.WATER, geom))
            elif _match(tags, config.surface_tags["green"]):
                areas.append(AreaFeature(f["id"], SurfaceClass.GREEN, geom))
            else:
                ignored["area:" + next(iter(sorted(tags)), "untagged")] += 1
        elif kind in ("line", "poi"):
            ignored[kind] += 1
    # Vehicular area polygons join the carriageway via their own buffer of 0.
    ways += [LinearWay(a.id, "vehicular", a.geometry, 0.0, "area") for a in areas if a.surface is SurfaceClass.CARRIAGEWAY]
    areas = [a for a in areas if a.surface is not SurfaceClass.CARRIAGEWAY]
    for key, n in sorted(ignored.items()):
        issues.append(Issue("feature_not_generated", "info", None, f"{n} × {key} not represented in phase 03 geometry"))

    site = make_valid(union_all([site, *kept_footprints]))
    site = max(polygons_of(site), key=lambda p: p.area) if site.geom_type != "Polygon" else site
    partition = build_partition(site, kept_footprints, ways, areas, config)
    depth = config.carriageway_depth_m

    def _emit_surface(surface_class: SurfaceClass, surface, object_id: str, material: str, extra: dict | None = None) -> None:
        nonlocal degenerate
        z = -depth if surface_class is SurfaceClass.CARRIAGEWAY else 0.0
        if surface.geometry.is_empty:
            return
        mesh, d = cap(surface.geometry, z).drop_degenerate()
        degenerate += d
        error = abs(upward_area(mesh) - surface.geometry.area)
        if error > max(0.05, 1e-4 * surface.geometry.area):
            issues.append(Issue("surface_triangulation_mismatch", "critical", object_id, f"{error:.3f} m² difference"))
        if surface.dropped_slivers:
            issues.append(Issue("slivers_dropped", "info", object_id, f"{surface.dropped_slivers} parts below {config.min_surface_area_m2} m²"))
        record = {
            "id": object_id,
            "type": "surface",
            "surface_class": str(surface_class),
            "z_m": z,
            "area_m2": round(surface.geometry.area, 3),
            "polygons": len(polygons_of(surface.geometry)),
            "source_ids": list(surface.sources),
            "generation": {
                "method": "planar_partition",
                "precedence": [str(s) for s in SurfaceClass],
                **({"sidewalk_width_m": config.sidewalk_width_m, "attribute_origin": "generated"} if surface_class is SurfaceClass.SIDEWALK else {}),
            },
            "materials": [material],
            "visual": {"triangles": mesh.triangle_count},
        }
        if extra:
            record.update(extra)
        objects.append(SceneObject(id=object_id, type="surface", visual=[Primitive(mesh, material)], collider=None, record=record))

    for surface_class, surface in partition.surfaces.items():
        if surface_class is SurfaceClass.PEDESTRIAN:
            rest, cobble = split_pedestrian_by_paving(surface, ways, areas, config)
            _emit_surface(SurfaceClass.PEDESTRIAN, rest, "surface/pedestrian", "pedestrian")
            if cobble is not None:
                _emit_surface(
                    SurfaceClass.PEDESTRIAN, cobble, "surface/pedestrian_cobble", "pedestrian",
                    extra={"paving": "cobble", "generation": {
                        "method": "planar_partition",
                        "precedence": [str(s) for s in SurfaceClass],
                        "paving_split": "osm_surface_sett_or_cobble",
                    }},
                )
            continue
        _emit_surface(surface_class, surface, f"surface/{surface_class}", str(surface_class))

    carriageway = partition.surfaces.get(SurfaceClass.CARRIAGEWAY)
    if carriageway is not None and depth > 0:
        curb, d = walls(carriageway.geometry, -depth, 0.0, inward=True).drop_degenerate()
        degenerate += d
        objects.append(
            SceneObject(
                "surface/curb", "curb", [Primitive(curb, "curb")], None,
                {"id": "surface/curb", "type": "curb", "height_m": depth, "source_ids": list(carriageway.sources),
                 "generation": {"method": "carriageway_boundary_walls", "attribute_origin": "generated"},
                 "materials": ["curb"], "visual": {"triangles": curb.triangle_count}},
            )
        )

    markings = Markings()
    if carriageway is not None:
        vehicular = [w for w in ways if w.road_class == "vehicular"]
        markings = paint_crossings(crossing_sites(vehicular), carriageway.geometry, crossing_evidence(features))
    if markings.bars:
        # One shared white material, lifted off the asphalt so the depth test keeps it in front.
        zebra = cap(MultiPolygon(markings.bars), -depth + ZEBRA_LIFT_M)
        painted = [c for c in markings.crossings if c.painted]
        objects.append(
            SceneObject(
                "surface/zebra_crossing", "surface", [Primitive(zebra, "road_marking")], None,
                {"id": "surface/zebra_crossing", "type": "surface", "surface_class": "road_marking",
                 "z_m": round(-depth + ZEBRA_LIFT_M, 4), "area_m2": round(sum(b.area for b in markings.bars), 3),
                 "source_ids": sorted({i for c in painted for i in c.evidence}),
                 "generation": {"method": "zebra_bars_on_osm_crossings", "attribute_origin": "observed", "bar_layout": "generated",
                                "bar_m": ZEBRA_BAR_M, "gap_m": ZEBRA_GAP_M, "lift_m": ZEBRA_LIFT_M},
                 "crossings": [
                     {"centre_m": [round(c.site.centre[0], 3), round(c.site.centre[1], 3)], "origin": c.origin,
                      "evidence": list(c.evidence), "painted": c.painted, "bars": len(c.bars)}
                     for c in markings.crossings
                 ],
                 "osm_crossings_unmatched": list(markings.unmatched),
                 "materials": ["road_marking"], "visual": {"triangles": zebra.triangle_count}},
            )
        )

    walkable = site.difference(carriageway.geometry) if carriageway is not None else site
    thickness = config.ground_thickness_m
    # Crossings are their own slab at road level. The adapter bakes every
    # collider named walkable, so they join the blocks on both kerbs (ADR-015).
    for cid, geom, top in (
        ("collider/walkable", walkable, 0.0),
        ("collider/carriageway", carriageway_slab(partition, config.precision_m), -depth),
        ("collider/walkable_crossing", partition.crossings, -depth),
    ):
        if geom.is_empty:
            continue
        # A zero-area spike is stored as a hole and would be extruded twice.
        # A hole that only touches the outer ring shares one vertex; open that
        # point so the slab weld does not count the corner twice.
        geom, dropped_holes = drop_degenerate_holes(geom)
        if dropped_holes:
            issues.append(Issue("degenerate_hole_dropped", "info", cid, f"{dropped_holes} holes below 0.0001 m² removed before the slab"))
        geom = open_point_contacts(geom)
        mesh = prism(geom, -thickness, top, bottom=True)
        defects = closed_manifold_defects(mesh)
        expected = geom.area * (top + thickness)
        volume = signed_volume(mesh)
        if defects or abs(volume - expected) > 0.01 * expected:
            issues.append(Issue("collider_not_closed", "critical", cid, f"{defects} defects; volume {volume:.1f} vs {expected:.1f} m³"))
        objects.append(
            SceneObject(
                cid, "ground_collider", [], mesh,
                {"id": cid, "type": "ground_collider", "top_z_m": top, "bottom_z_m": -thickness, "area_m2": round(geom.area, 3),
                 "generation": {"method": "closed_slab"}, "collider": {"triangles": mesh.triangle_count, "closed": defects == 0},
                 "checks": {"manifold_defects": defects}},
            )
        )

    overlap = partition.overlap_area()
    gap = partition.gap_area()
    if overlap > OVERLAP_CRITICAL_M2:
        issues.append(Issue("surface_overlap", "critical", None, f"surfaces overlap by {overlap:.2f} m²"))
    if gap > GAP_WARNING_M2:
        issues.append(Issue("surface_gap", "warning", None, f"{gap:.2f} m² of the site is not covered"))
    if degenerate:
        issues.append(Issue("degenerate_triangles_dropped", "info", None, f"{degenerate} zero-area triangles removed"))
    for o in objects:
        for prim in o.visual:
            if not np.isfinite(prim.mesh.vertices).all():
                issues.append(Issue("non_finite_vertices", "critical", o.id, "mesh has NaN or infinite coordinates"))

    visual_tris = Counter()
    for o in objects:
        for prim in o.visual:
            visual_tris[prim.material] += prim.mesh.triangle_count
    collider_meshes = [o.collider for o in objects if o.collider is not None]
    stats = {
        "objects": dict(sorted(Counter(o.type for o in objects).items())),
        "buildings_generated": sum(1 for o in objects if o.type == "building"),
        "buildings_skipped": len(skipped),
        "visual_triangles": int(sum(visual_tris.values())),
        "visual_triangles_by_material": dict(sorted(visual_tris.items())),
        "visual_vertices": int(sum(p.mesh.vertex_count for o in objects for p in o.visual)),
        "materials": len(visual_tris),
        "colliders": len(collider_meshes),
        "collision_triangles": int(sum(m.triangle_count for m in collider_meshes)),
        "site_area_m2": round(site.area, 3),
        "surface_area_m2": {str(k): round(s.geometry.area, 3) for k, s in partition.surfaces.items()},
        "building_footprint_area_m2": round(partition.buildings.intersection(site).area, 3),
        "streets": {"vehicular_ways": sum(w.road_class == "vehicular" for w in ways), "pedestrian_ways": sum(w.road_class == "pedestrian" for w in ways),
                    "width_origin": dict(sorted(Counter(w.width_origin for w in ways).items()))},
        "crossings": crossing_summary(markings),
        "height_sources": dict(sorted(Counter(o.record["height"]["height_source"] for o in objects if o.type == "building").items())),
    }

    attribution = [dict(scene.get("attribution") or {})]
    for extra in (heights.get("overrides") or {}).get("sources", []):
        attribution.append(dict(extra))
    inputs = {
        "geo_scene": scene_ref,
        "heights": heights_ref,
        "config": {"path": config.path, "sha256": config.sha256, "version": config.version},
        "frame": dict(frame),
    }
    return GeneratedScene(objects, issues, partition, inputs, attribution, config, stats, ways)
