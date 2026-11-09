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
    casement_pixels,
    cornice_pixels,
    door_pixels,
    grain,
    grille_pixels,
    linear_to_srgb8,
    luma_rgb,
    paint_window,
    sample_periodic,
    sash_pixels,
    shop_glass_pixels,
    srgb_to_linear,
    stone_pixels,
    style_seed,
    wall_pixels,
    window_pixels,
    zocalo_pixels,
)
from onward_generator.materials.trim_style import load_regional_table, load_style_table, trim_layout

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


GOLDEN = "e9af2f31b06d43236b1031c12fb6a1da86468d54536e32d8fe106e964ce0bee5"


# window_pixels of both mediterranean families at closed 0, 0.25, 0.4 and 0.7 (176 x 368), rounded to 9
# decimals, before plan D1 added the window kinds: the shutter cell must not change.
SHUTTER_WINDOWS = "0e2bceb43e3c90d50a4d0274ce6b66f9ff355c72bce37999217a3eacbaff2870"


def _windows_digest(paint: Any, families: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for family in families:
        for closed in (0.0, 0.25, 0.4, 0.7):
            digest.update(np.round(paint(176, 368, family, closed), 9).tobytes())
    return digest.hexdigest()


def test_the_shutter_window_keeps_its_bytes() -> None:
    families = list((load_style_table().styles["mediterranean"].atlas or {})["window_families"])
    assert all("kind" not in family for family in families)
    assert _windows_digest(window_pixels, families) == SHUTTER_WINDOWS
    assert _windows_digest(paint_window, families) == SHUTTER_WINDOWS
    assert _windows_digest(paint_window, [{**f, "kind": "shutter"} for f in families]) == SHUTTER_WINDOWS


FRAME = [0.9, 0.9, 0.9]
BLIND = [0.2, 0.6, 0.2]
IRON = [0.05, 0.05, 0.06]


def _at(pixels: np.ndarray, x: float, y: float) -> np.ndarray:
    """The pixel at cell coordinates x (0-1 left to right), y (0-1 bottom to top)."""
    h, w = pixels.shape[:2]
    return pixels[int((1.0 - y) * h), int(x * w)]


def test_a_casement_has_a_transom_and_no_shutter_box() -> None:
    family = {"kind": "casement", "frame": FRAME, "shutter": BLIND, "closed": [0.0]}
    open_ = casement_pixels(200, 400, family, 0.0)
    assert open_.shape == (400, 200, 3)
    # No shutter box at the top: glass (blue-ish) under the top frame, not the shutter colour.
    assert _at(open_, 0.25, 0.92).argmax() == 2
    assert _at(open_, 0.25, 0.74).tolist() == FRAME  # the transom
    assert _at(open_, 0.47, 0.4).tolist() == FRAME  # a meeting stile
    assert _at(open_, 0.47, 0.85).argmax() == 2  # the fanlight has no stile
    assert _at(open_, 0.5, 0.4).tolist() == pytest.approx([0.45, 0.45, 0.45])  # the line between the leaves
    assert _at(open_, 0.03, 0.5).tolist() == FRAME
    # The blind drops from the top over ``closed`` of the glass.
    half = casement_pixels(200, 400, family, 0.5)
    assert _at(half, 0.25, 0.6).argmax() == 1 and _at(half, 0.25, 0.3).argmax() == 2
    assert np.array_equal(casement_pixels(200, 400, family, 0.5), half)


def test_a_sash_has_a_meeting_rail_and_glazing_bars() -> None:
    family = {"kind": "sash", "frame": FRAME, "shutter": BLIND, "closed": [0.0]}
    sash = sash_pixels(300, 400, family, 0.0)
    assert _at(sash, 0.2, 0.5).tolist() == FRAME  # the meeting rail
    assert _at(sash, 1 / 3, 0.2).tolist() == FRAME and _at(sash, 2 / 3, 0.8).tolist() == FRAME
    assert _at(sash, 0.2, 0.725).tolist() == FRAME and _at(sash, 0.2, 0.275).tolist() == FRAME
    assert _at(sash, 0.2, 0.62).argmax() == 2 and _at(sash, 0.5, 0.62).argmax() == 2
    # The shadow of the upper sash's rail on the lower one, inside the frame only.
    assert _at(sash, 0.2, 0.4725)[2] < _at(sash, 0.2, 0.44)[2]
    assert _at(sash, 0.03, 0.4725).tolist() == FRAME
    blind = sash_pixels(300, 400, family, 0.5)
    assert _at(blind, 0.2, 0.62).argmax() == 1 and _at(blind, 0.2, 0.2).argmax() == 2


def test_a_grille_has_iron_bars_over_glass_and_leaves_behind() -> None:
    family = {"kind": "grille", "frame": [0.3, 0.2, 0.1], "shutter": BLIND, "iron": IRON, "closed": [0.0]}
    grille = grille_pixels(352, 400, family, 0.0)
    pitch = 0.88 / 7
    for k in range(1, 7):
        assert _at(grille, 0.06 + k * pitch, 0.5).tolist() == IRON
    # Glass between two bars, and the rails at 0.12 and 0.88.
    assert _at(grille, 0.06 + 1.5 * pitch, 0.5).argmax() == 2
    assert _at(grille, 0.06 + 1.5 * pitch, 0.12).tolist() == IRON
    assert _at(grille, 0.06 + 1.5 * pitch, 0.88).tolist() == IRON
    assert _at(grille, 0.03, 0.5).tolist() == [0.3, 0.2, 0.1]
    closed = grille_pixels(352, 400, family, 0.7)
    assert _at(closed, 0.06 + 1.5 * pitch, 0.5).argmax() == 1  # a timber leaf
    assert _at(closed, 0.06 + 1.5 * pitch, 0.5).tolist() != _at(grille, 0.06 + 1.5 * pitch, 0.5).tolist()
    assert _at(closed, 0.06 + 6 * pitch, 0.5).tolist() == IRON  # the bars stay in front


def test_paint_window_dispatches_on_the_kind() -> None:
    base = {"frame": FRAME, "shutter": BLIND, "iron": IRON, "closed": [0.0]}
    painted = {kind: paint_window(96, 192, {**base, "kind": kind}, 0.3) for kind in ("shutter", "casement", "sash", "grille")}
    assert np.array_equal(painted["shutter"], window_pixels(96, 192, base, 0.3))
    assert np.array_equal(painted["casement"], casement_pixels(96, 192, base, 0.3))
    assert np.array_equal(painted["sash"], sash_pixels(96, 192, base, 0.3))
    assert np.array_equal(painted["grille"], grille_pixels(96, 192, base, 0.3))
    kinds = list(painted)
    for i, a in enumerate(kinds):
        for b in kinds[i + 1 :]:
            assert not np.array_equal(painted[a], painted[b]), (a, b)


def small_regional(name: str) -> dict[str, Any]:
    """A regional recipe on a 1024 px atlas."""
    atlas = copy.deepcopy(dict(load_regional_table(load_style_table()).atlases[name]))
    atlas.update(size_px=1024, height_px=2048, fit_row_px=192, fit_cell_px=96, cornice_px=64, zocalo_px=64, stone_px=32)
    atlas["grime"] = dict(atlas["grime"], streaks_per_strip=12)
    return atlas


def test_regional_bakes_are_deterministic_and_differ_by_style() -> None:
    images = {}
    for name in ("central_european", "anglo", "latin_american"):
        atlas = small_regional(name)
        layout = trim_layout(atlas)
        image = bake_trim_atlas(name, atlas, layout, TINTS, sources())
        assert np.array_equal(image, bake_trim_atlas(name, atlas, layout, TINTS, sources()))
        assert (image[..., 3] == 255).all()
        images[name] = image
    assert not np.array_equal(images["central_european"], images["anglo"])
    assert not np.array_equal(images["anglo"], images["latin_american"])
    # The latin_american grille cell holds the iron colour; the casement cells of central_european do not.
    atlas = small_regional("latin_american")
    cell = trim_layout(atlas).cells["window_0_0"]
    iron = linear_to_srgb8(np.asarray(atlas["window_families"][0]["iron"]))
    block = images["latin_american"][cell.row0 : cell.row1, cell.col0 : cell.col1, :3]
    assert (block == iron).all(axis=-1).any()
