"""``es.catastro``: the Catastro INSPIRE BU plugin, an adapter over ``onward_generator.cadastre``.

The stages are the ones ``run`` always used: ``catastro`` (``cadastre download``)
and ``cadastre`` (``cadastre split``). Nothing in the cadastre module changes.
Coverage is ``config/catastro_zones.json`` (docs/05-cadastre.md#coverage).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from onward_generator.cadastre.acquire import CATASTRO_BU, LICENSE_NOTES
from onward_generator.cadastre.zones import catastro_zone
from onward_generator.next_step import cadastre_split_command, catastro_command
from onward_generator.sources.plugin import Kind, LicenceRecord, SourceStage, SourceSteps

OVERRIDES_FILE = "heights_overrides.catastro.json"


@dataclass(frozen=True, slots=True)
class CatastroPlugin:
    id: str = "es.catastro"
    licence: LicenceRecord = LicenceRecord(
        spdx=CATASTRO_BU.license,
        name="Creative Commons Attribution 4.0 (Catastro INSPIRE licence v1.0)",
        url=CATASTRO_BU.license_url,
        attribution=CATASTRO_BU.attribution,
        obligations=LICENSE_NOTES,
    )
    produces: frozenset[Kind] = frozenset({"footprints", "levels"})

    def covers(self, longitude: float, latitude: float) -> bool:
        return catastro_zone(longitude, latitude) is not None

    def steps(self, root: Path, scene: Path) -> SourceSteps:
        gml = root / "data" / "raw" / "catastro.gml"
        fetch = catastro_command(scene, gml).argv
        split = cadastre_split_command(scene, gml).argv
        out = Path(split[split.index("--out") + 1])
        return SourceSteps(
            stages=(
                SourceStage("catastro", fetch, gml, (scene,), download=True),
                SourceStage("cadastre", split, out / "geo_scene.json", (gml,)),
            ),
            overrides=out / OVERRIDES_FILE,
            scene=out / "geo_scene.json",
        )
