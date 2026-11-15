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

## Amendment, 13 November 2026: more decals, more kinds

On the headset, with pkg_10, the maintainer asked for more decals and more variety, since they cost almost no triangles.

- **30 to 50 per map** (`MIN_DECALS`, `MAX_DECALS`), at most 100 triangles.
- **Thirteen drawn kinds.** The seven of the first version are unchanged, pixel for pixel. Six more are drawn from the map's seed:
  - bullet holes with chipped plaster;
  - moss at the foot of a wall;
  - peeling paint;
  - rain streaks;
  - chalk tally marks;
  - a faded painted advert whose lettering has worn away.

  None carries letters or numbers. With the 16 brick patches of a local Asset Store pack, a map can draw on 29 decals.
- **The model names 12 to 24 ids of at least 5 kinds** (`MIN_PICK`, `MAX_PICK`, `MIN_KINDS`), so its pick fits one 2048 px atlas page of 25 cells. The rules fallback uses every decal, which can need a second page and so one more transparent material.

## Amendment, 14 November 2026: the maintainer's own decals on every map

Asked by the maintainer: decals made from their own photographs, several of them, on any playable street of every map. `signs/user_decals.py` reads the local, gitignored folder `assets/user_decals/`: `decals.json` lists `{"decals": [{"file", "width_m" or "height_m", "description"}]}`, each file a PNG of that folder named by its plain name (at most 16 MiB), its other side from the image's aspect, both from 0.2 to 3 m. The image keeps its alpha (an image without alpha is an opaque rectangle) and is scaled to at most 512 px, as the library decals are; its id is `user_<file stem>`, kind and origin `user`. `signs place` puts every one of them first, once each, on top of the 30 to 50 others, with the same rules (an exposed core wall, clear of openings and signs, on the built facade); the model's pick does not apply to them. A decal that fits no wall stops the stage with its id, and a bad manifest or image stops it with the reason. Like the Asset Store catalogues, the folder is read only while the local catalogues are on (`ONWARD_LOCAL_CATALOGUES` is not `0`; the tests turn it off). No folder or no `decals.json`: nothing changes. The photographs are the maintainer's; publishing a map that carries them is the maintainer's call, and they should hold no recognisable person, brand or personal data. A map meant for the Onward Workshop leaves them out with `map.json` `"user_decals": false` (any other value than a boolean stops the stage): the Custom Content Guide refuses "the full name, likeness or representation of any person (alive or dead)", and the maintainer's decals show real people. Without the key, or outside a map folder, they are placed.

## Amendment, 14 November 2026: the project's own decals, committed, on every map

Asked by the maintainer: decals of text and symbols that ship with the project, so every map anyone builds carries them. `assets/decals/` holds nine PNGs and a `decals.json` in the user decals' format (MIT, the repository's licence; `assets/decals/README.md`). `signs place` reads them with the same reader (`read_project_decals`: ids `project_<file stem>`, kind and origin `project`) and puts each once, first, then the user's decals, then the 30 to 50 others; a required decal that fits no wall stops the stage. A map leaves them out with `map.json` `"project_decals": false`. `ONWARD_PROJECT_DECALS=0` skips them; the tests set it, since the committed images would move every pinned scene. They show no person, so a Workshop map keeps them.

## Amendment, 15 November 2026: war damage on the walls and the ground

The maintainer asked for a generic war ambience (plan [2026-11-15-ambiente-belico](../plans/2026-11-15-ambiente-belico.md), increment A), first for a Fallujah map of November 2004 and for Mogadishu 1993.

- **Level.** `map.json` `"damage"`: `none` (absent means `none`; the map is drawn as before, byte for byte), `light`, `heavy` or `auto`. `auto` reads the street profile record: `props_profile` `wrecked` is `heavy`, anything else, or no record, `none`. The model is not asked a new field, so no stored street profile is invalidated. Any other value stops `signs place`.
- **Wall.** `signs/damage.py` draws three more kinds, deterministic from the map's seed, without letters or numbers: a shell impact (render blown off, brick courses showing, a dark crater, chips and cracks; 1.6 × 1.4 m), a scorch mark (1.6 × 1.8 m) and a spray of shrapnel pits (1.8 × 1.2 m). With damage they join the pool, and the decals come in two interleaved turns: the war kinds (bullets, cracks, soot, peeling paint and the three new ones), at least 40 % (`light`) or 60 % (`heavy`) of the count, and the rest. `heavy` puts 60 to 90 decals on the walls, by the seed; `light` keeps 30 to 50. The model's pick still chooses among the old kinds; the war kinds come in on top of it.
- **Ground.** Five drawn kinds (road cracks, a patched pothole, a sand drift that fades at every edge, a blast scorch and flat rubble, 1.4 to 3 m): 12 to 20 with `light`, 30 to 45 with `heavy`. Each lies flat 1.5 cm above the top face of the built scene, where that face under its centre and its four corners is `surface/carriageway`, `surface/sidewalk` or `surface/ground`, is not a crossing, manhole or road marking, and stays within 3 cm of one height (no decal bridges a kerb), 0.5 m clear of the other ground decals, inside the core. They share the wall decals' `wall_decal_atlas_<page>` pages, so the adapter draws them alike (transparent sprite shader, no shadow) and no material is added. Each is a `ground_decal` object (decal, kind, position, yaw, size).
- **Reports.** `signs_report.json` gains `damage` (`level`, `origin`, `war_share`, `war_decals`, `ground_decals`) and `detail_report.json` `ground_decals`, only with damage.
- **Cost.** Two triangles a decal: `heavy` adds at most about 150 triangles and some transparent overdraw where decals are in view, to check on the headset.
