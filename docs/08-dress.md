# Dress — material classification and a styled copy

An isolated step after phase 04. It does not apply an AI material plan to the scene. `--apply` is refused.

## What is true in the project

| Piece | Current behaviour |
| --- | --- |
| Surfaces | Phase-03 `SurfaceClass` (`carriageway`, `sidewalk`, `pedestrian`, `green`, `water`, `ground`) plus `curb`. Phase-04 buildings carry wall and roof ids in `facade.materials`. |
| Geometry | Volumes in `generate`, generic facades in `detail`. The classifier does not rebuild them. |
| Format | Intermediate GLB. The Unity adapter `onward_unity` 0.1.0 reads `TEXCOORD_0` and an embedded PNG when one exists. |
| Ollama | The same loopback `OllamaProvider` as phase 06. Default model `qwen3:14b`. `format` is the JSON Schema. |
| Textures | `dress style` embeds a procedural atlas. `materials apply` remaps tiles from that map's `config/texture_plan.json`. See ADR-009. |
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

`--no-atlas` leaves the GLB with flat colour only. `--scene` and `--heights` are used to place furniture on free ground. Facades are not rebuilt. Interior meshes are copied with the building: the floor, the walls that face the room, and the stair steps. The stair ramp stays on the building collider. Dropping the interior node leaves the ground colour, a wall that is invisible from inside, and a stair that can be climbed but not seen.

The atlas is a PNG generated in code: **greyscale modulation** (cobble, tile, asphalt, bark, foliage) so `PP_Opaque_Simple_Lit` can do `_BaseColor × _BaseMap` without darkening the tint. Each tile is 256 px. The grid grows (8×8, up to 2048²) so `tree_canopy`, `tree_trunk`, and `urban_metal` do not land on an empty or dark-metal tile. Historic plaster uses a 2 m `tile_m`. Licence: `project-generated`.

The adapter does not bake lightmaps on trees or urban metal. At a low texel density, poles and canopies came out black.

There are no vehicles in this step. Inventing cars on the carriageway is not OSM data. Wrecks, when the brief asks for them, are placed later by `overgrow`.

## Materials

`onward-generator materials apply` is the texture step. It reads `maps/<id>/config/texture_plan.json`. `map create` writes that plan from the imported library. `map create --textures` replaces it and leaves the rest of the map alone. A slot whose family has no texture in the library is named in the plan description and keeps the procedural tile. The library has no bark yet, so `trunks` is one of them. Historic plaster uses the photograph in greyscale and keeps the colour in `_BaseColor`. An older Poly Haven plan can still download Diffuse 1k with `--download`. The GLB carries one image per material, repeated on the wall plane. The 256 px atlas stays as a contact sheet. Windows and doors map to 0–1. Interior walls reuse the plain-facade photograph when the plan has one, otherwise the historic or ochre plaster. Interior floors reuse the door wood. Stairs reuse the sidewalk concrete. A colour photograph is shown with a white base colour. A greyscale photograph keeps the interior tint. These surfaces are not new plan slots. This step does not apply `dress classify`. Licences: [legal/data-sources.md](legal/data-sources.md).

Phase 07 stays open. `device_verified` stays false until the headset checklist is filled in.
