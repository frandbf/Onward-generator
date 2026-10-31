"""``onward-generator source list|merge`` (ADR-018)."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from onward_generator.geo.sources import sha256_file
from onward_generator.io import atomic_write_json, prepare_outputs
from onward_generator.sources import registry
from onward_generator.sources.merge import MERGED_FILE, REPORT_FILE, REPORT_SCHEMA, merge_overrides
from onward_generator.sources.plugin import SourceError
from onward_generator.sources.registry import CONFIG_PATH, load_source_config, plugin_by_id


def _list(args: argparse.Namespace) -> int:
    config = load_source_config(Path(args.config))
    for plugin in registry.PLUGINS:
        rank = config.priority.index(plugin.id) + 1 if plugin.id in config.priority else "-"
        allowed = "allowed" if plugin.licence.spdx in config.licence_allowlist else "NOT ALLOWED"
        here = ""
        if args.lat is not None and args.lon is not None:
            here = "  covers" if plugin.covers(args.lon, args.lat) else "  not here"
        kinds = ",".join(sorted(plugin.produces))
        print(f"{plugin.id}  rank {rank}  {kinds}  {plugin.licence.spdx} ({allowed}){here}")
    return 0


def _pair(text: str) -> tuple[str, Path]:
    plugin_id, sep, path = text.partition("=")
    if not sep or not plugin_id or not path:
        raise SourceError(f"--overrides takes <plugin id>=<file>, not {text!r}")
    return plugin_id, Path(path).expanduser().resolve()


def _merge(args: argparse.Namespace) -> int:
    config = load_source_config(Path(args.config))
    inputs = dict(_pair(text) for text in args.overrides)
    for plugin_id in inputs:
        plugin_by_id(plugin_id)
    documents = {plugin_id: json.loads(path.read_bytes()) for plugin_id, path in inputs.items()}
    merged, counts = merge_overrides(documents, config.priority)
    out_merged, out_report = prepare_outputs(
        args.out, (MERGED_FILE, REPORT_FILE), overwrite=args.overwrite, protected=tuple(inputs.values()),
    )
    report = {
        "schema": REPORT_SCHEMA,
        "schema_version": "1.0.0",
        "inputs": [
            {"source": plugin_id, "path": str(path), "sha256": sha256_file(path)}
            for plugin_id, path in sorted(inputs.items())
        ],
        **counts,
    }
    atomic_write_json(out_merged, merged)
    atomic_write_json(out_report, report)
    won = ", ".join(f"{c['source']} {c['won']}" for c in counts["sources"])
    print(f"buildings={counts['buildings']} won: {won}")
    print(f"wrote {out_merged}")
    print(f"wrote {out_report}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="onward-generator source", description="optional data-source plugins (ADR-018)")
    parser.add_argument("--config", default=str(CONFIG_PATH), help="licence allowlist and priority (default config/sources.json)")
    sub = parser.add_subparsers(required=True)
    listing = sub.add_parser("list", help="registered plugins, rank, licence; with --lat/--lon, whether each covers that point")
    listing.add_argument("--lat", type=float)
    listing.add_argument("--lon", type=float)
    listing.set_defaults(handler=_list)
    merge = sub.add_parser("merge", help="merge plugin overrides by priority into one heights_overrides.json (offline)")
    merge.add_argument("--overrides", action="append", required=True, help="<plugin id>=<heights_overrides file>; repeat per plugin")
    merge.add_argument("--out", required=True)
    merge.add_argument("--overwrite", action="store_true")
    merge.set_defaults(handler=_merge)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code in (0, None) else 2
    try:
        return args.handler(args)
    except (SourceError, FileExistsError, ValueError, OSError) as exc:
        print(f"source: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
