from __future__ import annotations

from datetime import UTC, datetime

import pytest

from onward_generator.domain.models import AreaOfInterest, Attribute, AttributeOrigin, OsmRef, SourceDataset
from tests.conftest import MURCIA_CENTER

LON, LAT = MURCIA_CENTER
SQUARE = ((LON, LAT), (LON + 0.001, LAT), (LON + 0.001, LAT + 0.001), (LON, LAT))


def test_osm_ref_key_and_validation() -> None:
    assert OsmRef("way", 123).key == "osm-way-123"
    with pytest.raises(ValueError):
        OsmRef("area", 1)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        OsmRef("node", 0)


def test_observed_attribute_requires_source_tag() -> None:
    with pytest.raises(ValueError):
        Attribute(12.0, AttributeOrigin.OBSERVED)
    with pytest.raises(ValueError):
        Attribute(12.0, AttributeOrigin.OBSERVED, source_tag="height", rule="x")


@pytest.mark.parametrize("origin", [AttributeOrigin.INFERRED, AttributeOrigin.GENERATED])
def test_non_observed_attribute_requires_rule(origin: AttributeOrigin) -> None:
    with pytest.raises(ValueError):
        Attribute(9.0, origin)


def test_attribute_confidence_bounds_and_finite_value() -> None:
    with pytest.raises(ValueError):
        Attribute(9.0, AttributeOrigin.INFERRED, rule="r", confidence=1.5)
    with pytest.raises(ValueError):
        Attribute(float("nan"), AttributeOrigin.INFERRED, rule="r")


def test_area_of_interest_validation() -> None:
    aoi = AreaOfInterest("x", SQUARE, 30.0)
    assert aoi.crs == "EPSG:25830"
    with pytest.raises(ValueError, match="closed"):
        AreaOfInterest("x", SQUARE[:-1] + ((LON, LAT + 0.002),), 30.0)
    with pytest.raises(ValueError, match="latitude"):
        AreaOfInterest("x", ((LON, 95.0), *SQUARE[1:-1], (LON, 95.0)), 30.0)
    with pytest.raises(ValueError, match="margin"):
        AreaOfInterest("x", SQUARE, -5.0)


def _source(**overrides) -> SourceDataset:
    values = dict(
        provider="openstreetmap",
        license="ODbL-1.0",
        attribution="© OpenStreetMap contributors",
        path="/tmp/a.osm",
        sha256="0" * 64,
        size_bytes=10,
        registered_at=datetime(2026, 1, 1, tzinfo=UTC),
        retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
        retrieved_at_origin="declared",
    )
    values.update(overrides)
    return SourceDataset(**values)


def test_source_dataset_validation() -> None:
    assert _source().to_dict()["license"] == "ODbL-1.0"
    with pytest.raises(ValueError):
        _source(attribution=" ")
    with pytest.raises(ValueError):
        _source(sha256="xyz")
    with pytest.raises(ValueError):
        _source(retrieved_at=datetime(2026, 1, 1))
