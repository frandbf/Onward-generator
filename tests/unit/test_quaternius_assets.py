"""The Quaternius catalogues: CC0, opaque albedo-only GLBs, street sizes, no kit building, and the importer's conversions."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from onward_generator.detail.config import FURNISH_CATALOGUES, load_detail_config
from onward_generator.generation.gltf import read_glb, scene_primitives
from onward_generator.overgrow.library import interior_licence, read_interior_catalogue, read_library
from onward_generator.overgrow.manifest import check_manifest

REPO = Path(__file__).resolve().parents[2]
ASSETS = REPO / "assets"
INTERIOR = ("quaternius_home_interior", "quaternius_ultimate_furniture", "quaternius_furniture")
OUTDOOR = ("quaternius_city_props", "quaternius_nature")
COUNTS = {"quaternius_home_interior": 88, "quaternius_ultimate_furniture": 17, "quaternius_furniture": 23,
          "quaternius_city_props": 5, "quaternius_nature": 35}


def _tool():
    spec = importlib.util.spec_from_file_location("import_quaternius", REPO / "tools" / "import_quaternius.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _manifest(catalogue: str) -> dict:
    return json.loads((ASSETS / catalogue / "manifest.json").read_text(encoding="utf-8"))


def _by_id() -> dict[str, dict]:
    return {row["id"]: row for catalogue in COUNTS for row in _manifest(catalogue)["assets"]}


@pytest.mark.parametrize("catalogue", sorted(COUNTS))
def test_every_catalogue_is_cc0_checked_and_complete(catalogue: str) -> None:
    document = _manifest(catalogue)
    check_manifest(document, catalogue)
    assert interior_licence(document["source"]) == "CC0-1.0"
    assert "CC0" in (ASSETS / catalogue / "License.txt").read_text(encoding="utf-8", errors="replace")
    rows = document["assets"]
    assert len(rows) == COUNTS[catalogue] == len({row["id"] for row in rows})
    assert all((ASSETS / catalogue / row["file"]).is_file() for row in rows)


def test_interior_rows_are_interior_and_outdoor_rows_never_are() -> None:
    for catalogue in INTERIOR:
        assert {row["placement"] for row in _manifest(catalogue)["assets"]} == {"interior"}
    for catalogue in OUTDOOR:
        assert "interior" not in {row["placement"] for row in _manifest(catalogue)["assets"]}
        assert "kit_only" not in {row["placement"] for row in _manifest(catalogue)["assets"]}


def test_no_kit_building_door_window_or_cutout_tree_is_copied() -> None:
    ids = set(_by_id())
    for banned in ("Building", "Brick_", "Roof_", "Street_", "Sidewalk_", "Door", "Window", "Column", "Curtains",
                   "Light_Ceiling", "CommonTree", "Pine_", "TwistedTree", "Flower", "Bush"):
        assert not any(banned in asset_id for asset_id in ids), banned
    assert {asset_id.removeprefix("qt_city_") for asset_id in ids if asset_id.startswith("qt_city_")} == {
        "Prop_ManholeCover", "Prop_Drain", "Prop_Planter_Single", "Prop_Bollard", "Prop_ACUnit"}


@pytest.mark.parametrize(("asset_id", "low", "high", "axis"), [
    ("qt_home_Chair_1", 0.75, 1.0, "height_m"),
    ("qt_home_Kitchen_Fridge", 1.4, 2.0, "height_m"),
    ("qt_home_Bed_King", 1.8, 2.2, "depth_m"),
    ("qt_home_Kitchen_Cabinet1", 0.7, 0.95, "height_m"),
    ("qt_furn_Chair", 0.8, 1.1, "height_m"),
    ("qt_city_Prop_Bollard", 0.7, 1.1, "height_m"),
    ("qt_nature_Grass_Common_Tall", 0.5, 1.2, "height_m"),
    ("qt_nature_Rock_Medium_1", 1.0, 2.0, "length_m"),
])
def test_street_scale_gives_real_sizes(asset_id: str, low: float, high: float, axis: str) -> None:
    assert low <= _by_id()[asset_id][axis] <= high


def test_every_glb_is_opaque_albedo_only() -> None:
    for catalogue in COUNTS:
        for row in _manifest(catalogue)["assets"]:
            doc, _ = read_glb((ASSETS / catalogue / row["file"]).read_bytes())
            for material in doc["materials"]:
                assert material.get("alphaMode", "OPAQUE") == "OPAQUE", row["id"]
                assert not {"normalTexture", "occlusionTexture", "emissiveTexture"} & set(material), row["id"]
                assert "metallicRoughnessTexture" not in material["pbrMetallicRoughness"], row["id"]
                assert "baseColorTexture" in material["pbrMetallicRoughness"], row["id"]


def test_the_libraries_load_every_row_without_a_refusal() -> None:
    props, rejects = read_library(ASSETS)
    loaded = {prop.spec.id for prop in props}
    assert {row["id"] for c in OUTDOOR for row in _manifest(c)["assets"]} <= loaded
    assert not [r for r in rejects if r.asset_id.startswith("qt_") and r.reason != "interior"]
    interior, refused = read_interior_catalogue(ASSETS, INTERIOR)
    assert len(interior) == sum(COUNTS[c] for c in INTERIOR) and refused == []
    assert all(prop.licence == "CC0-1.0" for prop in interior.values())


def test_the_default_furnishing_names_quaternius_rows_within_its_asset_caps() -> None:
    cfg = load_detail_config()
    assert set(INTERIOR) <= set(FURNISH_CATALOGUES)
    for furnish in (cfg.interiors.furnish, cfg.interiors.shops.furnish):
        assert furnish.enabled and "quaternius_home_interior" in furnish.catalogues
        ids = {e.id for chosen in furnish.sets.values() for e in (*chosen.counter, *chosen.wall)}
        assert any(i.startswith("qt_home_") for i in ids)
    floors = cfg.interiors.furnish
    assert len({e.id for e in floors.sets["generic"].wall}) <= floors.max_assets


# ---- the importer's conversions ----------------------------------------------------------------------------------

OBJ = """o quad
v 0 0 0
v 2 0 0
v 2 0 2
v 0 0 2
vn 0 1 0
usemtl Red
f 1//1 2//1 3//1 4//1
usemtl Bare
f 1 3 2
"""
MTL = "newmtl Red\nKd 1.0 0.0 0.0\nd 0.5\nnewmtl Unused\nKd 0 0 1\n"


def test_read_obj_fans_a_quad_and_computes_a_missing_normal() -> None:
    groups = _tool().read_obj(OBJ)
    assert sorted(groups) == ["Bare", "Red"] and len(groups["Red"]) == 2 and len(groups["Bare"]) == 1
    positions, normals = groups["Bare"][0]
    assert positions.tolist() == [[0, 0, 0], [2, 0, 2], [2, 0, 0]]
    assert np.allclose(normals, [[0, 1, 0]] * 3)


def test_read_mtl_keeps_linear_kd_and_a_grey_default() -> None:
    colours = _tool().read_mtl(MTL + "newmtl NoKd\n")
    assert colours == {"Red": (1.0, 0.0, 0.0), "Unused": (0.0, 0.0, 1.0), "NoKd": (0.8, 0.8, 0.8)}


def test_obj_to_glb_scales_and_bakes_each_colour_as_an_opaque_srgb_image() -> None:
    tool = _tool()
    data = tool.obj_to_glb(OBJ, MTL, "quad", factor=0.5)
    doc, blob = read_glb(data)
    assert [m["name"] for m in doc["materials"]] == ["Red", "Bare"]
    assert all(m["alphaMode"] == "OPAQUE" for m in doc["materials"])  # the MTL dissolve 0.5 is not carried over
    view = doc["bufferViews"][doc["images"][1]["bufferView"]]
    bare = Image.open(io.BytesIO(blob[view["byteOffset"]:view["byteOffset"] + view["byteLength"]]))
    assert bare.size == (4, 4) and bare.getpixel((0, 0)) == (231, 231, 231)  # Kd 0.8 linear is sRGB 231
    vertices = np.concatenate([p.mesh.vertices for p in scene_primitives(data)])
    assert np.allclose(vertices.max(axis=0) - vertices.min(axis=0), [1.0, 1.0, 0.0])


def test_srgb_is_the_piecewise_transfer() -> None:
    srgb = _tool()._srgb
    assert srgb(0.0) == 0.0 and srgb(1.0) == pytest.approx(1.0) and srgb(2.0) == pytest.approx(1.0)
    assert srgb(0.002) == pytest.approx(0.002 * 12.92) and srgb(0.5) == pytest.approx(0.7353569, abs=1e-6)


def _gltf(tmp_path: Path, size: int) -> Path:
    positions = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float32).tobytes()
    (tmp_path / "m.bin").write_bytes(positions)
    Image.new("RGB", (size, size // 2), (10, 20, 30)).save(tmp_path / "albedo.png")
    Image.new("RGB", (8, 8), (128, 128, 255)).save(tmp_path / "normal.png")
    doc = {
        "asset": {"version": "2.0"}, "buffers": [{"uri": "m.bin", "byteLength": len(positions)}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(positions)}],
        "accessors": [{"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3", "min": [0, 0, 0], "max": [1, 1, 0]}],
        "images": [{"uri": "normal.png"}, {"uri": "albedo.png"}], "textures": [{"source": 0}, {"source": 1}],
        "materials": [{"name": "leaf", "alphaMode": "MASK", "alphaCutoff": 0.5, "normalTexture": {"index": 0},
                       "pbrMetallicRoughness": {"baseColorTexture": {"index": 1}, "metallicRoughnessTexture": {"index": 0}}}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "material": 0}]}],
        "nodes": [{"mesh": 0}], "scenes": [{"nodes": [0]}], "scene": 0,
    }
    path = tmp_path / "m.gltf"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def test_gltf_to_glb_keeps_only_the_base_colour_capped_and_opaque(tmp_path: Path) -> None:
    tool = _tool()
    doc, blob = read_glb(tool.gltf_to_glb(_gltf(tmp_path, 2048), []))
    material = doc["materials"][0]
    assert material["alphaMode"] == "OPAQUE" and "alphaCutoff" not in material and "normalTexture" not in material
    assert material["pbrMetallicRoughness"] == {"baseColorTexture": {"index": 0}, "metallicFactor": 0.0, "roughnessFactor": 1.0}
    assert len(doc["images"]) == len(doc["textures"]) == 1 and "uri" not in doc["images"][0]
    view = doc["bufferViews"][doc["images"][0]["bufferView"]]
    image = Image.open(io.BytesIO(blob[view["byteOffset"]:view["byteOffset"] + view["byteLength"]]))
    assert image.size == (tool.MAX_TEXTURE_PX, tool.MAX_TEXTURE_PX // 2) and image.getpixel((0, 0)) == (10, 20, 30)


def test_gltf_to_glb_hangs_the_scene_from_a_scaled_root(tmp_path: Path) -> None:
    data = _tool().gltf_to_glb(_gltf(tmp_path, 64), [], factor=0.5)
    doc, _ = read_glb(data)
    root = doc["nodes"][doc["scenes"][0]["nodes"][0]]
    assert root["name"] == "street_scale" and root["scale"] == [0.5, 0.5, 0.5] and root["children"] == [0]
    assert doc["nodes"][0] == {"mesh": 0}  # the authored node is kept as is, under the scaled root
    assert "street_scale" not in json.dumps(read_glb(_tool().gltf_to_glb(_gltf(tmp_path, 64), []))[0]["nodes"])


def test_a_missing_texture_and_a_non_cc0_licence_stop_the_import(tmp_path: Path) -> None:
    tool = _tool()
    path = _gltf(tmp_path, 64)
    (tmp_path / "albedo.png").unlink()
    with pytest.raises(SystemExit, match="missing texture albedo.png"):
        tool.gltf_to_glb(path, [])
    with pytest.raises(SystemExit, match="does not say CC0"):
        tool._check_cc0("All rights reserved.", "pack")
    tool._check_cc0("https://creativecommons.org/publicdomain/zero/1.0/", "pack")


def test_nature_semantics_keep_only_known_opaque_kinds() -> None:
    semantics = _tool()._nature_semantics
    assert semantics("DeadTree_3")[:4] == ("vegetation", "tree", "open_ground", "ground")
    assert semantics("Pebble_Round_2")[2:4] == ("ground_cover", "flat")
    assert semantics("RockPath_Square_Wide")[1] == "path"
    assert semantics("Rock_Medium_1")[1] == "rock"
    assert semantics("CommonTree_1") is None and semantics("Flower_3_Group") is None
