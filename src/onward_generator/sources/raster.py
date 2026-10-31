"""Height rasters in memory, and the height of a building footprint read from one (ADR-018).

Only numpy and shapely: an ESRI ASCII grid and a raw float32 grid (BIL) are
both a header and an array, so no GDAL or rasterio is needed. A grid is
north-up with square cells; row 0 is the northern row.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import shapely
from shapely.geometry.base import BaseGeometry

from onward_generator.sources.plugin import SourceError

BBox = tuple[float, float, float, float]
NAN32 = np.float32(math.nan)
"""The empty cell of a mosaic; its type also makes the mosaic float32."""
HEADER_KEYS = frozenset({"ncols", "nrows", "xllcorner", "yllcorner", "xllcenter", "yllcenter", "cellsize", "nodata_value"})


@dataclass(frozen=True)
class Grid:
    """``values[row, column]``; the cell (0, 0) has its north-west corner at (``west``, ``north``)."""

    values: np.ndarray
    west: float
    north: float
    cell: float

    @property
    def bbox(self) -> BBox:
        rows, columns = self.values.shape
        return (self.west, self.north - rows * self.cell, self.west + columns * self.cell, self.north)


def parse_ascii_grid(data: bytes) -> Grid:
    """An ESRI ASCII grid (``ncols``, ``nrows``, ``xllcorner``/``xllcenter``, ``yllcorner``/``yllcenter``, ``cellsize``, ``NODATA_value``).

    NODATA cells become NaN.
    """
    tokens = data.decode("ascii").split()  # pragma: no mutate: codec names are case-insensitive
    header: dict[str, float] = {}
    index = 0
    while index < len(tokens) and tokens[index].lower() in HEADER_KEYS:
        header[tokens[index].lower()] = float(tokens[index + 1])
        index += 2
    try:
        columns, rows, cell = int(header["ncols"]), int(header["nrows"]), header["cellsize"]
    except KeyError as exc:
        raise SourceError(f"ASCII grid header has no {exc.args[0]}") from exc
    half = cell / 2
    west = header["xllcorner"] if "xllcorner" in header else header["xllcenter"] - half
    south = header["yllcorner"] if "yllcorner" in header else header["yllcenter"] - half
    values = np.array(tokens[index:], dtype=np.float32)
    if values.size != rows * columns:
        raise SourceError(f"ASCII grid has {values.size} values, header says {rows} x {columns}")
    grid = values.reshape(rows, columns)
    if "nodata_value" in header:
        grid[grid == np.float32(header["nodata_value"])] = np.nan  # pragma: no mutate: None also stores NaN in a float array
    return Grid(grid, west, south + rows * cell, cell)


def parse_float32(data: bytes, bbox: BBox, columns: int, rows: int, *, nodata_below: float) -> Grid:
    """A raw little-endian float32 grid of ``rows`` x ``columns`` covering ``bbox`` (WMS ``image/x-bil;bits=32``).

    Values below ``nodata_below`` are no data and become NaN.
    """
    if len(data) != rows * columns * 4:
        raise SourceError(f"float32 grid has {len(data)} bytes, expected {rows} x {columns} x 4")
    grid = np.frombuffer(data, dtype="<f4").reshape(rows, columns).astype(np.float32)
    grid[grid < nodata_below] = np.nan  # pragma: no mutate: None also stores NaN in a float array
    return Grid(grid, bbox[0], bbox[3], (bbox[2] - bbox[0]) / columns)


def snap_bbox(bbox: BBox, cell: float, offset: float = 0.0) -> BBox:
    """``bbox`` grown outwards to cell edges at ``offset + k * cell``."""
    xmin, ymin, xmax, ymax = bbox

    def down(value: float) -> float:
        return math.floor(round((value - offset) / cell, 6)) * cell + offset

    def up(value: float) -> float:
        return math.ceil(round((value - offset) / cell, 6)) * cell + offset

    return (down(xmin), down(ymin), up(xmax), up(ymax))


def tiles(bbox: BBox, cell: float, max_cells: int) -> list[tuple[BBox, int, int]]:
    """``bbox`` (already on the cell grid) cut into tiles of at most ``max_cells`` per side: (bbox, columns, rows), north-west first."""
    xmin, ymin, xmax, ymax = bbox
    columns = round((xmax - xmin) / cell)
    rows = round((ymax - ymin) / cell)
    out: list[tuple[BBox, int, int]] = []
    for top in range(0, rows, max_cells):
        height = min(max_cells, rows - top)
        for left in range(0, columns, max_cells):
            width = min(max_cells, columns - left)
            west, north = xmin + left * cell, ymax - top * cell
            out.append(((west, north - height * cell, west + width * cell, north), width, height))
    return out


def mosaic(grids: Sequence[Grid]) -> Grid:
    """One grid from tiles on the same cell grid. Cells no tile covers are NaN."""
    if not grids:
        raise SourceError("no raster tiles")
    cell = grids[0].cell
    if any(not math.isclose(g.cell, cell) for g in grids):
        raise SourceError("raster tiles have different cell sizes")
    west = min(g.west for g in grids)
    north = max(g.north for g in grids)
    east = max(g.bbox[2] for g in grids)
    south = min(g.bbox[1] for g in grids)
    shape = (round((north - south) / cell), round((east - west) / cell))
    out = np.full(shape, NAN32)
    for g in grids:
        row, column = round((north - g.north) / cell), round((g.west - west) / cell)
        rows, columns = g.values.shape
        out[row:row + rows, column:column + columns] = g.values
    return Grid(out, west, north, cell)


@dataclass(frozen=True)
class FootprintHeight:
    """What the raster says about one footprint. ``height_m`` is None when there is not enough to say."""

    cells: int
    valid: int
    buffer_m: float
    height_m: float | None
    p25_m: float | None
    p75_m: float | None

    @property
    def valid_share(self) -> float:
        return self.valid / self.cells if self.cells else 0.0


def footprint_height(
    grid: Grid,
    footprint: BaseGeometry,
    *,
    buffer_m: float,
    percentile: float,
    min_cells: int,
    min_valid_share: float,
    min_value: float,
) -> FootprintHeight:
    """The ``percentile`` of the cells whose centre lies inside ``footprint`` shrunk by ``buffer_m``.

    The inner buffer keeps eaves, facade edges and trees that overhang the
    outline out of the sample. A footprint too narrow to keep ``min_cells``
    centres after the buffer is sampled without it. A cell is valid when it
    has data and is at least ``min_value``. The height is given only when at
    least ``min_cells`` cells are valid and they are at least
    ``min_valid_share`` of the cells sampled.
    """
    used = buffer_m
    values = _cells(grid, footprint.buffer(-buffer_m))
    if values.size < min_cells:
        used = 0.0
        values = _cells(grid, footprint)
    valid = values[np.isfinite(values) & (values >= min_value)]
    if valid.size < min_cells or valid.size < min_valid_share * values.size:
        return FootprintHeight(values.size, valid.size, used, None, None, None)
    p25, mid, p75 = (float(v) for v in np.percentile(valid, [25.0, percentile, 75.0]))
    return FootprintHeight(values.size, valid.size, used, round(mid, 2), round(p25, 2), round(p75, 2))


def _cells(grid: Grid, area: BaseGeometry) -> np.ndarray:
    """The values of the cells whose centre lies inside ``area``."""
    if area.is_empty:
        return np.empty(0)
    rows, columns = grid.values.shape
    minx, miny, maxx, maxy = area.bounds
    c0 = max(0, math.floor((minx - grid.west) / grid.cell))
    c1 = min(columns, math.ceil((maxx - grid.west) / grid.cell))  # pragma: no mutate: a larger upper bound only widens the window contains_xy filters; the cells are the same
    r0 = max(0, math.floor((grid.north - maxy) / grid.cell))
    r1 = min(rows, math.ceil((grid.north - miny) / grid.cell))  # pragma: no mutate: a larger upper bound only widens the window contains_xy filters; the cells are the same
    cx = grid.west + (np.arange(c0, c1) + 0.5) * grid.cell
    cy = grid.north - (np.arange(r0, r1) + 0.5) * grid.cell
    gx, gy = np.meshgrid(cx, cy)
    return grid.values[r0:r1, c0:c1][shapely.contains_xy(area, gx, gy)]
