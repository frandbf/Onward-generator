"""Café terraces before the bars of a lived street (plan 2026-11-16, increment 2; ADR-044).

A street mood with ``"terraces": true`` (``config/street_moods.json``) asks overgrow for them. For each shop
``detail`` records in a category of ``config/terraces.json`` (``bar`` covers OSM cafés, bars, pubs and restaurants),
this module reads the shop's street door, finds the wall it stands in and the street before it, and sets units along
that wall, alternating right and left of the door and leaving ``door_clear_m`` either side of it free:

- a table with a chair on each side, facing it, ``sets_per_shop`` times;
- then a menu board, then a planter, when the configuration names one.

A terrace stands only before a pedestrian street or in a plaza: the nearest street within ``street_search_m`` must
carry a listed ``highway`` value, or the unit stand in a pedestrian area. A unit stands only where ``passage_m`` stays
free from its outer edge to the building across, and clear of the zone's edge, a spawn, another shop's door, the urban
elements of ``detail``, the pinned props and the other units. Pieces are rows of the allow-listed CC0 interior
catalogues (``read_interior_catalogue``); the first id of each slot that is installed is used everywhere, so every
terrace of a map matches. Nothing is random: the same scene builds the same terraces.

A table, a board and a planter the size of cover get a box collider (``ambient.is_solid``); a chair stays visual, so a
player walks through the seats as through a crowd of them on the headset.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

import numpy as np
from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match
from shapely import LineString, Point, Polygon, STRtree
from shapely.geometry import shape

from onward_generator.detail.config import FURNISH_CATALOGUES
from onward_generator.detail.modules import yaw_matrix
from onward_generator.generation.gltf import Node, Primitive
from onward_generator.generation.mesh import box
from onward_generator.overgrow.ambient import is_solid
from onward_generator.overgrow.library import InteriorProp, read_interior_catalogue
from onward_generator.overgrow.zone import Zone

REPO_ROOT = Path(__file__).resolve().parents[3]
TERRACES_PATH = REPO_ROOT / "config" / "terraces.json"
SCHEMA_PATH = REPO_ROOT / "config" / "terraces.schema.json"
SLOTS = ("table", "chair", "board", "planter")
NODE_PREFIX = "overgrow/terrace/"
COLLIDER_PREFIX = "overgrow/collider/terrace/"
WALL_SEARCH_M = 1.0
"""How far from a door its wall is looked for: ``detail`` writes the door on the footprint's edge."""
BACKING_M = 0.3
"""The strip this deep behind a unit, along the wall's line, must be building: a unit may run past its own footprint's
edge along the next facade in line (a Catastro part is often 3 to 5 m long, too short for a table and its chairs), but
never past a corner or across a side street."""
BACKING_SHARE = 0.9
"""The share of that strip that must be building."""
RAY_START_M = 0.05
"""The ray across the street starts this far out of the wall, so the shop's own footprint does not stop it."""
DOOR_KEEP_M = 0.6
"""No unit comes nearer than this to another shop's door."""
STREET_PROBE_M = 1.5
"""The street is looked for this far out of the terrace, not at the wall: a party wall's alley is nearer the wall."""
HEADROOM_SHARE = 0.25
"""The share of the map's triangle headroom the terraces may spend before the street props take what is left."""
NOT_STREETS = frozenset({"corridor", "steps", "elevator", "proposed", "construction"})


class TerraceError(ValueError):
    """``config/terraces.json`` is not valid."""


@dataclass(frozen=True, slots=True)
class TerraceConfig:
    shop_categories: frozenset[str]
    highways: frozenset[str]
    street_search_m: float
    passage_m: float
    wall_gap_m: float
    door_clear_m: float
    piece_gap_m: float
    set_gap_m: float
    reach_m: float
    along_m: float
    sets_per_shop: int
    max_sets: int
    pieces: Mapping[str, tuple[str, ...]]


def parse_terraces(doc: object, schema: Mapping[str, Any], where: str = "terraces") -> TerraceConfig:
    """The checked configuration."""
    first = best_match(Draft202012Validator(schema).iter_errors(doc))
    if first is not None:
        path = "/".join(str(part) for part in first.absolute_path)
        raise TerraceError(f"{where}: {path}: {first.message}" if path else f"{where}: {first.message}")
    assert isinstance(doc, dict)  # the schema requires an object  # pragma: no mutate: narrows the type only
    return TerraceConfig(
        shop_categories=frozenset(doc["shop_categories"]),
        highways=frozenset(doc["highways"]),
        street_search_m=float(doc["street_search_m"]),
        passage_m=float(doc["passage_m"]),
        wall_gap_m=float(doc["wall_gap_m"]),
        door_clear_m=float(doc["door_clear_m"]),
        piece_gap_m=float(doc["piece_gap_m"]),
        set_gap_m=float(doc["set_gap_m"]),
        reach_m=float(doc["reach_m"]),
        along_m=float(doc["along_m"]),
        sets_per_shop=int(doc["sets_per_shop"]),
        max_sets=int(doc["max_sets"]),
        pieces=MappingProxyType({slot: tuple(doc["pieces"][slot]) for slot in SLOTS}),
    )


@cache
def load_terraces(path: Path = TERRACES_PATH) -> TerraceConfig:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TerraceError(f"{path}: {exc}") from exc
    return parse_terraces(doc, schema, str(path))


@dataclass(frozen=True, slots=True)
class Front:
    """A shop's street door: its building, its ``detail`` category and the door's plan point."""

    building_id: str
    category: str
    door: tuple[float, float]


def shop_doors(manifest: Mapping[str, Any]) -> list[tuple[str, str, tuple[float, float]]]:
    """``(building id, category, door)`` of every street door of a shop room ``detail`` recorded, in record order."""
    found = []
    for item in manifest.get("objects") or []:
        if not isinstance(item, dict) or item.get("type") != "interior":
            continue
        building = str(item.get("building_id") or "")
        rooms = {str(room.get("id")): room for room in item.get("rooms") or [] if isinstance(room, dict)}
        for door in item.get("connections") or []:
            if not isinstance(door, dict) or door.get("type") != "door" or door.get("from") != "exterior":
                continue
            room = rooms.get(str(door.get("to")))
            shop = (room or {}).get("shop") or item.get("shop")
            if not isinstance(shop, dict) or not shop.get("category"):
                continue
            x, y = (float(value) for value in door["position_m"][:2])
            found.append((building, str(shop["category"]), (x, y)))
    return found


def shop_fronts(manifest: Mapping[str, Any], categories: Collection[str]) -> list[Front]:
    """The street doors of the shops of ``categories``, one per building and door, sorted so a run is repeatable."""
    fronts = {(building, door): Front(building, category, door)
              for building, category, door in shop_doors(manifest) if category in categories}
    return [fronts[key] for key in sorted(fronts)]


def other_doors(manifest: Mapping[str, Any]) -> list[tuple[float, float]]:
    """Every street door ``detail`` drew: shop doors and the facades' own doors."""
    doors = [door for _, _, door in shop_doors(manifest)]
    for item in manifest.get("objects") or []:
        if isinstance(item, dict) and item.get("type") == "building":
            doors.extend((float(x), float(y)) for x, y in (item.get("facade") or {}).get("doors_m") or [])
    return doors


@dataclass(frozen=True, slots=True)
class Wall:
    """The straight run of footprint a door stands in: its start, unit direction, outward normal, length, and the door's distance along it."""

    a: np.ndarray
    u: np.ndarray
    n: np.ndarray
    length: float
    door_s: float

    def point(self, s: float, d: float) -> np.ndarray:
        return self.a + self.u * s + self.n * d

    def rect(self, s0: float, s1: float, d0: float, d1: float) -> Polygon:
        return Polygon([tuple(self.point(s, d)) for s, d in ((s0, d0), (s1, d0), (s1, d1), (s0, d1))])


def wall_at(door: tuple[float, float], buildings: STRtree) -> Wall | None:
    """The footprint edge nearest ``door`` within ``WALL_SEARCH_M``, its normal pointing out of the footprint."""
    here = np.asarray(door)
    best: tuple[float, np.ndarray, np.ndarray, float, float, Polygon] | None = None
    for index in buildings.query(Point(door).buffer(WALL_SEARCH_M)):
        polygon = buildings.geometries[int(index)]
        if not isinstance(polygon, Polygon):
            continue
        for ring in (polygon.exterior, *polygon.interiors):
            coords = np.asarray(ring.coords)[:, :2]
            for a, b in zip(coords[:-1], coords[1:]):
                length = float(np.linalg.norm(b - a))
                if length < 1e-6:  # pragma: no mutate: a repeated vertex; any tiny bound skips it the same
                    continue
                u = (b - a) / length
                along = min(max(float((here - a) @ u), 0.0), length)
                gap = float(np.linalg.norm(here - (a + u * along)))
                if best is None or gap < best[0]:
                    best = (gap, a, u, length, along, polygon)
    if best is None or best[0] > WALL_SEARCH_M:
        return None
    _, a, u, length, along, polygon = best
    n = np.array([u[1], -u[0]])
    if polygon.covers(Point(*(a + u * along + n * RAY_START_M))):
        n = -n
    return Wall(a, u, n, length, along)


@dataclass(frozen=True, slots=True)
class Streets:
    lines: tuple[tuple[LineString, str], ...]
    plazas: tuple[Polygon, ...]


def streets_of(features: Sequence[Mapping[str, Any]]) -> Streets:
    """The street centre lines with their ``highway`` value, and the pedestrian areas (plazas), of a geo scene."""
    lines: list[tuple[LineString, str]] = []
    plazas: list[Polygon] = []
    for feature in features:
        if feature.get("kind") != "highway" or not isinstance(feature.get("geometry"), dict):
            continue
        highway = str((feature.get("tags") or {}).get("highway") or "")
        geom = shape(feature["geometry"])
        if geom.geom_type in {"Polygon", "MultiPolygon"}:
            if highway == "pedestrian":
                plazas.extend(getattr(geom, "geoms", [geom]))
        elif highway not in NOT_STREETS:
            lines.extend((part, highway) for part in getattr(geom, "geoms", [geom]))
    return Streets(tuple(lines), tuple(plazas))


def street_reason(point: np.ndarray, streets: Streets, config: TerraceConfig) -> str | None:
    """None when ``point`` is in a plaza or its nearest street is pedestrian; else why not."""
    here = Point(float(point[0]), float(point[1]))
    if any(plaza.covers(here) for plaza in streets.plazas):
        return None
    nearest = min(((line.distance(here), highway) for line, highway in streets.lines), default=None)
    if nearest is None or nearest[0] > config.street_search_m:
        return "no_street"
    return None if nearest[1] in config.highways else "not_pedestrian"


def free_across(start: np.ndarray, normal: np.ndarray, buildings: STRtree, reach_m: float) -> float:
    """How far a ray from ``start`` along ``normal`` runs before it meets a building, ``reach_m`` at most."""
    ray = LineString([tuple(start), tuple(start + normal * reach_m)])
    origin = Point(float(start[0]), float(start[1]))
    found = reach_m
    for index in buildings.query(ray, predicate="intersects"):  # pragma: no mutate: a box-only candidate meets the ray in nothing, whose NaN distance min() keeps out
        found = min(found, origin.distance(buildings.geometries[int(index)].intersection(ray)))
    return found


@dataclass(frozen=True, slots=True)
class Placed:
    """One piece of a unit: the row, its plan centre and the way its front faces."""

    slot: str
    piece: InteriorProp
    x: float
    y: float
    yaw_deg: float


@dataclass(slots=True)
class TerraceResult:
    nodes: list[Node] = field(default_factory=list)
    colliders: list[Node] = field(default_factory=list)
    images: dict[str, bytes] = field(default_factory=dict)
    footprints: list[Polygon] = field(default_factory=list)
    records: list[dict[str, Any]] = field(default_factory=list)
    triangles: int = 0
    sets: int = 0
    shops: int = 0
    skipped: Counter[str] = field(default_factory=Counter)
    pieces: dict[str, str] = field(default_factory=dict)
    missing: dict[str, str] = field(default_factory=dict)
    """Slots none of whose ids is installed, with why each was refused."""
    pieces_drawn: set[str] = field(default_factory=set)
    """Models whose mesh a node already carries; the next copies share it by ``mesh_key``."""

    def report(self) -> dict[str, Any]:
        """``terraces`` in ``overgrow_report.json``."""
        return {"shops": self.shops, "sets": self.sets, "pieces": len(self.nodes), "colliders": len(self.colliders),
                "triangles": self.triangles, "models": dict(sorted(self.pieces.items())), "skipped": dict(sorted(self.skipped.items())),
                "missing": dict(sorted(self.missing.items()))}


def facing_yaw(facing: np.ndarray) -> float:
    """The yaw in degrees that turns a catalogue model, whose front faces -Y, to face ``facing`` (``detail/furnish.py``)."""
    return math.degrees(math.atan2(float(facing[0]), -float(facing[1]))) % 360.0


def _size(piece: InteriorProp) -> tuple[float, float]:
    """(across its front, front to back) of the model's box."""
    low, high = piece.prop.mesh.vertices.min(axis=0), piece.prop.mesh.vertices.max(axis=0)
    return float(high[0] - low[0]), float(high[1] - low[1])


def unit_size(slot: str, models: Mapping[str, InteriorProp], config: TerraceConfig) -> tuple[float, float]:
    """(width along the wall, depth out of it) of one unit: a table with a chair on each side, or a single piece."""
    if slot != "table":
        return _size(models[slot])
    table_w, table_d = _size(models["table"])
    chair_w, chair_d = _size(models["chair"])
    return table_w + 2 * (config.piece_gap_m + chair_d), max(table_d, chair_w)


def unit_pieces(slot: str, models: Mapping[str, InteriorProp], wall: Wall, centre_s: float, config: TerraceConfig) -> list[Placed]:
    """The pieces of one unit centred ``centre_s`` along ``wall``: a single piece faces the street, the chairs face their table."""
    middle = config.wall_gap_m + unit_size(slot, models, config)[1] / 2
    at = wall.point(centre_s, middle)
    pieces = [Placed(slot, models[slot], float(at[0]), float(at[1]), facing_yaw(wall.n))]
    if slot == "table":
        offset = _size(models["table"])[0] / 2 + config.piece_gap_m + _size(models["chair"])[1] / 2
        for side in (1.0, -1.0):
            at = wall.point(centre_s + side * offset, middle)
            pieces.append(Placed("chair", models["chair"], float(at[0]), float(at[1]), facing_yaw(-side * wall.u)))
    return pieces


def chosen_models(library: str | Path, config: TerraceConfig) -> tuple[dict[str, InteriorProp], dict[str, str]]:
    """The first installed row of each slot, and why each slot found none (a table or a chair missing sets no terrace)."""
    wanted = {item for slot in SLOTS for item in config.pieces[slot]}  # pragma: no mutate: the ids only spare the GLBs no slot names
    rows, rejects = read_interior_catalogue(library, FURNISH_CATALOGUES, wanted)  # pragma: no mutate: the ids only spare the GLBs no slot names
    why = {reject.asset_id: reject.reason for reject in rejects}
    models: dict[str, InteriorProp] = {}
    missing: dict[str, str] = {}
    for slot in SLOTS:
        found = next((rows[item] for item in config.pieces[slot] if item in rows), None)
        if found is not None:
            models[slot] = found
        elif config.pieces[slot]:
            missing[slot] = ", ".join(f"{item} ({why.get(item, 'not installed')})" for item in config.pieces[slot])
    return models, missing


def place_terraces(
    manifest: Mapping[str, Any],
    features: Sequence[Mapping[str, Any]],
    buildings: STRtree,
    zone: Zone,
    library: str | Path | None,
    *,
    budget: int,
    ground_z: Callable[[float, float], float],
    near_spawn: Callable[[float, float], bool],
    keep_out: Sequence[Polygon] = (),
    config: TerraceConfig | None = None,
) -> TerraceResult:
    """The terraces of every shop front that allows one, within ``budget`` triangles."""
    cfg = config or load_terraces()
    result = TerraceResult()
    fronts = shop_fronts(manifest, cfg.shop_categories)
    if not fronts:
        return result
    if library is None:
        result.skipped["no_library"] += len(fronts)
        return result
    models, result.missing = chosen_models(library, cfg)
    if "table" not in models or "chair" not in models:
        result.skipped["no_table_or_chair"] += len(fronts)
        return result
    result.pieces = {slot: piece.prop.spec.id for slot, piece in models.items()}
    streets = streets_of(features)
    doors = other_doors(manifest)
    units = ["table"] * cfg.sets_per_shop + [slot for slot in ("board", "planter") if slot in models]
    for front in fronts:
        if result.sets >= cfg.max_sets:
            result.skipped["max_sets"] += 1
            continue
        wall = wall_at(front.door, buildings)
        if wall is None:
            result.skipped["no_wall"] += 1
            continue
        reason = street_reason(wall.point(wall.door_s, cfg.wall_gap_m + STREET_PROBE_M), streets, cfg)
        if reason is not None:
            result.skipped[reason] += 1
            continue
        own = [door for door in doors if math.dist(door, front.door) > 1e-6]  # pragma: no mutate: the front's own door is the same point
        cursors = {1.0: wall.door_s + cfg.door_clear_m, -1.0: wall.door_s - cfg.door_clear_m}
        stood = False
        for number, slot in enumerate(units):
            if slot == "table" and result.sets >= cfg.max_sets:
                continue  # the map's last table is set; its board and planter still go with it
            first = 1.0 if number % 2 == 0 else -1.0
            for side in (first, -first):
                why = _try_unit(slot, models, wall, side, cursors, cfg, result, buildings, zone, own, keep_out,
                                budget, ground_z, near_spawn, front)
                if why is None:
                    stood = True
                    break
            else:
                result.skipped[cast(str, why)] += 1  # the reason of the second side tried
        result.shops += stood
    return result


def _try_unit(
    slot: str, models: Mapping[str, InteriorProp], wall: Wall, side: float, cursors: dict[float, float], cfg: TerraceConfig,
    result: TerraceResult, buildings: STRtree, zone: Zone, doors: Sequence[tuple[float, float]], keep_out: Sequence[Polygon],
    budget: int, ground_z: Callable[[float, float], float], near_spawn: Callable[[float, float], bool], front: Front,
) -> str | None:
    """Set one unit on ``side`` of the door when it keeps every rule; None when it stands, else why not."""
    width, depth = unit_size(slot, models, cfg)
    start = cursors[side]
    centre_s = start + side * width / 2
    low, high = sorted((start, start + side * width))
    if max(abs(low - wall.door_s), abs(high - wall.door_s)) > cfg.along_m:
        return "no_frontage"
    pieces = unit_pieces(slot, models, wall, centre_s, cfg)
    footprint = wall.rect(low, high, cfg.wall_gap_m, cfg.wall_gap_m + depth)
    middle = wall.point(centre_s, cfg.wall_gap_m + depth / 2)
    triangles = sum(piece.piece.prop.triangles for piece in pieces)
    free = free_across(wall.point(centre_s, RAY_START_M), wall.n, buildings, cfg.reach_m) - RAY_START_M
    need = cfg.wall_gap_m + depth + cfg.passage_m
    reason = None
    if not _backed(wall.rect(low, high, -BACKING_M, 0.0), buildings):
        reason = "no_wall_behind"
    elif not all(zone.contains(*point) for point in footprint.exterior.coords):
        reason = "outside_zone"
    elif near_spawn(float(middle[0]), float(middle[1])):
        reason = "spawn"
    elif free < need:  # pragma: no mutate: < or <=: a passage of exactly passage_m, to the float, is not one a test can draw
        reason = "narrow"
    elif len(buildings.query(footprint, predicate="intersects")):
        reason = "building"
    elif any(footprint.distance(Point(door)) < DOOR_KEEP_M for door in doors):  # pragma: no mutate: < or <=: a door exactly DOOR_KEEP_M away, to the float
        reason = "door"
    elif any(footprint.intersects(area) for area in keep_out):
        reason = "urban_element"
    elif any(footprint.intersects(taken) for taken in result.footprints):
        reason = "overlap"
    elif result.triangles + triangles > budget:
        reason = "budget"
    if reason is not None:
        return reason
    cursors[side] = start + side * (width + cfg.set_gap_m)
    unit = len(result.footprints)
    for index, piece in enumerate(pieces):
        _emit(piece, unit, index, result, ground_z, front)
    result.footprints.append(footprint)
    result.sets += slot == "table"
    return None


def _backed(strip: Polygon, buildings: STRtree) -> bool:
    """Whether buildings cover ``BACKING_SHARE`` of ``strip``."""
    candidates = buildings.query(strip, predicate="intersects")  # pragma: no mutate: a box-only candidate covers no area of the strip
    covered = sum(buildings.geometries[int(index)].intersection(strip).area for index in candidates)
    return covered >= BACKING_SHARE * strip.area


def _emit(placed: Placed, unit: int, index: int, result: TerraceResult, ground_z: Callable[[float, float], float], front: Front) -> None:
    prop = placed.piece.prop
    asset_id = prop.spec.id
    low, high = prop.mesh.vertices.min(axis=0), prop.mesh.vertices.max(axis=0)
    centre = np.eye(4)
    centre[:2, 3] = -(low[:2] + high[:2]) / 2  # the plan places the middle of the model's box, as furnishing does
    z = ground_z(placed.x, placed.y)
    matrix = yaw_matrix(placed.x, placed.y, z, math.radians(placed.yaw_deg)) @ centre
    first = asset_id not in result.pieces_drawn
    result.pieces_drawn.add(asset_id)
    name = f"{unit}/{index}"
    length, width, height = (float(v) for v in high - low)
    solid = placed.slot != "chair" and is_solid(prop.spec.role, prop.spec.category, (length, width, height))
    result.nodes.append(Node(
        NODE_PREFIX + name,
        prop.primitives() if first else (),
        {"type": "overgrowth", "origin": "generated", "attribute_origin": "generated", "semantics_origin": "estimated",
         "kind": "terrace", "slot": placed.slot, "asset_id": asset_id, "catalogue": placed.piece.catalogue,
         "licence": placed.piece.licence, "role": prop.spec.role, "building_id": front.building_id, "solid": solid},
        matrix,
        f"prop:{asset_id}",
    ))
    result.images.update(prop.images())
    result.triangles += prop.triangles
    if solid:
        result.colliders.append(Node(
            COLLIDER_PREFIX + name,
            (Primitive(box(low[0], high[0], low[1], high[1], low[2], high[2]), "collider"),),
            {"type": "collision", "origin": "generated", "asset_id": asset_id, "role": prop.spec.role, "terrace": True},
            matrix,
        ))
    result.records.append({
        "kind": "terrace", "origin": "generated", "source_id": front.building_id,
        "position_m": [round(placed.x, 3), round(placed.y, 3), round(z, 3)],
        "asset_id": asset_id, "role": placed.slot, "size_m": [round(length, 3), round(width, 3), round(height, 3)], "solid": solid,
    })


__all__ = ["COLLIDER_PREFIX", "HEADROOM_SHARE", "NODE_PREFIX", "TERRACES_PATH", "Front", "TerraceConfig", "TerraceError", "TerraceResult", "Wall",
           "chosen_models", "facing_yaw", "free_across", "load_terraces", "other_doors", "parse_terraces", "place_terraces",
           "shop_doors", "shop_fronts", "street_reason", "streets_of", "unit_pieces", "wall_at"]
