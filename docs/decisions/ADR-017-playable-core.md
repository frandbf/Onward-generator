# ADR-017 — A playable core with full detail and a scenic ring outside it

Status: accepted · 30 October 2026. Plan items P16, P17 and P18 (`docs/plans/2026-10-30-scale-and-speed.md`), first increment.

## Context

The Quest triangle budget is now what limits the size of a map, not the geometry or the time it takes to build:

- The guide gives about 250 000 triangles and about 150 draw calls per view, players, objectives and SDK prefabs included (ADR-005). The Murcia measurement already sat at 247 627 triangles in Unity.
- With Catastro, Madrid at a 300 m radius reaches export with 510 861 visual triangles from `detail`, twice the guide. At 200 m it has 217 845, before props, signs and SDK prefabs.
- Every building gets the same treatment wherever it stands: openings, balconies, shopfronts, interiors, signs, a collider. A building 250 m from the centre costs as much as the one the player stands beside, and nobody can reach it.
- Fixed limits (16 generated benches, 10 bins, 6 manhole covers, the overgrow caps, outdoor light probes on the navmesh) are spread over the whole site, so their density falls about 27 times from r100 to r500.
- Navigation spacing is derived from the walkable area and a 320-node heuristic (`docs/11-onward-sdk.md`): `sqrt(area / 320)`. Above r≈150 it is over 15 m, 34.7 m at r300.

ADR-014 hides the edge of the area with fog and an optional backdrop quad ring, and names the next step: real building silhouettes, without openings, no collision, no lightmap, few renderers.

## Decision

1. **A playable core.** `map.json` gets `core_radius_m`: the half-side of a square around the map centre (the same convention as `radius_m`), in local metres. `map create --core-radius-m` and `run --core-radius-m` set it, default 150 m (since 5 November 2026 `min(150, radius − 50)`; amendment below). `0`, a value at or above `radius_m`, or a map written before the key existed (absent) means no core: the whole map is played and every stage behaves as before. Otherwise it must be at least 50 m.
2. **One tier per building.** `detail --core-radius-m` (the `next:` line and `run` read it from `map.json`) tiers each building: `core` when its footprint meets the open core square, `scenic` otherwise. A `building_part` and a landmark tower take the tier of their building. The tier is stored on every building record (`tier`), and `scene_detail.json` gets `core: {half_side_m, buildings: {core, scenic}}` (`null` without a core). Later stages read the core from that block, never from `map.json`.
3. **Inside the core, nothing changes.** Today's pipeline: facades, openings, doors, interiors, shops, signs, urban elements, overgrow, collision, navigation.
4. **The scenic tier is a plain shell.** A scenic building is its OSM (or Catastro) footprint extruded to its height (ADR-016 heights): walls and a flat roof cap, no base. No openings, doors, balconies, parapets, hip roofs, towers, interiors or shops (so `signs` draws no shop sign or vinyl for it). No collider. Walls use one of three shared materials, `scenic_wall_light`, `scenic_wall_warm` and `scenic_wall_ochre`, picked by `sha256(seed, building, "scenic")`; every roof is `roof_flat_grey`. `signs` puts no street plate outside the core either: plates are for the player, and when this was decided each plate was its own material, so a plate in the ring was a draw of its own (since 3 November 2026 the plates share atlas pages). `signs` reads the scene `detail` built from (the Catastro split where it ran, since 4 November 2026; before, the OSM scene), and it keeps a wall by the position of its midpoint, not by building id. `materials apply` gives the three scenic walls a plaster photograph in greyscale (the `SHARED_FACADE_SLOTS` rule), so the tint stays on `_BaseColor`. Windows painted into the texture were left out: it needs a second image per tone and a UV rule per storey, and the ring is seen from at least the width of a street, behind fog.
5. **No props or collision outside the core.** `detail` drops urban elements whose position is outside the core and counts them (`counts.tiers.scenic.urban_elements_dropped`). `dress style` places its generated benches and bins on free ground inside the core only. `overgrow place` clips its zone to the core. The ground colliders (`collider/walkable`, `collider/carriageway`, `collider/walkable_crossing`) are rebuilt as closed slabs over the part of their polygon inside the core. The visual ground, curbs and markings stay over the whole site: they are seen, not walked.
6. **The wall moves to the core edge.** `bounds` puts the 12 m perimeter collider on the core square (intersected with the site) when the manifest has a core. Players cannot reach the ring.
7. **Navigation only in the core (P18).** The adapter bakes the navmesh from the walkable colliders, so with item 5 the baked area is the core. `perf layout --playable` intersects its free ground and navmesh islands with the core inside the wall, so spawns, Uplink, Hunt routes, ammo, Escort, Evac and hard drives stay in the core. An ammo crate (`AmmoBox.prefab`) stays 3 m (`AMMO_SPAWN_CLEAR_M`) from every point of the four spawn blocks: the first crate went to the pole of the free ground, where the attackers' block already stood, and on 20 of 21 maps a crate was 0.5 to 0.8 m from a spawn point (madridescaleras2 0.71 m, now 30.67 m). The Escort, Evac and hard-drive points in turn stay 3 m (`AMMO_OBJECTIVE_CLEAR_M`) from every crate: they were scattered from the same pole, so on Barcelona Sarria (5 November 2026) both Escort points stood exactly on a crate and a hard drive 0.75 m from one. `layout.json` gets `navigation: {walkable_area_m2, span_m, node_spacing_estimate_m}` from the adapter's rule `max(1, span / 400, sqrt(area / 320))`. It is an estimate; the adapter measures the baked mesh.
8. **Limits apply to the core (P17).** The fixed limits stay absolute and are not scaled with area. They are spent inside the core, so a map with a 150 m core has the prop and probe density of a 150 m map whatever its radius. The outdoor light probes follow the navmesh bounds in the adapter, so they follow the core with no C# change.
9. **Lightmap.** `export.lightmap` gives the class `scenic` (materials starting `scenic_`) a scale of 0.25, the roof scale. The scenic roof is a roof material (0.25). Not baked at all (probe lit) was rejected for this increment: it needs `ContributesGi` in the adapter to change, and that C# cannot be compiled here.
10. **Fog starts at the wall.** `map create` writes `fog_start_m` = `core_radius_m` when there is a core (radius + margin otherwise), so the ring fades into the fog.
11. **Reports.** `detail_report.json` gets `counts.tiers`: per tier the buildings, visual and collision triangles. `onward_export.json` gets `tiers`: per tier `core`, `scenic` and `shared` the visual triangles and the predicted draws after the adapter's (cell, material) combine, plus the total predicted draws. A node goes to the tier of its building record; a node with no tier (ground, curbs, markings, props, the backdrop) goes to `core` when its bounds lie inside the core, `scenic` when they lie outside, and `shared` when they cross the edge. `run` prints the tier triangles on the `detail` line.

## Draw calls

The adapter combines visual meshes by (100 m cell, material) (ADR-005). A ring cell holds at most three scenic walls and one roof material, against the ten or so facade materials, glass, frames, doors, signs and shop materials a core cell holds. So a ring cell costs about 4 draws, plus the ground pieces whose centre falls in it. With the core at 150 m, an r300 map has about 35 ring cells; frustum culling leaves part of them in view. Measured on Madrid r300 (`docs/09-perf.md#playable-core`): the ring adds 140 uncullled draws and the core about 260, against 827 for the whole map without a core. These are predictions from the GLB, not a headset measurement.

## Relation to ADR-014

The scenic ring is the step ADR-014 left open, done inside the area already extracted instead of in a band outside it: the buildings are the OSM footprints of the map, with no extra download and no new licence. The fog now starts at the core wall. The backdrop quad ring stays optional at the site edge, beyond the scenic ring, to close the horizon behind the last buildings.

## Rejected alternatives

- **A triangle-budget mode** (pick the core from a triangle target). The cost of a core is only known after `detail` runs, so it needs a search over several `detail` runs or a model of the cost per building. Left as the next step; the fixed radius is measurable now.
- **A disc core.** The map, the Catastro tiles and the adapter cells are squares, and a square core keeps the wall to four straight sides.
- **Keeping each scenic building's own wall material.** About ten wall materials per ring cell, each its own draw, and the scenic lightmap scale cannot be told apart from the core one when a ring building and a core building share a material in a cell.
- **Tier-aware combining or lighting in the adapter.** Needs C# that cannot be compiled or run here.

## Consequences

- Inside the core, buildings get today's treatment. The interior quota and the triangle budgets are now spent on core buildings only, so the core can get interiors the ring used to take. A map without a core does not change, apart from `core: null` in `scene_detail.json`, `tiers: null` in `onward_export.json` and the `navigation` estimate in `layout.json`.
- The ring has no colliders and no navmesh: a bot or a player cannot leave the core, and the wall is the edge.
- A landmark in the ring is a plain box (`needs_custom_model` stays on its record).
- Must be checked on the headset: draws in the two fixed views (A and B), the frame time on the route, how the ring reads through the fog at the wall, the bake time and pages, and the light on the ring buildings at scale 0.25.

## Amendment, 5 November 2026: a default core keeps a 50 m scenic ring

The default core was 150 m whatever the radius, so a 150 m map had no core and the whole map was played. Barcelona Sarria (r 150) then reached 380 811 triangles in Unity, 12 lightmap pages and 135 / 318 / 376 draws a view (median, p90, max), against about 250 000 triangles and 150 draws in the guide. Without `--core-radius-m` the core is now `min(150, radius − 50)` (`default_core`, `CORE_RING_DEFAULT_M` 50); under the 50 m minimum, so under a 100 m radius, the whole map is played. A map of 200 m or more keeps its 150 m core. The same Barcelona map with a 100 m core: 234 166 triangles, 5 lightmap pages, 90 / 184 / 216 draws a view, and no lift, since no building of 3 to 6 storeys in that core had room for a cabin. An explicit `--core-radius-m`, 0 included, is kept as before, and a `map.json` already written keeps its value.
