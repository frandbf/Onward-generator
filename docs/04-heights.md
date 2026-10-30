# Building heights (`onward_generator.heights`)

Goal: a plausible, consistent skyline, not architectural precision. Every height carries its origin, its confidence, and the rule that produced it. An estimate is never stored as a measurement.

The module does not import `geo` and does not modify the scene. It reads `geo_scene.json` (0.1.0 or 0.2.0) and checks the SHA-256 before and after the read.

```bash
.venv/bin/python -m onward_generator heights \
  --scene maps/<id>/data/processed/geo/geo_scene.json \
  --out   maps/<id>/data/processed/heights \
  [--config config/heights.default.json] [--overrides <overrides.json>] [--overwrite]
```

`--overrides` replaces `overrides_file` in the configuration. That is how Catastro overrides from `cadastre match` or `cadastre split` are applied (`docs/05-cadastre.md`). Outside Spain there is no Catastro step. Heights stay on the OSM estimate.

After `cadastre split` the scene is `data/processed/cadastre/geo_scene.json`. A block OSM drew as one polygon arrives as several sub-buildings (`parent` names the OSM building), each with its own Catastro storey count, so each gets its own `authoritative_levels` height instead of one estimate for the whole block. `heights_report.json` counts them in `counts.subdivided`: `osm_buildings_split`, `sub_buildings`, `levels_range` (`null` when nothing was split). Override ids may be an OSM id or `<osm id>:<localId>` (`config/heights.overrides.schema.json`). ADR-016.

## Order

The first step that produces a value wins:

| # | `height_source` | Class | Rule | Confidence |
| --- | --- | --- | --- | --- |
| 1a | `override` | measured | An override with `height_m`, from an authorised open dataset, with source and licence | the file's (0.9 by default) |
| 1b | `explicit_height` | measured | An OSM `height` tag inside the bounds | 0.95 |
| 1c′ | `authoritative_levels` | inferred | An override that only has `levels` (Catastro): `levels × storey height` for the category. If it differs from OSM `building:levels`, `override_levels_differ_from_osm` is recorded | the file's (Catastro: 0.6–0.85) |
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

Issues: `override_not_in_scene`, and `override_differs_from_osm` when the override contradicts the OSM `height` by more than 10%.

The default configuration has no overrides. Catastro overrides are produced by `cadastre match` and passed with `--overrides`.

## Outputs

`heights.json` (`onward_generator.building_heights`, 0.1.0) records the scene (path, SHA-256, version), the configuration, the overrides, the attribution inherited from the scene, the full assumptions, and one record per building, ordered by id.

`levels_source` is `explicit`, `override`, `building_type`, or `derived_from_height`.

`heights_report.json` counts by class, source, and category; reports `measured` / `inferred` / `fallback` shares; reports min, p10, median, p90, and max of heights and confidences; lists manual reviews; and groups issues by code.

Outputs are deterministic (no timestamps) and are not overwritten without `--overwrite`.

## Limits

- Blocks come from footprints that touch, not from streets. A centre with disconnected footprints falls through to the radius level.
- When few heights are known, one tagged group dominates its surroundings.
- `roof:levels`, `min_height`, and `building:part` are not used here. A `building:part` with `height` is extruded later, in `detail`.
