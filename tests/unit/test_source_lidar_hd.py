"""fr.lidar_hd: IGN LiDAR HD MNH heights, with a fake Géoplateforme (ADR-018)."""

from __future__ import annotations

import json
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from shapely import box
from shapely.geometry import mapping

from onward_generator.maps.workspace import create_map
from onward_generator.pipeline_run import source_plan
from onward_generator.sources import lidar
from onward_generator.sources.fr.lidar_hd import LidarHdPlugin, coverage
from onward_generator.sources.plugin import SourceError

HD = LidarHdPlugin()
LYON = (4.8357, 45.7640)
ORIGIN = (643000.0, 5070000.0)  # EPSG:32631, central Lyon
TILE = {
    "type": "MultiPolygon",
    "coordinates": [[[[4.80, 45.74], [4.88, 45.74], [4.88, 45.79], [4.80, 45.79], [4.80, 45.74]]]],
}


def _scene() -> dict[str, Any]:
    feature = {"id": "osm-way-5", "kind": "building", "tags": {"building": "yes"}, "geometry": mapping(box(0, 0, 12, 8))}
    return {
        "schema": "onward_generator.geo_scene",
        "schema_version": "0.2.0",
        "frame": {"crs": "EPSG:32631", "coordinate_system": "local_grid", "unit": "m", "origin_projected": list(ORIGIN)},
        "features": [feature],
    }


class FakeGeoplateforme:
    """WMS-R replies a 21 m MNH everywhere except a -9999 strip; WFS replies one tile with dates."""

    def __init__(self) -> None:
        self.urls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.urls.append(url)
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        if query.get("SERVICE") == "WFS":
            properties = {
                "date_debut_acquisition": "2021-04-14Z", "date_fin_acquisition": "2021-09-24Z",
                "date_edition": "2025-02-20Z", "code_mission": "21LHD6OL", "nombre_points": 3,
            }
            return json.dumps({"type": "FeatureCollection", "features": [{"geometry": TILE, "properties": properties}]}).encode()
        width, height = int(query["WIDTH"]), int(query["HEIGHT"])
        values = np.full((height, width), 21.0, dtype="<f4")
        values[0, :] = -9999.0
        return values.tobytes()


def test_lidar_hd_identity_licence_and_coverage() -> None:
    assert HD.id == "fr.lidar_hd"
    assert HD.produces == frozenset({"height_m"})
    assert HD.licence.spdx == "etalab-2.0"
    assert HD.licence.name == "Licence Ouverte / Open Licence 2.0 (Etalab)"
    assert HD.licence.url == "https://www.etalab.gouv.fr/licence-ouverte-open-licence/"
    assert HD.licence.attribution == "IGN, LiDAR HD MNH"
    assert HD.source_label == "IGN LiDAR HD MNH 0.5 m"
    assert HD.hosts == frozenset({"data.geopf.fr"})
    assert HD.raster_crs == "EPSG:2154"
    assert HD.stage_names == ("lidar_hd", "lidar_hd_heights")
    assert HD.rule == lidar.HeightRule(buffer_m=1.0, percentile=50.0, min_cells=8, min_valid_share=0.5, min_value=2.0, confidence=0.9)
    assert HD.covers(*LYON)
    assert HD.covers(2.3522, 48.8566)  # Paris
    assert HD.covers(9.45, 42.70)  # Bastia, Corsica
    assert not HD.covers(-3.7038, 40.4168)  # Madrid
    assert not HD.covers(4.3517, 50.8503)  # Brussels
    assert isinstance(HD, lidar.RasterHeights)
    assert len(coverage()) == 2


def test_coverage_refuses_another_schema(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"schema": "x", "polygons": []}), encoding="utf-8")
    with pytest.raises(SourceError) as caught:
        coverage(path)
    assert str(caught.value) == "c.json: schema must be onward_generator.source_coverage"


def test_requests_keep_the_native_half_metre_grid_and_tile_at_1_km() -> None:
    (only,) = HD.requests((842516.1, 6519774.0, 842816.0, 6520074.3))
    assert only.bbox == (842515.75, 6519773.75, 842816.25, 6520074.75)
    assert (only.columns, only.rows) == (601, 602)
    assert only.url == (
        "https://data.geopf.fr/wms-r/wms?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap"
        "&LAYERS=IGNF_LIDAR-HD_MNH_ELEVATION.ELEVATIONGRIDCOVERAGE.LAMB93&STYLES=&CRS=EPSG:2154"
        "&BBOX=842515.75,6519773.75,842816.25,6520074.75&WIDTH=601&HEIGHT=602&FORMAT=image/x-bil;bits=32"
    )
    many = HD.requests((0.25, 0.25, 1500.25, 10.25))
    assert [(r.columns, r.rows) for r in many] == [(2000, 20), (1000, 20)]


def test_decode_reads_float32_and_drops_the_nodata_value() -> None:
    values = np.array([[5.0, -9999.0]], dtype="<f4").tobytes()
    grid = HD.decode(values, {"bbox": [0.25, 0.25, 1.25, 0.75], "columns": 2, "rows": 1})
    assert grid.west == 0.25 and grid.north == 0.75 and grid.cell == 0.5
    assert grid.values[0, 0] == 5.0 and np.isnan(grid.values[0, 1])
    noise = np.array([[-999.0, -1001.0]], dtype="<f4").tobytes()
    assert HD.decode(noise, {"bbox": [0, 0, 1, 0.5], "columns": 2, "rows": 1}).values[0, 0] == -999.0


def test_metadata_keeps_the_acquisition_dates_of_each_tile() -> None:
    service = FakeGeoplateforme()
    meta = HD.metadata((4.83, 45.76, 4.84, 45.77), service)
    (url,) = service.urls
    assert url == (
        "https://data.geopf.fr/wfs/ows?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetFeature&TYPENAMES=IGNF_LIDAR-HD_METADONNEE:metadata"
        "&OUTPUTFORMAT=application%2Fjson&COUNT=500&BBOX=45.760000,4.830000,45.770000,4.840000,urn:ogc:def:crs:EPSG::4326"
    )
    assert meta == {
        "layer": "IGNF_LIDAR-HD_METADONNEE:metadata",
        "tiles": [{
            "geometry": TILE, "date_debut_acquisition": "2021-04-14Z", "date_fin_acquisition": "2021-09-24Z",
            "date_edition": "2025-02-20Z", "code_mission": "21LHD6OL",
        }],
    }
    assert HD.data_date(*LYON, meta) == "acquired 2021-04-14 to 2021-09-24"
    assert HD.data_date(2.35, 48.85, meta) == "acquisition date unknown"
    assert HD.data_date(*LYON, {"tiles": [{"geometry": TILE}]}) == "acquired ? to ?"
    assert HD.data_date(*LYON, {}) == "acquisition date unknown"


def test_metadata_orders_tiles_and_tolerates_missing_properties() -> None:
    other = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}
    reply = {"features": [{"geometry": TILE, "properties": None}, {"geometry": other}]}
    meta = HD.metadata((0, 0, 1, 1), lambda url: json.dumps(reply).encode())
    assert [t["geometry"]["type"] for t in meta["tiles"]] == ["Polygon", "MultiPolygon"]
    assert meta["tiles"][1]["code_mission"] is None


@pytest.mark.parametrize("reply", [b"<html/>", b"{}", b"[]", b'{"features": [1]}'])
def test_metadata_refuses_a_reply_that_is_not_features(reply: bytes) -> None:
    with pytest.raises(SourceError, match="^LiDAR HD metadata response is not GeoJSON features: "):
        HD.metadata((0, 0, 1, 1), lambda url: reply)


def test_fetch_and_normalize_measure_the_mnh_with_its_tile_date(tmp_path: Path) -> None:
    scene = tmp_path / "geo_scene.json"
    scene.write_text(json.dumps(_scene()), encoding="utf-8")
    raw = tmp_path / "raw" / "fr.lidar_hd"
    service = FakeGeoplateforme()
    record = lidar.fetch_raster(HD, scene, raw, fetch=service, now=datetime(2026, 10, 30, tzinfo=UTC))
    assert record["crs"] == "EPSG:2154" and record["license"] == "etalab-2.0"
    assert record["metadata"]["tiles"][0]["code_mission"] == "21LHD6OL"
    assert service.urls[0].startswith("https://data.geopf.fr/wfs/ows?") and service.urls[1].startswith("https://data.geopf.fr/wms-r/wms?")
    report = lidar.normalize_raster(HD, scene, raw, tmp_path / "out")
    assert report["counts"] == {"buildings": 1, "measured": 1, "out_of_bounds": 0, "no_data": 0}
    (entry,) = json.loads((tmp_path / "out" / "heights_overrides.fr.lidar_hd.json").read_text(encoding="utf-8"))["overrides"]
    assert entry["height_m"] == 21.0 and entry["license"] == "etalab-2.0" and entry["confidence"] == 0.9
    assert entry["source"] == "IGN LiDAR HD MNH 0.5 m"
    assert entry["note"].endswith("data acquired 2021-04-14 to 2021-09-24")


def test_run_in_france_adds_only_the_lidar_hd_stages(tmp_path: Path) -> None:
    root = (tmp_path / "lyon").resolve()
    create_map(root, map_id="lyon", name="Lyon", latitude=LYON[1], longitude=LYON[0], radius_m=150)
    geo = root / "data" / "processed" / "geo" / "geo_scene.json"
    stages, scene, overrides = source_plan(root, geo)
    assert [s.name for s in stages] == ["lidar_hd", "lidar_hd_heights"]
    assert scene == geo
    assert overrides == root / "data" / "processed" / "fr.lidar_hd" / "heights_overrides.fr.lidar_hd.json"
