"""Curated placement semantics in a catalogue ``manifest.json``.

Version 0.2.0 gives every row a description, a placement, a grouping, a
condition and tags. A person estimated them from rendered views of the GLB;
the manifest's ``semantics`` block records that. They are not observed in the
file. A manifest of another version is read as before and is not checked.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from onward_generator.overgrow.library_catalog import LibraryError

MANIFEST_SCHEMA_PATH = Path(__file__).resolve().parents[3] / "config" / "asset_manifest.schema.json"
MANIFEST_VERSION = "0.2.0"
LEGACY_VERSION = "0.1.0"
SEMANTIC_FIELDS = ("description", "placement", "grouping", "condition", "tags")
# Load corrections a person set on a row: a quarter turn, or a refusal such as unknown_scale.
LOAD_FIELDS = ("yaw_deg", "refused")
KIT_ONLY = "kit_only"


@cache
def manifest_schema() -> dict[str, Any]:
    return json.loads(MANIFEST_SCHEMA_PATH.read_text(encoding="utf-8"))


def check_manifest(document: object, where: str) -> None:
    """Refuse a 0.2.0 manifest that misses a field, uses an unknown value, or contradicts itself."""
    if not isinstance(document, dict) or document.get("schema_version") != MANIFEST_VERSION:
        return
    for error in Draft202012Validator(manifest_schema()).iter_errors(document):
        pointer = "".join(f"/{part}" for part in error.absolute_path)
        raise LibraryError(f"{where}{pointer}: {error.message}")
    for row in document["assets"]:
        placement = row["placement"]
        if placement != KIT_ONLY and (row["anchor"] == "facade") != (placement == "wall_mounted"):
            raise LibraryError(f"{where}: {row['id']}: anchor {row['anchor']} does not match placement {placement}")
        if placement == "street" and "carriageway" not in row["surfaces"]:
            raise LibraryError(f"{where}: {row['id']}: placement street needs the carriageway surface")


def keep_semantics(document: dict[str, Any], previous: Mapping[str, Any] | None) -> dict[str, Any]:
    """Copy the curated fields and load corrections of rows the previous manifest described into a rewritten one.

    The fields go right after ``use``. When a row is left without them, the
    manifest is written as 0.1.0 and the semantics block is dropped.
    """
    old = previous if isinstance(previous, Mapping) else {}
    by_id = {str(row.get("id")): row for row in old.get("assets") or [] if isinstance(row, Mapping)}
    rows = []
    for row in document["assets"]:
        curated = {key: by_id[row["id"]][key] for key in (*SEMANTIC_FIELDS, *LOAD_FIELDS) if key in by_id.get(row["id"], {})}
        merged: dict[str, Any] = {}
        for key, value in row.items():
            if key not in curated:
                merged[key] = value
            if key == "use":
                merged.update(curated)
        rows.append(merged)
    complete = "semantics" in old and all(all(key in row for key in SEMANTIC_FIELDS) for row in rows)
    head = {key: value for key, value in document.items() if key not in ("assets", "semantics")}
    head["schema_version"] = MANIFEST_VERSION if complete else LEGACY_VERSION
    if complete:
        head["semantics"] = old["semantics"]
    head["assets"] = rows
    return head
