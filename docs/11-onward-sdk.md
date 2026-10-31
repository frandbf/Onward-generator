# Onward SDK v1.15 — playable-map rules

Source: [Onward Custom Content Guide](https://onwardthegame.com/doc/CustomContentGuide.pdf), March 2025, patch 2.0, guide and package 15.0. The SDK lives only in the Unity project `~/Unity/OnwardPhase0Project` (Unity 2021.3.23f1). It is not copied into this repository.

This document is the contract any stage can read. It does not authorise `CONFIRM_PUSH` and it does not set `device_verified`. A map is playable when the Quest checklist is filled in on the headset (`docs/12-export.md`).

## What this generator applies

The current playable layout is `gameplay_layout` 0.3.0. It describes Free Roam, Uplink, Assault, Escort, Hunt, Evac, and Retrieval. The Unity project matches the guide's setup: the SDK URP asset (`UniversalRenderPipelineAsset`, guid `7decc6ad7e4538047bac43b16df38ce6`), shader stripping on Custom with every lightmap and fog checkbox, and the `PP_` shaders.

The local card does not instantiate every mode the layout describes. `GameModes` on the card is Free Roam (4), Hunt (10), and Uplink (1). On Onward 2.0.4, putting Assault, Escort, Evac, and Retrieval on the single-player card left the menu on Free Roam. Those four stay in the layout and are not instantiated. `EnvironmentType` is Urban (2) when the layout is `gameplay_layout`. The scene `LevelDescriptor` uses the same profile. Both are required. In the match creator the filter has to be **Urban** and **Day**. A one-player Free Roam does not use that filter. Do not set the card to Desert.

| Guide rule | Where it lands |
| --- | --- |
| Free Roam and at least one other mode | Card: Free Roam, Hunt, Uplink. Layout 0.3.0 also describes the modes in the table below |
| Lightmap, one baked directional light, built-in skybox, far clip 1000 m | Adapter, profile `Urban` / `Day` / `UrbanDay1`. The adapter also bakes one point light per furnished room and adds a `LightProbeGroup` (ADR-013). Not yet shown on the headset that Onward keeps them. Optional linear fog from the export card (ADR-014); not known whether the runtime or the post-processing profile overrides `RenderSettings` fog, ambient, or the skybox |
| `SetPhysicMaterial` on every collider. Without it, everything is impenetrable concrete | Water → Water, green → Grass, a node named `#glass` → Glass with `AISeeThrough`, the rest → Concrete |
| `EnableReverbInjection` or `AreaReverb` does nothing on its own | Enabled only for `gameplay_layout` |
| Library ambience, not custom clips | `Suburban Day`, `Birds Park`, `Wind Rooftop` |
| Hunt: 32 Rifleman points and 32 routes | Layout. The launcher puts those bots back. `StableHuntBots` is 32. Snipers are not kept |
| A bot patrols when it spawns within 5 m of the route start. Otherwise it wanders | One spawn per route, on the first waypoint snapped to the navmesh. The next start is 8 m away when the streets hold 32, and otherwise the largest 0.1 m step that still does, never at or below 5 m. Walkable crossings join the blocks across the carriageway (ADR-015), and routes are spread over the navmesh island of the navigation origin when it holds 32 (see [Hunt routes](#hunt-routes)) |
| Navigation-map origin on walkable ground, near the centre, plus an outdoor area | The Uplink, after it is snapped to the navmesh |
| Flight goes to `LevelHeight / 2` of the MapQuad. A volume that crosses it clips the aircraft | `LevelHeight` puts the ceiling 20 m above the highest collider |
| Tent under the play space, `DamageVolume` under the map, do not edit SDK prefabs | Already the case |
| About 150 draws, about 250 000 triangles, about 1 GB of textures, mesh combining | ADR-005: a 100 m cell, measured on a headset for one scene |

## Impact (`SetPhysicMaterial`)

The SDK enum has no plaster, brick, or cobble. Those floors and walls are Concrete. The shot and the foot respond as concrete. A type is not invented.

| Collider | Material | Notes |
| --- | --- | --- |
| Name contains `water` | Water | Penetrates and lowers damage |
| Name contains `green` or `grass` | Grass | Impenetrable |
| Node name contains `#glass` | Glass + `AISeeThrough` | The see-through pane. The concrete wall is cut on the same opening, so a shot from either side meets this volume and not the wall. The type is the SDK enum `Glass` (1), not `GlassUnpenetratable` (8) and not `GlassVfx` (9) |
| Door, wall, sill, header, pier, cobble, asphalt, sidewalk | Concrete | Impenetrable. The guide's default |

`AISeeThrough` belongs only on a collider that can actually be seen through. A concrete wall with that flag lets the AI shoot through the facade. The visual shader does not decide penetration. `glass_dark` and `glass_boarded` stay concrete because they are not openings.

## AI and the navmesh

The phase-03 ground is `collider/walkable`. It covers the site minus the carriageway, including the building lots. `collider/walkable_crossing` is the crossings: 4 m strips across the carriageway at its level, past each junction and at most 50 m apart (ADR-015, `docs/06-generation.md`). The adapter:

1. Bakes that ground and the crossings (and, on a map that has them, `pedestrian`, `sidewalk`, `ground`, `green`). The 0.15 m kerb is under the 0.3 m agent step, so a crossing joins the sidewalks on both sides.
2. Leaves out the rest of the carriageway (`collider/carriageway`), buildings, the tower, furniture, and water. Furniture is not a navmesh obstacle. Cutting the mesh at every lamp breaks it into islands.
3. Cuts each building footprint with `Not Walkable` boxes only where a ray hits the mesh, not across the whole bounding box. A rotated footprint does not eat the sidewalk. The bot does not cross the interior or walk on the roof.
4. The origin and the outdoor point are the Uplink snapped to a Walkable area. `GenerateNodes` drops every node that cannot reach that origin. Before it runs, the adapter logs `Navmesh islands: N. The origin island holds A of T m² (P%)`, so a `task unity` run shows how much of the city the nodes can cover.
5. `NavigationMeshBaker` is born inactive. It receives `NavMeshData` and is activated before `Awake` calls `AddNavMeshData`. If the object is born active, that call sees an empty field, Unity disables the component, and the mesh is deleted.
6. Spawns and routes snap to the same area, at most 10 m (the SDK validator uses 10 m and does not look at the last point). The adapter projects every point, including the last, onto Walkable and leaves the spawn on that start. Two consecutive points must be at least 0.25 m apart. If the first and the last land on the same spot, the last is dropped. A route with fewer than two points does not count. Each route has `Loops` on.
7. Node spacing is computed from the baked mesh: the longest crossing divided by 400, and the square root of the walkable area divided by a budget of 320 nodes. The larger one wins, and it never goes below 1 m. The SDK writes each street in both directions. The Quest walk does not remember nodes it has already seen, so a there-and-back does not finish. After the nodes are generated, each link is kept only toward the higher index so that walk can be measured as a chain. Before the saved scene is bundled, the return link the SDK wrote is restored. Waypoints are then anchored, and the navigation is validated. A waypoint keeps the nearest node inside that gap which can walk to the previous waypoint and back. A navmesh link is tried once. A step that still has no return path is removed, and the route is removed when fewer than two points remain. The scene is refused when a one-way link remains, a kept waypoint is farther than the grid gap measured before extra nodes were added, a kept step still has no return path, or the walk measured without memory of seen nodes exceeds 400. `GenerateNodes` is repeated, raising the spacing, while that chain is over 400. If a link still points backward, or the chain is still over 400 after 6 attempts, the scene throws and the pipeline does not start the launcher. A link shorter than 5 cm, from two SDK nodes on the same point where two triangles share an edge, is removed and logged. A node that points at itself, or past the end of the list, also stops the scene.
8. The game does not instantiate `CustomContentNavigationMap`. It reads the `AI_NavBake` JSON the Launcher writes. The baker object is removed from the scene that enters the bundle and put back in the Editor afterwards, so a second launcher still finds the asset. If the JSON has no nodes, the launcher fails.

The return the rifleman walks is the neighbour list in `AI_NavBake`. It is not the Points list and it is not the Loops flag by itself.

### Playable core

On a map with a playable core (ADR-017) the ground colliders (`collider/walkable`, `collider/carriageway`, `collider/walkable_crossing`) end at the core edge and the perimeter wall stands on it. The navmesh, its nodes and the outdoor light probes (laid on the navmesh bounds) therefore cover the core only, with no change in the adapter. `perf layout` keeps every spawn, objective, route and pickup inside the core within its wall. `layout.json` gets `navigation: {walkable_area_m2, span_m, node_spacing_estimate_m}`: the adapter's first spacing, `max(1, span / 400, sqrt(area / 320))`, computed on the 2D navmesh islands. It is an estimate; the adapter measures the baked mesh and raises the spacing while the walk is deeper than 400.

### Hunt routes

`perf layout --playable` draws the patrols (`hunt_routes` in `perf/layout.py`):

- Lines. Pedestrian ways (every class in `road_classes.pedestrian`, steps included) are merged where one continues another, then followed on their centreline. OSM cuts an alley into 10 to 15 m ways at every junction, and one such way is too short for a route. Streets of the same width are merged the same way. A street gives two lines on each side, because only the crossings of the carriageway are baked: the middle of the sidewalk (half the width plus half `sidewalk_width_m`) and 0.5 m past the kerb. The second line is the one that still lands when the facades leave less than the configured sidewalk.
- Points. A route is up to 16 points, 14 m apart along its line (about 210 m), with the last at least 2 m before the line ends. A start is tried every separation (8 m, or less, see above). A point on street furniture is skipped.
- Islands. The navmesh is the walkable ground plus the crossings (`partition.crossings`), shrunk by the 0.35 m agent radius (`nav_islands`). The crossings join the blocks on both sides of each street, so the streets of a city are usually one island. A block that no crossing or pedestrian way reaches (behind water, a closed courtyard) stays its own island. `GenerateNodes` keeps only the nodes that reach the origin, the Uplink. A waypoint on another island has no node to walk on: the adapter anchors it hundreds of metres away, or drops the step because there is no return path. So a route keeps only the points on the island of its first point, and stops at the first point off it.
- Choice. When the island of the Uplink holds 32 starts, every route is on it, and farthest-point sampling from the focus spreads them over that whole connected network. Otherwise every route on it is kept, and the rest of the 32 are spread over the other islands from the focus, as before. Those bots spawn on their start but have no node to patrol.

Agent: radius 0.35 m, height 1.8 m, slope 30°, step 0.3 m. A bake from the visual meshes is not used. It would put nodes on the roofs.

Navigation-map doors (to split a large plaza) are not generated on their own. `AddDoor` requires a real opening of at most 3 m. In an open plaza it builds a degenerate node. Doors are added when an interior has a real opening.

Headset confirmation of a full 32-bot match is still the acceptance checklist. `device_verified` stays false.

## Sound and flyover

- Global: `Suburban Day`. Local: `Birds Park` on the plaza, `Wind Rooftop` on a landmark roof.
- Default reverb `SmoothOutdoor`. Inside a nave volume, `ReflectiveIndoorLarge`.
- `OnwardCustomAudioSource` is not used. It requires a custom clip, and the guide warns about uncompressed memory.
- `LightningSystem` is not placed on a day map. The guide says a daytime flash looks wrong because it reuses the night-vision effect.
- Urban flyover, jets and helicopters, boats at 0, `PhaseChance` 1. Phase duration stays on the prefab (300 s): one pass per phase, not the 30 s test value.
- The system must reference the MapQuad (`FindMapQuad`). The flight ceiling is the top of that volume.

## Quest budget

Figures from the guide, not from the Editor: about 150 draw calls, about 250 000 triangles, about 1 GB of textures, counting players. Share `PP_Opaque_Simple_Lit`. This texture set has no normals, so it is not promoted to `PP_Opaque_Lit`. Interior panes below the accessible ceiling use the SDK shader `PP_Sprite_Lit` (`_MainTex` only). No new shader is added and the SDK graphs are not edited. Transparency on Quest is expensive. That cost is a known limit, not a measurement from this package.

Occlusion culling and static batching are what the guide recommends. v01 was measured on the headset and did not lower draw calls (ADR-005). They stay off.

Textures are 1k. The adapter reads each PNG from the GLB with `Texture2D.LoadImage` into an uncompressed RGBA32 texture asset with mipmaps (`LoadPng` in `OnwardMapAdapter.cs`). They are not compressed for the Quest yet. Importing them as ASTC is planned. If the headset shows them pixelated, the guide asks to move the shader precision from Half to Single. That is checked on the Quest. The SDK shader is not changed in advance.

## Unity project (not edited per map)

Checked on disk, without opening a newer editor:

- Unity 2021.3.23f1, URP 12.1.11, the SDK pipeline asset.
- Shader stripping: Lightmap Modes and Fog Modes on Custom, every checkbox on. Required if Spec Ops is ever added.
- Do not rename `ExampleScene` into the working scene, and do not edit the `OnwardCustomContent` prefabs.
- On macOS, `OnwardLauncher.cs` must not convert the bundle path to `\`. The patch removes only `.Replace('/', '\\')`. Reimporting the `.unitypackage` undoes it (`docs/12-export.md`).
- The `tool` step patches the SDK's `ContentBuilder.cs` with `tools/unity/onward_adapter/patch_content_builder.py`: the launcher copies the `AI_NavBake` JSON, removes the navigation baker from the bundled scene, and puts it back afterwards. The original is kept as `ContentBuilder.cs.sdk-v1.15.orig`, outside `Assets`. Reimporting the SDK `.unitypackage` undoes the patch. The next `tool` applies it again.

XR (Oculus, Single Pass Instanced on PC, Multiview on Android) is a guide requirement. Android does not keep the automatic graphics API: Vulkan first, OpenGLES3 as fallback. The adapter sets that again when it builds the scene.

The export bake follows the Onward lightmap guide (August 2023), at the low end of the range for a city: Progressive GPU, 256 direct samples, 512 indirect and environment, 2 to 4 bounces, ambient occlusion, Open Image denoiser, non-directional lightmaps, the map's texels per metre (default 10, at most 40) scaled per surface class (roofs 0.25, ground, interiors and blank party walls 0.5, facades 1; [12-export.md](12-export.md#lighting)), and 2048 px. After the bake, the Android lightmaps become ASTC 8×8. Occlusion culling and static batching stay off.

A higher texel density makes the bake much longer. It does not change the navigation rules.

## Not in this package

The guide describes more. Assault, Escort, Evac, and Retrieval are described by layout 0.3.0 and are not instantiated on the local card.

| Piece | When |
| --- | --- |
| Fire Fight, Spec Ops, Gun Game, OITC | Still out. Fire Fight is not named in `GameVariantTypes`. This document does not claim they load |
| Stairs and `AreaLadderPerch` | When a volume has a real climb. The AI does not climb. The perch is the area at the top so bots do not pile up at the base |
| Lifts | A linked pair, not one prefab |
| Glass with `PP_Transparent_*` and `AISeeThrough` | After the cost is measured on Quest |
| `OnwardCustomAudioSource` | Only with a clearly licensed clip, few at a time |
| Lightning | Only on a night profile, and only after lowering `lightning chance` in a test |
| Workshop publish | Ideal title 6–18 characters (max 24), description about 250 (max 500), thumbnail 512×256, environment and time of day consistent with the scene. This pipeline does not publish |

## Card and modes

A layout 0.3.0 places the prefabs the SDK v1.15 validator requires. The codes come from `GM_Base.EGameModeType`:

| Mode | Code | What the validator requires |
| --- | --- | --- |
| Uplink | 1 | One objective and exactly one defender `SpawnSystem` |
| Escort | 2 | One objective, spawns for both teams, and two start transforms |
| Free Roam | 4 | The guide requires it in addition to another mode. Spawns for both teams |
| Assault | 6 | In `GameVariantTypes` that value is named `UplinkAssault`. Two `SpawnSystem`s per team and one `AssaultMapBoundary` per team |
| Hunt | 10 | Attacker spawns, an empty defender list, 32 Rifleman AI, routes of 2 points, a navmesh |
| Evac | 11 | Defenders, an empty attacker list, the same 32 AI, and an `Operations Evac Point` |
| Retrieval | 15 | The Hunt objective, the Evac point, at least one hard drive, and the navmesh origin on the mesh (tolerance 0.25 m) |

`GameVariantTypes` does not name 15. The Launcher writes the integer on the component. It has not been shown on the Quest that the list displays it as Retrieval. On headset 2.0.4, a one-player local map offered Free Roam. Uplink, Assault, and Escort are the SDK's competitive modes, and Evac, Hunt, and Retrieval are the cooperative ones. This document does not claim the one-player menu lists them until the headset shows it.

The Hunt objective has attacker spawns and an empty defender list. Otherwise the component clears it and throws. Evac is the opposite: defenders yes, attackers no. Hunt picks the spawn at random and patrols only when that point is within 5 m of a route start. There is one route per bot. The spawn is the first point of that route. The next start is 8 m away when that fits, and otherwise as close as 5.1 m, which stays outside the 5 m patrol link. Two bots on the same start appear in the same place. Route lines, length and islands are in [Hunt routes](#hunt-routes).

## Do not

- Extract Onward assets or edit its prefabs.
- Use unlicensed photographs (ADR-008).
- Treat the Editor as a device test.
- Mark the map playable before the headset checklist.
