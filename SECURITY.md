# Security policy

## Reporting a vulnerability

Please do not open a public issue for a security problem. Report it privately through GitHub: [Report a vulnerability](https://github.com/frandbf/Onward-generator/security/advisories/new) (the **Security** tab, **Report a vulnerability**).

Include what you found, the command or input that triggers it, and what an attacker could do with it. You will get an answer within a week. Once a fix is released, the advisory is published with credit to you, unless you ask otherwise.

## Scope

onward-generator runs locally. The parts that matter most are the ones that touch the network or run other programs:

- Downloads from OpenStreetMap, Overture, Catastro, LiDAR, Panoramax, Poly Haven and Sketchfab (`src/onward_generator/net.py` and each source's `acquire` module).
- The Sketchfab API token in `SKETCHFAB_API_TOKEN`, which must only ever be sent to `api.sketchfab.com` ([ADR-030](docs/decisions/ADR-030-sketchfab-download-api.md)).
- The optional Ollama provider, which must only talk to loopback.
- The Unity, `adb` and shell wrappers under `tools/`.

Problems in Unity, the Onward SDK, Ollama or a data provider belong to those projects. A map that looks wrong is a bug, not a vulnerability: open an issue for it.

## Supported versions

Only the latest commit on `main` is supported.
