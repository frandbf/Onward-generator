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
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageFilter

from onward_generator.ai.providers import Provider, ProviderError
from onward_generator.materials.plan import SLOT_FORBIDDEN, SLOT_MODE, PlanError
from onward_generator.materials.polyhaven import TextureRecord

REPO_ROOT = Path(__file__).resolve().parents[3]
LIBRARY_PATH = REPO_ROOT / "config" / "dress" / "imported_library.json"
WIDE_LIBRARY_PATH = REPO_ROOT / "config" / "dress" / "wide_library.json"
"""Plan T1: CC0 Poly Haven albedos that join the pools only for a map with the map.json ``wide_library`` switch."""

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
    catalog = list(rows) if rows is not None else catalogue_rows()
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


def catalogue_rows() -> list[dict[str, Any]]:
    """Every texture a plan may name: the imported library and the wide one (plan T1). Only the pools decide which
    a map draws on, so a plan written with ``wide`` still resolves later without the switch."""
    return library_rows() + library_rows(WIDE_LIBRARY_PATH)


def texture_family(row: Mapping[str, Any]) -> str | None:
    if row.get("family"):  # the wide library names its family; the imported one is read from the file name
        return str(row["family"])
    name = str(row.get("name") or "").lower()
    if name.startswith(("paintedplaster", "painted_plaster", "plastered_wall")):
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


# A tile repeats every few metres on a large wall or floor, so a visible blotch,
# streak or centred feature turns into a grid. Only calm images join their pool.
# Std of luminance (0-1) after a blur of 1/32 of the tile. Measured on the 1k library:
# kept asphalt011 0.0055, asphalt020s 0.0066, concrete012 0.0105, concrete047a 0.0072,
# concrete048 0.0026; dropped asphalt017 0.0204, asphalt019 0.0164, asphalt021 0.0143,
# asphalt022 0.0138, concrete047b 0.0261 (a rusted cover in the middle).
COARSE_CONTRAST_LIMIT = 0.012
# Walls carry mortar and plaster grain that the ground does not, so their limit is
# looser. Kept paintedplaster006 0.0214, bricks001 0.0221, bricks059 0.0236, rock030
# 0.0103; dropped paintedplaster010 0.0254, paintedplaster014 0.0492, paintedplaster016
# 0.0656 (large stains), bricks003 0.0260 (pale stones), bricks100 0.0440, rock022
# 0.0267, rock055 0.0409.
WALL_CONTRAST_LIMIT = 0.025
CONTRAST_LIMITS: dict[str | None, float] = {
    "asphalt": COARSE_CONTRAST_LIMIT,
    "concrete": COARSE_CONTRAST_LIMIT,
    "plaster": WALL_CONTRAST_LIMIT,
    "brick": WALL_CONTRAST_LIMIT,
    "rock": WALL_CONTRAST_LIMIT,
}
# Roofs and paving are measured only with the map.json "repetition" switch (plan C1, ADR-009
# amendment "Repetition per cell"), so the default pools and picks stay as they were.
# Paving carries joints and stone grain at a coarser scale than asphalt. Kept pavingstones151
# 0.0073, pavingstones007 0.0126, pavingstones008 0.0152; dropped pavingstones050 0.0165 (a
# fan of coloured setts whose arcs make a grid), pavingstones141 0.0188 (large mottled pebbles).
PAVING_CONTRAST_LIMIT = 0.016
# Roof tiles carry rows and weathering. Kept roofingtiles014a 0.0109, roofingtiles014b 0.0156,
# roofingtiles011b 0.0168, roofingtiles011c 0.0183; dropped roofingtiles012c 0.0211 (dark
# stains and moss patches that repeat).
ROOF_CONTRAST_LIMIT = 0.019
EXTENDED_CONTRAST_LIMITS: dict[str | None, float] = {
    **CONTRAST_LIMITS,
    "paving": PAVING_CONTRAST_LIMIT,
    "roof": ROOF_CONTRAST_LIMIT,
}
# A door image is mapped 0-1 on each opening (UNIT_UV_MATERIALS), so it never tiles: the
# coarse contrast says nothing about repetition there.
NOT_APPLICABLE_FAMILIES = frozenset({"door"})
_MEASURE_PX = 128


@lru_cache(maxsize=64)
def coarse_contrast(path: Path) -> float:
    """Large-scale luminance variation of a tile: fine grain is blurred away, patches and features stay."""
    with Image.open(path) as image:
        grey = image.convert("L").resize((_MEASURE_PX, _MEASURE_PX), Image.Resampling.BOX)
    blurred = grey.filter(ImageFilter.GaussianBlur(_MEASURE_PX / 32))
    return float(np.asarray(blurred, dtype=np.float64).std() / 255.0)


def contrast_limit(family: str | None, *, extended: bool = False) -> float | None:
    """The family's coarse-contrast limit; ``extended`` adds roofs and paving (the "repetition" switch)."""
    return (EXTENDED_CONTRAST_LIMITS if extended else CONTRAST_LIMITS).get(family)


def is_calm(row: Mapping[str, Any], limit: float | None = None, *, extended: bool = False) -> bool:
    """A row outside the measured families is always calm. A missing file is left to texture_record to report.

    ``limit`` replaces the family limit. ``extended`` measures roofs and paving as well.
    """
    family_limit = contrast_limit(texture_family(row), extended=extended)
    if family_limit is None:
        return True
    path = REPO_ROOT / str(row["path"])
    if not path.is_file():
        return True
    return coarse_contrast(path) <= (family_limit if limit is None else limit)


def _pools(rows: Sequence[Mapping[str, Any]], *, extended: bool) -> dict[str, list[dict[str, Any]]]:
    pools: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        family = texture_family(row)
        if family is None or not is_calm(row, extended=extended):
            continue
        pools.setdefault(family, []).append(dict(row))
    for family in pools:
        pools[family].sort(key=lambda row: str(row["id"]))
    return pools


def unfilled_slots(rows: Sequence[Mapping[str, Any]], *, extended: bool = False) -> list[str]:
    """Slots whose texture family has no row in the library, e.g. trunks without bark."""
    pools = _pools(rows, extended=extended)
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


def deterministic_choices(rows: Sequence[Mapping[str, Any]], seed: int, *, extended: bool = False) -> dict[str, str]:
    pools = _pools(rows, extended=extended)
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


def _document(name: str, choices: Mapping[str, str], rows: Sequence[Mapping[str, Any]], *, selector: str, brief: str | None,
              extended: bool) -> dict[str, Any]:
    by_id = {str(row["id"]): row for row in rows}
    entries = [_entry(slot, by_id[library_id]) for slot, library_id in choices.items()]
    entries.sort(key=lambda row: row["id"])
    atmosphere = "abandoned" if brief and "abandon" in brief.lower() else "inhabited"
    description = f"Textures for {name}, chosen from the imported library ({selector})."
    gaps = unfilled_slots(rows, extended=extended)
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


def _parse_choices(text: str, rows: Sequence[Mapping[str, Any]], *, extended: bool = False) -> dict[str, str]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PlanError(f"texture reply is not JSON: {exc}") from exc
    raw = payload.get("choices") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        raise PlanError("texture reply needs a choices list")
    pools = _pools(rows, extended=extended)
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


def choice_schema(pools: Mapping[str, Sequence[Mapping[str, Any]]]) -> dict[str, Any]:
    """``_CHOICE_SCHEMA`` with each slot's listed ids as an enum, for the model's structured output only.

    One choice per slot whose family has a pool, each ``slot`` a const and its ``library_id`` one id of that pool, so
    Ollama cannot pair a slot with another family's texture. The reply is still checked by ``_parse_choices``."""
    branches = [
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["slot", "library_id"],
            "properties": {"slot": {"const": slot}, "library_id": {"enum": sorted(str(row["id"]) for row in pools[family])}},
        }
        for slot, family in SLOT_FAMILY.items()
        if pools.get(family)
    ]
    choices: dict[str, Any] = {"type": "array", "minItems": len(branches), "maxItems": len(branches)}
    if branches:
        choices["items"] = {"anyOf": branches}
    return {**_CHOICE_SCHEMA, "properties": {"choices": choices}}


def _ask(provider: Provider, *, name: str, map_id: str, latitude: float, longitude: float, brief: str | None, rows: Sequence[Mapping[str, Any]], complaint: str | None,
         extended: bool) -> dict[str, str]:
    pools = _pools(rows, extended=extended)
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
    prompt = json.dumps(instruction, ensure_ascii=False)  # pragma: no mutate: None is falsy like False and writes the same JSON
    result = provider.complete(system, prompt, choice_schema(pools))
    return _parse_choices(result.text, rows, extended=extended)


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
    extended: bool = False,
    wide: bool = False,
) -> dict[str, Any]:
    """``extended`` chooses roofs and paving from their calm pools too (the map.json "repetition" switch); ``wide``
    adds the wide library to every pool (the map.json "wide_library" switch, plan T1)."""
    rows = list(library) if library is not None else (catalogue_rows() if wide else library_rows())
    if provider is None:
        choices = deterministic_choices(rows, seed, extended=extended)
        return _document(name, choices, rows, selector="library", brief=brief, extended=extended)
    complaint: str | None = None
    for _attempt in range(2):
        try:
            choices = _ask(
                provider, name=name, map_id=map_id, latitude=latitude, longitude=longitude,
                brief=brief, rows=rows, complaint=complaint, extended=extended,
            )
            return _document(name, choices, rows, selector="ollama", brief=brief, extended=extended)
        except ProviderError as exc:
            complaint = str(exc)
            break
        except PlanError as exc:
            complaint = str(exc)
    choices = deterministic_choices(rows, seed, extended=extended)
    document = _document(name, choices, rows, selector="library", brief=brief, extended=extended)
    document["description"] += f" The model reply was rejected: {complaint}"
    return document


def assert_library_entries(
    document: Mapping[str, Any], rows: Sequence[Mapping[str, Any]] | None = None, *, extended: bool = False,
) -> None:
    catalog = list(rows) if rows is not None else catalogue_rows()
    by_id = {str(row["id"]): row for row in catalog}
    seen: set[str] = set()
    for raw in document["entries"]:
        library_id = str(raw.get("library_id") or "")
        slot = str(raw["id"])
        if not library_id:
            raise PlanError(f"{slot} has no library texture")
        row = by_id.get(library_id)
        if row is None or texture_family(row) != SLOT_FAMILY.get(slot):
            raise PlanError(f"{slot} cannot use {library_id}")
        if not is_calm(row, extended=extended):
            raise PlanError(
                f"{slot} cannot use {library_id}: its tile repeats visibly. "
                "Run `map create --name <map> --textures` to choose this map's textures again"
            )
        if library_id in seen:
            raise PlanError(f"{library_id} is used more than once")
        seen.add(library_id)


def repetition_rows(applied: Sequence[Mapping[str, Any]], paths: Mapping[str, Path]) -> list[dict[str, Any]]:
    """One row per applied library slot: its coarse contrast against the extended family limit.

    ``paths`` maps an applied asset id to the image file that was applied. A door
    is ``not_applicable`` (mapped 0-1 per opening, never measured); a family with no limit,
    such as bark, is ``unmeasured``.
    """
    rows: list[dict[str, Any]] = []
    for row in applied:
        asset_id = str(row["asset_id"])
        family = SLOT_FAMILY.get(str(row["id"]))
        limit = contrast_limit(family, extended=True)
        contrast: float | None = None
        over = False
        if family in NOT_APPLICABLE_FAMILIES:
            status, limit = "not_applicable", None
        elif limit is None:
            status = "unmeasured"
        else:
            status = "measured"
            measured = coarse_contrast(paths[asset_id])
            # The verdict is is_calm's, on the unrounded value; the report shows 4 places.
            contrast, over = round(measured, 4), measured > limit
        rows.append({
            "slot": row["id"],
            "color_id": row["color_id"],
            "asset_id": asset_id,
            "family": family,
            "coarse_contrast": contrast,
            "limit": limit,
            "over_limit": over,
            "status": status,
        })
    return rows
