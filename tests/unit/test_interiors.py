"""Interior windows open both ways, with one glass pane. The fixture origin is MURCIA_CENTER."""

from __future__ import annotations

import itertools
import math
from dataclasses import replace

import numpy as np
import pytest
from shapely import Point, Polygon, box, unary_union

from onward_generator.detail.config import load_detail_config
from onward_generator.detail.facades import Hole, Layout, Segment, stable_unit
from onward_generator.detail.interiors import (
    GLASS_VIEW,
    SHOP_GLASS,
    Interior,
    InteriorPlan,
    _door_zone,
    _inner_walls,
    _lands_on_roof,
    assemble_interior,
    build_interior,
    flight_landings,
    glass_penetrations,
    interior_visual,
    light_anchors,
    pierce_room_windows,
    plan_interior,
    room_ceiling,
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
        plan, 18.0, footprint, cfg,
        room_window_cuts(layout, [segment]), glass_penetrations(layout, [segment]),
    )
    assert built.defects == 0
    # The window and the shopfront (shop glass) each keep a #glass pane: a shot crosses it, a body does not.
    assert [h.back for h in layout.holes] == [GLASS_VIEW, SHOP_GLASS, "glass_dark"]
    assert (built.spec["glass_panes"], built.spec["open_shopfronts"]) == (2, 1)
    assert len(built.glass_colliders) == 2
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
    # The shopfront has no concrete (a shot crosses) and one #glass pane (a body does not).
    assert not any(_inside(mesh, shop_point) for mesh in concrete)
    assert sum(_inside(mesh, shop_point) for mesh in built.glass_colliders) == 1
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
    built = build_interior(tall, 18.0, footprint, cfg)
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
    built = build_interior(plan, 18.0, footprint, cfg)
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

    built = build_interior(plan, 18.0, footprint, cfg, cuts, panes, wall_material="interior_shop_bar")
    visual = interior_visual(plan, 18.0, cfg, cuts, wall_material="interior_shop_bar")
    drawn = {name: [m.triangle_count for m in meshes] for name, meshes in visual.items()}
    assert drawn == {name: [m.triangle_count for m in meshes] for name, meshes in built.visual.items()}
    assert visual_triangles(visual) == built.visual_triangles == sum(sum(counts) for counts in drawn.values())
    # Without the window cuts the inner walls are plain quads: fewer triangles than with them.
    assert visual_triangles(interior_visual(plan, 18.0, cfg, wall_material="interior_shop_bar")) < built.visual_triangles
    assert visual_triangles({"a": [], "b": []}) == 0

    assembled = assemble_interior(plan, visual, 18.0, footprint, cfg, panes)
    assert assembled.visual is visual
    assert [(n, m.triangle_count) for n, m in assembled.colliders] == [(n, m.triangle_count) for n, m in built.colliders]
    assert assembled.spec == built.spec and assembled.roof_well is not None
    assert len(built.glass_colliders) == 2 and all(m.triangle_count == 12 for m in built.glass_colliders)
    walls_and_slabs = sum(m.triangle_count for _, m in built.colliders)
    assert built.collision_triangles == walls_and_slabs + 24
    assert assemble_interior(plan, visual, 18.0, footprint, cfg).collision_triangles < built.collision_triangles


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
    # Storey flights, then several roof flights: only the last lands on the roof.
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
    # Last, the lining of the empty storeys the climb passes, from the furnished ceiling to the roof slab.
    [lining] = walls_meshes
    furnished = room_ceiling(plan, height, slab)
    assert furnished < height - slab
    assert (lining.vertices[:, 2].min(), lining.vertices[:, 2].max()) == pytest.approx((furnished, height - slab))
    tri = lining.vertices[lining.triangles]
    inward = np.array([footprint.centroid.x, footprint.centroid.y]) - tri.mean(axis=1)[:, :2]
    assert (np.einsum("ij,ij->i", _normals(lining)[:, :2], inward) > 0).all()  # it faces into the building
    assert landed == 1 and floors == []
    # The door is cut through the room wall at its own span, from the inner face of the wall.
    [room_walls] = visual["interior_shop_bar"]
    corners = {(round(float(x), 6), round(float(y), 6), round(float(z), 6)) for x, y, z in room_walls.vertices}
    for along in (door.s0, door.s0 + door.width):
        x, y, _ = segment.point(along, door.height, cfg.interiors.wall_thickness_m)
        assert (round(float(x), 6), round(float(y), 6), round(door.height, 6)) in corners
    built = build_interior(plan, height, footprint, cfg)
    names = [n for n, _ in built.colliders]
    assert "upper_solid" not in names and "roof_slab" not in names and names.count("roof_deck") == 1


def _first_hit(meshes: list[Mesh], origin: np.ndarray, direction: np.ndarray) -> tuple[float, float] | None:
    """Distance to the nearest triangle along the ray and the cosine between its normal and the ray (Moller-Trumbore)."""
    best: tuple[float, float] | None = None
    for mesh in meshes:
        v = mesh.vertices[mesh.triangles]
        e1, e2 = v[:, 1] - v[:, 0], v[:, 2] - v[:, 0]
        p = np.cross(direction, e2)
        det = np.einsum("ij,ij->i", e1, p)
        ok = np.abs(det) > 1e-12
        inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
        t_vec = origin - v[:, 0]
        u = np.einsum("ij,ij->i", t_vec, p) * inv
        q = np.cross(t_vec, e1)
        w = (q @ direction) * inv
        dist = np.einsum("ij,ij->i", e2, q) * inv
        hit = ok & (u >= 0) & (w >= 0) & (u + w <= 1) & (dist > 1e-6)
        for index in np.flatnonzero(hit):
            if best is None or dist[index] < best[0]:
                normal = np.cross(e1[index], e2[index])
                best = (float(dist[index]), float(normal @ direction / np.linalg.norm(normal)))
    return best


def test_every_empty_storey_of_a_roof_climb_is_closed_from_inside() -> None:
    """From each storey the climb passes above the furnished rooms, a level look meets an inward face, never a culled one.

    The shell is one-sided and faces out: a ray that hits nothing of the interior, or hits a face from behind,
    would show the street through the wall in the game.
    """
    cfg, _, _, _, plan = _split_plan()
    height, slab = 18.0, cfg.interiors.slab_m
    visual = interior_visual(plan, height, cfg, wall_material="interior_shop_bar", floor_material="interior_shop_wood")
    meshes = [mesh for parts in visual.values() for mesh in parts]
    furnished = room_ceiling(plan, height, slab)
    empty = [f for f in plan.flights if f.level * plan.storey_m >= furnished - 1e-6]
    assert len(empty) >= 2
    for flight in empty:
        plate = plan.inner.difference(flight.well)
        inside = plate.representative_point()
        origin = np.array([inside.x, inside.y, flight.level * plan.storey_m + 1.6])
        for k in range(16):
            angle = 2 * math.pi * k / 16
            direction = np.array([math.cos(angle), math.sin(angle), 0.0])
            hit = _first_hit(meshes, origin, direction)
            assert hit is not None, (flight.level, k)
            assert hit[1] < 0, (flight.level, k)  # the face looks back at the ray


def test_the_lining_skips_the_window_cuts_of_the_furnished_rooms() -> None:
    """A room window lies below the lining: it cuts the room walls, and the lining stays whole on every edge."""
    cfg, footprint, segment, _, plan = _split_plan()
    height, slab, t = 18.0, cfg.interiors.slab_m, cfg.interiors.wall_thickness_m
    furnished = room_ceiling(plan, height, slab)
    window = (segment.point(3.0, depth_in=t), segment.point(5.0, depth_in=t), furnished - 2.0, furnished - 0.8)
    visual = interior_visual(plan, height, cfg, [window], wall_material="interior_shop_bar")
    lining = visual["interior_wall"][-1]
    perimeter = sum(math.dist(a, b) for a, b in itertools.pairwise(plan.inner.exterior.coords))
    v = lining.vertices[lining.triangles]
    area = float(np.linalg.norm(np.cross(v[:, 1] - v[:, 0], v[:, 2] - v[:, 0]), axis=1).sum() / 2)
    assert area == pytest.approx(perimeter * (height - slab - furnished))


def test_a_climb_whose_rooms_reach_the_roof_adds_no_lining() -> None:
    cfg, _, _, _, plan = _split_plan()
    height = plan.floors * plan.storey_m  # the furnished rooms reach the roof slab: no empty storey to line
    assert room_ceiling(plan, height, cfg.interiors.slab_m) == pytest.approx(height - cfg.interiors.slab_m)
    visual = interior_visual(plan, height, cfg, wall_material="interior_shop_bar")
    assert len(visual["interior_wall"]) == 2 * len(plan.flights)  # a soffit and a well per flight, nothing else


def test_an_interior_without_roof_access_closes_its_top_room() -> None:
    cfg, footprint, _, _, plan = _split_plan(roof_access=False)
    assert plan.flights and all(f.role == "storey" for f in plan.flights)
    visual = interior_visual(plan, 18.0, cfg, wall_material="interior_shop_bar")
    ceiling = visual["interior_wall"][0]
    assert set(ceiling.vertices[:, 2].round(9)) == {round(plan.floors * plan.storey_m, 9)} and (_normals(ceiling)[:, 2] < 0).all()
    assert len(visual["interior_wall"]) == 1 + 2 * len(plan.flights)
    names = [n for n, _ in build_interior(plan, 18.0, footprint, cfg).colliders]
    assert names.count("upper_solid") == 1 and "roof_deck" not in names


@pytest.mark.parametrize("height", [3.0, 6.0, 9.0, 12.0, 15.0, 18.0])
def test_no_flight_stands_in_the_head_room_of_the_flight_below(height: float) -> None:
    cfg = load_detail_config()
    footprint = Polygon([(0.0, 0.0), (24.0, 0.0), (24.0, 18.0), (0.0, 18.0)])
    segment = Segment(0, 0, 0, (0.0, 0.0), (24.0, 0.0), True)
    door = Hole("interior_door", 0, 11.0, 0.0, 1.2, 2.4, cfg.interiors.wall_thickness_m, None, 0, 0)
    plan = plan_interior("block-split", footprint, door, segment, height, 3.0, round(height / 3.0), cfg)
    assert not isinstance(plan, str)
    assert [f.level for f in plan.flights] == list(range(round(height / 3.0)))  # one flight per storey, to the roof
    for lower, upper in itertools.pairwise(plan.flights):
        assert not lower.well.intersects(upper.well)


def test_a_climb_of_several_storeys_needs_a_second_well() -> None:
    """A strip has room for one well: one storey climbs in it, and a taller roof stays closed rather than unclimbable."""
    narrow = Polygon([(0, 0), (10, 0), (10, 3), (0, 3)])
    cfg = load_detail_config()
    one_floor = replace(cfg, interiors=replace(cfg.interiors, floors=1))
    door = Hole("interior_door", 0, 1.0, 0.0, 1.2, 2.4, 0.3, None, 0, 0)
    segment = Segment(0, 0, 0, (0.0, 0.0), (10.0, 0.0), True)
    low = plan_interior("b", narrow, door, segment, 3.0, 3.0, 1, one_floor)
    assert isinstance(low, InteriorPlan) and [f.role for f in low.flights] == ["roof"]
    tall = plan_interior("b", narrow, door, segment, 6.0, 3.0, 2, one_floor)
    assert isinstance(tall, InteriorPlan) and tall.flights == ()
    wide = Polygon([(0, 0), (10, 0), (10, 6), (0, 6)])
    climbed = plan_interior("b", wide, door, segment, 6.0, 3.0, 2, one_floor)
    assert isinstance(climbed, InteriorPlan) and [f.role for f in climbed.flights] == ["roof", "roof"]
    assert not climbed.flights[0].well.intersects(climbed.flights[1].well)
    # The door takes the wall the second well would need.
    blocked = plan_interior("b", wide, replace(door, s0=4.0), segment, 6.0, 3.0, 2, one_floor)
    assert isinstance(blocked, InteriorPlan) and blocked.flights == ()


def test_a_climb_takes_turns_in_two_wells_and_reuses_the_last_storey_well() -> None:
    wide = Polygon([(0, 0), (24, 0), (24, 18), (0, 18)])
    plan = _plan(wide, 15.0, 3.0, 5)
    assert isinstance(plan, InteriorPlan)
    storey = [f for f in plan.flights if f.role == "storey"]
    roof = [f for f in plan.flights if f.role == "roof"]
    assert roof[1].well.equals(storey[-1].well)
    assert not roof[0].well.intersects(storey[-1].well)
    assert all(a.well.equals(b.well) for a, b in zip(roof, roof[2:], strict=False))


def test_a_player_can_fire_over_the_parapet_of_every_template() -> None:
    """A roof may get a climb at the layout stage, after the walls are built, so every template keeps the firing height (ADR-029)."""
    parapets = {t.parapet_m for t in load_detail_config().templates.values()}
    assert parapets == {0.0, 0.7}


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
    # Room for two wells: without the roof they hold the stairs of three furnished floors, with the roof they hold one of those and the climb.
    two_wells = Polygon([(0, 0), (10, 0), (10, 6), (0, 6)])
    cfg = load_detail_config()
    three = replace(cfg, seed=next(seed for seed in range(100) if stable_unit(seed, "b", "interior_floors") < 0.5))
    door = Hole("interior_door", 0, 1.0, 0.0, 1.2, 2.4, 0.3, None, 0, 0)
    segment = Segment(0, 0, 0, (0.0, 0.0), (10.0, 0.0), True)
    plan = plan_interior("b", two_wells, door, segment, 15.0, 3.0, 5, three)
    assert isinstance(plan, InteriorPlan)
    assert plan.floors == 2 and [f.role for f in plan.flights] == ["storey", "roof", "roof", "roof", "roof"]
    furnished = plan_interior("b", two_wells, door, segment, 15.0, 3.0, 5, three, roof_access=False)
    assert isinstance(furnished, InteriorPlan) and furnished.floors == 3 and [f.role for f in furnished.flights] == ["storey", "storey"]


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
    assert _plan(tiny, 9.0, 3.0, 3) == "no_room_for_stair"  # three storeys want two floors, so a stair
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


def test_no_stair_well_blocks_the_door() -> None:
    cfg = load_detail_config()
    door = Hole("interior_door", 0, 1.0, 0.0, 1.2, 2.4, 0.3, None, 0, 0)
    segment = Segment(0, 0, 0, (0.0, 0.0), (10.0, 0.0), True)
    plan = plan_interior("b", Polygon([(0, 0), (10, 0), (10, 3), (0, 3)]), door, segment, 15.0, 3.0, 5, cfg, roof_access=False)
    assert isinstance(plan, InteriorPlan) and plan.flights
    zone = _door_zone(segment, door, cfg.interiors)
    assert all(flight.well.disjoint(zone) for flight in plan.flights)


# ---- Stair landings and per-floor probes (ADR-034) -------------------------------------------------------------------


def _three_floor_plan() -> InteriorPlan:
    """24 x 18 m, 15 m high in 3 m storeys; seed 3 keeps three furnished floors and a roof climb."""
    plan = plan_interior("b", Polygon([(0, 0), (24, 0), (24, 18), (0, 18)]), Hole("interior_door", 0, 11.0, 0.0, 1.2, 2.4, 0.3, None, 0, 0),
                         Segment(0, 0, 0, (0.0, 0.0), (24.0, 0.0), True), 15.0, 3.0, 5, replace(load_detail_config(), seed=3))
    assert isinstance(plan, InteriorPlan) and plan.floors == 3 and [f.role for f in plan.flights].count("roof") == 3
    return plan


def test_flight_landings_match_the_rects_seek_flight_checked() -> None:
    assert MURCIA_CENTER == (-1.12863, 37.98407)
    plan = _three_floor_plan()
    landing = load_detail_config().interiors.stair.landing_m
    shrunk = plan.inner.buffer(-0.02)
    for flight in plan.flights:
        g = flight.geometry
        foot, arrival = flight_landings(flight, landing)
        inverse = np.linalg.inv(flight.matrix)
        for zone, (x0, x1) in ((foot, (-landing, 0.0)), (arrival, (g.run_m, g.run_m + landing))):
            assert shrunk.contains(zone)
            assert zone.area == pytest.approx(landing * g.width_m)
            assert zone.intersection(flight.well).area == pytest.approx(0.0, abs=1e-9)
            assert zone.intersection(flight.well).length == pytest.approx(g.width_m)  # one short side of the well, whole
            local = [inverse @ np.array([x, y, 0.0, 1.0]) for x, y in list(zone.exterior.coords)[:-1]]
            assert sorted({round(float(p[0]), 6) for p in local}) == [round(x0, 6), round(x1, 6)]
            assert sorted({round(float(p[1]), 6) for p in local}) == [0.0, round(g.width_m, 6)]


def _two_rooms() -> tuple[InteriorPlan, list[dict[str, object]]]:
    plan = _room_plan(10.0, 7.0)
    return replace(plan, flights=()), _rooms(2.8, 5.8)


def test_room_furniture_moves_probes_of_its_own_room_only() -> None:
    plan, rooms = _two_rooms()
    before = light_anchors(plan, rooms)
    assert light_anchors(plan, rooms, room_furniture=None) == before
    assert light_anchors(plan, rooms, room_furniture={}) == before
    assert light_anchors(plan, rooms, room_furniture={"elsewhere/L0": box(X0, Y0, X0 + 10.0, Y0 + 7.0)}) == before
    xs, ys = _grid(10.0, 7.0, 3, 2)
    piece = Point(xs[0], ys[0]).buffer(0.3)
    after = light_anchors(plan, rooms, room_furniture={"block-light/L1": piece})
    l0, l1 = after["rooms"]
    assert l0 == before["rooms"][0]
    assert (l1["light_m"], l1["range_m"]) == (before["rooms"][1]["light_m"], before["rooms"][1]["range_m"])
    assert _columns(l1["probes_m"]) == _columns(before["rooms"][1]["probes_m"]) - {(xs[0], ys[0])}


def test_furnished_floor_keeps_probes_outside_its_pieces_and_the_union_path_is_unchanged() -> None:
    plan, rooms = _two_rooms()
    xs, ys = _grid(10.0, 7.0, 3, 2)
    pieces = unary_union([Point(xs[1], y).buffer(0.4) for y in ys])
    after = light_anchors(plan, rooms, room_furniture={"block-light/L0": pieces})
    kept = _columns(after["rooms"][0]["probes_m"])
    assert kept == {(x, y) for x in (xs[0], xs[2]) for y in ys}
    assert not any(pieces.contains(Point(*c)) for c in kept)
    union = light_anchors(plan, rooms, furniture=pieces)  # ADR-031's union: every room loses those columns
    assert all(_columns(r["probes_m"]) == kept for r in union["rooms"])
    both = light_anchors(plan, rooms, furniture=pieces, room_furniture={"block-light/L1": Point(xs[0], ys[0]).buffer(0.3)})
    assert _columns(both["rooms"][1]["probes_m"]) == kept - {(xs[0], ys[0])}


def test_a_hidden_wall_window_is_never_pierced() -> None:
    """Plan P0: a hidden wall's window stays a flat dark pane with a room behind it, so the shell costs the same."""
    footprint, inner, segments = _square()
    seen, hidden = _window(4.0, 1.0), replace(_window(4.0, 1.0), plain=True)
    layout = Layout(holes=[hidden, seen])
    opened, omitted = pierce_room_windows(layout, segments, inner, footprint, 0.3, 6.0)
    assert (opened, omitted) == (1, 0)
    assert layout.holes[0] == hidden  # untouched: glass_dark, its own depth
    assert layout.holes[1].back == GLASS_VIEW


def test_a_window_at_the_accessible_ceiling_is_not_pierced_and_one_below_it_is() -> None:
    footprint, inner, segments = _square()
    ceiling = 6.0
    at, below = _window(4.0, ceiling - 1e-6), replace(_window(10.0, ceiling - 0.5), height=0.4)  # a transom under the ceiling
    layout = Layout(holes=[at, below])
    assert pierce_room_windows(layout, segments, inner, footprint, 0.3, ceiling) == (1, 0)
    assert layout.holes[0] == at and layout.holes[1].back == GLASS_VIEW
