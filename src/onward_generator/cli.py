"""Command-line interface: ``python -m onward_generator <command>``."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from onward_generator import __version__
from onward_generator.platform import PLATFORM_CHOICES, PLATFORM_ENV, PlatformError, resolve_host

EXIT_OK = 0
EXIT_INVALID = 1
EXIT_USAGE = 2


def _cmd_version(args: argparse.Namespace) -> int:
    if args.json:
        print(json.dumps({"name": "onward-generator", "version": __version__}))
    else:
        print(f"onward-generator {__version__}")
    return EXIT_OK


def _cmd_doctor(args: argparse.Namespace) -> int:
    from onward_generator.doctor import run_checks

    checks = run_checks()
    failed = any(c.status == "fail" for c in checks)
    if args.json:
        print(json.dumps({"ok": not failed, "checks": [c.to_dict() for c in checks]}, ensure_ascii=False, indent=2))
    else:
        for check in checks:
            print(f"[{check.status.upper():4}] {check.name:22} {check.detail}")
        print("doctor: " + ("FAILED" if failed else "OK"))
    return EXIT_INVALID if failed else EXIT_OK


def _cmd_platform(args: argparse.Namespace) -> int:
    try:
        host = resolve_host()
    except PlatformError as exc:
        print(f"platform: {exc}", file=sys.stderr)
        return EXIT_USAGE
    if args.json:
        print(json.dumps(host.to_dict(), ensure_ascii=False, indent=2))
    else:
        for key, value in host.to_dict().items():
            print(f"{key:18} {value}")
    return EXIT_OK


def _select_platform(value: str) -> str | None:
    """Validate a ``--platform`` value and export it to child processes. Returns an error or None."""
    try:
        resolve_host(value)
    except PlatformError as exc:
        return str(exc)
    if value != "auto":
        os.environ[PLATFORM_ENV] = value
    return None


def _cmd_validate_config(args: argparse.Namespace) -> int:
    from onward_generator.config.validation import validate_config_file

    result = validate_config_file(args.path)
    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        for diagnostic in result.diagnostics:
            print(diagnostic, file=sys.stderr if diagnostic.severity == "error" else sys.stdout)
        print(f"{result.path}: {'valid' if result.valid else 'INVALID'}")
    return EXIT_OK if result.valid else EXIT_INVALID


def _cmd_run(args: argparse.Namespace) -> int:
    from onward_generator.maps.workspace import MapError, map_directory
    from onward_generator.pipeline_run import run_map

    host = None
    if args.platform is not None:
        error = _select_platform(args.platform)
        if error is not None:
            print(f"run: {error}", file=sys.stderr)
            return EXIT_USAGE
        host = resolve_host()
    try:
        directory = map_directory(args.name)
    except MapError as exc:
        print(f"run: {exc}", file=sys.stderr)
        return EXIT_INVALID
    return run_map(
        map_id=directory.name,
        name=args.name,
        latitude=args.lat,
        longitude=args.lon,
        radius_m=args.radius_m,
        margin_m=args.margin_m,
        out=str(directory),
        seed=args.seed,
        description=args.description,
        brief=args.brief,
        ambient_ai=args.ai,
        panoramax=args.panoramax,
        lightmap_texels_per_m=args.lightmap_texels_per_m,
        overwrite=args.overwrite,
        start=args.start,
        host=host,
        verbose=args.verbose,
        no_unity=args.no_unity,
    )


def _cmd_heights(args: argparse.Namespace) -> int:
    from onward_generator.heights import HeightConfigError, SceneError, load_height_config, run_heights, write_height_outputs

    try:
        config = load_height_config(args.config, overrides_path=args.overrides)
        run = run_heights(args.scene, config)
        heights_path, report_path = write_height_outputs(run, args.out, overwrite=args.overwrite)
    except (HeightConfigError, SceneError, FileExistsError, ValueError) as exc:
        print(f"heights: {exc}", file=sys.stderr)
        return EXIT_INVALID
    report = run.report_dict()
    pct = report["percent_by_class"]
    print(
        f"buildings={report['counts']['buildings']} measured={pct['measured']}% inferred={pct['inferred']}% "
        f"fallback={pct['fallback']}% manual_review={report['counts']['needs_manual_review']}"
    )
    print(f"wrote {heights_path}")
    print(f"wrote {report_path}")
    from onward_generator.next_step import emit, generate_command

    emit(generate_command(Path(args.scene), heights_path))
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="onward-generator", description="Onward map generator")
    parser.add_argument(
        "--platform",
        choices=PLATFORM_CHOICES,
        default=None,
        help=f"host for the Unity paths (default: ${PLATFORM_ENV}, else detected); goes before the command",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")

    version = sub.add_parser("version", help="print the generator version")
    version.add_argument("--json", action="store_true")
    version.set_defaults(handler=_cmd_version)

    doctor = sub.add_parser("doctor", help="check the environment and dependencies (read-only)")
    doctor.add_argument("--json", action="store_true")
    doctor.set_defaults(handler=_cmd_doctor)

    host = sub.add_parser("platform", help="print the host platform and the Unity, adb and log paths it uses")
    host.add_argument("--json", action="store_true")
    host.set_defaults(handler=_cmd_platform)

    validate = sub.add_parser("validate-config", help="validate a project configuration file")
    validate.add_argument("path", help="configuration JSON file")
    validate.add_argument("--json", action="store_true")
    validate.set_defaults(handler=_cmd_validate_config)

    heights = sub.add_parser("heights", help="estimate building heights for a geo scene")
    heights.add_argument("--scene", required=True, help="geo_scene.json produced by the geo pipeline")
    heights.add_argument("--out", required=True, help="output directory for heights.json and heights_report.json")
    heights.add_argument("--config", default=None, help="height configuration (default: config/heights.default.json)")
    heights.add_argument("--overrides", default=None, help="authoritative overrides JSON (replaces the config's overrides_file)")
    heights.add_argument("--overwrite", action="store_true", help="replace existing outputs")
    heights.set_defaults(handler=_cmd_heights)

    sub.add_parser("geo", help="run the geographic pipeline (see: geo --help)", add_help=False)
    sub.add_parser("cadastre", help="Catastro INSPIRE floors: download | match (see: cadastre --help)", add_help=False)
    sub.add_parser("generate", help="phase 03 geometry: ground, streets, buildings (see: generate --help)", add_help=False)
    sub.add_parser("detail", help="phase 04: facades, roofs, urban elements, interiors (see: detail --help)", add_help=False)
    sub.add_parser("perf", help="phase 05: measure | layout | device | drawcalls (see: perf --help)", add_help=False)
    sub.add_parser("export", help="phase 07: package | verify — Onward export package (see: export --help)", add_help=False)
    sub.add_parser("ai", help="phase 06: plan — instruction to validated configuration (see: ai --help)", add_help=False)
    sub.add_parser("dress", help="classify materials (dry-run) or restyle a copy (see: dress --help)", add_help=False)
    sub.add_parser("bounds", help="copy a scene and add a playable perimeter wall (see: bounds --help)", add_help=False)
    sub.add_parser("props", help="Asset Store prop catalogue (see: props --help)", add_help=False)
    sub.add_parser("materials", help="apply a texture-plan JSON to a scene copy (see: materials --help)", add_help=False)
    sub.add_parser("panoramax", help="under development: project 360 street photos onto exposed facades (see: panoramax --help)", add_help=False)
    sub.add_parser("signs", help="hang OSM street-name plates on facades (see: signs --help)", add_help=False)
    sub.add_parser("overgrow", help="add abandoned-city grass, trees and wrecks (see: overgrow --help)", add_help=False)
    sub.add_parser("map", help="create or inspect one map folder (see: map --help)", add_help=False)
    run = sub.add_parser(
        "run",
        help="create a map and run every Python stage through export package",
    )
    run.add_argument("--name", required=True, help="Quest card title, 1–80 characters; the id and maps/<id> folder are the slug of this name")
    run.add_argument("--lat", required=True, type=float, help="centre latitude, WGS84 degrees")
    run.add_argument("--lon", required=True, type=float, help="centre longitude, WGS84 degrees")
    run.add_argument("--radius-m", type=float, default=400.0, help="half-side of the square area, metres (50–1500)")
    run.add_argument("--margin-m", type=float, default=30.0, help="inclusion margin around the square, metres")
    run.add_argument("--seed", type=int, default=None)
    run.add_argument("--description", default=None)
    run.add_argument("--brief", default=None, help="how the city should feel; overgrow reads this")
    run.add_argument("--lightmap-texels-per-m", type=float, default=10.0, help="lightmap texels per metre (default 10, greater than 0 and at most 40)")
    run.add_argument("--ai", action="store_true", help="ask local Ollama for a placement plan; a rejected reply keeps the current overgrow rules")
    run.add_argument("--panoramax", action="store_true", help="under development: project 360 street photos onto exposed facades; off unless this flag is set")
    run.add_argument("--overwrite", action="store_true", help="replace map.json when processed/ is empty")
    run.add_argument("--from", dest="start", default=None, help="stage to run, skipping the question when work already exists")
    run.add_argument("--platform", choices=PLATFORM_CHOICES, default=None, help="host for the Unity steps; same as the global --platform")
    run.add_argument("--verbose", action="store_true", help="also print each stage's full output")
    run.add_argument("--no-unity", action="store_true", help="stop after export and print the command for the Unity steps (task map uses this)")
    run.set_defaults(handler=_cmd_run)
    sub.add_parser("osm", help="download the OSM XML for one map (see: osm --help)", add_help=False)
    return parser


def _global_platform(argv: list[str]) -> tuple[list[str], str | None]:
    """Remove a leading ``--platform X`` or ``--platform=X``. Module subcommands never see it."""
    if argv[:1] == ["--platform"]:
        if len(argv) < 2:
            return argv[1:], ""
        return argv[2:], argv[1]
    if argv and argv[0].startswith("--platform="):
        return argv[1:], argv[0].split("=", 1)[1]
    return argv, None


def main(argv: Sequence[str] | None = None) -> int:
    argv, platform = _global_platform(list(sys.argv[1:] if argv is None else argv))
    if platform is not None:
        error = _select_platform(platform)
        if error is not None:
            print(f"onward-generator: {error}", file=sys.stderr)
            return EXIT_USAGE
    if argv[:1] == ["geo"]:
        from onward_generator.geo.__main__ import main as geo_main

        return geo_main(argv[1:])
    if argv[:1] == ["cadastre"]:
        from onward_generator.cadastre.__main__ import main as cadastre_main

        return cadastre_main(argv[1:])
    if argv[:1] == ["generate"]:
        from onward_generator.generation.__main__ import main as generate_main

        return generate_main(argv[1:])
    if argv[:1] == ["detail"]:
        from onward_generator.detail.__main__ import main as detail_main

        return detail_main(argv[1:])
    if argv[:1] == ["perf"]:
        from onward_generator.perf.__main__ import main as perf_main

        return perf_main(argv[1:])
    if argv[:1] == ["export"]:
        from onward_generator.export.__main__ import main as export_main

        return export_main(argv[1:])
    if argv[:1] == ["ai"]:
        from onward_generator.ai.__main__ import main as ai_main

        return ai_main(argv[1:])
    if argv[:1] == ["dress"]:
        from onward_generator.dress.__main__ import main as dress_main

        return dress_main(argv[1:])
    if argv[:1] == ["bounds"]:
        from onward_generator.bounds.__main__ import main as bounds_main

        return bounds_main(argv[1:])
    if argv[:1] == ["props"]:
        from onward_generator.props.__main__ import main as props_main

        return props_main(argv[1:])
    if argv[:1] == ["materials"]:
        from onward_generator.materials.__main__ import main as materials_main

        return materials_main(argv[1:])
    if argv[:1] == ["panoramax"]:
        from onward_generator.panoramax.__main__ import main as panoramax_main

        return panoramax_main(argv[1:])
    if argv[:1] == ["signs"]:
        from onward_generator.signs.__main__ import main as signs_main

        return signs_main(argv[1:])
    if argv[:1] == ["overgrow"]:
        from onward_generator.overgrow.__main__ import main as overgrow_main

        return overgrow_main(argv[1:])
    if argv[:1] == ["osm"]:
        from onward_generator.osm.__main__ import main as osm_main

        return osm_main(argv[1:])
    if argv[:1] == ["map"]:
        from onward_generator.maps.__main__ import main as map_main

        return map_main(argv[1:])
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0) if exc.code in (0, None) else EXIT_USAGE
    return args.handler(args)
