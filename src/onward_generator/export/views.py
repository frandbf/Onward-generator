"""Fixed eye-level views of a baked scene, to compare two bakes without the headset (plan E1, ADR-037).

``export views PACKAGE`` writes ``PACKAGE/build/views/views.json``; the adapter's ``views`` step opens the baked
scene and renders one PNG per view beside it. The package itself is not touched, and a views folder that exists is
never overwritten. The views come from the playable layout alone, so two packages of the same map, say ``neutral``
and ``golden_hour``, get the same cameras: each spawn looks at the other, and the Uplink looks at each spawn.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from onward_generator.contracts import is_schema
from onward_generator.io import atomic_write_json

VIEWS_SCHEMA = "onward_generator.capture_views"
VIEWS_VERSION = "0.1.0"
VIEWS_DIR = "views"
VIEWS_FILE = "views.json"
EYE_HEIGHT_M = 1.7
WIDTH_PX = 1920
HEIGHT_PX = 1080
# Vertical field of view: about 91 degrees across a 16:9 image, near what a Quest 3 shows.
FOV_DEG = 60.0
GAMEPLAY_SCHEMA = "onward_generator.gameplay_layout"


class ViewError(ValueError):
    """The layout gives no views, or the views folder already exists."""


def heading_deg(origin: Sequence[float], target: Sequence[float]) -> float:
    """Degrees clockwise from north, in [0, 360), from ``origin`` toward ``target`` (local metres, x east, y north)."""
    return round(math.degrees(math.atan2(target[0] - origin[0], target[1] - origin[1])) % 360.0, 2)


def _point(value: Any, what: str) -> tuple[float, float]:
    if not isinstance(value, Sequence) or len(value) != 2:
        raise ViewError(f"layout: {what} must be [x, y] in metres")
    return float(value[0]), float(value[1])


def spawn_heading(block: Mapping[str, Any]) -> float | None:
    """The circular mean of the ``yaw`` of a spawn block's points, the way its players face, or ``None`` without one.

    A point's ``yaw`` is Unity's: degrees clockwise from north, the heading of this file."""
    yaws = [float(p["yaw"]) for p in block.get("points_m") or [] if isinstance(p, Mapping) and "yaw" in p]
    if not yaws:
        return None
    east = sum(math.sin(math.radians(y)) for y in yaws)
    north = sum(math.cos(math.radians(y)) for y in yaws)
    return round(math.degrees(math.atan2(east, north)), 2) % 360.0


def capture_views(layout: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The views of a gameplay layout: both spawns, then the Uplink toward each spawn when the layout has one.

    A spawn view faces the way its players spawn (``spawn_heading``), so it shows what they see first; a block
    without yaws faces the other spawn. The Uplink views face each spawn."""
    if not is_schema(layout.get("schema"), GAMEPLAY_SCHEMA):
        raise ViewError("capture views need a gameplay layout: a perf layout has no spawns to look from")
    attackers = _point((layout.get("attackers") or {}).get("centre_m"), "attackers.centre_m")
    defenders = _point((layout.get("defenders") or {}).get("centre_m"), "defenders.centre_m")
    pairs = [("attackers", attackers, defenders), ("defenders", defenders, attackers)]
    if layout.get("uplink_m") is not None:
        uplink = _point(layout["uplink_m"], "uplink_m")
        pairs += [("uplink_to_attackers", uplink, attackers), ("uplink_to_defenders", uplink, defenders)]
    eye = float(layout.get("ground_z_m", 0.0)) + EYE_HEIGHT_M
    faced = {name: spawn_heading(layout.get(name) or {}) for name in ("attackers", "defenders")}
    views = []
    for name, origin, target in pairs:
        heading = faced.get(name)
        views.append({"name": name, "position_m": [origin[0], origin[1], round(eye, 3)],
                      "heading_deg": heading_deg(origin, target) if heading is None else heading, "pitch_deg": 0.0})
    return views


def views_document(layout: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": VIEWS_SCHEMA,
        "schema_version": VIEWS_VERSION,
        "frame": "local metres, x east, y north, z up; heading clockwise from north; Unity (x, z, y)",
        "width": WIDTH_PX,
        "height": HEIGHT_PX,
        "fov_deg": FOV_DEG,
        "views": capture_views(layout),
    }


def write_views(package: str | Path) -> Path:
    """Write ``build/views/views.json`` for ``package`` from its ``layout.json``; the folder must not exist yet."""
    root = Path(package).expanduser().resolve()
    layout_path = root / "layout.json"
    if not layout_path.is_file():
        raise ViewError(f"{root} has no layout.json; is it an export package?")
    folder = root / "build" / VIEWS_DIR
    if folder.exists():
        raise ViewError(f"{folder} exists; captures are never overwritten (run_export.sh archive clears the way)")
    document = views_document(json.loads(layout_path.read_bytes()))
    folder.mkdir(parents=True)
    atomic_write_json(folder / VIEWS_FILE, document)
    return folder / VIEWS_FILE


__all__ = ["EYE_HEIGHT_M", "FOV_DEG", "ViewError", "capture_views", "heading_deg", "spawn_heading", "views_document", "write_views"]
