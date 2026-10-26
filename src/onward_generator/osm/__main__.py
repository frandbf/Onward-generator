"""``onward-generator osm download`` — retry the fetch ``map create`` already runs."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from onward_generator.maps.workspace import MapError
from onward_generator.next_step import emit, geo_command
from onward_generator.osm.acquire import OsmDownloadError, download_map_osm


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="onward-generator osm", description="Fetch the OSM XML for one map folder")
    sub = parser.add_subparsers(dest="command", required=True)
    download = sub.add_parser("download", help="one Overpass request from the map centre, radius and margin")
    download.add_argument("--map", required=True, help="map directory containing map.json")
    download.add_argument("--overwrite", action="store_true", help="replace data/raw/<id>.osm")
    args = parser.parse_args(argv)
    try:
        record = download_map_osm(args.map, overwrite=args.overwrite)
    except (OsmDownloadError, MapError, FileExistsError, OSError) as exc:
        print(f"osm: {exc}", file=sys.stderr)
        return 1
    print(f"osm {record.size_bytes} bytes, sha256 {record.sha256}")
    print(f"bbox {record.bbox} ({record.bbox_order}); licence {record.license}")
    print(f"wrote {record.path}")
    emit(geo_command(args.map, retrieved_at=record.retrieved_at))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
