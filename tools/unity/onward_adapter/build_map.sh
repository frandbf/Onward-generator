#!/usr/bin/env bash
# The Unity steps of `onward-generator run`, on the host: tool, scene, launcher, then the Quest question.
#
#   build_map.sh [--platform macos|linux|auto] [MAP_NAME|MAP_ID|maps/<id>|PACKAGE_DIR]
#
# With no map, the newest export package of any map is used: the one task map just wrote.
# A map takes the card name given to task map ("Map name"), its id (map_name) or its folder,
# and uses that map's newest export package. Steps already
# built are skipped, as in `run`: no scene step when build/scene_report.json exists, nothing
# when the launcher pair exists. Then, in a terminal, it asks before copying to the Quest.
# Only s, si, y or yes runs push with CONFIRM_PUSH=yes, which backs up the headset map and
# replaces it. Without .venv/bin/python (and no PYTHON), run_export.sh uses the Docker image
# through tools/docker/python.sh. Unity and adb are the host's (tools/unity/lib.sh).
set -euo pipefail

TOOL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$TOOL_DIR/../../.." && pwd)"

usage() { echo "usage: $0 [--platform macos|linux|auto] [MAP_NAME|MAP_ID|maps/<id>|PACKAGE_DIR]" >&2; exit 2; }

platform=()
if [[ "${1:-}" == "--platform" ]]; then
  [[ $# -ge 2 ]] || usage
  platform=(--platform "$2")
  shift 2
elif [[ "${1:-}" == --platform=* ]]; then
  platform=("$1")
  shift
fi
if [[ $# -gt 1 ]]; then
  # A command copied with its trailing comment arrives as extra words when the copy turned spaces into U+00A0.
  printf 'unity: expected one map, got %d arguments:' "$#" >&2; printf ' [%s]' "$@" >&2; echo >&2
  usage
fi
if [[ -z "${PYTHON:-}" && ! -x "$REPO/.venv/bin/python" ]]; then
  export PYTHON="$REPO/tools/docker/python.sh"
fi

# Newest export manifest under the given glob, or nothing.
newest() {
  # shellcheck disable=SC2012 # pkg_NN and map ids have no spaces; ls -t gives the newest manifest.
  ls -t "$@" 2>/dev/null | head -n 1 || true
}

if [[ $# -eq 0 ]]; then
  manifest="$(newest "$REPO"/maps/*/data/processed/export/pkg_*/onward_export.json)"
  [[ -n "$manifest" ]] || { echo "unity: no map has an export package; run task map first" >&2; exit 1; }
  package="$(dirname "$manifest")"
else
  # Drop no-break spaces (U+00A0) and surrounding whitespace a copied command may carry.
  target="${1//$'\xc2\xa0'/}"
  target="${target#"${target%%[![:space:]]*}"}"
  target="${target%"${target##*[![:space:]]}"}"
  if [[ -f "$target/onward_export.json" ]]; then
    package="$target"
  else
    root="$target"
    [[ -f "$root/map.json" ]] || root="$REPO/maps/$target"
    if [[ ! -f "$root/map.json" ]]; then
      # A card name such as "Madrid Textura v6": the folder id is its slug, computed as task map does.
      id="$(PYTHONPATH="$REPO/src" "${PYTHON:-$REPO/.venv/bin/python}" -c \
        'import sys; from onward_generator.maps.workspace import slug_from_name; print(slug_from_name(sys.argv[1]))' \
        "$target" 2>/dev/null || true)"
      [[ -n "$id" ]] && root="$REPO/maps/$id"
    fi
    [[ -f "$root/map.json" ]] || { echo "unity: $target is not a map name, a map id, a map folder or an export package" >&2; exit 2; }
    manifest="$(newest "$root"/data/processed/export/pkg_*/onward_export.json)"
    [[ -n "$manifest" ]] || { echo "unity: $root has no export package; run task map first" >&2; exit 1; }
    package="$(dirname "$manifest")"
  fi
fi
echo "→ Package: ${package#"$REPO"/}"

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
