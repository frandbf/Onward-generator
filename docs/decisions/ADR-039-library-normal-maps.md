# ADR-039 — Opt-in real normal maps for library photographs

Status: proposed · 13 November 2026. The maintainer approved amending the albedo-only rule (AGENTS.md) for an opt-in list of materials, off by default, to be measured on the headset before any map turns it on by default.

## Context

- Every exported material is drawn with `PP_Opaque_Simple_Lit`: albedo × colour, no normal map (ADR-009 decision 7). Ground, roofs and stone read flat up close; the photographs carry relief only as baked shading.
- ADR-033 (proposed) lets one trim-facade material use the SDK's `PP_Opaque_Lit` with a normal map derived from its albedo (Sobel). The adapter already imports a tangent-space normal map (linear, ASTC 6×6) and adds tangents to the meshes that draw it.
- Plan T3 step a records, for each wide-library photograph (plan T1), its real PBR normal map from Poly Haven: `nor_gl` (OpenGL, the glTF convention), 1k JPEG, CC0, with hashes.

## Decision

1. **Opt-in per map.** `map.json` `"normal_maps": true` (`map create --normal-maps`). `run` then passes `materials apply --normal-maps`. Absent, nothing changes: no list, no file, no option, the same `package_id`.
2. **What is listed.** `materials apply --normal-maps` names, in the visual GLB's `extras.materials.normal_maps` and in `materials_report.json` `normal_maps`, each material that draws a library photograph with a normal map. A party wall or a tint takes its base's map (`material_base`). The other photographs stay albedo-only and are reported in `without`, and so do the trim facades, which keep ADR-033.
3. **Export.** `export package` writes each map once as `normal_<library id>.png` beside the GLBs (package paths are plain file names), at most 1024 px, with role `normal_map`. It checks each map's hash against the library and prices it in ASTC 6×6. It refuses a package whose maps add up to more than 24 MiB (`NORMAL_MAPS_MAX_BYTES`, about 2.4 % of ADR-005's 1 GB texture figure; a 1k map is 0.46 MiB) and a material that is also the `lit_normal_material`. The manifest records `normal_maps`: the files with their library ids and sources, the per-material binding, the total and the limit. `adapter_options.json` gets `normal_maps: [{material, path}]`.
4. **Adapter.** Each listed material is drawn with `PP_Opaque_Lit`, its albedo, the map on `_BumpMap` and the ADR-033 smoothness. Its meshes get tangents. Each map is imported once. The package check refuses options that name a file the manifest does not list, or the reverse.

## Consequences

- Texture memory: one normal map per photograph (about 0.46 MiB in ASTC 6×6), at most a dozen on a map, about 5 MiB. Geometry: tangents on the meshes of those materials. Shader cost: `PP_Opaque_Lit` on those materials only. To be measured on the headset (frame time, memory) before `normal_maps` is turned on for a shipped map.
- Baked lighting: as ADR-033 notes, without directional lightmaps the relief shows mostly in the specular term.
- AGENTS.md: the albedo-only rule now names this opt-in exception.
