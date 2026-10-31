"""``onward-generator cadastre download|match|split``."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from onward_generator.cadastre.acquire import (
    BUILDINGS_TYPE,
    DOWNLOAD_MARGIN_M,
    CadastreError,
    DownloadRecord,
    buildings_path,
    download_building_parts,
    load_download_record,
)
from onward_generator.cadastre.gml import BuildingPart, PartIssue, parse_building_parts, parse_building_uses, reproject_parts
from onward_generator.cadastre.match import (
    DEFAULT_MIN_COVERAGE,
    MatchOptions,
    match_buildings,
    match_report,
    overrides_document,
)
from onward_generator.cadastre.split import (
    SplitOptions,
    attach_uses,
    split_overrides,
    split_scene,
    split_summary,
    subdivided_scene,
)
from onward_generator.cadastre.zones import scene_request
from onward_generator.contracts import is_schema
from onward_generator.geo.sources import sha256_file
from onward_generator.io import atomic_write_json, prepare_outputs

OVERRIDES_FILE = "heights_overrides.catastro.json"
REPORT_FILE = "cadastre_match_report.json"
SCENE_FILE = "geo_scene.json"


def _load_scene(path: str) -> tuple[dict, dict]:
    scene_path = Path(path).expanduser().resolve()
    if not scene_path.is_file():
        raise CadastreError(f"scene not found: {scene_path}")
    scene = json.loads(scene_path.read_text(encoding="utf-8"))
    if not is_schema(scene.get("schema"), "onward_generator.geo_scene"):
        raise CadastreError("input is not a onward_generator.geo_scene")
    return scene, {"path": str(scene_path), "sha256": sha256_file(scene_path), "schema_version": scene.get("schema_version")}


def _parts(gml_path: Path, scene: dict) -> tuple[DownloadRecord, list[BuildingPart], list[PartIssue]]:
    """The download record and its parts, moved from the download CRS to the scene CRS."""
    record = load_download_record(gml_path)
    parts, issues = parse_building_parts(gml_path, record.crs)
    return record, reproject_parts(parts, record.crs, scene["frame"]["crs"]), issues


def _download(args: argparse.Namespace) -> int:
    scene, _ = _load_scene(args.scene)
    bbox, zone = scene_request(scene, args.margin_m)
    uses_out = buildings_path(Path(args.out))
    if uses_out.exists() and not args.overwrite:
        raise FileExistsError(f"refusing to overwrite {uses_out}")
    record = download_building_parts(bbox, args.out, crs=zone.crs, overwrite=args.overwrite)
    print(f"downloaded {record.size_bytes} bytes in {len(record.tiles)} tile(s), sha256 {record.sha256}")
    print(f"bbox {record.bbox} ({record.crs}, {zone.territory}); licence {record.license}")
    print(f"wrote {args.out} and its .source.json sidecar")
    uses = download_building_parts(bbox, uses_out, crs=zone.crs, overwrite=args.overwrite, typename=BUILDINGS_TYPE)
    print(f"downloaded {uses.size_bytes} bytes of buildings (current use), sha256 {uses.sha256}")
    print(f"wrote {uses_out} and its .source.json sidecar")
    return 0


def _uses(path: str | None, scene: dict, features: list[dict], parts: list[BuildingPart], options: SplitOptions) -> dict:
    """Attach the Catastro use of each building from ``--buildings``; the ``subdivision.uses`` block.

    A missing file (a map downloaded before buildings were) leaves every building without a use and says so.
    """
    if path is None:
        return {"status": "not_requested"}
    gml_path = Path(path).expanduser().resolve()
    if not gml_path.is_file():
        print(f"no Catastro buildings at {gml_path}; current uses not read (download again to get them)")
        return {"status": "missing", "path": str(gml_path)}
    record = load_download_record(gml_path)
    uses, issues = parse_building_uses(gml_path)
    if sha256_file(gml_path) != record.sha256:
        raise CadastreError("cadastre buildings file changed while being read")
    counts = attach_uses(scene, features, parts, uses, record, options)
    print(f"buildings={len(uses)} uses attached={sum(counts.values())} {counts}")
    return {
        "status": "read", "path": str(gml_path), "provider": record.provider, "license": record.license,
        "license_url": record.license_url, "attribution": record.attribution, "retrieved_at": record.retrieved_at,
        "sha256": record.sha256, "url": record.url, "buildings": len(uses), "issues": [i.to_dict() for i in issues],
        "attached": counts,
    }


def _match(args: argparse.Namespace) -> int:
    scene, scene_ref = _load_scene(args.scene)
    gml_path = Path(args.gml).expanduser().resolve()
    record, parts, part_issues = _parts(gml_path, scene)
    options = MatchOptions(min_coverage=args.min_coverage)
    matches = match_buildings(scene, parts, options)
    if sha256_file(gml_path) != record.sha256:
        raise CadastreError("cadastre file changed while being read")
    overrides = overrides_document(matches, record)
    report = match_report(
        matches, record=record, gml={"path": str(gml_path)}, scene_ref=scene_ref,
        part_count=len(parts), part_issues=part_issues, options=options,
    )
    out_overrides, out_report = prepare_outputs(
        args.out, (OVERRIDES_FILE, REPORT_FILE), overwrite=args.overwrite, protected=(gml_path, Path(scene_ref["path"]))
    )
    atomic_write_json(out_overrides, overrides)
    atomic_write_json(out_report, report)
    c = report["counts"]
    print(
        f"parts={c['cadastre_parts']} buildings={c['osm_buildings']} matched={c['status_matched']} "
        f"low_coverage={c['status_low_coverage']} no_parts={c['status_no_parts']} mixed={c['mixed_floors']}"
    )
    print(f"wrote {out_overrides}")
    print(f"wrote {out_report}")
    return 0


def _split(args: argparse.Namespace) -> int:
    from onward_generator.heights import load_height_config
    from onward_generator.heights.classify import classify

    scene, scene_ref = _load_scene(args.scene)
    gml_path = Path(args.gml).expanduser().resolve()
    record, parts, part_issues = _parts(gml_path, scene)
    height_config = load_height_config()
    match_options = MatchOptions(min_coverage=args.min_coverage)
    options = SplitOptions(
        min_coverage=args.split_min_coverage,
        min_piece_area_m2=args.min_piece_area_m2,
        min_piece_width_m=args.min_piece_width_m,
    )
    features, results = split_scene(
        scene, parts, record, options, is_landmark=lambda tags: classify(tags, height_config).landmark,
    )
    uses = _uses(args.buildings, scene, features, parts, options)
    split_ids = {r.parent_id for r in results if r.subs}
    matches = match_buildings(scene, parts, match_options)
    if sha256_file(gml_path) != record.sha256:
        raise CadastreError("cadastre file changed while being read")
    overrides = overrides_document([m for m in matches if m.building_id not in split_ids], record)
    overrides["overrides"] = sorted([*overrides["overrides"], *split_overrides(results, record, options)], key=lambda e: e["id"])
    report = match_report(
        matches, record=record, gml={"path": str(gml_path)}, scene_ref=scene_ref,
        part_count=len(parts), part_issues=part_issues, options=match_options,
        split={"options": options.to_dict(), "counts": split_summary(results), "buildings": [r.to_dict() for r in results]},
    )
    out_scene, out_overrides, out_report = prepare_outputs(
        args.out, (SCENE_FILE, OVERRIDES_FILE, REPORT_FILE), overwrite=args.overwrite,
        protected=(gml_path, Path(scene_ref["path"])),
    )
    atomic_write_json(
        out_scene, subdivided_scene(scene, features, results, scene_ref=scene_ref, record=record, options=options, uses=uses),
    )
    atomic_write_json(out_overrides, overrides)
    atomic_write_json(out_report, report)
    c = report["split"]["counts"]
    levels = c["levels_range"]
    span = f"{levels[0]}-{levels[1]}" if levels else "none"
    print(
        f"parts={len(parts)} buildings={c['osm_buildings']} split={c['osm_buildings_split']} "
        f"sub_buildings={c['sub_buildings']} levels={span} quarantined={c['status_quarantined']}"
    )
    print(f"wrote {out_scene}")
    print(f"wrote {out_overrides}")
    print(f"wrote {out_report}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="onward-generator cadastre", description="Catastro INSPIRE building floors")
    sub = parser.add_subparsers(dest="command", required=True)
    dl = sub.add_parser("download", help="download BuildingPart and Building features around a geo scene (network)")
    dl.add_argument("--scene", required=True)
    dl.add_argument("--out", required=True, help="target .gml under data/raw/")
    dl.add_argument("--margin-m", type=float, default=DOWNLOAD_MARGIN_M)
    dl.add_argument("--overwrite", action="store_true")
    dl.set_defaults(handler=_download)
    mt = sub.add_parser("match", help="match a downloaded GML to the scene buildings (offline)")
    mt.add_argument("--scene", required=True)
    mt.add_argument("--gml", required=True)
    mt.add_argument("--out", required=True)
    mt.add_argument("--min-coverage", type=float, default=DEFAULT_MIN_COVERAGE)
    mt.add_argument("--overwrite", action="store_true")
    mt.set_defaults(handler=_match)
    sp = sub.add_parser("split", help="split OSM footprints by Catastro parts; write a new geo scene and overrides (offline)")
    sp.add_argument("--scene", required=True)
    sp.add_argument("--gml", required=True)
    sp.add_argument("--out", required=True)
    sp.add_argument("--buildings", help="the bu:Building GML that `download` writes beside the parts (current use)")
    sp.add_argument("--min-coverage", type=float, default=DEFAULT_MIN_COVERAGE)
    defaults = SplitOptions()
    sp.add_argument("--split-min-coverage", type=float, default=defaults.min_coverage)
    sp.add_argument("--min-piece-area-m2", type=float, default=defaults.min_piece_area_m2)
    sp.add_argument("--min-piece-width-m", type=float, default=defaults.min_piece_width_m)
    sp.add_argument("--overwrite", action="store_true")
    sp.set_defaults(handler=_split)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code in (0, None) else 2
    try:
        return args.handler(args)
    except (CadastreError, FileExistsError, ValueError, OSError) as exc:
        print(f"cadastre: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
