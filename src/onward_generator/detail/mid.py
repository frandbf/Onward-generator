"""The intermediate HLOD ring between the playable core and the scenic ring (plan D3, ADR-017 amendment).

A ``mid`` building is its footprint extruded to its eave, in its scenic wall
material, with flat painted windows on its exposed walls and the roof form the
core would draw (flat, gabled, hipped, the estimated hip or the nDSM gable). It
has no doors, balconies, parapets, towers, interiors, shops or collider. A
painted window is an ADR-025 flat opening: a rectangular hole in the wall and
one ``glass_dark`` quad that fills it, so the shell stays closed and the trim
atlas puts the quad on one of its window cells.

Windows sit in fixed bays and are added a whole row (one wall, one storey) at a
time, longest wall first, then the lower storey, then the wall's index, while the
building stays at or under MID_MAX_TRIANGLES visual triangles. No randomness.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from shapely.geometry.base import BaseGeometry

from onward_generator.detail.config import Template

# The roof under the core's hip rule (``build_shell``): a convex footprint gets the same hip in both tiers.
from onward_generator.detail.facades import WELD_M, Segment, Storeys, _hip_roof, face_with_openings, wall_frame
from onward_generator.detail.modules import flat_opening
from onward_generator.detail.roofs import BuiltRoof, build_roof
from onward_generator.generation.mesh import Mesh, cap, merge, open_edge_defects, polygons_of, quad, transform

MID_MAX_TRIANGLES = 150
MID_BAY_M = 3.0
MID_WINDOW_W_M = 1.2
MID_WINDOW_H_M = 1.4
MID_SILL_M = 0.9
# Wall left above a window head, below the next storey line and below the eave.
MID_HEAD_M = 0.3
# A fallback after the headset session: False draws no window, and the record keeps the same keys.
MID_WINDOWS_ENABLED = True
MID_GLASS = "glass_dark"
MID_REPRESENTATION = "mid_shell"
# A hole in a triangulated face adds 4 vertices and 1 hole: 6 more wall triangles, plus the 2 of its glass quad.
WINDOW_TRIANGLES = 8
WALL_TRIANGLES = 2
CAP_FALLBACK = "mid_triangle_cap"


@dataclass(frozen=True, slots=True)
class Row:
    """One row of painted windows: ``windows`` bays on wall ``segment`` at ``storey`` (0 is the ground storey)."""

    segment: int
    storey: int
    windows: int

    @property
    def triangles(self) -> int:
        return WINDOW_TRIANGLES * self.windows


@dataclass(frozen=True, slots=True)
class MidShell:
    """The visual parts of a mid building by material, its roof record, its window count, its exposed walls
    (``refine_exposure`` segments) and the open edges of the closed shell (0 when it is watertight)."""

    parts: dict[str, Mesh]
    roof: dict[str, object]
    windows: int
    exposed_faces: int
    defects: int

    @property
    def triangles(self) -> int:
        return sum(m.triangle_count for m in self.parts.values())


def bays(length: float) -> int:
    """How many MID_BAY_M bays a wall of ``length`` metres holds; one window per bay."""
    return int(length // MID_BAY_M)


def window_rows(segments: Sequence[Segment], storeys: Storeys, eave_m: float, base_triangles: int, *,
                cap_triangles: int = MID_MAX_TRIANGLES) -> list[Row]:
    """The window rows a mid building draws, with walls and roof already costing ``base_triangles``.

    Only exposed walls of at least one bay get windows, and only storeys whose window head stays MID_HEAD_M
    under both the storey line and the eave. Rows are ranked longer wall, lower storey, then wall index, and
    each is taken whole when it keeps the building at or under ``cap_triangles``; one that does not is skipped.
    """
    if not MID_WINDOWS_ENABLED:
        return []
    head = MID_SILL_M + MID_WINDOW_H_M + MID_HEAD_M
    ranked: list[tuple[float, int, int, Row]] = []
    for seg in segments:
        n = bays(seg.length)
        if not seg.exposed or n < 1:
            continue
        for storey in range(storeys.count):
            if storey * storeys.height_m + head > min((storey + 1) * storeys.height_m, eave_m):
                break
            ranked.append((-seg.length, storey, seg.index, Row(seg.index, storey, n)))
    chosen: list[Row] = []
    total = base_triangles
    for *_, row in sorted(ranked, key=lambda r: r[:3]):
        if total + row.triangles <= cap_triangles:
            chosen.append(row)
            total += row.triangles
    return chosen


def window_origins(seg: Segment, row: Row, storey_m: float) -> list[tuple[float, float]]:
    """The lower-left ``(s, v)`` of each window of ``row`` on ``seg``: centred in equal bays, MID_SILL_M above the storey floor."""
    bay = seg.length / row.windows
    v0 = row.storey * storey_m + MID_SILL_M
    return [((k + 0.5) * bay - MID_WINDOW_W_M / 2, v0) for k in range(row.windows)]


def face_with_windows(seg: Segment, z_top: float, origins: Sequence[tuple[float, float]]) -> tuple[Mesh, Mesh]:
    """One wall from 0 to ``z_top`` with a hole per window, and the flat glass quads that fill the holes.

    The wall is the core's face with openings (``face_with_openings``, outward winding) and each quad the core's
    flat opening (ADR-025) at the same ``(s, v)``, so every hole edge is shared with its glass.
    """
    if not origins:
        a0, b0, b1, a1 = (tuple(seg.point(s, v)) for s, v in ((0.0, 0.0), (seg.length, 0.0), (seg.length, z_top), (0.0, z_top)))
        return quad(a0, b0, b1, a1), Mesh.empty()
    rects = [(s0, v0, s0 + MID_WINDOW_W_M, v0 + MID_WINDOW_H_M) for s0, v0 in origins]
    wall = transform(cap(face_with_openings(seg.length, 0.0, z_top, rects), 0.0), wall_frame(seg))
    ((_, pane),) = flat_opening(MID_WINDOW_W_M, MID_WINDOW_H_M, MID_GLASS).parts
    glass = merge([transform(pane, seg.module_matrix(s0, v0)) for s0, v0 in origins])
    return wall, glass


def mid_roof(footprint: BaseGeometry, eave_m: float, template: Template, roof_material: str,
             wall_material: str) -> tuple[list[tuple[str, Mesh]], dict[str, object]]:
    """The roof the core would draw on ``footprint``, without a parapet: a pitched roof (its gables in the wall
    material), the hip, or a flat cap in ``roof_material``. A pitched roof that cannot be drawn keeps the flat
    cap and its reason as ``fallback``, as ``build_shell`` does."""
    polygons = polygons_of(footprint)
    fallback: str | None = None
    if template.pitched is not None:
        built = build_roof(polygons[0], eave_m, template.pitched) if len(polygons) == 1 else "footprint_not_a_simple_polygon"
        if isinstance(built, BuiltRoof):
            material = template.pitched.material
            parts = [(material, built.roof)] + ([(wall_material, built.gables)] if built.gables.triangle_count else [])
            return parts, {**built.record, "material": material}
        fallback = built
    elif template.hip is not None and template.parapet_m == 0 and len(polygons) == 1:
        hip = _hip_roof(polygons[0], eave_m, template)
        if hip is not None:
            mesh, rise = hip
            return [(template.hip.material, mesh)], {"type": "hip", "rise_m": round(rise, 3), "pitch_deg": template.hip.pitch_deg,
                                                     "material": template.hip.material}
    return flat_roof(footprint, eave_m, roof_material, fallback)


def flat_roof(footprint: BaseGeometry, eave_m: float, material: str, fallback: str | None) -> tuple[list[tuple[str, Mesh]], dict[str, object]]:
    """A flat cap at the eave, no parapet; ``fallback`` names why a pitched roof was not drawn."""
    record: dict[str, object] = {"type": "flat", "material": material}
    if fallback is not None:
        record["fallback"] = fallback
    return [(material, cap(footprint, eave_m))], record


def build_mid_shell(footprint: BaseGeometry, segments: Sequence[Segment], storeys: Storeys, eave_m: float, template: Template,
                    wall_material: str, roof_material: str, *, windows: bool = True) -> MidShell:
    """The mid shell of one building: walls on ``segments`` up to ``eave_m``, its roof and its window rows.

    A roof that would take walls and roof over MID_MAX_TRIANGLES is drawn flat (``fallback: mid_triangle_cap``).
    ``windows`` False (a landmark: its single band is not a storey) draws none.
    """
    walls_cost = WALL_TRIANGLES * len(segments)
    roof_parts, roof = mid_roof(footprint, eave_m, template, roof_material, wall_material)
    if walls_cost + sum(m.triangle_count for _, m in roof_parts) > MID_MAX_TRIANGLES and roof["type"] != "flat":
        roof_parts, roof = flat_roof(footprint, eave_m, roof_material, CAP_FALLBACK)
    base = walls_cost + sum(m.triangle_count for _, m in roof_parts)
    rows = window_rows(segments, storeys, eave_m, base) if windows else []
    segment_of = {seg.index: seg for seg in segments}
    by_segment: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for row in rows:
        by_segment[row.segment].extend(window_origins(segment_of[row.segment], row, storeys.height_m))
    parts: dict[str, list[Mesh]] = defaultdict(list)
    for seg in segments:
        wall, glass = face_with_windows(seg, eave_m, by_segment.get(seg.index, []))
        parts[wall_material].append(wall)
        if glass.triangle_count:
            parts[MID_GLASS].append(glass)
    for material, mesh in roof_parts:
        parts[material].append(mesh)
    merged = {material: merge(meshes) for material, meshes in sorted(parts.items())}
    defects = open_edge_defects(merge(list(merged.values())), z_base=0.0, allowed=[], quantum=WELD_M)
    return MidShell(merged, roof, sum(r.windows for r in rows), sum(seg.exposed for seg in segments), defects)
