"""Atmosphere presets: the baked light of a day map (ADR-037, proposed).

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
from typing import Any

from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[3]
PRESETS_DEFAULT = REPO_ROOT / "config" / "atmosphere.default.json"
PRESETS_SCHEMA = REPO_ROOT / "config" / "atmosphere.schema.json"
NEUTRAL = "neutral"


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


__all__ = ["NEUTRAL", "PRESETS_DEFAULT", "AtmosphereError", "check_atmosphere", "load_presets", "preset_names"]
