"""``onward-generator export``: build and verify Onward export packages (phase 07.0)."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from onward_generator.export.package import ExportError, build_package, verify_package


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="onward-generator export", description="Phase 07: Onward export package")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("package", help="validate inputs and write a new export package for the Unity adapter")
    p.add_argument("--project", required=True, help="project configuration with export.enabled = true")
    p.add_argument("--detail", required=True, help="phase 04 output directory (scene_detail.json, GLB files)")
    p.add_argument("--layout", required=True, help="perf_layout 0.1.0 or gameplay_layout 0.3.0")
    p.add_argument("--config", default=None, help="adapter configuration (default: config/export/onward.default.json)")
    p.add_argument("--out", required=True, help="new package directory; must not exist")
    v = sub.add_parser("verify", help="re-hash every file of a package")
    v.add_argument("package")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code in (0, None) else 2
    if args.command == "verify":
        problems = verify_package(args.package)
        for p in problems:
            print(f"  ERROR {p}", file=sys.stderr)
        print("package intact" if not problems else f"package damaged: {len(problems)} problem(s)")
        return 0 if not problems else 1
    try:
        result = build_package(args.project, args.detail, args.layout, args.out, export_config=args.config)
    except ExportError as exc:
        print("export package rejected; nothing written:", file=sys.stderr)
        for p in exc.problems:
            print(f"  ERROR {p}", file=sys.stderr)
        return 1
    except FileExistsError as exc:
        print(f"export: {exc}", file=sys.stderr)
        return 1
    for w in result.warnings:
        print(f"  WARNING {w}")
    m = result.manifest
    print(f"package {m['package_id']} written to {result.directory}")
    print(f"  adapter {m['adapter']['name']} {m['adapter']['version']} (device_verified: {m['adapter']['device_verified']})")
    for f in m["files"]:
        print(f"  {f['path']}: {f['bytes']} bytes, sha256 {f['sha256'][:12]}")
    lm = m["lightmap_estimate"]
    print(f"  lightmap estimate: {lm['texels'] / 1e6:.1f} M texels at {lm['texels_per_m']:g} texels/m, "
          f"at least {lm['pages_at_least']} pages of {lm['max_size']} px")
    print(f"next: tools/unity/onward_adapter/run_export.sh {result.directory} tool scene launcher")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
