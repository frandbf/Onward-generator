# ADR-008 — Landmark volumes: an observed tower, clipped to the footprint (phase 07.2)

Status: proposed (visual review on the Quest is still open).

## Context

The cathedral was a 15 m prism from Catastro storeys and was not recognisable. OSM records the tower as a point (`osm-node-7391265244`) with `height=93` (observed). Geography is not invented. Observed, estimated, and generated attributes stay distinct. The chosen form is a documented volume, not a photographed model.

## Decision

1. **The building footprint does not move.** The nave stays the OSM polygon at the Catastro height (observed levels).
2. **A tower exists only when an OSM tower point lies inside that footprint and has a parseable `height`.** With no height, no volume is invented.
3. **The prism is clipped to the footprint.** The 12 m square (6 m half-width, a modelling assumption) cannot leave the building. Area and method are `estimated` in the manifest.
4. **Its own collision**, separate from the visual mesh: a closed prism.
5. **A more saturated palette** in `detail.default.json` (ochre, cream, warm stone, tile). It stays a flat colour (`baseColorFactor`) at this stage. Photographic textures are a later, licensed stage (ADR-009).

## Rejected alternatives

- Raising the nave to an estimated 25–30 m. That contradicts the Catastro storeys.
- An invented tower polygon outside the footprint. That would add ground OSM does not have.
- Street View or unlicensed photographs.

## Consequences

- The cathedral reads as a 93 m tower. It is not the real baroque tower.
- Applying it means a new `detail` directory and a new export package. `scene_detail` and an existing package are not overwritten.

## Amendment of 9 November 2026 (plan D6): an observed LoD2 volume from open data

Status: proposed (the headset session of block D, `docs/09-perf.md` *D6*, is still open).

An observed LoD2 volume from an open dataset on the licence allowlist may replace the placeholder shell and the tower prism of a landmark building. The first such dataset is the Ayuntamiento de Madrid municipal 3D building model (CC BY 4.0, `docs/legal/data-sources.md`), downloaded by the opt-in source plugin `es.madrid_lod2` ([ADR-018](ADR-018-source-plugins.md)) and drawn by `detail --lod2-landmarks` (`docs/07-detail.md`, *LoD2 landmarks*).

1. **Opt-in.** Nothing changes unless the map switches the plugin on (`run --source es.madrid_lod2`). Without it `detail` reads nothing and writes the same bytes.
2. **Only a landmark of the core.** A building `heights.json` flags `landmark` (`docs/04-heights.md`) that lies in the playable core. A scenic or mid landmark keeps its shell (ADR-017).
3. **The footprint still does not move, and there is no volume without a match.** The LoD2 groups whose plan hull lies at least half on the OSM footprint are its volume, if together they cover at least 0.6 of it. Otherwise the placeholder and the tower stay and an info issue says so. The building keeps its id, its footprint and its height record.
4. **Clipped to the footprint.** A vertex up to 1 m outside moves onto the footprint edge; a farther one drops its triangles, and so does a triangle whose plan shape leaves the footprint. Nothing is drawn outside the OSM footprint (`clip.outside_area_m2` is 0). The part of the footprint no group covers gets a floor at street level.
5. **On the map's flat ground.** The ground is the highest base of the outer walls, so no wall floats; the walls on the lower side of a sloping street sink and end at the street. Estimated, and recorded.
6. **Capped.** At most 2000 visual triangles per landmark, by in-house vertex clustering (no new dependency); over the cap after four passes, the placeholder stays.
7. **Its own collision**, separate from the visual mesh: one closed prism per matched group, its plan hull clipped to the footprint, up to the group's top.
8. **Existing materials only**: the landmark wall and roof materials (`PP_Opaque_Simple_Lit`, albedo only; ADR-009, ADR-020).
9. **Provenance.** Each landmark's `lod2` block names the provider, the licence, the block codes and OBJ groups, the data date, `geometry.origin: observed`, and the match, ground, clip and simplification as `estimated` with their parameters. The facade is still not the real one (`representative_of_real_facade: false`); the volume is (`representative_of_real_volume: true`). The CC BY attribution reaches the package `attribution` list next to the OSM line; the length-capped map description may not show it (checked on the headset).

It does not change the height priority: the plugin writes no overrides and has no rank in `config/sources.json`.

Rejected: drawing the LoD2 volume beside or over the placeholder (z-fighting, double collision); reprojecting another CRS inside `detail` (the dataset and the Madrid maps are both EPSG:25830, and any other pair is refused); a mesh-simplification library (a new dependency); the whole-city I3S/SLPK (2.2 GB).
