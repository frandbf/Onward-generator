"""Simple 3D Buildings tag parsing: list values, levels and compass bearings."""

from __future__ import annotations

import pytest

from onward_generator.detail.s3db import first_value, parse_direction, parse_levels


def test_first_value_is_the_first_of_a_list_trimmed_and_lower_case() -> None:
    assert first_value({"roof:shape": " Gabled ;hipped"}, "roof:shape") == "gabled"
    assert first_value({}, "roof:shape") == ""
    assert first_value({"roof:shape": ""}, "roof:shape") == ""


@pytest.mark.parametrize(("raw", "levels"), [
    ("3", 3.0), ("0", 0.0), ("0.5", 0.5), ("1,5", 1.5), ("100", 100.0), ("-1", None), ("100.5", None), ("x", None), (None, None),
])
def test_levels_are_zero_to_a_hundred(raw: object, levels: float | None) -> None:
    assert parse_levels(raw) == levels


@pytest.mark.parametrize(("raw", "bearing"), [
    ("N", 0.0), ("nne", 22.5), ("SE", 135.0), ("S", 180.0), ("nnw", 337.5), ("90", 90.0), ("370", 10.0), ("-90", 270.0),
    (" 45 ", 45.0), ("inf", None), ("nan", None), ("north", None), ("", None), (None, None),
])
def test_directions_are_compass_bearings(raw: object, bearing: float | None) -> None:
    assert parse_direction(raw) == bearing
