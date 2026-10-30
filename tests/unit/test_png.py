from __future__ import annotations

import hashlib
from functools import partial

import numpy as np
import pytest

from onward_generator.dress.atlas import PNG_WORKERS, decode_png_rgba, encode_png_rgba, run_png_jobs
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


def test_png_jobs_on_threads_give_the_serial_bytes_in_input_order() -> None:
    rng = np.random.default_rng(11)
    # Large and small images interleaved, more than one per worker, so jobs finish out of order.
    images = [rng.integers(0, 256, size=(side, side, 4), dtype=np.uint8) for side in (512, 3, 256, 1, 384, 9) * PNG_WORKERS]
    serial = [hashlib.sha256(encode_png_rgba(image)).hexdigest() for image in images]
    threaded = run_png_jobs({f"tile_{index}": partial(encode_png_rgba, image) for index, image in enumerate(images)})
    assert list(threaded) == [f"tile_{index}" for index in range(len(images))]
    assert [hashlib.sha256(data).hexdigest() for data in threaded.values()] == serial


def test_png_jobs_raise_the_first_failure() -> None:
    def broken() -> bytes:
        raise ValueError("bad tile")

    with pytest.raises(ValueError, match="bad tile"):
        run_png_jobs({"good": partial(encode_png_rgba, _pixels()), "bad": broken})
    assert run_png_jobs({}) == {}
