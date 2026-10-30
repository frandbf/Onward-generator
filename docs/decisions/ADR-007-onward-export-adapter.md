# ADR-007 — Export adapter: a verified package plus Unity and the SDK (phase 07)

Status: proposed (the Quest acceptance test is still open).

## Context

The only validated Onward map is the `.content3` / `.info3` pair from the Onward Launcher, inside a Unity 2021.3.23f1 project with SDK v1.15.

In phase 05, `MurciaPerfBuilder` loaded the cell-combined Murcia scene on a Quest 3. That tool measures. It is not the adapter.

Phase 07 requires export through a verified adapter. The adapter must show real compatibility on the device before `device_verified` can be true. The adapter stays isolated from the generation engine, and final artifacts are not overwritten without confirmation.

## Decision

1. **The contract between the two halves is an immutable package:** copies of the GLBs and the layout, options, and a manifest with the SHA-256 of every file. Python writes it after validating the inputs. C# verifies it before building. The generation engine does not depend on the adapter.
2. **The Unity half reuses the code that already ran on the headset:** the GLB reader, the axis change, the combine by cell and material, and the SDK prefabs. `MurciaPerfBuilder` stays unchanged as the measurement tool.
3. **Cell size, batching, and occlusion culling are the measured v02** (ADR-005): a 100 m cell, no static batching, no occlusion culling. Export lightmaps follow the Onward lightmap guide: 10 texels/m by default, 2048 px, non-directional. A map can raise texels per metre up to 40 in `config/export.json`. The `config/perf/` files keep the 2 texels/m of that measurement.
4. **Nothing is overwritten:** the package, the Unity scene folder, or the Launcher output. A headset copy requires `CONFIRM_PUSH=yes` and takes a backup first.
5. **`export.enabled: true` is a warning** (`export_adapter_unverified`) until the adapter passes the acceptance test. See `docs/02-configuration.md`.
6. **Versions are pinned** in the adapter configuration and in C#: `onward_unity` 0.1.0, Unity 2021.3.23f1, SDK 1.15, Quest. A mismatch builds nothing. A test compares the C# constants with the configuration.

The local card name stays `Unity Export`. The display title lives in the map folder and in `config/export.json`. The filename and the id stay `unity-export.local`.

## Amendment 1: Quest listing modes (experimental)

Status: proposed, not verified on the headset.

**Problem.** On the Quest the map is listed as `Unity Export` with a generic picture. Setting `MapName` to the map title in the local `.info3` hid the map and it could not be loaded, so `apply_local_listing` writes `Unity Export` back (the C# launcher writes the title; the Python pass replaces it).

**Evidence.**

- The *Onward Custom Content Guide* (patch 1.8.7, guide 2.0.8; [Google Doc](https://docs.google.com/document/d/1rwbBvO2qKBK-gR_GImbFl1V3nXVvoeMXpfXJE1itOpA), [offline PDF](https://www.onwardthegame.com/doc/CustomContentGuide.pdf), section *Testing your maps*) says a map built with the Onward Launcher "will be labeled as 'Unity Export'", and that on Quest the Launcher files are copied by hand into `Android/data/com.downpourinteractive.onward/files/CustomContent`. The name on the local test card is the SDK's design, not a bug in this adapter.
- The same guide requires a 512×256 thumbnail for publishing ("No exceptions"). `thumbnail.png` and the fallback `apocalipsis_murciana.png` are 512×256 PNG.
- The SDK v1.15 Launcher writes `CachedWorkshopItem { IsSanitized, WorkshopItem }` with `WorkshopItem` fields `ID`, `MapName`, `Description`, `GameModes`, `EnvironmentType`, `BundleHash` (MD5 of the `.content3`), `ThumbnailUrl` (empty) and `AvgRating`.
- No public source found (web search, October 2026) describes how the Quest client caches a downloaded Workshop map, how it names or loads its thumbnail, or whether the local slot is recognised by `ID` or by `MapName`. The guide sends questions to the Onward Discord, which was not reachable from here. The game is not decompiled: that would be extracting proprietary code.
- Headset history in this repository: Hunt appeared on the local card only with `MapName` `Unity Export` and `GameModes` `[4, 10, 1]` (Onward 2.0.4). The repository history was squashed into one public commit, so the earlier attempts are recorded only in the documents.

**Decision.** Today's install stays the default (`local`). Three candidates are opt-in behind one switch, `ONWARD_QUEST_LISTING` (or `--listing`), and change only the `.info3` that `push` copies and the names of the copies:

1. `local-thumb`: the local slot with `ThumbnailUrl` set to the `file://` path of its PNG on the headset. Hypothesis: the list loads the picture from `ThumbnailUrl`, and Unity's web request accepts `file://` on Android.
2. `named`: a second pair, `onwardgen.<map id>`, with the map title as `MapName`, beside the untouched local slot. Hypothesis: the restriction is on the local id, and another id is listed like a downloaded map.
3. `named-thumb`: 2 and 1 together. Kept separate so a failure points at one cause.

Every mode backs up the files it may replace and deletes none. `push` still needs `CONFIRM_PUSH=yes`. The `inspect` step copies the headset's `.info3` listings read-only, so a downloaded Workshop map can be compared with the local one. The test steps are in `docs/12-export.md` (*Headset test*). No C# changes: the Launcher output stays as it is, and the candidates are derived from it.

**Risks.** The game may ignore or delete an id it does not know, may refuse a `file://` URL, or may check `BundleHash` against a server for a non-local id. Each of these shows as the card missing or the generic picture, and `local` restores the working install.

**When verified.** The mode that works becomes the default in its own change, with the headset result recorded. The others are removed.

## Rejected alternatives

- **Use `MurciaPerfBuilder` as the adapter.** It does not verify its inputs, it reuses variant names, and it is not separate from measurement.
- **Write the Unity scene from Python** (`.unity` or `.prefab`). That would depend on unvalidated Unity and SDK file formats.
- **Export FBX or OBJ.** Those are not Onward maps.

## Consequences

- The first adapter build matched the measured v02 Editor counts (247 627 triangles, 234 renderers, 376 colliders). Headset compatibility is the acceptance test. It is not assumed.
- A new SDK or Unity version means raising the adapter version and repeating the headset test.
