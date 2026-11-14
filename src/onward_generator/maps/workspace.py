"""Create an isolated map workspace from a name and a WGS84 centre.

The folder holds the project configuration, the export card and a square area
of interest measured in metres. Later stages write under ``data/processed``.
Belluga is not modified. The Quest local slot still holds one map at a time.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Any

from shapely import box

from onward_generator.ai.providers import Provider
from onward_generator.config.validation import validate_config_file
from onward_generator.contracts import is_schema
from onward_generator.export.atmosphere import NEUTRAL, AtmosphereError, check_atmosphere
from onward_generator.export.package import ExportError, load_export_config
from onward_generator.geo.aoi import AoiError, load_aoi_geojson
from onward_generator.geo.projection import LocalFrame, ProjectionError, frame_for_origin
from onward_generator.io import atomic_write_json
from onward_generator.materials.library_plan import select_texture_plan
from onward_generator.sources.registry import choices, plugin_by_id
from onward_generator.tiers import CORE_BUDGET_MODE, MIN_CORE_HALF_SIDE_M, CoreError, check_mid_ring, check_target

MAP_SCHEMA = "onward_generator.map"
MAP_VERSION = "0.1.0"
_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")
_RADIUS_MIN_M = 50.0
_RADIUS_MAX_M = 1500.0
_MARGIN_MAX_M = 500.0
TIMES_OF_DAY = ("day", "night")
TIME_OF_DAY_DEFAULT = "day"
_TEXELS_DEFAULT = 10.0
# The playable core (ADR-017): a square of this half-side around the centre keeps full detail. Without
# ``core_radius_m`` a map keeps a scenic ring at least CORE_RING_DEFAULT_M wide and a core of at most this:
# on Barcelona Sarria (r 150) the whole map was played, at 381 k triangles and up to 376 draws a view.
CORE_RADIUS_DEFAULT_M = 150.0
CORE_RING_DEFAULT_M = 50.0
# The area of a new map (plan Q2b): a 150 m half-side keeps the default core at 100 m. The former 400 m, with its
# 150 m core, gave 421 689 visual triangles on Rue Mouffetard and 430 774 in Unity on Madrid Sol workshop (r 300),
# against the official 250 000. With 150 m Madrid Sol arena and Barcelona Sarria bake within it (244 834, 232 488);
# Rue Mouffetard, denser, was still 252 179 with its 100 m core and fitted at 95 m (plan Q2a's estimate says so).
RADIUS_DEFAULT_M = 150.0
MARGIN_DEFAULT_M = 30.0
_TEXELS_MAX = 40.0
# Linear fog from the wall outwards (ADR-014). The band is a look, not a measurement.
_FOG_BAND_M = 600.0
_FOG_COLOR = "#C8CDD2"
# Baked room lights the adapter places (ADR-013). Without these keys the adapter falls back to
# 24 lights, which left most rooms of a map dark. Keep the cap equal to interiors.lights_max
# in config/detail.default.json.
_INTERIOR_LIGHTS_MAX = 160
_INTERIOR_LIGHT_INTENSITY = 3.0


class MapError(ValueError):
    """The map folder cannot be created from these inputs."""


# Plan Q8: the boolean switches of map.json that map create can turn on, in the order it writes them, and the
# help each flag shows. Absent is off, so a map created without them is the one of before.
MAP_SWITCHES: Mapping[str, str] = {
    "street_profile": "local Ollama picks a street profile of enumerated ids for the stage flags (plan D2)",
    "repetition": "calm roof and paving pools and a per-cell ground phase; the texture plan takes the pools (plan C1)",
    "overture_tints": "facade tints from Overture building colours where OSM has none (plan C2)",
    "lidar_roofs": "flat or pitched roofs from the LiDAR nDSM spread where OSM has no roof tag (plan C3)",
    "regional_facades": "regional trim atlases from config/facade_styles.regional.json (plan D1)",
    "gameplay_report": "write gameplay_report.json beside the layout (ADR-032)",
    "balanced_spawns": "place the spawns and the Uplink by the gameplay metrics (plan C6)",
    "sightline_cover": "cover props on long open sightlines (plan C7)",
    "panoramax_atlas": "pack the street-photo decals per combine cell; acts only with --panoramax (ADR-020 amendment)",
    "wide_library": "the texture plan also draws on the wide CC0 library of config/dress/wide_library.json (plan T1)",
    "normal_maps": "draw the library photographs that have one with their real normal map (PP_Opaque_Lit, plan T3)",
    "texture_variants": "two more calm photographs for building roofs and plaster walls, one per building (plan T4)",
}


DEFAULT_OFF_SWITCHES = frozenset({"panoramax_atlas"})
"""Switches ``map create`` leaves off unless asked: the street-photo atlas acts only with Panoramax, still under
development. Every other switch of ``MAP_SWITCHES`` is on for a new map made from the command line (13 November 2026),
so a user gives a name, a centre and a radius and gets every improvement; ``--no-<switch>`` turns one off."""


def chosen_switches(explicit: Mapping[str, bool | None]) -> list[str]:
    """The switches a new map gets, in ``MAP_SWITCHES`` order: each one the user named on or off (``explicit``), the
    others on unless they are in ``DEFAULT_OFF_SWITCHES``."""
    return [switch for switch in MAP_SWITCHES
            if (explicit.get(switch) if explicit.get(switch) is not None else switch not in DEFAULT_OFF_SWITCHES)]


def create_map(
    directory: str | Path,
    *,
    map_id: str,
    name: str,
    latitude: float,
    longitude: float,
    radius_m: float = RADIUS_DEFAULT_M,
    margin_m: float = MARGIN_DEFAULT_M,
    seed: int | None = None,
    description: str | None = None,
    brief: str | None = None,
    ambient_ai: bool = False,
    panoramax: bool = False,
    time_of_day: str = TIME_OF_DAY_DEFAULT,
    storm: bool = False,
    atmosphere: str = NEUTRAL,
    lightmap_texels_per_m: float = _TEXELS_DEFAULT,
    core_radius_m: float | None = None,
    core_target_triangles: int | None = None,
    overwrite: bool = False,
    texture_provider: Provider | None = None,
    switches: Collection[str] = (),
) -> dict[str, Any]:
    """Write ``map.json``, configs and a metric AOI. The CLI then fetches OSM.

    ``core_target_triangles`` turns on the budget core (ADR-017 amendment): ``map.json`` then gets
    ``core: {mode: "budget", target_triangles, max_half_side_m}``, the largest half-side being
    ``core_radius_m`` when given and ``radius_m - CORE_RING_DEFAULT_M`` otherwise.

    ``atmosphere`` other than ``neutral`` writes ``atmosphere`` in ``map.json`` (ADR-037); ``neutral`` writes no key.

    ``switches`` names the ``MAP_SWITCHES`` to turn on (plan Q8): each is written ``true`` after ``paths``, the others are
    left out, and ``repetition`` also gives the texture plan its calm pools."""
    root = Path(directory).expanduser().resolve()
    map_id = _map_id(map_id)
    title = _title(name)
    radius_m = _radius(radius_m)
    margin_m = _margin(margin_m)
    seed = _seed(latitude, longitude, seed)
    description = _description(title, latitude, longitude, description)
    brief = _brief(brief)
    texels = _texels(lightmap_texels_per_m)
    atmosphere = _atmosphere(atmosphere, time_of_day)
    core = _core(core_radius_m, radius_m)
    budget = _core_budget(core_target_triangles, core_radius_m, core, radius_m)
    unknown = sorted(set(switches) - set(MAP_SWITCHES))
    if unknown:
        raise MapError(f"unknown map switch {', '.join(unknown)}; known: {', '.join(MAP_SWITCHES)}")
    _guard_directory(root, overwrite=overwrite)

    try:
        frame = frame_for_origin(longitude, latitude)
    except ProjectionError as exc:
        raise MapError(str(exc)) from exc
    ring = _square_ring(frame, radius_m)

    root.mkdir(parents=True, exist_ok=True)
    (root / "data" / "raw").mkdir(parents=True, exist_ok=True)
    (root / "data" / "processed").mkdir(parents=True, exist_ok=True)
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "reference").mkdir(parents=True, exist_ok=True)

    aoi_rel = "reference/aoi.geojson"
    project_rel = "config/project.json"
    export_rel = "config/export.json"
    atomic_write_json(root / aoi_rel, _aoi_document(title, longitude, latitude, radius_m, margin_m, ring))
    atomic_write_json(root / project_rel, _project_document(title, latitude, longitude, margin_m, seed, aoi_rel, frame.crs))
    atomic_write_json(root / export_rel, _export_document(map_id, title, description, texels, core or radius_m + margin_m))
    _write_texture_plan(
        root, map_id=map_id, name=title, latitude=latitude, longitude=longitude,
        brief=brief, seed=seed, provider=texture_provider, extended="repetition" in switches, wide="wide_library" in switches,
    )

    project_result = validate_config_file(root / project_rel)
    if not project_result.valid:
        raise MapError("; ".join(str(item) for item in project_result.diagnostics if item.severity == "error"))
    try:
        load_export_config(root / export_rel)
        load_aoi_geojson(root / aoi_rel, margin_m=margin_m, name=title)
    except (ExportError, AoiError) as exc:
        raise MapError(str(exc)) from exc

    document = {
        "schema": MAP_SCHEMA,
        "schema_version": MAP_VERSION,
        "id": map_id,
        "name": title,
        "description": description,
        "brief": brief,
        "ambient_ai": ambient_ai,
        "panoramax": bool(panoramax),
        "time_of_day": _time_of_day(time_of_day),
        "storm": _storm(storm, time_of_day),
        **({"atmosphere": atmosphere} if atmosphere != NEUTRAL else {}),
        "center": {"latitude": latitude, "longitude": longitude},
        "radius_m": radius_m,
        "margin_m": margin_m,
        "core_radius_m": core,
        **({"core": budget} if budget is not None else {}),
        "crs": frame.crs,
        "seed": seed,
        "paths": {
            "project": project_rel,
            "export": export_rel,
            "aoi": aoi_rel,
            "raw": "data/raw",
            "processed": "data/processed",
        },
        **{name: True for name in MAP_SWITCHES if name in switches},
    }
    atomic_write_json(root / "map.json", document)
    return document


def panoramax_enabled(directory: str | Path) -> bool:
    """True only when this map's ``map.json`` asks for Panoramax pictures.

    A map written before the flag existed has no key. That map stays off.
    """
    try:
        document = load_map(directory)
    except MapError:
        return False
    return bool(document.get("panoramax", False))


def panoramax_atlas_enabled(directory: str | Path) -> bool:
    """True only when ``map.json`` has ``"panoramax_atlas": true`` (ADR-020 amendment, off by default).

    It acts only together with ``"panoramax": true``. A map without ``map.json``
    or without the key stays off. A value that is not a bool is refused.
    """
    try:
        document = load_map(directory)
    except MapError:
        return False
    value = document.get("panoramax_atlas", False)
    if not isinstance(value, bool):
        raise MapError(f'map.json "panoramax_atlas" must be true or false, got {value!r}')
    return value


def gameplay_report_enabled(directory: str | Path) -> bool:
    """True only when ``map.json`` has ``"gameplay_report": true`` (ADR-032, off by default).

    ``run`` then asks ``perf layout`` for ``gameplay_report.json``. A map without
    ``map.json`` or without the key stays off. A value that is not a bool is refused.
    """
    try:
        document = load_map(directory)
    except MapError:
        return False
    value = document.get("gameplay_report", False)
    if not isinstance(value, bool):
        raise MapError(f'map.json "gameplay_report" must be true or false, got {value!r}')
    return value


def store_gameplay_report(directory: str | Path, enabled: bool) -> None:
    """Record the gameplay report switch on an existing map (ADR-032)."""
    if not isinstance(enabled, bool):
        raise MapError(f'"gameplay_report" must be true or false, got {enabled!r}')
    root = Path(directory).expanduser().resolve()
    document = load_map(root)
    document["gameplay_report"] = enabled
    atomic_write_json(root / "map.json", document)


def balanced_spawns_enabled(directory: str | Path) -> bool:
    """True only when ``map.json`` has ``"balanced_spawns": true`` (plan C6, ADR-032 amendment, off by default).

    ``run`` then asks ``perf layout`` for ``--balanced-spawns``. A map without ``map.json``
    or without the key stays off. A value that is not a bool is refused.
    """
    try:
        document = load_map(directory)
    except MapError:
        return False
    value = document.get("balanced_spawns", False)
    if not isinstance(value, bool):
        raise MapError(f'map.json "balanced_spawns" must be true or false, got {value!r}')
    return value


def store_balanced_spawns(directory: str | Path, enabled: bool) -> None:
    """Record the balanced spawns switch on an existing map (plan C6)."""
    if not isinstance(enabled, bool):
        raise MapError(f'"balanced_spawns" must be true or false, got {enabled!r}')
    root = Path(directory).expanduser().resolve()
    document = load_map(root)
    document["balanced_spawns"] = enabled
    atomic_write_json(root / "map.json", document)


def sightline_cover_enabled(directory: str | Path) -> bool:
    """True only when ``map.json`` has ``"sightline_cover": true`` (plan C7, ADR-032 C7 amendment, off by default).

    ``run`` then plans ``perf cover`` after the layout and exports its ``scene_covered``. A map without
    ``map.json`` or without the key stays off. A value that is not a bool is refused.
    """
    try:
        document = load_map(directory)
    except MapError:
        return False
    value = document.get("sightline_cover", False)
    if not isinstance(value, bool):
        raise MapError(f'map.json "sightline_cover" must be true or false, got {value!r}')
    return value


def store_sightline_cover(directory: str | Path, enabled: bool) -> None:
    """Record the sightline cover switch on an existing map (plan C7)."""
    if not isinstance(enabled, bool):
        raise MapError(f'"sightline_cover" must be true or false, got {enabled!r}')
    root = Path(directory).expanduser().resolve()
    document = load_map(root)
    document["sightline_cover"] = enabled
    atomic_write_json(root / "map.json", document)


def repetition_enabled(directory: str | Path) -> bool:
    """True only when ``map.json`` has ``"repetition": true`` (plan C1, ADR-009 amendment, off by default).

    ``materials apply`` then measures roof and paving tiles and phases the ground UVs per
    combine cell. A map without ``map.json`` or without the key stays off. A value that is not
    a bool is refused.
    """
    try:
        document = load_map(directory)
    except MapError:
        return False
    value = document.get("repetition", False)
    if not isinstance(value, bool):
        raise MapError(f'map.json "repetition" must be true or false, got {value!r}')
    return value


def wide_library_enabled(directory: str | Path) -> bool:
    """True only when ``map.json`` has ``"wide_library": true`` (plan T1, off by default): the texture plan then also
    draws on ``config/dress/wide_library.json``. A value that is not a bool is refused."""
    value = load_map(directory).get("wide_library", False)
    if not isinstance(value, bool):
        raise MapError(f'map.json "wide_library" must be true or false, got {value!r}')
    return value


def texture_variants_enabled(directory: str | Path) -> bool:
    """True only when ``map.json`` has ``"texture_variants": true`` (plan T4, off by default). A value that is not a bool
    is refused."""
    value = load_map(directory).get("texture_variants", False)
    if not isinstance(value, bool):
        raise MapError(f'map.json "texture_variants" must be true or false, got {value!r}')
    return value


def normal_maps_enabled(directory: str | Path) -> bool:
    """True only when ``map.json`` has ``"normal_maps": true`` (plan T3, off by default). A value that is not a bool is
    refused."""
    value = load_map(directory).get("normal_maps", False)
    if not isinstance(value, bool):
        raise MapError(f'map.json "normal_maps" must be true or false, got {value!r}')
    return value


def store_repetition(directory: str | Path, enabled: bool) -> None:
    """Record the repetition switch on an existing map. Does not rewrite scenes or the texture plan."""
    if not isinstance(enabled, bool):
        raise MapError(f'"repetition" must be true or false, got {enabled!r}')
    root = Path(directory).expanduser().resolve()
    document = load_map(root)
    document["repetition"] = enabled
    atomic_write_json(root / "map.json", document)


def overture_tints_enabled(directory: str | Path) -> bool:
    """True only when ``map.json`` has ``"overture_tints": true`` (plan C2, ADR-021 amendment, off by default).

    ``overture match`` then lends observed Overture facade and roof colours, and ``detail`` reads them below the
    OSM tags. A map without ``map.json`` or without the key stays off. A value that is not a bool is refused.
    """
    try:
        document = load_map(directory)
    except MapError:
        return False
    value = document.get("overture_tints", False)
    if not isinstance(value, bool):
        raise MapError(f'map.json "overture_tints" must be true or false, got {value!r}')
    return value


def store_overture_tints(directory: str | Path, enabled: bool) -> None:
    """Record the Overture tints switch on an existing map. Does not rewrite scenes."""
    if not isinstance(enabled, bool):
        raise MapError(f'"overture_tints" must be true or false, got {enabled!r}')
    root = Path(directory).expanduser().resolve()
    document = load_map(root)
    document["overture_tints"] = enabled
    atomic_write_json(root / "map.json", document)


def lidar_roofs_enabled(directory: str | Path) -> bool:
    """True only when ``map.json`` has ``"lidar_roofs": true`` (plan C3, ADR-023 amendment, off by default).

    ``detail`` then reads the LiDAR plugin's report and draws a flat or pitched roof from each footprint's nDSM spread.
    A map without ``map.json`` or without the key stays off. A value that is not a bool is refused.
    """
    try:
        document = load_map(directory)
    except MapError:
        return False
    value = document.get("lidar_roofs", False)
    if not isinstance(value, bool):
        raise MapError(f'map.json "lidar_roofs" must be true or false, got {value!r}')
    return value


def store_lidar_roofs(directory: str | Path, enabled: bool) -> None:
    """Record the LiDAR roofs switch on an existing map. Does not rewrite scenes."""
    if not isinstance(enabled, bool):
        raise MapError(f'"lidar_roofs" must be true or false, got {enabled!r}')
    root = Path(directory).expanduser().resolve()
    document = load_map(root)
    document["lidar_roofs"] = enabled
    atomic_write_json(root / "map.json", document)


def _mid_ring_value(document: Mapping[str, Any], value: object) -> float:
    """``value`` as ``mid_ring_m`` of the map ``document``: metres in range, on a map with a core (fixed or budget)."""
    if not document.get("core_radius_m") and document.get("core") is None:
        raise MapError('map.json "mid_ring_m" needs a playable core ("core_radius_m" or a budget "core")')
    try:
        return check_mid_ring(value)
    except CoreError as exc:
        raise MapError(f"map.json {exc}") from exc


def mid_ring_m(directory: str | Path) -> float | None:
    """``"mid_ring_m"`` of ``map.json`` (plan D3, ADR-017 amendment, off by default), or None without the key or the map.

    ``detail`` then draws the buildings outside the core within that many metres of it as mid shells. A value
    that is not a number of metres from 10 to 1000, or the key on a map without a core, is refused.
    """
    try:
        document = load_map(directory)
    except MapError:
        return None
    if "mid_ring_m" not in document:
        return None
    return _mid_ring_value(document, document["mid_ring_m"])


def store_mid_ring(directory: str | Path, metres: float | None) -> None:
    """Record the intermediate ring width on an existing map, or remove it with None. Does not rewrite scenes."""
    root = Path(directory).expanduser().resolve()
    document = load_map(root)
    if metres is None:
        document.pop("mid_ring_m", None)
    else:
        document["mid_ring_m"] = _mid_ring_value(document, metres)
    atomic_write_json(root / "map.json", document)


def regional_facades_enabled(directory: str | Path) -> bool:
    """True only when ``map.json`` has ``"regional_facades": true`` (plan D1, ADR-020 amendment, off by default).

    ``materials apply`` then gives central_european, anglo and latin_american their atlas from
    ``config/facade_styles.regional.json``. A map without ``map.json`` or without the key stays off.
    A value that is not a bool is refused.
    """
    try:
        document = load_map(directory)
    except MapError:
        return False
    value = document.get("regional_facades", False)
    if not isinstance(value, bool):
        raise MapError(f'map.json "regional_facades" must be true or false, got {value!r}')
    return value


def store_regional_facades(directory: str | Path, enabled: bool) -> None:
    """Record the regional facades switch on an existing map. Does not rewrite scenes."""
    if not isinstance(enabled, bool):
        raise MapError(f'"regional_facades" must be true or false, got {enabled!r}')
    root = Path(directory).expanduser().resolve()
    document = load_map(root)
    document["regional_facades"] = enabled
    atomic_write_json(root / "map.json", document)


def street_profile_enabled(directory: str | Path) -> bool:
    """True only when ``map.json`` has ``"street_profile": true`` (plan D2, ADR-006/010 amendments, off by default).

    ``run`` then asks the local model for a street profile of enumerated ids and passes the
    stage flags they stand for (docs/10-ai.md, Street profile). A map without ``map.json`` or
    without the key stays off. A value that is not a bool is refused.
    """
    try:
        document = load_map(directory)
    except MapError:
        return False
    value = document.get("street_profile", False)
    if not isinstance(value, bool):
        raise MapError(f'map.json "street_profile" must be true or false, got {value!r}')
    return value


def store_street_profile(directory: str | Path, enabled: bool) -> None:
    """Record the street profile switch on an existing map. Does not rewrite scenes."""
    if not isinstance(enabled, bool):
        raise MapError(f'"street_profile" must be true or false, got {enabled!r}')
    root = Path(directory).expanduser().resolve()
    document = load_map(root)
    document["street_profile"] = enabled
    atomic_write_json(root / "map.json", document)


DEFAULT_COMBINE_CELL_M = 100.0


def combine_cell_m(directory: str | Path) -> float:
    """The export card's ``build.combine_cell_m``; 100 m when the card is missing or the value is 0 (ADR-005)."""
    root = Path(directory).expanduser().resolve()
    try:
        document = load_map(root)
    except MapError:
        return DEFAULT_COMBINE_CELL_M
    card_path = root / str((document.get("paths") or {}).get("export") or "config/export.json")
    if not card_path.is_file():
        return DEFAULT_COMBINE_CELL_M
    import json

    try:
        card = json.loads(card_path.read_bytes())
    except ValueError as exc:  # JSONDecodeError and UnicodeDecodeError
        raise MapError(f"{card_path}: not valid JSON ({exc})") from exc
    build = (card.get("build") or {}) if isinstance(card, dict) else {}
    if not isinstance(build, dict):
        raise MapError(f"{card_path}: build must be an object, got {build!r}")
    value = build.get("combine_cell_m", 0.0)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise MapError(f"{card_path}: build.combine_cell_m must be a number of metres, got {value!r}")
    return float(value) if value > 0 else DEFAULT_COMBINE_CELL_M


def store_panoramax(directory: str | Path, enabled: bool) -> None:
    """Record the Panoramax switch on an existing map. Does not rewrite scenes."""
    root = Path(directory).expanduser().resolve()
    document = load_map(root)
    document["panoramax"] = bool(enabled)
    atomic_write_json(root / "map.json", document)


def source_enabled(directory: str | Path, plugin_id: str) -> bool:
    """True when ``run`` uses the source plugin ``plugin_id`` for this map (ADR-018).

    The map centre must be inside the plugin's coverage, and ``map.json`` must
    not switch it off (``run --no-source <id>``; for ``es.catastro`` also the
    older ``"catastro": false``). A map without a switch uses every plugin
    that covers it, except an opt-in plugin (plan D4), which needs
    ``run --source <id>`` (``"sources": {"<id>": true}``).
    """
    try:
        document = load_map(directory)
    except MapError:
        return False
    return any(choice.runs for choice in choices(document) if choice.plugin.id == plugin_id)


def facade_options(directory: str | Path) -> list[str]:
    """``materials apply`` flags from this map's ``map.json`` ``"facades"`` block (ADR-019).

    ``{"mode": "trim" | "legacy", "style": "<name>" | "auto", "weathering": "clean" | "worn" | "heavy"}``; any key may
    be left out. A map without the block, or without a ``map.json``, gets no flags and the stage defaults (``trim``,
    ``auto``, the style's own grime). A block of another shape is refused.
    """
    try:
        document = load_map(directory)
    except MapError:
        return []
    block = document.get("facades")
    if block is None:
        return []
    if not isinstance(block, dict) or not set(block) <= {"mode", "style", "weathering"} or not all(isinstance(v, str) for v in block.values()):
        raise MapError('map.json "facades" must be {"mode": "trim" | "legacy", "style": "<name>" | "auto", '
                       '"weathering": "clean" | "worn" | "heavy"}')
    flags = ["--facades", block["mode"]] if "mode" in block else []
    flags += ["--facade-style", block["style"]] if "style" in block else []
    return flags + (["--facade-weathering", block["weathering"]] if "weathering" in block else [])


def store_sources(directory: str | Path, switches: Mapping[str, bool]) -> None:
    """Record ``run --source`` / ``--no-source`` in ``map.json`` ``sources``. Does not rewrite scenes.

    Writing ``es.catastro`` drops the older ``catastro`` key, so one switch decides.
    """
    for plugin_id in switches:
        plugin_by_id(plugin_id)
    root = Path(directory).expanduser().resolve()
    document = load_map(root)
    stored = dict(document.get("sources") or {})
    stored.update({plugin_id: bool(enabled) for plugin_id, enabled in switches.items()})
    document["sources"] = dict(sorted(stored.items()))
    if "es.catastro" in switches:
        document.pop("catastro", None)
    atomic_write_json(root / "map.json", document)


def load_map(directory: str | Path) -> dict[str, Any]:
    path = Path(directory).expanduser().resolve() / "map.json"
    if not path.is_file():
        raise MapError(f"{path} not found")
    import json

    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not is_schema(document.get("schema"), MAP_SCHEMA):
        raise MapError(f"{path} is not a {MAP_SCHEMA} document")
    core_budget(document)
    return document


def core_budget(document: Mapping[str, Any]) -> tuple[float, int] | None:
    """``(max_half_side_m, target_triangles)`` of a budget core in ``map.json``, or None without the ``core`` key.

    The block is checked again on reading: a hand-edited mode, target or largest half-side is refused.
    """
    block = document.get("core")
    if block is None:
        return None
    if not isinstance(block, dict) or set(block) != {"mode", "target_triangles", "max_half_side_m"}:
        raise MapError('map.json "core" must be {"mode": "budget", "target_triangles": <int>, "max_half_side_m": <metres>}')
    if block["mode"] != CORE_BUDGET_MODE:
        raise MapError(f'map.json core mode must be "{CORE_BUDGET_MODE}", got {block["mode"]!r}')
    radius = float(document["radius_m"])
    core = document.get("core_radius_m")
    checked = _core_budget(block["target_triangles"], block["max_half_side_m"], float(core) if core else None, radius)
    assert checked is not None  # the target is set, so the block is built or refused
    return float(checked["max_half_side_m"]), int(checked["target_triangles"])


def _square_ring(frame: LocalFrame, radius_m: float) -> list[list[float]]:
    local = box(-radius_m, -radius_m, radius_m, radius_m)
    geographic = frame.to_wgs84(local)
    if not frame.within_area_of_use(geographic):
        raise MapError(f"a {radius_m:g} m square around the centre leaves the area of use of {frame.crs}")
    ring = [[round(float(x), 7), round(float(y), 7)] for x, y in geographic.exterior.coords]
    ring[-1] = list(ring[0])
    return ring


def refresh_texture_plan(directory: str | Path, *, texture_provider: Provider | None = None) -> Path:
    """Replace only ``config/texture_plan.json`` on a map that already exists.

    With the ``"repetition"`` switch on, roofs and paving come from their calm pools too; with ``"wide_library"`` every
    pool also holds the wide library.
    """
    root = Path(directory).expanduser().resolve()
    document = load_map(root)
    extended = repetition_enabled(root)
    center = document["center"]
    seed = int(document.get("seed") or 0)
    path = _write_texture_plan(
        root,
        map_id=str(document["id"]),
        name=str(document["name"]),
        latitude=float(center["latitude"]),
        longitude=float(center["longitude"]),
        brief=str(document.get("brief") or "") or None,
        seed=seed,
        provider=texture_provider,
        extended=extended,
        wide=wide_library_enabled(root),
    )
    return path


def _write_texture_plan(
    root: Path,
    *,
    map_id: str,
    name: str,
    latitude: float,
    longitude: float,
    brief: str | None,
    seed: int,
    provider: Provider | None,
    extended: bool = False,
    wide: bool,
) -> Path:
    document = select_texture_plan(
        name=name,
        map_id=map_id,
        latitude=latitude,
        longitude=longitude,
        brief=brief,
        seed=seed,
        provider=provider,
        extended=extended,
        wide=wide,
    )
    path = root / "config" / "texture_plan.json"
    atomic_write_json(path, document)
    return path


def _guard_directory(root: Path, *, overwrite: bool) -> None:
    if (root / "map.json").is_file() and not overwrite:
        raise MapError(f"{root} already has map.json; pass --overwrite or choose another folder")
    processed = root / "data" / "processed"
    if processed.is_dir() and any(path.is_file() and path.name != ".gitkeep" for path in processed.rglob("*")):
        raise MapError(f"{processed} already has pipeline output; use another folder")


def slug_from_name(name: str) -> str:
    """Folder id for a card title. ``Apocalipsis Murciana`` becomes ``apocalipsis_murciana``."""
    title = _title(name)
    folded = unicodedata.normalize("NFKD", title)
    letters = "".join(character for character in folded if not unicodedata.combining(character))
    slug = re.sub(r"[^a-z0-9]+", "_", letters.lower()).strip("_")
    return _map_id(slug)


def map_directory(name: str) -> Path:
    """``maps/<slug>`` for this card title."""
    return Path("maps") / slug_from_name(name)


def _map_id(value: str) -> str:
    if not _ID.fullmatch(value):
        raise MapError("map id must start with a letter and use only letters, digits and underscores (max 40)")
    return value


def _title(name: str) -> str:
    title = name.strip()
    if not title or len(title) > 80:
        raise MapError("name must be 1–80 characters")
    return title


def _radius(radius_m: float) -> float:
    if not _RADIUS_MIN_M <= radius_m <= _RADIUS_MAX_M:
        raise MapError(f"radius_m must be between {_RADIUS_MIN_M:g} and {_RADIUS_MAX_M:g}")
    return float(radius_m)


def _margin(margin_m: float) -> float:
    if not 0 <= margin_m <= _MARGIN_MAX_M:
        raise MapError(f"margin_m must be between 0 and {_MARGIN_MAX_M:g}")
    return float(margin_m)


def default_core(radius_m: float) -> float:
    """The core half-side a map gets when none is given: ``CORE_RING_DEFAULT_M`` inside the radius, at most
    ``CORE_RADIUS_DEFAULT_M``. Under ``MIN_CORE_HALF_SIDE_M`` it is 0, and the whole map is played."""
    core = min(CORE_RADIUS_DEFAULT_M, radius_m - CORE_RING_DEFAULT_M)
    return core if core >= MIN_CORE_HALF_SIDE_M else 0.0


def _core(core_radius_m: float | None, radius_m: float) -> float | None:
    """The playable core half-side, or None: 0, or a core at or past the radius, plays the whole map.

    ``None`` takes ``default_core``.
    """
    if core_radius_m is None:
        core_radius_m = default_core(radius_m)
    if isinstance(core_radius_m, bool) or not isinstance(core_radius_m, (int, float)):
        raise MapError("core_radius_m must be a number")
    if core_radius_m == 0 or core_radius_m >= radius_m:
        return None
    if not MIN_CORE_HALF_SIDE_M <= core_radius_m:
        raise MapError(f"core_radius_m must be 0 or at least {MIN_CORE_HALF_SIDE_M:g}")
    return float(core_radius_m)


def _core_budget(target: object, core_radius_m: object, core: float | None, radius_m: float) -> dict[str, Any] | None:
    """The ``map.json`` ``core`` block of a budget core, or None without a target.

    ``core`` is the stored ``core_radius_m``: a map without a playable core has nothing to solve. The largest
    half-side is ``core_radius_m`` when given, else ``radius_m - CORE_RING_DEFAULT_M``, and keeps the ring.
    """
    if target is None:
        return None
    try:
        target = check_target(target)
    except CoreError as exc:
        raise MapError(str(exc)) from exc
    if core is None:
        raise MapError("a budget core needs a playable core: core_radius_m 0, or a radius under 100 m, plays the whole map")
    cap = radius_m - CORE_RING_DEFAULT_M if core_radius_m is None else core_radius_m
    if isinstance(cap, bool) or not isinstance(cap, (int, float)) or not MIN_CORE_HALF_SIDE_M <= cap <= radius_m - CORE_RING_DEFAULT_M:
        raise MapError(f"the largest core half-side must be between {MIN_CORE_HALF_SIDE_M:g} and {radius_m - CORE_RING_DEFAULT_M:g} m "
                       f"(radius_m - {CORE_RING_DEFAULT_M:g}), got {cap!r}")
    return {"mode": CORE_BUDGET_MODE, "target_triangles": target, "max_half_side_m": float(cap)}


def _texels(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MapError("lightmap_texels_per_m must be a number")
    number = float(value)
    if not 0 < number <= _TEXELS_MAX:
        raise MapError(f"lightmap_texels_per_m must be greater than 0 and at most {_TEXELS_MAX:g}")
    return number


def _seed(latitude: float, longitude: float, seed: int | None) -> int:
    if seed is None:
        seed = (abs(int(round(latitude * 1_000_000))) * 1_000_003 + abs(int(round(longitude * 1_000_000)))) % 4_294_967_295
    if not isinstance(seed, int) or isinstance(seed, bool) or not 0 <= seed <= 4_294_967_295:
        raise MapError("seed must be an integer from 0 to 4294967295")
    return seed


def map_time_of_day(directory: str | Path) -> str:
    """The map's ``time_of_day``; a map from before the setting, or a folder without a map, is a day map."""
    path = Path(directory).expanduser() / "map.json"
    if not path.is_file():
        return TIME_OF_DAY_DEFAULT
    return str(load_map(path.parent).get("time_of_day", TIME_OF_DAY_DEFAULT))


def store_time_of_day(directory: str | Path, time_of_day: str) -> bool:
    """Record the time of day on an existing map. True when it changed, so the layout is out of date."""
    root = Path(directory).expanduser().resolve()
    document = load_map(root)
    changed = document.get("time_of_day", TIME_OF_DAY_DEFAULT) != _time_of_day(time_of_day)
    _storm(bool(document.get("storm")), time_of_day)
    _atmosphere(str(document.get("atmosphere", NEUTRAL)), time_of_day)
    document["time_of_day"] = time_of_day
    atomic_write_json(root / "map.json", document)
    return changed


def _time_of_day(value: str) -> str:
    if value not in TIMES_OF_DAY:
        raise MapError(f"time_of_day must be one of {', '.join(TIMES_OF_DAY)}, not {value!r}")
    return value


def map_storm(directory: str | Path) -> bool:
    """Whether the map has a storm; a map from before the setting, or a folder without a map, has none."""
    path = Path(directory).expanduser() / "map.json"
    if not path.is_file():
        return False
    return bool(load_map(path.parent).get("storm"))


def store_storm(directory: str | Path, storm: bool) -> bool:
    """Record the storm on an existing map. True when it changed, so the layout is out of date."""
    root = Path(directory).expanduser().resolve()
    document = load_map(root)
    changed = bool(document.get("storm")) != _storm(storm, str(document.get("time_of_day")))
    document["storm"] = storm
    atomic_write_json(root / "map.json", document)
    return changed


def _storm(storm: bool, time_of_day: str) -> bool:
    if storm and time_of_day != "night":
        raise MapError("storm needs a night map: a daytime flash looks wrong (ADR-028)")
    return storm


def map_atmosphere(directory: str | Path) -> str:
    """The map's atmosphere preset; a map from before the setting, or a folder without a map, is ``neutral``."""
    path = Path(directory).expanduser() / "map.json"
    if not path.is_file():
        return NEUTRAL
    return str(load_map(path.parent).get("atmosphere", NEUTRAL))


def store_atmosphere(directory: str | Path, atmosphere: str) -> bool:
    """Record the atmosphere on an existing map. True when it changed, so the package is out of date.

    ``neutral`` removes the key, so the map reads as it did before the setting."""
    root = Path(directory).expanduser().resolve()
    document = load_map(root)
    wanted = _atmosphere(atmosphere, str(document.get("time_of_day", TIME_OF_DAY_DEFAULT)))
    changed = document.get("atmosphere", NEUTRAL) != wanted
    document.pop("atmosphere", None)
    if wanted != NEUTRAL:
        document["atmosphere"] = wanted
    atomic_write_json(root / "map.json", document)
    return changed


def _atmosphere(atmosphere: str, time_of_day: str) -> str:
    try:
        return check_atmosphere(atmosphere, time_of_day)
    except AtmosphereError as exc:
        raise MapError(str(exc)) from exc


def store_brief(directory: str | Path, brief: str | None = None, *, ambient_ai: bool | None = None) -> None:
    """Record the dressing sentence on an existing map without rewriting the city."""
    root = Path(directory).expanduser().resolve()
    document = load_map(root)
    if brief is not None:
        document["brief"] = _brief(brief)
    if ambient_ai is not None:
        document["ambient_ai"] = bool(ambient_ai)
    atomic_write_json(root / "map.json", document)


def _brief(brief: str | None) -> str:
    text = brief.strip() if brief else ""
    if len(text) > 2000:
        raise MapError("brief must be at most 2000 characters")
    return text


def _description(title: str, latitude: float, longitude: float, description: str | None) -> str:
    text = description.strip() if description else (
        f"{title}: generated map centred at {latitude:.5f}, {longitude:.5f}. "
        "Generic facades; not a faithful reconstruction. Data: © OpenStreetMap contributors (ODbL-1.0)."
    )
    if not text or len(text) > 600:
        raise MapError("description must be 1–600 characters")
    return text


def _aoi_document(title: str, longitude: float, latitude: float, radius_m: float, margin_m: float, ring: list[list[float]]) -> dict[str, Any]:
    return {
        "type": "Feature",
        "properties": {
            "name": title,
            "margin_m": margin_m,
            "radius_m": radius_m,
            "center": [longitude, latitude],
        },
        "geometry": {"type": "Polygon", "coordinates": [ring]},
    }


def _project_document(title: str, latitude: float, longitude: float, margin_m: float, seed: int, aoi_rel: str, crs: str) -> dict[str, Any]:
    return {
        "project": {"name": title, "version": "0.1.0", "target": "onward_quest"},
        "source": {"provider": "openstreetmap", "attribution_required": True},
        "area": {
            "center": {"latitude": latitude, "longitude": longitude},
            "boundary_file": f"../{aoi_rel}",
            "margin_m": margin_m,
            "crs": crs,
        },
        "generation": {"buildings": True, "roads": True, "sidewalks": True, "facades": True, "interiors": False},
        "quality": {"detail_level": "low", "deterministic_seed": seed},
        "optimization": {"target": "quest_standalone", "generate_collisions": True, "generate_lods": True},
        "export": {"adapter": "onward", "enabled": True},
    }


def _export_document(map_id: str, title: str, description: str, texels: float, wall_m: float) -> dict[str, Any]:
    """The adapter card. Fog starts at ``wall_m``, the distance from the centre to the wall along an axis."""
    return {
        "version": "0.1.0",
        "adapter": {"name": "onward_unity", "version": "0.1.0"},
        "target": {"unity": "2021.3.23f1", "sdk": "1.15", "platform": "quest"},
        "map": {"id": map_id, "title": title, "description": description},
        "build": {
            "combine_cell_m": 100.0,
            "static_batching": False,
            "occlusion_culling": True,
            "lightmap_texels_per_m": texels,
            "lightmap_max_size": 2048,
            "lightmap_directional": False,
            "fog_enabled": True,
            "fog_start_m": wall_m,
            "fog_end_m": wall_m + _FOG_BAND_M,
            "fog_color": _FOG_COLOR,
            "interior_lights_max": _INTERIOR_LIGHTS_MAX,
            "interior_light_intensity": _INTERIOR_LIGHT_INTENSITY,
        },
    }
