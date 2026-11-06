# ADR-030 — Sketchfab downloads through the Download API

Status: accepted · 5 November 2026

## Context

`overgrow fetch-sketchfab` needs a Sketchfab account to download a model. The documented interface for that is the Download API with a personal token. The generator must not read browser data or the desktop keyring to get a session.

## Decision

1. The command calls `GET https://api.sketchfab.com/v3/models/{uid}/download` with `Authorization: Token <token>`.
2. The token is the personal API token from sketchfab.com (Settings > Password & API), read from `SKETCHFAB_API_TOKEN`. A missing or blank value stops the command before any request. The token is never written to disk.
3. The token goes only to `api.sketchfab.com`. The signed archive URL in the response is fetched with the User-Agent alone.
4. The User-Agent is `onward-generator/<version>`.

## Consequences

- Nothing in the generator reads browser data or the desktop keyring.
- The licence review and the attribution fields in `assets/normalized/manifest.json` are unchanged.
- `_pick` reads the `gltf` and `glb` entries with `url` and `size`, the shape the Download API documents. No real download with a token has been run yet; the tests use a recorded shape, not the live service.
