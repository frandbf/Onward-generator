"""The Unity triangle count of a package, estimated before the bake (plan Q2)."""

from __future__ import annotations

import pytest

from onward_generator.export.triangle_budget import (
    ADAPTER_BASE_TRIANGLES,
    LIFT_TRIANGLES,
    UNITY_TRIANGLE_LIMIT,
    budget_warning,
    climb_triangles,
    ladder_triangles,
    suggested_core,
    triangle_budget,
    visual_triangles,
)
from onward_generator.generation.gltf import Material, Node, Primitive, build_glb
from onward_generator.generation.mesh import quad


def _glb(quads: int) -> bytes:
    """``quads`` unit squares, two triangles each."""
    nodes = [Node(f"n/{i}", (Primitive(quad((i, 0, 0), (i + 1, 0, 0), (i + 1, 1, 0), (i, 1, 0)), "ground"),)) for i in range(quads)]
    return build_glb(nodes, {"ground": Material((1, 1, 1, 1), 0.9)})


def test_the_constants_are_the_measured_ones() -> None:
    assert (UNITY_TRIANGLE_LIMIT, ADAPTER_BASE_TRIANGLES, LIFT_TRIANGLES) == (250_000, 12_300, 120)


def test_visual_triangles_count_every_primitive() -> None:
    assert visual_triangles(_glb(3)) == 6


@pytest.mark.parametrize(("height", "parapet", "expected"), [
    # Rue Mouffetard pkg_04: 10.59 m and 11.85 m roofs under a 0.7 m parapet.
    (10.59, 0.7, (2 + 36) * 12), (11.85, 0.7, (2 + 39) * 12),
    # A rung exactly on the span: 0.342 * 10 = 3.42 m for 3.06 m of roof, no parapet.
    (3.06, 0.0, (2 + 11) * 12), (3.07, 0.0, (2 + 12) * 12),
])
def test_a_ladder_is_two_rails_and_a_rung_every_0_342_m(height: float, parapet: float, expected: int) -> None:
    assert ladder_triangles(height, parapet) == expected


def test_climbs_add_their_ladders_and_lifts() -> None:
    layout = {"ladders": [{"height_m": 10.59, "parapet_m": 0.7}, {"height_m": 3.06}], "lifts": [{}, {}, {}]}
    assert climb_triangles(layout) == ladder_triangles(10.59, 0.7) + ladder_triangles(3.06, 0.0) + 3 * 120
    assert climb_triangles({}) == 0 and climb_triangles({"ladders": None, "lifts": None}) == 0


def test_the_estimate_is_the_glb_plus_the_adapter_plus_the_climbs() -> None:
    block = triangle_budget(_glb(2), {"lifts": [{}]}, None, None)
    assert block == {
        "estimated_unity_triangles": 4 + 12_300 + 120, "visual_triangles": 4, "adapter_triangles": 12_300,
        "climb_triangles": 120, "limit": 250_000, "within": True, "origin": "estimated",
    }
    assert budget_warning(block) is None


def test_an_estimate_on_the_limit_is_within_and_one_over_is_not(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("onward_generator.export.triangle_budget.UNITY_TRIANGLE_LIMIT", 12_304)
    assert triangle_budget(_glb(2), {}, None, None)["within"] is True
    monkeypatch.setattr("onward_generator.export.triangle_budget.UNITY_TRIANGLE_LIMIT", 12_303)
    block = triangle_budget(_glb(2), {}, None, None)
    assert block["within"] is False and block["limit"] == 12_303 and "suggested_core_radius_m" not in block


def test_over_the_limit_a_core_map_gets_a_core_that_fits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("onward_generator.export.triangle_budget.UNITY_TRIANGLE_LIMIT", 12_303)
    # 12 304 estimated, 1 of them in the core: the rest alone fills the 12 303.
    assert triangle_budget(_glb(2), {}, {"core": {"visual_triangles": 1}}, 100.0)["suggested_core_radius_m"] is None
    # All 4 GLB triangles in the core: 3 of them fit, sqrt(3 / 4) of 100 m is 86.6, rounded down to 85.
    tiers = {"core": {"visual_triangles": 4}}
    assert triangle_budget(_glb(2), {}, tiers, 100.0)["suggested_core_radius_m"] == 85.0
    monkeypatch.setattr("onward_generator.export.triangle_budget.UNITY_TRIANGLE_LIMIT", 250_000)
    assert "suggested_core_radius_m" not in triangle_budget(_glb(2), {}, tiers, 100.0)  # within: no suggestion


def test_the_suggested_core_scales_with_the_area_and_rounds_down() -> None:
    # Rue Mouffetard pkg_01: 252 179 in Unity, 223 307 of them in the 100 m core; 95 m gave 239 790.
    assert suggested_core(100.0, 223_307, 252_179) == 95.0
    assert suggested_core(100.0, 100_000, 350_000) is None  # the rest alone is over the limit
    assert suggested_core(100.0, 0, 260_000) is None
    assert suggested_core(100.0, 0, 240_000) is None  # room, but no core triangles to scale
    assert suggested_core(100.0, 1, 250_001, 250_000) is None  # exactly no room left
    assert suggested_core(100.0, 4, 250_001, 250_000) == 85.0  # 3 of 4 fit: sqrt(0.75) of 100 m
    assert suggested_core(100.0, 1, 250_000, 250_000) == 100.0  # one core triangle, and room for it
    assert suggested_core(100.0, 200_000, 260_000, 250_000) == 95.0  # sqrt(190/200) = 0.975
    assert suggested_core(60.0, 200_000, 340_000, 250_000) is None  # 60 * sqrt(110/200) = 44.5, under 50
    assert suggested_core(60.0, 200_000, 300_000, 250_000) == 50.0  # 60 * sqrt(150/200) = 51.96
    assert suggested_core(100.0, 200_000, 250_001, 250_000) == 95.0  # just over: 99.99 rounds down to 95


def test_the_warning_names_the_estimate_the_limit_and_the_core() -> None:
    block = {"within": False, "estimated_unity_triangles": 252_131, "limit": 250_000, "suggested_core_radius_m": 95.0}
    assert budget_warning(block) == (
        "triangle_budget: an estimated 252131 Unity triangles, over the 250000 limit; a core of 95 m should fit "
        "(map.json core_radius_m, then run --from detail) [unity_triangle_budget]")
    assert budget_warning({**block, "suggested_core_radius_m": None}) == (
        "triangle_budget: an estimated 252131 Unity triangles, over the 250000 limit [unity_triangle_budget]")
    assert budget_warning({k: v for k, v in block.items() if k != "suggested_core_radius_m"}).endswith("limit [unity_triangle_budget]")
