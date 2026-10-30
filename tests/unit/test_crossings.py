"""Walkable crossings over the carriageway (ADR-015)."""

from __future__ import annotations

import math

import pytest
from shapely import LineString, MultiLineString, Polygon, box, union_all
from shapely.geometry.base import BaseGeometry

from onward_generator.generation.config import load_generation_config
from onward_generator.generation.surfaces import (
    CROSSING_WIDTH_M,
    LinearWay,
    SurfaceClass,
    _crossing,
    build_partition,
    crossing_offsets,
    crossing_strips,
)

CFG = load_generation_config()


def _street(way_id: str, coords: list[tuple[float, float]], width: float) -> LinearWay:
    return LinearWay(way_id, "vehicular", LineString(coords), width, "config_default")


def _carriageway(*ways: LinearWay) -> BaseGeometry:
    return union_all([way.geometry.buffer(way.width_m / 2.0) for way in ways])


def _same(left: BaseGeometry, right: BaseGeometry) -> bool:
    return left.symmetric_difference(right).area < 1e-6


def test_crossings_sit_on_both_setbacks_and_at_most_50_m_apart() -> None:
    assert crossing_offsets(100.0, 6.0, 6.0) == [6.0, 50.0, 94.0]
    assert crossing_offsets(112.0, 6.0, 6.0) == [6.0, 56.0, 106.0]
    offsets = crossing_offsets(113.0, 6.0, 6.0)
    assert offsets == pytest.approx([6.0, 39.0 + 2.0 / 3.0, 73.0 + 1.0 / 3.0, 107.0])


def test_a_short_segment_gets_one_crossing_halfway() -> None:
    assert crossing_offsets(12.0, 6.0, 3.0) == [6.0]
    assert crossing_offsets(13.0, 5.0, 5.0) == [6.5]
    # Exactly one crossing width between the setbacks still holds two.
    assert crossing_offsets(14.0, 5.0, 5.0) == [5.0, 9.0]


def test_a_crossing_is_square_to_the_street_and_as_long_as_the_span() -> None:
    strip = _crossing(LineString([(0, 0), (100, 0)]), 10.0, 8.0)
    assert _same(strip, box(10 - CROSSING_WIDTH_M / 2, -4, 10 + CROSSING_WIDTH_M / 2, 4))


def test_a_crossing_follows_the_chord_one_metre_ahead() -> None:
    """Half a metre before a right-angle bend the chord to 1 m ahead runs at 45 degrees."""
    strip = _crossing(LineString([(0, 0), (10, 0), (10, 10)]), 9.5, 8.0)
    half = 4.0 / math.sqrt(2.0)
    axis = LineString([(9.5 + half, -half), (9.5 - half, half)])
    assert _same(strip, axis.buffer(CROSSING_WIDTH_M / 2, cap_style="flat"))


def test_a_straight_street_gets_kerb_to_kerb_crossings() -> None:
    street = _street("s", [(0, 0), (100, 0)], 6.0)
    strips = crossing_strips([street], _carriageway(street))
    expected = union_all([box(x - 2, -3, x + 2, 3) for x in (6.0, 50.0, 94.0)])
    assert _same(strips, expected)


def test_junction_crossings_clear_the_widest_street() -> None:
    """A 6 m side street on a 10 m main street: its junction crossing sits 8 m from the centre."""
    main = _street("main", [(0, 0), (100, 0)], 10.0)
    side = _street("side", [(50, 0), (50, 60)], 6.0)
    strips = crossing_strips([main, side], _carriageway(main, side))
    expected = union_all([
        *(box(x - 2, -5, x + 2, 5) for x in (8.0, 42.0, 58.0, 92.0)),
        box(47, 6, 53, 10),
        box(47, 52, 53, 56),
    ])
    assert _same(strips, expected)


def test_areas_have_no_crossing_and_split_ways_get_one_per_part() -> None:
    parts = MultiLineString([[(0, 0), (30, 0)], [(0, 40), (30, 40)]])
    split = LinearWay("split", "vehicular", parts, 6.0, "inferred")
    square = LinearWay("square", "vehicular", box(100, 100, 120, 120), 0.0, "area")
    carriageway = union_all([parts.buffer(3.0), square.geometry])
    strips = crossing_strips([split, square], carriageway)
    expected = union_all([box(x - 2, y - 3, x + 2, y + 3) for x in (6.0, 24.0) for y in (0.0, 40.0)])
    assert _same(strips, expected)


def test_no_street_means_no_crossing() -> None:
    assert crossing_strips([], Polygon()).is_empty


def test_the_partition_keeps_its_crossings_inside_the_carriageway() -> None:
    street = _street("s", [(-60, 0), (60, 0)], 6.0)
    partition = build_partition(box(-50, -50, 50, 50), [], [street], [], CFG)
    carriageway = partition.surfaces[SurfaceClass.CARRIAGEWAY].geometry
    # The way runs 10 m past the site on each side: crossings at -54, -18, 18 and 54 m; two land inside.
    assert partition.crossings.area == pytest.approx(2 * CROSSING_WIDTH_M * 6.0, abs=0.01)
    assert partition.crossings.difference(carriageway).area < 1e-6
    assert build_partition(box(-50, -50, 50, 50), [], [], [], CFG).crossings.is_empty


def test_a_dead_end_inside_another_street_bounding_box_keeps_its_own_setback() -> None:
    """The narrow street ends inside the wide diagonal's envelope, 56 m from its line: only touching ways count."""
    diagonal = _street("diagonal", [(0, 0), (100, 100)], 12.0)
    narrow = _street("narrow", [(90, -40), (90, 10)], 6.0)
    strips = crossing_strips([diagonal, narrow], _carriageway(diagonal, narrow))
    near = strips.intersection(box(80, -50, 100, 20))
    assert _same(near, union_all([box(87, -36, 93, -32), box(87, 2, 93, 6)]))
