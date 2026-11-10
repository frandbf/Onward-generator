# ADR-031 — Catalogue furniture in the shop rooms

Status: proposed · 7 November 2026 (plan [2026-11-07-workshop-quality](../plans/2026-11-07-workshop-quality.md), increment B3). It stays proposed until a headset session has run the checks below. Turning furnishing on by default is a separate decision after that session (plan 4.2).

Cites [ADR-019](ADR-019-shop-rooms.md) (shop rooms), [ADR-013](ADR-013-interior-light-anchors.md) (light anchors and probes), [ADR-005](ADR-005-quest-performance.md) (Quest budget) and [ADR-017](ADR-017-playable-core.md) (shops only for buildings whole in the core). It amends none of them.

## Context

`detail` builds a generic room behind every shopfront (ADR-019): walls, floor, ceiling, one light and a probe grid, and see-through `shop_glass` display windows. The rooms are empty. `assets/` already holds 377 catalogue rows with `placement: interior` (Kenney Mini Shop, Furniture Kit and Food Kit, seven Poly Haven shop models), all CC0 1.0 in [data-sources.md](../legal/data-sources.md), and nothing placed them (`overgrow/manifest.py`). Street overgrow refuses them on purpose (`overgrow/library.py`), and `overgrow fit` reads anything inside a footprint as `inside_building`.

On the bench run `output/bench/runs/20261107T163844Z` the shop budget `interiors.shops.max_triangles` (6 000) is nearly full on Madrid (5 992, 54 shops turned down by it) and Murcia uses 2 304.

## Decision

1. **Where.** A planner in `detail/furnish.py`, called from the shop-interior branch of `detail_scene` after the room is built and charged. It writes an optional `furnishing` block on each shop room record of `scene_detail.json`. `overgrow place` builds the meshes (`overgrow/furnish.py`): one `furnish/<building>/<n>` visual node per piece and a `furnish/collider/<building>/<n>` box for each solid one. Not in overgrow's street placement: its fit check and its loader refuse interior rows, and they keep doing so. The `furnish/` prefix keeps the pieces out of `overgrow fit`'s street checks. `dress`, `materials`, `bounds`, `export` and the Unity adapter are not changed.
2. **The budget.** A separate one: `interiors.shops.furnish.max_triangles` (12 000) and `max_collision_triangles` (1 200), in `FurnishBudget`, all or nothing per room. `shops.max_triangles` is never spent by furniture and keeps its meaning. The plan says "respeta `shops.max_triangles`"; read literally, charging furniture to it would remove shop rooms on Madrid, where it is already full. This reading is a deviation the maintainer must accept. A room that does not fit keeps its shop interior, gets an info issue `shop_not_furnished` (`triangle_budget: needs N triangles, M left of shops.furnish.max_triangles L; room <id>`), and the building is never split. `max_assets` (16) caps the distinct rows per map (`asset_cap`).
3. **The sets.** Per `ShopCategory` (`bakery`, `pharmacy`, `bar`, `generic`; the category of the room, from OSM `shop=*`/`amenity=*`, the Catastro use or an Overture place through `shop_category`), three slots: one `counter`, `wall` pieces, and `decor` on the counter top (Food Kit only). Defaults favour cheap Kenney Furniture rows and Poly Haven `Shelf_01`. Refused at load: rows outside the four allow-listed catalogues, rows that are not `placement: interior`, the floor, wall, fence and column tiles, the character, the mannequin, the basket, the cart and the hand truck, a counter deeper than 1.0 m and a wall piece deeper than 0.8 m after its turn. Most Mini Shop rows are 1.2 to 2.1 m deep (their scale was never reviewed from a rendered view) and fail that test. The transparent Kenney rows (fridges, stoves, the glass tables) are refused by the opaque-only loader.
4. **Placement rules.** Inside the room polygon less `wall_gap_m` (0.03 m), so off the walls, partitions (`PARTITION_M`) and courtyards. Never in the walkable door's zone (`door_clearance_m`, 1.5 m deep, 0.3 m wider than the door each side) or in a `pane_clear_m` (0.15 m) band behind a see-through opening. The counter goes back to the wall of the display windows, else beside it. Every floor piece keeps a front zone `walk_width_m` (1.2 m, schema minimum) deep across its length, inside the room and free of other pieces, and the room less the pieces, eroded by 0.6 m, must keep every place the empty room reached joined to the door. Deterministic: `stable_unit(seed, building, room, slot, ring, edge, k)` picks the first row to try in a set sorted by id. Rooms without a door stay bare (`unreachable`).
5. **Colliders.** A piece gets one box collider (12 triangles), in the collision GLB only, when it meets the overgrow cover rule (`is_solid`: at least 0.5 m tall and 0.4 m on its short side). Decor never does.
6. **Materials and draws.** One `prop_<id>` material per row, plus `prop_<id>__<n>` per further albedo, albedo only on `PP_Opaque_Simple_Lit` like overgrow's props, and one mesh per row shared by its copies (`mesh_key prop:<id>`). `prop_` materials take no lightmap texels and are probe-lit (`docs/12-export.md`); when the adapter combines a cell, the combined renderer takes the scene's ambient light instead of the room probes. The probe columns of a furnished room keep out of the furniture's plan.
7. **Off by default.** `interiors.shops.furnish.enabled` is `false` in `config/detail.default.json`; an absent block is off. With it off the planner is not called, no catalogue is read and overgrow finds no block, so every file is as before. No `run` or `map.json` flag: a map opts in through `detail --config`.

## Measurements

Bench, option off: `task bench` gave runs `20261107T170959Z` and, with the final code, `20261107T184555Z`; `task bench -- check` of each against `20261107T163844Z` says the reports are the same apart from timings, on all four maps.

Option on: copies of `bench_murcia`, `bench_madrid` and `bench_krakow` from that run, every local stage again with `detail --config` and `furnish.enabled: true`, through `export`. Docker on an Apple M4 Pro. Python and package figures, not a headset measurement.

| Map | Shop interiors | Furnished / reachable rooms | Pieces | Furniture triangles (cap 12 000) | Colliders | Materials added | Refusals |
| --- | --- | --- | --- | --- | --- | --- | --- |
| bench_murcia | 24 (unchanged) | 7 / 27 | 56 | 11 454 | 41 | 19 | 20 `triangle_budget`, 3 `unreachable`; pieces: 6 `blocks_path` |
| bench_madrid | 67 (unchanged) | 7 / 70 | 53 | 11 822 | 43 | 19 | 63 `triangle_budget`; pieces: 3 `blocks_path` |
| bench_krakow | 4 (unchanged) | 4 / 4 | 32 | 5 680 | 25 | 18 | pieces: 3 `blocks_path`, 1 `no_counter_spot` |

| Map | Visual triangles off → on | Draws off → on | View draws p90 off → on | Lightmap pages | Layout routes |
| --- | --- | --- | --- | --- | --- |
| bench_murcia | 150 339 → 161 793 | 213 → 247 | 181 → 211 | 4 → 4 | 32 |
| bench_madrid | 398 025 → 409 847 | 504 → 547 | 374 → 412 | 11 → 11 | 32 |
| bench_krakow | 72 884 → 78 564 | 272 → 293 | 219 → 238 | 2 → 2 | 32 |

`shops.max_triangles` use is unchanged (2 304, 5 992, 226), `fit` and `bounds` pass, and the street navmesh inputs do not change: the furniture lies inside room polygons that the navmesh already cuts (`docs/11-onward-sdk.md`).

## Consequences

- At about 1 600 triangles a room, the 12 000 cap furnishes 7 rooms on Murcia and Madrid, nearest the map centre first (the shop order). Raising it costs core triangles on maps that are already over ADR-005's 250 000 (Madrid's core is 270 361 off, 282 183 on).
- The draws rise by 21 to 43 per map: each row's material is one draw in every combine cell it appears in. That is the main cost against the 150-draw target, and the reason it stays off until the headset session.
- The budget core (ADR-017 amendment, B1) does not count furniture: like overgrow's props, it is added after `detail` solves the core.
- A package built without the overgrow stage has the plan but no furniture. The GLB found at overgrow time must be the one `detail` priced: a different SHA-256 or triangle count stops `overgrow place`.
- The clear-path test is a geometric proxy (erosion), not the navmesh. Interiors are cut out of the bots' navmesh, so walking inside is a headset check.
- Window-only rooms stay bare, so some display windows show empty rooms.
- The facing (the model's local −Y, glTF +Z, into the room) and the scale of the Kenney rows are assumptions.

## Headset checks (pending, not done)

1. Load a `bench_murcia` package built with furnishing on: the furniture is lit by the room light and probes (or the ambient light of a combined cell), not black.
2. Walk in through each kind of shop door: the 1.2 m path is clear and no piece blocks the door or traps the player.
3. Cover-sized furniture stops bullets and the player; decor does not; nothing is an invisible wall.
4. OVR Metrics in fixed views A and B looking into a furnished shop row: draws against the 150 target, a median of at least 72 fps, graphics memory.
5. Through the `shop_glass` from the street, the furniture reads at scale and faces into the room.

Then this ADR goes from proposed to accepted, or is corrected.

## Amendment (10 November 2026): on by default, Quaternius rows

- **On by default.** The maintainer chose to turn shop furnishing on by default (`interiors.shops.furnish.enabled: true`) before the headset session, to see furnished shops on every new map. The status stays proposed: the headset checks below still decide whether it is accepted or corrected. `enabled: false` in a detail configuration restores the earlier output byte for byte.
- **Quaternius rows.** `FURNISH_CATALOGUES` adds `quaternius_home_interior`, `quaternius_ultimate_furniture` and `quaternius_furniture` (CC0 1.0, `tools/import_quaternius.py`, `docs/legal/data-sources.md`). The default sets swap some Kenney pieces for Quaternius ones (shelves, kitchen drawers, fridge, cabinets) instead of adding them: the distinct rows per map stay about as many as before, so the `max_assets` cap and the draws do not grow, and the lighter rows (68–600 triangles against about 1 000 for Kenney) let more rooms fit `max_triangles`. On Madrid r200 the cap, not the asset cap, refused 32 of 42 shop rooms before this change.
- **A short wall piece in `generic`.** `qt_home_Drawer_1` (0.66 m) keeps a low-ceiling shop furnishable: the 2.17 m shelves do not fit under a 2.0 m ceiling less the head clearance.
- **Four pieces per shop, nearest the core first.** `max_pieces_per_room` drops from 8 to 4 (a counter and two or three wall pieces), so the same 12 000 triangles reach about twice the rooms. `detail` no longer furnishes in build order: each built interior waits until the building loop ends, then the budget goes to the interiors nearest the map centre first (`_furnish_by_priority`). The map centre is the centre of the playable core, where `perf layout` later puts the spawns, the routes and the Uplink; the routes do not exist when `detail` runs. On Madrid r200 the furnished shop rooms go from 9 to 17 of 42 with 11 520 triangles; the next limit is `max_assets` (16 refusals as `asset_cap`). The floors of the stair interior follow the same order.
- **Furniture lit by its rooms.** Furniture was combined with the street props per 100 m cell and, like them, took the scene's ambient light (`LightProbeUsage.Off`): in a dark room it read pale blue. The adapter now combines `furnish/<building>/<n>` nodes per building and storey, so each combined mesh sits inside its rooms, and lights them with `LightProbeUsage.BlendProbes` from the room probes of ADR-013. `perf.measure.combine_groups` predicts the draws the same way. It adds draws (one per building, storey and material), mostly inside buildings, where the frustum culls them from the street.
- **More variety once models share one atlas.** With the prop atlas (ADR-005 amendment of 10 November 2026) a distinct model no longer adds a draw. The shop `max_triangles` rises from 12 000 to 16 000 and `max_assets` from 16 to 40; the floors' `max_assets` from 12 to 30, with 28 wall pieces in their set; local Asset Store rows join four per slot. On Madrid r200: 23 of 42 shop rooms (from 19) with 26 distinct models, and 8 of 9 floors, at 217 702 triangles in Unity.
- **Facing.** The Quaternius OBJ files face +Z, the glTF front the planner assumes. The Furniture pack (`quaternius_furniture`) has its long side on Y; it is in the catalogue list but in no default set.

## Rejected alternatives

- **Charging furniture to `shops.max_triangles`.** Madrid would lose shop rooms (5 992 of 6 000 used, 54 shops already turned down).
- **Placing furniture in overgrow's street placement.** Its fit check flags anything in a footprint as `inside_building`, and its loader must keep refusing interior rows so no shelf lands on a street.
- **Drawing the furniture in `detail`'s visual GLB.** `detail` writes no library mesh today; overgrow already loads, reduces and textures library GLBs, and its report records them.
- **The Mini Shop rows at their file size.** 1.2 to 2.1 m deep shelves would fill a 4 m room; they are refused by the depth limits until their scale is reviewed.
