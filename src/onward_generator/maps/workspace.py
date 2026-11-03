"""Create an isolated map workspace from a name and a WGS84 centre.

The folder holds the project configuration, the export card and a square area
of interest measured in metres. Later stages write under ``data/processed``.
Belluga is not modified. The Quest local slot still holds one map at a time.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from shapely import box

from onward_generator.ai.providers import Provider
from onward_generator.config.validation import validate_config_file
from onward_generator.contracts import is_schema
from onward_generator.export.package import ExportError, load_export_config
from onward_generator.geo.aoi import AoiError, load_aoi_geojson
from onward_generator.geo.projection import LocalFrame, ProjectionError, frame_for_origin
from onward_generator.io import atomic_write_json
from onward_generator.materials.library_plan import select_texture_plan
from onward_generator.sources.registry import choices, plugin_by_id
from onward_generator.tiers import MIN_CORE_HALF_SIDE_M

MAP_SCHEMA = "onward_generator.map"
MAP_VERSION = "0.1.0"
_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")
_RADIUS_MIN_M = 50.0
_RADIUS_MAX_M = 1500.0
_MARGIN_MAX_M = 500.0
TIMES_OF_DAY = ("day", "night")
TIME_OF_DAY_DEFAULT = "day"
_TEXELS_DEFAULT = 10.0
# The playable core (ADR-017): a square of this half-side around the centre keeps full detail.
CORE_RADIUS_DEFAULT_M = 150.0
_TEXELS_MAX = 40.0
# Linear fog from the wall outwards (ADR-014). The band is a look, not a measurement.
_FOG_BAND_M = 600.0
_FOG_COLOR = "#C8CDD2"


class MapError(ValueError):
    """The map folder cannot be created from these inputs."""


def create_map(
    directory: str | Path,
    *,
    map_id: str,
    name: str,
    latitude: float,
    longitude: float,
    radius_m: float = 400.0,
    margin_m: float = 30.0,
    seed: int | None = None,
    description: str | None = None,
    brief: str | None = None,
    ambient_ai: bool = False,
    panoramax: bool = False,
    time_of_day: str = TIME_OF_DAY_DEFAULT,
    storm: bool = False,
    lightmap_texels_per_m: float = _TEXELS_DEFAULT,
    core_radius_m: float = CORE_RADIUS_DEFAULT_M,
    overwrite: bool = False,
    texture_provider: Provider | None = None,
) -> dict[str, Any]:
    """Write ``map.json``, configs and a metric AOI. The CLI then fetches OSM."""
    root = Path(directory).expanduser().resolve()
    map_id = _map_id(map_id)
    title = _title(name)
    radius_m = _radius(radius_m)
    margin_m = _margin(margin_m)
    seed = _seed(latitude, longitude, seed)
    description = _description(title, latitude, longitude, description)
    brief = _brief(brief)
    texels = _texels(lightmap_texels_per_m)
    core = _core(core_radius_m, radius_m)
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
        brief=brief, seed=seed, provider=texture_provider,
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
        "center": {"latitude": latitude, "longitude": longitude},
        "radius_m": radius_m,
        "margin_m": margin_m,
        "core_radius_m": core,
        "crs": frame.crs,
        "seed": seed,
        "paths": {
            "project": project_rel,
            "export": export_rel,
            "aoi": aoi_rel,
            "raw": "data/raw",
            "processed": "data/processed",
        },
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
    that covers it.
    """
    try:
        document = load_map(directory)
    except MapError:
        return False
    return any(choice.runs for choice in choices(document) if choice.plugin.id == plugin_id)


def facade_options(directory: str | Path) -> list[str]:
    """``materials apply`` flags from this map's ``map.json`` ``"facades"`` block (ADR-019).

    ``{"mode": "trim" | "legacy", "style": "<name>" | "auto"}``; either key may be left
    out. A map without the block, or without a ``map.json``, gets no flags and the stage
    defaults (``trim``, ``auto``). A block of another shape is refused.
    """
    try:
        document = load_map(directory)
    except MapError:
        return []
    block = document.get("facades")
    if block is None:
        return []
    if not isinstance(block, dict) or not set(block) <= {"mode", "style"} or not all(isinstance(v, str) for v in block.values()):
        raise MapError('map.json "facades" must be {"mode": "trim" | "legacy", "style": "<name>" | "auto"}')
    flags = ["--facades", block["mode"]] if "mode" in block else []
    return flags + (["--facade-style", block["style"]] if "style" in block else [])


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
    return document


def _square_ring(frame: LocalFrame, radius_m: float) -> list[list[float]]:
    local = box(-radius_m, -radius_m, radius_m, radius_m)
    geographic = frame.to_wgs84(local)
    if not frame.within_area_of_use(geographic):
        raise MapError(f"a {radius_m:g} m square around the centre leaves the area of use of {frame.crs}")
    ring = [[round(float(x), 7), round(float(y), 7)] for x, y in geographic.exterior.coords]
    ring[-1] = list(ring[0])
    return ring


def refresh_texture_plan(directory: str | Path, *, texture_provider: Provider | None = None) -> Path:
    """Replace only ``config/texture_plan.json`` on a map that already exists."""
    root = Path(directory).expanduser().resolve()
    document = load_map(root)
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
) -> Path:
    document = select_texture_plan(
        name=name,
        map_id=map_id,
        latitude=latitude,
        longitude=longitude,
        brief=brief,
        seed=seed,
        provider=provider,
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


def _core(core_radius_m: float, radius_m: float) -> float | None:
    """The playable core half-side, or None: 0, or a core at or past the radius, plays the whole map."""
    if isinstance(core_radius_m, bool) or not isinstance(core_radius_m, (int, float)):
        raise MapError("core_radius_m must be a number")
    if core_radius_m == 0 or core_radius_m >= radius_m:
        return None
    if not MIN_CORE_HALF_SIDE_M <= core_radius_m:
        raise MapError(f"core_radius_m must be 0 or at least {MIN_CORE_HALF_SIDE_M:g}")
    return float(core_radius_m)


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
            "occlusion_culling": False,
            "lightmap_texels_per_m": texels,
            "lightmap_max_size": 2048,
            "lightmap_directional": False,
            "fog_enabled": True,
            "fog_start_m": wall_m,
            "fog_end_m": wall_m + _FOG_BAND_M,
            "fog_color": _FOG_COLOR,
        },
    }
