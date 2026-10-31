"""Pitched roofs from the OSM ``roof:*`` tags of the Simple 3D Buildings scheme.

Reimplemented from the scheme's description (https://wiki.openstreetmap.org/wiki/Simple_3D_Buildings),
not from any renderer's code. A roof is a height field over the footprint: the footprint is cut along
the lines where the slope changes (a ridge, the hips), each piece is triangulated flat and lifted to
the field, and every wall edge the roof leaves above the eaves gets a vertical gable piece. The roof
uses the footprint's own vertices, so it closes on the walls without new seams.

Coordinates are local metres (x east, y north, z up). A compass bearing ``b`` is the vector
``(sin b, cos b)``.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace

import numpy as np
from shapely import LineString, MultiLineString, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient
from shapely.ops import split

from onward_generator.detail.config import PitchedRoof
from onward_generator.detail.s3db import first_value, parse_direction, parse_levels
from onward_generator.generation.mesh import Mesh, cap, merge
from onward_generator.geo.attributes import parse_metres

PITCHED_SHAPES = ("gabled", "hipped", "half-hipped", "pyramidal", "skillion", "dome")
# Shapes drawn as the nearest shape above; the record keeps the OSM value and says so.
APPROXIMATIONS = {
    "mansard": "hipped",
    "gambrel": "gabled",
    "saltbox": "gabled",
    "round": "gabled",
    "onion": "dome",
    "cone": "pyramidal",
}
DEFAULT_PITCH_DEG = 30.0
DEFAULT_MAX_RISE_M = 4.0  # with no roof:height, roof:levels or roof:angle
MAX_DOME_RISE_M = 15.0
# A hipped roof needs a near-rectangular footprint; on any other it is drawn as this shape.
RECTANGLE_FALLBACK = {"hipped": "pyramidal", "half-hipped": "gabled"}
HALF_HIP_FRACTION = 0.5  # a half-hipped end is a gable up to this fraction of the rise, then a hip
MAX_ROOF_VERTICES = 64  # the triangle cost of a roof grows with the footprint's vertices
HIPPED_MIN_FILL = 0.95  # footprint area / minimum rotated rectangle area for a straight hipped roof
MIN_RISE_M = 0.1
MIN_HALF_SPAN_M = 0.5  # a footprint narrower than 1 m across the slope keeps a flat roof
DOME_BANDS = 3
EAVE_SNAP_M = 1e-6  # a field value this close to the eaves is the eaves (no sliver gable)
ON_EDGE_M = 1e-7
MIN_PIECE_M2 = 1e-9


def pitched_spec(tags: Mapping[str, str], material: str, storey_m: float) -> PitchedRoof | None:
    """The roof the tags describe, or ``None`` when ``roof:shape`` is absent, flat or not drawable.

    The rise comes from ``roof:height``, then ``roof:levels × storey_m``, then ``roof:angle``; with none
    of them the builder uses ``DEFAULT_PITCH_DEG`` capped at ``DEFAULT_MAX_RISE_M``.
    """
    tag = first_value(tags, "roof:shape")
    shape = APPROXIMATIONS.get(tag, tag)
    if shape not in PITCHED_SHAPES:
        return None
    rise = parse_metres(tags.get("roof:height"))
    rule = "roof:height"
    if rise is None:
        levels = parse_levels(tags.get("roof:levels"))
        if levels:
            rise, rule = levels * storey_m, "roof:levels"
    angle = parse_metres(tags.get("roof:angle"))
    if angle is not None and angle >= 90.0:
        angle = None
    if rise is None:
        rule = "roof:angle" if angle is not None else "default_pitch"
    orientation = first_value(tags, "roof:orientation")
    return PitchedRoof(
        shape=shape, tag=tag, material=material, rise_m=rise, rise_rule=rule, angle_deg=angle,
        direction_deg=parse_direction(first_value(tags, "roof:direction")), across=orientation == "across",
    )


@dataclass(slots=True)
class BuiltRoof:
    shape: str  # the shape drawn
    roof: Mesh  # the sloped faces, in the roof material
    gables: Mesh  # vertical pieces between the eaves and the roof, in the wall material
    rise_m: float
    record: dict[str, object] = field(default_factory=dict)


def build_roof(polygon: Polygon, eave_z: float, spec: PitchedRoof) -> BuiltRoof | str:
    """The roof of ``spec`` on ``polygon`` with its eaves at ``eave_z``, or the reason it cannot be drawn."""
    if polygon.interiors:
        return "footprint_has_courtyard"
    polygon = orient(polygon)
    if len(polygon.exterior.coords) - 1 > MAX_ROOF_VERTICES:
        return "too_many_vertices"
    shape = spec.shape
    approximation = None if spec.tag == shape else f"{spec.tag} drawn as {shape}"
    if shape in RECTANGLE_FALLBACK:
        rect = polygon.minimum_rotated_rectangle
        if polygon.area < HIPPED_MIN_FILL * rect.area:  # pragma: no mutate: a footprint at exactly 95% is a float coincidence
            shape = RECTANGLE_FALLBACK[shape]
            approximation = f"{spec.tag} drawn as {shape}: footprint not rectangular"
    builders: dict[str, Callable[[Polygon, float, PitchedRoof], tuple[Mesh, Mesh, float, float] | str]] = {
        "gabled": _gabled, "hipped": _hipped, "half-hipped": _half_hipped, "skillion": _skillion, "pyramidal": _pyramidal,
        "dome": _dome,
    }
    built = builders[shape](polygon, eave_z, spec)
    if isinstance(built, str):
        return built
    roof, gables, rise, half_span = built
    record: dict[str, object] = {
        "type": shape,
        "rise_m": round(rise, 3),
        "pitch_deg": round(math.degrees(math.atan2(rise, half_span)), 1),
        "rise_rule": spec.rise_rule,
        "rise_origin": "estimated" if spec.rise_rule == "default_pitch" else "observed",
    }
    if shape in ("gabled", "skillion"):
        record["direction_origin"] = "observed" if spec.direction_deg is not None or spec.across else "estimated"
    if approximation is not None:
        record["approximation"] = approximation
    return BuiltRoof(shape, roof, gables, rise, record)


def with_rise(spec: PitchedRoof, rise_m: float) -> PitchedRoof:
    """``spec`` with its rise fixed at ``rise_m`` (a part whose roof must fit under its height)."""
    return replace(spec, rise_m=rise_m)


def _rise(spec: PitchedRoof, half_span: float) -> float:
    if spec.rise_m is not None:
        return spec.rise_m
    if spec.angle_deg is not None:
        return math.tan(math.radians(spec.angle_deg)) * half_span
    return min(DEFAULT_MAX_RISE_M, math.tan(math.radians(DEFAULT_PITCH_DEG)) * half_span)


def _axes(polygon: Polygon, spec: PitchedRoof) -> tuple[np.ndarray, np.ndarray]:
    """``(u, v)``: ``u`` along the ridge, ``v`` across it, down the slope of a skillion.

    ``roof:direction`` is the way the slope faces. Without it the ridge runs along the longest side
    of the minimum rotated rectangle, or across it with ``roof:orientation=across``.
    """
    if spec.direction_deg is not None:
        b = math.radians(spec.direction_deg)
        v = np.array([math.sin(b), math.cos(b)])
        return np.array([v[1], -v[0]]), v
    e0, e1 = _rect_edges(polygon)
    longer, shorter = (e0, e1) if np.linalg.norm(e0) >= np.linalg.norm(e1) else (e1, e0)  # pragma: no mutate: a square has no long side; either axis is right
    axis = shorter if spec.across else longer
    u = axis / np.linalg.norm(axis)
    return u, np.array([-u[1], u[0]])


def _rect_edges(polygon: Polygon) -> tuple[np.ndarray, np.ndarray]:
    corners = _ring(polygon.minimum_rotated_rectangle)
    return corners[1] - corners[0], corners[2] - corners[1]


def _ring(polygon: Polygon) -> np.ndarray:
    return np.array(polygon.exterior.coords)[:-1]


def _line(origin: np.ndarray, direction: np.ndarray, reach: float) -> LineString:
    return LineString([tuple(origin - direction * reach), tuple(origin + direction * reach)])


def _gabled(polygon: Polygon, eave: float, spec: PitchedRoof) -> tuple[Mesh, Mesh, float, float] | str:
    u, v = _axes(polygon, spec)
    d = _ring(polygon) @ v
    mid, half = (d.max() + d.min()) / 2, (d.max() - d.min()) / 2
    if half < MIN_HALF_SPAN_M:
        return "footprint_too_narrow"
    rise = _rise(spec, half)
    if rise < MIN_RISE_M:
        return "rise_too_small"

    def z_of(x: float, y: float) -> float:
        return rise * (1.0 - abs(x * v[0] + y * v[1] - mid) / half)

    reach = _reach(polygon)
    ridge = _line(_centre(polygon, u, v, mid), u, reach)
    roof, gables = _field_roof(polygon, eave, z_of, ridge)
    return roof, gables, rise, half


def _skillion(polygon: Polygon, eave: float, spec: PitchedRoof) -> tuple[Mesh, Mesh, float, float] | str:
    _u, v = _axes(polygon, spec)
    d = _ring(polygon) @ v
    low, high = d.min(), d.max()
    span = high - low
    if span < 2 * MIN_HALF_SPAN_M:
        return "footprint_too_narrow"
    rise = _rise(spec, span)
    if rise < MIN_RISE_M:
        return "rise_too_small"

    def z_of(x: float, y: float) -> float:
        return rise * (high - (x * v[0] + y * v[1])) / span

    roof, gables = _field_roof(polygon, eave, z_of)
    return roof, gables, rise, span


def _hipped(polygon: Polygon, eave: float, spec: PitchedRoof) -> tuple[Mesh, Mesh, float, float] | str:
    """Four faces of one pitch on the minimum rotated rectangle; the ridge runs along its long side."""
    centre, u, v, length, half_w = _rectangle_axes(polygon)
    if half_w < MIN_HALF_SPAN_M:
        return "footprint_too_narrow"
    rise = _rise(spec, half_w)
    if rise < MIN_RISE_M:
        return "rise_too_small"
    rect = _ring(polygon.minimum_rotated_rectangle)
    slope = rise / half_w

    def z_of(x: float, y: float) -> float:
        px, py = x - centre[0], y - centre[1]
        return slope * min(half_w - abs(px * v[0] + py * v[1]), length / 2 - abs(px * u[0] + py * u[1]))

    # With one pitch on every face the ridge stops half a width short of each end.
    inset = length / 2 - half_w
    ends = (centre - u * inset, centre + u * inset)
    lines = []
    for corner in rect:
        apex = min(ends, key=lambda end: float(np.linalg.norm(corner - end)))
        # The hip runs from the ridge end through the corner and as far again outside the rectangle.
        lines.append(LineString([tuple(apex), tuple(2 * corner - apex)]))
    if inset > 0:  # pragma: no mutate: at 0 the ridge is a point, and a zero-length cut changes nothing
        lines.append(LineString([tuple(ends[0]), tuple(ends[1])]))
    roof, gables = _field_roof(polygon, eave, z_of, *lines)
    return roof, gables, rise, half_w


def _rectangle_axes(polygon: Polygon) -> tuple[np.ndarray, np.ndarray, np.ndarray, float, float]:
    """``(centre, u, v, length, half width)`` of the minimum rotated rectangle; ``u`` runs along its long side."""
    rect = _ring(polygon.minimum_rotated_rectangle)
    e0, e1 = rect[1] - rect[0], rect[2] - rect[1]
    axis, other = (e0, e1) if np.linalg.norm(e0) >= np.linalg.norm(e1) else (e1, e0)  # pragma: no mutate: a square has no long side; either axis is right
    length = float(np.linalg.norm(axis))
    u = axis / length
    return rect.mean(axis=0), u, np.array([-u[1], u[0]]), length, float(np.linalg.norm(other)) / 2


def _half_hipped(polygon: Polygon, eave: float, spec: PitchedRoof) -> tuple[Mesh, Mesh, float, float] | str:
    """A gabled roof whose ends are gables up to ``HALF_HIP_FRACTION`` of the rise and hips above it.

    Every face has one pitch. The hips run from a point on the ridge to the end wall, a quarter of the
    width in from each corner when the fraction is a half; the end wall below them is a trapezoid gable.
    """
    centre, u, v, length, half_w = _rectangle_axes(polygon)
    if half_w < MIN_HALF_SPAN_M:
        return "footprint_too_narrow"
    rise = _rise(spec, half_w)
    if rise < MIN_RISE_M:
        return "rise_too_small"
    slope = rise / half_w
    step = HALF_HIP_FRACTION * rise  # the height of the gable under each hip

    def z_of(x: float, y: float) -> float:
        p = np.array([x, y]) - centre
        across, along = abs(float(p @ v)), abs(float(p @ u))
        return min(slope * (half_w - across), step + slope * (length / 2 - along))

    # The end face meets each side face where the two planes are level: half_w × (1 - fraction) from the end.
    reach = half_w * (1 - HALF_HIP_FRACTION)
    lines = [LineString([tuple(centre - u * (length / 2 - reach)), tuple(centre + u * (length / 2 - reach))])]
    for sign_u in (-1.0, 1.0):
        apex = centre + sign_u * u * (length / 2 - reach)
        end = centre + sign_u * u * length / 2
        for foot in (end + v * reach, end - v * reach):
            lines.append(LineString([tuple(apex), tuple(2 * foot - apex)]))
    roof, gables = _field_roof(polygon, eave, z_of, *lines)
    return roof, gables, rise, half_w


def _star_centre(polygon: Polygon) -> tuple[float, float] | None:
    """The centroid when it sees every edge from inside (a fan from it does not fold), else ``None``."""
    c = polygon.centroid
    ring = _ring(polygon)
    for k, a in enumerate(ring):
        b = ring[(k + 1) % len(ring)]
        if math.dist(a, b) < 1e-9:  # pragma: no mutate: a repeated vertex is 0 m long, never 1e-9
            continue
        if (b[0] - a[0]) * (c.y - a[1]) - (b[1] - a[1]) * (c.x - a[0]) <= 0:
            return None
    return c.x, c.y


def _pyramidal(polygon: Polygon, eave: float, spec: PitchedRoof) -> tuple[Mesh, Mesh, float, float] | str:
    centre = _star_centre(polygon)
    if centre is None:
        return "centroid_does_not_see_every_eave"
    inradius = polygon.exterior.distance(polygon.centroid)
    rise = _rise(spec, inradius)
    if rise < MIN_RISE_M:
        return "rise_too_small"
    roof = _fan(_ring(polygon), centre, eave, [(1.0, 0.0)], rise)
    return roof, Mesh.empty(), rise, inradius


def _dome(polygon: Polygon, eave: float, spec: PitchedRoof) -> tuple[Mesh, Mesh, float, float] | str:
    centre = _star_centre(polygon)
    if centre is None:
        return "centroid_does_not_see_every_eave"
    inradius = polygon.exterior.distance(polygon.centroid)
    rise = spec.rise_m if spec.rise_m is not None else min(MAX_DOME_RISE_M, inradius)
    if rise < MIN_RISE_M:
        return "rise_too_small"
    bands = [(math.cos(t), math.sin(t)) for t in (k * math.pi / (2 * (DOME_BANDS + 1)) for k in range(DOME_BANDS + 1))]
    roof = _fan(_ring(polygon), centre, eave, bands, rise)
    return roof, Mesh.empty(), rise, inradius


def _fan(ring: np.ndarray, centre: tuple[float, float], eave: float, bands: Sequence[tuple[float, float]], rise: float) -> Mesh:
    """Rings of ``ring`` scaled about ``centre`` by each ``(scale, lift)`` in ``bands``, closed by an apex.

    The first band must be ``(1, 0)``: the footprint at the eaves.
    """
    c = np.array(centre)
    vertices: list[tuple[float, float, float]] = []
    triangles: list[tuple[int, int, int]] = []
    n = len(ring)
    for scale, lift in bands:
        for p in ring:
            q = c + (p - c) * scale
            vertices.append((float(q[0]), float(q[1]), eave + rise * lift))
    for k in range(len(bands) - 1):
        lo, hi = k * n, (k + 1) * n
        for i in range(n):
            j = (i + 1) % n
            triangles += [(lo + i, lo + j, hi + j), (lo + i, hi + j, hi + i)]
    apex = len(vertices)
    vertices.append((float(c[0]), float(c[1]), eave + rise))
    top = (len(bands) - 1) * n
    triangles += [(top + i, top + (i + 1) % n, apex) for i in range(n)]
    return Mesh(np.array(vertices), np.array(triangles))


def _reach(polygon: Polygon) -> float:
    """Half the length of a cut through the footprint: its perimeter is longer than any chord."""
    return float(polygon.length)


def _centre(polygon: Polygon, u: np.ndarray, v: np.ndarray, mid: float) -> np.ndarray:
    """The point on the ridge line nearest the footprint's centroid."""
    c = np.array(polygon.centroid.coords[0])
    return u * float(c @ u) + v * mid


def _field_roof(polygon: Polygon, eave: float, z_of: Callable[[float, float], float], *lines: LineString) -> tuple[Mesh, Mesh]:
    """Cut ``polygon`` along ``lines``, lift each flat piece to ``eave + z_of`` and close the walls under it."""

    def lift(x: float, y: float) -> float:
        z = z_of(x, y)
        return eave if z < EAVE_SNAP_M else eave + z  # pragma: no mutate: a field value of exactly 1e-6 m is a float coincidence

    ring = _ring(polygon)
    snapped = [_snap(p, ring) for p in split(polygon, MultiLineString(list(lines))).geoms]
    meshes: list[Mesh] = []
    tops: dict[tuple[float, float], float] = {}
    for piece in [p for p in snapped if p is not None]:
        flat = cap(piece, 0.0)  # pragma: no mutate: only x and y of the flat triangulation are kept
        z = np.array([lift(float(x), float(y)) for x, y, _ in flat.vertices])
        meshes.append(Mesh(np.column_stack([flat.vertices[:, :2], z]), flat.triangles))
        for x, y in piece.exterior.coords:
            tops[(x, y)] = lift(x, y)
    return merge(meshes), _gables(ring, eave, tops)


def _snap(piece: BaseGeometry, ring: np.ndarray) -> Polygon | None:
    """``piece`` with every vertex within ``ON_EDGE_M`` of a footprint vertex moved onto it.

    A cut through a footprint corner can come back a few 1e-16 m off it, and a second vertex that
    close would leave a sliver seam. ``None`` for a piece that is nothing once snapped.
    """
    moved: list[tuple[float, float]] = []
    for x, y in piece.exterior.coords[:-1]:
        gap = np.hypot(ring[:, 0] - x, ring[:, 1] - y)
        k = int(np.argmin(gap))
        moved.append((float(ring[k, 0]), float(ring[k, 1])) if gap[k] < ON_EDGE_M else (x, y))  # pragma: no mutate: a gap of exactly 1e-7 m is a float coincidence
    # Drop a vertex equal to the one before it, the last compared with the first.
    coords = [p for k, p in enumerate(moved) if p != moved[k - 1]]
    if len(coords) < 3:
        return None
    snapped = Polygon(coords)
    return snapped if snapped.area > MIN_PIECE_M2 else None  # pragma: no mutate: an area of exactly 1e-9 m² is a float coincidence


def _gables(ring: np.ndarray, eave: float, tops: Mapping[tuple[float, float], float]) -> Mesh:
    """Vertical pieces from the eaves up to the roof edge, one per wall edge the roof leaves above the eaves.

    The roof edge over a wall edge runs through every roof vertex on it (a ridge crossing), so each piece
    is a convex polygon: the wall edge at the eaves, then the roof edge back. Its normal points out.
    """
    points = np.array(list(tops))
    heights = np.array(list(tops.values()))
    vertices: list[tuple[float, float, float]] = []
    triangles: list[tuple[int, int, int]] = []
    for n, a in enumerate(ring):
        b = ring[(n + 1) % len(ring)]
        ab = b - a
        length2 = float(ab @ ab)
        if length2 == 0.0:  # pragma: no mutate: a repeated vertex would only divide by zero; nothing lies on it either way
            continue
        rel = points - a
        t = rel @ ab / length2
        off = np.abs(rel[:, 0] * ab[1] - rel[:, 1] * ab[0]) / math.sqrt(length2)
        on = (off < ON_EDGE_M) & (t > -1e-9) & (t < 1 + 1e-9)  # pragma: no mutate: < or <= differ only at the tolerances, a float coincidence
        chain = sorted((float(t[k]), (float(points[k, 0]), float(points[k, 1])), float(heights[k])) for k in np.flatnonzero(on))
        outline = [(float(a[0]), float(a[1]), eave), (float(b[0]), float(b[1]), eave)]
        outline += [(p[0], p[1], z) for _, p, z in reversed(chain) if z > eave]
        if len(outline) < 3:
            continue
        i = len(vertices)
        vertices += outline
        triangles += [(i, i + k, i + k + 1) for k in range(1, len(outline) - 1)]
    if not triangles:
        return Mesh.empty()
    return Mesh(np.array(vertices), np.array(triangles))
