#!/usr/bin/env bash
# The Unity steps of `onward-generator run`, on the host: tool, scene, launcher, then the Quest question.
#
#   build_map.sh [--platform macos|linux|auto] MAP_ID|maps/<id>|PACKAGE_DIR
#
# A map id or folder uses its newest export package (task map writes it). Steps already
# built are skipped, as in `run`: no scene step when build/scene_report.json exists, nothing
# when the launcher pair exists. Then, in a terminal, it asks before copying to the Quest.
# Only s, si, y or yes runs push with CONFIRM_PUSH=yes, which backs up the headset map and
# replaces it. Without .venv/bin/python (and no PYTHON), run_export.sh uses the Docker image
# through tools/docker/python.sh. Unity and adb are the host's (tools/unity/lib.sh).
set -euo pipefail

TOOL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$TOOL_DIR/../../.." && pwd)"

usage() { echo "usage: $0 [--platform macos|linux|auto] MAP_ID|maps/<id>|PACKAGE_DIR" >&2; exit 2; }

platform=()
if [[ "${1:-}" == "--platform" ]]; then
  [[ $# -ge 2 ]] || usage
  platform=(--platform "$2")
  shift 2
elif [[ "${1:-}" == --platform=* ]]; then
  platform=("$1")
  shift
fi
[[ $# -eq 1 ]] || usage
target="$1"

if [[ -f "$target/onward_export.json" ]]; then
  package="$target"
else
  root="$target"
  [[ -f "$root/map.json" ]] || root="$REPO/maps/$target"
  [[ -f "$root/map.json" ]] || { echo "unity: $target is not a map id, a map folder or an export package" >&2; exit 2; }
  # shellcheck disable=SC2012 # pkg_NN names have no spaces; ls -t gives the newest manifest.
  manifest="$(ls -t "$root"/data/processed/export/pkg_*/onward_export.json 2>/dev/null | head -n 1 || true)"
  [[ -n "$manifest" ]] || { echo "unity: $root has no export package; run task map first" >&2; exit 1; }
  package="$(dirname "$manifest")"
fi

if [[ -z "${PYTHON:-}" && ! -x "$REPO/.venv/bin/python" ]]; then
  export PYTHON="$REPO/tools/docker/python.sh"
fi

launcher="$package/build/launcher"
if [[ -f "$launcher/unity-export.local.content3" && -f "$launcher/unity-export.local.info3" ]]; then
  echo "→ Unity: already has the Quest build."
else
  steps=(tool)
  [[ -f "$package/build/scene_report.json" ]] || steps+=(scene)
  steps+=(launcher)
  echo "→ Unity: ${steps[*]}"
  "$TOOL_DIR/run_export.sh" ${platform[@]+"${platform[@]}"} "$package" "${steps[@]}"
fi

if [[ ! -t 0 ]]; then
  echo "unity: Quest upload skipped. Run this in a terminal to answer the question."
  exit 0
fi
echo "Subir al Quest sustituye el mapa local del visor (unity-export.local). Antes se guarda una copia del que haya."
read -r -p "¿Subir al Quest? [s/N] " answer || answer=""
case "$(printf '%s' "$answer" | tr '[:upper:]' '[:lower:]')" in
  s|si|sí|y|yes)
    CONFIRM_PUSH=yes "$TOOL_DIR/run_export.sh" ${platform[@]+"${platform[@]}"} "$package" push
    ;;
  *)
    echo "unity: Quest upload skipped."
    ;;
esac
