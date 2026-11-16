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


def _ground_base(root: Path) -> Path:
    """The C1 scene with the residual ground of an OSM-poor map (Overture only): a ``ground`` surface."""
    base = _c1_base(root)
    nodes, materials = nodes_from_glb((base / "scene_visual.glb").read_bytes())
    ground = Node("surface/ground", (Primitive(_c1_quad(-80, -80, 80, -60), "ground"),),
                  {"type": "surface", "source_ids": [], "object_id": "surface/ground"})
    materials["ground"] = Material((0.55, 0.55, 0.52, 1.0), 0.95)
    for name in ("scene_visual.glb", "scene_collision.glb"):
        (base / name).write_bytes(build_glb([*nodes, ground], materials))
    return base


def test_an_earth_streets_texture_covers_the_residual_ground_and_a_paved_one_does_not(tmp_path: Path) -> None:
    """Dirt streets (14 November 2026): the ground between them takes the same dirt; any paved plan keeps the tile."""
    base = _ground_base(tmp_path)
    earth = apply_materials(base, tmp_path / "earth", tile_cache=False,
                            plan_path=_c1_plan(tmp_path / "earth.json", streets_plazas="ph_brown_mud_dry"))
    rows = [row for row in earth["applied"] if row["color_id"] == "ground"]
    assert [(r["id"], r["catalog_id"], r["asset_id"], r["albedo_mode"]) for r in rows] == [
        ("streets_plazas", "ground_neutral", "ph_brown_mud_dry", "color_albedo")]
    assert rows[0]["license"] == "CC0-1.0" and rows[0]["repo_path"] == "assets/textures/brown_mud_dry/brown_mud_dry_diff_1k.jpg"
    assert set(rows[0]) == {"id", "catalog_id", "color_id", "albedo_mode", "asset_id", "provider", "license", "authors", "page_url",
                            "md5", "sha256", "repo_path"}
    street = next(r for r in earth["applied"] if r["id"] == "streets_plazas" and r["color_id"] != "ground")
    assert all(rows[0][key] == street[key] for key in ("provider", "authors", "page_url", "md5", "sha256", "repo_path"))
    assert rows[0]["provider"] == "polyhaven" and rows[0]["authors"] and len(rows[0]["md5"]) == 32 and len(rows[0]["sha256"]) == 64
    doc = read_glb((tmp_path / "earth" / "scene_visual.glb").read_bytes())[0]
    ground = next(m for m in doc["materials"] if m["name"] == "ground")
    assert ground["pbrMetallicRoughness"]["baseColorFactor"][:3] == [1.0, 1.0, 1.0] and ground["pbrMetallicRoughness"]["roughnessFactor"] == 0.95
    paved = apply_materials(base, tmp_path / "paved", tile_cache=False, plan_path=_c1_plan(tmp_path / "paved.json"))
    assert not [row for row in paved["applied"] if row["color_id"] == "ground"]
    doc = read_glb((tmp_path / "paved" / "scene_visual.glb").read_bytes())[0]
    assert next(m for m in doc["materials"] if m["name"] == "ground")["pbrMetallicRoughness"]["baseColorFactor"][:3] != [1.0, 1.0, 1.0]
    from onward_generator.materials.apply import earth_texture

    assert earth_texture("ph_brown_mud_dry") and not earth_texture("ph_worn_asphalt") and not earth_texture("")
    assert not earth_texture("no_such_texture")


def _dirt_base(root: Path) -> Path:
    """The C1 scene whose carriageway generate recorded as dirt west of x = 10 m: one of its two triangles."""
    root.mkdir()
    base = _c1_base(root)
    manifest = json.loads((base / "scene_detail.json").read_text(encoding="utf-8"))
    road = next(o for o in manifest["objects"] if o["id"] == "surface/carriageway")
    road["unpaved"] = {"area_m2": 500.0, "attribute_origin": "observed",
                       "polygons": [[[[-40.0, -5.0], [10.0, -5.0], [10.0, 5.0], [-40.0, 5.0], [-40.0, -5.0]]]]}
    (base / "scene_detail.json").write_text(json.dumps(manifest), encoding="utf-8")
    return base


def test_an_unpaved_carriageway_takes_the_earth_slot_and_a_paved_map_skips_it(tmp_path: Path) -> None:
    """15 November 2026: the carriageway triangles generate capped as dirt draw the unpaved_carriageway slot's photograph."""
    from onward_generator.generation.config import load_generation_config
    from onward_generator.materials.apply import UNPAVED_MATERIAL

    lon, lat = MURCIA_CENTER  # the wide library holds the earth photographs; a narrow plan has no earth slot
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(select_texture_plan(name="Murcia", map_id="murcia", latitude=lat, longitude=lon, brief=None, seed=1,
                                                   extended=True, wide=True)), encoding="utf-8")
    assert "unpaved_carriageway" in {e["id"] for e in json.loads(plan.read_text(encoding="utf-8"))["entries"]}
    dirt = apply_materials(_dirt_base(tmp_path / "d"), tmp_path / "dirt", plan_path=plan, tile_cache=False, repetition=True,
                           wide_library=True)
    rows = [r for r in dirt["applied"] if r["id"] == "unpaved_carriageway"]
    assert [r["color_id"] for r in rows] == [UNPAVED_MATERIAL]
    assert not [r for r in dirt["skipped"] if r["id"] == "unpaved_carriageway"]
    assert UNPAVED_MATERIAL in dirt["repetition"]["cell_phase_materials"]  # the earth is phased per cell like the asphalt
    nodes, _ = nodes_from_glb((tmp_path / "dirt" / "scene_visual.glb").read_bytes())
    road = next(n for n in nodes if n.name == "surface/carriageway")
    assert sorted(p.material for p in road.primitives) == sorted(["carriageway", UNPAVED_MATERIAL])
    assert all(p.mesh.triangle_count == 1 for p in road.primitives)
    doc = read_glb((tmp_path / "dirt" / "scene_visual.glb").read_bytes())[0]
    earth = next(m for m in doc["materials"] if m["name"] == UNPAVED_MATERIAL)
    expected = load_generation_config().materials[UNPAVED_MATERIAL]
    assert earth["pbrMetallicRoughness"]["roughnessFactor"] == pytest.approx(expected.roughness)
    assert "baseColorTexture" in earth["pbrMetallicRoughness"]

    (tmp_path / "p").mkdir()
    first = json.loads(plan.read_text(encoding="utf-8"))  # the earth slot first: skipping it skips nothing after it
    first["entries"].sort(key=lambda e: e["id"] != "unpaved_carriageway")
    reordered = tmp_path / "first_plan.json"
    reordered.write_text(json.dumps(first), encoding="utf-8")
    paved = apply_materials(_c1_base(tmp_path / "p"), tmp_path / "paved", plan_path=reordered, tile_cache=False, repetition=True,
                            wide_library=True)
    assert [r for r in paved["skipped"] if r["id"] == "unpaved_carriageway"] == [
        {"id": "unpaved_carriageway", "catalog_ids": ["dirt_road"], "reason": "no_unpaved_carriageway"}]
    assert {"sidewalks", "doors"} <= {r["id"] for r in paved["applied"]}  # the slots after it are still applied
    assert UNPAVED_MATERIAL not in paved["repetition"]["cell_phase_materials"]
    doc = read_glb((tmp_path / "paved" / "scene_visual.glb").read_bytes())[0]
    assert UNPAVED_MATERIAL not in {m["name"] for m in doc["materials"]}

    # A plan written before the slot existed leaves a dirt record's carriageway as it was.
    document = json.loads(plan.read_text(encoding="utf-8"))
    document["entries"] = [e for e in document["entries"] if e["id"] != "unpaved_carriageway"]
    old = tmp_path / "old_plan.json"
    old.write_text(json.dumps(document), encoding="utf-8")
    before = apply_materials(_dirt_base(tmp_path / "o"), tmp_path / "old", plan_path=old, tile_cache=False, wide_library=True)
    nodes, _ = nodes_from_glb((tmp_path / "old" / "scene_visual.glb").read_bytes())
    assert [p.material for p in next(n for n in nodes if n.name == "surface/carriageway").primitives] == ["carriageway"]
    assert not [r for r in before["skipped"] if r["id"] == "unpaved_carriageway"]
