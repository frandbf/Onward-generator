"""Where the DG Catastro WFS serves building parts, and in which CRS to ask for them.

The table is ``config/catastro_zones.json``: coarse WGS84 territory polygons,
the territories with their own (foral) cadastre, and for each territory the
request CRS by UTM zone. Every CRS must be one the service lists in its
GetCapabilities and the UTM CRS of its zone, so the request is in metres. docs/05-cadastre.md#coverage.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from pyproj import CRS as ProjCRS
from pyproj import Transformer
from shapely import Point, Polygon

from onward_generator.cadastre.acquire import BBox, CadastreError, bbox_from_scene

REPO_ROOT = Path(__file__).resolve().parents[3]
ZONES_PATH = REPO_ROOT / "config" / "catastro_zones.json"
SCHEMA = "onward_generator.catastro_zones"


@dataclass(frozen=True, slots=True)
class Territory:
    name: str
    polygon: Polygon
    crs_by_utm_zone: Mapping[int, str]


@dataclass(frozen=True, slots=True)
class ZoneTable:
    service_crs: tuple[str, ...]
    territories: tuple[Territory, ...]
    excluded: tuple[tuple[str, Polygon], ...]


@dataclass(frozen=True, slots=True)
class CatastroZone:
    """The territory a point is in and the CRS a Catastro request uses there."""

    territory: str
    crs: str


def utm_zone(longitude: float) -> int:
    """UTM zone number of a longitude, as ``frame_for_origin`` computes it."""
    return int((longitude + 180.0) / 6.0) + 1


def _polygon(name: str, rings: Any) -> Polygon:
    try:
        polygon = Polygon(rings[0], rings[1:])
    except (TypeError, ValueError, IndexError) as exc:
        raise CadastreError(f"catastro zones: {name}: polygon is not a list of rings") from exc
    minx, miny, maxx, maxy = polygon.bounds
    if not (-180 <= minx and maxx <= 180 and -90 <= miny and maxy <= 90):
        raise CadastreError(f"catastro zones: {name}: polygon is not in longitude/latitude degrees")
    if not polygon.is_valid:
        raise CadastreError(f"catastro zones: {name}: polygon is not valid")
    return polygon


def _request_crs(name: str, zone: int, code: str, service_crs: tuple[str, ...]) -> str:
    if code not in service_crs:
        raise CadastreError(f"catastro zones: {name}: {code} is not a CRS the service offers")
    if ProjCRS.from_user_input(code).utm_zone != f"{zone}N":
        raise CadastreError(f"catastro zones: {name}: {code} is not UTM zone {zone} north")
    return code


def zone_table(document: Mapping[str, Any]) -> ZoneTable:
    """Validate a zone document."""
    if document.get("schema") != SCHEMA:
        raise CadastreError(f"catastro zones: schema must be {SCHEMA}")
    service_crs = tuple(document["service"]["crs"])
    territories: list[Territory] = []
    for entry in document["territories"]:
        name = entry["name"]
        crs_by_zone = {int(zone): _request_crs(name, int(zone), code, service_crs) for zone, code in entry["crs_by_utm_zone"].items()}
        if not crs_by_zone:
            raise CadastreError(f"catastro zones: {name}: no request CRS")
        territories.append(Territory(name, _polygon(name, entry["polygon"]), crs_by_zone))
    excluded = tuple((entry["name"], _polygon(entry["name"], entry["polygon"])) for entry in document["excluded"])
    return ZoneTable(service_crs, tuple(territories), excluded)


@cache
def load_zones(path: Path = ZONES_PATH) -> ZoneTable:
    return zone_table(json.loads(path.read_text(encoding="utf-8")))


def catastro_zone(longitude: float, latitude: float, table: ZoneTable | None = None) -> CatastroZone | None:
    """The Catastro zone of a WGS84 point, or None where the DG Catastro WFS has no data.

    Navarre and the Basque Country keep their own cadastres and are excluded
    even inside a territory. Degrees are only compared with the degree
    polygons of the table.
    """
    table = table or load_zones()
    point = Point(longitude, latitude)
    if any(polygon.covers(point) for _, polygon in table.excluded):
        return None
    for territory in table.territories:
        if territory.polygon.covers(point):
            crs = territory.crs_by_utm_zone.get(utm_zone(longitude))
            return None if crs is None else CatastroZone(territory.name, crs)
    return None


def reproject_bbox(bbox: BBox, source_crs: str, target_crs: str) -> BBox:
    """The box in ``target_crs`` that holds ``bbox``; unchanged when both CRS are the same."""
    if source_crs == target_crs:
        return bbox
    transformer = Transformer.from_crs(source_crs, target_crs, always_xy=True)
    xmin, ymin, xmax, ymax = transformer.transform_bounds(*bbox, densify_pts=21)
    return (xmin, ymin, xmax, ymax)


def scene_request(scene: Mapping[str, Any], margin_m: float) -> tuple[BBox, CatastroZone]:
    """Box around the scene buildings plus ``margin_m``, in the CRS of the scene's Catastro zone."""
    frame = scene.get("frame") or {}
    longitude, latitude = frame["origin_wgs84"]
    zone = catastro_zone(float(longitude), float(latitude))
    if zone is None:
        raise CadastreError(f"({latitude}, {longitude}) is outside the territories the DG Catastro WFS covers")
    return reproject_bbox(bbox_from_scene(scene, margin_m), frame["crs"], zone.crs), zone
