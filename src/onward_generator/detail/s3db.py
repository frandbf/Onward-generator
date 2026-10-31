"""OSM Simple 3D Buildings tags: level counts, compass directions and part heights.

The tags follow https://wiki.openstreetmap.org/wiki/Simple_3D_Buildings. Every value read here is
observed; a height worked out from a level count is an estimate and says so.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from onward_generator.geo.attributes import parse_metres

# Upper bound of a plausible level count; beyond it the tag is treated as a typo. Lengths are read by
# geo.attributes.parse_metres, the one OSM height parser: (0, 200] m.
MAX_LEVELS = 100.0

# 16-point compass rose, clockwise from north.
_COMPASS = ("n", "nne", "ne", "ene", "e", "ese", "se", "sse", "s", "ssw", "sw", "wsw", "w", "wnw", "nw", "nnw")

def first_value(tags: Mapping[str, str], key: str) -> str:
    """The first ``;``-separated value of ``key``, stripped and lower case, or ``""``."""
    raw = tags.get(key) or ""
    return str(raw).split(";")[0].strip().lower()


def parse_levels(raw: object) -> float | None:
    """A level count of zero or more (``3``, ``0.5``), or ``None``."""
    text = str(raw).strip().replace(",", ".")
    try:
        value = float(text)
    except ValueError:
        return None
    return value if 0 <= value <= MAX_LEVELS else None


def parse_direction(raw: object) -> float | None:
    """A compass bearing in degrees clockwise from north (``135``, ``SE``), in [0, 360), or ``None``."""
    text = str(raw).strip().lower()
    if text in _COMPASS:
        return _COMPASS.index(text) * 22.5
    try:
        value = float(text)
    except ValueError:
        return None
    return value % 360.0 if math.isfinite(value) else None


@dataclass(frozen=True, slots=True)
class PartHeight:
    """The vertical extent of a ``building:part``: the S3DB ``height`` includes the roof."""

    height_m: float
    min_height_m: float
    origin: str  # observed (height tag) | estimated (from levels)
    min_height_origin: str  # observed | estimated | default
    rule: str
    roof_levels: float

    def to_record(self) -> dict[str, object]:
        return {
            "height_m": round(self.height_m, 3),
            "min_height_m": round(self.min_height_m, 3),
            "origin": self.origin,
            "min_height_origin": self.min_height_origin,
            "rule": self.rule,
        }


def part_height(tags: Mapping[str, str], storey_m: float, category: str) -> PartHeight | None:
    """Height and base of a part from ``height`` / ``min_height``, else from its level tags.

    Without ``height``, ``(building:levels + roof:levels) × storey_m`` is the height, an estimate from
    observed level counts. Without ``min_height``, ``building:min_level`` (or ``min_level``) ``× storey_m``
    is the base. ``None`` when neither ``height`` nor ``building:levels`` is usable.
    """
    roof_levels = parse_levels(tags.get("roof:levels")) or 0.0
    height = parse_metres(tags.get("height"))
    if height is not None:
        origin, rule = "observed", "height"
    else:
        levels = parse_levels(tags.get("building:levels"))
        if not levels:
            return None
        height = (levels + roof_levels) * storey_m
        origin = "estimated"
        rule = f"(building:levels {levels:g} + roof:levels {roof_levels:g}) × {storey_m:g} m ({category})"
    min_height = parse_metres(tags.get("min_height"))
    min_origin = "observed"
    if min_height is None:
        min_level = parse_levels(tags.get("building:min_level"))
        if min_level is None:
            min_level = parse_levels(tags.get("min_level"))
        min_height = (min_level or 0.0) * storey_m
        min_origin = "estimated" if min_level else "default"
    return PartHeight(height, min_height, origin, min_origin, rule, roof_levels)
