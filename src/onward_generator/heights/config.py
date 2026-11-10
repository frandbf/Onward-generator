"""Validated configuration for building-height estimation."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from types import MappingProxyType
from typing import Any

from jsonschema import Draft202012Validator

from onward_generator.config.loader import MAX_SCENE_BYTES, ConfigLoadError, read_json_document
from onward_generator.io import sha256_file as _sha256

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "heights.default.json"
CONFIG_SCHEMA_PATH = REPO_ROOT / "config" / "heights.schema.json"
OVERRIDES_SCHEMA_PATH = REPO_ROOT / "config" / "heights.overrides.schema.json"
UNKNOWN = "unknown"
WILDCARD = "*"


class HeightConfigError(ValueError):
    """The configuration or overrides file is invalid; ``problems`` lists every reason."""

    def __init__(self, path: Path | str, problems: list[str]) -> None:
        super().__init__(f"{path}: " + "; ".join(problems))
        self.path = str(path)
        self.problems = problems


@dataclass(frozen=True, slots=True)
class HeightRange:
    default: float
    min: float
    max: float


@dataclass(frozen=True, slots=True)
class Override:
    id: str
    source: str
    license: str
    height_m: float | None = None
    levels: int | None = None
    confidence: float = 0.9
    retrieved_at: str | None = None
    note: str | None = None
    origin: str | None = None
    """``observed``, ``estimated`` or ``ai_generated``, when the source states it (Overture, ADR-021)."""


@dataclass(frozen=True, slots=True)
class FileRef:
    path: str
    sha256: str

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "sha256": self.sha256}


@dataclass(frozen=True, slots=True)
class HeightConfig:
    version: str
    bounds_min_m: float
    bounds_max_m: float
    storey_height_m: Mapping[str, float]
    fallback_m: Mapping[str, HeightRange]
    typical_levels: Mapping[str, int]
    category_key_priority: tuple[str, ...]
    categories: Mapping[str, Mapping[str, str]]
    landmark_keys: tuple[str, ...]
    landmark_tags: Mapping[str, frozenset[str]]
    landmark_levels_floor: bool
    radius_m: float
    block_gap_m: float
    min_samples: int
    reliable_sources: frozenset[str]
    confidence: Mapping[str, float]
    overrides: Mapping[str, Override]
    document: Mapping[str, Any]
    file: FileRef | None = None
    overrides_file: FileRef | None = None
    secondary_overrides: Mapping[str, Override] = MappingProxyType({})
    """Third-party values (Overture): below an OSM ``height`` and authoritative overrides, above ``building:levels``."""
    secondary_overrides_file: FileRef | None = None

    @property
    def category_names(self) -> frozenset[str]:
        return frozenset(self.fallback_m)

    def storey_height(self, category: str) -> float:
        return self.storey_height_m[category]


@cache
def _validator(schema_path: Path) -> Draft202012Validator:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _schema_problems(doc: Mapping[str, Any], schema_path: Path) -> list[str]:
    errors = sorted(_validator(schema_path).iter_errors(doc), key=lambda e: (list(e.absolute_path), e.message))
    return [f"/{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in errors]


def _semantic_problems(doc: Mapping[str, Any]) -> list[str]:
    problems: list[str] = []
    categories = set(doc["fallback_m"])
    bounds = doc["bounds_m"]
    if bounds["min"] >= bounds["max"]:
        problems.append("/bounds_m: min must be below max")
    missing_storey = categories - set(doc["storey_height_m"])
    if missing_storey:
        problems.append(f"/storey_height_m: missing categories {sorted(missing_storey)}")
    for name, section in (("storey_height_m", doc["storey_height_m"]), ("typical_levels", doc["typical_levels"])):
        unknown = set(section) - categories
        if unknown:
            problems.append(f"/{name}: categories {sorted(unknown)} have no fallback_m entry")
    for name, r in doc["fallback_m"].items():
        if not r["min"] <= r["default"] <= r["max"]:
            problems.append(f"/fallback_m/{name}: default must lie within [min, max]")
        if r["min"] < bounds["min"] or r["max"] > bounds["max"]:
            problems.append(f"/fallback_m/{name}: range must lie within bounds_m")
    for key, mapping in doc["categories"].items():
        if key not in doc["category_key_priority"]:
            problems.append(f"/categories/{key}: key is not listed in category_key_priority")
        for value, category in mapping.items():
            if category not in categories:
                problems.append(f"/categories/{key}/{value}: unknown category {category!r}")
    return problems


def _load_overrides(path: Path, bounds: tuple[float, float]) -> tuple[dict[str, Override], FileRef]:
    # An overrides file is pipeline output (one entry per building from the source plugins),
    # not hand-written configuration: Catastro on Madrid r300 writes about 1.2 MB of it.
    try:
        doc, resolved = read_json_document(path, max_bytes=MAX_SCENE_BYTES)
    except ConfigLoadError as exc:
        raise HeightConfigError(path, [exc.diagnostic.message]) from exc
    problems = _schema_problems(doc, OVERRIDES_SCHEMA_PATH)
    if problems:
        raise HeightConfigError(resolved, problems)
    overrides: dict[str, Override] = {}
    for i, entry in enumerate(doc["overrides"]):
        if entry["id"] in overrides:
            problems.append(f"/overrides/{i}: duplicate id {entry['id']}")
        if "height_m" in entry and not bounds[0] <= entry["height_m"] <= bounds[1]:
            problems.append(f"/overrides/{i}: height_m {entry['height_m']} outside bounds_m {list(bounds)}")
        overrides[entry["id"]] = Override(**entry)
    if problems:
        raise HeightConfigError(resolved, problems)
    return overrides, FileRef(str(resolved), _sha256(resolved))


def config_from_document(
    doc: Mapping[str, Any],
    *,
    base_dir: Path | None = None,
    file: FileRef | None = None,
    overrides_path: str | Path | None = None,
    secondary_overrides_path: str | Path | None = None,
) -> HeightConfig:
    """Validate a configuration document (schema + cross-field rules) and build the typed config.

    ``overrides_path`` replaces the document's ``overrides_file``. ``secondary_overrides_path`` adds
    third-party overrides ranked below it (docs/04-heights.md).
    """
    origin = file.path if file else "<document>"
    problems = _schema_problems(doc, CONFIG_SCHEMA_PATH)
    if not problems:
        problems = _semantic_problems(doc)
    if problems:
        raise HeightConfigError(origin, problems)

    bounds = (float(doc["bounds_m"]["min"]), float(doc["bounds_m"]["max"]))
    overrides: dict[str, Override] = {}
    overrides_ref: FileRef | None = None
    if overrides_path is not None:
        doc = {**doc, "overrides_file": str(Path(overrides_path).expanduser().resolve())}
    if doc["overrides_file"] is not None:
        resolved_overrides = Path(doc["overrides_file"]).expanduser()
        if not resolved_overrides.is_absolute():
            resolved_overrides = (base_dir or Path.cwd()) / resolved_overrides
        overrides, overrides_ref = _load_overrides(resolved_overrides, bounds)
    secondary: dict[str, Override] = {}
    secondary_ref: FileRef | None = None
    if secondary_overrides_path is not None:
        secondary, secondary_ref = _load_overrides(Path(secondary_overrides_path).expanduser().resolve(), bounds)

    return HeightConfig(
        version=doc["version"],
        bounds_min_m=bounds[0],
        bounds_max_m=bounds[1],
        storey_height_m=MappingProxyType({k: float(v) for k, v in doc["storey_height_m"].items()}),
        fallback_m=MappingProxyType({k: HeightRange(float(v["default"]), float(v["min"]), float(v["max"])) for k, v in doc["fallback_m"].items()}),
        typical_levels=MappingProxyType(dict(doc["typical_levels"])),
        category_key_priority=tuple(doc["category_key_priority"]),
        categories=MappingProxyType({k: MappingProxyType(dict(v)) for k, v in doc["categories"].items()}),
        landmark_keys=tuple(doc["landmark"]["keys"]),
        landmark_tags=MappingProxyType({k: frozenset(v) for k, v in doc["landmark"]["tags"].items()}),
        landmark_levels_floor=bool(doc["landmark"]["levels_floor_at_category_default"]),
        radius_m=float(doc["neighbourhood"]["radius_m"]),
        block_gap_m=float(doc["neighbourhood"]["block_gap_m"]),
        min_samples=int(doc["neighbourhood"]["min_samples"]),
        reliable_sources=frozenset(doc["neighbourhood"]["reliable_sources"]),
        confidence=MappingProxyType({k: float(v) for k, v in doc["confidence"].items()}),
        overrides=MappingProxyType(overrides),
        document=MappingProxyType(json.loads(json.dumps(doc))),
        file=file,
        overrides_file=overrides_ref,
        secondary_overrides=MappingProxyType(secondary),
        secondary_overrides_file=secondary_ref,
    )


def load_height_config(
    path: str | Path | None = None, *, overrides_path: str | Path | None = None, secondary_overrides_path: str | Path | None = None,
) -> HeightConfig:
    """Load a configuration file (default: ``config/heights.default.json``)."""
    try:
        doc, resolved = read_json_document(path or DEFAULT_CONFIG_PATH)
    except ConfigLoadError as exc:
        raise HeightConfigError(path or DEFAULT_CONFIG_PATH, [exc.diagnostic.message]) from exc
    return config_from_document(
        doc, base_dir=resolved.parent, file=FileRef(str(resolved), _sha256(resolved)), overrides_path=overrides_path,
        secondary_overrides_path=secondary_overrides_path,
    )
