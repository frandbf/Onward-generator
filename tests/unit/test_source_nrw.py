"""de.nrw_ndom: Geobasis NRW nDOM50 heights, with a fake WCS (ADR-018 amendment, plan D4)."""

from __future__ import annotations

import io
import json
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import shapely
from PIL import Image
from shapely import box
from shapely.geometry import mapping

from onward_generator.maps.workspace import create_map, store_sources
from onward_generator.pipeline_run import source_plan
from onward_generator.sources import lidar
from onward_generator.sources.coverage import load_coverage
from onward_generator.sources.de import nrw_ndom
from onward_generator.sources.de.nrw_ndom import NrwNdomPlugin
from onward_generator.sources.plugin import SourceError
from tests.conftest import MURCIA_CENTER_LAT, MURCIA_CENTER_LON

pytestmark = pytest.mark.usefixtures("without_overture")  # these tests pin the national plugins; ADR-021 adds overture everywhere

NRW = NrwNdomPlugin()
KOELN = (6.9583, 50.9413)
ORIGIN = (356600.0, 5645300.0)  # EPSG:32632, Cologne Cathedral
NOT_COVERED = {
    "Murcia": (MURCIA_CENTER_LON, MURCIA_CENTER_LAT), "Madrid": (-3.7038, 40.4168), "Paris": (2.3522, 48.8566),
    "Krakow": (19.9450, 50.0647), "Amsterdam": (4.8952, 52.3702), "Frankfurt": (8.6821, 50.1109), "Berlin": (13.4050, 52.5200),
}


def tiff(values: np.ndarray) -> bytes:
    out = io.BytesIO()
    Image.fromarray(np.asarray(values, dtype=np.float32)).save(out, format="TIFF")
    return out.getvalue()


def _scene(*boxes: tuple[float, float, float, float]) -> dict[str, Any]:
    features = [
        {"id": f"osm-way-{index + 1}", "kind": "building", "tags": {"building": "yes"}, "geometry": mapping(box(*b))}
        for index, b in enumerate(boxes or ((0, 0, 12, 8),))
    ]
    return {
        "schema": "onward_generator.geo_scene",
        "schema_version": "0.2.0",
        "frame": {"crs": "EPSG:32632", "coordinate_system": "local_grid", "unit": "m", "origin_projected": list(ORIGIN)},
        "features": features,
    }


class FakeWcs:
    """An nDOM of 18 m over the buildings and -0.5 m (ground noise) elsewhere."""

    def __init__(self, scene: dict[str, Any], *, reply: bytes | None = None) -> None:
        self.buildings = shapely.union_all([g for _id, g in lidar.footprints(scene, nrw_ndom.CRS)])
        self.urls: list[str] = []
        self.reply = reply

    def __call__(self, url: str) -> bytes:
        self.urls.append(url)
        if self.reply is not None:
            return self.reply
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        (x,), (y,) = ([s[2:-1] for s in query["SUBSET"] if s.startswith(axis)] for axis in ("x(", "y("))
        xmin, xmax = (float(v) for v in x.split(","))
        ymin, ymax = (float(v) for v in y.split(","))
        cx = xmin + (np.arange(round((xmax - xmin) / 0.5)) + 0.5) * 0.5
        cy = ymax - (np.arange(round((ymax - ymin) / 0.5)) + 0.5) * 0.5
        gx, gy = np.meshgrid(cx, cy)
        return tiff(np.where(shapely.contains_xy(self.buildings, gx, gy), 18.0, -0.5))


def _scene_file(tmp_path: Path, scene: dict[str, Any]) -> Path:
    path = tmp_path / "geo_scene.json"
    path.write_text(json.dumps(scene), encoding="utf-8")
    return path


def test_nrw_identity_licence_and_rule() -> None:
    assert NRW.id == "de.nrw_ndom"
    assert NRW.opt_in is True
    assert NRW.produces == frozenset({"height_m"})
    assert NRW.stage_names == ("nrw_ndom", "nrw_heights")
    assert NRW.raster_crs == "EPSG:25832"
    assert NRW.hosts == frozenset({"www.wcs.nrw.de"})
    assert NRW.source_label == "Geobasis NRW nDOM50 0.5 m (image-based surface minus LiDAR terrain)"
    assert NRW.licence.spdx == "DL-DE-ZERO-2.0"
    assert NRW.licence.name == "Datenlizenz Deutschland – Zero – Version 2.0 (Geobasis NRW)"
    assert NRW.licence.url == "https://www.govdata.de/dl-de/zero-2-0"
    assert NRW.licence.attribution == "Geobasis NRW, nDOM50 (dl-de/zero-2-0)"
    assert NRW.licence.obligations == (
        "None: any use is allowed without conditions. Credit Geobasis NRW as good practice; "
        "heights per footprint are derived from the nDOM50, and Geobasis NRW excludes liability."
    )
    assert NRW.rule == lidar.HeightRule(buffer_m=1.0, percentile=50.0, min_cells=8, min_valid_share=0.5, min_value=2.0, confidence=0.8)
    assert (nrw_ndom.CELL_M, nrw_ndom.MAX_CELLS) == (0.5, 2000)
    assert isinstance(NRW, lidar.RasterHeights)
    assert not isinstance(NRW, lidar.SurfaceModelHeights)


def test_nrw_covers_the_state_and_none_of_the_bench_cities() -> None:
    assert NRW.covers(*KOELN)
    assert NRW.covers(6.7735, 51.2277)  # Düsseldorf
    assert NRW.covers(7.4653, 51.5136)  # Dortmund
    assert NRW.covers(7.6261, 51.9607)  # Münster
    for name, point in NOT_COVERED.items():
        assert not NRW.covers(*point), name
    assert len(load_coverage(nrw_ndom.COVERAGE_PATH)) == 1


@pytest.mark.parametrize(("lon", "lat"), [
    (5.87, 51.05),  # Selfkant, the western tip
    (6.16, 51.87),  # Elten, by the Dutch border
    (6.83, 52.035),  # Vreden
    (7.04, 52.21),  # Gronau
    (7.72, 52.37),  # Recke, the north-west corner
    (8.97, 52.38),  # Petershagen, the north-east corner
    (9.25, 51.95),  # Lügde
    (9.38, 51.65),  # Beverungen, the eastern tip
    (8.62, 51.11),  # Hallenberg
    (8.42, 50.93),  # Bad Laasphe
    (8.085, 50.745),  # Burbach, the southern tip of Siegen-Wittgenstein
    (8.1, 50.815),  # Wilnsdorf
    (6.43, 50.48),  # Hellenthal, by the Belgian border
    (6.48, 50.36),  # Dahlem-Kronenburg, the southern tip
    (6.02, 50.77),  # Aachen-Vaalserquartier
])
def test_nrw_covers_the_border_towns(lon: float, lat: float) -> None:
    assert NRW.covers(lon, lat)


@pytest.mark.parametrize(("lon", "lat"), [
    (7.59, 50.36),  # Koblenz
    (9.48, 51.31),  # Kassel
    (9.73, 52.37),  # Hannover
    (5.90, 51.98),  # Arnhem
    (5.69, 50.85),  # Maastricht
])
def test_nrw_leaves_out_the_cities_beyond_its_borders(lon: float, lat: float) -> None:
    assert not NRW.covers(lon, lat)


def test_requests_keep_the_native_grid_and_split_past_max_cells() -> None:
    (only,) = NRW.requests((356500.2, 5645250.3, 356599.9, 5645349.6))
    assert only.bbox == (356500.0, 5645250.0, 356600.0, 5645350.0)
    assert (only.columns, only.rows) == (200, 200)
    assert only.url == (
        "https://www.wcs.nrw.de/geobasis/wcs_nw_ndom?SERVICE=WCS&VERSION=2.0.1&REQUEST=GetCoverage&CoverageId=nw_ndom"
        "&FORMAT=image/tiff&SUBSET=x(356500.00,356600.00)&SUBSET=y(5645250.00,5645350.00)"
    )
    edge = nrw_ndom.MAX_CELLS * 0.5
    assert [(r.columns, r.rows) for r in NRW.requests((0.0, 0.0, edge, 1.0))] == [(2000, 2)]
    assert [(r.columns, r.rows) for r in NRW.requests((0.0, 0.0, edge + 0.5, 1.0))] == [(2000, 2), (1, 2)]


def test_decode_reads_a_tiff_and_keeps_every_value() -> None:
    grid = NRW.decode(tiff(np.array([[18.0, -0.5]])), {"bbox": [10.0, 20.0, 11.0, 20.5], "columns": 2, "rows": 1})
    assert (grid.west, grid.north, grid.cell) == (10.0, 20.5, 0.5)
    assert grid.values.tolist() == [[18.0, -0.5]]
    big = np.float32(3.4028234663852886e38)
    assert NRW.decode(tiff(np.array([[big, 1.0]])), {"bbox": [10.0, 20.0, 11.0, 20.5], "columns": 2, "rows": 1}).values[0, 0] == big


def test_decode_refuses_an_exception_report_and_a_wrong_size() -> None:
    request = {"bbox": [10.0, 20.0, 11.0, 20.5], "columns": 2, "rows": 1}
    with pytest.raises(SourceError, match="^reply is not a TIFF: "):
        NRW.decode(b"<?xml version='1.0'?><ows:ExceptionReport/>", request)
    with pytest.raises(SourceError, match="^TIFF reply is 2 x 2 cells, asked for 2 x 1$"):
        NRW.decode(tiff(np.zeros((2, 2))), request)


def test_metadata_and_date_say_the_flight_date_is_not_published() -> None:
    meta = NRW.metadata((6.9, 50.9, 7.0, 51.0), lambda url: pytest.fail("no metadata request"))
    assert meta == {"series": nrw_ndom.SERIES, "coverage": "nw_ndom", "service": nrw_ndom.WCS_URL}
    assert NRW.data_date(*KOELN, meta) == "nDOM50, continuously updated (flight date per tile not published)"


def test_the_scene_box_is_in_utm32_metres_not_degrees() -> None:
    xmin, ymin, xmax, ymax = lidar.scene_bbox(_scene((0, 0, 10, 10)), nrw_ndom.CRS)
    assert 49.9 < xmax - xmin < 50.1 and 49.9 < ymax - ymin < 50.1  # UTM 32 on both sides: only the margin is added
    assert xmin == pytest.approx(ORIGIN[0] - 20.0, abs=0.01) and ymin == pytest.approx(ORIGIN[1] - 20.0, abs=0.01)


def test_fetch_and_normalize_measure_the_ndom(tmp_path: Path) -> None:
    scene = _scene((0, 0, 12, 8), (30, 0, 31, 1))
    path = _scene_file(tmp_path, scene)
    raw = tmp_path / "raw" / "de.nrw_ndom"
    service = FakeWcs(scene)
    record = lidar.fetch_raster(NRW, path, raw, fetch=service, now=datetime(2026, 11, 9, tzinfo=UTC))
    assert record["crs"] == "EPSG:25832" and record["license"] == "DL-DE-ZERO-2.0"
    assert "layer" not in record["tiles"][0]
    assert len(service.urls) == 1 and service.urls[0].startswith(nrw_ndom.WCS_URL + "?")
    report = lidar.normalize_raster(NRW, path, raw, tmp_path / "out")
    assert report["counts"] == {"buildings": 2, "measured": 1, "out_of_bounds": 0, "no_data": 1}
    (entry,) = json.loads((tmp_path / "out" / "heights_overrides.de.nrw_ndom.json").read_text(encoding="utf-8"))["overrides"]
    assert entry["id"] == "osm-way-1" and entry["height_m"] == 18.0
    assert entry["license"] == "DL-DE-ZERO-2.0" and entry["confidence"] == 0.8
    assert entry["note"].endswith("data nDOM50, continuously updated (flight date per tile not published)")


def test_an_error_reply_is_refused_before_anything_is_written(tmp_path: Path) -> None:
    scene = _scene()
    raw = tmp_path / "raw" / "de.nrw_ndom"
    with pytest.raises(SourceError, match="^reply is not a TIFF: "):
        lidar.fetch_raster(NRW, _scene_file(tmp_path, scene), raw, fetch=FakeWcs(scene, reply=b"<ServiceExceptionReport/>"))
    assert not raw.exists() and not lidar.sidecar_path(raw).exists()


def test_http_fetch_refuses_a_host_outside_the_nrw_service() -> None:
    with pytest.raises(SourceError, match="^refusing to fetch host 'service.pdok.nl' over https$"):
        lidar.http_fetch(NRW, pause_s=0.0)("https://service.pdok.nl/wcs")


def test_run_in_cologne_adds_the_nrw_stages_only_when_switched_on(tmp_path: Path) -> None:
    root = (tmp_path / "koeln").resolve()
    create_map(root, map_id="koeln", name="Koeln", latitude=KOELN[1], longitude=KOELN[0], radius_m=150)
    geo = root / "data" / "processed" / "geo" / "geo_scene.json"
    assert source_plan(root, geo) == ([], geo, None)
    store_sources(root, {"de.nrw_ndom": True})
    stages, scene, overrides = source_plan(root, geo)
    assert [s.name for s in stages] == ["nrw_ndom", "nrw_heights"]
    assert overrides == root / "data" / "processed" / "de.nrw_ndom" / "heights_overrides.de.nrw_ndom.json"
