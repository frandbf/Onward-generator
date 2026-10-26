"""materials apply on a synthetic Murcia block. No live Poly Haven download."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

from PIL import Image

from onward_generator.cli import main
from onward_generator.dress.geometry import compare_fingerprints, geometry_fingerprint
from onward_generator.generation.gltf import read_glb
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
    assert (detail / "scene_visual.glb").read_bytes() != (out / "scene_visual.glb").read_bytes()
