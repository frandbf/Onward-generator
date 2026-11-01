"""``onward-generator overture fetch|match``."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from onward_generator.contracts import is_schema
from onward_generator.geo.sources import sha256_file
from onward_generator.io import atomic_write_json, prepare_outputs
from onward_generator.maps.workspace import MapError
from onward_generator.overture.acquire import OvertureError, fetch_map, load_record, theme_target
from onward_generator.overture.match import (
    OvertureConfigError,
    load_config,
    match_buildings,
    match_report,
    overrides_document,
    places_document,
    read_places,
    scene_frame,
)
from onward_generator.sources.registry import load_source_config

OVERRIDES_FILE = "heights_overrides.overture.json"
PLACES_FILE = "overture_places.json"
REPORT_FILE = "overture_match_report.json"


def _fetch(args: argparse.Namespace) -> int:
    for record in fetch_map(args.map, release=args.release, overwrite=args.overwrite, out=args.out):
        licences = ", ".join(f"{c['dataset']} {c['license']} {c['records']}" for c in record.licenses) or "none"
        files = f"; files {len(record.files)} of {record.files_in_theme}" if record.files_in_theme is not None else ""
        print(f"overture {record.theme} {record.release}: {record.rows} rows, {record.size_bytes} bytes; licences {licences}{files}")
        print(f"wrote {record.path}")
    return 0


def _match(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    scene_path = Path(args.scene).expanduser().resolve()
    if not scene_path.is_file():
        raise OvertureError(f"scene not found: {scene_path}")
    before = sha256_file(scene_path)
    scene = json.loads(scene_path.read_bytes())
    if not is_schema(scene.get("schema"), "onward_generator.geo_scene"):
        raise OvertureError("input is not a onward_generator.geo_scene")
    places_path, buildings_path = theme_target(args.map, "places"), theme_target(args.map, "buildings")
    places_record, buildings_record = load_record(places_path), load_record(buildings_path)
    frame = scene_frame(scene)
    allowed = load_source_config().licence_allowlist
    places, place_skips = read_places(places_path, frame, cfg, allowed)
    matches, building_skips = match_buildings(buildings_path, scene, cfg, allowed)
    if sha256_file(scene_path) != before:
        raise OvertureError("scene file changed while being read")
    places_doc = places_document(places, places_record, place_skips, frame)
    scene_ref = {"path": str(scene_path), "sha256": before, "schema_version": scene.get("schema_version")}
    osm_buildings = sum(1 for f in scene["features"] if f.get("kind") == "building")
    report = match_report(
        scene_ref=scene_ref, cfg=cfg, places=places_doc, matches=matches, building_skips=building_skips,
        buildings_record=buildings_record, osm_buildings=osm_buildings,
    )
    out_overrides, out_places, out_report = prepare_outputs(
        args.out, (OVERRIDES_FILE, PLACES_FILE, REPORT_FILE), overwrite=args.overwrite,
        protected=(scene_path, places_path, buildings_path),
    )
    atomic_write_json(out_overrides, overrides_document(matches, buildings_record, cfg))
    atomic_write_json(out_places, places_doc)
    atomic_write_json(out_report, report)
    counts = report["buildings"]["counts"]
    print(f"places={len(places)} of {places_record.rows} buildings matched={counts['matched']} of {osm_buildings} "
          f"height={counts['with_height']} levels={counts['with_levels']}")
    for path in (out_overrides, out_places, out_report):
        print(f"wrote {path}")
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
    match = sub.add_parser("match", help="Overture heights as overrides and places as POIs for a geo scene (offline)")
    match.add_argument("--scene", required=True, help="geo_scene.json (after cadastre split when Catastro is on)")
    match.add_argument("--map", required=True, help="map directory holding data/raw/overture_*.parquet")
    match.add_argument("--out", required=True, help="output directory")
    match.add_argument("--config", help="Overture configuration (default: config/overture.default.json)")
    match.add_argument("--overwrite", action="store_true")
    match.set_defaults(handler=_match)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code in (0, None) else 2
    try:
        return int(args.handler(args))
    except (OvertureError, OvertureConfigError, MapError, FileExistsError, ValueError, OSError) as exc:
        print(f"overture: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
