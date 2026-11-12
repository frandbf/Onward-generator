# ADR-010 — Overgrow: the model names gestures, code places them

Status: accepted · 20 October 2026

## Context

`overgrow` places props with fixed rules. A second mode, switched on when the map is created, lets a local model compose the dressing: a fence across a gap that still leaves a passage, a stair along a facade, cameras toward an OSM point. The mode has to work for any centre the map has already projected to local metres. ADR-006 still holds: the model does not execute code, does not touch the area or the export, and Ollama speaks only to loopback.

## Decision

1. **The switch is the existing `--ai` flag, stored as `ambient_ai`.** Without it, the rules do not change. With it, `overgrow place` does not ask the model for a `street_blocking` boolean.
2. **The model fills `config/overgrow_plan.schema.json` (0.1.0).** At most 24 actions: `span`, `cover`, `watch`, `place`. Ids come from that run's digest and from the installed library. The schema has no city list and no fixed asset list.
3. **The digest ignores the frame and the origin.** It reads `features` from geo: facades, corners, ways, and points or areas tagged `amenity`, `leisure`, `shop`, `tourism`, or `historic`. The prompt uses abstract examples.
4. **Code places the copies.** It repeats, scales uniformly, reserves the passage, and drops a whole gesture that does not fit. Grass, vines, and OSM trees still follow the profile. The prop grid and wreck boxes are not also spawned when the plan is valid.
5. **A failure is retried once before the rules.** If Ollama does not answer or the JSON does not validate, the second call quotes that failure. Rules are used only when that attempt also fails. The raw reply, the digest hash, and the instruction hash are stored in `scene_overgrow/ambient_plan.json`. A later run with the same digest and brief reuses that reply.
6. **`intent.schema.json` is not widened.** It remains the phase-06 contract.

The installed library is every `manifest.json` in a direct child of `assets` (`assets/normalized`, `assets/kenney`, and any catalogue added later). See `docs/abandoned-assets.md`. A `prop_selection.json` limits a run to installed ids. Without it, the whole catalogue is eligible. Since 2026-10-30 each library row in the prompt also carries the curated `description`, `placement`, `grouping`, `condition` and `tags` of a 0.2.0 manifest (prompt 0.2.0). `kit_only` rows never reach the prompt, a `span` needs a `run` and a `cover` needs a `grid`; code enforces both.

## Rejected alternatives

- Sending the GLB to the model. It does not fit a local model and cannot be validated.
- Letting the model send matrices or free metres. Code could not tell a gesture from an invented coordinate.
- A separate "tidy city" profile besides 10, 30, and 100. The profile only changes density. The map's sentence does not replace the library.

## Consequences

- A new city does not need new code. A new gesture needs a schema change, a realizer, and tests.
- An existing `scene_overgrow` is not rebuilt on its own. Resume `run` from `overgrow`.
- Reproducibility is the stored file, not a second call to the model.

## Amendment (8 November 2026): the street profile may choose the years and the props, behind an option

Status: proposed until a headset session checks a map with the option on (plan 2026-11-07, increment D2). Off by default.

`overgrow_plan.schema.json` stays at 0.1.0 and `intent.schema.json` is not widened (§6). With `map.json` `"street_profile": true` (`run --street-profile`), a separate contract, `config/street_profile.schema.json` (ADR-006 amendment of the same day), may set two existing `overgrow place` flags from enumerated ids:

- `abandonment` `years_10`, `years_30` or `years_100` replaces the `--profile 100` that `run` passes. Before this amendment "the profile does not change because of the map's sentence"; with the option on, it may, but only to one of the three existing profiles.
- `props_profile` `intact`, `worn` or `wrecked` writes `config/street_profile_selection.json` (the `prop_selection` format) with the library ids whose curated `condition` is in the group (intact; worn and damaged; damaged, wrecked and burned), plus the rows without a condition, and passes it as `--selection`. An empty group passes nothing (reason `props_profile_empty`); a map with its own `config/prop_selection.json` keeps it (reason `user_setting`).

The resolution follows §5: two attempts, the second quoting the first failure, then the neutral profile with the reason stored; a later run with the same inputs replays the stored reply. The overgrow placement plan of this record is unchanged and still optional. `overgrow_report.json` gains a `street_profile` block (ids, provenance `ai_generated` or `estimated`, the reply's hash) only when `--street-profile` is given. Off, the command and every output are as before.

## Amendment (12 November 2026): a prompt that always fits, and ids the model cannot invent

Status: accepted (plan Q9 follow-up).

- **Context.** The first map with `--ai` run from Docker (plan Q9) fell back to the rules: the prompt needed about 70 000 tokens and the provider allows 32 768. The library had grown to 399 fitted props, 265 of them from local Asset Store catalogues (ADR-035), about 56 000 tokens of rows. With the prompt cut to size, `qwen3:14b` then named `anchor_id: "ground"`, the library's anchor field, in all 24 actions.
- **Decision.** (1) The library rows a prompt carries are capped at 14 000 tokens. Over the cap, a deterministic subset takes one prop of each role in turn, a run or a grid first, then a prop sharing a word of four letters or more with the brief, then by the SHA-256 of its id; the model may use only the offered ids. `ambient_plan.json` records `library: {offered, fitted}`. (2) The schema sent as Ollama's `format` enumerates this run's ids per gesture: offered asset ids (a span only of `run` or uncurated rows, a cover only of `grid` or uncurated rows) and the digest's corner, facade, point and lane ids; a gesture with nothing to name is left out. Ollama samples only what that schema allows. The reply is still parsed against the plain schema and bound by `bind_plan`, so decision 4 of this record holds.
- **Consequences.** On Rue Mouffetard (399 fitted props, 102 offered, about 24 600 prompt tokens) the model returned a plan that binds on the first attempt, in about two minutes on an Apple M4 Pro. A large library is no longer offered whole, so a prop may not be offered to one map; the brief brings matching props forward. The instruction hash changes for any library over the cap.
- **Follow-up.** The same map rerun while the machine also ran a build timed out twice at the provider's default 180 s and fell back to the rules. The overgrow call now waits up to 600 s (`OVERGROW_TIMEOUT_S`); the street profile and the texture plan keep 180 s, their prompts are small.

## Amendment (12 November 2026): ids inside the zone, two copies per anchor, and no empty plan

Status: accepted.

- **Context.** Rue Mouffetard rerun from `main` with `--ai`: the model answered within the 600 s and its plan bound, but all 24 actions were `place` on one lane anchor, and that anchor lay outside the overgrow zone (the 55 m block square). Every action was dropped as `outside_zone`, the plan counted as accepted, so the rules did not run, and the map got no props. The digest offered the ids nearest the map centre without looking at the zone, so it offered ids the placement always refuses.
- **Decision.** (1) `build_digest(zone=)` leaves out facades (by their middle), corners, points and lane anchors outside the run's zone before the caps; `compose` passes `site.zone`. (2) `bind_plan` refuses more than `MAX_PER_ANCHOR` = 2 `place` actions on one lane anchor (`crowded_anchor`), and the system prompt says so (prompt version 0.3.0). (3) A plan none of whose actions could be placed is a failed attempt: the second call quotes `no action could be placed (<reason> <count>, ...)`, and after two such attempts the rules run, as for any other failure. A stored reply that places nothing is not replayed.
- **Consequences.** The digest hash and the instruction hash change for every map, so stored replies are asked again once. A reply the code cannot use no longer leaves a map without props.

## Amendment (12 November 2026): the model picks the props, the rules place them

Status: accepted.

- **Context.** With the zone and anchor fixes above, Rue Mouffetard was run three times with `--ai`. `qwen3:14b` put every `place` action on one lane anchor each time, even when the retry quoted `crowded_anchor` and asked it to spread them, so the rules placed every prop. The model chooses objects that fit a brief well; it does not lay out space.
- **Decision.** `overgrow` no longer asks for a placement plan. With `ambient_ai`, `overgrow/pick.py` asks the model to name 6 to 20 asset ids of the offered library, of at least 3 roles (all of them, or all its roles, when the library offers fewer), and a mood sentence. Ollama's `format` enumerates the offered ids, `uniqueItems`, and the bounds. The reply is parsed and checked again in code. `place.overgrow` then hands the rules only those props: the rules decide every position, the spacing, the passage and the keep-outs, as without `--ai`; the procedural wrecks, trees and growth are unchanged. Two attempts, the second quoting the failure, then the rules with the whole library. `ambient_plan.json` stores the pick (`schema` `onward_generator.overgrow_ambient_pick`, `asset_ids`, `mood`, `attempts`, `library`); a later run with the same instruction hash replays it. `overgrow_report.json` reports `ambient_mode` `ai_pick` and `ambient_pick` (the ids); `compose_attempts` and `compose_fallback` describe the pick, and `compose_actions` is 0.
- **Consequences.** The prompt carries no digest, about 15 000 tokens fewer. Gestures (span, cover, watch) are not requested any more; `realize.compose` and the plan schema stay for the stored records and their tests, and could be removed later. The model's choice changes which props stand on a map, never where.
