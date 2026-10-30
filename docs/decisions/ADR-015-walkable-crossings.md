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
2. **A separate collider, not a new surface.** The crossings stay part of `surface/carriageway` (the visual asphalt and the curb walls do not change; the surface partition and its overlap and gap checks do not change). Only the ground slab is split: `collider/carriageway` is the carriageway minus the crossings, and `collider/walkable_crossing` is the crossings, at the carriageway level (top −0.15 m). The kerb step of 0.15 m is under the agent's 0.3 m climb, so Recast joins the strip to the sidewalk on each side.
3. **Adapter.** `collider_walkable_crossing#collider` already passes `WalkableGround` (`walkable`, no `carriageway`), so the bake needs no new rule. The physics material stays Concrete. A new diagnostic line, `Navmesh islands: N. The origin island holds A of T m² (P%)`, is logged before `GenerateNodes`, from `NavMesh.CalculateTriangulation`.
4. **Python mirrors it.** `nav_islands` is the free ground plus `partition.crossings`, shrunk by the 0.35 m agent radius. When the crossings join the city, the Uplink's island is the whole network, so `hunt_routes` takes its first branch and `spread_routes` spreads the 32 starts over all of it. Routes still stop at the first point off their island, so a block that no crossing reaches (water, a closed courtyard) keeps the previous behaviour.

## Cost

- Collision: one more closed slab, a few triangles per crossing.
- Navmesh area grows by the crossings only (a 6 m street with crossings every 50 m adds about 8 % of its carriageway). The node spacing from the 320-node budget barely moves.
- The 400-node chain is now measured over the whole connected city instead of one island. The adapter already raises the spacing up to 6 times when the chain is too deep; a large map may end on a coarser spacing. If it still does not fit, the scene step throws as before.

## Not checked here

C# cannot be compiled on the Linux host. To check in Unity (`task unity`) and on the headset:

- The log line `Navmesh islands:` reports one large island holding most of the area, and the per-attempt `Navigation spacing ...` line keeps a depth at or under 400.
- `Waypoint nodes anchored` reports no waypoint tens of metres from its node, and few or no routes dropped for no return path.
- On the headset, Hunt bots patrol both sides of a street and cross on the strips, and do not stand on the road elsewhere.
