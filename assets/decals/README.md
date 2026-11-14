# Project wall decals

Wall decals that ship with onward-generator and go on every map it builds, each once, on an exposed wall of the
playable core, before the generated decals ([ADR-038](../../docs/decisions/ADR-038-wall-decals.md), amendment of
14 November 2026). `decals.json` lists each PNG with its size on the wall (`width_m` or `height_m`, 0.2 to 3 m).

Made by the maintainer for this project and released with it under its [MIT licence](../../LICENSE). They show
text and symbols only, no person, so a map for the Onward Workshop may keep them. A map leaves them out with
`map.json` `"project_decals": false`. Your own local decals go in `assets/user_decals/` instead (README).
