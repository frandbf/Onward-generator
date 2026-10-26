"""Validated configuration for phase 03 geometry generation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from types import MappingProxyType
from typing import Any

from jsonschema import Draft202012Validator

from onward_generator.config.loader import ConfigLoadError, read_json_document

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "generation.default.json"
SCHEMA_PATH = REPO_ROOT / "config" / "generation.schema.json"


class GenerationConfigError(ValueError):
    def __init__(self, path: Path | str, problems: list[str]) -> None:
        super().__init__(f"{path}: " + "; ".join(problems))
        self.problems = problems


@dataclass(frozen=True, slots=True)
class Material:
    color: tuple[float, float, float, float]
    roughness: float


@dataclass(frozen=True, slots=True)
class GenerationConfig:
    version: str
    vehicular: frozenset[str]
    pedestrian: frozenset[str]
    default_width_vehicular_m: float
    default_width_pedestrian_m: float
    sidewalk_width_m: float
    carriageway_depth_m: float
    surface_tags: Mapping[str, Mapping[str, frozenset[str]]]
    min_surface_area_m2: float
    precision_m: float
    collider_simplify_m: float
    ground_thickness_m: float
    overlap_warning_m2: float
    materials: Mapping[str, Material]
    document: Mapping[str, Any]
    path: str | None = None
    sha256: str | None = None


@cache
def _validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def config_from_document(doc: Mapping[str, Any], *, path: str | None = None, sha256: str | None = None) -> GenerationConfig:
    errors = sorted(_validator().iter_errors(doc), key=lambda e: (list(e.absolute_path), e.message))
    problems = [f"/{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in errors]
    if not problems:
        both = set(doc["road_classes"]["vehicular"]) & set(doc["road_classes"]["pedestrian"])
        if both:
            problems.append(f"/road_classes: {sorted(both)} listed as both vehicular and pedestrian")
    if problems:
        raise GenerationConfigError(path or "<document>", problems)
    return GenerationConfig(
        version=doc["version"],
        vehicular=frozenset(doc["road_classes"]["vehicular"]),
        pedestrian=frozenset(doc["road_classes"]["pedestrian"]),
        default_width_vehicular_m=float(doc["default_width_m"]["vehicular"]),
        default_width_pedestrian_m=float(doc["default_width_m"]["pedestrian"]),
        sidewalk_width_m=float(doc["sidewalk_width_m"]),
        carriageway_depth_m=float(doc["carriageway_depth_m"]),
        surface_tags=MappingProxyType(
            {cls: MappingProxyType({k: frozenset(v) for k, v in tags.items()}) for cls, tags in doc["surface_tags"].items()}
        ),
        min_surface_area_m2=float(doc["min_surface_area_m2"]),
        precision_m=float(doc["precision_m"]),
        collider_simplify_m=float(doc["collider"]["simplify_tolerance_m"]),
        ground_thickness_m=float(doc["collider"]["ground_thickness_m"]),
        overlap_warning_m2=float(doc["overlap_warning_m2"]),
        materials=MappingProxyType(
            {name: Material(tuple(m["color"]), float(m["roughness"])) for name, m in sorted(doc["materials"].items())}  # type: ignore[arg-type]
        ),
        document=MappingProxyType(json.loads(json.dumps(doc))),
        path=path,
        sha256=sha256,
    )


def load_generation_config(path: str | Path | None = None) -> GenerationConfig:
    try:
        doc, resolved = read_json_document(path or DEFAULT_CONFIG_PATH)
    except ConfigLoadError as exc:
        raise GenerationConfigError(path or DEFAULT_CONFIG_PATH, [exc.diagnostic.message]) from exc
    return config_from_document(doc, path=str(resolved), sha256=hashlib.sha256(resolved.read_bytes()).hexdigest())
