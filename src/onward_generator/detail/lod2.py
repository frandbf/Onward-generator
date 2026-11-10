"""Observed LoD2 volumes on landmark footprints (plan D6, amendment of ADR-008).

``detail --lod2-landmarks lod2_index.json`` reads the block ZIPs a ``landmark_mesh`` source plugin
downloaded (``es.madrid_lod2``): one FME OBJ per block, triangles grouped by ``usemtl`` (one group per
building or part), in ETRS89 / UTM 30N metres with absolute orthometric heights. For a landmark of the
core (``heights.json`` ``landmark``, ADR-008) the groups whose plan hull lies mostly on its OSM footprint
are its volume, if together they cover enough of the footprint. Their triangles are moved to the local
grid (the scene frame's ``origin_projected`` is subtracted, never degrees), set on the ground by their
lowest vertex, clipped to the footprint, simplified by vertex clustering down to a triangle cap and
split into the landmark wall and roof materials. Each matched group gets a closed prism collider,
separate from the visual mesh. The footprint never moves and nothing is drawn outside it; a landmark
with no match keeps the placeholder shell and its tower.

Only the standard library ``zipfile`` plus numpy and shapely are used: no new dependency.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import numpy as np
import shapely
from shapely import LineString, MultiPoint, Polygon, union_all
from shapely.geometry.base import BaseGeometry

from onward_generator.contracts import is_schema
from onward_generator.generation.gltf import Primitive
from onward_generator.generation.mesh import Mesh, cap, closed_manifold_defects, merge, polygons_of, prism, signed_volume
from onward_generator.generation.scene import Issue

INDEX_SCHEMA = "onward_generator.lod2_landmarks"
CRS = "EPSG:25830"
LICENCE = "CC-BY-4.0"
_EPSG = re.compile(r'AUTHORITY\["EPSG","(\d+)"\]')
_IGNORED = frozenset({"vn", "vt", "mtllib", "g", "o", "s"})
_EDGE_M = 1e-6
"""How far outside the footprint a clamped vertex may sit from floating-point rounding."""
_WALL_NZ = 1e-3
"""A triangle whose unit normal z is under this is a wall, cut in its own vertical plane rather than in plan."""
_WELD_M = 1e-3
"""Corners closer than this are one when ``close_cut`` looks for the edges a cut opened."""
_CUT_M = 1e-4
"""How near the footprint edge both ends of an opened edge must be for ``close_cut`` to wall it down."""


MAX_VERTICES = 400_000
MAX_FACES = 800_000
"""The default caps of one OBJ, shared by ``Lod2Settings`` and ``parse_obj``."""


class Lod2Error(ValueError):
    """An LoD2 index, ZIP or OBJ is invalid, or does not fit the scene."""


@dataclass(frozen=True, slots=True)
class Lod2Settings:
    """How an LoD2 volume becomes a landmark. Modelling assumptions, recorded in each landmark's ``lod2`` block."""

    max_triangles: int = 2000
    simplify_cell_m: float = 0.25
    max_simplify_passes: int = 4
    min_group_overlap: float = 0.5
    min_footprint_cover: float = 0.6
    clip_tolerance_m: float = 1.0
    min_footprint_area_m2: float = 20.0
    roof_min_normal_z: float = 0.35
    max_unzipped_bytes: int = 32 * 1024 * 1024
    max_vertices: int = MAX_VERTICES
    max_faces: int = MAX_FACES

    def to_record(self) -> dict[str, Any]:
        return {
            "max_triangles": self.max_triangles,
            "simplify_cell_m": self.simplify_cell_m,
            "min_group_overlap": self.min_group_overlap,
            "min_footprint_cover": self.min_footprint_cover,
            "clip_tolerance_m": self.clip_tolerance_m,
            "roof_min_normal_z": self.roof_min_normal_z,
        }


DEFAULT_SETTINGS = Lod2Settings()


@dataclass(frozen=True, slots=True)
class ObjGroup:
    name: str
    triangles: np.ndarray  # (m, 3) int64 indices into the OBJ vertices


@dataclass(frozen=True, slots=True)
class ObjMesh:
    vertices: np.ndarray  # (n, 3) float64, as in the file
    groups: tuple[ObjGroup, ...]
    crs: str | None  # from the ``# COORDINATE_SYSTEM`` header, the last EPSG authority (the projected one)


@dataclass(frozen=True, slots=True)
class Lod2Block:
    cod_manzan: str
    path: str
    sha256: str
    url: str
    obj_date: str
    mesh: ObjMesh


@dataclass(frozen=True, slots=True)
class Lod2Index:
    path: str
    sha256: str
    source: str
    provider: str
    license: str
    attribution: str
    data_date: str
    crs: str
    blocks: tuple[Lod2Block, ...]

    def attribution_line(self) -> dict[str, Any]:
        return {"text": self.attribution, "license": self.license, "license_url": "https://creativecommons.org/licenses/by/4.0/"}


# ---------------------------------------------------------------------------------------------------- reading


def _index_of(token: str, count: int, line: int) -> int:
    head = token.split("/", 1)[0]  # pragma: no mutate: only the head before the first slash is read, however many splits follow
    try:
        value = int(head)
    except ValueError as exc:
        raise Lod2Error(f"OBJ line {line}: face index {token!r} is not an integer") from exc
    if value == 0:
        raise Lod2Error(f"OBJ line {line}: face index 0 (OBJ counts from 1)")
    index = value - 1 if value > 0 else count + value  # pragma: no mutate: 0 is refused just above, so > and >= agree
    if not 0 <= index < count:
        raise Lod2Error(f"OBJ line {line}: face index {value} is outside the {count} vertices read so far")
    return index


def parse_obj(text: str, *, max_vertices: int = MAX_VERTICES, max_faces: int = MAX_FACES) -> ObjMesh:
    """Vertices and ``usemtl`` groups of an OBJ. Polygons are fan-triangulated; a malformed line raises with its number."""
    vertices: list[tuple[float, float, float]] = []
    groups: dict[str, list[tuple[int, int, int]]] = {}
    current = "default"
    crs: str | None = None
    faces = 0
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            if "COORDINATE_SYSTEM" in line:
                found = _EPSG.findall(line)
                crs = f"EPSG:{found[-1]}" if found else None
            continue
        tokens = line.split()
        directive = tokens[0]
        if directive == "v":
            if len(tokens) != 4:
                raise Lod2Error(f"OBJ line {number}: a vertex needs x y z")
            try:
                x, y, z = (float(t) for t in tokens[1:])
            except ValueError as exc:
                raise Lod2Error(f"OBJ line {number}: vertex {tokens[1:]} is not numeric") from exc
            if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
                raise Lod2Error(f"OBJ line {number}: vertex is not finite")
            if len(vertices) >= max_vertices:
                raise Lod2Error(f"OBJ has more than {max_vertices} vertices")
            vertices.append((x, y, z))
        elif directive == "f":
            if len(tokens) < 4:
                raise Lod2Error(f"OBJ line {number}: a face needs at least 3 vertices")
            if not vertices:
                raise Lod2Error(f"OBJ line {number}: a face before any vertex")
            ring = [_index_of(t, len(vertices), number) for t in tokens[1:]]
            fan = [(ring[0], ring[k], ring[k + 1]) for k in range(1, len(ring) - 1)]
            faces += len(fan)
            if faces > max_faces:
                raise Lod2Error(f"OBJ has more than {max_faces} triangles")
            groups.setdefault(current, []).extend(fan)
        elif directive == "usemtl":
            if len(tokens) != 2:
                raise Lod2Error(f"OBJ line {number}: usemtl needs one name")
            current = tokens[1]
        elif directive not in _IGNORED:
            raise Lod2Error(f"OBJ line {number}: unknown directive {directive!r}")
    if not vertices:
        raise Lod2Error("OBJ has no vertices")
    if not faces:
        raise Lod2Error("OBJ has no faces")
    return ObjMesh(
        np.asarray(vertices, dtype=np.float64),  # pragma: no mutate: numpy reads a list of Python floats as float64 already
        tuple(ObjGroup(name, np.asarray(tris, dtype=np.int64)) for name, tris in groups.items()),  # pragma: no mutate: numpy reads Python ints as int64 on the 64-bit image already
        crs,
    )


def read_obj_zip(data: bytes, settings: Lod2Settings = DEFAULT_SETTINGS, *, crs: str = CRS) -> tuple[ObjMesh, str]:
    """The one OBJ of a block ZIP and the date of its member. The ``.mtl`` is ignored; the OBJ header must name ``crs``."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise Lod2Error("not a valid ZIP file") from exc
    with archive:
        infos = archive.infolist()
        for info in infos:
            name = PurePosixPath(info.filename.replace("\\", "/"))
            if name.is_absolute() or ".." in name.parts:
                raise Lod2Error(f"ZIP member {info.filename!r} leaves the archive")
        objs = [info for info in infos if info.filename.lower().endswith(".obj")]
        if len(objs) != 1:
            raise Lod2Error(f"the ZIP holds {len(objs)} .obj files; one is needed")
        info = objs[0]
        if info.file_size > settings.max_unzipped_bytes:
            raise Lod2Error(f"{info.filename} is {info.file_size} bytes unzipped, over {settings.max_unzipped_bytes}")
        try:
            with archive.open(info) as member:
                body = member.read(settings.max_unzipped_bytes + 1)
        except (zipfile.BadZipFile, OSError, EOFError) as exc:
            raise Lod2Error(f"{info.filename} cannot be read: {exc}") from exc
        if len(body) > settings.max_unzipped_bytes:
            raise Lod2Error(f"{info.filename} is over {settings.max_unzipped_bytes} bytes unzipped")
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Lod2Error(f"{info.filename} is not UTF-8 text") from exc
    mesh = parse_obj(text, max_vertices=settings.max_vertices, max_faces=settings.max_faces)
    if mesh.crs != crs:
        raise Lod2Error(f"{info.filename}: the COORDINATE_SYSTEM header names {mesh.crs or 'no CRS'}, not {crs}")
    year, month, day = info.date_time[:3]
    return mesh, f"{year:04d}-{month:02d}-{day:02d}"


_INDEX_TEXT = ("source", "provider", "license", "attribution", "data_date", "crs")


def load_index(path: str | Path, settings: Lod2Settings = DEFAULT_SETTINGS) -> Lod2Index:
    """``lod2_index.json`` and its block ZIPs, checked: schema, licence, CRS, each ZIP's SHA-256 and path."""
    source = Path(path).expanduser()
    if not source.is_file():
        raise Lod2Error(f"{path} not found; rerun source madrid-lod2 for this scene")
    raw = source.read_bytes()
    try:
        doc = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Lod2Error(f"{path} is not JSON") from exc
    if not isinstance(doc, dict) or not is_schema(doc.get("schema"), INDEX_SCHEMA):
        raise Lod2Error(f"{path} is not {INDEX_SCHEMA}")
    if any(not isinstance(doc.get(k), str) or not doc.get(k) for k in _INDEX_TEXT):
        raise Lod2Error(f"{path}: the index needs {', '.join(_INDEX_TEXT)}")
    if doc["license"] != LICENCE:
        raise Lod2Error(f"{path}: licence {doc['license']} is not {LICENCE}")
    if doc["crs"] != CRS:
        raise Lod2Error(f"{path}: CRS {doc['crs']} is not {CRS}")
    entries = doc.get("blocks")
    if not isinstance(entries, list):
        raise Lod2Error(f"{path}: blocks must be a list")
    folder = source.parent.resolve()
    blocks: list[Lod2Block] = []
    for entry in sorted(entries, key=lambda e: str(e.get("cod_manzan")) if isinstance(e, dict) else ""):
        if not isinstance(entry, dict) or any(not isinstance(entry.get(k), str) for k in ("cod_manzan", "path", "sha256", "url")):
            raise Lod2Error(f"{path}: a block needs cod_manzan, path, sha256 and url")
        target = (folder / entry["path"]).resolve()
        if not target.is_relative_to(folder):
            raise Lod2Error(f"{path}: block path {entry['path']!r} leaves the index folder")
        if not target.is_file():
            raise Lod2Error(f"{path}: block {entry['cod_manzan']} is missing ({entry['path']})")
        body = target.read_bytes()
        if hashlib.sha256(body).hexdigest() != entry["sha256"]:
            raise Lod2Error(f"{path}: block {entry['cod_manzan']} does not match its sha256")
        try:
            mesh, obj_date = read_obj_zip(body, settings)  # the index CRS was checked to be CRS, the default
        except Lod2Error as exc:
            raise Lod2Error(f"{path}: block {entry['cod_manzan']}: {exc}") from exc
        blocks.append(Lod2Block(entry["cod_manzan"], entry["path"], entry["sha256"], entry["url"], obj_date, mesh))
    return Lod2Index(str(path), hashlib.sha256(raw).hexdigest(), doc["source"], doc["provider"], doc["license"],
                     doc["attribution"], doc["data_date"], doc["crs"], tuple(blocks))


# ---------------------------------------------------------------------------------------------------- geometry


def to_local(vertices: np.ndarray, frame: Mapping[str, Any]) -> np.ndarray:
    """EPSG:25830 metres → the scene's local grid: subtract ``origin_projected``; z is kept.

    The frame must be EPSG:25830 too (no reprojection here), and x/y that look like longitude and latitude are refused.
    """
    if frame.get("crs") != CRS or frame.get("unit", "m") != "m":
        raise Lod2Error(f"the scene frame is {frame.get('crs')}, not {CRS} in metres")
    if vertices.size and np.all(np.abs(vertices[:, 0]) <= 180.0) and np.all(np.abs(vertices[:, 1]) <= 90.0):
        raise Lod2Error("the OBJ x/y look like degrees, not EPSG:25830 metres")
    east, north = (float(v) for v in frame["origin_projected"])
    return vertices - np.array([east, north, 0.0])


@dataclass(frozen=True, slots=True)
class LocalGroup:
    """One OBJ group on the local grid: its id (``<COD_MANZAN>:<usemtl>``), triangles as their own vertex list, and plan hull."""

    id: str
    cod_manzan: str
    obj_date: str
    vertices: np.ndarray
    triangles: np.ndarray
    hull: BaseGeometry


def local_groups(index: Lod2Index, frame: Mapping[str, Any]) -> list[LocalGroup]:
    """Every group of every block on the local grid, in block then file order; groups with no area in plan are kept for the match."""
    found: list[LocalGroup] = []
    for block in index.blocks:
        moved = to_local(block.mesh.vertices, frame)
        for group in block.mesh.groups:
            used, inverse = np.unique(group.triangles, return_inverse=True)
            vertices = moved[used]
            triangles = inverse.astype(np.int64)  # np.unique keeps the (m, 3) shape of its input in its inverse
            hull = MultiPoint([tuple(p) for p in vertices[:, :2]]).convex_hull
            found.append(LocalGroup(f"{block.cod_manzan}:{group.name}", block.cod_manzan, block.obj_date, vertices, triangles, hull))
    return found


@dataclass(frozen=True, slots=True)
class Match:
    groups: tuple[LocalGroup, ...]
    cover: float


def match_landmark(footprint: BaseGeometry, groups: Sequence[LocalGroup], settings: Lod2Settings) -> Match | str:
    """The groups that are this footprint's volume, or why there is none (``too_small``, ``no_overlap``, ``low_cover``).

    A group is the landmark's when at least ``min_group_overlap`` of its plan hull lies on the footprint;
    together the matched hulls must cover ``min_footprint_cover`` of the footprint.
    """
    if footprint.area < settings.min_footprint_area_m2:
        return "too_small"
    matched = []
    for group in groups:
        if group.hull.area <= 0.0 or not group.hull.intersects(footprint):
            continue
        if group.hull.intersection(footprint).area / group.hull.area >= settings.min_group_overlap:
            matched.append(group)
    if not matched:
        return "no_overlap"
    cover = union_all([g.hull for g in matched]).intersection(footprint).area / footprint.area
    if cover < settings.min_footprint_cover:
        return "low_cover"
    return Match(tuple(matched), cover)


def cluster_simplify(vertices: np.ndarray, triangles: np.ndarray, cell_m: float) -> tuple[np.ndarray, np.ndarray]:
    """Vertex clustering: every vertex in one ``cell_m`` cube becomes their mean; collapsed and repeated triangles go."""
    unique, inverse = np.unique(np.floor(vertices / cell_m), axis=0, return_inverse=True)
    sums = np.zeros((unique.shape[0], 3))
    np.add.at(sums, inverse, vertices)
    merged = sums / np.bincount(inverse)[:, None]  # every cell holds at least one vertex
    remapped = inverse[triangles]
    if remapped.size:
        _, first = np.unique(np.sort(remapped), axis=0, return_index=True)
        remapped = remapped[np.sort(first)]
    # A collapsed triangle repeats a vertex, so it has no area and drop_degenerate removes it.
    mesh, _ = Mesh(merged, remapped).drop_degenerate()
    return compact(mesh.vertices, mesh.triangles)


def compact(vertices: np.ndarray, triangles: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Drop the vertices no triangle uses."""
    if not triangles.size:
        return np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64)
    used, inverse = np.unique(triangles, return_inverse=True)
    return vertices[used], inverse.astype(np.int64)  # np.unique keeps the (m, 3) shape of its input in its inverse


@dataclass(frozen=True, slots=True)
class Clip:
    """What ``clip_to_footprint`` kept and how: triangles dropped whole, cut at the footprint edge, and closure triangles added."""

    vertices: np.ndarray
    triangles: np.ndarray
    dropped: int
    cut: int
    closure: int


def clip_to_footprint(vertices: np.ndarray, triangles: np.ndarray, footprint: BaseGeometry, tolerance_m: float) -> Clip:
    """Keep only what stands on the footprint, cut exactly at its edge, and close what the cut opened.

    A vertex outside the footprint by at most ``tolerance_m`` moves onto its boundary. A triangle that still leaves
    the footprint is cut at its edge: a sloped or flat one in plan, a wall in its own vertical plane, each piece keeping
    the triangle's plane and facing. One with nothing on the footprint goes. Every edge the cut opened is walled straight
    down to the street (z 0) on the footprint edge (``close_cut``), so the volume is not left open where it was cut.
    Nothing kept lies outside the footprint, and the footprint is not changed.
    """
    if not triangles.size:
        return Clip(np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64), 0, 0, 0)
    moved = vertices.copy()
    xs, ys = moved[:, 0], moved[:, 1]
    outside = np.flatnonzero(~shapely.intersects_xy(footprint, xs, ys))  # a point on the edge intersects the footprint
    if outside.size:
        points = shapely.points(xs[outside], ys[outside])
        near = shapely.distance(footprint, points) <= tolerance_m
        boundary = footprint.boundary
        snapped = shapely.line_interpolate_point(boundary, shapely.line_locate_point(boundary, points[near]))
        moved[outside[near], 0] = shapely.get_x(snapped)
        moved[outside[near], 1] = shapely.get_y(snapped)
    grown = footprint.buffer(_EDGE_M)
    shapely.prepare(grown)  # pragma: no mutate: preparing only speeds the cover tests up
    plan = moved[:, :2][triangles]  # pragma: no mutate: shapely's predicates are planar, so a z coordinate changes none
    normals = Mesh(moved, triangles).face_normals()
    whole: list[int] = []
    pieces: list[np.ndarray] = []
    dropped = cut = 0
    for k, tri in enumerate(moved[triangles]):
        if grown.covers(_plan_shape(plan[k])):
            whole.append(k)
            continue
        found = _clip_triangle(tri, normals[k], footprint, grown)
        if found:
            cut += 1
            pieces.extend(found)
        else:
            dropped += 1
    kept = merge([Mesh(*compact(moved, triangles[whole])), _soup(pieces)])
    closed_v, closed_t, closure = close_cut(moved, triangles, kept.vertices, kept.triangles, footprint.boundary)
    return Clip(closed_v, closed_t, dropped, cut, closure)


def _triangles_of(polygon: Polygon) -> list[np.ndarray]:
    """A polygon as (3, 2) triangles, by constrained Delaunay without new points."""
    return [np.array(t.exterior.coords[:3]) for t in shapely.constrained_delaunay_triangles(polygon).geoms]


def _clip_triangle(tri: np.ndarray, normal: np.ndarray, footprint: BaseGeometry, grown: BaseGeometry) -> list[np.ndarray]:
    """The (3, 3) pieces of one triangle that lie on the footprint, each wound like the triangle.

    A sloped or flat triangle is cut by the footprint itself, so no piece has plan area outside it. A wall has no plan
    area: its line is cut by ``grown`` (the footprint 1e-6 m wider), so a wall along the edge is not lost to rounding.
    """
    length = float(np.linalg.norm(normal))
    if length == 0.0:
        return []
    unit = normal / length
    out: list[np.ndarray] = []
    if abs(unit[2]) >= _WALL_NZ:  # pragma: no mutate: no unit normal z is exactly 1e-3, so >= and > agree
        # Sloped or flat: cut in plan, each new corner on the triangle's own plane.
        origin = tri[0]
        frame = np.array([tri[1, :2] - origin[:2], tri[2, :2] - origin[:2]]).T
        rise = np.array([tri[1, 2] - origin[2], tri[2, 2] - origin[2]])
        for polygon in polygons_of(Polygon(tri[:, :2]).intersection(footprint)):
            for piece in _triangles_of(polygon):
                weights = np.linalg.solve(frame, (piece - origin[:2]).T).T
                out.append(np.column_stack([piece, origin[2] + weights @ rise]))
    else:
        # A wall: cut along its plan line, in the plane (s along the line, z) of the wall.
        plan = tri[:, :2]
        gaps = np.linalg.norm(plan[:, None] - plan[None], axis=2)
        i, j = np.unravel_index(int(np.argmax(gaps)), gaps.shape)
        span = float(gaps[i, j])
        if span <= 1e-9:  # pragma: no mutate: a wall triangle with a plan span of exactly 1e-9 m does not occur
            return []
        axis = (plan[j] - plan[i]) / span
        low, high = float(tri[:, 2].min()), float(tri[:, 2].max())
        strips = []
        for line in shapely.get_parts(LineString([plan[i], plan[j]]).intersection(grown)):
            if line.geom_type == "LineString" and not line.is_empty:
                along = (np.asarray(line.coords) - plan[i]) @ axis
                strips.append(shapely.box(float(along.min()), low, float(along.max()), high))
        section = Polygon(np.column_stack([(plan - plan[i]) @ axis, tri[:, 2]]))
        if strips:
            for polygon in polygons_of(section.intersection(union_all(strips))):
                for piece in _triangles_of(polygon):
                    out.append(np.column_stack([plan[i] + piece[:, :1] * axis, piece[:, 1]]))
    if not out:
        return []
    stack = np.array(out)
    flip = np.signbit(_soup(out).face_normals() @ unit)  # a piece wound against the triangle
    stack[flip] = stack[flip][:, ::-1]
    return list(stack)


def _soup(pieces: Sequence[np.ndarray]) -> Mesh:
    """(3, 3) triangles as one mesh, each with its own three vertices."""
    count = len(pieces)
    if not count:
        return Mesh.empty()
    return Mesh(np.array(pieces).reshape(3 * count, 3), np.arange(3 * count).reshape(count, 3))


_Corner = tuple[float, ...]


def _open_edges(vertices: np.ndarray, triangles: np.ndarray) -> dict[tuple[_Corner, _Corner], tuple[np.ndarray, np.ndarray]]:
    """Edges one triangle uses, welded at ``_WELD_M``: their key (both welded corners, sorted) and the edge as its triangle runs it."""
    cells, first, inverse = np.unique(np.round(vertices / _WELD_M), axis=0, return_index=True, return_inverse=True)
    welded = inverse[triangles]  # with axis=0 the inverse is flat
    runs = np.concatenate([welded[:, [0, 1]], welded[:, [1, 2]], welded[:, [2, 0]]])
    runs = runs[runs[:, 0] != runs[:, 1]]
    undirected, counts = np.unique(np.sort(runs), axis=0, return_counts=True)
    single = {(a, b) for a, b in undirected[counts == 1].tolist()}
    found: dict[tuple[_Corner, _Corner], tuple[np.ndarray, np.ndarray]] = {}
    for a, b in runs.tolist():
        if (min(a, b), max(a, b)) in single:
            ca, cb = tuple(cells[a].tolist()), tuple(cells[b].tolist())
            found[(min(ca, cb), max(ca, cb))] = (vertices[first[a]], vertices[first[b]])
    return found


def close_cut(before_v: np.ndarray, before_t: np.ndarray, vertices: np.ndarray, triangles: np.ndarray,
              edge: BaseGeometry) -> tuple[np.ndarray, np.ndarray, int]:
    """``(vertices, triangles, closure triangles)``: each edge open now, not before, and on ``edge``, walled down to z 0.

    ``edge`` is the footprint boundary, where the cut runs: an edge counts as on it when both its ends are within
    ``_CUT_M`` of it. The wall below an edge ``a -> b`` of the triangle that kept it runs ``b -> a``, as the triangle cut away did, so
    it faces the same way. An edge at the street or a vertical one gives a wall with no area, which goes. The source's
    own open edges (the LoD2 groups are not closed meshes) are left as they are.
    """
    was_open = _open_edges(before_v, before_t)
    walls = []
    for key, (a, b) in sorted(_open_edges(vertices, triangles).items()):
        if key in was_open or shapely.distance(edge, shapely.points([a[:2], b[:2]])).max() > _CUT_M:
            continue
        a0, b0 = np.array([a[0], a[1], 0.0]), np.array([b[0], b[1], 0.0])
        walls += [np.array([b, a, a0]), np.array([b, a0, b0])]
    if not walls:
        return vertices, triangles, 0
    skirt, _ = _soup(walls).drop_degenerate()
    closed = merge([Mesh(vertices, triangles), skirt])
    return closed.vertices, closed.triangles, skirt.triangle_count


def _plan_shape(tri: np.ndarray) -> BaseGeometry:
    """A triangle in plan as a polygon, or as the line through its corners when it has no area (a vertical wall)."""
    shape = Polygon(tri)
    return shape if shape.area > 1e-9 else LineString(tri)  # pragma: no mutate: no triangle's plan area is exactly 1e-9 m², so > and >= agree


def plan_cover(vertices: np.ndarray, triangles: np.ndarray) -> BaseGeometry:
    """The plan area the triangles cover (walls cover none)."""
    shapes = [Polygon(t) for t in vertices[:, :2][triangles]]  # pragma: no mutate: shapely areas are planar, so a z coordinate changes none
    shapes = [s for s in shapes if s.area > 1e-9]  # pragma: no mutate: no triangle's plan area is exactly 1e-9 m², so > and >= agree
    return union_all(shapes)  # empty when no triangle has plan area


def outside_area_m2(vertices: np.ndarray, triangles: np.ndarray, footprint: BaseGeometry) -> float:
    """Plan area of the triangles outside the footprint, rounded to 1e-6 m²: the clip's own check."""
    return round(float(plan_cover(vertices, triangles).difference(footprint).area), 6)


_COLUMN_M = 0.05
"""Plan cell of one wall column: the vertices of a vertical wall edge share it."""
_WALL_SPAN_M = 1.0
"""A column whose vertices span at least this much height is a wall; its lowest vertex is the ground there."""


def ground_offset(vertices: np.ndarray, footprint: BaseGeometry, near_m: float) -> tuple[float, float]:
    """``(ground, lowest)``: the orthometric height the local ground (z 0) is set at, and the lowest vertex.

    The map's ground is flat while Madrid's streets slope, so one height cannot fit every wall. The ground is the
    highest base of the outer walls (wall columns within ``near_m`` of the footprint edge): no wall floats above
    the street, and the walls on the lower side sink into it. Without an outer wall column it is the lowest vertex.
    """
    lowest = float(vertices[:, 2].min())
    cells, inverse = np.unique(np.floor(vertices[:, :2] / _COLUMN_M), axis=0, return_inverse=True)
    low = np.full(len(cells), np.inf)
    high = np.full(len(cells), -np.inf)
    np.minimum.at(low, inverse, vertices[:, 2])
    np.maximum.at(high, inverse, vertices[:, 2])
    first = np.zeros(len(cells), dtype=np.int64)
    first[inverse[::-1]] = np.arange(len(inverse))[::-1]
    walls = np.flatnonzero(high - low >= _WALL_SPAN_M)
    if not walls.size:
        return lowest, lowest
    xy = vertices[first[walls]]
    edge = shapely.distance(footprint.boundary, shapely.points(xy[:, 0], xy[:, 1])) <= near_m
    if not edge.any():
        return lowest, lowest
    return float(low[walls][edge].max()), lowest


def split_roof_wall(vertices: np.ndarray, triangles: np.ndarray, roof_min_normal_z: float) -> tuple[np.ndarray, np.ndarray]:
    """Triangles whose unit normal points up by at least ``roof_min_normal_z`` are roof; the rest are wall."""
    normals = Mesh(vertices, triangles).face_normals()
    length = np.linalg.norm(normals, axis=1)
    nz = normals[:, 2] / np.where(length > 0, length, np.inf)  # a triangle with no area has nz 0
    roof = nz >= roof_min_normal_z
    return triangles[roof], triangles[~roof]


def outward(vertices: np.ndarray, triangles: np.ndarray) -> np.ndarray:
    """The triangles wound so the group's signed volume is positive (normals out), whatever the file's winding."""
    return triangles[:, ::-1].copy() if signed_volume(Mesh(vertices, triangles)) < 0 else triangles


def group_colliders(footprint: BaseGeometry, groups: Sequence[tuple[str, BaseGeometry, float]],
                    building_id: str) -> tuple[list[Mesh], list[Issue]]:
    """One closed prism per matched group, its plan hull clipped to the footprint, from 0 to its top above ground."""
    colliders: list[Mesh] = []
    issues: list[Issue] = []
    for group_id, hull, top in groups:
        parts = [p for p in polygons_of(hull.intersection(footprint)) if p.area > 1e-6]  # pragma: no mutate: no clipped hull is exactly 1e-6 m², so > and >= agree; test_a_small_group_gets_its_collider pins the threshold
        if not parts or top <= 0:
            issues.append(Issue("landmark_lod2_collider_not_closed", "critical", building_id, f"{group_id}: no closed collider (empty plan or height)"))
            continue
        for part in parts:
            mesh = prism(part, 0.0, top, bottom=True)
            defects = closed_manifold_defects(mesh)
            if defects or signed_volume(mesh) <= 0:
                issues.append(Issue("landmark_lod2_collider_not_closed", "critical", building_id, f"{group_id}: {defects} collider defects"))
                continue
            colliders.append(mesh)
    return colliders, issues


@dataclass(frozen=True, slots=True)
class Lod2Part:
    """What replaces a matched landmark's placeholder: its visual primitives, closed colliders and ``lod2`` record."""

    visual: tuple[Primitive, ...]
    colliders: tuple[Mesh, ...]
    record: dict[str, Any]


def _simplify_to_cap(vertices: np.ndarray, triangles: np.ndarray, settings: Lod2Settings) -> tuple[np.ndarray, np.ndarray, float | None]:
    """Vertex clustering with a doubling cell until the cap holds; the cell used. None, and the mesh as it came, when no
    pass was needed or none reached the cap (the caller checks the cap)."""
    if len(triangles) <= settings.max_triangles:
        return vertices, triangles, None
    cell = settings.simplify_cell_m
    for _ in range(settings.max_simplify_passes):
        out_v, out_t = cluster_simplify(vertices, triangles, cell)
        if len(out_t) <= settings.max_triangles:
            return out_v, out_t, cell
        cell *= 2.0
    return vertices, triangles, None


def build_lod2_part(
    building_id: str,
    footprint: BaseGeometry,
    groups: Sequence[LocalGroup],
    index: Lod2Index,
    *,
    wall_material: str,
    roof_material: str,
    max_height_m: float,
    settings: Lod2Settings = DEFAULT_SETTINGS,
) -> tuple[Lod2Part | None, list[Issue]]:
    """The LoD2 volume of one landmark footprint, or None and why (an issue). ``groups`` are already on the local grid."""
    found = match_landmark(footprint, groups, settings)
    if found == "too_small":
        return None, [Issue("landmark_lod2_footprint_too_small", "warning", building_id,
                            f"footprint {footprint.area:.1f} m² is below {settings.min_footprint_area_m2:g} m²; the placeholder stays")]
    if isinstance(found, str):
        why = "no LoD2 group lies on it" if found == "no_overlap" else f"the LoD2 groups cover less than {settings.min_footprint_cover:g} of it"
        return None, [Issue("landmark_lod2_no_match", "info", building_id, f"{why}; the placeholder and its tower stay")]
    meshes = [(g, outward(g.vertices, g.triangles)) for g in found.groups]
    ground, lowest = ground_offset(np.concatenate([g.vertices for g, _ in meshes]), footprint, settings.clip_tolerance_m)
    top = float(max(g.vertices[:, 2].max() for g, _ in meshes)) - ground
    if top <= 0 or top > max_height_m:
        return None, [Issue("landmark_lod2_height_out_of_range", "warning", building_id,
                            f"LoD2 height {top:.1f} m is outside (0, {max_height_m:g}]; the placeholder stays")]
    issues: list[Issue] = []
    low = {g.id for g, _ in meshes if float(g.vertices[:, 2].max()) <= ground}
    if low:
        # On a slope a group on the low side can end under the ground the outer walls set: it is not drawn.
        issues = [Issue("landmark_lod2_group_below_ground", "warning", building_id,
                        f"{g.id}: its top {float(g.vertices[:, 2].max()):.2f} m is not above the ground at {ground:.2f} m; it is left out")
                  for g, _ in meshes if g.id in low]
        meshes = [(g, t) for g, t in meshes if g.id not in low]
        cover = union_all([g.hull for g, _ in meshes]).intersection(footprint).area / footprint.area
        if cover < settings.min_footprint_cover:
            return None, [*issues, Issue("landmark_lod2_no_match", "info", building_id,
                                         f"the LoD2 groups above the ground cover less than {settings.min_footprint_cover:g} of it; "
                                         "the placeholder and its tower stay")]
        found = Match(tuple(g for g, _ in meshes), cover)
    offset = 0
    vertices_parts, triangle_parts = [], []
    for group, tris in meshes:
        vertices_parts.append(group.vertices - np.array([0.0, 0.0, ground]))
        triangle_parts.append(tris + offset)
        offset += len(group.vertices)
    vertices = np.concatenate(vertices_parts)
    triangles = np.concatenate(triangle_parts)
    sunk = vertices[:, 2] < 0.0
    vertices[sunk, 2] = 0.0  # the walls of the lower side end at the street instead of below it
    before = int(len(triangles))
    clip = clip_to_footprint(vertices, triangles, footprint, settings.clip_tolerance_m)
    vertices, triangles = clip.vertices, clip.triangles
    dropped, cut, closure = clip.dropped, clip.cut, clip.closure
    clipped = int(len(triangles))
    vertices, triangles, cell = _simplify_to_cap(vertices, triangles, settings)
    if cell is not None:
        again = clip_to_footprint(vertices, triangles, footprint, settings.clip_tolerance_m)
        vertices, triangles = again.vertices, again.triangles
        dropped, cut, closure = dropped + again.dropped, cut + again.cut, closure + again.closure
    mesh, _ = Mesh(vertices, triangles).drop_degenerate()
    vertices, triangles = compact(mesh.vertices, mesh.triangles)
    if len(triangles) > settings.max_triangles:
        return None, [*issues, Issue("landmark_lod2_over_cap", "warning", building_id,
                                     f"{len(triangles)} triangles stay over the cap of {settings.max_triangles} after "
                                     f"{settings.max_simplify_passes} passes; the placeholder stays")]
    if not len(triangles):
        return None, [*issues, Issue("landmark_lod2_no_match", "info", building_id,
                                     "nothing of the LoD2 groups is left on the footprint; the placeholder and its tower stay")]
    outside = outside_area_m2(vertices, triangles, footprint)
    # The floor fills the plan the kept triangles leave open (a notch of an L-shaped group, a gap between groups).
    floor = union_all([p for p in polygons_of(footprint.difference(plan_cover(vertices, triangles))) if p.area > 1e-6])  # pragma: no mutate: no uncovered piece is exactly 1e-6 m², so > and >= agree
    roof_t, wall_t = split_roof_wall(vertices, triangles, settings.roof_min_normal_z)
    visual: list[Primitive] = []
    if len(wall_t):
        visual.append(Primitive(Mesh(*compact(vertices, wall_t)), wall_material))
    roof_meshes = [Mesh(*compact(vertices, roof_t))] if len(roof_t) else []
    floor_triangles = 0
    if not floor.is_empty:
        # The part of the footprint no group covers gets a floor at street level, so the ground under it is not open.
        filler = cap(floor, 0.0)  # facing up, cap's default
        floor_triangles = filler.triangle_count
        roof_meshes.append(filler)
    if roof_meshes:
        visual.append(Primitive(merge(roof_meshes), roof_material))
    hulls = [(g.id, g.hull, float(g.vertices[:, 2].max()) - ground) for g, _ in meshes]
    colliders, collider_issues = group_colliders(footprint, hulls, building_id)
    issues += collider_issues
    blocks = sorted({g.cod_manzan for g in found.groups})
    record = {
        "provider": index.provider,
        "source": index.source,
        "license": index.license,
        "attribution": index.attribution,
        "data_date": index.data_date,
        "cod_manzan": blocks,
        "obj_dates": sorted({g.obj_date for g in found.groups}),
        "objects": [g.id for g in found.groups],
        "representation": "observed_lod2",
        "representative_of_real_volume": True,
        "representative_of_real_facade": False,
        "needs_custom_model": False,
        "geometry": {"origin": "observed", "crs": index.crs, "note": "LoD2 triangles of the municipal 3D model, moved to the local grid"},
        "match": {"origin": "estimated", "method": "plan_hull_overlap", "footprint_cover": round(found.cover, 4),
                  "min_group_overlap": settings.min_group_overlap, "min_footprint_cover": settings.min_footprint_cover},
        "ground_offset": {"origin": "estimated", "method": "highest_outer_wall_base", "orthometric_m": round(ground, 3),
                          "lowest_orthometric_m": round(lowest, 3), "sunk_vertices": int(sunk.sum()), "height_m": round(top, 3)},
        "clip": {"origin": "estimated", "method": "clip_to_osm_footprint", "tolerance_m": settings.clip_tolerance_m,
                 "dropped_triangles": dropped, "cut_triangles": cut, "closure_triangles": closure,
                 "outside_area_m2": outside, "floor_triangles": floor_triangles},
        "simplification": {"origin": "estimated", "method": "vertex_clustering", "cell_m": cell,
                           "max_triangles": settings.max_triangles, "triangles_before": before,
                           "triangles_after_clip": clipped, "triangles_after": int(len(triangles))},
        "collider": {"method": "group_hull_prism_clipped_to_footprint", "parts": len(colliders),
                     "triangles": sum(m.triangle_count for m in colliders)},
        "materials": {"wall": wall_material, "roof": roof_material},
    }
    return Lod2Part(tuple(visual), tuple(colliders), record), issues


def lod2_parts(
    landmarks: Mapping[str, BaseGeometry],
    index: Lod2Index,
    frame: Mapping[str, Any],
    *,
    wall_material: str,
    roof_material: str,
    max_height_m: float,
    settings: Lod2Settings = DEFAULT_SETTINGS,
) -> tuple[dict[str, Lod2Part], dict[str, list[Issue]]]:
    """``build_lod2_part`` for each landmark footprint, in id order. A frame in another CRS gives each landmark a warning and no part."""
    issues: dict[str, list[Issue]] = {}
    parts: dict[str, Lod2Part] = {}
    try:
        groups = local_groups(index, frame)
    except Lod2Error as exc:
        for bid in sorted(landmarks):
            issues[bid] = [Issue("landmark_lod2_crs_mismatch", "warning", bid, f"{exc}; the placeholder stays")]
        return parts, issues
    for bid in sorted(landmarks):
        footprint = landmarks[bid]
        near = [g for g in groups if g.hull.intersects(footprint)]
        part, found = build_lod2_part(bid, footprint, near, index, wall_material=wall_material, roof_material=roof_material,
                                      max_height_m=max_height_m, settings=settings)
        issues[bid] = found
        if part is not None:
            parts[bid] = part
    return parts, issues
