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
