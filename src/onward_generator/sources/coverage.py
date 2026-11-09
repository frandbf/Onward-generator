"""Static coverage tables of source plugins (ADR-018 §1): WGS84 rings in the repository, no network call.

A table is ``{"schema": "onward_generator.source_coverage", "polygons": [[outer, hole, ...], ...]}``,
each ring a list of ``[longitude, latitude]``. A point on a ring counts as covered.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

from shapely import Point, Polygon

from onward_generator.sources.plugin import SourceError

SCHEMA = "onward_generator.source_coverage"
REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = REPO_ROOT / "config"


@cache
def load_coverage(path: Path) -> tuple[Polygon, ...]:
    """The polygons of a coverage table, with their holes."""
    document = json.loads(path.read_bytes())
    if document.get("schema") != SCHEMA:
        raise SourceError(f"{path.name}: schema must be {SCHEMA}")
    polygons = document.get("polygons")
    if not isinstance(polygons, list) or not polygons:
        raise SourceError(f"{path.name}: polygons must be a non-empty list of rings")
    return tuple(Polygon(rings[0], rings[1:]) for rings in polygons)


def in_coverage(path: Path, longitude: float, latitude: float) -> bool:
    """True when the point lies inside, or on the edge of, a polygon of the table at ``path``."""
    point = Point(longitude, latitude)
    return any(polygon.covers(point) for polygon in load_coverage(path))
