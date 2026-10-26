"""Explicit catalogue of Asset Store props. Missing files stay pending."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from onward_generator.config.loader import ConfigLoadError, read_json_document

REPO_ROOT = Path(__file__).resolve().parents[3]
CATALOG_PATH = REPO_ROOT / "config" / "props" / "catalog.json"
CATALOG_SCHEMA_PATH = REPO_ROOT / "config" / "props" / "catalog.schema.json"

CATEGORIES = ("defensive", "vehicle", "military", "ambient")
_PROP_ID = re.compile(r"^[a-z][a-z0-9_]{1,62}$")
_MESH_SUFFIXES = frozenset({".prefab", ".fbx", ".obj", ".blend"})


class CatalogError(ValueError):
    """The catalogue is missing, invalid, or an asset_store path is not a file."""


@dataclass(frozen=True, slots=True)
class PackRecord:
    id: str
    name: str
    url: str
    license: str
    license_url: str
    license_text: str
    category: str
    include_name_any: tuple[str, ...]
    exclude_name_any: tuple[str, ...]
    status: str


@dataclass(frozen=True, slots=True)
class PropEntry:
    prop_id: str
    category: str
    pack_id: str
    source: str
    path: str
    license: str
    lods: Mapping[str, str]
    has_collision: bool
    allowed_origin: tuple[str, ...]
    notes: str

    @property
    def resolved_path(self) -> Path | None:
        if not self.path:
            return None
        return Path(self.path).expanduser()

    @property
    def usable(self) -> bool:
        if self.source != "asset_store":
            return False
        path = self.resolved_path
        return path is not None and path.is_file()


@dataclass(frozen=True, slots=True)
class PropCatalog:
    path: Path
    sha256: str
    packs: tuple[PackRecord, ...]
    props: tuple[PropEntry, ...]

    @property
    def packs_by_id(self) -> dict[str, PackRecord]:
        return {p.id: p for p in self.packs}

    @property
    def usable_props(self) -> tuple[PropEntry, ...]:
        return tuple(p for p in self.props if p.usable)


@cache
def _schema_validator() -> Draft202012Validator:
    schema = json.loads(CATALOG_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _slug(name: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", Path(name).stem.lower()).strip("_")
    return text or "prop"


def pack_allows(pack: PackRecord, filename: str) -> bool:
    """Name filter: European City buildings stay out; only listed furniture/cars/trees enter."""
    lowered = Path(filename).stem.lower()
    if pack.exclude_name_any and any(token in lowered for token in pack.exclude_name_any):
        return False
    if not pack.include_name_any:
        return False
    return any(token in lowered for token in pack.include_name_any)


def _entry_from_raw(raw: Mapping[str, Any], packs: Mapping[str, PackRecord], catalog_path: Path) -> PropEntry:
    pack_id = raw["pack_id"]
    if pack_id not in packs:
        raise CatalogError(f"{catalog_path}: prop {raw['prop_id']!r} pack_id {pack_id!r} is not in packs")
    pack = packs[pack_id]
    source = raw["source"]
    path = raw["path"]
    if source == "asset_store":
        if not path:
            raise CatalogError(f"{catalog_path}: {raw['prop_id']} source is asset_store but path is empty")
        if not pack.license_text.strip():
            raise CatalogError(f"{catalog_path}: pack {pack_id} is missing license_text")
        resolved = Path(path).expanduser()
        if not resolved.is_file():
            raise CatalogError(f"{catalog_path}: {raw['prop_id']} asset_store file does not exist: {path}")
    elif path and source == "pending":
        # Expected location after import; the file may not exist yet.
        pass
    return PropEntry(
        prop_id=raw["prop_id"],
        category=raw["category"],
        pack_id=pack_id,
        source=source,
        path=path,
        license=raw["license"],
        lods=dict(raw["lods"]),
        has_collision=raw["has_collision"],
        allowed_origin=tuple(raw["allowed_origin"]),
        notes=raw["notes"],
    )


def load_catalog(path: str | Path | None = None) -> PropCatalog:
    source = Path(path).expanduser() if path else CATALOG_PATH
    try:
        doc, resolved = read_json_document(source)
    except ConfigLoadError as exc:
        raise CatalogError(str(exc)) from exc
    errors = sorted(_schema_validator().iter_errors(doc), key=lambda e: (list(e.absolute_path), e.message))
    if errors:
        raise CatalogError(f"{resolved}: " + "; ".join(e.message for e in errors))
    seen_packs: set[str] = set()
    packs: list[PackRecord] = []
    for raw in doc["packs"]:
        if raw["id"] in seen_packs:
            raise CatalogError(f"{resolved}: duplicate pack id {raw['id']!r}")
        seen_packs.add(raw["id"])
        if raw["status"] == "approved" and not raw["license_text"].strip():
            raise CatalogError(f"{resolved}: pack {raw['id']} is approved but license_text is empty")
        packs.append(
            PackRecord(
                id=raw["id"],
                name=raw["name"],
                url=raw["url"],
                license=raw["license"],
                license_url=raw["license_url"],
                license_text=raw["license_text"],
                category=raw["category"],
                include_name_any=tuple(t.lower() for t in raw["include_name_any"]),
                exclude_name_any=tuple(t.lower() for t in raw["exclude_name_any"]),
                status=raw["status"],
            )
        )
    pack_map = {p.id: p for p in packs}
    seen_props: set[str] = set()
    props: list[PropEntry] = []
    for raw in doc["props"]:
        pid = raw["prop_id"]
        if pid in seen_props:
            raise CatalogError(f"{resolved}: duplicate prop_id {pid!r}")
        seen_props.add(pid)
        props.append(_entry_from_raw(raw, pack_map, resolved))
    digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
    return PropCatalog(resolved, digest, tuple(packs), tuple(props))


def bind_inventory(
    catalog: PropCatalog,
    assets: Sequence[Mapping[str, Any]],
    *,
    unity_root: str | Path | None = None,
) -> PropCatalog:
    """Turn an importer inventory into prop entries. Buildings that fail the pack filter are dropped."""
    root = Path(unity_root).expanduser() if unity_root else None
    seen: set[str] = set()
    bound: list[PropEntry] = []
    for asset in assets:
        pack_id = str(asset["pack_id"])
        pack = catalog.packs_by_id.get(pack_id)
        if pack is None or pack.status != "approved":
            continue
        rel = str(asset["path"])
        if not pack_allows(pack, rel):
            continue
        suffix = Path(rel).suffix.lower()
        if suffix not in _MESH_SUFFIXES:
            continue
        slug = _slug(Path(rel).name)
        prop_id = f"p{pack_id}_{slug}"
        if len(prop_id) > 63:
            prop_id = prop_id[:63].rstrip("_")
        if not _PROP_ID.match(prop_id) or prop_id in seen:
            prop_id = f"p{pack_id}_{len(seen):03d}_{slug}"[:63].rstrip("_")
        seen.add(prop_id)
        abs_path = str((root / rel).expanduser()) if root and not Path(rel).is_absolute() else rel
        source = "asset_store" if Path(abs_path).expanduser().is_file() else "pending"
        bound.append(
            PropEntry(
                prop_id=prop_id,
                category=pack.category,
                pack_id=pack.id,
                source=source,
                path=abs_path if source == "asset_store" else abs_path,
                license=pack.license,
                lods={"lod0": abs_path} if source == "asset_store" else {},
                has_collision=False,
                allowed_origin=("osm", "placed") if pack.category == "vehicle" else ("osm", "placed", "generated"),
                notes=(
                    "Static vehicle; OSM parking evidence required."
                    if pack.category == "vehicle"
                    else "Ambient prop; do not replace OSM footprints."
                ),
            )
        )
    return PropCatalog(catalog.path, catalog.sha256, catalog.packs, tuple(bound))


def catalog_document(catalog: PropCatalog) -> dict[str, Any]:
    return {
        "schema": "onward_generator.prop_catalog",
        "schema_version": "0.1.0",
        "description": (
            "Bound Asset Store props. source asset_store only when the file exists. "
            "Not instanced until a later props increment."
        ),
        "packs": [
            {
                "id": p.id,
                "name": p.name,
                "url": p.url,
                "license": p.license,
                "license_url": p.license_url,
                "license_text": p.license_text,
                "category": p.category,
                "include_name_any": list(p.include_name_any),
                "exclude_name_any": list(p.exclude_name_any),
                "status": p.status,
            }
            for p in catalog.packs
        ],
        "props": [
            {
                "prop_id": e.prop_id,
                "category": e.category,
                "pack_id": e.pack_id,
                "source": e.source,
                "path": e.path,
                "license": e.license,
                "lods": dict(e.lods),
                "has_collision": e.has_collision,
                "allowed_origin": list(e.allowed_origin),
                "notes": e.notes,
            }
            for e in catalog.props
        ],
    }
