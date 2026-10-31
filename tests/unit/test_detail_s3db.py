"""Simple 3D Buildings tag parsing: lengths, levels, compass bearings, colours and part heights."""

from __future__ import annotations

import pytest

from onward_generator.detail.config import load_detail_config
from onward_generator.detail.s3db import (
    ROOF_PALETTE,
    WALL_PALETTE,
    PartHeight,
    Tint,
    _lab,
    first_value,
    parse_colour,
    parse_direction,
    parse_levels,
    part_height,
    quantize_colour,
    tint_base,
    tint_material,
    tint_materials,
)

COLOURS = {name: m.color for name, m in load_detail_config().materials.items()}


def test_first_value_is_the_first_of_a_list_trimmed_and_lower_case() -> None:
    assert first_value({"roof:shape": " Gabled ;hipped"}, "roof:shape") == "gabled"
    assert first_value({}, "roof:shape") == ""
    assert first_value({"roof:shape": ""}, "roof:shape") == ""


@pytest.mark.parametrize(("raw", "levels"), [
    ("3", 3.0), ("0", 0.0), ("0.5", 0.5), ("1,5", 1.5), ("100", 100.0), ("-1", None), ("100.5", None), ("x", None), (None, None),
])
def test_levels_are_zero_to_a_hundred(raw: object, levels: float | None) -> None:
    assert parse_levels(raw) == levels


@pytest.mark.parametrize(("raw", "bearing"), [
    ("N", 0.0), ("nne", 22.5), ("SE", 135.0), ("S", 180.0), ("nnw", 337.5), ("90", 90.0), ("370", 10.0), ("-90", 270.0),
    (" 45 ", 45.0), ("inf", None), ("nan", None), ("north", None), ("", None), (None, None),
])
def test_directions_are_compass_bearings(raw: object, bearing: float | None) -> None:
    assert parse_direction(raw) == bearing


def test_colours_are_css_names_or_hex() -> None:
    assert parse_colour("white") == (1.0, 1.0, 1.0)
    assert parse_colour("Light Grey") == parse_colour("light_grey") == pytest.approx((211 / 255,) * 3)
    assert parse_colour("#FAF0E6") == pytest.approx((250 / 255, 240 / 255, 230 / 255))
    assert parse_colour("#f80") == pytest.approx((1.0, 136 / 255, 0.0))
    for bad in ("#12", "#1234", "#gggggg", "#ggg", "nope", "", None):
        assert parse_colour(bad) is None


@pytest.mark.parametrize(("raw", "key"), [
    ("#faf0e6", "white"), ("white", "white"), ("wheat", "cream"), ("yellow", "yellow"), ("#c88c3c", "ochre"),
    ("darkorange", "orange"), ("pink", "pink"), ("red", "red"), ("darkred", "red"), ("saddlebrown", "brown"),
    ("grey", "grey"), ("silver", "grey"), ("black", "dark"), ("green", "green"), ("blue", "blue"), ("steelblue", "blue"),
])
def test_a_facade_colour_is_quantized_to_the_nearest_wall_tint(raw: str, key: str) -> None:
    tint = quantize_colour(raw, WALL_PALETTE)
    assert tint is not None and tint.key == key


@pytest.mark.parametrize(("raw", "key"), [
    ("red", "red"), ("terracotta", "red"), ("#6e462d", "brown"), ("olive", "brown"), ("grey", "grey"), ("darkgrey", "grey"), ("black", "dark"),
    ("#323237", "dark"), ("white", "light"), ("green", "green"), ("teal", "green"), ("navy", "blue"),
])
def test_a_roof_colour_is_quantized_to_the_nearest_roof_tint(raw: str, key: str) -> None:
    tint = quantize_colour(raw, ROOF_PALETTE)
    assert tint is not None and tint.key == key


def test_a_colour_that_is_not_one_has_no_tint_and_a_tie_goes_to_the_earlier_entry() -> None:
    assert quantize_colour("purple-ish", WALL_PALETTE) is None
    assert quantize_colour("", ROOF_PALETTE) is None
    twins = (Tint("a", "#ff0000", (1.0, 0.0, 0.0)), Tint("b", "#ff0000", (0.0, 1.0, 0.0)))
    assert quantize_colour("white", twins) == twins[0]
    assert quantize_colour("white", twins[::-1]) == twins[1]
    # The nearest is chosen on what the eye sees: a dark blue is blue, not dark grey.
    near = (Tint("grey", "#3c3c3c", (0.2, 0.2, 0.2)), Tint("blue", "#0000ff", (0.0, 0.0, 1.0)))
    tint = quantize_colour("navy", near)
    assert tint is not None and tint.key == "blue"


@pytest.mark.parametrize(("rgb", "lab"), [
    # D65 CIELAB: white, the sRGB red and blue primaries (53.24, 80.09, 67.20 and 32.30, 79.19, −107.86 in the
    # usual tables), a colour on the linear segments of both curves, and one on their power segments.
    ((1.0, 1.0, 1.0), (100.0, 0.00526, -0.01041)),
    ((1.0, 0.0, 0.0), (53.23288, 80.10931, 67.22007)),
    ((0.0, 0.0, 1.0), (32.30259, 79.19666, -107.86368)),
    ((0.01, 0.02, 0.03), (1.30013, -0.31197, -1.20053)),
    ((0.2, 0.5, 0.8), (52.25484, 2.78839, -46.29596)),
])
def test_colours_are_compared_in_cielab(rgb: tuple[float, float, float], lab: tuple[float, float, float]) -> None:
    assert _lab(rgb) == pytest.approx(lab, abs=1e-4)


def test_a_tint_copies_its_base_under_the_palette_colour() -> None:
    blue = WALL_PALETTE[-1]
    assert tint_material("plaster_white", blue) == "plaster_white__tint_blue"
    assert tint_base("plaster_white__tint_blue") == "plaster_white"
    assert tint_base("plaster_white__tint_blue__tint_red") == "plaster_white"
    assert tint_base("party_plaster_white__tint_blue") == "party_plaster_white"
    assert tint_base("roof_tile") == "roof_tile"
    tints = tint_materials({"plaster_white": (0.9, 0.9, 0.9, 0.5), "roof_tile": (0.7, 0.3, 0.2, 1.0), "glass_dark": (0, 0, 0, 1)})
    assert len(tints) == len(WALL_PALETTE) + len(ROOF_PALETTE)
    assert tints["plaster_white__tint_blue"] == (*blue.colour, 0.5)
    assert tints["roof_tile__tint_green"] == (*ROOF_PALETTE[5].colour, 1.0)
    assert not any(name.startswith(("glass", "roof_flat", "brick")) for name in tints)
    assert tint_materials({}) == {}
    # Every base of the shipped configuration has all its tints, with the base's roughness.
    cfg = load_detail_config()
    assert cfg.materials["brick_red__tint_white"].roughness == cfg.materials["brick_red"].roughness
    assert cfg.materials["roof_flat_grey__tint_dark"].color == (*ROOF_PALETTE[3].colour, 1.0)
    assert cfg.materials["party_stone_light__tint_red"] == cfg.materials["stone_light__tint_red"]
    # Four wall bases and two roof bases, each tint with its party copy.
    assert sum("__tint_" in name for name in COLOURS) == 2 * (4 * len(WALL_PALETTE) + 2 * len(ROOF_PALETTE))


def test_a_part_with_a_height_tag_is_observed() -> None:
    h = part_height({"height": "20", "min_height": "10", "building:levels": "9"}, 3.0, "unknown")
    assert h == PartHeight(20.0, 10.0, "observed", "observed", "height", 0.0)
    assert h is not None and h.to_record() == {"height_m": 20.0, "min_height_m": 10.0, "origin": "observed",
                                               "min_height_origin": "observed", "rule": "height"}


def test_a_part_without_height_takes_its_levels_times_the_storey() -> None:
    h = part_height({"building:levels": "4", "roof:levels": "1", "building:min_level": "2"}, 3.5, "commercial")
    assert h == PartHeight(17.5, 7.0, "estimated", "estimated", "(building:levels 4 + roof:levels 1) × 3.5 m (commercial)", 1.0)
    assert part_height({"building:levels": "3", "min_level": "1"}, 3.0, "unknown") == PartHeight(
        9.0, 3.0, "estimated", "estimated", "(building:levels 3 + roof:levels 0) × 3 m (unknown)", 0.0)
    # building:min_level wins over min_level; min_height over both.
    assert part_height({"building:levels": "3", "building:min_level": "2", "min_level": "1"}, 3.0, "u").min_height_m == 6.0  # type: ignore[union-attr]
    assert part_height({"building:levels": "3", "building:min_level": "0", "min_level": "1"}, 3.0, "u").min_height_m == 0.0  # type: ignore[union-attr]
    assert part_height({"building:levels": "3", "min_height": "4", "min_level": "1"}, 3.0, "u").min_height_m == 4.0  # type: ignore[union-attr]
    rounded = part_height({"height": "10.12345", "min_height": "1.23456"}, 3.0, "u")
    assert rounded is not None and rounded.to_record()["height_m"] == 10.123 and rounded.to_record()["min_height_m"] == 1.235


def test_a_part_with_no_height_or_levels_has_none() -> None:
    assert part_height({}, 3.0, "unknown") is None
    assert part_height({"building:levels": "0", "roof:levels": "2"}, 3.0, "unknown") is None
    assert part_height({"height": "tall"}, 3.0, "unknown") is None
    assert part_height({"building:min_level": "0", "building:levels": "2"}, 3.0, "u").min_height_origin == "default"  # type: ignore[union-attr]
