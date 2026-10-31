# Storeys from Catastro (INSPIRE BU)

Module `onward_generator.cadastre`. Licence obligations are in `docs/legal/data-sources.md`.

Download and processing are separate. Only `download` uses the network. The service covers Spain except Navarre and the Basque Country ([Coverage](#coverage)). A map elsewhere skips this step. `run` downloads and splits by default wherever the service has data ([In `run`](#in-run)).

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

## Coverage

The Dirección General del Catastro (DG Catastro) serves all of Spain except Navarre and the Basque Country (Álava, Bizkaia, Gipuzkoa), which keep their own foral cadastres: there the WFS returns no parts. `config/catastro_zones.json` is the table, validated when it is read (`cadastre/zones.py`):

| Territory | UTM zone of the map centre | Request CRS |
| --- | --- | --- |
| `peninsula` | 29 (west of 6° W: Galicia, western Asturias, Castilla y León and Extremadura, Huelva, Cádiz) | EPSG:25829 |
| `peninsula` | 30 (6° W–0°) | EPSG:25830 |
| `peninsula` | 31 (east of 0°: Catalonia, eastern Aragón, Castellón, Dénia) | EPSG:25831 |
| `balearic-islands` | 31 | EPSG:25831 |
| `canary-islands` | 28 (west of 18° W, the tip of El Hierro: 27) | EPSG:32628 (EPSG:32627) |
| `ceuta`, `melilla` | 30 | EPSG:25830 |
| `navarre`, `basque-country` (excluded; the Condado de Treviño, Burgos, is a hole and stays covered) | — | — |

The territory of a map is the one whose polygon holds the map centre (WGS84 longitude, latitude), unless an excluded polygon holds it. The request CRS is the territory's CRS for the UTM zone of the centre longitude, computed as `map create` does. Every CRS must be in the service's list and be the UTM CRS of its zone, so tiles and margins stay in metres. The polygons are coarse: generous over the sea and across the borders with Portugal, France, Andorra and Gibraltar, where the service returns nothing and the split scene equals the `geo` scene; drawn inside Navarre and the Basque Country, so a map in La Rioja, Burgos, Cantabria or Aragón next to them keeps Catastro. A map centred within a few kilometres of the inside of those two borders still sends the request and gets no parts. `tests/unit/test_cadastre_zones.py` checks about 75 places, many on both sides of those borders.

The service's CRS list comes from its `GetCapabilities` (checked 30 October 2026, recorded in the table): default EPSG:25830; others EPSG:25829, EPSG:25831, EPSG:32627, EPSG:32628, EPSG:3035, EPSG:3785, EPSG:3857, EPSG:4258, EPSG:4326 and CRS:84. ETRS89 / REGCAN95 UTM 28N (EPSG:4083) is not offered, so the Canaries are requested in WGS 84 / UTM 28N. One 100 m request each in Santiago de Compostela (EPSG:25829), Palma (EPSG:25831) and Las Palmas de Gran Canaria (EPSG:32628) returned parts (32, 364 and 155) whose `srsName` is the requested CRS.

The map's own CRS (`map.json` `crs`) is EPSG:25830 inside 6° W–0° and the WGS 84 UTM zone of the centre elsewhere (ADR-002). The scene box is converted to the request CRS with pyproj (`transform_bounds`, edges densified), and `match` and `split` convert every part from the request CRS to the scene CRS before they compare it with a footprint. pyproj relates ETRS89 and WGS 84 without a shift, as OSM in Spain is drawn over ETRS89 imagery; the same-zone conversion moves a vertex by less than a millimetre. When the two CRS are the same (Murcia) nothing is converted.

## Download

- The bounding box is in the request CRS (metres). Values that look like degrees are refused.
- A box over 1 km² is split into tiles of at most 1 km × 1 km, the service limit. The box is first grown outwards to whole centimetres (the precision of the request); each axis is cut into the fewest equal parts no longer than 1 km, on the centimetre grid, so neighbouring tiles share their edges and together are exactly the box. Tiles run from the south-west, row by row. At most 25 tiles (5 km × 5 km, the largest map plus margins) are requested; a larger box is refused.
- The request is HTTPS, a `GetFeature` of `bu:BuildingPart` per tile, then the same tiles again for `bu:Building` (one feature per parcel, with its `currentUse`, ADR-022) into `<stem>_buildings.gml` beside the parts (`data/raw/catastro_buildings.gml`), with its own sidecar. Both files are checked for `--overwrite` before the first request. The generator identifies itself as `onward-generator/<version>`. The default curl user agent is refused by the service. Each tile is retried like Overpass (see [In `run`](#in-run)).
- Each response must be a `FeatureCollection` with no DTD and no entities, and it must be at most 64 MB.
- Tiles are merged into one `FeatureCollection`. A part that crosses a tile edge comes back from both tiles; a member of a later tile whose `localId` an earlier tile already had is dropped. A single tile is written byte for byte as received.
- The write is atomic and never overwrites without `--overwrite`. If any tile fails, nothing is written. The sidecar `<file>.gml.source.json` stores the URL (the request itself for one tile, the service endpoint for several), the whole box, the request CRS, the date, the SHA-256, the size, the licence, notes, and `tiles`: per tile the URL, box, SHA-256 and size of the response, its `members` and the `repeats_dropped`. A sidecar written before tiles existed loads with `tiles: []`. `match` checks the file's SHA-256 before and after the read.

The downloaded GML stays out of git (`maps/*/data/`).

## Reading the GML

From each `BuildingPart`: `localId` (and the parcel reference `refcat`), `numberOfFloorsAboveGround`, `numberOfFloorsBelowGround`, and the geometry (`Surface` / `PolygonPatch`, including inner rings, 2D).

Parts are dropped with an issue when they are `malformed_part` (missing storeys, or a `srsName` other than the request CRS recorded in the sidecar), `invalid_part_geometry`, or `duplicate_part` (the same `localId` twice).

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

Decision: [ADR-016](decisions/ADR-016-catastro-split.md). OSM often maps a whole city block as one building. `match` gives that block one median storey count. `split` makes one building per construction instead, so heights, textures, doors and shopfronts vary inside the block.

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
| `cadastre_match_report.json` | The `match` report for the original footprints plus a `split` section: options, counts (including `status_quarantined` and `quarantined_m2`) and one entry per OSM building with its status, `dropped_m2`, `quarantined_m2` and `reason` |

For each OSM footprint, in the scene CRS:

1. **Landmarks** (the `heights` landmark rules) are not split (`landmark`).
2. Without a part with at least one storey above ground: `no_parts`. When all parts together (including those with 0 storeys) cover less than `--split-min-coverage` (0.8) of the footprint: `low_coverage`.
3. Parts are grouped by parcel (`refcat`) and storey count. Each group is clipped to the footprint; groups are made disjoint in the order of their smallest `localId`.
4. A clipped piece under `--min-piece-area-m2` (6 m²) or narrower than `--min-piece-width-m` (2 m) is a sliver and is dropped. Fewer than two groups left: `single_group`, the footprint is kept and `match` decides its storeys.
5. Area covered only by parts with 0 storeys above ground, and not a sliver, is a courtyard: it is removed from the footprint and becomes open ground.
6. Any other uncovered area (gaps, slivers, misalignment) is given to the nearest group: closest first, then the longest shared boundary, then the id. Coordinates are snapped to 1 mm.
7. Each connected polygon of a group is its own sub-building (pieces touching only at a corner would make non-manifold walls). Where the 1 mm snap leaves a hole touching the outline (or another hole) at a single point, a 2 cm diamond notch is cut there: the polygon is valid, but its walls would share one vertical edge between two rings. A polygon under `--min-piece-area-m2` left on its own is open ground. Both are reported as `dropped_m2`. The sub-buildings, courtyards and dropped pieces together cover the OSM footprint exactly.
8. **Quarantine.** Every sub-building is extruded with a flat roof and checked like `generate` checks a building (no open or non-manifold edge above the base). If one fails, the whole OSM footprint is kept unsplit (`quarantined`): it gets the `match` override like a `single_group` footprint, and the report records its area (`quarantined_m2`) and the reason (the sub-building and its edge count). When the quarantined area exceeds `max_quarantine_share` (5%) of the scene's building area, `split` fails instead of writing a scene that silently lost most of its split. `run` prints `kept whole N (A m²)` on the `cadastre` line when any footprint was quarantined.

A sub-building:

- id `<osm id>:<smallest localId>`, for example `osm-way-101:0000001AA0000A_part1`; further polygons of the same group get `.2`, `.3`, … by decreasing area;
- the parent's `source`, `tags`, `clip_status` and `geometry_repaired`;
- the parent's `attributes` without `height_m` and `levels`, which describe the whole block and move to `subdivision.parent_observed`;
- `parent: {"id": <osm id>}`;
- `subdivision`: `method` (`catastro_split`), `source`, `provider`, `license`, `license_url`, `attribution`, `retrieved_at`, `refcat`, `parts`, `levels`, `leftover_m2` (area it received from step 6), `parent_observed`.

Its override has the group's storey count, confidence 0.85, or 0.7 when more than a fifth of its area came from step 6. `heights` turns it into `authoritative_levels` as above. `heights_report.json` has `counts.subdivided`: `osm_buildings_split`, `sub_buildings` and `levels_range`.

Downstream nothing else changes. `generate` extrudes every edge of every footprint, so where a taller sub-building meets a lower one its side wall stays closed above the lower roof. `detail` marks shared edges between parcels as party walls: no doors, windows or shopfronts there. The parts of one parcel (`subdivision.refcat`) are one building to `detail`: one wall material, use and roof material, a flat terrace under a taller part, and windows on a taller part's wall above the lower part's roof (`docs/07-detail.md`, *Setbacks*). A shop or amenity point inside a footprint gives that footprint the shopfront; only a point inside no footprint uses the 2 m distance, and then only the nearest footprint (the lowest id on a tie), so a point in front of a party wall gives one building the shopfront (`docs/07-detail.md`, *One building per POI*).

### Current use (`--buildings`)

Decision: [ADR-022](decisions/ADR-022-catastro-use.md). `cadastre split --buildings data/raw/catastro_buildings.gml` reads each `bu:Building`: `localId` (the parcel reference), `currentUse`, `numberOfDwellings` and `numberOfBuildingUnits` (either may be `xsi:nil`, read as `null`). A building without a `localId` or a `currentUse`, or with a count that is not a non-negative integer, is a `malformed_building` issue; a repeated `localId` is `duplicate_building`. The geometry is not read. The file's SHA-256 is checked against its sidecar after the read.

Each building of the split scene then gets `catastro_use` when its parcel has a Catastro building:

- a sub-building takes its own parcel (`subdivision.refcat`); its `share` is the part of it that is not leftover;
- a footprint left whole takes the parcel whose parts with at least one storey cover most of it, when that parcel covers at least `use_min_share` (0.5) of it.

`catastro_use` holds `current_use` (the Catastro code: `1_residential`, `2_agriculture`, `3_industrial`, `4_1_office`, `4_2_retail`, `4_3_publicServices`), `use` (residential, agriculture, industrial, office, retail, public_services, or `other` for an unknown code), `refcat`, `share`, `dwellings`, `building_units`, `attribute_origin: observed`, `source`, `provider`, `license`, `license_url`, `attribution`, `retrieved_at`. The use is Catastro's *predominant* use of the parcel, not of any floor. The scene's `subdivision.uses` records the file (`status: read`, path, provenance, SHA-256, URL, `buildings` read, `issues`, and `attached` per code). Without `--buildings` it is `{"status": "not_requested"}`; when the file is missing (a map downloaded before this) it is `{"status": "missing", "path"}`, `split` says so and no building gets a use. In `run` that does not happen: the buildings file is the download stage's marker, so a map downloaded before it downloads both files again. `detail` uses it for the ground floor (`docs/07-detail.md`, *Fidelity*).

### In `run`

`run` adds two stages after `geo` when the map centre is in a [covered territory](#coverage). Any map size works: a map wider than 960 m is downloaded in tiles.

- `catastro`: `cadastre download` into `data/raw/catastro.gml`. HTTP 429, 5xx, a timeout or a dropped connection are retried three times per tile, the wait doubling from 8 s, as for Overpass. If a tile still fails, `run` stops there, nothing is written, and the next run continues at `catastro` from the first tile.
- `catastro` finishes when `data/raw/catastro_buildings.gml` exists, the file it writes last. A map with only `catastro.gml` (downloaded before ADR-022) runs it again with `--overwrite`.
- `cadastre`: `cadastre split --buildings data/raw/catastro_buildings.gml` into `data/processed/cadastre/`, rerun when either GML is newer.

`heights` then reads that scene with `--overrides`, and every later stage follows it. Catastro is the source plugin `es.catastro` ([ADR-018](decisions/ADR-018-source-plugins.md)), an adapter over this module with the same two stages. `run --no-source es.catastro` (or `--no-catastro`) stores `"sources": {"es.catastro": false}` in `map.json` and keeps the OSM chain; `--source es.catastro` (or `--catastro`) turns it back on. A map written earlier with `"catastro": false` keeps it until the next switch. When another plugin also writes height overrides, `overrides` merges them by `config/sources.json` `priority`; Catastro storeys rank below measured LiDAR heights and lend their `levels` to a LiDAR winner. Navarre, the Basque Country and places outside Spain have no Catastro stages; where a covered map reaches across a border, the parts stop at the border.
