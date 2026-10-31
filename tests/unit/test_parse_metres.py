"""One reading of an OSM height tag, shared by detail, signs and overgrow."""

from __future__ import annotations

import pytest

from onward_generator.geo.attributes import parse_metres
from onward_generator.overgrow import digest, dressing
from onward_generator.overgrow import place as overgrow_place
from onward_generator.signs import place as signs_place


@pytest.mark.parametrize(
    ("raw", "metres"),
    [
        ("12", 12.0),
        ("12m", 12.0),
        ("12 m", 12.0),
        (" 12.5 M ", 12.5),
        ("12,5", 12.5),
        (12, 12.0),
        ("200", 200.0),
        ("0.1", 0.1),
        ("200.1", None),
        ("0", None),
        ("-3", None),
        ("12 ft", None),
        ("tall", None),
        ("m", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_metres(raw: object, metres: float | None) -> None:
    assert parse_metres(raw) == metres


def test_signs_and_overgrow_read_12m_and_fall_back_to_levels() -> None:
    assert signs_place._height_m({"height": "12m"}) == 12.0
    assert overgrow_place._height_m({"building:height": "12m"}) == 12.0
    assert digest._height({"height": "12m"}) == (12.0, "observed")
    assert digest._height({"height": "tall", "building:levels": "4"}) == (12.0, "observed")
    assert digest._height({"building:height": "9"}) == (9.0, "observed")
    # signs and overgrow place keep their rule: an unreadable height is no height, the levels are not tried.
    assert signs_place._height_m({"height": "tall", "building:levels": "4"}) is None
    assert overgrow_place._height_m({"height": "tall", "building:levels": "4"}) is None
    features = [
        {"kind": "building", "id": "osm-way-1", "tags": {"height": "12m"}},
        {"kind": "building", "id": "osm-way-2", "tags": {"height": "250", "building:levels": "2"}},
        {"kind": "building", "id": "osm-way-3", "tags": {"building:height": "9,5"}},
        {"kind": "road", "id": "osm-way-4", "tags": {"height": "5"}},
    ]
    assert dressing._heights(features) == {"osm-way-1": 12.0, "osm-way-2": 6.0, "osm-way-3": 9.5}
