# ADR-024 — Record a building typology and construction era

Status: proposed · 1 November 2026. Second increment of the [facades, typology and triangle budget plan](../plans/2026-10-31-facades-typology-budget.md). Builds on [ADR-016](ADR-016-catastro-split.md), [ADR-018](ADR-018-source-plugins.md) and [ADR-022](ADR-022-catastro-use.md). It changes nothing that is drawn.

## Context

- A building's type today is one of five templates plus a facade use that picks a palette. A nineteenth-century block, a 1960s brick block and a terraced house share one grammar and differ only in colour.
- Studies of building stock (TABULA/EPISCOPE, EUBUCCO) and cadastres classify on two axes: construction period and form. Period says the most about how a facade looks.
- The period is often already downloaded. The Catastro buildings file that `cadastre split --buildings` reads has `bu-core2d:dateOfConstruction`, and OSM has `start_date`. The form can be measured from the footprint in any city.
- A later increment will let the typology choose the grammar and the atlas strips. Doing that blind would mean tuning rules against pictures, so the classification is recorded and measured first.

## Decision

1. **Era: observed or nothing.** An OSM date (`building:start_date`, `start_date`, `construction_date`: a leading four-digit year), then the Catastro `construction_year`, in the order of `config/sources.json` `evidence`. An era is never estimated. A date with no leading year is `era_unparsable` (info). Periods: `pre_1900`, `1900_1939`, `1940_1959`, `1960_1979`, `1980_1999`, `2000_plus`.
2. **Morphology from the footprint, in the metric CRS.** Area, perimeter, compactness, elongation and shared-wall share. The share reuses the facade probe (`refine_exposure`), so "against a neighbour" means the same thing as for party walls. Its minimum run is 0.1 m rather than the facade's 2.5 m: a real gap between neighbours stays exposed, and the few centimetres a probe leaves at the end of a wall do not count. With no minimum those stubs counted as exposed and a wall against one neighbour read 0.2487 instead of 0.25.
3. **Ordered rules, in a validated configuration.** `config/typology.default.json` and its schema: `landmark`, `other` (minor category), `civic`, `industrial_shed`, `tower`, `historic_core`, `row_house`, `detached_house`, `closed_block`, `open_block`, `other`. The first match wins; `docs/07-detail.md` has the table. Every threshold is a modelling assumption.
4. **Generic first.** Everything but the Catastro year works from OSM and geometry, in any city. Without an era, `historic_core` does not fire and the building falls to its form. The Catastro year arrives through the existing plugin record, `catastro_use`.
5. **One record per parcel.** The parts of a parcel share the record, measured on their union, under the same key as the facade use and look (ADR-016).
6. **Provenance.** `facade.typology` is `{"typology", "origin": "estimated", "rule", "inputs", "era"}`. The typology is always `estimated`; the era keeps `origin: observed`, its source and its evidence.
7. **Contracts.** Additive. A split scene is `geo_scene` 0.3.0 (`catastro_use.construction_year`, `subdivision.uses.with_construction_year`). `scene_detail` goes to 0.3.0 with `facade.typology`. `detail_report.json`, also 0.3.0, gains `counts.typologies`, `counts.eras` and `counts.era_sources`, for core buildings. `run` prints them.

## Measured

`run --no-unity`, 1 November 2026.

| Map | Core buildings | Observed era | Typologies | Eras |
| --- | --- | --- | --- | --- |
| Murcia 37.99521, −1.137511, r90, with Catastro | 22 | 22 (Catastro 22, OSM 0) | `open_block` 13 (slab 9, point 4), `civic` 5, `other` 4 | `1960_1979` 13, `1980_1999` 8, `2000_plus` 1 |
| Kraków 50.0614, 19.9366, r120, no plugin | 43 | 0 | `landmark` 16, `closed_block` 10, `row_house` 10, `other` 6 (minor 3, no rule 3), `open_block` 1 | unknown 43 |

`scene_visual.glb` of `detail` on Murcia had the same SHA-256 as before this change (`60afbdc47e4d1ce9…`), measured before the flat openings of ADR-025 joined main, so nothing drawn moved.

Reading these:

- **Where a cadastre exists, the era is complete.** Murcia has one for every core building, and none from OSM.
- **Outside one, the era is usually missing.** Kraków's old town has no `start_date` on any of its 43 core buildings, and so no `historic_core`: its attached medieval and later blocks read as `closed_block` and `row_house`. The rules are right about the form, and the period is simply not known. A later increment must work without an era, or with a regional default that says it is one.
- **A third of Kraków is `landmark`,** because `heights.json` marks that many in the old town. They keep the landmark placeholder either way.
- **Nobody has looked at these on the ground.** A distribution shows the rules fire, not that they are right. Murcia reading mostly `open_block` is plausible for its 1960s and 1970s blocks but has not been checked against the buildings.

## Rejected alternatives

- **A trained classifier.** Needs labelled data per region, is harder to keep deterministic, and answers no question the rules cannot answer yet.
- **Estimating the era from form or neighbourhood.** It would look like a fact downstream. An unknown stays unknown.
- **Typologies per country in the core.** The classes are generic; a country adds evidence through a plugin, not a class.
- **Reusing `min_facade_length_m` for the share.** A 2 m gap between two neighbours would count as shared wall, and a row with gaps would read as attached.
- **Choosing the grammar in this change.** Two changes at once could not be measured apart.

## Consequences

- Every new scene carries the era and the form of each building, ready for the grammar and atlas selection of the next increment.
- `detail` takes one more configuration file, and its hash enters the manifest.
- Thresholds were set from reasoning and the two maps above. They will need revisiting when the typology starts to change what is drawn.
