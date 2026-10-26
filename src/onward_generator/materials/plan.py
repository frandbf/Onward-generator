"""Validated texture-plan JSON: URL → catalogue IDs, not city-wide paint."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator

from onward_generator.config.loader import ConfigLoadError, read_json_document
from onward_generator.dress.catalog import MaterialCatalog, load_catalog

REPO_ROOT = Path(__file__).resolve().parents[3]
PLAN_SCHEMA_PATH = REPO_ROOT / "config" / "dress" / "texture_plan.schema.json"

AlbedoMode = Literal["greyscale_modulation", "color_albedo"]
SlotId = Literal[
    "historic_facades", "stone_monuments", "streets_plazas", "roofs", "brick",
    "carriageway", "sidewalks", "plain_pedestrian",     "doors", "trunks", "flat_roofs", "plain_facades", "ochre_facades",
]

SLOT_ALLOWED: dict[str, frozenset[str]] = {
    "historic_facades": frozenset({"historic_plaster_cream", "plaster_ochre_wall"}),
    "stone_monuments": frozenset({"limestone_warm", "stone_light_wall", "curb_stone"}),
    "streets_plazas": frozenset({"cobblestone_paving"}),
    "roofs": frozenset({"terracotta_roof"}),
    "brick": frozenset({"brick_red_wall"}),
    "carriageway": frozenset({"asphalt_road"}),
    "sidewalks": frozenset({"sidewalk_concrete"}),
    "plain_pedestrian": frozenset({"pedestrian_plain"}),
    "doors": frozenset({"wood_door"}),
    "trunks": frozenset({"tree_bark"}),
    "flat_roofs": frozenset({"flat_roof_grey", "flat_roof_warm"}),
    "plain_facades": frozenset({"plaster_white_wall"}),
    "ochre_facades": frozenset({"plaster_ochre_wall"}),
}
# Murcia keeps greyscale on weathered photos. A generic plan may use the colour.
SLOT_MODES: dict[str, frozenset[str]] = {
    "historic_facades": frozenset({"greyscale_modulation", "color_albedo"}),
    "flat_roofs": frozenset({"greyscale_modulation", "color_albedo"}),
}
SLOT_FORBIDDEN: dict[str, frozenset[str]] = {
    "historic_facades": frozenset({"plaster_white_wall"}),
    "streets_plazas": frozenset({"asphalt_road"}),
    "roofs": frozenset({"flat_roof_grey", "flat_roof_warm"}),
    "brick": frozenset({"historic_plaster_cream", "plaster_ochre_wall", "plaster_white_wall"}),
}
SLOT_MODE: dict[str, AlbedoMode] = {
    "historic_facades": "greyscale_modulation",
    "stone_monuments": "color_albedo",
    "streets_plazas": "color_albedo",
    "roofs": "color_albedo",
    "brick": "color_albedo",
    "carriageway": "color_albedo",
    "sidewalks": "color_albedo",
    "plain_pedestrian": "color_albedo",
    "doors": "color_albedo",
    "trunks": "color_albedo",
    "flat_roofs": "greyscale_modulation",
    "plain_facades": "color_albedo",
    "ochre_facades": "color_albedo",
}


class PlanError(ValueError):
    """The texture plan is missing, invalid, or maps a texture too broadly."""


@dataclass(frozen=True, slots=True)
class TextureBinding:
    id: SlotId
    url: str
    catalog_ids: tuple[str, ...]
    albedo_mode: AlbedoMode
    forbidden_catalog_ids: tuple[str, ...]
    note: str
    library_id: str = ""


@dataclass(frozen=True, slots=True)
class TexturePlan:
    path: Path
    sha256: str
    description: str
    entries: tuple[TextureBinding, ...]
    atmosphere: str = "inhabited"

    @property
    def by_id(self) -> dict[str, TextureBinding]:
        return {e.id: e for e in self.entries}

    def binding_for_catalog(self, catalog_id: str) -> TextureBinding | None:
        for entry in self.entries:
            if catalog_id in entry.catalog_ids:
                return entry
        return None


@cache
def _schema_validator() -> Draft202012Validator:
    schema = json.loads(PLAN_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _validate_slots(doc: Mapping[str, Any], resolved: Path, catalog: MaterialCatalog) -> None:
    seen: set[str] = set()
    seen_catalog: set[str] = set()
    for raw in doc["entries"]:
        slot = raw["id"]
        if slot in seen:
            raise PlanError(f"{resolved}: duplicate slot {slot!r}")
        seen.add(slot)
        ids = list(raw["catalog_ids"])
        overlap = seen_catalog & set(ids)
        if overlap:
            raise PlanError(f"{resolved}: catalog ids bound more than once: {sorted(overlap)}")
        seen_catalog.update(ids)
        allowed = SLOT_ALLOWED[slot]
        extra = set(ids) - allowed
        if extra:
            raise PlanError(f"{resolved}: slot {slot} cannot bind {sorted(extra)}; allowed {sorted(allowed)}")
        forbidden = SLOT_FORBIDDEN.get(slot, frozenset())
        hit = set(ids) & forbidden
        if hit:
            raise PlanError(f"{resolved}: slot {slot} forbids {sorted(hit)}")
        listed_forbidden = set(raw.get("forbidden_catalog_ids") or ())
        if set(ids) & listed_forbidden:
            raise PlanError(f"{resolved}: slot {slot} lists a catalog id that it also forbids")
        allowed_modes = SLOT_MODES.get(slot, frozenset({SLOT_MODE[slot]}))
        if raw["albedo_mode"] not in allowed_modes:
            raise PlanError(
                f"{resolved}: slot {slot} albedo_mode must be one of {sorted(allowed_modes)}, not {raw['albedo_mode']!r}"
            )
        if slot == "brick" and set(ids) != {"brick_red_wall"}:
            raise PlanError(f"{resolved}: brick must bind only brick_red_wall, not every wall")
        if slot == "streets_plazas" and "asphalt_road" in ids:
            raise PlanError(f"{resolved}: cobble cannot map to asphalt_road")
        for mid in ids:
            if mid not in catalog.ids:
                raise PlanError(f"{resolved}: {mid} is not in the material catalogue")
    if doc.get("provider") == "library":
        from onward_generator.materials.library_plan import assert_library_entries
        assert_library_entries(doc)
    elif any(not raw.get("url") for raw in doc["entries"]):
        raise PlanError(f"{resolved}: a Poly Haven entry needs a page URL")


def load_texture_plan(
    path: str | Path | None = None,
    *,
    catalog: MaterialCatalog | None = None,
) -> TexturePlan:
    if path is None:
        raise PlanError("a texture plan path is required; each map keeps config/texture_plan.json")
    source = Path(path).expanduser()
    try:
        doc, resolved = read_json_document(source)
    except ConfigLoadError as exc:
        raise PlanError(str(exc)) from exc
    errors = sorted(_schema_validator().iter_errors(doc), key=lambda e: (list(e.absolute_path), e.message))
    if errors:
        raise PlanError(f"{resolved}: " + "; ".join(e.message for e in errors))
    cat = catalog or load_catalog()
    _validate_slots(doc, resolved, cat)
    entries = tuple(
            TextureBinding(
            id=raw["id"],
            url=str(raw.get("url") or ""),
            catalog_ids=tuple(raw["catalog_ids"]),
            albedo_mode=raw["albedo_mode"],
            forbidden_catalog_ids=tuple(raw.get("forbidden_catalog_ids") or ()),
            note=str(raw.get("note") or ""),
            library_id=str(raw.get("library_id") or ""),
        )
        for raw in doc["entries"]
    )
    digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
    atmosphere = str(doc.get("atmosphere") or "inhabited")
    if atmosphere not in {"inhabited", "abandoned"}:
        raise PlanError(f"{resolved}: atmosphere must be inhabited or abandoned")
    return TexturePlan(resolved, digest, str(doc["description"]), entries, atmosphere)


def plan_document(plan: TexturePlan) -> dict[str, Any]:
    return {
        "schema": "onward_generator.texture_plan",
        "schema_version": "0.1.0",
        "description": plan.description,
        "atmosphere": plan.atmosphere,
        "path": str(plan.path),
        "sha256": plan.sha256,
        "entries": [
            {
                "id": e.id,
                "url": e.url,
                "library_id": e.library_id,
                "catalog_ids": list(e.catalog_ids),
                "albedo_mode": e.albedo_mode,
                "forbidden_catalog_ids": list(e.forbidden_catalog_ids),
                "note": e.note,
            }
            for e in plan.entries
        ],
    }
