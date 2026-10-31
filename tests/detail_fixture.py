"""Synthetic street block for phase 04 tests (CC0-1.0; not OpenStreetMap data).

The OSM XML is produced at test time from local coordinates (metres, x east,
y north) around ``MURCIA_CENTER`` with the same projection the pipeline uses,
so every position below is exact by construction.

Layout: a residential street along y = 0 (8 m wide, so the carriageway is
y ∈ [−4, 4] and the generated sidewalk y ∈ [4, 6]); a row of three touching
buildings north of it (party walls at x = −15 and x = 0); a church; a small shed
(hip-roof candidate); a shop POI in building 312; trees, a bollard and a bicycle
rack on the sidewalk, and one tree inside building 311 (must be rejected).
"""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import quoteattr

from shapely import Point

from onward_generator.geo.projection import LocalFrame

BUILDINGS: dict[int, tuple[list[tuple[float, float]], dict[str, str]]] = {
    311: ([(-30, 6), (-15, 6), (-15, 18), (-30, 18)], {"building": "apartments", "building:levels": "4"}),
    312: ([(-15, 6), (0, 6), (0, 22), (-15, 22)], {"building": "yes", "building:levels": "5"}),
    313: ([(0, 6), (14, 6), (14, 20), (0, 20)], {"building": "residential", "building:levels": "3"}),
    314: ([(30, 6), (50, 6), (50, 30), (30, 30)], {"building": "church", "name": "Iglesia Sintética"}),
    315: ([(-60, 30), (-54, 30), (-54, 35), (-60, 35)], {"building": "shed"}),
}
ROAD = ([(-90, 0), (90, 0)], {"highway": "residential", "width": "8"})
POIS: dict[int, tuple[tuple[float, float], dict[str, str]]] = {
    401: ((-7, 7), {"shop": "bakery"}),
    402: ((-20, 5), {"natural": "tree"}),
    403: ((-25, 12), {"natural": "tree"}),
    404: ((20, 5), {"barrier": "bollard"}),
    405: ((62, 5), {"amenity": "bicycle_parking"}),
    406: ((40, 18), {"man_made": "tower", "tower:type": "bell_tower", "height": "40", "name": "Torre Sintética"}),
}


# A second block for per-building facades and doors: six touching buildings on one street.
# 602 holds a school POI, 603 a bank POI, 606 a shop POI. 604 has a measured height whose storeys
# (8 m / 3 = 2.67 m) are too low for the 2.4 m template door. 605 is 2 m wide between two
# neighbours, so no wall reaches the 2.5 m street-facing minimum. 606 is a shop with 604's low
# storeys: its door stays with the shopfront rules.
ROW_BUILDINGS: dict[int, tuple[list[tuple[float, float]], dict[str, str]]] = {
    601: ([(-40, 6), (-30, 6), (-30, 18), (-40, 18)], {"building": "apartments", "building:levels": "4"}),
    602: ([(-30, 6), (-20, 6), (-20, 18), (-30, 18)], {"building": "yes", "building:levels": "3"}),
    603: ([(-20, 6), (-10, 6), (-10, 18), (-20, 18)], {"building": "yes", "building:levels": "3"}),
    604: ([(-10, 6), (0, 6), (0, 18), (-10, 18)], {"building": "yes", "building:levels": "3", "height": "8"}),
    605: ([(0, 6), (2, 6), (2, 18), (0, 18)], {"building": "yes", "building:levels": "3"}),
    606: ([(2, 6), (14, 6), (14, 18), (2, 18)], {"building": "yes", "building:levels": "3", "height": "8"}),
}
ROW_POIS: dict[int, tuple[tuple[float, float], dict[str, str]]] = {
    701: ((-25, 12), {"amenity": "school", "name": "Colegio Sintético"}),
    702: ((-15, 12), {"amenity": "bank"}),
    703: ((8, 7), {"shop": "bakery"}),
}


# A block tagged with the Simple 3D Buildings scheme. 801 is gabled with a roof height and a metal roof; 802
# (1 080 m², 12 m over 3 storeys) is hipped with one 4 m roof level; 803 is a brick L with a skillion facing south;
# 805 has a roof shape nothing draws; 808 is tagged flat, 809 is a second gabled roof and 822 is half-hipped. 811–816 are small
# untagged houses, some of which get the estimated hip, and 817–821 small untagged L-shapes. 806 is industrial (6 m storeys, 12 m) and carries
# building:part ways (geo keeps them as closed lines): a tower of 8 + 1 levels with a pyramidal roof (901),
# one level hidden inside the building (902), a brick part from 10 to 20 m with a 4 m gabled metal roof (903), one
# with no height or levels (904), a flat 15 m part (905), one whose base is above its top (906), one whose
# 15 m roof is cut to fit (908), one with no room for a roof (909) and a triangle (910). 907 stands outside
# every building.
S3DB_BUILDINGS: dict[int, tuple[list[tuple[float, float]], dict[str, str]]] = {
    801: ([(-40, 8), (-28, 8), (-28, 16), (-40, 16)], {"building": "house", "building:levels": "2", "roof:shape": "gabled",
                                                       "roof:height": "3", "roof:material": "metal"}),
    802: ([(-20, 8), (16, 8), (16, 38), (-20, 38)], {"building": "yes", "building:levels": "3", "height": "12", "roof:shape": "hipped",
                                                     "roof:levels": "1"}),
    803: ([(30, 8), (42, 8), (42, 14), (36, 14), (36, 20), (30, 20)], {"building": "yes", "building:levels": "2", "roof:shape": "skillion",
                                                                     "roof:direction": "S", "roof:height": "2", "building:material": "brick"}),
    805: ([(-40, 30), (-30, 30), (-30, 40), (-40, 40)], {"building": "yes", "building:levels": "2", "roof:shape": "crosspitched"}),
    808: ([(0, 50), (8, 50), (8, 56), (0, 56)], {"building": "yes", "building:levels": "2", "roof:shape": "flat"}),
    809: ([(20, 50), (28, 50), (28, 56), (20, 56)], {"building": "yes", "building:levels": "2", "roof:shape": "gabled"}),
    822: ([(36, 50), (44, 50), (44, 56), (36, 56)], {"building": "yes", "building:levels": "2", "roof:shape": "half-hipped"}),
    **{811 + k: ([(-90 + 8 * k, 70), (-85 + 8 * k, 70), (-85 + 8 * k, 74), (-90 + 8 * k, 74)], {"building": "house", "building:levels": "1"})
       for k in range(6)},
    # Small untagged L-shapes: not convex, so no estimated hip; 817 is a shed (the minor template), flat when not convex.
    **{817 + k: ([(-90 + 10 * k, 80), (-84 + 10 * k, 80), (-84 + 10 * k, 83), (-87 + 10 * k, 83), (-87 + 10 * k, 86), (-90 + 10 * k, 86)],
                 {"building": "shed" if k == 0 else "house", "building:levels": "1"}) for k in range(5)},
    806: ([(50, 8), (70, 8), (70, 28), (50, 28)], {"building": "industrial", "building:levels": "2"}),
    901: ([(52, 10), (58, 10), (58, 16), (52, 16)], {"building:part": "yes", "building:levels": "8", "roof:levels": "1", "roof:shape": "pyramidal"}),
    902: ([(60, 10), (68, 10), (68, 18), (60, 18)], {"building:part": "yes", "building:levels": "1"}),
    903: ([(60, 20), (68, 20), (68, 26), (60, 26)], {"building:part": "yes", "height": "20", "min_height": "10", "roof:shape": "gabled",
                                                     "roof:height": "4", "building:material": "brick", "roof:material": "metal"}),
    904: ([(52, 20), (56, 20), (56, 24), (52, 24)], {"building:part": "yes"}),
    905: ([(52, 24.5), (56, 24.5), (56, 27), (52, 27)], {"building:part": "yes", "height": "15"}),
    906: ([(57, 24), (59, 24), (59, 27), (57, 27)], {"building:part": "yes", "height": "25", "min_height": "30"}),
    907: ([(75, 10), (78, 10), (78, 13), (75, 13)], {"building:part": "yes", "height": "20"}),
    908: ([(57, 17), (59, 17), (59, 23), (57, 23)], {"building:part": "yes", "height": "20", "min_height": "10", "roof:shape": "skillion",
                                                     "roof:height": "15"}),
    909: ([(68.5, 26.5), (70, 26.5), (70, 28), (68.5, 28)], {"building:part": "yes", "height": "13", "min_height": "12.6",
                                                             "roof:shape": "pyramidal"}),
    910: ([(52, 17), (55, 17), (52, 19.5)], {"building:part": "yes", "height": "14"}),
}
# A gabled building of two separate outer rings: no single roof fits it.
S3DB_RELATIONS = {
    807: ([([(-40, 50), (-34, 50), (-34, 56), (-40, 56)], "outer"), ([(-30, 50), (-24, 50), (-24, 56), (-30, 56)], "outer")],
          {"building": "yes", "building:levels": "2", "roof:shape": "gabled"}),
}


# A multipolygon building: (ring, role) members, role "outer" or "inner", and the relation tags.
Relations = dict[int, tuple[list[tuple[list[tuple[float, float]], str]], dict[str, str]]]


def write_block_osm(
    path: Path,
    origin: tuple[float, float],
    buildings: dict[int, tuple[list[tuple[float, float]], dict[str, str]]] = BUILDINGS,
    pois: dict[int, tuple[tuple[float, float], dict[str, str]]] = POIS,
    relations: Relations | None = None,
) -> Path:
    frame = LocalFrame.create(origin[0], origin[1])
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<osm version="0.6" generator="onward-synthetic-fixture">',
             '  <meta osm_base="2026-10-18T00:00:00Z"/>']
    node_id = 1000

    def node(x: float, y: float, tags: dict[str, str] | None = None) -> int:
        nonlocal node_id
        node_id += 1
        p = frame.to_wgs84(Point(x, y))
        attrs = f'id="{node_id}" version="1" lat="{p.y:.8f}" lon="{p.x:.8f}"'
        if not tags:
            lines.append(f"  <node {attrs}/>")
        else:
            lines.append(f"  <node {attrs}>")
            lines.extend(f"    <tag k={quoteattr(k)} v={quoteattr(v)}/>" for k, v in tags.items())
            lines.append("  </node>")
        return node_id

    ways: list[str] = []

    def way(wid: int, refs: list[int], tags: dict[str, str]) -> None:
        ways.append(f'  <way id="{wid}" version="1">')
        ways.append("    " + "".join(f'<nd ref="{r}"/>' for r in refs))
        ways.extend(f"    <tag k={quoteattr(k)} v={quoteattr(v)}/>" for k, v in tags.items())
        ways.append("  </way>")

    for wid, (ring, tags) in buildings.items():
        refs = [node(x, y) for x, y in ring]
        way(wid, [*refs, refs[0]], tags)
    way(301, [node(x, y) for x, y in ROAD[0]], ROAD[1])
    members: list[str] = []
    for rid, (rings, tags) in (relations or {}).items():
        members.append(f'  <relation id="{rid}" version="1">')
        for k, (ring, role) in enumerate(rings):
            refs = [node(x, y) for x, y in ring]
            way(rid * 10 + k, [*refs, refs[0]], {})
            members.append(f'    <member type="way" ref="{rid * 10 + k}" role="{role}"/>')
        members.extend(f"    <tag k={quoteattr(k)} v={quoteattr(v)}/>" for k, v in {"type": "multipolygon", **tags}.items())
        members.append("  </relation>")
    for _pid, ((x, y), tags) in pois.items():
        node(x, y, tags)
    lines += ways + members
    lines.append("</osm>")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
