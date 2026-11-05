# ADR-029 — Ladders and lifts to the roofs

Status: proposed · 3 November 2026. Not compiled in Unity and not seen on the headset. Builds on the roof access of the interiors.

## Context

- The request: an outside ladder on buildings of at most 3 storeys with no interior, at most 3, and a lift on buildings of 3 or more.
- SDK v1.15, read on the Mac (issue #121):
  - `Ladder` has no height field, mesh or collider. 17 pairs of rung transforms run from 0.21 m to 5.683 m, about 0.34 m apart. `VaultTargetUpper` is at 5.111 m and 0.91 m ahead. `ExampleScene` sets it to the height of its roof.
  - `Elevator` is linked only by `PairedElevator`, each cabin pointing at the other. There is no travel or shaft field. `ExampleScene` puts its two cabins at the same height, 26 m apart. The cabin is about 3.01 × 3.13 × 3.03 m, its pivot 1.52 m above the floor, and its door on local +Z. Its only mesh is a helper cube.
- Onward is a shooter with many players. A roof is a strong position, an access is a choke point, and the AI does not climb.

## Decision

**Placement, in `perf/climbs.py`, at the layout stage, which already knows the spawns, routes and objectives:**

1. A building gets one access. Its roof must be flat, it must not be a landmark or in the scenic tier, and no stair may reach it. On a map with a playable core (ADR-017) the whole footprint must lie inside the perimeter wall (the core less `envelope.thickness_m`), and a roof higher than the wall (`envelope.height_m`, 12 m by default) must stay 6 m (`WALL_JUMP_CLEAR_M`) from the core edge. A core building only has to touch the core, so before this rule a lift reached a 16.7 m roof that ran 37 m past the 12 m wall on MurciaEjemplo, and a player could walk out of the core over it; that map lost one ladder and two lifts to it. The 6 m is an estimate, not a measured jump.
2. Storeys are the estimated levels, or what the height implies at 3 m a storey when that is more: a real Madrid map has a 22.9 m roof with few cadastral levels. Ladders: at most 3 storeys, no interior, at most 3 per map. Lifts: 3 to 6 storeys, at most 3 pairs per map. The upper limit and the lift cap are decisions of this ADR: a higher roof overlooks the whole map, and a pair is two choke points.
3. **Fairness.** No access within 30 m of a spawn. Buildings closest to equidistant between the teams go first, then the lowest. Accesses are at least 30 m apart.
4. **No blocking.** The foot of a ladder and a cabin with 3 m clear in front stay on free ground, 3 m from spawns, objectives, boxes and interior doors, 1.5 m from bot routes, and clear of the overgrow props (amendment below). So there may be no ladder or lift on a map with no room, and that is accepted. Free ground here is only the ground a player walks to from the Uplink: every surface but water, the carriageway too, less the buildings, with a gap narrower than a player (0.7 m) closed (`reachable_ground`; amendment below).

**A ladder keeps the prefab's shape.** The adapter raises it until `VaultTargetUpper` is at the top of the parapet (`parapet_m` in the site), puts that target back down on the roof, and adds rungs at the same spacing down to the ground, each level as a pair in the prefab's order (Left, Right). The top rungs stand 0.57 m above the parapet, as the prefab's stand above a bare edge, and the hand reach does not change. Under 5.1 m the lowest rungs are simply below the ground.

**A lift is one cabin in the street and one on the roof**, doors facing away from the wall line.

**Every flat parapet now has a collider**, not only those of a roof with a stair: any roof may get a ladder or a lift at the layout stage, which comes after detail, and a parapet that bullets cross is not cover. Roofs nobody reaches pay a few collision triangles.

**A simple visible model.** The ladder in `ExampleScene` is a child of hand-placed cubes with `BoxCollider`, so the prefab draws nothing and a map must bring its own. The cabin has no model there either: its box, door and button are editor gizmos, and the SDK does not show whether the game spawns one at load. The adapter adds rails and rungs, or four posts and a roof outside the cabin's size: one mesh and one draw call each, no collider, lit by probes. Without colliders, bullets cross the ladder, unlike the example's; the script only asks for the rung transforms.

## Amendment: the parapet (3 November 2026)

On the headset, some ladders ended below the top of the wall and the parapets got in the way of shooting. The top rung was 0.57 m above the roof and the parapet 1 to 1.2 m, so the ladder stopped up to 0.63 m short.

Every template's parapet is now 0.7 m, not only those of a roof with a climb. The climbs are chosen at the layout stage, after `detail` has built the walls and their colliders, and every roof a stair reaches needs the same height. 0.7 m is a modelling assumption, to be checked on the headset: below the shoulder of a kneeling player, who can fire over it, and still cover for a crouched one.

## Amendment: overgrow props (4 November 2026)

On the headset a street cabin stood on a pile of Kenney bricks. `overgrow` places its props before the layout picks the cabins, and the keep-out held only spawns, objectives, boxes, doors and routes. Every `overgrowth` record but the flat growth (`grass`, `vine`, `moss`) now joins the keep-out: a circle of 1 m around the prop's spot (the radius `free_ground` gives a detail urban element), 2 m around a tree, whose generated crown is about 3.8 m across. Both radii are estimates, not measured footprints. The regenerated Murcia2026 area had a generated tree in a cabin with the rule before this one.

## Amendment: a shell around the cabin (4 November 2026)

On the headset the game does spawn a cabin, with its panels, rails and buttons. From a distance it draws the cabin without its outer walls, so a far lift showed its inside panels, and the walls appeared only when the player came close. That is the game's model, which a map cannot change. The adapter's posts become a shell outside the cabin's 3.01 × 3.13 × 3.03 m: a back wall, two side walls and the roof, 0.1 m thick, with the door side (local +Z) open between the side walls. From outside the player sees the shell at any distance, and from inside the game's panels. It is still one mesh and one draw call, with no collider, lit by probes. The street cabin's back wall reaches 5 cm into the facade it stands against.

## Rejected alternatives

- **Stretching the prefab or its rungs.** 9 m would put rungs about 0.55 m apart and change the hand reach and the vault's height.
- **Stacking two ladders.** The first one's vault would push a player into the air.
- **Placing at the detail stage.** That stage does not know the spawns, so it could not be fair.
- **Choosing the nearest roofs to the focus.** It would make one corner the fight of every match.

## To check on the headset

- Whether the game spawns a cabin, a door and a button. It does (amendment above): the cabin, its panels and its buttons.
- Climbing a ladder with added rungs, and the vault from the top rungs over the parapet down to the roof, now that the target is lower than the prefab has it.
- Whether a parapet of 0.7 m lets a player shoot from the roof and still gives cover when crouched.
- A lift between different heights. `Validate` only requires `PairedElevator`, so the SDK does not refuse it, but the travel is in the game and `ExampleScene` only shows the same height. If it fails, the lifts come out and the ladders stay.
- Whether a player can still jump over the wall from a roof above it that stands 6 m from the core edge.
- Whether 3 and 3 are the right number for a match, and whether a roof overlooks a spawn: the rule measures distance, not line of sight.

## Amendment, 5 November 2026: an access only on ground a player reaches

On Barcelona Sarria (r 150 m) all three ladders and one of three lifts stood in courtyards the buildings close, 7 to 22 m from any ground a player reaches from the Uplink: `place_climbs` only asked that the foot of a ladder or the street side of a cabin lie on free ground, and a closed courtyard is free ground. The layout now hands it only the ground connected to the Uplink: every surface but water, the carriageway included since a player walks across it, less the buildings, eroded by the player's radius (`NAV_AGENT_RADIUS_M`, 0.35 m), the piece holding the Uplink kept and widened back. The same map now gets three ladders and three lifts, all on that ground. A player who could jump over a low building into a courtyard is not counted: the building is a solid footprint, whatever its height.

## Amendment, 5 November 2026: the roof inside the parapet, and its size

On Barcelona Sarria the landing of a ladder and the roof cabin of a lift were checked against the footprint. The parapet stands on the footprint, so the side parapets of a narrow roof left 1.45 m for a 1.6 m ladder, and the apron of one cabin ran 0.26 m into the far parapet of an oblique roof. Every ladder also led to a small roof (4.3 to 17.7 m² inside the parapet) under walls 4 to 12 m taller: a dead end.

The landing (1.6 × 1.5 m) and the roof cabin with its apron now lie on the deck, the footprint shrunk by the roof's `parapet_thickness_m` (`deck` in `perf/climbs.py`). The ladder's roof point, where the adapter adds its roof probes, is the middle of that landing. A roof is a candidate only when its deck is at least 30 m² (`ROOF_MIN_M2`), room for a squad to stand and move. That figure is an estimate, not measured on the headset. On Barcelona Sarria with its default 100 m core this leaves one ladder, on a 37 m² roof, where there were three. The core has no larger roof within the ladder and lift rules. A dead end under a taller wall is not tested beyond the size.
