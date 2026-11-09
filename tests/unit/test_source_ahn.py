"""nl.ahn: AHN DSM minus a filled DTM, with a fake PDOK WCS (ADR-018 amendment, plan D4)."""

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
from onward_generator.sources.nl import ahn
from onward_generator.sources.nl.ahn import AhnPlugin
from onward_generator.sources.plugin import SourceError
from onward_generator.sources.raster import Grid
from tests.conftest import MURCIA_CENTER_LAT, MURCIA_CENTER_LON

pytestmark = pytest.mark.usefixtures("without_overture")  # these tests pin the national plugins; ADR-021 adds overture everywhere

AHN = AhnPlugin()
AMSTERDAM = (4.8952, 52.3702)
ORIGIN = (628500.0, 5804000.0)  # EPSG:32631, central Amsterdam
NOT_COVERED = {
    "Murcia": (MURCIA_CENTER_LON, MURCIA_CENTER_LAT), "Madrid": (-3.7038, 40.4168), "Paris": (2.3522, 48.8566),
    "Krakow": (19.9450, 50.0647), "Brussels": (4.3517, 50.8503), "Koeln": (6.9603, 50.9375), "London": (-0.1276, 51.5072),
}


def tiff(values: np.ndarray) -> bytes:
    out = io.BytesIO()
    Image.fromarray(np.asarray(values, dtype=np.float32)).save(out, format="TIFF", compression="tiff_adobe_deflate")
    return out.getvalue()


def _scene(*boxes: tuple[float, float, float, float]) -> dict[str, Any]:
    features = [
        {"id": f"osm-way-{index + 1}", "kind": "building", "tags": {"building": "yes"}, "geometry": mapping(box(*b))}
        for index, b in enumerate(boxes or ((0, 0, 12, 8),))
    ]
    return {
        "schema": "onward_generator.geo_scene",
        "schema_version": "0.2.0",
        "frame": {"crs": "EPSG:32631", "coordinate_system": "local_grid", "unit": "m", "origin_projected": list(ORIGIN)},
        "features": features,
    }


class FakePdok:
    """DSM 14 m over the buildings and 2 m elsewhere; DTM 2 m with no data under the buildings."""

    def __init__(self, scene: dict[str, Any], *, short: bool = False) -> None:
        self.buildings = shapely.union_all([g for _id, g in lidar.footprints(scene, ahn.CRS)])
        self.urls: list[str] = []
        self.short = short

    def __call__(self, url: str) -> bytes:
        self.urls.append(url)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        (x,), (y,) = ([s[2:-1] for s in query["subset"] if s.startswith(axis)] for axis in ("x(", "y("))
        xmin, xmax = (float(v) for v in x.split(","))
        ymin, ymax = (float(v) for v in y.split(","))
        columns, rows = round((xmax - xmin) / 0.5), round((ymax - ymin) / 0.5)
        cx = xmin + (np.arange(columns) + 0.5) * 0.5
        cy = ymax - (np.arange(rows) + 0.5) * 0.5
        gx, gy = np.meshgrid(cx, cy)
        inside = shapely.contains_xy(self.buildings, gx, gy)
        if query["CoverageId"] == ["dsm_05m"]:
            values = np.where(inside, 14.0, 2.0)
        else:
            values = np.where(inside, ahn.NODATA, 2.0)
        return tiff(values[:-1] if self.short else values)


def _scene_file(tmp_path: Path, scene: dict[str, Any]) -> Path:
    path = tmp_path / "geo_scene.json"
    path.write_text(json.dumps(scene), encoding="utf-8")
    return path


# --- identity, licence, coverage ------------------------------------------------------------


def test_ahn_identity_licence_and_rule() -> None:
    assert AHN.id == "nl.ahn"
    assert AHN.opt_in is True
    assert AHN.produces == frozenset({"height_m"})
    assert AHN.stage_names == ("ahn", "ahn_heights")
    assert AHN.raster_crs == "EPSG:28992"
    assert AHN.hosts == frozenset({"service.pdok.nl"})
    assert AHN.source_label == "AHN DSM minus filled DTM 0.5 m (PDOK)"
    assert AHN.layers == ("dsm", "dtm")
    assert AHN.licence.spdx == "CC0-1.0"
    assert AHN.licence.name == "Creative Commons Zero 1.0 (Rijkswaterstaat AHN, PDOK)"
    assert AHN.licence.url == "https://creativecommons.org/publicdomain/zero/1.0/deed.nl"
    assert AHN.licence.attribution == "Actueel Hoogtebestand Nederland (AHN), Rijkswaterstaat / PDOK"
    assert AHN.licence.obligations == (
        "None under CC0. Credit AHN and its version as good practice; heights per footprint are derived from the DSM minus a filled DTM."
    )
    assert AHN.rule == lidar.HeightRule(buffer_m=1.0, percentile=50.0, min_cells=8, min_valid_share=0.5, min_value=2.0, confidence=0.85)
    assert (ahn.CELL_M, ahn.MAX_CELLS, ahn.FILL_M, ahn.NODATA) == (0.5, 2000, 20.0, 3.4028234663852886e38)
    assert isinstance(AHN, lidar.RasterHeights) and isinstance(AHN, lidar.SurfaceModelHeights)


def test_ahn_covers_the_netherlands_and_none_of_the_bench_cities() -> None:
    assert AHN.covers(*AMSTERDAM)
    assert AHN.covers(4.4777, 51.9244)  # Rotterdam
    assert AHN.covers(5.1214, 52.0907)  # Utrecht
    assert AHN.covers(5.6909, 50.8514)  # Maastricht
    assert AHN.covers(6.5665, 53.2194)  # Groningen
    for name, point in NOT_COVERED.items():
        assert not AHN.covers(*point), name
    assert len(load_coverage(ahn.COVERAGE_PATH)) == 1


# --- requests and decode --------------------------------------------------------------------


def test_requests_ask_both_layers_on_the_native_grid() -> None:
    dsm, dtm = AHN.requests((121250.3, 487420.1, 121299.7, 487469.9))
    assert isinstance(dsm, lidar.LayerTileRequest) and (dsm.layer, dtm.layer) == ("dsm", "dtm")
    assert dsm.bbox == dtm.bbox == (121250.0, 487420.0, 121300.0, 487470.0)
    assert (dsm.columns, dsm.rows) == (100, 100)
    assert dsm.url == (
        "https://service.pdok.nl/rws/actueel-hoogtebestand-nederland/wcs/v1_0?service=WCS&version=2.0.1&request=GetCoverage"
        "&CoverageId=dsm_05m&format=image/tiff&subset=x(121250.00,121300.00)&subset=y(487420.00,487470.00)"
    )
    assert dtm.url == dsm.url.replace("dsm_05m", "dtm_05m")


def test_requests_split_past_max_cells() -> None:
    edge = ahn.MAX_CELLS * 0.5
    assert [(r.columns, r.layer) for r in AHN.requests((0.0, 0.0, edge, 1.0))] == [(2000, "dsm"), (2000, "dtm")]
    split = AHN.requests((0.0, 0.0, edge + 0.5, 1.0))
    assert [(r.columns, r.rows, r.layer) for r in split] == [(2000, 2, "dsm"), (2000, 2, "dtm"), (1, 2, "dsm"), (1, 2, "dtm")]


def test_decode_reads_a_tiff_and_drops_the_nodata_value() -> None:
    values = np.array([[5.0, ahn.NODATA]], dtype=np.float32)
    grid = AHN.decode(tiff(values), {"bbox": [10.0, 20.0, 11.0, 20.5], "columns": 2, "rows": 1, "layer": "dtm"})
    assert (grid.west, grid.north, grid.cell) == (10.0, 20.5, 0.5)
    assert grid.values[0, 0] == 5.0 and np.isnan(grid.values[0, 1])


def test_decode_refuses_an_exception_report_and_a_wrong_size() -> None:
    request = {"bbox": [10.0, 20.0, 11.0, 20.5], "columns": 2, "rows": 1}
    with pytest.raises(SourceError, match="^reply is not a TIFF: b'<ows:ExceptionReport"):
        AHN.decode(b"<ows:ExceptionReport/>", request)
    with pytest.raises(SourceError, match="^TIFF reply is 3 x 1 cells, asked for 2 x 1$"):
        AHN.decode(tiff(np.zeros((1, 3))), request)


def test_metadata_and_date_name_the_series_and_the_fill() -> None:
    meta = AHN.metadata((4.8, 52.3, 4.9, 52.4), lambda url: pytest.fail("no metadata request"))
    assert meta == {"series": "AHN4, flown 2020-2022", "coverages": ["dsm_05m", "dtm_05m"], "service": ahn.WCS_URL, "fill_m": 20.0}
    assert AHN.data_date(*AMSTERDAM, meta) == "AHN4, flown 2020-2022"


def test_combine_subtracts_the_filled_terrain() -> None:
    dsm = Grid(np.full((4, 4), 14.0, dtype=np.float32), 0.0, 2.0, 0.5)
    dtm = Grid(np.full((4, 4), 2.0, dtype=np.float32), 0.0, 2.0, 0.5)
    dtm.values[1:3, 1:3] = np.nan
    assert AHN.combine({"dsm": dsm, "dtm": dtm}).values.tolist() == np.full((4, 4), 12.0).tolist()


# --- fetch and normalize --------------------------------------------------------------------


def test_the_scene_box_is_in_rd_metres_not_degrees() -> None:
    xmin, ymin, xmax, ymax = lidar.scene_bbox(_scene((0, 0, 10, 10)), ahn.CRS)
    assert 49.0 < xmax - xmin < 51.0 and 49.0 < ymax - ymin < 51.0
    assert 120000 < xmin < 123000 and 486000 < ymin < 489000


def test_fetch_and_normalize_measure_the_surface_above_the_filled_ground(tmp_path: Path) -> None:
    scene = _scene((0, 0, 12, 8), (30, 0, 31, 1))
    path = _scene_file(tmp_path, scene)
    raw = tmp_path / "raw" / "nl.ahn"
    service = FakePdok(scene)
    record = lidar.fetch_raster(AHN, path, raw, fetch=service, now=datetime(2026, 11, 9, tzinfo=UTC))
    assert record["crs"] == "EPSG:28992" and record["license"] == "CC0-1.0"
    assert [entry["layer"] for entry in record["tiles"]] == ["dsm", "dtm"]
    assert [url.split("CoverageId=")[1][:7] for url in service.urls] == ["dsm_05m", "dtm_05m"]
    report = lidar.normalize_raster(AHN, path, raw, tmp_path / "out")
    assert report["counts"] == {"buildings": 2, "measured": 1, "out_of_bounds": 0, "no_data": 1}
    big, tiny = report["buildings"]
    assert (big["id"], big["status"], big["height_m"], big["date"]) == ("osm-way-1", "measured", 12.0, "AHN4, flown 2020-2022")
    assert (tiny["id"], tiny["status"]) == ("osm-way-2", "no_data")
    (entry,) = json.loads((tmp_path / "out" / "heights_overrides.nl.ahn.json").read_text(encoding="utf-8"))["overrides"]
    assert entry["id"] == "osm-way-1" and entry["height_m"] == 12.0
    assert entry["license"] == "CC0-1.0" and entry["confidence"] == 0.85
    assert entry["source"] == "AHN DSM minus filled DTM 0.5 m (PDOK)"
    assert entry["note"].endswith("data AHN4, flown 2020-2022")
    assert report["schema_version"] == "1.0.0" and report["attribution"] == AHN.licence.attribution


def test_a_wrong_size_reply_is_refused_before_anything_is_written(tmp_path: Path) -> None:
    scene = _scene()
    raw = tmp_path / "raw" / "nl.ahn"
    with pytest.raises(SourceError, match="^TIFF reply is "):
        lidar.fetch_raster(AHN, _scene_file(tmp_path, scene), raw, fetch=FakePdok(scene, short=True))
    assert not raw.exists() and not lidar.sidecar_path(raw).exists()


def test_load_raster_refuses_a_sidecar_without_a_layer(tmp_path: Path) -> None:
    scene = _scene()
    path = _scene_file(tmp_path, scene)
    raw = tmp_path / "raw" / "nl.ahn"
    lidar.fetch_raster(AHN, path, raw, fetch=FakePdok(scene))
    side = lidar.sidecar_path(raw)
    record = json.loads(side.read_text(encoding="utf-8"))
    record["tiles"] = [entry for entry in record["tiles"] if entry["layer"] == "dsm"]
    side.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(SourceError) as caught:
        lidar.load_raster(AHN, raw)
    assert str(caught.value) == "nl.ahn.source.json has no dtm tiles"


def test_load_raster_refuses_a_tampered_layer_tile(tmp_path: Path) -> None:
    scene = _scene()
    path = _scene_file(tmp_path, scene)
    raw = tmp_path / "raw" / "nl.ahn"
    lidar.fetch_raster(AHN, path, raw, fetch=FakePdok(scene))
    (raw / "tile_001.bin").write_bytes(tiff(np.zeros((2, 2))))
    with pytest.raises(SourceError, match="tile_001.bin does not match the sha256 recorded in nl.ahn.source.json"):
        lidar.load_raster(AHN, raw)


def test_http_fetch_refuses_a_host_outside_pdok() -> None:
    with pytest.raises(SourceError, match="^refusing to fetch host 'example.org' over https$"):
        lidar.http_fetch(AHN, pause_s=0.0)("https://example.org/wcs")


# --- run ------------------------------------------------------------------------------------


def test_run_in_amsterdam_adds_the_ahn_stages_only_when_switched_on(tmp_path: Path) -> None:
    root = (tmp_path / "amsterdam").resolve()
    create_map(root, map_id="amsterdam", name="Amsterdam", latitude=AMSTERDAM[1], longitude=AMSTERDAM[0], radius_m=150)
    geo = root / "data" / "processed" / "geo" / "geo_scene.json"
    assert source_plan(root, geo) == ([], geo, None)
    store_sources(root, {"nl.ahn": True})
    stages, scene, overrides = source_plan(root, geo)
    assert [s.name for s in stages] == ["ahn", "ahn_heights"]
    assert scene == geo
    assert overrides == root / "data" / "processed" / "nl.ahn" / "heights_overrides.nl.ahn.json"
    store_sources(root, {"nl.ahn": False})
    assert source_plan(root, geo) == ([], geo, None)
