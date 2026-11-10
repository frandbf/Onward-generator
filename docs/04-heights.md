# Building heights (`onward_generator.heights`)

Goal: a plausible, consistent skyline, not architectural precision. Every height carries its origin, its confidence, and the rule that produced it. An estimate is never stored as a measurement.

The module does not import `geo` and does not modify the scene. It reads `geo_scene.json` (0.1.0 or 0.2.0) and checks the SHA-256 before and after the read.

```bash
.venv/bin/python -m onward_generator heights \
  --scene maps/<id>/data/processed/geo/geo_scene.json \
  --out   maps/<id>/data/processed/heights \
  [--config config/heights.default.json] [--overrides <overrides.json>] [--overwrite]
```

`--secondary-overrides <heights_overrides.overture.json>` adds third-party values from `overture match` (ADR-021) in a tier of their own, below an OSM `height` and the `--overrides` file. The file uses the same schema; an entry may add `origin` (`observed`, `estimated` or `ai_generated`). `run` passes it when the `overture` plugin runs, and stops at the plan when `config/sources.json` ranks `overture` above a plugin of the map that writes authoritative overrides, so the priority stays the only ranking.

`--overrides` replaces `overrides_file` in the configuration. That is how Catastro overrides from `cadastre match` or `cadastre split` are applied (`docs/05-cadastre.md`). Outside Spain, and in Navarre and the Basque Country, there is no Catastro step. Source plugins (ADR-018) each write a `heights_overrides.<id>.json`; when a map has more than one, `source merge` ranks them by `config/sources.json` `priority` into one file, and `run` passes that one. The priority is the only place sources are ranked. Without a plugin for the map, heights stay on the OSM estimate.

After `cadastre split` the scene is `data/processed/cadastre/geo_scene.json`. A block OSM drew as one polygon arrives as several sub-buildings (`parent` names the OSM building), each with its own Catastro storey count, so each gets its own `authoritative_levels` height instead of one estimate for the whole block. `heights_report.json` counts them in `counts.subdivided`: `osm_buildings_split`, `sub_buildings`, `levels_range` (`null` when nothing was split). Override ids may be an OSM id or `<osm id>:<localId>` (`config/heights.overrides.schema.json`). ADR-016.

## Order

The first step that produces a value wins:

| # | `height_source` | Class | Rule | Confidence |
| --- | --- | --- | --- | --- |
| 1a | `override` | measured | An override with `height_m`, from an authorised open dataset, with source and licence | the file's (0.9 by default) |
| 1b | `explicit_height` | measured | An OSM `height` tag inside the bounds | 0.95 |
| 1c′ | `authoritative_levels` | inferred | An override that only has `levels` (Catastro): `levels × storey height` for the category. If it differs from OSM `building:levels`, `override_levels_differ_from_osm` is recorded | the file's (Catastro: 0.6–0.85) |
| 1d | `third_party_height` | inferred | A secondary override with `height_m` (Overture). The rule names its origin, dataset and licence | the file's (Overture: 0.8 observed, 0.6 estimated, 0.5 ai_generated) |
| 1e | `third_party_levels` | inferred | A secondary override with only `levels`: `levels × storey height` | the file's |
| 1c | `explicit_levels` | inferred | `building:levels × storey height` (3.0 m residential; 3.75 m commercial and public) | 0.75 |
| — | `landmark_placeholder` | fallback | A landmark with no explicit value and no override: the category default, **flagged for manual review** | 0.1 |
| 2 | `building_type` | inferred | Typical storeys for the category × storey height, clamped to the category range. Only when a tag identifies the type. `building=yes` alone is not enough | 0.4 |
| 3 | `neighbourhood` | inferred | Median of nearby known heights | 0.55 / 0.5 / 0.45 / 0.4 |
| 4 | `fallback` | fallback | The configurable category default | 0.2 |

A `height` outside `bounds_m` is ignored, recorded as `explicit_height_out_of_bounds`, and the building is flagged for review. Any estimate outside the bounds is clamped (`estimate_clamped`).

### Categories

The category is the first key in `category_key_priority` (`building`, `building:use`, `amenity`, `office`, `shop`, `tourism`) whose value matches `categories`. `*` is a wildcard. Categories: `residential_small`, `residential_multi`, `commercial`, `public`, `industrial`, `religious`, `minor`, `unknown`.

### Landmarks

A building is a landmark when it has a key in `landmark.keys` (`historic`, `heritage`) or a value in `landmark.tags` (`building=cathedral|church|…`, `amenity=place_of_worship`). Landmarks do not use generic type or neighbourhood estimates:

- with `height` or an override, that value is used;
- with only storeys (`building:levels` or a storey override), those are used and `landmark_height_from_levels` is set. If `landmark.levels_floor_at_category_default` is `true` (the default), the height does not fall below the category default. Catastro records some naves as one storey; without that floor the height would be 4 m;
- with no data, the marker is `landmark_without_authoritative_height`.

The right fix is an override from an authorised source, not an invented number. An OSM tower point with `height` can add a clipped prism later (ADR-008). It does not change this height record.

### Neighbourhood

- Samples are buildings already resolved from `neighbourhood.reliable_sources` (by default `explicit_height`, `override`, `authoritative_levels`, `explicit_levels`). Type, neighbourhood, and fallback estimates never feed other buildings, so the result does not depend on order.
- A block is a connected component of footprints closer than `block_gap_m` (1 m). It is a footprint approximation, not the street grid.
- Tried in order: same block and category, same block, within `radius_m` (75 m between footprints) and the same category, then radius alone. The first level with at least `min_samples` (3) wins.
- The median is used, not the mean.

## LiDAR heights

Source plugins that read a LiDAR height model (nDSM: height above ground) write `height_m` overrides, so their heights are step 1a (`override`, class `measured`). They rank above Catastro storeys in `config/sources.json` `priority`; a footprint they cannot measure keeps the next source (ADR-018).

```bash
onward-generator source fetch es.pnoa_ndsm --scene <scene> --out maps/<id>/data/raw/es.pnoa_ndsm
onward-generator source normalize es.pnoa_ndsm --scene <scene> --raw maps/<id>/data/raw/es.pnoa_ndsm --out maps/<id>/data/processed/es.pnoa_ndsm
```

`fetch` asks the service for the box around the scene's buildings plus 20 m (at most 6 km a side), on the raster's own cell grid, in tiles, and keeps every reply as received in `data/raw/<id>/tile_NNN.bin` with `data/raw/<id>.source.json` (URL, box, SHA-256 and size per tile, retrieval time, licence, attribution, the plugin's metadata). Replies are checked before anything is written; HTTP 429, 5xx and dropped connections are retried three times, the wait doubling from 8 s; one request per second. `normalize` checks every tile's SHA-256, joins the tiles and, for each footprint of the scene (in `run`, the Catastro split scene, so each sub-building is measured on its own):

1. takes the cells whose centre lies inside the footprint shrunk by an **inner buffer** (1 m). The buffer keeps facade edges, eaves and trees that overhang the outline out of the sample. A footprint too narrow to keep the minimum number of cells after the buffer is sampled without it (`buffer_m: 0` in the report);
2. keeps the **valid** cells: with data and at least 2 m (lower is ground or a courtyard, not a roof);
3. writes the **median** (`percentile`, 50) when at least `min_cells` cells are valid and they are at least half of the cells sampled. The 25th and 75th percentiles go into the note: a wide spread is a pitched roof or a mixed footprint.

A height outside the `heights` bounds (2.5–120 m) is not written (`out_of_bounds`); a footprint without enough valid cells is `no_data`. Both stay in `<id>_report.json` with the cell counts. The median of a pitched roof lies between eave and ridge, so it is above the wall height `levels × storey height` describes.

`heights` does not read the spread: the override note carries it as text only. With `map.json` `"lidar_roofs": true` (plan C3, off by default) `detail --lidar-roofs` reads `p25_m`, `p75_m`, `valid` and `cells` from `<id>_report.json` and draws a flat or pitched roof from them ([07-detail.md](07-detail.md), *Roofs*). The rise is the spread itself (about half the real rise), capped at 4 m, on top of the recorded median, so the ridge lands near the measured one and the walls and the collider keep the height above. Only footprints with at least 8 valid cells are classified: at 2.5 m a cell, PNOA gives a median of 7 valid cells in Madrid and 3–4 on small Murcia parts.

| Plugin | Data | Cell | Buffer | `min_cells` | Confidence | Data date |
| --- | --- | --- | --- | --- | --- | --- |
| `es.pnoa_ndsm` | IGN MDSn, building class (trees already removed) | 2.5 m | 1 m | 3 | 0.8 | PNOA-LiDAR first coverage, 2008–2015 (per sheet, not per building) |
| `fr.lidar_hd` | IGN LiDAR HD MNH (surface minus terrain: buildings and trees) | 0.5 m, native Lambert-93 grid | 1 m | 8 | 0.9 | acquisition dates of the IGN 1 km tile under the footprint (WFS `IGNF_LIDAR-HD_METADONNEE:metadata`) |

On Madrid (40.40657, −3.719187, r150, Catastro split, 30 October 2026): 84 footprints, 66 measured (13.1–27.0 m), 18 `no_data` (small courtyard pieces and sub-buildings under 3 cells); 27 of the 66 were sampled without the buffer. Against Catastro `levels × storey height` on the same footprints the LiDAR height is a median 3.0 m higher (|Δ| median 3.0 m, p90 6.5 m, 34 of 66 within 3 m): a storey of 3 m misses the taller ground floor, the roof and the parapet. The largest differences (up to 20 m) are footprints Catastro gives 1–2 storeys that the LiDAR sees as tall (a low part under a taller volume, or a part OSM and Catastro outline differently). `fetch` took 3.4 s (one 99 KB tile), `normalize` 0.7 s. One map, one date: not a guarantee for another city.

On Lyon (45.7640, 4.8357, r150, no cadastre plugin, 30 October 2026): 225 footprints, 219 measured (2.8–34.8 m), 5 `no_data`, 1 `out_of_bounds` (a 2.4 m canopy); 14 sampled without the buffer; every footprint in one tile acquired 2021-04-14 to 2021-09-24. Against OSM `building:levels × storey height` (75 footprints with levels) the LiDAR height is a median 7.1 m higher (|Δ| p90 10.8 m, 6 of 75 within 3 m): Lyon storeys are taller than 3 m, and the roofs add to that. `fetch` took 7.1 s (one 2.5 MB tile and the metadata request), `normalize` 1.4 s.

## Configuration

`config/heights.default.json`, validated with `config/heights.schema.json`. Every number is a modelling assumption.

| Category | Storey height | Typical storeys | Default (range) |
| --- | --- | --- | --- |
| residential_small | 3.0 | 2 | 7.5 (6–9) |
| residential_multi | 3.0 | 4 | 12 (9–15) |
| commercial | 3.75 | 3 | 9 (6–12) |
| public | 3.75 | 3 | 12 (9–18) |
| industrial | 6.0 | 1 | 7.5 (5–10) |
| religious | 4.0 | — | 15 (8–40) |
| minor | 3.0 | 1 | 3 (2.5–4.5) |
| unknown | 3.0 | — | 8 (6–15) |

Global bounds: 2.5–120 m.

### Overrides

`overrides_file` (relative to the configuration file) points at JSON validated with `config/heights.overrides.schema.json`. `id`, `source`, and `license` are required, plus `height_m`, `levels`, or both. Duplicate ids and heights outside the bounds are refused.

Secondary overrides (`--secondary-overrides`) follow the same validation. `heights.json` and the report record them under `secondary_overrides`: path, SHA-256, `count`, `used`, and the attribution `sources` of the entries a building used. `generate` adds those sources to the scene attribution. They feed the neighbourhood median only when `neighbourhood.reliable_sources` lists `third_party_height` or `third_party_levels`; the default does not.

Issues: `override_not_in_scene` (for either file), and `override_differs_from_osm` when the override contradicts the OSM `height` by more than 10%.

The default configuration has no overrides. Catastro overrides are produced by `cadastre match` and passed with `--overrides`.

## Outputs

`heights.json` (`onward_generator.building_heights`, 0.1.0) records the scene (path, SHA-256, version), the configuration, the overrides, the attribution inherited from the scene, the full assumptions, and one record per building, ordered by id.

`levels_source` is `explicit`, `override`, `building_type`, or `derived_from_height`.

`heights_report.json` counts by class, source, and category; reports `measured` / `inferred` / `fallback` shares; reports min, p10, median, p90, and max of heights and confidences; lists manual reviews; and groups issues by code.

Outputs are deterministic (no timestamps) and are not overwritten without `--overwrite`.

## Limits

- Blocks come from footprints that touch, not from streets. A centre with disconnected footprints falls through to the radius level.
- When few heights are known, one tagged group dominates its surroundings.
- `roof:levels`, `min_height`, and `building:part` are not used here. `detail` extrudes each `building:part` from its `height`, or from `(building:levels + roof:levels)` × the `storey_height_m` of this configuration for the category of the building it stands on, and draws the `roof:*` tags (`docs/07-detail.md`, *Roofs* and *Parts*).
