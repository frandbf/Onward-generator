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
