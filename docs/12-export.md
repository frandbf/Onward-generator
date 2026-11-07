# Export to Onward — adapter `onward_unity` 0.1.0 (phase 07)

The adapter turns the directory that `export` reads (the dressed detail, and on a playable map the `bounds` copy) into the Onward Launcher pair `.content3` and `.info3`. That pair is the map format. A GLB is not a map. Decision: `docs/decisions/ADR-007-onward-export-adapter.md`.

**Status: the Quest acceptance test is still open.** Until it passes, `export.enabled: true` warns `export_adapter_unverified` and the manifest records `device_verified: false`.

## Two separate halves

| Half | Where | What it does |
| --- | --- | --- |
| Python | `onward-generator export package` (`src/onward_generator/export/`) | Validates the inputs and writes a new immutable **export package**. It does not use Unity or the headset |
| Unity | `tools/unity/onward_adapter/` (`OnwardMapAdapter.cs`, `run_export.sh`) | Verifies the package, builds the scene with the SDK v1.15 prefabs, bakes lighting, and calls the Launcher |

The generation engine does not know the adapter. The adapter only reads the package.

## Export package

```bash
.venv/bin/python -m onward_generator export package \
  --project maps/<id>/config/project.json \
  --detail  maps/<id>/data/processed/scene_playable \
  --layout  maps/<id>/data/processed/layout/layout.json \
  --config  maps/<id>/config/export.json \
  --out     maps/<id>/data/processed/export/pkg_01
```

`--detail` for a `gameplay_layout` must be the `bounds` copy (`envelope/perimeter_wall`). `--project` is the configuration with `export.enabled`. `--config` is the card (`config/export.json` in that folder, or `config/export/onward.default.json` when it is omitted).

The command writes nothing when:

- the project is invalid or does not have `"export": {"adapter": "onward", "enabled": true}`;
- the phase-04 automatic checks failed, a GLB is missing, or the ODbL attribution is missing;
- the scene holds overgrowth but `scene_detail.json` has no `envelope.fit`: `bounds` writes that record only from a passing `overgrow fit` of the same GLB (docs/01-pipeline.md, section 9), so a scene without it was never checked. Packages exported before 4 November 2026 have no such record; a new export of those scenes needs `overgrow fit` and `bounds` again;
- the layout is neither `onward_generator.perf_layout` nor `onward_generator.gameplay_layout`, or it lacks 8 spawn points per team (the SDK prefab count), Uplink, a map quad, a positive `level_size_m`, or the damage volume;
- a `gameplay_layout` lacks 32 AI points (one per route, 1 m from the start, separated by `ai_separation_m` or 8 m), 4 boxes, routes of 2 points, `Suburban Day`, Urban/Day/`UrbanDay1` or Urban/Night/`UrbanNight1` ([ADR-026](decisions/ADR-026-time-of-day.md)), far clip 1000, flyover, a `lightning` of 0 or 1 that is 1 only at night, and at most 3 `ladders` and 3 `lifts`;
- a `gameplay_layout` 0.3.0 lacks the mode codes, the second spawn block for each team, the two Escort starts, the Evac point, or 3 Retrieval hard drives;
- the adapter configuration does not match its schema;
- the output directory already exists. Packages are never overwritten. The next one is `pkg_02`.

| File | Contents |
| --- | --- |
| `onward_export.json` | Manifest `onward_generator.onward_export_package` 0.1.0: `package_id`, adapter and target (Unity 2021.3.23f1, SDK 1.15, Quest), title and description with attribution, the declared fidelity, SHA-256 and origin of every file, input hashes, `lightmap_estimate` (below), `tiers` (below), and warnings |
| `scene_visual.glb`, `scene_collision.glb` | Exact copies of the scene directory |
| `layout.json` | A copy of the gameplay layout |
| `adapter_options.json` | Combine by a 100 m cell, no static batching, no occlusion culling (variant v02, ADR-005). Onward lightmap guide: texels per metre from the map (default 10, at most 40), 2048 px, non-directional, and `lightmap_scales`: one `scaleInLightmap` per visual material (below). The samples and bounces come from the bake profile ([below](#bake-profiles)): `final` uses 256/512/512 samples and 2–4 bounces, `draft` (the default) 32/128/128 and 1–2. Lightmaps ASTC 8×8 on Android. `albedo_compression`: the Android format of the albedo textures, `astc_6x6` (default) or `none` for uncompressed RGBA32 as before ([below](#albedo-textures)) |
| `thumbnail.png` | 512×256, a view of the mesh and that map's title |
| `quest_acceptance.md` | The headset checklist: Free Roam, Uplink, Hunt, ambience, impact, flyover, and the urban card |

`package_id` is the map id plus the first ten characters of a hash of the files, the options, and the map data. The same content always produces the same id.

`onward-generator export verify PACKAGE` recomputes every hash.

## Unity build

```bash
tools/unity/onward_adapter/run_export.sh maps/<id>/data/processed/export/pkg_01 tool scene launcher
CONFIRM_PUSH=yes tools/unity/onward_adapter/run_export.sh maps/<id>/data/processed/export/pkg_01 push
```

`onward-generator run` calls `tool scene launcher` after the Python stages and then asks before `push`. Only `s` or `si` uploads. Before the first stage it checks that this project is not already open. A terminal asks for the Editor to be closed; the stages start after `s` or `si` and only once the lock is free.

| Step | Effect |
| --- | --- |
| `tool` | Copies `OnwardMapAdapter.cs` and the listing thumbnail into the Unity 2021.3.23f1 project that holds SDK v1.15 and the Launcher path patch. A file whose bytes already match (`cmp -s`) is not copied, so an unchanged adapter does not make Unity recompile |
| `scene` | Verifies the package in Python and in C# (SHA-256, adapter name and version, Unity version). Builds the scene under `Assets/OnwardAdapter/<package_id>/`: visuals combined by cell and material (materials that draw the same, with the same albedo texture, colour, shader, GI class and lightmap scale and no rule by name, share one group; the log prints `Material dedupe`), collision as `MeshCollider` with no renderer, a baked directional light, spawns, Uplink, Free Roam, map quad, tent, damage volume, and a `LevelDescriptor` with the title and attribution. Bakes with the profile from `--bake` or `ONWARD_BAKE` (`draft` by default, [Bake profiles](#bake-profiles)). Writes `build/scene_report.json`. Refuses if that folder already exists |
| `launcher` | Calls the Onward Launcher for Quest. Writes `build/launcher/unity-export.local.{content3,info3}`, `build/content3.md5`, `build/launcher.sha256`, and the step's timings in `build/launcher_report.json` (an older one is renamed with a time suffix). Never overwrites the launcher output |
| `push` | **Only with `CONFIRM_PUSH=yes`.** Backs up the headset files it will replace (`build/device_backup/<time>-XXXX/`), copies the bundle, the `.info3` and the thumbnail, checks the MD5 of each on the device, and records it in `build/push.log`. The listing mode (below) is `local` unless `--listing` or `ONWARD_QUEST_LISTING` says otherwise |
| `inspect` | Read-only. Writes `build/device_inspect/<time>/files_tree.txt` (`ls -laR` of the game's `files/` folder) and copies every `.info3` in `CustomContent`. Never copies a bundle and never writes to the headset |

Unity logs stay in `$LOG_ROOT/onward-export/`: `~/Library/Logs/onward-export/` on macOS and `${XDG_STATE_HOME:-~/.local/state}/onward-generator/onward-export/` on Linux. `LOG_DIR` replaces the whole folder. The host comes from `--platform` as the script's first argument, then `ONWARD_PLATFORM`, then `uname -s` ([01-pipeline.md](01-pipeline.md#host-platform)). The Editor must be closed before `scene` starts. `run` detects that from the exclusive lock on `Temp/UnityLockfile`, or from `Library/EditorInstance.json` when the lock file is absent and its process is still alive. A newer Unity editor rewrites the project and 2021.3.23f1 can no longer open it.

### Unity timings

Each Unity step charges its time to named phases and logs them in one line, `[OnwardAdapter] Timings: <phase> <s> s, ..., total <s> s`. `run` keeps that line in its Unity summary.

| Step | Phases, in order | Written to |
| --- | --- | --- |
| `scene` | `import` (package, GLB, textures, meshes, collision), `combine`, `navmesh`, `nav_nodes` (every `GenerateNodes` attempt; each `Navigation spacing` line also gives its own seconds), `gameplay` (gameplay objects, lights, probes, first scene save, level settings), `gi_bake` (`Lightmapping.Bake`), `astc` (lightmap reimport), `save` | `seconds.<phase>` and `seconds.total` in `build/scene_report.json`, beside the older `seconds.scene` (everything before the bake) and `seconds.bake` (`gi_bake` plus `astc`) |
| `launcher` | `open` (package check and scene open), `hunt` (Hunt roster and patrol, save), `bundle` (`ContentBuilder.BuildScene`), `copy` (bundle copy, MD5, `.info3`) | `seconds.<phase>` and `seconds.total` in `build/launcher_report.json` (schema `onward_generator.onward_adapter_launcher_report`). `scene_report.json` belongs to the scene step and is not rewritten |

A phase without its step (no gameplay layout: no `navmesh`) is left out. After the bake the scene step also logs `[OnwardAdapter] Lightmapper: <used> (bake draft|final, requested ProgressiveGPU, fallback yes|no[: first warning]). Lightmaps: N (<width>x<height> xK, ...)`, and `scene_report.json` adds `lightmap_bake`: `profile` (`draft` or `final`; absent in a report from before the profiles, which baked as `final`), `lightmapper_requested`, `lightmapper`, `fallback`, `fallback_warning` and `sizes`. `fallback` is true when the lightmapper in the settings after the bake is not `ProgressiveGPU`, or when Unity logged a message about falling back to the CPU during the bake. `run_export.sh` also prints any such line from the scene log as `[export] lightmapper: ...`, because a native warning may not reach the C# log hook. The message text is Unity's and was not seen on this project's hosts, so the match is loose (`fall back` together with `CPU`, `lightmapper` or `OpenCL`).

`python tools/unity/report_timings.py PATH...` prints the timings of one or more packages as a Markdown table, one column per package and one row per phase (`scene.<phase>`, `launcher.<phase>`, `lightmapper`, `lightmaps`, and `bake profile` when the report names one). PATH is a package folder, its `build/` folder or a `scene_report.json`; a missing value is `-`. `build_map.sh` (`task unity`) prints the table after the Unity steps. These are Editor timings, not device measurements. `task bench -- report RUN_DIR` reads the same seconds into a bench report, through `phase_seconds()` in that script ([01-pipeline.md](01-pipeline.md#benchmark)).

### Bake profiles

The GI bake is most of the scene step. On Madrid r150 at 10 texels per metre (macOS, M5 Pro, `ProgressiveGPU` with no fallback, 5 pages, 17.8 M texels, 12 interior lights, 1 972 probes) it took 1 378.5 s of a 23 min 40 s step, with the samples now called `final`. One switch picks how hard the scene step bakes: `ONWARD_BAKE=draft|final` for `task unity`, `build_map.sh` or `run_export.sh`, or `--bake draft|final` on either script (`task unity -- --bake final`). The flag wins over the variable; an empty variable means `draft`. An unknown profile stops with exit 2 and the list, before Unity runs. `run_export.sh` passes it to Unity as `-onwardBake <profile>`; `BuildSceneFromPaths` (the Editor and MCP entry) takes it as an optional argument and bakes `final` without one. The list lives in `tools/unity/lib.sh` (`BAKE_PROFILES`) and in `BakeProfileFor` of `OnwardMapAdapter.cs`; a test keeps them equal. `onward-generator run` calls `run_export.sh` with the current environment, so `ONWARD_BAKE` applies there too.

| Setting | `draft` (default, iteration) | `final` (release builds) |
| --- | --- | --- |
| Direct samples | 32 | 256 |
| Indirect samples | 128 | 512 |
| Environment samples | 128 | 512 |
| Bounces (min–max) | 1–2 | 2–4 |
| Texels per metre, padding, page size, `scaleInLightmap`, directional mode, AO | the map's | the map's |
| Filtering | Advanced, OpenImage denoiser on direct, indirect and AO | the same |
| `prioritizeView` | off | off |

Build a map for release, or for a headset check of the lighting, with `final`: `ONWARD_BAKE=final task unity -- "<map>"`. `lightmap_bake.profile` in `build/scene_report.json` says which one a package has. Naming another profile (`--bake` or `ONWARD_BAKE`) on `task unity` bakes that package again, with no new `task map`: `run_export.sh <package> archive` first moves `build/` beside the package as `pkg_NN.build-<time>` and the Unity scene to `OnwardAdapterArchive/<package_id>-<time>` in the project, outside `Assets`, so nothing is overwritten or deleted. The archives stay until you remove them. With no profile named, a package keeps the bake it has, so `task unity` alone never downgrades a `final` build. The scene step itself still runs once per package. `onward-generator run` without `--no-unity` does not archive: use `task unity` to change the bake.

Why these numbers. Unity's [Progressive Lightmapper manual (2021.3)](https://docs.unity3d.com/2021.3/Documentation/Manual/progressive-lightmapper.html) says of indirect samples that "for some Scenes, especially outdoor Scenes, 100 samples should be enough", that more environment samples smooth the result "at the cost of increased bake times", that the default for max bounces is 2 and each bounce adds computation, and that a wider gap between min and max bounces adds noise. Its [Lighting Settings Asset page](https://docs.unity3d.com/2021.3/Documentation/Manual/class-LightingSettings.html) says a higher texel density "increases bake times". The [GPU lightmapper page](https://docs.unity3d.com/2022.3/Documentation/Manual/GPUProgressiveLightmapper.html) adds that the GPU backend needs up to four times more indirect samples than the CPU one for the same quality, so 128 on the GPU is noisy, and the OpenImage denoiser is what keeps a draft readable. `final` keeps the values of the Onward lightmapping guide (Aug 2023, lower end of the city range).

Measured with `final` on 5 November 2026, macOS, Apple M4 Pro, `ProgressiveGPU` with no fallback: Barcelona Sarrià r150 (core 100) at 10 texels per metre, 5 pages, 17.8 M texels, `gi_bake` 195.7 s, `astc` 17.9 s, scene step 4 min 22 s; Paris Rue Mouffetard r50 at 40 texels per metre, 24 pages (23 of 2048 px and one of 1024 px), 97.5 M texels, `gi_bake` 1 036.1 s, `astc` 38.9 s, scene step 18 min 41 s. The Madrid run above was on another host and an earlier build of the adapter, so it is not part of the same series.

Expected speed-up of `draft`, not yet measured. Path-traced work per texel grows with direct samples plus indirect samples times the path length plus environment samples. With paths of `min` to `max` bounces that is 1 792–2 816 rays for `final` and 288–416 for `draft`: about 6–7 times fewer for the same path depth, 4.3–9.8 times across the extremes. Light probes are sampled from the indirect count, so they scale the same way. The denoiser, the scene setup and the ASTC reimport do not shrink. On the Madrid r150 run above that predicts a `gi_bake` of roughly 200–300 s instead of 1 378.5 s, and a scene step of about 5–6 min instead of 23 min 40 s. Measure it with `report_timings.py` on two packages of the same map.

What changes on screen with `draft`: indirect light (the light bounced off facades into streets, courtyards and rooms) is noisier before denoising, so after it the soft shading is blotchier and contact darkening in corners is smoother or smeared. With at most 2 bounces, places that only light reaching after 3 or 4 bounces lights (interiors away from their lamp, deep arcades, narrow alleys) come out darker. Sky light is noisier. Direct sunlight and its shadow edges change little: 32 direct samples on a sun with no shadow angle, denoised, stay sharp. The lightmap pages, their sizes, the texel count and the memory on the headset are the same as with `final`, so a draft still tests packing, the lightmap budget and the bundle.

Considered and left out:

- **Lower texel density for `draft`.** It would cut the bake further (texels fall with the square of the density), but the draft would then pack different pages and hide the lightmap budget that `lightmap_estimate` and the headset check depend on.
- **Hard shadows on the interior point lights.** The adapter never sets a baked shadow radius or angle, so they stay at 0 and a `Soft` baked shadow already comes from a point source. `Hard` would not save samples.
- **`lightmapCompression`.** It stays `None` in both profiles: the adapter recompresses the pages to ASTC 8×8 afterwards (`astc`, 7.8 s on the run above), and compression is not part of the path tracing.
- **`prioritizeView`.** Off in both: batch mode has no Scene view to bake first. It does not change the result.

## Quest listing modes (experimental)

Decision: [ADR-007, amendment 1](decisions/ADR-007-onward-export-adapter.md#amendment-1-quest-listing-modes-experimental). **None of these modes is verified on the headset.** The default, `local`, is the install that loaded Hunt, and it does not change.

One switch picks the mode: `ONWARD_QUEST_LISTING=<mode>` for `task unity`, `build_map.sh` or `run_export.sh`, or `--listing <mode>` on either script (`task unity -- --listing <mode>`). The flag wins over the variable; an empty variable means `local`. An unknown mode stops with exit 2 and the list, before Unity or the headset is touched. The list lives in `tools/unity/lib.sh` (`QUEST_LISTINGS`) and in `CANDIDATE_LISTINGS` of `src/onward_generator/export/info3.py`; a test keeps them equal.

| Mode | Device files written | `ID` | `MapName` | `ThumbnailUrl` |
| --- | --- | --- | --- | --- |
| `local` | `unity-export.local.{content3,info3,png}` | `unity-export.local` | `Unity Export` | empty |
| `local-thumb` | same three | `unity-export.local` | `Unity Export` | `file:///sdcard/Android/data/com.downpourinteractive.onward/files/CustomContent/unity-export.local.png` |
| `named` | `onwardgen.<map id>.{content3,info3,png}`; the local slot is not touched | `onwardgen.<map id>` | the map title | empty |
| `named-thumb` | same as `named` | `onwardgen.<map id>` | the map title | `file://…/CustomContent/onwardgen.<map id>.png` |

For a mode other than `local`, `push` first writes a new listing under `build/listings/<mode>/<ID>.info3`, derived from the Launcher's `unity-export.local.info3`, which is read and never changed. The writer (`write_candidate_listing`) refuses when the source `ID` is not `unity-export.local`, when `BundleHash` is not the MD5 of the bundle that will be copied, when the title is empty or longer than 80 characters, when the map id does not match `[A-Za-z0-9_]{1,40}`, or when a different file already sits at the target (the same bytes are accepted, so a second push reuses it). The thumbnail must be a PNG of 512×256, the size the Custom Content Guide requires. `GameModes`, `EnvironmentType`, `BundleHash`, `Description` and `IsSanitized` stay as the local listing has them. The bundle bytes are the same in every mode; only the name of the copy changes.

Every mode backs up the files of both ids it may replace (`unity-export.local.*` and, for `named*`, `onwardgen.<map id>.*`) before copying, and deletes nothing. To go back, run `push` again with `local`, or copy the backup folder's files to `CustomContent` with `adb push`. A `named*` pair stays on the headset next to the local one until removed by hand (`adb shell rm …/CustomContent/onwardgen.<map id>.*`).

### Headset test

Nothing here is marked as passed. Write each result in the package's `quest_acceptance.md` observations or in the pull request.

1. Before changing anything: `tools/unity/onward_adapter/run_export.sh <package> inspect`. If a Workshop map was ever downloaded on the headset, its files show how the game names a downloaded map and its picture (look for a `.png`, `.jpg` or a thumbnail folder next to its `.info3`, and at its `ID` and `ThumbnailUrl`). Keep that folder; it is the best evidence for the next step.
2. `ONWARD_QUEST_LISTING=local-thumb task unity -- "<map>"`, answer `s`. Start Onward. In the custom map list: does `Unity Export` show the map's thumbnail instead of the generic picture? Does Free Roam load? Does Hunt appear and load?
3. `ONWARD_QUEST_LISTING=named task unity -- "<map>"`, answer `s`. Is a second card listed with the map's title? Does it load in Free Roam, and is Hunt listed? Is its picture the map's or the generic one? Is `Unity Export` still listed and still loadable? Then `adb shell ls …/CustomContent`: are both `onwardgen.<map id>.*` files still there after the game started (the game might remove an id it does not know)?
4. Only if 3 lists the card: `ONWARD_QUEST_LISTING=named-thumb task unity -- "<map>"` and the same questions about the picture.
5. `ONWARD_QUEST_LISTING=local task unity -- "<map>"` (or plain `task unity`) restores today's install of the local slot.

A mode that hides the map or breaks Hunt is reverted by step 5; nothing else needs to be undone except removing a `named*` pair by hand.

## Albedo textures

The adapter makes one texture per glTF image of `scene_visual.glb`. Materials that share an image (`build_glb` keeps identical bytes once, so the three trim materials of ADR-020 and a party wall and its wall) share that texture.

- **`astc_6x6` (default).** The PNG bytes are written into the project as `Textures/T<nn>.png` and imported with a `TextureImporter`: sRGB, mipmaps, alpha from the image, no power-of-two rescale, Repeat wrap when the GLB has more than one image (Clamp for a single shared atlas, as before), `maxTextureSize` at least 4096 and at least the image's larger side (the importer's default 2048 would halve the 2048 × 4096 facade atlas), and an Android override to ASTC 6×6. ASTC 6×6 is 16 bytes per 6×6 block, 3.56 bits per texel against 32 for RGBA32: the facade atlas goes from 42.7 MiB to 4.8 MiB with its mips. It is finer than the lightmaps' 8×8 because the albedo carries the windows, doors and cornices of the atlas. The Onward guide (appendix C) recommends ASTC.
- **`none`.** The old path: `Texture2D.LoadImage` into an uncompressed RGBA32 `Textures/T<nn>.asset` with mipmaps. Set `build.albedo_compression` to `none` in the export configuration to roll back a headset regression without a code change.

A package written before this option has no `albedo_compression` and gets ASTC 6×6. The scene log says `[OnwardAdapter] Albedo: <n> of <m> images, Android <format>.` `perf measure` reports `texture_memory` per GLB: every embedded image with its full mip chain as RGBA32 and as ASTC 6×6 (`rgba32_mips_bytes`/`_mib`, `astc_6x6_mips_bytes`/`_mib`); these are estimates from the image sizes, not a headset figure.

The ASTC path was written on a Linux host without Unity and is unverified in Unity and on the headset. To check: `task unity` on the macOS host builds; the atlas texture in the Unity project is 2048 × 4096 with format ASTC 6×6 on Android, not 2048 × 2048; the seams and gutters of the trim atlas at a distance; the string course and the window cells up close; the shop decals' letters; and texture memory in OVR Metrics against a `none` package of the same map.

## See-through materials

The adapter builds every material with `PP_Opaque_Simple_Lit` and its albedo, except these, which are transparent and drawn on both faces. The C# could not be compiled on the Linux host that wrote this change; the first `task unity` run is its build check.

| glTF material | Unity material |
| --- | --- |
| `glass_view` (interior windows) | `PP_Sprite_Lit`, the framed window PNG on `_MainTex`, render queue `Transparent` (3000), set on the material. The sprite graph reports 2000 in batchmode; a pane left at the shader's queue was drawn among the opaque meshes, so on the headset the room behind some windows was drawn over the pane and the window read as an empty hole. The scene log prints `glass_view: render queue 3000 (sprite shader reports 2000)`. The same queue is set on `shop_glass` when it falls back to `PP_Sprite_Lit` |
| `shop_glass` (shop display windows, `docs/07-detail.md`) | A copy of the first material named exactly `GlassVfx`, then `Glass`, under `Assets/OnwardCustomContent` (`AssetDatabase.FindAssets("<name> t:Material")`, file name compared again). `_Cull` is set to Off when the copy has it. The scene log says which: `[OnwardAdapter] shop_glass: SDK material <path> (shader <name>); _Cull set to Off`, or `...; no _Cull property: check from inside a shop that the glass shows`, or `shop_glass: no SDK material named GlassVfx or Glass under Assets/OnwardCustomContent; PP_Sprite_Lit with the generated tint`. The SDK asset is never edited. Whether the v1.15 package ships such a material was not checked on this host |
| `shop_decal_atlas_<page>` (shop names on shop glass) | `PP_Sprite_Lit`, the RGBA page on `_MainTex`, render queue one above the shop glass so the letters draw over it from the street: `Transparent + 1` (3001), or one above the SDK glass material when that is higher; the scene log prints the queue |

The horizon ring's `horizon_backdrop` is the one opaque exception: it is drawn with the SDK's `PP_Opaque_Unlit` graph, the gradient PNG on `_BaseMap` (ADR-014 amendment of 5 November 2026). Lit, it took only the ambient light and read as a slate wall darker than the fog.

Each of the see-through materials gets white vertex colours (`PP_Sprite_Lit` multiplies by vertex alpha). The shop glass and the decals cast no shadow (`ShadowCastingMode.Off`, on a combined renderer, or on a per-node renderer whose materials are all shop glass or decals), so the baked sun does not darken the shop behind the window. `glass_view` keeps its shadow, as before.

## Lighting

Everything is baked. No realtime light reaches the Quest.

- **Sun.** One directional light, `Baked`, intensity 1.2, soft shadows.
- **Out of the lightmap.** Renderers whose material is a tree, urban metal, a railing, an overgrow prop (`M_prop_*`), or the overgrow grass tufts and vines (`M_abandoned_grass`, `M_abandoned_vine`, since 5 November 2026: lightmapped, some 0.55 m tufts baked black on Barcelona Sarria), and since the same day the see-through glass (`M_glass_view`, `M_shop_glass`, drawn unlit, so its texels lit nothing) and the shop and street sign rims (`*_sign_edge`, 17 of the 18 overlapping-UV charts) take no lightmap space (`scaleInLightmap = 0`) and are lit by light probes (`receiveGI = LightProbes`): thin or small meshes baked black at the map's texel density. The shop furniture (`furnish/` nodes, ADR-031, off by default) uses the same `prop_<id>` materials, so it takes no lightmap texels either and depends on the room probes of ADR-013, or on the ambient light where a cell is combined; whether that reads on the headset is unchecked. They still contribute GI, so they cast the baked sun's shadows on the ground and the walls. Before 4 November 2026 they were left out of GI as well, and with every light baked nothing cast a shadow from a tree, a lamp, a railing or a prop. Rendered in Unity on MurciaEjemplo2, an aircon now throws its shadow on its wall and a row of street trees darkens the sidewalk under it; the lightmap stays at 5 pages. The horizon backdrop alone stays out of GI. A combined renderer of these materials takes the scene's ambient light (`lightProbeUsage = Off`), not the probes: Unity samples the probes once per renderer, at its bounds centre, and for the trees of a 100 m cell that point fell inside a building on 3 of 4 cells of MurciaEjemplo2, so a whole cell of trees went dark. Sampling at one of the trees instead put another cell in a building's shadow. With the ambient light every tree of a row has the same colour, a little flatter than a sunlit probe sample. Without combining (`combine_cell_m` 0) each renderer keeps its own probe sample. The black aircon was its UVs (`docs/01-pipeline.md`, library).
- **Lightmap scale by surface class.** Every other renderer bakes at the map's texels per metre times its class scale: roofs 0.25, ground (`ground`, `pedestrian`, `sidewalk`, `carriageway`, `curb`, `green`, `water`, `cobblestone_paving`, `road_marking`) 0.5, interiors (`interior_wall`, `interior_floor`, `stair`, and every `interior_*` material, such as the shop colours) 0.5, the scenic shells outside the playable core (every `scenic_*` material, ADR-017) 0.25, blank party walls (`party_*`, the wall left above a lower neighbour or beside a gap; `docs/07-detail.md`) 0.5, facades and everything else 1. Roofs are rarely seen from the street; the ground and the interiors take soft baked light, a blank party wall has no opening to read the light on, and the facades a player walks past keep the full density. A party wall hidden by its neighbour is not in the visual mesh at all. On the synthetic 94-building map of `docs/07-detail.md` at 10 texels per metre, the estimate went from 7 654 245 texels (facades 6 034 789) to 5 963 058 with the hidden walls gone and to 5 502 111 with the party walls at 0.5 (facades 3 729 006, party walls 153 649): 28 % fewer in all. `export package` resolves the classes to `lightmap_scales` in `adapter_options.json` (`[{material, scale}]`, scale 1 left out), and the adapter sets `scaleInLightmap` on each combined renderer from its glTF material. `build.lightmap_class_scales` in the export configuration replaces any of the six (`roof`, `ground`, `interior`, `scenic`, `party`, `facade`, each above 0 and at most 1). The trim-sheet facades ([ADR-020](decisions/ADR-020-trim-sheet-facades.md)) keep these classes by name: `facade_trim_<style>` is a facade (1), `party_facade_trim_<style>` a party wall (0.5) and `scenic_trim_<style>` a scenic shell (0.25); the three share one image. A package written before this change has no `lightmap_scales` and bakes everything at 1, as before. The classes live in `src/onward_generator/export/lightmap.py`.
- **Lightmap UV margin.** Each combined mesh is unwrapped with a pack margin that leaves 4 texels between its charts (`LightmapMarginTexels`): `packMargin = 4 / side`, where `side = sqrt(surface area) x texels per metre x scaleInLightmap` is about the mesh's square in the atlas in texels, never below Unity's default and at most 0.25. With the default margin, a fixed fraction of that square, 128 of 168 lit renderers on MurciaEjemplo had less than one texel between charts (roofs, stairs, party walls, interiors), and Unity reported 137 objects with overlapping UVs. Measured on 4 November 2026 on MurciaEjemplo2 (the same area and seed), one package baked twice: 137 objects with overlapping UVs with the default margin, 14 with this one, and still 5 lightmap pages of 2048 px. The 14 left are the rims of shop signs and street plates (`shop_sign_edge`, `street_sign_edge`), thin meshes that should not take lightmap space at all. Whether the seams are gone on the headset is not checked.
- **Lightmap estimate.** `onward_export.json` carries `lightmap_estimate`: per combined renderer (100 m cell and material, as the adapter groups them) its area times the squared texel density, capped at one page as Unity scales down a renderer that does not fit; `texels`, `texels_by_class`, `renderers_capped` and `pages_at_least`. Packing and padding are left out, so the page count is a lower bound. `export package` prints it. After the bake the Unity log prints `Lightmaps: N, T texels`, and `build/scene_report.json` adds `lightmap_texels`, so the two can be compared.
- **Tiers.** On a map with a playable core (ADR-017), `onward_export.json` carries `tiers`: `cell_m` (the card's `combine_cell_m`, or 100 m when it is 0), `draws` (the (cell, material) groups the adapter's combine leaves, with no culling; it counts glTF materials and does not apply the adapter's dedupe of identical materials, so Unity can build fewer) and, for `core`, `scenic` and `shared`, `visual_triangles` and `draws`. A node takes the `tier` of its building record; a node with none (ground, curbs, markings, props, signs, the backdrop) is `core` when its bounds lie in the core, `scenic` when they lie outside, `shared` when they cross the edge. A group holding two tiers counts once in each, so the tier draws can add up to more than `draws`. `null` without a core. `run` prints `draws` on the `export` line. These are predictions from the GLB, not a headset measurement (ADR-005).
- **Draws per view.** `onward_export.json` carries `view_draws`, the draw count to compare with the guide's ~150, since `tiers.draws` adds up every cell at once. Cameras stand every `step_m` (25 m) across the playable area, the core or else the layout's `site_bounds_m`, from its south-west corner, and turn through `yaws` (16) level headings, 0 degrees being east. A (cell, material) group of the combine is seen when its plan bounds meet the camera's `fov_deg` (110) wedge out to `far_m` (1000 m, the `far_clip_m` every playable layout is pinned to). It gives `views`, `median`, `p90`, `max` and the `worst` view (`x_m`, `y_m` in local metres, `yaw_deg`). There is no occlusion, so a building behind another still counts: it is the frustum culling the engine does, and an upper bound per view. Like `tiers.draws`, it counts glTF materials and leaves out the adapter's dedupe of identical ones. `null` when the layout has no `site_bounds_m` and the map has no core. `run` prints it on the `export` line. Measured on 3 November 2026 with the 100 m cell: madridr302 (core 150 m) median 108, p90 269, max 321; a murcia2033 twin (r120, no core) 95, 206 and 244. The worst views stand at a corner of the area and look across it. ADR-005 measured 96 and 67 draws on the headset where the unculled count was 217.
- **Interior lights** ([ADR-013](decisions/ADR-013-interior-light-anchors.md)). For each room in the visual GLB's `light_anchors`, one `Point` light, `Baked`, soft baked shadows, warm white, intensity `build.interior_light_intensity` (3.0 in `config/export/onward.default.json` and in the card `map create` writes; 0.8 when the key is absent), range from the anchor. At most `build.interior_lights_max` per map: 160 in `config/export/onward.default.json` and in the `config/export.json` that `map create` writes; 24 when the key is absent, as in a map created before 3 November 2026. Both keys are optional in the export configuration. `detail` already keeps at most `interiors.lights_max` (160) rooms lit, shops first and then nearest the map centre (`docs/07-detail.md`, *Shop interiors*); a room past it has probes and no `light_m`, and the adapter adds its probes and no light. When the visual GLB asks for more lights than `build.interior_lights_max`, `export package` warns (`the scene asks for N interior lights`), because the adapter would then cut in node order. Keep the two numbers equal.
- **Light probes.** One `LightProbeGroup`: the room probes from the anchors plus outdoor columns on the walkable navmesh every 12 m (wider on a large map, at most 1500 columns), at 1 m and 6 m. A column is kept only where the sky reaches its ground (the first collider down from 300 m is that ground) and no collider stands within 1 m of it from 1.2 m to 6 m up, triggers ignored; the log counts the others as `shut in`. Before 5 November 2026 a column took the nearest navmesh, a shop floor or a gap between two footprints included, baked black there, and Unity's probe interpolation carried that dark to the street next to it: on Barcelona Sarria 653 of 1 718 outdoor probes read below 0.05 upward and a ladder rendered black. With the filter 50 of 770 do. Each roof a ladder or a lift reaches also gets two probes, 1 and 3 m over the roof at the climb's roof point: the columns stop at 6 m, and the roof cabins took the dark of the room probes under them. Without a navmesh (`perf_layout`) no group is added and unlit renderers keep the ambient probe.
- `build/scene_report.json` counts `lights`, `light_probes`, `lightmaps` and `lightmap_texels`, and records the lightmapper and page sizes in `lightmap_bake` ([Unity timings](#unity-timings)).
- **Lightmaps after the scene opens.** When `LightmapSettings.lightmaps` is empty after the launcher opens the scene, the adapter assigns every `Lightmap-N_comp_light.exr` beside the scene, with no fixed limit, and each `Lightmap-N_comp_dir.png` as its direction map in directional mode. When only some pages have a direction map it warns and assigns them all non-directional.

## Horizon

[ADR-014](decisions/ADR-014-horizon.md).

- **Fog.** The adapter sets linear `RenderSettings` fog from `build.fog_enabled`, `build.fog_start_m`, `build.fog_end_m` and `build.fog_color` (`#RRGGBB`, sRGB). All four keys are optional; without `fog_enabled` there is no fog, as before. The default card uses 160–1000 m and `#C8CDD2`. `map create` writes start = radius + margin (the wall, seen from the centre) and end = start + 600 m. Fog is measured from the camera, not from the wall.
- **Backdrop ring.** Off by default for `bounds` alone, on in `run` (ADR-014 amendment): `onward-generator bounds --backdrop [--backdrop-offset-m 2] [--backdrop-height-m 30]`. One inward-facing quad per edge of the site's convex hull, just outside the wall, with a procedural haze-to-sky gradient (`horizon/backdrop`, material `horizon_backdrop`), drawn by the adapter with the SDK's `PP_Opaque_Unlit` graph so it shows its own colours under the fog. No building is drawn on it, no collider, no lightmap. 2 triangles per edge and one draw call. Recorded as `envelope.backdrop` and an object of `type: backdrop` in the scene manifest, and as `backdrop` in `bounds_report.json` (`null` when off). The listing thumbnail leaves it out. `onward-generator run` passes `--backdrop` (`pipeline_run.py`), so every map it builds has the ring; run `bounds` by hand without it to compare.
- **Not done.** Silhouettes of real OSM buildings outside the area. ADR-014 estimates their triangle cost.

Unknown, to check on the headset: whether the Onward runtime or the `UrbanDay1` post-processing profile changes fog, ambient light, the skybox (`UseBuiltInSkybox = true`), or how probes and extra baked lights are used.

## Known limits

- The local pair is always `unity-export.local.*`. The adapter passes the package title to `ContentBuilder.BuildScene`. The launcher script sets `MapName` to `Unity Export` and writes the description. The card holds at most 600 characters. A credit already in the map sentence is not repeated, and credits that still do not fit are left off the card. The package `attribution` list keeps every source. It sets `GameModes` to Free Roam (4), Hunt (10), and Uplink (1). When the layout is `gameplay_layout`, it sets `EnvironmentType` to Urban (2). It does not change `ID` or `BundleHash`. `ThumbnailUrl` in the local `.info3` stays empty: Onward fills it only when a map is published to the Workshop. The thumbnail is assigned to `SharedSettings.Thumbnail` and copied to the headset as `unity-export.local.png`. On the headset the card still shows a generic picture, so the game does not appear to read that file; the [listing modes](#quest-listing-modes-experimental) are the opt-in attempts to fix the name and the picture.
- The headset has one local slot. Two maps on disk are built separately. The headset holds the last one that was uploaded.
- A new map's layout is `onward_generator.gameplay_layout` 0.3.0. It describes Free Roam, Uplink, Assault, Escort, Hunt, Evac, and Retrieval. The phase-05 measurement fixture stays `perf_layout` and does not carry those modes. How the SDK guide is applied is in [11-onward-sdk.md](11-onward-sdk.md).
- On macOS and Linux, `OnwardLauncher.cs` must not rewrite the bundle path to backslashes. The patch removes only `.Replace('/', '\\')`. The original is `~/Unity/OnwardPhase0Project/OnwardLauncher.cs.sdk-v1.15.orig`, outside `Assets`. Reimporting the `.unitypackage` undoes the patch.
- The Quest reads the `AI_NavBake` JSON, not the `NavigationMap` object. `tool` applies `patch_content_builder.py`: the launcher copies the nodes, removes the baker from the bundle, and restores it in the Editor scene. The original is `ContentBuilder.cs.sdk-v1.15.orig`, outside `Assets`. Reimporting the package undoes the patch. The next `tool` puts it back.
- Copying to the headset replaces the previous local map, after a backup. There is never more than one local map at a time.
- Unity runs on the host. Running it in a container (GameCI `unityci/editor`) is not set up: it needs a Unity licence file and the SDK project on the machine.
