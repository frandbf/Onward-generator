"""Onward export package (phase 07.0): the only input the Unity adapter accepts.

The package is a new, immutable directory holding copies of the phase 04 GLB
files and the gameplay layout, the adapter build options and a manifest with
the SHA-256 of every file. The Unity side (``tools/unity/onward_adapter``)
refuses to build if any hash differs. Nothing here talks to Unity or the device.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from onward_generator import __version__
from onward_generator.config.loader import MAX_SCENE_BYTES, ConfigLoadError, read_json_document
from onward_generator.config.validation import validate_config_file
from onward_generator.contracts import is_schema
from onward_generator.export.lightmap import DEFAULT_CLASS_SCALES, estimate, material_scales
from onward_generator.export.thumbnail import render_listing_thumbnail
from onward_generator.generation.gltf import read_glb
from onward_generator.io import atomic_write_bytes, atomic_write_json
from onward_generator.perf.layout import (
    GAMEPLAY_VERSION,
    MIN_SPAWN_SEPARATION_M,
    MODE_CODES,
    SPAWN_SEPARATION_M,
    hunt_spawn_problems,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
EXPORT_CONFIG_DEFAULT = REPO_ROOT / "config" / "export" / "onward.default.json"
EXPORT_CONFIG_SCHEMA = REPO_ROOT / "config" / "export" / "onward.schema.json"
PACKAGE_SCHEMA = "onward_generator.onward_export_package"
PACKAGE_VERSION = "0.1.0"
MANIFEST_FILE = "onward_export.json"
OPTIONS_FILE = "adapter_options.json"
THUMBNAIL_FILE = "thumbnail.png"
CHECKLIST_FILE = "quest_acceptance.md"
LAYOUT_SCHEMA = "onward_generator.perf_layout"
GAMEPLAY_SCHEMA = "onward_generator.gameplay_layout"
SDK_SPAWN_POINTS = 8
"""Points in the SDK v1.15 ``SpawnSystem`` prefab; the adapter places every one of them."""

PAYLOAD = (("visual", "scene_visual.glb"), ("collision", "scene_collision.glb"), ("layout", "layout.json"))

QUEST_CHECKLIST = (
    "El mapa aparece en el flujo autorizado de selección o carga.",
    "El escenario carga sin errores críticos.",
    "El jugador puede desplazarse por las áreas previstas.",
    "Las colisiones impiden atravesar paredes y edificios.",
    "Las puertas, escaleras y conexiones interiores funcionan cuando existan.",
    "No hay huecos críticos en el suelo ni edificios flotantes.",
    "Free Roam funciona.",
    "Uplink (segundo modo exigido por la guía) funciona.",
    "Hunt: un bot recorre una calle y no atraviesa edificios.",
    "Se oye el ambiente Suburban Day y cambia la reverberación bajo techo.",
    "El disparo contra un muro responde como hormigón.",
    "Se ve un flyover sobre la plaza.",
    "La ficha del mapa dice entorno urbano, no desierto.",
    "La iluminación y los materiales son correctos en el dispositivo.",
    "La tasa de fotogramas se mantiene en 72 fps (medir con OVR Metrics Tool, docs/09-perf.md).",
    "La memoria y el tiempo de carga quedan medidos y documentados.",
)


class ExportError(ValueError):
    """The inputs cannot form a valid export package; ``problems`` lists every reason."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("\n".join(problems))
        self.problems = problems


@dataclass(frozen=True, slots=True)
class PackageResult:
    directory: Path
    manifest: dict[str, Any]
    warnings: tuple[str, ...]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@cache
def _config_validator() -> Draft202012Validator:
    schema = json.loads(EXPORT_CONFIG_SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def load_export_config(path: str | Path | None = None) -> tuple[dict[str, Any], Path]:
    try:
        doc, resolved = read_json_document(path or EXPORT_CONFIG_DEFAULT)
    except ConfigLoadError as exc:
        raise ExportError([f"export config: {exc}"]) from exc
    errors = sorted(_config_validator().iter_errors(doc), key=lambda e: list(map(str, e.absolute_path)))
    if errors:
        raise ExportError([f"export config {resolved}: /{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in errors])
    return doc, resolved


def _check_layout(doc: Mapping[str, Any]) -> list[str]:
    problems = []
    if not is_schema(doc.get("schema"), LAYOUT_SCHEMA) and not is_schema(doc.get("schema"), GAMEPLAY_SCHEMA):
        return [f"layout: schema must be {LAYOUT_SCHEMA!r} or {GAMEPLAY_SCHEMA!r}, got {doc.get('schema')!r}"]

    def point(value: Any) -> bool:
        return isinstance(value, list) and len(value) >= 2 and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                                                                 for v in value)

    for side in ("attackers", "defenders"):
        block = doc.get(side) or {}
        pts = block.get("points_m") or []
        if len(pts) < SDK_SPAWN_POINTS:
            problems.append(f"layout: {side} has {len(pts)} spawn points; the SDK SpawnSystem prefab needs {SDK_SPAWN_POINTS}")
        if not point(block.get("centre_m")):
            problems.append(f"layout: {side}.centre_m must be [x, y]")
    for key in ("uplink_m", "map_quad_m"):
        if not point(doc.get(key)):
            problems.append(f"layout: {key} must be [x, y] (Uplink objective and map quad are required by the SDK)")
    size = doc.get("level_size_m")
    if not isinstance(size, (int, float)) or isinstance(size, bool) or size <= 0:
        problems.append("layout: level_size_m must be a positive number")
    dv = doc.get("damage_volume") or {}
    if not (point(dv.get("centre_m")) and isinstance(dv.get("size_m"), list) and len(dv["size_m"]) == 3):
        problems.append("layout: damage_volume needs centre_m [x, y] and size_m [x, y, z]")
    if is_schema(doc.get("schema"), GAMEPLAY_SCHEMA):
        ai = doc.get("ai_m") or []
        ammo = doc.get("ammo_m") or []
        routes = doc.get("routes") or []
        if len(ai) < 32:
            problems.append(f"layout: Operations Hunt needs 32 AI points, got {len(ai)}")
        if len(ammo) < 4:
            problems.append(f"layout: Operations Hunt needs 4 ammo boxes, got {len(ammo)}")
        if not routes or any(len(route.get("points") or []) < 2 for route in routes):
            problems.append("layout: each Hunt route needs at least 2 points")
        else:
            separation = doc.get("ai_separation_m", SPAWN_SEPARATION_M)
            if (
                not isinstance(separation, (int, float))
                or isinstance(separation, bool)
                or separation < MIN_SPAWN_SEPARATION_M
                or separation > SPAWN_SEPARATION_M
            ):
                problems.append(
                    f"layout: ai_separation_m must be from {MIN_SPAWN_SEPARATION_M:g} m to {SPAWN_SEPARATION_M:g} m"
                )
            else:
                problems.extend(hunt_spawn_problems(ai, routes, separation_m=float(separation)))
        if doc.get("audio_global") != "Suburban Day":
            problems.append("layout: audio_global must be the SDK clip Suburban Day")
        if doc.get("environment") != "Urban" or doc.get("time_of_day") != "Day" or doc.get("profile") != "UrbanDay1":
            problems.append("layout: environment must be Urban / Day / UrbanDay1")
        if doc.get("far_clip_m") != 1000 or doc.get("flyover") != 1:
            problems.append("layout: far_clip_m must be 1000 and flyover must be 1")
        if doc.get("schema_version") == GAMEPLAY_VERSION:
            problems.extend(_check_match_modes(doc, point))
    return problems


def _check_match_modes(doc: Mapping[str, Any], point) -> list[str]:
    """Uplink, Assault, Escort, Hunt, Evac and Retrieval, plus the required Free Roam."""
    problems = []
    if doc.get("modes") != MODE_CODES:
        problems.append(f"layout: modes must be {MODE_CODES}")

    def block_ok(name: str) -> None:
        block = doc.get(name) or {}
        pts = block.get("points_m") or []
        if len(pts) < SDK_SPAWN_POINTS or not point(block.get("centre_m")):
            problems.append(f"layout: {name} needs a centre and {SDK_SPAWN_POINTS} spawn points")

    block_ok("attackers_b")
    block_ok("defenders_b")
    if not point(doc.get("escort_a_m")) or not point(doc.get("escort_b_m")):
        problems.append("layout: Escort needs escort_a_m and escort_b_m")
    if not point(doc.get("evac_m")):
        problems.append("layout: Evac needs evac_m")
    drives = doc.get("harddrives_m") or []
    if len(drives) < 3:
        problems.append(f"layout: Retrieval needs 3 hard-drive points, got {len(drives)}")
    return problems


def _check_detail(detail_dir: Path) -> tuple[list[str], dict[str, Any], dict[str, Any]]:
    problems: list[str] = []
    manifest: dict[str, Any] = {}
    report: dict[str, Any] = {}
    for name, target in (("scene_detail.json", manifest), ("detail_report.json", report)):
        try:
            target.update(read_json_document(detail_dir / name, max_bytes=MAX_SCENE_BYTES)[0])
        except ConfigLoadError as exc:
            problems.append(f"detail: {exc}")
    if problems:
        return problems, manifest, report
    checks = report.get("phase_04_checks") or {}
    if checks.get("automatic_checks_passed") is not True or checks.get("critical_errors") != 0:
        problems.append(f"detail: phase 04 automatic checks did not pass ({checks}); nothing is exported from a failed scene")
    files = manifest.get("files") or {}
    for role, name in (("visual", "scene_visual.glb"), ("collision", "scene_collision.glb")):
        if files.get(role) != name:
            problems.append(f"detail: scene_detail.json files.{role} is {files.get(role)!r}, expected {name!r}")
        elif not (detail_dir / name).is_file():
            problems.append(f"detail: {detail_dir / name} not found")
    if not manifest.get("attribution"):
        problems.append("detail: scene_detail.json has no attribution; OSM data cannot be exported without it (ODbL)")
    return problems, manifest, report


def _adapter_options(build: Mapping[str, Any], visual: bytes) -> dict[str, Any]:
    """The build block for the adapter, with the class scales resolved to one scale per visual material."""
    options = {"variant": "", **build}
    class_scales = {**DEFAULT_CLASS_SCALES, **options.pop("lightmap_class_scales", {})}
    doc, _blob = read_glb(visual)
    names = [str(m.get("name")) for m in doc.get("materials") or []]
    options["lightmap_scales"] = material_scales(names, class_scales)
    return options


def _description(config: Mapping[str, Any], attribution: list[Mapping[str, Any]]) -> str:
    credits = "; ".join(f"{a['text']} ({a['license']})" for a in attribution)
    return f"{config['map']['description']} Data: {credits}."


def build_package(project: str | Path, detail_dir: str | Path, layout: str | Path, out_dir: str | Path, *,
                  export_config: str | Path | None = None, now: datetime | None = None) -> PackageResult:
    """Validate every input, then write the package into ``out_dir``, which must not exist yet."""
    detail = Path(detail_dir).expanduser().resolve()
    out = Path(out_dir).expanduser().resolve()
    problems: list[str] = []
    warnings: list[str] = []

    if out.exists():
        raise FileExistsError(f"{out} already exists; packages are never overwritten, choose a new --out")

    result = validate_config_file(project)
    for d in result.diagnostics:
        if d.severity == "error":
            problems.append(f"project: {d.path}: {d.message} [{d.code}]")
        elif d.severity == "warning":
            warnings.append(f"project: {d.path}: {d.message} [{d.code}]")
    project_doc: dict[str, Any] = {}
    if result.valid:
        project_doc = read_json_document(result.path)[0]
        if project_doc["export"] != {"adapter": "onward", "enabled": True}:
            problems.append("project: /export must be {\"adapter\": \"onward\", \"enabled\": true} to export")

    cfg, cfg_path = load_export_config(export_config)
    detail_problems, detail_manifest, detail_report = _check_detail(detail)
    problems += detail_problems
    layout_doc: dict[str, Any] = {}
    try:
        layout_doc, layout_path = read_json_document(layout)
        problems += _check_layout(layout_doc)
    except ConfigLoadError as exc:
        problems.append(f"layout: {exc}")
        layout_path = Path(layout)
    if is_schema(layout_doc.get("schema"), GAMEPLAY_SCHEMA):
        wall_ids = [o.get("id") for o in (detail_manifest.get("objects") or []) if isinstance(o, dict)]
        if "envelope/perimeter_wall" not in wall_ids:
            problems.append(
                "detail: a playable export has no envelope/perimeter_wall; "
                "run bounds on the dressed scene so the map edge is a closed wall"
            )
    if problems:
        raise ExportError(problems)

    sources = {"visual": detail / "scene_visual.glb", "collision": detail / "scene_collision.glb", "layout": layout_path}
    source_sha = {role: sha256_file(p) for role, p in sources.items()}
    visual_bytes = sources["visual"].read_bytes()
    thumbnail = render_listing_thumbnail(visual_bytes, str(cfg["map"]["title"]))
    thumbnail_sha = hashlib.sha256(thumbnail).hexdigest()
    options = _adapter_options(cfg["build"], visual_bytes)
    content_id = hashlib.sha256(json.dumps(
        {"files": source_sha, "thumbnail": thumbnail_sha, "options": options, "map": cfg["map"]},
        sort_keys=True,
    ).encode()).hexdigest()
    package_id = f"{cfg['map']['id']}_{content_id[:10]}"
    options["variant"] = package_id

    out.mkdir(parents=True)
    try:
        files = []
        for role, name in PAYLOAD:
            shutil.copyfile(sources[role], out / name)
            digest = sha256_file(out / name)
            if digest != source_sha[role]:
                raise ExportError([f"{name}: copy does not match its source (sha256 {digest} != {source_sha[role]})"])
            files.append({"role": role, "path": name, "sha256": digest, "bytes": (out / name).stat().st_size,
                          "source": str(sources[role])})
        atomic_write_bytes(out / THUMBNAIL_FILE, thumbnail)
        files.append({"role": "thumbnail", "path": THUMBNAIL_FILE, "sha256": thumbnail_sha,
                      "bytes": (out / THUMBNAIL_FILE).stat().st_size, "source": "generated"})
        atomic_write_json(out / OPTIONS_FILE, options)
        files.append({"role": "options", "path": OPTIONS_FILE, "sha256": sha256_file(out / OPTIONS_FILE),
                      "bytes": (out / OPTIONS_FILE).stat().st_size, "source": str(cfg_path)})

        attribution = list(detail_manifest["attribution"])
        manifest = {
            "schema": PACKAGE_SCHEMA,
            "schema_version": PACKAGE_VERSION,
            "package_id": package_id,
            "created_at": (now or datetime.now(UTC)).isoformat(timespec="seconds"),
            "generator_version": __version__,
            "adapter": {**cfg["adapter"], "device_verified": False},
            "target": cfg["target"],
            "map": {"id": cfg["map"]["id"], "title": cfg["map"]["title"],
                    "description": _description(cfg, attribution)},
            "attribution": attribution,
            "fidelity": detail_manifest.get("fidelity"),
            "files": files,
            "inputs": {
                "project": {"path": str(result.path), "sha256": sha256_file(result.path)},
                "export_config": {"path": str(cfg_path), "sha256": sha256_file(cfg_path)},
                "detail_manifest": {"path": str(detail / "scene_detail.json"), "sha256": sha256_file(detail / "scene_detail.json")},
                "detail_report": {"path": str(detail / "detail_report.json"), "sha256": sha256_file(detail / "detail_report.json"),
                                  "phase_04_checks": detail_report.get("phase_04_checks")},
                "layout_purpose": layout_doc.get("purpose"),
            },
            "lightmap_estimate": estimate(
                visual_bytes, texels_per_m=float(options["lightmap_texels_per_m"]),
                max_size=int(options["lightmap_max_size"]), cell_m=float(options["combine_cell_m"]),
                scales=options["lightmap_scales"],
            ),
            "warnings": warnings,
        }
        atomic_write_json(out / MANIFEST_FILE, manifest)
        atomic_write_bytes(out / CHECKLIST_FILE, checklist(manifest).encode("utf-8"))
    except BaseException:
        shutil.rmtree(out, ignore_errors=True)
        raise
    return PackageResult(out, manifest, tuple(warnings))


def checklist(manifest: Mapping[str, Any]) -> str:
    lines = [
        f"# Prueba de aceptación en Quest — {manifest['map']['title']}",
        "",
        f"Paquete `{manifest['package_id']}`, adaptador {manifest['adapter']['name']} {manifest['adapter']['version']}, "
        f"Unity {manifest['target']['unity']}, SDK {manifest['target']['sdk']}.",
        "Anota para cada punto: sí / no / no probado, y qué se observó. Ver docs/12-export.md (sección «Export package»).",
        f"`device_verified` sigue en false hasta que este `{CHECKLIST_FILE}` esté completo en el visor.",
        "",
        "| # | Comprobación | Resultado | Observación |",
        "| --- | --- | --- | --- |",
    ]
    lines += [f"| {i} | {item} | | |" for i, item in enumerate(QUEST_CHECKLIST, 1)]
    lines += ["", "MD5 del `.content3` probado: (ver `build/content3.md5`)", ""]
    return "\n".join(lines)


def verify_package(directory: str | Path) -> list[str]:
    """Re-hash every file listed in the manifest; returns the problems found (empty = intact)."""
    root = Path(directory).expanduser().resolve()
    try:
        manifest = read_json_document(root / MANIFEST_FILE)[0]
    except ConfigLoadError as exc:
        return [str(exc)]
    if not is_schema(manifest.get("schema"), PACKAGE_SCHEMA) or manifest.get("schema_version") != PACKAGE_VERSION:
        return [f"{root / MANIFEST_FILE} is not a {PACKAGE_SCHEMA} {PACKAGE_VERSION} manifest"]
    problems = []
    for entry in manifest.get("files", []):
        path = root / entry["path"]
        if Path(entry["path"]).name != entry["path"]:
            problems.append(f"{entry['path']}: package paths must be plain file names")
        elif not path.is_file():
            problems.append(f"{entry['path']}: missing")
        elif sha256_file(path) != entry["sha256"]:
            problems.append(f"{entry['path']}: sha256 differs from the manifest")
    return problems
