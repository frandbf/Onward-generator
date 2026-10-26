from __future__ import annotations

import pytest

from onward_generator.domain.models import AttributeOrigin, OsmRef
from onward_generator.geo.attributes import derive_attributes, parse_count, parse_length_m
from onward_generator.geo.osm_import import FeatureKind

REF = OsmRef("way", 7)


@pytest.mark.parametrize(
    "raw, expected",
    [("12", 12.0), ("12.5", 12.5), ("12 m", 12.0), ("12m", 12.0), ("10 ft", 3.048), ("6'", 1.8288), ("6'6\"", 1.9812)],
)
def test_parse_length(raw: str, expected: float) -> None:
    assert parse_length_m(raw) == pytest.approx(expected)


@pytest.mark.parametrize("raw", ["tall", "", "-3", "0", "12,5", "1e3", "nan"])
def test_parse_length_rejects(raw: str) -> None:
    assert parse_length_m(raw) is None


def test_parse_count() -> None:
    assert parse_count("4") == 4
    assert parse_count("3.5") is None
    assert parse_count("0") is None


def test_explicit_height_is_observed_and_not_overridden_by_levels() -> None:
    attrs, issues = derive_attributes(REF, FeatureKind.BUILDING, {"building": "yes", "height": "12", "building:levels": "5"})
    assert issues == []
    assert attrs["height_m"].origin is AttributeOrigin.OBSERVED
    assert attrs["height_m"].value == 12.0 and attrs["height_m"].source_tag == "height"
    assert attrs["levels"].origin is AttributeOrigin.OBSERVED and attrs["levels"].value == 5


def test_levels_alone_do_not_produce_a_height_in_geo() -> None:
    attrs, _ = derive_attributes(REF, FeatureKind.BUILDING, {"building": "yes", "building:levels": "3"})
    assert "height_m" not in attrs
    assert attrs["levels"].origin is AttributeOrigin.OBSERVED and attrs["levels"].value == 3


def test_building_without_height_data_gets_no_height() -> None:
    attrs, issues = derive_attributes(REF, FeatureKind.BUILDING, {"building": "yes"})
    assert attrs == {} and issues == []


def test_unparseable_height_is_reported() -> None:
    attrs, issues = derive_attributes(REF, FeatureKind.BUILDING, {"building": "yes", "height": "tall"})
    assert "height_m" not in attrs
    assert [i.code for i in issues] == ["unparseable_tag"]


def test_highway_width_observed_or_inferred() -> None:
    explicit, _ = derive_attributes(REF, FeatureKind.HIGHWAY, {"highway": "footway", "width": "2.5"})
    assert explicit["width_m"].origin is AttributeOrigin.OBSERVED and explicit["width_m"].value == 2.5
    inferred, _ = derive_attributes(REF, FeatureKind.HIGHWAY, {"highway": "residential"})
    assert inferred["width_m"].origin is AttributeOrigin.INFERRED and inferred["width_m"].value == 6.0
    unknown, _ = derive_attributes(REF, FeatureKind.HIGHWAY, {"highway": "raceway"})
    assert "width_m" not in unknown
