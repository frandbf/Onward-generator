"""Plan D5 spike (ADR-033, proposed): the tangent-space normal map derived from one facade albedo.

Pixel rows run top to bottom; V = 0 is the bottom row of the PNG, so "up" in tangent space is toward row 0.
The tiny GLBs hold one floor quad at MURCIA_CENTER (local metres, x east, y north, z up).
"""

from __future__ import annotations

import hashlib
import json
import struct
import zlib
from io import BytesIO

import numpy as np
import pytest
from PIL import Image

from onward_generator.export import normal_map as nm
from onward_generator.export.normal_map import (
    DEFAULT_STRENGTH,
    LIT_NORMAL_FILE,
    LIT_NORMAL_ROLE,
    NormalMapError,
    albedo_png,
    astc6x6_bytes,
    decode_rgb,
    encode_png,
    normal_map,
)
from onward_generator.generation.config import Material
from onward_generator.generation.gltf import Node, Primitive, build_glb, pack_glb, read_glb
from onward_generator.generation.mesh import quad
from onward_generator.materials.trim import TRIM_PREFIX


def _grey(value: int, h: int = 8, w: int = 8) -> np.ndarray:
    return np.full((h, w, 3), value, dtype=np.uint8)


def _fixture() -> np.ndarray:
    """16 x 8 deterministic albedo: a diagonal ramp with a bright block, no randomness."""
    y, x = np.mgrid[0:8, 0:16]
    rgb = np.stack([x * 13 + y * 7, x * 5 + y * 19, (x * y * 3) % 251], axis=-1)
    rgb[2:5, 9:12] = 240
    return (rgb % 256).astype(np.uint8)


def test_constants() -> None:
    assert (LIT_NORMAL_FILE, LIT_NORMAL_ROLE, DEFAULT_STRENGTH) == ("lit_normal.png", "lit_normal", 2.0)
    # The module keeps its own copy so the export does not import the materials stage (ADR-007).
    assert nm._TRIM_PREFIX == TRIM_PREFIX


@pytest.mark.parametrize("value", [0, 128, 255])
def test_a_flat_image_is_exactly_straight_up(value: int) -> None:
    out = normal_map(_grey(value), 2.0)
    assert out.dtype == np.uint8 and out.shape == (8, 8, 3)
    assert (out == np.array([128, 128, 255], dtype=np.uint8)).all()


def _ramp(axis: int, h: int = 8, w: int = 8) -> np.ndarray:
    """Brightens with the column (axis 1) or with the row (axis 0)."""
    y, x = np.mgrid[0:h, 0:w]
    values = (x if axis == 1 else y) * 20 + 40
    return np.repeat(values[:, :, None], 3, axis=2).astype(np.uint8)


def test_a_ramp_brightening_to_the_right_tilts_red_down_and_leaves_green() -> None:
    out = normal_map(_ramp(1), 2.0).astype(int)
    assert (out[:, :, 0] < 128).all()
    assert (out[:, :, 1] == 128).all()


def test_green_is_up_toward_row_0() -> None:
    toward_bottom = normal_map(_ramp(0), 2.0).astype(int)
    toward_top = normal_map(_ramp(0)[::-1], 2.0).astype(int)
    # Brighter toward row 0 (up in V): the surface rises upward, so the normal leans down, G below 128.
    assert (toward_top[:, :, 1] < 128).all()
    assert (toward_bottom[:, :, 1] > 128).all()
    assert (toward_top[:, :, 0] == 128).all() and (toward_bottom[:, :, 0] == 128).all()


def test_interior_ramp_value_is_pinned() -> None:
    # Interior Sobel slope of a 20/255 luma step per column: dx = 20/255; n = (-2 dx, 0, 1) normalised.
    dx = 20 / 255
    n = np.array([-2 * dx, 0.0, 1.0]) / np.hypot(2 * dx, 1.0)
    expected = np.round((n * 0.5 + 0.5) * 255).astype(int)
    assert normal_map(_ramp(1), 2.0)[4, 4].tolist() == expected.tolist()
    # The border column repeats itself: half the slope, not a wrapped jump.
    edge = np.array([-dx, 0.0, 1.0]) / np.hypot(dx, 1.0)
    assert normal_map(_ramp(1), 2.0)[4, 0].tolist() == np.round((edge * 0.5 + 0.5) * 255).astype(int).tolist()


def test_border_pixels_repeat_and_never_wrap() -> None:
    image = _grey(0)
    image[0, 0] = 255
    out = normal_map(image, 2.0)
    # The opposite edges see nothing of the corner: no wrap.
    assert (out[-1] == [128, 128, 255]).all() and (out[:, -1] == [128, 128, 255]).all()
    step = _grey(20)
    step[:, 4:] = 220
    out = normal_map(step, 2.0)
    # Outer columns of a uniform region stay flat: not zero padding (the bright edge) and not wrap (the dark edge).
    assert (out[:, 0] == [128, 128, 255]).all() and (out[:, -1] == [128, 128, 255]).all()
    assert (out[:, 3:5, 0] < 128).all()


def test_strength_scales_the_tilt_and_normals_are_unit() -> None:
    image = _fixture()
    weak, strong = normal_map(image, 1.0).astype(int), normal_map(image, 2.0).astype(int)
    tilted = np.abs(weak[:, :, 0] - 128) > 2
    assert tilted.any()
    assert (np.abs(strong[:, :, 0] - 128)[tilted] > np.abs(weak[:, :, 0] - 128)[tilted]).all()
    for out in (weak, strong, normal_map(image, 8.0).astype(int)):
        assert (out[:, :, 2] >= 128).all()
        decoded = out / 255.0 * 2.0 - 1.0
        assert np.allclose(np.linalg.norm(decoded, axis=-1), 1.0, atol=2 / 255 * np.sqrt(3))


@pytest.mark.parametrize("strength", [0, -1.0, float("nan")])
def test_a_strength_not_above_zero_is_refused(strength: float) -> None:
    with pytest.raises(ValueError, match="strength must be above 0"):
        normal_map(_grey(100), strength)


@pytest.mark.parametrize("shape", [(8, 8), (8, 8, 5), (8, 8, 2), (8,)])
def test_a_wrong_shape_is_refused(shape: tuple[int, ...]) -> None:
    with pytest.raises(ValueError, match="expected an H x W x 3 or H x W x 4 image"):
        normal_map(np.zeros(shape, dtype=np.uint8), 2.0)


def test_alpha_is_ignored() -> None:
    rgb = _fixture()
    rgba = np.concatenate([rgb, np.arange(128, dtype=np.uint8).reshape(8, 16, 1)], axis=2)
    assert (normal_map(rgba, 2.0) == normal_map(rgb, 2.0)).all()


def test_luma_is_rec709() -> None:
    assert nm.LUMA == (0.2126, 0.7152, 0.0722)
    red, green = _grey(0), _grey(0)
    red[:, 4:, 0] = 255
    green[:, 4:, 1] = 255
    # The same step in green tilts more than in red: Rec.709 weights green 0.7152, red 0.2126.
    assert abs(int(normal_map(green, 2.0)[4, 4, 0]) - 128) > abs(int(normal_map(red, 2.0)[4, 4, 0]) - 128)


def _chunks(png: bytes) -> list[bytes]:
    names, at = [], 8
    while at < len(png):
        length, name = struct.unpack(">I4s", png[at : at + 8])
        names.append(name)
        at += 12 + length
    return names


def test_output_is_deterministic_and_pinned() -> None:
    pixels = normal_map(_fixture(), DEFAULT_STRENGTH)
    assert hashlib.sha256(pixels.tobytes()).hexdigest() == PINNED_PIXELS_SHA256
    first, second = encode_png(pixels), encode_png(normal_map(_fixture(), DEFAULT_STRENGTH))
    assert first == second
    with Image.open(BytesIO(first)) as image:
        assert image.mode == "RGB" and image.size == (16, 8)
        assert (np.asarray(image) == pixels).all()
    assert _chunks(first) == [b"IHDR", b"IDAT", b"IEND"]
    # Fixed compression level 6: the IDAT holds zlib at level 6 (FLG 0x9c).
    assert first[first.index(b"IDAT") + 4 : first.index(b"IDAT") + 6] == b"\x78\x9c"


@pytest.mark.parametrize("shape", [(8, 8), (8, 8, 4), (8, 8, 1)])
def test_encode_png_refuses_anything_but_rgb(shape: tuple[int, ...]) -> None:
    with pytest.raises(ValueError, match="expected an H x W x 3 normal map"):
        encode_png(np.zeros(shape, dtype=np.uint8))


PINNED_PIXELS_SHA256 = "1cec4b6836f1b10b9de0f0ed31ae8d56d302503cb9706daeefbecec9cc98835f"


def test_decode_rgb_drops_alpha() -> None:
    out = BytesIO()
    Image.new("RGBA", (3, 2), (10, 20, 30, 40)).save(out, format="PNG")
    rgb = decode_rgb(out.getvalue())
    assert rgb.shape == (2, 3, 3) and rgb.dtype == np.uint8 and (rgb == [10, 20, 30]).all()


@pytest.mark.parametrize(("size", "expected"), [
    ((6, 6), 16 + 16 + 16),
    ((1, 1), 16),
    ((7, 6), 32 + 16 + 16),
    ((12, 6), 32 + 16 + 16 + 16),
    # The facade trim atlas (ADR-020): 3 737 376 bytes at level 0, about 4.8 MiB with the 13 levels.
    ((2048, 4096), 4988080),
])
def test_astc6x6_bytes(size: tuple[int, int], expected: int) -> None:
    assert astc6x6_bytes(*size) == expected


@pytest.mark.parametrize("size", [(0, 4), (4, 0), (-1, 1)])
def test_astc6x6_bytes_refuses_an_empty_size(size: tuple[int, int]) -> None:
    with pytest.raises(ValueError, match="at least 1 x 1"):
        astc6x6_bytes(*size)


# ---- albedo_png


def _png(colour: tuple[int, int, int]) -> bytes:
    out = BytesIO()
    Image.new("RGB", (4, 2), colour).save(out, format="PNG")
    return out.getvalue()


def _quad_glb() -> bytes:
    floor = quad((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0))
    names = ("facade_trim_test", "party_facade_trim_test", "roof_plain", "wall_other")
    nodes = [Node(f"n/{i}", (Primitive(floor, name),)) for i, name in enumerate(names)]
    materials = {name: Material((1.0, 1.0, 1.0, 1.0), 0.9) for name in names}
    image = _png((200, 100, 50))
    return build_glb(nodes, materials, repeat_images={"facade_trim_test": image, "party_facade_trim_test": image,
                                                      "wall_other": _png((1, 2, 3))})


def test_albedo_png_follows_texture_source_to_the_image_bytes() -> None:
    glb = _quad_glb()
    assert albedo_png(glb, "facade_trim_test") == _png((200, 100, 50))
    assert albedo_png(glb, "party_facade_trim_test") == albedo_png(glb, "facade_trim_test")
    assert albedo_png(glb, "wall_other") == _png((1, 2, 3))


def _edited(glb: bytes, edit) -> bytes:  # type: ignore[no-untyped-def]
    doc, blob = read_glb(glb)
    edit(doc)
    return pack_glb(json.loads(json.dumps(doc)), blob)


def test_albedo_png_maps_a_texture_to_its_source_not_its_index() -> None:
    def swap(doc):  # type: ignore[no-untyped-def]
        doc["textures"] = list(reversed(doc["textures"]))
    glb = _edited(_quad_glb(), swap)
    # Texture 0 now points at image 1 (wall_other's colour).
    doc = read_glb(glb)[0]
    trim = next(m for m in doc["materials"] if m["name"] == "facade_trim_test")
    assert doc["textures"][trim["pbrMetallicRoughness"]["baseColorTexture"]["index"]]["source"] != \
        trim["pbrMetallicRoughness"]["baseColorTexture"]["index"]
    assert albedo_png(glb, "facade_trim_test") == _png((1, 2, 3))


def test_albedo_png_without_textures_reads_the_image_index() -> None:
    def drop(doc):  # type: ignore[no-untyped-def]
        del doc["textures"]
    assert albedo_png(_edited(_quad_glb(), drop), "wall_other") == _png((1, 2, 3))


def test_albedo_png_refuses_a_missing_material_and_lists_the_trim_ones() -> None:
    with pytest.raises(NormalMapError, match=r"'facade_trim_nope' is not in the visual GLB \(facade trim materials present: facade_trim_test\)"):
        albedo_png(_quad_glb(), "facade_trim_nope")
    floor = quad((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0))
    plain = build_glb([Node("n", (Primitive(floor, "roof_plain"),))], {"roof_plain": Material((1, 1, 1, 1), 0.9)})
    with pytest.raises(NormalMapError, match="present: none"):
        albedo_png(plain, "facade_trim_test")


def test_albedo_png_refuses_a_material_without_a_texture() -> None:
    with pytest.raises(NormalMapError, match="'roof_plain' has no base colour texture"):
        albedo_png(_quad_glb(), "roof_plain")


@pytest.mark.parametrize(("edit", "match"), [
    (lambda d: d["images"][0].update(mimeType="image/jpeg"), "is 'image/jpeg', not image/png"),
    (lambda d: d["images"][0].pop("bufferView"), "has no bufferView"),
    (lambda d: d.update(images=[]), "image 0 is not in the GLB"),
    (lambda d: d["textures"][0].pop("source"), "texture 0 has no image"),
    (lambda d: d.update(textures=[]), "texture 0 has no image"),
])
def test_albedo_png_refuses_a_broken_image(edit, match: str) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(NormalMapError, match=match):
        albedo_png(_edited(_quad_glb(), edit), "facade_trim_test")


def test_albedo_png_uses_the_view_offset() -> None:
    glb = _quad_glb()
    doc, blob = read_glb(glb)
    view = doc["bufferViews"][doc["images"][1]["bufferView"]]
    assert view["byteOffset"] > 0
    assert zlib.crc32(albedo_png(glb, "wall_other")) == zlib.crc32(_png((1, 2, 3)))
