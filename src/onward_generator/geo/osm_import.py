"""Read OSM XML into raw elements and assemble WGS84 source features.

Raw elements keep the original OSM identifiers, versions and tags untouched.
Geometries built here are still in WGS84 degrees; projection happens later.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType

import numpy as np

from shapely import LineString, MultiPolygon, Point, Polygon, polygonize_full, unary_union
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient

from onward_generator.domain.models import OsmRef, validate_lon_lat
from onward_generator.geo.issues import GeoIssue

_PROLOG_SCAN_LIMIT = 1024 * 1024
_AREA_KEYS: frozenset[str] = frozenset(
    {"building", "landuse", "leisure", "amenity", "natural", "place", "shop", "tourism", "historic"}
)
_LINEAR_NATURAL: frozenset[str] = frozenset({"coastline", "tree_row", "cliff", "ridge"})


class OsmImportError(ValueError):
    """Raised when a file cannot be read as OSM XML."""


class FeatureKind(StrEnum):
    BUILDING = "building"
    HIGHWAY = "highway"
    AREA = "area"
    LINE = "line"
    POI = "poi"


@dataclass(frozen=True, slots=True)
class OsmNode:
    id: int
    lon: float
    lat: float
    tags: Mapping[str, str]
    version: int | None


@dataclass(frozen=True, slots=True)
class OsmWay:
    id: int
    node_refs: tuple[int, ...]
    tags: Mapping[str, str]
    version: int | None


@dataclass(frozen=True, slots=True)
class OsmMember:
    type: str
    ref: int
    role: str


@dataclass(frozen=True, slots=True)
class OsmRelation:
    id: int
    members: tuple[OsmMember, ...]
    tags: Mapping[str, str]
    version: int | None


@dataclass(frozen=True, slots=True)
class OsmDocument:
    nodes: Mapping[int, OsmNode]
    ways: Mapping[int, OsmWay]
    relations: Mapping[int, OsmRelation]
    generator: str | None
    data_timestamp: str | None
    issues: tuple[GeoIssue, ...]


@dataclass(frozen=True, slots=True)
class SourceFeature:
    """An OSM element turned into a WGS84 geometry, tags unchanged."""

    ref: OsmRef
    kind: FeatureKind
    tags: Mapping[str, str]
    geometry_wgs84: BaseGeometry


def _check_prolog(path: Path) -> None:
    """Reject DTDs and entity declarations before parsing (XML bomb guard)."""
    with path.open("rb") as handle:
        head = handle.read(_PROLOG_SCAN_LIMIT)
    root_at = head.find(b"<osm")
    prolog = head if root_at < 0 else head[:root_at]
    if b"<!DOCTYPE" in prolog or b"<!ENTITY" in prolog:
        raise OsmImportError(f"{path}: DTD or entity declarations are not allowed")


def _int_attr(elem: ET.Element, name: str) -> int:
    raw = elem.get(name)
    if raw is None:
        raise ValueError(f"missing attribute {name!r}")
    return int(raw)


def _optional_int(elem: ET.Element, name: str) -> int | None:
    raw = elem.get(name)
    return int(raw) if raw is not None else None


def _tags(elem: ET.Element) -> Mapping[str, str]:
    tags: dict[str, str] = {}
    for tag in elem.iterfind("tag"):
        key, value = tag.get("k"), tag.get("v")
        if key is not None and value is not None:
            tags[key] = value
    return MappingProxyType(tags)


def parse_osm_xml(path: str | Path) -> OsmDocument:
    """Parse an OSM XML file without modifying it."""
    source = Path(path)
    _check_prolog(source)

    nodes: dict[int, OsmNode] = {}
    ways: dict[int, OsmWay] = {}
    relations: dict[int, OsmRelation] = {}
    issues: list[GeoIssue] = []
    generator: str | None = None
    data_timestamp: str | None = None
    root_seen = False

    def store(table: dict, ref: OsmRef, element: object) -> None:
        if ref.osm_id in table:
            issues.append(
                GeoIssue(
                    "duplicate_element_id",
                    "warning",
                    f"{ref.key} appears more than once; the first occurrence is kept",
                    ref,
                )
            )
            return
        table[ref.osm_id] = element

    try:
        for event, elem in ET.iterparse(source, events=("start", "end")):
            if event == "start":
                if not root_seen:
                    if elem.tag != "osm":
                        raise OsmImportError(f"{source}: root element is <{elem.tag}>, expected <osm>")
                    root_seen = True
                    generator = elem.get("generator")
                    data_timestamp = elem.get("timestamp")
                continue

            if elem.tag == "meta" and elem.get("osm_base"):
                data_timestamp = elem.get("osm_base")
            if elem.tag not in ("node", "way", "relation"):
                continue

            try:
                osm_id = _int_attr(elem, "id")
                ref = OsmRef(elem.tag, osm_id)  # type: ignore[arg-type]
            except ValueError as exc:
                issues.append(GeoIssue("malformed_element", "error", f"<{elem.tag}>: {exc}"))
                elem.clear()
                continue

            if elem.get("action") == "delete" or elem.get("visible") == "false":
                issues.append(GeoIssue("deleted_element", "info", f"{ref.key} is marked deleted; skipped", ref))
                elem.clear()
                continue

            try:
                version = _optional_int(elem, "version")
                if elem.tag == "node":
                    lon, lat = float(elem.get("lon", "nan")), float(elem.get("lat", "nan"))
                    validate_lon_lat(lon, lat)
                    store(nodes, ref, OsmNode(osm_id, lon, lat, _tags(elem), version))
                elif elem.tag == "way":
                    refs = tuple(_int_attr(nd, "ref") for nd in elem.iterfind("nd"))
                    store(ways, ref, OsmWay(osm_id, refs, _tags(elem), version))
                else:
                    members = tuple(
                        OsmMember(m.get("type", ""), _int_attr(m, "ref"), m.get("role", ""))
                        for m in elem.iterfind("member")
                    )
                    store(relations, ref, OsmRelation(osm_id, members, _tags(elem), version))
            except ValueError as exc:
                issues.append(GeoIssue("malformed_element", "error", f"{ref.key}: {exc}", ref))
            elem.clear()
    except ET.ParseError as exc:
        raise OsmImportError(f"{source}: invalid XML: {exc}") from exc

    if not root_seen:
        raise OsmImportError(f"{source}: empty document")

    return OsmDocument(
        nodes=MappingProxyType(nodes),
        ways=MappingProxyType(ways),
        relations=MappingProxyType(relations),
        generator=generator,
        data_timestamp=data_timestamp,
        issues=tuple(issues),
    )


def is_area(tags: Mapping[str, str]) -> bool:
    """Decide whether a closed way represents an area (OSM conventions)."""
    area = tags.get("area")
    if area == "yes":
        return True
    if area == "no":
        return False
    if tags.get("natural") in _LINEAR_NATURAL:
        return False
    return any(key in tags for key in _AREA_KEYS)


def classify(tags: Mapping[str, str], geometry: BaseGeometry) -> FeatureKind:
    if tags.get("building", "no") != "no":
        return FeatureKind.BUILDING
    if "highway" in tags:
        return FeatureKind.HIGHWAY
    if isinstance(geometry, Point):
        return FeatureKind.POI
    if isinstance(geometry, (Polygon, MultiPolygon)):
        return FeatureKind.AREA
    return FeatureKind.LINE


def _way_coords(way: OsmWay, doc: OsmDocument) -> list[tuple[float, float]] | None:
    coords = []
    for node_id in way.node_refs:
        node = doc.nodes.get(node_id)
        if node is None:
            return None
        coords.append((node.lon, node.lat))
    return coords


def _build_way(way: OsmWay, doc: OsmDocument, issues: list[GeoIssue]) -> BaseGeometry | None:
    ref = OsmRef("way", way.id)
    coords = _way_coords(way, doc)
    if coords is None:
        missing = [n for n in way.node_refs if n not in doc.nodes]
        issues.append(GeoIssue("missing_node_reference", "error", f"{ref.key} references missing nodes {missing}", ref))
        return None
    if len(coords) < 2:
        issues.append(GeoIssue("degenerate_way", "error", f"{ref.key} has fewer than 2 nodes", ref))
        return None
    closed = way.node_refs[0] == way.node_refs[-1]
    if closed and is_area(way.tags):
        if len(coords) < 4:
            issues.append(GeoIssue("degenerate_ring", "error", f"{ref.key} is a closed area with fewer than 3 distinct nodes", ref))
            return None
        return Polygon(coords)
    return LineString(coords)


def _faces(lines: list[LineString]) -> tuple[list[Polygon], int]:
    """Faces of the noded member linework, and how many pieces close no face.

    Ways are joined wherever they meet, including nodes shared by three or
    more ways and rings that touch. Dangles, cuts and invalid rings count as
    open pieces.
    """
    polygons, cuts, dangles, invalid = polygonize_full([unary_union(lines)])
    faces = [face for face in polygons.geoms if isinstance(face, Polygon) and not face.is_empty]
    return faces, len(cuts.geoms) + len(dangles.geoms) + len(invalid.geoms)


def _member_segments(lines: list[LineString]) -> np.ndarray:
    """Every segment of every member way as ``(x1, y1, x2, y2)`` rows.

    A segment that two member ways both run along appears twice, once per way.
    """
    rows = [np.hstack([coords[:-1], coords[1:]]) for coords in (np.asarray(line.coords) for line in lines)]
    return np.vstack(rows)


def _ring_crossings(face: Polygon, segments: np.ndarray) -> int:
    """How many member way segments a ray from inside ``face`` towards +x crosses.

    The count is taken over the member ways, not the faces, so a stretch where
    an inner ring runs along its outer counts twice. Each segment is half-open
    in y, so a ray through a vertex counts the two segments meeting there once.
    """
    probe = face.representative_point()
    x, y = probe.x, probe.y
    straddling = segments[(segments[:, 1] > y) != (segments[:, 3] > y)]
    x1, y1, x2, y2 = straddling.T
    x_at = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
    return int(np.count_nonzero(x_at > x))


def _build_multipolygon(rel: OsmRelation, doc: OsmDocument, issues: list[GeoIssue]) -> BaseGeometry | None:
    """Assemble a multipolygon by the even-odd rule over the member ways.

    Member roles are advisory. A face is filled when a ray from it crosses the
    member ways an odd number of times, and is a hole when it crosses them an
    even number of times. A courtyard that shares an edge or a vertex with its
    outer ring is therefore a hole, although polygonize returns it as a face of
    its own. A role that disagrees with the
    nesting is reported, and the nesting wins. Exteriors run counter-clockwise
    and holes clockwise, as in RFC 7946.
    """
    ref = OsmRef("relation", rel.id)
    outer: list[LineString] = []
    inner: list[LineString] = []
    for member in rel.members:
        if member.type != "way":
            continue
        way = doc.ways.get(member.ref)
        coords = _way_coords(way, doc) if way else None
        if coords is None or len(coords) < 2:
            issues.append(GeoIssue("missing_member", "error", f"{ref.key} member way/{member.ref} is missing or incomplete", ref))
            return None
        (inner if member.role == "inner" else outer).append(LineString(coords))

    if not outer:
        issues.append(GeoIssue("missing_outer_ring", "error", f"{ref.key} has no outer member", ref))
        return None
    faces, open_pieces = _faces(outer + inner)
    if not faces or open_pieces:
        issues.append(
            GeoIssue(
                "unclosed_multipolygon_ring",
                "error",
                f"{ref.key} has rings that do not close ({open_pieces} open piece(s) in the member ways)",
                ref,
            )
        )
        return None
    segments = _member_segments(outer + inner)
    outer_lines = unary_union(outer)
    inner_lines = unary_union(inner) if inner else None
    filled: list[Polygon] = []
    for face in faces:
        from_inner = inner_lines is not None and inner_lines.covers(face.exterior)
        if _ring_crossings(face, segments) % 2 == 1:
            filled.append(face)
            if from_inner:
                issues.append(
                    GeoIssue(
                        "inner_ring_outside_outer",
                        "warning",
                        f"{ref.key} has an inner ring that no outer ring encloses; it is kept as an area",
                        ref,
                    )
                )
        elif not from_inner and outer_lines.covers(face.exterior):
            issues.append(
                GeoIssue(
                    "outer_ring_inside_outer",
                    "warning",
                    f"{ref.key} has an outer ring inside another outer ring; it is kept as a hole",
                    ref,
                )
            )
    merged = unary_union(filled)
    return MultiPolygon([orient(part, sign=1.0) for part in getattr(merged, "geoms", [merged])])


def build_source_features(doc: OsmDocument) -> tuple[list[SourceFeature], list[GeoIssue], dict[str, int]]:
    """Turn tagged OSM elements into WGS84 features.

    Untagged nodes and ways are geometry parts, not features. Only
    ``type=multipolygon`` relations are assembled.
    """
    features: list[SourceFeature] = []
    issues: list[GeoIssue] = []
    skipped = {"untagged_ways": 0, "unsupported_relations": 0}

    for node in doc.nodes.values():
        if node.tags:
            geom = Point(node.lon, node.lat)
            features.append(SourceFeature(OsmRef("node", node.id), FeatureKind.POI, node.tags, geom))

    for way in doc.ways.values():
        if not way.tags:
            skipped["untagged_ways"] += 1
            continue
        geom = _build_way(way, doc, issues)
        if geom is not None:
            features.append(SourceFeature(OsmRef("way", way.id), classify(way.tags, geom), way.tags, geom))

    for rel in doc.relations.values():
        if rel.tags.get("type") != "multipolygon":
            skipped["unsupported_relations"] += 1
            issues.append(
                GeoIssue("unsupported_relation", "info", f"relation type {rel.tags.get('type')!r} is not assembled", OsmRef("relation", rel.id))
            )
            continue
        geom = _build_multipolygon(rel, doc, issues)
        if geom is not None:
            features.append(SourceFeature(OsmRef("relation", rel.id), classify(rel.tags, geom), rel.tags, geom))

    return features, issues, skipped
