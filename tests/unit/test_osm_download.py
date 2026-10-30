"""OSM fetch uses the map centre and a metric margin. No network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from shapely import Point, box

from onward_generator.geo.projection import LocalFrame
from onward_generator.geo.sources import sha256_file
from onward_generator.maps.workspace import create_map
from onward_generator.osm.acquire import (
    OVERPASS_URL,
    OsmDownloadError,
    download_map_osm,
    inclusion_bbox,
    overpass_query,
)

LON = -58.381647718021945
LAT = -34.60369254204782
_XML = (
    b'<?xml version="1.0" encoding="UTF-8"?>\n'
    b'<osm version="0.6" generator="test">\n'
    b'<meta osm_base="2026-10-19T18:00:00Z"/>\n'
    b'<node id="1" lat="-34.6037" lon="-58.3816"/>\n'
    b"</osm>\n"
)


def _map(tmp_path: Path) -> Path:
    root = tmp_path / "obelisco"
    create_map(root, map_id="obelisco", name="Obelisco", latitude=LAT, longitude=LON, radius_m=400, margin_m=30)
    return root


def test_inclusion_bbox_is_the_square_plus_margin_in_metres(tmp_path: Path) -> None:
    root = _map(tmp_path)
    south, west, north, east = inclusion_bbox(root)
    assert south < LAT < north
    assert west < LON < east
    frame = LocalFrame.create(LON, LAT, "EPSG:32721")
    for x, y in ((430, 430), (430, -430), (-430, 430), (-430, -430)):
        corner = frame.to_wgs84(Point(x, y))
        assert south - 1e-6 <= corner.y <= north + 1e-6
        assert west - 1e-6 <= corner.x <= east + 1e-6
    outside = frame.to_wgs84(Point(0, 520))
    assert not (south <= outside.y <= north and west <= outside.x <= east)
    local = frame.to_local(box(west, south, east, north))
    minx, miny, maxx, maxy = local.bounds
    assert 860 < maxx - minx < 1000
    assert 860 < maxy - miny < 1000
    query = overpass_query((south, west, north, east))
    assert f"{south:.7f},{west:.7f},{north:.7f},{east:.7f}" in query


def test_download_writes_osm_and_refuses_a_second_request(tmp_path: Path) -> None:
    root = _map(tmp_path)
    seen: list[tuple[str, str]] = []

    def fetch(endpoint: str, query: str, timeout: float) -> bytes:
        seen.append((endpoint, query))
        assert timeout == 120.0
        return _XML

    record = download_map_osm(root, fetch=fetch)
    assert seen[0][0] == OVERPASS_URL
    assert record.crs == "EPSG:32721"
    assert record.center == {"latitude": LAT, "longitude": LON}
    assert record.osm_base == "2026-10-19T18:00:00Z"
    target = root / "data" / "raw" / "obelisco.osm"
    assert target.read_bytes() == _XML
    assert record.sha256 == sha256_file(target)
    sidecar = json.loads(target.with_name("obelisco.osm.source.json").read_text(encoding="utf-8"))
    assert sidecar["license"] == "ODbL-1.0"
    assert sidecar["attribution"] == "© OpenStreetMap contributors"
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        download_map_osm(root, fetch=fetch)
    assert len(seen) == 1


def test_download_rejects_a_page_that_is_not_osm(tmp_path: Path) -> None:
    root = _map(tmp_path)

    def fetch(_endpoint: str, _query: str, _timeout: float) -> bytes:
        return b"<html><body>busy</body></html>"

    with pytest.raises(OsmDownloadError, match="not OSM XML"):
        download_map_osm(root, fetch=fetch)
    assert not (root / "data" / "raw" / "obelisco.osm").exists()


def test_download_rejects_an_overpass_error(tmp_path: Path) -> None:
    root = _map(tmp_path)

    def fetch(_endpoint: str, _query: str, _timeout: float) -> bytes:
        return b'<osm version="0.6"><remark>runtime error: Query timed out</remark></osm>'

    waits: list[float] = []
    with pytest.raises(OsmDownloadError, match="timed out"):
        download_map_osm(root, fetch=fetch, sleep=waits.append)
    assert waits == [30.0, 30.0]
    assert not (root / "data" / "raw" / "obelisco.osm").exists()


def test_download_retries_an_overpass_runtime_error(tmp_path: Path) -> None:
    root = _map(tmp_path)
    answers = [b'<osm version="0.6"><remark>runtime error: Query run out of memory</remark></osm>', _XML]
    waits: list[float] = []
    queries: list[str] = []

    def fetch(_endpoint: str, query: str, _timeout: float) -> bytes:
        queries.append(query)
        return answers.pop(0)

    record = download_map_osm(root, fetch=fetch, sleep=waits.append)
    assert waits == [30.0]
    assert queries == [overpass_query(inclusion_bbox(root))] * 2
    assert record.osm_base == "2026-10-19T18:00:00Z"
    assert (root / "data" / "raw" / "obelisco.osm").read_bytes() == _XML


def test_download_cuts_a_long_overpass_remark(tmp_path: Path) -> None:
    root = _map(tmp_path)
    remark = "runtime error: " + "x" * 400

    def fetch(_endpoint: str, _query: str, _timeout: float) -> bytes:
        return f'<osm version="0.6"><remark>{remark}</remark></osm>'.encode()

    with pytest.raises(OsmDownloadError) as caught:
        download_map_osm(root, fetch=fetch, sleep=lambda _s: None)
    assert str(caught.value) == f"Overpass refused the query: {remark[:300]}"


def test_download_does_not_retry_a_page_that_is_not_osm(tmp_path: Path) -> None:
    root = _map(tmp_path)
    calls: list[str] = []

    def fetch(_endpoint: str, query: str, _timeout: float) -> bytes:
        calls.append(query)
        return b"<html><body>busy</body></html>"

    with pytest.raises(OsmDownloadError, match="not OSM XML"):
        download_map_osm(root, fetch=fetch, sleep=lambda _s: pytest.fail("no retry expected"))
    assert len(calls) == 1
