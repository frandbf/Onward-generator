# ADR-046: Soundscape by mood

Status: proposed · 17 November 2026. Plan [2026-11-17-efectos-sdk](../plans/2026-11-17-efectos-sdk.md), increment D. Pending a headset check: that the distant combat does not cover footsteps and shots, and that fire, flies and the generator are heard only near their source.

## Context

Every map so far sounds alike: `Suburban Day` or `Suburban Night` as the global ambience, `Birds Park` round the Uplink, `Wind Rooftop` on the reached roofs and a hall's reverb by the main landmark ([11-onward-sdk.md](../11-onward-sdk.md#sound-and-flyover)). A city at war, a wired East African street or a desert town sound the same as a calm Spanish square. The SDK v1.15 lists more clips in `OnwardAudioDataStrings` than the generator uses, among them `Distant Combat Sparse` and `Dense`, `Fire`, `Flies`, `Engine Generator`, `Birds African Bush 1` and `2` and `Wind Desert`. They ship with the game: an `OnwardAudioSource` names one, so nothing is added to the package and there is no licence to record. `OnwardCustomAudioSource`, which takes a clip of our own, stays unused.

## Decision

1. **Modes.** `map.json` `"soundscape"`: `auto` (the default), `default`, `regional`, `war` or `battle`. Any other value is refused with `MapError`.
   - `auto` follows the map's war damage (`signs.damage`): `heavy` is `war`, anything else `regional`.
   - `default` keeps the sound of before.
   - The key always wins over `auto`, so any map can ask for any mode.
2. **What each mode does.** `config/soundscape.json` (schema `config/soundscape.schema.json`) says it, never a map:
   - `regional` swaps the birds and the roof wind for the clips of the map's facade style (`regional`: `east_african` hears `Birds African Bush 1` and `Wind Desert`, `arid` hears `Wind Desert`; a style without a row keeps `Birds Park` and `Wind Rooftop`), and sets one `Engine Generator` by a power pole (ADR-045), 15 m clear of the spawns and objectives.
   - `war` adds `Distant Combat Sparse` as the global ambience, `Fire` 0.5 m above the foot of each smoke column (ADR-043) and `Flies` 0.8 m above up to four wrecks or piles of rubbish (`flies_kinds`), 10 m clear of the spawns and 20 m apart.
   - `battle` is `war` with `Distant Combat Dense`. It is never chosen by `auto`.
   - The schema lists every clip name the SDK ships, so a misspelt clip stops the export.
3. **Where.** In `export package`, which holds every site: the smoke columns, the scene's wrecks, rubbish and poles, and the layout's spawns.
   - `export package --soundscape MODE --facade-style STYLE`. `run` resolves `auto` with the map's damage and passes the mode and the style that materials uses. It passes nothing for `default`.
   - The plan put this in `perf layout`. It moved to export so the gameplay layout's contract, and its check that `audio_global` is `Suburban Day` or `Suburban Night`, stay unchanged.
4. **Package.** `adapter_options.json` gains a `soundscape` block (mode, global, birds, wind and the local sources with their clip and position) only when the mode changes something. A map in a style without a row, without poles and without war keeps its package id. `onward_export.json` records `soundscape`: mode, style, the clips and how many sources of each kind stood.
5. **Adapter.** `ApplySoundscape` runs after the playable layer. It renames the clip of the `Ambience`, `Birds`, `TowerWind` and `RoofWind_*` sources where the block names one, and places each local source as an `OnwardAudioSource` of type `LocalAmbience`. A local source's radius is fixed by its clip in the SDK.

## Consequences

- No triangle, texture or memory is added. A war map gains up to ten local sources.
- Murcia, Madrid or Paris without damage, poles or a regional row are unchanged. Mogadishu (heavy damage, `east_african`, wired) hears distant combat, African birds, desert wind, fire under its three smoke columns, flies by four wrecks and a generator.
- Whether the combat clip is too loud, and how far each local clip carries, is known only on the headset. The numbers (counts, distances, heights) are configuration and can be tuned without code.

## Amendment of 18 November 2026: louder flies, on the rubbish first

On Mogadishu's pkg_24 the maintainer found the flies too quiet and asked for them by the bins. `OnwardAudioSource` has only a type and a clip name: the client plays each clip at its own level and range (10 m for `Flies`), so no volume can be set.

- **Copies.** Each `local` row of `config/soundscape.json` gains `copies` (1 to 3): the clip is placed that many times on the same site. The extra sources are named `<Kind>_<n>_2`, `<Kind>_<n>_3`. `Flies` takes 2, so it should play louder; the fire and the generator keep 1. How much louder is known only on the headset.
- **Rubbish first.** `flies_first` (`trash`, `can`, `barrel`) lists the roles of the `flies_kinds` records placed before the others. The order inside each group is still fixed by the ids. A wreck carries flies only when there are not enough bins, cans and barrels.
- **More of them.** `Flies` goes from 4 sites 20 m apart to 8 sites 12 m apart. A war map gains up to 22 local sources instead of 10.
