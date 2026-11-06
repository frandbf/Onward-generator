# Contributing

Thank you for helping. This page covers the development setup, the tests and the quality gates. The working rules for every change, by a person or an agent, are in [AGENTS.md](AGENTS.md): read them first. How the pipeline fits together is [docs/01-pipeline.md](docs/01-pipeline.md).

By sending a contribution you agree that it is licensed like the project: code under the GNU AGPL v3 or later, documentation under CC BY-SA 4.0 ([NOTICE](NOTICE)). A new asset under `assets/` keeps its author's licence, which must be one the catalogue accepts and must be recorded in its manifest.

## Environment

- macOS (tested on macOS 26.6.2, Apple M4 Pro) or Linux. The host is detected, or chosen with `--platform macos|linux` or `ONWARD_PLATFORM` ([docs/01-pipeline.md](docs/01-pipeline.md#host-platform)). The Linux Unity paths are the Unity Hub defaults and have not been checked with Unity on Linux.
- Docker and [go-task](https://taskfile.dev), or Python 3.12 or newer (tested with 3.14.6). The pinned dependencies, all binary wheels, are in `requirements.lock`: shapely (GEOS), pyproj (PROJ, with the EPSG database offline), jsonschema, pillow, numpy, certifi and duckdb (Overture GeoParquet, ADR-021; the image also installs its httpfs extension).
- Optional: [Ollama](https://ollama.com) on loopback with a model already installed (tested with Ollama 0.33.2 and `qwen3:14b`), for AI plans, texture selection and overgrow plans. Without it, those steps use a deterministic fallback.
- Unity **2021.3.23f1** and the Onward Custom Content SDK v1.15, both outside this repository. Running Unity itself in a container (GameCI `unityci/editor`) is not set up: it needs a Unity licence file and the SDK project on the machine.

**Docker.** The image installs `requirements.lock`, plus the lint and mutation tools. The repository is mounted at `/work`, so outputs land in `maps/` and belong to your user. The host network is shared, so loopback Ollama works. The first `task` call builds the image `onward-generator:dev`; any command goes through the same wrapper:

```bash
tools/docker/run.sh onward-generator doctor
tools/docker/run.sh --build pytest -q
```

The wrapper rebuilds the image by itself when it is missing or when the Dockerfile, a lock file or `pyproject.toml` changed since it was built: the image carries the SHA-256 of those files (`tools/docker/inputs.sh`) as the label `org.onward-generator.inputs`. Before this, a lock file change (DuckDB, for one) kept the old image and the run failed with `No module named 'duckdb'`. `--build` still forces a rebuild. The wrapper also mounts `${XDG_CACHE_HOME:-~/.cache}/onward-generator`, so the texture tile cache (`docs/08-dress.md`) is kept between runs.

**Local virtual environment.** The README's [Without Docker](README.md#without-docker) section installs it; the editable install provides `.venv/bin/onward-generator`.

Check the environment with `onward-generator doctor` (read-only; a missing Unity, `adb` or SDK is a warning) and `onward-generator validate-config config/murcia-belluga.example.json`. Exit `0` is success, `1` a failed check, `2` a usage error.

## Checks

The checks have the same names locally and in CI, which runs on Linux:

| Task | What it does |
| --- | --- |
| `task --list` | All tasks. |
| `task ci` | Everything CI runs: shellcheck, ruff, mypy, actionlint, smoke, tests. |
| `task test -- -k cadastre` | The tests, with pytest arguments after `--`. |
| `task lint -- --fix` | ruff; `--fix` applies the safe fixes. |
| `task typecheck` | mypy on `src`; only errors missing from `mypy-baseline.txt` fail. |
| `task mutate` | Mutation testing of the lines changed since `main`. |
| `task guard` | Fails when a change edits both `src/` and a quality gate. |

Command lines in the README carry no trailing comments, so they paste as they are.

A pull request is ready when `task ci` and `task mutate` pass. The rules are in the *Quality gates* section of [AGENTS.md](AGENTS.md):

- mypy fails only on errors missing from `mypy-baseline.txt`. That list may only shrink: after fixing a listed error, run `task typecheck:baseline` and commit the shorter file.
- Every suppression names its code and a reason: `# noqa: CODE - reason`, `# type: ignore[code]`, `# pragma: no mutate: reason`.
- `task guard` fails a pull request that edits both `src/` and a gate (lint, type or mutation settings, the Taskfile, workflows, hooks, the lint and mutate locks, new baseline entries).
- `git config core.hooksPath .githooks` runs lint, typecheck and the tests before each push.
- On a repository where commits go straight to `main`, `task guard` and `task mutate` compare `main` with itself and check nothing. Run them against what is not pushed yet, after committing and before `git push`: `task guard BASE=origin/main` and `task mutate BASE=origin/main`.

**Mutation testing.** `task mutate` changes each line a branch touched in `src/onward_generator`, such as `<` to `<=` or a constant moved by one, with [mutmut](https://mutmut.readthedocs.io) 3.8.1, and checks that some test fails. It rewrites only the files with a changed function and runs only the mutants on changed lines.

- Exit `0`: every mutant was killed. Exit `1`: a survivor, printed with its diff. Write a test that fails on that edit, or, when the edit changes nothing a caller can see, end the line with `# pragma: no mutate: <reason>`. Exit `2`: the tool could not run.
- `BASE=` sets the base (default `main`), `JOBS=` the workers (default all cores in CI, half locally). `task mutate -- --whole-functions` mutates whole functions.
- On a pull request, CI runs it against the target branch and skips it when `src/` did not change.

Tests do not use the network. Fixtures live in `tests/fixtures/` and use one origin, latitude 37.98407, longitude −1.12863.

## Layout

```text
src/onward_generator/     the generator, one package per stage
  maps/ osm/              map folder and Overpass download
  geo/ heights/ cadastre/ geographic scene, building heights, Catastro storeys
  sources/ overture/      source plugins (es.*, fr.*), their registry and merge; Overture fetch and match
  generation/ detail/     base meshes; facades, roofs, interiors, street elements
  dress/ materials/       dressed copy; per-map texture plan
  panoramax/ signs/       street photos; street-name plates and shop signs
  overgrow/ props/        abandoned-city layer and prop library; Asset Store instances
  bounds/ perf/ export/   perimeter wall; layout and measurement; export package
  ai/                     local Ollama intent and planner
  cli.py pipeline_run.py run_log.py   commands, the ordered run, its console summary
  io.py net.py            hashing and atomic writes; HTTP with retries and size caps
config/                   schemas and defaults for every stage, export and perf settings
assets/                   prop catalogues (normalized, kenney), textures, fonts, licence reviews
docs/                     numbered guides, command reference, decisions, licences
tests/                    unit/, integration/, synthetic fixtures (CC0-1.0)
tools/
  docker/                 run.sh and python.sh: commands in the image
  unity/                  phase-0 room, perf harness, export adapter (C#, run_export.sh)
  mutate/ quality/        mutation testing; the gate guard
maps/<id>/                one generated map; the whole maps/ tree is gitignored
Taskfile.yml              every task, locally and in CI
Dockerfile compose.yaml   the development image
requirements*.lock        pinned runtime, lint and mutation dependencies
mypy-baseline.txt         type errors that predate mypy; may only shrink
.github/ .githooks/       CI workflow, Dependabot, PR template; opt-in pre-push hook
```
