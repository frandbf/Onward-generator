"""Fire and embers under the smoke (17 November 2026, plan 2026-11-17-efectos-sdk, increment E; ADR-043 amendment).

``map.json`` ``"fire": true`` lights small flames and rising embers on the wreck under every smoke column (ADR-043), so
the smoke has a source. Absent or ``false`` writes nothing, and without a smoke column there is nothing to burn under:
the package is the one of before and a warning says why. The Unity adapter draws two ``ParticleSystem`` per column on
the SDK's ``PP_Particle_Unlit``, unlit (the fire is its own light) and with no real-time light, a flicker
cannot be baked. Their sprite, ``fire_flame.png``, is a soft upright flame drawn here: CC0 by construction. The flames
are small and low: they do not hide a player from a player, as bots see through particles.
"""

from __future__ import annotations

import io
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
from PIL import Image

TEXTURE_FILE = "fire_flame.png"
TEXTURE_ROLE = "fire_texture"
TEXTURE_PX = 64
FLAMES = {
    "rate_per_s": 20.0,
    "lifetime_s": 0.6,
    "min_size_m": 0.4,
    "max_size_m": 1.0,
    "end_scale": 0.3,
    "speed_m_s": 1.5,
    "cone_deg": 12.0,
    "radius_m": 0.4,
    "start_color": "#FFC860",
    "end_color": "#FF3C0A",
    "max_particles": 16,
}
"""Every column's flames: the adapter reads these numbers, never a name."""
EMBERS = {
    "rate_per_s": 3.0,
    "lifetime_s": 2.5,
    "min_size_m": 0.04,
    "max_size_m": 0.07,
    "end_scale": 0.5,
    "speed_m_s": 1.8,
    "cone_deg": 25.0,
    "radius_m": 0.5,
    "start_color": "#FFA040",
    "end_color": "#FF3000",
    "max_particles": 8,
}
"""Every column's embers: sparks that rise past the flames and go out."""
CORE_RGB = np.array([255.0, 224.0, 128.0])
"""The flame's colour in its low core."""
EDGE_RGB = np.array([255.0, 84.0, 16.0])
"""Its colour at the edges and the tip."""
ALPHA_PEAK = 0.9
"""Its alpha where it is brightest, before the shape's falloff."""
BASE_RISE_M = 0.6
"""The fire stands this far above the smoke column's foot (1 m over the wreck's base): on a 1.55 m wreck's roof."""


class FireError(ValueError):
    """``fire`` is not true or false."""


def check_fire(value: object) -> bool:
    """A ``map.json`` ``fire`` value: True or False; None is False."""
    if value is None:
        return False
    if not isinstance(value, bool):
        raise FireError(f'map.json "fire" must be true or false, got {value!r}')
    return value


def flame_png() -> bytes:
    """A soft upright flame: a yellow core that turns orange-red towards its edges and tip, with an alpha bright low
    down that narrows and fades towards the tip. The sprite carries the colour itself; the particles' colour shades it over
    their life."""
    n = TEXTURE_PX
    yy, xx = np.mgrid[0:n, 0:n] / (n - 1) * 2.0 - 1.0  # y grows downwards: the tip is at the top
    rise = (1.0 - yy) / 2.0  # 0 at the foot, 1 at the tip
    width = 0.75 * (1.0 - rise) ** 0.8 + 0.05
    across = np.maximum(1.0 - (xx / width) ** 2, 0.0)  # at most 1: (xx / width) ** 2 is not negative
    along = (1.0 - rise) ** 0.6 * np.minimum(rise * 6.0, 1.0)  # rise lies in [0, 1]
    alpha = across * along * ALPHA_PEAK
    heat = (across * (1.0 - rise))[..., None]  # in [0, 1]: 1 in the low core, 0 at the edges and the tip
    colour = EDGE_RGB + (CORE_RGB - EDGE_RGB) * heat
    pixels = np.empty((n, n, 4), dtype=np.uint8)
    pixels[..., :3] = np.round(colour)  # the uint8 page casts it
    pixels[..., 3] = np.round(alpha * 255)
    out = io.BytesIO()
    Image.fromarray(pixels).save(out, format="PNG")
    return out.getvalue()


def fire_options(columns: Sequence[Mapping[str, float]]) -> dict[str, Any]:
    """The ``fire`` block of ``adapter_options.json``: the look in numbers and a site under each smoke column."""
    return {
        "texture": TEXTURE_FILE,
        "flames": dict(FLAMES),
        "embers": dict(EMBERS),
        "sites": [{"x": float(c["x"]), "y": float(c["y"]), "z": round(float(c["z"]) + BASE_RISE_M, 3), "seed": int(c["seed"])}
                  for c in columns],
    }


__all__ = ["ALPHA_PEAK", "BASE_RISE_M", "CORE_RGB", "EDGE_RGB", "EMBERS", "FLAMES", "TEXTURE_FILE", "TEXTURE_PX", "TEXTURE_ROLE", "FireError", "check_fire",
           "fire_options", "flame_png"]
