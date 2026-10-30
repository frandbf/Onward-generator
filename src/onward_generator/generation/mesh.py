"""Triangle meshes from local-metre polygons, plus topology checks.

Convention: x east, y north, z up (metres). Triangles are counter-clockwise
when seen from the side their normal points to.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import numpy as np
import shapely
from shapely import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient

_EMPTY_V = np.zeros((0, 3), dtype=np.float64)
_EMPTY_T = np.zeros((0, 3), dtype=np.int64)
_QUANT = 1e-4  # 0.1 mm: vertex identity for topology checks


@dataclass(frozen=True, slots=True)
class Mesh:
    vertices: np.ndarray  # (n, 3) float64
    triangles: np.ndarray  # (m, 3) int64

    @staticmethod
    def empty() -> Mesh:
        return Mesh(_EMPTY_V, _EMPTY_T)

    @property
    def triangle_count(self) -> int:
        return int(self.triangles.shape[0])

    @property
    def vertex_count(self) -> int:
        return int(self.vertices.shape[0])

    def face_normals(self) -> np.ndarray:
        v = self.vertices[self.triangles]
        return np.cross(v[:, 1] - v[:, 0], v[:, 2] - v[:, 0])

    def triangle_areas(self) -> np.ndarray:
        return 0.5 * np.linalg.norm(self.face_normals(), axis=1)

    def bounds(self) -> tuple[list[float], list[float]]:
        if not self.vertex_count:
            return [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]
        return self.vertices.min(axis=0).tolist(), self.vertices.max(axis=0).tolist()

    def drop_degenerate(self, min_area: float = 1e-8) -> tuple[Mesh, int]:
        keep = self.triangle_areas() > min_area
        return Mesh(self.vertices, self.triangles[keep]), int((~keep).sum())


def merge(meshes: Iterable[Mesh]) -> Mesh:
    vs, ts, offset = [], [], 0
    for m in meshes:
        if not m.triangle_count:
            continue
        vs.append(m.vertices)
        ts.append(m.triangles + offset)
        offset += m.vertex_count
    if not vs:
        return Mesh.empty()
    return Mesh(np.vstack(vs), np.vstack(ts))


def polygons_of(geometry: BaseGeometry) -> list[Polygon]:
    if geometry.is_empty:
        return []
    if isinstance(geometry, Polygon):
        return [geometry]
    if isinstance(geometry, MultiPolygon):
        return list(geometry.geoms)
    return [g for part in getattr(geometry, "geoms", []) for g in polygons_of(part)]


def cap(geometry: BaseGeometry, z: float, *, up: bool = True) -> Mesh:
    """Flat triangulation (constrained Delaunay, no Steiner points) of polygons at height ``z``."""
    vertices: list[tuple[float, float]] = []
    index: dict[tuple[float, float], int] = {}
    triangles: list[tuple[int, int, int]] = []
    for polygon in polygons_of(geometry):
        for tri in shapely.constrained_delaunay_triangles(polygon).geoms:
            (ax, ay), (bx, by), (cx, cy) = list(tri.exterior.coords)[:3]
            ids = []
            for p in ((ax, ay), (bx, by), (cx, cy)):
                if p not in index:
                    index[p] = len(vertices)
                    vertices.append(p)
                ids.append(index[p])
            ccw = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax) > 0
            a, b, c = ids
            triangles.append((a, b, c) if ccw == up else (a, c, b))
    if not triangles:
        return Mesh.empty()
    v = np.array(vertices, dtype=np.float64)
    return Mesh(np.column_stack([v, np.full(len(v), z)]), np.array(triangles, dtype=np.int64))


def _ring_walls(coords: Sequence[tuple[float, float]], z0: float, z1: float, inward: bool, min_edge: float) -> Mesh:
    vertices: list[tuple[float, float, float]] = []
    triangles: list[tuple[int, int, int]] = []
    for (ax, ay), (bx, by) in zip(coords[:-1], coords[1:], strict=True):
        if (bx - ax) ** 2 + (by - ay) ** 2 < min_edge**2:
            continue
        i = len(vertices)
        vertices += [(ax, ay, z0), (bx, by, z0), (bx, by, z1), (ax, ay, z1)]
        if inward:
            triangles += [(i, i + 2, i + 1), (i, i + 3, i + 2)]
        else:
            triangles += [(i, i + 1, i + 2), (i, i + 2, i + 3)]
    if not triangles:
        return Mesh.empty()
    return Mesh(np.array(vertices, dtype=np.float64), np.array(triangles, dtype=np.int64))


def walls(geometry: BaseGeometry, z0: float, z1: float, *, inward: bool = False, min_edge: float = 1e-6) -> Mesh:
    """Vertical faces along every ring. Outward (default) faces away from the polygon interior."""
    parts = []
    for polygon in polygons_of(geometry):
        p = orient(polygon, sign=1.0)  # exterior CCW, holes CW
        for ring in (p.exterior, *p.interiors):
            parts.append(_ring_walls(list(ring.coords), z0, z1, inward, min_edge))
    return merge(parts)


def _ring_area(coords: Sequence[tuple[float, float]]) -> float:
    total = 0.0
    for (ax, ay), (bx, by) in zip(coords, coords[1:]):
        total += ax * by - bx * ay
    return abs(total) * 0.5


def drop_degenerate_holes(geometry: BaseGeometry, min_area: float = 1e-4) -> tuple[BaseGeometry, int]:
    """Drop holes smaller than ``min_area`` square metres. Returns the geometry and how many were removed.

    A road clipped by the map edge can leave a there-and-back spike recorded as a hole.
    The spike has no area, and extruding it builds the same wall twice.
    """
    if geometry.is_empty or min_area < 0:
        return geometry, 0
    rebuilt: list[Polygon] = []
    dropped = 0
    for polygon in polygons_of(geometry):
        holes: list[list[tuple[float, float]]] = []
        removed = 0
        for ring in polygon.interiors:
            coords = list(ring.coords)
            if _ring_area(coords) < min_area:
                removed += 1
                continue
            holes.append(coords)
        dropped += removed
        if removed == 0:
            rebuilt.append(polygon)
        elif holes:
            rebuilt.append(Polygon(polygon.exterior.coords, holes))
        else:
            rebuilt.append(Polygon(polygon.exterior.coords))
    if not dropped:
        return geometry, 0
    if not rebuilt:
        return Polygon(), dropped
    return (rebuilt[0] if len(rebuilt) == 1 else MultiPolygon(rebuilt)), dropped


def open_point_contacts(geometry: BaseGeometry, gap_m: float = 0.01, precision_m: float = 0.001) -> BaseGeometry:
    """Open a ring that only kisses another ring, so the extrusion stays one manifold.

    A carriageway that leaves the map can touch the site boundary at a single
    point. The hole and the outer ring then share that vertex. Welding the slab
    on the 0.1 mm grid makes one vertical edge belong to both rings. Cutting a
    1 cm disk at that point joins the hole to the outside. Two polygons of one
    slab that touch at a corner (a carriageway cut by its crossings) are
    separated the same way. A shared edge is left alone.

    The cut is snapped to ``precision_m`` (the 1 mm grid of ``geo``): where an edge leaves the disk almost
    through one of its vertices, the new crossing point lies microns from that
    vertex, the 0.1 mm weld merges the pair and the slab stops closing. Two
    pieces that leave their contact almost parallel are still less than the
    grid apart on the rim, and snapping makes them touch again there; the disk
    then doubles until the snapped result has no contact (Madrid r300). A disk
    that would swallow the whole slab gives up and returns it unchanged.
    """
    if geometry.is_empty or gap_m <= 0:
        return geometry

    def contacts(of: BaseGeometry) -> list[BaseGeometry]:
        return _point_contacts(of, gap_m)

    cuts = contacts(geometry)
    while cuts:
        opened = shapely.set_precision(shapely.make_valid(geometry.difference(shapely.union_all(cuts).buffer(gap_m))), precision_m)
        if opened.is_empty:
            return geometry
        again = contacts(opened)
        if not again:
            return opened
        cuts += again
        gap_m *= 2
    return geometry


def _point_contacts(geometry: BaseGeometry, gap_m: float) -> list[BaseGeometry]:
    """Where two rings of ``geometry`` touch at a point or along less than ``gap_m``."""
    cuts = []
    parts = polygons_of(geometry)
    tree = shapely.STRtree(parts)
    for polygon in parts:
        rings = [polygon.exterior, *polygon.interiors]
        for index, ring in enumerate(rings):
            for other in rings[index + 1 :]:
                touch = ring.intersection(other)
                if _point_contact(touch, gap_m):
                    cuts.append(touch)
        # Two polygons of a valid slab can only touch at points.
        cuts.extend(polygon.intersection(parts[int(neighbour)]) for neighbour in tree.query(polygon, predicate="touches"))
    return cuts


def _point_contact(touch: BaseGeometry, gap_m: float) -> bool:
    if touch.is_empty:
        return False
    if touch.geom_type in {"Point", "MultiPoint"}:
        return True
    if touch.geom_type == "GeometryCollection":
        return all(_point_contact(part, gap_m) for part in touch.geoms)
    return float(getattr(touch, "length", 0.0)) < gap_m


def prism(geometry: BaseGeometry, z0: float, z1: float, *, bottom: bool) -> Mesh:
    """Extrusion with a top cap; with ``bottom`` the result is closed."""
    parts = [walls(geometry, z0, z1), cap(geometry, z1, up=True)]
    if bottom:
        parts.append(cap(geometry, z0, up=False))
    return merge(parts)


def _keys(mesh: Mesh) -> np.ndarray:
    q = np.round(mesh.vertices / _QUANT).astype(np.int64)
    _, inverse = np.unique(q, axis=0, return_inverse=True)
    return inverse.reshape(-1)[mesh.triangles]


def directed_edges(mesh: Mesh) -> Counter[tuple[int, int]]:
    t = _keys(mesh)
    edges: Counter[tuple[int, int]] = Counter()
    for a, b, c in t.tolist():
        edges[(a, b)] += 1
        edges[(b, c)] += 1
        edges[(c, a)] += 1
    return edges


def closed_manifold_defects(mesh: Mesh) -> int:
    """Directed edges without exactly one opposite twin (0 for a closed, consistently oriented mesh)."""
    edges = directed_edges(mesh)
    return sum(1 for (a, b), n in edges.items() if n != 1 or edges.get((b, a), 0) != 1)


def open_base_defects(mesh: Mesh, z_base: float) -> int:
    """Like :func:`closed_manifold_defects`, but edges lying on ``z = z_base`` may be unpaired."""
    q = np.round(mesh.vertices / _QUANT).astype(np.int64)
    uniq, inverse = np.unique(q, axis=0, return_inverse=True)
    z_of = uniq[:, 2] * _QUANT
    t = inverse.reshape(-1)[mesh.triangles]
    edges: Counter[tuple[int, int]] = Counter()
    for a, b, c in t.tolist():
        edges[(a, b)] += 1
        edges[(b, c)] += 1
        edges[(c, a)] += 1
    defects = 0
    for (a, b), n in edges.items():
        twin = edges.get((b, a), 0)
        on_base = abs(z_of[a] - z_base) < 1e-6 and abs(z_of[b] - z_base) < 1e-6
        if n != 1 or twin > 1 or (twin == 0 and not on_base):
            defects += 1
    return defects


def open_edge_defects(
    mesh: Mesh,
    *,
    z_base: float | None = None,
    allowed: Iterable[tuple[Sequence[float], Sequence[float]]] = (),
    quantum: float = _QUANT,
) -> int:
    """Edge defects where unpaired edges are tolerated only on ``z = z_base`` or in ``allowed``.

    ``allowed`` lists segments (p, q); an unpaired edge is tolerated if both of
    its endpoints lie on one of them. Vertices are welded on a ``quantum`` grid;
    a fine grid makes it unlikely that float noise straddles a rounding boundary.
    """
    q = np.round(mesh.vertices / quantum).astype(np.int64)
    uniq, inverse = np.unique(q, axis=0, return_inverse=True)
    pts = uniq * quantum
    t = inverse.reshape(-1)[mesh.triangles]
    edges: Counter[tuple[int, int]] = Counter()
    for a, b, c in t.tolist():
        edges[(a, b)] += 1
        edges[(b, c)] += 1
        edges[(c, a)] += 1
    segments = [(np.asarray(p, dtype=np.float64), np.asarray(r, dtype=np.float64)) for p, r in allowed]

    def on_allowed(x: np.ndarray) -> list[int]:
        hits = []
        for k, (p, r) in enumerate(segments):
            d = r - p
            length2 = float(d @ d)
            s = 0.0 if length2 == 0 else float(np.clip((x - p) @ d / length2, 0.0, 1.0))
            if np.linalg.norm(p + s * d - x) < max(2 * quantum, 1e-6):
                hits.append(k)
        return hits

    defects = 0
    for (a, b), n in edges.items():
        twin = edges.get((b, a), 0)
        if n != 1 or twin > 1:
            defects += 1
            continue
        if twin == 1:
            continue
        if z_base is not None and abs(pts[a, 2] - z_base) < max(quantum, 1e-6) and abs(pts[b, 2] - z_base) < max(quantum, 1e-6):
            continue
        if segments and set(on_allowed(pts[a])) & set(on_allowed(pts[b])):
            continue
        defects += 1
    return defects


def transform(mesh: Mesh, matrix: np.ndarray) -> Mesh:
    """Apply a 4×4 affine matrix. A negative determinant also flips winding to keep normals outward."""
    if not mesh.vertex_count:
        return mesh
    v = mesh.vertices @ matrix[:3, :3].T + matrix[:3, 3]
    tris = mesh.triangles if np.linalg.det(matrix[:3, :3]) > 0 else mesh.triangles[:, ::-1]
    return Mesh(v, tris.copy())


BOX_FACES = ("-x", "+x", "-y", "+y", "-z", "+z")


def box(x0: float, x1: float, y0: float, y1: float, z0: float, z1: float, *, omit: Iterable[str] = ()) -> Mesh:
    """Axis-aligned box with outward faces; faces named in ``omit`` (e.g. ``"+y"``) are left out."""
    skip = set(omit)
    unknown = skip - set(BOX_FACES)
    if unknown:
        raise ValueError(f"unknown box faces {sorted(unknown)}")
    quads = {
        "-x": [(x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (x0, y1, z0)],
        "+x": [(x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)],
        "-y": [(x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1)],
        "+y": [(x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (x1, y1, z0)],
        "-z": [(x0, y0, z0), (x0, y1, z0), (x1, y1, z0), (x1, y0, z0)],
        "+z": [(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)],
    }
    vertices, triangles = [], []
    for name in BOX_FACES:
        if name in skip:
            continue
        i = len(vertices)
        vertices += quads[name]
        triangles += [(i, i + 1, i + 2), (i, i + 2, i + 3)]
    if not triangles:
        return Mesh.empty()
    return Mesh(np.array(vertices, dtype=np.float64), np.array(triangles, dtype=np.int64))


def quad(p0: Sequence[float], p1: Sequence[float], p2: Sequence[float], p3: Sequence[float]) -> Mesh:
    """Planar quad p0→p1→p2→p3, counter-clockwise seen from the side its normal points to."""
    return Mesh(np.array([p0, p1, p2, p3], dtype=np.float64), np.array([(0, 1, 2), (0, 2, 3)], dtype=np.int64))


def signed_volume(mesh: Mesh) -> float:
    v = mesh.vertices[mesh.triangles]
    return float(np.einsum("ij,ij->i", v[:, 0], np.cross(v[:, 1], v[:, 2])).sum() / 6.0)


def upward_area(mesh: Mesh) -> float:
    n = mesh.face_normals()
    return float(0.5 * n[:, 2][n[:, 2] > 0].sum())
