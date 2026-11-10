"""Lightmap scale per surface class, and an offline estimate of the baked texel area.

Unity bakes every static renderer at ``lightmap_texels_per_m`` times its
``scaleInLightmap``. The adapter combines the visual GLB by (cell, material), so a
scale is chosen per material: roofs, ground, interiors and blank party walls
(``party_*``, docs/07-detail.md) take fewer texels than the facades a player walks past. Renderers kept out of the lightmap (trees,
urban metal, railings, overgrow props, the horizon backdrop) mirror
``ContributesGi`` in ``tools/unity/onward_adapter/Editor/OnwardMapAdapter.cs``. With the export option
``build.scenic_out_of_lightmap`` (plan C4, ADR-017 amendment, off when absent) the scenic shell walls
(``scenic_*``) leave the lightmap too; they stay in the bake as shadow casters, as props and trees do.

The estimate is not a Unity measurement. It sums, per combined renderer, its
surface area times the squared texel density, capped at one atlas page as Unity
scales down a renderer that does not fit. Chart packing and padding are left
out, so the page count is a lower bound.

With the export option ``build.lightmap_pages_max`` (plan C5, ADR-005 amendment, off when absent)
``page_budget_density`` lowers the density until that lower bound fits the budget, never below
``LIGHTMAP_TEXELS_FLOOR``.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

import numpy as np

from onward_generator.detail.config import PARTY_PREFIX
from onward_generator.generation.gltf import nodes_from_glb
from onward_generator.tiers import SCENIC_PREFIX

# Scale in the lightmap by surface class. Facades and everything else stay at 1.
# The scenic shells outside the playable core (ADR-017) bake at the roof scale.
DEFAULT_CLASS_SCALES: Mapping[str, float] = {"roof": 0.25, "ground": 0.5, "interior": 0.5, "scenic": 0.25, "party": 0.5}
GROUND_MATERIALS = frozenset({
    "ground", "pedestrian", "sidewalk", "carriageway", "curb", "green", "water", "cobblestone_paving",
    "road_marking",
})
INTERIOR_MATERIALS = frozenset({"interior_wall", "interior_floor", "stair"})
# Shop interiors paint their walls and floors with ``interior_shop_*`` (docs/07-detail.md).
INTERIOR_PREFIX = "interior_"
# Substrings and prefix of ContributesGi in the adapter, on the lower-case glTF material name.
NO_GI_PARTS = ("tree_", "urban_metal", "railing_metal", "horizon_backdrop")
NO_GI_PREFIX = "prop_"


def surface_class(material: str) -> str:
    """``roof``, ``ground``, ``interior``, ``scenic``, ``party`` or ``facade`` (every other lit surface)."""
    if material.startswith("roof_") or material == "building_roof":
        return "roof"
    if material.startswith(SCENIC_PREFIX):
        return "scenic"
    if material.startswith(PARTY_PREFIX):
        return "party"
    if material in GROUND_MATERIALS:
        return "ground"
    if material in INTERIOR_MATERIALS or material.startswith(INTERIOR_PREFIX):
        return "interior"
    return "facade"


def contributes_gi(material: str, *, scenic_out_of_lightmap: bool = False) -> bool:
    """False for the renderers the adapter keeps out of the lightmap (``scaleInLightmap = 0``).

    ``scenic_out_of_lightmap`` also leaves out the scenic shell walls (``scenic_*``); the scenic roofs share
    ``roof_flat_grey`` with the core and stay baked.
    """
    name = material.lower()
    if scenic_out_of_lightmap and name.startswith(SCENIC_PREFIX):
        return False
    return not (name.startswith(NO_GI_PREFIX) or any(part in name for part in NO_GI_PARTS))


def material_scales(materials: Iterable[str], class_scales: Mapping[str, float], *,
                    scenic_out_of_lightmap: bool = False) -> list[dict[str, Any]]:
    """``{"material", "scale"}`` for each lit material whose class scale is not 1, sorted by name."""
    rows = []
    for name in sorted(set(materials)):
        scale = float(class_scales.get(surface_class(name), 1.0))
        if contributes_gi(name, scenic_out_of_lightmap=scenic_out_of_lightmap) and scale != 1.0:
            rows.append({"material": name, "scale": scale})
    return rows


# Plan C5 (ADR-005 amendment, proposed): the page budget never lowers the density below this floor. ADR-005
# recorded lightmap UV overlaps at 2 texels/m; the floor is twice that, and unmeasured on the headset.
LIGHTMAP_TEXELS_FLOOR = 4.0
# The lowered density is a multiple of this step, a power of two so every grid value is an exact float.
LIGHTMAP_DENSITY_STEP = 0.25

LightmapAreas = dict[tuple[tuple[int, ...], str], float]


class LightmapBudgetError(ValueError):
    """``build.lightmap_pages_max`` cannot be met at or above ``LIGHTMAP_TEXELS_FLOOR``."""


def lightmap_areas(visual: bytes, *, cell_m: float, scenic_out_of_lightmap: bool = False) -> LightmapAreas:
    """Surface area in m² of each lit renderer of the visual GLB, keyed by (cell, material) as the adapter combines it.

    ``cell_m`` 0 means one renderer per node, as the adapter does without combining.
    ``scenic_out_of_lightmap`` leaves the ``scenic_*`` walls out, as ``contributes_gi`` does.
    """
    nodes, _materials = nodes_from_glb(visual)
    areas: LightmapAreas = defaultdict(float)
    for index, node in enumerate(nodes):
        for primitive in node.world_primitives():
            # build_glb never writes a primitive without triangles.
            if not contributes_gi(primitive.material, scenic_out_of_lightmap=scenic_out_of_lightmap):
                continue
            vertices = primitive.mesh.vertices
            centre = (vertices.min(axis=0) + vertices.max(axis=0)) * 0.5
            cell = (math.floor(centre[0] / cell_m), math.floor(centre[1] / cell_m)) if cell_m > 0 else (index,)
            areas[(cell, primitive.material)] += _area(vertices, primitive.mesh.triangles)
    return areas


def summarise(areas: LightmapAreas, *, texels_per_m: float, max_size: int,
              scales: Iterable[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """The estimate of ``areas`` at ``texels_per_m``: each renderer's texels capped at one page, then the pages."""
    by_material = _by_material(scales)
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


def estimate(visual: bytes, *, texels_per_m: float, max_size: int, cell_m: float,
             scales: Iterable[Mapping[str, Any]] = (), scenic_out_of_lightmap: bool = False) -> dict[str, Any]:
    """Texels the bake needs for the visual GLB, grouped as the adapter combines it.

    ``cell_m`` 0 means one renderer per node, as the adapter does without combining.
    ``scenic_out_of_lightmap`` leaves the ``scenic_*`` walls out, as ``contributes_gi`` does.
    """
    areas = lightmap_areas(visual, cell_m=cell_m, scenic_out_of_lightmap=scenic_out_of_lightmap)
    return summarise(areas, texels_per_m=texels_per_m, max_size=max_size, scales=scales)


def pages_at_least(areas: LightmapAreas, texels_per_m: float, *, max_size: int,
                   scales: Iterable[Mapping[str, Any]] = ()) -> int:
    """``summarise(...)["pages_at_least"]``: the lower-bound page count of ``areas`` at ``texels_per_m``."""
    return int(summarise(areas, texels_per_m=texels_per_m, max_size=max_size, scales=scales)["pages_at_least"])


def page_budget_density(areas: LightmapAreas, *, requested: float, pages_max: int, max_size: int,
                        scales: Iterable[Mapping[str, Any]] = ()) -> float:
    """The texel density that keeps the estimate within ``pages_max`` pages (plan C5).

    Within budget (``pages_at_least`` at ``requested`` at most ``pages_max``) the requested density comes back
    unchanged. Otherwise the result is the largest multiple of ``LIGHTMAP_DENSITY_STEP`` between
    ``LIGHTMAP_TEXELS_FLOOR`` and ``requested`` whose estimate fits, found by bisection: each renderer adds
    ``min(area × (density × scale)², page)``, which never falls as the density rises, so the page count is
    monotone in the density. The same areas in the same order always give the same density.
    The density is never raised and never goes below the floor: ``LightmapBudgetError`` when the budget cannot
    be met there.
    """
    rows = list(scales)

    def fits(density: float) -> bool:
        return pages_at_least(areas, density, max_size=max_size, scales=rows) <= pages_max

    if fits(requested):
        return requested
    low = math.ceil(LIGHTMAP_TEXELS_FLOOR / LIGHTMAP_DENSITY_STEP)
    high = math.floor(requested / LIGHTMAP_DENSITY_STEP)
    if high < low or not fits(low * LIGHTMAP_DENSITY_STEP):
        lowest = min(requested, LIGHTMAP_TEXELS_FLOOR)
        pages = pages_at_least(areas, lowest, max_size=max_size, scales=rows)
        raise LightmapBudgetError(
            f"build.lightmap_pages_max {pages_max} cannot be met: the lightmap needs at least {pages} pages at "
            f"{lowest:g} texels/m, and the density never goes below the {LIGHTMAP_TEXELS_FLOOR:g} texels/m floor (ADR-005)"
        )
    # Invariant: ``low`` fits, and no grid step above ``high`` is a candidate (it is over ``requested``).
    while low < high:
        middle = (low + high + 1) // 2  # pragma: no mutate: any middle in (low, high] gives the same result; +2 still lands there
        if fits(middle * LIGHTMAP_DENSITY_STEP):
            low = middle
        else:
            high = middle - 1
    return low * LIGHTMAP_DENSITY_STEP


def _by_material(scales: Iterable[Mapping[str, Any]]) -> dict[str, float]:
    return {str(row["material"]): float(row["scale"]) for row in scales}


def _area(vertices: np.ndarray, triangles: np.ndarray) -> float:
    a = vertices[triangles[:, 0]]
    cross = np.cross(vertices[triangles[:, 1]] - a, vertices[triangles[:, 2]] - a)
    return float(np.linalg.norm(cross, axis=1).sum() * 0.5)
