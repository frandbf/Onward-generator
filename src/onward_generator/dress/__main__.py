"""``onward-generator dress classify|style``.

classify writes a recommendation report and leaves the input scene untouched.
style writes a new directory with better trees, generated furniture and an
optional procedural atlas. It does not apply AI materials.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from onward_generator import __version__
from onward_generator.ai.providers import (
    DEFAULT_OLLAMA_HOST,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_TIMEOUT_S,
    OllamaProvider,
    Provider,
    ProviderError,
)
from onward_generator.config.loader import MAX_SCENE_BYTES, ConfigLoadError, read_json_document
from onward_generator.dress.catalog import CatalogError, load_catalog
from onward_generator.dress.classify import (
    SYSTEM_PROMPT,
    ClassifyError,
    ClassifyResult,
    classify_groups,
    parse_recommendations,
)
from onward_generator.dress.geometry import compare_fingerprints, file_sha256, geometry_fingerprint
from onward_generator.dress.surfaces import SurfaceError, collect_surfaces
from onward_generator.io import atomic_write_json, prepare_outputs
from onward_generator.next_step import emit, materials_command

REPORT_NAME = "material_plan.json"
GROUPS_NAME = "surface_groups.json"
REPLAY_NAME = "classify_run.json"
STYLE_FILES = ("scene_visual.glb", "scene_collision.glb", "scene_detail.json", "detail_report.json", "atlas.png")
MAX_RECOMMENDATIONS_BYTES = 256 * 1024


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="onward-generator dress", description="Dress 0.1: classify materials (dry-run) or restyle a copy")
    sub = parser.add_subparsers(dest="command", required=True)

    c = sub.add_parser("classify", help="recommend catalogue materials; never modifies the input scene")
    c.add_argument("--detail", required=True, help="phase 04 output directory (scene_detail.json)")
    c.add_argument("--scene", default=None, help="geo_scene.json (default: path recorded in the detail manifest)")
    c.add_argument("--out", required=True, help="output directory for the report")
    c.add_argument("--catalog", default=None, help="material catalogue (default: config/dress/materials.catalog.json)")
    c.add_argument("--limit", type=int, default=12, help="max surface groups (small reversible sample; 0 = all)")
    c.add_argument("--provider", choices=("ollama", "none", "replay"), default="none",
                   help="none: deterministic rules (default); ollama: local model; replay: recorded response")
    c.add_argument("--recommendations", help="recommendation JSON for --provider none (otherwise rules are used)")
    c.add_argument("--replay", help="classify_run.json to re-validate (--provider replay)")
    c.add_argument("--model", default=DEFAULT_OLLAMA_MODEL)
    c.add_argument("--host", default=DEFAULT_OLLAMA_HOST)
    c.add_argument("--seed", type=int, default=0)
    c.add_argument("--timeout-s", type=float, default=DEFAULT_TIMEOUT_S)
    c.add_argument("--apply", action="store_true", help="refused: applying materials is not enabled yet")
    c.add_argument("--overwrite", action="store_true")

    s = sub.add_parser("style", help="write a copy with better trees, generated furniture and a procedural atlas")
    s.add_argument("--detail", required=True, help="phase 04 directory used as the geometry baseline")
    s.add_argument("--scene", default=None)
    s.add_argument("--heights", default=None)
    s.add_argument("--base", default=None, help="validated phase 03 directory")
    s.add_argument("--out", required=True)
    s.add_argument("--config", default=None, help="detail config")
    s.add_argument("--generation-config", default=None)
    s.add_argument("--catalog", default=None)
    s.add_argument("--no-atlas", action="store_true")
    s.add_argument("--overwrite", action="store_true")
    return parser


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_text(path: str, limit: int, what: str) -> str:
    p = Path(path).expanduser()
    if not p.is_file():
        raise ValueError(f"{what} not found: {p}")
    raw = p.read_bytes()
    if len(raw) > limit:
        raise ValueError(f"{what} is {len(raw)} bytes; limit is {limit}")
    return raw.decode("utf-8")


def _input_hashes(detail_dir: Path) -> dict[str, str | None]:
    return {
        "scene_detail.json": file_sha256(detail_dir / "scene_detail.json"),
        "scene_visual.glb": file_sha256(detail_dir / "scene_visual.glb"),
        "scene_collision.glb": file_sha256(detail_dir / "scene_collision.glb"),
    }


def _run_classify(args: argparse.Namespace) -> int:
    if args.apply:
        print("dress classify: --apply is refused until a material_plan.json is reviewed and approved. "
              "Nothing was written to the map.", file=sys.stderr)
        return 1

    catalog = load_catalog(args.catalog)
    limit = None if args.limit == 0 else args.limit
    groups = collect_surfaces(args.detail, scene_path=args.scene, limit=limit)
    detail_dir = Path(args.detail).expanduser()
    before_files = _input_hashes(detail_dir)
    before = groups.fingerprint

    provider: Provider | None
    raw_override = None
    replay_ref = None
    if args.provider == "replay":
        if not args.replay:
            raise ValueError("--provider replay needs --replay FILE")
        doc, resolved = read_json_document(args.replay)
        raw = doc.get("raw_response")
        if not isinstance(raw, str) or _sha(raw) != doc.get("raw_response_sha256"):
            raise ValueError(f"replay file {resolved}: raw_response missing or hash mismatch")
        raw_override = raw
        provider = None
        replay_ref = {"path": str(resolved), "sha256": hashlib.sha256(resolved.read_bytes()).hexdigest()}
    elif args.provider == "none":
        if args.recommendations:
            raw_override = _read_text(args.recommendations, MAX_RECOMMENDATIONS_BYTES, "recommendations file")
            parse_recommendations(raw_override)
            provider = None
        else:
            provider = None
    else:
        if args.recommendations or args.replay:
            raise ValueError("--recommendations/--replay are not used with --provider ollama")
        provider = OllamaProvider(model=args.model, host=args.host, seed=args.seed, timeout_s=args.timeout_s)

    result = classify_groups(groups, catalog, provider, raw_override=raw_override)
    after_files = _input_hashes(detail_dir)
    after = geometry_fingerprint(groups.objects)
    compare = compare_fingerprints(before, after)
    compare["input_files"] = {
        "before": before_files,
        "after": after_files,
        "unchanged": before_files == after_files,
    }
    if not compare["identical"] or before_files != after_files:
        raise RuntimeError("classify mutated the input scene; this is a bug")

    out = Path(args.out).expanduser().resolve()
    protected = (detail_dir / "scene_detail.json", detail_dir / "scene_visual.glb", detail_dir / "scene_collision.glb")
    names = (REPORT_NAME, GROUPS_NAME, REPLAY_NAME)
    targets = prepare_outputs(out, names, overwrite=args.overwrite, protected=protected)
    report = result.report(geometry_compare=compare, applied=False)
    report.update({
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "generator_version": __version__,
        "detail_dir": str(detail_dir.resolve()),
        "limit": limit,
        "replay_of": replay_ref,
        "raw_response": result.raw_response,
        "raw_response_sha256": _sha(result.raw_response),
        "system_prompt": SYSTEM_PROMPT,
    })
    from onward_generator.dress.classify import groups_document

    atomic_write_json(targets[0], report)
    atomic_write_json(targets[1], groups_document(groups.groups, catalog))
    atomic_write_json(targets[2], {
        "schema": "onward_generator.dress_classify_run",
        "schema_version": "0.1.0",
        "raw_response": result.raw_response,
        "raw_response_sha256": _sha(result.raw_response),
        "provider": result.provenance,
    })
    _print_classify(result, targets[0], compare)
    return 0 if result.valid else 1


def _print_classify(result: ClassifyResult, report_path: Path, compare: dict[str, Any]) -> None:
    prov = result.provenance
    print(f"provider: {prov.get('provider', '?')}" + (f", model {prov.get('model')}" if prov.get("model") else ""))
    print(f"groups: {len(result.groups.groups)}  recommendations: {len(result.recommendations)}")
    review = sum(1 for r in result.recommendations if r.needs_review)
    print(f"needs_review: {review}")
    print(f"geometry_intact: {compare['identical']}  input_files_unchanged: {compare['input_files']['unchanged']}")
    for rec in result.recommendations:
        flag = " review" if rec.needs_review else ""
        print(f"  {rec.surface_id}: {rec.recommended_material_id} ({rec.confidence:.2f}, {rec.source}){flag}")
    for d in result.diagnostics:
        print(f"  {d}")
    print(f"wrote {report_path} (apply disabled)")


def _paths_from_detail(detail_dir: Path) -> dict[str, Path | None]:
    manifest = detail_dir / "scene_detail.json"
    try:
        doc, _ = read_json_document(manifest, max_bytes=MAX_SCENE_BYTES)
    except ConfigLoadError:
        return {"scene": None, "heights": None, "base": None}
    inputs = doc.get("inputs") or {}
    scene = Path((inputs.get("geo_scene") or {}).get("path") or "")
    heights = Path((inputs.get("heights") or {}).get("path") or "")
    base = Path((inputs.get("phase03_base") or {}).get("dir") or "")
    return {
        "scene": scene if scene.is_file() else None,
        "heights": heights if heights.is_file() else None,
        "base": base if base.is_dir() else None,
    }


def _run_style(args: argparse.Namespace) -> int:
    from onward_generator.detail.config import load_detail_config
    from onward_generator.dress.style import style_copy, write_styled
    from onward_generator.generation.config import Material, load_generation_config
    catalog = load_catalog(args.catalog)
    detail_dir = Path(args.detail).expanduser()
    recorded = _paths_from_detail(detail_dir)
    scene = Path(args.scene).expanduser() if args.scene else recorded["scene"]
    heights = Path(args.heights).expanduser() if args.heights else recorded["heights"]
    if scene is None or heights is None:
        raise ValueError("style needs --scene and --heights (not recorded in the detail manifest)")
    before_doc, _ = read_json_document(detail_dir / "scene_detail.json", max_bytes=MAX_SCENE_BYTES)
    before_objects = before_doc.get("objects") or []
    cfg = load_detail_config(args.config)
    gen_cfg = load_generation_config(args.generation_config)
    names = STYLE_FILES
    protected = (
        Path(scene), Path(heights),
        detail_dir / "scene_detail.json",
        detail_dir / "scene_visual.glb",
    )
    targets = prepare_outputs(args.out, names, overwrite=args.overwrite, protected=protected)
    visual, collision, objects, meta = style_copy(detail_dir, scene, heights, gen_cfg, cfg)
    materials = dict(gen_cfg.materials) | dict(cfg.materials) | dict(meta.get("vis_mats") or {})
    materials["collider"] = Material((0.2, 0.8, 0.3, 1.0), 1.0)
    source_manifest = dict(meta["source_manifest"])
    if (detail_dir / "detail_report.json").is_file():
        source_manifest["_report_path"] = str(detail_dir / "detail_report.json")
    paths = {
        "visual": targets[0], "collision": targets[1], "manifest": targets[2],
        "report": targets[3], "atlas": targets[4],
    }
    report = write_styled(
        visual, collision, objects, before_objects, materials, catalog, paths,
        atlas=not args.no_atlas, attribution=meta.get("attribution"),
        seed=int(meta["seed"]), source_manifest=source_manifest,
        source_detail_sha256=meta.get("source_detail_sha256"),
        generated_accessories=meta.get("generated_accessories") or {},
    )
    compare = report["dress"]["geometry"]
    print(
        f"style: trees={TREE_NOTE}  atlas={'on' if not args.no_atlas else 'off'}  "
        f"facades=copied  "
        f"geometry added={len(compare['added_ids'])} removed={len(compare['removed_ids'])} "
        f"changed={len(compare['changed_ids'])}"
    )
    print(f"wrote {targets[0].parent}")
    if _geographic_changed(before_objects, objects):
        print("style: geographic objects changed; this is unexpected", file=sys.stderr)
        return 1
    if report["dress"].get("regenerated_facades"):
        return 1
    emit(materials_command(Path(args.out)))
    return 0


TREE_NOTE = "16-side / 3 variants"


def _geographic_changed(before: Sequence[dict[str, Any]], after: Sequence[dict[str, Any]]) -> bool:
    keep = {"building", "surface", "curb", "landmark_part"}
    b = geometry_fingerprint([o for o in before if o.get("type") in keep])
    a = geometry_fingerprint([o for o in after if o.get("type") in keep])
    return not compare_fingerprints(b, a)["identical"]


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code in (0, None) else 2
    try:
        if args.command == "classify":
            return _run_classify(args)
        return _run_style(args)
    except (ValueError, FileExistsError, CatalogError, SurfaceError, ClassifyError, ProviderError) as exc:
        print(f"dress: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
