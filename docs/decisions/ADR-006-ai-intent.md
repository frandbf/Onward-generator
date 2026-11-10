# ADR-006 — AI: a bounded intent, applied by deterministic code (phase 06)

Status: accepted (phase 06 accepted on 2026-10-19).

## Context

Phase 06 turns a natural-language instruction into a valid, reproducible generator configuration. The model cannot silently change the area, the coordinates, the sources, the performance limits, or the export adapter.

The project contract (`generator.schema.json`) has general fields. Storeys and interiors live in `heights.default.json` and `detail.default.json`.

AI is optional and local. External providers are off. Code returned by a model is never executed.

## Decision

1. **The model fills only an intent** (`config/intent.schema.json`, 0.1.0): four bounded nullable parameters, plus the requests it did not apply and a summary. It does not emit configuration or code.
2. **Deterministic code translates the intent** into changes on copies of the three base configurations. Each change is recorded with the previous value, the new value, the effect (`geometry` or `recorded_only`), and the cost.
3. **Protected fields have two barriers.** They are absent from the schema, and a whitelist of JSON pointers rejects any other difference. Outputs are validated again with the existing loaders, and the resolved area must be unchanged.
4. **Performance.** The reference is the configuration measured in phase 05. With priority `max`, any cost increase is an error. Otherwise it is a warning that requires a new measurement.
5. **Confirmation.** A geometry change, or any warning, requires `--confirm`. Confirmation replays the recorded proposal (`--provider replay`). What is written is exactly what was reviewed.
6. **The provider is a protocol** with three implementations: local Ollama (loopback only, no proxies, no downloads), `none` (no model), and `replay`. The default model is `qwen3:14b`. Ollama structured output uses `format` equal to the schema, `temperature` 0, a fixed `seed`, and `think` off.
7. **No new dependency.** The HTTP client is the standard-library `urllib`.

A later placement plan (ADR-010) and the per-map texture choice are separate contracts. They do not widen this intent.

## Rejected alternatives

- **Widen `generator.schema.json`.** That would change a public contract, and stages do not read `quality.detail_level` or `generation.*`.
- **Let the model return full configurations.** Hundreds of fields would need a diff, and a protected field would be expressible even if it were later rejected.
- **Confirm by calling the model again.** That does not guarantee the same answer.
- **A third-party Ollama client.** A dependency for two HTTP calls.

## Consequences

- Reproducibility does not depend on the model. The log stores the raw reply and the hashes of the bases, the schema, and the prompt. Replayed outputs are identical byte for byte.
- Widening what the model may ask for means changing the intent schema, the translator, the whitelist, and the tests. It never happens implicitly.
- `facade_detail` is recorded with no effect until a stage reads `quality.detail_level`.

## Amendment (8 November 2026): the street profile, a second bounded contract

Status: proposed until a headset session checks a map with the option on (plan 2026-11-07, increment D2). Off by default.

The intent above is not widened: `config/intent.schema.json` stays at 0.1.0. A second, separate contract lets the local model do more without writing a value: `config/street_profile.schema.json` 0.1.0. It follows decisions 1, 2, 3, 6 and 7 of this record.

1. **The model chooses ids, never values.** Five required fields, each an `enum` with `map_default` (which changes nothing): `dominant_typology` (a facade style id of `config/facade_styles.json`), `palette` (`extended_pools`), `props_profile` (`intact`, `worn`, `wrecked`), `abandonment` (`years_10`, `years_30`, `years_100`) and `cover` (`off`, `light`, `full`). `additionalProperties` is false. The only string is an optional `summary` of at most 200 characters, stored as unverified text that no code reads. There is no number.
2. **Strict parse.** An empty reply, a reply over 4 KiB, a non-strict JSON reply (trailing text, a code fence, NaN, a duplicate key) or one that fails the schema is refused with an `AI_PROFILE_*` code ([10-ai.md](../10-ai.md#street-profile-plan-d2)).
3. **Deterministic table, two barriers.** Code maps each id to one row of a fixed table: a setting pointer, an existing stage flag and the ADR it cites (ADR-020/027 facade style, ADR-009 calm pools, ADR-010 props and years, ADR-032 cover count). The translation runs over an effective-settings document that also holds the area, the centre, the sources, the export card, the budgets, the core, the hash of the OSM extract and the `sightline_cover` switch. `planner.changed_paths` refuses any difference outside the five allowed pointers (`AI_PROFILE_PROTECTED`); the run then takes the neutral profile and records why. A user setting in `map.json` (a named facade style, `repetition`, a `prop_selection.json`) always wins: the id is recorded as skipped with reason `user_setting`.
4. **Replay.** The record `config/street_profile.json` keeps the raw reply, its SHA-256 and the hashes of the schema, the prompt and the inputs (brief, centre, style ids, library conditions, `sightline_cover`). The same hashes replay the stored reply without a call. A stale reply is archived, never overwritten.
5. **Provenance.** A model reply and its replay are `ai_generated`; the neutral rules profile is `estimated`. Nothing the profile drives is ever `observed`, and an observed OSM or Catastro colour, era or typology always wins: `dominant_typology` picks only the map-wide atlas, it never reclassifies a building (ADR-024).

Widening this profile again means a schema version, a table row, a guard pointer and tests, as decision 3 says for the intent.
