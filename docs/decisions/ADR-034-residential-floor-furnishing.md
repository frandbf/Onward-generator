# ADR-034 — Catalogue furniture on the floors of the stair interior

Status: proposed · 9 November 2026 (plan [2026-11-07-workshop-quality](../plans/2026-11-07-workshop-quality.md), increment D7). It stays proposed until a headset session has run the checks below. Turning it on by default is a separate decision after that session (plan 4.2).

Cites [ADR-031](ADR-031-shop-furnishing.md) (the furnishing planner, its budget, colliders, materials, the `furnish/` prefix and the probes kept out of the furniture) and [ADR-013](ADR-013-interior-light-anchors.md) (one light anchor per room record, probes, `lights_max` 160, shops first). It amends neither.

## Context

The generic stair interior (`docs/07-detail.md`, *Simplified interiors*) gives a building one open room per furnished floor, `<building>/L<n>`, a door from the street, a straight stair between each pair of floors, and on a flat roof a climb in two wells. Each floor record already gets one baked point light and a probe grid (ADR-013). The floors are empty. Plan row D7 asks for furniture on these floors and one light per floor, after B3 (shop furnishing, ADR-031).

## Decision

1. **Where.** A second pass of the ADR-031 planner (`detail/furnish.py`, `ShopFurnishing.of_floors`, `furnish_floors`), run in `detail_scene` for each built stair interior after it is charged. It writes the same optional `furnishing` block on each floor record, with `method: residential_furnishing`. `overgrow place` builds it exactly as it builds the shop rooms' (ADR-031 §1); nothing is added to `dress`, `materials`, `export` or the Unity adapter.
2. **Off by default.** `interiors.furnish.enabled` is `false` in `config/detail.default.json`; a configuration without the block is off. With it off nothing is planned and no catalogue is read, so every file is as before.
3. **The budget.** Its own: `interiors.furnish.max_triangles` (12 000), `max_collision_triangles` (1 200), `max_assets` (12). All or nothing per floor. It never spends `interiors.max_triangles` (24 000), `interiors.shops.max_triangles` or the shop rooms' `shops.furnish` budget, and the shop furnishing result is the same with this option on or off. A floor that does not fit keeps its interior and gets `floor_not_furnished` (info).
4. **The set.** One `generic` set of wall pieces only, at most 0.8 m deep after its turn (`MAX_WALL_DEPTH_M`): Kenney Furniture bookcases, TV cabinets, a side table with drawers and a desk, all CC0 1.0 and already catalogued. A non-empty `counter` or `decor`, another set key, or a row the shop checks refuse stops the load. This is a deliberate minimum: beds, sofas and base cabinets are deeper than 0.8 m and need a later decision (a deeper limit or a slot in the middle of the room). The transparent Kenney rows (fridges, the washer) are refused by the opaque-only loader.
5. **Placement rules.** ADR-031 §4 per floor, at the floor's own height, with the stairs added:
   - the entry is the door zone on the ground floor and the arrival landing of the flight from below on each floor above;
   - the stair wells of the level (the flight that stands on it) and of the level below (the hole it opens through) are holes in the walk region, by level, so the roof climb's reused well counts only on the levels it touches;
   - no piece in a well plus `WELL_CLEARANCE_M` (0.3 m), in the foot landing of a flight that starts on the floor, in the arrival landing of the flight that ends on it (storey and roof climb alike), in the ground-floor door zone, in a `pane_clear_m` band behind a `glass_view` window whose sill is on that storey, off the room polygon less `wall_gap_m`, or taller than the floor's ceiling less `head_clear_m`;
   - a piece's front zone may not lie over a well;
   - the room less the pieces, eroded by `walk_width_m / 2` (a 1.2 m path), keeps every place the empty floor reached joined to the entry and to every stair foot on that floor; otherwise the piece is refused (`blocks_path`), and a floor whose empty plan already fails is refused whole. A landing lies between the well and the walls, so the eroded region reaches only its open side: the entry and the feet count as reached within `walk_width_m / 2`.
6. **Light.** The ADR-013 anchor of each floor is unchanged (`light_m`, `range_m`). When a floor is furnished its probe columns keep out of that floor's pieces only (`light_anchors(..., room_furniture=)`), since the floors share one polygon; the other floors keep theirs. `cap_lights` and `lights_max` do not change, and furnished floors get no priority there.
7. **Generic, not observed.** The interior stays `representative_of_real_interior: false`. Every piece is `attribute_origin: generated` and `semantics_origin: estimated`: the furniture says nothing about the use of the real dwelling.

## Measurements

See `docs/09-perf.md`, *Residential floor furnishing*, for the bench runs (option off against the baseline, and two runs with the option on).

## Consequences

- The floors read as storage or living rooms: no bed, no sofa.
- A narrow stair interior may fail the empty-floor path check and stay bare; that fails safe and is counted (`refusals_by_reason.blocks_path`).
- More `prop_` draws and a higher view-draw p90 on maps with stair interiors. The draws are the cost against the 150-draw target, and the reason it stays off until the headset session.
- The clear path is a geometric proxy (erosion), not the navmesh, as in ADR-031.
- The facing and scale of the Kenney rows are assumptions; upper-floor furniture lit by probes only has never been seen on the headset.

## Headset checks (pending, not done)

1. Load a `bench_murcia` or `bench_krakow` package built with the option on: the furniture on every furnished floor is lit by that floor's baked light and probes, not black (`prop_` materials take no lightmap, `docs/12-export.md`).
2. Walk in through the door and climb every stair: no piece blocks the door, a stair foot, a stair arrival or the roof climb, the 1.2 m path holds on each floor, and the player is never trapped.
3. Cover-sized furniture stops bullets and the player, and nothing is an invisible wall.
4. Through the `glass_view` windows from the street, the furniture reads at scale and faces into the room.
5. OVR Metrics in fixed views A and B with the option on and off: draws against the 150 target, a median of at least 72 fps, graphics memory.
6. Unity 2021.3.23f1 (A3): the bake accepts the per-floor point lights and the LightProbeGroup; record `seconds.bake` draft and final.

Then this ADR goes from proposed to accepted, or is corrected.

## Rejected alternatives

- **Charging the furniture to `interiors.max_triangles`.** It would turn down stair interiors to pay for furniture.
- **One probe union for all floors (the shop path).** The floors share one polygon, so a piece on one floor would take probe columns off every other floor.
- **Beds and sofas now.** They are deeper than the 0.8 m wall limit; a middle-of-the-room slot needs its own path rules.
