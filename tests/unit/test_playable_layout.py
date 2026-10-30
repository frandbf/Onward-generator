"""Playable layout around the Murcia origin: Hunt anchors, SDK audio names, urban day."""

from __future__ import annotations

import math

import pytest
from shapely import LineString, MultiLineString, Point, STRtree, box
from shapely.geometry.base import BaseGeometry

from onward_generator.export.package import _check_layout
from onward_generator.generation.config import load_generation_config
from onward_generator.generation.surfaces import LinearWay, build_partition
from onward_generator.perf.layout import (
    AI_COUNT,
    GAMEPLAY_VERSION,
    MIN_SPAWN_SEPARATION_M,
    MODE_CODES,
    NAV_AGENT_RADIUS_M,
    ROUTE_POINTS,
    ROUTE_STEP_M,
    SPAWN_SEPARATION_M,
    LayoutError,
    _patrol_lines,
    _routes,
    _sample_route,
    component_holding,
    extra_spawn_block,
    fit_spawn_separation,
    hunt_routes,
    nav_islands,
    patrol_seeds,
    patrol_spawns,
    scatter_points,
    spread_routes,
    yaw_toward,
)

# Local metres. The pipeline origin for every test is MURCIA_CENTER, which this box surrounds.
PLAZA = box(-80, -80, 80, 80)


def test_assault_second_block_stays_clear_of_both_teams() -> None:
    centre = extra_spawn_block(PLAZA, (0.0, 0.0), [(0.0, 0.0), (30.0, 0.0)])
    assert math.hypot(centre[0], centre[1]) >= 10 - 1e-6
    assert math.hypot(centre[0] - 30, centre[1]) >= 10 - 1e-6
    with pytest.raises(LayoutError, match="second spawn block"):
        extra_spawn_block(box(0, 0, 6, 6), (3.0, 3.0), [(3.0, 3.0)])


def test_uplink_stays_on_its_own_sidewalk_when_another_is_larger() -> None:
    small = box(0, 170, 20, 190)
    large = box(-200, -200, -50, -50)
    chosen = component_holding([large, small], (2.13, 178.72))
    assert chosen.equals(small)
    with pytest.raises(LayoutError, match="not on walkable ground"):
        component_holding([large], (2.13, 178.72))


def test_yaw_looks_north_and_east() -> None:
    assert yaw_toward(0, 0, 0, 10) == 0
    assert yaw_toward(0, 0, 10, 0) == 90


def test_scatter_fills_the_plaza_and_rejects_a_tight_square() -> None:
    points = scatter_points(PLAZA, AI_COUNT, 8.0, (0.0, 0.0))
    assert len(points) == AI_COUNT
    assert all(math.hypot(a[0] - b[0], a[1] - b[1]) >= 8 - 1e-6 for i, a in enumerate(points) for b in points[i + 1:])
    with pytest.raises(LayoutError, match="walkable ground holds"):
        scatter_points(box(0, 0, 10, 10), AI_COUNT, 8.0, (5.0, 5.0))


def test_scatter_places_points_a_coarse_grid_would_miss() -> None:
    """A 7×30 m strip holds three points 8 m apart, and none of an 8 m lattice from its corner."""
    strip = box(0, 0, 7, 30)
    points = scatter_points(strip, 3, 8.0, (0.0, 0.0))
    assert len(points) == 3
    assert all(strip.contains(Point(x, y)) for x, y in points)
    assert all(math.hypot(a[0] - b[0], a[1] - b[1]) >= 8 - 1e-6 for i, a in enumerate(points) for b in points[i + 1:])
    with pytest.raises(LayoutError, match="walkable ground holds 1"):
        scatter_points(box(0, 0, 5, 5), 3, 8.0, (2.5, 2.5))


def test_patrol_seeds_share_a_close_start_and_scatter_keeps_them() -> None:
    routes = [
        {"points": [{"x": 0, "y": 0}, {"x": 14, "y": 0}]},
        {"points": [{"x": 2, "y": 0}, {"x": 16, "y": 0}]},
        {"points": [{"x": 40, "y": 10}, {"x": 40, "y": 24}]},
    ]
    seeds = patrol_seeds(routes, AI_COUNT)
    assert seeds == [(0.0, 0.0), (40.0, 10.0)]
    points = scatter_points(PLAZA, AI_COUNT, 8.0, (0.0, 0.0), seeds=seeds)
    assert points[:2] == seeds
    assert len(points) == AI_COUNT
    for extra in points[2:]:
        assert all(math.hypot(extra[0] - seed[0], extra[1] - seed[1]) >= 8 - 1e-6 for seed in seeds)


def test_every_hunt_spawn_is_its_own_route_start() -> None:
    routes = [{"points": [{"x": i * SPAWN_SEPARATION_M, "y": 0}, {"x": i * SPAWN_SEPARATION_M, "y": 14}]} for i in range(AI_COUNT)]
    points = patrol_spawns(routes, AI_COUNT)
    assert points == [(i * SPAWN_SEPARATION_M, 0.0) for i in range(AI_COUNT)]
    assert all(math.hypot(a[0] - b[0], a[1] - b[1]) >= SPAWN_SEPARATION_M - 1e-6 for i, a in enumerate(points) for b in points[i + 1:])


def test_spread_routes_cover_both_sides_of_the_focus() -> None:
    routes = [{"points": [{"x": x, "y": 0.0}, {"x": x, "y": 14.0}]} for x in range(-200, 201, 8)]
    chosen = spread_routes(routes, AI_COUNT, (0.0, 0.0))
    xs = [route["points"][0]["x"] for route in chosen]
    assert min(xs) < -50
    assert max(xs) > 50
    assert any(abs(x) <= 8 for x in xs)


def test_a_street_places_hunt_starts_on_both_sidewalks() -> None:
    street = LinearWay("street", "vehicular", LineString([(0.0, 0.0), (0.0, 300.0)]), 6.0, "config_default")
    foot = LinearWay("foot", "pedestrian", MultiLineString([[(40.0, 0.0), (40.0, 40.0)]]), 3.0, "config_default")

    class Config:
        sidewalk_width_m = 2.0

    class Base:
        ways = [street, foot]
        config = Config()

    ground = box(-20, -5, 50, 305)
    routes = _routes(Base(), ground, [ground])
    starts = [(route["points"][0]["x"], route["points"][0]["y"]) for route in routes]
    assert len(starts) >= AI_COUNT
    # The middle of each 2 m sidewalk is 4 m out, and the line by the kerb 3.5 m.
    assert all(round(abs(x), 2) in (3.5, 4.0) or abs(x - 40) < 0.05 for x, _y in starts)
    assert any(x < 0 for x, _y in starts)
    assert any(0 < x < 5 for x, _y in starts)
    assert any(abs(x - 40) < 0.05 for x, _y in starts)
    assert all(
        math.hypot(a[0] - b[0], a[1] - b[1]) >= SPAWN_SEPARATION_M - 1e-6
        for i, a in enumerate(starts)
        for b in starts[i + 1 :]
    )


def _pedestrian(length: float):
    class Config:
        sidewalk_width_m = 2.0

    class Base:
        ways = [LinearWay("foot", "pedestrian", LineString([(0.0, 0.0), (0.0, length)]), 3.0, "config_default")]
        config = Config()

    return Base(), box(-5, -5, 5, length + 5)


def test_spawn_separation_stays_at_8_m_on_a_long_street() -> None:
    base, free = _pedestrian(400.0)
    routes, separation = fit_spawn_separation(base, free, [free], AI_COUNT)
    assert separation == SPAWN_SEPARATION_M
    assert len(routes) >= AI_COUNT


def test_spawn_separation_shrinks_until_32_starts_fit() -> None:
    base, free = _pedestrian(240.0)
    routes, separation = fit_spawn_separation(base, free, [free], AI_COUNT)
    assert MIN_SPAWN_SEPARATION_M <= separation < SPAWN_SEPARATION_M
    assert len(routes) >= AI_COUNT
    assert len(_routes(base, free, [free], round(separation + 0.1, 2))) < AI_COUNT
    starts = [(route["points"][0]["x"], route["points"][0]["y"]) for route in routes]
    assert all(
        math.hypot(a[0] - b[0], a[1] - b[1]) >= separation - 1e-6
        for i, a in enumerate(starts)
        for b in starts[i + 1 :]
    )


def test_spawn_separation_stops_above_the_patrol_link() -> None:
    base, free = _pedestrian(80.0)
    with pytest.raises(LayoutError, match="5.1"):
        fit_spawn_separation(base, free, [free], AI_COUNT)


def test_hunt_spawns_refuse_a_shared_start() -> None:
    routes = [
        {"points": [{"x": 0, "y": 0}, {"x": 20, "y": 0}]},
        {"points": [{"x": 2, "y": 0}, {"x": 22, "y": 0}]},
    ]
    with pytest.raises(LayoutError, match="route starts hold 1"):
        patrol_spawns(routes, AI_COUNT)


def test_gameplay_schema_requires_hunt_audio_and_urban_day() -> None:
    doc = {
        "schema": "onward_generator.gameplay_layout",
        "attackers": {"centre_m": [0, 0], "points_m": [{"x": 0, "y": 0}] * 8},
        "defenders": {"centre_m": [30, 0], "points_m": [{"x": 30, "y": 0}] * 8},
        "uplink_m": [15, 0],
        "map_quad_m": [0, -2],
        "level_size_m": 160,
        "damage_volume": {"centre_m": [0, 0], "size_m": [10, 10, 10]},
        "ai_m": [{"x": i * 8, "y": 0} for i in range(32)],
        "ammo_m": [{"x": 1, "y": 1}] * 4,
        "routes": [{"points": [{"x": i * 8, "y": 0}, {"x": i * 8, "y": 14}]} for i in range(32)],
        "audio_global": "Suburban Day",
        "environment": "Urban",
        "time_of_day": "Day",
        "profile": "UrbanDay1",
        "far_clip_m": 1000,
        "flyover": 1,
    }
    assert _check_layout(doc) == []
    doc["schema_version"] = GAMEPLAY_VERSION
    assert any("modes must be" in problem for problem in _check_layout(doc))
    doc["modes"] = dict(MODE_CODES)
    doc["attackers_b"] = {"centre_m": [0, 12], "points_m": [{"x": 0, "y": 12}] * 8}
    doc["defenders_b"] = {"centre_m": [30, 12], "points_m": [{"x": 30, "y": 12}] * 8}
    doc["escort_a_m"] = [8, 8]
    doc["escort_b_m"] = [22, 8]
    doc["evac_m"] = [40, 40]
    doc["harddrives_m"] = [{"x": 10, "y": 20}, {"x": 18, "y": 20}, {"x": 26, "y": 20}]
    assert _check_layout(doc) == []
    doc["ai_m"] = doc["ai_m"][:3]
    assert any("32 AI" in problem for problem in _check_layout(doc))
    stacked = {**doc, "ai_m": [{"x": 0, "y": 0}] * 32, "routes": [{"points": [{"x": 0, "y": 0}, {"x": 0, "y": 14}]}]}
    assert any("closer than" in problem for problem in _check_layout(stacked))
    close = {
        **doc,
        "ai_m": [{"x": i * 6.5, "y": 0} for i in range(32)],
        "routes": [{"points": [{"x": i * 6.5, "y": 0}, {"x": i * 6.5, "y": 14}]} for i in range(32)],
    }
    assert any("closer than 8" in problem for problem in _check_layout(close))
    close["ai_separation_m"] = 6.5
    assert _check_layout(close) == []
    close["ai_separation_m"] = 4.0
    assert any("ai_separation_m" in problem for problem in _check_layout(close))


def _base(*ways: LinearWay, partition: object = None) -> object:
    class Config:
        sidewalk_width_m = 2.0

    class Base:
        pass

    base = Base()
    base.ways = list(ways)  # type: ignore[attr-defined]
    base.config = Config()  # type: ignore[attr-defined]
    base.partition = partition  # type: ignore[attr-defined]
    return base


def _sample(line: LineString, free: BaseGeometry, start: float, parts: list[BaseGeometry]) -> list[tuple[float, float]]:
    return [(p["x"], p["y"]) for p in _sample_route(line, free, start, parts, STRtree(parts))]


def test_a_route_keeps_two_metres_before_the_end_of_its_line() -> None:
    ground = box(-5, -5, 100, 5)
    assert _sample(LineString([(0, 0), (20, 0)]), ground, 4.0, [ground]) == [(4.0, 0.0)]
    assert _sample(LineString([(0, 0), (20.5, 0)]), ground, 4.0, [ground]) == [(4.0, 0.0), (18.0, 0.0)]


def test_route_points_are_rounded_to_the_centimetre() -> None:
    ground = box(-5, -5, 100, 100)
    assert _sample(LineString([(0, 0), (50, 50)]), ground, 4.0, [ground])[0] == (2.83, 2.83)


def test_routes_start_every_separation_until_the_last_two_points() -> None:
    """A 36.5 m way: starts at 4, 12 and 20 m. The one at 20 m holds exactly two points (20 and 34 m)."""
    way = LinearWay("foot", "pedestrian", LineString([(0, 0), (0, 36.5)]), 3.0, "config_default")
    ground = box(-1.5, -1, 1.5, 40)
    routes = _routes(_base(way), ground, [ground])  # type: ignore[arg-type]
    assert [[point["y"] for point in route["points"]] for route in routes] == [[4.0, 18.0, 32.0], [12.0, 26.0], [20.0, 34.0]]


def test_a_route_holds_at_most_route_points_one_step_apart() -> None:
    ground = box(-5, -5, 1000, 5)
    points = _sample(LineString([(0, 0), (900, 0)]), ground, 4.0, [ground])
    assert len(points) == ROUTE_POINTS == 16
    assert points == [(4.0 + ROUTE_STEP_M * i, 0.0) for i in range(ROUTE_POINTS)]


def test_a_route_skips_furniture_and_keeps_going() -> None:
    ground = box(-5, -5, 100, 5)
    free = ground.difference(Point(18, 0).buffer(1.0))
    assert _sample(LineString([(0, 0), (70, 0)]), free, 4.0, [ground]) == [(4.0, 0.0), (32.0, 0.0), (46.0, 0.0), (60.0, 0.0)]


def test_a_route_starts_on_the_first_point_of_an_island() -> None:
    ground = box(-5, -5, 100, 5)
    island = box(10, -1, 100, 1)
    assert _sample(LineString([(0, 0), (50, 0)]), ground, 4.0, [island]) == [(18.0, 0.0), (32.0, 0.0), (46.0, 0.0)]


def test_a_route_stops_where_it_leaves_its_island() -> None:
    """The line crosses a street onto another island, then back onto the first; the route stops at the street."""
    ground = box(-5, -50, 110, 5)
    # A U: two arms on the line, joined far below it.
    home = box(-1, -30, 101, 1).difference(box(31.0, -25, 51.5, 2))
    other = box(44, -1, 50, 1)
    assert _sample(LineString([(0, 0), (100, 0)]), ground, 4.0, [home, other]) == [(4.0, 0.0), (18.0, 0.0)]


def test_short_alley_ways_are_merged_into_one_patrol_line() -> None:
    pieces = [((0.0, 14.0 * i), (0.0, 14.0 * (i + 1))) for i in range(5)]
    alley = [LinearWay(f"alley-{i}", "pedestrian", LineString(piece), 3.0 - i * 0.5, "inferred") for i, piece in enumerate(pieces)]
    assert [round(line.length, 6) for line in _patrol_lines(alley, 2.0)] == [70.0]
    ground = box(-1.5, 0, 1.5, 70)
    routes = _routes(_base(*alley), ground, [ground])  # type: ignore[arg-type]
    assert routes
    assert all(len(route["points"]) >= 2 for route in routes)


def test_street_patrols_run_mid_sidewalk_and_by_the_kerb_on_merged_ways() -> None:
    ways = [
        LinearWay("wide-a", "vehicular", LineString([(0, 0), (0, 30)]), 6.0, "config_default"),
        LinearWay("wide-b", "vehicular", LineString([(0, 30), (0, 60)]), 6.0, "config_default"),
        LinearWay("narrow-a", "vehicular", LineString([(50, 0), (50, 30)]), 4.0, "inferred"),
        LinearWay("narrow-b", "vehicular", LineString([(50, 30), (50, 60)]), 4.0, "inferred"),
        LinearWay("foot", "pedestrian", LineString([(90, 0), (90, 60)]), 3.0, "config_default"),
    ]
    found = sorted((round(line.coords[0][0], 6), round(line.length, 6)) for line in _patrol_lines(ways, 2.0))
    assert found == [
        (-4.0, 60.0), (-3.5, 60.0), (3.5, 60.0), (4.0, 60.0),
        (47.0, 60.0), (47.5, 60.0), (52.5, 60.0), (53.0, 60.0),
        (90.0, 60.0),
    ]


def test_a_narrow_sidewalk_holds_a_patrol_by_the_kerb() -> None:
    """A 4 m service alley with facades 0.9 m from the kerb: the sidewalk middle is inside the wall."""
    alley = LinearWay("alley", "vehicular", LineString([(0, 0), (120, 0)]), 4.0, "inferred")
    strip = box(0, 2.0, 120, 2.9)
    routes = _routes(_base(alley), strip, [strip.buffer(-NAV_AGENT_RADIUS_M)])  # type: ignore[arg-type]
    assert routes
    assert {point["y"] for route in routes for point in route["points"]} == {2.5}


def test_nav_islands_split_at_the_carriageway_and_ignore_furniture() -> None:
    config = load_generation_config()
    street = LinearWay("street", "vehicular", LineString([(-60, 0), (60, 0)]), 6.0, "config_default")
    partition = build_partition(box(-50, -50, 50, 50), [], [street], [], config)
    islands = nav_islands(_base(street, partition=partition))  # type: ignore[arg-type]
    assert sorted(round(part.bounds[1], 6) for part in islands) == [-50 + NAV_AGENT_RADIUS_M, 3 + NAV_AGENT_RADIUS_M]
    assert sorted(round(part.bounds[3], 6) for part in islands) == [-3 - NAV_AGENT_RADIUS_M, 50 - NAV_AGENT_RADIUS_M]


def _two_islands(home_length: float) -> tuple[object, BaseGeometry, list[BaseGeometry], BaseGeometry]:
    home_way = LinearWay("home", "pedestrian", LineString([(0, 0), (0, home_length)]), 3.0, "config_default")
    far_way = LinearWay("far", "pedestrian", LineString([(100, 0), (100, 400)]), 3.0, "config_default")
    home = box(-1.5, 0, 1.5, home_length)
    far = box(98.5, 0, 101.5, 400)
    return _base(home_way, far_way), home.union(far), [home, far], home


def test_hunt_routes_stay_on_the_origin_island_when_it_holds_the_roster() -> None:
    base, free, islands, home = _two_islands(400.0)
    routes, separation = hunt_routes(base, free, islands, home, AI_COUNT, (0.0, 0.0))  # type: ignore[arg-type]
    assert separation == SPAWN_SEPARATION_M
    assert len(routes) == AI_COUNT
    assert all(point["x"] == 0.0 for route in routes for point in route["points"])


def test_hunt_routes_keep_every_origin_route_and_fill_from_other_islands() -> None:
    base, free, islands, home = _two_islands(60.0)
    on_home = _routes(base, free, [home])  # type: ignore[arg-type]
    assert 0 < len(on_home) < AI_COUNT
    routes, separation = hunt_routes(base, free, islands, home, AI_COUNT, (0.0, 0.0))  # type: ignore[arg-type]
    assert separation == SPAWN_SEPARATION_M
    assert len(routes) == AI_COUNT
    assert routes[: len(on_home)] == on_home
    assert all(point["x"] == 100.0 for route in routes[len(on_home):] for point in route["points"])
    assert len({(route["points"][0]["x"], route["points"][0]["y"]) for route in routes}) == AI_COUNT


def test_spread_routes_returns_nothing_for_no_room() -> None:
    routes = [{"points": [{"x": x, "y": 0.0}, {"x": x, "y": 14.0}]} for x in range(0, 80, 8)]
    assert spread_routes(routes, 0, (0.0, 0.0)) == []
    assert spread_routes(routes, 1, (0.0, 0.0)) == [routes[0]]
