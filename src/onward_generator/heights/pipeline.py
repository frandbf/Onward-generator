"""File-level entry point: geo scene JSON → heights.json + heights_report.json."""

from __future__ import annotations

import hashlib
import json
import statistics
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from onward_generator.heights.config import HeightConfig, load_height_config
from onward_generator.heights.estimator import (
    HeightClass,
    HeightResult,
    HeightSource,
    SceneError,
    buildings_from_scene,
    estimate_heights,
)
from onward_generator.io import atomic_write_json, prepare_outputs

HEIGHTS_SCHEMA = "onward_generator.building_heights"
REPORT_SCHEMA = "onward_generator.building_heights_report"
SCHEMA_VERSION = "0.1.0"
HEIGHTS_FILE = "heights.json"
REPORT_FILE = "heights_report.json"
MAX_SCENE_BYTES = 256 * 1024 * 1024


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _percent(part: int, total: int) -> float:
    return round(100.0 * part / total, 2) if total else 0.0


def _stats(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    ordered = sorted(values)
    deciles = statistics.quantiles(ordered, n=10, method="inclusive") if len(ordered) > 1 else [ordered[0]] * 9
    return {
        "min": ordered[0],
        "p10": round(deciles[0], 2),
        "median": round(statistics.median(ordered), 2),
        "p90": round(deciles[-1], 2),
        "max": ordered[-1],
    }


@dataclass(frozen=True, slots=True)
class HeightRun:
    result: HeightResult
    config: HeightConfig
    scene_ref: Mapping[str, Any]
    attribution: Mapping[str, Any] | None
    parents: Mapping[str, str] = field(default_factory=dict)
    """Sub-building id → OSM parent id, for footprints ``cadastre split`` divided (ADR-015)."""

    def _subdivided(self) -> dict[str, Any]:
        levels = [b.estimated_levels for b in self.result.buildings if b.id in self.parents]
        return {
            "osm_buildings_split": len(set(self.parents.values())),
            "sub_buildings": len(levels),
            "levels_range": [min(levels), max(levels)] if levels else None,
        }

    def _header(self) -> dict[str, Any]:
        cfg = self.config
        return {
            "schema_version": SCHEMA_VERSION,
            "scene": dict(self.scene_ref),
            "config": {"version": cfg.version, **(cfg.file.to_dict() if cfg.file else {"path": None, "sha256": None})},
            "overrides": cfg.overrides_file.to_dict()
            | {
                "count": len(cfg.overrides),
                "sources": [
                    {"text": s, "license": lic}
                    for s, lic in sorted({(o.source, o.license) for o in cfg.overrides.values()})
                ],
            }
            if cfg.overrides_file
            else None,
            "attribution": dict(self.attribution) if self.attribution else None,
        }

    def heights_dict(self) -> dict[str, Any]:
        return {
            "schema": HEIGHTS_SCHEMA,
            **self._header(),
            "assumptions": dict(self.config.document),
            "buildings": [b.to_dict() for b in self.result.buildings],
        }

    def report_dict(self) -> dict[str, Any]:
        buildings = self.result.buildings
        total = len(buildings)
        by_class = Counter(b.height_class.value for b in buildings)
        by_source = Counter(b.height_source.value for b in buildings)
        by_category = Counter(b.category for b in buildings)
        issues = [i.to_dict() for i in self.result.issues]
        review = [
            {"building_id": b.id, "name": b.name, "height_m": b.height_m, "height_source": b.height_source.value, "reasons": list(b.review_reasons)}
            for b in buildings
            if b.needs_manual_review
        ]
        return {
            "schema": REPORT_SCHEMA,
            **self._header(),
            "counts": {
                "buildings": total,
                "by_class": {c.value: by_class.get(c.value, 0) for c in HeightClass},
                "by_source": {s.value: by_source.get(s.value, 0) for s in HeightSource},
                "by_category": dict(sorted(by_category.items())),
                "landmarks": sum(b.landmark for b in buildings),
                "needs_manual_review": len(review),
                "subdivided": self._subdivided(),
            },
            "percent_by_class": {c.value: _percent(by_class.get(c.value, 0), total) for c in HeightClass},
            "percent_by_source": {s.value: _percent(by_source.get(s.value, 0), total) for s in HeightSource},
            "height_m": _stats([b.height_m for b in buildings]),
            "confidence": _stats([b.height_confidence for b in buildings]),
            "manual_review": review,
            "issues_by_code": dict(sorted(Counter(i["code"] for i in issues).items())),
            "issues": issues,
        }


def run_heights(scene_path: str | Path, config: HeightConfig | None = None) -> HeightRun:
    """Estimate heights for every building in a geo scene file. The scene is read only."""
    path = Path(scene_path).expanduser().resolve()
    if not path.is_file():
        raise SceneError(f"scene file not found: {path}")
    if path.stat().st_size > MAX_SCENE_BYTES:
        raise SceneError(f"scene file larger than {MAX_SCENE_BYTES} bytes")
    config = config or load_height_config()
    before = _sha256(path)
    try:
        scene = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SceneError(f"scene is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(scene, dict):
        raise SceneError("scene root must be a JSON object")
    buildings, scene_issues = buildings_from_scene(scene)
    result = estimate_heights(buildings, config)
    if _sha256(path) != before:
        raise SceneError("scene file changed while it was being read")
    result = HeightResult(result.buildings, tuple(scene_issues) + result.issues)
    scene_ref = {"path": str(path), "sha256": before, "schema": scene["schema"], "schema_version": scene["schema_version"]}
    parents = {
        str(f["id"]): str(f["parent"]["id"])
        for f in scene["features"]
        if f.get("kind") == "building" and isinstance(f.get("parent"), Mapping)
    }
    return HeightRun(result, config, scene_ref, scene.get("attribution"), parents)


def write_height_outputs(run: HeightRun, out_dir: str | Path, *, overwrite: bool = False) -> tuple[Path, Path]:
    heights, report = prepare_outputs(
        out_dir, (HEIGHTS_FILE, REPORT_FILE), overwrite=overwrite, protected=(Path(run.scene_ref["path"]),)
    )
    atomic_write_json(heights, run.heights_dict())
    atomic_write_json(report, run.report_dict())
    return heights, report
