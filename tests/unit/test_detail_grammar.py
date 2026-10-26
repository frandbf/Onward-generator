"""Roof and wall grammar: OSM tags win; estimates stay marked and stay off the default."""

from __future__ import annotations

from onward_generator.detail.config import load_detail_config
from onward_generator.detail.grammar import choose_roof, choose_wall

CFG = load_detail_config("config/detail.default.json")
RESIDENTIAL = CFG.templates["generic_residential"]


def test_osm_roof_shape_forces_hip() -> None:
    template, material, origin, shape = choose_roof(
        {"roof:shape": "gabled", "roof:material": "tiles"}, RESIDENTIAL, CFG.seed, "osm-way-1", 120.0,
    )
    assert origin == "observed" and shape == "gabled"
    assert material == "roof_tile" and template.parapet_m == 0 and template.hip is not None


def test_missing_roof_tag_keeps_large_parapet() -> None:
    template, _material, origin, shape = choose_roof({}, RESIDENTIAL, CFG.seed, "osm-way-311", 180.0)
    assert origin == "generated" and shape is None
    assert template.parapet_m == RESIDENTIAL.parapet_m


def test_osm_brick_is_observed_and_untagged_is_not_all_brick() -> None:
    material, origin = choose_wall({"building:material": "brick"}, RESIDENTIAL, CFG.seed, "b")
    assert material == "brick_red" and origin == "observed"
    origins = [choose_wall({}, RESIDENTIAL, CFG.seed, f"b{i}")[1] for i in range(40)]
    bricks = [choose_wall({}, RESIDENTIAL, CFG.seed, f"b{i}")[0] for i in range(40)]
    assert origins.count("observed") == 0
    assert bricks.count("brick_red") < 40 * 0.25
