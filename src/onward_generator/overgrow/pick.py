"""The model picks which library props suit the map; the rules decide where they stand (ADR-010 amendment).

The placement plan asked the model where each copy goes, by digest ids. ``qwen3:14b`` put every copy on one lane
anchor in each of three runs on Rue Mouffetard (12 November 2026), even when the retry said to spread them, so the
rules placed every prop of those maps. Here the model only names 6 to 20 offered asset ids of at least 3 roles, and
a mood; ``place.overgrow`` then hands the rules that subset of the library, so the props are the model's and the
spacing, the passage and the keep-outs are the rules'. The prompt carries no digest, so it is short.

A provider error or a reply that does not validate is tried once more, quoting the failure; then the rules use the
whole library. A later run with the same instruction replays the stored reply without calling the model.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field

from onward_generator.overgrow.library import Prop
from onward_generator.overgrow.prompt import library_rows, prompt_props
from onward_generator.overgrow.realize import OVERGROW_TIMEOUT_S, fitted_props

PICK_VERSION = "0.1.0"
PICK_SCHEMA = "onward_generator.overgrow_ambient_pick"
MIN_PICK = 6
MAX_PICK = 20
MIN_ROLES = 3
PICK_ATTEMPTS = 2

SYSTEM_PROMPT = f"""You choose the props of a 3D city map. Reply with one JSON object and nothing else. No prose, no markdown, no code.

The user message has a brief in quotes and the installed library. Choose the library rows that suit the brief: from {MIN_PICK} to {MAX_PICK} different asset ids, of at least {MIN_ROLES} different roles. When the brief is empty, choose what an abandoned street of any city holds. You do not place anything: the code spreads the chosen props along the streets and keeps the passages clear.

The object has exactly these keys:
- pick_version: always "{PICK_VERSION}".
- mood: one sentence on the street the choice makes.
- asset_ids: the chosen ids, each once, each an id of the library.
"""


# How the library rows are written into the prompt: keys sorted, non-ASCII text as it is.
_ROWS_JSON: dict[str, bool] = {"sort_keys": True, "ensure_ascii": False}


@dataclass
class PickResult:
    ok: bool
    origin: str
    asset_ids: tuple[str, ...]
    record: dict = field(default_factory=dict)
    reason: str | None = None


class PickError(ValueError):
    """A reply the rules cannot use."""


def instruction(brief: str, props: list[Prop]) -> str:
    quoted = json.dumps(brief, ensure_ascii=False)  # pragma: no mutate: None is falsy like False and writes the same text
    rows = library_rows(props)
    library_json = json.dumps(rows, **_ROWS_JSON)
    return f"Brief (quoted exactly):\n{quoted}\n\nLibrary:\n{library_json}\n"


def instruction_sha256(brief: str, props: list[Prop]) -> str:
    return hashlib.sha256((SYSTEM_PROMPT + "\n" + instruction(brief, props)).encode("utf-8")).hexdigest()


def _bounds(offered: Mapping[str, Prop]) -> tuple[int, int]:
    """The fewest ids and roles a pick needs: ``MIN_PICK`` and ``MIN_ROLES``, or all a small library offers."""
    return min(MIN_PICK, len(offered)), min(MIN_ROLES, len({prop.spec.role for prop in offered.values()}))


def pick_schema(asset_ids: list[str], fewest: int = MIN_PICK) -> dict:
    """The reply's JSON schema with this run's offered ids as the only values ``asset_ids`` may hold."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["pick_version", "mood", "asset_ids"],
        "properties": {
            "pick_version": {"const": PICK_VERSION},
            "mood": {"type": "string", "minLength": 1, "maxLength": 240},
            "asset_ids": {"type": "array", "items": {"enum": sorted(asset_ids)}, "uniqueItems": True,
                          "minItems": fewest, "maxItems": MAX_PICK},
        },
    }


def parse_pick(text: str, offered: Mapping[str, Prop]) -> tuple[tuple[str, ...], str]:
    """The ids and the mood of a reply, or ``PickError`` naming what is wrong."""
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PickError(f"the reply is not JSON: {exc.msg}") from exc
    if not isinstance(doc, dict) or doc.get("pick_version") != PICK_VERSION:
        raise PickError(f'the reply needs pick_version "{PICK_VERSION}"')
    ids = doc.get("asset_ids")
    mood = doc.get("mood")
    if not isinstance(mood, str) or not mood.strip():
        raise PickError("the reply needs a mood sentence")
    if not isinstance(ids, list) or not all(isinstance(item, str) for item in ids):
        raise PickError("asset_ids must be a list of ids")
    unknown = sorted({item for item in ids if item not in offered})
    if unknown:
        raise PickError("ids that are not in the library: " + ", ".join(unknown))
    chosen = tuple(dict.fromkeys(ids))
    if len(chosen) != len(ids):
        raise PickError("each id may be chosen once")
    fewest, fewest_roles = _bounds(offered)
    if not fewest <= len(chosen) <= MAX_PICK:
        raise PickError(f"choose from {fewest} to {MAX_PICK} ids, not {len(chosen)}")
    roles = {offered[item].spec.role for item in chosen}
    if len(roles) < fewest_roles:
        raise PickError(f"choose ids of at least {fewest_roles} roles, not {len(roles)}")
    return chosen, mood


def pick_props(props: list[Prop], *, brief: str, provider=None, replay: Mapping[str, object] | None = None) -> PickResult:
    """Ask for a pick of ``props``, or replay a stored one with the same instruction."""
    fitted = fitted_props(props)
    usable = prompt_props(fitted, brief)
    offered = {prop.spec.id: prop for prop in usable}
    library = {"offered": len(usable), "fitted": len(fitted)}
    digest = instruction_sha256(brief, usable)
    if isinstance(replay, Mapping) and replay.get("schema") == PICK_SCHEMA and replay.get("instruction_sha256") == digest \
            and replay.get("origin") in {"ai_generated", "replay"} and isinstance(replay.get("raw"), str):
        try:
            ids, mood = parse_pick(str(replay["raw"]), offered)
        except PickError:
            pass
        else:
            return PickResult(True, "replay", ids, _record("replay", digest, str(replay["raw"]), None, ids, mood, 0, library))
    error = text = ""
    for attempt in range(1, PICK_ATTEMPTS + 1):
        message = instruction(brief, usable)
        if error:
            message += f"\nThe previous reply was rejected:\n{error}\nReply again with one JSON object.\n"
        try:
            if provider is None:
                from onward_generator.ai.providers import OllamaProvider

                provider = OllamaProvider(timeout_s=OVERGROW_TIMEOUT_S)
            text = provider.complete(SYSTEM_PROMPT, message, pick_schema(list(offered), _bounds(offered)[0])).text
            ids, mood = parse_pick(text, offered)
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            error = str(exc)
            continue
        return PickResult(True, "ai_generated", ids, _record("ai_generated", digest, text, None, ids, mood, attempt, library))
    return PickResult(False, "rules_fallback", (), _record("rules_fallback", digest, text, error, (), None, PICK_ATTEMPTS, library), error)


def _record(origin: str, digest: str, raw: str, reason: str | None, ids: tuple[str, ...], mood: str | None, attempts: int,
            library: dict[str, int]) -> dict:
    return {
        "schema": PICK_SCHEMA,
        "pick_version": PICK_VERSION,
        "instruction_sha256": digest,
        "origin": origin,
        "raw": raw,
        "reason": reason,
        "asset_ids": list(ids),
        "mood": mood,
        "attempts": attempts,
        "library": library,
    }


__all__ = ["MAX_PICK", "MIN_PICK", "MIN_ROLES", "PICK_SCHEMA", "PICK_VERSION", "PickError", "PickResult", "parse_pick",
           "pick_props", "pick_schema"]
