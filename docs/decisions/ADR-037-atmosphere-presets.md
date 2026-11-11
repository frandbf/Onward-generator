# ADR-037 — Atmosphere presets: the baked light of a day map

Status: proposed · 10 November 2026. Plans E0 to E3 and E6 of [the atmosphere plan](../plans/2026-11-10-atmosphere.md): the setting, fixed views to compare bakes, headset test packages of the SDK environment, and the sun across the streets. It stays proposed until a headset session has compared a preset with `neutral` (plan E11).

## Context

- Every day map has the same light: one baked directional light at 50° elevation and −30° azimuth, intensity 1.2, the default skybox ambient, and the fog of ADR-014 (`OnwardMapAdapter.cs`, the sun after the import phase).
- Two official Onward maps used as references read warm and contrasted: a sun between 30° and 50° (the shadows of their vehicles are short), warm light on one side of the street and cool shade on the other, haze toward the horizon.
- Light is baked, so it costs nothing per frame on the Quest and adds no triangle. Madrid Sol Arena stands at 244 836 of the 250 000 triangles the maintainer allows (ADR-036).
- SDK v1.15 (`LevelDescriptor.cs`) has `ETimeOfDay` `Day`, `Night`, `Twilight` and twelve `EProfileId`; the pipeline uses `Day`/`UrbanDay1` and `Night`/`UrbanNight1` (ADR-026). `SharedLevelSettings` has `UseBuiltInSkybox` (true) and `SkyColor` (unused). What each profile looks like is decided by the game client.

## Decision

1. **One key, `atmosphere`, in `map.json`.** `neutral` (or no key) is today's light. `map create --atmosphere` and `run --atmosphere` set it; `run --atmosphere neutral` removes the key. On an existing map the option stores the value and says to run again with `--from export`.
2. **Light only.** A preset sets the sun, the ambient, the ambient occlusion, the fog, the lightmap density of the playable core and the wall palette of the atlas. Content (conflict dressing, smoke, parked cars) gets its own keys, if ever ([part 2 of the plan](../plans/2026-11-10-atmosphere-advanced.md)).
3. **Day only.** The map, `run` and `export package` refuse a preset other than `neutral` on a night map, and refuse turning a map with a preset to night, as a storm is refused by day (ADR-028).
4. **Numbers in a versioned file.** `config/atmosphere.default.json`, validated by `config/atmosphere.schema.json`, holds `golden_hour` and `overcast`. `neutral` is not a preset of the file. The adapter will read numbers in a `lighting` block of `adapter_options.json` and never a preset name.
5. **Byte for byte.** Without a preset the package is the one of before, `package_id` included. With one, `export package` writes `atmosphere` in `adapter_options.json` (it enters `package_id`) and an `atmosphere` block in the manifest: `preset`, `origin: generated` and `applied`, the parts of the preset the package carries. Plan E3 applies the sun: a `lighting` block with elevation, azimuth (where the sun stands), colour, intensity and baked shadow angle. The adapter turns the light toward the azimuth plus 180°; the neutral light, heading −30°, is a sun at 150°. Plan E6 resolves a `streets` azimuth: the dominant heading of the core's facade walls, modulo 90°, is the street axis, and the sun stands 35° off it, at the one of four such azimuths nearest 150°, so every street has a sunny and a shaded side and the sun stays roughly where it was.
6. **Fixed views to compare bakes (plan E1).** `run_export.sh PACKAGE views` renders four eye-level PNGs of the baked scene from cameras that depend only on the layout, so the packages of one map can be compared without the headset. It writes under `build/views/` and never into the package.
7. **Test packages of the SDK environment (plan E2).** The export card's `build.experimental_environment` replaces the time of day (`Day` or `Twilight`) and the post-process profile of a day gameplay layout, and may set `SkyColor`. The package is marked untested and warned against publishing; `ENVIRONMENT_PROFILES` grows only with what a headset session has seen.
8. **Starting values from the references, not from the proposal.** The sun starts at 30° for `golden_hour`: a 12° to 18° sun casts shadows three to five times the height of a building and hides an enemy in the shade.

## Rejected alternatives

- **New values of `time_of_day`** (`golden_hour`, `dusk`). The SDK's `Twilight` look is unknown; it would tie the light to an SDK enum before the headset has shown what it does.
- **One key for light and content** (a `smoke_dusk` preset with barriers and smoke). Light is free and reversible; barriers change routes and cover, and must be judged with the gameplay metrics of ADR-032.
- **Presets in the export card.** The card is per package; the atmosphere is a choice about the map, like the time of day.

## To check on the headset

- Whether the built-in sky under a warm bake looks wrong, and whether a profile, `Twilight` or `SkyColor` changes it (plan E2, E4).
- Whether the Onward runtime keeps `RenderSettings.fog`, the ambient mode and the ambient colours.
- Whether a soldier in the shade stays readable with the trilight ambient (plan E5).
