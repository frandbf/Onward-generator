# ADR-025 — Flat openings above the second storey

Status: proposed · 31 October 2026. Amends [ADR-004](ADR-004-facade-detail.md) decision 2 (openings with reveals). Builds on [ADR-005](ADR-005-quest-performance.md) (the triangle budget) and [ADR-020](ADR-020-trim-sheet-facades.md) (the trim sheet). Headset validation is open.

## Context

- The Onward guide gives about 250 000 triangles for the whole match, players, objectives and SDK prefabs included (ADR-005). The Murcia measurement sat at 247 627 in Unity.
- Every opening of `detail` is a hole in the wall, four reveal quads in `frame_stone` (jambs, head and sill) and a back quad 0.15 m in: 10 triangles, 8 of them reveals. ADR-004 measured the reveals at 45 % of the visual triangles (106 500 of 235 327) and named them the first lever; ADR-005 repeats it.
- Players stand at street level. A 0.15 m reveal on the third storey, about 7 m up, is seen at a steep angle from below or from across a 10 m street, where it shows as a thin line on one side of the window. Under `PP_Opaque_Simple_Lit` with a baked lightmap, the reveal's shading is the only depth cue; on the headset it is a few pixels.
- The trim sheet (ADR-020) already paints a frame, mullion, shutter box and a darkened foot in every window cell, so a flush window still reads as a window.

Measured on 31 October 2026 (`tools/docker/run.sh onward-generator run --no-unity`, before on `main` and after on this branch, fresh maps with the same arguments; triangles, materials and draws from the export package's `scene_visual.glb` with `perf measure --combine-cell-m 100`; `frame_stone` from `detail_report.json`, since the trim path merges it into the atlas material):

| Map | Openings (flat) | `detail` triangles | `frame_stone` in `detail` | Export triangles | Facade trim triangles | Materials | Predicted draws | Collision |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Murcia 37.99521, −1.137511, r90 (margin 5), before | 1 028 (0) | 26 348 | 11 018 | 62 714 | 21 758 | 97 | 126 | 9 284 |
| Murcia, after | 1 028 (622) | 21 372 | 6 042 | 57 738 | 16 782 | 97 | 126 | 9 284 |
| Madrid 40.40657, −3.719187, r150, before | 3 321 (0) | 93 203 | 36 854 | 142 408 | 70 987 | 99 | 221 | 33 668 |
| Madrid, after | 3 321 (2 156) | 75 955 | 19 606 | 125 160 | 53 739 | 99 | 221 | 33 668 |

Openings are those the shells drew (balconies not counted); both runs placed the same ones. Each flat opening saves exactly 8 triangles: Murcia −4 976 (−7.9 % of the export, −18.9 % of `detail`, −45 % of `frame_stone`), Madrid −17 248 (−12.1 % of the export, −18.5 % of `detail`, −47 % of `frame_stone`). Materials, predicted draws (100 m combine), glass and collision triangles do not change. Neither map has a scenic ring (core 150 m ≥ radius). These are file counts, not a headset measurement.

## Decision

1. **A storey threshold.** `config/detail.default.json` `openings.relief_storeys` (integer, 0 to 1000, required; default 2). A `window`, `narrow_window` or `balcony_door` whose storey index (0 at the ground) is at least the threshold and whose back is `glass_dark` is drawn flat. The default keeps the ground and first floors in relief.
2. **What a flat opening is.** The wall face keeps the opening as a hole, as before (exact coordinates, no boolean). The opening is one quad in its back material, in the wall plane, with exactly the hole's four corners (`modules.flat_opening`): the two meshes share every edge of the opening, do not overlap and are not offset, so nothing z-fights and the shell stays watertight under the existing check (`facade_mesh_not_watertight`, welded at 1e-6 m, ADR-004 §4). No jambs, head, sill or back 0.15 m in.
3. **What stays recessed.** Doors, landmark doors, shop doors, shopfronts and interior doors on any storey: they are at street level and the player walks up to them. Every opening an interior pierces (`glass_view`, `shop_glass`): it is a hole through the wall, and the pane collider sits in it. Balcony slabs and railings do not change: they read in silhouette.
4. **Placement does not change.** The layout places the same openings with the same footprint check (a 0.15 m recess inside the footprint), so the threshold changes only how an opening is drawn, and a threshold above the tallest building reproduces the recessed geometry byte for byte (a unit test pins it). `0` flattens every window and balcony door, the ground floor included.
5. **The cut lives in `detail`.** Phase 04 owns opening geometry, and the work in progress that kept ADR-020 out of `detail/facades.py` is merged. A texture-step rewrite would have to find each opening again from its reveals. Only `build_shell` and one prototype change.
6. **Trim sheet and legacy path.** A flat `glass_dark` back is one connected quad, as a recessed back was, so `materials apply` fits it 0–1 into the same cell: a window of the building's family (narrower than 1.9 m and above the ground) or, under the legacy path, the `glass_dark` image. The shutter position is drawn from the quad's centre, which moves 0.15 m outwards, so a flat window may show the other shutter position of its family. No painted reveal shadow was added to the cells: the bake already draws a frame, a mullion, a shutter box and a darkened foot in every window cell, and a shadow in the cell would double the real one on the recessed windows that share it. `TRIM_BAKE_VERSION` does not change.
7. **Collision does not change.** The building collider is the phase-03 prism (ADR-004 §5), which never followed a recess.
8. **Record.** Each building with a facade template gets `facade.relief`: `{"relief_storeys", "flat_openings", "recessed_openings"}`, the openings the shell drew by how it drew them (a balcony is not an opening). `detail_report.json` gets `counts.flat_openings`. Both files go from schema 0.1.0 to 0.2.0; the fields are additive.

## Rejected alternatives

- **Decal quads in front of the wall.** A window picture on a quad a few millimetres in front of an unbroken wall needs no hole, but the offset z-fights at distance on a 16- or 24-bit depth buffer, an offset large enough not to shimmer shows from the side, and an opaque decal still costs the wall's area twice in overdraw. A cut-out window would need transparency, which the guide asks to avoid.
- **A Unity `LODGroup` with a flat LOD.** Nothing shows that the Onward SDK keeps a `LODGroup` through its export, and the adapter combines meshes by cell and material (ADR-005), which would merge the LOD levels of every building in a cell. It cannot be tested here.
- **Removing upper openings.** Saves two more triangles per window and a hole, but a blank upper facade reads as a warehouse and drops the rhythm the facade templates exist for.
- **A distance threshold instead of a storey threshold.** The camera moves; the storey is fixed and the eye height of a player is too.
- **Flattening only under the trim path.** The legacy path would keep the triangles, and the cut would have to rebuild the wall in the texture step.

## Consequences

- About 8 triangles per upper window and balcony door: 12 % of the exported triangles on Madrid r150 and 8 % on the lower Murcia r90, at no cost in draws, materials, textures or collision. Taller cities save more; a map of two-storey houses saves nothing with the default.
- The ground and first floor keep their reveals, so the street the player walks keeps its depth. Above them a window is a flat picture with a painted frame; it no longer casts a lightmap shadow on its own sill.
- A map that wants the old look sets `openings.relief_storeys` above its tallest building; a map that needs every triangle sets it to 0 or 1.
- Scenes written before this change are not regenerated; they keep their reveals and schema 0.1.0.

## To check on the headset

- At eye level on the street, does the upper facade read flat, or does the painted frame carry the window? Compare a street with first-floor (recessed) and third-floor (flat) windows side by side.
- Any shimmering, a light seam or a dark line at the edge between a flat window and the wall, near and far, and in the lightmap bake (the two meshes are separate materials and may bake at different densities).
- Balcony doors above the threshold behind their slab and railing: does the flat door under a deep slab look pasted on?
- The triangle count in Unity on the two fixed views (ADR-005), against the before scene.
