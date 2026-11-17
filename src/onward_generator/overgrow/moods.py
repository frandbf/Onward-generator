"""Street moods: what overgrow and signs add on top of a map's real geometry, by the state of its streets (ADR-044).

``overgrow`` was written for an abandoned or fought-over city: vines, wild trees and car wrecks scaled by a year
profile, and props of any condition. A street in daily use, or a town emptied days ago, needs none of that.
``config/street_moods.json`` names the moods as data: each one scales the year profile's vines, planted trees and
wrecks, keeps library props of the listed conditions, scales the rule caps of a prop category, and leaves wall decal
kinds out, and may set café terraces before the bars. A map names one with ``map.json`` ``"street_mood"``; absent is the table's default, ``abandoned``, which
is the identity: a map without the key builds exactly what it built before (16 November 2026).

Mapped trees are never removed: a mood scales only the trees overgrow plants where OSM maps none.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from functools import cache
from pathlib import Path
from types import MappingProxyType
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match

from onward_generator.overgrow.library import Prop
from onward_generator.overgrow.profiles import Profile

REPO_ROOT = Path(__file__).resolve().parents[3]
MOODS_PATH = REPO_ROOT / "config" / "street_moods.json"
SCHEMA_PATH = REPO_ROOT / "config" / "street_moods.schema.json"


class MoodError(ValueError):
    """An unknown street mood, or a ``config/street_moods.json`` that is not valid."""


@dataclass(frozen=True, slots=True)
class Mood:
    id: str
    vines: float
    planted_trees: float
    wrecks: float
    conditions: frozenset[str] | None
    categories: Mapping[str, float]
    wall_decals_excluded: frozenset[str]
    terraces: bool = False
    """Café tables before the bars of pedestrian streets and plazas (``overgrow/terraces.py``, 16 November 2026)."""
    awnings: bool = False
    """Striped awnings under the shops' fascia signs (``overgrow/awnings.py``, 16 November 2026)."""
    cover_roles: frozenset[str] | None = None
    """The roles ``perf cover`` may stand on a long sightline; None keeps its own (17 November 2026)."""
    cover_conditions: frozenset[str] | None = None
    """The conditions of those cover props; a prop without one is kept; None keeps every condition."""


@dataclass(frozen=True, slots=True)
class MoodTable:
    default: str
    moods: Mapping[str, Mood]


def parse_moods(doc: object, schema: Mapping[str, Any], where: str = "street_moods") -> MoodTable:
    """The checked table: the schema, and a default that names one of its moods."""
    first = best_match(Draft202012Validator(schema).iter_errors(doc))
    if first is not None:
        path = "/".join(str(part) for part in first.absolute_path)
        raise MoodError(f"{where}: {path}: {first.message}" if path else f"{where}: {first.message}")
    assert isinstance(doc, dict)  # the schema requires an object  # pragma: no mutate: narrows the type only
    moods = {
        name: Mood(
            id=name,
            vines=float(body["vines"]),
            planted_trees=float(body["planted_trees"]),
            wrecks=float(body["wrecks"]),
            conditions=frozenset(body["conditions"]) if body["conditions"] is not None else None,
            categories=MappingProxyType({str(k): float(v) for k, v in body["categories"].items()}),
            wall_decals_excluded=frozenset(body["wall_decals_excluded"]),
            terraces=bool(body["terraces"]),
            awnings=bool(body["awnings"]),
            cover_roles=frozenset(body["cover"]["roles"]) if body["cover"] is not None else None,
            cover_conditions=_conditions(body["cover"]["conditions"]) if body["cover"] is not None else None,
        )
        for name, body in doc["moods"].items()
    }
    if doc["default"] not in moods:
        raise MoodError(f"{where}: default {doc['default']!r} is not one of its moods: {sorted(moods)}")
    return MoodTable(str(doc["default"]), MappingProxyType(moods))


def _conditions(value: list[str] | None) -> frozenset[str] | None:
    return frozenset(value) if value is not None else None


@cache
def load_moods(path: Path = MOODS_PATH) -> MoodTable:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise MoodError(f"{path}: {exc}") from exc
    return parse_moods(doc, schema, str(path))


def mood_for(name: str | None, table: MoodTable | None = None) -> Mood:
    """The named mood, or the table's default for None; an unknown name is refused with the names it knows."""
    moods = table or load_moods()
    key = moods.default if name is None else name
    if key not in moods.moods:
        raise MoodError(f"street_mood must be one of {', '.join(sorted(moods.moods))}, got {name!r}")
    return moods.moods[key]


def apply_mood(profile: Profile, mood: Mood) -> Profile:
    """The year profile as the mood scales it: vines, planted trees and wrecks multiplied, the category factors set."""
    return replace(profile, vines=profile.vines * mood.vines, planted=profile.planted * mood.planted_trees,
                   wrecks=profile.wrecks * mood.wrecks, categories=mood.categories)


def mood_props(props: Iterable[Prop], mood: Mood) -> list[Prop]:
    """The props the mood keeps: every one when it lists no conditions, else those of a listed condition or of none."""
    if mood.conditions is None:
        return list(props)
    return [prop for prop in props if not prop.spec.condition or prop.spec.condition in mood.conditions]


__all__ = ["MOODS_PATH", "Mood", "MoodError", "MoodTable", "apply_mood", "load_moods", "mood_for", "mood_props", "parse_moods"]
