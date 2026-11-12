"""Ground partition: disjoint flat surfaces covering the site.

Precedence (each class is cut out of the following ones): buildings →
carriageway → water → pedestrian → sidewalk → green → ground. The
carriageway is lowered by ``carriageway_depth_m`` and bordered by curb walls;
every other surface lies at z = 0.

Crossings are strips of the carriageway that the navmesh bakes as walkable
(ADR-015). They stay part of the carriageway surface; only the ground collider
is split.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

import shapely
from shapely import LineString, MultiPolygon, Polygon, STRtree, make_valid, union_all
from shapely.geometry.base import BaseGeometry

from onward_generator.generation.config import GenerationConfig
from onward_generator.generation.mesh import polygons_of


class SurfaceClass(StrEnum):
    CARRIAGEWAY = "carriageway"
    WATER = "water"
    PEDESTRIAN = "pedestrian"
    SIDEWALK = "sidewalk"
    GREEN = "green"
    GROUND = "ground"


PRECEDENCE: tuple[SurfaceClass, ...] = tuple(SurfaceClass)


COBBLE_PAVING = frozenset({"sett", "cobblestone", "paving_stones", "unhewn_cobblestone"})


@dataclass(frozen=True, slots=True)
class LinearWay:
    id: str
    road_class: str  # vehicular | pedestrian
    geometry: BaseGeometry
    width_m: float
    width_origin: str  # observed | inferred | config_default
    paving: str | None = None


@dataclass(frozen=True, slots=True)
class AreaFeature:
    id: str
    surface: SurfaceClass
    geometry: BaseGeometry
    paving: str | None = None


@dataclass(slots=True)
class Surface:
    surface: SurfaceClass
    geometry: BaseGeometry
    sources: tuple[str, ...]
    dropped_slivers: int = 0


@dataclass(slots=True)
class Partition:
    site: Polygon
    buildings: BaseGeometry
    surfaces: dict[SurfaceClass, Surface] = field(default_factory=dict)
    carriageway_raw: BaseGeometry = field(default_factory=Polygon)
    crossings: BaseGeometry = field(default_factory=Polygon)

    def overlap_area(self) -> float:
        parts = [s.geometry for s in self.surfaces.values()] + [self.buildings.intersection(self.site)]
        return max(0.0, sum(p.area for p in parts) - union_all(parts).area)

    def gap_area(self) -> float:
        parts = [s.geometry for s in self.surfaces.values()] + [self.buildings]
        return self.site.difference(union_all(parts)).area


def _polygonal(geometry: BaseGeometry, precision: float, min_area: float) -> tuple[BaseGeometry, int]:
    """Keep polygonal parts on a fixed grid, dropping slivers below ``min_area``."""
    if geometry.is_empty:
        return Polygon(), 0
    snapped = shapely.set_precision(make_valid(geometry), precision)
    polygons = [p for p in polygons_of(make_valid(snapped))]
    kept = [p for p in polygons if p.area >= min_area]
    if not kept:
        return Polygon(), len(polygons)
    return (kept[0] if len(kept) == 1 else MultiPolygon(kept)), len(polygons) - len(kept)


def build_partition(
    site: Polygon,
    building_footprints: Sequence[BaseGeometry],
    ways: Sequence[LinearWay],
    areas: Sequence[AreaFeature],
    config: GenerationConfig,
) -> Partition:
    precision, min_area = config.precision_m, config.min_surface_area_m2
    # Plan Q7: the buildings share the surfaces' grid, and every operation below keeps it (grid_size). Snapping only the
    # surfaces moved their vertices up to half a cell into the buildings, and the overlaps added up along every facade.
    buildings = (
        shapely.set_precision(make_valid(union_all(list(building_footprints), grid_size=precision)), precision)
        if building_footprints else Polygon()  # pragma: no mutate: an empty union is as empty as Polygon(); the test skips the work
    )

    def buffered(way: LinearWay) -> BaseGeometry:
        return way.geometry.buffer(way.width_m / 2.0, cap_style="round", join_style="round", quad_segs=4)

    vehicular = [w for w in ways if w.road_class == "vehicular"]
    pedestrian = [w for w in ways if w.road_class == "pedestrian"]
    carriageway_raw = union_all([buffered(w) for w in vehicular]) if vehicular else Polygon()

    candidates: Mapping[SurfaceClass, tuple[BaseGeometry, tuple[str, ...]]] = {
        SurfaceClass.CARRIAGEWAY: (carriageway_raw, tuple(sorted(w.id for w in vehicular))),
        SurfaceClass.WATER: (
            union_all([a.geometry for a in areas if a.surface is SurfaceClass.WATER]),
            tuple(sorted(a.id for a in areas if a.surface is SurfaceClass.WATER)),
        ),
        SurfaceClass.PEDESTRIAN: (
            union_all([buffered(w) for w in pedestrian] + [a.geometry for a in areas if a.surface is SurfaceClass.PEDESTRIAN]),
            tuple(sorted([w.id for w in pedestrian] + [a.id for a in areas if a.surface is SurfaceClass.PEDESTRIAN])),
        ),
        SurfaceClass.SIDEWALK: (
            carriageway_raw.buffer(config.sidewalk_width_m, join_style="round", quad_segs=4)
            if config.sidewalk_width_m > 0 and not carriageway_raw.is_empty
            else Polygon(),
            tuple(sorted(w.id for w in vehicular)),
        ),
        SurfaceClass.GREEN: (
            union_all([a.geometry for a in areas if a.surface is SurfaceClass.GREEN]),
            tuple(sorted(a.id for a in areas if a.surface is SurfaceClass.GREEN)),
        ),
        SurfaceClass.GROUND: (site, ()),
    }

    partition = Partition(site=site, buildings=buildings, carriageway_raw=carriageway_raw)
    taken: BaseGeometry = buildings
    for surface in PRECEDENCE:
        raw, sources = candidates[surface]
        region = raw.intersection(site, grid_size=precision).difference(taken) if not raw.is_empty else Polygon()  # pragma: no mutate: an empty raw clips to an empty region; the test skips the work
        geometry, dropped = _polygonal(region, precision, min_area)
        if not geometry.is_empty:
            partition.surfaces[surface] = Surface(surface, geometry, sources, dropped)
            taken = union_all([taken, geometry])
    carriageway = partition.surfaces.get(SurfaceClass.CARRIAGEWAY)
    if carriageway is not None:
        partition.crossings, _ = _polygonal(crossing_strips(vehicular, carriageway.geometry), precision, min_area)
    return partition


# A walkable strip across the carriageway, like a zebra crossing. The navmesh
# joins it to the sidewalks on both kerbs (0.15 m is below the 0.3 m agent
# step), so the blocks on either side become one island.
CROSSING_WIDTH_M = 4.0
# No stretch of street is longer than this without a crossing.
CROSSING_SPACING_M = 50.0
# A junction crossing sits this far past the kerb of the widest street there.
CROSSING_CORNER_M = 1.0
# A strip is this much longer than the widest street it touches, then clipped
# to the carriageway, so it always reaches both kerbs.
CROSSING_OVERHANG_M = 2.0  # pragma: no mutate: the strip is clipped to the carriageway; a longer overhang draws the same crossing
# Ways this close to a noded junction point meet there.
JUNCTION_TOLERANCE_M = 1e-6  # pragma: no mutate: a float tolerance; any distance well under a street width finds the same ways


def carriageway_slab(partition: Partition, precision_m: float) -> BaseGeometry:
    """The carriageway minus its crossings, for the ``collider/carriageway`` slab.

    Both inputs are snapped to ``precision_m``, but the cut adds vertices where a
    strip edge meets the kerb, some a fraction of a millimetre from an old one.
    The slab welds vertices on a 0.1 mm grid, so such a pair becomes one vertex
    and the walls stop closing. The result is snapped again, like the surfaces.
    """
    carriageway = partition.surfaces.get(SurfaceClass.CARRIAGEWAY)
    if carriageway is None:
        return Polygon()
    return shapely.set_precision(carriageway.geometry.difference(partition.crossings), precision_m)


def crossing_offsets(length_m: float, start_m: float, end_m: float) -> list[float]:
    """Distances along a street segment where a crossing goes.

    One at ``start_m`` and one at ``length_m - end_m`` (the junction setbacks),
    and evenly between them so no gap exceeds ``CROSSING_SPACING_M``. A segment
    too short for two crossings apart gets one, halfway along.
    """
    first, last = start_m, length_m - end_m
    if last - first < CROSSING_WIDTH_M:
        return [length_m / 2.0]
    gaps = math.ceil((last - first) / CROSSING_SPACING_M)
    return [first + (last - first) * i / gaps for i in range(gaps + 1)]


@dataclass(frozen=True, slots=True)
class CrossingSite:
    """Where one walkable crossing goes: its centre on the street, the street direction, and its length.

    ``along`` is the unit chord of the street at the centre. The strip runs
    square to it, ``span_m`` long and ``CROSSING_WIDTH_M`` wide.
    """

    centre: tuple[float, float]
    along: tuple[float, float]
    span_m: float

    def strip(self) -> BaseGeometry:
        (x, y), (ux, uy) = self.centre, self.along
        half = self.span_m / 2.0
        axis = LineString([(x + uy * half, y - ux * half), (x - uy * half, y + ux * half)])
        return axis.buffer(CROSSING_WIDTH_M / 2.0, cap_style="flat")


def _site(line: LineString, distance_m: float, span_m: float) -> CrossingSite:
    """The crossing at ``distance_m`` along ``line``; the direction is the chord to 1 m further on."""
    centre = line.interpolate(distance_m)
    ahead = line.interpolate(distance_m + 1.0)
    dx, dy = ahead.x - centre.x, ahead.y - centre.y
    length = math.hypot(dx, dy)
    return CrossingSite((centre.x, centre.y), (dx / length, dy / length), span_m)


def _crossing(line: LineString, distance_m: float, span_m: float) -> BaseGeometry:
    """A ``CROSSING_WIDTH_M`` strip square to ``line`` at ``distance_m``, ``span_m`` long."""
    return _site(line, distance_m, span_m).strip()


def crossing_sites(vehicular: Sequence[LinearWay]) -> list[CrossingSite]:
    """Every crossing over the vehicular ways, in a fixed order.

    The street centrelines are noded at every junction, so each segment runs
    from one junction (or dead end) to the next. A segment gets a crossing on
    each arm, set back past the kerb of the widest street at that junction, and
    more along a long block (``crossing_offsets``). Each strip spans the widest
    street the segment touches plus ``CROSSING_OVERHANG_M``. Vehicular areas
    have no centreline and get none.
    """
    lines = [part for way in vehicular for part in shapely.get_parts(way.geometry) if isinstance(part, LineString)]
    tree = STRtree([way.geometry for way in vehicular])

    def widest(at: BaseGeometry) -> float:
        return max(vehicular[int(i)].width_m for i in tree.query(at, predicate="dwithin", distance=JUNCTION_TOLERANCE_M))

    sites: list[CrossingSite] = []
    for segment in shapely.get_parts(shapely.line_merge(union_all(lines))):
        start, end = (widest(segment.interpolate(d)) / 2.0 + CROSSING_CORNER_M + CROSSING_WIDTH_M / 2.0 for d in (0.0, segment.length))
        span = widest(segment) + CROSSING_OVERHANG_M
        sites.extend(_site(segment, d, span) for d in crossing_offsets(segment.length, start, end))
    return sites


def crossing_strips(vehicular: Sequence[LinearWay], carriageway: BaseGeometry) -> BaseGeometry:
    """Walkable crossings over ``carriageway`` (``crossing_sites``), clipped to it."""
    return union_all([site.strip() for site in crossing_sites(vehicular)]).intersection(carriageway)


def _way_buffer(way: LinearWay) -> BaseGeometry:
    if way.width_m <= 0:
        return way.geometry if way.geometry.geom_type in {"Polygon", "MultiPolygon"} else Polygon()
    return way.geometry.buffer(way.width_m / 2.0, cap_style="round", join_style="round", quad_segs=4)


def split_pedestrian_by_paving(
    pedestrian: Surface,
    ways: Sequence[LinearWay],
    areas: Sequence[AreaFeature],
    config: GenerationConfig,
) -> tuple[Surface, Surface | None]:
    """Cut cobble-tagged pedestrian pieces out of the merged pedestrian surface.

    The partition stays one ``PEDESTRIAN`` class (overlap/gap unchanged). The
    scene writes ``surface/pedestrian`` (remainder) and optional
    ``surface/pedestrian_cobble``.
    """
    cobble_ways = [w for w in ways if w.road_class == "pedestrian" and (w.paving or "") in COBBLE_PAVING]
    cobble_areas = [
        a for a in areas
        if a.surface is SurfaceClass.PEDESTRIAN and (a.paving or "") in COBBLE_PAVING
    ]
    parts = [_way_buffer(w) for w in cobble_ways] + [a.geometry for a in cobble_areas]
    sources = tuple(sorted({*[w.id for w in cobble_ways], *[a.id for a in cobble_areas]}))
    if not parts or not sources:
        return pedestrian, None
    raw = union_all(parts)
    cut = raw.intersection(pedestrian.geometry)
    geometry, dropped = _polygonal(cut, config.precision_m, config.min_surface_area_m2)
    if geometry.is_empty:
        return pedestrian, None
    remainder, rest_dropped = _polygonal(
        pedestrian.geometry.difference(geometry), config.precision_m, config.min_surface_area_m2,
    )
    rest_sources = tuple(s for s in pedestrian.sources if s not in set(sources))
    rest = Surface(
        SurfaceClass.PEDESTRIAN,
        remainder if not remainder.is_empty else Polygon(),
        rest_sources or pedestrian.sources,
        pedestrian.dropped_slivers + rest_dropped,
    )
    cobble = Surface(SurfaceClass.PEDESTRIAN, geometry, sources, dropped)
    return rest, cobble
