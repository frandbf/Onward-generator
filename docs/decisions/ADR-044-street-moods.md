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
- The library still has little outdoor furniture. Awnings and the street profile offering the mood are later increments of the plan.
- A `lived` map spends less of its triangle budget on props until those increments land.
- `street_mood` is not yet in the street profile schema: a map gets a mood only from its own `map.json`.

## Amendment of 16 November 2026: café terraces (plan increment 2)

A mood now also says whether it sets café terraces (`"terraces"`, required; schema 0.2.0). Only `lived` does.

- `config/terraces.json` (schema `config/terraces.schema.json`) holds the rules as data: the `detail` shop categories that set one (`bar`, which covers OSM cafés, bars, pubs and restaurants), the `highway` values of a pedestrian street (`pedestrian`, `living_street`), the passage kept free (1.2 m), the gaps, how far along the wall a terrace may run (6 m from the door), tables per shop (2) and per map (24), and the catalogue rows of each slot.
- `overgrow/terraces.py` reads each such shop's street door from the `interior` records, finds the footprint edge it stands in and sets units along it, alternating right and left of the door: a table with a chair on each side, then a menu board and a planter. A unit may run past the shop's own footprint along the facades in line with it, but never round a corner or across a side street (the 30 cm behind it must be building).
- A unit stands only before a pedestrian street (the nearest street within 12 m) or in a pedestrian area, where the passage stays free to the building across, inside the zone, away from spawns, other doors, `detail`'s urban elements, the pinned props and the other units, within a quarter of the triangle headroom. The street props and wrecks placed after keep off it.
- The pieces are rows of the allow-listed CC0 interior catalogues, the first installed id of each slot: Quaternius round table and chair, Poly Haven chalkboard, Kenney potted plant. No new asset was needed. A table, a board and a planter get a box collider; a chair stays visual.
- Nodes are `overgrow/terrace/<unit>/<piece>`, combined per ground cell in Unity like the street props; the manifest records each piece as an `overgrowth` object of kind `terrace` with its `size_m`, and `overgrow_report.json` gains a `terraces` block (shops, tables, pieces, colliders, triangles, models, why units were skipped, missing models) only for a mood that sets terraces.
- On Calle Sierpes (Seville) the rule finds 9 bars and sets 7 tables before 5 of them; the others are kept back by spawns, neighbouring doors, a facade that turns a corner or a street that is not pedestrian.
