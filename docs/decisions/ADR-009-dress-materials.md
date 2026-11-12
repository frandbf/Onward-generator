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
7. The shader is `PP_Opaque_Simple_Lit`: `_BaseColor × _BaseMap`. No normals, no 8k, no PBR (one opt-in exception for measurement, off by default: [ADR-033](ADR-033-lit-normal-spike.md), proposed). Cobble is only `surface/pedestrian_cobble`. Historic plaster stays greyscale so the cream stays in `_BaseColor`.

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

## Amendment — Repetition per cell (7 November 2026, proposed)

Plan C1 (`docs/plans/2026-11-07-workshop-quality.md`, section 2.1 *Repetición de texturas*). Status: proposed until the headset review.

- **Switch.** `map.json` `"repetition"` (bool, default `false`, any other value refused). With it off the pools, the picks, the plan check and every output are byte for byte as before, because changing a pool changes the deterministic choice for every map.
- **Calm roofs and paving.** With the switch on, the calm-texture rule of `docs/08-dress.md` (*Calm textures*) also measures roofs (`ROOF_CONTRAST_LIMIT = 0.019`) and paving (`PAVING_CONTRAST_LIMIT = 0.016`), calibrated on the library and a visual review: `acg_roofingtiles012c`, `acg_pavingstones050` and `acg_pavingstones141` leave their pools. The plan choice uses the extended pools, and `materials apply --repetition` refuses a library plan that binds an over-limit image. Doors are not applicable (mapped 0-1 per opening); bark is unmeasured.
- **Report.** `materials_report.json` gets a `repetition` block with the coarse contrast, limit and verdict of every applied slot, only with the switch on.
- **Per-cell phase.** The map-wide ground surfaces (`carriageway`, `sidewalk`, `pedestrian`, `cobblestone_paving`) take a hashed UV phase per connected island, keyed on the adapter's combine cell (ADR-005, 100 m default). It is UV only: no vertex, triangle, material, image or draw. The per-building phase on walls (ADR-012) is unchanged.
- **Rejected.** Splitting ground islands at cell boundaries (adds vertices and seams); new tile variants as extra images (adds images and draws, against ADR-005); enforcing the roof and paving limits by default (would refuse existing plans and move every map's picks).
- **To confirm on the headset.** No visible grid at the 2 m and 10 m repeats on a long street and a plaza; no visible jump at the 100 m cell boundaries; no repeated blotch on the roofs seen from the elevated spawns; draws and frame rate in views A and B unchanged. Then this amendment moves to accepted, or the limits are corrected.

## Amendment (12 November 2026): the slots and ids the texture model may name

Status: accepted (plan Q9 follow-up). The schema sent as Ollama's `format` for the texture plan now enumerates, per slot that has a pool, that slot and the ids of its family's pool, with exactly one choice per such slot. Ollama samples only what the schema allows, so the model can no longer pick another family's texture or fill a slot that has no pool. `_parse_choices` still validates the reply. The first Rue Mouffetard map run from Docker (plan Q9) had its reply rejected for `trunks cannot use acg_concrete012`; with the enums the model's plan was accepted on the first attempt.
