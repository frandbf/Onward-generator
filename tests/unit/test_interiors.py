"""Interior windows open both ways, with one glass pane. The fixture origin is MURCIA_CENTER."""

from __future__ import annotations

import math
from dataclasses import replace

import numpy as np
import pytest
from shapely import Point, Polygon, box

from onward_generator.detail.config import load_detail_config
from onward_generator.detail.facades import Hole, Layout, Segment, stable_unit
from onward_generator.detail.interiors import (
    GLASS_VIEW,
    SHOP_GLASS,
    Interior,
    InteriorPlan,
    _inner_walls,
    _lands_on_roof,
    assemble_interior,
    build_interior,
    glass_penetrations,
    interior_visual,
    light_anchors,
    pierce_room_windows,
    plan_interior,
    room_window_cuts,
    visual_triangles,
)
from onward_generator.detail.modules import recess
from onward_generator.detail.scene import InteriorBudget, _select_interiors, interior_quota
from onward_generator.dress.atlas import decode_png_rgba
from onward_generator.generation.mesh import Mesh, closed_manifold_defects
from onward_generator.materials.openings import GLASS_ALPHA, boarded_rgba, window_png, window_rgba
from tests.conftest import MURCIA_CENTER


def _square(size: float = 20.0) -> tuple[Polygon, Polygon, list[Segment]]:
    footprint = Polygon([(0.0, 0.0), (size, 0.0), (size, size), (0.0, size)])
    inner = footprint.buffer(-0.3, join_style="mitre", mitre_limit=2.0)
    south = Segment(0, 0, 0, (0.0, 0.0), (size, 0.0), True)
    return footprint, inner, [south]


def _window(s0: float, v0: float) -> Hole:
    return Hole("window", 0, s0, v0, 1.2, 1.4, 0.15, "glass_dark", 0, 0)


def _area(mesh) -> float:
    triangles = mesh.vertices[mesh.triangles]
    return float(0.5 * np.linalg.norm(np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]), axis=1).sum())


def _inside(mesh: Mesh, p: np.ndarray) -> bool:
    """Ray-parity point-in-closed-mesh test. Same ray as the detail integration test."""
    direction = np.array([1.0, 0.1234, 0.0567])
    v = mesh.vertices[mesh.triangles]
    e1, e2 = v[:, 1] - v[:, 0], v[:, 2] - v[:, 0]
    h = np.cross(direction, e2)
    a = np.einsum("ij,ij->i", e1, h)
    ok = np.abs(a) > 1e-12
    f = np.where(ok, 1.0 / np.where(ok, a, 1.0), 0.0)
    s = p - v[:, 0]
    u = f * np.einsum("ij,ij->i", s, h)
    q = np.cross(s, e1)
    w = f * (q @ direction)
    t = f * np.einsum("ij,ij->i", e2, q)
    hits = ok & (u >= 0) & (w >= 0) & (u + w <= 1) & (t > 1e-9)
    return bool(hits.sum() % 2)


def test_see_through_window_opens_the_wall_and_keeps_a_pane() -> None:
    """A shot through the pane meets glass, not concrete, from either side. The sill and the pier stay solid."""
    assert MURCIA_CENTER == (-1.12863, 37.98407)
    cfg = load_detail_config()
    size = 24.0
    footprint = Polygon([(0.0, 0.0), (size, 0.0), (size, 18.0), (0.0, 18.0)])
    segment = Segment(0, 0, 0, (0.0, 0.0), (size, 0.0), True)
    door = Hole("interior_door", 0, 11.0, 0.0, 1.2, 2.4, cfg.interiors.wall_thickness_m, None, 0, 0)
    plan = plan_interior("block-window", footprint, door, segment, 18.0, 3.0, 6, cfg)
    assert not isinstance(plan, str), plan
    window = Hole("window", 0, 4.0, 0.9, 1.2, 1.4, 0.15, "glass_dark", 0, 0)
    shop = Hole("shopfront", 0, 18.0, 0.0, 2.4, 2.5, 0.15, "glass_dark", 1, 0)
    high = Hole("window", 0, 7.0, 16.0, 1.2, 1.4, 0.15, "glass_dark", 2, 5)
    layout = Layout(holes=[window, shop, high])
    thickness = cfg.interiors.wall_thickness_m
    ceiling = plan.floors * plan.storey_m
    opened, omitted = pierce_room_windows(layout, [segment], plan.inner, footprint, thickness, ceiling)
    assert opened == 2 and omitted == 0
    assert high.v0 >= ceiling
    built = build_interior(
        plan, 18.0, 18.0, footprint, cfg,
        room_window_cuts(layout, [segment]), glass_penetrations(layout, [segment]),
    )
    assert built.defects == 0
    # The window keeps its pane; the shopfront is shop glass, open with no pane.
    assert [h.back for h in layout.holes] == [GLASS_VIEW, SHOP_GLASS, "glass_dark"]
    assert (built.spec["glass_panes"], built.spec["open_shopfronts"]) == (1, 1)
    assert len(built.glass_colliders) == 1
    concrete = [mesh for _name, mesh in built.colliders]

    def at(s: float, z: float, depth: float) -> np.ndarray:
        return segment.point(s, z, depth)

    window_s, window_z = 4.6, 1.6
    for depth in (0.02, thickness / 2, thickness - 0.02):
        point = at(window_s, window_z, depth)
        assert not any(_inside(mesh, point) for mesh in concrete)
    centre = at(window_s, window_z, thickness / 2)
    assert sum(_inside(mesh, centre) for mesh in built.glass_colliders) == 1
    # Just outside each face of the wall: the hole is open to the street and to the room.
    # A deeper room point can meet the stair, which is a separate solid.
    outside = at(window_s, window_z, -0.04)
    just_inside = at(window_s, window_z, thickness + 0.02)
    for point in (outside, just_inside):
        assert not any(_inside(mesh, point) for mesh in concrete)
        assert not any(_inside(mesh, point) for mesh in built.glass_colliders)
    sill = at(window_s, 0.4, thickness / 2)
    pier = at(4.0 + 1.2 + 0.35, window_z, thickness / 2)
    assert any(_inside(mesh, sill) for mesh in concrete) and not any(_inside(mesh, sill) for mesh in built.glass_colliders)
    assert any(_inside(mesh, pier) for mesh in concrete) and not any(_inside(mesh, pier) for mesh in built.glass_colliders)
    shop_point = at(19.2, 1.2, thickness / 2)
    assert not any(_inside(mesh, shop_point) for mesh in [*concrete, *built.glass_colliders])
    door_point = at(11.6, 1.0, thickness / 2)
    assert not any(_inside(mesh, door_point) for mesh in [*concrete, *built.glass_colliders])
    blocked = at(7.6, 16.7, thickness / 2)
    assert any(_inside(mesh, blocked) for mesh in concrete)
    assert not any(_inside(mesh, blocked) for mesh in built.glass_colliders)


def test_room_window_is_one_pane_and_a_hole() -> None:
    assert MURCIA_CENTER == (-1.12863, 37.98407)
    footprint, inner, segments = _square()
    layout = Layout(holes=[_window(4.0, 1.0), _window(8.0, 6.2)])
    opened, omitted = pierce_room_windows(layout, segments, inner, footprint, 0.3, 6.0)
    assert (opened, omitted) == (1, 0)
    assert layout.holes[0].back == GLASS_VIEW and layout.holes[0].depth == 0.3
    assert layout.holes[1].back == "glass_dark" and layout.holes[1].depth == 0.15
    proto = recess(1.2, 1.4, 0.3, GLASS_VIEW)
    panes = [mesh for name, mesh in proto.parts if name == GLASS_VIEW]
    assert len(panes) == 1 and panes[0].triangle_count == 2 and proto.collider is None
    cuts = room_window_cuts(layout, segments)
    assert len(cuts) == 1
    wall = _inner_walls(inner, 0.0, 6.0, cuts)
    ring = list(inner.exterior.coords)
    perimeter = sum(math.dist(ring[i], ring[i + 1]) for i in range(len(ring) - 1))
    assert _area(wall) == pytest.approx(perimeter * 6.0 - 1.2 * 1.4)


def test_window_glass_is_translucent_and_the_frame_stays_opaque() -> None:
    image = window_rgba(256)
    assert image[40, 40, 3] == round(GLASS_ALPHA * 255)
    assert image[0, 0, 3] == 255 and image[128, 128, 3] == 255
    decoded = decode_png_rgba(window_png(256))
    assert decoded[40, 40, 3] == image[40, 40, 3] and decoded[0, 0, 3] == 255
    board = boarded_rgba(256)
    assert board[40, 40, 3] == 255 and board[0, 0, 3] == 255


def test_window_off_the_inner_wall_is_omitted() -> None:
    footprint, inner, _segments = _square()
    # An edge inside the footprint, not on the inner ring.
    floating = Segment(0, 0, 0, (2.0, 5.0), (18.0, 5.0), True)
    layout = Layout(holes=[_window(4.0, 1.0)])
    opened, omitted = pierce_room_windows(layout, [floating], inner, footprint, 0.3, 6.0)
    assert (opened, omitted) == (0, 1)
    assert layout.holes == []


def test_by_default_every_building_of_up_to_five_storeys_is_a_candidate() -> None:
    cfg = load_detail_config()
    assert (cfg.interiors.min_buildings, cfg.interiors.max_buildings) == (1000, 1000)
    assert (cfg.interiors.max_triangles, cfg.interiors.max_collision_triangles) == (24000, 72000)
    rule = cfg.interiors.rule
    assert (rule.min_levels, rule.max_levels, rule.min_area_m2, rule.max_area_m2, rule.min_rectangularity) == (1, 5, 20.0, 2000.0, 0.0)
    assert rule.categories == {"residential_multi", "residential_small", "unknown", "commercial", "public", "industrial", "minor"}
    assert interior_quota(cfg) == 1000
    assert {interior_quota(replace(cfg, interiors=replace(cfg.interiors, min_buildings=5, max_buildings=8), seed=seed)) for seed in range(40)} == {5, 6, 7, 8}


@pytest.mark.parametrize(
    ("used", "cost", "limit", "fits"),
    [
        (0, 8000, 8000, True),  # exactly at the budget is inside it
        (1, 8000, 8000, False),
        (0, 8001, 8000, False),
        (7000, 1000, 8000, True),
        (7000, 1001, 8000, False),
        (3000, 2000, 4999, False),  # the sum counts, not the larger term
        (0, 0, 0, True),
        (0, 1, 0, False),
    ],
)
def test_triangle_budget_admits_up_to_the_limit(used: int, cost: int, limit: int, fits: bool) -> None:
    budget = InteriorBudget("", {"triangles": limit}, {"triangles": used})
    assert (budget.refuse("triangles", cost) is None) is fits


def test_interior_visual_triangles_count_every_material_and_no_collider() -> None:
    tri = Mesh(np.zeros((3, 3)), np.array([[0, 1, 2]]))
    quad = Mesh(np.zeros((4, 3)), np.array([[0, 1, 2], [0, 2, 3]]))
    plan = _room_plan(6.0, 4.0)
    built = Interior(plan, {"interior_wall": [quad, tri], "interior_floor": [], "stair": [quad]}, [("walls_0", quad)], {})
    assert built.visual_triangles == 5
    assert Interior(plan, {}, [("walls_0", quad)], {}).visual_triangles == 0


def test_interior_quota_is_between_two_and_five() -> None:
    cfg = load_detail_config()
    assert interior_quota(cfg) == interior_quota(cfg)
    seen = {interior_quota(replace_seed(cfg, seed)) for seed in range(40)}
    assert seen == {2, 3, 4, 5}


def replace_seed(cfg, seed: int):
    from dataclasses import replace
    return replace(cfg, seed=seed, interiors=replace(cfg.interiors, min_buildings=2, max_buildings=5))


def test_seed_splits_tall_interiors_between_two_and_three_floors() -> None:
    cfg = load_detail_config()
    assert cfg.interiors.floors == 3
    size = 24.0
    footprint = Polygon([(0.0, 0.0), (size, 0.0), (size, 18.0), (0.0, 18.0)])
    segment = Segment(0, 0, 0, (0.0, 0.0), (size, 0.0), True)
    door = Hole("interior_door", 0, 11.0, 0.0, 1.2, 2.4, cfg.interiors.wall_thickness_m, None, 0, 0)
    plans: dict[int, object] = {}
    for n in range(40):
        building_id = f"block-{n}"
        plan = plan_interior(building_id, footprint, door, segment, 18.0, 3.0, 6, cfg)
        assert not isinstance(plan, str), plan
        choice = 3 if stable_unit(cfg.seed, building_id, "interior_floors") < 0.5 else 2
        assert plan.floors == choice
        assert len([flight for flight in plan.flights if flight.role == "storey"]) == choice - 1
        assert any(flight.role == "roof" for flight in plan.flights)
        plans[choice] = plan
        if len(plans) == 2:
            break
    assert set(plans) == {2, 3}
    tall = plans[3]
    built = build_interior(tall, 18.0, 18.0, footprint, cfg)
    assert built.defects == 0
    assert set(built.visual) <= {"interior_wall", "interior_floor", "stair"}
    assert all(mesh.triangle_count for meshes in built.visual.values() for mesh in meshes)
    stairs = [c for c in built.spec["connections"] if c["type"] == "stair"]
    assert [c["from"] for c in stairs[:2]] == [f"{tall.building_id}/L0", f"{tall.building_id}/L1"]
    assert stairs[-1]["to"] == "roof"
    assert built.spec["roof_access"] is True
    assert built.roof_well is not None
    assert any(name == "roof_deck" for name, _mesh in built.colliders)
    for _name, mesh in built.colliders:
        assert closed_manifold_defects(mesh) == 0


# ---- Light anchors (ADR-013) --------------------------------------------------------------
# A room off the origin so every coordinate keeps three decimals after rounding.
X0, Y0 = 100.1234, 200.5678


def _room_plan(width: float, depth: float, wells: tuple[Polygon, ...] = ()) -> InteriorPlan:
    """A plan whose inner polygon is ``width`` × ``depth`` at (X0, Y0). Only ``inner`` and the wells matter."""
    cfg = load_detail_config()
    size = 24.0
    footprint = Polygon([(0.0, 0.0), (size, 0.0), (size, 18.0), (0.0, 18.0)])
    segment = Segment(0, 0, 0, (0.0, 0.0), (size, 0.0), True)
    door = Hole("interior_door", 0, 11.0, 0.0, 1.2, 2.4, cfg.interiors.wall_thickness_m, None, 0, 0)
    template = plan_interior("block-light", footprint, door, segment, 18.0, 3.0, 6, cfg)
    assert not isinstance(template, str) and template.flights
    inner = Polygon([(X0, Y0), (X0 + width, Y0), (X0 + width, Y0 + depth), (X0, Y0 + depth)])
    flights = tuple(replace(template.flights[0], well=well) for well in wells)
    return replace(template, inner=inner, flights=flights)


def _rooms(*ceilings: float) -> list[dict[str, object]]:
    return [{"id": f"block-light/L{i}", "floor_z_m": round(i * 3.0, 3), "ceiling_z_m": c} for i, c in enumerate(ceilings)]


def _columns(probes: list[float]) -> set[tuple[float, float]]:
    return {(probes[k], probes[k + 1]) for k in range(0, len(probes), 3)}


def _grid(width: float, depth: float, nu: int, nv: int) -> tuple[list[float], list[float]]:
    return ([round(X0 + width * (i + 0.5) / nu, 3) for i in range(nu)],
            [round(Y0 + depth * (j + 0.5) / nv, 3) for j in range(nv)])


def test_light_anchor_sits_under_the_ceiling_of_each_room() -> None:
    assert MURCIA_CENTER == (-1.12863, 37.98407)
    anchors = light_anchors(_room_plan(10.0, 7.0), _rooms(2.7777, 5.6))
    assert anchors["attribute_origin"] == "generated" and anchors["method"] == "room_ceiling_centre"
    first, second = anchors["rooms"]
    assert first["room_id"] == "block-light/L0" and second["room_id"] == "block-light/L1"
    assert first["light_m"] == [round(X0 + 5.0, 3), round(Y0 + 3.5, 3), round(2.7777 - 0.3, 3)]
    assert second["light_m"][2] == 5.3
    # Half the diagonal plus one metre, so the light reaches the corners.
    assert first["range_m"] == round(math.hypot(10.0, 7.0) / 2 + 1.0, 3)
    # Columns at the centres of a 3 × 2 grid; one layer 1 m over the floor, one 0.5 m under the ceiling.
    xs, ys = _grid(10.0, 7.0, 3, 2)
    assert _columns(first["probes_m"]) == {(x, y) for x in xs for y in ys}
    assert len(first["probes_m"]) == 2 * 6 * 3
    assert sorted(set(first["probes_m"][2::3])) == [1.0, round(2.7777 - 0.5, 3)]
    assert sorted(set(second["probes_m"][2::3])) == [4.0, 5.1]


def test_light_range_is_clamped_and_small_rooms_keep_two_probes_per_axis() -> None:
    small = light_anchors(_room_plan(4.0, 3.6), _rooms(2.8))["rooms"][0]
    assert small["range_m"] == 4.0
    xs, ys = _grid(4.0, 3.6, 2, 2)
    assert _columns(small["probes_m"]) == {(x, y) for x in xs for y in ys}
    large = light_anchors(_room_plan(30.0, 20.0), _rooms(2.8))["rooms"][0]
    assert large["range_m"] == 12.0
    xs, ys = _grid(30.0, 20.0, 4, 4)
    assert _columns(large["probes_m"]) == {(x, y) for x in xs for y in ys}


def test_probes_stay_half_a_metre_off_the_walls() -> None:
    # A 1.6 m corridor: the two probe rows sit 0.4 m from each wall, so none is kept.
    assert light_anchors(_room_plan(10.0, 1.6), _rooms(2.8))["rooms"][0]["probes_m"] == []


def test_light_and_probes_keep_clear_of_the_stair_wells() -> None:
    # A well over the centroid moves the light; a well 0.53 m from a probe column keeps that column.
    centre_well = Polygon([(X0 + 4.0, Y0 + 1.0), (X0 + 6.0, Y0 + 1.0), (X0 + 6.0, Y0 + 6.0), (X0 + 4.0, Y0 + 6.0)])
    side_well = Polygon([(X0 + 6.2, Y0 + 0.5), (X0 + 7.8, Y0 + 0.5), (X0 + 7.8, Y0 + 6.5), (X0 + 6.2, Y0 + 6.5)])
    plan = _room_plan(10.0, 7.0, (centre_well, side_well))
    room = light_anchors(plan, _rooms(2.8))["rooms"][0]
    light = Point(room["light_m"][0], room["light_m"][1])
    assert plan.inner.contains(light)
    assert light.distance(centre_well) >= 0.3 - 1e-3 and light.distance(side_well) >= 0.3 - 1e-3
    xs, ys = _grid(10.0, 7.0, 3, 2)
    # The middle column falls in the centre well; the last one is 0.53 m from the side well and stays.
    assert _columns(room["probes_m"]) == {(x, y) for x in (xs[0], xs[2]) for y in ys}


def test_built_interior_carries_one_anchor_per_furnished_room() -> None:
    cfg = load_detail_config()
    footprint = Polygon([(0.0, 0.0), (24.0, 0.0), (24.0, 18.0), (0.0, 18.0)])
    segment = Segment(0, 0, 0, (0.0, 0.0), (24.0, 0.0), True)
    door = Hole("interior_door", 0, 11.0, 0.0, 1.2, 2.4, cfg.interiors.wall_thickness_m, None, 0, 0)
    plan = plan_interior("block-anchor", footprint, door, segment, 18.0, 3.0, 6, cfg)
    assert not isinstance(plan, str)
    built = build_interior(plan, 18.0, 18.0, footprint, cfg)
    assert built.light_anchors == light_anchors(plan, built.spec["rooms"])
    assert [a["room_id"] for a in built.light_anchors["rooms"]] == [r["id"] for r in built.spec["rooms"]]


def test_light_uses_the_centroid_of_an_l_shaped_room() -> None:
    # The centroid of a fat L is inside the room but is not the point shapely picks as representative.
    plan = _room_plan(10.0, 7.0)
    inner = Polygon([(X0, Y0), (X0 + 10.0, Y0), (X0 + 10.0, Y0 + 4.0), (X0 + 4.0, Y0 + 4.0), (X0 + 4.0, Y0 + 7.0), (X0, Y0 + 7.0)])
    room = light_anchors(replace(plan, inner=inner), _rooms(2.8))["rooms"][0]
    assert inner.centroid.distance(inner.representative_point()) > 0.5
    assert room["light_m"][:2] == [round(inner.centroid.x, 3), round(inner.centroid.y, 3)]


def test_the_visual_pass_is_what_the_built_interior_draws_and_its_colliders_are_counted() -> None:
    """``interior_visual`` alone gives the drawn triangles the scene budgets before it builds the shell."""
    cfg = load_detail_config()
    footprint = Polygon([(0.0, 0.0), (24.0, 0.0), (24.0, 18.0), (0.0, 18.0)])
    segment = Segment(0, 0, 0, (0.0, 0.0), (24.0, 0.0), True)
    door = Hole("interior_door", 0, 11.0, 0.0, 1.2, 2.4, cfg.interiors.wall_thickness_m, None, 0, 0)
    plan = plan_interior("block-split", footprint, door, segment, 18.0, 3.0, 6, cfg)
    assert not isinstance(plan, str) and any(f.role == "roof" for f in plan.flights)
    layout = Layout(holes=[_window(4.0, 0.9), _window(16.0, 0.9)])
    assert pierce_room_windows(layout, [segment], plan.inner, footprint, cfg.interiors.wall_thickness_m, 6.0) == (2, 0)
    cuts, panes = room_window_cuts(layout, [segment]), glass_penetrations(layout, [segment])

    built = build_interior(plan, 18.0, 18.0, footprint, cfg, cuts, panes, wall_material="interior_shop_bar")
    visual = interior_visual(plan, 18.0, cfg, cuts, wall_material="interior_shop_bar")
    drawn = {name: [m.triangle_count for m in meshes] for name, meshes in visual.items()}
    assert drawn == {name: [m.triangle_count for m in meshes] for name, meshes in built.visual.items()}
    assert visual_triangles(visual) == built.visual_triangles == sum(sum(counts) for counts in drawn.values())
    # Without the window cuts the inner walls are plain quads: fewer triangles than with them.
    assert visual_triangles(interior_visual(plan, 18.0, cfg, wall_material="interior_shop_bar")) < built.visual_triangles
    assert visual_triangles({"a": [], "b": []}) == 0

    assembled = assemble_interior(plan, visual, 18.0, 18.0, footprint, cfg, panes)
    assert assembled.visual is visual
    assert [(n, m.triangle_count) for n, m in assembled.colliders] == [(n, m.triangle_count) for n, m in built.colliders]
    assert assembled.spec == built.spec and assembled.roof_well is not None
    assert len(built.glass_colliders) == 2 and all(m.triangle_count == 12 for m in built.glass_colliders)
    walls_and_slabs = sum(m.triangle_count for _, m in built.colliders)
    assert built.collision_triangles == walls_and_slabs + 24
    assert assemble_interior(plan, visual, 18.0, 18.0, footprint, cfg).collision_triangles < built.collision_triangles


def test_interior_budgets_name_the_limit_they_refuse_on() -> None:
    cfg = load_detail_config()
    stairs, shops = InteriorBudget.of_stairs(cfg), InteriorBudget.of_shops(cfg)
    assert (stairs.prefix, stairs.limits) == ("", {"triangles": 24000, "collision_triangles": 72000})
    assert (shops.prefix, shops.limits) == ("shops.", {"triangles": 6000, "collision_triangles": 72000})
    assert stairs.used == shops.used == {"triangles": 0, "collision_triangles": 0}
    assert stairs.refuse("triangles", 24000) is None and stairs.refuse("collision_triangles", 72000) is None
    assert stairs.refuse("triangles", 24001) == "triangle_budget: needs 24001 triangles, 24000 left of max_triangles 24000"
    assert shops.refuse("collision_triangles", 72001) == (
        "triangle_budget: needs 72001 collision triangles, 72000 left of shops.max_collision_triangles 72000")
    built = Interior(InteriorPlan("b", Polygon(), 1, 3.0, _window(0, 0), Segment(0, 0, 0, (0.0, 0.0), (1.0, 0.0), True), ()),
                     {"interior_wall": [Mesh(np.zeros((3, 3)), np.array([[0, 1, 2]] * 5))]},
                     [("walls_0", Mesh(np.zeros((3, 3)), np.array([[0, 1, 2]] * 7)))], {},
                     glass_colliders=[Mesh(np.zeros((3, 3)), np.array([[0, 1, 2]] * 3))])
    assert (built.visual_triangles, built.collision_triangles) == (5, 10)
    stairs.spend(built)
    stairs.spend(built)
    assert stairs.used == {"triangles": 10, "collision_triangles": 20} and shops.used["triangles"] == 0
    assert stairs.refuse("triangles", 23991) == "triangle_budget: needs 23991 triangles, 23990 left of max_triangles 24000"
    assert stairs.refuse("collision_triangles", 71981) == (
        "triangle_budget: needs 71981 collision triangles, 71980 left of max_collision_triangles 72000")


def _normals(mesh: Mesh) -> np.ndarray:
    v = mesh.vertices[mesh.triangles]
    return np.cross(v[:, 1] - v[:, 0], v[:, 2] - v[:, 0])


def _split_plan(**kwargs: object):
    cfg = load_detail_config()
    footprint = Polygon([(0.0, 0.0), (24.0, 0.0), (24.0, 18.0), (0.0, 18.0)])
    segment = Segment(0, 0, 0, (0.0, 0.0), (24.0, 0.0), True)
    door = Hole("interior_door", 0, 11.0, 0.0, 1.2, 2.4, cfg.interiors.wall_thickness_m, None, 0, 0)
    plan = plan_interior("block-split", footprint, door, segment, 18.0, 3.0, 6, cfg, **kwargs)
    assert not isinstance(plan, str)
    return cfg, footprint, segment, door, plan


def test_interior_visual_puts_each_slab_well_and_stair_at_its_storey() -> None:
    cfg, footprint, segment, door, plan = _split_plan()
    s, slab, height = plan.storey_m, cfg.interiors.slab_m, 18.0
    roles = [f.role for f in plan.flights]
    # Storey flights, then a switchback of several roof flights: only the last lands on the roof.
    assert roles.count("storey") >= 1 and roles.count("roof") >= 2 and roles[-1] == "roof"
    matrices = [f.matrix.copy() for f in plan.flights]
    visual = interior_visual(plan, height, cfg, wall_material="interior_shop_bar", floor_material="interior_shop_wood")
    assert all(np.array_equal(f.matrix, m) for f, m in zip(plan.flights, matrices, strict=True))  # the plan is not moved
    walls_meshes, floors, stairs = list(visual["interior_wall"]), list(visual["interior_floor"]), visual["stair"]
    assert len(stairs) == len(plan.flights)
    landed = 0
    for flight, stair_mesh in zip(plan.flights, stairs, strict=True):
        z = stair_mesh.vertices[:, 2]
        assert z.min() == pytest.approx(flight.level * s) and z.max() == pytest.approx(flight.level * s + (flight.geometry.risers - 1) * flight.geometry.riser_m)
        top = (flight.level + 1) * s
        if flight is plan.flights[-1]:
            top = height
            landed += 1
        else:
            [floor] = [floors.pop(0)]
            assert set(floor.vertices[:, 2].round(9)) == {round(top, 9)} and (_normals(floor)[:, 2] > 0).all()
        soffit, well = walls_meshes.pop(0), walls_meshes.pop(0)
        assert set(soffit.vertices[:, 2].round(9)) == {round(top - slab, 9)} and (_normals(soffit)[:, 2] < 0).all()
        assert (well.vertices[:, 2].min(), well.vertices[:, 2].max()) == pytest.approx((top - slab, top))
        centre = np.array([flight.well.centroid.x, flight.well.centroid.y])
        tri = well.vertices[well.triangles]
        towards = centre - tri.mean(axis=1)[:, :2]
        assert (np.einsum("ij,ij->i", _normals(well)[:, :2], towards) > 0).all()  # the well walls face into the well
    assert landed == 1 and walls_meshes == [] and floors == []
    # The door is cut through the room wall at its own span, from the inner face of the wall.
    [room_walls] = visual["interior_shop_bar"]
    corners = {(round(float(x), 6), round(float(y), 6), round(float(z), 6)) for x, y, z in room_walls.vertices}
    for along in (door.s0, door.s0 + door.width):
        x, y, _ = segment.point(along, door.height, cfg.interiors.wall_thickness_m)
        assert (round(float(x), 6), round(float(y), 6), round(door.height, 6)) in corners
    built = build_interior(plan, height, height, footprint, cfg)
    names = [n for n, _ in built.colliders]
    assert "upper_solid" not in names and "roof_slab" not in names and names.count("roof_deck") == 1


def test_an_interior_without_roof_access_closes_its_top_room() -> None:
    cfg, footprint, _, _, plan = _split_plan(roof_access=False)
    assert plan.flights and all(f.role == "storey" for f in plan.flights)
    visual = interior_visual(plan, 18.0, cfg, wall_material="interior_shop_bar")
    ceiling = visual["interior_wall"][0]
    assert set(ceiling.vertices[:, 2].round(9)) == {round(plan.floors * plan.storey_m, 9)} and (_normals(ceiling)[:, 2] < 0).all()
    assert len(visual["interior_wall"]) == 1 + 2 * len(plan.flights)
    names = [n for n, _ in build_interior(plan, 18.0, 18.0, footprint, cfg).colliders]
    assert names.count("upper_solid") == 1 and "roof_deck" not in names


def test_only_a_roof_flight_within_15_cm_of_the_roof_lands_on_it() -> None:
    _, _, _, _, plan = _split_plan()
    roof = next(f for f in plan.flights if f.role == "roof")
    storey = next(f for f in plan.flights if f.role == "storey")
    assert _lands_on_roof(roof, 0.0, 0.15) and _lands_on_roof(roof, 0.15, 0.0)
    assert not _lands_on_roof(roof, 0.0, 0.1501) and not _lands_on_roof(roof, 0.0, 1.0)
    assert not _lands_on_roof(storey, 3.0, 3.0)


def test_an_included_building_that_also_matches_the_rule_keeps_the_first_place() -> None:
    # Seed 3 sorts osm-way-b before osm-way-a, so a repeated osm-way-a at the end would lose its include_ids priority.
    base = load_detail_config()
    cfg = replace(base, seed=3, interiors=replace(base.interiors, min_buildings=1, max_buildings=1, include_ids=("osm-way-a",)))
    room = Polygon([(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)])
    heights = {"category": "residential_multi", "estimated_levels": 3}
    candidates = [("osm-way-a", 3, heights, room), ("osm-way-b", 3, heights, room)]
    assert _select_interiors(candidates, cfg, []) == ["osm-way-a", "osm-way-b"]
    excluded = replace(cfg, interiors=replace(cfg.interiors, exclude_ids=frozenset({"osm-way-a"})))
    assert _select_interiors(candidates, excluded, []) == ["osm-way-b"]


def _plan(footprint: Polygon, height: float, storey: float, levels: int, **options: object) -> InteriorPlan | str:
    door = Hole("interior_door", 0, 1.0, 0.0, 1.2, 2.4, 0.3, None, 0, 0)
    return plan_interior("b", footprint, door, Segment(0, 0, 0, (0.0, 0.0), (footprint.bounds[2], 0.0), True), height, storey, levels,
                         load_detail_config(), **options)


def test_the_roof_comes_before_the_furnished_floors() -> None:
    # Room for one well: without the roof it holds the stair between two furnished floors, with the roof it holds the climb.
    narrow = Polygon([(0, 0), (10, 0), (10, 3), (0, 3)])
    plan = _plan(narrow, 15.0, 3.0, 5)
    assert isinstance(plan, InteriorPlan)
    assert plan.floors == 1 and [f.role for f in plan.flights] == ["roof"] * 5
    furnished = _plan(narrow, 15.0, 3.0, 5, roof_access=False)
    assert isinstance(furnished, InteriorPlan) and furnished.floors >= 2 and {f.role for f in furnished.flights} == {"storey"}


def test_without_roof_access_the_furnished_floors_stay() -> None:
    wide = Polygon([(0, 0), (24, 0), (24, 18), (0, 18)])
    plan = _plan(wide, 15.0, 3.0, 5, roof_access=False)
    assert isinstance(plan, InteriorPlan) and plan.flights and {f.role for f in plan.flights} == {"storey"}


def test_a_storey_that_does_not_divide_the_height_has_no_climb() -> None:
    wide = Polygon([(0, 0), (24, 0), (24, 18), (0, 18)])
    plan = _plan(wide, 16.0, 3.0, 4)
    assert isinstance(plan, InteriorPlan) and all(f.role == "storey" for f in plan.flights)


def test_a_tall_building_with_room_for_no_stair_is_refused_and_a_low_one_keeps_its_room() -> None:
    tiny = Polygon([(0, 0), (7, 0), (7, 5), (0, 5)])
    assert _plan(tiny, 15.0, 3.0, 5) == "no_room_for_stair"
    low = _plan(tiny, 3.0, 3.0, 1)
    assert isinstance(low, InteriorPlan) and (low.floors, low.flights) == (1, ())


@pytest.mark.parametrize(
    ("levels", "category", "area", "landmark", "taken"),
    [
        (1, "minor", 100.0, False, True),
        (5, "residential_multi", 100.0, False, True),
        (6, "residential_multi", 100.0, False, False),
        (3, "religious", 100.0, False, False),
        (3, "public", 100.0, True, False),
        (3, "public", 19.9, False, False),
        (3, "public", 20.0, False, True),
        (3, "industrial", 2000.0, False, True),
        (3, "industrial", 2000.1, False, False),
    ],
)
def test_the_default_rule_reads_the_drawn_storeys_category_and_area(levels: int, category: str, area: float, landmark: bool, taken: bool) -> None:
    depth = area / 10.0  # a 10 m wide rectangle has exactly this area, where a square root would round past the limit
    room = Polygon([(0.0, 0.0), (10.0, 0.0), (10.0, depth), (0.0, depth)])
    candidates = [("b", levels, {"category": category, "landmark": landmark, "estimated_levels": 99}, room)]
    assert _select_interiors(candidates, load_detail_config(), []) == (["b"] if taken else [])


def test_the_rule_takes_a_courtyard_but_not_two_separate_parts() -> None:
    cfg = load_detail_config()
    heights = {"category": "unknown"}
    ring = Polygon([(0, 0), (20, 0), (20, 20), (0, 20)], [[(8, 8), (12, 8), (12, 12), (8, 12)]])
    parts = box(0, 0, 10, 10).union(box(20, 0, 30, 10))
    assert _select_interiors([("ring", 3, heights, ring), ("parts", 3, heights, parts)], cfg, []) == ["ring"]
