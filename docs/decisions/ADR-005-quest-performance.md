# ADR-005 — Quest performance: measure on the headset, combine meshes by cell (phase 05)

Status: accepted (phase 05 accepted on 2026-10-19).

## Context

Phase 05 prepares the scene for a Quest 3 without degrading play, against the Onward SDK guide and numbers measured on the headset.

The official guide (*Custom Content Guide 2.0*, p. 67) gives rough Quest figures: about 150 draw calls, about 250 000 triangles, and about 1 GB of textures. Those figures include players and objectives.

On the headset, the phase-04 scene (v00) drew 578 calls in view A and 171 in view B. The cause was 1 356 primitives: one submesh per node and material.

## Decision

1. **Measure on the headset, not in the Editor.**
   - Variants are compared with OVR Metrics Tool (fixed route, same window) and `ovrgpuprofiler` traces (fixed views).
   - Editor figures are stored. They do not validate a change.
2. **Combine visual meshes by (100 m cell, material)** in the Unity adapter (`combine_cell_m`).
   - The generator, the GLB, and the collision meshes do not change.
   - This is the mesh combination the official guide recommends.
   - Each combined mesh gets its own lightmap UVs.
3. **Static batching and occlusion culling stay off.** v01 tried both on the headset and did not reduce draw calls (571 against 578 in view A).
4. **Cell size comes from an offline prediction** (`perf measure --combine-cell-m`) **and from the headset.**
   - At 100 m, the uncullled maximum was 217 draws.
   - The headset measured 96 in view A and 67 in view B.
   - The 50 m variant was built (483 at most) and not measured on the headset: 100 m already met the guide with margin.
5. **A budget states a limit only when it has a source.** Cited limits are the official guide, the headset refresh, and these measurements. Where there is no source, the measurement is recorded and no limit is invented.

## Consequences

- Draw calls drop by about 2.5× to 6× depending on the view. Triangles, materials, and collision do not change.
- Culling is coarser: view B shaded more vertices (143k against 87k). GPU time did not get measurably worse; the differences sit inside run-to-run variation.
- Instanced meshes (lamps, trees) are copied into each combined mesh. The package grew from 11.55 MB to 12.03 MB on that measurement.
- Lightmap UV overlaps at 2 texels/m were already present in v00.
- Triangles (247 627 in Unity, including SDK prefabs) sit on the guide's limit of about 250 000, which includes players. If a match with players requires it, the next measured lever is `frame_stone` (ADR-004).
- The export adapter applies the 100 m combine (ADR-007). `MurciaPerfBuilder` remains the measurement tool. It is not the adapter.

## Amendment, 8 November 2026: lightmap page budget (plan C5, U2)

Status: proposed until a Unity bake (A3) and a headset session check it (plan C5, `docs/plans/2026-11-07-workshop-quality.md`; U2 of `docs/plans/2026-10-30-scale-and-speed.md`). Off by default. No new Quest budget is fixed without measuring it on the headset (decision 5); this amendment gives the user a way to set one, not a value.

1. **Opt-in.** The export card's `build.lightmap_pages_max` (integer 1 to 64, absent means off) caps `lightmap_estimate.pages_at_least`. A card without the key runs none of this, and the package is the same, `package_id` included.
2. **Applied in Python.** `export package` lowers `lightmap_texels_per_m` to the largest multiple of 0.25 texels/m whose estimate fits the budget (bisection; the capped page count never falls as the density rises), with the resolved class scales and plan C4's `scenic_out_of_lightmap`. A density within the budget is kept. The applied density goes to `adapter_options.json`, which the adapter reads for both `lightmapResolution` and the unwrap's pack margin, so the C# is unchanged. Draft and final bakes use the same density (`docs/12-export.md`, bake profiles).
3. **Floor.** Never below 4 texels/m, twice the 2 texels/m at which the overlaps above were seen. When the budget does not fit there, the package is refused with an `ExportError` at `/build/lightmap_pages_max`, not warned: the user asked for the budget.
4. **Recorded.** `lightmap_estimate` is computed at the applied density and adds `texels_per_m_requested` and `lightmap_pages_max`.

On the bench (`docs/09-perf.md#lightmap-page-budget`) a budget of 8 changes only Madrid r300: 10 → 8.75 texels/m, 11 → 8 pages; with C4 on as well its density stays at 10. Triangles and draws do not change.

Risks, for the bake and the headset: `pages_at_least` is a lower bound with no packing or padding (Unity packed 24 pages where the estimate said 3 on Paris at 40 texels/m), so a bake can exceed the budget until A3 calibrates it; a lower density gives softer shadows and may bring seams or overlaps near the floor, which is itself unmeasured; Unity may round a fractional `lightmapResolution` such as 8.75.

To check before the status is accepted: a Unity draft and final bake of bench_madrid with and without the budget, compared with `tools/unity/report_timings.py` (`lightmaps` at most the budget, or the estimate's error recorded); the scene log's density line and overlapping-UV count; Editor views of a close facade; and on Quest 3 the shadow quality, at least 72 fps median, at most 150 draws in views A and B, at most 1 GB of graphics memory.

## Amendment (10 November 2026): one atlas for props and furniture

A draw audit of Madrid Sol Quest r200 found 479 draw groups. 260 were props and furniture, which carried 21 % of the triangles: every library model had its own `prop_<id>` material, so each distinct model cost one draw per 100 m cell (or per building and storey for furniture).

`overgrow place` now packs the albedo of every prop whose UVs stay in the unit square into a shared 2048 px atlas (`prop_atlas_<n>`, `overgrow/atlas.py`):
- a flat colour takes an 8 px cell, a texture at most 256 px, each with 4 px of its edge repeated;
- the UVs are scaled into the cell;
- the visual GLB's `extras.prop_atlas` records each material's cell, so `perf cover` puts its props in the same atlas;
- a prop whose texture repeats keeps its own material.

The name keeps the `prop_` prefix, so the adapter's lighting rules are unchanged.

On the same map the draws drop from 423 to 279. Per view, the median goes from 144 to 113 and the p90 from 321 to 204. A new model no longer costs a draw, so the furnishing `max_assets` caps rise from 16 to 40 (shops) and from 12 to 30 (floors); the schema maximum is 64.

## Amendment (10 November 2026): the triangle target is the official maximum

The maintainer set the triangle target to the guide's figure: at most 250 000 triangles in Unity (`scene_report.json` `triangles`), no longer 220 000. That count holds the scene and the SDK prefabs. It does not hold the players' avatars or weapons, which the guide's figure includes, so a map near 250 000 leaves no margin for them. The headset session decides whether that holds at 72 fps. Madrid Sol Quest r200 is at 226 750.


## Amendment (12 November 2026): occlusion culling on, 300 000 triangles

Decision 3 kept occlusion culling off because v01 (before combining) did not reduce draws. Combined by 100 m cells, Rue Mouffetard was baked twice from one scene, without (pkg_07) and with occlusion culling (pkg_08): the same 233 323 triangles, 17 s more bake (115.6 s against 98.7 s) and 0.5 MB more bundle. The maintainer tried pkg_08 on the Quest 3, found it good, and decided:

- A new map's export card sets `build.occlusion_culling: true` (`maps/workspace.py`); the adapter marks the static geometry Occluder and Occludee Static and runs `StaticOcclusionCulling.Compute()` in the bake. A card written before keeps its own value.
- The Unity triangle limit (`export/triangle_budget.py` `UNITY_TRIANGLE_LIMIT`, `run_export.sh push`) is 300 000, above the guide's rough 250 000. The headset checks of `docs/09-perf.md` (72 fps median, at most 150 draws in views A and B) stay the way to tell whether a denser map still plays.
