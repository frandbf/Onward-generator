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

`--no-atlas` leaves the GLB with flat colour only and writes no `atlas.png`. `--scene` and `--heights` are used to place furniture on free ground. Facades are not rebuilt. Interior meshes are copied with the building: the floor, the walls that face the room, and the stair steps. The stair ramp stays on the building collider. Dropping the interior node leaves the ground colour, a wall that is invisible from inside, and a stair that can be climbed but not seen.

The tiles are generated in code: **greyscale modulation** (cobble, tile, asphalt, bark, foliage) so `PP_Opaque_Simple_Lit` can do `_BaseColor × _BaseMap` without darkening the tint. Each tile is 256 px. The GLB embeds one tile per material as its own image, repeated in metres of the catalogue `tile_m` (2 m when the catalogue has none), the same way as `materials apply`. Historic plaster uses a 2 m `tile_m`. `atlas.png` puts the same tiles on one grid (8×8, up to 2048²) as a contact sheet for review. No material samples it, and the GLB extras mark it `"role": "contact_sheet"`. Before this change the GLB embedded that grid and wrapped each vertex into its slot, which smeared every triangle that crossed a tile edge. Scenes already written keep the old atlas. Licence: `project-generated`.

The adapter does not bake lightmaps on trees, urban metal, railings, or overgrow props (material `prop_<id>`). At a low texel density, poles and canopies came out black, and on the headset air-conditioner units flush against a wall came out solid black. Those renderers neither contribute GI nor receive a lightmap; they are lit by light probes, or by the ambient probe where the scene has none (`docs/12-export.md`).

There are no vehicles in this step. Inventing cars on the carriageway is not OSM data. Wrecks, when the brief asks for them, are placed later by `overgrow`.

## Materials

`onward-generator materials apply` is the texture step. It reads `maps/<id>/config/texture_plan.json`. `map create` writes that plan from the imported library. `map create --textures` replaces it and leaves the rest of the map alone. A slot whose family has no texture in the library is named in the plan description and keeps the procedural tile. The library has no bark yet, so `trunks` is one of them. Historic plaster uses the photograph in greyscale and keeps the colour in `_BaseColor`. An older Poly Haven plan can still download Diffuse 1k with `--download`. The GLB carries one image per material, repeated on the wall plane. The 256 px atlas stays as a contact sheet. Windows and doors map to 0–1. Interior walls reuse the plain-facade photograph when the plan has one, otherwise the historic or ochre plaster. Interior floors reuse the door wood. Stairs reuse the sidewalk concrete. Shop interior colours (`interior_shop_*`) get no photograph and keep their colour. A colour photograph is shown with a white base colour. A greyscale photograph keeps the interior tint. These surfaces are not new plan slots. The `plaster_salmon` facade (one of the residential and retail wall materials, `docs/07-detail.md`) has no slot either: it takes the plain-facade photograph, else the ochre one, else the historic one, in greyscale under its own salmon tint, so it does not look like the slot it borrows from. The materials report lists it under `textures.facade_shared`. This step does not apply `dress classify`. Licences: [legal/data-sources.md](legal/data-sources.md).

### Tile cache

Each photo tile is a 1024 px PNG, and the same library photographs come back in every map. `materials apply` bakes and encodes the tiles on 6 threads, and keeps each baked photo tile in a content cache (`materials/tile_cache.py`):

- **Place.** `$ONWARD_GENERATOR_CACHE_DIR/tiles`, else `$XDG_CACHE_HOME/onward-generator/tiles`, else `~/.cache/onward-generator/tiles`. `tools/docker/run.sh` mounts the host's `${XDG_CACHE_HOME:-~/.cache}/onward-generator` into the container and sets `ONWARD_GENERATOR_CACHE_DIR`, so the cache outlives the container.
- **Key.** The sha256 of the decoded photograph, its shape, the tile side, the albedo mode, the Pillow version, the PNG level and `BAKE_VERSION`. A change to `bake_photo_tile` or `encode_png_rgba` that changes their bytes bumps `BAKE_VERSION`.
- **Entry.** The sha256 of the PNG followed by the PNG. It is written to a temporary file and renamed into place, so runs at the same time never read half an entry. An entry whose hash does not match is baked again and replaced. A cache that cannot be written is skipped.
- **Off.** `--no-tile-cache`, or `ONWARD_GENERATOR_NO_TILE_CACHE=1`. The tests set the variable.

The GLB has the same bytes with a cold cache, a warm one, or none. The cache can be deleted at any time.

### Calm textures

A tile repeats every few metres on a large floor or facade, so a streak, a blotch or a centred feature becomes a visible grid. A headset test saw the rusted manhole cover baked into `acg_concrete047b` every 2 m along a sidewalk, and plaster stains repeating up a wall. The planner (`materials/library_plan.py`) measures each asphalt, concrete, plaster, brick and rock image before it joins a pool: the image is reduced to 128 px greyscale, blurred with a Gaussian of 4 px (1/32 of the tile), and the standard deviation of the luminance (0–1) is its coarse contrast. Fine grain is blurred away; patches, streaks and covers stay. An image joins the pool only when the coarse contrast is at most its family limit: `COARSE_CONTRAST_LIMIT = 0.012` for asphalt and concrete, `WALL_CONTRAST_LIMIT = 0.025` for plaster, brick and rock, whose mortar and grain read higher without forming a grid. Roofs, paving and doors are not measured.

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

Catalogue repeats for the ground: `asphalt_road` uses `tile_m` 10 m (it was 4 m, an identical grid smaller than a lane), `sidewalk_concrete` stays 2 m. A `tile_m` change reaches an existing map the next time `dress style` or `materials apply` runs.

## Texture coordinates

`dress style` and `materials apply` write `TEXCOORD_0` the same way for every material with a repeating image:

- **Walls and other steep faces:** U runs along the face, V runs up in world metres, both divided by `tile_m`.
- **Street facades** ([ADR-012](decisions/ADR-012-wall-uvs.md)): on a building node with `extras.uv_frame`, a face that lies on an exposed run, faces out, and sits within its segment follows the run instead. U is the distance along the run from its start, divided by `tile_u = L / n` with `n = max(1, round(L / tile_m))` for the run length `L`, so U is continuous at every corner inside a run and the run holds a whole number of repeats (stretch at most ±1/(2n)). A party wall ends a run, and the next run starts again at U = 0. V is the height divided by `tile_v = storey_h / k` with `k = max(1, round(storey_h / tile_m))`, so every storey line from the ground is a whole number of repeats. With `storey_h` null, V stays height / `tile_m`. Party walls, parapet inner walls, reveals, roofs, ground and openings keep the rules here unchanged. Roof materials and nodes with a `matrix` ignore the frame. No vertex, triangle, material or image is added.
- **Level faces:** world X/Y, divided by `tile_m`. A face counts as level when `|n.z| ≥ 0.75`.
- **Roofs:** catalogue entries whose `compatible_surfaces` include `roof` use the face plane up to `|n.z| < 0.98`. U runs along the eave and V up the slope, so a 22° hip or gable measures true metres along the slope instead of a top-down projection that is about 8% short. Only roofs get this rule. `cap()` shares vertices across a whole ground or slab surface, which must keep one planar projection.
- **Windows and doors** (`glass_dark`, `glass_boarded`, `glass_view`, `door_wood` in `materials apply`): each opening fits 0–1.
- **Per-building phase:** `materials apply` adds the hashed `uv_phase` of each building before the next step. On a street facade whose V follows storeys, only U takes the phase, so the storey lines stay whole.
- **Recentring:** for every connected island of triangles (vertices shared through triangles), `floor(min)` is subtracted from U and V. The fraction of every UV is unchanged, so the texture looks identical with Repeat wrapping, but the values stay within the island's extent plus one repeat instead of reaching hundreds or thousands far from the origin. That keeps the fraction exact if the shader works in half precision. Openings already fit 0–1 and are not shifted. The faces of one street-facade run count as one island, so recentring does not open a seam at its corners.

UVs already stored on a primitive (overgrow meshes, Panoramax facades) are copied as they are.

Phase 07 stays open. `device_verified` stays false until the headset checklist is filled in.
