#!/usr/bin/env bash
# Python from the Docker image, for host scripts that call "$PYTHON" when there is no .venv.
#
#   PYTHON=tools/docker/python.sh tools/unity/onward_adapter/run_export.sh PACKAGE tool scene launcher
#
# The repository and the Unity project ($PROJECT, default ~/Unity/OnwardPhase0Project) are
# mounted at their host paths, so the absolute paths run_export.sh passes mean the same inside.
# PYTHONPATH and stdin ("python -" with a heredoc) are passed through. Nothing is installed on the host.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROJECT="${PROJECT:-$HOME/Unity/OnwardPhase0Project}"
HOST_UID="$(id -u)"
HOST_GID="$(id -g)"
export HOST_UID HOST_GID
compose=(docker compose --project-directory "$REPO" -f "$REPO/compose.yaml")

if ! docker image inspect onward-generator:dev >/dev/null 2>&1; then
  "${compose[@]}" build dev >&2
fi

mounts=(-v "$REPO:$REPO")
if [[ -d "$PROJECT" ]]; then
  mounts+=(-v "$PROJECT:$PROJECT")
fi
exec "${compose[@]}" run --rm -T -w "$REPO" -e PYTHONPATH="${PYTHONPATH:-}" "${mounts[@]}" dev python "$@"
