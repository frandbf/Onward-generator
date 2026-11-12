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

`detail`, `dress style` and `perf layout` rebuild phase 03 from the same two files. Within one process the last build is kept, keyed by the resolved path and SHA-256 of `geo_scene.json` and `heights.json` and the configuration's SHA-256, and a later call with the same key gets a copy of it. `run` executes every stage in one process, so it builds phase 03 once instead of four times. A stage run on its own still builds it. Nothing is kept on disk: the check in `detail` (see `docs/07-detail.md`, Inputs) compares the stored manifest with what this code builds, and a file left by older code could not prove that.

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
| `collider/walkable_crossing` | Walkable crossings (`Partition.crossings`): 4 m strips square to each vehicular segment between junctions, set back past the kerb of the widest street at each junction (half its width + 1 m + 2 m), and evenly between so no stretch exceeds 50 m. Each is clipped to the carriageway. Still part of `surface/carriageway`; only the collider is split. The adapter bakes it into the navmesh (ADR-015) | top −0.15 | Generated |
| `surface/zebra_crossing` | White bars of a Spanish *paso de cebra* on the crossing strips that OSM marks (see "Zebra paint" below): 0.5 m wide, 0.5 m apart, parallel to the street, each as long as the 4 m strip is wide, laid from kerb to kerb 0.5 m clear of each kerb. One quad (two triangles) per bar, material `road_marking`, no collider | −0.14 (1 cm over the carriageway) | Placement observed (OSM crossing), bar layout generated |
| `collider/walkable`, `collider/carriageway` | Closed slabs, 1 m thick. `collider/carriageway` is the carriageway minus the crossings, snapped again to `precision_m`: the cut adds vertices a fraction of a millimetre apart, which the 0.1 mm weld would merge. A hole under 0.0001 m² (a there-and-back spike) is removed before extrusion. A hole that only touches the boundary at a point, or two pieces of one slab that share a corner, are opened by a 1 cm disk and the result is snapped again to 1 mm, so the slab stays manifold. When the snap closes the cut again (two pieces that leave the corner almost parallel), the disk doubles until it does not. Every ring vertex of every part is then checked against the 0.1 mm weld (`weld_safe`): a vertex closer than 0.1 mm to its neighbour, or in the same cell, is removed (`short_edge_collapsed`), and two corners that are not neighbours but share a cell (a hole beside the outline, two parts, a ring passing beside itself) are opened by the same 1 cm disk | top 0 / −0.15 | Generated |

The site is the area of interest plus its margin. A building that is not completely inside that polygon is omitted (`building_outside_map`) and does not extend the ground. Footprints that remain inside the site are unioned with it.

If two footprints overlap by more than 95%, the duplicate is not built and `duplicate_building_skipped` is recorded. Other overlaps above 1 m² raise `overlapping_buildings`.

POIs, barriers, lines, and unclassified `leisure` are not built in this stage. They are reported as `feature_not_generated`. Crossing nodes are still read for the zebra paint.

## Zebra paint

The walkable crossing strips (ADR-015) are placed by a rule, not by the map, so most of them are plain asphalt. Paint goes only on a strip that OSM marks (`generation/markings.py`):

- **Evidence.** A node with `highway=crossing` or any `crossing=*` (for example `highway=traffic_signals` with `crossing=traffic_signals`), or a way with `footway=crossing`, `cycleway=crossing` or `path=crossing`. Anything with a `railway` tag is a level crossing and does not count.
- **Match.** The OSM element lies within 4 m (one strip width, `ZEBRA_MATCH_M`) of the strip clipped to the carriageway. The node sits on the street centreline, often on the kerb line of the junction, while the strip is set 1 m past that kerb.
- **Paint or not.** `crossing:markings`, when present, decides (`no` means no paint). Otherwise `crossing=no`, `unmarked` or `informal` means no paint, and every other value, or none, means a zebra. One painted element is enough. A strip that overlaps a strip already painted is not painted again, so no two bars share a plane.
- **Bars.** The kerb-to-kerb run is the piece of the strip's axis on the carriageway that holds the strip centre. As many bars as fit 0.5 m clear of each kerb are centred on it (a 6 m street gets 5). A bar that a curved kerb would cut is left out, so every bar stays a quad.
- **Depth and light.** The bars lie 1 cm (`ZEBRA_LIFT_M`) above the asphalt: with a 24-bit depth buffer and a 1 cm near plane the two stay apart to about 40 m, and the step cannot be seen. `road_marking` is a ground material for the lightmap (scale 0.5, `docs/12-export.md`).
- **Material.** One shared `road_marking` material, off-white (0.92, 0.92, 0.89). `dress style` gives it the default speckled tile, which reads as worn paint. Exported, it is `PP_Opaque_Simple_Lit` with an albedo only, like every other material.

The manifest record of `surface/zebra_crossing` lists every strip with its centre, `origin` (`observed` when an OSM crossing matched it, `generated` otherwise), the matching OSM ids, whether it is painted, and its bar count, plus the OSM crossings that matched no strip. `generation_report.json` counts them under `counts.crossings`: `strips`, `observed`, `observed_painted`, `observed_unpainted`, `generated_unpainted`, `bars`, `osm_crossings_unmatched`. Without a painted strip there is no `surface/zebra_crossing` object.

On the Madrid test extract (141 strips, 32 OSM crossings): 26 strips observed, 19 painted with 105 bars (210 triangles), 7 observed without paint (unmarked, or overlapping a painted strip), 115 generated and unpainted, and 12 OSM crossings more than 4 m from any strip. Those twelve stay unpainted: moving a strip to them would change the navmesh rule of ADR-015.

## Outputs

| File | Contents |
| --- | --- |
| `scene_visual.glb` | One node per object, with `extras` (`object_id`, `type`, `source_ids`). Ten shared materials (`road_marking` only when a crossing is painted). Attribution in `extras` |
| `scene_collision.glb` | One `<id>#collider` node per collider, one material |
| `scene3d.json` | `onward_generator.scene3d` 0.1.0: inputs with SHA-256, attributions (OSM and Catastro), the frame and glTF axes, and one record per object |
| `generation_report.json` | `onward_generator.generation_report` 0.1.0: triangle, vertex, material, and collider counts; area by class; checks; issues; `phase_03_exit` |
| `preview_southwest.png`, `preview_northeast.png` | Axonometric views rendered with no extra dependencies. Not written with `--no-preview`, which `run` passes unless given `--preview` |

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
| `slivers_dropped`, `degenerate_triangles_dropped`, `degenerate_hole_dropped`, `short_edge_collapsed`, `footprint_weld_cleaned`, `feature_not_generated`, `building_outside_map` | info |

**One grid for the partition (plan Q7).** The buildings of the partition are snapped to `precision_m` (1 mm) like the surfaces, and every clip, cut and union of the partition runs with that `grid_size`, so the whole partition shares one grid. Before 12 November 2026 only the surfaces were snapped: each moved its vertices up to half a cell into the unsnapped buildings, and the overlaps added up along every facade. Rue Mouffetard at 400 m went from `surface_overlap` 0.8288 m² (critical) to 0.1009 m², and its gap from 2.22 to 1.52 m²; 300 random 400 m scenes from 0.41 to 0.21 m² at most (median 0.042 to 0). The building meshes come from the footprints and do not move. A map regenerated from `generate` gets new surface triangles; existing scenes are not touched.

### Large maps with Catastro

The check stays all or nothing: one critical issue fails the phase. Each failure class seen on large Catastro maps (Madrid r200 and r300) is fixed in the stage that creates it, and the one object that cannot be fixed is quarantined there, never here:

| Failure | Where it is fixed |
| --- | --- |
| A sub-building whose hole touches its outline at a point after the 1 mm snap (`building_mesh_not_manifold` and `collider_not_closed` on the same id) | `cadastre split` notches the contact open ([05-cadastre.md](05-cadastre.md), step 7) |
| A sub-building that still does not extrude closed | `cadastre split` keeps that OSM footprint unsplit and reports its area; above 5% of the building area the split fails (step 8) |
| `collider/walkable_crossing` open where two crossings meet at a corner almost parallel | The ground slabs: the 1 cm cut is snapped and widened (table above) |
| `collider/walkable` open where the site corner keeps two vertices in one 0.1 mm weld cell | The slab drops that vertex before extrusion (`short_edge_collapsed`) |
| A slab or a building whose ring vertices the 0.1 mm weld would merge, neighbours or not | `weld_safe` checks every vertex before extrusion. A building records `footprint_weld_cleaned` (info), and one with nothing left is `building_invalid_footprint` |
| A hip roof over a footprint with a reflex jog of a few centimetres (`roof_area_mismatch` in `detail`) | `detail` keeps that roof flat ([07-detail.md](07-detail.md), Roofs) |

Outputs are deterministic byte for byte and are not overwritten without `--overwrite`.

The perimeter wall is not written in this directory. `detail --base` keeps pointing here. `bounds` runs later, on the dressed copy, and that copy is what `export` reads. See [01-pipeline.md](01-pipeline.md).
