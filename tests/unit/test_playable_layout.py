"""Playable layout around the Murcia origin: Hunt anchors, SDK audio names, urban day."""

from __future__ import annotations

import math

import pytest
from shapely import LineString, MultiLineString, Point, box

from onward_generator.export.package import _check_layout
from onward_generator.generation.surfaces import LinearWay
from onward_generator.perf.layout import (
    AI_COUNT,
    GAMEPLAY_VERSION,
    MODE_CODES,
    SPAWN_SEPARATION_M,
    LayoutError,
    _routes,
    component_holding,
    extra_spawn_block,
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

    routes = _routes(Base(), box(-20, -5, 50, 305))
    starts = [(route["points"][0]["x"], route["points"][0]["y"]) for route in routes]
    assert len(starts) >= AI_COUNT
    assert all(abs(x) >= 4 - 0.05 or abs(x - 40) < 0.05 for x, _y in starts)
    assert any(abs(x + 4) < 0.05 for x, _y in starts)
    assert any(abs(x - 4) < 0.05 for x, _y in starts)
    assert any(abs(x - 40) < 0.05 for x, _y in starts)
    assert all(
        math.hypot(a[0] - b[0], a[1] - b[1]) >= SPAWN_SEPARATION_M - 1e-6
        for i, a in enumerate(starts)
        for b in starts[i + 1 :]
    )


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
