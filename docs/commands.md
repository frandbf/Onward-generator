# Command reference

Every command is `onward-generator <command>`, the same as `python -m onward_generator <command>`. In Docker it is `tools/docker/run.sh onward-generator <command>`. `version` prints `0.1.0`. The order the stages run in, and each one's inputs and outputs, are in [01-pipeline.md](01-pipeline.md).

| Command | What it does | Exit |
| --- | --- | --- |
| `version` | Print the generator version. | `0` |
| `platform` | Print the host (`macos` or `linux`), where it came from, and the Unity, adb, log, and Asset Store cache paths. `--json` for machine output. The global `--platform` goes before any command. | `0`, `2` for an unknown platform |
| `doctor` | Read-only check of Python and the pinned versions, GEOS, PROJ, and EPSG:25830 offline, the configuration schema, free disk, and the host platform, and the export toolchain (Unity 2021.3.23f1, adb, and the SDK package). A missing toolchain file is a warning. An unknown platform fails `host:platform`. | `0` ok, `1` if something fails |
| `validate-config` | Strict read, JSON Schema, and semantic rules. Each finding names the field, cause, and code. See `02-configuration.md`. | `0` valid, `1` invalid |
| `run` | Create a map and run every Python stage through `export package`, then Unity, then an optional Quest upload. See the README, *Make a map*. | `0` / `1` |
| `map create` / `map show` | One folder per map: name, latitude, longitude, radius, and area of interest. The id and `maps/<id>` come from the name. `create` downloads that square's `.osm` and writes `config/texture_plan.json`. `--textures` on an existing map rewrites only that plan. `show` prints the `geo` command for the folder. | `0` / `1` |
| `osm download` | Repeat the Overpass request for one map folder. | `0` / `1` |
| `geo` | Geographic pipeline. The command is in [docs/01-pipeline.md](01-pipeline.md#recipe-for-a-new-map). | `0` / `1` |
| `heights` | Building heights from `geo_scene.json`: `--scene`, `--out`, `[--config]`, `[--overrides]`, `[--overwrite]`. See `04-heights.md`. | `0` / `1` |
| `cadastre download` | **Uses the network.** One Catastro WFS request (`BuildingPart`) for the scene bbox: `--scene`, `--out`, `[--margin-m]`, `[--overwrite]`. Spain only. | `0` / `1` |
| `cadastre match` | Catastro storeys to `heights_overrides.catastro.json` and a report: `--scene`, `--gml`, `--out`, `[--min-coverage]`, `[--overwrite]`. See `05-cadastre.md`. | `0` / `1` |
| `generate` | Base meshes: `--scene`, `--heights`, `--out`, `[--config]`, `[--no-preview]`, `[--overwrite]`. See `06-generation.md`. | `0` when the 03 → 04 check passes, `1` on a critical error |
| `detail` | Facade, roof and interior detail on a validated base: `--scene`, `--heights`, `--base`, `--out`, `[--config]`, `[--generation-config]`, `[--no-preview]`, `[--overwrite]`. See `07-detail.md`. | `0` when the automatic checks pass, `1` on a critical error |
| `dress classify` | Recommend catalogue materials (Ollama, rules, or replay) for a sample. Does not modify the scene. `--apply` is refused. See `08-dress.md`. | `0` valid report, `1` rejection or provider failure |
| `dress style` | Copy the detail scene: 16-sided trees, benches, and an atlas. Does not rebuild facades. See `08-dress.md`. | `0` / `1` |
| `props catalog` | Validate the Asset Store catalogue and, with `--inventory`, bind imported prefabs. | `0` / `1` |
| `props place` | Copy a scene and write instances that have OSM evidence (surface parking). Does not put cars on the carriageway. Does not bake FBX into the GLB. | `0` / `1` |
| `materials apply` | Copy a `dress` or `detail` scene and remap atlas tiles from that map's `config/texture_plan.json`. `--download` is the only network step, and it is for an older Poly Haven plan. A library plan reads files already in `config/dress/imported_library.json`. See `01-pipeline.md`. | `0` / `1` |
| `panoramax apply` | Under development. With `map.json` `"panoramax": true`, project 360° street photos onto exposed facades and write `scene_panoramax`. Off, it writes nothing. Collision is copied byte for byte. Photos stay CC BY-SA 4.0. See `01-pipeline.md`. | `0` / `1` |
| `signs place` | Copy the dressed scene and hang one plate, with the OSM `name`, on the facade at a junction. A street with no name gets no plate. Collision is unchanged. | `0` / `1` |
| `overgrow place` | Copy the scene and add grass, vines, trees, wrecks, and the props in `--library`. `--profile` is 10, 30, or 100. `--seed` defaults to 48152. `--zone-radius` or `--zone-polygon` is in local metres. With no zone, the default is a 110 m block. The drive lane and the building footprints stay. `overgrow fit` then rejects a prop that is the wrong size or in the wrong place. | `0` / `1` |
| `overgrow library` / `fetch` / `fetch-sketchfab` | List the installed props and the Sketchfab licence review, download the named CC0 Poly Haven models, or download Sketchfab models the review already marked usable. | `0` / `1` |
| `bounds` | Copy a dressed scene and add a playable perimeter wall. Does not rewrite the `generate` output. | `0` / `1` |
| `perf measure` / `layout` / `device` / `drawcalls` | `measure` counts GLB contents offline. `layout` writes the measurement fixture; `--playable` writes gameplay layout 0.3.0 (Free Roam, Uplink, Assault, Escort, Hunt, Evac, Retrieval). `device` and `drawcalls` read headset logs. See `09-perf.md`. | `0` / `1` |
| `export package` / `verify` | Write an export package for the Unity adapter (`--project` with `export.enabled`, `--detail`, `--layout`, `--out`, `[--config]`). Never overwrites. The build and the headset copy are `tools/unity/onward_adapter/run_export.sh`. See `12-export.md`. | `0` / `1` |
| `ai plan` | Instruction to a validated configuration. `--instruction` or `--instruction-file`. `--provider ollama` (local, `--model`, default `qwen3:14b`), `none` (no model, `--intent FILE`), or `replay` (`--replay FILE`). `--out`, `[--confirm]`, `[--overwrite]`. See `10-ai.md`. | `0` written, `1` rejected, `3` needs confirmation |

Usage errors return `2`. `run --verbose` prints each stage's full output; `run --no-unity` stops after `export` and prints the `task unity` command for the host.
