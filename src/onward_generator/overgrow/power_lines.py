"""Overhead power lines: poles along the streets, wires between them and service drops to the facades (ADR-045).

A city whose streets are wired overhead (Mogadishu, Fallujah, much of Latin America) reads wrong without its poles,
and a modern European centre reads wrong with them. ``config/overhead_lines.json`` lists the facade styles whose maps
get them by default; ``map.json`` ``"overhead_lines"`` overrides either way. OSM rarely maps a pole in those cities
(none of the local maps has one), so the lines are generated, never observed.

A pole stands along a street of a listed ``highway`` every ``spacing_m``, past the street's half width by ``kerb_m``
and keeping ``back_m`` free to the building behind it, on one side of the street chosen by hash, else the other. It is
left out outside the zone, near a spawn, a door, a street lamp or another prop's footprint, or too near another pole.
Two poles of one street in a row are wired when their span is short enough and crosses no building; a pole also sends
up to ``drops_per_pole`` drops to the facades within ``drop_reach_m`` that are tall enough. Everything is drawn by code
in one generated material, ``overhead_line``, so the lines add no download and one draw per combine cell. A pole
collides (a box round it); a wire never does. The wires are thin and two-sided: the Unity adapter lights the material
by light probes, as it does the awning cloth.
"""

from __future__ import annotations

import io
import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

import numpy as np
from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match
from PIL import Image
from shapely import LineString, Point, STRtree
from shapely.geometry import Polygon, shape
from shapely.geometry.base import BaseGeometry

from onward_generator.detail.facades import stable_unit
from onward_generator.generation.gltf import Node, Primitive
from onward_generator.generation.mesh import Mesh, box
from onward_generator.overgrow.cloth import two_sided
from onward_generator.overgrow.terraces import free_across, other_doors
from onward_generator.overgrow.zone import Zone

REPO_ROOT = Path(__file__).resolve().parents[3]
LINES_PATH = REPO_ROOT / "config" / "overhead_lines.json"
SCHEMA_PATH = REPO_ROOT / "config" / "overhead_lines.schema.json"
LINE_MATERIAL = "overhead_line"
POLE_PREFIX = "overgrow/power/pole/"
WIRE_PREFIX = "overgrow/power/wire/"
COLLIDER_PREFIX = "overgrow/collider/pole/"
POLE_V = 0.25
"""V of the poles' band in the ``overhead_line`` image (its upper half)."""
WIRE_V = 0.75
"""V of the wires' band (its lower half)."""
IMAGE_PX = 16
SIDES = 8
"""Faces round a pole."""
TOP_SHARE = 0.75
"""A pole's radius at its top, as a share of its foot's."""
ARM_DROP_M = 0.4
"""The crossarm's axis, under the top of the pole."""
ARM_M = 0.1
"""The crossarm's square section."""
WIRE_ABOVE_ARM_M = 0.1
"""A wire rests this high above the crossarm's axis."""
ARM_INSET_M = 0.1
"""The outer wires sit this far in from the crossarm's ends."""
WIRE_SEGMENTS = 8
DROP_BELOW_M = 0.2
"""A drop leaves its pole this far under the line's wires."""
DROP_ROOF_M = 0.4
"""A drop lands at least this far under the top of its building."""
WALL_GAP_M = 0.05
"""A drop's end stands this far out of the facade."""
DROP_TOUCH_M = 0.15
"""The last stretch of a drop, at the facade, that is not checked against buildings."""
CROSS_M = 0.05
"""A span or drop crossing a building for longer than this is not drawn."""
POLE_BOX_M = 0.15
"""Half the side of a pole's collision box."""
NEAR_POLE = 0.4
"""Two poles closer than this share of ``spacing_m`` (the end of one street and the start of the next) keep the first."""
DEFAULT_WIDTH_M = 6.0
"""A street's width when the geo scene gives none."""
POLE_TRIANGLES = 2 * SIDES + (SIDES - 2) + 12
WIRE_TRIANGLES = 2 * 2 * 2 * WIRE_SEGMENTS
HEADROOM_SHARE = 0.25
"""The share of overgrow's triangle headroom the lines may spend, as the terraces and the awnings."""


class LinesError(ValueError):
    """A ``config/overhead_lines.json`` that is not valid."""


@dataclass(frozen=True, slots=True)
class LineConfig:
    styles: frozenset[str]
    highways: frozenset[str]
    spacing_m: float
    kerb_m: float
    back_m: float
    min_axis_m: float
    pole_height_m: float
    pole_radius_m: float
    crossarm_m: float
    max_span_m: float
    sag_ratio: float
    wire_m: float
    drops_per_pole: int
    drop_reach_m: float
    drop_min_z_m: float
    drop_max_z_m: float
    clear_m: float
    max_poles: int
    pole_colour: tuple[int, int, int]
    wire_colour: tuple[int, int, int]


_FLOATS = ("spacing_m", "kerb_m", "back_m", "min_axis_m", "pole_height_m", "pole_radius_m", "crossarm_m", "max_span_m", "sag_ratio",
           "wire_m")
_DROP_FLOATS = ("drop_reach_m", "drop_min_z_m", "drop_max_z_m", "clear_m")


def parse_lines(doc: object, schema: Mapping[str, Any], where: str = "overhead_lines") -> LineConfig:
    """The checked configuration."""
    first = best_match(Draft202012Validator(schema).iter_errors(doc))
    if first is not None:
        path = "/".join(str(part) for part in first.absolute_path)
        raise LinesError(f"{where}: {path}: {first.message}" if path else f"{where}: {first.message}")
    assert isinstance(doc, dict)  # the schema requires an object  # pragma: no mutate: narrows the type only
    return LineConfig(
        styles=frozenset(doc["styles"]), highways=frozenset(doc["highways"]), drops_per_pole=int(doc["drops_per_pole"]),
        max_poles=int(doc["max_poles"]), pole_colour=_rgb(doc["pole_colour"]), wire_colour=_rgb(doc["wire_colour"]),
        **{name: float(doc[name]) for name in (*_FLOATS, *_DROP_FLOATS)},
    )


def _rgb(value: Sequence[int]) -> tuple[int, int, int]:
    return int(value[0]), int(value[1]), int(value[2])


@cache
def load_lines(path: Path = LINES_PATH) -> LineConfig:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LinesError(f"{path}: {exc}") from exc
    return parse_lines(doc, schema, str(path))


def lines_enabled(request: bool | None, style: str, config: LineConfig) -> bool:
    """``map.json`` ``overhead_lines`` when it says, else whether the map's facade style is listed."""
    return request if request is not None else style in config.styles


def lines_png(config: LineConfig) -> bytes:
    """The ``overhead_line`` image: the poles' colour over the wires'."""
    pixels = np.empty((IMAGE_PX, IMAGE_PX, 3), dtype=np.uint8)
    pixels[: IMAGE_PX // 2] = config.pole_colour
    pixels[IMAGE_PX // 2:] = config.wire_colour
    out = io.BytesIO()
    Image.fromarray(pixels).save(out, format="PNG")
    return out.getvalue()


# -------------------------------------------------------------------------------------------------- meshes


def pole_mesh(foot: np.ndarray, height: float, radius: float, crossarm: float, across: np.ndarray) -> tuple[Mesh, np.ndarray]:
    """A tapered ``SIDES``-sided pole from ``foot`` (x, y, z) up ``height``, capped, with a crossarm ``crossarm`` long
    along the unit plan vector ``across``, ``ARM_DROP_M`` under its top."""
    x, y, z = (float(v) for v in foot)
    angles = [2 * math.pi * k / SIDES for k in range(SIDES)]
    ring = [(math.cos(a), math.sin(a)) for a in angles]
    top = z + height
    vertices = [[x + radius * cx, y + radius * cy, z] for cx, cy in ring]
    vertices += [[x + radius * TOP_SHARE * cx, y + radius * TOP_SHARE * cy, top] for cx, cy in ring]
    triangles = []
    for k in range(SIDES):
        a, b = k, (k + 1) % SIDES
        triangles += [[a, b, SIDES + b], [a, SIDES + b, SIDES + a]]
    triangles += [[SIDES, SIDES + k + 1, SIDES + k + 2] for k in range(SIDES - 2)]  # the cap, a fan from the first top corner
    u = np.asarray(across, dtype=np.float64)
    t = np.array([-u[1], u[0]])
    arm_z = top - ARM_DROP_M
    base = len(vertices)
    for su, st, sz in [(-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1), (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)]:
        px, py = np.array([x, y]) + u * (su * crossarm / 2) + t * (st * ARM_M / 2)
        vertices.append([float(px), float(py), arm_z + sz * ARM_M / 2])
    for face in ([0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6],
                 [3, 0, 4], [3, 4, 7]):
        triangles.append([base + i for i in face])
    uvs = np.tile([0.5, POLE_V], (len(vertices), 1))
    return Mesh(np.array(vertices, dtype=np.float64), np.array(triangles, dtype=np.int64)), uvs


def attach_points(foot: np.ndarray, height: float, crossarm: float, across: np.ndarray) -> list[np.ndarray]:
    """Where a pole's three wires rest: both ends of the crossarm, inset, and its middle, above it."""
    z = float(foot[2]) + height - ARM_DROP_M + WIRE_ABOVE_ARM_M
    reach = crossarm / 2 - ARM_INSET_M
    return [np.array([*(np.asarray(foot[:2]) + np.asarray(across) * (side * reach)), z]) for side in (-1, 0, 1)]


def sag_points(a: np.ndarray, b: np.ndarray, sag: float, segments: int = WIRE_SEGMENTS) -> np.ndarray:
    """``segments + 1`` points of a wire from ``a`` to ``b`` hanging ``sag`` at mid-span (a parabola)."""
    t = np.linspace(0.0, 1.0, segments + 1)[:, None]
    points = a + (b - a) * t
    points[:, 2] -= 4 * sag * t[:, 0] * (1 - t[:, 0])
    return points


def wire_mesh(a: np.ndarray, b: np.ndarray, sag: float, width: float) -> tuple[Mesh, np.ndarray]:
    """A wire from ``a`` to ``b``: two crossed ribbons ``width`` wide, one flat and one upright, drawn both ways, so it
    shows from the street, from under it and from the side."""
    points = sag_points(np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64), sag)
    run = np.asarray(b[:2], dtype=np.float64) - np.asarray(a[:2], dtype=np.float64)
    length = float(np.hypot(*run))
    side = np.array([-run[1], run[0], 0.0]) / length * (width / 2) if length > 0 else np.array([width / 2, 0.0, 0.0])
    up = np.array([0.0, 0.0, width / 2])
    vertices = np.vstack([points - side, points + side, points - up, points + up])
    n = len(points)
    triangles = []
    for first, second in ((0, n), (2 * n, 3 * n)):
        for k in range(n - 1):
            triangles += [[first + k, second + k, second + k + 1], [first + k, second + k + 1, first + k + 1]]
    uvs = np.tile([0.5, WIRE_V], (len(vertices), 1))
    return two_sided(vertices, np.array(triangles), uvs)


# -------------------------------------------------------------------------------------------------- placement


@dataclass(frozen=True, slots=True)
class Street:
    feature_id: str
    line: LineString
    half_width: float


def streets_for(features: Sequence[Mapping[str, Any]], highways: frozenset[str]) -> list[Street]:
    """The centre lines of the streets a line of poles follows, longest first (then by id)."""
    found: list[Street] = []
    for feature in features:
        if feature.get("kind") != "highway" or not isinstance(feature.get("geometry"), dict):
            continue
        if str((feature.get("tags") or {}).get("highway") or "") not in highways:
            continue
        geom = shape(feature["geometry"])
        width = ((feature.get("attributes") or {}).get("width_m") or {}).get("value")
        half = float(width) / 2 if isinstance(width, (int, float)) and width > 0 else DEFAULT_WIDTH_M / 2
        for index, part in enumerate(getattr(geom, "geoms", [geom])):
            if isinstance(part, LineString) and part.length > 0:
                name = str(feature.get("id") or "") + (f"#{index}" if index else "")
                found.append(Street(name, part, half))
    return sorted(found, key=lambda street: (-street.line.length, street.feature_id))


def building_heights(features: Sequence[Mapping[str, Any]], manifest: Mapping[str, Any]) -> list[tuple[Polygon, float]]:
    """Each building footprint of the geo scene with the height ``detail`` gave it; one ``detail`` left out has none."""
    heights: dict[str, float] = {}
    for item in manifest.get("objects") or []:
        if isinstance(item, dict) and item.get("type") == "building":
            value = (item.get("height") or {}).get("height_m")
            if isinstance(value, (int, float)):
                for source in item.get("source_ids") or []:
                    heights[str(source)] = float(value)
    found: list[tuple[Polygon, float]] = []
    for feature in features:
        if feature.get("kind") != "building" or not isinstance(feature.get("geometry"), dict):
            continue
        height = heights.get(str(feature.get("id") or ""))
        if height is None:
            continue
        geom = shape(feature["geometry"])
        found.extend((part, height) for part in getattr(geom, "geoms", [geom]) if isinstance(part, Polygon))
    return found


@dataclass(frozen=True, slots=True)
class Pole:
    street: str
    order: int
    foot: np.ndarray
    across: np.ndarray


def pole_spot(point: np.ndarray, normal: np.ndarray, half_width: float, buildings: STRtree, config: LineConfig) -> np.ndarray | None:
    """Where a pole stands off ``point`` on the street's axis along ``normal``: past the half width by ``kerb_m``, or
    nearer when the building behind would leave less than ``back_m``; None nearer the axis than ``min_axis_m``."""
    want = half_width + config.kerb_m
    free = free_across(point, normal, buildings, want + config.back_m)
    reach = min(want, free - config.back_m)
    if reach < config.min_axis_m:
        return None
    return point + normal * reach


def _spot_reason(spot: np.ndarray, zone: Zone, near_spawn: Callable[[float, float], bool], blocked: Sequence[BaseGeometry],
                 doors: Sequence[tuple[float, float]], poles: Sequence[Pole], config: LineConfig) -> str | None:
    here = Point(float(spot[0]), float(spot[1]))
    if not zone.contains(float(spot[0]), float(spot[1])):
        return "outside_zone"
    if near_spawn(float(spot[0]), float(spot[1])):
        return "spawn"
    if any(math.dist((float(spot[0]), float(spot[1])), door) < config.clear_m + config.back_m for door in doors):
        return "door"
    if any(area.distance(here) < config.clear_m for area in blocked):
        return "occupied"
    if any(math.dist((float(spot[0]), float(spot[1])), (float(pole.foot[0]), float(pole.foot[1]))) < config.spacing_m * NEAR_POLE for pole in poles):
        return "near_pole"
    return None


def place_poles(streets: Sequence[Street], buildings: STRtree, zone: Zone, seed: int, *, near_spawn: Callable[[float, float], bool],
                blocked: Sequence[BaseGeometry], doors: Sequence[tuple[float, float]], config: LineConfig,
                ground_z: Callable[[float, float], float]) -> tuple[list[Pole], dict[str, int]]:
    """The poles of every street in order, and why the others were left out."""
    poles: list[Pole] = []
    skipped: dict[str, int] = {}
    for street in streets:
        first_side = 1.0 if stable_unit(seed, street.feature_id, "side") < 0.5 else -1.0  # pragma: no mutate: either side starts; the test pins both
        along = config.spacing_m / 2
        order = 0
        while along < street.line.length and len(poles) < config.max_poles:
            point = np.asarray(street.line.interpolate(along).coords[0][:2], dtype=np.float64)
            ahead = np.asarray(street.line.interpolate(min(along + 0.5, street.line.length)).coords[0][:2])
            behind = np.asarray(street.line.interpolate(max(along - 0.5, 0.0)).coords[0][:2])
            direction = (ahead - behind) / np.linalg.norm(ahead - behind)
            left = np.array([-direction[1], direction[0]])
            reason = "narrow"
            for side in (first_side, -first_side):
                spot = pole_spot(point, left * side, street.half_width, buildings, config)
                if spot is None:
                    continue
                why = _spot_reason(spot, zone, near_spawn, blocked, doors, poles, config)
                if why is None:
                    foot = np.array([spot[0], spot[1], ground_z(float(spot[0]), float(spot[1]))])
                    poles.append(Pole(street.feature_id, order, foot, left))
                    break
                reason = why
            else:
                skipped[reason] = skipped.get(reason, 0) + 1
            order += 1
            along += config.spacing_m
    return poles, dict(sorted(skipped.items()))


def crosses(segment: LineString, buildings: STRtree) -> bool:
    """Whether ``segment`` runs through a building for longer than ``CROSS_M``."""
    return any(buildings.geometries[int(index)].intersection(segment).length > CROSS_M
               for index in buildings.query(segment, predicate="intersects"))  # pragma: no mutate: a box-only candidate meets the segment in nothing, of length 0


def spans(poles: Sequence[Pole], buildings: STRtree, config: LineConfig) -> list[tuple[Pole, Pole]]:
    """The pairs of poles that are wired: one street's poles in a row, a span short enough, crossing no building."""
    found: list[tuple[Pole, Pole]] = []
    for a, b in zip(poles, poles[1:]):
        if a.street != b.street or b.order != a.order + 1:
            continue
        if math.dist(a.foot[:2], b.foot[:2]) > config.max_span_m:
            continue
        if crosses(LineString([tuple(a.foot[:2]), tuple(b.foot[:2])]), buildings):
            continue
        found.append((a, b))
    return found


def drops(pole: Pole, index: int, seed: int, heights: Sequence[tuple[Polygon, float]], tree: STRtree, buildings: STRtree,
          config: LineConfig) -> list[tuple[np.ndarray, np.ndarray]]:
    """The service drops of one pole, nearest facade first: (start on the pole, end on the facade)."""
    wanted = round(stable_unit(seed, index, "drops") * config.drops_per_pole)
    here = Point(float(pole.foot[0]), float(pole.foot[1]))
    candidates: list[tuple[float, int, np.ndarray, float]] = []
    for found in tree.query(here.buffer(config.drop_reach_m)):
        polygon, height = heights[int(found)]
        nearest = polygon.exterior.interpolate(polygon.exterior.project(here))
        gap = here.distance(nearest)
        if 0 < gap <= config.drop_reach_m:
            candidates.append((gap, int(found), np.asarray(nearest.coords[0][:2]), height))
    ends: list[tuple[np.ndarray, np.ndarray]] = []
    tops = attach_points(pole.foot, config.pole_height_m, config.crossarm_m, pole.across)
    for gap, _, wall, height in sorted(candidates, key=lambda item: (item[0], item[1])):
        if len(ends) >= wanted:
            break
        z = min(config.drop_max_z_m, height - DROP_ROOF_M)
        if z < config.drop_min_z_m:
            continue
        out = (pole.foot[:2] - wall) / gap
        end_xy = wall + out * WALL_GAP_M
        if crosses(LineString([tuple(pole.foot[:2]), tuple(wall + out * DROP_TOUCH_M)]), buildings):
            continue
        start = min(tops, key=lambda top: float(np.hypot(*(top[:2] - end_xy))))
        ends.append((start - np.array([0.0, 0.0, DROP_BELOW_M]), np.array([end_xy[0], end_xy[1], pole.foot[2] + z])))
    return ends


@dataclass(slots=True)
class LineResult:
    nodes: list[Node] = field(default_factory=list)
    colliders: list[Node] = field(default_factory=list)
    records: list[dict[str, Any]] = field(default_factory=list)
    footprints: list[BaseGeometry] = field(default_factory=list)
    triangles: int = 0
    spans: int = 0
    drops: int = 0
    skipped: dict[str, int] = field(default_factory=dict)

    def report(self, style: str, requested: bool | None) -> dict[str, Any]:
        poles = sum(1 for row in self.records if row["kind"] == "power_pole")
        return {"style": style, "requested": requested, "poles": poles, "spans": self.spans, "drops": self.drops,
                "triangles": self.triangles, "colliders": len(self.colliders), "skipped": dict(self.skipped)}


def _wire(result: LineResult, a: np.ndarray, b: np.ndarray, role: str, source: str, config: LineConfig) -> None:
    length = float(np.linalg.norm(b - a))
    sag = length * config.sag_ratio
    mesh, uvs = wire_mesh(a, b, sag, config.wire_m)
    result.nodes.append(Node(f"{WIRE_PREFIX}{len(result.nodes)}", (Primitive(mesh, LINE_MATERIAL, uvs),),
                             {"type": "overgrowth", "origin": "generated", "attribute_origin": "generated", "kind": "power_line",
                              "role": role, "solid": False}))
    lowest = float(sag_points(a, b, sag)[:, 2].min())
    middle = (a + b) / 2
    result.records.append({"kind": "power_line", "origin": "generated", "source_id": source, "role": role,
                           "position_m": [round(float(middle[0]), 3), round(float(middle[1]), 3), round(lowest, 3)],
                           "size_m": [round(length, 3), round(config.wire_m, 3), round(max(float(a[2]), float(b[2])) - lowest, 3)],
                           "solid": False})
    result.triangles += mesh.triangle_count


def place_lines(manifest: Mapping[str, Any], features: Sequence[Mapping[str, Any]], buildings: STRtree, zone: Zone, seed: int, *,
                budget: int, ground_z: Callable[[float, float], float], near_spawn: Callable[[float, float], bool],
                blocked: Sequence[BaseGeometry] = (), config: LineConfig | None = None) -> LineResult:
    """The poles, spans and drops of a wired map within ``budget`` triangles. A pole with neither a span nor a drop is
    left out (``unwired``): a real one always carries a wire. The poles go with their spans first, street by street, then
    the drops while the budget lasts."""
    cfg = config or load_lines()
    result = LineResult()
    poles, skipped = place_poles(streets_for(features, cfg.highways), buildings, zone, seed, near_spawn=near_spawn, blocked=blocked,
                                 doors=other_doors(manifest), config=cfg, ground_z=ground_z)
    wired = spans(poles, buildings, cfg)
    heights = building_heights(features, manifest)
    tree = STRtree([polygon for polygon, _ in heights]) if heights else None
    served = [drops(pole, index, seed, heights, tree, buildings, cfg) if tree is not None else [] for index, pole in enumerate(poles)]
    spanned = {id(pole) for pair in wired for pole in pair}
    by_end = {id(pair[1]): pair[0] for pair in wired}
    kept: list[Pole] = []
    kept_drops: list[list[tuple[np.ndarray, np.ndarray]]] = []
    for pole, ends in zip(poles, served, strict=True):
        if id(pole) not in spanned and not ends:
            skipped["unwired"] = skipped.get("unwired", 0) + 1
            continue
        previous = by_end.get(id(pole))
        linked = previous is not None and any(previous is other for other in kept)
        cost = POLE_TRIANGLES + (3 * WIRE_TRIANGLES if linked else 0)
        if result.triangles + cost > budget:
            skipped["budget"] = skipped.get("budget", 0) + 1
            continue
        _pole(result, pole, cfg)
        kept.append(pole)
        kept_drops.append(ends)
        if linked:
            assert previous is not None  # pragma: no mutate: linked says so; narrows the type only
            for a, b in zip(attach_points(previous.foot, cfg.pole_height_m, cfg.crossarm_m, previous.across),
                            attach_points(pole.foot, cfg.pole_height_m, cfg.crossarm_m, pole.across), strict=True):
                _wire(result, a, b, "span", pole.street, cfg)
            result.spans += 1
    for pole, ends in zip(kept, kept_drops, strict=True):
        for start, end in ends:
            if result.triangles + WIRE_TRIANGLES > budget:
                skipped["drop_budget"] = skipped.get("drop_budget", 0) + 1
                continue
            _wire(result, start, end, "drop", pole.street, cfg)
            result.drops += 1
    result.skipped = dict(sorted(skipped.items()))
    return result


def _pole(result: LineResult, pole: Pole, config: LineConfig) -> None:
    mesh, uvs = pole_mesh(pole.foot, config.pole_height_m, config.pole_radius_m, config.crossarm_m, pole.across)
    number = sum(1 for row in result.records if row["kind"] == "power_pole")
    result.nodes.append(Node(f"{POLE_PREFIX}{number}", (Primitive(mesh, LINE_MATERIAL, uvs),),
                             {"type": "overgrowth", "origin": "generated", "attribute_origin": "generated", "kind": "power_pole",
                              "role": "pole", "solid": True}))
    x, y, z = (float(v) for v in pole.foot)
    result.colliders.append(Node(f"{COLLIDER_PREFIX}{number}",
                                 (Primitive(box(x - POLE_BOX_M, x + POLE_BOX_M, y - POLE_BOX_M, y + POLE_BOX_M, z, z + config.pole_height_m),
                                            "collider"),),
                                 {"type": "collision", "origin": "generated", "role": "pole"}))
    result.records.append({"kind": "power_pole", "origin": "generated", "source_id": pole.street, "role": "pole",
                           "position_m": [round(x, 3), round(y, 3), round(z, 3)],
                           "size_m": [round(2 * POLE_BOX_M, 3), round(2 * POLE_BOX_M, 3), round(config.pole_height_m, 3)], "solid": True})
    result.footprints.append(Point(x, y).buffer(config.clear_m))
    result.triangles += mesh.triangle_count


__all__ = ["COLLIDER_PREFIX", "HEADROOM_SHARE", "LINES_PATH", "LINE_MATERIAL", "LineConfig", "LineResult", "LinesError", "Pole", "Street",
           "attach_points", "building_heights", "crosses", "drops", "lines_enabled", "lines_png", "load_lines", "parse_lines",
           "place_lines", "place_poles", "pole_mesh", "pole_spot", "sag_points", "spans", "streets_for", "wire_mesh"]
