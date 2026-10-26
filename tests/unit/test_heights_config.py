from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from onward_generator.heights import HeightConfigError, load_height_config
from onward_generator.heights.config import DEFAULT_CONFIG_PATH, config_from_document

DEFAULT_DOC: dict[str, Any] = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))


def test_default_config_is_valid_and_matches_requested_ranges() -> None:
    cfg = load_height_config()
    assert cfg.file is not None and cfg.file.path == str(DEFAULT_CONFIG_PATH)
    assert cfg.storey_height("residential_multi") == 3.0
    assert 3.5 <= cfg.storey_height("commercial") <= 4.0 and 3.5 <= cfg.storey_height("public") <= 4.0
    expected = {
        "residential_small": (6, 9), "residential_multi": (9, 15), "commercial": (6, 12), "industrial": (5, 10),
    }
    for name, (low, high) in expected.items():
        r = cfg.fallback_m[name]
        assert (r.min, r.max) == (low, high) and low <= r.default <= high
    assert cfg.fallback_m["unknown"].default == 8.0
    assert cfg.overrides == {}


def mutated(path: str, value: Any) -> dict[str, Any]:
    doc = copy.deepcopy(DEFAULT_DOC)
    *parents, leaf = path.split("/")
    target = doc
    for p in parents:
        target = target[p]
    if value is KeyError:
        del target[leaf]
    else:
        target[leaf] = value
    return doc


@pytest.mark.parametrize(
    "path, value, fragment",
    [
        ("bounds_m/min", 200.0, "min must be below max"),
        ("bounds_m/min", -1, "/bounds_m/min"),
        ("fallback_m/unknown/default", 50.0, "default must lie within"),
        ("fallback_m/unknown", KeyError, "unknown"),
        ("storey_height_m/minor", KeyError, "missing categories"),
        ("typical_levels/towers", 30, "have no fallback_m entry"),
        ("categories/building/house", "castle_like", "unknown category"),
        ("categories/roof:shape", {"flat": "minor"}, "not listed in category_key_priority"),
        ("confidence/fallback", 1.5, "/confidence/fallback"),
        ("neighbourhood/min_samples", 0, "/neighbourhood/min_samples"),
        ("storey_height_m/unknown", "3", "/storey_height_m/unknown"),
        ("surprise", True, "Additional properties"),
    ],
)
def test_invalid_config_is_rejected_with_location(path: str, value: Any, fragment: str) -> None:
    with pytest.raises(HeightConfigError) as exc:
        config_from_document(mutated(path, value))
    assert any(fragment in p for p in exc.value.problems), exc.value.problems


def test_duplicate_keys_and_missing_files_are_rejected(tmp_path: Path) -> None:
    dup = tmp_path / "dup.json"
    dup.write_text('{"version": "0.1.0", "version": "0.2.0"}', encoding="utf-8")
    with pytest.raises(HeightConfigError, match="duplicate key"):
        load_height_config(dup)
    with pytest.raises(HeightConfigError, match="not found"):
        load_height_config(tmp_path / "missing.json")
    doc = mutated("overrides_file", "nope.json")
    with pytest.raises(HeightConfigError, match="not found"):
        config_from_document(doc, base_dir=tmp_path)
