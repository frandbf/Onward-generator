# ADR-004 — Facades, street elements, and interiors (phase 04)

Status: proposed (visual review of phase 04 is still open).

## Context

Phase 04 turns the phase-03 volumes into a street-scale scene: openings, balconies, roofs, materials, furniture, a few interiors, and separate collision.

OSM and Catastro provide the footprint, the height, and the storey count. They do not provide the arrangement of openings. Quest performance comes before ornament. Generation stays deterministic and traceable. The export adapter is a separate stage.

## Decision

1. **Generic templates, declared as such.**
   - Each building gets a template from its category. Each facade is `generic_template` with `representative_of_real_facade: false`.
   - Landmarks get `landmark_placeholder` and `needs_custom_model: true`, not invented ornament.
   - A commercial ground floor is applied only with recorded OSM evidence.
2. **Openings are cut into the building mesh.** They are not instances.
   - Windows, doors, and recesses are exact holes and notches in the wall face, with reveals.
   - The result is merged by material on the building node.
   - GEOS boolean is not used, because it rounds coordinates.
   - The mesh can be checked for watertightness.
3. **Street elements are instanced.** Each type is one shared mesh and nodes with a glTF `matrix`.
4. **Edge welding at 1e-6 m** for the detail watertightness check.
   - The phase-03 quantum of 1e-4 m split identical vertices that landed on a rounding boundary and produced false positives.
   - Phase 03 keeps its own quantum. Its output does not change.
5. **Collision stays simple and separate.**
   - Outside interiors, a building collider is the phase-03 prism plus boxes on the balconies.
   - Interiors use closed solids: walls with a door hole, a slab, a stair ramp, and a solid above the accessible floors.
6. **Interiors only in selected buildings.** Selection is a rule plus `include_ids`. The count is a hash of the seed from `min_buildings` (5) through `max_buildings` (8), within `max_triangles` (8 000 visual triangles for all interiors together; see `docs/07-detail.md`). An estimated height is raised so each storey is at least 2.8 m and the 2.4 m door fits under the 0.35 m lintel. A measured height is not raised. Furnished floors are 2, or 3 when the building has a spare storey and `sha256(seed, id, interior_floors) < 0.5`. A switchback stair continues to the flat roof deck. A hip roof stays closed.
7. **Variation by hash.** Every random choice uses `sha256(seed, id, …)`, never a stateful generator. Adding or removing a building does not change the others, with one exception: a wall material skips the materials of the touching buildings that came before it in id order, so a block does not read as one texture. Adding or removing a building can change the wall of a later building that touches it, and through it the next one.
8. **No new dependencies and no Onward assets.** Phase-04 geometry is procedural, with NumPy and Shapely. Later prop libraries (ADR-010) are a different stage and do not replace these templates.

## Consequences

- The scene reads as a street: opening rhythm, balconies, blind party walls, commercial ground floors, trees, and lamps. It is not a faithful reconstruction, and the manifest and the GLB say so.
- Reveals (`frame_stone`) were 45% of the visual triangles on the measured scene (106 500 of 235 327). If a Quest budget requires it, simplifying those reveals is the first lever.
- Collision does not follow opening recesses. A flat surface is more stable for the player.
- Landmarks need a specific model in a later stage. An observed OSM tower height may add a clipped prism (ADR-008).

## Amendment — Simple 3D Buildings tags (31 October 2026)

[ADR-023](ADR-023-simple-3d-buildings.md) replaces the tagged hip of this decision. `roof:shape` is drawn on any area as its own shape, with a recorded flat fallback. `building:part` is extruded from `height` or its levels, and `building:colour` and `roof:colour` tint shared materials.

## Amendment — flat upper openings (31 October 2026)

[ADR-025](ADR-025-flat-upper-openings.md) pulls the first lever named above. Windows and balcony doors from storey `openings.relief_storeys` up (default 2) are one quad flush with the wall, with no reveals; doors, shopfronts and pierced windows keep theirs. On Madrid r150 `frame_stone` fell from 36 854 to 19 606 triangles and the export from 142 408 to 125 160, with the same draws.

## Amendment — Interiors in buildings of up to five storeys, with the roof first (1 November 2026)

Decision 6 limited interiors to 5–8 selected buildings within 8 000 drawn triangles. The new default is every building of up to five storeys that has room for one, not a shop, with a way up to a flat roof: `interiors.rule` (`max_levels` 5, `min_levels` 1, every category but `religious`, 20–2 000 m²), a quota of 1 000 so the rule decides, and budgets of 24 000 drawn and 72 000 collision triangles.

- **The roof comes first.** A building gives up furnished floors, down to the ground floor, before it gives up the climb. Furnished floors stay at most three: a fifth-storey room adds a light and a floor nobody asked for, and the climb passes the storeys above them inside a solid.
- **One and two storeys.** They had no stair. A one-storey building has its room under the roof and a climb from it.
- **Flat roofs without a parapet** now have access, so their edge is open. A player can walk off it, as off any roof in the game.
- **Cost.** Madrid r120 has 16 stair interiors and 93 054 visual triangles, Murcia r90 has 6 and 25 828 (`docs/07-detail.md`). [ADR-025](ADR-025-flat-upper-openings.md) freed about 12 % of the exported triangles on Madrid r150, which pays for this on a map of this size. There is no sourced limit for collision triangles (ADR-005), so 72 000 is what the stair interiors of these maps need with margin, not a limit from a guide.
- **Open.** The 24 baked lights (ADR-013) leave 13 rooms of Madrid r120 without their own light. Whether those rooms read as too dark, how long the bake takes with this many rooms, and the frame time inside a building are headset questions, and shops still have no way up.
