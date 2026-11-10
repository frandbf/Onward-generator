"""Catalogue furniture in the generic shop rooms (ADR-031). Off by default (``interiors.shops.furnish``).

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

The model's front is assumed to be its local -Y (glTF +Z): ``yaw_deg`` turns it to face into the room, and a
set entry's ``turn_deg`` turns it first. That facing is an assumption for the headset check.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from shapely import LineString, Point, Polygon, unary_union
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient

from onward_generator.detail.config import FURNISH_SLOTS, FurnishEntry, FurnishSet, Interiors, ShopFurnish
from onward_generator.detail.facades import Hole, Segment, stable_unit
from onward_generator.detail.interiors import SHOP_GLASS, Interior, InteriorPlan, _door_zone, _rim_on, light_anchors
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
    """One shop room to furnish. ``door_zone`` is ``None`` for a room with no walkable door (seen through its windows only).

    ``panes`` are the keep-out bands behind see-through openings; ``display`` the inner spans of its ``shop_glass``
    openings; ``door_span`` the inner span of its door.
    """

    room_id: str
    polygon: Polygon
    door_zone: Polygon | None
    ceiling_m: float
    category: str = "generic"
    panes: tuple[Polygon, ...] = ()
    display: tuple[LineString, ...] = ()
    door_span: LineString | None = None


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
        """The plan of the pieces on the floor; decor stands on a counter."""
        return unary_union([p.footprint for p in self.pieces if p.slot != "decor"])

    def record(self, building_id: str, first: int) -> dict[str, Any]:
        """The ``furnishing`` block of the room record; piece ids ``furnish/<building>/<n>`` from ``first``."""
        return {
            "method": "shop_furnishing", "attribute_origin": "generated", "set": self.category,
            "pieces": [p.record(f"furnish/{building_id}/{first + i}") for i, p in enumerate(self.pieces)],
            "visual_triangles": self.visual_triangles, "collision_triangles": self.collision_triangles,
            "refusals": list(self.refusals),
        }


@dataclass(slots=True)
class FurnishBudget:
    """What the map's furniture has used of ``shops.furnish``: drawn and collision triangles, and distinct assets.

    Separate from ``shops.max_triangles``: furniture never takes a shop room's budget (ADR-031).
    """

    max_triangles: int
    max_collision_triangles: int
    max_assets: int
    used: dict[str, int] = field(default_factory=lambda: {"triangles": 0, "collision_triangles": 0})
    assets: set[str] = field(default_factory=set)

    @classmethod
    def of(cls, furnish: ShopFurnish) -> FurnishBudget:
        return cls(furnish.max_triangles, furnish.max_collision_triangles, furnish.max_assets)

    def refuse(self, room: RoomFurnishing) -> str | None:
        """``None`` when the whole room fits, else the reason. A room is furnished whole or not at all."""
        for kind, cost, limit, label in (
            ("triangles", room.visual_triangles, self.max_triangles, "triangle_budget"),
            ("collision_triangles", room.collision_triangles, self.max_collision_triangles, "collision_budget"),
        ):
            left = limit - self.used[kind]
            if cost > left:
                return f"{label}: needs {cost} {kind.replace('_', ' ')}, {left} left of shops.furnish.max_{kind} {limit}"
        wanted = len(self.assets | room.assets)
        if wanted > self.max_assets:
            return f"asset_cap: needs {wanted} distinct assets, {self.max_assets} allowed by shops.furnish.max_assets"
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

    return slot != "decor" and is_solid(asset.role, asset.category, asset.size_m)


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
        self.keepout = unary_union([room.door_zone, *room.panes])
        self.half_walk = furnish.walk_width_m / 2
        empty = self.polygon.buffer(-self.half_walk)
        self.reached_empty = [c for c in polygons_of(empty) if c.intersects(self.door_zone)]
        self.islands = unary_union([c for c in polygons_of(empty) if not c.intersects(self.door_zone)])
        self.edges = room_edges(room.polygon)
        self.pieces: list[Piece] = []
        self.fronts: list[Polygon] = []
        self.refusals: set[str] = set()
        self._allowed: BaseGeometry | None = None

    def add(self, piece: Piece) -> None:
        self.pieces.append(piece)
        self._allowed = None

    @property
    def full(self) -> bool:
        return len(self.pieces) >= self.cfg.max_pieces_per_room

    def allowed(self) -> BaseGeometry:
        """The floor a further piece may cover: the free floor less the keep-outs, the front zones, and the pieces with
        ``piece_gap_m`` round each."""
        if self._allowed is None:
            placed = [p.footprint.buffer(self.cfg.piece_gap_m, join_style="mitre") for p in self.pieces if p.slot != "decor"]
            self._allowed = self.free.difference(unary_union([self.keepout, *placed, *self.fronts]))
        return self._allowed

    def conflict(self, footprint: Polygon) -> str | None:
        """Why a floor piece cannot stand on ``footprint``: off the floor, in a keep-out, or on another piece."""
        if not self.free.buffer(1e-7, join_style="mitre").contains(footprint):
            return "outside_room"
        if footprint.intersection(self.door_zone).area > SLIVER_M2:
            return "door_zone"
        if any(footprint.intersection(pane).area > SLIVER_M2 for pane in self.room.panes):
            return "pane_band"
        if any(footprint.intersection(p.footprint).area > SLIVER_M2 for p in self.pieces if p.slot != "decor"):
            return "overlap"
        return None

    def blocks(self, footprint: Polygon, front: Polygon) -> bool:
        """Whether a floor piece on ``footprint`` with its ``front`` zone would cut the clear path (``blocks_path``)."""
        if not self.polygon.buffer(1e-7, join_style="mitre").contains(front):
            return True
        if any(front.intersection(p.footprint).area > SLIVER_M2 for p in self.pieces if p.slot != "decor"):
            return True
        if any(footprint.intersection(zone).area > SLIVER_M2 for zone in self.fronts):
            return True
        return self.path_cut([footprint], [front])

    def path_cut(self, extra: Sequence[Polygon] = (), fronts: Sequence[Polygon] = ()) -> bool:
        """Whether the pieces and ``extra`` cut a place the empty room reached off the door, or leave a front zone unreached."""
        floor = [p.footprint for p in self.pieces if p.slot != "decor"] + list(extra)
        walk = self.polygon.difference(unary_union(floor)).buffer(-self.half_walk)
        reached = unary_union([c for c in polygons_of(walk) if c.intersects(self.door_zone)])
        if reached.is_empty:
            return True
        if walk.difference(reached).difference(self.islands).area > ISLAND_M2:
            return True
        return any(not front.intersects(reached) for front in (*self.fronts, *fronts))

    def fits_height(self, asset: FurnishAsset, z: float = 0.0) -> bool:
        return z + asset.size_m[2] <= self.room.ceiling_m - self.cfg.head_clear_m + 1e-9

    def make(self, entry: FurnishEntry, slot: str, edge: _Edge, s: float) -> tuple[Piece, Polygon]:
        """The piece of ``entry`` with its back on ``edge`` from ``s``, and its front zone, ``walk_width_m`` deep."""
        asset = self.catalogue[entry.id]
        along, depth = footprint_size(asset, entry.turn_deg)
        g = self.cfg.wall_gap_m
        footprint = edge.rect(s, s + along, g, g + depth)
        centre = edge.a + edge.u * (s + along / 2) + edge.n * (g + depth / 2)
        yaw = (edge.yaw_deg + entry.turn_deg) % 360.0
        piece = Piece(asset, slot, footprint, (float(centre[0]), float(centre[1])), 0.0, yaw, entry.turn_deg, piece_solid(asset, slot))
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
        entries = _ordered(self.room_set().decor, self.seed, self.building_id, self.room.room_id, "decor")
        if not entries:
            return
        top = counter.z_m + counter.asset.size_m[2]
        c_along, c_depth = footprint_size(counter.asset, counter.turn_deg)
        theta = math.radians(counter.yaw_deg - counter.turn_deg)
        along_axis = np.array([-math.cos(theta), -math.sin(theta)])  # the wall direction ``u`` the counter was set on
        centre = np.array(counter.centre)
        for k in range(DECOR_PER_COUNTER):
            if self.full:
                return
            offset = (k + 1) / (DECOR_PER_COUNTER + 1) - 0.5
            for entry in entries[k:] + entries[:k]:
                asset = self.catalogue[entry.id]
                along, depth = footprint_size(asset, entry.turn_deg)
                if along > c_along / DECOR_PER_COUNTER or depth > c_depth or not self.fits_height(asset, top):
                    continue
                at = centre + along_axis * offset * c_along
                half_u, half_n = along_axis * along / 2, np.array([-along_axis[1], along_axis[0]]) * depth / 2
                corners = [at - half_u - half_n, at + half_u - half_n, at + half_u + half_n, at - half_u + half_n]
                footprint = Polygon([(float(p[0]), float(p[1])) for p in corners])
                self.add(Piece(asset, "decor", footprint, (float(at[0]), float(at[1])), top,
                                         (counter.yaw_deg - counter.turn_deg + entry.turn_deg) % 360.0, entry.turn_deg, False))
                break

    def place_walls(self) -> None:
        """Round after round, one wall piece on each edge that still has room, longest edge first, until the room is full."""
        open_edges = sorted(self.edges, key=lambda e: (-round(e.length, 6), e.ring, e.index))
        k = 0
        while open_edges and not self.full:
            open_edges = [edge for edge in open_edges if not self.full and self.place_wall(edge, k)]
            k += 1

    def place_wall(self, edge: _Edge, k: int) -> bool:
        """Put the ``k``-th wall piece of ``edge`` at the first free stretch it fits; ``False`` when none fits."""
        for entry in _ordered(self.room_set().wall, self.seed, self.building_id, self.room.room_id, "wall", edge.ring, edge.index, k):
            asset = self.catalogue[entry.id]
            if not self.fits_height(asset):
                continue
            along, depth = footprint_size(asset, entry.turn_deg)
            fitting = [lo for lo, hi in free_intervals(edge, depth, self.allowed(), self.cfg.wall_gap_m) if hi - lo >= along - 1e-9]
            if not fitting:
                continue
            piece, front = self.make(entry, "wall", edge, fitting[0])
            if self.conflict(piece.footprint) is not None:
                continue
            if self.blocks(piece.footprint, front):
                self.refusals.add("blocks_path")
                continue
            self.fronts.append(front)
            self.add(piece)
            return True
        return False

    def room_set(self) -> FurnishSet:
        return self.cfg.set_for(self.room.category)


def plan_room(room: FurnishRoom, furnish: ShopFurnish, catalogue: Mapping[str, FurnishAsset], seed: int, building_id: str) -> RoomFurnishing | str:
    """The furniture of one shop room, or why it gets none: ``unreachable`` (no door), ``blocks_path`` (the empty room
    has no clear path from its door, or every piece that fitted would cut it), ``no_free_wall`` (nothing fitted).

    The counter comes first, then its decor, then the wall pieces. Budgets are the caller's (``FurnishBudget``).
    """
    if room.door_zone is None:
        return "unreachable"
    planner = _Planner(room, furnish, catalogue, seed, building_id)
    if not planner.reached_empty:
        return "blocks_path"
    counter = planner.place_counter()
    if counter is not None:
        planner.place_decor(counter)
    planner.place_walls()
    if not planner.pieces:
        return "blocks_path" if "blocks_path" in planner.refusals else "no_free_wall"
    refusals = tuple(r for r in PIECE_REFUSALS if r in planner.refusals)
    return RoomFurnishing(room.room_id, room.category if room.category in furnish.sets else "generic", tuple(planner.pieces), refusals)


class FurnishError(ValueError):
    """The furnishing sets name a row the catalogues cannot give."""


def load_catalogue(furnish: ShopFurnish, root: Path) -> dict[str, FurnishAsset]:
    """Every row the sets name, read from the allow-listed catalogues under ``root`` as overgrow will read it.

    The triangles are those after the load-time level of detail, so detail prices what overgrow builds. A row
    that is not installed, or that the interior loader refuses, is an error naming the row and the reason.
    """
    from onward_generator.overgrow.library import read_interior_catalogue  # overgrow imports detail; load it on use

    ids = sorted({entry.id for chosen in furnish.sets.values() for slot in FURNISH_SLOTS for entry in chosen.slot(slot)})
    key = (str(root), furnish.catalogues, tuple(ids))
    if key in _CATALOGUES:  # the budget core runs detail several times in one process
        return _CATALOGUES[key]
    props, rejects = read_interior_catalogue(root, furnish.catalogues, ids)
    why = {reject.asset_id: reject.reason for reject in rejects}
    missing = [f"{i} ({why.get(i, 'not installed')})" for i in ids if i not in props]
    if missing:
        raise FurnishError(f"shops.furnish: rows the catalogues under {root} do not give: " + ", ".join(missing))
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


@dataclass(slots=True)
class ShopFurnishing:
    """The map's furnishing pass: one call per built shop interior, in the order they were built."""

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

    @classmethod
    def of(cls, furnish: ShopFurnish, catalogue: Mapping[str, FurnishAsset], seed: int) -> ShopFurnishing:
        return cls(furnish, catalogue, seed, FurnishBudget.of(furnish))

    def furnish_interior(self, building_id: str, plan: InteriorPlan, built: Interior, penetrations: Sequence[tuple[Segment, Hole]],
                         ic: Interiors) -> list[str]:
        """Furnish every room of a built shop interior that fits; return one message per room left bare.

        A furnished room record gains ``furnishing``. When any room is furnished, the light probes are
        placed again so no probe column stands in a piece. A room left bare keeps its shop interior.
        """
        records = built.spec["rooms"]
        messages: list[str] = []
        floors: list[BaseGeometry] = []
        first = 0
        for room in furnish_rooms(plan, records, penetrations, ic):
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
            next(r for r in records if r["id"] == room.room_id)["furnishing"] = result.record(building_id, first)
            first += len(result.pieces)
            floors.append(result.floor_footprints)
        if floors:
            built.light_anchors = light_anchors(plan, records, furniture=unary_union(floors))
        return messages

    def stats(self) -> dict[str, Any]:
        """``counts.shop_furnishing`` in ``detail_report.json``."""
        return {
            "shop_rooms": self.rooms, "reachable_rooms": self.reachable, "furnished_rooms": self.furnished,
            "pieces": sum(self.pieces.values()), "pieces_by_asset": dict(sorted(self.pieces.items())),
            "refusals_by_reason": dict(sorted(self.refused.items())),
            "piece_refusals_by_reason": dict(sorted(self.piece_refusals.items())),
            "visual_triangles": self.budget.used["triangles"], "max_triangles": self.budget.max_triangles,
            "collision_triangles": self.budget.used["collision_triangles"], "max_collision_triangles": self.budget.max_collision_triangles,
            "assets": len(self.budget.assets), "max_assets": self.budget.max_assets,
            "materials_added": sum(self.catalogue[i].parts for i in self.budget.assets),
        }
