"""Generation intent: the only document a model may produce (phase 06).

The model's text is data. It is parsed as strict JSON and validated against
``config/intent.schema.json``; nothing in it is executed or evaluated.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator

from onward_generator.config.diagnostics import Diagnostic
from onward_generator.config.loader import loads_strict

REPO_ROOT = Path(__file__).resolve().parents[3]
INTENT_SCHEMA_PATH = REPO_ROOT / "config" / "intent.schema.json"
INTENT_VERSION = "0.1.0"
MAX_RESPONSE_BYTES = 64 * 1024

FacadeDetail = Literal["low", "medium", "high"]
Priority = Literal["max", "balanced"]


@dataclass(frozen=True, slots=True)
class Intent:
    facade_detail: FacadeDetail | None
    residential_levels: int | None
    interior_buildings: int | None
    performance_priority: Priority | None
    unsupported_requests: tuple[tuple[str, str], ...]
    summary: str
    document: dict[str, Any]

    @property
    def requested(self) -> dict[str, Any]:
        return {k: v for k, v in self.document["parameters"].items() if v is not None}


class IntentError(ValueError):
    def __init__(self, diagnostics: list[Diagnostic]) -> None:
        super().__init__("\n".join(str(d) for d in diagnostics))
        self.diagnostics = diagnostics


@cache
def intent_schema() -> dict[str, Any]:
    schema = json.loads(INTENT_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return schema


def intent_schema_sha256() -> str:
    return hashlib.sha256(INTENT_SCHEMA_PATH.read_bytes()).hexdigest()


def _pointer(parts: Any) -> str:
    return "".join(f"/{p}" for p in parts)


def _explain(error: Any) -> str:
    """Schema messages rewritten for a person reading the CLI output."""
    where = _pointer(error.absolute_path) or "/"
    if error.validator == "additionalProperties":
        return f"{where}: unknown field(s); the intent only accepts the fields of config/intent.schema.json ({error.message})"
    if error.validator == "required":
        return f"{where}: incomplete response, {error.message}"
    if error.validator in ("minimum", "maximum"):
        return f"{where}: value {error.instance!r} is outside the allowed range ({error.message})"
    if error.validator == "enum":
        return f"{where}: {error.instance!r} is not an allowed value; allowed: {error.validator_value}"
    if error.validator == "type":
        return f"{where}: expected {error.validator_value}, got {type(error.instance).__name__} {error.instance!r}"
    return f"{where}: {error.message}"


def parse_intent(text: str) -> Intent:
    """Strict JSON → schema → typed intent. Raises ``IntentError`` with every problem found."""
    def fail(code: str, message: str, path: str = "") -> IntentError:
        return IntentError([Diagnostic("error", code, path, message)])

    raw = text.encode("utf-8")
    if len(raw) > MAX_RESPONSE_BYTES:
        raise fail("response_too_large", f"response is {len(raw)} bytes; limit is {MAX_RESPONSE_BYTES}")
    if not text.strip():
        raise fail("empty_response", "the response is empty")
    try:
        doc = loads_strict(text.strip())
    except json.JSONDecodeError as exc:
        raise fail("invalid_json", f"the response is not JSON (line {exc.lineno}, column {exc.colno}: {exc.msg}); "
                                   "only a JSON object is accepted, without prose or code fences") from exc
    except ValueError as exc:
        raise fail("invalid_json", f"the response is not strict JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise fail("invalid_root", f"the response must be a JSON object, got {type(doc).__name__}")
    errors = sorted(Draft202012Validator(intent_schema()).iter_errors(doc), key=lambda e: (list(map(str, e.absolute_path)), e.message))
    if errors:
        raise IntentError([Diagnostic("error", f"intent_{e.validator}", _pointer(e.absolute_path), _explain(e)) for e in errors])
    p = doc["parameters"]
    return Intent(
        facade_detail=p["facade_detail"],
        residential_levels=p["residential_levels"],
        interior_buildings=p["interior_buildings"],
        performance_priority=p["performance_priority"],
        unsupported_requests=tuple((u["request"], u["reason"]) for u in doc["unsupported_requests"]),
        summary=doc["summary"],
        document=doc,
    )
