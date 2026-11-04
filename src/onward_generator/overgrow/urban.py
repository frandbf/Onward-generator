"""What ``detail`` placed that overgrow keeps off: urban elements (benches, lamps, bicycle stands, street trees).

Shop signs are ``detail.shops.sign_footprints``: ``signs`` keeps its street plates off them too.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from onward_generator.detail.modules import urban_prototypes

# A prop's footprint keeps this far beyond the collider of a bench, lamp, bicycle stand or other ``detail`` urban element.
DETAIL_ELEMENT_CLEAR_M = 0.3
# Half the plan diagonal of an urban element whose kind has no prototype.
DETAIL_ELEMENT_DEFAULT_M = 1.0
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
