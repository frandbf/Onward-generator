"""Float32 TIFF replies and the surface-minus-terrain grid (ADR-018 amendment, plan D4)."""

from __future__ import annotations

import io
import math

import numpy as np
import pytest
from PIL import Image, TiffImagePlugin, TiffTags

from onward_generator.sources import surface
from onward_generator.sources.plugin import SourceError
from onward_generator.sources.raster import Grid

SENTINEL = 3.4028234663852886e38
BBOX = (100.0, 200.0, 101.5, 201.0)  # 3 x 2 cells of 0.5 m


def tiff(values: np.ndarray, *, georef: tuple[float, float, float] | None = None, compression: str | None = None) -> bytes:
    """A float32 TIFF of ``values``; ``georef`` = (west, north, cell) writes the GeoTIFF tie point and pixel scale."""
    image = Image.fromarray(np.asarray(values, dtype=np.float32))
    info = TiffImagePlugin.ImageFileDirectory_v2()
    if georef is not None:
        west, north, cell = georef
        info[surface.PIXEL_SCALE_TAG] = (cell, cell, 0.0)
        info.tagtype[surface.PIXEL_SCALE_TAG] = TiffTags.DOUBLE
        info[surface.TIEPOINT_TAG] = (0.0, 0.0, 0.0, west, north, 0.0)
        info.tagtype[surface.TIEPOINT_TAG] = TiffTags.DOUBLE
    out = io.BytesIO()
    image.save(out, format="TIFF", tiffinfo=info, **({"compression": compression} if compression else {}))
    return out.getvalue()


VALUES = np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]], dtype=np.float32)


def test_parse_reads_values_north_up_with_the_box_and_cell() -> None:
    grid = surface.parse_tiff_float32(tiff(VALUES, georef=(100.0, 201.0, 0.5)), BBOX, 3, 2, nodata=SENTINEL)
    assert grid.values.dtype == np.float32
    assert grid.values.tolist() == VALUES.tolist()
    assert (grid.west, grid.north, grid.cell) == (100.0, 201.0, 0.5)
    assert grid.bbox == BBOX


def test_parse_reads_a_deflate_compressed_tiff() -> None:
    grid = surface.parse_tiff_float32(tiff(VALUES, compression="tiff_adobe_deflate"), BBOX, 3, 2, nodata=None)
    assert grid.values.tolist() == VALUES.tolist()


def test_parse_maps_only_the_sentinel_to_nan() -> None:
    below = float(np.nextafter(np.float32(SENTINEL), np.float32(0)))
    values = np.array([[SENTINEL, below, 2.0], [0.0, -1.0, SENTINEL]], dtype=np.float32)
    grid = surface.parse_tiff_float32(tiff(values), BBOX, 3, 2, nodata=SENTINEL)
    assert np.isnan(grid.values[0, 0]) and np.isnan(grid.values[1, 2])
    assert grid.values[0, 1] == np.float32(below)
    assert grid.values[1].tolist()[:2] == [0.0, -1.0]
    kept = surface.parse_tiff_float32(tiff(values), BBOX, 3, 2, nodata=None)
    assert kept.values[0, 0] == np.float32(SENTINEL)


def test_parse_refuses_an_exception_report() -> None:
    body = b'<?xml version="1.0"?><ows:ExceptionReport><ows:Exception exceptionCode="NoSuchCoverage"/></ows:ExceptionReport>'
    with pytest.raises(SourceError) as caught:
        surface.parse_tiff_float32(body, BBOX, 3, 2, nodata=None)
    assert str(caught.value) == f"reply is not a TIFF: {body[:120]!r}"


def test_parse_refuses_a_png() -> None:
    out = io.BytesIO()
    Image.new("L", (3, 2)).save(out, format="PNG")
    with pytest.raises(SourceError, match="^reply is not a TIFF: "):
        surface.parse_tiff_float32(out.getvalue(), BBOX, 3, 2, nodata=None)


def test_parse_refuses_an_eight_bit_tiff() -> None:
    out = io.BytesIO()
    Image.new("L", (3, 2)).save(out, format="TIFF")
    with pytest.raises(SourceError) as caught:
        surface.parse_tiff_float32(out.getvalue(), BBOX, 3, 2, nodata=None)
    assert str(caught.value) == "TIFF reply has mode L, not float32"


def test_parse_refuses_a_broken_tiff() -> None:
    with pytest.raises(SourceError, match="^TIFF reply cannot be decoded: "):
        surface.parse_tiff_float32(b"II*\x00" + b"\x00" * 20, BBOX, 3, 2, nodata=None)


@pytest.mark.parametrize(("columns", "rows"), [(2, 2), (3, 3)])
def test_parse_refuses_a_wrong_size(columns: int, rows: int) -> None:
    with pytest.raises(SourceError) as caught:
        surface.parse_tiff_float32(tiff(VALUES), (100.0, 200.0, 100.0 + 0.5 * columns, 200.0 + 0.5 * rows), columns, rows, nodata=None)
    assert str(caught.value) == f"TIFF reply is 3 x 2 cells, asked for {columns} x {rows}"


@pytest.mark.parametrize(
    ("georef", "message"),
    [
        ((100.0, 201.0, 1.0), "TIFF reply has 1 x 1 m cells, asked for 0.5 m"),
        ((100.25, 201.0, 0.5), "TIFF reply starts at 100.25, 201, asked for 100, 201"),
        ((100.0, 201.25, 0.5), "TIFF reply starts at 100, 201.25, asked for 100, 201"),
    ],
)
def test_parse_refuses_another_georeference(georef: tuple[float, float, float], message: str) -> None:
    with pytest.raises(SourceError) as caught:
        surface.parse_tiff_float32(tiff(VALUES, georef=georef), BBOX, 3, 2, nodata=None)
    assert str(caught.value) == message


def test_georeference_checks_each_axis_and_tolerates_a_millimetre() -> None:
    surface.check_georeference((0.5, 0.5, 0.0), (0, 0, 0, 100.0009, 200.9991, 0), BBOX, 0.5)
    surface.check_georeference(None, None, BBOX, 0.5)
    with pytest.raises(SourceError, match="cells"):
        surface.check_georeference((0.5, 0.25, 0.0), None, BBOX, 0.5)
    with pytest.raises(SourceError, match="starts at"):
        surface.check_georeference(None, (0, 0, 0, 100.0011, 201.0, 0), BBOX, 0.5)
    with pytest.raises(SourceError, match="starts at"):
        surface.check_georeference(None, (0, 0, 0, 100.0, 201.0011, 0), BBOX, 0.5)


# --- the ground fill ----------------------------------------------------------------------------


def test_fill_steps_counts_whole_cells_within_the_distance() -> None:
    assert surface.fill_steps(20.0, 0.5) == 40
    assert surface.fill_steps(1.0, 0.5) == 2
    assert surface.fill_steps(0.3, 0.1) == 3  # 0.3 / 0.1 is 2.9999999999999996 in floats
    assert surface.fill_steps(0.4, 0.5) == 0


def test_fill_restores_a_flat_terrain_exactly_and_meets_in_the_middle_of_a_slope() -> None:
    flat = np.full((8, 8), 2.0, dtype=np.float32)
    flat[2:6, 2:6] = np.nan
    filled = surface.fill_ground(Grid(flat, 0.0, 8.0, 1.0), 2.0)
    assert filled.values.tolist() == np.full((8, 8), 2.0).tolist()
    assert (filled.west, filled.north, filled.cell) == (0.0, 8.0, 1.0)
    assert np.isnan(flat[3, 3])  # the input is not changed
    row = np.array([[0.0, np.nan, np.nan, np.nan, 4.0]], dtype=np.float32)
    assert surface.fill_ground(Grid(row, 0.0, 1.0, 1.0), 2.0).values.tolist() == [[0.0, 0.0, 2.0, 4.0, 4.0]]


def test_fill_reaches_exactly_fill_m_along_rows_and_columns() -> None:
    values = np.full((1, 7), np.nan, dtype=np.float32)
    values[0, 0] = 5.0
    one = surface.fill_ground(Grid(values, 0.0, 1.0, 1.0), 3.0).values[0]
    assert one[:4].tolist() == [5.0, 5.0, 5.0, 5.0]
    assert np.isnan(one[4:]).all()
    assert np.isnan(surface.fill_ground(Grid(values, 0.0, 1.0, 1.0), 2.999).values[0, 3])


def test_fill_leaves_the_centre_of_a_wide_hole_empty() -> None:
    values = np.zeros((9, 9), dtype=np.float32)
    values[1:8, 1:8] = np.nan
    filled = surface.fill_ground(Grid(values, 0.0, 9.0, 1.0), 2.0).values
    assert np.isnan(filled[4, 4]) and np.isnan(filled[3, 4])
    assert filled[2, 4] == 0.0 and filled[1, 1] == 0.0


def test_fill_takes_the_mean_of_the_valid_neighbours_at_each_step() -> None:
    values = np.array([[2.0, np.nan, 6.0], [np.nan, np.nan, np.nan]], dtype=np.float32)
    filled = surface.fill_ground(Grid(values, 0.0, 2.0, 1.0), 1.0).values
    assert filled[0].tolist() == [2.0, 4.0, 6.0]
    assert filled[1, 0] == 2.0 and filled[1, 2] == 6.0
    assert np.isnan(filled[1, 1])  # its neighbours were all empty at the step
    two = surface.fill_ground(Grid(values, 0.0, 2.0, 1.0), 2.0).values
    assert two[1, 1] == pytest.approx((4.0 + 2.0 + 6.0) / 3)


def test_fill_without_steps_and_without_holes_returns_the_terrain() -> None:
    values = np.array([[1.0, np.nan]], dtype=np.float32)
    assert np.isnan(surface.fill_ground(Grid(values, 0.0, 1.0, 1.0), 0.5).values[0, 1])
    full = np.ones((2, 2), dtype=np.float32)
    assert surface.fill_ground(Grid(full, 0.0, 2.0, 1.0), 10.0).values.tolist() == full.tolist()


# --- height above ground ------------------------------------------------------------------------


def _pair() -> tuple[Grid, Grid]:
    dsm = np.full((6, 6), 2.0, dtype=np.float32)
    dsm[2:4, 2:4] = 14.0
    dtm = np.full((6, 6), 2.0, dtype=np.float32)
    dtm[2:4, 2:4] = np.nan
    return Grid(dsm, 0.0, 3.0, 0.5), Grid(dtm, 0.0, 3.0, 0.5)


def test_height_above_ground_is_the_surface_minus_the_filled_terrain() -> None:
    dsm, dtm = _pair()
    out = surface.height_above_ground(dsm, dtm, 1.0)
    assert out.values[2:4, 2:4].tolist() == [[12.0, 12.0], [12.0, 12.0]]
    assert out.values[0, 0] == 0.0
    assert (out.west, out.north, out.cell) == (0.0, 3.0, 0.5)


def test_a_building_wider_than_the_fill_keeps_no_data() -> None:
    dsm, dtm = _pair()
    out = surface.height_above_ground(dsm, dtm, 0.4)
    assert np.isnan(out.values[2:4, 2:4]).all()


def test_a_surface_cell_without_data_stays_empty() -> None:
    dsm, dtm = _pair()
    dsm.values[0, 0] = np.nan
    assert np.isnan(surface.height_above_ground(dsm, dtm, 1.0).values[0, 0])


def test_swapped_layers_are_refused() -> None:
    dsm = np.full((4, 4), 2.0, dtype=np.float32)
    dsm[:, :2] = 9.0  # trees over half the shared cells
    terrain = Grid(np.full((4, 4), 2.0, dtype=np.float32), 0.0, 2.0, 0.5)
    surface.height_above_ground(Grid(dsm, 0.0, 2.0, 0.5), terrain, 1.0)
    with pytest.raises(SourceError) as caught:
        surface.height_above_ground(terrain, Grid(dsm, 0.0, 2.0, 0.5), 1.0)
    assert str(caught.value) == "the surface lies 7.00 m below the terrain on 10 % of the shared cells: are the layers swapped?"


def test_the_swap_check_allows_half_a_metre_and_looks_at_the_tenth_percentile() -> None:
    terrain = Grid(np.full((10, 10), 2.0, dtype=np.float32), 0.0, 5.0, 0.5)
    noisy = np.full((10, 10), 2.0, dtype=np.float32)
    noisy[0, :9] = 1.0  # 9 % of the cells 1 m below
    surface.height_above_ground(Grid(noisy, 0.0, 5.0, 0.5), terrain, 1.0)
    noisy[1, 0:2] = 1.0  # 11 %
    with pytest.raises(SourceError) as caught:
        surface.height_above_ground(Grid(noisy, 0.0, 5.0, 0.5), terrain, 1.0)
    assert str(caught.value).startswith("the surface lies 1.00 m below the terrain on 10 % ")
    shallow = np.full((10, 10), 1.5, dtype=np.float32)  # 0.5 m below everywhere
    surface.height_above_ground(Grid(shallow, 0.0, 5.0, 0.5), terrain, 1.0)
    deeper = np.full((10, 10), 1.49, dtype=np.float32)
    with pytest.raises(SourceError, match="swapped"):
        surface.height_above_ground(Grid(deeper, 0.0, 5.0, 0.5), terrain, 1.0)
    empty = Grid(np.full((10, 10), np.nan, dtype=np.float32), 0.0, 5.0, 0.5)
    assert np.isnan(surface.height_above_ground(empty, terrain, 1.0).values).all()


@pytest.mark.parametrize(
    "other",
    [
        Grid(np.zeros((6, 5), dtype=np.float32), 0.0, 3.0, 0.5),
        Grid(np.zeros((6, 6), dtype=np.float32), 0.0, 3.0, 1.0),
        Grid(np.zeros((6, 6), dtype=np.float32), 0.5, 3.0, 0.5),
        Grid(np.zeros((6, 6), dtype=np.float32), 0.0, 3.5, 0.5),
    ],
)
def test_grids_of_another_cell_or_extent_are_refused(other: Grid) -> None:
    dsm, _dtm = _pair()
    with pytest.raises(SourceError, match="^surface and terrain grids differ: "):
        surface.height_above_ground(dsm, other, 1.0)


def test_grids_within_a_millimetre_are_the_same_extent() -> None:
    dsm, dtm = _pair()
    shifted = Grid(dtm.values, 0.0009, 3.0009, 0.5)
    assert surface.height_above_ground(dsm, shifted, 1.0).values[2, 2] == 12.0


def test_two_runs_are_byte_identical() -> None:
    rng = np.random.default_rng(7)
    dtm = rng.uniform(0.0, 3.0, (40, 40)).astype(np.float32)
    dtm[rng.uniform(size=(40, 40)) < 0.4] = np.nan
    dsm = (np.nan_to_num(dtm, nan=1.5) + rng.uniform(0.0, 20.0, (40, 40))).astype(np.float32)
    a = surface.height_above_ground(Grid(dsm, 0.0, 20.0, 0.5), Grid(dtm, 0.0, 20.0, 0.5), 3.0)
    b = surface.height_above_ground(Grid(dsm, 0.0, 20.0, 0.5), Grid(dtm, 0.0, 20.0, 0.5), 3.0)
    assert a.values.tobytes() == b.values.tobytes()
    assert not math.isnan(float(np.nanmax(a.values)))
