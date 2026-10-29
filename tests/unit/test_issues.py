"""Geographic pipeline issues, tied to the original OSM element when known."""

from __future__ import annotations

import pytest

from onward_generator.domain.models import OsmRef
from onward_generator.geo.issues import GeoIssue


def test_issue_with_an_element_names_it() -> None:
    issue = GeoIssue("tiny_polygon", "warning", "area below the minimum", OsmRef("way", 42))
    assert issue.to_dict() == {"code": "tiny_polygon", "severity": "warning", "message": "area below the minimum", "element": "osm-way-42"}


def test_issue_without_an_element_has_none() -> None:
    issue = GeoIssue("no_buildings", "info", "the extract has no buildings")
    assert issue.ref is None
    assert issue.to_dict()["element"] is None


def test_issue_is_immutable() -> None:
    issue = GeoIssue("x", "error", "y")
    with pytest.raises(AttributeError):
        issue.code = "z"  # type: ignore[misc]
