"""Explicit catalogue of materials the classifier may recommend."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from onward_generator.config.loader import ConfigLoadError, read_json_document
from onward_generator.detail.config import DEFAULT_CONFIG_PATH as DETAIL_DEFAULT
from onward_generator.generation.config import DEFAULT_CONFIG_PATH as GENERATION_DEFAULT

REPO_ROOT = Path(__file__).resolve().parents[3]
CATALOG_PATH = REPO_ROOT / "config" / "dress" / "materials.catalog.json"
CATALOG_SCHEMA_PATH = REPO_ROOT / "config" / "dress" / "materials.catalog.schema.json"

ROLES = (
    "wall", "roof", "ground", "sidewalk", "street", "plaza", "curb",
    "water", "green", "opening", "accessory", "other",
)

SAFE_BY_ROLE: dict[str, str] = {
    "wall": "plaster_white_wall",
    "roof": "flat_roof_grey",
    "ground": "ground_neutral",
    "sidewalk": "sidewalk_concrete",
    "street": "asphalt_road",
    "plaza": "cobblestone_paving",
    "curb": "curb_stone",
    "water": "water_surface",
    "green": "green_vegetation",
    "opening": "frame_stone_trim",
    "accessory": "urban_metal_neutral",
    "other": "ground_neutral",
}


class CatalogError(ValueError):
    """The catalogue file is missing, invalid, or points at a colour that does not exist."""


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    id: str
    name: str
    description: str
    compatible_surfaces: frozenset[str]
    color_id: str
    textures: Mapping[str, Any]
    technical: Mapping[str, Any]
    performance_cost: str
    source: str
    license: str
    status: str

    def for_model(self) -> dict[str, Any]:
        """Compact entry sent to a model. No invented paths; file lists stay empty unless real."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "compatible_surfaces": sorted(self.compatible_surfaces),
            "status": self.status,
            "textures": {
                "kind": self.textures["kind"],
                "available": bool(self.textures.get("paths")),
            },
        }


@dataclass(frozen=True, slots=True)
class MaterialCatalog:
    path: Path
    sha256: str
    entries: tuple[CatalogEntry, ...]

    @property
    def by_id(self) -> dict[str, CatalogEntry]:
        return {e.id: e for e in self.entries}

    @property
    def ids(self) -> frozenset[str]:
        return frozenset(e.id for e in self.entries)

    def get(self, material_id: str) -> CatalogEntry | None:
        return self.by_id.get(material_id)

    def compatible(self, material_id: str, role: str) -> bool:
        entry = self.get(material_id)
        return entry is not None and role in entry.compatible_surfaces

    def safe_id(self, role: str) -> str:
        chosen = SAFE_BY_ROLE.get(role, SAFE_BY_ROLE["other"])
        if chosen not in self.ids:
            raise CatalogError(f"safe material {chosen!r} for role {role!r} is missing from the catalogue")
        return chosen

    def model_payload(self) -> list[dict[str, Any]]:
        return [e.for_model() for e in self.entries]


@cache
def _schema_validator() -> Draft202012Validator:
    schema = json.loads(CATALOG_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _known_color_ids() -> frozenset[str]:
    known: set[str] = set()
    for path in (GENERATION_DEFAULT, DETAIL_DEFAULT):
        doc = json.loads(path.read_text(encoding="utf-8"))
        known.update(doc.get("materials", {}))
    return frozenset(known)


def load_catalog(path: str | Path | None = None) -> MaterialCatalog:
    source = Path(path).expanduser() if path else CATALOG_PATH
    try:
        doc, resolved = read_json_document(source)
    except ConfigLoadError as exc:
        raise CatalogError(str(exc)) from exc
    errors = sorted(_schema_validator().iter_errors(doc), key=lambda e: (list(e.absolute_path), e.message))
    if errors:
        raise CatalogError(f"{resolved}: " + "; ".join(e.message for e in errors))
    seen: set[str] = set()
    colors = _known_color_ids()
    entries: list[CatalogEntry] = []
    for raw in doc["materials"]:
        mid = raw["id"]
        if mid in seen:
            raise CatalogError(f"{resolved}: duplicate material id {mid!r}")
        seen.add(mid)
        if raw["color_id"] not in colors:
            raise CatalogError(
                f"{resolved}: {mid} color_id {raw['color_id']!r} is not defined in "
                "generation.default.json or detail.default.json"
            )
        if raw["textures"]["kind"] == "file" and not raw["textures"]["paths"]:
            raise CatalogError(f"{resolved}: {mid} textures.kind is file but paths is empty")
        if raw["textures"]["kind"] != "file" and raw["textures"]["paths"]:
            raise CatalogError(f"{resolved}: {mid} lists texture paths but kind is {raw['textures']['kind']}")
        for p in raw["textures"]["paths"]:
            if not (REPO_ROOT / p).is_file():
                raise CatalogError(f"{resolved}: {mid} texture file does not exist: {p}")
        entries.append(
            CatalogEntry(
                id=mid,
                name=raw["name"],
                description=raw["description"],
                compatible_surfaces=frozenset(raw["compatible_surfaces"]),
                color_id=raw["color_id"],
                textures=raw["textures"],
                technical=raw["technical"],
                performance_cost=raw["performance_cost"],
                source=raw["source"],
                license=raw["license"],
                status=raw["status"],
            )
        )
    digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
    return MaterialCatalog(resolved, digest, tuple(entries))
