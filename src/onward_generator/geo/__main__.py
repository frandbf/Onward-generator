"""Run the geographic pipeline on a local OSM file.

Usage::

    .venv/bin/python -m onward_generator.geo \\
        --source maps/<id>/data/raw/<id>.osm --aoi maps/<id>/reference/aoi.geojson --out maps/<id>/data/processed/geo
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

from onward_generator.geo.aoi import AoiError, load_aoi_geojson
from onward_generator.geo.clipping import ClipError
from onward_generator.geo.osm_import import OsmImportError
from onward_generator.geo.pipeline import GeoOptions, run_geo_pipeline, write_outputs
from onward_generator.geo.projection import DEFAULT_CRS, ProjectionError
from onward_generator.geo.sources import SourceError, register_local_source
from onward_generator.next_step import emit, heights_command


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m onward_generator.geo", description=__doc__.splitlines()[0])
    parser.add_argument("--source", required=True, help="local OSM XML file (.osm); read-only")
    parser.add_argument("--aoi", required=True, help="GeoJSON Polygon in WGS84 lon/lat")
    parser.add_argument("--out", required=True, help="output directory for geo_scene.json and geo_report.json")
    parser.add_argument("--margin-m", type=float, help="inclusion margin in metres (overrides the AOI file)")
    parser.add_argument("--origin", type=float, nargs=2, metavar=("LON", "LAT"), help="local origin; default AOI centroid")
    parser.add_argument("--crs", default=DEFAULT_CRS, help="projected metric CRS (default EPSG:25830; map show prints the one for that map)")
    parser.add_argument("--retrieved-at", type=datetime.fromisoformat, help="download date, ISO 8601 with timezone")
    parser.add_argument("--repair-invalid", action="store_true", help="repair invalid geometries instead of rejecting them")
    parser.add_argument("--overwrite", action="store_true", help="allow replacing existing outputs")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        aoi = load_aoi_geojson(args.aoi, margin_m=args.margin_m, crs=args.crs)
        source = register_local_source(args.source, retrieved_at=args.retrieved_at)
        result = run_geo_pipeline(
            source,
            aoi,
            origin=tuple(args.origin) if args.origin else None,
            options=GeoOptions(repair_invalid=args.repair_invalid),
        )
        scene, report = write_outputs(result, args.out, overwrite=args.overwrite)
    except (AoiError, SourceError, OsmImportError, ProjectionError, ClipError, FileExistsError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    counts = result.report.counts
    print(f"source:   {source.path} (sha256 {source.sha256[:12]}…, {source.license})")
    print(f"frame:    {result.frame.crs}, origin {result.frame.origin_lon:.7f}, {result.frame.origin_lat:.7f}")
    print(f"kept:     {counts['kept']}  dropped: {counts.get('dropped_outside', 0)}  rejected: {counts.get('rejected_invalid', 0)}")
    print(f"issues:   {counts['errors']} errors, {counts['warnings']} warnings")
    print(f"written:  {scene}\n          {report}")
    emit(heights_command(scene))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
