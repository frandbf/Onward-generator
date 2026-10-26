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


def write_block_osm(path: Path, origin: tuple[float, float]) -> Path:
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

    for wid, (ring, tags) in BUILDINGS.items():
        refs = [node(x, y) for x, y in ring]
        way(wid, [*refs, refs[0]], tags)
    way(301, [node(x, y) for x, y in ROAD[0]], ROAD[1])
    for _pid, ((x, y), tags) in POIS.items():
        node(x, y, tags)
    lines += ways
    lines.append("</osm>")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
