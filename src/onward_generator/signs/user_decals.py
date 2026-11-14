"""Decals the maintainer supplies, placed on every map (ADR-038 amendment of 14 November 2026).

The folder ``assets/user_decals/`` is local and gitignored: a photograph of the maintainer's own stays on this
machine and goes out only inside a built map. It holds PNG images and ``decals.json``::

    {"decals": [{"file": "mural.png", "width_m": 1.2, "description": "A mural from my street"}]}

Each row names one PNG of the folder (a plain file name) and its size on the wall: ``width_m`` or ``height_m``, the
other from the image's aspect, each from ``MIN_SIZE_M`` to ``MAX_SIZE_M``. Transparent pixels stay transparent, an
image without alpha is an opaque rectangle. The image is scaled to at most ``MAX_PX`` on its long side, as the
library decals are. ``signs place`` puts every one of them, once, on an exposed wall of the playable core before
the other decals, and stops when one fits no wall. No folder, or no ``decals.json``: nothing changes.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from onward_generator.signs.decals import Decal

USER_DECALS_DIR = "user_decals"
MANIFEST = "decals.json"
USER_ORIGIN = "user"
USER_KIND = "user"
# The project's own decals (``assets/decals/``, committed, MIT): the same format, on every map, for everyone who uses
# the project (ADR-038 amendment of 14 November 2026). ``ONWARD_PROJECT_DECALS=0`` leaves them out (the tests do).
PROJECT_DECALS_DIR = "decals"
PROJECT_ORIGIN = "project"
PROJECT_DECALS_ENV = "ONWARD_PROJECT_DECALS"
MAX_PX = 512
MAX_FILE_BYTES = 16 * 1024 * 1024
MIN_SIZE_M = 0.2
MAX_SIZE_M = 3.0


class UserDecalError(ValueError):
    """``decals.json`` or one of its images cannot be used."""


def _size(row: dict[str, Any], aspect: float, where: str) -> tuple[float, float]:
    width, height = row.get("width_m"), row.get("height_m")
    if (width is None) == (height is None):
        raise UserDecalError(f"{where}: give width_m or height_m, not both and not neither")
    value = width if width is not None else height
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise UserDecalError(f"{where}: the size must be a number of metres, got {value!r}")
    width_m, height_m = (float(value), float(value) / aspect) if width is not None else (float(value) * aspect, float(value))
    for name, size in (("width", width_m), ("height", height_m)):
        if not MIN_SIZE_M <= size <= MAX_SIZE_M:
            raise UserDecalError(f"{where}: the {name} on the wall is {size:.2f} m; it must lie between {MIN_SIZE_M:g} and {MAX_SIZE_M:g} m")
    return round(width_m, 3), round(height_m, 3)


def project_decals_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Whether the project's decals are read: unless ``ONWARD_PROJECT_DECALS`` is ``0``."""
    return (os.environ if env is None else env).get(PROJECT_DECALS_ENV) != "0"


def read_project_decals(assets: Path) -> list[Decal]:
    """The decals of ``assets/decals/decals.json``: ids ``project_<stem>``, kind and origin ``project``."""
    return read_user_decals(assets, PROJECT_DECALS_DIR, PROJECT_ORIGIN)


def read_user_decals(assets: Path, directory: str = USER_DECALS_DIR, origin: str = USER_ORIGIN) -> list[Decal]:
    """The decals of ``assets/<directory>/decals.json``, in its order; empty when there is no such file. Ids are
    ``<origin>_<file stem>``, kind and origin ``origin``."""
    folder = assets / directory
    manifest = folder / MANIFEST
    if not manifest.is_file():
        return []
    try:
        document = json.loads(manifest.read_text(encoding="utf-8"))  # pragma: no mutate: UTF-8 is also the locale encoding of every host and image this runs on
    except json.JSONDecodeError as exc:
        raise UserDecalError(f"{manifest}: not JSON: {exc.msg}") from exc
    rows = document.get("decals") if isinstance(document, dict) else None
    if not isinstance(rows, list):
        raise UserDecalError(f'{manifest}: needs {{"decals": [...]}}')
    found: list[Decal] = []
    for index, row in enumerate(rows):
        where = f"{manifest} decals[{index}]"
        if not isinstance(row, dict) or not isinstance(row.get("file"), str):
            raise UserDecalError(f"{where}: needs a file name")
        name = row["file"]
        if Path(name).name != name or not name.lower().endswith(".png"):
            raise UserDecalError(f"{where}: file must be a .png in {folder}, by its plain name, got {name!r}")
        path = folder / name
        if not path.is_file():
            raise UserDecalError(f"{where}: {path} does not exist")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise UserDecalError(f"{where}: {name} is over {MAX_FILE_BYTES // (1024 * 1024)} MiB")
        try:
            with Image.open(path) as picture:
                if picture.format != "PNG":
                    raise UserDecalError(f"{where}: {name} is not a PNG image")
                rgba = picture.convert("RGBA")
        except OSError as exc:
            raise UserDecalError(f"{where}: {name} cannot be read: {exc}") from exc
        rgba.thumbnail((MAX_PX, MAX_PX), Image.Resampling.LANCZOS)  # never enlarges
        width_m, height_m = _size(row, rgba.size[0] / rgba.size[1], where)
        stem = "_".join(re.findall(r"[a-z0-9]+", Path(name).stem.lower())) or str(index)
        found.append(Decal(f"{origin}_{stem}", origin, width_m, height_m, np.asarray(rgba).copy(), origin,
                           str(row.get("description") or "")))
    ids = [decal.id for decal in found]
    if len(set(ids)) != len(ids):
        raise UserDecalError(f"{manifest}: two files give the same decal id: " + ", ".join(sorted({i for i in ids if ids.count(i) > 1})))
    return found


__all__ = ["MANIFEST", "MAX_FILE_BYTES", "MAX_PX", "MAX_SIZE_M", "MIN_SIZE_M", "PROJECT_DECALS_DIR", "PROJECT_DECALS_ENV", "PROJECT_ORIGIN",
           "USER_DECALS_DIR", "USER_KIND", "USER_ORIGIN", "UserDecalError", "project_decals_enabled", "read_project_decals",
           "read_user_decals"]
