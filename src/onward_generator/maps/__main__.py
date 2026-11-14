"""Create or inspect a map folder.

    onward-generator map create --name "Apocalipsis Murciana" --lat 37.98407 --lon -1.12863
    onward-generator map show maps/apocalipsis_murciana
"""

from __future__ import annotations

import argparse
import sys

from onward_generator.ai.providers import OllamaProvider, ProviderError
from onward_generator.export.atmosphere import NEUTRAL, preset_names
from onward_generator.maps.workspace import (
    DEFAULT_OFF_SWITCHES,
    MAP_SWITCHES,
    chosen_switches,
    MARGIN_DEFAULT_M,
    RADIUS_DEFAULT_M,
    TIME_OF_DAY_DEFAULT,
    TIMES_OF_DAY,
    MapError,
    create_map,
    load_map,
    map_directory,
    refresh_texture_plan,
)
from onward_generator.materials.plan import PlanError
from onward_generator.next_step import emit, geo_command
from onward_generator.osm.acquire import OsmDownloadError, download_map_osm, osm_target


def _texture_provider(seed: int | None) -> OllamaProvider:
    return OllamaProvider(seed=0 if seed is None else seed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="onward-generator map", description="One folder per map.")
    sub = parser.add_subparsers(dest="action", required=True)

    create = sub.add_parser("create", help="write a map folder from a name and a WGS84 centre")
    create.add_argument("--name", required=True, help="Quest card title, 1–80 characters; the id and maps/<id> folder are the slug of this name")
    create.add_argument("--lat", type=float, default=None, help="centre latitude, WGS84 degrees")
    create.add_argument("--lon", type=float, default=None, help="centre longitude, WGS84 degrees")
    create.add_argument("--radius-m", type=float, default=RADIUS_DEFAULT_M, help=f"half-side of the square area, metres (50–1500, default {RADIUS_DEFAULT_M:g})")
    create.add_argument("--margin-m", type=float, default=MARGIN_DEFAULT_M, help="inclusion margin around the square, metres")
    create.add_argument("--seed", type=int, default=None, help="deterministic seed; default derives from the centre")
    create.add_argument("--description", default=None, help="card description, max 600 characters")
    create.add_argument("--brief", default=None, help="how the city should feel; overgrow reads this")
    create.add_argument("--time-of-day", choices=TIMES_OF_DAY, default=TIME_OF_DAY_DEFAULT, help="day or night: light, Onward profile and street lamps (ADR-026)")  # pragma: no mutate: help text
    create.add_argument("--storm", action="store_true", help="night maps only: lightning and thunder (ADR-028)")  # pragma: no mutate: help text
    create.add_argument("--atmosphere", choices=preset_names(), default=NEUTRAL, help="day maps only: baked light preset (ADR-037)")  # pragma: no mutate: help text
    create.add_argument("--lightmap-texels-per-m", type=float, default=10.0, help="lightmap texels per metre (default 10, greater than 0 and at most 40)")
    create.add_argument("--core-radius-m", type=float, help="half-side of the playable core square, metres (default: 50 m inside the radius, at most 150; 0 or at least the radius plays the whole map; ADR-017)")
    create.add_argument("--core-target-triangles", type=int, help="budget core (ADR-017 amendment): solve the core half-side for this many core visual triangles in detail, 1000 to 2000000; --core-radius-m, or 50 m inside the radius, is the largest half-side")
    create.add_argument("--ai", action="store_true", help="ask local Ollama for a placement plan; a rejected reply keeps the current overgrow rules")
    create.add_argument("--panoramax", action="store_true", help="under development: project 360 street photos onto exposed facades; off unless this flag is set")
    create.add_argument("--overwrite", action="store_true", help="replace map.json and configs when processed/ is empty")
    create.add_argument("--textures", action="store_true", help="write or replace only config/texture_plan.json")
    # Plan Q8: the map.json switches, so a new map needs no hand edit and no second --textures run. Since 13 November
    # 2026 each is on unless --no-<switch> (panoramax_atlas off unless --panoramax-atlas): see DEFAULT_OFF_SWITCHES.
    for switch, text in MAP_SWITCHES.items():
        create.add_argument(
            "--" + switch.replace("_", "-"), dest=switch, action=argparse.BooleanOptionalAction, default=None,
            help=f"map.json {switch}: {text} (default {'off' if switch in DEFAULT_OFF_SWITCHES else 'on'})",  # pragma: no mutate: help text
        )

    show = sub.add_parser("show", help="print a map folder and the geo command that reads it")
    show.add_argument("directory", help="map directory containing map.json")

    args = parser.parse_args(argv)
    try:
        if args.action == "create":
            directory = map_directory(args.name)
            if args.textures and (directory / "map.json").is_file():
                path = refresh_texture_plan(directory, texture_provider=_texture_provider(args.seed))
                print(f"texture plan {path}")
                return 0
            if args.lat is None or args.lon is None:
                parser.error("--lat and --lon are required to create a map")
            document = create_map(
                directory,
                map_id=directory.name,
                name=args.name,
                latitude=args.lat,
                longitude=args.lon,
                radius_m=args.radius_m,
                margin_m=args.margin_m,
                seed=args.seed,
                description=args.description,
                brief=args.brief,
                ambient_ai=args.ai,
                panoramax=args.panoramax,
                time_of_day=args.time_of_day,
                storm=args.storm,
                atmosphere=args.atmosphere,
                lightmap_texels_per_m=args.lightmap_texels_per_m,
                core_radius_m=args.core_radius_m,
                core_target_triangles=args.core_target_triangles,
                overwrite=args.overwrite,
                texture_provider=_texture_provider(args.seed),
                switches=chosen_switches({switch: getattr(args, switch) for switch in MAP_SWITCHES}),
            )
            print(f"map {document['id']} ({document['name']}) at {directory}")
            print(f"texture plan {directory / 'config' / 'texture_plan.json'}")
            print(f"centre {document['center']['latitude']}, {document['center']['longitude']}  radius {document['radius_m']} m  lightmap {args.lightmap_texels_per_m:g} texels/m")
            on = [switch for switch in MAP_SWITCHES if document.get(switch)]
            if on:
                print("switches " + ", ".join(on))
            existing = osm_target(directory)
            if existing.is_file() and not args.overwrite:
                print(f"osm already at {existing}")
            else:
                record = download_map_osm(directory, overwrite=args.overwrite)
                print(f"osm {record.size_bytes} bytes, sha256 {record.sha256}")
                print(f"wrote {record.path}")
            emit(geo_command(directory))
            return 0
        document = load_map(args.directory)
    except (MapError, OsmDownloadError, PlanError, ProviderError, FileExistsError, OSError) as exc:
        print(f"map: {exc}", file=sys.stderr)
        return 1

    root = args.directory
    center = document["center"]
    paths = document["paths"]
    print(f"{document['id']}  {document['name']}")
    print(f"centre {center['latitude']}, {center['longitude']}  radius {document['radius_m']} m  {document['crs']}")
    print(f"project  {root}/{paths['project']}")
    print(f"export   {root}/{paths['export']}")
    print(f"aoi      {root}/{paths['aoi']}")
    emit(geo_command(root))
    return 0
