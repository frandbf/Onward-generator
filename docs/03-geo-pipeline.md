# Geographic pipeline (phase 02)

Turns a local OSM XML file into a normalized scene in local metres, with an auditable report. It does not build 3D geometry and it does not export a game map.

## Stages

| Module | Responsibility |
| --- | --- |
| `geo/sources.py` | Register the local file: SHA-256, licence, attribution, and dates. Read only. |
| `geo/osm_import.py` | Read OSM XML 0.6 into nodes, ways, and relations, keeping ids, versions, and tags. Build WGS84 geometries. |
| `geo/projection.py` | WGS84 to the map CRS to local metres (ADR-002). |
| `geo/topology.py` | Invalid, empty, or tiny geometries, repeated vertices, and duplicates. |
| `geo/clipping.py` | Keep features in the area of interest plus the margin. |
| `geo/attributes.py` | Typed attributes. Observed values stay distinct from inferred ones. |
| `geo/aoi.py` | Load the area of interest from GeoJSON (RFC 7946). |
| `geo/pipeline.py` | Run the stages and write `geo_scene.json` and `geo_report.json`. |

## Import rules

- **XML safety.** Files with `<!DOCTYPE` or `<!ENTITY` are refused. So are files whose root is not `<osm>`.
- **Repeated elements.** The same type and id twice keeps the first and warns `duplicate_element_id`.
- **Deleted elements.** `action="delete"` or `visible="false"` are skipped (`deleted_element`).
- **Untagged nodes and ways** are geometry, not entities.
- **Areas.** A closed way is an area when it has `building`, `landuse`, `leisure`, `amenity`, `natural`, `place`, `shop`, `tourism`, or `historic`, or `area=yes`. `area=no` cancels that. Other ways are lines.
- **Relations.** Only `type=multipolygon` is assembled. Others warn `unsupported_relation`. `osm download` asks Overpass for multipolygon relations only, so a new extract has none to warn about. An older extract, whose `.source.json` has no `relation_types`, holds every relation in the box and its member ways from outside the area: its `geo_report` counts more `osm_relations`, `osm_ways` and `dropped_outside` and lists the `unsupported_relation` warnings, and its `geo_scene` features are the same.
- **Multipolygon rings.** The member ways are noded together and polygonized, so a ring may be split across any number of ways, and rings may touch at a node. The rings are the faces that the `outer` ways and the `inner` ways each close on their own, polygonized per role. A face enclosed by an odd number of rings is filled and an even number is a hole (even-odd nesting). So a courtyard that shares an edge or a vertex with its outer ring is a hole, although the noded faces return it as a face of its own; two outer rings that share one way are both filled; and an island inside a courtyard is kept. Member roles are advisory: an `inner` ring that no ring encloses warns `inner_ring_outside_outer` and stays an area, and an `outer` ring nested in another outer warns `outer_ring_inside_outer` and becomes a hole. Limitation: an `outer` ring nested in another outer and sharing an edge with it has the same linework as two adjacent outers, so it stays filled without a warning. The relation still needs at least one `outer` member. Exteriors are written counter-clockwise and holes clockwise (RFC 7946). Vertices are the OSM node positions; noding adds a vertex only where two member ways cross.
- **Construction errors** (the entity is not created): `missing_node_reference`, `degenerate_way`, `degenerate_ring`, `missing_member`, `missing_outer_ring`, `unclosed_multipolygon_ring` (a member way leaves a dangle, a cut edge, or an invalid ring).
- **Data date.** If the source record does not set it, `source.data_timestamp` comes from the file: Overpass `<meta osm_base>` or the `<osm>` `timestamp`.

## Clipping

The inclusion zone is the area of interest expanded by `margin_m`, with mitre corners.

| Status | Meaning |
| --- | --- |
| `inside` | Covered by the area of interest. |
| `margin` | Touches the inclusion zone and is not covered by the area of interest. Areas and points are kept whole. A building is kept only when the zone covers it. |
| `clipped` | A line cut at the inclusion boundary. |
| `outside` | Misses the zone, or a building that is not completely inside it. Dropped and counted in `dropped_outside`. |

A building that leaves the inclusion zone is dropped whole. It is not cut, and the part that sticks out is not kept. An outside sliver of at most 0.01 m² is treated as the boundary. The generator does not invent a footprint OSM did not provide inside that zone.

## Geometry checks

| Code | Severity | Effect |
| --- | --- | --- |
| `invalid_geometry` | error | For example a self-intersection. Refused, unless `--repair-invalid` runs `make_valid` and sets `geometry_repaired: true` |
| `empty_geometry` | error | Refused |
| `tiny_polygon` / `tiny_line` | warning | Area under 1 m² or length under 0.5 m |
| `repeated_vertices` | warning | Identical consecutive vertices |
| `duplicate_geometry` | warning | Same geometry and kind within 1 mm, even if the vertex order differs |
| `outside_crs_area_of_use` | warning | The feature leaves the CRS area of use |

## Attributes

| Attribute | Observed (tag) | Inferred (rule, confidence) |
| --- | --- | --- |
| `height_m` (building) | `height`, in metres, `ft`, or `6'2"` | — (see `docs/04-heights.md`) |
| `levels` (building) | `building:levels`, a positive integer | — |
| `width_m` (way) | `width` | Default width for that `highway`, confidence 0.4 |
| `lanes` (way) | `lanes` | — |

From `schema_version` 0.2.0, geo does not estimate building height. It publishes explicit tags only. Estimation belongs to `heights`. A 0.1.0 scene may still carry an inferred `height_m` of `building:levels × 3.0 m`. `heights` ignores any attribute that is not `observed`, so it accepts both versions.

An explicit value is never replaced by an inference. A value that cannot be parsed warns `unparseable_tag` and produces no attribute. The origin `generated` exists on the model. No rule in this stage uses it.

## Output contract

`geo_scene.json` (`schema: onward_generator.geo_scene`, `schema_version: 0.2.0`):

```json
{
  "schema": "onward_generator.geo_scene",
  "schema_version": "0.2.0",
  "source": { "provider": "openstreetmap", "license": "ODbL-1.0", "sha256": "…", "retrieved_at": "…", "…": "…" },
  "attribution": { "text": "© OpenStreetMap contributors", "license": "ODbL-1.0", "license_url": "…" },
  "area_of_interest": { "name": "…", "polygon_wgs84": [[lon, lat], "…"], "margin_m": 30, "crs": "EPSG:25830" },
  "frame": { "crs": "EPSG:25830", "coordinate_system": "local_grid", "unit": "m", "axes": {"x": "east", "y": "north"}, "origin_wgs84": [lon, lat], "origin_projected": [E, N], "transformation": "…" },
  "features": [
    {
      "id": "osm-way-102",
      "source": { "provider": "openstreetmap", "license": "ODbL-1.0", "osm_type": "way", "osm_id": 102 },
      "kind": "building",
      "tags": { "building": "residential", "building:levels": "3" },
      "geometry": { "type": "Polygon", "coordinates": [[[80.0, 0.0], "…"]], "unit": "m", "coordinate_system": "local_grid" },
      "geometry_repaired": false,
      "clip_status": "margin",
      "attributes": {
        "levels": { "value": 3, "origin": "observed", "source_tag": "building:levels" }
      }
    }
  ]
}
```

`geo_report.json` (`schema: onward_generator.geo_report`) repeats the header and adds `options`, `counts`, `issues_by_code`, and `issues` (code, severity, message, OSM element).

Features are ordered by kind and OSM id. The same input always produces the same scene. Outputs are not overwritten without `--overwrite`, and writes are atomic.
