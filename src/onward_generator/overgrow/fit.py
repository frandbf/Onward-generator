"""Scale a prop to the size the street can hold, then check its footprint.

The library GLB keeps the file's own units. Placement copies the mesh and
scales that copy. A car-shaped wreck is brought into the car band. A wide
shape such as a helicopter is brought to the largest size that still leaves
a side passage. A deep fire escape is pulled back to the wall depth. Moss,
grass and trees are not measured here. ``check_scene`` also refuses a
wall-mounted prop off its wall, a prop that floats above or sinks into the
ground, and two solid props standing inside each other.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from shapely import Point, STRtree
from shapely.geometry import LineString, Polygon, shape
from shapely.geometry.polygon import orient

from onward_generator.generation.gltf import nodes_from_glb
from onward_generator.generation.mesh import Mesh, merge, transform
from onward_generator.io import atomic_write_json
from onward_generator.overgrow.ambient import is_solid
from onward_generator.overgrow.constants import LANE_M
from onward_generator.overgrow.ground import RoadFloor, road_floor

WRECK_LENGTH_M = (2.8, 6.5)
WRECK_WIDTH_M = (1.4, 2.4)
WRECK_HEIGHT_M = (1.0, 2.4)
WRECK_TARGET_LENGTH_M = 4.5
# Pivot 0.9 m off the centreline: width 4 m leaves at least 1.2 m on the open side.
PASSAGE_WIDTH_M = 4.0
FIRE_ESCAPE_DEPTH_M = 1.6
# The library's plants are 1.3 to 2.1 m across; a scanned patch of grass came in at 14 x 13 m.
PLANT_MAX_M = 2.5
FACADE_GAP_M = 0.15
PASSAGE_M = 1.2
BUILDING_OVERLAP_M2 = 0.25
# Two solid props whose footprints share more than this stand inside each other.
PROP_OVERLAP_M2 = 0.25
# A wall-mounted prop farther than FACADE_GAP_M from every wall within this reach floats in the street.
WALL_REACH_M = 2.0
# A prop on the ground starts within this of the sidewalk (z = 0) or the carriageway under its centre.
GROUND_TOLERANCE_M = 0.05
# ``signs place`` names each street plate node ``street_sign/<material>``.
STREET_SIGN_NODE = "street_sign/"

SKIP_HIGHWAYS = frozenset({
    "footway", "path", "cycleway", "steps", "corridor", "elevator",
    "platform", "raceway", "bridleway", "proposed", "construction",
    "service", "busway", "bus_guideway",
})


class FitError(ValueError):
    """The dressed scene cannot be checked."""


def measure(mesh: Mesh) -> tuple[float, float, float]:
    """Local spans in metres: length (X), width (Y), height (Z)."""
    if mesh.vertex_count == 0:
        return 0.0, 0.0, 0.0
    span = mesh.vertices.max(axis=0) - mesh.vertices.min(axis=0)
    return float(span[0]), float(span[1]), float(span[2])


def size_reason(role: str, length_m: float, width_m: float, height_m: float) -> str | None:
    """``None`` when this role has no size band or the mesh is inside it.

    Wreck width may reach ``PASSAGE_WIDTH_M``. The car band is narrower;
    ``fit_scale`` uses it when the proportions allow.
    """
    if role == "wreck":
        length_ok = WRECK_LENGTH_M[0] <= length_m <= WRECK_LENGTH_M[1]
        width_ok = WRECK_WIDTH_M[0] <= width_m <= PASSAGE_WIDTH_M
        height_ok = WRECK_HEIGHT_M[0] <= height_m <= WRECK_HEIGHT_M[1]
        if not (length_ok and width_ok and height_ok):
            return "wrong_size"
    if role == "fire_escape" and width_m > FIRE_ESCAPE_DEPTH_M:
        return "too_deep"
    if role == "plant" and max(length_m, width_m) > PLANT_MAX_M:
        return "wrong_size"
    return None


def fit_scale(role: str, length_m: float, width_m: float, height_m: float) -> float:
    """Uniform scale that gives this role a street size. ``1`` when it already fits."""
    if role == "fire_escape":
        if width_m <= 0.0 or width_m <= FIRE_ESCAPE_DEPTH_M:
            return 1.0
        return FIRE_ESCAPE_DEPTH_M / width_m
    if role == "plant":
        # Never above 1: a plant within the band keeps its size.
        return PLANT_MAX_M / max(length_m, width_m, PLANT_MAX_M)
    if role != "wreck" or size_reason(role, length_m, width_m, height_m) is None:
        return 1.0
    if min(length_m, width_m, height_m) <= 0.0:
        return 1.0
    car = _band_interval(length_m, width_m, height_m, WRECK_LENGTH_M, WRECK_WIDTH_M, WRECK_HEIGHT_M)
    if car is not None:
        return _clamp(WRECK_TARGET_LENGTH_M / length_m, car)
    street = _band_interval(
        length_m, width_m, height_m,
        WRECK_LENGTH_M, (WRECK_WIDTH_M[0], PASSAGE_WIDTH_M), WRECK_HEIGHT_M,
    )
    if street is not None:
        return street[1]
    return min(
        WRECK_LENGTH_M[1] / length_m,
        PASSAGE_WIDTH_M / width_m,
        WRECK_HEIGHT_M[1] / height_m,
    )


def apply_fit_scale(mesh: Mesh, role: str) -> tuple[Mesh, float]:
    """Return a scaled copy and the factor. The caller's mesh is left as it was."""
    length_m, width_m, height_m = measure(mesh)
    scale = fit_scale(role, length_m, width_m, height_m)
    if abs(scale - 1.0) <= 1e-9:
        return mesh, 1.0
    matrix = np.diag([scale, scale, scale, 1.0])
    return transform(mesh, matrix), scale


def object_reason(
    *,
    role: str,
    mesh: Mesh,
    matrix: np.ndarray,
    buildings: STRtree,
    lanes: list[LineString],
) -> str | None:
    """Why this posed mesh cannot stay. Tries nothing else; the caller does."""
    length_m, width_m, height_m = measure(mesh)
    reason = size_reason(role, length_m, width_m, height_m)
    if reason:
        return reason
    polygon = _footprint(mesh, matrix)
    if polygon.is_empty:
        return None
    return _location_reason(role, polygon, buildings, lanes)


def check_scene(base: str | Path, geo_scene: str | Path) -> tuple[int, list[str]]:
    """Measure every placed prop, wreck and street plate. Moss, grass, vines and trees stay out."""
    directory = Path(base)
    visual = directory / "scene_visual.glb"
    if not visual.is_file():
        raise FitError(f"{directory}: needs scene_visual.glb")
    nodes, _materials = nodes_from_glb(visual.read_bytes())
    buildings, lanes = grounds_from_scene(geo_scene)
    floor = road_floor(nodes)
    problems: list[str] = []
    checked = 0
    solid: list[tuple[str, Polygon]] = []
    for node in nodes:
        if node.name.startswith(STREET_SIGN_NODE):
            checked += 1
            problems.extend(_plate_reasons(node, buildings))
            continue
        role = _role(node)
        if role is None:
            continue
        # nodes_from_glb gives every node that reuses a mesh that mesh's primitives.
        mesh = merge(p.mesh for p in node.primitives)
        if not mesh.triangle_count:
            problems.append(f"{node.name} missing_mesh")
            checked += 1
            continue
        matrix = node.matrix if node.matrix is not None else np.eye(4)
        reason = object_reason(role=role, mesh=mesh, matrix=matrix, buildings=buildings, lanes=lanes)
        checked += 1
        footprint = _footprint(mesh, matrix)
        # Every wall-mounted prop is in the facade category, whether the rules or an AI plan placed it.
        if reason is None and node.extras.get("kind") == "facade":
            reason = _wall_reason(footprint, buildings)
        elif reason is None:
            reason = _ground_reason(transform(mesh, matrix), floor)
        if reason:
            length_m, width_m, height_m = measure(mesh)
            problems.append(f"{node.name} {reason} {length_m:.2f}x{width_m:.2f}x{height_m:.2f}")
        # A wreck's role is solid on its own; a prop may be solid by its category (``kind``).
        if is_solid(role, str(node.extras.get("kind"))):
            solid.append((node.name, footprint))
    problems.extend(_overlaps(solid))
    return checked, problems


def _wall_reason(footprint: Polygon, buildings: STRtree) -> str | None:
    """``off_wall`` when a wall-mounted prop is more than ``FACADE_GAP_M`` from every wall."""
    near = [buildings.geometries[int(i)] for i in buildings.query(footprint.buffer(WALL_REACH_M))]
    walls = [b for b in near if b.geom_type == "Polygon"]
    if not walls or min(float(b.boundary.distance(footprint)) for b in walls) > FACADE_GAP_M:
        return "off_wall"
    return None


def _plate_reasons(node, buildings: STRtree) -> list[str]:
    """``inside_building`` when the centre of a street plate falls inside a footprint."""
    posed = merge(p.mesh for p in node.primitives)
    if node.matrix is not None:
        posed = transform(posed, node.matrix)
    low, high = posed.vertices.min(axis=0), posed.vertices.max(axis=0)
    centre = Point(float(low[0] + high[0]) / 2, float(low[1] + high[1]) / 2)
    if buildings.query(centre, predicate="within").size:
        return [f"{node.name} inside_building"]
    return []


def _ground_reason(posed: Mesh, floor: RoadFloor) -> str | None:
    """``floating`` or ``sunk`` when a prop's lowest vertex misses the ground under its centre."""
    low = posed.vertices.min(axis=0)
    high = posed.vertices.max(axis=0)
    gap = float(low[2]) - floor.z(float(low[0] + high[0]) / 2, float(low[1] + high[1]) / 2)
    if gap > GROUND_TOLERANCE_M:
        return f"floating_{gap:.2f}m"
    if gap < -GROUND_TOLERANCE_M:
        return f"sunk_{-gap:.2f}m"
    return None


def _overlaps(solid: list[tuple[str, Polygon]]) -> list[str]:
    """One line per pair of solid props standing inside each other."""
    tree = STRtree([footprint for _name, footprint in solid])
    found = []
    for index, (name, footprint) in enumerate(solid):
        # Box hits that do not meet share no area, so the box query is enough.
        for other in sorted(int(i) for i in tree.query(footprint)):
            if other <= index:
                continue
            shared = float(footprint.intersection(solid[other][1]).area)
            if shared > PROP_OVERLAP_M2:
                found.append(f"{name} overlaps {solid[other][0]} {shared:.2f}m2")
    return found


def write_report(path: str | Path, checked: int) -> None:
    atomic_write_json(path, {
        "schema": "onward_generator.overgrow_fit",
        "checked": checked,
        "rejected": [],
    })


def grounds_from_scene(path: str | Path) -> tuple[STRtree, list[LineString]]:
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    polygons: list[Polygon] = []
    lanes: list[LineString] = []
    for feature in document.get("features") or []:
        if not isinstance(feature, dict):
            continue
        raw = feature.get("geometry") or {}
        if not raw.get("type"):
            continue
        geom = shape({"type": raw.get("type"), "coordinates": raw.get("coordinates")})
        if geom.is_empty:
            continue
        kind = str(feature.get("kind") or "")
        if kind == "building" and geom.geom_type in {"Polygon", "MultiPolygon"}:
            parts = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
            polygons.extend(orient(part, sign=1.0) for part in parts if isinstance(part, Polygon) and not part.is_empty)
        if kind != "highway" or geom.length < 8.0:
            continue
        tags = feature.get("tags") or {}
        if str(tags.get("highway") or "") in SKIP_HIGHWAYS:
            continue
        if geom.geom_type == "LineString":
            lanes.append(geom)
        elif geom.geom_type == "MultiLineString":
            lanes.extend(part for part in geom.geoms if part.length >= 8.0)
    return STRtree(polygons or [Point(1.0e9, 1.0e9)]), lanes


def _footprint(mesh: Mesh, matrix: np.ndarray) -> Polygon:
    if mesh.vertex_count == 0:
        return Polygon()
    low = mesh.vertices.min(axis=0)
    high = mesh.vertices.max(axis=0)
    corners = np.array([
        [low[0], low[1], 0.0, 1.0],
        [high[0], low[1], 0.0, 1.0],
        [high[0], high[1], 0.0, 1.0],
        [low[0], high[1], 0.0, 1.0],
    ], dtype=np.float64)
    world = (matrix @ corners.T).T[:, :2]
    polygon = Polygon(world)
    if not polygon.is_valid:
        polygon = polygon.buffer(0)
    return polygon


def _location_reason(role: str, polygon: Polygon, buildings: STRtree, lanes: list[LineString]) -> str | None:
    structures = [geom for geom in buildings.geometries if getattr(geom, "geom_type", "") == "Polygon"]
    if structures and _overlaps_building(polygon, buildings):
        return "inside_building"
    if _blocks_passage(polygon, lanes):
        return "blocks_passage"
    if role == "fire_escape" and structures:
        gap = min(float(building.boundary.distance(polygon)) for building in structures)
        if gap > FACADE_GAP_M:
            return "off_wall"
    return None


def _overlaps_building(polygon: Polygon, buildings: STRtree) -> bool:
    hits = buildings.query(polygon, predicate="intersects")
    for index in hits:
        building = buildings.geometries[int(index)]
        if getattr(building, "geom_type", "") != "Polygon":
            continue
        if polygon.intersection(building).area > BUILDING_OVERLAP_M2:
            return True
    return False


def _blocks_passage(polygon: Polygon, lanes: list[LineString]) -> bool:
    if not lanes or polygon.is_empty:
        return False
    centre = polygon.centroid
    line = min(lanes, key=lambda item: item.distance(centre))
    distance = float(line.project(centre))
    point = line.interpolate(distance)
    ahead = line.interpolate(min(line.length, distance + 2.0))
    behind = line.interpolate(max(0.0, distance - 2.0))
    tangent = np.array([ahead.x - behind.x, ahead.y - behind.y], dtype=np.float64)
    length = float(np.linalg.norm(tangent))
    if length < 1e-6:
        return False
    normal = np.array([-tangent[1], tangent[0]]) / length
    offsets = (np.asarray(polygon.exterior.coords)[:, :2] - np.array([point.x, point.y])) @ normal
    near, far = float(offsets.min()), float(offsets.max())
    cover_lo = max(near, -LANE_M)
    cover_hi = min(far, LANE_M)
    if cover_hi <= cover_lo:
        return False
    free_neg = cover_lo - (-LANE_M)
    free_pos = LANE_M - cover_hi
    return free_neg < PASSAGE_M and free_pos < PASSAGE_M


def _band_interval(
    length_m: float,
    width_m: float,
    height_m: float,
    length_band: tuple[float, float],
    width_band: tuple[float, float],
    height_band: tuple[float, float],
) -> tuple[float, float] | None:
    lo = 0.0
    hi = float("inf")
    for dim, band in ((length_m, length_band), (width_m, width_band), (height_m, height_band)):
        if dim <= 0.0:
            return None
        lo = max(lo, band[0] / dim)
        hi = min(hi, band[1] / dim)
    if lo > hi + 1e-9:
        return None
    return lo, hi


def _clamp(target: float, band: tuple[float, float]) -> float:
    return min(band[1], max(band[0], target))


def _role(node) -> str | None:
    name = node.name
    if name.startswith("overgrow/wreck/"):
        return "wreck"
    if not name.startswith("overgrow/prop/"):
        return None
    role = node.extras.get("role")
    return str(role) if role else None
