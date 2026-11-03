# ADR-028 — An optional storm on a night map

Status: proposed · 3 November 2026. Not compiled in Unity and not seen on the headset. Builds on [ADR-026](ADR-026-time-of-day.md).

## Context

- `LightningSystem` was on no map: the adapter did not reference it.
- SDK v1.15 (`LightningSystem.prefab` and `LightningSystem.cs`, read on the Mac): a standalone prefab with numbers, switches and two colours. It has no object references, unlike `FlyoverSystem`, which needs the MapQuad. `LightningChance` is the chance that lightning affects the map, per match (its tooltip). The prefab has 0.1 and `LightningChanceLight` 0. `ExampleScene` sets both to 1.
- The guide says a daytime flash looks wrong because it reuses the night-vision effect.

## Decision

1. **`run --storm` and `map create --storm`**, kept as `storm` in `map.json`. `run --no-storm` removes it. A map without the key has no storm. On an existing map the option stores the value and says to run again with `--from layout`.
2. **Night only.** The map, the layout and `export package` refuse a storm on a day map. Changing a storm map to day without `--no-storm` is refused, not silently cleared.
3. **The layout carries `lightning`, 0 or 1.** The key is additive, like `street_lights_m`, and a missing one means none. The schema version stays.
4. **Both chances are 1, as in `ExampleScene`.** With the prefab's 0.1, nine matches in ten of a map asked to have a storm would have none. The other values stay as on the prefab.

## Rejected alternatives

- **A `--lightning-chance` option.** Asking for a storm means wanting one. Add it if someone wants a storm only in some matches.
- **A rain effect.** The request is lightning, and the SDK prefab has none.

## To check on the headset

- How often it flashes during a match (`IntervalDuration` 3 s, `FlashSequenceChance` 0.3), and whether it gets tiring.
- `SkyColorNormal` and `SkyColorBright` are both transparent black on the prefab while `ShouldBrightenSkyOutdoors` is on. Does the sky brighten in a flash, or go black?
- Thunder over `Suburban Day`, the global ambience a night map keeps.
