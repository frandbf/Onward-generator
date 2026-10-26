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
