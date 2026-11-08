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
| `onward-generator perf layout … --playable --gameplay-report` | Also writes `gameplay_report.json` beside `--out`: routes between the spawns, overlap, longest open sightline, cover per 25 m stretch and the teams' times to the Uplink, estimated on the 2D navmesh. A report only, off by default ([ADR-032](decisions/ADR-032-gameplay-metrics.md), [Gameplay metrics](#gameplay-metrics)) |
| `onward-generator perf device --csv [--logcat] --label --time-column "Time Stamp" --skip-first-s --until-s [--note] --out` | Per-column statistics (n, mean, min, p05, p50, p95, max) of an OVR Metrics CSV inside a time window, plus load times from `logcat` |
| `onward-generator perf drawcalls LABEL=PATH… --out` | Counts draw calls of each command buffer in `ovrgpuprofiler -t -x=…` traces |
| `tools/unity/perf_harness/Editor/MurciaPerfBuilder.cs` | Editor tool copied into the phase-0 project. Loads the generator's GLB subset, builds a scene with the SDK prefabs, bakes lighting, and calls the Onward Launcher. Writes `unity_stats.json` |
| `tools/unity/perf_harness/run_perf.sh` | Steps `tool`, `scene`, `launcher`, `push`, `session-start`, `record`, `drawcalls`, `session-end` |
| `config/perf/*.json` | One variant per file: static batching, occlusion, lightmap, and `combine_cell_m` |

`MurciaPerfBuilder` is a measurement tool. It is not the export adapter. It does not modify phase-03 or phase-04 outputs.

`task bench` ([01-pipeline.md](01-pipeline.md#benchmark)) records the predicted triangles, draws and lightmap pages and the Python and Unity Editor seconds of four fixed reference maps. Those are generator and Editor figures, not device measurements. The headset figures still come from the protocol below.

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

## Budget core

ADR-017 amendment of 7 November 2026 (plan B1). `detail --core-radius-m M --core-target-triangles N` on the frozen inputs of the bench run `output/bench/runs/20261107T134307Z` (fix/overrides-size, a57265a), written to a scratch folder, one run per target, Docker on an Apple M4 Pro. The r500 row is a scratch map, `maps/b1_madrid_r500` (same centre as bench_madrid, `--source es.catastro`), made with `run --no-unity --core-target-triangles 250000`; its Catastro download took 15 min 55 s and succeeded the first time. "Achieved" is `counts.tiers.core.visual_triangles` of the kept run. The times are the whole `detail` command, probe and kept run. Python and GLB figures, not a headset measurement.

| Map | M | N | Half-side | Predicted | Achieved | Off | Issue | `detail` s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| bench_murcia r150 | 100 | 50 000 | 79.3 | 49 863 | 50 203 | +0.4 % | | 6.3 |
| bench_murcia r150 | 100 | 74 590 | 99.9 | 74 590 | 74 590 | 0 | | 6.2 |
| bench_murcia r150 | 100 | 100 000 | 100 | 74 590 | 74 590 | −25 % | `core_budget_ceiling` | 3.3 |
| bench_madrid r300 | 250 | 120 000 | 127.2 | 119 975 | 120 287 | +0.2 % | | 35.8 |
| bench_madrid r300 | 250 | 179 479 | 150.1 | 179 371 | 179 693 | +0.1 % | | 34.8 |
| bench_madrid r300 | 250 | 250 000 | 176.2 | 249 987 | 250 405 | +0.2 % | | 34.9 |
| B1 Madrid r500, Catastro (`run --no-unity`, a new map) | 450 | 250 000 | 176.2 | 250 057 | 250 405 | +0.2 % | | 98 |
- 74 590 and 179 479 are the core triangles of the fixed 100 m and 150 m cores in that bench run; the budget core lands on 99.9 m and 150.1 m.
- At r500 the probe details nearly all of the 5 817 buildings, those within 450 m, so `detail` takes 1 min 38 s; the kept core is the same 176.2 m as at r300 for the same target, as it should be around one centre.
- The r500 run went on through `export`: `bounds` put the wall on the solved 176.24 m core, and the package has 360 717 core triangles (1.44 times `detail`'s), 344 458 scenic and 53 248 shared, 764 draws at 100 m, `view_draws` 260 / 509 / 605. The ring of a 500 m map costs as much as its core: the target does not bound it.
- The fixed-core `detail` took 3.1 s (Murcia) and 11.0 s (Madrid) in the bench. The budget mode runs it twice, the first time at the largest core, so Madrid's `detail` goes from 11 s to about 35 s.
- What N does not count: in that bench run the exported core is 125 052 triangles for 74 590 in `detail` on Murcia (1.68), and 270 361 for 179 479 on Madrid (1.51); the scenic ring adds 18 448 and 108 602. Dress, overgrow, signs, the ground and the SDK prefabs make the difference. To aim at the plan's 220 000 in Unity, N is about (220 000 − ring) / ratio; for Madrid r300, about 74 000.
- Synthetic grids (`tests/integration/test_core_budget.py`, half the triangles of the largest core as N): r150 +2.0 %, r300 −1.3 %, r500 +1.5 %. The first model, one interior reserve for the whole map, missed r150 by 33 %: buildings across the edge lose their interiors.

Pending on the headset (A2), not measured: Unity `scene_report.json` triangles against N, draws in the fixed views A and B (at most 150), median fps on the route (at least 72), and how the ring reads through the fog, which still starts at `core_radius_m`, not at the solved wall.

## Shop furnishing

Plan B3, [ADR-031](decisions/ADR-031-shop-furnishing.md), off by default (`interiors.shops.furnish.enabled`). Package figures with the option on, on copies of the bench run `20261107T170959Z` (Docker, Apple M4 Pro): visual triangles 150 339 → 161 793 (bench_murcia), 398 025 → 409 847 (bench_madrid), 72 884 → 78 564 (bench_krakow); draws 213 → 247, 504 → 547, 272 → 293; view draws p90 181 → 211, 374 → 412, 219 → 238; lightmap pages unchanged. Generator figures, not device measurements.

Pending on the headset (not done; the option stays off until they pass):

- [ ] A `bench_murcia` package built with furnishing on: the furniture inside the shop rooms is lit by the room light and probes (or the combined cell's ambient light), not black (`prop_` materials take no lightmap; `docs/12-export.md`).
- [ ] Walk in through each kind of shop door: the 1.2 m clear path holds, and no piece blocks the door or traps the player.
- [ ] Cover-sized furniture stops bullets and the player; decor does not; nothing is an invisible wall.
- [ ] OVR Metrics in fixed views A and B looking into a furnished shop row: draws added against the 150 target, a median of at least 72 fps, graphics memory.
- [ ] From the street, through the `shop_glass`, the furniture reads at scale and faces into the room (Kenney scale and facing are estimated, never reviewed from a rendered view).

Then ADR-031 goes from proposed to accepted or is corrected. Turning furnishing on by default is a separate decision (plan 4.2).

## Panoramax with the trim atlas

Plan B2, ADR-020 amendment of 7 November 2026, off by default (`map.json` `"panoramax_atlas": true`, with `"panoramax": true`). With the switch off, `task bench -- --offline` (run `20261107T201937Z`, no Panoramax) is the same as run `20261107T195936Z` apart from timings (`bench.py check`), and the Panoramax stage of the synthetic Murcia block writes the same five files, by SHA-256, as before the change.

Measured on copies of the bench inputs in `output/b2_measure/` (never `maps/`), with network, one run with the switch off (per-face decals) and one with it on, Docker on an Apple M4 Pro. The searches succeeded; no download failed, so no retry was needed:

| Copy | 360 pictures kept | Exposed frontage | With a photo | Decal draws added | Package draws | View draws median / p90 / max | Panoramax stage, off / on |
| --- | --- | --- | --- | --- | --- | --- | --- |
| bench_murcia r150 | 0 (2 flat skipped) | 6 179.8 m | 0 m, 0.0 % | 0 | 213 → 213 | 85 / 181 / 213 | 16.7 s / 15.6 s |
| bench_madrid r300 | 0 (7 flat skipped) | 22 328.7 m | 0 m, 0.0 % | 0 | 504 → 504 | 175 / 374 / 448 | 5 min 58 s / 5 min 18 s |
| bench_paris r50, `facades.style` mediterranean | 65 | 52.2 m (2 OSM buildings) | 0 m, 0.0 % | 0 | — (no core) | 33 / 74 / 87 | 2 min 11 s (downloads) / 9.7 s |

None of the three squares has a 360 picture in front of an exposed facade, so the per-cell figures on a real city (decals per cell, image side, scale, tints) are still open: a blocker for that part of the measure, not for the code. On the synthetic block (`tests/integration/test_panoramax_pipeline.py`) one photographed building gives one cell, one decal, one added draw, a 512 px cell image at scale 1, and a tint from `stone_warm` tone 1 to tone 0 for a pale photo. Generator figures, not device measurements.

Pending (not done; the switch stays off until they pass):

- [ ] Build a map with Panoramax photos in front of trim facades (a square with 360 coverage) and the switch on; record coverage, decals per cell, cell image sides and scales, and `draws_added` against the package `tiers`.
- [ ] Headset, OVR Metrics in fixed views A and B on that map: the draws the decals add against the 150 target, and a median of at least 72 fps.
- [ ] Headset: decals 4 cm in front of the trim facades: no z-fighting or shimmer against the string courses and zocalo; no visible seams between crops packed in one cell texture under ASTC 6×6.
- [ ] Headset: the tinted wall strip reads as the photographed building's colour and does not clash with the photo above 2.2 m.
- [ ] Headset: graphics memory with the per-cell CC BY-SA images beside the 4096 px trim atlas.

Then the ADR-020 amendment goes from proposed to accepted or is corrected. Turning the switch on by default is a separate decision (plan 4.2).

## Gameplay metrics

Plan B4, [ADR-032](decisions/ADR-032-gameplay-metrics.md), a report only and off by default (`perf layout --playable --gameplay-report`, `map.json` `"gameplay_report": true`, `bench.py run --gameplay-report`). With the option off, `task bench -- --offline` (run `20261107T221930Z`) is the same as run `20261107T215608Z` apart from timings (`bench.py check`). With it on, runs `20261107T222047Z` and `20261107T222156Z` are the same as each other apart from timings, every `gameplay_report.json` is byte-identical between them, and every `layout.json` and every package file but `onward_export.json` is byte-identical to the option-off run (the manifest differs only in the run folder paths and the input hashes that already differ between two option-off runs). No package holds the report.

Estimated on the 2D navmesh grid (2 m) and the collision GLB's boxes, Docker on an Apple M4 Pro; not a device or playtest measurement:

| Map | Distinct routes (attackers / _b) | Max overlap | Longest open sightline | Stretches with no cover | Cover per 100 m, median / max | Uplink at 4 m/s, attackers / defenders | Contact difference | Layout stage off → on |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| bench_murcia r150 | 2 / 2 | 16.9 % | 133.86 m, (90.49, 12.49) → (−25.44, −54.44) | 17 of 38 | 4.0 / 32.0 | 24.73 s / 23.43 s | 5.23 % | 0.7 → 1.1 s |
| bench_madrid r300, Catastro | 3 / 4 | 43.35 % | 291.21 m, (−131.29, −46.0) → (150.0, 29.37) | 53 of 74 | 0.0 / 24.0 | 22.55 s / 31.98 s | 29.48 % | 2.2 → 3.5 s |
| bench_paris r50, 40 t/m, no core | 4 / 4 | 32.02 % | 195.2 m, (78.0, −51.63) → (−71.54, 73.84) | 19 of 44 | 4.0 / 9.09 | 12.69 s / 12.78 s | 0.67 % | 0.2 → 0.5 s |
| bench_krakow r150 | 6 / 6 | 45.46 % | 208.99 m, (89.41, −82.59) → (−100.0, 5.73) | 66 of 118 | 0.0 / 20.0 | 26.02 s / 20.69 s | 20.48 % | 0.6 → 1.5 s |

Against the plan §1 proposals: three or more distinct routes pass on Madrid, Paris and Krakow and fail on Murcia (2: the two pairs' third attempts stay within 8 m of a kept route for more than half its length). The longest open sightline fails everywhere: these are long streets and plazas with no prop within 1.5 m of the sample, seen from a route sample to a building or the wall, and the 60 m proposal is for a sightline a player can be shot along. The contact difference passes on Murcia and Paris and fails on Madrid and Krakow, where one team's nearer block is 21 to 38 m farther from the Uplink. The cover density N is reported, not judged: about half of the 25 m stretches have no cover collider within 1.5 m, and the median stretch has 0 to 4 per 100 m. The report adds 0.3 to 1.3 s to the layout stage.

Pending (not done; the option stays a report until they pass):

- [ ] Playtest (plan 4.2): two or three real matches on a bench map, Hunt and Uplink, comparing the reported routes, open sightlines and first-contact balance with what players feel. It calibrates the §1 thresholds, the 8 m corridor and N.
- [ ] Headset, on a `bench_murcia` package: stand at the four spawn blocks and at both ends of the longest reported sightline, (90.49, 12.49) and (−25.44, −54.44) in local metres (Unity x, z), and confirm that the open stretches are open ground in the game and that the 2D grid matches the baked navmesh there.

Then ADR-032 goes from proposed to accepted or is corrected. Making a metric a blocking check, or acting on it (C6, C7), comes after that.

## Repetition per cell

Plan C1, the ADR-009 amendment *Repetition per cell* (proposed), off by default (`map.json` `"repetition": true`, `materials apply --repetition --cell-m M`, `bench.py run --repetition`; rules in [08-dress.md](08-dress.md#roofs-and-paving-the-repetition-switch-plan-c1)). Docker on an Apple M4 Pro, frozen bench inputs, `--offline --gameplay-report`:

- **Off.** Run `c1_off` against the baseline `20261107T233423Z`: `bench.py check` finds no difference outside timings. Every `scene_materials` and export GLB is the same bytes once the run folder name is normalised in the GLB JSON; the JSON files differ only in run folder paths, `created_at` and the hashes of files that hold those paths, as between two runs at HEAD.
- **On.** Runs `c1_on` and `c1_on2` are the same apart from timings (`bench.py check`), and every GLB is byte-identical between them. Against `c1_off`, triangles (total, core, scenic), draws (total, core, scenic), view draws (median, p90, max) and lightmap pages are identical on all four maps. In `scene_materials` and `scene_playable` the vertices, triangles, materials and image counts are the same; only `TEXCOORD_0` of `carriageway`, `sidewalk`, `pedestrian` and `cobblestone_paving` changes. The roof and paving images can change, because the run copy's plan is chosen again from the extended pools.

| Map | Slots measured | Max contrast / limit | Over limit |
| --- | --- | --- | --- |
| bench_murcia r150 | 8 | 0.875 | 0 |
| bench_madrid r300 | 9 | 0.875 | 0 |
| bench_paris r50 | 8 | 0.7875 | 0 |
| bench_krakow r150 | 13 | 0.875 | 0 |

Ground islands that take a phase (scene_materials, baseline run): carriageway 1 / 13 / 4 / 2, sidewalk 71 / 137 / 23 / 5, pedestrian 35 / 43 / 23 / 46, cobble 20 / 49 / 11 / 10 for Murcia / Madrid / Paris / Krakow. The Murcia carriageway is one island, so it takes a single phase. Generator figures, not device measurements.

Pending (not done; the switch stays off until they pass):

- [ ] Headset, on a map with the switch on: walk a long straight street and a plaza. Sidewalk, carriageway and cobble show no visible grid at the 2 m and 10 m repeats, and no visible seam or jump at the 100 m combine-cell boundaries.
- [ ] Headset: roofs seen from the elevated spawns and ladders show no repeated blotch with the extended roof and paving pools (`acg_roofingtiles011c`, 0.0183, is the closest to its limit).
- [ ] Headset, OVR Metrics in fixed views A and B: draws and frame rate unchanged against the switch off.

Then the ADR-009 amendment goes from proposed to accepted, or the limits are corrected.

## Overture colours as tints

Plan C2 is covered by the ADR-021 amendment *Overture colours as tints* (proposed) and is off by default. It is turned on by `map.json` `"overture_tints": true`, `overture match --tints`, `detail --overture-tints PATH` or `bench.py run --overture-tints`. The rules are in [07-detail.md](07-detail.md). The runs used Docker on an Apple M4 Pro, the frozen bench inputs and `--offline`.

- **Off.** `bench.py check` finds no difference outside timings between run `c2_off` and the baseline `20261108T022003Z` (HEAD of plan C1). The objects, attribution, counts and issues of every `scene_detail.json` and `detail_report.json` are equal, and `scene_visual.glb` and every export package file are the same bytes once the run folder name is normalised. `onward_export.json` differs only in paths, `created_at` and the hashes of files that hold those paths.
- **On.** Run `c2_on` against `c2_off` shows a delta of 0 in triangles (total, core, scenic), draws (total, core, scenic), view draws and lightmap pages on all four maps. Nothing is lent, because every Overture colour on the bench maps comes from OpenStreetMap, which the extract already holds:

| Map | Overture rows | Colours refused as OpenStreetMap (`skipped_dataset`) | Lent | `third_party_tints` walls / roofs |
| --- | --- | --- | --- | --- |
| bench_murcia r150 | 206 | 0 | 0 | 0 / 0 |
| bench_madrid r300 | 749 | 50 | 0 | 0 / 0 |
| bench_paris r50 | 11 | 1 | 0 | 0 / 0 |
| bench_krakow r150 | 221 | 132 | 0 | 0 / 0 |

- **Synthetic block** (`tests/integration/test_overture_pipeline.py`). One Esri facade and roof colour lent to building 312 gives `plaster_white__tint_ochre` and `roof_flat_grey__tint_red`. After `materials apply` the tinted wall is under `facades.stats.kept_materials` and off the trim atlas. Draws (`combine_groups`, 100 m cells) go from 29 to 32. Each distinct tint is one material outside the atlas, so at most 12 × 4 wall and 7 × 2 roof tints plus `party_` copies are possible. These are generator figures, not device measurements.

Pending (not done; the switch stays off until they pass):

- [ ] Find a real city where Overture colours come from a dataset other than OpenStreetMap (Esri, IGN), so the switch has a measurable effect. None of the four bench maps qualifies.
- [ ] Headset, on that map with the switch on: check that a tinted plaster or brick wall reads as its colour under the baked light and does not clash with the palette walls beside it.
- [ ] Headset: check from the spawns that a tinted roof (`roof_tile` or `roof_flat_grey` tint) reads correctly and shows no seam against the trim-atlas facades below it.
- [ ] Headset, OVR Metrics in fixed views A and B: check that the draws the tint materials add keep the views within 150 draws.

Then the ADR-021 amendment goes from proposed to accepted.

## Hunt with bots on a small map

Paris, Rue Mouffetard (48.84045, 2.34981), r50, no core, 40 texels per metre, `final` bake: 24 lightmap pages, `view_draws` median 76 and p90 177 (the export estimate, no culling). Quest 3, 5 November 2026, default listing mode, one player in a Hunt match with bots. OVR Metrics CSV, `logcat`, `ovrgpuprofiler -r="2,4,26,33"` and `dumpsys meminfo`, read with `perf device`; the window is the 440 samples in the map, from 47 s after the capture started to the end.

| Measure | Value |
| --- | --- |
| Map load | 2.9 s |
| Frame rate | median 73, p05 72, minimum 44 |
| App GPU time | median 4.85 ms, p95 6.57 ms, max 8.58 ms |
| GPU / CPU utilisation (median) | 48 % / 34 % |
| App PSS | 2 422–2 628 MB, flat after the load |
| Free memory | at least 1 907 MB |
| Graphics memory (`meminfo`) | 670–710 MB in the menu, 880–960 MB in the map |

- Six samples fall below 72: five in the first 20 s in the map (71, 66, 58, 44, 66), while the bots spawn, and one 69 at 125 s. Every other sample is 72 or 73.
- No error from the map in `logcat`.
- One small map, one session. It says the 40 texels-per-metre lightmap of a 50 m map fits in memory with room left; it is not a budget for a larger map or a match with other players.

## Known limits of the measurement

- Free Roam, one player, no bots, except the Hunt session above, which had bots but still one player. The official guide counts players and objectives inside its budget.
- One walking route, repeated by hand. Between two passes of the same build, GPU time p50/p95 varied by 9% and PSS by 183 MB.
- Draw calls were measured in two views, not at the worst view of the whole map.
- `ovrgpuprofiler -r` has no timestamp. It is aligned to the first `logcat` line (±2 s), so it is a relative signal. In one session the fragment counter returned −1.
- On that headset, OVR Metrics returned 0 for `main_thread_cpu_time`, `render_thread_cpu_time`, and `avg_vertices_per_frame`.

## Roof form from the nDSM spread

Plan C3 is covered by the ADR-023 amendment *Roof form from the nDSM spread* (proposed) and is off by default. It is turned on by `map.json` `"lidar_roofs": true`, `detail --lidar-roofs REPORT` or `bench.py run --lidar-roofs`. The rules are in [07-detail.md](07-detail.md) (*Roofs*). The runs used Docker on an Apple M4 Pro, the frozen bench inputs and `--offline`, on 8 November 2026.

- **Off.** `bench.py check` finds no difference outside timings between run `c3_off` and the baseline `c3_baseline` (HEAD of plan C2), and every export package GLB is the same bytes. `detail` run from the plan C2 tree and from this branch on the same `c3_off` inputs writes byte-identical `scene_visual.glb`, `scene_collision.glb`, `scene_detail.json` and `detail_report.json` on all four maps.
- **On.** Run `c3_on` against `c3_off`. Kraków has no LiDAR plugin and is the control (delta 0).

| Map | Triangles total (core) | Draws total (core) | View draws median / p90 / max | Lightmap pages | Core buildings | Flat | Pitched | Classified |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| bench_murcia r150 | 150 339 → 150 178, −161 (125 052 → 124 891) | 213 → 213 (165 → 165) | 85 / 181 / 213, 0 | 4, 0 | 228 | 2 | 4 | 2.6 % |
| bench_madrid r300 | 398 025 → 397 041, −984 (270 361 → 269 377) | 504 → 505 (352 → 354) | 175 / 375 / 449, +0 / +1 / +1 | 11, 0 | 629 | 9 | 12 | 3.3 % |
| bench_paris r50 | 47 585, 0 | 91, 0 | 34 / 77 / 91, 0 | 3, 0 | 2 | 0 | 0 | 0 % |
| bench_krakow r150 | 72 884, 0 | 272, 0 | 95 / 219 / 268, 0 | 2, 0 | — | — | — | no LiDAR |

Unclassified core buildings, by reason (`counts.lidar_roofs.unclassified`, scenic shells left out):

| Map | tagged | terrace | no_data | too_few_cells | not_rectangular | mixed | ambiguous | fallback |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| bench_murcia | 0 | 150 | 13 | 41 | 17 | 1 | 0 | 0 |
| bench_madrid | 176 | 304 | 7 | 69 | 50 | 2 | 0 | 0 |
| bench_paris | 1 | 0 | 0 | 0 | 1 | 0 | 0 | 0 |

A gable replaces a parapet ring, so the triangles go down. Murcia's core stays under the 220 000 target trajectory (124 891). Madrid's core was already over it (270 361) and drops by 984. Madrid's core draws grow by 2 (total by 1): a gable's `roof_tile` or a flat class's template flat material appears in a combine cell that had none. The Catastro split makes most parts the lower part of a parcel with a taller one, so the terrace rule stops more buildings than the spread does; small parts lack the 8 valid cells. Murcia keeps its 14 coin hips; Madrid loses one hip to a flat class and one stair roof access (7 → 6) to a gable. Pitched rises: Murcia 1.43–2.13 m, Madrid 0.65–4.0 m (median about 1.0 m). These are generator figures, not device measurements.

Pending (not done; the switch stays off until they pass):

- [ ] Headset, on bench_murcia or a Murcia map with `"lidar_roofs": true`, from both spawns and fixed views A and B: the estimated gables read as roofs, the ridge runs along the long side, and there is no gap or z-fighting at the gable pieces and no lightmap seam on slopes or gables.
- [ ] Headset: the same street with the switch off and on. No building looks a whole roof taller, and the Catastro setback terraces stay flat.
- [ ] Headset: a building that turned pitched shows no roof ladder or lift, and no roof a player used to reach has lost its access (ADR-029).
- [ ] Headset, OVR Metrics in views A and B, switch on vs off: median 72 fps or more and 150 draws or fewer.
- [ ] Optional playtest: pitched roofs do not change sniper lines or roof routes in a way that hurts balance. Run `bench.py run --gameplay-report --lidar-roofs` first.

Then the ADR-023 amendment goes from proposed to accepted, or the thresholds are corrected.

