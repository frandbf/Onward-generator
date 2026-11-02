"""Playable layout around the Murcia origin: Hunt anchors, SDK audio names, urban day."""

from __future__ import annotations

import math

import pytest
from shapely import LineString, MultiLineString, Point, Polygon, STRtree, box
from shapely.geometry.base import BaseGeometry

from onward_generator.export.package import _check_layout
from onward_generator.generation.config import load_generation_config
from onward_generator.generation.surfaces import LinearWay, build_partition
from onward_generator.perf.layout import (
    AI_COUNT,
    BLOCK_GAP_M,
    GAMEPLAY_VERSION,
    GRID_M,
    MIN_SPAWN_SEPARATION_M,
    MODE_CODES,
    NAV_AGENT_RADIUS_M,
    ROUTE_POINTS,
    ROUTE_STEP_M,
    SPAWN_SEPARATION_M,
    STREET_LIGHTS_MAX,
    TIME_OF_DAY_SETTINGS,
    LayoutError,
    _block,
    _patrol_lines,
    _routes,
    _sample_route,
    component_holding,
    extra_spawn_block,
    fit_spawn_separation,
    free_ground,
    hunt_routes,
    nav_islands,
    navigation_estimate,
    patrol_seeds,
    patrol_spawns,
    playable_area,
    scatter_points,
    spread_routes,
    street_lights,
    yaw_toward,
)

# Local metres. The pipeline origin for every test is MURCIA_CENTER, which this box surrounds.
PLAZA = box(-80, -80, 80, 80)


def test_assault_second_block_stays_clear_of_both_teams() -> None:
    centre = extra_spawn_block(PLAZA, (0.0, 0.0), [(0.0, 0.0), (30.0, 0.0)])
    assert math.hypot(centre[0], centre[1]) >= 10 - 1e-6
    assert math.hypot(centre[0] - 30, centre[1]) >= 10 - 1e-6
    with pytest.raises(LayoutError, match="^no second spawn block fits for Assault$"):
        extra_spawn_block(box(0, 0, 6, 6), (3.0, 3.0), [(3.0, 3.0)])


def _first_block_by_scanning(area: BaseGeometry, near: tuple[float, float], avoid: list[tuple[float, float]]) -> tuple[float, float] | None:
    """The scan ``extra_spawn_block`` replaced: every grid slot, smallest (dist, y, x) wins."""
    minx, miny, maxx, maxy = area.bounds
    best, best_key = None, None
    y = math.floor(miny / GRID_M) * GRID_M
    while y <= maxy + 1e-6:
        x = math.floor(minx / GRID_M) * GRID_M
        while x <= maxx + 1e-6:
            clear = all(math.hypot(x - ax, y - ay) >= BLOCK_GAP_M - 1e-6 for ax, ay in avoid)
            if clear and all(area.contains(Point(p)) for p in _block(x, y)):
                key = (math.hypot(x - near[0], y - near[1]), y, x)
                if best_key is None or key < best_key:
                    best_key, best = key, (round(x, 2), round(y, 2))
            x += GRID_M
        y += GRID_M
    return best


def test_assault_second_block_is_the_nearest_slot_south_then_west_first() -> None:
    # Twelve slots lie exactly 10 m from the team; the southernmost, then westernmost, wins.
    first = extra_spawn_block(PLAZA, (0.0, 0.0), [(0.0, 0.0)])
    assert first == (0.0, -10.0) and all(type(v) is float for v in first)
    # The gap is measured from each team, not from the origin.
    assert extra_spawn_block(PLAZA, (20.0, 0.0), [(20.0, 0.0)]) == (20.0, -10.0)
    assert extra_spawn_block(PLAZA, (0.0, 20.0), [(0.0, 20.0)]) == (0.0, 10.0)
    # 9.5 m is too close; exactly the gap less the 1e-6 m tolerance is not.
    assert extra_spawn_block(PLAZA, (0.0, 0.5), [(0.0, 0.5)]) == (-10.0, 0.0)
    assert extra_spawn_block(PLAZA, (0.0, 10.0), [(0.0, 1e-6)]) == (0.0, 10.0)
    assert extra_spawn_block(box(-80, -9, 80, 80), (0.0, 0.0), [(0.0, 0.0)]) == (-6.0, -8.0)
    yard = Polygon([(-41, -37), (53, -37), (53, 3), (13, 3), (13, 47), (-41, 47)], [[(-5, -9), (7, -9), (7, 9), (-5, 9)]])
    for near, avoid in (((0.0, 0.0), [(0.0, 0.0)]), ((40.0, -20.0), [(0.0, 0.0), (30.0, -30.0)]), ((-30.0, 40.0), [])):
        assert extra_spawn_block(yard, near, avoid) == _first_block_by_scanning(yard, near, avoid)


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
    night = {**doc, "time_of_day": "Night", "profile": "UrbanNight1", "street_lights_m": [1.0, 2.0, 4.2]}
    assert _check_layout(night) == []
    for bad, message in (
        ({**night, "profile": "UrbanDay1"}, "environment must be"),
        ({**doc, "profile": "UrbanNight1"}, "environment must be"),
        ({**doc, "environment": "Desert"}, "environment must be"),
        ({**night, "street_lights_m": [1.0, 2.0]}, "street_lights_m must be flat"),
        ({**night, "street_lights_m": "x"}, "street_lights_m must be flat"),
        ({**night, "street_lights_m": [1.0, 2.0, True]}, "street_lights_m must be flat"),
        ({**doc, "street_lights_m": [1.0, 2.0, 4.2]}, "only for a night map"),
    ):
        assert any(message in problem for problem in _check_layout(bad)), bad
    assert "layout: environment must be Urban / Day / UrbanDay1 or Urban / Night / UrbanNight1" in _check_layout({**doc, "environment": "Desert"})
    assert "layout: street_lights_m must be flat x, y, z triples" in _check_layout({**night, "street_lights_m": "x"})
    assert "layout: street_lights_m is only for a night map" in _check_layout({**doc, "street_lights_m": [1.0, 2.0, 4.2]})
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


def test_a_route_does_not_start_in_the_bounding_box_of_an_island() -> None:
    """The first point sits in the gap of a U-shaped island: inside its envelope, 9 m from its ground."""
    ground = box(-5, -50, 110, 5)
    island = box(-1, -30, 101, 1).difference(box(31.0, -25, 51.5, 2))
    assert _sample(LineString([(36, 0), (100, 0)]), ground, 4.0, [island]) == [(54.0, 0.0), (68.0, 0.0), (82.0, 0.0), (96.0, 0.0)]


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
    partition.crossings = Polygon()
    islands = nav_islands(_base(street, partition=partition))  # type: ignore[arg-type]
    assert sorted(round(part.bounds[1], 6) for part in islands) == [-50 + NAV_AGENT_RADIUS_M, 3 + NAV_AGENT_RADIUS_M]
    assert sorted(round(part.bounds[3], 6) for part in islands) == [-3 - NAV_AGENT_RADIUS_M, 50 - NAV_AGENT_RADIUS_M]


def test_nav_islands_join_across_the_walkable_crossings() -> None:
    config = load_generation_config()
    street = LinearWay("street", "vehicular", LineString([(-60, 0), (60, 0)]), 6.0, "config_default")
    partition = build_partition(box(-50, -50, 50, 50), [], [street], [], config)
    islands = nav_islands(_base(street, partition=partition))  # type: ignore[arg-type]
    assert len(islands) == 1
    (island,) = islands
    assert [round(value, 6) for value in island.bounds] == [-50 + NAV_AGENT_RADIUS_M] * 2 + [50 - NAV_AGENT_RADIUS_M] * 2
    # The crossings at x = -18 and 18 m stay on the mesh; the rest of the street does not.
    assert island.covers(Point(-18, 0)) and island.covers(Point(18, 0))
    assert not island.covers(Point(0, 0))


def test_hunt_routes_spread_over_both_sides_of_a_crossed_street() -> None:
    """Each side holds the roster on its own; the crossings make it one island, so patrols use both."""
    config = load_generation_config()
    street = LinearWay("street", "vehicular", LineString([(-210, 0), (210, 0)]), 6.0, "config_default")
    partition = build_partition(box(-200, -200, 200, 200), [], [street], [], config)
    base = _base(street, partition=partition)
    free = free_ground(base, [])  # type: ignore[arg-type]
    islands = nav_islands(base)  # type: ignore[arg-type]
    routes, _ = hunt_routes(base, free, islands, component_holding(islands, (0.0, 20.0)), AI_COUNT, (0.0, 20.0))  # type: ignore[arg-type]
    assert len(routes) == AI_COUNT
    starts = [route["points"][0]["y"] for route in routes]
    assert sum(y > 0 for y in starts) >= 8 and sum(y < 0 for y in starts) >= 8


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


def test_the_playable_area_is_the_core_inside_its_wall() -> None:
    assert playable_area({"core": None}) is None
    assert playable_area({}) is None
    assert playable_area({"core": {"half_side_m": 60.0}}).bounds == (-59.6, -59.6, 59.6, 59.6)
    thick = playable_area({"core": {"half_side_m": 60.0}, "envelope": {"thickness_m": 1.0}})
    assert thick.bounds == (-59.0, -59.0, 59.0, 59.0)
    assert thick.area == pytest.approx(118.0 ** 2)  # mitred corners: still a square


def test_free_ground_and_islands_stay_inside_the_core() -> None:
    config = load_generation_config()
    street = LinearWay("street", "vehicular", LineString([(-210, 0), (210, 0)]), 6.0, "config_default")
    partition = build_partition(box(-200, -200, 200, 200), [], [street], [], config)
    base = _base(street, partition=partition)
    inside = box(-60, -60, 60, 60)
    islands = nav_islands(base, inside)  # type: ignore[arg-type]
    assert len(islands) == 1
    assert [round(v, 6) for v in islands[0].bounds] == [-60 + NAV_AGENT_RADIUS_M] * 2 + [60 - NAV_AGENT_RADIUS_M] * 2


def test_navigation_estimate_follows_the_adapter_rule() -> None:
    # 320.37 x 30.03 m away from the origin: sqrt(9620.71 / 320) = 5.483 beats the span over 400 (0.8).
    assert navigation_estimate([box(100, 200, 420.37, 230.03)]) == {
        "walkable_area_m2": 9620.7, "span_m": 320.4, "node_spacing_estimate_m": 5.48}
    assert navigation_estimate([box(200, 100, 230.03, 420.37)])["span_m"] == 320.4
    # A 2 400 m strip, 2 m wide: span / 400 = 6 beats sqrt(4800 / 320) = 3.87.
    assert navigation_estimate([box(0, 0, 2400, 2)])["node_spacing_estimate_m"] == 6.0
    # Two islands: their areas add, their span is the union's.
    two = navigation_estimate([box(0, 0, 10, 10), box(90, 0, 100, 10)])
    assert two == {"walkable_area_m2": 200.0, "span_m": 100.0, "node_spacing_estimate_m": 1.0}
    assert navigation_estimate([box(0, 0, 300, 300)])["node_spacing_estimate_m"] == pytest.approx(16.77, abs=0.01)
    # The span is the longer side.
    assert navigation_estimate([box(0, 0, 2, 2400)])["span_m"] == 2400.0
    # Never below 1 m, even for a tiny island.
    assert navigation_estimate([box(0, 0, 0.5, 0.5)])["node_spacing_estimate_m"] == 1.0


def test_spawns_stay_in_the_core_of_a_larger_site() -> None:
    """The plaza runs from the core to 400 m east; without the core the teams would stand near its middle."""
    from onward_generator.perf.layout import gameplay_layout

    config = load_generation_config()
    street = LinearWay("street", "vehicular", LineString([(-60, -200), (-60, -190)]), 2.0, "config_default")
    partition = build_partition(box(-60, -60, 400, 60), [], [street], [], config)
    base = _base(street, partition=partition)
    doc = gameplay_layout(base, {"objects": [], "core": {"half_side_m": 60.0}})  # type: ignore[arg-type]
    for team in ("attackers", "defenders"):
        assert all(abs(p["x"]) < 60 and abs(p["y"]) < 60 for p in doc[team]["points_m"])
    wide = gameplay_layout(base, {"objects": []})  # type: ignore[arg-type]
    assert wide["attackers"]["centre_m"][0] > 60


def _lamp(x: float, y: float, yaw: float = 0.0) -> dict:
    return {"type": "urban_element", "kind": "street_lamp", "position_m": [x, y], "yaw_deg": yaw}


def test_a_street_light_hangs_under_each_lamp_head_nearest_the_focus_first() -> None:
    manifest = {"objects": [_lamp(30.0, 0.0), _lamp(10.0, 0.0, 90.0), {"type": "urban_element", "kind": "bench", "position_m": [1.0, 1.0]},
                            {"type": "building", "kind": "street_lamp", "position_m": [2.0, 2.0]}]}
    assert street_lights(manifest, (0.0, 0.0)) == [10.0, 1.0, 4.2, 31.0, 0.0, 4.2]
    assert street_lights({"objects": []}, (0.0, 0.0)) == []


def test_street_lights_stop_at_the_cap_keeping_the_nearest() -> None:
    manifest = {"objects": [_lamp(float(i), 0.0) for i in range(STREET_LIGHTS_MAX + 5, 0, -1)]}
    lights = street_lights(manifest, (0.0, 0.0))
    assert len(lights) == 3 * STREET_LIGHTS_MAX and lights[0] == 2.0 and lights[-3] == float(STREET_LIGHTS_MAX + 1)


def test_the_time_of_day_names_exist_in_the_sdk_enums() -> None:
    assert TIME_OF_DAY_SETTINGS == {"day": ("Day", "UrbanDay1"), "night": ("Night", "UrbanNight1")}


def test_street_lights_round_to_the_centimetre_and_sort_by_distance_to_the_focus() -> None:
    near_the_focus = _lamp(90.123, 1.456)
    far_from_it = _lamp(10.0, 0.0)
    lights = street_lights({"objects": [far_from_it, near_the_focus]}, (100.0, 0.0))
    assert lights == [91.12, 1.46, 4.2, 11.0, 0.0, 4.2]


def test_a_lamp_with_no_yaw_points_along_x_and_a_height_in_its_position_is_ignored() -> None:
    lamp = {"type": "urban_element", "kind": "street_lamp", "position_m": [5.0, 6.0, 99.0]}
    assert street_lights({"objects": [lamp]}, (0.0, 0.0)) == [6.0, 6.0, 4.2]


def test_a_manifest_with_no_objects_has_no_street_lights() -> None:
    assert street_lights({}, (0.0, 0.0)) == []
