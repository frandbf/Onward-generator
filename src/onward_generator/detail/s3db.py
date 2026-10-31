"""OSM Simple 3D Buildings tags: level counts and compass directions.

The tags follow https://wiki.openstreetmap.org/wiki/Simple_3D_Buildings. Every value read here is
observed. Lengths are read by ``geo.attributes.parse_metres``, the one OSM height parser.
"""

from __future__ import annotations

import math
from collections.abc import Mapping

# Upper bound of a plausible level count; beyond it the tag is treated as a typo.
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
