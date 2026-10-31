"""Materials stage: texture plan, Poly Haven client, atlas remap. No live network."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from onward_generator.cli import main
from onward_generator.dress.atlas import (
    MIN_MEAN_LUMA,
    MIN_TILE_PX,
    _tile_pixels,
    build_atlas,
    decode_jpeg_rgb,
    decode_png_rgba,
    encode_opaque_png,
    tile_luminance,
)
from onward_generator.dress.catalog import load_catalog
from onward_generator.dress.surfaces import osm_material_override
from onward_generator.generation.config import Material, load_generation_config
from onward_generator.generation.gltf import Node, Primitive, build_glb, images_by_material, nodes_from_glb, read_glb
from onward_generator.generation.mesh import Mesh
from onward_generator.materials.apply import (
    apply_materials,
    rematerialize_brick_walls,
    share_facade_photos,
    share_shop_interior_photos,
)
from onward_generator.materials.library_plan import select_texture_plan
from onward_generator.materials.plan import PlanError, load_texture_plan
from onward_generator.materials.polyhaven import DownloadError, TextureRecord, ensure_diffuse_1k, resolve_polyhaven_id
from tests.conftest import MURCIA_CENTER

REPO = Path(__file__).resolve().parents[2]


def _jpeg_bytes(color: tuple[int, int, int] = (180, 140, 90), size: int = 32) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (size, size), color).save(buf, format="JPEG", quality=95)
    return buf.getvalue()


_PLAN_COLORS = (
    ("coral_fort_wall_02", (200, 180, 140)),
    ("rock_wall_11", (160, 140, 110)),
    ("cobblestone_color", (120, 110, 90)),
    ("clay_roof_tiles_02", (170, 70, 40)),
    ("red_bricks_04", (140, 50, 40)),
    ("asphalt_02", (40, 40, 42)),
    ("concrete_floor_worn_001", (150, 148, 142)),
    ("concrete_floor_02", (170, 168, 160)),
    ("wood_planks", (140, 100, 60)),
    ("bark_brown_02", (90, 60, 40)),
    ("concrete_wall_001", (180, 178, 170)),
)


def _cache_plan(cache: Path) -> None:
    for asset, color in _PLAN_COLORS:
        _write_cached(cache, asset, color)


def _polyhaven_plan(path: Path) -> Path:
    """Fixture plan. The shipped generator no longer keeps a shared texture template."""
    rows = [
        ("historic_facades", "coral_fort_wall_02", ["historic_plaster_cream"], "greyscale_modulation"),
        ("ochre_facades", "coral_fort_wall_02", ["plaster_ochre_wall"], "color_albedo"),
        ("plain_facades", "concrete_wall_001", ["plaster_white_wall"], "color_albedo"),
        ("stone_monuments", "rock_wall_11", ["limestone_warm"], "color_albedo"),
        ("streets_plazas", "cobblestone_color", ["cobblestone_paving"], "color_albedo"),
        ("roofs", "clay_roof_tiles_02", ["terracotta_roof"], "color_albedo"),
        ("brick", "red_bricks_04", ["brick_red_wall"], "color_albedo"),
        ("carriageway", "asphalt_02", ["asphalt_road"], "color_albedo"),
        ("sidewalks", "concrete_floor_worn_001", ["sidewalk_concrete"], "color_albedo"),
        ("plain_pedestrian", "concrete_floor_02", ["pedestrian_plain"], "color_albedo"),
        ("doors", "wood_planks", ["wood_door"], "color_albedo"),
        ("trunks", "bark_brown_02", ["tree_bark"], "color_albedo"),
        ("flat_roofs", "concrete_wall_001", ["flat_roof_grey"], "greyscale_modulation"),
    ]
    path.write_text(json.dumps({
        "schema": "onward_generator.texture_plan",
        "schema_version": "0.1.0",
        "description": "Fixture Poly Haven plan for the materials tests.",
        "provider": "polyhaven",
        "resolution": "1k",
        "map": "Diffuse",
        "format": "jpg",
        "entries": [
            {
                "id": slot,
                "url": f"https://polyhaven.com/a/{asset}",
                "catalog_ids": catalog_ids,
                "albedo_mode": mode,
            }
            for slot, asset, catalog_ids, mode in rows
        ],
    }), encoding="utf-8")
    return path


def _write_cached(cache: Path, asset_id: str, color: tuple[int, int, int]) -> Path:
    jpeg = cache / asset_id / f"{asset_id}_diff_1k.jpg"
    jpeg.parent.mkdir(parents=True)
    payload = _jpeg_bytes(color)
    jpeg.write_bytes(payload)
    sidecar = {
        "provider": "polyhaven",
        "asset_id": asset_id,
        "license": "CC0",
        "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
        "authors": ["fixture"],
        "page_url": f"https://polyhaven.com/a/{asset_id}",
        "file_url": f"https://dl.polyhaven.org/fixture/{asset_id}.jpg",
        "md5": hashlib.md5(payload, usedforsecurity=False).hexdigest(),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
        "map": "Diffuse",
        "resolution": "1k",
        "format": "jpg",
        "path": str(jpeg),
        "retrieved_at": "2026-10-19T00:00:00+00:00",
        "user_agent": "test",
    }
    jpeg.with_name(jpeg.name + ".source.json").write_text(json.dumps(sidecar), encoding="utf-8")
    return jpeg


def _mesh() -> Mesh:
    verts = np.array([[0.0, 0.0, 0.0], [2.0, 0.0, 0.0], [0.0, 2.0, 0.0], [0.0, 0.0, 2.0]], dtype=np.float64)
    tris = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int64)
    return Mesh(verts, tris)


def _node(name: str, material: str, extras: dict[str, Any]) -> Node:
    return Node(name, (Primitive(_mesh(), material),), extras)


def _write_base(root: Path, *, brick: bool = False, cobble: bool = True, salmon: bool = False, party: bool = False,
                shop: bool = False) -> Path:
    _ = MURCIA_CENTER
    detail = root / "base"
    detail.mkdir()
    ochre = (0.93, 0.66, 0.28, 1.0)
    white = (0.93, 0.92, 0.89, 1.0)
    asphalt = (0.18, 0.18, 0.18, 1.0)
    pedestrian = (0.72, 0.68, 0.58, 1.0)
    stone = (0.84, 0.70, 0.46, 1.0)
    tile = (0.74, 0.30, 0.16, 1.0)
    flat = (0.58, 0.58, 0.57, 1.0)
    materials = {
        "plaster_ochre": Material(ochre, 0.9),
        "plaster_white": Material(white, 0.9),
        "carriageway": Material(asphalt, 1.0),
        "pedestrian": Material(pedestrian, 1.0),
        "stone_warm": Material(stone, 0.85),
        "roof_tile": Material(tile, 0.9),
        "roof_flat_grey": Material(flat, 0.95),
        "tree_canopy": Material((0.30, 0.46, 0.24, 1.0), 1.0),
        "interior_wall": Material((0.90, 0.89, 0.86, 1.0), 0.9),
        "interior_floor": Material((0.62, 0.55, 0.47, 1.0), 0.8),
        "stair": Material((0.70, 0.67, 0.62, 1.0), 0.85),
    }
    nodes = [
        _node("building/osm-way-1", "plaster_ochre", {"type": "building", "source_ids": ["osm-way-1"], "object_id": "building/osm-way-1"}),
        _node("building/osm-way-white", "plaster_white", {"type": "building", "source_ids": ["osm-way-white"], "object_id": "building/osm-way-white"}),
        _node("building/osm-way-stone", "stone_warm", {"type": "building", "source_ids": ["osm-way-stone"], "object_id": "building/osm-way-stone"}),
        _node("building/osm-way-roof", "roof_tile", {"type": "building", "source_ids": ["osm-way-roof"], "object_id": "building/osm-way-roof"}),
        _node("building/osm-way-flat", "roof_flat_grey", {"type": "building", "source_ids": ["osm-way-flat"], "object_id": "building/osm-way-flat"}),
        _node("surface/carriageway", "carriageway", {"type": "surface", "source_ids": ["osm-way-road"], "object_id": "surface/carriageway"}),
        _node("surface/pedestrian", "pedestrian", {"type": "surface", "source_ids": ["osm-way-other"], "object_id": "surface/pedestrian"}),
        _node("tree/1", "tree_canopy", {"type": "urban_element", "source_ids": [], "object_id": "tree/1"}),
        _node("interior/wall", "interior_wall", {"type": "interior", "source_ids": ["osm-way-1"], "object_id": "interior/wall"}),
        _node("interior/floor", "interior_floor", {"type": "interior", "source_ids": ["osm-way-1"], "object_id": "interior/floor"}),
        _node("interior/stair", "stair", {"type": "interior", "source_ids": ["osm-way-1"], "object_id": "interior/stair"}),
    ]
    if cobble:
        nodes.append(_node(
            "surface/pedestrian_cobble", "pedestrian",
            {"type": "surface", "source_ids": ["osm-way-plaza"], "object_id": "surface/pedestrian_cobble"},
        ))
    if salmon:
        materials["plaster_salmon"] = Material((0.88, 0.68, 0.58, 1.0), 0.9)
        nodes.append(_node(
            "building/osm-way-salmon", "plaster_salmon",
            {"type": "building", "source_ids": ["osm-way-salmon"], "object_id": "building/osm-way-salmon"},
        ))
    if brick:
        nodes.append(_node(
            "building/osm-way-brick", "plaster_ochre",
            {"type": "building", "source_ids": ["osm-way-brick"], "object_id": "building/osm-way-brick"},
        ))
    if shop:
        # A bakery and a bar: two wall tints, a tiled and a wooden floor, and a frameless display window.
        for name, colour in (("interior_shop_bakery", (0.94, 0.80, 0.54, 1.0)), ("interior_shop_bar", (0.70, 0.38, 0.26, 1.0)),
                             ("interior_shop_tile", (0.86, 0.82, 0.74, 1.0)), ("interior_shop_wood", (0.55, 0.37, 0.22, 1.0)),
                             ("shop_glass", (0.80, 0.87, 0.90, 1.0))):
            materials[name] = Material(colour, 0.6)
            nodes.append(_node(f"interior/{name}", name, {"type": "interior", "source_ids": ["osm-way-1"], "object_id": f"interior/{name}"}))
    if party:
        # A party wall beside its wall on the same mesh, so both get the same UV phase. Salmon is used only as a party wall.
        extra = {"building/osm-way-1": "party_plaster_ochre", "building/osm-way-white": "party_plaster_white",
                 "building/osm-way-stone": "party_plaster_salmon", "building/osm-way-brick": "party_plaster_ochre"}
        for name in set(extra.values()):
            materials[name] = materials.get(name.removeprefix("party_"), Material((0.88, 0.68, 0.58, 1.0), 0.9))
        nodes = [Node(n.name, (*n.primitives, Primitive(_mesh(), extra[n.name])), n.extras) if n.name in extra else n
                 for n in nodes]
    (detail / "scene_visual.glb").write_bytes(build_glb(nodes, materials))
    (detail / "scene_collision.glb").write_bytes(build_glb(nodes, materials))
    objects = [
        {"id": "building/osm-way-1", "type": "building", "source_ids": ["osm-way-1"], "footprint_area_m2": 80.0},
        {"id": "building/osm-way-white", "type": "building", "source_ids": ["osm-way-white"], "footprint_area_m2": 40.0},
        {"id": "building/osm-way-stone", "type": "building", "source_ids": ["osm-way-stone"], "footprint_area_m2": 90.0},
        {"id": "building/osm-way-roof", "type": "building", "source_ids": ["osm-way-roof"], "footprint_area_m2": 50.0},
        {"id": "building/osm-way-flat", "type": "building", "source_ids": ["osm-way-flat"], "footprint_area_m2": 50.0},
        {"id": "surface/carriageway", "type": "surface", "surface_class": "carriageway", "source_ids": ["osm-way-road"], "area_m2": 100.0},
        {"id": "surface/pedestrian", "type": "surface", "surface_class": "pedestrian", "source_ids": ["osm-way-other"], "area_m2": 80.0},
    ]
    if cobble:
        objects.append({
            "id": "surface/pedestrian_cobble", "type": "surface", "surface_class": "pedestrian",
            "paving": "cobble", "source_ids": ["osm-way-plaza"], "area_m2": 40.0,
        })
    if brick:
        objects.append({"id": "building/osm-way-brick", "type": "building", "source_ids": ["osm-way-brick"], "footprint_area_m2": 30.0})
    plaza_surface = "sett" if cobble else "asphalt"
    scene = root / "geo_scene.json"
    scene.write_text(json.dumps({
        "features": [
            {"id": "osm-way-1", "tags": {"building": "yes"}},
            {"id": "osm-way-white", "tags": {"building": "yes"}},
            {"id": "osm-way-stone", "tags": {"building": "yes", "building:material": "stone"}},
            {"id": "osm-way-roof", "tags": {"building": "yes", "roof:material": "tile"}},
            {"id": "osm-way-flat", "tags": {"building": "yes"}},
            {"id": "osm-way-road", "tags": {"highway": "residential", "surface": "asphalt"}},
            {"id": "osm-way-plaza", "tags": {"highway": "pedestrian", "surface": plaza_surface}},
            {"id": "osm-way-brick", "tags": {"building": "yes", "building:material": "brick"}},
        ]
    }), encoding="utf-8")
    (detail / "scene_detail.json").write_text(json.dumps({
        "schema": "onward_generator.scene_detail",
        "objects": objects,
        "inputs": {"geo_scene": {"path": str(scene)}},
        "attribution": {"osm": "© OpenStreetMap contributors"},
    }), encoding="utf-8")
    return detail


def _library_plan() -> dict:
    return select_texture_plan(
        name="Murcia", map_id="murcia", latitude=37.98407, longitude=-1.12863, brief=None, seed=1,
    )


def test_library_plan_keeps_gates(tmp_path: Path) -> None:
    path = tmp_path / "texture_plan.json"
    path.write_text(json.dumps(_library_plan()), encoding="utf-8")
    plan = load_texture_plan(path)
    assert plan.by_id["streets_plazas"].catalog_ids == ("cobblestone_paving",)
    assert plan.by_id["brick"].catalog_ids == ("brick_red_wall",)
    assert plan.by_id["carriageway"].catalog_ids == ("asphalt_road",)
    assert plan.by_id["plain_pedestrian"].catalog_ids == ("pedestrian_plain",)
    assert "asphalt_road" not in plan.by_id["streets_plazas"].catalog_ids
    assert "terracotta_roof" not in plan.by_id["flat_roofs"].catalog_ids
    assert plan.by_id["historic_facades"].albedo_mode == "greyscale_modulation"
    assert "plaster_white_wall" not in plan.by_id["historic_facades"].catalog_ids
    assert load_catalog().get("brick_red_wall") is not None
    assert load_catalog().get("brick_red_wall").color_id == "brick_red"


def test_plan_rejects_cobble_on_asphalt(tmp_path: Path) -> None:
    doc = _library_plan()
    cobble = next(e for e in doc["entries"] if e["id"] == "streets_plazas")
    cobble["catalog_ids"] = ["asphalt_road"]
    path = tmp_path / "bad_plan.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(PlanError):
        load_texture_plan(path)


def test_plan_rejects_brick_on_all_walls(tmp_path: Path) -> None:
    doc = _library_plan()
    brick = next(e for e in doc["entries"] if e["id"] == "brick")
    brick["catalog_ids"] = ["historic_plaster_cream", "plaster_ochre_wall", "brick_red_wall"]
    path = tmp_path / "bad_brick.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(PlanError):
        load_texture_plan(path)


def test_resolve_polyhaven_id_strips_query() -> None:
    assert resolve_polyhaven_id("https://polyhaven.com/a/rock_wall_11?utm_source=chatgpt.com") == "rock_wall_11"
    with pytest.raises(DownloadError):
        resolve_polyhaven_id("https://example.com/a/rock_wall_11")


def test_download_accepts_missing_license_as_polyhaven_cc0(tmp_path: Path) -> None:
    payload = _jpeg_bytes()
    md5 = hashlib.md5(payload, usedforsecurity=False).hexdigest()

    def fetch(url: str) -> bytes:
        if "/info/" in url:
            return json.dumps({"license": None, "authors": {"Dimitrios Savva": "All"}}).encode()
        if "/files/" in url:
            return json.dumps({
                "Diffuse": {"1k": {"jpg": {
                    "url": "https://dl.polyhaven.org/file/ph-assets/Textures/jpg/1k/coral_fort_wall_02/x.jpg",
                    "md5": md5,
                    "size": len(payload),
                }}}
            }).encode()
        return payload

    record = ensure_diffuse_1k(
        "https://polyhaven.com/a/coral_fort_wall_02",
        cache_dir=tmp_path,
        download=True,
        fetch=fetch,
    )
    assert record.license == "CC0"
    assert record.authors == ("Dimitrios Savva",)


def test_download_refused_when_license_is_not_cc0(tmp_path: Path) -> None:
    payload = _jpeg_bytes()

    def fetch(url: str) -> bytes:
        if "/info/" in url:
            return json.dumps({"license": "MIT", "authors": {"x": "a"}}).encode()
        if "/files/" in url:
            return json.dumps({
                "Diffuse": {"1k": {"jpg": {"url": "https://dl.polyhaven.org/file/x.jpg", "md5": "0" * 32, "size": 1}}}
            }).encode()
        return payload

    with pytest.raises(DownloadError, match="not CC0"):
        ensure_diffuse_1k(
            "https://polyhaven.com/a/coral_fort_wall_02",
            cache_dir=tmp_path,
            download=True,
            fetch=fetch,
        )


def test_missing_cache_without_download_fails(tmp_path: Path) -> None:
    with pytest.raises(DownloadError, match="not cached"):
        ensure_diffuse_1k("https://polyhaven.com/a/coral_fort_wall_02", cache_dir=tmp_path, download=False)


def test_osm_brick_override() -> None:
    assert osm_material_override("wall", {"building:material": "brick"}) == "brick_red_wall"
    assert osm_material_override("wall", {"building:material": "stone"}) == "limestone_warm"


def test_apply_remaps_only_listed_ids(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _cache_plan(cache)
    base = _write_base(tmp_path, brick=False, cobble=True)
    out = tmp_path / "materials"
    report = apply_materials(base, out, cache_dir=cache, download=False, plan_path=_polyhaven_plan(tmp_path / "plan.json"))
    assert report["geometry"]["identical"] is True
    assert any(row["catalog_id"] == "plaster_ochre_wall" for row in report["applied"])
    assert any(row["reason"] == "no_osm_brick_buildings" for row in report["skipped"])
    assert report["cobble_applied"] is True
    doc, _ = read_glb((out / "scene_visual.glb").read_bytes())
    by_name = {m["name"]: m["pbrMetallicRoughness"]["baseColorFactor"] for m in doc["materials"]}
    assert by_name["carriageway"][:3] == [1.0, 1.0, 1.0]
    assert by_name["pedestrian"][:3] == [1.0, 1.0, 1.0]
    assert by_name["plaster_white"][0] > 0.85
    assert by_name["plaster_ochre"][:3] == [1.0, 1.0, 1.0]
    assert by_name["stone_warm"][:3] == [1.0, 1.0, 1.0]
    assert by_name["cobblestone_paving"][:3] == [1.0, 1.0, 1.0]
    assert by_name["roof_tile"][:3] == [1.0, 1.0, 1.0]
    assert by_name["roof_flat_grey"][0] < 0.7
    assert by_name["interior_wall"][:3] == [1.0, 1.0, 1.0]
    assert by_name["interior_floor"][:3] == [1.0, 1.0, 1.0]
    assert by_name["stair"][:3] == [1.0, 1.0, 1.0]
    assert {row["color_id"] for row in report["textures"]["interior"]} == {"interior_wall", "interior_floor", "stair"}
    assert report["textures"]["facade_shared"] == []
    assert "brick_red" not in by_name
    atlas = decode_png_rgba((out / "atlas.png").read_bytes())
    unused = atlas[atlas.shape[0] // 2 :, atlas.shape[1] // 2 :]
    assert tile_luminance(unused) >= MIN_MEAN_LUMA
    assert (base / "scene_visual.glb").read_bytes() != (out / "scene_visual.glb").read_bytes()


def test_apply_threads_write_the_bytes_of_the_serial_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = tmp_path / "cache"
    _cache_plan(cache)
    base = _write_base(tmp_path, brick=False, cobble=True, salmon=True)
    plan = _polyhaven_plan(tmp_path / "plan.json")
    apply_materials(base, tmp_path / "threaded", cache_dir=cache, download=False, plan_path=plan)
    monkeypatch.setattr("onward_generator.materials.apply.run_png_jobs", lambda jobs: {key: job() for key, job in jobs.items()})
    apply_materials(base, tmp_path / "serial", cache_dir=cache, download=False, plan_path=plan)
    for name in ("scene_visual.glb", "atlas.png"):
        threaded = hashlib.sha256((tmp_path / "threaded" / name).read_bytes()).hexdigest()
        assert threaded == hashlib.sha256((tmp_path / "serial" / name).read_bytes()).hexdigest()
    # A colour with no photograph keeps the procedural tile seeded by its place among the used colours.
    images = images_by_material((tmp_path / "threaded" / "scene_visual.glb").read_bytes())
    index = sorted(images).index("tree_canopy")
    assert index > 0
    assert images["tree_canopy"] == encode_opaque_png(_tile_pixels("tree_canopy", 256, 48152 + index))


def test_apply_brick_only_when_osm_says_brick(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _cache_plan(cache)
    base = _write_base(tmp_path, brick=True, cobble=False)
    out = tmp_path / "with_brick"
    report = apply_materials(base, out, cache_dir=cache, download=False, plan_path=_polyhaven_plan(tmp_path / "plan_brick.json"))
    assert "building/osm-way-brick" in report["brick_nodes"]
    assert report["cobble_applied"] is False
    doc, _ = read_glb((out / "scene_visual.glb").read_bytes())
    names = {m["name"] for m in doc["materials"]}
    assert "brick_red" in names
    brick = next(m for m in doc["materials"] if m["name"] == "brick_red")
    assert brick["pbrMetallicRoughness"]["baseColorFactor"][:3] == [1.0, 1.0, 1.0]


def test_salmon_walls_borrow_a_plaster_photo_in_greyscale_under_their_own_tint(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _cache_plan(cache)
    base = _write_base(tmp_path, salmon=True)
    out = tmp_path / "with_salmon"
    report = apply_materials(base, out, cache_dir=cache, download=False, plan_path=_polyhaven_plan(tmp_path / "plan.json"))
    assert report["textures"]["facade_shared"] == [{
        "color_id": "plaster_salmon", "slot": "plain_facades", "albedo_mode": "greyscale_modulation",
        "asset_id": "concrete_wall_001",
    }]
    data = (out / "scene_visual.glb").read_bytes()
    doc, _ = read_glb(data)
    salmon = next(m for m in doc["materials"] if m["name"] == "plaster_salmon")
    assert salmon["pbrMetallicRoughness"]["baseColorFactor"] == pytest.approx([0.88, 0.68, 0.58, 1.0])
    tile = decode_png_rgba(images_by_material(data)["plaster_salmon"])[..., :3].astype(int)
    assert (tile[..., 0] == tile[..., 1]).all() and (tile[..., 1] == tile[..., 2]).all()
    # The photograph is concrete_wall_001 (180, 178, 170), not the procedural tile.
    assert abs(int(tile.mean()) - 178) <= 2


def test_shop_interiors_take_shared_library_photographs_and_the_display_window_a_flat_tint(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _cache_plan(cache)
    out = tmp_path / "shop"
    report = apply_materials(_write_base(tmp_path, shop=True), out, cache_dir=cache, plan_path=_polyhaven_plan(tmp_path / "plan.json"))
    rows = {row["color_id"]: row for row in report["textures"]["shop_interior"]}
    assert {k: (r["library_id"], r["albedo_mode"], r["tile_m"]) for k, r in rows.items()} == {
        "interior_shop_bakery": ("ph_plastered_wall", "greyscale_modulation", 2.0),
        "interior_shop_bar": ("ph_plastered_wall", "greyscale_modulation", 2.0),
        "interior_shop_tile": ("acg_tiles139", "color_albedo", 2.0),
        "interior_shop_wood": ("ph_wood_planks", "color_albedo", 2.0),
    }
    assert rows["interior_shop_wood"]["license"] == "CC0-1.0" and rows["interior_shop_wood"]["repo_path"] == (
        "assets/textures/wood_planks/wood_planks_diff_1k.jpg")
    assert rows["interior_shop_wood"]["provider"] == "polyhaven" and rows["interior_shop_tile"]["provider"] == "ambientcg"
    assert rows["interior_shop_wood"]["sha256"] == "3b0669f683e4bf10f5a55a381cfa9669a7b8dfd921901829daa3b35acc2bbdec"
    data = (out / "scene_visual.glb").read_bytes()
    doc, _ = read_glb(data)
    mats = {m["name"]: m["pbrMetallicRoughness"] for m in doc["materials"]}
    # A wall keeps its category tint over a grey plaster; a floor shows its photograph under white.
    assert mats["interior_shop_bakery"]["baseColorFactor"] == pytest.approx([0.94, 0.80, 0.54, 1.0])
    assert mats["interior_shop_tile"]["baseColorFactor"] == mats["interior_shop_wood"]["baseColorFactor"] == [1.0, 1.0, 1.0, 1.0]
    assert mats["interior_shop_wood"]["roughnessFactor"] == 0.6
    # Shared, not one per shop: the two walls use one texture.
    assert mats["interior_shop_bakery"]["baseColorTexture"]["index"] == mats["interior_shop_bar"]["baseColorTexture"]["index"]
    assert mats["interior_shop_bakery"]["baseColorTexture"]["index"] != mats["interior_shop_tile"]["baseColorTexture"]["index"]
    images = images_by_material(data)
    wall = decode_png_rgba(images["interior_shop_bakery"])
    assert wall.shape == (1024, 1024, 4) and (wall[..., 0] == wall[..., 2]).all()
    wood = decode_png_rgba(images["interior_shop_wood"])[..., :3].astype(int)
    assert wood[..., 0].mean() > wood[..., 2].mean() + 20  # the brown photograph, not a grey procedural tile
    # The display window: a flat pale tint at 20 % alpha, no frame, on 0-1 UVs, out of the opaque atlas.
    glass = decode_png_rgba(images["shop_glass"])
    assert glass.shape == (16, 16, 4) and (glass == [196, 212, 220, 51]).all()
    assert mats["shop_glass"]["baseColorFactor"] == [1.0, 1.0, 1.0, 1.0]
    [pane] = [p for n in nodes_from_glb(data)[0] if n.name == "interior/shop_glass" for p in n.primitives]
    assert pane.uvs.min() == 0.0 and pane.uvs.max() == 1.0
    assert doc["extras"]["atlas"]["tiles"] == len(mats) - 1  # every material but the shop glass


def test_share_shop_interior_photos_leaves_a_scene_without_shops_alone() -> None:
    photos: dict[str, Any] = {}
    materials = {"interior_wall": Material((0.9, 0.9, 0.9, 1.0), 0.9)}
    tile_m: dict[str, float] = {}
    assert share_shop_interior_photos({"interior_wall"}, photos, materials, tile_m) == []
    assert photos == {} and tile_m == {} and materials == {"interior_wall": Material((0.9, 0.9, 0.9, 1.0), 0.9)}


def test_brick_walls_keep_their_uvs_and_their_party_walls() -> None:
    uvs = np.array([[0.0, 0.0], [1.0, 0.0], [0.0, 1.0], [0.0, 0.5]])
    node = Node("building/b", (Primitive(_mesh(), "plaster_ochre", uvs), Primitive(_mesh(), "party_plaster_white", uvs),
                               Primitive(_mesh(), "glass_dark", uvs)), {"type": "building", "source_ids": ["b"]})
    [out], changed = rematerialize_brick_walls([node], {"b": {"tags": {"building:material": "brick"}}})
    assert changed == ["building/b"]
    assert [p.material for p in out.primitives] == ["brick_red", "party_brick_red", "glass_dark"]
    assert all(p.uvs is uvs for p in out.primitives)


def test_party_walls_take_their_wall_albedo_colour_and_repeat(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _cache_plan(cache)
    base = _write_base(tmp_path, brick=True, party=True)
    out = tmp_path / "with_party"
    report = apply_materials(base, out, cache_dir=cache, download=False, plan_path=_polyhaven_plan(tmp_path / "plan.json"))
    data = (out / "scene_visual.glb").read_bytes()
    doc, _ = read_glb(data)
    factor = {m["name"]: m["pbrMetallicRoughness"]["baseColorFactor"] for m in doc["materials"]}
    texture = {m["name"]: m["pbrMetallicRoughness"]["baseColorTexture"]["index"] for m in doc["materials"]}
    # The contact sheet has one tile per wall, not one per party copy.
    assert doc["extras"]["atlas"]["tiles"] == len({name.removeprefix("party_") for name in factor} - {"glass_view"})
    # The OSM brick building's party wall becomes a brick party wall.
    assert "party_plaster_ochre" in factor and "party_brick_red" in factor
    for party, wall in (("party_plaster_ochre", "plaster_ochre"), ("party_plaster_white", "plaster_white"),
                        ("party_brick_red", "brick_red")):
        assert factor[party] == factor[wall] == [1.0, 1.0, 1.0, 1.0]
        assert texture[party] == texture[wall]
    # Salmon appears only as a party wall: it still borrows the plaster photograph under its own tint.
    assert [row["color_id"] for row in report["textures"]["facade_shared"]] == ["plaster_salmon"]
    assert factor["party_plaster_salmon"] == pytest.approx([0.88, 0.68, 0.58, 1.0])
    tile = decode_png_rgba(images_by_material(data)["party_plaster_salmon"])[..., :3].astype(int)
    assert (tile[..., 0] == tile[..., 1]).all() and abs(int(tile.mean()) - 178) <= 2
    # Same mesh, same building: a party wall repeats exactly like its wall (plaster_white 4 m, brick 1.2 m).
    uvs = {(n.name, p.material): p.uvs for n in nodes_from_glb(data)[0] for p in n.primitives}
    assert np.array_equal(uvs[("building/osm-way-white", "party_plaster_white")], uvs[("building/osm-way-white", "plaster_white")])
    assert np.array_equal(uvs[("building/osm-way-brick", "party_brick_red")], uvs[("building/osm-way-brick", "brick_red")])
    assert not np.array_equal(uvs[("building/osm-way-white", "plaster_white")], uvs[("building/osm-way-brick", "brick_red")])


def _record(tmp_path: Path, name: str, color: tuple[int, int, int]) -> TextureRecord:
    path = tmp_path / f"{name}.jpg"
    path.write_bytes(_jpeg_bytes(color))
    return TextureRecord("library", name, "CC0", "", ("fixture",), "", "", "", "", 0, "Diffuse", "1k", "jpg", str(path), "", "test")


def test_share_facade_photos_takes_the_first_plaster_slot_the_plan_has(tmp_path: Path) -> None:
    ochre, cream = _record(tmp_path, "ochre", (200, 150, 60)), _record(tmp_path, "cream", (220, 200, 160))
    tiles: dict[str, tuple[Any, str]] = {}
    shared = share_facade_photos({"historic_facades": cream, "ochre_facades": ochre}, {"plaster_salmon"}, tiles)
    assert shared == [{"color_id": "plaster_salmon", "slot": "ochre_facades", "albedo_mode": "greyscale_modulation", "asset_id": "ochre"}]
    rgb, mode = tiles["plaster_salmon"]
    assert mode == "greyscale_modulation"
    assert np.array_equal(rgb, decode_jpeg_rgb(Path(ochre.path).read_bytes()))
    # Not used, already textured, or no plaster slot in the plan: nothing is shared.
    assert share_facade_photos({"ochre_facades": ochre}, {"plaster_ochre"}, {}) == []
    kept = {"plaster_salmon": ("photo", "color_albedo")}
    assert share_facade_photos({"ochre_facades": ochre}, {"plaster_salmon"}, kept) == []
    assert kept == {"plaster_salmon": ("photo", "color_albedo")}
    empty: dict[str, tuple[Any, str]] = {}
    assert share_facade_photos({"doors": ochre}, {"plaster_salmon"}, empty) == [] and empty == {}


def test_a_warm_tile_cache_writes_the_bytes_of_a_cold_one_without_baking(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = tmp_path / "cache"
    _cache_plan(cache)
    base = _write_base(tmp_path, cobble=True, salmon=True)
    plan = _polyhaven_plan(tmp_path / "plan.json")
    apply_materials(base, tmp_path / "off", cache_dir=cache, download=False, plan_path=plan)
    monkeypatch.delenv("ONWARD_GENERATOR_NO_TILE_CACHE")
    monkeypatch.setenv("ONWARD_GENERATOR_CACHE_DIR", str(tmp_path / "tiles_home"))
    tiles = tmp_path / "tiles_home" / "tiles"
    apply_materials(base, tmp_path / "unused", cache_dir=cache, download=False, plan_path=plan, tile_cache=False)
    assert not tiles.exists()
    report = apply_materials(base, tmp_path / "cold", cache_dir=cache, download=False, plan_path=plan)
    photos = {row["color_id"] for row in report["applied"]} | {row["color_id"] for row in report["textures"]["interior"]}
    photos |= {row["color_id"] for row in report["textures"]["facade_shared"]}
    # Two colours can share one photograph, mode and side, and so one entry.
    assert 0 < len(list(tiles.iterdir())) <= len(photos)

    def no_bake(*_: object) -> None:
        raise AssertionError("a warm cache does not bake again")

    monkeypatch.setattr("onward_generator.materials.apply.bake_photo_tile", no_bake)
    apply_materials(base, tmp_path / "warm", cache_dir=cache, download=False, plan_path=plan)
    for name in ("scene_visual.glb", "atlas.png"):
        cold = (tmp_path / "cold" / name).read_bytes()
        assert cold == (tmp_path / "off" / name).read_bytes() == (tmp_path / "warm" / name).read_bytes()


def test_apply_help_names_the_tile_cache_switch(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["materials", "apply", "--help"]) == 0
    text = " ".join(capsys.readouterr().out.split())
    assert "--no-tile-cache bake every photo tile again instead of reading the tile cache" in text


def test_cli_no_tile_cache_leaves_the_cache_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = tmp_path / "cache"
    _cache_plan(cache)
    base = _write_base(tmp_path)
    plan = _polyhaven_plan(tmp_path / "plan.json")
    monkeypatch.delenv("ONWARD_GENERATOR_NO_TILE_CACHE")
    monkeypatch.setenv("ONWARD_GENERATOR_CACHE_DIR", str(tmp_path / "tiles_home"))
    args = ["materials", "apply", "--base", str(base), "--cache", str(cache), "--plan", str(plan)]
    assert main([*args, "--out", str(tmp_path / "first"), "--no-tile-cache"]) == 0
    assert not (tmp_path / "tiles_home").exists()
    assert main([*args, "--out", str(tmp_path / "second")]) == 0
    assert list((tmp_path / "tiles_home" / "tiles").iterdir())


def test_cli_refuses_protected_output(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _cache_plan(cache)
    base = _write_base(tmp_path)
    assert main([
        "materials", "apply",
        "--base", str(base),
        "--out", str(tmp_path / "scene_detail_dress_02"),
        "--cache", str(cache),
    ]) == 1
    assert main([
        "materials", "apply",
        "--base", str(base),
        "--out", str(tmp_path / "scene_detail_materials_01"),
        "--cache", str(cache),
    ]) == 1


def test_atlas_photo_and_unused_luma() -> None:
    catalog = load_catalog()
    materials = dict(load_generation_config().materials)
    rgb = np.full((16, 16, 3), 90, dtype=np.uint8)
    built = build_atlas(
        catalog, materials,
        used_color_ids=["plaster_ochre", "tree_canopy", "urban_metal", "carriageway"],
        photo_tiles={"plaster_ochre": (rgb, "greyscale_modulation")},
    )
    image = decode_png_rgba(built.png)
    assert image.shape[0] >= 512
    assert built.size // built.tiles >= MIN_TILE_PX
    assert MIN_TILE_PX >= 256
    # unused corner stays mid-grey
    assert tile_luminance(image[-64:, -64:]) >= MIN_MEAN_LUMA
    assert "photographic" in built.note


def test_plaster_tile_m_is_two_metres() -> None:
    catalog = load_catalog()
    assert catalog.get("historic_plaster_cream").technical["tile_m"] == 2.0
    assert catalog.get("plaster_ochre_wall").technical["tile_m"] == 2.0


def test_scenic_walls_share_a_plaster_photograph(tmp_path: Path) -> None:
    plain, ochre = _record(tmp_path, "plain", (180, 178, 170)), _record(tmp_path, "ochre", (200, 150, 60))
    tiles: dict[str, tuple[Any, str]] = {}
    shared = share_facade_photos({"plain_facades": plain, "ochre_facades": ochre}, {"scenic_wall_ochre", "scenic_wall_light"}, tiles)
    assert [row["color_id"] for row in shared] == ["scenic_wall_light", "scenic_wall_ochre"]
    assert {row["slot"] for row in shared} == {"plain_facades"}
    assert {mode for _rgb, mode in tiles.values()} == {"greyscale_modulation"}
    only_ochre: dict[str, tuple[Any, str]] = {}
    assert share_facade_photos({"ochre_facades": ochre}, {"scenic_wall_warm"}, only_ochre)[0]["slot"] == "ochre_facades"
