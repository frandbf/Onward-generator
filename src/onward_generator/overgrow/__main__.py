"""``onward-generator overgrow place``."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from onward_generator.config.loader import ConfigLoadError
from onward_generator.next_step import bounds_command, emit, fit_command
from onward_generator.overgrow.constants import SEED
from onward_generator.overgrow.fetch import FetchError, fetch_library
from onward_generator.overgrow.fit import FitError, check_scene, write_report
from onward_generator.overgrow.library_catalog import (
    REVIEW_FILE,
    LibraryError,
    choose_ids,
    installed_rows,
    review_rows,
    write_selection,
)
from onward_generator.overgrow.place import OvergrowError, overgrow
from onward_generator.overgrow.sketchfab_fetch import SketchfabError, fetch_sketchfab
from onward_generator.overgrow.zone import ZoneError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="onward-generator overgrow",
        description="Add abandoned-city grass, trees and wrecks to a scene copy. Does not export.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    place = sub.add_parser("place", help="write a new directory; the drive lane stays clear")
    place.add_argument("--base", required=True, help="signs, materials, dress or detail directory")
    place.add_argument("--scene", required=True, help="geo_scene.json in local metres")
    place.add_argument("--out", required=True, help="new output directory")
    place.add_argument("--profile", type=int, choices=(10, 30, 100), default=100, help="abandonment years; density only")
    place.add_argument("--seed", type=int, default=SEED)
    place.add_argument("--zone-radius", type=float, default=None, help="disc radius in local metres; default is one 110 m block")
    place.add_argument("--zone-polygon", default=None, help="GeoJSON polygon in local metres")
    place.add_argument("--library", default=None, help="one catalogue directory, or a parent of catalogues; missing means procedural only")
    place.add_argument("--selection", default=None, help="prop_selection.json; only those installed ids are placed")
    place.add_argument("--layout", default=None, help="layout.json whose spawn points stay clear")
    place.add_argument("--overwrite", action="store_true")
    fit = sub.add_parser("fit", help="reject props whose size or footprint does not fit the street")
    fit.add_argument("--base", required=True, help="overgrow output directory")
    fit.add_argument("--scene", required=True, help="geo_scene.json in local metres")
    fit.add_argument("--out", required=True, help="fit.json written only when every checked object passes")
    fit.add_argument("--overwrite", action="store_true")
    library = sub.add_parser("library", help="list the prop library or write a selection for place")
    library_sub = library.add_subparsers(dest="library_command", required=True)
    listing = library_sub.add_parser("list", help="installed meshes and the Sketchfab licence review")
    listing.add_argument("--library", default="assets", help="one catalogue, or a parent whose children each have manifest.json")
    select = library_sub.add_parser("select", help="write a prop_selection.json of installed ids")
    select.add_argument("--library", default="assets", help="one catalogue, or a parent whose children each have manifest.json")
    select.add_argument("--out", required=True)
    select.add_argument("--installed", action="store_true", help="every normalized mesh already on disk")
    select.add_argument("--id", action="append", default=[], help="installed asset id; repeat for several")
    select.add_argument("--overwrite", action="store_true")
    fetch = sub.add_parser("fetch", help="download the named CC0 props and write normalized GLBs")
    fetch.add_argument("--raw", required=True, help="gitignored directory for the original downloads")
    fetch.add_argument("--library", required=True, help="normalized GLB directory")
    fetch.add_argument("--inventory", required=True, help="inventory JSON to write")
    sketchfab = sub.add_parser("fetch-sketchfab", help="download CC-BY Sketchfab models already marked usable")
    sketchfab.add_argument("--raw", required=True, help="gitignored directory for the original downloads")
    sketchfab.add_argument("--library", default="assets/normalized")
    sketchfab.add_argument("--review", default=None)
    return parser


def _library(args: argparse.Namespace) -> int:
    if args.library_command == "list":
        rows = installed_rows(args.library)
        print(f"installed {len(rows)}  {args.library}")
        for row in rows:
            print(f"  {row['id']}\t{row.get('category', '')}\t{row.get('role', '')}\t{row.get('file', '')}")
        reviewed = review_rows()
        usable = [row for row in reviewed if row.get("usable") is True]
        blocked = [row for row in reviewed if row.get("usable") is not True]
        print(f"sketchfab licence ok, not downloaded {len(usable)}  {REVIEW_FILE}")
        for row in usable:
            print(f"  {row.get('name')}\t{row.get('license')}\t{row.get('author')}")
        print(f"sketchfab not usable {len(blocked)}")
        for row in blocked:
            print(f"  {row.get('name')}\t{row.get('license') or 'none'}\t{row.get('reason')}")
        return 0
    out = Path(args.out)
    if args.overwrite and out.exists():
        out.unlink()
    ids = choose_ids(args.library, args.id, all_installed=args.installed)
    write_selection(out, ids)
    print(f"selection {len(ids)} ids -> {out}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code in (0, None) else 2
    try:
        if args.command == "fetch-sketchfab":
            document = fetch_sketchfab(args.raw, args.library, args.review)
            for line in document["results"]:
                print(line)
            return 0
        if args.command == "fetch":
            document = fetch_library(args.raw, args.library, args.inventory)
            ready = sum(1 for row in document["assets"] if row.get("import_status") == "normalized")
            print(f"overgrow fetch: normalized={ready}/{len(document['assets'])} inventory={args.inventory}")
            return 0
        if args.command == "library":
            return _library(args)
        if args.command == "fit":
            return _fit(args)
        report = overgrow(
            args.base, args.scene, args.out,
            overwrite=args.overwrite, profile=args.profile, seed=args.seed,
            zone_radius_m=args.zone_radius, zone_polygon=args.zone_polygon,
            library=args.library, layout=args.layout, selection=args.selection,
        )
    except (OvergrowError, FitError, FetchError, SketchfabError, ZoneError, LibraryError, ConfigLoadError, FileExistsError, OSError, ValueError) as exc:
        print(f"overgrow: {exc}", file=sys.stderr)
        return 1
    print(
        f"overgrow: grass={report['grass']} vines={report['vines']} "
        f"trees={report['trees']} ({report['trees_observed']} observed) wrecks={report['wrecks']} "
        f"props={report['props']} tris={report['added_triangles']}"
    )
    print(f"wrote {Path(args.out).expanduser()}")
    emit(fit_command(Path(args.scene), Path(args.out)))
    return 0


def _fit(args: argparse.Namespace) -> int:
    out = Path(args.out).expanduser()
    if out.exists() and not args.overwrite:
        raise FileExistsError(f"{out} already exists")
    checked, problems = check_scene(args.base, args.scene)
    if problems:
        if out.exists():
            out.unlink()
        for line in problems:
            print(f"fit: {line}", file=sys.stderr)
        print(f"fit: rejected={len(problems)}", file=sys.stderr)
        return 1
    write_report(out, checked)
    print(f"fit: checked={checked}")
    emit(bounds_command(Path(args.base)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
