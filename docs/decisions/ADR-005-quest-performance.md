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
