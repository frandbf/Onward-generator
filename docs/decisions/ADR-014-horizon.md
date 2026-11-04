# ADR-014 — Horizon past the perimeter wall: fog and an optional backdrop ring

Status: accepted · 29 October 2026.

## Context

Past the 12 m perimeter wall (`bounds`, collision only, not drawn) the player sees the ground end and the built-in skybox. Nothing hides the edge of the generated area. Two cheap tools are in reach without inventing data:

- linear fog, a scene setting the adapter can write (`RenderSettings`);
- a few opaque quads just outside the wall with a neutral gradient.

Real silhouettes of the buildings outside the area of interest would look better, but they are real OSM data the pipeline does not extract today.

## Decision

1. **Fog in the adapter.** `OnwardMapAdapter.cs` sets `RenderSettings.fog`, `FogMode.Linear`, `fogStartDistance`, `fogEndDistance` and `fogColor` from the export options:
   - `build.fog_enabled` (default `false` when the key is absent, so an older configuration keeps the previous look),
   - `build.fog_start_m`, `build.fog_end_m` (end greater than start; the adapter refuses otherwise),
   - `build.fog_color`, sRGB hex `#RRGGBB`.
   `config/export/onward.default.json` turns it on at 160–1000 m, about the distance from the centre of the Murcia area to its wall. `map create` writes `fog_start_m` = radius + margin (the distance from the centre to the wall along an axis) and `fog_end_m` = start + 600 m. Unity fog is measured from the camera, so it starts at the wall only for a player near the centre.
2. **Backdrop ring in `bounds`, off by default.** `onward-generator bounds --backdrop` adds one visual node `horizon/backdrop` to the copied `scene_visual.glb`:
   - one quad per edge of the site's convex hull (simplified by 1 m), `--backdrop-offset-m` (default 2 m) outside the site, from 1 m below the ground to `--backdrop-height-m` (default 30 m), facing the site;
   - one material `horizon_backdrop`, white base colour, one 4 × 64 px PNG: a vertical gradient from `#C8CDD2` (bottom, the default fog colour) to `#D6E0EC` (top). Generated in code, licence `project-generated`. No building is drawn on it;
   - no collision node, `attribute_origin: generated`;
   - the adapter keeps it out of GI and the lightmap (`ContributesGi`), so it is lit by light probes or the ambient probe;
   - the listing thumbnail leaves it out.
   The contract: `scene_detail.json` (or `scene3d.json`) gets an object of `type: backdrop` and `envelope.backdrop` (`null` when off); `bounds_report.json` gets `backdrop` (`null` when off).
3. **Unknown, checked on the headset.** Whether the Onward runtime or the `UrbanDay1` post-processing profile overrides `RenderSettings` fog, ambient light, or the skybox (`UseBuiltInSkybox = true`). The project's shader stripping keeps every fog mode (`docs/11-onward-sdk.md`). Whether `PP_Opaque_Simple_Lit` applies fog is not documented in this repository.

## Amendment: the ring is on in `run` (4 November 2026)

On the headset, Murcia2026 (no playable core, so the wall stands on the site edge) showed the paving end on the built-in skybox's brown lower half: the player read it as the edge of the world, as if they could fall off. `run` now passes `--backdrop` to `bounds`, at the default 2 m offset and 30 m height; `bounds` on its own keeps it off. The cost is the one under *Cost* below: 2 triangles per hull edge and one draw call. Whether the gradient reads as haze on the headset, and whether the runtime keeps the ring lit as the editor does, is still to be checked there. A painted skyline or the real buildings outside the area (the next step below) remain separate decisions.

## Rejected alternatives

- A custom skybox or a horizon shader. Exported materials stay `PP_Opaque_Simple_Lit` with an albedo, and the skybox stays the built-in one.
- Drawing the perimeter wall. It stands on the site edge; the players would face a grey wall instead of a horizon.
- Backdrop geometry in the adapter. The adapter does not generate content; it reads the package.

## Cost

- Fog: no triangle and no draw call. A per-pixel fog term in the shader variant.
- Backdrop, when enabled: 2 triangles per hull edge. A square map from `map create` has 4 edges (8 triangles); a 30 m margin rounds the corners, so after the 1 m simplification expect about 10–40 edges (20–80 triangles). One material and one node, so one draw call: the combine step groups it in the cell of its centre. Always in view, never culled.
- The visual GLB is rebuilt when the ring is added (the same `build_glb` path `signs` and `overgrow` use). Without `--backdrop` it is still a byte copy.

## Next step, not done: silhouettes of real buildings outside the area

ADR-017 takes the cheaper route first: the buildings already extracted between a playable core and the site edge become plain scenic shells, and the wall moves to the core edge. The band outside the extract below is still not done.

Extract OSM buildings in a band (for example 150–400 m) outside the inclusion zone, extrude each footprint as a prism with its OSM height or levels (or the heights default), no roofs, no openings, one dark haze material, no collision, no lightmap, combined into one mesh per sector. Estimate from the Murcia centre: about 2 000–4 000 footprints in a 400 m band; at about 8 vertices per footprint a plain prism without a base is about 16 wall triangles plus 6 roof triangles, so 45 000–90 000 triangles before simplification. The map already uses about 248 000 of the guide's 250 000 (docs/09-perf.md). It needs footprint simplification to 4–6 vertices, merging adjacent blocks, and dropping buildings hidden behind nearer ones; a target of 10 000–20 000 triangles would still require taking triangles from the playable area. Data, licence and attribution are the existing OSM extract (ODbL), widened.
