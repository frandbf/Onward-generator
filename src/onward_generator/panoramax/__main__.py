"""``onward-generator panoramax apply``. Under development.

With the map flag off, the command writes nothing and exits successfully.
A failed download does not create the output directory.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from onward_generator.maps.workspace import (
    DEFAULT_COMBINE_CELL_M,
    MapError,
    combine_cell_m,
    panoramax_atlas_enabled,
    panoramax_enabled,
)
from onward_generator.next_step import emit, map_root, signs_command
from onward_generator.panoramax.apply import apply_pictures
from onward_generator.panoramax.cache import PanoramaxError, PanoramaxTransport, UrllibTransport, cache_pictures


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="onward-generator panoramax",
        description="Under development. Project 360 street photos onto exposed facades. Off unless the map asks for it.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    apply_p = sub.add_parser("apply", help="write a new directory, or do nothing when the map flag is off")
    apply_p.add_argument("--base", required=True, help="materials directory (scene_visual.glb)")
    apply_p.add_argument("--out", required=True, help="new output directory")
    apply_p.add_argument("--overwrite", action="store_true")
    return parser


def run_apply(
    base: str | Path,
    out: str | Path,
    *,
    overwrite: bool = False,
    client: PanoramaxTransport | None = None,
) -> int:
    """Cache pictures and write the scene. A failed download does not create ``out``."""
    root = map_root(Path(base))
    if root is None or not panoramax_enabled(root):
        print("panoramax off")
        return 0
    output = Path(out).expanduser()
    try:
        atlas = panoramax_atlas_enabled(root)
        # The cell matters only to the atlas: a bad export card must not break the default stage.
        cell_m = combine_cell_m(root) if atlas else DEFAULT_COMBINE_CELL_M
        cached = cache_pictures(root, client=client or UrllibTransport())
        written = apply_pictures(Path(base).expanduser(), output, overwrite=overwrite, atlas=atlas, cell_m=cell_m)
    except (MapError, PanoramaxError, FileExistsError) as exc:
        print(f"panoramax: {exc}", file=sys.stderr)
        return 1
    summary = f"panoramax: kept={cached.kept} assignments={len(written['assignments'])}"
    if "coverage" in written:
        percent = written["coverage"]["percent"]
        summary += f" coverage={'n/a' if percent is None else f'{percent}%'} draws_added={written['draws_added']}"
    print(summary)
    emit(signs_command(output))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code in (0, None) else 2
    if args.command != "apply":
        return 2
    return run_apply(args.base, args.out, overwrite=bool(args.overwrite))


if __name__ == "__main__":
    raise SystemExit(main())
