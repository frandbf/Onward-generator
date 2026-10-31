# onward-generator

Local tool that turns legal geographic data into 3D scenes for custom Onward maps on Meta Quest. The command is `onward-generator` (`python -m onward_generator`), version 0.1.0.

OpenStreetMap supplies the streets and building footprints, and works for any city. The generator extrudes footprints the extract actually contains. Heights come from OSM tags or a deterministic estimate; optional source plugins add better data where it exists: Overture Maps everywhere, Catastro and the IGN LiDAR model in Spain, IGN LiDAR HD in France. A later dressing pass adds facades, roofs, shops, materials, street names, and an optional abandoned-city layer. Unity 2021.3 and the Onward custom-content SDK build the headset package. A GLB is not a playable map. The headset checklist in `quest_acceptance.md` is still open, so `device_verified` stays false. See [docs/12-export.md](docs/12-export.md).

Documentation is in English. Working rules for changes are in [AGENTS.md](AGENTS.md). The operational contract is [docs/01-pipeline.md](docs/01-pipeline.md).

## Quick start

With Docker and [go-task](https://taskfile.dev), nothing is installed on the host except Unity:

```bash
task map -- --name "Map name" --lat 37.98407 --lon -1.12863 --radius-m 400
task unity
```

`task map` runs every Python stage in the container and stops after `export`. `task unity` runs on the host: it builds the newest export package with Unity, then asks before uploading to the Quest. To build another map, pass its name as given to `task map` (`task unity -- "Map name"`), its folder id (`map_name`) or a package path. The folder id is the name in lower case, without accents, with `_` between words. Unity must be [prepared once](#prepare-unity-once).

## Requirements

- macOS (tested on macOS 26.6.2, Apple M4 Pro) or Linux. The host is detected, or chosen with `--platform macos|linux` or `ONWARD_PLATFORM`; see [docs/01-pipeline.md](docs/01-pipeline.md#host-platform). The Linux Unity paths are the Unity Hub defaults and have not been checked with Unity on Linux.
- Docker and go-task, or Python 3.12 or newer (tested with 3.14.6). The pinned dependencies, all binary wheels, are in `requirements.lock`: shapely (GEOS), pyproj (PROJ, with the EPSG database offline), jsonschema, pillow, numpy, certifi and duckdb (Overture GeoParquet, ADR-021; the image also installs its httpfs extension).
- Optional: [Ollama](https://ollama.com) on loopback with a model already installed (tested with Ollama 0.33.2 and `qwen3:14b`), for AI plans, texture selection and overgrow plans. Without it, those steps use a deterministic fallback.
- Unity **2021.3.23f1** and the Onward Custom Content SDK v1.15, both outside this repository.

## Install

**Docker (recommended).** The image installs `requirements.lock`, plus the lint and mutation tools. The repository is mounted at `/work`, so outputs land in `maps/` and belong to your user. The host network is shared, so loopback Ollama works. The first `task` call builds the image `onward-generator:dev`; any command goes through the same wrapper:

```bash
tools/docker/run.sh onward-generator doctor
tools/docker/run.sh --build pytest -q
```

The wrapper rebuilds the image by itself when it is missing or when the Dockerfile, a lock file or `pyproject.toml` changed since it was built: the image carries the SHA-256 of those files (`tools/docker/inputs.sh`) as the label `org.onward-generator.inputs`. Before this, a lock file change (DuckDB, for one) kept the old image and the run failed with `No module named 'duckdb'`. `--build` still forces a rebuild. The wrapper also mounts `${XDG_CACHE_HOME:-~/.cache}/onward-generator`, so the texture tile cache (`docs/08-dress.md`) is kept between runs.

**Local virtual environment.**

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --only-binary=:all: -r requirements.lock
.venv/bin/python -m pip install --no-deps -e .
```

The editable install is required: the code finds `config/` and `assets/` relative to the repository. It installs `.venv/bin/onward-generator`.

Check the environment with `onward-generator doctor` (read-only; a missing Unity, `adb` or SDK is a warning) and `onward-generator validate-config config/murcia-belluga.example.json`. Exit `0` is success, `1` a failed check, `2` a usage error.

## Make a map

`--lat` and `--lon` are WGS84. A negative longitude uses the keyboard hyphen (`-3.65288`); a typographic minus is rejected.

```bash
onward-generator run \
  --name "Map name" \
  --lat 37.98407 \
  --lon -1.12863 \
  --radius-m 400 \
  --margin-m 30 \
  --lightmap-texels-per-m 10 \
  --brief "How the streets should feel."
```

- `--radius-m` is half the side of the square, in metres (50–1500, default 400). `--margin-m` defaults to 30. `--lightmap-texels-per-m` defaults to 10 and cannot exceed 40.
- `--core-radius-m` (default 150) is half the side of the playable core. Past it, buildings are plain scenic shells with no openings, interiors, signs or colliders, and the wall stands on the core edge, so a larger map stays near the Quest triangle budget. `0`, or a value at or above the radius, plays the whole map ([ADR-017](docs/decisions/ADR-017-playable-core.md)).
- **Source plugins.** `run` adds the stages of every plugin that covers the map centre and prints them on a `sources` line; `run --no-source <id>` switches one off (`--no-catastro` is `--no-source es.catastro`). `onward-generator source list` shows each plugin, its coverage and its licence. `config/sources.json` holds the only height ranking (`priority`), the only shop-evidence ranking (`evidence`) and the licence allowlist; a plugin whose licence is not on the list stops `run` before any request ([ADR-018](docs/decisions/ADR-018-source-plugins.md)).
- `--brief` is stored on the map and read by overgrow. `--ai` asks local Ollama for a placement plan; a rejected reply falls back to the rules. `--panoramax` (under development) projects street photos onto facades.
- `--verbose` prints each stage's full output. `--preview` also draws the diagnostic PNGs of `generate` and `detail`, which `run` skips by default. `--no-unity` stops after `export` and prints the Unity command for the host; `task map` always uses it.

`run` creates `maps/<id>/` from the name, downloads that square from Overpass, and runs:

`map` → `geo` → *source plugins* → `heights` → `generate` → `detail` → `dress` → `materials` → `signs` → `overgrow` → `fit` → `bounds` → `layout` → `export` → Unity → Quest

Each stage prints one line: number, name, duration and its key numbers. Breakdowns go on indented lines, and non-zero warnings on their own line, in colour on a terminal unless `NO_COLOR` is set. The last line gives the Python time and warning count, then the Unity steps.

```text
 [ 4/12] detail     3.6 s  28 458 tris  25 mat  128 nodes
         openings   narrow_window 571, balcony 201, balcony_door 201, window 109, ...
         ! warning 10 (urban_element_not_placed 10)
 [ 6/12] materials 15.5 s  applied 13  skipped 2
...
✓ Python 45.0 s  10 warnings
```

- **Resuming.** If a stage fails, the console prints `FAILED` and the stage to resume from. Run the same command again: finished stages are skipped. `--from <stage>` resumes at a named stage without asking. A second `run` with the same name and centre continues that folder; a different centre is refused. `--overwrite` replaces `map.json` only while `data/processed/` is empty.
- **Overpass.** A busy server is retried on its own: HTTP 429 and 5xx, a dropped connection and a timeout three times each, waiting 8, 16, 32 s and up to 90 s, and a runtime-error remark twice, 30 s apart. If the download still fails after the folder exists, `onward-generator osm download --map maps/<id>` tries again.
- **Packages.** An export package is never overwritten; the next one is `pkg_02`.
- **Unity.** If the Editor has `~/Unity/OnwardPhase0Project` open, `run` asks you to close it first. After the build it asks before uploading; only `s` or `si` uploads.
- **One slot.** The headset has one local slot. The card title is `Unity Export`; the name passed to `run` is the folder name and the title in `maps/<id>/config/export.json`. `ONWARD_QUEST_LISTING` tries other installs ([below](#map-name-and-thumbnail-on-the-headset-experimental)).

Each stage can also run by hand; the commands are in [docs/01-pipeline.md](docs/01-pipeline.md#recipe-for-a-new-map), and every command and flag is in [docs/commands.md](docs/commands.md). `props place` is optional and not called by `run`.

## Unity and the Quest

### Prepare Unity once

Put the SDK package at `~/Unity/OnwardSDK/OnwardCustomContent_SDK_v1.15.unitypackage`, then create the project, import the SDK, patch the launcher path and install the XR packages:

```bash
tools/unity/phase0_room/run_phase0.sh create import patch tool xr configure
```

The project is `~/Unity/OnwardPhase0Project`. Open it only with 2021.3.23f1: a newer editor rewrites it. Close the Editor before a map build.

### Build and upload

`task unity` picks the newest export package (of any map, or of the map named after `--`) and runs `tool scene launcher`, skipping steps already built. Each Unity step prints its wall time (`step 'scene' OK in 4 min 05 s`) and `✓ Unity` gives the total. Then it asks `¿Subir al Quest? [s/N]`; only `s`, `si`, `y` or `yes` runs `push`, which backs up the headset map and replaces it. Without `.venv`, its Python steps run in the image (`tools/docker/python.sh`). The same steps by hand:

```bash
tools/unity/onward_adapter/run_export.sh maps/<id>/data/processed/export/pkg_01 tool scene launcher
CONFIRM_PUSH=yes tools/unity/onward_adapter/run_export.sh maps/<id>/data/processed/export/pkg_01 push
```

The lightmap bake is most of the `scene` step. `task unity` bakes the `draft` profile by default: fewer samples and bounces, the same lightmap pages, noisier and darker bounced light, roughly 4–7 times faster to bake (estimated, not yet measured). Build a release map with `final`, the full bake:

```bash
ONWARD_BAKE=final task unity -- "Map name"     # or: task unity -- --bake final "Map name"
```

An unknown profile stops with exit 2. The scene step runs once per package, so a package baked as `draft` needs a new `task map` to bake as `final`. Details and sources: [docs/12-export.md](docs/12-export.md#bake-profiles).

### Map name and thumbnail on the headset (experimental)

On the Quest the local map is listed as `Unity Export` with a generic picture. One switch tries other installs that might show the map's own title and its 512×256 thumbnail. It is off by default: without it, `task unity` does exactly what it did before.

```bash
ONWARD_QUEST_LISTING=named task unity          # or: task unity -- --listing named
```

| Mode | What `push` installs |
| --- | --- |
| `local` (default) | Today's install: `unity-export.local.*`, card `Unity Export`, Hunt verified |
| `local-thumb` | The same slot and name; the `.info3` points `ThumbnailUrl` at the copied PNG (`file://…/unity-export.local.png`) |
| `named` | A second map `onwardgen.<map id>.*` beside the local one, with the map's title as its name. The local slot is left as it is |
| `named-thumb` | `named` plus `ThumbnailUrl` pointing at its PNG |

An unknown mode stops with exit 2 and the list. Every mode backs up the headset files first, never deletes one, and still asks before uploading. None of them has been verified on the headset; what to check is in [docs/12-export.md](docs/12-export.md#quest-listing-modes-experimental).

The editor log is `~/Library/Logs/onward-export/` on macOS and `~/.local/state/onward-generator/onward-export/` on Linux. Running Unity itself in a container (GameCI `unityci/editor`) is not set up yet: it needs a Unity licence file and the SDK project on the machine.

## What a map contains

- **Textures.** `map create` writes one texture plan, `maps/<id>/config/texture_plan.json`, choosing one tileable albedo per slot from `config/dress/imported_library.json` with local Ollama, or a deterministic pick without it. Only calm images join the asphalt, concrete, plaster, brick and rock pools, so no stain or baked-in manhole cover repeats on every tile, and asphalt repeats every 10 m. A saved plan that still names a busy image is refused until it is rewritten. `map create --name "Map name" --textures` rewrites only that plan; then run `materials apply` again. See [docs/08-dress.md](docs/08-dress.md).
- **Props.** `overgrow` reads every `manifest.json` in a direct child of `assets/` (`assets/normalized`, `assets/kenney`). Materials are opaque albedo only (`PP_Opaque_Simple_Lit`), so meshes with a non-opaque material and tall "flat" vegetation are refused and listed under `library_rejects`. Up to six manhole covers are placed on sidewalks. `overgrow library list` shows the catalogue; see [docs/abandoned-assets.md](docs/abandoned-assets.md).
- **Buildings.** In Spain a block footprint is split into one building per Catastro construction, and a parcel keeps one look with flat terrace roofs on its setbacks ([ADR-016](docs/decisions/ADR-016-catastro-split.md)). Walls shared with a neighbour get no openings. OSM Simple 3D Buildings tags are read on any map: `roof:shape` (gabled, hipped, half-hipped, skillion, pyramidal, dome, with height, angle and direction), `building:part` ways with their own heights and roofs, and `building:colour` / `roof:colour` as tints of the shared materials ([ADR-023](docs/decisions/ADR-023-simple-3d-buildings.md)).
- **Shops.** A building is a shop from the first source in `config/sources.json` `evidence` that has a reason: OSM shop, amenity, office and craft points, then the Catastro current use of the parcel (office or retail, only where a shop fits, [ADR-022](docs/decisions/ADR-022-catastro-use.md)), then Overture places with an open licence ([ADR-021](docs/decisions/ADR-021-overture-evidence.md)).
- **Street plates and shop signs.** Drawn with the vendored DejaVu Sans Bold, so accented names render. Shops get a larger display window, a glazed door, a fascia or blade sign with the OSM name (or a generic Spanish word, marked estimated), and vinyl lettering on some windows. Each shop has a one-storey textured room behind frameless glass with no collider, so it is seen from both sides and does not stop bullets; the shop name is lettered in black on the glass. The room is coloured by kind (bakery, pharmacy, bar, other) and has its own light ([ADR-019](docs/decisions/ADR-019-shop-rooms.md)). See [docs/07-detail.md](docs/07-detail.md).
- **Lighting and horizon.** One baked point light per furnished room (at most 24), light probes in rooms and streets, props lit by probes, linear fog near the perimeter wall, and an optional haze ring (`bounds --backdrop`). See [ADR-013](docs/decisions/ADR-013-interior-light-anchors.md) and [ADR-014](docs/decisions/ADR-014-horizon.md). Not yet checked in Unity or on the headset.

## Development

The checks have the same names locally and in CI, which runs on Linux:

| Task | What it does |
| --- | --- |
| `task --list` | All tasks. |
| `task ci` | Everything CI runs: shellcheck, ruff, mypy, actionlint, smoke, tests. |
| `task test -- -k cadastre` | The tests, with pytest arguments after `--`. |
| `task lint -- --fix` | ruff; `--fix` applies the safe fixes. |
| `task typecheck` | mypy on `src`; only errors missing from `mypy-baseline.txt` fail. |
| `task mutate` | Mutation testing of the lines changed since `main`. |
| `task guard` | Fails when a change edits both `src/` and a quality gate. |

Command lines in this README carry no trailing comments, so they paste as they are.

A pull request is ready when `task ci` and `task mutate` pass. The rules are in the *Quality gates* section of [AGENTS.md](AGENTS.md):

- mypy fails only on errors missing from `mypy-baseline.txt`. That list may only shrink: after fixing a listed error, run `task typecheck:baseline` and commit the shorter file.
- Every suppression names its code and a reason: `# noqa: CODE - reason`, `# type: ignore[code]`, `# pragma: no mutate: reason`.
- `task guard` fails a pull request that edits both `src/` and a gate (lint, type or mutation settings, the Taskfile, workflows, hooks, the lint and mutate locks, new baseline entries).
- `git config core.hooksPath .githooks` runs lint, typecheck and the tests before each push.

**Mutation testing.** `task mutate` changes each line a branch touched in `src/onward_generator`, such as `<` to `<=` or a constant moved by one, with [mutmut](https://mutmut.readthedocs.io) 3.8.1, and checks that some test fails. It rewrites only the files with a changed function and runs only the mutants on changed lines.

- Exit `0`: every mutant was killed. Exit `1`: a survivor, printed with its diff. Write a test that fails on that edit, or, when the edit changes nothing a caller can see, end the line with `# pragma: no mutate: <reason>`. Exit `2`: the tool could not run.
- `BASE=` sets the base (default `main`), `JOBS=` the workers (default all cores in CI, half locally). `task mutate -- --whole-functions` mutates whole functions.
- On a pull request, CI runs it against the target branch and skips it when `src/` did not change.

Tests do not use the network. Fixtures live in `tests/fixtures/` and use one origin, latitude 37.98407, longitude −1.12863.

## Data and licences

- OpenStreetMap data is ODbL-1.0. Derived maps keep the attribution `© OpenStreetMap contributors`.
- Catastro INSPIRE BU is CC-BY 4.0 and is used for Spain only.
- Source plugins: the IGN MDSn (Spain) is CC BY 4.0; IGN LiDAR HD (France) is Licence Ouverte 2.0 (`etalab-2.0`); Overture places are CDLA-Permissive-2.0, Apache-2.0 or CC0-1.0 per record, and a place with another licence is dropped.
- Generated maps (`maps/`, including each map's raw extract) and Panoramax photos in `cache/panoramax/` are gitignored. Do not commit credentials, private SDK files, or large source extracts.
- Every imported texture, prop and font records its licence. See [docs/legal/data-sources.md](docs/legal/data-sources.md).

## Documentation

Read [docs/01](docs/01-pipeline.md) through [docs/12](docs/12-export.md) in order: pipeline, configuration, geo, heights, Catastro, generation, detail, dress and textures, Quest performance, AI, Onward SDK rules, export. Look these up when you need them:

- [docs/commands.md](docs/commands.md): every command, its flags and exit codes.
- [docs/decisions/](docs/decisions/): architecture decision records, one per choice.
- [docs/abandoned-assets.md](docs/abandoned-assets.md): the props overgrow can place, with a picture of each.
- [docs/legal/data-sources.md](docs/legal/data-sources.md): licences, attribution and ODbL obligations.
- [docs/plans/](docs/plans/): measured studies and work plans (in Spanish), such as the [speed and scale plan](docs/plans/2026-10-30-scale-and-speed.md).
  - [Realism sources](docs/plans/2026-10-30-realism-sources.md): global data sources (Overture, OSM 3D tags, canopy height), per-country plugins (LiDAR, LoD2) and the top 8 realism opportunities.

## Layout

```text
src/onward_generator/     the generator, one package per stage
  maps/ osm/              map folder and Overpass download
  geo/ heights/ cadastre/ geographic scene, building heights, Catastro storeys
  sources/ overture/      source plugins (es.*, fr.*), their registry and merge; Overture fetch and match
  generation/ detail/     base meshes; facades, roofs, interiors, street elements
  dress/ materials/       dressed copy; per-map texture plan
  panoramax/ signs/       street photos; street-name plates and shop signs
  overgrow/ props/        abandoned-city layer and prop library; Asset Store instances
  bounds/ perf/ export/   perimeter wall; layout and measurement; export package
  ai/                     local Ollama intent and planner
  cli.py pipeline_run.py run_log.py   commands, the ordered run, its console summary
  io.py net.py            hashing and atomic writes; HTTP with retries and size caps
config/                   schemas and defaults for every stage, export and perf settings
assets/                   prop catalogues (normalized, kenney), textures, fonts, licence reviews
docs/                     numbered guides, command reference, decisions, licences
tests/                    unit/, integration/, synthetic fixtures (CC0-1.0)
tools/
  docker/                 run.sh and python.sh: commands in the image
  unity/                  phase-0 room, perf harness, export adapter (C#, run_export.sh)
  mutate/ quality/        mutation testing; the gate guard
maps/<id>/                one generated map; the whole maps/ tree is gitignored
Taskfile.yml              every task, locally and in CI
Dockerfile compose.yaml   the development image
requirements*.lock        pinned runtime, lint and mutation dependencies
mypy-baseline.txt         type errors that predate mypy; may only shrink
.github/ .githooks/       CI workflow, Dependabot, PR template; opt-in pre-push hook
```
