"""Hierarchical building-height estimation. Footprints are local metres around MURCIA_CENTER."""

from __future__ import annotations

import copy
import json
import random
from pathlib import Path
from typing import Any

import pytest
from shapely.geometry import box, mapping

from onward_generator.config.loader import MAX_CONFIG_BYTES
from onward_generator.heights import (
    BuildingInput,
    HeightClass,
    HeightConfigError,
    HeightSource,
    SceneError,
    buildings_from_scene,
    estimate_heights,
    load_height_config,
)
from onward_generator.heights.config import DEFAULT_CONFIG_PATH, HeightConfig, config_from_document
from tests.conftest import MURCIA_CENTER

DEFAULT_DOC: dict[str, Any] = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def cfg() -> HeightConfig:
    return load_height_config()


def custom(tmp_path: Path | None = None, **changes: Any) -> HeightConfig:
    doc = copy.deepcopy(DEFAULT_DOC)
    for dotted, value in changes.items():
        *parents, leaf = dotted.split("__")
        target = doc
        for p in parents:
            target = target[p]
        target[leaf] = value
    return config_from_document(doc, base_dir=tmp_path)


def bld(n: int, x: float, y: float, size: float = 10.0, *, h: float | None = None, lv: int | None = None, **tags: str) -> BuildingInput:
    tags = {k.replace("__", ":"): v for k, v in tags.items()} or {"building": "yes"}
    tags.setdefault("building", "yes")
    return BuildingInput(f"osm-way-{n}", tags, box(x, y, x + size, y + size), h, lv)


def row(start: int, x: float, y: float, heights: list[float | None], **tags: str) -> list[BuildingInput]:
    """Touching 10 m boxes along x: one street block."""
    return [bld(start + i, x + 10 * i, y, h=h, **tags) for i, h in enumerate(heights)]


# --- Step 1: explicit data. ---


def test_explicit_height_is_preserved_and_marked_measured(cfg: HeightConfig) -> None:
    r = estimate_heights([bld(1, 0, 0, h=20.0)], cfg).get("osm-way-1")
    assert r is not None
    assert r.height_m == 20.0 and r.height_source is HeightSource.EXPLICIT_HEIGHT
    assert r.height_class is HeightClass.MEASURED and r.height_confidence == 0.95
    assert r.estimated_levels == 7 and r.levels_source == "derived_from_height"
    assert not r.needs_manual_review


def test_explicit_height_wins_over_levels(cfg: HeightConfig) -> None:
    r = estimate_heights([bld(1, 0, 0, h=12.0, lv=5)], cfg).get("osm-way-1")
    assert r and r.height_m == 12.0 and r.estimated_levels == 5 and r.levels_source == "explicit"


@pytest.mark.parametrize(
    "building, levels, expected",
    [("apartments", 4, 12.0), ("house", 2, 6.0), ("commercial", 4, 15.0), ("public", 3, 11.25), ("yes", 5, 15.0)],
)
def test_levels_use_storey_height_of_category(cfg: HeightConfig, building: str, levels: int, expected: float) -> None:
    r = estimate_heights([bld(1, 0, 0, lv=levels, building=building)], cfg).get("osm-way-1")
    assert r and r.height_source is HeightSource.EXPLICIT_LEVELS and r.height_m == expected
    assert r.height_class is HeightClass.INFERRED and r.estimated_levels == levels and r.levels_source == "explicit"


def test_out_of_bounds_explicit_height_is_ignored_and_flagged(cfg: HeightConfig) -> None:
    result = estimate_heights([bld(1, 0, 0, h=500.0, lv=4, building="apartments")], cfg)
    r = result.get("osm-way-1")
    assert r and r.height_source is HeightSource.EXPLICIT_LEVELS and r.height_m == 12.0
    assert r.needs_manual_review and "explicit_height_out_of_bounds" in r.review_reasons
    assert [i.code for i in result.issues] == ["explicit_height_out_of_bounds"]


def test_levels_height_is_clamped_to_bounds(cfg: HeightConfig) -> None:
    result = estimate_heights([bld(1, 0, 0, lv=60)], cfg)
    r = result.get("osm-way-1")
    assert r and r.height_m == cfg.bounds_max_m and r.needs_manual_review
    assert "estimate_clamped" in {i.code for i in result.issues}


# --- Step 2: building type. ---


@pytest.mark.parametrize(
    "tags, category, expected",
    [
        ({"building": "house"}, "residential_small", 6.0),
        ({"building": "apartments"}, "residential_multi", 12.0),
        ({"building": "retail"}, "commercial", 11.25),
        ({"building": "yes", "shop": "bakery"}, "commercial", 11.25),
        ({"building": "yes", "amenity": "police"}, "public", 11.25),
        ({"building": "warehouse"}, "industrial", 6.0),
        ({"building": "garage"}, "minor", 3.0),
    ],
)
def test_type_estimate(cfg: HeightConfig, tags: dict[str, str], category: str, expected: float) -> None:
    r = estimate_heights([BuildingInput("osm-way-1", tags, box(0, 0, 10, 10), None, None)], cfg).get("osm-way-1")
    assert r and r.height_source is HeightSource.BUILDING_TYPE
    assert r.category == category and r.height_m == expected and r.height_confidence == 0.4
    assert r.levels_source == "building_type"


def test_type_estimate_is_clamped_to_category_range(tmp_path: Path) -> None:
    config = custom(tmp_path, typical_levels__residential_multi=10)
    result = estimate_heights([bld(1, 0, 0, building="apartments")], config)
    assert result.get("osm-way-1").height_m == 15.0  # type: ignore[union-attr]
    assert [i.code for i in result.issues] == ["estimate_clamped"]


def test_type_is_preferred_over_neighbourhood(cfg: HeightConfig) -> None:
    buildings = row(1, 0, 0, [20.0, 20.0, 20.0]) + [bld(9, 30, 0, building="apartments")]
    r = estimate_heights(buildings, cfg).get("osm-way-9")
    assert r and r.height_source is HeightSource.BUILDING_TYPE and r.height_m == 12.0


# --- Step 3: neighbourhood. ---


def test_neighbourhood_median_is_robust_to_outliers(cfg: HeightConfig) -> None:
    buildings = row(1, 0, 0, [12.0, 15.0, 90.0, None])
    r = estimate_heights(buildings, cfg).get("osm-way-4")
    assert r and r.height_source is HeightSource.NEIGHBOURHOOD
    assert r.height_m == 15.0  # the mean would be 39 m
    assert r.samples == 3 and r.height_confidence == 0.55 and "same block and category" in r.rule


def test_neighbourhood_requires_min_samples(cfg: HeightConfig) -> None:
    r = estimate_heights(row(1, 0, 0, [12.0, 15.0, None]), cfg).get("osm-way-3")
    assert r and r.height_source is HeightSource.FALLBACK and r.height_m == 8.0


def test_neighbourhood_falls_back_to_any_category_in_block(cfg: HeightConfig) -> None:
    block = [bld(1 + i, 10 * i, 0, lv=5, building="apartments") for i in range(3)] + [bld(9, 30, 0)]
    distant_same_category = row(20, 0, 40, [9.0, 9.0, 9.0])
    r = estimate_heights(block + distant_same_category, cfg).get("osm-way-9")
    assert r and r.height_m == 15.0 and r.height_confidence == 0.5 and "same block" in r.rule


def test_neighbourhood_radius_same_category_when_isolated(cfg: HeightConfig) -> None:
    isolated = bld(9, 0, 0)
    same_category = [bld(20 + i, 30 * i, 30, h=h) for i, h in enumerate([9.0, 9.0, 10.0])]
    other = [bld(30 + i, 30 * i, -30, lv=5, building="apartments") for i in range(3)]
    r = estimate_heights([isolated, *same_category, *other], cfg).get("osm-way-9")
    assert r and r.height_m == 9.0 and r.height_confidence == 0.45 and "radius and same category" in r.rule


def test_neighbours_beyond_radius_are_ignored(cfg: HeightConfig) -> None:
    far = [bld(20 + i, 200 + 30 * i, 0, h=20.0) for i in range(3)]
    r = estimate_heights([bld(9, 0, 0), *far], cfg).get("osm-way-9")
    assert r and r.height_source is HeightSource.FALLBACK


def test_type_estimates_are_not_neighbourhood_samples_by_default(cfg: HeightConfig) -> None:
    buildings = [bld(1 + i, 10 * i, 0, building="apartments") for i in range(3)] + [bld(9, 30, 0)]
    r = estimate_heights(buildings, cfg).get("osm-way-9")
    assert r and r.height_source is HeightSource.FALLBACK


# --- Step 4: fallback and landmarks. ---


def test_fallback_default_for_unknown_type(cfg: HeightConfig) -> None:
    r = estimate_heights([bld(1, 0, 0)], cfg).get("osm-way-1")
    assert r and r.height_source is HeightSource.FALLBACK and r.height_m == 8.0
    assert r.height_class is HeightClass.FALLBACK and r.height_confidence == 0.2 and r.category == "unknown"


def test_fallback_default_is_configurable(tmp_path: Path) -> None:
    config = custom(tmp_path, fallback_m__unknown={"default": 10.0, "min": 6.0, "max": 15.0})
    assert estimate_heights([bld(1, 0, 0)], config).get("osm-way-1").height_m == 10.0  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "tags, reason",
    [({"building": "cathedral"}, "building=cathedral"), ({"building": "yes", "historic": "church"}, "historic=church"),
     ({"building": "yes", "amenity": "place_of_worship"}, "amenity=place_of_worship")],
)
def test_landmark_without_data_gets_flagged_placeholder(cfg: HeightConfig, tags: dict[str, str], reason: str) -> None:
    neighbours = row(10, 10, 0, [12.0, 12.0, 12.0])
    r = estimate_heights([BuildingInput("osm-way-1", tags, box(0, 0, 10, 10), None, None), *neighbours], cfg).get("osm-way-1")
    assert r and r.landmark and r.height_source is HeightSource.LANDMARK_PLACEHOLDER
    assert r.needs_manual_review and "landmark_without_authoritative_height" in r.review_reasons
    assert r.height_confidence == 0.1 and reason in r.rule


def test_landmark_with_explicit_height_is_trusted(cfg: HeightConfig) -> None:
    r = estimate_heights([bld(1, 0, 0, h=30.0, building="church")], cfg).get("osm-way-1")
    assert r and r.height_source is HeightSource.EXPLICIT_HEIGHT and not r.needs_manual_review


def test_landmark_height_from_levels_is_flagged(cfg: HeightConfig) -> None:
    r = estimate_heights([bld(1, 0, 0, lv=3, building="yes", heritage="2")], cfg).get("osm-way-1")
    assert r and r.height_source is HeightSource.EXPLICIT_LEVELS and "landmark_height_from_levels" in r.review_reasons


# --- Overrides (authoritative open data). ---


def write_overrides(tmp_path: Path, entries: list[dict[str, Any]]) -> HeightConfig:
    (tmp_path / "overrides.json").write_text(json.dumps({"version": "0.1.0", "overrides": entries}), encoding="utf-8")
    return custom(tmp_path, overrides_file="overrides.json")


OVERRIDE = {"id": "osm-way-1", "height_m": 41.5, "source": "Synthetic survey", "license": "CC0-1.0"}


def test_override_wins_and_is_measured(tmp_path: Path) -> None:
    config = write_overrides(tmp_path, [OVERRIDE, {**OVERRIDE, "id": "osm-way-77"}])
    result = estimate_heights([bld(1, 0, 0, h=20.0, building="cathedral")], config)
    r = result.get("osm-way-1")
    assert r and r.height_source is HeightSource.OVERRIDE and r.height_m == 41.5
    assert r.height_class is HeightClass.MEASURED and not r.needs_manual_review and "Synthetic survey" in r.rule
    assert {i.code for i in result.issues} == {"override_differs_from_osm", "override_not_in_scene"}
    assert config.overrides_file is not None and len(config.overrides_file.sha256) == 64


@pytest.mark.parametrize(
    "entries",
    [[{**OVERRIDE, "height_m": 500}], [OVERRIDE, OVERRIDE], [{"id": "osm-way-1", "height_m": 10}], [{**OVERRIDE, "id": "way 1"}]],
)
def test_invalid_overrides_are_rejected(tmp_path: Path, entries: list[dict[str, Any]]) -> None:
    with pytest.raises(HeightConfigError):
        write_overrides(tmp_path, entries)


def test_an_overrides_file_over_the_config_limit_loads(tmp_path: Path) -> None:
    # Source plugins write one entry per building; a large Catastro map passes 1 MiB (Madrid r300: 1.2 MB).
    entries = [{**OVERRIDE, "id": f"osm-way-{i}", "source": "Synthetic survey " + "x" * 40} for i in range(1, 12001)]
    config = write_overrides(tmp_path, entries)
    assert (tmp_path / "overrides.json").stat().st_size > MAX_CONFIG_BYTES
    assert config.overrides_file is not None and len(config.overrides) == 12000


# --- Global guarantees. ---


def city(seed: int) -> list[BuildingInput]:
    rng = random.Random(seed)
    kinds = [{}, {}, {}, {"building": "apartments"}, {"building": "church"}, {"shop": "bakery"}]
    buildings = []
    for i in range(60):
        tags = {"building": "yes", **rng.choice(kinds)}
        h = rng.choice([None, None, None, rng.uniform(6, 25)])
        lv = rng.choice([None, None, rng.randint(1, 8)])
        buildings.append(BuildingInput(f"osm-way-{1000 + i}", tags, box(12 * (i % 10), 12 * (i // 10), 12 * (i % 10) + 11.5, 12 * (i // 10) + 11.5), h, lv))
    return buildings


def test_every_building_gets_all_fields_within_bounds(cfg: HeightConfig) -> None:
    buildings = city(7)
    result = estimate_heights(buildings, cfg)
    assert len(result.buildings) == len(buildings)
    sources = set()
    for r in result.buildings:
        d = r.to_dict()
        assert {"height_m", "height_source", "height_confidence", "estimated_levels"} <= d.keys()
        assert cfg.bounds_min_m <= r.height_m <= cfg.bounds_max_m and r.estimated_levels >= 1
        assert 0 <= r.height_confidence <= 1
        sources.add(r.height_source)
    assert len(sources) >= 4


def test_result_is_independent_of_input_order(cfg: HeightConfig) -> None:
    buildings = city(11)
    shuffled = buildings[:]
    random.Random(3).shuffle(shuffled)
    a = [b.to_dict() for b in estimate_heights(buildings, cfg).buildings]
    b = [b.to_dict() for b in estimate_heights(shuffled, cfg).buildings]
    assert a == b and [d["building_id"] for d in a] == sorted(d["building_id"] for d in a)


# --- Scene contract. ---


def scene(features: list[dict[str, Any]], **frame: Any) -> dict[str, Any]:
    return {
        "schema": "onward_generator.geo_scene",
        "schema_version": "0.2.0",
        "frame": {"crs": "EPSG:25830", "coordinate_system": "local_grid", "unit": "m", "origin_wgs84": list(MURCIA_CENTER), **frame},
        "features": features,
    }


def feature(fid: str, geom: Any, attributes: dict[str, Any] | None = None, kind: str = "building") -> dict[str, Any]:
    g = mapping(geom) if geom is not None else None
    return {
        "id": fid,
        "kind": kind,
        "tags": {"building": "yes"},
        "geometry": {"type": g["type"], "coordinates": g["coordinates"], "unit": "m"} if g else None,
        "attributes": attributes or {},
    }


def test_scene_reader_uses_only_observed_attributes() -> None:
    features = [
        feature("osm-way-1", box(0, 0, 10, 10), {"height_m": {"value": 9.0, "origin": "inferred", "rule": "x"},
                                                 "levels": {"value": 3, "origin": "observed", "source_tag": "building:levels"}}),
        feature("osm-way-2", box(20, 0, 30, 10), {"height_m": {"value": 14.0, "origin": "observed", "source_tag": "height"}}),
        feature("osm-way-3", box(0, 20, 5, 25), kind="highway"),
        feature("osm-way-4", None),
    ]
    buildings, issues = buildings_from_scene(scene(features))
    by_id = {b.id: b for b in buildings}
    assert set(by_id) == {"osm-way-1", "osm-way-2", "osm-way-4"}
    assert by_id["osm-way-1"].explicit_height_m is None and by_id["osm-way-1"].explicit_levels == 3
    assert by_id["osm-way-2"].explicit_height_m == 14.0
    assert [(i.code, i.building_id) for i in issues] == [("missing_footprint", "osm-way-4")]


@pytest.mark.parametrize(
    "doc",
    [
        {**scene([]), "schema": "something_else"},
        {**scene([]), "schema_version": "9.9.9"},
        scene([], unit="degree"),
        scene([], coordinate_system="wgs84"),
    ],
)
def test_scene_reader_rejects_bad_scenes(doc: dict[str, Any]) -> None:
    with pytest.raises(SceneError):
        buildings_from_scene(doc)
