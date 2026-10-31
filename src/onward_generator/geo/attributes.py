"""Typed attributes from OSM tags, separating explicit values from inferences."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from types import MappingProxyType

from onward_generator.domain.models import Attribute, AttributeOrigin, OsmRef
from onward_generator.geo.issues import GeoIssue
from onward_generator.geo.osm_import import FeatureKind

FEET_TO_M = 0.3048

HIGHWAY_DEFAULT_WIDTH_M: Mapping[str, float] = MappingProxyType(
    {
        "primary": 10.0,
        "secondary": 8.0,
        "tertiary": 7.0,
        "residential": 6.0,
        "living_street": 5.0,
        "service": 4.0,
        "pedestrian": 6.0,
        "footway": 2.0,
        "path": 1.5,
        "steps": 2.0,
        "cycleway": 2.0,
    }
)

_METRES = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(m)?\s*$")
_FEET = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*ft\s*$")
_FEET_INCHES = re.compile(r"^\s*(\d+)'\s*(?:(\d+(?:\.\d+)?)\")?\s*$")


def parse_length_m(raw: str) -> float | None:
    """Parse an OSM length value (metres by default, ``ft`` or ``6'2"``)."""
    if match := _METRES.match(raw):
        value = float(match.group(1))
    elif match := _FEET.match(raw):
        value = float(match.group(1)) * FEET_TO_M
    elif match := _FEET_INCHES.match(raw):
        value = (int(match.group(1)) + float(match.group(2) or 0) / 12) * FEET_TO_M
    else:
        return None
    return value if math.isfinite(value) and value > 0 else None


def parse_metres(raw: object) -> float | None:
    """A building height tag in metres: ``"12"``, ``"12 m"``, ``"12m"`` or ``"12,5"``.

    Anything else (``None`` and ``""`` included), and any value outside (0, 200], is None.
    """
    text = str(raw).strip().lower().replace(",", ".")
    if text.endswith("m"):
        text = text[:-1].strip()
    try:
        value = float(text)
    except ValueError:
        return None
    if not 0 < value <= 200:
        return None
    return value


def parse_count(raw: str) -> int | None:
    stripped = raw.strip()
    if not stripped.isdigit():
        return None
    value = int(stripped)
    return value if value > 0 else None


def derive_attributes(
    ref: OsmRef,
    kind: FeatureKind,
    tags: Mapping[str, str],
) -> tuple[dict[str, Attribute], list[GeoIssue]]:
    """Explicit tags for every kind; inferred road widths. Building heights are
    estimated by ``onward_generator.heights``, not here."""
    attributes: dict[str, Attribute] = {}
    issues: list[GeoIssue] = []

    def observed(name: str, tag: str, parser) -> None:
        raw = tags.get(tag)
        if raw is None:
            return
        value = parser(raw)
        if value is None:
            issues.append(GeoIssue("unparseable_tag", "warning", f"{ref.key} tag {tag}={raw!r} is not a valid value", ref))
        else:
            attributes[name] = Attribute(value, AttributeOrigin.OBSERVED, source_tag=tag)

    if kind is FeatureKind.BUILDING:
        observed("height_m", "height", parse_length_m)
        observed("levels", "building:levels", parse_count)
    elif kind is FeatureKind.HIGHWAY:
        observed("width_m", "width", parse_length_m)
        observed("lanes", "lanes", parse_count)
        highway = tags.get("highway", "")
        if "width_m" not in attributes and highway in HIGHWAY_DEFAULT_WIDTH_M:
            attributes["width_m"] = Attribute(
                HIGHWAY_DEFAULT_WIDTH_M[highway],
                AttributeOrigin.INFERRED,
                rule=f"default width for highway={highway}",
                confidence=0.4,
            )
    return attributes, issues
