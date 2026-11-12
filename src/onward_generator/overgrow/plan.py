"""Placement plan: the only document the overgrow model may produce.

The text is data. It is parsed as strict JSON and checked against
``config/overgrow_plan.schema.json``. Nothing in it is executed. Asset ids and
anchor ids are not fixed to one city: ``bind_plan`` accepts whatever the digest
and the installed library of this run declare.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from onward_generator.config.diagnostics import Diagnostic
from onward_generator.config.loader import loads_strict

REPO_ROOT = Path(__file__).resolve().parents[3]
PLAN_SCHEMA_PATH = REPO_ROOT / "config" / "overgrow_plan.schema.json"
PLAN_VERSION = "0.1.0"
MAX_ACTIONS = 24
MAX_RESPONSE_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class SpanAction:
    asset_id: str
    from_id: str
    to_id: str
    passage_m: float


@dataclass(frozen=True, slots=True)
class CoverAction:
    asset_id: str
    edge_id: str


@dataclass(frozen=True, slots=True)
class WatchAction:
    asset_id: str
    poi_id: str
    count: int


@dataclass(frozen=True, slots=True)
class PlaceAction:
    asset_id: str
    anchor_id: str
    crosswise: bool


Action = SpanAction | CoverAction | WatchAction | PlaceAction


@dataclass(frozen=True, slots=True)
class OvergrowPlan:
    mood: str
    actions: tuple[Action, ...]
    document: dict[str, Any]


class PlanError(ValueError):
    def __init__(self, diagnostics: list[Diagnostic]) -> None:
        super().__init__("\n".join(str(item) for item in diagnostics))
        self.diagnostics = diagnostics


@cache
def plan_schema() -> dict[str, Any]:
    schema = json.loads(PLAN_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return schema


# The digest ids each gesture names, and the curated grouping its asset needs ("" is an older row, which may repeat).
_GESTURE_IDS: dict[str, tuple[tuple[str, str], ...]] = {
    "span": (("from_id", "corner"), ("to_id", "corner")),
    "cover": (("edge_id", "facade"),),
    "watch": (("poi_id", "poi"),),
    "place": (("anchor_id", "lane"),),
}
_GESTURE_GROUPING = {"span": "run", "cover": "grid"}


def run_schema(anchor_ids: dict[str, set[str]], groupings: dict[str, str]) -> dict[str, Any]:
    """The plan schema with this run's ids as enums, for the model's structured output only.

    ``anchor_ids`` is ``Digest.anchor_ids()``; ``groupings`` maps each offered asset id to its curated grouping.
    Ollama samples only what the schema allows, so a reply cannot name an id this run did not declare, or a span of
    an asset that is not a run. A gesture with no id or no asset to offer is left out. The reply is still parsed
    against the plain schema and bound by ``bind_plan``: the enums only make a valid reply likely."""
    schema = copy.deepcopy(plan_schema())  # plan_schema is cached: never edit it in place
    kept = []
    for branch in schema["properties"]["actions"]["items"]["oneOf"]:
        op = branch["properties"]["op"]["const"]
        need = _GESTURE_GROUPING.get(op)
        assets = sorted(a for a, grouping in groupings.items() if need is None or grouping in ("", need))
        fields = _GESTURE_IDS[op]
        if not assets or any(not anchor_ids.get(kind) for _, kind in fields):
            continue
        branch["properties"]["asset_id"] = {"enum": assets}
        for name, kind in fields:
            branch["properties"][name] = {"enum": sorted(anchor_ids[kind])}
        kept.append(branch)
    if kept:
        schema["properties"]["actions"]["items"]["oneOf"] = kept
    else:
        schema["properties"]["actions"] = {"type": "array", "maxItems": 0}
    return schema


def plan_schema_sha256() -> str:
    return hashlib.sha256(PLAN_SCHEMA_PATH.read_bytes()).hexdigest()


def _pointer(parts: Any) -> str:
    return "".join(f"/{part}" for part in parts)


def _flatten(error: Any) -> list[Any]:
    children = list(getattr(error, "context", ()) or ())
    if not children:
        return [error]
    found: list[Any] = []
    for child in children:
        found.extend(_flatten(child))
    return found or [error]


def _explain(error: Any) -> str:
    where = _pointer(error.absolute_path) or "/"
    if error.validator == "additionalProperties":
        return f"{where}: unknown field; the plan only accepts the fields of config/overgrow_plan.schema.json ({error.message})"
    if error.validator == "required":
        return f"{where}: incomplete response, {error.message}"
    if error.validator in ("minimum", "maximum", "minItems", "maxItems"):
        return f"{where}: value {error.instance!r} is outside the allowed range ({error.message})"
    if error.validator == "const":
        return f"{where}: {error.instance!r} is not {error.validator_value!r}"
    if error.validator == "oneOf":
        return f"{where}: action does not match span, cover, watch, or place"
    if error.validator == "type":
        return f"{where}: expected {error.validator_value}, got {type(error.instance).__name__}"
    return f"{where}: {error.message}"


def _fail(code: str, message: str, path: str = "") -> PlanError:
    return PlanError([Diagnostic("error", code, path, message)])


def parse_overgrow_plan(text: str) -> OvergrowPlan:
    """Strict JSON, then the schema. Ids are checked later against this run's digest."""
    raw = text.encode("utf-8")
    if len(raw) > MAX_RESPONSE_BYTES:
        raise _fail("response_too_large", f"response is {len(raw)} bytes; limit is {MAX_RESPONSE_BYTES}")
    if not text.strip():
        raise _fail("empty_response", "the response is empty")
    try:
        document = loads_strict(text.strip())
    except json.JSONDecodeError as exc:
        raise _fail(
            "invalid_json",
            f"the response is not JSON (line {exc.lineno}, column {exc.colno}: {exc.msg}); "
            "only a JSON object is accepted, without prose or code fences",
        ) from exc
    except ValueError as exc:
        raise _fail("invalid_json", f"the response is not strict JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise _fail("invalid_root", f"the response must be a JSON object, got {type(document).__name__}")
    errors = sorted(
        Draft202012Validator(plan_schema()).iter_errors(document),
        key=lambda item: (list(map(str, item.absolute_path)), item.message),
    )
    if errors:
        detailed = []
        for item in errors:
            detailed.extend(_flatten(item))
        raise PlanError([
            Diagnostic("error", f"plan_{item.validator}", _pointer(item.absolute_path), _explain(item))
            for item in detailed
        ])
    actions: list[Action] = []
    for row in document["actions"]:
        op = row["op"]
        if op == "span":
            actions.append(SpanAction(row["asset_id"], row["from_id"], row["to_id"], float(row["passage_m"])))
        elif op == "cover":
            actions.append(CoverAction(row["asset_id"], row["edge_id"]))
        elif op == "watch":
            actions.append(WatchAction(row["asset_id"], row["poi_id"], int(row["count"])))
        else:
            actions.append(PlaceAction(row["asset_id"], row["anchor_id"], bool(row["crosswise"])))
    return OvergrowPlan(document["mood"], tuple(actions), document)


def bind_plan(plan: OvergrowPlan, *, asset_ids: set[str], anchor_ids: dict[str, set[str]]) -> None:
    """Reject ids this run did not declare. ``anchor_ids`` maps ``corner``, ``facade``, ``poi``, ``lane``."""
    problems: list[Diagnostic] = []
    for index, action in enumerate(plan.actions):
        path = f"/actions/{index}"
        if action.asset_id not in asset_ids:
            problems.append(Diagnostic(
                "error", "unknown_asset", f"{path}/asset_id",
                f"unknown asset {action.asset_id!r}; the library for this run does not include it",
            ))
        if isinstance(action, SpanAction):
            _known(problems, anchor_ids["corner"], action.from_id, f"{path}/from_id")
            _known(problems, anchor_ids["corner"], action.to_id, f"{path}/to_id")
        elif isinstance(action, CoverAction):
            _known(problems, anchor_ids["facade"], action.edge_id, f"{path}/edge_id")
        elif isinstance(action, WatchAction):
            _known(problems, anchor_ids["poi"], action.poi_id, f"{path}/poi_id")
        else:
            _known(problems, anchor_ids["lane"], action.anchor_id, f"{path}/anchor_id")
    if problems:
        raise PlanError(problems)


def _known(problems: list[Diagnostic], known: set[str], value: str, path: str) -> None:
    if value not in known:
        problems.append(Diagnostic(
            "error", "unknown_anchor", path,
            f"unknown id {value!r}; it is not in this run's digest",
        ))
