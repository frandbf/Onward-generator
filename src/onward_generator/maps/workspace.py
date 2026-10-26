"""Create an isolated map workspace from a name and a WGS84 centre.

The folder holds the project configuration, the export card and a square area
of interest measured in metres. Later stages write under ``data/processed``.
Belluga is not modified. The Quest local slot still holds one map at a time.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

from shapely import box

from onward_generator.config.validation import validate_config_file
from onward_generator.contracts import is_schema
from onward_generator.export.package import ExportError, load_export_config
from onward_generator.geo.aoi import AoiError, load_aoi_geojson
from onward_generator.geo.projection import ProjectionError, frame_for_origin
from onward_generator.ai.providers import Provider
from onward_generator.io import atomic_write_bytes, atomic_write_json
from onward_generator.materials.library_plan import select_texture_plan

MAP_SCHEMA = "onward_generator.map"
MAP_VERSION = "0.1.0"
_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")
_RADIUS_MIN_M = 50.0
_RADIUS_MAX_M = 1500.0
_MARGIN_MAX_M = 500.0
_TEXELS_DEFAULT = 10.0
_TEXELS_MAX = 40.0


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
    lightmap_texels_per_m: float = _TEXELS_DEFAULT,
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
    atomic_write_json(root / export_rel, _export_document(map_id, title, description, texels))
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
        "center": {"latitude": latitude, "longitude": longitude},
        "radius_m": radius_m,
        "margin_m": margin_m,
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


def _export_document(map_id: str, title: str, description: str, texels: float) -> dict[str, Any]:
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
        },
    }
