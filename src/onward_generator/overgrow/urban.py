"""What ``detail`` placed that overgrow keeps off: urban elements (benches, lamps, bicycle stands, street trees) and shop signs."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import numpy as np
from shapely.geometry import LineString, Polygon

from onward_generator.detail.modules import urban_prototypes

# A prop's footprint keeps this far beyond the collider of a bench, lamp, bicycle stand or other ``detail`` urban element.
DETAIL_ELEMENT_CLEAR_M = 0.3
# Half the plan diagonal of an urban element whose kind has no prototype.
DETAIL_ELEMENT_DEFAULT_M = 1.0
# A shop sign's footprint reaches this far beyond its own depth, front and back.
SIGN_CLEAR_M = 0.05
# A prop lower than this (a manhole cover, a path) lies flush with the ground and may run under an element.
FLAT_PROP_M = 0.1


def detail_elements(manifest: Mapping[str, Any]) -> list[tuple[float, float, float]]:
    """(x, y, radius) of every urban element ``detail`` placed, for the props to keep clear of.

    The radius is half the plan diagonal of its prototype's collider plus ``DETAIL_ELEMENT_CLEAR_M``, and
    a prop's whole footprint stays out of it: a barrier stood on a bicycle stand and a dumpster on a street tree.
    """
    halves = {
        kind: math.hypot(*(proto.collider.vertices.max(axis=0) - proto.collider.vertices.min(axis=0))[:2]) / 2
        for kind, proto in urban_prototypes().items() if proto.collider is not None
    }
    found = []
    for item in manifest.get("objects") or []:
        if not isinstance(item, dict) or item.get("type") != "urban_element":
            continue
        position = item.get("position_m") or []
        if len(position) < 2:
            continue
        half = halves.get(str(item.get("kind")), DETAIL_ELEMENT_DEFAULT_M)
        found.append((float(position[0]), float(position[1]), half + DETAIL_ELEMENT_CLEAR_M))
    return found


def shop_signs(manifest: Mapping[str, Any]) -> list[tuple[Polygon, float, float]]:
    """(plan footprint, bottom z, top z) of every shop sign ``detail`` hung: a fascia along its wall, a blade out from it.

    A wall-mounted prop may not cover one: nothing stopped an awning, 0.9 m tall from 2.5 m, from hanging over a fascia.
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
