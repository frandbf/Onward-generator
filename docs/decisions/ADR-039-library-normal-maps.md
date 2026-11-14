# ADR-039 — Opt-in real normal maps for library photographs

Status: accepted · 14 November 2026, after a headset session (amendment below). Proposed 13 November 2026: the maintainer approved amending the albedo-only rule (AGENTS.md) for an opt-in list of materials, off by default, to be measured on the headset before any map turns it on by default.

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

## Amendment, 14 November 2026: measured on the headset, accepted

Murcia Catedral 2, pkg_05 (`murcia_catedral_2_6e8a247d4c`, final bake), Meta Quest 3: 12 library normal maps on `PP_Opaque_Lit` (asphalt, paving, kerb, pavements, roofs, bark), with 22 cut-out trees (ADR-040), 62 props and 288 426 estimated Unity triangles. VrApi logcat, one sample a second over about 6.5 minutes of play (404 samples): 72–73 FPS median, 72 at the 10th percentile; app GPU time 4.78 ms median, 5.99 ms at the 90th percentile, 8.81 ms at most, against 13.9 ms at 72 Hz; GPU load 50 % median, 68 % at most; at least 1 714 MB free. Four seconds dropped one to three frames (69–71 FPS). Two seconds fell to 53 FPS close to the end of the session: the second, with the app GPU time under 1 ms, matches leaving the map; the first was still under normal load (4.2 ms), so it is not explained. The log does not say what the player was looking at, so no drop can be put on the normal maps or on the trees. The maintainer looked at the ground, roofs and facades and found them right. The switch stays on by default for a new map (since 13 November 2026, `maps.workspace.DEFAULT_OFF_SWITCHES`); `--no-normal-maps` turns it off.

