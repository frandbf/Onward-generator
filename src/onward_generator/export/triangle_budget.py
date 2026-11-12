"""The Unity triangle count of a package, estimated before the bake, against the 300 000 limit (plan Q2).

The guide's rough Quest figure is about 250 000 triangles (docs/11-onward-sdk.md). From 10 November 2026 a package had
to stay within it; on 12 November 2026 the maintainer raised the limit to 300 000, with occlusion culling on by
default (ADR-005 amendment), after Rue Mouffetard played well on the headset at 233 323. The figure only exists after the Unity scene step (``scene_report.json``), minutes
later and on the host; this module predicts it from the visual GLB and the layout, so ``export package`` can warn.

On 23 baked packages of five maps (11 November 2026) Unity counted the GLB's visual triangles plus 12 300, plus
the adapter's ladder and lift models: the SDK prefabs and the adapter's fixed pieces do not depend on the map.
Each climb model is boxes of the built-in cube, 12 triangles each (``AddClimbLook`` in the adapter): a ladder is two
rails and one rung every 0.342 m (the prefab's spacing) from 0.21 m to 0.57 m over its parapet. A lift measured 120
more triangles per pair on Barcelona Sarria night and Rue Mouffetard: two shells of four boxes, and 24 of the SDK's
own cabins.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from onward_generator.generation.gltf import scene_primitives

UNITY_TRIANGLE_LIMIT = 300_000
ADAPTER_BASE_TRIANGLES = 12_300
"""Unity's ``scene_report.json`` ``triangles`` less the GLB's visual triangles, the same on every day package measured."""
BOX_TRIANGLES = 12
LIFT_TRIANGLES = 2 * 4 * BOX_TRIANGLES + 24
"""A street shell and a roof shell, each a back wall, two side walls and a roof, and 24 the two SDK cabins add (measured)."""
RUNG_SPACING_M = 0.342
LADDER_RUNG_SPAN_M = 0.36
"""From the lowest rung at 0.21 m to the top rung 0.57 m over the parapet, beyond ``height_m + parapet_m``."""
CORE_STEP_M = 5.0
"""A suggested core is rounded down to this step."""
MIN_CORE_M = 50.0
"""The smallest playable core (``tiers.MIN_CORE_HALF_SIDE_M``)."""


def visual_triangles(visual: bytes) -> int:
    """Every triangle of the visual GLB."""
    return sum(p.mesh.triangle_count for p in scene_primitives(visual))


def ladder_triangles(height_m: float, parapet_m: float) -> int:
    """A ladder's model: two rails and its rungs, rounded up."""
    rungs = math.ceil((height_m + parapet_m + LADDER_RUNG_SPAN_M) / RUNG_SPACING_M) + 1
    return (2 + rungs) * BOX_TRIANGLES


def climb_triangles(layout: Mapping[str, Any]) -> int:
    """The adapter's models of the layout's ladders and lifts (ADR-029)."""
    ladders = sum(ladder_triangles(float(s["height_m"]), float(s.get("parapet_m", 0.0))) for s in layout.get("ladders") or [])
    return ladders + LIFT_TRIANGLES * len(layout.get("lifts") or [])


def suggested_core(half_side_m: float, core_triangles: int, estimated: int, limit: int = UNITY_TRIANGLE_LIMIT) -> float | None:
    """A core half-side that should fit ``limit``, assuming the core's triangles scale with its area.

    Rounded down to ``CORE_STEP_M``. None when even the smallest core would not fit, or the core has no triangles.
    """
    room = limit - (estimated - core_triangles)
    if core_triangles <= 0:
        return None
    if room <= 0:  # pragma: no mutate: room 0 gives a half-side of 0, under MIN_CORE_M, so < and <= both return None
        return None
    half = math.floor(half_side_m * math.sqrt(room / core_triangles) / CORE_STEP_M) * CORE_STEP_M
    return float(half) if half >= MIN_CORE_M else None


def triangle_budget(visual: bytes, layout: Mapping[str, Any], tiers: Mapping[str, Any] | None,
                    core_half_side_m: float | None) -> dict[str, Any]:
    """The manifest's ``triangle_budget`` block: the estimate, its parts and the limit, and a core that would fit
    when the estimate is over it and the map has a core."""
    glb = visual_triangles(visual)
    climbs = climb_triangles(layout)
    estimated = glb + ADAPTER_BASE_TRIANGLES + climbs
    block: dict[str, Any] = {
        "estimated_unity_triangles": estimated,
        "visual_triangles": glb,
        "adapter_triangles": ADAPTER_BASE_TRIANGLES,
        "climb_triangles": climbs,
        "limit": UNITY_TRIANGLE_LIMIT,
        "within": estimated <= UNITY_TRIANGLE_LIMIT,
        "origin": "estimated",
    }
    if not block["within"] and tiers is not None and core_half_side_m is not None:
        block["suggested_core_radius_m"] = suggested_core(core_half_side_m, int(tiers["core"]["visual_triangles"]), estimated,
                                                          UNITY_TRIANGLE_LIMIT)
    return block


def budget_warning(block: Mapping[str, Any]) -> str | None:
    """The package warning for an estimate over the limit, or None within it."""
    if block["within"]:
        return None
    core = block.get("suggested_core_radius_m")
    hint = f"; a core of {core:g} m should fit (map.json core_radius_m, then run --from detail)" if core else ""
    return (f"triangle_budget: an estimated {block['estimated_unity_triangles']} Unity triangles, over the "
            f"{block['limit']} limit{hint} [unity_triangle_budget]")


__all__ = [
    "ADAPTER_BASE_TRIANGLES", "LIFT_TRIANGLES", "UNITY_TRIANGLE_LIMIT", "budget_warning", "climb_triangles",
    "ladder_triangles", "suggested_core", "triangle_budget", "visual_triangles",
]
