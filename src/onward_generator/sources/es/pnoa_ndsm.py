"""``es.pnoa_ndsm``: building heights from the IGN MDSn 2.5 m (building class), PNOA-LiDAR first coverage.

Service: the IGN WCS 2.0 ``https://wcs-mds.idee.es/mds``, coverage
``mdsn_e025``, asked for an ESRI ASCII grid in EPSG:25830 (the data are
ETRS89 UTM 30 extended over all of Spain). A cell holds the height above
ground of the LiDAR points classed as building, or 0 where there is none, so
trees are already out. The first coverage was flown 2008-2015; the data can
predate a building. Licence CC BY 4.0 (``ign.es``). docs/04-heights.md#lidar.
"""

from __future__ import annotations

import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from onward_generator.cadastre.zones import load_zones
from onward_generator.sources.lidar import Fetch, HeightRule, TileRequest, raster_steps
from onward_generator.sources.plugin import Kind, LicenceRecord, SourceError, SourceSteps
from onward_generator.sources.raster import BBox, Grid, parse_ascii_grid, snap_bbox, tiles

WCS_URL = "https://wcs-mds.idee.es/mds"
COVERAGE = "mdsn_e025"
CRS = "EPSG:25830"
CELL_M = 2.5
MAX_CELLS = 1000
"""2.5 km a side per request; the largest map needs 9."""
SERIES = "PNOA-LiDAR 1st coverage, 2008-2015"
TERRITORIES = ("peninsula", "balearic-islands", "canary-islands")
"""Ceuta, Melilla and Alborán are not in the MDSn (IGN). Navarre and the Basque Country are."""


def asc_part(body: bytes) -> bytes:
    """The ESRI ASCII grid inside the WCS multipart response (or the body itself when it is the grid)."""
    stripped = body.lstrip()
    if stripped.startswith(b"ncols"):
        return stripped
    if not stripped.startswith(b"--"):
        raise SourceError(f"WCS response is neither a grid nor multipart: {stripped[:120]!r}")
    boundary = stripped.partition(b"\n")[0].strip()
    for part in stripped.split(boundary):
        head, sep, payload = part.partition(b"\r\n\r\n")
        if not sep:
            head, sep, payload = part.partition(b"\n\n")
        if sep and b"application/asc" in head:
            return payload.strip()
    raise SourceError("WCS multipart response has no application/asc part")


def in_mdsn(longitude: float, latitude: float) -> bool:
    from shapely import Point

    point = Point(longitude, latitude)
    return any(t.name in TERRITORIES and t.polygon.covers(point) for t in load_zones().territories)


def wcs_requests(bbox: BBox) -> list[TileRequest]:
    out = []
    for tile, columns, rows in tiles(snap_bbox(bbox, CELL_M), CELL_M, MAX_CELLS):
        xmin, ymin, xmax, ymax = tile
        crs_uri = f"http://www.opengis.net/def/crs/EPSG/0/{CRS.split(':')[1]}"
        query = urllib.parse.urlencode(
            [
                ("service", "WCS"), ("version", "2.0.1"), ("request", "GetCoverage"), ("CoverageId", COVERAGE),
                ("format", "application/asc"),
                ("subset", f"x({xmin:.2f},{xmax:.2f})"), ("subset", f"y({ymin:.2f},{ymax:.2f})"),
                ("subsettingCRS", crs_uri), ("outputCRS", crs_uri),
            ],
            safe="(),:/",
        )
        out.append(TileRequest(f"{WCS_URL}?{query}", tile, columns, rows))
    return out


def decode_asc(body: bytes, request: Mapping[str, Any]) -> Grid:
    grid = parse_ascii_grid(asc_part(body))
    if grid.values.shape != (request["rows"], request["columns"]):
        raise SourceError(f"WCS grid is {grid.values.shape}, asked for {(request['rows'], request['columns'])}")
    return grid


@dataclass(frozen=True, slots=True)
class PnoaNdsmPlugin:
    id: str = "es.pnoa_ndsm"
    licence: LicenceRecord = LicenceRecord(
        spdx="CC-BY-4.0",
        name="Creative Commons Attribution 4.0 (IGN / CNIG, ign.es)",
        url="https://www.ign.es/resources/licencia/Condiciones_licenciaUso_IGN.pdf",
        attribution="Obra derivada de MDSn 2008-2015 CC-BY 4.0 ign.es",
        obligations=(
            "Cite the source as 'MDSn <year> CC-BY 4.0 ign.es'; a modified product says 'Obra derivada de'. "
            "Heights per footprint are a derived work of the IGN MDSn (first PNOA-LiDAR coverage)."
        ),
    )
    produces: frozenset[Kind] = frozenset({"height_m"})
    stage_names: tuple[str, str] = ("pnoa_ndsm", "pnoa_heights")
    raster_crs: str = CRS
    hosts: frozenset[str] = frozenset({"wcs-mds.idee.es"})
    source_label: str = f"IGN MDSn 2.5 m building class ({SERIES})"
    rule: HeightRule = HeightRule(buffer_m=1.0, percentile=50.0, min_cells=3, min_valid_share=0.5, min_value=2.0, confidence=0.8)

    def covers(self, longitude: float, latitude: float) -> bool:
        return in_mdsn(longitude, latitude)

    def steps(self, root: Path, scene: Path) -> SourceSteps:
        return raster_steps(self, root, scene)

    def requests(self, bbox: BBox) -> list[TileRequest]:
        return wcs_requests(bbox)

    def decode(self, body: bytes, request: Mapping[str, Any]) -> Grid:
        return decode_asc(body, request)

    def metadata(self, bbox_wgs84: BBox, fetch: Fetch) -> dict[str, Any]:
        return {"series": SERIES, "coverage": COVERAGE, "service": WCS_URL}

    def data_date(self, longitude: float, latitude: float, metadata: Mapping[str, Any]) -> str:
        return SERIES
