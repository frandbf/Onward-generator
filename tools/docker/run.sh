#!/usr/bin/env bash
# Run a command inside the optional Docker image, with the repository mounted at /work.
#
#   tools/docker/run.sh                          # onward-generator --help
#   tools/docker/run.sh pytest -q
#   tools/docker/run.sh onward-generator doctor
#   tools/docker/run.sh --build pytest -q        # rebuild the image first
#
# Files written under the repository belong to the calling user. Unity and adb are not in the image.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HOST_UID="$(id -u)"
HOST_GID="$(id -g)"
export HOST_UID HOST_GID
compose=(docker compose --project-directory "$REPO" -f "$REPO/compose.yaml")

if [[ "${1:-}" == "--build" ]]; then
  shift
  "${compose[@]}" build dev
elif ! docker image inspect onward-generator:dev >/dev/null 2>&1; then
  "${compose[@]}" build dev
fi

tty=()
[[ -t 0 && -t 1 ]] || tty=(-T)
# In a git worktree, .git points at the main repository by absolute path; mount it there so git works inside.
mounts=()
common="$(git -C "$REPO" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
if [[ -n "$common" && "$common" != "$REPO/.git" ]]; then
  mounts=(-v "$common:$common")
fi
# ${a[@]+"${a[@]}"} expands an empty array under set -u on macOS bash 3.2 too.
exec "${compose[@]}" run --rm ${tty[@]+"${tty[@]}"} ${mounts[@]+"${mounts[@]}"} dev "$@"
