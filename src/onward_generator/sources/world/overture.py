"""``overture``: Overture Maps places and buildings, for any city (ADR-021), an adapter over ``onward_generator.overture``.

Two stages: ``overture`` (``overture fetch``, the download) and ``overture-match``
(``overture match``, offline, reading the latest scene). Its height overrides do
not join the ADR-018 merge: a merged ``height_m`` is an authoritative override
that outranks an OSM ``height``. They go to ``heights --secondary-overrides``,
below an OSM ``height`` and the merged overrides, above OSM ``building:levels``.
Its places go to ``detail --places``, ranked as shop evidence by ``config/sources.json``
``evidence`` (ADR-022). With ``map.json`` ``"overture_tints": true`` (plan C2, off by default)
the match also writes ``overture_tints.json`` for ``detail --overture-tints``; that file is then
the stage's marker, so switching the option on runs the match again.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from onward_generator.next_step import overture_fetch_command, overture_match_command
from onward_generator.sources.plugin import Kind, LicenceRecord, SourceStage, SourceSteps

OBLIGATIONS = (
    "Buildings: ODbL 1.0 (OpenStreetMap, Microsoft ML footprints) and CC BY 4.0 (Esri, Google Open Buildings, IGN). "
    "Places: each record's licence, CDLA Permissive 2.0, Apache 2.0 (Foursquare, with its NOTICE) or CC0 1.0. "
    "Attribute every dataset used, in the wording of https://docs.overturemaps.org/attribution/ (docs/legal/data-sources.md)."
)


PLUGIN_ID = "overture"


@dataclass(frozen=True, slots=True)
class OverturePlugin:
    id: str = PLUGIN_ID
    licence: LicenceRecord = LicenceRecord(
        spdx="ODbL-1.0",
        name="Open Database License 1.0 (Overture buildings); places under each record's licence",
        url="https://docs.overturemaps.org/attribution/",
        attribution="Overture Maps Foundation, overturemaps.org",
        obligations=OBLIGATIONS,
    )
    produces: frozenset[Kind] = frozenset({"height_m", "levels", "use"})

    def covers(self, longitude: float, latitude: float) -> bool:
        return -180.0 <= longitude <= 180.0 and -90.0 <= latitude <= 90.0

    def steps(self, root: Path, scene: Path) -> SourceSteps:
        from onward_generator.maps.workspace import overture_tints_enabled  # the workspace imports the registry, which imports this plugin

        raw = root / "data" / "raw"
        tints = overture_tints_enabled(root)
        match = overture_match_command(scene, root, tints=tints).argv
        out = Path(match[match.index("--out") + 1])
        buildings = raw / "overture_buildings.parquet"
        tints_file = out / "overture_tints.json" if tints else None
        return SourceSteps(
            stages=(
                SourceStage("overture", overture_fetch_command(root).argv, buildings, (), download=True),
                SourceStage("overture-match", match, tints_file or out / "overture_places.json", (scene, buildings)),
            ),
            overrides=None,
            secondary_overrides=out / "heights_overrides.overture.json",
            places=out / "overture_places.json",
            tints=tints_file,
        )
