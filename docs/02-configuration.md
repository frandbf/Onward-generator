# Project configuration

Defined by `config/generator.schema.json` (JSON Schema 2020-12) and the example `config/murcia-belluga.example.json`.

## Validation

```bash
.venv/bin/python -m onward_generator validate-config config/murcia-belluga.example.json [--json]
```

Exit codes: `0` valid, `1` invalid, `2` usage error.

Validation has three steps:

1. **Strict read** (`config/loader.py`). The file must be `.json`, UTF-8, at most 1 MB, with an object at the root, no duplicate keys, and no `NaN` or `Infinity`.
2. **Schema.** Unknown fields are refused (`additionalProperties: false`). Types, ranges, and enums are strict. Every error is reported, each with its JSON pointer (`/area/margin_m`).
3. **Semantic rules** (`config/validation.py`), only after the schema passes:

| Code | Severity | Rule |
| --- | --- | --- |
| `center_incomplete` | error | Latitude and longitude must both be numbers or both `null` |
| `area_undefined` | warning | With no centre and no `boundary_file`, the geographic pipeline cannot run. Valid as a template |
| `center_outside_crs` | error | The centre falls outside the area of use of `area.crs` (EPSG:25830 when the field is absent) |
| `boundary_not_found` / `boundary_invalid` | error | `boundary_file` is missing, or it is not a valid WGS84 GeoJSON Polygon |
| `margin_mismatch` | warning | `area.margin_m` differs from the GeoJSON `margin_m`. The configuration wins |
| `center_outside_boundary` | warning | The centre is outside the polygon |
| `export_adapter_unverified` | warning | `export.enabled: true` is valid, but `onward_unity` 0.1.0 has not passed the Quest acceptance test (`docs/12-export.md`) |
| `stage_not_implemented` | info | Enabled `generation` stages that do not exist yet |

Schema codes look like `schema_<keyword>`: `schema_minimum`, `schema_enum`, `schema_required`, `schema_additionalProperties`.

## One folder per map

`onward-generator map create` writes this configuration from a name and a WGS84 centre. The id and `maps/<id>` are the slug of that name (`Apocalipsis Murciana` becomes `maps/apocalipsis_murciana`). The folder holds `map.json`, `config/project.json`, `config/export.json` (card title and `build.lightmap_texels_per_m`, default 10, at most 40), `config/texture_plan.json`, and `reference/aoi.geojson`. `map.json` keeps `core_radius_m` (`--core-radius-m`, default 150): the half-side of the playable core square, or `null` when the whole map is played (0, a value at or above `radius_m`, or a map created before the key existed). A core must be at least 50 m. It is read by `detail` through its `next:` line; later stages read the `core` block of `scene_detail.json` ([ADR-017](decisions/ADR-017-playable-core.md)). `map.json` `sources` holds the `run --source` / `--no-source` switches per source plugin, for example `{"es.catastro": false}`; a plugin without a switch runs wherever it covers the centre. `config/sources.json` holds the plugins' licence allowlist, the one priority that ranks height sources ([ADR-018](decisions/ADR-018-source-plugins.md)) and `evidence`, the one ranking of ground-floor shop evidence: `osm`, then `es.catastro`, then `overture` ([ADR-022](decisions/ADR-022-catastro-use.md)). `map.json` may also hold `"facades": {"mode": "trim" | "legacy", "style": "<name>" | "auto"}` (either key optional; `map create` does not write it): `run` passes it to `materials apply` as `--facades` and `--facade-style`. Without it the facades use the trim-sheet atlas of the style chosen from the map centre ([ADR-020](decisions/ADR-020-trim-sheet-facades.md), `config/facade_styles.json`). Any other shape stops `run`. The projected CRS is EPSG:25830 in peninsular Spain and the UTM zone of the centre anywhere else. See [01-pipeline.md](01-pipeline.md).

## Paths

`area.boundary_file` is resolved from the configuration file's directory. The extension must be `.geojson`. The example points at `config/murcia_belluga_aoi.geojson`. A map created with `map create` points at `maps/<id>/reference/aoi.geojson`.

## From code

```python
from onward_generator.config import load_config, validate_config_file

config = load_config("config/murcia-belluga.example.json")  # GeneratorConfig or ConfigValidationError
config.area.center          # (lon, lat) or None
config.area.boundary_file   # absolute Path or None
```

`GeneratorConfig` and its sections are frozen typed dataclasses (`config/models.py`).

## Where the schema lives

The default is `config/generator.schema.json` at the repository root. `ONWARD_GENERATOR_SCHEMA` can point at another file.

## Natural language (phase 06)

`onward-generator ai plan` does not widen this contract. It writes copies of this configuration and of `heights.default.json` and `detail.default.json`, changing at most the whitelisted fields (`quality.detail_level` and `generation.interiors` on the project). Relative paths are rewritten to the same files, and the output is validated again with this schema. The area, the sources, the output path, optimisation, export, and the seed cannot change. See `docs/10-ai.md`.
