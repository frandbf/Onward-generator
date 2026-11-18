# ADR-047: Dust in the air

Status: proposed · 17 November 2026. Plan [2026-11-17-efectos-sdk](../plans/2026-11-17-efectos-sdk.md), increment F. Pending a headset check: that the motes read without bothering, that the dither does not shimmer in stereo, and the frame rate.

## Context

A dry, dusty city (Mogadishu, Fallujah, a map under the `dusty` light) has a haze from its fog (ADR-037) and sand on the ground (ADR-038), but the air itself is still. The SDK v1.15 ships `PP_Particle_UnLit_Dither`: an opaque particle shader with a dithered cut-out, so its particles are not blended and add no overdraw from transparency, which the Custom Content Guide warns is expensive on the Quest.

## Decision

1. **When.** `map.json` `"dust"`: `auto` (the default), `light` or `off`. Any other value is refused with `MapError`.
   - `auto` is `light` on a map whose light preset or facade style `config/dust.json` lists (`dusty`; `arid` and `east_african`), and `off` otherwise.
   - The key always wins, so any map can have dust or be kept clean.
   - `export package --dust auto|light|off` with `--facade-style`. `run` passes the map's value (`auto` without the key).
2. **Where.** Up to `volumes` (6) emission boxes stand on the points of the layout's AI routes, which follow the walkable streets.
   - They stay inside the playable core, `apart_m` (30 m) from each other, in an order fixed by a hash of the points.
   - Each box is `box_m` (30 × 30 × 5.5 m) with its floor `base_m` (0.5 m) over the layout's ground.
   - A layout without routes gets no dust, and a warning.
3. **Look.** Each box holds at most `max_particles` (60) motes of 3 to 10 cm, born at `rate_per_s` (7.5) and living `lifetime_s` (8 s), fading in and out.
   - They drift at `drift_m_s` (0.5 m/s) on one wind per map: a heading fixed by a hash of the map's id.
   - Their colour is the light preset's fog colour, else `color` (`#C9B48E`, sand).
   - The sprite, `dust_mote.png`, is a 32 px soft dot drawn by code (CC0, role `dust_texture`).
4. **Package.** `adapter_options.json` gains a `dust` block (the look, the wind and the volumes) only when the map gets dust and the layout has a route, so a map without dust keeps its package id. `onward_export.json` records `dust`: the choice, whether it was wanted, the style, the preset and the volumes.
5. **Adapter.** `AddDust` makes one `ParticleSystem` per volume:
   - a box shape, with world-space velocity along the wind and a slight bob;
   - on `PP_Particle_UnLit_Dither` (`_BaseMap`, `_BaseColor`, `_AlphaClipping` 0.3);
   - unlit, without shadows or light probes, not static.
   - A package with volumes and no mote texture, or a texture and no volumes, is refused.
6. **Left out.** Gusts of sand at eye level. Bots see through particles, so a gust would hide a player from a player only.

## Consequences

- At most 360 motes on a map, a few centimetres each, opaque. No triangle enters the scene budget, and colliders and navigation do not change.
- Murcia, Madrid and Paris without the `dusty` light are unchanged. Mogadishu (`east_african`) gets six volumes.
- How visible the motes are, and whether the dither shimmers, is known only on the headset. The numbers are configuration and can be tuned without code.
