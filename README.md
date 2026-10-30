# onward-generator

Local tool that turns legal geographic data into 3D scenes for custom Onward maps on Meta Quest. The command is `onward-generator` (`python -m onward_generator`), version 0.1.0.

OpenStreetMap supplies the streets and building footprints. The generator extrudes footprints the extract actually contains. Heights can come from a deterministic estimate or, in peninsular Spain, from Catastro floor counts. A later dressing pass adds facades, materials, street names, and an optional abandoned-city layer. Unity 2021.3 and the Onward custom-content SDK build the headset package. A GLB is not a playable map. The headset checklist in `quest_acceptance.md` is still open, so `device_verified` stays false. See [docs/12-export.md](docs/12-export.md).

Documentation is in English. Working rules for changes are in `AGENTS.md`. The operational contract is [docs/01-pipeline.md](docs/01-pipeline.md).

## Requirements

- macOS (tested on macOS 26.6.2, Apple M4 Pro) or Linux. The host is detected, or chosen with `--platform macos|linux` or `ONWARD_PLATFORM`; see [docs/01-pipeline.md](docs/01-pipeline.md#host-platform). The Linux Unity paths are the Unity Hub defaults and have not been checked with Unity on Linux.
- Python 3.12 or newer (tested with 3.14.6).
- Pinned dependencies, all from binary wheels:
  - `shapely==2.2.0` (GEOS 3.14.1): geometry.
  - `pyproj==3.8.0` (PROJ 9.8.1): projections. The EPSG database is included and works offline.
  - `jsonschema==4.26.0`: configuration checks against JSON Schema 2020-12.
  - `pillow==11.3.0`: image decode and encode. Every PNG the generator writes or reads (atlases, thumbnails, previews, plaques), JPEG inputs (albedo textures in `materials apply`, Panoramax street photos), and the decode and resize of imported overgrow textures.
  - `pytest==9.1.1`: tests.
- The locked environment, including transitive dependencies, is `requirements.lock`.
- Optional, for phase 06, texture selection, and overgrow plans: [Ollama](https://ollama.com) on loopback with a model already installed (tested with Ollama 0.33.2 and `qwen3:14b`). There is no extra Python dependency. The client uses the standard-library `urllib`. Without Ollama, placement and texture selection use the deterministic fallback.
- Unity **2021.3.23f1** and the Onward Custom Content SDK v1.15, both outside this repository. See [Prepare Unity once](#prepare-unity-once).

## Install

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --only-binary=:all: -r requirements.lock
.venv/bin/python -m pip install --no-deps -e .
```

The editable install (`-e .`) is required: the code finds `config/` and `assets/` relative to the repository. It also installs the `.venv/bin/onward-generator` command, which is the same as `.venv/bin/python -m onward_generator`.

### Docker (optional)

To keep the machine clean, the Python pipeline and its checks can run in a container instead of `.venv`. The image installs `requirements.lock` and `shellcheck`. The repository is mounted at `/work`, so outputs land in `maps/` as usual and belong to your user. The container uses the host network, so loopback Ollama works the same way. Unity and `adb` stay on the host.

The checks have the same names locally and in CI ([go-task](https://taskfile.dev), `Taskfile.yml`). CI runs `task ci` on Linux.

```bash
task --list            # test, lint, typecheck, guard, lint:shell, lint:actions, smoke, ci, mutate, image, shell
task ci                # everything the CI gate runs
task test -- -k cadastre
task lint -- --fix     # ruff (rules in pyproject.toml); --fix applies the safe fixes
task typecheck         # mypy on src; only errors missing from mypy-baseline.txt fail
task guard             # a change must not edit both src/ and a quality gate (AGENTS.md)
```

### Mutation testing

`task mutate` changes the lines a branch touched and checks that some test notices. It takes the lines in `src/onward_generator` added or changed since `BASE` (default `main`, uncommitted edits included), lets [mutmut](https://mutmut.readthedocs.io) 3.8.1 from `requirements-mutate.lock` generate the mutants of the functions that contain them, and runs only the mutants whose edit falls on one of those lines. A mutant is a small edit such as `<` to `<=`, `+` to `-`, or a constant moved by one. `task mutate -- --whole-functions` runs every mutant of those functions instead, which also measures the old code around the change.

```bash
task mutate                         # lines changed since main
task mutate BASE=fork/main MAX=10   # another base; at most 10 functions
task mutate JOBS=4                  # cap mutmut workers (default: all cores in CI, half locally)
```

Exit 0 means every mutant was killed. Exit 1 lists each survivor with its diff: a behaviour no test holds. Write a test that fails on that edit, or, when the edit changes nothing a caller can see, end that line with `# pragma: no mutate: <reason>`. The reason must not start with `block`, `start` or `end`, which are mutmut keywords. Exit 2 means the tool could not run. Functions over `MAX` are listed as not mutated. mutmut works on a copy in `mutants/` (gitignored), and a second run only retests what changed. On a pull request, CI runs the same task against the target branch.

Any other command goes through the same wrapper:

```bash
tools/docker/run.sh onward-generator doctor
tools/docker/run.sh onward-generator run --name "Map name" --lat 37.98407 --lon -1.12863
tools/docker/run.sh --build pytest -q   # rebuild after requirements.lock changes
```

The first call builds the image `onward-generator:dev`. Unity is not in the image: inside the container, `run` writes the export package and then fails at the Unity step. Build that package on the host with `tools/unity/onward_adapter/run_export.sh`.

Check the environment:

```bash
.venv/bin/python -m onward_generator doctor
.venv/bin/python -m onward_generator validate-config config/murcia-belluga.example.json
```

`doctor` is read-only. A missing Unity editor, `adb`, or SDK package is a warning. `validate-config` checks the example project. Exit `0` is success, `1` is a failed check, `2` is a usage error.

## Prepare Unity once

Export needs Unity **2021.3.23f1** and the Onward Custom Content SDK v1.15. Neither is in this repository. Put the SDK package at:

```text
~/Unity/OnwardSDK/OnwardCustomContent_SDK_v1.15.unitypackage
```

Create the project, import the SDK, patch the launcher path (macOS and Linux), and install the XR packages:

```bash
tools/unity/phase0_room/run_phase0.sh create import patch tool xr configure
```

The project is `~/Unity/OnwardPhase0Project`. Do not open it with another Unity editor: a newer editor rewrites it and 2021.3.23f1 can no longer open it. Close the Editor before a map build. `room` and `launcher` build the 12 m probe room. A city map does not need those two steps.

## Make a map

`--lat` is the WGS84 latitude. `--lon` is the WGS84 longitude. A negative longitude uses the hyphen on the keyboard (`-3.65288`). A typographic minus is rejected.

```bash
.venv/bin/python -m onward_generator run \
  --name "Map name" \
  --lat 37.98407 \
  --lon -1.12863 \
  --radius-m 400 \
  --margin-m 30 \
  --lightmap-texels-per-m 10 \
  --brief "How the streets should feel."
```

`--radius-m` is half the side of the square, in metres (50–1500, default 400). `--margin-m` defaults to 30. `--lightmap-texels-per-m` defaults to 10 and cannot exceed 40. `--brief` is stored on the map and read by overgrow. `--ai` asks local Ollama for a placement plan. A rejected reply falls back to the placement rules.

`run` creates `maps/<id>/` from the name, downloads that square from Overpass, and runs every Python stage through the export package. It then builds in Unity (`tool scene launcher`) and asks before uploading. Only `s` or `si` uploads. Any other answer, or a terminal that cannot ask, leaves the headset alone.

If the Editor already has `~/Unity/OnwardPhase0Project` open, `run` stops before the first stage and asks you to close it. The stages start after `s` or `si`, and only once that project is closed. Any other answer cancels the run.

The stages inside `run` are:

`map` → `geo` → `heights` → `generate` → `detail` → `dress` → `materials` → `signs` → `overgrow` → `fit` → `bounds` → `layout` → `export` → Unity → Quest

`props place` and `cadastre` are separate commands. `run` does not call them. `overgrow fit` is required: if a placed prop is the wrong size or blocks the street, `bounds` does not start. There is no single `validate` command. Each stage refuses to write when its input fails, and `export package` checks again. An export package is never overwritten. The next one is `pkg_02`.

If a stage fails, the console prints `FAILED` and the stage to resume from. Run the same command again. Finished stages are skipped. `--from` resumes at a named stage without asking:

```bash
.venv/bin/python -m onward_generator run \
  --name "Map name" \
  --lat 37.98407 \
  --lon -1.12863 \
  --from dress
```

The names are `map`, `osm`, `geo`, `heights`, `generate`, `detail`, `dress`, `materials`, `signs`, `overgrow`, `fit`, `bounds`, `layout`, and `export`.

A second `run` with the same name and the same centre continues that folder. A different centre for that name is refused. `--overwrite` replaces `map.json` only while `data/processed/` is still empty.

If Overpass fails after the folder exists:

```bash
.venv/bin/python -m onward_generator osm download --map maps/<id>
```

The headset has one local slot. The launcher always writes `unity-export.local`. The card title on the headset is `Unity Export`. The name passed to `run` is the folder name and the title stored in `maps/<id>/config/export.json`.

Each stage prints an indented result and `OK` with its duration. At the end, `run: time` lists the stages that ran and the total. The Quest question is excluded. Full paths and the Unity log stay out of that summary. The editor log is `~/Library/Logs/onward-export/` on macOS and `~/.local/state/onward-generator/onward-export/` on Linux.

To build or upload without going through `run`:

```bash
tools/unity/onward_adapter/run_export.sh maps/<id>/data/processed/export/pkg_01 tool scene launcher
CONFIRM_PUSH=yes tools/unity/onward_adapter/run_export.sh maps/<id>/data/processed/export/pkg_01 push
```

`push` replaces the local map on the headset after a backup.

## Stages by hand

`run` is the usual path. The same stages, one command at a time. `map show` prints the `geo` command for that folder, including its CRS.

```bash
.venv/bin/python -m onward_generator map create --name "Map name" --lat 37.98407 --lon -1.12863
.venv/bin/python -m onward_generator map show maps/<id>

.venv/bin/python -m onward_generator geo \
  --source maps/<id>/data/raw/<id>.osm \
  --aoi maps/<id>/reference/aoi.geojson \
  --out maps/<id>/data/processed/geo \
  --crs EPSG:25830

.venv/bin/python -m onward_generator heights \
  --scene maps/<id>/data/processed/geo/geo_scene.json \
  --out   maps/<id>/data/processed/heights

.venv/bin/python -m onward_generator generate \
  --scene   maps/<id>/data/processed/geo/geo_scene.json \
  --heights maps/<id>/data/processed/heights/heights.json \
  --out     maps/<id>/data/processed/scene3d

.venv/bin/python -m onward_generator detail \
  --scene   maps/<id>/data/processed/geo/geo_scene.json \
  --heights maps/<id>/data/processed/heights/heights.json \
  --base    maps/<id>/data/processed/scene3d \
  --out     maps/<id>/data/processed/scene_detail

.venv/bin/python -m onward_generator dress style \
  --detail maps/<id>/data/processed/scene_detail \
  --scene  maps/<id>/data/processed/geo/geo_scene.json \
  --heights maps/<id>/data/processed/heights/heights.json \
  --out    maps/<id>/data/processed/scene_dress

.venv/bin/python -m onward_generator materials apply \
  --base maps/<id>/data/processed/scene_dress \
  --plan maps/<id>/config/texture_plan.json \
  --out  maps/<id>/data/processed/scene_materials

# under development; only when map.json has "panoramax": true; then signs --base is scene_panoramax
.venv/bin/python -m onward_generator panoramax apply \
  --base maps/<id>/data/processed/scene_materials \
  --out  maps/<id>/data/processed/scene_panoramax

.venv/bin/python -m onward_generator signs place \
  --base  maps/<id>/data/processed/scene_materials \
  --scene maps/<id>/data/processed/geo/geo_scene.json \
  --out   maps/<id>/data/processed/scene_signs

.venv/bin/python -m onward_generator overgrow place \
  --base    maps/<id>/data/processed/scene_signs \
  --scene   maps/<id>/data/processed/geo/geo_scene.json \
  --library assets \
  --profile 100 \
  --seed    48152 \
  --out     maps/<id>/data/processed/scene_overgrow

.venv/bin/python -m onward_generator overgrow fit \
  --scene maps/<id>/data/processed/geo/geo_scene.json \
  --base  maps/<id>/data/processed/scene_overgrow \
  --out   maps/<id>/data/processed/scene_overgrow/fit.json

.venv/bin/python -m onward_generator bounds \
  --base maps/<id>/data/processed/scene_overgrow \
  --out  maps/<id>/data/processed/scene_playable

.venv/bin/python -m onward_generator perf layout \
  --scene   maps/<id>/data/processed/geo/geo_scene.json \
  --heights maps/<id>/data/processed/heights/heights.json \
  --base    maps/<id>/data/processed/scene3d \
  --detail  maps/<id>/data/processed/scene_playable \
  --playable \
  --out     maps/<id>/data/processed/layout/layout.json

.venv/bin/python -m onward_generator export package \
  --project maps/<id>/config/project.json \
  --detail  maps/<id>/data/processed/scene_playable \
  --layout  maps/<id>/data/processed/layout/layout.json \
  --config  maps/<id>/config/export.json \
  --out     maps/<id>/data/processed/export/pkg_01
```

`map show` prints the `--crs` stored for that map. Peninsular Spain uses EPSG:25830. Anywhere else uses the UTM zone of the centre. Pass that value to `geo`. Other `geo` options: `--margin-m` replaces the margin in the GeoJSON, `--origin LON LAT` sets the local origin (default is the centroid), `--repair-invalid` repairs invalid geometries, `--overwrite` replaces existing outputs, and `--retrieved-at` records the download time. Degrees are never treated as metres. The `.osm` file is not modified. `geo` writes `geo_scene.json` (the scene in local metres) and `geo_report.json`. The source record is OpenStreetMap (ODbL-1.0, `© OpenStreetMap contributors`). Details are in `docs/03-geo-pipeline.md`.

A library texture plan does not use `--download`. Add `--download` only for an older Poly Haven plan.

Optional, and not called by `run`:

```bash
.venv/bin/python -m onward_generator cadastre download \
  --scene maps/<id>/data/processed/geo/geo_scene.json \
  --out   maps/<id>/data/raw/catastro.gml

.venv/bin/python -m onward_generator cadastre match \
  --scene maps/<id>/data/processed/geo/geo_scene.json \
  --gml   maps/<id>/data/raw/catastro.gml \
  --out   maps/<id>/data/processed/cadastre

.venv/bin/python -m onward_generator props place \
  --base maps/<id>/data/processed/scene_dress \
  --scene maps/<id>/data/processed/geo/geo_scene.json \
  --out maps/<id>/data/processed/scene_props
```

`cadastre` is Spain only. It writes storey counts for `heights`, and it runs before `generate`. `props place` copies a scene and records parked vehicles that have OSM evidence. It does not bake those files into the GLB, and the Unity adapter does not place that list.

Inputs, outputs, and the rules for repeating one stage: [docs/01-pipeline.md](docs/01-pipeline.md).

## Command reference

Every command is `.venv/bin/python -m onward_generator <command>`. `version` prints `0.1.0`.

| Command | What it does | Exit |
| --- | --- | --- |
| `version` | Print the generator version. | `0` |
| `platform` | Print the host (`macos` or `linux`), where it came from, and the Unity, adb, log, and Asset Store cache paths. `--json` for machine output. The global `--platform` goes before any command. | `0`, `2` for an unknown platform |
| `doctor` | Read-only check of Python and the pinned versions, GEOS, PROJ, and EPSG:25830 offline, the configuration schema, free disk, and the host platform, and the export toolchain (Unity 2021.3.23f1, adb, and the SDK package). A missing toolchain file is a warning. An unknown platform fails `host:platform`. | `0` ok, `1` if something fails |
| `validate-config` | Strict read, JSON Schema, and semantic rules. Each finding names the field, cause, and code. See `docs/02-configuration.md`. | `0` valid, `1` invalid |
| `run` | Create a map and run every Python stage through `export package`, then Unity, then an optional Quest upload. See [Make a map](#make-a-map). | `0` / `1` |
| `map create` / `map show` | One folder per map: name, latitude, longitude, radius, and area of interest. The id and `maps/<id>` come from the name. `create` downloads that square's `.osm` and writes `config/texture_plan.json`. `--textures` on an existing map rewrites only that plan. `show` prints the `geo` command for the folder. | `0` / `1` |
| `osm download` | Repeat the Overpass request for one map folder. | `0` / `1` |
| `geo` | Geographic pipeline. The command is in [Stages by hand](#stages-by-hand). | `0` / `1` |
| `heights` | Building heights from `geo_scene.json`: `--scene`, `--out`, `[--config]`, `[--overrides]`, `[--overwrite]`. See `docs/04-heights.md`. | `0` / `1` |
| `cadastre download` | **Uses the network.** One Catastro WFS request (`BuildingPart`) for the scene bbox: `--scene`, `--out`, `[--margin-m]`, `[--overwrite]`. Spain only. | `0` / `1` |
| `cadastre match` | Catastro storeys to `heights_overrides.catastro.json` and a report: `--scene`, `--gml`, `--out`, `[--min-coverage]`, `[--overwrite]`. See `docs/05-cadastre.md`. | `0` / `1` |
| `generate` | Phase-03 meshes: `--scene`, `--heights`, `--out`, `[--config]`, `[--no-preview]`, `[--overwrite]`. See `docs/06-generation.md`. | `0` when the 03 → 04 check passes, `1` on a critical error |
| `detail` | Phase-04 detail on a validated base: `--scene`, `--heights`, `--base`, `--out`, `[--config]`, `[--generation-config]`, `[--no-preview]`, `[--overwrite]`. See `docs/07-detail.md`. | `0` when the automatic checks pass, `1` on a critical error |
| `dress classify` | Recommend catalogue materials (Ollama, rules, or replay) for a sample. Does not modify the scene. `--apply` is refused. See `docs/08-dress.md`. | `0` valid report, `1` rejection or provider failure |
| `dress style` | Copy the detail scene: 16-sided trees, benches, and an atlas. Does not rebuild facades. See `docs/08-dress.md`. | `0` / `1` |
| `props catalog` | Validate the Asset Store catalogue and, with `--inventory`, bind imported prefabs. | `0` / `1` |
| `props place` | Copy a scene and write instances that have OSM evidence (surface parking). Does not put cars on the carriageway. Does not bake FBX into the GLB. | `0` / `1` |
| `materials apply` | Copy a `dress` or `detail` scene and remap atlas tiles from that map's `config/texture_plan.json`. `--download` is the only network step, and it is for an older Poly Haven plan. A library plan reads files already in `config/dress/imported_library.json`. See `docs/01-pipeline.md`. | `0` / `1` |
| `panoramax apply` | Under development. With `map.json` `"panoramax": true`, project 360° street photos onto exposed facades and write `scene_panoramax`. Off, it writes nothing. Collision is copied byte for byte. Photos stay CC BY-SA 4.0. See `docs/01-pipeline.md`. | `0` / `1` |
| `signs place` | Copy the dressed scene and hang one plate, with the OSM `name`, on the facade at a junction. A street with no name gets no plate. Collision is unchanged. | `0` / `1` |
| `overgrow place` | Copy the scene and add moss, grass, vines, trees, wrecks, and the props in `--library`. `--profile` is 10, 30, or 100. `--seed` defaults to 48152. `--zone-radius` or `--zone-polygon` is in local metres. With no zone, the default is a 110 m block. The drive lane and the building footprints stay. `overgrow fit` then rejects a prop that is the wrong size or in the wrong place. | `0` / `1` |
| `overgrow library` / `fetch` / `fetch-sketchfab` | List the installed props and the Sketchfab licence review, download the named CC0 Poly Haven models, or download Sketchfab models the review already marked usable. | `0` / `1` |
| `bounds` | Copy a dressed scene and add a playable perimeter wall. Does not rewrite the phase-03 generate. | `0` / `1` |
| `perf measure` / `layout` / `device` / `drawcalls` | Phase 05. `measure` counts GLB contents offline. `layout` writes the measurement fixture; `--playable` writes gameplay layout 0.3.0 (Free Roam, Uplink, Assault, Escort, Hunt, Evac, Retrieval). `device` and `drawcalls` read headset logs. See `docs/09-perf.md`. | `0` / `1` |
| `export package` / `verify` | Phase 07. Write an export package for the Unity adapter (`--project` with `export.enabled`, `--detail`, `--layout`, `--out`, `[--config]`). Never overwrites. The build and the headset copy are `tools/unity/onward_adapter/run_export.sh`. See `docs/12-export.md`. | `0` / `1` |
| `ai plan` | Phase 06. Instruction to a validated configuration. `--instruction` or `--instruction-file`. `--provider ollama` (local, `--model`, default `qwen3:14b`), `none` (no model, `--intent FILE`), or `replay` (`--replay FILE`). `--out`, `[--confirm]`, `[--overwrite]`. See `docs/10-ai.md`. | `0` written, `1` rejected, `3` needs confirmation |

Usage errors return `2`.

## Textures and props

`map create` writes one texture plan, `maps/<id>/config/texture_plan.json`. It asks local Ollama to choose one tileable albedo per slot from `config/dress/imported_library.json` (imported ambientCG and Kenney files). The reply must be JSON naming ids that exist in that library. An invalid reply is retried once. If Ollama is down, the plan is a deterministic pick from the same library and the description records that fallback. There is no shared texture template. This rewrites only that file on a map that already exists:

```bash
.venv/bin/python -m onward_generator map create --name "Map name" --textures
```

List the catalogues overgrow can place:

```bash
.venv/bin/python -m onward_generator overgrow library list
```

`overgrow` reads every `manifest.json` in a direct child of `assets`. `assets/normalized` holds the 49 albedo GLBs pictured in [docs/abandoned-assets.md](docs/abandoned-assets.md) (CC0 and CC-BY 4.0; the licence review is `assets/normalized_review.json`). `assets/kenney` is the second catalogue (`assets/kenney/manifest.json`, review in `assets/kenney_review.json`). Each of those GLBs loads `Textures/<name>.png` from its own pack folder. A new folder under `assets` with its own `manifest.json` joins the same catalogue. `overgrow library list` and `overgrow library select` use that whole set. `overgrow place --library assets` does too. A `prop_selection.json` limits a run to the ids it names. Without that file, the whole catalogue is eligible, still under the placement caps.

## Tests

```bash
.venv/bin/python -m pytest
```

Tests do not use the network. The Catastro download is tested with a stand-in fetch. Fixtures live in `tests/fixtures/` and use one fixed origin, latitude 37.98407, longitude -1.12863.

## Data and licences

- OpenStreetMap data is ODbL-1.0. Derived maps keep the attribution `© OpenStreetMap contributors`.
- Catastro INSPIRE BU is CC-BY 4.0 and is used for Spain only.
- Raw extracts under `maps/*/data/` are gitignored. Panoramax JPEGs live in `cache/panoramax/` and are shared by every map; that directory is gitignored too. Do not commit credentials, private SDK files, or large source extracts.
- The prop and texture libraries record the licence of every imported file. See `docs/legal/data-sources.md` and `docs/abandoned-assets.md`.

## Documentation

Read `docs/01` through `docs/12` in order.

| | Document |
| --- | --- |
| 01 | `docs/01-pipeline.md` — stages, inputs, outputs, and the recipe for a new map |
| 02 | `docs/02-configuration.md` — configuration contract and diagnostic codes |
| 03 | `docs/03-geo-pipeline.md` — geo stages and the output contract |
| 04 | `docs/04-heights.md` — height estimation |
| 05 | `docs/05-cadastre.md` — Catastro storeys, Spain only |
| 06 | `docs/06-generation.md` — phase-03 geometry |
| 07 | `docs/07-detail.md` — facades, roofs, interiors, street elements |
| 08 | `docs/08-dress.md` — material classification, the dressed copy, and textures |
| 09 | `docs/09-perf.md` — Quest measurement |
| 10 | `docs/10-ai.md` — instruction interpretation |
| 11 | `docs/11-onward-sdk.md` — playable-map rules from the custom-content guide |
| 12 | `docs/12-export.md` — export package and the Unity adapter |

Look these up when you need them. They are not the next chapter:

- `docs/abandoned-assets.md`: the 49 props overgrow can place, with a picture of each.
- `docs/legal/data-sources.md`: licences, attribution, and ODbL obligations.
- `docs/decisions/`: why a choice was made. ADR-002 coordinates, ADR-003 geometry, ADR-004 facades, ADR-005 Quest performance, ADR-006 the AI intent, ADR-007 the export adapter, ADR-008 landmark towers, ADR-009 materials, ADR-010 overgrow gestures, ADR-011 the host platform.
- `tools/unity/phase0_room/`: phase-0 test room.
- `tools/unity/perf_harness/`: phase-05 measurement harness (`MurciaPerfBuilder.cs`, `run_perf.sh`).
- `tools/unity/onward_adapter/`: export adapter (`OnwardMapAdapter.cs`, `run_export.sh`).

## Layout

```text
config/
├── generator.schema.json              configuration contract (JSON Schema 2020-12)
├── murcia-belluga.example.json        example configuration, checked by the tests
├── murcia_belluga_aoi.geojson         polygon that example uses as its area of interest
├── heights.default.json               height-estimation assumptions
├── heights*.schema.json               height configuration and override schemas
├── generation.{default,schema}.json   phase-03 geometry assumptions
├── detail.{default,schema}.json       phase-04 templates, materials, interiors, budget
├── intent.schema.json                 the only document a model may produce (phase 06)
├── overgrow_plan.schema.json          placement plan: span, cover, watch, place
├── dress/                             material catalogue, imported texture library, texture-plan schema
├── props/                             Asset Store catalogue (not instantiated in the GLB)
├── export/                            Onward adapter configuration (phase 07)
└── perf/                              phase-05 measurement variants (v00, v01, v02)
assets/normalized/                     49 albedo GLBs and manifest.json
assets/normalized_review.json          licence review for that library
assets/textures/                       tileable albedo images (ambientCG, Kenney, Poly Haven cache)
assets/kenney/                         Kenney CC0 GLBs and manifest.json, without the building kit
assets/kenney_review.json              one card per Kenney mesh
assets/fonts/dejavu/                   DejaVu Sans Bold for street plates, with its licence
src/onward_generator/
├── cli.py, __main__.py                CLI
├── pipeline_run.py                    ordered run, stage timing, condensed log
├── next_step.py                       the next command each stage prints
├── doctor.py                          environment check
├── platform.py                        host platform: macOS or Linux paths
├── io.py                              shared atomic JSON writes
├── config/                            loader, validation, models, diagnostics
├── domain/models.py                   entities with no GIS dependency
├── maps/                              one folder per map: map.json, config, area of interest
├── osm/                               one Overpass download for that folder
├── geo/                               sources, osm import, projection, topology, clipping, attributes
├── heights/                           config, classify, estimator, pipeline
├── cadastre/                          WFS acquire, GML, match
├── generation/                        config, mesh, surfaces, scene, gltf, preview
├── detail/                            config, modules, facades, interiors, urban, scene
├── dress/                             classify (report) and style (dressed copy)
├── materials/                         per-map texture plan and atlas remap
├── props/                             catalogue and OSM-evidenced instances
├── signs/                             OSM street-name plates
├── overgrow/                          abandoned-city dressing, fit, and the prop library
├── bounds/                            perimeter wall on a copy
├── perf/                              measure, layout, device (phase 05)
├── export/                            package, info3, verify (phase 07)
└── ai/                                intent, local Ollama provider, planner (phase 06)
tests/
├── unit/
├── integration/
├── detail_fixture.py                  synthetic phase-04 block (CC0-1.0)
└── fixtures/geo/, fixtures/cadastre/  synthetic data (CC0-1.0)
maps/<id>/                             one generated map; raw and processed data are gitignored
```
