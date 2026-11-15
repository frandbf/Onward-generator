"""Smoke columns (15 November 2026, plan 2026-11-15-ambiente-belico, increment C).

``map.json`` ``"smoke": N`` (1 to ``SMOKE_MAX``) asks for N columns of smoke rising above the roofs; absent or 0
writes nothing, so the package is the one of before. Each column stands on a wreck of the playable core (a procedural
wreck or a library vehicle placed as one, pinned props included), at least ``SPAWN_CLEAR_M`` from every spawn point and
the Uplink, ``APART_M`` from the other columns and ``FOOT_CLEAR_M`` from a ladder or lift foot. The Unity adapter
draws each as a ``ParticleSystem`` with the SDK's ``PP_Particle_Simple_Lit`` shader and the package's
``smoke_puff.png``, a soft puff drawn here (CC0 by construction). Columns rise and thin out above the roofs: bots
see through particles, so there is no smoke at ground level to hide a player from a player only.
"""

from __future__ import annotations

import hashlib
import io
import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import numpy as np
from PIL import Image, ImageFilter

SMOKE_MAX = 5
SPAWN_CLEAR_M = 15.0
APART_M = 15.0
FOOT_CLEAR_M = 3.0
TEXTURE_FILE = "smoke_puff.png"
TEXTURE_ROLE = "smoke_texture"
TEXTURE_PX = 128
COLUMN = {
    "height_m": 16.0,
    "rate_per_s": 4.0,
    "lifetime_s": 10.0,
    "start_size_m": 2.0,
    "end_size_m": 8.0,
    "drift_m_s": 0.5,
    "color": "#4A4640",
    "alpha": 0.6,
    "max_particles": 40,
}
"""Every column's look: the adapter reads these numbers, never a name."""
SPAWN_KEYS = ("attackers", "defenders", "attackers_b", "defenders_b")
POINT_KEYS = ("uplink_m", "escort_a_m", "escort_b_m", "evac_m")


class SmokeError(ValueError):
    """``smoke`` is not an integer from 0 to ``SMOKE_MAX``."""


def check_count(value: object) -> int:
    """The number of columns asked for: an integer from 0 to ``SMOKE_MAX``; None is 0."""
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= SMOKE_MAX:
        raise SmokeError(f'map.json "smoke" must be an integer from 0 to {SMOKE_MAX}, got {value!r}')
    return value


def wreck_sites(objects: Iterable[Mapping[str, Any]]) -> list[tuple[str, float, float, float]]:
    """(id, x, y, z) of every wreck record: a procedural wreck, or a vehicle placed with the wreck role."""
    sites = []
    for record in objects:
        if record.get("type") != "overgrowth":
            continue
        kind, role = record.get("kind"), record.get("role")
        if kind == "wreck" or (kind == "vehicles" and role == "wreck"):
            x, y, *rest = record.get("position_m") or (0.0, 0.0)
            sites.append((str(record.get("id")), float(x), float(y), float(rest[0]) if rest else 0.0))
    return sites


def keep_out(layout: Mapping[str, Any]) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """The spawn and objective points, and the ladder and lift feet, of a layout."""
    points: list[tuple[float, float]] = []
    for key in SPAWN_KEYS:
        block = layout.get(key) or {}
        points += [(float(p["x"]), float(p["y"])) for p in block.get("points_m") or []]
    for key in POINT_KEYS:
        value = layout.get(key)
        if isinstance(value, Sequence) and len(value) >= 2 and not isinstance(value, str):
            points.append((float(value[0]), float(value[1])))
    feet = [(float(c["x"]), float(c["y"])) for key in ("ladders", "lifts") for c in layout.get(key) or []]
    return points, feet


def choose(sites: Sequence[tuple[str, float, float, float]], wanted: int, half_side_m: float | None,
           layout: Mapping[str, Any]) -> tuple[list[dict[str, float]], dict[str, int]]:
    """Up to ``wanted`` columns on ``sites``, in an order fixed by their ids, and the count of each refusal."""
    points, feet = keep_out(layout)
    refused = {"outside_core": 0, "near_spawn": 0, "near_climb": 0, "near_column": 0}
    chosen: list[dict[str, float]] = []
    for _site_id, x, y, z in sorted(sites, key=lambda s: hashlib.sha256(s[0].encode()).hexdigest()):
        if len(chosen) == wanted:
            break
        if half_side_m is not None and max(abs(x), abs(y)) > half_side_m:
            refused["outside_core"] += 1
        elif any(math.hypot(x - px, y - py) < SPAWN_CLEAR_M for px, py in points):
            refused["near_spawn"] += 1
        elif any(math.hypot(x - fx, y - fy) < FOOT_CLEAR_M for fx, fy in feet):
            refused["near_climb"] += 1
        elif any(math.hypot(x - c["x"], y - c["y"]) < APART_M for c in chosen):
            refused["near_column"] += 1
        else:
            chosen.append({"x": round(x, 3), "y": round(y, 3), "z": round(max(z, 0.0) + 1.0, 3), "seed": len(chosen) + 1})
    return chosen, refused


def puff_png(seed: int = 0) -> bytes:
    """A soft round puff of smoke, white with a noisy alpha that fades to the edge, deterministic."""
    rng = np.random.default_rng(seed)
    n = TEXTURE_PX
    yy, xx = np.mgrid[0:n, 0:n] / (n - 1) * 2.0 - 1.0
    radial = np.clip(1.0 - np.hypot(xx, yy), 0.0, 1.0) ** 1.6
    noise = np.zeros((n, n))
    for octave in (8, 16, 32):
        coarse = rng.uniform(0.0, 1.0, (octave, octave))
        noise += np.asarray(Image.fromarray((coarse * 255).astype(np.uint8)).resize((n, n), Image.Resampling.BICUBIC)) / 255.0 / (octave / 8)
    noise /= noise.max()
    alpha = Image.fromarray((np.clip(radial * (0.55 + 0.45 * noise), 0.0, 1.0) * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.5))
    pixels = np.zeros((n, n, 4), dtype=np.uint8)
    pixels[..., :3] = 255
    pixels[..., 3] = np.asarray(alpha)
    out = io.BytesIO()
    Image.fromarray(pixels).save(out, format="PNG")
    return out.getvalue()


def smoke_options(columns: Sequence[Mapping[str, float]]) -> dict[str, Any]:
    """The ``smoke`` block of ``adapter_options.json``."""
    return {"texture": TEXTURE_FILE, **COLUMN, "columns": [dict(c) for c in columns]}


__all__ = ["APART_M", "COLUMN", "FOOT_CLEAR_M", "SMOKE_MAX", "SPAWN_CLEAR_M", "TEXTURE_FILE", "TEXTURE_ROLE", "SmokeError",
           "check_count", "choose", "keep_out", "puff_png", "smoke_options", "wreck_sites"]
