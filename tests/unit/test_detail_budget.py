"""The cost model of the budget core (ADR-017 amendment, 7 November 2026): the probe's refused and built interiors."""

from __future__ import annotations

from onward_generator.detail import load_detail_config
from onward_generator.detail.budget import interior_pools, refused_interiors
from onward_generator.detail.scene import InteriorBudget
from onward_generator.generation.scene import Issue
from onward_generator.tiers import InteriorPool

CFG = load_detail_config()


def test_refused_interiors_read_the_budget_refusal() -> None:
    stairs, shops = InteriorBudget.of_stairs(CFG), InteriorBudget.of_shops(CFG)
    stairs.used["triangles"] = CFG.interiors.max_triangles - 10
    shops.used["triangles"] = CFG.interiors.shops.max_triangles + 5
    stair_reason, shop_reason = stairs.refuse("triangles", 1234), shops.refuse("triangles", 77)
    collision_reason = stairs.refuse("collision_triangles", 10 ** 9)
    assert stair_reason is not None and shop_reason is not None and collision_reason is not None
    issues = [
        Issue("interior_not_generated", "info", "osm-way-1", stair_reason),
        Issue("interior_not_generated", "info", "osm-way-2", shop_reason),
        Issue("interior_not_generated", "info", "osm-way-3", collision_reason),  # its drawn triangles are not known
        Issue("interior_not_generated", "info", "osm-way-4", "no_street_frontage"),
        Issue("budget_exceeded", "warning", "osm-way-5", stair_reason),
        Issue("interior_not_generated", "info", None, stair_reason),
        Issue("interior_not_generated", "info", "osm-way-6", stair_reason + " extra"),
    ]
    assert refused_interiors(issues) == {"residential": {"osm-way-1": 1234}, "shop": {"osm-way-2": 77}}
    assert refused_interiors([]) == {}


def test_interior_pools_take_the_budgets_of_the_configuration() -> None:
    built = {"residential": {"a": 500, "b": 300}, "shop": {"c": 40}}
    refused = {"residential": {"d": 900}, "shop": {"e": 60}}
    extent = {"a": 61.0, "b": 72.0, "c": 80.0, "d": 90.0, "e": 95.0}
    assert interior_pools(CFG, built, refused, extent) == [
        InteriorPool(CFG.interiors.max_triangles, {"a": (61.0, 500), "b": (72.0, 300), "d": (90.0, 900)}),
        InteriorPool(CFG.interiors.shops.max_triangles, {"c": (80.0, 40), "e": (95.0, 60)}),
    ]
    assert interior_pools(CFG, {}, {}, extent) == [InteriorPool(CFG.interiors.max_triangles, {}),
                                                   InteriorPool(CFG.interiors.shops.max_triangles, {})]
