"""One texture plan per map, chosen from the imported library.

The model may only name library ids. A reply that misses a slot, repeats an
id, or picks the wrong surface is rejected. If the model is absent or the
reply fails twice, the same library is sampled without a stored palette.
"""

from __future__ import annotations

import hashlib
import json
import zlib
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from onward_generator.ai.providers import Provider, ProviderError
from onward_generator.materials.plan import SLOT_FORBIDDEN, SLOT_MODE, PlanError
from onward_generator.materials.polyhaven import TextureRecord

REPO_ROOT = Path(__file__).resolve().parents[3]
LIBRARY_PATH = REPO_ROOT / "config" / "dress" / "imported_library.json"

SLOT_BINDINGS: dict[str, tuple[str, ...]] = {
    "historic_facades": ("historic_plaster_cream",),
    "ochre_facades": ("plaster_ochre_wall",),
    "plain_facades": ("plaster_white_wall",),
    "stone_monuments": ("limestone_warm", "stone_light_wall", "curb_stone"),
    "streets_plazas": ("cobblestone_paving",),
    "roofs": ("terracotta_roof",),
    "brick": ("brick_red_wall",),
    "carriageway": ("asphalt_road",),
    "sidewalks": ("sidewalk_concrete",),
    "plain_pedestrian": ("pedestrian_plain",),
    "doors": ("wood_door",),
    "trunks": ("tree_bark",),
    "flat_roofs": ("flat_roof_grey", "flat_roof_warm"),
}
SLOT_FAMILY = {
    "historic_facades": "plaster",
    "ochre_facades": "plaster",
    "plain_facades": "plaster",
    "stone_monuments": "rock",
    "streets_plazas": "paving",
    "roofs": "roof",
    "brick": "brick",
    "carriageway": "asphalt",
    "sidewalks": "concrete",
    "plain_pedestrian": "concrete",
    "doors": "door",
    "trunks": "bark",
    "flat_roofs": "concrete",
}

_CHOICE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["choices"],
    "properties": {
        "choices": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["slot", "library_id"],
                "properties": {
                    "slot": {"type": "string"},
                    "library_id": {"type": "string"},
                },
            },
        }
    },
}


def texture_record(library_id: str, rows: Sequence[Mapping[str, Any]] | None = None) -> TextureRecord:
    catalog = list(rows) if rows is not None else library_rows()
    row = next((item for item in catalog if item.get("id") == library_id), None)
    if row is None:
        raise PlanError(f"{library_id} is not in the imported library")
    path = REPO_ROOT / str(row["path"])
    if not path.is_file():
        raise PlanError(f"{library_id} file is missing at {row['path']}")
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != row.get("sha256"):
        raise PlanError(f"{library_id} file does not match the library hash")
    return TextureRecord(
        provider=str(row.get("provider") or "library"),
        asset_id=library_id,
        license=str(row.get("license") or ""),
        license_url=str(row.get("license_url") or ""),
        authors=(str(row.get("credit") or row.get("provider") or "library"),),
        page_url=str(row.get("source_url") or ""),
        file_url=str(row.get("source_url") or ""),
        md5=hashlib.md5(data, usedforsecurity=False).hexdigest(),
        sha256=digest,
        size_bytes=len(data),
        map="Diffuse",
        resolution="1k",
        format=path.suffix.lstrip("."),
        path=str(path),
        retrieved_at=datetime.now(UTC).isoformat(),
        user_agent="imported-library",
    )


def library_rows(path: Path | None = None) -> list[dict[str, Any]]:
    source = path or LIBRARY_PATH
    document = json.loads(source.read_text(encoding="utf-8"))
    rows = document.get("textures") if isinstance(document, dict) else None
    if not isinstance(rows, list):
        raise PlanError(f"{source}: imported library has no textures")
    return [row for row in rows if isinstance(row, dict) and row.get("id")]


def texture_family(row: Mapping[str, Any]) -> str | None:
    name = str(row.get("name") or "").lower()
    if name.startswith("paintedplaster"):
        return "plaster"
    if name.startswith("bricks"):
        return "brick"
    if name.startswith("rock"):
        return "rock"
    if name.startswith("roofingtiles"):
        return "roof"
    if name.startswith("pavingstones"):
        return "paving"
    if name.startswith("concrete"):
        return "concrete"
    if name.startswith("asphalt"):
        return "asphalt"
    if name.startswith("bark"):
        return "bark"
    if name.startswith("door_wood") and "lit" not in name and "overlay" not in name:
        return "door"
    return None


def _pools(rows: Sequence[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    pools: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        family = texture_family(row)
        if family is None:
            continue
        pools.setdefault(family, []).append(dict(row))
    for family in pools:
        pools[family].sort(key=lambda row: str(row["id"]))
    return pools


def unfilled_slots(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    """Slots whose texture family has no row in the library, e.g. trunks without bark."""
    pools = _pools(rows)
    return [slot for slot, family in SLOT_FAMILY.items() if not pools.get(family)]


def _entry(slot: str, row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": slot,
        "library_id": row["id"],
        "catalog_ids": list(SLOT_BINDINGS[slot]),
        "albedo_mode": SLOT_MODE[slot],
        "forbidden_catalog_ids": sorted(SLOT_FORBIDDEN.get(slot, ())),
        "note": f"{row.get('credit') or row['id']}. {row.get('note') or ''}".strip(),
    }


def deterministic_choices(rows: Sequence[Mapping[str, Any]], seed: int) -> dict[str, str]:
    pools = _pools(rows)
    used: set[str] = set()
    chosen: dict[str, str] = {}
    for slot, family in SLOT_FAMILY.items():
        pool = [row for row in pools.get(family, ()) if row["id"] not in used]
        if not pool:
            continue
        index = zlib.crc32(f"{seed}:{slot}".encode()) % len(pool)
        chosen[slot] = str(pool[index]["id"])
        used.add(chosen[slot])
    return chosen


def _document(name: str, choices: Mapping[str, str], rows: Sequence[Mapping[str, Any]], *, selector: str, brief: str | None) -> dict[str, Any]:
    by_id = {str(row["id"]): row for row in rows}
    entries = [_entry(slot, by_id[library_id]) for slot, library_id in choices.items()]
    entries.sort(key=lambda row: row["id"])
    atmosphere = "abandoned" if brief and "abandon" in brief.lower() else "inhabited"
    description = f"Textures for {name}, chosen from the imported library ({selector})."
    gaps = unfilled_slots(rows)
    if gaps:
        families = ", ".join(f"{slot} ({SLOT_FAMILY[slot]})" for slot in gaps)
        description += f" The library has no texture for {families}; those slots keep the procedural tile."
    return {
        "schema": "onward_generator.texture_plan",
        "schema_version": "0.1.0",
        "description": description,
        "provider": "library",
        "resolution": "1k",
        "map": "Diffuse",
        "format": "jpg",
        "atmosphere": atmosphere,
        "selector": selector,
        "entries": entries,
    }


def _parse_choices(text: str, rows: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PlanError(f"texture reply is not JSON: {exc}") from exc
    raw = payload.get("choices") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        raise PlanError("texture reply needs a choices list")
    pools = _pools(rows)
    by_id = {str(row["id"]): texture_family(row) for row in rows}
    chosen: dict[str, str] = {}
    for item in raw:
        if not isinstance(item, dict):
            raise PlanError("each texture choice must be an object")
        slot = str(item.get("slot") or "")
        library_id = str(item.get("library_id") or "")
        if slot not in SLOT_FAMILY:
            raise PlanError(f"unknown texture slot {slot!r}")
        if library_id in chosen.values():
            raise PlanError(f"{library_id} is used more than once")
        if by_id.get(library_id) != SLOT_FAMILY[slot]:
            raise PlanError(f"{slot} cannot use {library_id}")
        if library_id not in {row["id"] for row in pools[SLOT_FAMILY[slot]]}:
            raise PlanError(f"{library_id} is not in the {SLOT_FAMILY[slot]} pool")
        chosen[slot] = library_id
    missing = [slot for slot, family in SLOT_FAMILY.items() if pools.get(family) and slot not in chosen]
    if missing:
        raise PlanError(f"texture reply missed {missing}")
    return chosen


def _ask(provider: Provider, *, name: str, map_id: str, latitude: float, longitude: float, brief: str | None, rows: Sequence[Mapping[str, Any]], seed: int, complaint: str | None) -> dict[str, str]:
    pools = _pools(rows)
    listed = {
        family: [row["id"] for row in pool]
        for family, pool in pools.items()
    }
    instruction = {
        "map": {"id": map_id, "name": name, "latitude": latitude, "longitude": longitude, "brief": brief or ""},
        "slots": SLOT_FAMILY,
        "ids_by_family": listed,
        "complaint": complaint or "",
    }
    system = (
        "Choose one tileable albedo for each slot of a playable city map. "
        "Keep the walls, roofs and ground in one calm palette. "
        "Use only listed ids, one id once, and the family named for that slot. "
        "Reply with JSON only."
    )
    result = provider.complete(system, json.dumps(instruction, ensure_ascii=False), _CHOICE_SCHEMA)
    return _parse_choices(result.text, rows)


def select_texture_plan(
    *,
    name: str,
    map_id: str,
    latitude: float,
    longitude: float,
    brief: str | None,
    seed: int,
    provider: Provider | None = None,
    library: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    rows = list(library) if library is not None else library_rows()
    if provider is None:
        choices = deterministic_choices(rows, seed)
        return _document(name, choices, rows, selector="library", brief=brief)
    complaint: str | None = None
    for _attempt in range(2):
        try:
            choices = _ask(
                provider, name=name, map_id=map_id, latitude=latitude, longitude=longitude,
                brief=brief, rows=rows, seed=seed, complaint=complaint,
            )
            return _document(name, choices, rows, selector="ollama", brief=brief)
        except ProviderError as exc:
            complaint = str(exc)
            break
        except PlanError as exc:
            complaint = str(exc)
    choices = deterministic_choices(rows, seed)
    document = _document(name, choices, rows, selector="library", brief=brief)
    document["description"] += f" The model reply was rejected: {complaint}"
    return document


def assert_library_entries(document: Mapping[str, Any], rows: Sequence[Mapping[str, Any]] | None = None) -> None:
    catalog = list(rows) if rows is not None else library_rows()
    known = {str(row["id"]): texture_family(row) for row in catalog}
    seen: set[str] = set()
    for raw in document["entries"]:
        library_id = str(raw.get("library_id") or "")
        slot = str(raw["id"])
        if not library_id:
            raise PlanError(f"{slot} has no library texture")
        if known.get(library_id) != SLOT_FAMILY.get(slot):
            raise PlanError(f"{slot} cannot use {library_id}")
        if library_id in seen:
            raise PlanError(f"{library_id} is used more than once")
        seen.add(library_id)
