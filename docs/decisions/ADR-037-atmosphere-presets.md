# ADR-037 — Atmosphere presets: the baked light of a day map

Status: accepted · 11 November 2026 (proposed 10 November 2026). Plans E0 to E3, E5 to E8 and E10 of [the atmosphere plan](../plans/2026-11-10-atmosphere.md): the setting, fixed views to compare bakes, headset test packages of the SDK environment, and the sun across the streets. Accepted after the headset sessions of plans E4 and E11 below.

## Context

- Every day map has the same light: one baked directional light at 50° elevation and −30° azimuth, intensity 1.2, the default skybox ambient, and the fog of ADR-014 (`OnwardMapAdapter.cs`, the sun after the import phase).
- Two official Onward maps used as references read warm and contrasted: a sun between 30° and 50° (the shadows of their vehicles are short), warm light on one side of the street and cool shade on the other, haze toward the horizon.
- Light is baked, so it costs nothing per frame on the Quest and adds no triangle. Madrid Sol Arena stands at 244 836 of the 250 000 triangles the maintainer allows (ADR-036).
- SDK v1.15 (`LevelDescriptor.cs`) has `ETimeOfDay` `Day`, `Night`, `Twilight` and twelve `EProfileId`; the pipeline uses `Day`/`UrbanDay1` and `Night`/`UrbanNight1` (ADR-026). `SharedLevelSettings` has `UseBuiltInSkybox` (true) and `SkyColor` (unused). What each profile looks like is decided by the game client.

## Decision

1. **One key, `atmosphere`, in `map.json`.** `neutral` (or no key) is today's light. `map create --atmosphere` and `run --atmosphere` set it; `run --atmosphere neutral` removes the key. On an existing map the option stores the value and says to run again with `--from materials`, where the facade atlas takes the preset's palette (plan E8).
2. **Light only.** A preset sets the sun, the ambient, the ambient occlusion, the fog, the lightmap density of the playable core and the wall palette of the atlas. Content (conflict dressing, smoke, parked cars) gets its own keys, if ever ([part 2 of the plan](../plans/2026-11-10-atmosphere-advanced.md)).
3. **Day only.** The map, `run` and `export package` refuse a preset other than `neutral` on a night map, and refuse turning a map with a preset to night, as a storm is refused by day (ADR-028).
4. **Numbers in a versioned file.** `config/atmosphere.default.json`, validated by `config/atmosphere.schema.json`, holds `golden_hour` and `overcast`. `neutral` is not a preset of the file. The adapter will read numbers in a `lighting` block of `adapter_options.json` and never a preset name.
5. **Byte for byte.** Without a preset the package is the one of before, `package_id` included. With one, `export package` writes `atmosphere` in `adapter_options.json` (it enters `package_id`) and an `atmosphere` block in the manifest: `preset`, `origin: generated` and `applied`, the parts of the preset the package carries. Plan E3 applies the sun: a `lighting` block with elevation, azimuth (where the sun stands), colour, intensity and baked shadow angle. The adapter turns the light toward the azimuth plus 180°; the neutral light, heading −30°, is a sun at 150°. Plan E6 resolves a `streets` azimuth: the dominant heading of the core's facade walls, modulo 90°, is the street axis, and the sun stands 35° off it, at the one of four such azimuths nearest 150°, so every street has a sunny and a shaded side and the sun stays roughly where it was. Plan E5 adds the ambient (`trilight` sky, equator and ground colours, or `flat`) and the AO distance and exponents of the bake to the same block; `applied` is then `sun`, `ambient`, `ao`. Plan E7 gives the map's fog the preset's colour when the card has fog on (`applied` gains `fog`); the start and end stay the map's, from its wall (ADR-014): a fixed distance per preset would fog the playable area of a large map and never show on a small one. Plan E8 is the first to change `materials`: `materials apply --atmosphere` scales the style's grime by the preset's `grime`, desaturates each palette wall colour by `wall_desaturation` and caps its brightest sRGB channel at `wall_value_max` before the atlas is baked; observed colours are not on the atlas and stay as they are. `facades.atmosphere` in `materials_report.json` records it. Plan E10 bakes the combined cells wholly inside the playable core at the preset's `lightmap_texels_per_m_core` (`lightmap_core` in the adapter options, a scale over the card's density), and the page budget counts them, so the budget still wins.
6. **Fixed views to compare bakes (plan E1).** `run_export.sh PACKAGE views` renders four eye-level PNGs of the baked scene from cameras that depend only on the layout, so the packages of one map can be compared without the headset. It writes under `build/views/` and never into the package.
7. **Test packages of the SDK environment (plan E2).** The export card's `build.experimental_environment` replaces the time of day (`Day` or `Twilight`) and the post-process profile of a day gameplay layout, and may set `SkyColor`. The package is marked untested and warned against publishing; `ENVIRONMENT_PROFILES` grows only with what a headset session has seen.
8. **Starting values from the references, not from the proposal.** The sun starts at 30° for `golden_hour`: a 12° to 18° sun casts shadows three to five times the height of a building and hides an enemy in the shade.

## Rejected alternatives

- **New values of `time_of_day`** (`golden_hour`, `dusk`). The SDK's `Twilight` look is unknown; it would tie the light to an SDK enum before the headset has shown what it does.
- **One key for light and content** (a `smoke_dusk` preset with barriers and smoke). Light is free and reversible; barriers change routes and cover, and must be judged with the gameplay metrics of ADR-032.
- **Presets in the export card.** The card is per package; the atmosphere is a choice about the map, like the time of day.

## Headset session (plan E4, 11 November 2026)

Madrid Sol Arena on a Quest 3, the user's own judgement, one package at a time in the local slot:

| Package | Light | SDK environment | Seen |
|---|---|---|---|
| `pkg_15` | `golden_hour`, sun at 30°, azimuth 119.09° (streets) | `Day` / `UrbanDay1` | looks good |
| `pkg_16` | the same | `Twilight` / `UrbanDay1` | looks good |
| `pkg_17` | the same | `Day` / `UrbanDay2`, `SkyColor #E8B890` | looks good, no change from `pkg_16` |

Decisions: `golden_hour` keeps `Day` / `UrbanDay1`, since neither `Twilight`, `UrbanDay2` nor `SkyColor` changed anything the user could see; no `dusk` preset; `ENVIRONMENT_PROFILES` gains nothing; the built-in sky under the warm bake did not look wrong, so a custom sky (part 2) is not urgent. The sun's values stay as they are.

## Headset session (plans E5 to E10, 11 November 2026)

The same map and slot, each package adding one increment to `pkg_15`; every one judged good by the user:

| Package | Adds | Bake |
|---|---|---|
| `pkg_18` | trilight ambient and AO (E5): the darkest quarter of each view 7 to 9 grey levels brighter | 2 min 02 s |
| `pkg_19` | the preset's fog colour (E7) | 2 min 04 s |
| `pkg_20` | the wall palette of the atlas (E8): mean wall luminance 0.7083 → 0.5589 | 2 min 15 s |
| `pkg_21` | the core at 14 texels/m (E10): 7 lightmap pages instead of 5, 32 MB instead of 30 | 2 min 48 s |

## Measured session (plan E11, 11 November 2026)

`pkg_21` (the whole `golden_hour` preset) on the Quest 3, five minutes of play read from the headset's `VrApi` log (one line a second) while the user walked the core, both spawns and the Uplink:

| | In the map (319 s) | After the first minute (252 s) |
|---|---|---|
| Median FPS | 72 | 72 |
| Seconds under 72 | 31 | 12 |
| Lowest second | 7 (loading) | 43 |
| GPU, median / max | 54 % / 78 % | 55 % / 78 % |
| CPU, median / max | 42 % / 92 % | 41 % / 92 % |
| Least free system memory | 1734 MB | 1734 MB |

- **FPS:** the median of 72 meets the checklist. The deep drops are the map load and the loadout screen; the CPU peaks are the game's, since a preset only changes baked data.
- **Draws:** not in the log. The package's offline estimate over every view is a median of 111 and a p90 of 227, above the 150 of the checklist, but a preset adds no draw: the same map without one has the same count. Lowering it belongs to part 2 of the plan (P0, freeing geometry), not to this ADR.
- **Graphics memory:** not in the log either. The lightmaps take about 7 MB in ASTC and the bundle 32 MB, and the system never had less than 1.7 GB free.

With the FPS met and the draws unchanged by a preset, the maintainer accepted the ADR on 11 November 2026.

## To check on the headset

- Whether the Onward runtime keeps `RenderSettings.fog`, the ambient mode and the ambient colours: the user saw the warm fog and the lighter shade of `pkg_19`, so the bake and the fog reach the client.
- Whether a soldier in the shade stays readable with the trilight ambient (plan E5): yes, by the user's judgement on `pkg_18`, 11 November 2026.
