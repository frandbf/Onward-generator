"""One GLB packer and one PNG encoder: the bytes match what each site wrote before. No network."""

from __future__ import annotations

import io
import struct
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from onward_generator.bounds.envelope import backdrop_png
from onward_generator.dress.atlas import encode_png
from onward_generator.generation import preview
from onward_generator.generation.config import Material
from onward_generator.generation.gltf import Node, Primitive, build_glb, pack_glb, read_glb
from onward_generator.generation.mesh import box as mesh_box

REPO = Path(__file__).resolve().parents[2]


def _scene_glb() -> bytes:
    node = Node("crate", (Primitive(mesh_box(0.0, 0.0, 1.0, 1.0, 0.0, 1.0), "wood"),))
    return build_glb([node], {"wood": Material((0.6, 0.4, 0.2, 1.0), 0.8)}, extras={"note": "ñ"})


def test_pack_glb_writes_the_header_and_pads_both_chunks() -> None:
    packed = pack_glb({"a": "ñ"}, b"\x01\x02\x03\x04\x05")
    json_chunk = '{"a":"ñ"}'.encode() + b"  "
    assert packed == b"".join([
        struct.pack("<4sII", b"glTF", 2, 12 + 8 + 12 + 8 + 8),
        struct.pack("<I4s", 12, b"JSON"), json_chunk,
        struct.pack("<I4s", 8, b"BIN\x00"), b"\x01\x02\x03\x04\x05\x00\x00\x00",
    ])
    assert read_glb(packed) == ({"a": "ñ"}, b"\x01\x02\x03\x04\x05\x00\x00\x00")


def test_pack_glb_round_trips_a_built_glb_byte_for_byte() -> None:
    data = _scene_glb()
    assert pack_glb(*read_glb(data)) == data


def test_pack_glb_round_trips_an_installed_kenney_glb_byte_for_byte() -> None:
    glbs = sorted((REPO / "assets" / "kenney").rglob("*.glb"))
    if not glbs:
        pytest.skip("no Kenney GLB installed")
    data = glbs[0].read_bytes()
    assert pack_glb(*read_glb(data)) == data


def _pillow(pixels: np.ndarray, **options: object) -> bytes:
    buffer = io.BytesIO()
    Image.fromarray(pixels).save(buffer, format="PNG", **options)
    return buffer.getvalue()


def test_encode_png_matches_pillow_at_the_given_level() -> None:
    pixels = (np.arange(16 * 8 * 3) % 251).astype(np.uint8).reshape(16, 8, 3)
    assert encode_png(pixels, 9) == _pillow(pixels, compress_level=9)
    assert encode_png(pixels, 1) == _pillow(pixels, compress_level=1)
    assert encode_png(pixels, 1) != encode_png(pixels, 9)


def test_the_backdrop_keeps_the_pillow_default_bytes() -> None:
    decoded = np.asarray(Image.open(io.BytesIO(backdrop_png())))
    assert backdrop_png() == _pillow(np.ascontiguousarray(decoded))


GRADIENT = (np.arange(24 * 32 * 3) % 97 / 96.0).reshape(24, 32, 3)


def test_the_preview_keeps_level_nine(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def render_rgb(*args: object, **kwargs: object) -> np.ndarray:
        seen.update(kwargs)
        return GRADIENT

    monkeypatch.setattr(preview, "render_rgb", render_rgb)
    expected = _pillow((GRADIENT * 255).round().astype(np.uint8), compress_level=9, optimize=False)
    assert preview.render([], {}, azimuth_deg=95.0, splat_subpixel=True) == expected
    assert seen == {
        "width": 1600, "height": 1100, "azimuth_deg": 95.0, "elevation_deg": 40.0,
        "margin_px": 30, "focus": None, "splat_subpixel": True,
    }
    assert expected != _pillow((GRADIENT * 255).round().astype(np.uint8))


def test_side_by_side_keeps_level_nine() -> None:
    gap = np.full((24, 8, 3), 0.15)
    expected = _pillow((np.concatenate([GRADIENT, gap, GRADIENT], axis=1) * 255).round().astype(np.uint8), compress_level=9)
    assert preview.side_by_side([GRADIENT, GRADIENT]) == expected
