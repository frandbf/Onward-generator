"""Validation diagnostics that identify the offending field and the cause."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

Severity = Literal["error", "warning", "info"]


@dataclass(frozen=True, slots=True)
class Diagnostic:
    severity: Severity
    code: str
    path: str
    """JSON pointer of the field (``/area/margin_m``); empty for the whole document."""
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"severity": self.severity, "code": self.code, "path": self.path, "message": self.message}

    def __str__(self) -> str:
        return f"{self.severity.upper():7} {self.path or '/'}: {self.message} [{self.code}]"


def has_errors(diagnostics: Iterable[Diagnostic]) -> bool:
    return any(d.severity == "error" for d in diagnostics)
