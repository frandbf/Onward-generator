"""Schema and semantic validation of project configurations."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from onward_generator.config.diagnostics import Diagnostic, has_errors
from onward_generator.config.loader import ConfigLoadError, read_json_document
from onward_generator.config.models import GeneratorConfig

SCHEMA_ENV = "ONWARD_GENERATOR_SCHEMA"
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SCHEMA_PATH = REPO_ROOT / "config" / "generator.schema.json"

_NOT_IMPLEMENTED_STAGES = ("buildings", "roads", "sidewalks", "facades", "interiors")


def schema_path() -> Path:
    override = os.environ.get(SCHEMA_ENV)
    return Path(override).expanduser() if override else DEFAULT_SCHEMA_PATH


@cache
def _validator(path: Path) -> Draft202012Validator:
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def load_schema_validator() -> Draft202012Validator:
    return _validator(schema_path().resolve())


def _pointer(parts: Any) -> str:
    return "".join(f"/{p}" for p in parts)


def schema_diagnostics(doc: dict[str, Any]) -> list[Diagnostic]:
    errors = sorted(load_schema_validator().iter_errors(doc), key=lambda e: (list(map(str, e.absolute_path)), e.message))
    return [Diagnostic("error", f"schema_{e.validator}", _pointer(e.absolute_path), e.message) for e in errors]


def semantic_diagnostics(config: GeneratorConfig) -> list[Diagnostic]:
    """Rules that JSON Schema cannot express."""
    out: list[Diagnostic] = []
    area = config.area
    lon, lat = area.center_lon, area.center_lat

    if (lon is None) != (lat is None):
        out.append(Diagnostic("error", "center_incomplete", "/area/center", "latitude and longitude must both be set or both be null"))
    if area.center is None and area.boundary_file is None:
        out.append(Diagnostic("warning", "area_undefined", "/area", "no center and no boundary_file; the geo pipeline cannot run"))

    if area.center is not None:
        from onward_generator.geo.projection import LocalFrame, ProjectionError

        try:
            LocalFrame.create(*area.center, area.crs)
        except ProjectionError as exc:
            out.append(Diagnostic("error", "center_outside_crs", "/area/center", str(exc)))

    if area.boundary_file is not None:
        out.extend(_boundary_diagnostics(config))

    if config.export.enabled:
        out.append(
            Diagnostic(
                "warning",
                "export_adapter_unverified",
                "/export/enabled",
                "the Onward export adapter (onward_unity 0.1.0) has not passed the Quest acceptance test yet "
                "(docs/12-export.md); exported maps are for device testing only",
            )
        )
    enabled = [name for name in _NOT_IMPLEMENTED_STAGES if getattr(config.generation, name)]
    if enabled:
        out.append(
            Diagnostic("info", "stage_not_implemented", "/generation", f"enabled but not implemented yet: {', '.join(enabled)}")
        )
    return out


def _boundary_diagnostics(config: GeneratorConfig) -> list[Diagnostic]:
    from shapely import Point, Polygon

    from onward_generator.geo.aoi import AoiError, load_aoi_geojson

    path = config.area.boundary_file
    assert path is not None
    if not path.is_file():
        return [Diagnostic("error", "boundary_not_found", "/area/boundary_file", f"file not found: {path}")]
    try:
        aoi = load_aoi_geojson(path)
    except AoiError as exc:
        return [Diagnostic("error", "boundary_invalid", "/area/boundary_file", str(exc))]

    out: list[Diagnostic] = []
    if not Polygon(aoi.polygon).is_valid:
        out.append(Diagnostic("error", "boundary_invalid", "/area/boundary_file", "boundary polygon is not a valid polygon"))
    if aoi.margin_m != config.area.margin_m:
        out.append(
            Diagnostic(
                "warning",
                "margin_mismatch",
                "/area/margin_m",
                f"configuration margin {config.area.margin_m} m differs from the boundary file margin {aoi.margin_m} m; the configuration wins",
            )
        )
    center = config.area.center
    if center is not None and not Polygon(aoi.polygon).covers(Point(center)):
        out.append(Diagnostic("warning", "center_outside_boundary", "/area/center", "center lies outside the boundary polygon"))
    return out


@dataclass(frozen=True, slots=True)
class ValidationResult:
    path: Path
    config: GeneratorConfig | None
    diagnostics: tuple[Diagnostic, ...]

    @property
    def valid(self) -> bool:
        return self.config is not None and not has_errors(self.diagnostics)

    def to_dict(self) -> dict[str, Any]:
        return {"path": str(self.path), "valid": self.valid, "diagnostics": [d.to_dict() for d in self.diagnostics]}


def validate_config_file(path: str | Path) -> ValidationResult:
    """Load, schema-check and semantically check a configuration file."""
    try:
        doc, resolved = read_json_document(path)
    except ConfigLoadError as exc:
        return ValidationResult(Path(path), None, (exc.diagnostic,))
    schema = schema_diagnostics(doc)
    if schema:
        return ValidationResult(resolved, None, tuple(schema))
    config = GeneratorConfig.from_document(doc, resolved)
    return ValidationResult(resolved, config, tuple(semantic_diagnostics(config)))


class ConfigValidationError(ValueError):
    def __init__(self, result: ValidationResult) -> None:
        errors = [str(d) for d in result.diagnostics if d.severity == "error"]
        super().__init__(f"invalid configuration {result.path}:\n" + "\n".join(errors))
        self.result = result


def load_config(path: str | Path) -> GeneratorConfig:
    """Return a validated configuration or raise with every error."""
    result = validate_config_file(path)
    if not result.valid or result.config is None:
        raise ConfigValidationError(result)
    return result.config
