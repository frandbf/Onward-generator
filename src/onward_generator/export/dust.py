"""Dust in the air (17 November 2026, plan 2026-11-17-efectos-sdk, increment F; ADR-047).

``map.json`` ``"dust"`` is ``auto`` (the default), ``light`` or ``off``. ``auto`` is ``light`` on a map whose light preset
or facade style ``config/dust.json`` lists (``dusty``; ``arid``, ``east_african``), else ``off``; the key always wins.
With dust, a few emission volumes stand over the map's AI routes inside the playable core, apart from each other, and the
Unity adapter fills each with fine motes that drift with one wind across the map: ``ParticleSystem`` boxes on the SDK's
dithered ``PP_Particle_UnLit_Dither``, opaque with a dithered cut-out, so no transparency is blended. Their sprite,
``dust_mote.png``, is a soft dot drawn here (CC0 by construction). The motes are a few centimetres across and hide no
one: bots see through particles, and gusts of sand at eye level, which would hide a player from a player only, are left
out.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

import numpy as np
from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match
from PIL import Image

from onward_generator.export.smoke import keep_out

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = REPO_ROOT / "config" / "dust.json"
SCHEMA_PATH = REPO_ROOT / "config" / "dust.schema.json"
AUTO = "auto"
LIGHT = "light"
OFF = "off"
CHOICES = (AUTO, LIGHT, OFF)
TEXTURE_FILE = "dust_mote.png"
TEXTURE_ROLE = "dust_texture"
TEXTURE_PX = 32


class DustError(ValueError):
    """A bad ``dust`` value or configuration."""


@dataclass(frozen=True)
class DustConfig:
    """The checked ``config/dust.json``."""

    presets: frozenset[str]
    styles: frozenset[str]
    volumes: int
    apart_m: float
    spawn_clear_m: float
    look: Mapping[str, Any]
    """What the adapter reads: the box, the floor, the rate, the life, the sizes, the drift, the colour, the cap."""


_LOOK = ("box_m", "base_m", "rate_per_s", "lifetime_s", "min_size_m", "max_size_m", "drift_m_s", "color", "max_particles")


def parse_dust(doc: object, schema: Mapping[str, Any], where: str) -> DustConfig:
    """The configuration, after its schema; the largest mote may not be smaller than the smallest."""
    first = best_match(Draft202012Validator(schema).iter_errors(doc))
    if first is not None:
        path = "/".join(str(part) for part in first.absolute_path)
        raise DustError(f"{where}: {path}: {first.message}" if path else f"{where}: {first.message}")
    assert isinstance(doc, dict)  # the schema requires an object  # pragma: no mutate: narrows the type only
    if doc["max_size_m"] < doc["min_size_m"]:
        raise DustError(f"{where}: max_size_m {doc['max_size_m']} is smaller than min_size_m {doc['min_size_m']}")
    return DustConfig(presets=frozenset(doc["presets"]), styles=frozenset(doc["styles"]), volumes=int(doc["volumes"]),
                      apart_m=float(doc["apart_m"]), spawn_clear_m=float(doc["spawn_clear_m"]),
                      look={key: doc[key] for key in _LOOK})


@cache
def load_dust(path: Path = CONFIG_PATH) -> DustConfig:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DustError(f"{path}: {exc}") from exc
    return parse_dust(doc, schema, str(path))


def check_choice(value: object) -> str:
    """A ``map.json`` ``dust`` value; None is ``auto``."""
    if value is None:
        return AUTO
    if value not in CHOICES:
        raise DustError(f'map.json "dust" must be one of {", ".join(CHOICES)}, got {value!r}')
    return str(value)


def dust_wanted(choice: str, preset: str | None, style: str, config: DustConfig) -> bool:
    """Whether the map gets dust: ``light`` or ``off`` as asked, ``auto`` by its light preset or facade style."""
    if choice == AUTO:
        return preset in config.presets or style in config.styles
    return choice == LIGHT


def route_points(layout: Mapping[str, Any]) -> list[tuple[float, float]]:
    """Every point of the layout's AI routes, which follow the walkable streets."""
    return [(float(p["x"]), float(p["y"])) for route in layout.get("routes") or [] for p in route.get("points") or []]


def _key(point: tuple[float, float]) -> str:
    return hashlib.sha256(f"{point[0]:.2f},{point[1]:.2f}".encode()).hexdigest()


def choose_volumes(points: Iterable[tuple[float, float]], config: DustConfig, half_side_m: float | None,
                   layout: Mapping[str, Any]) -> list[dict[str, float]]:
    """Up to ``config.volumes`` centres on route points inside the core, clear of the spawns and apart from each
    other, in an order fixed by the points. A volume's centre stands half its height over its floor, ``base_m`` over
    the layout's ground."""
    spawns, _feet = keep_out(layout)
    centre_z = float(layout.get("ground_z_m") or 0.0) + float(config.look["base_m"]) + float(config.look["box_m"][2]) / 2
    chosen: list[dict[str, float]] = []
    for x, y in sorted(set(points), key=_key):
        if len(chosen) == config.volumes:
            break
        inside = half_side_m is None or max(abs(x), abs(y)) <= half_side_m
        clear = all(math.hypot(x - px, y - py) >= config.spawn_clear_m for px, py in spawns)
        apart = all(math.hypot(x - c["x"], y - c["y"]) >= config.apart_m for c in chosen)
        if inside and clear and apart:
            chosen.append({"x": round(x, 3), "y": round(y, 3), "z": round(centre_z, 3), "seed": len(chosen) + 1})
    return chosen


def wind_heading(map_id: str) -> float:
    """The one heading, clockwise from north, the motes of a map drift towards: fixed by its id."""
    return float(int(hashlib.sha256(map_id.encode()).hexdigest()[:8], 16) % 360)


def mote_png() -> bytes:
    """A soft round mote: white, with an alpha that falls from the centre to the edge."""
    n = TEXTURE_PX
    yy, xx = np.mgrid[0:n, 0:n] / (n - 1) * 2.0 - 1.0
    alpha = np.maximum(1.0 - np.hypot(xx, yy), 0.0) ** 1.5  # at most 1: hypot is not negative
    pixels = np.full((n, n, 4), 255, dtype=np.uint8)
    pixels[..., 3] = np.round(alpha * 255)
    out = io.BytesIO()
    Image.fromarray(pixels).save(out, format="PNG")
    return out.getvalue()


def dust_options(volumes: Sequence[Mapping[str, float]], config: DustConfig, colour: str | None, heading_deg: float) -> dict[str, Any]:
    """The ``dust`` block of ``adapter_options.json``: the look in numbers, the wind and the volumes. ``colour`` is the
    light preset's fog colour, else the configuration's."""
    return {"texture": TEXTURE_FILE, **config.look, "color": colour or config.look["color"], "heading_deg": heading_deg,
            "volumes": [dict(v) for v in volumes]}


__all__ = ["AUTO", "CHOICES", "LIGHT", "OFF", "TEXTURE_FILE", "TEXTURE_PX", "TEXTURE_ROLE", "DustConfig", "DustError",
           "check_choice", "choose_volumes", "dust_options", "dust_wanted", "load_dust", "mote_png", "parse_dust",
           "route_points", "wind_heading"]
