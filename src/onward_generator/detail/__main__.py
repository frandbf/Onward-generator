"""``onward-generator detail``: phase 04 facades, roofs, urban elements and selected interiors."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from onward_generator.contracts import is_schema
from onward_generator.detail.budget import detail_scene_budget
from onward_generator.detail.config import DetailConfigError, load_detail_config
from onward_generator.detail.scene import DetailError, detail_scene, load_base
from onward_generator.detail.typology import TypologyConfigError, load_typology_config
from onward_generator.generation.config import GenerationConfigError, Material, load_generation_config
from onward_generator.generation.gltf import build_glb
from onward_generator.generation.preview import render_rgb, side_by_side
from onward_generator.generation.scene import GenerationError
from onward_generator.io import atomic_write_bytes, atomic_write_json, prepare_outputs
from onward_generator.next_step import dress_command, emit
from onward_generator.tiers import SCENIC_WALLS, check_target, core_square

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
    parser.add_argument("--typology-config", default=None, help="typology rules (default: config/typology.default.json, ADR-024)")  # pragma: no mutate: help text; argparse's default for an option is already None
    parser.add_argument("--generation-config", default=None, help="phase 03 config used for the base (default: config/generation.default.json)")
    parser.add_argument("--no-preview", action="store_true", help="skip the before/after comparison images")
    parser.add_argument("--core-radius-m", type=float,
                        help="half-side of the playable core square, metres (ADR-017); buildings outside it are scenic shells")
    parser.add_argument("--core-target-triangles", type=int,
                        help="budget mode (ADR-017 amendment): solve the core half-side for this many core visual triangles, "
                             "1000 to 2000000; --core-radius-m is then the largest half-side")
    parser.add_argument("--places",
                        help="overture_places.json from overture match: shop evidence ranked by config/sources.json evidence (ADR-021)")
    parser.add_argument("--overwrite", action="store_true")
    return parser


def load_places(path: str | None, scene_path: str) -> list[dict[str, Any]]:
    """The places of ``overture_places.json``, after checking it was made for this scene's local grid."""
    if path is None:
        return []
    doc = json.loads(Path(path).expanduser().read_bytes())
    if not is_schema(doc.get("schema"), "onward_generator.overture_places"):
        raise DetailError(f"{path} is not onward_generator.overture_places")
    frame = json.loads(Path(scene_path).expanduser().read_bytes()).get("frame") or {}
    if any(doc["frame"].get(k) != frame.get(k) for k in ("crs", "origin_projected")):
        raise DetailError(f"{path} was made for another local grid than {scene_path}")
    return list(doc["places"])


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code in (0, None) else 2
    try:
        if args.core_target_triangles is not None:
            if args.core_radius_m is None:
                raise DetailError("--core-target-triangles needs --core-radius-m")
            check_target(args.core_target_triangles)
            core_square(args.core_radius_m)
        cfg = load_detail_config(args.config)
        typology = load_typology_config(args.typology_config)
        gen_cfg = load_generation_config(args.generation_config)
        clash = sorted(set(cfg.materials) & set(gen_cfg.materials))
        if clash:
            raise DetailConfigError(cfg.path or "<detail config>", [f"materials {clash} are already defined by the generation config"])
        names = (VISUAL_FILE, COLLISION_FILE, MANIFEST_FILE, REPORT_FILE) + (() if args.no_preview else COMPARE_FILES)
        base_dir = Path(args.base)
        places = load_places(args.places, args.scene)
        protected = (Path(args.scene), Path(args.heights), base_dir / "scene3d.json", base_dir / "generation_report.json",
                     base_dir / "scene_visual.glb", base_dir / "scene_collision.glb")
        targets = prepare_outputs(args.out, names, overwrite=args.overwrite, protected=protected)
        base, base_ref = load_base(args.base, args.scene, args.heights, gen_cfg)
        if args.core_target_triangles is None:
            detailed = detail_scene(base, base_ref, args.heights, args.scene, cfg, core_half_side_m=args.core_radius_m, places=places,
                                    typology=typology)
        else:
            detailed = detail_scene_budget(base, base_ref, args.heights, args.scene, cfg, target_triangles=args.core_target_triangles,
                                           max_half_side_m=args.core_radius_m, places=places, typology=typology)
    except (DetailConfigError, TypologyConfigError, GenerationConfigError, DetailError, GenerationError, FileExistsError, ValueError, OSError, KeyError) as exc:
        print(f"detail: {exc}", file=sys.stderr)
        return 1

    materials = dict(gen_cfg.materials) | dict(cfg.materials) | dict(SCENIC_WALLS) | {"collider": COLLIDER_MATERIAL}
    # The attribution names the third-party datasets that gave a shop (ADR-021).
    extras = {"attribution": detailed.attribution, "manifest": MANIFEST_FILE, "fidelity": "generic templates; not a faithful reconstruction"}
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
    if detailed.core is not None and "target_triangles" in detailed.core:
        core = detailed.core
        print(f"core half_side_m={core['half_side_m']:g} target={core['target_triangles']} predicted={core['predicted_triangles']} "
              f"achieved={core['achieved_triangles']} within_tolerance={core['within_tolerance']}")
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
