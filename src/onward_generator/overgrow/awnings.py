"""Striped awnings under the shops' fascia signs on a lived street (plan 2026-11-16, increment 3; ADR-044).

A street mood with ``"awnings": true`` (``config/street_moods.json``) asks overgrow for them. ``detail`` records each
shop's fascia board over its window (``facade.shops[].sign``, kind ``fascia``): its centre, its outward normal, its
width and height. One shop in ``share`` (a hash of the seed and the shop) gets an awning hung just under that board, as
wide as it, reaching ``depth_m`` out of the wall and sloping down at most ``max_drop_m``, with a ``valance_m`` skirt
(``cloth.awning_mesh``). Its lowest edge stays ``head_clear_m`` above the ground: the canopy slopes less under a low
fascia, and one that cannot slope ``min_drop_m`` is left out.

An awning is left out, too, outside the zone, where the building across is nearer than its depth and ``free_m``, and
where it would cover a street name plate or a wall decal ``signs`` hung, or another shop's sign. The wall props placed
after it keep off it as off a sign. A blade sign has no window board above it, so its shop gets none. No awning collides.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

import numpy as np
from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match
from shapely import STRtree
from shapely.geometry import LineString, Polygon

from onward_generator.detail.facades import stable_unit
from onward_generator.detail.shops import sign_footprints
from onward_generator.generation.gltf import Node, Primitive
from onward_generator.overgrow.cloth import FABRIC_MATERIAL, POLE_BAND, awning_mesh
from onward_generator.overgrow.terraces import free_across
from onward_generator.overgrow.zone import Zone

REPO_ROOT = Path(__file__).resolve().parents[3]
AWNINGS_PATH = REPO_ROOT / "config" / "awnings.json"
SCHEMA_PATH = REPO_ROOT / "config" / "awnings.schema.json"
NODE_PREFIX = "overgrow/awning/"
PLATE_HALF_M = (0.7, 0.18)
"""Half the width and height of a street name plate (``signs.place.PLATE_WIDTH_M``, ``PLATE_HEIGHT_M``)."""
WALL_CLEAR_M = 0.1
"""A plate, decal or other sign is kept this far from an awning's box."""
RAY_START_M = 0.05
"""The ray across the street starts this far out, so the shop's own wall does not stop it."""
WALL_GAP_M = 0.01
"""The canopy's top edge stands this far out of the wall, so it does not flicker in it."""


class AwningError(ValueError):
    """``config/awnings.json`` is not valid."""


@dataclass(frozen=True, slots=True)
class AwningConfig:
    share: float
    depth_m: float
    max_drop_m: float
    min_drop_m: float
    valance_m: float
    head_clear_m: float
    under_fascia_m: float
    free_m: float


def parse_awnings(doc: object, schema: Mapping[str, Any], where: str = "awnings") -> AwningConfig:
    """The checked configuration."""
    first = best_match(Draft202012Validator(schema).iter_errors(doc))
    if first is not None:
        path = "/".join(str(part) for part in first.absolute_path)  # pragma: no mutate: the schema is flat, a path has one part
        raise AwningError(f"{where}: {path}: {first.message}" if path else f"{where}: {first.message}")
    assert isinstance(doc, dict)  # the schema requires an object  # pragma: no mutate: narrows the type only
    return AwningConfig(*(float(doc[name]) for name in ("share", "depth_m", "max_drop_m", "min_drop_m", "valance_m", "head_clear_m",
                                                         "under_fascia_m", "free_m")))


@cache
def load_awnings(path: Path = AWNINGS_PATH) -> AwningConfig:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AwningError(f"{path}: {exc}") from exc
    return parse_awnings(doc, schema, str(path))


def fascias(manifest: Mapping[str, Any]) -> list[tuple[str, str, Mapping[str, Any]]]:
    """``(building id, shop id, sign)`` of every fascia board ``detail`` hung, in record order."""
    found: list[tuple[str, str, Mapping[str, Any]]] = []
    for item in manifest.get("objects") or []:
        if not isinstance(item, dict) or item.get("type") != "building":
            continue
        building = str((item.get("source_ids") or [""])[0])
        for shop in (item.get("facade") or {}).get("shops") or []:
            sign = shop.get("sign") if isinstance(shop, dict) else None
            if isinstance(sign, dict) and sign.get("kind") == "fascia":
                found.append((building, str(shop.get("id") or ""), sign))
    return found


def wall_things(manifest: Mapping[str, Any]) -> list[tuple[float, float, float, float, float]]:
    """``(x, y, half width, bottom z, top z)`` of every street name plate and wall decal ``signs`` hung."""
    found = []
    for item in manifest.get("objects") or []:
        if not isinstance(item, dict) or len(item.get("position_m") or []) < 3:
            continue
        x, y, z = (float(v) for v in item["position_m"][:3])
        if item.get("type") == "street_sign":
            found.append((x, y, PLATE_HALF_M[0], z - PLATE_HALF_M[1], z + PLATE_HALF_M[1]))
        elif item.get("type") == "wall_decal" and len(item.get("size_m") or []) >= 2:
            width, height = (float(v) for v in item["size_m"][:2])
            found.append((x, y, width / 2, z - height / 2, z + height / 2))
    return found


def overlaps(z0: float, z1: float, low: float, high: float) -> bool:
    """Whether the height range ``z0``-``z1`` overlaps ``low``-``high``."""
    return z0 < high and z1 > low  # pragma: no mutate: < or <=: ranges exactly touching, to the float


def plate_line(x: float, y: float, half: float, along: np.ndarray) -> LineString:
    """A plate or decal ``half`` wide each way of (``x``, ``y``), run along the awning's wall."""
    return LineString([(x - half * along[0], y - half * along[1]), (x + half * along[0], y + half * along[1])])


@dataclass(slots=True)
class AwningResult:
    nodes: list[Node] = field(default_factory=list)
    records: list[dict[str, Any]] = field(default_factory=list)
    footprints: list[tuple[Polygon, float, float]] = field(default_factory=list)
    """(plan, bottom z, top z) of each awning, for the wall props placed after it."""
    triangles: int = 0
    skipped: Counter[str] = field(default_factory=Counter)
    fascias: int = 0

    def report(self) -> dict[str, Any]:
        """``awnings`` in ``overgrow_report.json``."""
        return {"fascias": self.fascias, "awnings": len(self.nodes), "triangles": self.triangles, "skipped": dict(sorted(self.skipped.items()))}


def place_awnings(manifest: Mapping[str, Any], buildings: STRtree, zone: Zone, seed: int, *, budget: int,
                  config: AwningConfig | None = None) -> AwningResult:
    """An awning under each chosen fascia that keeps every rule, within ``budget`` triangles."""
    cfg = config or load_awnings()
    result = AwningResult()
    boards = fascias(manifest)
    result.fascias = len(boards)
    things = wall_things(manifest)
    signs = sign_footprints(manifest)
    for building, shop, sign in boards:
        if stable_unit(seed, shop, "awning") >= cfg.share:  # pragma: no mutate: > or >=: a hash exactly equal to share
            result.skipped["share"] += 1
            continue
        reason = _place(building, shop, sign, cfg, buildings, zone, things, signs, budget, result, seed)
        if reason is not None:
            result.skipped[reason] += 1
    return result


def _place(building: str, shop: str, sign: Mapping[str, Any], cfg: AwningConfig, buildings: STRtree, zone: Zone,
           things: Sequence[tuple[float, float, float, float, float]], signs: Sequence[tuple[Polygon, float, float]], budget: int,
           result: AwningResult, seed: int) -> str | None:
    """Hang one awning under ``sign``; None when it hangs, else why not."""
    centre = np.asarray(sign["centre_m"])
    out = np.asarray(sign["normal"])[:2]
    along = np.array([-out[1], out[0]])
    width = float(sign["width_m"])
    base = centre[:2] - out * (float(sign["depth_m"]) / 2 - WALL_GAP_M)
    top = float(centre[2]) - float(sign["height_m"]) / 2 - cfg.under_fascia_m
    drop = min(cfg.max_drop_m, top - cfg.valance_m - cfg.head_clear_m)
    if drop < cfg.min_drop_m:
        return "low"
    bottom = top - drop - cfg.valance_m
    half = along * (width / 2)
    plan = Polygon([tuple(base - half), tuple(base + half), tuple(base + half + out * cfg.depth_m), tuple(base - half + out * cfg.depth_m)])
    if not zone.contains(float(base[0]), float(base[1])):
        return "outside_zone"
    need = cfg.depth_m + cfg.free_m
    if free_across(base + out * RAY_START_M, out, buildings, need) < need:
        return "narrow"
    grown = plan.buffer(WALL_CLEAR_M)
    if any(overlaps(z0, z1, bottom - WALL_CLEAR_M, top + WALL_CLEAR_M) and grown.intersects(plate_line(x, y, hw, along))
           for x, y, hw, z0, z1 in things):
        return "covers_sign"
    if any(overlaps(z0, z1, bottom, top) and footprint.intersects(plan) for footprint, z0, z1 in signs):  # its own fascia is above it
        return "covers_sign"
    band = int(stable_unit(seed, shop, "colour") * POLE_BAND)  # the colour bands come before the pole's
    mesh, uvs = awning_mesh(base, along, out, width, top, cfg.depth_m, drop, cfg.valance_m, band)
    if result.triangles + mesh.triangle_count > budget:
        return "budget"
    number = len(result.nodes)
    result.nodes.append(Node(
        f"{NODE_PREFIX}{number}",
        (Primitive(mesh, FABRIC_MATERIAL, uvs),),
        {"type": "overgrowth", "origin": "generated", "attribute_origin": "generated", "kind": "awning", "building_id": building,
         "shop": shop, "solid": False},
    ))
    result.triangles += mesh.triangle_count
    result.footprints.append((plan, bottom, top))
    middle = base + out * (cfg.depth_m / 2)
    result.records.append({
        "kind": "awning", "origin": "generated", "source_id": building, "role": "awning",
        "position_m": [round(float(middle[0]), 3), round(float(middle[1]), 3), round(bottom, 3)],
        "size_m": [round(width, 3), round(cfg.depth_m, 3), round(top - bottom, 3)], "solid": False,
    })
    return None


__all__ = ["AWNINGS_PATH", "NODE_PREFIX", "AwningConfig", "AwningError", "AwningResult", "fascias", "load_awnings", "parse_awnings",
           "place_awnings", "wall_things"]
