# ADR-018 — Optional data sources as plugins behind the generic core

Status: accepted · 30 October 2026.

## Context

The generator works for any city from OpenStreetMap alone. Better data exists only in some countries: Catastro storey counts in Spain (ADR-016), LiDAR height models in Spain (IGN MDSn) and France (IGN LiDAR HD), 3D BAG in the Netherlands, Overture worldwide. Until now Catastro was wired into `run` by hand: `pipeline_run` knew the stage names, `map.json` had a `catastro` key and `run` a `--catastro` flag. A second dataset written the same way would mean a second set of special cases in the runner, the workspace and the CLI, and nothing would say which source wins when two give a building a height.

## Decision

1. **A plugin is a dataset with a coverage, a licence and stages.** `onward_generator.sources.plugin.SourcePlugin` is a `Protocol`:
   - `id`: `<country code>.<dataset>` (`es.catastro`, `es.pnoa_ndsm`, `fr.lidar_hd`). It names the plugin in `map.json`, on the command line and in the priority.
   - `licence`: a `LicenceRecord` with the SPDX id, name, URL, attribution text and obligations.
   - `produces`: a subset of `footprints` (it writes a new `geo_scene.json` that later stages read), `height_m` and `levels` (it writes a `heights_overrides.<id>.json` in the existing overrides contract, `config/heights.overrides.schema.json`).
   - `covers(longitude, latitude)`: whether the map centre is inside its coverage. Coverage is a static, versioned table in the repository (Catastro: `config/catastro_zones.json`). There is no network call to decide coverage.
   - `steps(root, scene)`: its `run` stages for one map, reading `scene`, the latest scene before it. A stage is a CLI call, the file that shows it finished, and the files that file must be newer than. One stage per plugin is the download: it writes `data/raw/<file>` and its `.source.json`, and a repeated download passes `--overwrite`.

   Plugins live in `onward_generator/sources/<country code>/`. They never import the exporter.
2. **Discovery is an explicit tuple, `sources.registry.PLUGINS`.** Its order is the order `run` adds the stages, so the plan is deterministic. A plugin that writes `footprints` comes first, so the plugins after it read its scene: LiDAR heights are measured on the Catastro sub-buildings, not on the OSM block.
3. **`run` asks every plugin.** For each registered plugin that covers the map centre and is not switched off, `run` adds its stages after `geo`, in registry order. It prints which plugins run before the first stage: `sources  es.catastro  (off: …)  (not here: …)`.
4. **Switches are stored per map.** `run --source <id>` and `run --no-source <id>` (repeatable) write `"sources": {"<id>": true|false}` in `map.json`. A map without a switch uses every plugin that covers it. A switch does not extend coverage: `--source` outside a plugin's coverage does nothing. `--catastro` / `--no-catastro` stay, as aliases of `--source es.catastro` / `--no-source es.catastro`. A map written before plugins keeps its `"catastro": false`; the first `es.catastro` switch replaces that key.
5. **One licence allowlist, checked before any fetch.** `config/sources.json` `licence_allowlist` lists the SPDX ids a plugin may be used under (`CC-BY-4.0`, `etalab-2.0`, `ODbL-1.0`). A plugin whose licence is not on it stops `run` at the plan, before any request.
6. **One priority ranks the height sources.** `config/sources.json` `priority` is the only ranking: measured LiDAR or LoD2, then national cadastre storeys, then Overture (`overture` keeps its slot until that plugin exists). Every plugin that writes overrides must have a rank. When two or more plugins of a map write overrides, a stage `overrides` (`source merge`) writes `data/processed/sources/heights_overrides.json` and `sources_report.json`, and `heights` reads that file. For each building the entry of the first ranked source wins whole. When that entry has `height_m` and no `levels`, it takes `levels` from the next ranked source that has them, so a LiDAR height keeps the cadastre storey count for the facade, and its `source` and `license` name both. A `height_m` is never added to a storey-only winner, because `heights` puts any override `height_m` (step 1a) above override `levels` (step 1c′), and that would let a lower-ranked source win. Below every plugin, `heights` keeps its own order: OSM `height`, OSM `building:levels`, then the estimate (`docs/04-heights.md`).
7. **Catastro is the first plugin, without a behaviour change.** `sources.es.catastro.CatastroPlugin` is an adapter over `onward_generator.cadastre`. Its stages are the ones `run` already had, `catastro` (`cadastre download`) and `cadastre` (`cadastre split`), with the same files and arguments. With Catastro alone there is no merge: `heights` reads `heights_overrides.catastro.json` as before.

## Consequences

- A new dataset is a module in `sources/<cc>/` and a line in `PLUGINS`, plus a rank in `config/sources.json` when it writes overrides. The runner, the workspace and the CLI do not change.
- `map.json` gains `sources`. `run` prints one more line. Nothing else in the Catastro chain changes.
- A plugin's `covers` is coarse by design: a map centre inside the table can still have gaps (Catastro has none in Navarre; a LiDAR campaign may not have reached a tile yet). Each plugin reports what it found; a gap leaves those buildings on the next source in the priority.
- Only `height_m` and `levels` are merged. Roof meshes, trees, furniture and points of interest are not part of this decision; a plugin for them needs its own contract first.

## Rejected

- **Entry-point or import-scan discovery.** It makes the stage order depend on the environment, and nothing outside this repository needs to add a plugin.
- **A rank per kind (`height_m`, `levels`).** Two lists can disagree: Overture `height_m` would outrank Catastro `levels` in `heights` even when Catastro ranks above Overture. One list and the levels-only borrowing rule keep the order the configuration states.
- **Coverage from a capabilities request.** It makes the plan depend on the network and on service uptime. A static table can be reviewed and versioned.

## Amendment — LiDAR height plugins (30 October 2026)

`es.pnoa_ndsm` is the first raster plugin. The shared code is `sources.raster` (an ESRI ASCII grid and a raw float32 grid are a header and an array: numpy and shapely read them, so no GDAL, rasterio or tifffile dependency is added) and `sources.lidar` (`source fetch`, `source normalize`, the footprint median with an inner buffer). A raster plugin also satisfies `sources.lidar.RasterHeights`: its CRS, cell grid and requests, how to decode a reply, and the date of the data at a point. It runs after `cadastre`, on the split scene. Rule and measurements: `docs/04-heights.md#lidar-heights`. `fr.lidar_hd` is the second: the same code, a native 0.5 m grid in Lambert-93 (the footprints move to the raster CRS, the raster is never resampled) and the acquisition dates of each IGN tile.

## Amendment — shop evidence (31 October 2026)

A plugin may also produce `use`: an observed use on the buildings of its scene that `detail` reads as ground-floor shop evidence. `config/sources.json` `evidence` ranks it against OSM, separately from the height `priority`: `osm`, then `es.catastro`, then `overture`. Contract and measurements: [ADR-022](ADR-022-catastro-use.md).
