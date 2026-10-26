from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from onward_generator.geo.aoi import AoiError, load_aoi_geojson
from onward_generator.geo.sources import (
    OPENSTREETMAP,
    SourceError,
    register_local_source,
    sha256_file,
    verify_unchanged,
)
from tests.conftest import AOI_FIXTURE, MURCIA_CENTER, OSM_FIXTURE

LON, LAT = MURCIA_CENTER
RING = [[LON, LAT], [LON + 0.002, LAT], [LON + 0.002, LAT + 0.002], [LON, LAT]]
INNER_RING = [[LON + 0.0005, LAT + 0.0002], [LON + 0.001, LAT + 0.0002], [LON + 0.001, LAT + 0.0004], [LON + 0.0005, LAT + 0.0002]]


def test_default_license_is_openstreetmap_odbl(tmp_path: Path) -> None:
    path = tmp_path / "a.osm"
    path.write_text('<osm version="0.6"/>', encoding="utf-8")
    source = register_local_source(path)
    assert source.provider == "openstreetmap"
    assert source.license == "ODbL-1.0"
    assert source.attribution == OPENSTREETMAP.attribution
    assert source.license_url == "https://www.openstreetmap.org/copyright"
    assert source.retrieved_at_origin == "file_mtime"
    assert source.sha256 == sha256_file(path)


def test_declared_retrieval_date(synthetic_source) -> None:
    assert synthetic_source.retrieved_at_origin == "declared"
    assert synthetic_source.license == "CC0-1.0"


def test_rejects_missing_unsupported_and_naive_date(tmp_path: Path) -> None:
    with pytest.raises(SourceError, match="not found"):
        register_local_source(tmp_path / "missing.osm")
    pbf = tmp_path / "a.pbf"
    pbf.write_bytes(b"x")
    with pytest.raises(SourceError, match="unsupported"):
        register_local_source(pbf)
    with pytest.raises(SourceError, match="timezone"):
        register_local_source(OSM_FIXTURE, retrieved_at=datetime(2026, 1, 1))


def test_verify_unchanged_detects_modification(tmp_path: Path) -> None:
    path = tmp_path / "a.osm"
    path.write_text('<osm version="0.6"/>', encoding="utf-8")
    source = register_local_source(path)
    verify_unchanged(source)
    path.write_text('<osm version="0.6"><node id="1" lat="0" lon="0"/></osm>', encoding="utf-8")
    with pytest.raises(SourceError, match="changed"):
        verify_unchanged(source)


def test_aoi_fixture_loads() -> None:
    aoi = load_aoi_geojson(AOI_FIXTURE)
    assert aoi.name == "Synthetic AOI 200 m"
    assert aoi.margin_m == 30
    assert len(aoi.polygon) == 5
    assert load_aoi_geojson(AOI_FIXTURE, margin_m=5).margin_m == 5


def _write(tmp_path: Path, doc: dict) -> Path:
    path = tmp_path / "aoi.geojson"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "doc, message",
    [
        ({"type": "Point", "coordinates": [LON, LAT]}, "Polygon"),
        ({"type": "FeatureCollection", "features": []}, "exactly one"),
        ({"type": "Polygon", "coordinates": [RING, INNER_RING]}, "one ring"),
        # Murcia centre in EPSG:25830 metres, passed where degrees are expected.
        ({"type": "Polygon", "coordinates": [[[664344, 4205700], [664444, 4205700], [664444, 4205800], [664344, 4205700]]]}, "longitude"),
        ({"type": "Polygon", "crs": {"type": "name", "properties": {"name": "EPSG:25830"}}, "coordinates": [RING]}, "WGS84"),
    ],
)
def test_invalid_aoi_files(tmp_path: Path, doc: dict, message: str) -> None:
    with pytest.raises(AoiError, match=message):
        load_aoi_geojson(_write(tmp_path, doc))
