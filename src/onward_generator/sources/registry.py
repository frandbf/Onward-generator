"""The registered source plugins, their configuration, and which ones a map uses (ADR-018).

Discovery is this explicit tuple, not an import scan: the order is the order
``run`` adds the stages, so it is deterministic. A plugin that writes a new
scene (``footprints``) comes before the ones that read the scene.
``config/sources.json`` holds the licence allowlist, the one priority
that ranks height sources and the one ranking of shop evidence (ADR-022);
nothing else ranks them.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from onward_generator.contracts import is_schema
from onward_generator.sources.es.catastro import CatastroPlugin
from onward_generator.sources.es.pnoa_ndsm import PnoaNdsmPlugin
from onward_generator.sources.fr.lidar_hd import LidarHdPlugin
from onward_generator.sources.plugin import OVERRIDE_KINDS, SourceError, SourcePlugin
from onward_generator.sources.world.overture import OverturePlugin

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = REPO_ROOT / "config" / "sources.json"
SCHEMA = "onward_generator.sources"
OSM_EVIDENCE = "osm"
"""The evidence rank of the OSM shop, amenity, office and craft POIs and of a commercial building category."""

PLUGINS: tuple[SourcePlugin, ...] = (CatastroPlugin(), PnoaNdsmPlugin(), LidarHdPlugin(), OverturePlugin())


@dataclass(frozen=True, slots=True)
class SourceConfig:
    licence_allowlist: frozenset[str]
    priority: tuple[str, ...]
    evidence: tuple[str, ...]
    """The ranking of ground-floor shop evidence: ``osm`` (POIs and the building category) and the ``use`` plugins (ADR-022)."""


def source_config(document: Mapping[str, Any]) -> SourceConfig:
    """Validate ``config/sources.json``: every plugin that writes overrides must have a rank, every ``use`` plugin an evidence rank."""
    if not is_schema(document.get("schema"), SCHEMA):
        raise SourceError(f"sources config: schema must be {SCHEMA}")
    allowlist = document.get("licence_allowlist")
    priority = document.get("priority")
    if not isinstance(allowlist, list) or not all(isinstance(item, str) and item for item in allowlist):
        raise SourceError("sources config: licence_allowlist must be a list of SPDX ids")
    if not isinstance(priority, list) or not all(isinstance(item, str) and item for item in priority):
        raise SourceError("sources config: priority must be a list of plugin ids")
    if len(set(priority)) != len(priority):
        raise SourceError("sources config: priority names a plugin twice")
    unranked = sorted(p.id for p in PLUGINS if p.produces & OVERRIDE_KINDS and p.id not in priority)
    if unranked:
        raise SourceError(f"sources config: priority has no rank for {', '.join(unranked)}")
    evidence = document.get("evidence")
    if not isinstance(evidence, list) or not all(isinstance(item, str) and item for item in evidence):
        raise SourceError("sources config: evidence must be a list of source ids")
    if len(set(evidence)) != len(evidence):
        raise SourceError("sources config: evidence names a source twice")
    unranked = sorted(({OSM_EVIDENCE} | {p.id for p in PLUGINS if "use" in p.produces}) - set(evidence))
    if unranked:
        raise SourceError(f"sources config: evidence has no rank for {', '.join(unranked)}")
    return SourceConfig(frozenset(allowlist), tuple(priority), tuple(evidence))


@cache
def load_source_config(path: Path = CONFIG_PATH) -> SourceConfig:
    return source_config(json.loads(path.read_text(encoding="utf-8")))


def plugin_by_id(plugin_id: str) -> SourcePlugin:
    for plugin in PLUGINS:
        if plugin.id == plugin_id:
            return plugin
    raise SourceError(f"unknown source {plugin_id!r}. Known: {', '.join(p.id for p in PLUGINS)}")


def check_licence(plugin: SourcePlugin, config: SourceConfig) -> None:
    """Refuse a plugin whose licence is not on the allowlist. Called before any fetch."""
    if plugin.licence.spdx not in config.licence_allowlist:
        raise SourceError(f"{plugin.id}: licence {plugin.licence.spdx} is not in licence_allowlist (config/sources.json)")


def check_secondary_rank(primary: Sequence[str], secondary: Sequence[str], config: SourceConfig) -> None:
    """Keep ``priority`` the one ranking when a plugin's overrides are secondary (Overture, ADR-021).

    ``heights`` reads secondary overrides below every authoritative override, so a plugin that writes
    them must rank below every plugin of the map that writes authoritative ones.
    """
    rank = {plugin_id: index for index, plugin_id in enumerate(config.priority)}
    for low in secondary:
        above = sorted(plugin_id for plugin_id in primary if rank[plugin_id] > rank[low])
        if above:
            raise SourceError(
                f"sources config: priority ranks {low} above {', '.join(above)}, "
                f"but heights reads the secondary overrides of {low} below theirs"
            )


@dataclass(frozen=True, slots=True)
class SourceChoice:
    """What ``run`` does with one plugin for one map."""

    plugin: SourcePlugin
    covers: bool
    switched_off: bool

    @property
    def runs(self) -> bool:
        return self.covers and not self.switched_off


def switch(document: Mapping[str, Any], plugin_id: str) -> bool | None:
    """The map's ``--source`` / ``--no-source`` choice for a plugin; None when it has none.

    ``sources`` in map.json holds the choices. A map written before plugins
    keeps its ``"catastro": false`` for ``es.catastro``.
    """
    stored = (document.get("sources") or {}).get(plugin_id)
    if stored is None and plugin_id == "es.catastro":
        stored = document.get("catastro")
    return None if stored is None else bool(stored)


def choices(document: Mapping[str, Any]) -> list[SourceChoice]:
    """Every registered plugin, in registry order, with its coverage of the map centre and its switch."""
    center = document["center"]
    longitude, latitude = float(center["longitude"]), float(center["latitude"])
    return [
        SourceChoice(plugin, plugin.covers(longitude, latitude), switch(document, plugin.id) is False)
        for plugin in PLUGINS
    ]


def describe(found: Sequence[SourceChoice]) -> str:
    """``sources  es.catastro  (off: …)  (not here: …)``: the line ``run`` prints."""
    running = [c.plugin.id for c in found if c.runs]
    parts = ["sources", ", ".join(running) if running else "none"]
    off = [c.plugin.id for c in found if c.covers and c.switched_off]
    absent = [c.plugin.id for c in found if not c.covers]
    if off:
        parts.append(f"(off: {', '.join(off)})")
    if absent:
        parts.append(f"(not here: {', '.join(absent)})")
    return "  ".join(parts)
