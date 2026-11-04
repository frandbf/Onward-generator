"""Hang one OSM street-name plate on the wall that faces that street, and draw the shop signs.

Names come only from the highway ``name`` tag. Unnamed ways get no plate.
The plate is visual: it is not added to the collision mesh. Shop signs and
vinyls come from ``facade.shops`` in the detail manifest (``signs/shop.py``).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from shapely import LineString, Point, Polygon, STRtree, box, get_parts
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient

from onward_generator import __version__
from onward_generator.config.loader import MAX_SCENE_BYTES, ConfigLoadError, read_json_document
from onward_generator.dress.atlas import encode_png_rgba
from onward_generator.dress.geometry import compare_fingerprints, file_sha256, geometry_fingerprint
from onward_generator.generation.config import Material
from onward_generator.generation.gltf import Node, Primitive, build_glb, images_by_material, nodes_from_glb
from onward_generator.generation.mesh import Mesh, quad
from onward_generator.geo.attributes import parse_metres
from onward_generator.io import atomic_write_bytes, atomic_write_json, prepare_outputs
from onward_generator.signs.plaque import PLATE_H, PLATE_W, plaque_rgba
from onward_generator.signs.shop import ATLAS_PX, PAD_PX, pack, page_height, shop_signs
from onward_generator.tiers import core_of

BUILDING_NODE = "building/"  # visual node name prefix; the rest is the building id
GEOGRAPHIC = frozenset({"building", "surface", "curb", "landmark_part"})
SKIP_HIGHWAYS = frozenset({
    "footway", "path", "cycleway", "steps", "corridor", "elevator",
    "platform", "raceway", "bridleway", "proposed", "construction",
    "service", "busway", "bus_guideway",
})
PLATE_WIDTH_M = 1.4
PLATE_HEIGHT_M = 0.36
PLATE_THICK_M = 0.03
MOUNT_Z_M = 2.55
WALL_GAP_M = 0.06
MAX_JUNCTION_M = 80.0
CLUSTER_M = 10.0
MIN_EDGE_M = 1.6
MIN_WALL_M = 3.4
# A plate's wall must have open ground this far in front of it, not another building.
FRONT_CLEAR_M = 0.5
MAX_SIGNS = 64
EDGE_COLOR = (0.07, 0.14, 0.28, 1.0)
# Every plate is drawn on a shared atlas page, one material per page: a draw per page and cell,
# not one per street name. As wide as the shop sign pages; MAX_SIGNS plates fit on two pages.
SIGN_ATLAS_PX = ATLAS_PX
SIGN_ATLAS_MATERIAL = "street_sign_atlas_{}"
OUTPUT_FILES = (
    "scene_visual.glb",
    "scene_collision.glb",
    "scene_detail.json",
    "detail_report.json",
    "signs_report.json",
)


class SignsError(ValueError):
    """Street signs cannot be placed on this scene."""


@dataclass(frozen=True, slots=True)
class _Street:
    name: str
    line: LineString
    feature_id: str


@dataclass(frozen=True, slots=True)
class _Wall:
    feature_id: str
    a: np.ndarray
    b: np.ndarray
    outward: np.ndarray
    height_m: float | None


@dataclass(frozen=True, slots=True)
class Sign:
    name: str
    material: str
    position: np.ndarray
    along: np.ndarray
    outward: np.ndarray
    feature_id: str
    name_origin: str = "osm"


def plate_atlas(names: Sequence[str]) -> tuple[dict[str, np.ndarray], dict[str, tuple[str, np.ndarray]]]:
    """Atlas pages of the plates and, per name, ``(page material, the face's four UVs)``.

    Each plate keeps its 512 x 128 px and gets ``PAD_PX`` of its own edge colour around it, so a
    mipmap does not bleed a neighbour's blue or letters into it.
    """
    spots = pack([(PLATE_W, PLATE_H)] * len(names))
    lowest = {page: max(y for p, _, y in spots if p == page) for page, _, _ in spots}
    pages = {SIGN_ATLAS_MATERIAL.format(page): np.zeros((page_height(y + PLATE_H + PAD_PX), SIGN_ATLAS_PX, 4), dtype=np.uint8)
             for page, y in lowest.items()}
    slots: dict[str, tuple[str, np.ndarray]] = {}
    for name, (page, x, y) in zip(names, spots, strict=True):  # pragma: no mutate: pack returns one spot per size, so strict changes nothing a caller sees
        material = SIGN_ATLAS_MATERIAL.format(page)
        pixels = pages[material]
        pixels[y - PAD_PX:y + PLATE_H + PAD_PX, x - PAD_PX:x + PLATE_W + PAD_PX] = np.pad(
            plaque_rgba(name), ((PAD_PX, PAD_PX), (PAD_PX, PAD_PX), (0, 0)), mode="edge")
        side_h = pixels.shape[0]
        u0, u1 = x / SIGN_ATLAS_PX, (x + PLATE_W) / SIGN_ATLAS_PX
        v1, v0 = 1 - y / side_h, 1 - (y + PLATE_H) / side_h
        slots[name] = (material, np.array([[u0, v0], [u1, v0], [u1, v1], [u0, v1]]))
    return pages, slots


def material_id(name: str) -> str:
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:10]
    return f"street_sign_{digest}"


def place_signs(
    base_dir: str | Path,
    geo_scene: str | Path,
    out_dir: str | Path,
    *,
    overwrite: bool = False,
    max_signs: int = MAX_SIGNS,
) -> dict[str, Any]:
    base = Path(base_dir).expanduser().resolve()
    out = Path(out_dir).expanduser().resolve()
    if not (base / "scene_visual.glb").is_file() or not (base / "scene_detail.json").is_file():
        raise SignsError(f"{base}: needs scene_visual.glb and scene_detail.json")
    if out == base:
        raise SignsError("output must be a new directory")
    if max_signs < 1:
        raise SignsError("max_signs must be at least 1")

    try:
        manifest, _ = read_json_document(base / "scene_detail.json", max_bytes=MAX_SCENE_BYTES)
        geo, _ = read_json_document(geo_scene, max_bytes=MAX_SCENE_BYTES)
    except ConfigLoadError as exc:
        raise SignsError(str(exc)) from exc

    streets = _streets(geo.get("features") or [])
    # No street plate outside the playable core (ADR-017): plates are for the player, and each cell holding one is a draw.
    # The walls come from the scene ``detail`` built from (the Catastro split where it ran), so they are kept by position.
    core = core_of(manifest)
    walls = [wall for wall in _walls(geo.get("features") or []) if core is None or core.covers(Point(*((wall.a + wall.b) / 2)))]
    signs = _choose(streets, walls, max_signs, STRtree(_footprints(geo.get("features") or [])))
    visual_bytes = (base / "scene_visual.glb").read_bytes()
    nodes, materials = nodes_from_glb(visual_bytes)
    images = images_by_material(visual_bytes)
    before = _geographic(manifest)

    pages, slots = plate_atlas(sorted({sign.name for sign in signs}))
    sign_nodes: list[Node] = []
    for sign in signs:
        front, back, sides = _plate_meshes(sign)
        page, uv = slots[sign.name]
        sign_nodes.append(Node(
            f"street_sign/{sign.material}",
            (
                Primitive(front, page, uv),
                Primitive(back, page, uv),
                Primitive(sides, "street_sign_edge", _edge_uvs(sides)),
            ),
            {"name": sign.name, "name_origin": "osm", "wall": sign.feature_id},
        ))
    for page, pixels in pages.items():
        materials.setdefault(page, Material((1.0, 1.0, 1.0, 1.0), 0.6))
        images[page] = encode_png_rgba(pixels)
    materials.setdefault("street_sign_edge", Material(EDGE_COLOR, 0.7))
    boarded = {n.name[len(BUILDING_NODE):] for n in nodes if n.name.startswith(BUILDING_NODE)
               and any(p.material == "glass_boarded" for p in n.primitives)}
    shops = shop_signs(manifest, boarded)
    materials.update(shops.materials)
    images.update(shops.images)

    visual = build_glb(
        [*nodes, *sign_nodes, *shops.nodes],
        materials,
        extras={"manifest": "scene_detail.json", "street_signs": len(signs), "shop_signs": len(shops.rows)},
        repeat_images=images,
        preserve_uvs=True,
    )
    targets = prepare_outputs(out, OUTPUT_FILES, overwrite=overwrite)
    atomic_write_bytes(targets[0], visual)
    collision = base / "scene_collision.glb"
    atomic_write_bytes(targets[1], collision.read_bytes() if collision.is_file() else b"")

    written = dict(manifest)
    objects = list(written.get("objects") or [])
    for sign in signs:
        objects.append({
            "id": f"street_sign/{sign.material}/{_slug_xy(sign)}",
            "type": "street_sign",
            "name": sign.name,
            "name_origin": sign.name_origin,
            "wall_feature": sign.feature_id,
            "position_m": [round(float(v), 3) for v in sign.position],
        })
    for n, row in enumerate(shops.rows):
        objects.append({"id": f"shop_sign/{row['building']}/{n}", "type": "shop_sign", "attribute_origin": "generated", **row})
    written["objects"] = objects
    atomic_write_json(targets[2], written)
    report_src = base / "detail_report.json"
    detail_report = json.loads(report_src.read_text(encoding="utf-8")) if report_src.is_file() else {}
    after = _geographic(written)
    compare = compare_fingerprints(before, after)
    detail_report["street_signs"] = {"count": len(signs), "geometry": compare}
    detail_report["shop_signs"] = {"count": len(shops.rows), "skipped": shops.skipped}
    atomic_write_json(targets[3], detail_report)
    signs_report = {
        "schema": "onward_generator.street_signs",
        "schema_version": "0.1.0",
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "generator_version": __version__,
        "base_dir": str(base),
        "geo_scene": str(Path(geo_scene).expanduser().resolve()),
        "count": len(signs),
        "max_signs": max_signs,
        "signs": [
            {
                "name": sign.name,
                "name_origin": sign.name_origin,
                "material": slots[sign.name][0],
                "wall_feature": sign.feature_id,
                "position_m": [round(float(v), 3) for v in sign.position],
            }
            for sign in signs
        ],
        "shop_signs": {
            "count": len(shops.rows),
            "skipped_atlas_full": shops.skipped,
            "pages": len(shops.images),
            "kinds": {kind: sum(1 for row in shops.rows if row["kind"] == kind) for kind in ("fascia", "blade", "vinyl")},
            "triangles": sum(row["triangles"] for row in shops.rows),
            "signs": shops.rows,
        },
        "geometry": compare,
        "input_hashes": {
            "scene_detail.json": file_sha256(base / "scene_detail.json"),
            "scene_visual.glb": file_sha256(base / "scene_visual.glb"),
        },
    }
    atomic_write_json(targets[4], signs_report)
    if not compare["identical"]:
        raise SignsError("street signs changed a geographic object record; this is a bug")
    return signs_report


def _geographic(manifest: Mapping[str, Any]) -> dict[str, str]:
    objects = [o for o in (manifest.get("objects") or []) if isinstance(o, dict) and o.get("type") in GEOGRAPHIC]
    return geometry_fingerprint(objects)


def _slug_xy(sign: Sign) -> str:
    return f"{sign.position[0]:.1f}_{sign.position[1]:.1f}".replace("-", "m").replace(".", "p")


def _streets(features: Sequence[Mapping[str, Any]]) -> list[_Street]:
    grouped: dict[str, list[LineString]] = {}
    ids: dict[str, str] = {}
    for feature in features:
        if feature.get("kind") != "highway":
            continue
        tags = feature.get("tags") or {}
        if str(tags.get("highway") or "") in SKIP_HIGHWAYS:
            continue
        name = " ".join(str(tags.get("name") or "").split())
        if len(name) < 2:
            continue
        geom = _line(shape(_geometry(feature)))
        if geom is None:
            continue
        grouped.setdefault(name, []).append(geom)
        ids.setdefault(name, str(feature.get("id") or ""))
    streets: list[_Street] = []
    for name, parts in grouped.items():
        for line in parts:
            streets.append(_Street(name, line, ids[name]))
    return streets


def _footprints(features: Sequence[Mapping[str, Any]]) -> list[Polygon]:
    """Every building polygon, the parts of a multipolygon apart."""
    parts = (get_parts(shape(_geometry(feature))) for feature in features if feature.get("kind") == "building")
    return [part for group in parts for part in group if isinstance(part, Polygon)]


def _walls(features: Sequence[Mapping[str, Any]]) -> list[_Wall]:
    walls: list[_Wall] = []
    for feature in features:
        if feature.get("kind") != "building":
            continue
        geom = shape(_geometry(feature))
        if geom.is_empty or geom.geom_type not in {"Polygon", "MultiPolygon"}:
            continue
        height = _height_m(feature.get("tags") or {})
        polygons = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
        for polygon in polygons:
            if not isinstance(polygon, Polygon) or polygon.exterior is None:
                continue
            # Counter-clockwise, so (dy, -dx) points out of the building whichever way the ring was drawn.
            polygon = orient(polygon)
            coords = [np.array(p[:2], dtype=np.float64) for p in polygon.exterior.coords]
            for start, end in zip(coords, coords[1:]):
                edge = end - start
                length = float(np.linalg.norm(edge))
                if length < MIN_EDGE_M:
                    continue
                outward = np.array([edge[1], -edge[0]], dtype=np.float64) / length
                walls.append(_Wall(str(feature.get("id") or ""), start, end, outward, height))
    return walls


def _choose(streets: Sequence[_Street], walls: Sequence[_Wall], max_signs: int, buildings: STRtree | None = None) -> list[Sign]:
    if not streets or not walls:
        return []
    junctions = _junctions(streets)
    tree = STRtree([LineString([wall.a, wall.b]) for wall in walls])
    chosen: list[Sign] = []
    used_edges: set[tuple[str, int]] = set()
    seen_names: set[str] = set()
    for point, names in junctions:
        for name in names:
            sign = _sign_for(name, point, streets, walls, tree, used_edges, buildings)
            if sign is None or _too_close(sign, chosen):
                continue
            chosen.append(sign)
            seen_names.add(name)
    for street in streets:
        if street.name in seen_names:
            continue
        mid = street.line.interpolate(0.5, normalized=True)
        sign = _sign_for(street.name, np.array([mid.x, mid.y]), streets, walls, tree, used_edges, buildings)
        if sign is None or _too_close(sign, chosen):
            continue
        chosen.append(sign)
        seen_names.add(street.name)
    chosen.sort(key=lambda s: (float(s.position[0] ** 2 + s.position[1] ** 2), s.name))
    return chosen[:max_signs]


def _junctions(streets: Sequence[_Street]) -> list[tuple[np.ndarray, tuple[str, ...]]]:
    lines = [s.line for s in streets]
    tree = STRtree(lines)
    raw: list[tuple[np.ndarray, str]] = []
    for index, street in enumerate(streets):
        for other in tree.query(street.line):
            other = int(other)
            if other <= index or streets[other].name == street.name:
                continue
            cross = street.line.intersection(lines[other])
            for point in _points(cross):
                raw.append((np.array([point.x, point.y], dtype=np.float64), street.name))
                raw.append((np.array([point.x, point.y], dtype=np.float64), streets[other].name))
    if not raw:
        return []
    clusters: list[list[tuple[np.ndarray, str]]] = []
    for item in raw:
        placed = False
        for cluster in clusters:
            if float(np.linalg.norm(cluster[0][0] - item[0])) <= CLUSTER_M:
                cluster.append(item)
                placed = True
                break
        if not placed:
            clusters.append([item])
    junctions: list[tuple[np.ndarray, tuple[str, ...]]] = []
    for cluster in clusters:
        centre = np.mean([p for p, _ in cluster], axis=0)
        names = tuple(sorted({name for _, name in cluster}))
        junctions.append((centre, names))
    junctions.sort(key=lambda item: (float(item[0][0]), float(item[0][1]), item[1]))
    return junctions


def _too_close(sign: Sign, chosen: Sequence[Sign]) -> bool:
    for other in chosen:
        if other.name != sign.name:
            continue
        if float(np.linalg.norm(other.position[:2] - sign.position[:2])) < 1.5:
            return True
    return False


def _sign_for(
    name: str,
    point: np.ndarray,
    streets: Sequence[_Street],
    walls: Sequence[_Wall],
    tree: STRtree,
    used_edges: set[tuple[str, int]],
    buildings: STRtree | None = None,
) -> Sign | None:
    """The nearest wall within ``MAX_JUNCTION_M`` that faces ``point`` along the street.

    ``tree`` holds one segment per wall, in the order of ``walls``. Only walls
    whose box meets the square of half-side ``MAX_JUNCTION_M`` around ``point``
    can be that close, and they are tried in the order of ``walls``.
    """
    tangent = _tangent(name, point, streets)
    best: tuple[float, int, _Wall, np.ndarray] | None = None
    x, y = float(point[0]), float(point[1])
    near = tree.query(box(x - MAX_JUNCTION_M, y - MAX_JUNCTION_M, x + MAX_JUNCTION_M, y + MAX_JUNCTION_M))
    for index in sorted(int(k) for k in near):
        wall = walls[index]
        if wall.height_m is not None and wall.height_m < MIN_WALL_M:
            continue
        edge = wall.b - wall.a
        length = float(np.linalg.norm(edge))
        direction = edge / length
        if abs(float(direction @ tangent)) < 0.65:
            continue
        closest = _closest_on_segment(point, wall.a, wall.b)
        offset = closest - point
        distance = float(np.linalg.norm(offset))
        if distance > MAX_JUNCTION_M or distance < 0.4:
            continue
        # Outward must point from the wall back toward the junction.
        if float(wall.outward @ (offset / distance)) > -0.35:
            continue
        along_street = _closest_on_line(closest, name, streets)
        if along_street is None or float(np.linalg.norm(along_street - closest)) > MAX_JUNCTION_M:
            continue
        if (wall.feature_id, index) in used_edges:
            continue
        # A party wall, or a wall another footprint covers, has a building in front of it, not a street.
        if buildings is not None and buildings.query(Point(*(closest + wall.outward * FRONT_CLEAR_M)), predicate="intersects").size:
            continue
        rank = distance
        if best is None or rank < best[0]:
            best = (rank, index, wall, closest)
    if best is None:
        return None
    _, index, wall, closest = best
    used_edges.add((wall.feature_id, index))
    centre = np.array([closest[0], closest[1], MOUNT_Z_M], dtype=np.float64)
    centre[:2] = closest + wall.outward * (WALL_GAP_M + PLATE_THICK_M * 0.5)
    along = wall.b - wall.a
    along = along / np.linalg.norm(along)
    return Sign(name, material_id(name), centre, along, wall.outward, wall.feature_id)


def _plate_meshes(sign: Sign) -> tuple[Mesh, Mesh, Mesh]:
    x = np.array([sign.along[0], sign.along[1], 0.0], dtype=np.float64)
    y = np.array([sign.outward[0], sign.outward[1], 0.0], dtype=np.float64)
    z = np.array([0.0, 0.0, 1.0], dtype=np.float64)
    origin = sign.position

    def at(u: float, v: float, w: float) -> tuple[float, float, float]:
        point = origin + x * u + np.array([y[0], y[1], 0.0]) * v + z * w
        return (float(point[0]), float(point[1]), float(point[2]))

    hw, hh, ht = PLATE_WIDTH_M / 2, PLATE_HEIGHT_M / 2, PLATE_THICK_M / 2
    front = quad(at(-hw, ht, -hh), at(hw, ht, -hh), at(hw, ht, hh), at(-hw, ht, hh))
    back = quad(at(hw, -ht, -hh), at(-hw, -ht, -hh), at(-hw, -ht, hh), at(hw, -ht, hh))
    sides = Mesh(
        np.vstack([
            quad(at(-hw, -ht, -hh), at(hw, -ht, -hh), at(hw, ht, -hh), at(-hw, ht, -hh)).vertices,
            quad(at(-hw, ht, hh), at(hw, ht, hh), at(hw, -ht, hh), at(-hw, ht, hh)).vertices,
            quad(at(-hw, -ht, -hh), at(-hw, ht, -hh), at(-hw, ht, hh), at(-hw, -ht, hh)).vertices,
            quad(at(hw, ht, -hh), at(hw, -ht, -hh), at(hw, -ht, hh), at(hw, ht, hh)).vertices,
        ]),
        np.vstack([np.array([[0, 1, 2], [0, 2, 3]]) + 4 * i for i in range(4)]),
    )
    return front, back, sides


def _edge_uvs(sides: Mesh) -> np.ndarray:
    """One 0–1 quad per side face.

    The face that carries the name already has coordinates. The rim did not, and
    the adapter drops every coordinate on a mesh when one face is missing them,
    so the name texture was sampled at its blue corner.
    """
    count = len(sides.vertices)
    uvs = np.zeros((count, 2), dtype=np.float32)
    quad = np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]], dtype=np.float32)
    for start in range(0, count, 4):
        uvs[start:start + 4] = quad
    return uvs


def _tangent(name: str, point: np.ndarray, streets: Sequence[_Street]) -> np.ndarray:
    best: tuple[float, np.ndarray] | None = None
    here = Point(float(point[0]), float(point[1]))
    for street in streets:
        if street.name != name:
            continue
        distance = float(street.line.distance(here))
        fraction = float(street.line.project(here, normalized=True))
        sample = street.line.interpolate(min(1.0, fraction + 0.02), normalized=True)
        direction = np.array([sample.x - point[0], sample.y - point[1]], dtype=np.float64)
        length = float(np.linalg.norm(direction))
        if length < 1e-4:
            continue
        direction /= length
        if best is None or distance < best[0]:
            best = (distance, direction)
    if best is None:
        return np.array([1.0, 0.0])
    return best[1]


def _closest_on_line(point: np.ndarray, name: str, streets: Sequence[_Street]) -> np.ndarray | None:
    here = Point(float(point[0]), float(point[1]))
    best: tuple[float, np.ndarray] | None = None
    for street in streets:
        if street.name != name:
            continue
        snapped = street.line.interpolate(street.line.project(here))
        distance = float(np.hypot(snapped.x - point[0], snapped.y - point[1]))
        if best is None or distance < best[0]:
            best = (distance, np.array([snapped.x, snapped.y]))
    return None if best is None else best[1]


def _closest_on_segment(point: np.ndarray, start: np.ndarray, end: np.ndarray) -> np.ndarray:
    edge = end - start
    length2 = float(edge @ edge)
    if length2 <= 1e-8:
        return start.copy()
    t = float(np.clip((point - start) @ edge / length2, 0.0, 1.0))
    margin = (PLATE_WIDTH_M * 0.5) / max(float(np.sqrt(length2)), 1e-6)
    t = float(np.clip(t, margin, 1.0 - margin))
    return start + edge * t


def _points(geom: BaseGeometry) -> list[Point]:
    if geom.is_empty:
        return []
    if geom.geom_type == "Point":
        return [geom]
    if geom.geom_type == "MultiPoint":
        return list(geom.geoms)
    if geom.geom_type == "GeometryCollection":
        return [p for part in geom.geoms for p in _points(part)]
    return []


def _line(geom: BaseGeometry) -> LineString | None:
    if geom.geom_type == "LineString" and geom.length >= 8.0:
        return geom
    if geom.geom_type == "MultiLineString":
        parts = [part for part in geom.geoms if part.length >= 8.0]
        if not parts:
            return None
        return max(parts, key=lambda part: part.length)
    return None


def _geometry(feature: Mapping[str, Any]) -> Mapping[str, Any]:
    geometry = feature.get("geometry") or {}
    return {"type": geometry.get("type"), "coordinates": geometry.get("coordinates")}


def _height_m(tags: Mapping[str, Any]) -> float | None:
    raw = tags.get("height") or tags.get("building:height")
    if raw not in (None, ""):
        return parse_metres(raw)
    levels = tags.get("building:levels")
    if levels not in (None, ""):
        try:
            return float(str(levels).split(";")[0]) * 3.0
        except ValueError:
            return None
    return None
