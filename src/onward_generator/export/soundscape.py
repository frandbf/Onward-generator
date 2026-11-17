"""Soundscape by mood (17 November 2026, plan 2026-11-17-efectos-sdk, increment D; ADR-046).

``map.json`` ``"soundscape"`` is ``auto`` (the default), ``default``, ``regional``, ``war`` or ``battle``. ``auto``
reads the map's war damage (``signs.damage``): ``heavy`` is ``war``, anything else ``regional``. ``default`` keeps the
sound of before. ``regional`` swaps the birds and the roof wind for the clips of the map's facade style
(``config/soundscape.json`` ``regional``) and sets an engine generator by a power pole. ``war`` and ``battle`` add
distant combat as the global ambience, fire under each smoke column and flies over wrecks and rubbish.

Every clip is one the Onward client ships (``OnwardAudioDataStrings`` of the SDK), named by the configuration and
checked by its schema: nothing is added to the package, no licence to record. ``adapter_options.json`` gains a
``soundscape`` block only when the mode changes something, so a map in a style without a row, without poles and
without war keeps its package id.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match

from onward_generator.export.smoke import keep_out, wreck_sites

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = REPO_ROOT / "config" / "soundscape.json"
SCHEMA_PATH = REPO_ROOT / "config" / "soundscape.schema.json"
DEFAULT = "default"
AUTO = "auto"
MODES = (DEFAULT, "regional", "war", "battle")
"""The modes ``export package --soundscape`` takes; ``map.json`` takes ``auto`` too."""
CHOICES = (AUTO, *MODES)
LOCAL_KINDS = ("fire", "flies", "generator")


class SoundscapeError(ValueError):
    """A bad ``soundscape`` value or configuration."""


@dataclass(frozen=True)
class Source:
    """One kind of local source: its clip, how many, how far apart, how far from the spawns, how high."""

    clip: str
    max: int
    apart_m: float
    spawn_clear_m: float
    height_m: float


@dataclass(frozen=True)
class SoundConfig:
    """The checked ``config/soundscape.json``."""

    globals_: Mapping[str, str | None]
    locals_: Mapping[str, tuple[str, ...]]
    regional: Mapping[str, tuple[str | None, str | None]]
    sources: Mapping[str, Source]
    flies_kinds: frozenset[str]


def parse_soundscape(doc: object, schema: Mapping[str, Any], where: str) -> SoundConfig:
    """The configuration, after its schema."""
    first = best_match(Draft202012Validator(schema).iter_errors(doc))
    if first is not None:
        path = "/".join(str(part) for part in first.absolute_path)
        raise SoundscapeError(f"{where}: {path}: {first.message}" if path else f"{where}: {first.message}")
    assert isinstance(doc, dict)  # the schema requires an object  # pragma: no mutate: narrows the type only
    return SoundConfig(
        globals_={name: mode["global"] for name, mode in doc["modes"].items()},
        locals_={name: tuple(mode["local"]) for name, mode in doc["modes"].items()},
        regional={style: (row["birds"], row["wind"]) for style, row in doc["regional"].items()},
        sources={name: Source(str(row["clip"]), int(row["max"]), float(row["apart_m"]), float(row["spawn_clear_m"]),
                              float(row["height_m"])) for name, row in doc["local"].items()},
        flies_kinds=frozenset(doc["flies_kinds"]),
    )


@cache
def load_soundscape(path: Path = CONFIG_PATH) -> SoundConfig:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SoundscapeError(f"{path}: {exc}") from exc
    return parse_soundscape(doc, schema, str(path))


def check_choice(value: object) -> str:
    """A ``map.json`` ``soundscape`` value; None is ``auto``."""
    if value is None:
        return AUTO
    if value not in CHOICES:
        raise SoundscapeError(f'map.json "soundscape" must be one of {", ".join(CHOICES)}, got {value!r}')
    return str(value)


def resolve_mode(choice: str, damage: str) -> str:
    """The mode of a checked choice: ``auto`` is ``war`` on a heavily damaged map, else ``regional``."""
    if choice == AUTO:
        return "war" if damage == "heavy" else "regional"
    return choice


def _ordered(sites: Iterable[tuple[str, float, float, float]]) -> list[tuple[str, float, float, float]]:
    return sorted(sites, key=lambda s: hashlib.sha256(s[0].encode()).hexdigest())


def pick(sites: Iterable[tuple[str, float, float, float]], source: Source, half_side_m: float | None,
         spawns: Sequence[tuple[float, float]]) -> list[tuple[float, float, float]]:
    """Up to ``source.max`` sites inside the core, clear of the spawns and of each other, in an order fixed by ids."""
    chosen: list[tuple[float, float, float]] = []
    for _site_id, x, y, z in _ordered(sites):
        if len(chosen) == source.max:
            break
        inside = half_side_m is None or max(abs(x), abs(y)) <= half_side_m
        clear = all(math.hypot(x - px, y - py) >= source.spawn_clear_m for px, py in spawns)
        apart = all(math.hypot(x - cx, y - cy) >= source.apart_m for cx, cy, _ in chosen)
        if inside and clear and apart:
            chosen.append((x, y, z))
    return chosen


def flies_sites(objects: Iterable[Mapping[str, Any]], kinds: frozenset[str]) -> list[tuple[str, float, float, float]]:
    """(id, x, y, z) of the wrecks and of every overgrowth record of a listed kind."""
    records = list(objects)
    sites = wreck_sites(records)
    for record in records:
        position = record.get("position_m")
        if record.get("type") == "overgrowth" and record.get("kind") in kinds - {"wreck"} and position:
            x, y, *rest = position
            sites.append((str(record.get("id")), float(x), float(y), float(rest[0]) if rest else 0.0))
    return sites


def pole_sites(objects: Iterable[Mapping[str, Any]]) -> list[tuple[str, float, float, float]]:
    """(id, x, y, z) of every power pole (ADR-045)."""
    sites = []
    for record in objects:
        position = record.get("position_m")
        if record.get("kind") == "power_pole" and position:
            x, y, *rest = position
            sites.append((str(record.get("id")), float(x), float(y), float(rest[0]) if rest else 0.0))
    return sites


def soundscape_block(mode: str, style: str, config: SoundConfig, objects: Sequence[Mapping[str, Any]],
               columns: Sequence[Mapping[str, float]], half_side_m: float | None,
               layout: Mapping[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """The ``adapter_options.json`` block (None when the mode changes nothing) and the manifest record."""
    if mode not in MODES:
        raise SoundscapeError(f"mode must be one of {', '.join(MODES)}, got {mode!r}")
    record: dict[str, Any] = {"mode": mode, "style": style}
    if mode == DEFAULT:
        return None, record
    birds, wind = config.regional.get(style, (None, None))
    spawns, _feet = keep_out(layout)
    sites = {
        "fire": [(f"smoke/{int(c['seed'])}", float(c["x"]), float(c["y"]), float(c["z"])) for c in columns],
        "flies": flies_sites(objects, config.flies_kinds),
        "generator": pole_sites(objects),
    }
    sources = []
    counts: dict[str, int] = {}
    for kind in config.locals_[mode]:
        source = config.sources[kind]
        chosen = pick(sites[kind], source, half_side_m, spawns)
        counts[kind] = len(chosen)
        sources += [{"name": f"{kind.capitalize()}_{i + 1}", "clip": source.clip, "x": round(x, 3), "y": round(y, 3),
                     "z": round(z + source.height_m, 3)} for i, (x, y, z) in enumerate(chosen)]
    combat = config.globals_[mode]
    record.update({"global": combat, "birds": birds, "wind": wind, "sources": counts})
    if combat is None and birds is None and wind is None and not sources:
        return None, record
    return {"mode": mode, "global": combat, "birds": birds, "wind": wind, "sources": sources}, record


__all__ = ["AUTO", "CHOICES", "DEFAULT", "LOCAL_KINDS", "MODES", "SoundConfig", "SoundscapeError", "Source",
           "check_choice", "flies_sites", "load_soundscape", "parse_soundscape", "pick", "pole_sites", "resolve_mode",
           "soundscape_block"]
