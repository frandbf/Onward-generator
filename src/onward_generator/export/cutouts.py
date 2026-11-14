"""Opaque alpha cut-out materials through the scene and into the export package (ADR-040).

``overgrow place`` lists, in the visual GLB's ``extras.materials.cutouts``, each material whose albedo alpha clips a
library tree's leaves, with its threshold. A stage that rebuilds the visual GLB carries the list forward with the
normal maps (``carried_material_extras``). ``export package`` hands it to the adapter as
``adapter_options.json`` ``cutout_materials``, and the adapter turns on the SDK shader's ``ALPHACLIPPING_ON`` keyword
with ``_AlphaClipping`` at that threshold. A scene without the list gets no option and the same ``package_id``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from onward_generator.export.library_normals import scene_normal_maps
from onward_generator.generation.gltf import read_glb

CUTOUT_KEY = "cutouts"


class CutoutError(ValueError):
    """The scene's cut-out list cannot be exported."""


def scene_cutouts(visual: bytes) -> dict[str, float]:
    """The visual GLB's ``extras.materials.cutouts``: material name to alpha threshold; empty without."""
    doc, _ = read_glb(visual)
    found = ((doc.get("extras") or {}).get("materials") or {}).get(CUTOUT_KEY) or {}
    return {str(name): float(value) for name, value in found.items()}


def carried_material_extras(visual: bytes, cutouts: Mapping[str, float] | None = None) -> dict[str, Any]:
    """The scene extras a stage that rebuilds the visual GLB writes: the input's normal maps and cut-outs, with
    ``cutouts`` added; nothing when there are none (the GLB is as before)."""
    materials: dict[str, Any] = {}
    normals = scene_normal_maps(visual)
    if normals:
        materials["normal_maps"] = normals
    merged = {**scene_cutouts(visual), **(cutouts or {})}
    if merged:
        materials[CUTOUT_KEY] = dict(sorted(merged.items()))
    return {"materials": materials} if materials else {}


def cutout_options(cutouts: Mapping[str, float], material_names: set[str]) -> list[dict[str, Any]]:
    """``adapter_options.json`` ``cutout_materials``: ``{material, cutoff}`` sorted by material. ``CutoutError`` when a
    listed material is not a material of the visual GLB or its threshold is not strictly between 0 and 1."""
    rows = []
    for name, cutoff in sorted(cutouts.items()):
        if name not in material_names:
            raise CutoutError(f"cut-out material {name} is not a material of the visual GLB")
        if not 0.0 < cutoff < 1.0:
            raise CutoutError(f"cut-out material {name}: the threshold must lie between 0 and 1, got {cutoff}")
        rows.append({"material": name, "cutoff": cutoff})
    return rows


__all__ = ["CUTOUT_KEY", "CutoutError", "carried_material_extras", "cutout_options", "scene_cutouts"]
