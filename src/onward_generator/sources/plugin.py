"""The interface every optional data source implements (ADR-018).

A source plugin is a dataset that only covers part of the world: a national
cadastre, a LiDAR height model. The generic core (OSM, ``geo``, ``heights``)
never depends on one. ``run`` asks every registered plugin whether it covers
the map centre and adds the stages of those that do and are not switched off.
An opt-in plugin runs only where ``map.json`` switches it on (ADR-018, plan D4).

A plugin never imports the exporter. It writes into ``data/raw`` (the
download and its ``.source.json``) and ``data/processed`` (an overrides file,
and for a plugin that subdivides footprints a new ``geo_scene.json``).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

Kind = Literal["footprints", "height_m", "levels", "use"]
"""What a plugin contributes. ``footprints``: it writes a new scene that later stages read.
``height_m`` and ``levels``: it writes a ``heights_overrides.<id>.json`` (``config/heights.overrides.schema.json``).
``use``: it labels the buildings of its scene with an observed use that ``detail`` reads as shop evidence (ADR-022)."""

OVERRIDE_KINDS: frozenset[Kind] = frozenset({"height_m", "levels"})


class SourceError(ValueError):
    """A plugin, its configuration or its output is invalid."""


@dataclass(frozen=True, slots=True)
class LicenceRecord:
    """The licence a plugin's data is used under. ``spdx`` is checked against the allowlist before any fetch."""

    spdx: str
    name: str
    url: str
    attribution: str
    obligations: str


@dataclass(frozen=True, slots=True)
class SourceStage:
    """One ``run`` stage of a plugin: a CLI call and the file that shows it finished.

    ``download`` marks the stage that writes a raw file and its ``.source.json``;
    a repeated download passes ``--overwrite`` when either exists. ``inputs`` are
    the files the marker must be at least as new as.
    """

    name: str
    argv: tuple[str, ...]
    marker: Path
    inputs: tuple[Path, ...]
    download: bool = False


@dataclass(frozen=True, slots=True)
class SourceSteps:
    """A plugin's stages for one map. ``scene`` is the scene later stages read, when the plugin writes one."""

    stages: tuple[SourceStage, ...]
    overrides: Path | None
    scene: Path | None = None
    secondary_overrides: Path | None = None
    """Third-party overrides that ``heights --secondary-overrides`` ranks below an OSM ``height`` (Overture, ADR-021)."""
    places: Path | None = None
    """Third-party POIs that ``detail --places`` ranks as shop evidence by ``config/sources.json`` ``evidence`` (Overture, ADR-021)."""
    tints: Path | None = None
    """Third-party colours that ``detail --overture-tints`` reads below the OSM wall and roof tags (Overture, plan C2; opt-in)."""
    roof_spread: Path | None = None
    """A raster plugin's report, whose nDSM spreads ``detail --lidar-roofs`` reads as flat or pitched roofs (plan C3; opt-in)."""


class SourcePlugin(Protocol):
    """A dataset that covers part of the world. See ADR-018."""

    @property
    def id(self) -> str:
        """``<country code>.<dataset>``, for example ``es.catastro``. Used in map.json, ``--source`` and the priority."""
        ...

    @property
    def licence(self) -> LicenceRecord: ...

    @property
    def produces(self) -> frozenset[Kind]: ...

    @property
    def opt_in(self) -> bool:
        """True: the plugin runs only on a map that switches it on (``run --source <id>``). False: wherever it covers the centre."""
        ...

    def covers(self, longitude: float, latitude: float) -> bool:
        """True when the map centre is inside the plugin's coverage. A static table in the repository, no network."""
        ...

    def steps(self, root: Path, scene: Path) -> SourceSteps:
        """The stages for the map at ``root``, reading ``scene`` (the latest scene before this plugin)."""
        ...
