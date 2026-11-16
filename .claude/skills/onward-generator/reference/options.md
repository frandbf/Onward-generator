# Options for `task map`

The flags of `onward-generator run` (what `task map -- …` passes on), and the `map.json` keys a user may ask for. The full contract is `docs/02-configuration.md`; this page translates requests into flags.

## What the user says → what to pass

| The user asks for | Pass | Notes |
| --- | --- | --- |
| A name, a place | `--name "…" --lat Y --lon X` | Required on every run of the map; the same three reopen it. |
| A bigger or smaller area | `--radius-m N` (50–1500, default 125) | New maps only. The playable core is 25 m inside it, at most 150 m. |
| A smaller or larger playable area inside the same radius | `--core-radius-m N` (at least 50; 0 plays the whole map) | New maps only. A larger core costs triangles. |
| "Abandoned", "chaos", "blocked streets", any mood | `--brief "…"` | One sentence, in any language. Overgrow reads it; with `--ai` the local model picks props and decals from it. |
| "Use AI" | `--ai` | Needs Ollama on the machine with `qwen3:14b`. Without it the run falls back to the rules and the map is still valid. |
| Night | `--time-of-day night` | Lit by the street lamps. `--storm` adds lightning and thunder (night only). |
| Warm evening light, cloudy light | `--atmosphere golden_hour` or `--atmosphere overcast` | Day maps only. `neutral` (default) changes nothing. |
| Dry, dusty, desert air; a city after a battle | `--atmosphere dusty` | Day maps only. A warm muted sun, a close sand-coloured haze and dirtier walls. Pairs well with `"damage"` (below). |
| Sharper lighting | `--lightmap-texels-per-m N` (default 10, at most 40) | Higher looks better and bakes much longer; 20 is a good "final" value. |
| Leave out a data source | `--no-source <id>` | `tools/docker/run.sh onward-generator source list --lat Y --lon X` shows them. |
| Use an opt-in data source | `--source <id>` | Only where it covers the centre. |
| Turn one recommended switch off | `--no-<switch>` (below) | A new map has them all on except `--panoramax-atlas`. |
| Redo from a stage | `--from <stage>` | See the skill, step 4. |
| A fixed random seed | `--seed N` | Rarely needed; the seed comes from the centre. |

## Switches (on by default for a new map)

Each has a `--no-` form for `task map` on a **new** map, and is a `true`/`false` key in `map.json` afterwards.

| Switch | What it does |
| --- | --- |
| `street_profile` | Local AI picks facade style, palette, props and abandonment years from the brief (falls back to defaults without Ollama). |
| `repetition` | Calmer texture pools, less visible tiling. |
| `overture_tints` | Facade tints from Overture building colours where they exist. |
| `lidar_roofs` | Roof shapes from LiDAR where a source covers the place. |
| `regional_facades` | Regional facade atlases outside the Mediterranean style. |
| `gameplay_report` | A gameplay report: routes, sightlines, spawn balance. |
| `balanced_spawns` | Spawns placed for balance between teams. |
| `sightline_cover` | Cover where long sightlines cross the street. |
| `wide_library` | 25 extra CC0 photo textures. |
| `normal_maps` | Real relief on ground, roofs and bark (accepted after a headset session). |
| `texture_variants` | Two more calm photos for roofs and plaster walls, one per building. |
| `panoramax_atlas` | **Off by default**; only with `--panoramax`, under development. |

## Other `map.json` keys a user may ask for

Edit `maps/<id>/map.json`, show the change first, then rerun from the stage named.

| Key | Values | Rerun from | What it does |
| --- | --- | --- | --- |
| `"triangle_limit"` | integer, 100 000 to 300 000 (default 300 000) | `overgrow` | The Unity triangle limit props and library trees fill up to, and what the package is checked against. 250 000 for a Workshop map. |
| `"project_decals"` | `true` (default) / `false` | `signs` | `false` leaves out the project's nine wall decals of `assets/decals/` (text and symbols, safe for the Workshop). |
| `"user_decals"` | `true` (default) / `false` | `signs` | `false` leaves out the user's own decals of `assets/user_decals/` on this map (a Workshop map must not show real people). |
| `"damage"` | `"none"` (default) / `"light"` / `"heavy"` / `"auto"` | `signs` | War damage: shell impacts, scorch and shrapnel among the wall decals, more decals with `heavy`, and cracks, scorch and rubble on the ground, with asphalt patches on a paved map and sand drifts on a mostly unpaved map or under the `dusty` light. `"auto"` turns it on (`heavy`) when the street profile reads the brief as wrecked. Ask for it when the user describes a city at war or after a battle. |
| `"smoke"` | `0` (default) to `5` | `export` | Columns of smoke rising from wrecked cars of the playable core, above the roofs. A few (2 or 3) suit a city at war; the headset check of frame rate is pending (ADR-043). |
| `"facades"` | `{"mode": "trim" \| "legacy", "style": "<name>" \| "auto", "weathering": "clean" \| "worn" \| "heavy"}` (each key optional) | `detail` when `style` changes, else `materials` | Facade look. `style` is one of `mediterranean`, `central_european`, `anglo`, `latin_american`, `haussmann`, `east_african`, `arid` or `generic`; it also decides the style's geometry (Haussmann mansards and balconies) and its climate (an arid style keeps the mapped street trees). `weathering: "worn"` or `"heavy"` ages the walls. |
| `"brief"`, `"ambient_ai"` | text / `true`, `false` | `overgrow` | Set by `--brief` and `--ai`; edit them to change the mood or the AI choice later. |
| `"mid_ring_m"` | 10 to 1000 | `detail` | A middle ring of simpler buildings between the core and the scenic ring. |

**Pinned props** (a model the map must carry at a real place, e.g. a monument or a crashed helicopter): write `maps/<id>/config/pinned_props.json` as `{"props": [{"asset_id": "<library id>", "latitude": Y, "longitude": X, "yaw_deg": 0, "search_m": 30, "label": "…"}]}` and rerun from `overgrow`. The id must exist in the library (`assets/*/manifest.json`); the prop keeps its own size and moves to the nearest free spot. To lay a model on its side add `"roll_deg": 80`; add `"sink_m"` (metres, up to 10) when it then floats on a rotor or a corner. Keep the point inside the playable core, or it will not find a spot.

Never edit `center`, `radius_m`, `crs`, `seed` or `paths`: make a new map instead.

## Card title and description

`maps/<id>/config/export.json`, `map.title` and `map.description`, rerun from `export`. The generator appends the data credits to the description. For the Onward Workshop: title 6–18 characters (24 at most), description about 250 characters (500 at most) in English.
