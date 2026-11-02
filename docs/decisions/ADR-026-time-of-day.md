# ADR-026 — Day or night per map, with lit street lamps

Status: proposed · 1 November 2026. Not compiled in Unity and not seen on the headset. Amends the day-only rule of [docs/11-onward-sdk.md](../11-onward-sdk.md). Builds on [ADR-013](ADR-013-interior-light-anchors.md) (baked lights).

## Context

- A map was always day: the layout wrote `Urban` / `Day` / `UrbanDay1`, `export package` refused anything else, and the adapter passed `ETimeOfDay.Day` to the SDK and baked one sun.
- SDK v1.15 (`LevelDescriptor.cs`, read on the Mac): `ETimeOfDay` has `Day`, `Night`, `Twilight`; `EProfileId` has `UrbanNight1` (8) and `UrbanNight2` (9). The profile is an enumerated id in `SharedLevelSettings`, sent inside the scene, and the SDK holds no asset or code that applies it: its look is in the game client.
- Exported materials are `PP_Opaque_Simple_Lit` with an albedo only, and realtime lights cost every frame (ADR-013).

## Decision

1. **`run --time-of-day day|night`**, kept as `time_of_day` in `map.json`. A map without the key is a day map. On an existing map the option stores the value and says to run again with `--from layout`: the layout and a new package are what change, not the city.
2. **SDK names.** `perf layout --playable --time-of-day` writes `Night` and `UrbanNight1`. `export package` accepts exactly the day and night pairs. `UrbanNight2` is not offered: nothing here can tell the two apart.
3. **A baked moon.** The adapter turns the sun into a faint blue light (intensity 0.12), sets a dark flat ambient and lowers reflections, so the bake does not light the city from a bright sky. The values are a first guess for the headset.
4. **Street lamps are baked point lights.** `street_lights_m` in the layout, flat `x, y, z` triples, 1 m along each lamp's arm and 4.2 m up, warm, range 16 m, with soft baked shadows, nearest the focus first, at most 64. No triangle, material or draw call is added; they cost bake time and lightmap data.
5. **Audio.** The global ambience stays `Suburban Day`: no night clip name is known yet, and a wrong name is not checked until the game. `Birds Park` is left out at night.
6. **Listing.** The adapter passes the map's time of day to `ContentBuilder.BuildScene`. The environment there stays `Desert`, as before this change, although the scene says `Urban`; that is a separate question.

## Rejected alternatives

- **Realtime lamps.** They would cost every frame on the Quest.
- **An emissive lamp head.** Materials are albedo only, so the bulb itself does not glow; the light it casts is baked into the street.
- **A custom night skybox.** The skybox stays the built-in one (ADR-014). Whether the game darkens it for a night profile is the first thing to check.
- **`Twilight`.** There is no twilight profile in the SDK.

## Consequences

- A day map's layout gains only an empty `street_lights_m` and is otherwise the same.
- Layout keeps its schema version: `street_lights_m` is additive, and the adapter treats a missing one as none.
- Interior lights keep their cap of 24 (ADR-013), so rooms past it are darker at night than by day.
- Props and urban metal stay out of the lightmap (`docs/08-dress.md`), so a lamp post is lit by probes, not by its own light.

## To check on the headset

- The sky: does the built-in skybox go dark with `UrbanNight1`? If it stays bright, this needs another answer.
- The moon, ambient and lamp values: can a player see, and do the lamps read as pools of light?
- `Suburban Day` at night: is there a better clip in the game's library?
- Bake time with 64 more lights, and whether Onward keeps the baked lamps.
