"""``onward-generator export``: build and verify Onward export packages (phase 07.0)."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from onward_generator.export.package import ExportError, build_package, verify_package
from onward_generator.export.views import ViewError, write_views


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="onward-generator export", description="Phase 07: Onward export package")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("package", help="validate inputs and write a new export package for the Unity adapter")
    p.add_argument("--project", required=True, help="project configuration with export.enabled = true")
    p.add_argument("--detail", required=True, help="phase 04 output directory (scene_detail.json, GLB files)")
    p.add_argument("--layout", required=True, help="perf_layout 0.1.0 or gameplay_layout 0.3.0")
    p.add_argument("--config", default=None, help="adapter configuration (default: config/export/onward.default.json)")
    p.add_argument("--out", required=True, help="new package directory; must not exist")
    p.add_argument("--atmosphere", default=None, help="day maps only: preset of config/atmosphere.default.json; neutral or absent changes nothing (ADR-037)")  # pragma: no mutate: help text
    v = sub.add_parser("verify", help="re-hash every file of a package")
    v.add_argument("package")
    w = sub.add_parser("views", help="write build/views/views.json, the fixed eye-level cameras the adapter's views step renders (plan E1)")
    w.add_argument("package")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code in (0, None) else 2
    if args.command == "views":
        try:
            path = write_views(args.package)
        except ViewError as exc:
            print(f"export views: {exc}", file=sys.stderr)
            return 1
        print(f"views written to {path}")
        print(f"next: tools/unity/onward_adapter/run_export.sh {path.parents[2]} views")
        return 0
    if args.command == "verify":
        problems = verify_package(args.package)
        for p in problems:
            print(f"  ERROR {p}", file=sys.stderr)
        print("package intact" if not problems else f"package damaged: {len(problems)} problem(s)")
        return 0 if not problems else 1
    try:
        result = build_package(args.project, args.detail, args.layout, args.out, export_config=args.config,
                               atmosphere=args.atmosphere)
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
    budget = ""
    if "lightmap_pages_max" in lm:
        budget = (f", lowered from {lm['texels_per_m_requested']:g} t/m by build.lightmap_pages_max {lm['lightmap_pages_max']}"
                  if lm["texels_per_m"] != lm["texels_per_m_requested"]
                  else f", within build.lightmap_pages_max {lm['lightmap_pages_max']}")
    print(f"  lightmap estimate: {lm['texels']:,} texels at {lm['texels_per_m']:g} texels/m, "
          f"at least {lm['pages_at_least']} pages of {lm['max_size']} px{budget}")
    lit = m.get("lit_normal")
    if lit is not None:
        print(f"  lit normal (plan D5 spike, ADR-033 proposed): {lit['material']} on PP_Opaque_Lit, {lit['path']} "
              f"{lit['width']} x {lit['height']} px at strength {lit['strength']:g}, about {lit['astc6x6_bytes']:,} bytes in ASTC 6x6")
    normals = m.get("normal_maps")
    if normals is not None:
        print(f"  normal maps (plan T3, ADR-039): {len(normals['materials'])} materials on PP_Opaque_Lit, {len(normals['files'])} maps, "
              f"about {normals['astc6x6_bytes']:,} bytes in ASTC 6x6 of {normals['limit_bytes']:,}")
    atmosphere = m.get("atmosphere")
    if atmosphere is not None:
        sun = atmosphere["sun"]
        applied = ", ".join(atmosphere["applied"])
        print(f"  atmosphere {atmosphere['preset']} (ADR-037): applies {applied}; "
              f"sun at {sun['elevation_deg']:g} deg, azimuth {sun['azimuth_deg']:g} deg ({sun['azimuth_source']})")
    print(f"next: tools/unity/onward_adapter/run_export.sh {result.directory} tool scene launcher")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
