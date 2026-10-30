# ADR-012 — Wall texture coordinates follow storeys and facade runs

Status: accepted · 29 October 2026. Amends [ADR-009](ADR-009-dress-materials.md) decision 4.

## Context

`dress style` and `materials apply` give every wall face its own projection: U is the position along that face and V is world height, both divided by the catalogue `tile_m`. U jumps at every corner, and brick courses or plaster bands do not meet the storey lines where `detail` already places the openings. `materials apply` also adds a hashed per-building `uv_phase` to V, which moves those bands again.

Only `detail` knows the storeys and which wall pieces face the street. The later steps read the detail GLB again (`nodes_from_glb`) and must not rebuild facades (ADR-009 decision 4).

## Decision

1. **`detail` writes the frame, not the coordinates.** Each building node in `scene_visual.glb` gets `extras.uv_frame`:
   - `runs`: one list per exposed run. A run is a chain of street-facing segments (`Segment.exposed`) in one ring, broken by party walls. Each segment is `[ax, ay, bx, by]` in local metres, in ring order, rounded to 1 µm.
   - `storey_h`: the storey height in metres, or `null`.
   - `storeys_origin`: `storeys.origin` (`heights_levels`, `recomputed`, `interior_storey`), or `null`. An estimated storey stays marked as estimated.
2. **`scene_detail.json` does not change.** The frame lives only in GLB node extras, which every later step already copies.
3. **`surface_uvs` has a facade mode.** For a non-opening, non-roof primitive of a node with `uv_frame`, a triangle that lies in a run segment's plane, faces out, and sits within the segment gets:
   - `u = (s0 + t) / tile_u`, where `s0` is the run length before the segment and `t` the position along it. `tile_u = L / n`, `n = max(1, round(L / tile_m))` for the run length `L`, so a run holds a whole number of repeats. The stretch is at most ±1/(2n).
   - `v = z / tile_v`, `tile_v = storey_h / k`, `k = max(1, round(storey_h / tile_m))`, so V is a whole number at every storey line from the ground (z = 0). With `storey_h` null, `v = z / tile_m` as before.
   Every other triangle keeps the previous projection, byte for byte.
4. **Phase and recentring.** `uv_phase` shifts U on run faces but not V when V follows storeys. Recentring treats each run as one island, so a corner inside a run stays continuous.
5. **When there is no frame.** A building without levels (`estimated_levels` absent) or a landmark (single band) gets `storey_h: null`. A scene written before this change has no `uv_frame` and keeps the previous UVs. Historical scenes are not regenerated.

## Rejected alternatives

- Writing `TEXCOORD_0` in `detail`. The coordinates depend on `tile_m`, which only the texture step knows, and `materials apply` would have to keep them through a material swap such as `brick_red`.
- A new field per segment in `scene_detail.json`. It changes a public contract for data only the GLB writer uses.
- Continuous U around the whole ring. Party walls are not textured as street facades, and a closed ring cannot hold a whole number of repeats on every edge.

## Consequences

- No new material, image, vertex or triangle. Draw calls and triangle counts are unchanged.
- `storey_h` changes the effective tile size by up to about 15% between buildings with 3.0 m and 3.5 m storeys. The repeats are aligned to storeys, not in true metres.
- Party walls and parapet inner walls keep the face projection.
