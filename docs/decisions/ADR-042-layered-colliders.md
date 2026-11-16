# ADR-042: layered colliders for solid props

Status: proposed, 15 November 2026. Pending a headset check.

## Context

On the headset, Mogadishu 1993 plays well, but a shot into the air beside a crashed helicopter or a car hit something. `fit.collider_mesh` gave every solid prop that is not a tree, a lamp or a fence its whole box, and a pinned prop the fence rule: the plan of all its triangles extruded from its lowest point to its highest. The Unity adapter makes each collision node a non-convex `MeshCollider` with the Concrete physic material, so that solid stops bullets, bot sight and the player alike.

Measured on the library, the share of the collider's silhouette with no triangle in front of it (seen from the front and from the side): the Black Hawk 86 % and 69 % (under the blades, above the tail, between the fuselage and the rotor; posed on its side it is worse), the cars 24 to 42 %, the concrete road barrier 47 % from the front (a trapezoid in a box). A barrel is 1 %.

## Options

1. The prop's own triangles as its collider: exact, but 8 000 physics triangles a car and 12 600 a helicopter, and a shell with holes lets a shot through where it looks closed.
2. A convex hull per welded part: fewer triangles, but a car is one part, so the air over its bonnet stays.
3. Slices: the height cut into slices of at most 30 cm, each a prism of the plan its triangles cover inside it.

## Decision

Option 3, with 30 cm slices (the maintainer's choice; 15 cm halves the remaining air and doubles the triangles). `fit.layer_plans` cuts the height into the fewest equal slices no taller than `LAYER_M` 0.3 m. A triangle that crosses a slice is clipped to it. A flat triangle on a boundary counts on the side it faces away from (by its winding): a box's top in the slice below, a ledge's underside in the slice above, so neither reaches into the next slice; the prop's foot counts in the first slice and its top in the last. Each piece's plan is widened by `LAYER_PAD_M` 1 cm, so an upright wall, flat in plan, still has one. The slice's plan is the union, simplified to `LAYER_SIMPLIFY_M` 2 cm, with its holes filled: a car's cabin is one solid, not a ring of pillars, so a shot through its windows still stops, as bot sight does. A slice no triangle reaches is left open; a slice whose plan lies within 2 cm of the one below (their Hausdorff distance) is merged into it, keeping the lower plan. `fit.layered_collider` makes a closed prism of each slice; a flat prop keeps its box.

`fit.solid_collider` gives a tree its trunk box and a lamp its post, as before, and every other solid role the layered collider. The rules dressing and an AI plan write it through `fit.prop_collider` (a see-through prop keeps its open collider, ADR-041), and the procedural wrecks, the pinned props and the `perf cover` props write it too. Spacing, `fit`'s pile check and the cover stage's passage test keep `collider_mesh`'s whole box, so placement does not change.

## Cost

Measured on the library at street size, old collider triangles to new, and the air in the front silhouette:

| Prop | Triangles | Air in front |
| --- | ---: | ---: |
| `uh60_black_hawk` | 648 → 4 952 | 86 % → 40 % |
| `abandoned_auto` | 12 → 656 | 24 % → 17 % |
| `destroyed_car_03` | 12 → 1 764 | 31 % → 22 % |
| `burned_police_cars` | 12 → 1 760 | 33 % → 20 % |
| `concrete_road_barrier` | 12 → 96 | 47 % → 22 % |
| `Barrel_01` | 12 → 36 | 1 % → 4 % |

The air left is the step of the slices (a slice reaches its top even where the bonnet ends half way up it) and, on the helicopter, the blades: thin and sloped, each slice holds them edge to edge. A barrel gains the 1 cm widening. A layered collider takes about half a second for an 8 000-triangle car. Prop colliders are not counted against the 120 000-triangle collision budget, which `detail` checks before props are placed; colliders have no renderer, so draw calls do not change.

## Not checked here

On the headset: shots just over a car's bonnet and under a helicopter's blades pass, a body is still stopped by both, and bots still take a car as cover.
