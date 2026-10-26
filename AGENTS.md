# AGENTS.md

Working rules for changes in this repository. The operational contract is [docs/01-pipeline.md](docs/01-pipeline.md). Read the numbered documents `docs/01` through `docs/12` in order when the task touches the pipeline. Architecture decision records in `docs/decisions/` explain a choice. `docs/legal/data-sources.md` is the licence record. There is no `PRD.md`.

## Project objective

Build a local, AI-assisted 3D map generator from legal geographic data, for custom Onward maps on Meta Quest. The command is `onward-generator` (`python -m onward_generator`), version 0.1.0.

Phases 00 through 06 are done. Phase 07, the final export, stays open. `device_verified` stays false until the headset checklist in `quest_acceptance.md` is filled in. Do not mark that checklist complete, and do not upload to a headset, unless the user asks.

## Mandatory workflow

1. Read the relevant numbered document and any architecture decision record the change touches.
2. Inspect existing code before changing it.
3. Work only on what was asked. Do not start the next stage.
4. Propose a short implementation plan before a significant change.
5. Implement the smallest verifiable increment.
6. Run the relevant tests and report their actual results.
7. Update documentation when a contract or a behavior changes.
8. Stop when the requested increment is done.

## Layout

- One map lives in `maps/<id>/`. Its OSM extract and generated scenes are `maps/<id>/data/`, and that tree is gitignored.
- The example area of interest is `config/murcia_belluga_aoi.geojson`. A created map keeps its own polygon at `maps/<id>/reference/aoi.geojson`.
- Placeable meshes are catalogues under `assets/`. Each direct child with a `manifest.json` joins the overgrow catalogue. Tileable albedo images are `assets/textures`. A texture referenced by a GLB stays beside that GLB.
- The Unity project and the Onward SDK stay outside this repository, in `~/Unity/OnwardPhase0Project`. The editor is Unity 2021.3.23f1. Another editor rewrites that project. The export adapter is `tools/unity/onward_adapter/`.

## Technical constraints

- Use explicit types and validated configuration.
- Preserve original geographic source data. Do not invent buildings that are not in the OSM extract. Kenney meshes with a building role are not in the library and must not replace footprints.
- Keep source, normalized scene, and game export separate.
- Never treat geographic degrees as metres.
- Never invent compatibility with the Onward SDK. A GLB is not a playable map.
- Keep the export adapter isolated from the generation engine.
- Use deterministic generation where possible.
- Track the provenance of generated objects.
- Distinguish observed, estimated, and AI-generated attributes.
- Keep AI optional and validate every model output. The model host is loopback Ollama only.
- Prefer reusable geometry and materials. Exported materials use `PP_Opaque_Simple_Lit` and an albedo only.
- Keep collision meshes separate from visual meshes.
- Do not rewrite historical scenes under `maps/*/data/`.

## Safety and licensing

- Do not extract proprietary game assets.
- Verify the licence of every external asset and dataset.
- Record OpenStreetMap attribution and the applicable ODbL obligations. Catastro is CC-BY-4.0 and is used for Spain only.
- Do not commit credentials, private SDK files, or `maps/*/data/`.
- Do not install software or run destructive commands without approval.
- Never execute code returned by an AI model.
- Never publish or overwrite a final artifact without confirmation. An export package is never overwritten.

## Testing

- Add unit tests for new domain behavior.
- Add integration tests for data pipeline changes.
- Use small deterministic fixtures.
- Tests use the Murcia city-centre point latitude 37.98407, longitude −1.12863 (`MURCIA_CENTER` in `tests/conftest.py`) as origin and reference location.
- Test invalid input and failure cases.
- Do not claim tests passed unless they were actually executed.
- Test game compatibility on the real headset when the task requires it.

## Scope control

Do not implement future work prematurely.

Do not rewrite unrelated modules.

Do not add dependencies without explaining why.

Do not replace a working component without evidence.

Do not silently change a public data contract.

## Required completion report

At the end of each implementation task, report:

1. Scope implemented.
2. Files created or modified.
3. Decisions and assumptions.
4. Tests executed and results.
5. Acceptance criteria satisfied.
6. Known limitations.
7. Recommended next step.

Stop after the requested increment.
