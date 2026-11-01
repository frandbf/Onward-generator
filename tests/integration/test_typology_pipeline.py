"""detail on the synthetic Murcia block: every building records a typology, counted for the core only (ADR-024)."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from onward_generator.detail import detail_scene, load_base, load_detail_config
from onward_generator.detail.__main__ import main
from onward_generator.detail.typology import DEFAULT_CONFIG_PATH, load_typology_config
from onward_generator.generation import load_generation_config
from tests.conftest import _detail_inputs
from tests.detail_fixture import BUILDINGS, POIS

GEN = load_generation_config()
CFG = load_detail_config()
CORE_M = 50.0
DATED = 311
FAR = {314: ([(60, 6), (80, 6), (80, 30), (60, 30)], {**BUILDINGS[314][1], "start_date": "1999"})}
"""Outside a 50 m core, so its year must not reach the counts."""


@pytest.fixture(scope="module")
def dated_inputs(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, Path]:
    outline, tags = BUILDINGS[DATED]
    buildings = {**BUILDINGS, **FAR, DATED: (outline, {**tags, "start_date": "1925-03-01"})}
    return _detail_inputs(tmp_path_factory.mktemp("dated"), buildings, POIS)


def _detail(inputs: tuple[Path, Path, Path], **options: Any) -> Any:
    scene, heights, base = inputs
    generated, ref = load_base(base, scene, heights, GEN)
    return detail_scene(generated, ref, heights, scene, CFG, **options)


def _records(detailed: Any) -> list[dict[str, Any]]:
    return [o.record for o in detailed.objects if o.type == "building"]


def test_every_building_has_a_typology_and_the_report_counts_them(detail_inputs: tuple[Path, Path, Path]) -> None:
    detailed = _detail(detail_inputs)
    typologies = [r["facade"]["typology"] for r in _records(detailed)]
    assert typologies and all(t["origin"] == "estimated" and t["rule"].startswith(t["typology"].split("_")[0]) for t in typologies)
    assert detailed.stats["typologies"] == dict(sorted(Counter(t["typology"] for t in typologies).items()))
    assert detailed.stats["eras"] == {"unknown": len(typologies)}
    assert detailed.stats["era_sources"] == {}
    config = load_typology_config()
    assert detailed.report()["inputs"]["typology_config"] == {"path": config.path, "sha256": config.sha256, "version": config.version}


def test_an_observed_era_is_counted_by_period_and_source(dated_inputs: tuple[Path, Path, Path]) -> None:
    detailed = _detail(dated_inputs)
    records = _records(detailed)
    dated = next(r for r in records if r["source_ids"] == [f"osm-way-{DATED}"])
    assert dated["facade"]["typology"]["era"]["period"] == "1900_1939" and dated["facade"]["typology"]["era"]["origin"] == "observed"
    assert detailed.stats["eras"] == {"1900_1939": 1, "1980_1999": 1, "unknown": len(records) - 2}
    assert detailed.stats["era_sources"] == {"osm": 2}


def test_only_core_buildings_are_counted(dated_inputs: tuple[Path, Path, Path]) -> None:
    detailed = _detail(dated_inputs, core_half_side_m=CORE_M)
    records = _records(detailed)
    core = [r["facade"]["typology"] for r in records if r["tier"] == "core"]
    scenic = [r["facade"]["typology"] for r in records if r["tier"] == "scenic"]
    assert core and any(t["era"] and t["era"]["year"] == 1999 for t in scenic)
    assert detailed.stats["typologies"] == dict(sorted(Counter(t["typology"] for t in core).items()))
    assert "1980_1999" not in detailed.stats["eras"] and detailed.stats["era_sources"] == {"osm": 1}


def test_the_command_line_uses_the_config_it_is_given(detail_inputs: tuple[Path, Path, Path], tmp_path: Path) -> None:
    scene, heights, base = detail_inputs
    document = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    document["version"] = "9.9.9"
    path = tmp_path / "typology.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    args = ["--scene", str(scene), "--heights", str(heights), "--base", str(base), "--no-preview"]
    assert main([*args, "--out", str(tmp_path / "out"), "--typology-config", str(path)]) == 0
    report = json.loads((tmp_path / "out" / "detail_report.json").read_text(encoding="utf-8"))
    assert report["inputs"]["typology_config"]["version"] == "9.9.9"
    assert report["inputs"]["typology_config"]["path"] == str(path.resolve())
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    assert main([*args, "--out", str(tmp_path / "other"), "--typology-config", str(bad)]) == 1


def test_the_style_reaches_the_facade_and_a_residential_palette(tmp_path_factory: pytest.TempPathFactory) -> None:
    outline = [(-30, 6), (-15, 6), (-15, 18), (-30, 18)]
    tags = {"building": "apartments", "building:levels": "5"}
    plain = {701: (outline, tags)}
    dated = {701: (outline, {**tags, "start_date": "1965"})}
    before = _detail(_detail_inputs(tmp_path_factory.mktemp("plain"), plain, {}))
    after = _detail(_detail_inputs(tmp_path_factory.mktemp("sixties"), dated, {}))
    record = next(o.record for o in after.objects if o.id == "building/osm-way-701")
    style = record["facade"]["typology"]["facade_style"]
    assert tuple(style["palette"]) == ("brick_red", "plaster_cream", "plaster_white") and style["window_width_factor"] == 1.25
    assert record["materials"]["wall"] in style["palette"]
    plain_style = next(o.record for o in before.objects if o.id == "building/osm-way-701")["facade"]["typology"]["facade_style"]
    assert plain_style["palette"] != style["palette"]
