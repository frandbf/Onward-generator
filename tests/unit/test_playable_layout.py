"""Playable layout around the Murcia origin: Hunt anchors, SDK audio names, urban day."""

from __future__ import annotations

import dataclasses
import math

import pytest
from shapely import LineString, MultiLineString, Point, Polygon, STRtree, box, union_all
from shapely.geometry.base import BaseGeometry

from onward_generator.export.package import _check_climbs, _check_layout
from onward_generator.generation.config import load_generation_config
from onward_generator.generation.surfaces import LinearWay, build_partition
from onward_generator.perf.layout import (
    AI_COUNT,
    AMMO_COUNT,
    AMMO_SPAWN_CLEAR_M,
    BLOCK_GAP_M,
    CLIMB_BALCONY_CLEAR_M,
    GAMEPLAY_VERSION,
    GRID_M,
    MIN_SPAWN_SEPARATION_M,
    MODE_CODES,
    NAV_AGENT_RADIUS_M,
    ROOF_WIND_MAX,
    ROUTE_CLEARANCE_M,
    ROUTE_POINTS,
    ROUTE_STEP_M,
    SPAWN_SEPARATION_M,
    STREET_LIGHTS_MAX,
    TEAM_SEPARATION_M,
    TIME_OF_DAY_SETTINGS,
    LayoutError,
    _block,
    _board_line,
    _defenders,
    _patrol_lines,
    _routes,
    _sample_route,
    ammo_points,
    climb_ground,
    climb_keep_out,
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
    reachable_ground,
    roof_wind,
    route_islands,
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
    assert _check_layout({**doc, "audio_roof_wind_m": [1.0, 2.0, 3.0]}) == []
    for bad in ([1.0, 2.0], "x", [1.0, 2.0, True]):
        assert "layout: audio_roof_wind_m must be flat x, y, z triples" in _check_layout({**doc, "audio_roof_wind_m": bad})
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


def test_patrol_ground_leaves_a_narrow_strip_along_a_wall() -> None:
    """Plan Q1b: a strip 0.8 m wide off a square is navmesh in 2D and not in Unity's voxels; patrols keep off it."""
    square = box(0.0, 0.0, 20.0, 20.0)
    strip = box(20.0, 9.6, 40.0, 10.4)
    far = box(60.0, 0.0, 90.0, 30.0)
    parts, home = route_islands([union_all([square, strip]), far], (5.0, 5.0))
    assert ROUTE_CLEARANCE_M == 0.5
    assert home.bounds[:2] == pytest.approx((0.5, 0.5), abs=1e-6) and home.bounds[2] < 20.0
    assert not home.covers(Point(25.0, 10.0))
    # Every island shrinks, the far one too, and stays a separate piece.
    assert len(parts) == 2 and max(part.bounds[2] for part in parts) == pytest.approx(89.5)


def test_patrol_home_is_the_largest_piece_of_the_uplink_island() -> None:
    """A neck narrower than twice the clearance splits the island; the larger side is home, wherever the Uplink is."""
    small = box(0.0, 0.0, 6.0, 6.0)
    neck = box(6.0, 2.6, 8.0, 3.4)
    large = box(8.0, 0.0, 30.0, 20.0)
    parts, home = route_islands([union_all([small, neck, large])], (3.0, 3.0))
    assert len(parts) == 2
    assert home.bounds[1:] == pytest.approx((0.5, 29.5, 19.5), abs=1e-6) and home.bounds[0] > 7.0
    assert not home.covers(Point(3.0, 3.0))


def test_patrol_ground_refuses_an_uplink_island_with_no_room() -> None:
    with pytest.raises(LayoutError, match=r"leaves no patrol ground 0.5 m from its edges"):
        route_islands([box(0.0, 0.0, 0.9, 10.0)], (0.45, 5.0))


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


def test_a_wall_lamp_lights_under_its_lantern_0_57_m_out_of_the_facade() -> None:
    """Plan Q6: the lights follow the wall lamps too, under the lantern rather than a pole's head."""
    wall = {"type": "urban_element", "kind": "wall_lamp", "position_m": [20.0, 5.0], "yaw_deg": 90.0}
    assert street_lights({"objects": [wall, _lamp(40.0, 0.0)]}, (0.0, 0.0)) == [20.0, 5.57, 4.2, 41.0, 0.0, 4.2]


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


def _climbable(index: int, x: float, y: float, *, access: bool = True, height: float = 12.0) -> list[dict]:
    bid = f"b{index}"
    return [
        {"id": f"building/{bid}", "type": "building", "height": {"height_m": height}},
        {"id": f"interior/{bid}", "type": "interior", "source_ids": [bid], "roof_access": access,
         "connections": [{"start_m": [0.0, 0.0]}, {"start_m": [-1.0, -1.0]}, {"start_m": [x, y]}]},
    ]


def test_roof_wind_sits_above_the_last_flight_of_a_reachable_roof() -> None:
    manifest = {"objects": [*_climbable(1, 50.0, 0.0, height=9.0), *_climbable(2, 10.0, 0.0), *_climbable(3, 90.0, 0.0, access=False)]}
    assert roof_wind(manifest, (0.0, 0.0)) == [10.0, 0.0, 13.5, 50.0, 0.0, 10.5]
    assert roof_wind({}, (0.0, 0.0)) == []


def test_roof_wind_goes_nearest_the_focus_first_and_is_rounded() -> None:
    manifest = {"objects": [*_climbable(1, 10.123, 5.678, height=9.123), *_climbable(2, 90.0, 0.0)]}
    assert roof_wind(manifest, (100.0, 0.0)) == [90.0, 0.0, 13.5, 10.12, 5.68, 10.62]


def test_roof_wind_keeps_its_spacing_and_its_cap() -> None:
    near = {"objects": [*_climbable(1, 0.0, 0.0), *_climbable(2, 34.9, 0.0), *_climbable(3, 35.0, 0.0)]}
    assert roof_wind(near, (0.0, 0.0))[::3] == [0.0, 35.0]
    many = {"objects": [row for k in range(ROOF_WIND_MAX + 3) for row in _climbable(k, 40.0 * k, 0.0)]}
    assert roof_wind(many, (0.0, 0.0))[::3] == [40.0 * k for k in range(ROOF_WIND_MAX)]


def test_roof_wind_also_covers_the_roofs_a_ladder_or_a_lift_reaches() -> None:
    manifest = {"objects": _climbable(1, 90.0, 0.0)}
    assert roof_wind(manifest, (0.0, 0.0), [(40.0, 0.0, 8.0), (41.0, 0.0, 8.0)]) == [40.0, 0.0, 9.5, 90.0, 0.0, 13.5]


def test_a_manifest_with_no_objects_has_no_street_lights() -> None:
    assert street_lights({}, (0.0, 0.0)) == []


def _layout_with(**climbs: object) -> list[str]:
    return [p for p in _check_climbs(climbs)]


def test_the_package_checks_the_ladders_and_lifts() -> None:
    ladder = {"x": 1.0, "y": 2.0, "yaw": 0.0, "height_m": 9.0, "parapet_m": 0.7, "roof_x": 1.0, "roof_y": 3.0}
    lift = {**ladder, "roof_yaw": 180.0}
    assert _layout_with() == [] and _layout_with(ladders=[ladder] * 3, lifts=[lift] * 3) == []
    assert _layout_with(ladders=[ladder] * 9) == []  # 3 street ladders and 6 roof links
    assert _layout_with(ladders=[ladder] * 10) == ["layout: ladders must be a list of at most 9"]
    assert _layout_with(lifts=[lift] * 4) == ["layout: lifts must be a list of at most 3"]
    assert _layout_with(lifts="x") == ["layout: lifts must be a list of at most 3"]
    needs_ladder = "layout: each of ladders needs x, y, yaw, height_m, parapet_m, roof_x, roof_y and a positive height"
    needs_lift = "layout: each of lifts needs x, y, yaw, height_m, roof_x, roof_y, roof_yaw and a positive height"
    assert _layout_with(lifts=[ladder]) == [needs_lift]
    assert _layout_with(ladders=[ladder, {**ladder, "x": True}, "x"]) == [needs_ladder]
    assert _layout_with(ladders=["x"]) == [needs_ladder]
    assert _layout_with(ladders="x", lifts="x") == ["layout: ladders must be a list of at most 9", "layout: lifts must be a list of at most 3"]
    assert _layout_with(ladders=[{**ladder, "height_m": 0}]) == [needs_ladder]
    assert _layout_with(ladders=[{**ladder, "height_m": 0.1}]) == []
    # A roof link stands on a lower roof: base_m from 0 to its height.
    for base, ok in ((0.0, True), (9.0, True), (6.0, True), (9.01, False), (-0.01, False), (True, False), ("6", False)):
        assert (_layout_with(ladders=[{**ladder, "base_m": base}]) == []) is ok, base


def test_the_climb_keep_out_covers_points_street_doors_and_routes() -> None:
    manifest = {"objects": [
        {"type": "interior", "connections": [{"from": "exterior", "position_m": [100.0, 0.0]}, {"from": "room", "position_m": [200.0, 0.0]}]},
        {"type": "interior"},
        {"type": "building", "connections": [{"from": "exterior", "position_m": [300.0, 0.0]}]},
    ]}
    routes = [{"points": [{"x": 0.0, "y": 50.0}, {"x": 10.0, "y": 50.0}]}]
    keep_out = climb_keep_out([(0.0, 0.0)], routes, manifest)
    for inside in ((2.9, 0.0), (100.0, 2.9), (5.0, 51.4)):
        assert keep_out.contains(Point(inside)), inside
    for outside in ((3.1, 0.0), (100.0, 3.1), (5.0, 51.6), (200.0, 0.0), (300.0, 0.0)):
        assert not keep_out.contains(Point(outside)), outside
    assert climb_keep_out([(0.0, 0.0)], [], {}).contains(Point(2.9, 0.0))


def test_the_climb_keep_out_covers_every_ground_floor_door_of_a_facade() -> None:
    """Barcelona Sarria: two ladders stood over a facade door of a building with no interior."""
    manifest = {"objects": [
        {"type": "building", "facade": {"doors_m": [[400.0, 0.0], [400.0, 20.0]]}},
        {"type": "building", "facade": {"door": "none"}},  # a manifest from before doors_m
        {"type": "building"},
        {"type": "interior", "facade": {"doors_m": [[600.0, 0.0]]}},
    ]}
    keep_out = climb_keep_out([(0.0, 0.0)], [], manifest)
    for inside in ((402.9, 0.0), (400.0, 22.9)):
        assert keep_out.contains(Point(inside)), inside
    for outside in ((403.1, 0.0), (600.0, 0.0)):
        assert not keep_out.contains(Point(outside)), outside


def test_the_climb_keep_out_covers_shop_signs_window_vinyls_and_street_plates() -> None:
    """Rue Mouffetard: a cabin hid a shop's fascia and stood against its window."""
    sign = {"centre_m": [100.0, 0.0, 2.8], "normal": [0.0, -1.0], "width_m": 4.0, "height_m": 0.4}
    vinyl = {"centre_m": [200.0, 0.0, 1.6], "normal": [1.0, 0.0], "width_m": 2.0, "glass": "shop_glass"}
    manifest = {"objects": [
        {"type": "building", "facade": {"shops": [{"sign": sign, "vinyls": [vinyl]}, {"sign": None, "vinyls": []}, {}]}},
        {"type": "building", "facade": {"shops": [{"sign": {"centre_m": [300.0, 0.0]}}]}},  # no normal: skipped
        {"type": "street_sign", "position_m": [0.0, 300.0, 2.5]},
        {"type": "street_sign"},
    ]}
    keep_out = climb_keep_out([(-500.0, -500.0)], [], manifest)
    # The fascia runs 4 m along x at y = 0: 1.5 m around its whole length, not just its centre.
    for inside in ((101.9, 1.4), (98.1, -1.4), (103.4, 0.0), (200.0, 2.4), (200.0, -2.4), (201.4, 0.0), (0.0, 301.4)):
        assert keep_out.contains(Point(inside)), inside
    for outside in ((103.6, 0.0), (100.0, 1.6), (200.0, 2.6), (201.6, 0.0), (0.0, 301.6), (300.0, 0.0)):
        assert not keep_out.contains(Point(outside)), outside
    assert _board_line({"centre_m": [0.0, 0.0], "normal": [0.0, -1.0], "width_m": 2.0}).length == pytest.approx(2.0)
    assert sorted(_board_line({"centre_m": [5.0, 5.0], "normal": [1.0, 0.0], "width_m": 4.0}).coords) == [(5.0, 3.0), (5.0, 7.0)]
    # A normal with a vertical part keeps only its plane part.
    assert sorted(_board_line({"centre_m": [5.0, 5.0, 2.8], "normal": [1.0, 0.0, 0.0], "width_m": 4.0}).coords) == [(5.0, 3.0), (5.0, 7.0)]


def test_the_climb_keep_out_covers_every_column_of_balconies() -> None:
    """Rue Mouffetard: a ladder climbed a wall through a column of four balconies."""
    manifest = {"objects": [
        {"type": "building", "facade": {"balconies_m": [[[100.0, 0.0], [101.5, 0.0]], [[200.0, 0.0], [200.0, 1.5]]]}},
        {"type": "building", "facade": {}},  # a manifest from before facade.balconies_m
        {"type": "interior"},
    ]}
    assert CLIMB_BALCONY_CLEAR_M == 0.5
    keep_out = climb_keep_out([(-500.0, -500.0)], [], manifest)
    for inside in ((100.75, -0.45), (99.6, 0.0), (101.9, 0.0), (200.45, 0.75), (200.0, 1.95)):
        assert keep_out.contains(Point(inside)), inside
    for outside in ((100.75, -0.55), (99.4, 0.0), (102.1, 0.0), (200.0, 2.1)):
        assert not keep_out.contains(Point(outside)), outside


def test_the_climb_keep_out_covers_the_overgrow_props_but_not_flat_growth() -> None:
    """On the headset a street cabin held a pile of bricks: ``overgrow`` places its props before the layout picks the cabins."""
    manifest = {"objects": [
        {"type": "overgrowth", "kind": "waste", "role": "debris", "position_m": [50.0, 0.0, 0.0]},
        {"type": "overgrowth", "kind": "tree", "position_m": [0.0, 80.0, 0.0]},
        {"type": "overgrowth", "kind": "vegetation", "role": "tree", "position_m": [0.0, -80.0, 0.0]},
        *({"type": "overgrowth", "kind": kind, "position_m": [-50.0, y, 0.0]} for kind, y in (("grass", 0.0), ("vine", 20.0), ("moss", 40.0))),
        {"type": "urban_element", "kind": "waste", "position_m": [-80.0, 0.0]},
    ]}
    keep_out = climb_keep_out([(500.0, 500.0)], [], manifest)
    for inside in ((50.9, 0.0), (1.9, 80.0), (1.9, -80.0)):
        assert keep_out.contains(Point(inside)), inside
    for outside in ((51.1, 0.0), (2.1, 80.0), (2.1, -80.0), (-50.0, 0.0), (-50.0, 20.0), (-50.0, 40.0), (-80.0, 0.0)):
        assert not keep_out.contains(Point(outside)), outside



def test_no_ammo_crate_lands_on_the_attackers_block() -> None:
    plaza = box(0, 0, 40, 12)
    # The attackers' block stands on the pole of the plaza, where the first crate used to go.
    pole = (20.0, 6.0)
    spawns = _block(*pole)
    ammo = ammo_points(plaza, [], spawns, pole)
    assert len(ammo) == AMMO_COUNT
    assert min(math.dist(a, s) for a in ammo for s in spawns) >= AMMO_SPAWN_CLEAR_M
    assert all(plaza.contains(Point(a)) for a in ammo)


def test_ammo_keeps_clear_of_the_ai_starts_while_there_is_room() -> None:
    plaza = box(0, 0, 40, 12)
    ai = [(5.0, 6.0)]
    ammo = ammo_points(plaza, ai, [(35.0, 6.0)], (20.0, 6.0))
    assert min(math.dist(a, ai[0]) for a in ammo) >= 3.0
    # A strip the AI circles fill still gets its crates, but never next to the spawn.
    strip = box(0, 0, 60, 2)
    crowded = ammo_points(strip, [(x, 1.0) for x in range(0, 61, 4)], [(58.0, 1.0)], (0.0, 1.0))
    assert len(crowded) == AMMO_COUNT
    assert all(math.dist(a, (58.0, 1.0)) >= AMMO_SPAWN_CLEAR_M for a in crowded)



def test_ammo_crates_are_6_m_apart() -> None:
    # 20 m holds four crates 6 m apart, not 7 m.
    ammo = ammo_points(box(0, 0, 20, 1), [], [(40.0, 0.5)], (0.0, 0.5))
    assert len(ammo) == AMMO_COUNT
    assert min(math.dist(a, b) for i, a in enumerate(ammo) for b in ammo[i + 1:]) >= 6.0


def test_ammo_needs_only_3_m_from_an_ai_start() -> None:
    # Four 1 m squares, each with an AI start 2.9 m from its centre: only the far side of each square is 3 m clear.
    squares = [box(x, 0, x + 1, 1) for x in (0.0, 20.0, 40.0, 60.0)]
    ai = [(x + 0.5 - 2.9, 0.5) for x in (0.0, 20.0, 40.0, 60.0)]
    ammo = ammo_points(union_all(squares), ai, [(100.0, 0.5)], (0.0, 0.5))
    assert len(ammo) == AMMO_COUNT
    assert min(math.dist(a, s) for a in ammo for s in ai) >= 3.0



def test_the_fixture_keeps_the_defenders_within_30_m() -> None:
    street = box(0, 0, 200, 10)
    x, y = _defenders(street, (6.0, 4.0), farthest=False)
    assert math.dist((x, y), (6.0, 4.0)) <= TEAM_SEPARATION_M
    assert (x, y) == (36.0, 4.0)  # on the line: 30 m exactly, the north tie-break leaves it there


def test_the_playable_defenders_go_to_the_far_end_of_the_area() -> None:
    street = box(0, 0, 200, 10)
    x, y = _defenders(street, (6.0, 4.0), farthest=True)
    # The block (4 x 2 points, 1.2 m apart, so 1.8 m and 0.6 m from its centre) must fit inside the street.
    assert (x, y) == (198.0, 8.0)


def test_the_playable_layout_refuses_teams_closer_than_30_m() -> None:
    plaza = box(0, 0, 32, 10)
    # The farthest block that fits is (30, 8), 24.3 m from (6, 4).
    with pytest.raises(LayoutError, match=r"^the largest free area holds the teams only 24\.3 m apart; 30 m are required$"):
        _defenders(plaza, (6.0, 4.0), farthest=True)
    # Exactly 30 m is enough: in a 3 m strip a block fits only on y = 2, and x = 34 is the last column that fits.
    assert _defenders(box(0, 0, 35.9, 3), (4.0, 2.0), farthest=True) == (34.0, 2.0)



@pytest.mark.parametrize(
    ("area", "attackers", "expected"),
    [
        # A long arm north and a short one east: north is farther, though east runs farther in x.
        (box(100, 0, 160, 6).union(box(100, 0, 106, 100)), (104.0, 4.0), (102.0, 98.0)),
        # A long arm east and a short one north.
        (box(100, 100, 260, 106).union(box(100, 100, 106, 160)), (104.0, 104.0), (258.0, 102.0)),
        # Bounds off the 2 m grid: the grid stays on whole multiples of 2 m.
        (box(101, 0, 301, 10), (107.0, 4.0), (298.0, 8.0)),
        # Two points tie on distance: the northern one wins.
        (box(0, 0, 200, 10), (6.0, 5.0), (198.0, 8.0)),
        # Four tie: north, then east.
        (box(0, 0, 200, 10), (100.0, 5.0), (198.0, 8.0)),
    ],
)
def test_the_farthest_block_is_measured_in_both_axes(area: BaseGeometry, attackers: tuple[float, float], expected: tuple[float, float]) -> None:
    assert _defenders(area, attackers, farthest=True) == expected



def test_the_fixture_needs_two_block_widths_between_the_teams() -> None:
    # Two block widths are 2 x 4 columns x 1.2 m = 9.6 m. In a 3 m strip a block fits only on y = 2.
    assert _defenders(box(0, 0, 14.2, 3), (2.4, 2.0), farthest=False) == (12.0, 2.0)  # 9.6 m: enough
    with pytest.raises(LayoutError, match=r"^no second spawn block fits apart from the first$"):
        _defenders(box(0, 0, 14.0, 3), (4.0, 2.0), farthest=False)  # 8 m
    with pytest.raises(LayoutError, match=r"^no second spawn block fits near the first$"):
        _defenders(box(0, 0, 3.0, 3.0), (1.5, 1.5), farthest=False)


@pytest.mark.parametrize(("gap_m", "reached"), [(0.6, False), (0.8, True)])
def test_a_courtyard_is_reached_through_a_gap_a_player_fits(gap_m: float, reached: bool) -> None:
    config = load_generation_config()
    street = LinearWay("street", "vehicular", LineString([(-60, 0), (60, 0)]), 6.0, "config_default")
    # A block closed round a 20 m courtyard, open on its south side by ``gap_m``.
    ring = box(10, 10, 40, 40).difference(box(15, 15, 35, 35)).difference(box(25 - gap_m / 2, 9, 25 + gap_m / 2, 16))
    partition = build_partition(box(-50, -50, 50, 50), [ring], [street], [], config)
    ground = reachable_ground(_base(street, partition=partition), {}, (0.0, -20.0))  # type: ignore[arg-type]
    assert ground.covers(Point(0, 20)) and ground.covers(Point(0, 0))  # across the carriageway
    assert ground.covers(Point(25, 25)) is reached
    assert not ground.intersects(box(10.5, 10.5, 14.5, 39.5))  # the buildings


def test_reachable_ground_stays_inside_the_core() -> None:
    config = load_generation_config()
    street = LinearWay("street", "vehicular", LineString([(-210, 0), (210, 0)]), 6.0, "config_default")
    partition = build_partition(box(-200, -200, 200, 200), [], [street], [], config)
    ground = reachable_ground(_base(street, partition=partition), {"core": {"half_side_m": 60.0}}, (0.0, -20.0))  # type: ignore[arg-type]
    assert ground.covers(Point(59.5, 0)) and not ground.covers(Point(59.7, 0))  # the core less its 0.4 m wall


def test_climb_ground_keeps_the_carriageway_and_leaves_out_the_urban_elements() -> None:
    """ADR-029: a ladder's foot or a cabin may stand on the carriageway a player walks across, not on a bench."""
    config = load_generation_config()
    street = LinearWay("street", "vehicular", LineString([(-60, 0), (60, 0)]), 6.0, "config_default")
    partition = build_partition(box(-50, -50, 50, 50), [], [street], [], config)
    base = _base(street, partition=partition)
    bench = {"type": "urban_element", "position_m": [20.0, 10.0]}
    ground = climb_ground(base, {"objects": [bench]}, (0.0, -20.0))  # type: ignore[arg-type]
    assert ground.covers(Point(0, 0)) and ground.covers(Point(0, 2.9))  # the carriageway
    assert not ground.intersects(Point(20, 10).buffer(0.95)) and ground.covers(Point(21.1, 10))  # the bench's 1 m circle
    assert climb_ground(base, {}, (0.0, -20.0)).covers(Point(20, 10))  # type: ignore[arg-type]
    # The very hole free_ground cuts for the bench, polygon and all.
    near = box(17, 7, 23, 13)
    assert ground.intersection(near).equals(free_ground(base, [(20.0, 10.0)]).intersection(near))  # type: ignore[arg-type]


# ------------------------------------------------------------------------------------------------ balanced spawns (plan C6)


def _plaza_base(site: BaseGeometry | None = None) -> object:
    street = LinearWay("street", "vehicular", LineString([(-60, -200), (-60, -190)]), 2.0, "config_default")
    partition = build_partition(site if site is not None else box(-60, -60, 60, 60), [], [street], [], load_generation_config())
    return _base(street, partition=partition)


L_SITE = Polygon([(0, 0), (120, 0), (120, 12), (12, 12), (12, 80), (0, 80)])


def test_defender_candidates_follow_the_farthest_first_order() -> None:
    from onward_generator.perf.layout import _defender_candidates

    strip = box(0, 0, 80, 3)
    found = _defender_candidates(strip, (8.0, 2.0), 10)
    # 10 m apart from each other, and the last one exactly 30 m from the attackers.
    assert found == [(78.0, 2.0), (68.0, 2.0), (58.0, 2.0), (48.0, 2.0), (38.0, 2.0)]
    assert all(type(v) is float for point in found for v in point)
    assert found[0] == _defenders(strip, (8.0, 2.0), farthest=True)
    assert _defender_candidates(strip, (8.0, 2.0), 4) == found[:4]
    assert _defender_candidates(strip, (8.0, 2.0), 1) == found[:1]
    assert _defender_candidates(strip, (8.0, 2.0), 0) == []
    # Ties on the distance go north, then east, as in _defenders.
    assert _defender_candidates(box(-40, -40, 40, 40), (0.0, 0.0), 2) == [(38.0, 38.0), (-38.0, 38.0)]
    # One point fits 30 m away; none fits farther.
    assert _defender_candidates(box(0, 0, 35.9, 3), (4.0, 2.0), 4) == [(34.0, 2.0)]
    assert _defender_candidates(box(0, 0, 33.9, 3), (4.0, 2.0), 4) == []
    for x, y in _defender_candidates(PLAZA.buffer(-1.5), (0.0, 0.0), 4):
        assert math.hypot(x, y) >= TEAM_SEPARATION_M
    # Distances are measured in both axes from the attackers' own centre.
    assert _defender_candidates(box(-40, -40, 40, 40), (10.0, -20.0), 1) == [(-38.0, 38.0)]
    column = box(-2, 0, 2, 80)
    assert _defender_candidates(column, (0.0, 8.0), 10) == [(0.0, 78.0), (0.0, 68.0), (0.0, 58.0), (0.0, 48.0), (0.0, 38.0)]
    plaza = _defender_candidates(PLAZA.buffer(-1.5), (0.0, 0.0), 4)
    assert len(plaza) == 4
    assert all(math.dist(p, q) >= BLOCK_GAP_M for i, p in enumerate(plaza) for q in plaza[i + 1:])


def test_a_placement_sets_the_defenders_and_the_uplink() -> None:
    from onward_generator.gameplay.placement import Placement
    from onward_generator.perf.layout import gameplay_layout

    base = _plaza_base()
    manifest: dict = {"objects": []}
    today = gameplay_layout(base, manifest, farthest=True)  # type: ignore[arg-type]
    assert gameplay_layout(base, manifest, farthest=True, placement=None) == today  # type: ignore[arg-type]
    a = (today["attackers"]["centre_m"][0], today["attackers"]["centre_m"][1])
    moved = gameplay_layout(base, manifest, farthest=True, placement=Placement(a, (40.0, -40.0), (10.0, -10.0), a, a))  # type: ignore[arg-type]
    assert moved["defenders"]["centre_m"] == [40.0, -40.0] and moved["uplink_m"] == [10.0, -10.0]
    assert moved["attackers"] == today["attackers"]
    assert moved["defenders"]["points_m"][0] == {"x": 38.2, "y": -40.6}
    assert {k: v for k, v in moved.items() if k not in ("defenders", "uplink_m")} == {k: v for k, v in today.items() if k not in ("defenders", "uplink_m")}
    # Exactly 30 m apart is enough.
    assert gameplay_layout(base, manifest, placement=Placement(a, (30.0, 0.0), (15.0, 0.0), a, a))["defenders"]["centre_m"] == [30.0, 0.0]  # type: ignore[arg-type]
    # Since the C6 amendment of 11 November 2026 the attackers may move too, and the map quad follows them.
    shifted = gameplay_layout(base, manifest, placement=Placement((-40.0, 40.0), (40.0, -40.0), (10.0, -10.0), a, a))  # type: ignore[arg-type]
    assert shifted["attackers"]["centre_m"] == [-40.0, 40.0] and shifted["attackers"]["points_m"][0] == {"x": -41.8, "y": 39.4}
    assert shifted["map_quad_m"] == [-40.0, 37.6]
    for placement, message in (
        (Placement((57.0, 0.0), (-40.0, 0.0), (10.0, 0.0), a, a), r"^the placed attackers' block does not fit the free area$"),
        (Placement((-29.9, 0.0), (0.0, 0.0), (10.0, 0.0), a, a), r"^the placement holds the teams only 29.9 m apart; 30 m are required$"),
        (Placement(a, (29.9, 0.0), (10.0, 0.0), a, a), r"^the placement holds the teams only 29.9 m apart; 30 m are required$"),
        (Placement(a, (57.0, 0.0), (10.0, 0.0), a, a), r"^the placed defenders' block does not fit the free area$"),
        (Placement(a, (40.0, -40.0), (58.6, 0.0), a, a), r"^the placed Uplink is not on the free area$"),
    ):
        with pytest.raises(LayoutError, match=message):
            gameplay_layout(base, manifest, placement=placement)  # type: ignore[arg-type]
    # The boundary of the free area is on it.
    assert gameplay_layout(base, manifest, placement=Placement(a, (40.0, -40.0), (58.5, 0.0), a, a))["uplink_m"] == [58.5, 0.0]  # type: ignore[arg-type]


def test_placed_second_blocks_must_fit_and_keep_their_gap() -> None:
    from onward_generator.gameplay.placement import Placement
    from onward_generator.perf.layout import _checked_second_blocks

    area = PLAZA.buffer(-1.0)
    a, d, u = (0.0, 0.0), (40.0, 0.0), (20.0, 0.0)
    assert _checked_second_blocks(area, Placement(a, d, u, (0.0, -10.0), (40.0, -10.0))) == ((0.0, -10.0), (40.0, -10.0))
    assert _checked_second_blocks(area, Placement(a, d, u, (0.0, -10.0 + 5e-7), (40.0, -10.0)))[0] == (0.0, -10.0 + 5e-7)
    # Exactly the gap less the 1e-6 m tolerance, as extra_spawn_block.
    edge = (0.0, -(BLOCK_GAP_M - 1e-6))
    assert math.dist(a, edge) == BLOCK_GAP_M - 1e-6
    assert _checked_second_blocks(area, Placement(a, d, u, edge, (40.0, -10.0)))[0] == edge
    for attack_b, defend_b, message in (
        ((0.0, -9.9), (40.0, -10.0), r"^the placed second block at \(0.0, -9.9\) is closer than 10 m to another block$"),
        ((0.0, -10.0), (9.9, -10.0), r"^the placed second block at \(9.9, -10.0\) is closer than 10 m to another block$"),
        ((0.0, -10.0), (40.0, 9.9), r"^the placed second block at \(40.0, 9.9\) is closer than 10 m to another block$"),
        ((78.0, 0.0), (40.0, -10.0), r"^the placed second block at \(78.0, 0.0\) does not fit the Uplink's ground$"),
        ((0.0, -10.0), (40.0, -78.5), r"^the placed second block at \(40.0, -78.5\) does not fit the Uplink's ground$"),
    ):
        with pytest.raises(LayoutError, match=message):
            _checked_second_blocks(area, Placement(a, d, u, attack_b, defend_b))


@pytest.mark.parametrize("site", [None, L_SITE])
def test_the_balanced_uplink_stays_on_the_spawn_area_and_the_island_of_today(site: BaseGeometry | None) -> None:
    from onward_generator.gameplay.metrics import SNAP_M, nav_graph
    from onward_generator.gameplay.placement import Placement
    from onward_generator.perf.layout import CLEARANCE_M, choose_placement, gameplay_layout

    base = _plaza_base(site)
    manifest: dict = {"objects": [], "core": {"half_side_m": 60.0}} if site is None else {"objects": []}
    placement, choice = choose_placement(base, manifest)  # type: ignore[arg-type]
    assert (placement, choice) == choose_placement(base, manifest)  # type: ignore[arg-type]
    assert isinstance(placement, Placement) and choice.reason is None and choice.index > 0
    assert all(type(v) is float for centre in (placement.uplink, *placement.blocks().values()) for v in centre)
    assert choice.chosen.diff_pct is not None and choice.baseline.diff_pct is not None
    assert choice.chosen.diff_pct < choice.baseline.diff_pct
    today = gameplay_layout(base, manifest, farthest=True)  # type: ignore[arg-type]
    # The attackers may move (C6 amendment of 11 November 2026), but their block stays on the spawn area.
    from onward_generator.perf.layout import _block, _fits, _spawn_site

    area, _ = _spawn_site(base, manifest)  # type: ignore[arg-type]
    assert _fits(area, _block(*placement.attack)) and _fits(area, _block(*placement.defend))
    inside = playable_area(manifest)
    free = free_ground(base, [])  # type: ignore[arg-type]
    if inside is not None:
        free = free.intersection(inside)
        assert inside.covers(Point(placement.uplink))
    assert free.buffer(-CLEARANCE_M).covers(Point(placement.uplink))
    islands = nav_islands(base, inside)  # type: ignore[arg-type]
    assert component_holding(islands, tuple(today["uplink_m"])).covers(Point(placement.uplink))
    graph = nav_graph(islands)
    for centre in (placement.uplink, *placement.blocks().values()):
        assert min(math.dist(centre, (float(x), float(y))) for x, y in graph.xy) <= SNAP_M
    assert math.dist(placement.attack, placement.defend) >= TEAM_SEPARATION_M
    centres = list(placement.blocks().values())
    assert all(math.dist(p, q) >= BLOCK_GAP_M - 1e-6 for i, p in enumerate(centres) for q in centres[i + 1:])


def test_candidates_whose_second_blocks_do_not_fit_are_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    import onward_generator.perf.layout as layout

    base = _plaza_base()
    manifest: dict = {"objects": []}
    real = layout._second_blocks
    calls: list[tuple[float, float]] = []

    def first_only(parts: list, a: tuple[float, float], d: tuple[float, float], uplink: tuple[float, float]) -> object:
        calls.append(uplink)
        if len(calls) > 1:
            raise LayoutError("no second spawn block fits for Assault")
        return real(parts, a, d, uplink)

    monkeypatch.setattr(layout, "_second_blocks", first_only)
    monkeypatch.setattr(layout, "K_SITES", 1)  # the candidates of the first C6 rule only: no pair of spread sites
    placement, choice = layout.choose_placement(base, manifest)  # type: ignore[arg-type]
    assert placement is None and choice.index == 0
    assert choice.reason == "kept the current placement: no candidate beats the current placement"
    # Candidate 0 and its even Uplink, then the midpoint of each other defenders' position.
    assert choice.count == len(calls) == 2 + 3

    calls.clear()

    def never(parts: list, a: tuple[float, float], d: tuple[float, float], uplink: tuple[float, float]) -> object:
        calls.append(uplink)
        raise LayoutError("no second spawn block fits for Assault")

    monkeypatch.setattr(layout, "_second_blocks", never)
    placement, choice = layout.choose_placement(base, manifest)  # type: ignore[arg-type]
    assert placement is None and choice.count == 4
    assert choice.reason == "kept the current placement: candidate 0 cannot be scored (no second spawn block fits for Assault)"


def test_balanced_layout_keeps_the_current_placement_when_it_must(monkeypatch: pytest.MonkeyPatch) -> None:
    import onward_generator.perf.layout as layout
    from onward_generator.gameplay.metrics import GameplayError
    from onward_generator.gameplay.placement import Choice, Placement, Score

    built: list[object] = []

    def fake_layout(base: object, manifest: object, heights: object, **kwargs: object) -> dict:
        assert (base, manifest, heights) == ("base", {"m": 1}, {"h": 2})
        built.append(kwargs.get("placement"))
        if kwargs.get("placement") is not None and kwargs["placement"].uplink == (9.0, 9.0):  # type: ignore[attr-defined]
            raise LayoutError("no ground left for Escort, Evac and Retrieval")
        return {"placement": kwargs.get("placement"), "time_of_day": kwargs["time_of_day"], "storm": kwargs["storm"], "footprints": kwargs["footprints"]}

    monkeypatch.setattr(layout, "playable_layout", fake_layout)
    tail = " (estimated from the 2D navmesh)"
    good = Placement((0.0, 0.0), (40.0, 0.0), (20.0, 0.0), (0.0, -10.0), (40.0, -10.0))
    chosen = Choice(3, 8, Score(0, 20.0, 3, 0.2), Score(3, 2.0, 3, 0.2))

    def returning(result: object) -> object:
        return lambda base, manifest: result

    monkeypatch.setattr(layout, "choose_placement", returning((good, chosen)))
    doc, line = layout.balanced_playable_layout("base", {"m": 1}, {"h": 2}, time_of_day="night", storm=True, footprints={"a": None})  # type: ignore[arg-type,dict-item]
    assert doc == {"placement": good, "time_of_day": "night", "storm": True, "footprints": {"a": None}}
    assert line.startswith("placement: balanced, candidate 3 of 8, contact diff 20.00 -> 2.00 %")

    kept = Choice(0, 8, Score(0, 20.0, 3, 0.2), Score(0, 20.0, 3, 0.2), "kept the current placement: no candidate beats the current placement")
    monkeypatch.setattr(layout, "choose_placement", returning((None, kept)))
    built.clear()
    doc, line = layout.balanced_playable_layout("base", {"m": 1}, {"h": 2}, footprints={"b": None})  # type: ignore[arg-type,dict-item]
    assert doc["placement"] is None and built == [None] and (doc["time_of_day"], doc["storm"], doc["footprints"]) == ("day", False, {"b": None})
    assert line == "placement: kept the current placement: no candidate beats the current placement" + tail

    monkeypatch.setattr(layout, "choose_placement", returning((dataclasses.replace(good, uplink=(9.0, 9.0)), chosen)))
    built.clear()
    doc, line = layout.balanced_playable_layout("base", {"m": 1}, {"h": 2}, time_of_day="night", footprints={"c": None})  # type: ignore[arg-type,dict-item]
    assert doc["placement"] is None and doc["time_of_day"] == "night" and doc["footprints"] == {"c": None} and len(built) == 2
    assert line == "placement: kept the current placement: candidate 3 cannot be built: no ground left for Escort, Evac and Retrieval" + tail

    for error in (LayoutError("no free walkable area with 1.5 m clearance"), GameplayError("the navmesh has no island")):
        def raising(base: object, manifest: object, error: Exception = error) -> object:
            raise error

        monkeypatch.setattr(layout, "choose_placement", raising)
        built.clear()
        doc, line = layout.balanced_playable_layout("base", {"m": 1}, {"h": 2}, storm=True, footprints={"d": None})  # type: ignore[arg-type,dict-item]
        assert doc["placement"] is None and doc["storm"] is True and doc["footprints"] == {"d": None} and built == [None]
        assert line == f"placement: kept the current placement: {error}" + tail


def test_balanced_layout_raises_where_the_current_one_does(monkeypatch: pytest.MonkeyPatch) -> None:
    import onward_generator.perf.layout as layout

    def failing(*args: object, **kwargs: object) -> dict:
        raise LayoutError("no free walkable area with 1.5 m clearance")

    monkeypatch.setattr(layout, "playable_layout", failing)
    with pytest.raises(LayoutError, match="1.5 m clearance"):
        layout.balanced_playable_layout(_plaza_base(box(0, 0, 2, 2)), {"objects": []}, {})  # type: ignore[arg-type]


def test_the_midpoint_uplink_is_rounded_to_the_centimetre() -> None:
    from onward_generator.perf.layout import _midpoint_on

    mid = _midpoint_on(PLAZA, (0.03, 0.01), (30.0, 0.0))
    assert mid == (round(15.015, 2), round(0.005, 2)) and mid[0] != 15.015 and mid[1] != 0.005
    # Off the area, it moves onto the nearest point of it.
    assert _midpoint_on(box(0, 0, 10, 10), (0.0, 20.0), (10.0, 30.0)) == (5.0, 10.0)


def test_the_even_uplink_is_the_balanced_node_of_the_path_inside_the_spawn_area() -> None:
    from onward_generator.gameplay.metrics import from_edges
    from onward_generator.gameplay.placement import Placement
    from onward_generator.perf.layout import _even_uplink

    chain = from_edges([(2.0 * i, 0.0) for i in range(11)], {(i, i + 1): 2000 for i in range(10)})
    p = Placement((0.0, 0.0), (20.0, 0.0), (10.0, 0.0), (0.0, 0.0), (20.0, 0.0))
    even = _even_uplink(chain, box(-1, -1, 30, 1), 0, p)
    assert even == (10.0, 0.0) and all(type(v) is float for v in even)
    # The middle is outside the spawn area: the nearest balance inside it.
    assert _even_uplink(chain, box(-1, -1, 7, 1), 0, p) == (6.0, 0.0)
    # The defenders' second block counts: nearer the attackers, it moves the balance west.
    assert _even_uplink(chain, box(-1, -1, 30, 1), 0, Placement((0.0, 0.0), (20.0, 0.0), (10.0, 0.0), (0.0, 0.0), (12.0, 0.0))) == (6.0, 0.0)
    assert _even_uplink(chain, box(-1, -1, 30, 1), 0, Placement((0.0, 0.0), (20.0, 0.0), (10.0, 0.0), (8.0, 0.0), (20.0, 0.0))) == (14.0, 0.0)
    assert _even_uplink(chain, box(40, 40, 50, 50), 0, p) is None
    assert _even_uplink(chain, box(-1, -1, 30, 1), None, p) is None
    assert _even_uplink(chain, box(-1, -1, 30, 1), 0, Placement((0.0, 0.0), (20.0, 9.0), (10.0, 0.0), (0.0, 0.0), (20.0, 0.0))) is None
    assert _even_uplink(chain, box(-1, -1, 30, 1), 0, Placement((0.0, 0.0), (20.0, 0.0), (10.0, 0.0), (0.0, 9.0), (20.0, 0.0))) is None
    assert _even_uplink(chain, box(-1, -1, 30, 1), 0, Placement((0.0, 0.0), (20.0, 0.0), (10.0, 0.0), (0.0, 0.0), (20.0, 9.0))) is None
    split = from_edges([(0.0, 0.0), (2.0, 0.0), (20.0, 0.0)], {(0, 1): 2000})
    assert _even_uplink(split, box(-1, -1, 30, 1), 0, p) is None


def test_choose_placement_scores_on_the_navmesh_of_the_core(monkeypatch: pytest.MonkeyPatch) -> None:
    import onward_generator.perf.layout as layout

    seen: list[object] = []
    real = layout.nav_islands

    def spy(base: object, inside: object = None) -> list:
        seen.append(inside)
        return real(base, inside)  # type: ignore[arg-type]

    monkeypatch.setattr(layout, "nav_islands", spy)
    manifest: dict = {"objects": [], "core": {"half_side_m": 50.0}}
    layout.choose_placement(_plaza_base(), manifest)  # type: ignore[arg-type]
    assert len(seen) == 1 and isinstance(seen[0], BaseGeometry) and seen[0].equals(playable_area(manifest))


def test_an_even_uplink_on_the_midpoint_is_not_a_second_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    import onward_generator.perf.layout as layout

    monkeypatch.setattr(layout, "K_SITES", 1)  # the candidates of the first C6 rule only
    monkeypatch.setattr(layout, "_even_uplink", lambda graph, area, node, placement: placement.uplink)
    placement, choice = layout.choose_placement(_plaza_base(), {"objects": []})  # type: ignore[arg-type]
    assert placement is None and choice.count == 4
    monkeypatch.setattr(layout, "_even_uplink", lambda graph, area, node, placement: None)
    assert layout.choose_placement(_plaza_base(), {"objects": []})[1].count == 4  # type: ignore[arg-type]


def test_spread_sites_start_at_the_attackers_and_take_the_farthest_fitting_point() -> None:
    from onward_generator.perf.layout import K_SITES, SITE_GAP_M, _block, _fits, _spawn_site, _spread_sites

    assert (K_SITES, SITE_GAP_M) == (6, 20.0)
    area, a = _spawn_site(_plaza_base(), {"objects": []})  # type: ignore[arg-type]
    sites = _spread_sites(area, a, 6)
    assert sites[0] == a and len(sites) == 6
    assert all(_fits(area, _block(*p)) for p in sites)
    assert all(math.dist(p, q) >= SITE_GAP_M for i, p in enumerate(sites) for q in sites[i + 1:])
    # The plaza is the square from -60 to 60 less 1.5 m: the second site is a far corner, ties to the north, then east.
    assert sites[1] == (56.0, 56.0)
    assert _spread_sites(area, a, 1) == [a] and _spread_sites(area, a, 2) == sites[:2]
    # A narrow strip has room for few sites 20 m apart.
    strip, s0 = _spawn_site(_plaza_base(box(-60, -60, 60, -48)), {"objects": []})  # type: ignore[arg-type]
    few = _spread_sites(strip, s0, 6)
    assert 2 <= len(few) < 6 and all(math.dist(p, q) >= SITE_GAP_M for i, p in enumerate(few) for q in few[i + 1:])


def test_spread_sites_break_ties_to_the_north_then_east_and_keep_a_gap_of_exactly_20_m() -> None:
    from onward_generator.perf.layout import _spread_sites

    # Blocks fit up to 56 m from the centre: the corners tie on distance, and north wins, then east.
    square = box(-58, -58, 58, 58)
    assert _spread_sites(square, (0.0, 0.0), 3) == [(0.0, 0.0), (56.0, 56.0), (-56.0, 56.0)]
    # A strip where blocks fit from x = -40 to 40: the ends, then the quarter points exactly 20 m from their neighbours.
    strip = box(-42, -1, 42, 1)
    assert _spread_sites(strip, (0.0, 0.0), 9) == [(0.0, 0.0), (40.0, 0.0), (-40.0, 0.0), (20.0, 0.0), (-20.0, 0.0)]


def test_candidate_pairs_take_the_defenders_then_the_site_pairs_30_m_apart_once() -> None:
    from onward_generator.perf.layout import _candidate_pairs

    a = (0.0, 0.0)
    sites = [a, (30.0, 0.0), (-29.9, 0.0), (0.0, 40.0)]
    assert _candidate_pairs(a, [(0.0, 40.0), (50.0, 0.0)], sites) == [
        (a, (0.0, 40.0)), (a, (50.0, 0.0)),            # the first rule's defenders
        (a, (30.0, 0.0)),                              # exactly 30 m is enough; (a, (0, 40)) is not repeated
        ((30.0, 0.0), (-29.9, 0.0)), ((30.0, 0.0), (0.0, 40.0)), ((-29.9, 0.0), (0.0, 40.0)),
    ]
    assert _candidate_pairs(a, [], [a, (29.9, 0.0)]) == []  # 29.9 m is too close


def test_candidates_pair_the_spread_sites_and_move_the_attackers(monkeypatch: pytest.MonkeyPatch) -> None:
    import onward_generator.perf.layout as layout
    from onward_generator.gameplay import placement as P

    seen: list[list] = []
    real = P.score_all

    def spy(graph: object, candidates: list, **kw: object) -> list:
        seen.append(list(candidates))
        return real(graph, candidates, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(P, "score_all", spy)
    base = _plaza_base()
    area, a = layout._spawn_site(base, {"objects": []})  # type: ignore[arg-type]
    layout.choose_placement(base, {"objects": []})  # type: ignore[arg-type]
    placed = [c for c in seen[0] if not isinstance(c, str)]
    sites = layout._spread_sites(area, a, layout.K_SITES)
    moved = {(c.attack, c.defend) for c in placed if c.attack != a}
    expected = {(s, t) for i, s in enumerate(sites) for t in sites[i + 1:] if s != a and math.dist(s, t) >= TEAM_SEPARATION_M}
    assert moved == expected and moved
    # The first candidates are the first rule's: the attackers where gameplay_layout puts them.
    assert seen[0][0].attack == a
    assert all(math.dist(c.attack, c.defend) >= TEAM_SEPARATION_M for c in placed)


def _fake_base(*pieces: BaseGeometry) -> object:
    from onward_generator.generation.surfaces import SurfaceClass

    class Surface:
        def __init__(self, geometry: BaseGeometry) -> None:
            self.geometry = geometry

    class Partition:
        surfaces = {SurfaceClass.SIDEWALK: Surface(union_all(list(pieces))), SurfaceClass.CARRIAGEWAY: Surface(box(-500, -500, 500, 500))}
        buildings = Polygon()

    return _base(partition=Partition())


def test_the_playable_layout_patrols_the_shrunk_islands_of_the_uplink(monkeypatch: pytest.MonkeyPatch) -> None:
    """Plan Q1b: hunt_routes gets the islands shrunk by the route clearance and the Uplink's piece of them."""
    from onward_generator.perf import layout as module

    ways = [LinearWay(f"w{i}", "pedestrian", LineString([(-55, y), (190, y)]), 4.0, "config_default") for i, y in enumerate(range(-50, 60, 10))]
    base = _base(*ways, partition=build_partition(box(-60, -60, 200, 60), [], ways, [], load_generation_config()))
    base.objects = []  # type: ignore[attr-defined]
    seen: list[tuple[object, object]] = []
    real = module.hunt_routes

    def spy(base_: object, free: object, islands: object, home: object, count: int, focus: tuple[float, float]) -> object:
        seen.append((islands, home))
        return real(base_, free, islands, home, count, focus)  # type: ignore[arg-type]

    monkeypatch.setattr(module, "hunt_routes", spy)
    doc = module.playable_layout(base, {"objects": []}, {})  # type: ignore[arg-type]
    patrol, home = route_islands(nav_islands(base), tuple(doc["uplink_m"]))  # type: ignore[arg-type]
    assert len(seen) == 1
    islands, given_home = seen[0]
    assert isinstance(islands, list) and [part.wkb for part in islands] == [part.wkb for part in patrol]
    assert isinstance(given_home, BaseGeometry) and given_home.equals(home)


def test_objective_ground_keeps_every_piece_with_1_m_clearance() -> None:
    from onward_generator.perf.layout import _objective_ground

    free, parts = _objective_ground(_fake_base(box(0, 0, 20, 20), box(40, 0, 42.8, 2.8)), {"objects": []})  # type: ignore[arg-type]
    # The small square keeps 0.8 x 0.8 m.
    assert sorted(round(p.area, 2) for p in parts) == [0.64, 324.0]
    assert round(free.area, 2) == 400.0 + 2.8 * 2.8
    with pytest.raises(LayoutError, match="^no walkable ground with 1 m clearance for AI and ammo$"):
        _objective_ground(_fake_base(box(0, 0, 1.9, 40)), {"objects": []})  # type: ignore[arg-type]


def test_the_playable_layout_of_a_core_measures_the_core_navmesh_with_or_without_balance() -> None:
    from onward_generator.perf.layout import balanced_playable_layout, playable_layout

    ways = [LinearWay(f"w{i}", "pedestrian", LineString([(-55, y), (190, y)]), 4.0, "config_default") for i, y in enumerate(range(-50, 60, 10))]
    base = _base(*ways, partition=build_partition(box(-60, -60, 200, 60), [], ways, [], load_generation_config()))
    base.objects = []  # type: ignore[attr-defined]
    core: dict = {"objects": [], "core": {"half_side_m": 60.0}}
    today = playable_layout(base, core, {})  # type: ignore[arg-type]
    assert today["navigation"]["span_m"] <= 120.0
    assert playable_layout(base, {"objects": []}, {})["navigation"]["span_m"] > 200.0  # type: ignore[arg-type]
    doc, line = balanced_playable_layout(base, core, {})  # type: ignore[arg-type]
    assert line.startswith("placement: ")
    assert doc["navigation"]["span_m"] <= 120.0 and set(doc) == set(today)
    inside = playable_area(core)
    assert inside is not None
    for block in ("attackers", "defenders", "attackers_b", "defenders_b"):
        assert all(inside.covers(Point(p["x"], p["y"])) for p in doc[block]["points_m"])
    assert inside.covers(Point(doc["uplink_m"]))
    assert _check_layout(doc) == []
