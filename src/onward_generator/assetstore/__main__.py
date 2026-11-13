"""``onward-generator asset-store scan | import | textures`` (ADR-035).

``scan`` lists the ``.unitypackage`` files in this user's Unity Asset Store cache and whether each one already has a
local catalogue. ``import`` exports the chosen packages with the host's Unity editor and writes
``assets/asset_store_<id>/``, which git ignores. Run it on the host, not in Docker: it needs the editor and the cache. ``textures`` reads the texture sets that
``config/dress/asset_store_textures.json`` names straight from their packages (no editor) into ``assets/asset_store_<id>/``.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from onward_generator.assetstore.importer import (
    DEFAULT_MAX_MB,
    DEFAULT_MAX_TRIANGLES,
    REPO_ROOT,
    PackageResult,
    import_packages,
)
from onward_generator.assetstore.packages import Package, find_packages
from onward_generator.platform import PlatformError, resolve_host

ASSETS = REPO_ROOT / "assets"


def _cache(args: argparse.Namespace) -> Path:
    if args.cache:
        return Path(args.cache).expanduser()
    return resolve_host().asset_store_cache


def _scan(args: argparse.Namespace) -> int:
    packages = find_packages(_cache(args))
    if not packages:
        print(f"asset-store: no .unitypackage under {_cache(args)}. Download packs in Unity: Window > Package Manager > My Assets > Download.")
        return 0
    for package in packages:
        state = "imported" if (ASSETS / package.catalogue / "manifest.json").is_file() else "not imported"
        large = "  (over --max-mb)" if package.size_mb > args.max_mb else ""
        print(f"{package.id:48s} {package.size_mb:8.1f} MB  {state:12s}  {package.publisher} / {package.name}{large}")
    print(f"{len(packages)} packages")
    return 0


def _select(packages: list[Package], args: argparse.Namespace) -> list[Package]:
    chosen = [p for p in packages if not args.only or p.id in args.only]
    missing = sorted(set(args.only or []) - {p.id for p in packages})
    if missing:
        raise SystemExit(f"asset-store: no downloaded package with id {', '.join(missing)} (see: asset-store scan)")
    if not args.only:
        dropped = [p for p in chosen if p.size_mb > args.max_mb]
        for p in dropped:
            print(f"skip {p.id}: {p.size_mb:.0f} MB is over --max-mb {args.max_mb:g} (pass --only {p.id} to import it anyway)")
        chosen = [p for p in chosen if p.size_mb <= args.max_mb]
        if not args.refresh:
            chosen = [p for p in chosen if not (ASSETS / p.catalogue / "manifest.json").is_file()]
    return chosen


def _print(result: PackageResult) -> None:
    skipped = ", ".join(f"{k} {v}" for k, v in sorted(result.skipped.items()))
    tail = f"  skipped: {skipped}" if skipped else ""
    error = f"  {result.error}" if result.error else ""
    print(f"{result.package.id}: {result.status}, {result.rows} rows{tail}{error}", flush=True)


def _import(args: argparse.Namespace) -> int:
    try:
        host = resolve_host()
    except PlatformError as exc:
        print(f"asset-store: {exc}", file=sys.stderr)
        return 2
    unity = Path(args.unity).expanduser() if args.unity else host.unity_editor
    if not unity.exists():
        print(f"asset-store: the Unity editor is not at {unity} (pass --unity)", file=sys.stderr)
        return 2
    chosen = _select(find_packages(_cache(args)), args)
    if not chosen:
        print("asset-store: nothing to import")
        return 0
    results = import_packages(chosen, unity, ASSETS, max_triangles=args.max_triangles, report=_print)
    rows = sum(r.rows for r in results)
    failed = [r.package.id for r in results if r.status == "failed"]
    print(f"{rows} rows in {sum(1 for r in results if r.rows)} local catalogues under {ASSETS}/asset_store_*" + (f"; failed: {', '.join(failed)}" if failed else ""))
    return 1 if failed and not rows else 0


def _textures(args: argparse.Namespace) -> int:
    from onward_generator.assetstore.textures import read_table, texture_rows

    table = read_table()
    packages = {p.id: p for p in find_packages(_cache(args))}
    wanted = args.only or sorted(table)
    missing = sorted(set(wanted) - set(table))
    if missing:
        raise SystemExit(f"asset-store: {', '.join(missing)} has no texture sets in config/dress/asset_store_textures.json")
    total = 0
    for package_id in wanted:
        if package_id not in packages:
            print(f"{package_id}: not downloaded")
            continue
        rows = texture_rows(packages[package_id], ASSETS, table[package_id])
        normals = sum(1 for row in rows if "normal" in row)
        print(f"{package_id}: {len(rows)} textures of {len(table[package_id])} named, {normals} with a normal map", flush=True)
        total += len(rows)
    print(f"{total} textures under {ASSETS}/asset_store_*")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="onward-generator asset-store", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("scan", "import"):
        command = sub.add_parser(name)
        command.add_argument("--cache", help="the Unity Asset Store cache (default: the host profile's asset_store_cache)")
        command.add_argument("--max-mb", type=float, default=DEFAULT_MAX_MB, help="skip packages larger than this unless named by --only")
    imp = sub.choices["import"]
    imp.add_argument("--only", nargs="+", help="package ids from scan")
    imp.add_argument("--refresh", action="store_true", help="import again packages that already have a local catalogue")
    imp.add_argument("--max-triangles", type=int, default=DEFAULT_MAX_TRIANGLES, help="skip models with more LOD0 triangles")
    imp.add_argument("--unity", help="the Unity editor executable (default: the host profile's)")
    tex = sub.add_parser("textures", help="texture sets named in config/dress/asset_store_textures.json, without the editor")
    tex.add_argument("--cache", help="the Unity Asset Store cache (default: the host profile's asset_store_cache)")
    tex.add_argument("--only", nargs="+", help="package ids of the table")
    args = parser.parse_args(argv)
    if args.command == "textures":
        return _textures(args)
    return _scan(args) if args.command == "scan" else _import(args)


if __name__ == "__main__":
    sys.exit(main())
