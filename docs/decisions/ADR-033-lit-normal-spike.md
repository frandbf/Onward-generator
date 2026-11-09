# ADR-033 — One facade material on PP_Opaque_Lit with a derived normal map, as a spike

Status: proposed · 9 November 2026 (plan [2026-11-07-workshop-quality](../plans/2026-11-07-workshop-quality.md), increment D5). It stays proposed until a headset session has measured the option off, on, and on with directional lightmaps (plan row D5: "Medida en casco antes de cualquier ADR"). Only then can it be accepted or dropped. Accepting it does not by itself change a default or the AGENTS.md rule "Exported materials use `PP_Opaque_Simple_Lit` and an albedo only"; that takes a separate decision.

Amends, for one opt-in material only, [ADR-009](ADR-009-dress-materials.md) decision 7 ("The shader is `PP_Opaque_Simple_Lit` ... No normals") and the context of [ADR-020](ADR-020-trim-sheet-facades.md) ("`PP_Opaque_Simple_Lit` is `_BaseColor × _BaseMap` and nothing else: no normal map"). Cites [ADR-005](ADR-005-quest-performance.md) (combine by cell, the Quest budget, measurement on the headset) and [ADR-007](ADR-007-onward-export-adapter.md) (the adapter stays apart from the engine).

## Context

- Plan §2.1 *Normales y roughness*: every exported material is the SDK's `PP_Opaque_Simple_Lit`, `_BaseColor × _BaseMap`. Plaster grain, cornices and window reveals exist only in the albedo, and at grazing angles a facade reads flat.
- The SDK ships `PP_Opaque_Lit` (`Assets/OnwardCustomContent/Internal/Shaders/PP_Opaque_Lit.shadergraph`, read in `~/Unity/OnwardPhase0Project`), with `_BaseColor`, `_BaseMap`, `_BumpMap`, `_Smoothness`, `_SpecColor` and `_SpecGlossMap`. Its cost per fragment on the Quest, and whether the gain shows, are unknown.
- Everything is baked (docs/12-export.md *Lighting*). With non-directional lightmaps (`build.lightmap_directional` false, the default) the baked diffuse carries no direction, so a normal map changes little beyond the specular term. Directional lightmaps add a direction map per page, about twice the lightmap memory.
- The project adds no assets for this (maintainer note for D5) and no dependency. The facade trim atlas (ADR-020) is 2048 × 4096 and shared by `facade_trim_<style>`, `party_facade_trim_<style>` and `scenic_trim_<style>`.

## Decision

1. **Off by default.** The export card's optional `build.lit_normal_material` names exactly one material matching `^facade_trim_[a-z0-9_]+$` (at most 64 characters). The optional `build.lit_normal_strength` (above 0, at most 8; 2 when absent) needs it (JSON Schema `dependentRequired`). `map create` writes neither. A card without the key gives the same `adapter_options.json`, manifest file list and `package_id` as before, byte for byte.
2. **A derived normal map, no new asset.** `export package` reads the material's albedo PNG from `scene_visual.glb` and derives a tangent-space normal map (`src/onward_generator/export/normal_map.py`): Rec.709 luma as height, 3×3 Sobel with the border pixels repeated (never wrapped), `n = normalize(−s·dh/dx, −s·dh/dV, 1)` in the OpenGL / Unity green-up convention, encoded `round((n·0.5 + 0.5)·255)`. Deterministic: fixed PNG compression, no metadata, pixels pinned by a test digest. It is written as `lit_normal.png` (role `lit_normal`, source `generated`), and the manifest records `lit_normal` (material, strength, size, predicted ASTC 6×6 bytes, `derived_from: albedo`).
3. **Refuse a no-op, warn on flat lighting.** The package is refused when the material is not in the visual GLB, has no base colour texture, or its image is not a PNG. It warns when `build.lightmap_directional` is not true.
4. **Adapter.** Only the named material moves to `PP_Opaque_Lit`: `_BaseColor` and `_BaseMap` as before, `_BumpMap` the normal map imported as `TextureImporterType.NormalMap` (linear, mipmaps, ASTC 6×6 on Android), `_Smoothness` 0.1 and `_SpecColor` 0.04 grey when present. A missing graph or required property stops the build. No shader is added, the SDK graphs are not edited, and every other material keeps `PP_Opaque_Simple_Lit`, `PP_Opaque_Unlit` or `PP_Sprite_Lit` as before. Tangents (`Mesh.RecalculateTangents`) go only on the meshes that draw the promoted material. The package refuses the option without the file and the file without the option.
5. **Measurement.** `task bench -- run --lit-normal-material NAME` sets the key in the run copies only and reports the normal map's bytes. The headset session compares off, on, and on with `build.lightmap_directional: true` (docs/09-perf.md *Lit normal spike*).

## Measurements

Bench, Docker on an Apple M4 Pro, frozen inputs, `--offline --gameplay-report`, 9 November 2026. Off (run `d5_off`): `bench.py check` finds no difference outside timings against `20261109T143845Z`, and the four packages are byte-identical apart from `onward_export.json` (`created_at` and input paths; same `package_id`). On, `facade_trim_mediterranean` (run `d5_on`, Murcia and Madrid; Paris and Krakow have no such material and would be refused): triangles, draws, view draws and lightmap pages unchanged; one 2048 × 4096 normal map, 5 502 650 bytes of PNG and an estimated 4 988 080 bytes (4.8 MiB) in ASTC 6×6 with mips; the export stage takes about 2.6 s more. Unity and the headset are not measured.

## Rejected alternatives

- **Making it a default, or promoting every trim material.** Not before a headset measurement; the rule in AGENTS.md stays.
- **A downloaded or hand-made normal map.** Adds an asset and a licence to check; the derived map needs neither.
- **Deriving the map in C#.** It would be untestable in the Docker image; in Python the pixels are pinned by a test.
- **Using a `_BumpScale`.** `PP_Opaque_Lit` has none; the strength is baked into the PNG.
- **Forcing `lightmap_directional` with the option.** It changes the lightmap memory too; the headset matrix measures the two effects apart.

## Consequences

- The package can hold one more texture (about 4.8 MiB on the Quest for the trim atlas). Draws and triangles do not change: the promoted material already had its own group per cell, and the dedupe key holds the shader name, so its siblings are not merged with it.
- A luma-derived normal map reads dark as low: dark window panes look recessed, but a dark stain or a painted band also gets relief. The headset judgment decides whether that is acceptable.
- The C# was written without Unity; the first `task unity` build on the macOS host is its compile check.
- `device_verified` stays false and `quest_acceptance.md` is not marked.
