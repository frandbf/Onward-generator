"""Lightmap scale per surface class, and an offline estimate of the baked texel area.

Unity bakes every static renderer at ``lightmap_texels_per_m`` times its
``scaleInLightmap``. The adapter combines the visual GLB by (cell, material), so a
scale is chosen per material: roofs, ground and interiors take fewer texels than
the facades a player walks past. Renderers kept out of the lightmap (trees,
urban metal, railings, overgrow props, the horizon backdrop) mirror
``ContributesGi`` in ``tools/unity/onward_adapter/Editor/OnwardMapAdapter.cs``.

The estimate is not a Unity measurement. It sums, per combined renderer, its
surface area times the squared texel density, capped at one atlas page as Unity
scales down a renderer that does not fit. Chart packing and padding are left
out, so the page count is a lower bound.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

import numpy as np

from onward_generator.generation.gltf import nodes_from_glb

# Scale in the lightmap by surface class. Facades and everything else stay at 1.
DEFAULT_CLASS_SCALES: Mapping[str, float] = {"roof": 0.25, "ground": 0.5, "interior": 0.5}
GROUND_MATERIALS = frozenset({
    "ground", "pedestrian", "sidewalk", "carriageway", "curb", "green", "water", "cobblestone_paving",
})
INTERIOR_MATERIALS = frozenset({"interior_wall", "interior_floor", "stair"})
# Substrings and prefix of ContributesGi in the adapter, on the lower-case glTF material name.
NO_GI_PARTS = ("tree_", "urban_metal", "railing_metal", "horizon_backdrop")
NO_GI_PREFIX = "prop_"


def surface_class(material: str) -> str:
    """``roof``, ``ground``, ``interior`` or ``facade`` (every other lit surface)."""
    if material.startswith("roof_") or material == "building_roof":
        return "roof"
    if material in GROUND_MATERIALS:
        return "ground"
    if material in INTERIOR_MATERIALS:
        return "interior"
    return "facade"


def contributes_gi(material: str) -> bool:
    """False for the renderers the adapter keeps out of the lightmap (``scaleInLightmap = 0``)."""
    name = material.lower()
    return not (name.startswith(NO_GI_PREFIX) or any(part in name for part in NO_GI_PARTS))


def material_scales(materials: Iterable[str], class_scales: Mapping[str, float]) -> list[dict[str, Any]]:
    """``{"material", "scale"}`` for each lit material whose class scale is not 1, sorted by name."""
    rows = []
    for name in sorted(set(materials)):
        scale = float(class_scales.get(surface_class(name), 1.0))
        if contributes_gi(name) and scale != 1.0:
            rows.append({"material": name, "scale": scale})
    return rows


def estimate(visual: bytes, *, texels_per_m: float, max_size: int, cell_m: float,
             scales: Iterable[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """Texels the bake needs for the visual GLB, grouped as the adapter combines it.

    ``cell_m`` 0 means one renderer per node, as the adapter does without combining.
    """
    by_material = {str(row["material"]): float(row["scale"]) for row in scales}
    nodes, _materials = nodes_from_glb(visual)
    areas: dict[tuple[tuple[int, ...], str], float] = defaultdict(float)
    for index, node in enumerate(nodes):
        for primitive in node.world_primitives():
            # build_glb never writes a primitive without triangles.
            if not contributes_gi(primitive.material):
                continue
            vertices = primitive.mesh.vertices
            centre = (vertices.min(axis=0) + vertices.max(axis=0)) * 0.5
            cell = (math.floor(centre[0] / cell_m), math.floor(centre[1] / cell_m)) if cell_m > 0 else (index,)
            areas[(cell, primitive.material)] += _area(vertices, primitive.mesh.triangles)
    page = max_size * max_size
    texels = 0.0
    capped = 0
    by_class: dict[str, float] = defaultdict(float)
    for (_cell, material), area in areas.items():
        density = texels_per_m * by_material.get(material, 1.0)
        wanted = area * density * density
        if wanted > page:
            capped += 1
        used = min(wanted, page)
        texels += used
        by_class[surface_class(material)] += used
    return {
        "texels_per_m": texels_per_m,
        "max_size": max_size,
        "renderers": len(areas),
        "renderers_capped": capped,
        "texels": int(round(texels)),
        "texels_by_class": {name: int(round(value)) for name, value in sorted(by_class.items())},
        "pages_at_least": math.ceil(texels / page),
    }


def _area(vertices: np.ndarray, triangles: np.ndarray) -> float:
    a = vertices[triangles[:, 0]]
    cross = np.cross(vertices[triangles[:, 1]] - a, vertices[triangles[:, 2]] - a)
    return float(np.linalg.norm(cross, axis=1).sum() * 0.5)
