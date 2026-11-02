"""The shop, furniture and food catalogues: real sizes, CC0, and never on a street."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest

from onward_generator.generation.gltf import images_by_material, pack_glb, read_glb, scene_primitives
from onward_generator.overgrow.library import read_library
from onward_generator.overgrow.library_catalog import installed_rows
from onward_generator.overgrow.manifest import check_manifest
from onward_generator.overgrow.normalize import _apply_scale

REPO = Path(__file__).resolve().parents[2]
CATALOGUES = ("kenney_mini_shop", "kenney_furniture", "kenney_food", "polyhaven_shop")
COUNTS = {"kenney_mini_shop": 30, "kenney_furniture": 140, "kenney_food": 200, "polyhaven_shop": 7}


def _tool():
    spec = importlib.util.spec_from_file_location("import_shop_assets", REPO / "tools" / "import_shop_assets.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _rows(catalogue: str) -> list[dict]:
    return json.loads((REPO / "assets" / catalogue / "manifest.json").read_text(encoding="utf-8"))["assets"]


def _by_id(catalogue: str) -> dict[str, dict]:
    return {row["id"]: row for row in _rows(catalogue)}


def _triangle_glb() -> bytes:
    positions = np.array([[0, 0, 0], [1, 0, 0], [0, 2, 0]], dtype=np.float32).tobytes()
    indices = np.array([0, 1, 2, 0], dtype=np.uint16).tobytes()
    doc = {
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": len(positions) + len(indices)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(positions)},
            {"buffer": 0, "byteOffset": len(positions), "byteLength": 6},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3", "min": [0, 0, 0], "max": [1, 2, 0]},
            {"bufferView": 1, "componentType": 5123, "count": 3, "type": "SCALAR"},
        ],
        "materials": [{"name": "red", "pbrMetallicRoughness": {"baseColorFactor": [1.0, 0.0, 0.0, 1.0]}}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1, "material": 0}]}],
        "nodes": [{"mesh": 0}],
        "scenes": [{"nodes": [0]}],
        "scene": 0,
    }
    return pack_glb(doc, positions + indices)


@pytest.mark.parametrize("catalogue", CATALOGUES)
def test_a_catalogue_passes_its_schema_and_every_file_is_there(catalogue: str) -> None:
    folder = REPO / "assets" / catalogue
    check_manifest(json.loads((folder / "manifest.json").read_text(encoding="utf-8")), catalogue)
    rows = _rows(catalogue)
    assert len(rows) == COUNTS[catalogue]
    assert all((folder / row["file"]).is_file() for row in rows)


def test_no_shop_prop_can_reach_a_street() -> None:
    props, rejects = read_library(REPO / "assets")
    ids = {row["id"] for catalogue in CATALOGUES for row in _rows(catalogue)}
    assert not ids & {prop.spec.id for prop in props}
    assert {reject.asset_id: reject.reason for reject in rejects if reject.asset_id in ids} == dict.fromkeys(ids, "interior")
    assert ids <= {row["id"] for row in installed_rows(REPO / "assets")}


@pytest.mark.parametrize(
    ("catalogue", "asset", "axis", "low", "high"),
    [
        ("kenney_mini_shop", "kenney_shop_mannequin", "height_m", 1.6, 2.0),
        ("kenney_mini_shop", "kenney_shop_shopping_cart", "height_m", 0.8, 1.2),
        ("kenney_furniture", "kenney_furniture_doorway", "height_m", 1.9, 2.2),
        ("kenney_furniture", "kenney_furniture_bedSingle", "depth_m", 1.8, 2.4),
        ("kenney_food", "kenney_food_banana", "depth_m", 0.12, 0.25),
        ("polyhaven_shop", "steel_frame_shelves_01", "height_m", 2.0, 2.3),
        ("polyhaven_shop", "steel_frame_shelves_02", "height_m", 2.0, 2.3),
    ],
)
def test_sizes_are_real_ones(catalogue: str, asset: str, axis: str, low: float, high: float) -> None:
    assert low <= _by_id(catalogue)[asset][axis] <= high


@pytest.mark.parametrize("catalogue", ("kenney_mini_shop", "kenney_furniture", "kenney_food"))
def test_a_kenney_licence_is_cc0(catalogue: str) -> None:
    assert "CC0" in (REPO / "assets" / catalogue / "License.txt").read_text(encoding="utf-8", errors="replace")


def test_furniture_keeps_its_colours_as_albedo_images() -> None:
    data = (REPO / "assets" / "kenney_furniture" / "bedSingle.glb").read_bytes()
    doc, _ = read_glb(data)
    assert set(images_by_material(data)) == {material["name"] for material in doc["materials"]}


def test_the_scale_goes_on_one_root_and_children_keep_their_transform() -> None:
    tool = _tool()
    data = tool.normalized_kenney(_triangle_glb(), 2.5)
    doc, _ = read_glb(data)
    assert doc["extras"]["street_scale"] == 2.5
    assert doc["scenes"][0]["nodes"] == [1]
    assert doc["nodes"][1]["children"] == [0]
    assert np.ptp(scene_primitives(data)[0].mesh.vertices, axis=0).tolist() == pytest.approx([2.5, 0.0, 5.0])


def test_a_factor_becomes_an_srgb_image() -> None:
    from io import BytesIO

    from PIL import Image

    tool = _tool()
    doc, blob = read_glb(_triangle_glb())
    doc["materials"][0]["pbrMetallicRoughness"]["baseColorFactor"] = [0.5, 0.0, 1.0, 1.0]
    baked = tool.bake_flat_albedos(doc, blob)
    doc["buffers"][0]["byteLength"] = len(baked)
    image = Image.open(BytesIO(images_by_material(pack_glb(doc, baked))["red"]))
    assert image.convert("RGB").getpixel((0, 0)) == (188, 0, 255)
    assert image.size == (4, 4)


def test_a_file_off_by_one_factor_takes_the_size_the_api_declares() -> None:
    vertices = np.array([[0.0, 0.0, 0.0], [10.97, 21.4, 5.02]])
    note = _apply_scale(vertices, [1097.4, 502.1, 2140.5])
    assert np.ptp(vertices, axis=0).tolist() == pytest.approx([1.097, 2.14, 0.502], abs=0.002)
    assert "10 times" in note


def test_a_file_with_another_shape_is_not_rescaled() -> None:
    vertices = np.array([[0.0, 0.0, 0.0], [5.0, 40.0, 1.0]])
    assert _apply_scale(vertices, [1000.0, 1000.0, 1000.0]) == "glTF bbox kept in metres"
    assert vertices.max() == 40.0


def test_a_flat_file_is_not_divided_by_zero() -> None:
    vertices = np.array([[0.0, 0.0, 0.0], [20.0, 20.0, 0.0]])
    assert _apply_scale(vertices, [1000.0, 1000.0, 0.0]) == "glTF bbox kept in metres"
