"""Baking the trim-sheet atlas (ADR-020): colour, grime, gutters, cells, determinism."""

from __future__ import annotations

import copy
import hashlib
import warnings
from typing import Any

import numpy as np
import pytest

from onward_generator.materials.trim_atlas import (
    BRIGHTNESS,
    INF,
    bake_trim_atlas,
    between,
    boarded_pixels,
    cornice_pixels,
    door_pixels,
    grain,
    linear_to_srgb8,
    luma_rgb,
    sample_periodic,
    shop_glass_pixels,
    srgb_to_linear,
    stone_pixels,
    style_seed,
    wall_pixels,
    window_pixels,
    zocalo_pixels,
)
from onward_generator.materials.trim_style import load_style_table, trim_layout

TINTS = {
    "brick_red": (0.62, 0.28, 0.22), "plaster_cream": (0.96, 0.86, 0.62), "plaster_ochre": (0.93, 0.66, 0.28),
    "plaster_salmon": (0.88, 0.68, 0.58), "plaster_white": (0.93, 0.92, 0.89), "stone_warm": (0.84, 0.70, 0.46),
}


def small_atlas() -> dict[str, Any]:
    """The mediterranean recipe on a 1024 px atlas: the same strips at half the rows."""
    atlas = copy.deepcopy(dict(load_style_table().styles["mediterranean"].atlas or {}))
    atlas.update(size_px=1024, height_px=2048, fit_row_px=192, fit_cell_px=96, cornice_px=64, zocalo_px=64, stone_px=32)
    atlas["grime"] = dict(atlas["grime"], streaks_per_strip=12)
    return atlas


def sources() -> dict[str, np.ndarray]:
    rng = np.random.default_rng(7)
    return {
        "plaster": rng.integers(150, 200, (64, 64, 3), dtype=np.uint8),
        "stone": rng.integers(60, 120, (64, 64, 3), dtype=np.uint8),
        "brick": rng.integers(90, 160, (32, 64, 3), dtype=np.uint8),
    }


def test_srgb_round_trip_and_known_values() -> None:
    assert srgb_to_linear(np.array([0.0, 1.0])).tolist() == [0.0, 1.0]
    assert float(srgb_to_linear(np.array(0.5))) == pytest.approx(0.2140, abs=1e-4)
    assert float(srgb_to_linear(np.array(0.04))) == pytest.approx(0.04 / 12.92)
    assert linear_to_srgb8(np.array([0.0, 0.2159, 1.0, 2.0, -1.0])).tolist() == [0, 128, 255, 255, 0]
    assert linear_to_srgb8(np.array(0.003)).tolist() == round(0.003 * 12.92 * 255)
    values = np.arange(256) / 255.0
    assert np.array_equal(linear_to_srgb8(srgb_to_linear(values)), np.arange(256))


def test_luma_of_primaries() -> None:
    rgb = np.array([[[255, 0, 0], [0, 255, 0], [0, 0, 255]]], dtype=np.uint8)
    assert luma_rgb(rgb)[0].tolist() == pytest.approx([0.2126, 0.7152, 0.0722])


def test_grain_keeps_the_colour_and_scales_contrast() -> None:
    image = np.array([[0.2, 0.4], [0.6, 0.8]])
    assert float(grain(image).mean()) == pytest.approx(BRIGHTNESS)
    assert np.allclose(grain(image, 0.0), BRIGHTNESS)
    full, half = grain(image) - BRIGHTNESS, grain(image, 0.5) - BRIGHTNESS
    assert np.allclose(half, full / 2)
    linear = srgb_to_linear(image)
    assert np.allclose(grain(image), BRIGHTNESS * linear / linear.mean())


def test_between_is_strict_and_takes_infinite_bounds() -> None:
    values = np.array([-1.0, 0.0, 0.5, 1.0, 2.0])
    assert between(values, 0.0, 1.0).tolist() == [False, False, True, False, False]
    assert between(values, -INF, 1.0).tolist() == [True, True, True, False, False]
    assert between(values, 0.0, INF).tolist() == [False, False, True, True, True]
    assert between(0.5, 0.0, 1.0).ndim == 0
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert between(np.array([0.0, 1.0]), 0.0, INF).tolist() == [False, True]


def test_sample_periodic_resamples_in_float_and_keeps_tiny_tiles() -> None:
    image = np.array([[10, 11], [200, 13]], dtype=np.uint8)
    as_float = sample_periodic(image.astype(np.float32), 7, 5, (2.0, 2.0), 2.0)
    assert np.allclose(sample_periodic(image, 7, 5, (2.0, 2.0), 2.0), as_float)
    # A 4 x 4 box of 0-255 averages to a fraction a uint8 resample would round.
    odd = np.arange(16, dtype=np.uint8).reshape(4, 4) * 17
    assert sample_periodic(odd, 1, 1, (1.0, 1.0), 1.0)[0, 0] == pytest.approx(odd.mean())
    # 1.2 px a repeat rounds to one pixel: every column of a row is the same, and every row of a column.
    ramp = np.arange(36, dtype=np.float32).reshape(6, 6)
    assert np.ptp(sample_periodic(ramp, 6, 6, (5.0, 5.0), 1.0)) == 0.0
    assert np.ptp(sample_periodic(ramp, 6, 60, (5.0, 5.0), 1.0), axis=1).max() == 0.0
    assert np.ptp(sample_periodic(ramp, 60, 6, (5.0, 5.0), 1.0), axis=0).max() == 0.0


def test_sample_periodic_holds_whole_repeats() -> None:
    image = np.arange(16 * 16, dtype=np.float32).reshape(16, 16)
    out = sample_periodic(image, 120, 30, (12.0, 3.0), 2.0)
    assert out.shape == (30, 120)
    # Six repeats of 20 px across 12 m.
    assert np.allclose(out[:, :20], out[:, 20:40])
    assert np.allclose(out[:, :20], out[:, 100:])
    assert not np.allclose(out[:, :10], out[:, 10:20])
    # 3 m tall with a 2 m repeat: rows wrap after 20 px.
    assert np.allclose(out[:10], out[20:30])
    rgb = np.stack([image, image * 2, image * 3], axis=-1)
    coloured = sample_periodic(rgb, 120, 30, (12.0, 3.0), 2.0)
    assert coloured.shape == (30, 120, 3)
    assert np.allclose(coloured[..., 1], 2 * coloured[..., 0], rtol=1e-4)
    # 13 m with a 2 m repeat rounds to 6 repeats (6.5 → 6, banker's rounding is not involved here).
    assert np.allclose(sample_periodic(image, 120, 30, (13.0, 3.0), 2.0)[:, :20], out[:, :20])
    assert sample_periodic(image, 60, 10, (0.5, 3.0), 2.0).shape == (10, 60)


def test_style_seed_is_stable() -> None:
    assert style_seed("mediterranean") == style_seed("mediterranean")
    assert style_seed("mediterranean") != style_seed("anglo")
    assert style_seed("x") == int.from_bytes(hashlib.sha256(b"trim:x").digest()[:8], "big")


def _rng() -> np.random.Generator:
    return np.random.default_rng(3)


def test_a_wall_strip_is_its_colour_with_a_string_course_and_a_shadow_under_the_next() -> None:
    atlas = small_atlas()
    pixels = wall_pixels("plaster", TINTS["plaster_ochre"], 256, 100, atlas, sources(), _rng())
    assert pixels.shape == (100, 256, 3)
    middle = pixels[40:60].mean(axis=(0, 1))
    assert middle / middle.max() == pytest.approx(np.array(TINTS["plaster_ochre"]) / 0.93, abs=0.03)
    # Rows run from the top: the last rows are the floor line, lit by the string course.
    course = pixels[-3:].mean()
    shadow = pixels[:3].mean()
    assert course > middle.mean() * 1.03
    assert shadow < middle.mean() * 0.9
    brick = wall_pixels("brick", TINTS["brick_red"], 256, 100, atlas, sources(), _rng())
    assert brick[40:60].mean(axis=(0, 1)).argmax() == 0
    ashlar = wall_pixels("ashlar", TINTS["stone_warm"], 256, 100, atlas, sources(), _rng())
    plaster = wall_pixels("plaster", TINTS["stone_warm"], 256, 100, atlas, sources(), _rng())
    assert ashlar.min() < plaster.min()


def test_zocalo_cornice_and_stone_strips() -> None:
    atlas = small_atlas()
    zocalo = zocalo_pixels(256, 60, atlas, sources(), _rng())
    assert zocalo.shape == (60, 256, 3)
    assert zocalo[-6:].mean() < zocalo[20:30].mean() * 0.85  # splashed foot
    assert zocalo[:2].mean() > zocalo[20:30].mean()  # lit chamfer on top
    cornice = cornice_pixels(256, 60, atlas, sources(), _rng())
    assert cornice[-2:].mean() < cornice[20:40].mean() * 0.7  # the drip shadow at the foot
    stone = stone_pixels(256, 40, atlas, sources())
    assert stone[0].mean() < stone[20].mean()
    assert stone[-1].mean() < stone[20].mean()
    assert stone[20].mean(axis=0) / stone[20].mean() == pytest.approx(np.array(atlas["stone_color"]) / np.mean(atlas["stone_color"]), abs=0.05)


def test_fit_cells_paint_windows_doors_glass_and_boards() -> None:
    family = {"frame": [0.9, 0.9, 0.9], "shutter": [0.2, 0.6, 0.2], "closed": [0.0]}
    open_window = window_pixels(48, 96, family, 0.0)
    closed = window_pixels(48, 96, family, 0.8)
    assert open_window.shape == (96, 48, 3)
    # The shutter box on top is the shutter colour.
    assert open_window[2, 24].tolist() == pytest.approx([0.16, 0.48, 0.16])
    # Lowered over 80 %: the middle of the window is shutter, green above all.
    assert closed[48, 24].argmax() == 1
    assert open_window[48, 12].argmax() == 2  # glass is blue-ish
    door = door_pixels(48, 96, (0.3, 0.2, 0.1), _rng())
    assert door.shape == (96, 48, 3)
    assert door[48].mean(axis=0).argmax() == 0
    glass = shop_glass_pixels(48, 96)
    assert glass[1, 24].tolist() == [0.5, 0.51, 0.52]
    boards = boarded_pixels(48, 96, _rng())
    assert boards.shape == (96, 48, 3)
    assert boards.mean(axis=(0, 1)).argmax() == 0


def test_bake_is_deterministic_opaque_and_padded() -> None:
    atlas = small_atlas()
    layout = trim_layout(atlas)
    image = bake_trim_atlas("mediterranean", atlas, layout, TINTS, sources())
    assert image.shape == (2048, 1024, 4)
    assert image.dtype == np.uint8
    assert (image[..., 3] == 255).all()
    again = bake_trim_atlas("mediterranean", atlas, layout, TINTS, sources())
    assert np.array_equal(image, again)
    assert not np.array_equal(image, bake_trim_atlas("anglo", atlas, layout, TINTS, sources()))
    gutter = layout.gutter
    for strip in (*(s for tones in layout.tones.values() for s in tones), layout.zocalo, layout.cornice, layout.stone):
        top, bottom = strip.row0 + gutter, strip.row1 - gutter - 1
        assert (image[strip.row0 : top] == image[top]).all()
        assert (image[bottom + 1 : strip.row1] == image[bottom]).all()
    cell = layout.cells["window_0_0"]
    assert (image[cell.row0 : cell.row0 + gutter, cell.col0 + gutter] == image[cell.row0 + gutter, cell.col0 + gutter]).all()
    # Each wall strip is its own colour: ochre is redder than white.
    ochre = image[layout.walls["plaster_ochre"].row0 + 40, :, :3].mean(axis=0)
    white = image[layout.walls["plaster_white"].row0 + 40, :, :3].mean(axis=0)
    assert ochre[0] - ochre[2] > white[0] - white[2] + 40
    # The tones of one colour: the second darker, the third warmer (less blue against red).
    first, darker, warmer = (image[s.row0 + 40, :, :3].astype(float).mean(axis=0) for s in layout.tones["plaster_white"])
    assert darker.sum() < first.sum() - 60
    assert warmer[0] - warmer[2] > first[0] - first[2] + 20
    # Right of the last cell, the fit row repeats the stone colour.
    right = len(layout.cells) * (cell.col1 - cell.col0)
    assert (image[: cell.row1, right:] == image[layout.stone.row0 + gutter, 0]).all()
    assert hashlib.sha256(image.tobytes()).hexdigest() == GOLDEN


GOLDEN = "fc8dca3fc6656aff64dc8b9491916445c45f12995c65c023d2d5ddf16858cbad"
