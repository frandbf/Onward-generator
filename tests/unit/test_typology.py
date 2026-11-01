"""Building typology and construction era (ADR-024): the year parser, the era ranking, the morphology and every rule."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from shapely import STRtree, box
from shapely.geometry import Polygon

from onward_generator.detail import load_detail_config
from onward_generator.detail.grammar import FacadeUse
from onward_generator.detail.typology import (
    DEFAULT_CONFIG_PATH,
    Era,
    Morphology,
    TypologyConfigError,
    TypologyInputs,
    building_era,
    building_typologies,
    classify,
    era_period,
    load_typology_config,
    morphology,
    parse_year,
    shared_wall_share,
    typology_config_from_document,
    typology_record,
)
from onward_generator.generation.scene import Issue

CFG = load_detail_config()
TCFG = load_typology_config()
EVIDENCE = ("osm", "es.catastro", "overture")


@pytest.mark.parametrize(("value", "year"), [
    ("1890", 1890), ("1890-05-01", 1890), ("~1890", 1890), (" ~1890 ", 1890), ("1960s", 1960), ("1890..1900", 1890),
    ("1000", 1000), ("2100", 2100), ("2019-07-13", 2019),
])
def test_a_leading_four_digit_year_is_read(value: str, year: int) -> None:
    assert parse_year(value) == year


@pytest.mark.parametrize("value", ["C19", "before 1900", "mid 1800s", "19th century", "18901", "999", "0999", "2101", "", "~", "~ 1890", "189"])
def test_anything_else_is_not_a_year(value: str) -> None:
    assert parse_year(value) is None


@pytest.mark.parametrize(("year", "period"), [
    (1500, "pre_1900"), (1899, "pre_1900"), (1900, "1900_1939"), (1939, "1900_1939"), (1940, "1940_1959"), (1959, "1940_1959"),
    (1960, "1960_1979"), (1979, "1960_1979"), (1980, "1980_1999"), (1999, "1980_1999"), (2000, "2000_plus"), (2026, "2000_plus"),
])
def test_era_periods_and_their_boundaries(year: int, period: str) -> None:
    assert era_period(year) == period


def test_an_era_record_is_observed_and_names_its_evidence() -> None:
    era = Era(1931, "osm", {"source_id": "b", "tag": "start_date", "value": "1931"})
    assert era.to_record() == {"year": 1931, "period": "1900_1939", "origin": "observed", "source": "osm",
                               "evidence": {"source_id": "b", "tag": "start_date", "value": "1931"}}


CATASTRO_USE = {"refcat": "R1", "provider": "catastro-inspire-bu", "license": "CC-BY-4.0", "attribution": "DG Catastro",
                "construction_year": 1976}


def _era(features: dict[str, dict[str, Any]], evidence: tuple[str, ...] = EVIDENCE) -> tuple[dict[str, Any] | None, list[Issue]]:
    issues: list[Issue] = []
    era = building_era(sorted(features), features, evidence, TCFG, issues)
    return (era.to_record() if era else None), issues


def test_osm_dates_rank_above_catastro_and_follow_the_tag_order() -> None:
    record, issues = _era({"a": {"tags": {"start_date": "1902", "building:start_date": "1899"}, "catastro_use": CATASTRO_USE}})
    assert record == {"year": 1899, "period": "pre_1900", "origin": "observed", "source": "osm",
                      "evidence": {"source_id": "a", "tag": "building:start_date", "value": "1899"}}
    assert issues == []
    record, _ = _era({"a": {"tags": {"construction_date": "1955"}}})
    assert record is not None and (record["year"], record["evidence"]["tag"]) == (1955, "construction_date")


def test_an_unparsable_date_is_an_issue_and_the_next_tag_or_source_decides() -> None:
    record, issues = _era({"a": {"tags": {"building:start_date": "C19", "start_date": "1931"}}})
    assert record is not None and record["year"] == 1931
    assert issues == [Issue("era_unparsable", "info", "a", "building:start_date='C19' has no leading year")]
    record, issues = _era({"a": {"tags": {"start_date": "before 1900"}, "catastro_use": CATASTRO_USE}})
    assert record == {"year": 1976, "period": "1960_1979", "origin": "observed", "source": "es.catastro",
                      "evidence": {"refcat": "R1", "provider": "catastro-inspire-bu", "license": "CC-BY-4.0", "attribution": "DG Catastro"}}
    assert [i.message for i in issues] == ["start_date='before 1900' has no leading year"]


def test_the_parts_of_a_parcel_report_one_unparsable_tag_once() -> None:
    record, issues = _era({"a:1": {"tags": {"start_date": "C19"}}, "a:2": {"tags": {"start_date": "C19"}}})
    assert record is None
    assert issues == [Issue("era_unparsable", "info", "a:1", "start_date='C19' has no leading year")]


def test_the_ranking_decides_and_nothing_is_estimated() -> None:
    both = {"a": {"tags": {"start_date": "1902"}, "catastro_use": CATASTRO_USE}}
    record, _ = _era(both, ("es.catastro", "osm", "overture"))
    assert record is not None and record["source"] == "es.catastro"
    record, _ = _era(both, ("overture",))  # Overture buildings carry no year
    assert record is None
    # A null Catastro year, an empty tag and no tags give no era: unknown is not guessed.
    record, issues = _era({"a": {"tags": {"start_date": ""}, "catastro_use": {**CATASTRO_USE, "construction_year": None}}, "b": {}})
    assert (record, issues) == (None, [])


def test_a_later_part_can_carry_the_parcel_year() -> None:
    record, _ = _era({"a:1": {"tags": {}}, "a:2": {"tags": {}, "catastro_use": CATASTRO_USE}})
    assert record is not None and record["year"] == 1976


# --- morphology ---


def test_morphology_of_a_lone_square_in_metres() -> None:
    m = morphology(box(0, 0, 10, 10), Polygon(), CFG)
    assert (m.area_m2, m.perimeter_m, m.elongation, m.shared_wall_share) == pytest.approx((100.0, 40.0, 1.0, 0.0))
    assert m.compactness == pytest.approx(math.pi / 4)


def test_a_wall_against_a_neighbour_counts_and_a_gap_beyond_the_probe_does_not() -> None:
    slab = box(0, 0, 30, 10)
    m = morphology(slab, box(30, 0, 40, 10), CFG)
    assert m.shared_wall_share == pytest.approx(10 / 80)
    assert m.elongation == pytest.approx(3.0)
    assert shared_wall_share(slab, box(30 + CFG.probe_m + 0.1, 0, 40, 10), CFG) == 0.0
    assert shared_wall_share(slab, box(30 + CFG.probe_m - 0.1, 0, 40, 10), CFG) == pytest.approx(10 / 80)


def test_a_short_exposed_run_between_two_neighbours_stays_exposed() -> None:
    # A 2 m gap is under min_facade_length_m (2.5 m): the facade blanks it, the share does not count it as shared.
    lot = box(0, 0, 10, 10)
    others = box(0, 10, 4, 14).union(box(6, 10, 10, 14))
    assert shared_wall_share(lot, others, CFG) == pytest.approx(8 / 40)
    assert shared_wall_share(lot, others, replace(CFG, courtyard_openings=True)) == pytest.approx(8 / 40)


def test_a_gap_at_the_minimum_run_counts_as_exposed_and_a_shorter_one_does_not() -> None:
    lot = box(0, 0, 10, 10)
    assert shared_wall_share(lot, box(0, 10, 4, 14).union(box(4 + 0.15, 10, 10, 14)), CFG) < shared_wall_share(lot, box(0, 10, 10, 14), CFG)
    assert shared_wall_share(lot, box(0, 10, 4, 14).union(box(4 + 0.05, 10, 10, 14)), CFG) == pytest.approx(10 / 40)


def test_a_courtyard_lowers_compactness_but_not_the_shared_share() -> None:
    ring = Polygon([(0, 0), (20, 0), (20, 20), (0, 20)], [[(5, 5), (15, 5), (15, 15), (5, 15)]])
    m = morphology(ring, box(20, 0, 30, 20), CFG)
    assert (m.area_m2, m.perimeter_m) == (300.0, 120.0)
    assert m.compactness == pytest.approx(4 * math.pi * 300 / 120**2)
    assert m.shared_wall_share == pytest.approx(20 / 80)


# --- classification ---


def _x(**kw: Any) -> TypologyInputs:
    shape = {k: kw.pop(k) for k in ("area_m2", "compactness", "elongation", "shared_wall_share") if k in kw}
    m = Morphology(**{"area_m2": 400.0, "perimeter_m": 80.0, "compactness": 0.5, "elongation": 1.5, "shared_wall_share": 0.5, **shape})
    base: dict[str, Any] = {"morphology": m, "height_m": 15.0, "storeys": 5, "category": "residential_multi", "use": "residential",
                            "catastro_use": None, "landmark": False, "period": None}
    return TypologyInputs(**{**base, **kw})


@pytest.mark.parametrize(("inputs", "expected"), [
    (_x(landmark=True, use="institutional", storeys=20), ("landmark", "landmark:heights_landmark")),
    (_x(category="minor", use="industrial"), ("other", "other:minor_category")),
    (_x(use="institutional"), ("civic", "civic:use")),
    (_x(use="historic"), ("civic", "civic:use")),
    (_x(catastro_use="public_services"), ("civic", "civic:use")),
    (_x(category="public"), ("civic", "civic:use")),
    (_x(category="religious"), ("civic", "civic:use")),
    (_x(use="industrial", storeys=20), ("industrial_shed", "industrial_shed:use")),
    (_x(catastro_use="industrial"), ("industrial_shed", "industrial_shed:use")),
    (_x(category="industrial"), ("industrial_shed", "industrial_shed:use")),
    (_x(storeys=1, area_m2=800.0), ("industrial_shed", "industrial_shed:large_low")),
    (_x(storeys=2, area_m2=800.0, shared_wall_share=0.0), ("other", "other:no_rule")),
    (_x(storeys=1, area_m2=799.9, shared_wall_share=0.0), ("other", "other:no_rule")),
    (_x(storeys=13, period="pre_1900"), ("tower", "tower:storeys")),
    (_x(storeys=12, shared_wall_share=0.0, elongation=1.0, compactness=0.6), ("open_block", "open_block:point")),
    (_x(period="pre_1900", shared_wall_share=0.1), ("historic_core", "historic_core:attached_early_era")),
    (_x(period="1900_1939", storeys=2, area_m2=100.0), ("historic_core", "historic_core:attached_early_era")),
    (_x(period="1940_1959", storeys=2, area_m2=100.0), ("row_house", "row_house:attached_small_low")),
    (_x(period="pre_1900", shared_wall_share=0.099, storeys=2, area_m2=100.0), ("detached_house", "detached_house:detached_small_low")),
    (_x(storeys=3, area_m2=250.0, shared_wall_share=0.1), ("row_house", "row_house:attached_small_low")),
    (_x(storeys=3, area_m2=250.0, shared_wall_share=0.099), ("detached_house", "detached_house:detached_small_low")),
    (_x(storeys=4, area_m2=100.0, shared_wall_share=0.3), ("closed_block", "closed_block:attached_mid_rise")),
    (_x(storeys=3, area_m2=250.1, shared_wall_share=0.3), ("closed_block", "closed_block:attached_mid_rise")),
    (_x(storeys=2, area_m2=300.0, shared_wall_share=0.3), ("closed_block", "closed_block:attached_mid_rise")),
    (_x(storeys=1, area_m2=300.0, shared_wall_share=0.3), ("other", "other:no_rule")),
    (_x(storeys=12, shared_wall_share=0.3), ("closed_block", "closed_block:attached_mid_rise")),
    (_x(storeys=12, shared_wall_share=1.0, area_m2=900.0), ("closed_block", "closed_block:attached_mid_rise")),
    (_x(storeys=4, shared_wall_share=0.299, elongation=2.0, compactness=0.1), ("open_block", "open_block:slab")),
    (_x(storeys=4, shared_wall_share=0.0, elongation=1.999, compactness=0.6), ("open_block", "open_block:point")),
    (_x(storeys=4, shared_wall_share=0.0, elongation=1.999, compactness=0.599), ("other", "other:no_rule")),
    (_x(storeys=3, shared_wall_share=0.0, elongation=3.0, area_m2=400.0), ("other", "other:no_rule")),
    (_x(storeys=6, shared_wall_share=0.0, elongation=3.0, area_m2=150.0), ("open_block", "open_block:slab")),
    (_x(storeys=6, shared_wall_share=0.0, elongation=3.0, area_m2=149.9), ("other", "other:no_rule")),
])
def test_each_rule_and_its_thresholds(inputs: TypologyInputs, expected: tuple[str, str]) -> None:
    assert classify(inputs, TCFG) == expected


def test_the_rules_follow_the_configuration() -> None:
    doc = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    doc["tower_min_storeys"] = 8
    doc["closed_block"]["max_storeys"] = 6
    doc["open_block"]["max_storeys"] = 7
    doc["historic_core_periods"] = ["pre_1900", "1900_1939", "1940_1959"]
    cfg = typology_config_from_document(doc)
    assert classify(_x(storeys=8), cfg) == ("tower", "tower:storeys")
    assert classify(_x(storeys=7), cfg) == ("other", "other:no_rule")
    assert classify(_x(storeys=7, shared_wall_share=0.0, elongation=3.0), cfg) == ("open_block", "open_block:slab")
    assert classify(_x(period="1940_1959"), cfg) == ("historic_core", "historic_core:attached_early_era")
    assert (cfg.path, cfg.sha256) == (None, None)


def test_the_default_configuration_is_read_with_its_hash() -> None:
    assert TCFG.path == str(DEFAULT_CONFIG_PATH) and TCFG.version == "0.1.0" and len(TCFG.sha256 or "") == 64
    assert TCFG.era_tags == ("building:start_date", "start_date", "construction_date")


def test_an_invalid_configuration_is_refused(tmp_path: Path) -> None:
    doc = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    bad = copy.deepcopy(doc)
    bad["attached_min_share"] = 1.5
    bad["historic_core_periods"] = ["victorian"]
    del bad["open_block"]["min_area_m2"]
    with pytest.raises(TypologyConfigError) as err:
        typology_config_from_document(bad)
    assert len(err.value.problems) == 3 and str(err.value).startswith("<document>: /attached_min_share")
    path = tmp_path / "typology.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(TypologyConfigError, match=str(path)):
        load_typology_config(path)
    with pytest.raises(TypologyConfigError, match="not found"):
        load_typology_config(tmp_path / "missing.json")


def test_a_config_file_records_its_path_hash_and_version(tmp_path: Path) -> None:
    path = tmp_path / "typology.json"
    path.write_bytes(DEFAULT_CONFIG_PATH.read_bytes())
    loaded = load_typology_config(path)
    assert loaded.path == str(path.resolve()) and loaded.version == "0.1.0"
    assert loaded.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


def test_a_config_error_names_the_file_and_the_full_path_of_the_value(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    with pytest.raises(TypologyConfigError) as err:
        load_typology_config(missing)
    assert str(err.value).startswith(f"{missing}: ")
    bad = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    bad["open_block"]["min_area_m2"] = -1
    with pytest.raises(TypologyConfigError) as err:
        typology_config_from_document(bad)
    assert err.value.problems[0].startswith("/open_block/min_area_m2: ")


def test_no_evidence_source_gives_no_era() -> None:
    assert building_era(["a"], {"a": {"tags": {"start_date": "1925"}}}, [], TCFG, []) is None


def test_the_record_rounds_each_measure_to_its_own_precision() -> None:
    m = Morphology(100.1234, 40.1234, 0.76543, 2.34567, 0.12345)
    x = TypologyInputs(m, 9.8765, 3, "unknown", "residential", None, False, None)
    inputs = typology_record(x, None, FacadeUse("residential", "estimated"), "k", 1, "recomputed", TCFG)["inputs"]
    assert (inputs["area_m2"], inputs["perimeter_m"], inputs["height_m"]) == (100.12, 40.12, 9.88)
    assert (inputs["compactness"], inputs["elongation"], inputs["shared_wall_share"]) == (0.7654, 2.346, 0.1235)


# --- one record per parcel ---


def _typologies(footprints: dict[str, Polygon], keys: dict[str, str], heights: dict[str, float], features: dict[str, dict[str, Any]],
                height_of: dict[str, dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[Issue]]:
    ids = sorted(footprints)
    issues: list[Issue] = []
    uses = {b: FacadeUse("residential", "estimated") for b in ids}
    out = building_typologies(ids, footprints, STRtree([footprints[i] for i in ids]), keys, features, height_of, heights,
                              uses, EVIDENCE, CFG, TCFG, issues)
    return out, issues


def test_the_parts_of_a_parcel_share_one_typology_measured_on_their_union() -> None:
    # Parcel P is two parts (10 × 10 and 10 × 10, 3 and 5 storeys) side by side; Q is a 10 × 10 neighbour east of it.
    footprints = {"p:1": box(0, 0, 10, 10), "p:2": box(10, 0, 20, 10), "q": box(20, 0, 30, 10)}
    keys = {"p:1": "parcel:P", "p:2": "parcel:P", "q": "q"}
    heights = {"p:1": 9.0, "p:2": 15.0, "q": 9.0}
    height_of = {"p:1": {"category": "unknown", "estimated_levels": 3}, "p:2": {"category": "residential_multi", "estimated_levels": 5},
                 "q": {"category": "unknown", "estimated_levels": 3, "landmark": True}}
    features = {"p:1": {"tags": {}}, "p:2": {"tags": {"start_date": "1925"}}, "q": {"tags": {}}}
    out, issues = _typologies(footprints, keys, heights, features, height_of)
    assert out["p:1"] is out["p:2"] and issues == []
    inputs = out["p:1"]["inputs"]
    # The wall between the two parts is inside the parcel; only the 10 m against q is shared, of a 60 m outline.
    assert inputs == {"key": "parcel:P", "parts": 2, "area_m2": 200.0, "perimeter_m": 60.0, "compactness": round(4 * math.pi * 200 / 3600, 4),
                      "elongation": 2.0, "shared_wall_share": round(10 / 60, 4), "height_m": 15.0, "storeys": 5, "storeys_origin": "heights_levels",
                      "category": "unknown", "use": "residential", "use_origin": "estimated", "catastro_use": None, "landmark": False,
                      "period": "1900_1939"}
    assert (out["p:1"]["typology"], out["p:1"]["origin"], out["p:1"]["rule"]) == ("historic_core", "estimated", "historic_core:attached_early_era")
    assert out["p:1"]["era"]["evidence"] == {"source_id": "p:2", "tag": "start_date", "value": "1925"}
    assert (out["q"]["typology"], out["q"]["era"], out["q"]["inputs"]["shared_wall_share"]) == ("landmark", None, 0.25)


def test_the_tallest_part_sets_the_height_whatever_the_id_order() -> None:
    footprints = {"p:1": box(0, 0, 10, 10), "p:2": box(10, 0, 20, 10)}
    height_of = {"p:1": {"category": "unknown", "estimated_levels": 5}, "p:2": {"category": "unknown", "estimated_levels": 3}}
    out, _ = _typologies(footprints, dict.fromkeys(footprints, "parcel:P"), {"p:1": 15.0, "p:2": 9.0}, {b: {"tags": {}} for b in footprints}, height_of)
    assert (out["p:2"]["inputs"]["height_m"], out["p:2"]["inputs"]["storeys"]) == (15.0, 5)


def test_a_neighbour_within_the_probe_counts_even_when_it_does_not_touch() -> None:
    gap = CFG.probe_m / 2
    footprints = {"a": box(0, 0, 10, 10), "b": box(10 + gap, 0, 20, 10)}
    height_of = {b: {"category": "unknown", "estimated_levels": 3} for b in footprints}
    out, _ = _typologies(footprints, {b: b for b in footprints}, dict.fromkeys(footprints, 9.0), {b: {"tags": {}} for b in footprints}, height_of)
    assert out["a"]["inputs"]["shared_wall_share"] == 0.25


def test_a_landmark_part_makes_its_parcel_a_landmark_and_the_catastro_use_counts() -> None:
    footprints = {"p:1": box(0, 0, 10, 10), "p:2": box(10, 0, 20, 10)}
    keys = dict.fromkeys(footprints, "parcel:P")
    features = {"p:1": {"tags": {}, "catastro_use": {**CATASTRO_USE, "use": "industrial"}}, "p:2": {"tags": {}}}
    height_of = {"p:1": {"category": "unknown"}, "p:2": {"category": "unknown", "landmark": True}}
    out, _ = _typologies(footprints, keys, {"p:1": 6.0, "p:2": 6.0}, features, height_of)
    assert out["p:1"]["typology"] == "landmark" and out["p:1"]["inputs"]["catastro_use"] == "industrial"
    assert out["p:1"]["inputs"]["storeys_origin"] == "recomputed" and out["p:1"]["era"]["source"] == "es.catastro"
    height_of["p:2"] = {"category": "unknown"}
    out, _ = _typologies(footprints, keys, {"p:1": 6.0, "p:2": 6.0}, features, height_of)
    assert (out["p:2"]["typology"], out["p:2"]["rule"]) == ("industrial_shed", "industrial_shed:use")
