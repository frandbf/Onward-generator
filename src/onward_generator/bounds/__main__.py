"""``onward-generator bounds``: copy a scene and add the playable perimeter wall."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from onward_generator.bounds.envelope import (
    DEFAULT_HEIGHT_M,
    DEFAULT_THICKNESS_M,
    BoundsError,
    apply_bounds,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="onward-generator bounds",
        description="Copy a generate or detail directory and add a perimeter wall. Does not rewrite the phase 03 base.",
    )
    parser.add_argument("--base", required=True, help="scene3d or scene_detail directory")
    parser.add_argument("--out", required=True, help="new directory (never overwrites without --overwrite)")
    parser.add_argument("--height-m", type=float, default=DEFAULT_HEIGHT_M)
    parser.add_argument("--thickness-m", type=float, default=DEFAULT_THICKNESS_M)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code in (0, None) else 2
    try:
        report = apply_bounds(
            args.base, args.out,
            height_m=args.height_m, thickness_m=args.thickness_m, overwrite=args.overwrite,
        )
    except (BoundsError, FileExistsError, ValueError) as exc:
        print(f"bounds: {exc}", file=sys.stderr)
        return 1
    wall = report["wall"]
    print(
        f"bounds: wall {wall['id']} h={wall['height_m']} m t={wall['thickness_m']} m "
        f"triangles={wall['triangles']} site={wall['site_area_m2']} m²"
    )
    print(f"wrote {args.out}")
    from onward_generator.next_step import emit, layout_command

    command = layout_command(Path(args.out))
    if command:
        emit(command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
