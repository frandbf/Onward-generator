# ADR-038 — Wall decals: stains, soot, rust, posters and brick patches on every map

Status: accepted · 12 November 2026. Asked by the maintainer: decals cost almost no triangles, so every map should carry 10 to 20 varied ones, chosen by the model.

## Context

- Street walls of the core read clean: one trim atlas per style, no wear of its own on any wall.
- The Onward SDK ships no decals. The maintainer's Asset Store cache holds one decal package, Bricks Decals FREE (15 patches of brick showing through broken plaster, albedos with a transparent background). Its EULA allows local use in built maps, as for the model catalogues (ADR-035).
- The adapter already draws transparent atlas pages: the shop decals (`shop_decal_atlas_*`, ADR-031) are quads 1 cm in front of the shop glass, drawn with the SDK's transparent sprite shader, out of the material dedupe and casting no shadow.

## Decision

1. **Catalogue.** `assetstore/decals.py` reads a decal package's albedo PNGs straight from the `.unitypackage` (no Unity export; normal, height and smoothness maps and opaque textures are left out), scales them to at most 512 px and writes `assets/asset_store_<package>/` with a `onward_generator.wall_decals` manifest (id, file, kind, description, size on the wall from the image's aspect around 1.6 m, licence). The folder is gitignored like every local catalogue.
2. **Decals from code.** `signs/decals.py` draws seven kinds itself, deterministic from the map centre and CC0 by construction: a damp stain, soot streaks, rust drips, plaster cracks, a torn poster without text, an abstract scribble without letters and grime along a wall foot. A map without any local catalogue still gets its decals.
3. **Always on, 10 to 20.** `signs place` puts `decal_count` (10 to 20, by the map's seed) decals on the exposed walls of the core: walls of at least 2.5 m whose outside is in no footprint, taken in a seeded order. Each decal goes at the first height (1.4, 0.9, 2.4, 3.6, 5.2 m) and position where it covers no glass, door, railing, stone frame, balcony or sign, keeps 0.4 m from the other decals on its wall, 0.15 m off the ground and 0.5 m under the wall top. The decals are taken in a seeded turn, so a map shows every decal it has before any repeats.
4. **The model picks, the rules place.** With `ambient_ai` in `map.json`, `signs/decal_pick.py` gives the local model each decal's id, kind and description and the brief; it names 6 to 12 ids of at least 3 kinds (Ollama's structured output enumerates them). Two attempts, then every decal. The pick is stored in `signs_report.json` (`wall_decals.pick`) and replayed when the instruction is the same.
5. **Drawing.** One quad (2 triangles) per decal, 1 cm in front of the wall, on `wall_decal_atlas_<page>` pages of 400 px cells. The adapter treats the prefix like the shop decals: the transparent sprite shader, no dedupe, no shadow. Each decal is a `wall_decal` object of the manifest (decal, kind, origin, wall, position, size).

## Consequences

- 20 to 40 more triangles and one more material per map; the transparent pages add some overdraw where a decal is in view, to check on the headset with the next package.
- This is a second transparent material family besides the shop overlays; every other exported material stays `PP_Opaque_Simple_Lit` with an albedo only.
- The decals are generic wear. They do not represent the state of any real wall.

## Amendment, 13 November 2026: laid on the built facade

On the headset, with pkg_09 of Rue Mouffetard, decals hung over windows and doors, and some floated away from the wall. There were two causes. First, the signs stage reads the materials stage, where a trim-sheet map has re-skinned every opening into one facade material, so the material blocker found no glass or door. Second, the built facade does not always stand on the footprint's wall line; it can sit up to 0.3 m behind it, so a quad 1 cm in front of the line floated.

- **Surface probe** (`signs/decals.py`, `wall_surface`). Before a position is taken, a grid of horizontal rays at most 0.3 m apart (`PROBE_STEP_M`) crosses the decal. Each ray starts 0.6 m outside the line (`PROBE_OUT_M`) and looks straight in. Every ray must first meet a face that is not an obstacle, all within 2 cm of one depth (`FLAT_M`), and that depth must lie within 0.5 m of the line (`PROBE_REACH_M`). A window or door recess, flush glass, a frame, a sign, a prop in front of the wall, or the end of the wall breaks the probe. The decal is drawn 1 cm in front of the depth it finds (`Placed.depth_m`).
- **Openings of the untrimmed scene** (`signs/place.py`, `untrimmed`). When the base is a materials stage, the blocker and the probe read the scene that stage was baked from (`materials_report.json` → `base_dir`; a container `/work` path maps to the repository). The trim sheet only re-skins, so its geometry is the same and the openings keep their own materials.

Result on Rue Mouffetard: all 17 decals lie 1 cm in front of plaster, stone or brick, and none covers glass, a door or a recess or overhangs a wall end. Before, 7 covered glass (2 over a window recess) and 3 hung partly in the air.
