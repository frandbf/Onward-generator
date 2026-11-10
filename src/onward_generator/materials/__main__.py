"""``onward-generator materials apply``."""

from __future__ import annotations

import argparse
import math
import sys
from collections.abc import Sequence
from pathlib import Path

from onward_generator.dress.catalog import CatalogError
from onward_generator.materials.apply import MaterialsError, apply_materials
from onward_generator.materials.plan import PlanError
from onward_generator.materials.polyhaven import DEFAULT_CACHE, DownloadError
from onward_generator.materials.trim_style import AUTO, FACADE_MODES
from onward_generator.next_step import after_materials, emit


def cell_metres(text: str) -> float:
    """``--cell-m``: a finite number of metres above 0."""
    try:
        value = float(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number of metres") from exc
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError(f"the combine cell must be a finite number of metres above 0, got {text!r}")
    return value


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
    apply_p.add_argument("--no-tile-cache", action="store_true", help="bake every photo tile again instead of reading the tile cache")
    apply_p.add_argument("--seed", type=int, default=48152)
    apply_p.add_argument(
        "--facades", choices=FACADE_MODES, default="trim",
        help="trim: one regional trim-sheet atlas for the facades (ADR-020); legacy: one material per facade part",  # pragma: no mutate: help text only
    )
    apply_p.add_argument(
        "--facade-style", default=AUTO,
        help="a style of config/facade_styles.json, or auto (from the map centre)",  # pragma: no mutate: help text only
    )
    apply_p.add_argument(
        "--repetition", action="store_true",
        help="measure roof and paving tiles and phase the ground UVs per combine cell (plan C1)",  # pragma: no mutate: help text only
    )
    apply_p.add_argument(
        "--cell-m", type=cell_metres, default=100.0,
        help="combine cell in metres for --repetition (ADR-005)",  # pragma: no mutate: help text only
    )
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
        tile_cache=not args.no_tile_cache,
        facades=args.facades,
        facade_style=args.facade_style,
        repetition=args.repetition,
        cell_m=args.cell_m,
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
    facades = report.get("facades")
    if facades:
        detail = f"materials={','.join(facades['materials'])}" if facades["mode"] == "trim" else f"reason={facades['reason']}"
        print(f"facades: {facades['mode']} style={facades['style']} ({facades['style_origin']}) {detail}")
    repetition = report.get("repetition")
    if repetition:
        over = ", ".join(repetition["over_limit"]) or "none"
        print(f"repetition: cell {repetition['cell_m']:g} m, slots={len(repetition['slots'])} over limit: {over}")
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
