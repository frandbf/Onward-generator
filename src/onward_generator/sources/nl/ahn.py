"""``nl.ahn``: building heights from the AHN surface and terrain models (DSM and DTM, 0.5 m), opt-in.

Service: the PDOK WCS 2.0.1
``https://service.pdok.nl/rws/actueel-hoogtebestand-nederland/wcs/v1_0``,
coverages ``dsm_05m`` and ``dtm_05m``, asked for float32 GeoTIFF in their native
RD New grid (EPSG:28992, 0.5 m cells with edges on multiples of 0.5 m), so
nothing is resampled. Heights are metres above NAP. The DSM keeps every point
but water and the DTM only the ground, so the DTM has no data under buildings:
``surface.height_above_ground`` fills it from the ground around, up to
``FILL_M``, before subtracting. The service abstract names AHN4, flown
2020-2022. Licence CC0 1.0 (PDOK capabilities). Opt-in: only
``run --source nl.ahn`` turns it on. docs/04-heights.md#lidar-heights.
"""

from __future__ import annotations

import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from onward_generator.sources.coverage import CONFIG_DIR, in_coverage
from onward_generator.sources.lidar import Fetch, HeightRule, LayerTileRequest, TileRequest, raster_steps
from onward_generator.sources.plugin import Kind, LicenceRecord, SourceSteps
from onward_generator.sources.raster import BBox, Grid, snap_bbox, tiles
from onward_generator.sources.surface import height_above_ground, parse_tiff_float32

WCS_URL = "https://service.pdok.nl/rws/actueel-hoogtebestand-nederland/wcs/v1_0"
COVERAGES = (("dsm", "dsm_05m"), ("dtm", "dtm_05m"))
"""(layer, WCS coverage id), surface first."""
CRS = "EPSG:28992"
CELL_M = 0.5
MAX_CELLS = 2000
"""1 km a side per request and layer."""
NODATA = 3.4028234663852886e38
"""The float32 maximum, the GDAL_NODATA of both coverages."""
FILL_M = 20.0
"""How far the terrain is filled under a building: a footprint up to about 40 m across is filled through."""
SERIES = "AHN4, flown 2020-2022"
COVERAGE_PATH = CONFIG_DIR / "ahn_coverage.json"


def wcs_requests(bbox: BBox) -> list[TileRequest]:
    """A DSM and a DTM request per tile of ``bbox`` on the native grid, surface first."""
    out: list[TileRequest] = []
    for tile, columns, rows in tiles(snap_bbox(bbox, CELL_M), CELL_M, MAX_CELLS):
        xmin, ymin, xmax, ymax = tile
        for layer, coverage_id in COVERAGES:
            query = urllib.parse.urlencode(
                [
                    ("service", "WCS"), ("version", "2.0.1"), ("request", "GetCoverage"), ("CoverageId", coverage_id),
                    ("format", "image/tiff"), ("subset", f"x({xmin:.2f},{xmax:.2f})"), ("subset", f"y({ymin:.2f},{ymax:.2f})"),
                ],
                safe="(),/",  # pragma: no mutate: letters are never escaped, so adding them to safe changes nothing
            )
            out.append(LayerTileRequest(f"{WCS_URL}?{query}", tile, columns, rows, layer))
    return out


def decode_tiff(body: bytes, request: Mapping[str, Any]) -> Grid:
    xmin, ymin, xmax, ymax = request["bbox"]
    return parse_tiff_float32(body, (xmin, ymin, xmax, ymax), request["columns"], request["rows"], nodata=NODATA)


@dataclass(frozen=True, slots=True)
class AhnPlugin:
    id: str = "nl.ahn"
    licence: LicenceRecord = LicenceRecord(
        spdx="CC0-1.0",
        name="Creative Commons Zero 1.0 (Rijkswaterstaat AHN, PDOK)",
        url="https://creativecommons.org/publicdomain/zero/1.0/deed.nl",
        attribution="Actueel Hoogtebestand Nederland (AHN), Rijkswaterstaat / PDOK",
        obligations=(
            "None under CC0. Credit AHN and its version as good practice; "
            "heights per footprint are derived from the DSM minus a filled DTM."
        ),
    )
    produces: frozenset[Kind] = frozenset({"height_m"})
    opt_in: bool = True
    stage_names: tuple[str, str] = ("ahn", "ahn_heights")
    raster_crs: str = CRS
    hosts: frozenset[str] = frozenset({"service.pdok.nl"})
    source_label: str = "AHN DSM minus filled DTM 0.5 m (PDOK)"
    rule: HeightRule = HeightRule(buffer_m=1.0, percentile=50.0, min_cells=8, min_valid_share=0.5, min_value=2.0, confidence=0.85)
    layers: tuple[str, ...] = ("dsm", "dtm")

    def covers(self, longitude: float, latitude: float) -> bool:
        return in_coverage(COVERAGE_PATH, longitude, latitude)

    def steps(self, root: Path, scene: Path) -> SourceSteps:
        return raster_steps(self, root, scene)

    def requests(self, bbox: BBox) -> list[TileRequest]:
        return wcs_requests(bbox)

    def decode(self, body: bytes, request: Mapping[str, Any]) -> Grid:
        return decode_tiff(body, request)

    def combine(self, grids: Mapping[str, Grid]) -> Grid:
        return height_above_ground(grids["dsm"], grids["dtm"], FILL_M)

    def metadata(self, bbox_wgs84: BBox, fetch: Fetch) -> dict[str, Any]:
        return {"series": SERIES, "coverages": [coverage_id for _layer, coverage_id in COVERAGES], "service": WCS_URL, "fill_m": FILL_M}

    def data_date(self, longitude: float, latitude: float, metadata: Mapping[str, Any]) -> str:
        return SERIES
