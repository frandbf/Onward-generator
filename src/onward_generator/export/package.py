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
from shapely import box

from onward_generator import __version__
from onward_generator.config.loader import MAX_SCENE_BYTES, ConfigLoadError, read_json_document
from onward_generator.config.validation import validate_config_file
from onward_generator.contracts import is_schema
from onward_generator.export.atmosphere import NEUTRAL, STREETS, AtmosphereError, check_atmosphere, lighting, load_presets
from onward_generator.export.info3 import fit_listing_description
from onward_generator.export.lightmap import (
    DEFAULT_CLASS_SCALES,
    LightmapBudgetError,
    estimate,
    lightmap_areas,
    material_scales,
    page_budget_density,
    summarise,
)
from onward_generator.export.normal_map import (
    DEFAULT_STRENGTH,
    LIT_NORMAL_FILE,
    LIT_NORMAL_ROLE,
    NormalMapError,
    albedo_png,
    astc6x6_bytes,
    decode_rgb,
    encode_png,
    normal_map,
)
from onward_generator.export.sun import street_sun
from onward_generator.export.thumbnail import render_listing_thumbnail
from onward_generator.generation.gltf import read_glb
from onward_generator.io import atomic_write_bytes, atomic_write_json, sha256_file
from onward_generator.perf.climbs import LADDERS_MAX, LIFTS_MAX
from onward_generator.perf.layout import (
    GAMEPLAY_VERSION,
    MIN_SPAWN_SEPARATION_M,
    MODE_CODES,
    SPAWN_SEPARATION_M,
    hunt_spawn_problems,
)
from onward_generator.tiers import core_of, tier_summary, view_draws

REPO_ROOT = Path(__file__).resolve().parents[3]
EXPORT_CONFIG_DEFAULT = REPO_ROOT / "config" / "export" / "onward.default.json"
EXPORT_CONFIG_SCHEMA = REPO_ROOT / "config" / "export" / "onward.schema.json"
PACKAGE_SCHEMA = "onward_generator.onward_export_package"
PACKAGE_VERSION = "0.1.0"
MANIFEST_FILE = "onward_export.json"
OPTIONS_FILE = "adapter_options.json"
THUMBNAIL_FILE = "thumbnail.png"
CHECKLIST_FILE = "quest_acceptance.md"
# The adapter's combine cell (ADR-005), used to predict draws when the card leaves combining off.
DEFAULT_CELL_M = 100.0
LAYOUT_SCHEMA = "onward_generator.perf_layout"
GAMEPLAY_SCHEMA = "onward_generator.gameplay_layout"
SDK_SPAWN_POINTS = 8
"""Points in the SDK v1.15 ``SpawnSystem`` prefab; the adapter places every one of them."""

PAYLOAD = (("visual", "scene_visual.glb"), ("collision", "scene_collision.glb"), ("layout", "layout.json"))

# Shop display windows (docs/07-detail.md, *Shop interiors*). The pane has no collider: the walk-through is a known risk.
SHOP_GLASS_CHECKLIST = (
    "Escaparates de tienda: vidrio sin marco, visible desde la calle y desde dentro (anotar qué material de vidrio registró el adaptador).",
    "Un disparo atraviesa el escaparate de una tienda sin impacto en el vidrio.",
    "Riesgo abierto: el escaparate no tiene colisión sobre el zócalo de 0,25 m. Anotar si el jugador entra a la tienda por él.",
    "El nombre de la tienda en el escaparate se lee en letras negras desde la calle y no tapa el interior.",
    "Los interiores de tienda muestran suelo y paredes con textura.",
)
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
    *SHOP_GLASS_CHECKLIST,
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


@cache
def _config_validator() -> Draft202012Validator:
    schema = json.loads(EXPORT_CONFIG_SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


ENVIRONMENT_PROFILES = frozenset({("Day", "UrbanDay1"), ("Night", "UrbanNight1")})
"""The SDK v1.15 ``ETimeOfDay`` and ``EProfileId`` a playable layout may carry (ADR-026)."""


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
        if doc.get("environment") != "Urban" or (doc.get("time_of_day"), doc.get("profile")) not in ENVIRONMENT_PROFILES:
            problems.append("layout: environment must be Urban / Day / UrbanDay1 or Urban / Night / UrbanNight1")
        lights = doc.get("street_lights_m", [])
        if not isinstance(lights, list) or len(lights) % 3 or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in lights):
            problems.append("layout: street_lights_m must be flat x, y, z triples")
        elif lights and doc.get("time_of_day") != "Night":
            problems.append("layout: street_lights_m is only for a night map")
        roof_wind = doc.get("audio_roof_wind_m", [])
        if not isinstance(roof_wind, list) or len(roof_wind) % 3 or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in roof_wind):
            problems.append("layout: audio_roof_wind_m must be flat x, y, z triples")
        lightning = doc.get("lightning", 0)
        if type(lightning) is not int or lightning not in (0, 1):
            problems.append("layout: lightning must be 0 or 1")
        elif lightning and doc.get("time_of_day") != "Night":
            problems.append("layout: lightning is only for a night map")
        problems.extend(_check_climbs(doc))
        if doc.get("far_clip_m") != 1000 or doc.get("flyover") != 1:
            problems.append("layout: far_clip_m must be 1000 and flyover must be 1")
        if doc.get("schema_version") == GAMEPLAY_VERSION:
            problems.extend(_check_match_modes(doc, point))
    return problems


def _check_climbs(doc: Mapping[str, Any]) -> list[str]:
    """Ladders and lifts (ADR-029). A layout from before them has neither."""
    problems = []
    for key, limit, fields in (
        ("ladders", LADDERS_MAX, ("x", "y", "yaw", "height_m", "parapet_m", "roof_x", "roof_y")),
        ("lifts", LIFTS_MAX, ("x", "y", "yaw", "height_m", "roof_x", "roof_y", "roof_yaw")),
    ):
        sites = doc.get(key, [])
        if not isinstance(sites, list) or len(sites) > limit:
            problems.append(f"layout: {key} must be a list of at most {limit}")
            continue
        for site in sites:
            if (
                not isinstance(site, dict)
                or any(isinstance(site.get(f), bool) or not isinstance(site.get(f), (int, float)) for f in fields)
                or site["height_m"] <= 0
            ):
                problems.append(f"layout: each of {key} needs {', '.join(fields)} and a positive height")
                break
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
    # bounds copies what overgrow fit passed into envelope.fit; a scene with overgrowth and no record was never checked.
    overgrown = any(isinstance(o, dict) and o.get("type") == "overgrowth" for o in manifest.get("objects") or [])
    if overgrown and not (manifest.get("envelope") or {}).get("fit"):
        problems.append("detail: the scene has overgrowth but no envelope.fit; run overgrow fit, then bounds")
    return problems, manifest, report


# The adapter's cap when the export configuration leaves ``build.interior_lights_max`` out (ADR-013).
ADAPTER_INTERIOR_LIGHTS_MAX = 24


def interior_lights(visual: bytes) -> int:
    """Room lights the visual GLB asks for: every ``light_anchors`` room that still has a ``light_m`` (ADR-013)."""
    doc, _blob = read_glb(visual)
    return sum(
        1
        for node in doc.get("nodes") or []
        for room in ((node.get("extras") or {}).get("light_anchors") or {}).get("rooms") or []
        if "light_m" in room
    )


def _resolve_lightmap_scales(options: dict[str, Any], visual: bytes, *, scenic_out_of_lightmap: bool) -> None:
    """Replace ``lightmap_class_scales`` in the adapter options by one scale per visual material."""
    class_scales = {**DEFAULT_CLASS_SCALES, **options.pop("lightmap_class_scales", {})}
    doc, _blob = read_glb(visual)
    names = [str(m.get("name")) for m in doc.get("materials") or []]
    options["lightmap_scales"] = material_scales(names, class_scales, scenic_out_of_lightmap=scenic_out_of_lightmap)


def _apply_page_budget(options: dict[str, Any], visual: bytes, *, scenic_out_of_lightmap: bool) -> dict[str, Any]:
    """Plan C5: lower ``lightmap_texels_per_m`` in the options until the estimate fits ``lightmap_pages_max``.

    Uses the resolved ``lightmap_scales`` and the scenic rule of the manifest estimate, so C4 composes. Within
    budget the options are left as they are. Returns the manifest's ``lightmap_estimate`` at the applied
    density, with the requested density and the budget; ``ExportError`` when the budget cannot be met.
    """
    requested = float(options["lightmap_texels_per_m"])
    pages_max = int(options["lightmap_pages_max"])
    max_size = int(options["lightmap_max_size"])
    areas = lightmap_areas(visual, cell_m=float(options["combine_cell_m"]), scenic_out_of_lightmap=scenic_out_of_lightmap)
    try:
        applied = page_budget_density(areas, requested=requested, pages_max=pages_max, max_size=max_size,
                                      scales=options["lightmap_scales"])
    except LightmapBudgetError as exc:
        raise ExportError([f"export config: /build/lightmap_pages_max: {exc}"]) from exc
    if applied != requested:
        options["lightmap_texels_per_m"] = applied
    found = summarise(areas, texels_per_m=applied, max_size=max_size, scales=options["lightmap_scales"])
    return {**found, "texels_per_m_requested": requested, "lightmap_pages_max": pages_max}


def _lit_normal(options: Mapping[str, Any], visual: bytes, warnings: list[str]) -> tuple[bytes, dict[str, Any]]:
    """Plan D5: the PNG of the normal map derived from the named material's albedo, and the manifest block.

    ``ExportError`` when the material is not in the visual GLB or has no PNG albedo, so nobody measures a no-op
    on the headset. A warning when the lightmaps are not directional: everything is baked, and a non-directional
    lightmap gives the baked diffuse no direction for the normal map to bend.
    """
    material = str(options["lit_normal_material"])
    strength = float(options.get("lit_normal_strength", DEFAULT_STRENGTH))
    try:
        pixels = normal_map(decode_rgb(albedo_png(visual, material)), strength)
    except NormalMapError as exc:
        raise ExportError([f"export config: /build/lit_normal_material: {exc}"]) from exc
    if options.get("lightmap_directional") is not True:
        warnings.append(f"build.lit_normal_material {material}: build.lightmap_directional is not true, so the baked "
                        "diffuse ignores the normal map and the relief shows only in the specular term; set "
                        "build.lightmap_directional true for the headset measurement (ADR-033)")
    height, width = int(pixels.shape[0]), int(pixels.shape[1])
    block = {"material": material, "strength": strength, "path": LIT_NORMAL_FILE, "width": width, "height": height,
             "astc6x6_bytes": astc6x6_bytes(width, height), "source": "generated", "derived_from": "albedo"}
    return encode_png(pixels), block


def _description(config: Mapping[str, Any], attribution: list[Mapping[str, Any]]) -> str:
    credits = "; ".join(f"{a['text']} ({a['license']})" for a in attribution)
    return fit_listing_description(f"{config['map']['description']} Data: {credits}.")


def build_package(project: str | Path, detail_dir: str | Path, layout: str | Path, out_dir: str | Path, *,
                  export_config: str | Path | None = None, now: datetime | None = None,
                  atmosphere: str | None = None) -> PackageResult:
    """Validate every input, then write the package into ``out_dir``, which must not exist yet.

    ``atmosphere`` names a preset of ``config/atmosphere.default.json`` (ADR-037, proposed). None or ``neutral``
    changes nothing, the package id included."""
    preset = None if atmosphere in (None, NEUTRAL) else atmosphere
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
    experimental = cfg["build"].get("experimental_environment")
    if experimental is not None and not (is_schema(layout_doc.get("schema"), GAMEPLAY_SCHEMA) and layout_doc.get("time_of_day") == "Day"):
        problems.append("export config: /build/experimental_environment: only for a day gameplay layout (plan E2)")
    if preset is not None:
        try:
            check_atmosphere(preset, str(layout_doc.get("time_of_day", "Day")).lower())
        except AtmosphereError as exc:
            problems.append(f"atmosphere: {exc}")
    if problems:
        raise ExportError(problems)

    sources = {"visual": detail / "scene_visual.glb", "collision": detail / "scene_collision.glb", "layout": layout_path}
    source_sha = {role: sha256_file(p) for role, p in sources.items()}
    thumbnail = render_listing_thumbnail(sources["visual"].read_bytes(), str(cfg["map"]["title"]))
    thumbnail_sha = hashlib.sha256(thumbnail).hexdigest()
    options = {"variant": "", **cfg["build"]}
    visual_bytes = sources["visual"].read_bytes()
    # Plan C4: read, never written or normalised, so a card without the key keeps its package_id.
    scenic_out = options.get("scenic_out_of_lightmap") is True
    _resolve_lightmap_scales(options, visual_bytes, scenic_out_of_lightmap=scenic_out)
    # Plan C5: only a card with the key runs the page budget, so a card without it keeps its package_id.
    budget_estimate = (_apply_page_budget(options, visual_bytes, scenic_out_of_lightmap=scenic_out)
                       if "lightmap_pages_max" in options else None)
    # Plan D5 (ADR-033, proposed): read, never written or normalised, so a card without the key keeps its package_id.
    lit_normal = _lit_normal(options, visual_bytes, warnings) if "lit_normal_material" in options else None
    if experimental is not None:
        warnings.append("build.experimental_environment: a test package for the headset (plan E2, ADR-037); do not publish it")
    lights, cap = interior_lights(visual_bytes), int(options.get("interior_lights_max", ADAPTER_INTERIOR_LIGHTS_MAX))
    if lights > cap:
        warnings.append(f"detail: the scene asks for {lights} interior lights; the adapter bakes the first {cap} "
                        "(build.interior_lights_max) in node order. Set interiors.lights_max in the detail configuration to match.")
    sun_record: dict[str, Any] | None = None  # pragma: no mutate: read only when a preset sets it below
    if preset is not None:
        # ADR-037: written only with a preset, so a map without one keeps its package_id.
        options["atmosphere"] = preset
        chosen = load_presets()[preset]
        street = None  # pragma: no mutate: a preset with a numeric azimuth never reads it
        if chosen["sun"]["azimuth"] == STREETS:
            # Plan E6: the sun crosses the street axis of the playable core, or of the whole map without a core.
            street = street_sun(visual_bytes, (detail_manifest.get("core") or {}).get("half_side_m"))
        options["lighting"], sun_record = lighting(chosen, street)
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
        if lit_normal is not None:
            png, _block = lit_normal
            atomic_write_bytes(out / LIT_NORMAL_FILE, png)
            files.append({"role": LIT_NORMAL_ROLE, "path": LIT_NORMAL_FILE, "sha256": sha256_file(out / LIT_NORMAL_FILE),
                          "bytes": (out / LIT_NORMAL_FILE).stat().st_size, "source": "generated"})

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
            "lightmap_estimate": budget_estimate if budget_estimate is not None else estimate(
                visual_bytes, texels_per_m=float(options["lightmap_texels_per_m"]),
                max_size=int(options["lightmap_max_size"]), cell_m=float(options["combine_cell_m"]),
                scales=options["lightmap_scales"], scenic_out_of_lightmap=scenic_out,
            ),
            "tiers": _tiers(visual_bytes, detail_manifest, float(options["combine_cell_m"])),
            "view_draws": _view_draws(visual_bytes, detail_manifest, layout_doc, float(options["combine_cell_m"])),
            "warnings": warnings,
        }
        if lit_normal is not None:
            manifest["lit_normal"] = lit_normal[1]
        if experimental is not None:
            # Plan E2: what the client shows for each profile is unknown until a headset session (ADR-037).
            manifest["experimental_environment"] = {**experimental, "status": "untested"}
        if preset is not None:
            # Which parts of the preset the package applies: the sun since plan E3.
            manifest["atmosphere"] = {"preset": preset, "origin": "generated", "applied": ["sun"], "sun": sun_record}
        atomic_write_json(out / MANIFEST_FILE, manifest)
        atomic_write_bytes(out / CHECKLIST_FILE, checklist(manifest).encode("utf-8"))
    except BaseException:
        shutil.rmtree(out, ignore_errors=True)
        raise
    return PackageResult(out, manifest, tuple(warnings))


def _tiers(visual: bytes, detail_manifest: Mapping[str, Any], cell_m: float) -> dict[str, Any] | None:
    """Triangles and predicted draws of the playable core and the scenic ring (ADR-017); None without a core.

    ``combine_cell_m`` 0 leaves one renderer per node; the prediction then uses 100 m cells (ADR-005).
    """
    core = core_of(detail_manifest)
    if core is None:
        return None
    return tier_summary(visual, detail_manifest.get("objects") or [], core, cell_m=cell_m or DEFAULT_CELL_M)


def _view_draws(visual: bytes, detail_manifest: Mapping[str, Any], layout_doc: Mapping[str, Any], cell_m: float) -> dict[str, Any] | None:
    """Draws per player view across the playable area: the core, else the layout's site (``tiers.view_draws``).

    None when there is neither: a layout without ``site_bounds_m`` on a map without a core.
    """
    site = layout_doc.get("site_bounds_m")
    area = core_of(detail_manifest) or (box(*(float(v) for v in site)) if site else None)
    if area is None:
        return None
    return view_draws(visual, area, cell_m=cell_m or DEFAULT_CELL_M)


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
