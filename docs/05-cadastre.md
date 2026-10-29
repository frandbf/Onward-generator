# Storeys from Catastro (INSPIRE BU)

Module `onward_generator.cadastre`. Licence obligations are in `docs/legal/data-sources.md`.

Download and processing are separate. Only `download` uses the network. The service covers Spain. A map outside Spain skips this step.

```bash
.venv/bin/python -m onward_generator cadastre download \
  --scene maps/<id>/data/processed/geo/geo_scene.json \
  --out   maps/<id>/data/raw/catastro.gml

.venv/bin/python -m onward_generator cadastre match \
  --scene maps/<id>/data/processed/geo/geo_scene.json \
  --gml   maps/<id>/data/raw/catastro.gml \
  --out   maps/<id>/data/processed/cadastre

.venv/bin/python -m onward_generator heights \
  --scene     maps/<id>/data/processed/geo/geo_scene.json \
  --overrides maps/<id>/data/processed/cadastre/heights_overrides.catastro.json \
  --out       maps/<id>/data/processed/heights
```

## Download

- The bounding box is in the scene CRS (metres). Values that look like degrees are refused, and so is a box over 1 km².
- The request is HTTPS, a `GetFeature` of `bu:BuildingPart`. The generator identifies itself as `onward-generator/<version>`. The default curl user agent is refused by the service.
- The response must be a `FeatureCollection` with no DTD and no entities, and it must be at most 64 MB.
- The write is atomic and never overwrites without `--overwrite`. The sidecar `<file>.gml.source.json` stores the URL, the box, the date, the SHA-256, the size, the licence, and notes. `match` checks that SHA-256 before and after the read.

The downloaded GML stays out of git (`maps/*/data/`).

## Reading the GML

From each `BuildingPart`: `localId` (and the parcel reference `refcat`), `numberOfFloorsAboveGround`, `numberOfFloorsBelowGround`, and the geometry (`Surface` / `PolygonPatch`, including inner rings, 2D).

Parts are dropped with an issue when they are `malformed_part` (missing storeys, or another CRS), `invalid_part_geometry`, or `duplicate_part` (the same `localId` twice).

## Matching

For each OSM footprint, in the scene CRS:

1. Keep parts with **at least one storey above ground** that overlap the footprint by at least 1 m². Parts with 0 storeys, such as basements under courtyards, do not count.
2. `coverage` = area of the union of those overlaps ÷ footprint area.
3. `levels` = the **overlap-area-weighted median** of the storey counts.
4. Status: `matched` when `coverage ≥ 0.5`, `low_coverage` when parts exist but cover less, `no_parts` when there are none.
5. Confidence: 0.85 when `coverage ≥ 0.8`, otherwise 0.7, minus 0.1 when the median represents less than 60% of the area (`mixed`).

`--min-coverage` is configurable. Only `matched` rows go into `heights_overrides.catastro.json`. Each row has `levels` and no `height_m`, plus `source`, `license`, `retrieved_at`, `confidence`, and a note with the coverage, the parts, the storey range, and the `refcat` values.

## How `heights` uses it

An override that only has `levels` becomes `height_source: authoritative_levels` (class `inferred`): storeys × the category storey height. It ranks below an explicit OSM `height` and below an override with `height_m`, and above OSM `building:levels`. A difference is recorded as `override_levels_differ_from_osm`.

For landmarks, Catastro can count a tall nave as one storey. With `landmark.levels_floor_at_category_default: true`, the height does not fall below the category default (religious: 15 m). Those buildings stay **flagged for manual review**. The number is not a measured height.

On the Murcia cathedral and Plaza Belluga extract, 167 of 167 buildings matched, with coverage at least 0.83. That result is specific to that extract. It is not a guarantee for another city.
