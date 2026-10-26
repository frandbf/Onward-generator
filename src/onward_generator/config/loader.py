"""Strict JSON loading of project configuration files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from onward_generator.config.diagnostics import Diagnostic

MAX_CONFIG_BYTES = 1024 * 1024
MAX_SCENE_BYTES = 64 * 1024 * 1024


class ConfigLoadError(ValueError):
    """The file cannot be read as a JSON object; carries one diagnostic."""

    def __init__(self, diagnostic: Diagnostic) -> None:
        super().__init__(diagnostic.message)
        self.diagnostic = diagnostic


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key {key!r}")
        result[key] = value
    return result


def _reject_constant(name: str) -> Any:
    raise ValueError(f"non-standard JSON number {name}")


def loads_strict(text: str) -> Any:
    """``json.loads`` that rejects duplicate keys and NaN/Infinity (raises ``ValueError``)."""
    return json.loads(text, object_pairs_hook=_reject_duplicates, parse_constant=_reject_constant)


def read_json_document(path: str | Path, *, max_bytes: int = MAX_CONFIG_BYTES) -> tuple[dict[str, Any], Path]:
    """Read a JSON object, rejecting duplicate keys, NaN/Infinity and huge files.

    Configuration stays at 1 MB. Scene manifests pass ``MAX_SCENE_BYTES``.
    """
    source = Path(path).expanduser()

    def fail(code: str, message: str) -> ConfigLoadError:
        return ConfigLoadError(Diagnostic("error", code, "", message))

    if not source.is_file():
        raise fail("file_not_found", f"configuration file not found: {source}")
    if source.suffix.lower() != ".json":
        raise fail("unsupported_format", f"configuration must be a .json file: {source}")
    size = source.stat().st_size
    if size > max_bytes:
        raise fail("file_too_large", f"file is {size} bytes; limit is {max_bytes}")
    try:
        doc = json.loads(
            source.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicates,
            parse_constant=_reject_constant,
        )
    except UnicodeDecodeError as exc:
        raise fail("invalid_encoding", f"configuration is not UTF-8: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise fail("invalid_json", f"invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}") from exc
    except ValueError as exc:
        raise fail("invalid_json", str(exc)) from exc
    if not isinstance(doc, dict):
        raise fail("invalid_root", "configuration root must be a JSON object")
    return doc, source.resolve()
