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


