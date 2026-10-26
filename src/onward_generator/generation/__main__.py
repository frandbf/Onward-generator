"""``onward-generator generate``: phase 03 geometry from a geo scene and its heights."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from onward_generator.generation.config import GenerationConfigError, Material, load_generation_config
from onward_generator.generation.gltf import build_glb
from onward_generator.generation.preview import render
from onward_generator.generation.scene import GenerationError, generate_scene
from onward_generator.io import atomic_write_bytes, atomic_write_json, prepare_outputs
from onward_generator.next_step import detail_command, emit

VISUAL_FILE = "scene_visual.glb"
COLLISION_FILE = "scene_collision.glb"
MANIFEST_FILE = "scene3d.json"
REPORT_FILE = "generation_report.json"
PREVIEW_FILES = ("preview_southwest.png", "preview_northeast.png")
COLLIDER_MATERIAL = Material((0.2, 0.8, 0.3, 1.0), 1.0)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="onward-generator generate", description="Phase 03: ground, streets, sidewalks, building volumes")
    parser.add_argument("--scene", required=True, help="geo_scene.json")
    parser.add_argument("--heights", required=True, help="heights.json computed from that scene")
    parser.add_argument("--out", required=True, help="output directory")
    parser.add_argument("--config", default=None, help="generation config (default: config/generation.default.json)")
    parser.add_argument("--no-preview", action="store_true", help="skip the PNG previews")
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code in (0, None) else 2
    try:
        config = load_generation_config(args.config)
        names = (VISUAL_FILE, COLLISION_FILE, MANIFEST_FILE, REPORT_FILE) + (() if args.no_preview else PREVIEW_FILES)
        targets = prepare_outputs(args.out, names, overwrite=args.overwrite, protected=(Path(args.scene), Path(args.heights)))
        generated = generate_scene(args.scene, args.heights, config)
    except (GenerationConfigError, GenerationError, FileExistsError, ValueError) as exc:
        print(f"generate: {exc}", file=sys.stderr)
        return 1

    visual = generated.visual_nodes()
    materials = dict(config.materials) | {"collider": COLLIDER_MATERIAL}
    extras = {"attribution": generated.attribution, "manifest": MANIFEST_FILE}
    atomic_write_bytes(targets[0], build_glb(visual, materials, extras=extras))
    atomic_write_bytes(targets[1], build_glb(generated.collision_nodes(), materials, extras=extras))
    atomic_write_json(targets[2], generated.manifest())
    report = generated.report()
    atomic_write_json(targets[3], report)
    if not args.no_preview:
        atomic_write_bytes(targets[4], render(visual, materials, azimuth_deg=210.0))
        atomic_write_bytes(targets[5], render(visual, materials, azimuth_deg=40.0))

    c, exit_ = report["counts"], report["phase_03_exit"]
    print(
        f"buildings={c['buildings_generated']} (skipped {c['buildings_skipped']}) visual_triangles={c['visual_triangles']} "
        f"collision_triangles={c['collision_triangles']} materials={c['materials']} colliders={c['colliders']}"
    )
    print(f"severity={report['severity']} overlap={report['checks']['surface_overlap_m2']} m² gap={report['checks']['surface_gap_m2']} m²")
    for path in targets:
        print(f"wrote {path}")
    print("phase 03 exit: " + ("PASSED" if exit_["passed"] else f"FAILED ({exit_['critical_errors']} critical)"))
    if exit_["passed"]:
        emit(detail_command(Path(args.scene), Path(args.heights), Path(args.out)))
    return 0 if exit_["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
