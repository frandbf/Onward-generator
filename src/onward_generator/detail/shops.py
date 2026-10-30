"""Shop names, signs and window vinyls on a shopfront ground floor.

Each shop is an OSM POI with shop evidence (or the building itself when its
category is commercial). It gets the shopfront bay nearest to its POI, and on that
bay one sign: a fascia board over the window or a blade sign on a bracket beside
it. The choice is ``sha256(seed, building, shop, shop_sign) < fascia_share``. A
shopfront with a ``glass_dark`` back carries a vinyl strip with the name of the
nearest shop when ``sha256(seed, building, segment, column, vinyl) < vinyl_share``.
See-through ``glass_view`` panes get none.

The name is the OSM ``name`` tag (observed). Without it, a generic Spanish word
for the ``shop``/``amenity``/``office``/``craft`` value is used (estimated). The
signs are generic: they do not show the real sign of any shop. This module only
records where they go. ``signs place`` draws them.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

from onward_generator.detail.config import FASCIA_GAP_M, DetailConfig
from onward_generator.detail.facades import Hole, Segment, stable_unit

VINYL_INSET_M = 0.01  # in front of the glass, inside the recess


def shop_name(tags: Mapping[str, Any], cfg: DetailConfig) -> dict[str, str]:
    """``{"name", "name_origin", "name_rule"}``: the OSM name, else a generic word for the first evidence key."""
    name = " ".join(str(tags.get("name") or "").split())
    if name:
        return {"name": name, "name_origin": "observed", "name_rule": "name"}
    names = cfg.shops.generic_names
    for key in cfg.shop_evidence_keys:
        if key not in tags:
            continue
        for rule in (f"{key}={tags[key]}", key):
            if rule in names:
                return {"name": names[rule], "name_origin": "estimated", "name_rule": rule}
    return {"name": cfg.shops.default_name, "name_origin": "estimated", "name_rule": "default"}


def _board(seg: Segment, s: float, z: float, out: float, normal: np.ndarray, width: float, height: float, depth: float) -> dict[str, Any]:
    """A board centred at ``s`` along the segment, height ``z`` and ``out`` metres outside the wall face."""
    return {
        "centre_m": [float(v) for v in seg.point(s, z, -out)],
        "normal": [float(v) for v in normal[:2]],
        "width_m": width,
        "height_m": height,
        "depth_m": depth,
    }


def _sign(kind: str, seg: Segment, hole: Hole, cfg: DetailConfig) -> dict[str, Any]:
    sc = cfg.shops
    if kind == "fascia":
        z0 = hole.v0 + hole.height + FASCIA_GAP_M
        board = _board(seg, hole.s0 + hole.width / 2, z0 + sc.fascia_height_m / 2, sc.fascia_depth_m / 2, seg.n_out,
                       hole.width + 2 * sc.fascia_overhang_m, sc.fascia_height_m, sc.fascia_depth_m)
        return {"kind": kind, **board, "double_sided": False, "bracket_m": 0.0}
    out = sc.blade_wall_gap_m + sc.blade_width_m / 2
    board = _board(seg, hole.s0 - cfg.min_pier_m / 4, sc.blade_clearance_m + sc.blade_height_m / 2, out, -seg.u,
                   sc.blade_width_m, sc.blade_height_m, sc.blade_thickness_m)
    return {"kind": kind, **board, "double_sided": True, "bracket_m": sc.blade_wall_gap_m + sc.blade_width_m}


def _vinyl(seg: Segment, hole: Hole, cfg: DetailConfig) -> dict[str, Any]:
    sc = cfg.shops
    half = sc.vinyl_height_m / 2
    z = min(max(sc.vinyl_centre_m, hole.v0 + half), hole.v0 + hole.height - half)
    return _board(seg, hole.s0 + hole.width / 2, z, VINYL_INSET_M - hole.depth, seg.n_out,
                  hole.width * sc.vinyl_width_share, sc.vinyl_height_m, 0.0)


def plan_shops(
    building_id: str,
    shops: Sequence[tuple[str | None, Mapping[str, Any], tuple[float, float] | None]],
    holes: Sequence[Hole],
    segments: Sequence[Segment],
    cfg: DetailConfig,
) -> list[dict[str, Any]]:
    """One record per shop ``(poi_id, tags, point)``: its name, its sign, and the vinyls on the windows it owns.

    A shop takes the free shopfront nearest to its point (the first free one without a point).
    A shop left without a shopfront gets no sign and no vinyl. ``shops`` is never empty.
    """
    fronts = sorted((h for h in holes if h.kind == "shopfront"), key=lambda h: (h.segment, h.s0))
    centre = {h: tuple(segments[h.segment].point(h.s0 + h.width / 2)[:2]) for h in fronts}
    free = list(fronts)
    records: list[dict[str, Any]] = []
    bays: list[tuple[Hole, dict[str, Any]]] = []
    for poi_id, tags, point in shops:
        key = poi_id or "building"
        record: dict[str, Any] = {"id": f"shop/{building_id}/{key}", "poi_id": poi_id, **shop_name(tags, cfg), "sign": None, "vinyls": []}
        records.append(record)
        if not free:
            continue
        bay = free[0] if point is None else _nearest(point, free, centre)
        free.remove(bay)
        kind = "fascia" if stable_unit(cfg.seed, building_id, key, "shop_sign") < cfg.shops.fascia_share else "blade"
        record["sign"] = {**_sign(kind, segments[bay.segment], bay, cfg), "attribute_origin": "generated"}
        bays.append((bay, record))
    for hole in fronts:
        if hole.back != "glass_dark" or stable_unit(cfg.seed, building_id, hole.segment, hole.column, "vinyl") >= cfg.shops.vinyl_share:
            continue
        owner = _nearest(centre[hole], [b for b, _ in bays], centre)
        next(r for b, r in bays if b == owner)["vinyls"].append(_vinyl(segments[hole.segment], hole, cfg))
    return records


def _nearest(point: Sequence[float], holes: Sequence[Hole], centre: Mapping[Hole, Sequence[float]]) -> Hole:
    """The hole whose centre is closest to ``point``; the first one on a tie."""
    distances = [math.dist(point, centre[h]) for h in holes]
    return holes[distances.index(min(distances))]
