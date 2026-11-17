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
- The library still has little outdoor furniture. The street profile offering the mood is a later increment of the plan.
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

## Amendment of 16 November 2026: awnings and parasols (plan increment 3)

A mood now also says whether it hangs awnings (`"awnings"`, required; schema 0.3.0). Only `lived` does.

- `config/awnings.json` (schema `config/awnings.schema.json`) holds the rules: the share of shops with a fascia that get one (0.7, by a hash of the seed and the shop), the depth out of the wall (0.9 m), the slope (at most 0.35 m, at least 0.1 m), the valance (0.2 m), the head clearance (2.2 m), the gap under the fascia and the open ground kept beyond it.
- `overgrow/awnings.py` hangs each awning just under the fascia board `detail` recorded over the shop's window, as wide as the board. Under a low fascia the canopy slopes less, so its lowest edge stays at 2.2 m; one that cannot slope 0.1 m is left out. It is left out, too, outside the zone, where the building across is too near, and where it would cover a street name plate, a wall decal or another shop's sign. A blade sign has no board over the window, so its shop gets none. The wall props placed after it keep off it as off a sign.
- `config/terraces.json` gains `parasol` (radius 1.1 m, rim at 2.15 m, apex at 2.5 m, a 5 cm pole; `null` for none): each table stands under one, in one colour per bar, and the table and its parasol fit the budget together or not at all.
- `overgrow/cloth.py` builds both from code: a two-sided sloped canopy with a valance and two cheeks (12 triangles; each side on its own copy of the vertices, since a face and its turned copy sharing vertices sum their normals to nothing and Unity draws the cloth black), and an eight-sided cone on a square pole (32 triangles). Both draw one generated material, `awning_fabric`: three bands of red, green or blue stripes on cream, and a grey band for the poles, the stripes repeating once a metre. No model is downloaded. Neither collides. The Unity adapter lights the cloth by light probes, not the lightmap, as it does grass and vines: the two faces of a thin cloth would share texels.
- Nodes are `overgrow/awning/<n>` and `overgrow/terrace/<unit>/parasol`; the manifest records each as an `overgrowth` object (kind `awning`, or kind `terrace` role `parasol`) with its `size_m`, and `overgrow_report.json` gains an `awnings` block and `terraces.parasols` only for a mood that sets them.
- On Calle Sierpes (Seville) 34 of 61 fascias get an awning (408 triangles) and the 7 tables their parasols; 17 shops are left out by the share, 7 outside the zone, 2 under a fascia too low and 1 where it would cover a sign.
