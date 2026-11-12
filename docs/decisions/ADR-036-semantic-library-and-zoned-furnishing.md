# ADR-036 — A semantic description of every library row, recipes per room type, and a zoned furnishing planner

Status: proposed · 10 November 2026. It stays proposed until a headset session has walked furnished shops and homes.

## Context

Shops got five pieces each, and homes six, all against the walls (ADR-031, ADR-034). The sets were hand lists of 57 rows. The library holds about 1 300 placeable interior rows across the Kenney, Poly Haven, Quaternius and local Asset Store catalogues (ADR-035), and nothing said what each one is. A sofa could not be told from a shelf except by its id. On Madrid Sol Arena (r150) the furniture used 36 000 triangles of a 250 000 map, and the median shop room is 63 m².

## Decision

1. **A `semantic` block on every row.** `overgrow/semantics.py` describes each manifest row:
   - `kind` comes from a fixed vocabulary (`KINDS`: counter, shelf_tall, table, chair, sofa, bed, rug, plant_pot, decor_food and others).
   - `mount` says where the piece goes: wall, centre, corner, counter top, table top, flat on the floor, seat, or `none`.
   - `settings` says which rooms it fits: shop types and home rooms.
   - `origin: estimated` and a `basis` record that the block was read from the name, catalogue, role, placement and measured size.

   A row a person reviewed (`origin: reviewed`) keeps its block. The block is never placeable (`mount: none`) for:
   - split parts of one FBX (a cabinet's base, a handle);
   - hung pieces (wall shelves, pendants);
   - a large kind that measures under 0.45 m;
   - a model with a non-opaque material (`transparent: true`), which the export cannot draw.

   `tools/describe_library.py` rewrites every manifest, and `--check` fails when one is stale. The Asset Store importer writes the block when it builds a catalogue. Every tracked manifest carries it (`tests/unit/test_semantics.py`).
2. **Recipes, not hand lists.** With `selection: semantic` (the default for both furnishing blocks), `detail/recipes.py` builds the sets when the configuration loads. Each shop type (generic, bakery, bar, pharmacy) and the home set has a recipe: the kinds each slot takes and the settings it accepts. The slots are:
   - the existing `counter`, `wall` and `decor`;
   - `wall_deep` (beds and sofas against a wall), `center` (free-standing islands), `table` and `seat`, `corner`, `rug`, and `top` (small things on the tables).

   Per slot, a few models (`MODELS_PER_SLOT`) are drawn from the four lightest rows of each kind, at a seeded start, taking the kinds in turn. Each must fit the slot's size limits and cost at most 900 triangles (400 for decor). Small decor is taken biggest first, so it reads from the door. A slot nothing fills keeps the hand-written entries. `ONWARD_SEMANTIC_SETS=0` keeps the hand-written sets (the test suite does).
3. **A zoned planner.** A set with any zone slot is planned by zones in `detail/furnish.py`:
   - a rug;
   - the counter and its decor;
   - up to two deep wall pieces;
   - two corner pieces, placed before the wall runs, which start in the corners;
   - wall pieces up to 40 % of the room's pieces, each edge keeping the model that first fitted it, so a wall reads as one run;
   - table groups on a grid parallel to the longest wall: each table has its seats spread along both long sides, 0.5 m of clearance round the group, and one thing on top;
   - rows of islands with a `walk_width_m` aisle in front of each;
   - the walls again with what is left.

   Every check of ADR-031 still applies: free floor, door zone, pane bands, the clear path, front zones, and whole rooms within the budget. Decor, table tops, rugs and seats take no floor from the cap. Rugs and table tops get no collider. The cap grows with the room: one piece per 8 m², at least 4, at most `max_pieces_per_room` (12). A set of counter, wall and decor only keeps the ADR-031 plan exactly (its pinned records do not move).
4. **Budgets.** Shops: `max_triangles` 70 000, `max_collision_triangles` 8 000, `max_assets` 64. Homes: 30 000, 4 000, 64. Madrid Sol Arena, every shop and home furnished, measured 244 836 Unity triangles, under the official 250 000.

## Consequences

- Madrid Sol Arena: 42 of 42 shop rooms, 479 pieces (210 before), 57 331 triangles; 9 of 9 home floors, 186 pieces, 24 138 triangles. The pieces are 45 tables with 150 seats, 46 islands, 102 corner pieces, 181 wall and 18 deep wall pieces.
- The sets now follow the library. Re-describing a manifest, or adding a catalogue, can change which models a map gets. The sets are recorded in each room's `furnishing` block, as before.
- The kinds are estimated from names. A misnamed model can land in the wrong slot. The size limits per slot bound how wrong that looks, and a reviewed block corrects it.
- Not done: a second storey of shelving, wall-hung pieces, and kitchen or bedroom zones inside one home floor (a floor is one room).

## Amendment (12 November 2026): textured models before flat colours

On Rue Mouffetard (pkg_05, on the headset) a shop held a pale bookcase, a pale cabinet and a white fridge that read as untextured beside the photographic walls and floor. Their textures were intact: each face of these low-poly models samples one swatch of a palette, or a material is a 4 x 4 image of one colour, so the model has no grain. The selection took the lightest rows first, and the flat models are the lightest. Of the 1 505 interior rows on the measuring machine, 812 are flat (all of Kenney Furniture, Quaternius, Dollhouse, Low Poly Storage and Office, Customizable Kitchen; part of Apartment Kit and Clean Cozy), and the 46 Persian carpets lost their texture in conversion: every vertex samples one texel of a 4 x 4 image.

`overgrow/flatness.py` tells a model drawn in flat colours from the GLB's JSON, accessors and image sizes, never the pixels: a face is a swatch when its UVs span under 2 texels, or its image is at most 8 px, or it has no texture; a model is flat when nine tenths of its surface are swatches. `choose` sorts each kind's candidates textured first, then by weight (or size for small things), and the local rows of `with_local_furnishing` likewise, so a flat model is chosen only where fewer than `POOL` (or `ROWS_PER_SLOT`) textured models fit the slot. The 812 flat rows stay in the library.
