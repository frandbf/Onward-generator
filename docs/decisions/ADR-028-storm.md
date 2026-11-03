# ADR-028 — An optional storm on a night map

Status: proposed · 3 November 2026. Not compiled in Unity and not seen on the headset. Builds on [ADR-026](ADR-026-time-of-day.md).

## Context

- `LightningSystem` was on no map: the adapter did not reference it.
- SDK v1.15 (`LightningSystem.prefab` and `LightningSystem.cs`, read on the Mac): a standalone prefab with numbers, switches and two colours. It has no object references, unlike `FlyoverSystem`, which needs the MapQuad. `LightningChance` is 0.1 per 3 s interval, `ThunderSoundChance` 0.2.
- The guide says a daytime flash looks wrong because it reuses the night-vision effect, and suggests lowering the chance after a test.

## Decision

1. **`run --storm` and `map create --storm`**, kept as `storm` in `map.json`. `run --no-storm` removes it. A map without the key has no storm. On an existing map the option stores the value and says to run again with `--from layout`.
2. **Night only.** The map, the layout and `export package` refuse a storm on a day map. Changing a storm map to day without `--no-storm` is refused, not silently cleared.
3. **The layout carries `lightning`, 0 or 1.** The key is additive, like `street_lights_m`, and a missing one means none. The schema version stays.
4. **The prefab's values stay.** Nothing here has seen a flash, so a lower chance would be a guess.

## Rejected alternatives

- **A `--lightning-chance` option.** It would expose a number nobody has tried. Add it after a test on the headset, if the prefab's value is wrong.
- **A rain effect.** The request is lightning, and the SDK prefab has none.

## To check on the headset

- How often 0.1 per 3 s flashes in practice, and whether it gets tiring.
- `SkyColorNormal` and `SkyColorBright` are both transparent black on the prefab while `ShouldBrightenSkyOutdoors` is on. Does the sky brighten in a flash, or go black?
- Thunder over `Suburban Day`, the global ambience a night map keeps.
