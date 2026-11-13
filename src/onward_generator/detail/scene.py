"""Phase 04 assembly on top of the validated phase 03 scene.

The phase 03 scene is regenerated in memory from the same inputs and must match
the stored, validated ``scene3d.json`` exactly; phase 04 only adds detail to it.
Nothing here knows about Onward or Unity.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np
from shapely import LineString, Point, Polygon, STRtree, union_all
from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry

from onward_generator import __version__
from onward_generator.detail.config import REPO_ROOT, DetailConfig, Template
from onward_generator.detail.facades import (
    DOOR_KINDS,
    HIDDEN_GAP_M,
    MANSARD_MIN_STOREYS,
    Hole,
    Layout,
    Mansard,
    Segment,
    Shell,
    Storeys,
    WallAreas,
    build_shell,
    cut_roof_deck,
    door_walls,
    ensure_door,
    entrance,
    exposed_frontage,
    filant_storeys,
    interior_storey,
    layout_segment,
    refine_exposure,
    setback_floors,
    split_blank_walls,
    stable_unit,
    storeys_for,
    wall_uv_frame,
)
from onward_generator.detail.furnish import FLOOR_SCOPE, FurnishError, ShopFurnishing, load_catalogue
from onward_generator.detail.grammar import (
    FLAT_SHAPES,
    OVERTURE_FACADE,
    OVERTURE_ROOF,
    FacadeUse,
    appearance_key,
    choose_roof,
    choose_wall,
    facade_use,
    resolve_use,
    tagged_roof,
    tagged_wall,
)
from onward_generator.detail.interiors import (
    MIN_ROOM_M2,
    Interior,
    InteriorPlan,
    assemble_interior,
    cap_lights,
    glass_penetrations,
    interior_visual,
    pierce_room_windows,
    plan_interior,
    plan_shop_rooms,
    rectangularity,
    room_ceiling,
    room_window_cuts,
    visual_triangles,
)
from onward_generator.detail.landmarks import attach_towers
from onward_generator.detail.lidar_roofs import FLAT, PITCHED, REASONS, RoofSpread, SpreadReport, classify, is_roof_tagged
from onward_generator.detail.lod2 import Lod2Index, Lod2Part, lod2_parts
from onward_generator.detail.mid import MID_MAX_TRIANGLES, MID_REPRESENTATION, build_mid_shell
from onward_generator.detail.modules import Prototype, urban_prototypes
from onward_generator.detail.reach import hidden_face, reached_ground
from onward_generator.detail.roofs import MIN_RISE_M, BuiltRoof, build_roof, pitched_spec, with_rise
from onward_generator.detail.s3db import part_height
from onward_generator.detail.shops import CATASTRO_USE_TAG, plan_shops
from onward_generator.detail.typology import (
    FacadeStyle,
    TypologyConfig,
    building_typologies,
    facade_style,
    load_typology_config,
    styled_template,
)
from onward_generator.detail.urban import Element, Rejected, classify_poi, place_elements
from onward_generator.generation.config import GenerationConfig
from onward_generator.generation.gltf import Node, Primitive
from onward_generator.generation.mesh import (
    Mesh,
    cap,
    closed_manifold_defects,
    collapse_short_edges,
    merge,
    polygons_of,
    prism,
    quad,
    signed_volume,
    walls,
)
from onward_generator.generation.scene import GeneratedScene, Issue, SceneObject, generate_scene, ground_slabs
from onward_generator.generation.surfaces import SurfaceClass
from onward_generator.io import sha256_file as _sha256
from onward_generator.sources.es.catastro import PLUGIN_ID as CATASTRO_ID
from onward_generator.sources.registry import OSM_EVIDENCE, load_source_config
from onward_generator.sources.world.overture import PLUGIN_ID as OVERTURE_ID
from onward_generator.tiers import (
    CORE,
    MID,
    SCENIC,
    SCENIC_ROOF,
    CoreError,
    check_mid_ring,
    core_square,
    mid_square,
    ring_tier,
    scenic_wall,
)

MANIFEST_SCHEMA = "onward_generator.scene_detail"
REPORT_SCHEMA = "onward_generator.detail_report"
SCHEMA_VERSION = "0.4.0"
"""0.2.0: ``facade.relief`` and ``counts.flat_openings`` (ADR-025). 0.3.0: ``facade.typology`` and its counts (ADR-024). 0.4.0: ``facade.typology.facade_style`` (ADR-027)."""
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
    core: dict[str, Any] | None = None  # ADR-017: half-side and building counts, None without a core
    extra_attribution: list[dict[str, Any]] = field(default_factory=list)  # third-party POIs that gave a shop (ADR-021)

    @property
    def attribution(self) -> list[dict[str, Any]]:
        """The phase 03 attribution, then one line per third-party dataset whose places this scene used."""
        return [*self.base.attribution, *self.extra_attribution]

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
            source = tint_source(o.record)
            if source is not None:
                extras["tint_source"] = source
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
            "attribution": self.attribution,
            "frame": self.base.manifest()["frame"],
            "files": {"visual": "scene_visual.glb", "collision": "scene_collision.glb"},
            "core": self.core,
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


@dataclass(slots=True)
class InteriorBudget:
    """Triangles one kind of interior (stair or shop) has used of its limits: ``triangles`` (drawn) and ``collision_triangles``.

    ``prefix`` names the limits in ``interiors`` for the issue: ``""`` or ``"shops."``.
    """

    prefix: str
    limits: dict[str, int]
    used: dict[str, int] = field(default_factory=lambda: {"triangles": 0, "collision_triangles": 0})

    @classmethod
    def of_stairs(cls, cfg: DetailConfig) -> InteriorBudget:
        ic = cfg.interiors
        return cls("", {"triangles": ic.max_triangles, "collision_triangles": ic.max_collision_triangles})

    @classmethod
    def of_shops(cls, cfg: DetailConfig) -> InteriorBudget:
        sc = cfg.interiors.shops
        return cls("shops.", {"triangles": sc.max_triangles, "collision_triangles": sc.max_collision_triangles})

    def refuse(self, kind: str, cost: int) -> str | None:
        """``None`` when ``cost`` more ``kind`` triangles fit, else the ``triangle_budget`` reason."""
        used, limit = self.used[kind], self.limits[kind]
        if used + cost <= limit:
            return None
        return f"triangle_budget: needs {cost} {kind.replace('_', ' ')}, {limit - used} left of {self.prefix}max_{kind} {limit}"

    def spend(self, built: Interior) -> None:
        self.used["triangles"] += built.visual_triangles
        self.used["collision_triangles"] += built.collision_triangles


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


def _poi_building(point: BaseGeometry, ids: list[str], tree: STRtree, footprints: Mapping[str, BaseGeometry], cfg: DetailConfig) -> tuple[str, bool] | None:
    """The one building a POI belongs to, and whether the point is inside its footprint.

    A point inside a footprint belongs to that building only (ADR-016). A point outside every footprint
    goes to the nearest one within ``shop_evidence_distance_m``. A tie (a point on a party line, or as far
    from two footprints) goes to the footprint that contains it, then to the lowest id, so a POI never
    gives two buildings a shopfront.
    """
    hits = tree.query(point, predicate="dwithin", distance=cfg.shop_evidence_distance_m)
    if hits.size == 0:
        return None
    ranked = sorted((footprints[ids[k]].distance(point), not footprints[ids[k]].contains(point), ids[k]) for k in hits)
    distance, _, bid = ranked[0]
    return bid, distance == 0.0


def _shop_evidence(features: list[Mapping[str, Any]], footprints: dict[str, BaseGeometry], cfg: DetailConfig) -> dict[str, list[str]]:
    """The shop POIs of each building (``_poi_building``): a ``shop_evidence_keys`` POI that is not street furniture."""
    pois = [(f["id"], shape(f["geometry"])) for f in features if f.get("kind") == "poi"
            and any(k in (f.get("tags") or {}) for k in cfg.shop_evidence_keys) and classify_poi(f.get("tags") or {}, cfg) is None]
    evidence: dict[str, list[str]] = defaultdict(list)
    if not pois:
        return evidence
    ids = sorted(footprints)
    tree = STRtree([footprints[i] for i in ids])
    for pid, point in pois:
        owner = _poi_building(point, ids, tree, footprints, cfg)
        if owner is not None:
            evidence[owner[0]].append(pid)
    return evidence


def _shop_sources(ids: list[str], feature_of: Mapping[str, Mapping[str, Any]], evidence: Mapping[str, list[str]],
                  templates: Mapping[str, Template], cfg: DetailConfig, ranking: Sequence[str],
                  fits: Callable[[str], bool], places: Mapping[str, list[str]]) -> dict[str, str]:
    """The source of each building's best-ranked shop evidence; a building with none is left out (ADR-022).

    ``ranking`` is ``config/sources.json`` ``evidence``. ``osm`` is a shop POI or a commercial building category;
    the Catastro use counts when its code is in ``shop_catastro_uses`` and a shop fits the building (``fits``):
    the use is the parcel's, and a part of it behind the others, or a strip too narrow for a room, holds no shop.
    ``overture`` is an Overture place inside the building (``places``, ADR-021).
    A source with no evidence for the building passes its turn to the next.
    """
    out: dict[str, str] = {}
    for bid in ids:
        use = feature_of[bid].get("catastro_use")
        found = {
            OSM_EVIDENCE: bool(evidence.get(bid)) or templates[bid].ground == "shopfront",
            CATASTRO_ID: use is not None and use["current_use"] in cfg.shop_catastro_uses and fits(bid),
            OVERTURE_ID: bool(places.get(bid)),
        }
        winner = next((source for source in ranking if found.get(source)), None)
        if winner is not None:
            out[bid] = winner
    return out


def _shop_fits(bid: str, ids: Sequence[str], footprints: Mapping[str, BaseGeometry], tree: STRtree, cfg: DetailConfig) -> bool:
    """A street front, and somewhere inside the walls the door clearance of a shop room is wide enough (ADR-022).

    ``tree`` indexes ``footprints`` in ``ids`` order. Every box neighbour within ``probe_m`` is passed on;
    ``refine_exposure`` only lets the ones within ``probe_m`` of a wall hide it.
    """
    near = [footprints[ids[k]] for k in tree.query(footprints[bid].buffer(cfg.probe_m)) if ids[k] != bid]
    refined, segments = refine_exposure(footprints[bid], union_all(near), cfg)
    reach = cfg.interiors.wall_thickness_m + cfg.interiors.door_clearance_m / 2
    return exposed_frontage(segments) is not None and not refined.buffer(-reach).is_empty


def _use_evidence(
    features: list[Mapping[str, Any]], footprints: dict[str, BaseGeometry], cfg: DetailConfig,
) -> dict[str, list[tuple[str, Mapping[str, str], str]]]:
    """OSM features that may name a building's use: ``(source id, tags, match)`` per building.

    A POI goes to one building (``_poi_building``): ``poi_inside`` when the point is in its footprint,
    ``poi_near`` when it is outside every footprint. An area whose
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
            for k in sorted(int(x) for x in tree.query(geom)):
                if geom.contains(footprints[ids[k]].representative_point()):
                    related[ids[k]].append((f["id"], tags, "enclosing_area"))
            continue
        owner = _poi_building(geom, ids, tree, footprints, cfg)
        if owner is not None:
            related[owner[0]].append((f["id"], tags, "poi_inside" if owner[1] else "poi_near"))
    return related


def _facade_walls(
    ids: list[str], footprints: dict[str, BaseGeometry], tree: STRtree, feature_of: Mapping[str, Mapping[str, Any]],
    height_of: Mapping[str, Mapping[str, Any]], features: list[Mapping[str, Any]], cfg: DetailConfig,
    style_palettes: Mapping[str, Sequence[str]] | None = None,
    lent: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, tuple[FacadeUse, str, str]]:
    """``(use, wall, wall origin)`` per building, in id order so a wall can avoid its neighbours' walls.

    A landmark (flagged by the heights stage from its tags) keeps its template walls. Every
    other building draws from the palette of its use, or a residential one from ``style_palettes``
    (ADR-027) when that holds one for it.
    The sub-buildings of one parcel (``appearance_key``, ADR-016) are one building: the first in id
    order resolves the use from the evidence of them all and draws the wall for its key, and the
    others take the same. Neighbours are the footprints of other keys within ``probe_m``.
    ``lent`` holds the Overture facade colour of each key (``_lent_by_key``, plan C2); a landmark is never lent one.
    """
    related = _use_evidence(features, footprints, cfg)
    key_of = {bid: appearance_key(bid, feature_of[bid]) for bid in ids}
    members: dict[str, list[str]] = defaultdict(list)
    for bid in ids:
        members[key_of[bid]].append(bid)
    out: dict[str, tuple[FacadeUse, str, str]] = {}
    for bid in ids:
        key = key_of[bid]
        first = members[key][0]
        if first != bid:
            out[bid] = out[first]
            continue
        h = height_of[bid]
        tags = dict(feature_of[bid].get("tags") or {})
        colour = _lent_colour((lent or {}).get(key))
        if h.get("landmark"):
            use = FacadeUse("landmark", "observed", bid, None, "landmark")
            palette = cfg.templates[cfg.landmark_template].wall_materials
            colour = None  # pragma: no mutate: an empty colour parses as no colour, so the landmark keeps its wall either way
        else:
            use = resolve_use(bid, tags, [r for m in members[key] for r in related.get(m, [])])
            palette = cfg.facade_palettes[use.use]
            if use.use == "residential" and style_palettes and style_palettes.get(bid):
                palette = tuple(style_palettes[bid])
        near = {ids[k] for m in members[key] for k in tree.query(footprints[m], predicate="dwithin", distance=cfg.probe_m)}
        avoid = {out[n][1] for n in near if n in out}  # no other part of this parcel is drawn yet
        wall, origin = choose_wall(tags, palette, cfg.seed, key, avoid, cfg.materials, lent=colour)
        out[bid] = (use, wall, origin)
    return out


RoofPlan = tuple[Template, str, str, str | None]


def _templates(ids: list[str], height_of: Mapping[str, Mapping[str, Any]], cfg: DetailConfig) -> dict[str, Template]:
    """The facade template of each building, from its heights category; a landmark takes the landmark template."""
    return {bid: cfg.template_for(str(height_of[bid].get("category")), bool(height_of[bid].get("landmark"))) for bid in ids}


def _roof_plans(
    ids: list[str], footprints: Mapping[str, BaseGeometry], tree: STRtree, key_of: Mapping[str, str],
    tags_of: Mapping[str, dict[str, str]], templates: Mapping[str, Template], heights_m: Mapping[str, float], cfg: DetailConfig,
    storeys_m: Mapping[str, float],
    lent: Mapping[str, Mapping[str, Any]] | None = None,
    lidar: Mapping[str, tuple[str, RoofSpread]] | None = None,
) -> dict[str, RoofPlan]:
    """``choose_roof`` per building, drawn for its ``appearance_key`` so the parts of one parcel share a roof.

    ``storeys_m`` is each building's storey height, the height of one ``roof:levels``. ``lent`` holds the
    Overture roof colour of each key (``_lent_by_key``, plan C2). ``lidar`` holds the class and spread of each building
    the nDSM classified ``flat`` or ``pitched`` (plan C3).

    A part with a taller part of its own parcel within ``probe_m`` gets no estimated hip: its roof is
    the terrace under the setback, flat behind its parapet.
    """
    plans: dict[str, RoofPlan] = {}
    for bid in ids:
        geom = footprints[bid]
        terrace = _terrace(bid, ids, footprints, tree, key_of, heights_m, cfg)
        convex = geom.geom_type == "Polygon" and not geom.interiors and geom.convex_hull.area <= geom.area * 1.02
        plans[bid] = choose_roof(tags_of[bid], templates[bid], cfg.seed, key_of[bid], geom.area, convex=convex and not terrace,
                                 storey_m=storeys_m[bid], colours=cfg.materials, lent=_lent_colour((lent or {}).get(key_of[bid])),
                                 lidar=(lidar or {}).get(bid))
    return plans


def _terrace(bid: str, ids: Sequence[str], footprints: Mapping[str, BaseGeometry], tree: STRtree, key_of: Mapping[str, str],
             heights_m: Mapping[str, float], cfg: DetailConfig) -> bool:
    """True when a taller part of the building's own parcel stands within ``probe_m``: its roof is the terrace under the setback."""
    near = [ids[k] for k in tree.query(footprints[bid], predicate="dwithin", distance=cfg.probe_m)]
    return any(key_of[n] == key_of[bid] and heights_m[n] > heights_m[bid] for n in near)


def _lidar_classes(ids: Sequence[str], footprints: Mapping[str, BaseGeometry], tree: STRtree, key_of: Mapping[str, str],
                   tags_of: Mapping[str, Mapping[str, str]], heights_m: Mapping[str, float], tier_of: Mapping[str, str],
                   report: SpreadReport, cfg: DetailConfig) -> dict[str, str]:
    """``lidar_roofs.classify`` of every building (plan C3): ``flat``, ``pitched`` or the reason it is not classified."""
    return {bid: classify(report.buildings.get(bid), footprints[bid], tagged=is_roof_tagged(tags_of[bid]), scenic=tier_of[bid] == SCENIC,
                          terrace=_terrace(bid, ids, footprints, tree, key_of, heights_m, cfg))
            for bid in ids}


def _lidar_inputs(report: SpreadReport, classes: dict[str, str]) -> tuple[
        dict[str, str], dict[str, tuple[str, RoofSpread]], dict[str, dict[str, object]]]:
    """``(classes, plans, evidence)``: every class, the ``(class, spread)`` of the buildings classified flat or pitched, and their ``roof.lidar``."""
    plans = {bid: (c, report.buildings[bid]) for bid, c in classes.items() if c in (FLAT, PITCHED)}
    return classes, plans, {bid: report.evidence(spread, c) for bid, (c, spread) in plans.items()}


def _lidar_record(detailed: DetailedScene, objects: Sequence[DetailObject], classes: Mapping[str, str], report: SpreadReport) -> None:
    """``counts.lidar_roofs`` and, when a classified roof was kept, the plugin's attribution line (plan C3).

    ``buildings`` is every building, ``core_buildings`` those detailed in the core; ``flat`` and ``pitched`` the roofs
    the spread decided, and ``unclassified`` the others by reason (``REASONS``), a pitched roof drawn flat as ``fallback``.
    """
    outcomes = dict(classes)
    for o in objects:
        if o.type == "building" and "fallback" in o.record["roof"] and outcomes[o.record["source_ids"][0]] == PITCHED:
            outcomes[o.record["source_ids"][0]] = "fallback"
    counts = Counter(outcomes.values())
    detailed.stats["lidar_roofs"] = {
        "source": report.source,
        "buildings": len(outcomes),
        "core_buildings": len(outcomes) - counts["scenic"],
        FLAT: counts[FLAT], PITCHED: counts[PITCHED],
        "unclassified": {reason: counts[reason] for reason in REASONS},
    }
    if counts[FLAT] or counts[PITCHED]:
        lines = {(a["text"], a["license"]): a for a in detailed.extra_attribution}
        lines.setdefault((report.attribution, report.license), {"text": report.attribution, "license": report.license})
        detailed.extra_attribution = [dict(lines[key]) for key in sorted(lines)]


def _lent_colour(lend: Mapping[str, Any] | None) -> str | None:
    """The colour of one lent Overture value, or ``None`` when the key has none."""
    return None if lend is None else str(lend["colour"])


def _lent_by_key(ids: Sequence[str], key_of: Mapping[str, str], tints: Sequence[Mapping[str, Any]]) -> tuple[
        dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """The Overture facade and roof colour of each ``appearance_key`` (plan C2), each with the evidence it came with.

    The parts of one parcel share their wall and roof, so a colour lent to one part is the parcel's: the first part
    in id order with a facade colour lends it, and apart from that, the first with a roof colour.
    """
    by_id = {str(t["building_id"]): t for t in tints}
    walls: dict[str, dict[str, Any]] = {}
    roofs: dict[str, dict[str, Any]] = {}
    for bid in ids:
        entry = by_id.get(bid)
        if entry is None:
            continue
        for kind, found in (("facade", walls), ("roof", roofs)):
            if kind in entry and key_of[bid] not in found:
                found[key_of[bid]] = {"gers_id": entry["gers_id"], "iou": entry["iou"], **entry[kind]}
    return walls, roofs


def _tint_evidence(lend: Mapping[str, Any]) -> dict[str, Any]:
    """``facade.materials.wall_evidence`` / ``roof_evidence``: the Overture record a lent colour came from."""
    return {"provider": "overture", "gers_id": lend["gers_id"], "dataset": lend["dataset"], "license": lend["license"],
            "origin": "observed", "colour": lend["colour"], "iou": lend["iou"]}


def tint_source(record: Mapping[str, Any]) -> str | None:
    """``overture`` when a building's tints all come from Overture colours (plan C2), else ``None``.

    The visual node carries it, so ``panoramax apply`` can say why it leaves the wall alone (``overture_colour``).
    """
    materials = (record.get("facade") or {}).get("materials") or {}
    sources = {materials.get("wall_source"), materials.get("roof_source")}
    if sources & {OVERTURE_FACADE, OVERTURE_ROOF} and not sources & {"building:colour", "roof:colour"}:
        return "overture"
    return None


def _ground_evidence(ground: str, template: Template, category: str, poi_ids: list[str],
                     catastro: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    """Why a ground floor is a shopfront: the Catastro use of the parcel when it ranked first (``_shop_sources``),
    else the building category, else OSM POIs.

    The Catastro use is observed for the parcel; that its commerce is on the ground floor is estimated.
    """
    if ground != "shopfront":
        return None
    if catastro is None:
        if template.ground == "shopfront":
            return {"source": "building_category", "category": category, "osm_poi_ids": poi_ids}
        return {"source": "osm_poi", "osm_poi_ids": poi_ids}
    return {
        "source": "catastro_use", "osm_poi_ids": [],
        "current_use": catastro["current_use"], "refcat": catastro["refcat"], "share": catastro["share"],
        "use_origin": catastro["attribute_origin"], "floor_assignment": "estimated",
        "rule": f"Catastro predominant use {catastro['current_use']}: commercial ground floor",
        "provider": catastro["provider"], "license": catastro["license"], "attribution": catastro["attribution"],
    }


def third_party_ground(evidence: dict[str, Any] | None) -> dict[str, Any] | None:
    """The ground floor evidence of a building whose best-ranked evidence is Overture places (ADR-021): listed apart from OSM POIs."""
    if evidence is None:
        return None
    return {"source": "overture_place", "overture_place_ids": evidence["osm_poi_ids"]}


def place_feature(place: Mapping[str, Any]) -> dict[str, Any]:
    """An ``overture match`` place as a POI feature of the local grid (``overture_places.json``)."""
    x, y = place["point_m"]
    return {"id": place["id"], "kind": "poi", "geometry": mapping(Point(x, y)), "tags": dict(place["tags"])}


def place_source(place: Mapping[str, Any]) -> dict[str, Any]:
    """Provenance of a third-party POI on the shop it gave: provider, dataset, licence and origin."""
    return {
        "provider": "overture", "gers_id": place["gers_id"], "dataset": place["dataset"], "license": place["license"],
        "confidence": place["confidence"], "attribute_origin": "observed", "observed_by": "third_party",
    }


def _third_party_record(detailed: DetailedScene, objects: Sequence[DetailObject], places: Sequence[Mapping[str, Any]], buildings: set[str]) -> None:
    """``counts.third_party_shops`` and one attribution line per (text, licence) of the places that gave a shop, sorted."""
    place_of = {p["id"]: p for p in places}
    used = [place_of[s["poi_id"]] for o in objects if o.type == "building" for s in o.record["facade"]["shops"] if s["poi_id"] in place_of]
    detailed.stats["third_party_shops"] = {
        "places": len(places), "buildings": len(buildings), "shops": len(used),
        "by_dataset": dict(sorted(Counter(p["dataset"] for p in used).items())),
    }
    lines = {(p["attribution"]["text"], p["attribution"]["license"]): p["attribution"] for p in used}
    detailed.extra_attribution = [dict(lines[key]) for key in sorted(lines)]


# Why a colour lent to a building's key did not decide its wall or roof (``counts.third_party_tints.not_used``).
TINT_NOT_USED = ("osm_tag", "landmark", "scenic", "estimated_roof", "no_tint_material")


def _tint_not_used(record: Mapping[str, Any], part: str, lent_source: str) -> str | None:
    """Why a building's ``part`` (``wall`` or ``roof``) did not take the colour lent to it, or ``None`` when it did."""
    # A mid building (plan D3) has a scenic wall but the roof of its plan, which a lent colour may decide.
    if record.get("tier") == SCENIC or (record.get("tier") == MID and part == "wall"):
        return "scenic"
    materials = record["facade"]["materials"]
    source = materials[f"{part}_source"]
    if source == lent_source:
        return None
    if part == "wall" and record["facade"]["needs_custom_model"]:
        return "landmark"
    if source is not None:
        return "osm_tag"
    if materials[f"{part}_origin"] == "estimated":
        return "estimated_roof"
    return "no_tint_material"


def _third_party_tints(detailed: DetailedScene, objects: Sequence[DetailObject], tints: Sequence[Mapping[str, Any]], ids: Sequence[str],
                       key_of: Mapping[str, str], lent: tuple[Mapping[str, Mapping[str, Any]], Mapping[str, Mapping[str, Any]]]) -> None:
    """``counts.third_party_tints`` (plan C2) and one attribution line per Overture dataset whose colour a building took.

    ``matched`` is the tints entries of this scene's buildings; ``walls`` and ``roofs`` the buildings (each Catastro part
    counts) whose material a lent colour decided; ``by_dataset`` those materials by dataset; ``not_used`` the lent colours
    that decided nothing, by reason (``TINT_NOT_USED``). The attribution merges these lines with the places' lines, sorted.
    """
    known = set(ids)
    not_used: Counter[str] = Counter(dict.fromkeys(TINT_NOT_USED, 0))
    used: Counter[str] = Counter()
    by_dataset: Counter[str] = Counter()
    lines = {(a["text"], a["license"]): a for a in detailed.extra_attribution}
    for o in objects:
        if o.type != "building":
            continue
        key = key_of[o.record["source_ids"][0]]
        for part, lent_source, by_key in (("wall", OVERTURE_FACADE, lent[0]), ("roof", OVERTURE_ROOF, lent[1])):
            lend = by_key.get(key)
            if lend is None:
                continue
            reason = _tint_not_used(o.record, part, lent_source)
            if reason is not None:
                not_used[reason] += 1
                continue
            used[part] += 1
            by_dataset[str(lend["dataset"])] += 1
            lines[(lend["attribution"]["text"], lend["attribution"]["license"])] = lend["attribution"]
    detailed.stats["third_party_tints"] = {
        "matched": sum(str(t["building_id"]) in known for t in tints),
        "walls": used["wall"], "roofs": used["roof"],
        "by_dataset": dict(sorted(by_dataset.items())),
        "not_used": dict(not_used),
    }
    detailed.extra_attribution = [dict(lines[key]) for key in sorted(lines)]


def _shops_of(poi_ids: list[str], feature_of: Mapping[str, Mapping[str, Any]], building_tags: Mapping[str, Any]) -> list[
        tuple[str | None, Mapping[str, Any], tuple[float, float] | None]]:
    """The shops of a shopfront building: its evidence POIs, or the building itself when it has none."""
    if not poi_ids:
        return [(None, building_tags, None)]
    points = {pid: shape(feature_of[pid]["geometry"]).representative_point() for pid in poi_ids}
    return [(pid, feature_of[pid].get("tags") or {}, (points[pid].x, points[pid].y)) for pid in poi_ids]


def _furnishes_shops(cfg: DetailConfig) -> bool:
    """Whether shop rooms get catalogue furniture: interiors, shop interiors and ``shops.furnish`` all on."""
    ic = cfg.interiors
    return ic.enabled and ic.shops.enabled and ic.shops.furnish.enabled


def _furnishing(cfg: DetailConfig) -> ShopFurnishing:
    """The map's furnishing pass, with the catalogue rows its sets name, read from ``assets/``."""
    furnish = cfg.interiors.shops.furnish
    try:
        catalogue = load_catalogue(furnish, REPO_ROOT / "assets")
    except FurnishError as exc:
        raise DetailError(str(exc)) from exc
    return ShopFurnishing.of(furnish, catalogue, cfg.seed)


def _furnishes_floors(cfg: DetailConfig) -> bool:
    """Whether the floors of the stair interior get catalogue furniture: interiors and ``interiors.furnish`` on (ADR-034)."""
    ic = cfg.interiors
    return ic.enabled and ic.furnish.enabled


def _floor_furnishing(cfg: DetailConfig) -> ShopFurnishing:
    """The map's pass for the floors of the stair interior, with the rows ``interiors.furnish`` names, read from ``assets/``."""
    furnish = cfg.interiors.furnish
    try:
        catalogue = load_catalogue(furnish, REPO_ROOT / "assets", FLOOR_SCOPE)
    except FurnishError as exc:
        raise DetailError(str(exc)) from exc
    return ShopFurnishing.of_floors(furnish, catalogue, cfg.seed)


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


def _select_interiors(candidates: list[tuple[str, int, Mapping[str, Any], BaseGeometry]], cfg: DetailConfig, issues: list[Issue],
                      reported: Collection[str] = ()) -> list[str]:
    """Interior queue. ``candidates`` are ``(id, storeys drawn, heights record, footprint)``: the rule reads the storeys the facade shows.

    ``reported`` are buildings left out with an issue of their own (across the core edge): an ``include_ids`` entry naming one is not unknown.
    """
    ic = cfg.interiors
    if interior_quota(cfg) == 0:
        return []
    known = {bid for bid, _, _, _ in candidates}
    ordered: list[str] = []
    for bid in ic.include_ids:
        if bid in known:
            ordered.append(bid)
        elif bid not in reported:
            issues.append(Issue("interior_include_unknown", "warning", bid, "include_ids entry is not a generated building"))
    rule = ic.rule
    ruled = []
    for bid, levels, h, geom in candidates:
        if (h.get("category") in rule.categories and not h.get("landmark") and rule.min_levels <= levels <= rule.max_levels
                and rule.min_area_m2 <= geom.area <= rule.max_area_m2 and geom.geom_type == "Polygon"
                and rectangularity(geom) >= rule.min_rectangularity):
            ruled.append(bid)
    ruled.sort(key=lambda b: hashlib.sha256(f"{cfg.seed}:interior:{b}".encode()).hexdigest())
    return [b for b in dict.fromkeys([*ordered, *ruled]) if b not in ic.exclude_ids]


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


# Why a core building gets no interior: part of its footprint lies past the core edge.
CROSSES_CORE_EDGE = "crosses_core_edge"
MIN_PART_WALL_M = 0.5  # a part's roof leaves at least this much wall above its base
# Issue codes of a building:part that is not drawn.
PART_SKIPS = frozenset({
    "building_part_no_height", "building_part_height_order", "building_part_outside_footprint",
    "building_part_not_closed", "building_part_hidden", "building_part_mesh_not_closed",
})


def _source(tagged: tuple[str, str] | None, origin: str) -> str | None:
    """The OSM tag that chose an observed material, or ``None`` for a generated one."""
    return tagged[1] if tagged is not None and origin == "observed" else None


def _part_polygon(geometry: Mapping[str, Any]) -> BaseGeometry | None:
    """The part's polygon: its largest polygon, or the ring of a closed way (``geo`` keeps a part as a line)."""
    geom = shape(geometry)
    if geom.geom_type == "LineString":
        coords = list(geom.coords)
        if len(coords) < 4 or coords[0] != coords[-1]:
            return None
        geom = Polygon(coords).buffer(0)
    return _largest_polygon(geom)


def _part_roof(
    clipped: BaseGeometry, tags: Mapping[str, str], materials: tuple[str, str], storey_m: float, height: float, min_h: float,
) -> tuple[float, list[Primitive], dict[str, Any], str | None]:
    """``(eave height, roof primitives, roof record, fallback reason)`` of a part whose ``height`` includes its roof.

    ``materials`` is ``(wall, roof)``; a gable piece takes the wall.
    """
    wall, material = materials
    flat_roof = [Primitive(cap(clipped, height), material)]
    spec = pitched_spec(tags, material, storey_m)
    if spec is None:
        return height, flat_roof, {"type": "flat"}, None
    built = build_roof(clipped, 0.0, spec)
    room = height - min_h - MIN_PART_WALL_M
    if isinstance(built, BuiltRoof) and built.rise_m > room:  # pragma: no mutate: at a rise of exactly the room the rebuilt roof is the same
        built = build_roof(clipped, 0.0, with_rise(spec, room)) if room >= MIN_RISE_M else "no_room_under_height"  # pragma: no mutate: a room of exactly 0.1 m is a float coincidence
    if isinstance(built, str):
        return height, flat_roof, {"type": "flat"}, built
    eave = height - built.rise_m
    lift = np.array([0.0, 0.0, eave])
    roof = [Primitive(Mesh(built.roof.vertices + lift, built.roof.triangles), spec.material)]
    if built.gables.triangle_count:
        roof.append(Primitive(Mesh(built.gables.vertices + lift, built.gables.triangles), wall))
    return eave, roof, dict(built.record), None


# A generated lamp, bench or bin keeps off this much ground in front of a ground-floor door, and this far past each jamb.
# Barcelona Sarria: a bench stood 0.47 m in front of a shop door, across its whole width.
DOOR_CLEAR_M = 1.5
DOOR_SIDE_CLEAR_M = 0.3


def _ground_doors(holes: Sequence[Hole]) -> list[Hole]:
    """The ground-floor doors: a shop room's own door is an ``interior_door``."""
    return [h for h in holes if h.storey == 0 and h.kind in DOOR_KINDS]


def _door_centres(holes: Sequence[Hole], segments: Sequence[Segment]) -> list[list[float]]:
    """``facade.doors_m``: the middle of each ground-floor door on the wall line, ``[x, y]`` in metres."""
    return [[round(float(c), 3) for c in segments[h.segment].point(h.s0 + h.width / 2)[:2]] for h in _ground_doors(holes)]


def regional_style(base: GeneratedScene) -> str:
    """The regional facade style of the map's centre (``materials.trim_style.select_style``), as the materials stage
    resolves it by default; ``generic`` without a centre. A map's own ``facades.style`` does not reach detail."""
    from onward_generator.materials.trim_style import load_style_table, select_style  # materials imports detail; load it on use

    origin = (base.manifest().get("frame") or {}).get("origin_wgs84")
    if not origin:
        return "generic"
    return select_style(load_style_table(), float(origin[1]), float(origin[0]))


def mansard_for(haussmann: bool, interior: bool, landmark: bool, storeys: Any, template: Template) -> Mansard | None:
    """Plan Q3b: on a Haussmann map a building of at least ``MANSARD_MIN_STOREYS`` storeys (``count`` and ``height_m``)
    with a flat roof, no interior and no landmark keeps its height and turns its top storey into a zinc mansard."""
    if (haussmann and not interior and not landmark and storeys.count >= MANSARD_MIN_STOREYS
            and template.pitched is None and template.hip is None):
        return Mansard(storeys.height_m)
    return None


def _balcony_columns(layout: Layout, spec: Any) -> list[tuple[int, float, float]]:
    """Each bay balcony of ``layout`` as ``(segment, s0, s1)`` on its wall."""
    if spec is None:  # pragma: no mutate: a template without a balcony lays out none
        return []
    return [(seg, bs0, bs0 + spec.width_m) for seg, bs0, _ in layout.balconies]


def _balcony_spans(balconies: Sequence[tuple[int, float, float]], segments: Sequence[Segment],
                   filants: Sequence[tuple[int, float, float]] = ()) -> list[list[list[float]]]:
    """``facade.balconies_m``: each column of balconies (``(segment, s0, s1)``) and each balcon filant as its span on the
    wall line, ``[[x, y], [x, y]]`` in metres, once however many storeys it has. ``perf.layout.climb_keep_out`` keeps
    ladders and cabins off them."""
    spans = {
        tuple(tuple(round(float(c), 3) for c in segments[seg].point(s)[:2]) for s in (s0, s1))
        for seg, s0, s1 in (*balconies, *filants)
    }
    return [[list(a), list(b)] for a, b in sorted(spans)]


def _door_fronts(holes: Sequence[Hole], segments: Sequence[Segment]) -> list[Polygon]:
    """The ground in front of each ground-floor door, outside the wall."""
    fronts = []
    for h in _ground_doors(holes):
        seg = segments[h.segment]
        a, b = seg.point(h.s0 - DOOR_SIDE_CLEAR_M)[:2], seg.point(h.s0 + h.width + DOOR_SIDE_CLEAR_M)[:2]
        out = seg.n_out[:2] * DOOR_CLEAR_M
        fronts.append(Polygon([a, b, b + out, a + out]))
    return fronts


# A part edge that lies within this of its building's outline along its whole length stands on that outline.
PART_OUTLINE_M = 0.01


def _part_walls(part: BaseGeometry, outline: BaseGeometry, base: float, rim: float, eave: float) -> Mesh:
    """A part's walls from ``base`` to ``eave``; on the edges it shares with its building's outline, from ``rim`` up.

    ``rim`` is the top of the building's own wall, its parapet included: below it that wall stands in the same
    plane, and on Barcelona Sarria the two z-fought along the 0.7 m of parapet.
    """
    if base >= eave:
        return Mesh.empty()
    near = outline.buffer(PART_OUTLINE_M)
    raised_from = max(base, rim)
    # Beside a raised edge an inner face gets a corner at ``rim`` too, so the two walls share their vertical edge.
    corner = rim if base < rim < eave else None
    collapsed, _ = collapse_short_edges(part)
    faces = []
    for polygon in polygons_of(collapsed):
        for ring in (polygon.exterior, *polygon.interiors):
            edges = list(pairwise(ring.coords))
            shared = [near.covers(LineString(edge)) for edge in edges]
            for i, (a, b) in enumerate(edges):
                if not shared[i]:
                    faces.append(_edge_face(a, b, base, eave, corner, shared[i - 1], shared[(i + 1) % len(edges)]))
                elif raised_from < eave:
                    faces.append(quad((*a, raised_from), (*b, raised_from), (*b, eave), (*a, eave)))
    return merge(faces)


def _edge_face(
    a: tuple[float, float], b: tuple[float, float], z0: float, z1: float, rim: float | None, at_a: bool, at_b: bool,
) -> Mesh:
    """The wall face a→b from ``z0`` to ``z1``, outward like :func:`quad`, with a vertex at ``rim`` on the ``at_a``/``at_b`` sides."""
    a0, b0, b1, a1 = (*a, z0), (*b, z0), (*b, z1), (*a, z1)
    if rim is None or not (at_a or at_b):
        return quad(a0, b0, b1, a1)
    ar, br = (*a, rim), (*b, rim)
    if at_a and at_b:
        return merge([quad(a0, b0, br, ar), quad(ar, br, b1, a1)])
    vertices, triangles = ([a0, b0, b1, a1, ar], [(0, 1, 4), (4, 1, 2), (4, 2, 3)]) if at_a else \
        ([a0, b0, b1, a1, br], [(0, 1, 4), (0, 4, 2), (0, 2, 3)])
    return Mesh(np.array(vertices), np.array(triangles))


def _building_parts(
    features: list[Mapping[str, Any]],
    footprints: Mapping[str, BaseGeometry],
    cfg: DetailConfig,
    *,
    storey_of: Callable[[str], tuple[float, str]],
    top_of: Mapping[str, float],
    parapet_of: Mapping[str, float],
) -> tuple[list[DetailObject], list[Issue]]:
    """Extrude OSM ``building:part`` polygons, clipped to the building footprint they overlap most.

    The part's height is its ``height`` tag, else its levels times the storey height of that building's
    category (``storey_of``: storey metres and category). A part no higher than that building
    (``top_of``) is inside it and is not drawn; a higher one draws its walls from that top up, and from the top
    of the building's parapet (``parapet_of``, metres) where it stands on the building's outline. ``roof:*``
    tags shape its roof under its height.
    """
    from shapely.geometry.polygon import orient

    objects: list[DetailObject] = []
    issues: list[Issue] = []
    template = cfg.template_for("unknown", False)
    for f in features:
        tags = dict(f.get("tags") or {})
        if "building:part" not in tags or f.get("kind") == "building":
            continue
        fid = str(f["id"])
        geom = _part_polygon(f["geometry"])
        if geom is None:
            issues.append(Issue("building_part_not_closed", "info", fid, "building:part is not a closed ring or polygon; skipped"))
            continue
        if geom.area < 1.0:
            continue
        parent: str | None = None
        best = 0.0
        clipped = geom
        for bid, fp in footprints.items():
            inter = _largest_polygon(geom.intersection(fp))
            if inter is not None and inter.area > best:
                best, parent, clipped = inter.area, bid, inter
        if parent is None or clipped.area < 1.0:
            issues.append(Issue("building_part_outside_footprint", "info", fid,
                                "building:part does not overlap a building footprint; skipped"))
            continue
        storey_m, category = storey_of(parent)
        measured = part_height(tags, storey_m, category)
        if measured is None:
            issues.append(Issue("building_part_no_height", "info", fid, "building:part without a parseable height or building:levels; skipped"))
            continue
        height, min_h = measured.height_m, measured.min_height_m
        if min_h >= height:
            issues.append(Issue("building_part_height_order", "warning", fid, "min_height is not below height; skipped"))
            continue
        if height <= top_of[parent] + 1e-6:  # pragma: no mutate: a height of exactly the building's plus 1e-6 m is a float coincidence
            issues.append(Issue("building_part_hidden", "info", fid,
                                f"{height:.2f} m is not above building {parent} ({top_of[parent]:.2f} m); skipped"))
            continue
        clipped = orient(clipped, sign=1.0)
        wall, wall_origin = choose_wall(tags, template.wall_materials, cfg.seed, fid, colours=cfg.materials)
        tagged = tagged_roof(tags, cfg.materials)
        roof_material = tagged[0] if tagged else "roof_tile"
        eave, roof, roof_record, fallback = _part_roof(clipped, tags, (wall, roof_material), storey_m, height, min_h)
        if fallback is not None:
            issues.append(Issue("roof_shape_fallback", "info", fid, f"roof:shape={tags.get('roof:shape')} drawn flat: {fallback}"))
            roof_record["fallback"] = fallback
        collider = prism(clipped, min_h, eave, bottom=True)
        defects = closed_manifold_defects(collider)
        if defects:
            issues.append(Issue("building_part_mesh_not_closed", "warning", fid, f"{defects} collider defects; skipped"))
            continue
        part_id = f"building_part/{parent}/{f['id']}"
        # Below the building's top the part is inside it, and its walls on the shared outline stood over the
        # facade: on Barcelona Sarria they hid 11 shopfronts and z-fought with the walls they covered.
        base = max(min_h, top_of[parent])
        part_walls = _part_walls(clipped, footprints[parent].boundary, base, top_of[parent] + parapet_of.get(parent, 0.0), eave)
        visual = [*([Primitive(part_walls, wall)] if part_walls.triangle_count else []), *roof]
        objects.append(DetailObject(
            part_id, "building_part", visual, [collider],
            {
                "id": part_id,
                "type": "building_part",
                "source_ids": [parent, f["id"]],
                "representation": "osm_building_part",
                "attribute_origin": "observed",
                "part": tags.get("building:part"),
                "height": measured.to_record() | {"eave_m": round(eave, 3)},
                "roof": roof_record | {"material": roof_material, "roof_shape": tags.get("roof:shape"),
                                       "origin": "observed" if tags.get("roof:shape") or tagged else "generated"},
                "materials": {"wall": wall, "wall_origin": wall_origin, "wall_source": _source(tagged_wall(tags, cfg.materials), wall_origin),
                              "roof": roof_material, "roof_origin": "observed" if tagged else "generated",
                              "roof_source": tagged[1] if tagged else None},
                "visual": {"triangles": sum(p.mesh.triangle_count for p in visual)},
            },
        ))
    return objects, issues


def _scenic_building(o: Any, geom: BaseGeometry, landmark: bool, use: FacadeUse, cfg: DetailConfig) -> DetailObject:
    """A scenic-tier building (ADR-017): its footprint extruded to its height, walls and a flat roof cap, no collider.

    No openings, doors, parapets, hip roofs, towers, interiors or shops. The wall is one of
    the shared scenic materials and the roof is ``SCENIC_ROOF``, so a ring cell is a few draws.
    """
    bid = o.record["source_ids"][0]
    height = float(o.record["height"]["height_m"])
    wall = scenic_wall(cfg.seed, bid)
    visual = [Primitive(walls(geom, 0.0, height), wall), Primitive(cap(geom, height), SCENIC_ROOF)]
    roof = {"type": "flat", "material": SCENIC_ROOF, "origin": "generated", "roof_shape": "flat"}
    record = {
        # The phase-03 checks describe the phase-03 mesh; every other key below replaces its phase-03 value.
        **{k: v for k, v in o.record.items() if k != "checks"},
        "tier": SCENIC,
        "generation": {"method": "phase03_volume + scenic shell", "phase03": o.record.get("generation"),
                       "template_config_version": cfg.version, "seed": cfg.seed},
        "facade": {
            "template": "scenic_shell",
            "representation": "scenic_shell",
            "representative_of_real_facade": False,
            "attribute_origin": "generated",
            "note": FIDELITY_NOTE,
            "needs_custom_model": landmark,
            "ground_floor": "none",
            "openings": {},
            "shops": [],
            "materials": {"wall": wall, "wall_origin": "generated", "roof": SCENIC_ROOF, "roof_origin": "generated"},
            "use": use.to_record(),
            "door": "none",
            "doors_m": [],
            "balconies_m": [],
        },
        "roof": roof,
        "interior": False,
        "materials": [p.material for p in visual],
        "visual": {"triangles": sum(p.mesh.triangle_count for p in visual)},
        "collider": {"source": "none_scenic", "parts": 0, "triangles": 0, "balconies": 0, "glass_panes": 0},
    }
    return DetailObject(o.id, "building", visual, [], record)


def _mid_building(o: Any, geom: BaseGeometry, others: BaseGeometry, levels: int | None, landmark: bool, use: FacadeUse,
                  plan: RoofPlan, roof_materials: Mapping[str, Any], lidar: Mapping[str, object] | None,
                  cfg: DetailConfig) -> tuple[DetailObject, list[Issue]]:
    """A mid-tier building (plan D3): the footprint extruded to its height in its scenic wall, painted windows on its
    exposed walls and the roof form of its ``plan``, at most MID_MAX_TRIANGLES visual triangles, no collider.

    No doors, balconies, parapets, towers, interiors or shops. ``roof_materials`` holds the roof's material
    origin and source (and the lent colour's evidence), as a core building's; ``lidar`` is the nDSM evidence
    when the plan's roof form came from it.
    """
    bid = o.record["source_ids"][0]
    height = float(o.record["height"]["height_m"])
    wall = scenic_wall(cfg.seed, bid)
    template, roof_material, roof_origin, roof_shape = plan
    refined, segments = refine_exposure(geom, others, cfg)
    storeys = storeys_for(height, levels, landmark, cfg)
    shell = build_mid_shell(refined, segments, storeys, height, template, wall, roof_material, windows=not landmark)
    visual = [Primitive(mesh, material) for material, mesh in shell.parts.items() if mesh.triangle_count]
    roof: dict[str, Any] = {**shell.roof, "origin": roof_origin, "roof_shape": roof_shape}
    if lidar is not None:
        roof["origin"] = "estimated"
        roof["lidar"] = dict(lidar)
    issues = []
    if "fallback" in roof:
        issues.append(Issue("roof_shape_fallback", "info", bid, f"mid shell roof drawn flat: {roof['fallback']}"))
    if shell.defects:
        issues.append(Issue("facade_mesh_not_watertight", "critical", bid, f"mid shell: {shell.defects} open or non-manifold edges"))
    record = {
        **{k: v for k, v in o.record.items() if k != "checks"},
        "tier": MID,
        "generation": {"method": "phase03_volume + mid shell", "phase03": o.record.get("generation"),
                       "template_config_version": cfg.version, "seed": cfg.seed},
        "facade": {
            "template": MID_REPRESENTATION,
            "representation": MID_REPRESENTATION,
            "representative_of_real_facade": False,
            "attribute_origin": "generated",
            "note": FIDELITY_NOTE,
            "needs_custom_model": landmark,
            "ground_floor": "none",
            "storeys": {"count": storeys.count, "height_m": round(storeys.height_m, 3), "origin": storeys.origin},
            "openings": {},
            "mid": {"windows": shell.windows, "exposed_faces": shell.exposed_faces, "cap": MID_MAX_TRIANGLES},
            "shops": [],
            "materials": {"wall": wall, "wall_origin": "generated", "roof": roof["material"], **roof_materials},
            "use": use.to_record(),
            "door": "none",
            "doors_m": [],
            "balconies_m": [],
        },
        "roof": roof,
        "interior": False,
        "materials": [p.material for p in visual],
        "visual": {"triangles": sum(p.mesh.triangle_count for p in visual)},
        "collider": {"source": "none_mid", "parts": 0, "triangles": 0, "balconies": 0, "glass_panes": 0},
        "checks": {"facade_open_edges": shell.defects},
    }
    frame = wall_uv_frame(segments, storeys, levels)
    return DetailObject(o.id, "building", visual, [], record, uv_frame=frame), issues


def _tier_counts(objects: list[DetailObject], dropped_elements: int, *, mid: bool) -> dict[str, Any]:
    """Buildings, visual and collision triangles of the objects that carry a tier (ADR-017).

    With the intermediate ring (``mid``, plan D3) the ``mid`` tier is added, with the visual triangles of each
    mid building's own shell (``triangles_per_building``: mean to 0.1, min and max; null without a mid building).
    """
    counts: dict[str, Any] = {t: {"buildings": 0, "visual_triangles": 0, "collision_triangles": 0} for t in ((CORE, SCENIC, MID) if mid else (CORE, SCENIC))}
    for x in (o for o in objects if "tier" in o.record):
        entry = counts[x.record["tier"]]
        entry["buildings"] += x.type == "building"
        entry["visual_triangles"] += sum(p.mesh.triangle_count for p in x.visual)
        entry["collision_triangles"] += sum(m.triangle_count for m in (*x.colliders, *x.glass_colliders))
    counts[SCENIC]["urban_elements_dropped"] = dropped_elements
    if mid:
        shells = [sum(p.mesh.triangle_count for p in x.visual) for x in objects if x.type == "building" and x.record.get("tier") == MID]
        counts[MID]["triangles_per_building"] = {
            "mean": round(sum(shells) / len(shells), 1) if shells else None,
            "min": min(shells, default=None),
            "max": max(shells, default=None),
        }
    return counts


ExtraUrban = Callable[
    [BaseGeometry, BaseGeometry, Mapping[str, Prototype], DetailConfig, list[Element]],
    tuple[list[Element], list[Rejected]],
]


def rejection_severity(rejected: Rejected) -> str:
    """``info`` for a generated attempt (its id holds ``#``) and for a lamp kept off its neighbour: neither loses
    observed data (plan Q5). ``warning`` for an observed element that finds no place."""
    return "info" if "#" in rejected.id or rejected.reason == "too_close_to_another_lamp" else "warning"


def detail_scene(
    base: GeneratedScene,
    base_ref: Mapping[str, Any],
    heights_path: str | Path,
    scene_path: str | Path,
    cfg: DetailConfig,
    *,
    prototypes: Mapping[str, Prototype] | None = None,
    extra_urban: ExtraUrban | None = None,
    core_half_side_m: float | None = None,
    places: Sequence[Mapping[str, Any]] = (),
    typology: TypologyConfig | None = None,
    tints: Sequence[Mapping[str, Any]] | None = None,
    roof_spread: SpreadReport | None = None,
    mid_ring_m: float | None = None,
    lod2_landmarks: Lod2Index | None = None,
) -> DetailedScene:
    """Detail every building of ``base``. With a core square of ``core_half_side_m`` (ADR-017), a building
    outside it is a scenic shell, urban elements outside it are dropped and the ground colliders end at its edge.

    ``places`` are third-party POIs from ``overture_places.json`` (ADR-021): shop evidence ``overture``,
    ranked by ``config/sources.json`` ``evidence`` (ADR-022). ``typology`` (default ``config/typology.default.json``)
    names each building's typology and era (ADR-024); it changes nothing that is drawn. ``tints`` are the entries of
    ``overture_tints.json`` (plan C2): Overture colours a wall or roof takes when no OSM tag names it. ``None``
    (the default) reads none and adds no count. ``roof_spread`` is a raster plugin's report (plan C3): a building
    without a roof tag takes a flat or pitched roof from its nDSM spread (``lidar_roofs.classify``). ``None`` (the
    default) reads none, adds no key and draws the roofs as before. ``mid_ring_m`` (plan D3, needs a core) makes a building
    outside the core that meets a band that wide around it a ``mid`` shell (``tiers.ring_tier``); ``None`` (the default)
    adds no tier and no key. ``lod2_landmarks`` (plan D6, ADR-008 amendment) is a checked ``lod2_index.json``: a core
    landmark whose footprint it matches is drawn with the observed LoD2 volume clipped to that footprint instead of the
    placeholder shell and its tower (``detail.lod2``); ``None`` (the default) reads none and adds no key."""
    if mid_ring_m is not None:
        if core_half_side_m is None:
            raise CoreError("mid_ring_m needs a core (core_half_side_m)")
        check_mid_ring(mid_ring_m)
    tcfg = typology or load_typology_config()
    core = core_square(core_half_side_m) if core_half_side_m is not None else None
    mid = mid_square(core_half_side_m, mid_ring_m) if core_half_side_m is not None and mid_ring_m is not None else None
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
    place_evidence = _shop_evidence([place_feature(p) for p in places], footprints, cfg)
    place_of = {p["id"]: p for p in places}
    feature_of.update({p["id"]: place_feature(p) for p in places})
    key_of = {bid: appearance_key(bid, feature_of[bid]) for bid in ids}
    lent_walls, lent_roofs = _lent_by_key(ids, key_of, tints or ())
    facade_walls = _facade_walls(ids, footprints, tree, feature_of, height_of, features, cfg)  # only the use is read from this pass
    heights_m = {o.record["source_ids"][0]: float(o.record["height"]["height_m"]) for o in building_objs}
    templates = _templates(ids, height_of, cfg)
    ranking = load_source_config().evidence
    typologies = building_typologies(ids, footprints, tree, key_of, feature_of, height_of, heights_m,
                                     {bid: facade_walls[bid][0] for bid in ids}, ranking, cfg, tcfg, issues)
    facade_styles = {bid: facade_style(t["typology"], t["inputs"]["period"], tcfg) for bid, t in typologies.items()}
    # The use comes from the walls above; drawing them again with the palettes of the styles keeps the neighbour rule.
    facade_walls = _facade_walls(ids, footprints, tree, feature_of, height_of, features, cfg, {bid: style.palette for bid, style in facade_styles.items()},
                                 lent_walls)
    sources = _shop_sources(ids, feature_of, evidence, templates, cfg, ranking,
                            lambda bid: _shop_fits(bid, ids, footprints, tree, cfg), place_evidence)
    catastro = {bid: feature_of[bid]["catastro_use"] for bid, source in sources.items() if source == CATASTRO_ID}
    third_party = {bid for bid, source in sources.items() if source == OVERTURE_ID}
    for bid in catastro:  # the POIs of a building whose Catastro use ranked above them are not its shops
        evidence.pop(bid, None)
    for bid in third_party:  # its Overture places ranked first: they are its shops (ADR-021)
        evidence[bid] = place_evidence[bid]
    # One roof:levels is a storey of the building, a landmark's too: its single facade band is not a storey.
    storey_heights = {bid: storeys_for(heights_m[bid], height_of[bid].get("estimated_levels"), False, cfg).height_m for bid in ids}  # pragma: no mutate: storeys_for reads landmark as a truth value, so None is False
    tags_of = {bid: dict(feature_of[bid].get("tags") or {}) for bid in ids}
    tier_of = {bid: ring_tier(footprints[bid], core, mid) for bid in ids}
    lod2_built, lod2_issues = _lod2_inputs(lod2_landmarks, ids, footprints, height_of, tier_of, scene.get("frame") or {}, cfg)
    lidar_classes, lidar_plans, lidar_evidence = _lidar_inputs(
        roof_spread, _lidar_classes(ids, footprints, tree, key_of, tags_of, heights_m, tier_of, roof_spread, cfg),
    ) if roof_spread is not None else ({}, {}, {})
    roof_plans = _roof_plans(ids, footprints, tree, key_of, tags_of, templates, heights_m, cfg, storey_heights, lent_roofs, lidar_plans)
    setbacks: Counter[str] = Counter()

    # An interior needs the whole building inside the core: past its edge the ground colliders end and the
    # perimeter wall stands, so a room there has no floor and a stair there cannot be reached.
    whole_in_core = {bid for bid in ids if core is None or core.covers(footprints[bid])}
    candidates = [(bid, storeys_for(heights_m[bid], height_of[bid].get("estimated_levels"), False, cfg).count, height_of[bid], footprints[bid])  # pragma: no mutate: storeys_for reads its third argument only for its truth
                  for bid in ids if tier_of[bid] == CORE and bid in whole_in_core]
    edge = {bid for bid in cfg.interiors.include_ids if bid in tier_of and bid not in whole_in_core}
    issues.extend(Issue("interior_not_generated", "info", bid, CROSSES_CORE_EDGE) for bid in sorted(edge))
    interior_queue = _select_interiors(candidates, cfg, issues, edge)
    interiors_left = interior_quota(cfg)

    objects: list[DetailObject] = []
    counts: Counter[str] = Counter()
    templates_used: Counter[str] = Counter()
    roof_types: Counter[str] = Counter()
    roof_shapes: Counter[str] = Counter()
    door_rules: Counter[str] = Counter()
    flat_openings = 0
    interiors_built: list[str] = []
    shop_interiors: list[str] = []
    budgets = {False: InteriorBudget.of_stairs(cfg), True: InteriorBudget.of_shops(cfg)}
    lit: list[tuple[bool, dict[str, Any]]] = []
    furnishing = _furnishing(cfg) if _furnishes_shops(cfg) else None
    floor_furnishing = _floor_furnishing(cfg) if _furnishes_floors(cfg) else None
    door_fronts: list[Polygon] = []
    every_footprint = union_all(list(footprints.values()))  # what a blade sign must not face (``plan_shops``)
    exposed_m = party_m = 0.0

    # Buildings in the interior queue go first, in queue order, so the quota and the triangle budget go
    # to the sha256 order and not to the id order. The others follow nearest the map centre first, so the
    # shop budget goes to the shops a player meets first (a shop never takes the queue). Objects and issues are put back in building order below.
    rank = {bid: k for k, bid in enumerate(interior_queue)}

    def queue_key(b: Any) -> tuple[int, float]:
        bid = b.record["source_ids"][0]
        centre = footprints[bid].centroid
        sells = evidence.get(bid) or bid in catastro
        return (len(rank) if sells else rank.get(bid, len(rank))), float(np.hypot(centre.x, centre.y))

    order = sorted(building_objs, key=queue_key)
    leading_issues = len(issues)
    emitted: dict[str, tuple[list[DetailObject], list[Issue]]] = {}
    core_shells: dict[str, tuple[Shell, list[Segment], DetailObject]] = {}

    return _detail_buildings(
        base=base,
        base_ref=base_ref,
        cfg=cfg,
        tcfg=tcfg,
        prototypes=prototypes,
        extra_urban=extra_urban,
        core_half_side_m=core_half_side_m,
        mid_ring_m=mid_ring_m,
        places=places,
        tints=tints,
        roof_spread=roof_spread,
        lod2_landmarks=lod2_landmarks,
        lod2_built=lod2_built,
        lod2_issues=lod2_issues,
        core=core,
        heights=heights,
        height_of=height_of,
        features=features,
        feature_of=feature_of,
        place_of=place_of,
        footprints=footprints,
        ids=ids,
        tree=tree,
        key_of=key_of,
        tier_of=tier_of,
        building_objs=building_objs,
        order=order,
        heights_m=heights_m,
        templates=templates,
        facade_walls=facade_walls,
        typologies=typologies,
        facade_styles=facade_styles,
        lent_walls=lent_walls,
        lent_roofs=lent_roofs,
        roof_plans=roof_plans,
        lidar_classes=lidar_classes,
        lidar_evidence=lidar_evidence,
        evidence=evidence,
        catastro=catastro,
        third_party=third_party,
        whole_in_core=whole_in_core,
        interior_queue=interior_queue,
        interiors_left=interiors_left,
        objects=objects,
        issues=issues,
        leading_issues=leading_issues,
        emitted=emitted,
        core_shells=core_shells,
        lit=lit,
        every_footprint=every_footprint,
        door_fronts=door_fronts,
        counts=counts,
        templates_used=templates_used,
        roof_types=roof_types,
        roof_shapes=roof_shapes,
        door_rules=door_rules,
        setbacks=setbacks,
        interiors_built=interiors_built,
        shop_interiors=shop_interiors,
        budgets=budgets,
        furnishing=furnishing,
        floor_furnishing=floor_furnishing,
        flat_openings=flat_openings,
        exposed_m=exposed_m,
        party_m=party_m,
    )


@dataclass(slots=True)
class _PendingFurnish:
    """A built interior waiting for its furniture: the budget goes to the interiors nearest the core centre first."""

    shop: bool
    bid: str
    interior: InteriorPlan
    built: Interior
    penetrations: list[tuple[Segment, Hole]]
    key: str
    distance_m: float
    obj: DetailObject | None = None
    lit_index: int = -1


def _furnish_by_priority(pending: Sequence[_PendingFurnish], furnishing: ShopFurnishing | None, floor_furnishing: ShopFurnishing | None,
                         cfg: DetailConfig, lit: list[tuple[bool, dict[str, Any]]],
                         emitted: dict[str, tuple[list[DetailObject], list[Issue]]]) -> None:
    """Furnish the waiting interiors nearest the map centre first (ADR-031 amendment of 10 November 2026).

    The map centre is the centre of the playable core, where ``perf layout`` later puts the spawns, the routes and the
    Uplink; the routes themselves do not exist yet when ``detail`` runs. Each pass keeps its own budget, whole rooms
    or none. A furnished interior gets its new light anchors on its object and in ``lit``, before ``cap_lights``.
    """
    for entry in sorted(pending, key=lambda e: (e.distance_m, e.bid)):
        passer = furnishing if entry.shop else floor_furnishing
        assert passer is not None
        furnish = passer.furnish_interior if entry.shop else passer.furnish_floors
        code = "shop_not_furnished" if entry.shop else "floor_not_furnished"
        messages = furnish(entry.bid, entry.interior, entry.built, entry.penetrations, cfg.interiors)
        if entry.obj is not None and entry.built.light_anchors is not None:  # pragma: no mutate: entry.obj is always set before furnishing; the check is for mypy
            entry.obj.light_anchors = entry.built.light_anchors
            lit[entry.lit_index] = (entry.shop, entry.built.light_anchors)
        emitted[entry.key][1].extend(Issue(code, "info", entry.bid, message) for message in messages)


def _detail_buildings(
    *,
    base: GeneratedScene,
    base_ref: Mapping[str, Any],
    cfg: DetailConfig,
    tcfg: TypologyConfig,
    prototypes: Mapping[str, Prototype] | None,
    extra_urban: ExtraUrban | None,
    core_half_side_m: float | None,
    mid_ring_m: float | None,
    places: Sequence[Mapping[str, Any]],
    tints: Sequence[Mapping[str, Any]] | None,
    roof_spread: SpreadReport | None,
    lod2_landmarks: Lod2Index | None,
    lod2_built: dict[str, Lod2Part],
    lod2_issues: dict[str, list[Issue]],
    core: Polygon | None,
    heights: dict[str, Any],
    height_of: dict[str, Any],
    features: list[Any],
    feature_of: dict[str, Any],
    place_of: dict[str, Mapping[str, Any]],
    footprints: dict[str, BaseGeometry],
    ids: list[str],
    tree: STRtree,
    key_of: dict[str, str],
    tier_of: dict[str, str],
    building_objs: list[SceneObject],
    order: list[SceneObject],
    heights_m: dict[str, float],
    templates: dict[str, Template],
    facade_walls: dict[str, tuple[FacadeUse, str, str]],
    typologies: dict[str, dict[str, Any]],
    facade_styles: dict[str, FacadeStyle],
    lent_walls: dict[str, dict[str, Any]],
    lent_roofs: dict[str, dict[str, Any]],
    roof_plans: dict[str, RoofPlan],
    lidar_classes: dict[str, str],
    lidar_evidence: dict[str, dict[str, object]],
    evidence: dict[str, list[str]],
    catastro: dict[str, Any],
    third_party: set[str],
    whole_in_core: set[str],
    interior_queue: list[str],
    interiors_left: int,
    objects: list[DetailObject],
    issues: list[Issue],
    leading_issues: int,
    emitted: dict[str, tuple[list[DetailObject], list[Issue]]],
    core_shells: dict[str, tuple[Shell, list[Segment], DetailObject]],
    lit: list[tuple[bool, dict[str, Any]]],
    every_footprint: BaseGeometry,
    door_fronts: list[Polygon],
    counts: Counter[str],
    templates_used: Counter[str],
    roof_types: Counter[str],
    roof_shapes: Counter[str],
    door_rules: Counter[str],
    setbacks: Counter[str],
    interiors_built: list[str],
    shop_interiors: list[str],
    budgets: dict[bool, InteriorBudget],
    furnishing: ShopFurnishing | None,
    floor_furnishing: ShopFurnishing | None,
    flat_openings: int,
    exposed_m: float,
    party_m: float,
) -> DetailedScene:
    """The building loop of ``detail_scene``: each building in ``order`` drawn as a core, mid or scenic one, then ``_assemble_detail``.

    It is split from ``detail_scene`` only to keep each function short; it reads the values ``detail_scene`` built and changes none of its behaviour."""
    lod2_swap: set[str] = set()
    pending_furnish: list[_PendingFurnish] = []
    # Plan P0: a core wall facing no ground a player reaches is laid out plain (``reach.hidden_face``).
    reached = reached_ground(base) if core is not None else None
    # Plan Q3b: a map whose centre falls in the Haussmann box gets balcons filants on its core and ring street walls.
    haussmann = regional_style(base) == "haussmann"
    for o in order:
        first_object, first_issue = len(objects), len(issues)
        bid = o.record["source_ids"][0]
        geom = footprints[bid]
        h = height_of[bid]
        if tier_of[bid] == SCENIC:
            objects.append(_scenic_building(o, geom, bool(h.get("landmark")), facade_walls[bid][0], cfg))
            emitted[o.id] = (objects[first_object:], issues[first_issue:])
            continue
        if tier_of[bid] == MID:
            _, roof_material, roof_origin, _ = roof_plans[bid]
            lent_roof = lent_roofs.get(key_of[bid])
            roof_source = _source(tagged_roof(dict(feature_of[bid].get("tags") or {}), cfg.materials, lent=_lent_colour(lent_roof)), roof_origin)
            roof_materials: dict[str, Any] = {"roof_origin": roof_origin, "roof_source": roof_source}
            if roof_source == OVERTURE_ROOF and lent_roof is not None:
                roof_materials["roof_evidence"] = _tint_evidence(lent_roof)
            near = [ids[k] for k in tree.query(geom, predicate="dwithin", distance=cfg.probe_m) if ids[k] != bid]
            shell_obj, shell_issues = _mid_building(o, geom, union_all([footprints[n] for n in near]), h.get("estimated_levels"),
                                                    bool(h.get("landmark")), facade_walls[bid][0], roof_plans[bid], roof_materials,
                                                    lidar_evidence.get(bid), cfg)
            objects.append(shell_obj)
            issues.extend(shell_issues)
            emitted[o.id] = (objects[first_object:], issues[first_issue:])
            continue
        height = heights_m[bid]
        landmark = bool(h.get("landmark"))
        template = styled_template(templates[bid], facade_styles[bid])
        templates_used[template.name] += 1
        near = [ids[k] for k in tree.query(geom, predicate="dwithin", distance=cfg.probe_m) if ids[k] != bid]
        others = union_all([footprints[n] for n in near])
        refined, segments = refine_exposure(geom, others, cfg)
        floors = setback_floors(segments, [(footprints[n], heights_m[n] if key_of[n] == key_of[bid] else None) for n in near], height, cfg)
        if abs(refined.area - geom.area) > 1e-6 * max(1.0, geom.area):
            issues.append(Issue("refined_footprint_area_changed", "critical", bid, f"{refined.area - geom.area:.6f} m²"))
        storeys = storeys_for(height, h.get("estimated_levels"), landmark, cfg)
        ground = "shopfront" if (evidence.get(bid) or bid in catastro) and template.ground != "main_door_only" else template.ground
        main = exposed_frontage(segments)
        tags = dict(feature_of[bid].get("tags") or {})
        shop_tags = {**tags, CATASTRO_USE_TAG: catastro[bid]["current_use"]} if bid in catastro else tags
        use, wall_material, wall_origin = facade_walls[bid]
        shell_template, roof_material, roof_origin, roof_shape = roof_plans[bid]
        if roof_shape and roof_shape not in FLAT_SHAPES and shell_template.pitched is None:
            issues.append(Issue("roof_shape_unsupported", "info", bid, f"roof:shape={roof_shape} is not drawn; the template roof stays"))
        shop = ground == "shopfront"
        door_kind, door_spec = entrance(landmark, shop, cfg)
        plain_door = (door_kind, cfg.recess_depth_m, door_spec)
        previous_height = height
        plain_storeys = storeys

        def run_layout(door_kind: str, door_depth: float, door_opening: Any, storeys_used: Storeys) -> Layout:
            layout = Layout()
            for seg in segments:  # noqa: B023 - run_layout is called only inside this iteration, so it sees this building's values
                if seg.exposed or seg.index in floors:  # noqa: B023 - run_layout is called only inside this iteration, so it sees this building's values
                    layout_segment(seg, storeys_used, template, ground, door_opening if seg is main else None, door_kind, door_depth,  # noqa: B023 - run_layout is called only inside this iteration, so it sees this building's values
                                   bid, refined, others, cfg, layout, floors.get(seg.index, 0.0),  # noqa: B023 - run_layout is called only inside this iteration, so it sees this building's values
                                   hidden=reached is not None and seg.exposed and hidden_face(seg, reached),
                                   filants=filant_storeys(storeys_used.count) if haussmann and seg.exposed else frozenset())
            return layout

        interior = None
        layout = None
        ic = cfg.interiors
        # A shop gets a one-storey room behind its shopfront and never the stair interior. Neither uses the other's budget.
        # A shop above max_area_m2 has its room cut to a band behind the door's wall, closed by a back wall.
        sells = shop and ic.enabled and ic.shops.enabled and bid in whole_in_core
        if shop and ic.enabled and ic.shops.enabled and bid not in whole_in_core:
            issues.append(Issue("interior_not_generated", "info", bid, CROSSES_CORE_EDGE))
        depth = ic.shops.band_depth_m if shop and geom.area > ic.shops.max_area_m2 else None
        if main is None and not shop and bid in interior_queue and interiors_left > 0:
            issues.append(Issue("interior_not_generated", "info", bid, "no_street_frontage"))
        if main is not None and (sells or (not shop and bid in interior_queue and interiors_left > 0)):
            raised = interior_storey(height, storeys, h.get("height_source") or o.record["height"].get("height_source"))
            attempt_height, attempt_storeys = raised if raised is not None else (height, storeys)
            trial = run_layout("interior_door", ic.wall_thickness_m, ic.door, attempt_storeys)
            door = next((x for x in trial.holes if x.kind == "interior_door"), None)
            plan = plan_interior(
                bid, refined, door, main, attempt_height, attempt_storeys.height_m, attempt_storeys.count, cfg,
                roof_access=shell_template.pitched is None and shell_template.hip is None,
                max_floors=1 if shop else None,
                several_parts=shop,
                depth_m=depth,
                min_area_m2=ic.shops.min_room_area_m2 if shop else MIN_ROOM_M2,
            ) if door else "no_door_position"
            if shop and not isinstance(plan, str):
                plan = plan_shop_rooms(plan, refined, trial, segments, _shops_of(evidence[bid], feature_of, shop_tags), cfg)
            if isinstance(plan, str):
                issues.append(Issue("interior_not_generated", "info", bid, plan))
            else:
                height = attempt_height
                storeys = attempt_storeys
                layout = trial
                interior = plan
        if layout is None:
            layout = run_layout(*plain_door, storeys)
        doors_on = door_walls(segments, others, cfg)
        door_rule = ensure_door(layout, doors_on, storeys, plain_door[0], door_spec, template, refined, cfg)
        opened_windows = omitted_windows = 0  # pragma: no mutate: read only when an interior is built, and building one pierces the windows first
        budget = budgets[shop]
        shell = built = refusal = None
        if interior is not None:
            opened_windows, omitted_windows = pierce_room_windows(
                layout, segments, interior.ground, refined, cfg.interiors.wall_thickness_m,
                room_ceiling(interior, height, cfg.interiors.slab_m), interior.hosts,
            )
            # The drawn triangles are counted before the shell and the colliders are built, so an
            # interior over the budget costs its plan and its visual meshes, not a whole building.
            drawn = interior_visual(interior, height, cfg, room_window_cuts(layout, segments))
            refusal = budget.refuse("triangles", visual_triangles(drawn))
            if refusal is None:
                shell = build_shell(refined, segments, layout, height, shell_template, wall_material, roof_material, bid, cfg)  # pragma: no mutate: only None for building_id or cfg survives, build_shell reads neither; the budget tests pin the shell
                built = assemble_interior(interior, drawn, height, refined, cfg, glass_penetrations(layout, segments))
                refusal = budget.refuse("collision_triangles", built.collision_triangles)
        if refusal is not None:
            issues.append(Issue("interior_not_generated", "info", bid, refusal))
            interior = built = shell = None
            height, storeys = previous_height, plain_storeys
            layout = run_layout(*plain_door, storeys)
            door_rule = ensure_door(layout, doors_on, storeys, plain_door[0], door_spec, template, refined, cfg)
        if built is not None:
            budget.spend(built)
            if interior is not None and (furnishing if shop else floor_furnishing) is not None:
                # Furnished after the loop, nearest the core centre first, so the budget goes where play is (ADR-031).
                pending_furnish.append(_PendingFurnish(shop, bid, interior, built, glass_penetrations(layout, segments), o.id,
                                                       float(geom.distance(Point(0.0, 0.0)))))
        if shell is None:
            mansard = mansard_for(haussmann, interior is not None, landmark, storeys, shell_template)
            shell = build_shell(refined, segments, layout, height, shell_template, wall_material, roof_material, bid, cfg, mansard)  # pragma: no mutate: only None for building_id or cfg survives, build_shell reads neither; the budget tests pin the shell
        shell.roof["origin"] = roof_origin
        shell.roof["roof_shape"] = roof_shape
        if bid in lidar_evidence:
            # The form is the nDSM's estimate; facade.materials keeps the material's own origin.
            shell.roof["origin"] = "estimated"
            shell.roof["lidar"] = lidar_evidence[bid]
        if "fallback" in shell.roof:
            shape_text = "the estimated gable (nDSM spread)" if bid in lidar_evidence else f"roof:shape={roof_shape}"
            issues.append(Issue("roof_shape_fallback", "info", bid, f"{shape_text} drawn flat: {shell.roof['fallback']}"))
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
            interior_record = built.spec | {
                "kind": "shop" if shop else "residential",
                "checks": built.checks,
                "collider_parts": [n for n, _ in built.colliders],
                "through_windows": opened_windows,
                "omitted_windows": omitted_windows,
                "visual_triangles": built.visual_triangles,
                "collision_triangles": built.collision_triangles,
            }
            objects.append(DetailObject(
                f"interior/{bid}", "interior",
                [Primitive(merge(ms), mat) for mat, ms in sorted(built.visual.items()) if ms],
                [], {"id": f"interior/{bid}", "type": "interior", "source_ids": [bid], **interior_record,
                     "generation": {"method": "generic_simplified_interior", "attribute_origin": "generated"}},
                light_anchors=built.light_anchors,
            ))
            assert built.light_anchors is not None
            lit.append((shop, built.light_anchors))
            if pending_furnish and pending_furnish[-1].bid == bid and pending_furnish[-1].obj is None:
                pending_furnish[-1].obj, pending_furnish[-1].lit_index = objects[-1], len(lit) - 1
            if shop:
                shop_interiors.append(bid)
            else:
                interiors_built.append(bid)
                interiors_left -= 1
        elif base_collider is not None:
            colliders = [base_collider]
        # Any flat roof may get a ladder or a lift at the layout stage (ADR-029), and a parapet a player can walk or shoot through is not cover.
        if shell.parapet_collider is not None:
            colliders.append(shell.parapet_collider)
        balcony_collider = merge(shell.balcony_colliders)
        if balcony_collider.triangle_count:
            defects = closed_manifold_defects(balcony_collider)
            if defects:
                issues.append(Issue("collider_not_closed", "critical", bid, f"balcony colliders: {defects} defects"))
            colliders.append(balcony_collider)

        for kind, n in shell.counts.items():
            counts[kind] += n
        flat_openings += shell.relief["flat_openings"]
        setback_holes = [x for x in layout.holes if x.segment in floors]
        setbacks["walls"] += len(floors)
        setbacks["opened"] += len({x.segment for x in setback_holes})
        setbacks["openings"] += len(setback_holes)
        roof_types[str(shell.roof["type"])] += 1
        if roof_shape:
            roof_shapes[f"{roof_shape}:{shell.roof['type']}"] += 1
        door_rules[door_rule] += 1
        seg_exposed = sum(s.length for s in segments if s.exposed)
        exposed_m += seg_exposed
        party_m += sum(s.length for s in segments if not s.exposed)
        for reason, n in sorted(layout.skipped.items()):
            counts[f"skipped:{reason}"] += n
        visual = [Primitive(merge(ms), mat) for mat, ms in sorted(shell.parts.items()) if any(m.triangle_count for m in ms)]
        spec = shell_template.balcony
        balcony_slabs = [(seg, bs0, bs0 + spec.width_m, v0 - spec.slab_m) for seg, bs0, v0 in layout.balconies] if spec is not None else []  # pragma: no mutate: a template without a balcony lays out none; the guard only tells mypy spec is set
        shops = plan_shops(bid, _shops_of(evidence[bid], feature_of, shop_tags), layout.holes, segments, cfg,
                           interior.shop_doors if interior is not None else (), balcony_slabs, every_footprint) if shop else []
        door_fronts.extend(_door_fronts(layout.holes, segments))
        for entry in shops:
            if entry["poi_id"] in place_of:
                entry["poi_source"] = place_source(place_of[entry["poi_id"]])
        # A landmark was lent no wall colour (``_facade_walls``), so its OSM tag or generated wall leaves no Overture source here.
        lent_wall = lent_walls.get(key_of[bid])
        lent_roof = lent_roofs.get(key_of[bid])
        wall_source = _source(tagged_wall(tags, cfg.materials, lent=_lent_colour(lent_wall)), wall_origin)
        roof_source = _source(tagged_roof(tags, cfg.materials, lent=_lent_colour(lent_roof)), roof_origin)
        wall_record: dict[str, Any] = {"wall": wall_material, "wall_origin": wall_origin, "wall_source": wall_source}
        if wall_source == OVERTURE_FACADE and lent_wall is not None:
            wall_record["wall_evidence"] = _tint_evidence(lent_wall)
        roof_record: dict[str, Any] = {"roof": shell.roof["material"], "roof_origin": roof_origin, "roof_source": roof_source}
        if roof_source == OVERTURE_ROOF and lent_roof is not None:
            roof_record["roof_evidence"] = _tint_evidence(lent_roof)
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
                "ground_floor_evidence": _ground_evidence(ground, template, str(h.get("category")), evidence[bid], catastro.get(bid)),
                "storeys": {"count": storeys.count, "height_m": round(storeys.height_m, 3), "origin": storeys.origin},
                "exposed_length_m": round(seg_exposed, 3),
                "party_wall_length_m": round(sum(s.length for s in segments if not s.exposed), 3),
                "openings": dict(sorted(shell.counts.items())),
                "relief": dict(shell.relief),
                "shops": shops,
                "skipped": dict(sorted(layout.skipped.items())),
                "materials": wall_record | roof_record,
                "use": use.to_record(),
                "door": door_rule,
                "doors_m": _door_centres(layout.holes, segments),
                "balconies_m": _balcony_spans(_balcony_columns(layout, spec), segments, [(seg, fs0, fs1) for seg, fs0, fs1, _ in layout.filants]),
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
        if bid in third_party:
            record["facade"]["ground_floor_evidence"] = third_party_ground(record["facade"]["ground_floor_evidence"])
        if storeys.origin == "interior_storey":
            record["height"] = {
                **record["height"],
                "height_m": round(height, 3),
                "previous_height_m": round(previous_height, 3),
                "interior_storey_origin": "estimated",
            }
        frame = wall_uv_frame(segments, storeys, h.get("estimated_levels"))
        objects.append(DetailObject(o.id, "building", visual, colliders, record, glass_colliders=glass_colliders, uv_frame=frame))
        core_shells[bid] = (shell, segments, objects[-1])
        if landmark:
            issues.extend(lod2_issues.get(bid, ()))
            if bid in lod2_built and interior_record is not None:
                issues.append(Issue("landmark_lod2_not_used", "info", bid, "it has an interior; the placeholder and its tower stay"))
            if bid in lod2_built and interior_record is None:
                lod2_swap.add(bid)  # drawn with its LoD2 volume once the party walls are split (``_swap_lod2``): no tower prism
            else:
                towers, tower_issues = attach_towers(bid, geom, features, cfg)
                issues.extend(tower_issues)
                for part in towers:
                    objects.append(DetailObject(part.id, "landmark_part", list(part.visual), [part.collider], part.record))
        if core is not None:
            for x in objects[first_object:]:
                x.record["tier"] = CORE
        emitted[o.id] = (objects[first_object:], issues[first_issue:])

    _furnish_by_priority(pending_furnish, furnishing, floor_furnishing, cfg, lit, emitted)
    return _assemble_detail(
        base=base,
        base_ref=base_ref,
        cfg=cfg,
        tcfg=tcfg,
        prototypes=prototypes,
        extra_urban=extra_urban,
        core_half_side_m=core_half_side_m,
        mid_ring_m=mid_ring_m,
        places=places,
        tints=tints,
        roof_spread=roof_spread,
        lod2_landmarks=lod2_landmarks,
        lod2_built=lod2_built,
        lod2_swap=lod2_swap,
        core=core,
        heights=heights,
        height_of=height_of,
        features=features,
        footprints=footprints,
        ids=ids,
        tree=tree,
        key_of=key_of,
        tier_of=tier_of,
        building_objs=building_objs,
        facade_walls=facade_walls,
        typologies=typologies,
        facade_styles=facade_styles,
        lent_walls=lent_walls,
        lent_roofs=lent_roofs,
        lidar_classes=lidar_classes,
        third_party=third_party,
        issues=issues,
        leading_issues=leading_issues,
        emitted=emitted,
        core_shells=core_shells,
        lit=lit,
        every_footprint=every_footprint,
        door_fronts=door_fronts,
        counts=counts,
        templates_used=templates_used,
        roof_types=roof_types,
        roof_shapes=roof_shapes,
        door_rules=door_rules,
        setbacks=setbacks,
        interiors_built=interiors_built,
        shop_interiors=shop_interiors,
        budgets=budgets,
        furnishing=furnishing,
        floor_furnishing=floor_furnishing,
        flat_openings=flat_openings,
        exposed_m=exposed_m,
        party_m=party_m,
    )


def _assemble_detail(
    *,
    base: GeneratedScene,
    base_ref: Mapping[str, Any],
    cfg: DetailConfig,
    tcfg: TypologyConfig,
    prototypes: Mapping[str, Prototype] | None,
    extra_urban: ExtraUrban | None,
    core_half_side_m: float | None,
    mid_ring_m: float | None,
    places: Sequence[Mapping[str, Any]],
    tints: Sequence[Mapping[str, Any]] | None,
    roof_spread: SpreadReport | None,
    lod2_landmarks: Lod2Index | None,
    lod2_built: dict[str, Lod2Part],
    lod2_swap: set[str],
    core: Polygon | None,
    heights: dict[str, Any],
    height_of: dict[str, Any],
    features: list[Any],
    footprints: dict[str, BaseGeometry],
    ids: list[str],
    tree: STRtree,
    key_of: dict[str, str],
    tier_of: dict[str, str],
    building_objs: list[SceneObject],
    facade_walls: dict[str, tuple[FacadeUse, str, str]],
    typologies: dict[str, dict[str, Any]],
    facade_styles: dict[str, FacadeStyle],
    lent_walls: dict[str, dict[str, Any]],
    lent_roofs: dict[str, dict[str, Any]],
    lidar_classes: dict[str, str],
    third_party: set[str],
    issues: list[Issue],
    leading_issues: int,
    emitted: dict[str, tuple[list[DetailObject], list[Issue]]],
    core_shells: dict[str, tuple[Shell, list[Segment], DetailObject]],
    lit: list[tuple[bool, dict[str, Any]]],
    every_footprint: BaseGeometry,
    door_fronts: list[Polygon],
    counts: Counter[str],
    templates_used: Counter[str],
    roof_types: Counter[str],
    roof_shapes: Counter[str],
    door_rules: Counter[str],
    setbacks: Counter[str],
    interiors_built: list[str],
    shop_interiors: list[str],
    budgets: dict[bool, InteriorBudget],
    furnishing: ShopFurnishing | None,
    floor_furnishing: ShopFurnishing | None,
    flat_openings: int,
    exposed_m: float,
    party_m: float,
) -> DetailedScene:
    """The rest of ``detail_scene`` once every building is drawn: party walls, building parts, ground, urban elements, checks and the report.

    It is split from ``detail_scene`` only to keep each function short; it reads the values ``detail_scene`` built and changes none of its behaviour."""
    wall_areas = _split_party_walls(core_shells, footprints, tree, ids, lod2_swap)
    _swap_lod2(building_objs, emitted, lod2_built, lod2_swap)
    objects = [x for o in building_objs for x in emitted[o.id][0]]
    for x in objects:
        if x.type == "building":
            x.record["facade"]["typology"] = {**typologies[x.record["source_ids"][0]], "facade_style": asdict(facade_styles[x.record["source_ids"][0]])}
    issues = issues[:leading_issues] + [x for o in building_objs for x in emitted[o.id][1]]
    dropped_lights = cap_lights(lit, cfg.interiors.lights_max)
    issues.extend(Issue("interior_light_dropped", "info", room, f"over interiors.lights_max {cfg.interiors.lights_max}")
                  for room in dropped_lights)

    storeys_m = heights["assumptions"]["storey_height_m"]
    part_objects, part_issues = _building_parts(
        features, footprints, cfg,
        storey_of=lambda b: (float(storeys_m[height_of[b]["category"]]), str(height_of[b]["category"])),
        top_of={o.record["source_ids"][0]: float(o.record["height"]["height_m"]) for o in building_objs},
        parapet_of={x.record["source_ids"][0]: float(x.record["roof"]["parapet_m"]) for x in objects
                    if x.type == "building" and "parapet_m" in x.record["roof"]},
    )
    issues.extend(part_issues)
    # A building:part of a landmark drawn with its LoD2 volume would stand inside that volume; the volume holds it.
    replaced = sum(p.record["source_ids"][0] in lod2_swap for p in part_objects)
    part_objects = [p for p in part_objects if p.record["source_ids"][0] not in lod2_swap]
    if core is not None:
        for piece in part_objects:
            piece.record["tier"] = tier_of[piece.record["source_ids"][0]]
            if piece.record["tier"] != CORE:
                piece.colliders = []
    objects.extend(part_objects)

    # Surfaces, curbs and ground slabs are reused from phase 03 unchanged. With a core,
    # the ground slabs are rebuilt over the part of the ground inside it (ADR-017).
    for o in base.objects:
        if o.type == "building" or (core is not None and o.type == "ground_collider"):
            continue
        objects.append(DetailObject(o.id, o.type, list(o.visual), [o.collider] if o.collider is not None else [], dict(o.record)))
    if core is not None:
        slabs, slab_issues = ground_slabs(base.partition, base.config, clip=core)
        issues.extend(slab_issues)
        objects.extend(DetailObject(o.id, o.type, [], [o.collider], dict(o.record)) for o in slabs if o.collider is not None)

    prototypes = dict(prototypes or urban_prototypes())
    walkable = [s.geometry for c, s in base.partition.surfaces.items() if c not in (SurfaceClass.CARRIAGEWAY, SurfaceClass.WATER)]
    free = union_all(walkable) if walkable else Point(0, 0).buffer(0)
    buildings_union = every_footprint
    elements, rejected = place_elements(features, base.ways, free, buildings_union, prototypes, cfg, doors=union_all(door_fronts))
    if extra_urban is not None:
        extra, extra_rejected = extra_urban(free, buildings_union, prototypes, cfg, elements)
        elements.extend(extra)
        rejected.extend(extra_rejected)
    placed = len(elements)
    if core is not None:
        elements = [e for e in elements if core.covers(Point(float(e.matrix[0, 3]), float(e.matrix[1, 3])))]
    for r in rejected:
        issues.append(Issue("urban_element_not_placed", rejection_severity(r), r.id, f"{r.kind}: {r.reason}"))
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
        "typology_config": {"path": tcfg.path, "sha256": tcfg.sha256, "version": tcfg.version},
    }
    detailed.stats = _stats(detailed, counts, templates_used, roof_types, interiors_built, exposed_m, party_m, elements)
    detailed.stats["interior_visual_triangles"] = budgets[False].used["triangles"]
    detailed.stats["interior_collision_triangles"] = budgets[False].used["collision_triangles"]
    detailed.stats["shop_interiors"] = sorted(shop_interiors)
    grounds = [o.record["facade"].get("ground_floor_evidence") for o in objects if o.type == "building"]
    detailed.stats["shopfront_evidence"] = dict(sorted(Counter(g["source"] for g in grounds if g).items()))
    detailed.stats["shop_interior_visual_triangles"] = budgets[True].used["triangles"]
    detailed.stats["shop_interior_collision_triangles"] = budgets[True].used["collision_triangles"]
    if furnishing is not None:
        detailed.stats["shop_furnishing"] = furnishing.stats()
    if floor_furnishing is not None:
        detailed.stats["floor_furnishing"] = floor_furnishing.stats()
    detailed.stats["interior_lights"] = {
        "max": cfg.interiors.lights_max,
        "placed": sum(len(anchors["rooms"]) for _, anchors in lit) - len(dropped_lights),
        "dropped": dropped_lights,
    }
    detailed.stats["flat_openings"] = flat_openings
    detailed.stats["doors"] = dict(sorted(door_rules.items()))
    detailed.stats["wall_area_m2"] = wall_areas.to_record()
    detailed.stats["setback_walls"] = {k: setbacks[k] for k in ("walls", "opened", "openings")}
    _third_party_record(detailed, objects, places, third_party)
    if tints is not None:
        _third_party_tints(detailed, objects, tints, ids, key_of, (lent_walls, lent_roofs))
    if roof_spread is not None:
        detailed.inputs["roof_spread"] = {"path": roof_spread.path, "sha256": roof_spread.sha256, "source": roof_spread.source}
        _lidar_record(detailed, objects, lidar_classes, roof_spread)
    if lod2_landmarks is not None:
        _lod2_record(detailed, objects, lod2_landmarks, lod2_swap, replaced)
    detailed.stats["roof_shapes"] = dict(sorted(roof_shapes.items()))
    detailed.stats["building_parts"] = {
        "kept": len(part_objects),
        "height_origin": dict(sorted(Counter(str(x.record["height"]["origin"]) for x in part_objects).items())),
        "roofs": dict(sorted(Counter(str(x.record["roof"]["type"]) for x in part_objects).items())),
        "skipped": dict(sorted(Counter(i.code for i in part_issues if i.code in PART_SKIPS).items())),
    }
    shells = [facade_walls[bid] for bid in ids if tier_of[bid] == CORE]
    detailed.stats["facade_uses"] = dict(sorted(Counter(u.use for u, _, _ in shells).items()))
    detailed.stats["wall_materials"] = dict(sorted(Counter(w for _, w, _ in shells).items()))
    core_typologies = [typologies[bid] for bid in ids if tier_of[bid] == CORE]
    detailed.stats["typologies"] = dict(sorted(Counter(t["typology"] for t in core_typologies).items()))
    detailed.stats["eras"] = dict(sorted(Counter(t["era"]["period"] if t["era"] else "unknown" for t in core_typologies).items()))
    detailed.stats["era_sources"] = dict(sorted(Counter(t["era"]["source"] for t in core_typologies if t["era"]).items()))
    if core is not None:
        tiers = Counter(tier_of.values())
        detailed.core = {"half_side_m": core_half_side_m, "buildings": {CORE: tiers[CORE], SCENIC: tiers[SCENIC]}}
        if mid_ring_m is not None:
            detailed.core["mid_ring_m"] = float(mid_ring_m)
            detailed.core["buildings"][MID] = tiers[MID]
        detailed.stats["tiers"] = _tier_counts(objects, placed - len(elements), mid=mid_ring_m is not None)
    _budget(detailed)
    return detailed


def _lod2_inputs(
    index: Lod2Index | None,
    ids: Sequence[str],
    footprints: Mapping[str, BaseGeometry],
    height_of: Mapping[str, Any],
    tier_of: Mapping[str, str],
    frame: Mapping[str, Any],
    cfg: DetailConfig,
) -> tuple[dict[str, Lod2Part], dict[str, list[Issue]]]:
    """The LoD2 volume of each core landmark the index matches, and each core landmark's LoD2 issues (plan D6).

    Only a core landmark (``heights.json`` ``landmark``, ADR-008) is a candidate: a scenic or mid landmark keeps its shell.
    Nothing without an index, and nothing with ``landmarks.enabled`` false.
    """
    if index is None or not cfg.landmarks.enabled:
        return {}, {}
    landmarks = {bid: footprints[bid] for bid in ids if height_of[bid].get("landmark") and tier_of[bid] == CORE}
    return lod2_parts(landmarks, index, frame, wall_material=cfg.landmarks.wall_material, roof_material=cfg.landmarks.roof_material,
                      max_height_m=cfg.landmarks.max_tower_height_m)


def _swap_lod2(building_objs: Sequence[SceneObject], emitted: Mapping[str, tuple[list[DetailObject], list[Issue]]],
               built: Mapping[str, Lod2Part], swap: set[str]) -> None:
    """Draw each landmark of ``swap`` with its LoD2 volume and colliders instead of the placeholder shell (plan D6).

    Runs after ``_split_party_walls``, which redraws every core shell. The building keeps its id, its OSM footprint
    and its record; the record says the volume is observed LoD2 (``lod2``) and the facade still is not the real one.
    """
    for o in building_objs:
        bid = o.record["source_ids"][0]
        if bid not in swap:
            continue
        x = next(obj for obj in emitted[o.id][0] if obj.type == "building")
        part = built[bid]
        x.visual = list(part.visual)
        x.colliders = list(part.colliders)
        x.glass_colliders = []
        x.uv_frame = None  # the LoD2 faces are not the template's wall runs (ADR-012): generic surface UVs
        x.record["materials"] = [p.material for p in x.visual]
        x.record["visual"] = {"triangles": sum(p.mesh.triangle_count for p in x.visual)}
        x.record["collider"] = {"source": "lod2_group_prisms", "parts": len(x.colliders),
                                "triangles": sum(m.triangle_count for m in x.colliders), "balconies": 0, "glass_panes": 0}
        x.record["generation"] = {**x.record["generation"], "method": "observed LoD2 volume clipped to the OSM footprint"}
        # representative_of_real_facade stays False, as every building record has it: the LoD2 faces carry no facade.
        x.record["facade"].update({"representation": "observed_lod2", "representative_of_real_volume": True, "needs_custom_model": False})
        x.record["lod2"] = dict(part.record)


def _lod2_record(detailed: DetailedScene, objects: Sequence[DetailObject], index: Lod2Index, swap: set[str],
                 replaced: int) -> None:
    """``inputs.lod2_landmarks``, ``counts.lod2_landmarks`` and, when a landmark was drawn with it, the CC BY attribution line."""
    detailed.inputs["lod2_landmarks"] = {"path": index.path, "sha256": index.sha256, "source": index.source}
    drawn = [o for o in objects if o.type == "building" and "lod2" in o.record]
    codes = Counter(i.code for i in detailed.issues if i.code.startswith("landmark_lod2_"))
    detailed.stats["lod2_landmarks"] = {
        "matched": len(swap),
        "issues": dict(sorted(codes.items())),
        "visual_triangles": sum(p.mesh.triangle_count for o in drawn for p in o.visual),
        "collision_triangles": sum(m.triangle_count for o in drawn for m in o.colliders),
        "building_parts_replaced": replaced,
    }
    if swap:
        lines = {(a["text"], a["license"]): a for a in detailed.extra_attribution}
        line = index.attribution_line()
        lines.setdefault((line["text"], line["license"]), line)
        detailed.extra_attribution = [dict(lines[key]) for key in sorted(lines)]


def _split_party_walls(shells: Mapping[str, tuple[Shell, list[Segment], DetailObject]], footprints: Mapping[str, Any],
                       tree: STRtree, ids: list[str], lod2_swap: set[str]) -> WallAreas:
    """Drop the party walls hidden by a neighbour's wall and give the visible ones the party material.

    Runs once every shell is built, since a neighbour's wall top is only known then
    (an interior can raise it). Updates each building's visual and its record. A landmark of ``lod2_swap`` hides no
    neighbour's wall: its shell is about to give way to the LoD2 volume (``_swap_lod2``), which can be lower than the
    shell or leave part of the footprint open, so its neighbours keep their party walls against it (plan D6).
    """
    tops = {bid: shell.z_top for bid, (shell, _, _) in shells.items() if bid not in lod2_swap}
    total = WallAreas()
    for bid, (shell, segments, obj) in shells.items():
        found = tree.query(footprints[bid].buffer(HIDDEN_GAP_M))  # pragma: no mutate: a wider query only adds candidates that wall_cover finds nothing in
        # Only core shells hide a wall: a scenic shell (ADR-017) is not in ``shells``.
        near = [(footprints[ids[k]], tops[ids[k]]) for k in found if ids[k] in tops and ids[k] != bid]
        areas = split_blank_walls(shell, near)
        areas += WallAreas(street=sum(s.length for s in segments) * shell.z_top - areas.party - areas.hidden)
        total += areas
        obj.visual = [Primitive(merge(ms), mat) for mat, ms in sorted(shell.parts.items()) if any(m.triangle_count for m in ms)]
        obj.record["materials"] = [p.material for p in obj.visual]
        obj.record["visual"] = {"triangles": sum(p.mesh.triangle_count for p in obj.visual)}
        obj.record["facade"]["wall_area_m2"] = areas.to_record()
    return total


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
