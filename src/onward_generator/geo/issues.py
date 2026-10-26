"""Diagnostics emitted by the geographic pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from onward_generator.domain.models import OsmRef

Severity = Literal["error", "warning", "info"]


@dataclass(frozen=True, slots=True)
class GeoIssue:
    """A problem tied, when possible, to the original OSM element."""

    code: str
    severity: Severity
    message: str
    ref: OsmRef | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "element": self.ref.key if self.ref else None,
        }
