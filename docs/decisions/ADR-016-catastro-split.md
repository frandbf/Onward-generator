# ADR-016 — Split OSM block footprints by Catastro building parts

Status: accepted · 30 October 2026.

## Context

In many Spanish city centres OSM maps a whole city block as one `building=*` polygon. Around 40.40657, −3.719187 (Madrid, 150 m radius) the extract has 19 building polygons; 15 are `building=apartments` polygons that each cover a block, 3 have `building:levels` and none has `height`. A real run logged `heights 13 buildings inferred 100%`: every block got one estimated height, one wall texture and one door plan. A one-storey bakery and the five-storey house next to it came out as the same box.

Catastro already describes each construction separately: one INSPIRE `BuildingPart` per volume, with its own `numberOfFloorsAboveGround` and its parcel reference (`refcat`). `cadastre match` reads those parts, but it reduces every part inside an OSM footprint to one overlap-weighted median storey count, so the block stays uniform. And `run` never called the cadastre step, so even that median was not used.

## Decision

1. **A new stage between `geo` and `heights`: `cadastre split`.** It reads the immutable `geo_scene.json` and a downloaded Catastro GML and writes a *new* directory `data/processed/cadastre/` with:
   - `geo_scene.json`: the same contract (`onward_generator.geo_scene` 0.2.0) with some building footprints replaced by sub-footprints, plus a top-level `subdivision` block (input scene hash, Catastro provenance, options, counts);
   - `heights_overrides.catastro.json`: storey overrides for every sub-building and for the matched footprints that were not split (the `cadastre match` rule);
   - `cadastre_match_report.json`: the match report plus a `split` section.
   Every later stage (`heights`, `generate`, `detail`, `dress`, layout, export) reads that scene instead of the `geo` one. They see sub-buildings as ordinary buildings, so heights, wall textures, doors, shopfronts and interiors vary per construction without changes to those stages. `geo` stays free of non-OSM data and the original scene is never rewritten.
2. **What is split.** A footprint is split when it has a Catastro part with at least one storey above ground, all its parts (including those with 0 storeys) cover at least `split_min_coverage` (0.8) of it and, after clipping to the footprint and dropping slivers, at least two *groups* remain. A group is the union of the parts that share a parcel (`refcat`) and a storey count. A sliver is a clipped piece below `min_piece_area_m2` (6 m²) or narrower than `min_piece_width_m` (2 m, an inward buffer of half that width leaves nothing). OSM and Catastro outlines are often offset by tens of centimetres; without the width test a neighbouring parcel would leave a long strip of the wrong height along the façade. Landmarks (the `heights` landmark rules) are not split; ADR-008 handles them as one volume.
3. **Geometry.** Each group is clipped to the footprint and the groups are made disjoint in id order. Area that no group covers (gaps, slivers, misalignment) is assigned piece by piece to the nearest group, ties broken by the longest shared boundary and then the id. Area covered only by parts with 0 storeys above ground (a courtyard over a basement) becomes open ground when it is not a sliver. Coordinates are snapped to 1 mm, like `geo`. Each connected polygon becomes its own sub-building: two pieces of one group that touch only at a corner would give `generate` non-manifold walls (seen on real Madrid data). A stray piece under the minimum area that ends up on its own (it touched its group only at a corner, or not at all) is left as open ground and reported as `dropped_m2`. The sub-footprints, the courtyards and those crumbs together equal the OSM footprint.
4. **Identity and provenance.** A sub-building id is `<osm id>:<smallest localId in its group>`, for example `osm-way-101:0000001AA0000A_part1`; when a group has several polygons, the largest keeps that id and the others get `.2`, `.3`, … in order of decreasing area. It keeps the parent's OSM `source`, `tags`, `clip_status` and the non-height `attributes`. It gets `parent: {"id": <osm id>}` and `subdivision: {source, provider, license, license_url, attribution, retrieved_at, refcat, parts, levels, leftover_m2}`. The parent's observed `height_m` and `levels` describe the whole block, so they move to `subdivision.parent_observed` instead of being applied to every sub-building. This subdivides a building that is in the OSM extract with authoritative data; it never adds a building outside an OSM footprint.
5. **Heights.** Each sub-building gets a `levels` override (`authoritative_levels`, confidence 0.85, or 0.7 when assigned leftover is more than a fifth of its area). `heights_report.json` gets `counts.subdivided`: OSM buildings split, sub-buildings and their storey range.
6. **Party walls and steps.** No change is needed downstream. `generate` extrudes every edge of every footprint, so the taller neighbour's side wall above the lower roof is closed. `detail` already marks an edge with another footprint just outside it as a party wall: no doors, windows or shopfronts on shared edges.
7. **POIs.** `detail` matches a shop or amenity point to the footprint that *contains* it; only a point inside no footprint falls back to the 2 m distance rule. A bakery inside a one-storey sub-building gives that sub-building the shopfront, not the five-storey neighbour 1 m away.
8. **`run` uses Catastro by default in Spain.** When the map uses EPSG:25830 (used between 6° W and 0°), its centre is between latitudes 35.85° and 43.8° and the download box fits the 1 km² limit, `run` adds `catastro` (download, network, retried like Overpass: three times on 429, 5xx and dropped connections, waits doubling from 8 s) and `cadastre` (split) after `geo`. `run --no-catastro` stores `"catastro": false` in `map.json` and keeps the old chain; `--catastro` turns it back on. Outside Spain nothing changes.

## Rejected alternatives

- **Split inside `geo`.** `geo` would mix a second source into the OSM normalisation and need the network.
- **Split inside `heights`.** `heights` writes one record per scene building. Every later stage reads footprints from the scene, so sub-footprints in `heights.json` would be ignored by `generate` and `detail`.
- **Per-part heights on one block footprint (stepped roof only).** One building would still get one texture, one door plan and one shop decision.
- **Replacing the OSM footprint by the Catastro parts.** The outline would change to Catastro's and buildings Catastro has but OSM lacks could appear. The OSM outline stays the envelope.

## Consequences

- More buildings per map: draw calls and triangles rise with the number of sub-buildings. Shared walls add hidden faces back to back.
- The Catastro area test is coarse: a CRS and a latitude band. It includes a strip of south-western France and the Basque Country and Navarre, which have their own cadastres. There the service returns no parts and the stage passes the scene through unchanged. Galicia, Catalonia, the Balearic and Canary Islands use another UTM zone and stay without Catastro until the download supports their CRS.
- A map larger than about 900 m across does not fit one request and keeps the old chain.
- Catastro geometry now shapes the exported meshes, not only a number. That is a transformation the Catastro licence allows; the attribution is carried as before (`docs/legal/data-sources.md`).
