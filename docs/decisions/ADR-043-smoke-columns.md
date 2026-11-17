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
- **Where.** A site 0.4 m under the foot of each column, on the wreck's top. `adapter_options.json` gains a `fire` block (the numbers of the flames and the embers, and the sites), and the package `fire_flame.png`, a 64 px upright flame drawn by code (CC0, role `fire_texture`). `onward_export.json` records `fire` (`sites`).
- **Adapter.** `AddFire` draws two `ParticleSystem` per site on the SDK's `PP_Particle_Unlit_Additive`: flames (20 a second living 0.6 s, 0.4 to 1 m, rising at 1.5 m/s in a 12° cone, at most 16) and embers (3 a second living 2.5 s, 4 to 7 cm, in a 25° cone, at most 8), coloured from yellow-orange to red and fading in and out. Unlit and without light probes, shadows or a real-time light: the fire is its own light, and a light that flickers cannot be baked. A package with fire sites and no flame texture, or the other way round, is refused.
- **Cost.** At most 24 more billboards a column, 120 with five columns, additive and small on screen. No triangle enters the scene budget. Like the smoke, the frame rate with a fire in view is still to be measured on the headset.

