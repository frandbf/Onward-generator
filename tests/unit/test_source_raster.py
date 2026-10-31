"""Height rasters and the height of a footprint read from them (ADR-018). Synthetic grids only."""

from __future__ import annotations

import numpy as np
import pytest
from shapely import Polygon, box

from onward_generator.sources.plugin import SourceError
from onward_generator.sources.raster import (
    Grid,
    footprint_height,
    mosaic,
    parse_ascii_grid,
    parse_float32,
    snap_bbox,
    tiles,
)

RULE = {"buffer_m": 1.0, "percentile": 50.0, "min_cells": 3, "min_valid_share": 0.5, "min_value": 2.0}


def _flat(value: float, rows: int = 20, columns: int = 20, cell: float = 1.0) -> Grid:
    return Grid(np.full((rows, columns), value, dtype=np.float32), 100.0, 220.0, cell)


def test_parse_ascii_grid_reads_corner_header_and_nodata() -> None:
    data = b"ncols 3\nnrows 2\nxllcorner 440140.0\nyllcorner 4474110.0\ncellsize 2.5\nNODATA_value -9999\n1 2 3\n4 -9999 6\n"
    grid = parse_ascii_grid(data)
    assert grid.bbox == (440140.0, 4474110.0, 440147.5, 4474115.0)
    assert grid.values[0].tolist() == [1, 2, 3]
    assert grid.values[1, 0] == 4 and np.isnan(grid.values[1, 1]) and grid.values[1, 2] == 6
    assert grid.values.dtype == np.float32


def test_parse_ascii_grid_reads_a_centre_header() -> None:
    grid = parse_ascii_grid(b"NCOLS 2\nNROWS 1\nXLLCENTER 1.0\nYLLCENTER 11.0\nCELLSIZE 2\n5 6\n")
    assert grid.bbox == (0.0, 10.0, 4.0, 12.0)
    assert not np.isnan(grid.values).any()


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"ncols 2\nnrows 1\nxllcorner 0\nyllcorner 0\n1 2\n", "ASCII grid header has no cellsize"),
        (b"ncols 2\nnrows 2\nxllcorner 0\nyllcorner 0\ncellsize 1\n1 2 3\n", "ASCII grid has 3 values, header says 2 x 2"),
    ],
)
def test_parse_ascii_grid_refuses_a_broken_grid(data: bytes, message: str) -> None:
    with pytest.raises(SourceError) as caught:
        parse_ascii_grid(data)
    assert str(caught.value) == message


def test_parse_float32_reads_little_endian_rows_from_the_north_and_drops_nodata() -> None:
    values = np.array([[1.5, -9999.0], [3.0, -2.0]], dtype="<f4")
    grid = parse_float32(values.tobytes(), (10.0, 20.0, 11.0, 21.0), 2, 2, nodata_below=-1000.0)
    assert grid.west == 10.0 and grid.north == 21.0 and grid.cell == 0.5
    assert grid.values[0, 0] == 1.5 and np.isnan(grid.values[0, 1])
    assert grid.values[1].tolist() == [3.0, -2.0]
    grid.values[1, 1] = 0.0  # writable copy, not a view of the bytes


def test_parse_float32_refuses_the_wrong_size() -> None:
    with pytest.raises(SourceError) as caught:
        parse_float32(b"\0" * 12, (0, 0, 1, 1), 2, 2, nodata_below=-1)
    assert str(caught.value) == "float32 grid has 12 bytes, expected 2 x 2 x 4"


def test_snap_bbox_grows_outwards_to_the_cell_grid_and_its_offset() -> None:
    assert snap_bbox((10.2, 20.0, 14.9, 22.6), 2.5) == (10.0, 20.0, 15.0, 25.0)
    assert snap_bbox((842516.1, 6519774.0, 842816.0, 6520074.3), 0.5, 0.25) == (842515.75, 6519773.75, 842816.25, 6520074.75)
    assert snap_bbox((0.30000000001, 0.0, 0.7, 0.5), 0.1) == pytest.approx((0.3, 0.0, 0.7, 0.5))  # no extra cell from float noise


def test_tiles_cut_a_box_north_west_first_with_short_last_tiles() -> None:
    assert tiles((0.0, 0.0, 5.0, 3.0), 1.0, 2) == [
        ((0.0, 1.0, 2.0, 3.0), 2, 2), ((2.0, 1.0, 4.0, 3.0), 2, 2), ((4.0, 1.0, 5.0, 3.0), 1, 2),
        ((0.0, 0.0, 2.0, 1.0), 2, 1), ((2.0, 0.0, 4.0, 1.0), 2, 1), ((4.0, 0.0, 5.0, 1.0), 1, 1),
    ]
    assert tiles((0.0, 0.0, 2.0, 2.0), 1.0, 2) == [((0.0, 0.0, 2.0, 2.0), 2, 2)]


def test_mosaic_places_tiles_and_leaves_gaps_empty() -> None:
    a = Grid(np.array([[1.0, 2.0]], dtype=np.float32), 0.0, 2.0, 1.0)
    b = Grid(np.array([[3.0]], dtype=np.float32), 2.0, 1.0, 1.0)
    grid = mosaic([a, b])
    assert grid.bbox == (0.0, 0.0, 3.0, 2.0)
    assert grid.values[0, :2].tolist() == [1.0, 2.0] and np.isnan(grid.values[0, 2])
    assert np.isnan(grid.values[1, :2]).all() and grid.values[1, 2] == 3.0


def test_mosaic_refuses_no_tiles_and_mixed_cells() -> None:
    with pytest.raises(SourceError, match="^no raster tiles$"):
        mosaic([])
    with pytest.raises(SourceError, match="^raster tiles have different cell sizes$"):
        mosaic([_flat(1.0), _flat(1.0, cell=0.5)])


def test_footprint_height_takes_the_median_inside_the_inner_buffer() -> None:
    grid = _flat(12.0)
    grid.values[5:15, 5:15] = 18.0  # a 10 m building at x 105..115, y 205..215
    grid.values[5, 5:15] = 30.0  # a parapet or a tree along its north edge
    result = footprint_height(grid, box(105, 205, 115, 215), **RULE)
    assert result.cells == 64  # 8 x 8 centres inside the 1 m inner buffer
    assert result.valid == 64 and result.valid_share == 1.0
    assert result.buffer_m == 1.0
    assert (result.height_m, result.p25_m, result.p75_m) == (18.0, 18.0, 18.0)


def test_footprint_height_uses_the_requested_percentile() -> None:
    grid = _flat(0.0)
    grid.values[5:15, 5:15] = np.arange(100, dtype=np.float32).reshape(10, 10) / 10 + 3
    result = footprint_height(grid, box(105, 205, 115, 215), **{**RULE, "buffer_m": 0.0, "percentile": 90.0})
    assert result.cells == 100 and result.buffer_m == 0.0
    assert result.height_m == pytest.approx(11.91)
    assert (result.p25_m, result.p75_m) == (5.48, 10.42)


def test_a_narrow_footprint_is_sampled_without_the_buffer() -> None:
    grid = _flat(9.0)
    result = footprint_height(grid, box(105, 205, 107, 215), **RULE)
    assert result.buffer_m == 0.0 and result.cells == 20 and result.height_m == 9.0


def test_cells_below_the_minimum_or_without_data_are_not_valid() -> None:
    grid = _flat(0.0)
    grid.values[5:15, 5:10] = 15.0
    grid.values[5:15, 10:15] = np.nan
    half = footprint_height(grid, box(105, 205, 115, 215), **{**RULE, "buffer_m": 0.0})
    assert (half.cells, half.valid, half.height_m) == (100, 50, 15.0)
    less = footprint_height(grid, box(105, 205, 115, 215), **{**RULE, "buffer_m": 0.0, "min_valid_share": 0.51})
    assert (less.valid, less.height_m, less.p25_m, less.p75_m) == (50, None, None, None)
    low = _flat(1.99)
    assert footprint_height(low, box(105, 205, 115, 215), **RULE).valid == 0
    assert footprint_height(_flat(2.0), box(105, 205, 115, 215), **RULE).valid == 64


def test_too_few_valid_cells_give_no_height() -> None:
    grid = _flat(0.0)
    grid.values[9, 9] = 10.0
    grid.values[9, 10] = 10.0
    tiny = Polygon([(108, 209), (111, 209), (111, 211), (108, 211)])
    result = footprint_height(grid, tiny, **{**RULE, "buffer_m": 0.0, "min_valid_share": 0.0})
    assert (result.cells, result.valid, result.height_m) == (6, 2, None)
    assert footprint_height(grid, tiny, **{**RULE, "buffer_m": 0.0, "min_valid_share": 0.0, "min_cells": 2}).height_m == 10.0


def test_a_footprint_outside_the_grid_has_no_cells() -> None:
    result = footprint_height(_flat(10.0), box(500, 500, 510, 510), **RULE)
    assert (result.cells, result.valid, result.height_m, result.valid_share) == (0, 0, None, 0.0)
    edge = footprint_height(_flat(10.0), box(115, 215, 130, 230), **{**RULE, "buffer_m": 0.0})
    assert edge.cells == 25


def test_a_footprint_the_buffer_erases_falls_back_to_the_outline() -> None:
    result = footprint_height(_flat(10.0), box(105, 205, 106.5, 215), **{**RULE, "min_cells": 1})
    assert result.buffer_m == 0.0 and result.cells == 10


def test_parse_ascii_grid_reads_padded_headers_and_a_header_only_grid() -> None:
    grid = parse_ascii_grid(b"ncols        2\nnrows        1\nxllcorner    440140.0\nyllcorner    4474110.0\ncellsize     2.5\n 0.000 19.500\n")
    assert grid.bbox == (440140.0, 4474110.0, 440145.0, 4474112.5)
    assert grid.values.tolist() == [[0.0, 19.5]]
    empty = parse_ascii_grid(b"ncols 0\nnrows 0\nxllcorner 1\nyllcorner 2\ncellsize 1\n")
    assert empty.values.shape == (0, 0) and empty.bbox == (1.0, 2.0, 1.0, 2.0)


def test_parse_float32_keeps_float32_and_a_value_at_the_nodata_limit() -> None:
    values = np.array([[-1000.0, -1000.5]], dtype="<f4")
    grid = parse_float32(values.tobytes(), (0.0, 0.0, 2.0, 1.0), 2, 1, nodata_below=-1000.0)
    assert grid.values.dtype == np.float32
    assert grid.values[0, 0] == -1000.0 and np.isnan(grid.values[0, 1])


def test_snap_bbox_ignores_float_noise_below_a_millionth_of_a_cell_only() -> None:
    assert snap_bbox((4.9999996, 0.0, 6.0000004, 1.0), 1.0) == (5.0, 0.0, 6.0, 1.0)
    assert snap_bbox((4.999998, 0.0, 6.000002, 1.0), 1.0) == (4.0, 0.0, 7.0, 1.0)


def test_tiles_step_by_the_cell_size() -> None:
    assert tiles((100.0, 200.0, 110.0, 205.0), 2.5, 2) == [
        ((100.0, 200.0, 105.0, 205.0), 2, 2), ((105.0, 200.0, 110.0, 205.0), 2, 2),
    ]
    cut = tiles((100.0, 200.0, 110.0, 210.0), 2.5, 2)
    assert [bbox for bbox, _c, _r in cut] == [
        (100.0, 205.0, 105.0, 210.0), (105.0, 205.0, 110.0, 210.0),
        (100.0, 200.0, 105.0, 205.0), (105.0, 200.0, 110.0, 205.0),
    ]


def test_mosaic_places_tiles_by_cell_size_far_from_the_origin() -> None:
    a = Grid(np.array([[1.0, 2.0]], dtype=np.float32), 1000.0, 5005.0, 2.5)
    b = Grid(np.array([[3.0]], dtype=np.float32), 1005.0, 5002.5, 2.5)
    grid = mosaic([a, b])
    assert grid.values.dtype == np.float32
    assert grid.bbox == (1000.0, 5000.0, 1007.5, 5005.0)
    assert grid.values.shape == (2, 3)
    assert grid.values[0, :2].tolist() == [1.0, 2.0] and grid.values[1, 2] == 3.0
    assert np.isnan(grid.values[0, 2]) and np.isnan(grid.values[1, :2]).all()


def test_a_footprint_on_the_first_column_and_row_reads_them() -> None:
    grid = Grid(np.arange(16, dtype=np.float32).reshape(4, 4) + 10, 1000.0, 5010.0, 2.5)
    result = footprint_height(grid, box(1000.0, 5005.0, 1005.0, 5010.0), **{**RULE, "buffer_m": 0.0})
    assert result.cells == 4 and result.height_m == 12.5  # cells 10, 11, 14, 15


def test_footprint_height_rounds_to_centimetres_and_keeps_the_buffer_at_exactly_min_cells() -> None:
    grid = _flat(12.345)
    result = footprint_height(grid, box(105, 205, 115, 215), **RULE)
    assert result.height_m == 12.35
    exact = footprint_height(_flat(9.0), box(105, 205, 109, 209), **{**RULE, "min_cells": 4})
    assert exact.cells == 4 and exact.buffer_m == 1.0


def test_a_footprint_without_enough_data_still_reports_the_buffer_used() -> None:
    result = footprint_height(_flat(0.0), box(105, 205, 115, 215), **RULE)
    assert (result.height_m, result.buffer_m, result.cells) == (None, 1.0, 64)
