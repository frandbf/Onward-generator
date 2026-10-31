"""The registered source plugins, their configuration, and which ones a map uses (ADR-018).

Discovery is this explicit tuple, not an import scan: the order is the order
``run`` adds the stages, so it is deterministic. A plugin that writes a new
scene (``footprints``) comes before the ones that read the scene.
``config/sources.json`` holds the licence allowlist and the one priority
that ranks height sources; nothing else ranks them.
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
from onward_generator.sources.plugin import OVERRIDE_KINDS, SourceError, SourcePlugin

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = REPO_ROOT / "config" / "sources.json"
SCHEMA = "onward_generator.sources"

PLUGINS: tuple[SourcePlugin, ...] = (CatastroPlugin(),)


@dataclass(frozen=True, slots=True)
class SourceConfig:
    licence_allowlist: frozenset[str]
    priority: tuple[str, ...]


def source_config(document: Mapping[str, Any]) -> SourceConfig:
    """Validate ``config/sources.json``: every plugin that writes overrides must have a rank."""
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
    return SourceConfig(frozenset(allowlist), tuple(priority))


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
