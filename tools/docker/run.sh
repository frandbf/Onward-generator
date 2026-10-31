#!/usr/bin/env bash
# Run a command inside the optional Docker image, with the repository mounted at /work.
#
#   tools/docker/run.sh                          # onward-generator --help
#   tools/docker/run.sh pytest -q
#   tools/docker/run.sh onward-generator doctor
#   tools/docker/run.sh --build pytest -q        # rebuild the image first
#
# The image is also rebuilt when it is missing or older than its inputs (tools/docker/inputs.sh:
# the Dockerfile, the lock files, pyproject.toml), so a new dependency needs no --build.
# Files written under the repository belong to the calling user. Unity and adb are not in the image.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HOST_UID="$(id -u)"
HOST_GID="$(id -g)"
ONWARD_IMAGE="$("$REPO/tools/docker/image.sh")"
ONWARD_IMAGE_INPUTS="$("$REPO/tools/docker/inputs.sh")"
export HOST_UID HOST_GID ONWARD_IMAGE ONWARD_IMAGE_INPUTS
compose=(docker compose --project-directory "$REPO" -f "$REPO/compose.yaml")

# compose.yaml stamps the image with the inputs hash as this label.
built="$(docker image inspect --format '{{ index .Config.Labels "org.onward-generator.inputs" }}' "$ONWARD_IMAGE" 2>/dev/null || true)"
if [[ "${1:-}" == "--build" ]]; then
  shift
  "${compose[@]}" build dev
elif [[ "$built" != "$ONWARD_IMAGE_INPUTS" ]]; then
  echo "Building $ONWARD_IMAGE: missing, or its Dockerfile, lock files or pyproject.toml changed." >&2
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
# The baked texture tile cache (src/onward_generator/materials/tile_cache.py) outlives the container:
# the host's ${XDG_CACHE_HOME:-~/.cache}/onward-generator is mounted and named by ONWARD_GENERATOR_CACHE_DIR.
cache="${XDG_CACHE_HOME:-$HOME/.cache}/onward-generator"
mkdir -p "$cache"
mounts+=(-v "$cache:/cache/onward-generator" -e ONWARD_GENERATOR_CACHE_DIR=/cache/onward-generator)
# ${a[@]+"${a[@]}"} expands an empty array under set -u on macOS bash 3.2 too.
exec "${compose[@]}" run --rm ${tty[@]+"${tty[@]}"} ${mounts[@]+"${mounts[@]}"} dev "$@"
