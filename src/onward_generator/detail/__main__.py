"""``onward-generator detail``: phase 04 facades, roofs, urban elements and selected interiors."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

import numpy as np

from onward_generator.detail.config import DetailConfigError, load_detail_config
from onward_generator.detail.scene import DetailError, detail_scene, load_base
from onward_generator.generation.config import GenerationConfigError, Material, load_generation_config
from onward_generator.generation.gltf import build_glb
from onward_generator.generation.preview import render_rgb, side_by_side
from onward_generator.generation.scene import GenerationError
from onward_generator.io import atomic_write_bytes, atomic_write_json, prepare_outputs
from onward_generator.next_step import dress_command, emit

VISUAL_FILE = "scene_visual.glb"
COLLISION_FILE = "scene_collision.glb"
MANIFEST_FILE = "scene_detail.json"
REPORT_FILE = "detail_report.json"
COMPARE_FILES = ("compare_overview.png", "compare_closeup.png")
COLLIDER_MATERIAL = Material((0.2, 0.8, 0.3, 1.0), 1.0)
COMPARE_SIZE = (1000, 720)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="onward-generator detail", description="Phase 04: generic facades, roofs, urban elements, selected interiors")
    parser.add_argument("--scene", required=True, help="geo_scene.json")
    parser.add_argument("--heights", required=True, help="heights.json computed from that scene")
    parser.add_argument("--base", required=True, help="validated phase 03 output directory (scene3d.json, generation_report.json)")
    parser.add_argument("--out", required=True, help="output directory")
    parser.add_argument("--config", default=None, help="detail config (default: config/detail.default.json)")
    parser.add_argument("--generation-config", default=None, help="phase 03 config used for the base (default: config/generation.default.json)")
    parser.add_argument("--no-preview", action="store_true", help="skip the before/after comparison images")
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code in (0, None) else 2
    try:
        cfg = load_detail_config(args.config)
        gen_cfg = load_generation_config(args.generation_config)
        clash = sorted(set(cfg.materials) & set(gen_cfg.materials))
        if clash:
            raise DetailConfigError(cfg.path or "<detail config>", [f"materials {clash} are already defined by the generation config"])
        names = (VISUAL_FILE, COLLISION_FILE, MANIFEST_FILE, REPORT_FILE) + (() if args.no_preview else COMPARE_FILES)
        base_dir = Path(args.base)
        protected = (Path(args.scene), Path(args.heights), base_dir / "scene3d.json", base_dir / "generation_report.json",
                     base_dir / "scene_visual.glb", base_dir / "scene_collision.glb")
        targets = prepare_outputs(args.out, names, overwrite=args.overwrite, protected=protected)
        base, base_ref = load_base(args.base, args.scene, args.heights, gen_cfg)
        detailed = detail_scene(base, base_ref, args.heights, args.scene, cfg)
    except (DetailConfigError, GenerationConfigError, DetailError, GenerationError, FileExistsError, ValueError) as exc:
        print(f"detail: {exc}", file=sys.stderr)
        return 1

    materials = dict(gen_cfg.materials) | dict(cfg.materials) | {"collider": COLLIDER_MATERIAL}
    extras = {"attribution": base.attribution, "manifest": MANIFEST_FILE, "fidelity": "generic templates; not a faithful reconstruction"}
    visual = detailed.visual_nodes()
    atomic_write_bytes(targets[0], build_glb(visual, materials, extras=extras))
    atomic_write_bytes(targets[1], build_glb(detailed.collision_nodes(), materials, extras=extras))
    atomic_write_json(targets[2], detailed.manifest())
    report = detailed.report()
    atomic_write_json(targets[3], report)
    if not args.no_preview:
        before = base.visual_nodes()
        w, h = COMPARE_SIZE
        views = (
            {"azimuth_deg": 210.0, "elevation_deg": 40.0},
            {"azimuth_deg": 210.0, "elevation_deg": cfg.closeup_elevation_deg, "focus": cfg.closeup_focus},
        )
        # Both renders of a pair share one camera; the overview frames the phase 03 extent.
        for target, view in zip(targets[4:6], views, strict=True):
            if "focus" not in view:
                xs = [p.mesh.vertices for n in before for p in n.primitives if p.mesh.vertex_count]
                allv = np.concatenate(xs)
                centre = (allv[:, 0].min() + allv[:, 0].max()) / 2, (allv[:, 1].min() + allv[:, 1].max()) / 2
                radius = 0.55 * max(allv[:, 0].max() - allv[:, 0].min(), allv[:, 1].max() - allv[:, 1].min())
                view = view | {"focus": (centre[0], centre[1], radius)}
            images = [render_rgb(nodes, materials, width=w, height=h, splat_subpixel=True, **view) for nodes in (before, visual)]
            atomic_write_bytes(target, side_by_side(images))

    c, chk = report["counts"], report["phase_04_checks"]
    print(f"buildings={c['objects'].get('building', 0)} templates={c['templates']} roofs={c['roofs']}")
    print(f"openings={c['openings']} interiors={c['interiors']} urban={c['urban_elements']}")
    print(f"visual_triangles={c['visual_triangles']} (phase 03: {c['visual_triangles_phase03']}) collision_triangles={c['collision_triangles']} "
          f"materials={c['materials']} nodes={c['visual_nodes']} shared_meshes={c['shared_meshes']}")
    print(f"severity={report['severity']}")
    for path in targets:
        print(f"wrote {path}")
    print("phase 04 automatic checks: " + ("PASSED" if chk["automatic_checks_passed"] else f"FAILED ({chk['critical_errors']} critical)")
          + "; visual review: pending")
    if chk["automatic_checks_passed"]:
        emit(dress_command(Path(args.out), Path(args.scene), Path(args.heights)))
    return 0 if chk["automatic_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
