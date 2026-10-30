# Architectural and street detail (phase 04)

Module `onward_generator.detail`. Design decisions: `docs/decisions/ADR-004-facade-detail.md`. The tower volume is ADR-008.

```bash
.venv/bin/python -m onward_generator detail \
  --scene   maps/<id>/data/processed/geo/geo_scene.json \
  --heights maps/<id>/data/processed/heights/heights.json \
  --base    maps/<id>/data/processed/scene3d \
  --out     maps/<id>/data/processed/scene_detail \
  [--config config/detail.default.json] [--generation-config config/generation.default.json] \
  [--no-preview] [--overwrite]
```

Returns `0` when the automatic checks pass (no critical error) and `1` otherwise. The human visual review stays `pending` in the report. Passing the automatic checks does not close that review.

## Fidelity

Facades, roofs, and interiors are **generic templates** chosen by rules. They do not represent the real facade or interior of any building. Each building says so in the manifest: `representation: generic_template`, `representative_of_real_facade: false`, `attribute_origin: generated`.

- Landmarks (`landmark` in `heights.json`) get `landmark_placeholder`: a plain wall, one door, and `needs_custom_model: true`. No ornament is invented.
- **Tower.** If a landmark contains an OSM tower point (`man_made=tower`, `bell_tower`, and similar) with an observable `height` tag, a prism clipped to the building footprint is added. The height is **observed**. The tower square is **estimated** and never leaves the footprint. With no `height`, no volume is invented.
- Interiors use `representation: generic_simplified_interior` and `representative_of_real_interior: false`.
- The only observed inputs are what earlier stages already had: the OSM footprint, height and storeys (Catastro or OSM), the building category, and OSM POIs. A commercial ground floor is applied only with evidence in `ground_floor_evidence`: an OSM POI (`shop`, `amenity`, `office`, or `craft`) within 2 m of the footprint, or a building category that is already commercial.

## Inputs

`--base` is the validated phase-03 directory:

- `generation_report.json` must have `phase_03_exit.passed`;
- the recorded SHA-256 of `geo_scene.json`, `heights.json`, and the generation configuration must match;
- phase 03 is rebuilt in memory and the manifest must match the file on disk.

A mismatch raises `DetailError` and writes nothing.

`config/detail.default.json` is validated with `config/detail.schema.json`. Every value is a modelling assumption, not an observation. Examples: every window or balcony fits its bay with a minimum pier; each category has one template; every material exists.

## What is built

### Facades

1. **Exposure.** A 1 m probe outside each edge is intersected with neighbouring footprints. An edge that touches a neighbour is a blind party wall. An exposed run shorter than 2.5 m is also blind. Cuts are inserted as collinear vertices, so the area does not change (`refined_footprint_area_changed` is critical). Interior courtyards get no openings.
2. **Storeys.** If `heights.json` has `estimated_levels`, the storey height is h/n. Otherwise 3 m is used, clamped between 2.6 m and 6 m. The origin is stored in `storeys.origin`. A building that receives an interior and whose storey is below 2.8 m is raised to 2.8 m per storey when the height is an estimate. An OSM `height`, a Catastro height, or Catastro levels stay as recorded. The previous height is kept on the building as `previous_height_m`, with `interior_storey_origin: estimated`. `heights.json` is not rewritten.
3. **Bays.** Each exposed run is split into bays of `bay_m`. The ground floor is `door_and_windows`, `shopfront` when there is evidence, or `main_door_only` on landmarks. The main door sits in the centre bay of the longest exposed run. Upper floors are windows or balcony stacks. The choice is `sha256(seed, building, run, column) < balcony.share`. An opening is placed only when its recess stays inside the footprint. A balcony is placed only when it does not enter another footprint.
   **A door on every building.** When the template places no door (the storey is too low for the 2.4 m door under its 0.35 m lintel, or no wall reaches the 2.5 m street-facing minimum), a fallback door is cut. The walls are tried in this order: the street-facing walls, longest first (the first is the main frontage of a one-part building), then outer walls too short to count as street-facing whose 1 m (`probe_m`) strip outside meets no other footprint. A party wall is never used. The door sits in the centre bay of that wall. It keeps the template size when it fits and shrinks to the wall (a 0.5 m pier each side) and to the storey (minus the lintel), but not below 0.9 × 2.0 m. It replaces the ground-floor openings within 0.5 m of it. The building record says how the door came: `facade.door` is `template`, `fallback`, or `none`. A building with a shopfront keeps the shopfront door rules and is never given a fallback door. Measured on the six-building test row (`ROW_BUILDINGS` in `tests/detail_fixture.py`): 3 of 6 buildings had a door before, 5 of 6 after; the sixth is the shop.
4. **Geometry.** Openings are exact holes in the wall face, plus notches for doors. GEOS boolean is not used. Each opening has a 0.15 m recess: jambs in `frame_stone` and a back of `glass_dark` or `door_wood`. On a building with an interior, a window below the accessible ceiling uses the full wall thickness and a single `glass_view` quad. The wall and its recesses are a closed solid. The only open edges are the base (z = 0) and the through-door of an interior.
5. **Roofs.** An OSM `roof:shape` of `hipped`, `gabled`, `pyramidal`, `skillion`, `half-hipped`, `gambrel`, or `round`, on a footprint of at most 800 m², becomes a `hip`. `roof:material` chooses tile or a flat roof. That origin is `observed`. With no tag, a convex footprint of at most 160 m² that the template gives a parapet may receive an estimated `hip` (`origin: estimated`). Otherwise the template stands: `flat_parapet` (slab, parapet, coping), `hip`, or `flat`. The projected roof area is checked against the footprint (`roof_area_mismatch`).
6. **Materials.** OSM `building:material` or `material` wins (`wall_origin: observed`). Otherwise each building draws its wall from the palette of its **facade use** (`facade_palettes` in the configuration), and the record says `wall_origin: generated`. A landmark keeps its template wall (`stone_warm`).
   - **Use.** Read from the building's own tags first, then from a POI inside the footprint, then from an area that holds the footprint's representative point (a school ground), then from a POI outside every footprint but within `shop_evidence_distance_m` (2 m) of the nearest. Street furniture POIs are not evidence. When several tags name a use, the most specific wins: `historic`, `institutional`, `office`, `retail`, `industrial`, `residential`. A building with no evidence is `residential`, `origin: estimated`. A POI near the footprint is also `estimated`; the building's tags, a POI inside, and an enclosing area are `observed`. The record keeps it as `facade.use`: `{"use", "origin", "evidence": [{"source_id", "tag", "match"}]}`, with `match` one of `building`, `poi_inside`, `enclosing_area`, `poi_near`, or `landmark`.

     | Use | OSM tags (`building` and `building:use` read alike) | Palette |
     | --- | --- | --- |
     | `institutional` | `building` school, kindergarten, university, college, public, civic, government, townhall, hospital, fire_station, education; `amenity` school, kindergarten, college, university, library, townhall, courthouse, police, hospital, clinic, fire_station, community_centre; `office=government` | brick, white plaster, cream plaster |
     | `office` | `building` office, commercial, bank; `amenity` bank, bureau_de_change; any other `office`; `tourism=hotel` | warm stone, white plaster, cream plaster |
     | `retail` | `building` retail, supermarket, kiosk; any `shop`; `amenity` restaurant, cafe, bar, pub, fast_food, pharmacy, ice_cream | ochre, cream, salmon, white plaster (above the shopfront) |
     | `historic` | `historic` building, castle, palace, manor, monastery; `tourism` museum, gallery | cream plaster, warm stone |
     | `industrial` | `building` industrial, warehouse, manufacture | white plaster, brick |
     | `residential` | `building` apartments, residential, house, detached, semidetached_house, terrace, dormitory, bungalow; no evidence | ochre, cream, salmon, white plaster, brick |
   - **Choice.** Buildings are taken in id order. Each ranks its palette by `sha256(seed, building, "wall", material)` and takes the first material that no footprint within `probe_m` already has. When every material is taken, it keeps its first. So two buildings that touch get different walls whenever the palette has a second material. The palette is a fixed set of shared materials, not one per building, so the draw calls stay bounded (ADR-005). `counts.facade_uses` and `counts.wall_materials` in `detail_report.json` count them.
   - Before this rule a residential building drew from its template's two plasters (ochre, cream), with stone and brick as rare estimates, each building on its own. Touching buildings matched half the time, so a street block often read as one texture: on the test block the three touching buildings 311, 312, and 313 were all ochre. They are now white, cream, and salmon.
7. **Parts.** A polygon with `building:part` and `height` is extruded, clipped to the building it overlaps most, with its own collision. With no `height`, no volume is invented.
8. **Wall texture frame.** Each building node in `scene_visual.glb` carries `extras.uv_frame` ([ADR-012](decisions/ADR-012-wall-uvs.md)): the exposed runs (chains of street-facing segments, broken by party walls, as `[ax, ay, bx, by]` in ring order), `storey_h`, and `storeys_origin` (a copy of `storeys.origin`). Without `estimated_levels`, or on a landmark, `storey_h` and `storeys_origin` are `null`. No coordinates are written here, and `scene_detail.json` does not change. `dress style` and `materials apply` turn the frame into UVs (`docs/08-dress.md`).
10. **Interior light anchors.** Each `interior/<building>` node in `scene_visual.glb` carries `extras.light_anchors` ([ADR-013](decisions/ADR-013-interior-light-anchors.md)), `attribute_origin: generated`. One entry per furnished room: `room_id`, `light_m` (the room centroid, or a point clear of the stair wells, 0.3 m under the ceiling, in local metres), `range_m` (half the room diagonal plus 1 m, 4–12 m), and `probes_m` (flat x, y, z triples: a grid of at most 4 × 4 columns, 0.5 m off the walls and out of the wells, at 1 m above the floor and 0.5 m under the ceiling). `scene_detail.json` does not change. The adapter turns the anchors into baked lights and light probes (`docs/12-export.md`).
9. **Generated furniture.** Besides lamps, benches and bins are placed along pedestrian ways at a fixed spacing, from the seed, with a cap per type. An OSM point (`amenity=bench`, `amenity=waste_basket`, `natural=tree`, and similar) still places the observed object.

### Templates

| Template | Categories | Ground floor | Roof |
| --- | --- | --- | --- |
| `generic_residential` | `residential_multi`, `residential_small`, `unknown` | door and windows; balconies on 35% of upper bays | `flat_parapet` (1 m parapet) |
| `generic_commercial` | `commercial` | `shopfront` | `flat_parapet` (1 m parapet) |
| `generic_public` | `public`, `industrial` | door and windows; no balconies | `flat_parapet` (1.2 m parapet) |
| `generic_minor` | `minor` | door and windows | `hip` in `roof_tile` when convex and ≤ 200 m²; otherwise `flat` |
| `landmark_placeholder` | `religious` and any `landmark` | door only (2.4 × 4 m) | `flat` |

### Simplified interiors

Generated only in **selected buildings**. The count is `sha256(seed, interiors, quota)` from `min_buildings` (5) through `max_buildings` (8). A map with fewer matches builds only those:

- `include_ids` first, except `exclude_ids`;
- then buildings that match the rule: `residential_multi` or `unknown`, at least 3 storeys, 80–500 m², rectangularity ≥ 0.8, not a landmark, no courtyards. Ordered by `sha256(seed:interior:id)`.

Buildings are tried in that order until the count is reached. A building whose plan fails (`no_room_for_stair`, `no_door_position`) does not use up the count. The objects and issues are still written in building order.

**Triangle budget.** `max_triangles` (default 8 000) caps the visual triangles of all `interior/<building>` nodes together: walls, floors, slab soffits and stairs. An interior that would take the total over it is not built. The building keeps the facade it has with interiors off, and `interior_not_generated` (info) records `triangle_budget` with the cost and what was left. Later buildings in the queue are still tried, so a smaller interior can fit after a larger one was turned down. The total is `counts.interior_visual_triangles` in `detail_report.json`. Colliders are not in this budget; an interior adds about 1.3 k collision triangles (Madrid sample below).

Measured on central Madrid (40.4168, −3.7038, 150 m half-side, 172 buildings, 39 match the rule) and on the test block:

| Scene | Interiors | Interior triangles (each) | Scene visual triangles |
| --- | --- | --- | --- |
| Madrid, interiors off | 0 | 0 | 162 848 |
| Madrid, previous defaults (2–5, id order) | 2 | 472 (236 each) | 163 344 |
| Madrid, these defaults (seed 48152 draws 5) | 5 | 2 640 (236–808, 11 rooms) | 165 524 |
| Madrid, every match, 8 000 budget | 15 | 7 830 | 170 766 |
| Madrid, every match, no budget | 39 | 21 118 (230–1 162, median 484) | 184 094 |
| Test block, 3 matches | 3 | 2 242 (612–874) | 5 595 |

Stairs are most of an interior: a flight is a closed stepped solid of about 130 triangles. Eight interiors of 2–3 floors give 16–24 furnished rooms, within the adapter's 24 baked lights (ADR-013).

Each interior has a through-door on the main facade, a straight stair (tread 0.28 m, riser ≤ 0.185 m) between accessible floors, and partitions with a passage. The manifest describes rooms and connections (`door`, `stair`). If there is no room, `interior_not_generated` (info) records the reason, for example `no_room_for_stair`.

`floors` is the furnished count (default 3, and never more than the storeys minus one). A building that can hold three floors gets three when `sha256(seed, id, interior_floors) < 0.5`, and two otherwise. Further flights, a switchback in one well, continue from the top furnished floor to the roof deck. The deck is the flat roof with that well cut out, and the parapet is a collider. A hip roof is left closed. If the extra flight does not fit, the interior keeps its furnished floors and the volume under the roof stays solid. Walls, floors, slab soffits, and stairs use `interior_wall`, `interior_floor`, and `stair`, which `materials apply` paints from the texture plan. The roof deck keeps the roof photograph.

Windows, shopfronts, and balcony doors below the accessible ceiling keep a hole through the exterior wall and the inner wall, and one glass quad in that hole. The quad is `glass_view`: a single piece, no flipped copy, and no renderer collider. The same opening is cut out of the concrete wall collider, through the full wall thickness, and one closed pane is written on a separate collision node `<id>#glass`. The pane is 2 cm thick, centred in the wall, and inset 1 cm from the jambs. The adapter assigns `PP_Sprite_Lit` and the window PNG on `_MainTex`. That shader is already transparent and draws both faces, so the pane is visible from the street and from the room. The dark fill of the window image is about 0.35 alpha; the frame and mullions stay opaque. The image is RGBA and is not a tile of the opaque atlas. `PP_Opaque_Simple_Lit` still covers windows above the accessible ceiling, buildings without an interior, and `glass_boarded`. Those stay solid concrete: they are not openings. The stone frame, the sill, the header, and the pier stay concrete. A window that does not land on the inner wall is omitted (`interior_window_omitted`, info). The counts are `through_windows` and `omitted_windows` on the interior record. `glass_panes` matches `through_windows`. Transparency on Quest is a known cost and has not been measured.

### Street elements

- **Observed:** OSM POIs `tree`, `bollard`, `bicycle_parking`, `bus_stop`, `fire_hydrant`, `traffic_signals`, `street_lamp`, `bench`, `waste_basket`. Marked `attribute_origin: observed`.
- **Generated:** lamps every 25 m beside `vehicular` ways, 0.5 m from the kerb, at least 1 m from buildings and 8 m from each other. Marked `attribute_origin: generated`.
- An element is placed only when its footprint fits on free walkable ground and does not touch another element. A missed observed element warns `urban_element_not_placed`. A missed generated lamp records the same code as info.
- Each type is one shared prototype mesh, instanced with a glTF `matrix`, plus a collision box.

### Collision

`scene_collision.glb` is a separate file, one material `collider`:

- buildings: the phase-03 prism (a flat facade; recesses and balconies do not change it), plus balcony boxes;
- buildings with an interior: walls with a door hole and a hole for each `glass_view` opening, a separate `<id>#glass` pane in each of those holes, a slab and a stair ramp for each flight, a roof-deck slab with the stair well cut out, and the parapet; without roof access, a solid above the furnished floors;
- street elements: instanced boxes;
- ground: the phase-03 slabs.

Every collider must be closed, manifold, and have positive volume (`collider_not_closed`, `interior_collider_not_closed`).

## Outputs

| File | Contents |
| --- | --- |
| `scene_visual.glb` | The detailed scene. Shared prototype meshes. Attribution and the fidelity statement in `extras`. Each building node has a `uv_frame` in its extras, each interior node a `light_anchors`. No `TEXCOORD_0` |
| `scene_collision.glb` | Collision, separate from the visual mesh |
| `scene_detail.json` | `onward_generator.scene_detail` 0.1.0. Input hashes, including the phase-03 base; fidelity, prototypes, and attribution; per object: template, storeys, openings, materials, facade use and its evidence, how the door was placed, roof, evidence, interior, and checks |
| `detail_report.json` | `onward_generator.detail_report` 0.1.0. The full configuration; opening, template, roof, door (`counts.doors`), facade use, and wall material counts; triangles by material and the budget; issues; `phase_04_checks` |
| `compare_overview.png`, `compare_closeup.png` | Before and after, phase 03 on the left and phase 04 on the right, same camera |

Material names that collide with the generation configuration are refused. Input files and the base are never overwritten, even with `--overwrite`.

## Budget (provisional)

| Limit | Value | Measured scene |
| --- | --- | --- |
| Visual triangles | 600 000 | 235 327 |
| Interior visual triangles (`interiors.max_triangles`) | 8 000 | 2 640 (Madrid sample) |
| Collision triangles | 120 000 | 42 896 |
| Materials | 32 | 24 |
| Nodes | 2 500 | 383 |

Crossing a limit warns `budget_exceeded`. These limits were not measured on the Quest. The headset budget is ADR-005.

## Checks

| Code | Severity |
| --- | --- |
| `facade_mesh_not_watertight`: an open edge other than the base and through-doors. Welded at 1e-6 m | critical |
| `refined_footprint_area_changed`, `roof_area_mismatch` | critical |
| `collider_not_closed`, `interior_collider_not_closed`, `non_finite_vertices` | critical |
| `urban_element_not_placed` (observed), `interior_include_unknown`, `budget_exceeded` | warning |
| `urban_element_not_placed` (generated), `interior_not_generated`, `landmark_tower_height_missing` | info |
| `landmark_tower_height_out_of_range`, `landmark_tower_footprint_too_small` | warning |
| `landmark_tower_mesh_not_closed` | critical |

Outputs are deterministic byte for byte for the same configuration and seed. Changing `seed` changes variants (materials, balconies, which interiors) and does not change validity.

`dress` is a later copy. It does not rewrite this directory. See `docs/08-dress.md`.
