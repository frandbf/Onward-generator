"""Typed, immutable view of a validated project configuration (PRD §8.4)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

DetailLevel = Literal["low", "medium", "high"]


@dataclass(frozen=True, slots=True)
class ProjectInfo:
    name: str
    version: str
    target: Literal["onward_quest"]


@dataclass(frozen=True, slots=True)
class SourceConfig:
    provider: Literal["openstreetmap"]
    attribution_required: bool


@dataclass(frozen=True, slots=True)
class AreaConfig:
    center_lon: float | None
    center_lat: float | None
    boundary_file: Path | None
    """Resolved against the configuration file's directory."""
    margin_m: float
    crs: str = "EPSG:25830"

    @property
    def center(self) -> tuple[float, float] | None:
        """``(longitude, latitude)`` when both are set."""
        if self.center_lon is None or self.center_lat is None:
            return None
        return (self.center_lon, self.center_lat)


@dataclass(frozen=True, slots=True)
class GenerationConfig:
    buildings: bool
    roads: bool
    sidewalks: bool
    facades: bool
    interiors: bool


@dataclass(frozen=True, slots=True)
class QualityConfig:
    detail_level: DetailLevel
    deterministic_seed: int


@dataclass(frozen=True, slots=True)
class OptimizationConfig:
    target: Literal["quest_standalone"]
    generate_collisions: bool
    generate_lods: bool


@dataclass(frozen=True, slots=True)
class ExportConfig:
    adapter: Literal["onward"]
    enabled: bool


@dataclass(frozen=True, slots=True)
class GeneratorConfig:
    project: ProjectInfo
    source: SourceConfig
    area: AreaConfig
    generation: GenerationConfig
    quality: QualityConfig
    optimization: OptimizationConfig
    export: ExportConfig
    config_path: Path

    @classmethod
    def from_document(cls, doc: dict[str, Any], config_path: Path) -> GeneratorConfig:
        """Build from a document that already passed schema validation."""
        area = doc["area"]
        boundary = area["boundary_file"]
        return cls(
            project=ProjectInfo(**doc["project"]),
            source=SourceConfig(**doc["source"]),
            area=AreaConfig(
                center_lon=_optional_float(area["center"]["longitude"]),
                center_lat=_optional_float(area["center"]["latitude"]),
                boundary_file=(config_path.parent / boundary).resolve() if boundary else None,
                margin_m=float(area["margin_m"]),
                crs=str(area.get("crs") or "EPSG:25830"),
            ),
            generation=GenerationConfig(**doc["generation"]),
            quality=QualityConfig(**doc["quality"]),
            optimization=OptimizationConfig(**doc["optimization"]),
            export=ExportConfig(**doc["export"]),
            config_path=config_path,
        )


def _optional_float(value: float | int | None) -> float | None:
    return None if value is None else float(value)
