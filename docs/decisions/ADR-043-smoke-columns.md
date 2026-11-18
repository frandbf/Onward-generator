# ADR-043: smoke columns over wrecks

Status: proposed, 15 November 2026. Pending a headset check: the frame rate with the columns in view, and whether the Onward client plays particles in a published map.

## Context

The maintainer asked for smoke in a few places of a war-torn map, "without turning the whole city into a permanent fire" (plan [2026-11-15-ambiente-belico](../plans/2026-11-15-ambiente-belico.md), increment C). Part 2 of the atmosphere plan (C10) set aside static smoke billboards, which read flat in stereo, and left open whether the SDK allowed anything better. The SDK v1.15 ships particle shaders of its own (`PP_Particle_Simple_Lit`, `PP_Particle_UnLit_Dither`, `PP_Particle_Unlit` and their additive variants), so a `ParticleSystem` stays within the rule of PP_ shaders only. The Custom Content Guide warns that transparency is expensive on the Quest's tiled renderer.

## Options

1. Smoke at ground level, across the streets: it would hide players from players only, since bots see through particles.
2. Columns that rise from wrecks and thin out above the roofs: atmosphere seen from far off, without an asymmetric screen at eye level.
3. A smoke-painted sky: needs `UseBuiltInSkybox = false`, still unknown in the SDK.

## Decision

Option 2. `map.json` `"smoke": N` (0 to 5; absent or 0 writes nothing, so the package is the one of before) makes `run` pass `export package --smoke N`. `export/smoke.py` takes the wreck records of the scene (a procedural wreck, or a vehicle placed with the wreck role, pinned props included), in an order fixed by their ids, and keeps up to N inside the playable core, at least 15 m from every spawn point and objective, 15 m from each other and 3 m from a ladder or lift foot. Each column starts 1 m over its wreck. `adapter_options.json` gains a `smoke` block of numbers (height 16 m, 4 particles a second living 10 s, 2 to 8 m across, a 0.5 m/s drift, colour `#4A4640` at 0.6 alpha, at most 40 particles, the columns) and the package a `smoke_texture` file, `smoke_puff.png`, a soft 128 px puff drawn by code (CC0). `onward_export.json` records `smoke` (wanted, columns, refusals); fewer columns than asked is a warning.

The adapter (`AddSmoke`) makes one `ParticleSystem` per column with `PP_Particle_Simple_Lit` (Base Map `_MainTex`, `_BaseColor` white, alpha clip `_Cutoff` 0.02): a looping, prewarmed upward cone of 8°, world-space, fading in and out over its life, billboards sorted by distance, no shadows, lit by the light probes, and not static, so nothing is baked. A package with columns and no texture, or a texture and no columns, is refused.

## Cost

Five columns hold at most 200 billboards, all transparent. No triangles are added to the scene budget, and colliders and navigation do not change.

## Not checked here

On the headset: the frame rate (at least 72 fps median, as measured on Murcia), how the columns read against the sky, and whether a published map keeps them. If the cost is too high, `PP_Particle_UnLit_Dither` (dithered, no blending) is the next step, then fewer columns.

## Amendment of 17 November 2026: fire under the smoke

Plan [2026-11-17-efectos-sdk](../plans/2026-11-17-efectos-sdk.md), increment E. A column of smoke rose from nothing; now it can have a fire at its foot.

- **When.** `map.json` `"fire": true` (`export package --fire`; absent or `false` writes nothing). It burns only under smoke columns, so a map that asks for fire without `smoke` gets a warning and the package of before. Fire stays off by default, so a map that already has smoke does not change until it asks.
- **Where.** A site 0.6 m above the foot of each column, 1.6 m over the wreck's base: on the roof of a 1.55 m wreck, where the flames show (a first try at 0.4 m under the foot burnt inside the wreck, out of sight). `adapter_options.json` gains a `fire` block (the numbers of the flames and the embers, and the sites), and the package `fire_flame.png`, a 64 px upright flame drawn by code (CC0, role `fire_texture`). `onward_export.json` records `fire` (`sites`).
- **Adapter.** `AddFire` draws two `ParticleSystem` per site on the SDK's `PP_Particle_Unlit`: flames (20 a second living 0.6 s, 0.4 to 1 m, rising at 1.5 m/s in a 12° cone, at most 16) and embers (3 a second living 2.5 s, 4 to 7 cm, in a 25° cone, at most 8), coloured from yellow-orange to red and fading in and out. Unlit and without light probes, shadows or a real-time light: the fire is its own light, and a light that flickers cannot be baked. A package with fire sites and no flame texture, or the other way round, is refused. The first headset-free render tried `PP_Particle_Unlit_Additive`: over a daylight sky the additive flames washed out to white, so the fire uses the alpha-blended `PP_Particle_Unlit`, and its sprite carries the yellow-to-red colour.
- **Cost.** At most 24 more billboards a column, 120 with five columns, alpha-blended like the smoke and small on screen. No triangle enters the scene budget. Like the smoke, the frame rate with a fire in view is still to be measured on the headset.


## Amendment of 18 November 2026: a particle pack's flipbooks

The smoke puff and the flame are drawn by code. After the dust motes drawn the same way were withdrawn (ADR-047), the maintainer brought Unity's *Particle Pack Starter Assets*. `asset-store particles` copies its textures to a local catalogue (ADR-035 amendment of the same day).

- **When.** On by default whenever the local catalogue has the role and the map has a smoke column. `map.json` `"pack_particles": false` (`export package --no-pack-particles`) keeps the drawn textures. Without a catalogue, with `ONWARD_LOCAL_CATALOGUES=0`, or without a column, nothing changes and the pack is not even looked for.
- **Smoke.** With the `smoke` role, `smoke_puff.png` is the pack's `SmokePuff04`, 25 white puffs on a 5 × 5 grid. The `smoke` block of `adapter_options.json` gains `sheet` (`tiles`), and each puff plays the grid once over its life. The colour, sizes and timings stay the column's own.
- **Flames.** With the `flame` role, the package adds `fire_flame_sheet.png` (role `fire_sheet_texture`): the pack's `MediumFlame01`, 100 frames on a 10 × 10 grid. The `fire` block gains `sheet` (`texture`, `tiles`, `pivot_y` 0.5), and the flames take `SHEET_FLAMES`, after the pack's own `MediumFlames`:
  - 3 a second living 3 s, 0.9 to 1.25 m, still (speed 0) in a 25° cone, at most 12;
  - white, because the frames carry the colour;
  - the billboard is lifted by half its size, because each frame's flame stands on its foot;
  - the site stands 0.2 m under the column's foot, 0.8 m over the wreck's base, on the top of the wreck's body. At the sprite's 0.6 m over the foot, pkg_24 showed on the headset a gap between the wreck and the flames: their foot floated 0.2 m over the cabin and 0.8 m over the body. The drawn sprite keeps 0.6 m: it is centred on its site and rises, and lower it burns inside the wreck.

  The embers keep the drawn sprite and their own material.
- **Record.** `onward_export.json` `smoke.texture` and `fire.texture` record the origin (`asset_store`), the role, the catalogue path, the SHA-256, the grid, the licence and the credit. Each file's `source` is that catalogue path.
- **Adapter.** `ApplySheet` turns on the particle system's texture sheet animation: a grid, the whole sheet, frame over time from 0 to 1, one cycle. It also sets the renderer's pivot. The shaders stay `PP_Particle_Simple_Lit` and `PP_Particle_Unlit`. A package with a flame flipbook and no `fire_sheet_texture`, or the other way round, is refused. A grid outside 1 to 16 tiles, or a pivot outside 0 to 1, is refused too.
- **Cost.** Fewer flames than before (about 9 alive, 12 at most, against 16), each larger. The 2048 px flipbook adds one texture of about 2.7 MB in ASTC 6×6 with mips. The look and the frame rate are still to be checked on the headset.
- **Licence.** Unity Asset Store EULA: the textures go into a built map, never into the repository.
