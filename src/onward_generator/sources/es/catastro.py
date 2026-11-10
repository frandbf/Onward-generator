"""``es.catastro``: the Catastro INSPIRE BU plugin, an adapter over ``onward_generator.cadastre``.

The stages are the ones ``run`` always used: ``catastro`` (``cadastre download``)
and ``cadastre`` (``cadastre split``). The download writes the parts and, beside
them, the parcels with their current use (ADR-022); the second file is the marker,
so a map downloaded before it fetches both again.
Coverage is ``config/catastro_zones.json`` (docs/05-cadastre.md#coverage).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from onward_generator.cadastre.acquire import CATASTRO_BU, LICENSE_NOTES, buildings_path
from onward_generator.cadastre.zones import catastro_zone
from onward_generator.next_step import cadastre_split_command, catastro_command
from onward_generator.sources.plugin import Kind, LicenceRecord, SourceStage, SourceSteps

OVERRIDES_FILE = "heights_overrides.catastro.json"
PLUGIN_ID = "es.catastro"


@dataclass(frozen=True, slots=True)
class CatastroPlugin:
    id: str = PLUGIN_ID
    licence: LicenceRecord = LicenceRecord(
        spdx=CATASTRO_BU.license,
        name="Creative Commons Attribution 4.0 (Catastro INSPIRE licence v1.0)",
        url=CATASTRO_BU.license_url,
        attribution=CATASTRO_BU.attribution,
        obligations=LICENSE_NOTES,
    )
    produces: frozenset[Kind] = frozenset({"footprints", "levels", "use"})
    opt_in: bool = False

    def covers(self, longitude: float, latitude: float) -> bool:
        return catastro_zone(longitude, latitude) is not None

    def steps(self, root: Path, scene: Path) -> SourceSteps:
        gml = root / "data" / "raw" / "catastro.gml"
        uses = buildings_path(gml)
        fetch = catastro_command(scene, gml).argv
        split = cadastre_split_command(scene, gml).argv
        out = Path(split[split.index("--out") + 1])
        return SourceSteps(
            stages=(
                SourceStage("catastro", fetch, uses, (scene,), download=True),
                SourceStage("cadastre", split, out / "geo_scene.json", (gml, uses)),
            ),
            overrides=out / OVERRIDES_FILE,
            scene=out / "geo_scene.json",
        )
