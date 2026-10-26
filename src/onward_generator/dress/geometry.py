"""Geometry fingerprints used to prove classify does not move or invent geography."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


def _object_stamp(obj: Mapping[str, Any]) -> dict[str, Any]:
    rec: dict[str, Any] = {
        "id": obj.get("id"),
        "type": obj.get("type"),
        "source_ids": list(obj.get("source_ids") or []),
    }
    for key in ("position_m", "footprint_area_m2", "area_m2", "surface_class", "kind"):
        if key in obj:
            rec[key] = obj[key]
    height = obj.get("height")
    if isinstance(height, Mapping) and "height_m" in height:
        rec["height_m"] = height["height_m"]
    roof = obj.get("roof")
    if isinstance(roof, Mapping):
        rec["roof_type"] = roof.get("type")
        rec["roof_area_m2"] = roof.get("area_m2")
    return rec


def geometry_fingerprint(objects: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Stable subset of object records: ids, sources, areas, heights, positions. No materials."""
    stamps = {str(o["id"]): _object_stamp(o) for o in objects if o.get("id")}
    payload = {"object_ids": sorted(stamps), "objects": stamps}
    return payload


def fingerprint_digest(fingerprint: Mapping[str, Any]) -> str:
    import json

    raw = json.dumps(fingerprint, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compare_fingerprints(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    before_ids = set(before.get("object_ids") or [])
    after_ids = set(after.get("object_ids") or [])
    changed: list[str] = []
    for oid in sorted(before_ids & after_ids):
        if before["objects"][oid] != after["objects"][oid]:
            changed.append(oid)
    return {
        "identical": before == after,
        "added_ids": sorted(after_ids - before_ids),
        "removed_ids": sorted(before_ids - after_ids),
        "changed_ids": changed,
        "before_sha256": fingerprint_digest(before),
        "after_sha256": fingerprint_digest(after),
    }
