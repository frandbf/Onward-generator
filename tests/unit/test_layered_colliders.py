"""Solid props collide by 30 cm slices of their own shape, not by their whole box (ADR-042, 15 November 2026)."""

from __future__ import annotations

import numpy as np
import pytest
from shapely import Point

from onward_generator.generation.mesh import Mesh, merge
from onward_generator.generation.mesh import box as mesh_box
from onward_generator.overgrow.fit import (
    LAYER_M,
    LAYER_PAD_M,
    LAYER_SIMPLIFY_M,
    collider_mesh,
    layer_plans,
    layered_collider,
    solid_collider,
)


def _covered(plans: list, x: float, y: float, z: float) -> bool:
    return any(z0 <= z <= z1 and plan.contains(Point(x, y)) for plan, z0, z1 in plans)


def _car() -> Mesh:
    """Four 0.3 m wheels, a body from 0.3 to 0.9 m and a cabin to 1.4 m over its middle."""
    wheels = [mesh_box(x, x + 0.6, y, y + 0.3, 0.0, 0.3) for x in (0.3, 3.1) for y in (0.0, 1.5)]
    return merge([*wheels, mesh_box(0.0, 4.0, 0.0, 1.8, 0.3, 0.9), mesh_box(1.0, 3.0, 0.1, 1.7, 0.9, 1.4)])


def test_the_settings() -> None:
    assert (LAYER_M, LAYER_PAD_M, LAYER_SIMPLIFY_M) == (0.3, 0.01, 0.02)


def test_a_box_is_one_slice_its_own_size() -> None:
    plans = layer_plans(mesh_box(0.0, 4.0, 0.0, 1.8, 0.0, 1.4))
    assert len(plans) == 1
    plan, z0, z1 = plans[0]
    assert (z0, z1) == (0.0, 1.4)
    assert plan.bounds == pytest.approx((-0.01, -0.01, 4.01, 1.81))
    assert plan.area == pytest.approx(4.02 * 1.82, rel=1e-2)  # the simplification may cut a 1 cm corner
    assert layered_collider(mesh_box(0.0, 4.0, 0.0, 1.8, 0.0, 1.4)).triangle_count == 12


def test_a_car_collides_by_its_wheels_body_and_cabin() -> None:
    plans = layer_plans(_car())
    assert plans[0][1] == 0.0 and plans[-1][2] == pytest.approx(1.4)
    assert _covered(plans, 2.0, 0.9, 1.2)  # in the cabin
    assert not _covered(plans, 0.5, 0.9, 1.2)  # over the bonnet: air, a shot passes
    assert _covered(plans, 0.5, 0.9, 0.6)  # the bonnet itself
    assert not _covered(plans, 2.0, 0.9, 0.1)  # under the body, between the wheels
    assert _covered(plans, 0.6, 0.15, 0.1)  # a wheel
    # The box stopped all of them.
    whole = collider_mesh(_car(), "wreck").vertices
    assert whole.min(axis=0).tolist() == [0.0, 0.0, 0.0] and whole.max(axis=0).tolist() == [4.0, 1.8, 1.4]


def test_the_slices_are_equal_and_no_taller_than_the_limit() -> None:
    # 1.4 m in five slices of 0.28 m (merged ones span several); a 0.9 m prop in exactly three of 0.3 m.
    edges = {round(z, 9) for _, z0, z1 in layer_plans(_car()) for z in (z0, z1)}
    assert edges <= {round(k * 0.28, 9) for k in range(6)} and round(0.28, 9) in edges
    upright = Mesh(np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 0.9]]), np.array([[0, 1, 2]]))
    assert [(round(z0, 6), round(z1, 6)) for _, z0, z1 in layer_plans(upright)] == [(0.0, 0.3), (0.3, 0.6), (0.6, 0.9)]


def test_an_upright_triangle_is_cut_at_each_slice() -> None:
    # A wall seen edge-on has no area in plan: each piece is widened by LAYER_PAD_M, its length what the slice holds.
    upright = Mesh(np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 0.9]]), np.array([[0, 1, 2]]))
    widths = [plan.bounds[2] for plan, _, _ in layer_plans(upright)]
    assert widths == pytest.approx([1.0 + LAYER_PAD_M, 1.0 - 0.3 / 0.9 + LAYER_PAD_M, 1.0 - 0.6 / 0.9 + LAYER_PAD_M], abs=1e-6)
    assert all(plan.bounds[1] == pytest.approx(-LAYER_PAD_M) for plan, _, _ in layer_plans(upright))


def test_the_air_between_a_body_and_its_blades_stays_open() -> None:
    body = mesh_box(0.0, 4.0, -1.0, 1.0, 0.0, 1.5)
    blades = mesh_box(-6.0, 10.0, -0.1, 0.1, 2.5, 2.55)
    plans = layer_plans(merge([body, blades]))
    assert _covered(plans, 2.0, 0.0, 1.0) and _covered(plans, -5.0, 0.0, 2.52)
    assert not _covered(plans, 2.0, 0.0, 2.1)  # between the roof and the blades
    assert not _covered(plans, -5.0, 0.0, 1.0)  # under a blade tip
    collider = layered_collider(merge([body, blades]))
    middle = collider.vertices[:, 2]
    assert not np.any((middle > 1.8) & (middle < 2.2))


def test_a_ring_is_filled() -> None:
    walls = merge([mesh_box(0.0, 2.0, 0.0, 0.1, 0.0, 1.0), mesh_box(0.0, 2.0, 1.9, 2.0, 0.0, 1.0),
                   mesh_box(0.0, 0.1, 0.0, 2.0, 0.0, 1.0), mesh_box(1.9, 2.0, 0.0, 2.0, 0.0, 1.0)])
    plans = layer_plans(walls)
    assert len(plans) == 1 and _covered(plans, 1.0, 1.0, 0.5)
    plan = plans[0][0]
    assert plan.geom_type == "Polygon" and not plan.interiors


def test_stacked_slices_merge_only_when_their_plans_match() -> None:
    # Two equal boxes stacked: one slice; the upper one 0.2 m wider, more than LAYER_SIMPLIFY_M: two.
    lower = mesh_box(0.0, 1.0, 0.0, 1.0, 0.0, 0.3)
    same = merge([lower, mesh_box(0.0, 1.0, 0.0, 1.0, 0.3, 0.6)])
    assert len(layer_plans(same)) == 1
    wider = merge([lower, mesh_box(0.0, 1.2, 0.0, 1.0, 0.3, 0.6)])
    plans = layer_plans(wider)
    assert len(plans) == 2
    # The wider box's bottom face lies on the boundary: it counts above it only, the lower slice keeps 1 m.
    assert plans[0][0].bounds[2] == pytest.approx(1.0 + LAYER_PAD_M) and plans[1][0].bounds[2] == pytest.approx(1.2 + LAYER_PAD_M)


def test_a_slice_no_triangle_reaches_is_not_extruded() -> None:
    # Two plates 1.2 m apart: the slices between them hold no triangle.
    plates = merge([mesh_box(0.0, 1.0, 0.0, 1.0, 0.0, 0.01), mesh_box(0.0, 1.0, 0.0, 1.0, 1.19, 1.2)])
    plans = layer_plans(plates)
    assert len(plans) == 2 and plans[0][2] == pytest.approx(0.3) and plans[1][1] == pytest.approx(0.9)


def test_a_flat_prop_keeps_its_box() -> None:
    flat = mesh_box(0.0, 1.0, 0.0, 1.0, 0.0, 0.0)
    assert np.array_equal(layered_collider(flat).vertices, collider_mesh(flat, "box").vertices)


@pytest.mark.parametrize("role", ["tree", "lamp"])
def test_a_tree_and_a_lamp_keep_their_trunk_and_post(role: str) -> None:
    mesh = merge([mesh_box(-0.1, 0.1, -0.1, 0.1, 0.0, 3.0), mesh_box(-1.5, 1.5, -1.5, 1.5, 2.5, 4.0)])
    assert np.array_equal(solid_collider(mesh, role).vertices, collider_mesh(mesh, role).vertices)


def test_any_other_solid_role_is_layered() -> None:
    assert np.array_equal(solid_collider(_car(), "wreck").vertices, layered_collider(_car()).vertices)
    assert np.array_equal(solid_collider(_car(), "barrel").vertices, layered_collider(_car()).vertices)
