"""Buildings of up to five storeys get an interior, and a climb to a flat roof where it fits (ADR-004, amendment)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from onward_generator.detail import detail_scene, load_base, load_detail_config
from onward_generator.generation import load_generation_config
from tests.conftest import _detail_inputs

GEN = load_generation_config()
CFG = load_detail_config()
ROAD_Y = 6
LOW: dict[int, tuple[list[tuple[float, float]], dict[str, str]]] = {
    701: ([(-70, ROAD_Y), (-55, ROAD_Y), (-55, 18), (-70, 18)], {"building": "yes", "building:levels": "1"}),
    702: ([(-50, ROAD_Y), (-35, ROAD_Y), (-35, 18), (-50, 18)], {"building": "yes", "building:levels": "2"}),
    703: ([(-30, ROAD_Y), (-15, ROAD_Y), (-15, 18), (-30, 18)], {"building": "apartments", "building:levels": "5"}),
    704: ([(-10, ROAD_Y), (5, ROAD_Y), (5, 18), (-10, 18)], {"building": "apartments", "building:levels": "6"}),
    705: ([(10, ROAD_Y), (30, ROAD_Y), (30, 26), (10, 26)], {"building": "apartments", "building:levels": "3"}),
}


@pytest.fixture(scope="module")
def low_inputs(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, Path]:
    return _detail_inputs(tmp_path_factory.mktemp("low"), LOW, {})


def _detail(inputs: tuple[Path, Path, Path], cfg: Any = CFG) -> Any:
    scene, heights, base = inputs
    generated, ref = load_base(base, scene, heights, GEN)
    return detail_scene(generated, ref, heights, scene, cfg)


def _interior(detailed: Any, way: int) -> dict[str, Any]:
    return next(o.record for o in detailed.objects if o.id == f"interior/osm-way-{way}")


@pytest.mark.parametrize(("way", "storeys", "floors"), [(701, 1, 1), (702, 2, 1), (703, 5, 3), (705, 3, 2)])
def test_every_low_building_has_a_way_up_to_its_roof(low_inputs: tuple[Path, Path, Path], way: int, storeys: int, floors: int) -> None:
    detailed = _detail(low_inputs)
    assert not detailed.critical
    record = _interior(detailed, way)
    assert record["kind"] == "residential" and record["roof_access"] is True
    assert record["accessible_floors"] <= floors
    climbs = [c for c in record["connections"] if c["type"] == "stair" and c["to"] == "roof"]
    assert len(climbs) == 1, record["connections"]
    assert "roof_deck" in record["collider_parts"] and "upper_solid" not in record["collider_parts"]
    building = next(o.record for o in detailed.objects if o.id == f"building/osm-way-{way}")
    assert building["interior"] is True and building["facade"]["storeys"]["count"] == storeys
    assert building["checks"]["facade_open_edges"] == 0


def test_a_building_over_the_ceiling_gets_no_stair_interior(low_inputs: tuple[Path, Path, Path]) -> None:
    detailed = _detail(low_inputs)
    assert not any(o.id == "interior/osm-way-704" for o in detailed.objects)
    assert "osm-way-704" not in detailed.stats["interiors"]


def test_the_ceiling_is_read_from_the_rule(low_inputs: tuple[Path, Path, Path]) -> None:
    rule = replace(CFG.interiors.rule, max_levels=6)
    wider = _detail(low_inputs, replace(CFG, interiors=replace(CFG.interiors, rule=rule)))
    assert _interior(wider, 704)["kind"] == "residential"
    narrower = _detail(low_inputs, replace(CFG, interiors=replace(CFG.interiors, rule=replace(rule, min_levels=2, max_levels=2))))
    assert narrower.stats["interiors"] == ["osm-way-702"]


# A pitched roof stays closed, whichever way it comes: a tagged shape or the template's own hip.
PITCHED = {
    711: ([(-30, ROAD_Y), (-15, ROAD_Y), (-15, 18), (-30, 18)], {"building": "apartments", "building:levels": "3", "roof:shape": "gabled"}),
    712: ([(-10, ROAD_Y), (0, ROAD_Y), (0, 14), (-10, 14)], {"building": "shed"}),
}
# 722 is walled in by its neighbours, so it has no wall for a door.
ENCLOSED = {
    721: ([(-30, ROAD_Y), (-10, ROAD_Y), (-10, 12), (-30, 12)], {"building": "apartments", "building:levels": "3"}),
    722: ([(-24, 12), (-16, 12), (-16, 20), (-24, 20)], {"building": "apartments", "building:levels": "3"}),
    723: ([(-30, 12), (-24, 12), (-24, 20), (-30, 20)], {"building": "apartments", "building:levels": "3"}),
    724: ([(-16, 12), (-10, 12), (-10, 20), (-16, 20)], {"building": "apartments", "building:levels": "3"}),
    725: ([(-30, 20), (-10, 20), (-10, 26), (-30, 26)], {"building": "apartments", "building:levels": "3"}),
}
WALLED = "osm-way-722"
OPEN_IDS = ("osm-way-721", "osm-way-723", "osm-way-724", "osm-way-725")


@pytest.mark.parametrize("way", [711, 712])
def test_a_pitched_roof_has_no_climb(tmp_path: Path, way: int) -> None:
    detailed = _detail(_detail_inputs(tmp_path, PITCHED, {}))
    record = next((o.record for o in detailed.objects if o.id == f"interior/osm-way-{way}"), None)
    assert record is not None and record["roof_access"] is False
    assert not any(c["to"] == "roof" for c in record["connections"])


@pytest.fixture(scope="module")
def enclosed_inputs(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, Path]:
    return _detail_inputs(tmp_path_factory.mktemp("enclosed"), ENCLOSED, {})


def _frontage_issues(detailed: Any) -> list[tuple[str, str | None, str]]:
    return [(i.code, i.severity, i.object_id, i.message) for i in detailed.issues if i.message == "no_street_frontage"]


def _interiors(**changes: Any) -> Any:
    return replace(CFG, interiors=replace(CFG.interiors, **changes))


def test_a_building_with_no_street_wall_says_why_it_has_no_interior(enclosed_inputs: tuple[Path, Path, Path]) -> None:
    detailed = _detail(enclosed_inputs)
    assert _frontage_issues(detailed) == [("interior_not_generated", "info", WALLED, "no_street_frontage")]
    assert WALLED not in detailed.stats["interiors"]


def test_the_last_place_in_the_quota_still_reports_it(enclosed_inputs: tuple[Path, Path, Path]) -> None:
    detailed = _detail(enclosed_inputs, _interiors(min_buildings=1, max_buildings=1, exclude_ids=frozenset(OPEN_IDS)))
    assert _frontage_issues(detailed) == [("interior_not_generated", "info", WALLED, "no_street_frontage")]


def test_a_spent_quota_or_an_excluded_building_reports_nothing(enclosed_inputs: tuple[Path, Path, Path]) -> None:
    spent = _detail(enclosed_inputs, _interiors(min_buildings=1, max_buildings=1, include_ids=("osm-way-721",)))
    assert spent.stats["interiors"] == ["osm-way-721"] and _frontage_issues(spent) == []
    excluded = _detail(enclosed_inputs, _interiors(exclude_ids=frozenset({WALLED})))
    assert _frontage_issues(excluded) == []
