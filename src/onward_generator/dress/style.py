"""Copy a detailed scene with better trees, generated sidewalk furniture and optional repeating textures.

Does not apply AI material recommendations. Does not move OSM footprints, streets or plazas.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from shapely import Point, STRtree, union_all
from shapely.geometry.base import BaseGeometry

from onward_generator.detail.config import DetailConfig
from onward_generator.detail.facades import material_base, party_base, stable_unit
from onward_generator.detail.modules import Prototype, urban_prototypes, yaw_matrix
from onward_generator.detail.urban import Element, Rejected, _footprint
from onward_generator.dress.atlas import MIN_TILE_PX, _tile_pixels, build_atlas, encode_png_rgba
from onward_generator.dress.catalog import MaterialCatalog
from onward_generator.dress.geometry import compare_fingerprints, file_sha256, geometry_fingerprint
from onward_generator.generation.config import GenerationConfig, Material
from onward_generator.generation.gltf import Node, Primitive, build_glb, nodes_from_glb
from onward_generator.generation.scene import generate_scene
from onward_generator.generation.surfaces import SurfaceClass
from onward_generator.tiers import core_of

TREE_SIDES = 16
TREE_VARIANTS = 3
MAX_GENERATED_BENCHES = 16
MAX_GENERATED_BINS = 10
GRID_STEP_M = 8.0
CLEARANCE_M = 0.6


def dressed_prototypes() -> dict[str, Prototype]:
    return urban_prototypes(tree_sides=TREE_SIDES, tree_variants=TREE_VARIANTS)


def generated_accessories(
    free: BaseGeometry,
    buildings: BaseGeometry,
    prototypes: Mapping[str, Prototype],
    cfg: DetailConfig,
    placed: Sequence[Element],
    *,
    max_benches: int = MAX_GENERATED_BENCHES,
    max_bins: int = MAX_GENERATED_BINS,
) -> tuple[list[Element], list[Rejected]]:
    """Deterministic benches and bins on free walkable ground. Never on carriageway."""
    extra: list[Element] = []
    rejected: list[Rejected] = []
    # A wall lamp has no collider: it hangs above the players' heads.
    taken = [_footprint(prototypes[e.kind], e.matrix, cfg.urban_clearance_m) for e in placed if e.kind in prototypes and prototypes[e.kind].collider is not None]
    existing = [Point(float(e.matrix[0, 3]), float(e.matrix[1, 3])) for e in placed]

    def try_place(element: Element) -> str | None:
        proto = prototypes.get(element.kind)
        if proto is None:
            return "missing_prototype"
        fp = _footprint(proto, element.matrix, cfg.urban_clearance_m)
        if not free.contains(fp):
            return "not_on_free_walkable_ground"
        if buildings.distance(Point(float(element.matrix[0, 3]), float(element.matrix[1, 3]))) < 1.0:
            return "too_close_to_building"
        if taken and STRtree(taken).query(fp, predicate="intersects").size:
            return "overlaps_another_element"
        taken.append(fp)
        extra.append(element)
        return None

    minx, miny, maxx, maxy = free.bounds
    candidates: list[Point] = []
    x = minx + 2.0
    while x < maxx:
        y = miny + 2.0
        while y < maxy:
            pt = Point(x, y)
            if free.contains(pt.buffer(CLEARANCE_M)):
                candidates.append(pt)
            y += GRID_STEP_M
        x += GRID_STEP_M
    ranked = sorted(candidates, key=lambda p: stable_unit(cfg.seed, f"{p.x:.3f}", f"{p.y:.3f}", "accessory"))

    benches = 0
    bins = 0
    for i, pt in enumerate(ranked):
        if benches >= max_benches and bins >= max_bins:
            break
        if any(pt.distance(q) < 4.0 for q in existing):
            continue
        kind = "bench" if (benches < max_benches and (i % 3 != 2 or bins >= max_bins)) else "waste_basket"
        if kind == "waste_basket" and bins >= max_bins:
            continue
        yaw = 2 * math.pi * stable_unit(cfg.seed, f"{pt.x:.3f}", f"{pt.y:.3f}", "yaw")
        gid = f"generated-{kind}-{i}"
        element = Element(
            f"urban/{kind}/{gid}", kind, yaw_matrix(pt.x, pt.y, 0.0, yaw),
            "generated", (), f"dress style grid {GRID_STEP_M} m on free walkable ground",
        )
        reason = try_place(element)
        if reason:
            rejected.append(Rejected(gid, kind, reason))
            continue
        existing.append(pt)
        if kind == "bench":
            benches += 1
        else:
            bins += 1
    return extra, rejected


GEOGRAPHIC = frozenset({"building", "surface", "curb", "landmark_part", "building_part", "interior"})


def _is_geographic_node(node: Node) -> bool:
    """Keep shells, ground, and the interior meshes that belong to a building.

    The stair ramp and the wall prisms live on the building collider. The floor,
    the inward walls, and the stair steps are a separate visual node. Dropping
    that node leaves a coloured ground, a wall that disappears from inside, and
    a stair that can be climbed but not seen.
    """
    kind = str((node.extras or {}).get("type") or "")
    if kind in GEOGRAPHIC or kind in {"ground_collider"}:
        return True
    name = node.name.split("#", 1)[0]
    return name.startswith(("building/", "building_part/", "surface/", "collider/", "landmark", "interior/"))


def _elements_from_records(objects: Sequence[Mapping[str, Any]], prototypes: Mapping[str, Prototype]) -> list[Element]:
    elements: list[Element] = []
    for rec in objects:
        if rec.get("type") != "urban_element":
            continue
        kind = str(rec.get("kind") or "")
        if kind not in prototypes and kind.startswith("tree"):
            kind = "tree" if "tree" in prototypes else kind
        if kind not in prototypes:
            continue
        pos = rec.get("position_m") or [0.0, 0.0]
        yaw = math.radians(float(rec.get("yaw_deg") or 0.0))
        elements.append(Element(
            str(rec["id"]), kind, yaw_matrix(float(pos[0]), float(pos[1]), 0.0, yaw),
            str(rec.get("attribute_origin") or "observed"),
            tuple(rec.get("source_ids") or ()),
            str(rec.get("placement_rule") or "copied_from_detail"),
        ))
    return elements


def _urban_nodes(elements: Sequence[Element], prototypes: Mapping[str, Prototype]) -> tuple[list[Node], list[Node], list[dict[str, Any]]]:
    visual: list[Node] = []
    collision: list[Node] = []
    records: list[dict[str, Any]] = []
    for e in elements:
        proto = prototypes[e.kind]
        extras = {"object_id": e.id, "type": "urban_element", "source_ids": list(e.source_ids)}
        visual.append(Node(e.id, tuple(Primitive(m, mat) for mat, m in proto.parts), extras, e.matrix, proto.key))
        if proto.collider is not None:
            collision.append(Node(
                f"{e.id}#collider", (Primitive(proto.collider, "collider"),), extras, e.matrix, proto.key + "#collider",
            ))
        records.append({
            "id": e.id, "type": "urban_element", "kind": e.kind, "source_ids": list(e.source_ids),
            "attribute_origin": e.origin, "placement_rule": e.rule, "prototype": proto.key,
            "position_m": [round(float(e.matrix[0, 3]), 3), round(float(e.matrix[1, 3]), 3)],
            "yaw_deg": round(float(math.degrees(math.atan2(e.matrix[1, 0], e.matrix[0, 0]))), 2),
            "representation": "generic_prototype",
        })
    return visual, collision, records


def style_copy(
    detail_dir: str | Path,
    scene_path: str | Path,
    heights_path: str | Path,
    gen_cfg: GenerationConfig,
    detail_cfg: DetailConfig,
) -> tuple[list[Node], list[Node], list[dict[str, Any]], dict[str, Any]]:
    """Copy a detail scene. Rebuild only urban instances. Do not regenerate facades."""
    from onward_generator.config.loader import MAX_SCENE_BYTES, read_json_document

    detail = Path(detail_dir).expanduser()
    manifest, _ = read_json_document(detail / "scene_detail.json", max_bytes=MAX_SCENE_BYTES)
    stored_geo = ((manifest.get("inputs") or {}).get("geo_scene") or {}).get("sha256")
    stored_h = ((manifest.get("inputs") or {}).get("heights") or {}).get("sha256")
    if stored_geo and file_sha256(Path(scene_path)) != stored_geo:
        raise ValueError(f"{scene_path}: sha256 does not match the detail manifest")
    if stored_h and file_sha256(Path(heights_path)) != stored_h:
        raise ValueError(f"{heights_path}: sha256 does not match the detail manifest")

    visual_in, vis_mats = nodes_from_glb((detail / "scene_visual.glb").read_bytes())
    collision_in, _ = nodes_from_glb((detail / "scene_collision.glb").read_bytes())
    kept_visual = [n for n in visual_in if _is_geographic_node(n)]
    kept_collision = [n for n in collision_in if _is_geographic_node(n)]
    if not any(n.extras.get("type") == "building" or n.name.startswith("building/") for n in kept_visual):
        raise ValueError(f"{detail}: visual GLB has no building nodes to keep")

    prototypes = dressed_prototypes()
    objects = list(manifest.get("objects") or [])
    existing = _elements_from_records(objects, prototypes)
    generated = generate_scene(scene_path, heights_path, gen_cfg)
    walkable = [
        s.geometry for c, s in generated.partition.surfaces.items()
        if c not in (SurfaceClass.CARRIAGEWAY, SurfaceClass.WATER)
    ]
    free = union_all(walkable) if walkable else Point(0, 0).buffer(0)
    core = core_of(manifest)
    if core is not None:
        # Generated benches and bins stay in the playable core (ADR-017).
        free = free.intersection(core)
    extra: list[Element] = []
    rejected: list[Rejected] = []
    if not any(e.origin == "generated" and e.kind in {"bench", "waste_basket"} for e in existing):
        extra, rejected = generated_accessories(
            free, generated.partition.buildings, prototypes, detail_cfg, existing,
        )
    urban_visual, urban_collision, urban_records = _urban_nodes([*existing, *extra], prototypes)
    after_objects = [o for o in objects if o.get("type") != "urban_element"] + urban_records
    meta = {
        "attribution": manifest.get("attribution"),
        "seed": detail_cfg.seed,
        "source_detail_sha256": file_sha256(detail / "scene_detail.json"),
        "rejected": [{"id": r.id, "kind": r.kind, "reason": r.reason} for r in rejected],
        "vis_mats": vis_mats,
        "source_manifest": manifest,
        "generated_accessories": {
            f"{k}:generated": sum(1 for e in extra if e.kind == k) for k in ("bench", "waste_basket")
        },
    }
    return kept_visual + urban_visual, kept_collision + urban_collision, after_objects, meta


def write_styled(
    visual_nodes: Sequence[Node],
    collision_nodes: Sequence[Node],
    objects: Sequence[Mapping[str, Any]],
    before_objects: Sequence[Mapping[str, Any]],
    materials: Mapping[str, Material],
    catalog: MaterialCatalog,
    targets: Mapping[str, Path],
    *,
    atlas: bool,
    attribution: Any,
    seed: int,
    source_manifest: Mapping[str, Any],
    source_detail_sha256: str | None,
    generated_accessories: Mapping[str, int],
) -> dict[str, Any]:
    from onward_generator.io import atomic_write_bytes, atomic_write_json

    used = sorted({p.material for n in visual_nodes for p in n.primitives})
    extras: dict[str, Any] = {
        "attribution": attribution,
        "manifest": "scene_detail.json",
        "fidelity": "dress style 0.3: copy of detail GLB, urban overlay, one repeating procedural tile per material, no AI material apply",
        "dress": {"tree_sides": TREE_SIDES, "tree_variants": TREE_VARIANTS, "atlas": atlas, "regenerated_facades": False},
    }
    repeat_images: dict[str, bytes] = {}
    tile_m: dict[str, float] = {}
    roofs: set[str] = set()
    atlas_note = None
    if atlas:
        # The GLB repeats one tile per material on the face plane. atlas.png is only the contact sheet.
        # A party wall (``party_<wall>``) shows its wall's tile, so the tiles are drawn per wall.
        bases = sorted({party_base(color_id) for color_id in used})
        built = build_atlas(catalog, materials, used_color_ids=bases, seed=seed)  # pragma: no mutate: build_atlas ignores materials; bases and seed are pinned by the party test
        tiles = {
            color_id: encode_png_rgba(_tile_pixels(color_id, MIN_TILE_PX, seed + index))
            for index, color_id in enumerate(bases)
        }
        repeat_images = {color_id: tiles[party_base(color_id)] for color_id in used}
        catalog_tile = {e.color_id: float(e.technical["tile_m"]) for e in catalog.entries}
        tile_m = {color_id: catalog_tile[material_base(color_id)] for color_id in used if material_base(color_id) in catalog_tile}
        roof_ids = {e.color_id for e in catalog.entries if "roof" in e.compatible_surfaces}
        roofs = {color_id for color_id in used if material_base(color_id) in roof_ids}  # a tint is mapped as its base
        extras["atlas"] = {
            "size": built.size, "grid": built.tiles, "tiles": len(built.slots), "note": built.note,
            "role": "contact_sheet",
        }
        atlas_note = built.note
        atomic_write_bytes(targets["atlas"], built.png)
    visual = build_glb(
        visual_nodes, materials, extras=extras,
        repeat_images=repeat_images, repeat_tile_m=tile_m, roof_uv_materials=roofs,
    )
    collision = build_glb(collision_nodes, materials, extras=extras)
    atomic_write_bytes(targets["visual"], visual)
    atomic_write_bytes(targets["collision"], collision)
    written = dict(source_manifest)
    written.pop("_report_path", None)
    written["objects"] = list(objects)
    written["fidelity"] = {
        **dict(source_manifest.get("fidelity") or {}),
        "dress": "copy; facades not regenerated",
    }
    atomic_write_json(targets["manifest"], written)
    after = geometry_fingerprint([o for o in objects if o.get("type") in GEOGRAPHIC])
    before = geometry_fingerprint([o for o in before_objects if o.get("type") in GEOGRAPHIC])
    compare = compare_fingerprints(before, after)
    report: dict[str, Any] = {}
    src_report = Path(str(source_manifest.get("_report_path") or ""))
    if src_report.is_file():
        import json
        report = json.loads(src_report.read_text(encoding="utf-8"))
    report["dress"] = {
        "schema": "onward_generator.dress_style_report",
        "schema_version": "0.2.0",
        "applied_ai_materials": False,
        "regenerated_facades": False,
        "atlas": atlas,
        "atlas_note": atlas_note,
        "tree_sides": TREE_SIDES,
        "generated_accessories": dict(generated_accessories),
        "geometry": compare,
        "source_detail_sha256": source_detail_sha256,
    }
    atomic_write_json(targets["report"], report)
    return report
