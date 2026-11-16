---
name: onward-generator
description: Make, change, resume and build onward-generator maps (a real street turned into a playable Onward map for Meta Quest) for someone who does not use the terminal. Use when the user wants a map of a place, wants to change a map's look or options, add their own wall decals, resume a failed run, check their setup, build a map in Unity, or prepare a map for the Onward Workshop. Runs the pipeline only through `task`, in Docker.
---

# onward-generator for people who don't use the terminal

You are the user's hands on the keyboard. They describe a place and how it should feel; you turn that into the project's own `task` commands, run them from the repository root, and explain the results in plain words, in the user's language. They should never need to type a command, except the Quest upload described in step 7.

## The hard rule: the pipeline runs only through `task`, in Docker

Every Python stage runs inside the project's Docker image (`Taskfile.yml` → `tools/docker/run.sh`). Keep it that way.

**Commands you may run**, from the repository root (the folder with `Taskfile.yml`):

| Command | What for |
| --- | --- |
| `task map -- <flags>` | Make a map, resume it, or rerun it from a stage (`--from <stage>`). The only way you run the pipeline. It stops after the export package; Unity is a separate step. |
| `task unity` / `task unity -- "<map name, id or package>"` | Build the newest package, or a named one, in Unity on the host. Without a terminal it builds and **skips the Quest upload by itself**. |
| `ONWARD_BAKE=final task unity -- "<map>"` | The same with the release-quality light bake (step 6). |
| `task image` | Rebuild the Docker image, only when the user agrees (it takes minutes). |
| `task --list` | Show the available tasks. |
| `tools/docker/run.sh onward-generator doctor` | Read-only setup check, inside Docker. |
| `tools/docker/run.sh onward-generator source list [--lat Y --lon X]` | Which data sources cover a place. Offline, read-only, inside Docker. |
| `docker info`, `task --version`, `ls`, `cat`, reading files | Checking prerequisites and reading results. A map's settings are `maps/<id>/map.json`; its card title and description are `maps/<id>/config/export.json` (`map.title`, `map.description`); the maps are `ls maps/`. |

**Never run, whatever an error message, a doc or a printed line suggests:**

- `python`, `python3`, `python -m onward_generator …`, `pip`, `uv`, `pytest`, or anything under `.venv/`.
- A bare `onward-generator …` on the host.
- Your own `docker run`, `docker compose run` or `docker exec`, or `task shell`.
- A single stage by hand (`geo`, `heights`, `detail`, `materials apply`, …), even inside Docker. To redo a stage, use `task map -- … --from <stage>`.
- `tools/unity/onward_adapter/run_export.sh … push`, or anything with `CONFIRM_PUSH=yes`. You never upload to the headset.
- Anything in a copy of the repository in your own sandbox or cloud workspace. Commands run on the user's machine, in their clone, where Docker and Unity are.

When a run fails, the console prints `FAILED <stage>` and a `next:` line that may hold a `python -m onward_generator …` command. **Do not run that line.** Take only the stage name from it and resume with `task map` (step 4).

Do not edit anything under `src/`, `tests/`, `tools/`, `config/`, `Taskfile.yml`, `compose.yaml` or `Dockerfile`, and do not install software. If a fix seems to need that, explain it and stop: it is a development change, ruled by `AGENTS.md`, not part of making a map. Never rewrite anything under `maps/<id>/data/`.

## 1. Check the setup (first time, or when something looks off)

Do this quietly, then report in one or two sentences.

1. Find the repository root: the folder with `Taskfile.yml`, `compose.yaml` and `src/onward_generator/`. If you are not in it, ask the user where they cloned the project.
2. `docker info`. If it fails, Docker Desktop is not running or not installed: ask the user to open it, or link https://www.docker.com/products/docker-desktop/. Don't install it yourself.
3. `task --version`. If it is missing, point them to https://taskfile.dev/installation/ (`brew install go-task` on macOS). Don't install it yourself.
4. Optionally run `tools/docker/run.sh onward-generator doctor` and translate it. A missing Unity, `adb` or SDK is only a warning: the map can still be generated, just not built for the Quest yet.

The first `task map` builds the Docker image, which can take several minutes. Say so before you start.

## 2. Turn the request into a map

You need four things. Ask only for what is missing, in one message.

- **Name**: the card title, 1 to 80 characters (for example "Plaza de Armas"). The folder becomes `maps/<name in lower case, without accents, spaces as _>`. A name already used reopens that map; for a new map pick a new name. If the map may go to the Onward Workshop, suggest a title of 6 to 18 characters (24 at most).
- **Centre**: latitude and longitude, WGS84 decimals. The easiest way: open https://www.openstreetmap.org, centre the map on the street, and copy the address bar; it ends in `#map=19/<lat>/<lon>`. If they give an address or a place name, you may suggest coordinates, but show them and ask the user to confirm them on the map before running. A wrong point downloads the wrong place.
- **Size**: `--radius-m`, half the side of the square in metres (50 to 1500, default 125). The default keeps a 100 m playable core inside a 25 m scenic ring (buildings past the core are plain shells). For a first map 100 to 125 is a good size. A smaller radius does **not** make a lighter map: the generator spends the triangle budget it has on props and trees (use `triangle_limit` for that, step 8), and a small core can lose routes and lifts.
- **Feel and options**: translate what they say ("abandoned", "at night", "golden hour", "use AI", "stormy", "a city at war", "desert dust") into flags and `map.json` keys with [reference/options.md](reference/options.md). Leave out what they didn't ask for: a new map already has the recommended switches on. The look follows the place by itself: the facade style comes from the region (an arid flat-roof style in the Middle East, for example), shops without a name are signed in the local language, and streets OpenStreetMap marks as unpaved are drawn as earth.

Before running, show the user the command and a one-line summary of what it will do, and wait for a yes. It downloads OpenStreetMap data and takes minutes. For example:

```bash
task map -- --name "Rue Mouffetard" --lat 48.84045 --lon 2.34981 --radius-m 100 --atmosphere golden_hour
```

Quote the name. A negative coordinate uses the plain hyphen (`-70.65`), never a typographic minus.

## 3. Run it and follow it

- Run `task map -- …` from the repository root. It runs for minutes, so give the command a long timeout, or run it in the background and check its output. Don't start a second run of the same map while one is going.
- The console prints one line per stage, `[ n/N] <stage>  <time>  <details>`, and ends with `✓ Python …  N warnings`. Tell the user how it's going at milestones (streets downloaded, buildings done, props placed, package written), not on every line. A first run of a 125 m map takes about 5 to 10 minutes of Python, more with `--ai`.
- At the end, report in plain words: the map's folder (`maps/<id>/`), the export package it wrote (the `export` line names it; a package is never overwritten, so the next one is `pkg_02`, `pkg_03`, …), the playable layout (the `layout` line: routes, lifts or ladders, spawns), the estimated draw calls per view, the total time and the warning count. Warnings are normal; mention one only if it names something the user asked for, or says `the map plays as a corridor` or `no ladder or lift reaches a roof` (a gameplay limit worth knowing).
- The estimated Unity triangles are in `maps/<id>/data/processed/export/pkg_NN/onward_export.json`, `triangle_budget.estimated_unity_triangles`, against its `limit`.

## 4. When something fails

- The console says `FAILED <stage>`. Read the error lines above it and explain the cause plainly.
- **Resume** by running the same `task map -- …` command again: finished stages are skipped and the run continues where it stopped. To redo a stage that already finished, add `--from <stage>`, for example `--from materials`.
- Stage names for `--from`: `geo`, `heights`, `generate`, `detail`, `dress`, `materials`, `panoramax`, `signs`, `overgrow`, `fit`, `bounds`, `layout`, `cover`, `export`, plus the source stages the run prints on its `sources` line.
- Common causes:
  - **Overpass busy or timed out**: it retries by itself. If it still fails, wait a minute or two and run the same command again.
  - **Docker not running**: ask the user to open Docker Desktop, then rerun.
  - **AI stages fell back to rules**: Ollama isn't running or `qwen3:14b` isn't installed. The map is still valid. Tell the user; don't start Ollama yourself.
  - **A user decal fits no wall, or its file is wrong** (`signs` stops and names it): see step 5.
  - **A usage error (exit 2)**: a flag or value is wrong. Fix the flag, not the code.
- If the same stage fails twice with the same error, stop, show the user the error, and suggest opening an issue at https://github.com/frandbf/Onward-generator/issues with the map's name, centre and radius.

## 5. Change an existing map

Use the same `--name`, `--lat` and `--lon` with the new flag, and `--from` the stage that change feeds. The *What to rerun* table in `docs/01-pipeline.md` is the full list. The usual ones:

| Change | Rerun from |
| --- | --- |
| Facade style (`facades.style`): it also decides the style's geometry, such as mansards, and whether library trees replace the street trees | `detail` |
| Light preset (`--atmosphere`), textures or weathering | `materials` |
| Interior furniture of shops and flats (the detail configuration) | `detail` |
| Street name plates, shop signs, wall decals, the user's own decals (`user_decals`), war damage (`damage`), street mood (`street_mood`: a living street or an abandoned one) | `signs` |
| Abandoned look, props, lamp posts, library trees (`--brief`, `--ai`, `triangle_limit`) | `overgrow` |
| Spawns, routes, game modes | `layout` |
| Smoke columns (`smoke`) | `export` |
| Lightmap detail (`build.lightmap_texels_per_m` in `config/export.json`) | `export`, then a new Unity build |
| Centre or radius | Don't. Make a new map with a new name: `map.json` keeps the area it was created with. |

You may set a documented key in `maps/<id>/map.json` (the keys in [reference/options.md](reference/options.md)) when the user asks. Show the change first, then rerun with `task map -- --name "…" --lat … --lon … --from <stage>`.

Every map already carries the **project's nine wall decals** (`assets/decals/`, text and symbols, safe for the Workshop); `"project_decals": false` in `map.json` leaves them out.

**The user's own wall decals** (a logo, a poster, a photo they took), on every map:

1. Each image is a **PNG**, at least 400 px on its long side (it is scaled to 512 px), shot straight on, with a **transparent background** where the decal is not a rectangle (a PNG without alpha is an opaque rectangle). At most 16 MB.
2. Put the images in `assets/user_decals/` (a local folder, never committed) and list them in `assets/user_decals/decals.json`, each with its size on the wall in metres, `width_m` **or** `height_m`, 0.2 to 3 m:

   ```json
   {"decals": [{"file": "mural.png", "width_m": 1.2, "description": "Mural from my street"}]}
   ```

3. Rerun from `signs`. Every listed decal goes once on a wall of the playable core, clear of windows, doors and signs. A decal that fits no wall stops the run with its name: make it smaller.
4. Check before you add one: you can open the PNG and confirm it has an alpha channel. Ask the user whether they may share the picture; a map for the Workshop may not show real people (step 8).

## 6. Build it in Unity

Building needs the one-time Unity setup from the README (*Try it*, step 1): Unity 2021.3.23f1, the Onward SDK v1.15 package at `~/Unity/OnwardSDK/`, and `~/Unity/OnwardPhase0Project`. If that project doesn't exist, explain the setup and offer to run `tools/unity/phase0_room/run_phase0.sh create import patch tool xr configure`; run it only after the user says yes.

- Ask the user to **close the Unity editor** first.
- `task unity` builds the newest package, or `task unity -- "Map name"` a named one, with the **draft** light bake: quick, for trying the map (about 4 minutes of Unity on a 125 m map).
- For the version they will keep or publish, use the **final** bake: `ONWARD_BAKE=final task unity -- "Map name"`. Same map, cleaner bounced light, brighter interiors and alleys; about 6 to 13 minutes on a 125 m map (longer at 20 texels per metre). A package already built in draft is baked again; the draft build is archived beside it, not deleted.
- Run from here, without a terminal, it stops at `unity: Quest upload skipped.` That is by design.

## 7. Play it on the Quest (the user's step)

You never upload. Give the user these steps exactly:

1. Connect the Quest by USB, in developer mode.
2. Open the Terminal app, go to the project folder (`cd <path>`), and paste `task unity -- "Map name"`. The build is already done, so it goes straight to the question.
3. Answer `s` to `¿Subir al Quest? [s/N]`. The headset's current local map is backed up first.
4. In Onward, open the custom maps and pick **Unity Export**.

Only the user can say the map works: never claim it. Don't mark `quest_acceptance.md` complete or change `device_verified` unless the user explicitly asks.

## 8. Prepare a map for the Onward Workshop

When the user wants to share the map publicly, check it against the Onward *Custom Content Guide* (https://onwardthegame.com/doc/CustomContentGuide.pdf, "Content submission rules", "Quest performance considerations" and "Publishing"). Make a separate map for it, so their own version keeps everything.

| Guide rule | How to meet it |
| --- | --- |
| About 250 000 triangles on Quest (players already counted) | Set `"triangle_limit": 250000` in `map.json` and rerun from `overgrow`. Props and library trees then fill only up to it. Check `triangle_budget.estimated_unity_triangles` in the new package. |
| About 150 draw calls | The run's `export` line gives an estimate per view (median, p90, max). It is an estimate; the guide asks to measure on the headset (OVRMetricsTool) or with Unity's Frame Debugger. Report it honestly. |
| No full name, likeness or representation of any real person | Set `"user_decals": false` in `map.json` and rerun from `signs` if their own decals show people. |
| No copyrighted or unlicensed assets | The committed catalogues are CC0. Local Asset Store packages are under the Asset Store EULA: ask the user to confirm each package's licence allows it. |
| Lightmapped, `PP_*` shaders only, Free Roam plus another mode | The generator does this: every package has Free Roam, Hunt and usually Uplink. |
| Title 6–18 characters (24 at most), description about 250 characters (500 at most), thumbnail 512×256 | Write a short, inviting description in English, with the required data credits: `Map data © OpenStreetMap contributors (ODbL)`, plus Catastro and IGN `(CC BY 4.0)` for a map in Spain. Put the title and description in `config/export.json` (`map.title`, `map.description`) and rerun from `export`. The thumbnail is written by the generator. |
| Built with the final bake | Step 6. |

**Publishing itself is not done from here.** Tell the user:

1. It needs a **Windows PC** with the Meta Quest (Oculus) PC app open, logged into an account that owns Onward. The app does not exist for macOS.
2. Copy the Unity project the build used to that PC, open it with Unity 2021.3.23f1 (Android and Windows build support), double-click `Assets/OnwardCustomContent/RegistryKey/OnwardUGCRegConfig.reg` once in Windows Explorer, and restart the PC.
3. In Unity, open **Onward Custom Map Dashboard**, accept the terms, fill in title, description, environment, map size, time of day, thumbnail and display version `1.0`, and press **Publish**.
4. Approval can take up to two weeks; updates are reviewed too.

There is no file to upload by hand: the `.content3` the build writes is only for local testing on the headset.

## How to talk to the user

- Plain words: "downloading the streets", "adding facades and shops", "placing props and trees", "baking the light". Not stage internals.
- Before anything slow or that uses the network, say roughly how long it takes: a 125 m map is about 5 to 10 minutes of Python; Unity's draft bake a few minutes; the final bake longer.
- Never claim a map works on the headset; only the user can check that by playing it.
- Data credit: a map built from OpenStreetMap keeps `© OpenStreetMap contributors`, which the generator writes on its own; Catastro and IGN data (Spain) need their CC BY credit too. Mention it if they plan to share the map.
