# Dress — material classification and a styled copy

An isolated step after phase 04. It does not apply an AI material plan to the scene. `--apply` is refused.

## What is true in the project

| Piece | Current behaviour |
| --- | --- |
| Surfaces | Phase-03 `SurfaceClass` (`carriageway`, `sidewalk`, `pedestrian`, `green`, `water`, `ground`) plus `curb`. Phase-04 buildings carry wall and roof ids in `facade.materials`. |
| Geometry | Volumes in `generate`, generic facades in `detail`. The classifier does not rebuild them. |
| Format | Intermediate GLB. The Unity adapter `onward_unity` 0.1.0 reads `TEXCOORD_0` and an embedded PNG when one exists. |
| Ollama | The same loopback `OllamaProvider` as phase 06. Default model `qwen3:14b`. `format` is the JSON Schema. |
| Textures | `dress style` embeds one procedural tile per material. `materials apply` replaces them from that map's `config/texture_plan.json`. Both repeat each image on the face plane and write `atlas.png` as a contact sheet. See ADR-009. |
| Applying the classifier | Off. `--apply` is refused. |

## Classify

```bash
.venv/bin/python -m onward_generator dress classify \
  --detail maps/<id>/data/processed/scene_detail \
  --out    maps/<id>/data/processed/dress_classify \
  --provider none \
  --limit 12
```

- `--provider none` (the default): deterministic rules. No call to Ollama.
- `--provider ollama --model qwen3:14b`: the model may only choose catalogue ids. Code checks the ids, the role, the confidence, and the OSM tags again.
- `--provider replay --replay classify_run.json`: revalidates a stored reply.
- `--limit 12`: a small sample. `--limit 0` is every group.
- `--apply` is always refused.

Output: `material_plan.json`, `surface_groups.json`, `classify_run.json`.

Each recommendation has `surface_id`, `recommended_material_id`, `confidence`, `reason`, `alternative_material_ids`, and `needs_review`. The report also lists member surfaces (`expanded`) and `geometry.identical`.

Rules:

- Explicit OSM tags (`building:material`, `roof:material`, `surface`, `historic`, `building=cathedral`, and similar) win over the model.
- Building use is not inferred from shape. With no tags the result is `unknown` and a lower confidence.
- Historic walls: cream stucco or stone, from context and tags.
- Roofs: tile only with a pitch, a `roof:shape`, or `roof:material=tile`. Otherwise a neutral flat roof.
- Streets and plazas: asphalt or cobble from `surface` and the role.
- Confidence under 0.4 becomes a safe neutral material and `needs_review`.

## Style

```bash
.venv/bin/python -m onward_generator dress style \
  --detail maps/<id>/data/processed/scene_detail \
  --out    maps/<id>/data/processed/scene_dress
```

If the detail manifest stores the paths, `--scene`, `--heights`, and `--base` are filled in. The detail directory is not overwritten.

`--no-atlas` leaves the GLB with flat colour only and writes no `atlas.png`. `--scene` and `--heights` are used to place furniture on free ground. On a map with a playable core (ADR-017) that ground is cut to the core, so the 16 benches and 10 bins stay where players are. Facades are not rebuilt. Interior meshes are copied with the building: the floor, the walls that face the room, and the stair steps. The stair ramp stays on the building collider. Dropping the interior node leaves the ground colour, a wall that is invisible from inside, and a stair that can be climbed but not seen.

The tiles are generated in code: **greyscale modulation** (cobble, tile, asphalt, bark, foliage) so `PP_Opaque_Simple_Lit` can do `_BaseColor × _BaseMap` without darkening the tint. Each tile is 256 px. The GLB embeds one tile per material as its own image, repeated in metres of the catalogue `tile_m` (2 m when the catalogue has none), the same way as `materials apply`. Historic plaster uses a 2 m `tile_m`. `atlas.png` puts the same tiles on one grid (8×8, up to 2048²) as a contact sheet for review. No material samples it, and the GLB extras mark it `"role": "contact_sheet"`. Before this change the GLB embedded that grid and wrapped each vertex into its slot, which smeared every triangle that crossed a tile edge. Scenes already written keep the old atlas. Licence: `project-generated`.

The adapter does not bake lightmaps on trees, urban metal, railings, or overgrow props (material `prop_<id>`). At a low texel density, poles and canopies came out black, and on the headset air-conditioner units flush against a wall came out solid black. Those renderers neither contribute GI nor receive a lightmap; they are lit by light probes, or by the ambient probe where the scene has none (`docs/12-export.md`).

There are no vehicles in this step. Inventing cars on the carriageway is not OSM data. Wrecks, when the brief asks for them, are placed later by `overgrow`.

## Materials

`onward-generator materials apply` is the texture step. It reads `maps/<id>/config/texture_plan.json`. `map create` writes that plan from the imported library. `map create --textures` replaces it and leaves the rest of the map alone. A slot whose family has no texture in the library is named in the plan description and keeps the procedural tile. The library has no bark yet, so `trunks` is one of them. Historic plaster uses the photograph in greyscale and keeps the colour in `_BaseColor`. An older Poly Haven plan can still download Diffuse 1k with `--download`. The GLB carries one image per material, repeated on the wall plane. The 256 px atlas stays as a contact sheet. Windows and doors map to 0–1. Interior walls reuse the plain-facade photograph when the plan has one, otherwise the historic or ochre plaster. Interior floors reuse the door wood. Stairs reuse the sidewalk concrete. Shop interior colours (`interior_shop_*`) get no photograph and keep their colour. A colour photograph is shown with a white base colour. A greyscale photograph keeps the interior tint. These surfaces are not new plan slots. The `plaster_salmon` facade (one of the residential and retail wall materials, `docs/07-detail.md`) has no slot either: it takes the plain-facade photograph, else the ochre one, else the historic one, in greyscale under its own salmon tint, so it does not look like the slot it borrows from. The three scenic shell walls (`scenic_wall_*`, ADR-017) take a plaster photograph the same way, the plain-facade one first. The materials report lists them under `textures.facade_shared`. A tint (`<base>__tint_<key>`, from `building:colour` or `roof:colour`, `docs/07-detail.md` and ADR-023) takes its base's photograph in greyscale under its own colour, repeats at its base's scale, and a tinted roof is mapped as a roof; a base with no photograph leaves the tint its procedural tile. The report lists them under `textures.tint_shared`. This step does not apply `dress classify`. Licences: [legal/data-sources.md](legal/data-sources.md).

### Trim-sheet facades

Decision: [ADR-020](decisions/ADR-020-trim-sheet-facades.md). By default (`--facades trim`, `--facade-style auto`) `materials apply` puts the facade parts of every building, building part and landmark tower on one 2048 × 4096 px atlas of a regional style, baked in code from three CC0 library photographs with grime in the albedo (`materials/trim_atlas.py`):

- **Style.** `config/facade_styles.json`: the first latitude/longitude box that holds the map centre, else `generic`. Only `mediterranean` has an atlas there; a map whose style has none keeps the per-material path below (`facades.reason: style_has_no_atlas`). `map.json` `"facades"` overrides the style or selects `legacy` (`docs/02-configuration.md`).
- **Regional atlases (opt-in, plan D1).** With `map.json` `"regional_facades": true` (or `materials apply --regional-facades`) `central_european`, `anglo` and `latin_american` take their atlas from `config/facade_styles.regional.json`: the same 2048 × 4096 layout with their own CC0 photographs, tones, dress and grime, and their own windows (casements without roller shutters, sashes, iron grilles). `mediterranean` keeps its atlas and `generic` stays on the per-material path. Their recipes also give **era bands**: a building whose record has an observed era takes that era's tones, window families and dress; one with no observed era keeps the look it would have had. The report adds `regional_table_sha256`, `atlas_origin: regional` and `era_bands` (era `observed`, band `estimated`). Off by default, until the headset session of the ADR-020 and ADR-027 amendments of 8 November 2026.
- **Paris (plan Q3).** A centre inside Paris (48.80–48.93 N, 2.20–2.50 E, the first box of the table) resolves to `haussmann`, which used to be `generic`. With `regional_facades` its regional recipe draws every rendered or stone wall colour of the palette as light limestone ashlar (joints every 0.4 m course and 0.8 m block, on the CC0 grain of `acg_concrete012`), with a stone plinth on every building, a cornice on 95 % and a string course at every storey on 90 %, tall casements in painted or dark grey frames with shutters mostly open, and dark green or blue-black doors. Brick stays brick. A recipe may fix a strip's tint with `wall_tints` (linear RGB per wall of `walls`), and Paris does, so the limestone is the same whatever the palette assigns. Without the option a Paris map keeps the per-material path, as before. Not drawn yet: the wrought-iron balconies of the second and fifth floors and the zinc mansards, which are geometry.
- **Walls** are cut into bands (zocalo to 0.9 m, each storey, the parapet) and each band takes its strip: the stone zocalo, the storey strip of the wall's colour (string course at the foot, shadow and streaks under the next), the cornice. A wall with no opening takes only the zocalo and the parapet cuts and the middle of its strip. U follows the ADR-012 runs in 12 m repeats.
- **Neighbours differ**: each wall colour has three tones in the atlas (2048 × 4096), and each building draws its tone, and whether it has the stone zocalo, the cornice and a string course at every storey, by sha256 of its id without a Catastro `_part` suffix, so the parts of one parcel share them. `stats.looks` in the report counts them.
- **Atmosphere palette (plan E8, [ADR-037](decisions/ADR-037-atmosphere-presets.md)).** With `materials apply --atmosphere <preset>` (passed by the pipeline when `map.json` names one) the preset's `grime` scales the streaks (`streak_dark`) and the splash at the foot of the wall (`base_splash_dark`) of the style, each wall colour moves `wall_desaturation` of the way to its own grey, and its brightest sRGB channel is capped at `wall_value_max`, before the bake. Only the palette's wall strips change: an observed colour (`building:colour`, a `__tint_` material) is not on the atlas. `facades.atmosphere` in `materials_report.json` records the values and the mean wall luminance before and after (linear, unweighted). Without a preset, or with `neutral`, the atlas is the one of before, byte for byte. Madrid Sol Arena with `golden_hour`: mean wall luminance 0.7083 → 0.5589; the fixed views of the baked map are 1 to 5 grey levels darker on the sunlit walls and unchanged in the shade.
- **Openings and trim**: windows (two families, two shutter positions), doors, shop glass and boarded windows fit 0–1 into cells; reveals, sills, slabs and coping take the stone strip.
- **Materials**: `facade_trim_<style>`, `party_facade_trim_<style>` (party walls) and `scenic_trim_<style>` (scenic shells), one shared image. `glass_view`, railings, roofs, interiors and signs are unchanged. A wall colour the style has no strip for keeps its own material.
- **Output**: `facade_trim.png` (the atlas, for review) and `facades` in `materials_report.json`. The atlas PNG is cached in the tile cache below.

With `--facades legacy` every facade part keeps its own material and image, as described above.

### Tile cache

Each photo tile is a 1024 px PNG, and the same library photographs come back in every map. The photo and procedural tiles of `materials apply` and the `atlas.png` contact sheet are opaque, so they are written as RGB PNGs at zlib level 3 (`encode_opaque_png`, `TILE_PNG_LEVEL`): about three times faster than level 9 and about 4 % larger, and they decode to the same RGBA. The see-through window image, the signs and the `dress style` tiles stay RGBA at level 9. Unity recompresses every texture. `materials apply` bakes and encodes the tiles on 6 threads, and keeps each baked photo tile in a content cache (`materials/tile_cache.py`):

- **Place.** `$ONWARD_GENERATOR_CACHE_DIR/tiles`, else `$XDG_CACHE_HOME/onward-generator/tiles`, else `~/.cache/onward-generator/tiles`. `tools/docker/run.sh` mounts the host's `${XDG_CACHE_HOME:-~/.cache}/onward-generator` into the container and sets `ONWARD_GENERATOR_CACHE_DIR`, so the cache outlives the container.
- **Key.** The sha256 of the decoded photograph, its shape, the tile side, the albedo mode, the Pillow version, the PNG level and `BAKE_VERSION`. A change to `bake_photo_tile` or `encode_opaque_png` that changes their bytes bumps `BAKE_VERSION`.
- **Entry.** The sha256 of the PNG followed by the PNG. It is written to a temporary file and renamed into place, so runs at the same time never read half an entry. An entry whose hash does not match is baked again and replaced. A cache that cannot be written is skipped.
- **Off.** `--no-tile-cache`, or `ONWARD_GENERATOR_NO_TILE_CACHE=1`. The tests set the variable.

The GLB has the same bytes with a cold cache, a warm one, or none. The cache can be deleted at any time.

### Calm textures

A tile repeats every few metres on a large floor or facade, so a streak, a blotch or a centred feature becomes a visible grid. A headset test saw the rusted manhole cover baked into `acg_concrete047b` every 2 m along a sidewalk, and plaster stains repeating up a wall. The planner (`materials/library_plan.py`) measures each asphalt, concrete, plaster, brick and rock image before it joins a pool: the image is reduced to 128 px greyscale, blurred with a Gaussian of 4 px (1/32 of the tile), and the standard deviation of the luminance (0–1) is its coarse contrast. Fine grain is blurred away; patches, streaks and covers stay. An image joins the pool only when the coarse contrast is at most its family limit: `COARSE_CONTRAST_LIMIT = 0.012` for asphalt and concrete, `WALL_CONTRAST_LIMIT = 0.025` for plaster, brick and rock, whose mortar and grain read higher without forming a grid. By default roofs, paving and doors are not measured; the `"repetition"` switch below measures roofs and paving.

| Texture | Coarse contrast | Pool |
| --- | --- | --- |
| `acg_asphalt011` | 0.0055 | kept |
| `acg_asphalt020s` | 0.0066 | kept |
| `acg_asphalt017` | 0.0204 | dropped: horizontal light streaks |
| `acg_asphalt019` | 0.0164 | dropped: streaks |
| `acg_asphalt021` | 0.0143 | dropped: crack pattern |
| `acg_asphalt022` | 0.0138 | dropped: blotches |
| `acg_concrete048` | 0.0026 | kept |
| `acg_concrete047a` | 0.0072 | kept |
| `acg_concrete012` | 0.0105 | kept |
| `acg_concrete047b` | 0.0261 | dropped: one rusted manhole cover and a pale patch in the middle |
| `acg_paintedplaster006` | 0.0214 | kept |
| `ph_painted_plaster_wall` | 0.0059 | kept |
| `ph_plastered_wall` | 0.0021 | kept |
| `acg_paintedplaster010` | 0.0254 | dropped: red patches |
| `acg_paintedplaster014` | 0.0492 | dropped: large stains |
| `acg_paintedplaster016` | 0.0656 | dropped: large stains |
| `acg_bricks001`, `059`, `082b`, `105` | 0.0221, 0.0236, 0.0123, 0.0119 | kept |
| `acg_bricks003`, `023`, `097`, `100` | 0.0260, 0.0291, 0.0315, 0.0440 | dropped: pale stones, white mortar, dark bands, large stones |
| `acg_rock030` | 0.0103 | kept |
| `acg_rock022`, `034`, `051`, `055`, `056` | 0.0267 to 0.0476 | dropped: streaks and patches |

The two Poly Haven plasters were already in `assets/textures/`; they joined the imported library so the three plaster slots (`historic_facades`, `ochre_facades`, `plain_facades`) still get three different calm photographs.

The rule applies when a plan is chosen: the deterministic pick and the Ollama reply both use the filtered pools, and a reply that names a dropped image is rejected. It also applies when a plan is loaded: a saved `texture_plan.json` that binds a dropped image is refused by `materials apply`, and the message says to run `map create --name <map> --textures`, which rewrites only that map's texture plan; the next `materials apply` uses it. Before 2026-10-30 such a plan still applied, which is how a map created before the rule kept the repeated cover. The one asphalt slot (`carriageway`) keeps one colour per map.

#### Roofs and paving: the `"repetition"` switch (plan C1)

`map.json` key `"repetition"` (a bool, default `false`; any other value is refused with `MapError`) extends the rule to roofs and paving ([ADR-009](decisions/ADR-009-dress-materials.md), amendment *Repetition per cell*, proposed). With it off, the pools, the deterministic picks, the plan check and every output are as before. With it on:

- `PAVING_CONTRAST_LIMIT = 0.016` and `ROOF_CONTRAST_LIMIT = 0.019` join the table (`EXTENDED_CONTRAST_LIMITS`; `CONTRAST_LIMITS` is unchanged). Paving carries joints and stone grain at a coarser scale than asphalt, and roof tiles carry rows and weathering, so both limits sit between the ground and the wall limits. Every library value is at least 1e-4 away from its limit.
- `map create --name <map> --textures` (`refresh_texture_plan`) and the Ollama check choose roofs and `streets_plazas` from the extended pools. The pool sizes change, so the extended pick for a seed can differ even when the default pick was calm.
- `materials apply --repetition` refuses a library plan that binds an over-limit roof or paving image, before it writes anything, with the same `repeats visibly ... map create --name <map> --textures` message.
- Doors are **not applicable**: `door_wood` is a unit-UV material, each opening is mapped 0-1, so the image never tiles and its coarse contrast says nothing about repetition. Bark has no limit and is reported as **unmeasured**.

| Texture | Coarse contrast | Pool with `"repetition"` |
| --- | --- | --- |
| `acg_pavingstones151` | 0.0073 | kept: small even setts |
| `acg_pavingstones007` | 0.0126 | kept: even diamond pavers |
| `acg_pavingstones008` | 0.0152 | kept: herringbone, light joints only |
| `acg_pavingstones050` | 0.0165 | dropped: fan of red and grey setts whose arcs read as a grid |
| `acg_pavingstones141` | 0.0188 | dropped: large mottled pebbles |
| `acg_roofingtiles014a` | 0.0109 | kept: even tiles |
| `acg_roofingtiles014b` | 0.0156 | kept: light weathering |
| `acg_roofingtiles011b` | 0.0168 | kept: one darker band, faint from the roofs' viewing distance |
| `acg_roofingtiles011c` | 0.0183 | kept, closest to the limit: lichen spots and a darker band |
| `acg_roofingtiles012c` | 0.0211 | dropped: dark stains and moss patches that repeat |
| `kenney_fantasy_door_wood` (and `_frame`, `_handle`, `_window`) | 0.0393 to 0.1305 | not applicable: mapped 0-1 per opening |

With the switch on, `materials_report.json` gets a `repetition` block (written only then, so a report without the switch is byte for byte as before; `schema_version` stays `0.1.0` because the block is optional and additive):

```json
"repetition": {
  "enabled": true, "cell_m": 100.0,
  "cell_phase_materials": ["carriageway", "cobblestone_paving", "pedestrian", "sidewalk"],
  "slots": [{"slot": "roofs", "color_id": "roof_tile", "asset_id": "acg_roofingtiles011b", "family": "roof",
             "coarse_contrast": 0.0168, "limit": 0.019, "over_limit": false, "status": "measured"}],
  "over_limit": []
}
```

`slots` has one row per applied plan binding. `status` is `measured`, `unmeasured` (no family limit, e.g. bark) or `not_applicable` (doors). `coarse_contrast` is rounded to 4 places; `over_limit` is decided on the unrounded value, as `is_calm` does. `over_limit` lists the asset ids over their limit; a library plan cannot reach it with the switch on, but a Poly Haven plan is reported, not refused. The visual GLB records `extras.materials.repetition = {"cell_m": ...}`.

Catalogue repeats for the ground: `asphalt_road` uses `tile_m` 10 m (it was 4 m, an identical grid smaller than a lane), `sidewalk_concrete` stays 2 m. A `tile_m` change reaches an existing map the next time `dress style` or `materials apply` runs.

## Texture coordinates

`dress style` and `materials apply` write `TEXCOORD_0` the same way for every material with a repeating image:

- **Walls and other steep faces:** U runs along the face, V runs up in world metres, both divided by `tile_m`.
- **Street facades** ([ADR-012](decisions/ADR-012-wall-uvs.md)): on a building node with `extras.uv_frame`, a face that lies on an exposed run, faces out, and sits within its segment follows the run instead. U is the distance along the run from its start, divided by `tile_u = L / n` with `n = max(1, round(L / tile_m))` for the run length `L`, so U is continuous at every corner inside a run and the run holds a whole number of repeats (stretch at most ±1/(2n)). A party wall ends a run, and the next run starts again at U = 0. V is the height divided by `tile_v = storey_h / k` with `k = max(1, round(storey_h / tile_m))`, so every storey line from the ground is a whole number of repeats. With `storey_h` null, V stays height / `tile_m`. Party walls, parapet inner walls, reveals, roofs, ground and openings keep the rules here unchanged. Roof materials and nodes with a `matrix` ignore the frame. No vertex, triangle, material or image is added.
- **Level faces:** world X/Y, divided by `tile_m`. A face counts as level when `|n.z| ≥ 0.75`.
- **Roofs:** catalogue entries whose `compatible_surfaces` include `roof` use the face plane up to `|n.z| < 0.98`. U runs along the eave and V up the slope, so a 22° hip or gable measures true metres along the slope instead of a top-down projection that is about 8% short. Only roofs get this rule. `cap()` shares vertices across a whole ground or slab surface, which must keep one planar projection.
- **Windows and doors** (`glass_dark`, `glass_boarded`, `glass_view`, `door_wood` in `materials apply`): each opening fits 0–1.
- **Per-building phase:** `materials apply` adds the hashed `uv_phase` of each building before the next step. On a street facade whose V follows storeys, only U takes the phase, so the storey lines stay whole.
- **Per-cell phase** (`"repetition"` switch only, plan C1): the ground surfaces `carriageway`, `sidewalk`, `pedestrian` and `cobblestone_paving` are one node per surface for the whole map, so they have no building phase. With the switch on, `materials apply` shifts each connected UV island of those materials by `cell_uv_phase(material, cell)`: bytes 0 and 1 of `sha256("cell:<material>:<i>:<j>")` divided by 255. The cell is the island's plan-bounds centre on x and -y floored by the combine cell (`build.combine_cell_m` of the export card, 100 m by default), the same rule as the adapter's `tiers.combine_cell`. The shift is rigid per island, before recentring, so no seam opens inside an island; neighbouring islands in different cells that touch without sharing vertices can show a phase jump at their border. An island that spans several cells takes its centre cell, and an island that is the whole network takes one phase: on the bench maps the carriageway is 1 island in Murcia, 13 in Madrid, 4 in Paris and 2 in Krakow; sidewalks are 71, 137, 23 and 5. Splitting islands at the cell boundaries would add vertices and is left out. `ground` and `green`, nodes with a `matrix`, unit-UV openings, roofs and buildings with their own `uv_phase` (ADR-012) are untouched. No vertex, triangle, material, image or draw is added; downstream stages keep the UVs (`preserve_uvs`).
- **Recentring:** for every connected island of triangles (vertices shared through triangles), `floor(min)` is subtracted from U and V. The fraction of every UV is unchanged, so the texture looks identical with Repeat wrapping, but the values stay within the island's extent plus one repeat instead of reaching hundreds or thousands far from the origin. That keeps the fraction exact if the shader works in half precision. Openings already fit 0–1 and are not shifted. The faces of one street-facade run count as one island, so recentring does not open a seam at its corners.

- **Party walls** (`party_<wall>`, `docs/07-detail.md`): the wall's own image, `tile_m` and colour. Both steps draw the tiles per wall and point the party material at the same texture, so the GLB holds one image for the two; `build_glb` shares a texture between any materials whose images are byte for byte the same. `materials apply` binds the plan to the wall, so a party wall whose wall appears nowhere else still gets the photograph, and an OSM brick building's party walls become `party_brick_red`.

UVs already stored on a primitive (overgrow meshes, Panoramax facades) are copied as they are.

Phase 07 stays open. `device_verified` stays false until the headset checklist is filled in.
