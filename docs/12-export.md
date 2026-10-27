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
PYTHONPATH=src .venv/bin/python -m onward_generator export package \
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
- the layout is neither `onward_generator.perf_layout` nor `onward_generator.gameplay_layout`, or it lacks 8 spawn points per team (the SDK prefab count), Uplink, a map quad, a positive `level_size_m`, or the damage volume;
- a `gameplay_layout` lacks 32 AI points (one per route, 1 m from the start, separated by `ai_separation_m` or 8 m), 4 boxes, routes of 2 points, `Suburban Day`, Urban/Day/`UrbanDay1`, far clip 1000, and flyover;
- a `gameplay_layout` 0.3.0 lacks the mode codes, the second spawn block for each team, the two Escort starts, the Evac point, or 3 Retrieval hard drives;
- the adapter configuration does not match its schema;
- the output directory already exists. Packages are never overwritten. The next one is `pkg_02`.

| File | Contents |
| --- | --- |
| `onward_export.json` | Manifest `onward_generator.onward_export_package` 0.1.0: `package_id`, adapter and target (Unity 2021.3.23f1, SDK 1.15, Quest), title and description with attribution, the declared fidelity, SHA-256 and origin of every file, input hashes, and warnings |
| `scene_visual.glb`, `scene_collision.glb` | Exact copies of the scene directory |
| `layout.json` | A copy of the gameplay layout |
| `adapter_options.json` | Combine by a 100 m cell, no static batching, no occlusion culling (variant v02, ADR-005). Onward lightmap guide: texels per metre from the map (default 10, at most 40), 2048 px, non-directional. The bake uses 256/512/512 samples, 2–4 bounces, and ASTC 8×8 on Android |
| `thumbnail.png` | 512×256, a view of the mesh and that map's title |
| `quest_acceptance.md` | The headset checklist: Free Roam, Uplink, Hunt, ambience, impact, flyover, and the urban card |

`package_id` is the map id plus the first ten characters of a hash of the files, the options, and the map data. The same content always produces the same id.

`onward-generator export verify PACKAGE` recomputes every hash.

## Unity build

```bash
tools/unity/onward_adapter/run_export.sh maps/<id>/data/processed/export/pkg_01 tool scene launcher
CONFIRM_PUSH=yes tools/unity/onward_adapter/run_export.sh maps/<id>/data/processed/export/pkg_01 push
```

`onward-generator run` calls `tool scene launcher` after the Python stages and then asks before `push`. Only `s` or `si` uploads.

| Step | Effect |
| --- | --- |
| `tool` | Copies `OnwardMapAdapter.cs` into the Unity 2021.3.23f1 project that holds SDK v1.15 and the macOS Launcher patch |
| `scene` | Verifies the package in Python and in C# (SHA-256, adapter name and version, Unity version). Builds the scene under `Assets/OnwardAdapter/<package_id>/`: visuals combined by cell and material, collision as `MeshCollider` with no renderer, a baked directional light, spawns, Uplink, Free Roam, map quad, tent, damage volume, and a `LevelDescriptor` with the title and attribution. Writes `build/scene_report.json`. Refuses if that folder already exists |
| `launcher` | Calls the Onward Launcher for Quest. Writes `build/launcher/unity-export.local.{content3,info3}`, `build/content3.md5`, and `build/launcher.sha256`. Never overwrites |
| `push` | **Only with `CONFIRM_PUSH=yes`.** Backs up the local map already on the headset (`build/device_backup/`), copies the new one, checks the MD5 on the device, and records it in `build/push.log` |

Unity logs stay in `~/Library/Logs/onward-export/`. The Editor must be closed before `scene` starts. A newer Unity editor rewrites the project and 2021.3.23f1 can no longer open it.

## Known limits

- The local pair is always `unity-export.local.*`. The adapter passes the package title to `ContentBuilder.BuildScene`. The launcher script sets `MapName` to `Unity Export` and confirms the description. It sets `GameModes` to Free Roam (4), Hunt (10), and Uplink (1). When the layout is `gameplay_layout`, it sets `EnvironmentType` to Urban (2). It does not change `ID` or `BundleHash`. `ThumbnailUrl` in the local `.info3` stays empty: Onward fills it only when a map is published to the Workshop. The thumbnail is assigned to `SharedSettings.Thumbnail` and copied to the headset as `unity-export.local.png`.
- The headset has one local slot. Two maps on disk are built separately. The headset holds the last one that was uploaded.
- A new map's layout is `onward_generator.gameplay_layout` 0.3.0. It describes Free Roam, Uplink, Assault, Escort, Hunt, Evac, and Retrieval. The phase-05 measurement fixture stays `perf_layout` and does not carry those modes. How the SDK guide is applied is in [11-onward-sdk.md](11-onward-sdk.md).
- On macOS, `OnwardLauncher.cs` must not rewrite the bundle path to backslashes. The patch removes only `.Replace('/', '\\')`. The original is `~/Unity/OnwardPhase0Project/OnwardLauncher.cs.sdk-v1.15.orig`, outside `Assets`. Reimporting the `.unitypackage` undoes the patch.
- The Quest reads the `AI_NavBake` JSON, not the `NavigationMap` object. `tool` applies `patch_content_builder.py`: the launcher copies the nodes, removes the baker from the bundle, and restores it in the Editor scene. The original is `ContentBuilder.cs.sdk-v1.15.orig`, outside `Assets`. Reimporting the package undoes the patch. The next `tool` puts it back.
- Copying to the headset replaces the previous local map, after a backup. There is never more than one local map at a time.
