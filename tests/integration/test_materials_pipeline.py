"""materials apply on a synthetic Murcia block. No live Poly Haven download."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from onward_generator.cli import main
from onward_generator.dress.geometry import compare_fingerprints, geometry_fingerprint
from onward_generator.generation.config import Material
from onward_generator.generation.gltf import Node, Primitive, build_glb, cell_uv_phase, nodes_from_glb, read_glb
from onward_generator.generation.mesh import Mesh
from onward_generator.materials.apply import apply_materials
from onward_generator.materials.library_plan import select_texture_plan
from onward_generator.materials.plan import PlanError
from tests.conftest import MURCIA_CENTER


def _cache_asset(cache: Path, asset_id: str, color: tuple[int, int, int]) -> None:
    jpeg = cache / asset_id / f"{asset_id}_diff_1k.jpg"
    jpeg.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), color).save(buf, format="JPEG", quality=90)
    payload = buf.getvalue()
    jpeg.write_bytes(payload)
    sidecar = {
        "provider": "polyhaven",
        "asset_id": asset_id,
        "license": "CC0",
        "authors": ["fixture"],
        "page_url": f"https://polyhaven.com/a/{asset_id}",
        "file_url": f"https://dl.polyhaven.org/fixture/{asset_id}.jpg",
        "md5": hashlib.md5(payload, usedforsecurity=False).hexdigest(),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
        "map": "Diffuse",
        "resolution": "1k",
        "format": "jpg",
        "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
        "path": str(jpeg),
        "retrieved_at": "2026-10-19T00:00:00+00:00",
        "user_agent": "test",
    }
    jpeg.with_name(jpeg.name + ".source.json").write_text(json.dumps(sidecar), encoding="utf-8")


def _polyhaven_plan(path: Path) -> Path:
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
        "description": "Fixture Poly Haven plan. The generator no longer ships a shared texture template.",
        "provider": "polyhaven",
        "resolution": "1k",
        "map": "Diffuse",
        "format": "jpg",
        "entries": [
            {"id": slot, "url": f"https://polyhaven.com/a/{asset}", "catalog_ids": catalog_ids, "albedo_mode": mode}
            for slot, asset, catalog_ids, mode in rows
        ],
    }), encoding="utf-8")
    return path


def test_materials_apply_on_detail_copy(detail_inputs: tuple[Path, Path, Path], tmp_path: Path) -> None:
    lon, lat = MURCIA_CENTER
    assert (lon, lat) == MURCIA_CENTER
    scene, heights, base = detail_inputs
    detail = tmp_path / "detail"
    assert main(["detail", "--scene", str(scene), "--heights", str(heights), "--base", str(base),
                 "--out", str(detail), "--no-preview"]) == 0
    before = json.loads((detail / "scene_detail.json").read_text())["objects"]
    cache = tmp_path / "cache"
    for asset, color in (
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
    ):
        _cache_asset(cache, asset, color)
    out = tmp_path / "scene_detail_materials_itest"
    plan = _polyhaven_plan(tmp_path / "texture_plan.json")
    assert main([
        "materials", "apply",
        "--base", str(detail),
        "--scene", str(scene),
        "--plan", str(plan),
        "--out", str(out),
        "--cache", str(cache),
    ]) == 0
    after = json.loads((out / "scene_detail.json").read_text())["objects"]
    keep = {"building", "surface", "curb", "landmark_part"}
    compare = compare_fingerprints(
        geometry_fingerprint([o for o in before if o.get("type") in keep]),
        geometry_fingerprint([o for o in after if o.get("type") in keep]),
    )
    assert compare["identical"], compare
    report = json.loads((out / "materials_report.json").read_text())
    assert report["geometry"]["identical"] is True
    assert (out / "atlas.png").read_bytes().startswith(b"\x89PNG")
    doc, _ = read_glb((out / "scene_visual.glb").read_bytes())
    assert doc.get("images")
    names = {m["name"] for m in doc["materials"]}
    assert "carriageway" in names
    # asphalt stays a generation colour, not remapped to cobble
    carriage = next(m for m in doc["materials"] if m["name"] == "carriageway")
    assert carriage["pbrMetallicRoughness"]["baseColorFactor"][:3] == [1.0, 1.0, 1.0]
    for name in ("interior_wall", "interior_floor", "stair"):
        surface = next(m for m in doc["materials"] if m["name"] == name)
        assert surface["pbrMetallicRoughness"]["baseColorFactor"][:3] == [1.0, 1.0, 1.0]
        assert "baseColorTexture" in surface["pbrMetallicRoughness"]
    assert (detail / "scene_visual.glb").read_bytes() != (out / "scene_visual.glb").read_bytes()


# ---- Plan C1: the "repetition" switch (ADR-009 "Repetition per cell") on a small block around MURCIA_CENTER.

def _c1_quad(x0: float, y0: float, x1: float, y1: float, z: float = 0.0) -> Mesh:
    return Mesh(np.array([[x0, y0, z], [x1, y0, z], [x1, y1, z], [x0, y1, z]], dtype=np.float64),
                np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int64))


def _c1_base(root: Path) -> Path:
    """Sidewalk islands in two combine cells, a carriageway, a cobble plaza, a tiled roof and a door."""
    def merge(*meshes: Mesh) -> Mesh:
        offsets = np.cumsum([0] + [len(m.vertices) for m in meshes[:-1]])
        return Mesh(np.concatenate([m.vertices for m in meshes]),
                    np.concatenate([m.triangles + o for m, o in zip(meshes, offsets, strict=True)]))

    base = root / "base"
    base.mkdir()
    grey = (0.6, 0.6, 0.6, 1.0)
    materials = {name: Material(grey, 0.9) for name in ("sidewalk", "carriageway", "pedestrian", "roof_tile", "plaster_white", "door_wood")}

    def surface(name: str, mesh: Mesh, material: str) -> Node:
        return Node(name, (Primitive(mesh, material),), {"type": "surface", "source_ids": [], "object_id": name})

    building = Node("building/osm-way-1", (
        Primitive(_c1_quad(20, 20, 30, 30, 0.0), "plaster_white"),
        Primitive(_c1_quad(20, 20, 30, 30, 9.0), "roof_tile"),
        Primitive(_c1_quad(21, 20, 22, 20.5, 0.0), "door_wood"),
    ), {"type": "building", "source_ids": ["osm-way-1"], "object_id": "building/osm-way-1"})
    nodes = [
        surface("surface/sidewalk", merge(_c1_quad(10, -20, 14, -16), _c1_quad(130, -20, 134, -16)), "sidewalk"),
        surface("surface/carriageway", _c1_quad(-40, -5, 60, 5), "carriageway"),
        surface("surface/pedestrian_cobble", _c1_quad(-60, 40, -40, 60), "pedestrian"),
        building,
    ]
    (base / "scene_visual.glb").write_bytes(build_glb(nodes, materials))
    (base / "scene_collision.glb").write_bytes(build_glb(nodes, materials))
    objects = [
        {"id": "building/osm-way-1", "type": "building", "source_ids": ["osm-way-1"], "footprint_area_m2": 100.0},
        {"id": "surface/sidewalk", "type": "surface", "surface_class": "sidewalk", "source_ids": [], "area_m2": 32.0},
        {"id": "surface/carriageway", "type": "surface", "surface_class": "carriageway", "source_ids": [], "area_m2": 1000.0},
        {"id": "surface/pedestrian_cobble", "type": "surface", "surface_class": "pedestrian", "paving": "cobble",
         "source_ids": [], "area_m2": 400.0},
    ]
    (base / "scene_detail.json").write_text(json.dumps({
        "schema": "onward_generator.scene_detail", "objects": objects,
        "origin": {"longitude": MURCIA_CENTER[0], "latitude": MURCIA_CENTER[1]},
        "attribution": {"osm": "© OpenStreetMap contributors"},
    }), encoding="utf-8")
    return base


def _c1_plan(path: Path, **override: str) -> Path:
    lon, lat = MURCIA_CENTER
    document = select_texture_plan(name="Murcia", map_id="murcia", latitude=lat, longitude=lon, brief=None, seed=1)
    for entry in document["entries"]:
        if entry["id"] in override:
            entry["library_id"] = override[entry["id"]]
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _c1_report(out: Path) -> dict:
    report = json.loads((out / "materials_report.json").read_text(encoding="utf-8"))
    report.pop("created_at")
    report.pop("base_dir")
    return report


def _c1_uvs(out: Path) -> dict[str, list]:
    nodes, _ = nodes_from_glb((out / "scene_visual.glb").read_bytes())
    return {node.name: [p.uvs for p in node.primitives] for node in nodes}


def test_repetition_off_writes_what_the_default_writes(tmp_path: Path) -> None:
    base, plan = _c1_base(tmp_path), _c1_plan(tmp_path / "plan.json")
    apply_materials(base, tmp_path / "default", plan_path=plan, tile_cache=False)
    apply_materials(base, tmp_path / "off", plan_path=plan, tile_cache=False, repetition=False, cell_m=50.0)
    for name in ("scene_visual.glb", "scene_collision.glb", "atlas.png", "materials.catalog.json"):
        assert (tmp_path / "default" / name).read_bytes() == (tmp_path / "off" / name).read_bytes(), name
    report = _c1_report(tmp_path / "off")
    assert report == _c1_report(tmp_path / "default")
    assert "repetition" not in report
    doc = read_glb((tmp_path / "off" / "scene_visual.glb").read_bytes())[0]
    assert "repetition" not in doc["extras"]["materials"]


def test_repetition_on_reports_the_slots_and_phases_the_ground_per_cell(tmp_path: Path) -> None:
    base, plan = _c1_base(tmp_path), _c1_plan(tmp_path / "plan.json")
    apply_materials(base, tmp_path / "off", plan_path=plan, tile_cache=False)
    report = apply_materials(base, tmp_path / "on", plan_path=plan, tile_cache=False, repetition=True)
    apply_materials(base, tmp_path / "again", plan_path=plan, tile_cache=False, repetition=True)
    block = report["repetition"]
    assert block["enabled"] is True and block["cell_m"] == 100.0
    assert block["cell_phase_materials"] == ["carriageway", "cobblestone_paving", "pedestrian", "sidewalk"]
    by_slot = {row["slot"]: row for row in block["slots"]}
    applied = {row["id"] for row in report["applied"]}
    assert set(by_slot) == applied
    assert {"roofs", "streets_plazas", "doors", "sidewalks", "carriageway"} <= applied
    assert by_slot["doors"]["status"] == "not_applicable" and by_slot["doors"]["coarse_contrast"] is None
    for slot, limit in (("roofs", 0.019), ("streets_plazas", 0.016), ("sidewalks", 0.012), ("carriageway", 0.012)):
        assert by_slot[slot]["status"] == "measured" and by_slot[slot]["limit"] == limit, slot
        assert 0 < by_slot[slot]["coarse_contrast"] <= limit and by_slot[slot]["over_limit"] is False, slot
    assert block["over_limit"] == []
    saved = json.loads((tmp_path / "on" / "materials_report.json").read_text(encoding="utf-8"))
    assert saved["repetition"] == block

    off_doc, on_doc = (read_glb((tmp_path / name / "scene_visual.glb").read_bytes())[0] for name in ("off", "on"))
    assert on_doc["extras"]["materials"]["repetition"] == {"cell_m": 100.0}
    for key in ("materials", "images", "textures", "meshes", "samplers"):
        assert off_doc[key] == on_doc[key], key
    assert [a["count"] for a in off_doc["accessors"]] == [a["count"] for a in on_doc["accessors"]]
    assert (tmp_path / "on" / "scene_collision.glb").read_bytes() == (tmp_path / "off" / "scene_collision.glb").read_bytes()
    assert (tmp_path / "on" / "scene_visual.glb").read_bytes() == (tmp_path / "again" / "scene_visual.glb").read_bytes()

    off, on = _c1_uvs(tmp_path / "off"), _c1_uvs(tmp_path / "on")
    for name in ("surface/sidewalk", "surface/carriageway", "surface/pedestrian_cobble"):
        assert not np.allclose(off[name][0], on[name][0]), name
    gap = np.mod(on["surface/sidewalk"][0].astype(np.float64) - off["surface/sidewalk"][0].astype(np.float64), 1.0)
    for rows, cell in ((slice(0, 4), (0, 0)), (slice(4, 8), (1, 0))):
        shift = np.mod(np.array(cell_uv_phase("sidewalk", cell)), 1.0)
        assert np.all(np.minimum(np.abs(gap[rows] - shift), 1 - np.abs(gap[rows] - shift)) < 1e-5), cell
    for before, after in zip(off["building/osm-way-1"], on["building/osm-way-1"], strict=True):
        assert np.array_equal(before, after)


def test_repetition_on_refuses_an_over_limit_plan_before_writing(tmp_path: Path) -> None:
    base = _c1_base(tmp_path)
    for slot, library_id in (("streets_plazas", "acg_pavingstones141"), ("roofs", "acg_roofingtiles012c")):
        plan = _c1_plan(tmp_path / f"{slot}.json", **{slot: library_id})
        apply_materials(base, tmp_path / f"{slot}_off", plan_path=plan, tile_cache=False)
        out = tmp_path / f"{slot}_on"
        with pytest.raises(PlanError) as caught:
            apply_materials(base, out, plan_path=plan, tile_cache=False, repetition=True)
        assert str(caught.value) == (f"{slot} cannot use {library_id}: its tile repeats visibly. "
                                     "Run `map create --name <map> --textures` to choose this map's textures again")
        assert not out.exists()


def test_repetition_refuses_a_bad_cell_before_writing(tmp_path: Path) -> None:
    base, plan = _c1_base(tmp_path), _c1_plan(tmp_path / "plan.json")
    with pytest.raises(ValueError, match="cell phase needs a finite cell size above 0 m"):
        apply_materials(base, tmp_path / "zero", plan_path=plan, repetition=True, cell_m=0.0)
    assert not (tmp_path / "zero").exists()


def test_cli_repetition_through_run_cli(tmp_path: Path) -> None:
    base, plan = _c1_base(tmp_path), _c1_plan(tmp_path / "plan.json")
    out = tmp_path / "cli"
    assert main(["materials", "apply", "--base", str(base), "--plan", str(plan), "--out", str(out),
                 "--no-tile-cache", "--repetition", "--cell-m", "50"]) == 0
    report = json.loads((out / "materials_report.json").read_text(encoding="utf-8"))
    assert report["repetition"]["cell_m"] == 50.0
