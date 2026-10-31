# ADR-009 — Dress: material recommendations stay separate from the geometry

Status: proposed (dress 0.1, 2026-10-19). Updated for the per-map texture plan.

## Context

Flat-colour volumes needed textures, better trees, and sidewalk furniture, plus a local classifier that recommends a material per surface. The model must not move OSM footprints or invent materials. AI is optional, and every model output is validated.

## Decision

1. **`dress` is an isolated step**, not a silent edit of `detail`.
2. **An explicit catalogue** (`config/dress/materials.catalog.json`). Each id points at a `color_id` that already exists. A `kind: file` requires a real file.
3. **`classify` only recommends.** It groups surfaces, calls Ollama with a JSON Schema (the enum is the catalogue ids) or uses rules, validates in code, and writes a report. `--apply` is refused. OSM wins over the model. The input geometry is fingerprinted and compared.
4. **`style` writes a copy of the `detail` GLB.** It does not rebuild facades. It keeps each interior (floor, room walls, stair steps) and replaces street instances (16-sided trees, benches, bins). It embeds one procedural greyscale tile per material, repeated on the face plane, and writes the same tiles as an `atlas.png` contact sheet (a grid that fits every material). The adapter leaves trees and urban metal out of the lightmap. Wall UVs follow the storey and run frame that `detail` writes in node extras ([ADR-012](ADR-012-wall-uvs.md)).
5. **`materials apply` is the texture step**, on another copy. It reads that map's `config/texture_plan.json`. `map create` writes the plan from `config/dress/imported_library.json` (ambientCG Color 1k and Kenney, both CC0). Local Ollama picks one tileable albedo per slot. An invalid reply is retried once. If Ollama is down, the same library is picked deterministically and the plan records that fallback. There is no shared texture template. `map create --textures` rewrites only that file.
6. The Unity adapter reads `TEXCOORD_0` and an embedded PNG when one exists. Older colour-only GLBs stay valid.
7. The shader is `PP_Opaque_Simple_Lit`: `_BaseColor × _BaseMap`. No normals, no 8k, no PBR. Cobble is only `surface/pedestrian_cobble`. Historic plaster stays greyscale so the cream stays in `_BaseColor`.

## Rejected alternatives

- Applying the classifier to a whole map before a report is reviewed.
- Inventing texture files or downloading unlicensed photographs.
- Inferring "historic" from the footprint size. Only tags, or `unknown`.
- Random vehicles on the carriageway. They invent geography and block play.

## Consequences

- The classify report stores the raw reply, the catalogue hash, and the geometry comparison. It can be audited and dropped.
- A future classify apply must be opt-in, on a copy, and must reuse the validated remap.
- The atlas is not a photograph of a city. An older Poly Haven plan can still download Diffuse 1k CC0 with `--download`. A library plan reads files that are already imported.

## Amendment — colour tints (31 October 2026)

[ADR-023](ADR-023-simple-3d-buildings.md) adds tinted copies of catalogue materials (`<base>__tint_<key>`) for OSM `building:colour` and `roof:colour`. `materials apply` and `dress style` give a tint its base's texture, in greyscale under the tint colour, and its base's repeat. No photograph is added.
