# ADR-041: open colliders for see-through props

Status: proposed, 14 November 2026. Pending a headset check.

## Context

On the headset, Mogadishu 1993's metal fences and gates stopped shots and sight as if they were walls. No prop uses its own triangles as a collider. `fit.collider_mesh` gives a fence the plan of its panels extruded to its full height (one solid strip along the fence, ADR of 5 November 2026 in docs/01), and every other solid role (gate, barrier, fire escape, scaffold) its whole box. The Unity adapter makes each collision node a non-convex `MeshCollider` with the Concrete physic material, so that collider stops bullets, bot sight and the player alike. Worst case: `large_iron_gate`, 84 bars, collided as a solid 2.9 × 2.9 m plate.

## Options

1. Keep the slab and name it `#glass` (SDK Glass, `AISeeThrough`): no triangles, but the bars stop nothing and hits read as glass.
2. The prop's own triangles as the collider, for every see-through prop.
3. One collider per welded part: the fence rule on each part, so a bar, a rail or a panel collides as itself and the gaps between parts stay open; the own triangles only past a part cap.

## Decision

Option 3. A solid prop is open when its role is `fence`, `gate`, `fire_escape` or `scaffold`, or when it is a `barrier` whose triangles cover less than half its front face (`fit.face_fill` under `OPEN_FILL_MAX` 0.5: metal crowd barriers, not jersey blocks). Its collider (`fit.open_collider`) is the fence rule on each part welded at 1 mm, or a copy of its own triangles above `OPEN_PARTS_MAX` 64 parts (the fire escapes, 121 and 213 parts). The rules dressing and an AI plan write it through `fit.prop_collider` and mark the node `extras.open`. Spacing and `fit`'s pile check keep the old footprint, so placement does not change. The gameplay report reads an open collider as kind `open`, neither cover nor a sight blocker, and `perf cover` refuses an open prop as cover (`see_through`).

A gap a hand wide is narrower than the player's capsule, so a body still cannot pass; bullets are raycasts and pass.

## Cost

Measured on the library: `large_iron_gate` 12 → 1 384 collision triangles, the fire escapes 12 → 1 657 and 1 716, `fence_door_metal` 220 → 736, `barrier_low` 12 → 72; the Kenney fences stay within ±20 of their old count. Prop colliders are not counted against the 120 000-triangle collision budget, which `detail` checks before props are placed; colliders have no renderer, so draw calls do not change.

## Not checked here

On the headset: shots through a gate and a fence, a body still stopped, and no navmesh island on a rail (bars are excluded from the navmesh sources: the adapter bakes ground only).
