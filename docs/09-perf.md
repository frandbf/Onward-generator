# Quest performance measurement (phase 05)

How the generated scene is measured on a real headset, and which tools do it. The decision is `docs/decisions/ADR-005-quest-performance.md`. The export adapter applies the measured 100 m combine. See `docs/12-export.md`.

**Rule:** an optimisation is not validated by Blender or by the Unity Editor. Editor figures (`unity_stats.json`) describe the saved scene. Headset figures (OVR Metrics Tool and `ovrgpuprofiler`) describe what is drawn.

## What was measured

On a Quest 3, the phase-04 scene before combining (v00) drew 578 calls in view A and 171 in view B. Combining visual meshes by a 100 m cell and material dropped that to 96 and 67. Frame time stayed at 72 fps on the measured route. Triangle count in Unity, including SDK prefabs, was 247 627, on the guide's limit of about 250 000. Static batching and occlusion culling (v01) did not reduce draw calls (571 against 578 in view A) and stay off.

Those numbers are one scene, one route, one player, no bots. A match with players adds draws and triangles. They are not a budget for every future map.

## Technical sources

- *Onward Custom Content Guide 2.0* (SDK 15), Downpour Interactive. It is not copied into this repository.
  - p. 67, rough Quest guidelines: about 150 draw calls, about 250 000 triangles, about 1 GB of textures. They include players and objectives. The guide states them for the Quest 2 platform.
  - Appendix C: OVR Metrics Tool, fewer draw calls by fewer shaders, mesh combining, and culling or LOD, ASTC, `PP_` shaders, avoid transparency. Onward uses Vulkan and the SRP Batcher.
  - Lightmapping is required.
- OVR Metrics Tool (Meta, installed from the Store on the headset): a CSV at one sample per second.
- `ovrgpuprofiler`, on the headset at `/system_ext/bin/ovrgpuprofiler`: live GPU counters, per-stage render traces, and draw-call traces.

## Tools in this repository

| Piece | What it does |
| --- | --- |
| `onward-generator perf measure DIRS [--combine-cell-m …] --out` | Counts nodes, meshes, primitives, triangles, vertices, materials, textures, and lights in `scene_visual.glb` and `scene_collision.glb`, and estimates texture memory (`texture_memory`: every embedded image with mips as RGBA32 and as ASTC 6×6). With `--combine-cell-m`, predicts draw calls if primitives were merged by (cell, material). Offline |
| `onward-generator perf layout --scene --heights --base --detail --out` | Without `--playable`: the measurement fixture (`perf_layout` 0.1.0). Spawns, Uplink, map quad, and tent. With `--playable`: `gameplay_layout` 0.3.0, the layout of a new map. See [01-pipeline.md](01-pipeline.md) |
| `onward-generator perf device --csv [--logcat] --label --time-column "Time Stamp" --skip-first-s --until-s [--note] --out` | Per-column statistics (n, mean, min, p05, p50, p95, max) of an OVR Metrics CSV inside a time window, plus load times from `logcat` |
| `onward-generator perf drawcalls LABEL=PATH… --out` | Counts draw calls of each command buffer in `ovrgpuprofiler -t -x=…` traces |
| `tools/unity/perf_harness/Editor/MurciaPerfBuilder.cs` | Editor tool copied into the phase-0 project. Loads the generator's GLB subset, builds a scene with the SDK prefabs, bakes lighting, and calls the Onward Launcher. Writes `unity_stats.json` |
| `tools/unity/perf_harness/run_perf.sh` | Steps `tool`, `scene`, `launcher`, `push`, `session-start`, `record`, `drawcalls`, `session-end` |
| `config/perf/*.json` | One variant per file: static batching, occlusion, lightmap, and `combine_cell_m` |

`MurciaPerfBuilder` is a measurement tool. It is not the export adapter. It does not modify phase-03 or phase-04 outputs.

## Headset protocol

- Quest 3 over USB, with debugging on.
- OVR Metrics Tool installed.
- Onward closed at the start. Metrics are recorded per application launch.

The SDK always writes the local map as `unity-export.local.*`, titled `Unity Export`. `push` backs up whatever is on the headset before replacing it and checks the md5 on the device.

Measure with the default listing mode (`local`). The experimental modes for the map's name and thumbnail, and their headset test, are in [12-export.md](12-export.md#quest-listing-modes-experimental).

### Route session (frame time)

1. Launch Onward, open `Unity Export`, Free Roam.
2. Leave the tent and stand at the spawn.
3. Route: 30 s still, a slow 360° turn, and about 2 minutes of walking.
4. Leave the map.

**Analysis window:** from 10 s after the load-finished marker, for 150 s. The same window is used for every variant.

- The marker time is converted to seconds from the first CSV row.
- The `Time Stamp` column is milliseconds from the start of the capture.
- The first row has `Time Stamp` = 1000.
- The start time is in the file name.

### Draw-call session

`DRAWCALLS=1 … session-start` enables the driver's detailed profiling mode for Onward (`ovrgpuprofiler -e`). The mode must be on before Onward launches.

Two fixed views from the spawn:

- **A:** looking at the Uplink.
- **B:** turned 180°.

`drawcalls` runs `ovrgpuprofiler -t -x="1,21"`, which reports cycles and shaded vertices per draw. `ovrgpuprofiler -t` reports the per-stage trace: surface size, bins, and milliseconds.

The detailed mode adds an overhead that was not measured. **Frame times from these sessions are not used as frame times.** Only draw calls and vertices count.

### Load markers

In `logcat`:

- start: `Loading level: CustomContentLoader`;
- end: `SendSetIsLoadingMap: False`, emitted from `GameManager:Start()`. The player is in the tent just after that.

`logcat` is captured live. A dump at the end loses Onward lines when the buffer rotates.

## Playable core

ADR-017. Madrid, 40.40657, −3.719187, with Catastro, on fork/main of 30 October 2026, `run --no-unity`. Triangles and draws are read from the exported `scene_visual.glb`: draws are the (100 m cell, material) groups left after the adapter's combine, with no culling (`perf measure --combine-cell-m 100`), so they are an upper bound for a view, not a view. The lightmap is the export estimate at 10 texels/m (a lower bound on pages). The node spacing is the `layout.json` estimate of the adapter's first spacing. None of this is a headset measurement.

| Map | `detail` visual (core / scenic) | Export visual: core / scenic / shared | Collision | Draws: total (core / scenic / shared) | Lightmap pages | Node spacing |
| --- | --- | --- | --- | --- | --- | --- |
| r200, no core | 217 845 | 263 597 | 52 464 | 439 | 10 | 20.7 m |
| r200, core 150 | 143 342 (127 301 / 3 720) | 175 139 / 3 761 / 5 574 = 184 474 | 31 060 | 328 (259 / 65 / 12) | 7 | 13.3 m |
| r300, no core | 511 195 | 564 731 | 139 200 | 827 | 23 | 28.1 m |
| r300, core 150 | 166 694 (127 033 / 19 641) | 174 373 / 19 725 / 14 564 = 208 662 | 30 796 | 404 (261 / 140 / 12) | 9 | 13.3 m |
| r300, core 120 | 127 277 (87 308 / 21 377) | 131 404 / 21 437 / 14 764 = 167 605 | 21 868 | 377 (228 / 145 / 14) | 7 | 10.8 m |

- The core costs the same whatever the radius: 108 core buildings and about 175 000 exported triangles at r200 and r300. The export adds the overgrow props (33 762 triangles), the shop signs and the street plates to the `detail` count, all in the core.
- 446 scenic buildings at r300 cost 19 641 triangles, 44 each: four walls and a roof, against about 1 180 for a detailed building.
- A ring cell is about four draws (three scenic walls and one roof). The ring adds 140 uncullled draws at r300, the core about 260. Before the street plates were kept to the core the ring cost 230: each plate was then a material of its own. Since 3 November 2026 they share `street_sign_atlas_<page>`.
- With a 150 m core the map is about 209 000 triangles before the SDK prefabs and the players, against the guide's 250 000 for all of it (ADR-005). A 120 m core leaves about 82 000. Which margin a match needs is a headset question.
- The scenic shells bake at scale 0.25: 3.4 M texels of the 34.7 M at r300.

## Known limits of the measurement

- Free Roam, one player, no bots. The official guide counts players and objectives inside its budget.
- One walking route, repeated by hand. Between two passes of the same build, GPU time p50/p95 varied by 9% and PSS by 183 MB.
- Draw calls were measured in two views, not at the worst view of the whole map.
- `ovrgpuprofiler -r` has no timestamp. It is aligned to the first `logcat` line (±2 s), so it is a relative signal. In one session the fragment counter returned −1.
- On that headset, OVR Metrics returned 0 for `main_thread_cpu_time`, `render_thread_cpu_time`, and `avg_vertices_per_frame`.
