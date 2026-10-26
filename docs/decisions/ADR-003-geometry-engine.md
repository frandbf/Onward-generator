# ADR-003 — Phase 03 geometry engine

Status: accepted · 18 October 2026

## Context

Phase 03 builds ground, streets, sidewalks, footprints, and building volumes. The gate to phase 04 is buildings and streets with no critical topology errors. GLB is the interchange format. A GLB is not an Onward map.

## Decision

1. **In-repo geometry in Python**, with shapely 2.2 and numpy. Blender is not used.
   - Triangulation: `shapely.constrained_delaunay_triangles`, constrained Delaunay with no Steiner points, so courtyards stay holes.
   - Extrusion, walls, and caps live in `generation/mesh.py`.
   - No new dependency: numpy already came with shapely.
2. **A disjoint planar partition of the ground**, in this order: buildings, carriageway, water, pedestrian, sidewalk, green, ground. Each class is cut out of the later ones, and everything snaps to a 1 mm grid. That avoids coplanar overlaps, which would z-fight.
3. **Carriageway sunk 0.15 m**, with vertical kerbs facing inward. Sidewalks, pedestrian areas, and plazas stay at z = 0, level with the buildings.
4. **Buildings** are walls plus a flat roof. They have no bottom face, because it would be hidden.
5. **Collision is separate from the visual mesh:**
   - Each building is a closed prism of its footprint, simplified by 0.25 m.
   - The ground is two closed slabs: walkable and carriageway.
6. **Topology checks are mandatory.** Critical errors are open or non-manifold building edges (other than the base), colliders that are not closed or whose volume differs by more than 1%, triangulations whose area does not match the polygon, surfaces overlapping by more than 0.5 m², buildings with no height, and non-finite coordinates.
7. **Intermediate output is GLB 2.0**, visual and collision apart, plus `scene3d.json` for per-object provenance and `generation_report.json`. Local axes (east, north, up) become glTF (+X, +Y up, +Z south) as `(x, y, z) → (x, z, −y)`.
8. **An in-repo preview:** an orthographic z-buffer rasterizer that writes PNG with no extra dependencies.

A walkable slab drops holes under 0.0001 m² before extrusion, and opens a hole that only touches the boundary at a point. See `docs/06-generation.md`.

## Consequences

- The stage is deterministic byte for byte and checked with pytest.
- Textures, UVs, and pitched roofs belong to later stages. Mesh combining belongs to the export adapter (ADR-005, ADR-007).
- GLB stays an interchange format. Unity import and the Onward SDK package belong to the export adapter, and headset compatibility is a device test, not an assumption.
