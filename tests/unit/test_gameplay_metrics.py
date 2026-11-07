"""Gameplay metrics on small synthetic geometry (ADR-032). Local metres; no map, no Unity."""

from __future__ import annotations

import math

import numpy as np
import pytest
from shapely import LineString, Point, box
from shapely.geometry import Polygon

from onward_generator.gameplay.metrics import (
    CORRIDOR_M,
    COVER_REACH_M,
    GRID_M,
    MAX_OVERLAP,
    MAX_ROUTES,
    RAY_CAP_M,
    RAY_COUNT,
    ROUTE_PENALTY,
    SAMPLE_STEP_M,
    SIGHT_BLOCK_HEIGHT_M,
    SNAP_M,
    STRETCH_M,
    Collider,
    CoverIndex,
    GameplayError,
    NavGraph,
    Route,
    SightCaster,
    balance,
    blocks_sight,
    collider_kind,
    cover_colliders,
    dijkstra,
    distinct_routes,
    from_edges,
    is_cover,
    nav_graph,
    overlap,
    path_edges,
    path_length_mm,
    per_100m,
    route_points,
    route_samples,
    route_stretches,
    shortest_path,
    sight_segments,
    snap,
)


def _node(graph: NavGraph, x: float, y: float) -> int:
    node = snap(graph, (x, y), 1e-6)
    assert node is not None, (x, y)
    return node


def _edges(graph: NavGraph) -> set[tuple[int, int]]:
    return {(a, b) for a in range(graph.size) for b, _ in graph.neighbours[a] if a < b}


def _prop(bounds: tuple[float, float, float, float], height: float, *, bottom: float = 0.0, name: str = "overgrow/collider/prop/x/1") -> Collider:
    return Collider(name, collider_kind(name), bounds, bottom, height)


# ------------------------------------------------------------------------------------------------ constants


def test_the_constants_are_the_ones_adr_032_records() -> None:
    assert (GRID_M, SNAP_M, MAX_ROUTES, ROUTE_PENALTY, MAX_OVERLAP, CORRIDOR_M) == (2.0, 2.0, 6, 3, 0.5, 8.0)
    assert (SAMPLE_STEP_M, STRETCH_M, COVER_REACH_M, SIGHT_BLOCK_HEIGHT_M) == (5.0, 25.0, 1.5, 1.2)
    assert (RAY_COUNT, RAY_CAP_M) == (72, 300.0)


# ------------------------------------------------------------------------------------------------ nav graph


def test_a_rectangle_gets_every_inner_node_and_eight_neighbour_edges() -> None:
    graph = nav_graph([box(-1, -1, 11, 5)])
    assert graph.size == 18  # x 0..10 by 2, y 0..4 by 2
    assert sorted(map(tuple, graph.xy.tolist()))[:3] == [(0.0, 0.0), (0.0, 2.0), (0.0, 4.0)]
    # 15 east, 12 north, 10 north-east and 10 north-west links.
    assert len(_edges(graph)) == 47


def test_nodes_are_numbered_south_to_north_then_west_to_east() -> None:
    graph = nav_graph([box(-1, -1, 5, 3)])
    assert graph.xy.tolist() == [[0.0, 0.0], [2.0, 0.0], [4.0, 0.0], [0.0, 2.0], [2.0, 2.0], [4.0, 2.0]]


def test_no_edge_crosses_the_notch_of_a_u() -> None:
    notch = box(3, 3, 7, 11)
    graph = nav_graph([box(-1, -1, 11, 11).difference(notch)])
    inside = notch.buffer(-0.01)
    for a, b in _edges(graph):
        assert not LineString([graph.xy[a], graph.xy[b]]).intersects(inside)
    path = shortest_path(graph, _node(graph, 2, 8), _node(graph, 8, 8))
    assert path is not None and path_length_mm(graph, path) > 6000


def test_a_segment_off_the_ground_is_no_edge_even_when_both_nodes_are_on_it() -> None:
    # A slit from the south edge cuts the line between the two nodes; the ground stays joined to the north.
    ground = box(-1, -1, 3, 1).difference(box(0.9, -1, 1.1, 0.5))
    graph = nav_graph([ground])
    assert graph.size == 2
    assert not _edges(graph)
    assert shortest_path(graph, _node(graph, 0, 0), _node(graph, 2, 0)) is None


def test_a_diagonal_needs_both_orthogonal_neighbours() -> None:
    # The diagonal segment lies on the ground, but the node at (2, 0) does not.
    ground = box(-0.5, -0.5, 2.5, 2.5).difference(Point(2, 0).buffer(0.3))
    graph = nav_graph([ground])
    assert graph.size == 3
    start, goal = _node(graph, 0, 0), _node(graph, 2, 2)
    assert (start, goal) not in _edges(graph)
    path = shortest_path(graph, start, goal)
    assert path is not None and path_length_mm(graph, path) == 4000


def test_a_straight_step_costs_2000_mm_and_a_diagonal_2828() -> None:
    corridor = nav_graph([box(-1, -1, 21, 1)])
    path = shortest_path(corridor, _node(corridor, 0, 0), _node(corridor, 20, 0))
    assert path is not None and path_length_mm(corridor, path) == 20000
    band = nav_graph([LineString([(0, 0), (6, 6)]).buffer(1.6)])
    path = shortest_path(band, _node(band, 0, 0), _node(band, 6, 6))
    assert path is not None and path_length_mm(band, path) == 3 * 2828
    assert [tuple(band.xy[node]) for node in path] == [(0.0, 0.0), (2.0, 2.0), (4.0, 4.0), (6.0, 6.0)]


def test_a_grid_of_another_spacing_scales_the_costs() -> None:
    graph = nav_graph([box(-0.5, -0.5, 3.5, 0.5)], grid_m=1.0)
    path = shortest_path(graph, _node(graph, 0, 0), _node(graph, 3, 0))
    assert path is not None and path_length_mm(graph, path) == 3000


@pytest.mark.parametrize("grid_m", [0.0, -2.0])
def test_a_grid_spacing_that_is_not_positive_is_refused(grid_m: float) -> None:
    with pytest.raises(GameplayError, match="grid_m must be greater than 0"):
        nav_graph([box(0, 0, 10, 10)], grid_m)


def test_an_empty_navmesh_is_refused() -> None:
    with pytest.raises(GameplayError, match="^the navmesh has no island$"):
        nav_graph([])
    with pytest.raises(GameplayError, match="^the navmesh has no island$"):
        nav_graph([Polygon()])


def test_ground_away_from_the_origin_is_aligned_to_the_grid() -> None:
    graph = nav_graph([box(5, 5, 9, 9)])
    assert graph.xy.tolist() == [[6.0, 6.0], [8.0, 6.0], [6.0, 8.0], [8.0, 8.0]]


def test_two_islands_make_one_graph() -> None:
    graph = nav_graph([box(-1, -1, 1, 1), box(9, -1, 11, 1)])
    assert graph.size == 2 and not _edges(graph)


# ------------------------------------------------------------------------------------------------ snap


def test_snap_takes_the_nearest_node_up_to_snap_m_inclusive() -> None:
    graph = nav_graph([box(-1, -1, 11, 5)])
    assert snap(graph, (0.9, 0.2)) == _node(graph, 0, 0)
    assert snap(graph, (1.1, 0.2)) == _node(graph, 2, 0)
    assert snap(graph, (0.0, -SNAP_M)) == _node(graph, 0, 0)
    assert snap(graph, (0.0, -SNAP_M - 0.01)) is None


def test_snap_on_a_tie_takes_the_lower_index() -> None:
    graph = nav_graph([box(-1, -1, 11, 5)])
    assert snap(graph, (1.0, 0.0)) == _node(graph, 0, 0)


def test_snap_on_an_empty_graph_is_none() -> None:
    assert snap(from_edges([], {}), (0.0, 0.0)) is None


# ------------------------------------------------------------------------------------------------ paths


def _square() -> NavGraph:
    return from_edges([(0, 0), (2, 0), (0, 2), (2, 2)], {(0, 1): 2000, (0, 2): 2000, (1, 3): 2000, (2, 3): 2000})


def test_a_tie_between_two_equal_paths_takes_the_lower_index_every_time() -> None:
    assert shortest_path(_square(), 0, 3) == [0, 1, 3]
    assert shortest_path(_square(), 0, 3) == [0, 1, 3]
    assert shortest_path(_square(), 3, 0) == [3, 1, 0]


def test_costs_override_some_edges() -> None:
    assert shortest_path(_square(), 0, 3, {(0, 1): 5000}) == [0, 2, 3]


def test_no_path_between_two_islands() -> None:
    graph = nav_graph([box(-1, -1, 5, 1), box(5.6, -1, 11, 1)])
    assert shortest_path(graph, _node(graph, 0, 0), _node(graph, 10, 0)) is None
    assert distinct_routes(graph, _node(graph, 0, 0), _node(graph, 10, 0)) == []


def test_a_path_from_a_node_to_itself_has_no_length() -> None:
    graph = _square()
    assert shortest_path(graph, 2, 2) == [2]
    assert path_length_mm(graph, [2]) == 0


def test_dijkstra_gives_every_distance_in_millimetres() -> None:
    best, previous = dijkstra(_square(), 0)
    assert best == [0, 2000, 2000, 4000]
    assert previous[1:] == [0, 0, 1]
    lone = from_edges([(0, 0), (2, 0), (9, 9)], {(0, 1): 2000})
    assert dijkstra(lone, 0)[0] == [0, 2000, None]


def test_from_edges_keeps_float_coordinates_and_a_one_millimetre_edge() -> None:
    graph = from_edges([(0, 0), (1, 0)], {(0, 1): 1})
    assert graph.xy.dtype == np.float64 and graph.cost(0, 1) == 1


def test_from_edges_refuses_a_loop_and_a_free_edge() -> None:
    with pytest.raises(GameplayError, match="positive cost"):
        from_edges([(0, 0), (1, 0)], {(0, 0): 1000})
    with pytest.raises(GameplayError, match="positive cost"):
        from_edges([(0, 0), (1, 0)], {(0, 1): 0})


def test_cost_of_unlinked_nodes_is_refused() -> None:
    with pytest.raises(GameplayError, match="not linked"):
        _square().cost(0, 3)


def test_route_points_keep_the_corners_only() -> None:
    graph = nav_graph([box(-1, -1, 9, 1).union(box(7, -1, 9, 9))])
    path = shortest_path(graph, _node(graph, 0, 0), _node(graph, 8, 8))
    assert path is not None
    corners = route_points(graph, Route(tuple(path), path_length_mm(graph, path), 1))
    assert corners == [(0.0, 0.0), (8.0, 0.0), (8.0, 8.0)]
    assert len(path) == 9
    single = route_points(graph, Route((path[0],), 0, 1))
    assert single == [(0.0, 0.0)]
    assert route_points(_square(), Route((0, 1), 2000, 1)) == [(0.0, 0.0), (2.0, 0.0)]


# ------------------------------------------------------------------------------------------------ routes


def test_a_ring_gives_two_routes_that_share_nothing() -> None:
    graph = nav_graph([box(-1, -1, 21, 21).difference(box(1, 1, 19, 19))])
    routes = distinct_routes(graph, _node(graph, 0, 0), _node(graph, 20, 20), corridor_m=0.5)
    assert len(routes) == 2
    assert [route.length_mm for route in routes] == [40000, 40000]
    assert overlap(graph, routes[0], routes[1], 0.5) == 0.0
    # With the default corridor the ends near each spawn are shared: 8 m at each end of 40 m.
    assert overlap(graph, routes[0], routes[1]) == pytest.approx(0.4)
    # The corridor is inclusive: the edge midpoints 7 m from the other route count at 7 m, not below.
    assert overlap(graph, routes[0], routes[1], 7.0) == pytest.approx(0.4)
    assert overlap(graph, routes[0], routes[1], 6.99) == pytest.approx(0.3)


def test_a_single_corridor_gives_one_route() -> None:
    graph = nav_graph([box(-1, -1, 41, 1)])
    routes = distinct_routes(graph, _node(graph, 0, 0), _node(graph, 40, 0))
    assert len(routes) == 1 and routes[0].attempt == 1 and routes[0].length_mm == 40000


def test_three_corridors_give_three_routes() -> None:
    ladder = [box(-1, y - 1, 41, y + 1) for y in (0, 20, 40)] + [box(-1, -1, 1, 41), box(39, -1, 41, 41)]
    graph = nav_graph(ladder)
    routes = distinct_routes(graph, _node(graph, 0, 20), _node(graph, 40, 20), corridor_m=0.5)
    assert len(routes) == 3
    assert sorted(route.length_mm for route in routes) == [40000, 80000, 80000]


def _detour(ratio: float) -> NavGraph:
    """A 1000 mm direct edge and a detour ``ratio`` times longer, far from it."""
    half = round(ratio * 500)
    return from_edges([(0, 0), (100, 0), (50, 100)], {(0, 1): 1000, (0, 2): half, (2, 1): half})


def test_the_penalty_is_three_times_the_cost() -> None:
    # A detour 2.5 times longer wins the second attempt only when the penalty is above 2.5.
    routes = distinct_routes(_detour(2.5), 0, 1)
    assert [route.attempt for route in routes] == [1, 2]
    # A detour 3.5 times longer waits for the third attempt: 3 is below 3.5, 9 is above.
    routes = distinct_routes(_detour(3.5), 0, 1)
    assert [route.attempt for route in routes] == [1, 3]
    assert distinct_routes(_detour(2.5), 0, 1, penalty=1) == distinct_routes(_detour(2.5), 0, 1, attempts=1)


def test_seven_parallel_ways_give_at_most_max_routes() -> None:
    xy = [(0.0, 0.0), (1000.0, 0.0)] + [(500.0, 200.0 * (k - 4)) for k in range(1, 8)]
    edges = {(0, k): 1000 for k in range(2, 9)} | {(k, 1): 1000 for k in range(2, 9)}
    routes = distinct_routes(from_edges(xy, edges), 0, 1)
    assert len(routes) == MAX_ROUTES == 6
    assert [route.attempt for route in routes] == [1, 2, 3, 4, 5, 6]


def _fork(costs: tuple[int, int, int, int]) -> NavGraph:
    """``s(0,0) - m(2,0) - g(4,0)`` and a detour ``m - d(2,2) - g``."""
    sm, mg, md, dg = costs
    return from_edges([(0, 0), (2, 0), (4, 0), (2, 2)], {(0, 1): sm, (1, 2): mg, (1, 3): md, (3, 2): dg})


def test_an_overlap_of_exactly_half_is_kept() -> None:
    graph = _fork((2000, 2000, 2000, 2828))
    routes = distinct_routes(graph, 0, 2, corridor_m=0.5)
    assert [route.nodes for route in routes] == [(0, 1, 2), (0, 1, 3, 2)]
    assert overlap(graph, routes[0], routes[1], 0.5) == 0.5


def test_an_overlap_above_half_is_rejected() -> None:
    graph = _fork((2080, 1920, 2000, 2000))
    routes = distinct_routes(graph, 0, 2, corridor_m=0.5)
    assert [route.nodes for route in routes] == [(0, 1, 2)]
    detour = Route((0, 1, 3, 2), 6080, 2)
    assert overlap(graph, routes[0], detour, 0.5) == pytest.approx(0.52)


def test_the_overlap_is_over_the_shorter_route_in_either_order() -> None:
    graph = _fork((2000, 2000, 2000, 2828))
    short, long = Route((0, 1, 2), 4000, 1), Route((0, 1, 3, 2), 6828, 2)
    assert overlap(graph, short, long, 0.5) == 0.5
    assert overlap(graph, long, short, 0.5) == 0.5


def test_on_a_tie_the_first_route_is_the_shorter_one() -> None:
    # Both 2000 mm. The edges of a lie within 4 m of b; the edges of b lie 5 m from a.
    graph = from_edges([(0, 0), (10, 0), (20, 0), (10, 10)], {(0, 1): 1000, (1, 2): 1000, (0, 3): 1000, (3, 2): 1000})
    a, b = Route((0, 1, 2), 2000, 1), Route((0, 3, 2), 2000, 2)
    assert overlap(graph, a, b, 4.0) == 1.0
    assert overlap(graph, b, a, 4.0) == 0.0


def test_a_route_without_length_overlaps_fully() -> None:
    graph = _square()
    assert overlap(graph, Route((0,), 0, 1), Route((0, 1, 3), 4000, 1)) == 1.0
    tiny = from_edges([(0, 0), (1, 0), (500, 500)], {(0, 1): 1, (0, 2): 1000, (2, 1): 1000})
    assert overlap(tiny, Route((0, 1), 1, 1), Route((0, 2, 1), 2000, 2), 0.1) == 0.0


def test_routes_from_a_node_to_itself_are_one_route_of_no_length() -> None:
    assert distinct_routes(_square(), 2, 2) == [Route((2,), 0, 1)]


def test_path_edges_are_undirected() -> None:
    assert path_edges([3, 1, 0]) == [(1, 3), (0, 1)]


# ------------------------------------------------------------------------------------------------ colliders


@pytest.mark.parametrize("name", [
    "collider/walkable#collider",
    "collider/carriageway#collider",
    "collider/walkable_crossing#collider",
])
def test_each_ground_collider_is_left_out(name: str) -> None:
    collider = _prop((0, 0, 50, 50), 2.0, name=name)
    assert collider.kind == "ground"
    assert not is_cover(collider) and not blocks_sight(collider)


def test_the_object_id_ends_at_the_first_hash() -> None:
    assert collider_kind("collider/walkable#collider#2") == "ground"


def test_a_ground_collider_is_also_known_by_its_type() -> None:
    assert collider_kind("collider/other#collider", "ground_collider") == "ground"
    assert collider_kind("collider/other#collider") == "prop"


@pytest.mark.parametrize("name", ["building/osm-way-1#collider", "building_part/osm-way-1:2#collider", "interior/osm-way-1/stair#collider"])
def test_buildings_come_from_the_footprints_not_the_collision(name: str) -> None:
    collider = _prop((0, 0, 10, 10), 12.0, name=name)
    assert collider.kind == "building"
    assert not is_cover(collider) and not blocks_sight(collider)


def test_the_wall_blocks_sight_but_is_never_cover() -> None:
    wall = _prop((-50, -50, 50, 50), 12.0, name="envelope/perimeter_wall#collider")
    assert wall.kind == "wall"
    assert not is_cover(wall) and blocks_sight(wall)
    assert cover_colliders([wall]) == []
    lengths = SightCaster(sight_segments(Polygon(), [wall])).lengths((0.0, 0.0))
    assert lengths[0] == pytest.approx(50.0)


def test_the_short_side_is_measured_away_from_the_origin() -> None:
    assert not is_cover(_prop((10.0, 0.0, 10.39, 1.0), 2.0))
    assert is_cover(_prop((10.0, 0.0, 10.4, 1.0), 2.0))


def test_cover_is_half_a_metre_tall_and_forty_centimetres_deep() -> None:
    assert is_cover(_prop((0, 0, 1, 0.4), 0.5))
    assert not is_cover(_prop((0, 0, 1, 0.4), 0.49))
    assert not is_cover(_prop((0, 0, 1, 0.39), 0.5))
    assert not is_cover(_prop((0, 0, 0.39, 1), 0.5))


def test_a_collider_above_street_level_is_neither_cover_nor_a_blocker() -> None:
    assert is_cover(_prop((0, 0, 1, 1), 3.0, bottom=0.5))
    assert not is_cover(_prop((0, 0, 1, 1), 3.0, bottom=0.51))
    assert blocks_sight(_prop((0, 0, 1, 1), 3.0, bottom=0.5))
    assert not blocks_sight(_prop((0, 0, 1, 1), 3.0, bottom=0.51))


def test_a_collider_blocks_sight_from_one_metre_twenty() -> None:
    assert blocks_sight(_prop((5, -1, 6, 1), 1.2))
    assert not blocks_sight(_prop((5, -1, 6, 1), 1.1))
    tall = SightCaster(sight_segments(Polygon(), [_prop((5, -1, 6, 1), 1.2)])).lengths((0.0, 0.0))
    low = SightCaster(sight_segments(Polygon(), [_prop((5, -1, 6, 1), 1.1)])).lengths((0.0, 0.0))
    assert tall[0] == pytest.approx(5.0) and low[0] == RAY_CAP_M


# ------------------------------------------------------------------------------------------------ sightlines


def _courtyard() -> SightCaster:
    return SightCaster(sight_segments(box(-30, -30, 30, 30).difference(box(-10, -10, 10, 10)), []))


def test_a_ray_stops_at_the_first_building_edge() -> None:
    lengths = _courtyard().lengths((0.0, 0.0))
    assert len(lengths) == RAY_COUNT
    assert lengths[0] == pytest.approx(10.0)  # east
    assert lengths[18] == pytest.approx(10.0)  # north, 90 degrees
    assert lengths[9] == pytest.approx(10.0 * math.sqrt(2))  # north-east, 45 degrees
    assert float(lengths.max()) == pytest.approx(10.0 * math.sqrt(2))


def test_no_blocker_is_an_empty_segment_list() -> None:
    assert sight_segments(Polygon(), [_prop((0, 0, 1, 1), 1.1)]).shape == (0, 4)
    assert sight_segments(box(0, 0, 1, 1), []).shape == (4, 4)


def test_a_point_close_to_a_wall_sees_less_than_a_metre() -> None:
    lengths = _courtyard().lengths((9.5, 0.0))
    assert lengths[0] == pytest.approx(0.5)


def test_a_point_on_a_segment_along_the_ray_sees_nothing() -> None:
    caster = SightCaster(np.array([[-2.0, 0.0, 5.0, 0.0]]))
    assert caster.lengths((0.0, 0.0))[0] == 0.0


def test_a_short_cap_is_one_piece() -> None:
    caster = SightCaster(np.array([[4.0, -1.0, 4.0, 1.0]]), cap_m=5.0)
    assert caster.lengths((0.0, 0.0))[0] == pytest.approx(4.0)
    assert caster.lengths((0.0, 0.0))[36] == 5.0


def test_a_ray_that_hits_nothing_stops_at_the_cap() -> None:
    lengths = SightCaster(np.zeros((0, 4))).lengths((3.0, 4.0))
    assert lengths.tolist() == [RAY_CAP_M] * RAY_COUNT
    lone = SightCaster(sight_segments(box(400, 400, 410, 410), [])).lengths((0.0, 0.0))
    assert lone.tolist() == [RAY_CAP_M] * RAY_COUNT


def test_a_far_edge_inside_the_cap_is_found_in_a_later_piece() -> None:
    lengths = SightCaster(sight_segments(box(237.5, -5, 240, 5), [])).lengths((0.0, 0.0))
    assert lengths[0] == pytest.approx(237.5)
    assert lengths[36] == RAY_CAP_M


def test_a_segment_along_the_ray_stops_it_at_its_near_end() -> None:
    caster = SightCaster(np.array([[5.0, 0.0, 9.0, 0.0]]))
    assert caster.lengths((0.0, 0.0))[0] == pytest.approx(5.0)
    behind = SightCaster(np.array([[-9.0, 0.0, -5.0, 0.0]]))
    assert behind.lengths((0.0, 0.0))[0] == RAY_CAP_M


def test_cover_within_reach_and_not_past_it() -> None:
    near = CoverIndex.of([_prop((COVER_REACH_M, -0.5, COVER_REACH_M + 1, 0.5), 1.0)])
    far = CoverIndex.of([_prop((COVER_REACH_M + 0.1, -0.5, COVER_REACH_M + 1, 0.5), 1.0)])
    assert near.near(Point(0, 0)) == [0]
    assert far.near(Point(0, 0)) == []
    assert CoverIndex.of([]).near(Point(0, 0)) == []
    assert CoverIndex.of([_prop((1, 0, 2, 1), 0.3)]).colliders == ()


# ------------------------------------------------------------------------------------------------ stretches


def test_a_60_m_route_is_three_stretches() -> None:
    assert route_stretches(60.0) == [(0.0, 25.0), (25.0, 50.0), (50.0, 60.0)]
    assert route_stretches(50.0) == [(0.0, 25.0), (25.0, 50.0)]
    assert route_stretches(0.0) == []
    assert route_stretches(50.4) == [(0.0, 25.0), (25.0, 50.0), (50.0, 50.4)]
    assert route_stretches(1.0, 0.5) == [(0.0, 0.5), (0.5, 1.0)]


@pytest.mark.parametrize("stretch_m", [0.0, -1.0])
def test_a_stretch_that_is_not_positive_is_refused(stretch_m: float) -> None:
    with pytest.raises(GameplayError, match="stretch_m must be greater than 0"):
        route_stretches(10.0, stretch_m)


def test_samples_every_five_metres_and_the_end() -> None:
    assert route_samples(12.0) == [0.0, 5.0, 10.0, 12.0]
    assert route_samples(10.0) == [0.0, 5.0, 10.0]
    assert route_samples(0.0) == [0.0]
    assert route_samples(10.4) == [0.0, 5.0, 10.0, 10.4]


def test_cover_per_100_m() -> None:
    assert per_100m(1, 25.0) == 4.0
    assert per_100m(2, 10.0) == 20.0
    assert per_100m(1, 30.0) == 3.33
    assert per_100m(0, 25.0) == 0.0
    with pytest.raises(GameplayError, match="^a stretch must be longer than 0 m$"):
        per_100m(1, 0.0)


# ------------------------------------------------------------------------------------------------ balance


def test_balance_divides_by_the_speed_and_the_slower_team() -> None:
    assert balance(100.0, 85.0) == {"attackers_s": 25.0, "defenders_s": 21.25, "diff_pct": 15.0}
    assert balance(100.0, 84.0)["diff_pct"] == 16.0
    assert balance(84.0, 100.0)["diff_pct"] == 16.0
    assert balance(0.0, 0.0)["diff_pct"] == 0.0
    assert balance(30.0, 30.0, 2.0) == {"attackers_s": 15.0, "defenders_s": 15.0, "diff_pct": 0.0}
    assert balance(10.0, 20.0, 3.0) == {"attackers_s": 3.33, "defenders_s": 6.67, "diff_pct": 50.0}
    assert balance(1.0, 1.0, 0.5) == {"attackers_s": 2.0, "defenders_s": 2.0, "diff_pct": 0.0}


@pytest.mark.parametrize("speed", [0.0, -4.0])
def test_balance_refuses_a_speed_that_is_not_positive(speed: float) -> None:
    with pytest.raises(GameplayError, match="speed must be greater than 0"):
        balance(100.0, 85.0, speed)


def test_balance_refuses_a_negative_length() -> None:
    with pytest.raises(GameplayError, match="^a route length cannot be negative$"):
        balance(-1.0, 85.0)
    with pytest.raises(GameplayError, match="^a route length cannot be negative$"):
        balance(1.0, -85.0)
