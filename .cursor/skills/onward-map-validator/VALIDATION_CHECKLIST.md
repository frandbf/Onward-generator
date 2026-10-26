# Onward map validation checklist

Use alongside the Cursor skill. This is a working checklist, not a substitute for the latest official guide.

## Project
- [ ] Unity version checked against the guide for the installed SDK
- [ ] SDK version identified
- [ ] Intended scene identified and opens without import errors
- [ ] Missing scripts, broken GUIDs and missing prefabs checked
- [ ] Project backed up / version-control checkpoint created

## Scene and assets
- [ ] Free Roam setup present
- [ ] At least one additional supported game mode configured
- [ ] Spawn points inside playable geometry and clear of obstacles
- [ ] Collision checked on floors, walls, stairs, ramps and cover
- [ ] Materials/shaders compared against installed SDK samples
- [ ] Lighting/lightmaps configured and baked as required
- [ ] Asset provenance/licensing recorded
- [ ] Quest performance risks inspected; actual device test scheduled

## Hunt / AI (if intended)
- [ ] Hunt mode setup compared with official SDK example
- [ ] Objective prefab/component and all required references checked
- [ ] Navigation generated/saved using the installed SDK workflow
- [ ] AI spawners and waypoints are on navigable surfaces
- [ ] AI can navigate the playable area
- [ ] Objective activation and completion flow tested
- [ ] Map appears in the Hunt selection/list in Onward

## Runtime and release
- [ ] Unity Console reviewed
- [ ] Free Roam tested in Onward
- [ ] Each intended mode tested separately
- [ ] Standalone Quest test completed if Quest is a target
- [ ] Loading, collision, visual artifacts and performance checked
- [ ] Current submission rules and package workflow rechecked
- [ ] Final report states what was and was not tested
