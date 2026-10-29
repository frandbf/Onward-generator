"""``onward-generator materials apply``."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from onward_generator.dress.catalog import CatalogError
from onward_generator.materials.apply import MaterialsError, apply_materials
from onward_generator.materials.plan import PlanError
from onward_generator.materials.polyhaven import DEFAULT_CACHE, DownloadError
from onward_generator.next_step import after_materials, emit


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="onward-generator materials",
        description="Apply a texture-plan JSON to a copy of a dress/detail scene. Does not export.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    apply_p = sub.add_parser("apply", help="download (opt-in) and remap atlas tiles on a new directory")
    apply_p.add_argument("--base", required=True, help="dress or detail directory (scene_visual.glb)")
    apply_p.add_argument("--out", required=True, help="new output directory")
    apply_p.add_argument("--plan", default=None, help="the map's config/texture_plan.json")
    apply_p.add_argument("--catalog", default=None, help="material catalogue")
    apply_p.add_argument("--scene", default=None, help="geo_scene.json if not recorded in the manifest")
    apply_p.add_argument("--cache", default=str(DEFAULT_CACHE), help="raw texture cache (gitignored)")
    apply_p.add_argument("--download", action="store_true", help="the only network path; fetch missing CC0 1k JPEGs")
    apply_p.add_argument("--overwrite", action="store_true")
    apply_p.add_argument("--seed", type=int, default=48152)
    return parser


def _run_apply(args: argparse.Namespace) -> int:
    report = apply_materials(
        args.base,
        args.out,
        plan_path=args.plan,
        catalog_path=args.catalog,
        scene_path=args.scene,
        cache_dir=args.cache,
        download=args.download,
        overwrite=args.overwrite,
        seed=args.seed,
    )
    applied = report["applied"]
    skipped = report["skipped"]
    print(
        f"materials apply: applied={len(applied)} skipped={len(skipped)} "
        f"brick_nodes={len(report['brick_nodes'])} cobble={report['cobble_applied']}"
    )
    for row in applied:
        print(f"  {row['catalog_id']}: {row['asset_id']} ({row['albedo_mode']})")
    for row in skipped:
        print(f"  skip {row.get('catalog_id') or row.get('id')}: {row['reason']}")
    print(f"wrote {Path(args.out).expanduser()}")
    emit(after_materials(Path(args.out)))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code in (0, None) else 2
    try:
        if args.command == "apply":
            return _run_apply(args)
        return 2
    except (MaterialsError, PlanError, DownloadError, CatalogError, FileExistsError, ValueError) as exc:
        print(f"materials: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
