"""The playable core and the scenic ring around it (ADR-017).

The core is a square of half-side ``half_side_m`` around the map centre, in
local metres. A building whose footprint meets the open square is ``core``:
it keeps today's detail. Every other building is ``scenic``: a plain shell
with no openings, interior, sign or collider. Stages after ``detail`` read
the core from the ``core`` block of ``scene_detail.json``.
"""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

from shapely import box
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry

from onward_generator.generation.config import Material
from onward_generator.generation.gltf import nodes_from_glb

CORE = "core"
SCENIC = "scenic"
SHARED = "shared"
TIERS = (CORE, SCENIC, SHARED)
MIN_CORE_HALF_SIDE_M = 50.0
MAX_CORE_HALF_SIDE_M = 2000.0
# Shared wall materials of the scenic shells. Three tones, so the ring is not one colour,
# and few, so a ring cell stays a handful of draws after the adapter's combine.
SCENIC_PREFIX = "scenic_"
SCENIC_WALLS: Mapping[str, Material] = {
    "scenic_wall_light": Material((0.90, 0.88, 0.84, 1.0), 0.9),
    "scenic_wall_warm": Material((0.92, 0.82, 0.62, 1.0), 0.9),
    "scenic_wall_ochre": Material((0.90, 0.68, 0.38, 1.0), 0.9),
}
# Every scenic roof: one roof material of the detail configuration, so it bakes at the roof scale.
SCENIC_ROOF = "roof_flat_grey"


class CoreError(ValueError):
    """The core is not a square the map can play in."""


def core_square(half_side_m: float) -> Polygon:
    """The core square around the local origin. Refuses a half-side outside [50, 2000] m."""
    if not MIN_CORE_HALF_SIDE_M <= half_side_m <= MAX_CORE_HALF_SIDE_M:
        raise CoreError(f"core half-side must be between {MIN_CORE_HALF_SIDE_M:g} and {MAX_CORE_HALF_SIDE_M:g} m, got {half_side_m:g}")
    return box(-half_side_m, -half_side_m, half_side_m, half_side_m)


def core_of(manifest: Mapping[str, Any]) -> Polygon | None:
    """The core square a ``scene_detail.json`` was built with, or None for a map without a core."""
    block = manifest.get("core")
    if not block:
        return None
    return core_square(float(block["half_side_m"]))


def building_tier(footprint: BaseGeometry, core: BaseGeometry | None) -> str:
    """``core`` when the footprint shares area with the core (or there is no core), else ``scenic``."""
    if core is None or (footprint.intersects(core) and not footprint.touches(core)):
        return CORE
    return SCENIC


def scenic_wall(seed: int, building_id: str) -> str:
    """One of the scenic wall materials, by ``sha256(seed, building, "scenic")``."""
    names = sorted(SCENIC_WALLS)
    return names[int(hashlib.sha256(f"{seed}:{building_id}:scenic".encode()).hexdigest(), 16) % len(names)]


def bounds_tier(bounds: tuple[float, float, float, float], core: BaseGeometry) -> str:
    """``core`` when the bounds lie inside the core, ``scenic`` when they miss it, ``shared`` otherwise."""
    extent = box(*bounds)
    if core.covers(extent):
        return CORE
    if not extent.intersects(core) or extent.touches(core):
        return SCENIC
    return SHARED


def tier_summary(visual: bytes, objects: Iterable[Mapping[str, Any]], core: BaseGeometry, *, cell_m: float) -> dict[str, Any]:
    """Visual triangles and predicted draws per tier, after the adapter's (cell, material) combine.

    A node takes the ``tier`` of its object record; a node whose record has none
    (ground, curbs, markings, props, the backdrop) takes the tier of its bounds.
    A draw is one (cell, material) group; a group that holds two tiers counts
    once in each, so the tier draws can add up to more than ``draws``.
    """
    if not cell_m > 0:
        raise CoreError(f"cell_m must be positive, got {cell_m}")
    tier_of = {str(o["id"]): str(o["tier"]) for o in objects if o.get("tier") in TIERS}
    nodes, _ = nodes_from_glb(visual)
    triangles: Counter[str] = Counter()
    groups: dict[str, set[tuple[int, int, str]]] = {tier: set() for tier in TIERS}
    for node in nodes:
        primitives = node.world_primitives()
        lo = [min(float(p.mesh.vertices[:, k].min()) for p in primitives) for k in (0, 1)]
        hi = [max(float(p.mesh.vertices[:, k].max()) for p in primitives) for k in (0, 1)]
        name = str(node.extras.get("object_id") or node.name)
        tier = tier_of.get(name) or bounds_tier((lo[0], lo[1], hi[0], hi[1]), core)
        # The adapter's cell is on the glTF ground plane (x east, z = -north), as perf measure.
        cell = (math.floor((lo[0] + hi[0]) / 2 / cell_m), math.floor(-(lo[1] + hi[1]) / 2 / cell_m))
        for p in primitives:
            triangles[tier] += p.mesh.triangle_count
            groups[tier].add((cell[0], cell[1], p.material))
    return {
        "cell_m": cell_m,
        "draws": len(set().union(*groups.values())),
        **{tier: {"visual_triangles": triangles[tier], "draws": len(groups[tier])} for tier in TIERS},
    }
