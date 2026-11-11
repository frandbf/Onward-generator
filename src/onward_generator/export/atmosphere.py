"""Atmosphere presets: the baked light of a day map (ADR-037).

A map names one preset in ``map.json`` (``atmosphere``). ``neutral``, or no key at all, is today's light and
changes nothing, down to the package id. The presets themselves are numbers in ``config/atmosphere.default.json``;
the Unity adapter never sees a preset name. A preset is a day setting: a night map refuses every one but ``neutral``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from functools import cache
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from jsonschema import Draft202012Validator

if TYPE_CHECKING:
    from onward_generator.export.sun import StreetSun

REPO_ROOT = Path(__file__).resolve().parents[3]
PRESETS_DEFAULT = REPO_ROOT / "config" / "atmosphere.default.json"
PRESETS_SCHEMA = REPO_ROOT / "config" / "atmosphere.schema.json"
NEUTRAL = "neutral"
# The neutral light points toward heading -30 (Quaternion.Euler(50, -30, 0)), so its sun stands at 150 degrees.
NEUTRAL_SUN_AZIMUTH_DEG = 150.0
STREETS = "streets"
# The parts of a preset the package carries, in the order the plan added them (E3 the sun, E5 the ambient and the AO,
# E7 the colour of the fog, only when the map's card has its fog on).
APPLIED = ("sun", "ambient", "ao")
FOG = "fog"
# E10: the playable core bakes at the preset's density, only with a core, combining and a lower card density.
LIGHTMAP_CORE = "lightmap_core"


class AtmosphereError(ValueError):
    """The preset is unknown, the presets file is invalid, or the map is a night map."""


@cache
def _validator() -> Draft202012Validator:
    schema = json.loads(PRESETS_SCHEMA.read_bytes())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def load_presets(path: str | Path | None = None) -> Mapping[str, Mapping[str, Any]]:
    """Every preset of the presets file, by name, after schema validation."""
    source = Path(path) if path is not None else PRESETS_DEFAULT
    try:
        document = json.loads(source.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise AtmosphereError(f"{source}: {exc}") from exc
    errors = sorted(_validator().iter_errors(document), key=lambda e: list(map(str, e.absolute_path)))
    if errors:
        first = errors[0]
        raise AtmosphereError(f"{source}: /{'/'.join(map(str, first.absolute_path))}: {first.message}")
    return MappingProxyType(document["presets"])


def preset_names(path: str | Path | None = None) -> tuple[str, ...]:
    """``neutral`` first, then the presets of the file in name order."""
    return (NEUTRAL, *sorted(load_presets(path)))


def check_atmosphere(name: str, time_of_day: str, path: str | Path | None = None) -> str:
    """``name`` when it is ``neutral`` or a preset of the file and the map is a day map."""
    names = preset_names(path)
    if name not in names:
        raise AtmosphereError(f"atmosphere must be one of {', '.join(names)}, not {name!r}")
    if name != NEUTRAL and time_of_day != "day":
        raise AtmosphereError(f"atmosphere {name} needs a day map: a preset sets the sun, and a night map has the moon (ADR-037)")
    return name


def lighting(preset: Mapping[str, Any], street: StreetSun | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """The ``lighting`` block of ``adapter_options.json`` for ``preset``, and what the manifest records of the sun.

    The block holds the sun (plan E3), the ambient (``trilight`` with sky, equator and ground colours, or ``flat`` with
    the sky colour alone) and the baked ambient occlusion (plan E5).

    An ``azimuth: streets`` preset takes the azimuth of ``street`` (plan E6, ``export.sun``): ``azimuth_source`` is
    ``streets``, or ``streets_none`` when the core has no wall and the neutral azimuth stays. Without ``street`` it
    keeps the neutral azimuth as ``streets_pending``."""
    sun = preset["sun"]
    record: dict[str, Any] = {"elevation_deg": float(sun["elevation_deg"])}
    if sun["azimuth"] != STREETS:
        azimuth, record["azimuth_source"] = float(sun["azimuth"]), "preset"
    elif street is None:
        azimuth, record["azimuth_source"] = NEUTRAL_SUN_AZIMUTH_DEG, "streets_pending"
    else:
        azimuth = street.azimuth_deg
        record["azimuth_source"] = "streets" if street.axis_deg is not None else "streets_none"
        record["axis_deg"] = street.axis_deg
        record["wall_area_m2"] = street.wall_area_m2
    record["azimuth_deg"] = azimuth
    block = {
        "sun_elevation_deg": record["elevation_deg"],
        "sun_azimuth_deg": azimuth,
        "sun_color": str(sun["color"]),
        "sun_intensity": float(sun["intensity"]),
        "sun_shadow_angle_deg": float(sun["shadow_angle_deg"]),
    }
    ambient, ao = preset["ambient"], preset["ao"]
    block["ambient_mode"] = str(ambient["mode"])
    block["ambient_sky_color"] = str(ambient["sky"])
    if ambient["mode"] == "trilight":
        block["ambient_equator_color"] = str(ambient["equator"])
        block["ambient_ground_color"] = str(ambient["ground"])
    block["ao_max_distance_m"] = float(ao["max_distance_m"])
    block["ao_exponent_indirect"] = float(ao["exponent_indirect"])
    block["ao_exponent_direct"] = float(ao["exponent_direct"])
    return block, record


__all__ = ["APPLIED", "FOG", "LIGHTMAP_CORE", "NEUTRAL", "NEUTRAL_SUN_AZIMUTH_DEG", "STREETS", "lighting", "PRESETS_DEFAULT", "AtmosphereError", "check_atmosphere", "load_presets", "preset_names"]
