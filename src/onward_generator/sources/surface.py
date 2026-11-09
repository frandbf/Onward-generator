"""Float32 GeoTIFF replies and surface-minus-terrain height grids (ADR-018 amendment, plan D4).

``parse_tiff_float32`` reads one WCS reply through Pillow, already a dependency,
so no GDAL, rasterio or tifffile is needed. ``height_above_ground`` turns a
surface model (DSM) and a terrain model (DTM) on the same cell grid into a
height-above-ground grid that ``lidar.measure`` reads like any nDSM. Where the
terrain has no data (under buildings, in AHN) ``fill_ground`` fills it from
the ground around, but only up to ``fill_m``: a cell left empty becomes no data,
never a guessed height. docs/04-heights.md#lidar-heights.
"""

from __future__ import annotations

import io
import math
from collections.abc import Sequence

import numpy as np
from PIL import Image

from onward_generator.sources.plugin import SourceError
from onward_generator.sources.raster import BBox, Grid

TIFF_MAGIC = (b"II*\x00", b"MM\x00*")
PIXEL_SCALE_TAG = 33550
"""GeoTIFF ModelPixelScaleTag: (x size, y size, z size) of a cell."""
TIEPOINT_TAG = 33922
"""GeoTIFF ModelTiepointTag: (i, j, k, x, y, z); cell (0, 0) has its north-west corner at (x, y)."""
GEOREF_TOLERANCE_M = 0.001
SWAP_TOLERANCE_M = 0.5
SWAP_PERCENTILE = 10.0
"""A DSM holds every point but water and a DTM only the ground, so the DSM is not below the DTM on shared cells."""


def parse_tiff_float32(data: bytes, bbox: BBox, columns: int, rows: int, *, nodata: float | None) -> Grid:
    """A single-band float32 TIFF of ``columns`` x ``rows`` cells covering ``bbox``. Cells equal to ``nodata`` become NaN.

    A body that is not a TIFF (an XML exception report), a TIFF of another sample type or size, and a
    GeoTIFF whose tie point or cell size is not the box asked for are refused.
    """
    if data[:4] not in TIFF_MAGIC:
        raise SourceError(f"reply is not a TIFF: {data[:120]!r}")
    try:
        with Image.open(io.BytesIO(data)) as image:
            mode, size = image.mode, image.size
            scale, tie = image.tag_v2.get(PIXEL_SCALE_TAG), image.tag_v2.get(TIEPOINT_TAG)
            values = np.array(image, dtype=np.float32) if mode == "F" else None
    except (OSError, ValueError, SyntaxError) as exc:  # how Pillow reports a damaged or unsupported TIFF
        raise SourceError(f"TIFF reply cannot be decoded: {exc}") from exc
    if values is None:
        raise SourceError(f"TIFF reply has mode {mode}, not float32")
    if size != (columns, rows):
        raise SourceError(f"TIFF reply is {size[0]} x {size[1]} cells, asked for {columns} x {rows}")
    cell = (bbox[2] - bbox[0]) / columns
    check_georeference(scale, tie, bbox, cell)
    if nodata is not None:
        values[values == np.float32(nodata)] = np.nan  # pragma: no mutate: None also stores NaN in a float array
    return Grid(values, bbox[0], bbox[3], cell)


def check_georeference(scale: Sequence[float] | None, tie: Sequence[float] | None, bbox: BBox, cell: float) -> None:
    """Refuse a GeoTIFF whose cell size or north-west corner differs from the request. Missing tags are not checked."""
    if scale is not None and not (math.isclose(scale[0], cell) and math.isclose(scale[1], cell)):
        raise SourceError(f"TIFF reply has {scale[0]:g} x {scale[1]:g} m cells, asked for {cell:g} m")
    if tie is not None and not (
        math.isclose(tie[3], bbox[0], abs_tol=GEOREF_TOLERANCE_M) and math.isclose(tie[4], bbox[3], abs_tol=GEOREF_TOLERANCE_M)
    ):
        raise SourceError(f"TIFF reply starts at {tie[3]:g}, {tie[4]:g}, asked for {bbox[0]:g}, {bbox[3]:g}")


def fill_steps(fill_m: float, cell: float) -> int:
    """How many one-cell steps ``fill_ground`` takes: valid ground within ``fill_m`` along rows and columns fills a cell."""
    return math.floor(fill_m / cell + 1e-9)


def fill_ground(dtm: Grid, fill_m: float) -> Grid:
    """``dtm`` with its empty cells filled from the ground around, at most ``fill_m`` away along rows and columns.

    Each step gives every empty cell with a valid row or column neighbour the mean of those
    neighbours, all at once, so the result does not depend on any order. Cells still empty
    after ``fill_steps`` steps stay NaN.
    """
    values = dtm.values.astype(np.float32, copy=True)
    for _step in range(fill_steps(fill_m, dtm.cell)):
        padded = np.pad(values, 1, constant_values=np.nan)
        total = np.zeros(values.shape, dtype=np.float32)
        count = np.zeros(values.shape, dtype=np.int8)
        for near in (padded[:-2, 1:-1], padded[2:, 1:-1], padded[1:-1, :-2], padded[1:-1, 2:]):
            valid = np.isfinite(near)
            total += np.where(valid, near, np.float32(0.0))
            count += valid
        take = np.isnan(values) & (count > 0)
        if not take.any():
            break  # pragma: no mutate: only stops early; a later step would find nothing to fill either
        values[take] = total[take] / count[take]
    return Grid(values, dtm.west, dtm.north, dtm.cell)


def height_above_ground(dsm: Grid, dtm: Grid, fill_m: float) -> Grid:
    """The surface minus the filled terrain, cell by cell. Both grids must share their cells and extent."""
    same = (
        dsm.values.shape == dtm.values.shape
        and math.isclose(dsm.cell, dtm.cell)
        and math.isclose(dsm.west, dtm.west, abs_tol=GEOREF_TOLERANCE_M)
        and math.isclose(dsm.north, dtm.north, abs_tol=GEOREF_TOLERANCE_M)
    )
    if not same:
        raise SourceError(f"surface and terrain grids differ: {dsm.bbox} at {dsm.cell:g} m against {dtm.bbox} at {dtm.cell:g} m")
    both = np.isfinite(dsm.values) & np.isfinite(dtm.values)
    if both.any():
        low = float(np.percentile(dsm.values[both] - dtm.values[both], SWAP_PERCENTILE))
        if low < -SWAP_TOLERANCE_M:
            raise SourceError(f"the surface lies {-low:.2f} m below the terrain on {SWAP_PERCENTILE:g} % of the shared cells: are the layers swapped?")
    ground = fill_ground(dtm, fill_m)
    return Grid(dsm.values - ground.values, dsm.west, dsm.north, dsm.cell)
