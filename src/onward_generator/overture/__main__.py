"""``onward-generator overture fetch``."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from onward_generator.maps.workspace import MapError
from onward_generator.overture.acquire import OvertureError, fetch_map


def _fetch(args: argparse.Namespace) -> int:
    for record in fetch_map(args.map, release=args.release, overwrite=args.overwrite, out=args.out):
        licences = ", ".join(f"{c['dataset']} {c['license']} {c['records']}" for c in record.licenses) or "none"
        print(f"overture {record.theme} {record.release}: {record.rows} rows, {record.size_bytes} bytes; licences {licences}")
        print(f"wrote {record.path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="onward-generator overture", description="Overture Maps places and buildings (ADR-021)")
    sub = parser.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser("fetch", help="clip Overture places and buildings to the map bbox (network)")
    fetch.add_argument("--map", required=True, help="map directory containing map.json")
    fetch.add_argument("--release", help="Overture release, YYYY-MM-DD.N (default: the STAC catalogue's latest)")
    fetch.add_argument("--out", help="directory for overture_places.parquet and overture_buildings.parquet (default: the map's data/raw, where overture match reads them)")
    fetch.add_argument("--overwrite", action="store_true", help="replace data/raw/overture_*.parquet")
    fetch.set_defaults(handler=_fetch)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code in (0, None) else 2
    try:
        return int(args.handler(args))
    except (OvertureError, MapError, FileExistsError, ValueError, OSError) as exc:
        print(f"overture: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
