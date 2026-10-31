"""OSM Simple 3D Buildings tags: lengths, level counts, compass directions, colours and part heights.

The tags follow https://wiki.openstreetmap.org/wiki/Simple_3D_Buildings. Every value read here is
observed; a height worked out from a level count is an estimate and says so. A colour is quantized to a
small fixed palette and becomes a tint: a shared copy of a catalogue material that keeps its texture and
takes the palette colour (``tint_material``; ADR-023), so colours never add more than a bounded set of
materials (ADR-005).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from onward_generator.geo.attributes import parse_metres

# Upper bound of a plausible level count; beyond it the tag is treated as a typo. Lengths are read by
# geo.attributes.parse_metres, the one OSM height parser: (0, 200] m.
MAX_LEVELS = 100.0

# A tinted material is ``<base><TINT_SEPARATOR><palette key>``: the base's texture under the palette colour.
TINT_SEPARATOR = "__tint_"


@dataclass(frozen=True, slots=True)
class Tint:
    """One palette entry: ``anchor`` is the colour (``#rrggbb``) an OSM value is matched against,
    ``colour`` the material colour drawn, muted as a painted facade or a roof weathers."""

    key: str
    anchor: str
    colour: tuple[float, float, float]


WALL_PALETTE = (
    Tint("white", "#f5f5f0", (0.93, 0.92, 0.89)),
    Tint("cream", "#f0e1b4", (0.95, 0.88, 0.68)),
    Tint("yellow", "#f0c846", (0.93, 0.78, 0.38)),
    Tint("ochre", "#c88c3c", (0.85, 0.60, 0.30)),
    Tint("orange", "#eb7832", (0.88, 0.52, 0.30)),
    Tint("pink", "#ebaaaa", (0.90, 0.68, 0.66)),
    Tint("red", "#b42823", (0.66, 0.27, 0.22)),
    Tint("brown", "#784b2d", (0.46, 0.33, 0.24)),
    Tint("grey", "#969696", (0.62, 0.62, 0.60)),
    Tint("dark", "#373737", (0.28, 0.28, 0.28)),
    Tint("green", "#5a965a", (0.52, 0.66, 0.48)),
    Tint("blue", "#5078c8", (0.54, 0.64, 0.78)),
)
ROOF_PALETTE = (
    Tint("red", "#b43c28", (0.70, 0.30, 0.18)),
    Tint("brown", "#6e462d", (0.42, 0.28, 0.20)),
    Tint("grey", "#969696", (0.58, 0.58, 0.57)),
    Tint("dark", "#323237", (0.22, 0.22, 0.24)),
    Tint("light", "#e6e6e1", (0.82, 0.81, 0.78)),
    Tint("green", "#468c6e", (0.36, 0.56, 0.46)),
    Tint("blue", "#3c5aa0", (0.30, 0.38, 0.55)),
)
# The catalogue materials a tint may copy: the texture family the tag names, else plain plaster or tile.
WALL_TINT_BASES = ("plaster_white", "brick_red", "stone_warm", "stone_light")
ROOF_TINT_BASES = ("roof_tile", "roof_flat_grey")

# 16-point compass rose, clockwise from north.
_COMPASS = ("n", "nne", "ne", "ene", "e", "ese", "se", "sse", "s", "ssw", "sw", "wsw", "w", "wnw", "nw", "nnw")

# The colour names OSM uses most for buildings and roofs (CSS values), as sRGB 0–255.
NAMED_COLOURS: dict[str, tuple[int, int, int]] = {
    "white": (255, 255, 255),
    "ivory": (255, 255, 240),
    "linen": (250, 240, 230),
    "beige": (245, 245, 220),
    "wheat": (245, 222, 179),
    "cream": (255, 253, 208),
    "lightyellow": (255, 255, 224),
    "yellow": (255, 255, 0),
    "gold": (255, 215, 0),
    "khaki": (240, 230, 140),
    "tan": (210, 180, 140),
    "orange": (255, 165, 0),
    "salmon": (250, 128, 114),
    "pink": (255, 192, 203),
    "red": (255, 0, 0),
    "darkred": (139, 0, 0),
    "maroon": (128, 0, 0),
    "brown": (165, 42, 42),
    "sienna": (160, 82, 45),
    "chocolate": (210, 105, 30),
    "silver": (192, 192, 192),
    "lightgrey": (211, 211, 211),
    "lightgray": (211, 211, 211),
    "grey": (128, 128, 128),
    "gray": (128, 128, 128),
    "darkgrey": (169, 169, 169),
    "darkgray": (169, 169, 169),
    "dimgrey": (105, 105, 105),
    "dimgray": (105, 105, 105),
    "black": (0, 0, 0),
    "green": (0, 128, 0),
    "darkgreen": (0, 100, 0),
    "blue": (0, 0, 255),
    "lightblue": (173, 216, 230),
    "navy": (0, 0, 128),
    "antiquewhite": (250, 235, 215),
    "bisque": (255, 228, 196),
    "blanchedalmond": (255, 235, 205),
    "burlywood": (222, 184, 135),
    "coral": (255, 127, 80),
    "crimson": (220, 20, 60),
    "cyan": (0, 255, 255),
    "darkblue": (0, 0, 139),
    "darkgoldenrod": (184, 134, 11),
    "darkkhaki": (189, 183, 107),
    "darkolivegreen": (85, 107, 47),
    "darkorange": (255, 140, 0),
    "darkslategray": (47, 79, 79),
    "darkslategrey": (47, 79, 79),
    "firebrick": (178, 34, 34),
    "gainsboro": (220, 220, 220),
    "goldenrod": (218, 165, 32),
    "indianred": (205, 92, 92),
    "lightgreen": (144, 238, 144),
    "lightpink": (255, 182, 193),
    "lightsalmon": (255, 160, 122),
    "lightslategray": (119, 136, 153),
    "lightslategrey": (119, 136, 153),
    "lightsteelblue": (176, 196, 222),
    "magenta": (255, 0, 255),
    "moccasin": (255, 228, 181),
    "navajowhite": (255, 222, 173),
    "oldlace": (253, 245, 230),
    "olive": (128, 128, 0),
    "olivedrab": (107, 142, 35),
    "orangered": (255, 69, 0),
    "palegoldenrod": (238, 232, 170),
    "peachpuff": (255, 218, 185),
    "peru": (205, 133, 63),
    "purple": (128, 0, 128),
    "rosybrown": (188, 143, 143),
    "saddlebrown": (139, 69, 19),
    "sandybrown": (244, 164, 96),
    "seashell": (255, 245, 238),
    "skyblue": (135, 206, 235),
    "slategray": (112, 128, 144),
    "slategrey": (112, 128, 144),
    "snow": (255, 250, 250),
    "steelblue": (70, 130, 180),
    "teal": (0, 128, 128),
    "terracotta": (226, 114, 91),
    "tomato": (255, 99, 71),
    "turquoise": (64, 224, 208),
    "violet": (238, 130, 238),
    "whitesmoke": (245, 245, 245),
}


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


def parse_colour(raw: object) -> tuple[float, float, float] | None:
    """An sRGB colour in [0, 1] from a CSS name or ``#rgb`` / ``#rrggbb``, or ``None``."""
    text = str(raw).strip().lower().replace(" ", "").replace("_", "")
    if text in NAMED_COLOURS:
        rgb = NAMED_COLOURS[text]
    elif text.startswith("#") and len(text) in (4, 7):
        digits = text[1:] if len(text) == 7 else "".join(c * 2 for c in text[1:])
        try:
            red, green, blue = (int(digits[k:k + 2], 16) for k in (0, 2, 4))
            rgb = (red, green, blue)
        except ValueError:
            return None
    else:
        return None
    return (rgb[0] / 255, rgb[1] / 255, rgb[2] / 255)


def _lab(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    """CIELAB (D65) of an sRGB colour in [0, 1]: distances there follow what the eye sees."""

    def linear(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4  # pragma: no mutate: the two branches meet at 0.04045, so < and <= agree

    r, g, b = (linear(c) for c in rgb)
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116  # pragma: no mutate: the two branches meet at 0.008856, so > and >= agree

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def quantize_colour(raw: object, palette: Sequence[Tint]) -> Tint | None:
    """The palette entry nearest ``raw`` (an OSM colour) in CIELAB, or ``None`` when it is not a colour.

    A tie goes to the earlier entry.
    """
    rgb = parse_colour(raw)
    if rgb is None:
        return None
    lab = _lab(rgb)
    return min(palette, key=lambda tint: math.dist(lab, _lab(_anchor(tint))))


def _anchor(tint: Tint) -> tuple[float, float, float]:
    rgb = parse_colour(tint.anchor)
    assert rgb is not None, tint
    return rgb


def tint_material(base: str, tint: Tint) -> str:
    """The name of ``base`` under ``tint``: the same texture and roughness, the palette colour."""
    return f"{base}{TINT_SEPARATOR}{tint.key}"


def tint_base(material: str) -> str:
    """The catalogue material a tinted material copies; any other material is its own base."""
    return material.partition(TINT_SEPARATOR)[0]


def tint_materials(colours: Mapping[str, tuple[float, float, float, float]]) -> dict[str, tuple[float, float, float, float]]:
    """The colour of every tint of every base in ``colours``: walls take ``WALL_PALETTE``, roofs ``ROOF_PALETTE``.

    ``colours`` maps a material to its RGBA colour; a tint keeps the base's alpha.
    """
    out: dict[str, tuple[float, float, float, float]] = {}
    for bases, palette in ((WALL_TINT_BASES, WALL_PALETTE), (ROOF_TINT_BASES, ROOF_PALETTE)):
        for base in (b for b in bases if b in colours):
            out |= {tint_material(base, t): (*t.colour, colours[base][3]) for t in palette}
    return out


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
