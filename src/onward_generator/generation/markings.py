"""Zebra paint on the walkable crossings that OpenStreetMap records.

Every vehicular segment gets walkable crossing strips (ADR-015), placed by a
rule, not by the map. Paint goes only on a strip that an OSM crossing marks:
a ``highway=crossing`` or ``crossing=*`` node, or a ``footway=crossing`` way,
within ``ZEBRA_MATCH_M`` of the strip. A strip with no such evidence stays
plain asphalt, and the report says which strips are observed and which are
generated.

The paint is a Spanish *paso de cebra*: bars ``ZEBRA_BAR_M`` wide with
``ZEBRA_GAP_M`` between them, parallel to the street, each as long as the
strip is wide, laid side by side from kerb to kerb. Each bar is one quad (two
triangles) ``ZEBRA_LIFT_M`` above the carriageway, so the depth buffer keeps
it in front of the asphalt.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import shapely
from shapely import LineString, Point, Polygon, STRtree
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from onward_generator.generation.surfaces import CROSSING_WIDTH_M, JUNCTION_TOLERANCE_M, CrossingSite

# Bar and gap of a Spanish zebra crossing (Norma 8.2-IC: 0.5 m bars, 0.5 m apart).
ZEBRA_BAR_M = 0.5
ZEBRA_GAP_M = 0.5
# The first and last bar stay this far from the kerb, over the gutter.
ZEBRA_KERB_M = 0.5
# Height of the paint over the asphalt. At 1 cm a 24-bit depth buffer still
# separates the two at 40 m with a 1 cm near plane, and the step is invisible.
ZEBRA_LIFT_M = 0.01
# An OSM crossing this close to a strip marks it. The node sits on the street
# centreline, often on the kerb line of the junction, while the strip is set 1 m
# past that kerb; one strip width pairs them without reaching the next strip.
ZEBRA_MATCH_M = CROSSING_WIDTH_M
# A crossing tagged with one of these has no paint on the road.
UNPAINTED = frozenset({"no", "unmarked", "informal"})
# The ``footway``, ``cycleway`` or ``path`` value of a way that crosses a road.
CROSSING_WAY_KEYS = ("footway", "cycleway", "path")
# A covered-by test with a floating-point margin, in square metres.
OUTSIDE_TOLERANCE_M2 = 1e-6  # pragma: no mutate: a float tolerance; any area well under a bar finds the same bars


@dataclass(frozen=True, slots=True)
class CrossingEvidence:
    """An OSM element that says a crossing is there, and whether it is painted."""

    id: str
    geometry: BaseGeometry
    painted: bool


@dataclass(frozen=True, slots=True)
class PaintedCrossing:
    """One generated crossing strip and what the map says about it."""

    site: CrossingSite
    evidence: tuple[str, ...]
    painted: bool
    bars: tuple[Polygon, ...] = ()

    @property
    def origin(self) -> str:
        return "observed" if self.evidence else "generated"


@dataclass(frozen=True, slots=True)
class Markings:
    crossings: tuple[PaintedCrossing, ...] = ()
    unmatched: tuple[str, ...] = field(default=())

    @property
    def bars(self) -> list[Polygon]:
        return [bar for crossing in self.crossings for bar in crossing.bars]


def is_crossing(kind: str, tags: Mapping[str, str]) -> bool:
    """True for an OSM node or way that marks where pedestrians cross a road."""
    if "railway" in tags:
        return False
    if kind == "poi":
        return tags.get("highway") == "crossing" or "crossing" in tags
    if kind == "highway":
        return any(tags.get(key) == "crossing" for key in CROSSING_WAY_KEYS)
    return False


def is_painted(tags: Mapping[str, str]) -> bool:
    """False when the tags say the road has no marking there."""
    markings = tags.get("crossing:markings")
    if markings is not None:
        return markings != "no"
    return tags.get("crossing") not in UNPAINTED


def crossing_evidence(features: Sequence[Mapping[str, Any]]) -> list[CrossingEvidence]:
    """The crossings among geo scene ``features`` (local metres), sorted by id."""
    evidence = [
        CrossingEvidence(str(f["id"]), shape(f["geometry"]), is_painted(f.get("tags") or {}))
        for f in features
        if is_crossing(str(f.get("kind")), f.get("tags") or {})
    ]
    return sorted(evidence, key=lambda e: e.id)


def zebra_bars(site: CrossingSite, carriageway: BaseGeometry) -> list[Polygon]:
    """The bars of one crossing, inside ``carriageway``.

    The kerb-to-kerb run is the piece of the strip's axis on the carriageway
    that holds the centre. The bars are centred on it, ``ZEBRA_KERB_M`` clear
    of each kerb. A bar that a curved kerb would cut is left out, so every bar
    stays a quad.
    """
    (x, y), (ux, uy) = site.centre, site.along
    vx, vy = uy, -ux
    half = site.span_m / 2.0
    axis = LineString([(x - vx * half, y - vy * half), (x + vx * half, y + vy * half)])
    run = next((part for part in shapely.get_parts(axis.intersection(carriageway))
                if isinstance(part, LineString) and part.distance(Point(x, y)) <= JUNCTION_TOLERANCE_M), None)
    if run is None:
        return []
    ends = [(px - x) * vx + (py - y) * vy for px, py in run.coords]
    low, high = min(ends) + ZEBRA_KERB_M, max(ends) - ZEBRA_KERB_M
    pitch = ZEBRA_BAR_M + ZEBRA_GAP_M
    count = math.floor((high - low + ZEBRA_GAP_M) / pitch)
    first = (low + high) / 2.0 - (count * pitch - ZEBRA_GAP_M) / 2.0
    width = CROSSING_WIDTH_M / 2.0
    bars = []
    for i in range(count):
        a, b = first + i * pitch, first + i * pitch + ZEBRA_BAR_M
        bar = Polygon([
            (x + vx * a - ux * width, y + vy * a - uy * width),
            (x + vx * b - ux * width, y + vy * b - uy * width),
            (x + vx * b + ux * width, y + vy * b + uy * width),
            (x + vx * a + ux * width, y + vy * a + uy * width),
        ])
        if bar.difference(carriageway).area <= OUTSIDE_TOLERANCE_M2:
            bars.append(bar)
    return bars


def paint_crossings(sites: Sequence[CrossingSite], carriageway: BaseGeometry, evidence: Sequence[CrossingEvidence]) -> Markings:
    """Match each crossing strip on ``carriageway`` with the OSM crossings near it, and paint the observed ones.

    A strip is painted when at least one matching crossing is painted. A strip
    that overlaps one already painted is not painted again, so no two bars
    share a plane.
    """
    tree = STRtree([e.geometry for e in evidence])
    matched: set[str] = set()
    crossings: list[PaintedCrossing] = []
    painted_area: list[BaseGeometry] = []
    for site in sites:
        strip = site.strip().intersection(carriageway)
        if strip.is_empty:
            continue
        near = [evidence[int(i)] for i in sorted(tree.query(strip, predicate="dwithin", distance=ZEBRA_MATCH_M))]
        matched.update(e.id for e in near)
        painted = any(e.painted for e in near)
        bars: list[Polygon] = []
        if painted and not any(strip.intersection(other).area > OUTSIDE_TOLERANCE_M2 for other in painted_area):
            bars = zebra_bars(site, carriageway)
            painted_area.append(strip)
        crossings.append(PaintedCrossing(site, tuple(e.id for e in near), painted and bool(bars), tuple(bars)))
    unmatched = tuple(e.id for e in evidence if e.id not in matched)
    return Markings(tuple(crossings), unmatched)


def summary(markings: Markings) -> dict[str, int]:
    """Counts for the generation report: strips by origin and paint, bars, and unmatched OSM crossings."""
    crossings = markings.crossings
    return {
        "strips": len(crossings),
        "observed": sum(c.origin == "observed" for c in crossings),
        "observed_painted": sum(c.painted for c in crossings),
        "observed_unpainted": sum(c.origin == "observed" and not c.painted for c in crossings),
        "generated_unpainted": sum(c.origin == "generated" for c in crossings),
        "bars": len(markings.bars),
        "osm_crossings_unmatched": len(markings.unmatched),
    }
