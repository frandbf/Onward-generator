"""``de.nrw_ndom``: building heights from the Geobasis NRW nDOM50 (0.5 m), opt-in.

Service: the WCS 2.0.1 ``https://www.wcs.nrw.de/geobasis/wcs_nw_ndom``,
coverage ``nw_ndom``, asked for float32 GeoTIFF in its native ETRS89 / UTM 32N
grid (EPSG:25832, 0.5 m cells with edges on multiples of 0.5 m), so nothing is
resampled. The nDOM50 is an image-based, leaf-on surface model (bDOM50, summer
flights) minus a 0.5 m LiDAR terrain model: height above ground of buildings
and trees, served ready, so no terrain fill is needed. The service gives no
flight date per tile and no nodata value; cells below ``min_value`` are not
valid anyway. Licence Datenlizenz Deutschland – Zero 2.0. Opt-in: only
``run --source de.nrw_ndom`` turns it on. docs/04-heights.md#lidar-heights.
"""

from __future__ import annotations

import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from onward_generator.sources.coverage import CONFIG_DIR, in_coverage
from onward_generator.sources.lidar import Fetch, HeightRule, TileRequest, raster_steps
from onward_generator.sources.plugin import Kind, LicenceRecord, SourceSteps
from onward_generator.sources.raster import BBox, Grid, snap_bbox, tiles
from onward_generator.sources.surface import parse_tiff_float32

WCS_URL = "https://www.wcs.nrw.de/geobasis/wcs_nw_ndom"
COVERAGE = "nw_ndom"
CRS = "EPSG:25832"
CELL_M = 0.5
MAX_CELLS = 2000
"""1 km a side per request; the service allows one 4000-cell original tile."""
SERIES = "nDOM50, continuously updated (flight date per tile not published)"
COVERAGE_PATH = CONFIG_DIR / "nrw_coverage.json"


def wcs_requests(bbox: BBox) -> list[TileRequest]:
    out = []
    for tile, columns, rows in tiles(snap_bbox(bbox, CELL_M), CELL_M, MAX_CELLS):
        xmin, ymin, xmax, ymax = tile
        query = urllib.parse.urlencode(
            [
                ("SERVICE", "WCS"), ("VERSION", "2.0.1"), ("REQUEST", "GetCoverage"), ("CoverageId", COVERAGE),
                ("FORMAT", "image/tiff"), ("SUBSET", f"x({xmin:.2f},{xmax:.2f})"), ("SUBSET", f"y({ymin:.2f},{ymax:.2f})"),
            ],
            safe="(),/",  # pragma: no mutate: letters are never escaped, so adding them to safe changes nothing
        )
        out.append(TileRequest(f"{WCS_URL}?{query}", tile, columns, rows))
    return out


def decode_tiff(body: bytes, request: Mapping[str, Any]) -> Grid:
    xmin, ymin, xmax, ymax = request["bbox"]
    return parse_tiff_float32(body, (xmin, ymin, xmax, ymax), request["columns"], request["rows"], nodata=None)


@dataclass(frozen=True, slots=True)
class NrwNdomPlugin:
    id: str = "de.nrw_ndom"
    licence: LicenceRecord = LicenceRecord(
        spdx="DL-DE-ZERO-2.0",
        name="Datenlizenz Deutschland – Zero – Version 2.0 (Geobasis NRW)",
        url="https://www.govdata.de/dl-de/zero-2-0",
        attribution="Geobasis NRW, nDOM50 (dl-de/zero-2-0)",
        obligations=(
            "None: any use is allowed without conditions. Credit Geobasis NRW as good practice; "
            "heights per footprint are derived from the nDOM50, and Geobasis NRW excludes liability."
        ),
    )
    produces: frozenset[Kind] = frozenset({"height_m"})
    opt_in: bool = True
    stage_names: tuple[str, str] = ("nrw_ndom", "nrw_heights")
    raster_crs: str = CRS
    hosts: frozenset[str] = frozenset({"www.wcs.nrw.de"})
    source_label: str = "Geobasis NRW nDOM50 0.5 m (image-based surface minus LiDAR terrain)"
    rule: HeightRule = HeightRule(buffer_m=1.0, percentile=50.0, min_cells=8, min_valid_share=0.5, min_value=2.0, confidence=0.8)

    def covers(self, longitude: float, latitude: float) -> bool:
        return in_coverage(COVERAGE_PATH, longitude, latitude)

    def steps(self, root: Path, scene: Path) -> SourceSteps:
        return raster_steps(self, root, scene)

    def requests(self, bbox: BBox) -> list[TileRequest]:
        return wcs_requests(bbox)

    def decode(self, body: bytes, request: Mapping[str, Any]) -> Grid:
        return decode_tiff(body, request)

    def metadata(self, bbox_wgs84: BBox, fetch: Fetch) -> dict[str, Any]:
        return {"series": SERIES, "coverage": COVERAGE, "service": WCS_URL}

    def data_date(self, longitude: float, latitude: float, metadata: Mapping[str, Any]) -> str:
        return SERIES
