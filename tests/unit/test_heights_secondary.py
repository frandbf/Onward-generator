"""Secondary (third-party, Overture) height overrides: ranked below an OSM height and Catastro, above building:levels."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from onward_generator.heights import HeightClass, HeightConfigError, HeightSource, estimate_heights, load_height_config
from onward_generator.heights.config import HeightConfig
from onward_generator.heights.pipeline import HeightRun, _overrides_ref
from tests.unit.test_heights import bld

THIRD = {"id": "osm-way-1", "height_m": 14.5, "source": "Overture Maps buildings 2026-10-21.0: OpenStreetMap",
         "license": "ODbL-1.0", "confidence": 0.8, "origin": "observed"}
CATASTRO = {"id": "osm-way-1", "levels": 5, "source": "Catastro", "license": "CC-BY-4.0", "confidence": 0.85}


def _write(path: Path, entries: list[dict[str, Any]]) -> Path:
    path.write_text(json.dumps({"version": "0.1.0", "overrides": entries}), encoding="utf-8")
    return path


def _config(tmp_path: Path, secondary: list[dict[str, Any]], primary: list[dict[str, Any]] | None = None) -> HeightConfig:
    return load_height_config(
        overrides_path=_write(tmp_path / "catastro.json", primary) if primary is not None else None,
        secondary_overrides_path=_write(tmp_path / "overture.json", secondary),
    )


def test_secondary_height_beats_osm_levels(tmp_path: Path) -> None:
    result = estimate_heights([bld(1, 0, 0, lv=2)], _config(tmp_path, [{**THIRD, "levels": 4}]))
    r = result.get("osm-way-1")
    assert r is not None and r.height_source is HeightSource.THIRD_PARTY_HEIGHT and r.height_m == 14.5
    assert r.height_class is HeightClass.INFERRED and r.height_confidence == 0.8
    assert r.estimated_levels == 4 and r.levels_source == "override"
    assert r.rule == "third-party observed: Overture Maps buildings 2026-10-21.0: OpenStreetMap (ODbL-1.0)"
    assert result.issues == ()


def test_secondary_height_keeps_osm_levels_or_derives_them(tmp_path: Path) -> None:
    config = _config(tmp_path, [THIRD, {**THIRD, "id": "osm-way-2", "height_m": 9.0}])
    result = estimate_heights([bld(1, 0, 0, lv=3), bld(2, 50, 0)], config)
    one, two = result.get("osm-way-1"), result.get("osm-way-2")
    assert one is not None and one.estimated_levels == 3 and one.levels_source == "explicit"
    assert two is not None and two.estimated_levels == 3 and two.levels_source == "derived_from_height"


def test_secondary_levels_become_a_height_from_the_category_storey(tmp_path: Path) -> None:
    entry = {k: v for k, v in THIRD.items() if k != "height_m"} | {"levels": 5, "origin": "ai_generated", "confidence": 0.5}
    result = estimate_heights([bld(1, 0, 0, lv=2)], _config(tmp_path, [entry]))
    r = result.get("osm-way-1")
    assert r is not None and r.height_source is HeightSource.THIRD_PARTY_LEVELS and r.height_m == 15.0
    assert r.estimated_levels == 5 and r.levels_source == "override" and r.height_confidence == 0.5
    assert r.rule.startswith("third-party ai_generated: Overture Maps buildings 2026-10-21.0: OpenStreetMap (ODbL-1.0): levels 5 × 3.0 m")
    assert r.height_class is HeightClass.INFERRED


def test_an_entry_without_origin_says_so(tmp_path: Path) -> None:
    entry = {k: v for k, v in THIRD.items() if k != "origin"}
    r = estimate_heights([bld(1, 0, 0)], _config(tmp_path, [entry])).get("osm-way-1")
    assert r is not None and r.rule.startswith("third-party unstated: ")


def test_osm_height_and_catastro_levels_outrank_the_secondary(tmp_path: Path) -> None:
    config = _config(tmp_path, [THIRD, {**THIRD, "id": "osm-way-2"}], primary=[{**CATASTRO, "id": "osm-way-2"}])
    result = estimate_heights([bld(1, 0, 0, h=20.0), bld(2, 50, 0)], config)
    assert result.get("osm-way-1").height_source is HeightSource.EXPLICIT_HEIGHT  # type: ignore[union-attr]
    two = result.get("osm-way-2")
    assert two is not None and two.height_source is HeightSource.AUTHORITATIVE_LEVELS and two.estimated_levels == 5


def test_secondary_beats_type_and_neighbourhood_estimates(tmp_path: Path) -> None:
    config = _config(tmp_path, [{**THIRD, "id": "osm-way-1", "height_m": 30.0}])
    result = estimate_heights([bld(1, 0, 0, building="apartments")], config)
    assert result.get("osm-way-1").height_source is HeightSource.THIRD_PARTY_HEIGHT  # type: ignore[union-attr]


def test_a_secondary_id_missing_from_the_scene_is_reported(tmp_path: Path) -> None:
    result = estimate_heights([bld(1, 0, 0)], _config(tmp_path, [THIRD, {**THIRD, "id": "osm-way-9"}]))
    assert [(i.code, i.building_id) for i in result.issues] == [("override_not_in_scene", "osm-way-9")]


def test_secondary_overrides_are_validated_like_the_primary(tmp_path: Path) -> None:
    for bad in ([{**THIRD, "height_m": 500}], [THIRD, THIRD], [{**THIRD, "origin": "guessed"}]):
        with pytest.raises(HeightConfigError):
            _config(tmp_path, bad)
    cfg = load_height_config()
    assert cfg.secondary_overrides == {} and cfg.secondary_overrides_file is None


def test_the_header_lists_only_the_secondary_sources_used(tmp_path: Path) -> None:
    config = _config(tmp_path, [
        THIRD,
        {**THIRD, "id": "osm-way-2", "source": "Overture Maps buildings 2026-10-21.0: Google Open Buildings", "license": "CC-BY-4.0"},
    ])
    result = estimate_heights([bld(1, 0, 0), bld(2, 50, 0, h=11.0)], config)
    header = HeightRun(result, config, {"path": "s"}, None).heights_dict()
    assert header["overrides"] is None
    secondary = header["secondary_overrides"]
    assert secondary["path"] == str((tmp_path / "overture.json").resolve()) and len(secondary["sha256"]) == 64
    assert secondary["count"] == 2 and secondary["used"] == 1
    assert secondary["sources"] == [{"text": THIRD["source"], "license": "ODbL-1.0"}]
    plain = HeightRun(estimate_heights([bld(1, 0, 0)], load_height_config()), load_height_config(), {"path": "s"}, None)
    assert "secondary_overrides" not in plain.heights_dict()
    report = HeightRun(result, config, {"path": "s"}, None).report_dict()
    assert report["counts"]["by_source"]["third_party_height"] == 1 and report["counts"]["by_source"]["third_party_levels"] == 0


def test_overrides_ref_is_none_without_a_file() -> None:
    assert _overrides_ref(None, {}) is None


def test_a_config_file_resolves_its_overrides_file_beside_it(tmp_path: Path) -> None:
    from onward_generator.heights.config import DEFAULT_CONFIG_PATH

    doc = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")) | {"overrides_file": "catastro.json"}
    (tmp_path / "heights.json").write_text(json.dumps(doc), encoding="utf-8")
    _write(tmp_path / "catastro.json", [CATASTRO])
    cfg = load_height_config(tmp_path / "heights.json", secondary_overrides_path=_write(tmp_path / "o.json", [THIRD]))
    assert set(cfg.overrides) == {"osm-way-1"} and set(cfg.secondary_overrides) == {"osm-way-1"}
    header = HeightRun(estimate_heights([bld(1, 0, 0)], cfg), cfg, {"path": "s"}, None).heights_dict()
    assert header["overrides"]["count"] == 1 and header["secondary_overrides"]["count"] == 1

