"""``onward-generator signs place``."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from onward_generator.config.loader import ConfigLoadError
from onward_generator.next_step import emit, overgrow_command
from onward_generator.signs.place import SignsError, place_signs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="onward-generator signs",
        description="Hang OSM street-name plates on a copy of a dressed scene. Does not export.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    place = sub.add_parser("place", help="write a new directory with one plate per named street at a junction")
    place.add_argument("--base", required=True, help="dress, materials or detail directory")
    place.add_argument("--scene", required=True, help="geo_scene.json (local metres, highway name tags)")
    place.add_argument("--out", required=True, help="new output directory")
    place.add_argument("--max-signs", type=int, default=64)
    place.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code in (0, None) else 2
    try:
        report = place_signs(args.base, args.scene, args.out, overwrite=args.overwrite, max_signs=args.max_signs)
    except (SignsError, ConfigLoadError, FileExistsError, OSError, ValueError) as exc:
        print(f"signs: {exc}", file=sys.stderr)
        return 1
    print(f"signs: plates={report['count']}")
    for row in report["signs"]:
        print(f"  {row['name']} at {row['position_m']}")
    print(f"wrote {Path(args.out).expanduser()}")
    emit(overgrow_command(Path(args.out)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
