"""See-through props collide by their bars, the gaps open (14 November 2026)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from shapely import Point, Polygon, STRtree, box, unary_union

from onward_generator.detail.modules import yaw_matrix
from onward_generator.generation.config import Material
from onward_generator.generation.gltf import Node, Primitive, build_glb
from onward_generator.generation.mesh import Mesh, merge
from onward_generator.generation.mesh import box as mesh_box
from onward_generator.overgrow.catalog import AssetSpec
from onward_generator.overgrow.dressing import Dressing, Spot, _place_props
from onward_generator.overgrow.fit import (
    OPEN_FILL_MAX,
    OPEN_PARTS_MAX,
    OPEN_ROLES,
    collider_mesh,
    face_fill,
    is_open,
    open_collider,
    prop_collider,
    welded_parts,
)
from onward_generator.overgrow.library import Prop
from onward_generator.overgrow.profiles import profile_for
from onward_generator.overgrow.zone import square_zone

BAR_M = 0.04


def _bars(count: int, gap_m: float = 0.26, height_m: float = 2.0) -> Mesh:
    """``count`` separate square bars along x, ``gap_m`` apart, like a gate without its frame."""
    step = BAR_M + gap_m
    return merge(mesh_box(i * step, i * step + BAR_M, 0.0, BAR_M, 0.0, height_m) for i in range(count))


def _plan(mesh: Mesh) -> Polygon:
    return unary_union([Polygon(t[:, :2]).buffer(0) for t in mesh.vertices[mesh.triangles] if Polygon(t[:, :2]).area > 1e-9])


def test_the_settings() -> None:
    assert OPEN_ROLES == frozenset({"fence", "gate", "fire_escape", "scaffold"})
    assert (OPEN_FILL_MAX, OPEN_PARTS_MAX) == (0.5, 64)


def test_the_front_face_fill_tells_a_crowd_barrier_from_a_jersey_block() -> None:
    assert face_fill(mesh_box(0.0, 2.0, 0.0, 0.6, 0.0, 1.0)) == pytest.approx(1.0)
    bars = _bars(5)  # 5 bars of 0.04 m over 1.24 m: 16 % of the face
    assert face_fill(bars) == pytest.approx(5 * BAR_M / (4 * 0.3 + BAR_M), rel=1e-3)
    # Along y, the face is y by z: the turned barrier reads the same.
    turned = Mesh(bars.vertices[:, [1, 0, 2]], bars.triangles[:, [0, 2, 1]])
    assert face_fill(turned) == pytest.approx(face_fill(bars))
    assert face_fill(mesh_box(0.0, 1.0, 0.0, 1.0, 0.0, 0.0)) == 1.0  # no face: counted full


def test_open_roles_and_sparse_barriers_are_open_and_the_rest_is_not() -> None:
    solid = mesh_box(0.0, 2.0, 0.0, 0.6, 0.0, 1.0)
    for role in ("fence", "gate", "fire_escape", "scaffold"):
        assert is_open(solid, role)
    assert is_open(_bars(5), "barrier") and not is_open(solid, "barrier")
    assert not is_open(_bars(5), "wreck") and not is_open(_bars(5), "bin")


def test_welded_parts_split_at_gaps_and_join_at_shared_corners() -> None:
    parts = welded_parts(_bars(3))
    assert [len(p.triangles) for p in parts] == [12, 12, 12]
    assert [round(float(p.vertices[:, 0].min()), 2) for p in parts] == [0.0, 0.3, 0.6]
    # Two boxes sharing a face's corners (to the millimetre) are one part.
    joined = merge([mesh_box(0.0, 1.0, 0.0, 1.0, 0.0, 1.0), mesh_box(1.0004, 2.0, 0.0, 1.0, 0.0, 1.0)])
    assert len(welded_parts(joined)) == 1
    apart = merge([mesh_box(0.0, 1.0, 0.0, 1.0, 0.0, 1.0), mesh_box(1.002, 2.0, 0.0, 1.0, 0.0, 1.0)])
    assert len(welded_parts(apart)) == 2


def test_an_open_collider_stops_at_the_bars_and_leaves_the_gaps() -> None:
    bars = _bars(5)
    collider = open_collider(bars)
    plan = _plan(collider)
    for i in range(5):
        assert plan.contains(Point(i * 0.3 + BAR_M / 2, BAR_M / 2))  # each bar
        if i < 4:
            assert not plan.contains(Point(i * 0.3 + 0.17, BAR_M / 2))  # the middle of each gap
    assert collider.vertices[:, 2].min() == pytest.approx(0.0) and collider.vertices[:, 2].max() == pytest.approx(2.0)
    # The whole box closed every gap.
    assert _plan(collider_mesh(bars, "gate")).contains(Point(0.17, BAR_M / 2))


def test_past_the_part_cap_the_props_own_triangles_collide() -> None:
    many = _bars(OPEN_PARTS_MAX + 1, gap_m=0.06)
    collider = open_collider(many)
    assert collider.triangle_count == many.triangle_count and np.array_equal(collider.vertices, many.vertices)
    assert collider.vertices is not many.vertices  # a copy: the prop's mesh is not shared with the collider
    capped = _bars(OPEN_PARTS_MAX, gap_m=0.06)
    assert len(welded_parts(capped)) == OPEN_PARTS_MAX
    expected = merge(collider_mesh(part, "fence") for part in welded_parts(capped))
    assert np.array_equal(open_collider(capped).vertices, expected.vertices)  # at the cap: still one prism per part


def test_prop_collider_says_which_kind_it_wrote() -> None:
    bars, car = _bars(5), mesh_box(0.0, 4.0, 0.0, 1.8, 0.0, 1.4)
    collider, see_through = prop_collider(bars, "gate")
    assert see_through and collider.triangle_count == open_collider(bars).triangle_count
    collider, see_through = prop_collider(car, "wreck")
    assert not see_through and np.array_equal(collider.vertices, collider_mesh(car, "wreck").vertices)


def _gate() -> Prop:
    """Two rows of bars 0.6 m apart: big enough to be solid (``is_solid`` wants 0.4 m on the short side)."""
    rows = _bars(5, height_m=1.2)
    mesh = merge([rows, Mesh(rows.vertices + [0.0, 0.56, 0.0], rows.triangles)])
    spec = AssetSpec("gate_0", "structures", "gate", ("curb",), "ground", "test")  # structures_edge places it at a curb
    return Prop(spec, mesh, np.zeros((mesh.vertex_count, 2), dtype=np.float32), b"")


def test_the_rules_and_an_ai_plan_mark_an_open_collider() -> None:
    from onward_generator.overgrow.realize import _emit as emit_planned

    gate = _gate()
    rules = Dressing()
    _place_props([gate], [Spot("curb", 0.0, 0.0, 0.0, "grid:0", "grid", 10.0)], STRtree([box(30.0, 30.0, 40.0, 40.0)]),
                 None, [], square_zone(80.0), profile_for(100), 48152, [], 60_000, rules, used=[], seen=set())
    planned = Dressing()
    emit_planned(gate, yaw_matrix(0.0, -6.0, 0.0, 0.0), planned, op="place", anchor_id="poi:0", crosswise=False, op_index=0)
    for result in (rules, planned):
        (collider,) = result.colliders
        assert collider.extras["open"] is True and collider.extras["role"] == "gate"
        assert collider.primitives[0].mesh.triangle_count == open_collider(gate.mesh).triangle_count
    # Spacing keeps the plan of the whole fence rule: an open gate holds others as far off as before.
    assert rules.solid_prints and rules.solid_prints[0].area == pytest.approx(
        _plan(collider_mesh(gate.mesh, "gate")).area, rel=0.05)


def _glb(path: Path, nodes: list[Node]) -> Path:
    path.write_bytes(build_glb(nodes, {"collider": Material((1.0, 1.0, 1.0, 1.0), 1.0)}))
    return path


def test_the_report_reads_an_open_collider_as_neither_cover_nor_a_sight_blocker(tmp_path: Path) -> None:
    from onward_generator.gameplay.metrics import blocks_sight, is_cover
    from onward_generator.gameplay.report import read_colliders

    wall = mesh_box(0.0, 3.0, 0.0, 0.6, 0.0, 2.0)
    path = _glb(tmp_path / "scene_collision.glb", [
        Node("overgrow/collider/prop/gate/0", (Primitive(wall, "collider"),), {"type": "collision", "open": True}),
        Node("overgrow/collider/prop/van/1", (Primitive(wall, "collider"),), {"type": "collision"}),
        Node("overgrow/collider/prop/odd/2", (Primitive(wall, "collider"),), {"type": "collision", "open": "yes"}),
    ])
    gate, odd, van = read_colliders(path)
    assert (gate.kind, odd.kind, van.kind) == ("open", "prop", "prop")
    assert not blocks_sight(gate) and not is_cover(gate)
    assert blocks_sight(van) and is_cover(van)


def test_an_open_prop_is_not_cover() -> None:
    from onward_generator.gameplay.cover import prop_reason

    def barrier(mesh: Mesh) -> Prop:
        spec = AssetSpec("rail_0", "barriers", "barrier", ("sidewalk",), "ground", "test")
        return Prop(spec, mesh, np.zeros((mesh.vertex_count, 2), dtype=np.float32), b"")

    # Two rows of bars 0.6 m deep: solid by its size, open by its face.
    rows = _bars(5, height_m=1.2)
    railing = merge([rows, Mesh(rows.vertices + [0.0, 0.56, 0.0], rows.triangles)])
    assert prop_reason(barrier(railing), "CC0-1.0") == "see_through"
    assert prop_reason(barrier(mesh_box(0.0, 1.24, 0.0, 0.6, 0.0, 1.2)), "CC0-1.0") is None  # a jersey block is cover
