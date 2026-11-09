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
| `onward-generator perf layout … --playable --balanced-spawns` | Places the defenders' block and the Uplink by the gameplay metrics: the most balanced of up to 8 candidates, or today's placement when none is strictly better. Prints one `placement:` line; `layout.json` keeps its keys. Off by default ([ADR-032 C6 amendment](decisions/ADR-032-gameplay-metrics.md#c6-amendment-proposed--spawns-and-the-uplink-placed-by-these-metrics), [Balanced spawns](#balanced-spawns)) |
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
- The scenic shells bake at scale 0.25: 3.4 M texels of the 34.7 M at r300. Plan C4 can take the walls out of the lightmap ([below](#scenic-walls-out-of-the-lightmap)).

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

Then ADR-032 goes from proposed to accepted or is corrected. Making a metric a blocking check comes after that. C6 acts on the metrics behind an option that is off by default ([Balanced spawns](#balanced-spawns)), and so does C7 ([Cover on long sightlines](#cover-on-long-sightlines)); turning either on by default waits for the playtest.

## Balanced spawns

Plan C6, the ADR-032 C6 amendment (proposed), off by default (`perf layout --playable --balanced-spawns`, `map.json` `"balanced_spawns": true`, `bench.py run --balanced-spawns`). The defenders' block and the Uplink are the most balanced of up to 8 candidates (4 defenders' positions, each with the midpoint Uplink and the even-contact Uplink), scored with the gameplay report's own measures; today's placement is kept unless one is strictly better. Docker on an Apple M4 Pro, frozen bench inputs, `--offline --gameplay-report`, runs in a scratch copy of the bench folder because the repository volume was full:

- **Off.** Run `c6_off` against `head_off` (workshop/c5): `bench.py check` finds no difference outside timings; `layout.json`, `gameplay_report.json` and every package file but `onward_export.json` are byte-identical.
- **On.** Runs `c6_on` and `c6_on2` are the same apart from timings, with byte-identical `layout.json` and `gameplay_report.json`. Triangles, draws and lightmap pages are those of `c6_off`.
- **After the mutation fixes.** Runs `c6_off_b` and `c6_on_b` repeat `c6_off` and `c6_on`: `bench.py check` finds no difference outside timings, and every `layout.json` and `gameplay_report.json` is byte-identical.

| Map | Candidate | Contact difference off → on | Distinct routes off → on | Max overlap off → on | Layout stage off → on |
| --- | --- | --- | --- | --- | --- |
| bench_murcia r150 | 7 of 8 | 5.23 → 2.29 % | 2 → 2 | 16.9 → 9.89 % | 1.2 → 1.6 s |
| bench_madrid r300, Catastro | 5 of 8 | 29.48 → 0.57 % | 3 → 3 | 43.35 → 38.3 % | 3.5 → 5.1 s |
| bench_paris r50, no core | 3 of 8 | 0.67 → 0.13 % | 4 → 4 | 32.02 → 31.89 % | 0.5 → 0.9 s |
| bench_krakow r150 | 1 of 8 | 20.48 → 0.49 % | 6 → 6 | 45.46 → 45.46 % | 1.6 → 2.2 s |

Layout seconds include the gameplay report. All four maps pass the 15 % contact proposal with the option on; Murcia still misses the 3-route proposal (2 routes on and off). The figures are estimates on the 2D navmesh at 4 m/s.

Pending (not done):

- [ ] Playtest (plan 4.2): Hunt and Uplink on a bench map with the option on and off, comparing the reported balance, routes and sightlines with what players feel.
- [ ] Headset, a `bench_murcia` package with the option on: the four spawn blocks and the Uplink load; Assault, Uplink and Hunt validate in the SDK (Unity 2021.3.23f1 build without validator errors); bots patrol from their starts; no spawn stands in or against a prop; the spawns are on the baked navmesh.
- [ ] Headset: ≥ 72 fps and ≤ 150 draws at views A and B with the option off and on; no long open sightline joins the two teams' spawns.
- [ ] Unity (A3): the Uplink, the navigation origin, is within 0.25 m of the baked mesh, and the `Navmesh islands` log line still shows the origin island holding most of the area.

## Cover on long sightlines

Plan C7, the ADR-032 C7 amendment (proposed), off by default (`map.json` `"sightline_cover": true`, `perf cover`, `bench.py run --sightline-cover`; rules in [01-pipeline.md](01-pipeline.md#sightline-cover-off-by-default)). A stage after the layout copies `scene_playable` to `scene_covered` and stands up to 24 solid catalogue props at the route samples the gameplay report finds open past 60 m, each leaving 1.2 m of passage and clear of the spawns, objectives, AI starts, ammo, climbs, doors, Hunt routes and the carriageway. Docker on an Apple M4 Pro, frozen bench inputs, `--offline --gameplay-report`, runs in a scratch bench root because the repository volume was nearly full:

- **Off.** Run `c7_off` against the workshop/c6 run `20261108T201929Z`: `bench.py check` finds no difference outside timings; `layout.json`, `gameplay_report.json`, `scene_visual.glb`, `scene_collision.glb` and every package file but `onward_export.json` are byte-identical, and no `scene_covered` is written.
- **On.** Runs `c7_on` and `c7_on2` are the same apart from timings; every GLB and `gameplay_report.json` in `scene_covered` and every package file but `onward_export.json` are byte-identical, and the JSON files differ only in the run folder paths and the hashes of files that hold them. `layout.json` is the option-off file.

| Map | Props (targets over 60 m) | Longest open sightline off → on | Uncovered stretches off → on | Stretches over 60 m off → on | Cover per 100 m, min stretch | Distinct routes, max overlap, contact diff | Triangles off → on | Draws off → on | View draws max | Lightmap pages | Cover stage |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| bench_murcia r150 | 17 (97) | 133.86 → 129.42 m | 17 → 4 of 38 | 32 → 29 | 0.0 → 0.0 | 2, 16.9 %, 5.23 % (unchanged) | 150 339 → 151 273 | 213 → 218 | 213 → 218 | 4 → 4 | 2.4 s |
| bench_madrid r300, Catastro | 24 (273) | 291.21 → 254.3 m | 53 → 35 of 74 | 72 → 71 | 0.0 → 0.0 | 3, 43.35 %, 29.48 % (unchanged) | 398 025 → 398 551 | 504 → 509 | 448 → 453 | 11 → 11 | 6.3 s |
| bench_paris r50, no core | 22 (183) | 195.2 → 192.34 m | 19 → 5 of 44 | 44 → 44 | 0.0 → 0.0 | 4, 32.02 %, 0.67 % (unchanged) | 47 585 → 47 849 | 91 → 93 | 91 → 93 | 3 → 3 | 1.7 s |
| bench_krakow r150 | 24 (508) | 208.99 → 205.01 m | 66 → 31 of 118 | 118 → 118 | 0.0 → 0.0 | 6, 45.46 %, 20.48 % (unchanged) | 72 884 → 73 172 | 272 → 275 | 268 → 271 | 2 → 2 | 3.9 s |

With `--balanced-spawns` as well (run `c7_on_bal`), the longest open sightline is 124.08 m on Murcia, 241.55 m on Madrid, 191.62 m on Paris and 205.01 m on Krakow, with the C6 contact differences (2.29, 0.57, 0.13 and 0.49 %) unchanged by the cover.

The longest open sightline falls on all four maps and the uncovered stretches by a third to three quarters; the stretches over 60 m stay the same on Paris and Krakow, where every stretch still has a sample open past 60 m. The minimum cover per 100 m stays 0.0 because some stretches remain uncovered, mostly beside the 5 m spawn keep-outs: Murcia's longest ray starts 3.4 m from a defenders' spawn point. Routes, overlap and contact difference cannot move, because the 2D navmesh ignores furniture. Every prop placed was already in the scene (`kenney_urban_detail_block`, `Barrel_02`, `kenney_urban_detail_barrier_strong_type_a`), so no material is added: 264 to 934 triangles, 2 to 5 draws and no lightmap page. Part of the gain is by definition: a sample with cover within 1.5 m stops counting as open even under a prop lower than 1.2 m (`cover_report.json` `rays_cut` counts the rays each prop really shortens). The cover stage takes 1.7 to 6.3 s. Estimates on the 2D navmesh, not device or playtest measurements.

Pending (not done; the option stays off until they pass):

- [ ] Playtest (plan 4.2), with the option off and on, on a bench map in Hunt and Uplink: the new cover reads as cover, does not feel like an arbitrary obstacle and does not block a route players use. It calibrates the 60 m sightline and the cover N.
- [ ] Headset: a `bench_murcia` package with the option on, built in Unity 2021.3.23f1 with no SDK validator errors. Stand at both ends of the previously longest reported sightlines and confirm a placed prop now breaks them.
- [ ] Headset: walk past every cover prop; at least 1.2 m of passage stays free, no prop floats, sinks or clips a facade, door, ladder or lift, and none sits on a spawn, the Uplink, an ammo box or an AI start.
- [ ] Headset or Unity: the baked navmesh is not split by the new colliders (the `Navmesh islands` log line still shows the origin island holding most of the area), and Hunt bots still patrol from their starts.
- [ ] Headset: ≥ 72 fps and ≤ 150 draws at views A and B with the option off and on (OVR Metrics, the protocol above).

Then the ADR-032 C7 amendment moves to accepted or is corrected. The option is not turned on by default before then.

## Regional atlases

Plan D1, the ADR-020 and ADR-027 amendments of 8 November 2026 (proposed), off by default (`map.json` `"regional_facades": true`, `materials apply --regional-facades`, `bench.py run --regional-facades`; [08-dress.md](08-dress.md#trim-sheet-facades)). `central_european`, `anglo` and `latin_american` take a trim atlas from `config/facade_styles.regional.json`, with their own windows and era bands. Docker on an Apple M4 Pro, frozen bench inputs, `--offline`:

- **Off.** Run `d1_off` against the workshop/c7 run `20261108T232105Z`: `bench.py check` finds no difference outside timings; triangles, draws, view draws and lightmap pages have delta 0 on all four maps; every package file but `onward_export.json` (creation time, run folder paths and the hashes of files that hold them, as between any two runs) is byte-identical, and the `facades` block of every `materials_report.json` is equal.
- **On.** Runs `d1_on` and `d1_on2` are the same apart from timings, and the Kraków atlas is byte-identical (`facade_trim.png` sha256 `3e114867875c6f94…`). Murcia and Madrid (mediterranean) and Paris (`generic`, no regional recipe) give every package file but `onward_export.json` byte-identical to the off run. Only Kraków changes.

| Map | Path | Materials | Predicted draws (core, scenic) | View draws median / p90 / max | Triangles | Images | Texture memory RGBA32 / ASTC 6×6 | Lightmap pages |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| bench_krakow r150 | per material (off; `central_european`, `style_has_no_atlas`) | 118 | 272 (215, 51) | 95 / 219 / 268 | 72 884 | 64 | 129.7 / 14.5 MiB | 2 |
| bench_krakow r150 | trim, regional `central_european` (on) | 103 | 214 (179, 29) | 73 / 180 / 214 | 77 688 | 61 | 156.1 / 17.5 MiB | 2 |

Texture memory is every distinct image of the package's `scene_visual.glb` with mips (4/3), in RGBA32 and as ASTC 6×6 blocks: an estimate, not a Unity or device reading. The option takes 58 predicted draws (−21 %) and 15 materials off Kraków for 6.6 % more triangles (the band cuts) and one 2048 × 4096 atlas (+3.0 MiB as ASTC 6×6). This matches ADR-020's measure with a forced mediterranean atlas (−29 % draws on an older Kraków build). 155 buildings take the atlas; 41 tinted walls (`building:colour`) keep their own material, as on Murcia and Madrid. Only one trimmed building has an observed era (`pre_1900`); the other 154 keep the look they would have had without era bands (`era_bands.buildings`: `pre_1900` 1, `unknown` 154). `anglo` and `latin_american` have no bench map. They are covered by unit and integration tests on synthetic centres and the Murcia block.

Pending (not done; the option stays off until they pass):

- [ ] Headset, option on and off, on a `central_european` map (bench_krakow r150), and if possible synthetic `anglo` and `latin_american` maps: the casement, sash and grille cells up close and at a distance under ASTC 6×6; the atlas seams at a distance and the string course up close; neighbouring blocks read apart through tone and era; the stretched plain-wall body reads as weathering, not a smear.
- [ ] Headset: ≥ 72 fps median and ≤ 150 draws at views A and B with the option off and on (OVR Metrics, the protocol above); graphics memory with the 4096 px atlas.
- [ ] Plan §1 rubric (1 to 5 for facade and repetition) on the contact sheet and the fixed spawn captures, option off and on.
- [ ] Era bands on a map with observed eras (Catastro year or OSM `start_date`) in a regional style.
- [ ] Optional, Unity 2021.3.23f1 (A3): real draws and texture memory for Kraków with the option on.

Then the ADR-020 and ADR-027 amendments move to accepted or are corrected. The option is not turned on by default before then.

## Street profile

Plan D2, the ADR-006 and ADR-010 amendments of 8 November 2026 (proposed), off by default (`map.json` `"street_profile": true`, `run --street-profile`, `bench.py run --street-profile [--street-profile-reply FILE]`; [10-ai.md](10-ai.md#street-profile-plan-d2)). The bench never calls a model: with a reply file it is replayed (provider `replay`), without one the reply is empty and the neutral rules profile changes nothing. Docker on an Apple M4 Pro, frozen bench inputs, `--offline`, `--gameplay-report`:

- **Off.** Run `d2_off` against the workshop/d1 run `20261109T013210Z`: `bench.py check` finds no difference outside timings; triangles, draws, view draws, lightmap pages and the gameplay headline have delta 0 on all four maps.
- **On.** Runs `d2_on` and `d2_on2` with `tests/fixtures/street_profile/applied.json` (`mediterranean`, `extended_pools`, `wrecked`, `years_30`, `light`). On every map `palette` is skipped (`texture_plan_not_calm`: the saved texture plans bind a roof or paving tile over the calm limit) and `cover` is inactive (no `sightline_cover`); the facade style, the wrecked props selection (18 library ids) and the 30-year profile apply.

| Map | Triangles off → on | Predicted draws off → on | View draws median / p90 / max, on | Lightmap pages | Longest open sight m |
| --- | --- | --- | --- | --- | --- |
| bench_murcia r150 | 150 339 → 147 826 | 213 → 175 | 80 / 145 / 175 | 4 → 4 | 133.9 → 116.8 |
| bench_madrid r300 | 398 025 → 381 972 | 504 → 456 | 173 / 326 / 400 | 11 → 11 | 291.2 → 291.2 |
| bench_paris r50 | 47 585 → 29 512 | 91 → 44 | 23 / 39 / 44 | 3 → 3 | 195.2 → 190.4 |
| bench_krakow r150 | 72 884 → 62 735 | 272 → 168 | 68 / 136 / 168 | 2 → 2 | 209.0 → 216.0 |

Murcia and Madrid are mediterranean already, so their drop comes from overgrow: fewer props (the wrecked group) and the 30-year profile. Paris (`generic`) and Kraków (`central_european`) also take the mediterranean trim atlas, which the profile names explicitly: that is the largest share of their drop, and it is a look the brief asked for, not a regional fact. Estimates from the package, not Unity or device readings.

Pending (not done; the option stays off until they pass):

- [ ] Optional, with local Ollama (`qwen3:14b`, loopback): one map with `"street_profile": true`; read the record (raw reply, hashes, ids), rerun and confirm the replay makes no call and the outputs are byte-identical.
- [ ] Headset, the same map with the option off and on: ≥ 72 fps median and ≤ 150 draws at views A and B, draws and triangles inside the plan §1 budget.
- [ ] Headset: the abandonment and props profile read as the brief asked (density, condition); walkways keep 1.2 m of passage and spawns stay clear.
- [ ] Headset: the chosen facade style shows no atlas seams, and observed OSM colours and eras still show.
- [ ] Headset and a playtest (plan 4.2): a cover id that changes the C7 count does not seal a route.

Then the ADR-006 and ADR-010 amendments move to accepted or are corrected. The option is not turned on by default before then.

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


## Intermediate HLOD ring

Plan D3, [ADR-017 amendment of 9 November 2026](decisions/ADR-017-playable-core.md#amendment-9-november-2026-an-intermediate-hlod-ring-plan-d3), off by default. Bench runs on the frozen inputs, Docker on an Apple M4 Pro, workshop/d3: `d3_off` (no option) against the baseline `20261109T041434Z` is identical apart from timings (`bench.py check`), and `d3_on100` and `d3_on100b` (`--mid-ring-m 100`) are identical to each other. Package figures from `onward_export.json`, not a bake and not a headset measurement.

| Map | Triangles total (core / mid / scenic) | Draws total (core / mid / scenic) | View draws median / p90 / max | Lightmap pages | Mid buildings | Mid triangles per building mean / min / max | `detail` s |
| --- | --- | --- | --- | --- | --- | --- | --- |
| bench_murcia r150, off | 150 339 (125 052 / – / 18 448) | 213 (165 / – / 32) | 85 / 181 / 213 | 4 | – | – | 2.9 |
| bench_murcia r150, on | 170 603 (125 052 / 38 608 / 104) | 222 (165 / 35 / 12) | 88 / 186 / 221 | 4 | 319 | 74.2 / 10 / 234 | 3.6 |
| bench_madrid r300, off | 398 025 (270 361 / – / 108 602) | 504 (352 / – / 191) | 175 / 374 / 448 | 11 | – | – | 10.5 |
| bench_madrid r300, on | 460 922 (270 361 / 126 022 / 45 477) | 535 (352 / 134 / 143) | 187 / 391 / 473 | 10 | 928 | 84.6 / 10 / 496 | 12.7 |
| bench_paris r50, off and on | 47 585 (no core) | 91 | 34 / 77 / 91 | 3 | – | – | 0.1 |
| bench_krakow r150, off | 72 884 (58 115 / – / 5 832) | 272 (215 / – / 51) | 95 / 219 / 268 | 2 | – | – | 0.9 |
| bench_krakow r150, on | 79 940 (58 115 / 12 836 / 52) | 302 (215 / 85 / 5) | 107 / 235 / 293 | 2 | 111 | 115.6 / 12 / 184 | 1.2 |

- The core does not change: core triangles, draws and the gameplay headline are the same off and on. Paris has no core and cannot have a ring.
- With a 100 m ring nearly every scenic building of these maps becomes mid (Murcia and Kraków keep none past it). The ring costs 2.1 times the scenic shells it replaces in Murcia, 1.7 in Madrid and 2.2 in Kraków.
- Against the plan's 220 000 Unity triangles: Murcia stays under it (170 603), Kraków too (79 940); Madrid r300 was already over (398 025) and rises by 62 897. On Madrid the option needs a smaller ring or a budget core.
- The mid tier brings no new material family; its roofs add (cell, roof material) groups, and the ring's draws rise by 9 (Murcia), 31 (Madrid) and 30 (Kraków); the view-draw p90 by 5, 17 and 16. Views A and B are measured on the headset.
- Per building: the cap is 150, and no building with painted windows passes it. The maxima above it (Murcia 3 of 319 buildings, Madrid 23 of 928, Kraków 3 of 111) are footprints whose plain shell, walls and flat cap, is already over 150; they draw what the scenic shell draws, with no window. Under 50 (Murcia 158, Madrid 349, Kraków 10) are buildings with few exposed walls of a bay (terraced blocks) or one low storey; the shell is not padded.
- Lightmap pages: Madrid 11 → 10 in the estimate, the others unchanged; the mid walls and windows stay in the scenic class at 0.25.

Pending on the headset (A2) and in Unity (A3), not done; the option stays off until they pass:
- Unity 2021.3.23f1: bench_madrid r300 with the ring off and on, `task unity -- --bake draft` and `--bake final`; `scene_report.json` triangles and bake seconds with `tools/unity/report_timings.py`; lightmap pages within the estimate.
- Quest 3, the same map off and on: at least 72 fps median on the route, at most 150 draws in views A and B (OVR Metrics), triangles within 220 000.
- How the ring reads from the core wall and through the fog (which still starts at `core_radius_m`): atlas seams or a smeared window treatment on the mid walls, roof forms against the sky.
- The seams core / mid / scenic: brightness steps between baked core walls and mid walls, with `build.scenic_out_of_lightmap` on and off; popping or z-fighting where tiers share a cell.
- No player or bot reaches a mid building: the perimeter wall stays on the core edge, no ladder, lift or spawn outside the core.
- After the session, move the ADR-017 D3 amendment to accepted or correct it.

## Scenic walls out of the lightmap

Plan C4 is covered by the ADR-017 amendment of 8 November 2026 (proposed) and is off by default. It is turned on by the export card's `build.scenic_out_of_lightmap: true` or `bench.py run --scenic-out-of-lightmap`, which sets it in each run copy's card. The rules are in [12-export.md](12-export.md#lighting). The runs used Docker on an Apple M4 Pro, the frozen bench inputs and `--offline`, on 8 November 2026.

- **Off.** `bench.py check` finds no difference outside timings between run `c4_off` and the baseline `c4_baseline` (HEAD of plan C3).
- **On.** Run `c4_on` against `c4_off`. Paris has no core and no scenic walls, and is the control.

| Map | Lightmap texels (estimate) | Scenic texels taken off | Pages at least | Triangles, draws, view draws |
| --- | --- | --- | --- | --- |
| bench_murcia r150 | 13 796 669 → 11 916 415 | 1 880 254 (13.6 %) | 4 → 3 | 150 339; 213; 85 / 181 / 213: 0 |
| bench_madrid r300 | 42 183 417 → 30 723 971 | 11 459 446 (27.2 %) | 11 → 8 | 398 025; 504; 175 / 374 / 448: 0 |
| bench_paris r50 | 11 516 466, 0 | 0 | 3, 0 | 47 585; 91; 34 / 77 / 91: 0 |
| bench_krakow r150 | 6 995 380 → 6 218 679 | 776 701 (11.1 %) | 2, 0 | 72 884; 272; 95 / 219 / 268: 0 |

Madrid's drop is one texel over its `scenic` class because the total and each class are rounded on their own. The scenic roofs (`roof_flat_grey`) stay baked and are counted under `roof`. The figures are the export estimate (a lower bound on pages), not a bake: the Unity seconds are not measured (`unity.status` is `missing` on every bench map).

Pending (not done; the option stays off until they pass):

- [ ] Unity 2021.3.23f1: the adapter compiles, and the scene log's `scaleInLightmap (scale x renderers)` line shows the scenic renderers at 0 with the option on and at 0.25 with it off.
- [ ] Bake time: bench_murcia (and bench_madrid if time allows) with the option off and on, `task unity -- --bake draft` and `--bake final`; compare `seconds.gi_bake`, `seconds.bake`, `lightmaps` and `lightmap_texels` with `python tools/unity/report_timings.py <pkgA> <pkgB>`. The final bake must stay at or under 5 min and the draft at or under 1.5 min at r150 / 10 texels/m.
- [ ] Editor views A, B and one across the core edge: the ring does not read black or slate-dark (ADR-014 saw about 48 % of the albedo under the ambient light alone), and the core-edge buildings still cast and receive plausible shadows.
- [ ] Headset (Quest 3), option on and off: the ring's brightness and colour sit with the fog, no seam at the core wall, median at least 72 fps, views A and B at or under 150 draws, graphics memory at or under 1 GB.

Then the ADR-017 amendment goes from proposed to accepted, or the option is dropped.


## Lightmap page budget

Plan C5 (U2 of the scale-and-speed plan) is covered by the ADR-005 amendment of 8 November 2026 (proposed) and is off by default. It is turned on by the export card's `build.lightmap_pages_max: N` or `bench.py run --lightmap-pages-max N`, which sets it in each run copy's card. `export package` then lowers `lightmap_texels_per_m`, in 0.25 steps and never below 4 texels/m, until `lightmap_estimate.pages_at_least` is at most N. The rules are in [12-export.md](12-export.md#lighting). The runs used Docker on an Apple M4 Pro, the frozen bench inputs and `--offline`, on 8 November 2026.

- **Off.** `bench.py check` finds no difference outside timings between run `c5_off` and the baseline `c5_baseline` (HEAD of plan C4), and every map's `adapter_options.json` and `package_id` are byte-identical.
- **On, N = 8.** Run `c5_on8` against `c5_off`. Only Madrid r300 is over the budget at 10 texels/m, as U2 expected (only at 40 texels/m or from r300).
- **On, N = 8, with plan C4.** Run `c5_on8_scenic` (Madrid only, `--scenic-out-of-lightmap` too): the scenic walls leave the estimate first, Madrid is at 8 pages at 10 texels/m, and the density is kept.

| Map | Texels/m: card → applied | Lightmap texels (estimate) | Pages at least | Triangles, draws, view draws |
| --- | --- | --- | --- | --- |
| bench_murcia r150 | 10, kept | 13 796 669, 0 | 4, 0 | 150 339; 213; 85 / 181 / 213: 0 |
| bench_madrid r300 | 10 → 8.75 | 42 183 417 → 32 296 678 | 11 → 8 | 398 025; 504; 175 / 374 / 448: 0 |
| bench_madrid r300, with C4 | 10, kept | 42 183 417 → 30 723 971 | 11 → 8 | 398 025; 504: 0 |
| bench_paris r50 | 40, kept | 11 516 466, 0 | 3, 0 | 47 585; 91; 34 / 77 / 91: 0 |
| bench_krakow r150 | 10, kept | 6 995 380, 0 | 2, 0 | 72 884; 272; 95 / 219 / 268: 0 |

At 9 texels/m Madrid would need 34.2 M texels, over the 33.6 M of 8 pages of 2048 px, so 8.75 is the largest density on the grid that fits. A map whose budget does not bind keeps its density but gets a new `package_id`, since the card changed. The figures are the export estimate, a lower bound on pages, not a bake: Unity packed 5 pages for 17.8 M texels on Barcelona r150 and 24 pages for Paris at 40 texels/m, where the estimate said 3, so the measured count can still exceed N (`unity.status` is `missing` on every bench map).

Pending (not done; the option stays off until they pass):

- [ ] Unity 2021.3.23f1: build bench_madrid with `build.lightmap_pages_max` 8 and without it, `task unity -- --bake draft` and `--bake final`; compare `lightmaps`, `lightmap_texels`, `seconds.gi_bake` and `seconds.bake` with `python tools/unity/report_timings.py <pkgA> <pkgB>`. The `lightmaps` count of `build/scene_report.json` is at most 8, or record how far the lower-bound estimate is off (A3).
- [ ] Scene log: the `Lightmaps: N, T texels, at X texels/m` line shows 8.75, and no new object with overlapping UVs beyond the known sign rims.
- [ ] Editor views A, B and a close facade: shadows softer, with no blotches, seams or black charts at the lowered density.
- [ ] Headset (Quest 3), budget on and off on bench_madrid or another r300 map: shadow and contact-shadow quality on facades and streets acceptable, median at least 72 fps, views A and B at or under 150 draws, graphics memory at or under 1 GB, load time noted.

Then the ADR-005 amendment goes from proposed to accepted, or the option is dropped.
