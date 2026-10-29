from __future__ import annotations

import numpy as np
import pytest

from onward_generator.dress.atlas import decode_png_rgba, encode_png_rgba
from onward_generator.generation.preview import _png


def _pixels() -> np.ndarray:
    rng = np.random.default_rng(7)
    return rng.integers(0, 256, size=(5, 7, 4), dtype=np.uint8)


def test_png_roundtrip_rgba() -> None:
    pixels = _pixels()
    data = encode_png_rgba(pixels)
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    decoded = decode_png_rgba(data)
    assert decoded.shape == (5, 7, 4)
    assert decoded.dtype == np.uint8
    assert np.array_equal(decoded, pixels)


def test_png_encoding_is_deterministic_and_has_no_metadata() -> None:
    pixels = _pixels()
    data = encode_png_rgba(pixels)
    assert data == encode_png_rgba(pixels.copy())
    for chunk in (b"tEXt", b"iTXt", b"zTXt", b"tIME", b"pHYs"):
        assert chunk not in data


def test_encode_rejects_wrong_shape_or_dtype() -> None:
    with pytest.raises(ValueError):
        encode_png_rgba(np.zeros((4, 4, 3), dtype=np.uint8))
    with pytest.raises(ValueError):
        encode_png_rgba(np.zeros((4, 4, 4), dtype=np.float32))


def test_decode_rejects_non_png() -> None:
    with pytest.raises(ValueError, match="not a PNG"):
        decode_png_rgba(b"\xff\xd8\xff\xe0 not a png")
    with pytest.raises(ValueError):
        decode_png_rgba(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)


def test_decode_widens_rgb_png_to_opaque_rgba() -> None:
    rgb = _pixels()[..., :3].copy()
    decoded = decode_png_rgba(_png(rgb))
    assert np.array_equal(decoded[..., :3], rgb)
    assert (decoded[..., 3] == 255).all()
