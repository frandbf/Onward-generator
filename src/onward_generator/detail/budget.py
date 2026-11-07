"""Budget mode of the playable core (ADR-017 amendment, 7 November 2026).

The core stays a square around the map centre; only its half-side comes from a
triangle target. ``detail`` runs once with the largest core (the probe). Each
core building's cost, and the cost of its interior, are read from that run, and
``tiers.solve_core`` picks the half-side whose predicted core triangles come
nearest the target. ``detail`` then runs at that half-side, unless it is the
probe's, and only that run is kept. The seed is the map centre: spawns and
routes come later, from ``perf layout``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from shapely.geometry import shape

from onward_generator.detail.config import DetailConfig
from onward_generator.detail.scene import DetailedScene, detail_scene
from onward_generator.generation.scene import GeneratedScene, Issue
from onward_generator.tiers import (
    CORE,
    CORE_BUDGET_MODE,
    CORE_BUDGET_TOLERANCE,
    MIN_CORE_HALF_SIDE_M,
    CoreBudget,
    InteriorPool,
    building_costs,
    core_extent,
    core_reach,
    solve_core,
    within_tolerance,
)

# The reason ``InteriorBudget.refuse`` gives an interior its drawn triangles did not fit, with the triangles it needed.
REFUSED = re.compile(r"^triangle_budget: needs (\d+) triangles, -?\d+ left of (shops\.)?max_triangles \d+$")


def footprint_bounds(base: GeneratedScene, scene_path: str | Path) -> dict[str, tuple[float, float]]:
    """``(core_reach, core_extent)`` of every building footprint of ``base``, from the scene ``detail`` reads, in local metres."""
    scene = json.loads(Path(scene_path).expanduser().read_bytes())
    feature_of = {f["id"]: f for f in scene["features"]}
    ids = sorted({o.record["source_ids"][0] for o in base.objects if o.type == "building"})
    footprints = {bid: shape(feature_of[bid]["geometry"]) for bid in ids}
    return {bid: (core_reach(fp), core_extent(fp)) for bid, fp in footprints.items()}


def refused_interiors(issues: Iterable[Issue]) -> dict[str, dict[str, int]]:
    """Interiors the probe refused for its drawn-triangle budget, per kind: a smaller core may build them."""
    refused: dict[str, dict[str, int]] = {}
    for issue in issues:
        found = REFUSED.match(issue.message) if issue.code == "interior_not_generated" and issue.object_id else None
        if found:
            refused.setdefault("shop" if found.group(2) else "residential", {})[str(issue.object_id)] = int(found.group(1))
    return refused


def interior_pools(cfg: DetailConfig, built: Mapping[str, Mapping[str, int]], refused: Mapping[str, Mapping[str, int]],
                   extent: Mapping[str, float]) -> list[InteriorPool]:
    """The stair and shop interior pools, each with its triangle budget from ``interiors``."""
    limits = {"residential": cfg.interiors.max_triangles, "shop": cfg.interiors.shops.max_triangles}
    return [InteriorPool(limit, {bid: (extent[bid], t) for bid, t in sorted({**refused.get(kind, {}), **built.get(kind, {})}.items())})
            for kind, limit in limits.items()]


def detail_scene_budget(
    base: GeneratedScene,
    base_ref: dict[str, Any],
    heights_path: str | Path,
    scene_path: str | Path,
    cfg: DetailConfig,
    *,
    target_triangles: int,
    max_half_side_m: float,
    **options: Any,
) -> DetailedScene:
    """``detail_scene`` with the core half-side solved from ``target_triangles`` (core visual triangles of ``counts.tiers``).

    The probe runs at ``max_half_side_m``. Only the kept run's objects and issues are returned. The
    ``core`` block gains the budget fields; ``core_budget_floor`` / ``core_budget_ceiling`` (info) tell a
    target the core cannot reach, and ``core_budget_missed`` (warning) a core more than
    ``CORE_BUDGET_TOLERANCE`` off the target that no clamp explains.
    """
    probe = detail_scene(base, base_ref, heights_path, scene_path, cfg, core_half_side_m=max_half_side_m, **options)
    costs, built = building_costs((o.record, sum(p.mesh.triangle_count for p in o.visual)) for o in probe.objects)
    bounds = footprint_bounds(base, scene_path)
    pools = interior_pools(cfg, built, refused_interiors(probe.issues), {bid: b[1] for bid, b in bounds.items()})
    budget = solve_core({bid: b[0] for bid, b in bounds.items()}, costs, target_triangles, max_half_side_m=max_half_side_m,
                        interiors=pools)
    if budget.half_side_m == max_half_side_m:
        detailed = probe
    else:
        detailed = detail_scene(base, base_ref, heights_path, scene_path, cfg, core_half_side_m=budget.half_side_m, **options)
    _record(detailed, budget, target_triangles, max_half_side_m)
    return detailed


def _record(detailed: DetailedScene, budget: CoreBudget, target: int, max_half_side_m: float) -> None:
    achieved = int(detailed.stats["tiers"][CORE]["visual_triangles"])
    within = within_tolerance(achieved, target)
    assert detailed.core is not None  # a run with a core half-side always has the block
    detailed.core.update({
        "mode": CORE_BUDGET_MODE,
        "target_triangles": target,
        "max_half_side_m": max_half_side_m,
        "predicted_triangles": budget.predicted_triangles,
        "achieved_triangles": achieved,
        "tolerance": CORE_BUDGET_TOLERANCE,
        "within_tolerance": within,
    })
    h = budget.half_side_m
    if budget.clamped == "floor":
        detailed.issues.append(Issue("core_budget_floor", "info", None,
                                     f"the smallest core ({MIN_CORE_HALF_SIDE_M:g} m) is predicted at {budget.predicted_triangles} "
                                     f"core triangles, over the target {target}; core {h:g} m has {achieved}"))
    elif budget.clamped == "ceiling":
        detailed.issues.append(Issue("core_budget_ceiling", "info", None,
                                     f"the largest core ({max_half_side_m:g} m) is predicted at {budget.predicted_triangles} "
                                     f"core triangles, under the target {target}; core {h:g} m has {achieved}"))
    elif not within:
        detailed.issues.append(Issue("core_budget_missed", "warning", None,
                                     f"core {h:g} m has {achieved} core triangles, predicted {budget.predicted_triangles}, "
                                     f"more than {CORE_BUDGET_TOLERANCE:.0%} off the target {target}"))
