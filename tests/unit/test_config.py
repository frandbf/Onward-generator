from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from onward_generator.config import ConfigValidationError, load_config, validate_config_file
from onward_generator.config.validation import DEFAULT_SCHEMA_PATH, REPO_ROOT, load_schema_validator
from tests.conftest import AOI_FIXTURE, MURCIA_CENTER_LAT, MURCIA_CENTER_LON

EXAMPLE = REPO_ROOT / "config" / "murcia-belluga.example.json"


def base_doc() -> dict[str, Any]:
    doc = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    doc.pop("$schema")
    doc["area"]["boundary_file"] = str(AOI_FIXTURE)
    return doc


def write(tmp_path: Path, doc: Any, name: str = "project.json") -> Path:
    path = tmp_path / name
    path.write_text(doc if isinstance(doc, str) else json.dumps(doc), encoding="utf-8")
    return path


def codes(path: Path) -> list[tuple[str, str, str]]:
    return [(d.severity, d.code, d.path) for d in validate_config_file(path).diagnostics]


def test_schema_is_valid_2020_12() -> None:
    assert DEFAULT_SCHEMA_PATH.is_file()
    assert load_schema_validator().schema["$schema"].endswith("2020-12/schema")


def test_example_config_is_valid_and_typed() -> None:
    result = validate_config_file(EXAMPLE)
    assert result.valid, result.diagnostics
    config = result.config
    assert config is not None
    assert config.project.name == "Murcia Belluga"
    assert config.area.center == (MURCIA_CENTER_LON, MURCIA_CENTER_LAT)
    assert config.area.boundary_file == REPO_ROOT / "config" / "murcia_belluga_aoi.geojson"
    assert config.area.margin_m == 30.0
    assert config.quality.deterministic_seed == 48152
    assert config.export.enabled is False
    assert [d.code for d in result.diagnostics] == ["stage_not_implemented"]


def test_fixture_based_config_is_valid(tmp_path: Path) -> None:
    config = load_config(write(tmp_path, base_doc()))
    assert config.area.boundary_file == AOI_FIXTURE.resolve()


def test_prd_template_with_null_area_is_valid_with_warning(tmp_path: Path) -> None:
    doc = base_doc()
    doc["area"] = {"center": {"latitude": None, "longitude": None}, "boundary_file": None, "margin_m": 30}
    result = validate_config_file(write(tmp_path, doc))
    assert result.valid
    assert ("warning", "area_undefined", "/area") in [(d.severity, d.code, d.path) for d in result.diagnostics]


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda d: d["area"].update(margin_m=-1), ("error", "schema_minimum", "/area/margin_m")),
        (lambda d: d["area"].update(margin_m="30"), ("error", "schema_type", "/area/margin_m")),
        (lambda d: d["area"]["center"].update(latitude=95), ("error", "schema_maximum", "/area/center/latitude")),
        (lambda d: d["project"].update(version="1.0"), ("error", "schema_pattern", "/project/version")),
        (lambda d: d["project"].update(target="pc"), ("error", "schema_enum", "/project/target")),
        (lambda d: d["source"].update(attribution_required=False), ("error", "schema_const", "/source/attribution_required")),
        (lambda d: d["quality"].update(deterministic_seed=1.5), ("error", "schema_type", "/quality/deterministic_seed")),
        (lambda d: d["generation"].update(colour="red"), ("error", "schema_additionalProperties", "/generation")),
        (lambda d: d.pop("export"), ("error", "schema_required", "")),
        (lambda d: d["area"].update(boundary_file="area.kml"), ("error", "schema_pattern", "/area/boundary_file")),
    ],
)
def test_schema_errors_identify_field_and_cause(tmp_path: Path, mutate, expected) -> None:
    doc = copy.deepcopy(base_doc())
    mutate(doc)
    result = validate_config_file(write(tmp_path, doc))
    assert not result.valid and result.config is None
    assert expected in [(d.severity, d.code, d.path) for d in result.diagnostics]


def test_all_schema_errors_are_reported_together(tmp_path: Path) -> None:
    doc = base_doc()
    doc["area"]["margin_m"] = -1
    doc["project"]["version"] = "x"
    assert len(validate_config_file(write(tmp_path, doc)).diagnostics) == 2


def test_export_enabled_warns_until_the_adapter_is_device_verified(tmp_path: Path) -> None:
    doc = base_doc()
    doc["export"]["enabled"] = True
    result = validate_config_file(write(tmp_path, doc))
    assert result.valid
    assert ("warning", "export_adapter_unverified", "/export/enabled") in codes(write(tmp_path, doc))


def test_incomplete_center_is_rejected(tmp_path: Path) -> None:
    doc = base_doc()
    doc["area"]["center"]["longitude"] = None
    assert ("error", "center_incomplete", "/area/center") in codes(write(tmp_path, doc))


def test_center_outside_default_crs_is_rejected(tmp_path: Path) -> None:
    doc = base_doc()
    doc["area"]["center"] = {"latitude": 48.85, "longitude": 2.35}
    found = codes(write(tmp_path, doc))
    assert ("error", "center_outside_crs", "/area/center") in found


def test_center_outside_boundary_warns(tmp_path: Path) -> None:
    doc = base_doc()
    doc["area"]["center"]["latitude"] = MURCIA_CENTER_LAT + 0.01
    result = validate_config_file(write(tmp_path, doc))
    assert result.valid
    assert ("warning", "center_outside_boundary", "/area/center") in [(d.severity, d.code, d.path) for d in result.diagnostics]


def test_boundary_file_problems(tmp_path: Path) -> None:
    doc = base_doc()
    doc["area"]["boundary_file"] = "missing.geojson"
    assert ("error", "boundary_not_found", "/area/boundary_file") in codes(write(tmp_path, doc))

    write(tmp_path, {"type": "Point", "coordinates": [MURCIA_CENTER_LON, MURCIA_CENTER_LAT]}, "point.geojson")
    doc["area"]["boundary_file"] = "point.geojson"
    assert ("error", "boundary_invalid", "/area/boundary_file") in codes(write(tmp_path, doc))


def test_margin_mismatch_warns(tmp_path: Path) -> None:
    doc = base_doc()
    doc["area"]["margin_m"] = 50
    assert ("warning", "margin_mismatch", "/area/margin_m") in codes(write(tmp_path, doc))


@pytest.mark.parametrize(
    "text, code",
    [
        ("{not json", "invalid_json"),
        ("[]", "invalid_root"),
        ('{"a": 1, "a": 2}', "invalid_json"),
        ('{"margin": NaN}', "invalid_json"),
    ],
)
def test_malformed_files(tmp_path: Path, text: str, code: str) -> None:
    result = validate_config_file(write(tmp_path, text))
    assert not result.valid
    assert [d.code for d in result.diagnostics] == [code]


def test_missing_and_wrong_extension(tmp_path: Path) -> None:
    assert [d.code for d in validate_config_file(tmp_path / "nope.json").diagnostics] == ["file_not_found"]
    assert [d.code for d in validate_config_file(write(tmp_path, "{}", "c.yaml")).diagnostics] == ["unsupported_format"]


def test_load_config_raises_with_all_errors(tmp_path: Path) -> None:
    doc = base_doc()
    doc["area"]["margin_m"] = -1
    with pytest.raises(ConfigValidationError, match="minimum"):
        load_config(write(tmp_path, doc))
