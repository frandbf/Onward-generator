"""Parsing of Catastro INSPIRE BU ``BuildingPart`` GML (read only)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from shapely import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

from onward_generator.cadastre.acquire import CadastreError

NS = {
    "gml": "http://www.opengis.net/gml/3.2",
    "base": "urn:x-inspire:specification:gmlas:BaseTypes:3.2",
    "bu-ext2d": "http://inspire.jrc.ec.europa.eu/schemas/bu-ext2d/2.0",
}
_GML = "{http://www.opengis.net/gml/3.2}"
_EXT = "{http://inspire.jrc.ec.europa.eu/schemas/bu-ext2d/2.0}"
_BASE = "{urn:x-inspire:specification:gmlas:BaseTypes:3.2}"
_ACCEPTED_SRS = ("EPSG::25830", "EPSG:25830")
_PROLOG_SCAN = 64 * 1024


@dataclass(frozen=True, slots=True)
class BuildingPart:
    local_id: str
    refcat: str
    floors_above: int
    floors_below: int
    geometry: BaseGeometry  # EPSG:25830 metres


@dataclass(frozen=True, slots=True)
class PartIssue:
    code: str
    local_id: str | None
    message: str

    def to_dict(self) -> dict[str, str | None]:
        return {"code": self.code, "local_id": self.local_id, "message": self.message}


def _ring(pos_list: ET.Element) -> list[tuple[float, float]]:
    if pos_list.get("srsDimension", "2") != "2":
        raise ValueError("only 2D posList is supported")
    values = [float(v) for v in (pos_list.text or "").split()]
    if len(values) % 2 or len(values) < 8:
        raise ValueError("posList must hold at least 4 coordinate pairs")
    return list(zip(values[0::2], values[1::2], strict=True))


def _geometry(part: ET.Element) -> BaseGeometry:
    polygons = []
    for surface in part.iter(f"{_GML}Surface"):
        srs = surface.get("srsName", "")
        if not srs.endswith(_ACCEPTED_SRS):
            raise ValueError(f"unsupported srsName {srs!r}")
        for patch in surface.iter(f"{_GML}PolygonPatch"):
            exterior = patch.find(f"{_GML}exterior/{_GML}LinearRing/{_GML}posList")
            if exterior is None:
                raise ValueError("patch without exterior ring")
            holes = [_ring(p) for p in patch.findall(f"{_GML}interior/{_GML}LinearRing/{_GML}posList")]
            polygons.append(Polygon(_ring(exterior), holes))
    if not polygons:
        raise ValueError("no surface geometry")
    return polygons[0] if len(polygons) == 1 else MultiPolygon(polygons)


def _int(part: ET.Element, tag: str) -> int:
    elem = part.find(f"{_EXT}{tag}")
    if elem is None or elem.text is None:
        raise ValueError(f"missing {tag}")
    value = int(elem.text.strip())
    if value < 0:
        raise ValueError(f"negative {tag}")
    return value


def parse_building_parts(path: str | Path) -> tuple[list[BuildingPart], list[PartIssue]]:
    """Read every BuildingPart. Malformed parts are reported and skipped."""
    source = Path(path)
    with source.open("rb") as handle:
        head = handle.read(_PROLOG_SCAN)
    root_at = head.find(b"FeatureCollection")
    prolog = head if root_at < 0 else head[:root_at]
    if b"<!DOCTYPE" in prolog or b"<!ENTITY" in prolog:
        raise CadastreError(f"{source}: DTD or entity declarations are not allowed")
    if root_at < 0:
        raise CadastreError(f"{source}: not a WFS FeatureCollection")

    parts: list[BuildingPart] = []
    issues: list[PartIssue] = []
    seen: set[str] = set()
    try:
        for _, elem in ET.iterparse(source, events=("end",)):
            if elem.tag != f"{_EXT}BuildingPart":
                continue
            id_elem = elem.find(f".//{_BASE}localId")
            local_id = id_elem.text.strip() if id_elem is not None and id_elem.text else None
            try:
                if not local_id:
                    raise ValueError("missing localId")
                if local_id in seen:
                    issues.append(PartIssue("duplicate_part", local_id, "repeated localId; later copy ignored"))
                    continue
                geometry = _geometry(elem)
                if not geometry.is_valid or geometry.is_empty:
                    issues.append(PartIssue("invalid_part_geometry", local_id, "invalid polygon; part skipped"))
                    continue
                parts.append(
                    BuildingPart(
                        local_id=local_id,
                        refcat=local_id.split("_part")[0],
                        floors_above=_int(elem, "numberOfFloorsAboveGround"),
                        floors_below=_int(elem, "numberOfFloorsBelowGround"),
                        geometry=geometry,
                    )
                )
                seen.add(local_id)
            except ValueError as exc:
                issues.append(PartIssue("malformed_part", local_id, str(exc)))
            finally:
                elem.clear()
    except ET.ParseError as exc:
        raise CadastreError(f"{source}: XML parse error: {exc}") from exc
    parts.sort(key=lambda p: p.local_id)
    return parts, issues
