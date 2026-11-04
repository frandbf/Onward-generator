"""Turn a map brief into the street rule the placer is allowed to apply.

The model, when it is used, may only answer whether wrecks can sit in the
carriageway. Counts, coordinates and collision stay in code. A missing or
rejected reply falls back to keywords in the brief.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from onward_generator.config.loader import loads_strict

_BLOCKING = (
    "abandon",
    "caos",
    "bloque",
    "chaos",
    "wreck",
    "overgrown",
    "desorden",
)
_STREET_ROLES = frozenset({"wreck", "barrel", "barrier", "container", "pole"})
_SOLID_ROLES = _STREET_ROLES | frozenset({"gate", "fence", "cart", "tank", "table", "bed"})
_OPEN_ROLES = frozenset({
    "can", "plant", "bush", "tree", "camera", "aircon", "manhole", "flower",
    # A box around a lamp or traffic light would be an invisible wall under its arm.
    "lamp",
})
COVER_HEIGHT_M = 0.5
COVER_DEPTH_M = 0.4
"""A prop at least this tall and this deep on its short side looks like cover, so it stops players and bullets."""
_CROSS_ROLES = frozenset({"wreck", "gate", "barrier"})
_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["street_blocking"],
    "properties": {"street_blocking": {"type": "boolean"}},
}
_SYSTEM = (
    "Reply with one JSON object and nothing else: "
    '{"street_blocking": true} when the instruction asks for an abandoned, chaotic '
    "or blocked city, otherwise {\"street_blocking\": false}. "
    "Do not choose coordinates, asset ids or collision."
)


@dataclass(frozen=True, slots=True)
class AmbientPlan:
    brief: str
    street_blocking: bool
    origin: str


def plan_from_brief(brief: str, *, provider=None) -> AmbientPlan:
    """Keywords by default. ``provider`` may override only ``street_blocking``."""
    text = brief.strip()
    planned = AmbientPlan(text, _keywords(text), "rules")
    if provider is None or not text:
        return planned
    try:
        blocking = _model_blocking(provider, text)
    except (OSError, ValueError, TypeError):
        return planned
    return AmbientPlan(text, blocking, "ai_generated")


def is_solid(role: str, category: str, size_m: tuple[float, float, float] | None = None) -> bool:
    """Cars, barrels, posts and similar masses stop the player. Bottles and plants do not.

    Any other prop with its ``size_m`` (length, width, height) is solid when it looks like cover:
    a dumpster, a concrete block, a bench, a planter. Wall-mounted props are never solid.
    """
    if role in _OPEN_ROLES or category in {"vegetation", "facade"}:
        return False
    if role in _SOLID_ROLES or category in {"vehicles", "structures"}:
        return True
    if size_m is None:
        return False
    length_m, width_m, height_m = size_m
    return height_m >= COVER_HEIGHT_M and min(length_m, width_m) >= COVER_DEPTH_M


def blocks_street(role: str, *, street_blocking: bool) -> bool:
    return street_blocking and role in _STREET_ROLES


def cross_yaw(role: str, already_placed: int, *, street_blocking: bool, on_street: bool) -> float:
    """Quarter turn for every third wreck, gate or barrier that sits on a street."""
    if street_blocking and on_street and role in _CROSS_ROLES and already_placed % 3 == 2:
        return math.pi / 2
    return 0.0


def _keywords(text: str) -> bool:
    folded = text.lower()
    return any(word in folded for word in _BLOCKING)


def _model_blocking(provider, brief: str) -> bool:
    from onward_generator.ai.providers import ProviderError

    try:
        result = provider.complete(_SYSTEM, brief, _SCHEMA)
    except ProviderError as exc:
        raise ValueError(str(exc)) from exc
    document = loads_strict(result.text)
    if not isinstance(document, dict) or set(document) != {"street_blocking"}:
        raise ValueError("ambient plan must be exactly street_blocking")
    blocking = document["street_blocking"]
    if not isinstance(blocking, bool):
        raise ValueError("street_blocking must be a boolean")
    return blocking
