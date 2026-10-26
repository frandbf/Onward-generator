"""Ground partition: disjoint flat surfaces covering the site.

Precedence (each class is cut out of the following ones): buildings →
carriageway → water → pedestrian → sidewalk → green → ground. The
carriageway is lowered by ``carriageway_depth_m`` and bordered by curb walls;
every other surface lies at z = 0.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

import shapely
from shapely import MultiPolygon, Polygon, make_valid, union_all
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
    buildings = union_all(list(building_footprints)) if building_footprints else Polygon()

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
        region = raw.intersection(site).difference(taken) if not raw.is_empty else Polygon()
        geometry, dropped = _polygonal(region, precision, min_area)
        if not geometry.is_empty:
            partition.surfaces[surface] = Surface(surface, geometry, sources, dropped)
            taken = union_all([taken, geometry])
    return partition


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
