# ADR-013 — Interior light anchors in the detail GLB

Status: accepted · 29 October 2026. Follows the node-extras pattern of [ADR-012](ADR-012-wall-uvs.md).

## Context

On the headset the furnished interiors are dark. The scene has one baked directional light and nothing else: no light inside a building, and no light probes, so a player or a prop that is not in the lightmap gets only the ambient probe from the skybox. Guide p. 67 and ADR-005 leave no room for realtime lights on the Quest.

Only `detail` knows where the furnished rooms are, their floor and ceiling heights, and where the stair wells cut them. The adapter must not rebuild that from meshes, and `scene_detail.json` is a public contract that should not grow for data only the adapter reads.

## Decision

1. **`detail` writes anchors, the adapter places lights.** Each `interior/<building>` node in `scene_visual.glb` gets `extras.light_anchors`:
   - `attribute_origin`: `generated`. Nothing here is observed.
   - `method`: `room_ceiling_centre`.
   - `rooms`: one entry per furnished room, in the order of the interior record's `rooms`:
     - `room_id`: the room id from the interior record (`<building>/L<n>`).
     - `light_m`: `[x, y, z]` in local metres (x east, y north, z up), the frame of `uv_frame`. x, y is the centroid of the inner polygon, or a point of the room clear of the stair wells (well plus 0.3 m) when the centroid falls in one. z is 0.3 m under the room's `ceiling_z_m`.
     - `range_m`: half the diagonal of the room's minimum rotated rectangle plus 1 m, clamped to 4–12 m.
     - `probes_m`: a flat list of x, y, z triples (JsonUtility reads no nested arrays). Columns sit at the cell centres of a grid over the rotated rectangle, `ceil(side / 4 m)` cells per side, at least 2 and at most 4. A column is kept only when it is at least 0.5 m inside the walls and outside every well (plus 0.3 m). Each column has two probes: 1 m above the floor and 0.5 m under the ceiling.
   All values are rounded to 1 mm. The constants are in `detail/interiors.py`. They are modelling assumptions.
2. **`scene_detail.json` does not change.** Later steps (`dress style`, `materials apply`, `signs`, `overgrow`, `bounds`) copy node extras as they already do for `uv_frame`.
3. **The adapter bakes everything.** `OnwardMapAdapter.cs` reads `light_anchors` from the visual GLB nodes:
   - One `Light` per room: `Point`, `lightmapBakeType = Baked`, soft baked shadows so walls and slabs stop it, warm white, intensity `interior_light_intensity` (default 0.8, the sun is 1.2), range `range_m`.
   - At most `interior_lights_max` lights per map (default 24, schema maximum 64). The cap is applied in node order; the rooms past it keep their probes.
   - One `LightProbeGroup` holds the room probes plus outdoor probes: a grid of columns on the walkable navmesh every 12 m (wider when the map would need more than 1500 columns), at 1 m and 6 m above the ground.
   - Without a navmesh (`perf_layout`) no probe group is added: a group with only indoor probes would light the street from inside the buildings. Renderers outside the lightmap then keep the ambient probe, as before.
4. **Export options.** `build.interior_lights_max` and `build.interior_light_intensity` are optional keys in `config/export/onward.schema.json`. When they are absent the adapter uses the defaults above.

## Amendment — shop interiors and the shared cap (30 October 2026)

Shops now get a one-storey interior with one room and one anchor (`docs/07-detail.md`, *Shop interiors*). With 5–8 stair interiors and every shop of a street lit, a map passes 24 rooms. `detail` therefore applies the cap itself, as `interiors.lights_max` (default 24, the adapter's default): shop rooms first, because they are seen through the glass from the street; then every room by the distance of its light from the local origin, the map centre, where the player is most of the time; then by room id. A room past the cap keeps `room_id` and `probes_m`, loses `light_m` and `range_m`, and gets `light: over_cap`. `detail_report.json` lists the dropped rooms (`counts.interior_lights`) and each is an `interior_light_dropped` issue (info).

The adapter now collects the probes of a room before it checks `light_m`, so a room without a light still adds its probes. Before, a room without `light_m` was skipped whole. `export package` warns when the scene asks for more lights than `build.interior_lights_max`. Not compiled in Unity on this host.

## Amendment — several shop rooms (30 October 2026)

A shop building now has one room per shop on its main frontage, one per other footprint part with a shopfront, and keeps a courtyard as a hole (`docs/07-detail.md`, *Shop interiors*). Each room has its own anchor, in the order of the interior record's `rooms`, and its own id: `<building>/L0`, then `<building>/L0-1`, `<building>/L0-2`, … `light_m`, `range_m` and the probe grid are computed per room polygon, so a light never sits in a courtyard or across a partition. A room with no door (a part seen through its windows) is lit too. They all count against the same cap, shops first.

## Rejected alternatives

- Realtime or mixed lights. They cost every frame on the Quest; a baked light costs nothing at runtime.
- An emissive ceiling material. Exported materials are `PP_Opaque_Simple_Lit` with an albedo only.
- Fields in `scene_detail.json` or in the layout. Both are public contracts and only the adapter reads these values.
- Probes computed in the adapter from the room meshes. The adapter would rebuild geometry `detail` already knows, and the stair wells are not visible in the combined meshes.
- Area lights sized to the room. Baked-only in Unity 2021.3 and a better fit for wide rooms, but their brightness per area is harder to keep conservative before a headset test. Point lights first.

## Consequences

- No triangle, material or draw call is added. Lights and probes are baked: the cost is bake time and a little lightmap and probe data.
- With 5–8 interiors of 2–3 floors, a map has 10–24 lights, at most the default cap. A map with more rooms than the cap leaves the rooms past it dark (they keep their probes).
- Larger lightmaps are not needed: the lights change the lightmap contents, not their size.
- One point light under the centre of a large room (up to 500 m²) leaves its far corners dimmer. The range is clamped at 12 m.
- Not verified in Unity or on the headset. `docs/11-onward-sdk.md` records the guide rule as a lightmap and one baked directional light. This repository has no evidence on whether the Onward runtime or its post-processing profile accepts more baked lights and a `LightProbeGroup`, or overrides them. That is checked in Unity and on the headset (`docs/12-export.md`).

## Amendment, 3 November 2026

The default cap is now 160 in both `interiors.lights_max` and `build.interior_lights_max`, with a schema maximum of 256 for each. Measured on murcia2033 (r120): the cap of 24 lit 24 of 125 rooms and left 66 of 80 interiors fully dark, so most room windows showed an unlit room as a black square. 160 gives every room its light on that map. When the key is absent from the export configuration, the adapter still uses 24. Bake time with the higher count is measured per map in Unity; it has not been tested on the headset yet.

The default intensity is now 3.0 in `config/export/onward.default.json` and in the card `map create` writes (0.8 when the key is absent). With inverse-square falloff, 0.8 left about 0.13 on the floor of a room lit from 0.3 m under its ceiling; 3.0 gives about 0.48. The Unity comparison is in the commit that made the change.
