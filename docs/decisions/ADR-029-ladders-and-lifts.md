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

1. A building gets one access. Its roof must be flat, it must not be a landmark or in the scenic tier, and no stair may reach it.
2. Ladders: at most 3 storeys, no interior, at most 3 per map. Lifts: 3 to 6 storeys, at most 3 pairs per map. The upper limit and the lift cap are decisions of this ADR: a higher roof overlooks the whole map, and a pair is two choke points.
3. **Fairness.** No access within 30 m of a spawn. Buildings closest to equidistant between the teams go first, then the lowest. Accesses are at least 30 m apart.
4. **No blocking.** The foot of a ladder and a cabin with 3 m clear in front stay on free ground, 3 m from spawns, objectives, boxes and interior doors, and 1.5 m from bot routes. So there may be no ladder or lift on a map with no room, and that is accepted.

**A ladder keeps the prefab's shape.** The adapter raises it until `VaultTargetUpper` is on the roof, and adds rungs at the same spacing down to the ground. The top rungs and the vault stay as the SDK made them, and the hand reach does not change. Under 5.1 m the lowest rungs are simply below the ground.

**A lift is one cabin in the street and one on the roof**, doors facing away from the wall line.

**Every flat parapet now has a collider**, not only those of a roof with a stair: any roof may get a ladder or a lift at the layout stage, which comes after detail, and a parapet that bullets cross is not cover. Roofs nobody reaches pay a few collision triangles.

**A simple visible model.** Neither prefab has a mesh, and it is not known whether the game draws one. The adapter adds rails and rungs, or four posts and a roof outside the cabin's size: one mesh and one draw call each, no collider, lit by probes. If the game draws its own, these are redundant but not in the way.

## Rejected alternatives

- **Stretching the prefab or its rungs.** 9 m would put rungs about 0.55 m apart and change the hand reach and the vault's height.
- **Stacking two ladders.** The first one's vault would push a player into the air.
- **Placing at the detail stage.** That stage does not know the spawns, so it could not be fair.
- **Choosing the nearest roofs to the focus.** It would make one corner the fight of every match.

## To check on the headset

- Whether the game draws the ladder and the cabin, and so whether the added models are needed.
- Climbing a ladder with added rungs, and the vault over a parapet of 1 m, which is higher than the top rung (0.57 m above the roof).
- A lift between different heights. `ExampleScene` only shows the same height.
- Whether 3 and 3 are the right number for a match, and whether a roof overlooks a spawn: the rule measures distance, not line of sight.
