# ADR-035 — Local catalogues from the user's own Unity Asset Store packs

Status: proposed · 10 November 2026. It stays proposed until a headset session has seen converted props and furniture in a map.

## Context

The project is public. Each user has a Unity account with their own Asset Store packs, almost all free and under the Standard Unity Asset Store EULA as "Extension Assets". That EULA does not allow redistributing the asset files, so they cannot go into `assets/` or any commit. It does allow building them into a product ("Licensed Product"), such as a built map. Unity gives no public API to list a user's "My Assets" without signing in. Downloading a pack with the Package Manager does leave it as a `.unitypackage` in the user's local cache (`HostProfile.asset_store_cache`, ADR-011). A pack holds FBX models, prefabs, materials and textures. Python cannot read FBX without a new dependency, and the prefab and material decide the scale and the albedo.

## Decision

1. **Read the user's cache, nothing else.** `onward-generator asset-store scan` lists the `.unitypackage` files in that user's cache. `asset-store import [--only ID…] [--max-mb 400] [--max-triangles 8000] [--refresh]` converts them on the host. It runs outside Docker, because it needs the Unity editor and the cache. Nothing is downloaded and nothing leaves the machine.
2. **Convert with the user's Unity, in a throwaway project.** `src/onward_generator/assetstore/packages.py` unpacks a package's files under `cache/asset_store/project/Assets`, with three exclusions, because the archive is untrusted:
   - code (`.cs`, `.dll`, `.js` and the like): Unity would compile and run it;
   - anything outside `Assets/`, and `Assets/Editor`;
   - members over 512 MB.

   Then `tools/unity/asset_store_export/Editor/AssetStoreExport.cs` runs in batch mode:
   - It writes each prefab, LOD0 only, as OBJ + MTL + one albedo PNG per material. The PNG is the base map tinted by the base colour, at most 512 px.
   - Coordinates are right-handed, Y up, in metres, centred, with the lowest point at 0.
   - A prefab the 2021 editor cannot read (Unity 3.x binary) falls back to the FBX, one item per top-level child.
   - It refuses transparent and alpha-tested materials, and models over `--max-triangles`.

   After each package the project is emptied. The Onward Unity project is never opened.
3. **Same output as every catalogue.** `assetstore/objglb.py` converts each OBJ to an opaque GLB with only a base colour image, as the Kenney and Quaternius imports do. A package whose models all come out under 5 cm (Unity 3.x FBX in centimetres) is scaled by 100 (`unit_factor` in the manifest).
4. **Classification from words, recorded as estimated.** `assetstore/classify.py` reads the model name, the package name and its store category, and the measured size:
   - Building and room kit pieces are skipped (`kit_piece`): a kit building never replaces an OSM footprint.
   - Vehicles (at least 2.5 m) become `street` rows. Street objects become `kerb` or `ground_cover`. Storage from an exterior package becomes `wall_foot`. Everything for a room becomes `interior`. The rest is skipped as `unclassified`, with counts in the manifest.
   - An interior row that fits a wall (at most 0.8 m deep) or a counter (at most 1.0 m) gets a `furnish` hint: its slot, its shop sets (generic, bakery, bar) and whether it suits a home floor.
5. **Local, gitignored catalogues.** Each package writes `assets/asset_store_<id>/`. That folder holds its GLBs, a manifest whose `source` names the EULA, and a `License.txt` that says not to commit or share the files. `.gitignore` has `assets/asset_store_*/`.
   - The library reads these folders like any other catalogue.
   - Interior rows under the EULA load only from an `asset_store_*` folder; the same manifest anywhere else is refused as `licence`.
   - A test checks that no tracked file names that licence.
6. **Furnishing takes them when they are there.** `load_detail_config` adds hinted rows to the enabled furnishing sets (`with_local_furnishing`). It adds at most two per set slot, lightest first (four pushed Madrid r200 from 34 triangle-budget refusals to 35 `asset_cap` ones), so the distinct rows stay near `max_assets`. Each row passes the same checks as the shared rows.
7. **Reproducible output.** `ONWARD_LOCAL_CATALOGUES=0` hides every local catalogue from the library and from furnishing. The test suite sets it, so tests give the same result on every machine. Each placed piece records its catalogue, its GLB SHA-256 and its licence.

## Amendment (10 November 2026): textures read on the CPU

The first conversion lost every texture: the headset showed white and pale-blue cars, barriers and furniture. The exporter drew each texture through the GPU (`Graphics.Blit`), and batch mode runs with `-nographics`, where that returns a blank grey (205, 205, 205), so all 1 395 models came out flat. URP and HDRP packs had a second fault: their shaders do not exist in the Built-in export project, so `HasProperty` and `GetTexture` found nothing. The exporter now:

- reads the textures and colours from the material's saved properties (`m_SavedProperties`), which the `.mat` keeps whatever its shader;
- makes the texture importer readable, uncompressed, sRGB and at most 1024 px, then reads the pixels on the CPU (`GetPixels32`).

Python scales the albedo to 512 px at most. Outdoor rows with their long side on the file's Z get `yaw_deg: 90`.

## Consequences

- Two users with different packs get different furniture and props on the same map. The map records which local rows it used.
- A built map embeds the converted meshes, which the EULA allows. The export package and the GLBs under `assets/asset_store_*` must not be shared or published: they are the asset files themselves.
- Packs over 400 MB (archviz and HDRP scenes) are skipped unless named with `--only`. Packs made for Unity 2022 or 6 may not import in 2021.3.23. The report says which models failed and why.
- The words decide placement, so some rows will be wrong (a "bin" inside a shop, a planter on the street). The manifest is the place to correct a row by hand, as for the other catalogues.
- Not yet checked on the headset: the facing of converted furniture (the planner assumes glTF +Z is the front), and whether the tinted albedo matches the store's look.

## Rejected alternatives

- **Commit the converted packs.** Forbidden by the EULA.
- **A Python FBX reader (or Blender, or FBX2glTF).** It is a new dependency or install, and it ignores prefab scale and materials. The user already has the right editor.
- **`AssetDatabase.ImportPackage` or `-importPackage`.** Both import after the batch method has run, so the export saw nothing.
- **Import into the Onward Unity project.** It would grow by gigabytes and mix third-party content with the export adapter.

## Amendment (12 November 2026): real sizes by role

Status: accepted. The import keeps a model at the size its author gave it (`unit_factor` 1.0 in all 68 local catalogues), and placement scaled only wrecks, fire escapes and plants. On Rue Mouffetard concrete barriers stood 1.66 m tall, barrels 1.17 m, pallets 1.65 m long, a kit debris cube 1.3 m, and coffee cups on the shop tables 45 cm long. `overgrow/fit.py` `ROLE_SIZE_M` now gives every common street role a height band and a longest side, and a mesh is scaled uniformly into it or refused (`wrong_size`); tableware and food set on tables and counters are capped by semantic kind (`INTERIOR_MAX_M`). The shop door shrinks from 1.6 × 2.5 m to 1.1 × 2.3 m. The walkable `interior_door` keeps 1.2 × 2.4 m: its width is the 1.2 m clear path VR players and bots need (ADR-031). On the map the estimated Unity triangles barely move (242 234 → 241 507).

## Amendment (12 November 2026): the rules read the placement

On Rue Mouffetard (pkg_06, on the headset) cardboard boxes and a gift box stood in the middle of the sidewalk by the kerb. The rows said `wall_foot`, but only the model's prompt read `placement`; the rules took any spot of the row's surfaces. `dressing.placement_reason` now refuses a `wall_foot` copy farther than `WALL_FOOT_M` = 1.5 m from a building (`not_wall_foot`), an `open_ground` copy on the carriageway, a sidewalk or a kerb (`not_open_ground`), and any copy of a role in `INDOOR_ROLES` (the gift box, `indoor_only`). Rows without a `placement` keep the earlier rule.
