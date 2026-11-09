"""Copy a dress/detail scene and remap atlas tiles from a texture plan.

Does not call detail_scene, does not restyle urban props, does not write Unity.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

from onward_generator import __version__
from onward_generator.config.loader import MAX_SCENE_BYTES, ConfigLoadError, read_json_document
from onward_generator.detail.config import load_detail_config
from onward_generator.detail.facades import material_base, party_base, party_material
from onward_generator.detail.s3db import tint_base
from onward_generator.dress.atlas import (
    MIN_MEAN_LUMA,
    _tile_pixels,
    bake_photo_tile,
    build_atlas,
    decode_jpeg_rgb,
    encode_opaque_png,
    run_png_jobs,
)
from onward_generator.dress.catalog import MaterialCatalog, load_catalog
from onward_generator.dress.geometry import compare_fingerprints, file_sha256, geometry_fingerprint
from onward_generator.dress.surfaces import _load_features, _tags_of
from onward_generator.generation.config import Material
from onward_generator.generation.gltf import CellPhase, Node, Primitive, build_glb, nodes_from_glb
from onward_generator.io import atomic_write_bytes, atomic_write_json, prepare_outputs
from onward_generator.materials.openings import boarded_png, shop_glass_png, window_png
from onward_generator.materials.plan import TexturePlan, load_texture_plan
from onward_generator.materials.polyhaven import (
    DEFAULT_CACHE,
    DownloadError,
    Fetcher,
    TextureRecord,
    ensure_diffuse_1k,
)
from onward_generator.materials.tile_cache import TileCache, photo_tile_key, tile_cache_dir
from onward_generator.materials.trim import apply_trim
from onward_generator.materials.trim_style import AUTO
from onward_generator.tiers import SCENIC_WALLS

REPO_ROOT = Path(__file__).resolve().parents[3]
GEOGRAPHIC = frozenset({"building", "surface", "curb", "landmark_part"})
OUTPUT_FILES = (
    "scene_visual.glb",
    "scene_collision.glb",
    "scene_detail.json",
    "detail_report.json",
    "atlas.png",
    "materials_report.json",
    "materials.catalog.json",
    "facade_trim.png",
)
PROTECTED_OUTPUT_NAMES = frozenset({
    "scene_detail_v07_2",
    "scene_detail_v08",
    "scene_detail_dress_01",
    "scene_detail_dress_02",
    "scene_detail_dress_03",
    "scene_detail_materials_01",
    "scene_detail_materials_02",
    "scene_detail_materials_03",
})
WALL_COLOR_IDS = frozenset({
    "plaster_ochre", "plaster_cream", "plaster_white", "plaster_salmon",
    "stone_light", "stone_warm", "building_wall",
})
WHITE = (1.0, 1.0, 1.0, 1.0)
MAX_PHOTO_PX = 1024
UNIT_UV_MATERIALS = frozenset({"glass_dark", "glass_boarded", "glass_view", "shop_glass", "door_wood"})
# The map-wide ground surfaces that take a UV phase per combine cell with the "repetition" switch
# (plan C1, ADR-009 "Repetition per cell"). The open ground and the green keep one phase.
GROUND_CELL_PHASE_MATERIALS = frozenset({"carriageway", "sidewalk", "pedestrian", "cobblestone_paving"})
# The see-through panes keep their RGBA images. The contact atlas is opaque.
ATLAS_SKIP = frozenset({"glass_view", "shop_glass"})
# Shop interiors (docs/07-detail.md, *Shop interiors*): one library photograph per shared material,
# not a plan slot. Walls keep their category tint over a greyscale plaster; floors show the photograph.
# ``(library id, albedo mode, metres per repeat)``.
SHOP_WALL_TEXTURE = ("ph_plastered_wall", "greyscale_modulation", 2.0)
SHOP_INTERIOR_TEXTURES: dict[str, tuple[str, str, float]] = {
    "interior_shop_bakery": SHOP_WALL_TEXTURE,
    "interior_shop_pharmacy": SHOP_WALL_TEXTURE,
    "interior_shop_bar": SHOP_WALL_TEXTURE,
    "interior_shop_generic": SHOP_WALL_TEXTURE,
    "interior_shop_tile": ("acg_tiles139", "color_albedo", 2.0),
    "interior_shop_wood": ("ph_wood_planks", "color_albedo", 2.0),
}
# Interior surfaces are not plan slots. They reuse a photograph the plan already chose.
INTERIOR_PHOTO_SLOTS: dict[str, tuple[str, ...]] = {
    "interior_wall": ("plain_facades", "historic_facades", "ochre_facades"),
    "interior_floor": ("doors",),
    "stair": ("sidewalks",),
}
# A facade colour with no plan slot of its own. It takes a plaster photograph the plan
# already chose, in greyscale, so its own tint stays on _BaseColor and it does not look
# like the slot it borrows from.
SHARED_FACADE_SLOTS: dict[str, tuple[str, ...]] = {
    "plaster_salmon": ("plain_facades", "ochre_facades", "historic_facades"),
    # The scenic shells outside the playable core (ADR-017).
    **{name: ("plain_facades", "historic_facades", "ochre_facades") for name in sorted(SCENIC_WALLS)},
}


def share_facade_photos(
    records: Mapping[str, TextureRecord],
    used: Collection[str],
    photo_tiles: dict[str, tuple[Any, str]],
) -> list[dict[str, Any]]:
    """Give each used colour of ``SHARED_FACADE_SLOTS`` the first listed slot's photograph, in greyscale."""
    shared: list[dict[str, Any]] = []
    for target, slots in SHARED_FACADE_SLOTS.items():
        slot = next((name for name in slots if name in records), None)
        if target in used and target not in photo_tiles and slot is not None:
            photo_tiles[target] = (decode_jpeg_rgb(Path(records[slot].path).read_bytes()), "greyscale_modulation")
            shared.append({"color_id": target, "slot": slot, "albedo_mode": "greyscale_modulation",
                           "asset_id": records[slot].asset_id})
    return shared


def share_tint_photos(used: Collection[str], photo_tiles: dict[str, tuple[Any, str]]) -> list[dict[str, Any]]:
    """Give each used tint (``building:colour``, ``roof:colour``; ADR-023) its base's photograph, in greyscale.

    The tint stays on _BaseColor, so a blue facade shows the plaster of its base under its own colour.
    A tint whose base has no photograph keeps the procedural tile.
    """
    shared: list[dict[str, Any]] = []
    for tint in sorted(name for name in used if tint_base(name) != name):
        base = tint_base(tint)
        if base in photo_tiles:
            photo_tiles[tint] = (photo_tiles[base][0], "greyscale_modulation")
            shared.append({"color_id": tint, "base": base, "albedo_mode": "greyscale_modulation"})
    return shared


def share_interior_photos(
    plan: TexturePlan,
    records: Mapping[str, TextureRecord],
    catalog: MaterialCatalog,
    used: Collection[str],
    photo_tiles: dict[str, tuple[Any, str]],
    materials: dict[str, Material],
) -> list[dict[str, Any]]:
    """Give interior walls, floors, and stairs a photograph from the texture plan.

    Walls prefer the plain facade, then historic plaster, then ochre. Floors use
    the door wood. Stairs use the sidewalk concrete. A colour albedo is shown
    with a white base colour; a greyscale photo keeps the interior tint.
    """
    shared: list[dict[str, Any]] = []
    for target, slots in INTERIOR_PHOTO_SLOTS.items():
        if target not in used or target in photo_tiles:
            continue
        for slot in slots:
            binding = plan.by_id.get(slot)
            record = records.get(slot)
            if binding is None or record is None or not binding.catalog_ids:
                continue
            entry = catalog.get(binding.catalog_ids[0])
            if entry is None:
                continue
            rgb = decode_jpeg_rgb(Path(record.path).read_bytes())
            photo_tiles[target] = (rgb, binding.albedo_mode)
            if binding.albedo_mode == "color_albedo":
                prev = materials.get(target) or Material(WHITE, 0.9)
                materials[target] = Material(WHITE, prev.roughness)
            shared.append({
                "color_id": target,
                "slot": slot,
                "source_color_id": entry.color_id,
                "albedo_mode": binding.albedo_mode,
                "tile_m": float(entry.technical["tile_m"]),
                "asset_id": record.asset_id,
            })
            break
    return shared


def share_shop_interior_photos(
    used: Collection[str],
    photo_tiles: dict[str, tuple[Any, str]],
    materials: dict[str, Material],
    tile_m: dict[str, float],
) -> list[dict[str, Any]]:
    """Give each used shop interior material of ``SHOP_INTERIOR_TEXTURES`` its library photograph.

    A floor in ``color_albedo`` gets a white base colour; a wall in ``greyscale_modulation`` keeps its tint.
    """
    from onward_generator.materials.library_plan import texture_record

    shared: list[dict[str, Any]] = []
    for target, (library_id, mode, metres) in SHOP_INTERIOR_TEXTURES.items():
        if target not in used:
            continue
        record = texture_record(library_id)
        photo_tiles[target] = (decode_jpeg_rgb(Path(record.path).read_bytes()), mode)
        tile_m[target] = metres
        if mode == "color_albedo":
            materials[target] = Material(WHITE, materials[target].roughness)
        shared.append({"color_id": target, "library_id": library_id, "albedo_mode": mode, "tile_m": metres,
                       "provider": record.provider, "license": record.license, "sha256": record.sha256,
                       "repo_path": _rel(Path(record.path))})
    return shared


def _photo_png(rgb: Any, mode: str, cache: TileCache | None = None) -> bytes:
    side = min(MAX_PHOTO_PX, max(int(rgb.shape[0]), int(rgb.shape[1]), 1))

    def bake() -> bytes:
        return encode_opaque_png(bake_photo_tile(rgb, side, mode))

    return bake() if cache is None else cache.fetch(photo_tile_key(rgb, side, mode), bake)


def _procedural_png(color_id: str, seed: int) -> bytes:
    return encode_opaque_png(_tile_pixels(color_id, 256, seed))


class MaterialsError(ValueError):
    """The materials stage cannot apply the plan to this scene."""


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path.resolve())


def _base_hashes(base: Path) -> dict[str, str | None]:
    return {
        "scene_detail.json": file_sha256(base / "scene_detail.json"),
        "scene_visual.glb": file_sha256(base / "scene_visual.glb"),
        "scene_collision.glb": file_sha256(base / "scene_collision.glb"),
    }


def _objects(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    objects = manifest.get("objects") or []
    return [o for o in objects if isinstance(o, dict)]


def _scene_path(manifest: Mapping[str, Any], explicit: str | Path | None) -> Path | None:
    if explicit:
        path = Path(explicit).expanduser()
        return path if path.is_file() else None
    raw = ((manifest.get("inputs") or {}).get("geo_scene") or {}).get("path")
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_file() else None


def has_pedestrian_cobble(objects: Sequence[Mapping[str, Any]]) -> bool:
    """True when generate split out a cobble-tagged pedestrian piece."""
    for obj in objects:
        if obj.get("id") == "surface/pedestrian_cobble":
            return True
        if obj.get("type") == "surface" and obj.get("paving") == "cobble":
            return True
    return False


def rematerialize_cobble_nodes(nodes: Sequence[Node]) -> tuple[list[Node], list[str]]:
    """Give the cobble split its own material slot so asphalt/plaza remainder stay unpainted."""
    out: list[Node] = []
    changed: list[str] = []
    for node in nodes:
        extras = node.extras or {}
        name = node.name.split("#", 1)[0]
        if name != "surface/pedestrian_cobble" and extras.get("object_id") != "surface/pedestrian_cobble":
            out.append(node)
            continue
        prims = [Primitive(p.mesh, "cobblestone_paving", p.uvs) for p in node.primitives]
        out.append(replace(node, primitives=tuple(prims)))
        changed.append(node.name)
    return out, changed


def _is_brick_building(source_ids: Sequence[str], features: Mapping[str, Mapping[str, Any]]) -> bool:
    tags = _tags_of(features, source_ids)
    value = tags.get("building:material") or tags.get("material")
    return value == "brick"


def rematerialize_brick_walls(
    nodes: Sequence[Node],
    features: Mapping[str, Mapping[str, Any]],
) -> tuple[list[Node], list[str]]:
    """Reassign wall primitives of OSM-brick buildings to ``brick_red``, and their party walls to ``party_brick_red``."""
    out: list[Node] = []
    changed: list[str] = []
    for node in nodes:
        extras = node.extras or {}
        source_ids = [str(s) for s in extras.get("source_ids") or []]
        if extras.get("type") != "building" or not _is_brick_building(source_ids, features):
            out.append(node)
            continue
        prims: list[Primitive] = []
        touched = False
        for prim in node.primitives:
            if party_base(prim.material) in WALL_COLOR_IDS:
                brick = "brick_red" if party_base(prim.material) == prim.material else party_material("brick_red")
                prims.append(Primitive(prim.mesh, brick, prim.uvs))
                touched = True
            else:
                prims.append(prim)
        out.append(replace(node, primitives=tuple(prims)))
        if touched:
            changed.append(node.name)
    return out, changed


def building_uv_phase(name: str) -> tuple[float, float]:
    digest = hashlib.sha256(f"uv:{name}".encode()).digest()
    return (digest[0] / 255.0, digest[1] / 255.0)


def building_boarded(name: str) -> bool:
    return hashlib.sha256(f"board:{name}".encode()).digest()[0] % 5 == 0


def stamp_abandoned_facades(nodes: Sequence[Node], *, board: bool) -> list[Node]:
    """Shift each building's repeating albedo, and board some windows when the plan is abandoned."""
    out: list[Node] = []
    for node in nodes:
        extras = dict(node.extras or {})
        if extras.get("type") != "building":
            out.append(node)
            continue
        extras["uv_phase"] = list(building_uv_phase(node.name))
        prims = list(node.primitives)
        if board and building_boarded(node.name):
            prims = [
                Primitive(prim.mesh, "glass_boarded", prim.uvs) if prim.material == "glass_dark" else prim
                for prim in prims
            ]
        out.append(replace(node, primitives=tuple(prims), extras=extras))
    return out


def _used_materials(nodes: Sequence[Node]) -> list[str]:
    return sorted({p.material for n in nodes for p in n.primitives})


def _catalog_snapshot(
    catalog: MaterialCatalog,
    applied: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    doc = json.loads(catalog.path.read_text(encoding="utf-8"))
    by_file = {row["catalog_id"]: row for row in applied}
    for entry in doc["materials"]:
        row = by_file.get(entry["id"])
        if not row:
            continue
        entry["textures"] = {
            "kind": "file",
            "paths": [row["repo_path"]],
            "note": f"{row.get('provider', 'library')} {row['asset_id']} albedo ({row['albedo_mode']}).",
        }
        entry["license"] = "CC0"
        entry["source"] = row["page_url"]
    return doc


def _assert_calm_plan(path: Path) -> None:
    """Refuse a library plan that binds a roof or paving tile over its limit, before anything is written."""
    from onward_generator.materials.library_plan import assert_library_entries

    document = json.loads(path.read_bytes())
    if document.get("provider") == "library":
        assert_library_entries(document, extended=True)


def apply_materials(
    base_dir: str | Path,
    out_dir: str | Path,
    *,
    plan_path: str | Path | None = None,
    catalog_path: str | Path | None = None,
    scene_path: str | Path | None = None,
    cache_dir: str | Path = DEFAULT_CACHE,
    download: bool = False,
    overwrite: bool = False,
    fetch: Fetcher | None = None,
    seed: int = 48152,
    tile_cache: bool = True,
    facades: str = "trim",
    facade_style: str = AUTO,
    style_table: str | Path | None = None,
    regional_facades: bool = False,
    regional_table: str | Path | None = None,
    repetition: bool = False,
    cell_m: float = 100.0,
    street_profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """``tile_cache`` reuses baked photo tiles from ``tile_cache_dir()``; the bytes are the same without it.

    ``repetition`` (the map.json switch, off by default) refuses a library plan that binds a roof
    or paving tile over its coarse-contrast limit, shifts the ground surfaces' UVs by a phase per
    ``cell_m`` combine cell, and adds a ``repetition`` block to the report. Off, nothing changes.

    ``facades`` is ``trim`` (the regional trim-sheet atlas, ADR-020) or ``legacy`` (one
    material per facade part). ``facade_style`` names a style of ``style_table``, or
    ``auto`` to choose it from the map centre. ``regional_facades`` (the map.json switch, plan D1,
    off by default) gives a style whose atlas is null in ``style_table`` its recipe from
    ``regional_table`` (default ``config/facade_styles.regional.json``). Off, nothing changes.
    """
    base = Path(base_dir).expanduser().resolve()
    out = Path(out_dir).expanduser().resolve()
    if not (base / "scene_visual.glb").is_file() or not (base / "scene_detail.json").is_file():
        raise MaterialsError(f"{base}: needs scene_visual.glb and scene_detail.json")
    if out.name in PROTECTED_OUTPUT_NAMES:
        raise MaterialsError(f"refusing to write protected artifact directory {out.name}")
    if out == base:
        raise MaterialsError("output must be a new directory; will not overwrite the base scene")

    cell_phase = CellPhase(float(cell_m), GROUND_CELL_PHASE_MATERIALS) if repetition else None
    catalog = load_catalog(catalog_path)
    plan = load_texture_plan(plan_path, catalog=catalog)
    if repetition:
        _assert_calm_plan(plan.path)
    try:
        manifest, _ = read_json_document(base / "scene_detail.json", max_bytes=MAX_SCENE_BYTES)
    except ConfigLoadError as exc:
        raise MaterialsError(str(exc)) from exc

    before_hashes = _base_hashes(base)
    objects = _objects(manifest)
    before_geo = geometry_fingerprint([o for o in objects if o.get("type") in GEOGRAPHIC])
    scene = _scene_path(manifest, scene_path)
    features = _load_features(scene)

    records: dict[str, TextureRecord] = {}
    for binding in plan.entries:
        if binding.library_id:
            from onward_generator.materials.library_plan import texture_record
            records[binding.id] = texture_record(binding.library_id)
            continue
        try:
            records[binding.id] = ensure_diffuse_1k(
                binding.url, cache_dir=cache_dir, download=download, fetch=fetch,
            )
        except DownloadError:
            raise

    visual_nodes, vis_mats = nodes_from_glb((base / "scene_visual.glb").read_bytes())
    brick_nodes: list[str] = []
    cobble_nodes: list[str] = []
    if "brick" in plan.by_id:
        visual_nodes, brick_nodes = rematerialize_brick_walls(visual_nodes, features)
    if "streets_plazas" in plan.by_id and has_pedestrian_cobble(objects):
        visual_nodes, cobble_nodes = rematerialize_cobble_nodes(visual_nodes)
    visual_nodes = stamp_abandoned_facades(visual_nodes, board=plan.atmosphere == "abandoned")
    trim_root = tile_cache_dir() if tile_cache else None
    # A wall strip takes the colour the scene gives that material; a colour the scene does not use
    # takes the detail configuration's, so the atlas of a style is the same for every map.
    colors = {name: m.color for name, m in load_detail_config().materials.items()} | {
        name: m.color for name, m in vis_mats.items()
    }
    trim = apply_trim(visual_nodes, manifest, colors, mode=facades, style=facade_style, table_path=style_table,
                      cache=None if trim_root is None else TileCache(trim_root),
                      regional=regional_facades, regional_table_path=regional_table)
    visual_nodes = trim.nodes
    # A party wall (``party_<wall>``) takes its wall's albedo: the plan binds the wall. A tint
    # (``<base>__tint_<key>``) shows its base's photograph, so the plan binds the base as well.
    used = {party_base(name) for name in _used_materials(visual_nodes)}
    used |= {tint_base(name) for name in used}
    cobble_ok = bool(cobble_nodes)

    materials = dict(vis_mats)
    photo_tiles: dict[str, tuple[Any, str]] = {}
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for binding in plan.entries:
        record = records[binding.id]
        rgb = decode_jpeg_rgb(Path(record.path).read_bytes())
        if binding.id == "streets_plazas" and not cobble_ok:
            skipped.append({
                "id": binding.id,
                "catalog_ids": list(binding.catalog_ids),
                "reason": "no_pedestrian_cobble_split",
            })
            continue
        if binding.id == "brick" and not brick_nodes and "brick_red" not in used:
            skipped.append({
                "id": binding.id,
                "catalog_ids": list(binding.catalog_ids),
                "reason": "no_osm_brick_buildings",
            })
            continue
        for catalog_id in binding.catalog_ids:
            entry = catalog.get(catalog_id)
            if entry is None:
                skipped.append({"id": binding.id, "catalog_id": catalog_id, "reason": "missing_catalog_id"})
                continue
            slot = "cobblestone_paving" if binding.id == "streets_plazas" else entry.color_id
            if slot not in used:
                skipped.append({
                    "id": binding.id,
                    "catalog_id": catalog_id,
                    "color_id": slot,
                    "reason": "color_id_not_used",
                })
                continue
            photo_tiles[slot] = (rgb, binding.albedo_mode)
            if binding.albedo_mode == "color_albedo":
                prev = materials.get(slot) or materials.get(entry.color_id) or Material(WHITE, 0.9)
                materials[slot] = Material(WHITE, prev.roughness)
            applied.append({
                "id": binding.id,
                "catalog_id": catalog_id,
                "color_id": slot,
                "albedo_mode": binding.albedo_mode,
                "asset_id": record.asset_id,
                "provider": record.provider,
                "license": record.license,
                "authors": list(record.authors),
                "page_url": record.page_url,
                "md5": record.md5,
                "sha256": record.sha256,
                "repo_path": _rel(Path(record.path)),
            })
    interior_shared = share_interior_photos(plan, records, catalog, used, photo_tiles, materials)
    facade_shared = share_facade_photos(records, used, photo_tiles)
    tint_shared = share_tint_photos(used, photo_tiles)
    shop_tile_m: dict[str, float] = {}
    shop_shared = share_shop_interior_photos(used, photo_tiles, materials, shop_tile_m)

    repetition_block: dict[str, Any] | None = None
    if cell_phase is not None:
        from onward_generator.materials.library_plan import repetition_rows

        slots = repetition_rows(applied, {record.asset_id: Path(record.path) for record in records.values()})
        repetition_block = {
            "enabled": True,
            "cell_m": cell_phase.cell_m,
            "cell_phase_materials": sorted(cell_phase.materials),
            "slots": slots,
            "over_limit": sorted({row["asset_id"] for row in slots if row["over_limit"]}),
        }

    after_hashes = _base_hashes(base)
    if after_hashes != before_hashes:
        raise MaterialsError("base scene changed while applying materials; nothing was written")

    collision = base / "scene_collision.glb"
    protected = (
        base / "scene_visual.glb",
        base / "scene_collision.glb",
        base / "scene_detail.json",
    )
    targets = prepare_outputs(out, OUTPUT_FILES, overwrite=overwrite, protected=protected)
    used_ids = _used_materials(visual_nodes)
    bases = sorted({party_base(color_id) for color_id in used_ids})
    atlas_ids = [color_id for color_id in bases if color_id not in ATLAS_SKIP and color_id not in trim.materials]
    built = build_atlas(catalog, materials, used_color_ids=atlas_ids, seed=seed, photo_tiles=photo_tiles)
    extras: dict[str, Any] = {
        "attribution": manifest.get("attribution"),
        "manifest": "scene_detail.json",
        "fidelity": "materials 0.2: one repeating albedo per material, openings fit 0-1, no facade regen",
        "materials": {
            "plan": str(plan.path),
            "plan_sha256": plan.sha256,
            "applied": [row["catalog_id"] for row in applied],
        },
        "atlas": {"size": built.size, "grid": built.tiles, "tiles": len(built.slots), "note": built.note},
    }
    if cell_phase is not None:
        extras["materials"]["repetition"] = {"cell_m": cell_phase.cell_m}
    tile_m = {e.color_id: float(e.technical["tile_m"]) for e in catalog.entries}
    for row in interior_shared:
        tile_m[str(row["color_id"])] = float(row["tile_m"])
    tile_m.update(shop_tile_m)
    cobble_entry = catalog.get("cobblestone_paving")
    if cobble_entry is not None:
        tile_m["cobblestone_paving"] = float(cobble_entry.technical["tile_m"])
    cache_root = tile_cache_dir() if tile_cache else None
    cache = None if cache_root is None else TileCache(cache_root)
    png_jobs: dict[str, Callable[[], bytes]] = {}
    for index, color_id in enumerate(bases):
        if color_id in trim.materials:
            png_jobs[color_id] = partial(bytes, trim.png)
            materials[color_id] = Material(WHITE, 0.9)
            continue
        if color_id in ("glass_dark", "glass_view"):
            png_jobs[color_id] = window_png
            prev = materials.get(color_id) or Material(WHITE, 0.15)
            materials[color_id] = Material(WHITE, prev.roughness)
            continue
        if color_id == "shop_glass":
            png_jobs[color_id] = shop_glass_png
            materials[color_id] = Material(WHITE, materials[color_id].roughness)
            continue
        if color_id == "glass_boarded":
            png_jobs[color_id] = boarded_png
            materials[color_id] = Material(WHITE, 0.85)
            continue
        if color_id in photo_tiles:
            rgb, mode = photo_tiles[color_id]
            png_jobs[color_id] = partial(_photo_png, rgb, mode, cache)
            continue
        png_jobs[color_id] = partial(_procedural_png, color_id, seed + index)
    repeat_images = run_png_jobs(png_jobs)
    for color_id in used_ids:
        base_id = party_base(color_id)
        repeat_images[color_id] = repeat_images[base_id]
        texture_id = material_base(color_id)  # a tint repeats at its base's scale
        if texture_id in tile_m:
            tile_m[color_id] = tile_m[texture_id]
        if base_id in materials:
            materials[color_id] = materials[base_id]
    roof_ids = {e.color_id for e in catalog.entries if "roof" in e.compatible_surfaces}
    visual = build_glb(
        visual_nodes, materials, extras=extras,
        repeat_images=repeat_images, repeat_tile_m=tile_m, unit_uv_materials=UNIT_UV_MATERIALS,
        roof_uv_materials={color_id for color_id in used_ids if material_base(color_id) in roof_ids},
        explicit_uv_materials=trim.materials, cell_phase=cell_phase,
    )
    atomic_write_bytes(targets[0], visual)
    if collision.is_file():
        atomic_write_bytes(targets[1], collision.read_bytes())
    else:
        atomic_write_bytes(targets[1], b"")
    written = dict(manifest)
    written["fidelity"] = {
        **dict(manifest.get("fidelity") or {}),
        "materials": "copy; facades not regenerated; each material repeats its own albedo",
    }
    atomic_write_json(targets[2], written)
    report_src = base / "detail_report.json"
    detail_report: dict[str, Any] = {}
    if report_src.is_file():
        detail_report = json.loads(report_src.read_text(encoding="utf-8"))
    after_geo = geometry_fingerprint([o for o in objects if o.get("type") in GEOGRAPHIC])
    compare = compare_fingerprints(before_geo, after_geo)
    detail_report["materials"] = {
        "schema": "onward_generator.materials_apply_report",
        "schema_version": "0.1.0",
        "applied_ai_materials": False,
        "regenerated_facades": False,
        "geometry": compare,
        "source_base_sha256": before_hashes,
    }
    atomic_write_json(targets[3], detail_report)
    atomic_write_bytes(targets[4], built.png)
    if trim.png:
        atomic_write_bytes(targets[7], trim.png)
    snapshot = _catalog_snapshot(catalog, applied)
    atomic_write_json(targets[6], snapshot)
    materials_report = {
        "schema": "onward_generator.materials_report",
        "schema_version": "0.1.0",
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "generator_version": __version__,
        "base_dir": str(base),
        "plan": {"path": str(plan.path), "sha256": plan.sha256},
        "catalog": {"path": str(catalog.path), "sha256": catalog.sha256},
        "applied": applied,
        "skipped": skipped,
        "brick_nodes": brick_nodes,
        "cobble_applied": any(row["id"] == "streets_plazas" for row in applied),
        "atlas": {"size": built.size, "grid": built.tiles, "note": built.note},
        "textures": {
            "mode": "repeat",
            "materials": sorted(repeat_images),
            "unit_uv": sorted(UNIT_UV_MATERIALS & set(repeat_images)),
            "window": "project-generated frame, not a photograph",
            "interior": interior_shared,
            "facade_shared": facade_shared,
            "tint_shared": tint_shared,
            "shop_interior": shop_shared,
        },
        "facades": dict(trim.report),
        "geometry": compare,
        "input_hashes": before_hashes,
        "min_mean_luma": MIN_MEAN_LUMA,
    }
    if repetition_block is not None:
        materials_report["repetition"] = repetition_block
    if street_profile is not None:
        # The street profile record's block (plan D2): ids, provenance and the reply's hash. Never observed.
        materials_report["street_profile"] = dict(street_profile)
    atomic_write_json(targets[5], materials_report)
    if not compare["identical"]:
        raise MaterialsError("materials apply mutated geographic object records; this is a bug")
    return materials_report
