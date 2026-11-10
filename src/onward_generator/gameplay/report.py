"""``gameplay_report.json``: routes, sightlines, cover and balance of a playable layout (ADR-032).

A report only. It reads the playable ``layout.json`` document, the base partition,
the detail manifest and the collision GLB of the playable directory, and changes
none of them. Every proposed check states pass or fail and never stops a stage or
an export: the thresholds are uncalibrated until a playtest (plan §1, 4.2).
"""

from __future__ import annotations

import json
import struct
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
from shapely import LineString, Point
from shapely.geometry.base import BaseGeometry
from shapely.ops import substring

from onward_generator.gameplay.headline import REPORT_FILE, headline_text
from onward_generator.gameplay.metrics import (
    CORRIDOR_M,
    COVER_REACH_M,
    GRID_M,
    GROUND_CONTACT_M,
    MAX_CONTACT_DIFF_PCT,
    MAX_OVERLAP,
    MAX_ROUTES,
    MAX_SIGHTLINE_M,
    MIN_ROUTES,
    RAY_CAP_M,
    RAY_COUNT,
    ROUTE_PENALTY,
    RUN_SPEED_M_S,
    SAMPLE_STEP_M,
    SIGHT_BLOCK_HEIGHT_M,
    SNAP_M,
    STRETCH_M,
    Collider,
    CoverIndex,
    GameplayError,
    NavGraph,
    Route,
    SightCaster,
    balance,
    blocks_sight,
    collider_kind,
    dijkstra,
    distinct_routes,
    nav_graph,
    overlap,
    per_100m,
    route_points,
    route_samples,
    route_stretches,
    sight_segments,
    snap,
)
from onward_generator.generation.gltf import nodes_from_glb

REPORT_SCHEMA = "onward_generator.gameplay_report"
REPORT_VERSION = "0.1.0"
LAYOUT_SCHEMA = "onward_generator.gameplay_layout"
LAYOUT_VERSION = "0.3.0"
BLOCKS = ("attackers", "defenders", "attackers_b", "defenders_b")
PAIRS = (("attackers", "defenders"), ("attackers_b", "defenders_b"))
TEAMS = {"attackers": ("attackers", "attackers_b"), "defenders": ("defenders", "defenders_b")}
NOTE = (
    "Estimated on a 2D grid over the navmesh islands of perf layout (free ground and crossings, inside the core, "
    "shrunk by the 0.35 m agent radius). The navmesh the adapter bakes in Unity is the real one (docs/11-onward-sdk.md). "
    "Collider boxes are axis-aligned bounds of the collision GLB. The thresholds are proposals, uncalibrated until a "
    "playtest, and never block a stage or an export (ADR-032)."
)
__all__ = [
    "REPORT_FILE", "build_report", "gameplay_report", "headline_text", "parameters", "proposed_checks", "read_colliders", "report_text",
]
_BLOCKING_STATUSES = ("spawn_missing", "spawn_off_navmesh", "uplink_off_navmesh", "no_path")


def read_colliders(path: str | Path) -> list[Collider]:
    """The collision GLB's nodes as boxes in local metres, sorted by name. A file that is not a GLB is a ValueError."""
    data = Path(path).expanduser().read_bytes()
    try:
        nodes, _ = nodes_from_glb(data)
    except (ValueError, KeyError, IndexError, TypeError, struct.error, UnicodeDecodeError) as exc:
        raise ValueError(f"{path}: not a readable collision GLB ({exc})") from exc
    colliders: list[Collider] = []
    for node in nodes:
        meshes = [prim.mesh.vertices for prim in node.world_primitives() if len(prim.mesh.vertices)]
        if not meshes:
            continue
        vertices = np.vstack(meshes)
        low, high = vertices.min(axis=0), vertices.max(axis=0)
        kind = collider_kind(node.name, node.extras.get("type"))
        colliders.append(Collider(
            node.name, kind,
            (float(low[0]), float(low[1]), float(high[0]), float(high[1])),
            float(low[2]), float(high[2]),
        ))
    return sorted(colliders, key=lambda c: (c.name, c.bounds))


def _centre(layout: Mapping[str, Any], block: str) -> tuple[float, float] | None:
    value = layout.get(block)
    centre = value.get("centre_m") if isinstance(value, Mapping) else None
    if not isinstance(centre, Sequence) or len(centre) < 2:
        return None
    return (float(centre[0]), float(centre[1]))


def _uplink(layout: Mapping[str, Any]) -> tuple[float, float] | None:
    value = layout.get("uplink_m")
    if not isinstance(value, Sequence) or len(value) < 2:
        return None
    return (float(value[0]), float(value[1]))


def _r(value: float) -> float:
    return round(float(value), 2)


def parameters() -> dict[str, Any]:
    return {
        "grid_m": GRID_M,
        "snap_m": SNAP_M,
        "max_routes": MAX_ROUTES,
        "route_penalty": ROUTE_PENALTY,
        "max_overlap": MAX_OVERLAP,
        "corridor_m": CORRIDOR_M,
        "sample_step_m": SAMPLE_STEP_M,
        "stretch_m": STRETCH_M,
        "cover_reach_m": COVER_REACH_M,
        "sight_block_height_m": SIGHT_BLOCK_HEIGHT_M,
        "ground_contact_m": GROUND_CONTACT_M,
        "ray_count": RAY_COUNT,
        "ray_cap_m": RAY_CAP_M,
        "run_speed_m_s": RUN_SPEED_M_S,
    }


def _empty_headline() -> dict[str, Any]:
    return {
        "distinct_routes": None,
        "max_overlap_pct": None,
        "longest_open_sight_m": None,
        "cover_per_100m_min_stretch": None,
        "contact_diff_pct": None,
    }


def proposed_checks(headline: Mapping[str, Any]) -> dict[str, Any]:
    def result(value: Any, ok: bool) -> str | None:
        return None if value is None else ("pass" if ok else "fail")

    routes = headline["distinct_routes"]
    sight = headline["longest_open_sight_m"]
    diff = headline["contact_diff_pct"]
    return {
        "distinct_routes": {
            "proposal": f">= {MIN_ROUTES} routes per spawn pair, no two sharing more than {MAX_OVERLAP:.0%}",
            "value": routes, "result": result(routes, routes is not None and routes >= MIN_ROUTES),
        },
        "longest_open_sight_m": {
            "proposal": f"<= {MAX_SIGHTLINE_M:g} m, adjustable per mode",
            "value": sight, "result": result(sight, sight is not None and sight <= MAX_SIGHTLINE_M),
        },
        "contact_diff_pct": {
            "proposal": f"<= {MAX_CONTACT_DIFF_PCT:g} %",
            "value": diff, "result": result(diff, diff is not None and diff <= MAX_CONTACT_DIFF_PCT),
        },
        "cover_per_100m": {
            "proposal": "N cover colliders per 100 m of route, fixed after the first measurement",
            "value": headline["cover_per_100m_min_stretch"], "result": None,
        },
        "blocking": False,
    }


class _Sight:
    """Open sightlines at route samples, cached by position: overlapping routes share samples."""

    def __init__(self, caster: SightCaster, cover: CoverIndex) -> None:
        self.caster = caster
        self.cover = cover
        self.cache: dict[tuple[float, float], dict[str, Any] | None] = {}

    def at(self, point: tuple[float, float]) -> dict[str, Any] | None:
        """None when cover lies within reach; else the longest ray and its end."""
        key = (round(point[0], 3), round(point[1], 3))  # pragma: no mutate: a cache key; samples are metres apart
        if key not in self.cache:
            if self.cover.near(Point(point)):
                self.cache[key] = None
            else:
                lengths = self.caster.lengths(point)
                ray = int(np.argmax(lengths))
                length = float(lengths[ray])
                dx, dy = self.caster.directions[ray]
                self.cache[key] = {
                    "length_m": _r(length),
                    "from_m": [_r(point[0]), _r(point[1])],
                    "to_m": [_r(point[0] + dx * length), _r(point[1] + dy * length)],
                }
        return self.cache[key]


def _route_doc(graph: NavGraph, route: Route, index: int, sight: _Sight, cover: CoverIndex) -> dict[str, Any]:
    corners = route_points(graph, route)
    length = route.length_mm / 1000.0
    # The first corner twice: a route between two spawns on one node is then a line of no length.
    line = LineString([corners[0], *corners])
    samples: list[tuple[float, dict[str, Any] | None]] = []
    for distance in route_samples(length):
        point = line.interpolate(distance)
        samples.append((distance, sight.at((float(point.x), float(point.y)))))
    stretches = []
    for start, end in route_stretches(length):
        piece = substring(line, start, end)
        near = cover.near(piece)
        # Samples are whole multiples of 5 m and stretch ends multiples of 25 m or the route's end: exact.
        open_here = [s for d, s in samples if s is not None and start <= d <= end]
        longest = max(open_here, key=lambda s: (s["length_m"], s["from_m"], s["to_m"])) if open_here else None
        stretches.append({
            "start_m": _r(start), "end_m": _r(end),
            "cover": len(near),
            "cover_per_100m": per_100m(len(near), end - start),
            "longest_open_sight_m": longest["length_m"] if longest else None,
        })
    open_samples = [s for _, s in samples if s is not None]
    return {
        "index": index,
        "attempt": route.attempt,
        "length_m": _r(length),
        "points_m": [[_r(x), _r(y)] for x, y in corners],
        "samples": len(samples),
        "open_samples": len(open_samples),
        "stretches": stretches,
    }


def build_report(
    islands: Sequence[BaseGeometry],
    buildings: BaseGeometry,
    colliders: Sequence[Collider],
    layout: Mapping[str, Any],
) -> dict[str, Any]:
    """The report document. Missing spawns, a spawn off the navmesh or no path are statuses, not exceptions."""
    if layout.get("schema") != LAYOUT_SCHEMA or layout.get("schema_version") != LAYOUT_VERSION:
        raise GameplayError(
            f"the gameplay report reads a {LAYOUT_SCHEMA} {LAYOUT_VERSION} layout, "
            f"not {layout.get('schema')!r} {layout.get('schema_version')!r}"
        )
    graph = nav_graph(islands)
    cover = CoverIndex.of(colliders)
    doc: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "schema_version": REPORT_VERSION,
        "provenance": "estimated",
        "note": NOTE,
        "parameters": parameters(),
        "graph": {"nodes": graph.size, "edges": sum(len(n) for n in graph.neighbours) // 2},
        "colliders": {
            "cover": len(cover.colliders),
            "sight_blockers": sum(1 for c in colliders if blocks_sight(c)),
            "ignored_ground": sum(1 for c in colliders if c.kind == "ground"),
            "ignored_buildings": sum(1 for c in colliders if c.kind == "building"),
        },
    }
    problems: list[tuple[str, str]] = []
    nodes: dict[str, int] = {}
    for block in BLOCKS:
        centre = _centre(layout, block)
        if centre is None:
            problems.append(("spawn_missing", block))
            continue
        node = snap(graph, centre)
        if node is None:
            problems.append(("spawn_off_navmesh", f"{block} at {_r(centre[0])}, {_r(centre[1])}"))
            continue
        nodes[block] = node
    uplink = _uplink(layout)
    uplink_node = snap(graph, uplink) if uplink is not None else None
    if uplink is None:
        problems.append(("spawn_missing", "uplink_m"))
    elif uplink_node is None:
        problems.append(("uplink_off_navmesh", f"{_r(uplink[0])}, {_r(uplink[1])}"))

    pairs: list[dict[str, Any]] = []
    found: dict[tuple[str, str], list[Route]] = {}
    for a, b in PAIRS:
        if a not in nodes or b not in nodes:
            continue
        routes = distinct_routes(graph, nodes[a], nodes[b])
        if not routes:
            problems.append(("no_path", f"{a} to {b}"))
        found[(a, b)] = routes

    distances: dict[str, float] = {}
    if uplink_node is not None:
        best, _ = dijkstra(graph, uplink_node)
        for team, blocks in TEAMS.items():
            reach = [best[nodes[block]] for block in blocks if block in nodes]
            known = [value for value in reach if value is not None]
            if known:
                distances[team] = min(known) / 1000.0
            elif reach:
                problems.append(("no_path", f"{team} to uplink_m"))

    blocking = any(status in _BLOCKING_STATUSES for status, _ in problems)
    if not cover.colliders:
        problems.append(("no_cover", "the collision GLB has no cover collider"))
    doc["status"] = problems[0][0] if problems else "ok"
    doc["problems"] = [f"{status}: {detail}" for status, detail in problems]
    if blocking:
        doc |= {"pairs": [], "sightline": None, "balance": None, "headline": _empty_headline()}
        doc["proposed_checks"] = proposed_checks(doc["headline"])
        return doc

    caster = SightCaster(sight_segments(buildings, colliders))
    sight = _Sight(caster, cover)
    overlaps_all: list[float] = []
    stretch_cover: list[float] = []
    for (a, b), routes in found.items():
        route_docs = [_route_doc(graph, route, index, sight, cover) for index, route in enumerate(routes)]
        shares = [(i, j, round(overlap(graph, routes[i], routes[j]), 4)) for i in range(len(routes)) for j in range(i + 1, len(routes))]
        overlaps = [{"routes": [i, j], "overlap": share} for i, j, share in shares]
        overlaps_all.extend(share for _, _, share in shares)
        stretch_cover.extend(s["cover_per_100m"] for r in route_docs for s in r["stretches"])
        pairs.append({"from": a, "to": b, "routes": route_docs, "overlaps": overlaps})
    open_points = [value for value in sight.cache.values() if value is not None]
    # The longest; on a tie, the larger start, so the choice does not depend on the order of the routes.
    longest = max(open_points, key=lambda value: (value["length_m"], value["from_m"]), default=None)
    times = balance(distances["attackers"], distances["defenders"])
    doc["pairs"] = pairs
    doc["sightline"] = longest
    doc["balance"] = {
        "contact_point": "uplink_m",
        "attackers_m": _r(distances["attackers"]),
        "defenders_m": _r(distances["defenders"]),
        **times,
    }
    doc["headline"] = {
        "distinct_routes": min(len(routes) for routes in found.values()),
        "max_overlap_pct": round(max(overlaps_all, default=0.0) * 100.0, 2),  # pragma: no mutate: overlaps have 4 places, so 2 after x100 are exact
        "longest_open_sight_m": longest["length_m"] if longest else 0.0,
        "cover_per_100m_min_stretch": min(stretch_cover) if stretch_cover else None,
        "contact_diff_pct": times["diff_pct"],
    }
    doc["proposed_checks"] = proposed_checks(doc["headline"])
    return doc


def gameplay_report(base: Any, manifest: Mapping[str, Any], layout: Mapping[str, Any], collision_path: str | Path) -> dict[str, Any]:
    """The report for a playable layout: ``base`` is the phase 03 scene, ``manifest`` the playable detail manifest."""
    from onward_generator.perf.layout import nav_islands, playable_area

    colliders = read_colliders(collision_path)
    islands = nav_islands(base, playable_area(manifest))
    return build_report(islands, base.partition.buildings, colliders, layout)


def report_text(report: Mapping[str, Any]) -> str:
    """Canonical JSON of the report: sorted keys, indent 2, one trailing newline."""
    return json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False) + "\n"  # pragma: no mutate: ensure_ascii=None is false, as False
