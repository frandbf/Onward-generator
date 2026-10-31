# ADR-020 — One regional trim-sheet atlas for the facades

Status: proposed · 30 October 2026. Realism study items #4 (trim sheets per regional style) and #5 (grime and ambient occlusion baked into the albedo). Amends [ADR-009](ADR-009-dress-materials.md) decision 5 for facades and builds on [ADR-012](ADR-012-wall-uvs.md). Headset validation is open: the per-material path stays selectable until it passes.

## Context

- The adapter combines the visual GLB by (100 m cell, material) (ADR-005), so every material a cell holds is one draw. A core cell holds up to six wall colours (`plaster_ochre`, `plaster_cream`, `plaster_white`, `plaster_salmon`, `stone_warm`, `brick_red`, from the facade palettes by use of `docs/07-detail.md`), their `party_*` copies, and `frame_stone`, `glass_dark` and `door_wood`: up to fifteen draws for facades alone, against a budget of about 150 per view.
- Each of those materials embeds its own image. The adapter builds every image as an uncompressed RGBA32 `Texture2D` with mipmaps, so each 1024 px photograph costs 5.3 MiB on the headset.
- `PP_Opaque_Simple_Lit` is `_BaseColor × _BaseMap` and nothing else: no normal map, no occlusion map, no detail map, no second UV set for albedo, and no evidence that it reads vertex colours. Plinths, string courses, cornices, window surrounds, shutters and grime can only exist in the albedo.
- Before this change a wall was one repeating plaster photograph, the same on every storey, with flat reveals, a picture of a casement window and a Kenney door.

- On Madrid r150 the per-material path drew whole blocks in the same pale plaster: one Catastro parcel is one look (ADR-016), a parcel there is often a whole block, and `plaster_white`, `plaster_cream` and `plaster_salmon` differ little under the same photograph. Blocks across a street read as one colour.

Measured on 31 October 2026 (`run --no-unity`, r150, then `run --from materials` per path; the export package GLB, combine at 100 m; texture memory is every embedded image as RGBA32 with mips, which is what the adapter loads):

| Map | Path | Materials | Predicted draws | Triangles | Images | Texture memory |
| --- | --- | --- | --- | --- | --- | --- |
| Madrid 40.40657, −3.719187 | per material | 105 | 224 | 134 693 | 66 | 101.5 MiB |
| Madrid | trim, `mediterranean` (auto) | 94 | 180 | 141 477 | 62 | 122.5 MiB |
| Kraków 50.0614, 19.9366 | per material (auto: `central_european`, no atlas yet) | 123 | 499 | 149 396 | 79 | 163.5 MiB |
| Kraków | trim, `mediterranean` forced | 110 | 354 | 161 975 | 75 | 184.5 MiB |

The trim path takes 20 % of the predicted draws off Madrid (−44) and 29 % off Kraków (−145), and 11–13 materials, for 5.0 % (Madrid) and 8.4 % (Kraków, taller, more storeys) more triangles. Texture memory grows by 21 MiB: the atlas is 2048 × 4096 (42.7 MiB) to hold three tones of every wall colour, against 21.3 MiB for the per-part images it replaces. A first 2048² version with one tone measured the same draws and no memory change, and the monotony above. The rest is ground, roofs, props and signs. These maps have no scenic ring (core = radius).

## Decision

1. **One atlas per style, 2048 wide × 4096 high, horizontal strips.** Each strip spans the full width, so U repeats along a facade with the GPU's Repeat wrap while V stays inside the strip. From the top (`materials/trim_style.py`, `trim_layout`):
   - a fit row of 384 px: eight 192 px cells, fitted 0–1 by the openings: four windows (two families of frame and shutter colour, each with two roller-shutter positions), two panelled doors, shop glass, boarded planks;
   - the cornice (128 px), the zocalo (128 px) and the stone trim (64 px);
   - one 184 px strip per wall colour of the style and tone (`tones`, three: the colour, a darker aged one, a warmer one), each one storey high.
   Every strip and cell has an 8 px gutter that repeats its edge pixels, so filtering and the first mip levels stay inside.
2. **Walls are cut into bands.** `materials apply` joins the triangles of each vertical wall plane back into its face, cuts it at the zocalo line (0.9 m), at every storey line and at the foot of the parapet, and triangulates each band again (constrained Delaunay, as `cap` does). Band 0 maps to the zocalo strip (on a building with a zocalo, decision 5), each storey to the strip of the wall's colour (a string course at its foot, the shadow of the next one at its head), the parapet band to the cornice. A face with no opening (a party wall, a blind wall) is cut only at the zocalo and the parapet and samples the middle of its colour strip, stretched, so it costs 6 triangles instead of 2 per storey. Storeys come from `facade.storeys` of the building record; a storey taller than 4.5 m (a landmark's single band) is split into bands near 3 m. U is the ADR-012 run coordinate (whole repeats per run, continuous round a corner), or the face plane off a run, in 12 m repeats (6 m on the zocalo and the cornice).
3. **Openings and trim fit cells and the stone strip.** A `glass_dark` back at least 1.9 m wide, or starting at the ground, is shop glass; any other is a window of the building's family (by `sha256` of its look key, decision 5), its shutter position by `sha256(key, centre)`. `door_wood` takes one of the two doors by the same key, `glass_boarded` the planks. Reveals, sills, balcony slabs and coping (`frame_stone`) take the stone strip, V fitted per connected piece.
4. **Fewer materials.** Everything above in a building becomes one primitive of `facade_trim_<style>`; blank party walls (`party_<wall>`) become `party_facade_trim_<style>`, so they keep the party lightmap class (0.5); scenic shells (`scenic_wall_*`, ADR-017) become `scenic_trim_<style>` on the light, warm and ochre strips, so they keep the scenic class (0.25). The three materials share one image (`build_glb` already shares identical images). `glass_view` (transparent, `PP_Sprite_Lit`), `railing_metal` (out of the lightmap), roofs, interiors, signs and props are unchanged. A wall colour the style has no strip for (`stone_light`, `building_wall`, or a future observed `building:colour`) keeps its own material, and the report counts it under `kept_materials`.
5. **Neighbours differ.** Each building draws a look by sha256 of its look key: the tone of its wall strip (one in three), and whether it has the stone zocalo (`dress.zocalo_share` 0.7), the cornice (`cornice_share` 0.6) and a string course at every storey (`course_share` 0.5). A building without them keeps its wall strip there: no zocalo cut, a plain parapet band, the middle of the strip on every storey. The window family and the door are drawn for the same key. The look key is the building id less a Catastro `_part<n>` suffix, so the constructions of one parcel keep one look, as they keep one wall colour. Two neighbours with the same wall colour now differ in tone two times in three, and in their dress most of the time. On Madrid r150 the 84 buildings split 34/12/38 over the three tones.
6. **Grime is baked, deterministic.** The bake (`materials/trim_atlas.py`) draws in linear light and writes sRGB: wall colour × the photograph's grain (divided by its mean, so a dark rock does not darken a light stone) × low-frequency mottling × the string course × the shadow under the next course × 70 streaks per strip running down from the top of the storey or the coping; a splashed, darker foot and a lit chamfer on the zocalo; a dark drip under a stepped cornice. The random numbers come from `sha256("trim:" + style)`. Darkened corners are left to the lightmap: the band strips repeat, and a corner is not at a fixed U.
7. **Colours are the palette's.** Each wall strip is the colour the scene gives that material (else `config/detail.default.json`), so the facade palettes by use, the neighbour rule and OSM `building:material` (brick) choose a strip exactly as they chose a material. The texel for the first tone is `sRGB(colour × grain)`, the same product the shader formed before; the other tones multiply the colour first.
8. **Style table and selection.** `config/facade_styles.json` (schema beside it) lists `mediterranean` (with its atlas), and `central_european`, `anglo`, `latin_american` and `generic`, whose `atlas` is `null` until they are drawn: a map in one of them keeps the per-material path and says so (`reason: style_has_no_atlas`). The style is the first rule whose latitude/longitude box holds the map centre (`frame.origin_wgs84` of the detail manifest), else `generic`. The boxes are coarse on purpose, and `map.json` `"facades": {"style": "<name>"}` overrides them; `{"mode": "legacy"}` selects the per-material path (`materials apply --facades legacy`).
9. **Licensing.** The atlas is generated code over three CC0 photographs already in the imported library (`ph_plastered_wall`, `acg_rock030`, `acg_bricks105`); each is read through `texture_record`, which checks its library hash, and refused unless its licence is CC0. Licence of the atlas: `project-generated` (`docs/legal/data-sources.md`). The OSM2World default style was not used.
10. **Cache.** The PNG is kept in the tile cache under the sha256 of the style recipe, the tints, the source hashes, `TRIM_BAKE_VERSION`, Pillow, numpy and the PNG level. The bytes are the same with a cold cache, a warm one or none; `--no-tile-cache` bypasses it.
11. **Lightmap UVs.** Unchanged: the adapter generates the second UV set on each combined mesh (`Unwrapping.GenerateSecondaryUVSet`). The band cut adds edges at storey lines, which the unwrap treats as any other edge.
12. **Report.** `materials_report.json` gets `facades`: `mode`, `style`, `style_origin` (`map_centre`, `config` or `default`), the table hash, the materials, the atlas (size as `[width, height]`, sha256, cache key, sources and licences) and `stats` (buildings, primitives merged, wall triangles before and after the cut, kept materials, openings per cell, and `looks`: buildings per tone and with a zocalo, a cornice, string courses). `facade_trim.png` beside it is the atlas for review. `run` prints `facades trim mediterranean` on the `materials` line.

## Interactions

- **Party walls** (`docs/07-detail.md`): a hidden one is already gone; a visible one maps like a plain face onto its wall's strip under `party_facade_trim_<style>`.
- **Shop fronts**: shopfronts and glazed shop doors on `glass_dark` take the shop glass cell; vinyls and signs are separate meshes and materials (`signs place`) and do not change. The frameless display window of a shop interior (`shop_glass`) and the interior panes (`glass_view`) keep their own see-through materials.
- **Catastro setbacks** (ADR-016): each construction of a parcel is its own node with its own storeys, so its bands follow its own height; it shares the parcel's look.
- **Scenic shells** (ADR-017): plain faces, so zocalo, body and parapet, on the strip their `scenic_wall_*` maps to, in the building's tone.
- **Interiors** do not change.
- **Panoramax** (off by default) replaces facade images of exposed faces after `materials`; with trim on it would treat the merged primitive as a wall. Not tested together: keep `"facades": {"mode": "legacy"}` on a map that turns Panoramax on.
- **OSM colours** (`building:colour`, in progress elsewhere): an observed colour is not a strip, so it keeps its own material. Quantising observed colours to a few strips per style is the way to keep them inside the atlas.

## Rejected alternatives

- **Slicing every wall triangle at the band lines.** The constrained Delaunay faces of `detail` are long slivers across storeys: on Madrid (before the party-wall change) the wall triangles went from 22 378 to 78 278. Rebuilding each face per band gives 31 390.
- **Cutting the bands in `detail`.** Cheaper still, but it changes phase 04 geometry for every map and both paths, and `detail/facades.py` is shared with other work in progress. The texture step owns texture coordinates (ADR-012).
- **One material per colour sharing the atlas image.** Saves memory, not draws.
- **Vertex colours for the tint.** The shader is not known to read them.
- **A taller strip for a whole facade.** One strip over six storeys gives about 12 texels per metre vertically.
- **Tones on a 2048² atlas.** Eighteen strips of 112 px: 37 texels per metre up a storey, so the string course would be four pixels. The 4096 height keeps 61.
- **A tone per construction rather than per parcel.** More variety in a block, but it undoes the one look per parcel of ADR-016.
- **Darkened corners in the strips.** A strip repeats along the run, so its dark edge would come back every repeat, not at the corner.

## Consequences

- Facade draws per cell drop from up to fifteen to at most three, with one texture for all of them: −20 % predicted draws on Madrid, −29 % on Kraków. Triangles grow by the band cuts (two per storey and face with openings, four per plain face): +5.0 % on Madrid, +8.4 % on Kraków. Texture memory grows by 21 MiB.
- The texel density is lower than a 1024 px photograph tiled every 2 m: about 170 texels per metre along a wall and 56 per metre up a 3 m storey. The window, door and shop cells are 176 × 368 px.
- The atlas is not a photograph of a city. Facades stay generic templates (`representative_of_real_facade: false`).
- To check on the headset: the draw count in the two fixed views, the strips' seams (gutters) at a distance, the string course and the zocalo at eye level, the shutters and doors up close, the light on party walls and scenic shells, whether the stretched body of a plain wall reads as weathering or as a smear, whether neighbouring blocks of one colour read apart through their tones, and the frame time with the 4096 px texture.
