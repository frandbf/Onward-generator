"""Catalogue furniture in the generic shop rooms (ADR-031) and on the floors of the stair interior (ADR-034).
Each is off by default (``interiors.shops.furnish``, ``interiors.furnish``).

This module only plans: which catalogue row stands where in each shop room, at what turn, and what it
costs. ``overgrow place`` reads the plan from ``scene_detail.json`` and builds the meshes and colliders
(``overgrow/furnish.py``). Nothing here is observed: every piece is ``attribute_origin: generated``, and the
catalogue rows' semantics are ``estimated`` (no person reviewed a rendered view).

Rules, in local metres:

- **Floor.** A piece stands inside its room polygon less ``wall_gap_m``. Outer walls, partitions and a
  courtyard lie outside the room polygon, so a containment test keeps every piece off them.
- **Keep-outs.** The room's walkable door zone (``interiors.door_clearance_m`` deep, 0.3 m wider than the
  door each side, as ``interiors._door_zone``) and a ``pane_clear_m`` band behind every see-through opening.
- **Counter.** One, back to the wall of the room's display windows (the ring edge with the most
  ``shop_glass``), else the wall of the door, then the two walls beside it, as near the windows as it fits.
  Without a spot the room records ``no_counter_spot`` and keeps its wall pieces.
- **Decor.** At most ``DECOR_PER_COUNTER`` rows of the decor slot on the counter top, visual only.
- **Wall pieces.** Back to the wall at the first free stretch of a ring edge, ``piece_gap_m`` apart: one
  piece on each edge per round, longest edge first, until ``max_pieces_per_room`` pieces in all.
- **Clear path.** Every floor piece keeps a front zone ``walk_width_m`` deep across its length, inside the
  room and free of every other piece. After each piece, the room less the pieces, eroded by half of
  ``walk_width_m``, must keep every place it reached in the empty room joined to the door zone, and reach
  every front zone. A piece that would break either is refused (``blocks_path``).
- **Choice.** ``stable_unit(seed, building, room, slot, ring, edge, k)`` picks the first row to try, in a
  set sorted by id, so the order of a set or a catalogue does not change the plan.

A floor of the stair interior (``floor_rooms``, ADR-034) is one room ``<building>/L<n>`` on the interior's one
polygon, at its own ``floor_m``. Its entry is the door zone on the ground floor and the arrival landing of the flight
from below on the floors above. Its stair wells are holes in the walk region, and each well plus
``interiors.WELL_CLEARANCE_M``, the foot of every flight that starts on it and the arrival of the flight that ends on
it are keep-outs (``stair_zone``). The walk region must keep every stair foot joined to the entry. A landing lies
between the well and the walls, so the walk region of a ``walk_width_m`` body only reaches its open side: the entry
and the feet count as reached within ``reach_m`` (half of ``walk_width_m``).

The model's front is assumed to be its local -Y (glTF +Z): ``yaw_deg`` turns it to face into the room, and a
set entry's ``turn_deg`` turns it first. That facing is an assumption for the headset check.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import numpy as np
from shapely import LineString, Point, Polygon, unary_union
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient

from onward_generator.detail.config import ALL_SLOTS, ZONE_SLOTS, FurnishEntry, FurnishSet, Interiors, ShopFurnish
from onward_generator.detail.facades import Hole, Segment, stable_unit
from onward_generator.detail.interiors import (
    SEE_THROUGH,
    SHOP_GLASS,
    WELL_CLEARANCE_M,
    Interior,
    InteriorPlan,
    _door_zone,
    _rim_on,
    flight_landings,
    light_anchors,
)
from onward_generator.generation.mesh import polygons_of

# A solid piece's collider is one box: 12 triangles, in the collision GLB only.
COLLIDER_TRIANGLES = 12
DECOR_PER_COUNTER = 2
# The strip a wall piece is sought in starts this far off the wall-gap line, so the room's own edge is not a hit.
STRIP_EPS_M = 1e-7
# Pieces of a difference smaller than this are float noise along a shared line, not an obstacle.
SLIVER_M2 = 1e-8
# A part of the walk region this small, cut off from the door, is noise; anything larger is a pocket nobody can reach.
ISLAND_M2 = 1e-6
ROOM_REFUSALS = ("unreachable", "blocks_path", "no_free_wall", "triangle_budget", "collision_budget", "asset_cap")
# Pieces that stand on another piece or lie flat on the floor: they take no floor, cut no path and get no collider.
ON_TOP = frozenset({"decor", "top", "rug"})
# A room gets one piece per this many square metres, at least MIN_PIECES, at most max_pieces_per_room (ADR-036).
AREA_PER_PIECE_M2 = 8.0
MIN_PIECES = 4
# Share of a room's pieces the walls may take, so the floor keeps room for islands, tables and corners.
WALL_SHARE = 0.4
CORNER_PIECES = 2
TOP_PER_TABLE = 1
# Clear space round a table group, so a body can pull a chair and walk between groups.
GROUP_CLEAR_M = 0.5
# Pieces a zoned room's cap leaves out: what stands on a piece or lies flat, and the seats that go with a table.
CAP_FREE = ON_TOP | {"seat"}
PIECE_REFUSALS = ("no_counter_spot", "blocks_path")


@dataclass(frozen=True, slots=True)
class FurnishAsset:
    """A catalogue row as furnishing prices it: size after its level of detail (length X, width Y, height), and provenance."""

    asset_id: str
    catalogue: str
    licence: str
    role: str
    category: str
    size_m: tuple[float, float, float]
    triangles: int
    glb_sha256: str
    parts: int = 1


@dataclass(frozen=True, slots=True)
class FurnishRoom:
    """One room to furnish. ``door_zone`` is ``None`` for a room with no walkable door (seen through its windows only).

    ``panes`` are the keep-out bands behind see-through openings; ``display`` the inner spans of its ``shop_glass``
    openings; ``door_span`` the inner span of its door.

    A floor of the stair interior (ADR-034) also has: ``floor_m``, the z its pieces stand at; ``holes``, the stair
    wells, which the walk region leaves out; ``keepouts``, the stair zones no piece enters; ``targets``, the stair feet
    the walk region must keep joined to the entry (``door_zone``); and ``reach_m``, how far short of the entry and the
    targets the walk region may stop. The defaults are a shop room's: ground floor, no stair.
    """

    room_id: str
    polygon: Polygon
    door_zone: Polygon | None
    ceiling_m: float
    category: str = "generic"
    panes: tuple[Polygon, ...] = ()
    display: tuple[LineString, ...] = ()
    door_span: LineString | None = None
    floor_m: float = 0.0
    holes: tuple[Polygon, ...] = ()
    keepouts: tuple[Polygon, ...] = ()
    targets: tuple[Polygon, ...] = ()
    reach_m: float = 0.0


@dataclass(frozen=True, slots=True)
class Piece:
    asset: FurnishAsset
    slot: str
    footprint: Polygon
    centre: tuple[float, float]
    z_m: float
    yaw_deg: float
    turn_deg: int
    solid: bool

    def record(self, piece_id: str) -> dict[str, Any]:
        a = self.asset
        return {
            "id": piece_id, "asset_id": a.asset_id, "catalogue": a.catalogue, "licence": a.licence, "role": a.role, "slot": self.slot,
            "position_m": [round(self.centre[0], 4), round(self.centre[1], 4), round(self.z_m, 4)],
            "yaw_deg": round(self.yaw_deg, 4), "turn_deg": self.turn_deg,
            "size_m": [round(v, 4) for v in a.size_m],
            "footprint_m": [[round(x, 4), round(y, 4)] for x, y in list(self.footprint.exterior.coords)[:-1]],
            "triangles": a.triangles, "solid": self.solid, "glb_sha256": a.glb_sha256,
            "attribute_origin": "generated", "semantics_origin": "estimated",
        }


@dataclass(frozen=True, slots=True)
class RoomFurnishing:
    room_id: str
    category: str
    pieces: tuple[Piece, ...]
    refusals: tuple[str, ...] = ()
    """Pieces the room did not get, once per reason: ``no_counter_spot``, ``blocks_path``."""
    method: str = "shop_furnishing"

    @property
    def visual_triangles(self) -> int:
        return sum(p.asset.triangles for p in self.pieces)

    @property
    def collision_triangles(self) -> int:
        return COLLIDER_TRIANGLES * sum(p.solid for p in self.pieces)

    @property
    def assets(self) -> frozenset[str]:
        return frozenset(p.asset.asset_id for p in self.pieces)

    @property
    def floor_footprints(self) -> BaseGeometry:
        """The plan of the pieces on the floor; decor stands on a counter or a table, a rug lies flat."""
        return unary_union([p.footprint for p in self.pieces if p.slot not in ON_TOP])

    def record(self, building_id: str, first: int) -> dict[str, Any]:
        """The ``furnishing`` block of the room record; piece ids ``furnish/<building>/<n>`` from ``first``."""
        return {
            "method": self.method, "attribute_origin": "generated", "set": self.category,
            "pieces": [p.record(f"furnish/{building_id}/{first + i}") for i, p in enumerate(self.pieces)],
            "visual_triangles": self.visual_triangles, "collision_triangles": self.collision_triangles,
            "refusals": list(self.refusals),
        }


@dataclass(slots=True)
class FurnishBudget:
    """What the map's furniture has used of its block (``scope``): drawn and collision triangles, and distinct assets.

    Separate from ``shops.max_triangles`` and ``interiors.max_triangles``: furniture never takes a room's budget
    (ADR-031, ADR-034). The shop rooms and the floors of the stair interior each have their own.
    """

    max_triangles: int
    max_collision_triangles: int
    max_assets: int
    used: dict[str, int] = field(default_factory=lambda: {"triangles": 0, "collision_triangles": 0})
    assets: set[str] = field(default_factory=set)
    scope: str = "shops.furnish"

    @classmethod
    def of(cls, furnish: ShopFurnish, scope: str = "shops.furnish") -> FurnishBudget:
        return cls(furnish.max_triangles, furnish.max_collision_triangles, furnish.max_assets, scope=scope)

    def refuse(self, room: RoomFurnishing) -> str | None:
        """``None`` when the whole room fits, else the reason. A room is furnished whole or not at all."""
        for kind, cost, limit, label in (
            ("triangles", room.visual_triangles, self.max_triangles, "triangle_budget"),
            ("collision_triangles", room.collision_triangles, self.max_collision_triangles, "collision_budget"),
        ):
            left = limit - self.used[kind]
            if cost > left:
                return f"{label}: needs {cost} {kind.replace('_', ' ')}, {left} left of {self.scope}.max_{kind} {limit}"
        wanted = len(self.assets | room.assets)
        if wanted > self.max_assets:
            return f"asset_cap: needs {wanted} distinct assets, {self.max_assets} allowed by {self.scope}.max_assets"
        return None

    def spend(self, room: RoomFurnishing) -> None:
        self.used["triangles"] += room.visual_triangles
        self.used["collision_triangles"] += room.collision_triangles
        self.assets |= room.assets


@dataclass(frozen=True, slots=True)
class _Edge:
    ring: int
    index: int
    a: np.ndarray
    u: np.ndarray
    n: np.ndarray
    """Unit normal into the room (the left of ``u`` on an oriented ring)."""
    length: float

    def rect(self, s0: float, s1: float, d0: float, d1: float) -> Polygon:
        pts = [self.a + self.u * s + self.n * d for s, d in ((s0, d0), (s1, d0), (s1, d1), (s0, d1))]
        return Polygon([(float(p[0]), float(p[1])) for p in pts])

    @property
    def line(self) -> LineString:
        b = self.a + self.u * self.length
        return LineString([(float(self.a[0]), float(self.a[1])), (float(b[0]), float(b[1]))])

    @property
    def yaw_deg(self) -> float:
        """The turn that points the model's local -Y along ``n``, into the room."""
        return math.degrees(math.atan2(float(self.n[0]), -float(self.n[1])))


def piece_solid(asset: FurnishAsset, slot: str) -> bool:
    """Whether a piece gets a box collider: the overgrow cover rule (``is_solid``) at its placed size; decor never."""
    from onward_generator.overgrow.ambient import is_solid  # overgrow imports detail; load it on use

    return slot not in ON_TOP and is_solid(asset.role, asset.category, asset.size_m)


def footprint_size(asset: FurnishAsset, turn_deg: int) -> tuple[float, float]:
    """(along the wall, across it) after a quarter turn."""
    length, width, _ = asset.size_m
    return (width, length) if turn_deg % 180 else (length, width)


def room_edges(polygon: Polygon) -> list[_Edge]:
    """Every edge of every ring of the oriented room, outer ring first, then each courtyard."""
    oriented = orient(polygon)
    edges: list[_Edge] = []
    for ring, coords in enumerate([oriented.exterior.coords, *(r.coords for r in oriented.interiors)]):
        points = np.asarray(coords)
        for index, (a, b) in enumerate(zip(points[:-1], points[1:])):
            length = float(np.linalg.norm(b - a))
            if not length:  # a repeated vertex
                continue
            u = (b - a) / length
            edges.append(_Edge(ring, index, a, u, np.array([-u[1], u[0]]), length))
    return edges


def free_intervals(edge: _Edge, depth: float, allowed: BaseGeometry, gap: float) -> list[tuple[float, float]]:
    """Stretches ``[s0, s1]`` along ``edge`` where any piece ``depth`` deep, its back ``gap`` off the wall, stays in ``allowed``."""
    strip = edge.rect(0.0, edge.length, gap + STRIP_EPS_M, gap + depth)
    parts = [part for part in polygons_of(strip.difference(allowed)) if part.area > SLIVER_M2]  # pragma: no mutate: a part of exactly SLIVER_M2 is float noise whichever side of the line it falls
    blocked: list[tuple[float, float]] = []
    for part in parts:
        along = (np.asarray(part.exterior.coords) - edge.a) @ edge.u
        blocked.append((float(along.min()), float(along.max())))
    free: list[tuple[float, float]] = []
    cursor = 0.0
    for lo, hi in sorted(blocked):
        if lo > cursor:
            free.append((cursor, lo))
        cursor = max(cursor, hi)
    if cursor < edge.length:
        free.append((cursor, edge.length))
    return free


def _ordered(entries: Sequence[FurnishEntry], seed: int, *key: object) -> list[FurnishEntry]:
    """``entries`` sorted by id and turn, rotated to start at the seeded one."""
    ranked = sorted(entries, key=lambda e: (e.id, e.turn_deg))
    if not ranked:
        return []
    start = int(stable_unit(seed, *key) * len(ranked))
    return ranked[start:] + ranked[:start]


class _Planner:
    def __init__(self, room: FurnishRoom, furnish: ShopFurnish, catalogue: Mapping[str, FurnishAsset], seed: int, building_id: str) -> None:
        self.room, self.cfg, self.catalogue, self.seed, self.building_id = room, furnish, catalogue, seed, building_id
        self.polygon = orient(room.polygon)
        self.free = self.polygon.buffer(-furnish.wall_gap_m, join_style="mitre")
        assert room.door_zone is not None
        self.door_zone = room.door_zone
        self.keepout = unary_union([room.door_zone, *room.panes, *room.keepouts])
        self.half_walk = furnish.walk_width_m / 2
        # The floor a body walks on: the room less its stair wells. A shop room has none, and keeps its own polygon.
        self.walk_floor = self.polygon.difference(unary_union(room.holes)) if room.holes else self.polygon
        self.entry = room.door_zone.buffer(room.reach_m) if room.reach_m else room.door_zone
        self.targets = [t.buffer(room.reach_m) if room.reach_m else t for t in room.targets]
        empty = self.walk_floor.buffer(-self.half_walk)
        self.reached_empty = [c for c in polygons_of(empty) if c.intersects(self.entry)]
        self.islands = unary_union([c for c in polygons_of(empty) if not c.intersects(self.entry)])
        self.edges = room_edges(room.polygon)
        self.pieces: list[Piece] = []
        self.fronts: list[Polygon] = []
        self.refusals: set[str] = set()
        self._allowed: BaseGeometry | None = None
        self.edge_choice: dict[tuple[int, int], FurnishEntry] = {}
        # A set with zone slots (the semantic recipes, ADR-036) is planned by zones and scaled by the room's area; a
        # hand-written set of counter, wall and decor only keeps the plan of ADR-031 exactly.
        self.zoned = any(self.room_set().slot(name) for name in ZONE_SLOTS)
        by_area = max(MIN_PIECES, round(float(room.polygon.area) / AREA_PER_PIECE_M2))
        self.cap = min(furnish.max_pieces_per_room, by_area) if self.zoned else furnish.max_pieces_per_room

    def add(self, piece: Piece) -> None:
        self.pieces.append(piece)
        self._allowed = None

    @property
    def floor_count(self) -> int:
        """Pieces against the cap: in a zoned room all but decor, table tops, rugs and seats (a table brings its own),
        else all."""
        return sum(1 for p in self.pieces if p.slot not in CAP_FREE) if self.zoned else len(self.pieces)

    @property
    def full(self) -> bool:
        return self.floor_count >= self.cap

    def allowed(self) -> BaseGeometry:
        """The floor a further piece may cover: the free floor less the keep-outs, the front zones, and the pieces with
        ``piece_gap_m`` round each."""
        if self._allowed is None:
            placed = [p.footprint.buffer(self.cfg.piece_gap_m, join_style="mitre") for p in self.pieces if p.slot not in ON_TOP]
            self._allowed = self.free.difference(unary_union([self.keepout, *placed, *self.fronts]))
        return self._allowed

    def conflict(self, footprint: Polygon) -> str | None:
        """Why a floor piece cannot stand on ``footprint``: off the floor, in a keep-out, or on another piece."""
        if not self.free.buffer(1e-7, join_style="mitre").contains(footprint):
            return "outside_room"
        if footprint.intersection(self.door_zone).area > SLIVER_M2:
            return "door_zone"
        if any(footprint.intersection(zone).area > SLIVER_M2 for zone in self.room.keepouts):
            return "stair_zone"
        if any(footprint.intersection(pane).area > SLIVER_M2 for pane in self.room.panes):
            return "pane_band"
        if any(footprint.intersection(p.footprint).area > SLIVER_M2 for p in self.pieces if p.slot not in ON_TOP):
            return "overlap"
        return None

    def blocks(self, footprint: Polygon, front: Polygon) -> bool:
        """Whether a floor piece on ``footprint`` with its ``front`` zone would cut the clear path (``blocks_path``)."""
        if not self.polygon.buffer(1e-7, join_style="mitre").contains(front):
            return True
        if any(front.intersection(hole).area > SLIVER_M2 for hole in self.room.holes):
            return True
        if any(front.intersection(p.footprint).area > SLIVER_M2 for p in self.pieces if p.slot not in ON_TOP):
            return True
        if any(footprint.intersection(zone).area > SLIVER_M2 for zone in self.fronts):
            return True
        return self.path_cut([footprint], [front])

    def path_cut(self, extra: Sequence[Polygon] = (), fronts: Sequence[Polygon] = ()) -> bool:
        """Whether the pieces and ``extra`` cut a place the empty room reached off the door, leave a stair foot
        (``targets``) cut off from the entry, or leave a front zone unreached."""
        floor = [p.footprint for p in self.pieces if p.slot not in ON_TOP] + list(extra)
        walk = self.walk_floor.difference(unary_union(floor)).buffer(-self.half_walk)
        reached = unary_union([c for c in polygons_of(walk) if c.intersects(self.entry)])
        if reached.is_empty:
            return True
        if walk.difference(reached).difference(self.islands).area > ISLAND_M2:
            return True
        if self.cut_off(reached):
            return True
        return any(not front.intersects(reached) for front in (*self.fronts, *fronts))

    def cut_off(self, reached: BaseGeometry) -> bool:
        """Whether a stair foot is out of ``reached``, the walk region joined to the entry."""
        return any(not target.intersects(reached) for target in self.targets)

    def fits_height(self, asset: FurnishAsset, z: float | None = None) -> bool:
        """Whether the piece, standing at ``z`` (the room's floor when ``None``), keeps ``head_clear_m`` under the ceiling."""
        base = self.room.floor_m if z is None else z
        return base + asset.size_m[2] <= self.room.ceiling_m - self.cfg.head_clear_m + 1e-9

    def make(self, entry: FurnishEntry, slot: str, edge: _Edge, s: float) -> tuple[Piece, Polygon]:
        """The piece of ``entry`` with its back on ``edge`` from ``s``, and its front zone, ``walk_width_m`` deep."""
        asset = self.catalogue[entry.id]
        along, depth = footprint_size(asset, entry.turn_deg)
        g = self.cfg.wall_gap_m
        footprint = edge.rect(s, s + along, g, g + depth)
        centre = edge.a + edge.u * (s + along / 2) + edge.n * (g + depth / 2)
        yaw = (edge.yaw_deg + entry.turn_deg) % 360.0
        piece = Piece(asset, slot, footprint, (float(centre[0]), float(centre[1])), self.room.floor_m, yaw, entry.turn_deg,
                      piece_solid(asset, slot))
        front = edge.rect(s, s + along, g + depth, g + depth + self.cfg.walk_width_m)
        return piece, front

    def counter_edges(self) -> list[_Edge]:
        """The wall of the display windows (else of the door), then the walls on either side of it on its ring."""
        def overlap(edge: _Edge, spans: Sequence[LineString]) -> float:
            near = edge.line.buffer(1e-3, cap_style="flat")
            return sum(near.intersection(span).length for span in spans)

        spans: Sequence[LineString] = self.room.display
        scores = [overlap(e, spans) for e in self.edges] if spans else [0.0] * len(self.edges)
        if max(scores, default=0.0) <= 1e-6 and self.room.door_span is not None:
            scores = [overlap(e, [self.room.door_span]) for e in self.edges]
        if max(scores, default=0.0) <= 1e-6:
            return []
        best = scores.index(max(scores))
        main = self.edges[best]
        ring = [e for e in self.edges if e.ring == main.ring]
        k = ring.index(main)
        found = [main]
        for other in (ring[(k - 1) % len(ring)], ring[(k + 1) % len(ring)]):
            if other not in found:
                found.append(other)
        return found

    def place_counter(self) -> Piece | None:
        entries = _ordered(self.room_set().counter, self.seed, self.building_id, self.room.room_id, "counter")
        if not entries or self.full:
            return None
        edges = self.counter_edges()
        target = unary_union(list(self.room.display) or ([self.room.door_span] if self.room.door_span is not None else []))
        for entry in entries:
            asset = self.catalogue[entry.id]
            if not self.fits_height(asset):
                continue
            along, depth = footprint_size(asset, entry.turn_deg)
            allowed = self.allowed()
            spots: list[tuple[int, float, float, _Edge]] = []
            for rank, edge in enumerate(edges):
                for lo, hi in free_intervals(edge, depth, allowed, self.cfg.wall_gap_m):
                    if hi - lo < along - 1e-9:
                        continue
                    for s in dict.fromkeys((lo, hi - along)):
                        centre = edge.a + edge.u * (s + along / 2)
                        near = target.distance(Point(float(centre[0]), float(centre[1]))) if not target.is_empty else 0.0
                        spots.append((rank, round(near, 6), s, edge))
            for _, _, s, edge in sorted(spots, key=lambda spot: spot[:3]):
                piece, front = self.make(entry, "counter", edge, s)
                if self.conflict(piece.footprint) is not None:
                    continue
                if self.blocks(piece.footprint, front):
                    self.refusals.add("blocks_path")
                    continue
                self.fronts.append(front)
                self.add(piece)
                return piece
        self.refusals.add("no_counter_spot")
        return None

    def place_decor(self, counter: Piece) -> None:
        self.place_on(counter, self.room_set().decor, "decor", DECOR_PER_COUNTER)

    def place_on(self, counter: Piece, slot_entries: Sequence[FurnishEntry], slot: str, count: int) -> None:
        """Up to ``count`` small pieces of ``slot_entries`` on the top of ``counter`` (a counter or a table), visual only."""
        # The counter's decor keeps the key of ADR-031; each table of a zoned room shuffles its own.
        key = (slot,) if slot == "decor" else (slot, round(counter.centre[0], 3), round(counter.centre[1], 3))
        entries = _ordered(slot_entries, self.seed, self.building_id, self.room.room_id, *key)
        if not entries:
            return
        top = counter.z_m + counter.asset.size_m[2]
        c_along, c_depth = footprint_size(counter.asset, counter.turn_deg)
        theta = math.radians(counter.yaw_deg - counter.turn_deg)
        along_axis = np.array([-math.cos(theta), -math.sin(theta)])  # the wall direction ``u`` the counter was set on
        centre = np.array(counter.centre)
        for k in range(count):
            if not self.zoned and self.full:
                return
            offset = (k + 1) / (count + 1) - 0.5
            for entry in entries[k:] + entries[:k]:
                asset = self.catalogue[entry.id]
                along, depth = footprint_size(asset, entry.turn_deg)
                if along > c_along / count or depth > c_depth or not self.fits_height(asset, top):
                    continue
                at = centre + along_axis * offset * c_along
                half_u, half_n = along_axis * along / 2, np.array([-along_axis[1], along_axis[0]]) * depth / 2
                corners = [at - half_u - half_n, at + half_u - half_n, at + half_u + half_n, at - half_u + half_n]
                footprint = Polygon([(float(p[0]), float(p[1])) for p in corners])
                self.add(Piece(asset, slot, footprint, (float(at[0]), float(at[1])), top,
                                         (counter.yaw_deg - counter.turn_deg + entry.turn_deg) % 360.0, entry.turn_deg, False))
                break

    def place_walls(self, quota: int | None = None, slot: str = "wall") -> None:
        """Round after round, one wall piece on each edge that still has room, longest edge first, until the room is
        full or ``quota`` pieces of ``slot`` stand. An edge keeps the model that first fitted it, so a wall reads as a
        run of the same piece (ADR-036)."""
        open_edges = sorted(self.edges, key=lambda e: (-round(e.length, 6), e.ring, e.index))
        k = 0
        start = len(self.pieces)
        while open_edges and not self.full and (quota is None or len(self.pieces) - start < quota):
            open_edges = [edge for edge in open_edges
                          if not self.full and (quota is None or len(self.pieces) - start < quota) and self.place_wall(edge, k, slot)]
            k += 1

    def place_wall(self, edge: _Edge, k: int, slot: str = "wall") -> bool:
        """Put the ``k``-th piece of ``slot`` on ``edge`` at the first free stretch it fits; ``False`` when none fits."""
        # A zoned room orders an edge's models once, so the edge keeps one; ADR-031 reshuffled them for every k.
        round_key = () if self.zoned else (k,)
        ordered = _ordered(self.room_set().slot(slot), self.seed, self.building_id, self.room.room_id, slot, edge.ring, edge.index, *round_key)
        kept = self.edge_choice.get((edge.ring, edge.index)) if self.zoned else None
        if kept is not None and kept in ordered:
            ordered = [kept, *(e for e in ordered if e != kept)]
        for entry in ordered:
            asset = self.catalogue[entry.id]
            if not self.fits_height(asset):
                continue
            along, depth = footprint_size(asset, entry.turn_deg)
            fitting = [lo for lo, hi in free_intervals(edge, depth, self.allowed(), self.cfg.wall_gap_m) if hi - lo >= along - 1e-9]
            if not fitting:
                continue
            piece, front = self.make(entry, slot, edge, fitting[0])
            if self.conflict(piece.footprint) is not None:
                continue
            if self.blocks(piece.footprint, front):
                self.refusals.add("blocks_path")
                continue
            self.fronts.append(front)
            self.add(piece)
            if slot == "wall":
                self.edge_choice[(edge.ring, edge.index)] = entry
            return True
        return False

    # ---- zones (ADR-036) ------------------------------------------------------------------------------------------

    def frame(self) -> tuple[_Edge, float, float, float, float]:
        """The room's longest outer edge as a frame, and the free floor's extent along it (s) and into the room (d)."""
        main = max((e for e in self.edges if e.ring == 0), key=lambda e: (round(e.length, 6), -e.index))
        coords = np.asarray(self.free.exterior.coords) if isinstance(self.free, Polygon) else np.asarray(self.polygon.exterior.coords)
        rel = coords - main.a
        s_vals, d_vals = rel @ main.u, rel @ main.n
        return main, float(s_vals.min()), float(s_vals.max()), float(d_vals.min()), float(d_vals.max())

    def _rect_piece(self, frame: _Edge, entry: FurnishEntry, slot: str, s: float, d: float, facing: np.ndarray) -> Piece:
        asset = self.catalogue[entry.id]
        along, depth = footprint_size(asset, entry.turn_deg)
        footprint = frame.rect(s, s + along, d, d + depth)
        centre = frame.a + frame.u * (s + along / 2) + frame.n * (d + depth / 2)
        yaw = (math.degrees(math.atan2(float(facing[0]), -float(facing[1]))) + entry.turn_deg) % 360.0
        return Piece(asset, slot, footprint, (float(centre[0]), float(centre[1])), self.room.floor_m, yaw, entry.turn_deg,
                     piece_solid(asset, slot))

    def place_rug(self) -> None:
        """One rug of the set, centred on the free floor along its longest wall; it lies flat, so nothing else minds it."""
        entries = _ordered(self.room_set().rug, self.seed, self.building_id, self.room.room_id, "rug")
        if not entries:
            return
        frame, s0, s1, d0, d1 = self.frame()
        for entry in entries:
            asset = self.catalogue[entry.id]
            along, depth = footprint_size(asset, entry.turn_deg)
            s, d = (s0 + s1 - along) / 2, (d0 + d1 - depth) / 2
            piece = self._rect_piece(frame, entry, "rug", s, d, frame.n)
            if self.free.contains(piece.footprint) and piece.footprint.intersection(self.keepout).area <= SLIVER_M2:
                self.add(piece)
                return

    def place_center(self, quota: int) -> int:
        """Rows of free-standing islands (shelving, display cases) parallel to the longest wall, an aisle
        ``walk_width_m`` wide in front of each; one model for the whole room, so the rows line up."""
        return self._grid(self.room_set().center, "center", quota, group=False)

    def place_tables(self, quota: int) -> int:
        """Tables in a grid, each with its seats on its two long sides facing it, ``GROUP_CLEAR_M`` round each group."""
        return self._grid(self.room_set().table, "table", quota, group=True)

    def _grid(self, slot_entries: Sequence[FurnishEntry], slot: str, quota: int, *, group: bool) -> int:
        entries = _ordered(slot_entries, self.seed, self.building_id, self.room.room_id, slot)
        if not entries or quota <= 0 or self.full:
            return 0
        frame, s0, s1, d0, d1 = self.frame()
        walk = self.cfg.walk_width_m
        for entry in entries:
            asset = self.catalogue[entry.id]
            if not self.fits_height(asset):
                continue
            along, depth = footprint_size(asset, entry.turn_deg)
            seat = self._seat(along) if group else None
            seat_depth = footprint_size(self.catalogue[seat.id], seat.turn_deg)[1] if seat is not None else 0.0
            margin = (seat_depth + GROUP_CLEAR_M) if group else 0.0
            placed = 0
            d = d0 + walk + margin
            while d + depth + margin <= d1 - (walk if not group else 0.0) and placed < quota and not self.full:
                s = s0 + walk / 2 + margin
                while s + along + margin <= s1 - walk / 2 and placed < quota and not self.full:
                    if self._try_unit(frame, entry, slot, s, d, seat, group):
                        placed += 1
                        s += along + (2 * margin if group else self.cfg.piece_gap_m)
                    else:
                        s += max(0.25, along / 2)
                d += depth + (2 * margin if group else walk)
            if placed:
                return placed
        return 0

    def _seat(self, table_along: float) -> FurnishEntry | None:
        for entry in _ordered(self.room_set().seat, self.seed, self.building_id, self.room.room_id, "seat"):
            along, _depth = footprint_size(self.catalogue[entry.id], entry.turn_deg)
            if along <= table_along + 0.1 and self.fits_height(self.catalogue[entry.id]):
                return entry
        return None

    def _try_unit(self, frame: _Edge, entry: FurnishEntry, slot: str, s: float, d: float, seat: FurnishEntry | None, group: bool) -> bool:
        """Place one island, or one table and its seats, at (s, d) of ``frame`` if it keeps every rule."""
        asset = self.catalogue[entry.id]
        along, depth = footprint_size(asset, entry.turn_deg)
        main = self._rect_piece(frame, entry, slot, s, d, -frame.n)
        unit = [main]
        if group and seat is not None:
            s_along, s_depth = footprint_size(self.catalogue[seat.id], seat.turn_deg)
            count = 2 if along >= 2 * s_along + 0.2 else 1
            for facing, dd in ((-frame.n, d + depth + 0.05), (frame.n, d - 0.05 - s_depth)):
                for k in range(count):
                    # Seats spread evenly along the table side, an equal gap before, between and after them.
                    start_s = s + (along - count * s_along) * (k + 1) / (count + 1) + k * s_along
                    unit.append(self._rect_piece(frame, seat, "seat", start_s, dd, facing))
        if self.full:
            return False
        footprints = [p.footprint for p in unit]
        for footprint in footprints:
            if self.conflict(footprint) is not None or any(footprint.intersection(z).area > SLIVER_M2 for z in self.fronts):
                return False
        if any(f.intersection(g).area > SLIVER_M2 for i, f in enumerate(footprints) for g in footprints[i + 1:]):
            return False
        whole = unary_union(footprints)
        clear = whole.buffer(GROUP_CLEAR_M if group else self.cfg.piece_gap_m, join_style="mitre")
        if any(clear.intersection(p.footprint).area > SLIVER_M2 for p in self.pieces if p.slot not in ON_TOP):
            return False
        front = None if group else frame.rect(s, s + along, d - self.cfg.walk_width_m, d)
        if self.path_cut(footprints, [front] if front is not None else []):
            self.refusals.add("blocks_path")
            return False
        if front is not None:
            self.fronts.append(front)
        for piece in unit:
            self.add(piece)
        return True

    def place_corners(self, quota: int) -> None:
        """Up to ``quota`` corner pieces (plants, a floor lamp, an armchair), each in a free corner of the outer wall."""
        entries = _ordered(self.room_set().corner, self.seed, self.building_id, self.room.room_id, "corner")
        if not entries:
            return
        placed = 0
        for edge in sorted((e for e in self.edges if e.ring == 0), key=lambda e: (-round(e.length, 6), e.index)):
            for at_end in (False, True):
                if placed >= quota or self.full:
                    return
                for entry in entries[placed:] + entries[:placed]:
                    asset = self.catalogue[entry.id]
                    along, depth = footprint_size(asset, entry.turn_deg)
                    if not self.fits_height(asset) or along > edge.length:
                        continue
                    piece, front = self.make(entry, "corner", edge, edge.length - along - self.cfg.wall_gap_m if at_end else self.cfg.wall_gap_m)
                    if self.conflict(piece.footprint) is not None or any(piece.footprint.intersection(z).area > SLIVER_M2 for z in self.fronts):
                        continue
                    if self.path_cut([piece.footprint]):
                        continue
                    self.add(piece)
                    placed += 1
                    break

    def place_tops(self) -> None:
        """Small things on every table: cups and plates in a bar, cakes in a bakery, books and lamps at home."""
        for table in [p for p in self.pieces if p.slot == "table"]:
            self.place_on(table, self.room_set().top, "top", TOP_PER_TABLE)

    def room_set(self) -> FurnishSet:
        return self.cfg.set_for(self.room.category)


def plan_room(room: FurnishRoom, furnish: ShopFurnish, catalogue: Mapping[str, FurnishAsset], seed: int, building_id: str) -> RoomFurnishing | str:
    """The furniture of one room, or why it gets none: ``unreachable`` (no door), ``blocks_path`` (the empty room
    has no clear path from its door, or none to a stair foot, or every piece that fitted would cut it), ``no_free_wall``
    (nothing fitted).

    The counter comes first, then its decor, then the wall pieces. Budgets are the caller's (``FurnishBudget``).
    """
    if room.door_zone is None:
        return "unreachable"
    planner = _Planner(room, furnish, catalogue, seed, building_id)
    if not planner.reached_empty or planner.cut_off(unary_union(planner.reached_empty)):
        return "blocks_path"
    if not planner.zoned:
        counter = planner.place_counter()
        if counter is not None:
            planner.place_decor(counter)
        planner.place_walls()
        if not planner.pieces:
            return "blocks_path" if "blocks_path" in planner.refusals else "no_free_wall"
        refusals = tuple(r for r in PIECE_REFUSALS if r in planner.refusals)
        return RoomFurnishing(room.room_id, room.category if room.category in furnish.sets else "generic", tuple(planner.pieces), refusals)
    planner.place_rug()
    counter = planner.place_counter()
    if counter is not None:
        planner.place_decor(counter)
    planner.place_walls(quota=2, slot="wall_deep")
    planner.place_corners(CORNER_PIECES)  # before the wall runs, which start in the corners
    planner.place_walls(quota=max(1, math.ceil(WALL_SHARE * planner.cap)))
    planner.place_tables(planner.cap - planner.floor_count)
    planner.place_center(planner.cap - planner.floor_count)
    planner.place_tops()
    planner.place_walls()  # whatever the floor left, back to the walls
    if not [p for p in planner.pieces if p.slot not in ON_TOP]:
        return "blocks_path" if "blocks_path" in planner.refusals else "no_free_wall"
    refusals = tuple(r for r in PIECE_REFUSALS if r in planner.refusals)
    return RoomFurnishing(room.room_id, room.category if room.category in furnish.sets else "generic", tuple(planner.pieces), refusals)


class FurnishError(ValueError):
    """The furnishing sets name a row the catalogues cannot give."""


def load_catalogue(furnish: ShopFurnish, root: Path, scope: str = "shops.furnish") -> dict[str, FurnishAsset]:
    """Every row the sets name, read from the allow-listed catalogues under ``root`` as overgrow will read it.

    The triangles are those after the load-time level of detail, so detail prices what overgrow builds. A row
    that is not installed, or that the interior loader refuses, is an error naming the row and the reason.
    """
    from onward_generator.overgrow.library import read_interior_catalogue  # overgrow imports detail; load it on use

    ids = sorted({entry.id for chosen in furnish.sets.values() for slot in ALL_SLOTS for entry in chosen.slot(slot)})
    key = (str(root), furnish.catalogues, tuple(ids))
    if key in _CATALOGUES:  # the budget core runs detail several times in one process
        return _CATALOGUES[key]
    props, rejects = read_interior_catalogue(root, furnish.catalogues, ids)
    why = {reject.asset_id: reject.reason for reject in rejects}
    missing = [f"{i} ({why.get(i, 'not installed')})" for i in ids if i not in props]
    if missing:
        raise FurnishError(f"{scope}: rows the catalogues under {root} do not give: " + ", ".join(missing))
    assets = {
        i: FurnishAsset(i, p.catalogue, p.licence, p.prop.spec.role, p.prop.spec.category,
                        (round(p.prop.size_m[0], 6), round(p.prop.size_m[1], 6), round(p.prop.size_m[2], 6)), p.prop.triangles, p.glb_sha256, p.parts)
        for i, p in props.items()
    }
    _CATALOGUES[key] = assets
    return assets


_CATALOGUES: dict[tuple[str, tuple[str, ...], tuple[str, ...]], dict[str, FurnishAsset]] = {}


def furnish_rooms(plan: InteriorPlan, records: Sequence[Mapping[str, Any]], penetrations: Sequence[tuple[Segment, Hole]],
                  ic: Interiors) -> list[FurnishRoom]:
    """The shop rooms of ``plan`` as furnishing sees them: polygon, door zone, pane bands, display spans and ceiling."""
    t, pane = ic.wall_thickness_m, ic.shops.furnish.pane_clear_m
    bands = [seg.plan_rect(hole.s0, hole.s0 + hole.width, -(t + pane), 0.0) for seg, hole in penetrations]
    display = [(seg, hole, LineString([seg.point(hole.s0, depth_in=t)[:2], seg.point(hole.s0 + hole.width, depth_in=t)[:2]]))
               for seg, hole in penetrations if hole.back == SHOP_GLASS]
    rooms: list[FurnishRoom] = []
    for room, record in zip(plan.rooms, records, strict=True):
        door_zone = door_span = None
        if room.entrance is not None:
            door, seg = room.entrance
            door_zone = _door_zone(seg, door, ic)
            door_span = LineString([seg.point(door.s0, depth_in=t)[:2], seg.point(door.s0 + door.width, depth_in=t)[:2]])
        rooms.append(FurnishRoom(
            str(record["id"]), room.polygon, door_zone, float(record["ceiling_z_m"]),
            room.category.name if room.category is not None else "generic",
            tuple(band for band in bands if band.intersects(room.polygon)),
            tuple(span for seg, hole, span in display if _rim_on(room.polygon, seg, hole, t)),
            door_span,
        ))
    return rooms


def floor_rooms(plan: InteriorPlan, records: Sequence[Mapping[str, Any]], penetrations: Sequence[tuple[Segment, Hole]],
                ic: Interiors, furnish: ShopFurnish) -> list[FurnishRoom]:
    """The floors of a stair interior as furnishing sees them (ADR-034): one room per record ``<building>/L<n>``.

    Every floor is ``plan.inner`` at the record's ``floor_z_m`` under its ``ceiling_z_m``. The entry is the door zone
    on the ground floor and the arrival landing of the flight from below above it. The holes are the wells of the
    flights of that level (standing on it) and of the level below (opening through it), roof climb included, by level.
    The keep-outs are each hole plus ``WELL_CLEARANCE_M``, the foot of each flight of that level and the arrival of
    the flight from below; the targets are those feet. A ``pane_clear_m`` band stands behind each see-through window
    whose sill is on that storey (``floor_z_m <= v0 < ceiling_z_m``).
    """
    t, landing = ic.wall_thickness_m, ic.stair.landing_m
    ends = [flight_landings(flight, landing) for flight in plan.flights]
    door_zone = _door_zone(plan.door_segment, plan.door, ic)
    rooms: list[FurnishRoom] = []
    for record in records:
        level, floor, ceiling = int(record["level"]), float(record["floor_z_m"]), float(record["ceiling_z_m"])
        rising = [k for k, flight in enumerate(plan.flights) if flight.level == level]
        arriving = [k for k, flight in enumerate(plan.flights) if flight.level == level - 1]
        holes = tuple(plan.flights[k].well for k in (*rising, *arriving))
        entry = door_zone if level == 0 else ends[arriving[0]][1]
        panes = tuple(seg.plan_rect(hole.s0, hole.s0 + hole.width, -(t + furnish.pane_clear_m), 0.0)
                      for seg, hole in penetrations if hole.back in SEE_THROUGH and floor <= hole.v0 < ceiling)
        rooms.append(FurnishRoom(
            str(record["id"]), plan.inner, entry, ceiling, "generic", panes,
            floor_m=floor,
            holes=holes,
            keepouts=(*(hole.buffer(WELL_CLEARANCE_M) for hole in holes), *(ends[k][0] for k in rising), *(ends[k][1] for k in arriving)),
            targets=tuple(ends[k][0] for k in rising),
            reach_m=furnish.walk_width_m / 2,
        ))
    return rooms


FLOOR_SCOPE = "interiors.furnish"


@dataclass(slots=True)
class ShopFurnishing:
    """The map's furnishing pass: one call per built interior, in the order they were built.

    ``scope`` names the block it reads (``shops.furnish`` for the shop rooms, ``interiors.furnish`` for the floors of
    the stair interior); each pass has its own budget. ``method`` is written on each room's block and ``room_key``
    names the room count in ``stats``.
    """

    furnish: ShopFurnish
    catalogue: Mapping[str, FurnishAsset]
    seed: int
    budget: FurnishBudget
    rooms: int = 0
    reachable: int = 0
    furnished: int = 0
    pieces: Counter[str] = field(default_factory=Counter)
    refused: Counter[str] = field(default_factory=Counter)
    piece_refusals: Counter[str] = field(default_factory=Counter)
    method: str = "shop_furnishing"
    room_key: str = "shop_rooms"

    @classmethod
    def of(cls, furnish: ShopFurnish, catalogue: Mapping[str, FurnishAsset], seed: int) -> ShopFurnishing:
        return cls(furnish, catalogue, seed, FurnishBudget.of(furnish))

    @classmethod
    def of_floors(cls, furnish: ShopFurnish, catalogue: Mapping[str, FurnishAsset], seed: int) -> ShopFurnishing:
        """The pass of the floors of the stair interior (ADR-034), with the budget of ``interiors.furnish``."""
        return cls(furnish, catalogue, seed, FurnishBudget.of(furnish, FLOOR_SCOPE), method="residential_furnishing", room_key="floor_rooms")

    def _furnish(self, building_id: str, rooms: Sequence[FurnishRoom], records: Sequence[dict[str, Any]]) -> tuple[list[str], dict[str, BaseGeometry]]:
        """Furnish each of ``rooms`` that fits, whole or not at all; return one message per room left bare, and the
        plan of the floor pieces of each furnished room. Piece ids run on across the building's rooms."""
        messages: list[str] = []
        floors: dict[str, BaseGeometry] = {}
        first = 0
        for room in rooms:
            self.rooms += 1
            self.reachable += room.door_zone is not None
            result = plan_room(room, self.furnish, self.catalogue, self.seed, building_id)
            reason = result if isinstance(result, str) else self.budget.refuse(result)
            if isinstance(result, str) or reason is not None:
                assert reason is not None
                self.refused[reason.split(":")[0]] += 1
                messages.append(f"{reason}; room {room.room_id}")
                continue
            self.budget.spend(result)
            self.furnished += 1
            self.pieces.update(p.asset.asset_id for p in result.pieces)
            self.piece_refusals.update(result.refusals)
            block = replace(result, method=self.method).record(building_id, first)
            next(r for r in records if r["id"] == room.room_id)["furnishing"] = block
            first += len(result.pieces)
            floors[room.room_id] = result.floor_footprints
        return messages, floors

    def furnish_interior(self, building_id: str, plan: InteriorPlan, built: Interior, penetrations: Sequence[tuple[Segment, Hole]],
                         ic: Interiors) -> list[str]:
        """Furnish every room of a built shop interior that fits; return one message per room left bare.

        A furnished room record gains ``furnishing``. When any room is furnished, the light probes are
        placed again so no probe column stands in a piece. A room left bare keeps its shop interior.
        """
        records = built.spec["rooms"]
        messages, floors = self._furnish(building_id, furnish_rooms(plan, records, penetrations, ic), records)
        if floors:
            built.light_anchors = light_anchors(plan, records, furniture=unary_union(list(floors.values())))
        return messages

    def furnish_floors(self, building_id: str, plan: InteriorPlan, built: Interior, penetrations: Sequence[tuple[Segment, Hole]],
                       ic: Interiors) -> list[str]:
        """Furnish every floor of a built stair interior that fits (ADR-034); return one message per floor left bare.

        A furnished floor record gains ``furnishing``. The light of each floor stays where it was; when any floor is
        furnished, the probes are placed again so no probe column of a floor stands in that floor's pieces.
        """
        records = built.spec["rooms"]
        messages, floors = self._furnish(building_id, floor_rooms(plan, records, penetrations, ic, self.furnish), records)
        if floors:
            built.light_anchors = light_anchors(plan, records, room_furniture=floors)
        return messages

    def stats(self) -> dict[str, Any]:
        """``counts.shop_furnishing`` (or ``counts.floor_furnishing``) in ``detail_report.json``."""
        return {
            self.room_key: self.rooms, "reachable_rooms": self.reachable, "furnished_rooms": self.furnished,
            "pieces": sum(self.pieces.values()), "pieces_by_asset": dict(sorted(self.pieces.items())),
            "refusals_by_reason": dict(sorted(self.refused.items())),
            "piece_refusals_by_reason": dict(sorted(self.piece_refusals.items())),
            "visual_triangles": self.budget.used["triangles"], "max_triangles": self.budget.max_triangles,
            "collision_triangles": self.budget.used["collision_triangles"], "max_collision_triangles": self.budget.max_collision_triangles,
            "assets": len(self.budget.assets), "max_assets": self.budget.max_assets,
            "materials_added": sum(self.catalogue[i].parts for i in self.budget.assets),
        }
