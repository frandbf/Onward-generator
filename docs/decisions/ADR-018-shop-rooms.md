# ADR-018 — Shop rooms that fit large shops and necked footprints

Status: accepted · 30 October 2026.

## Context

Every building with a `shopfront` ground floor gets a one-storey generic shop room (`docs/07-detail.md`, *Shop interiors*). On a real Madrid map (40.40657, −3.719187, r150, Catastro split, 84 buildings) the three shop buildings got no room: two were refused `inner_space_too_small` and one `shop_too_large` (1 181 m² above `interiors.shops.max_area_m2`, 600 m²). On Murcia centre (r150) 13 of 57 shop buildings were refused the same way (9 and 4).

Diagnosis of `inner_space_too_small`. The room is the footprint shrunk by the 0.3 m wall. The plan accepted only a single polygon of at least 20 m²:

- **Necks.** Catastro sub-buildings (ADR-016) often keep a strip narrower than 0.6 m where a parcel boundary meets the OSM outline, or a long thin lot joined to a wider one. Shrinking by the wall cuts there, so the inset is a multipolygon: Madrid `9131401VK3793A_part13` became pieces of 169 and 46 m², `9033801VK3793C_part18.2` pieces of 129 and 61 m² plus three of 2 m². A thinner wall does not help: at 0.15 m they are still split. Six of the nine Murcia refusals were this.
- **Small lots.** A 16–25 m² sub-building leaves 11–18 m² inside its walls, under the 20 m² that the stair interior needs for a stair well and that was applied to shops too. A shop needs only the door and the 1.5 m clear zone behind it. The other three Murcia refusals were this.

## Decision

1. **The room is the piece the door opens into.** When the inset falls apart, the plan keeps the piece whose ring holds the door's inner corners (the largest piece when none does, which then fails the door test as before). The other pieces stay solid: the colliders already make everything outside the room solid, and windows that do not meet the room keep `glass_dark` (`interior_window_omitted`). This applies to the stair interior too; its rule (rectangularity ≥ 0.8, 80–500 m²) leaves it almost no necked footprint, and neither sample changed.
2. **A smaller shop room.** `interiors.shops.min_room_area_m2` (9 m², validated 4–100) replaces the 20 m² floor for shops. The stair interior keeps 20 m² (`MIN_ROOM_M2`). The wall thickness and the door clearance are unchanged: a necked footprint is not fixed by thinner walls, and the clearance is what makes the door walkable.
3. **A band room for a large shop.** A shop footprint above `max_area_m2` is no longer refused. Its room is cut to the band `interiors.shops.band_depth_m` (12 m, validated 3–50) deep behind the wall of the main door, measured from the outer face; the room's own wall ring closes it at the back, and what lies behind is solid. The interior record has `room_depth_m`. The budgets (`shops.max_triangles`, `shops.max_collision_triangles`), the shop order (nearest the centre first) and the light cap (`interiors.lights_max`, shops first) apply unchanged.

## Consequences

- Madrid r150: 0 → 3 shop interiors, 12 → 15 lights. Murcia r150: 40 → 49 shop interiors; the drawn shop budget now binds (5 980 of 6 000, 4 shops turned down by it); the 24 lights were already full. No critical issue on either.
- A band room shows its back wall through the shopfronts, and windows deeper than the band are dark: the room is generic, not the real shop.
- The issue reason `shop_too_large` is gone. `inner_space_too_small` now means that the room behind the door is under the minimum area.

## Rejected alternatives

- **Thinner inner walls on narrow lots.** Measured at 0.2 and 0.15 m, the necked footprints still split.
- **Raising `max_area_m2`.** A 1 000 m² room behind two shopfronts costs light reach and triangles for space the player cannot see from the street.
- **A room in every inset piece.** A piece behind no door is unreachable; it would cost a light and triangles to be seen only through a window, if it has one.
