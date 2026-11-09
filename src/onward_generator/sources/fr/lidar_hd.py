"""``fr.lidar_hd``: building heights from the IGN LiDAR HD height model (MNH, 0.5 m).

Service: the Géoplateforme raster WMS ``https://data.geopf.fr/wms-r``, layer
``IGNF_LIDAR-HD_MNH_ELEVATION.ELEVATIONGRIDCOVERAGE.LAMB93``, asked for raw
float32 (``image/x-bil;bits=32``) in its native Lambert-93 grid (EPSG:2154,
0.5 m cells with edges at 0.25 m), so nothing is resampled. The MNH is the
surface model minus the terrain model: buildings and trees alike, which is
why the footprint is shrunk before the median. The acquisition dates of each
1 km tile come from the WFS layer ``IGNF_LIDAR-HD_METADONNEE:metadata``.
Licence Ouverte 2.0 (Etalab). docs/04-heights.md#lidar.
"""

from __future__ import annotations

import json
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from shapely import Point, Polygon
from shapely.geometry import shape

from onward_generator.sources.lidar import Fetch, HeightRule, TileRequest, raster_steps
from onward_generator.sources.plugin import Kind, LicenceRecord, SourceError, SourceSteps
from onward_generator.sources.raster import BBox, Grid, parse_float32, snap_bbox, tiles

WMS_URL = "https://data.geopf.fr/wms-r/wms"
WFS_URL = "https://data.geopf.fr/wfs/ows"
LAYER = "IGNF_LIDAR-HD_MNH_ELEVATION.ELEVATIONGRIDCOVERAGE.LAMB93"
METADATA_LAYER = "IGNF_LIDAR-HD_METADONNEE:metadata"
CRS = "EPSG:2154"
CELL_M = 0.5
GRID_OFFSET_M = 0.25
MAX_CELLS = 2000
"""1 km a side per request, the size of an IGN tile."""
NODATA_BELOW = -1000.0
"""No-data cells (restricted zones) hold a large negative value; real MNH noise stays above -10 m."""
REPO_ROOT = Path(__file__).resolve().parents[4]
COVERAGE_PATH = REPO_ROOT / "config" / "lidar_hd_coverage.json"


@cache
def coverage(path: Path = COVERAGE_PATH) -> tuple[Polygon, ...]:
    document = json.loads(path.read_bytes())
    if document.get("schema") != "onward_generator.source_coverage":
        raise SourceError(f"{path.name}: schema must be onward_generator.source_coverage")
    return tuple(Polygon(rings[0], rings[1:]) for rings in document["polygons"])


def in_coverage(longitude: float, latitude: float) -> bool:
    point = Point(longitude, latitude)
    return any(polygon.covers(point) for polygon in coverage())


def wms_requests(bbox: BBox) -> list[TileRequest]:
    out = []
    for tile, columns, rows in tiles(snap_bbox(bbox, CELL_M, GRID_OFFSET_M), CELL_M, MAX_CELLS):
        query = urllib.parse.urlencode(
            [
                ("SERVICE", "WMS"), ("VERSION", "1.3.0"), ("REQUEST", "GetMap"), ("LAYERS", LAYER), ("STYLES", ""),
                ("CRS", CRS), ("BBOX", ",".join(f"{v:.2f}" for v in tile)),
                ("WIDTH", str(columns)), ("HEIGHT", str(rows)), ("FORMAT", "image/x-bil;bits=32"),
            ],
            safe=",:;/=",  # pragma: no mutate: letters are never escaped, so adding them to safe changes nothing
        )
        out.append(TileRequest(f"{WMS_URL}?{query}", tile, columns, rows))
    return out


def decode_mnh(body: bytes, request: Mapping[str, Any]) -> Grid:
    xmin, ymin, xmax, ymax = request["bbox"]
    return parse_float32(body, (xmin, ymin, xmax, ymax), request["columns"], request["rows"], nodata_below=NODATA_BELOW)


def tile_metadata(bbox_wgs84: BBox, fetch: Fetch) -> dict[str, Any]:
    west, south, east, north = bbox_wgs84
    query = urllib.parse.urlencode(
        [
            ("SERVICE", "WFS"), ("VERSION", "2.0.0"), ("REQUEST", "GetFeature"), ("TYPENAMES", METADATA_LAYER),
            ("OUTPUTFORMAT", "application/json"), ("COUNT", "500"),
            ("BBOX", f"{south:.6f},{west:.6f},{north:.6f},{east:.6f},urn:ogc:def:crs:EPSG::4326"),
        ],
        safe=",:",  # pragma: no mutate: letters are never escaped, so adding them to safe changes nothing
    )
    keep = ("date_debut_acquisition", "date_fin_acquisition", "date_edition", "code_mission")
    try:
        features = json.loads(fetch(f"{WFS_URL}?{query}"))["features"]
        tiles_out = [
            {"geometry": f["geometry"], **{key: (f.get("properties") or {}).get(key) for key in keep}}
            for f in features
        ]
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise SourceError(f"LiDAR HD metadata response is not GeoJSON features: {exc}") from exc
    return {"layer": METADATA_LAYER, "tiles": sorted(tiles_out, key=lambda t: json.dumps(t["geometry"], sort_keys=True))}


def tile_date(longitude: float, latitude: float, metadata: Mapping[str, Any]) -> str:
    point = Point(longitude, latitude)
    for tile in metadata.get("tiles", []):
        if shape(tile["geometry"]).covers(point):
            start = str(tile.get("date_debut_acquisition") or "?").removesuffix("Z")
            end = str(tile.get("date_fin_acquisition") or "?").removesuffix("Z")
            return f"acquired {start} to {end}"
    return "acquisition date unknown"


@dataclass(frozen=True, slots=True)
class LidarHdPlugin:
    id: str = "fr.lidar_hd"
    licence: LicenceRecord = LicenceRecord(
        spdx="etalab-2.0",
        name="Licence Ouverte / Open Licence 2.0 (Etalab)",
        url="https://www.etalab.gouv.fr/licence-ouverte-open-licence/",
        attribution="IGN, LiDAR HD MNH",
        obligations="Mention the source (IGN, LiDAR HD) and the date of the data; say the heights are derived from it.",
    )
    produces: frozenset[Kind] = frozenset({"height_m"})
    opt_in: bool = False
    stage_names: tuple[str, str] = ("lidar_hd", "lidar_hd_heights")
    raster_crs: str = CRS
    hosts: frozenset[str] = frozenset({"data.geopf.fr"})
    source_label: str = "IGN LiDAR HD MNH 0.5 m"
    rule: HeightRule = HeightRule(buffer_m=1.0, percentile=50.0, min_cells=8, min_valid_share=0.5, min_value=2.0, confidence=0.9)

    def covers(self, longitude: float, latitude: float) -> bool:
        return in_coverage(longitude, latitude)

    def steps(self, root: Path, scene: Path) -> SourceSteps:
        return raster_steps(self, root, scene)

    def requests(self, bbox: BBox) -> list[TileRequest]:
        return wms_requests(bbox)

    def decode(self, body: bytes, request: Mapping[str, Any]) -> Grid:
        return decode_mnh(body, request)

    def metadata(self, bbox_wgs84: BBox, fetch: Fetch) -> dict[str, Any]:
        return tile_metadata(bbox_wgs84, fetch)

    def data_date(self, longitude: float, latitude: float, metadata: Mapping[str, Any]) -> str:
        return tile_date(longitude, latitude, metadata)
