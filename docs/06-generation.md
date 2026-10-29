# Basic procedural geometry (phase 03)

Module `onward_generator.generation`. The design decision is `docs/decisions/ADR-003-geometry-engine.md`.

```bash
.venv/bin/python -m onward_generator generate \
  --scene   maps/<id>/data/processed/geo/geo_scene.json \
  --heights maps/<id>/data/processed/heights/heights.json \
  --out     maps/<id>/data/processed/scene3d \
  [--config config/generation.default.json] [--no-preview] [--overwrite]
```

Returns `0` when the phase 03 → 04 check passes (no critical error) and `1` otherwise.

## Inputs

- `geo_scene.json` (geo contract 0.1.0 or 0.2.0), in local metres. A frame that is not `local_grid` in metres is refused.
- `heights.json` computed from **that same scene**. The SHA-256 stored in `heights.json` must match.
- `config/generation.default.json`, validated with `config/generation.schema.json`. It sets way classes (`vehicular` and `pedestrian`) and default widths, sidewalk width (2 m), carriageway depth (0.15 m), water and green tags, the collision simplification tolerance, and the materials.

## What is built

| Object | Geometry | z | Origin |
| --- | --- | --- | --- |
| `building/<osm-id>` | Walls plus a flat roof. The OSM footprint extruded to `height_m` | 0 → h | Observed footprint. Height from `heights.json` (source, class, and confidence in the manifest) |
| `surface/carriageway` | `vehicular` ways, buffered by width/2, unioned, and clipped | −0.15 | Observed or inferred width (geo) |
| `surface/curb` | Vertical walls along the whole carriageway edge, facing inward | −0.15 → 0 | Generated |
| `surface/water` | Fountains, `natural=water` | 0 | Observed |
| `surface/pedestrian` | `living_street`, `pedestrian`, `footway`, `cycleway`, and pedestrian areas, buffered | 0 | Observed or inferred |
| `surface/sidewalk` | A 2 m band around the carriageway | 0 | **Generated.** Many extracts have no sidewalk polygons |
| `surface/green` | `landuse=grass`, `leisure=park`, and similar | 0 | Observed |
| `surface/ground` | The rest of the site | 0 | Generated |
| `collider/walkable`, `collider/carriageway` | Closed slabs, 1 m thick. A hole under 0.0001 m² (a there-and-back spike) is removed before extrusion. A hole that only touches the boundary at a point is opened by 1 cm so the slab stays manifold | top 0 / −0.15 | Generated |

The site is the area of interest plus its margin. A building that is not completely inside that polygon is omitted (`building_outside_map`) and does not extend the ground. Footprints that remain inside the site are unioned with it.

If two footprints overlap by more than 95%, the duplicate is not built and `duplicate_building_skipped` is recorded. Other overlaps above 1 m² raise `overlapping_buildings`.

POIs, barriers, lines, and unclassified `leisure` are not built in this stage. They are reported as `feature_not_generated`.

## Outputs

| File | Contents |
| --- | --- |
| `scene_visual.glb` | One node per object, with `extras` (`object_id`, `type`, `source_ids`). Nine shared materials. Attribution in `extras` |
| `scene_collision.glb` | One `<id>#collider` node per collider, one material |
| `scene3d.json` | `onward_generator.scene3d` 0.1.0: inputs with SHA-256, attributions (OSM and Catastro), the frame and glTF axes, and one record per object |
| `generation_report.json` | `onward_generator.generation_report` 0.1.0: triangle, vertex, material, and collider counts; area by class; checks; issues; `phase_03_exit` |
| `preview_southwest.png`, `preview_northeast.png` | Axonometric views rendered with no extra dependencies |

Axes: local `(x east, y north, z up)` becomes glTF `(x, z, −y)`.

## Checks

| Code | Severity |
| --- | --- |
| `building_mesh_not_manifold`: an open or non-manifold edge above the base | critical |
| `collider_not_closed`: an open collider, or a volume more than ±1% from the expected volume | critical |
| `roof_area_mismatch`, `surface_triangulation_mismatch` | critical |
| `surface_overlap` above 0.5 m² | critical |
| `building_missing_height`, `building_invalid_footprint`, `non_finite_vertices` | critical |
| `surface_gap` above 0.5 m², `overlapping_buildings`, `duplicate_building_skipped`, `footprint_repaired` | warning |
| `slivers_dropped`, `degenerate_triangles_dropped`, `degenerate_hole_dropped`, `feature_not_generated`, `building_outside_map` | info |

Outputs are deterministic byte for byte and are not overwritten without `--overwrite`.

The perimeter wall is not written in this directory. `detail --base` keeps pointing here. `bounds` runs later, on the dressed copy, and that copy is what `export` reads. See [01-pipeline.md](01-pipeline.md).
