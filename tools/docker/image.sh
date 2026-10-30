#!/usr/bin/env bash
# The image tag for this checkout. ONWARD_IMAGE wins; a linked git worktree gets
# onward-generator:dev-<folder>, so worktrees building in parallel do not replace
# each other's image; any other checkout, CI included, gets onward-generator:dev.
set -euo pipefail

if [[ -n "${ONWARD_IMAGE:-}" ]]; then
  printf '%s\n' "$ONWARD_IMAGE"
  exit 0
fi
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
common="$(git -C "$REPO" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
if [[ -n "$common" && "$common" != "$REPO/.git" ]]; then
  name="$(printf '%s' "${REPO##*/}" | tr -c 'A-Za-z0-9_.-' '-' | cut -c1-100)"
  printf 'onward-generator:dev-%s\n' "$name"
else
  printf 'onward-generator:dev\n'
fi
