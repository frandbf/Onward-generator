"""The model picks which wall decals suit the map; the rules place them (ADR-038).

With ``ambient_ai`` in ``map.json`` the local model sees each decal's id, kind and description and the map's brief,
and names 6 to 12 of them of at least 3 kinds (all of them, or all their kinds, when fewer are offered). Ollama's
structured output enumerates the offered ids. A reply that does not validate is tried once more, quoting the
failure; then every decal is used. A later run with the same instruction replays the stored pick.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from onward_generator.signs.decals import Decal

PICK_VERSION = "0.1.0"
MIN_PICK = 6
MAX_PICK = 12
MIN_KINDS = 3
ATTEMPTS = 2
TIMEOUT_S = 180.0

SYSTEM_PROMPT = f"""You choose the wall decals of a 3D city map: stains, soot, rust, cracks, torn posters, scribbles, brick patches. Reply with one JSON object and nothing else.

The user message has a brief in quotes and the decals offered. Choose from {MIN_PICK} to {MAX_PICK} different decal ids, of at least {MIN_KINDS} different kinds, that suit the brief. When the brief is empty, choose what the walls of a worn street of any city show. You do not place anything: the code puts the decals on the walls.

The object has exactly these keys:
- pick_version: always "{PICK_VERSION}".
- decal_ids: the chosen ids, each once.
"""


@dataclass
class DecalPick:
    ok: bool
    decal_ids: tuple[str, ...]
    record: dict[str, Any] = field(default_factory=dict)


class DecalPickError(ValueError):
    """A reply the rules cannot use."""


def instruction(brief: str, decals: Sequence[Decal]) -> str:
    rows = [{"id": d.id, "kind": d.kind, "description": d.description} for d in sorted(decals, key=lambda d: d.id)]
    return f"Brief (quoted exactly):\n{json.dumps(brief, ensure_ascii=False)}\n\nDecals:\n{json.dumps(rows, ensure_ascii=False)}\n"  # pragma: no mutate: ensure_ascii=None is false, as False


def _bounds(decals: Sequence[Decal]) -> tuple[int, int]:
    return min(MIN_PICK, len(decals)), min(MIN_KINDS, len({d.kind for d in decals}))


def schema(decals: Sequence[Decal]) -> dict[str, Any]:
    fewest, _ = _bounds(decals)
    return {
        "type": "object", "additionalProperties": False, "required": ["pick_version", "decal_ids"],
        "properties": {
            "pick_version": {"const": PICK_VERSION},
            "decal_ids": {"type": "array", "items": {"enum": sorted(d.id for d in decals)}, "uniqueItems": True,
                          "minItems": fewest, "maxItems": MAX_PICK},
        },
    }


def parse(text: str, decals: Sequence[Decal]) -> tuple[str, ...]:
    """The ids of a reply, or ``DecalPickError`` naming what is wrong."""
    by_id = {d.id: d for d in decals}
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DecalPickError(f"the reply is not JSON: {exc.msg}") from exc
    if not isinstance(doc, dict) or doc.get("pick_version") != PICK_VERSION:
        raise DecalPickError(f'the reply needs pick_version "{PICK_VERSION}"')
    ids = doc.get("decal_ids")
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        raise DecalPickError("decal_ids must be a list of ids")
    unknown = sorted({i for i in ids if i not in by_id})
    if unknown:
        raise DecalPickError("ids that are not offered: " + ", ".join(unknown))
    if len(set(ids)) != len(ids):
        raise DecalPickError("each id may be chosen once")
    fewest, kinds = _bounds(decals)
    if not fewest <= len(ids) <= MAX_PICK:
        raise DecalPickError(f"choose from {fewest} to {MAX_PICK} ids, not {len(ids)}")
    if len({by_id[i].kind for i in ids}) < kinds:
        raise DecalPickError(f"choose ids of at least {kinds} kinds")
    return tuple(ids)


def pick_decals(decals: Sequence[Decal], *, brief: str, provider=None, replay: Mapping[str, Any] | None = None) -> DecalPick:
    """Ask for a pick of ``decals``, or replay a stored one with the same instruction; every decal when both fail."""
    digest = hashlib.sha256((SYSTEM_PROMPT + instruction(brief, decals)).encode("utf-8")).hexdigest()
    if isinstance(replay, Mapping) and replay.get("instruction_sha256") == digest and replay.get("origin") in ("ai_generated", "replay"):
        try:
            ids = parse(str(replay.get("raw") or ""), decals)  # pragma: no mutate: a record without a reply parses as nothing, whatever stands for it
        except DecalPickError:
            pass
        else:
            return DecalPick(True, ids, _record("replay", digest, str(replay["raw"]), ids, None, 0))
    error = text = ""
    for attempt in range(1, ATTEMPTS + 1):
        message = instruction(brief, decals) + (f"\nThe previous reply was rejected:\n{error}\nReply again with one JSON object.\n" if error else "")
        try:
            if provider is None:
                from onward_generator.ai.providers import OllamaProvider

                provider = OllamaProvider(timeout_s=TIMEOUT_S)
            text = provider.complete(SYSTEM_PROMPT, message, schema(decals)).text
            ids = parse(text, decals)
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            error = str(exc)
            continue
        return DecalPick(True, ids, _record("ai_generated", digest, text, ids, None, attempt))
    every = tuple(sorted(d.id for d in decals))
    return DecalPick(False, every, _record("rules_fallback", digest, text, every, error, ATTEMPTS))


def _record(origin: str, digest: str, raw: str, ids: tuple[str, ...], reason: str | None, attempts: int) -> dict[str, Any]:
    return {"pick_version": PICK_VERSION, "instruction_sha256": digest, "origin": origin, "raw": raw, "decal_ids": list(ids),
            "reason": reason, "attempts": attempts}


__all__ = ["MAX_PICK", "MIN_KINDS", "MIN_PICK", "DecalPick", "DecalPickError", "parse", "pick_decals", "schema"]
