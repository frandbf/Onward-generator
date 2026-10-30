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
- The only observed inputs are what earlier stages already had: the OSM footprint, height and storeys (Catastro or OSM), the building category, and OSM POIs. A commercial ground floor is applied only with evidence in `ground_floor_evidence`: an OSM POI (`shop`, `amenity`, `office`, or `craft`) inside the footprint, or within 2 m of it when the point is inside no footprint, or a building category that is already commercial. A point inside one building does not give its neighbour a shopfront across the party wall (ADR-016).
- **One building per POI.** The shopfront evidence and the facade use (below) share one rule. A POI inside a footprint belongs to that building. A POI outside every footprint belongs to the nearest footprint within `shop_evidence_distance_m` (2 m). On a tie (a point on a party wall, or as far from two footprints) the footprint that contains the point wins, then the lowest building id. So a POI on a party line gives one building a shopfront, not both. Before this rule the shopfront took every footprint within 2 m and the facade use every footprint at the nearest distance: a POI 1 m in front of a party wall gave both neighbours a shopfront.

## Inputs

`--base` is the validated phase-03 directory:

- `generation_report.json` must have `phase_03_exit.passed`;
- the recorded SHA-256 of `geo_scene.json`, `heights.json`, and the generation configuration must match;
- phase 03 is rebuilt in memory and the manifest must match the file on disk. Inside `run`, the rebuild is the copy kept from `generate` (`docs/06-generation.md`, Inputs).

A mismatch raises `DetailError` and writes nothing.

`config/detail.default.json` is validated with `config/detail.schema.json`. Every value is a modelling assumption, not an observation. Examples: every window or balcony fits its bay with a minimum pier; each category has one template; every material exists.

## What is built

### Facades

1. **Exposure.** A 1 m probe outside each edge is intersected with neighbouring footprints. An edge that touches a neighbour is a blind party wall. An exposed run shorter than 2.5 m is also blind. Cuts are inserted as collinear vertices, so the area does not change (`refined_footprint_area_changed` is critical). Interior courtyards get no openings.
2. **Storeys.** If `heights.json` has `estimated_levels`, the storey height is h/n. Otherwise 3 m is used, clamped between 2.6 m and 6 m. The origin is stored in `storeys.origin`. A building that receives an interior and whose storey is below 2.8 m is raised to 2.8 m per storey when the height is an estimate. An OSM `height`, a Catastro height, or Catastro levels stay as recorded. The previous height is kept on the building as `previous_height_m`, with `interior_storey_origin: estimated`. `heights.json` is not rewritten.
3. **Bays.** Each exposed run is split into bays of `bay_m`. The ground floor is `door_and_windows`, `shopfront` when there is evidence, or `main_door_only` on landmarks. The main door sits in the centre bay of the longest exposed run. On a `shopfront` ground floor it is a `shop_door` (see *Shopfronts and shop signs*). Upper floors are windows or balcony stacks. The choice is `sha256(seed, building, run, column) < balcony.share`. An opening is placed only when its recess stays inside the footprint. A balcony is placed only when it does not enter another footprint.
   **A door on every building.** When the template places no door (the storey is too low for the 2.4 m door under its 0.35 m lintel, or no wall reaches the 2.5 m street-facing minimum), a fallback door is cut. The walls are tried in this order: the street-facing walls, longest first (the first is the main frontage of a one-part building), then outer walls too short to count as street-facing whose 1 m (`probe_m`) strip outside meets no other footprint. A party wall is never used. The door sits in the centre bay of that wall. It keeps the template size when it fits and shrinks to the wall (a 0.5 m pier each side) and to the storey (minus the lintel), but not below 0.9 × 2.0 m. It replaces the ground-floor openings within 0.5 m of it. The building record says how the door came: `facade.door` is `template`, `fallback`, or `none`. A shop gets the fallback too, as a `shop_door` (glazed, 1.6 × 2.5 m when it fits), so a shop on a low storey still has an entrance. Measured on the six-building test row (`ROW_BUILDINGS` in `tests/detail_fixture.py`): 3 of 6 buildings had a door before, 6 of 6 after; the sixth, a shop on 2.67 m storeys, gets a 1.6 x 2.32 m shop door.
4. **Geometry.** Openings are exact holes in the wall face, plus notches for doors. GEOS boolean is not used. Each opening has a 0.15 m recess: jambs in `frame_stone` and a back of `glass_dark` or `door_wood` (a shop door is glazed: `glass_dark`). On a building with an interior, a window below the accessible ceiling uses the full wall thickness and a single `glass_view` quad. The wall and its recesses are a closed solid. The only open edges are the base (z = 0) and the through-door of an interior.
5. **Roofs.** An OSM `roof:shape` of `hipped`, `gabled`, `pyramidal`, `skillion`, `half-hipped`, `gambrel`, or `round`, on a footprint of at most 800 m², becomes a `hip`. `roof:material` chooses tile or a flat roof. That origin is `observed`. With no tag, a convex footprint of at most 160 m² that the template gives a parapet may receive an estimated `hip` (`origin: estimated`). Otherwise the template stands: `flat_parapet` (slab, parapet, coping), `hip`, or `flat`. A hip is a fan from the footprint's centroid, so it is kept flat when the centroid does not see every eave from inside: a jog of a few centimetres in a near-convex footprint (common where a Catastro split meets the street) would fold one face under the roof. The projected roof area is checked against the footprint (`roof_area_mismatch`).
6. **Materials.** OSM `building:material` or `material` wins (`wall_origin: observed`). Otherwise each building draws its wall from the palette of its **facade use** (`facade_palettes` in the configuration), and the record says `wall_origin: generated`. A landmark keeps its template wall (`stone_warm`).
   - **Use.** Read from the building's own tags first, then from a POI inside the footprint, then from an area that holds the footprint's representative point (a school ground), then from a POI outside every footprint whose nearest footprint within `shop_evidence_distance_m` (2 m) is this one (*One building per POI*, above). Street furniture POIs are not evidence. When several tags name a use, the most specific wins: `historic`, `institutional`, `office`, `retail`, `industrial`, `residential`. A building with no evidence is `residential`, `origin: estimated`. A POI near the footprint is also `estimated`; the building's tags, a POI inside, and an enclosing area are `observed`. The record keeps it as `facade.use`: `{"use", "origin", "evidence": [{"source_id", "tag", "match"}]}`, with `match` one of `building`, `poi_inside`, `enclosing_area`, `poi_near`, or `landmark`.

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
10. **Interior light anchors.** Each `interior/<building>` node in `scene_visual.glb` carries `extras.light_anchors` ([ADR-013](decisions/ADR-013-interior-light-anchors.md)), `attribute_origin: generated`. One entry per furnished room: `room_id`, `light_m` (the room centroid, or a point clear of the stair wells, 0.3 m under the ceiling, in local metres), `range_m` (half the room diagonal plus 1 m, 4–12 m), and `probes_m` (flat x, y, z triples: a grid of at most 4 × 4 columns, 0.5 m off the walls and out of the wells, at 1 m above the floor and 0.5 m under the ceiling). `scene_detail.json` does not change. The adapter turns the anchors into baked lights and light probes (`docs/12-export.md`). The map keeps at most `interiors.lights_max` (24) lights: see *Shop interiors*.
9. **Generated furniture.** Besides lamps, benches and bins are placed along pedestrian ways at a fixed spacing, from the seed, with a cap per type. An OSM point (`amenity=bench`, `amenity=waste_basket`, `natural=tree`, and similar) still places the observed object.

### Templates

| Template | Categories | Ground floor | Roof |
| --- | --- | --- | --- |
| `generic_residential` | `residential_multi`, `residential_small`, `unknown` | door and windows; balconies on 35% of upper bays | `flat_parapet` (1 m parapet) |
| `generic_commercial` | `commercial` | `shopfront` | `flat_parapet` (1 m parapet) |
| `generic_public` | `public`, `industrial` | door and windows; no balconies | `flat_parapet` (1.2 m parapet) |
| `generic_minor` | `minor` | door and windows | `hip` in `roof_tile` when convex and ≤ 200 m²; otherwise `flat` |
| `landmark_placeholder` | `religious` and any `landmark` | door only (2.4 × 4 m) | `flat` |

### Shopfronts and shop signs

Only a building with a `shopfront` ground floor (the evidence above) gets these. Configuration: `doors.shopfront`, `doors.shop_door` and `shops` in `config/detail.default.json`.

- **Display window.** Each ground-floor bay that is not the entrance gets one `shopfront`: as wide as the bay less `min_pier_m`, at most `max_width_m` (3.4 m), and as tall as the storey less `sill_m` (0.25 m, a stallriser) and `head_m` (0.45 m, room for the fascia), at most `max_height_m` (3.2 m). Below `min_width_m` (2.0 m) or `min_height_m` (1.9 m) the bay gets a window instead. On a 3 m storey and a 3.6 m bay that is 3.1 × 2.3 m (7.1 m²); the fixed 2.4 × 2.5 m opening it replaces was 6.0 m². A taller storey gets a taller window. The back is `glass_dark`, or `glass_view` when the shop interior pierces it (see *Shop interiors*).
- **Shop door.** The entrance of a shop is a `shop_door`, 1.6 × 2.5 m with a `glass_dark` back, where a home gets a 1.2 × 2.4 m `door` in `door_wood`. It is a recess like the plain door: it is not walkable and the collider stays the phase-03 prism. A shop that gets its interior has the walkable `interior_door` (1.2 × 2.4 m, open) in its place.
- **Shops.** Each evidence POI is a shop. A commercial building with no POI is one shop. A shop takes the free shopfront nearest to its POI. A shop left without one gets no sign.
- **Name.** The POI `name` tag (`name_origin: observed`). Without it, `shops.generic_names` gives a Spanish word for `shop=<value>`, `amenity=<value>`, … in the order of `shop_evidence_keys`, then for the key alone (`shop` → `Tienda`), else `default_name` (`Comercio`). Those carry `name_origin: estimated` and the rule used in `name_rule`.
- **Sign.** One per shop. A fascia when `sha256(seed, building, shop, shop_sign) < fascia_share` (0.5), otherwise a blade sign. The fascia is a 0.36 × 0.06 m board 5 cm above the window head, 0.15 m wider than the window on each side. The blade is a 0.7 × 0.5 m board, 4 cm thick, perpendicular to the wall, 0.15 m from it on a bracket, its bottom at 2.4 m, in the pier beside the window. No balcony or upper window reaches that pier. The semantic checks keep the fascia under `head_m` and both boards inside the pier.
- **Vinyl.** A shopfront with a `glass_dark` back gets a strip with the name of the nearest shop when `sha256(seed, building, segment, column, vinyl) < vinyl_share` (0.5). It is 80% of the window wide, 0.26 m tall, centred at 1.6 m (kept on the window), 1 cm in front of the glass. That holds for the see-through `glass_view` pane of a shop interior too: the strip sits 1 cm in front of the pane, inside the wall, and is a band across the glass, not a cover. Any other back (`glass_boarded`, a door) gets none.

`facade.shops` in `scene_detail.json` lists, per shop: `id`, `poi_id`, `name`, `name_origin`, `name_rule`, `sign` (`kind`, `centre_m`, `normal`, `width_m`, `height_m`, `depth_m`, `double_sided`, `bracket_m`, `attribute_origin: generated`, or `null`) and `vinyls` (same frame, depth 0). Buildings without a shopfront have an empty list. Phase 04 draws nothing for them: `signs place` builds the boards and the text atlas (`docs/01-pipeline.md`, 8b).

Cost per shop: a fascia is 10 triangles (front and four rims; the back is against the wall), a blade 20 (two faces, four rims, a four-sided bracket), a vinyl 2. All labels share one atlas material `shop_sign_atlas_0` (2048 px wide, 200 px per metre, a second page only when the first is full), and every rim and bracket shares `shop_sign_edge`: two materials for the whole map. The vinyl is opaque: the adapter draws only `glass_view` with a transparent shader, and an opaque `glass_dark` pane behind it hides nothing, so the strip takes the glass colour of the window image behind white letters. On a `glass_view` pane it is an opaque band 0.26 m tall, read from the street; its back faces the room and is culled, so from inside the player looks through the glass around and behind it. Signs add no collider. A fascia takes about 1.2 m² of lightmap and a blade about 0.7 m².

### Shop interiors

Every building with a `shopfront` ground floor (the evidence above) gets a generic shop storey behind its shopfronts, in place of the stair interior: one room per shop on its main frontage (see *Rooms*). Configuration: `interiors.shops` and `interiors.lights_max` in `config/detail.default.json`. Representation `generic_simplified_interior`, `representative_of_real_interior: false`, `attribute_origin: generated`: nothing about the real shop is known.

- **One storey.** The plan is the stair interior's with `max_floors = 1`: one open room over the whole inner footprint (the footprint less `wall_thickness_m`), the walkable `interior_door` (1.2 × 2.4 m) on the main frontage, the walls, the floor, and a ceiling at the first storey line. There is no stair, no upper floor and no roof access. Above the ceiling the collider is solid (`upper_solid`) and the upper windows stay `glass_dark`. A one-storey shop (a single storey in `heights.json`, often a Catastro sub-building, ADR-016) has its room under the roof: the ceiling is the underside of a `roof_slab` collider `slab_m` (0.2 m) thick under the roof line, there is nothing solid above it, and the shell's roof (flat or hip) is unchanged and stays closed. The stair interiors still need two storeys. The storey is raised to 2.8 m when the height is an estimate, as for any interior. The shop takes neither the quota nor the budget of the stair interiors, and a shop is never given the stair interior.
- **Rooms.** Each shop takes its shopfront as for its sign (above). When two or more shops have their shopfront on the wall of the main door, a partition wall `PARTITION_M` (0.2 m, in `detail/interiors.py`) thick runs across the room, perpendicular to that wall, between each pair of neighbouring shops: on the bay boundary between their shopfronts, the middle one when other bays lie between. It stays in the pier, at least `min_pier_m / 2` from both openings. A shop belongs to the room its shopfront opens into; a shop left without a shopfront (more shops than bays) belongs to the room of the main door. A shop whose shopfront is on another wall does not split the room and shares the room that wall belongs to. The room of the main door keeps it; any other room with a shop gets a walkable `interior_door` (1.2 × 2.4 m) centred on its first shop's shopfront bay, in place of that shopfront, and that shop's sign goes on the door (a door gets no vinyl). Every room gets its walls, floor, ceiling, colours and light. Room ids are `<building>/L0` (the room of the main door), then `<building>/L0-1`, `<building>/L0-2`, … Measured on the test street (`tests/integration/test_shop_interiors.py`): an 18 × 12 m building with a bakery and a pharmacy is two rooms of 98 m², two doors, two lights and 104 triangles.
- **Courtyards.** A footprint with an inner ring keeps it as a hole in the room: inner walls run round the courtyard `wall_thickness_m` from it, facing into the room, the floor and the ceiling slab leave the hole out, and the band of wall round the courtyard is solid in the colliders. Windows on the courtyard side (only with `exposure.courtyard_openings`) are treated like the street ones. A light whose room centroid falls in the courtyard moves to a point of the room. Measured: a 24 × 24 m shop round an 8 × 8 m courtyard is one 474 m² room, 23 see-through shopfronts and 174 triangles. The stair rule still excludes courtyards; an `include_ids` building with one now gets its stair interior round it.
- **Several parts.** A footprint of several parts (a multipolygon with two outer rings) has its main room in the part that holds the main door. Every other part whose inner ring holds a shopfront is one more room. Two parts never share a wall (they would be one polygon), so a part is entered only through a door of its own: it gets one when one of the building's shops has its shopfront there (the rule above), and otherwise it is seen through its see-through windows only, with `reachable: false` on its room record and no door connection. A part with no shopfront stays a closed volume and keeps its glass `glass_dark`. Measured: a 12 × 10 m part with a café and an unreachable 10 × 8 m part are two rooms (107 and 70 m²), one door, two lights and 118 triangles; a third part 3.4 m square, too narrow for a shopfront, stays solid with its dark windows. The stair interior still refuses several parts (`footprint_not_a_simple_polygon`).
- **See-through windows.** Every display window, window or balcony door below the ceiling becomes a hole through the wall with one `glass_view` quad, as for the stair interiors (below): the adapter draws it with `PP_Sprite_Lit`, both faces, so the player sees in from the street and out from the room. Vinyls stay on that glass (above).
- **Colour.** Each room's walls and floor take the colours of its shops' category; the ceiling stays `interior_wall`. The category is that of the first shop of the room, in `facade.shops` order, whose tags match one in `interiors.shops.categories`; categories are tried in their listed order. A shop that matches none is `generic`. A room with no shop (a part seen through its windows) takes the category of every shop of the building. With one room this is the building's first matching shop, as before. Six shared albedo-only materials, not one per shop:

  | Category | Tags | Walls | Floor |
  | --- | --- | --- | --- |
  | `bakery` | `shop` bakery, pastry, confectionery | `interior_shop_bakery` (warm yellow) | `interior_shop_tile` |
  | `pharmacy` | `amenity=pharmacy`, `shop=chemist`, `healthcare=pharmacy` | `interior_shop_pharmacy` (pale green) | `interior_shop_tile` |
  | `bar` | `amenity` bar, pub, cafe, restaurant, fast_food, ice_cream | `interior_shop_bar` (terracotta) | `interior_shop_wood` |
  | `generic` | any other shop | `interior_shop_generic` (peach) | `interior_shop_wood` |

  The names must start with `interior_`, so the export bakes them in the interior lightmap class (`docs/12-export.md`). `materials apply` gives them no photograph: they keep their colour over the plain procedural tile of `dress style`.
- **Light.** One light anchor in each room (ADR-013), reachable or not. See the cap below.
- **Budget.** `interiors.shops.max_triangles` (6 000) caps the visual triangles of all shop interiors together, apart from `interiors.max_triangles`. A shop that does not fit is not built (`interior_not_generated`, info, `triangle_budget: … of shops.max_triangles …`) and keeps its glazed `shop_door`. Shops are tried nearest the map centre (the local origin) first. A footprint above `interiors.shops.max_area_m2` (600 m²) is not given one (`shop_too_large`). Every room of a shop counts in its visual and collision budgets. Measured on the test block: the bakery in building 312 (222 m² of room, 8 see-through windows) is 64 triangles: two per floor and ceiling, the rest the walls around the door and window cuts. A 107 m² shop with 3 windows on the row block is 34. So the default budget holds about a hundred shops. The colliders add the cut walls, the panes and one solid above the ceiling: 108–2 828 collision triangles a shop on the Madrid and Murcia samples (*Triangle budget* below), about ten for each drawn triangle. `interiors.shops.max_collision_triangles` (72 000) caps them as `interiors.max_collision_triangles` does for the stair interiors.
- **Record.** The interior record has `kind: shop` (the stair interiors have `kind: residential`), `shop` (`category`, `wall_material`, `floor_material`, `attribute_origin: generated`; that of the room of the main door), `visual_triangles` and `collision_triangles`. Each shop room in `rooms` also has `reachable` (whether it has a door), `shops` (indices into `facade.shops`) and its own `shop`. `connections` has one `door` per reachable room. `counts.shop_interiors`, `counts.shop_interior_visual_triangles` and `counts.shop_interior_collision_triangles` in `detail_report.json` list them. A shop interior also keeps `panoramax apply` off that facade, as any interior does.
- **Light cap.** Shop rooms and stair-interior rooms share `interiors.lights_max` (24, at most 64), the same number as the adapter's `build.interior_lights_max` (`docs/12-export.md`). When the map has more rooms, shop rooms come first, then every room by the distance of its light from the local origin (the map centre), then by room id. A room past the cap keeps its probes, loses `light_m` and `range_m`, and gets `"light": "over_cap"`. Each such room is an `interior_light_dropped` issue (info), and `counts.interior_lights` is `{"max", "placed", "dropped": [room ids]}`.

Before this change a shop building could get the stair interior when it matched the rule (building 312 on the test block did: three floors and a stair behind a shopfront), and every other shop was a closed volume behind dark glass.

### Simplified interiors

Generated only in **selected buildings** that are not shops. The count is `sha256(seed, interiors, quota)` from `min_buildings` (5) through `max_buildings` (8). A map with fewer matches builds only those:

- `include_ids` first, except `exclude_ids`;
- then buildings that match the rule: `residential_multi` or `unknown`, at least 3 storeys, 80–500 m², rectangularity ≥ 0.8, not a landmark, no courtyards. Ordered by `sha256(seed:interior:id)`.

Buildings are tried in that order until the count is reached. A building whose plan fails (`no_room_for_stair`, `no_door_position`) does not use up the count. The objects and issues are still written in building order.

**Triangle budget.** `max_triangles` (default 8 000) caps the visual triangles of all `interior/<building>` nodes together: walls, floors, slab soffits and stairs. An interior that would take the total over it is not built. The building keeps the facade it has with interiors off, and `interior_not_generated` (info) records `triangle_budget` with the cost and what was left. Later buildings in the queue are still tried, so a smaller interior can fit after a larger one was turned down. The total is `counts.interior_visual_triangles` in `detail_report.json`.

The drawn triangles are counted before anything else is built. The interior is planned and its visual meshes are built (`interior_visual`); only when they fit are the shell with the interior door and the colliders built (`assemble_interior`). A building turned down costs its plan and its visual meshes, and its shell is built once, with the plain door. Before this change every candidate was built whole, shell and colliders included, and then thrown away when it did not fit, so a map with every match allowed built and discarded dozens of shells once the budget was full. The selection is the same: the count is the same number, taken earlier. `tests/integration/test_interior_budget.py` runs the old build-then-count loop beside the new one on the three test blocks, under budgets that turn interiors down, and compares every record, mesh and issue. On the Madrid and Murcia samples below, with every match allowed (`max_buildings` 50), the manifest and the report are byte for byte the same apart from the new collision fields, and `detail_scene` takes about 18% less time on Madrid (6.5 s to 5.3 s, median of three, a shared machine): 75 buildings there are turned down by the budgets, 71 of them shops.

**Collision budget.** `max_collision_triangles` (default 24 000) caps the collision triangles of the stair interiors together, and `interiors.shops.max_collision_triangles` (72 000) those of the shop interiors. An interior counts its own colliders: the cut wall bands, the slabs, the stair ramps, the roof deck or the solid above, and the glass panes. The building's balcony boxes and parapet are not counted. The collision count is known only once the colliders are built, so an interior over this limit is turned down after it was built, with `triangle_budget: needs N collision triangles, M left of max_collision_triangles L` (or `shops.max_collision_triangles`), and its building is rebuilt as with interiors off. The totals are `counts.interior_collision_triangles` and `counts.shop_interior_collision_triangles`; each interior record has `collision_triangles`. The collision limits are separate from `max_triangles`, not added to it: a collider costs physics, not drawing, the scene budget already keeps the two apart (`max_visual_triangles`, `max_collision_triangles`), and counting about 1.3 k collision triangles a stair interior against the 8 000 drawn would leave three interiors on a map that has fifteen. The defaults sit above everything measured, so the drawn limits still bind first and no map changes its interiors:

| Sample, every match allowed | Stair interiors: drawn, collision (each) | Shop interiors: drawn, collision (each) |
| --- | --- | --- |
| Madrid, 40.4153, −3.7074, 150 m | 14: 7 824, 17 612 (256–2 272) | 75: 5 974, 58 180 (128–2 828) |
| Madrid, interiors probe | 14: 8 000, 18 388 (828–2 688) | 63: 5 960, 56 340 (236–2 228) |
| Murcia, 150 m | 13: 7 836, 18 332 (404–2 176) | 45: 4 646, 40 380 (108–2 332) |

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
| `scene_detail.json` | `onward_generator.scene_detail` 0.1.0. Input hashes, including the phase-03 base; fidelity, prototypes, and attribution; per object: template, storeys, openings, shops, materials, facade use and its evidence, how the door was placed, roof, evidence, interior, and checks |
| `detail_report.json` | `onward_generator.detail_report` 0.1.0. The full configuration; opening, template, roof, door (`counts.doors`), facade use, and wall material counts; triangles by material and the budget; issues; `phase_04_checks` |
| `compare_overview.png`, `compare_closeup.png` | Before and after, phase 03 on the left and phase 04 on the right, same camera. Not written with `--no-preview`, which `run` passes unless given `--preview` |

Material names that collide with the generation configuration are refused. Input files and the base are never overwritten, even with `--overwrite`.

## Budget (provisional)

| Limit | Value | Measured scene |
| --- | --- | --- |
| Visual triangles | 600 000 | 235 327 |
| Interior visual triangles (`interiors.max_triangles`) | 8 000 | 2 640 (Madrid sample) |
| Shop interior visual triangles (`interiors.shops.max_triangles`) | 6 000 | 64 (test block, one shop) |
| Interior collision triangles (`interiors.max_collision_triangles`) | 24 000 | 18 388 (Madrid probe, every match) |
| Shop interior collision triangles (`interiors.shops.max_collision_triangles`) | 72 000 | 58 180 (Madrid, every match) |
| Interior lights (`interiors.lights_max`) | 24 | 5 (test block) |
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
| `urban_element_not_placed` (generated), `interior_not_generated`, `interior_light_dropped`, `landmark_tower_height_missing` | info |
| `landmark_tower_height_out_of_range`, `landmark_tower_footprint_too_small` | warning |
| `landmark_tower_mesh_not_closed` | critical |

Outputs are deterministic byte for byte for the same configuration and seed. Changing `seed` changes variants (materials, balconies, which interiors) and does not change validity.

`dress` is a later copy. It does not rewrite this directory. See `docs/08-dress.md`.
