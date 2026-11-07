"""The playable core and the scenic ring (ADR-017).

Local coordinates are metres from MURCIA_CENTER (x east, y north, z up).
"""

from __future__ import annotations

import pytest
from shapely import Point, box
from shapely.geometry import Polygon

from onward_generator.generation.config import Material
from onward_generator.generation.gltf import Node, Primitive, build_glb
from onward_generator.generation.mesh import Mesh, quad
from onward_generator.tiers import (
    CORE,
    CORE_BUDGET_MODE,
    CORE_BUDGET_TOLERANCE,
    MAX_TARGET_TRIANGLES,
    MIN_TARGET_TRIANGLES,
    SCENIC,
    SCENIC_WALLS,
    SHARED,
    CoreBudget,
    CoreError,
    InteriorPool,
    bounds_tier,
    building_costs,
    building_tier,
    check_target,
    core_extent,
    core_of,
    core_reach,
    core_square,
    scenic_wall,
    solve_core,
    tier_summary,
    view_draws,
    within_tolerance,
)

CORE_100 = box(-100, -100, 100, 100)


def _floor(x0: float, y0: float, x1: float, y1: float) -> Mesh:
    return quad((x0, y0, 0.0), (x1, y0, 0.0), (x1, y1, 0.0), (x0, y1, 0.0))


def test_core_square_is_centred_on_the_origin() -> None:
    assert core_square(150).bounds == (-150, -150, 150, 150)
    assert core_square(50).area == 100 * 100
    assert core_square(2000).bounds[2] == 2000


@pytest.mark.parametrize("half", [49.99, 2000.01, 0, -150])
def test_core_square_refuses_a_half_side_out_of_range(half: float) -> None:
    with pytest.raises(CoreError, match="between 50 and 2000 m"):
        core_square(half)


def test_core_of_reads_the_detail_manifest() -> None:
    assert core_of({"core": {"half_side_m": 120.0}}).equals(box(-120, -120, 120, 120))
    assert core_of({"core": None}) is None
    assert core_of({}) is None


@pytest.mark.parametrize(("footprint", "tier"), [
    (box(10, 10, 20, 20), CORE),
    (box(95, 10, 110, 20), CORE),       # crosses the edge
    (box(100, 10, 110, 20), SCENIC),    # only touches the edge
    (box(120, 10, 130, 20), SCENIC),
])
def test_building_tier(footprint: object, tier: str) -> None:
    assert building_tier(footprint, CORE_100) == tier  # type: ignore[arg-type]


def test_every_building_is_core_without_a_core() -> None:
    assert building_tier(box(500, 500, 510, 510), None) == CORE


def test_scenic_wall_is_seeded_per_building() -> None:
    picks = {scenic_wall(48152, f"osm-way-{n}") for n in range(60)}
    assert picks == set(SCENIC_WALLS)
    assert scenic_wall(48152, "osm-way-1") == scenic_wall(48152, "osm-way-1")
    assert [scenic_wall(48152, f"osm-way-{n}") for n in range(4)] == [
        "scenic_wall_warm", "scenic_wall_light", "scenic_wall_warm", "scenic_wall_ochre",
    ]
    assert [scenic_wall(7, f"osm-way-{n}") for n in range(4)] != [scenic_wall(48152, f"osm-way-{n}") for n in range(4)]


@pytest.mark.parametrize(("bounds", "tier"), [
    ((-10.0, -10.0, 10.0, 10.0), CORE),
    ((90.0, 0.0, 100.0, 10.0), CORE),        # on the edge, still inside
    ((100.0, 0.0, 120.0, 10.0), SCENIC),     # touches from outside
    ((150.0, 0.0, 160.0, 10.0), SCENIC),
    ((-300.0, -300.0, 300.0, 300.0), SHARED),
    ((90.0, 0.0, 110.0, 10.0), SHARED),
])
def test_bounds_tier(bounds: tuple[float, float, float, float], tier: str) -> None:
    assert bounds_tier(bounds, CORE_100) == tier


def _glb(*nodes: tuple[str, str, Mesh]) -> bytes:
    built = [Node(f"node/{k}", (Primitive(mesh, material),), {"object_id": name}) for k, (name, material, mesh) in enumerate(nodes)]
    return build_glb(built, {material: Material((1, 1, 1, 1), 0.9) for _, material, _ in nodes})


def test_tier_summary_counts_triangles_and_draws_per_tier() -> None:
    visual = _glb(
        ("building/a", "plaster", _floor(10, 10, 20, 20)),           # record: core
        ("building/b", "scenic_wall_light", _floor(110, 10, 120, 20)),  # record: scenic
        ("building/c", "scenic_wall_light", _floor(130, 10, 140, 20)),  # record: scenic, same cell as b
        ("surface/ground", "ground", _floor(-300, -300, 300, 300)),   # no record tier: crosses the edge
        ("urban/bench", "urban_metal", _floor(1, 1, 2, 2)),           # no record tier: inside
        ("surface/far", "ground", _floor(250, 250, 260, 260)),        # no record tier: outside
    )
    objects = [{"id": "building/a", "tier": CORE}, {"id": "building/b", "tier": SCENIC},
               {"id": "building/c", "tier": SCENIC}, {"id": "surface/ground", "type": "surface"}]
    summary = tier_summary(visual, objects, CORE_100, cell_m=100.0)
    assert summary == {
        "cell_m": 100.0,
        "draws": 5,
        CORE: {"visual_triangles": 4, "draws": 2},
        SCENIC: {"visual_triangles": 6, "draws": 2},
        SHARED: {"visual_triangles": 2, "draws": 1},
    }


def test_tier_summary_puts_nodes_in_the_adapters_cells() -> None:
    # Centres at y = 50 and y = 100 share the glTF cell (z = -north): floor(-0.5) and floor(-1.0) are both -1.
    same = _glb(("a", "ground", _floor(0, 40, 10, 60)), ("b", "ground", _floor(0, 90, 10, 110)))
    assert tier_summary(same, [], box(-500, -500, 500, 500), cell_m=100.0)["draws"] == 1
    # Centres at x = 50 and x = 100 do not: floor(0.5) is 0, floor(1.0) is 1.
    apart = _glb(("a", "ground", _floor(40, 0, 60, 10)), ("b", "ground", _floor(90, 0, 110, 10)))
    assert tier_summary(apart, [], box(-500, -500, 500, 500), cell_m=100.0)["draws"] == 2
    # A node goes to the cell of its bounds centre, not of its corner.
    wide = _glb(("a", "ground", _floor(0, 0, 10, 10)), ("b", "ground", _floor(-20, 0, 60, 10)))
    assert tier_summary(wide, [], box(-500, -500, 500, 500), cell_m=100.0)["draws"] == 1
    assert tier_summary(wide, [], box(-500, -500, 500, 500), cell_m=15.0)["draws"] == 2


def test_tier_summary_ignores_an_unknown_tier_and_refuses_no_cell() -> None:
    visual = _glb(("building/a", "plaster", _floor(150, 0, 160, 10)))
    assert tier_summary(visual, [{"id": "building/a", "tier": "outer"}], CORE_100, cell_m=100.0)[SCENIC]["visual_triangles"] == 2
    with pytest.raises(CoreError, match="cell_m must be positive"):
        tier_summary(visual, [], CORE_100, cell_m=0.0)


def _wall(x0: float, y0: float, x1: float, y1: float, height: float) -> Mesh:
    return quad((x0, y0, 0.0), (x1, y1, 0.0), (x1, y1, height), (x0, y0, height))


def test_a_record_tier_wins_over_the_bounds() -> None:
    # A core building that crosses the edge, and a scenic one whose bounds lie inside the core.
    visual = _glb(("building/edge", "plaster", _floor(90, 0, 120, 10)), ("building/odd", "scenic_wall_light", _floor(0, 0, 10, 10)))
    objects = [{"id": "building/edge", "tier": CORE}, {"id": "building/odd", "tier": SCENIC}]
    summary = tier_summary(visual, objects, CORE_100, cell_m=100.0)
    assert (summary[CORE]["visual_triangles"], summary[SCENIC]["visual_triangles"], summary[SHARED]["visual_triangles"]) == (2, 2, 0)
    # Without the records the bounds decide.
    plain = tier_summary(visual, [{"id": "building/edge"}, {"id": "building/odd", "tier": "outer"}], CORE_100, cell_m=100.0)
    assert (plain[CORE]["visual_triangles"], plain[SHARED]["visual_triangles"]) == (2, 2)


def test_tier_summary_reads_the_ground_plane_not_the_height() -> None:
    # A 300 m tall wall at x 10 stands inside the core; its height is not a ground extent.
    visual = _glb(("tower", "plaster", _wall(10, 10, 20, 10, 300.0)))
    assert tier_summary(visual, [], CORE_100, cell_m=100.0)[CORE]["visual_triangles"] == 2


def test_tier_summary_uses_the_centre_of_the_north_extent() -> None:
    # Centres at y = 60 and 140: glTF cells floor(-0.6) = -1 and floor(-1.4) = -2.
    visual = _glb(("a", "ground", _floor(0, 55, 10, 65)), ("b", "ground", _floor(0, 135, 10, 145)))
    assert tier_summary(visual, [], box(-500, -500, 500, 500), cell_m=100.0)["draws"] == 2
    # Two nodes of one height far apart in y are two cells.
    visual = _glb(("a", "ground", _floor(0, 0, 10, 10)), ("b", "ground", _floor(0, 300, 10, 310)))
    assert tier_summary(visual, [], box(-500, -500, 500, 500), cell_m=100.0)["draws"] == 2


def test_tier_summary_accepts_a_cell_under_a_metre() -> None:
    visual = _glb(("a", "ground", _floor(0, 0, 0.2, 0.2)), ("b", "ground", _floor(0.6, 0, 0.8, 0.2)))
    assert tier_summary(visual, [], CORE_100, cell_m=0.5)["draws"] == 2


ONE_CAMERA = box(-1, -1, 1, 1)  # with step 10 m the grid has its one point at (-1, -1)


def test_view_draws_counts_the_groups_in_front_of_the_camera() -> None:
    visual = _glb(("east", "plaster", _floor(50, -5, 60, 5)))
    seen = view_draws(visual, ONE_CAMERA, cell_m=100.0, step_m=10.0, yaws=4)
    # Headings 0 (east), 90, 180, 270: only the first sees the group.
    assert {k: seen[k] for k in ("views", "median", "p90", "max", "worst")} == {
        "views": 4, "median": 0, "p90": 1, "max": 1, "worst": {"x_m": -1.0, "y_m": -1.0, "yaw_deg": 0.0}}
    assert (seen["cell_m"], seen["fov_deg"], seen["far_m"], seen["step_m"], seen["yaws"]) == (100.0, 110.0, 1000.0, 10.0, 4)
    north = view_draws(_glb(("north", "plaster", _floor(5, 50, 15, 60))), box(10, 0, 11, 1), cell_m=100.0, step_m=10.0, yaws=4)
    assert north["worst"] == {"x_m": 10.0, "y_m": 0.0, "yaw_deg": 90.0}


def test_view_draws_stops_at_the_far_clip_and_at_the_edge_of_the_field() -> None:
    far = _glb(("far", "plaster", _floor(1500, -5, 1510, 5)))
    assert view_draws(far, ONE_CAMERA, cell_m=100.0, step_m=10.0, yaws=4)["max"] == 0
    assert view_draws(far, ONE_CAMERA, cell_m=100.0, step_m=10.0, yaws=4, far_m=2000.0)["max"] == 1
    # 60 degrees off the heading: outside a 110 degree field, inside a 130 degree one.
    side = _glb(("side", "plaster", _floor(49, 85, 51, 87)))
    assert view_draws(side, box(0, 0, 0.5, 0.5), cell_m=100.0, step_m=10.0, yaws=1)["max"] == 0
    assert view_draws(side, box(0, 0, 0.5, 0.5), cell_m=100.0, step_m=10.0, yaws=1, fov_deg=130.0)["max"] == 1


def test_view_draws_counts_one_draw_per_cell_and_material() -> None:
    visual = _glb(
        ("a", "plaster", _floor(20, -5, 30, 5)), ("b", "plaster", _floor(40, -5, 50, 5)),  # one group: same cell, same material
        ("c", "brick", _floor(20, 10, 30, 20)),                                              # another material
        ("d", "plaster", _floor(120, -5, 130, 5)),                                           # the next cell
    )
    assert view_draws(visual, ONE_CAMERA, cell_m=100.0, step_m=10.0, yaws=1)["max"] == 3


def test_view_draws_puts_cameras_on_a_grid_inside_the_area() -> None:
    visual = _glb(("a", "plaster", _floor(0, 0, 1, 1)))
    assert view_draws(visual, box(-50, -50, 50, 50), cell_m=100.0)["views"] == 5 * 5 * 16
    # The column x = 25 starts outside (25, 0) and goes on inside (25, 25), (25, 50).
    triangle = Polygon([(0, 0), (50, 50), (0, 50)])
    assert view_draws(visual, triangle, cell_m=100.0, yaws=1)["views"] == 6  # (0,0) (0,25) (0,50) (25,25) (25,50) (50,50)
    assert view_draws(visual, ONE_CAMERA, cell_m=0.5, step_m=10.0, yaws=1)["views"] == 1  # any positive cell
    assert view_draws(visual, box(0, 0, 0.9, 0.4), cell_m=100.0, step_m=0.5, yaws=1)["views"] == 2  # and any positive step


def test_view_draws_reports_the_median_and_p90_of_the_views() -> None:
    # North-east of the camera: seen at headings 0 and 90, not at 180 and 270, so the views are 0, 0, 1, 1.
    diagonal = _glb(("ne", "plaster", _floor(40, 40, 50, 50)))
    seen = view_draws(diagonal, ONE_CAMERA, cell_m=100.0, step_m=10.0, yaws=4)
    assert (seen["median"], seen["p90"], seen["max"]) == (1, 1, 1)


def test_view_draws_names_the_first_busiest_view_in_grid_order() -> None:
    wide = _glb(("east", "plaster", _floor(200, -50, 210, 50)))
    # Both cameras see the group at heading 0 only: the tie goes to the west one.
    assert view_draws(wide, box(0, 0, 10, 0.5), cell_m=1000.0, step_m=10.0, yaws=4)["worst"] == {"x_m": 0.0, "y_m": 0.0, "yaw_deg": 0.0}


@pytest.mark.parametrize(("options", "message"), [
    ({"cell_m": 0.0}, "positive"), ({"cell_m": 100.0, "step_m": 0.0}, "positive"), ({"cell_m": 100.0, "yaws": 0}, "at least 1"),
])
def test_view_draws_refuses_bad_options(options: dict, message: str) -> None:
    with pytest.raises(CoreError, match=message):
        view_draws(_glb(("a", "plaster", _floor(0, 0, 1, 1))), ONE_CAMERA, **options)


def test_view_draws_refuses_an_area_with_no_camera() -> None:
    # A thin ring: with a 15 m step the grid points (-10, -10), (5, -10), (-10, 5), (5, 5) all miss it.
    ring = Point(0, 0).buffer(10).difference(Point(0, 0).buffer(9.9))
    with pytest.raises(CoreError, match="^view_draws: no camera position falls inside the area$"):
        view_draws(_glb(("a", "plaster", _floor(0, 0, 1, 1))), ring, cell_m=100.0, step_m=15.0)


# Budget mode (ADR-017 amendment, 7 November 2026).

HOLED = Polygon([(60, -20), (100, -20), (100, 20), (60, 20)], [[(70, -5), (80, -5), (80, 5), (70, 5)]])
# (footprint, core_reach, core_extent)
REACHES = [
    (box(-5, -5, 5, 5), 0.0, 5.0),
    (box(60, -5, 80, 5), 60.0, 80.0),
    (box(60, 60, 80, 80), 60.0, 80.0),
    (box(-80, -30, -60, -6), 60.0, 80.0),                     # mirrored signs
    (Polygon([(100, 0), (0, 100), (100, 100)]), 50.0, 100.0),  # the nearest point is inside an edge, where |x| = |y|
    (box(-80, 70, 80, 74), 70.0, 80.0),                       # a thin footprint across x = 0 at y 70; its corners are 80 m out
    (Polygon([(-80, 70), (80, 72), (80, 74), (-80, 72)]), 5680 / 81, 80.0),  # the sloped edge meets |x| = |y| west of x = 0
    (Polygon([(-3, 90), (3, 90), (3, 95)]), 90.0, 95.0),      # an edge along y = 90, nearest at x = 0
    (Polygon([(30, -40), (40, -40), (40, 40), (30, 40)]), 30.0, 40.0),  # crosses y = 0
    (Polygon([(55, -60), (60, -55), (60, -50), (55, -55)]), 55.0, 60.0),
    (Polygon([(-60, -55), (-55, -60), (-50, -60), (-55, -55)]), 55.0, 60.0),
    (Polygon([(100, 50), (50, 100), (100, 100)]), 75.0, 100.0),   # the edge meets x = y between its ends
    (Polygon([(-100, 50), (-50, 100), (-100, 100)]), 75.0, 100.0),  # the edge meets x = -y between its ends
    (HOLED, 60.0, 100.0),
    (box(-70, 66, -60, 80), 66.0, 80.0),
]


@pytest.mark.parametrize(("footprint", "reach", "extent"), REACHES)
def test_core_reach_exact_values(footprint: Polygon, reach: float, extent: float) -> None:
    assert core_reach(footprint) == pytest.approx(reach, abs=1e-9)
    assert core_extent(footprint) == extent


def test_core_reach_of_a_multipolygon_and_bad_footprints() -> None:
    from shapely.geometry import MultiPolygon

    two = MultiPolygon([box(60, 60, 80, 80), box(-90, 0, -70, 10)])
    assert (core_reach(two), core_extent(two)) == (60.0, 90.0)
    assert core_reach(Polygon([(-5, 60), (5, 60), (0, 52)])) == 52.0
    # A hole around the centre: the footprint does not hold it, the hole's edge is nearest.
    ring = Polygon([(-50, -50), (50, -50), (50, 50), (-50, 50)], [[(-10, -10), (10, -10), (10, 10), (-10, 10)]])
    assert (core_reach(ring), core_extent(ring)) == (10.0, 50.0)
    # The centre on the outline is inside the footprint, and so is a centre half a metre from the outline.
    assert core_reach(box(0, 0, 10, 10)) == 0.0
    assert core_reach(box(-5, -5, 0.5, 0.5)) == 0.0
    for measure in (core_reach, core_extent):
        with pytest.raises(CoreError, match=f"^{measure.__name__} needs a polygon footprint, got Point$"):
            measure(Point(1, 1))
        with pytest.raises(CoreError, match="got Polygon$"):
            measure(Polygon())


@pytest.mark.parametrize(("footprint", "reach", "extent"), REACHES)
def test_core_reach_matches_building_tier(footprint: Polygon, reach: float, extent: float) -> None:
    assert building_tier(footprint, core_square(max(50.0, reach + 0.01))) == CORE
    if reach > 50:
        assert building_tier(footprint, core_square(reach - 0.01)) == SCENIC
        assert building_tier(footprint, core_square(reach)) == SCENIC  # on the edge it only touches the core
    # The extent is the smallest core that covers the footprint, the rule an interior needs.
    assert core_square(max(50.0, extent)).covers(footprint)
    if extent > 50:
        assert not core_square(extent - 0.01).covers(footprint)


def test_building_costs_groups_whole_buildings() -> None:
    records = [
        ({"type": "building", "tier": "core", "source_ids": ["a"]}, 100),
        ({"type": "building_part", "tier": "core", "source_ids": ["a", "p1"]}, 20),
        ({"type": "landmark_part", "tier": "core", "source_ids": ["a", "t1"]}, 7),
        ({"type": "interior", "tier": "core", "kind": "residential", "source_ids": ["a"]}, 500),
        ({"type": "interior", "tier": "core", "kind": "shop", "source_ids": ["b"]}, 40),
        ({"type": "interior", "tier": "core", "source_ids": ["d"]}, 9),
        ({"type": "interior", "tier": "core", "kind": "residential", "source_ids": ["d"]}, 2),
        ({"type": "building", "tier": "core", "source_ids": ["b"]}, 60),
        ({"type": "building", "tier": "scenic", "source_ids": ["c"]}, 10),
        ({"type": "building_part", "tier": "scenic", "source_ids": ["c", "p2"]}, 4),
        ({"type": "ground", "source_ids": ["w"]}, 900),
        ({"type": "urban_element", "source_ids": ["n"]}, 0),
    ]
    costs, interiors = building_costs(records)
    assert costs == {"a": 127, "b": 60}
    assert list(costs) == ["a", "b"]
    assert interiors == {"residential": {"a": 500, "d": 11}, "shop": {"b": 40}}
    assert building_costs([]) == ({}, {})


REACH = {"a": 60.0, "b": 70.0, "c": 80.0}
COST = {"a": 100, "b": 200, "c": 300}


def test_solve_core_hits_exact_target() -> None:
    assert solve_core(REACH, COST, 300, max_half_side_m=100) == CoreBudget(75.0, 300, ("a", "b"), None)
    assert solve_core(REACH, COST, 100, max_half_side_m=100) == CoreBudget(65.0, 100, ("a",), None)
    # Past the last reach the half-side is the midpoint to the cap.
    assert solve_core(REACH, COST, 600, max_half_side_m=100) == CoreBudget(90.0, 600, ("a", "b", "c"), None)


def test_solve_core_tie_prefers_smaller_core() -> None:
    # 200 is 100 from both {a} (100) and {a, b} (300): the smaller core wins.
    assert solve_core(REACH, COST, 200, max_half_side_m=100) == CoreBudget(65.0, 100, ("a",), None)
    assert solve_core(REACH, COST, 201, max_half_side_m=100) == CoreBudget(75.0, 300, ("a", "b"), None)
    # 50 is 50 from both the floor (0) and {a} (100).
    assert solve_core(REACH, COST, 50, max_half_side_m=100) == CoreBudget(50.0, 0, (), None)
    assert solve_core(REACH, COST, 51, max_half_side_m=100) == CoreBudget(65.0, 100, ("a",), None)


def test_solve_core_groups_equal_reaches() -> None:
    reach = {**REACH, "b2": 70.0}
    cost = {**COST, "b2": 200}
    assert solve_core(reach, cost, 300, max_half_side_m=100) == CoreBudget(65.0, 100, ("a",), None)
    assert solve_core(reach, cost, 400, max_half_side_m=100) == CoreBudget(75.0, 500, ("a", "b", "b2"), None)


def test_solve_core_is_order_independent() -> None:
    reach = {"z": 70.0, "a": 60.0, "m": 70.0, "c": 90.0}
    cost = {"z": 10, "a": 20, "m": 30, "c": 40}
    pool = InteriorPool(100, {"z": (75.0, 5), "a": (62.0, 7)})
    first = solve_core(reach, cost, 72, max_half_side_m=100, interiors=[pool])
    shuffled = InteriorPool(100, dict(reversed(list(pool.costs.items()))))
    assert first == solve_core(dict(reversed(list(reach.items()))), dict(sorted(cost.items(), key=lambda kv: -kv[1])), 72,
                               max_half_side_m=100, interiors=[shuffled])
    assert first == CoreBudget(82.5, 72, ("a", "m", "z"), None)


def test_solve_core_floor() -> None:
    reach = {"in": 10.0, "edge": 49.9, "a": 60.0}
    cost = {"in": 500, "edge": 500, "a": 100}
    assert solve_core(reach, cost, 300, max_half_side_m=100) == CoreBudget(50.0, 1000, ("edge", "in"), "floor")
    # At the floor and on the target there is nothing to report.
    assert solve_core(reach, cost, 1000, max_half_side_m=100) == CoreBudget(50.0, 1000, ("edge", "in"), None)
    # A building at exactly 50 m is not inside the 50 m core; its interior would be, were it inside.
    at = InteriorPool(10, {"at": (50.5, 3)})
    assert solve_core({"at": 50.0}, {"at": 10}, 1, max_half_side_m=100, interiors=[at]) == CoreBudget(50.0, 0, (), None)
    # An interior whose footprint the 50 m core covers counts at the floor.
    inner = InteriorPool(10, {"in": (50.0, 4)})
    assert solve_core({"in": 10.0}, {"in": 10}, 13, max_half_side_m=100, interiors=[inner]) == CoreBudget(50.0, 14, ("in",), "floor")


def test_solve_core_ceiling() -> None:
    reach = {"a": 60.0, "b": 70.0, "far": 100.0, "past": 140.0}
    cost = {"a": 100, "b": 200, "far": 5000, "past": 5000}
    assert solve_core(reach, cost, 10_000, max_half_side_m=100) == CoreBudget(100.0, 300, ("a", "b"), "ceiling")
    # On the target the smaller core is kept: a building at the cap is outside it, so the midpoint is 85 m.
    assert solve_core(reach, cost, 300, max_half_side_m=100) == CoreBudget(85.0, 300, ("a", "b"), None)
    # An interior whose footprint reaches past the cap is never built.
    out = InteriorPool(1000, {"b": (101.0, 50)})
    assert solve_core(reach, cost, 350, max_half_side_m=100, interiors=[out]) == CoreBudget(100.0, 300, ("a", "b"), "ceiling")
    # An interior that only the cap covers comes in at the cap.
    rim = InteriorPool(1000, {"b": (100.0, 50)})
    assert solve_core(reach, cost, 350, max_half_side_m=100, interiors=[rim]) == CoreBudget(100.0, 350, ("a", "b"), None)


def test_solve_core_interiors_follow_the_footprint_and_the_limit() -> None:
    # a's interior needs a 64 m core; b's, 78 m. The stair pool keeps at most 120 triangles.
    stairs = InteriorPool(120, {"a": (64.0, 50), "b": (78.0, 100)})
    assert solve_core(REACH, COST, 100, max_half_side_m=100, interiors=[stairs]) == CoreBudget(62.0, 100, ("a",), None)
    assert solve_core(REACH, COST, 150, max_half_side_m=100, interiors=[stairs]) == CoreBudget(67.0, 150, ("a",), None)
    assert solve_core(REACH, COST, 350, max_half_side_m=100, interiors=[stairs]) == CoreBudget(74.0, 350, ("a", "b"), None)
    assert solve_core(REACH, COST, 420, max_half_side_m=100, interiors=[stairs]) == CoreBudget(79.0, 420, ("a", "b"), None)
    assert solve_core(REACH, COST, 720, max_half_side_m=100, interiors=[stairs]) == CoreBudget(90.0, 720, ("a", "b", "c"), None)
    # Each pool has its own limit; a limit of 0 builds nothing.
    shops = InteriorPool(0, {"c": (85.0, 999)})
    assert solve_core(REACH, COST, 720, max_half_side_m=100, interiors=[stairs, shops]) == CoreBudget(82.5, 720, ("a", "b", "c"), None)
    assert solve_core(REACH, COST, 1000, max_half_side_m=100, interiors=[InteriorPool(500, {"c": (85.0, 400)})]) == CoreBudget(
        92.5, 1000, ("a", "b", "c"), None)


def test_solve_core_empty_costs() -> None:
    assert solve_core({}, {}, 1000, max_half_side_m=200) == CoreBudget(200.0, 0, (), "ceiling")
    # A building without a cost costs nothing.
    assert solve_core({"a": 60.0}, {}, 10, max_half_side_m=200) == CoreBudget(200.0, 0, ("a",), "ceiling")


@pytest.mark.parametrize(("target", "message"), [
    (0, "^target_triangles must be a positive integer, got 0$"),
    (-1, "got -1$"),
    (True, "got True$"),
    (1.5, "got 1.5$"),
    ("100", "got '100'$"),
])
def test_solve_core_refuses_a_bad_target(target: object, message: str) -> None:
    with pytest.raises(CoreError, match=message):
        solve_core({}, {}, target, max_half_side_m=100)  # type: ignore[arg-type]


@pytest.mark.parametrize(("kwargs", "message"), [
    ({"cost": {"a": -1}}, "^cost of a must be a non-negative integer, got -1$"),
    ({"cost": {"a": 1.0}}, "^cost of a must be a non-negative integer, got 1.0$"),
    ({"cost": {"a": True}}, "^cost of a must be a non-negative integer, got True$"),
    ({"cost": {"x": 1}}, "^cost of x has no reach$"),
    ({"reach": {"a": float("nan")}}, "^reach of a must be a finite non-negative number, got nan$"),
    ({"reach": {"a": float("inf")}}, "got inf$"),
    ({"reach": {"a": -0.5}}, "got -0.5$"),
    ({"reach": {"a": True}}, "got True$"),
    ({"reach": {"a": "60"}}, "got '60'$"),
    ({"cap": 49.9}, "^max_half_side_m must be between 50 and 2000 m, got 49.9$"),
    ({"cap": 2000.1}, "got 2000.1$"),
    ({"cap": True}, "got True$"),
    ({"pools": [InteriorPool(-1, {})]}, "^interior limit must be a non-negative integer, got -1$"),
    ({"pools": [InteriorPool(10, {"a": (70.0, -2)})]}, "^interior of a must be a non-negative integer, got -2$"),
    ({"pools": [InteriorPool(10, {"x": (70.0, 2)})]}, "^interior of x has no reach$"),
    ({"pools": [InteriorPool(10, {"a": (float("nan"), 2)})]}, "^extent of a must be a finite non-negative number, got nan$"),
    ({"pools": [InteriorPool(10, {"a": (60.0, 2)})]}, "^extent of a must exceed its reach$"),
    ({"pools": [InteriorPool(10, {"a": (59.0, 2)})]}, "^extent of a must exceed its reach$"),
])
def test_solve_core_refuses_bad_input(kwargs: dict[str, object], message: str) -> None:
    reach = kwargs.get("reach", {"a": 60.0})
    cost = kwargs.get("cost", {"a": 10})
    with pytest.raises(CoreError, match=message):
        solve_core(reach, cost, 100, max_half_side_m=kwargs.get("cap", 100.0), interiors=kwargs.get("pools", ()))  # type: ignore[arg-type]


def test_solve_core_accepts_the_bounds() -> None:
    assert solve_core({"a": 60.0}, {"a": 10}, 1, max_half_side_m=50) == CoreBudget(50.0, 0, (), "ceiling")
    assert solve_core({"a": 60.0}, {"a": 10}, 10, max_half_side_m=2000) == CoreBudget(1030.0, 10, ("a",), None)
    assert solve_core({"a": 0}, {"a": 0}, 1, max_half_side_m=100, interiors=[InteriorPool(0, {"a": (0.5, 0)})]).buildings == ("a",)


@pytest.mark.parametrize("value", [MIN_TARGET_TRIANGLES, 150_000, MAX_TARGET_TRIANGLES])
def test_check_target_accepts_the_range(value: int) -> None:
    assert check_target(value) == value


@pytest.mark.parametrize("value", [999, 2_000_001, 0, True, 1500.0, "1500", None])
def test_check_target_refuses(value: object) -> None:
    with pytest.raises(CoreError) as info:
        check_target(value)
    assert str(info.value) == f"target_triangles must be an integer from 1000 to 2000000, got {value!r}"


def test_budget_constants() -> None:
    assert (CORE_BUDGET_MODE, CORE_BUDGET_TOLERANCE, MIN_TARGET_TRIANGLES, MAX_TARGET_TRIANGLES) == ("budget", 0.05, 1000, 2_000_000)


def test_segment_reach_takes_both_ends() -> None:
    from onward_generator.tiers import _segment_reach

    assert _segment_reach(10, 60, 80, 80) == 60.0  # the start
    assert _segment_reach(80, 80, 10, 60) == 60.0  # the end
    assert _segment_reach(-40, 70, 40, 70) == 70.0  # where x = 0
    assert _segment_reach(90, -40, 90, 40) == 90.0  # where y = 0
    assert _segment_reach(5, 5, 5, 5) == 5.0  # a point


@pytest.mark.parametrize(("achieved", "within"), [(95, True), (105, True), (100, True), (94, False), (106, False)])
def test_within_tolerance_is_five_percent_either_way(achieved: int, within: bool) -> None:
    assert within_tolerance(achieved, 100) is within
    assert within_tolerance(achieved * 1000, 100_000) is within
