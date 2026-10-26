"""Domain entities shared by every layer.

These types do not depend on GIS libraries. Geographic coordinates are always
WGS84 ``(longitude, latitude)`` in degrees, following RFC 7946 order.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

OsmType = Literal["node", "way", "relation"]
_OSM_TYPES: frozenset[str] = frozenset({"node", "way", "relation"})
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class AttributeOrigin(StrEnum):
    """Where an attribute value comes from."""

    OBSERVED = "observed"
    """Read verbatim from an explicit source tag."""
    INFERRED = "inferred"
    """Derived by a deterministic, named rule from other data."""
    GENERATED = "generated"
    """Produced by a procedural or AI method; never a geographic fact."""


@dataclass(frozen=True, slots=True)
class OsmRef:
    """Identity of an original OpenStreetMap element."""

    osm_type: OsmType
    osm_id: int

    def __post_init__(self) -> None:
        if self.osm_type not in _OSM_TYPES:
            raise ValueError(f"invalid OSM type: {self.osm_type!r}")
        if isinstance(self.osm_id, bool) or not isinstance(self.osm_id, int) or self.osm_id == 0:
            raise ValueError(f"invalid OSM id: {self.osm_id!r}")

    @property
    def key(self) -> str:
        return f"osm-{self.osm_type}-{self.osm_id}"

    def to_dict(self) -> dict[str, Any]:
        return {"osm_type": self.osm_type, "osm_id": self.osm_id}


@dataclass(frozen=True, slots=True)
class Attribute:
    """A typed attribute value with explicit provenance."""

    value: float | int | str
    origin: AttributeOrigin
    source_tag: str | None = None
    rule: str | None = None
    confidence: float | None = None

    def __post_init__(self) -> None:
        if isinstance(self.value, float) and not math.isfinite(self.value):
            raise ValueError("attribute value must be finite")
        if self.origin is AttributeOrigin.OBSERVED:
            if not self.source_tag:
                raise ValueError("observed attributes require source_tag")
            if self.rule is not None:
                raise ValueError("observed attributes cannot carry an inference rule")
        elif not self.rule:
            raise ValueError(f"{self.origin.value} attributes require a rule")
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be within [0, 1]")

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"value": self.value, "origin": self.origin.value}
        if self.source_tag is not None:
            data["source_tag"] = self.source_tag
        if self.rule is not None:
            data["rule"] = self.rule
        if self.confidence is not None:
            data["confidence"] = self.confidence
        return data


@dataclass(frozen=True, slots=True)
class SourceDataset:
    """Provenance record of an immutable input file."""

    provider: str
    license: str
    attribution: str
    path: str
    sha256: str
    size_bytes: int
    registered_at: datetime
    retrieved_at: datetime
    retrieved_at_origin: Literal["declared", "file_mtime"]
    license_url: str | None = None
    data_timestamp: str | None = None

    def __post_init__(self) -> None:
        for name in ("provider", "license", "attribution", "path"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be empty")
        if not _SHA256_RE.match(self.sha256):
            raise ValueError("sha256 must be 64 lowercase hex characters")
        if self.size_bytes < 0:
            raise ValueError("size_bytes must be non-negative")
        for name in ("registered_at", "retrieved_at"):
            if getattr(self, name).tzinfo is None:
                raise ValueError(f"{name} must be timezone-aware")
        if self.retrieved_at_origin not in ("declared", "file_mtime"):
            raise ValueError("retrieved_at_origin must be 'declared' or 'file_mtime'")

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "license": self.license,
            "license_url": self.license_url,
            "attribution": self.attribution,
            "path": self.path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "registered_at": self.registered_at.isoformat(),
            "retrieved_at": self.retrieved_at.isoformat(),
            "retrieved_at_origin": self.retrieved_at_origin,
            "data_timestamp": self.data_timestamp,
        }


def validate_lon_lat(lon: float, lat: float) -> None:
    """Reject values that cannot be WGS84 longitude/latitude degrees."""
    if not (math.isfinite(lon) and math.isfinite(lat)):
        raise ValueError(f"non-finite coordinate: ({lon}, {lat})")
    if not -180.0 <= lon <= 180.0:
        raise ValueError(f"longitude out of range: {lon}")
    if not -90.0 <= lat <= 90.0:
        raise ValueError(f"latitude out of range: {lat}")


@dataclass(frozen=True, slots=True)
class AreaOfInterest:
    """Playable area as a WGS84 polygon plus an inclusion margin in metres."""

    name: str
    polygon: tuple[tuple[float, float], ...]
    margin_m: float
    crs: str = "EPSG:25830"

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("area name must not be empty")
        if len(self.polygon) < 4:
            raise ValueError("area polygon needs at least 4 positions (closed ring)")
        if self.polygon[0] != self.polygon[-1]:
            raise ValueError("area polygon ring must be closed")
        for lon, lat in self.polygon:
            validate_lon_lat(lon, lat)
        if not math.isfinite(self.margin_m) or self.margin_m < 0:
            raise ValueError("margin_m must be a finite, non-negative number of metres")
        if not self.crs.strip():
            raise ValueError("crs must not be empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "polygon_wgs84": [list(p) for p in self.polygon],
            "margin_m": self.margin_m,
            "crs": self.crs,
        }
