"""Props pinned to a place: a library model the map must carry, near a given WGS84 point (14 November 2026).

A map that tells a story needs some props where the story put them: the two crashed Black Hawks of Mogadishu, a
monument, a checkpoint. ``maps/<id>/config/pinned_props.json`` lists them::

    {"props": [{"asset_id": "crashed_helicopter", "latitude": 2.047458, "longitude": 45.326639, "yaw_deg": 30,
                "search_m": 30, "label": "Super Six-Four"}]}

Each one keeps the size its file gives it (the placement rules would fit a wreck to a car), stands at the free spot
nearest its point, within ``search_m`` (default ``SEARCH_DEFAULT_M``) and inside the zone, whose plan stays
``BUILDING_GAP_M`` clear of every footprint, trying its yaw and then the three quarter turns. It collides along the
plan its triangles cover (``fit.collider_mesh`` with the fence rule), so the gaps between the pieces of a wreck stay
open, and the other props and wrecks keep out of that plan. A prop that is not in the library, or that finds no spot,
stops the stage: a pinned prop is not optional.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import numpy as np
from shapely import STRtree
from shapely.affinity import affine_transform
from shapely.geometry import MultiPoint, Point, Polygon

from onward_generator.detail.modules import yaw_matrix
from onward_generator.generation.gltf import Node, Primitive
from onward_generator.generation.mesh import Mesh
from onward_generator.geo.projection import LocalFrame
from onward_generator.overgrow.fit import collider_mesh
from onward_generator.overgrow.library import Prop
from onward_generator.overgrow.zone import Zone

SEARCH_DEFAULT_M = 30.0
SEARCH_MAX_M = 200.0
SEARCH_STEP_M = 1.0
SINK_MAX_M = 10.0
BUILDING_GAP_M = 0.3


class PinnedError(ValueError):
    """``pinned_props.json`` cannot be read, or a pinned prop cannot be placed."""


@dataclass(frozen=True, slots=True)
class Pin:
    asset_id: str
    latitude: float
    longitude: float
    yaw_deg: float = 0.0
    search_m: float = SEARCH_DEFAULT_M
    label: str = ""
    roll_deg: float = 0.0
    """A turn about the model's length (+X) before the yaw: 80 lays a helicopter on its side (14 November 2026)."""
    pitch_deg: float = 0.0
    """A turn about the model's width (+Y) before the yaw: a nose down in the ground."""
    sink_m: float = 0.0
    """How far the posed model goes below the ground: a helicopter on its side rests on a rotor tip, its body metres up,
    until the blades are buried (14 November 2026)."""


@dataclass
class PinnedResult:
    nodes: list[Node] = field(default_factory=list)
    colliders: list[Node] = field(default_factory=list)
    images: dict[str, bytes] = field(default_factory=dict)
    footprints: list[Polygon] = field(default_factory=list)
    triangles: int = 0
    records: list[dict[str, Any]] = field(default_factory=list)


def _number(row: Mapping[str, Any], key: str, where: str, default: float | None = None) -> float:
    value = row.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PinnedError(f"{where}: {key} must be a number, got {value!r}")
    return float(value)


def read_pins(path: str | Path) -> list[Pin]:
    """The pinned props of ``path``, in its order."""
    source = Path(path)
    try:
        document = json.loads(source.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise PinnedError(f"{source}: cannot be read: {exc}") from exc
    rows = document.get("props") if isinstance(document, dict) else None
    if not isinstance(rows, list) or not rows:
        raise PinnedError(f'{source}: needs {{"props": [...]}} with at least one prop')
    pins: list[Pin] = []
    for index, row in enumerate(rows):
        where = f"{source} props[{index}]"
        if not isinstance(row, dict) or not isinstance(row.get("asset_id"), str) or not row["asset_id"]:
            raise PinnedError(f"{where}: needs an asset_id")
        latitude, longitude = _number(row, "latitude", where), _number(row, "longitude", where)
        if not (-90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0):
            raise PinnedError(f"{where}: latitude {latitude} / longitude {longitude} is not a WGS84 point")
        search = _number(row, "search_m", where, SEARCH_DEFAULT_M)
        if not 0.0 <= search <= SEARCH_MAX_M:
            raise PinnedError(f"{where}: search_m must lie between 0 and {SEARCH_MAX_M:g}, got {search:g}")
        roll, pitch = _number(row, "roll_deg", where, 0.0), _number(row, "pitch_deg", where, 0.0)
        if not (-180.0 <= roll <= 180.0 and -90.0 <= pitch <= 90.0):
            raise PinnedError(f"{where}: roll_deg must lie between -180 and 180 and pitch_deg between -90 and 90, got {roll:g} / {pitch:g}")
        sink = _number(row, "sink_m", where, 0.0)
        if not 0.0 <= sink <= SINK_MAX_M:
            raise PinnedError(f"{where}: sink_m must lie between 0 and {SINK_MAX_M:g}, got {sink:g}")
        pins.append(Pin(row["asset_id"], latitude, longitude, _number(row, "yaw_deg", where, 0.0), search, str(row.get("label") or ""),
                        roll, pitch, sink))
    return pins


def frame_of(geo: Mapping[str, Any]) -> LocalFrame:
    """The local frame of a geo scene (``frame.origin_wgs84`` and ``frame.crs``)."""
    frame = geo.get("frame") or {}
    origin, crs = frame.get("origin_wgs84"), frame.get("crs")
    if not isinstance(origin, list) or len(origin) != 2 or not isinstance(crs, str):
        raise PinnedError("the geo scene names no frame (origin_wgs84 and crs): pinned props need it")
    return LocalFrame.create(float(origin[0]), float(origin[1]), crs)


def plan(mesh: Mesh) -> Polygon:
    """The convex plan of a mesh in its own frame."""
    return MultiPoint([(float(x), float(y)) for x, y in mesh.vertices[:, :2]]).convex_hull


def posed(prop: Prop, roll_deg: float, pitch_deg: float) -> Prop:
    """``prop`` turned by ``roll_deg`` about +X, then ``pitch_deg`` about +Y, resting on z = 0 again; itself when both are 0."""
    if roll_deg == 0.0 and pitch_deg == 0.0:
        return prop
    r, p = math.radians(roll_deg), math.radians(pitch_deg)
    roll = np.array([[1.0, 0.0, 0.0], [0.0, math.cos(r), -math.sin(r)], [0.0, math.sin(r), math.cos(r)]])
    pitch = np.array([[math.cos(p), 0.0, math.sin(p)], [0.0, 1.0, 0.0], [-math.sin(p), 0.0, math.cos(p)]])
    vertices = prop.mesh.vertices @ (pitch @ roll).T
    vertices[:, 2] -= vertices[:, 2].min()
    return replace(prop, mesh=Mesh(vertices, prop.mesh.triangles))


def _offsets(search_m: float) -> list[tuple[float, float]]:
    """Points within ``search_m`` of the target, nearest first, on a ``SEARCH_STEP_M`` grid."""
    reach = search_m / SEARCH_STEP_M
    steps = int(reach)
    cells: list[tuple[float, float]] = []
    for i in range(-steps, steps + 1):
        half = int(math.sqrt(reach * reach - i * i))
        cells.extend((i * SEARCH_STEP_M, j * SEARCH_STEP_M) for j in range(-half, half + 1))
    return sorted(cells, key=lambda p: (math.hypot(*p), p))


def place_pins(pins: Sequence[Pin], props: Mapping[str, Prop], frame: LocalFrame, buildings: STRtree, zone: Zone) -> PinnedResult:
    """Each pin at the free spot nearest its point, at its file's size; ``PinnedError`` when one cannot stand."""
    result = PinnedResult()
    taken: list[Polygon] = []
    for number, pin in enumerate(pins):
        found = props.get(pin.asset_id)
        if found is None:
            raise PinnedError(f"pinned prop {pin.asset_id} ({pin.label or 'no label'}) is not in the library")
        prop = posed(found, pin.roll_deg, pin.pitch_deg)
        target = frame.to_local(Point(pin.longitude, pin.latitude))
        local = plan(prop.mesh)
        spot = None
        for dx, dy in _offsets(pin.search_m):
            x, y = target.x + dx, target.y + dy
            if not zone.contains(x, y):
                continue
            for turn in (0.0, 90.0, 180.0, 270.0):
                yaw = math.radians(pin.yaw_deg + turn)
                c, s = math.cos(yaw), math.sin(yaw)
                footprint = affine_transform(local, [c, -s, s, c, x, y])
                grown = footprint.buffer(BUILDING_GAP_M)
                if len(buildings.query(grown, predicate="intersects")) or any(grown.intersects(t) for t in taken):
                    continue
                spot = (x, y, yaw, footprint)
                break
            if spot is not None:
                break
        if spot is None:
            raise PinnedError(f"pinned prop {pin.asset_id} ({pin.label or 'no label'}) finds no free spot within "
                              f"{pin.search_m:g} m of {pin.latitude}, {pin.longitude}: raise search_m or move the point")
        x, y, yaw, footprint = spot
        matrix = yaw_matrix(x, y, -pin.sink_m, yaw)
        extras = {"type": "overgrowth", "origin": "pinned", "kind": prop.spec.category, "role": prop.spec.role,
                  "asset_id": prop.spec.id, "label": pin.label}
        # Pins of one model share its glTF mesh only in the same pose: with one key for all, every pin drew the
        # first pin's turned mesh (14 November 2026).
        pose = f":{pin.roll_deg:g}:{pin.pitch_deg:g}" if pin.roll_deg or pin.pitch_deg else ""
        result.nodes.append(Node(f"overgrow/pinned/{prop.spec.id}/{number}", prop.primitives(), extras, matrix, f"pinned:{prop.spec.id}{pose}"))
        result.colliders.append(Node(f"overgrow/collider/pinned/{prop.spec.id}/{number}", (Primitive(collider_mesh(prop.mesh, "fence"), "collider"),),
                                     {"type": "collision", "origin": "pinned", "asset_id": prop.spec.id}, matrix))
        result.images.update(prop.images())
        result.footprints.append(footprint)
        result.triangles += prop.triangles
        taken.append(footprint)
        result.records.append({
            "asset_id": prop.spec.id, "label": pin.label, "target_m": [round(target.x, 3), round(target.y, 3)],
            "position_m": [round(x, 3), round(y, 3)], "offset_m": round(math.hypot(x - target.x, y - target.y), 3),
            "yaw_deg": round(math.degrees(yaw) % 360.0, 2), "triangles": prop.triangles,
            **({"roll_deg": pin.roll_deg, "pitch_deg": pin.pitch_deg} if pin.roll_deg or pin.pitch_deg else {}),
            **({"sink_m": pin.sink_m} if pin.sink_m else {}),
        })
    return result


def keep_out_circles(footprints: Sequence[Polygon]) -> list[tuple[float, float, float]]:
    """A circle round each footprint, for the props and wrecks placed after the pins."""
    circles = []
    for footprint in footprints:
        centre = footprint.centroid
        radius = max(float(np.hypot(px - centre.x, py - centre.y)) for px, py in footprint.exterior.coords)
        circles.append((round(centre.x, 3), round(centre.y, 3), round(radius, 3)))
    return circles


__all__ = ["BUILDING_GAP_M", "Pin", "PinnedError", "PinnedResult", "SEARCH_DEFAULT_M", "SEARCH_MAX_M", "SEARCH_STEP_M", "SINK_MAX_M",
           "frame_of", "keep_out_circles", "place_pins", "plan", "posed", "read_pins"]
