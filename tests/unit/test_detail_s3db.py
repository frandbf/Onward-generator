"""Simple 3D Buildings tag parsing: list values, levels, compass bearings and part heights."""

from __future__ import annotations

import pytest

from onward_generator.detail.s3db import PartHeight, first_value, parse_direction, parse_levels, part_height


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


def test_a_part_with_a_height_tag_is_observed() -> None:
    h = part_height({"height": "20", "min_height": "10", "building:levels": "9"}, 3.0, "unknown")
    assert h == PartHeight(20.0, 10.0, "observed", "observed", "height", 0.0)
    assert h is not None and h.to_record() == {"height_m": 20.0, "min_height_m": 10.0, "origin": "observed",
                                               "min_height_origin": "observed", "rule": "height"}


def test_a_part_without_height_takes_its_levels_times_the_storey() -> None:
    h = part_height({"building:levels": "4", "roof:levels": "1", "building:min_level": "2"}, 3.5, "commercial")
    assert h == PartHeight(17.5, 7.0, "estimated", "estimated", "(building:levels 4 + roof:levels 1) × 3.5 m (commercial)", 1.0)
    assert part_height({"building:levels": "3", "min_level": "1"}, 3.0, "unknown") == PartHeight(
        9.0, 3.0, "estimated", "estimated", "(building:levels 3 + roof:levels 0) × 3 m (unknown)", 0.0)
    # building:min_level wins over min_level; min_height over both.
    assert part_height({"building:levels": "3", "building:min_level": "2", "min_level": "1"}, 3.0, "u").min_height_m == 6.0  # type: ignore[union-attr]
    assert part_height({"building:levels": "3", "building:min_level": "0", "min_level": "1"}, 3.0, "u").min_height_m == 0.0  # type: ignore[union-attr]
    assert part_height({"building:levels": "3", "min_height": "4", "min_level": "1"}, 3.0, "u").min_height_m == 4.0  # type: ignore[union-attr]
    rounded = part_height({"height": "10.12345", "min_height": "1.23456"}, 3.0, "u")
    assert rounded is not None and rounded.to_record()["height_m"] == 10.123 and rounded.to_record()["min_height_m"] == 1.235


def test_a_part_with_no_height_or_levels_has_none() -> None:
    assert part_height({}, 3.0, "unknown") is None
    assert part_height({"building:levels": "0", "roof:levels": "2"}, 3.0, "unknown") is None
    assert part_height({"height": "tall"}, 3.0, "unknown") is None
    assert part_height({"building:min_level": "0", "building:levels": "2"}, 3.0, "u").min_height_origin == "default"  # type: ignore[union-attr]
