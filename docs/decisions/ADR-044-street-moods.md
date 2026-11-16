# ADR-044: Street moods

Status: proposed · 16 November 2026. Plan [2026-11-16-ambientes-de-calle](../plans/2026-11-16-ambientes-de-calle.md), increment 1.

## Context

`overgrow` was written for an abandoned or fought-over city. Its year profiles (10, 30, 100) scale grass, vines, wild trees, car wrecks and bushes; its props come in any condition; and `signs` draws soot, bullet holes and moss on any map. A street in daily use, such as Calle Sierpes in Seville, came out with vines, wrecked cars, concrete barriers and containers. The street profile's `props_profile` only orders props by condition, so a wrecked prop still stood.

## Decision

1. `config/street_moods.json`, validated by `config/street_moods.schema.json`, names the moods as data. Each one has:
   - factors on the profile's `vines`, on the trees overgrow plants where OSM maps none (`planted_trees`) and on `wrecks`;
   - the library prop `conditions` it keeps (a prop without a condition is kept; `null` keeps all);
   - factors on the rule caps of prop `categories`;
   - the wall decal kinds it leaves out (`wall_decals_excluded`).
2. `map.json` `"street_mood"` names one (`MapError` for an unknown name or a non-string).
   - `run` passes `overgrow place --street-mood`.
   - `signs place` reads the key itself, as it reads `damage` and `atmosphere`. An unreadable `map.json` reads as no key, as for the other switches.
3. The default is `abandoned`, the identity. A map without the key builds byte for byte what it built before, and the reports gain `street_mood` only for another mood.
4. The mood is applied to the year profile (`moods.apply_mood`). `Profile` gains `planted` (a factor on generated trees only) and `categories` (factors in `rule_factor`). The placement code is otherwise unchanged.
5. Mapped trees are never removed, and war damage (`damage`) still adds its own decal kinds.
6. Shipped moods:
   - `lived`: no growth or wrecks, intact props only; furniture and plants up; barriers, rubbish, vehicles and site structures down; no soot, bullets, moss, rust, peeling, cracks or chalk.
   - `emptied`: no growth, a third of the wrecks, intact or worn props; barriers and rubbish halved; no soot, bullets or moss.
   - `abandoned`: as before.

## Consequences

- A new mood is a table row, with no code.
- The library still has little outdoor furniture. Terraces, awnings and the street profile offering the mood are later increments of the plan.
- A `lived` map spends less of its triangle budget on props until those increments land.
- `street_mood` is not yet in the street profile schema: a map gets a mood only from its own `map.json`.
