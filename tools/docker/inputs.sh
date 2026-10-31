#!/usr/bin/env bash
# The SHA-256 of what the image is built from: the Dockerfile, the three lock files and
# pyproject.toml. run.sh stamps the image with it and rebuilds when it changes, so a new
# dependency in a lock file reaches the image without a manual --build.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
inputs=(Dockerfile requirements.lock requirements-mutate.lock requirements-lint.lock pyproject.toml)
if command -v sha256sum >/dev/null 2>&1; then
  hash=(sha256sum)
else
  hash=(shasum -a 256)
fi
# Name and contents of each file in a fixed order; a missing file fails the script.
(cd "$REPO" && "${hash[@]}" "${inputs[@]}") | "${hash[@]}" | cut -d' ' -f1
