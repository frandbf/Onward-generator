"""Generic facade templates on validated phase 03 footprints.

Facades are *generic templates*: rule-based bays of windows, doors and balconies
on street-facing walls. They do not represent the real facade of any building.

Coordinates are local metres (x east, y north, z up). A facade segment has
direction ``u`` and outward normal ``n = (u_y, −u_x)`` (exterior rings CCW,
holes CW, so the building is always to the left).
"""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from dataclasses import dataclass, field
from itertools import groupby, pairwise

import numpy as np
from shapely import LineString, MultiPolygon, Polygon, union_all
from shapely import box as rect_uv
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient

from onward_generator.detail.config import PARTY_PREFIX, DetailConfig, Opening, Template
from onward_generator.detail.modules import Prototype, balcony, frame_matrix, recess, recess_rim
from onward_generator.detail.roofs import BuiltRoof, build_roof
from onward_generator.generation.mesh import Mesh, cap, merge, open_edge_defects, polygons_of, prism, quad, transform, upward_area, walls

SNAP_M = 0.05
# A neighbour footprint within this gap outside a party wall hides the wall up to the lower of the two tops.
HIDDEN_GAP_M = 0.05
WELD_M = 1e-6  # vertex identity for watertightness checks (modules and faces agree to ~1e-14 m)
UP = np.array([0.0, 0.0, 1.0])


def stable_unit(seed: int, *parts: object) -> float:
    """Deterministic value in [0, 1) from the seed and a key (independent of iteration order)."""
    digest = hashlib.sha256(":".join([str(seed), *map(str, parts)]).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def stable_choice(seed: int, options: tuple[str, ...], *parts: object) -> str:
    return options[int(stable_unit(seed, *parts) * len(options))]


@dataclass(frozen=True, slots=True)
class Segment:
    part: int
    ring: int  # 0 exterior, ≥1 courtyard
    index: int
    a: tuple[float, float]
    b: tuple[float, float]
    exposed: bool

    @property
    def length(self) -> float:
        return math.dist(self.a, self.b)

    @property
    def u(self) -> np.ndarray:
        d = np.subtract(self.b, self.a)
        return np.array([d[0], d[1], 0.0]) / self.length

    @property
    def n_out(self) -> np.ndarray:
        u = self.u
        return np.array([u[1], -u[0], 0.0])

    def point(self, s: float, v: float = 0.0, depth_in: float = 0.0) -> np.ndarray:
        return np.array([self.a[0], self.a[1], 0.0]) + s * self.u + v * UP - depth_in * self.n_out

    def module_matrix(self, s: float, v: float) -> np.ndarray:
        return frame_matrix(self.point(s, v), self.u, -self.n_out)

    def plan_rect(self, s0: float, s1: float, out0: float, out1: float) -> Polygon:
        """Plan rectangle between ``s0…s1`` along the segment and ``out0…out1`` along the outward normal."""
        pts = [self.point(s0, 0, -out0), self.point(s1, 0, -out0), self.point(s1, 0, -out1), self.point(s0, 0, -out1)]
        return Polygon([(p[0], p[1]) for p in pts])


def _merge_intervals(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for lo, hi in sorted(intervals):
        if out and lo <= out[-1][1] + 1e-9:
            out[-1] = (out[-1][0], max(out[-1][1], hi))
        else:
            out.append((lo, hi))
    return out


def exposure_probe(seg: Segment, cfg: DetailConfig) -> Polygon:
    """The strip ``probe_m`` deep outside a segment, ``SNAP_M`` short of each end (a quarter of a short segment)."""
    inset = min(SNAP_M, seg.length / 4)
    return seg.plan_rect(inset, seg.length - inset, 0.0, cfg.probe_m)


def refine_exposure(geometry: BaseGeometry, others: BaseGeometry, cfg: DetailConfig) -> tuple[BaseGeometry, list[Segment]]:
    """Split ring edges where street exposure changes; return the refined footprint and its segments.

    An edge portion is a *party wall* when another footprint lies within ``probe_m``
    outside it. Exposed portions shorter than ``min_facade_length_m`` stay party/blank.
    The refined footprint only adds collinear vertices, so its area is unchanged.
    """
    parts: list[Polygon] = []
    segments: list[Segment] = []
    for pi, polygon in enumerate(sorted(polygons_of(geometry), key=lambda p: (-p.area, p.bounds))):
        polygon = orient(polygon, sign=1.0)
        new_rings: list[list[tuple[float, float]]] = []
        for ri, ring in enumerate((polygon.exterior, *polygon.interiors)):
            coords = list(ring.coords)
            refined: list[tuple[float, float]] = []
            for ei, (a, b) in enumerate(zip(coords[:-1], coords[1:], strict=True)):
                length = math.dist(a, b)
                refined.append(a)
                if length < 1e-6:
                    continue
                exposed_ranges: list[tuple[float, float]] = []
                if ri == 0 or cfg.courtyard_openings:
                    seg = Segment(pi, ri, ei, a, b, True)
                    probe = exposure_probe(seg, cfg)
                    covered: list[tuple[float, float]] = []
                    hit = probe.intersection(others) if not others.is_empty else Polygon()
                    for piece in polygons_of(hit):
                        if piece.area < 1e-4:
                            continue
                        proj = [(np.array([x, y, 0.0]) - seg.point(0)) @ seg.u for x, y in piece.exterior.coords]
                        covered.append((max(0.0, min(proj)), min(length, max(proj))))
                    covered = _merge_intervals(covered)
                    cursor = 0.0
                    for lo, hi in [*covered, (length, length)]:
                        if lo - cursor >= cfg.min_facade_length_m:
                            exposed_ranges.append((cursor, lo))
                        cursor = max(cursor, hi)
                    exposed_ranges = [(0.0 if lo < SNAP_M else lo, length if length - hi < SNAP_M else hi) for lo, hi in exposed_ranges]
                breaks = sorted({t for r in exposed_ranges for t in r if SNAP_M < t < length - SNAP_M})
                ax, ay = a
                d = ((b[0] - ax) / length, (b[1] - ay) / length)
                pts = [a, *[(ax + d[0] * t, ay + d[1] * t) for t in breaks], b]
                ts = [0.0, *breaks, length]
                for k in range(len(pts) - 1):
                    if k > 0:
                        refined.append(pts[k])
                    mid = (ts[k] + ts[k + 1]) / 2
                    exposed = any(lo <= mid <= hi for lo, hi in exposed_ranges)
                    segments.append(Segment(pi, ri, len(segments), pts[k], pts[k + 1], exposed))
            refined.append(refined[0])
            new_rings.append(refined)
        parts.append(Polygon(new_rings[0], new_rings[1:]))
    refined_geom = parts[0] if len(parts) == 1 else union_all(parts)
    return refined_geom, segments


def setback_floor(seg: Segment, neighbours: list[tuple[BaseGeometry, float | None]], cfg: DetailConfig) -> float | None:
    """The height above which a party wall is an outer wall of its own building, or ``None``.

    ``neighbours`` are ``(footprint, roof)``, with ``roof`` the roof height of a sub-building of the
    same parcel (ADR-016) and ``None`` for any other building. The probe is the one ``refine_exposure``
    uses. When every footprint it meets is of the same parcel, the wall is that building's
    facade above the highest of their roofs: a setback storey over a terrace, or a taller rear
    wing. The terrace parapet may hide a sill, as on a real setback. A wall that meets another
    parcel, or nothing, stays a blank party wall.
    """
    probe = exposure_probe(seg, cfg)
    roofs: list[float] = []
    for footprint, roof in neighbours:
        if probe.intersection(footprint).area < 1e-4:  # pragma: no mutate: refine_exposure's corner-touch threshold; a contact of exactly 1e-4 m² is measure zero
            continue
        if roof is None:
            return None
        roofs.append(roof)
    return max(roofs, default=None)


def setback_floors(segments: list[Segment], neighbours: list[tuple[BaseGeometry, float | None]], height: float,
                   cfg: DetailConfig) -> dict[int, float]:
    """``setback_floor`` of every party wall that opens below ``height``, by segment index."""
    floors = {seg.index: setback_floor(seg, neighbours, cfg) for seg in segments if not seg.exposed}
    return {index: floor for index, floor in floors.items() if floor is not None and floor < height}


@dataclass(frozen=True, slots=True)
class Hole:
    kind: str  # window | narrow_window | balcony_door | door | landmark_door | shop_door | shopfront | interior_door
    segment: int
    s0: float
    v0: float
    width: float
    height: float
    depth: float
    back: str | None
    column: int
    storey: int
    balcony: bool = False

    def uv_rect(self) -> Polygon:
        return rect_uv(self.s0, self.v0, self.s0 + self.width, self.v0 + self.height)


# A 2.4 m door plus the 0.35 m lintel needs 2.75 m. Interior storeys are at least 2.8 m.
INTERIOR_MIN_STOREY_M = 2.8
# OSM `height`, a Catastro height override, and Catastro levels stay as recorded.
MEASURED_HEIGHT_SOURCES = frozenset({"explicit_height", "override", "authoritative_levels"})


@dataclass(slots=True)
class Storeys:
    count: int
    height_m: float
    origin: str  # heights_levels | recomputed | single_band | interior_storey


def storeys_for(height: float, levels: int | None, landmark: bool, cfg: DetailConfig) -> Storeys:
    if landmark:
        return Storeys(1, height, "single_band")
    if levels and levels >= 1 and cfg.storey_min_m <= height / levels <= cfg.storey_max_m:
        return Storeys(int(levels), height / levels, "heights_levels")
    n = max(1, round(height / cfg.storey_fallback_m))
    while n > 1 and height / n < cfg.storey_min_m:
        n -= 1
    return Storeys(n, height / n, "recomputed")


def interior_storey(height: float, storeys: Storeys, height_source: str | None) -> tuple[float, Storeys] | None:
    """Raise an estimated shell so each storey is at least 2.8 m.

    The storey count stays. A measured height is left unchanged. ``None`` means
    the current storey already fits, or the height must not be edited.
    """
    if storeys.origin == "single_band" or storeys.count < 1:
        return None
    if height_source in MEASURED_HEIGHT_SOURCES:
        return None
    if storeys.height_m + 1e-9 >= INTERIOR_MIN_STOREY_M:
        return None
    return (
        storeys.count * INTERIOR_MIN_STOREY_M,
        Storeys(storeys.count, INTERIOR_MIN_STOREY_M, "interior_storey"),
    )


def exposed_runs(segments: list[Segment]) -> list[list[Segment]]:
    """Chains of exposed segments in ring order. A party wall ends a chain.

    A ring with no party wall is one chain starting at its first segment. A chain
    that runs over the ring start continues from the last segment to the first.
    """
    rings: dict[tuple[int, int], list[Segment]] = defaultdict(list)
    for seg in segments:
        rings[(seg.part, seg.ring)].append(seg)
    runs: list[list[Segment]] = []
    for key in sorted(rings):
        ring = rings[key]
        start = next((i for i, s in enumerate(ring) if not s.exposed), 0)
        current: list[Segment] = []
        for seg in ring[start:] + ring[:start]:
            if seg.exposed:
                current.append(seg)
            elif current:
                runs.append(current)
                current = []
        if current:
            runs.append(current)
    return runs


def wall_uv_frame(segments: list[Segment], storeys: Storeys, levels: int | None) -> dict[str, object]:
    """Node extras that align wall UVs to exposed runs and storeys (ADR-012).

    Without levels, or on a single band, ``storey_h`` is ``None`` and V keeps ``tile_m``.
    """
    aligned = bool(levels) and storeys.origin != "single_band"
    return {
        "runs": [[[round(c, 6) for c in (*s.a, *s.b)] for s in run] for run in exposed_runs(segments)],
        "storey_h": storeys.height_m if aligned else None,
        "storeys_origin": storeys.origin if aligned else None,
    }


def _fits(opening: Opening, bay_w: float, storey_h: float, cfg: DetailConfig) -> bool:
    return opening.width_m + cfg.min_pier_m <= bay_w + 1e-9 and opening.sill_m + opening.height_m + cfg.head_clearance_m <= storey_h + 1e-9


def shopfront_opening(bay_w: float, storey_h: float, cfg: DetailConfig) -> Opening | None:
    """The display window of one shop bay: the bay less a pier, the storey less sill and head, within the limits."""
    s = cfg.shopfront
    width = min(s.max_width_m, bay_w - cfg.min_pier_m)
    height = min(s.max_height_m, storey_h - s.sill_m - s.head_m)
    if width < s.min_width_m or height < s.min_height_m:
        return None
    return Opening(width, height, s.sill_m)


# The back panel of each entrance. A shop door is glazed; ``interior_door`` has no back.
DOOR_BACK = {"door": "door_wood", "landmark_door": "door_wood", "shop_door": "glass_dark", "interior_door": None}


def entrance(landmark: bool, shop: bool, cfg: DetailConfig) -> tuple[str, Opening]:
    """The main door kind and size: a landmark door, a glazed shop door on a shopfront, or a plain door."""
    if landmark:
        return "landmark_door", cfg.landmark_door
    if shop:
        return "shop_door", cfg.shop_door
    return "door", cfg.main_door


@dataclass(slots=True)
class Layout:
    holes: list[Hole] = field(default_factory=list)
    balconies: list[tuple[int, float, float]] = field(default_factory=list)  # (segment, s0, v0)
    skipped: dict[str, int] = field(default_factory=lambda: defaultdict(int))


def layout_segment(
    seg: Segment,
    storeys: Storeys,
    template: Template,
    ground: str,
    main_door: Opening | None,
    door_kind: str,
    door_depth: float,
    building_id: str,
    footprint: BaseGeometry,
    others: BaseGeometry,
    cfg: DetailConfig,
    layout: Layout,
    floor_m: float = 0.0,
) -> None:
    """Lay the template's bays on one wall. With ``floor_m`` (a setback wall, ``setback_floor``),
    a storey whose top is not above it is skipped and no opening starts below it."""
    length = seg.length
    usable = length - 2 * cfg.edge_margin_m
    if usable < template.narrow_window.width_m + cfg.min_pier_m:
        layout.skipped["segment_too_short"] += 1
        return
    bays = max(1, math.floor(usable / template.bay_m))
    bay_w = usable / bays
    centres = [cfg.edge_margin_m + bay_w * (j + 0.5) for j in range(bays)]
    main_bay = bays // 2 if main_door is not None else -1
    shop = shopfront_opening(bay_w, storeys.height_m, cfg)
    d = cfg.recess_depth_m

    def add(kind: str, centre: float, v0: float, o: Opening, back: str | None, column: int, storey: int, depth: float = d) -> bool:
        if v0 < floor_m:
            return False
        s0 = centre - o.width_m / 2
        plan = seg.plan_rect(s0, s0 + o.width_m, -depth, 0.0)
        if not footprint.buffer(1e-6).contains(plan):
            layout.skipped["recess_outside_footprint"] += 1
            return False
        layout.holes.append(Hole(kind, seg.index, s0, v0, o.width_m, o.height_m, depth, back, column, storey))
        return True

    def window(centre: float, z0: float, column: int, storey: int) -> None:
        for kind, o in (("window", template.window), ("narrow_window", template.narrow_window)):
            if _fits(o, bay_w, storeys.height_m, cfg):
                add(kind, centre, z0 + o.sill_m, Opening(o.width_m, o.height_m), "glass_dark", column, storey)
                return
        layout.skipped["window_does_not_fit"] += 1

    for i in range(storeys.count):
        z0 = i * storeys.height_m
        if z0 + storeys.height_m <= floor_m:
            continue
        for j, c in enumerate(centres):
            if i == 0:
                if j == main_bay and main_door is not None:
                    if _fits(main_door, bay_w, storeys.height_m, cfg):
                        add(door_kind, c, 0.0, main_door, DOOR_BACK[door_kind], j, 0, depth=door_depth)
                    else:
                        layout.skipped["door_does_not_fit"] += 1
                elif ground == "shopfront" and shop is not None:
                    add("shopfront", c, shop.sill_m, shop, "glass_dark", j, 0)
                elif ground != "main_door_only":
                    window(c, z0, j, i)
                continue
            b = template.balcony
            if b is not None and stable_unit(cfg.seed, building_id, "balcony", seg.index, j) < b.share:
                door = b.door
                fits = door.width_m + cfg.min_pier_m <= bay_w and door.height_m + cfg.head_clearance_m <= storeys.height_m
                bs0 = c - b.width_m / 2
                slab = seg.plan_rect(bs0, bs0 + b.width_m, 0.0, b.depth_m)
                inside = bs0 >= SNAP_M and bs0 + b.width_m <= length - SNAP_M
                if fits and inside and not others.intersects(slab.buffer(-0.01)):
                    if add("balcony_door", c, z0, door, "glass_dark", j, i):
                        layout.balconies.append((seg.index, bs0, z0))
                        continue
                else:
                    layout.skipped["balcony_not_placed"] += 1
            window(c, z0, j, i)


def face_with_openings(length: float, z0: float, z1: float, rects: list[tuple[float, float, float, float]]) -> Polygon:
    """Wall face [0, length] × [z0, z1] with disjoint openings ``(s0, v0, s1, v1)``.

    Built from exact coordinates (no overlay), so opening corners coincide bit-for-bit
    with the recess modules placed at the same (s, v). Openings starting at ``z0``
    become notches in the bottom edge; the others become holes.
    """
    notches = sorted(r for r in rects if abs(r[1] - z0) < 1e-9)
    holes = [r for r in rects if abs(r[1] - z0) >= 1e-9]
    ring: list[tuple[float, float]] = [(0.0, z0)]
    for s0, _, s1, v1 in notches:
        ring += [(s0, z0), (s0, v1), (s1, v1), (s1, z0)]
    ring += [(length, z0), (length, z1), (0.0, z1)]
    interiors = [[(s0, v0), (s0, v1), (s1, v1), (s1, v0)] for s0, v0, s1, v1 in sorted(holes)]
    return Polygon(ring, interiors)


def parapet_strip(refined: BaseGeometry, inner: BaseGeometry) -> BaseGeometry | None:
    """Exact ``refined − inner`` for an inward offset: outer rings with inner rings as holes, plus courtyard bands."""
    pieces: list[Polygon] = []
    inner_parts = polygons_of(inner)
    for part in polygons_of(refined):
        contained = [ip for ip in inner_parts if part.contains(ip.representative_point())]
        pieces.append(Polygon(part.exterior.coords, [ip.exterior.coords for ip in contained]))
        for hole in part.interiors:
            hole_poly = Polygon(hole)
            grown = [Polygon(r) for ip in contained for r in ip.interiors if Polygon(r).contains(hole_poly.representative_point())]
            if len(grown) != 1:
                return None
            pieces.append(Polygon(grown[0].exterior.coords, [hole.coords]))
    if any(not p.is_valid for p in pieces):
        return None
    if abs(sum(p.area for p in pieces) - (refined.area - inner.area)) > 1e-6 * max(1.0, refined.area):
        return None
    return pieces[0] if len(pieces) == 1 else MultiPolygon(pieces)


@dataclass(slots=True)
class Shell:
    parts: dict[str, list[Mesh]]
    roof: dict[str, object]
    z_top: float
    defects: int
    roof_area_error: float
    rims: list[tuple[tuple[float, float, float], tuple[float, float, float]]]
    balcony_colliders: list[Mesh]
    counts: dict[str, int]
    roof_deck: BaseGeometry | None = None
    roof_z: float | None = None
    parapet_collider: Mesh | None = None
    # Party walls with no opening: segment and its quad in ``parts[wall_material]``, for :func:`split_blank_walls`.
    blank_walls: list[tuple[Segment, Mesh]] = field(default_factory=list)
    wall_material: str = ""
    # Party walls with openings (a setback wall, a fallback door): segment, its face, and the openings ``(s0, v0, s1, v1)``.
    opened_walls: list[tuple[Segment, Mesh, list[tuple[float, float, float, float]]]] = field(default_factory=list)


def _hip_roof(polygon: Polygon, height: float, template: Template) -> tuple[Mesh, float] | None:
    hip = template.hip
    if hip is None or polygon.interiors or polygon.area > hip.max_area_m2:
        return None
    if polygon.area < 0.995 * polygon.convex_hull.area:
        return None
    apex2 = polygon.centroid
    inradius = polygon.exterior.distance(apex2)
    rise = min(hip.max_rise_m, math.tan(math.radians(hip.pitch_deg)) * inradius)
    if rise <= 0.1:
        return None
    apex = (apex2.x, apex2.y, height + rise)
    coords = list(orient(polygon, sign=1.0).exterior.coords)
    vertices, triangles = [], []
    for (ax, ay), (bx, by) in zip(coords[:-1], coords[1:], strict=True):
        if math.dist((ax, ay), (bx, by)) < 1e-6:
            continue
        if (bx - ax) * (apex2.y - ay) - (by - ay) * (apex2.x - ax) <= 0:
            # A reflex jog the apex cannot see (a few centimetres where a Catastro
            # split meets the street) would fold its face under the roof: keep it flat.
            return None
        i = len(vertices)
        vertices += [(ax, ay, height), (bx, by, height), apex]
        triangles.append((i, i + 1, i + 2))
    return Mesh(np.array(vertices, dtype=np.float64), np.array(triangles, dtype=np.int64)), rise


def wall_frame(seg: Segment) -> np.ndarray:
    """Wall face coordinates ``(s, v, 0)`` to the world: along the segment, up, and out."""
    to_world = np.eye(4)  # pragma: no mutate: transform reads only the top 3x4 block, which every size above 3 fills alike
    to_world[:3, 0], to_world[:3, 1], to_world[:3, 2], to_world[:3, 3] = seg.u, UP, seg.n_out, seg.point(0)
    return to_world


def _bake(parts: dict[str, list[Mesh]], proto: Prototype, matrix: np.ndarray) -> None:
    for material, mesh in proto.parts:
        parts[material].append(transform(mesh, matrix))


def build_shell(
    refined: BaseGeometry,
    segments: list[Segment],
    layout: Layout,
    height: float,
    template: Template,
    wall_material: str,
    roof_material: str,
    building_id: str,
    cfg: DetailConfig,
) -> Shell:
    parts: dict[str, list[Mesh]] = defaultdict(list)
    holes_by_seg: dict[int, list[Hole]] = defaultdict(list)
    for h in layout.holes:
        holes_by_seg[h.segment].append(h)

    polygons = polygons_of(refined)
    roof_info: dict[str, object] = {}
    parapet = template.parapet_m
    hip = _hip_roof(polygons[0], height, template) if len(polygons) == 1 and parapet == 0 else None
    pitched: BuiltRoof | str | None = None
    if template.pitched is not None:
        pitched = build_roof(polygons[0], height, template.pitched) if len(polygons) == 1 else "footprint_not_a_simple_polygon"
    inner: BaseGeometry | None = None
    if parapet > 0:
        inner = refined.buffer(-template.parapet_thickness_m, join_style="mitre", mitre_limit=2.0)
        strip = None
        if not inner.is_empty and inner.is_valid and inner.area >= 0.25 * refined.area:
            strip = parapet_strip(refined, inner)
        if strip is None:
            inner = None
    z_top = height + parapet if inner is not None else height
    roof_deck: BaseGeometry | None = None
    parapet_collider: Mesh | None = None

    roof_area = 0.0
    if inner is not None:
        assert strip is not None
        parts[roof_material].append(cap(inner, height))
        parts[wall_material].append(walls(inner, height, z_top, inward=True))
        parts["frame_stone"].append(cap(strip, z_top))
        roof_area = upward_area(cap(inner, height)) + upward_area(cap(strip, z_top))
        roof_deck = inner
        if z_top > height + 1e-6:
            parapet_collider = prism(strip, height, z_top, bottom=True)
        roof_info = {"type": "flat_parapet", "parapet_m": parapet, "parapet_thickness_m": template.parapet_thickness_m}
    elif isinstance(pitched, BuiltRoof):
        assert template.pitched is not None
        roof_material = template.pitched.material
        parts[roof_material].append(pitched.roof)
        if pitched.gables.triangle_count:
            parts[wall_material].append(pitched.gables)
        roof_area = upward_area(pitched.roof)
        roof_info = dict(pitched.record)
    elif hip is not None:
        mesh, rise = hip
        material = template.hip.material if template.hip else roof_material
        parts[material].append(mesh)
        roof_area = upward_area(mesh)
        roof_material = material
        roof_info = {"type": "hip", "rise_m": round(rise, 3), "pitch_deg": template.hip.pitch_deg if template.hip else None}
    else:
        roof_mesh = cap(refined, height)
        parts[roof_material].append(roof_mesh)
        roof_area = upward_area(roof_mesh)
        roof_deck = refined
        roof_info = {"type": "flat"}
        if isinstance(pitched, str):
            roof_info["fallback"] = pitched
    roof_info["material"] = roof_material

    rims: list[tuple[tuple[float, float, float], tuple[float, float, float]]] = []
    counts: dict[str, int] = defaultdict(int)
    blank_walls: list[tuple[Segment, Mesh]] = []
    opened_walls: list[tuple[Segment, Mesh, list[tuple[float, float, float, float]]]] = []
    for seg in segments:
        holes = holes_by_seg.get(seg.index, [])
        a0, b0, b1, a1 = seg.point(0, 0), seg.point(seg.length, 0), seg.point(seg.length, z_top), seg.point(0, z_top)
        if not holes:
            wall = quad(a0, b0, b1, a1)
            parts[wall_material].append(wall)
            if not seg.exposed:
                blank_walls.append((seg, wall))
            continue
        rects = [(h.s0, h.v0, h.s0 + h.width, h.v0 + h.height) for h in holes]
        face_mesh = transform(cap(face_with_openings(seg.length, 0.0, z_top, rects), 0.0), wall_frame(seg))
        parts[wall_material].append(face_mesh)
        if not seg.exposed:
            opened_walls.append((seg, face_mesh, rects))
        for h in holes:
            proto = recess(h.width, h.height, h.depth, h.back)
            m = seg.module_matrix(h.s0, h.v0)
            _bake(parts, proto, m)
            counts[h.kind] += 1
            if h.back is None:
                for p, q in recess_rim(h.width, h.height, h.depth, include_bottom=h.v0 > 0):
                    rims.append((tuple((m @ [*p, 1.0])[:3]), tuple((m @ [*q, 1.0])[:3])))  # type: ignore[arg-type]

    # The shell (walls, reveals, panels, roof) must be watertight except at the base and
    # through-door rims. Balconies are attached afterwards: they are open against the wall by design.
    defects = open_edge_defects(merge([m for meshes in parts.values() for m in meshes]), z_base=0.0, allowed=rims, quantum=WELD_M)

    balcony_colliders: list[Mesh] = []
    spec = template.balcony
    for seg_index, bs0, v0 in layout.balconies:
        assert spec is not None
        proto = balcony(spec.width_m, spec.depth_m, spec.slab_m, spec.railing_m)
        m = segments[seg_index].module_matrix(bs0, v0)
        _bake(parts, proto, m)
        if proto.collider is not None:
            balcony_colliders.append(transform(proto.collider, m))
        counts["balcony"] += 1

    footprint_area = sum(p.area for p in polygons)
    return Shell(
        dict(parts), roof_info, z_top, defects, abs(roof_area - footprint_area), rims, balcony_colliders, dict(counts),
        roof_deck, height, parapet_collider,
        blank_walls, wall_material, opened_walls,
    )


def cut_roof_deck(shell: Shell, well: BaseGeometry) -> None:
    """Open the flat roof deck where a stair arrives. The parapet stays."""
    deck = shell.roof_deck
    if deck is None or shell.roof_z is None or shell.roof.get("type") not in ("flat", "flat_parapet"):
        return
    holed = deck.difference(well)
    if holed.is_empty or not holed.is_valid:
        return
    material = str(shell.roof.get("material") or "")
    shell.parts[material] = [cap(holed, shell.roof_z)]


def party_material(wall_material: str) -> str:
    """The material of a blank party wall: the wall's own albedo, its own lightmap class (``docs/12-export.md``)."""
    return PARTY_PREFIX + wall_material


def party_base(material: str) -> str:
    """The wall material a party material copies; any other material is its own base."""
    return material.removeprefix(PARTY_PREFIX)


@dataclass(frozen=True, slots=True)
class WallAreas:
    """Gross wall area of one shell by class, in m²: openings are not subtracted."""

    street: float = 0.0  # exposed walls, and party walls with an opening
    party: float = 0.0  # blank walls that stay visible
    hidden: float = 0.0  # blank walls against a neighbour's wall, left out of the visual mesh

    def __add__(self, other: WallAreas) -> WallAreas:
        return WallAreas(self.street + other.street, self.party + other.party, self.hidden + other.hidden)

    def to_record(self) -> dict[str, float]:
        return {"street": round(self.street, 1), "party": round(self.party, 1), "hidden": round(self.hidden, 1)}


def wall_cover(seg: Segment, neighbours: list[tuple[BaseGeometry, float]]) -> list[tuple[float, float, float]]:
    """``(s0, s1, top)``: where a neighbour footprint lies within ``HIDDEN_GAP_M`` outside the segment, and its wall top."""
    probe = seg.plan_rect(0.0, seg.length, 0.0, HIDDEN_GAP_M)
    cover: list[tuple[float, float, float]] = []
    for footprint, top in neighbours:
        for piece in polygons_of(probe.intersection(footprint)):
            proj = [float(np.subtract((x, y), seg.a) @ seg.u[:2]) for x, y in piece.exterior.coords]
            lo, hi = max(0.0, min(proj)), min(seg.length, max(proj))
            if hi - lo >= SNAP_M:
                cover.append((0.0 if lo < SNAP_M else lo, seg.length if seg.length - hi < SNAP_M else hi, top))
    return cover


def cover_steps(length: float, z_top: float, cover: list[tuple[float, float, float]]) -> list[tuple[float, float, float]]:
    """The hidden height along a wall as steps ``(s0, s1, h)`` over ``[0, length]``, with ``0 ≤ h ≤ z_top``.

    A step shorter than ``SNAP_M`` between two higher ones takes the lower of the two,
    so a seam between two neighbours does not leave a sliver of wall.
    """
    cuts = sorted({0.0, length, *(t for s0, s1, _ in cover for t in (s0, s1))})
    steps: list[tuple[float, float, float]] = []
    for t0, t1 in pairwise(cuts):
        steps.append((t0, t1, min(z_top, max((top for s0, s1, top in cover if s0 <= t0 and t1 <= s1), default=0.0))))
    for k in range(1, len(steps) - 1):
        t0, t1, h = steps[k]
        if t1 - t0 < SNAP_M:
            steps[k] = (t0, t1, max(h, min(steps[k - 1][2], steps[k + 1][2])))
    merged: list[tuple[float, float, float]] = []
    for t0, t1, h in steps:
        if merged and merged[-1][2] == h:
            merged[-1] = (merged[-1][0], t1, h)
        else:
            merged.append((t0, t1, h))
    return merged


def visible_face(z_top: float, steps: list[tuple[float, float, float]]) -> list[Polygon]:
    """The wall above the hidden steps, from exact coordinates: one polygon per run of steps below ``z_top``."""
    faces: list[Polygon] = []
    for below, group in groupby(steps, key=lambda step: step[2] < z_top):
        run = list(group)
        if below:
            ring = [p for t0, t1, h in run for p in ((t0, h), (t1, h))]
            faces.append(Polygon([*ring, (run[-1][1], z_top), (run[0][0], z_top)]))
    return faces


def split_blank_walls(shell: Shell, neighbours: list[tuple[BaseGeometry, float]]) -> WallAreas:
    """Take the blank party walls out of ``parts[wall_material]``.

    The part against a neighbour's wall, up to the lower of the two tops, is never seen
    and is dropped; the rest goes to ``party_material(wall_material)``. A party wall with
    openings (a setback wall) loses the band under its lowest opening that a neighbour hides
    along its whole length, and keeps the rest in its wall material. The collider is not touched.
    Call it after the shell's open-edge check, which counts the full walls.
    """
    replaced = {id(mesh) for _, mesh in shell.blank_walls} | {id(mesh) for _, mesh, _ in shell.opened_walls}
    shell.parts[shell.wall_material] = [m for m in shell.parts[shell.wall_material] if id(m) not in replaced]
    hidden = party = 0.0
    for seg, _ in shell.blank_walls:
        steps = cover_steps(seg.length, shell.z_top, wall_cover(seg, neighbours))
        covered = sum((t1 - t0) * h for t0, t1, h in steps)
        hidden += covered
        party += seg.length * shell.z_top - covered
        for face in visible_face(shell.z_top, steps):
            shell.parts.setdefault(party_material(shell.wall_material), []).append(transform(cap(face, 0.0), wall_frame(seg)))
    for seg, _, rects in shell.opened_walls:
        steps = cover_steps(seg.length, shell.z_top, wall_cover(seg, neighbours))
        cut = min(min(h for _, _, h in steps), min(v0 for _, v0, _, _ in rects))
        hidden += seg.length * cut
        face = face_with_openings(seg.length, cut, shell.z_top, rects)
        shell.parts[shell.wall_material].append(transform(cap(face, 0.0), wall_frame(seg)))
    return WallAreas(0.0, party, hidden)


def exposed_frontage(segments: list[Segment]) -> Segment | None:
    exposed = [s for s in segments if s.exposed and s.ring == 0 and s.part == 0]
    return max(exposed, key=lambda s: (round(s.length, 6), -s.index)) if exposed else None


DOOR_KINDS = frozenset({"door", "landmark_door", "shop_door", "interior_door"})
# The smallest door the fallback cuts: a narrow wall or a low storey shrinks the template door to this.
MIN_DOOR_WIDTH_M = 0.9
MIN_DOOR_HEIGHT_M = 2.0


def door_walls(segments: list[Segment], others: BaseGeometry, cfg: DetailConfig) -> list[Segment]:
    """Outer walls that may take a door, best first.

    The street-facing walls, longest first (on a one-part building the first is the
    main frontage), then the outer walls too short to count as street-facing whose
    ``probe_m`` strip outside meets no neighbour. A party wall is never listed.
    """
    def free(seg: Segment) -> bool:
        return not others.intersects(seg.plan_rect(0.0, seg.length, 0.0, cfg.probe_m).buffer(-SNAP_M))

    outer = sorted((s for s in segments if s.ring == 0), key=lambda s: (-s.length, s.index))
    return [s for s in outer if s.exposed] + [s for s in outer if not s.exposed and free(s)]


def ensure_door(
    layout: Layout,
    walls_in_order: list[Segment],
    storeys: Storeys,
    door_kind: str,
    door: Opening,
    template: Template,
    footprint: BaseGeometry,
    cfg: DetailConfig,
) -> str:
    """Give a building without a door one on the first wall of ``walls_in_order`` that holds it.

    Returns ``template`` when the layout already has a door, ``fallback`` when one was
    added, and ``none`` when no wall holds the smallest door. The door sits in the
    centre bay, shrinks to the wall and the storey (not below ``MIN_DOOR_WIDTH_M`` ×
    ``MIN_DOOR_HEIGHT_M``), and replaces the openings of that wall within ``min_pier_m``
    of it. Upper storeys start above the door, so only ground-floor openings go.
    """
    if any(h.kind in DOOR_KINDS for h in layout.holes):
        return "template"
    height = min(door.height_m, storeys.height_m - cfg.head_clearance_m)
    if height < MIN_DOOR_HEIGHT_M:
        return "none"
    for seg in walls_in_order:
        usable = seg.length - 2 * cfg.edge_margin_m
        bays = max(1, math.floor(usable / template.bay_m))
        centre = cfg.edge_margin_m + usable / bays * (bays // 2 + 0.5)
        width = min(door.width_m, 2 * (min(centre, seg.length - centre) - cfg.min_pier_m))
        s0 = centre - width / 2
        if width >= MIN_DOOR_WIDTH_M and footprint.buffer(1e-6).contains(seg.plan_rect(s0, s0 + width, -cfg.recess_depth_m, 0.0)):
            hole = Hole(
                door_kind, seg.index, s0, 0.0, width, height, cfg.recess_depth_m, DOOR_BACK[door_kind],
                -1, 0,  # pragma: no mutate: nothing reads the column or the storey of a fallback door
            )
            clear = rect_uv(hole.s0 - cfg.min_pier_m, hole.v0, hole.s0 + hole.width + cfg.min_pier_m, hole.v0 + hole.height)
            layout.holes = [h for h in layout.holes if h.segment != seg.index or not clear.intersects(h.uv_rect())]
            layout.holes.append(hole)
            return "fallback"
    return "none"


def line_of(seg: Segment) -> LineString:
    return LineString([seg.a, seg.b])
