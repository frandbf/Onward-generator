"""Phase 04 assembly on top of the validated phase 03 scene.

The phase 03 scene is regenerated in memory from the same inputs and must match
the stored, validated ``scene3d.json`` exactly; phase 04 only adds detail to it.
Nothing here knows about Onward or Unity.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from shapely import Point, STRtree, union_all
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from onward_generator import __version__
from onward_generator.detail.config import DetailConfig, Template
from onward_generator.detail.facades import (
    Layout,
    Storeys,
    build_shell,
    cut_roof_deck,
    door_walls,
    ensure_door,
    exposed_frontage,
    interior_storey,
    layout_segment,
    refine_exposure,
    stable_unit,
    storeys_for,
    wall_uv_frame,
)
from onward_generator.detail.grammar import FacadeUse, choose_roof, choose_wall, facade_use, resolve_use
from onward_generator.detail.interiors import (
    build_interior,
    fits_triangle_budget,
    glass_penetrations,
    pierce_room_windows,
    plan_interior,
    rectangularity,
    room_window_cuts,
)
from onward_generator.detail.landmarks import attach_towers
from onward_generator.detail.modules import Prototype, urban_prototypes
from onward_generator.detail.urban import Element, Rejected, classify_poi, place_elements
from onward_generator.generation.config import GenerationConfig
from onward_generator.generation.gltf import Node, Primitive
from onward_generator.generation.mesh import Mesh, cap, closed_manifold_defects, merge, prism, signed_volume, walls
from onward_generator.generation.scene import GeneratedScene, Issue, generate_scene
from onward_generator.generation.surfaces import SurfaceClass

MANIFEST_SCHEMA = "onward_generator.scene_detail"
REPORT_SCHEMA = "onward_generator.detail_report"
SCHEMA_VERSION = "0.1.0"
BASE_MANIFEST = "scene3d.json"
BASE_REPORT = "generation_report.json"
FIDELITY_NOTE = "Generic template chosen by rules; it does not represent the real facade of this building."


class DetailError(ValueError):
    """The phase 03 base is missing, failed, or does not match the inputs."""


@dataclass(slots=True)
class DetailObject:
    id: str
    type: str
    visual: list[Primitive]
    colliders: list[Mesh]
    record: dict[str, Any]
    instance_of: str | None = None
    matrix: np.ndarray | None = None
    glass_colliders: list[Mesh] = field(default_factory=list)
    uv_frame: dict[str, Any] | None = None  # visual node extras only (ADR-012)
    light_anchors: dict[str, Any] | None = None  # visual node extras only (ADR-013)


@dataclass(slots=True)
class DetailedScene:
    base: GeneratedScene
    objects: list[DetailObject]
    prototypes: dict[str, Prototype]
    issues: list[Issue]
    config: DetailConfig
    inputs: dict[str, Any]
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def critical(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "critical"]

    def visual_nodes(self) -> list[Node]:
        nodes = []
        for o in self.objects:
            extras = {"object_id": o.id, "type": o.type, "source_ids": o.record.get("source_ids", [])}
            if o.uv_frame is not None:
                extras["uv_frame"] = o.uv_frame
            if o.light_anchors is not None:
                extras["light_anchors"] = o.light_anchors
            if o.instance_of is not None:
                proto = self.prototypes[o.instance_of]
                nodes.append(Node(o.id, tuple(Primitive(m, mat) for mat, m in proto.parts), extras, o.matrix, proto.key))
            elif o.visual:
                nodes.append(Node(o.id, tuple(o.visual), extras))
        return nodes

    def collision_nodes(self) -> list[Node]:
        nodes = []
        for o in self.objects:
            extras = {"object_id": o.id, "type": o.type}
            if o.instance_of is not None:
                proto = self.prototypes[o.instance_of]
                if proto.collider is not None:
                    nodes.append(Node(f"{o.id}#collider", (Primitive(proto.collider, "collider"),), extras, o.matrix, proto.key + "#collider"))
            elif o.colliders:
                nodes.append(Node(f"{o.id}#collider", tuple(Primitive(m, "collider") for m in o.colliders if m.triangle_count), extras))
            if o.instance_of is None and o.glass_colliders:
                panes = tuple(Primitive(m, "collider") for m in o.glass_colliders if m.triangle_count)
                if panes:
                    # ``#glass`` is the collision role. The primitive material stays ``collider``.
                    nodes.append(Node(f"{o.id}#glass", panes, extras))
        return nodes

    def manifest(self) -> dict[str, Any]:
        return {
            "schema": MANIFEST_SCHEMA,
            "schema_version": SCHEMA_VERSION,
            "level": "L2/L3",
            "generator": {"name": "onward-generator", "version": __version__, "method": "rule-based generic facade templates on phase 03 volumes",
                          "template_config_version": self.config.version, "seed": self.config.seed},
            "fidelity": {"facades": "generic_templates", "interiors": "generic_simplified", "note": FIDELITY_NOTE},
            "inputs": self.inputs,
            "attribution": self.base.attribution,
            "frame": self.base.manifest()["frame"],
            "files": {"visual": "scene_visual.glb", "collision": "scene_collision.glb"},
            "prototypes": {k: {"triangles": p.triangle_count, "materials": [m for m, _ in p.parts],
                               "collider_triangles": p.collider.triangle_count if p.collider is not None else 0}
                           for k, p in sorted(self.prototypes.items())},
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
            "issues_by_code": dict(sorted(Counter(i.code for i in self.issues).items())),
            "severity": {k: severities.get(k, 0) for k in ("critical", "warning", "info")},
            "phase_04_checks": {
                "automatic_checks_passed": severities.get("critical", 0) == 0,
                "critical_errors": severities.get("critical", 0),
                "visual_review": "pending (docs/07-detail.md: a person must review the scene visually)",
            },
            "issues": [i.to_dict() for i in self.issues],
        }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_base(base_dir: str | Path, scene_path: str | Path, heights_path: str | Path, gen_config: GenerationConfig) -> tuple[GeneratedScene, dict[str, Any]]:
    """Regenerate phase 03 and prove it is the validated base stored in ``base_dir``."""
    base = Path(base_dir).expanduser()
    manifest_path, report_path = base / BASE_MANIFEST, base / BASE_REPORT
    for p in (manifest_path, report_path):
        if not p.is_file():
            raise DetailError(f"phase 03 base not found: {p}")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    stored = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not (report.get("phase_03_exit") or {}).get("passed"):
        raise DetailError(f"{report_path}: phase 03 exit did not pass; phase 04 needs a validated base")
    for key, path in (("geo_scene", Path(scene_path)), ("heights", Path(heights_path))):
        if (stored.get("inputs") or {}).get(key, {}).get("sha256") != _sha256(path.expanduser()):
            raise DetailError(f"{path} is not the {key} the phase 03 base was generated from (sha256 mismatch)")
    if (stored.get("inputs") or {}).get("config", {}).get("sha256") != gen_config.sha256:
        raise DetailError("generation config differs from the one used for the phase 03 base")
    generated = generate_scene(scene_path, heights_path, gen_config)
    if generated.critical:
        raise DetailError("regenerated phase 03 scene has critical errors")
    regenerated = json.loads(json.dumps(generated.manifest()))
    if regenerated != stored:
        raise DetailError("regenerated phase 03 geometry differs from the stored base manifest; regenerate the base first")
    ref = {"dir": str(base.resolve()), "manifest_sha256": _sha256(manifest_path), "report_sha256": _sha256(report_path)}
    return generated, ref


def _shop_evidence(features: list[Mapping[str, Any]], footprints: dict[str, BaseGeometry], cfg: DetailConfig) -> dict[str, list[str]]:
    pois = [(f["id"], shape(f["geometry"])) for f in features if f.get("kind") == "poi"
            and any(k in (f.get("tags") or {}) for k in cfg.shop_evidence_keys) and classify_poi(f.get("tags") or {}, cfg) is None]
    evidence: dict[str, list[str]] = defaultdict(list)
    if not pois:
        return evidence
    ids = sorted(footprints)
    tree = STRtree([footprints[i] for i in ids])
    for pid, point in pois:
        for k in sorted(int(x) for x in tree.query(point, predicate="dwithin", distance=cfg.shop_evidence_distance_m)):
            evidence[ids[k]].append(pid)
    return evidence


def _use_evidence(
    features: list[Mapping[str, Any]], footprints: dict[str, BaseGeometry], cfg: DetailConfig,
) -> dict[str, list[tuple[str, Mapping[str, str], str]]]:
    """OSM features that may name a building's use: ``(source id, tags, match)`` per building.

    A POI inside a footprint is ``poi_inside``. A POI outside every footprint goes to the
    nearest one within ``shop_evidence_distance_m`` as ``poi_near``. An area whose
    tags name a use (an ``amenity=school`` ground) is ``enclosing_area`` for every
    building whose representative point it contains. Street furniture is not evidence.
    """
    ids = sorted(footprints)
    tree = STRtree([footprints[i] for i in ids])
    related: dict[str, list[tuple[str, Mapping[str, str], str]]] = defaultdict(list)
    for f in features:
        tags = f.get("tags") or {}
        if f.get("kind") not in ("poi", "area") or facade_use(tags) is None or classify_poi(tags, cfg) is not None:
            continue
        geom = shape(f["geometry"])
        if f["kind"] == "area":
            for k in sorted(int(x) for x in tree.query(geom, predicate="intersects")):
                if geom.contains(footprints[ids[k]].representative_point()):
                    related[ids[k]].append((f["id"], tags, "enclosing_area"))
            continue
        inside = sorted(int(x) for x in tree.query(geom, predicate="intersects"))
        match = "poi_inside" if inside else "poi_near"
        hits = inside or sorted(int(x) for x in tree.query_nearest(geom, max_distance=cfg.shop_evidence_distance_m, all_matches=True))
        for k in hits:
            related[ids[k]].append((f["id"], tags, match))
    return related


def _facade_walls(
    ids: list[str], footprints: dict[str, BaseGeometry], tree: STRtree, feature_of: Mapping[str, Mapping[str, Any]],
    height_of: Mapping[str, Mapping[str, Any]], features: list[Mapping[str, Any]], cfg: DetailConfig,
) -> dict[str, tuple[FacadeUse, str, str]]:
    """``(use, wall, wall origin)`` per building, in id order so a wall can avoid its neighbours' walls.

    A landmark (flagged by the heights stage from its tags) keeps its template walls. Every
    other building draws from the palette of its use.
    Neighbours are the footprints within ``probe_m``.
    """
    related = _use_evidence(features, footprints, cfg)
    out: dict[str, tuple[FacadeUse, str, str]] = {}
    for bid in ids:
        h = height_of[bid]
        tags = dict(feature_of[bid].get("tags") or {})
        if h.get("landmark"):
            use = FacadeUse("landmark", "observed", bid, None, "landmark")
            palette = cfg.templates[cfg.landmark_template].wall_materials
        else:
            use = resolve_use(bid, tags, related.get(bid, []))
            palette = cfg.facade_palettes[use.use]
        near = [ids[k] for k in tree.query(footprints[bid], predicate="dwithin", distance=cfg.probe_m)]
        avoid = {out[n][1] for n in near if n in out}
        wall, origin = choose_wall(tags, palette, cfg.seed, bid, avoid)
        out[bid] = (use, wall, origin)
    return out


def _ground_evidence(ground: str, template: Template, category: str, poi_ids: list[str]) -> dict[str, Any] | None:
    if ground != "shopfront":
        return None
    if template.ground == "shopfront":
        return {"source": "building_category", "category": category, "osm_poi_ids": poi_ids}
    return {"source": "osm_poi", "osm_poi_ids": poi_ids}


def interior_quota(cfg: DetailConfig) -> int:
    """How many interiors this seed builds, from ``min_buildings`` through ``max_buildings``.

    A maximum of 0, or interiors turned off, builds none. The draw is
    ``sha256(seed, interiors, quota)``. A map with fewer matching buildings
    builds only those.
    """
    ic = cfg.interiors
    if not ic.enabled or ic.max_buildings <= 0:
        return 0
    low = min(ic.min_buildings, ic.max_buildings)
    high = ic.max_buildings
    if high <= low:
        return high
    span = high - low + 1
    return low + int(stable_unit(cfg.seed, "interiors", "quota") * span)


def _select_interiors(candidates: list[tuple[str, Mapping[str, Any], BaseGeometry]], cfg: DetailConfig, issues: list[Issue]) -> list[str]:
    ic = cfg.interiors
    if interior_quota(cfg) == 0:
        return []
    known = {bid for bid, _, _ in candidates}
    ordered: list[str] = []
    for bid in ic.include_ids:
        if bid in known:
            ordered.append(bid)
        else:
            issues.append(Issue("interior_include_unknown", "warning", bid, "include_ids entry is not a generated building"))
    rule = ic.rule
    ruled = []
    for bid, h, geom in candidates:
        levels = h.get("estimated_levels") or 0
        if (h.get("category") in rule.categories and not h.get("landmark") and levels >= rule.min_levels
                and rule.min_area_m2 <= geom.area <= rule.max_area_m2 and geom.geom_type == "Polygon" and not geom.interiors
                and rectangularity(geom) >= rule.min_rectangularity):
            ruled.append(bid)
    ruled.sort(key=lambda b: hashlib.sha256(f"{cfg.seed}:interior:{b}".encode()).hexdigest())
    return [b for b in [*ordered, *ruled] if b not in ic.exclude_ids]


def _metres(raw: object) -> float | None:
    if raw is None or raw == "":
        return None
    text = str(raw).strip().lower().replace(",", ".")
    if text.endswith("m"):
        text = text[:-1].strip()
    try:
        value = float(text)
    except ValueError:
        return None
    if not 0 < value <= 200:
        return None
    return value


def _largest_polygon(geom: BaseGeometry) -> BaseGeometry | None:
    if geom.is_empty:
        return None
    if geom.geom_type == "Polygon":
        return geom
    if geom.geom_type == "MultiPolygon":
        return max(geom.geoms, key=lambda g: g.area)
    if geom.geom_type == "GeometryCollection":
        polys = [g for g in geom.geoms if g.geom_type in ("Polygon", "MultiPolygon") and not g.is_empty]
        return _largest_polygon(max(polys, key=lambda g: g.area)) if polys else None
    return None


def _building_parts(
    features: list[Mapping[str, Any]],
    footprints: Mapping[str, BaseGeometry],
    cfg: DetailConfig,
) -> tuple[list[DetailObject], list[Issue]]:
    """Extrude OSM ``building:part`` polygons that carry a height, clipped to a building footprint."""
    from shapely.geometry.polygon import orient

    objects: list[DetailObject] = []
    issues: list[Issue] = []
    template = cfg.template_for("unknown", False)
    for f in features:
        tags = dict(f.get("tags") or {})
        if "building:part" not in tags or f.get("kind") == "building":
            continue
        height = _metres(tags.get("height"))
        if height is None:
            issues.append(Issue("building_part_no_height", "info", str(f["id"]), "building:part without a parseable height; skipped"))
            continue
        min_h = _metres(tags.get("min_height")) or 0.0
        if min_h >= height:
            issues.append(Issue("building_part_height_order", "warning", str(f["id"]), "min_height is not below height; skipped"))
            continue
        geom = _largest_polygon(shape(f["geometry"]))
        if geom is None or geom.area < 1.0:
            continue
        parent: str | None = None
        best = 0.0
        clipped = geom
        for bid, fp in footprints.items():
            inter = _largest_polygon(geom.intersection(fp))
            if inter is not None and inter.area > best:
                best, parent, clipped = inter.area, bid, inter
        if parent is None or clipped.area < 1.0:
            issues.append(Issue("building_part_outside_footprint", "info", str(f["id"]),
                                "building:part does not overlap a building footprint; skipped"))
            continue
        clipped = orient(clipped, sign=1.0)
        wall, wall_origin = choose_wall(tags, template.wall_materials, cfg.seed, str(f["id"]))
        collider = prism(clipped, min_h, height, bottom=True)
        defects = closed_manifold_defects(collider)
        if defects:
            issues.append(Issue("building_part_mesh_not_closed", "warning", str(f["id"]), f"{defects} collider defects; skipped"))
            continue
        part_id = f"building_part/{parent}/{f['id']}"
        visual = [Primitive(walls(clipped, min_h, height), wall), Primitive(cap(clipped, height), "roof_tile")]
        objects.append(DetailObject(
            part_id, "building_part", visual, [collider],
            {
                "id": part_id,
                "type": "building_part",
                "source_ids": [parent, f["id"]],
                "representation": "osm_building_part",
                "attribute_origin": "observed",
                "part": tags.get("building:part"),
                "height": {"height_m": height, "min_height_m": min_h, "origin": "observed"},
                "materials": {"wall": wall, "wall_origin": wall_origin, "roof": "roof_tile"},
                "visual": {"triangles": sum(p.mesh.triangle_count for p in visual)},
            },
        ))
    return objects, issues


ExtraUrban = Callable[
    [BaseGeometry, BaseGeometry, Mapping[str, Prototype], DetailConfig, list[Element]],
    tuple[list[Element], list[Rejected]],
]


def detail_scene(
    base: GeneratedScene,
    base_ref: Mapping[str, Any],
    heights_path: str | Path,
    scene_path: str | Path,
    cfg: DetailConfig,
    *,
    prototypes: Mapping[str, Prototype] | None = None,
    extra_urban: ExtraUrban | None = None,
) -> DetailedScene:
    heights = json.loads(Path(heights_path).expanduser().read_text(encoding="utf-8"))
    scene = json.loads(Path(scene_path).expanduser().read_text(encoding="utf-8"))
    height_of = {b["building_id"]: b for b in heights.get("buildings", [])}
    features = sorted(scene.get("features", []), key=lambda f: f["id"])
    feature_of = {f["id"]: f for f in features}
    issues: list[Issue] = []

    building_objs = [o for o in base.objects if o.type == "building"]
    footprints = {o.record["source_ids"][0]: shape(feature_of[o.record["source_ids"][0]]["geometry"]) for o in building_objs}
    ids = sorted(footprints)
    tree = STRtree([footprints[i] for i in ids])
    evidence = _shop_evidence(features, footprints, cfg)
    facade_walls = _facade_walls(ids, footprints, tree, feature_of, height_of, features, cfg)

    candidates = [(bid, height_of[bid], footprints[bid]) for bid in ids]
    interior_queue = _select_interiors(candidates, cfg, issues)
    interiors_left = interior_quota(cfg)

    objects: list[DetailObject] = []
    counts: Counter[str] = Counter()
    templates_used: Counter[str] = Counter()
    roof_types: Counter[str] = Counter()
    door_rules: Counter[str] = Counter()
    interiors_built: list[str] = []
    interior_triangles = 0
    exposed_m = party_m = 0.0

    # Buildings in the interior queue go first, in queue order, so the quota and the triangle budget go
    # to the sha256 order and not to the id order. Their objects and issues are put back in building order below.
    rank = {bid: k for k, bid in enumerate(interior_queue)}
    order = sorted(building_objs, key=lambda b: rank.get(b.record["source_ids"][0], len(rank)))
    leading_issues = len(issues)
    emitted: dict[str, tuple[list[DetailObject], list[Issue]]] = {}

    for o in order:
        first_object, first_issue = len(objects), len(issues)
        bid = o.record["source_ids"][0]
        geom = footprints[bid]
        h = height_of[bid]
        height = float(o.record["height"]["height_m"])
        landmark = bool(h.get("landmark"))
        template = cfg.template_for(str(h.get("category")), landmark)
        templates_used[template.name] += 1
        others = union_all([footprints[ids[k]] for k in tree.query(geom.buffer(cfg.probe_m + 0.5)) if ids[k] != bid])
        refined, segments = refine_exposure(geom, others, cfg)
        if abs(refined.area - geom.area) > 1e-6 * max(1.0, geom.area):
            issues.append(Issue("refined_footprint_area_changed", "critical", bid, f"{refined.area - geom.area:.6f} m²"))
        storeys = storeys_for(height, h.get("estimated_levels"), landmark, cfg)
        ground = "shopfront" if evidence.get(bid) and template.ground != "main_door_only" else template.ground
        main = exposed_frontage(segments)
        tags = dict(feature_of[bid].get("tags") or {})
        use, wall_material, wall_origin = facade_walls[bid]
        convex = geom.geom_type == "Polygon" and not geom.interiors and geom.convex_hull.area <= geom.area * 1.02
        shell_template, roof_material, roof_origin, roof_shape = choose_roof(
            tags, template, cfg.seed, bid, geom.area, convex=convex,
        )
        door_spec = cfg.landmark_door if landmark else cfg.main_door
        plain_door = ("landmark_door" if landmark else "door", cfg.recess_depth_m, door_spec)
        previous_height = height
        plain_storeys = storeys

        def run_layout(door_kind: str, door_depth: float, door_opening: Any, storeys_used: Storeys) -> Layout:
            layout = Layout()
            for seg in segments:  # noqa: B023 - run_layout is called only inside this iteration, so it sees this building's values
                if seg.exposed:
                    layout_segment(seg, storeys_used, template, ground, door_opening if seg is main else None, door_kind, door_depth,  # noqa: B023 - run_layout is called only inside this iteration, so it sees this building's values
                                   bid, refined, others, cfg, layout)  # noqa: B023 - run_layout is called only inside this iteration, so it sees this building's values
            return layout

        interior = None
        layout = None
        if bid in interior_queue and interiors_left > 0 and main is not None:
            ic = cfg.interiors
            raised = interior_storey(height, storeys, h.get("height_source") or o.record["height"].get("height_source"))
            attempt_height, attempt_storeys = raised if raised is not None else (height, storeys)
            trial = run_layout("interior_door", ic.wall_thickness_m, ic.door, attempt_storeys)
            door = next((x for x in trial.holes if x.kind == "interior_door"), None)
            plan = plan_interior(
                bid, refined, door, main, attempt_height, attempt_storeys.height_m, attempt_storeys.count, cfg,
                roof_access=shell_template.parapet_m > 0,
            ) if door else "no_door_position"
            if isinstance(plan, str):
                issues.append(Issue("interior_not_generated", "info", bid, plan))
            else:
                height = attempt_height
                storeys = attempt_storeys
                layout = trial
                interior = plan
        if layout is None:
            layout = run_layout(*plain_door, storeys)
        shop = ground == "shopfront"
        doors_on = door_walls(segments, main, others, cfg)
        door_rule = ensure_door(layout, [] if shop else doors_on, storeys, plain_door[0], door_spec, template, refined, cfg)
        opened_windows = omitted_windows = 0
        if interior is not None:
            opened_windows, omitted_windows = pierce_room_windows(
                layout, segments, interior.inner, refined, cfg.interiors.wall_thickness_m,
                interior.floors * interior.storey_m,
            )

        shell = build_shell(refined, segments, layout, height, shell_template, wall_material, roof_material, bid, cfg)
        built = None
        if interior is not None:
            built = build_interior(
                interior, height, shell.z_top, refined, cfg, room_window_cuts(layout, segments),
                glass_penetrations(layout, segments),
            )
            cost = built.visual_triangles
            if fits_triangle_budget(interior_triangles, cost, cfg.interiors.max_triangles):
                interior_triangles += cost
            else:
                issues.append(Issue("interior_not_generated", "info", bid,
                                    f"triangle_budget: needs {cost} triangles, {cfg.interiors.max_triangles - interior_triangles} left "
                                    f"of max_triangles {cfg.interiors.max_triangles}"))
                interior = built = None
                height, storeys = previous_height, plain_storeys
                layout = run_layout(*plain_door, storeys)
                door_rule = ensure_door(layout, [] if shop else doors_on, storeys, plain_door[0], door_spec, template, refined, cfg)
                shell = build_shell(refined, segments, layout, height, shell_template, wall_material, roof_material, bid, cfg)  # pragma: no mutate: only None for building_id or cfg survives, build_shell reads neither; the budget test pins the rebuilt shell
        shell.roof["origin"] = roof_origin
        shell.roof["roof_shape"] = roof_shape
        if shell.defects:
            issues.append(Issue("facade_mesh_not_watertight", "critical", bid, f"{shell.defects} open or non-manifold edges"))
        if shell.roof_area_error > max(0.02, 1e-3 * geom.area):
            issues.append(Issue("roof_area_mismatch", "critical", bid, f"roof differs from footprint by {shell.roof_area_error:.3f} m²"))
        colliders: list[Mesh] = []
        glass_colliders: list[Mesh] = []
        base_collider = o.collider
        interior_record = None
        if built is not None:
            if omitted_windows:
                issues.append(Issue(
                    "interior_window_omitted", "info", bid,
                    f"{omitted_windows} windows omitted; they did not meet the inner wall",
                ))
            if built.defects:
                issues.append(Issue("interior_collider_not_closed", "critical", bid, f"{built.defects} defects"))
            if built.roof_well is not None:
                cut_roof_deck(shell, built.roof_well)
            colliders = [m for _, m in built.colliders]
            glass_colliders = list(built.glass_colliders)
            if shell.parapet_collider is not None and built.roof_well is not None:
                colliders.append(shell.parapet_collider)
            interior_record = built.spec | {
                "checks": built.checks,
                "collider_parts": [n for n, _ in built.colliders],
                "through_windows": opened_windows,
                "omitted_windows": omitted_windows,
            }
            objects.append(DetailObject(
                f"interior/{bid}", "interior",
                [Primitive(merge(ms), mat) for mat, ms in sorted(built.visual.items()) if ms],
                [], {"id": f"interior/{bid}", "type": "interior", "source_ids": [bid], **interior_record,
                     "generation": {"method": "generic_simplified_interior", "attribute_origin": "generated"}},
                light_anchors=built.light_anchors,
            ))
            interiors_built.append(bid)
            interiors_left -= 1
        elif base_collider is not None:
            colliders = [base_collider]
        balcony_collider = merge(shell.balcony_colliders)
        if balcony_collider.triangle_count:
            defects = closed_manifold_defects(balcony_collider)
            if defects:
                issues.append(Issue("collider_not_closed", "critical", bid, f"balcony colliders: {defects} defects"))
            colliders.append(balcony_collider)

        for kind, n in shell.counts.items():
            counts[kind] += n
        roof_types[str(shell.roof["type"])] += 1
        door_rules[door_rule] += 1
        seg_exposed = sum(s.length for s in segments if s.exposed)
        exposed_m += seg_exposed
        party_m += sum(s.length for s in segments if not s.exposed)
        for reason, n in sorted(layout.skipped.items()):
            counts[f"skipped:{reason}"] += n
        visual = [Primitive(merge(ms), mat) for mat, ms in sorted(shell.parts.items()) if any(m.triangle_count for m in ms)]
        record = {
            **{k: v for k, v in o.record.items() if k not in ("visual", "materials", "collider", "checks", "generation")},
            "generation": {"method": "phase03_volume + generic facade template", "phase03": o.record.get("generation"),
                           "template": template.name, "template_config_version": cfg.version, "seed": cfg.seed},
            "facade": {
                "template": template.name,
                "representation": "generic_template",
                "representative_of_real_facade": False,
                "attribute_origin": "generated",
                "note": FIDELITY_NOTE,
                "needs_custom_model": landmark,
                "ground_floor": ground,
                "ground_floor_evidence": _ground_evidence(ground, template, str(h.get("category")), evidence.get(bid, [])),
                "storeys": {"count": storeys.count, "height_m": round(storeys.height_m, 3), "origin": storeys.origin},
                "exposed_length_m": round(seg_exposed, 3),
                "party_wall_length_m": round(sum(s.length for s in segments if not s.exposed), 3),
                "openings": dict(sorted(shell.counts.items())),
                "skipped": dict(sorted(layout.skipped.items())),
                "materials": {"wall": wall_material, "wall_origin": wall_origin, "roof": shell.roof["material"], "roof_origin": roof_origin},
                "use": use.to_record(),
                "door": door_rule,
            },
            "roof": shell.roof,
            "interior": interior_record is not None,
            "materials": [p.material for p in visual],
            "visual": {"triangles": sum(p.mesh.triangle_count for p in visual)},
            "collider": {"source": "interior_shell" if interior_record else "phase03_simplified_prism",
                         "parts": len(colliders), "triangles": sum(m.triangle_count for m in colliders),
                         "balconies": len(shell.balcony_colliders),
                         "glass_panes": len(glass_colliders)},
            "checks": {"facade_open_edges": shell.defects, "roof_area_error_m2": round(shell.roof_area_error, 4)},
        }
        if storeys.origin == "interior_storey":
            record["height"] = {
                **record["height"],
                "height_m": round(height, 3),
                "previous_height_m": round(previous_height, 3),
                "interior_storey_origin": "estimated",
            }
        frame = wall_uv_frame(segments, storeys, h.get("estimated_levels"))
        objects.append(DetailObject(o.id, "building", visual, colliders, record, glass_colliders=glass_colliders, uv_frame=frame))
        if landmark:
            towers, tower_issues = attach_towers(bid, geom, features, cfg)
            issues.extend(tower_issues)
            for part in towers:
                objects.append(DetailObject(part.id, "landmark_part", list(part.visual), [part.collider], part.record))
        emitted[o.id] = (objects[first_object:], issues[first_issue:])

    objects = [x for o in building_objs for x in emitted[o.id][0]]
    issues = issues[:leading_issues] + [x for o in building_objs for x in emitted[o.id][1]]

    part_objects, part_issues = _building_parts(features, footprints, cfg)
    issues.extend(part_issues)
    objects.extend(part_objects)

    # Surfaces, curbs and ground slabs are reused from phase 03 unchanged.
    for o in base.objects:
        if o.type == "building":
            continue
        objects.append(DetailObject(o.id, o.type, list(o.visual), [o.collider] if o.collider is not None else [], dict(o.record)))

    prototypes = dict(prototypes or urban_prototypes())
    walkable = [s.geometry for c, s in base.partition.surfaces.items() if c not in (SurfaceClass.CARRIAGEWAY, SurfaceClass.WATER)]
    free = union_all(walkable) if walkable else Point(0, 0).buffer(0)
    buildings_union = union_all(list(footprints.values()))
    elements, rejected = place_elements(features, base.ways, free, buildings_union, prototypes, cfg)
    if extra_urban is not None:
        extra, extra_rejected = extra_urban(free, buildings_union, prototypes, cfg, elements)
        elements.extend(extra)
        rejected.extend(extra_rejected)
    for r in rejected:
        severity = "info" if "#lamp" in r.id else "warning"
        issues.append(Issue("urban_element_not_placed", severity, r.id, f"{r.kind}: {r.reason}"))
    used_protos: dict[str, Prototype] = {}
    for e in elements:
        proto = prototypes[e.kind]
        used_protos[proto.key] = proto
        objects.append(DetailObject(
            e.id, "urban_element", [], [], {
                "id": e.id, "type": "urban_element", "kind": e.kind, "source_ids": list(e.source_ids),
                "attribute_origin": e.origin, "placement_rule": e.rule, "prototype": proto.key,
                "position_m": [round(float(e.matrix[0, 3]), 3), round(float(e.matrix[1, 3]), 3)],
                "yaw_deg": round(float(np.degrees(np.arctan2(e.matrix[1, 0], e.matrix[0, 0]))), 2),
                "representation": "generic_prototype",
            }, proto.key, e.matrix))

    for o in objects:
        for prim in o.visual:
            if not np.isfinite(prim.mesh.vertices).all():
                issues.append(Issue("non_finite_vertices", "critical", o.id, "mesh has NaN or infinite coordinates"))
        for m in (*o.colliders, *o.glass_colliders):
            if m.triangle_count and o.type in ("building", "landmark_part") and signed_volume(m) <= 0:
                issues.append(Issue("collider_not_closed", "critical", o.id, "collider volume is not positive"))

    detailed = DetailedScene(base, objects, used_protos, issues, cfg, {})
    detailed.inputs = {
        "phase03_base": dict(base_ref),
        "geo_scene": base.inputs["geo_scene"],
        "heights": base.inputs["heights"],
        "generation_config": base.inputs["config"],
        "detail_config": {"path": cfg.path, "sha256": cfg.sha256, "version": cfg.version},
    }
    detailed.stats = _stats(detailed, counts, templates_used, roof_types, interiors_built, exposed_m, party_m, elements)
    detailed.stats["interior_visual_triangles"] = interior_triangles
    detailed.stats["doors"] = dict(sorted(door_rules.items()))
    detailed.stats["facade_uses"] = dict(sorted(Counter(u.use for u, _, _ in facade_walls.values()).items()))
    detailed.stats["wall_materials"] = dict(sorted(Counter(w for _, w, _ in facade_walls.values()).items()))
    _budget(detailed)
    return detailed


def _stats(d: DetailedScene, counts: Counter[str], templates: Counter[str], roofs: Counter[str], interiors: list[str],
           exposed_m: float, party_m: float, elements: list[Any]) -> dict[str, Any]:
    vis_nodes, col_nodes = d.visual_nodes(), d.collision_nodes()
    by_material: Counter[str] = Counter()
    rendered = unique = 0
    seen: set[str] = set()
    for n in vis_nodes:
        tris = sum(p.mesh.triangle_count for p in n.primitives)
        rendered += tris
        if n.mesh_key is None or n.mesh_key not in seen:
            unique += tris
            if n.mesh_key:
                seen.add(n.mesh_key)
        for p in n.primitives:
            by_material[p.material] += p.mesh.triangle_count
    col_rendered = sum(p.mesh.triangle_count for n in col_nodes for p in n.primitives)
    openings = {k: v for k, v in sorted(counts.items()) if not k.startswith("skipped:")}
    return {
        "objects": dict(sorted(Counter(o.type for o in d.objects).items())),
        "templates": dict(sorted(templates.items())),
        "roofs": dict(sorted(roofs.items())),
        "openings": openings,
        "openings_skipped": {k.split(":", 1)[1]: v for k, v in sorted(counts.items()) if k.startswith("skipped:")},
        "facade_exposed_length_m": round(exposed_m, 1),
        "party_wall_length_m": round(party_m, 1),
        "interiors": interiors,
        "urban_elements": dict(sorted(Counter(f"{e.kind}:{e.origin}" for e in elements).items())),
        "visual_triangles": rendered,
        "visual_triangles_unique": unique,
        "visual_triangles_by_material": dict(sorted(by_material.items())),
        "visual_triangles_phase03": int(d.base.stats["visual_triangles"]),
        "materials": len(by_material),
        "visual_nodes": len(vis_nodes),
        "instanced_nodes": sum(1 for n in vis_nodes if n.mesh_key),
        "shared_meshes": len({n.mesh_key for n in vis_nodes if n.mesh_key}),
        "collision_nodes": len(col_nodes),
        "collision_triangles": col_rendered,
    }


def _budget(d: DetailedScene) -> None:
    b, s = d.config.budget, d.stats
    measured = {"max_visual_triangles": s["visual_triangles"], "max_collision_triangles": s["collision_triangles"],
                "max_materials": s["materials"], "max_nodes": s["visual_nodes"]}
    s["budget"] = {k: {"limit": b[k], "value": v, "within": v <= b[k]} for k, v in measured.items()}
    for k, v in measured.items():
        if v > b[k]:
            d.issues.append(Issue("budget_exceeded", "warning", None, f"{k}: {v} > {b[k]} (provisional budget)"))
