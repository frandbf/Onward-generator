"""Content cache for baked photo tiles, so ``materials`` does not re-encode the same PNG every run.

A photo tile is a pure function of the decoded photograph, the tile side, the
albedo mode, the bake code and the PNG encoder. The key is the sha256 of all of
them, so a warm cache returns the bytes a cold one would write.

The cache lives in ``$ONWARD_GENERATOR_CACHE_DIR/tiles``, else
``$XDG_CACHE_HOME/onward-generator/tiles``, else ``~/.cache/onward-generator/tiles``.
``ONWARD_GENERATOR_NO_TILE_CACHE=1`` turns it off. An entry is the sha256 of the
PNG followed by the PNG, written to a temporary file and renamed into place, so
two runs at once never read half an entry. An entry whose hash does not match is
baked again and replaced. A cache that cannot be read or written is skipped: the
stage still writes its tiles.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import PIL

from onward_generator.dress.atlas import PNG_LEVEL
from onward_generator.io import atomic_write_bytes

# Bump when bake_photo_tile or encode_png_rgba changes the bytes it writes for the same input.
BAKE_VERSION = 1
CACHE_DIR_ENV = "ONWARD_GENERATOR_CACHE_DIR"
NO_CACHE_ENV = "ONWARD_GENERATOR_NO_TILE_CACHE"
DIGEST_BYTES = 32


def tile_cache_dir(env: Mapping[str, str] | None = None) -> Path | None:
    """The tile cache directory for ``env`` (default ``os.environ``), or None when it is turned off."""
    environ = os.environ if env is None else env
    if environ.get(NO_CACHE_ENV, "") not in ("", "0"):
        return None
    if environ.get(CACHE_DIR_ENV):
        root = Path(environ[CACHE_DIR_ENV])
    elif environ.get("XDG_CACHE_HOME"):
        root = Path(environ["XDG_CACHE_HOME"]) / "onward-generator"
    else:
        root = Path(environ.get("HOME") or Path.home()) / ".cache" / "onward-generator"
    return root / "tiles"


def photo_tile_key(rgb: np.ndarray, side: int, mode: str) -> str:
    """sha256 of the decoded photograph and every parameter that changes its baked PNG."""
    pixels = np.ascontiguousarray(rgb)
    digest = hashlib.sha256()
    header = (
        f"bake={BAKE_VERSION};pillow={PIL.__version__};png_level={PNG_LEVEL};"
        f"mode={mode};side={side};shape={pixels.shape};dtype={pixels.dtype}\n"
    )
    digest.update(header.encode())
    digest.update(pixels.tobytes())
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class TileCache:
    root: Path

    def path(self, key: str) -> Path:
        return self.root / f"{key}.tile"

    def fetch(self, key: str, make: Callable[[], bytes]) -> bytes:
        """The cached PNG for ``key``, or ``make()`` written to the cache when it is missing or damaged."""
        cached = self._read(key)
        if cached is not None:
            return cached
        png = make()
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            atomic_write_bytes(self.path(key), hashlib.sha256(png).digest() + png)
        except OSError:
            pass
        return png

    def _read(self, key: str) -> bytes | None:
        try:
            data = self.path(key).read_bytes()
        except OSError:
            return None
        digest, png = data[:DIGEST_BYTES], data[DIGEST_BYTES:]
        return png if hashlib.sha256(png).digest() == digest else None
