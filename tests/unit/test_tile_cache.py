from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest

from onward_generator.dress.atlas import bake_photo_tile, encode_png_rgba
from onward_generator.materials.apply import _photo_png
from onward_generator.materials.tile_cache import TileCache, photo_tile_key, tile_cache_dir


def _photo() -> np.ndarray:
    return np.random.default_rng(3).integers(0, 256, size=(6, 5, 3), dtype=np.uint8)


class _Baker:
    def __init__(self, png: bytes) -> None:
        self.png = png
        self.calls = 0

    def __call__(self) -> bytes:
        self.calls += 1
        return self.png


def test_cache_dir_follows_the_environment(tmp_path: Path) -> None:
    home = tmp_path / "home"
    assert tile_cache_dir({"HOME": str(home)}) == home / ".cache" / "onward-generator" / "tiles"
    assert tile_cache_dir({}) == Path.home() / ".cache" / "onward-generator" / "tiles"
    xdg = {"HOME": str(home), "XDG_CACHE_HOME": str(tmp_path / "xdg")}
    assert tile_cache_dir(xdg) == tmp_path / "xdg" / "onward-generator" / "tiles"
    own = {**xdg, "ONWARD_GENERATOR_CACHE_DIR": str(tmp_path / "own")}
    assert tile_cache_dir(own) == tmp_path / "own" / "tiles"
    assert tile_cache_dir({**own, "ONWARD_GENERATOR_NO_TILE_CACHE": "0"}) == tmp_path / "own" / "tiles"
    assert tile_cache_dir({**own, "ONWARD_GENERATOR_NO_TILE_CACHE": "1"}) is None
    assert tile_cache_dir({**own, "ONWARD_GENERATOR_NO_TILE_CACHE": "yes"}) is None


def test_cache_dir_reads_os_environ(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ONWARD_GENERATOR_NO_TILE_CACHE")
    monkeypatch.setenv("ONWARD_GENERATOR_CACHE_DIR", str(tmp_path))
    assert tile_cache_dir() == tmp_path / "tiles"


def test_photo_tile_key_is_pinned_and_covers_every_input(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("PIL.__version__", "12.0.0")
    rgb = _photo()
    key = photo_tile_key(rgb, 6, "color_albedo")
    assert key == hashlib.sha256(
        b"bake=1;pillow=12.0.0;png_level=9;mode=color_albedo;side=6;shape=(6, 5, 3);dtype=uint8\n" + rgb.tobytes()
    ).hexdigest()
    # A non-contiguous view hashes the pixels it shows.
    wide = np.zeros((6, 10, 3), dtype=np.uint8)
    wide[:, ::2] = rgb
    assert photo_tile_key(wide[:, ::2], 6, "color_albedo") == key


def test_cache_returns_the_same_bytes_cold_and_warm_without_baking_again(tmp_path: Path) -> None:
    cache = TileCache(tmp_path / "a" / "tiles")
    first, second = _Baker(b"first tile"), _Baker(b"second tile")
    assert cache.fetch("k1", first) == b"first tile"
    assert cache.fetch("k2", second) == b"second tile"
    assert cache.fetch("k1", first) == b"first tile"
    assert cache.fetch("k2", second) == b"second tile"
    assert (first.calls, second.calls) == (1, 1)
    assert cache.path("k1") == tmp_path / "a" / "tiles" / "k1.tile"
    assert cache.path("k1").read_bytes() == hashlib.sha256(b"first tile").digest() + b"first tile"
    assert sorted(p.name for p in cache.root.iterdir()) == ["k1.tile", "k2.tile"]


def test_a_damaged_entry_is_baked_again_and_replaced(tmp_path: Path) -> None:
    cache = TileCache(tmp_path)
    baker = _Baker(b"tile bytes")
    cache.fetch("k", baker)
    entry = cache.path("k")
    entry.write_bytes(entry.read_bytes()[:-1] + b"X")
    assert cache.fetch("k", baker) == b"tile bytes"
    assert cache.fetch("k", baker) == b"tile bytes"
    assert baker.calls == 2
    entry.write_bytes(b"short")
    assert cache.fetch("k", baker) == b"tile bytes"
    assert baker.calls == 3


def test_a_cache_that_cannot_be_written_still_returns_the_tile(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_bytes(b"")
    cache = TileCache(blocker / "tiles")
    baker = _Baker(b"tile bytes")
    assert cache.fetch("k", baker) == b"tile bytes"
    assert cache.fetch("k", baker) == b"tile bytes"
    assert baker.calls == 2


def test_photo_png_is_the_same_with_a_cold_or_warm_cache(tmp_path: Path) -> None:
    rgb = np.random.default_rng(5).integers(0, 256, size=(40, 30, 3), dtype=np.uint8)
    expected = encode_png_rgba(bake_photo_tile(rgb, 40, "greyscale_modulation"))
    cache = TileCache(tmp_path)
    assert _photo_png(rgb, "greyscale_modulation") == expected
    assert _photo_png(rgb, "greyscale_modulation", cache) == expected
    key = photo_tile_key(rgb, 40, "greyscale_modulation")
    assert cache.path(key).read_bytes()[32:] == expected
    assert _photo_png(rgb, "greyscale_modulation", cache) == expected
