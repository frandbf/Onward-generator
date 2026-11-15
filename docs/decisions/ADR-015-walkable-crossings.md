# ADR-015 — Walkable crossings join the navmesh across the carriageway

Status: accepted · 30 October 2026.

## Context

The adapter bakes the navmesh from the ground colliders whose name passes `WalkableGround` in `OnwardMapAdapter.cs`: `walkable`, `pedestrian`, `sidewalk`, `ground`, `green`, and never a name with `carriageway`. Phase 03 writes two ground slabs, `collider/walkable` (the site minus the carriageway, top 0) and `collider/carriageway` (top −0.15 m). So every block between streets is its own navmesh island unless a pedestrian way or a plaza joins it to the next.

No document or commit gives a gameplay or SDK reason for leaving the carriageway out; `git log -S WalkableGround` stops at the first public commit. The effect is what PR #38 measured: `GenerateNodes` keeps only the nodes that reach the navigation origin (the Uplink), so Hunt routes were moved onto the Uplink's island (`hunt_routes`, `nav_islands` in `perf/layout.py`) and the 32 bots patrol one part of the city. A Madrid run showed waypoints 30–232 m from any node and routes dropped.

The SDK constraints that any fix has to keep (`docs/11-onward-sdk.md`):

- The game walks the `AI_NavBake` node graph that `GenerateNodes` writes, not the Unity navmesh. Unity area costs do not reach that graph.
- The Quest walk does not remember seen nodes: its chain must stay at most 400 nodes. The adapter sizes the node spacing from the baked area (a 320-node budget) and the longest crossing, and raises it while the chain is too deep.
- Each link must keep its return; the adapter refuses one-way links.

## Options

| Option | Effect on the node graph | Verdict |
| --- | --- | --- |
| (a) Walkable crossing strips over the carriageway, a separate slab | A few square metres per street. The spacing budget barely moves; nodes stay on the sidewalks except a short line on each crossing | **Chosen** |
| (b) Bake the whole carriageway, with a higher area cost | Adds 30–40 % of the site to the area. Spacing grows for the whole map, nodes and cover points land in the middle of the road, and the cost is a Unity area cost that `GenerateNodes` and the game's walk do not read | Rejected |
| (c) NavMesh links (`OffMeshLink` / `NavMeshLink`) | Not known whether `GenerateNodes` or its reachability test follows a link, or how a link is written into `AI_NavBake`; a link is one-way unless set otherwise, and the adapter refuses one-way links. Cannot be checked without Unity | Rejected |

## Decision

1. **Crossings in the partition.** `build_partition` (`generation/surfaces.py`) computes `Partition.crossings`: the vehicular centrelines are noded at every junction (`union_all` then `line_merge`), and each segment between two junctions or dead ends gets a 4 m strip square to the street:
   - at each end, set back by half the widest street meeting there, plus 1 m, plus half the strip, so the crossing clears the junction;
   - between them, evenly, so no stretch is longer than 50 m without one; a segment too short for two gets one halfway;
   - each strip is as long as the widest street the segment touches plus 2 m, then clipped to the carriageway surface and snapped like every other surface. Vehicular areas (width 0) get none.
   Constants: `CROSSING_WIDTH_M` 4, `CROSSING_SPACING_M` 50, `CROSSING_CORNER_M` 1. They are deterministic and depend only on the OSM ways and their widths.
2. **A separate collider, not a new surface.** The crossings stay part of `surface/carriageway` (the visual asphalt and the curb walls do not change; the surface partition and its overlap and gap checks do not change). Only the ground slab is split: `collider/carriageway` is the carriageway minus the crossings (snapped again, and pieces that share a corner opened, so the slab stays closed), and `collider/walkable_crossing` is the crossings, at the carriageway level (top −0.15 m). The kerb step of 0.15 m is under the agent's 0.3 m climb, so Recast joins the strip to the sidewalk on each side.
3. **Adapter.** `collider_walkable_crossing#collider` already passes `WalkableGround` (`walkable`, no `carriageway`), so the bake needs no new rule. The physics material stays Concrete. A new diagnostic line, `Navmesh islands: N. The origin island holds A of T m² (P%)`, is logged before `GenerateNodes`, from `NavMesh.CalculateTriangulation`.
4. **Python mirrors it.** `nav_islands` is the free ground plus `partition.crossings`, shrunk by the 0.35 m agent radius. When the crossings join the city, the Uplink's island is the whole network, so `hunt_routes` takes its first branch and `spread_routes` spreads the 32 starts over all of it. Routes still stop at the first point off their island, so a block that no crossing reaches (water, a closed courtyard) keeps the previous behaviour.

5. **Paint only where OSM has a crossing.** The strips stay where the rule puts them. A strip within 4 m of an OSM crossing node or `footway=crossing` way gets zebra bars, a visual mesh with no collider (`surface/zebra_crossing`, `docs/06-generation.md`). The other strips are walkable but unpainted, and the manifest records each strip as `observed` or `generated`.

## Cost

- Collision: one more closed slab, a few triangles per crossing.
- Navmesh area grows by the crossings only (a 6 m street with crossings every 50 m adds about 8 % of its carriageway). The node spacing from the 320-node budget barely moves.
- The 400-node chain is now measured over the whole connected city instead of one island. The adapter already raises the spacing up to 6 times when the chain is too deep; a large map may end on a coarser spacing. If it still does not fit, the scene step throws as before.

## Not checked here

C# cannot be compiled on the Linux host. To check in Unity (`task unity`) and on the headset:

- The log line `Navmesh islands:` reports one large island holding most of the area, and the per-attempt `Navigation spacing ...` line keeps a depth at or under 400.
- `Waypoint nodes anchored` reports no waypoint tens of metres from its node, and few or no routes dropped for no return path.
- On the headset, Hunt bots patrol both sides of a street and cross on the strips, and do not stand on the road elsewhere.

## Amendment, 14 November 2026: unpaved streets are walkable all along

The carriageway of a vehicular way whose OSM `surface` names no paving (`UNPAVED_SURFACES`: unpaved, dirt, earth, ground, sand, gravel, fine_gravel, compacted, mud, grass, pebblestone) joins the crossings (`Partition.crossings`), so it is part of `collider/walkable_crossing` and of the bots' navmesh from end to end, not only at its crossing strips. A paved street, or one with no `surface` tag, keeps the rule above. Zebra paint is unchanged: it comes from `crossing_sites` and the OSM crossings. In Mogadishu 1993, 23 of 29 streets are `surface=unpaved`, and the navmesh without them cut the town into blocks the bots could not leave. A map built before this keeps its navmesh until it runs again from `generate`.

## Amendment, 14 November 2026: the navmesh ground comes from the layout

On the headset, Mogadishu 1993's bots walked through house corners. The ground collider `collider/walkable` runs under every house, and the adapter cut the houses out with 2.6 m `Not Walkable` boxes on a 2 m grid, where a ray hit the building. Reproduced on the package's footprints, that raster left 0.5 to 0.95 m of walkable strip inside every one of the 231 buildings (1 525 m² in all, strips up to 15 m long along party walls), and the SDK's node graph links nodes on that mesh in straight lines up to 1.5 × the spacing (9.9 m there). The building colliders themselves are closed rings; they are not navmesh sources.

`perf layout --playable` now writes `nav_ground` into `layout.json`: the islands of `nav_islands` before the agent radius (free ground at 0, the crossings at the carriageway's top, inside the core), triangulated with every footprint a hole, as flat x, y, z triples and triangle indices. The adapter bakes that mesh alone when the layout has it, so the walls are the navmesh border and the 0.35 m agent radius keeps bots off them; lift cabins are still blocked. A layout without it bakes as before. The ground colliders are unchanged: players still stand on `collider/walkable` inside houses, and phase 03 output stays the same, so existing maps rerun from `layout`.

Routes also stop at the first leg whose straight segment leaves the island grown by the agent radius, and drop their last points until the loop's closing leg stays on it: 15 of 88 legs and 7 closing legs of Mogadishu 1993 crossed a footprint.

**Pending:** a headset session on Mogadishu 1993 (bots keep off the walls; `Navmesh islands` and the node chain still pass).

