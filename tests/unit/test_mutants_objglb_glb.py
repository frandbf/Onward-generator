"""The GLB the Asset Store converter writes, read back field by field: document, buffer layout, accessors and images."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from onward_generator.assetstore.objglb import ObjError, obj_to_glb
from onward_generator.generation.gltf import read_glb

U = "0.31183144450187683"  # a float32 whose scaled, offset and flipped UV rounds differently in float64
OBJ = f"""v 0 0 0
v 2 0 0
v 0 2 0
v 2 2 1
vt {U} {U}
vt 1 0.5
vt 0 1
vn 0 1 0
usemtl Brick wall-1
f 1/1/1 2/2/1 3/3/1
usemtl Flat
f 2 4 3
usemtl Unknown
f 1 2 4
"""
MTL = "newmtl Brick wall-1\nKd 1 1 1\nmap_Kd -s 3 3 1 -o 0.7 0.7 0 tex.png\nnewmtl Flat\nKd 0.8 0.2 0.3\n"
TEXTURE = [[(10, 20, 30), (200, 100, 50)], [(200, 100, 50), (200, 100, 50)]]


def _texture(directory: Path) -> None:
    picture = Image.new("RGB", (2, 2), (200, 100, 50))
    picture.putpixel((0, 0), (10, 20, 30))
    picture.save(directory / "tex.png")


def _read(data: bytes) -> tuple[dict[str, Any], bytes]:
    doc, blob = read_glb(data)
    return doc, blob


def _view(doc: dict[str, Any], blob: bytes, index: int) -> bytes:
    view = doc["bufferViews"][index]
    return blob[view["byteOffset"] : view["byteOffset"] + view["byteLength"]]


def _accessor(doc: dict[str, Any], blob: bytes, index: int) -> np.ndarray:
    accessor = doc["accessors"][index]
    dtype = {5125: np.uint32, 5126: np.float32}[accessor["componentType"]]
    width = {"SCALAR": 1, "VEC2": 2, "VEC3": 3}[accessor["type"]]
    data = np.frombuffer(_view(doc, blob, accessor["bufferView"]), dtype=dtype)
    return data.reshape(accessor["count"], width) if width > 1 else data


def _image(doc: dict[str, Any], blob: bytes, index: int) -> Image.Image:
    return Image.open(io.BytesIO(_view(doc, blob, doc["images"][index]["bufferView"])))


def test_the_document_is_exactly_one_mesh_with_one_textured_primitive_per_material(tmp_path: Path) -> None:
    _texture(tmp_path)
    doc, blob = _read(obj_to_glb(OBJ, MTL, "crate", tmp_path))
    assert doc["asset"] == {"version": "2.0", "generator": "onward_generator.assetstore"}
    assert doc["scene"] == 0 and doc["scenes"] == [{"nodes": [0]}]
    assert doc["nodes"] == [{"name": "crate", "mesh": 0}]
    assert doc["buffers"] == [{"byteLength": len(blob)}]
    assert doc["images"] == [{"mimeType": "image/png", "bufferView": 5 * k} for k in range(3)]
    assert doc["textures"] == [{"source": k} for k in range(3)]
    assert doc["materials"] == [
        {
            "name": name,
            "alphaMode": "OPAQUE",
            "doubleSided": False,
            "pbrMetallicRoughness": {"baseColorTexture": {"index": k}, "metallicFactor": 0.0, "roughnessFactor": 1.0},
        }
        for k, name in enumerate(["Brick_wall_1", "Flat", "Unknown"])
    ]
    assert doc["meshes"] == [
        {
            "name": "crate",
            "primitives": [
                {
                    "attributes": {"POSITION": 4 * k, "NORMAL": 4 * k + 1, "TEXCOORD_0": 4 * k + 2},
                    "indices": 4 * k + 3,
                    "material": k,
                    "mode": 4,
                }
                for k in range(3)
            ],
        }
    ]


def test_accessors_carry_bounds_on_positions_only_and_index_as_unsigned_int(tmp_path: Path) -> None:
    _texture(tmp_path)
    doc, _ = _read(obj_to_glb(OBJ, MTL, "crate", tmp_path))
    bounds = [([0.0, 0.0, 0.0], [2.0, 2.0, 0.0]), ([0.0, 0.0, 0.0], [2.0, 2.0, 1.0]), ([0.0, 0.0, 0.0], [2.0, 2.0, 1.0])]
    expected = []
    for k, (low, high) in enumerate(bounds):
        first = 5 * k + 1
        expected += [
            {"bufferView": first, "componentType": 5126, "count": 3, "type": "VEC3", "min": low, "max": high},
            {"bufferView": first + 1, "componentType": 5126, "count": 3, "type": "VEC3"},
            {"bufferView": first + 2, "componentType": 5126, "count": 3, "type": "VEC2"},
            {"bufferView": first + 3, "componentType": 5125, "count": 3, "type": "SCALAR"},
        ]
    assert doc["accessors"] == expected


def test_buffer_views_are_packed_in_order_and_aligned_to_four_bytes(tmp_path: Path) -> None:
    _texture(tmp_path)
    doc, blob = _read(obj_to_glb(OBJ, MTL, "crate", tmp_path))
    views = doc["bufferViews"]
    assert len(views[0]) == 3 and views[0]["buffer"] == 0
    assert views[0]["byteLength"] % 4 == 3  # the texture PNG leaves padding to write before the next view
    offset = 0
    for view in views:
        offset += -offset % 4
        assert view == {"buffer": 0, "byteOffset": offset, "byteLength": view["byteLength"]}
        offset += view["byteLength"]
    assert [view["byteLength"] for view in views if view not in (views[0], views[5], views[10])] == [36, 36, 24, 12] * 3
    assert blob[views[0]["byteLength"] : views[1]["byteOffset"]] == b"\x00"
    assert doc["buffers"][0]["byteLength"] == offset


def test_vertex_data_is_scaled_flipped_and_offset_as_the_mtl_says(tmp_path: Path) -> None:
    _texture(tmp_path)
    doc, blob = _read(obj_to_glb(OBJ, MTL, "crate", tmp_path))
    assert _accessor(doc, blob, 0).tolist() == [[0, 0, 0], [2, 0, 0], [0, 2, 0]]
    assert _accessor(doc, blob, 1).tolist() == [[0, 1, 0]] * 3
    expected_uv = np.array([[1.6354942321777344, -0.6354942321777344], [3.7, -1.2], [0.7, -2.7]], dtype=np.float32)
    assert np.array_equal(_accessor(doc, blob, 2)[0], expected_uv[0])
    assert np.allclose(_accessor(doc, blob, 2), expected_uv, atol=1e-6)
    assert _accessor(doc, blob, 3).tolist() == [0, 1, 2]
    for k in (1, 2):
        assert _accessor(doc, blob, 4 * k + 2).tolist() == [[0.5, 0.5]] * 3
        assert _accessor(doc, blob, 4 * k + 3).tolist() == [0, 1, 2]
    assert _accessor(doc, blob, 4).tolist() == [[2, 0, 0], [2, 2, 1], [0, 2, 0]]


def test_the_default_factor_keeps_positions_and_a_factor_scales_them() -> None:
    quad = "v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n"
    doc, blob = _read(obj_to_glb(quad, "", "q"))
    assert _accessor(doc, blob, 0).tolist() == [[0, 0, 0], [1, 0, 0], [0, 1, 0]]
    doc, blob = _read(obj_to_glb(quad, "", "q", factor=3.0))
    assert _accessor(doc, blob, 0).tolist() == [[0, 0, 0], [3, 0, 0], [0, 3, 0]]
    assert doc["accessors"][0]["max"] == [3.0, 3.0, 0.0]


def test_images_hold_the_texture_and_flat_srgb_colours(tmp_path: Path) -> None:
    _texture(tmp_path)
    doc, blob = _read(obj_to_glb(OBJ, MTL, "crate", tmp_path))
    texture = _image(doc, blob, 0)
    assert texture.mode == "RGB" and np.asarray(texture).tolist() == [[list(p) for p in row] for row in TEXTURE]
    for index, colour in ((1, [231, 124, 149]), (2, [231, 231, 231])):
        flat = _image(doc, blob, index)
        assert flat.size == (4, 4) and flat.mode == "RGB"
        assert np.asarray(flat).reshape(-1, 3).tolist() == [colour] * 16


def test_a_large_or_alpha_texture_becomes_a_lanczos_rgb_png_of_at_most_512_px(tmp_path: Path) -> None:
    ramp = np.tile(np.arange(1024, dtype=np.uint16) % 256, (6, 1)).astype(np.uint8)
    source = Image.fromarray(np.stack([ramp, ramp[::-1], 255 - ramp, np.full_like(ramp, 128)], axis=-1)).convert("RGBA")
    source.save(tmp_path / "big.png")
    obj = "v 0 0 0\nv 1 0 0\nv 0 1 0\nvt 0 0\nusemtl t\nf 1/1 2/1 3/1\n"
    doc, blob = _read(obj_to_glb(obj, "newmtl t\nmap_Kd big.png\n", "big", tmp_path))
    embedded = _view(doc, blob, doc["images"][0]["bufferView"])
    reference = source.convert("RGB")
    reference.thumbnail((512, 512), Image.Resampling.LANCZOS)
    expected = io.BytesIO()
    reference.save(expected, format="PNG", optimize=True)
    picture = Image.open(io.BytesIO(embedded))
    assert picture.mode == "RGB" and picture.size == (512, 3)
    assert np.array_equal(np.asarray(picture), np.asarray(reference))
    assert embedded == expected.getvalue()


def test_a_material_missing_from_the_mtl_is_flat_default_grey() -> None:
    doc, blob = _read(obj_to_glb("v 0 0 0\nv 1 0 0\nv 0 1 0\nusemtl ghost\nf 1 2 3\n", "newmtl other\nKd 1 0 0\n", "g"))
    assert doc["materials"][0]["name"] == "ghost"
    assert np.asarray(_image(doc, blob, 0)).reshape(-1, 3).tolist() == [[231, 231, 231]] * 16


def test_a_texture_outside_the_directory_or_without_one_is_missing(tmp_path: Path) -> None:
    obj = "v 0 0 0\nv 1 0 0\nv 0 1 0\nusemtl t\nf 1 2 3\n"
    with pytest.raises(ObjError, match=r"^t: missing texture a\.png$"):
        obj_to_glb(obj, "newmtl t\nmap_Kd a.png\n", "t")
    with pytest.raises(ObjError, match=r"^t: missing texture a\.png$"):
        obj_to_glb(obj, "newmtl t\nmap_Kd a.png\n", "t", tmp_path)
