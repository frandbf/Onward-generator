"""Tiny synthetic Overture parquets written at test time (CC0, project-made; no Overture data).

The columns follow the Overture places and buildings schema as far as ``overture
fetch`` and ``overture match`` read them. Every place and footprint sits around
MURCIA_CENTER in the local grid of ``frame()``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import duckdb
import shapely
from shapely import Point, box
from shapely.geometry.base import BaseGeometry

from onward_generator.geo.projection import LocalFrame
from tests.conftest import MURCIA_CENTER

CRS = "EPSG:25830"
PLACE_COLUMNS = (
    'id VARCHAR, names STRUCT("primary" VARCHAR), basic_category VARCHAR, '
    'taxonomy STRUCT("primary" VARCHAR, hierarchy VARCHAR[], alternates VARCHAR[]), confidence DOUBLE, operating_status VARCHAR, '
    "sources STRUCT(property VARCHAR, dataset VARCHAR, license VARCHAR)[], "
    "bbox STRUCT(xmin DOUBLE, xmax DOUBLE, ymin DOUBLE, ymax DOUBLE), geometry GEOMETRY"
)
BUILDING_COLUMNS = (
    "id VARCHAR, height DOUBLE, num_floors INTEGER, facade_color VARCHAR, roof_color VARCHAR, "
    "sources STRUCT(property VARCHAR, dataset VARCHAR, license VARCHAR)[], "
    "bbox STRUCT(xmin DOUBLE, xmax DOUBLE, ymin DOUBLE, ymax DOUBLE), geometry GEOMETRY"
)


def local_connect() -> duckdb.DuckDBPyConnection:
    """DuckDB that never loads or installs an extension: an s3:// source fails at once, offline."""
    return duckdb.connect(config={"autoload_known_extensions": False, "autoinstall_known_extensions": False})


def frame() -> LocalFrame:
    return LocalFrame.create(MURCIA_CENTER[0], MURCIA_CENTER[1], CRS)


def wgs84(local: BaseGeometry) -> BaseGeometry:
    return frame().to_wgs84(local)


def source(dataset: str, license: str | None, prop: str = "") -> dict[str, Any]:
    return {"property": prop, "dataset": dataset, "license": license}


def _bbox(geom: BaseGeometry) -> dict[str, float]:
    xmin, ymin, xmax, ymax = geom.bounds
    return {"xmin": xmin, "xmax": xmax, "ymin": ymin, "ymax": ymax}


def _write(path: Path, columns: str, rows: Sequence[Sequence[Any]], *, geometry_blob: bool = False) -> Path:
    """Write ``rows``. ``geometry_blob`` stores the last column as WKB bytes, the way a release can expose it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if geometry_blob:
        columns = columns.replace("geometry GEOMETRY", "geometry BLOB")
    connection = duckdb.connect()
    try:
        connection.execute(f"CREATE TABLE t ({columns})")
        width = len(rows[0]) if rows else 0
        last = "?" if geometry_blob else "ST_GeomFromWKB(?)"
        marks = ", ".join(["?"] * (width - 1) + [last])
        for row in rows:
            connection.execute(f"INSERT INTO t VALUES ({marks})", list(row))
        connection.execute(f"COPY t TO '{path}' (FORMAT parquet)")
    finally:
        connection.close()
    return path


def place(
    gers: str, x: float, y: float, *, name: str | None = "Horno", basic: str | None = "casual_eatery", primary: str | None = "bakery",
    confidence: float | None = 0.9, status: str | None = "open", sources: Sequence[Mapping[str, Any]] | None = None,
) -> list[Any]:
    """One place row at local ``(x, y)`` metres."""
    point = wgs84(Point(x, y))
    taxonomy = None if primary is None else {"primary": primary, "hierarchy": [primary], "alternates": None}
    return [
        gers, {"primary": name}, basic, taxonomy, confidence, status,
        list(sources if sources is not None else [source("meta", "CDLA-Permissive-2.0")]), _bbox(point), shapely.to_wkb(point),
    ]


def building(
    gers: str, footprint: BaseGeometry, *, height: float | None = 12.0, floors: int | None = 4,
    sources: Sequence[Mapping[str, Any]] | None = None, facade: str | None = None, roof: str | None = None,
) -> list[Any]:
    """One building row whose footprint is ``footprint`` in local metres."""
    geom = wgs84(footprint)
    return [
        gers, height, floors, facade, roof, list(sources if sources is not None else [source("Esri Community Maps", None)]),
        _bbox(geom), shapely.to_wkb(geom),
    ]


def write_places(path: Path, rows: Sequence[Sequence[Any]], *, geometry_blob: bool = False) -> Path:
    return _write(path, PLACE_COLUMNS, rows, geometry_blob=geometry_blob)


def write_buildings(path: Path, rows: Sequence[Sequence[Any]], *, geometry_blob: bool = False) -> Path:
    return _write(path, BUILDING_COLUMNS, rows, geometry_blob=geometry_blob)


def scene(buildings: Mapping[str, BaseGeometry]) -> dict[str, Any]:
    """A minimal geo scene in the local grid of ``frame()`` with these building footprints."""
    f = frame()
    return {
        "schema": "onward_generator.geo_scene",
        "schema_version": "0.2.0",
        "frame": f.to_dict(),
        "features": [
            {"id": bid, "kind": "building", "tags": {"building": "yes"}, "geometry": shapely.geometry.mapping(geom)}
            for bid, geom in sorted(buildings.items())
        ],
    }


BLOCK = {"osm-way-1": box(0, 0, 10, 10), "osm-way-2": box(12, 0, 22, 10), "osm-way-3": box(0, 20, 10, 30)}
"""Three OSM footprints, 10 m squares, local metres."""
