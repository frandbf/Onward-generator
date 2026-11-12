"""Plan Q7: the surface partition shares one 1 mm grid, buildings included, so its parts do not overlap."""

from __future__ import annotations

import random

from shapely import LineString, box, get_coordinates
from shapely.affinity import rotate, translate
from shapely.geometry.base import BaseGeometry

from onward_generator.generation.config import load_generation_config
from onward_generator.generation.surfaces import LinearWay, build_partition


def _scene(seed: int) -> tuple[list[BaseGeometry], list[LinearWay]]:
    """Rotated footprints with off-grid corners and one street: seed 235 overlapped 0.0009 m2 without the shared grid."""
    rng = random.Random(seed)
    footprints = []
    for _ in range(rng.randint(2, 4)):
        width, depth = rng.uniform(5, 12), rng.uniform(5, 12)
        shape = rotate(box(-width / 2, -depth / 2, width / 2, depth / 2), rng.uniform(0, 90))
        footprints.append(translate(shape, rng.uniform(-8, 8) + 1e-4 * rng.random(), rng.uniform(-8, 8)))
    line = rotate(LineString([(-60, rng.uniform(-15, 15)), (60, rng.uniform(-15, 15))]), rng.uniform(0, 90), origin=(0, 0))
    return footprints, [LinearWay("w", "vehicular", line, 6.0, "config_default")]


def test_the_partition_of_off_grid_footprints_does_not_overlap() -> None:
    config = load_generation_config()
    footprints, ways = _scene(235)
    assert len(footprints) == 3
    partition = build_partition(box(-40, -40, 40, 40), footprints, ways, [], config)
    assert partition.overlap_area() < 1e-9
    assert partition.gap_area() < 1e-3


def test_the_partition_buildings_sit_on_the_grid() -> None:
    config = load_generation_config()
    footprints, ways = _scene(235)
    partition = build_partition(box(-40, -40, 40, 40), footprints, ways, [], config)
    corners = get_coordinates(partition.buildings) / config.precision_m
    assert abs(corners - corners.round()).max() < 1e-6
    assert any(abs(c / config.precision_m - round(c / config.precision_m)) > 1e-3 for f in footprints for c in get_coordinates(f).ravel())
