"""``onward-generator props catalog``: validate and list approved Asset Store props."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from onward_generator.props.catalog import (
    CATALOG_PATH,
    CatalogError,
    bind_inventory,
    catalog_document,
    load_catalog,
)
from onward_generator.props.place import PlaceError, place_props


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="onward-generator props",
        description="Prop catalogue. Does not instance objects on the map.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("catalog", help="validate the catalogue and optionally bind a Unity inventory")
    c.add_argument("--catalog", default=None, help="catalogue JSON (default: config/props/catalog.json)")
    c.add_argument("--inventory", default=None, help="importer inventory JSON written by Unity")
    c.add_argument("--out", default=None, help="write the bound catalogue (never overwrites without --overwrite)")
    c.add_argument("--overwrite", action="store_true")
    c.add_argument("--json", action="store_true")
    p = sub.add_parser("place", help="copy a scene and write OSM-evidenced Asset Store instances (no GLB bake)")
    p.add_argument("--base", required=True, help="dress/detail/materials directory")
    p.add_argument("--out", required=True, help="new output directory")
    p.add_argument("--catalog", default=None, help="bound catalogue with real paths")
    p.add_argument("--scene", default=None, help="geo_scene.json if not recorded in the manifest")
    p.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code in (0, None) else 2
    try:
        if args.command == "place":
            report = place_props(
                args.base, args.out,
                catalog_path=args.catalog, scene_path=args.scene, overwrite=args.overwrite,
            )
            print(
                f"props place: instances={report['instances']} vehicles={report['vehicles']} "
                f"usable={report['usable_props']}"
            )
            for row in report["skipped"]:
                print(f"  skip {row['reason']}")
            print(f"wrote {args.out}")
            return 0
        catalog = load_catalog(args.catalog)
        if args.inventory:
            inv = json.loads(Path(args.inventory).expanduser().read_text(encoding="utf-8"))
            assets = inv.get("assets", [])
            unity_root = inv.get("unity_project")
            catalog = bind_inventory(catalog, assets, unity_root=unity_root)
        if args.out:
            dest = Path(args.out).expanduser()
            if dest.exists() and not args.overwrite:
                raise FileExistsError(f"refusing to overwrite {dest}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(json.dumps(catalog_document(catalog), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except (CatalogError, PlaceError, FileExistsError, OSError, json.JSONDecodeError, KeyError) as exc:
        print(f"props: {exc}", file=sys.stderr)
        return 1
    usable = catalog.usable_props
    summary = {
        "catalog": str(args.catalog or CATALOG_PATH),
        "packs": len(catalog.packs),
        "props": len(catalog.props),
        "usable": len(usable),
        "pending": sum(1 for p in catalog.props if p.source == "pending"),
        "prop_ids": [p.prop_id for p in catalog.props],
        "wrote": str(args.out) if args.out else None,
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(
            f"packs={summary['packs']} props={summary['props']} "
            f"usable={summary['usable']} pending={summary['pending']}"
        )
        if args.out:
            print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
