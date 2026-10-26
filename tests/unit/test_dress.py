"""Dress 0.1: catalogue, surface groups, Ollama classifier validation. No real model."""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

import pytest

from onward_generator.ai.providers import OllamaProvider, ProviderError
from onward_generator.cli import main
from onward_generator.dress.atlas import build_atlas, encode_png_rgba
from onward_generator.dress.catalog import CatalogError, load_catalog
from onward_generator.dress.classify import (
    ClassifyError,
    classify_groups,
    parse_recommendations,
    recommendation_schema,
)
from onward_generator.dress.geometry import compare_fingerprints, file_sha256, geometry_fingerprint
from onward_generator.dress.surfaces import building_context, collect_surfaces, osm_material_override
from onward_generator.generation.config import load_generation_config
from tests.conftest import MURCIA_CENTER
from tests.unit.test_ai import fake_ollama  # noqa: F401

REPO = Path(__file__).resolve().parents[2]


def _rec(surface_id: str, material: str, confidence: float = 0.8, **extra: Any) -> dict[str, Any]:
    doc = {
        "surface_id": surface_id,
        "recommended_material_id": material,
        "confidence": confidence,
        "reason": extra.pop("reason", "test"),
        "alternative_material_ids": extra.pop("alternative_material_ids", []),
        "needs_review": extra.pop("needs_review", False),
    }
    doc.update(extra)
    return doc


def _payload(recs: list[dict[str, Any]]) -> str:
    return json.dumps({
        "schema": "onward_generator.material_recommendations",
        "schema_version": "0.1.0",
        "recommendations": recs,
    })


def write_sample(root: Path) -> tuple[Path, Path]:
    """Minimal phase-04-like manifest around MURCIA_CENTER. Geometry is metadata only."""
    _ = MURCIA_CENTER
    detail = root / "detail"
    detail.mkdir()
    objects = [
        {
            "id": "building/osm-way-1",
            "type": "building",
            "source_ids": ["osm-way-1"],
            "footprint_area_m2": 180.0,
            "height": {"height_m": 15.0},
            "facade": {
                "template": "generic_residential",
                "attribute_origin": "generated",
                "materials": {"wall": "plaster_ochre", "roof": "roof_flat_light"},
            },
            "roof": {"type": "flat", "material": "roof_flat_light", "area_m2": 180.0},
            "materials": ["plaster_ochre", "roof_flat_light", "door_wood"],
        },
        {
            "id": "building/osm-way-3",
            "type": "building",
            "source_ids": ["osm-way-3"],
            "footprint_area_m2": 900.0,
            "height": {"height_m": 40.0},
            "facade": {
                "template": "generic_public",
                "attribute_origin": "generated",
                "materials": {"wall": "stone_warm", "roof": "roof_flat_grey"},
            },
            "roof": {"type": "flat", "material": "roof_flat_grey", "area_m2": 900.0},
            "materials": ["stone_warm", "roof_flat_grey"],
        },
        {
            "id": "surface/carriageway",
            "type": "surface",
            "surface_class": "carriageway",
            "source_ids": ["osm-way-2"],
            "area_m2": 420.0,
            "materials": ["carriageway"],
            "generation": {"method": "planar_partition"},
        },
        {
            "id": "surface/pedestrian",
            "type": "surface",
            "surface_class": "pedestrian",
            "source_ids": ["osm-way-4"],
            "area_m2": 210.0,
            "materials": ["pedestrian"],
        },
    ]
    (detail / "scene_detail.json").write_text(json.dumps({"schema": "onward_generator.scene_detail", "objects": objects}), encoding="utf-8")
    (detail / "scene_visual.glb").write_bytes(b"glTF-fake")
    (detail / "scene_collision.glb").write_bytes(b"glTF-fake")
    scene = root / "geo_scene.json"
    scene.write_text(json.dumps({
        "features": [
            {"id": "osm-way-1", "tags": {"building": "yes", "historic": "yes", "building:material": "stone"}},
            {"id": "osm-way-2", "tags": {"highway": "residential", "surface": "asphalt"}},
            {"id": "osm-way-3", "tags": {"building": "yes"}},
            {"id": "osm-way-4", "tags": {"highway": "pedestrian", "surface": "sett"}},
        ]
    }), encoding="utf-8")
    return detail, scene


def test_catalog_uses_existing_colours_and_no_invented_files() -> None:
    catalog = load_catalog()
    assert "historic_plaster_cream" in catalog.ids
    assert "terracotta_roof" in catalog.ids
    assert catalog.get("wood_door").status == "pending"
    assert catalog.get("wrought_iron").textures["kind"] == "pending"
    assert catalog.get("wood_door").textures["paths"] == []
    colors = set(load_generation_config().materials) | set(json.loads((REPO / "config" / "detail.default.json").read_text())["materials"])
    for entry in catalog.entries:
        assert entry.color_id in colors
        assert not entry.textures["paths"]


def test_catalog_rejects_a_missing_texture_file(tmp_path: Path) -> None:
    src = json.loads((REPO / "config" / "dress" / "materials.catalog.json").read_text())
    src["materials"] = [src["materials"][0]]
    src["materials"][0]["textures"] = {"kind": "file", "paths": ["textures/does_not_exist.png"], "note": "invented"}
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(src), encoding="utf-8")
    with pytest.raises(CatalogError, match="does not exist"):
        load_catalog(path)


def test_context_comes_from_tags_not_footprint_size() -> None:
    assert building_context({"historic": "yes"}, None) == ("historic", "observed")
    assert building_context({"building": "cathedral"}, None) == ("religious", "observed")
    assert building_context({}, None) == ("unknown", "unknown")
    assert building_context({}, "residential_multi") == ("residential", "estimated")


def test_osm_surface_tags_win() -> None:
    assert osm_material_override("street", {"surface": "asphalt"}) == "asphalt_road"
    assert osm_material_override("plaza", {"surface": "sett"}) == "cobblestone_paving"
    assert osm_material_override("wall", {"building:material": "stone"}) == "limestone_warm"
    assert osm_material_override("wall", {"building:material": "brick"}) == "brick_red_wall"
    assert osm_material_override("roof", {"roof:material": "tile"}) == "terracotta_roof"
    assert osm_material_override("wall", {}) is None


def test_groups_and_osm_overrides(tmp_path: Path) -> None:
    detail, scene = write_sample(tmp_path)
    groups = collect_surfaces(detail, scene_path=scene)
    walls = [g for g in groups.groups if g.role == "wall"]
    historic = next(g for g in walls if g.building_context == "historic")
    unknown = next(g for g in walls if g.building_context == "unknown")
    assert historic.osm_override == "limestone_warm"
    assert unknown.osm_override is None
    street = next(g for g in groups.groups if g.role == "street")
    plaza = next(g for g in groups.groups if g.role == "plaza")
    assert street.osm_override == "asphalt_road"
    assert plaza.osm_override == "cobblestone_paving"
    ids = [g.group_id for g in groups.groups]
    assert len(ids) == len(set(ids))
    sample = collect_surfaces(detail, scene_path=scene, limit=4)
    assert {"wall", "street"} <= {g.role for g in sample.groups}


def test_rules_do_not_need_ollama(tmp_path: Path) -> None:
    detail, scene = write_sample(tmp_path)
    catalog = load_catalog()
    result = classify_groups(collect_surfaces(detail, scene_path=scene), catalog, None)
    assert result.valid
    historic = next(r for r in result.recommendations if "historic" in r.surface_id and r.surface_id.startswith("wall/"))
    assert historic.recommended_material_id == "limestone_warm"
    assert historic.source == "osm_tag"


def test_unknown_material_is_rejected(tmp_path: Path) -> None:
    detail, scene = write_sample(tmp_path)
    catalog = load_catalog()
    groups = collect_surfaces(detail, scene_path=scene, limit=1)
    gid = groups.groups[0].group_id
    raw = _payload([_rec(gid, "invented_marble_from_the_model")])
    result = classify_groups(groups, catalog, None, raw_override=raw)
    assert result.valid
    rec = result.recommendations[0]
    assert rec.recommended_material_id in catalog.ids
    assert rec.recommended_material_id != "invented_marble_from_the_model"
    assert rec.source == "safe_fallback" or rec.source == "osm_tag"


def test_invalid_json_and_extra_fields_are_rejected() -> None:
    with pytest.raises(ClassifyError):
        parse_recommendations("not json")
    with pytest.raises(ClassifyError):
        parse_recommendations(_payload([_rec("x", "asphalt_road") | {"texture_path": "secret.png"}]))
    with pytest.raises(ClassifyError):
        parse_recommendations(_payload([_rec("x", "asphalt_road", confidence=1.5)]))


def test_structured_schema_only_allows_catalog_ids() -> None:
    catalog = load_catalog()
    schema = recommendation_schema(sorted(catalog.ids))
    allowed = schema["$defs"]["recommendation"]["properties"]["recommended_material_id"]["enum"]
    assert "historic_plaster_cream" in allowed
    assert "secret_gold" not in allowed


def test_classify_cli_leaves_geometry_and_files_intact(tmp_path: Path) -> None:
    detail, scene = write_sample(tmp_path)
    before_detail = (detail / "scene_detail.json").read_bytes()
    before_visual = (detail / "scene_visual.glb").read_bytes()
    out = tmp_path / "plan"
    assert main(["dress", "classify", "--detail", str(detail), "--scene", str(scene), "--out", str(out),
                 "--provider", "none"]) == 0
    report = json.loads((out / "material_plan.json").read_text())
    assert report["applied"] is False
    assert report["geometry"]["identical"] is True
    assert report["geometry"]["input_files"]["unchanged"] is True
    assert (detail / "scene_detail.json").read_bytes() == before_detail
    assert (detail / "scene_visual.glb").read_bytes() == before_visual
    assert file_sha256(detail / "scene_visual.glb") == report["geometry"]["input_files"]["before"]["scene_visual.glb"]


def test_apply_is_refused(tmp_path: Path) -> None:
    detail, scene = write_sample(tmp_path)
    assert main(["dress", "classify", "--detail", str(detail), "--scene", str(scene),
                 "--out", str(tmp_path / "x"), "--apply"]) == 1
    assert not (tmp_path / "x").exists()


def test_disconnected_ollama_is_reported(tmp_path: Path) -> None:
    detail, scene = write_sample(tmp_path)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    code = main(["dress", "classify", "--detail", str(detail), "--scene", str(scene),
                 "--out", str(tmp_path / "x"), "--provider", "ollama",
                 "--host", f"http://127.0.0.1:{port}", "--timeout-s", "1"])
    assert code == 1
    report = json.loads((tmp_path / "x" / "material_plan.json").read_text())
    assert report["valid"] is False
    assert any(d["code"] == "provider_error" for d in report["diagnostics"])
    assert report["applied"] is False


def test_fake_ollama_response_is_validated(tmp_path: Path, fake_ollama) -> None:  # type: ignore[no-untyped-def]
    host, handler = fake_ollama
    detail, scene = write_sample(tmp_path)
    groups = collect_surfaces(detail, scene_path=scene, limit=1)
    gid = groups.groups[0].group_id
    handler.chat = {
        "message": {"role": "assistant", "content": _payload([_rec(gid, "historic_plaster_cream")])},
        "done_reason": "stop",
    }
    provider = OllamaProvider(host=host, timeout_s=5)
    result = classify_groups(groups, load_catalog(), provider)
    assert result.valid
    assert result.provenance["provider"] == "ollama"
    assert handler.requests[0]["body"]["format"]["$defs"]["recommendation"]["properties"]["recommended_material_id"]["enum"]


def test_atlas_png_is_generated_not_loaded_from_disk() -> None:
    catalog = load_catalog()
    materials = load_generation_config().materials
    atlas = build_atlas(catalog, materials, used_color_ids=["carriageway", "pedestrian"], seed=1)
    assert atlas.png.startswith(b"\x89PNG")
    assert atlas.slots["carriageway"] != atlas.slots["pedestrian"]
    again = encode_png_rgba(__import__("numpy").zeros((4, 4, 4), dtype=__import__("numpy").uint8))
    assert again.startswith(b"\x89PNG")


def test_atlas_keeps_every_used_material_and_stays_bright() -> None:
    from onward_generator.dress.atlas import MIN_MEAN_LUMA, decode_png_rgba, tile_luminance

    catalog = load_catalog()
    materials = load_generation_config().materials
    used = [
        "carriageway", "curb", "door_wood", "frame_stone", "glass_dark", "green", "ground",
        "interior_floor", "interior_wall", "pedestrian", "plaster_cream", "plaster_ochre",
        "railing_metal", "roof_flat_grey", "roof_flat_light", "roof_tile", "sidewalk",
        "stair", "stone_warm", "tree_canopy", "tree_trunk", "urban_metal", "water",
    ]
    atlas = build_atlas(catalog, materials, used_color_ids=used, seed=1)
    assert set(used) <= set(atlas.slots)
    assert atlas.tiles >= 8
    pixels = decode_png_rgba(atlas.png)
    assert pixels.shape[0] == atlas.size
    assert atlas.size // atlas.tiles >= 256
    for name in ("tree_canopy", "tree_trunk", "urban_metal", "sidewalk"):
        u0, v0, u1, v1 = atlas.slots[name]
        # PNG row 0 is the top; OpenGL v=0 is the bottom.
        x0, x1 = int(u0 * atlas.size), int(u1 * atlas.size)
        y0, y1 = int((1.0 - v1) * atlas.size), int((1.0 - v0) * atlas.size)
        luma = tile_luminance(pixels[y0:y1, x0:x1])
        assert luma >= MIN_MEAN_LUMA, (name, luma)
    assert atlas.slots["tree_canopy"] != atlas.slots["carriageway"]


def test_fingerprint_ignores_materials() -> None:
    a = [{"id": "building/1", "type": "building", "source_ids": ["a"], "footprint_area_m2": 10, "materials": ["x"]}]
    b = [{"id": "building/1", "type": "building", "source_ids": ["a"], "footprint_area_m2": 10, "materials": ["y"]}]
    assert compare_fingerprints(geometry_fingerprint(a), geometry_fingerprint(b))["identical"]
