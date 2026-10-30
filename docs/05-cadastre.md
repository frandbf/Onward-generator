# Storeys from Catastro (INSPIRE BU)

Module `onward_generator.cadastre`. Licence obligations are in `docs/legal/data-sources.md`.

Download and processing are separate. Only `download` uses the network. The service covers Spain. A map outside Spain skips this step. `run` downloads and splits by default in mainland Spain ([In `run`](#in-run)).

```bash
.venv/bin/python -m onward_generator cadastre download \
  --scene maps/<id>/data/processed/geo/geo_scene.json \
  --out   maps/<id>/data/raw/catastro.gml

.venv/bin/python -m onward_generator cadastre match \
  --scene maps/<id>/data/processed/geo/geo_scene.json \
  --gml   maps/<id>/data/raw/catastro.gml \
  --out   maps/<id>/data/processed/cadastre

.venv/bin/python -m onward_generator heights \
  --scene     maps/<id>/data/processed/geo/geo_scene.json \
  --overrides maps/<id>/data/processed/cadastre/heights_overrides.catastro.json \
  --out       maps/<id>/data/processed/heights
```

## Download

- The bounding box is in the scene CRS (metres). Values that look like degrees are refused, and so is a box over 1 km².
- The request is HTTPS, a `GetFeature` of `bu:BuildingPart`. The generator identifies itself as `onward-generator/<version>`. The default curl user agent is refused by the service.
- The response must be a `FeatureCollection` with no DTD and no entities, and it must be at most 64 MB.
- The write is atomic and never overwrites without `--overwrite`. The sidecar `<file>.gml.source.json` stores the URL, the box, the date, the SHA-256, the size, the licence, and notes. `match` checks that SHA-256 before and after the read.

The downloaded GML stays out of git (`maps/*/data/`).

## Reading the GML

From each `BuildingPart`: `localId` (and the parcel reference `refcat`), `numberOfFloorsAboveGround`, `numberOfFloorsBelowGround`, and the geometry (`Surface` / `PolygonPatch`, including inner rings, 2D).

Parts are dropped with an issue when they are `malformed_part` (missing storeys, or another CRS), `invalid_part_geometry`, or `duplicate_part` (the same `localId` twice).

## Matching

For each OSM footprint, in the scene CRS:

1. Keep parts with **at least one storey above ground** that overlap the footprint by at least 1 m². Parts with 0 storeys, such as basements under courtyards, do not count.
2. `coverage` = area of the union of those overlaps ÷ footprint area.
3. `levels` = the **overlap-area-weighted median** of the storey counts.
4. Status: `matched` when `coverage ≥ 0.5`, `low_coverage` when parts exist but cover less, `no_parts` when there are none.
5. Confidence: 0.85 when `coverage ≥ 0.8`, otherwise 0.7, minus 0.1 when the median represents less than 60% of the area (`mixed`).

`--min-coverage` is configurable. Only `matched` rows go into `heights_overrides.catastro.json`. Each row has `levels` and no `height_m`, plus `source`, `license`, `retrieved_at`, `confidence`, and a note with the coverage, the parts, the storey range, and the `refcat` values.

## How `heights` uses it

An override that only has `levels` becomes `height_source: authoritative_levels` (class `inferred`): storeys × the category storey height. It ranks below an explicit OSM `height` and below an override with `height_m`, and above OSM `building:levels`. A difference is recorded as `override_levels_differ_from_osm`.

For landmarks, Catastro can count a tall nave as one storey. With `landmark.levels_floor_at_category_default: true`, the height does not fall below the category default (religious: 15 m). Those buildings stay **flagged for manual review**. The number is not a measured height.

On the Murcia cathedral and Plaza Belluga extract, 167 of 167 buildings matched, with coverage at least 0.83. That result is specific to that extract. It is not a guarantee for another city.

## Splitting blocks (`cadastre split`)

Decision: [ADR-015](decisions/ADR-015-catastro-split.md). OSM often maps a whole city block as one building. `match` gives that block one median storey count. `split` makes one building per construction instead, so heights, textures, doors and shopfronts vary inside the block.

```bash
.venv/bin/python -m onward_generator cadastre split \
  --scene maps/<id>/data/processed/geo/geo_scene.json \
  --gml   maps/<id>/data/raw/catastro.gml \
  --out   maps/<id>/data/processed/cadastre
```

It writes three files in a new directory:

| File | Content |
| --- | --- |
| `geo_scene.json` | The `onward_generator.geo_scene` contract with the split footprints replaced by sub-buildings, and a top-level `subdivision` block: the input scene (path, SHA-256), the Catastro record (provider, licence, attribution, retrieval date, SHA-256, URL), the options and the counts |
| `heights_overrides.catastro.json` | A `levels` override per sub-building, plus the `match` overrides for footprints that were not split |
| `cadastre_match_report.json` | The `match` report for the original footprints plus a `split` section: options, counts and one entry per OSM building |

For each OSM footprint, in the scene CRS:

1. **Landmarks** (the `heights` landmark rules) are not split (`landmark`).
2. Without a part with at least one storey above ground: `no_parts`. When all parts together (including those with 0 storeys) cover less than `--split-min-coverage` (0.8) of the footprint: `low_coverage`.
3. Parts are grouped by parcel (`refcat`) and storey count. Each group is clipped to the footprint; groups are made disjoint in the order of their smallest `localId`.
4. A clipped piece under `--min-piece-area-m2` (6 m²) or narrower than `--min-piece-width-m` (2 m) is a sliver and is dropped. Fewer than two groups left: `single_group`, the footprint is kept and `match` decides its storeys.
5. Area covered only by parts with 0 storeys above ground, and not a sliver, is a courtyard: it is removed from the footprint and becomes open ground.
6. Any other uncovered area (gaps, slivers, misalignment) is given to the nearest group: closest first, then the longest shared boundary, then the id. Coordinates are snapped to 1 mm.
7. Each connected polygon of a group is its own sub-building (pieces touching only at a corner would make non-manifold walls). A polygon under `--min-piece-area-m2` left on its own is open ground, reported as `dropped_m2`. The sub-buildings, courtyards and dropped pieces together cover the OSM footprint exactly.

A sub-building:

- id `<osm id>:<smallest localId>`, for example `osm-way-101:0000001AA0000A_part1`; further polygons of the same group get `.2`, `.3`, … by decreasing area;
- the parent's `source`, `tags`, `clip_status` and `geometry_repaired`;
- the parent's `attributes` without `height_m` and `levels`, which describe the whole block and move to `subdivision.parent_observed`;
- `parent: {"id": <osm id>}`;
- `subdivision`: `method` (`catastro_split`), `source`, `provider`, `license`, `license_url`, `attribution`, `retrieved_at`, `refcat`, `parts`, `levels`, `leftover_m2` (area it received from step 6), `parent_observed`.

Its override has the group's storey count, confidence 0.85, or 0.7 when more than a fifth of its area came from step 6. `heights` turns it into `authoritative_levels` as above. `heights_report.json` has `counts.subdivided`: `osm_buildings_split`, `sub_buildings` and `levels_range`.

Downstream nothing else changes. `generate` extrudes every edge of every footprint, so where a taller sub-building meets a lower one its side wall stays closed above the lower roof. `detail` marks shared edges as party walls: no doors, windows or shopfronts there. A shop or amenity point inside a footprint gives that footprint the shopfront; only a point inside no footprint uses the 2 m distance.

### In `run`

`run` adds two stages after `geo` when the map uses EPSG:25830, its centre is between latitudes 35.85° and 43.8°, and its square plus the 20 m download margin fits 1 km² (radius plus margin up to 480 m):

- `catastro`: `cadastre download` into `data/raw/catastro.gml`. HTTP 429, 5xx, a timeout or a dropped connection are retried three times, the wait doubling from 8 s, as for Overpass. If it still fails, `run` stops there and the next run continues at `catastro`.
- `cadastre`: `cadastre split` into `data/processed/cadastre/`.

`heights` then reads that scene with `--overrides`, and every later stage follows it. `run --no-catastro` stores `"catastro": false` in `map.json` and keeps the OSM chain; `run --catastro` turns it back on. Where the service has no data (Navarre, the Basque Country, the French strip of the latitude band) the split scene equals the `geo` scene.
