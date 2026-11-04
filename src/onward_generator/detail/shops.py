"""Shop names, signs and window vinyls on a shopfront ground floor.

Each shop is an OSM POI with shop evidence (or the building itself when its
category is commercial). It gets the shopfront bay nearest to its POI, and on that
bay one sign: a fascia board over the window or a blade sign on a bracket beside
it. The choice is ``sha256(seed, building, shop, shop_sign) < fascia_share``. A
shopfront carries a vinyl strip with the name of the nearest shop when
``sha256(seed, building, segment, column, vinyl) < vinyl_share``, on a ``glass_dark``
back or on the see-through pane of a shop interior (``shop_glass``, or ``glass_view``).
Each vinyl records the ``glass`` it sits on: ``signs`` draws black letters with no
background on ``shop_glass`` and an opaque strip on the rest.

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
from shapely.geometry import LineString, Polygon

from onward_generator.detail.config import FASCIA_GAP_M, DetailConfig, ShopCategory
from onward_generator.detail.facades import Hole, Segment, stable_unit

CATASTRO_USE_TAG = "catastro:current_use"
"""The tag a shop of a Catastro-use building carries in place of an OSM POI's tags (ADR-022). Not an OSM tag."""
VINYL_INSET_M = 0.01  # in front of the glass, inside the recess
VINYL_BACKS = frozenset({"glass_dark", "glass_view", "shop_glass"})
# A shop sign's footprint reaches this far beyond its own depth, front and back.
SIGN_CLEAR_M = 0.05
# One shop: its POI id (``None`` for the building itself), its tags, and its point in local metres.
Shop = tuple[str | None, Mapping[str, Any], tuple[float, float] | None]


def shop_name(tags: Mapping[str, Any], cfg: DetailConfig) -> dict[str, str]:
    """``{"name", "name_origin", "name_rule"}``: the OSM name, else a generic word for the first evidence key,
    else the generic word for the building's Catastro use, else the default."""
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
    use = str(tags.get(CATASTRO_USE_TAG))
    if use in cfg.shop_catastro_uses:
        return {"name": cfg.shop_catastro_uses[use], "name_origin": "estimated", "name_rule": f"catastro_use={use}"}
    return {"name": cfg.shops.default_name, "name_origin": "estimated", "name_rule": "default"}


def shop_category(shops: Sequence[Mapping[str, Any]], cfg: DetailConfig) -> ShopCategory:
    """The interior colours of a shop building: the first shop, in order, that a category's tags match; else ``generic``."""
    rooms = cfg.interiors.shops
    for tags in shops:
        for category in rooms.categories:
            if category.matches(tags):
                return category
    return rooms.generic


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
    board = _board(seg, hole.s0 + hole.width / 2, z, VINYL_INSET_M - hole.depth, seg.n_out,
                   hole.width * sc.vinyl_width_share, sc.vinyl_height_m, 0.0)
    return {**board, "glass": hole.back}


def _fronts(holes: Sequence[Hole], segments: Sequence[Segment], entrances: Sequence[Hole]) -> dict[Hole, tuple[float, ...]]:
    """The shopfronts and ``entrances``, in wall order, with the centre of each."""
    fronts = sorted((h for h in holes if h.kind == "shopfront" or h in entrances), key=lambda h: (h.segment, h.s0))
    return {h: tuple(segments[h.segment].point(h.s0 + h.width / 2)[:2]) for h in fronts}


def shop_bays(shops: Sequence[Shop], holes: Sequence[Hole], segments: Sequence[Segment], entrances: Sequence[Hole] = ()) -> list[Hole | None]:
    """The bay of each shop: the free shopfront nearest to its point (the first free one without a point), else ``None``.

    ``entrances`` are walkable doors that took a shopfront (``interiors.plan_shop_rooms``); they count as one.
    """
    centre = _fronts(holes, segments, entrances)
    free = list(centre)
    bays: list[Hole | None] = []
    for _, _, point in shops:
        bay = None if not free else free[0] if point is None else _nearest(point, free, centre)
        if bay is not None:
            free.remove(bay)
        bays.append(bay)
    return bays


def plan_shops(
    building_id: str,
    shops: Sequence[Shop],
    holes: Sequence[Hole],
    segments: Sequence[Segment],
    cfg: DetailConfig,
    entrances: Sequence[Hole] = (),
) -> list[dict[str, Any]]:
    """One record per shop ``(poi_id, tags, point)``: its name, its sign, and the vinyls on the windows it owns.

    A shop takes its bay (``shop_bays``) and its sign goes on it. A shop left without one gets no sign
    and no vinyl. A door in ``entrances`` gets no vinyl. ``shops`` is never empty.
    """
    centre = _fronts(holes, segments, entrances)
    records: list[dict[str, Any]] = []
    bays: list[tuple[Hole, dict[str, Any]]] = []
    assigned = shop_bays(shops, holes, segments, entrances)
    for index, (poi_id, tags, _) in enumerate(shops):
        bay = assigned[index]
        key = poi_id or "building"
        record: dict[str, Any] = {"id": f"shop/{building_id}/{key}", "poi_id": poi_id, **shop_name(tags, cfg), "sign": None, "vinyls": []}
        records.append(record)
        if bay is None:
            continue
        kind = "fascia" if stable_unit(cfg.seed, building_id, key, "shop_sign") < cfg.shops.fascia_share else "blade"
        record["sign"] = {**_sign(kind, segments[bay.segment], bay, cfg), "attribute_origin": "generated"}
        bays.append((bay, record))
    for hole in centre:
        if hole.back not in VINYL_BACKS or stable_unit(cfg.seed, building_id, hole.segment, hole.column, "vinyl") >= cfg.shops.vinyl_share:
            continue
        owner = _nearest(centre[hole], [b for b, _ in bays], centre)
        next(r for b, r in bays if b == owner)["vinyls"].append(_vinyl(segments[hole.segment], hole, cfg))
    return records


def _nearest(point: Sequence[float], holes: Sequence[Hole], centre: Mapping[Hole, Sequence[float]]) -> Hole:
    """The hole whose centre is closest to ``point``; the first one on a tie."""
    distances = [math.dist(point, centre[h]) for h in holes]
    return holes[distances.index(min(distances))]


def sign_footprints(manifest: Mapping[str, Any]) -> list[tuple[Polygon, float, float]]:
    """(plan footprint, bottom z, top z) of every shop sign ``detail`` hung: a fascia along its wall, a blade out from it.

    Neither a wall-mounted overgrow prop nor a street plate may cover one: an awning, 0.9 m tall from 2.5 m, hung
    over a fascia, and the plate of Calle Platería hid most of a shop's name.
    """
    found = []
    for building in manifest.get("objects") or []:
        if not isinstance(building, dict) or building.get("type") != "building":
            continue
        for shop in (building.get("facade") or {}).get("shops") or []:
            sign = shop.get("sign") if isinstance(shop, dict) else None
            if not sign:
                continue
            centre = np.asarray(sign["centre_m"])
            normal = np.asarray(sign["normal"])
            width, depth = float(sign["width_m"]) / 2, float(sign["depth_m"]) / 2 + SIGN_CLEAR_M
            # A fascia runs along the wall; a blade sign stands out of it, along the normal.
            length_axis = np.array([-normal[1], normal[0]]) if sign["kind"] == "fascia" else normal  # pragma: no mutate: either way along the wall gives the same symmetric footprint
            run = LineString([centre[:2] - length_axis * width, centre[:2] + length_axis * width])
            half = float(sign["height_m"]) / 2
            found.append((run.buffer(depth, cap_style="flat"), float(centre[2]) - half, float(centre[2]) + half))
    return found
