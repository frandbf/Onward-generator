"""The report's file name and its one-line summary, without the geometry imports of ``gameplay.report``."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

REPORT_FILE = "gameplay_report.json"


def headline_text(report: Mapping[str, Any]) -> str:
    """``routes N  sight X m  balance Y %`` from the report's headline; ``-`` for a value that is missing or not a number."""
    headline = report.get("headline")
    values = headline if isinstance(headline, Mapping) else {}

    def show(key: str, unit: str = "") -> str:
        value = values.get(key)
        if isinstance(value, bool) or not isinstance(value, int | float):
            return "-"
        return f"{value:g}{unit}"

    return f"routes {show('distinct_routes')}  sight {show('longest_open_sight_m', ' m')}  balance {show('contact_diff_pct', ' %')}"
