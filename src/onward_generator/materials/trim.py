"""Map facade parts of each building onto one trim-sheet atlas material (ADR-020).

Walls are cut at the zocalo line, at every storey line and at the foot of the
parapet, so each piece lies in one band and its V can stay inside one strip:
the zocalo, the strip of its wall colour, or the cornice. U runs along the
facade (the ``uv_frame`` runs of ADR-012, else the face plane) and repeats
across the full width of the atlas. Window and door backs fit 0–1 into a cell
of the fit row; reveals, sills, slabs and coping into the stone strip.

Every part of a building that the atlas covers becomes one primitive of one
material, so the adapter's (cell, material) combine draws all of them at once.
A wall colour the style has no strip for keeps its own material.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np
import PIL
from shapely import Polygon, box, constrained_delaunay_triangles, union_all

from onward_generator.detail.facades import party_base, party_material
from onward_generator.dress.atlas import TILE_PNG_LEVEL, decode_jpeg_rgb, encode_opaque_png
from onward_generator.generation.gltf import Node, Primitive, _facade_uvs, _triangle_groups, recentre_uvs, surface_uvs
from onward_generator.generation.mesh import Mesh, merge, polygons_of
from onward_generator.materials.library_plan import texture_record
from onward_generator.materials.tile_cache import TileCache
from onward_generator.materials.trim_atlas import INF, bake_trim_atlas, between
from onward_generator.materials.trim_style import (
    AUTO,
    BOARDED_CELL,
    DOOR_CELLS,
    FACADE_MODES,
    SHOP_CELL,
    Cell,
    Strip,
    StyleError,
    TrimLayout,
    load_regional_table,
    load_style_table,
    resolve_style,
    style_atlas,
    trim_layout,
)
from onward_generator.tiers import MID, SCENIC

TRIM_PREFIX = "facade_trim_"
SCENIC_TRIM_PREFIX = "scenic_trim_"
# Nodes whose walls the atlas covers: buildings, OSM building:part volumes and landmark towers.
TRIM_NODE_TYPES = frozenset({"building", "building_part", "landmark_part"})
STONE_MATERIALS = frozenset({"frame_stone"})
GLASS_MATERIALS = frozenset({"glass_dark"})
DOOR_MATERIALS = frozenset({"door_wood"})
BOARDED_MATERIALS = frozenset({"glass_boarded"})
# A glass back at least this wide, or starting at the ground, is a shopfront or a glazed door.
SHOP_GLASS_WIDTH_M = 1.9
GROUND_Z_M = 0.05
# A face whose normal leans further than this from horizontal is not a wall plane, and is not cut.
PLANE_TOL = 1e-6
# Faces of one plane agree far below these; the rounding only groups them.
NORMAL_DECIMALS = 4
OFFSET_DECIMALS = 3
# Openings fit 0-1 whatever the tile.
# The face id of every triangle that is not vertical: no vertical face uses a negative one.
SLOPED_FACE = -1
UNIT_TILE_M = 1.0
FALLBACK_STOREY_M = 3.0
# The part of a wall strip a plain face samples: above the string course, below the shadow of the next.
PLAIN_V = (0.1, 0.85)


def trim_material(style: str) -> str:
    return f"{TRIM_PREFIX}{style}"


def scenic_trim_material(style: str) -> str:
    return f"{SCENIC_TRIM_PREFIX}{style}"


def band_mesh(mesh: Mesh, levels: Sequence[float], plain_levels: Sequence[float]) -> tuple[Mesh, np.ndarray, np.ndarray]:
    """Cut a wall mesh at horizontal planes. Return the mesh, each vertex's band and whether its face is plain.

    The triangles of each vertical plane are joined back into the face they came
    from, the face is cut into bands and each band is triangulated again
    (constrained Delaunay, as ``cap`` does), so a band costs a few triangles
    instead of splitting every sliver that crossed it. A face with openings is
    cut at ``levels``; a plain face (no hole, one rectangle: a party wall, a
    blind wall) only at ``plain_levels``. Band ``b`` lies between the ``b``-th
    and the ``b+1``-th sorted level of its list (band 0 below the first). A
    vertex is written once per face and band, so a corner keeps a vertex (and a normal) on each
    of its faces, and a point on a level one in each band. A triangle that is not vertical
    is kept whole, in the band of its centre; a triangle of no area is dropped.
    Faces keep their outward side.
    """
    full, coarse = np.unique(levels), np.unique(plain_levels)
    vertices, triangles = mesh.vertices, mesh.triangles
    cross = mesh.face_normals()
    length = np.linalg.norm(cross, axis=1)
    keys: dict[tuple[float, ...], int] = {}
    out_v: list[np.ndarray] = []
    out_b: list[int] = []
    out_p: list[bool] = []
    out_t: list[tuple[int, int, int]] = []

    def vertex(face: int, band: int, plain: bool, point: np.ndarray) -> int:
        key = (face, band, float(plain), *point.tolist())
        if key not in keys:
            keys[key] = len(out_v)
            out_v.append(point)
            out_b.append(band)
            out_p.append(plain)
        return keys[key]

    planes: dict[tuple[float, ...], tuple[np.ndarray, float, list[int]]] = {}
    for t in np.flatnonzero(length > 0):
        normal = cross[t] / length[t]
        if abs(normal[2]) > PLANE_TOL:  # pragma: no mutate: no triangle leans exactly PLANE_TOL from vertical, so > and >= sort the same triangles
            band = int(np.searchsorted(full, vertices[triangles[t], 2].mean()))
            out_t.append(tuple(vertex(SLOPED_FACE, band, False, vertices[i]) for i in triangles[t]))  # type: ignore[arg-type]  # pragma: no mutate: any id no vertical face uses keeps the sloped triangles apart, None included
            continue
        offset = float(normal @ vertices[triangles[t, 0]])  # pragma: no mutate: every corner of a triangle lies on its plane, so each gives the same offset
        key = (*np.round(normal, NORMAL_DECIMALS).tolist(), round(offset, OFFSET_DECIMALS))
        planes.setdefault(key, (normal, offset, []))[2].append(int(t))
    for face_index, (normal, offset, members) in enumerate(planes.values()):
        along = np.array([-normal[1], normal[0], 0.0])
        origin = np.array([normal[0] * offset, normal[1] * offset, 0.0])
        flat = vertices @ along  # ``origin`` lies along the normal, so it adds nothing along the face
        # Simplifying with no tolerance drops the collinear points where two faces of one line met.
        face = union_all([Polygon([(flat[i], vertices[i, 2]) for i in triangles[t]]) for t in members]).simplify(0.0)
        plain = isinstance(face, Polygon) and not face.interiors and len(face.exterior.coords) == 5
        lv = coarse if plain else full
        left, low, right, high = face.bounds
        cuts = [float(c) for c in lv if low < c < high]  # pragma: no mutate: a level on the face's own edge only adds an empty band
        for lo, hi in pairwise([low, *cuts, high]):
            band = int(np.searchsorted(lv, (lo + hi) / 2))
            for polygon in polygons_of(face.intersection(box(left, lo, right, hi))):
                for tri in constrained_delaunay_triangles(polygon).geoms:
                    a, b, c = tri.exterior.coords[:3]
                    # Counter-clockwise in (along, up) faces the way ``normal`` does.
                    if between((b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1]), -INF, 0.0):  # pragma: no mutate: shapely winds every triangle one way, so any bound below the smallest doubled area sorts them alike
                        b, c = c, b
                    out_t.append(tuple(vertex(face_index, band, plain, origin + x * along + np.array([0.0, 0.0, z])) for x, z in (a, b, c)))  # type: ignore[arg-type]
    if not out_t:
        return Mesh.empty(), np.zeros(0, dtype=np.int64), np.zeros(0, dtype=bool)
    return Mesh(np.array(out_v), np.array(out_t)), np.array(out_b), np.array(out_p)


@dataclass(frozen=True, slots=True)
class Bands:
    """Where a building's wall is cut: the zocalo line (when it has a zocalo), each storey line, the foot of the parapet."""

    zocalo_m: float
    storey_m: float
    storeys: int
    wall_top_m: float
    zocalo: bool = True

    @property
    def levels(self) -> list[float]:
        return [*self._foot, *(k * self.storey_m for k in range(1, self.storeys)), self.wall_top_m]

    @property
    def plain_levels(self) -> list[float]:
        return [*self._foot, self.wall_top_m]

    @property
    def _foot(self) -> list[float]:
        return [self.zocalo_m] if self.zocalo else []


@dataclass(frozen=True, slots=True)
class Look:
    """What one building takes from the atlas besides its wall colour: the tone of its strip, and
    whether it has a stone zocalo, a cornice under the parapet and a string course at every storey."""

    tone: int
    zocalo: bool
    cornice: bool
    courses: bool


# A Catastro split names each construction of a parcel ``<parcel id>_part<n>`` or ``_part<n>.<m>`` (ADR-016).
_PART_SUFFIX = re.compile(r"_part[0-9.]+$")


def look_key(name: str) -> str:
    """The id a building's look is drawn for: the parts of one Catastro parcel share it, as they share a wall."""
    return _PART_SUFFIX.sub("", name)


def stable_unit(*parts: object) -> float:
    """A number in [0, 1) from ``sha256`` of the parts."""
    digest = hashlib.sha256(":".join(map(str, parts)).encode()).digest()
    return int.from_bytes(digest[:8]) / 2.0**64


def observed_period(record: Mapping[str, Any] | None) -> str | None:
    """The era period of a building record (``facade.typology.era``), only when it is observed; else None.

    Unknown stays unknown (ADR-024): no era, an estimated one or a malformed record gives None.
    """
    facade = (record or {}).get("facade")
    typology = facade.get("typology") if isinstance(facade, Mapping) else None
    era = typology.get("era") if isinstance(typology, Mapping) else None
    if not isinstance(era, Mapping) or era.get("origin") != "observed":
        return None
    period = era.get("period")
    return period if isinstance(period, str) else None


def era_band(atlas: Mapping[str, Any], period: str | None) -> Mapping[str, Any] | None:
    """The era band of ``atlas`` for ``period``, or None (no period, or none drawn for it)."""
    if period is None:
        return None
    band: Mapping[str, Any] | None = (atlas.get("eras") or {}).get(period)
    return band


def building_look(key: str, atlas: Mapping[str, Any], band: Mapping[str, Any] | None = None) -> Look:
    """The tone and the dress of one building, by ``sha256`` of its look key and the shares of the style.

    An era ``band`` narrows the tones to its own and overrides the dress shares it names.
    """
    if band is None:
        dress = atlas["dress"]
        tone = stable_index(len(atlas["tones"]), key, "tone")
    else:
        dress = {**atlas["dress"], **(band.get("dress") or {})}
        tones = band["tones"]
        tone = int(tones[stable_index(len(tones), key, "tone")])
    return Look(
        tone,
        stable_unit(key, "zocalo") < float(dress["zocalo_share"]),
        stable_unit(key, "cornice") < float(dress["cornice_share"]),
        stable_unit(key, "courses") < float(dress["course_share"]),
    )


def building_bands(record: Mapping[str, Any] | None, z_top: float, zocalo_m: float, max_band_m: float,
                   zocalo: bool = True) -> Bands:
    """Storeys from the building record (``facade.storeys``), else ``FALLBACK_STOREY_M`` bands up to ``z_top``.

    A storey taller than ``max_band_m`` (a landmark's single band) is split into
    bands near the fallback height, so a strip is never stretched over a tower.
    Without a ``zocalo`` the wall is not cut at the zocalo line.
    """
    facade = (record or {}).get("facade") or {}
    storeys = facade.get("storeys") or {}
    roof = (record or {}).get("roof") or {}
    count = int(storeys.get("count") or 0)
    height = float(storeys.get("height_m") or 0.0)
    parapet = float(roof.get("parapet_m") or 0.0) if roof.get("type") == "flat_parapet" else 0.0
    measured = count >= 1 and height > 0.0
    wall_top = count * height if measured else z_top - parapet
    if not measured or height > max_band_m:
        count = max(1, round(wall_top / FALLBACK_STOREY_M))
        height = wall_top / count
    return Bands(min(zocalo_m, height / 2), height, count, wall_top, zocalo)


def _strip_v(strip: Strip, local: np.ndarray) -> np.ndarray:
    return strip.v0 + local * (strip.v1 - strip.v0)


def wall_uvs(mesh: Mesh, bands: Bands, wall: Strip, zocalo: Strip, cornice: Strip | None,
             frame: Mapping[str, Any] | None, phase_u: float, courses: bool = True) -> tuple[Mesh, np.ndarray]:
    """Cut a wall primitive into bands and give each piece the UVs of its strip.

    A plain face has one body band from the zocalo to the parapet; it samples the
    middle of its wall strip (``PLAIN_V``), stretched, so it shows no string course.
    Without ``courses`` every storey samples that middle too. Band 0 takes the
    zocalo strip only when ``bands`` has a zocalo, and the top band the cornice
    strip only when there is a ``cornice``; otherwise they take the wall strip.
    """
    sliced, band, plain = band_mesh(mesh, bands.levels, bands.plain_levels)
    if not sliced.triangle_count:
        return sliced, np.zeros((0, 2), dtype=np.float32)
    z = sliced.vertices[:, 2]
    full, coarse = np.unique(bands.levels), np.unique(bands.plain_levels)
    top = float(z.max())
    plain_band = np.minimum(band, len(coarse))
    lower = np.where(plain, np.concatenate([[0.0], coarse])[plain_band], np.concatenate([[0.0], full])[band])
    upper = np.where(plain, np.concatenate([coarse, [top]])[plain_band], np.concatenate([full, [top]])[band])
    local = (z - lower) / (upper - lower)
    last = np.where(plain, len(coarse), len(full))
    body = np.where(plain | (not courses), PLAIN_V[0] + local * (PLAIN_V[1] - PLAIN_V[0]), local)
    v = _strip_v(wall, body)
    on_zocalo = (band == 0) & bands.zocalo
    v = np.where(on_zocalo, _strip_v(zocalo, local), v)
    base = surface_uvs(sliced, wall.repeat_m)
    runs = _facade_uvs(sliced, wall.repeat_m, frame, base) if frame else None
    u = base[:, 0] + phase_u
    # The zocalo and cornice strips repeat at their own lengths; scale U from the wall repeat.
    u = np.where(on_zocalo, u * wall.repeat_m / zocalo.repeat_m, u)
    if cornice is not None:
        on_cornice = band == last
        v = np.where(on_cornice, _strip_v(cornice, local), v)
        u = np.where(on_cornice, u * wall.repeat_m / cornice.repeat_m, u)
    recentred = recentre_uvs(np.column_stack([u, np.zeros_like(u)]), sliced.triangles, runs)
    return sliced, np.column_stack([recentred[:, 0], v]).astype(np.float32)


def stone_uvs(mesh: Mesh, stone: Strip) -> np.ndarray:
    """U on the face plane at the stone repeat; V of each connected piece fitted to the stone strip."""
    base = surface_uvs(mesh, stone.repeat_m)
    v = base[:, 1].copy()
    for group in _triangle_groups(mesh.triangles).values():
        index = np.unique(mesh.triangles[group])
        low, high = float(v[index].min()), float(v[index].max())
        v[index] = (v[index] - low) / max(high - low, 1e-6)
    u = recentre_uvs(np.column_stack([base[:, 0], np.zeros(len(base))]), mesh.triangles)[:, 0]
    return np.column_stack([u, _strip_v(stone, v)]).astype(np.float32)


def stable_index(count: int, *parts: object) -> int:
    digest = hashlib.sha256(":".join(map(str, parts)).encode()).digest()
    return int.from_bytes(digest[:8]) % count


def opening_cell(material: str, mesh: Mesh, index: np.ndarray, building: str, windows: Sequence[Sequence[str]]) -> str:
    """The cell for one opening back: boarded, a door, shop glass, or a window of the building's family."""
    if material in BOARDED_MATERIALS:
        return BOARDED_CELL
    if material in DOOR_MATERIALS:
        return DOOR_CELLS[stable_index(len(DOOR_CELLS), building, "door")]
    points = mesh.vertices[index]
    width = float(np.hypot(*(points[:, :2].max(axis=0) - points[:, :2].min(axis=0))))
    if width >= SHOP_GLASS_WIDTH_M or float(points[:, 2].min()) < GROUND_Z_M:
        return SHOP_CELL
    family = windows[stable_index(len(windows), building, "window")]
    centre = np.round(points.mean(axis=0), 2).tolist()
    return family[stable_index(len(family), building, *centre)]


def opening_uvs(material: str, mesh: Mesh, cells: Mapping[str, Cell], building: str,
                windows: Sequence[Sequence[str]], counts: dict[str, int]) -> np.ndarray:
    """Each connected opening back fits 0–1, then into its cell."""
    unit = surface_uvs(mesh, UNIT_TILE_M, fit_unit=True)
    out = np.zeros_like(unit)
    for group in _triangle_groups(mesh.triangles).values():
        index = np.unique(mesh.triangles[group])
        name = opening_cell(material, mesh, index, building, windows)
        counts[name] = counts.get(name, 0) + 1
        cell = cells[name]
        out[index, 0] = cell.u0 + unit[index, 0] * (cell.u1 - cell.u0)
        out[index, 1] = cell.v0 + unit[index, 1] * (cell.v1 - cell.v0)
    return out.astype(np.float32)


@dataclass(slots=True)
class TrimStats:
    buildings: int = 0
    primitives_merged: int = 0
    wall_triangles_before: int = 0
    wall_triangles_after: int = 0
    kept_materials: dict[str, int] = field(default_factory=dict)
    cells: dict[str, int] = field(default_factory=dict)
    looks: dict[str, int] = field(default_factory=dict)
    # Buildings by era band, only for an atlas that has eras: the period, ``no_band`` or ``unknown``.
    eras: dict[str, int] = field(default_factory=dict)

    def count_look(self, look: Look) -> None:
        names = [f"tone_{look.tone}"] + [name for name, on in (("zocalo", look.zocalo), ("cornice", look.cornice),
                                                               ("courses", look.courses)) if on]
        for name in names:
            self.looks[name] = self.looks.get(name, 0) + 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "buildings": self.buildings,
            "primitives_merged": self.primitives_merged,
            "wall_triangles_before": self.wall_triangles_before,
            "wall_triangles_after": self.wall_triangles_after,
            "kept_materials": dict(sorted(self.kept_materials.items())),
            "openings_by_cell": dict(sorted(self.cells.items())),
            "looks": dict(sorted(self.looks.items())),
            **({"eras": dict(sorted(self.eras.items()))} if self.eras else {}),
        }


def trim_building(node: Node, record: Mapping[str, Any] | None, style: str, atlas: Mapping[str, Any],
                  layout: TrimLayout, windows: Sequence[Sequence[str]], stats: TrimStats) -> Node:
    """One building node with every part the atlas covers in one primitive of the trim material."""
    walls = atlas["walls"]
    scenic = atlas["scenic_walls"]
    extras = node.extras or {}
    frame = extras.get("uv_frame")
    # The first value of the building's ``uv_phase`` (materials apply), or none.
    phase_u = float(sum((extras.get("uv_phase") or [])[:1]))
    key = look_key(node.name)
    period = observed_period(record)
    band = era_band(atlas, period)
    look = building_look(key, atlas, band)
    if band is not None:
        windows = [windows[i] for i in band["window_families"]]
    merged: dict[str, tuple[list[Mesh], list[np.ndarray]]] = {}
    kept: list[Primitive] = []
    # A mid building (plan D3) is all scenic: its painted windows join its walls in the scenic material and lightmap class.
    mid = (record or {}).get("tier") == MID
    # The scenic and mid rings stand behind the fog, past the play area: one band per wall, no zocalo, cornice or
    # string course, so a face is not cut into three or into storeys (triangle audit, 10 November 2026).
    background = (record or {}).get("tier") in (MID, SCENIC)
    for prim in node.primitives:
        # A blank party wall (``party_<wall>``) samples its wall's strip under its own lightmap class.
        material = party_base(prim.material)
        target = scenic_trim_material(style) if material in scenic or mid else trim_material(style)
        if material != prim.material:
            target = party_material(target)
        if not prim.mesh.triangle_count:
            kept.append(prim)
            continue
        if material in walls or material in scenic:
            strip = layout.tones[material if material in walls else scenic[material]][look.tone]
            z_top = max(float(p.mesh.vertices[:, 2].max()) for p in node.primitives if p.mesh.triangle_count)
            bands = building_bands(record, z_top, float(atlas["zocalo_m"]), float(atlas["max_band_m"]), look.zocalo)
            if background:
                bands = Bands(0.0, bands.wall_top_m, 1, bands.wall_top_m, zocalo=False)  # pragma: no mutate: one band and no zocalo, so the zocalo and storey heights are never read
            cornice = layout.cornice if look.cornice and not background else None
            mesh, uvs = wall_uvs(prim.mesh, bands, strip, layout.zocalo, cornice, frame, phase_u, look.courses and not background)
            stats.wall_triangles_before += prim.mesh.triangle_count
            stats.wall_triangles_after += mesh.triangle_count
        elif material in STONE_MATERIALS:
            mesh, uvs = prim.mesh, stone_uvs(prim.mesh, layout.stone)
        elif material in GLASS_MATERIALS | DOOR_MATERIALS | BOARDED_MATERIALS:
            mesh, uvs = prim.mesh, opening_uvs(material, prim.mesh, layout.cells, key, windows, stats.cells)
        else:
            if material.startswith(("plaster_", "stone_", "brick_", "building_wall")):
                stats.kept_materials[prim.material] = stats.kept_materials.get(prim.material, 0) + 1
            kept.append(prim)
            continue
        meshes, uv_list = merged.setdefault(target, ([], []))
        meshes.append(mesh)
        uv_list.append(uvs)
        stats.primitives_merged += 1
    if not merged:
        return node
    stats.buildings += 1
    stats.count_look(look)
    if "eras" in atlas:
        era = "unknown" if period is None else period if band is not None else "no_band"
        stats.eras[era] = stats.eras.get(era, 0) + 1
    trims = [Primitive(merge(meshes), name, np.vstack(uv_list).astype(np.float32)) for name, (meshes, uv_list) in sorted(merged.items())]
    return replace(node, primitives=(*trims, *kept))


def window_families(atlas: Mapping[str, Any]) -> list[list[str]]:
    return [[f"window_{f}_{p}" for p in range(len(family["closed"]))] for f, family in enumerate(atlas["window_families"])]


def trim_nodes(nodes: Sequence[Node], records: Mapping[str, Mapping[str, Any]], style: str,
               atlas: Mapping[str, Any], layout: TrimLayout) -> tuple[list[Node], TrimStats]:
    """Every building, building part and landmark tower node (no matrix) through ``trim_building``; the rest as they are."""
    stats = TrimStats()
    windows = window_families(atlas)
    out: list[Node] = []
    for node in nodes:
        extras = node.extras or {}
        if extras.get("type") not in TRIM_NODE_TYPES or node.matrix is not None:
            out.append(node)
            continue
        record = records.get(str(extras.get("object_id") or node.name))
        out.append(trim_building(node, record, style, atlas, layout, windows, stats))
    return out, stats


# Bump when bake_trim_atlas or the PNG encoder changes the bytes for the same style, tints and photographs.
# 3: the cornice strip follows CORNICE_REF_M 0.7 (was 1.0); version 2 kept serving the old atlas from the cache.
TRIM_BAKE_VERSION = 3


@dataclass(frozen=True, slots=True)
class WallPalette:
    """The wall palette of an atmosphere preset (plan E8, ADR-037).

    ``grime`` scales the streaks and the darkening at the foot of the wall that the atlas bakes; ``desaturation``
    moves each wall colour toward its own grey; ``value_max`` caps its brightest sRGB channel. Only the palette's
    wall strips change: an observed colour (``building:colour``, a ``__tint_`` material) is not in the atlas.
    """

    preset: str
    grime: float
    desaturation: float
    value_max: float


def _luminance(tint: Sequence[float]) -> float:
    return 0.2126 * float(tint[0]) + 0.7152 * float(tint[1]) + 0.0722 * float(tint[2])


def _linear_to_srgb(value: float) -> float:
    if value <= 0.0031308:  # pragma: no mutate: the two pieces meet at the knee; < and <= differ below the six-decimal rounding
        return value * 12.92
    return 1.055 * value ** (1.0 / 2.4) - 0.055


def _srgb_to_linear(value: float) -> float:
    if value <= 0.04045:  # pragma: no mutate: the two pieces meet at the knee; < and <= differ below the six-decimal rounding
        return value / 12.92
    return ((value + 0.055) / 1.055) ** 2.4


def palette_tint(tint: Sequence[float], palette: WallPalette) -> tuple[float, float, float]:
    """A linear wall colour moved ``desaturation`` of the way to its grey, then its brightest sRGB channel capped."""
    grey = _luminance(tint)
    mixed = [float(c) + palette.desaturation * (grey - float(c)) for c in tint[:3]]
    srgb = [_linear_to_srgb(max(c, 0.0)) for c in mixed]
    peak = max(srgb)
    if peak > palette.value_max:  # pragma: no mutate: at peak == value_max the scale is 1, so >= changes nothing
        srgb = [c * palette.value_max / peak for c in srgb]
    r, g, b = (_srgb_to_linear(c) for c in srgb)
    return (round(r, 6), round(g, 6), round(b, 6))


def palette_atlas(atlas: Mapping[str, Any], palette: WallPalette) -> dict[str, Any]:
    """``atlas`` with its streaks and its zocalo splash darkened by ``grime`` times what the style says."""
    grime = dict(atlas["grime"])
    grime["streak_dark"] = float(grime["streak_dark"]) * palette.grime
    grime["base_splash_dark"] = float(grime["base_splash_dark"]) * palette.grime
    return {**atlas, "grime": grime}


@dataclass(frozen=True, slots=True)
class TrimResult:
    """The facade atlas of one style applied to a scene, or the reason the legacy path stays."""

    mode: str
    style: str
    nodes: list[Node]
    materials: tuple[str, ...] = ()
    png: bytes = b""
    report: Mapping[str, Any] = field(default_factory=dict)


def trim_cache_key(style: str, atlas: Mapping[str, Any], tints: Mapping[str, Sequence[float]], sources: Mapping[str, str]) -> str:
    """sha256 of everything that changes the atlas bytes."""
    doc = {
        "bake": TRIM_BAKE_VERSION, "pillow": PIL.__version__, "numpy": np.__version__, "png_level": TILE_PNG_LEVEL,
        "style": style, "atlas": atlas, "tints": {k: [float(c) for c in v] for k, v in tints.items()},
        "sources": dict(sources),
    }
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def apply_trim(
    nodes: Sequence[Node],
    manifest: Mapping[str, Any],
    colors: Mapping[str, Sequence[float]],
    *,
    mode: str = "trim",
    style: str = AUTO,
    table_path: str | Path | None = None,
    cache: TileCache | None = None,
    regional: bool = False,
    regional_table_path: str | Path | None = None,
    palette: WallPalette | None = None,
) -> TrimResult:
    """Choose the style, bake (or read from ``cache``) its atlas and remap the building nodes.

    ``colors`` are the linear wall colours of the scene by material id. A mode of
    ``legacy``, or a style without an atlas, returns the nodes unchanged. With
    ``regional`` (plan D1, off by default) a style whose atlas is null in the table
    takes its recipe from the regional table, when that has one. A ``palette`` (plan E8) changes the wall colours
    and the grime before the bake; without one the atlas is the one of before, byte for byte.
    """
    if mode not in FACADE_MODES:
        raise StyleError(f"unknown facade mode {mode!r}; known: {', '.join(FACADE_MODES)}")
    table = load_style_table(table_path)
    origin = (manifest.get("frame") or {}).get("origin_wgs84")
    name, chosen = resolve_style(table, style, origin)
    head = {"mode": mode, "style": name, "style_origin": chosen, "style_table_sha256": table.sha256}
    wants_regional = regional and mode == "trim" and table.styles[name].atlas is None
    regional_table = load_regional_table(table, regional_table_path) if wants_regional else None
    atlas, atlas_origin = style_atlas(table, name, regional_table)
    if mode == "legacy" or atlas is None:
        reason = "config" if mode == "legacy" else "style_has_no_atlas"
        return TrimResult("legacy", name, list(nodes), report={**head, "mode": "legacy", "reason": reason})
    records = {key: texture_record(str(library_id)) for key, library_id in atlas["sources"].items()}
    for key, record in records.items():
        if not record.license.upper().startswith("CC0"):
            raise StyleError(f"style {name}: source {key} ({record.asset_id}) is {record.license}, not CC0")
    # Plan Q3: a style may fix a strip's tint (Paris limestone) instead of taking the palette's colour.
    fixed = atlas.get("wall_tints") or {}
    missing = sorted(set(atlas["walls"]) - set(colors) - set(fixed))
    if missing:
        raise StyleError(f"style {name}: no colour for wall strips {', '.join(missing)}")
    tints = {wall: tuple(float(c) for c in (fixed[wall] if wall in fixed else colors[wall])[:3]) for wall in sorted(atlas["walls"])}
    atmosphere: dict[str, Any] | None = None
    if palette is not None:
        before = sum(_luminance(t) for t in tints.values()) / len(tints)
        atlas = palette_atlas(atlas, palette)
        tints = {wall: palette_tint(tint, palette) for wall, tint in tints.items()}
        after = sum(_luminance(t) for t in tints.values()) / len(tints)
        atmosphere = {"preset": palette.preset, "origin": "generated", "grime": palette.grime,
                      "wall_desaturation": palette.desaturation, "wall_value_max": palette.value_max,
                      "wall_luminance_before": round(before, 4), "wall_luminance_after": round(after, 4)}
    layout = trim_layout(atlas)
    key = trim_cache_key(name, atlas, tints, {k: r.sha256 for k, r in sorted(records.items())})

    def bake() -> bytes:
        sources = {k: decode_jpeg_rgb(Path(r.path).read_bytes()) for k, r in records.items()}
        return encode_opaque_png(bake_trim_atlas(name, atlas, layout, tints, sources))

    png = bake() if cache is None else cache.fetch(key, bake)
    records_by_id = {str(o.get("id")): o for o in manifest.get("objects") or [] if isinstance(o, dict)}
    out, stats = trim_nodes(nodes, records_by_id, name, atlas, layout)
    names = {trim_material(name), scenic_trim_material(name), party_material(trim_material(name))}
    used = sorted({p.material for n in out for p in n.primitives} & names)
    extra: dict[str, Any] = {}
    if regional_table is not None:
        extra = {"regional_table_sha256": regional_table.sha256, "atlas_origin": atlas_origin}
        if "eras" in atlas:
            extra["era_bands"] = {"era_origin": "observed", "band_origin": "estimated", "buildings": dict(sorted(stats.eras.items()))}
    if atmosphere is not None:
        extra["atmosphere"] = atmosphere
    report = {
        **head,
        **extra,
        "materials": used,
        "atlas": {
            "size": [layout.size, layout.height], "sha256": hashlib.sha256(png).hexdigest(), "cache_key": key,
            "sources": [
                {"key": k, "asset_id": r.asset_id, "license": r.license, "credit": r.authors[0], "sha256": r.sha256}
                for k, r in sorted(records.items())
            ],
            "license": "project-generated from CC0 photographs",
        },
        "stats": stats.as_dict(),
    }
    return TrimResult("trim", name, out, tuple(used), png, report)
