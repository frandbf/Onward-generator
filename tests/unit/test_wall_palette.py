"""The wall palette of an atmosphere preset on the facade atlas (plan E8, ADR-037)."""

from __future__ import annotations

import io
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from onward_generator.export.atmosphere import AtmosphereError
from onward_generator.maps.workspace import create_map
from onward_generator.materials.__main__ import wall_palette
from onward_generator.materials.trim import WallPalette, apply_trim, palette_atlas, palette_tint
from onward_generator.next_step import materials_command
from tests.unit.test_trim import COLORS, MANIFEST, _building, small_table  # noqa: F401 - small_table is a fixture

GOLDEN = WallPalette("golden_hour", 1.0, 0.08, 0.85)


def _luma(rgb: tuple[float, float, float]) -> float:
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def test_a_neutral_palette_keeps_the_colour() -> None:
    assert palette_tint((0.5, 0.3, 0.1), WallPalette("p", 1.0, 0.0, 1.0)) == pytest.approx((0.5, 0.3, 0.1), abs=1e-6)


def test_desaturation_moves_the_colour_toward_its_grey() -> None:
    grey = _luma((0.5, 0.3, 0.1))
    assert palette_tint((0.5, 0.3, 0.1), WallPalette("p", 1.0, 1.0, 1.0)) == pytest.approx((grey, grey, grey), abs=1e-6)
    half = palette_tint((0.5, 0.3, 0.1), WallPalette("p", 1.0, 0.5, 1.0))
    assert half == pytest.approx(((0.5 + grey) / 2, (0.3 + grey) / 2, (0.1 + grey) / 2), abs=1e-6)


def test_the_value_cap_scales_the_srgb_channels_of_a_bright_wall() -> None:
    capped = palette_tint((1.0, 0.5, 0.25), WallPalette("p", 1.0, 0.0, 0.5))
    assert capped[0] == pytest.approx(0.214041, abs=1e-6)
    srgb = [c * 12.92 if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055 for c in capped]
    assert max(srgb) == pytest.approx(0.5, abs=1e-6)
    assert srgb[1] / srgb[0] == pytest.approx(0.735357, abs=1e-5)
    dark = (0.1, 0.05, 0.02)
    assert palette_tint(dark, WallPalette("p", 1.0, 0.0, 0.5)) == pytest.approx(dark, abs=1e-6)


def test_a_wall_exactly_at_the_cap_is_kept() -> None:
    assert palette_tint((0.214041, 0.1, 0.05), WallPalette("p", 1.0, 0.0, 0.5))[0] == pytest.approx(0.214041, abs=1e-6)


def test_a_negative_channel_counts_as_black() -> None:
    assert palette_tint((-0.1, 0.5, 0.5), WallPalette("p", 1.0, 0.0, 1.0))[0] == 0.0


def test_the_tint_is_rounded_to_six_decimals() -> None:
    tint = palette_tint((0.123456789, 0.2, 0.3), WallPalette("p", 1.0, 0.0, 1.0))
    assert tint[0] == round(tint[0], 6) and tint[0] == pytest.approx(0.123457, abs=1e-6)


def test_grime_scales_the_streaks_and_the_splash_and_nothing_else() -> None:
    atlas = {"grime": {"streak_dark": 0.2, "base_splash_dark": 0.3, "mottle": 0.1}, "walls": {"a": "plaster"}}
    darker = palette_atlas(atlas, WallPalette("p", 1.5, 0.0, 1.0))
    assert darker == {"grime": {"streak_dark": pytest.approx(0.3), "base_splash_dark": pytest.approx(0.45), "mottle": 0.1}, "walls": {"a": "plaster"}}
    assert atlas["grime"]["streak_dark"] == 0.2


def test_no_palette_keeps_the_atlas_byte_for_byte_and_a_palette_darkens_it(small_table: Path) -> None:  # noqa: F811 - the fixture
    plain = apply_trim([_building()], MANIFEST, COLORS, table_path=small_table)
    assert "atmosphere" not in plain.report
    toned = apply_trim([_building()], MANIFEST, COLORS, table_path=small_table, palette=GOLDEN)
    assert toned.png != plain.png
    block = toned.report["atmosphere"]
    assert {k: block[k] for k in ("preset", "origin", "grime", "wall_desaturation", "wall_value_max")} == {
        "preset": "golden_hour", "origin": "generated", "grime": 1.0, "wall_desaturation": 0.08, "wall_value_max": 0.85}
    assert block["wall_luminance_after"] < block["wall_luminance_before"]
    walls = sorted(json.loads(small_table.read_text(encoding="utf-8"))["styles"]["mediterranean"]["atlas"]["walls"])
    before = sum(_luma(tuple(COLORS[w][:3])) for w in walls) / len(walls)
    assert block["wall_luminance_before"] == round(before, 4)
    after = sum(_luma(palette_tint(COLORS[w], GOLDEN)) for w in walls) / len(walls)
    assert block["wall_luminance_after"] == round(after, 4)
    plain_px = np.asarray(Image.open(io.BytesIO(plain.png)).convert("L"), dtype=float)
    toned_px = np.asarray(Image.open(io.BytesIO(toned.png)).convert("L"), dtype=float)
    assert toned_px.mean() < plain_px.mean()


def test_the_cli_reads_the_preset_and_refuses_an_unknown_one() -> None:
    assert wall_palette(None) is None and wall_palette("neutral") is None
    assert wall_palette("golden_hour") == GOLDEN
    assert wall_palette("overcast") == WallPalette("overcast", 1.2, 0.05, 0.85)
    with pytest.raises(AtmosphereError, match=r"^atmosphere must be one of neutral, golden_hour, overcast, not 'dusk'$"):
        wall_palette("dusk")


def test_the_materials_stage_carries_the_preset_of_map_json(tmp_path: Path) -> None:
    root = tmp_path / "madrid"
    create_map(root, map_id="madrid", name="Madrid", latitude=40.40657, longitude=-3.719187, radius_m=150)
    dress = root / "data" / "processed" / "scene_dress"
    assert "--atmosphere" not in materials_command(dress).argv
    create_map(tmp_path / "golden", map_id="golden", name="Golden", latitude=40.40657, longitude=-3.719187, radius_m=150, atmosphere="golden_hour")
    argv = list(materials_command(tmp_path / "golden" / "data" / "processed" / "scene_dress").argv)
    assert argv[-4:-2] == ["--atmosphere", "golden_hour"]
    assert "--atmosphere" not in materials_command(tmp_path / "loose" / "scene_dress").argv


def test_a_dark_channel_takes_the_linear_piece_both_ways() -> None:
    """0.002 is under both knees: 0.025840 in sRGB, halved by the cap of 0.5 to 0.012920, back to 0.001."""
    capped = palette_tint((1.0, 0.002, 0.0), WallPalette("p", 1.0, 0.0, 0.5))
    assert capped == (0.214041, 0.001, 0.0)


def test_the_cap_divides_by_the_peak() -> None:
    """A peak of 0.906 (0.8 linear) capped at 0.5: every channel scales by 0.5 / 0.906."""
    capped = palette_tint((0.8, 0.3, 0.1), WallPalette("p", 1.0, 0.0, 0.5))
    assert capped == (0.214041, 0.084654, 0.030858)


def test_green_and_blue_are_rounded_to_six_decimals() -> None:
    tint = palette_tint((0.5, 0.123456789, 0.987654321), WallPalette("p", 1.0, 0.0, 1.0))
    assert tint == (0.5, 0.123457, 0.987654)


def test_an_alpha_channel_is_left_out() -> None:
    assert palette_tint((0.5, 0.3, 0.1, 1.0), WallPalette("p", 1.0, 0.0, 0.5)) == palette_tint((0.5, 0.3, 0.1), WallPalette("p", 1.0, 0.0, 0.5))


def test_the_luminance_after_is_rounded_to_four_decimals(small_table: Path) -> None:  # noqa: F811 - the fixture
    """overcast's 5 % at 0.85 gives a mean of 0.62129..., which four decimals round to 0.6213."""
    toned = apply_trim([_building()], MANIFEST, COLORS, table_path=small_table, palette=WallPalette("overcast", 1.2, 0.05, 0.85))
    assert toned.report["atmosphere"]["wall_luminance_after"] == 0.6213
