"""``onward-generator perf``: phase 05 measurement (offline files, gameplay fixture, device logs)."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from shapely.geometry import shape

from onward_generator.detail.scene import DetailError, load_base
from onward_generator.gameplay.report import REPORT_FILE, gameplay_report, headline_text, report_text
from onward_generator.generation.config import GenerationConfigError, load_generation_config
from onward_generator.generation.scene import GenerationError
from onward_generator.io import atomic_write_bytes, atomic_write_json, prepare_outputs, sha256_file
from onward_generator.perf.device import drawcall_trace, load_times, summarize_csv
from onward_generator.perf.layout import (
    TIME_OF_DAY_SETTINGS,
    LayoutError,
    balanced_playable_layout,
    gameplay_layout,
    load_detail_manifest,
    playable_layout,
)
from onward_generator.perf.measure import combine_groups, measure_scene_dir

MEASURE_SCHEMA = "onward_generator.perf_measure"
DEVICE_SCHEMA = "onward_generator.perf_device"
DRAWCALLS_SCHEMA = "onward_generator.perf_drawcalls"
SCHEMA_VERSION = "0.1.0"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="onward-generator perf", description="Phase 05: measure for Meta Quest")
    sub = parser.add_subparsers(dest="command", required=True)

    m = sub.add_parser("measure", help="count what a scene directory's GLB files contain (offline)")
    m.add_argument("dirs", nargs="+", help="directories with scene_visual.glb and scene_collision.glb")
    m.add_argument("--combine-cell-m", type=float, nargs="*", default=[],
                   help="also predict draws if visual primitives were merged per (cell, material)")
    m.add_argument("--out", required=True, help="output JSON")
    m.add_argument("--overwrite", action="store_true")

    dc = sub.add_parser("drawcalls", help="count draws in ovrgpuprofiler draw-call traces")
    dc.add_argument("traces", nargs="+", help="LABEL=PATH, e.g. v00_A=drawcalls_2.txt")
    dc.add_argument("--out", required=True)
    dc.add_argument("--overwrite", action="store_true")

    lay = sub.add_parser("layout", help="spawn/objective fixture for the device measurement map")
    lay.add_argument("--scene", required=True)
    lay.add_argument("--heights", required=True)
    lay.add_argument("--base", required=True, help="validated phase 03 directory")
    lay.add_argument("--detail", required=True, help="phase 04 directory (urban element positions)")
    lay.add_argument("--generation-config", default=None)
    lay.add_argument("--out", required=True, help="output JSON")
    lay.add_argument("--playable", action="store_true", help="write the Hunt/audio/urban layout instead of the measurement fixture")
    lay.add_argument(
        "--time-of-day", choices=sorted(TIME_OF_DAY_SETTINGS), default="day",
        help="playable layout only: day or night (ADR-026)",  # pragma: no mutate: help text
    )
    lay.add_argument("--storm", action="store_true", help="playable night layout only: lightning and thunder (ADR-028)")  # pragma: no mutate: help text
    lay.add_argument(
        "--gameplay-report", action="store_true",
        help=f"playable layout only: also write {REPORT_FILE} beside --out (routes, sightlines, cover, balance; ADR-032)",  # pragma: no mutate: help text
    )
    lay.add_argument(
        "--balanced-spawns", action="store_true",
        help="playable layout only: place the defenders and the Uplink by the gameplay metrics (plan C6, ADR-032; off by default)",  # pragma: no mutate: help text
    )
    lay.add_argument("--overwrite", action="store_true")

    dev = sub.add_parser("device", help="summarise an OVR Metrics Tool CSV and the logcat of one run")
    dev.add_argument("--csv", required=True)
    dev.add_argument("--logcat", default=None)
    dev.add_argument("--label", required=True, help="variant measured, e.g. baseline")
    dev.add_argument("--time-column", default=None, help="CSV timestamp column (ms) used with --skip-first-s")
    dev.add_argument("--skip-first-s", type=float, default=0.0, help="drop warm-up samples (menu, loading)")
    dev.add_argument("--until-s", type=float, default=None, help="drop samples from this time on (left the map)")
    dev.add_argument("--note", default=None, help="how the window was chosen")
    dev.add_argument("--out", required=True)
    dev.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code in (0, None) else 2
    want_report = args.command == "layout" and args.gameplay_report
    if want_report and not args.playable:
        print("perf: --gameplay-report needs --playable", file=sys.stderr)
        return 1
    if args.command == "layout" and args.balanced_spawns and not args.playable:
        print("perf: --balanced-spawns needs --playable", file=sys.stderr)
        return 1
    report: dict[str, Any] | None = None
    placement_line: str | None = None
    try:
        names = (Path(args.out).name, *((REPORT_FILE,) if want_report else ()))
        target, *report_target = prepare_outputs(Path(args.out).parent, names, overwrite=args.overwrite)
        if args.command == "measure":
            doc = {"schema": MEASURE_SCHEMA, "schema_version": SCHEMA_VERSION,
                   "note": "file contents only; device draw calls depend on culling and batching",
                   "scenes": [measure_scene_dir(d) for d in args.dirs]}
            for s in doc["scenes"]:
                s["combine_predictions"] = [combine_groups(Path(s["dir"]) / "scene_visual.glb", c) for c in args.combine_cell_m]
        elif args.command == "drawcalls":
            traces = {}
            for spec in args.traces:
                label, sep, path = spec.partition("=")
                if not sep or not label or not path:
                    raise ValueError(f"trace {spec!r} must be LABEL=PATH")
                if label in traces:
                    raise ValueError(f"duplicate trace label {label!r}")
                traces[label] = drawcall_trace(path)
            doc = {"schema": DRAWCALLS_SCHEMA, "schema_version": SCHEMA_VERSION,
                   "note": "ovrgpuprofiler draw-call traces taken in detailed profiling mode; not frame-time data",
                   "traces": traces}
        elif args.command == "layout":
            base, _ = load_base(args.base, args.scene, args.heights, load_generation_config(args.generation_config))
            manifest = load_detail_manifest(args.detail)
            if args.playable:
                heights = json.loads(Path(args.heights).expanduser().read_text(encoding="utf-8"))
                scene = json.loads(Path(args.scene).expanduser().read_bytes())
                footprints = {f["id"]: shape(f["geometry"]) for f in scene["features"]}
                if args.balanced_spawns:
                    doc, placement_line = balanced_playable_layout(
                        base, manifest, heights, time_of_day=args.time_of_day, storm=args.storm, footprints=footprints)
                else:
                    doc = playable_layout(base, manifest, heights, time_of_day=args.time_of_day, storm=args.storm, footprints=footprints)
                if want_report:
                    # Computed before either file is written: a refusal leaves both untouched.
                    report = gameplay_report(base, manifest, doc, Path(args.detail).expanduser() / "scene_collision.glb")
            else:
                doc = gameplay_layout(base, manifest)
        else:
            doc = {"schema": DEVICE_SCHEMA, "schema_version": SCHEMA_VERSION, "label": args.label,
                   "metrics": summarize_csv(args.csv, skip_first_s=args.skip_first_s, until_s=args.until_s,
                                            time_column=args.time_column),
                   "window_s": [args.skip_first_s, args.until_s], "window_note": args.note,
                   "loads": load_times(args.logcat) if args.logcat else None}
    except (DetailError, GenerationError, GenerationConfigError, LayoutError, FileExistsError, FileNotFoundError, ValueError) as exc:
        print(f"perf: {exc}", file=sys.stderr)
        return 1
    atomic_write_json(target, doc)
    if report is not None:
        # The report names the layout bytes it was computed from, so a stale report is recognised.
        report["layout_sha256"] = sha256_file(target)
        atomic_write_bytes(report_target[0], report_text(report).encode("utf-8"))
    if args.command == "measure":
        for s in doc["scenes"]:
            v, c = s["visual"], s["collision"]
            print(f"{s['dir']}: visual tris={v['triangles']} verts={v['vertices']} nodes={v['nodes_with_mesh']} "
                  f"primitives={v['primitives_drawn']} materials={v['materials']} textures={v['textures']} | "
                  f"collision tris={c['triangles']} nodes={c['nodes_with_mesh']}")
    elif args.command == "drawcalls":
        for label, t in doc["traces"].items():
            print(f"{label}: {t['frame_draws']} draws, {t['frame_vertices_shaded']:.0f} vertices shaded")
    elif args.command == "layout":
        parts: list[str] = []
        if "routes" in doc:
            parts.append(f"routes={len(doc['routes'])}")
        if "ai_m" in doc:
            parts.append(f"hunt={len(doc['ai_m'])}")
        if "ai_separation_m" in doc:
            parts.append(f"separation={doc['ai_separation_m']} m")
        for team in ("attackers", "defenders"):
            points = doc.get(team)
            if isinstance(points, dict) and "points_m" in points:
                parts.append(f"{team}={len(points['points_m'])}")
        print("layout: " + (" ".join(parts) if parts else str(doc.get("schema", "written"))))
    else:
        print(json.dumps({k: v for k, v in doc.items() if k not in ("metrics",)}, ensure_ascii=False)[:600])
    if placement_line is not None:
        print(placement_line)
    print(f"wrote {target}")
    if report is not None:
        print(f"gameplay: {headline_text(report)}  status {report['status']}  (estimated, report only)")
        print(f"wrote {report_target[0]}")
    if args.command == "layout" and args.playable:
        from onward_generator.next_step import emit, export_command

        command = export_command(Path(args.detail), Path(args.out))
        if command:
            emit(command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
