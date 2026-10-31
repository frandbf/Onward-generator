"""Parsing of Catastro INSPIRE BU ``BuildingPart`` and ``Building`` GML (read only)."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import shapely
from pyproj import Transformer
from shapely import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

from onward_generator.cadastre.acquire import CRS, CadastreError

NS = {
    "gml": "http://www.opengis.net/gml/3.2",
    "base": "urn:x-inspire:specification:gmlas:BaseTypes:3.2",
    "bu-ext2d": "http://inspire.jrc.ec.europa.eu/schemas/bu-ext2d/2.0",
}
_GML = "{http://www.opengis.net/gml/3.2}"
_EXT = "{http://inspire.jrc.ec.europa.eu/schemas/bu-ext2d/2.0}"
_CORE = "{http://inspire.jrc.ec.europa.eu/schemas/bu-core2d/2.0}"
# INSPIRE DateOfEvent: Catastro fills ``beginning`` (and ``end``, the same day); ``anyPoint`` is the generic form.
_DATE_EVENTS = ("beginning", "anyPoint", "end")
_YEAR = re.compile(r"(\d{4})-\d{2}-\d{2}")
_BASE = "{urn:x-inspire:specification:gmlas:BaseTypes:3.2}"
_PROLOG_SCAN = 64 * 1024


@dataclass(frozen=True, slots=True)
class BuildingPart:
    local_id: str
    refcat: str
    floors_above: int
    floors_below: int
    geometry: BaseGeometry  # metres in the download CRS, or the scene CRS after reproject_parts


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


def _accepted_srs(crs: str) -> tuple[str, str]:
    """``srsName`` endings for ``EPSG:nnnn``: the URN form ``EPSG::nnnn`` and the short form."""
    code = crs.removeprefix("EPSG:")
    return (f"EPSG::{code}", f"EPSG:{code}")


def _geometry(part: ET.Element, accepted: tuple[str, str]) -> BaseGeometry:
    polygons = []
    for surface in part.iter(f"{_GML}Surface"):
        srs = surface.get("srsName", "")
        if not srs.endswith(accepted):
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


def _check_prolog(source: Path) -> None:
    """Refuse a file without a FeatureCollection, or with a DTD or entities before it."""
    with source.open("rb") as handle:
        head = handle.read(_PROLOG_SCAN)
    root_at = head.find(b"FeatureCollection")
    prolog = head if root_at < 0 else head[:root_at]
    if b"<!DOCTYPE" in prolog or b"<!ENTITY" in prolog:
        raise CadastreError(f"{source}: DTD or entity declarations are not allowed")
    if root_at < 0:
        raise CadastreError(f"{source}: not a WFS FeatureCollection")


def parse_building_parts(path: str | Path, crs: str = CRS) -> tuple[list[BuildingPart], list[PartIssue]]:
    """Read every BuildingPart in ``crs``, the download CRS. Malformed parts (another CRS among them) are reported and skipped."""
    source = Path(path)
    accepted = _accepted_srs(crs)
    _check_prolog(source)

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
                geometry = _geometry(elem, accepted)
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


@dataclass(frozen=True, slots=True)
class BuildingUse:
    """The predominant use of one Catastro building (a parcel, ``localId`` = ``refcat``) and its unit counts."""

    refcat: str
    current_use: str
    dwellings: int | None
    building_units: int | None
    construction_year: int | None = None


def _optional_int(building: ET.Element, tag: str) -> int | None:
    """An integer that Catastro may leave ``xsi:nil``: ``None`` when it is empty."""
    elem = building.find(f"{_EXT}{tag}")
    text = None if elem is None else (elem.text or "").strip()
    if not text:
        return None
    value = int(text)
    if value < 0:
        raise ValueError(f"negative {tag}")
    return value


def construction_year(building: ET.Element) -> int | None:
    """The year of ``bu-core2d:dateOfConstruction``: ``None`` when it is absent or ``xsi:nil``.

    The first filled ``beginning``, ``anyPoint`` or ``end`` of its ``DateOfEvent`` is read. A date that is
    not ``YYYY-MM-DD...`` raises ``ValueError``.
    """
    event = building.find(f"{_CORE}dateOfConstruction/{_CORE}DateOfEvent")
    if event is None:
        return None
    for name in _DATE_EVENTS:
        elem = event.find(f"{_CORE}{name}")
        text = "" if elem is None else (elem.text or "").strip()
        if not text:
            continue
        match = _YEAR.match(text)
        if match is None:
            raise ValueError(f"dateOfConstruction {name} {text!r} is not a date")
        return int(match.group(1))
    return None


def parse_building_uses(path: str | Path) -> tuple[dict[str, BuildingUse], list[PartIssue]]:
    """Every ``bu-ext2d:Building`` of a downloaded GML by ``refcat``: its ``currentUse``, unit counts and
    construction year.

    A building without a ``localId`` or a ``currentUse``, or with a bad count, is ``malformed_building``;
    a repeated ``localId`` is ``duplicate_building``. A ``dateOfConstruction`` that is not a date is
    ``malformed_construction_date``: the building keeps its use, with no year. The geometry is not read:
    a building is the parcel its parts already name.
    """
    source = Path(path)
    _check_prolog(source)
    uses: dict[str, BuildingUse] = {}
    issues: list[PartIssue] = []
    try:
        for _, elem in ET.iterparse(source):
            if elem.tag != f"{_EXT}Building":
                continue
            id_elem = elem.find(f".//{_BASE}localId")
            refcat = id_elem.text.strip() if id_elem is not None and id_elem.text else None
            try:
                if not refcat:
                    raise ValueError("missing localId")
                if refcat in uses:
                    issues.append(PartIssue("duplicate_building", refcat, "repeated localId; later copy ignored"))
                    continue
                use_elem = elem.find(f"{_EXT}currentUse")
                current = (use_elem.text or "").strip() if use_elem is not None else ""
                if not current:
                    raise ValueError("missing currentUse")
                dwellings, units = _optional_int(elem, "numberOfDwellings"), _optional_int(elem, "numberOfBuildingUnits")
                try:
                    year = construction_year(elem)
                except ValueError as exc:
                    issues.append(PartIssue("malformed_construction_date", refcat, str(exc)))
                    year = None
                uses[refcat] = BuildingUse(refcat, current, dwellings, units, year)
            except ValueError as exc:
                issues.append(PartIssue("malformed_building", refcat, str(exc)))
            finally:
                elem.clear()
    except ET.ParseError as exc:
        raise CadastreError(f"{source}: XML parse error: {exc}") from exc
    return dict(sorted(uses.items())), issues


def reproject_parts(parts: Sequence[BuildingPart], source_crs: str, target_crs: str) -> list[BuildingPart]:
    """Parts with their geometry moved from the download CRS to the scene CRS (pyproj, x east, y north).

    Same CRS: unchanged. Catastro answers in ETRS89 (or WGS 84 UTM for the
    Canaries); a map outside EPSG:25830 is on a WGS 84 UTM zone, and pyproj
    relates the two datums without a shift, as the OSM data is drawn.
    """
    if source_crs == target_crs:
        return list(parts)
    transformer = Transformer.from_crs(source_crs, target_crs, always_xy=True)  # pragma: no mutate: download and scene CRS are UTM, whose axes are already east, north

    def move(xy: np.ndarray) -> np.ndarray:
        x, y = transformer.transform(xy[:, 0], xy[:, 1])
        return np.column_stack([x, y])

    return [replace(part, geometry=shapely.transform(part.geometry, move)) for part in parts]
