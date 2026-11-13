"""Real normal maps of library photographs in the export package (plan T3, ADR-039).

``materials apply --normal-maps`` lists, in the visual GLB's ``extras.materials.normal_maps``, each material that draws a
wide-library photograph with its OpenGL normal map (``nor_gl``, CC0). ``export package`` writes each map once, as a PNG
``normal_<library id>.png`` beside the GLBs (package paths are plain file names), checks its hash against the library, prices it in ASTC 6x6 and refuses a set over
``NORMAL_MAPS_MAX_BYTES``. The adapter draws those materials with ``PP_Opaque_Lit`` and the map on ``_BumpMap``; every
other material keeps ``PP_Opaque_Simple_Lit`` and an albedo only. A scene without the list gets no file, no option and
the same ``package_id`` as before.
"""

from __future__ import annotations

import hashlib
import io
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from onward_generator.export.normal_map import astc6x6_bytes, encode_png
from onward_generator.generation.gltf import read_glb

NORMAL_MAP_ROLE = "normal_map"
NORMAL_MAP_PREFIX = "normal_"
MAX_PX = 1024
"""A normal map is drawn at most this large, the size of the library photographs (``MAX_PHOTO_PX``)."""
NORMAL_MAPS_MAX_BYTES = 24 * 1024 * 1024
"""All the normal maps of a package together, in ASTC 6x6: 24 MiB, about 2.4 % of the guide's 1 GB texture figure
(ADR-005). A 1k map costs 0.46 MiB, so the limit is far above a map's dozen photographs."""
REPO_ROOT = Path(__file__).resolve().parents[3]


class LibraryNormalError(ValueError):
    """The scene's normal-map list cannot be exported."""


def scene_normal_maps(visual: bytes) -> dict[str, dict[str, str]]:
    """The visual GLB's ``extras.materials.normal_maps``: material name to ``{library_id, path, sha256}``; empty without."""
    doc, _ = read_glb(visual)
    found = ((doc.get("extras") or {}).get("materials") or {}).get("normal_maps") or {}
    return {str(name): {key: str(value) for key, value in binding.items()} for name, binding in found.items()}


def carried_normal_maps(visual: bytes) -> dict[str, Any]:
    """The scene extras a stage that rebuilds the visual GLB carries forward, so ``export package`` still finds the list:
    ``{"materials": {"normal_maps": ...}}`` when the input lists normal maps, else nothing (the GLB is as before)."""
    bindings = scene_normal_maps(visual)
    return {"materials": {"normal_maps": bindings}} if bindings else {}


def normal_map_files(bindings: Mapping[str, Mapping[str, str]], *, lit_normal_material: str | None = None,
                     repo: Path = REPO_ROOT) -> tuple[dict[str, bytes], dict[str, Any]]:
    """The PNG of each library normal map the ``bindings`` name, once per map (several materials may share one), and
    the manifest block. ``LibraryNormalError`` when a map is missing or changed, when a material is also the plan D5
    ``lit_normal_material`` (one normal map per material), or when the set is over ``NORMAL_MAPS_MAX_BYTES``."""
    if lit_normal_material is not None and lit_normal_material in bindings:
        raise LibraryNormalError(f"{lit_normal_material} is both the lit_normal_material and a library normal-map material")
    pngs: dict[str, bytes] = {}
    files: list[dict[str, Any]] = []
    materials: dict[str, str] = {}
    for name, binding in sorted(bindings.items()):
        path = f"{NORMAL_MAP_PREFIX}{binding['library_id']}.png"
        materials[name] = path
        if path in pngs:
            continue
        source = repo / binding["path"]
        if not source.is_file():
            raise LibraryNormalError(f"{name}: normal map {binding['path']} is missing")
        data = source.read_bytes()
        if hashlib.sha256(data).hexdigest() != binding["sha256"]:
            raise LibraryNormalError(f"{name}: normal map {binding['path']} does not match the library hash")
        with Image.open(io.BytesIO(data)) as image:
            rgb = image.convert("RGB")
            rgb.thumbnail((MAX_PX, MAX_PX), Image.Resampling.LANCZOS)
            pixels = np.asarray(rgb)
        pngs[path] = encode_png(pixels)
        height, width = int(pixels.shape[0]), int(pixels.shape[1])
        files.append({"path": path, "library_id": binding["library_id"], "width": width, "height": height,
                      "astc6x6_bytes": astc6x6_bytes(width, height), "source": binding["path"], "source_sha256": binding["sha256"]})
    total = sum(int(f["astc6x6_bytes"]) for f in files)
    if total > NORMAL_MAPS_MAX_BYTES:
        raise LibraryNormalError(f"{len(files)} normal maps take {total} bytes in ASTC 6x6, over the {NORMAL_MAPS_MAX_BYTES} limit")
    block = {"materials": materials, "files": files, "astc6x6_bytes": total, "limit_bytes": NORMAL_MAPS_MAX_BYTES,
             "origin": "observed", "license": "CC0-1.0"}
    return pngs, block


__all__ = ["MAX_PX", "carried_normal_maps", "NORMAL_MAPS_MAX_BYTES", "NORMAL_MAP_PREFIX", "NORMAL_MAP_ROLE", "LibraryNormalError", "normal_map_files",
           "scene_normal_maps"]
