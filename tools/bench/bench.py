"""Fixed-seed benchmark of the reference maps: ``bench_report.json``, a contact sheet and a comparison table.

    python tools/bench/bench.py [--host-os S] [--host-gpu S] [run] [--only ID ...] [--offline] [--against REPORT] [--gameplay-report]
    python tools/bench/bench.py report RUN_DIR
    python tools/bench/bench.py compare OLD NEW
    python tools/bench/bench.py check A B

``task bench`` runs ``run``. Each reference map (``REFERENCE_MAPS``, plan 2026-11-07 section 1) is created
with the same seed in ``output/bench/inputs/<id>/``, then copied into a new run folder,
``output/bench/runs/<UTC stamp>/<id>/``, where every Python stage of ``onward-generator run`` runs through
``export package``. Unity, the headset and ``maps/`` are never touched.

The downloads (``osm`` and the source plugins' fetch stages) are frozen: after a networked run their raw
files are copied back into ``inputs/<id>/data/raw`` (a cached file is never overwritten), and a later run
skips every download whose file is cached. ``--offline`` refuses to start while a cache is missing.

``bench_report.json`` keeps everything that cannot be reproduced (host, stage seconds, Unity seconds) under
``timings``. Two runs on the same frozen inputs give the same file apart from ``timings``; ``check`` says
whether that holds and names the JSON paths that differ. ``report RUN_DIR`` reads the packages again, so
the Unity timings appear after ``task unity`` has built a bench package on the host. Editor and Python
figures only, not device measurements.

Exit codes: 0 done, 1 the bench or the comparison failed, 2 usage.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import re
import shutil
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from types import ModuleType
from typing import Any

from PIL import Image, ImageDraw, ImageFont
from shapely import box

from onward_generator.export.package import MANIFEST_FILE, THUMBNAIL_FILE
from onward_generator.export.thumbnail import THUMBNAIL_HEIGHT, THUMBNAIL_WIDTH
from onward_generator.gameplay.headline import REPORT_FILE as GAMEPLAY_FILE
from onward_generator.maps.workspace import MapError, create_map, load_map, store_gameplay_report, store_sources
from onward_generator.next_step import geo_command
from onward_generator.osm.acquire import osm_target
from onward_generator.pipeline_run import PipelineError, plan_steps, run_steps, source_plan
from onward_generator.run_log import SETUP_STEPS, RunLog
from onward_generator.sources.plugin import SourceError
from onward_generator.sources.registry import choices
from onward_generator.tiers import CORE, SCENIC, SHARED, tier_summary

SCHEMA = "onward-bench"
SCHEMA_VERSION = 1
SEED = 48152
BENCH_ROOT = Path("output") / "bench"
REPORT_FILE = "bench_report.json"
DIFF_FILE = "bench_diff.md"
CONTACT_SHEET_FILE = "contact_sheet.png"
VISUAL_FILE = "scene_visual.glb"
MISSING = "-"
_RUN_ID = re.compile(r"^[0-9A-Za-z][0-9A-Za-z_.-]{0,63}$")
# The input files that decide a map's output; data/processed is pipeline output and never frozen.
_INPUT_PARTS = ("map.json", "config", "reference", "data/raw")
# A box far past any map radius (1500 m): with no core every node of the map is played (ADR-017).
_WHOLE_MAP = box(-1.0e7, -1.0e7, 1.0e7, 1.0e7)
_SHEET_COLUMNS = 2
_LABEL_H = 28
_BACKGROUND = (24, 26, 28)
_MISSING_TILE = (96, 96, 96)
_LABEL_INK = (240, 238, 232)

Execute = Callable[[Sequence[str]], int]
_COMMANDS = ("run", "report", "compare", "check")
# Global options, before the command; the Taskfile always passes them, whatever the command.
_HOST_FLAGS = ("--host-os", "--host-gpu")


class BenchError(RuntimeError):
    """The bench cannot run, read a package or compare two reports."""


@dataclass(frozen=True, slots=True)
class ReferenceMap:
    """One map of the fixed benchmark. Latitude and longitude are WGS84 degrees, passed only to ``create_map``."""

    id: str
    name: str
    latitude: float
    longitude: float
    radius_m: float
    margin_m: float
    lightmap_texels_per_m: float
    purpose: str
    # Source plugins forced on (True) or off (False) in map.json; a forced-on plugin must cover the centre.
    sources: tuple[tuple[str, bool], ...] = ()

    def parameters(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "radius_m": self.radius_m,
            "margin_m": self.margin_m,
            "lightmap_texels_per_m": self.lightmap_texels_per_m,
            "sources": dict(self.sources),
            "purpose": self.purpose,
        }


REFERENCE_MAPS: tuple[ReferenceMap, ...] = (
    ReferenceMap("bench_murcia", "Bench Murcia", 37.98407, -1.12863, 150.0, 30.0, 10.0, "base case"),
    ReferenceMap("bench_madrid", "Bench Madrid", 40.41690, -3.70350, 300.0, 30.0, 10.0,
                 "largest map that exports, with Catastro", (("es.catastro", True),)),
    ReferenceMap("bench_paris", "Bench Paris", 48.85300, 2.34830, 50.0, 30.0, 40.0,
                 "worst bake: 40 texels per metre, LiDAR HD heights", (("fr.lidar_hd", True),)),
    ReferenceMap("bench_krakow", "Bench Krakow", 50.06140, 19.93660, 150.0, 30.0, 10.0,
                 "outside Spain, a facade style with no atlas (central_european)"),
)


@dataclass(frozen=True, slots=True)
class Prepared:
    """A reference map whose frozen inputs exist, and the seconds ``create_map`` took when it ran now."""

    ref: ReferenceMap
    inputs: Path
    created_s: float | None = None


@dataclass(slots=True)
class MapRun:
    """What one map's run left: its folder, the package and the seconds of every stage, in order."""

    ref: ReferenceMap
    root: Path
    package: Path
    times: list[tuple[str, float]] = field(default_factory=list)
    downloads: frozenset[str] = frozenset()


def _report_timings() -> ModuleType:
    """``tools/unity/report_timings.py``, the one reader of the Unity phase seconds."""
    path = Path(__file__).resolve().parents[1] / "unity" / "report_timings.py"
    spec = importlib.util.spec_from_file_location("onward_report_timings", path)
    if spec is None or spec.loader is None:
        raise BenchError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def reference(map_id: str) -> ReferenceMap:
    for ref in REFERENCE_MAPS:
        if ref.id == map_id:
            return ref
    raise BenchError(f"unknown reference map {map_id!r}. Known: {', '.join(r.id for r in REFERENCE_MAPS)}")


# ---------------------------------------------------------------------------------------------------- inputs


def _flag(argv: Sequence[str], name: str) -> Path:
    try:
        return Path(argv[list(argv).index(name) + 1])
    except (ValueError, IndexError) as exc:
        raise BenchError(f"stage argv has no {name}: {' '.join(argv)}") from exc


def download_targets(root: Path) -> dict[str, Path]:
    """The download stages of the map at ``root`` (``osm`` and every source fetch) and the file each one leaves."""
    targets = {"osm": osm_target(root)}
    scene = _flag(geo_command(root).argv, "--out") / "geo_scene.json"
    try:
        stages = source_plan(root, scene)[0]
    except (PipelineError, SourceError, MapError) as exc:
        raise BenchError(f"{root.name}: {exc}") from exc
    targets |= {stage.name: stage.marker for stage in stages if stage.download}
    return targets


def _present(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def missing_cache(inputs: Path) -> list[str]:
    """The frozen inputs ``--offline`` would need and does not have, as names a person can act on."""
    if not (inputs / "map.json").is_file():
        return ["map.json"]
    return [name for name, path in download_targets(inputs).items() if not _present(path)]


def _check_parameters(ref: ReferenceMap, inputs: Path) -> None:
    document = load_map(inputs)
    stored = (
        float(document["center"]["latitude"]), float(document["center"]["longitude"]),
        float(document["radius_m"]), float(document["margin_m"]), int(document["seed"]),
    )
    if stored != (ref.latitude, ref.longitude, ref.radius_m, ref.margin_m, SEED):
        raise BenchError(f"{inputs} was frozen for other parameters than {ref.id}; delete it to create it again")


def check_sources(ref: ReferenceMap, inputs: Path) -> None:
    """Every plugin the reference map forces on must run for it: covered and not switched off."""
    running = {choice.plugin.id for choice in choices(load_map(inputs)) if choice.runs}
    for plugin_id, enabled in ref.sources:
        if enabled and plugin_id not in running:
            raise BenchError(f"{ref.id}: source {plugin_id} does not run at {ref.latitude}, {ref.longitude}")


def prepare_inputs(ref: ReferenceMap, inputs_root: Path, *, offline: bool) -> Prepared:
    """The frozen inputs of ``ref``: created with ``create_map`` when absent, checked before any download."""
    inputs = inputs_root / ref.id
    created_s: float | None = None
    if not (inputs / "map.json").is_file():
        if offline:
            raise BenchError(f"{ref.id}: --offline, and {inputs} has no frozen inputs; run once with the network")
        started = time.perf_counter()
        try:
            create_map(
                inputs, map_id=ref.id, name=ref.name, latitude=ref.latitude, longitude=ref.longitude,
                radius_m=ref.radius_m, margin_m=ref.margin_m, seed=SEED,
                lightmap_texels_per_m=ref.lightmap_texels_per_m,
            )
            if ref.sources:
                store_sources(inputs, dict(ref.sources))
            check_sources(ref, inputs)
        except BenchError:
            shutil.rmtree(inputs, ignore_errors=True)
            raise
        except (MapError, SourceError) as exc:
            shutil.rmtree(inputs, ignore_errors=True)
            raise BenchError(f"{ref.id}: {exc}") from exc
        created_s = time.perf_counter() - started
    _check_parameters(ref, inputs)
    check_sources(ref, inputs)
    if offline:
        missing = missing_cache(inputs)
        if missing:
            raise BenchError(f"{ref.id}: --offline, and the cache has no {', '.join(missing)}; run once with the network")
    return Prepared(ref, inputs, created_s)


def _input_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for part in _INPUT_PARTS:
        path = root / part
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            files.extend(p for p in path.rglob("*") if p.is_file())
    return sorted(files, key=lambda p: p.relative_to(root).as_posix())


def inputs_sha256(root: Path) -> str:
    """One digest over the relative path and bytes of each input file (map.json, config, reference, data/raw)."""
    digest = hashlib.sha256()
    for path in _input_files(root):
        digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def copy_inputs(inputs: Path, root: Path) -> None:
    """The frozen inputs into a run folder that does not exist yet. ``data/processed`` stays behind."""
    if root.exists():
        raise BenchError(f"{root} already exists; a bench run never writes over another")
    for path in _input_files(inputs):
        target = root / path.relative_to(inputs)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    for folder in ("raw", "processed"):
        (root / "data" / folder).mkdir(parents=True, exist_ok=True)


def freeze_downloads(root: Path, inputs: Path) -> list[str]:
    """Copy the raw files a run downloaded into the frozen inputs. A file already cached keeps its bytes."""
    raw = root / "data" / "raw"
    frozen: list[str] = []
    if not raw.is_dir():
        return frozen
    for path in sorted(p for p in raw.rglob("*") if p.is_file()):
        rel = path.relative_to(root)
        target = inputs / rel
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        frozen.append(rel.as_posix())
    return frozen


# ---------------------------------------------------------------------------------------------------- running


def bench_map(prepared: Prepared, run_dir: Path, execute: Execute, *, gameplay_report: bool = False) -> MapRun:
    """Run every Python stage of one reference map in ``run_dir/<id>``; skip the downloads already cached.

    ``gameplay_report`` switches ``gameplay_report.json`` on in the run copy's ``map.json`` only (ADR-032).
    The frozen inputs and their digest do not change.
    """
    ref = prepared.ref
    root = run_dir / ref.id
    copy_inputs(prepared.inputs, root)
    if gameplay_report:
        store_gameplay_report(root, True)
    log = RunLog()
    if prepared.created_s is not None:
        log.times.append(("map", prepared.created_s))
    targets = download_targets(root)
    if not _present(targets["osm"]):
        _run(ref, root, prepared.inputs, [("osm", ["osm", "download", "--map", str(root)])], execute, log, targets)
    # Planned after osm, as run plans after map create: geo reads the OSM sidecar's retrieved_at.
    try:
        steps = plan_steps(root)
    except (PipelineError, SourceError, MapError) as exc:
        raise BenchError(f"{ref.id}: {exc}") from exc
    steps = [(name, argv) for name, argv in steps if not (name in targets and _present(targets[name]))]
    package = next((_flag(argv, "--out") for name, argv in steps if name == "export"), None)
    if package is None:
        raise BenchError(f"{ref.id}: the plan has no export stage")
    _run(ref, root, prepared.inputs, steps, execute, log, targets)
    return MapRun(ref, root, package, list(log.times), frozenset(targets))


def _run(
    ref: ReferenceMap,
    root: Path,
    inputs: Path,
    steps: Sequence[tuple[str, Sequence[str]]],
    execute: Execute,
    log: RunLog,
    downloads: Mapping[str, Path],
) -> None:
    """Run ``steps``, then freeze what they downloaded. A failed download may have left part of a file: nothing is frozen."""
    code = run_steps(steps, execute=execute, log=log)
    failed = log.times[-1][0] if code != 0 and log.times else None
    if failed not in downloads:
        freeze_downloads(root, inputs)
    if code != 0:
        raise BenchError(f"{ref.id}: stage {failed or steps[0][0]} failed (exit {code})")


# ---------------------------------------------------------------------------------------------------- reading


def _read_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise BenchError(f"{path} not found")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BenchError(f"{path}: not valid JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise BenchError(f"{path} is not a JSON object")
    return data


def _int(block: Mapping[str, Any], key: str, where: str) -> int:
    value = block.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise BenchError(f"{where}: {key} must be an integer, got {value!r}")
    return value


def _object(block: Mapping[str, Any], key: str, where: str) -> dict[str, Any]:
    value = block.get(key)
    if not isinstance(value, dict):
        raise BenchError(f"{where}: {key} must be an object, got {value!r}")
    return value


def _tier_counts(tiers: Mapping[str, Any], where: str) -> tuple[dict[str, int], dict[str, int]]:
    triangles: dict[str, int] = {}
    draws: dict[str, int] = {"total": _int(tiers, "draws", where)}
    for tier in (CORE, SCENIC, SHARED):
        block = _object(tiers, tier, f"{where}: tiers")
        triangles[tier] = _int(block, "visual_triangles", f"{where}: tiers.{tier}")
        draws[tier] = _int(block, "draws", f"{where}: tiers.{tier}")
    triangles["total"] = sum(triangles.values())
    return triangles, draws


def map_metrics(package: Path) -> dict[str, Any]:
    """Triangles, predicted draws and lightmap pages of one export package, from its ``onward_export.json``.

    A map with no playable core has ``tiers: null`` in the manifest; its counts then come from the package's
    ``scene_visual.glb`` through ``tier_summary`` with the whole map as core, and ``tiers_source`` says so.
    ``created_at``, ``package_id`` and every path stay out. A missing value is an error, never a zero.
    """
    path = package / MANIFEST_FILE
    manifest = _read_object(path)
    where = str(path)
    for key in ("tiers", "view_draws", "lightmap_estimate"):
        if key not in manifest:
            raise BenchError(f"{where}: no {key}")
    lightmap = _object(manifest, "lightmap_estimate", where)
    pages = _int(lightmap, "pages_at_least", f"{where}: lightmap_estimate")
    texels = _int(lightmap, "texels", f"{where}: lightmap_estimate")
    view = manifest["view_draws"]
    if view is None:
        views: dict[str, int | None] = {"views": None, "median": None, "p90": None, "max": None}
    elif isinstance(view, dict):
        views = {key: _int(view, key, f"{where}: view_draws") for key in ("views", "median", "p90", "max")}
    else:
        raise BenchError(f"{where}: view_draws must be an object or null, got {view!r}")
    tiers = manifest["tiers"]
    if tiers is None:
        source = "visual_glb_no_core"
        tiers = whole_map_tiers(package, view if isinstance(view, dict) else None)
    elif isinstance(tiers, dict):
        source = "manifest"
    else:
        raise BenchError(f"{where}: tiers must be an object or null, got {tiers!r}")
    triangles, draws = _tier_counts(tiers, where)
    return {
        "generator_version": str(manifest.get("generator_version") or MISSING),
        "visual_triangles": triangles,
        "draws": draws,
        "tiers_source": source,
        "view_draws": views,
        "lightmap": {"pages_at_least": pages, "texels": texels},
    }


def whole_map_tiers(package: Path, view: Mapping[str, Any] | None) -> dict[str, Any]:
    """``tier_summary`` of the package's visual GLB with every node in the core, at the manifest's cell size."""
    visual = package / VISUAL_FILE
    if not visual.is_file():
        raise BenchError(f"{package / MANIFEST_FILE}: tiers is null and {visual} is missing")
    cell = float(view["cell_m"]) if view and isinstance(view.get("cell_m"), int | float) else 100.0
    return tier_summary(visual.read_bytes(), [], _WHOLE_MAP, cell_m=cell)


def unity_timings(package: Path) -> dict[str, Any]:
    """The Unity phase seconds of a bench package: ``measured`` with ``build/scene_report.json``, else ``missing``."""
    report = package / "build" / "scene_report.json"
    if not report.is_file():
        return {"status": "missing"}
    bake = _read_object(report).get("lightmap_bake")
    try:
        seconds = _report_timings().phase_seconds(report)
    except (OSError, ValueError) as exc:
        raise BenchError(f"{report}: {exc}") from exc
    result: dict[str, Any] = {"status": "measured", "seconds": {name: round(value, 1) for name, value in seconds.items()}}
    if isinstance(bake, dict):
        result |= {key: bake[key] for key in ("profile", "lightmapper", "fallback") if key in bake}
    return result


def split_times(times: Iterable[tuple[str, float]], downloads: Iterable[str]) -> dict[str, Any]:
    """Stage seconds in order, with map setup and downloads (``setup_s``) apart from the Python stages (``python_s``).

    ``run`` prints source downloads as Python time; the bench counts them as setup, so ``python_s`` is the
    figure the 30 s offline criterion reads.
    """
    setup_names = set(SETUP_STEPS) | set(downloads)
    stages = [{"stage": name, "seconds": round(seconds, 1)} for name, seconds in times]
    setup = sum(seconds for name, seconds in times if name in setup_names)
    python = sum(seconds for name, seconds in times if name not in setup_names)
    return {"stages": stages, "setup_s": round(float(setup), 1), "python_s": round(float(python), 1)}


def host_info(host_os: str | None, host_gpu: str | None) -> dict[str, Any]:
    """Where the figures were taken. No hostname. The host OS and GPU come from the caller (Taskfile on the host)."""
    def known(value: str | None) -> str:
        return value.strip() if value and value.strip() else "unknown"

    return {
        "host_os": known(host_os),
        "host_gpu": known(host_gpu),
        "system": known(platform.system()),
        "machine": known(platform.machine()),
        "release": known(platform.release()),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count() or 0,
        "in_container": Path("/.dockerenv").exists(),
    }


# ---------------------------------------------------------------------------------------------------- report


GAMEPLAY_KEYS = ("distinct_routes", "max_overlap_pct", "longest_open_sight_m", "cover_per_100m_min_stretch", "contact_diff_pct")


def gameplay_path(root: Path) -> Path:
    return root / "data" / "processed" / "layout" / GAMEPLAY_FILE


def gameplay_metrics(root: Path) -> dict[str, Any] | None:
    """The headline of the map's ``gameplay_report.json`` and its status, or None when the run wrote no report.

    A missing headline key or a value that is not a number or null is an error, never a zero. A null value
    (a report whose spawns have no path) stays null and reads ``-`` in the comparison.
    """
    path = gameplay_path(root)
    if not path.is_file():
        return None
    report = _read_object(path)
    where = str(path)
    headline = _object(report, "headline", where)
    metrics: dict[str, Any] = {}
    for key in GAMEPLAY_KEYS:
        if key not in headline:
            raise BenchError(f"{where}: headline has no {key}")
        value = headline[key]
        if value is not None and (isinstance(value, bool) or not isinstance(value, int | float)):
            raise BenchError(f"{where}: headline.{key} must be a number or null, got {value!r}")
        metrics[key] = value
    status = report.get("status")
    if not isinstance(status, str):
        raise BenchError(f"{where}: status must be a string, got {status!r}")
    return {**metrics, "status": status}


def map_entry(ref: ReferenceMap, package: Path, inputs: Path, root: Path | None = None) -> dict[str, Any]:
    """The comparable figures of one map; ``gameplay`` only when ``root`` holds a gameplay report."""
    entry = {"reference": ref.parameters(), "inputs_sha256": inputs_sha256(inputs), **map_metrics(package)}
    gameplay = gameplay_metrics(root) if root is not None else None
    if gameplay is not None:
        entry["gameplay"] = gameplay
    return entry


def build_report(
    entries: Mapping[str, Mapping[str, Any]],
    map_timings: Mapping[str, Mapping[str, Any]],
    host: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "seed": SEED,
        "maps": {key: dict(value) for key, value in entries.items()},
        "contact_sheet": CONTACT_SHEET_FILE,
        "timings": {"host": dict(host), "maps": {key: dict(value) for key, value in map_timings.items()}},
    }


def report_text(report: Mapping[str, Any]) -> str:
    """Canonical JSON: sorted keys, indent 2, UTF-8 as is, one trailing newline."""
    return json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def write_report(report: Mapping[str, Any], path: Path) -> None:
    path.write_text(report_text(report), encoding="utf-8")


def strip_timings(report: Mapping[str, Any]) -> dict[str, Any]:
    """The comparable body of a report: everything but ``timings``."""
    return {key: value for key, value in report.items() if key != "timings"}


def load_report(path: Path) -> dict[str, Any]:
    """A bench report from a file or a run folder, refused when it is not schema ``onward-bench`` v1."""
    target = path / REPORT_FILE if path.is_dir() else path
    report = _read_object(target)
    if report.get("schema") != SCHEMA or report.get("schema_version") != SCHEMA_VERSION:
        raise BenchError(f"{target}: not a {SCHEMA} v{SCHEMA_VERSION} report "
                         f"(schema {report.get('schema')!r}, version {report.get('schema_version')!r})")
    if not isinstance(report.get("maps"), dict):
        raise BenchError(f"{target}: maps must be an object")
    return report


def contact_sheet(tiles: Sequence[tuple[str, Path]], out: Path) -> None:
    """Every package thumbnail on a fixed grid, two columns, each under its map id. A missing one is a grey tile."""
    rows = max(1, -(-len(tiles) // _SHEET_COLUMNS))
    cell_h = THUMBNAIL_HEIGHT + _LABEL_H
    sheet = Image.new("RGB", (THUMBNAIL_WIDTH * _SHEET_COLUMNS, cell_h * rows), _BACKGROUND)
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    for index, (label, thumbnail) in enumerate(tiles):
        x = (index % _SHEET_COLUMNS) * THUMBNAIL_WIDTH
        y = (index // _SHEET_COLUMNS) * cell_h
        text = label
        if thumbnail.is_file():
            with Image.open(thumbnail) as image:
                tile = image.convert("RGB")
            if tile.size != (THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT):
                tile = tile.resize((THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT), Image.Resampling.BILINEAR)
        else:
            tile = Image.new("RGB", (THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT), _MISSING_TILE)
            text = f"{label} (missing)"
        sheet.paste(tile, (x, y + _LABEL_H))
        draw.text((x + 8, y + 8), text, fill=_LABEL_INK, font=font)
    buffer = BytesIO()
    sheet.save(buffer, format="PNG")
    out.write_bytes(buffer.getvalue())


# ---------------------------------------------------------------------------------------------------- comparison


def previous_report(bench_root: Path, current: Path) -> Path | None:
    """The newest run before ``current`` (run ids sort by time) that wrote a report."""
    runs = bench_root / "runs"
    if not runs.is_dir():
        return None
    earlier = sorted(p for p in runs.iterdir() if p.is_dir() and p.name < current.name and (p / REPORT_FILE).is_file())
    return earlier[-1] / REPORT_FILE if earlier else None


def _get(block: Any, *keys: str) -> Any:
    for key in keys:
        if not isinstance(block, Mapping):
            return None
        block = block.get(key)
    return block


_ROWS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("triangles total", "maps", ("visual_triangles", "total")),
    ("triangles core", "maps", ("visual_triangles", CORE)),
    ("triangles scenic", "maps", ("visual_triangles", SCENIC)),
    ("draws total", "maps", ("draws", "total")),
    ("draws core", "maps", ("draws", CORE)),
    ("draws scenic", "maps", ("draws", SCENIC)),
    ("view draws median", "maps", ("view_draws", "median")),
    ("view draws p90", "maps", ("view_draws", "p90")),
    ("view draws max", "maps", ("view_draws", "max")),
    ("lightmap pages", "maps", ("lightmap", "pages_at_least")),
    ("distinct routes", "maps", ("gameplay", "distinct_routes")),
    ("max route overlap %", "maps", ("gameplay", "max_overlap_pct")),
    ("longest open sight m", "maps", ("gameplay", "longest_open_sight_m")),
    ("cover per 100 m (min stretch)", "maps", ("gameplay", "cover_per_100m_min_stretch")),
    ("contact diff %", "maps", ("gameplay", "contact_diff_pct")),
    ("python s", "timings", ("python_s",)),
    ("setup s", "timings", ("setup_s",)),
    ("unity bake s", "timings", ("unity", "seconds", "scene.bake")),
)


def _value(report: Mapping[str, Any] | None, map_id: str, where: str, keys: tuple[str, ...]) -> Any:
    if report is None:
        return None
    block = _get(report, "maps", map_id) if where == "maps" else _get(report, "timings", "maps", map_id)
    value = _get(block, *keys)
    return value if isinstance(value, int | float) and not isinstance(value, bool) else None


def _cell(value: Any) -> str:
    if value is None:
        return MISSING
    return str(value) if isinstance(value, int) else f"{value:.1f}"


def _delta(old: Any, new: Any) -> str:
    if old is None or new is None:
        return MISSING
    change = new - old
    if isinstance(change, int):
        return f"{change:+d}" if change else "0"
    return f"{change:+.1f}" if round(change, 1) else "0.0"


def compare(old: Mapping[str, Any], new: Mapping[str, Any], *, old_name: str, new_name: str) -> str:
    """The Markdown table for a pull request: per map and metric, old, new and the signed change."""
    old_maps, new_maps = old.get("maps") or {}, new.get("maps") or {}
    lines = [f"# Bench: {old_name} -> {new_name}", ""]
    old_host, new_host = _get(old, "timings", "host") or {}, _get(new, "timings", "host") or {}
    keys = ("host_os", "host_gpu", "machine", "in_container")
    if any(old_host.get(key) != new_host.get(key) for key in keys):
        def describe(host: Mapping[str, Any]) -> str:
            return ", ".join(f"{key} {host.get(key, MISSING)}" for key in keys)
        lines += [f"**Host changed**: the seconds are not comparable. Old: {describe(old_host)}. New: {describe(new_host)}.", ""]
    ids = sorted(set(old_maps) | set(new_maps))
    added = [map_id for map_id in ids if map_id not in old_maps]
    removed = [map_id for map_id in ids if map_id not in new_maps]
    lines += ["| map | metric | old | new | delta |", "|---|---|---:|---:|---:|"]
    for map_id in ids:
        if map_id in added:
            lines.append(f"| {map_id} | added | - | - | - |")
        if map_id in removed:
            lines.append(f"| {map_id} | removed | - | - | - |")
        for label, where, path in _ROWS:
            before = _value(old if map_id in old_maps else None, map_id, where, path)
            after = _value(new if map_id in new_maps else None, map_id, where, path)
            lines.append(f"| {map_id} | {label} | {_cell(before)} | {_cell(after)} | {_delta(before, after)} |")
    return "\n".join(lines) + "\n"


def differences(a: Any, b: Any, path: str = "") -> list[str]:
    """The JSON paths where ``a`` and ``b`` differ, in sorted order."""
    if isinstance(a, dict) and isinstance(b, dict):
        found: list[str] = []
        for key in sorted(set(a) | set(b)):
            here = f"{path}.{key}" if path else str(key)
            if key not in a or key not in b:
                found.append(here)
            else:
                found.extend(differences(a[key], b[key], here))
        return found
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [item for index, (x, y) in enumerate(zip(a, b)) for item in differences(x, y, f"{path}[{index}]")]
    if type(a) is not type(b) or a != b:
        return [path or "$"]
    return []


# ---------------------------------------------------------------------------------------------------- commands


def _package_of(run_dir: Path, map_id: str) -> Path:
    """The package a bench run exported: a run folder is fresh, so it is always ``pkg_01``."""
    return run_dir / map_id / "data" / "processed" / "export" / "pkg_01"


def _sheet(run_dir: Path, ids: Sequence[str]) -> None:
    contact_sheet([(map_id, _package_of(run_dir, map_id) / THUMBNAIL_FILE) for map_id in ids], run_dir / CONTACT_SHEET_FILE)


def _compare_and_write(old_path: Path | None, new_path: Path) -> None:
    if old_path is None:
        print("bench: no previous run to compare with")
        return
    old = load_report(old_path)
    new = load_report(new_path)
    text = compare(old, new, old_name=_run_name(old_path), new_name=_run_name(new_path))
    target = (new_path if new_path.is_dir() else new_path.parent) / DIFF_FILE
    target.write_text(text, encoding="utf-8")
    print(text, end="")


def _run_name(path: Path) -> str:
    return path.name if path.is_dir() else path.parent.name


def cmd_run(args: argparse.Namespace, execute: Execute) -> int:
    root: Path = args.root
    run_id = args.run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    if not _RUN_ID.fullmatch(run_id):
        raise BenchError(f"run id {run_id!r} must be letters, digits, '.', '_' or '-'")
    run_dir = root / "runs" / run_id
    if run_dir.exists():
        raise BenchError(f"{run_dir} already exists; a bench run never writes over another")
    refs = [reference(map_id) for map_id in args.only] if args.only else list(REFERENCE_MAPS)
    prepared = [prepare_inputs(ref, root / "inputs", offline=args.offline) for ref in refs]
    run_dir.mkdir(parents=True)
    entries: dict[str, dict[str, Any]] = {}
    map_timings: dict[str, dict[str, Any]] = {}
    for item in prepared:
        print(f"bench: {item.ref.id}", flush=True)
        ran = bench_map(item, run_dir, execute, gameplay_report=args.gameplay_report)
        entries[item.ref.id] = map_entry(item.ref, ran.package, item.inputs, ran.root)
        map_timings[item.ref.id] = {**split_times(ran.times, ran.downloads), "unity": unity_timings(ran.package)}
    _sheet(run_dir, list(entries))
    report = build_report(entries, map_timings, host_info(args.host_os, args.host_gpu))
    write_report(report, run_dir / REPORT_FILE)
    print(f"bench: wrote {run_dir / REPORT_FILE}")
    _compare_and_write(args.against or previous_report(root, run_dir), run_dir / REPORT_FILE)
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    """Read the packages of a finished run again: metrics and Unity timings. Stage seconds stay as recorded."""
    run_dir: Path = args.run_dir
    report = load_report(run_dir)
    for map_id, entry in report["maps"].items():
        package = _package_of(run_dir, map_id)
        entry |= map_metrics(package)
        gameplay = gameplay_metrics(run_dir / map_id)
        if gameplay is not None:
            entry["gameplay"] = gameplay
        timing = report.setdefault("timings", {}).setdefault("maps", {}).setdefault(map_id, {})
        timing["unity"] = unity_timings(package)
    write_report(report, run_dir / REPORT_FILE)
    print(f"bench: wrote {run_dir / REPORT_FILE}")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    _compare_and_write(args.old, args.new)
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    found = differences(strip_timings(load_report(args.a)), strip_timings(load_report(args.b)))
    if found:
        print("bench: the reports differ outside timings at " + ", ".join(found))
        return 1
    print("bench: the reports are the same apart from timings")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bench.py", description="Fixed-seed benchmark of the reference maps.")
    parser.add_argument("--host-os", default=None, help="the host operating system (the Taskfile passes uname -srm)")
    parser.add_argument("--host-gpu", default=None, help="the host GPU (the Taskfile passes it on macOS)")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="generate the reference maps and write bench_report.json")
    run.add_argument("--only", nargs="+", choices=[ref.id for ref in REFERENCE_MAPS], help="run only these maps")
    run.add_argument("--offline", action="store_true", help="refuse to start when a frozen download is missing")
    run.add_argument("--against", type=Path, help="compare with this report or run folder instead of the previous run")
    run.add_argument("--root", type=Path, default=BENCH_ROOT, help="bench folder (default output/bench)")
    run.add_argument("--run-id", default=None, help="run folder name (default: the UTC time, YYYYMMDDTHHMMSSZ)")
    run.add_argument("--gameplay-report", action="store_true",
                     help="also write gameplay_report.json in each run copy (ADR-032) and add its headline to the report")
    report = sub.add_parser("report", help="read a run's packages again, for the Unity timings")
    report.add_argument("run_dir", type=Path)
    comparison = sub.add_parser("compare", help="write bench_diff.md beside NEW and print it")
    comparison.add_argument("old", type=Path)
    comparison.add_argument("new", type=Path)
    check = sub.add_parser("check", help="exit 1 when two reports differ outside timings")
    check.add_argument("a", type=Path)
    check.add_argument("b", type=Path)
    return parser


def _execute_cli(argv: Sequence[str]) -> int:
    from onward_generator.cli import main

    return main(list(argv))


def main(argv: Sequence[str] | None = None, *, execute: Execute | None = None) -> int:
    rest = list(sys.argv[1:] if argv is None else argv)
    host: list[str] = []
    while rest and rest[0].split("=", 1)[0] in _HOST_FLAGS:
        width = 1 if "=" in rest[0] else 2
        host, rest = host + rest[:width], rest[width:]
    if not rest or (rest[0] not in _COMMANDS and rest[0] not in ("-h", "--help")):
        rest.insert(0, "run")
    args_list = host + rest
    try:
        args = build_parser().parse_args(args_list)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2
    try:
        if args.command == "run":
            return cmd_run(args, execute or _execute_cli)
        if args.command == "report":
            return cmd_report(args)
        if args.command == "compare":
            return cmd_compare(args)
        return cmd_check(args)
    except (BenchError, OSError) as error:
        print(f"bench: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
