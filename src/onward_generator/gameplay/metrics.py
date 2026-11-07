"""Gameplay metrics on the playable layout, report only (ADR-032).

Pure functions on local metres (x east, y north). The navigation graph is a
grid over the 2D navmesh islands of ``perf.layout.nav_islands``: an estimate
of what the adapter bakes, not the baked mesh itself (``docs/11-onward-sdk.md``).
Every value here is estimated and uncalibrated until a playtest (plan 4.2).

Only shapely, numpy and heapq are used. Costs are integer millimetres and ties
break on the node index, so two runs give the same routes.
"""

from __future__ import annotations

import heapq
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import shapely
from shapely import STRtree, contains_xy, prepare, union_all
from shapely.geometry.base import BaseGeometry

from onward_generator.generation.mesh import polygons_of
from onward_generator.overgrow.ambient import COVER_DEPTH_M, COVER_HEIGHT_M

GRID_M = 2.0
"""Node spacing of the route graph."""
SNAP_M = GRID_M
"""A spawn or the Uplink snaps to the nearest node at most this far (inclusive)."""
MAX_ROUTES = 6
"""Shortest-path attempts per spawn pair; also the most distinct routes a pair can report."""
ROUTE_PENALTY = 3
"""Each attempt multiplies the cost of the edges it used by this factor."""
MAX_OVERLAP = 0.5
"""A candidate route is distinct when it shares at most this fraction of the shorter route with every kept one."""
CORRIDOR_M = 8.0
"""An edge of one route within this distance of another route is shared with it: two lanes of one street are one route."""
SAMPLE_STEP_M = 5.0
"""Route samples for sightlines and cover, this far apart along the route."""
STRETCH_M = 25.0
"""Length of a route stretch in the cover report."""
COVER_REACH_M = 1.5
"""A sample or a stretch has cover when a cover collider lies within this distance."""
SIGHT_BLOCK_HEIGHT_M = 1.2
"""A collider at least this tall blocks a line of sight at chest height."""
GROUND_CONTACT_M = 0.5
"""A collider whose bottom is higher than this is not at street level: neither cover nor a sight blocker."""
RAY_COUNT = 72
"""Rays per sample, every 360 / 72 = 5 degrees."""
RAY_CAP_M = 300.0
"""A ray that hits nothing stops here."""
RAY_PIECE_M = 10.0
"""Rays are queried in pieces this long so the spatial index stays selective; the hit distance is exact."""
RUN_SPEED_M_S = 4.0
"""Estimated running speed for the time to first contact."""
MAX_SIGHTLINE_M = 60.0
"""Proposed threshold (plan §1): the longest open sightline along the routes."""
MIN_ROUTES = 3
"""Proposed threshold (plan §1): distinct routes between the spawns."""
MAX_CONTACT_DIFF_PCT = 15.0
"""Proposed threshold (plan §1): difference between the teams' times to first contact."""

Edge = tuple[int, int]


class GameplayError(ValueError):
    """An argument no metric can be computed from."""


@dataclass(frozen=True, slots=True, eq=False)
class NavGraph:
    """Nodes (``xy``, metres) and, for each node, its ``(neighbour, cost_mm)`` pairs sorted by neighbour."""

    xy: np.ndarray
    neighbours: tuple[tuple[tuple[int, int], ...], ...]

    @property
    def size(self) -> int:
        return len(self.neighbours)

    def cost(self, a: int, b: int) -> int:
        for other, cost in self.neighbours[a]:
            if other == b:
                return cost
        raise GameplayError(f"nodes {a} and {b} are not linked")


def from_edges(xy: Sequence[tuple[float, float]], edges: Mapping[Edge, int]) -> NavGraph:
    """A graph from node coordinates and undirected edge costs in millimetres."""
    links: list[list[tuple[int, int]]] = [[] for _ in xy]
    for (a, b), cost in edges.items():
        if a == b or cost <= 0:
            raise GameplayError(f"edge {a}-{b} must join two nodes at a positive cost")
        links[a].append((b, int(cost)))
        links[b].append((a, int(cost)))
    coords = np.asarray(xy, dtype=np.float64).reshape(-1, 2)  # pragma: no mutate: numpy reads any negative size as the unknown dimension
    return NavGraph(coords, tuple(tuple(sorted(link)) for link in links))


def nav_graph(islands: Sequence[BaseGeometry], grid_m: float = GRID_M) -> NavGraph:
    """A grid every ``grid_m`` on the navmesh islands, aligned to ``floor(bounds / grid_m)``.

    A node lies inside an island. Eight neighbours; an edge is kept when the
    whole segment is on the islands, and a diagonal also needs both orthogonal
    neighbours, so no edge cuts an inner corner. Ladders and lifts are not on
    the islands (ADR-029). Nodes are numbered south to north, then west to east.
    """
    if not grid_m > 0:
        raise GameplayError(f"grid_m must be greater than 0, got {grid_m}")
    parts = [part for part in islands if not part.is_empty]
    if not parts:
        raise GameplayError("the navmesh has no island")
    ground = union_all(parts)
    prepare(ground)  # pragma: no mutate: only makes the containment tests faster
    minx, miny, maxx, maxy = ground.bounds
    x0, y0 = math.floor(minx / grid_m) * grid_m, math.floor(miny / grid_m) * grid_m
    columns = int(math.floor((maxx - x0) / grid_m)) + 1  # pragma: no mutate: more columns only add points past the bounds, off every island
    rows = int(math.floor((maxy - y0) / grid_m)) + 1  # pragma: no mutate: more rows only add points past the bounds, off every island
    gx, gy = np.meshgrid(x0 + grid_m * np.arange(columns), y0 + grid_m * np.arange(rows))
    inside = contains_xy(ground, gx.ravel(), gy.ravel()).reshape(rows, columns)
    index = np.full((rows, columns), -1)  # pragma: no mutate: any negative value marks a cell without a node
    index[inside] = np.arange(int(inside.sum()))
    straight = round(grid_m * 1000)
    diagonal = round(grid_m * math.sqrt(2) * 1000)
    edges: dict[Edge, int] = {}
    for di, dj in ((1, 0), (0, 1), (1, 1), (-1, 1)):
        for j in range(rows - dj):
            # Column 0 lies on or west of the ground's west edge, so it never holds a node.
            for i in range(max(0, -di), columns - max(0, di)):  # pragma: no mutate: column 0 never holds a node
                a, b = int(index[j, i]), int(index[j + dj, i + di])
                if a < 0 or b < 0:  # pragma: no mutate: b is north or east of a node, so never node 0
                    continue
                if di and dj and (index[j, i + di] < 0 or index[j + dj, i] < 0):  # pragma: no mutate: a node to the north is never node 0
                    continue
                edges[(a, b)] = diagonal if di and dj else straight
    xy = np.column_stack([gx[inside], gy[inside]])
    if edges:
        keys = sorted(edges)
        segments = shapely.linestrings(np.stack([xy[[a for a, _ in keys]], xy[[b for _, b in keys]]], axis=1))
        on_ground = shapely.covers(ground, segments)
        edges = {key: edges[key] for key, keep in zip(keys, on_ground) if keep}
    return from_edges([(float(x), float(y)) for x, y in xy], edges)


def snap(graph: NavGraph, point: tuple[float, float], max_m: float = SNAP_M) -> int | None:
    """The nearest node to ``point`` within ``max_m`` (inclusive; the lower index on a tie), or None."""
    if graph.size == 0:
        return None
    distance = np.hypot(graph.xy[:, 0] - point[0], graph.xy[:, 1] - point[1])
    nearest = int(np.argmin(distance))
    return nearest if distance[nearest] <= max_m else None


def _key(a: int, b: int) -> Edge:
    return (min(a, b), max(a, b))


def dijkstra(graph: NavGraph, source: int, target: int | None = None, costs: Mapping[Edge, int] | None = None) -> tuple[list[int | None], list[int]]:
    """Distances in millimetres from ``source`` (None when unreachable) and predecessors (-1 for none).

    The heap orders on (cost, node index). ``costs`` overrides the cost of some edges.
    It stops at ``target`` once that node is settled.
    """
    best: list[int | None] = [None] * graph.size
    previous = [-1] * graph.size  # pragma: no mutate: a placeholder; only the predecessors of reached nodes are read
    done = [False] * graph.size
    best[source] = 0
    heap = [(0, source)]
    while heap:
        cost, node = heapq.heappop(heap)
        if done[node]:
            continue
        done[node] = True  # pragma: no mutate: only skips stale heap entries, which never improve a distance
        if node == target:
            break
        for other, base in graph.neighbours[node]:
            step = base if costs is None else costs.get(_key(node, other), base)
            total = cost + step
            known = best[other]
            if known is None or total < known:
                best[other] = total
                previous[other] = node
                heapq.heappush(heap, (total, other))
    return best, previous


def shortest_path(graph: NavGraph, start: int, goal: int, costs: Mapping[Edge, int] | None = None) -> list[int] | None:
    """Node indices from ``start`` to ``goal``, or None when no path joins them."""
    best, previous = dijkstra(graph, start, goal, costs)  # pragma: no mutate: stopping at the goal only saves time
    if best[goal] is None:
        return None
    path = [goal]
    for _ in range(graph.size):
        if path[-1] == start:
            return path[::-1]
        path.append(previous[path[-1]])
    raise GameplayError("the predecessors do not lead back to the start")  # pragma: no mutate: unreachable; Dijkstra's predecessors lead back to the start


def path_edges(path: Sequence[int]) -> list[Edge]:
    return [_key(a, b) for a, b in zip(path, path[1:])]


def path_length_mm(graph: NavGraph, path: Sequence[int]) -> int:
    return sum(graph.cost(a, b) for a, b in zip(path, path[1:]))


@dataclass(frozen=True, slots=True)
class Route:
    """A route between two spawns: its nodes, its length in millimetres and the attempt that found it (1-based)."""

    nodes: tuple[int, ...]
    length_mm: int
    attempt: int


def _near_edges(graph: NavGraph, edges: Sequence[Edge], path: Sequence[int], corridor_m: float) -> np.ndarray:
    """Whether the midpoint of each edge lies within ``corridor_m`` (inclusive) of ``path``, a path of two nodes or more."""
    mids = (graph.xy[[a for a, _ in edges]] + graph.xy[[b for _, b in edges]]) / 2.0
    return np.asarray(shapely.dwithin(shapely.linestrings(graph.xy[list(path)]), shapely.points(mids), corridor_m))


def overlap(graph: NavGraph, a: Route, b: Route, corridor_m: float = CORRIDOR_M) -> float:
    """The share of the shorter route that runs along the other one; 1.0 when the shorter route has no length.

    An edge of the shorter route (``a`` on a tie) is shared when its midpoint lies within
    ``corridor_m`` of the other route. The result is the cost of the shared edges over the
    shorter route's length.
    """
    short, other = (a, b) if a.length_mm <= b.length_mm else (b, a)
    if short.length_mm <= 0:
        return 1.0
    edges = path_edges(short.nodes)
    near = _near_edges(graph, edges, other.nodes, corridor_m)
    return sum(graph.cost(*edge) for edge, hit in zip(edges, near) if hit) / short.length_mm


def distinct_routes(
    graph: NavGraph,
    start: int,
    goal: int,
    *,
    attempts: int = MAX_ROUTES,
    penalty: int = ROUTE_PENALTY,
    max_overlap: float = MAX_OVERLAP,
    corridor_m: float = CORRIDOR_M,
) -> list[Route]:
    """Routes from ``start`` to ``goal`` found by an iterative edge penalty.

    Each attempt is the shortest path under the current costs; then every edge whose
    midpoint lies within ``corridor_m`` of it costs ``penalty`` times more, so the next
    attempt leaves that street rather than shifting one lane over. A candidate is kept
    when its ``overlap`` with every kept route is at most ``max_overlap``. An empty list
    means no path.
    """
    if start == goal:
        return [Route((start,), 0, 1)]
    every = sorted({_key(a, b) for a in range(graph.size) for b, _ in graph.neighbours[a]})
    penalised: dict[Edge, int] = {}
    kept: list[Route] = []
    for attempt in range(1, attempts + 1):
        path = shortest_path(graph, start, goal, penalised)
        if path is None:
            break
        candidate = Route(tuple(path), path_length_mm(graph, path), attempt)
        if all(overlap(graph, candidate, route, corridor_m) <= max_overlap for route in kept):
            kept.append(candidate)
        for edge, hit in zip(every, _near_edges(graph, every, path, corridor_m)):
            if hit:
                penalised[edge] = penalised.get(edge, graph.cost(*edge)) * penalty
    return kept


def route_points(graph: NavGraph, route: Route) -> list[tuple[float, float]]:
    """The route's corners: the first and last node and every node where the direction changes."""
    points = [tuple(float(v) for v in graph.xy[node]) for node in route.nodes]
    corners = [points[0]]
    for before, here, after in zip(points, points[1:], points[2:]):
        if (here[0] - before[0], here[1] - before[1]) != (after[0] - here[0], after[1] - here[1]):
            corners.append(here)
    if len(points) > 1:
        corners.append(points[-1])
    return [(x, y) for x, y in corners]


# ------------------------------------------------------------------------------------------------ colliders


@dataclass(frozen=True, slots=True)
class Collider:
    """A collision node's axis-aligned box in local metres and what it is for the report.

    ``kind`` is ``prop`` (may be cover and may block sight), ``wall`` (the perimeter wall:
    blocks sight, never cover), ``ground`` or ``building`` (both left out: buildings come
    from the partition footprints).
    """

    name: str
    kind: str
    bounds: tuple[float, float, float, float]
    min_z: float
    max_z: float


GROUND_COLLIDERS = frozenset({"collider/walkable", "collider/carriageway", "collider/walkable_crossing"})
BUILDING_PREFIXES = ("building/", "building_part/", "interior/")
WALL_PREFIX = "envelope/"


def collider_kind(name: str, node_type: object = None) -> str:
    """``ground``, ``building``, ``wall`` or ``prop`` from a collision node's name (without ``#collider``)."""
    object_id = name.partition("#")[0]
    if object_id in GROUND_COLLIDERS or node_type == "ground_collider":
        return "ground"
    if object_id.startswith(BUILDING_PREFIXES):
        return "building"
    if object_id.startswith(WALL_PREFIX):
        return "wall"
    return "prop"


def _at_street_level(collider: Collider) -> bool:
    return collider.min_z <= GROUND_CONTACT_M


def is_cover(collider: Collider) -> bool:
    """The overgrow cover rule (``overgrow.ambient.is_solid``): at least 0.5 m tall, 0.4 m on the short side."""
    if collider.kind != "prop" or not _at_street_level(collider):
        return False
    minx, miny, maxx, maxy = collider.bounds
    return collider.max_z >= COVER_HEIGHT_M and min(maxx - minx, maxy - miny) >= COVER_DEPTH_M


def blocks_sight(collider: Collider) -> bool:
    """The wall, and a street-level prop at least ``SIGHT_BLOCK_HEIGHT_M`` tall."""
    if collider.kind == "wall":
        return True
    return collider.kind == "prop" and _at_street_level(collider) and collider.max_z >= SIGHT_BLOCK_HEIGHT_M


def cover_colliders(colliders: Iterable[Collider]) -> list[Collider]:
    return [collider for collider in colliders if is_cover(collider)]


def _box(bounds: tuple[float, float, float, float]) -> BaseGeometry:
    return shapely.box(*bounds)


def _ring_segments(coords: np.ndarray) -> np.ndarray:
    return np.hstack([coords[:-1], coords[1:]])


def sight_segments(buildings: BaseGeometry, colliders: Iterable[Collider]) -> np.ndarray:
    """``(n, 4)`` segments that stop a line of sight: building outlines, tall props' boxes and the wall's box."""
    rings: list[np.ndarray] = []
    for polygon in polygons_of(buildings):
        rings.append(shapely.get_coordinates(polygon.exterior))
        rings.extend(shapely.get_coordinates(hole) for hole in polygon.interiors)
    for collider in colliders:
        if blocks_sight(collider):
            rings.append(shapely.get_coordinates(_box(collider.bounds).exterior))
    if not rings:
        return np.zeros((0, 4))
    return np.vstack([_ring_segments(ring) for ring in rings])


class SightCaster:
    """Casts ``RAY_COUNT`` rays from a point against fixed segments; lengths are exact, capped at ``RAY_CAP_M``."""

    def __init__(self, segments: np.ndarray, *, cap_m: float = RAY_CAP_M, rays: int = RAY_COUNT, piece_m: float = RAY_PIECE_M) -> None:
        self.segments = np.asarray(segments, dtype=np.float64).reshape(-1, 4)
        self.cap_m = cap_m
        angles = np.radians(np.arange(rays) * (360.0 / rays))
        self.directions = np.column_stack([np.cos(angles), np.sin(angles)])
        self.pieces = max(1, math.ceil(cap_m / piece_m))  # pragma: no mutate: a second piece past the cap has no length and finds no other hit
        steps = np.minimum(np.arange(self.pieces + 1) * piece_m, cap_m)
        self._starts, self._ends = steps[:-1], steps[1:]
        lines = shapely.linestrings(self.segments.reshape(-1, 2, 2)) if len(self.segments) else np.array([])
        self.tree = STRtree(lines)

    def lengths(self, point: tuple[float, float]) -> np.ndarray:
        """The distance to the first segment along each ray (direction ``k * 360 / RAY_COUNT`` degrees from east)."""
        result = np.full(len(self.directions), self.cap_m)
        origin = np.asarray(point, dtype=np.float64)
        d = self.directions
        starts = origin + d[:, None, :] * self._starts[None, :, None]
        ends = origin + d[:, None, :] * self._ends[None, :, None]
        pieces = shapely.linestrings(np.stack([starts, ends], axis=2).reshape(-1, 2, 2))
        query, hit = self.tree.query(pieces, predicate="intersects")
        if not len(query):
            return result
        ray = query // self.pieces
        seg = self.segments[hit]
        a, e = seg[:, :2], seg[:, 2:] - seg[:, :2]
        direction = d[ray]
        w = a - origin
        denom = direction[:, 0] * e[:, 1] - direction[:, 1] * e[:, 0]
        parallel = np.abs(denom) < 1e-12  # pragma: no mutate: a determinant of exactly 1e-12 does not occur with metre coordinates
        safe = np.where(parallel, 1.0, denom)  # pragma: no mutate: only avoids a division by zero; parallel rows are replaced below
        t = (w[:, 0] * e[:, 1] - w[:, 1] * e[:, 0]) / safe
        # A segment along the ray: the nearer endpoint ahead of the point.
        along = np.minimum(np.einsum("ij,ij->i", w, direction), np.einsum("ij,ij->i", w + e, direction))
        t = np.where(parallel, np.maximum(along, 0.0), t)
        t = np.clip(t, 0.0, self.cap_m)
        np.minimum.at(result, ray, t)
        return result


# ------------------------------------------------------------------------------------------------ routes along


def route_samples(length_m: float, step_m: float = SAMPLE_STEP_M) -> list[float]:
    """Distances along a route: every ``step_m`` from 0, and the end."""
    count = int(math.floor(length_m / step_m + 1e-9))
    samples = [k * step_m for k in range(count + 1)]
    if length_m - samples[-1] > 1e-6:  # pragma: no mutate: equal only at exactly 1 micrometre
        samples.append(length_m)
    return samples


def route_stretches(length_m: float, stretch_m: float = STRETCH_M) -> list[tuple[float, float]]:
    """Consecutive ``(start, end)`` pieces of ``stretch_m``; the last one is the remainder."""
    if not stretch_m > 0:
        raise GameplayError(f"stretch_m must be greater than 0, got {stretch_m}")
    count = max(0, math.ceil(length_m / stretch_m - 1e-9))
    return [(k * stretch_m, min((k + 1) * stretch_m, length_m)) for k in range(count)]


def per_100m(count: int, length_m: float) -> float:
    if length_m <= 0:
        raise GameplayError("a stretch must be longer than 0 m")
    return round(count * 100.0 / length_m, 2)


@dataclass(frozen=True, slots=True)
class CoverIndex:
    """The cover colliders and a spatial index of their boxes."""

    colliders: tuple[Collider, ...]
    tree: STRtree

    @classmethod
    def of(cls, colliders: Iterable[Collider]) -> CoverIndex:
        cover = tuple(cover_colliders(colliders))
        return cls(cover, STRtree([_box(c.bounds) for c in cover]))

    def near(self, geometry: BaseGeometry, reach_m: float = COVER_REACH_M) -> list[int]:
        """Indices of the cover colliders within ``reach_m`` of ``geometry``, sorted."""
        return sorted(int(i) for i in self.tree.query(geometry, predicate="dwithin", distance=reach_m))


def balance(attackers_m: float, defenders_m: float, speed_m_s: float = RUN_SPEED_M_S) -> dict[str, float]:
    """Each team's time to first contact (length / speed) and their difference over the slower one, in percent."""
    if not speed_m_s > 0:
        raise GameplayError(f"speed must be greater than 0, got {speed_m_s}")
    if attackers_m < 0 or defenders_m < 0:
        raise GameplayError("a route length cannot be negative")
    a, b = attackers_m / speed_m_s, defenders_m / speed_m_s
    slower = max(a, b)
    diff = 0.0 if slower == 0 else abs(a - b) / slower * 100.0
    return {"attackers_s": round(a, 2), "defenders_s": round(b, 2), "diff_pct": round(diff, 2)}
